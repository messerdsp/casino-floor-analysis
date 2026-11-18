"""05 Leak hunt: does any machine pay out more than its published math allows, beyond variance?
Method 1: luck z over the whole window (03) and per month (step changes).
Method 2: hit rate vs published hit_probability (binomial z) -- independent of payout size.
Method 3: meter oddities: payout not a multiple of payout_increment, hourly meters not summing to daily,
          spins/coin-in inconsistent with allowed bets.
Method 4: link to operations (repairs right before a change) and security incidents (tamper reports).
Writes outputs/05_leak_monthly.csv, outputs/05_leak_flags.csv, outputs/05_leak_summary.json."""
from common import con, q, save_csv, save_json
import numpy as np

c = con()

# ---- monthly luck z per machine
mon = q(c, """
select m.machine_id, strftime(m.gaming_date, '%Y-%m') AS month, any_value(m.config_id) config_id,
       sum(coin_in_cents)/100.0 coin_in, sum(house_win_cents)/100.0 house_win, sum(theo_cents)::double/100.0 theo,
       sqrt(sum(sum_bet_squared)*any_value(g.payout_multiplier_variance::double))/100.0 sd,
       sum(paid_spins) spins, sum(payout_positive_spins) pos, any_value(g.hit_probability::double) p
from md m join gm g using(config_id) where m.location_id is not null group by 1,2""")
mon["luck_z"] = (mon.house_win - mon.theo) / mon.sd
mon["hold"] = mon.house_win / mon.coin_in
mon["theo_hold"] = mon.theo / mon.coin_in
mon["hit_z"] = (mon.pos - mon.spins * mon.p) / np.sqrt(mon.spins * mon.p * (1 - mon.p))
mon["hit_rate"] = mon.pos / mon.spins
save_csv("05_leak_monthly.csv", mon.round(5))

# ---- whole-window hit-rate test
hw = q(c, """select m.machine_id, any_value(m.config_id) config_id, sum(paid_spins) spins, sum(payout_positive_spins) pos,
              any_value(g.hit_probability::double) p from md m join gm g using(config_id)
              where m.location_id is not null group by 1""")
hw["hit_z"] = (hw.pos - hw.spins * hw.p) / np.sqrt(hw.spins * hw.p * (1 - hw.p))
sc = q(c, f"select * from read_csv_auto('{(__import__('common').OUT / '03_scorecard.csv')}')")
flags = sc[["machine_id", "theme", "volatility", "zone_id", "bank_id", "manufacturer", "days_on_floor", "coin_in",
            "house_win", "theo", "hold", "theo_hold", "luck", "luck_sd", "luck_z"]].merge(hw[["machine_id", "hit_z", "spins", "pos", "p"]], on="machine_id")
flags["hit_rate"] = flags.pos / flags.spins

# ---- meter oddities (machine_hour level: payouts must be multiples of the payout increment)
odd = q(c, """
select h.machine_id, count(*) AS n_hours,
       sum(case when h.payout_cents % g.payout_increment_cents <> 0 then 1 else 0 end) payout_not_multiple,
       sum(case when h.carded_coin_in_cents > h.coin_in_cents then 1 else 0 end) carded_gt_total,
       sum(case when h.payout_positive_spins > h.paid_spins then 1 else 0 end) pos_gt_spins,
       sum(case when h.paid_spins>0 and h.coin_in_cents < h.paid_spins*g.min_bet_cents then 1 else 0 end) below_min_bet,
       sum(case when h.paid_spins>0 and h.coin_in_cents > h.paid_spins*g.max_bet_cents then 1 else 0 end) above_max_bet,
       sum(case when h.occupied_seconds > h.available_seconds then 1 else 0 end) occ_gt_avail,
       sum(case when h.paid_spins>0 and h.occupied_seconds=0 then 1 else 0 end) spins_without_occupancy
from mh h join md d on d.machine_id=h.machine_id and d.gaming_date=h.gaming_date
join gm g on g.config_id=d.config_id group by 1""")
roll = q(c, """select count(*) n_mismatch from (
   select machine_id, gaming_date, sum(coin_in_cents) ci, sum(payout_cents) po from mh group by 1,2) h
   join md d using(machine_id, gaming_date) where h.ci<>d.coin_in_cents or h.po<>d.payout_cents""")
flags = flags.merge(odd, on="machine_id", how="left")

# ---- operations & incidents per machine
ops = q(c, """select machine_id, count(*) filter (where event_type='fault_repair') faults,
              count(*) filter (where event_type not in ('fault_repair')) other_ops from ops group by 1""")
inc = q(c, """select machine_id, count(*) incidents, count(*) filter (where category='machine_tampering_attempt') tamper_reports
              from incidents where machine_id is not null group by 1""")
flags = flags.merge(ops, on="machine_id", how="left").merge(inc, on="machine_id", how="left").fillna(
    {"faults": 0, "other_ops": 0, "incidents": 0, "tamper_reports": 0})
flags["inspect_lead"] = (flags.luck_z < -3) | (flags.hit_z.abs() > 4) | (flags.payout_not_multiple > 0)
save_csv("05_leak_flags.csv", flags.round(5))

# ---- month-level outliers and step-change scan
mz = mon[(mon.luck_z.abs() > 3) | (mon.hit_z.abs() > 4)].sort_values("luck_z")
lead = flags[flags.inspect_lead]
summary = {
    "hourly_vs_daily_meter_mismatches": int(roll.n_mismatch[0]),
    "meter_oddity_totals": {k: int(odd[k].sum()) for k in odd.columns if k not in ("machine_id", "n_hours")},
    "machines_hit_z_abs_gt_4": flags.loc[flags.hit_z.abs() > 4, "machine_id"].tolist(),
    "hit_z_sd": float(flags.hit_z.std()),
    "month_outliers": mz[["machine_id", "month", "hold", "theo_hold", "luck_z", "hit_z"]].round(3).to_dict("records"),
    "inspect_leads": lead[["machine_id", "theme", "bank_id", "hold", "theo_hold", "luck_z", "hit_z", "payout_not_multiple",
                           "faults", "tamper_reports"]].round(3).to_dict("records"),
    "tamper_reports_total": int(flags.tamper_reports.sum()),
    "tamper_top": flags.sort_values("tamper_reports", ascending=False)[["machine_id", "tamper_reports", "luck_z"]].head(5).to_dict("records"),
}
save_json("05_leak_summary.json", summary)
for k, v in summary.items():
    print(k, v if not isinstance(v, list) or len(v) < 25 else v[:25])

# ---- title-level pooled tests (excluding the single-machine lead), both methods
x = flags[~flags.machine_id.isin(lead.machine_id)]
rows = []
for theme, d in x.groupby("theme"):
    n, k, p = d.spins.sum(), d.pos.sum(), d.p.iloc[0]
    rows.append(dict(theme=theme, machines=len(d), coin_in=d.coin_in.sum(), hold=d.house_win.sum() / d.coin_in.sum(),
                     theo_hold=d.theo.sum() / d.coin_in.sum(), luck=d.luck.sum(),
                     pooled_luck_z=d.luck.sum() / np.sqrt((d.luck_sd ** 2).sum()),
                     hit_rate=k / n, published_hit=p, pooled_hit_z=(k - n * p) / np.sqrt(n * p * (1 - p))))
cfg = __import__("pandas").DataFrame(rows)
save_csv("05_title_pooled.csv", cfg.round(6))
summary["title_pooled"] = cfg[["theme", "hold", "theo_hold", "pooled_luck_z", "pooled_hit_z"]].round(4).to_dict("records")
m1158 = flags[flags.machine_id == "M-1158"].iloc[0]
summary["M-1158"] = dict(hit_rate=float(m1158.hit_rate), published_hit=float(m1158.p), hold=float(m1158.hold),
                         theo_hold=float(m1158.theo_hold), shortfall_usd=float(-m1158.luck),
                         shortfall_ci95=[float(-m1158.luck - 1.96 * m1158.luck_sd), float(-m1158.luck + 1.96 * m1158.luck_sd)],
                         implied_rtp=float(1 - m1158.hold), luck_z=float(m1158.luck_z), hit_z=float(m1158.hit_z),
                         months_with_hit_z_gt_40=int((mon[mon.machine_id == "M-1158"].hit_z > 40).sum()))
save_json("05_leak_summary.json", summary)
print(cfg[["theme", "hold", "theo_hold", "pooled_luck_z", "pooled_hit_z"]].round(4).to_string(index=False))
print(summary["M-1158"])

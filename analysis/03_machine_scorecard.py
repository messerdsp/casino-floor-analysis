"""03 Per-machine scorecard: exposure, availability, luck (with volatility-based uncertainty), location context.

Luck uncertainty: for a spin with bet b on a game whose payout multiplier has variance V,
Var(house win) = b^2 * V. Summed over spins: Var = sum_bet_squared * V (cents^2).
z = (house_win - theo) / sqrt(sum_bet_squared * V). 95% CI on luck = +/- 1.96 * SD.
Only on-floor rows (location_id not null) are used.
Writes outputs/03_scorecard.csv and outputs/03_scorecard_summary.json."""
from common import con, q, save_csv, save_json
import numpy as np

c = con()
sc = q(c, """
with d as (
  select m.*, g.payout_multiplier_variance::double as pmv, g.configured_rtp::double as rtp,
         g.volatility_class, g.theme, l.zone_id, l.bank_id
  from md m join gm g using(config_id) join locs l on l.location_id = m.location_id
  where m.location_id is not null
),
last_loc as (
  select machine_id, arg_max(zone_id, gaming_date) zone_id, arg_max(bank_id, gaming_date) bank_id,
         arg_max(location_id, gaming_date) location_id, count(distinct location_id) n_locations
  from d group by 1
)
select d.machine_id, any_value(config_id) config_id, any_value(theme) theme, any_value(volatility_class) volatility,
       any_value(rtp) rtp, any_value(pmv) pmv,
       count(*) days_on_floor, sum(scheduled_seconds)/3600.0 sched_h, sum(available_seconds)/3600.0 avail_h,
       sum(occupied_seconds)/3600.0 occ_h, sum(paid_spins) spins, sum(payout_positive_spins) pos_spins,
       sum(coin_in_cents)/100.0 coin_in, sum(house_win_cents)/100.0 house_win, sum(theo_cents)::double/100.0 theo,
       sqrt(sum(sum_bet_squared) * any_value(pmv))/100.0 luck_sd,
       sum(carded_coin_in_cents)/100.0 carded_coin_in
from d group by d.machine_id
""")
loc = q(c, """select machine_id, arg_max(l.zone_id, gaming_date) zone_id, arg_max(l.bank_id, gaming_date) bank_id,
              arg_max(m.location_id, gaming_date) location_id, count(distinct m.location_id) n_locations
              from md m join locs l on l.location_id=m.location_id where m.location_id is not null group by 1""")
mach = q(c, "select machine_id, manufacturer, lease_class, install_date, retire_date from machines")
cost = q(c, """select machine_id, sum(lease_cost_usd+service_cost_usd+power_cost_usd+repair_cost_usd)::double supplied_costs,
               sum(contribution_after_supplied_costs_usd)::double contribution, sum(faults_opened) faults
               from pdv where location_id is not null group by 1""")
sc = sc.merge(loc, on="machine_id").merge(mach, on="machine_id").merge(cost, on="machine_id")

sc["availability"] = sc.avail_h / sc.sched_h
sc["downtime_h"] = sc.sched_h - sc.avail_h
sc["occupancy"] = sc.occ_h / sc.avail_h
sc["hold"] = sc.house_win / sc.coin_in
sc["theo_hold"] = sc.theo / sc.coin_in
sc["luck"] = sc.house_win - sc.theo
sc["luck_z"] = sc.luck / sc.luck_sd
sc["luck_lo95"] = sc.luck - 1.96 * sc.luck_sd
sc["luck_hi95"] = sc.luck + 1.96 * sc.luck_sd
sc["hit_rate"] = sc.pos_spins / sc.spins
sc["theo_per_day"] = sc.theo / sc.days_on_floor
sc["theo_per_avail_h"] = sc.theo / sc.avail_h
sc["coin_in_per_avail_h"] = sc.coin_in / sc.avail_h
sc["hw_per_day"] = sc.house_win / sc.days_on_floor
sc["avg_bet"] = sc.coin_in / sc.spins

# Location adjustment: compare each machine's theo per available hour with the median of its bank peers
# (same bank, excluding itself) and its zone.
def peer_median(df, key):
    out = []
    for i, row in df.iterrows():
        peers = df[(df[key] == row[key]) & (df.machine_id != row.machine_id)]
        out.append(peers.theo_per_avail_h.median() if len(peers) else np.nan)
    return out
sc["bank_peer_theo_per_h"] = peer_median(sc, "bank_id")
sc["zone_peer_theo_per_h"] = peer_median(sc, "zone_id")
sc["theo_vs_bank_peers"] = sc.theo_per_avail_h / sc.bank_peer_theo_per_h
sc["theo_vs_zone_peers"] = sc.theo_per_avail_h / sc.zone_peer_theo_per_h
sc["rank_house_win"] = sc.house_win.rank(method="first").astype(int)
sc["rank_theo_per_avail_h"] = sc.theo_per_avail_h.rank(method="first").astype(int)

save_csv("03_scorecard.csv", sc.round(6))
summ = {
    "machines": int(len(sc)),
    "luck_z_abs_gt_1.96": int((sc.luck_z.abs() > 1.96).sum()),
    "luck_z_lt_-3": sc.loc[sc.luck_z < -3, "machine_id"].tolist(),
    "luck_z_gt_3": sc.loc[sc.luck_z > 3, "machine_id"].tolist(),
    "luck_z_sd": float(sc.luck_z.std()),
    "luck_z_mean": float(sc.luck_z.mean()),
}
save_json("03_scorecard_summary.json", summ)
print(summ)
cols = ["machine_id", "theme", "volatility", "zone_id", "bank_id", "days_on_floor", "availability", "house_win", "theo",
        "luck_z", "hold", "theo_hold", "theo_per_avail_h", "theo_vs_bank_peers", "rank_theo_per_avail_h"]
print("--- manager bottom 10 rows")
b10 = ["M-5076", "M-1158", "M-1120", "M-1118", "M-1238", "M-1181", "M-1035", "M-1179", "M-1017", "M-1265"]
print(sc.set_index("machine_id").loc[b10, cols[1:]].round(3).to_string())
print("--- extreme luck_z")
print(sc.sort_values("luck_z")[cols].head(8).round(3).to_string(index=False))
print(sc.sort_values("luck_z")[cols].tail(5).round(3).to_string(index=False))
print("--- lowest theo per available hour vs bank peers")
print(sc.sort_values("theo_vs_bank_peers")[cols].head(12).round(3).to_string(index=False))
print("--- lowest availability")
print(sc.sort_values("availability")[cols + ["manufacturer", "faults"]].head(10).round(3).to_string(index=False))

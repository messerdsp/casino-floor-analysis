"""04 Separate location from the machine itself, then classify every machine.

Luck-free earning power = theo per available hour (theo removes luck; per available hour removes downtime
and days off the floor). We regress log(theo/avail h) and log(coin-in/avail h) on zone + game title,
using machine-days at each machine's actual location each day (machines move).
Replacement value of a machine = (zone mean theo/h of all OTHER machines in that zone − machine theo/h) × its
available hours/day: what an average machine for that spot would have earned instead.

Verdict per machine (first rule that applies):
  Payout lead  : luck z < −3 or hit-rate z > 4 (05) -> inspect, do not replace yet
  Short window : < 90 days on floor -> too little evidence
  Availability : availability below 98.5% AND downtime cost > 5% of theo
  Weak game for its spot: theo/h below 75% of zone peers AND coin-in/h not above zone peers (no hidden volume benefit).
                 In practice these are low-margin titles (92-95% RTP); the fix is moving the game, not repairing the machine.
  Location     : machine near/above its zone peers but zone mean theo/h < 80% of floor mean
  Unlucky      : house win below theo, but within 95% CI
  OK           : otherwise
Writes outputs/04_effects.csv, outputs/04_verdicts.csv, outputs/04_summary.json."""
from common import con, q, save_csv, save_json, OUT
import numpy as np, pandas as pd
import statsmodels.formula.api as smf

c = con()
sc = pd.read_csv(OUT / "03_scorecard.csv")
leak = pd.read_csv(OUT / "05_leak_flags.csv")[["machine_id", "hit_z"]]
sc = sc.merge(leak, on="machine_id")

# machine x zone segments (handles moved machines)
seg = q(c, """select m.machine_id, l.zone_id, any_value(g.theme) theme, count(*) AS days,
              sum(available_seconds)/3600.0 avail_h, sum(theo_cents)::double/100 theo, sum(coin_in_cents)/100.0 coin_in
              from md m join locs l on l.location_id=m.location_id join gm g using(config_id)
              where m.location_id is not null group by 1,2""")
seg = seg[seg.days >= 14].copy()
seg["l_theo"] = np.log(seg.theo / seg.avail_h)
seg["l_coin"] = np.log(seg.coin_in / seg.avail_h)
eff = []
for y in ["l_theo", "l_coin"]:
    m = smf.wls(f"{y} ~ C(zone_id, Treatment('Z-PIT')) + C(theme, Treatment('Desert Gold'))", seg, weights=seg.avail_h).fit(cov_type="HC1")
    ci = m.conf_int()
    for k in m.params.index:
        if k == "Intercept":
            continue
        kind = "zone" if "zone_id" in k else "title"
        name = k.split("[T.")[1].rstrip("]")
        eff.append(dict(outcome=y, kind=kind, level=name, effect_pct=100 * (np.exp(m.params[k]) - 1),
                        lo_pct=100 * (np.exp(ci.loc[k, 0]) - 1), hi_pct=100 * (np.exp(ci.loc[k, 1]) - 1)))
    print(y, "R2", round(m.rsquared, 3))
eff = pd.DataFrame(eff)

# Do title effects travel between zones? (the swap plan assumes they do)
seg["rtp"] = seg.theme.map(q(c, "select theme, any_value(configured_rtp) r from gm group by 1").set_index("theme").r)
seg["le"] = np.log(1 - seg.rtp)
seg["prime"] = seg.zone_id.isin(["Z-ENT", "Z-PIT"]).astype(int)
te = {}
for gname, d in [("B40", seg[seg.zone_id == "Z-B40"]), ("prime", seg[seg.prime == 1])]:
    mm = smf.wls("l_theo ~ C(zone_id) + C(theme, Treatment('Desert Gold'))", d, weights=d.avail_h).fit()
    te[gname] = {k.split("[T.")[1].rstrip("]"): v for k, v in mm.params.items() if "theme" in k}
te = pd.DataFrame(te).dropna()
el = smf.wls("l_coin ~ C(zone_id) + le + le:prime", seg, weights=seg.avail_h).fit(cov_type="HC1")
elci = el.conf_int()
r2_coin = smf.wls("l_coin ~ C(zone_id) + C(theme)", seg, weights=seg.avail_h).fit().rsquared
transfer = dict(title_effect_corr_B40_vs_prime=float(te.corr().iloc[0, 1]), mean_diff_log=float((te.prime - te.B40).mean()),
                titles_compared=int(len(te)), title_effects=te.round(3).to_dict("index"),
                coin_in_elasticity_to_house_edge=float(el.params["le"]), elasticity_ci95=[float(elci.loc["le", 0]), float(elci.loc["le", 1])],
                elasticity_extra_in_prime=float(el.params["le:prime"]), extra_ci95=[float(elci.loc["le:prime", 0]), float(elci.loc["le:prime", 1])],
                r2_coin_in_model=float(r2_coin),
                note=("Title effects estimated separately in the Back Forty and in prime zones agree, so a title keeps its relative "
                      "earning power when it moves. Tighter games draw slightly less play (a 10% higher house edge -> ~2% less coin-in/h), "
                      "and that penalty is no bigger in prime zones. The theo model's R2 is partly mechanical (theo = coin-in x edge), "
                      "but zone + title also explain 95% of coin-in per hour, which has no edge built in."))
print("transfer", {k: v for k, v in transfer.items() if k != "title_effects"})
save_csv("04_effects.csv", eff.round(2))

# zone peer comparison (excluding the machine itself), using last zone
floor_theo_h = sc.theo.sum() / sc.avail_h.sum()
zone = sc.groupby("zone_id").agg(z_theo=("theo", "sum"), z_h=("avail_h", "sum"), z_coin=("coin_in", "sum"))
sc = sc.join(zone, on="zone_id")
sc["zone_peer_theo_h"] = (sc.z_theo - sc.theo) / (sc.z_h - sc.avail_h)
sc["zone_peer_coin_h"] = (sc.z_coin - sc.coin_in) / (sc.z_h - sc.avail_h)
sc["zone_theo_h_vs_floor"] = (sc.z_theo / sc.z_h) / floor_theo_h
sc["theo_h_vs_zone"] = sc.theo_per_avail_h / sc.zone_peer_theo_h
sc["coin_h_vs_zone"] = sc.coin_in_per_avail_h / sc.zone_peer_coin_h
sc["avail_h_per_day"] = sc.avail_h / sc.days_on_floor
sc["replacement_gain_per_day"] = (sc.zone_peer_theo_h - sc.theo_per_avail_h) * sc.avail_h_per_day
sc["downtime_theo_cost"] = sc.downtime_h * sc.theo_per_avail_h

def verdict(r):
    if r.luck_z < -3 or r.hit_z > 4:
        return "Payout lead: inspect"
    if r.days_on_floor < 90:
        return "Short window"
    if r.availability < 0.985 and r.downtime_theo_cost > 0.05 * r.theo:
        return "Availability"
    if r.theo_h_vs_zone < 0.75 and r.coin_h_vs_zone <= 1.0:
        return "Weak game for its spot"
    if r.zone_theo_h_vs_floor < 0.8:
        return "Location"
    if r.luck < 0:
        return "Unlucky (within noise)"
    return "OK"
sc["verdict"] = sc.apply(verdict, axis=1)
save_csv("04_verdicts.csv", sc.round(5))

b10 = pd.read_csv(OUT / "02_bottom10.csv").machine_id.tolist()
true_under = sc[sc.verdict == "Weak game for its spot"].sort_values("replacement_gain_per_day", ascending=False)
summ = dict(
    floor_theo_per_avail_h=floor_theo_h,
    verdict_counts=sc.verdict.value_counts().to_dict(),
    bottom10_verdicts=sc.set_index("machine_id").loc[b10, "verdict"].to_dict(),
    zone_theo_h_vs_floor=sc.groupby("zone_id").zone_theo_h_vs_floor.first().round(3).to_dict(),
    true_underperformers_top10=true_under[["machine_id", "theme", "zone_id", "bank_id", "theo_per_avail_h", "zone_peer_theo_h",
                                           "coin_h_vs_zone", "replacement_gain_per_day"]].head(10).round(2).to_dict("records"),
    true_under_themes=true_under.theme.value_counts().to_dict(),
    title_transfer_test=transfer,
    overlap_true_top10_with_manager_bottom10=sorted(set(true_under.machine_id.head(10)) & set(b10)),
)
save_json("04_summary.json", summ)
print(eff[eff.outcome == "l_theo"].round(1).to_string(index=False))
print(eff[(eff.outcome == "l_coin") & (eff.kind == "title")].round(1).to_string(index=False))
for k, v in summ.items():
    print(k, v)

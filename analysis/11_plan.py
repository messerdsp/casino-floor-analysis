"""11 The $100,000 plan for July 1-30, 2025.

Cost rule (GUIDE §6): quantity × (unit_cost_cents + recurring_cents_per_day × max(0, 30 − lead_days)) / 100.
Benefits are expected house win (theo) over the days left after lead time, estimated from our data:
  1. A-INSPECT    (leak)     : M-1158 (payout lead, two tests) + M-1181 (Comet Cash title-level lead, in manager's list).
  2. A-MOVE-MACHINE (location): swap strong-title machines out of the Back Forty with weak-title machines in the
                                strongest zones. Gain from the zone × title model in 04 (multiplicative effects):
                                gain/day = H × base × (e^zH − e^zL)(e^tH − e^tL) > 0 for every such pair.
                                Validated by the natural experiment M-1152 (B40 → Main Entrance, +106% vs floor).
  3. A-PROMO      (demand)   : one promotion weekend; benefit from the two past property promotions (07).
Not funded (data says it does not pay back in July): tech shift and preventive maintenance (06), comp change/pilot (09),
lease/purchase replacements (break-even at best in 16 / 9 effective days), signage (no evidence it restores traffic).
Writes outputs/11_plan.json, outputs/11_swaps.csv."""
from common import con, q, OUT, save_json, save_csv
import json, numpy as np, pandas as pd
import statsmodels.formula.api as smf

c = con()
cat = q(c, "select * from actions").set_index("action_type_id")
def cost(aid, qty):
    a = cat.loc[aid]
    return qty * (a.unit_cost_cents + a.recurring_cost_cents_per_day * max(0, 30 - a.lead_days)) / 100
def eff_days(aid):
    return max(0, 30 - int(cat.loc[aid].lead_days))

sc = pd.read_csv(OUT / "04_verdicts.csv")
tt = json.load(open(OUT / "04_summary.json"))["title_transfer_test"]
sc_last = sc.set_index("machine_id")

# ---------------- 1. inspections ----------------
leak = json.load(open(OUT / "05_leak_summary.json"))
m = leak["M-1158"]
ci_day = sc_last.loc["M-1158", "coin_in"] / sc_last.loc["M-1158", "days_on_floor"]
gap = m["theo_hold"] - m["hold"]                       # observed hold shortfall (incl. luck)
gap_lo, gap_hi = m["shortfall_ci95"][0] / sc_last.loc["M-1158", "coin_in"], m["shortfall_ci95"][1] / sc_last.loc["M-1158", "coin_in"]
d_ins = eff_days("A-INSPECT") - 1                      # 1 day lead, plus allow 1 day for paytable reset
title = pd.read_csv(OUT / "05_title_pooled.csv").set_index("theme")
cc = title.loc["Comet Cash"]
comet_gap = cc.theo_hold - cc.hold
comet_ci_day = cc.coin_in / 181
inspect = dict(
    action="A-INSPECT", quantity=2, targets=["M-1158", "M-1181"], cost=cost("A-INSPECT", 2),
    cause="Leak: M-1158 hit rate 38.6% vs 31.5% published (z=+124) and hold 3.0% vs 10% (z=−4.6)",
    benefit_central=ci_day * gap * d_ins, benefit_low=0.0, benefit_high=ci_day * gap_hi * d_ins,
    assumptions=(f"If the paytable is confirmed wrong and reset, M-1158 returns to its published 10% hold on its own "
                 f"coin-in (${ci_day:,.0f}/day) for {d_ins} days. Low = inspection finds nothing (value is information). "
                 f"M-1181 checks whether Comet Cash as a title pays {comet_gap*100:.2f} pts over spec "
                 f"(title-wide upside ≈ ${comet_ci_day*comet_gap*d_ins:,.0f} in July if confirmed; not counted in central)."),
    measurement=("Inspection report (paytable vs. published G-STD90). After reset: daily hit rate must fall back to ~31.5% "
                 "(binomial test, detectable within ~2 days of play) and hold track 10% within its volatility band."),
    evidence="#s-leak",
)

# ---------------- 2. machine swaps out of the Back Forty ----------------
seg = q(c, """select m.machine_id, l.zone_id, any_value(g.theme) theme, count(*) AS n_days,
              sum(available_seconds)/3600.0 avail_h, sum(theo_cents)::double/100 theo
              from md m join locs l on l.location_id=m.location_id join gm g using(config_id)
              where m.location_id is not null group by 1,2""")
seg = seg[seg.n_days >= 14].copy()
seg["y"] = np.log(seg.theo / seg.avail_h)
fit = smf.wls("y ~ C(zone_id, Treatment('Z-PIT')) + C(theme, Treatment('Desert Gold'))", seg, weights=seg.avail_h).fit(cov_type="HC1")
P = fit.params
def zeff(z): return 0.0 if z == "Z-PIT" else P.get(f"C(zone_id, Treatment('Z-PIT'))[T.{z}]", 0.0)
def teff(t): return 0.0 if t == "Desert Gold" else P.get(f"C(theme, Treatment('Desert Gold'))[T.{t}]", 0.0)
base = np.exp(P["Intercept"]) * np.mean(np.exp(fit.resid))

# Current placement (last day), on-floor at June 30 only
cur = q(c, """select m.machine_id, l.zone_id, l.bank_id, m.location_id, g.theme
              from md m join locs l on l.location_id=m.location_id join gm g using(config_id)
              where m.gaming_date = (select max(gaming_date) from md) and m.location_id is not null""")
cur["t"] = cur.theme.map(teff)
cur["z"] = cur.zone_id.map(zeff)
H = (sc.avail_h.sum() / sc.days_on_floor.sum())        # available hours per machine-day (fleet)
exclude = {"M-1158", "M-1181"}                          # being inspected: keep in place
lowz = cur[(cur.zone_id == "Z-B40") & ~cur.machine_id.isin(exclude)].sort_values("t", ascending=False)
weak = set(sc[sc.verdict == "Weak game for its spot"].machine_id)  # partners must be flagged in section 6
hiz = cur[cur.zone_id.isin(["Z-ENT", "Z-PIT"]) & ~cur.machine_id.isin(exclude) & cur.machine_id.isin(weak)].copy()
hiz = hiz.sort_values(["t", "z"], ascending=[True, False])
swaps = []
used = set()
for _, a in lowz.iterrows():
    for _, b in hiz.iterrows():
        if b.machine_id in used:
            continue
        g = H * base * (np.exp(b.z) - np.exp(a.z)) * (np.exp(a.t) - np.exp(b.t))
        if g > 0:
            swaps.append(dict(b40_machine=a.machine_id, b40_title=a.theme, b40_bank=a.bank_id, b40_location=a.location_id,
                              partner_machine=b.machine_id, partner_title=b.theme, partner_zone=b.zone_id, partner_bank=b.bank_id,
                              partner_location=b.location_id, gain_per_day=g))
            used.add(b.machine_id)
        break
assert set(x["partner_machine"] for x in swaps) <= weak
allsw = pd.DataFrame(swaps).sort_values("gain_per_day", ascending=False).head(12).reset_index(drop=True)
# Fund only swaps that pay back inside the July window (GUIDE §6 counts benefits only for days after lead time).
swap_down = 2 * (cat.loc["A-MOVE-MACHINE"].downtime_minutes / 60) * (sc.theo.sum() / sc.avail_h.sum())
allsw["july_net"] = allsw.gain_per_day * eff_days("A-MOVE-MACHINE") - swap_down - cost("A-MOVE-MACHINE", 2)
allsw["status"] = np.where(allsw.july_net > 0, "funded (pays back in July)", "queued for August (pays back in ~32 days)")
sw = allsw[allsw.july_net > 0].reset_index(drop=True)
queued = allsw[allsw.july_net <= 0]

# Uncertainty: (a) parameter uncertainty by simulation from the coefficient covariance;
# (b) transfer risk: low case assumes only 50% of the modelled zone effect transfers to a moved machine.
rng = np.random.default_rng(11)
draws = rng.multivariate_normal(P.values, fit.cov_params().values, 2000)
names = list(P.index)
def gain_draw(row, d):
    pp = dict(zip(names, d))
    ze = lambda z: 0.0 if z == "Z-PIT" else pp.get(f"C(zone_id, Treatment('Z-PIT'))[T.{z}]", 0.0)
    te = lambda t: 0.0 if t == "Desert Gold" else pp.get(f"C(theme, Treatment('Desert Gold'))[T.{t}]", 0.0)
    b0 = np.exp(pp["Intercept"]) * np.mean(np.exp(fit.resid))
    return H * b0 * (np.exp(ze(row.partner_zone)) - np.exp(ze("Z-B40"))) * (np.exp(te(row.b40_title)) - np.exp(te(row.partner_title)))
tot = np.array([sum(gain_draw(r, d) for _, r in sw.iterrows()) for d in draws[:400]])
d_mv = eff_days("A-MOVE-MACHINE")
down_cost = len(sw) * 2 * (cat.loc["A-MOVE-MACHINE"].downtime_minutes / 60) * (sc.theo.sum() / sc.avail_h.sum())
central = sw.gain_per_day.sum() * d_mv - down_cost
moves = json.load(open(OUT / "07_closure_did.json")) if (OUT / "07_closure_did.json").exists() else {}
mv = pd.read_csv(OUT / "07_moves.csv").set_index("machine_id")
m1152 = mv.loc["M-1152"]
# Predicted change for M-1152 from the zone model (B40 -> ENT): exp(z_ENT - z_B40) - 1
pred_1152 = np.exp(zeff("Z-ENT") - zeff("Z-B40")) - 1
move = dict(
    action="A-MOVE-MACHINE", quantity=int(2 * len(sw)), cost=cost("A-MOVE-MACHINE", 2 * len(sw)),
    targets=[f"{r.b40_machine} ({r.b40_title}, B40) <-> {r.partner_machine} ({r.partner_title}, {r.partner_zone})" for _, r in sw.iterrows()],
    cause=("Location: Back Forty earns 43% less expected win per available hour than Center Pit at the same title (CI 41–44%), "
           "because fewer guests sit down there (occupancy 0.28 vs 0.42), not because of its games"),
    benefit_central=central, benefit_low=0.5 * np.percentile(tot, 5) * d_mv - down_cost,
    benefit_high=np.percentile(tot, 95) * d_mv * max(1.0, (m1152_obs := float(pd.read_csv(OUT / "07_moves.csv").set_index("machine_id").loc["M-1152", "floor_relative_change"])) / (np.exp(zeff("Z-ENT") - zeff("Z-B40")) - 1)) - down_cost,
    gain_per_day_after_july=float(sw.gain_per_day.sum()),
    queued_for_august=[f"{r.b40_machine} ({r.b40_title}) <-> {r.partner_machine} ({r.partner_title}, {r.partner_zone}): +${r.gain_per_day:,.0f}/day" for _, r in queued.iterrows()],
    queued_gain_per_day=float(queued.gain_per_day.sum()),
    assumptions=(f"Each machine's expected win per available hour = base × zone effect × title effect (R² 0.97). Swapping a strong "
                 f"title out of the Back Forty with a weak title in Center Pit/Main Entrance adds (zone gap) × (title gap). "
                 f"{d_mv} effective days after 2-day lead; minus 3 h downtime per moved machine (${down_cost:,.0f}). "
                 f"Low = 5th percentile of parameter draws AND only half the zone effect transfers; high = 95th percentile scaled up by how much the real M-1152 move beat the model. "
                 f"Check: the model predicts +{pred_1152*100:.0f}% for the one real B40→Main Entrance move (M-1152, Feb 4); "
                 f"observed +{m1152.floor_relative_change*100:.0f}% vs floor (95% CI {m1152.floor_relative_change_ci95_lo*100:.0f}–{m1152.floor_relative_change_ci95_hi*100:.0f}%). "
                 f"Partners are all machines flagged 'Weak game for its spot' in section 6. Title effects estimated separately in the Back Forty and in prime zones "
                 f"correlate {tt['title_effect_corr_B40_vs_prime']:.2f}, so a title keeps its relative earning power when moved; tighter games draw only slightly less play "
                 f"(10% more house edge → {abs(tt['coin_in_elasticity_to_house_edge'])*10:.0f}% less coin-in/h), with no extra penalty in prime zones. "
                 f"Adding 8 machines to Center Pit in March raised zone coin-in ~18% without taking play from existing machines, so we assume no cannibalisation. "
                 f"We fund only the {len(sw)} swaps whose gain pays back their two moves inside July; {len(queued)} more Back Forty ↔ prime swaps "
                 f"(Prairie Thunder ↔ Comet Cash, +${queued.gain_per_day.sum():,.0f}/day together) gain less than they cost in 28 days and are queued for August."),
    measurement=("Difference-in-differences over July: swapped machines' theo per available hour, before vs after, against unmoved "
                 "machines of the same titles in the same zones; plus total zone coin-in for B40, PIT and ENT to catch cannibalisation. "
                 f"Success = combined theo/h of the {2*len(sw)} moved machines up ≥10% vs control; if met, run the queued swaps in August."),
    evidence="#s-location",
)

# ---------------- 3. promotion weekend ----------------
ev = pd.read_csv(OUT / "07_event_effects.csv").set_index("event_id")
t, l = ev.loc["EV-P-TOURN"], ev.loc["EV-P-LOCALS"]
promo = dict(
    action="A-PROMO", quantity=1, cost=cost("A-PROMO", 1), targets=["Slot-tournament weekend Fri Jul 18 – Sun Jul 20 (after the 14-day lead)"],
    cause=(f"Demand: the Feb slot tournament added +{t.coin_in_uplift*100:.0f}% coin-in (CI {t.coin_in_uplift_ci95_lo*100:.0f}–{t.coin_in_uplift_ci95_hi*100:.0f}%) "
           f"= ${t.theo_incremental_usd:,.0f} expected win; June Locals Week +{l.coin_in_uplift*100:.1f}% = ${l.theo_incremental_usd:,.0f}"),
    benefit_central=float(t.theo_incremental_usd),
    benefit_low=float(l.theo_incremental_ci95_lo), benefit_high=float(t.theo_incremental_ci95_hi),
    assumptions=("Same format as the February slot tournament (a 3-day weekend), so central = that event's measured extra expected win and high = "
                 "its upper 95% bound. Low = the lower 95% bound of the weaker June Locals Week (a 7-day, smaller-lift promotion), in case July "
                 "responds less. Benefit is theo (expected house win before costs); the $15k covers marketing, prizes and staffing, and free-play "
                 "prizes are inside it, so benefit − cost is the July net. Open to all guests; no targeting of heavy losers; responsible-gaming messaging."),
    measurement=("Pre-registered: compare Jul 18–20 coin-in and theo against the frozen weekday forecast and against matched July weekends "
                 "(same placebo-window method as for past events); also check the following week for pull-forward."),
    evidence="#s-demand",
)

plan = [inspect, move, promo]
spent = sum(p["cost"] for p in plan)
for p in plan:
    p.setdefault("gain_per_day_after_july", None)
    for k in ["cost", "benefit_central", "benefit_low", "benefit_high"]:
        p[k] = float(round(p[k], 0))
    p["net_central"] = p["benefit_central"] - p["cost"]
    a = cat.loc[p["action"]]
    p.update(unit_cost_usd=float(a.unit_cost_cents / 100), recurring_usd_per_day=float(a.recurring_cost_cents_per_day / 100),
             lead_days=int(a.lead_days), downtime_minutes=int(a.downtime_minutes), quantity_limit=int(a.quantity_limit),
             effective_days=eff_days(p["action"]), name=a["name"])
    assert p["quantity"] <= a.quantity_limit
moved = set(sw.b40_machine) | set(sw.partner_machine)
assert not (moved & set(inspect["targets"])), "same machine in two actions"

down = json.load(open(OUT / "06_action_benefits.json"))
_wk = pd.DataFrame(next(v for k, v in json.load(open(OUT / "07_closure_did.json"))["recovery"].items() if k.startswith("weekly")))
rec_c, rec_t = _wk[_wk.period == "post"].cpah_index.mean(), _wk[_wk.period == "post"].traffic_index.mean()
comp = json.load(open(OUT / "09_action_benefits.json"))
rejected = [
    dict(action="A-TECH-SHIFT", cost=cost("A-TECH-SHIFT", 1), why="Extra technician recovers only ~$1–2k expected win in 23 days (06); downtime is 0.38% of hours.", evidence="#s-downtime"),
    dict(action="A-PREVENTIVE", cost=cost("A-PREVENTIVE", 1), why="Ridgeline sweep: central ~$8.3k benefit vs $9,000 cost, and no evidence a sweep cuts faults (06).", evidence="#s-downtime"),
    dict(action="A-REPLACE-LEASE", cost=cost("A-REPLACE-LEASE", 1), why="The weakest-for-its-spot machine gains ~$244/day × 16 days ≈ $3.9k vs $3,380 per unit, before new-title risk. Revisit for August with a full month.", evidence="#s-true"),
    dict(action="A-REPLACE-BUY", cost=cost("A-REPLACE-BUY", 1), why="$22,000 for 9 effective days; cannot pay back in July.", evidence="#s-true"),
    dict(action="A-COMP-CHANGE / A-COMP-PILOT", cost=cost("A-COMP-CHANGE", 1), why="May 1 comp price cut had no detectable effect on play or cost; reverting saves < fee (09). Comps are not why machines earn less.", evidence="#s-comps"),
    dict(action="A-WAYFINDING (Back Forty)", cost=cost("A-WAYFINDING", 1), why=f"After the walkway reopened, Back Forty coin-in per machine-hour recovered to ~{rec_c*100:.0f}% of its pre-closure level and counter traffic to ~{rec_t*100:.0f}%; signs might close that last gap, but we have no evidence they work, and the zone was already 35% below the floor before the closure. Swaps target the same cause with measured evidence.", evidence="#s-location"),
    dict(action="A-MOVE-BANK", cost=cost("A-MOVE-BANK", 1), why="Moving a whole B40 bank needs an empty bank in a strong zone; swaps capture the gain machine by machine.", evidence="#s-location"),
    dict(action="Replace the manager's bottom 10", cost=10 * cost("A-REPLACE-LEASE", 1), why="Most are location, luck or short-window cases; one is a payout lead. Replacing them would not fix the cause.", evidence="#s-bottom10"),
]
for r in rejected:
    r["cost"] = float(round(r["cost"], 0))
    code = r["action"].split(" ")[0]
    r["code"] = code if code in cat.index else ""
    r["action"] = (cat.loc[code, "name"] + (" (Back Forty)" if "Back Forty" in r["action"] else "")) if code in cat.index else (
        "Comp redemption change / comp pilot" if code.startswith("A-COMP") else r["action"])
out = dict(budget=100000, spent=float(spent), kept=float(100000 - spent), actions=plan, rejected=rejected,
           total_benefit_central=float(sum(p["benefit_central"] for p in plan)),
           total_benefit_low=float(sum(p["benefit_low"] for p in plan)), total_benefit_high=float(sum(p["benefit_high"] for p in plan)),
           kept_reason=("Keep $%s: the rest of the shortfall is luck (keeping is the right answer for luck) or has no action that pays back in July." % f"{100000-spent:,.0f}"),
           recovery_cpah=float(rec_c), recovery_traffic=float(rec_t),
           model_check_M1152=dict(predicted=float(pred_1152), observed=float(m1152.floor_relative_change),
                                  ci95=[float(m1152.floor_relative_change_ci95_lo), float(m1152.floor_relative_change_ci95_hi)]))
save_json("11_plan.json", out)
save_csv("11_swaps.csv", allsw.round(2))
print(f"spent {spent:,.0f} kept {100000-spent:,.0f}")
for p in plan:
    print(p["action"], p["quantity"], p["cost"], "benefit", p["benefit_low"], p["benefit_central"], p["benefit_high"])
print(sw[["b40_machine", "b40_title", "partner_machine", "partner_title", "partner_zone", "gain_per_day"]].round(1).to_string(index=False))
print("M-1152 predicted", round(pred_1152, 3), "observed", m1152.floor_relative_change)

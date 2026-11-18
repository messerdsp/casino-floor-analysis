"""Q2: What changed in demand across the floor over the six months, and why?
Also the 'location' explanation for weak machines (Back Forty / walkway closure).

Conventions
- Gaming date for traffic: hour_start_utc mapped into calendar_day [gaming_day_start_utc, gaming_day_end_utc).
- Traffic = crossings, not unique visitors. Missing counts (offline) are never zero: daily zone entries are
  coverage-adjusted = observed entries / covered minutes * scheduled minutes of the gaming day.
- Machine metrics from prepared_daily_view rows with a location (on-floor days only); ratios are ratio of sums;
  coin-in normalised by available machine-hours (cpah = coin-in per available hour).
- LVCVA city data is all Las Vegas visitors, published 2026 -> context only, never used as a driver.
"""
import numpy as np, pandas as pd
import statsmodels.formula.api as smf
from common import con, q, save_csv, save_json

RNG = np.random.default_rng(7)
NBOOT = 2000
CLOSE_FROM, CLOSE_TO = pd.Timestamp("2025-03-03"), pd.Timestamp("2025-04-07")  # gaming dates [from, to)
c = con()

# ---------------------------------------------------------------- closure timing check
w = q(c, "select * from walkways where walkway_id='W-ENT-B40'").iloc[0]
cd = q(c, f"""select min(gaming_date) d0, max(gaming_date) d1 from cal
              where gaming_day_start_utc >= TIMESTAMP '{w.closed_from_utc}' and gaming_day_end_utc <= TIMESTAMP '{w.closed_to_utc}'""").iloc[0]
assert pd.Timestamp(cd.d0) == CLOSE_FROM and pd.Timestamp(cd.d1) == CLOSE_TO - pd.Timedelta(days=1)
CLOSED_DAYS = (CLOSE_TO - CLOSE_FROM).days

def period(d):
    d = pd.Timestamp(d)
    return "pre" if d < CLOSE_FROM else ("closed" if d < CLOSE_TO else "post")

# ---------------------------------------------------------------- zone-day traffic (coverage adjusted)
tz = q(c, """
select cal.gaming_date, t.zone_id,
       sum(t.entries_count) entries_observed,
       sum(case when t.entries_count is not null then t.coverage_minutes else 0 end) covered_min,
       any_value(cal.scheduled_seconds)/60.0 sched_min
from traffic t join cal on t.hour_start_utc >= cal.gaming_day_start_utc and t.hour_start_utc < cal.gaming_day_end_utc
group by 1,2""")
tz["coverage_share"] = tz.covered_min / tz.sched_min
tz["entries_adj"] = np.where(tz.covered_min > 0, tz.entries_observed / tz.covered_min.replace(0, np.nan) * tz.sched_min, np.nan)

# ---------------------------------------------------------------- zone-day machine metrics
zd = q(c, """
select gaming_date, zone_id, sum(coin_in_usd)::double coin_in, sum(theo_usd)::double theo,
       sum(house_win_usd)::double house_win, sum(available_hours) available_h, sum(occupied_hours) occupied_h,
       count(*) machines
from pdv where location_id is not null group by 1,2""")
zone = zd.merge(tz[["gaming_date", "zone_id", "entries_adj", "coverage_share"]], on=["gaming_date", "zone_id"], how="left")
zone["gaming_date"] = pd.to_datetime(zone.gaming_date)
zone["coin_in_per_avail_h"] = zone.coin_in / zone.available_h
zone["theo_per_avail_h"] = zone.theo / zone.available_h
zone["period"] = zone.gaming_date.map(period)
zone = zone.sort_values(["gaming_date", "zone_id"])
out = zone[["gaming_date", "zone_id", "machines", "coin_in", "theo", "house_win", "available_h", "occupied_h",
            "coin_in_per_avail_h", "theo_per_avail_h", "entries_adj", "coverage_share"]].copy()
save_csv("07_zone_daily.csv", out.assign(gaming_date=out.gaming_date.dt.date).round(3))

# ---------------------------------------------------------------- bank-day
bank = q(c, """
select gaming_date, zone_id, bank_id, round(sum(coin_in_usd)::double,2) coin_in, round(sum(theo_usd)::double,2) theo,
       round(sum(house_win_usd)::double,2) house_win, round(sum(available_hours),3) available_h, count(*) machines
from pdv where location_id is not null group by 1,2,3 order by 1,3""")
bank["coin_in_per_avail_h"] = (bank.coin_in / bank.available_h).round(2)
save_csv("07_bank_daily.csv", bank)

# ---------------------------------------------------------------- floor daily
fl = q(c, """
select p.gaming_date, p.local_weekday weekday, cal.is_weekend, cal.federal_holiday, cal.month, p.events_active,
       p.active_machines, p.coin_in_usd::double coin_in, p.theo_usd::double theo, p.house_win_usd::double house_win,
       p.available_machine_hours, p.occupied_machine_hours, p.total_zone_entries_observed entries_observed,
       p.counter_coverage_share
from prop p join cal using (gaming_date) order by 1""")
fl["gaming_date"] = pd.to_datetime(fl.gaming_date)
tzf = tz.assign(gaming_date=pd.to_datetime(tz.gaming_date)).groupby("gaming_date").agg(
    entries_adj=("entries_adj", lambda s: s.sum() if s.notna().all() else np.nan),
    eo=("entries_observed", "sum"))
fl = fl.merge(tzf, on="gaming_date", how="left")
assert (fl.eo == fl.entries_observed).all()  # reconciles with property_day
fl = fl.drop(columns="eo")
fl["coin_in_per_avail_h"] = fl.coin_in / fl.available_machine_hours
fl["b40_closure_period"] = fl.gaming_date.map(period)
zb = zone[zone.zone_id == "Z-B40"].set_index("gaming_date")
fl["coin_in_ex_b40"] = fl.coin_in - fl.gaming_date.map(zb.coin_in)
fl["theo_ex_b40"] = fl.theo - fl.gaming_date.map(zb.theo)
save_csv("07_floor_daily.csv", fl.assign(gaming_date=fl.gaming_date.dt.date).round(4))

# ---------------------------------------------------------------- helpers
def nw_ols(formula, df, lags=7):
    return smf.ols(formula, df).fit(cov_type="HAC", cov_kwds={"maxlags": lags})

def pct(x):
    return float(np.expm1(x))

def week_blocks(dates):
    d = pd.to_datetime(dates)
    return ((d - pd.Timestamp("2024-12-30")).dt.days // 7).values  # Monday-start weeks

# ---------------------------------------------------------------- closure DiD
CONTROLS = {
    "all_ex_B40_HL": ["Z-BAR", "Z-ENT", "Z-GAR", "Z-HTL", "Z-PIT"],
    "GAR_HTL_no_spillover": ["Z-GAR", "Z-HTL"],
    "all_ex_B40_incl_HL": ["Z-BAR", "Z-ENT", "Z-GAR", "Z-HTL", "Z-PIT", "Z-HL"],
    "PIT_HTL_not_adjacent": ["Z-PIT", "Z-HTL"],
}

def did_frame(ctrl, num, den):
    t = zone[zone.zone_id == "Z-B40"].set_index("gaming_date")
    cc = zone[zone.zone_id.isin(ctrl)].groupby("gaming_date")[[num] + ([den] if den else [])].sum(min_count=len(ctrl))
    f = pd.DataFrame({"tn": t[num], "cn": cc[num]})
    if den:
        f["td"], f["cd"] = t[den], cc[den]
    else:
        f["td"], f["cd"] = 1.0, 1.0
    f = f.dropna().reset_index()
    f["period"] = f.gaming_date.map(period)
    f["wd"] = f.gaming_date.dt.day_name()
    f["y"] = np.log((f.tn / f.td) / (f.cn / f.cd))
    f["closed"] = (f.period == "closed").astype(int)
    f["post"] = (f.period == "post").astype(int)
    f["wk"] = week_blocks(f.gaming_date)
    return f

def ros_ratio(f):  # ratio of sums: (B40 num/den) / (control num/den)
    return (f.tn.sum() / f.td.sum()) / (f.cn.sum() / f.cd.sum())

def did(ctrl, num, den):
    f = did_frame(ctrl, num, den)
    g = {p: f[f.period == p] for p in ["pre", "closed", "post"]}
    R = {p: ros_ratio(g[p]) for p in g}
    # week-block bootstrap within period
    boots = []
    blocks = {p: [g[p][g[p].wk == k] for k in g[p].wk.unique()] for p in g}
    for _ in range(NBOOT):
        rb = {}
        for p in g:
            bl = blocks[p]
            s = pd.concat([bl[i] for i in RNG.integers(0, len(bl), len(bl))])
            rb[p] = ros_ratio(s)
        boots.append([rb["pre"], rb["closed"] / rb["pre"] - 1, rb["post"] / rb["pre"] - 1, rb["post"]])
    boots = np.array(boots)
    ci = lambda j: [float(np.percentile(boots[:, j], 2.5)), float(np.percentile(boots[:, j], 97.5))]
    m = nw_ols("y ~ closed + post + C(wd)", f)
    b, se = m.params, m.bse
    res = {
        "controls": ctrl, "n_days": int(len(f)),
        "ratio_B40_vs_control": {p: float(R[p]) for p in R},
        "ratio_of_sums": {
            "structural_gap_pre": float(R["pre"] - 1), "structural_gap_pre_ci95": [x - 1 for x in ci(0)],
            "structural_gap_post": float(R["post"] - 1), "structural_gap_post_ci95": [x - 1 for x in ci(3)],
            "closure_effect": float(R["closed"] / R["pre"] - 1), "closure_effect_ci95": ci(1),
            "post_vs_pre": float(R["post"] / R["pre"] - 1), "post_vs_pre_ci95": ci(2),
            "method": "B40/control ratio of sums by period; CI = week-block bootstrap within period (2000 reps)"},
        "regression": {
            "closure_effect": pct(b["closed"]), "closure_effect_ci95": [pct(b["closed"] - 1.96 * se["closed"]), pct(b["closed"] + 1.96 * se["closed"])],
            "post_vs_pre": pct(b["post"]), "post_vs_pre_ci95": [pct(b["post"] - 1.96 * se["post"]), pct(b["post"] + 1.96 * se["post"])],
            "method": "daily log(B40 metric / control metric) ~ closed + post + weekday FE; Newey-West SE (7 lags)"},
    }
    return res, f

did_out = {"closure": {"walkway": "W-ENT-B40 (Main doors/Z-ENT <-> Back Forty/Z-B40)", "closed_from_utc": str(w.closed_from_utc),
                       "closed_to_utc": str(w.closed_to_utc), "gaming_dates": [str(CLOSE_FROM.date()), str((CLOSE_TO - pd.Timedelta(days=1)).date())],
                       "closed_gaming_days": CLOSED_DAYS,
                       "note": "Closure instants are exactly gaming-day boundaries (06:00 local), so closed days are whole gaming days."}}
frames = {}
for metric, (num, den) in {"coin_in_per_avail_h": ("coin_in", "available_h"), "theo_per_avail_h": ("theo", "available_h"),
                           "traffic_entries_adj": ("entries_adj", None),
                           "occupancy": ("occupied_h", "available_h")}.items():
    did_out[metric] = {}
    for name, ctrl in CONTROLS.items():
        r, f = did(ctrl, num, den)
        did_out[metric][name] = r
        frames[(metric, name)] = f
did_out["primary_control"] = "all_ex_B40_HL (BAR, ENT, GAR, HTL, PIT; HL excluded: separate high-limit room, ~6% occupancy, very different product)"
did_out["interpretation_notes"] = [
    "traffic structural_gap_* compares counts from different sensors (crossings, sensor placement differs) - NOT interpretable as a level gap; only the within-zone changes (closure_effect, post_vs_pre) are meaningful.",
    "post_vs_pre for cpah depends on control: PIT added 8 high-earning machines on 2025-03-24, inflating controls that include PIT; GAR_HTL control gives the cleanest post comparison.",
    "Spillover: during closure BAR traffic +14% and ENT traffic -13% vs GAR/HTL (rerouting past counters); PIT/HL cpah +3% - so controls incl. those zones slightly overstate the B40 effect (GAR_HTL control is the conservative estimate).",
]

# Lost coin-in / theo during closure (counterfactual: B40 keeps its pre-period ratio to control cpah)
for metric, num in [("coin_in", "coin_in"), ("theo", "theo")]:
    f = did_frame(CONTROLS["all_ex_B40_HL"], num, "available_h")
    Rpre = ros_ratio(f[f.period == "pre"])
    cl = f[f.period == "closed"]
    cf = (cl.td * cl.cn / cl.cd * Rpre).sum()
    did_out.setdefault("closure_cost_B40", {})[metric] = {
        "actual": float(cl.tn.sum()), "counterfactual": float(cf), "lost": float(cf - cl.tn.sum()),
        "lost_per_day": float((cf - cl.tn.sum()) / len(cl))}
did_out["closure_cost_B40"]["note"] = ("Gross loss in Z-B40 only. Some play appears to have shifted to other zones (see spillover), "
                                       "so the net floor loss is smaller; floor-level net effect is not identifiable without a control property.")

# spillover: each other zone, closed/pre change relative to the period change in GAR+HTL (flat zones)
sp = {}
for z in ["Z-BAR", "Z-ENT", "Z-PIT", "Z-HL", "Z-GAR", "Z-HTL"]:
    sub = zone[zone.zone_id == z]
    ref = zone[zone.zone_id.isin(["Z-GAR", "Z-HTL"]) & (zone.zone_id != z)]
    def rr(df, col, den):
        return {p: df[df.period == p][col].sum() / (df[df.period == p][den].sum() if den else df[df.period == p].gaming_date.nunique()) for p in ["pre", "closed", "post"]}
    a, b_ = rr(sub, "coin_in", "available_h"), rr(ref, "coin_in", "available_h")
    ta, tb = rr(sub, "entries_adj", None), rr(ref, "entries_adj", None)
    sp[z] = {"cpah_closed_vs_pre": float(a["closed"] / a["pre"] - 1),
             "cpah_closed_vs_pre_rel_GAR_HTL": float((a["closed"] / a["pre"]) / (b_["closed"] / b_["pre"]) - 1),
             "traffic_closed_vs_pre": float(ta["closed"] / ta["pre"] - 1),
             "traffic_closed_vs_pre_rel_GAR_HTL": float((ta["closed"] / ta["pre"]) / (tb["closed"] / tb["pre"]) - 1)}
did_out["spillover_other_zones"] = sp

# recovery: weekly B40/control cpah ratio relative to pre-period ratio
f = frames[("coin_in_per_avail_h", "all_ex_B40_HL")]
Rpre = ros_ratio(f[f.period == "pre"])
wk = f.groupby("wk").apply(lambda g: pd.Series({"week_start": g.gaming_date.min().date(), "days": len(g),
                                                 "period": g.period.mode()[0], "ratio": ros_ratio(g)}), include_groups=False)
wk["index_vs_pre"] = wk.ratio / Rpre
post_w = wk[(wk.period == "post") & (wk.days == 7)]
ft = frames[("traffic_entries_adj", "all_ex_B40_HL")]
Rpre_t = ros_ratio(ft[ft.period == "pre"])
wkt = ft.groupby("wk").apply(lambda g: ros_ratio(g), include_groups=False) / Rpre_t
did_out["recovery"] = {
    "weekly_cpah_index_vs_pre (1.0 = pre-closure B40/control ratio)": [
        {"week_start": str(r.week_start), "period": r.period, "days": int(r.days), "cpah_index": round(float(r.index_vs_pre), 3),
         "traffic_index": round(float(wkt.loc[k]), 3)} for k, r in wk.iterrows()],
    "first_full_post_week_cpah_index": float(post_w.index_vs_pre.iloc[0]),
    "post_full_weeks_mean_cpah_index": float(post_w.index_vs_pre.mean()),
    "share_of_closure_gap_recovered_cpah": float(1 - did_out["coin_in_per_avail_h"]["all_ex_B40_HL"]["ratio_of_sums"]["post_vs_pre"]
                                                 / did_out["coin_in_per_avail_h"]["all_ex_B40_HL"]["ratio_of_sums"]["closure_effect"]),
}

# structural location effect, method 2: same game configuration across zones (pre-closure, then post)
cfg = q(c, """select case when gaming_date < DATE '2025-03-03' then 'pre' when gaming_date < DATE '2025-04-07' then 'closed' else 'post' end period,
              config_id, zone_id='Z-B40' b40, sum(coin_in_usd)::double ci, sum(theo_usd)::double th, sum(available_hours) ah
              from pdv where location_id is not null and zone_id <> 'Z-HL' group by all""")
st = {}
for p in ["pre", "closed", "post"]:
    x = cfg[cfg.period == p].pivot_table(index="config_id", columns="b40", values=["ci", "ah", "th"], aggfunc="sum").dropna()
    r = (x["ci"][True] / x["ah"][True]) / (x["ci"][False] / x["ah"][False])
    wts = x["ah"][True]
    st[p] = {"within_config_B40_vs_rest_cpah_ratio_weighted": float((r * wts).sum() / wts.sum()),
             "per_config": {k: round(float(v), 3) for k, v in r.items()},
             "theo_hold_B40": float(x["th"][True].sum() / x["ci"][True].sum()), "theo_hold_rest": float(x["th"][False].sum() / x["ci"][False].sum())}
did_out["structural_same_config"] = st
# decomposition: cpah = occupancy x coin-in per occupied hour
dec = {}
for p in ["pre", "closed", "post"]:
    s = zone[zone.period == p]
    b4, ot = s[s.zone_id == "Z-B40"], s[s.zone_id.isin(CONTROLS["all_ex_B40_HL"])]
    dec[p] = {"occupancy_B40": float(b4.occupied_h.sum() / b4.available_h.sum()), "occupancy_ctrl": float(ot.occupied_h.sum() / ot.available_h.sum()),
              "coin_per_occupied_h_B40": float(b4.coin_in.sum() / b4.occupied_h.sum()), "coin_per_occupied_h_ctrl": float(ot.coin_in.sum() / ot.occupied_h.sum())}
did_out["decomposition_occupancy_vs_spend"] = dec

# ---------------------------------------------------------------- machine moves
plc = q(c, """select p.machine_id, p.location_id, l.zone_id, p.effective_from_utc, p.effective_to_utc
              from plc p join locs l using(location_id)
              where machine_id in (select machine_id from plc group by 1 having count(*)>1) order by 1,4""")
md = q(c, """select machine_id, gaming_date, zone_id, coin_in_usd::double ci, theo_usd::double th, available_hours ah
             from pdv where location_id is not null""")
md["gaming_date"] = pd.to_datetime(md.gaming_date)
moves = []
for mid, g in plc.groupby("machine_id"):
    g = g.sort_values("effective_from_utc")
    old, new = g.iloc[0], g.iloc[1]
    mdate = q(c, f"select gaming_date from cal where gaming_day_start_utc = TIMESTAMP '{new.effective_from_utc}'").gaming_date.iloc[0]
    mdate = pd.Timestamp(mdate)
    m = md[md.machine_id == mid]
    others = md[md.machine_id != mid]
    rec = {"machine_id": mid, "move_gaming_date": mdate.date(), "old_location": old.location_id, "old_zone": old.zone_id,
           "new_location": new.location_id, "new_zone": new.zone_id}
    for lab, sel in [("before", m.gaming_date < mdate), ("after", m.gaming_date > mdate)]:  # move day excluded
        mm = m[sel]
        dates = mm.gaming_date
        rec[f"days_{lab}"] = len(mm)
        rec[f"cpah_{lab}"] = mm.ci.sum() / mm.ah.sum()
        rec[f"theo_per_h_{lab}"] = mm.th.sum() / mm.ah.sum()
        for zlab, z in [("old_zone", old.zone_id), ("new_zone", new.zone_id), ("floor", None)]:
            o = others[others.gaming_date.isin(dates) & ((others.zone_id == z) if z else True)]
            rec[f"{zlab}_cpah_{lab}"] = o.ci.sum() / o.ah.sum()
    rec["machine_index_vs_old_zone_before"] = rec["cpah_before"] / rec["old_zone_cpah_before"]
    rec["machine_index_vs_new_zone_after"] = rec["cpah_after"] / rec["new_zone_cpah_after"]
    rec["machine_index_vs_floor_before"] = rec["cpah_before"] / rec["floor_cpah_before"]
    rec["machine_index_vs_floor_after"] = rec["cpah_after"] / rec["floor_cpah_after"]
    rec["machine_change_after_vs_before"] = rec["cpah_after"] / rec["cpah_before"] - 1
    rec["zone_gap_new_vs_old_during_after"] = rec["new_zone_cpah_after"] / rec["old_zone_cpah_after"] - 1
    # bootstrap CI (days) for change in machine cpah relative to floor
    mb, ma = m[m.gaming_date < mdate], m[m.gaming_date > mdate]
    fl_ci = others.groupby("gaming_date").agg(ci=("ci", "sum"), ah=("ah", "sum"))
    bs = []
    for _ in range(NBOOT):
        sb, sa = mb.sample(len(mb), replace=True, random_state=RNG.integers(1e9)), ma.sample(len(ma), replace=True, random_state=RNG.integers(1e9))
        fb, fa = fl_ci.loc[sb.gaming_date], fl_ci.loc[sa.gaming_date]
        bs.append((sa.ci.sum() / sa.ah.sum() / (fa.ci.sum() / fa.ah.sum())) / (sb.ci.sum() / sb.ah.sum() / (fb.ci.sum() / fb.ah.sum())) - 1)
    rec["floor_relative_change"] = rec["machine_index_vs_floor_after"] / rec["machine_index_vs_floor_before"] - 1
    rec["floor_relative_change_ci95_lo"], rec["floor_relative_change_ci95_hi"] = np.percentile(bs, [2.5, 97.5])
    moves.append(rec)
moves = pd.DataFrame(moves)
save_csv("07_moves.csv", moves.round(4))

# ---------------------------------------------------------------- events
ev = q(c, "select event_id, name, event_type, start_date, end_date, local_scope from events order by start_date")
ev["start_date"], ev["end_date"] = pd.to_datetime(ev.start_date), pd.to_datetime(ev.end_date)
F = fl.set_index("gaming_date")
F["is_event"] = F.events_active.notna()
F["is_hol"] = F.federal_holiday.notna()
clean = ~F.is_event & ~F.is_hol
dates_all = F.index

def baseline(day, metric, exclude=()):
    cand = F[(F.index != day) & (F.weekday == F.loc[day, "weekday"]) & clean & (abs((F.index - day).days) <= 28)]
    cand = cand[~cand.index.isin(exclude)]
    return cand[metric].mean(), len(cand)

def uplift(days, metric, exclude=()):
    act = F.loc[days, metric].sum()
    bl = [baseline(d, metric, exclude) for d in days]
    base = sum(b for b, _ in bl)
    return act, base, min(n for _, n in bl)

def placebo(days, metric):
    """Same-weekday shifted windows (multiples of 7 days) made only of clean days; same baseline procedure."""
    res = []
    for k in range(-26, 27):
        if k == 0:
            continue
        sd = [d + pd.Timedelta(days=7 * k) for d in days]
        if all(x in dates_all for x in sd) and clean.loc[sd].all():
            a, b, n = uplift(sd, metric, exclude=sd)
            if n >= 2:
                res.append(a / b - 1)
    return np.array(res)

# regression (method 2): log metric ~ weekday + month + holiday + B40 closure + event dummies, NW SE
R = F.reset_index().copy()
for e in ev.itertuples():
    R[e.event_id.replace("-", "_")] = ((R.gaming_date >= e.start_date) & (R.gaming_date <= e.end_date)).astype(int)
R["hol"] = R.federal_holiday.notna().astype(int)
R["closed"] = (R.b40_closure_period == "closed").astype(int)
R["lci"], R["lth"], R["lte"] = np.log(R.coin_in), np.log(R.theo), np.log(R.entries_adj)
evcols = [e.replace("-", "_") for e in ev.event_id]
regs = {m: nw_ols(f"{m} ~ C(weekday) + C(month) + hol + closed + " + " + ".join(evcols), R) for m in ["lci", "lth", "lte"]}
for t in ev.event_type.unique():
    R["T_" + t] = R[[e.replace("-", "_") for e in ev[ev.event_type == t].event_id]].max(axis=1)
tcols = ["T_" + t for t in ev.event_type.unique()]
regs_t = {m: nw_ols(f"{m} ~ C(weekday) + C(month) + hol + closed + " + " + ".join(tcols), R) for m in ["lci", "lth"]}

rows = []
def ev_row(label, etype, days, scope):
    r = {"event_id": label, "event_type": etype, "scope": scope, "start": days[0].date(), "end": days[-1].date(), "days": len(days)}
    for metric, lab in [("coin_in", "coin_in"), ("theo", "theo"), ("entries_adj", "traffic"), ("coin_in_ex_b40", "coin_in_ex_b40")]:
        a, b, n = uplift(days, metric)
        r[f"{lab}_actual"], r[f"{lab}_baseline"], r[f"{lab}_uplift"] = a, b, a / b - 1
        if lab in ("coin_in", "theo"):
            r[f"{lab}_incremental_usd"] = a - b
        if lab in ("coin_in", "theo", "traffic"):
            pl = placebo(days, metric)
            r[f"{lab}_uplift_ci95_lo"] = r[f"{lab}_uplift"] - np.percentile(pl, 97.5)
            r[f"{lab}_uplift_ci95_hi"] = r[f"{lab}_uplift"] - np.percentile(pl, 2.5)
            r[f"{lab}_placebo_p"] = float((np.abs(pl) >= abs(r[f"{lab}_uplift"])).mean())
            r[f"{lab}_n_placebo"] = len(pl)
            if lab == "theo":
                r["theo_incremental_ci95_lo"] = b * r["theo_uplift_ci95_lo"]
                r["theo_incremental_ci95_hi"] = b * r["theo_uplift_ci95_hi"]
        r["min_baseline_days_per_day"] = n
    return r

for e in ev.itertuples():
    days = list(pd.date_range(e.start_date, e.end_date))
    r = ev_row(e.event_id, e.event_type, days, e.local_scope)
    r["name"] = e.name
    col = e.event_id.replace("-", "_")
    for m, lab in [("lci", "coin_in"), ("lth", "theo"), ("lte", "traffic")]:
        b, se = regs[m].params[col], regs[m].bse[col]
        r[f"reg_{lab}_uplift"], r[f"reg_{lab}_ci95_lo"], r[f"reg_{lab}_ci95_hi"] = pct(b), pct(b - 1.96 * se), pct(b + 1.96 * se)
    r["overlaps_b40_closure"] = bool(any(CLOSE_FROM <= d < CLOSE_TO for d in days))
    rows.append(r)
evt = pd.DataFrame(rows)
# per event type: pooled matched-baseline (ratio of sums) + regression
trow = []
for t, g in evt.groupby("event_type"):
    r = {"event_id": "TYPE:" + t, "event_type": t, "scope": ",".join(sorted(g.scope.unique())), "days": int(g.days.sum()), "name": f"all {t} events"}
    for lab in ["coin_in", "theo", "traffic", "coin_in_ex_b40"]:
        r[f"{lab}_actual"], r[f"{lab}_baseline"] = g[f"{lab}_actual"].sum(), g[f"{lab}_baseline"].sum()
        r[f"{lab}_uplift"] = r[f"{lab}_actual"] / r[f"{lab}_baseline"] - 1
    r["coin_in_incremental_usd"], r["theo_incremental_usd"] = g.coin_in_incremental_usd.sum(), g.theo_incremental_usd.sum()
    for m, lab in [("lci", "coin_in"), ("lth", "theo")]:
        b, se = regs_t[m].params["T_" + t], regs_t[m].bse["T_" + t]
        r[f"reg_{lab}_uplift"], r[f"reg_{lab}_ci95_lo"], r[f"reg_{lab}_ci95_hi"] = pct(b), pct(b - 1.96 * se), pct(b + 1.96 * se)
    trow.append(r)
evt = pd.concat([evt, pd.DataFrame(trow)], ignore_index=True)
evt["is_property_promotion"] = evt.event_type == "property_promotion"
first = ["event_id", "name", "event_type", "scope", "is_property_promotion", "start", "end", "days", "coin_in_uplift",
         "coin_in_uplift_ci95_lo", "coin_in_uplift_ci95_hi", "coin_in_placebo_p", "reg_coin_in_uplift", "reg_coin_in_ci95_lo",
         "reg_coin_in_ci95_hi", "coin_in_incremental_usd", "theo_uplift", "theo_incremental_usd", "theo_incremental_ci95_lo",
         "theo_incremental_ci95_hi"]
evt = evt[first + [x for x in evt.columns if x not in first]]
save_csv("07_event_effects.csv", evt.round(4))

# ---------------------------------------------------------------- monthly
city = q(c, "select month, visitor_volume, mean_visitors_per_calendar_day::double v, visitor_yoy_ratio::double yoy, hotel_occupancy_ratio::double occ, convention_attendance from city")
sv = q(c, "select month, survey_metric, estimate from survey").pivot(index="month", columns="survey_metric", values="estimate")
mo = fl.groupby("month").agg(days=("gaming_date", "count"), coin_in=("coin_in", "sum"), theo=("theo", "sum"), house_win=("house_win", "sum"),
                             avail_h=("available_machine_hours", "sum"), occ_h=("occupied_machine_hours", "sum"),
                             entries=("entries_adj", "sum"), active_machines=("active_machines", "mean")).reset_index()
mo["coin_in_per_day"], mo["theo_per_day"], mo["house_win_per_day"] = mo.coin_in / mo.days, mo.theo / mo.days, mo.house_win / mo.days
mo["coin_in_per_avail_h"], mo["theo_per_avail_h"] = mo.coin_in / mo.avail_h, mo.theo / mo.avail_h
mo["occupancy"] = mo.occ_h / mo.avail_h
mo["traffic_entries_adj_per_day"] = mo.entries / mo.days
mo["coin_in_per_day_index_jan"] = mo.coin_in_per_day / mo.coin_in_per_day.iloc[0]
mo["traffic_per_day_index_jan"] = mo.traffic_entries_adj_per_day / mo.traffic_entries_adj_per_day.iloc[0]
# clean-day (no event/holiday), weekday-mix-adjusted coin-in: mean over weekdays of month x weekday means
cl = fl[fl.events_active.isna() & fl.federal_holiday.isna()]
mo["coin_in_per_day_clean_weekday_adj"] = mo.month.map(cl.groupby(["month", "weekday"]).coin_in.mean().groupby("month").mean())
mo = mo.merge(city.rename(columns={"v": "CONTEXT_lvcva_visitors_per_day", "yoy": "CONTEXT_lvcva_visitor_yoy",
                                   "occ": "CONTEXT_lvcva_hotel_occupancy", "convention_attendance": "CONTEXT_lvcva_convention_attendance"})
              .drop(columns="visitor_volume"), on="month", how="left")
mo["CONTEXT_lvcva_visitors_index_jan"] = mo.CONTEXT_lvcva_visitors_per_day / mo.CONTEXT_lvcva_visitors_per_day.iloc[0]
mo["survey_lives_locally_share"] = mo.month.map(sv["lives_locally"])
mo["survey_purpose_convention_share"] = mo.month.map(sv["purpose_convention"])
mo["context_note"] = "CONTEXT_lvcva_* = all Las Vegas visitors (LVCVA, Aug-2026 revision; not knowable in 2025, not this property)"
mo = mo.drop(columns=["coin_in", "theo", "house_win", "avail_h", "occ_h", "entries"])
save_csv("07_monthly.csv", mo.round(4))

# ---------------------------------------------------------------- other demand shifts
summ = {}
# (a) floor trend: log coin-in per avail h ~ weekday + events + hol + closure + linear time
R["t_months"] = (R.gaming_date - R.gaming_date.min()).dt.days / 30.4
R["lcpah"] = np.log(R.coin_in_per_avail_h)
R["any_event"] = R.is_event.astype(int)
mt = nw_ols("lcpah ~ C(weekday) + any_event + hol + closed + t_months", R)
mt2 = nw_ols("lci ~ C(weekday) + any_event + hol + closed + t_months", R)
summ["floor_trend"] = {
    "cpah_pct_per_month": pct(mt.params["t_months"]), "cpah_ci95": [pct(mt.params["t_months"] - 1.96 * mt.bse["t_months"]), pct(mt.params["t_months"] + 1.96 * mt.bse["t_months"])],
    "coin_in_per_day_pct_per_month": pct(mt2.params["t_months"]), "coin_in_ci95": [pct(mt2.params["t_months"] - 1.96 * mt2.bse["t_months"]), pct(mt2.params["t_months"] + 1.96 * mt2.bse["t_months"])],
    "monthly_coin_in_per_day": {r.month: round(r.coin_in_per_day) for r in mo.itertuples()},
    "monthly_coin_in_per_day_clean_weekday_adj": {r.month: round(r.coin_in_per_day_clean_weekday_adj) for r in mo.itertuples()},
    "monthly_cpah": {r.month: round(r.coin_in_per_avail_h, 1) for r in mo.itertuples()},
    "jun_vs_feb_clean_weekday_adj": float(mo.coin_in_per_day_clean_weekday_adj.iloc[5] / mo.coin_in_per_day_clean_weekday_adj.iloc[1] - 1),
    "context_lvcva_visitors_per_day_jun_vs_feb": float(mo.CONTEXT_lvcva_visitors_per_day.iloc[5] / mo.CONTEXT_lvcva_visitors_per_day.iloc[1] - 1),
}
# (b) weekday shape by month (share of week's coin-in, clean days)
ws = cl.groupby(["month", "weekday"]).coin_in.mean().unstack()
ws = ws.div(ws.sum(axis=1), axis=0)
summ["weekday_shape"] = {"weekend_FriSatSun_share_by_month": ws[["Fri", "Sat", "Sun"]].sum(axis=1).round(4).to_dict(),
                         "max_abs_share_change_any_weekday_vs_jan": float((ws - ws.iloc[0]).abs().max().max())}
# (c) hour-of-day shape: traffic (coverage-adjusted rate) and coin-in (machine_hour), by month
ht = q(c, """select cal.month, t.local_hour, sum(t.entries_count)/sum(case when t.entries_count is not null then t.coverage_minutes end) rate
            from traffic t join cal on t.hour_start_utc>=cal.gaming_day_start_utc and t.hour_start_utc<cal.gaming_day_end_utc group by 1,2""")
ht = ht.pivot(index="month", columns="local_hour", values="rate"); ht = ht.div(ht.sum(axis=1), axis=0)
hm = q(c, "select strftime(gaming_date,'%Y-%m') AS month, local_hour, sum(coin_in_cents)::double ci from mh group by 1,2")
hm = hm.pivot(index="month", columns="local_hour", values="ci"); hm = hm.div(hm.sum(axis=1), axis=0)
summ["hour_of_day_shape"] = {"traffic_max_abs_hour_share_change_vs_jan": float((ht - ht.iloc[0]).abs().max().max()),
                             "coin_in_max_abs_hour_share_change_vs_jan": float((hm - hm.iloc[0]).abs().max().max()),
                             "coin_in_evening_17_23_share_by_month": hm[[17, 18, 19, 20, 21, 22, 23]].sum(axis=1).round(4).to_dict()}
# (d) zone trends relative to rest of floor (excluding closure period)
zt = {}
for z in sorted(zone.zone_id.unique()):
    a = zone[zone.zone_id == z].set_index("gaming_date")
    b = zone[zone.zone_id != z].groupby("gaming_date")[["coin_in", "available_h"]].sum()
    d = pd.DataFrame({"y": np.log((a.coin_in / a.available_h) / (b.coin_in / b.available_h))}).reset_index()
    d["t"] = (d.gaming_date - d.gaming_date.min()).dt.days / 30.4
    d["wd"] = d.gaming_date.dt.day_name()
    d = d[d.gaming_date.map(period) != "closed"]
    m = nw_ols("y ~ t + C(wd)", d)
    zt[z] = {"rel_cpah_pct_per_month_ex_closure": pct(m.params["t"]), "ci95": [pct(m.params["t"] - 1.96 * m.bse["t"]), pct(m.params["t"] + 1.96 * m.bse["t"])]}
summ["zone_trends_vs_rest_of_floor"] = zt
summ["zone_period_cpah"] = {z: {p: round(float(g[g.period == p].coin_in.sum() / g[g.period == p].available_h.sum()), 1) for p in ["pre", "closed", "post"]}
                            for z, g in zone.groupby("zone_id")}
summ["zone_period_traffic_per_day"] = {z: {p: round(float(g[g.period == p].entries_adj.mean()), 0) for p in ["pre", "closed", "post"]}
                                       for z, g in zone.groupby("zone_id")}
# (e) PIT expansion (8 installs 2025-03-24): did zone total coin-in grow, and did incumbents lose?
inst = q(c, "select o.machine_id, o.opened_at_utc from ops o join locs l using(location_id) where o.event_type='install' and l.zone_id='Z-PIT'")
newm = set(inst.machine_id)
pre_w = (md.gaming_date >= "2025-01-27") & (md.gaming_date < "2025-03-03")   # 5 clean weeks before closure
post_w = (md.gaming_date >= "2025-04-07") & (md.gaming_date < "2025-05-12")  # 5 weeks after reopening
ctrlz = ["Z-BAR", "Z-ENT", "Z-GAR", "Z-HTL"]
def agg(sel):
    s = md[sel]
    nd = s.gaming_date.nunique()
    return s.ci.sum() / nd, s.ci.sum() / s.ah.sum()
pit_tot = [agg(w_ & (md.zone_id == "Z-PIT")) for w_ in (pre_w, post_w)]
pit_inc = [agg(w_ & (md.zone_id == "Z-PIT") & ~md.machine_id.isin(newm)) for w_ in (pre_w, post_w)]
ctl = [agg(w_ & md.zone_id.isin(ctrlz)) for w_ in (pre_w, post_w)]
summ["pit_expansion_2025_03_24"] = {
    "new_machines": len(newm), "windows": ["2025-01-27..2025-03-02", "2025-04-07..2025-05-11"],
    "pit_zone_coin_in_per_day_change": pit_tot[1][0] / pit_tot[0][0] - 1,
    "control_zones_coin_in_per_day_change": ctl[1][0] / ctl[0][0] - 1,
    "pit_total_did": (pit_tot[1][0] / pit_tot[0][0]) / (ctl[1][0] / ctl[0][0]) - 1,
    "pit_incumbent_cpah_change": pit_inc[1][1] / pit_inc[0][1] - 1,
    "control_cpah_change": ctl[1][1] / ctl[0][1] - 1,
    "pit_incumbent_cpah_did": (pit_inc[1][1] / pit_inc[0][1]) / (ctl[1][1] / ctl[0][1]) - 1,
    "new_machines_cpah_post": agg(post_w & md.machine_id.isin(newm))[1],
    "note": "Control zones BAR/ENT/GAR/HTL; windows avoid the B40 closure. Single pre/post comparison, no CI."}
# (f) weather: residual of log coin-in after calendar controls vs tmax / rain
wx = q(c, "select source_date, tmax_c::double tmax, prcp_mm::double prcp from weather")
wx["gaming_date"] = pd.to_datetime(wx.source_date)
RW = R.merge(wx, on="gaming_date", how="left")
RW["rain"] = (RW.prcp > 0).astype(int)
mw = nw_ols("lci ~ C(weekday) + C(month) + hol + closed + " + " + ".join(evcols) + " + tmax + rain", RW.dropna(subset=["tmax", "prcp"]))
summ["weather"] = {"coin_in_pct_per_1C_tmax_within_month": pct(mw.params["tmax"]), "tmax_p": float(mw.pvalues["tmax"]),
                   "coin_in_pct_rain_day": pct(mw.params["rain"]), "rain_p": float(mw.pvalues["rain"]), "rain_days": int(RW.rain.sum()),
                   "note": "calendar-date weather aligned to gaming date; month FE so seasonal heat is absorbed"}
# (g) holidays (non-event) vs matched baseline
hol_rows = {}
for d in F[F.is_hol & ~F.is_event].index:
    a, b, n = uplift([d], "coin_in")
    hol_rows[f"{d.date()} {F.loc[d, 'federal_holiday']} ({F.loc[d, 'weekday']})"] = round(a / b - 1, 4)
summ["federal_holidays_coin_in_uplift_vs_same_weekday"] = hol_rows

# headline
p = did_out["coin_in_per_avail_h"]["all_ex_B40_HL"]
tr = did_out["traffic_entries_adj"]["all_ex_B40_HL"]
promo = evt[evt.event_type == "property_promotion"].set_index("event_id")
summ["headline"] = {
    "b40_closure_cpah_effect": p["ratio_of_sums"]["closure_effect"], "b40_closure_cpah_ci95": p["ratio_of_sums"]["closure_effect_ci95"],
    "b40_closure_cpah_effect_regression": p["regression"]["closure_effect"],
    "b40_closure_traffic_effect": tr["ratio_of_sums"]["closure_effect"], "b40_closure_traffic_ci95": tr["ratio_of_sums"]["closure_effect_ci95"],
    "b40_closure_lost_coin_in": did_out["closure_cost_B40"]["coin_in"]["lost"], "b40_closure_lost_theo": did_out["closure_cost_B40"]["theo"]["lost"],
    "b40_structural_gap_pre": p["ratio_of_sums"]["structural_gap_pre"], "b40_structural_gap_pre_ci95": p["ratio_of_sums"]["structural_gap_pre_ci95"],
    "b40_structural_gap_post": p["ratio_of_sums"]["structural_gap_post"],
    "b40_within_config_ratio_pre": st["pre"]["within_config_B40_vs_rest_cpah_ratio_weighted"],
    "b40_post_vs_pre_residual": p["ratio_of_sums"]["post_vs_pre"], "b40_post_vs_pre_ci95": p["ratio_of_sums"]["post_vs_pre_ci95"],
    "promo_TOURN_coin_in_uplift": float(promo.loc["EV-P-TOURN", "coin_in_uplift"]), "promo_TOURN_incremental_theo": float(promo.loc["EV-P-TOURN", "theo_incremental_usd"]),
    "promo_TOURN_incremental_theo_ci95": [float(promo.loc["EV-P-TOURN", "theo_incremental_ci95_lo"]), float(promo.loc["EV-P-TOURN", "theo_incremental_ci95_hi"])],
    "promo_LOCALS_coin_in_uplift": float(promo.loc["EV-P-LOCALS", "coin_in_uplift"]), "promo_LOCALS_incremental_theo": float(promo.loc["EV-P-LOCALS", "theo_incremental_usd"]),
    "promo_LOCALS_incremental_theo_ci95": [float(promo.loc["EV-P-LOCALS", "theo_incremental_ci95_lo"]), float(promo.loc["EV-P-LOCALS", "theo_incremental_ci95_hi"])],
    "promo_action_cost_usd": 15000,
}
save_json("07_closure_did.json", did_out)
save_json("07_summary.json", summ)

# ---------------------------------------------------------------- short print
h = summ["headline"]
print(f"B40 closure: cpah {h['b40_closure_cpah_effect']:+.1%} CI {h['b40_closure_cpah_ci95'][0]:+.1%}..{h['b40_closure_cpah_ci95'][1]:+.1%} "
      f"(reg {h['b40_closure_cpah_effect_regression']:+.1%}); traffic {h['b40_closure_traffic_effect']:+.1%}; lost coin-in ${h['b40_closure_lost_coin_in']:,.0f}, theo ${h['b40_closure_lost_theo']:,.0f}")
print(f"B40 structural gap pre {h['b40_structural_gap_pre']:+.1%} post {h['b40_structural_gap_post']:+.1%}; within-config ratio {h['b40_within_config_ratio_pre']:.2f}; post vs pre {h['b40_post_vs_pre_residual']:+.1%}")
for k, v in CONTROLS.items():
    r = did_out["coin_in_per_avail_h"][k]["ratio_of_sums"]; t = did_out["traffic_entries_adj"][k]["ratio_of_sums"]
    print(f"  ctrl {k}: cpah {r['closure_effect']:+.1%}  traffic {t['closure_effect']:+.1%}")
print(evt[["event_id", "days", "coin_in_uplift", "coin_in_uplift_ci95_lo", "coin_in_uplift_ci95_hi", "reg_coin_in_uplift", "theo_incremental_usd"]].round(3).to_string(index=False))
print(moves[["machine_id", "old_zone", "new_zone", "cpah_before", "cpah_after", "machine_index_vs_old_zone_before", "machine_index_vs_new_zone_after", "floor_relative_change"]].round(3).to_string(index=False))
print("trend", {k: round(v, 4) if isinstance(v, float) else v for k, v in summ["floor_trend"].items() if "pct" in k})
print("pit", {k: round(v, 3) for k, v in summ["pit_expansion_2025_03_24"].items() if isinstance(v, float)})
print("weather", summ["weather"]); print("zones", {z: round(v["rel_cpah_pct_per_month_ex_closure"], 4) for z, v in zt.items()})
print("hours", {k: v for k, v in summ["hour_of_day_shape"].items() if "max" in k}, "wd", summ["weekday_shape"]["max_abs_share_change_any_weekday_vs_jan"])

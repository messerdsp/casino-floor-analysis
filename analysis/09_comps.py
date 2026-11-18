"""09 Q5: What did the loyalty (comp) program cost, and did anything about it change?

Money rules
- Comp cost = ECONOMIC cost (loyalty_ledger.economic_cost_cents == linked POS cogs == player_period.comp_cost_usd).
  It is already inside POS cogs: count it once. Face value is menu price, NOT revenue.
- Comp reinvestment = redeemed comp economic cost / carded theo, for the stated period (ratio of sums).
- Points cost per comp dollar = points redeemed / comp FACE value $ (also shown per economic-cost $).
- Ledger and POS timestamps -> gaming date via calendar_day [gaming_day_start_utc, gaming_day_end_utc).

Change detection
- Method 1: single mean-shift change-point scan (min SSE) over daily series.
- Method 2: event-level exact per-tier rates (points / face $) and per-benefit before/after; first/last timestamps.
- Behaviour: interrupted time series (Feb-Apr pre-trend, weekday dummies, Newey-West HAC CI) on redemptions,
  F&B and play; DiD carded vs uncarded coin-in (uncarded guests cannot redeem comps); per-player DiD
  (Jan-Feb comp redeemers vs non-redeemers) with placebo period.

Writes outputs/09_comp_monthly.csv (month + week rows), 09_comp_daily.csv, 09_comp_change.json,
09_comp_by_tier.csv, 09_action_benefits.json, 09_summary.json."""
from common import con, q, save_csv, save_json
import numpy as np
import pandas as pd
import statsmodels.api as sm

c = con()
CHANGE = pd.Timestamp("2025-05-01")

# ---------- 0. Gaming-date ledger and reconciliation ----------
c.execute("""create temp table l as
  select l.*, c.gaming_date, c.gaming_day_start_utc from ledger l join cal c
  on l.event_at_utc >= c.gaming_day_start_utc and l.event_at_utc < c.gaming_day_end_utc""")
out_of_window = q(c, "select count(*) n, sum(points_delta) pts from ledger where event_at_utc >= (select max(gaming_day_end_utc) from cal) or event_at_utc < (select min(gaming_day_start_utc) from cal)").iloc[0]
recon = q(c, """select
  (select sum(economic_cost_cents)/100.0 from ledger where event_type='redeem') ledger_econ,
  (select sum(f.cogs_cents)/100.0 from fnb f where comp_id is not null) fnb_comp_cogs,
  (select sum(comp_cost_usd)::double from pp) pp_comp_cost,
  (select sum(comp_cost_usd)::double from prop) prop_comp_cost,
  (select sum(pos_cogs_usd)::double from prop) prop_pos_cogs_incl_comps,
  (select sum(face_value_cents)/100.0 from ledger where event_type='redeem') ledger_face,
  (select count(*) from fnb f join ledger l on l.redeemed_pos_id=f.transaction_id
      where f.cogs_cents<>l.economic_cost_cents or f.face_value_cents<>l.face_value_cents) mismatched_links,
  (select count(*) from fnb where comp_id is not null and cash_receipt_cents<>0) comp_tickets_with_cash""").iloc[0]
lag = q(c, """select event_type, count(*) n, avg(date_diff('second', event_at_utc, available_at_utc)) mean_lag_s,
  max(date_diff('second', event_at_utc, available_at_utc)) max_lag_s,
  count(*) filter (where available_at_utc<>event_at_utc) n_lagged from ledger group by 1""")

# ---------- 1. Daily series (each table aggregated before joining) ----------
d = q(c, """
with lg as (select gaming_date,
    count(*) filter (where event_type='redeem') comps_redeemed,
    sum(economic_cost_cents)/100.0 comp_econ_cost, sum(face_value_cents)/100.0 comp_face_value,
    -sum(points_delta) filter (where event_type='redeem') points_redeemed,
    sum(points_delta) filter (where event_type='award') points_earned,
    count(distinct player_id) filter (where event_type='redeem') redeeming_players
  from l group by 1),
m as (select gaming_date, sum(carded_coin_in_cents)/100.0 carded_coin_in, sum(coin_in_cents-carded_coin_in_cents)/100.0 uncarded_coin_in,
    sum(carded_theo_cents)::double/100.0 carded_theo, sum(theo_cents-carded_theo_cents)::double/100.0 uncarded_theo from md group by 1),
f as (select c.gaming_date,
    count(*) filter (where player_id is not null and comp_id is null) carded_cash_tickets,
    sum(cash_receipt_cents) filter (where player_id is not null)/100.0 carded_fnb_cash,
    count(*) filter (where player_id is null) uncarded_tickets,
    sum(cash_receipt_cents) filter (where player_id is null)/100.0 uncarded_fnb_cash
  from fnb f join cal c on f.event_at_utc >= c.gaming_day_start_utc and f.event_at_utc < c.gaming_day_end_utc group by 1),
t as (select c.gaming_date, count(*) visits, count(distinct player_id) visiting_players
  from trips t join cal c on t.first_observed_at_utc >= c.gaming_day_start_utc and t.first_observed_at_utc < c.gaming_day_end_utc group by 1)
select cal.gaming_date, cal.local_weekday, cal.month, lg.*exclude(gaming_date), m.*exclude(gaming_date),
       f.*exclude(gaming_date), t.*exclude(gaming_date)
from cal left join lg using(gaming_date) left join m using(gaming_date) left join f using(gaming_date)
left join t using(gaming_date) order by 1""")
d["gaming_date"] = pd.to_datetime(d.gaming_date)
d = d.fillna({k: 0 for k in ["comps_redeemed", "comp_econ_cost", "comp_face_value", "points_redeemed", "points_earned"]})

def derive(x):
    x["reinvestment"] = x.comp_econ_cost / x.carded_theo
    x["points_per_face_usd"] = x.points_redeemed / x.comp_face_value
    x["points_per_econ_usd"] = x.points_redeemed / x.comp_econ_cost
    x["econ_cost_per_face"] = x.comp_econ_cost / x.comp_face_value
    x["points_earned_per_coin_in_usd"] = x.points_earned / x.carded_coin_in
    x["comps_per_visit"] = x.comps_redeemed / x.visits
    x["comp_share_of_carded_fnb_tickets"] = x.comps_redeemed / (x.comps_redeemed + x.carded_cash_tickets)
    x["carded_fnb_tickets_per_visit"] = (x.comps_redeemed + x.carded_cash_tickets) / x.visits
    x["carded_cash_tickets_per_visit"] = x.carded_cash_tickets / x.visits
    x["carded_coin_in_share"] = x.carded_coin_in / (x.carded_coin_in + x.uncarded_coin_in)
    x["carded_coin_in_per_visit"] = x.carded_coin_in / x.visits
    return x
d = derive(d)
d["week_start"] = (d.gaming_date - pd.to_timedelta(d.gaming_date.dt.day_of_week, unit="D")).dt.date.astype(str)
d["post_change"] = d.gaming_date >= CHANGE
save_csv("09_comp_daily.csv", d.assign(gaming_date=d.gaming_date.dt.date.astype(str)).round(6))

# ---------- 2. Monthly (player_period, state period = month) + weekly (ledger gaming dates) ----------
pm = q(c, """select period_month period, sum(comps_redeemed) comps_redeemed, sum(comp_cost_usd)::double comp_econ_cost,
   sum(comp_face_value_usd)::double comp_face_value, sum(points_earned) points_earned, sum(points_redeemed) points_redeemed,
   sum(theo_usd)::double carded_theo, sum(tracked_coin_in_usd)::double carded_coin_in, sum(observed_visits) visits,
   count(*) filter (where comps_redeemed>0) redeeming_players, count(*) active_players
   from pp group by 1 order by 1""")
pm.insert(0, "period_type", "month")
pm["reinvestment"] = pm.comp_econ_cost / pm.carded_theo
pm["points_per_face_usd"] = pm.points_redeemed / pm.comp_face_value
pm["points_per_econ_usd"] = pm.points_redeemed / pm.comp_econ_cost
pm["points_earned_per_coin_in_usd"] = pm.points_earned / pm.carded_coin_in
pm["comps_per_visit"] = pm.comps_redeemed / pm.visits
pm["face_value_per_theo"] = pm.comp_face_value / pm.carded_theo
cols = ["comps_redeemed", "comp_econ_cost", "comp_face_value", "points_earned", "points_redeemed", "carded_theo",
        "carded_coin_in", "visits"]
wk = d.groupby("week_start")[cols + ["carded_cash_tickets"]].sum().reset_index().rename(columns={"week_start": "period"})
wk["days"] = d.groupby("week_start").size().values
wk.insert(0, "period_type", "week")
wk["reinvestment"] = wk.comp_econ_cost / wk.carded_theo
wk["points_per_face_usd"] = wk.points_redeemed / wk.comp_face_value
wk["points_per_econ_usd"] = wk.points_redeemed / wk.comp_econ_cost
wk["points_earned_per_coin_in_usd"] = wk.points_earned / wk.carded_coin_in
wk["comps_per_visit"] = wk.comps_redeemed / wk.visits
wk["face_value_per_theo"] = wk.comp_face_value / wk.carded_theo
save_csv("09_comp_monthly.csv", pd.concat([pm, wk], ignore_index=True).round(6))

# ---------- 3. Change detection, method 1: change-point scan ----------
def changepoint(s, dates, min_seg=14):
    v = s.values.astype(float); best = (np.inf, None)
    for i in range(min_seg, len(v) - min_seg):
        a, b = v[:i], v[i:]
        sse = ((a - a.mean()) ** 2).sum() + ((b - b.mean()) ** 2).sum()
        if sse < best[0]: best = (sse, i)
    i = best[1]; a, b = v[:i], v[i:]
    sse0 = ((v - v.mean()) ** 2).sum()
    return {"date": str(dates.iloc[i].date()), "mean_before": float(a.mean()), "mean_after": float(b.mean()),
            "pct_change": float(b.mean() / a.mean() - 1), "share_variance_explained": float(1 - best[0] / sse0)}
cp_series = ["points_per_face_usd", "points_per_econ_usd", "econ_cost_per_face", "points_earned_per_coin_in_usd",
             "reinvestment", "comps_per_visit", "comp_share_of_carded_fnb_tickets"]
dd = d[d.gaming_date >= "2025-01-06"].reset_index(drop=True)  # skip first days (balances start at 0 on Jan 1)
cps = {s: changepoint(dd[s], dd.gaming_date) for s in cp_series}

# ---------- 4. Method 2: event-level exact rates ----------
ev = q(c, f"""select tier_before tier, gaming_date >= DATE '{CHANGE.date()}' post, count(*) redemptions,
   sum(-points_delta) points, sum(face_value_cents)/100.0 face, sum(economic_cost_cents)/100.0 econ,
   median(-points_delta*100.0/face_value_cents) median_pts_per_face_usd,
   min(-points_delta*100.0/face_value_cents) min_rate, max(-points_delta*100.0/face_value_cents) max_rate,
   min(event_at_utc) first_utc, max(event_at_utc) last_utc
   from l where event_type='redeem' group by all order by 1,2""")
ev["pts_per_face_usd"] = ev.points / ev.face
# implied base rate: smallest r with ceil(r*face$) == points for all redemptions (rates are per-tier constants, rounded up)
rates = {}
for (tier, post), sub in q(c, f"""select tier_before tier, gaming_date >= DATE '{CHANGE.date()}' post, -points_delta p,
        face_value_cents f, count(*) n from l where event_type='redeem' group by all""").groupby(["tier", "post"]):
    lo = (sub.p - 1) / (sub.f / 100); hi = sub.p / (sub.f / 100)
    rates[f"{tier}_{'post' if post else 'pre'}"] = {"rate_lower_excl": float(lo.max()), "rate_upper_incl": float(hi.min()),
                                                    "consistent_single_rate": bool(lo.max() < hi.min())}
# exact switch instant: last old-rate and first new-rate redemption per tier
pre_rate = {t: ev[(ev.tier == t) & (~ev.post)].median_pts_per_face_usd.iloc[0] for t in ev.tier.unique()}
sw = q(c, """select tier_before tier, event_at_utc, timezone('America/Los_Angeles', event_at_utc::timestamptz) local_ts,
             -points_delta*100.0/face_value_cents rate from l where event_type='redeem'
             and event_at_utc between TIMESTAMP '2025-04-30 00:00' and TIMESTAMP '2025-05-02 23:59'""")
sw["old"] = sw.apply(lambda r: r.rate >= pre_rate[r.tier] - 1e-9, axis=1)
switch = {t: {"last_old_rate_utc": str(s[s.old].event_at_utc.max()), "last_old_rate_local": str(s[s.old].local_ts.max()),
              "first_new_rate_utc": str(s[~s.old].event_at_utc.min()), "first_new_rate_local": str(s[~s.old].local_ts.min())}
          for t, s in sw.groupby("tier")}
n_misclassified = int(q(c, f"""select count(*) from l where event_type='redeem' and
   ((gaming_date < DATE '{CHANGE.date()}') <> (-points_delta*100.0/face_value_cents >= case tier_before
     when 'Base' then {pre_rate['Base']} when 'Gold' then {pre_rate['Gold']} else {pre_rate['Platinum']} end - 1e-9))""").iloc[0, 0])
gd_start = str(q(c, f"select gaming_day_start_utc from cal where gaming_date = DATE '{CHANGE.date()}'").iloc[0, 0])
ben = q(c, f"""select benefit_id, gaming_date >= DATE '{CHANGE.date()}' post, count(*) n, avg(face_value_cents)/100 face,
   avg(economic_cost_cents)/100 econ, sum(-points_delta)/(sum(face_value_cents)/100) pts_per_face_usd
   from l where event_type='redeem' group by all order by 1,2""")
benp = ben.pivot(index="benefit_id", columns="post", values=["face", "econ", "pts_per_face_usd", "n"])
benefit_change = {b: {"face_pre": float(r[("face", False)]), "face_post": float(r[("face", True)]),
                      "econ_pre": float(r[("econ", False)]), "econ_post": float(r[("econ", True)]),
                      "pts_per_face_pre": float(r[("pts_per_face_usd", False)]),
                      "pts_per_face_post": float(r[("pts_per_face_usd", True)])} for b, r in benp.iterrows()}
# same-player paired check: players who redeemed the same benefit at the same tier both before and after
paired = q(c, f"""with x as (select player_id, benefit_id, tier_before, gaming_date >= DATE '{CHANGE.date()}' post,
   avg(-points_delta) pts from l where event_type='redeem' group by all)
   select count(*) pairs, avg(b.pts / a.pts) mean_ratio, quantile_cont(b.pts / a.pts, 0.05) p05, quantile_cont(b.pts / a.pts, 0.95) p95
   from x a join x b using(player_id, benefit_id, tier_before) where not a.post and b.post""").iloc[0]
# earn-rate check by tier (points per $ coin-in), pre vs post
earn = q(c, f"""with a as (select source_segment_id, tier_before, points_delta, gaming_date from l where event_type='award'),
   s as (select session_segment_id, coin_in_cents from sessions)
   select tier_before tier, gaming_date >= DATE '{CHANGE.date()}' post, sum(points_delta)/(sum(coin_in_cents)/100.0) pts_per_usd, count(*) n
   from a join s on s.session_segment_id = a.source_segment_id group by all order by 1,2""")
unmatched_awards = int(q(c, "select count(*) from l a left join sessions s on s.session_segment_id=a.source_segment_id where a.event_type='award' and s.session_segment_id is null").iloc[0, 0])

# ---------- 5. Behaviour: interrupted time series with HAC CI ----------
def its(y, start="2025-02-01", log=False, label=None):
    x = d[d.gaming_date >= start].copy()
    yy = np.log(x[y]) if log else x[y]
    X = pd.DataFrame({"const": 1.0, "t": (x.gaming_date - CHANGE).dt.days / 30.0,
                      "post": (x.gaming_date >= CHANGE).astype(float)}, index=x.index)
    X = X.join(pd.get_dummies(x.local_weekday, drop_first=True, dtype=float))
    r = sm.OLS(yy, X).fit(cov_type="HAC", cov_kwds={"maxlags": 7})
    b, se = r.params["post"], r.bse["post"]
    pre_mean = x.loc[x.gaming_date < CHANGE, y].mean()
    res = {"metric": label or y, "pre_window_start": start, "pre_mean": float(pre_mean),
           "pre_trend_per_30d": float(r.params["t"]), "post_level_shift": float(b),
           "ci95": [float(b - 1.96 * se), float(b + 1.96 * se)], "p_value": float(r.pvalues["post"]),
           "scale": "log points (~% change)" if log else "level"}
    if not log:
        res["shift_pct_of_pre_mean"] = float(b / pre_mean)
        res["ci95_pct"] = [float((b - 1.96 * se) / pre_mean), float((b + 1.96 * se) / pre_mean)]
    return res
its_res = {}
for y, lg in [("comps_per_visit", False), ("reinvestment", False), ("comp_econ_cost", True), ("comps_redeemed", True),
              ("comp_share_of_carded_fnb_tickets", False), ("carded_fnb_tickets_per_visit", False),
              ("carded_cash_tickets_per_visit", False), ("visits", True), ("carded_coin_in", True),
              ("carded_coin_in_per_visit", True), ("carded_theo", True)]:
    its_res[y] = its(y, log=lg)
its_robust_jan = {y: its(y, start="2025-01-06", log=lg) for y, lg in [("comps_per_visit", False), ("comp_econ_cost", True)]}
# placebo: fake change on 2025-04-01 using only pre-change data
def placebo(y, fake="2025-04-01", log=False):
    x = d[(d.gaming_date >= "2025-02-01") & (d.gaming_date < CHANGE)].copy(); fk = pd.Timestamp(fake)
    yy = np.log(x[y]) if log else x[y]
    X = pd.DataFrame({"const": 1.0, "t": (x.gaming_date - fk).dt.days / 30.0, "post": (x.gaming_date >= fk).astype(float)}, index=x.index)
    X = X.join(pd.get_dummies(x.local_weekday, drop_first=True, dtype=float))
    r = sm.OLS(yy, X).fit(cov_type="HAC", cov_kwds={"maxlags": 7})
    return {"fake_date": fake, "shift": float(r.params["post"]), "ci95": [float(r.params["post"] - 1.96 * r.bse["post"]),
            float(r.params["post"] + 1.96 * r.bse["post"])], "p_value": float(r.pvalues["post"])}
placebos = {"comps_per_visit": placebo("comps_per_visit"), "comp_econ_cost_log": placebo("comp_econ_cost", log=True)}

# DiD 1: carded vs uncarded (uncarded guests cannot redeem comps) - log ratio, weekday dummies, HAC
def did_ratio(a, b, label):
    x = d[d.gaming_date >= "2025-02-01"].copy()
    yy = np.log(x[a]) - np.log(x[b])
    X = pd.DataFrame({"const": 1.0, "t": (x.gaming_date - CHANGE).dt.days / 30.0,
                      "post": (x.gaming_date >= CHANGE).astype(float)}, index=x.index)
    X = X.join(pd.get_dummies(x.local_weekday, drop_first=True, dtype=float))
    r = sm.OLS(yy, X).fit(cov_type="HAC", cov_kwds={"maxlags": 7})
    X2 = X.drop(columns="t"); r2 = sm.OLS(yy, X2).fit(cov_type="HAC", cov_kwds={"maxlags": 7})
    f = lambda rr: {"did_log": float(rr.params["post"]), "ci95": [float(rr.params["post"] - 1.96 * rr.bse["post"]),
                    float(rr.params["post"] + 1.96 * rr.bse["post"])], "p_value": float(rr.pvalues["post"])}
    return {"comparison": label, "with_trend": f(r), "no_trend": f(r2)}
did_coin = did_ratio("carded_coin_in", "uncarded_coin_in", "carded vs uncarded coin-in (log ratio)")
did_fnb = did_ratio("carded_fnb_cash", "uncarded_fnb_cash", "carded vs uncarded F&B cash receipts (log ratio)")

# DiD 2: per player. Treated = redeemed >=1 comp in Jan-Feb; control = no redemption Jan-Feb; population = played Jan-Feb.
pp2 = q(c, """select player_id,
   sum(comps_redeemed) filter (where period_month in ('2025-01','2025-02')) jf_comps,
   sum(tracked_coin_in_usd) filter (where period_month in ('2025-01','2025-02'))::double ci_jf,
   sum(tracked_coin_in_usd) filter (where period_month in ('2025-03','2025-04'))::double ci_ma,
   sum(tracked_coin_in_usd) filter (where period_month in ('2025-05','2025-06'))::double ci_mj,
   sum(observed_visits) filter (where period_month in ('2025-01','2025-02')) v_jf,
   sum(observed_visits) filter (where period_month in ('2025-03','2025-04')) v_ma,
   sum(observed_visits) filter (where period_month in ('2025-05','2025-06')) v_mj
   from pp group by 1""").fillna(0)
pp2 = pp2[pp2.v_jf > 0]
pp2["treated"] = pp2.jf_comps > 0
def did_pl(pre, post, label):
    dlt = pp2[post] - pp2[pre]
    t, u = dlt[pp2.treated], dlt[~pp2.treated]
    est = t.mean() - u.mean(); se = np.sqrt(t.var() / len(t) + u.var() / len(u))
    base = pp2.loc[pp2.treated, pre].mean()
    return {"metric": label, "treated_n": int(len(t)), "control_n": int(len(u)), "treated_pre_mean": float(base),
            "did": float(est), "ci95": [float(est - 1.96 * se), float(est + 1.96 * se)],
            "did_pct_of_treated_pre": float(est / base)}
did_players = {"visits_main_MarApr_to_MayJun": did_pl("v_ma", "v_mj", "visits per player (2 months)"),
               "visits_placebo_JanFeb_to_MarApr": did_pl("v_jf", "v_ma", "visits per player (2 months)"),
               "coin_in_main_MarApr_to_MayJun": did_pl("ci_ma", "ci_mj", "coin-in per player (2 months)"),
               "coin_in_placebo_JanFeb_to_MarApr": did_pl("ci_jf", "ci_ma", "coin-in per player (2 months)")}
for k in ["visits", "coin_in"]:
    m, p = did_players[f"{k}_main_MarApr_to_MayJun"], did_players[f"{k}_placebo_JanFeb_to_MarApr"]
    did_players[f"{k}_main_minus_placebo"] = float(m["did"] - p["did"])

# ---------- 6. By tier ----------
tier_pp = q(c, f"""select opening_tier tier, case when period_month >= '{CHANGE.strftime('%Y-%m')}' then 'post' else 'pre' end period,
   count(distinct player_id) players, sum(comps_redeemed) comps_redeemed, sum(comp_cost_usd)::double comp_econ_cost,
   sum(comp_face_value_usd)::double comp_face_value, sum(points_earned) points_earned, sum(points_redeemed) points_redeemed,
   sum(theo_usd)::double carded_theo, sum(tracked_coin_in_usd)::double carded_coin_in, sum(observed_visits) visits
   from pp group by all""")
tier_pp["months"] = np.where(tier_pp.period == "pre", 4, 2)
tier_pp["tier_basis"] = "opening_tier (player_period)"
tier_pp["reinvestment"] = tier_pp.comp_econ_cost / tier_pp.carded_theo
tier_pp["points_per_face_usd"] = tier_pp.points_redeemed / tier_pp.comp_face_value
tier_pp["points_earned_per_coin_in_usd"] = tier_pp.points_earned / tier_pp.carded_coin_in
tier_pp["comps_per_visit"] = tier_pp.comps_redeemed / tier_pp.visits
tier_pp["comp_econ_cost_per_month"] = tier_pp.comp_econ_cost / tier_pp.months
ev_t = ev.assign(period=np.where(ev.post, "post", "pre"), tier_basis="tier_before at redemption (ledger)")[
    ["tier", "period", "tier_basis", "redemptions", "points", "face", "econ", "pts_per_face_usd", "median_pts_per_face_usd"]]
ev_t = ev_t.rename(columns={"redemptions": "comps_redeemed", "points": "points_redeemed", "face": "comp_face_value",
                            "econ": "comp_econ_cost", "pts_per_face_usd": "points_per_face_usd"})
save_csv("09_comp_by_tier.csv", pd.concat([tier_pp, ev_t], ignore_index=True).sort_values(["tier_basis", "tier", "period"],
         ascending=[True, True, False]).round(6))

# ---------- 7. Change summary ----------
pre_m = pm[pm.period < "2025-05"]; post_m = pm[pm.period >= "2025-05"]
blend_pre = pre_m.points_redeemed.sum() / pre_m.comp_face_value.sum()
blend_post = post_m.points_redeemed.sum() / post_m.comp_face_value.sum()
change = {
    "what_changed": "Points needed per comp dollar (redemption price) was cut ~53% for every tier; earn rate and benefit "
                    "menu/cost did not change.",
    "effective": {"gaming_date": str(CHANGE.date()), "gaming_day_start_utc": gd_start,
                  "note": "Switch falls exactly on the 06:00 PDT gaming-day boundary (no redemption between last old-rate and first new-rate).",
                  "switch_instants_by_tier": switch,
                  "redemptions_classified_by_rate_that_disagree_with_date_split": n_misclassified},
    "magnitude": {"blended_points_per_face_usd_pre": float(blend_pre), "blended_points_per_face_usd_post": float(blend_post),
                  "blended_pct_change": float(blend_post / blend_pre - 1),
                  "per_tier_event_level": ev[["tier", "post", "redemptions", "pts_per_face_usd", "median_pts_per_face_usd",
                                              "min_rate", "max_rate"]].to_dict(orient="records"),
                  "implied_exact_rates_points_per_face_usd_rounded_up": rates,
                  "value_per_point_multiplier": float(blend_pre / blend_post),
                  "note": "Blended rate drifts Jan-Apr only because tier mix shifts (Base->Gold upgrades); within-tier rates are constant."},
    "method1_changepoint_scan": cps,
    "method2_event_level": {"per_benefit_before_after": benefit_change,
                            "same_player_same_benefit_same_tier_points_ratio_post_over_pre": paired.to_dict(),
                            "earn_rate_points_per_usd_coin_in_by_tier": earn.to_dict(orient="records"),
                            "awards_without_matching_session": unmatched_awards},
    "not_changed": {"benefit_face_and_economic_cost": "identical before/after for all 10 benefits",
                    "earn_rate": "~1 point per $1 carded coin-in (floored per segment), same in all tiers, before and after",
                    "available_at_vs_event_at_lag": lag.to_dict(orient="records"),
                    "lag_conclusion": "available_at_utc == event_at_utc for every ledger row: no reporting-lag pattern."},
    "effect_on_redemption_and_play_ITS": its_res,
    "ITS_robustness_pre_window_from_Jan6": its_robust_jan,
    "ITS_placebo_fake_change_2025-04-01": placebos,
    "DiD_carded_vs_uncarded": {"coin_in": did_coin, "fnb_cash": did_fnb},
    "DiD_per_player_JanFeb_redeemers_vs_nonredeemers": did_players,
    "no_tier_control": "All three tiers received the same ~53% cut on the same instant, so no unaffected tier exists; "
                       "uncarded play/F&B (cannot redeem) and non-redeemers are used as controls instead.",
}
save_json("09_comp_change.json", change)

# ---------- 8. July 30-day action estimates ----------
post = d[d.post_change]
daily_cost = post.comp_econ_cost.mean()            # $ / day after the change
daily_theo = post.carded_theo.mean()
avg_face = post.comp_face_value.sum() / post.comps_redeemed.sum()
avg_cost = post.comp_econ_cost.sum() / post.comps_redeemed.sum()
lnp = np.log(blend_post / blend_pre)                # log price change of May 1 (negative)
eff = its_res["comp_econ_cost"]                     # log effect of May 1 cut on daily comp cost
elas = {"central": eff["post_level_shift"] / lnp, "low": max(eff["ci95"][0], 0) / lnp, "high": eff["ci95"][1] / lnp}
# substitution: share of extra comps that replaced a cash F&B purchase (central from ITS on cash tickets per visit)
sub_eff = its_res["carded_cash_tickets_per_visit"]["post_level_shift"]
comp_eff = its_res["comps_per_visit"]["post_level_shift"]
subst_share = float(np.clip(-sub_eff / comp_eff, 0, 1)) if comp_eff > 0 else 0.0
avg_cash_ticket = (post.carded_fnb_cash.sum() / post.carded_cash_tickets.sum())
avg_cash_cogs_ratio = float(q(c, "select sum(cogs_cents)/sum(cash_receipt_cents) from fnb where comp_id is null").iloc[0, 0])
# play response: CI of carded/uncarded DiD (no-trend spec) on carded theo per day
pr = did_coin["no_trend"]
def comp_change_scenarios(P, days, share_of_players=1.0):
    dlnp = np.log(P / blend_post)
    out = {}
    for sc in ["low", "central", "high"]:
        pct = elas[sc] * dlnp    # constant elasticity applied to the log price change
        cost_saving = -(np.exp(pct) - 1) * daily_cost * days * share_of_players
        # avoided comps that would have replaced cash purchases -> recovered cash margin
        n_avoided = cost_saving / avg_cost
        sub = {"low": 0.0, "central": subst_share, "high": min(1.0, 2 * subst_share)}[sc]
        cash_margin = n_avoided * sub * avg_cash_ticket * (1 - avg_cash_cogs_ratio)
        out[sc] = {"comp_cost_change_pct": float(np.exp(pct) - 1), "comp_cost_saving_usd": float(cost_saving),
                   "recovered_cash_margin_usd": float(cash_margin), "gross_benefit_usd": float(cost_saving + cash_margin)}
    return out
days_change, days_pilot = 30 - 3, 30 - 5
scen = {}
for P in [10, 13, 20]:
    s = comp_change_scenarios(P, days_change)
    for k in s: s[k]["net_after_action_cost_usd"] = s[k]["gross_benefit_usd"] - 1500
    scen[f"A-COMP-CHANGE_P{P}"] = s
pil = comp_change_scenarios(13, days_pilot, share_of_players=0.5)
for k in pil: pil[k]["net_after_action_cost_usd"] = pil[k]["gross_benefit_usd"] - 4000
# play-risk band: DiD 95% CI on carded share of coin-in, applied to carded theo for the active days
play_risk = {"did_log_ci95": pr["ci95"], "theo_per_day": float(daily_theo),
             "A-COMP-CHANGE_P13_theo_change_band_usd_if_reversal_mirrors_did": [
                 float(-pr["ci95"][1] * daily_theo * days_change), float(-pr["ci95"][0] * daily_theo * days_change)],
             "note": "The May 1 generosity increase had no detectable effect on carded play; we assume restoring the old price "
                     "also has none (central = 0) but show the house theo change (negative = house loses theo) implied by "
                     "mirroring the DiD 95% CI. This band dwarfs the comp savings, which is why a controlled pilot is the "
                     "only way to learn the play response."}
# pilot precision: per-player 25-day coin-in variance from June player_period (scaled), 50/50 split
jun = q(c, "select tracked_coin_in_usd::double ci, observed_visits v from pp where period_month='2025-06' and observed_visits>0")
n_arm = len(jun) / 2
se_ci = np.sqrt(2 * jun.ci.var() * (25 / 30) / n_arm)   # crude: scale variance of monthly totals
se_v = np.sqrt(2 * jun.v.var() * (25 / 30) / n_arm)
pilot_power = {"active_players_per_month": int(len(jun)), "players_per_arm": int(n_arm),
               "mde_80pct_power_coin_in_per_player_usd": float(2.8 * se_ci),
               "mde_as_pct_of_mean_coin_in": float(2.8 * se_ci / (jun.ci.mean() * 25 / 30)),
               "mde_visits_pct": float(2.8 * se_v / (jun.v.mean() * 25 / 30)),
               "comp_cost_per_player_effect_detectable": "comps per visit / cost per player are far less noisy; the pilot can measure redemption response precisely",
               "note": "Approximation: variance of 30-day totals scaled to 25 days; heavy-tailed coin-in means a trimmed or "
                       "log metric would be more powerful."}
actions = q(c, "select * from actions where action_type_id like 'A-COMP%'").to_dict(orient="records")
ab = {
    "baseline_july": {"comp_econ_cost_per_day_post_change": float(daily_cost), "comp_econ_cost_30d": float(daily_cost * 30),
                      "carded_theo_per_day": float(daily_theo), "current_blended_points_per_face_usd": float(blend_post),
                      "pre_change_blended_points_per_face_usd": float(blend_pre),
                      "avg_comp_face_usd": float(avg_face), "avg_comp_econ_cost_usd": float(avg_cost)},
    "assumptions": [
        "Comp cost responds to the point price with a constant elasticity estimated from the May 1 cut (ITS on log daily comp economic cost, Feb-Apr pre-trend, HAC CI): elasticity = effect / ln(price_post/price_pre).",
        "low uses the CI bound closest to zero (no response), high the far CI bound; extrapolating to P=20 goes beyond the observed range.",
        "A-COMP-CHANGE sets ONE floor-wide point cost per comp face-dollar (P) for all tiers, effective after 3 lead days (27 of 30 days).",
        "A-COMP-PILOT randomizes 50% of carded players to P=13 (pre-change level) after 5 lead days (25 days); the other 50% are the control. Its main output is a causal estimate, not savings.",
        "Avoided comps that would have replaced a cash F&B purchase recover cash margin (cash ticket x (1-cogs ratio)); share from ITS on cash tickets per visit (low 0, high 2x central).",
        "Carded play is assumed unchanged (central); DiD carded vs uncarded CI shown as a risk band in theo dollars.",
        "Comp economic cost already sits in POS cogs: savings are counted once; face value is never counted as revenue.",
        "Nothing here targets heavy losers to play more; a tier-neutral price restore reduces giveaway cost only. Any pilot should exclude self-excluded players and monitor loss-chasing signals."],
    "elasticity_of_comp_cost_wrt_point_price": elas,
    "substitution_share_of_extra_comps_replacing_cash": subst_share,
    "avg_cash_ticket_usd": float(avg_cash_ticket), "cash_cogs_ratio": avg_cash_cogs_ratio,
    "A-COMP-CHANGE": {"cost_usd": 1500, "lead_days": 3, "active_days": days_change, "scenarios_by_P": scen},
    "A-COMP-PILOT": {"cost_usd": 4000, "lead_days": 5, "active_days": days_pilot, "design": "50/50 random split, treated arm P=13",
                     "scenarios": pil, "power": pilot_power},
    "play_response_risk": play_risk,
    "catalog_rows": actions,
}
save_json("09_action_benefits.json", ab)

# ---------- 9. Summary ----------
tot = pm[cols].sum()
outstanding = float(tot.points_earned - tot.points_redeemed)
summ = {
    "scope": "Loyalty program, tracked (carded) players, gaming dates 2025-01-01..2025-06-30",
    "totals": {"comps_redeemed": int(tot.comps_redeemed), "comp_economic_cost_usd": float(tot.comp_econ_cost),
               "comp_face_value_usd_not_revenue": float(tot.comp_face_value), "points_earned": int(tot.points_earned),
               "points_redeemed": int(tot.points_redeemed), "share_of_earned_points_redeemed": float(tot.points_redeemed / tot.points_earned),
               "carded_theo_usd": float(tot.carded_theo), "reinvestment_6m": float(tot.comp_econ_cost / tot.carded_theo),
               "reinvestment_pre_JanApr": float(pre_m.comp_econ_cost.sum() / pre_m.carded_theo.sum()),
               "reinvestment_post_MayJun": float(post_m.comp_econ_cost.sum() / post_m.carded_theo.sum()),
               "econ_cost_per_face_dollar": float(tot.comp_econ_cost / tot.comp_face_value),
               "points_earned_per_usd_coin_in": float(tot.points_earned / tot.carded_coin_in)},
    "reconciliation": {**{k: (float(v) if not isinstance(v, str) else v) for k, v in recon.items()},
                       "ledger_events_outside_window": int(out_of_window.n),
                       "note": "ledger economic cost == comp-ticket POS cogs == player_period comp_cost == property_day comp_cost; counted once."},
    "change": {"date": str(CHANGE.date()), "what": change["what_changed"],
               "points_per_face_usd": [float(blend_pre), float(blend_post)],
               "comp_cost_effect_log_ITS": its_res["comp_econ_cost"], "comps_per_visit_ITS": its_res["comps_per_visit"],
               "carded_vs_uncarded_coin_in_DiD": did_coin["no_trend"]},
    "outstanding_points_end_June": outstanding,
    "outstanding_points_cost_if_redeemed_usd": {"at_post_change_price": float(outstanding / blend_post * tot.comp_econ_cost / tot.comp_face_value),
                                                "at_pre_change_price": float(outstanding / blend_pre * tot.comp_econ_cost / tot.comp_face_value),
                                                "note": "Upper bound, not a booked liability: only ~4% of earned points were redeemed and balances reset each year."},
}
save_json("09_summary.json", summ)

pd.set_option("display.width", 200)
print(summ["totals"]); print(summ["reconciliation"])
print(pm[["period", "comps_redeemed", "comp_econ_cost", "reinvestment", "points_per_face_usd", "points_earned_per_coin_in_usd", "comps_per_visit"]].round(4).to_string())
print(ev[["tier", "post", "redemptions", "pts_per_face_usd", "min_rate", "max_rate"]].round(3).to_string())
print(rates); print(switch); print("misclassified", n_misclassified, "gd start", gd_start)
print({k: (v["date"], round(v["pct_change"], 4), round(v["share_variance_explained"], 3)) for k, v in cps.items()})
print(paired.to_dict()); print(earn.round(4).to_string()); print("unmatched awards", unmatched_awards); print(lag.to_string())
for k, v in its_res.items(): print(k, round(v["post_level_shift"], 5), [round(x, 5) for x in v["ci95"]], round(v["p_value"], 3), round(v["pre_mean"], 4))
print(its_robust_jan); print(placebos); print(did_coin); print(did_fnb)
for k, v in did_players.items(): print(k, v)
print(ab["baseline_july"], elas, subst_share)
for k, v in scen.items(): print(k, {s: round(x["net_after_action_cost_usd"]) for s, x in v.items()}, {s: round(x["comp_cost_change_pct"], 3) for s, x in v.items()})
print("pilot", {s: round(x["net_after_action_cost_usd"]) for s, x in pil.items()}, pilot_power)
print(play_risk); print(summ["outstanding_points_cost_if_redeemed_usd"])

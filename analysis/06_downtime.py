"""06 Downtime (Q3): how much play was lost to downtime, and when? Does availability explain weak machines?

Rules: on-floor rows only (location_id not null). Gaming day 06:00-06:00 America/Los_Angeles; ops timestamps are
mapped to gaming dates with calendar_day.gaming_day_start_utc/end_utc (never calendar dates). Scheduled hours come
from scheduled_seconds (Mar 8 = 23 h). All rates are ratios of sums. Cents -> dollars at the edge.

Lost-play valuation (two methods, both assume the play would not have moved to another machine = gross/upper bound):
  A "flat":  downtime hours x the machine's own theo (coin-in) per AVAILABLE hour over the whole period (machine_day).
  B "hourly": each down second is valued at the machine's own theo (coin-in) per available second in that LOCAL HOUR
             OF DAY (machine_hour; theo = coin-in x (1 - configured_rtp), one config per machine). Faults happen
             mostly in busy hours, so B > A.  B is the headline.
  A displacement test (do neighbours in the same bank earn more while a machine is down?) checks the gross assumption.

Technician queue: response delay (opened -> work_started) is a multi-server queue. A replay of all 1,207 faults with
the roster's actual slot_tech headcount (FIFO; techs only count against the shift they started in; a waiting job
starts when a same-shift job ends or at next shift start) reproduces the recorded start times, which lets us
simulate counterfactual staffing exactly instead of extrapolating averages.

Writes outputs/06_downtime_daily.csv, 06_downtime_by_manufacturer.csv, 06_staffing_effect.csv, 06_tech_by_day.csv,
06_repeat_faults.csv, 06_action_benefits.json, 06_summary.json.
"""
from common import con, q, save_csv, save_json, OUT
import numpy as np
import pandas as pd
from scipy import stats

rng = np.random.default_rng(6)
c = con()
HORIZON_DAYS = 23  # 30-day horizon minus 7 lead days

# ---------------------------------------------------------------- base tables
c.execute("""
create temp table mday as
select m.machine_id, m.gaming_date, m.location_id, l.bank_id, l.zone_id, mc.manufacturer, m.config_id,
       g.configured_rtp::double rtp, m.scheduled_seconds, m.available_seconds,
       m.scheduled_seconds - m.available_seconds down_s, m.occupied_seconds, m.coin_in_cents, m.theo_cents::double theo_cents
from md m join machines mc using(machine_id) join gm g using(config_id) join locs l on l.location_id = m.location_id
where m.location_id is not null""")
# machine rates (method A)
c.execute("""create temp table rate_a as
select machine_id, any_value(bank_id) bank_id, sum(theo_cents)/nullif(sum(available_seconds),0) theo_ps,
       sum(coin_in_cents)/nullif(sum(available_seconds),0) coin_ps from mday group by 1""")
# machine x local hour rates (method B); fall back to the machine's flat rate if a cell has no available time
c.execute("""create temp table rate_b as
with h as (select h.machine_id, h.local_hour, sum(h.available_seconds) av, sum(h.coin_in_cents) ci,
                  sum(h.coin_in_cents * (1 - g.configured_rtp::double)) th
           from mh h join md d using(machine_id, gaming_date) join gm g on g.config_id = d.config_id
           where h.location_id is not null and h.active group by 1, 2)
select h.machine_id, h.local_hour, coalesce(th/nullif(av,0), a.theo_ps) theo_ps, coalesce(ci/nullif(av,0), a.coin_ps) coin_ps
from h join rate_a a using(machine_id)""")

# operations split into gaming-day segments (ops -> gaming dates via calendar_day UTC bounds)
c.execute("""create temp table seg as
select o.operation_id, o.machine_id, o.event_type, k.gaming_date,
       epoch(least(o.available_at_utc, k.gaming_day_end_utc) - greatest(o.opened_at_utc, k.gaming_day_start_utc)) secs
from ops o join cal k on o.opened_at_utc < k.gaming_day_end_utc and o.available_at_utc > k.gaming_day_start_utc
where o.location_id is not null""")
recon = q(c, """with a as (select machine_id, gaming_date, sum(secs) s from seg group by all)
select sum(abs(m.down_s - coalesce(a.s,0)))/3600 abs_diff_h, sum(m.down_s)/3600 md_h, sum(coalesce(a.s,0))/3600 ops_h
from mday m left join a using(machine_id, gaming_date)""").iloc[0]
assert recon.abs_diff_h < 1e-6, recon  # machine_day downtime == sum of operations intervals, machine-day by machine-day

# operations split into machine-hour segments, valued at the hourly (B) and flat (A) rates
c.execute("""create temp table oph as
select o.operation_id, o.machine_id, o.event_type, h.gaming_date, h.local_hour,
       epoch(least(o.available_at_utc, h.hour_start_utc + interval 1 hour) - greatest(o.opened_at_utc, h.hour_start_utc)) secs
from ops o join mh h on h.machine_id = o.machine_id and o.opened_at_utc < h.hour_start_utc + interval 1 hour
     and o.available_at_utc > h.hour_start_utc
where o.location_id is not null and h.location_id is not null""")
c.execute("""create temp table oph_v as
select s.*, s.secs * b.theo_ps/100 theo_b, s.secs * b.coin_ps/100 coin_b, s.secs * a.theo_ps/100 theo_a, s.secs * a.coin_ps/100 coin_a
from oph s join rate_b b using(machine_id, local_hour) join rate_a a using(machine_id)""")
assert abs(q(c, "select sum(secs)/3600 h from oph").h[0] - recon.md_h) < 1e-6

# faults: one row each, with gaming date of opening, shift in effect, delays, cost, valuation
faults = q(c, """
select o.operation_id, o.machine_id, mc.manufacturer, l.bank_id, l.zone_id, k.gaming_date, k.month,
       r.shift, r.planned_headcount planned, r.actual_headcount actual, r.start_utc shift_start,
       hour(o.opened_at_local) local_hour, o.opened_at_utc,
       epoch(o.work_started_at_utc - o.opened_at_utc)/3600 wait_h,
       epoch(o.available_at_utc - o.work_started_at_utc)/3600 work_h,
       epoch(o.available_at_utc - o.opened_at_utc)/3600 ttr_h,
       o.direct_cost_cents/100.0 repair_usd, v.theo_b, v.theo_a, v.coin_b,
       b.theo_ps/100*3600 theo_ph_open_hour
from ops o join machines mc using(machine_id) join locs l on l.location_id = o.location_id
join cal k on o.opened_at_utc >= k.gaming_day_start_utc and o.opened_at_utc < k.gaming_day_end_utc
join roster r on r.role = 'slot_tech' and o.opened_at_utc >= r.start_utc and o.opened_at_utc < r.end_utc
join (select operation_id, sum(theo_b) theo_b, sum(theo_a) theo_a, sum(coin_b) coin_b from oph_v group by 1) v using(operation_id)
join rate_b b on b.machine_id = o.machine_id and b.local_hour = hour(o.opened_at_local)
where o.event_type = 'fault_repair' and o.location_id is not null order by o.opened_at_utc""")
assert len(faults) == q(c, "select count(*) n from ops where event_type='fault_repair'").n[0]
faults["short"] = faults.actual < faults.planned

# ---------------------------------------------------------------- 1. daily
daily = q(c, """
with m as (select gaming_date, sum(scheduled_seconds)/3600 sched_h, sum(available_seconds)/3600 avail_h,
                  sum(down_s)/3600 downtime_h, sum(theo_cents)/100 theo_usd, sum(coin_in_cents)/100 coin_in_usd,
                  count(*) machines_on_floor
           from mday group by 1),
c as (select gaming_date,
             sum(secs) filter (where event_type='fault_repair')/3600 down_fault_h,
             sum(secs) filter (where event_type='scheduled_maintenance')/3600 down_maint_h,
             sum(secs) filter (where event_type in ('move','install','retire'))/3600 down_move_install_h
      from seg group by 1),
v as (select gaming_date, sum(theo_a) lost_theo_flat_usd, sum(coin_a) lost_coin_in_flat_usd,
             sum(theo_b) lost_theo_hourly_usd, sum(coin_b) lost_coin_in_hourly_usd from oph_v group by 1),
f as (select k.gaming_date, count(*) faults_opened from ops o join cal k
      on o.opened_at_utc >= k.gaming_day_start_utc and o.opened_at_utc < k.gaming_day_end_utc
      where o.event_type='fault_repair' group by 1)
select m.gaming_date, k.local_weekday, k.month, m.machines_on_floor, m.sched_h, m.avail_h, m.downtime_h,
       m.downtime_h / m.sched_h downtime_share,
       coalesce(c.down_fault_h,0) down_fault_h, coalesce(c.down_maint_h,0) down_maint_h,
       coalesce(c.down_move_install_h,0) down_move_install_h, coalesce(f.faults_opened,0) faults_opened,
       coalesce(v.lost_theo_flat_usd,0) lost_theo_flat_usd, coalesce(v.lost_theo_hourly_usd,0) lost_theo_hourly_usd,
       coalesce(v.lost_coin_in_flat_usd,0) lost_coin_in_flat_usd, coalesce(v.lost_coin_in_hourly_usd,0) lost_coin_in_hourly_usd,
       m.theo_usd floor_theo_usd, m.coin_in_usd floor_coin_in_usd
from m join cal k using(gaming_date) left join c using(gaming_date) left join v using(gaming_date) left join f using(gaming_date)
order by 1""")
daily["gaming_date"] = pd.to_datetime(daily.gaming_date).dt.date
for col in ["down_fault_h", "down_maint_h", "down_move_install_h"]:
    assert (daily[col] >= 0).all()
assert np.allclose(daily.downtime_h, daily.down_fault_h + daily.down_maint_h + daily.down_move_install_h)
save_csv("06_downtime_daily.csv", daily.round(4))

T = daily[["sched_h", "avail_h", "downtime_h", "down_fault_h", "down_maint_h", "down_move_install_h",
           "lost_theo_flat_usd", "lost_theo_hourly_usd", "lost_coin_in_flat_usd", "lost_coin_in_hourly_usd",
           "floor_theo_usd", "floor_coin_in_usd", "faults_opened"]].sum()

d2 = daily.assign(gd=pd.to_datetime(daily.gaming_date))
monthly = d2.groupby("month").agg(downtime_h=("downtime_h", "sum"), sched_h=("sched_h", "sum"),
                                  faults=("faults_opened", "sum"), lost_theo_hourly_usd=("lost_theo_hourly_usd", "sum"),
                                  days=("downtime_h", "size"))
monthly["downtime_share"] = monthly.downtime_h / monthly.sched_h
monthly["downtime_h_per_day"] = monthly.downtime_h / monthly.days
d2["week"] = d2.gd - pd.to_timedelta(d2.gd.dt.day_of_week, unit="D")
weekly = d2.groupby("week").agg(downtime_h=("downtime_h", "sum"), sched_h=("sched_h", "sum"), days=("gd", "size"),
                                lost_theo_hourly_usd=("lost_theo_hourly_usd", "sum"))
weekly["share"] = weekly.downtime_h / weekly.sched_h
wk_full = weekly[weekly.days == 7]
by_wd = d2.groupby("local_weekday").downtime_h.mean().round(1).to_dict()
hour_prof = q(c, "select local_hour, sum(secs)/3600 h, sum(theo_b) theo from oph_v group by 1 order by 1")

# ---------------------------------------------------------------- displacement test (gross vs net lost play)
# For each bank-hour: coin-in of the OTHER machines vs their expected coin-in (their available seconds x their own
# machine x local_hour coin-in rate). If players move when a machine is down, others beat expectation in down-hours.
disp = q(c, """
with h as (select h.machine_id, h.gaming_date, h.hour_start_utc, h.local_hour, l.bank_id, h.available_seconds av,
                  h.coin_in_cents/100.0 ci, h.available_seconds * b.coin_ps/100 exp_ci, (3600-h.available_seconds) * b.coin_ps/100 lost_ci
           from mh h join locs l on l.location_id = h.location_id join rate_b b using(machine_id, local_hour)
           where h.location_id is not null),
bh as (select bank_id, gaming_date, hour_start_utc, sum(ci) ci, sum(exp_ci) exp_ci, sum(lost_ci) lost_ci,
              max((av = 0)::int) any_full_down, count(*) n from h group by all)
select gaming_date, any_full_down, sum(ci - exp_ci) excess, sum(lost_ci) lost, sum(exp_ci) expct, count(*) n
from (select h.bank_id, h.gaming_date, h.hour_start_utc, bh.any_full_down,
             sum(h.ci) ci, sum(h.exp_ci) exp_ci, max(bh.lost_ci) lost_ci
      from h join bh using(bank_id, gaming_date, hour_start_utc)
      where h.av = 3600 group by all) x
group by all""")
# excess coin-in of fully available neighbours in hours with a fully-down machine, relative to non-down hours
dd = disp.pivot_table(index="gaming_date", columns="any_full_down", values=["excess", "lost", "expct"], aggfunc="sum").fillna(0)
def disp_share(ix):
    s = dd.loc[ix]
    base_rel = s[("excess", 0)].sum() / s[("expct", 0)].sum()          # general calibration drift of the baseline
    adj_excess = s[("excess", 1)].sum() - base_rel * s[("expct", 1)].sum()
    lost_down = s[("lost", 1)].sum()                                    # includes the down machines' lost coin-in
    return adj_excess / lost_down
days_ix = dd.index.values
disp_point = disp_share(days_ix)
disp_boot = [disp_share(rng.choice(days_ix, len(days_ix))) for _ in range(500)]
disp_ci = np.percentile(disp_boot, [2.5, 97.5])

# ---------------------------------------------------------------- 2. manufacturer
def poisson_ci(k, expo, alpha=0.05):
    lo = stats.chi2.ppf(alpha / 2, 2 * k) / 2 if k > 0 else 0.0
    hi = stats.chi2.ppf(1 - alpha / 2, 2 * k + 2) / 2
    return lo / expo, hi / expo

mach = q(c, """
with f as (select machine_id, count(*) faults, sum(direct_cost_cents)/100.0 repair_usd from ops
           where event_type='fault_repair' and location_id is not null group by 1),
v as (select machine_id, sum(theo_b) lost_theo_hourly_usd, sum(theo_a) lost_theo_flat_usd from oph_v group by 1)
select m.machine_id, any_value(m.manufacturer) manufacturer, arg_max(m.bank_id, m.gaming_date) bank_id,
       count(*) days_on_floor, sum(scheduled_seconds)/86400 sched_days, sum(scheduled_seconds)/3600 sched_h,
       sum(available_seconds)/3600 avail_h, sum(down_s)/3600 downtime_h, sum(occupied_seconds)/3600 occ_h,
       sum(theo_cents)/100 theo_usd, coalesce(any_value(f.faults),0) faults, coalesce(any_value(f.repair_usd),0) repair_usd,
       coalesce(any_value(v.lost_theo_hourly_usd),0) lost_theo_hourly_usd, coalesce(any_value(v.lost_theo_flat_usd),0) lost_theo_flat_usd
from mday m left join f using(machine_id) left join v using(machine_id) group by m.machine_id""")

def boot_ratio(df, num, den, B=2000):
    """Cluster (machine) bootstrap CI for sum(num)/sum(den)."""
    n = len(df); a = df[num].values; b = df[den].values
    idx = rng.integers(0, n, (B, n))
    r = a[idx].sum(1) / b[idx].sum(1)
    return np.percentile(r, [2.5, 97.5])

rows = []
fleet_rate_occ = mach.faults.sum() / mach.occ_h.sum()
for mf, g in mach.groupby("manufacturer"):
    fg = faults[faults.manufacturer == mf]
    k, expo = g.faults.sum(), g.sched_days.sum()
    lo, hi = poisson_ci(k, expo / 30)
    blo, bhi = boot_ratio(g.assign(sd30=g.sched_days / 30), "faults", "sd30")
    other = mach[mach.manufacturer != mf]
    # exact conditional test of rate ratio vs the other manufacturers (per machine-day of exposure)
    k2, e2 = other.faults.sum(), other.sched_days.sum()
    p_rr = stats.binomtest(int(k), int(k + k2), expo / (expo + e2)).pvalue
    # same, but exposure = occupied hours (tests "they just get played more")
    ko, eo = g.occ_h.sum(), other.occ_h.sum()
    p_rr_occ = stats.binomtest(int(k), int(k + k2), ko / (ko + eo)).pvalue
    ttr = fg.ttr_h.values
    ttr_b = np.percentile([rng.choice(ttr, len(ttr)).mean() for _ in range(2000)], [2.5, 97.5])
    wait_b = np.percentile([rng.choice(fg.wait_h.values, len(fg)).mean() for _ in range(2000)], [2.5, 97.5])
    disp_ratio = (g.faults.var() / g.faults.mean())  # dispersion index across machines (≈1 if Poisson, equal exposure)
    rows.append(dict(
        manufacturer=mf, machines=len(g), machine_days=round(expo, 1), faults=int(k),
        faults_per_machine_30d=k / expo * 30, faults_per_machine_30d_lo95_poisson=lo, faults_per_machine_30d_hi95_poisson=hi,
        faults_per_machine_30d_lo95_boot=blo, faults_per_machine_30d_hi95_boot=bhi,
        rate_ratio_vs_others=(k / expo) / (k2 / e2), p_rate_ratio_exact=p_rr,
        faults_per_1k_occupied_h=k / ko * 1000, rate_ratio_vs_others_per_occ_h=(k / ko) / (k2 / eo), p_rate_ratio_occ_exact=p_rr_occ,
        dispersion_index=disp_ratio,
        mean_wait_h=fg.wait_h.mean(), mean_wait_h_lo95=wait_b[0], mean_wait_h_hi95=wait_b[1],
        mean_work_h=fg.work_h.mean(), mttr_h=ttr.mean(), mttr_h_lo95=ttr_b[0], mttr_h_hi95=ttr_b[1], median_ttr_h=np.median(ttr),
        downtime_h=g.downtime_h.sum(), downtime_h_per_machine=g.downtime_h.sum() / len(g),
        downtime_h_per_machine_30d=g.downtime_h.sum() / expo * 30, availability=g.avail_h.sum() / g.sched_h.sum(),
        lost_theo_hourly_usd=g.lost_theo_hourly_usd.sum(), lost_theo_flat_usd=g.lost_theo_flat_usd.sum(),
        lost_theo_share_of_theo=g.lost_theo_hourly_usd.sum() / g.theo_usd.sum(),
        repair_cost_usd=g.repair_usd.sum(), repair_cost_per_fault_usd=g.repair_usd.sum() / k,
        theo_per_avail_h=g.theo_usd.sum() / g.avail_h.sum()))
mfr = pd.DataFrame(rows)
save_csv("06_downtime_by_manufacturer.csv", mfr.round(5))
worst_mfr = mfr.sort_values("faults_per_machine_30d").iloc[-1].manufacturer

# ---------------------------------------------------------------- 4. time pattern: change points, banks, repeat faults
fd = q(c, """with f as (select machine_id, k.gaming_date, count(*) f from ops o join cal k
                 on o.opened_at_utc >= k.gaming_day_start_utc and o.opened_at_utc < k.gaming_day_end_utc
                 where event_type='fault_repair' group by all)
select m.gaming_date, m.manufacturer, sum(coalesce(f.f,0)) f, sum(occupied_seconds)/3600 occ, sum(scheduled_seconds)/86400 mdays
from mday m left join f using(machine_id, gaming_date) group by all order by 1""")
fd["gaming_date"] = pd.to_datetime(fd.gaming_date)

def changepoint(x, min_seg=14):
    F, X = x.f.values, x.occ.values
    ll_null = F.sum() * np.log(F.sum() / X.sum()) - F.sum()
    best = None
    for k in range(min_seg, len(x) - min_seg):
        a, b = F[:k].sum() / X[:k].sum(), F[k:].sum() / X[k:].sum()
        ll = F[:k].sum() * np.log(a) + F[k:].sum() * np.log(b) - F.sum()
        if best is None or ll - ll_null > best[0]:
            best = (ll - ll_null, k)
    return best

def perm_pvalue(x, obs_llr, B=300):
    """Permutation null for the max-LLR scan: shuffle daily (f, occ) pairs."""
    cnt = 0
    for _ in range(B):
        if changepoint(x.sample(frac=1, random_state=int(rng.integers(1e9))).reset_index(drop=True))[0] >= obs_llr:
            cnt += 1
    return (cnt + 1) / (B + 1)

cp = {}
for mf in sorted(fd.manufacturer.unique()):
    x = fd[fd.manufacturer == mf].reset_index(drop=True)
    llr, k = changepoint(x)
    d0 = x.gaming_date[k]
    pre, post = x[x.gaming_date < d0], x[x.gaming_date >= d0]
    cp[mf] = dict(best_split_date=str(d0.date()), llr=round(llr, 2), p_scan_permutation=perm_pvalue(x, llr, B=200),
                  rate_per_1k_occ_h_before=pre.f.sum() / pre.occ.sum() * 1000, rate_per_1k_occ_h_after=post.f.sum() / post.occ.sum() * 1000,
                  faults_per_machine_30d_before=pre.f.sum() / pre.mdays.sum() * 30, faults_per_machine_30d_after=post.f.sum() / post.mdays.sum() * 30)
# second method for the worst manufacturer: fixed split (Q1 vs Q2) with the other manufacturers as a control
q2 = pd.Timestamp("2025-04-01")
def rr_q(mf_mask):
    x = fd[mf_mask]; a, b = x[x.gaming_date < q2], x[x.gaming_date >= q2]
    return a.f.sum(), a.occ.sum(), b.f.sum(), b.occ.sum()
fa, oa, fb, ob = rr_q(fd.manufacturer == worst_mfr)
ca, coa, cb, cob = rr_q(fd.manufacturer != worst_mfr)
rr_w, rr_c = (fb / ob) / (fa / oa), (cb / cob) / (ca / coa)
# ratio of rate ratios, log-scale SE from Poisson counts
se = np.sqrt(1 / fa + 1 / fb + 1 / ca + 1 / cb)
did = rr_w / rr_c
did_ci = [did * np.exp(-1.96 * se), did * np.exp(1.96 * se)]
p_w = stats.binomtest(int(fb), int(fa + fb), ob / (oa + ob)).pvalue

# banks: observed vs expected faults given each machine's manufacturer rate per occupied hour
mrate = mach.groupby("manufacturer").apply(lambda g: g.faults.sum() / g.occ_h.sum()).to_dict()
bank = q(c, """with f as (select machine_id, k.gaming_date, count(*) f from ops o join cal k
                 on o.opened_at_utc >= k.gaming_day_start_utc and o.opened_at_utc < k.gaming_day_end_utc
                 where event_type='fault_repair' group by all)
select m.bank_id, m.manufacturer, sum(coalesce(f.f,0)) f, sum(occupied_seconds)/3600 occ, sum(down_s)/3600 downtime_h
from mday m left join f using(machine_id, gaming_date) group by all""")
bank["exp"] = bank.occ * bank.manufacturer.map(mrate)
bank = bank.groupby("bank_id")[["f", "exp", "downtime_h"]].sum()
# split-half replication (Jan-Mar vs Apr-Jun) of each bank's observed/expected, rates re-estimated within each half
bh = q(c, """with f as (select machine_id, k.gaming_date, count(*) f from ops o join cal k
                 on o.opened_at_utc >= k.gaming_day_start_utc and o.opened_at_utc < k.gaming_day_end_utc
                 where event_type='fault_repair' group by all)
select m.bank_id, m.manufacturer, (m.gaming_date >= date '2025-04-01') h2, sum(coalesce(f.f,0)) f, sum(occupied_seconds)/3600 occ
from mday m left join f using(machine_id, gaming_date) group by all""")
hr = bh.groupby(["manufacturer", "h2"]).apply(lambda g: g.f.sum() / g.occ.sum()).rename("r").reset_index()
bh = bh.merge(hr, on=["manufacturer", "h2"]).assign(exp=lambda d: d.occ * d.r).groupby(["bank_id", "h2"])[["f", "exp"]].sum()
bh["o_e"] = bh.f / bh.exp
bh = bh.o_e.unstack()
bank["o_e_h1"], bank["o_e_h2"] = bh[False], bh[True]
bank_half_corr = float(stats.spearmanr(bank.o_e_h1, bank.o_e_h2)[0])
bank["o_e"] = bank.f / bank.exp
bank["p_high"] = stats.poisson.sf(bank.f - 1, bank.exp)
bank_top = bank.sort_values("p_high").head(3)
n_bank_sig = int((bank.p_high < 0.05 / len(bank)).sum())

# monthly downtime by cause, worst month: is it driven by fault volume or by slower repair?
mon_f = faults.groupby("month").agg(faults=("ttr_h", "size"), mean_wait=("wait_h", "mean"), mean_work=("work_h", "mean"))

# repeat faults: top 15 machines; are they more than Poisson given manufacturer & usage?
mach["exp_faults"] = mach.occ_h * mach.manufacturer.map(mrate)
mach["p_excess_poisson"] = stats.poisson.sf(mach.faults - 1, mach.exp_faults)
mach["faults_per_30d"] = mach.faults / mach.sched_days * 30
rep = mach.sort_values(["faults", "downtime_h"], ascending=False).head(15)[
    ["machine_id", "manufacturer", "bank_id", "days_on_floor", "faults", "faults_per_30d", "exp_faults", "p_excess_poisson",
     "downtime_h", "lost_theo_hourly_usd", "repair_usd"]].rename(columns={"exp_faults": "expected_faults_given_mfr_and_use"})
save_csv("06_repeat_faults.csv", rep.round(4))
n_mach_sig = int((mach.p_excess_poisson < 0.05 / len(mach)).sum())
# overdispersion within manufacturer after usage adjustment (Pearson chi2 / df)
od = {mf: float((((g.faults - g.exp_faults) ** 2) / g.exp_faults).sum() / (len(g) - 1)) for mf, g in mach.groupby("manufacturer")}

# ---------------------------------------------------------------- 3. staffing
tech = q(c, """select gaming_date, shift, start_utc, end_utc, planned_headcount planned, actual_headcount actual
               from roster where role='slot_tech' order by start_utc""")
tech["gaming_date"] = pd.to_datetime(tech.gaming_date).dt.date
save_csv("06_tech_by_day.csv", tech[["gaming_date", "shift", "planned", "actual"]].assign(short=tech.actual < tech.planned))

def boot_ci(x, f=np.mean, B=2000):
    x = np.asarray(x)
    return np.percentile([f(rng.choice(x, len(x))) for _ in range(B)], [2.5, 97.5]) if len(x) > 1 else [np.nan, np.nan]

srows = []
for keys, g in faults.groupby(["shift", "short"]):
    lo, hi = boot_ci(g.wait_h)
    tlo, thi = boot_ci(g.ttr_h)
    srows.append(dict(shift=keys[0], staffed_short=bool(keys[1]), n_faults=len(g), shifts=g.shift_start.nunique(),
                      mean_wait_h=g.wait_h.mean(), wait_lo95=lo, wait_hi95=hi, median_wait_h=g.wait_h.median(),
                      share_waited=(g.wait_h > 0).mean(), mean_work_h=g.work_h.mean(),
                      mean_ttr_h=g.ttr_h.mean(), ttr_lo95=tlo, ttr_hi95=thi,
                      mean_actual_techs=g.actual.mean(), mean_planned_techs=g.planned.mean()))
staff = pd.DataFrame(srows)
# finer cut by actual headcount
hc = faults.groupby(["shift", "planned", "actual"]).agg(n=("wait_h", "size"), mean_wait_h=("wait_h", "mean"),
                                                        mean_work_h=("work_h", "mean")).reset_index()
save_csv("06_staffing_effect.csv", staff.round(4))

# second method for the shortage effect: (a) swing shift, same weeks only (Apr 14 - May 25 window contains all
# shortages; swing was short May 5-18 and fully staffed on the other days of the window) -> controls for season/volume;
# (b) day shift matched on planned headcount (every day shift in that window was short, so no same-week control)
win = faults[(faults.gaming_date >= pd.Timestamp("2025-04-14")) & (faults.gaming_date <= pd.Timestamp("2025-05-25"))]
win_sw = win[win["shift"] == "swing"]
a, b = win_sw[win_sw.short].wait_h, win_sw[~win_sw.short].wait_h
diff = a.mean() - b.mean()
diff_ci = np.percentile([rng.choice(a.values, len(a)).mean() - rng.choice(b.values, len(b)).mean() for _ in range(2000)], [2.5, 97.5])
mw = stats.mannwhitneyu(a, b, alternative="greater").pvalue
n_same_weeks = (len(a), len(b))
day_matched = {}
for p_ in [1, 2]:
    g = faults[(faults["shift"] == "day") & (faults.planned == p_)]
    a2, b2 = g[g.short].wait_h, g[~g.short].wait_h
    ci2 = np.percentile([rng.choice(a2.values, len(a2)).mean() - rng.choice(b2.values, len(b2)).mean() for _ in range(2000)], [2.5, 97.5])
    day_matched[f"planned_{p_}"] = dict(n_short=len(a2), n_full=len(b2), wait_short=float(a2.mean()), wait_full=float(b2.mean()),
                                       diff_h=float(a2.mean() - b2.mean()), ci95=[float(x) for x in ci2])
# wait vs work: does shortage lengthen hands-on repair? (it should not, if the effect is queueing)
work_diff = faults[faults.short].work_h.mean() - faults[~faults.short].work_h.mean()
work_p = stats.mannwhitneyu(faults[faults.short].work_h, faults[~faults.short].work_h).pvalue

# ---------------------------------------------------------------- queue replay (validates the mechanism; powers counterfactuals)
S = tech.start_utc.values.astype("datetime64[s]").astype(np.int64)
E = tech.end_utc.values.astype("datetime64[s]").astype(np.int64)
O = faults.opened_at_utc.values.astype("datetime64[s]").astype(np.int64)
D = np.round(faults.work_h.values * 3600).astype(np.int64)

def replay(H, keep=None):
    """FIFO multi-server replay with shift-varying headcount; returns wait hours (nan for dropped faults)."""
    idx = np.arange(len(O)) if keep is None else np.flatnonzero(keep)
    jobs, out, prev = [], np.full(len(O), np.nan), 0
    for i in idx:
        t = max(O[i], prev)
        while True:
            k = np.searchsorted(S, t, side="right") - 1
            busy = [e for (s, e) in jobs if s >= S[k] and s <= t and e > t]
            if len(busy) < H[k]:
                break
            t = min(busy + [E[k]])  # a same-shift job ends, or the next crew arrives
        out[i] = (t - O[i]) / 3600
        prev = t
        jobs.append((t, t + D[i]))
        jobs = [j for j in jobs if j[1] > t - 2 * 86400]
    return out

H_act = tech.actual.values.astype(int)
w_act = replay(H_act)
replay_fit = dict(corr=float(np.corrcoef(w_act, faults.wait_h)[0, 1]),
                  share_within_1min=float(np.mean(np.abs(w_act - faults.wait_h) < 1 / 60)),
                  replay_mean_wait_h=float(w_act.mean()), observed_mean_wait_h=float(faults.wait_h.mean()))
# wait attributable to shortages: replay with planned headcount
w_plan = replay(tech.planned.values.astype(int))
short_cost_h = float(np.sum(w_act - w_plan))
short_cost_theo = float(np.sum((w_act - w_plan) * faults.theo_ph_open_hour))

per_day_idx = pd.to_datetime(daily.gaming_date)
def window_range(saved_by_fault, values_by_fault):
    """Spread the per-fault savings to gaming days; return mean/p10/p90 of all 23-day moving-window sums."""
    s = pd.DataFrame({"d": pd.to_datetime(faults.gaming_date), "h": saved_by_fault, "v": values_by_fault})
    s = s.groupby("d")[["h", "v"]].sum().reindex(per_day_idx, fill_value=0)
    rh, rv = s.h.rolling(HORIZON_DAYS).sum().dropna(), s.v.rolling(HORIZON_DAYS).sum().dropna()
    return dict(hours_low_p10=float(rh.quantile(.1)), hours_central_mean=float(s.h.mean() * HORIZON_DAYS),
                hours_high_p90=float(rh.quantile(.9)), theo_low_p10=float(rv.quantile(.1)),
                theo_central_mean=float(s.v.mean() * HORIZON_DAYS), theo_high_p90=float(rv.quantile(.9)))

tech_actions = {}
cost_tech = 11520.0
for sh in ["day", "swing", "grave"]:
    H = H_act + (tech["shift"].values == sh)
    w = replay(H)
    saved = w_act - w
    val = saved * faults.theo_ph_open_hour  # valued at the machine's own theo rate in the hour the fault opened
    r = window_range(saved, val)
    r.update(dict(theo_low_net_of_displacement=r["theo_low_p10"] * (1 - disp_ci[1]),
                  theo_central_net_of_displacement=r["theo_central_mean"] * (1 - disp_point),
                  net_central_after_cost_usd=r["theo_central_mean"] - cost_tech,
                  saved_hours_whole_period=float(saved.sum()), faults_on_shift=int((faults["shift"] == sh).sum()),
                  mean_wait_on_shift_now_h=float(faults.wait_h[faults["shift"] == sh].mean()),
                  mean_wait_on_shift_with_extra_tech_h=float(w[faults["shift"] == sh].mean()),
                  cost_usd=cost_tech, theo_central_per_dollar=r["theo_central_mean"] / cost_tech))
    tech_actions[sh] = r
best_shift = max(tech_actions, key=lambda s: tech_actions[s]["theo_central_mean"])

# ---------------------------------------------------------------- 5b. preventive sweep for the worst manufacturer
# Baseline: worst manufacturer's fault rate per occupied hour AFTER its change point (most recent regime).
cp_date = pd.Timestamp(cp[worst_mfr]["best_split_date"])
post = (pd.to_datetime(faults.gaming_date) >= cp_date).values
post_days = int((per_day_idx >= cp_date).sum())
w_mfr = mach.manufacturer == worst_mfr
fd_post = fd[fd.gaming_date >= cp_date]
r_worst = fd_post[fd_post.manufacturer == worst_mfr].f.sum() / fd_post[fd_post.manufacturer == worst_mfr].occ.sum()
r_fleet = fd_post.f.sum() / fd_post.occ.sum()
r_others = fd_post[fd_post.manufacturer != worst_mfr].f.sum() / fd_post[fd_post.manufacturer != worst_mfr].occ.sum()
scen = {"low (25% of excess over other manufacturers removed)": r_worst - 0.25 * (r_worst - r_others),
        "central (to fleet average incl. itself)": r_fleet,
        "high (to other manufacturers' average)": r_others}
is_worst = (faults.manufacturer == worst_mfr).values
n_worst_active = int(q(c, f"select count(*) n from mday where manufacturer='{worst_mfr}' and gaming_date=(select max(gaming_date) from mday)").n[0])
# cost of the sweep's own downtime: 90 min per machine in the quietest local hour, valued at that hour's theo rate
quiet = q(c, f"""select b.local_hour, sum(b.theo_ps)*5400/100 theo from rate_b b join machines m using(machine_id)
                 join (select distinct machine_id from mday where gaming_date=(select max(gaming_date) from mday)) a using(machine_id)
                 where m.manufacturer='{worst_mfr}' group by 1 order by 2 limit 1""").iloc[0]
pm = {}
for name, target in scen.items():
    keep_p = target / r_worst
    hs, vs, rc = [], [], []
    for rep_i in range(40):
        drop = is_worst & post & (rng.random(len(O)) > keep_p)
        w = replay(H_act, keep=~drop)
        # saved downtime = full outage of dropped faults + shorter queues for the faults that remain
        saved_h = np.where(drop, faults.ttr_h, w_act - np.nan_to_num(w, nan=0.0))
        saved_v = np.where(drop, faults.theo_b, (w_act - np.nan_to_num(w, nan=0.0)) * faults.theo_ph_open_hour)
        hs.append(saved_h[post].sum() / post_days * HORIZON_DAYS)
        vs.append(saved_v[post].sum() / post_days * HORIZON_DAYS)
        rc.append(faults.repair_usd.values[drop].sum() / post_days * HORIZON_DAYS)
    pm[name] = dict(target_rate_per_1k_occ_h=target * 1000, fault_reduction_share=1 - keep_p,
                    downtime_hours_saved=float(np.mean(hs)), downtime_hours_saved_sim_p05_p95=list(np.percentile(hs, [5, 95])),
                    theo_saved_usd=float(np.mean(vs)), repair_cost_saved_usd=float(np.mean(rc)))
sweep_down_h = n_worst_active * 1.5
pm_net = {k: dict(v, sweep_downtime_h=sweep_down_h, sweep_downtime_theo_usd=float(quiet.theo),
                  net_theo_plus_repair_usd=v["theo_saved_usd"] + v["repair_cost_saved_usd"] - float(quiet.theo),
                  net_after_9000_cost_usd=v["theo_saved_usd"] + v["repair_cost_saved_usd"] - float(quiet.theo) - 9000.0,
                  theo_saved_net_of_displacement_usd=v["theo_saved_usd"] * (1 - disp_point))
          for k, v in pm.items()}

bens = {
    "horizon": "30 days, 7-day lead -> 23 effective days; benefits are theo (expected house win) $, gross of displacement",
    "valuation": "saved wait hours x the faulted machine's own theo per available hour in the local hour the fault opened; "
                 "removed faults valued at their actual hour-by-hour lost theo (method B)",
    "displacement_check": f"neighbour coin-in recovers {disp_point:.0%} (95% CI {disp_ci[0]:.0%}..{disp_ci[1]:.0%}) of a down machine's expected coin-in",
    "A-TECH-SHIFT": dict(cost_usd=cost_tech, method="queue replay of all 1,207 faults with +1 actual slot_tech on every shift of that type; "
                         "low/high = 10th/90th percentile of 23-day moving windows over Jan-Jun",
                         best_shift=best_shift, by_shift=tech_actions),
    "A-PREVENTIVE": dict(cost_usd=9000.0, manufacturer=worst_mfr, machines_active=n_worst_active,
                         assumption=f"No sweep in the data shows an effect; we assume the sweep moves {worst_mfr}'s post-{cp_date.date()} fault rate "
                                    f"({r_worst*1000:.2f}/1k occupied h) to a target for the 23 days; 40 random thinning replays of "
                                    f"its post-change faults through the queue model",
                         scenarios=pm_net),
}
save_json("06_action_benefits.json", bens)

# ---------------------------------------------------------------- availability explanation for weak machines
sc = pd.read_csv(OUT / "03_scorecard.csv") if (OUT / "03_scorecard.csv").exists() else None
avail_test = {}
if sc is not None:
    bot = pd.read_csv(OUT / "02_bottom10.csv")
    fleet_av = mach.avail_h.sum() / mach.sched_h.sum()
    b10 = mach[mach.machine_id.isin(bot.machine_id)]
    avail_test["manager_bottom10_availability"] = float(b10.avail_h.sum() / b10.sched_h.sum())
    avail_test["fleet_availability"] = float(fleet_av)
    avail_test["manager_bottom10_lost_theo_hourly_usd"] = float(b10.lost_theo_hourly_usd.sum())
    avail_test["manager_bottom10_house_win_shortfall_usd"] = float((bot.expected_win - bot.house_win).sum()) if "expected_win" in bot else None
    # rank correlation: machine availability vs theo per available hour (if downtime made machines "weak", lower availability
    # would line up with lower raw totals but not with lower per-available-hour rates)
    m2 = mach.assign(av=mach.avail_h / mach.sched_h, tpa=mach.theo_usd / mach.avail_h, tpd=mach.theo_usd / mach.days_on_floor)
    avail_test["spearman_availability_vs_theo_per_avail_h"] = float(stats.spearmanr(m2.av, m2.tpa)[0])
    avail_test["spearman_availability_vs_theo_per_day"] = float(stats.spearmanr(m2.av, m2.tpd)[0])
    q10 = m2.tpd.quantile(.1)
    weak = m2[m2.tpd <= q10]
    avail_test["bottom_decile_theo_per_day_availability"] = float(weak.avail_h.sum() / weak.sched_h.sum())
    avail_test["bottom_decile_theo_per_day_share_explained_by_downtime"] = float(
        weak.lost_theo_hourly_usd.sum() / ((m2.tpd.median() * weak.days_on_floor) - weak.theo_usd).sum())

# ---------------------------------------------------------------- summary
worst_m = monthly.downtime_h_per_day.idxmax()
worst_w = wk_full.downtime_h.idxmax()
gm_ = mfr.set_index("manufacturer")
summary = {
    "scope": "on-floor machine-days (location_id not null), gaming dates 2025-01-01..2025-06-30; ops mapped to gaming days via calendar_day UTC bounds",
    "reconciliation": {"machine_day_downtime_h": float(recon.md_h), "operations_intervals_h": float(recon.ops_h),
                       "abs_diff_h_machine_day_level": float(recon.abs_diff_h)},
    "total_scheduled_machine_h": float(T.sched_h), "total_downtime_machine_h": float(T.downtime_h),
    "downtime_share_of_scheduled": float(T.downtime_h / T.sched_h),
    "downtime_by_cause_h": {"fault_repair": float(T.down_fault_h), "scheduled_maintenance": float(T.down_maint_h),
                            "move_install_retire": float(T.down_move_install_h)},
    "faults": int(T.faults_opened),
    "lost_theo_usd": {"hourly_method_B_headline": float(T.lost_theo_hourly_usd), "flat_method_A": float(T.lost_theo_flat_usd),
                      "share_of_floor_theo_B": float(T.lost_theo_hourly_usd / T.floor_theo_usd),
                      "method": "B: down seconds x machine's own theo per available second in that local hour of day (machine_hour, theo=coin_in*(1-rtp)); "
                                "A: downtime hours x machine's own period theo per available hour. Gross: assumes no play moves to neighbours."},
    "lost_coin_in_usd": {"hourly_method_B": float(T.lost_coin_in_hourly_usd), "flat_method_A": float(T.lost_coin_in_flat_usd)},
    "displacement_test": {"share_of_lost_coin_in_recovered_by_bank_neighbours": float(disp_point), "ci95": [float(x) for x in disp_ci],
                          "method": "bank-hours with a fully down machine: neighbours' coin-in minus their own hour-of-day expectation, drift-adjusted, "
                                    "divided by lost coin-in; day-cluster bootstrap"},
    "monthly": monthly[["downtime_h", "downtime_h_per_day", "downtime_share", "faults", "lost_theo_hourly_usd"]].round(3).reset_index().to_dict("records"),
    "monthly_fault_wait_work": mon_f.round(3).reset_index().to_dict("records"),
    "worst_month_by_downtime_per_day": worst_m,
    "worst_full_week": {"week_start": str(worst_w.date()), "downtime_h": float(wk_full.downtime_h.max()),
                        "median_week_h": float(wk_full.downtime_h.median()), "lost_theo_hourly_usd": float(wk_full.loc[worst_w, "lost_theo_hourly_usd"])},
    "weekly_downtime_h_full_weeks": {str(k.date()): round(float(v), 1) for k, v in wk_full.downtime_h.items()},
    "mean_downtime_h_by_weekday": by_wd,
    "downtime_h_by_local_hour": hour_prof.h.round(1).tolist(),
    "manufacturer": {m: {k: (round(float(v), 4) if isinstance(v, (float, np.floating, int, np.integer)) else v)
                         for k, v in gm_.loc[m, ["faults_per_machine_30d", "faults_per_machine_30d_lo95_boot", "faults_per_machine_30d_hi95_boot",
                                                  "rate_ratio_vs_others", "p_rate_ratio_exact", "faults_per_1k_occupied_h",
                                                  "rate_ratio_vs_others_per_occ_h", "p_rate_ratio_occ_exact", "mttr_h", "mttr_h_lo95", "mttr_h_hi95",
                                                  "mean_wait_h", "downtime_h_per_machine", "availability", "lost_theo_hourly_usd", "repair_cost_usd"]].items()}
                     for m in gm_.index},
    "worst_manufacturer": worst_mfr,
    "change_points_fault_rate_per_occ_h": cp,
    "worst_mfr_q2_vs_q1": {"rate_ratio": float(rr_w), "p_exact": float(p_w), "others_rate_ratio": float(rr_c),
                           "ratio_of_ratios": float(did), "ci95": [float(x) for x in did_ci]},
    "banks": {"n_banks": len(bank), "n_significant_excess_bonferroni": n_bank_sig, "split_half_spearman_o_e": bank_half_corr,
              "top3_by_p": bank_top.round(4).reset_index().to_dict("records")},
    "repeat_faults": {"n_machines_excess_bonferroni": n_mach_sig, "pearson_dispersion_by_mfr_after_usage": od,
                      "note": "fault counts are ~Poisson given manufacturer x occupied hours: top repeat machines are high-use machines of the worst manufacturer, not lemons"},
    "fault_rate_scales_with_use": "theme Platinum Reels has ~0.11 faults/machine/30d but the same faults per occupied hour as others",
    "staffing": {
        "by_shift_short": staff[["shift", "staffed_short", "n_faults", "mean_wait_h", "wait_lo95", "wait_hi95", "median_wait_h", "mean_ttr_h"]].round(3).to_dict("records"),
        "by_headcount": hc.round(3).to_dict("records"),
        "same_weeks_swing_short_minus_full_wait_h": float(diff), "ci95": [float(x) for x in diff_ci], "mannwhitney_p": float(mw),
        "same_weeks_n_short_full": n_same_weeks, "day_shift_matched_on_planned": day_matched,
        "work_time_short_minus_full_h": float(work_diff), "work_time_p": float(work_p),
        "queue_replay_fit": replay_fit,
        "downtime_h_caused_by_shortages": short_cost_h, "theo_usd_lost_to_shortages": short_cost_theo,
        "wait_by_local_hour_mean_h": faults.groupby("local_hour").wait_h.mean().round(2).tolist(),
    },
    "actions": {"tech_best_shift": best_shift,
                "tech_by_shift_theo_central_23d": {k: round(v["theo_central_mean"], 0) for k, v in tech_actions.items()},
                "tech_by_shift_hours_central_23d": {k: round(v["hours_central_mean"], 1) for k, v in tech_actions.items()},
                "preventive": {k: {"hours": round(v["downtime_hours_saved"], 1), "theo": round(v["theo_saved_usd"], 0),
                                   "repair": round(v["repair_cost_saved_usd"], 0), "net_before_cost": round(v["net_theo_plus_repair_usd"], 0),
                                   "net_after_cost": round(v["net_after_9000_cost_usd"], 0)} for k, v in pm_net.items()}},
    "availability_explains_weak_machines": avail_test,
}
save_json("06_summary.json", summary)

# ---------------------------------------------------------------- short print
print(f"downtime {T.downtime_h:,.0f} machine-h = {T.downtime_h/T.sched_h:.2%} of scheduled; faults {T.down_fault_h/T.downtime_h:.1%}")
print(f"lost theo B ${T.lost_theo_hourly_usd:,.0f} (A ${T.lost_theo_flat_usd:,.0f}); = {T.lost_theo_hourly_usd/T.floor_theo_usd:.2%} of floor theo; "
      f"lost coin-in B ${T.lost_coin_in_hourly_usd:,.0f}")
print(f"displacement recovered {disp_point:.0%} CI {disp_ci.round(2)}")
print(f"worst month {worst_m}; worst week {worst_w.date()} {wk_full.downtime_h.max():.0f}h (median {wk_full.downtime_h.median():.0f}h)")
print(mfr[["manufacturer", "faults_per_machine_30d", "rate_ratio_vs_others", "rate_ratio_vs_others_per_occ_h", "mttr_h", "mean_wait_h",
           "downtime_h_per_machine", "lost_theo_hourly_usd", "repair_cost_usd"]].round(3).to_string(index=False))
print("change points:", {k: (v["best_split_date"], round(v["rate_per_1k_occ_h_before"], 2), round(v["rate_per_1k_occ_h_after"], 2), v["p_scan_permutation"]) for k, v in cp.items()})
print(f"{worst_mfr} Q2/Q1 RR {rr_w:.2f} p={p_w:.4f}; others {rr_c:.2f}; ratio {did:.2f} CI {np.round(did_ci,2)}")
print(bank_top[["f", "exp", "o_e", "o_e_h1", "o_e_h2", "p_high"]].round(3).to_string(), "split-half rho", round(bank_half_corr, 2))
print(f"banks sig {n_bank_sig}/{len(bank)}; machines sig excess {n_mach_sig}; dispersion {({k: round(v,2) for k,v in od.items()})}")
print(staff[["shift", "staffed_short", "n_faults", "mean_wait_h", "wait_lo95", "wait_hi95", "mean_ttr_h"]].round(2).to_string(index=False))
print(f"same-weeks swing short-full wait {diff:.2f}h CI {np.round(diff_ci,2)} p={mw:.4f} n={n_same_weeks}; work diff {work_diff:.2f}h p={work_p:.2f}")
print("day matched:", {k: (round(v['diff_h'],2), np.round(v['ci95'],2).tolist(), v['n_short']) for k, v in day_matched.items()})
print(f"replay fit {replay_fit}; shortages cost {short_cost_h:.0f}h / ${short_cost_theo:,.0f} theo")
for k, v in tech_actions.items():
    print(f"  +1 tech {k}: {v['hours_low_p10']:.0f}/{v['hours_central_mean']:.0f}/{v['hours_high_p90']:.0f} h, "
          f"${v['theo_low_p10']:,.0f}/${v['theo_central_mean']:,.0f}/${v['theo_high_p90']:,.0f} theo")
for k, v in pm_net.items():
    print(f"  PM {worst_mfr} {k}: {v['downtime_hours_saved']:.0f}h, theo ${v['theo_saved_usd']:,.0f}, repair ${v['repair_cost_saved_usd']:,.0f}, net ${v['net_theo_plus_repair_usd']:,.0f}, after cost ${v['net_after_9000_cost_usd']:,.0f}, sweep downtime ${v['sweep_downtime_theo_usd']:,.0f}")
print("availability test:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in avail_test.items()})

"""10 Daily floor coin-in forecast. Train Jan–Apr 2025, test May–Jun 2025 (time split, no shuffling).

Forecast issue date: 2025-04-30 (end of training). Both forecasts are frozen before May–Jun actuals are read.
  Baseline  : constant training-period mean of daily coin-in (the guide's baseline).
  Baseline 2: seasonal naive, frozen = repeat the last training week (Apr 24–30) by weekday.
  Reference : rolling seasonal naive (same weekday one week earlier, uses actuals up to t−7; NOT frozen, shown for context).
  Model     : log(coin-in per scheduled hour) ~ weekday + event flags + federal holiday + B40-walkway-closure flag,
              OLS on training days. Prediction = exp(fit) * scheduled hours * smearing factor.
Event flags use only events whose announced_date <= issue date (no leakage). The Locals Appreciation Week
promo (announced 2025-05-26) was NOT known on Apr 30, so the frozen forecast does not see it.
The closure flag is 1 only Mar 3–Apr 7 (known and over by the issue date), so it is 0 for every test day.
Model choice was made on an inner split (train Jan–Mar, validate Apr) BEFORE touching May–Jun.
Writes outputs/10_forecast_daily.csv, outputs/10_forecast_metrics.json."""
from common import con, q, save_csv, save_json
import numpy as np, pandas as pd
import statsmodels.formula.api as smf

ISSUE = pd.Timestamp("2025-04-30")
c = con()
d = q(c, """select p.gaming_date, p.local_weekday wd, p.coin_in_usd::double coin_in, c.scheduled_seconds/3600.0 sched_h,
            c.federal_holiday is not null hol from prop p join cal c using(gaming_date) order by 1""")
ev = q(c, "select * from events")
def flags(df, known_by):
    df = df.copy()
    known = ev[ev.announced_date <= known_by]
    weekend = df.wd.isin(["Fri", "Sat", "Sun"])
    for col in ["ev_weekend_draw", "ev_convention", "ev_promo"]:
        df[col] = 0
    for _, e in known.iterrows():
        on = (df.gaming_date >= e.start_date) & (df.gaming_date <= e.end_date)
        if e.event_type == "property_promotion":
            df.loc[on, "ev_promo"] = 1
        elif e.event_type == "convention":
            df.loc[on, "ev_convention"] = 1
        else:  # sports_viewing / festival
            df.loc[on & weekend, "ev_weekend_draw"] = 1
    df["closure"] = ((df.gaming_date >= "2025-03-03") & (df.gaming_date <= "2025-04-07")).astype(int)
    df["hol"] = df.hol.astype(int)
    df["y"] = np.log(df.coin_in / df.sched_h)
    return df

FORMULAS = {
    "weekday": "y ~ C(wd)",
    "weekday+events": "y ~ C(wd) + ev_weekend_draw + ev_convention + ev_promo + hol + closure",
}
def fit_predict(train, test, formula):
    m = smf.ols(formula, train).fit()
    smear = np.mean(np.exp(m.resid))
    pred = np.exp(m.predict(test)) * test.sched_h * smear
    return m, pred

# ---- inner validation (model selection) : train Jan–Mar, validate April
inner_issue = pd.Timestamp("2025-03-31")
di = flags(d, inner_issue)
tr, va = di[di.gaming_date <= inner_issue], di[(di.gaming_date > inner_issue) & (di.gaming_date <= ISSUE)]
inner = {"constant_mean": float(np.mean(np.abs(va.coin_in - tr.coin_in.mean())))}
for k, f in FORMULAS.items():
    f2 = f.replace(" + closure", "")  # closure not observed after Mar in inner train end? it is partially; keep simple
    _, p = fit_predict(tr, va, f if k == "weekday" else f)
    inner[k] = float(np.mean(np.abs(va.coin_in - p)))
chosen = min(FORMULAS, key=lambda k: inner[k])

# ---- frozen forecasts at the Apr 30 issue date
df = flags(d, ISSUE)
train, test = df[df.gaming_date <= ISSUE].copy(), df[df.gaming_date > ISSUE].copy()
base_value = train.coin_in.mean()
model, _ = fit_predict(train, train, FORMULAS[chosen])
frozen_pred = fit_predict(train, test, FORMULAS[chosen])[1]
resid_sd = np.std(model.resid, ddof=len(model.params))
smear = np.mean(np.exp(model.resid))
test["baseline"] = base_value
lastwk = train[train.gaming_date > ISSUE - pd.Timedelta(days=7)].set_index("wd").coin_in
test["snaive"] = test.wd.map(lastwk).values
allc = df.set_index("gaming_date").coin_in
test["snaive_rolling"] = [allc.get(g - pd.Timedelta(days=7), np.nan) for g in test.gaming_date]
test["model"] = frozen_pred
test["model_lo80"] = np.exp(model.predict(test) - 1.2816 * resid_sd) * test.sched_h
test["model_hi80"] = np.exp(model.predict(test) + 1.2816 * resid_sd) * test.sched_h
# Also: the other candidate, for transparency (not used for the decision)
other = [k for k in FORMULAS if k != chosen][0]
test["alt_" + other.replace("+", "_")] = fit_predict(train, test, FORMULAS[other])[1]
train["model"] = np.exp(model.fittedvalues) * train.sched_h * smear
train["baseline"] = base_value
train["snaive"] = np.nan; train["snaive_rolling"] = np.nan
out = pd.concat([train.assign(split="train"), test.assign(split="test")])
save_csv("10_forecast_daily.csv", out[["gaming_date", "wd", "split", "coin_in", "baseline", "snaive", "model"] +
                                     [c_ for c_ in out.columns if c_.startswith(("model_", "alt_"))] +
                                     ["ev_weekend_draw", "ev_convention", "ev_promo", "hol"]])

mae = lambda a, b: float(np.mean(np.abs(a - b)))
err = test.coin_in - test.model
by_wd = test.assign(err=err, abs_b=(test.coin_in - test.baseline).abs(), abs_m=err.abs(), abs_s=(test.coin_in - test.snaive).abs()).groupby("wd").agg(
    n=("err", "size"), bias_model=("err", "mean"), mae_model=("abs_m", "mean"), mae_baseline=("abs_b", "mean"), mae_snaive=("abs_s", "mean"))
# bootstrap CI for MAE improvement (day-level resampling)
rng = np.random.default_rng(7)
ab, am = (test.coin_in - test.baseline).abs().values, err.abs().values
boot = [np.mean(ab[i] - am[i]) for i in (rng.integers(0, len(ab), len(ab)) for _ in range(5000))]
asn = (test.coin_in - test.snaive).abs().values
boot2 = [np.mean(asn[i] - am[i]) for i in (rng.integers(0, len(asn), len(asn)) for _ in range(5000))]
wd_mult = (train.groupby("wd").coin_in.mean() / train.coin_in.mean()).round(3).to_dict()
metrics = dict(
    issue_date=str(ISSUE.date()), train_days=int(len(train)), test_days=int(len(test)),
    inner_validation_mae=inner, chosen_model=chosen, formula=FORMULAS[chosen],
    baseline_value=base_value,
    test_mae_baseline=mae(test.coin_in, test.baseline), test_mae_model=mae(test.coin_in, test.model),
    test_mae_snaive=mae(test.coin_in, test.snaive), test_mape_snaive=float(np.mean(np.abs(test.coin_in - test.snaive) / test.coin_in)),
    mae_improvement_vs_snaive_ci95=[float(np.percentile(boot2, 2.5)), float(np.percentile(boot2, 97.5))],
    test_mae_snaive_rolling=mae(test.coin_in, test.snaive_rolling),
    inner_mae_snaive=float(np.mean(np.abs(va.coin_in.values - va.wd.map(tr[tr.gaming_date > inner_issue - pd.Timedelta(days=7)].set_index("wd").coin_in).values))),
    test_mae_alt=mae(test.coin_in, test["alt_" + other.replace("+", "_")]), alt_model=other,
    test_mape_baseline=float(np.mean(np.abs(test.coin_in - test.baseline) / test.coin_in)),
    test_mape_model=float(np.mean(np.abs(err) / test.coin_in)),
    mae_improvement_ci95=[float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
    test_bias_model=float(err.mean()),
    pi80_coverage=float(np.mean((test.coin_in >= test.model_lo80) & (test.coin_in <= test.model_hi80))),
    coefficients={k: float(v) for k, v in model.params.items()},
    weekday_multiplier_train=wd_mult,
    by_weekday=by_wd.round(0).reset_index().to_dict("records"),
    locals_week_actual_vs_model=float((test.coin_in - test.model)[(test.gaming_date >= "2025-06-09") & (test.gaming_date <= "2025-06-15")].mean()),
)
save_json("10_forecast_metrics.json", metrics)
for k in ["test_mae_snaive", "test_mae_snaive_rolling", "inner_mae_snaive", "inner_validation_mae", "chosen_model", "test_mae_baseline", "test_mae_model", "test_mae_alt", "test_mape_baseline",
          "test_mape_model", "mae_improvement_ci95", "test_bias_model", "pi80_coverage", "weekday_multiplier_train",
          "locals_week_actual_vs_model"]:
    print(k, metrics[k])
print({k: round(v, 3) for k, v in metrics["coefficients"].items()})

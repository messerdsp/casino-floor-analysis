"""Export compact, precomputed data for the dashboard into outputs/dashboard_data.json.
Core fact table: on-floor machine-days (location_id not null) in columnar form, so the page can
re-aggregate any date range / bank in plain JS with correct ratio-of-sums and luck variance."""
from common import con, q, OUT, save_json
import json, numpy as np, pandas as pd

c = con()
md = q(c, """
select m.machine_id, m.gaming_date, l.bank_id, l.zone_id,
       m.coin_in_cents/100.0 ci, m.house_win_cents/100.0 hw, m.theo_cents::double/100.0 th,
       m.sum_bet_squared*g.payout_multiplier_variance::double/10000.0 var,
       m.available_seconds/3600.0 ah, m.scheduled_seconds/3600.0 sh, m.occupied_seconds/3600.0 oh,
       m.paid_spins sp, m.payout_positive_spins ps
from md m join gm g using(config_id) join locs l on l.location_id=m.location_id
where m.location_id is not null order by m.gaming_date, m.machine_id""")
days = sorted(md.gaming_date.dt.strftime("%Y-%m-%d").unique())
day_idx = {d: i for i, d in enumerate(days)}
sc = pd.read_csv(OUT / "04_verdicts.csv")
machines = sc.machine_id.tolist()
m_idx = {m: i for i, m in enumerate(machines)}
banks = sorted(md.bank_id.unique())
b_idx = {b: i for i, b in enumerate(banks)}
bank_zone = md.groupby("bank_id").zone_id.first().to_dict()
zones = sorted(md.zone_id.unique())

fact = {
    "m": md.machine_id.map(m_idx).tolist(),
    "d": md.gaming_date.dt.strftime("%Y-%m-%d").map(day_idx).tolist(),
    "b": md.bank_id.map(b_idx).tolist(),
    "ci": md.ci.round(2).tolist(), "hw": md.hw.round(2).tolist(), "th": md.th.round(2).tolist(),
    "var": md["var"].round(1).tolist(), "ah": md.ah.round(3).tolist(), "sh": md.sh.round(3).tolist(),
    "oh": md.oh.round(3).tolist(), "sp": md.sp.astype(int).tolist(), "ps": md.ps.astype(int).tolist(),
}
gm = q(c, "select config_id, theme, configured_rtp::double rtp, hit_probability::double p, volatility_class from gm")
mach_meta = sc.merge(gm[["theme","p"]], on="theme")[["machine_id", "p", "theme", "volatility", "rtp", "manufacturer", "lease_class", "zone_id", "bank_id",
                "verdict", "hit_z", "theo_h_vs_zone", "coin_h_vs_zone", "replacement_gain_per_day", "days_on_floor",
                "availability", "faults"]].copy()
cal = q(c, "select gaming_date, local_weekday, federal_holiday from cal order by 1")
prop = q(c, "select gaming_date, events_active from prop order by 1")
zname = q(c, "select zone_id, name from locs where node_type='zone'").set_index("zone_id").name.to_dict()
events = q(c, "select event_id, name, event_type, start_date, end_date, announced_date from events order by start_date")
data = dict(
    days=days, weekday=cal.local_weekday.tolist(), holiday=cal.federal_holiday.fillna("").tolist(),
    events_active=prop.events_active.fillna("").tolist(),
    machines=machines, banks=banks, bank_zone=bank_zone, zones=zones, zone_names=zname,
    machine_meta=json.loads(mach_meta.round(4).to_json(orient="records")),
    fact=fact, events=json.loads(events.to_json(orient="records", date_format="iso")),
    game_math=json.loads(gm.to_json(orient="records")),
)
with open(OUT / "dashboard_fact.json", "w") as f:
    json.dump(data, f, separators=(",", ":"))
print("rows", len(md), "days", len(days), "machines", len(machines), "banks", len(banks),
      "MB", round((OUT / "dashboard_fact.json").stat().st_size / 1e6, 2))

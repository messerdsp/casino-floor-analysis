"""01 Data audit: row counts, reconciliation, units, off-floor rows, DST, carded coverage.
Writes outputs/01_audit.json."""
from common import con, q, save_json

c = con()
r = {}
r["rows"] = {t: int(c.execute(f"select count(*) from {t}").fetchone()[0])
             for t in ["pdv", "md", "prop", "pp", "ops", "plc", "machines", "sessions", "ledger", "fnb", "traffic"]}

# Off-floor rows (location empty) -> excluded everywhere
r["off_floor_rows"] = int(c.execute("select count(*) from pdv where location_id is null").fetchone()[0])
r["off_floor_active_flag"] = q(c, "select active, meter_status, count(*) n from pdv where location_id is null group by all").to_dict("records")
r["off_floor_any_coin_in"] = float(c.execute("select coalesce(sum(coin_in_usd),0) from pdv where location_id is null").fetchone()[0])

# Cents (machine_day) vs dollars (prepared view) reconcile
rec = q(c, """select sum(m.coin_in_cents)/100.0 md_coin, sum(p.coin_in_usd) pdv_coin,
                     sum(m.house_win_cents)/100.0 md_hw, sum(p.house_win_usd) pdv_hw,
                     sum(m.theo_cents)/100.0 md_theo, sum(p.theo_usd) pdv_theo,
                     sum(case when m.house_win_cents <> m.coin_in_cents - m.payout_cents then 1 else 0 end) hw_identity_breaks
              from md m join pdv p using(machine_id, gaming_date)""").iloc[0]
r["reconcile_md_vs_pdv"] = {k: float(v) for k, v in rec.items()}
pr = q(c, "select sum(coin_in_usd) coin, sum(house_win_usd) hw, sum(theo_usd) theo from prop").iloc[0]
r["property_day_totals"] = {k: float(v) for k, v in pr.items()}
r["floor_hold_ratio_of_sums"] = float(pr.hw / pr.coin)
r["floor_theo_hold"] = float(pr.theo / pr.coin)
r["mean_of_daily_holds_WRONG"] = float(c.execute("select avg(observed_hold) from pdv where location_id is not null").fetchone()[0])

# DST day
r["dst"] = q(c, "select gaming_date, scheduled_seconds from cal where scheduled_seconds <> 86400").to_dict("records")
r["active_machines_per_day"] = q(c, "select min(active_machines) mn, max(active_machines) mx from prop").iloc[0].to_dict()

# Carded coverage
cc = q(c, "select sum(carded_coin_in_cents)/sum(coin_in_cents) share from md").iloc[0]
r["carded_coin_in_share"] = float(cc.share)
s = q(c, "select sum(coin_in_cents)/100.0 sess_coin from sessions").iloc[0]
r["sessions_coin_in_usd"] = float(s.sess_coin)
r["md_carded_coin_in_usd"] = float(c.execute("select sum(carded_coin_in_cents)/100.0 from md").fetchone()[0])
r["pp_tracked_coin_in_usd"] = float(c.execute("select sum(tracked_coin_in_usd) from pp").fetchone()[0])

# Comp double count check: comp cost inside cogs
r["comp_inside_cogs"] = q(c, "select sum(comp_cost_usd) comp, sum(pos_cogs_usd) cogs from prop").iloc[0].astype(float).to_dict()

# Traffic counter health
r["traffic_status"] = q(c, "select counter_status, count(*) n, count(entries_count) non_null from traffic group by 1").to_dict("records")

# Placements: machines that moved
r["machines_with_multiple_placements"] = int(c.execute(
    "select count(*) from (select machine_id from plc group by 1 having count(*)>1)").fetchone()[0])
r["config_changes"] = int(c.execute(
    "select count(*) from (select machine_id from plc group by 1 having count(distinct config_id)>1)").fetchone()[0])
r["location_changes"] = int(c.execute(
    "select count(*) from (select machine_id from plc group by 1 having count(distinct location_id)>1)").fetchone()[0])

save_json("01_audit.json", r)
for k in ["off_floor_rows", "floor_hold_ratio_of_sums", "floor_theo_hold", "mean_of_daily_holds_WRONG",
          "carded_coin_in_share", "machines_with_multiple_placements", "config_changes", "location_changes"]:
    print(k, r[k])
print("reconcile", r["reconcile_md_vs_pdv"])
print("property", r["property_day_totals"])
print("dst", r["dst"], "traffic", r["traffic_status"])
print("carded md/sessions/pp", r["md_carded_coin_in_usd"], r["sessions_coin_in_usd"], r["pp_tracked_coin_in_usd"])

"""Shared paths and helpers. All analysis scripts import from here."""
import json, os, pathlib
import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "data"
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)

TABLES = {
    "pdv": "1_prepared_views/prepared_daily_view", "pp": "1_prepared_views/player_period",
    "prop": "1_prepared_views/property_day", "actions": "2_machine_decision/action_catalog",
    "cal": "2_machine_decision/calendar_day", "cost_rules": "2_machine_decision/cost_rules",
    "locs": "2_machine_decision/floor_locations", "walkways": "2_machine_decision/floor_walkways",
    "gm": "2_machine_decision/game_math", "md": "2_machine_decision/machine_day",
    "machines": "2_machine_decision/machines", "ops": "2_machine_decision/operations",
    "plc": "2_machine_decision/placements", "fnb": "3_guests/fnb_transactions",
    "ledger": "3_guests/loyalty_ledger", "players": "3_guests/players",
    "sessions": "3_guests/sessions", "trips": "3_guests/trips",
    "events": "4_context/events", "traffic": "4_context/zone_traffic_hourly",
    "city": "4_context/real_sources/city_visitation_monthly",
    "weather": "4_context/real_sources/weather_daily",
    "bench_prop": "4_context/real_sources/benchmark_property_monthly",
    "bench_state": "4_context/real_sources/benchmark_state_monthly",
    "mh": "5_optional/machine_hour", "incidents": "5_optional/security_incidents",
    "roster": "5_optional/shift_roster", "survey": "5_optional/visitor_profile_monthly",
}

def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    for k, v in TABLES.items():
        c.execute(f"CREATE VIEW {k} AS SELECT * FROM read_parquet('{RAW / (v + '.parquet')}')")
    return c

def save_json(name, obj):
    with open(OUT / name, "w") as f:
        json.dump(obj, f, indent=1, default=str)

def save_csv(name, df):
    df.to_csv(OUT / name, index=False)

def q(c, sql, **kw):
    """Run SQL and return a pandas DataFrame."""
    return c.execute(sql, kw or None).df() if kw else c.execute(sql).df()

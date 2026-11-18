"""Assemble dashboard.html: template + inline plotly.js + embedded data (fact table + small outputs)."""
from common import OUT, ROOT
import json, pathlib, pandas as pd, plotly

A = pathlib.Path(__file__).parent
def csv(name):
    p = OUT / name
    return json.loads(pd.read_csv(p).to_json(orient="records")) if p.exists() else []
def js(name):
    p = OUT / name
    return json.load(open(p)) if p.exists() else {}

leak = js("05_leak_summary.json")
flags = pd.read_csv(OUT / "05_leak_flags.csv")
x = flags[flags.machine_id != "M-1158"]
leak["hit_z_sd_excl"], leak["hit_z_max_excl"] = float(x.hit_z.std()), float(x.hit_z.abs().max())
out = {
    "bottom10": csv("02_bottom10.csv"), "effects": csv("04_effects.csv"),
    "leak_monthly": csv("05_leak_monthly.csv"),
    "leak_flags": json.loads(flags[["machine_id", "theme", "hit_z", "luck_z"]].round(3).to_json(orient="records")),
    "leak_summary": leak, "title_pooled": csv("05_title_pooled.csv"),
    "forecast_daily": csv("10_forecast_daily.csv"), "forecast_metrics": js("10_forecast_metrics.json"),
    "down_daily": csv("06_downtime_daily.csv"), "down_summary": js("06_summary.json"), "mfr": csv("06_downtime_by_manufacturer.csv"),
    "staff": csv("06_staffing_effect.csv"),
    "player_totals": js("08_player_totals.json"), "lorenz": csv("08_lorenz.csv"), "nethist": csv("08_net_hist.csv"),
    "groups": csv("08_groups.csv"), "group_monthly": csv("08_group_monthly.csv"),
    "comp_daily": csv("09_comp_daily.csv"), "comp_change": js("09_comp_change.json"), "comp_summary": js("09_summary.json"),
    "floor_daily": csv("07_floor_daily.csv"), "event_effects": csv("07_event_effects.csv"), "monthly": csv("07_monthly.csv"),
    "closure": js("07_closure_did.json"), "moves": csv("07_moves.csv"), "demand_summary": js("07_summary.json"),
    "plan": js("11_plan.json"), "transfer": js("04_summary.json").get("title_transfer_test", {}),
    "down_benefits": js("06_action_benefits.json"), "swaps_all": csv("11_swaps.csv"),
}
html = (A / "dashboard_template.html").read_text()
plotly_js = (pathlib.Path(plotly.__file__).parent / "package_data" / "plotly.min.js").read_text()
app = (A / "dashboard_app.js").read_text() + "\n" + (A / "dashboard_sections.js").read_text()
sec2 = A / "dashboard_sections2.js"
if sec2.exists():
    app += "\n" + sec2.read_text()
frag = A / "dashboard_fragments.json"   # html fragments for late sections
if frag.exists():
    for k, v in json.load(open(frag)).items():
        html = html.replace(f"<!--__{k}__-->", v)
html = (html.replace("/*__PLOTLY__*/", plotly_js.replace("</script>", "<\\/script>"))
            .replace("/*__FACT__*/", (OUT / "dashboard_fact.json").read_text())
            .replace("/*__OUT__*/", json.dumps(out, separators=(",", ":"), default=str).replace("</", "<\\/"))
            .replace("/*__APPJS__*/", app))
(ROOT / "dashboard.html").write_text(html)
print("dashboard.html MB", round((ROOT / "dashboard.html").stat().st_size / 1e6, 2))

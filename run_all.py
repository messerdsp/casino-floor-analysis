"""Rebuild every output and dashboard.html from raw/ (the cloned dataset).
Usage: .venv/bin/python run_all.py [--pull]   (--pull: git pull the dataset first, then verify it)"""
import subprocess, sys, pathlib, time
ROOT = pathlib.Path(__file__).resolve().parent
PY = sys.executable
if "--pull" in sys.argv:
    subprocess.run(["git", "-C", str(ROOT / "raw"), "pull", "--ff-only"], check=True)
subprocess.run([PY, "scripts/verify_data.py"], cwd=ROOT / "raw", check=True)
steps = ["01_audit.py", "02_bottom10.py", "03_machine_scorecard.py", "05_leak.py", "04_location_vs_machine.py",
         "06_downtime.py", "07_demand.py", "08_players.py", "09_comps.py", "10_forecast.py", "11_plan.py",
         "build_data.py", "make_fragments.py", "build_dashboard.py"]
for s in steps:
    t = time.time()
    r = subprocess.run([PY, s], cwd=ROOT / "analysis", capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-2000:], r.stderr[-3000:]); sys.exit(f"FAILED: {s}")
    print(f"ok  {s:28s} {time.time()-t:5.1f}s")
print("done -> dashboard.html")

"""HTML fragments injected into the dashboard template (sections whose layout depends on late analysis)."""
import json, pathlib
F = {}
F["DEMAND"] = '''<p class="caption filtered">Daily floor coin-in (all play). Orange bars are days with a city event or one of our promotions. Shaded: Back Forty walkway closed.</p>
  <div id="c-floor" class="chart"></div>
  <div class="grid2">
   <div><h3>What each event did (vs. matched non-event days)</h3><div id="c-events" class="chart tall"></div></div>
   <div><h3 class="filtered">Month by month</h3><div class="tablewrap"><table id="t-monthly"></table></div>
   <p class="note">Weekday-adjusted, no events = average coin-in on non-event days after removing the weekday mix. Counter entries are sensor crossings (not unique people), adjusted for offline hours. LVCVA = all Las Vegas visitors, published 2026: context, not an input.</p></div>
  </div>
  <div id="demand-answer"></div>'''
F["LOCATION_EXTRA"] = '''<h3>Machines that moved: did they take on their new zone's level?</h3>
  <div class="tablewrap"><table id="t-moves"></table></div>'''
F["PLAN"] = '''<p class="caption">Cost = quantity × (unit cost + daily cost × (30 − lead days)), per the guide. Benefits are expected house win (theo) from our own data, counted only for days after the lead time. Each action links to the evidence behind it.</p>
  <div class="kpis" id="plan-kpis"></div>
  <div id="plan-cards"></div>
  <div id="plan-answer"></div>
  <h3>What we chose not to fund, and why</h3>
  <div class="tablewrap"><table id="t-rejected"></table></div>'''
F["METHODS"] = '''<div class="grid2"><div>
  <h3>Money math</h3><ul class="caption">
   <li>Hold = Σ house win ÷ Σ coin-in (ratio of sums). Floor hold 9.50% vs. 9.46% theo; a mean of daily holds would wrongly give 9.25%.</li>
   <li>Theo = Σ bet × (1 − published RTP). Luck = house win − theo.</li>
   <li>Luck uncertainty: Var(house win) = Σ bet² × Var(payout multiplier) from <code>game_math</code> (exact for the bets played; uses <code>sum_bet_squared</code>). z = luck ÷ SD; 95% range = ±1.96 SD. Hit-rate test: binomial z against <code>hit_probability</code>.</li>
   <li>Only on-floor days (1,018 off-floor rows excluded). Exposure = available hours (scheduled − downtime); Mar 8 has 23 hours.</li>
   <li>Location by day from <code>machine_day.location_id</code> (machines move). Cents converted once. Comp cost counted once (it sits inside POS cost of goods).</li>
   <li>Player tables are carded play only (65.8% of coin-in); they reconcile to <code>machine_day.carded_*</code> to the cent.</li></ul>
  <h3>Verdict rules (section 6, applied in order)</h3><ol class="caption">
   <li><b>Payout lead</b>: luck z &lt; −3 or hit-rate z &gt; 4 → inspect.</li>
   <li><b>Short window</b>: &lt; 90 days on the floor.</li>
   <li><b>Availability</b>: availability &lt; 98.5% and downtime cost &gt; 5% of theo.</li>
   <li><b>Weak game for its spot</b>: theo per available hour &lt; 75% of zone peers, and coin-in per hour not above peers. In practice these are loose (92–95% RTP) titles: guests play them about as much, but the game keeps less. The fix is moving the game, not repairing the machine.</li>
   <li><b>Location</b>: the machine's zone earns &lt; 80% of floor theo per hour.</li>
   <li><b>Unlucky</b>: below theo but within noise; otherwise <b>OK</b>.</li></ol>
 </div><div>
  <h3>Scripts behind each section (all in <code>analysis/</code>, rebuilt by <code>run_all.py</code>)</h3><ul class="caption">
   <li>Audit &amp; reconciliation: <code>01_audit.py</code>. Manager's list: <code>02_bottom10.py</code> (the guide's DuckDB query, verbatim).</li>
   <li>Luck &amp; scorecard: <code>03_machine_scorecard.py</code>. Zone/title model &amp; verdicts: <code>04_location_vs_machine.py</code>.</li>
   <li>Leak tests: <code>05_leak.py</code>. Downtime &amp; staffing: <code>06_downtime.py</code>. Demand, closure, events, moves: <code>07_demand.py</code>.</li>
   <li>Players: <code>08_players.py</code>. Comps: <code>09_comps.py</code>. Forecast: <code>10_forecast.py</code>. Plan: <code>11_plan.py</code>.</li></ul>
  <h3>Limitations</h3><ul class="caption">
   <li>Synthetic data; real-world magnitudes will differ. Supplied costs are partial (no rent, tax, full labour); benefits are theo, not profit.</li>
   <li>The zone + title model's R² (0.97) is partly mechanical, because theo = coin-in × house edge. But zone + title also explain 95% of coin-in per hour, which has no edge built in, and title effects estimated separately in the Back Forty and in prime zones correlate 0.97, so a title keeps its relative earning power when moved. Tighter games draw a little less play (10% more edge → ~2% less coin-in per hour), with no extra penalty in prime zones.</li>
   <li>Zone and title effects are observational. The M-1152 move and the walkway closure are the only natural experiments, and the swap plan includes a measurement design for that reason.</li>
   <li>Player concentration covers carded play only. Uncarded guests can't be profiled.</li>
   <li>Event uplift CIs rest on 13–19 placebo windows. The forecast was selected on a single month of inner validation.</li>
   <li>Anomalies are leads to inspect, never accusations.</li></ul>
 </div></div>'''
json.dump(F, open(pathlib.Path(__file__).parent / "dashboard_fragments.json", "w"), indent=0)

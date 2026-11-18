// ---------- Q2 demand ----------
HOOKS.dynamic.push(() => {
  const D = OUT.floor_daily.slice(state.d0, state.d1 + 1), x = D.map(r => r.gaming_date.slice(0, 10));
  const isEv = D.map(r => !!r.events_active);
  plot("c-floor", [
    {type: "bar", name: "Normal day", x: x.filter((_, i) => !isEv[i]), y: D.filter((_, i) => !isEv[i]).map(r => r.coin_in), marker: {color: css("--s1")}, hovertemplate: "%{x}: $%{y:,.0f}<extra></extra>"},
    {type: "bar", name: "Event or promotion day", x: x.filter((_, i) => isEv[i]), y: D.filter((_, i) => isEv[i]).map(r => r.coin_in), marker: {color: css("--s2")},
      text: D.filter((_, i) => isEv[i]).map(r => r.events_active), hovertemplate: "%{x}: $%{y:,.0f}<br>%{text}<extra></extra>"},
  ], {barmode: "overlay", bargap: 0.1, yaxis: {title: "Floor coin-in per gaming day, $"}, xaxis: {title: "Gaming date"}, shapes: closureShape()});
  const Mo = OUT.monthly.filter(r => { const m = r.month; return m >= DAYS[state.d0].slice(0, 7) && m <= DAYS[state.d1].slice(0, 7); });
  table("t-monthly", [{h: "Month", k: "month"}, {h: "Coin-in / day", f: r => fmtK(r.coin_in_per_day), num: 1},
    {h: "Weekday-adjusted, no events", f: r => fmtK(r.coin_in_per_day_clean_weekday_adj), num: 1},
    {h: "Coin-in / avail. machine-h", f: r => fmt$(r.coin_in_per_avail_h, 0), num: 1}, {h: "Occupancy", f: r => pct(r.occupancy, 1), num: 1},
    {h: "Counter entries / day", f: r => num(r.traffic_entries_adj_per_day), num: 1},
    {h: "Context only: LV visitors / day (LVCVA)", f: r => num(r.CONTEXT_lvcva_visitors_per_day), num: 1}], Mo);
});
HOOKS.static.push(() => {
  const E = OUT.event_effects;
  plot("c-events", [{type: "bar", orientation: "h", y: E.map(r => r.name + (r.is_property_promotion ? " (our promotion)" : "")), x: E.map(r => 100 * r.coin_in_uplift),
    marker: {color: E.map(r => r.is_property_promotion ? css("--s2") : css("--s1"))},
    error_x: {type: "data", symmetric: false, array: E.map(r => 100 * (r.coin_in_uplift_ci95_hi - r.coin_in_uplift)), arrayminus: E.map(r => 100 * (r.coin_in_uplift - r.coin_in_uplift_ci95_lo)), color: css("--ink2")},
    text: E.map(r => "extra expected win " + fmtK(r.theo_incremental_usd)), textposition: "none", hovertemplate: "%{y}: %{x:.1f}% coin-in vs matched days<br>%{text}<extra></extra>"}],
    {xaxis: {title: "Coin-in uplift vs. same weekdays without events, % (95% CI)"}, yaxis: {automargin: true}, margin: {l: 290}, showlegend: false});
  const H = OUT.demand_summary.headline, P = OUT.demand_summary.pit_expansion_2025_03_24;
  $("#demand-answer").innerHTML = `<div class="answer"><b>Answer:</b> underlying demand was <b>flat</b> from January to May (weekday-adjusted, event-free coin-in about $1.35–1.39M/day), with June about 2.6% softer. Three things moved it. (1) <b>Events:</b> sports and festival weekends added 15–23% (Super Bowl +23%), conventions added almost nothing (0–4%), and our two promotions added +18% (slot tournament, ${fmtK(H.promo_TOURN_incremental_theo)} extra expected win) and +5.6% (Locals Week, ${fmtK(H.promo_LOCALS_incremental_theo)}). (2) <b>The Back Forty walkway closure</b> (Mar 3–Apr 7) cut Back Forty traffic ${pct(-H.b40_closure_traffic_effect, 0)} and its coin-in per machine-hour ${pct(-H.b40_closure_cpah_effect, 0)} (95% CI ${pct(-H.b40_closure_cpah_ci95[1], 0)}–${pct(-H.b40_closure_cpah_ci95[0], 0)}), about ${fmtK(H.b40_closure_lost_theo)} of expected win, before coin-in per machine-hour recovered to about ${pct(OUT.plan.recovery_cpah, 0)} of its pre-closure level (traffic about ${pct(OUT.plan.recovery_traffic, 0)}). (3) <b>Eight machines added to Center Pit</b> on Mar 24 raised the zone's coin-in about ${pct(P.pit_total_did, 0)} without taking play from its existing machines. LVCVA city visitor numbers are shown for context only: they cover all of Las Vegas and were published after 2025.</div>`;
  const C = OUT.closure.coin_in_per_avail_h.all_ex_B40_HL.ratio_of_sums;
  $("#location-answer").innerHTML = `<div class="answer"><b>Answer: location is the main reason the bottom-10 earn little.</b> At the <i>same game title</i>, a Back Forty machine expects to win 43% less per available hour than one in Center Pit (CI 41–44%). Even before the closure, Back Forty coin-in per machine-hour was ${pct(-C.structural_gap_pre, 0)} below the rest of the floor. Guests spend the same once seated (≈$485 per occupied hour), but far fewer sit down there (occupancy 0.28 vs. 0.42). The natural experiment: <b>M-1152</b> moved from the Back Forty to the Main Entrance on Feb 4 and its coin-in per hour <b>doubled</b> ($119 → $245; +106% vs. the floor, 95% CI +82% to +136%). The other three moves tracked their new zones too. Replacing a Back Forty machine with a new one would leave it in the same quiet spot.</div>`;
  const mv = OUT.moves;
  table("t-moves", [{h: "Machine", k: "machine_id"}, {h: "Moved (gaming day)", k: "move_gaming_date"}, {h: "From → to zone", f: r => ZN[r.old_zone] + " → " + ZN[r.new_zone]},
    {h: "Coin-in / avail h before", f: r => fmt$(r.cpah_before, 0), num: 1}, {h: "After", f: r => fmt$(r.cpah_after, 0), num: 1},
    {h: "Change vs. floor (95% CI)", f: r => `${(100 * r.floor_relative_change).toFixed(0)}% (${(100 * r.floor_relative_change_ci95_lo).toFixed(0)} to ${(100 * r.floor_relative_change_ci95_hi).toFixed(0)}%)`, num: 1}], mv,
    {rowClass: r => r.machine_id === "M-1152" ? "hl" : ""});
});

// ---------- Q6 plan ----------
HOOKS.static.push(() => {
  const P = OUT.plan;
  const k = [["Budget spent", fmt$(P.spent), "3 action types"], ["Kept", fmt$(P.kept), "luck needs no spending"],
    ["Expected benefit in July (theo)", fmtK(P.total_benefit_central), "range " + fmtK(P.total_benefit_low) + " – " + fmtK(P.total_benefit_high)],
    ["Expected net in July", fmtK(P.total_benefit_central - P.spent), "low case " + fmtK(P.total_benefit_low - P.spent) + " · swaps keep earning after July"]];
  $("#plan-kpis").innerHTML = k.map(x => `<div class="kpi"><div class="l">${x[0]}</div><div class="v">${x[1]}</div><div class="f">${x[2]}</div></div>`).join("");
  const ids = {"A-INSPECT": "plan-inspect", "A-MOVE-MACHINE": "plan-move", "A-PROMO": "plan-promo"};
  $("#plan-cards").innerHTML = P.actions.map((a, i) => `<div class="answer ${i === 0 ? "crit" : i === 1 ? "" : "warn"}" id="${ids[a.action]}">
    <h3 style="margin-top:0">${i + 1}. ${a.name} <span class="flag" title="catalogue code">${a.action}</span></h3>
    <div class="kpis" style="margin:6px 0">
      <div class="kpi"><div class="l">Cost</div><div class="v">${fmt$(a.cost)}</div><div class="f">${a.quantity} × ${fmt$(a.unit_cost_usd)}${a.recurring_usd_per_day ? " + " + fmt$(a.recurring_usd_per_day) + "/day" : ""} · lead ${a.lead_days} d · ${a.effective_days} effective days</div></div>
      <div class="kpi"><div class="l">Expected benefit (central)</div><div class="v">${fmtK(a.benefit_central)}</div><div class="f">sensitivity ${fmtK(a.benefit_low)} – ${fmtK(a.benefit_high)}</div></div>
      <div class="kpi"><div class="l">Net in July (central)</div><div class="v">${fmtK(a.net_central)}</div><div class="f">${a.gain_per_day_after_july ? "then +" + fmtK(a.gain_per_day_after_july) + "/day from August at no extra cost" : a.downtime_minutes ? a.downtime_minutes + " min downtime per unit" : "no machine downtime"}</div></div>
    </div>
    <p class="caption"><b>Cause it fixes:</b> ${a.cause}.</p>
    <p class="caption"><b>Targets:</b> ${a.targets.length > 3 ? `<details><summary>${a.targets.length} targets (show)</summary>${a.targets.join("<br>")}</details>` : a.targets.join("; ")}</p>
    ${a.queued_for_august && a.queued_for_august.length ? `<p class="caption"><b>Queued for August, not funded:</b> <details><summary>${a.queued_for_august.length} more swaps worth +${fmtK(a.queued_gain_per_day)}/day together, which don't pay back inside July (show)</summary>${a.queued_for_august.join("<br>")}</details></p>` : ""}
    <p class="caption"><b>Assumptions:</b> ${a.assumptions}</p>
    <p class="caption"><b>How we'll know it worked:</b> ${a.measurement}</p>
    <p class="evid"><b>Evidence →</b> <a href="${a.evidence}">${{"#s-leak": "Section 5: leak tests (M-1158 hit rate & hold)", "#s-location": "Section 3: zone effects, closure and the M-1152 move", "#s-demand": "Q2: event and promotion uplifts"}[a.evidence]}</a>${a.action === "A-MOVE-MACHINE" ? ` · <a href="#s-true">Section 6: weak titles in strong zones</a>` : ""}${a.action === "A-PROMO" ? ` · <a href="#s-model">Model: forecast used to measure it</a>` : ""}</p></div>`).join("");
  table("t-rejected", [{h: "Not funded", f: r => r.action + (r.code ? ` <span class="note">${r.code}</span>` : "")}, {h: "Would cost", f: r => fmt$(r.cost), num: 1}, {h: "Why not (from our data)", k: "why"}, {h: "Evidence", f: r => `<a href="${r.evidence}">see chart</a>`}], P.rejected);
  $("#plan-answer").innerHTML = `<div class="answer"><b>Why keep ${fmt$(P.kept)}:</b> most of the manager's shortfall is luck and location, not broken machines. Luck needs no fix. Location is addressed by the swaps, but only the 3 strongest pay back inside July, so the other 9 wait for August. The other catalogue items don't pay back within the 30-day window on our data (table below). We also declined a second tournament weekend: we have measured only one, and a repeat a week later could just pull play forward. Recommended order of decisions: do the inspection on day 1; book the swaps for July 3 (2-day lead) and avoid the promotion weekend; launch the tournament weekend July 18–20.</div>`;
});

// ---------- Q3 downtime + section 4 availability ----------
HOOKS.static.push(() => {
  const S = OUT.down_summary;
  const k = [["Machine-hours down", num(S.total_downtime_machine_h), pct(S.downtime_share_of_scheduled, 2) + " of scheduled hours"],
    ["Expected win lost", fmtK(S.lost_theo_usd.hourly_method_B_headline), pct(S.lost_theo_usd.share_of_floor_theo_B, 2) + " of floor theo (gross)"],
    ["Net of players moving to neighbours", fmtK(S.lost_theo_usd.hourly_method_B_headline * (1 - S.displacement_test.share_of_lost_coin_in_recovered_by_bank_neighbours)),
      pct(S.displacement_test.share_of_lost_coin_in_recovered_by_bank_neighbours, 0) + " recovered (95% CI " + pct(S.displacement_test.ci95[0], 0) + "–" + pct(S.displacement_test.ci95[1], 0) + ")"],
    ["Manager's bottom 10 availability", pct(S.availability_explains_weak_machines.manager_bottom10_availability, 2), "fleet " + pct(S.availability_explains_weak_machines.fleet_availability, 2)]];
  $("#avail-kpis").innerHTML = k.map(x => `<div class="kpi"><div class="l">${x[0]}</div><div class="v">${x[1]}</div><div class="f">${x[2]}</div></div>`).join("");
  $("#avail-answer").innerHTML = `<div class="answer"><b>Answer: availability does not explain the weak machines.</b> The manager's bottom 10 were up ${pct(S.availability_explains_weak_machines.manager_bottom10_availability, 1)} of the time, slightly <i>more</i> than the fleet. For the bottom tenth of machines, downtime explains only ${pct(S.availability_explains_weak_machines.bottom_decile_theo_per_day_share_explained_by_downtime, 1)} of their shortfall. The real downtime story is about the manufacturer and staffing (<a href="#s-downtime">Q3</a>).</div>`;
  const M = OUT.mfr;
  plot("c-mfr", [{type: "bar", x: M.map(r => r.manufacturer), y: M.map(r => r.faults_per_machine_30d), marker: {color: M.map(r => r.manufacturer === "Ridgeline" ? css("--s2") : css("--s1"))},
    error_y: {type: "data", symmetric: false, array: M.map(r => r.faults_per_machine_30d_hi95_poisson - r.faults_per_machine_30d), arrayminus: M.map(r => r.faults_per_machine_30d - r.faults_per_machine_30d_lo95_poisson), color: css("--ink2")},
    text: M.map(r => `${r.machines} machines · repair ${r.mttr_h.toFixed(1)} h`), textposition: "none", hovertemplate: "%{x}: %{y:.2f} faults per machine per 30 days<br>%{text}<extra></extra>"}],
    {yaxis: {title: "Faults per machine per 30 days"}, xaxis: {title: "Manufacturer (fictional)"}, showlegend: false});
  const st = OUT.staff;
  const lab = st.map(r => r.shift + (r.staffed_short ? " · short-staffed" : " · fully staffed"));
  plot("c-staff", [{type: "bar", orientation: "h", y: lab, x: st.map(r => r.mean_wait_h), marker: {color: st.map(r => r.staffed_short ? css("--s2") : css("--s1"))},
    error_x: {type: "data", symmetric: false, array: st.map(r => r.wait_hi95 - r.mean_wait_h), arrayminus: st.map(r => r.mean_wait_h - r.wait_lo95), color: css("--ink2")},
    text: st.map(r => r.n_faults + " faults"), textposition: "none", hovertemplate: "%{y}: %{x:.2f} h average wait (%{text})<extra></extra>"}],
    {xaxis: {title: "Average hours from fault to technician starting work"}, yaxis: {automargin: true}, margin: {l: 170}, showlegend: false});
  const mf = M.find(r => r.manufacturer === "Ridgeline"), ot = M.filter(r => r.manufacturer !== "Ridgeline");
  const sh = st.reduce((o, r) => (o[r.shift + (r.staffed_short ? "S" : "F")] = r, o), {});
  $("#down-answer").innerHTML = `<div class="answer"><b>Answer (full period Jan–Jun; the daily chart above follows the date filter):</b> ${num(S.total_downtime_machine_h)} machine-hours were lost (${pct(S.downtime_share_of_scheduled, 2)} of scheduled time), worth about ${fmtK(S.lost_theo_usd.hourly_method_B_headline)} of expected win, and ${pct(S.downtime_by_cause_h.fault_repair / S.total_downtime_machine_h, 0)} of it was fault repair. <b>When:</b> May was worst (the week of May 19 lost ${num(S.worst_full_week.downtime_h)} h vs. a typical ${num(S.worst_full_week.median_week_h)} h), along with weekends and 10 PM–1 AM. <b>Why:</b> (1) <b>Ridgeline</b> machines fault ${mf.faults_per_machine_30d.toFixed(2)} times per machine per 30 days, ${mf.rate_ratio_vs_others.toFixed(1)}× the others, with the same gap per hour of play, so heavier use doesn't explain it. Repair time itself is normal. (2) When a shift is <b>short a technician</b>, the wait before work starts rises from ${sh.dayF ? sh.dayF.mean_wait_h.toFixed(1) : "?"} h to ${sh.dayS ? sh.dayS.mean_wait_h.toFixed(1) : "?"} h on days and from ${sh.swingF ? sh.swingF.mean_wait_h.toFixed(1) : "?"} h to ${sh.swingS ? sh.swingS.mean_wait_h.toFixed(1) : "?"} h on swing. That is more than triple the wait, confirmed within the same weeks; repair work time itself is unchanged. Even so, the total at stake is small: an extra technician recovers only about $1–2k of expected win in July, far less than its $11,520 cost (<a href="#s-plan">plan</a>).</div>`;
});
HOOKS.dynamic.push(() => {
  const D = OUT.down_daily.slice(state.d0, state.d1 + 1), x = D.map(r => r.gaming_date.slice(0, 10));
  plot("c-down", [
    {type: "bar", name: "Fault repair", x, y: D.map(r => r.down_fault_h), marker: {color: css("--s1")}},
    {type: "bar", name: "Maintenance", x, y: D.map(r => r.down_maint_h), marker: {color: css("--s3")}},
    {type: "bar", name: "Move / install / retire", x, y: D.map(r => r.down_move_install_h), marker: {color: css("--s4")}},
  ], {barmode: "stack", bargap: 0.15, yaxis: {title: "Machine-hours down per gaming day"}, xaxis: {title: "Gaming date"}, hovermode: "x unified"});
});

// ---------- Q4 players ----------
HOOKS.static.push(() => {
  const T = OUT.player_totals, t = T.totals_usd, C = T.concentration, Dd = T.distribution;
  const k = [["Tracked coin-in", fmtK(t.tracked_coin_in), "carded play only"], ["Paid back to players", fmtK(t.tracked_payout), pct(t.tracked_payout / t.tracked_coin_in) + " of wagers"],
    ["Players' net result", fmtK(t.tracked_net_player_view), "theo " + fmtK(t.theo) + " · luck " + fmtK(t.luck_residual_player_view)],
    ["Players who ended ahead", pct(Dd.share_ended_ahead), "of " + num(Dd.players_with_carded_gaming) + " carded players who gambled"],
    ["Top 1% / 10% share of coin-in", pct(C.coin_in.top_1pct_share, 0) + " / " + pct(C.coin_in.top_10pct_share, 0), "Gini " + C.coin_in.gini.toFixed(2)],
    ["Median player result", fmt$(Dd.net_percentiles_usd.p50), "1st pct " + fmt$(Dd.net_percentiles_usd.p1) + " · 99th " + fmt$(Dd.net_percentiles_usd.p99)]];
  $("#pl-kpis").innerHTML = k.map(x => `<div class="kpi"><div class="l">${x[0]}</div><div class="v">${x[1]}</div><div class="f">${x[2]}</div></div>`).join("");
  const L = OUT.lorenz;
  plot("c-lorenz", [
    {type: "scatter", mode: "lines", name: "Coin-in", x: L.map(r => 100 * r.pop_share), y: L.map(r => 100 * r.cum_share_coin_in), line: {color: css("--s1"), width: 2}},
    {type: "scatter", mode: "lines", name: "Net loss (players who lost)", x: L.map(r => 100 * r.pop_share), y: L.map(r => 100 * r.cum_share_net_loss_among_losers), line: {color: css("--s2"), width: 2}},
    {type: "scatter", mode: "lines", name: "Perfect equality", x: [0, 100], y: [0, 100], line: {color: css("--neutral"), dash: "dot", width: 1.5}}],
    {xaxis: {title: "Players, smallest to largest, cumulative %"}, yaxis: {title: "Cumulative share, %"}, hovermode: "x unified"});
  const H = OUT.nethist.filter(r => r.players > 0);
  plot("c-nethist", [{type: "bar", x: H.map(r => r.bin), y: H.map(r => r.players), marker: {color: H.map(r => (r.hi != null && r.hi <= 0) ? css("--s2") : css("--s1"))},
    hovertemplate: "%{x}: %{y:,} players<extra></extra>"}], {xaxis: {title: "Six-month net result per player (payout − coin-in), $", type: "category", tickangle: -50}, yaxis: {title: "Players"}, showlegend: false, margin: {b: 140}});
  const top = C.top_1pct_of_all_players_by_loss;
  $("#pl-answer").innerHTML = `<div class="answer"><b>Answer:</b> Tracked players wagered ${fmtK(t.tracked_coin_in)}, got back ${fmtK(t.tracked_payout)} and lost ${fmtK(-t.tracked_net_player_view)} in total, almost exactly the expected ${fmtK(t.theo)}, so as a group they were not unlucky. ${pct(Dd.share_ended_ahead, 0)} ended ahead. Results are concentrated: the top 1% of players placed ${pct(C.coin_in.top_1pct_share, 0)} of wagers, and the ${num(top.players)} biggest losers account for ${pct(top.share_of_total_tracked_net_loss, 0)} of all tracked net loss (${fmtK(top.their_net_usd)}). ${fmtK(top.their_theo_usd)} of that was expected from how much they played, so it is volume, not bad luck. We propose nothing that encourages these guests to play more. Monitoring them is a responsible-gaming task, not a marketing target. Use the guest-group filter above to drill into a tier, region or age band.</div>`;
});
HOOKS.guest = () => {
  const [gt, g] = state.group.split("|");
  const months = [...new Set(DAYS.slice(state.d0, state.d1 + 1).map(d => d.slice(0, 7)))];
  const rows = OUT.group_monthly.filter(r => r.group_type === gt && r.group === g && months.includes(r.month));
  const allRows = gt === "all" ? OUT.group_monthly.filter(r => r.group_type === "opening_tier" && months.includes(r.month)) : rows;
  const src = gt === "all" ? Object.values(allRows.reduce((o, r) => { const m = o[r.month] || (o[r.month] = {month: r.month, coin_in: 0, net: 0, theo: 0, comp_cost: 0, players: 0, gaming_players: 0, visits: 0}); ["coin_in", "net", "theo", "comp_cost", "players", "gaming_players", "visits"].forEach(k => m[k] += r[k]); return o; }, {})) : rows;
  src.sort((a, b) => a.month < b.month ? -1 : 1);
  $("#guest-title").textContent = gt === "all" ? "all tracked players" : `${gt.replace("_", " ")} = ${g}`;
  plot("c-guest", [
    {type: "bar", name: "Expected loss (theo)", x: src.map(r => r.month), y: src.map(r => r.theo), marker: {color: css("--s1")}},
    {type: "bar", name: "Actual net loss", x: src.map(r => r.month), y: src.map(r => -r.net), marker: {color: css("--s2")}},
  ], {barmode: "group", yaxis: {title: "Players' loss to the house, $"}, xaxis: {title: "Month (in selected date range)"}});
  const s = src.reduce((o, r) => { ["coin_in", "net", "theo", "comp_cost", "visits"].forEach(k => o[k] = (o[k] || 0) + r[k]); return o; }, {});
  const G = OUT.groups.find(r => r.group_type === gt && r.group === g) || {};
  table("t-guest", [{h: "Measure", k: "a"}, {h: "Value", k: "b", num: 1}], [
    {a: "Carded players who gambled (full Jan–Jun)", b: num(G.gaming_players || 0)}, {a: "Players who ended ahead (full Jan–Jun)", b: G.share_ahead != null ? pct(G.share_ahead) : "–"},
    {a: "Coin-in (selected months)", b: fmtK(s.coin_in || 0)}, {a: "Net result, player view (selected months)", b: fmtK(s.net || 0)},
    {a: "Theo, expected loss (selected months)", b: fmtK(s.theo || 0)}, {a: "Luck, net + theo (selected months)", b: fmtK((s.net || 0) + (s.theo || 0))},
    {a: "Comp cost (selected months)", b: fmtK(s.comp_cost || 0)}, {a: "Comp cost ÷ theo, reinvestment (selected months)", b: s.theo ? pct(s.comp_cost / s.theo, 2) : "–"},
    {a: "Share of all tracked coin-in (full Jan–Jun)", b: G.share_of_tracked_coin_in != null ? pct(G.share_of_tracked_coin_in) : "–"}]);
};

// ---------- Q5 comps ----------
HOOKS.static.push(() => {
  const S = OUT.comp_summary.totals, C = OUT.comp_change;
  const k = [["Comp cost (economic, counted once)", fmtK(S.comp_economic_cost_usd), num(S.comps_redeemed) + " comps redeemed"],
    ["Face value (not revenue)", fmtK(S.comp_face_value_usd_not_revenue), "cost is " + pct(S.econ_cost_per_face_dollar, 0) + " of face value"],
    ["Reinvestment, Jan–Apr → May–Jun", pct(S.reinvestment_pre_JanApr, 2) + " → " + pct(S.reinvestment_post_MayJun, 2), "comp cost ÷ carded theo"],
    ["Points per $1 face value", C.magnitude.blended_points_per_face_usd_pre.toFixed(1) + " → " + C.magnitude.blended_points_per_face_usd_post.toFixed(1), "changed on gaming day " + C.effective.gaming_date]];
  $("#comp-kpis").innerHTML = k.map(x => `<div class="kpi"><div class="l">${x[0]}</div><div class="v">${x[1]}</div><div class="f">${x[2]}</div></div>`).join("");
  $("#comp-answer").innerHTML = `<div class="answer warn"><b>Answer:</b> the comp program cost ${fmtK(S.comp_economic_cost_usd)} over six months, just ${pct(S.reinvestment_6m, 1)} of carded theo. <b>What changed:</b> at the 6 AM start of gaming day <b>May 1</b>, the points a guest needs per comp dollar were cut by about 53% for every tier (Base 15→7, Gold 12→5.6, Platinum 9→4.2), so comps became twice as cheap to redeem. Confirmed two ways: a change-point scan of the daily rate, and the same players redeeming the same benefit before vs. after (0.47× the points). The earn rate (≈1 point per $1 wagered) and benefit costs did not change. <b>Did it matter?</b> No measurable effect on cost or play yet (reinvestment drifted from ${pct(S.reinvestment_pre_JanApr, 2)} to ${pct(S.reinvestment_post_MayJun, 2)}, but the daily-cost test below cannot tell that from noise): daily comp cost +2.1% (95% CI −3.8% to +7.9%) and carded play share +2.4% (CI −0.9% to +5.6%). The change cost little and gained nothing we can detect. Undoing it would save under $1k a month, less than the $1,500 change fee, so we don't fund a comp change. A pilot is the only way to learn the true effect, but it doesn't trace to why machines earn less.</div>`;
});
HOOKS.dynamic.push(() => {
  const D = OUT.comp_daily.slice(state.d0, state.d1 + 1), x = D.map(r => r.gaming_date.slice(0, 10));
  const cut = [{type: "line", xref: "x", yref: "paper", x0: "2025-05-01", x1: "2025-05-01", y0: 0, y1: 1, line: {color: css("--ink2"), dash: "dash", width: 1.5}}];
  const ann = [{x: "2025-05-01", y: 1, yref: "paper", text: "May 1: redemption price cut", showarrow: false, xanchor: "left", font: {size: 11}}];
  plot("c-comprate", [{type: "scatter", mode: "lines", name: "Points per $1 face value", x, y: D.map(r => r.points_per_face_usd), line: {color: css("--s1"), width: 2}}],
    {yaxis: {title: "Points redeemed per $1 of comp face value", rangemode: "tozero"}, xaxis: {title: "Gaming date"}, shapes: cut, annotations: ann, showlegend: false});
  plot("c-compcost", [{type: "bar", name: "Comp economic cost, $/day", x, y: D.map(r => r.comp_econ_cost), marker: {color: css("--s3")}}],
    {yaxis: {title: "Comp economic cost per gaming day, $", rangemode: "tozero"}, xaxis: {title: "Gaming date"}, shapes: cut, annotations: ann, showlegend: false});
});

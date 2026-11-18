// ---------- helpers ----------
const $ = s => document.querySelector(s);
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fmt$ = (v, d = 0) => (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString(undefined, {maximumFractionDigits: d, minimumFractionDigits: d});
const fmtK = v => { const a = Math.abs(v), s = v < 0 ? "−$" : "$"; return a >= 1e6 ? s + (a / 1e6).toFixed(2) + "M" : a >= 1e3 ? s + (a / 1e3).toFixed(1) + "k" : s + a.toFixed(0); };
const pct = (v, d = 1) => (100 * v).toFixed(d) + "%";
const num = (v, d = 0) => v.toLocaleString(undefined, {maximumFractionDigits: d, minimumFractionDigits: d});
const F = FACT.fact, N = F.m.length, DAYS = FACT.days, META = FACT.machine_meta, NM = FACT.machines.length;
const ZN = FACT.zone_names;
const ZCOL = {"Z-PIT": "--s1", "Z-ENT": "--s2", "Z-BAR": "--s3", "Z-HTL": "--s4", "Z-GAR": "--s5", "Z-HL": "--s7", "Z-B40": "--s8"};
const B10 = OUT.bottom10.map(r => r.machine_id);
const state = {d0: 0, d1: DAYS.length - 1, bank: "", group: "all|All"};
const PLOT_CFG = {displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d"]};
function baseLayout(extra = {}) {
  const ink = css("--ink2"), line = css("--line");
  const l = {paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: {family: "system-ui, sans-serif", size: 12, color: ink},
    margin: {l: 64, r: 16, t: 28, b: 52}, hovermode: "closest", legend: {orientation: "h", y: -0.2},
    xaxis: {gridcolor: line, zerolinecolor: line, linecolor: line}, yaxis: {gridcolor: line, zerolinecolor: line, linecolor: line}};
  for (const k in extra) l[k] = (typeof extra[k] === "object" && !Array.isArray(extra[k]) && l[k]) ? Object.assign({}, l[k], extra[k]) : extra[k];
  for (const ax of ["xaxis", "yaxis"]) if (l[ax] && typeof l[ax].title === "string") l[ax].title = {text: l[ax].title, standoff: 8};
  if (l.xaxis && l.xaxis.title && /date/i.test(l.xaxis.title.text || "") && !l.xaxis.tickformat) l.xaxis.tickformat = "%b %d";
  return l;
}
function chartLabel(el, layout) { // text alternative for screen readers: heading + axes + the answer box nearby
  const sec = el.closest("section"), h = el.parentElement && el.parentElement.querySelector("h3");
  const ax = t => (t && (t.title && (t.title.text || t.title))) || "";
  return `Chart: ${h ? h.textContent : (sec ? sec.querySelector("h2").textContent : "")}. Vertical axis: ${ax(layout.yaxis)}. Horizontal axis: ${ax(layout.xaxis)}. The written answer below the chart summarises what it shows.`;
}
function plot(id, traces, layout) { const el = document.getElementById(id); if (!el) return; const L = baseLayout(layout);
  el.setAttribute("role", "img"); el.setAttribute("aria-label", chartLabel(el, L)); Plotly.react(el, traces, L, PLOT_CFG); }
function table(id, cols, rows, opts = {}) {
  const el = document.getElementById(id); if (!el) return;
  let h = "<thead><tr>" + cols.map(c => `<th class="${c.num ? "num" : ""}" title="${c.tip || ""}">${c.h}</th>`).join("") + "</tr></thead><tbody>";
  for (const r of rows) h += `<tr class="${opts.rowClass ? opts.rowClass(r) : ""}">` + cols.map(c => `<td class="${c.num ? "num" : ""}">${c.f ? c.f(r) : r[c.k]}</td>`).join("") + "</tr>";
  el.innerHTML = h + "</tbody>";
}
function verdictPill(v) {
  const cls = v.startsWith("Payout") ? "lead" : v.startsWith("Weak") ? "true" : v === "Location" ? "loc" : v.startsWith("Short") ? "short" : v.startsWith("Unlucky") ? "luck" : "";
  return `<span class="pill ${cls}">${v}</span>`;
}
function rolling(num, den, w = 7) { const out = []; for (let i = 0; i < num.length; i++) { let a = 0, b = 0; for (let j = Math.max(0, i - w + 1); j <= i; j++) { a += num[j]; b += den[j]; } out.push(b > 0 ? a / b : null); } return out; }
function inRange(i) { return F.d[i] >= state.d0 && F.d[i] <= state.d1; }
function phiInv(p) { return p; }

// ---------- aggregation (ratio of sums, variance adds) ----------
function aggMachines() {
  const A = Array.from({length: NM}, () => ({ci: 0, hw: 0, th: 0, v: 0, ah: 0, sh: 0, oh: 0, sp: 0, ps: 0, days: 0}));
  for (let i = 0; i < N; i++) { if (!inRange(i)) continue; const a = A[F.m[i]];
    a.ci += F.ci[i]; a.hw += F.hw[i]; a.th += F.th[i]; a.v += F.var[i]; a.ah += F.ah[i]; a.sh += F.sh[i]; a.oh += F.oh[i]; a.sp += F.sp[i]; a.ps += F.ps[i]; a.days++; }
  const zone = {}; let fT = 0, fH = 0;
  A.forEach((a, k) => { const z = META[k].zone_id; zone[z] = zone[z] || {th: 0, ah: 0, ci: 0}; zone[z].th += a.th; zone[z].ah += a.ah; zone[z].ci += a.ci; fT += a.th; fH += a.ah; });
  const floorTH = fT / fH;
  A.forEach((a, k) => {
    const m = META[k]; Object.assign(a, {id: FACT.machines[k], meta: m});
    if (!a.days) return;
    a.luck = a.hw - a.th; a.sd = Math.sqrt(a.v); a.z = a.luck / a.sd; a.hold = a.hw / a.ci; a.theo_hold = a.th / a.ci;
    a.avail = a.ah / a.sh; a.thh = a.th / a.ah; a.cih = a.ci / a.ah;
    const Z = zone[m.zone_id]; a.peer_thh = (Z.th - a.th) / (Z.ah - a.ah); a.peer_cih = (Z.ci - a.ci) / (Z.ah - a.ah);
    a.thh_vs_zone = a.thh / a.peer_thh; a.cih_vs_zone = a.cih / a.peer_cih; a.zone_vs_floor = (Z.th / Z.ah) / floorTH;
    a.hit_z = (a.ps - a.sp * m.p) / Math.sqrt(a.sp * m.p * (1 - m.p));
    a.gain = (a.peer_thh - a.thh) * a.ah / a.days; a.down_cost = (a.sh - a.ah) * a.thh;
    a.verdict = a.z < -3 || a.hit_z > 4 ? "Payout lead: inspect" : a.days < 90 ? "Short window"
      : (a.avail < 0.985 && a.down_cost > 0.05 * a.th) ? "Availability"
      : (a.thh_vs_zone < 0.75 && a.cih_vs_zone <= 1.0) ? "Weak game for its spot"
      : a.zone_vs_floor < 0.8 ? "Location" : a.luck < 0 ? "Unlucky (within noise)" : "OK";
  });
  return A.filter(a => a.days > 0);
}
function aggDaily(filterFn) { // returns per-day sums for rows passing filterFn
  const n = DAYS.length, o = {ci: new Array(n).fill(0), th: new Array(n).fill(0), hw: new Array(n).fill(0), ah: new Array(n).fill(0), sh: new Array(n).fill(0)};
  for (let i = 0; i < N; i++) { if (filterFn && !filterFn(i)) continue; const d = F.d[i]; o.ci[d] += F.ci[i]; o.th[d] += F.th[i]; o.hw[d] += F.hw[i]; o.ah[d] += F.ah[i]; o.sh[d] += F.sh[i]; }
  return o;
}
const sliceR = arr => arr.slice(state.d0, state.d1 + 1);
const daysR = () => DAYS.slice(state.d0, state.d1 + 1);
const closureShape = () => [{type: "rect", xref: "x", yref: "paper", x0: "2025-03-03", x1: "2025-04-07", y0: 0, y1: 1, fillcolor: css("--neutral"), opacity: 0.18, line: {width: 0}, layer: "below"}];

// ---------- renderers ----------
function renderSummary(M) {
  let ci = 0, hw = 0, th = 0, ah = 0, sh = 0, v = 0;
  M.forEach(a => { ci += a.ci; hw += a.hw; th += a.th; ah += a.ah; sh += a.sh; v += a.v; });
  const nd = state.d1 - state.d0 + 1, sd = Math.sqrt(v);
  const k = [
    ["Coin-in (all play)", fmtK(ci), fmtK(ci / nd) + " per gaming day"],
    ["House win", fmtK(hw), "hold " + pct(hw / ci, 2) + " (ratio of sums)"],
    ["Expected win (theo)", fmtK(th), "theo hold " + pct(th / ci, 2)],
    ["Luck (house win − theo)", fmtK(hw - th), "95% range ±" + fmtK(1.96 * sd) + " → " + (Math.abs(hw - th) < 1.96 * sd ? "normal luck" : "unusual")],
    ["Machine availability", pct(ah / sh, 2), num(sh - ah, 0) + " machine-hours down"],
    ["Machines on floor", num(M.length), nd + " gaming days selected"],
  ];
  $("#kpis").innerHTML = k.map(x => `<div class="kpi"><div class="l">${x[0]}</div><div class="v">${x[1]}</div><div class="f">${x[2]}</div></div>`).join("");
}
function renderBottom10(M) {
  const rows = [...M].sort((a, b) => a.hw - b.hw).slice(0, 10);
  table("t-bottom10", [
    {h: "#", f: (r) => rows.indexOf(r) + 1, num: 1},
    {h: "Machine", f: r => `<b>${r.id}</b>`}, {h: "Title (RTP)", f: r => `${r.meta.theme} (${pct(r.meta.rtp, 0)})`},
    {h: "Zone · bank", f: r => `${ZN[r.meta.zone_id]} · ${r.meta.bank_id}`},
    {h: "Days on floor", k: "days", num: 1}, {h: "Availability", f: r => pct(r.avail, 1), num: 1},
    {h: "House win", f: r => fmt$(r.hw), num: 1}, {h: "Theo", f: r => fmt$(r.th), num: 1},
    {h: "Luck (95% range)", f: r => `${fmt$(r.luck)} <span class="note">±${fmt$(1.96 * r.sd)}</span>`, num: 1},
    {h: "Luck z", f: r => r.z.toFixed(2), num: 1, tip: "house win minus theo in standard deviations of the game's volatility"},
    {h: "Theo/avail h vs zone peers", f: r => pct(r.thh_vs_zone, 0), num: 1},
    {h: "Verdict", f: r => verdictPill(r.verdict)},
  ], rows, {rowClass: r => r.verdict.startsWith("Payout") ? "hl" : ""});
  const cnt = {}; rows.forEach(r => cnt[r.verdict] = (cnt[r.verdict] || 0) + 1);
  const b40 = rows.filter(r => r.meta.zone_id === "Z-B40").length, rd = rows.filter(r => r.meta.rtp >= 0.92).length;
  $("#bottom10-answer").innerHTML = `<div class="answer"><b>What the list really shows (selected dates):</b> ${b40} of 10 sit in the Back Forty, the weakest zone. ${rd} of 10 run a loose 92–95% payout game, so they are <i>designed</i> to keep less of each dollar. Verdicts: ${Object.entries(cnt).map(([k, v]) => v + " × " + k).join(", ")}. The ones marked "Weak game for its spot" are loose titles that guests play about as much as their neighbours; the cure is moving the game, not replacing the machine, and the biggest such gaps are in strong zones, not on this list (<a href="#s-true">section 6</a>).</div>`;
}
function renderLuck(M) {
  const sel = state.bank;
  const groups = [
    {name: "Other machines", f: a => !B10.includes(a.id) && !a.verdict.startsWith("Payout"), color: css("--s1"), size: 7},
    {name: "Manager's bottom 10", f: a => B10.includes(a.id) && !a.verdict.startsWith("Payout"), color: css("--s2"), size: 11},
    {name: "Payout lead (inspect)", f: a => a.verdict.startsWith("Payout"), color: css("--crit"), size: 13},
  ];
  const tr = groups.map(g => { const pts = M.filter(g.f); return {type: "scatter", mode: "markers", name: g.name, x: pts.map(a => a.th), y: pts.map(a => a.z),
    text: pts.map(a => `<b>${a.id}</b> ${a.meta.theme} (${a.meta.volatility})<br>${ZN[a.meta.zone_id]} · ${a.meta.bank_id}<br>theo ${fmt$(a.th)} · house win ${fmt$(a.hw)}<br>luck ${fmt$(a.luck)} (z ${a.z.toFixed(2)})`),
    hovertemplate: "%{text}<extra></extra>",
    marker: {color: g.color, size: g.size, opacity: 0.85, line: {color: pts.map(a => a.meta.bank_id === sel ? css("--ink") : css("--card")), width: pts.map(a => a.meta.bank_id === sel ? 2.5 : 1)}}}; });
  const xs = M.map(a => a.th), x0 = Math.min(...xs) * 0.9, x1 = Math.max(...xs) * 1.1;
  const hl = (y, dash, name) => ({type: "scatter", mode: "lines", x: [x0, x1], y: [y, y], line: {color: css("--neutral"), width: 1.5, dash}, name, hoverinfo: "skip", showlegend: !!name});
  tr.push(hl(1.96, "dash", "±1.96 (95% of luck)"), hl(-1.96, "dash"), hl(3, "dot", "±3 (red flag)"), hl(-3, "dot"));
  const lead = M.find(a => a.verdict.startsWith("Payout"));
  plot("c-luck", tr, {xaxis: {title: "Expected win (theo) in selected period, $ (log scale)", type: "log"}, yaxis: {title: "Luck z = (house win − theo) ÷ SD"},
    annotations: lead ? [{x: Math.log10(lead.th), y: lead.z, text: lead.id + ": hold " + pct(lead.hold, 1) + " vs " + pct(lead.theo_hold, 0) + " expected", showarrow: true, arrowhead: 0, ax: 60, ay: -10, font: {size: 12}}] : []});
  const out = M.filter(a => Math.abs(a.z) > 1.96).length, out3 = M.filter(a => Math.abs(a.z) > 3).map(a => a.id);
  const b10in = M.filter(a => B10.includes(a.id)), b10ok = b10in.filter(a => Math.abs(a.z) <= 1.96).length;
  $("#luck-answer").innerHTML = `<div class="answer"><b>Answer:</b> ${out} of ${M.length} machines are outside ±1.96 (≈${Math.round(0.05 * M.length)} expected by chance), so luck explains most of the spread. ${b10ok} of the ${b10in.length} manager's bottom-10 machines on the floor in these dates are within normal luck: their low win is explained by their game math, play volume and location, not by bad luck. Beyond ±3: ${out3.length ? out3.join(", ") : "none"}${out3.length ? ", which luck does not explain (see the leak test in section 5)" : ""}.</div>`;
}
function renderZones() {
  const tr = FACT.zones.map(z => { const D = aggDaily(i => META[F.m[i]].zone_id === z);
    return {type: "scatter", mode: "lines", name: ZN[z], x: daysR(), y: sliceR(rolling(D.ci, D.ah)), line: {color: css(ZCOL[z]), width: z === "Z-B40" ? 3 : 2},
      hovertemplate: ZN[z] + " %{x}: $%{y:.0f} per machine-hour<extra></extra>"}; });
  plot("c-zones", tr, {yaxis: {title: "Coin-in per available machine-hour, $"}, xaxis: {title: "Gaming date"}, shapes: closureShape(), hovermode: "x unified"});
}
function renderEffects() {
  const e = OUT.effects.filter(r => r.outcome === "l_theo");
  const lab = e.map(r => (r.kind === "zone" ? ZN[r.level] : r.level) + (r.kind === "zone" ? " (zone)" : " (title)"));
  plot("c-effects", [{type: "bar", orientation: "h", y: lab, x: e.map(r => r.effect_pct), marker: {color: e.map(r => r.kind === "zone" ? css("--s1") : css("--s3"))},
    error_x: {type: "data", symmetric: false, array: e.map(r => r.hi_pct - r.effect_pct), arrayminus: e.map(r => r.effect_pct - r.lo_pct), color: css("--ink2")},
    hovertemplate: "%{y}: %{x:.1f}%<extra></extra>"}], {xaxis: {title: "Effect on expected win per available hour, % (vs Center Pit / Desert Gold)"}, yaxis: {automargin: true}, margin: {l: 170}, showlegend: false});
}
function renderBank(M) {
  const b = state.bank;
  const fl = aggDaily(null);
  if (!b) { $("#bank-title").textContent = "pick a bank in the filter bar (showing whole floor)";
    plot("c-bank", [{type: "scatter", mode: "lines", name: "Whole floor", x: daysR(), y: sliceR(rolling(fl.ci, fl.ah)), line: {color: css("--neutral"), width: 2}}],
      {yaxis: {title: "Coin-in per available machine-hour, $"}, shapes: closureShape()});
    $("#t-bank").innerHTML = "<tbody><tr><td>Choose a bank to list its machines.</td></tr></tbody>"; return; }
  const z = FACT.bank_zone[b], bi = FACT.banks.indexOf(b);
  $("#bank-title").textContent = `${b} in ${ZN[z]}`;
  const B = aggDaily(i => F.b[i] === bi), Z = aggDaily(i => META[F.m[i]].zone_id === z);
  plot("c-bank", [
    {type: "scatter", mode: "lines", name: "Bank " + b, x: daysR(), y: sliceR(rolling(B.ci, B.ah)), line: {color: css("--s2"), width: 2.5}},
    {type: "scatter", mode: "lines", name: ZN[z] + " (zone)", x: daysR(), y: sliceR(rolling(Z.ci, Z.ah)), line: {color: css(ZCOL[z]), width: 2, dash: "dot"}},
    {type: "scatter", mode: "lines", name: "Whole floor", x: daysR(), y: sliceR(rolling(fl.ci, fl.ah)), line: {color: css("--neutral"), width: 2}},
  ], {yaxis: {title: "Coin-in per available machine-hour, $"}, xaxis: {title: "Gaming date"}, shapes: closureShape(), hovermode: "x unified"});
  const rows = M.filter(a => { for (let i = 0; i < N; i++) {} return true; }).filter(a => bankOfMachineInRange(a) === bi).sort((x, y) => x.thh - y.thh);
  table("t-bank", [{h: "Machine", k: "id"}, {h: "Title", f: r => r.meta.theme}, {h: "Days", k: "days", num: 1},
    {h: "Theo/avail h", f: r => fmt$(r.thh, 2), num: 1}, {h: "vs zone", f: r => pct(r.thh_vs_zone, 0), num: 1},
    {h: "Luck z", f: r => r.z.toFixed(2), num: 1}, {h: "Avail.", f: r => pct(r.avail, 1), num: 1}, {h: "Verdict", f: r => verdictPill(r.verdict)}], rows);
}
let _bankCache = null;
function bankOfMachineInRange(a) { // last bank in range for this machine (machines move)
  if (!_bankCache || _bankCache.key !== state.d0 + "-" + state.d1) { const m = new Array(NM).fill(-1); for (let i = 0; i < N; i++) if (inRange(i)) m[F.m[i]] = F.b[i]; _bankCache = {key: state.d0 + "-" + state.d1, m}; }
  return _bankCache.m[FACT.machines.indexOf(a.id)];
}
function renderLeakStatic() {
  const L = OUT.leak_monthly.filter(r => r.machine_id === "M-1158").sort((a, b) => a.month < b.month ? -1 : 1);
  plot("c-leak", [
    {type: "bar", name: "Observed hit rate", x: L.map(r => r.month), y: L.map(r => 100 * r.hit_rate), marker: {color: css("--crit")}, hovertemplate: "%{x}: %{y:.1f}% of spins paid<extra></extra>"},
    {type: "scatter", mode: "lines", name: "Published hit probability (31.5%)", x: L.map(r => r.month), y: L.map(r => 100 * r.p), line: {color: css("--ink2"), dash: "dash", width: 2}},
    {type: "scatter", mode: "markers+lines", name: "Observed hold %", x: L.map(r => r.month), y: L.map(r => 100 * r.hold), marker: {color: css("--s1"), size: 9}, line: {color: css("--s1"), width: 2}},
    {type: "scatter", mode: "lines", name: "Expected hold (10%)", x: L.map(r => r.month), y: L.map(r => 100 * r.theo_hold), line: {color: css("--s1"), dash: "dot", width: 2}},
  ], {yaxis: {title: "Percent of spins / of coin-in, %", rangemode: "tozero"}, xaxis: {title: "Month"}});
  const fl = OUT.leak_flags;
  plot("c-leak2", [{type: "scatter", mode: "markers", x: fl.map(r => r.hit_z), y: fl.map(r => r.luck_z), text: fl.map(r => `${r.machine_id} ${r.theme}<br>hit z ${r.hit_z.toFixed(1)}, payout z ${r.luck_z.toFixed(2)}`), hovertemplate: "%{text}<extra></extra>",
    marker: {size: fl.map(r => r.machine_id === "M-1158" ? 14 : 7), color: fl.map(r => r.machine_id === "M-1158" ? css("--crit") : css("--s1")), opacity: .8, line: {color: css("--card"), width: 1}}}],
    {xaxis: {title: "Hit-rate z (observed − published hit rate, in SD)", type: "linear"}, yaxis: {title: "Payout (luck) z"}, showlegend: false,
     annotations: [{x: 124, y: fl.find(r => r.machine_id === "M-1158").luck_z, text: "M-1158", showarrow: true, ax: -40, ay: -20}]});
  const S = OUT.leak_summary["M-1158"];
  $("#leak-answer").innerHTML = `<div class="answer crit"><b>One lead, confirmed two ways.</b> M-1158 paid on <b>${pct(S.hit_rate)}</b> of spins vs. a published <b>${pct(S.published_hit)}</b> (z = ${S.hit_z.toFixed(0)}, every month), and held <b>${pct(S.hold)}</b> vs. <b>${pct(S.theo_hold, 0)}</b> expected (z = ${S.luck_z.toFixed(2)}). It behaves like a ${pct(S.implied_rtp)} payout game, not 90%. Shortfall vs. theo: <b>${fmt$(S.shortfall_usd)}</b> over six months (95% range ${fmt$(S.shortfall_ci95[0])}–${fmt$(S.shortfall_ci95[1])}). Every other machine's hit rate matches its published math (SD of z = ${OUT.leak_summary.hit_z_sd_excl.toFixed(2)}, max |z| ${OUT.leak_summary.hit_z_max_excl.toFixed(1)}). Action: a $1,200 paytable inspection, <a href="#plan-inspect">see plan</a>. Secondary lead: Comet Cash as a title held 7.69% vs 8.00% (pooled z −3.5, hit-rate z +3.2). This is small and could just be a rounded published hit probability (0.046), so we verify it with one inspection rather than act on it.</div>`;
  table("t-titles", [{h: "Title", k: "theme"}, {h: "Machines", k: "machines", num: 1}, {h: "Coin-in", f: r => fmtK(r.coin_in), num: 1},
    {h: "Hold", f: r => pct(r.hold, 2), num: 1}, {h: "Expected hold", f: r => pct(r.theo_hold, 2), num: 1},
    {h: "Pooled payout z", f: r => r.pooled_luck_z.toFixed(2), num: 1}, {h: "Pooled hit-rate z", f: r => r.pooled_hit_z.toFixed(2), num: 1}],
    OUT.title_pooled, {rowClass: r => Math.abs(r.pooled_luck_z) > 3 ? "hl" : ""});
}
function renderTrue(M) {
  const order = ["Payout lead: inspect", "Short window", "Availability", "Weak game for its spot", "Location", "Unlucky (within noise)", "OK"];
  const cnt = order.map(v => M.filter(a => a.verdict === v).length);
  plot("c-verdicts", [{type: "bar", orientation: "h", y: order, x: cnt, marker: {color: [css("--crit"), css("--neutral"), css("--s7"), css("--s4"), css("--s1"), css("--neutral"), css("--s3")]},
    text: cnt.map(String), textposition: "outside", hovertemplate: "%{y}: %{x} machines<extra></extra>"}], {xaxis: {title: "Machines"}, yaxis: {automargin: true, autorange: "reversed"}, margin: {l: 170, t: 10}, showlegend: false});
  const tu = M.filter(a => a.verdict === "Weak game for its spot").sort((a, b) => b.gain - a.gain);
  const themes = {}; tu.forEach(a => themes[a.meta.theme] = (themes[a.meta.theme] || 0) + 1);
  const inB10 = tu.slice(0, 10).filter(a => B10.includes(a.id)).length;
  const TT = OUT.transfer;
  $("#true-answer").innerHTML = `<b>Answer:</b> ${tu.length} machines are weak for their spot: they earn under 75% of their zone peers' expected win per available hour without making it up in volume. They are not broken. All are loose, low-margin titles (${Object.entries(themes).map(([k, v]) => v + " " + k).join(", ")}): guests play them about as much as the machines around them, but the game is designed to keep less. The ten with the most to gain sit mostly in <i>strong</i> zones. ${inB10 ? "Only " + inB10 + " of those ten are" : "None of those ten is"} on the manager's list, which catches weak titles only where they also sit in the weak Back Forty. <b>Will a tighter game keep its play in a prime spot?</b> Title effects measured separately in the Back Forty and in prime zones correlate ${TT.title_effect_corr_B40_vs_prime.toFixed(2)}, and a 10% higher house edge costs only about ${(-10 * TT.coin_in_elasticity_to_house_edge).toFixed(0)}% of coin-in per hour, no more in prime zones. So our plan swaps strong Back Forty titles with machines from this list (<a href="#plan-move">plan</a>).`;
  table("t-true", [{h: "#", f: r => tu.indexOf(r) + 1, num: 1}, {h: "Machine", k: "id"}, {h: "Title (RTP)", f: r => `${r.meta.theme} (${pct(r.meta.rtp, 0)})`},
    {h: "Zone · bank", f: r => `${ZN[r.meta.zone_id]} · ${r.meta.bank_id}`}, {h: "Theo per avail h", f: r => fmt$(r.thh, 2), num: 1},
    {h: "Zone peers", f: r => fmt$(r.peer_thh, 2), num: 1}, {h: "Coin-in/h vs zone", f: r => pct(r.cih_vs_zone, 0), num: 1},
    {h: "Replacement value, $/day", f: r => fmt$(r.gain, 0), num: 1}, {h: "In manager's list?", f: r => B10.includes(r.id) ? "yes" : ""}], tu,
    {rowClass: r => B10.includes(r.id) ? "hl" : ""});
}
function renderModel() {
  const D = OUT.forecast_daily, M = OUT.forecast_metrics, te = D.filter(r => r.split === "test");
  const x = D.map(r => r.gaming_date.slice(0, 10));
  plot("c-model", [
    {type: "scatter", mode: "lines", name: "Actual coin-in", x, y: D.map(r => r.coin_in), line: {color: css("--ink2"), width: 2}},
    {type: "scatter", mode: "lines", name: "Weekday model (chosen, frozen Apr 30)", x, y: D.map(r => r.model), line: {color: css("--s1"), width: 2}},
    {type: "scatter", mode: "lines", name: "80% prediction band (low)", x: te.map(r => r.gaming_date.slice(0, 10)), y: te.map(r => r.model_lo80), line: {width: 0}, showlegend: false, hoverinfo: "skip"},
    {type: "scatter", mode: "lines", name: "80% prediction band", x: te.map(r => r.gaming_date.slice(0, 10)), y: te.map(r => r.model_hi80), fill: "tonexty", fillcolor: "rgba(42,120,214,0.15)", line: {width: 0}, hoverinfo: "skip"},
    {type: "scatter", mode: "lines", name: "Baseline: constant training mean", x, y: D.map(r => r.baseline), line: {color: css("--s2"), width: 2, dash: "dash"}},
    {type: "scatter", mode: "lines", name: "Baseline 2: repeat last training week", x: te.map(r => r.gaming_date.slice(0, 10)), y: te.map(r => r.snaive), line: {color: css("--s3"), width: 1.5, dash: "dot"}},
  ], {yaxis: {title: "Daily floor coin-in, $"}, xaxis: {title: "Gaming date"}, hovermode: "x unified",
      shapes: [{type: "rect", xref: "x", yref: "paper", x0: "2025-05-01", x1: "2025-06-30", y0: 0, y1: 1, fillcolor: css("--s4"), opacity: .08, line: {width: 0}}],
      annotations: [{x: "2025-03-01", y: 1.04, yref: "paper", text: "TRAINING (Jan–Apr)", showarrow: false}, {x: "2025-06-01", y: 1.04, yref: "paper", text: "HOLDOUT TEST (May–Jun)", showarrow: false}]});
  const k = [["Constant-mean baseline MAE", fmtK(M.test_mae_baseline), pct(M.test_mape_baseline) + " of daily coin-in"],
             ["Seasonal-naive baseline MAE", fmtK(M.test_mae_snaive), "repeat Apr 24–30 by weekday · " + pct(M.test_mape_snaive) + " of coin-in"],
             ["Weekday model MAE", fmtK(M.test_mae_model), pct(M.test_mape_model) + " of daily coin-in"],
             ["Improvement vs constant mean", fmtK(M.test_mae_baseline - M.test_mae_model) + "/day", "95% CI " + fmtK(M.mae_improvement_ci95[0]) + "–" + fmtK(M.mae_improvement_ci95[1])],
             ["Improvement vs seasonal naive", fmtK(M.test_mae_snaive - M.test_mae_model) + "/day", "95% CI " + fmtK(M.mae_improvement_vs_snaive_ci95[0]) + "–" + fmtK(M.mae_improvement_vs_snaive_ci95[1]) + ": a tie"],
             ["80% band coverage", pct(M.pi80_coverage, 0), "of holdout days inside the band"]];
  $("#model-kpis").innerHTML = k.map(x => `<div class="kpi"><div class="l">${x[0]}</div><div class="v">${x[1]}</div><div class="f">${x[2]}</div></div>`).join("");
  table("t-model-wd", [{h: "Weekday", k: "wd"}, {h: "Days", k: "n", num: 1}, {h: "Model MAE", f: r => fmtK(r.mae_model), num: 1}, {h: "Constant-mean MAE", f: r => fmtK(r.mae_baseline), num: 1}, {h: "Seasonal-naive MAE", f: r => fmtK(r.mae_snaive), num: 1}, {h: "Model bias (actual − forecast)", f: r => fmtK(r.bias_model), num: 1}],
    ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(w => M.by_weekday.find(r => r.wd === w)));
  const wm = M.weekday_multiplier_train;
  $("#model-method").innerHTML = `<h3>How it was validated</h3><ul class="caption">
   <li><b>Split by time:</b> train Jan 1–Apr 30 (120 days), test May 1–Jun 30 (61 days). No random split.</li>
   <li><b>Model:</b> log(coin-in per scheduled hour) = a separate level for each weekday, fitted by least squares on the 120 training days; forecast × scheduled hours (handles the 23-hour DST day).</li>
   <li><b>Two baselines:</b> the guide's constant training mean, and a tougher seasonal-naive one that repeats the last training week (Apr 24–30) weekday by weekday. The model clearly beats the first. Against the second it is ${fmtK(M.test_mae_snaive - M.test_mae_model)}/day better, which is within noise, though it was better on the inner April check (${fmtK(M.inner_validation_mae.weekday)} vs ${fmtK(M.inner_mae_snaive)}). What it adds over "copy last week" is stability (one odd week doesn't move it) and a prediction band.</li>
   <li><b>Chosen before testing:</b> on an inner split (train Jan–Mar, validate April) the weekday model (MAE ${fmtK(M.inner_validation_mae.weekday)}) beat a weekday+events model (${fmtK(M.inner_validation_mae["weekday+events"])}) and the constant mean (${fmtK(M.inner_validation_mae.constant_mean)}), so we froze the weekday model.</li>
   <li><b>Honest footnote:</b> on May–June the event-aware variant would have done better (MAE ${fmtK(M.test_mae_alt)}). We did not pick it in advance, so we report it but don't claim it.</li>
   <li><b>Where it fails:</b> Sundays (MAE ${fmtK(M.by_weekday.find(r => r.wd === "Sun").mae_model)} vs ${fmtK(M.by_weekday.find(r => r.wd === "Sun").mae_baseline)} for the constant mean), because May–June Sundays were softer than the event-heavy training Sundays.</li>
   <li><b>Weakness:</b> the model over-forecasts by ${fmtK(-M.test_bias_model)}/day on average because January–April had more event weekends than May–June.</li></ul>`;
  $("#model-answer").innerHTML = `<div class="answer"><b>Decision it changes:</b> the constant baseline implies every day needs the same staff. The model says Saturday brings ${pct(wm.Sat / wm.Tue - 1, 0)} more coin-in than Tuesday, and Friday through Sunday is ${pct((wm.Fri + wm.Sat + wm.Sun) / 7, 0)} of a week's play. So (1) existing flexible technician and floor-attendant hours should move from Tue/Wed to Fri/Sat, when an hour of downtime costs about 1.6× more, and short-staffed weekend shifts (which more than triple the wait for a technician, Q3) get filled first. <b>Value:</b> our queue replay says a full extra swing technician would save about ${fmtK(OUT.down_benefits["A-TECH-SHIFT"].by_shift.swing.theo_central_net_of_displacement)}–${fmtK(OUT.down_benefits["A-TECH-SHIFT"].by_shift.swing.theo_central_mean)} of expected win in July; moving existing hours to the busiest days targets the same waits for $0, while the $11,520 extra technician does not pay back; (2) the forecast is the yardstick for the July promotion weekend: the 80% band says a normal July Saturday lands within about ±8% of forecast, so a tournament lift like February's (+18%) will be clearly visible, while a Locals-Week-sized lift (+5.6%) would not be. The June Locals Week came in ${fmtK(M.locals_week_actual_vs_model)}/day above forecast even though the model ran high on average, which is consistent with a small real lift. (<a href="#plan-promo">plan</a>)</div>`;
}

// ---------- extra section hooks (filled by section scripts) ----------
const HOOKS = {static: [], dynamic: []};

// ---------- controls ----------
let rangeWarn = "";
function setRange(d0, d1) { rangeWarn = d0 > d1 ? " · ⚠ From was after To, so we swapped them" : ""; state.d0 = Math.max(0, Math.min(d0, d1)); state.d1 = Math.min(DAYS.length - 1, Math.max(d0, d1)); $("#f-from").value = DAYS[state.d0]; $("#f-to").value = DAYS[state.d1]; renderAll(); }
const idxOf = s => { let i = DAYS.indexOf(s); if (i >= 0) return i; return s < DAYS[0] ? 0 : DAYS.length - 1; };
function initControls() {
  ["f-from", "f-to"].forEach(id => { const el = $("#" + id); el.min = DAYS[0]; el.max = DAYS[DAYS.length - 1]; el.addEventListener("change", () => setRange(idxOf($("#f-from").value), idxOf($("#f-to").value))); });
  const P = {all: ["2025-01-01", "2025-06-30"], q1: ["2025-01-01", "2025-03-31"], q2: ["2025-04-01", "2025-06-30"], train: ["2025-01-01", "2025-04-30"], test: ["2025-05-01", "2025-06-30"]};
  document.querySelectorAll("[data-preset]").forEach(b => b.addEventListener("click", () => setRange(idxOf(P[b.dataset.preset][0]), idxOf(P[b.dataset.preset][1]))));
  const bs = $("#f-bank"); bs.innerHTML = `<option value="">All banks</option>` + FACT.zones.map(z => `<optgroup label="${ZN[z]}">` + FACT.banks.filter(b => FACT.bank_zone[b] === z).map(b => `<option value="${b}">${b}</option>`).join("") + "</optgroup>").join("");
  bs.addEventListener("change", () => { state.bank = bs.value; renderAll(); if (state.bank) document.getElementById("bank-drill").scrollIntoView({behavior: "smooth"}); });
  const gs = $("#f-group"); const G = OUT.groups || [];
  const types = [...new Set(G.map(r => r.group_type))];
  gs.innerHTML = types.map(t => `<optgroup label="${t.replace("_", " ")}">` + G.filter(r => r.group_type === t).map(r => `<option value="${t}|${r.group}">${r.group}</option>`).join("") + "</optgroup>").join("");
  gs.addEventListener("change", () => { state.group = gs.value; renderGuests(); document.getElementById("s-players").scrollIntoView({behavior: "smooth"}); });
  $("#f-reset").addEventListener("click", () => { state.bank = ""; bs.value = ""; state.group = "all|All"; gs.value = "all|All"; setRange(0, DAYS.length - 1); });
  document.querySelectorAll('a[href^="#"]').forEach(a => a.addEventListener("click", () => { const t = document.querySelector(a.getAttribute("href")); if (t) { t.classList.remove("flash"); void t.offsetWidth; t.classList.add("flash"); } }));
}
function renderGuests() { HOOKS.guest && HOOKS.guest(); }
function renderAll() {
  const M = aggMachines();
  $("#rangeinfo").textContent = `${DAYS[state.d0]} → ${DAYS[state.d1]} · ${state.d1 - state.d0 + 1} days${state.bank ? " · bank " + state.bank : ""}`;
  $("#rangeinfo").innerHTML += rangeWarn ? `<span class="rangewarn">${rangeWarn}</span>` : "";
  renderSummary(M); renderBottom10(M); renderLuck(M); renderZones(); renderBank(M); renderTrue(M);
  HOOKS.dynamic.forEach(f => f(M)); renderGuests();
}
window.addEventListener("DOMContentLoaded", () => {
  initControls(); renderEffects(); renderLeakStatic(); renderModel(); HOOKS.static.forEach(f => f());
  setRange(0, DAYS.length - 1);
  if (location.hash) { const t = document.querySelector(location.hash); t && t.scrollIntoView(); }
});

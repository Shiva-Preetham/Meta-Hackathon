// Signal dashboard. Plain JS, no build step. Every data string is inserted with
// textContent (it can come from customer messages), never innerHTML.
"use strict";

const state = { selected: null, sort: "score", themes: [], windowDays: 7, detailKey: null, themesKey: null };
const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";

// --- helpers ------------------------------------------------------------------------
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  return node;
}
function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}
async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}
function money(d) {
  if (d === null || d === undefined) return "–";
  if (Math.abs(d) >= 1e6) return `$${(d / 1e6).toFixed(1)}M`;
  if (Math.abs(d) >= 1e4) return `$${(d / 1e3).toFixed(1)}K`;
  return `$${d.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
function num(n, digits = 0) {
  if (n === null || n === undefined) return "–";
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function dateLabel(iso, withTime = false) {
  if (!iso) return "–";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  const opts = { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" };
  if (withTime) Object.assign(opts, { hour: "2-digit", minute: "2-digit", hour12: false });
  return d.toLocaleString(undefined, opts) + (withTime ? " UTC" : "");
}
function addDays(isoDate, n) {
  const d = new Date(isoDate + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

// --- tooltip ------------------------------------------------------------------------
const tip = $("tooltip");
function showTip(evt, value, label) {
  tip.replaceChildren(el("strong", { text: value }), el("span", { text: label }));
  tip.hidden = false;
  const r = evt.target.getBoundingClientRect();
  const x = (evt.clientX ?? r.left + r.width / 2) + window.scrollX;
  const y = (evt.clientY ?? r.top) + window.scrollY;
  const w = tip.offsetWidth;
  tip.style.left = `${Math.min(Math.max(8, x - w / 2), document.documentElement.clientWidth - w - 8)}px`;
  tip.style.top = `${y - tip.offsetHeight - 12}px`;
}
function hideTip() { tip.hidden = true; }
function hoverable(node, value, label) {
  node.setAttribute("tabindex", "0");
  node.setAttribute("aria-label", `${label}: ${value}`);
  node.addEventListener("pointermove", (e) => showTip(e, value, label));
  node.addEventListener("pointerleave", hideTip);
  node.addEventListener("focus", (e) => showTip(e, value, label));
  node.addEventListener("blur", hideTip);
}

// --- sparkline: daily mentions, previous window de-emphasised, recent window in accent --
function sparkline(counts, windowDays) {
  const w = 84, h = 24, n = counts.length || 1;
  const step = w / n, bw = Math.max(2, step - 2);
  const max = Math.max(1, ...counts);
  const s = svg("svg", { width: w, height: h, viewBox: `0 0 ${w} ${h}`, class: "spark", role: "img",
    "aria-label": `Daily mentions, last ${n} days: ${counts.join(", ")}` });
  s.appendChild(svg("line", { x1: 0, x2: w, y1: h - 0.5, y2: h - 0.5, stroke: "var(--grid)", "stroke-width": 1 }));
  counts.forEach((c, i) => {
    if (!c) return;
    const bh = Math.max(2, (c / max) * (h - 2));
    s.appendChild(svg("rect", { x: i * step + (step - bw) / 2, y: h - bh, width: bw, height: bh, rx: 1,
      fill: i >= n - windowDays ? "var(--accent)" : "var(--accent-soft)" }));
  });
  return s;
}

function severity(sev) {
  if (!sev) return el("span", { class: "note", text: "–" });
  return el("span", { class: `sev ${sev}` }, el("span", { class: "dot", "aria-hidden": "true" }), sev);
}
function growthText(g) {
  if (g === null || g === undefined) return "–";
  const arrow = g > 1.05 ? "↑" : g < 0.95 ? "↓" : "→";
  return `${arrow} ${num(g, 2)}×`;
}

// --- summary + KPIs -----------------------------------------------------------------
function renderSummary(s) {
  state.windowDays = s.window_days;
  const offline = s.mode === "offline";
  const mode = $("mode");
  mode.textContent = offline ? "Offline demo mode: heuristic, not Muse Spark" : `Model: ${s.model}`;
  mode.className = `pill ${offline ? "mode-offline" : ""}`;
  $("data-note").textContent = s.data_note;
  $("footer-note").textContent = `${s.data_note} Embeddings: ${s.embedder}.`;
  const r = s.replay || {};
  const clock = s.data_clock ? `Data clock ${dateLabel(s.data_clock, true)}` : "No data yet";
  $("clock").textContent = r.status === "running" ? `${clock} · replay ${r.ingested}/${r.total}` : clock;
  $("replay-stop").disabled = r.status !== "running";

  $("kpi-items").textContent = num(s.items_processed);
  $("kpi-items-sub").textContent = `of ${num(s.items_total)} ingested`;
  $("kpi-themes").textContent = num(s.active_themes);
  $("kpi-themes-sub").textContent = `${num(s.items_by_state.clustered || 0)} items in themes`;
  $("kpi-rar").textContent = money(s.revenue_at_risk_dollars);
  $("kpi-rar-sub").textContent = `monthly, last ${s.window_days} days, each account once`;
  $("kpi-cost").textContent = `$${num(s.cost_usd, s.cost_usd < 10 ? 4 : 2)}`;
  $("kpi-cost-sub").textContent = offline ? "offline heuristic costs nothing" : `budget $${num(s.budget_usd, 2)}`;
}

// --- themes table -------------------------------------------------------------------
function renderThemes(themes) {
  const key = JSON.stringify(themes) + state.selected;
  if (key === state.themesKey) return;
  state.themesKey = key;
  state.themes = themes;
  const body = $("themes-body");
  $("themes-empty").hidden = themes.length > 0;
  body.replaceChildren(...themes.map((t) => {
    const name = el("div", { class: "theme-name" }, t.name, t.is_new ? el("span", { class: "badge-new", text: "new" }) : null);
    const row = el("tr", {
      class: `row ${t.id === state.selected ? "selected" : ""}`, tabindex: "0",
      onclick: () => select(t.id),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(t.id); } },
    },
      el("td", { class: "rank", text: t.rank }),
      el("td", {}, name, t.summary ? el("div", { class: "theme-summary", text: t.summary }) : null),
      el("td", { class: "num", text: t.score === null ? "pending" : num(t.score, 1) }),
      el("td", { class: "num", text: money(t.revenue_at_risk_dollars) }),
      el("td", { class: "num hide-sm" }, el("div", { class: "mentions" },
        sparkline(t.sparkline, state.windowDays),
        el("span", { class: "counts" }, num(t.mentions_recent), el("span", { class: "prev", text: ` / ${num(t.mentions_prev)}` })))),
      el("td", { class: "num growth hide-sm", text: growthText(t.growth) }),
      el("td", { class: "hide-sm" }, severity(t.top_severity)),
    );
    return row;
  }));
  if (state.selected === null && themes.length) select(themes[0].id);
}

// --- detail panel -------------------------------------------------------------------
function trendChart(trend, windowDays) {
  const counts = trend.daily_counts || [];
  const wrap = el("div", { class: "chart-wrap" });
  if (!counts.length) return wrap;
  const width = Math.max(260, $("detail").clientWidth - 34), height = 150;
  const m = { l: 28, r: 4, t: 8, b: 34 };
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const max = Math.max(1, ...counts);
  const nice = max <= 4 ? max : Math.ceil(max / 2) * 2;
  const s = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": `Daily mentions for the last ${counts.length} days` });
  [0, nice / 2, nice].forEach((v) => {
    const y = m.t + ih - (v / nice) * ih;
    s.appendChild(svg("line", { x1: m.l, x2: width - m.r, y1: y, y2: y, stroke: v === 0 ? "var(--axis)" : "var(--grid)", "stroke-width": 1 }));
    const label = svg("text", { x: m.l - 6, y: y + 4, "text-anchor": "end" });
    label.textContent = Number.isInteger(v) ? v : v.toFixed(1);
    s.appendChild(label);
  });
  const step = iw / counts.length, bw = Math.min(24, Math.max(3, step - 2));
  counts.forEach((c, i) => {
    const day = addDays(trend.start, i);
    const recent = i >= counts.length - windowDays;
    const x = m.l + i * step + (step - bw) / 2;
    const bh = (c / nice) * ih;
    const g = svg("g", { class: "bar" });
    g.appendChild(svg("rect", { x: m.l + i * step, y: m.t, width: step, height: ih, fill: "transparent" })); // hit area
    if (c > 0) {
      const r = Math.min(4, bw / 2, bh);
      // rounded data-end, square at the baseline
      g.appendChild(svg("path", { fill: recent ? "var(--accent)" : "var(--accent-soft)",
        d: `M${x},${m.t + ih} V${m.t + ih - bh + r} Q${x},${m.t + ih - bh} ${x + r},${m.t + ih - bh} H${x + bw - r} Q${x + bw},${m.t + ih - bh} ${x + bw},${m.t + ih - bh + r} V${m.t + ih} Z` }));
    }
    hoverable(g, `${c} mention${c === 1 ? "" : "s"}`, `${dateLabel(day)}${recent ? " · recent window" : ""}`);
    s.appendChild(g);
  });
  // window brackets under the axis
  const split = m.l + (counts.length - windowDays) * step;
  const prevStart = m.l + Math.max(0, counts.length - 2 * windowDays) * step;
  const narrow = split - prevStart < 100;  // short labels when the brackets are tight
  [[prevStart, split, narrow ? `prev ${windowDays}d` : `previous ${windowDays} days`],
   [split, width - m.r, narrow ? `last ${windowDays}d` : `recent ${windowDays} days`]].forEach(([x1, x2, text]) => {
    const y = height - 18;
    s.appendChild(svg("line", { x1: x1 + 2, x2: x2 - 2, y1: y, y2: y, stroke: "var(--axis)", "stroke-width": 1 }));
    const t = svg("text", { x: (x1 + x2) / 2, y: y + 13, "text-anchor": "middle" });
    t.textContent = text;
    s.appendChild(t);
  });
  const first = svg("text", { x: m.l, y: height - 2, "text-anchor": "start" });
  first.textContent = dateLabel(trend.start);
  s.appendChild(first);
  wrap.appendChild(s);
  return wrap;
}

const PLAN_ORDER = ["free", "individual", "family", "business"];
function planMix(plans) {
  const counts = Object.fromEntries(plans);
  const total = plans.reduce((a, [, n]) => a + n, 0) || 1;
  const keys = [...PLAN_ORDER.filter((p) => counts[p]), ...Object.keys(counts).filter((p) => !PLAN_ORDER.includes(p))];
  const color = (p) => { const i = PLAN_ORDER.indexOf(p); return i >= 0 ? `var(--plan-${i + 1})` : "var(--plan-other)"; };
  const bar = el("div", { class: "segbar", role: "img",
    "aria-label": keys.map((p) => `${p} ${counts[p]}`).join(", ") });
  keys.forEach((p) => {
    const seg = el("div", { style: `flex:${counts[p]} 1 0;background:${color(p)}` });
    hoverable(seg, `${counts[p]} (${Math.round((100 * counts[p]) / total)}%)`, `${p} plan`);
    bar.appendChild(seg);
  });
  const legend = el("div", { class: "legend" }, keys.map((p) =>
    el("span", {}, el("span", { class: "key", style: `background:${color(p)}` }), `${p} ${counts[p]}`)));
  return el("div", {}, el("h3", { text: "Who reports it (simulated plans)" }), bar, legend);
}

function renderDetail(d, force = false) {
  const key = JSON.stringify(d);
  if (!force && key === state.detailKey) return;  // unchanged: keep open panels and focus
  state.detailKey = key;
  const box = $("detail");
  const parts = [el("h2", { text: d.name }), el("p", { class: "summary", text: d.summary || "Not named yet." })];
  const x = d.explanation;
  if (x) {
    parts.push(el("div", { class: "why" },
      el("h3", { text: "Why it is ranked here" }),
      el("div", { class: "formula", text: x.formula }),
      el("div", { class: "formula filled", text: x.filled }),
      el("div", { class: "inputs" },
        [["Revenue at risk", money(x.revenue_at_risk_dollars)],
         ["At-risk accounts", num(x.at_risk_accounts)],
         ["Growth", growthText(x.growth)],
         ["Recent mentions", num(x.mentions_recent)],
         ["Previous mentions", num(x.mentions_prev)],
         ["Severity weight", num(x.severity_weight, 2)]].map(([k, v]) =>
          el("div", {}, el("div", { class: "k", text: k }), el("div", { class: "v", text: v })))),
      el("div", { class: "window-note", text: `${x.window}. Revenue counts each account once, only when its message signals churn or is high/critical severity. Revenue is simulated.` }),
    ));
  } else {
    parts.push(el("p", { class: "note", text: "Score pending: the scorer runs a few seconds after clustering." }));
  }
  parts.push(el("h3", { text: "Daily mentions" }), trendChart(d.trend, state.windowDays));
  if (d.breakdown && d.breakdown.plans.length) parts.push(planMix(d.breakdown.plans));
  parts.push(el("h3", { text: "Evidence", style: "margin-top:14px" }));
  parts.push(el("ul", { class: "quotes" }, d.quotes.map((q) => {
    const quote = el("p", { class: "quote" }, el("mark", { text: `“${q.quote}”` }));
    const meta = el("div", { class: "meta" },
      el("span", { text: q.source.replace("_", " ") }),
      el("span", { text: `${q.plan} plan` }),
      el("span", { text: dateLabel(q.created_at) }),
      el("span", { text: `severity ${q.severity}` }),
      q.churn_signal ? el("span", { class: "churn", text: "churn signal" }) : null,
      q.quote_verified ? el("span", { text: "verbatim quote" }) : el("span", { text: "quote not verbatim: first sentence shown" }),
    );
    const full = q.text !== q.quote ? el("details", {}, el("summary", { class: "note", text: "Full message" }), el("p", { text: q.text })) : null;
    return el("li", {}, quote, meta, full);
  })));
  box.replaceChildren(...parts);
}

async function select(id) {
  state.selected = id;
  document.querySelectorAll("#themes-body tr").forEach((tr, i) => {
    tr.classList.toggle("selected", state.themes[i] && state.themes[i].id === id);
  });
  state.themesKey = null;
  try { renderDetail(await api(`/api/themes/${id}`), true); } catch (e) { console.warn(e); }
}

// --- pipeline health ----------------------------------------------------------------
function renderHealth(m) {
  const q = m.queue_by_state || {};
  const dead = q.dead || 0;
  const stageRows = Object.entries(m.stage_latency || {}).map(([k, v]) =>
    el("tr", {}, el("td", { text: k }), el("td", { class: "num", text: num(v.p50_ms, 0) }), el("td", { class: "num", text: num(v.p95_ms, 0) })));
  const llm = m.llm || {};
  $("health").replaceChildren(
    el("div", {}, el("h3", { text: "Queue" }), el("table", {},
      el("tr", {}, el("th", { text: "State" }), el("th", { class: "num", text: "Jobs" })),
      ["queued", "running", "failed", "done", "dead"].map((s) =>
        el("tr", {}, el("td", { text: s }), el("td", { class: "num", text: num(q[s] || 0) }))))),
    el("div", {}, el("h3", { text: "Stage time (ms)" }), el("table", {},
      el("tr", {}, el("th", { text: "Stage" }), el("th", { class: "num", text: "p50" }), el("th", { class: "num", text: "p95" })),
      stageRows.length ? stageRows : el("tr", {}, el("td", { text: "no finished jobs" })))),
    el("div", {}, el("h3", { text: "Model calls" }), el("table", {},
      el("tr", {}, el("td", { text: "Calls" }), el("td", { class: "num", text: num(llm.calls || 0) })),
      el("tr", {}, el("td", { text: "Cache hit rate" }), el("td", { class: "num", text: llm.cache_hit_rate === null || llm.cache_hit_rate === undefined ? "–" : `${num(100 * llm.cache_hit_rate, 1)}%` })),
      el("tr", {}, el("td", { text: "Throughput" }), el("td", { class: "num", text: m.throughput_items_per_min ? `${num(m.throughput_items_per_min, 0)} items/min` : "–" })),
      el("tr", {}, el("td", { text: "Unassigned pool" }), el("td", { class: "num", text: num(m.pool_size) })))),
    el("div", {}, el("h3", { text: "Status" }),
      el("p", { class: `status ${dead ? "bad" : "ok"}` }, el("span", { class: "icon", "aria-hidden": "true", text: dead ? "!" : "✓" }),
        dead ? `${dead} dead job${dead === 1 ? "" : "s"}: run "python -m signalhq stats"` : "No dead jobs"),
      el("p", { class: `status ${m.workers_running ? "ok" : "bad"}` }, el("span", { class: "icon", "aria-hidden": "true", text: m.workers_running ? "✓" : "–" }),
        m.workers_running ? "Workers running" : "No workers in this process")),
  );
}

// --- loop -----------------------------------------------------------------------------
async function refresh() {
  try {
    const [summary, themes] = await Promise.all([api("/api/summary"), api(`/api/themes?sort=${state.sort}`)]);
    renderSummary(summary);
    renderThemes(themes);
    if (state.selected !== null && themes.some((t) => t.id === state.selected)) {
      renderDetail(await api(`/api/themes/${state.selected}`));
    } else if (state.selected !== null) {
      state.selected = null;
      if (themes.length) select(themes[0].id);
    }
  } catch (e) { console.warn(e); }
}
async function refreshHealth() {
  try { renderHealth(await api("/api/metrics")); } catch (e) { console.warn(e); }
}

$("sort").addEventListener("change", (e) => { state.sort = e.target.value; refresh(); });
$("replay-start").addEventListener("click", async () => {
  if (!confirm("Restart the replay? Processed items and themes are cleared; the model cache, cost ledger and labels are kept.")) return;
  state.selected = null;
  try { await api("/api/replay/start", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" }); }
  catch (e) { alert(e.message); }
  refresh();
});
$("replay-stop").addEventListener("click", async () => { await api("/api/replay/stop", { method: "POST" }); refresh(); });
window.addEventListener("resize", () => { if (state.selected !== null) select(state.selected); });

refresh();
refreshHealth();
setInterval(refresh, 2000);
setInterval(refreshHealth, 4000);

"use strict";
/* Village dashboard: plain JS, no dependencies. Data comes from /api/*. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const S = { state: null, history: null, plan: null, catalog: null, settings: null,
            notes: null, status: null, page: "dashboard", catKind: "all", serverSkew: 0 };
const LIVE_PHASE = 9;   // every setting is used by the running automation

// ---------- helpers ----------
const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const now = () => Date.now() / 1000 + S.serverSkew;
const nf = new Intl.NumberFormat();
function compact(n) {
  if (n == null) return "–";
  const a = Math.abs(n);
  if (a >= 1e6) return (n / 1e6).toFixed(2).replace(/\.?0+$/, "") + "M";
  if (a >= 1e4) return (n / 1e3).toFixed(a >= 1e5 ? 0 : 1).replace(/\.0$/, "") + "K";
  return nf.format(n);
}
function dur(s, precise) {
  s = Math.max(0, Math.round(s));
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600),
        m = Math.floor(s % 3600 / 60), sec = s % 60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return precise ? `${m}m ${String(sec).padStart(2, "0")}s` : `${m}m`;
  return `${sec}s`;
}
const dayFmt = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const clock = t => t == null ? "–" : dayFmt.format(new Date(t * 1000));
function ago(t) {
  const s = now() - t;
  if (s < 60) return "just now";
  return dur(s) + " ago";
}
const RES = {
  gold:        { label: "Gold",        color: "#eda100", icon: '<circle cx="12" cy="12" r="8" fill="#eda100"/><circle cx="12" cy="12" r="5" fill="none" stroke="#fff6" stroke-width="1.5"/>' },
  elixir:      { label: "Elixir",      color: "#e87ba4", icon: '<path d="M12 3c3 5 6 8 6 11a6 6 0 0 1-12 0c0-3 3-6 6-11z" fill="#e87ba4"/>' },
  dark_elixir: { label: "Dark elixir", color: "#9085e9", icon: '<path d="M12 3c3 5 6 8 6 11a6 6 0 0 1-12 0c0-3 3-6 6-11z" fill="#4a3f8f"/><path d="M9 14a3 3 0 0 0 3 3" stroke="#9085e9" stroke-width="1.6" fill="none"/>' },
  gems:        { label: "Gems",        color: "#1baf7a", icon: '<path d="M12 3l7 6-7 12L5 9z" fill="#1baf7a"/><path d="M5 9h14M12 3v18" stroke="#fff5" stroke-width="1"/>' },
};
const resIcon = k => `<svg class="res-icon" viewBox="0 0 24 24" aria-hidden="true">${RES[k]?.icon ?? ""}</svg>`;
const resName = r => ({ Gold: "gold", Elixir: "elixir", DarkElixir: "dark_elixir" }[r] || r);

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Village": "1" },
    body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
function toast(msg, kind = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}

// ---------- tooltip ----------
const tip = $("#tooltip");
function showTip(html, ev) {
  tip.innerHTML = html;
  tip.classList.remove("hidden");
  const pad = 14, r = tip.getBoundingClientRect();
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => tip.classList.add("hidden");
document.addEventListener("scroll", hideTip, true);

// ---------- navigation ----------
const TITLES = { dashboard: "Dashboard", queue: "Builder queue", automation: "Automation", attack: "Attack", settings: "Settings", notifications: "Alerts" };
function go(page) {
  if (!TITLES[page]) page = "dashboard";
  S.page = page;
  $$(".nav-btn").forEach(b => b.classList.toggle("active", b.dataset.page === page));
  Object.keys(TITLES).forEach(p => $(`#page-${p}`).classList.toggle("hidden", p !== page));
  $("#page-title").textContent = TITLES[page];
  if (location.hash.slice(1) !== page) history.replaceState(null, "", "#" + page);
  if (page === "queue") loadQueue();
  if (page === "attack") loadFarm();
  if (page === "automation") loadAuto();
  if (page === "settings") loadSettings();
  if (page === "notifications") loadNotes();
}
$$(".nav-btn").forEach(b => b.addEventListener("click", () => go(b.dataset.page)));
document.addEventListener("click", e => {
  const g = e.target.closest("[data-goto]");
  if (g) go(g.dataset.goto);
});

// ---------- theme ----------
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("theme", t);
  if (S.state) renderDashboard();
  if (S.plan && S.page === "queue") renderPlanTimeline();
}
$("#btn-theme").addEventListener("click", () =>
  applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light"));
applyTheme(new URLSearchParams(location.search).get("theme") || localStorage.getItem("theme") || "dark");

// ---------- status pills & capture ----------
async function loadStatus() {
  try { S.status = await api("/api/status"); } catch { S.status = null; }
  renderStatus();
}
function renderStatus() {
  const st = S.status;
  const pill = $("#pill-emulator");
  const map = {
    game: ["good", "Game running"], dialog: ["warning", "System dialog open"],
    other: ["warning", "Game not in front"], offline: ["critical", "Emulator offline"],
    unknown: ["", "Checking emulator…"] };
  const [cls, label] = st ? (map[st.state] || map.unknown) : ["critical", "Dashboard offline"];
  pill.innerHTML = `<span class="dot ${cls}"></span><span>${label}</span>`;
  pill.title = st?.detail ? `Focused window: ${st.detail}` : "";
  const btn = $("#btn-capture");
  const running = st?.capture_running;
  btn.disabled = !!running;
  btn.querySelector("span").textContent = running ? "Capturing…" : "Capture now";
  $("#desktop-warn").classList.toggle("hidden", !st || st.desktop_usable !== false);
  if (st?.desktop_problem) $("#desktop-why").textContent = st.desktop_problem;
}
$("#btn-capture").addEventListener("click", async () => {
  try {
    await api("/api/actions/capture", {});
    toast("Capture started: about a minute. The page updates when it's done.");
    S.status = { ...(S.status || {}), capture_running: true };
    renderStatus();
    pollAfterCapture();
  } catch (e) { toast(e.message, "error"); }
});
function pollAfterCapture() {
  const startId = S.state?.snapshot?.id;
  let tries = 0;
  const t = setInterval(async () => {
    tries++;
    await loadStatus();
    if (!S.status?.capture_running || tries > 60) {
      clearInterval(t);
      await loadState();
      await loadNotesCount();
      if (S.state?.snapshot?.id !== startId) toast("Village updated");
      else toast("Capture finished without a new snapshot: see Alerts", "error");
    }
  }, 5000);
}

// ---------- data loading ----------
async function loadState() {
  try {
    const st = await api("/api/state");
    S.serverSkew = st.now - Date.now() / 1000;
    S.state = st;
    renderDashboard();
  } catch (e) { toast("Could not load the village: " + e.message, "error"); }
}
async function loadHistory() {
  try { S.history = await api("/api/history?hours=72"); renderDashboard(); } catch { /* keep old */ }
}

// ---------- dashboard ----------
function sparkline(points, key, color) {
  const pts = points.filter(p => p[key] != null);
  if (pts.length < 2) return `<div class="muted" style="font-size:12px;margin-top:10px">Trend appears after a few captures</div>`;
  const W = 300, H = 44, t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
  let lo = Math.min(...pts.map(p => p[key])), hi = Math.max(...pts.map(p => p[key]));
  if (hi === lo) { hi += 1; lo -= 1; }
  const x = t => (t - t0) / Math.max(1, t1 - t0) * (W - 4) + 2;
  const y = v => H - 4 - (v - lo) / (hi - lo) * (H - 10);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
  const area = `${d}L${x(t1).toFixed(1)},${H}L${x(t0).toFixed(1)},${H}Z`;
  const id = "g" + key;
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-spark="${key}">
    <defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="${color}" stop-opacity=".25"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    <path d="${area}" fill="url(#${id})"/>
    <path d="${d}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
    <line class="xh" x1="0" x2="0" y1="0" y2="${H}" stroke="var(--text-muted)" stroke-width="1" vector-effect="non-scaling-stroke" visibility="hidden"/>
  </svg>`;
}
function attachSparkHover(el, points, key, label) {
  const svg = el.querySelector("svg.spark");
  if (!svg) return;
  const pts = points.filter(p => p[key] != null);
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  svg.addEventListener("mousemove", ev => {
    const r = svg.getBoundingClientRect();
    const t = t0 + (ev.clientX - r.left) / r.width * (t1 - t0);
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    const xh = svg.querySelector(".xh");
    const vx = (best.t - t0) / Math.max(1, t1 - t0) * 296 + 2;
    xh.setAttribute("x1", vx); xh.setAttribute("x2", vx); xh.setAttribute("visibility", "visible");
    showTip(`<b>${nf.format(best[key])}</b> ${esc(label)}<br><span class="muted">${clock(best.t)}</span>`, ev);
  });
  svg.addEventListener("mouseleave", () => { hideTip(); svg.querySelector(".xh").setAttribute("visibility", "hidden"); });
}

function renderTiles() {
  const st = S.state, pts = S.history?.points || [];
  const series = getComputedStyle(document.documentElement).getPropertyValue("--series-1").trim();
  $("#tiles").innerHTML = Object.keys(RES).map(k => {
    const v = st.resources?.[k], before = st.resources_before?.[k];
    let delta = "";
    if (v != null && before != null) {
      const d = v - before;
      const cls = d > 0 ? "up" : d < 0 ? "down" : "";
      delta = `<span class="${cls}">${d > 0 ? "▲" : d < 0 ? "▼" : "•"} ${compact(Math.abs(d))}</span> vs ${dur(st.snapshot.exported_at - st.resources_before.export_ts)} earlier`;
    } else if (v == null) delta = "not read at the last capture";
    return `<div class="card tile" data-res="${k}">
      <div class="tile-label">${resIcon(k)}${RES[k].label}</div>
      <div class="tile-value num" title="${v == null ? "" : nf.format(v)}">${v == null ? "–" : compact(v)}</div>
      <div class="tile-delta">${delta}</div>
      <div class="spark-wrap">${sparkline(pts, k, series)}</div></div>`;
  }).join("");
  $$("#tiles .tile").forEach(el => attachSparkHover(el, pts, el.dataset.res, RES[el.dataset.res].label));
}

function laneHTML(j) {
  const start = j.duration ? j.ends_at - j.duration : null;
  const tags = [];
  if (j.extra) tags.push(`<span class="tag" title="Runs on the extra (7th) builder">extra builder</span>`);
  if (j.helper) tags.push(`<span class="tag" title="Builder's Apprentice assigned; plain timer ends ${clock(j.timer_end)}">apprentice</span>`);
  return `<div class="lane">
    <div><div class="lane-name">${esc(j.name)}</div>
      <div class="lane-sub"><span>Level ${j.from} → ${j.to}</span>${tags.join("")}</div></div>
    <div class="bar" title="${start ? "Started about " + clock(start) : ""}"><i data-start="${start ?? ""}" data-end="${j.ends_at}"></i></div>
    <div class="lane-time"><div class="cd num" data-countdown="${j.ends_at}">–</div><div class="at">${clock(j.ends_at)}</div></div>
  </div>`;
}

function renderBuilders() {
  const b = S.state.builders;
  const t = now();
  const busy = b.jobs.filter(j => j.ends_at > t).length;
  const free = Math.max(0, b.total - busy);
  $("#free-figure").innerHTML = `${free} <small>/ ${b.total}</small>`;
  $("#free-caption").textContent = free === 1 ? "builder free" : "builders free";
  $("#builders-source").textContent = b.source === "counter" ? "total from the in-game counter"
    : "total from the export (an idle extra builder may be missing)";
  $("#builder-meter").innerHTML = Array.from({ length: b.total }, (_, i) =>
    `<span class="${i < busy ? "busy" : ""}"></span>`).join("");
  const nxt = busy < b.total ? t : Math.min(...b.jobs.filter(j => j.ends_at > t).map(j => j.ends_at));
  const known = Number.isFinite(nxt);
  if (free || !known) delete $("#next-free").dataset.countdown;
  else $("#next-free").dataset.countdown = nxt;
  $("#next-free").textContent = free ? "Now" : known ? dur(nxt - t) : "–";
  $("#next-free-at").textContent = free ? `${free} idle` : known ? clock(nxt) : "";
  const active = b.jobs.filter(j => j.ends_at > t);
  const idle = Array.from({ length: free }, () => `<div class="lane idle">
      <div><div class="lane-name"><span class="status-label"><span class="dot good"></span>Idle</span></div>
      <div class="lane-sub">Ready for the next upgrade</div></div><div></div><div></div></div>`);
  $("#builder-lanes").innerHTML = idle.join("") + active.map(laneHTML).join("");
  $("#builder-table").innerHTML = `<table class="data"><thead><tr><th>Upgrade</th><th>Level</th>
      <th>Finishes</th><th class="num">Remaining</th></tr></thead><tbody>${
      active.map(j => `<tr><td>${esc(j.name)}${j.extra ? " (extra builder)" : ""}</td><td>${j.from} → ${j.to}</td>
        <td>${clock(j.ends_at)}</td><td class="num">${dur(j.ends_at - t)}</td></tr>`).join("")}</tbody></table>`;
}

function miniList(el, jobs, emptyText) {
  const t = now();
  const live = jobs.filter(j => j.ends_at > t);
  el.innerHTML = live.length ? live.map(j => {
    const start = j.duration ? j.ends_at - j.duration : null;
    const tags = (j.helper ? ' <span class="tag">assistant</span>' : "") + (j.extra ? ' <span class="tag">extra slot</span>' : "");
    return `<div class="mini-item"><div><b>${esc(j.name)}</b> <span class="muted">${j.from} → ${j.to}</span>${tags}</div>
      <div class="num" style="text-align:right"><span data-countdown="${j.ends_at}">–</span><div class="muted" style="font-size:12px">${clock(j.ends_at)}</div></div>
      <div class="bar"><i data-start="${start ?? ""}" data-end="${j.ends_at}"></i></div></div>`;
  }).join("") : `<div class="empty">${emptyText}</div>`;
}

function renderHelpers() {
  const t = now();
  const list = S.state.helpers || [];
  $("#helper-list").innerHTML = list.length ? list.map(h => {
    const ready = !h.ready_at || h.ready_at <= t;
    return `<div class="mini-item"><div><b>${esc(h.name)}</b> <span class="muted">level ${h.level}</span></div>
      <div style="text-align:right">${ready ? `<span class="status-label"><span class="dot good"></span>Ready</span>`
        : `<span class="num" data-countdown="${h.ready_at}">–</span><div class="muted" style="font-size:12px">ready ${clock(h.ready_at)}</div>`}</div></div>`;
  }).join("") : `<div class="empty">No helpers</div>`;
}

function renderFacts() {
  const st = S.state, s = st.snapshot;
  const rows = [
    ["Town Hall", s.town_hall], ["Hero Hall", s.hero_hall ?? "–"],
    ["Player", esc(s.tag)], ["Game version", esc(s.game_version || "?")],
    ["Last export", `${clock(s.exported_at)} <span class="muted">(${ago(s.exported_at)})</span>`],
    ["Screen counter", st.counter ? `${st.counter[0]} / ${st.counter[1]} free` : "not read"],
    ["Snapshots stored", nf.format(st.snapshots)],
  ];
  $("#village-facts").innerHTML = rows.map(([k, v]) =>
    `<div class="mini-item"><span class="muted">${k}</span><span>${v}</span></div>`).join("");
  $("#brand-sub").textContent = `${s.tag} · TH${s.town_hall}`;
  $("#foot").innerHTML = `Snapshot #${s.id}<br>Game ${esc(s.game_version || "?")}`;
}

function renderFreeHistory() {
  const pts = (S.history?.points || []).filter(p => p.free != null && p.total != null);
  const el = $("#free-history");
  if (pts.length < 2) { el.innerHTML = `<div class="empty">Appears after a few captures with the counter read.</div>`; return; }
  const W = 320, H = 120, L = 22, B = 18, total = Math.max(...pts.map(p => p.total));
  const t0 = pts[0].t, t1 = Math.max(pts[pts.length - 1].t, t0 + 1);
  const x = t => L + (t - t0) / (t1 - t0) * (W - L - 6);
  const y = v => 6 + (1 - v / total) * (H - B - 6);
  let d = `M${x(pts[0].t)},${y(pts[0].free)}`;
  for (let i = 1; i < pts.length; i++) d += `H${x(pts[i].t)}V${y(pts[i].free)}`;
  const grid = [0, Math.round(total / 2), total].map(v =>
    `<line x1="${L}" x2="${W - 6}" y1="${y(v)}" y2="${y(v)}" class="gridline"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`).join("");
  const series = getComputedStyle(document.documentElement).getPropertyValue("--series-1").trim();
  el.innerHTML = `<svg class="timeline" viewBox="0 0 ${W} ${H}" style="min-width:0">
    ${grid}<text x="${L}" y="${H - 3}">${clock(t0)}</text><text x="${W - 6}" y="${H - 3}" text-anchor="end">${clock(t1)}</text>
    <path d="${d}" fill="none" stroke="${series}" stroke-width="2" stroke-linejoin="round"/>
    <rect x="${L}" y="0" width="${W - L - 6}" height="${H - B}" fill="transparent" class="hit"/></svg>`;
  const svg = el.querySelector("svg");
  svg.querySelector(".hit").addEventListener("mousemove", ev => {
    const r = svg.getBoundingClientRect();
    const t = t0 + ((ev.clientX - r.left) / r.width * W - L) / (W - L - 6) * (t1 - t0);
    let best = pts[0];
    for (const p of pts) if (p.t <= t) best = p;
    showTip(`<b>${best.free} / ${best.total}</b> free<br><span class="muted">capture at ${clock(best.t)}</span>`, ev);
  });
  svg.querySelector(".hit").addEventListener("mouseleave", hideTip);
}

/* Gantt-style timeline. rows: [{label, sub, bars:[{start,end,cls,tip}]}] */
function timeline(el, rows, t0, t1, opts = {}) {
  const rowH = 30, top = 22, left = opts.labelWidth ?? 170, W = Math.max(el.clientWidth || 900, 640);
  const H = top + rows.length * rowH + 8;
  const x = t => left + (Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0) * (W - left - 10);
  // Ticks on local calendar boundaries: every 6 h for short spans, else days.
  const span = t1 - t0;
  const hours = span <= 4 * 86400 ? 6 : span <= 12 * 86400 ? 24 : 48;
  const d = new Date(t0 * 1000);
  d.setMinutes(0, 0, 0);
  d.setHours(hours >= 24 ? 0 : Math.floor(d.getHours() / hours) * hours);
  const wd = new Intl.DateTimeFormat(undefined, { weekday: "short" });
  let ticks = "";
  for (; d.getTime() / 1000 < t1; d.setHours(d.getHours() + hours)) {
    const t = d.getTime() / 1000;
    if (t <= t0) continue;
    const lab = hours >= 24 || d.getHours() === 0 ? wd.format(d) : timeFmt.format(d);
    const strong = d.getHours() === 0;
    ticks += `<line class="gridline" x1="${x(t)}" x2="${x(t)}" y1="${top - 4}" y2="${H - 6}" ${strong ? 'style="stroke:var(--axis)"' : ""}/>` +
             `<text x="${x(t) + 3}" y="12" ${strong ? 'style="fill:var(--text-secondary)"' : ""}>${esc(lab)}</text>`;
  }
  const bars = [], hits = [];
  rows.forEach((r, i) => {
    const y = top + i * rowH;
    bars.push(`<text class="lane-label" x="0" y="${y + 19}">${esc(r.label)}</text>`);
    if (r.sub) bars.push(`<text x="${left - 8}" y="${y + 19}" text-anchor="end">${esc(r.sub)}</text>`);
    r.bars.forEach(b => {
      if (b.end <= t0 || b.start >= t1) return;
      const bx = x(b.start), bw = Math.max(3, x(b.end) - bx);
      const k = hits.length;
      hits.push(b.tip);
      bars.push(`<rect class="mark" data-k="${k}" x="${bx}" y="${y + 6}" width="${bw}" height="${rowH - 12}" rx="4" fill="${b.fill}"/>`);
      // Full label if it fits, else the short one (e.g. the level), else none.
      const fits = s => s && s.length * 6.6 + 12 <= bw;
      const label = fits(b.text) ? b.text : fits(b.short) ? b.short : "";
      if (label) bars.push(`<text x="${bx + 6}" y="${y + 19}" style="fill:#fff;pointer-events:none">${esc(label)}</text>`);
    });
  });
  const nowX = x(now());
  el.innerHTML = `<svg class="timeline" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${ticks}
    ${bars.join("")}<line class="nowline" x1="${nowX}" x2="${nowX}" y1="${top - 6}" y2="${H - 6}"/></svg>`;
  $$("rect.mark", el).forEach(r => {
    r.addEventListener("mousemove", ev => showTip(hits[+r.dataset.k], ev));
    r.addEventListener("mouseleave", hideTip);
  });
}

function renderDashTimeline() {
  const t = now(), b = S.state.builders;
  const series = getComputedStyle(document.documentElement).getPropertyValue("--series-1").trim();
  const active = b.jobs.filter(j => j.ends_at > t);
  const rows = active.map((j, i) => ({
    label: j.name, sub: `${j.from}→${j.to}`,
    bars: [{ start: t, end: j.ends_at, fill: series, text: dur(j.ends_at - t), short: "",
             tip: `<b>${esc(j.name)}</b> ${j.from} → ${j.to}<br>finishes ${clock(j.ends_at)}<br><span class="muted">in ${dur(j.ends_at - t)}</span>` }] }));
  const idle = Math.max(0, b.total - active.length);
  for (let i = 0; i < idle; i++) rows.unshift({ label: "Idle builder", sub: "", bars: [] });
  timeline($("#dash-timeline"), rows, t - 1800, t + 72 * 3600);
}

function renderDashboard() {
  const st = S.state;
  if (!st) return;
  if (!st.snapshot) {
    $("#tiles").innerHTML = `<div class="card" style="grid-column:1/-1">No village snapshot yet. Press <b>Capture now</b>.</div>`;
    return;
  }
  renderTiles();
  renderBuilders();
  miniList($("#lab-list"), st.lab, "Laboratory idle");
  miniList($("#pet-list"), st.pets, "Pet House idle");
  renderHelpers();
  renderFacts();
  renderFreeHistory();
  renderDashTimeline();
  renderUnread(st.unread);
  const age = now() - st.snapshot.exported_at;
  $("#pill-updated").innerHTML = `<span class="dot ${age < 3 * 3600 ? "good" : "warning"}"></span><span>Updated ${ago(st.snapshot.exported_at)}</span>`;
  tick();
}

// ---------- per-second updates ----------
function tick() {
  const t = now();
  $$("[data-countdown-up]").forEach(el => { el.textContent = dur(t - +el.dataset.countdownUp); });
  $$("[data-countdown]").forEach(el => {
    const end = +el.dataset.countdown;
    el.textContent = end > t ? dur(end - t, end - t < 3600) : "done";
  });
  $$("i[data-end]").forEach(el => {
    const s = +el.dataset.start, e = +el.dataset.end;
    el.style.width = s ? `${Math.min(100, Math.max(2, (t - s) / (e - s) * 100)).toFixed(1)}%` : "4%";
  });
  // A job just finished: refresh the builder view without waiting for the poll.
  if (S.state?.builders && S.page === "dashboard") {
    const busy = S.state.builders.jobs.filter(j => j.ends_at > t).length;
    if (busy !== S._lastBusy) { S._lastBusy = busy; if (S._rendered) renderBuilders(); }
    S._rendered = true;
  }
}
setInterval(tick, 1000);

// ---------- notifications ----------
const SEV = {
  error: '<svg viewBox="0 0 24 24" fill="none" stroke="var(--critical)" stroke-width="2.2"><circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.5"/></svg>',
  warning: '<svg viewBox="0 0 24 24" fill="none" stroke="var(--warning)" stroke-width="2.2"><path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="var(--series-1)" stroke-width="2.2"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg>',
};
// Dry-run notes list screenshot paths (…\screens\dryrun\<file>.png); show
// them as thumbnails that open full size.
const SHOT_PATH = /[A-Za-z]:\\[^\n]*?\\screens\\dryrun\\([\w.-]+\.png)/g;
function detailHTML(detail) {
  const shots = [];
  const text = String(detail).replace(SHOT_PATH, (_, f) => { shots.push(f); return `[screenshot ${shots.length}]`; });
  const thumbs = shots.map(f => {
    const url = "/shots/dryrun/" + encodeURIComponent(f);
    return `<a href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="dialog screenshot" loading="lazy"></a>`;
  }).join("");
  return `<div class="detail">${esc(text)}</div>` + (thumbs ? `<div class="shots">${thumbs}</div>` : "");
}
function noteHTML(n, compactView) {
  return `<div class="note ${n.severity} ${n.acked_at ? "read" : ""}">${SEV[n.severity] || SEV.info}
    <div><div><span class="sev ${n.severity}">${n.severity}</span> <span class="muted">· ${esc(n.source)}</span></div>
      <div class="msg">${esc(n.message)}</div>${n.detail && !compactView ? detailHTML(n.detail) : ""}</div>
    <div style="text-align:right"><div class="when">${ago(n.created_at)}</div>
      ${!n.acked_at && !compactView ? `<button class="btn small ghost" data-ack="${n.id}">Mark read</button>` : ""}</div></div>`;
}
function renderUnread(n) {
  const b = $("#nav-unread");
  b.textContent = n; b.classList.toggle("hidden", !n);
}
async function loadNotesCount() {
  try {
    S.notes = await api("/api/notifications");
    renderUnread(S.notes.unread);
    const open = S.notes.items.filter(n => !n.acked_at).slice(0, 3);
    $("#dash-notes").innerHTML = open.length ? open.map(n => noteHTML(n, true)).join("")
      : `<div class="empty"><span class="status-label"><span class="dot good"></span>No open alerts</span></div>`;
  } catch { /* ignore */ }
}
async function loadNotes() {
  await loadNotesCount();
  const items = S.notes?.items || [];
  $("#notes-sub").textContent = `${S.notes?.unread || 0} unread · last ${items.length}`;
  $("#notes").innerHTML = items.length ? items.map(n => noteHTML(n)).join("")
    : `<div class="empty">Nothing yet. Errors from captures and the dashboard appear here.</div>`;
}
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-ack]");
  if (!b) return;
  try { await api("/api/notifications/ack", { id: +b.dataset.ack }); loadNotes(); } catch (err) { toast(err.message, "error"); }
});
$("#ack-all").addEventListener("click", async () => {
  try { await api("/api/notifications/ack", {}); loadNotes(); } catch (err) { toast(err.message, "error"); }
});

// ---------- queue ----------
async function loadQueue() {
  try {
    const [plan, cat] = await Promise.all([api("/api/plan"), S.catalog ? null : api("/api/catalog")]);
    S.plan = plan;
    if (cat) S.catalog = cat;
    renderQueue(); renderCatalog(); renderPlanTimeline();
  } catch (e) {
    $("#queue").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}
function costText(cost) {
  return Object.entries(cost || {}).filter(([, v]) => v).map(([r, v]) => {
    const k = resName(r);
    return `<span title="${nf.format(v)} ${esc(r)}">${resIcon(k)} ${compact(v)}</span>`;
  }).join(" ");
}
function renderQueue() {
  const items = S.plan.items;
  if (S.plan.truncated && !S._truncWarned) { S._truncWarned = true; toast("The preview covers the first 600 upgrades; later dates are not shown.", "error"); }
  const catById = Object.fromEntries((S.catalog?.items || []).map(c => [c.id, c]));
  if (!items.length) {
    $("#queue").innerHTML = `<div class="empty">The queue is empty. Add upgrades from the list on the right; the first item gets the next free builder.</div>`;
    return;
  }
  $("#queue").innerHTML = items.map((it, idx) => {
    const c = catById[it.data_id];
    const lo = c?.min_level ?? 1, cap = it.max_level ?? c?.max_level ?? lo;
    const opts = [`<option value="" ${it.target_level == null ? "selected" : ""}>Max (${cap})</option>`];
    for (let l = lo + 1; l < cap; l++) opts.push(`<option value="${l}" ${it.target_level === l ? "selected" : ""}>Level ${l}</option>`);
    const meta = it.steps
      ? [`${it.steps} upgrade${it.steps > 1 ? "s" : ""}`, `${dur(it.work_seconds)} of work`, costText(it.cost),
         it.done_at ? `done ≈ ${clock(it.done_at)}` : ""].filter(Boolean)
      : [it.enabled ? "nothing left to do at this target" : "disabled"];
    return `<div class="q-item ${it.enabled ? "" : "disabled"}" draggable="true" data-id="${it.id}">
      <div class="grip" title="Drag to reorder"><svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg></div>
      <div class="q-pos">${idx + 1}</div>
      <div class="q-main"><div class="q-title">${esc(it.name)}
          ${c ? `<span class="muted" style="font-weight:400;font-size:12.5px">×${c.count}, level ${c.min_level === c.levels?.[c.levels.length - 1] ? c.min_level : c.min_level + "–" + c.levels[c.levels.length - 1]}</span>` : ""}
          ${c?.upgrading ? `<span class="tag" title="Counted at its next level">${c.upgrading} upgrading</span>` : ""}</div>
        <div class="q-meta">${meta.map(m => `<span>${m}</span>`).join("")}</div></div>
      <div class="q-actions">
        <button class="icon-btn" data-move="-1" title="Move up" ${idx === 0 ? "disabled" : ""}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 15l6-6 6 6"/></svg></button>
        <button class="icon-btn" data-move="1" title="Move down" ${idx === items.length - 1 ? "disabled" : ""}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg></button>
        <select data-target title="Target level">${opts.join("")}</select>
        <label class="switch" title="Enabled"><input type="checkbox" data-enabled ${it.enabled ? "checked" : ""}><span></span></label>
        <button class="icon-btn" data-delete title="Remove from queue"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg></button>
      </div></div>`;
  }).join("");
}
async function planCall(path, body, okMsg) {
  try { await api(path, body); if (okMsg) toast(okMsg); await loadQueue(); }
  catch (e) { toast(e.message, "error"); await loadQueue(); }
}
$("#queue").addEventListener("click", e => {
  const item = e.target.closest(".q-item"); if (!item) return;
  const id = +item.dataset.id;
  const mv = e.target.closest("[data-move]");
  if (mv && !mv.disabled) {
    const ids = S.plan.items.map(i => i.id), i = ids.indexOf(id), j = i + +mv.dataset.move;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    planCall("/api/plan/order", { ids });
  }
  if (e.target.closest("[data-delete]")) planCall("/api/plan/delete", { id }, "Removed from the queue");
});
$("#queue").addEventListener("change", e => {
  const item = e.target.closest(".q-item"); if (!item) return;
  const id = +item.dataset.id;
  if (e.target.matches("[data-target]")) planCall("/api/plan/update", { id, target_level: e.target.value === "" ? null : +e.target.value });
  if (e.target.matches("[data-enabled]")) planCall("/api/plan/update", { id, enabled: e.target.checked });
});
// drag and drop
let dragId = null;
$("#queue").addEventListener("dragstart", e => {
  const it = e.target.closest(".q-item"); if (!it) return;
  dragId = +it.dataset.id; it.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
});
$("#queue").addEventListener("dragend", () => {
  dragId = null; $$(".q-item").forEach(x => x.classList.remove("dragging", "drop-before"));
});
$("#queue").addEventListener("dragover", e => {
  if (dragId == null) return;
  e.preventDefault();
  $$(".q-item").forEach(x => x.classList.remove("drop-before"));
  const over = e.target.closest(".q-item");
  if (over && +over.dataset.id !== dragId) over.classList.add("drop-before");
});
$("#queue").addEventListener("drop", e => {
  e.preventDefault();
  const over = e.target.closest(".q-item");
  if (dragId == null || !over || +over.dataset.id === dragId) return;
  const ids = S.plan.items.map(i => i.id).filter(i => i !== dragId);
  ids.splice(ids.indexOf(+over.dataset.id), 0, dragId);
  planCall("/api/plan/order", { ids });
});

function renderCatalog() {
  const items = S.catalog?.items || [];
  const q = $("#cat-search").value.trim().toLowerCase();
  const inQueue = new Set((S.plan?.items || []).map(i => i.data_id));
  const showMaxed = S.catKind === "maxed";
  const list = items.filter(c =>
    (showMaxed || c.steps_left > 0) &&
    (S.catKind === "all" || showMaxed || c.kind === S.catKind) &&
    (!q || c.name.toLowerCase().includes(q)));
  $("#cat-sub").textContent = `${items.filter(c => c.steps_left > 0).length} types can still be upgraded`;
  $("#cat-list").innerHTML = list.length ? list.map(c => {
    const hi = c.levels[c.levels.length - 1];
    const lvls = c.count > 1 && hi !== c.min_level ? `${c.min_level}–${hi}` : `${c.min_level}`;
    const nxt = c.next ? `next: ${resIcon(resName(c.next.resource))} ${compact(c.next.cost)} · ${dur(c.next.seconds)}` : "maxed for this Town Hall";
    const pips = c.max_level <= 30 ? `<div class="levelbar">${Array.from({ length: c.max_level }, (_, i) =>
      `<i class="${i < c.min_level ? "on" : ""}"></i>`).join("")}</div>` : "";
    return `<div class="cat-item"><div><div class="name">${esc(c.name)} <span class="muted" style="font-weight:400">×${c.count}</span>
        ${c.upgrading ? `<span class="tag">${c.upgrading} upgrading</span>` : ""}</div>
      <div class="meta">level ${lvls} of ${c.max_level} · ${c.steps_left} step${c.steps_left === 1 ? "" : "s"} left · ${nxt}</div>${pips}</div>
      <button class="btn small" data-add="${c.id}" ${c.steps_left ? "" : "disabled"}>${inQueue.has(c.id) ? "Add again" : "Add"}</button></div>`;
  }).join("") : `<div class="empty">Nothing matches.</div>`;
}
$("#cat-search").addEventListener("input", renderCatalog);
$("#cat-chips").addEventListener("click", e => {
  const c = e.target.closest(".chip"); if (!c) return;
  S.catKind = c.dataset.kind;
  $$(".chip").forEach(x => x.classList.toggle("active", x === c));
  renderCatalog();
});
$("#cat-list").addEventListener("click", e => {
  const b = e.target.closest("[data-add]"); if (!b) return;
  const c = S.catalog.items.find(x => x.id === +b.dataset.add);
  planCall("/api/plan/add", { data_id: +b.dataset.add }, `${c?.name || "Item"} added to the queue`);
});

function renderPlanTimeline() {
  const p = S.plan; if (!p) return;
  const cs = getComputedStyle(document.documentElement);
  const c1 = cs.getPropertyValue("--series-1").trim(), c2 = cs.getPropertyValue("--series-2").trim();
  const t = now();
  const lanes = Array.from({ length: p.builders }, (_, i) => ({ label: `Builder ${i + 1}`, bars: [] }));
  // Same lane order as the server's preview: idle lanes first, then jobs by end time.
  const jobs = p.current.filter(j => j.end > t);
  const idle = Math.max(0, p.builders - jobs.length);
  jobs.forEach((j, i) => lanes[idle + i]?.bars.push({ start: t, end: j.end, fill: c1, text: `${j.name} ${j.to}`, short: `${j.to}`,
    tip: `<b>${esc(j.name)}</b> ${j.from} → ${j.to}<br>in progress, finishes ${clock(j.end)}${j.extra ? "<br>extra builder" : ""}` }));
  p.schedule.forEach(s => lanes[s.lane]?.bars.push({ start: s.start, end: Math.max(s.end, s.start + 600), fill: c2,
    text: `${s.name} ${s.to}`, short: `${s.to}`,
    tip: `<b>${esc(s.name)}</b> ${s.from} → ${s.to}<br>${clock(s.start)} → ${clock(s.end)}<br>${resIcon(resName(s.resource))} ${nf.format(s.cost)} ${esc(s.resource)}` }));
  const end = Math.max(t + 3 * 86400, ...p.schedule.map(s => s.end), ...jobs.map(j => j.end));
  timeline($("#plan-timeline"), lanes, t - 3600, Math.min(end + 3600, t + 45 * 86400), { labelWidth: 90 });
}

// ---------- settings ----------
async function loadSettings() {
  try { S.settings = await api("/api/settings"); renderSettings(); }
  catch (e) { toast(e.message, "error"); }
}
function renderSettings() {
  const groups = {};
  S.settings.settings.forEach(s => (groups[s.group] ||= []).push(s));
  $("#settings").innerHTML = Object.entries(groups).map(([g, list]) => `
    <div class="card set-group" data-group="${esc(g)}">
      <div class="card-head"><h2>${esc(g)}</h2><span class="right">
        <button class="btn small primary" data-save="${esc(g)}">Save</button></span></div>
      ${list.map(s => {
        const live = s.phase <= LIVE_PHASE;
        let input;
        if (s.type === "bool") input = `<label class="switch"><input type="checkbox" data-key="${s.key}" ${s.value ? "checked" : ""}><span></span></label>`;
        else if (s.type === "int") input = `<input type="number" data-key="${s.key}" value="${esc(s.value)}" ${s.min != null ? `min="${s.min}"` : ""} ${s.max != null ? `max="${s.max}"` : ""}>`;
        else if (s.type === "time") input = `<input type="time" data-key="${s.key}" value="${esc(s.value)}">`;
        else input = `<input type="text" data-key="${s.key}" value="${esc(s.value)}" placeholder="${esc(s.default)}">`;
        return `<div class="set-row"><div><div class="set-label">${esc(s.label)}
            <span class="phase ${live ? "live" : ""}">${live ? "in effect" : "from Phase " + s.phase}</span>
            ${s.custom ? `<span class="custom-mark">changed</span>` : ""}</div>
          <div class="set-help">${esc(s.help)} <span class="muted">Default: ${esc(String(s.default) || "none")}.</span></div></div>
          <div class="set-input">${input}</div></div>`;
      }).join("")}
    </div>`).join("");
}
$("#settings").addEventListener("click", async e => {
  const b = e.target.closest("[data-save]"); if (!b) return;
  const card = b.closest(".set-group"), body = {};
  $$("[data-key]", card).forEach(inp => {
    const s = S.settings.settings.find(x => x.key === inp.dataset.key);
    const v = s.type === "bool" ? inp.checked : s.type === "int" ? Number(inp.value) : inp.value;
    if (v !== s.value) body[s.key] = v;
  });
  if (!Object.keys(body).length) { toast("Nothing changed"); return; }
  try { await api("/api/settings", body); toast("Settings saved"); loadSettings(); }
  catch (err) { toast(err.message, "error"); }
});

$("#btn-dryrun").addEventListener("click", async () => {
  try {
    await api("/api/actions/dryrun", {});
    toast("Dry run started: it opens the upgrade dialog, reads it and closes it (never confirms). The result appears under Alerts in a few minutes.");
    S.status = { ...(S.status || {}), capture_running: true };
    renderStatus();
  } catch (e) { toast(e.message, "error"); }
});

// ---------- automation ----------
const ACTION_LABEL = { upgrade: "Upgrade", dry_run: "Dry run", farm: "Farm" };
function renderAuto() {
  const a = S.auto; if (!a) return;
  const o = a.orchestrator || {};
  // top-bar pill (every page)
  const pill = $("#pill-auto");
  let cls = "good", txt;
  if (!a.alive) { cls = "critical"; txt = "Automation not running"; }
  else if (o.running) { cls = "good pulse"; txt = "Session running"; }
  else if (a.paused) { cls = "warning"; txt = "Automation paused"; }
  else txt = o.next_due ? `Next session ${o.next_due <= now() ? "now" : "in " + dur(o.next_due - now())}` : "Automation on";
  pill.innerHTML = `<span class="dot ${cls}"></span><span>${txt}${a.dry_run && a.alive ? " · dry run" : ""}</span>`;
  if (S.page !== "automation") return;

  $("#auto-dead").classList.toggle("hidden", a.alive);
  $("#auto-on").checked = !a.paused;
  $("#auto-live").checked = !a.dry_run;
  $("#auto-farm").checked = a.farm_enabled;
  $("#auto-mode-chip").textContent = a.dry_run ? "dry run" : "LIVE";
  $("#auto-mode-chip").className = "phase" + (a.dry_run ? "" : " live");
  $("#auto-sub").textContent = a.alive ? `automation process running (pid ${o.pid})` : "automation process not running";
  const nx = $("#auto-next");
  if (o.running) { nx.textContent = "Running now"; $("#auto-next-why").textContent = `${o.running.trigger}, since ${clock(o.running.since)}`; delete nx.dataset.countdown; }
  else if (a.paused) { nx.textContent = "Paused"; $("#auto-next-why").textContent = "turn Automation on to schedule sessions"; delete nx.dataset.countdown; }
  else if (o.next_due) { nx.dataset.countdown = o.next_due; nx.textContent = o.next_due <= now() ? "now" : dur(o.next_due - now());
    $("#auto-next-why").textContent = `${clock(o.next_due)} · ${o.next_reason}`; }
  else { nx.textContent = "–"; $("#auto-next-why").textContent = ""; delete nx.dataset.countdown; }
  $("#auto-run").disabled = !!o.running || a.pending_commands > 0;
  $("#auto-run").textContent = a.pending_commands > 0 ? "Session requested…" : "Run a session now";

  const last = a.sessions[0];
  const rows = [
    ["Process", a.alive ? `<span class="status-label"><span class="dot good"></span>running</span>` : `<span class="status-label"><span class="dot critical"></span>stopped</span>`],
    ["Heartbeat", o.heartbeat ? ago(o.heartbeat) : "–"],
    ["Mode", a.dry_run ? "dry run (never confirms)" : "<b>live upgrades</b>"],
    ["Last session", last ? `#${last.id} ${esc(last.status)}, ${ago(last.ended_at || last.started_at)}` : "none yet"],
  ];
  $("#auto-now").innerHTML = rows.map(([k, v]) => `<div class="mini-item"><span class="muted">${k}</span><span>${v}</span></div>`).join("");

  const open = new Set($$(".sess.open").map(e => +e.dataset.id));
  $("#auto-sessions").innerHTML = a.sessions.length ? a.sessions.map((s, i) => {
    const t1 = s.ended_at || now();
    const acts = s.actions.length ? `<table class="data"><thead><tr><th>Action</th><th>Target</th><th>Result</th><th>Detail</th></tr></thead><tbody>${
      s.actions.map(x => `<tr><td>${ACTION_LABEL[x.kind] || esc(x.kind)}</td>
        <td>${esc(x.name)}${x.from_level != null ? ` ${x.from_level} → ${x.from_level + 1}` : ""}</td>
        <td><span class="status-chip ${x.status}">${esc(x.status.replace("_", " "))}</span></td>
        <td>${detailHTML(x.detail || "")}</td></tr>`).join("")}</tbody></table>` : "";
    return `<div class="sess ${open.has(s.id) || (i === 0 && !S._sessTouched) ? "open" : ""}" data-id="${s.id}">
      <div class="sess-head"><b>#${s.id}</b><span class="status-chip ${s.status}">${esc(s.status)}</span>
        <span>${esc(s.trigger)}</span><span class="when">${clock(s.started_at)} · ${dur(t1 - s.started_at)}</span></div>
      <div class="sess-body"><div class="sess-lines">${esc(s.summary || (s.status === "running" ? "in progress…" : ""))}</div>${acts}</div></div>`;
  }).join("") : `<div class="empty">No sessions yet.</div>`;
}
async function loadAuto() {
  try { S.auto = await api("/api/automation"); renderAuto(); } catch { /* keep */ }
}
$("#auto-sessions").addEventListener("click", e => {
  const h = e.target.closest(".sess-head"); if (!h) return;
  S._sessTouched = true; h.parentElement.classList.toggle("open");
});
async function autoSet(body, msg) {
  try { await api("/api/automation", body); toast(msg); } catch (e) { toast(e.message, "error"); }
  loadAuto();
}
$("#auto-on").addEventListener("change", e => autoSet({ paused: !e.target.checked },
  e.target.checked ? "Automation on" : "Automation paused"));
$("#auto-farm").addEventListener("change", e => autoSet({ farm_enabled: e.target.checked },
  e.target.checked ? "Farming for upgrades on" : "Farming for upgrades off"));
$("#auto-live").addEventListener("change", e => {
  if (e.target.checked && !confirm("Turn on LIVE upgrades?\n\nThe bot will press Confirm on upgrades from your queue and spend gold/elixir/dark elixir. It never spends gems; a gem drop pauses automation.")) {
    e.target.checked = false; return;
  }
  autoSet({ dry_run: !e.target.checked }, e.target.checked ? "Live upgrades ON" : "Back to dry run");
});
$("#auto-run").addEventListener("click", async () => {
  try {
    const r = await api("/api/automation/run", {});
    toast(r.orchestrator_running ? "Session requested: it starts within ~20 s." : "Requested, but the automation process isn't running.", r.orchestrator_running ? "ok" : "error");
  } catch (e) { toast(e.message, "error"); }
  loadAuto();
});

// ---------- attack / farming ----------
$("#farm-modes").addEventListener("change", e => {
  if (!e.target.matches("input[type=radio]")) return;
  $$("#farm-modes .mode").forEach(m => m.classList.toggle("active", m.contains(e.target)));
});
function farmGoalText(p) {
  if (!p) return "";
  if (p.mode === "attacks") return `${p.attacks} attacks`;
  if (p.mode === "full") return { both: "gold and elixir full", gold: "gold full", elixir: "elixir full", any: "gold or elixir full" }[p.full];
  return "reach " + [["gold", "gold"], ["elixir", "elixir"], ["dark_elixir", "dark elixir"]]
    .filter(([k]) => p[k]).map(([k, label]) => `${compact(p[k])} ${label}`).join(", ");
}
function farmProgress(r) {
  const p = r.params, s = r.state || {};
  if (p.mode === "attacks") return Math.min(1, r.attacks / p.attacks);
  if (p.mode === "full") {
    const g = s.gold_fill ?? 0, e = s.elixir_fill ?? 0;
    return p.full === "gold" ? g : p.full === "elixir" ? e : p.full === "any" ? Math.max(g, e) : Math.min(g, e);
  }
  const parts = [["gold", "gold"], ["elixir", "elixir"], ["dark_elixir", "dark_elixir"]]
    .filter(([k]) => p[k]).map(([k]) => Math.min(1, (s[k] ?? 0) / p[k]));
  return parts.length ? Math.min(...parts) : 0;
}
function gained(r, key) {
  const a = r.start_state?.[key], b = r.state?.[key];
  return a == null || b == null ? null : b - a;
}
function fillRow(label, cls, fill, value, gain) {
  const pct = fill == null ? null : Math.round(fill * 100);
  return `<div class="fill-row"><span>${label}</span>
    <div class="bar"><i class="${cls}" style="width:${pct ?? 0}%"></i></div>
    <span class="num" style="text-align:right">${value == null ? "–" : compact(value)}${pct == null ? "" : ` · ${pct}%`}
      ${gain ? `<div class="muted" style="font-size:12px">${gain > 0 ? "+" : ""}${compact(gain)}</div>` : ""}</span></div>`;
}
function renderFarm() {
  const f = S.farm; if (!f) return;
  const r = f.current;
  const pill = $("#pill-farm");
  pill.classList.toggle("hidden", !r);
  if (r) pill.querySelector("span:last-child").textContent =
    r.stop_requested ? "Stopping…" : `Farming · ${r.attacks} attack${r.attacks === 1 ? "" : "s"}`;
  $("#farm-start").disabled = !!r;
  $("#farm-actions").innerHTML = r && !r.stop_requested
    ? `<button class="btn small" id="farm-stop" title="Stops after the current attack">Stop after this attack</button>` : "";
  const last = f.runs.find(x => x !== r);
  if (!r) {
    $("#farm-current").innerHTML = `<div class="empty">No farming run active.${last ? ` Last run: <b>${esc(last.status)}</b>, ${esc(last.message)}.` : ""}</div>`;
  } else {
    const st = r.stop_requested ? "stopping" : r.status;
    const s = r.state || {};
    const started = r.started_at || r.created_at;
    const prog = farmProgress(r);
    $("#farm-current").innerHTML = `
      <div class="farm-head"><span class="status-chip ${st}">${st}</span><span class="farm-goal">Goal: ${esc(farmGoalText(r.params))}</span></div>
      <div class="farm-big num">${Math.round(prog * 100)}%</div>
      <div class="bar" style="height:10px;margin-top:6px"><i style="width:${(prog * 100).toFixed(1)}%"></i></div>
      <div class="farm-stats">
        <div><b class="num">${r.attacks}</b><span>attacks</span></div>
        <div><b class="num">${r.fails}</b><span>failed</span></div>
        <div><b class="num">${r.walls}</b><span>walls</span></div>
        <div><b class="num" data-countdown-up="${started}">${dur(now() - started)}</b><span>elapsed</span></div>
      </div>
      ${fillRow("Gold", "gold", s.gold_fill, s.gold, gained(r, "gold"))}
      ${fillRow("Elixir", "elixir", s.elixir_fill, s.elixir, gained(r, "elixir"))}
      ${fillRow("Dark elixir", "dark", s.dark_fill, s.dark_elixir, gained(r, "dark_elixir"))}
      <div class="muted" style="margin-top:8px;font-size:12.5px">${esc(r.message)} · limits ${r.params.max_attacks} attacks / ${r.params.max_minutes} min</div>`;
  }
  $("#farm-history").innerHTML = f.runs.length ? `<table class="data"><thead><tr><th>Started</th><th>Goal</th><th>Result</th>
      <th class="num">Attacks</th><th class="num">Gold</th><th class="num">Elixir</th><th class="num">Dark</th><th class="num">Time</th></tr></thead><tbody>${
    f.runs.map(x => {
      const t0 = x.started_at || x.created_at, t1 = x.ended_at || now();
      const g = k => { const v = gained(x, k); return v == null ? "–" : (v > 0 ? "+" : "") + compact(v); };
      return `<tr><td>${clock(t0)}</td><td>${esc(farmGoalText(x.params))}</td>
        <td><span class="status-chip ${x.status}">${x.status}</span> <span class="muted">${esc(x.message)}</span></td>
        <td class="num">${x.attacks}${x.fails ? ` <span class="muted">(+${x.fails} failed)</span>` : ""}</td>
        <td class="num">${g("gold")}</td><td class="num">${g("elixir")}</td><td class="num">${g("dark_elixir")}</td>
        <td class="num">${dur(t1 - t0)}</td></tr>`;
    }).join("")}</tbody></table>` : `<div class="empty">No runs yet.</div>`;
}
async function loadFarm() {
  try { S.farm = await api("/api/farm"); renderFarm(); } catch { /* keep */ }
}
$("#farm-start").addEventListener("click", async () => {
  const mode = $("#farm-modes input[name=mode]:checked").value;
  const num = id => $(id).value === "" ? null : Number($(id).value);
  const body = {
    mode, full: $("#f-full").value, attacks: num("#f-attacks"),
    gold: num("#f-gold"), elixir: num("#f-elixir"), dark_elixir: num("#f-dark"),
    ranked: $("#f-ranked").checked, event_troops: $("#f-event").checked,
    walls: $("#f-walls").checked, wall_every: num("#f-wall-every"),
    max_attacks: num("#f-max-attacks"), max_minutes: num("#f-max-minutes"),
  };
  try {
    await api("/api/farm/start", body);
    toast("Farming started. Progress updates here after every attack.");
    loadFarm(); loadStatus();
  } catch (e) { toast(e.message, "error"); }
});
$("#farm-actions").addEventListener("click", async e => {
  if (!e.target.closest("#farm-stop")) return;
  try { await api("/api/farm/stop", {}); toast("Stopping after the current attack."); loadFarm(); }
  catch (err) { toast(err.message, "error"); }
});

// ---------- builder table toggle ----------
$("#toggle-builder-table").addEventListener("click", e => {
  const tbl = $("#builder-table"), lanes = $("#builder-lanes");
  const showTable = tbl.classList.contains("hidden");
  tbl.classList.toggle("hidden", !showTable); lanes.classList.toggle("hidden", showTable);
  e.target.textContent = showTable ? "Lanes view" : "Table view";
});

// ---------- boot ----------
addEventListener("resize", () => { if (S.state) renderDashTimeline(); if (S.page === "queue") renderPlanTimeline(); });
addEventListener("hashchange", () => go(location.hash.slice(1)));
(async function boot() {
  go(location.hash.slice(1) || "dashboard");
  await Promise.all([loadState(), loadHistory(), loadStatus(), loadNotesCount()]);
  setInterval(loadState, 15000);
  setInterval(loadStatus, 10000);
  setInterval(loadNotesCount, 30000);
  setInterval(loadHistory, 300000);
  setInterval(() => { if (S.page === "queue") loadQueue(); }, 60000);
  loadFarm(); loadAuto();
  setInterval(() => loadFarm(), 5000);   // cheap; keeps the "Farming" pill live on every page
  setInterval(() => loadAuto(), 8000);
})();

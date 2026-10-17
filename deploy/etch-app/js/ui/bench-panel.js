// Benchmarks: a round button just above the "?" button that opens a small panel with the latest build's numbers.
// Nothing shows until the person presses it. The dock hands its benchmarks card here instead of the chat
// (dock.bench), and physics() adds three industry checks computed from the build's own layout.json + sim.json:
//
//   IPC-2221 trace heating   temperature rise of the narrowest supply trace at the peak supply current
//                            (I = k · ΔT^0.44 · A^0.725, k 0.048 outer / 0.024 inner, A in mil², 1 oz copper)
//   IPC-2221B spacing        the smallest gap between copper of different nets against Table 6-1 B2
//                            (external, uncoated) for the supply voltage
//   Supply IR drop           I · ρL / (w·t) along the supply traces (copper 1.72e-8 Ω·m at 20 °C, 35 µm)
import { benchCard } from "./trace.js";
import { squish } from "./goo.js";

const CSS = `
.bp-fab { position: fixed; right: 24px; bottom: 84px; width: 48px; height: 48px; z-index: 56; }
.bp-fab.bp-away { opacity: 0; pointer-events: none; }
.bp-btn { position: absolute; inset: 0; border: 0; border-radius: 50%; padding: 0; cursor: pointer; display: grid; place-items: center;
  background: #fff; color: var(--ag-ink, #171b24); box-shadow: 0 6px 10px rgba(22, 25, 29, .16), 0 1px 2px rgba(22, 25, 29, .14);
  -webkit-tap-highlight-color: transparent; transition: translate .2s cubic-bezier(.22, .9, .3, 1); }
.bp-btn:hover { translate: 0 -2px; }
.bp-btn:focus-visible { outline: 2px solid var(--ag-accent, #2f6bff); outline-offset: 3px; }
.bp-btn svg { width: 20px; height: 20px; }
.bp-btn[aria-expanded="true"] { background: var(--ag-ink, #171b24); color: #fff; }
.bp-new { position: absolute; top: 3px; right: 3px; width: 9px; height: 9px; border-radius: 50%; background: var(--ag-accent, #2f6bff); box-shadow: 0 0 0 2px #fff; }
.bp-new[data-tone="fail"] { background: var(--ag-fail, #d64545); }
.bp-pan { position: fixed; z-index: 57; width: min(340px, calc(100vw - 32px)); max-height: min(70vh, 560px); overflow: auto;
  display: flex; flex-direction: column; gap: 8px; border-radius: 18px; }
.bp-pan[hidden] { display: none; }
.bp-empty { padding: 12px 14px; font: 500 13px/1.4 var(--ag-sans, system-ui); color: var(--ag-muted, #6c717c); }
.bp-src { padding: 0 2px; font: 500 11px/1.4 var(--ag-sans, system-ui); color: var(--ag-faint, #a0a4ad); }
`;
const ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 16V11M8 16V6M12 16V9M16 16V4"/></svg>';

let fab = null, btn = null, pan = null, dot = null, body = null, cur = null;
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

function mount() {
  if (fab) return;
  const s = el("style"); s.textContent = CSS; document.head.append(s);
  fab = el("div", "bp-fab");
  btn = el("button", "bp-btn", ICON);
  btn.type = "button";
  btn.title = "Benchmarks";
  btn.setAttribute("aria-label", "Benchmarks");
  btn.setAttribute("aria-expanded", "false");
  dot = el("i", "bp-new"); dot.hidden = true;
  fab.append(btn, dot);
  pan = el("div", "bp-pan"); pan.hidden = true;
  pan.setAttribute("role", "region"); pan.setAttribute("aria-label", "Benchmarks");
  body = el("div", "ag-card bp-empty", "No benchmarks yet. Build or change a board with Etch and its numbers show up here.");
  pan.append(body);
  document.body.append(fab, pan);
  squish(fab);
  btn.addEventListener("click", () => toggle());
  for (const t of ["pointerdown", "click", "dblclick", "contextmenu"]) { fab.addEventListener(t, (e) => e.stopPropagation()); pan.addEventListener(t, (e) => e.stopPropagation()); }
  pan.addEventListener("wheel", (e) => e.stopPropagation(), { passive: true });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pan.hidden) toggle(false); });
  document.addEventListener("pointerdown", (e) => { if (!pan.hidden && !pan.contains(e.target) && !fab.contains(e.target)) toggle(false); });
  place();
  addEventListener("resize", () => requestAnimationFrame(place));
  document.addEventListener("sh:placed", () => requestAnimationFrame(place));
  setTimeout(place, 900); setTimeout(place, 2600);
}

function toggle(v = pan.hidden) {
  pan.hidden = !v;
  btn.setAttribute("aria-expanded", v ? "true" : "false");
  if (v) { dot.hidden = true; place(); }
}

// straight above the "?" button, the panel opens to its left-top, inside the window
function place() {
  if (!fab) return;
  const q = document.querySelector(".sh-fab");
  const r = q && q.getClientRects().length && !q.classList.contains("sh-away") ? q.getBoundingClientRect() : null;
  const right = r ? Math.round(innerWidth - r.right) : innerWidth <= 560 ? 16 : 24;
  const top = r ? Math.round(r.top - 12 - 48) : innerHeight - 84 - 48;
  Object.assign(fab.style, { right: right + "px", top: top + "px", bottom: "auto" });
  if (!pan.hidden) {
    const gap = 10, h = pan.offsetHeight;
    const t = Math.max(16, Math.min(top + 48 - h, innerHeight - 16 - h));
    Object.assign(pan.style, { right: right + 48 + gap + "px", top: t + "px" });
    if (innerWidth - (right + 48 + gap) < pan.offsetWidth + 16) Object.assign(pan.style, { right: "16px", top: Math.max(16, top - gap - h) + "px" });
  }
}

function fresh(tone) {
  if (pan.hidden) { dot.hidden = false; dot.dataset.tone = tone || ""; }
  place();
}

// ---- the dock's card for one run: only the newest run is kept ----
function card(id) {
  mount();
  if (cur && cur.id === id) return cur.card;
  const c = benchCard();
  const phys = el("div", "bp-phys");
  const src = el("div", "bp-src", "");
  pan.replaceChildren(c.el, phys, src);
  body = null;
  cur = { id, card: c, phys, src };
  // the dot follows the card's verdict
  const row = c.row.bind(c);
  c.row = (k, p) => { const r = row(k, p); fresh(p?.status === "fail" ? "fail" : ""); return r; };
  fresh();
  return c;
}

// ---- physics from layout.json + sim.json ----
const RAIL = /^(\+?\d+V\d*|\+?\d*V\d+|VCC|VDD|VIN|VBUS|VBAT|VSYS|V\+|PWR|\+?[35]V3?|\+?12V|5V|3V3)$/i;
const OZ_MM = 0.035, MM_MIL = 39.3701, RHO = 1.72e-8;
// IPC-2221B Table 6-1 B2 (external conductors, uncoated, sea level to 3050 m), peak volts -> mm
function ipcSpacing(v) {
  if (v <= 30) return 0.1;
  if (v <= 150) return 0.6;
  if (v <= 300) return 1.25;
  if (v <= 500) return 2.5;
  return 0.005 * v;
}
function segDist(a, b, c, d) {
  const pt = (p, q, r) => { const dx = r[0] - q[0], dy = r[1] - q[1], L = dx * dx + dy * dy; const t = L ? Math.max(0, Math.min(1, ((p[0] - q[0]) * dx + (p[1] - q[1]) * dy) / L)) : 0; return Math.hypot(p[0] - q[0] - t * dx, p[1] - q[1] - t * dy); };
  return Math.min(pt(a, c, d), pt(b, c, d), pt(c, a, b), pt(d, a, b));
}
export function physics(layout, sim) {
  const tracks = (layout?.tracks || []).filter((t) => Array.isArray(t.pts) && t.pts.length > 1 && +t.width > 0);
  const out = [];
  const iA = +(sim?.supply_ma_peak ?? sim?.supply_ma_avg) / 1000, v = +(sim?.supply_v ?? 5);
  const rail = tracks.filter((t) => RAIL.test(String(t.net || "").replace(/^\//, "")));
  if (rail.length && isFinite(iA) && iA > 0) {
    // trace heating: the narrowest supply trace carries the whole supply current
    let worst = null;
    for (const t of rail) {
      const inner = /^In\d/.test(String(t.layer || ""));
      const A = (+t.width * MM_MIL) * (OZ_MM * MM_MIL);
      const dT = Math.pow(iA / ((inner ? 0.024 : 0.048) * Math.pow(A, 0.725)), 1 / 0.44);
      if (!worst || dT > worst) worst = dT;
    }
    out.push(["ipc2221_dt", { label: "IPC-2221 trace rise", value: worst, unit: "°C", max: 10 }]);
    // IR drop along the supply traces, all in series (the worst case)
    let R = 0;
    for (const t of rail) for (let i = 1; i < t.pts.length; i++) R += (RHO * Math.hypot(t.pts[i][0] - t.pts[i - 1][0], t.pts[i][1] - t.pts[i - 1][1]) * 1e-3) / ((+t.width * 1e-3) * (OZ_MM * 1e-3));
    out.push(["ir_drop", { label: "Supply IR drop", value: iA * R * 1000, unit: "mV", max: Math.round(v * 0.02 * 1000) }]);
  }
  // spacing: the closest copper of two different nets
  if (tracks.length > 1 && tracks.length < 4000) {
    let gap = Infinity;
    const segs = [];
    for (const t of tracks) for (let i = 1; i < t.pts.length; i++) segs.push([t.pts[i - 1], t.pts[i], +t.width / 2, t.net, t.layer]);
    for (let i = 0; i < segs.length; i++) for (let j = i + 1; j < segs.length; j++) {
      const a = segs[i], b = segs[j];
      if (a[3] === b[3] || (a[4] && b[4] && a[4] !== b[4])) continue;
      const g = segDist(a[0], a[1], b[0], b[1]) - a[2] - b[2];
      if (g < gap) gap = g;
    }
    if (isFinite(gap)) out.push(["ipc2221b_gap", { label: "IPC-2221B spacing", value: Math.max(0, gap), unit: "mm", min: ipcSpacing(v) }]);
  }
  return out;
}

export function addPhysics(id, layout, sim) {
  if (!cur || cur.id !== id) return;
  const rows = physics(layout, sim);
  if (!rows.length) return;
  const c = benchCard();
  c.el.querySelector(".ag-bn-t").textContent = "Industry standards";
  for (const [k, p] of rows) c.row(k, { ...p, status: (p.min == null || p.value >= p.min) && (p.max == null || p.value <= p.max) ? "pass" : "fail" });
  cur.phys.replaceChildren(c.el);
  cur.src.textContent = `1 oz copper, ${+(sim?.supply_v ?? 5)} V supply at ${+(sim?.supply_ma_peak ?? sim?.supply_ma_avg ?? 0)} mA peak.`;
  fresh(c.summary.failed ? "fail" : "");
}

export const benchPanel = { mount, card, addPhysics };
if (typeof document !== "undefined") (document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", mount) : mount());

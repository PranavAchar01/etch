// Etch app (2026-10-16): the public, talk-to-it version of the approved studio stage. Base: studio/rehearsal-v4.js
// (same stage, camera, panels, colours, fonts, mascot, smooth drop-ins, why-cards, captions) in live mode only;
// pixel background from studio/index.html. Replay, capture, cues and the speed overlay are removed.
import { createViewer } from "./viewer3d.js?v=12";
const Q = new URLSearchParams(location.search), REC = false, LIVE = true;
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const ease = (v) => { v = clamp(v); return v < 0.5 ? 4 * v * v * v : 1 - Math.pow(-2 * v + 2, 3) / 2; };
const span = (T, a, b) => ease((T - a) / (b - a)), lin = (T, a, b) => clamp((T - a) / (b - a));
const win = (T, a, b, f = 0.4) => Math.min(lin(T, a, a + f), 1 - lin(T, b - f, b));
const lerp = (a, b, k) => a + (b - a) * k, $ = (id) => document.getElementById(id);
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const hash = (x, y) => { const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); };
const V = "#3dff6e", RED = "#ff5d5d", AMB = "#ffc857";
const eOut = (v) => 1 - Math.pow(1 - clamp(v), 3), sOut = (T, a, b) => eOut((T - a) / (b - a));
const W8 = new Map();
const put = (el, k, v) => { const m = W8.get(el) || W8.set(el, {}).get(el); if (m[k] !== v) { m[k] = v; if (k === "html") el.innerHTML = v; else if (k === "text") el.textContent = v; else if (k === "class") el.className = v; else el.style[k] = v; } };
document.body.classList.add("live");
// desktop: the 1920x1080 stage scaled to the window. phone (narrow): the board view scales to the width and the
// panels, captions and talk controls stack below it at full size
function fit() {
  // layout-viewport size: on phones innerWidth is the zoomed-out visual viewport (the 1920 px stage widens it)
  const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
  const mob = vw < 760 || (vw < 1000 && vh > vw);
  document.body.classList.toggle("mobile", mob);
  if (mob) { const s = vw / 1380; $("stage").style.transform = ""; $("view").style.transform = `scale(${s})`; $("vwrap").style.height = (874 * s).toFixed(1) + "px"; }
  else { const s = Math.min(vw / 1920, vh / 1080); $("stage").style.transform = `scale(${s})`; $("view").style.transform = ""; $("vwrap").style.height = ""; }
}
fit(); addEventListener("resize", fit);
const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const fmtT = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const load = (src) => new Promise((ok) => { const i = new Image(); i.crossOrigin = "anonymous"; i.onload = () => ok(i); i.onerror = () => ok(null); i.src = src; });
const bx = $("bc").getContext("2d");
bx.imageSmoothingQuality = "high";
const RECT = [60, 40, 1260, 794];
const short = (k) => String(k || "").replace(/^kicad:/, "").replace(/@.*$/, "").replace(/^[^:]*:/, "");

/* ---------- frame sets: real KiCad renders, one frame per placed part (same drop-in as v3) ---------- */
// SETS[k] = { files:[ImageBitmap], labels, bbox, crop, anchors, boxes, parts:{k:{x,y,w,h,body,shade}} }
const SETS = {};
const bitmap = async (im, c) => (im ? createImageBitmap(im, c[0], c[1], c[2] - c[0], c[3] - c[1]) : null);
// raw = the resting layout; otherwise the 2D pan / zoom (V2) applies, so the board, LED glows and card anchors move together
function place(set, r = RECT, raw = false) {
  const b = SETS[set].bbox, bw = b[2] - b[0], bh = b[3] - b[1], s = Math.min(r[2] / bw, r[3] / bh, LIVE ? 1.9 : 1.25);
  const x = r[0] + r[2] / 2 - (b[0] + bw / 2) * s, y = r[1] + r[3] / 2 - (b[1] + bh / 2) * s;
  return raw ? { s, x, y } : { s: s * V2.s, x: x * V2.s + V2.x, y: y * V2.s + V2.y };
}
/* ---------- 2D board: pan and zoom (2026-10-17, shown while parts land and when a build has no 3D board) ---------- */
// screen = base * s + (x, y), in the 1380 x 874 board canvas; V2 eases toward V2T
const V2 = { s: 1, x: 0, y: 0 }, V2T = { s: 1, x: 0, y: 0 };
let HINTED = false;
const v2on = () => !!st.setKey && !!SETS[st.setKey] && st.v3.a < 0.5 && st.idle < 0.5;
const v2moved = () => Math.abs(V2T.s - 1) > 1e-3 || Math.abs(V2T.x) > 0.5 || Math.abs(V2T.y) > 0.5;
function v2reset(now = false) { V2T.s = 1; V2T.x = 0; V2T.y = 0; if (now) Object.assign(V2, V2T); for (const k in PLACED) delete PLACED[k]; }
function v2keep() { // keep at least 40% of the view's width / height covered by the board area
  const s = V2T.s;
  V2T.x = clamp(V2T.x, 1380 * (0.4 - s), 1380 * 0.6); V2T.y = clamp(V2T.y, 874 * (0.4 - s), 874 * 0.6);
}
function v2zoom(c, k) {
  const s = clamp(V2T.s * k, 0.6, 5);
  V2T.x = c[0] - ((c[0] - V2T.x) * s) / V2T.s; V2T.y = c[1] - ((c[1] - V2T.y) * s) / V2T.s; V2T.s = s;
  v2keep(); HINTED = true; for (const k2 in PLACED) delete PLACED[k2];
}
{
  const cv = $("bc"), P2 = new Map();
  let pinch = null;
  const at = (e) => { const r = cv.getBoundingClientRect(); return [((e.clientX - r.left) * 1380) / r.width, ((e.clientY - r.top) * 874) / r.height]; };
  const two = () => { const [a, b] = [...P2.values()]; return { d: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, c: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] }; };
  // wheel / trackpad pinch zooms toward the cursor; the page only scrolls when the cursor is not on a movable board
  cv.addEventListener("wheel", (e) => { if (!v2on()) return; e.preventDefault(); v2zoom(at(e), Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : e.ctrlKey ? 0.01 : 0.0015))); }, { passive: false });
  cv.addEventListener("pointerdown", (e) => { if (!v2on()) return; cv.setPointerCapture(e.pointerId); P2.set(e.pointerId, at(e)); if (P2.size === 2) pinch = two(); HINTED = true; });
  cv.addEventListener("pointermove", (e) => {
    if (!P2.has(e.pointerId)) return;
    const p = at(e), o = P2.get(e.pointerId);
    P2.set(e.pointerId, p);
    if (P2.size === 1) { V2T.x += p[0] - o[0]; V2T.y += p[1] - o[1]; v2keep(); Object.assign(V2, V2T); }
    else if (P2.size === 2 && pinch) { const q = two(); V2T.x += q.c[0] - pinch.c[0]; V2T.y += q.c[1] - pinch.c[1]; v2zoom(q.c, q.d / pinch.d); pinch = q; }
  });
  const up = (e) => { if (!P2.delete(e.pointerId)) return; pinch = P2.size === 2 ? two() : null; for (const k in PLACED) delete PLACED[k]; };
  cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up);
  cv.addEventListener("dblclick", () => { if (v2on()) v2reset(); });
}
function blit(set, i, a, p = place(set)) {
  const S = SETS[set], im = S.files[i], c = S.crop; if (!im || a <= 0.002) return;
  bx.globalAlpha = a; bx.drawImage(im, p.x + c[0] * p.s, p.y + c[1] * p.s, im.width * p.s, im.height * p.s);
}
const last = (set) => SETS[set].files.length - 1;
function draw(set, idx, a = 1) {
  if (a <= 0.002 || !SETS[set]) return null;
  const n = SETS[set].files.length, f = clamp(idx, 0, n - 1), i0 = Math.floor(f), fr = f - i0, p = place(set);
  blit(set, i0, a, p);
  if (fr > 0.01 && SETS[set].files[i0 + 1]) blit(set, i0 + 1, a * fr * fr * (3 - 2 * fr), p);
  bx.globalAlpha = 1; return p;
}
const DROP = 0.3, LIFT = 42;
function build(set, idx, from, a = 1) {
  const n = last(set), p = place(set);
  if (idx <= from) { blit(set, from, a, p); bx.globalAlpha = 1; return; }
  if (idx >= n) { blit(set, n, a, p); bx.globalAlpha = 1; return; }
  const k = Math.floor(idx) + 1, u = idx - (k - 1), pt = SETS[set].parts[k];
  if (u >= DROP || !pt) { blit(set, u >= DROP ? k : k - 1, a, p); bx.globalAlpha = 1; return; }
  blit(set, k - 1, a, p);
  const c = SETS[set].crop, d = clamp(u / DROP), x = p.x + (pt.x - c[0] + c[0]) * p.s, y = p.y + pt.y * p.s, w = pt.w * p.s, h = pt.h * p.s;
  if (pt.shade) { bx.globalAlpha = a * d * d; bx.drawImage(pt.shade, x, y, w, h); }
  if (pt.body) { bx.globalAlpha = a * eOut(d / 0.45); bx.drawImage(pt.body, x, y - LIFT * p.s * (1 - eOut(d)), w, h); }
  bx.globalAlpha = 1;
}
// live explode: every part's cutout (from the frame differences) lifts off the bare board by its own height;
// copper and silk stay on the board. lift k = 0..1
function lifted(set, k, a = 1) {
  const S = SETS[set], p = place(set), n = last(set);
  blit(set, 0, a, p);
  const order = Object.keys(S.parts).map(Number).sort((x, y) => S.parts[x].y + S.parts[x].h - (S.parts[y].y + S.parts[y].h));
  for (const i of order) {
    const pt = S.parts[i]; if (!pt.body) continue;
    const up = (S.lift?.[S.labels[i]] ?? 30) * k;
    if (pt.shade) { bx.globalAlpha = a * (1 - 0.6 * k); bx.drawImage(pt.shade, p.x + pt.x * p.s, p.y + pt.y * p.s, pt.w * p.s, pt.h * p.s); }
    bx.globalAlpha = a; bx.drawImage(pt.body, p.x + pt.x * p.s, p.y + (pt.y - up) * p.s, pt.w * p.s, pt.h * p.s);
  }
  if (k < 0.01) blit(set, n, a, p);
  bx.globalAlpha = 1;
}
const anchorXY = (set, ref) => { const a = SETS[set]?.anchors[ref], p = SETS[set] && place(set); return a ? [p.x + a[0] * p.s, p.y + a[1] * p.s] : null; };
const boxXY = (set, ref) => { const b = SETS[set]?.boxes[ref], p = SETS[set] && place(set); return b && [p.x + b[0] * p.s, p.y + b[1] * p.s, p.x + b[2] * p.s, p.y + b[3] * p.s]; };
// cutouts in the browser (live mode): frame k differs from k-1 only where part k landed. Body = changed pixels
// that are not board green; shade = the rest of the change. Same split as tools/build_rehearsal_v3_parts.py, simpler.
async function cutouts(set) {
  const S = SETS[set], W = S.files[0].width, H = S.files[0].height;
  const cv = new OffscreenCanvas(W, H), g = cv.getContext("2d", { willReadFrequently: true });
  const px = (i) => { g.clearRect(0, 0, W, H); g.drawImage(S.files[i], 0, 0); return g.getImageData(0, 0, W, H).data; };
  let prev = px(0);
  for (let k = 1; k < S.files.length; k++) {
    const cur = px(k), body = new Uint8ClampedArray(cur.length), shade = new Uint8ClampedArray(cur.length);
    let x0 = W, y0 = H, x1 = 0, y1 = 0, sx = 0, sy = 0, n = 0;
    for (let i = 0; i < cur.length; i += 4) {
      const d = Math.max(Math.abs(cur[i] - prev[i]), Math.abs(cur[i + 1] - prev[i + 1]), Math.abs(cur[i + 2] - prev[i + 2]));
      if (d <= 10) continue;
      const q = i >> 2, x = q % W, y = (q / W) | 0, green = cur[i + 1] > cur[i] + 8 && cur[i + 1] > cur[i + 2] + 4;
      const dst = d > 30 && !green ? body : shade;
      dst[i] = cur[i]; dst[i + 1] = cur[i + 1]; dst[i + 2] = cur[i + 2]; dst[i + 3] = cur[i + 3];
      if (d > 30) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); sx += x; sy += y; n++; }
    }
    prev = cur;
    if (!n) continue;
    const ref = S.labels[k];
    S.anchors[ref] = [sx / n - S.crop[0], sy / n - S.crop[1]];
    S.boxes[ref] = [x0 - S.crop[0], y0 - S.crop[1], x1 - S.crop[0], y1 - S.crop[1]];
    const bw = x1 - x0 + 1, bh = y1 - y0 + 1;
    const mk = async (arr) => { const full = new ImageData(arr, W, H); g.clearRect(0, 0, W, H); g.putImageData(full, 0, 0); return createImageBitmap(cv, x0, y0, bw, bh); };
    S.parts[k] = { x: x0 - S.crop[0], y: y0 - S.crop[1], w: bw, h: bh, body: await mk(body), shade: await mk(shade) };
  }
}
async function loadSet(key, dir, fr, extra = {}) {
  const files = fr.frames.slice().sort((a, b) => a.i - b.i).map((f) => f.png);
  const ims = await Promise.all(files.map((f) => load(dir + f)));
  const ok = ims.filter(Boolean); if (!ok.length) return null;
  const bb = extra.bbox || alphaBox(ims[ims.length - 1]);
  const crop = [Math.max(0, bb[0] - 60), Math.max(0, bb[1] - 80), bb[2] + 60, bb[3] + 20];
  SETS[key] = { files: await Promise.all(ims.map((im) => bitmap(im, crop))), labels: fr.labels, bbox: bb, crop,
    anchors: extra.anchors || {}, boxes: extra.boxes || {}, parts: {}, lift: extra.lift };
  return SETS[key];
}
function alphaBox(im) {
  const c = new OffscreenCanvas(im.width, im.height), g = c.getContext("2d"); g.drawImage(im, 0, 0);
  const d = g.getImageData(0, 0, im.width, im.height).data; let x0 = im.width, y0 = im.height, x1 = 0, y1 = 0;
  for (let y = 0; y < im.height; y += 2) for (let x = 0; x < im.width; x += 2) if (d[(y * im.width + x) * 4 + 3] > 20) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
  return [x0, y0, x1, y1];
}

/* ---------- the agent's event stream -> panel state (shared by replay and live) ---------- */
// one "request" = everything between a lead:start and its lead:done (design, modify) or benchmarks:done
function newReq(e) { return { start: e, t0: e.t, kind: e.base ? "modify" : "design", cls: null, batches: [], filt: null, pickStart: null, pick: null, iters: [], change: null, exp: null, render: null, bench: [], benchDone: null, done: null, says: [] }; }
function feed(st, e) {
  const r = st.req;
  if (e.agent === "lead" && e.type === "start") { st.req = newReq(e); return; }
  if (e.agent === "lead" && e.type === "bench") { st.req = { ...newReq(e), kind: "bench" }; return; }
  if (!r) return;
  const k = `${e.agent}:${e.type}`;
  if (k === "classifier:start") r.cls = e;
  else if (k === "classifier:batch") r.batches.push(e);
  else if (k === "classifier:done") r.clsDone = e;
  else if (k === "filter:done") r.filt = e;
  else if (k === "picker:start") r.pickStart = e;
  else if (k === "picker:pick") r.pick = e;
  else if (k === "assembly:iteration" && e.phase === "place-route-check") r.iters.push({ n: e.iteration, t: e.t, gates: [], built: null, decide: null });
  else if (k === "assembly:built") { const it = r.iters.at(-1); if (it) it.built = e; }
  else if (k === "assembly:gate") { const it = r.iters.at(-1); if (it) it.gates.push(e); }
  else if (k === "assembly:iteration" && e.phase === "decide") { const it = r.iters.at(-1); if (it) it.decide = e; }
  else if (k === "picker:repair") { const it = r.iters.at(-1); if (it) it.repair = e; }
  else if (k === "assembly:change") r.change = e;
  else if (k === "export:done") r.exp = e;
  else if (k === "render:start") r.renderStart = e;
  else if (k === "render:done") r.render = e;
  else if (k === "benchmarks:check") r.bench.push(e);
  else if (k === "benchmarks:done") r.benchDone = e;
  else if (k === "lead:say") r.says.push(e);
  else if (k === "lead:done" || k === "lead:error") r.done = e;
  else if (k === "lead:cancelled") r.cancel = r.cancel || { t: (e.lt ?? 0) - (st.reqStart ?? 0) };
}
function stepLine(e) {
  const t = `<span class="t">${(+e.t || 0).toFixed(1)}s</span> `, a = `<b>${esc(e.agent)}</b> `;
  switch (`${e.agent}:${e.type}`) {
    case "lead:start": return t + a + (e.base ? "change request received" : "request received");
    case "lead:bench": return t + a + "company benchmark list received";
    case "classifier:start": return t + a + `${esc(clsName(e))} screens ${e.candidates} KiCad parts`;
    case "classifier:done": return t + a + (e.backend === "stored board" ? `built this before (${esc(e.stored)}), loading it` : `${e.yes} yes · ${e.no} no in ${(e.seconds * 1000).toFixed(e.seconds < 0.01 ? 1 : 0)} ms`);
    case "pieces:confirm": return t + a + `${e.parts.length} parts confirmed · ${e.drop.length} set aside`;
    case "filter:done": return t + a + `kept ${e.kept.length} · ${e.rejected.length} variants set aside`;
    case "picker:start": return t + a + `${esc(e.model)} picks from ${e.kept} kept parts`;
    case "picker:pick": return t + a + (e.recipe ? `${e.recipe === "stored board" ? "stored board" : "known circuit " + esc(e.recipe)}: ${e.parts.length} parts, no model call` : `picked ${e.parts.length} parts in ${e.seconds.toFixed(1)} s`);
    case "picker:repair": return t + a + `repaired design in ${e.seconds.toFixed(1)} s`;
    case "assembly:built": return t + a + `placed + routed ${e.board.w}x${e.board.h} mm, ${e.tracks} tracks, checked in ${e.seconds.toFixed(1)} s`;
    case "assembly:change": return t + a + `added ${esc(e.added.map((x) => x.split(" ")[0]).join(", "))} · kept ${e.kept} parts`;
    case "sourcing:start": return t + a + `not in KiCad's libraries: fetching ${esc(e.what)} (${esc(e.search)})`;
    case "sourcing:done": return t + a + (e.ok ? `added ${esc(e.key)} to the repo library · ${esc(e.provenance?.route || "")} · ${e.seconds.toFixed(1)} s` : `<span class="bad">could not fetch ${esc(e.what)}</span>`);
    case "export:done": return t + a + `KiCad project + ${e.gerbers.length} fab files + BOM → zip ${(e.zip_bytes / 1024).toFixed(0)} KB`;
    case "render:start": return t + a + `KiCad 3D render (${esc(e.quality)})`;
    case "render:done": return t + a + `${e.count} frames in ${e.seconds.toFixed(1)} s`;
    case "benchmarks:done": return t + a + `${e.passed} pass · ${e.failed} fail · ${e.unmeasured} can't measure · ${e.seconds.toFixed(1)} s`;
    case "lead:done": if (e.passed !== undefined) return t + a + `benchmarks: ${e.passed} pass · ${e.failed} fail`;
      return t + a + (e.ok ? `done in ${e.timings.total_s.toFixed(1)} s` : e.iterations ? `<span class="bad">stopped after ${e.iterations} ${e.iterations === 1 ? "try" : "tries"}</span>` : `<span class="bad">stopped before building</span>`);
    case "lead:error": return t + a + `<span class="bad">error</span>`;
    case "lead:cancelled": return `<b>lead</b> <span class="bad">build cancelled</span>`;
  }
  if (e.type === "llm" && e.agent !== "classifier") return t + a + `${esc(e.model)} ${e.seconds.toFixed(1)} s · ${e.tokens_in}+${e.tokens_out} tokens`;
  if (e.agent === "assembly" && e.type === "iteration" && e.phase === "place-route-check") return t + a + `iteration ${e.iteration}: place, route, check`;
  if (e.agent === "assembly" && e.type === "iteration" && e.phase === "decide") return t + a + (e.decision === "done" ? `iteration ${e.iteration} passes` : `<span class="bad">iteration ${e.iteration} failed</span> → ${esc(e.decision)}`);
  return null;
}
const PLAIN = { "DRC clean": "no short circuits, nothing too close", "all nets connected": "every wire connects",
  "ERC has no errors": "circuit drawing has no mistakes", "board matches schematic": "board matches the circuit drawing",
  "requested change present": "your change is on the saved board" };
function fmtV(g) {
  const v = g.value, c = String(g.check || "");
  if (v == null || typeof v === "string") return v == null ? "" : String(v).slice(0, 16);
  if (g.unit) return `${+v.toFixed(2)} ${g.unit}`;
  if (c.endsWith("blink_hz") || c.endsWith("load_hz")) return `${v.toFixed(1)} / sec`;
  if (c.endsWith("_ma") || c.includes("ma_")) return `${v.toFixed(1)} mA`;
  if (c.endsWith("duty")) return `${Math.round(v * 100)}%`;
  return v === 0 ? "0 problems" : String(+v.toFixed(2));
}
// gates panel rows for a request: the latest iteration's checks, or the company benchmark rows
function gateRows(r) {
  if (!r) return { title: "GATES", rows: [] };
  if (r.kind === "bench") return { title: "COMPANY BENCHMARKS", rows: r.bench.map((b) => ({ gate: b.label || b.said, ok: b.pass, value: b.pass == null ? "can't measure" : fmtV(b) })) };
  const it = r.iters.at(-1);
  if (!it) return { title: "GATES", rows: [] };
  const rows = it.gates.filter((g) => g.check || g.gate === "requested change present").map((g) => ({ gate: PLAIN[g.gate] || g.gate, ok: g.ok, value: fmtV(g) }));
  return { title: `GATES · TRY ${it.n}`, rows };
}
let GKEY = "";
function gatesDraw(G) {
  // room for 11 rows: when a board has more checks, failures go first so none is hidden; the count covers them all
  const list = (G.rows.length > 11 ? [...G.rows].sort((x, y) => (x.ok === false ? 0 : 1) - (y.ok === false ? 0 : 1)) : G.rows).slice(0, 11), gl = $("glist"), key = G.title + list.map((g) => g.gate).join("|");
  const h = $("gates").querySelector("h4").firstChild;
  if (h.nodeValue !== G.title + " ") h.nodeValue = G.title + " ";
  if (key !== GKEY) {
    GKEY = key;
    gl.innerHTML = list.map((g, i) => `<div class="g" style="animation-delay:${i * 45}ms"><div class="st"><b>·</b></div>${esc(g.gate)}<span class="val"></span></div>`).join("");
  }
  list.forEach((g, i) => {
    const row = gl.children[i], st = row.firstChild, cls = "st" + (g.ok === true ? " pass" : g.ok === false ? " fail" : g.value === "can't measure" ? " unk" : "");
    if (st.className !== cls) { st.className = cls; st.innerHTML = `<b style="animation-delay:${g.ok == null ? 0 : i * 55}ms">${g.ok === true ? "✓" : g.ok === false ? "✕" : g.value === "can't measure" ? "?" : "·"}</b>`; }
    put(row.lastChild, "text", g.value ?? "");
  });
  const np = G.rows.filter((g) => g.ok === true).length; put($("gsum"), "text", G.rows.length ? `${np} of ${G.rows.length}` : "");
}

/* ---------- the classifier / picker / assembly card ---------- */
const clsName = (c) => (c.backend === "decisions" ? `OpenAI Decisions (${c.model || "gpt-6-luna"})` : c.backend === "table" ? "parts table lookup" : c.model || "classifier");
const KCV = document.createElement("canvas"); KCV.width = 968; KCV.height = 184;
// marks: one square per candidate KiCad part; basic stock (plain R and C) is pre-kept and drawn grey
function marks(r, now) {
  const g = KCV.getContext("2d"), n = r.cls.candidates, stock = r.cls.basic_stock || 0, tot = n + stock, cols = 48, sz = 20, gap = 4;
  g.clearRect(0, 0, KCV.width, KCV.height);
  let i = 0;
  const cell = (fill, a) => { const x = (i % cols) * sz, y = Math.floor(i / cols) * sz; g.globalAlpha = a; g.fillStyle = fill; g.fillRect(x, y, sz - gap, sz - gap); i++; };
  for (let k = 0; k < stock; k++) cell("#6f8f78", 0.9);
  const seen = new Map(); for (const b of r.batches) for (const p of b.pieces) seen.set(p.k, [p.yes, b.t]);
  for (let k = 0; k < n; k++) {
    const b = r.batches.find((bb) => k >= bb.i0 && k < bb.i0 + bb.pieces.length);
    if (!b) { cell("rgba(61,255,110,.12)", 1); continue; }
    const p = b.pieces[k - b.i0], age = clamp((now - b.t) / 0.35);
    cell(p.yes ? V : RED, 0.25 + 0.75 * age * (p.yes ? 1 : 0.55));
  }
  g.globalAlpha = 1;
  return tot;
}
function kcard(r, now, show) {
  const el = $("kc");
  put(el, "opacity", show.toFixed(3)); put(el, "transform", `translateY(${((1 - show) * 10).toFixed(2)}px)`);
  if (show <= 0.01 || !r || !r.cls) return;
  let i0 = 0; for (const b of r.batches) { b.i0 = i0; i0 += b.pieces.length; }
  const yes = r.batches.reduce((s, b) => s + b.pieces.filter((p) => p.yes).length, 0), no = i0 - yes;
  const el_s = (r.clsDone ? r.clsDone.seconds : Math.max(0, now - r.cls.t)).toFixed(2);
  let html;
  if (!r.pickStart) {
    // stage 1: classifier marks streaming in
    html = `<h5>KICAD LIBRARY <span>${r.cls.symbol_libraries} libraries · ${r.cls.symbols.toLocaleString()} parts</span></h5>
      <div class="nm">CLASSIFIER</div><div class="sub">${r.cls.libraries_kept.length} part families → ${r.cls.candidates} candidates · ${r.cls.basic_stock || 0} basic R/C pre-kept</div>
      <div id="kcm"></div>
      <div class="cnt"><b>YES ${yes}</b><i>NO ${no}</i><em>${el_s} s</em></div>
      ${r.filt ? `<div class="row ok"><i>KEPT</i><span>${r.filt.kept.length} parts have a 3D model, pads and a SPICE model</span></div><div class="row"><i>REJECTED</i><span>${r.filt.rejected.length}: ${esc(topWhy(r.filt.rejected))}</span></div>` : ""}
      <div class="stand">${esc(clsName(r.cls))}</div>`;
  } else if (!r.iters.length) {
    html = `<h5>PICKER <span>${esc(r.pickStart.model)}</span></h5><div class="nm">${r.pick ? "PICKED" : "PICKING"}</div>
      <div class="sub">${r.pick ? `${r.pick.parts.length} parts from ${r.pickStart.kept} kept · ${r.pick.seconds.toFixed(1)} s` : `from ${r.pickStart.kept} kept parts · ${(now - r.pickStart.t).toFixed(1)} s`}</div>
      ${r.pick ? `<div class="parts">${r.pick.parts.map((p) => `<div><b>${esc(p.ref)}</b> ${esc(short(p.piece))} ${esc(p.value || "")}</div>`).join("")}</div>` : ""}`;
  } else {
    const it = r.iters.at(-1), fails = it.gates.filter((g) => g.ok === false), passing = it.decide && it.decide.decision === "done";
    html = `<h5>ASSEMBLY LOOP <span>try ${it.n}</span></h5><div class="nm">${passing ? "PASSES" : it.decide ? "REPAIRING" : it.built ? "CHECKING" : "PLACE · ROUTE · CHECK"}</div>
      ${r.iters.map((x) => `<div class="row ${x.decide ? (x.decide.decision === "done" ? "ok" : "bad") : ""}"><i>TRY ${x.n}</i><span>${x.built ? `${x.built.board.w}x${x.built.board.h} mm · ${x.built.tracks} tracks · ${x.built.seconds.toFixed(1)} s` : "placing and routing..."}${x.decide ? (x.decide.decision === "done" ? " · all checks pass" : ` · ${x.gates.filter((g) => g.ok === false).length} failed`) : ""}</span></div>`).join("")}
      ${fails.length && !passing ? `<div class="sub" style="color:var(--red)">${fails.slice(0, 3).map((g) => esc((PLAIN[g.gate] || g.gate).slice(0, 52))).join("<br>")}</div>` : ""}`;
  }
  put(el, "html", html);
  const m = el.querySelector("#kcm"); if (m && !m.firstChild) m.appendChild(KCV);
  if (m) marks(r, now);
}
function topWhy(rej) {
  const c = {}; for (const p of rej) { const w = p.why.replace(/ for this.*| for a .*| in the KiCad library/, ""); c[w] = (c[w] || 0) + 1; }
  return Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([w, n]) => `${n} ${w}`).join(", ");
}
function xcard(r, show) {
  const el = $("xc"); put(el, "opacity", show.toFixed(3)); put(el, "transform", `translateY(${((1 - show) * 10).toFixed(2)}px)`);
  if (show <= 0.01 || !r || !r.exp) return;
  const e = r.exp;
  put(el, "html", `<h5>EXPORT <span>${(e.zip_bytes / 1024).toFixed(0)} KB zip · ${e.seconds.toFixed(2)} s</span></h5>
    <b>${esc(e.project.join("  "))}</b><br>${e.gerbers.length} Gerber + drill files · BOM ${e.bom_lines} lines<br><span style="color:var(--mute)">${esc(e.zip.split("/").pop())}</span>`);
}

/* ---------- Etch mascot (unchanged from v3) ---------- */
let LEVEL = (T, who) => 0;
function drawEtch(cv, T, mode) {
  const g = cv.getContext("2d"), W = 32, H = 30, img = g.createImageData(W, H), d = img.data;
  const lv = LEVEL(T, "etch"), bob = Math.round(Math.sin(T * 3.2) * 0.9), blink = (T % 3.7) > 3.55, talk = mode === "talk" && lv > 0.22, wide = talk && lv > 0.6;
  const set = (x, y, c) => { if (x < 0 || y < 0 || x >= W || y >= H) return; const i = (y * W + x) * 4; d[i] = c[0]; d[i + 1] = c[1]; d[i + 2] = c[2]; d[i + 3] = 255; };
  const inB = (x, y) => { const dx = (x - 15.5) / 11.2, dy = (y - 17 - bob) / 10.2, a = Math.atan2(dy, dx); return dx * dx + dy * dy < 1 + 0.07 * (hash(Math.round(a * 5), 3) - 0.3) + 0.05 * Math.sin(a * 9 + T * 2); };
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    if (!inB(x, y)) continue;
    const edge = !inB(x + 1, y) || !inB(x - 1, y) || !inB(x, y + 1) || !inB(x, y - 1), lit = (x - 11) ** 2 + (y - 11 - bob) ** 2 < 18;
    set(x, y, edge ? [8, 58, 26] : lit ? [170, 255, 196] : hash(x, y + Math.floor(T * 4)) > 0.9 ? [44, 214, 92] : [61, 255, 110]);
  }
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) { const r = Math.hypot(x - 15.5, (y - 15 - bob) * 1.05); if (r > 12.2 && r < 13.6 && y < 13 + bob) set(x, y, [200, 210, 204]); }
  for (const c0 of [2, 27]) for (let y = 13; y < 21; y++) for (let x = c0; x < c0 + 3; x++) set(x, y + bob, x === c0 + 1 || y === 13 || y === 20 ? [110, 120, 116] : [200, 210, 204]);
  const ey = 16 + bob;
  for (const ex of [11, 19]) { if (blink) { set(ex, ey + 1, [3, 12, 6]); set(ex + 1, ey + 1, [3, 12, 6]); } else { for (let k = 0; k < 3; k++) { set(ex, ey + k, [3, 12, 6]); set(ex + 1, ey + k, [3, 12, 6]); } set(ex, ey, [255, 255, 255]); } }
  for (const bxx of [8, 22]) { set(bxx, ey + 4, [255, 140, 175]); set(bxx + 1, ey + 4, [255, 140, 175]); }
  if (talk) for (const [x, y] of wide ? [[14, 21], [15, 21], [16, 21], [17, 21], [15, 22], [16, 22], [15, 23], [16, 23]] : [[15, 21], [16, 21], [15, 22], [16, 22]]) set(x, y + bob, [3, 12, 6]);
  else { set(14, 20 + bob, [3, 12, 6]); set(15, 21 + bob, [3, 12, 6]); set(16, 21 + bob, [3, 12, 6]); set(17, 20 + bob, [3, 12, 6]); }
  g.putImageData(img, 0, 0);
}

/* ---------- part cards (v3 why-cards), placement clear of every part ---------- */
const OBST = [[0, 0, 700, 104], [760, 0, 1380, 112], [1150, 798, 1380, 874]];
const isect = (a, b) => Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) * Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
const PLACED = {};
function spot(key, set, ref, w, h, extra = [], pad = 10) {
  if (PLACED[key]) return PLACED[key];
  const parts = Object.keys(SETS[set].boxes).map((r) => boxXY(set, r)).filter(Boolean).map((b) => [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad]);
  const a = anchorXY(set, ref) || [690, 372], obst = OBST.concat(extra);
  let best = null;
  for (let y = 12; y + h <= 734; y += 8) for (let x = 12; x + w <= 1368; x += 8) {
    const r = [x, y, x + w, y + h];
    let sc = Math.hypot(x + w / 2 - a[0], y + h / 2 - a[1]);
    for (const b of parts) sc += isect(r, b) * 50;
    for (const b of obst) sc += isect(r, b) * 500;
    if (!best || sc < best.sc) best = { sc, r };
  }
  return (PLACED[key] = best.r);
}
let svg = "";
const CARDH = {};
function card(set, ref, o, why) {
  const el = $("pcard");
  if (!ref || o <= 0.01 || !why) { put(el, "display", "none"); return null; }
  const key = set + ":" + ref;
  put(el, "html", `<div class="ref">${esc(ref)}<small>${esc(why.name)}</small></div><div class="why">${esc(why.plain)}</div>`);
  put(el, "display", "");
  const r = spot(key, set, ref, 310, CARDH[key] || (CARDH[key] = el.offsetHeight)), a = anchorXY(set, ref);
  put(el, "opacity", o.toFixed(3)); put(el, "transform", `translateY(${((1 - o) * 8).toFixed(2)}px)`);
  put(el, "left", r[0] + "px"); put(el, "top", r[1] + "px");
  if (!a) return r;
  const px = clamp(a[0], r[0], r[2]), py = clamp(a[1], r[1], r[3]), lx = lerp(px, a[0], o), ly = lerp(py, a[1], o);
  svg += `<line x1="${px}" y1="${py}" x2="${lx}" y2="${ly}" stroke="${V}" stroke-width="2" stroke-opacity="${o}"/><circle cx="${a[0]}" cy="${a[1]}" r="${(6 * o).toFixed(2)}" fill="${V}" fill-opacity="${o}"/>`;
  return r;
}
function landed(set, idx, from) {
  const n = last(set);
  if (idx <= from) return null;
  const k = Math.min(n, Math.floor(idx) + 1), u = idx - (k - 1);
  if (u >= DROP || k === n && idx >= n) return { ref: SETS[set].labels[k], o: k === n && idx >= n ? 1 : sOut(u, DROP, DROP + 0.14) };
  if (k - 1 > from) return { ref: SETS[set].labels[k - 1], o: 1 - sOut(u, 0.12, 0.26) };
  return null;
}
const OVDEFS = `<defs><radialGradient id="glow"><stop offset="0" stop-color="#ff5050" stop-opacity=".95"/><stop offset="1" stop-color="#ff5050" stop-opacity="0"/></radialGradient><radialGradient id="glowb"><stop offset="0" stop-color="#5a8cff" stop-opacity=".95"/><stop offset="1" stop-color="#5a8cff" stop-opacity="0"/></radialGradient><radialGradient id="glowy"><stop offset="0" stop-color="#ffe7a0" stop-opacity=".95"/><stop offset="1" stop-color="#ffe7a0" stop-opacity="0"/></radialGradient></defs>`;
// LEDs light at the frequency and duty ngspice measured for this board (per LED, phase from the sim's ordering)
function blink(set, sim, rt, o, colors = {}) {
  if (!sim) return;
  const leds = Object.keys(sim.leds || {});
  leds.forEach((ref, i) => {
    const m = sim.leds[ref], a = anchorXY(set, ref); if (!a || !m) return;
    const on = m.blink_hz > 0.05 ? ((rt * m.blink_hz + (i % 2) * 0.5) % 1) < m.duty : m.avg_ma > 0.5;
    if (!on) return;
    const c = colors[ref] || "glow";
    svg += `<circle cx="${a[0]}" cy="${a[1]}" r="46" fill="url(#${c})" opacity="${o}"/><circle cx="${a[0]}" cy="${a[1]}" r="10" fill="${c === "glowb" ? "#9ab6ff" : c === "glowy" ? "#fff2c8" : "#ff8080"}" opacity="${o}"/>`;
  });
}

/* ---------- words flow in (v3) ---------- */
function wordPos(words, p) { const lens = words.map((w) => w.length + 1), tot = lens.reduce((a, b) => a + b, 0); let t = p * tot; for (let i = 0; i < words.length; i++) { if (t < lens[i]) return i + t / lens[i]; t -= lens[i]; } return words.length; }
function flow(el, key, words, nf, lo, ramp = 1.6) {
  if (el.dataset.k !== key) { el.dataset.k = key; el.innerHTML = words.map((w) => `<span class="w">${esc(w)}</span> `).join(""); }
  const sp = el.children;
  for (let i = 0; i < sp.length; i++) { const k = clamp((nf - i) / ramp + 0.5); put(sp[i], "opacity", (lo + (1 - lo) * k * k * (3 - 2 * k)).toFixed(3)); }
}
const MIC = `<svg viewBox="0 0 13 16"><rect x="4" y="0" width="5" height="10" rx="2.5" fill="${AMB}"/><path d="M1.5 7.5a5 5 0 0 0 10 0M6.5 12.5V15M3.5 15h6" stroke="${AMB}" stroke-width="1.6" fill="none"/></svg>`;
function vu(T, etch) {
  const c = $("vu"), g = c.getContext("2d"), col = etch ? V : AMB; g.clearRect(0, 0, 92, 36); g.fillStyle = col;
  for (let k = 0; k < 5; k++) { const lv = LEVEL(T - Math.abs(k - 2) * 0.06, etch ? "etch" : "user"), h = 4 + 28 * clamp(lv * (1 - Math.abs(k - 2) * 0.18)); g.globalAlpha = 0.85; g.fillRect(6 + k * 18, 18 - h / 2, 8, h); }
  g.globalAlpha = 1;
}
function timerDraw(on, val, label, done) {
  const t = $("timer");
  put(t, "display", on > 0.001 ? "" : "none"); put(t, "opacity", on.toFixed(3)); put(t, "transform", `translateY(${((1 - on) * -8).toFixed(2)}px)`);
  put($("tval"), "text", fmtT(val)); put(t, "class", "lay" + (done ? " done" : "")); put(t.querySelector("small"), "text", label);
}
// the done pill sits over the top of the steps card on desktop: step lines that would run under it are hidden
// (the newest lines are at the bottom and stay); phones put the pill at the screen's foot instead
let PINGK = "";
function stepsUnderPing(on) {
  const el = $("steps"), k = on ? `${STEPN}|${Math.floor(performance.now() / 250)}` : ""; // re-measured 4x a second (panels slide in)
  if (k === PINGK) return; PINGK = k;
  const pr = on ? $("ping").getBoundingClientRect() : null;
  for (const c of el.children) {
    if (c.id === "stepsh") continue;
    const cr = pr && c.getBoundingClientRect();
    c.style.visibility = pr && cr.top < pr.bottom + 4 && cr.right > pr.left && cr.left < pr.right ? "hidden" : "";
  }
}
function pingDraw(o, text, set, ref) {
  const p = $("ping"); put(p, "display", o > 0.01 ? "" : "none");
  if (o <= 0.01) return stepsUnderPing(false);
  put(p, "text", text); put(p, "opacity", o.toFixed(3)); put(p, "transform", `translateY(${((1 - o) * 10).toFixed(2)}px)`);
  if (set && SETS[set]) { const r = spot("ping:" + set + text.length, set, ref, p.offsetWidth, p.offsetHeight, [], 24); put(p, "right", "auto"); put(p, "left", r[0] + "px"); put(p, "top", r[1] + "px"); }
  else { put(p, "left", "auto"); put(p, "right", "20px"); put(p, "top", "120px"); }
  stepsUnderPing(!document.body.classList.contains("mobile"));
}
function simText(set, sim, r) {
  if (!sim) return "";
  const leds = Object.entries(sim.leds || {}), loads = Object.entries(sim.loads || {});
  const sc = (sim.scenarios || []).map((s) => s.name).filter((n) => n !== "default");
  if (sc.length >= 2 && loads.length) { const [ref] = loads[0]; return `simulated: ${sc.map((n) => `${n} → ${ref} ${(sim[`${n}.${ref}.avg_ma`] ?? 0).toFixed(1)} mA`).join(" · ")}`; }
  if (sc.length >= 2 && leds.length) { const [ref] = leds[0]; return `simulated: ${sc.map((n) => `${n} → ${ref} ${(sim[`${n}.${ref}.avg_ma`] ?? 0).toFixed(1)} mA`).join(" · ")}`; }
  const parts = [];
  if (leds.length && leds[0][1].blink_hz > 0.05) parts.push(`blinks ${leds[0][1].blink_hz.toFixed(1)} times a second`);
  if (leds.length) parts.push(`LED ${leds[0][1].peak_ma.toFixed(1)} mA`);
  if (loads.length) parts.push(`${loads[0][0]} ${loads[0][1].peak_ma.toFixed(0)} mA${loads[0][1].blink_hz > 0.05 ? ", on with the blink" : ""}`);
  return parts.length ? `simulated: ${parts.join(" · ")}` : "";
}

/* ---------- background: an endless circuit board, built in code (Bayer dither, pixel style) ----------
   Footprints, pads, 45-degree traces and vias are generated once on a wrapping tile; the view drifts slowly
   across it and signal pulses run along the real trace paths, flashing the via they reach. One low-res canvas
   behind everything, no images. The middle stays dim so text reads; reduced motion gets a still board. */
const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map((v) => (v + 0.5) / 16);
const PAL = [[2, 4, 2], [8, 30, 14], [20, 120, 50], [61, 255, 110]];
const RM = matchMedia("(prefers-reduced-motion: reduce)");
const BG = { cv: $("bg"), W: 512, H: 320, base: null, paths: [], ends: [], pulses: [], hot: null, flash: new Map(), w: 0, h: 0, img: null, mask: null, last: -1, calm: -1, key: "" };
function bgRng(seed) { let s = seed >>> 0; return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296); }
function bgBuild() {
  const { W, H } = BG, L = new Float32Array(W * H), R = bgRng(20261017), G = 4, gw = W / G, gh = H / G, occ = new Uint8Array(gw * gh);
  const ix = (x, y) => (((y % H) + H) % H) * W + (((x % W) + W) % W);
  const set = (x, y, v) => { const i = ix(x, y); if (L[i] < v) L[i] = v; };
  const oc = (cx, cy) => (((cy % gh) + gh) % gh) * gw + (((cx % gw) + gw) % gw);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) L[y * W + x] = R() < 0.02 ? 0.55 : 0.06; // laminate fleck
  const pads = [];
  for (let n = 0, tries = 0; n < 70 && tries < 900; tries++) { // footprints: a silkscreen outline with a pad row on each long side
    const cw = 2 + Math.floor(R() * 6), ch = 2 + Math.floor(R() * 2), cx = Math.floor(R() * gw), cy = Math.floor(R() * gh);
    let free = true;
    for (let j = -2; j <= ch + 2 && free; j++) for (let i = -2; i <= cw + 2; i++) if (occ[oc(cx + i, cy + j)]) { free = false; break; }
    if (!free) continue;
    n++;
    for (let j = 0; j <= ch; j++) for (let i = 0; i <= cw; i++) occ[oc(cx + i, cy + j)] = 2;
    const x0 = cx * G + 2, x1 = (cx + cw) * G - 2, y0 = cy * G + 2, y1 = (cy + ch) * G - 2;
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) set(x, y, y === y0 || y === y1 || x === x0 || x === x1 ? 1.3 : 0.45);
    set(x0 + 2, y0 + 2, 1.6); // pin-1 dot
    for (let i = 1; i < cw; i++) { pads.push([cx + i, cy, 0, -1]); pads.push([cx + i, cy + ch, 0, 1]); }
  }
  const pad = (cx, cy, v) => { for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) set(cx * G + i, cy * G + j, v); };
  const via = (cx, cy) => { for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) if (i || j) set(cx * G + i, cy * G + j, 1.8); BG.ends.push(ix(cx * G, cy * G)); };
  const DIRS = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];
  const walk = (cx, cy, d, len) => { // one trace: straight runs with 45-degree bends, ends on a via
    const pts = [[cx, cy]];
    for (let k = 0; k < len; k++) {
      if (k > 1 && R() < 0.16) d = (d + (R() < 0.5 ? 1 : 7)) % 8;
      const nx = cx + DIRS[d][0], ny = cy + DIRS[d][1];
      if (occ[oc(nx, ny)]) break;
      occ[oc(nx, ny)] = 1; cx = nx; cy = ny; pts.push([cx, cy]);
    }
    if (pts.length < 3) return;
    const px = [];
    for (let k = 1; k < pts.length; k++) {
      const [ax, ay] = pts[k - 1], [bx, by] = pts[k];
      for (let s = 0; s < G; s++) { const x = ax * G + (bx - ax) * s, y = ay * G + (by - ay) * s; set(x, y, 1.35); px.push(ix(x, y)); }
    }
    via(cx, cy); BG.paths.push(px);
  };
  for (const [cx, cy, , dy] of pads) { pad(cx, cy, 2.2); walk(cx, cy, dy < 0 ? 6 : 2, 8 + Math.floor(R() * 40)); }
  for (let b = 0; b < 26; b++) { // buses: parallel runs that jog together
    const cx = Math.floor(R() * gw), cy = Math.floor(R() * gh), d = [0, 2, 1, 7][b % 4], n = 3 + Math.floor(R() * 4), len = 18 + Math.floor(R() * 40);
    for (let k = 0; k < n; k++) { const sx = cx + (d === 2 ? k : 0) - (d === 1 || d === 7 ? k : 0), sy = cy + (d === 2 ? 0 : k); if (!occ[oc(sx, sy)]) { occ[oc(sx, sy)] = 1; pad(sx, sy, 2.0); walk(sx, sy, d, len); } }
  }
  for (let r = 0; r < 9; r++) { // hatched ground pours, kept clear of every trace and pad
    const rx = Math.floor(R() * gw), ry = Math.floor(R() * gh), rw = 8 + Math.floor(R() * 22), rh = 5 + Math.floor(R() * 12);
    for (let j = 0; j < rh; j++) for (let i = 0; i < rw; i++) {
      let clear = true;
      for (let v = -1; v <= 1 && clear; v++) for (let u = -1; u <= 1; u++) if (occ[oc(rx + i + u, ry + j + v)]) { clear = false; break; }
      if (!clear) continue;
      for (let y = 0; y < G; y++) for (let x = 0; x < G; x++) { const X = (rx + i) * G + x, Y = (ry + j) * G + y; if ((X + Y) % 4 === 0 || (X - Y + 4096) % 4 === 0) set(X, Y, 0.85); }
    }
  }
  BG.base = L; BG.hot = new Float32Array(W * H);
}
function bgFit() {
  const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight, mob = vw < 760;
  const px = Math.max(mob ? 4 : 6, Math.ceil(vw / (BG.W - 8)), Math.ceil(vh / (BG.H - 8)));
  const w = Math.ceil(vw / px), h = Math.ceil(vh / px), key = `${w}x${h}`;
  if (key === BG.key) return;
  BG.key = key; BG.w = w; BG.h = h; BG.cv.width = w; BG.cv.height = h;
  BG.g = BG.cv.getContext("2d"); BG.img = BG.g.createImageData(w, h); BG.mask = new Float32Array(w * h); BG.calm = -1;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { // dim behind the middle, where the words are
    const cx = x / w - 0.5, cy = (y / h - (mob ? 0.3 : 0.46)) * (mob ? 0.9 : 0.62);
    // a phone is one column of words top to bottom, so keep the whole board quieter there
    BG.mask[y * w + x] = mob ? 0.2 + 0.42 * clamp(Math.sqrt(cx * cx + cy * cy) * 2.1 - 0.15) : 0.22 + 0.9 * clamp(Math.sqrt(cx * cx + cy * cy) * 2.1 - 0.15);
  }
}
addEventListener("resize", bgFit);
function drawBg(T, calm) {
  if (!BG.base) { bgBuild(); bgFit(); }
  const still = RM.matches;
  if (still ? Math.abs(calm - BG.calm) < 0.02 : T - BG.last < 1 / 30) return; // 30 fps is plenty for a drift
  const dt = BG.last < 0 ? 0 : Math.min(0.1, T - BG.last);
  BG.last = T; BG.calm = calm;
  const { W, H, w, h, base, mask, hot, img } = BG, d = img.data, touched = [];
  if (!still) { // signal pulses: a bright head with a fading tail, running a trace toward its via
    while (BG.pulses.length < 14) BG.pulses.push({ p: BG.paths[(Math.random() * BG.paths.length) | 0], s: -Math.random() * 120, v: 30 + Math.random() * 45 });
    for (const q of BG.pulses) {
      q.s += q.v * dt;
      const head = Math.floor(q.s);
      for (let k = 0; k < 9; k++) { const j = head - k; if (j < 0 || j >= q.p.length) continue; const i = q.p[j]; hot[i] = Math.max(hot[i], 2.9 - k * 0.28); touched.push(i); }
      if (head >= q.p.length + 9) { BG.flash.set(q.p[q.p.length - 1], 1); q.done = true; }
    }
    BG.pulses = BG.pulses.filter((q) => !q.done);
    for (const [i, a] of BG.flash) { // the via a pulse reached glows for a moment
      const x = i % W, y = (i / W) | 0;
      for (let j = -2; j <= 2; j++) for (let k = -2; k <= 2; k++) { const n = (((y + j) % H) + H) % H * W + (((x + k) % W) + W) % W; hot[n] = Math.max(hot[n], a * (Math.abs(j) + Math.abs(k) <= 2 ? 2.6 : 1.2)); touched.push(n); }
      if (a - dt * 1.4 <= 0) BG.flash.delete(i); else BG.flash.set(i, a - dt * 1.4);
    }
  }
  const ox = still ? 0 : Math.floor(T * 2.2) % W, oy = still ? 0 : Math.floor(T * 1.1) % H;
  for (let y = 0; y < h; y++) {
    const row = ((y + oy) % H) * W, b4 = (y & 3) * 4;
    for (let x = 0; x < w; x++) {
      const ti = row + ((x + ox) % W), v = (base[ti] + hot[ti]) * mask[y * w + x] * calm;
      const lv = v > 2.999 ? 2.999 : v, lo = lv | 0, c = PAL[lv - lo > BAYER[b4 + (x & 3)] ? lo + 1 : lo], i = (y * w + x) * 4;
      d[i] = c[0]; d[i + 1] = c[1]; d[i + 2] = c[2]; d[i + 3] = 255;
    }
  }
  for (const i of touched) hot[i] = 0;
  BG.g.putImageData(img, 0, 0);
}

/* =====================================================================================================
   THE APP: talk (local Parakeet STT on the Mac) or type; every ask runs the real engine (agent/live.py)
   through the public backend (deploy/etch_public.py), polled, and builds on this stage part by part.
   ===================================================================================================== */
const CFG = await fetch("config.json", { cache: "no-store" }).then((r) => r.json()).catch(() => ({}));
const API = String(Q.get("api") || CFG.api || "").replace(/\/$/, "");
const FILES = API + "/etch/files";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function api(path, opt = {}) {
  return fetch(API + path, opt);
}

const st = { req: null, log: [], setKey: null, setT: 0, base: null, explode: 0, exTarget: 0, sim: null, why: {}, busy: false,
  heard: "", heardT: -9, heardHow: "", sayT: -9, sayText: "", sayDur: 3, idle: 1, idleTarget: 1, queued: null, online: null,
  view: "home", viewJob: null, viewRun: null, vmode: false, sttBusy: false, fetching: false, runs: [],
  v3: { run: null, ready: false, a: 0 }, versions: [], pending: null };
try { st.versions = JSON.parse(sessionStorage.getItem("etch-versions") || "[]") || []; } catch (e) { st.versions = []; }
window.__st = st; // inspection from the console
const T0 = performance.now() / 1000, now = () => performance.now() / 1000 - T0;
const ci = $("chatin");

/* ---------- Etch's voice: a queue, so every line finishes. Only the mic, NEW BOARD and CANCEL cut a line short.
   No audio at all until the person has talked or typed to Etch first (the text still shows). ---------- */
const VOICE = Q.get("voice") !== "0";
let etchLevel = 0, micLevel = 0, rec = null, starting = false;
LEVEL = (T, who) => (who === "etch" ? etchLevel : micLevel);
let vctx = null, cur = null, sayN = 0, pumping = false, TALKED = false;
const VQ = [];
function stopVoice() { sayN++; VQ.length = 0; if (cur) cur.stop(); cur = null; etchLevel = 0; }
// lines said before the first turn are text only; drop them once the person starts talking
function dropSilent() { for (let i = VQ.length - 1; i >= 0; i--) if (!VQ[i].audio) VQ.splice(i, 1); if (cur && cur.silent) cur.stop(); }
function pieces(text) {
  if (text.length <= 200) return [text];
  const out = []; let b = "";
  for (const s of text.split(/(?<=[.!?])\s+/)) { if (b && (b + " " + s).length > 200) { out.push(b); b = s; } else b = b ? b + " " + s : s; }
  if (b) out.push(b);
  return out;
}
const tts = (text) => fetch(API + "/etch/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) })
  .then((r) => (r.ok ? r.blob() : null)).catch(() => null);
function say(text) {
  for (const p of pieces(String(text))) VQ.push({ text: p, t0: now(), audio: VOICE && TALKED ? tts(p) : null });
  while (VQ.length > 4) VQ.shift(); // never fall far behind the build
  pump();
}
function caption(text, dur) { st.sayText = text; st.sayT = now(); st.sayDur = dur; }
async function pump() {
  if (pumping) return; pumping = true;
  try {
    while (VQ.length) {
      const it = VQ.shift();
      if (now() - it.t0 > 25 && VQ.length) continue; // stale narration, a newer line is waiting
      await playItem(it, sayN);
    }
  } finally { pumping = false; }
  autoListen();
}
function hold(sec, n, move) {
  return new Promise((ok) => {
    const t0 = now(), item = { silent: !move, stop: () => { item.done = true; } };
    cur = item;
    const step = () => {
      if (item.done || n !== sayN || now() - t0 > sec) { etchLevel = 0; if (cur === item) cur = null; return ok(); }
      etchLevel = move ? 0.35 + 0.3 * Math.abs(Math.sin(now() * 11)) : 0;
      requestAnimationFrame(step);
    };
    step();
  });
}
async function playItem(it, n) {
  const est = Math.max(1.6, it.text.split(" ").length * 0.33);
  st.fetching = !!it.audio;
  const blob = it.audio ? await Promise.race([it.audio, sleep(9000).then(() => null)]) : null;
  st.fetching = false;
  if (n !== sayN) return;
  if (!blob) { caption(it.text, est); return hold(est, n, !!it.audio); }
  return new Promise((done) => {
    const a = new Audio(URL.createObjectURL(blob));
    let fin = false, wd = 0, an = null, buf = null;
    const end = () => { if (fin) return; fin = true; clearTimeout(wd); a.pause(); etchLevel = 0; if (cur === item) cur = null; URL.revokeObjectURL(a.src); done(); };
    const item = { a, stop: end };
    cur = item;
    const tick = () => {
      if (fin) return;
      if (an) { an.getByteTimeDomainData(buf); let s = 0; for (const v of buf) { const d = (v - 128) / 128; s += d * d; } etchLevel = Math.min(1, Math.sqrt(s / buf.length) * 5); }
      else etchLevel = 0.35 + 0.3 * Math.abs(Math.sin(now() * 11));
      requestAnimationFrame(tick);
    };
    a.onplay = () => { caption(it.text, isFinite(a.duration) && a.duration > 0 ? a.duration : est); tick(); };
    a.onended = end; a.onerror = end;
    a.addEventListener("loadedmetadata", () => { if (isFinite(a.duration) && a.duration > 0) { clearTimeout(wd); wd = setTimeout(end, (a.duration + 4) * 1000); } });
    wd = setTimeout(end, (est * 2.5 + 10) * 1000); // a stuck element never blocks the queue
    (async () => {
      try {
        if (!vctx) vctx = new AudioContext();
        if (vctx.state !== "running") await Promise.race([vctx.resume(), sleep(400)]);
        // the analyser drives the mouth; only route through it when the context really runs, or the line goes silent
        if (vctx.state === "running") { an = vctx.createAnalyser(); an.fftSize = 512; buf = new Uint8Array(512); vctx.createMediaElementSource(a).connect(an); an.connect(vctx.destination); }
      } catch (e) { an = null; }
      if (fin) return;
      try { await a.play(); } catch (e) { if (fin) return; fin = true; clearTimeout(wd); if (cur === item) cur = null; caption(it.text, est); await hold(est, n, true); done(); }
    })();
  });
}
function note(t) { st.log.push({ html: `<span class="bad">${esc(t)}</span>` }); }

/* ---------- talking: click Etch (click again to send), hold space, or voice mode keeps the conversation going ----------
   Mic fix: the AudioContext is made inside the click (before the permission await) at the device's own rate and
   resumed there; audio is box-filtered down to 16 kHz for the STT; echo cancelling and noise suppression are off
   (they flattened quiet speech to near silence); a peak check says when the input was too quiet to use. */
function to16k(x, rate) {
  if (Math.abs(rate - 16000) < 1) return x;
  const r = rate / 16000, n = Math.floor(x.length / r), y = new Float32Array(n);
  for (let i = 0; i < n; i++) { const a = Math.floor(i * r), b = Math.min(x.length, Math.max(a + 1, Math.floor((i + 1) * r))); let s = 0; for (let k = a; k < b; k++) s += x[k]; y[i] = s / (b - a); }
  return y;
}
async function micStart(opt = {}) {
  if (rec || starting) return;
  starting = true;
  let ac = null;
  try {
    stopVoice(); TALKED = true;
    ac = new AudioContext();
    ac.resume().catch(() => {});
    const ms = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: true } });
    if (ac.state !== "running") await Promise.race([ac.resume(), sleep(600)]);
    const src = ac.createMediaStreamSource(ms), sp = ac.createScriptProcessor(4096, 1, 1), mute = ac.createGain(), chunks = [];
    mute.gain.value = 0;
    const r = { ms, ac, sp, chunks, t: performance.now(), auto: !!opt.auto, held: !!opt.held, spoke: 0, quietSince: 0, label: ms.getAudioTracks()[0]?.label || "" };
    sp.onaudioprocess = (e) => {
      const x = e.inputBuffer.getChannelData(0); chunks.push(new Float32Array(x));
      let s = 0; for (const v of x) s += v * v; const rms = Math.sqrt(s / x.length);
      micLevel = Math.min(1, rms * 12);
      // voice mode listens hands free: stop after a pause that follows speech, give up when nobody talks
      if (!r.auto || rec !== r) return;
      const t = performance.now();
      if (rms > 0.012) { r.spoke = r.spoke || t; r.quietSince = 0; } else if (!r.quietSince) r.quietSince = t;
      if (r.spoke && r.quietSince && t - r.quietSince > 1300) micStop();
      else if (!r.spoke && t - r.t > 7000) micStop(true);
    };
    src.connect(sp); sp.connect(mute); mute.connect(ac.destination);
    rec = r; ac = null;
    st.vmode = st.vmode || !opt.held;
    ci.classList.add("live"); ci.setAttribute("contenteditable", "false");
    ci.innerHTML = `<span class="mic">${MIC}<i></i><i></i><i></i> <em style="font-style:normal">LISTENING</em></span><span class="caret"></span>`;
  } catch (e) {
    if (ac) ac.close().catch(() => {});
    st.vmode = false;
    say("I can't hear you: the microphone is blocked. You can type instead."); note("microphone: " + e.message);
  }
  starting = false;
}
async function micStop(giveUp = false) {
  if (!rec) return;
  const { ms, ac, sp, chunks, label } = rec; rec = null; micLevel = 0;
  sp.disconnect(); ms.getTracks().forEach((t) => t.stop());
  const rate = ac.sampleRate; await ac.close().catch(() => {});
  const reset = () => { ci.classList.remove("live"); ci.setAttribute("contenteditable", "true"); ci.innerHTML = ""; };
  if (giveUp) { reset(); st.vmode = false; return; } // voice mode heard nobody: go quiet, click Etch to talk again
  const n = chunks.reduce((s, c) => s + c.length, 0), raw = new Float32Array(n); let o = 0; for (const c of chunks) { raw.set(c, o); o += c.length; }
  const all = to16k(raw, rate);
  let peak = 0, s2 = 0; for (const v of all) { const a = Math.abs(v); if (a > peak) peak = a; s2 += v * v; }
  window.__mic = { rate, seconds: +(all.length / 16000).toFixed(2), peak: +peak.toFixed(4), rms: +Math.sqrt(s2 / Math.max(1, all.length)).toFixed(4), device: label };
  if (all.length < 16000 * 0.3) { reset(); return say("I didn't catch that. Click me, speak, then click me again."); }
  if (peak < 0.015) { reset(); note(`microphone too quiet (${label || "default input"})`); return say("That was too quiet for me to hear. Check that the right microphone is picked, then try again."); }
  if (peak < 0.5) { const g = Math.min(20, 0.5 / peak); for (let i = 0; i < all.length; i++) all[i] *= g; }
  ci.innerHTML = `<span class="mic">${MIC} <em style="font-style:normal">TRANSCRIBING</em></span>`;
  st.sttBusy = true;
  try {
    const r = await (await api("/etch/stt", { method: "POST", body: all.buffer })).json();
    st.sttBusy = false; reset();
    const text = (r.text || "").trim();
    if (text) {
      // heard you: show the words for a beat, then act on them
      st.heard = text; st.heardT = now(); st.heardHow = "spoken";
      await sleep(250);
      handle(text, "spoken");
    } else if (r.error) { say(r.error); note(r.error); }
    else say("I didn't catch that. Try again, or type it.");
  } catch (e) { st.sttBusy = false; reset(); note("speech-to-text failed: " + e.message); }
}
// voice mode: after Etch answers a spoken turn, listen again (only while no build is running)
function autoListen() {
  if (!st.vmode || rec || starting || st.busy || st.heardHow !== "spoken" || st.view === "lib") return;
  setTimeout(() => { if (!VQ.length && !cur && !pumping && !st.busy && st.vmode) micStart({ auto: true }); }, 350);
}
let downAt = 0;
// click: a soft burst of small pixel sparks in the theme colours (no squash, no ring), ~500 ms
const SPARK = ["#3dff6e", "#3dff6e", "#b6ffc8", "#ffc857"];
function press(w) {
  const f = w.querySelector(".face"); if (!f || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const n = 14, R = f.offsetWidth * 0.36;
  for (let i = 0; i < n; i++) {
    const s = document.createElement("i"), ang = (i / n) * Math.PI * 2 + (Math.random() - 0.5) * 0.35, d = R * (0.85 + Math.random() * 0.45), z = 4 + Math.round(Math.random() * 3);
    s.className = "spark"; s.style.width = s.style.height = z + "px"; s.style.background = s.style.color = SPARK[i % SPARK.length]; f.appendChild(s);
    const x = Math.cos(ang) * d, y = Math.sin(ang) * d;
    s.animate([{ transform: `translate(-50%,-50%) translate(${(x * 0.55).toFixed(1)}px,${(y * 0.55).toFixed(1)}px) scale(.6)`, opacity: 0 },
      { transform: `translate(-50%,-50%) translate(${(x * 0.8).toFixed(1)}px,${(y * 0.8).toFixed(1)}px) scale(1)`, opacity: 1, offset: 0.25 },
      { transform: `translate(-50%,-50%) translate(${x.toFixed(1)}px,${y.toFixed(1)}px) scale(.5)`, opacity: 0 }],
      { duration: 440 + Math.random() * 120, easing: "cubic-bezier(.2,.7,.3,1)" }).onfinish = () => s.remove();
  }
}
function micDown(e) { e.preventDefault(); press(e.currentTarget); if (rec) { micStop(); return; } downAt = performance.now(); micStart(); }
function micUp() { if (rec && !rec.auto && performance.now() - downAt > 600) micStop(); } // held = push to talk, short click = toggle
for (const w of [$("heroW"), $("bandW")]) {
  w.addEventListener("pointerdown", micDown); w.addEventListener("pointerup", micUp);
  w.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); press(w); if (rec) micStop(); else micStart(); } });
}
const typing = () => document.activeElement === ci || document.activeElement === $("ltext");
addEventListener("keydown", (e) => { if (e.code === "Space" && !typing() && !e.repeat && !document.activeElement?.classList?.contains("etchw")) { e.preventDefault(); downAt = 0; micStart({ held: true }); } if (e.key === "Escape" && st.view === "lib") closeLib(); });
addEventListener("keyup", (e) => { if (e.code === "Space" && !typing() && !document.activeElement?.classList?.contains("etchw")) { e.preventDefault(); micStop(); } });

/* ---------- typing: the landing box and the side panel box ---------- */
function sendFrom(el) { const t = (el.value ?? el.textContent).trim(); if (el.value !== undefined) el.value = ""; else el.textContent = ""; if (t) handle(t, "typed"); }
ci.setAttribute("contenteditable", "true");
ci.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); sendFrom(ci); } });
$("sendb").addEventListener("click", () => sendFrom(ci));
$("ltext").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); sendFrom($("ltext")); } });
document.querySelectorAll("[data-ask]").forEach((c) => c.addEventListener("click", () => handle(c.dataset.ask, "picked")));
$("resetb").addEventListener("click", () => { if (st.v3.a > 0.5 && VIEW3) VIEW3.reset(); else v2reset(); });
$("expandb").addEventListener("click", () => { if (!st.setKey && !st.v3.solo) return; st.exTarget = st.exTarget > 0.5 ? 0 : 1; });

/* ---------- views: home (the landing), a build (live or finished), the library ---------- */
let FOLLOW = 0, MINE = null, TOK = {};
try { TOK = JSON.parse(localStorage.getItem("etch-tokens") || "{}") || {}; } catch (e) { TOK = {}; }
function saveTok() { try { const k = Object.keys(TOK); while (k.length > 20) delete TOK[k.shift()]; localStorage.setItem("etch-tokens", JSON.stringify(TOK)); } catch (e) { /* not remembered */ } }
function setView(v) {
  st.view = v; st.idleTarget = v === "home" ? 1 : 0;
  const bub = $("bub"), home = v === "home";
  if (home && bub.parentElement !== $("hbub")) $("hbub").appendChild(bub);
  if (!home && bub.parentElement !== $("capband")) $("capband").appendChild(bub);
}
function resetView() {
  st.req = null; st.log = []; STEPN = 0;
  for (const c of [...$("steps").children]) if (c.id !== "stepsh") c.remove();
  GKEY = ""; $("glist").innerHTML = "";
  if (st.setKey) st.prevKey = st.setKey;
  st.setKey = null; st.sim = null; st.base = null; st.why = {}; st.exTarget = 0; st.explode = 0;
  st.busy = false; st.viewJob = null; st.viewRun = null; st.queued = null;
  st.v3 = { run: null, ready: false, a: 0 }; v3card(null); v2reset(true);
  put($("asked"), "display", "none");
}
function showAsked(text, mine) { $("askedt").textContent = text || "(no words saved for this build)"; $("asked").querySelector("h4").firstChild.nodeValue = mine ? "YOU ASKED " : "ASKED "; put($("asked"), "display", ""); }
const mineJob = () => MINE && MINE.job;
async function mineBusy() {
  if (!MINE) return false;
  try { const d = await (await api(`/etch/job?id=${encodeURIComponent(MINE.job)}&since=999999`)).json(); if (d.error || d.done) { MINE = null; return false; } return true; }
  catch (e) { return false; }
}
function goHome() {
  closeLib();
  const mineRunning = st.busy && st.viewJob && st.viewJob === mineJob();
  if (!mineRunning) { FOLLOW++; resetView(); } // someone else's build, or a finished one: stop watching it
  setView("home");
}
$("homeb").addEventListener("click", goHome);
$("homel").addEventListener("click", goHome);
$("homel").addEventListener("keydown", (e) => { if (e.key === "Enter") goHome(); });
$("newb").addEventListener("click", async () => {
  stopVoice();
  if (await mineBusy()) { if (st.viewJob !== mineJob()) openJob(MINE.job, MINE.mode, MINE.text, true); return say("Your last build is still running. Press CANCEL to stop it, or wait for it to finish."); }
  closeLib(); FOLLOW++; resetView(); setView("home");
  say("Ready for a new board. What should it do?");
  setTimeout(() => $("ltext").focus(), 60);
});
$("cancelb").addEventListener("click", async () => {
  const job = st.viewJob, token = TOK[job]; if (!job || !token) return;
  stopVoice();
  put($("cancelb"), "display", "none");
  let j = {};
  try { j = await (await api("/etch/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job, token }) })).json(); }
  catch (e) { j = { error: String(e.message || e) }; }
  if (j.error && !/finished|done/.test(j.error)) { note("cancel: " + j.error); return say(j.error); }
  FOLLOW++;
  if (st.req && !st.req.done) st.req.cancel = { t: now() - st.reqStart };
  st.busy = false; st.queued = null; if (mineJob() === job) MINE = null;
  st.log.push({ html: `<span class="t">${(now() - st.reqStart).toFixed(1)}s</span> <b>lead</b> <span class="bad">build cancelled</span>` });
  say("Build cancelled.");
  if (!st.setKey) setView("home");
  pollRuns();
});
// EXPORT: ERC + DRC run again on the files, then one zip (Gerbers + drill, BOM, STEP, GLB, KiCad project,
// firmware). If a check fails, Etch says why and nothing is downloaded.
let EXPORTING = false;
$("zipb").addEventListener("click", async () => {
  const run = st.viewRun; if (!run || EXPORTING) return;
  EXPORTING = true; put($("zipb"), "text", "CHECKING...");
  try {
    const j = await (await api(`/etch/export?id=${encodeURIComponent(run)}`)).json();
    if (!j.ok) { const why = (j.reasons || []).join("; ") || j.error || "the checks did not pass"; say("I won't export this one: " + why + "."); note("export blocked: " + why); return; }
    const r = await api(`/etch/export.zip?id=${encodeURIComponent(run)}`); if (!r.ok) throw new Error(`error ${r.status}`);
    const cd = r.headers.get("Content-Disposition") || "", name = (cd.match(/filename="([^"]+)"/) || [])[1] || run + "-export.zip";
    const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = name;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    say(`Exported. ERC and DRC passed again on these files: Gerbers, drill, BOM, STEP and the 3D model${j.files ? `, ${j.files} files` : ""}.`);
  } catch (e) { note("export failed: " + e.message); }
  finally { EXPORTING = false; put($("zipb"), "text", "EXPORT"); }
});

/* ---------- the 3D board (three.js, KiCad's GLB) ---------- */
let VIEW3 = null;
// the 3D files (KiCad GLB, parts sheet, and the SO-101 for a voice arm), fetched as early as possible
function prefetch3d(run) {
  const q = encodeURIComponent(run);
  const parts = api(`/etch/parts?id=${q}`).then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
  // the board's real copper pours (zone fills) for the 3D view; the board still shows without them
  const layers = api(`/etch/layers?id=${q}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  // a voice-arm board shows the SO-101 it drives beside it (static file, TheRobotStudio SO-ARM100, Apache-2.0)
  const arm = parts.then((p) => (/VOICE ARM/i.test(p.title || "") ? fetch("so101.glb?v=2").then((r) => (r.ok ? r.arrayBuffer() : null)).catch(() => null) : null));
  const glb = api(`/etch/glb?id=${q}`).then((g) => { if (!g.ok) throw new Error(`3D model: error ${g.status}`); return g.arrayBuffer(); });
  return Promise.all([glb, parts, arm, layers]);
}
// solo = a finished build with no render frames: the 3D board is shown straight away, framed in the default box
async function load3d(run, key, pre, solo = false) {
  if (!run || !key) return;
  st.v3 = { run, key, ready: false, a: st.v3.a, solo };
  try {
    const [buf, parts, armBuf, layers] = await (pre || prefetch3d(run));
    if (st.v3.run !== run) return;
    if (!VIEW3) {
      VIEW3 = window.__v3 = createViewer($("v3")); VIEW3.onPick = (ref, info) => v3card(ref, info); VIEW3.canvas.addEventListener("dblclick", () => VIEW3.reset());
      VIEW3.onStart = () => { HINTED = true; };
    }
    const S = SETS[key], p = S && place(key, RECT, true), b = S && S.bbox;
    await VIEW3.load(buf, parts, S ? [p.x + b[0] * p.s, p.y + b[1] * p.s, (b[2] - b[0]) * p.s, (b[3] - b[1]) * p.s] : null, armBuf, layers);
    if (st.v3.run === run) st.v3.ready = true;
  } catch (e) { note(solo ? "this build has no 3D picture" : "3D view: " + e.message); }
}
function ledsOn(sim, T) {
  const out = {}; if (!sim || !sim.leds) return out;
  Object.keys(sim.leds).forEach((ref, i) => { const m = sim.leds[ref]; if (m) out[ref] = m.blink_hz > 0.05 ? ((T * m.blink_hz + (i % 2) * 0.5) % 1) < m.duty : m.avg_ma > 0.5; });
  return out;
}
let V3REF = null;
function v3card(ref, info) {
  const el = $("v3card"); V3REF = ref;
  if (!ref) { put(el, "display", "none"); if (VIEW3 && VIEW3.pickedRef()) VIEW3.clearPick(); return; }
  const i = info || {}, gh = /^https:\/\/github\.com\/TheRobotStudio\//.test(i.source || ""), src = /^https:\/\/gitlab\.com\//.test(i.source || "") || gh ? i.source : null, mdl = /^https:\/\/gitlab\.com\//.test(i.model || "") ? i.model : null;
  el.innerHTML = `<div class="ref">${esc(ref)}<small>${esc(`${i.name || ""} ${i.value || ""}`.trim())}</small></div>` +
    (i.why ? `<div class="why">${esc(i.why)}</div>` : "") +
    `<div class="src"><i>SOURCE</i>${src ? `<a href="${esc(src)}" target="_blank" rel="noopener">${gh ? "SO-ARM100 on GitHub" : "KiCad footprint"}</a>` : "KiCad library"}${mdl ? ` · <a href="${esc(mdl)}" target="_blank" rel="noopener">3D model</a>` : ""}</div>` +
    (i.licence ? `<div class="lic">${esc(i.licence)}</div>` : "");
  put(el, "display", "");
  v3cardMove();
}
function v3cardMove() {
  if (!V3REF || !VIEW3) return;
  const p = VIEW3.screenOf(V3REF), el = $("v3card"); if (!p) return;
  const w = 330, h = el.offsetHeight || 120, x = clamp(p[0] + 40, 20, 1380 - w - 20), y = clamp(p[1] - h - 30, 70, 874 - h - 80);
  put(el, "left", Math.round(p[0] + 40 + w > 1360 ? p[0] - w - 40 : x) + "px"); put(el, "top", Math.round(y) + "px");
}

/* ---------- versions: every decision is a real board; step back to any of them ---------- */
function addVersion(run, req) {
  if (!run) return;
  if (req.mode === "design") st.versions = [];
  if (req.mode === "bench") return;
  st.versions = st.versions.filter((v) => v.run !== run).concat([{ run, text: req.text, mode: req.mode }]).slice(-12);
  try { sessionStorage.setItem("etch-versions", JSON.stringify(st.versions)); } catch (e) { /* not remembered */ }
  versDraw();
}
let VERSK = "";
function versDraw() {
  const el = $("vers"), cur = st.viewRun, k = JSON.stringify(st.versions) + cur;
  const show = st.versions.length > 1 && st.versions.some((v) => v.run === cur) && st.idle < 0.5;
  put(el, "opacity", show ? "1" : "0"); put(el, "pointerEvents", show ? "auto" : "none");
  if (k === VERSK) return; VERSK = k;
  el.innerHTML = st.versions.map((v, i) => `<button class="ver${v.run === cur ? " on" : ""}" data-run="${esc(v.run)}" title="${esc(v.text)}"><img alt="" src="${esc(API)}/etch/thumb?id=${encodeURIComponent(v.run)}"><span><b>V${i + 1}</b>${esc(i === 0 ? "first board" : v.text)}</span></button>`).join("");
}
$("vers").addEventListener("click", (e) => {
  const b = e.target.closest(".ver"); if (!b || st.busy) return;
  const v = st.versions.find((x) => x.run === b.dataset.run); if (!v || v.run === st.viewRun) return;
  stopVoice();
  openBuild({ run: v.run, text: v.text, mode: v.mode, state: "done" }).then(() => say(`Back to version ${st.versions.indexOf(v) + 1}. Changes from here start from this board.`));
});

// follow one job's events until it ends; a newer follow (or HOME / CANCEL) stops this one
async function follow(job, mode, catchup = false) {
  const my = ++FOLLOW; st.viewJob = job; st.busy = true;
  let since = 0, miss = 0, first = true, d = null;
  for (;;) {
    try { d = await (await api(`/etch/job?id=${encodeURIComponent(job)}&since=${since}`)).json(); miss = 0; }
    catch (e) { if (my !== FOLLOW) return; if (++miss > 40) { note("lost the connection to Etch's engine"); break; } await sleep(400); continue; }
    if (my !== FOLLOW) return;
    if (d.error) { note(d.error); break; }
    if (d.run) st.viewRun = d.run;
    const quiet = (catchup && first) || st.view === "home";
    for (const e of d.events) onEvent(e, mode, { quiet, home: st.view === "home" && !(catchup && first) });
    if (catchup && first && d.events.length) st.reqStart = now() - (+d.events.at(-1).t || 0);
    first = false; since = d.next;
    if (d.done) break;
    await sleep(400);
  }
  if (my !== FOLLOW) return;
  st.busy = false; st.queued = null;
  if (mineJob() === job) MINE = null;
  if (d && d.state === "cancelled" && st.req && !st.req.cancel) st.req.cancel = { t: now() - st.reqStart };
  pollRuns();
}
function openJob(job, mode, text, mine) {
  closeLib(); resetView(); st.prevKey = null; setView("build"); showAsked(text, mine);
  st.reqStart = now();
  return follow(job, mode || "design", true);
}
// a build from the library: running ones are followed live, finished ones replay their saved events
async function openBuild(b) {
  if (b.job && (b.state === "running" || b.state === "queued")) return openJob(b.job, b.mode, b.text, !!TOK[b.job]);
  if (!b.run) return;
  closeLib();
  const my = ++FOLLOW;
  resetView(); setView("build"); showAsked(b.text, !!TOK[b.job]);
  st.viewRun = b.run; st.reqStart = now();
  let d;
  try { d = await (await api(`/etch/run?id=${encodeURIComponent(b.run)}`)).json(); } catch (e) { return note("could not open that build: " + e.message); }
  if (my !== FOLLOW) return;
  if (!d.events) return note(d.error || "could not open that build");
  for (const e of d.events) onEvent(e, b.mode || "design", { quiet: true, replay: true });
  const r = st.req;
  if (r && !r.done && !r.benchDone) r.cancel = { t: +(d.events.at(-1)?.t || 0), label: b.state === "cancelled" ? "CANCELLED" : "STOPPED" };
  const rd = d.events.filter((e) => e.agent === "render" && e.type === "done").at(-1);
  if (rd) loadRender(rd, true); else if (b.state === "done") load3d(b.run, "solo", null, true); // no frames: straight to its 3D board
}

/* ---------- the library: every build, running ones first, then newest first ---------- */
let LIBT = 0, LIBK = "", SKEW = 0;
const ago = (t) => { const s = Math.max(0, Date.now() / 1000 - SKEW - t); return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; };
const LABEL = { running: "BUILDING", queued: "IN LINE", done: "DONE", failed: "FAILED", cancelled: "CANCELLED" };
async function fetchBuilds() {
  const d = await (await api("/etch/builds")).json();
  if (d.now) SKEW = Date.now() / 1000 - d.now;
  st.runs = d.builds || [];
  return st.runs;
}
function libDraw(list) {
  const key = JSON.stringify(list.map((b) => [b.id, b.state, b.stage, b.thumb]));
  put($("libn"), "text", `${list.filter((b) => b.state === "running" || b.state === "queued").length} RUNNING · ${list.length} IN ALL`);
  put($("libe"), "text", list.length ? "" : "No builds yet. Ask Etch for a board and it shows up here.");
  if (key === LIBK) { $("libg").querySelectorAll(".mt span").forEach((s) => (s.textContent = ago(+s.dataset.t))); return; }
  LIBK = key;
  $("libg").innerHTML = list.map((b, i) => {
    const live = b.state === "running" || b.state === "queued", mine = !!(b.job && TOK[b.job]);
    const th = b.thumb ? `<img loading="lazy" alt="" src="${API}/etch/thumb?id=${encodeURIComponent(b.run)}">` : live ? `<div class="live"><i></i>${esc((b.stage || "starting").toUpperCase())}</div>` : `<div class="none">NO PICTURE</div>`;
    const dur = b.ended && b.started ? ` · ${fmt(b.ended - b.started)}` : "";
    return `<div class="bt ${live ? "running" : ""}" data-i="${i}" role="button" tabindex="0"><div class="th">${th}<span class="stt ${esc(b.state)}">${LABEL[b.state] || esc(b.state).toUpperCase()}</span>${mine ? '<span class="mine">YOURS</span>' : ""}</div>
      <div class="tx">${esc(b.text || "(no words saved for this build)")}</div><div class="mt"><span data-t="${+b.started || 0}">${ago(+b.started || 0)}</span>${live ? ` · <b>${esc(b.stage || "")}</b>` : esc(dur)}${b.mode && b.mode !== "design" ? ` · ${esc(b.mode)}` : ""}</div></div>`;
  }).join("");
}
$("libg").addEventListener("click", (e) => { const c = e.target.closest(".bt"); if (c) openBuild(st.runs[+c.dataset.i]); });
$("libg").addEventListener("keydown", (e) => { const c = e.target.closest(".bt"); if (c && e.key === "Enter") openBuild(st.runs[+c.dataset.i]); });
async function libTick() { if (st.view !== "lib") return; try { libDraw(await fetchBuilds()); } catch (e) { put($("libe"), "text", "Could not load the builds: " + e.message); } LIBT = setTimeout(libTick, 3000); }
let preLib = "home";
function openLib() { if (st.view === "lib") return closeLib(); preLib = st.view; st.view = "lib"; put($("lib"), "display", ""); put($("libb"), "class", "tb on"); LIBK = ""; libTick(); }
function closeLib() { if (st.view !== "lib") return; clearTimeout(LIBT); put($("lib"), "display", "none"); put($("libb"), "class", "tb"); setView(preLib); }
$("libb").addEventListener("click", openLib);
$("libx").addEventListener("click", closeLib);
// the compact "building now" list (HUD card, and under the prompt on the start page)
function runsDraw() {
  const live = st.runs.filter((b) => b.state === "running" || b.state === "queued").slice(0, 3);
  const html = live.map((b) => `<div class="rb" data-id="${esc(b.id)}" title="Watch this build"><i></i><b>${b.job && TOK[b.job] ? "YOURS" : LABEL[b.state]}</b><span>${esc(b.text || "a board")}</span><em>${esc(b.stage || "")}</em></div>`).join("");
  put($("runl"), "html", html); put($("runn"), "text", live.length ? String(live.length) : "");
  put($("runc"), "display", live.length ? "" : "none");
  put($("lrun"), "html", html);
}
for (const box of [$("runl"), $("lrun")]) box.addEventListener("click", (e) => { const r = e.target.closest(".rb"); const b = r && st.runs.find((x) => x.id === r.dataset.id); if (b) openBuild(b); });
async function pollRuns() { if (st.online === false) return; try { await fetchBuilds(); runsDraw(); } catch (e) { /* the next tick tries again */ } }
setInterval(() => { if (st.view !== "lib") pollRuns(); }, 5000);

// what the words ask for: small talk / expand / enough / benchmarks / a change to the current board / a new board
function route(t) {
  const s = t.toLowerCase().replace(/[^a-z' ]/g, " ").trim();
  if (s.split(/\s+/).length <= 4 && /^(hi|hello|hey|yo|hiya|good (morning|afternoon|evening)|thanks|thank you|cool|nice|ok|okay|great|awesome|wow|bye|goodbye)\b/.test(s) && !/\b(board|pcb|led|light|make|build)\b/.test(s)) return "chat";
  if (/\b(expand|explode|blow (it|this) (up|apart)|pull (it|this) apart|show me (the )?(layers|inside))\b/.test(s)) return "expand";
  if (/\b(that's enough|enough|collapse|put it back|close it)\b/.test(s)) return "enough";
  if (st.base && /(benchmark|require|requirements|spec sheet|our rules)/.test(s)) return "bench";
  if (st.base && /\b(add|swap|switch|change|replace|make (it|the)|turn (it|the)|also|instead|bigger|smaller|shrink|move|remove|delete|drop|take out|get rid of|put|include|use)\b/.test(s)) return "modify";
  return "design";
}
function chat(t) {
  const s = t.toLowerCase();
  if (/thank/.test(s)) return st.setKey ? "Any time. Want to change anything on this board?" : "Any time. What should we build?";
  if (/bye/.test(s)) { st.vmode = false; return "Bye for now. Click me when you want another board."; }
  if (/cool|nice|great|awesome|wow|ok/.test(s)) return st.setKey ? "Glad you like it. Say expand to see it apart, or tell me what to change." : "Tell me what your board should do, and I'll build it.";
  return st.setKey ? "Hi. This board is ready. Tell me a change, or ask for a new one." : "Hi! Tell me what your board should do, like a light that turns on when it gets dark.";
}
async function handle(text, how) {
  TALKED = true; dropSilent();
  if (how !== "spoken") { st.heard = text; st.heardT = now(); st.heardHow = how; }
  if (st.view === "lib") closeLib();
  const m = route(text);
  if (m === "chat") return say(chat(text));
  if (m === "expand") { if (!st.setKey) return say("Build a board first, then I can take it apart for you."); st.exTarget = 1; return say("Taking it apart. Say that's enough when you have seen it."); }
  if (m === "enough") { st.exTarget = 0; return say("Putting it back together."); }
  if (await mineBusy()) { if (st.viewJob !== mineJob()) openJob(MINE.job, MINE.mode, MINE.text, true); return say("Still working on your last build. Press CANCEL to stop it, or wait a moment."); }
  const base = m === "design" ? undefined : st.base;
  if (st.busy || m === "design") { FOLLOW++; resetView(); } // stop watching someone else's build; a new board starts clean
  st.base = base || null;
  showAsked(text, true);
  run(m, text, base);
}
async function run(mode, text, base) {
  st.pending = { mode, text };
  setView("build");
  st.busy = true; st.reqStart = now(); st.req = null; st.queued = null;
  if (mode === "design") { if (st.setKey) st.prevKey = st.setKey; st.setKey = null; st.sim = null; }
  say(mode === "design" ? "On it. Screening real KiCad parts for that." : mode === "modify" ? "Changing the board." : "Running your checks.");
  try {
    const r = await api("/etch/live", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode, text, base }) });
    const j = await r.json();
    if (!r.ok) { say(j.error || "I could not start that."); note(j.error || `error ${r.status}`); st.busy = false; return; }
    if (j.token) { TOK[j.job] = j.token; saveTok(); }
    MINE = { job: j.job, mode, text };
    pollRuns();
    await follow(j.job, mode);
  } catch (e) { note(String(e.message || e)); say("I lost the connection to my engine. Try again in a moment."); st.busy = false; }
}
function onEvent(e, mode, o = {}) {
  e.lt = now(); // arrival time in the page
  if (e.agent === "lead" && e.type === "queued") { st.queued = e.position; return; }
  st.queued = null;
  if (mode === "bench" && !st.req) feed(st, { agent: "lead", type: "bench", t: 0 });
  feed(st, e);
  if (!o.replay && !o.home) {
    if (e.agent === "lead" && e.type === "start") piecesClear();
    else if (e.agent === "pieces" && e.type === "show") pieceShow(e);
    else if (e.agent === "pieces" && e.type === "confirm") pieceConfirm(e);
  }
  const line = stepLine(e); if (line) st.log.push({ html: line });
  const talk = !o.quiet;
  if (e.agent === "lead" && e.type === "say" && talk) say(e.text);
  if (e.agent === "lead" && e.type === "done" && e.ok && e.board) {
    st.base = e.board;
    if (!o.replay && st.pending) addVersion(String(e.board).split("/").filter(Boolean).pop(), st.pending), (st.pending = null);
    if (talk) say(e.route === "stored board" ? "I built this one before. Here it is, and every check passes." : `Done in ${Math.max(1, Math.round(e.timings.total_s))} seconds. Every check passes.`);
    else if (o.home && st.viewJob === mineJob()) say("Your board is done. Press LIBRARY to open it.");
    fetch(FILES + e.board + "/sim.json").then((r) => (r.ok ? r.json() : null)).then((s) => (st.sim = s)).catch(() => {});
  }
  if (e.agent === "lead" && e.type === "done" && !e.ok && e.passed === undefined && e.iterations && talk) say("I could not get every check to pass. Try asking another way.");
  if (e.agent === "lead" && e.type === "error") { const m = String(e.error).split("\n").filter(Boolean).pop() || ""; note("engine error: " + m); if (talk) say(/heavy lock/.test(m) ? "The board is designed and checked, but my 3D renderer is busy with another job on this machine. Try again in a few minutes." : "Something went wrong on my side. Try again, or ask a little differently."); }
  if (e.agent === "benchmarks" && e.type === "done" && talk) say(`${e.passed} of your checks pass${e.failed ? `, ${e.failed} fail` : ""}${e.unmeasured ? `, and ${e.unmeasured} I can't measure` : ""}.`);
  if (e.agent === "render" && e.type === "done" && !o.replay) loadRender(e, st.req?.pick?.recipe === "stored board");
  if (e.agent === "sourcing" && e.type === "done" && e.ok && !o.quiet) {
    const pv = e.provenance || {};
    $("srcnm").textContent = e.key; $("srcn").textContent = `${e.seconds.toFixed(1)} s`;
    $("srcroute").textContent = pv.route || ""; $("srcurl").textContent = pv.source_url || pv.api_url || "";
    $("srclic").textContent = pv.licence || "KiCad libraries: CC-BY-SA 4.0 with exception";
    const c = $("srcc"); c.style.transition = "opacity .5s"; c.style.opacity = 1;
    setTimeout(() => (c.style.opacity = 0), 9000);
  }
}
/* ---------- library parts: a tile per KiCad part as the engine screens and confirms it ---------- */
const PCS = new Map();
function piecesClear() { for (const el of PCS.values()) el.remove(); PCS.clear(); put($("pieces"), "opacity", "1"); }
function pieceShow(e) {
  if (PCS.has(e.k)) return;
  const el = document.createElement("div"); el.className = "pc";
  const im = new Image(); im.alt = ""; im.decoding = "async"; im.src = API + e.img;
  const nm = document.createElement("b"); nm.textContent = String(e.n || "").split(" / ")[0];
  el.title = e.n || ""; el.append(im, nm); PCS.set(e.k, el); $("pieces").appendChild(el);
}
function pieceConfirm(e) {
  for (const p of e.parts) {
    let el = PCS.get(p.k); if (!el) { pieceShow({ k: p.k, img: p.img, n: p.k.split(":").pop().split("@")[0] }); el = PCS.get(p.k); }
    el.classList.add("ok"); const tag = document.createElement("em"); tag.textContent = p.refs.length > 3 ? `${p.refs[0]}+${p.refs.length - 1}` : p.refs.join(" "); el.appendChild(tag);
  }
  for (const k of e.drop) { const el = PCS.get(k); if (!el) continue; PCS.delete(k); el.classList.add("out"); setTimeout(() => el.remove(), 380); }
}
// fast = a board built before (stored, or reopened from the library or the version strip): no part-by-part
// drop-in, the 3D view takes over as soon as its GLB is in. The GLB is fetched alongside the frames either way.
async function loadRender(e, fast = false) {
  const my = FOLLOW, run = st.viewRun, pre = run ? prefetch3d(run) : null;
  if (pre) pre.catch(() => {}); // load3d reports a failure
  const dir = FILES + e.frames.replace(/frames\.json$/, ""), fr = await (await fetch(FILES + e.frames)).json();
  const key = "live" + Date.now();
  if (!(await loadSet(key, dir, fr, { lift: {} }))) return note("could not load the board render");
  const cut = cutouts(key); // part cutouts for the drop-in and the 2D explode
  if (!fast) await cut;
  if (my !== FOLLOW) return; // the viewer moved on while the frames loaded
  put($("pieces"), "opacity", "0"); setTimeout(() => { if ($("pieces").style.opacity === "0") piecesClear(); }, 700); // the board takes over
  for (const r of SETS[key].labels) SETS[key].lift[r] = /^J|^BZ|^BT/.test(r) ? 34 : /^Q|^D|^C/.test(r) ? 22 : 12;
  const pick = st.req?.pick?.parts || [];
  st.why = Object.fromEntries(pick.map((p) => [p.ref, { name: `${short(p.piece)} ${p.value || ""}`.trim(), plain: p.why }]));
  v2reset(true);
  st.prevKey = st.setKey; st.setKey = key; st.setT = fast ? now() - 0.55 * (last(key) + 0.6) : now(); st.exTarget = 0; st.explode = 0;
  load3d(run, key, pre);
}

/* ---------- engine status ---------- */
async function health() {
  try { const h = await (await fetch(API + "/etch/health", { cache: "no-store" })).json(); st.online = !!h.ok; st.sttUp = !!h.stt; }
  catch (e) { st.online = false; }
  put($("dot"), "class", st.online ? "on" : st.online === false ? "off" : "");
  put($("dott"), "text", st.online ? "ENGINE ONLINE" : st.online === false ? "ENGINE OFFLINE" : "CONNECTING");
  if (st.online === false) put($("loff"), "display", "");
  else put($("loff"), "display", "none");
}
health().then(pollRuns); setInterval(health, 20000);

/* ---------- one frame ---------- */
let STEPN = 0, lastT = now();
const TOUCH = matchMedia("(pointer: coarse)").matches;
const VLAB = { listening: "LISTENING · CLICK WHEN DONE", heard: "GOT IT", thinking: "THINKING...", speaking: "TALKING · CLICK TO CUT IN" };
function frame() {
  const T = now(), dt = Math.min(0.1, T - lastT); lastT = T;
  st.idle += (st.idleTarget - st.idle) * Math.min(1, dt * 3);
  document.body.classList.toggle("home", st.idle > 0.5);
  drawBg(T, 0.28 + 0.72 * st.idle);
  // steps (the hint line never scrolls away)
  const el = $("steps");
  if (st.log.length !== STEPN) {
    for (const l of st.log.slice(STEPN)) { const d = document.createElement("div"); d.innerHTML = l.html; el.appendChild(d); }
    let n = el.children.length - 1; for (const c of [...el.children]) { if (n <= 22) break; if (c.id !== "stepsh") { c.remove(); n--; } }
    STEPN = st.log.length;
  }
  put($("stepsh"), "display", st.log.length ? "none" : "");
  gatesDraw(gateRows(st.req));
  put($("gatesh"), "display", st.req && st.req.iters.length ? "none" : "");
  // the start page: Etch is the whole hero
  const ld = $("landing"); put(ld, "opacity", st.idle.toFixed(3)); put(ld, "display", st.idle > 0.01 ? "" : "none"); put(ld, "pointerEvents", st.idleTarget > 0.5 ? "auto" : "none");
  put(ld, "transform", `translateY(${((1 - st.idle) * -14).toFixed(1)}px)`);
  put($("hchips"), "display", st.idleTarget > 0.5 ? "" : "none");
  // voice mode: one clear state at a time on the mascot
  const speaking = !!(cur && cur.a) || (!!cur && !cur.silent && etchLevel > 0);
  const vs = rec ? "listening" : st.sttBusy ? "thinking" : st.heardHow === "spoken" && T - st.heardT < 1.2 ? "heard" : speaking ? "speaking" : st.fetching || (TALKED && !cur && VQ.length) ? "thinking" : "";
  const lab = vs === "listening" && rec?.auto ? "LISTENING..." : VLAB[vs] || (st.vmode ? "CLICK ME TO TALK AGAIN" : "CLICK ME TO TALK · OR HOLD SPACE");
  for (const [w, l, cv] of [[$("heroW"), $("heroL"), $("mL")], [$("bandW"), $("bandL"), $("mB")]]) {
    put(w, "class", "etchw " + vs); put(l, "text", lab);
    w.style.setProperty("--lv", etchLevel.toFixed(3));
    put(cv, "transform", vs === "listening" ? `scale(${(1 + 0.07 * micLevel).toFixed(3)}) translateY(${(-4 * micLevel).toFixed(1)}px)` : "");
  }
  if (st.idle > 0.01) drawEtch($("mL"), T, etchLevel > 0.2 ? "talk" : "idle");
  if (st.idle < 0.99) drawEtch($("mB"), T, etchLevel > 0.2 ? "talk" : "idle");
  // board: the last render builds up part by part (0.55 s each), then LEDs light from the ngspice result
  bx.clearRect(0, 0, 1380, 874); svg = "";
  st.explode += (st.exTarget - st.explode) * Math.min(1, dt * 1.6);
  let curp = null;
  if (st.prevKey && SETS[st.prevKey] && (!st.setKey || T - st.setT < 0.55)) draw(st.prevKey, last(st.prevKey), st.setKey ? 1 - (T - st.setT) / 0.55 : Math.max(0, 1 - st.idle * 1.5) * (st.busy ? 0.35 : 0));
  // the 3D board takes over once the drop-in has finished and its GLB is loaded
  const v3on = !!(st.v3.ready && st.v3.run === st.viewRun && st.idle < 0.5 && (st.v3.solo || (st.setKey && st.v3.key === st.setKey && (T - st.setT) / 0.55 >= last(st.setKey) + 0.6)));
  st.v3.a += ((v3on ? 1 : 0) - st.v3.a) * Math.min(1, dt * 4); if (st.v3.a < 0.002) st.v3.a = 0;
  // 3D takes over framed like the unzoomed 2D picture: a 2D zoom made during the drop-in eases back as it fades in
  if (v3on && v2moved()) v2reset();
  const v3a = st.v3.a, v3el = $("v3");
  put(v3el, "opacity", v3a.toFixed(3)); put(v3el, "pointerEvents", v3a > 0.5 ? "auto" : "none"); put(v3el, "display", v3a > 0 || v3on ? "" : "none");
  if (VIEW3 && v3a > 0) { VIEW3.setExplode(st.explode); VIEW3.setLeds(ledsOn(st.sim, T)); v3cardMove(); }
  // the 2D board eases to its pan / zoom; it takes the pointer only while it is the board on show
  const k2 = Math.min(1, dt * 14); V2.s += (V2T.s - V2.s) * k2; V2.x += (V2T.x - V2.x) * k2; V2.y += (V2T.y - V2.y) * k2;
  const on2 = v2on(), on3 = v3a > 0.5;
  put($("bc"), "touchAction", on2 ? "none" : ""); put($("bc"), "cursor", on2 ? "grab" : "");
  // how to move it (until the first move this visit), and a way back once it has moved
  put($("movh"), "opacity", (on3 || on2) && !HINTED ? "1" : "0");
  put($("movh"), "text", (on3 ? "DRAG TO ROTATE" : "DRAG TO MOVE") + (TOUCH ? " · PINCH TO ZOOM" : " · SCROLL TO ZOOM"));
  const mv = on3 ? !!VIEW3 && VIEW3.moved() : on2 && v2moved();
  put($("resetb"), "opacity", mv ? "1" : "0"); put($("resetb"), "pointerEvents", mv ? "auto" : "none");
  if (v3a <= 0.5) v3card(null);
  if (st.setKey && v3a < 0.999) {
    const k = st.setKey, n = last(k), u = (T - st.setT) / 0.55, idx = Math.min(n, u);
    if (st.explode > 0.003 && idx >= n && v3a < 0.5) { const br = st.exTarget > 0.5 ? 0.9 + 0.1 * Math.sin(T * 0.9) : 1; lifted(k, st.explode * br); }
    else {
      build(k, idx, 0, clamp(u * 2) * (1 - st.idle) * (1 - v3a));
      const c = landed(k, idx, 0);
      if (c && (idx < n || T - st.setT < n * 0.55 + 2)) curp = [k, c.ref, idx < n ? c.o : 1 - clamp((T - st.setT - n * 0.55 - 1.6) / 0.4)];
      if (idx >= n && st.sim) blink(k, st.sim, T, (1 - st.idle) * (1 - v3a));
    }
  }
  card(curp?.[0], curp?.[1], (curp?.[2] ?? 0) * (1 - st.idle) * (1 - v3a), curp && st.why[curp[1]]);
  $("ov").innerHTML = OVDEFS + svg;
  const sim = st.setKey && st.sim && st.explode < 0.05 && st.idle < 0.5 ? simText(st.setKey, st.sim) : "";
  if (sim) put($("sim"), "text", sim); put($("sim"), "opacity", sim ? "1" : "0");
  const r = st.req, rnow = r ? T - st.reqStart : 0;
  // the classifier / picker / assembly card while the engine works
  const working = st.busy && r && r.cls && !r.render && st.idle < 0.5;
  kcard(r && liveTimes(r), r ? r.lastT ?? 0 : 0, working && r.kind !== "bench" ? 1 : 0);
  xcard(r, r && r.exp ? 1 - st.idle : 0);
  const showB = (st.setKey || (st.v3.solo && v3a > 0.5)) && st.idle < 0.5;
  put($("expandb"), "opacity", showB ? "1" : "0"); put($("expandb"), "pointerEvents", showB ? "auto" : "none");
  put($("expandb"), "class", "lay" + (st.exTarget > 0.5 ? " on" : "")); put($("expandb"), "text", st.exTarget > 0.5 ? "PUT BACK" : "EXPAND");
  const zipOn = !st.busy && !!st.viewRun && !!(r && r.exp) && st.idle < 0.5;
  put($("zipb"), "opacity", zipOn ? "1" : "0"); put($("zipb"), "pointerEvents", zipOn ? "auto" : "none");
  versDraw();
  put($("cancelb"), "display", st.busy && st.viewJob && TOK[st.viewJob] && st.view !== "lib" ? "" : "none");
  // waiting for the engine (another build first, or the render lock)
  const wait = st.idle > 0.5 ? "" : st.busy && !r ? (st.queued != null ? `Another build is running first · ${st.queued} ahead of you` : "Starting the engine...") : st.busy && r && r.renderStart && !r.render ? "Rendering the board in KiCad 3D..." : "";
  put($("wait"), "text", wait); put($("wait"), "opacity", wait ? "1" : "0");
  // timer: real time since the request was sent
  const fin = r && (r.done || r.benchDone);
  if ((r || st.busy) && st.idle < 0.5) {
    if (r && r.cancel && !fin) timerDraw(1, r.cancel.t, r.cancel.label || "CANCELLED", false);
    else timerDraw(1, fin ? (fin.timings?.total_s ?? fin.seconds ?? rnow) : rnow, fin ? (r.done?.route === "stored board" ? "STORED · BUILT BEFORE" : r.done && r.done.ok === false && r.done.passed === undefined ? "STOPPED · REAL TIME" : "DONE · REAL TIME") : "ETCH WORKING · REAL TIME", !!fin);
  } else timerDraw(0, 0, "", false);
  let ptxt = "";
  if (fin && r.kind === "bench" && r.benchDone) ptxt = `Your checks: ${r.benchDone.passed} pass · ${r.benchDone.failed} fail${r.benchDone.unmeasured ? ` · ${r.benchDone.unmeasured} can't measure` : ""}`;
  else if (fin && r.done && r.done.ok && r.done.timings) { const it = r.iters.at(-1), gs = it ? it.gates.filter((x) => x.check || x.gate === "requested change present") : []; ptxt = `${r.done.route === "stored board" ? "Built before" : `Done in ${r.done.timings.total_s.toFixed(1)} s`} · ${gs.filter((x) => x.ok).length} of ${gs.length} checks pass`; }
  pingDraw(ptxt && st.explode < 0.05 && st.idle < 0.5 && st.setKey ? 1 : 0, ptxt, null, null);
  put($("floaty"), "display", "none"); // the mascot next to the speech bubble replaces the floating one
  if (rec) ci.querySelectorAll(".mic i").forEach((b, k) => { b.style.height = (3 + 11 * clamp(micLevel * (0.8 + 0.25 * k))).toFixed(1) + "px"; });
  // the speech bubble by the mascot: what you said, then Etch's line word by word; fades out when quiet
  const youOn = !!st.heard && T - st.heardT < 2.4, etchOn = !youOn && !!st.sayText && T < st.sayT + st.sayDur + 8;
  const status = rec ? "I'm listening..." : st.sttBusy ? "Working out what you said..." : "";
  const bub = $("bub");
  if (status && !youOn) {
    put($("whot"), "text", "YOU"); put($("who"), "color", AMB); put($("cap"), "class", "");
    flow($("cap"), "s" + status, status.split(" "), 99, 1);
    put(bub, "class", "you"); put(bub, "opacity", "1");
  } else if (etchOn || youOn) {
    const l = etchOn ? st.sayText : st.heard, words = l.split(" ");
    put($("whot"), "text", etchOn ? "ETCH" : st.heardHow === "spoken" ? "YOU SAID" : "YOU ASKED"); put($("who"), "color", etchOn ? V : AMB); put($("cap"), "class", etchOn ? "etch" : "");
    flow($("cap"), (etchOn ? "e" : "u") + l, words, etchOn ? wordPos(words, lin(T, st.sayT, st.sayT + st.sayDur)) : words.length, 0.38);
    put(bub, "class", etchOn ? "" : "you"); put(bub, "opacity", "1");
  } else put(bub, "opacity", "0");
  vu(T, etchOn);
  put($("clock"), "text", new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  requestAnimationFrame(frame);
}
// live: events carry the engine's own run seconds (e.t); the card's clock is the newest event time plus the
// time since it arrived, so the classifier marks and picker timer move in real time
function liveTimes(r) { const lastE = [r.cls, ...r.batches, r.clsDone, r.filt, r.pickStart, r.pick].filter(Boolean).at(-1); r.lastT = lastE ? lastE.t + (now() - (lastE.lt ?? now())) : 0; return r; }
setView("home");
await document.fonts.ready;
window.__ready = true;
frame();

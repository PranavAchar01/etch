// Explore: the open-source board library (world/index.json). Category chips with a liquid indicator, an
// expanding search, a licence switch, sort, a lazily filled grid of springy cards, and prefetch: boards near the
// viewport get their small files warmed into Cache Storage, a hovered or touched board its GLB as well.
import { Q, $, h, esc, icon, LOGO, carry, page, fmtStars, prefetchBoard, cachedFetch, boardFiles, WORLD, registerSW, ago, toast, isLive, slugOf } from "./common.js";

import { spring, reducedMotion } from "./ui/spring.js";
import { gooFilter } from "./ui/goo.js";
import * as mine from "./my-boards.js";
import * as etch from "./etch-api.js";
import { resolveApi, mountBackendButton } from "./backend.js";
import { mountJobPill, jobHref, jobBoardId } from "./ui/job-pill.js";

registerSW();
$("logo").innerHTML = LOGO;
$("sIc").innerHTML = icon("search", 18);
$("xIc").innerHTML = icon("close", 14);
$("plusIc").innerHTML = icon("plus", 18);
// explore is the homepage: the logo comes back here, "Build a board" opens the editor on an empty workplane
$("homeL").href = (/\.html$/.test(location.pathname) ? "explore.html" : "/") + carry();
for (const id of ["buildL", "buildTop"]) $(id).href = page("board") + carry({ new: 1 });

const ORDER = ["keyboards", "iot", "computing", "dev-boards", "audio", "robotics", "tools", "rf", "retro", "lighting", "displays", "power", "wearables", "everything-else"];
const st = { q: Q.get("q") || "", cat: Q.get("cat") || "", sort: Q.get("sort") === "recent" ? "recent" : "popular", copyable: Q.get("copyable") === "1", shown: 0 };
let ALL = [], CATS = [], list = [];
const PAGE = 48;

function syncURL() {
  const p = new URLSearchParams(location.search);
  for (const [k, v] of [["q", st.q], ["cat", st.cat], ["sort", st.sort === "recent" ? "recent" : ""], ["copyable", st.copyable ? "1" : ""]]) v ? p.set(k, v) : p.delete(k);
  const s = p.toString();
  history.replaceState(null, "", location.pathname + (s ? "?" + s : ""));
}

const words = () => st.q.toLowerCase().split(/\s+/).filter(Boolean);
function filter() {
  const w = words();
  list = ALL.filter((b) => (!st.cat || b.category === st.cat) && (!st.copyable || b.copyable) && w.every((x) => b._s.includes(x)));
  // boards with no parts (bare plates, panels) go last either way
  const bare = (b) => (b.parts ? 0 : 1);
  const when = (b) => String(b.updated || b.pushed || b.rendered || "");
  if (st.sort === "popular") list.sort((a, b) => bare(a) - bare(b) || (b.stars || 0) - (a.stars || 0) || a.title.localeCompare(b.title));
  else list.sort((a, b) => bare(a) - bare(b) || when(b).localeCompare(when(a)) || b._i - a._i);
}

// ---- category chips + goo indicator ----
// Two blobs chase the active chip: the lead on a stiff spring, the tail on a loose one. Under the goo filter they
// stretch into one drop while travelling and pinch back into a pill when they land.
const cats = $("cats"), goo = cats.querySelector(".goo"), [bA, bB] = goo.querySelectorAll(".blob"), bridge = goo.querySelector(".bridge");
goo.style.filter = gooFilter({ id: "xp-goo", blur: 7, alpha: 22, shift: -9 });
// a neck between the two blobs: thins as they pull apart, so the drop pinches instead of snapping in two
let want = 0, top = 0, H = 38;
function paintBridge() {
  const R = H / 2;
  const l = Math.min(lead.x.value, tail.x.value) + R, r = Math.max(lead.x.value + lead.w.value, tail.x.value + tail.w.value) - R;
  const span = Math.max(0, r - l), stretch = want ? span / Math.max(1, want - H) : 1;
  const th = Math.max(10, Math.min(H, H / Math.max(1, stretch * 0.9)));
  bridge.style.width = span + "px";
  bridge.style.height = th + "px";
  bridge.style.transform = `translate(${l}px, ${top + (H - th) / 2}px)`;
}
const paint = (el, x, w) => { if (!w) return; el.style.transform = `translateX(${x.value}px)`; el.style.width = Math.max(0, w.value) + "px"; if (tail) paintBridge(); };
const mk = (el, k, d) => {
  const o = {};
  // precision in px: rest within 0.1 px (the default 0.002 is meant for 0..1 values and keeps the loop alive)
  o.x = spring(0, { stiffness: k, damping: d, precision: 0.1, onUpdate: () => paint(el, o.x, o.w) });
  o.w = spring(0, { stiffness: k, damping: d, precision: 0.1, onUpdate: () => paint(el, o.x, o.w) });
  return o;
};
let tail = null;
const lead = mk(bA, 420, 30);
tail = mk(bB, 190, 21);
let gooReady = false, gooOn = null;

// a new active chip: the drop travels. The same chip moved or resized (counts, fonts, viewport): follow it at once,
// unless the drop is still travelling, then just retarget so it lands on the new spot.
function moveGoo() {
  const on = cats.querySelector(".cat.on");
  goo.style.setProperty("--cw", cats.scrollWidth + "px");
  if (!on) { goo.classList.remove("ready"); gooOn = null; return; }
  const x = on.offsetLeft, w = on.offsetWidth, y = on.offsetTop;
  if (!w) return; // not laid out (display:none)
  H = on.offsetHeight;
  for (const el of [bA, bB]) { el.style.top = y + "px"; el.style.height = H + "px"; el.style.borderRadius = H / 2 + "px"; }
  bridge.style.top = "0"; top = y; want = w;
  const travelling = lead.x.moving || tail.x.moving || lead.w.moving || tail.w.moving;
  const instant = !gooReady || reducedMotion() || (on === gooOn && !travelling);
  gooOn = on;
  if (instant) { for (const b of [lead, tail]) { b.x.jump(x); b.w.jump(w); } gooReady = true; goo.classList.add("ready"); return; }
  for (const b of [lead, tail]) { b.x.set(x); b.w.set(w); }
}

function renderCats() {
  // counts follow the search + licence filter, so a chip never promises boards that aren't there
  const w = words(), n = {}; let total = 0;
  for (const b of ALL) if ((!st.copyable || b.copyable) && w.every((x) => b._s.includes(x))) { n[b.category] = (n[b.category] || 0) + 1; total++; }
  const items = [["", "All", total], ...CATS.map((c) => [c.id, c.label, n[c.id] || 0])];
  let chips = [...cats.querySelectorAll(".cat")];
  if (chips.length !== items.length) {
    chips.forEach((c) => c.remove());
    chips = items.map(([id]) => {
      const b = h("button", { class: "cat", type: "button", "data-id": id }, h("span", {}), h("em", {}));
      b.addEventListener("click", () => {
        st.cat = st.cat === id && id ? "" : id;
        update();
        b.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "nearest", inline: "nearest" });
      });
      cats.append(b);
      if (chipRO) chipRO.observe(b);
      return b;
    });
  }
  items.forEach(([id, label, c], i) => {
    const b = chips[i], on = st.cat === id;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
    b.disabled = !!(id && !c && !on);
    b.firstChild.textContent = label;
    b.lastChild.textContent = c.toLocaleString();
    b.setAttribute("aria-label", `${label}, ${c.toLocaleString()} board${c === 1 ? "" : "s"}`);
  });
  moveGoo();
}
// chip widths change with counts, fonts and the viewport: keep the drop on its chip
if ("ResizeObserver" in window) new ResizeObserver(() => moveGoo()).observe(cats);
const chipRO = "ResizeObserver" in window ? new ResizeObserver(() => moveGoo()) : null;
addEventListener("resize", () => moveGoo());
if (document.fonts) document.fonts.addEventListener?.("loadingdone", () => { moveGoo(); moveSeg(true); });

// ---- search: an icon that pours out into a field ----
const sBox = $("search"), qIn = $("q");
qIn.value = st.q;
function openSearch(focus = true) {
  sBox.classList.add("open"); qIn.tabIndex = 0; $("sX").tabIndex = 0;
  $("sBtn").setAttribute("aria-expanded", "true");
  // focus at once: a delayed focus drops the first keys of a fast typist onto the button
  if (focus && document.activeElement !== qIn) qIn.focus({ preventScroll: true });
}
function closeSearch() {
  if (qIn.value) return;
  sBox.classList.remove("open"); qIn.tabIndex = -1; $("sX").tabIndex = -1;
  $("sBtn").setAttribute("aria-expanded", "false");
}
const markHas = () => sBox.classList.toggle("has", !!qIn.value);
if (st.q) openSearch(false);
markHas();
$("sBtn").addEventListener("click", () => (sBox.classList.contains("open") && !qIn.value ? (qIn.blur(), closeSearch()) : openSearch()));
qIn.addEventListener("blur", () => setTimeout(() => { if (!sBox.contains(document.activeElement)) closeSearch(); }, 120));
let qt = 0;
qIn.addEventListener("input", () => { markHas(); clearTimeout(qt); qt = setTimeout(() => { st.q = qIn.value.trim(); update(); }, 90); });
// Escape clears, closes and hands focus back to the search button (never strands it on <body>)
qIn.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  e.preventDefault(); clearTimeout(qt);
  const had = !!st.q; qIn.value = ""; markHas(); st.q = "";
  if (had) update();
  closeSearch(); $("sBtn").focus();
});
$("sX").addEventListener("click", () => { clearTimeout(qt); qIn.value = ""; markHas(); st.q = ""; update(); qIn.focus(); });
addEventListener("keydown", (e) => {
  const a = document.activeElement;
  if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && a !== qIn && !/input|textarea|select/i.test(a?.tagName || "") && !a?.isContentEditable) { e.preventDefault(); openSearch(false); qIn.focus({ preventScroll: true }); }
});

// ---- copyable switch ----
const sw = $("copyable");
const paintSw = () => sw.setAttribute("aria-checked", String(st.copyable));
paintSw();
sw.addEventListener("click", () => { st.copyable = !st.copyable; paintSw(); update(); });

// ---- sort: segmented control, pill on springs (it stretches a little as it moves) ----
const seg = $("sort"), ind = seg.querySelector(".seg-ind");
const segX = spring(0, { stiffness: 380, damping: 28, precision: 0.1, onUpdate: () => paintSeg() });
const segW = spring(0, { stiffness: 220, damping: 20, precision: 0.1, onUpdate: () => paintSeg() });
function paintSeg() { ind.style.transform = `translateX(${segX.value}px)`; ind.style.width = segW.value + "px"; }
function moveSeg(instant) {
  const b = seg.querySelector(`[data-v="${st.sort}"]`);
  for (const x of seg.querySelectorAll("button")) { x.setAttribute("aria-checked", String(x === b)); x.tabIndex = x === b ? 0 : -1; }
  if (!b.offsetWidth) return;
  instant || reducedMotion() ? (segX.jump(b.offsetLeft), segW.jump(b.offsetWidth)) : (segX.set(b.offsetLeft), segW.set(b.offsetWidth));
}
seg.addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b || b.dataset.v === st.sort) return;
  st.sort = b.dataset.v; moveSeg(false); update();
});
seg.addEventListener("keydown", (e) => {
  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) return;
  e.preventDefault();
  const v = e.key === "Home" ? "popular" : e.key === "End" ? "recent" : st.sort === "popular" ? "recent" : "popular";
  if (v === st.sort) return;
  st.sort = v; moveSeg(false); update();
  seg.querySelector(`[data-v="${st.sort}"]`).focus();
});
moveSeg(true);
// same chip, new size (fonts, viewport): follow at once unless the pill is mid-flight
if ("ResizeObserver" in window) { const ro = new ResizeObserver(() => moveSeg(!segX.moving && !segW.moving)); for (const b of seg.querySelectorAll("button")) ro.observe(b); }

// ---- cards ----
// a board's meta.json (small, prefetched anyway) carries the pinned commit; the source arrow links there once known
const metaOf = new Map();
function meta(slug) {
  if (!metaOf.has(slug)) metaOf.set(slug, cachedFetch(boardFiles(slug).meta, { refresh: false }).then((r) => (r.ok ? r.json() : null)).catch(() => null));
  return metaOf.get(slug);
}
// warm a board: meta first (through the same cache, so it is fetched once), then the rest
function warmBoard(slug, glb) {
  meta(slug).then(() => setTimeout(() => prefetchBoard(slug, { glb }), 30));
}
const io = "IntersectionObserver" in window ? new IntersectionObserver((es) => {
  for (const e of es) if (e.isIntersecting) { const c = e.target; warmBoard(c.dataset.slug, (+c.dataset.bytes || 0) < 1.5e6 && nearBudget-- > 0); sourceLink(c); io.unobserve(c); }
}, { rootMargin: "300px 0px" }) : null;
let nearBudget = 8; // GLBs warmed just for being near the viewport; hover always warms
function sourceLink(c) {
  const s = c.querySelector(".src");
  if (!s || s.dataset.pinned) return;
  meta(c.dataset.slug).then((m) => {
    if (!m || !m.commit) return;
    const base = (m.url || s.href).replace(/\/+$/, "");
    s.href = `${base}/tree/${m.commit}`;
    s.dataset.pinned = "1";
  });
}

// KiCad's stills sit on a lavender gradient: key it out to pure white. The backdrop is flood-filled in from the
// edges (so board pixels that happen to be lavender stay), its edge feathered, and the lavender un-mixed from the
// feathered pixels so no tinted fringe is left. Returns a canvas cropped to the board, or null.
// worked at most 480 px wide (a card shows it at ~240 css px), about 10 ms for a 640x400 still on a slow machine
function whiteThumb(img) {
  const k0 = Math.min(1, 480 / (img.naturalWidth || 1));
  const w = Math.round(img.naturalWidth * k0), hh = Math.round(img.naturalHeight * k0); if (!w || !hh) return null;
  const cv = document.createElement("canvas"); cv.width = w; cv.height = hh;
  const g = cv.getContext("2d", { willReadFrequently: true });
  let d;
  try { g.drawImage(img, 0, 0, w, hh); d = g.getImageData(0, 0, w, hh); } catch (e) { return null; }
  const p = d.data, n = w * hh;
  // backdrop colour per row from the outermost pixels; rows where the board touches an edge keep the last good one
  const bg = new Float32Array(hh * 3);
  let br = p[0], bgc = p[1], bb = p[2];
  for (let y = 0; y < hh; y++) {
    const a = y * w * 4, z = (y * w + w - 1) * 4;
    const da = Math.abs(p[a] - br) + Math.abs(p[a + 1] - bgc) + Math.abs(p[a + 2] - bb), dz = Math.abs(p[z] - br) + Math.abs(p[z + 1] - bgc) + Math.abs(p[z + 2] - bb);
    const e = da <= dz ? a : z; if (Math.min(da, dz) < 24) { br = p[e]; bgc = p[e + 1]; bb = p[e + 2]; }
    bg[y * 3] = br; bg[y * 3 + 1] = bgc; bg[y * 3 + 2] = bb;
  }
  const LO = 14, HI = 46; // colour distance: below LO pure backdrop, above HI pure board
  // the raytracer's contact shadow is the backdrop darkened (same hue): s = how much of the backdrop is left,
  // off = how far the pixel is from that pure darkening, relative to its brightness
  const dist = new Float32Array(n), shade = new Float32Array(n), shadow = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const y = (i / w) | 0, o = i * 4, r0 = bg[y * 3], g0 = bg[y * 3 + 1], b0 = bg[y * 3 + 2];
    const dr = p[o] - r0, dg = p[o + 1] - g0, db = p[o + 2] - b0;
    dist[i] = Math.sqrt(dr * dr + dg * dg + db * db);
    const k = (p[o] * r0 + p[o + 1] * g0 + p[o + 2] * b0) / (r0 * r0 + g0 * g0 + b0 * b0 || 1);
    const pr = p[o] - k * r0, pg = p[o + 1] - k * g0, pb = p[o + 2] - k * b0;
    const off = Math.sqrt(pr * pr + pg * pg + pb * pb) / Math.max(1, k * Math.sqrt(r0 * r0 + g0 * g0 + b0 * b0));
    shade[i] = k;
    shadow[i] = k > 0.42 && k < 1.06 && off < 0.035 ? 1 : 0;
  }
  // flood from the border through backdrop and its shadow
  const inBg = new Uint8Array(n), stack = new Int32Array(n); let sp = 0;
  const push = (i) => { if (!inBg[i] && (dist[i] < HI || shadow[i])) { inBg[i] = 1; stack[sp++] = i; } };
  for (let x = 0; x < w; x++) { push(x); push((hh - 1) * w + x); }
  for (let y = 0; y < hh; y++) { push(y * w); push(y * w + w - 1); }
  while (sp) { const i = stack[--sp], x = i % w; if (x > 0) push(i - 1); if (x < w - 1) push(i + 1); if (i >= w) push(i - w); if (i < n - w) push(i + w); }
  const SHADOW = 0.3; // keep a faint, neutral grey contact shadow (0 = none)
  // alpha: board 1, backdrop/shadow 0, the backdrop's soft edge in between; then a 3x3 box blur so the cut-out
  // edge is anti-aliased instead of stair-stepped
  const al = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    if (!inBg[i]) al[i] = 1;
    else if (shadow[i]) al[i] = 0;
    else { const t = Math.min(1, Math.max(0, (dist[i] - LO) / (HI - LO))); al[i] = t * t * (3 - 2 * t); }
  }
  const sa = new Float32Array(n);
  for (let y = 0; y < hh; y++) for (let x = 0; x < w; x++) {
    let sum = 0, c = 0;
    for (let j = Math.max(0, y - 1); j <= Math.min(hh - 1, y + 1); j++) for (let k = Math.max(0, x - 1); k <= Math.min(w - 1, x + 1); k++) { sum += al[j * w + k]; c++; }
    sa[y * w + x] = sum / c;
  }
  let x0 = w, y0 = hh, x1 = -1, y1 = -1;
  for (let i = 0; i < n; i++) {
    const o = i * 4, y = (i / w) | 0, x = i % w, a0 = al[i], a = sa[i];
    // what sits behind: white, or the neutral shadow
    const back = inBg[i] && shadow[i] ? 255 * (1 - SHADOW * (1 - Math.min(1, shade[i]))) : 255;
    for (let c = 0; c < 3; c++) {
      // the pixel with the lavender un-mixed out (composite = a0*fg + (1-a0)*bg)
      const fg = a0 > 0.02 ? Math.min(255, Math.max(0, (p[o + c] - (1 - a0) * bg[y * 3 + c]) / a0)) : back;
      p[o + c] = Math.round(a * fg + (1 - a) * back);
    }
    p[o + 3] = 255;
    if (a > 0.06) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  }
  if (x1 < 0) return null;
  g.putImageData(d, 0, 0);
  // crop to the board with a little white margin so every board centres the same way
  const m = 6; x0 = Math.max(0, x0 - m); y0 = Math.max(0, y0 - m); x1 = Math.min(w - 1, x1 + m); y1 = Math.min(hh - 1, y1 + m);
  const out = document.createElement("canvas"); out.width = x1 - x0 + 1; out.height = y1 - y0 + 1;
  out.getContext("2d").drawImage(cv, x0, y0, out.width, out.height, 0, 0, out.width, out.height);
  return out;
}

// hover lifts the card a little on a spring and nudges the source arrow up-right; press squishes, release settles
const lerpC = (a, b, t) => Math.round(a + (b - a) * Math.min(1, Math.max(0, t)));
function springy(c) {
  const arrow = c.querySelector(".src"), glyph = arrow.firstChild;
  let lift = 0, press = 0, apress = 0;
  const draw = () => {
    const p = sq.value, y = ly.value;
    c.style.transform = Math.abs(p) > 1e-4 || Math.abs(y) > 1e-4 ? `translateY(${(-3 * y).toFixed(2)}px) scale(${(1 + 0.012 * p).toFixed(4)}, ${(1 - 0.02 * p).toFixed(4)})` : "";
    glyph.style.transform = Math.abs(y) > 1e-4 ? `translate(${(2 * y).toFixed(2)}px, ${(-2 * y).toFixed(2)}px)` : "";
    const k = lerpC(241, 226, y);
    arrow.style.backgroundColor = y > 1e-3 ? `rgb(${k},${k},${k})` : "";
    const q = aq.value;
    arrow.style.scale = Math.abs(q) > 1e-4 ? `${(1 + 0.08 * q).toFixed(4)} ${(1 - 0.12 * q).toFixed(4)}` : "";
  };
  const ly = spring(0, { stiffness: 300, damping: 26, onUpdate: draw });
  const sq = spring(0, { stiffness: 600, damping: 34, onUpdate: draw });
  const aq = spring(0, { stiffness: 600, damping: 30, onUpdate: draw });
  const go = () => { ly.set(lift); sq.set(press); aq.set(apress); };
  c.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") { lift = 1; go(); } });
  c.addEventListener("pointerleave", () => { lift = c.querySelector(":focus-visible") ? 1 : 0; press = apress = 0; go(); });
  c.addEventListener("pointerdown", (e) => { if (e.button > 0) return; if (arrow.contains(e.target)) apress = 1; else press = 1; go(); });
  for (const t of ["pointerup", "pointercancel", "dragstart"]) c.addEventListener(t, () => { press = apress = 0; go(); });
  c.addEventListener("focusin", (e) => { if (e.target.matches(":focus-visible")) { lift = 1; go(); } });
  c.addEventListener("focusout", (e) => { if (!c.contains(e.relatedTarget)) { lift = 0; go(); } });
}

// keyed thumbs are kept per board, so a filter / sort re-render reuses them instead of keying again, and new ones
// are keyed one per idle slice (never inside a busy frame, so the springs keep their frames)
const keyed = new Map();
const copyCanvas = (src) => { const c = document.createElement("canvas"); c.width = src.width; c.height = src.height; c.getContext("2d").drawImage(src, 0, 0); c.className = "in"; return c; };
const kq = []; let kRunning = false;
const idle = window.requestIdleCallback || ((fn) => setTimeout(() => fn({ timeRemaining: () => 16, didTimeout: true }), 16));
function keyQueue(img, slug) { kq.push([img, slug]); if (!kRunning) { kRunning = true; idle(keyRun, { timeout: 1500 }); } }
function keyRun(dl) {
  let n = 0; // two per slice at most, so input arriving meanwhile is never kept waiting long
  while (kq.length && n < 2 && (dl.didTimeout || dl.timeRemaining() > 9)) {
    const [img, slug] = kq.shift();
    if (!img.isConnected && keyed.has(slug)) continue;
    let cv = keyed.get(slug);
    if (!cv) { cv = whiteThumb(img); if (cv) keyed.set(slug, cv); }
    if (!img.isConnected) continue;
    if (cv) img.replaceWith(copyCanvas(cv)); else img.classList.add("in");
    n++;
    if (dl.didTimeout) break;
  }
  if (kq.length) idle(keyRun, { timeout: 1500 }); else kRunning = false;
}
const mm = (v) => (Math.round(v * 10) / 10).toString();
const catLabel = (id) => CATS.find((c) => c.id === id)?.label || (id ? id.replace(/-/g, " ").replace(/^./, (x) => x.toUpperCase()) : "");
const ARROW = '<svg class="ic" width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5.5 12.5 12.5 5.5M7 5.5h5.5V11"/></svg>';
function card(b, i) {
  const f = boardFiles(b.slug);
  const done = keyed.get(b.slug);
  const img = done ? copyCanvas(done) : h("img", { src: f.thumb, alt: "", width: "640", height: "400", loading: "lazy", decoding: "async" });
  if (!done) {
    img.addEventListener("load", () => keyQueue(img, b.slug), { once: true });
    img.addEventListener("error", () => img.remove(), { once: true });
  }
  const lic = b.license || "No licence";
  const specs = [b.parts ? `${b.parts.toLocaleString()} parts` : "No parts", b.layers ? `${b.layers} layers` : "", b.size_mm ? `${mm(b.size_mm[0])} × ${mm(b.size_mm[1])} mm` : ""].filter(Boolean).join(" · ");
  const tip = `${b.title}\n${b.owner}/${b.repo}\n${specs}\nLicence: ${lic}${b.copyable ? " (copying allowed)" : " (view only)"}`;
  const c = h("article", { class: "card", "data-slug": b.slug, "data-bytes": b.glb_bytes || 0, style: `--i:${Math.min(i, 12)}` },
    h("a", { class: "card-main", href: page("board") + carry({ id: b.id }), "aria-label": `${b.title} by ${b.owner}`, title: tip },
      h("div", { class: "th" }, img),
      h("div", { class: "info" },
        h("div", { class: "l1" }, h("span", { class: "who" }, `${b.owner} · ${catLabel(b.category)}`), h("span", { class: "lic" }, ` · ${lic}`)),
        h("div", { class: "l2" }, h("span", { class: "t" }, b.title), h("span", { class: "stars", "aria-label": `${(b.stars || 0).toLocaleString()} stars`, html: icon("star", 14) + esc(fmtStars(b.stars)) })))),
    h("a", { class: "src", href: `https://github.com/${encodeURIComponent(b.owner)}/${encodeURIComponent(b.repo)}`, target: "_blank", rel: "noopener", "aria-label": "View source on GitHub", title: "View source on GitHub", html: `<span class="g">${ARROW}</span>` }));
  const main = c.firstChild;
  const warm = () => { warmBoard(b.slug, true); sourceLink(c); };
  main.addEventListener("pointerenter", warm, { once: true });
  main.addEventListener("focus", warm, { once: true });
  c.addEventListener("touchstart", warm, { once: true, passive: true });
  c.lastChild.addEventListener("pointerenter", () => sourceLink(c), { once: true });
  c.lastChild.addEventListener("focus", () => sourceLink(c), { once: true });
  springy(c);
  if (io) io.observe(c);
  return c;
}

function more() {
  const g = $("grid"), frag = document.createDocumentFragment(), start = st.shown;
  list.slice(start, start + PAGE).forEach((b, i) => frag.append(card(b, i)));
  g.append(frag);
  st.shown = Math.min(list.length, start + PAGE);
}

// state, chips, URL and count update at once; the grid is rebuilt on the next frame, so a chip / sort / switch
// starts moving before the (heavier) card rebuild lands, and fast input coalesces into one rebuild
let gridPending = 0;
function renderGrid() {
  gridPending = 0;
  const g = $("grid");
  if (io) for (const c of g.children) io.unobserve(c);
  g.textContent = ""; st.shown = 0; more(); fill();
}
function update() {
  filter(); renderCats(); syncURL();
  if (!gridPending) gridPending = requestAnimationFrame(() => requestAnimationFrame(renderGrid));
  const label = st.cat ? (CATS.find((c) => c.id === st.cat)?.label || st.cat) : "all categories";
  $("count").innerHTML = `<b>${list.length.toLocaleString()}</b> board${list.length === 1 ? "" : "s"} · ${esc(label)}`;
  const em = $("empty");
  em.hidden = list.length > 0;
  if (!list.length) em.innerHTML = `<b>Nothing here yet</b>${st.q ? `No boards match “${esc(st.q)}”. ` : ""}Try another search${st.copyable ? " or turn off “Copyable only”" : ""}.`;
}

// the sentinel can stay inside the margin after a page lands (tall screens, a re-render while scrolled down), and
// IntersectionObserver only reports changes, so keep filling while it is still near
const sentinel = $("more");
const near = () => sentinel.getBoundingClientRect().top < innerHeight + 800;
let filling = 0;
function fill() { if (filling) return; filling = requestAnimationFrame(() => { filling = 0; if (st.shown < list.length && near()) { more(); fill(); } }); }
new IntersectionObserver((es) => { if (es[0].isIntersecting) fill(); }, { rootMargin: "800px 0px" }).observe(sentinel);

try {
  const idx = await (window.__index || fetch(WORLD + "index.json").then((r) => r.json()));
  ALL = (idx.boards || []).map((b, i) => ({ ...b, _i: i, _s: `${b.title} ${b.owner} ${b.repo} ${b.description || ""} ${(b.topics || []).join(" ")}`.toLowerCase() }));
  const byId = new Map((idx.categories || []).map((c) => [c.id, c]));
  CATS = ORDER.map((id) => byId.get(id) || { id, label: id.replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase()) });
  const owners = new Set(ALL.map((b) => b.owner)).size;
  $("stat").innerHTML = `<b>${ALL.length.toLocaleString()}</b> boards from <b>${owners.toLocaleString()}</b> makers`;
  update();
} catch (e) {
  $("count").textContent = "";
  const em = $("empty"); em.hidden = false;
  em.innerHTML = `<b>The library isn't here yet</b>No <code>${esc(WORLD)}index.json</code> on this server. Try <a href="${page("explore")}?world=fixtures">the fixture library</a>.`;
}

// ---- My boards: what the person saved in the editor, first, in the same white cards (js/my-boards.js) ----
const DOTS = '<svg class="ic" width="18" height="18" viewBox="0 0 18 18" fill="currentColor" aria-hidden="true"><circle cx="4.5" cy="9" r="1.4"/><circle cx="9" cy="9" r="1.4"/><circle cx="13.5" cy="9" r="1.4"/></svg>';
let apiBase = "";
resolveApi().then(({ api }) => { apiBase = api; renderMine(); loadBuilt(); }).catch(() => {});
mountBackendButton(document.getElementById("backendB"));
function closeMenus() { for (const m of document.querySelectorAll(".mine-menu")) m.remove(); }
addEventListener("click", (e) => { if (!e.target.closest(".mine-menu, .mine-more")) closeMenus(); });
addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenus(); });
function mineCard(b, i) {
  const live = isLive(b.id), slug = live ? "" : slugOf(b.id);
  const src = live ? mine.thumbFor(b.id, apiBase) : boardFiles(slug).thumb;
  const img = src ? h("img", { src, alt: "", width: "640", height: "400", loading: "lazy", decoding: "async" }) : null;
  if (img) {
    img.addEventListener("load", () => (live ? img.classList.add("in") : keyQueue(img, slug)), { once: true });
    img.addEventListener("error", () => img.remove(), { once: true });
  }
  const sub = [b.owner || (live ? "Etch build" : ""), b.updated ? "saved " + ago(b.updated / 1000) : ""].filter(Boolean).join(" · ");
  const t = h("span", { class: "t" }, b.title);
  const c = h("article", { class: "card mine-card", "data-id": b.id, style: `--i:${Math.min(i, 12)}` },
    h("a", { class: "card-main", href: page("board") + carry({ id: b.id }), "aria-label": `${b.title}, one of your boards`, title: b.title },
      h("div", { class: "th" }, img || h("div", { class: "th-none" })),
      h("div", { class: "info" },
        h("div", { class: "l1" }, h("span", { class: "yours" }, "Yours"), h("span", { class: "who" }, sub)),
        h("div", { class: "l2" }, t))),
    h("button", { class: "src mine-more", type: "button", "aria-label": `More for ${b.title}`, "aria-haspopup": "menu", html: `<span class="g">${DOTS}</span>` }));
  const more = c.lastChild;
  more.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    const open = c.querySelector(".mine-menu"); closeMenus(); if (open) return;
    const m = h("div", { class: "mine-menu", role: "menu" },
      h("button", { type: "button", role: "menuitem", onclick: () => { closeMenus(); rename(c, b, t); } }, "Rename"),
      h("button", { type: "button", role: "menuitem", class: "bad", onclick: () => { closeMenus(); mine.remove(b.id); toast(`Removed “${b.title}” from My boards`); } }, "Remove"));
    c.append(m); m.firstChild.focus();
  });
  springy(c);
  return c;
}
function rename(c, b, t) {
  const inp = h("input", { class: "mine-in", value: b.title, "aria-label": "Board name", maxlength: "80" });
  t.replaceWith(inp); inp.focus(); inp.select();
  let done = false;
  const end = (keep) => {
    if (done) return; done = true;
    const v = inp.value.replace(/\s+/g, " ").trim();
    if (keep && v && v !== b.title) mine.rename(b.id, v); else inp.replaceWith(t);
  };
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); end(true); } else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); end(false); } });
  inp.addEventListener("blur", () => end(true));
  inp.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); });
}
function renderMine() {
  const ls = mine.list(), g = $("mineGrid");
  $("mine").hidden = !ls.length;
  g.textContent = "";
  ls.slice(0, 8).forEach((b, i) => g.append(mineCard(b, i)));
}
renderMine();
mine.onChange(renderMine);

// a build left running in the editor: the pill at the bottom follows it and goes back to it
// a finished design build goes into My boards, named from what was asked
mountJobPill({ job: etch.job, open: (st, done) => jobHref(st, done, page, carry),
  onReady: (st) => { const id = jobBoardId(st); if (st.design && id) mine.save({ id, title: st.title, thumb: mine.thumbFor(id, apiBase), owner: st.mock ? "" : "Etch build", prompt: st.text }); } });

// ---- Built on Etch: everyone's passing builds, one per distinct prompt, newest first (backend /etch/gallery) ----
const BUILT_FIRST = 4; // the four most involved boards (most parts) lead; "See all" adds the rest, newest first
let built = [], builtAll = false;
function builtCard(b, i) {
  const title = b.concept ? b.concept.replace(/^./, (c) => c.toUpperCase()) : mine.nameFrom(b.text) || "Etch build";
  const img = h("img", { src: mine.thumbFor(b.id, apiBase), alt: "", width: "640", height: "400", loading: "lazy", decoding: "async" });
  img.addEventListener("load", () => img.classList.add("in"), { once: true });
  img.addEventListener("error", () => img.remove(), { once: true });
  const c = h("article", { class: "card mine-card", "data-id": b.id, style: `--i:${Math.min(i, 12)}` },
    h("a", { class: "card-main", href: page("board") + carry({ id: b.id }), "aria-label": `${title}, built on Etch`, title: b.text },
      h("div", { class: "th" }, img),
      h("div", { class: "info" },
        h("div", { class: "l1" }, h("span", { class: "etched" }, "Etch"), h("span", { class: "who" }, ["built " + ago(b.started), b.seconds >= 1 ? `in ${Math.round(b.seconds)} s` : ""].filter(Boolean).join(" "))),
        h("div", { class: "l2" }, h("span", { class: "t" }, title)))),
    h("a", { class: "src", href: page("board") + carry({ id: b.id }), target: "_blank", rel: "noopener", "aria-label": `Open ${title} in a new tab`, title: "Open in a new tab", html: `<span class="g">${ARROW}</span>` }));
  springy(c);
  return c;
}
function renderBuilt() {
  const g = $("builtGrid"), more = $("builtMore");
  $("built").hidden = !built.length;
  $("builtN").textContent = built.length ? (builtAll ? `${built.length} boards, one per idea` : "the most involved boards so far") : "";
  g.textContent = "";
  (builtAll ? built : built.slice(0, BUILT_FIRST)).forEach((b, i) => g.append(builtCard(b, i)));
  more.hidden = builtAll || built.length <= BUILT_FIRST;
  more.textContent = `See all ${built.length}`;
}
$("builtMore").addEventListener("click", () => { builtAll = true; renderBuilt(); });
function loadBuilt() {
  if (!apiBase) return;
  fetch(apiBase + "/etch/gallery", { cache: "no-store" }).then((r) => r.json())
    .then((d) => {
      const all = Array.isArray(d.builds) ? d.builds.filter((b) => isLive(b.id)) : [];
      const top = [...all].sort((a, b) => (b.parts || 0) - (a.parts || 0) || (b.kinds || 0) - (a.kinds || 0)).slice(0, BUILT_FIRST);
      built = [...top, ...all.filter((b) => !top.includes(b))];
      renderBuilt();
    }).catch(() => {});
}

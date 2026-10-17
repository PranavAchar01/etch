// Explore: the open-source board library (world/index.json). Category chips with a liquid indicator, an
// expanding search, a licence switch, sort, a lazily filled grid of springy cards, and prefetch: boards near the
// viewport get their small files warmed into Cache Storage, a hovered or touched board its GLB as well.
import { Q, $, h, esc, icon, LOGO, carry, page, fmtStars, prefetchBoard, boardFiles, WORLD, registerSW, keyBackdrop } from "./common.js";

import { spring, reducedMotion } from "./ui/spring.js";
import { gooFilter } from "./ui/goo.js";

registerSW();
$("logo").innerHTML = LOGO;
$("sIc").innerHTML = icon("search", 18);
$("xIc").innerHTML = icon("close", 14);
$("plusIc").innerHTML = icon("plus", 18);
for (const id of ["homeL", "buildL", "buildTop"]) $(id).href = page("index") + carry();

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
let want = 0, top = 0;
function paintBridge() {
  const l = Math.min(lead.x.value, tail.x.value) + 19, r = Math.max(lead.x.value + lead.w.value, tail.x.value + tail.w.value) - 19;
  const span = Math.max(0, r - l), stretch = want ? span / Math.max(1, want - 38) : 1;
  const th = Math.max(10, Math.min(38, 38 / Math.max(1, stretch * 0.9)));
  bridge.style.width = span + "px";
  bridge.style.height = th + "px";
  bridge.style.transform = `translate(${l}px, ${top + (38 - th) / 2}px)`;
}
const paint = (el, x, w) => { if (!w) return; el.style.transform = `translateX(${x.value}px)`; el.style.width = Math.max(0, w.value) + "px"; if (tail) paintBridge(); };
const mk = (el, k, d) => {
  const o = {};
  o.x = spring(0, { stiffness: k, damping: d, onUpdate: () => paint(el, o.x, o.w) });
  o.w = spring(0, { stiffness: k, damping: d, onUpdate: () => paint(el, o.x, o.w) });
  return o;
};
let tail = null;
const lead = mk(bA, 420, 30);
tail = mk(bB, 190, 21);
let gooReady = false;

function moveGoo(instant) {
  const on = cats.querySelector(".cat.on");
  goo.style.setProperty("--cw", cats.scrollWidth + "px");
  if (!on) { goo.classList.remove("ready"); return; }
  const x = on.offsetLeft, w = on.offsetWidth, y = on.offsetTop;
  bA.style.top = bB.style.top = y + "px"; bridge.style.top = "0"; top = y; want = w;
  if (instant || !gooReady) { for (const b of [lead, tail]) { b.x.jump(x); b.w.jump(w); } gooReady = true; goo.classList.add("ready"); return; }
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
      const b = h("button", { class: "cat", type: "button", role: "tab", "data-id": id }, h("span", {}), h("em", {}));
      b.addEventListener("click", () => {
        st.cat = st.cat === id && id ? "" : id;
        update();
        b.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "nearest", inline: "nearest" });
      });
      cats.append(b);
      return b;
    });
  }
  items.forEach(([id, label, c], i) => {
    const b = chips[i], on = st.cat === id;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", String(on));
    b.disabled = !!(id && !c && !on);
    b.firstChild.textContent = label;
    b.lastChild.textContent = c.toLocaleString();
  });
  requestAnimationFrame(() => moveGoo(false));
}
addEventListener("resize", () => moveGoo(true));
if (document.fonts) document.fonts.ready.then(() => moveGoo(true));

// ---- search: an icon that pours out into a field ----
const sBox = $("search"), qIn = $("q");
qIn.value = st.q;
function openSearch(focus = true) {
  sBox.classList.add("open"); qIn.tabIndex = 0; $("sX").tabIndex = 0;
  $("sBtn").setAttribute("aria-expanded", "true");
  if (focus) setTimeout(() => qIn.focus({ preventScroll: true }), reducedMotion() ? 0 : 60);
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
qIn.addEventListener("keydown", (e) => { if (e.key === "Escape") { qIn.value = ""; markHas(); st.q = ""; update(); qIn.blur(); closeSearch(); } });
$("sX").addEventListener("click", () => { qIn.value = ""; markHas(); st.q = ""; update(); qIn.focus(); });
addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== qIn && !/input|textarea/i.test(document.activeElement?.tagName || "")) { e.preventDefault(); openSearch(); }
});

// ---- copyable switch ----
const sw = $("copyable");
const paintSw = () => sw.setAttribute("aria-checked", String(st.copyable));
paintSw();
sw.addEventListener("click", () => { st.copyable = !st.copyable; paintSw(); update(); });

// ---- sort: segmented control, pill on springs (it stretches a little as it moves) ----
const seg = $("sort"), ind = seg.querySelector(".seg-ind");
const segX = spring(0, { stiffness: 380, damping: 28, onUpdate: () => paintSeg() });
const segW = spring(0, { stiffness: 220, damping: 20, onUpdate: () => paintSeg() });
function paintSeg() { ind.style.transform = `translateX(${segX.value}px)`; ind.style.width = segW.value + "px"; }
function moveSeg(instant) {
  const b = seg.querySelector(`[data-v="${st.sort}"]`);
  for (const x of seg.querySelectorAll("button")) x.setAttribute("aria-checked", String(x === b));
  instant ? (segX.jump(b.offsetLeft), segW.jump(b.offsetWidth)) : (segX.set(b.offsetLeft), segW.set(b.offsetWidth));
}
seg.addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b || b.dataset.v === st.sort) return;
  st.sort = b.dataset.v; moveSeg(false); update();
});
seg.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  st.sort = st.sort === "popular" ? "recent" : "popular"; moveSeg(false); update();
  seg.querySelector(`[data-v="${st.sort}"]`).focus();
});
moveSeg(true);
if (document.fonts) document.fonts.ready.then(() => moveSeg(true));

// ---- cards ----
const io = "IntersectionObserver" in window ? new IntersectionObserver((es) => {
  for (const e of es) if (e.isIntersecting) { prefetchBoard(e.target.dataset.slug, { glb: (+e.target.dataset.bytes || 0) < 1.5e6 && nearBudget-- > 0 }); io.unobserve(e.target); }
}, { rootMargin: "300px 0px" }) : null;
let nearBudget = 8; // GLBs warmed just for being near the viewport; hover always warms

// hover lifts on a spring, press squishes (wider and shorter), release wobbles back
function springy(a) {
  let lift = 0, press = 0;
  const draw = () => {
    const p = sq.value, y = ly.value;
    a.style.transform = p || y ? `translateY(${(-6 * y).toFixed(2)}px) scale(${(1 + 0.03 * p).toFixed(4)}, ${(1 - 0.045 * p).toFixed(4)})` : "";
  };
  const ly = spring(0, { stiffness: 260, damping: 17, onUpdate: draw });
  const sq = spring(0, { stiffness: 520, damping: 16, onUpdate: draw });
  const go = () => { ly.set(lift); sq.set(press); };
  a.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") { lift = 1; go(); } });
  a.addEventListener("pointerleave", () => { lift = 0; press = 0; go(); });
  a.addEventListener("pointerdown", () => { press = 1; go(); });
  for (const t of ["pointerup", "pointercancel", "dragstart"]) a.addEventListener(t, () => { press = 0; go(); });
  a.addEventListener("focus", () => { lift = 1; go(); });
  a.addEventListener("blur", () => { lift = 0; go(); });
}

const mm = (v) => (Math.round(v * 10) / 10).toString();
function card(b, i) {
  const f = boardFiles(b.slug);
  const img = h("img", { src: f.thumb, alt: "", width: "640", height: "400", loading: "lazy", decoding: "async" });
  img.addEventListener("load", () => {
    const go = () => { const cv = keyBackdrop(img); if (cv) { cv.className = "in"; img.replaceWith(cv); } else img.classList.add("in"); };
    (window.requestIdleCallback || ((fn) => setTimeout(fn, 16)))(go, { timeout: 300 });
  }, { once: true });
  img.addEventListener("error", () => img.remove(), { once: true });
  const meta = [b.parts ? `${b.parts.toLocaleString()} parts` : "No parts", b.layers ? `${b.layers} layers` : "", b.size_mm ? `${mm(b.size_mm[0])} × ${mm(b.size_mm[1])} mm` : ""].filter(Boolean).join(" · ");
  const a = h("a", { class: "card", href: page("board") + carry({ id: b.id }), "data-slug": b.slug, "data-bytes": b.glb_bytes || 0, style: `--i:${Math.min(i, 12)}`, "aria-label": `${b.title} by ${b.owner}` },
    h("div", { class: "th" }, img),
    h("div", { class: "body" },
      h("div", { class: "t", title: b.title }, b.title),
      h("div", { class: "m" }, meta),
      h("div", { class: "cr" },
        h("span", { class: "or", title: `${b.owner}/${b.repo}` }, `${b.owner}/${b.repo}`),
        h("span", { class: "lic" + (b.copyable ? " ok" : ""), title: b.copyable ? "Licence allows copying and changes" : "View only" }, b.license || "No licence"),
        h("span", { class: "stars", title: `${(b.stars || 0).toLocaleString()} stars`, html: icon("star", 13) + esc(fmtStars(b.stars)) }))));
  const warm = () => prefetchBoard(b.slug);
  a.addEventListener("pointerenter", warm, { once: true });
  a.addEventListener("focus", warm, { once: true });
  a.addEventListener("touchstart", warm, { once: true, passive: true });
  springy(a);
  if (io) io.observe(a);
  return a;
}

function more() {
  const g = $("grid"), frag = document.createDocumentFragment(), start = st.shown;
  list.slice(start, start + PAGE).forEach((b, i) => frag.append(card(b, i)));
  g.append(frag);
  st.shown = Math.min(list.length, start + PAGE);
}

function update() {
  filter(); renderCats(); syncURL();
  $("grid").textContent = ""; st.shown = 0; more();
  const label = st.cat ? (CATS.find((c) => c.id === st.cat)?.label || st.cat) : "all categories";
  $("count").innerHTML = `<b>${list.length.toLocaleString()}</b> board${list.length === 1 ? "" : "s"} · ${esc(label)}`;
  const em = $("empty");
  em.hidden = list.length > 0;
  if (!list.length) em.innerHTML = `<b>Nothing here yet</b>${st.q ? `No boards match “${esc(st.q)}”. ` : ""}Try another search${st.copyable ? " or turn off “Copyable only”" : ""}.`;
}

new IntersectionObserver((es) => { if (es[0].isIntersecting && st.shown < list.length) more(); }, { rootMargin: "800px 0px" }).observe($("more"));

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

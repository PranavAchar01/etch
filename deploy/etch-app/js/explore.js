// Explore: the open-source board library (world/index.json). Category chips, search, licence filter, sort,
// a lazily filled grid, and prefetch: boards near the viewport get their small files warmed, a hovered board its GLB.
import { Q, $, h, esc, icon, LOGO, carry, page, fmtStars, prefetchBoard, boardFiles, WORLD, registerSW, keyBackdrop } from "./common.js";

registerSW();
$("logo").innerHTML = LOGO;
$("sIc").innerHTML = icon("search", 17);
$("homeL").href = page("index") + carry();
$("buildL").href = page("index") + carry();

const ORDER = ["keyboards", "iot", "computing", "dev-boards", "audio", "robotics", "tools", "rf", "retro", "lighting", "displays", "power", "wearables", "everything-else"];
const st = { q: Q.get("q") || "", cat: Q.get("cat") || "", sort: Q.get("sort") === "recent" ? "recent" : "popular", copyable: Q.get("copyable") === "1", shown: 0 };
let ALL = [], CATS = [], list = [];
const PAGE = 48;

$("q").value = st.q;
$("sort").value = st.sort;
$("copyable").checked = st.copyable;

function syncURL() {
  const p = new URLSearchParams(location.search);
  for (const [k, v] of [["q", st.q], ["cat", st.cat], ["sort", st.sort === "recent" ? "recent" : ""], ["copyable", st.copyable ? "1" : ""]]) v ? p.set(k, v) : p.delete(k);
  const s = p.toString();
  history.replaceState(null, "", location.pathname + (s ? "?" + s : ""));
}

function filter() {
  const words = st.q.toLowerCase().split(/\s+/).filter(Boolean);
  list = ALL.filter((b) => (!st.cat || b.category === st.cat) && (!st.copyable || b.copyable) && words.every((w) => b._s.includes(w)));
  // boards with no parts (bare plates, panels) go last either way
  const empty = (b) => (b.parts ? 0 : 1);
  if (st.sort === "popular") list.sort((a, b) => empty(a) - empty(b) || (b.stars || 0) - (a.stars || 0) || a.title.localeCompare(b.title));
  else list.sort((a, b) => empty(a) - empty(b) || String(b.updated || b.pushed || b.rendered || "").localeCompare(String(a.updated || a.pushed || a.rendered || "")) || (b._i - a._i));
}

function renderCats() {
  // counts follow the search + licence filter, so a chip never promises boards that aren't there
  const words = st.q.toLowerCase().split(/\s+/).filter(Boolean);
  const n = {}; let total = 0;
  for (const b of ALL) if ((!st.copyable || b.copyable) && words.every((w) => b._s.includes(w))) { n[b.category] = (n[b.category] || 0) + 1; total++; }
  const el = $("cats"); el.textContent = "";
  const chip = (id, label, c) => h("button", { class: "cat" + (st.cat === id ? " on" : ""), role: "tab", "aria-selected": String(st.cat === id), disabled: !!(id && !c && st.cat !== id),
    onclick: () => { st.cat = st.cat === id ? "" : id; update(); } }, label, h("em", {}, c.toLocaleString()));
  el.append(chip("", "All", total));
  for (const c of CATS) el.append(chip(c.id, c.label, n[c.id] || 0));
}

const io = "IntersectionObserver" in window ? new IntersectionObserver((es) => {
  for (const e of es) if (e.isIntersecting) { prefetchBoard(e.target.dataset.slug, { glb: (+e.target.dataset.bytes || 0) < 1.5e6 && nearBudget-- > 0 }); io.unobserve(e.target); }
}, { rootMargin: "300px 0px" }) : null;
let nearBudget = 8; // GLBs warmed just for being near the viewport; hover always warms

function card(b) {
  const f = boardFiles(b.slug);
  const img = h("img", { src: f.thumb, alt: "", width: "640", height: "400", loading: "lazy", decoding: "async" });
  img.addEventListener("load", () => {
    const go = () => { const cv = keyBackdrop(img); if (cv) { cv.className = "in"; img.replaceWith(cv); } else img.classList.add("in"); };
    (window.requestIdleCallback || ((f) => setTimeout(f, 16)))(go, { timeout: 300 });
  }, { once: true });
  img.addEventListener("error", () => img.remove(), { once: true });
  const a = h("a", { class: "bcard", href: page("board") + carry({ id: b.id }), "data-slug": b.slug, "data-bytes": b.glb_bytes || 0 },
    h("div", { class: "th" }, img),
    h("div", { class: "t", title: b.title }, b.title),
    h("div", { class: "s" },
      h("span", { class: "or" }, `${b.owner}/${b.repo}`),
      h("span", { class: "lic" + (b.copyable ? " ok" : ""), title: b.copyable ? "Licence allows copying and changes" : "View only" }, b.license || "—"),
      h("span", { class: "stars", html: icon("star", 14) + esc(fmtStars(b.stars)) })));
  const warm = () => prefetchBoard(b.slug);
  a.addEventListener("pointerenter", warm, { once: true });
  a.addEventListener("focus", warm, { once: true });
  a.addEventListener("touchstart", warm, { once: true, passive: true });
  if (io) io.observe(a);
  return a;
}

function more() {
  const g = $("grid"), frag = document.createDocumentFragment();
  for (const b of list.slice(st.shown, st.shown + PAGE)) frag.append(card(b));
  g.append(frag);
  st.shown = Math.min(list.length, st.shown + PAGE);
}

function update() {
  filter(); renderCats(); syncURL();
  $("grid").textContent = ""; st.shown = 0; more();
  const label = st.cat ? (CATS.find((c) => c.id === st.cat)?.label || st.cat) : "all categories";
  $("count").textContent = `${list.length.toLocaleString()} board${list.length === 1 ? "" : "s"} · ${label}`;
  const em = $("empty");
  em.hidden = list.length > 0;
  if (!list.length) em.innerHTML = `<b>No boards match</b>${st.q ? `Nothing for “${esc(st.q)}”.` : ""} Try another search or turn off “Copyable only”.`;
}

new IntersectionObserver((es) => { if (es[0].isIntersecting && st.shown < list.length) more(); }, { rootMargin: "800px 0px" }).observe($("more"));
let qt = 0;
$("q").addEventListener("input", () => { clearTimeout(qt); qt = setTimeout(() => { st.q = $("q").value.trim(); update(); }, 90); });
$("sort").addEventListener("change", () => { st.sort = $("sort").value; update(); });
$("copyable").addEventListener("change", () => { st.copyable = $("copyable").checked; update(); });
addEventListener("keydown", (e) => { if (e.key === "/" && document.activeElement !== $("q")) { e.preventDefault(); $("q").focus(); } });

try {
  const idx = await (window.__index || fetch(WORLD + "index.json").then((r) => r.json()));
  ALL = (idx.boards || []).map((b, i) => ({ ...b, _i: i, _s: `${b.title} ${b.owner} ${b.repo} ${b.description || ""} ${(b.topics || []).join(" ")}`.toLowerCase() }));
  const byId = new Map((idx.categories || []).map((c) => [c.id, c]));
  CATS = ORDER.map((id) => byId.get(id) || { id, label: id.replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase()) });
  update();
} catch (e) {
  $("count").textContent = "";
  const em = $("empty"); em.hidden = false;
  em.innerHTML = `<b>The library isn't here yet</b>No <code>${esc(WORLD)}index.json</code> on this server. Try <a href="${page("explore")}?world=fixtures">the fixture library</a>.`;
}

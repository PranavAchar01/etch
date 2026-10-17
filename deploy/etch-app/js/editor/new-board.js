// The start screen for a new board (board.html?new=1, ?new=1&from=library opens on the second choice). Over the
// workplane, one panel with two choices side by side (stacked on phones), a ring that stretches liquidly from one to
// the other, and the chosen choice's content revealed below on a spring:
//   1. Start from scratch: a big prompt composer with Etch's eyes sitting on it and a mic, then "Popular starts"
//      (example prompts as small white cards that fill the composer) -> onSubmit(text)
//   2. Start from an existing board: "Recently opened" (or "Popular boards"), then search + category chips + a
//      compact grid of Explore's white product cards, copyable boards only -> onPick(board row from index.json)
// Either way the panel then melts down into the agent dock's pill (a goo layer: the panel blob shrinks onto the pill
// while droplets bud off and drop into it) and is gone.
//
//   const hero = createNewBoardHero(stageEl, { onSubmit, onMic, onPick, boardHref(b), target: () => DOMRect|null,
//     initial: "scratch" | "library", recent: ["gh:…", …] });
//   hero.collapse() -> Promise   (melt into the dock without sending, e.g. when the user talks to the dock instead)
//   hero.open                    (still showing)
import { createEyes } from "../ui/eyes.js";
import { gooLayer, spawnBlob, squish, wobble } from "../ui/goo.js";
import { spring, reducedMotion } from "../ui/spring.js";
import { WORLD, boardFiles, cachedFetch, keyBackdrop, prefetchBoard, icon, fmtStars } from "../common.js";

// example prompts, each with a tiny glyph of the kind of board it makes
export const EXAMPLES = [
  ["led", "A board that blinks one red LED from a coin cell"],
  ["leaf", "USB-C powered plant moisture sensor with an OLED"],
  ["cloud", "ESP32 weather station with a BME280"],
  ["keys", "A 4-key USB macro pad"],
];
const GLYPH = {
  led: '<rect x="4" y="6" width="24" height="20" rx="4"/><circle cx="12" cy="16" r="3.2" class="hot"/><path d="M18 16h6M20 12h4M20 20h4"/>',
  leaf: '<rect x="4" y="6" width="24" height="20" rx="4"/><rect x="8" y="10" width="9" height="6" rx="1" class="hot"/><path d="M21 20c0-4 2-6 4-6 0 4-2 6-4 6zM21 20v3M8 20h8"/>',
  cloud: '<rect x="4" y="6" width="24" height="20" rx="4"/><rect x="8" y="10" width="7" height="7" rx="1.2" class="hot"/><path d="M19 20h5a2.4 2.4 0 0 0-1-4.6 3 3 0 0 0-5.7 1A1.8 1.8 0 0 0 19 20zM8 21h6"/>',
  keys: '<rect x="4" y="6" width="24" height="20" rx="4"/><rect x="8" y="10" width="6" height="5" rx="1.2"/><rect x="18" y="10" width="6" height="5" rx="1.2"/><rect x="8" y="17.5" width="6" height="5" rx="1.2" class="hot"/><rect x="18" y="17.5" width="6" height="5" rx="1.2"/>',
};
const glyph = (k) => `<svg class="nb-gl" viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${GLYPH[k] || GLYPH.led}</svg>`;

const ICON = {
  mic: '<rect x="6.5" y="2.5" width="5" height="8.5" rx="2.5"/><path d="M4 8.5a5 5 0 0 0 10 0M9 13.5v2.2"/>',
  send: '<path d="M9 14V4.5M4.8 8.6 9 4.4l4.2 4.2"/>',
  search: '<circle cx="8" cy="8" r="4.8"/><path d="m11.6 11.6 3.4 3.4"/>',
};
const svg = (n, s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[n]}</svg>`;
function el(tag, cls, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  for (const [k, v] of Object.entries(attrs)) if (v != null && v !== false) e.setAttribute(k, v === true ? "" : v);
  e.append(...kids.filter((k) => k != null));
  return e;
}
const STAR4 = '<path d="M12 2.5c.5 4.6 2.4 6.9 7 7.5-4.6.6-6.5 2.9-7 7.5-.5-4.6-2.4-6.9-7-7.5 4.6-.6 6.5-2.9 7-7.5z"/>';
const SPARKLE = `<svg class="nb-spark" viewBox="0 0 24 24" aria-hidden="true">${STAR4}</svg><svg class="nb-spark s2" viewBox="0 0 24 24" aria-hidden="true">${STAR4}</svg>`;
// a little stack of white cards, each with a board on it
const STACK = '<span class="nb-stack" aria-hidden="true">' + [0, 1, 2].map((i) => `<i class="c c${i}"><b></b><s></s><s class="w"></s></i>`).join("") + "</span>";

export function createNewBoardHero(host, { onSubmit, onMic, onPick, boardHref = () => "#", target = () => null, initial = "scratch", recent = [], mine = [], mineHref = () => "#", max = 400 } = {}) {
  let gone = false, done = null, typeT = 0, mode = initial === "library" ? "library" : "scratch";
  const root = el("div", "nb", { role: "dialog", "aria-modal": "false", "aria-labelledby": "nbT" });
  const veil = el("div", "nb-veil");
  const card = el("div", "nb-card");
  const title = el("h2", "nb-t", { id: "nbT" }, "Start a new board");
  const sub = el("p", "nb-d", {}, "Describe it to Etch, or begin from an open-source board and change it.");

  // ---- the two choices ----
  const choice = (value, head, text, art) => {
    const b = el("button", "nb-ch", { type: "button", role: "tab", "data-value": value, id: "nbTab-" + value, "aria-controls": "nbPane-" + value });
    const a = el("span", "nb-art"); a.innerHTML = art;
    b.append(a, el("span", "nb-ch-tx", {}, el("span", "nb-ch-t", {}, head), el("span", "nb-ch-d", {}, text)));
    return b;
  };
  const chScratch = choice("scratch", "Start from scratch", "Tell Etch what the board should do. It picks the parts, routes the board and checks it.", '<span class="nb-mini-eyes"></span>' + SPARKLE);
  const chLib = choice("library", "Start from an existing board", "Pick an open-source board from the library and have Etch change it.", STACK);
  const ring = el("div", "nb-ring", { "aria-hidden": "true" });
  const choices = el("div", "nb-chs", { role: "tablist", "aria-label": "How to start" }, ring, chScratch, chLib);

  // ---- pane 1: scratch ----
  const eyesHost = el("div", "nb-eyes");
  const input = el("textarea", "nb-in", { rows: "2", maxlength: String(max), placeholder: "What should your board do?", "aria-label": "Describe your board", autocomplete: "off", spellcheck: "true", enterkeyhint: "send" });
  const mic = el("button", "nb-mic lq-btn", { type: "button", "aria-label": "Describe it out loud", title: "Talk to Etch" });
  mic.innerHTML = svg("mic");
  const send = el("button", "nb-send lq-btn", { type: "submit", "aria-label": "Build this board", title: "Build it (Enter)" });
  send.innerHTML = svg("send");
  const form = el("form", "nb-comp", {}, eyesHost, input, el("div", "nb-acts", {}, mic, send));
  const starts = el("div", "nb-strip nb-starts", { role: "list" });
  for (const [k, x] of EXAMPLES) {
    const c = el("button", "nb-pc lq-btn", { type: "button", role: "listitem", "aria-label": `Use this example: ${x}` });
    c.innerHTML = glyph(k);
    c.append(el("span", "", {}, x));
    c.addEventListener("click", () => {
      input.value = x; sync(); input.focus();
      input.setSelectionRange(x.length, x.length);
      eyes.react("nod"); wobble(form, 0.35);
    });
    starts.append(c);
  }
  const paneS = el("div", "nb-pane", { id: "nbPane-scratch", role: "tabpanel", "aria-labelledby": "nbTab-scratch" }, form, el("h3", "nb-sl", {}, "Popular starts"), starts);

  // ---- pane 2: library ----
  // "My boards" (saved in this browser) first, when there are any
  const mineL = el("h3", "nb-sl", { hidden: !mine.length }, "My boards");
  const mineS = el("div", "nb-strip nb-recent nb-mine", { role: "list", hidden: !mine.length });
  mine.slice(0, 4).forEach((b) => {
    const img = b.thumb ? el("img", "", { src: b.thumb, alt: "", loading: "eager", decoding: "async" }) : null;
    if (img) {
      img.addEventListener("load", () => { const cv = /^gh:/.test(b.id) ? keyBackdrop(img) : null; if (cv) { cv.className = "in"; img.replaceWith(cv); } else img.classList.add("in"); }, { once: true });
      img.addEventListener("error", () => img.remove(), { once: true });
    }
    const a = el("a", "nb-bc sm", { href: mineHref(b), role: "listitem", "aria-label": `${b.title}, one of your boards` },
      el("div", "nb-th", {}, img),
      el("div", "nb-info", {}, el("div", "nb-l1", {}, el("span", "nb-yours", {}, "Yours"), el("span", "who", {}, b.owner || "")), el("div", "nb-l2", {}, el("span", "t", {}, b.title))));
    if (/^gh:/.test(b.id)) a.addEventListener("click", (e) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button > 0 || gone) return;
      e.preventDefault(); a.classList.add("picked");
      collapse().then(() => onPick && onPick({ id: b.id, title: b.title, slug: b.id.replace(/^gh:/, "").toLowerCase().replace(/\//g, "__").replace(/[^a-z0-9_.-]/g, "-") }));
    });
    squish(a, { amount: 0.4 });
    mineS.append(a);
  });
  const recentL = el("h3", "nb-sl", {}, recent.length ? "Recently opened" : "Popular boards");
  const recentS = el("div", "nb-strip nb-recent", { role: "list" });
  const q = el("input", "nb-q", { type: "search", placeholder: "Search boards: title, owner, repo…", "aria-label": "Search boards", autocomplete: "off", spellcheck: "false" });
  const qWrap = el("label", "nb-qw", {}, el("span", "nb-qi"), q);
  qWrap.firstChild.innerHTML = svg("search", 17);
  const cats = el("div", "nb-cats", { role: "group", "aria-label": "Category" });
  const grid = el("div", "nb-grid", { role: "list" });
  const note = el("div", "nb-note", { role: "status" }, "Loading the library…");
  const moreB = el("button", "nb-more lq-btn", { type: "button", hidden: true }, "Show more boards");
  const paneL = el("div", "nb-pane", { id: "nbPane-library", role: "tabpanel", "aria-labelledby": "nbTab-library" },
    mineL, mineS, recentL, recentS, el("div", "nb-lhead", {}, el("h3", "nb-sl", {}, "Every board you can copy"), qWrap), cats, note, grid, moreB);

  const panes = el("div", "nb-panes", {}, paneS, paneL);
  card.append(title, sub, choices, panes);
  const goo = el("div", "nb-goo", { "aria-hidden": "true" });
  gooLayer(goo, { id: "nb-goo", blur: 10, alpha: 22, shift: -10, after: "drop-shadow(0 18px 40px rgba(22,28,45,.16)) drop-shadow(0 1px 2px rgba(22,28,45,.12))" });
  const blob = el("div", "nb-blob");
  goo.append(blob);
  const sc = el("div", "nb-sc", {}, card);
  root.append(veil, goo, sc);
  host.append(root);
  const eyes = createEyes(eyesHost, { label: "Etch" });
  const miniEyes = createEyes(chScratch.querySelector(".nb-mini-eyes"), { label: "" });
  chScratch.querySelector(".nb-mini-eyes svg")?.setAttribute("aria-hidden", "true");
  for (const b of [mic, send, moreB, ...starts.children]) squish(b, { amount: b.classList.contains("nb-pc") ? 0.4 : 1 });

  // the panel is a goo blob behind the content, so it can melt into the dock later
  const place = () => {
    if (gone) return;
    const r = card.getBoundingClientRect();
    Object.assign(blob.style, { left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px" });
    measureRing();
  };
  const ro = new ResizeObserver(() => place()); ro.observe(card); ro.observe(choices); ro.observe(document.documentElement);
  addEventListener("resize", place, { passive: true });
  sc.addEventListener("scroll", place, { passive: true });

  // ---- the ring: each edge on its own spring, the leading one quick and the trailing one lazy, so it stretches ----
  const fast = { stiffness: 520, damping: 34 }, slow = { stiffness: 190, damping: 21 };
  const edge = () => spring(0, { precision: 0.05, onUpdate: drawRing });
  const eL = edge(), eR = edge(), eT = edge(), eB = edge();
  let geo = null;
  function drawRing() {
    const l = eL.value, r = Math.max(eR.value, l + 20), t = eT.value, b = Math.max(eB.value, t + 20);
    ring.style.transform = `translate(${l}px, ${t}px)`;
    ring.style.width = r - l + "px"; ring.style.height = b - t + "px";
  }
  function measureRing() {
    const t = mode === "library" ? chLib : chScratch;
    const g = { l: t.offsetLeft, r: t.offsetLeft + t.offsetWidth, t: t.offsetTop, b: t.offsetTop + t.offsetHeight };
    if (!g.r) return;
    if (!geo) { geo = g; eL.jump(g.l); eR.jump(g.r); eT.jump(g.t); eB.jump(g.b); ring.classList.add("in"); return; }
    if (g.l === geo.l && g.r === geo.r && g.t === geo.t && g.b === geo.b) return;
    const right = g.l > geo.l, down = g.t > geo.t, moved = g.l !== geo.l || g.t !== geo.t;
    geo = g;
    if (!moved) { eL.jump(g.l); eR.jump(g.r); eT.jump(g.t); eB.jump(g.b); return; }
    eL.set(g.l, right ? slow : fast); eR.set(g.r, right ? fast : slow);
    eT.set(g.t, down ? slow : fast); eB.set(g.b, down ? fast : slow);
  }

  // ---- switching: the panes' box springs to the new pane's height while the content crossfades ----
  const hS = spring(0, { preset: { stiffness: 240, damping: 26 }, precision: 0.5, onUpdate: (v) => { panes.style.height = v + "px"; }, onRest: () => { panes.style.height = ""; } });
  function show(m, { focus = false, instant = false } = {}) {
    const from = panes.offsetHeight;
    mode = m;
    for (const [b, on] of [[chScratch, m === "scratch"], [chLib, m === "library"]]) {
      b.classList.toggle("on", on); b.setAttribute("aria-selected", on ? "true" : "false"); b.tabIndex = on ? 0 : -1;
    }
    paneS.hidden = m !== "scratch"; paneL.hidden = m !== "library";
    for (const p of [paneS, paneL]) if (!p.hidden) { p.classList.remove("in"); void p.offsetWidth; p.classList.add("in"); }
    measureRing();
    if (m === "library") loadLibrary();
    if (!instant && from && !reducedMotion()) {
      panes.style.height = "";
      const to = panes.offsetHeight;
      hS.jump(from); hS.set(to);
    }
    if (focus) (m === "scratch" ? input : q).focus({ preventScroll: true });
    place();
  }
  choices.addEventListener("click", (e) => { const b = e.target.closest("[data-value]"); if (b) { show(b.dataset.value); wobble(b, 0.25); } });
  choices.addEventListener("keydown", (e) => {
    const k = e.key;
    if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"].includes(k)) return;
    e.preventDefault();
    const m = k === "Home" ? "scratch" : k === "End" ? "library" : mode === "scratch" ? "library" : "scratch";
    show(m); (m === "scratch" ? chScratch : chLib).focus();
  });

  // ---- scratch: the composer ----
  const sync = () => {
    send.disabled = !input.value.trim();
    input.style.height = "auto";
    input.style.height = Math.min(160, input.scrollHeight) + "px";
  };
  input.addEventListener("input", () => { sync(); eyes.setState(input.value.trim() ? "userTalking" : "idle"); clearTimeout(typeT); typeT = setTimeout(() => eyes.setState("idle"), 900); });
  input.addEventListener("focus", () => { const r = input.getBoundingClientRect(); eyes.lookAt(r.left + 80, r.top + r.height / 2); });
  input.addEventListener("blur", () => eyes.lookAt(null));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const t = input.value.replace(/\s+/g, " ").trim();
    if (gone) return;
    if (!t) { eyes.react("shake"); input.focus(); return; }
    eyes.setState("heard");
    collapse().then(() => onSubmit && onSubmit(t));
  });
  mic.addEventListener("click", () => {
    if (input.value.trim()) { form.requestSubmit(); return; }
    collapse().then(() => onMic && onMic());
  });
  sync();

  // ---- library: index.json, copyable boards only ----
  let ALL = null, CATS = [], cat = "", shown = 0, list = [];
  const PAGE = 12;
  function loadLibrary() {
    if (ALL) return;
    ALL = [];
    const idx = window.__index || cachedFetch(WORLD + "index.json").then((r) => { if (!r.ok) throw new Error("index " + r.status); return r.json(); });
    Promise.resolve(idx).then((j) => {
      ALL = (j.boards || []).filter((b) => b.copyable).map((b) => ({ ...b, _s: `${b.title} ${b.owner} ${b.repo} ${b.category || ""}`.toLowerCase() }));
      ALL.sort((a, b) => (b.stars || 0) - (a.stars || 0));
      CATS = (j.categories || []).filter((c) => ALL.some((b) => b.category === c.id));
      const byId = new Map(ALL.map((b) => [b.id, b]));
      const rec = recent.map((id) => byId.get(id)).filter(Boolean).slice(0, 4);
      recentL.textContent = rec.length ? "Recently opened" : "Popular boards";
      (rec.length ? rec : ALL.slice(0, 4)).forEach((b, i) => recentS.append(boardCard(b, i, true)));
      renderCats(); update();
    }).catch(() => { note.textContent = "The library isn't here right now."; note.hidden = false; recentL.hidden = recentS.hidden = true; });
  }
  const catLabel = (id) => CATS.find((c) => c.id === id)?.label || (id ? id.replace(/-/g, " ").replace(/^./, (x) => x.toUpperCase()) : "");
  function renderCats() {
    cats.textContent = "";
    for (const [id, label] of [["", "All"], ...CATS.map((c) => [c.id, c.label])]) {
      const n = id ? ALL.filter((b) => b.category === id).length : ALL.length;
      const b = el("button", "nb-cat lq-btn" + (id === cat ? " on" : ""), { type: "button", "aria-pressed": id === cat ? "true" : "false" }, label, el("em", "", {}, String(n)));
      b.addEventListener("click", () => { cat = id; for (const x of cats.children) { const on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-pressed", on ? "true" : "false"); } update(); });
      squish(b);
      cats.append(b);
    }
  }
  let qT = 0;
  q.addEventListener("input", () => { clearTimeout(qT); qT = setTimeout(update, 90); });
  function update() {
    const w = q.value.toLowerCase().split(/\s+/).filter(Boolean);
    list = ALL.filter((b) => (!cat || b.category === cat) && w.every((x) => b._s.includes(x)));
    grid.textContent = ""; shown = 0;
    note.hidden = !!list.length;
    note.textContent = list.length ? "" : "No copyable boards match. Try another word.";
    more();
  }
  function more() {
    const frag = document.createDocumentFragment();
    list.slice(shown, shown + PAGE).forEach((b, i) => frag.append(boardCard(b, i)));
    shown = Math.min(list.length, shown + PAGE);
    grid.append(frag);
    moreB.hidden = shown >= list.length;
    moreB.textContent = `Show more boards (${list.length - shown})`;
  }
  moreB.addEventListener("click", more);
  const mm = (v) => (Math.round(v * 10) / 10).toString();
  // Explore's white product card, compact: the board on white, "owner · category · licence", title + stars
  function boardCard(b, i, small = false) {
    const f = boardFiles(b.slug);
    const img = el("img", "", { src: f.thumb, alt: "", width: "640", height: "400", loading: small ? "eager" : "lazy", decoding: "async" });
    img.addEventListener("load", () => {
      const cv = keyBackdrop(img);
      if (cv) { cv.className = "in"; img.replaceWith(cv); } else img.classList.add("in");
    }, { once: true });
    img.addEventListener("error", () => img.remove(), { once: true });
    const lic = b.license || "No licence";
    const specs = [b.parts ? `${b.parts.toLocaleString()} parts` : "", b.size_mm ? `${mm(b.size_mm[0])} × ${mm(b.size_mm[1])} mm` : ""].filter(Boolean).join(" · ");
    const stars = el("span", "nb-stars", { "aria-label": `${(b.stars || 0).toLocaleString()} stars` });
    stars.innerHTML = icon("star", 13) + fmtStars(b.stars);
    const a = el("a", "nb-bc" + (small ? " sm" : ""), { href: boardHref(b), role: "listitem", style: `--i:${Math.min(i, 12)}`, title: `${b.title}\n${b.owner}/${b.repo}${specs ? "\n" + specs : ""}\nLicence: ${lic}`, "aria-label": `${b.title} by ${b.owner}, start from this board` },
      el("div", "nb-th", {}, img),
      el("div", "nb-info", {},
        el("div", "nb-l1", {}, el("span", "who", {}, `${b.owner} · ${catLabel(b.category)}`), el("span", "lic", {}, ` · ${lic}`)),
        el("div", "nb-l2", {}, el("span", "t", {}, b.title), stars)));
    a.addEventListener("pointerenter", () => prefetchBoard(b.slug), { once: true });
    a.addEventListener("focus", () => prefetchBoard(b.slug), { once: true });
    a.addEventListener("click", (e) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button > 0 || gone) return; // a new tab still works as a plain link
      e.preventDefault();
      prefetchBoard(b.slug);
      a.classList.add("picked");
      collapse().then(() => onPick && onPick(b));
    });
    squish(a, { amount: 0.4 });
    return a;
  }

  // first paint
  show(mode, { instant: true });
  requestAnimationFrame(place);
  if (!matchMedia("(pointer: coarse)").matches) setTimeout(() => (mode === "scratch" ? input : q).focus({ preventScroll: true }), 60);

  // ---- melt into the dock ----
  function collapse() {
    if (done) return done;
    gone = true;
    root.classList.add("out");
    const to = target();
    const r = card.getBoundingClientRect();
    if (reducedMotion() || !to || !blob.animate) {
      done = new Promise((res) => setTimeout(() => { teardown(); res(); }, reducedMotion() ? 200 : 0));
      return done;
    }
    const cx = to.left + to.width / 2, cy = to.top + to.height / 2;
    // a tall, scrolled panel starts from the part that is on screen
    const top = Math.max(r.top, 0), bottom = Math.min(r.bottom, innerHeight), hh = Math.max(40, bottom - top);
    // the panel shrinks onto the pill with a little overshoot; its corners round off into the pill's
    const a = blob.animate([
      { left: r.left + "px", top: top + "px", width: r.width + "px", height: hh + "px", borderRadius: "28px" },
      { left: (r.left + to.left) / 2 + "px", top: top + (to.top - top) * 0.62 + "px", width: Math.max(to.width, r.width * 0.56) + "px", height: Math.max(to.height, hh * 0.42) + "px", borderRadius: "40px", offset: 0.45 },
      { left: to.left + "px", top: to.top + "px", width: to.width + "px", height: to.height + "px", borderRadius: to.height / 2 + "px" },
    ], { duration: 620, easing: "cubic-bezier(.55,0,.25,1.08)", fill: "forwards" });
    // droplets bud off the panel's bottom edge and fall into the pill after it
    [[-0.22, 26, 60], [0.18, 20, 130], [0.02, 16, 200]].forEach(([dx, s, delay]) => {
      setTimeout(() => {
        const from = { x: r.left + r.width * (0.5 + dx), y: bottom - s };
        const b = spawnBlob(goo, { from, to: { x: from.x + (cx - from.x) * 0.5, y: from.y + (cy - from.y) * 0.55 }, w: s, h: s });
        setTimeout(() => b.absorb({ x: cx + dx * 40, y: cy }), 170);
      }, delay);
    });
    done = a.finished.catch(() => {}).then(() => new Promise((res) => setTimeout(res, 160))).then(() => { teardown(); });
    return done;
  }
  function teardown() {
    ro.disconnect(); removeEventListener("resize", place);
    clearTimeout(typeT);
    for (const e of [eyes, miniEyes]) { try { e.destroy(); } catch (er) { /* already gone */ } }
    root.remove();
  }

  return {
    el: root,
    get open() { return !gone; },
    get mode() { return mode; },
    get value() { return input.value; },
    show: (m) => show(m, { focus: true }),
    focus() { (mode === "scratch" ? input : q).focus(); },
    collapse,
  };
}

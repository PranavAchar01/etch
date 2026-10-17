// Board editor: one board on a light-blue mm workplane with Etch's agent at the bottom. Toolbar (edit tools, view
// tools, Import / Export / Share), view cube + camera buttons, selection with outlines / handles / guides, an
// inspector card, hide / isolate, snap grid + board colours, a right panel with Etch's KiCad parts (drag one onto the
// board to ask Etch to add it) and this board's parts. Works for imported boards (gh:, static files under world/)
// and Etch builds (live-*, through the backend); ?mock=1 runs everything without a backend.
import { Q, $, h, esc, LOGO, carry, page, toast, slugOf, isLive, boardFiles, cachedFetch, registerSW, keyBackdrop } from "../common.js";
import { createBoardViewer, MASKS, SILKS, FINISHES } from "../board-viewer.js";
import * as etch from "../etch-api.js";
import { ic } from "./icons.js";
import { loadDock } from "./dock-loader.js";
import { createViewCube, createCameraButtons } from "./viewcube.js";
import { createPartsPanel } from "./parts-panel.js";
import { createAgentBridge } from "./agent-bridge.js";
import { createSelectionOverlay } from "./selection.js";
import { createInspector } from "./inspector.js";
import { createBoardList } from "./board-list.js";
import { createDrop } from "./drop.js";
import { bindLiquid, liquidTabs, wobble } from "../ui/goo.js";

registerSW();
// the API client, with `authed` for the backend call (named `backend` since the access code went away)
const api = { ...etch, authed: etch.backend || etch.authed, get status() { return etch.status; } };
const ID = Q.get("id") || "";
const MOBILE = matchMedia("(max-width: 768px)");
const board = { id: ID, kind: isLive(ID) ? "live" : "gh", live: isLive(ID), meta: {}, parts: {}, files: null };

// ---------- poster first: the prerendered still, keyed onto the stage, before anything else ----------
const boot = window.__boot || {};
function showPoster(src) {
  const cv = $("posterC"); if (!cv || !src) return;
  const im = new Image(); im.decoding = "async";
  im.onload = () => {
    const keyed = keyBackdrop(im);
    cv.width = im.naturalWidth; cv.height = im.naturalHeight;
    cv.getContext("2d").drawImage(keyed || im, 0, 0);
    cv.classList.add("in");
  };
  im.src = src;
}
if (boot.thumb) showPoster(boot.thumb);
else if (ID && !board.live) showPoster(boardFiles(slugOf(ID)).thumb);
function hidePoster() {
  const p = $("poster"); if (!p) return;
  p.classList.add("gone");
  setTimeout(() => p.remove(), 700);
}

// ---------- static chrome ----------
$("logo").innerHTML = LOGO;
$("homeL").href = page("index") + carry();
$("exL").href = page("explore") + carry();
for (const [id, name] of [["copyB", "copy"], ["pasteB", "paste"], ["dupB", "dup"], ["delB", "del"], ["undoB", "undo"], ["redoB", "redo"], ["showAllB", "showAll"], ["panelT", "panel"], ["rpChev", "chev"]]) $(id).innerHTML = ic(name);
for (const b of document.querySelectorAll(".top .btn, .tools .ib, .seg button, .pill-btn")) b.classList.add("lq-btn");

const viewer = createBoardViewer($("cw"));
const overlay = createSelectionOverlay($("selOv"), viewer);
let sel = [];

// ---------- undo / redo of the edits this page makes itself (visibility, isolate, locks, colours) ----------
const undo = [], redo = [];
function record(label, doIt, undoIt) { doIt(); undo.push({ label, doIt, undoIt }); redo.length = 0; syncTools(); }
function stepHistory(from, to, back) {
  const e = from.pop(); if (!e) return;
  (back ? e.undoIt : e.doIt)(); to.push(e); syncTools();
  toast(`${back ? "Undid" : "Redid"}: ${e.label}`);
}
function syncTools() {
  $("undoB").disabled = !undo.length; $("redoB").disabled = !redo.length;
  const none = !sel.length;
  for (const id of ["copyB", "dupB", "delB"]) $(id).disabled = none;
  $("pasteB").disabled = !clip.length;
  $("showAllB").disabled = !viewer.hidden.size && !viewer.isolated;
}
$("undoB").onclick = () => stepHistory(undo, redo, true);
$("redoB").onclick = () => stepHistory(redo, undo, false);

// ---------- selection ----------
const locked = new Set();
function setSel(refs, { focus = false } = {}) {
  sel = [...new Set(refs || [])].filter((r) => board.parts[r] || viewer.parts[r]);
  viewer.select(sel);
  overlay.set(sel);
  inspector.set(sel);
  list?.sync();
  syncTools();
  if (focus && sel.length === 1) viewer.focus(sel[0]);
  if (sel.length === 1) list?.reveal(sel[0]);
}
function pick(ref, { add = false, focus = false } = {}) {
  if (!ref) { if (!add) setSel([]); return; }
  if (add) setSel(sel.includes(ref) ? sel.filter((r) => r !== ref) : [...sel, ref]);
  else setSel([ref], { focus });
}
viewer.on("pick", ({ ref, shift }) => pick(ref, { add: shift }));

const actions = {
  isLocked: (r) => locked.has(r),
  lock(refs, on) { record(`${on ? "lock" : "unlock"} ${refs.join(", ")}`, () => refs.forEach((r) => (on ? locked.add(r) : locked.delete(r))), () => refs.forEach((r) => (on ? locked.delete(r) : locked.add(r)))); inspector.render(); },
  hide(refs, on) {
    const before = refs.map((r) => viewer.hidden.has(r));
    record(`${on ? "hide" : "show"} ${refs.join(", ")}`, () => refs.forEach((r) => viewer.setHidden(r, on)), () => refs.forEach((r, i) => viewer.setHidden(r, before[i])));
    list?.sync(); inspector.render(); syncTools();
  },
  isolate(refs) {
    const before = viewer.isolated;
    record(refs ? `isolate ${refs.join(", ")}` : "show all", () => viewer.isolate(refs), () => viewer.isolate(before));
    inspector.render(); syncTools();
    if (refs) viewer.fitView(refs);
  },
  clear: () => setSel([]),
  select: (refs) => setSel(refs, { focus: true }),
  pick,
  changeBlock,
  ask(refs) {
    dock?.expand(true);
    if (refs.length === 1) bridge?.ask(`What does ${refs[0]} do on this board?`, { ref: refs[0] });
    else bridge?.ask(`What do ${refs.join(", ")} do together on this board?`, { ref: refs[0] });
  },
  async change(refs) {
    if (!dock || !bridge) return;
    dock.expand(true);
    const r = refs[0];
    const what = refs.length === 1 ? `${r}${board.parts[r]?.value ? ` (${board.parts[r].value})` : ""}` : refs.join(", ");
    const ans = await dock.ask({ id: "chg-" + Date.now(), question: `What should Etch change about ${what}?`, options: ["Swap it for an equivalent part", "Move it closer to the board edge", "Remove it"], multi: false });
    const pickd = Array.isArray(ans) ? ans[0] : ans && typeof ans === "object" ? ans.label || ans.id : ans;
    if (!pickd) return;
    const text = /^remove/i.test(pickd) ? `remove ${refs.join(", ")}` : /^move/i.test(pickd) ? `move ${refs.join(", ")} closer to the board edge` : `swap ${refs.join(", ")} for an equivalent part`;
    bridge.change(text, { ref: r });
  },
  liquid: (el) => bindLiquid(el),
};
const inspector = createInspector($("insp"), { board, viewer, api, actions });
let list = null, dock = null, bridge = null;

// a board whose licence doesn't allow copies can't be changed (the backend's own 403 wording)
function changeBlock() {
  if (board.local) return "A board opened from a file can't be changed by Etch.";
  return board.meta.copyable === false ? "This board's licence does not allow copying it, so Etch cannot change it." : "";
}

// ---------- edit tools: copy / paste / duplicate / delete go to the agent as change requests ----------
let clip = [];
const partLabel = (r) => { const p = board.parts[r] || {}; return `${r}${p.value ? ` (${p.value})` : ""}`; };
function blocked() { const why = changeBlock(); if (why) { toast(why); dock?.say(why); return true; } if (sel.some((r) => locked.has(r))) { toast("Unlock the part first."); return true; } return false; }
$("copyB").onclick = () => { if (!sel.length) return; clip = [...sel]; toast(`Copied ${clip.join(", ")}`); syncTools(); };
$("pasteB").onclick = () => { if (!clip.length || blocked()) return; bridge?.change(`add another ${clip.map(partLabel).join(", ")} next to the original`, { ref: clip[0] }); };
$("dupB").onclick = () => { if (!sel.length || blocked()) return; bridge?.change(`duplicate ${sel.map(partLabel).join(", ")}`, { ref: sel[0] }); };
$("delB").onclick = () => { if (!sel.length || blocked()) return; bridge?.change(`remove ${sel.map(partLabel).join(", ")}`, { ref: sel[0] }); };
$("showAllB").onclick = () => {
  const hid = [...viewer.hidden], iso = viewer.isolated;
  record("show all parts", () => { for (const r of hid) viewer.setHidden(r, false); viewer.isolate(null); }, () => { for (const r of hid) viewer.setHidden(r, true); viewer.isolate(iso); });
  list?.sync(); inspector.render(); syncTools();
};

// ---------- view tools + camera ----------
let cube = null, camBtns = null;
function mountCube() {
  cube?.dispose();
  cube = createViewCube($("cubeHost"), { camera: viewer.camera, controls: viewer.controls, onSnap: (name) => { segSync(name === "top" ? "top" : "3d"); } });
}
function setOrtho(on) {
  viewer.setOrtho(on);
  camBtns?.setOrtho(viewer.ortho);
  mountCube();
  segSync();
}
camBtns = createCameraButtons($("camHost"), {
  home: () => { viewer.reset(); segSync("3d"); },
  fit: () => viewer.fitView(sel.length ? sel : null),
  zoomIn: () => viewer.zoomBy(1.35),
  zoomOut: () => viewer.zoomBy(1 / 1.35),
  toggleOrtho: (next) => setOrtho(typeof next === "boolean" ? next : !viewer.ortho),
});
mountCube();
viewer.on("frame", () => cube?.update());
let viewMode = "3d";
function segSync(mode) {
  if (mode) viewMode = mode;
  for (const b of $("viewSeg").children) {
    const on = b.dataset.v === "ortho" ? viewer.ortho : b.dataset.v === viewMode;
    b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false");
  }
}
for (const b of $("viewSeg").children) b.onclick = () => {
  if (b.dataset.v === "ortho") return setOrtho(!viewer.ortho);
  if (b.dataset.v === "top") viewer.view("top"); else viewer.reset();
  segSync(b.dataset.v);
};
$("cw").addEventListener("dblclick", () => { viewer.reset(); segSync("3d"); });

// ---------- right panel: chevron collapse, liquid tabs, bottom sheet on phones ----------
function setPanel(open) {
  document.body.classList.toggle("rp-closed", !open);
  $("panelT").setAttribute("aria-pressed", open ? "true" : "false");
  $("rpChev").setAttribute("aria-label", open ? "Collapse panel" : "Open panel");
  $("rp").classList.toggle("open", open);
  setTimeout(() => viewer.invalidate(), 320);
}
$("panelT").onclick = () => setPanel(document.body.classList.contains("rp-closed"));
$("rpChev").onclick = () => setPanel(document.body.classList.contains("rp-closed"));
$("grab").onclick = () => $("rp").classList.toggle("full");
if (MOBILE.matches) setPanel(false);
function showTab(v) {
  $("paneParts").hidden = v !== "parts"; $("paneBoard").hidden = v !== "board";
  if (v === "board") list?.render();
}
liquidTabs(document.querySelector(".rp-tabs"), { value: "parts", onChange: showTab });

// ---------- bottom-right: snap grid + settings (board colours, workplane) ----------
const snap = () => parseFloat($("snapS").value) || 1;
function pop(btn, el) {
  btn.addEventListener("click", (e) => { e.stopPropagation(); const show = !el.classList.contains("show"); closePops(); if (show) el.classList.add("show"); });
  el.addEventListener("click", (e) => e.stopPropagation());
}
function closePops() { for (const p of document.querySelectorAll(".pop.show")) p.classList.remove("show"); }
addEventListener("click", closePops);
pop($("setB"), $("setPop")); pop($("importB"), $("importPop")); pop($("exportB"), $("exportPop"));
const swatchHex = (n) => "#" + n.toString(16).padStart(6, "0");
function renderSettings() {
  const el = $("setPop"); el.textContent = "";
  const look = viewer.look;
  const group = (title, table, cur, set, label = (k) => k) => {
    const row = h("div", { class: "sw-row", role: "radiogroup", "aria-label": title });
    for (const [k, v] of Object.entries(table)) {
      const col = typeof v === "number" ? v : v.color;
      row.append(h("button", { type: "button", class: "sw" + (k === cur ? " on" : ""), role: "radio", "aria-checked": k === cur ? "true" : "false", title: label(k), "aria-label": `${title}: ${label(k)}`, style: `--c:${swatchHex(col)}`,
        onclick: () => { const prev = viewer.look[title === "Solder mask" ? "mask" : title === "Silkscreen" ? "silk" : "finish"]; record(`${title.toLowerCase()} ${k}`, () => set(k), () => set(prev)); renderSettings(); } }));
    }
    return h("div", { class: "sw-g" }, h("div", { class: "sw-t" }, title, h("span", {}, label(cur))), row);
  };
  el.append(h("h3", {}, "Settings"),
    group("Solder mask", MASKS, look.mask, (k) => viewer.setMask(k)),
    group("Silkscreen", SILKS, look.silk, (k) => viewer.setSilk(k)),
    group("Finish", FINISHES, look.finish, (k) => viewer.setFinish(k), (k) => (k === "HASL" ? "HASL (tin)" : "ENIG (gold)")),
    h("label", { class: "toggle" }, h("input", { type: "checkbox", checked: viewer.floor || null, onchange: (e) => viewer.setFloor(e.target.checked) }), "Workplane grid"));
}

// ---------- import / export / share ----------
function renderMenus() {
  const im = $("importPop"); im.textContent = "";
  im.append(
    h("a", { role: "menuitem", href: page("explore") + carry() }, h("b", {}, "Browse open-source boards"), h("span", {}, "Real KiCad projects from GitHub")),
    h("button", { role: "menuitem", type: "button", onclick: () => $("glbFile").click() }, h("b", {}, "Open a .glb file"), h("span", {}, "View a 3D board from your computer")));
  const ex = $("exportPop"); ex.textContent = "";
  const m = board.meta;
  if (board.files) ex.append(h("a", { role: "menuitem", href: board.files.glb, download: (m.slug || slugOf(ID)) + ".glb" }, h("b", {}, "3D model (.glb)"), h("span", {}, "Board and parts, metres, Y up")));
  if (board.live) {
    ex.append(h("button", { role: "menuitem", type: "button", onclick: () => download(`/etch/glb?id=${encodeURIComponent(ID)}`, `${ID}.glb`) }, h("b", {}, "3D model (.glb)"), h("span", {}, "From the Etch engine")));
    ex.append(h("button", { role: "menuitem", type: "button", onclick: () => download(`/etch/export.zip?id=${encodeURIComponent(ID)}`, `${ID}-fab.zip`) }, h("b", {}, "KiCad project + Gerbers (.zip)"), h("span", {}, "Checked fab files, drill, BOM")));
  }
  if (m.url) {
    const at = m.commit ? `/tree/${m.commit}` : "", dir = m.pcb_path ? "/" + m.pcb_path.split("/").slice(0, -1).join("/") : "";
    ex.append(h("a", { role: "menuitem", href: m.url + at + (m.commit ? dir : ""), target: "_blank", rel: "noopener" }, h("b", {}, "KiCad project"), h("span", {}, `On GitHub at ${m.commit ? m.commit.slice(0, 7) : "the latest commit"}`)));
  }
  if (!ex.children.length) ex.append(h("div", { class: "empty" }, "Nothing to export for this board."));
}
async function download(path, name) {
  try {
    const r = await api.authed(path);
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error((j.reasons || [])[0] || j.error || `error ${r.status}`); }
    const a = h("a", { href: URL.createObjectURL(await r.blob()), download: name }); document.body.append(a); a.click(); a.remove();
  } catch (e) { toast(e.message === "no backend" ? "Downloads need the Etch engine." : e.message); }
}
$("shareB").onclick = async () => {
  try { await navigator.clipboard.writeText(location.href); toast("Link copied"); wobble($("shareB")); }
  catch (e) { toast("Copy the address bar to share this board"); }
};
$("glbFile").addEventListener("change", async (e) => {
  const f = e.target.files?.[0]; if (!f) return;
  try {
    board.local = true; board.parts = {}; board.meta = { title: f.name.replace(/\.glb$/i, "") };
    await viewer.load(await f.arrayBuffer(), { parts: {}, meta: board.meta });
    for (const r of Object.keys(viewer.parts)) board.parts[r] = { ref: r };
    showMeta(board.meta); setSel([]); list?.render(); renderSettings();
    dock?.setStatus(`Opened ${f.name}`);
  } catch (er) { toast("That file isn't a 3D board Etch can open."); }
});

// ---------- hover tag ----------
let mouse = { x: 0, y: 0 };
$("stage").addEventListener("pointermove", (e) => { mouse = { x: e.clientX, y: e.clientY }; });
viewer.on("hover", (ref) => {
  const t = $("htag");
  if (!ref || ghostDrop.active) { t.classList.remove("show"); return; }
  const p = board.parts[ref] || {}, r = $("stage").getBoundingClientRect();
  t.innerHTML = `<b>${esc(ref)}</b>${p.value ? ` ${esc(p.value)}` : ""}`;
  t.style.transform = `translate(${mouse.x - r.left + 14}px, ${mouse.y - r.top + 14}px)`;
  t.classList.add("show");
});

// ---------- drop a part from the panel onto the board ----------
let apiBase = "";
const ghostDrop = createDrop({
  stage: $("stage"), ghost: $("ghost"), viewer, snap,
  imgUrl: (p) => (p.img && apiBase ? `${apiBase}/etch/piece/${p.img}.png` : ""),
  onDrop(part, l) {
    if (blockedDrop()) return;
    const x = l.mm[0].toFixed(snap() < 1 ? 2 : 1), y = l.mm[1].toFixed(snap() < 1 ? 2 : 1);
    const name = part.value ? `${part.name} (${part.value})` : part.name;
    bridge?.change(`add ${name} here (${x}, ${y} mm from the board's top-left corner)`, { at: { x: +x, y: +y, mm: l.mm, abs: l.abs } });
  },
});
function blockedDrop() { const why = changeBlock(); if (!why) return false; dock?.setState("confused"); dock?.say(why); toast(why); return true; }

// ---------- credits (required by the boards' licences) ----------
function showMeta(m) {
  const title = m.title || ID || "Board";
  $("crTitle").textContent = title;
  document.title = `${title} · Etch`;
  const c = $("credits"); c.textContent = "";
  if (m.owner && m.repo) {
    const href = (m.url || `https://github.com/${m.owner}/${m.repo}`) + (m.commit ? `/tree/${m.commit}` : "");
    c.append("From ", h("a", { href, target: "_blank", rel: "noopener" }, `${m.owner}/${m.repo}`), m.commit ? h("span", { class: "sha" }, ` @ ${m.commit.slice(0, 7)}`) : null,
      m.license ? h("span", {}, " · ", m.license_url ? h("a", { href: m.license_url, target: "_blank", rel: "noopener" }, m.license) : m.license) : null);
  } else if (board.live) c.append(`Etch build ${ID.replace("live-", "#")}`);
  renderMenus();
}
function fail(title, html) {
  const e = $("err"); e.classList.add("show");
  e.firstElementChild.innerHTML = `<b>${esc(title)}</b>${html}`;
  hidePoster();
}

// ---------- load ----------
async function fetchBoard(steps) {
  if (board.live) {
    await api.init();
    if (!api.hasBackend()) throw Object.assign(new Error("This Etch build needs the Etch engine. Open it from the Etch app, or add <code>?api=</code>."), { html: true });
    const q = encodeURIComponent(ID);
    const pr = api.authed(`/etch/parts?id=${q}`).then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
    const lr = api.authed(`/etch/layers?id=${q}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    const parts = await pr;
    const meta = { id: ID, title: parts.title || parts.name || `Etch build ${ID.replace("live-", "#")}`, board: {}, copyable: true };
    showMeta(meta); steps.done("board", meta.title);
    steps.done("parts", `${Object.keys(parts.parts || {}).length} parts`);
    const glb = await api.authed(`/etch/glb?id=${q}`).then((r) => { if (!r.ok) throw new Error(`3D model: error ${r.status}`); return r.arrayBuffer(); });
    steps.done("glb", `${(glb.byteLength / 1048576).toFixed(1)} MB`);
    return { glb, meta, parts: parts.parts || {}, layers: await lr };
  }
  const f = board.files = boardFiles(slugOf(ID));
  const mp = boot.meta || cachedFetch(f.meta).then((r) => { if (!r.ok) throw new Error("missing"); return r.json(); });
  const pp = boot.parts || cachedFetch(f.parts).then((r) => (r.ok ? r.json() : {}));
  const gp = boot.glb || cachedFetch(f.glb).then((r) => { if (!r.ok) throw new Error("missing"); return r.arrayBuffer(); });
  const meta = await mp;
  showMeta(meta); steps.done("board", meta.title);
  const parts = await pp.catch(() => ({}));
  const pl = parts.parts || parts || {};
  steps.done("parts", `${Object.keys(pl).length} parts`);
  const glb = await gp;
  steps.done("glb", `${(glb.byteLength / 1048576).toFixed(1)} MB`);
  return { glb, meta, parts: pl, layers: null };
}

// render:done from a change: load the new run's board in place, keeping the camera and selection
viewer.reload = async ({ run } = {}) => {
  try {
    if (run && /^live-\d+$/.test(run) && api.hasBackend() && !api.status.mock) {
      const q = encodeURIComponent(run);
      const [glb, parts, layers] = await Promise.all([
        api.authed(`/etch/glb?id=${q}`).then((r) => { if (!r.ok) throw new Error(`error ${r.status}`); return r.arrayBuffer(); }),
        api.authed(`/etch/parts?id=${q}`).then((r) => (r.ok ? r.json() : {})).catch(() => ({})),
        api.authed(`/etch/layers?id=${q}`).then((r) => (r.ok ? r.json() : null)).catch(() => null)]);
      board.parts = parts.parts || {};
      await viewer.load(glb, { parts: board.parts, meta: board.meta, layers, keepView: true });
      board.id = run; board.kind = "live"; board.live = true;
      history.replaceState(null, "", page("board") + carry({ id: run }));
    } else if (board.files) {
      // demo: the same board again, so the swap in place is visible
      const glb = await cachedFetch(board.files.glb).then((r) => r.arrayBuffer());
      await viewer.load(glb, { parts: board.parts, meta: board.meta, keepView: true });
    }
    api.setBoard(board.id, board.parts, board.meta);
    renderSettings(); list?.render(); setSel(sel);
  } catch (e) { toast("Couldn't load the new version: " + e.message); }
};

async function main() {
  if (!ID) { fail("No board chosen", `Pick one in <a href="${page("explore") + carry()}">Explore</a>.`); return; }
  await api.init();
  if (!api.status.mock) {
    const cfg = Q.get("api") ? {} : await fetch("config.json", { cache: "no-store" }).then((r) => r.json()).catch(() => ({}));
    apiBase = String(Q.get("api") || cfg.api || "").replace(/\/$/, "");
  }
  createPartsPanel($("paneParts"), { apiBase, onDragStart: (p, e) => ghostDrop.start(p, e), onDragEnd: (p, e) => ghostDrop.end(p, e) });

  // the dock and the agent bridge; until the dock loads, the load steps wait in a queue
  const createDock = await loadDock();
  dock = createDock(document.body, {
    onSend(text, { via } = {}) {
      const ref = sel.length === 1 ? sel[0] : null;
      if (CHANGE.test(text)) { if (blockedDrop()) return; bridge.change(text, { ref }); }
      else bridge.ask(text, { ref });
      void via;
    },
    onMention(ref, { hover } = {}) {
      if (hover) { viewer.setHover(ref || null, { silent: true }); return; }
      if (ref) setSel([ref], { focus: true });
    },
    partScreenPos(ref) {
      const a = viewer.partAnchor(ref); if (!a) return null;
      const s = viewer.project(a), r = viewer.canvas.getBoundingClientRect();
      return s.behind ? null : { x: s.x + r.left, y: s.y + r.top };
    },
  });
  bridge = createAgentBridge({
    dock, api, board, viewer,
    highlight(refs) {
      const list2 = Array.isArray(refs) ? refs : refs ? [refs] : [];
      viewer.mark(list2);
      clearTimeout(main.markT); if (list2.length) main.markT = setTimeout(() => viewer.mark([]), 4000);
    },
    select(ref) { if (ref) setSel([ref], { focus: true }); },
  });
  const steps = bridge.onLoad([
    { id: "board", title: "Opening the board" },
    { id: "parts", title: "Reading parts.json" },
    { id: "glb", title: "Loading the 3D board" },
    { id: "render", title: "Rendering in 3D" },
  ]);

  let data;
  try { data = await fetchBoard(steps); }
  catch (e) {
    const missing = /missing|404/.test(e.message);
    steps.fail(missing ? "board" : "glb", missing ? "Not in the library" : e.message.replace(/<[^>]+>/g, ""));
    fail(missing ? "This board isn't in the library" : "Couldn't load this board",
      missing ? `Nothing for <code>${esc(ID)}</code>. <a href="${page("explore") + carry()}">Back to Explore</a>.` : e.html ? e.message : esc(e.message));
    return;
  }
  board.meta = data.meta; board.parts = data.parts;
  api.setBoard(ID, board.parts, board.meta);
  const n = Object.keys(board.parts).length;
  steps.update("render", { title: `Rendering ${n} parts` });
  viewer.on("ready", () => { hidePoster(); steps.done("render", `${Object.keys(viewer.parts).length} with 3D bodies`); });
  try {
    await viewer.load(data.glb, { parts: board.parts, meta: board.meta, layers: data.layers });
  } catch (e) { steps.fail("render", e.message); fail("Couldn't show this board in 3D", esc(e.message)); return; }
  list = createBoardList($("paneBoard"), { board, viewer, actions });
  list.render();
  renderSettings();
  syncTools();
  bindLiquid(document);
  document.body.classList.add("ready");
  if (Q.get("sel")) setSel(Q.get("sel").split(","), { focus: true });
}

// change requests (typed or spoken) start the change flow; everything else is a question
const CHANGE = /^(please\s+|can you\s+|could you\s+|etch,?\s+)*(change|make|turn|swap|replace|add|remove|delete|move|rotate|use|switch|recolou?r|paint|shrink|enlarge|widen|route|duplicate)\b/i;

// ---------- keyboard ----------
const typing = () => { const a = document.activeElement; return a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT" || a.isContentEditable); };
addEventListener("keydown", (e) => {
  if (typing()) return;
  const mod = e.metaKey || e.ctrlKey, k = e.key.toLowerCase();
  if (k === "escape") { closePops(); setSel([]); }
  else if (mod && k === "z") { e.preventDefault(); e.shiftKey ? stepHistory(redo, undo, false) : stepHistory(undo, redo, true); }
  else if (mod && k === "y") { e.preventDefault(); stepHistory(redo, undo, false); }
  else if (mod && k === "c") $("copyB").click();
  else if (mod && k === "v") $("pasteB").click();
  else if (mod && k === "d") { e.preventDefault(); $("dupB").click(); }
  else if (mod && k === "a") { e.preventDefault(); setSel(Object.keys(board.parts)); }
  else if (!mod && (k === "delete" || k === "backspace") && sel.length) { e.preventDefault(); $("delB").click(); }
  else if (!mod && k === "h" && sel.length) actions.hide(sel, !sel.every((r) => viewer.hidden.has(r)));
  else if (!mod && k === "i" && sel.length) actions.isolate(viewer.isolated ? null : sel);
  else if (!mod && k === "f") viewer.fitView(sel.length ? sel : null);
});

window.__editor = { board, viewer, api, get dock() { return dock; }, get bridge() { return bridge; }, setSel, get sel() { return sel; } }; // inspection from tests
main();

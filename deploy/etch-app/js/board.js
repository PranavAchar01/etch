// Board view: one board in 3D with Etch beside it. Left: chat about the whole board. Canvas: click a part to pin a
// numbered comment; Etch answers on a card tied to the pin that follows the part as the camera moves. Bottom: solder
// mask / silkscreen / finish recolour. Works for imported boards (gh:, static files) and Etch builds (live-*).
import { Q, $, h, esc, md, ago, icon, LOGO, carry, page, toast, slugOf, isLive, boardFiles, cachedFetch, registerSW } from "./common.js";
import { createBoardViewer, MASKS, SILKS, FINISHES } from "./board-viewer.js";
import * as api from "./etch-api.js";
import { createVoice } from "./voice.js";
import { createMascot, mascotFrame } from "./mascot.js";

registerSW();
const ID = Q.get("id") || "";
const MOBILE = matchMedia("(max-width: 760px)");

// ---------- static chrome ----------
$("logo").innerHTML = LOGO;
$("homeL").href = page("index") + carry();
$("exL").href = page("explore") + carry();
for (const [id, ic] of [["sideT", "sidebar"], ["undoB", "undo"], ["redoB", "redo"], ["histB", "history"], ["resetB", "refresh"], ["findB", "search"], ["panelT", "panel"], ["newChat", "plus"], ["wideB", "expand"], ["sendB", "send"]]) $(id).innerHTML = icon(ic, id === "sendB" ? 16 : 18);
// the talk button is Etch itself; a small mic badge says what it does
$("micB").innerHTML = `<canvas class="mascot" width="32" height="30"></canvas><span class="micbadge">${icon("mic", 12)}</span>`;
const mascot = createMascot($("micB").querySelector("canvas"));
let thinking = 0;
const think = (d) => { thinking = Math.max(0, thinking + d); if (mascot.state === "idle" || mascot.state === "thinking") mascot.state = thinking ? "thinking" : "idle"; };
$("dlB").insertAdjacentHTML("afterbegin", icon("download", 16));

const board = { id: ID, meta: {}, parts: {}, refs: new Set(), files: null, glbUrl: "", live: isLive(ID) };
let viewer = null;

// crossfade from the poster to the live view, then take the poster out of the page
function hidePoster() {
  const p = $("poster"); if (!p) return;
  p.classList.add("gone"); p.style.opacity = "0";
  setTimeout(() => p.remove(), 600);
}
function fail(title, text) {
  const e = $("err"); e.classList.add("show");
  e.firstElementChild.innerHTML = `<b>${esc(title)}</b>${text}`;
  hidePoster();
}

// ---------- load the board ----------
async function loadBoard() {
  if (!ID) { fail("No board chosen", `Pick one in <a href="${page("explore") + carry()}">Explore</a>.`); return false; }
  viewer = createBoardViewer($("cw"));
  viewer.attachMinimap($("miniC"));
  viewer.on("ready", hidePoster);
  let glb, meta = {}, parts = {}, layers = null;
  try {
    if (board.live) {
      await api.init();
      if (!api.hasBackend()) { fail("This Etch build needs the Etch engine", "No backend is configured for this page. Open it from the Etch app, or add <code>?api=</code>."); return false; }
      const q = encodeURIComponent(ID);
      const pr = api.backend(`/etch/parts?id=${q}`).then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
      const lr = api.backend(`/etch/layers?id=${q}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
      glb = await api.backend(`/etch/glb?id=${q}`).then((r) => { if (!r.ok) throw new Error(`3D model: error ${r.status}`); return r.arrayBuffer(); });
      parts = await pr; layers = await lr;
      meta = { id: ID, title: parts.title || parts.name || `Etch build ${ID.replace("live-", "#")}`, board: {}, copyable: true };
      board.glbUrl = "";
    } else {
      const boot = window.__boot || {};
      const f = board.files = boardFiles(slugOf(ID));
      board.glbUrl = f.glb;
      if (!boot.glb) window.__showPoster?.(f.thumb);
      const mp = boot.meta || cachedFetch(f.meta).then((r) => { if (!r.ok) throw new Error("missing"); return r.json(); });
      const pp = boot.parts || cachedFetch(f.parts).then((r) => (r.ok ? r.json() : {}));
      const gp = boot.glb || cachedFetch(f.glb).then((r) => { if (!r.ok) throw new Error("missing"); return r.arrayBuffer(); });
      meta = await mp.catch(() => ({ id: ID, title: ID.replace(/^gh:/, "") }));
      showMeta(meta);
      parts = await pp.catch(() => ({}));
      glb = await gp;
    }
  } catch (e) {
    const missing = /missing|404/.test(e.message);
    fail(missing ? "This board isn't in the library" : "Couldn't load this board",
      missing ? `Nothing for <code>${esc(ID)}</code>. <a href="${page("explore") + carry()}">Back to Explore</a>.` : esc(e.message));
    return false;
  }
  board.meta = meta; board.parts = parts.parts || parts || {};
  showMeta(meta);
  api.setBoard(ID, board.parts, meta);
  try {
    const r = await viewer.load(glb, { parts: board.parts, meta, layers });
    board.refs = new Set([...r.parts, ...Object.keys(board.parts)]);
    paintLook(r.look);
  } catch (e) { fail("Couldn't show this board in 3D", esc(e.message)); return false; }
  return true;
}

function showMeta(m) {
  const title = m.title || ID;
  $("crTitle").textContent = title;
  fitCrumb();
  document.title = `${title} · Etch`;
  const bits = ["PCB and soldered parts"];
  let src = "";
  if (m.owner && m.repo) {
    const at = m.commit ? `/tree/${m.commit}` : "";
    src = ` from <a href="${esc((m.url || `https://github.com/${m.owner}/${m.repo}`) + at)}" target="_blank" rel="noopener">${esc(m.owner)}/${esc(m.repo)}${m.commit ? " @ " + esc(m.commit.slice(0, 7)) : ""}</a>`;
  }
  $("info").innerHTML = `${bits[0]}${src}${m.license ? ` · ${esc(m.license)}` : ""}${m.kicad ? ` · rendered with KiCad ${esc(m.kicad)}` : ""}.`;
  renderDownloads(m);
  renderDrawer();
}

// ---------- top bar ----------
// a long board title gets the room: the (placeholder) menu words step aside rather than cut the title short
function fitCrumb() {
  const b = $("crTitle"), m = document.querySelector(".menu"); if (!m) return;
  m.style.display = "";
  if (b.scrollWidth > b.clientWidth + 1) m.style.display = "none";
}
addEventListener("resize", fitCrumb);
$("sideT").onclick = () => { if (MOBILE.matches) $("chat").classList.toggle("open"); else document.body.classList.toggle("nochat"); setTimeout(() => viewer?.invalidate(), 260); };
$("wideB").onclick = () => { $("chat").classList.toggle("wide"); };
for (const b of $("viewSeg").children) b.onclick = () => {
  if (b.dataset.v === "3d") return;
  const name = { schematic: "Schematic", pcb: "PCB layout", product: "Product" }[b.dataset.v];
  toast(`${name} view is coming soon. The board is shown in 3D.`);
};
for (const b of document.querySelectorAll(".menu button")) b.onclick = () => toast(`The ${b.textContent} menu is coming soon.`);
$("histB").onclick = () => toast("Version history is coming soon.");
$("resetB").onclick = () => viewer?.reset();
$("homeV").onclick = () => viewer?.reset();
$("cw").addEventListener("dblclick", () => viewer?.reset());
$("mini").onclick = () => viewer?.reset();
$("shareB").onclick = async () => {
  try { await navigator.clipboard.writeText(location.href); toast("Link copied"); }
  catch (e) { toast("Copy the address bar to share this board"); }
};
function pop(btn, el) {
  btn.addEventListener("click", (e) => { e.stopPropagation(); const show = !el.classList.contains("show"); closePops(); if (show) { el.classList.add("show"); el.querySelector("input")?.focus(); } });
  el.addEventListener("click", (e) => e.stopPropagation());
}
function closePops() { for (const p of document.querySelectorAll(".pop.show")) p.classList.remove("show"); }
addEventListener("click", closePops);
pop($("dlB"), $("dlPop"));
pop($("findB"), $("findPop"));
function renderDownloads(m) {
  const el = $("dlPop"); el.textContent = "";
  const glbName = (m.slug || slugOf(ID)) + ".glb";
  if (board.live) el.append(h("button", { onclick: downloadLive, html: icon("download", 16) + "<div>3D model (.glb)<small>This Etch build</small></div>" }));
  else el.append(h("a", { href: board.files?.glb || "#", download: glbName, html: icon("download", 16) + `<div>3D model (.glb)<small>${m.glb_bytes ? (m.glb_bytes / 1e6).toFixed(1) + " MB · " : ""}KiCad render</small></div>` }));
  if (m.url) el.append(h("a", { href: m.commit ? `${m.url}/tree/${m.commit}` : m.url, target: "_blank", rel: "noopener", html: icon("github", 16) + `<div>KiCad source on GitHub<small>${esc(m.owner)}/${esc(m.repo)}${m.commit ? " @ " + esc(m.commit.slice(0, 7)) : ""}</small></div>` }));
  if (m.license_url) el.append(h("a", { href: m.license_url, target: "_blank", rel: "noopener", html: icon("info", 16) + `<div>Licence<small>${esc(m.license || "")}</small></div>` }));
}
async function downloadLive() {
  try {
    const r = await api.backend(`/etch/glb?id=${encodeURIComponent(ID)}`); if (!r.ok) throw new Error();
    const a = h("a", { href: URL.createObjectURL(await r.blob()), download: ID + ".glb" }); document.body.append(a); a.click(); a.remove();
  } catch (e) { toast("Couldn't download the model"); }
}
// find a part
function partRow(ref, after) {
  const p = board.parts[ref] || {};
  return h("button", { onclick: () => { after?.(); focusPart(ref); } }, h("b", {}, ref), h("span", {}, [p.value, (p.footprint || "").split(":").pop()].filter(Boolean).join(" · ")));
}
function renderFind() {
  const q = $("findQ").value.trim().toLowerCase(), el = $("findL"); el.textContent = "";
  const refs = [...board.refs].filter((r) => !q || `${r} ${board.parts[r]?.value || ""} ${board.parts[r]?.footprint || ""} ${board.parts[r]?.description || ""}`.toLowerCase().includes(q)).sort(natural).slice(0, 80);
  for (const r of refs) el.append(partRow(r, closePops));
  if (!refs.length) el.append(h("div", { class: "card-meta", style: "padding:8px" }, "No parts match"));
}
const natural = (a, b) => a.localeCompare(b, undefined, { numeric: true });
$("findQ").addEventListener("input", renderFind);
$("findB").addEventListener("click", renderFind);
$("findQ").addEventListener("keydown", (e) => { if (e.key === "Enter") $("findL").querySelector("button")?.click(); });
function focusPart(ref) { if (!viewer) return; viewer.focus(ref); viewer.select(ref); clearTimeout(focusPart.t); focusPart.t = setTimeout(() => viewer.select(draft?.ref || null), 1800); }
// board info drawer
$("panelT").onclick = () => { const on = $("drawer").classList.toggle("show"); $("panelT").classList.toggle("on", on); setTimeout(() => viewer?.invalidate(), 260); };
function renderDrawer() {
  const m = board.meta, el = $("drawerIn"); if (!el) return;
  const facts = [["Repo", m.owner ? `${m.owner}/${m.repo}` : ""], ["Licence", m.license], ["Layers", m.layers], ["Size", m.size_mm ? `${m.size_mm[0]} × ${m.size_mm[1]} mm` : ""], ["Parts", Object.keys(board.parts).length || m.parts], ["Stars", m.stars], ["KiCad", m.kicad]].filter((f) => f[1] != null && f[1] !== "");
  el.innerHTML = `<h3>${esc(m.title || ID)}</h3><p>${esc(m.description || "")}</p><div class="k">About</div><dl class="facts">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl><div class="k">Parts</div>`;
  const list = h("div", { class: "plist" });
  for (const r of Object.keys(board.parts).sort(natural)) list.append(partRow(r));
  el.append(list);
}

// ---------- appearance: mask / silk / finish, with undo ----------
const undo = [], redo = [];
let look = { mask: "green", silk: "white", finish: "HASL" };
function paintLook(l) {
  look = { ...l };
  for (const [g, set] of [["maskG", "mask"], ["silkG", "silk"]]) for (const b of $(g).querySelectorAll(".sw")) b.classList.toggle("on", b.dataset.v === look[set]);
  for (const b of $("finG").querySelectorAll(".fin")) b.classList.toggle("on", b.dataset.v === look.finish);
  $("undoB").disabled = !undo.length; $("redoB").disabled = !redo.length;
}
function applyLook(l, record = true) {
  if (record) { undo.push({ ...look }); redo.length = 0; }
  if (!viewer) return;
  if (l.mask !== look.mask) viewer.setMask(l.mask);
  if (l.silk !== look.silk) viewer.setSilk(l.silk);
  if (l.finish !== look.finish) viewer.setFinish(l.finish);
  paintLook(l);
}
const HEX = (n) => "#" + n.toString(16).padStart(6, "0");
for (const [name, hex] of Object.entries(MASKS)) $("maskG").append(h("button", { class: "sw", "data-v": name, title: `${name} mask`, "aria-label": `${name} solder mask`, style: `background:${HEX(hex)}`, onclick: () => applyLook({ ...look, mask: name }) }));
for (const [name, hex] of Object.entries(SILKS)) $("silkG").append(h("button", { class: "sw", "data-v": name, title: `${name} silkscreen`, "aria-label": `${name} silkscreen`, style: `background:${HEX(hex)}`, onclick: () => applyLook({ ...look, silk: name }) }));
for (const name of Object.keys(FINISHES)) $("finG").append(h("button", { class: "fin", "data-v": name, title: name === "ENIG" ? "Gold pads (ENIG)" : "Silver pads (HASL)", onclick: () => applyLook({ ...look, finish: name }) }, name));
$("undoB").onclick = () => { if (!undo.length) return; redo.push({ ...look }); applyLook(undo.pop(), false); };
$("redoB").onclick = () => { if (!redo.length) return; undo.push({ ...look }); applyLook(redo.pop(), false); };
paintLook(look);
// Floor | 3D | Photo
for (const b of $("modeSeg").children) b.onclick = () => {
  const m = b.dataset.m;
  if (m === "floor") { const on = !b.classList.contains("on"); b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); viewer?.setFloor(on); return; }
  for (const x of $("modeSeg").children) if (x.dataset.m !== "floor") x.classList.toggle("on", x === b);
  $("stage").classList.toggle("photo", m === "photo");
  viewer?.setPhoto(m === "photo");
};

// ---------- answer blocks (shared by chat and cards) ----------
function answerBlock(a, { compact = false } = {}) {
  const wrap = h("div", { class: compact ? "card-a" : "msg-a" });
  if (!compact) wrap.append(h("div", { class: "who", html: `<img class="avatar" src="${mascotFrame()}" alt="" width="20" height="19">Etch` }));
  else wrap.append(h("div", { class: "who" }, "Etch"));
  if (a.pending) {
    wrap.append(h("div", { class: "worked" }, "Thinking…"), h("div", { class: "typing", html: "<i></i><i></i><i></i>" }));
    return wrap;
  }
  const th = a.thoughts || [];
  if (th.length) {
    const sec = Math.max(1, Math.round(a.seconds || 0));
    const list = h("div", { class: "thoughts", hidden: compact || undefined });
    for (const t of th) list.append(h("div", { class: "thought", html: icon("chev", 14) + `Thought: ${esc(t)}` }));
    const w = h("button", { type: "button", class: "worked" + (compact ? "" : " open"), html: `Worked for ${sec}s` + icon("chev", 14) });
    w.onclick = () => { list.hidden = !list.hidden; w.classList.toggle("open", !list.hidden); layoutSoon(); };
    wrap.append(w, list);
  }
  wrap.append(h("div", { class: "answer", html: md(a.text, board.refs) }));
  if (!compact) {
    const up = h("button", { class: "ib" + (a.fb === 1 ? " on" : ""), title: "Good answer", "aria-label": "Good answer", html: icon("up", 16) });
    const dn = h("button", { class: "ib" + (a.fb === -1 ? " on" : ""), title: "Bad answer", "aria-label": "Bad answer", html: icon("down", 16) });
    const set = (v) => { a.fb = a.fb === v ? 0 : v; up.classList.toggle("on", a.fb === 1); dn.classList.toggle("on", a.fb === -1); saveChat(); if (a.fb) toast("Thanks for the feedback"); };
    up.onclick = () => set(1); dn.onclick = () => set(-1);
    wrap.append(h("div", { class: "fb" }, up, dn));
  }
  return wrap;
}
// refdes mentioned in an answer light up the part
document.addEventListener("click", (e) => { const r = e.target.closest?.(".ref"); if (r) focusPart(r.dataset.ref); });
document.addEventListener("pointerover", (e) => { const r = e.target.closest?.(".ref"); if (r && viewer) viewer.setHover(r.dataset.ref); });
document.addEventListener("pointerout", (e) => { const r = e.target.closest?.(".ref"); if (r && viewer) viewer.setHover(null); });

// ---------- chat panel ----------
const CHAT_KEY = "etch-chat:" + ID;
let chat = [];
try { chat = JSON.parse(localStorage.getItem(CHAT_KEY) || "[]"); } catch (e) { chat = []; }
function saveChat() { try { localStorage.setItem(CHAT_KEY, JSON.stringify(chat.filter((m) => !m.pending))); } catch (e) { /* full */ } }
const SUGG = ["What is this board for? I'm not an engineer.", "How many parts are on it?", "Can I copy and change this board?"];
function renderChat() {
  const t = $("thread"); t.textContent = "";
  if (!chat.length) {
    const s = h("div", { class: "sugg" });
    for (const q of SUGG) s.append(h("button", { type: "button", onclick: () => askBoard(q) }, q));
    t.append(h("div", { class: "empty-chat" }, h("b", {}, "Ask Etch about this board"), "Talk to Etch with the mic below. Point at a part while you talk, or click it, to pin your question to that part.", s));
    return;
  }
  for (const m of chat) t.append(m.role === "user" ? h("div", { class: "msg-u" }, m.text) : answerBlock(m));
  t.scrollTop = t.scrollHeight;
}
async function askBoard(q, how = "typed") {
  q = q.trim(); if (!q) return;
  chat.push({ role: "user", text: q, at: Date.now() / 1000, how });
  const pend = { role: "assistant", pending: true }; chat.push(pend); renderChat();
  if (MOBILE.matches) $("chat").classList.add("open");
  try {
    think(1);
    const r = await api.ask({ board: ID, question: q }).finally(() => think(-1));
    Object.assign(pend, { pending: false, text: r.answer, thoughts: r.thoughts || [], seconds: r.seconds, mentions: r.mentions || [] });
    if (how === "spoken") voice.answer(r.answer);
  } catch (e) { Object.assign(pend, { pending: false, text: `Sorry, I couldn't answer that: ${e.message}` }); if (how === "spoken") voice.answer(e.message); }
  saveChat(); renderChat();
}
// one place every question goes, spoken or typed: a part being pointed at (or clicked) pins it to that part, a change
// request starts the change flow, anything else is about the whole board
const CHANGE = /^(please\s+|can you\s+|could you\s+|etch,?\s+)*(change|make|turn|swap|replace|add|remove|delete|move|rotate|take (it|this) apart|explode|use|switch|recolou?r|paint|shrink|enlarge|widen|route)\b/i;
const COLORS = "green|purple|red|yellow|blue|white|black";
function route(text, how) {
  text = text.trim(); if (!text) return;
  const ref = draft?.ref || (how === "spoken" ? pointedAt() : null);
  if (CHANGE.test(text)) { preview(text); return changeBoard(text, ref, how); }
  if (draft) return submitDraft(text, how);
  if (ref) { const a = viewer.partAnchor(ref); if (a) return pinAndAsk(ref, a, text, how); }
  askBoard(text, how);
}
// the colour words in a change request show at once on the 3D board, while the real change runs
function preview(text) {
  const t = text.toLowerCase(), c = t.match(new RegExp(`\\b(${COLORS})\\b`));
  if (c && /mask|board|pcb/.test(t) && MASKS[c[1]]) applyLook({ ...look, mask: c[1] });
  else if (c && /silk|label|text|legend/.test(t) && SILKS[c[1]]) applyLook({ ...look, silk: c[1] });
  if (/\b(enig|gold)\b/.test(t)) applyLook({ ...look, finish: "ENIG" });
  else if (/\b(hasl|silver|tin)\b/.test(t)) applyLook({ ...look, finish: "HASL" });
}
// a board whose licence doesn't allow copies can't be changed (the backend's own 403 wording); everything else is
// up to the backend, whose refusals (409 files not on hand, 422 too many / unknown parts, 429 busy) are shown as is
function changeBlock() {
  return board.meta.copyable === false ? "This board's licence does not allow copying it, so Etch cannot change it." : "";
}
async function changeBoard(text, ref, how) {
  chat.push({ role: "user", text, at: Date.now() / 1000, how });
  const msg = { role: "assistant", text: "Starting the change…", thoughts: [], seconds: 0 }; chat.push(msg); renderChat();
  const set = (t) => { msg.text = t; saveChat(); renderChat(); };
  const why = changeBlock();
  if (why) {
    const shown = /mask|silk|finish|gold|enig|hasl|colou?r/i.test(text) ? " I've shown that look on the 3D view." : "";
    set(why + shown);
    if (how === "spoken") voice.answer(msg.text);
    return;
  }
  try {
    const ask = ref ? `${ref}: ${text}` : text;
    const r = board.live ? await api.changeLive({ run: ID, text: ask }) : await api.change({ board: ID, ref: ref || undefined, text });
    if (r.ok === false) throw new Error(r.error || "refused");
    set(r.mock ? "Demo: I've shown the look on the 3D board. With the Etch engine connected, Etch copies this board into a new build and changes it." : board.live ? "On it. I'm building a new version of this board." : "On it. I'm copying this board into a new Etch build and changing it.");
    if (how === "spoken") voice.answer(msg.text);
    if (!r.mock) followJob(r.job, set, how);
  } catch (e) { set(e.message); if (how === "spoken") voice.answer(e.message); }
}
// questions, comments and replies stop at 400 characters (the backend refuses longer); a counter shows near the end
function limitField(input, wrap, send) {
  input.maxLength = api.MAX_TEXT;
  const count = h("span", { class: "count", "aria-live": "polite" });
  send.before(count);
  const upd = () => {
    const n = input.value.length;
    count.textContent = n > api.MAX_TEXT - 60 ? `${n}/${api.MAX_TEXT}` : "";
    count.classList.toggle("full", n >= api.MAX_TEXT);
    send.disabled = !input.value.trim();
  };
  input.addEventListener("input", upd);
  return upd;
}
const ci = $("ci");
const autosize = limitField(ci, $("comp"), $("sendB"));
$("comp").addEventListener("submit", (e) => { e.preventDefault(); const q = ci.value; ci.value = ""; autosize(); route(q, "typed"); });
$("newChat").onclick = () => { chat = []; saveChat(); renderChat(); ci.focus(); };
$("chatH").addEventListener("click", (e) => { if (MOBILE.matches && !e.target.closest(".acts")) $("chat").classList.toggle("open"); });
let commentMode = true;
$("cmtChip").onclick = () => { commentMode = !commentMode; $("cmtChip").classList.toggle("on", commentMode); $("cmtChip").setAttribute("aria-pressed", commentMode); if (!commentMode) closeDraft(); };
const EFF = ["Effort Max", "Effort Medium", "Effort Low"];
$("effChip").onclick = () => { const b = $("effChip"); b.textContent = EFF[(EFF.indexOf(b.textContent) + 1) % EFF.length]; };
$("prodChip").onclick = () => { const b = $("prodChip"), on = b.classList.toggle("on"); b.setAttribute("aria-pressed", on); };
api.onStatus((s) => { $("demo").classList.toggle("show", s.mock); $("demo").lastElementChild.textContent = s.mock ? "Demo answers, written from this board's parts list." : ""; $("demo").title = s.why; });
renderChat();

// ---------- pinned comments ----------
const cmts = new Map(); // id -> { c, pin, card, pill, line, open, mode, status }
let draft = null; // { ref, anchor, pin, pill, input }
const ov = $("ov"), links = $("links");

// a pin shows its number only once the server has given it one (numbers are never reused or shuffled)
function pinEl(n, cls = "") { return h("div", { class: "pin " + (n ? "" : "draft ") + cls, html: `<span></span><b>${n || ""}</b>` }); }

function closeDraft() { if (!draft) return; draft.pin.remove(); draft.pill.remove(); draft = null; viewer?.select(null); layoutSoon(); }
function openDraft(ref, anchor) {
  closeDraft();
  const input = h("input", { placeholder: placeholderFor(ref), "aria-label": `Ask about ${ref}`, maxlength: String(api.MAX_TEXT) });
  const send = h("button", { class: "send", type: "submit", "aria-label": "Send", disabled: true, html: icon("send", 16) });
  const pill = h("form", { class: "askpill" }, input, send);
  limitField(input, pill, send);
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDraft(); });
  pill.addEventListener("submit", (e) => { e.preventDefault(); const t = input.value.trim(); if (t) submitDraft(t); });
  draft = { ref, anchor, pin: pinEl(0), pill, input };
  ov.append(draft.pin, pill);
  viewer.select(ref);
  layout();
  setTimeout(() => input.focus({ preventScroll: true }), 0);
}
function placeholderFor(ref) {
  const p = board.parts[ref] || {}, fp = String(p.footprint || "");
  if (/Socket|Header|Conn|USB|RJ45/i.test(fp + ref)) return `What plugs into ${ref}?`;
  return `Ask Etch about ${ref}…`;
}
function submitDraft(text, how = "typed") {
  const { ref, anchor } = draft; closeDraft();
  return pinAndAsk(ref, anchor, text, how);
}
async function pinAndAsk(ref, anchor, text, how = "typed") {
  const n = 0; // the server numbers the pin
  // show the thread at once, with Etch thinking
  const temp = { id: "tmp_" + Date.now(), n, ref, anchor, author: "You", created: Date.now() / 1000, messages: [{ role: "user", text, at: Date.now() / 1000 }, { role: "assistant", pending: true }] };
  addComment(temp, true);
  viewer.select(null);
  try {
    think(1);
    const r = await api.createComment({ board: ID, ref, anchor, text }).finally(() => think(-1));
    removeComment(temp.id);
    addComment(r.comment, true);
    const a = r.comment.messages[r.comment.messages.length - 1];
    if (how === "spoken") {
      // spoken turns also land in the thread, tagged with the part
      chat.push({ role: "user", text: `${ref}: ${text}`, how }, { role: "assistant", text: a.text, thoughts: a.thoughts || [], seconds: a.seconds, mentions: a.mentions || [] });
      saveChat(); renderChat(); voice.answer(a.text);
    }
  } catch (e) {
    const x = cmts.get(temp.id); if (x) { x.c.messages[1] = { role: "assistant", text: `Sorry, that didn't go through: ${e.message}` }; renderCard(x); }
    if (how === "spoken") voice.answer(e.message);
  }
}
function addComment(c, open = false) {
  const x = { c, open, mode: "reply", status: "" };
  x.pin = pinEl(c.n);
  x.pin.title = c.n ? `${c.ref} · comment ${c.n}` : c.ref;
  x.pin.onclick = () => { x.open = !x.open; renderCard(x); layoutSoon(); };
  x.card = h("div", { class: "card", role: "group", "aria-label": `Comment on ${c.ref}` });
  x.pill = h("div", { class: "qpill" });
  x.line = document.createElementNS("http://www.w3.org/2000/svg", "line");
  links.append(x.line);
  ov.append(x.pin, x.pill, x.card);
  cmts.set(c.id, x);
  renderCard(x);
  layoutSoon();
}
function removeComment(id) {
  const x = cmts.get(id); if (!x) return;
  x.pin.remove(); x.card.remove(); x.pill.remove(); x.line.remove(); cmts.delete(id); layoutSoon();
}
function renderCard(x) {
  const { c } = x, card = x.card;
  card.classList.toggle("hidden", !x.open);
  x.pill.style.display = x.open ? "" : "none";
  x.pill.textContent = c.messages[0]?.text || "";
  x.pill.title = x.pill.textContent;
  card.textContent = "";
  const close = h("button", { class: "ib", title: "Close", "aria-label": "Close", html: icon("close", 15), onclick: () => { x.open = false; renderCard(x); layoutSoon(); } });
  card.append(h("div", { class: "card-h" }, h("div", {}, h("div", { class: "card-ref" }, c.ref), h("div", { class: "card-meta", html: `<b>${esc(c.author || "You")}</b> · ${esc(ago(c.created))}` })), close));
  const body = h("div", { class: "card-body" });
  c.messages.forEach((m, i) => {
    if (m.role === "user") { if (i > 0) body.append(h("div", { class: "card-q" }, m.text)); }
    else body.append(answerBlock(m, { compact: true }));
  });
  card.append(body);
  if (x.status) card.append(h("div", { class: "status" }, x.status));
  const input = h("input", { placeholder: x.mode === "change" ? `Describe the change to ${c.ref}…` : "Reply…", "aria-label": "Reply", maxlength: String(api.MAX_TEXT) });
  const send = h("button", { class: "send", type: "submit", disabled: true, "aria-label": "Send", html: icon("send", 15) });
  input.addEventListener("keydown", (e) => { if (e.key === "Escape" && x.mode === "change") { x.mode = "reply"; renderCard(x); } });
  const form = h("form", { class: "card-reply" }, input, send);
  limitField(input, form, send);
  form.addEventListener("submit", (e) => { e.preventDefault(); const t = input.value.trim(); if (!t) return; x.mode === "change" ? doChange(x, t) : doReply(x, t); });
  card.append(form);
  const busy = c.messages.some((m) => m.pending) || String(c.id).startsWith("tmp_");
  const why = changeBlock();
  const chg = h("button", { class: "chg" + (why ? " off" : ""), type: "button", disabled: busy, title: why || (board.live ? "Etch builds a changed version of this board" : "Etch copies this board into a new build and changes it"),
    onclick: () => { if (why) { x.status = why; renderCard(x); layoutSoon(); return; } x.mode = x.mode === "change" ? "reply" : "change"; renderCard(x); x.card.querySelector("input").focus(); } }, x.mode === "change" ? "Back to reply" : "Ask Etch to change it");
  const del = h("button", { class: "ib", title: "Delete comment", "aria-label": "Delete comment", disabled: busy, html: icon("trash", 16), onclick: () => doDelete(x) });
  card.append(h("div", { class: "card-f" }, chg, del));
  // a thread scrolls to its latest question; a single answer reads from the top
  const bd = card.querySelector(".card-body"), qs = bd.querySelectorAll(".card-q");
  bd.scrollTop = qs.length ? qs[qs.length - 1].offsetTop - 6 : 0;
}
async function doReply(x, text) {
  x.c.messages.push({ role: "user", text, at: Date.now() / 1000 }, { role: "assistant", pending: true });
  renderCard(x); layoutSoon();
  try { const r = await api.reply({ id: x.c.id, text, board: ID }); x.c = r.comment; }
  catch (e) { x.c.messages[x.c.messages.length - 1] = { role: "assistant", text: `Sorry, that didn't go through: ${e.message}` }; }
  renderCard(x); layoutSoon();
}
async function doDelete(x) {
  const id = x.c.id;
  removeComment(id);
  try { await api.deleteComment({ id, board: ID }); toast("Comment deleted"); }
  catch (e) { toast("Couldn't delete: " + e.message); addComment(x.c, x.open); }
}
async function doChange(x, text) {
  x.status = "Starting a change…"; x.mode = "reply"; renderCard(x); layoutSoon();
  try {
    // live-* builds change through the existing /etch/live modify mode; imported boards through /etch/change
    const ask = `${x.c.ref}: ${text}`;
    const r = board.live ? await api.changeLive({ run: ID, text: ask }) : await api.change({ board: ID, ref: x.c.ref, text });
    if (r.ok === false) throw new Error(r.error || "refused");
    x.status = r.mock ? "Demo: with the Etch engine connected, this starts a modified build." : "Change started. Etch is building a new version.";
    renderCard(x); layoutSoon();
    if (!r.mock) followJob(r.job, (st) => { x.status = st; renderCard(x); layoutSoon(); });
  } catch (e) { x.status = e.message; renderCard(x); layoutSoon(); }
}
// API.md build events: lead:say -> chat, stage -> status line, render:done -> reload the 3D board of the new run,
// lead:done ok -> that run is the board now
async function followJob(job, setStatus, how = "typed") {
  let since = 0, run = null, miss = 0;
  for (;;) {
    let d;
    try { d = await api.job(job, since); miss = 0; }
    catch (e) { if (/404|forgotten|not found/i.test(e.message) || ++miss > 20) { setStatus("Lost track of the build. Open it from the Etch app's library."); return; } await sleep(1500); continue; }
    since = d.next ?? since + (d.events || []).length;
    if (d.stage) setStatus(d.stage.charAt(0).toUpperCase() + d.stage.slice(1) + "…");
    for (const ev of d.events || []) {
      const k = `${ev.agent}:${ev.type}`;
      if (ev.run) run = ev.run;
      if (k === "lead:say" && ev.text) { chat.push({ role: "assistant", text: ev.text, thoughts: [], seconds: 0 }); saveChat(); renderChat(); if (how === "spoken") voice.say(ev.text); }
      if (k === "render:done" && run && isLive(run)) reloadLive(run);
      if (k === "lead:done") {
        if (ev.ok && run) {
          setStatus("Done. Opening the new version…"); toast("New version ready");
          if (isLive(run) && run !== ID) setTimeout(() => location.assign(page("board") + carry({ id: run })), 900);
        }
        else setStatus(ev.error || "The checks didn't all pass, so the board wasn't changed.");
        return;
      }
      if (k === "lead:error") { setStatus("The change stopped with an error: " + (ev.error || "unknown")); return; }
      if (k === "lead:cancelled") { setStatus("The change was cancelled."); return; }
    }
    if (d.done) { if (d.state === "done" && !run) setStatus("Done."); return; }
    await sleep(400);
  }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function reloadLive(run) {
  try {
    const q = encodeURIComponent(run);
    const [glb, parts, layers] = await Promise.all([
      api.backend(`/etch/glb?id=${q}`).then((r) => { if (!r.ok) throw new Error(`3D model: error ${r.status}`); return r.arrayBuffer(); }),
      api.backend(`/etch/parts?id=${q}`).then((r) => (r.ok ? r.json() : {})).catch(() => ({})),
      api.backend(`/etch/layers?id=${q}`).then((r) => (r.ok ? r.json() : null)).catch(() => null)]);
    board.parts = parts.parts || {};
    const res = await viewer.load(glb, { parts: board.parts, meta: board.meta, layers });
    board.refs = new Set([...res.parts, ...Object.keys(board.parts)]);
    paintLook(res.look); renderDrawer(); layoutSoon();
  } catch (e) { toast("Couldn't load the new version: " + e.message); }
}

async function loadComments() {
  try {
    const r = await api.listComments(ID);
    const list = (r.comments || []).sort((a, b) => a.n - b.n);
    list.forEach((c, i) => addComment(c, i >= list.length - 3));
  } catch (e) { /* comments are optional */ }
}

// ---------- overlay layout: pins on their anchors, cards beside them, no overlaps ----------
let layoutQueued = false;
function layoutSoon() { if (layoutQueued) return; layoutQueued = true; requestAnimationFrame(() => { layoutQueued = false; layout(); }); }
function layout() {
  if (!viewer) return;
  const { w: W, h: H } = viewer.size;
  const narrow = W < 560;
  const bodyMax = Math.max(84, Math.round(H * 0.6) - 150);
  if (ov.style.getPropertyValue("--body-max") !== bodyMax + "px") ov.style.setProperty("--body-max", bodyMax + "px");
  const sheet = MOBILE.matches ? $("chat").offsetHeight : 0;
  const TOP = narrow ? 196 : 64, BOT = H - (narrow ? sheet + 70 : 74);
  // read sizes first, then write positions
  const items = [];
  for (const x of cmts.values()) {
    const p = viewer.project(x.c.anchor || viewer.partAnchor(x.c.ref) || [0, 0, 0]);
    const vis = !p.behind && p.x > -20 && p.x < W + 20 && p.y > -20 && p.y < H + 20;
    // full and compact heights from the body's own content, so toggling compact never feeds back into the choice
    const body = x.open ? x.card.querySelector(".card-body") : null, bh = body ? body.offsetHeight : 0, bs = body ? body.scrollHeight : 0;
    const base = x.open ? x.card.offsetHeight - bh : 0;
    items.push({ x, p, vis, full: base + Math.min(bs, Math.max(84, Math.round(H * 0.6) - 150)), small: base + Math.min(bs, 112), cw: x.card.offsetWidth || 284, pw: x.open ? Math.min(x.pill.offsetWidth, 300) : 0 });
  }
  // pins are obstacles too, so a card never hides another comment's pin
  const placed = items.filter((i) => i.vis).map((i) => ({ l: i.p.x - 14, t: i.p.y - 28, w: 28, h: 30, pin: i }));
  // the floating chrome (caption toggles, bottom bar, minimap) is off limits too
  const cr = $("cw").getBoundingClientRect();
  for (const id of ["bb", "mini", "modeSeg"]) {
    const r = $(id)?.getBoundingClientRect();
    if (r && r.width) placed.push({ l: r.left - cr.left, t: r.top - cr.top, w: r.width, h: r.height });
  }
  if (draft) { const dp = viewer.project(draft.anchor); placed.push({ l: dp.x - 14, t: dp.y - 40, w: (draft.pill.offsetWidth || 320) + 40, h: 48 }); }
  // newest first gets the best spot; a card that can't fit without overlapping waits (its pin stays, click to open)
  const order = items.filter((i) => i.vis && i.x.open).sort((a, b) => (b.x.c.created || 0) - (a.x.c.created || 0) || b.x.c.n - a.x.c.n);
  const clash = (l, t, w, hh, own) => placed.find((o) => l < o.l + o.w + 10 && l + w + 10 > o.l && t < o.t + o.h + 8 && t + hh + 8 > o.t);
  for (const it of order) {
    const gap = 44, pillH = it.x.pill.textContent ? 44 : 0;
    let best = null, hgt = 0;
    // full card if it fits, else a compact one (the answer scrolls inside it), else just the pin
    for (const ch of [it.full, it.small]) {
      hgt = pillH + ch; it.ch = ch; it.compact = ch === it.small && it.small < it.full;
      const want = it.p.y - 40 - pillH;
      const prefRight = it.p.x + gap + it.cw <= W - 12;
      // beside the pin on either side first; failing that, out at the canvas edges (the line still ties it to its pin)
      const sides = [[true, it.p.x + gap], [false, it.p.x - gap - it.cw], [true, W - it.cw - 16], [false, 16]];
      for (const [right0, l0] of sides) {
        const left = Math.max(8, Math.min(W - it.cw - 8, l0)), right = left >= it.p.x ? true : left + it.cw <= it.p.x ? false : right0;
        const cands = [want, ...placed.flatMap((o) => [o.t + o.h + 8, o.t - 8 - hgt])];
        for (let top of cands) {
          top = Math.max(TOP, Math.min(Math.max(TOP, BOT - hgt), top));
          if (clash(left, top, it.cw, hgt, it)) continue;
          const dx = right ? left - it.p.x : it.p.x - (left + it.cw);
          const cost = Math.abs(top - want) + Math.max(0, dx - gap) * 0.8 + (right === prefRight ? 0 : 60);
          if (!best || cost < best.cost) best = { left, top, right, cost };
        }
      }
      if (best) break;
    }
    // a phone has no room beside the pins: the newest card docks under the toolbar, compact, over the board
    if (!best && narrow && it === order[0]) { hgt = pillH + it.small; it.ch = it.small; it.compact = it.small < it.full; best = { left: Math.max(8, (W - it.cw) / 2), top: TOP, right: true }; }
    if (!best) continue;
    placed.push({ l: best.left, t: best.top, w: it.cw, h: hgt });
    it.left = best.left; it.top = best.top; it.pillH = pillH; it.right = best.right;
  }
  for (const it of items) {
    const { x, p, vis } = it;
    x.pin.style.display = vis ? "" : "none";
    x.pin.style.transform = `translate(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px)`;
    const show = vis && x.open && it.left != null;
    x.card.style.visibility = x.pill.style.visibility = show ? "" : "hidden";
    x.line.style.display = show ? "" : "none";
    if (!show) continue;
    x.pill.style.transform = `translate(${(it.right ? it.left : it.left + it.cw - it.pw).toFixed(1)}px, ${it.top.toFixed(1)}px)`;
    const ct = it.top + it.pillH;
    x.card.style.transform = `translate(${it.left.toFixed(1)}px, ${ct.toFixed(1)}px)`;
    x.card.classList.toggle("compact", !!it.compact);
    const bd = x.card.querySelector(".card-body"); if (bd) bd.classList.toggle("more", bd.scrollHeight - bd.scrollTop - bd.clientHeight > 4);
    const ex = it.right ? it.left : it.left + it.cw, ey = Math.max(ct + 18, Math.min(ct + it.ch - 18, p.y - 13));
    x.line.setAttribute("x1", p.x); x.line.setAttribute("y1", p.y - 13); x.line.setAttribute("x2", ex); x.line.setAttribute("y2", ey);
  }
  if (draft) {
    const p = viewer.project(draft.anchor);
    draft.pin.style.transform = `translate(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px)`;
    const pw = draft.pill.offsetWidth || 320;
    let l = p.x + 22; if (l + pw > W - 10) l = p.x - 22 - pw;
    l = Math.max(8, l);
    draft.pill.style.transform = `translate(${l.toFixed(1)}px, ${(p.y - 34).toFixed(1)}px)`;
  }
}

// ---------- canvas interaction ----------
const REF_OK = /^[A-Za-z][A-Za-z0-9_.+-]{0,15}$/;
const pinnable = (ref) => !!ref && REF_OK.test(ref) && Object.prototype.hasOwnProperty.call(board.parts, ref);
const htag = $("htag");
let mouse = [0, 0];
$("cw").addEventListener("pointermove", (e) => { const r = $("cw").getBoundingClientRect(); mouse = [e.clientX - r.left, e.clientY - r.top]; if (htag.classList.contains("show")) htag.style.transform = `translate(${mouse[0]}px, ${mouse[1] - 14}px) translate(-50%, -100%)`; });
function wireViewer() {
  viewer.on("frame", layout);
  viewer.on("start", () => { htag.classList.remove("show"); });
  viewer.on("hover", (ref) => {
    if (ref) lastPoint = { ref, t: performance.now() };
    if (!ref) { htag.classList.remove("show"); return; }
    const p = board.parts[ref] || {};
    htag.innerHTML = `${esc(ref)}${p.value ? `<small>${esc(p.value)}</small>` : ""}`;
    htag.style.transform = `translate(${mouse[0]}px, ${mouse[1] - 14}px) translate(-50%, -100%)`;
    htag.classList.add("show");
  });
  viewer.on("pick", ({ ref, anchor }) => {
    closePops();
    if (!ref) { closeDraft(); return; }
    // only parts listed for this board can carry a comment (the backend refuses any other ref)
    if (!pinnable(ref)) { closeDraft(); toast("That part isn't listed for this board"); return; }
    if (!commentMode) { ci.value = (ci.value ? ci.value.trimEnd() + " " : "") + ref + " "; autosize(); ci.focus(); viewer.select(ref); return; }
    if (voice.listening) { lastPoint = { ref, t: performance.now() }; viewer.select(ref); return; } // pointing while talking
    if (draft && draft.ref === ref) { closeDraft(); return; }
    openDraft(ref, anchor);
  });
}
addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeDraft(); closePops(); }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("findB").click(); }
});
setInterval(() => { for (const x of cmts.values()) { const m = x.card.querySelector(".card-meta"); if (m) m.innerHTML = `<b>${esc(x.c.author || "You")}</b> · ${esc(ago(x.c.created))}`; } }, 30000);

// the mobile bottom sheet's height drives where the bottom bar and cards sit
new ResizeObserver(() => { $("stage").style.setProperty("--sheet-h", ($("chat").offsetHeight || 0) + "px"); layoutSoon(); }).observe($("chat"));
document.addEventListener("scroll", (e) => { if (e.target.classList?.contains("card-body")) layoutSoon(); }, true);
$("chat").querySelector(".sheet-grab").addEventListener("click", () => { if (MOBILE.matches) $("chat").classList.toggle("open"); });
// ---------- voice: Etch's main way of talking ----------
let lastPoint = null, micAt = 0;
// the part the person is pointing at: hovered when they started talking or since, or clicked while talking
function pointedAt() { return lastPoint && pinnable(lastPoint.ref) && lastPoint.t >= micAt - 1500 && performance.now() - lastPoint.t < 15000 ? lastPoint.ref : null; }
const bars = [...$("lvl").children];
const TOUCH = matchMedia("(pointer: coarse)").matches;
const VSTATE = { idle: TOUCH ? "Tap to talk, or hold the mic" : "Click to talk, or hold <kbd>Space</kbd>", listening: "Listening…", transcribing: "Transcribing…", speaking: "Etch is talking", off: "" };
const voice = createVoice({
  onState(s, d) {
    $("voice").dataset.state = s;
    mascot.state = s === "transcribing" ? "thinking" : s === "off" ? "idle" : s === "idle" && thinking ? "thinking" : s;
    if (s === "listening") { micAt = performance.now(); if (!pointedAt()) lastPoint = null; $("vText").textContent = "Point at a part to pin your question to it."; }
    if (s === "off") { $("micB").disabled = true; $("vState").textContent = d || "Voice is off"; $("vText").textContent = "Type below instead."; return; }
    $("vState").innerHTML = VSTATE[s] ?? "";
  },
  onLevel(who, x) { mascot.level(x); const n = bars.length; bars.forEach((b, i) => { const k = Math.max(0.12, Math.min(1, x * (1.4 - Math.abs(i - (n - 1) / 2) / n))); b.style.transform = `scaleY(${k.toFixed(2)})`; }); $("lvl").dataset.who = x > 0.02 ? who : ""; },
  onInterim(t) { $("vText").textContent = `“${t}”`; },
  onHeard(t) { $("vText").textContent = `“${t}”`; route(t, "spoken"); },
  onCaption(t) { $("vText").textContent = t; },
});
voice.bind($("micB"));
$("vState").innerHTML = VSTATE.idle;
// ---------- go ----------
api.init();
if (await loadBoard()) {
  wireViewer();
  loadComments();
  renderChat();
}
window.__board = { board, viewer, cmts, api, voice }; // inspection from the console

// Stand-in for Etch's board endpoints when there's no backend (or ?mock=1). Comments live in localStorage per board;
// answers are written from the board's own parts.json: the part's value, footprint and description, what kind of
// part its refdes and footprint say it is, and the parts sitting nearest to it. Short Markdown, a few "thoughts",
// and a 0.6-1.5 s think so the UI behaves like the real thing.

const boards = new Map(); // board id -> { parts, meta }
const KEY = (board) => "etch-comments:" + board;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const think = () => sleep(600 + Math.random() * 900);
const now = () => Math.floor(Date.now() / 1000);

export function setBoard(board, parts, meta) { boards.set(board, { parts: parts || {}, meta: meta || {} }); }

function load(board) { try { return JSON.parse(localStorage.getItem(KEY(board)) || "[]"); } catch (e) { return []; } }
function save(board, list) { try { localStorage.setItem(KEY(board), JSON.stringify(list)); } catch (e) { /* full / private */ } }
function find(id) {
  for (const board of boards.keys()) { const list = load(board); const c = list.find((x) => x.id === id); if (c) return { board, list, c }; }
  // a board we haven't seen this session: scan storage
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i); if (!k || !k.startsWith("etch-comments:")) continue;
    const board = k.slice(14), list = load(board), c = list.find((x) => x.id === id); if (c) return { board, list, c };
  }
  return null;
}

// ---- what a part is, from its refdes prefix, footprint library and value ----
const KINDS = [
  [/RF_Module|WROOM|WROVER|ESP32|nRF52|RP2040|STM32|ATmega|SAMD/i, "module", "the board's main controller"],
  [/PinSocket|Socket/i, "socket", "a female header that a shield, display or ribbon plugs into"],
  [/PinHeader/i, "header", "a male pin header for jumpers, a debugger or a breadboard"],
  [/USB_|USB-C|Receptacle|USB Micro|USB_Mini/i, "usb", "the USB connection"],
  [/Connector|^J\d|^P\d|^CN/i, "connector", "a connector that brings signals or power on and off the board"],
  [/Button|Switch|^SW/i, "button", "a push button"],
  [/Crystal|^Y\d|^X\d/i, "crystal", "the clock reference"],
  [/LED/i, "led", "an indicator LED"],
  [/Capacitor|^C\d/i, "capacitor", "a capacitor"],
  [/Resistor|^R\d|^RN/i, "resistor", "a resistor"],
  [/Inductor|^L\d|^FB/i, "inductor", "an inductor / ferrite"],
  [/Diode|^D\d/i, "diode", "a diode"],
  [/SOT-23|^Q\d/i, "transistor", "a transistor"],
  [/Package_|SOIC|QFN|TSSOP|^U\d|^IC/i, "ic", "an integrated circuit"],
  [/MountingHole|^H\d/i, "hole", "a mounting hole"],
  [/TestPoint|^TP/i, "testpoint", "a test point"],
];
function kindOf(p) {
  const s = `${p.footprint || ""} ${p.value || ""} ${p.ref || ""}`;
  for (const [re, k, what] of KINDS) if (re.test(s) || re.test(p.ref || "")) return { k, what };
  return { k: "part", what: "a component" };
}
function near(parts, ref, n = 3) {
  const me = parts[ref]; if (!me?.pos_mm) return [];
  return Object.values(parts).filter((p) => p.ref !== ref && p.pos_mm)
    .map((p) => ({ p, d: Math.hypot(p.pos_mm[0] - me.pos_mm[0], p.pos_mm[1] - me.pos_mm[1]) }))
    .sort((a, b) => a.d - b.d).slice(0, n);
}
// library descriptions can be long spec lists or boilerplate: keep the first clause or two, drop the boilerplate
function tidy(d) {
  d = String(d || "").replace(/\s+/g, " ").trim();
  if (!d || /script generated|^generic\b/i.test(d)) return "";
  if (d.length <= 70) return d;
  const parts = d.split(/,\s*/); let out = "";
  for (const x of parts) { if ((out + ", " + x).length > 70 && out) break; out = out ? out + ", " + x : x; }
  return out;
}
const lib = (fp) => String(fp || "").split(":").pop();

function partAnswer(board, ref, question) {
  const { parts, meta } = boards.get(board) || { parts: {}, meta: {} };
  const p = parts[ref] || { ref };
  const { k, what } = kindOf(p);
  const nb = near(parts, ref);
  const q = String(question || "").toLowerCase();
  const mentions = nb.map((x) => x.p.ref);
  const thoughts = [`Reading ${ref} in the parts list`, "Checking what sits around it", "Writing the answer"];
  const v0 = String(p.value || "").split(/[\/]/)[0].replace(/\(.*\)$/, "").trim(), val = v0 ? ` (${v0})` : "";
  let lead;
  if (/replace|alternative|substitut|swap|cheaper/.test(q)) {
    lead = `**Replacing ${ref}${val}**: pick a part with the same footprint, \`${lib(p.footprint) || "the same footprint"}\`, so it drops onto the existing pads. ` +
      (k === "capacitor" ? "Match the capacitance and use an equal or higher voltage rating; X5R/X7R keeps the value under bias." :
       k === "resistor" ? "Match the resistance; power and tolerance can only go up." :
       k === "module" || k === "ic" ? "Check the pinout too: a pin-compatible part in the same package is the only drop-in." :
       "Check the pinout and ratings against the datasheet.");
  } else if (/value|rating|size|footprint|package/.test(q)) {
    lead = `**${ref} is ${p.value || "unmarked"}** in a \`${lib(p.footprint) || "unknown"}\` package${p.side ? `, on the ${p.side} side` : ""}.`;
  } else {
    const desc = tidy(p.description || p.why || ""), purpose = desc ? desc.replace(/\.$/, "") : what;
    lead = `**${ref} is ${k === "part" ? "a part" : what}**${val}${desc ? `: ${purpose}` : ""}.`;
    if (k === "socket") lead += " Anything plugged in here sits above the board, so leave clearance when you design a case.";
    if (k === "module") lead += " Most of the other parts exist to power it, program it, or break its pins out.";
    if (k === "capacitor") lead += " It steadies the supply right where a chip draws current.";
    if (k === "button") lead += " Pressed during reset it changes how the chip boots.";
    if (k === "usb") lead += " It's how you power the board and flash new firmware.";
  }
  const short = (v) => String(v || "").split(/[\/(]/)[0].trim();
  const around = nb.length ? `\n\nIt sits next to ${nb.map((x) => `**${x.p.ref}**${short(x.p.value) ? ` (${short(x.p.value)})` : ""}`).join(", ")}.` : "";
  return { ok: true, answer: lead + around, mentions, thoughts, seconds: 0 };
}

function boardAnswer(board, question) {
  const { parts, meta } = boards.get(board) || { parts: {}, meta: {} };
  const list = Object.values(parts);
  const by = {};
  for (const p of list) { const { k } = kindOf(p); (by[k] = by[k] || []).push(p); }
  const main = (by.module || by.ic || [])[0];
  const conns = [...(by.usb || []), ...(by.connector || []), ...(by.socket || []), ...(by.header || [])];
  const q = String(question || "").toLowerCase();
  const title = meta.title || "This board";
  const size = meta.size_mm ? `${meta.size_mm[0]} × ${meta.size_mm[1]} mm` : null;
  const thoughts = ["Reading the parts list", "Grouping parts by what they do", "Writing the answer"];
  let a;
  if (/licen[cs]e|copy|fork|use it/.test(q)) {
    a = `**${title}** is published under \`${meta.license || "an unknown licence"}\`. ` + (meta.copyable ? "That licence lets you copy and modify it — ask Etch to change it and it will start a new build from this board." : "That licence doesn't allow copying, so Etch can explain it but won't start a modified copy.");
  } else if (/how many|count|parts/.test(q)) {
    a = `**${list.length} parts** on ${title}: ` + Object.entries(by).sort((x, y) => y[1].length - x[1].length).map(([k, v]) => `${v.length} ${k}${v.length > 1 ? "s" : ""}`).join(", ") + ".";
  } else {
    a = `**${title}** is ${meta.layers ? `a ${meta.layers}-layer board` : "a board"}${size ? ` about ${size}` : ""} with ${list.length} parts.` +
      (main ? ` The heart of it is **${main.ref}**${main.value ? `, a ${main.value}` : ""}.` : "") +
      (conns.length ? ` It connects to the outside world through ${conns.slice(0, 3).map((c) => `**${c.ref}**`).join(", ")}.` : "") +
      "\n\nClick any part to pin a question to it.";
  }
  const mentions = [main, ...conns.slice(0, 3)].filter(Boolean).map((p) => p.ref);
  return { ok: true, answer: a, mentions, thoughts, seconds: 0 };
}

async function answer(board, ref, question) {
  const t0 = performance.now();
  await think();
  const r = ref ? partAnswer(board, ref, question) : boardAnswer(board, question);
  r.seconds = Math.round((performance.now() - t0) / 100) / 10;
  return r;
}

// the backend's own refusals, same wording (API.md "Board view")
function err(status, msg) { const e = new Error(msg); e.status = status; return e; }
function checkText(t) {
  t = String(t || "").trim();
  if (!t) throw err(400, "Type a question first.");
  if (t.length > 400) throw err(400, "Keep it under 400 characters.");
  return t;
}
function checkRef(board, ref) { if (ref && !(boards.get(board)?.parts || {})[ref]) throw err(400, "That part is not on this board."); }
export async function ask({ board, ref, question }) { question = checkText(question); checkRef(board, ref); return answer(board, ref, question); }
export async function listComments(board) { await sleep(60); return { ok: true, comments: load(board) }; }
export async function createComment({ board, ref, anchor, text }) {
  text = checkText(text); checkRef(board, ref);
  const r = await answer(board, ref, text);
  const list = load(board);
  const t = now();
  // pin numbers count up per board and are never reused, even after a delete (like the backend)
  let last = 0; try { last = +localStorage.getItem("etch-comments-n:" + board) || 0; } catch (e) { /* none */ }
  const n = Math.max(last, list.reduce((m, c) => Math.max(m, c.n), 0)) + 1;
  try { localStorage.setItem("etch-comments-n:" + board, String(n)); } catch (e) { /* not kept */ }
  const id = "c_" + Array.from(crypto.getRandomValues(new Uint8Array(6)), (b) => b.toString(16).padStart(2, "0")).join("");
  const comment = { id, board, n, ref, anchor, author: "You", created: t,
    messages: [{ role: "user", text, at: t }, { role: "assistant", text: r.answer, at: now(), thoughts: r.thoughts, seconds: r.seconds, mentions: r.mentions }] };
  list.push(comment); save(board, list);
  return { ok: true, comment };
}
export async function reply({ id, text }) {
  text = checkText(text);
  const f = find(id); if (!f) throw err(404, "That comment is gone.");
  const r = await answer(f.board, f.c.ref, text);
  const g = find(id); if (!g) throw err(404, "That comment is gone.");
  g.c.messages.push({ role: "user", text, at: now() - Math.round(r.seconds) }, { role: "assistant", text: r.answer, at: now(), thoughts: r.thoughts, seconds: r.seconds, mentions: r.mentions });
  save(g.board, g.list);
  return { ok: true, comment: g.c };
}
export async function deleteComment({ id }) {
  const f = find(id); if (!f) throw err(404, "That comment is gone.");
  save(f.board, f.list.filter((c) => c.id !== id));
  return { ok: true };
}
export async function change({ board, ref, text }) {
  if (!String(text || "").trim()) throw err(400, "Tell me what to change.");
  await think();
  const meta = boards.get(board)?.meta || {};
  if (meta.copyable === false) throw err(403, "This board's licence does not allow copying it, so Etch cannot change it.");
  return { ok: true, job: "mock-" + Date.now(), token: "mock", mock: true };
}
export async function job(id, since) {
  await sleep(400);
  return { done: true, state: "done", stage: "done", next: (since || 0) + 1,
    events: [] };
}

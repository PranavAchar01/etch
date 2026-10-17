// "My boards": boards the person saved in the editor (Save, Ctrl/Cmd+S) or that Etch built for them, kept in this
// browser only (localStorage "etch-my-boards"). Explore shows them as a first row, the new-board start screen in its
// "existing board" choice, and the editor uses the saved title.
//
//   list() -> [{ id, title, thumb, updated, owner?, category?, license?, slug? }]   newest first
//   get(id), save(entry), rename(id, title), remove(id), onChange(fn) -> off()
//   available(), record({ run, title, prompt }), retitle(run, title): what the editor calls when a build ends
import { boardFiles, slugOf, isLive } from "./common.js";

const KEY = "etch-my-boards";
const EV = "etch-my-boards";

export function list() {
  try {
    const a = JSON.parse(localStorage.getItem(KEY) || "[]");
    // entries written as { run, t } are read as { id, updated }
    return Array.isArray(a) ? a.map((b) => (b && !b.id && typeof b.run === "string" ? { ...b, id: b.run, updated: b.updated || b.t, title: b.title || (b.prompt ? nameFrom(b.prompt) : "") } : b))
      .filter((b) => b && typeof b.id === "string" && b.id).sort((x, y) => (y.updated || 0) - (x.updated || 0)) : [];
  } catch (e) { return []; }
}
function write(a) {
  try { localStorage.setItem(KEY, JSON.stringify(a.slice(0, 60))); } catch (e) { return false; }
  dispatchEvent(new CustomEvent(EV));
  return true;
}
export const get = (id) => list().find((b) => b.id === id) || null;
export function save(entry) {
  if (!entry?.id) return false;
  const old = get(entry.id) || {};
  const e = { ...old, ...entry, title: String(entry.title || old.title || entry.id).trim().slice(0, 80), updated: Date.now() };
  return write([e, ...list().filter((b) => b.id !== e.id)]);
}
export function rename(id, title) {
  const b = get(id); if (!b) return false;
  return save({ ...b, title, renamed: true });
}
export function remove(id) { return write(list().filter((b) => b.id !== id)); }
export function onChange(fn) {
  const f = () => fn(list());
  addEventListener(EV, f);
  const s = (e) => { if (e.key === KEY) f(); }; // another tab
  addEventListener("storage", s);
  return () => { removeEventListener(EV, f); removeEventListener("storage", s); };
}
// a board's still: an Etch build's last render frame from the backend, a library board's prerendered thumb
export function thumbFor(id, apiBase = "") {
  if (isLive(id)) return apiBase ? `${apiBase}/etch/thumb?id=${encodeURIComponent(id)}` : "";
  return boardFiles(slugOf(id)).thumb;
}
// a short name from what the person asked for: at most four words, capitalised
export function nameFrom(prompt) {
  const w = String(prompt || "").replace(/[^\p{L}\p{N}\s+-]/gu, " ").split(/\s+/).filter(Boolean);
  const skip = /^(a|an|the|board|that|which|with|from|for|and|of|to|my)$/i;
  const keep = w.filter((x, i) => !(skip.test(x) && i < 2)).slice(0, 4);
  const t = (keep.length ? keep : w.slice(0, 4)).join(" ");
  return t ? t[0].toUpperCase() + t.slice(1) : "New board";
}

// false in private windows / blocked storage, so Explore can leave the row hidden
export function available() {
  try { const k = KEY + ":t"; localStorage.setItem(k, "1"); localStorage.removeItem(k); return true; } catch (e) { return false; }
}
// a build or change that ended ok: add or refresh it, never replacing a title the person already has
export function record({ run, title = "", prompt = "" } = {}) {
  if (!run) return false;
  const old = get(run);
  return save({ id: run, prompt: prompt || old?.prompt || "", title: old?.title || title || nameFrom(prompt) });
}
// the board's title once it has loaded, only when it has no name yet (never over one from the ask or a rename)
export function retitle(run, title) {
  const b = run && title ? get(run) : null;
  if (!b || b.renamed || (b.title && b.title !== b.id)) return false;
  return save({ ...b, title });
}

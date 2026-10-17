// "My boards": boards the person saved in the editor (Save, Ctrl/Cmd+S) or that Etch built for them, kept in this
// browser only (localStorage "etch-my-boards"). Explore shows them as a first row, the new-board start screen in its
// "existing board" choice, and the editor uses the saved title.
//
//   list() -> [{ id, title, thumb, updated, owner?, category?, license?, slug? }]   newest first
//   get(id), save(entry), rename(id, title), remove(id), onChange(fn) -> off()
import { boardFiles, slugOf, isLive } from "./common.js";

const KEY = "etch-my-boards";
const EV = "etch-my-boards";

export function list() {
  try {
    const a = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(a) ? a.filter((b) => b && typeof b.id === "string").sort((x, y) => (y.updated || 0) - (x.updated || 0)) : [];
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
  return save({ ...b, title });
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

// My boards: the boards this browser built or changed, kept in localStorage (there are no accounts). The editor
// records a run when its design / change job ends ok; Explore lists them. Removing one only forgets it here.
const KEY = "etch-my-boards";
const MAX = 60;

// false in private windows / blocked storage, so the Explore section stays hidden
export function available() {
  try { const k = KEY + ":t"; localStorage.setItem(k, "1"); localStorage.removeItem(k); return true; } catch (e) { return false; }
}
export function list() {
  try {
    const a = JSON.parse(localStorage.getItem(KEY) || "[]");
    return (Array.isArray(a) ? a : []).filter((x) => x && typeof x.run === "string" && x.run).sort((a, b) => (b.t || 0) - (a.t || 0));
  } catch (e) { return []; }
}
function save(a) { try { localStorage.setItem(KEY, JSON.stringify(a.slice(0, MAX))); } catch (e) { /* not kept */ } }
// add or refresh one board (newest first); empty fields never overwrite what is already known
export function record({ run, title = "", prompt = "", t = Date.now() } = {}) {
  if (!run) return;
  const old = list().find((x) => x.run === run) || {};
  save([{ run, title: title || old.title || "", prompt: prompt || old.prompt || "", t }, ...list().filter((x) => x.run !== run)]);
}
// fill in a title once the board has loaded, only for a board already recorded
export function retitle(run, title) {
  if (!run || !title) return;
  const a = list(), x = a.find((y) => y.run === run);
  if (x && x.title !== title) { x.title = title; save(a); }
}
export function remove(run) { save(list().filter((x) => x.run !== run)); }

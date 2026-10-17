// Dropping a part from the Parts panel onto the board: a ghost of the part's picture follows the cursor; over the
// board it snaps to the grid (mm from the board's top-left corner) and shows where it will land; letting go there
// asks the agent to add it. Letting go anywhere else sends the ghost back. Keyboard "place" drops at the board centre.
export function createDrop({ stage, ghost, viewer, snap, imgUrl, onDrop }) {
  const img = ghost.querySelector(".g-img"), tag = ghost.querySelector(".g-tag");
  let cur = null, last = { x: 0, y: 0 }, raf = 0;

  const track = (e) => { if (e.clientX || e.clientY) { last = { x: e.clientX, y: e.clientY }; schedule(); } };
  function schedule() { if (!raf) raf = requestAnimationFrame(() => { raf = 0; place(); }); }
  // where the cursor lands, snapped: { mm (board-relative), abs (KiCad mm), screen (page px) } or null off the board
  function landing(x, y) {
    const r = stage.getBoundingClientRect();
    if (x < r.left || x > r.right || y < r.top || y > r.bottom) return null;
    const p = viewer.pickBoard(x, y);
    if (!p || !p.inside) return null;
    const g = snap();
    const rel = [Math.round((p.mm[0] - p.rect.x0) / g) * g, Math.round((p.mm[1] - p.rect.y0) / g) * g];
    rel[0] = Math.min(Math.max(rel[0], 0), p.rect.x1 - p.rect.x0); rel[1] = Math.min(Math.max(rel[1], 0), p.rect.y1 - p.rect.y0);
    const abs = [rel[0] + p.rect.x0, rel[1] + p.rect.y0];
    const s = viewer.mmToScreen(abs[0], abs[1]);
    return { mm: rel, abs, screen: { x: s.x + r.left, y: s.y + r.top } };
  }
  const fmt = (v) => { const g = snap(); return v.toFixed(g < 1 ? (g < 0.5 ? 2 : 1) : 0); };
  function place() {
    if (!cur) return;
    const l = landing(last.x, last.y);
    cur.land = l;
    const at = l ? l.screen : last;
    ghost.style.transform = `translate(${at.x}px, ${at.y}px)`;
    ghost.classList.toggle("on-board", !!l);
    tag.textContent = l ? `${cur.part.name} · ${fmt(l.mm[0])}, ${fmt(l.mm[1])} mm` : `${cur.part.name} · drop it on the board`;
  }
  function start(part, evt = {}) {
    if (evt.keyboard) {
      const r = viewer.boardRect(); if (!r) return;
      const g = snap(), mm = [Math.round((r.x1 - r.x0) / 2 / g) * g, Math.round((r.y1 - r.y0) / 2 / g) * g];
      onDrop(part, { mm, abs: [mm[0] + r.x0, mm[1] + r.y0] });
      return;
    }
    cur = { part, from: { x: evt.clientX || 0, y: evt.clientY || 0 } };
    last = { ...cur.from };
    const u = imgUrl(part);
    img.style.backgroundImage = u ? `url("${u}")` : "";
    img.classList.toggle("blank", !u);
    ghost.classList.remove("back"); ghost.classList.add("show");
    document.body.classList.add("dragging-part");
    addEventListener("pointermove", track); addEventListener("dragover", track);
    place();
  }
  function end(part, evt = {}) {
    if (!cur) return;
    track(evt);
    const l = landing(last.x, last.y);
    removeEventListener("pointermove", track); removeEventListener("dragover", track);
    document.body.classList.remove("dragging-part");
    const done = cur; cur = null;
    if (l) {
      ghost.classList.add("drop");
      setTimeout(() => { ghost.classList.remove("show", "drop", "on-board"); }, 380);
      onDrop(done.part, l);
    } else {
      // spring back towards where it came from, then fade
      ghost.classList.add("back");
      ghost.style.transform = `translate(${done.from.x}px, ${done.from.y}px)`;
      setTimeout(() => ghost.classList.remove("show", "back", "on-board"), 360);
    }
  }
  return { start, end, get active() { return !!cur; } };
}

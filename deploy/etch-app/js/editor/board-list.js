// "This board" tab: every part on the board grouped by type, with refdes + value. Hover lights the part up in 3D,
// click selects it (shift / cmd adds to the selection), the eye hides or shows it.
import { h } from "../common.js";
import { ic } from "./icons.js";
import { kindOf, KIND_ORDER, byRef } from "./kinds.js";

export function createBoardList(host, { board, viewer, actions }) {
  const q = h("input", { type: "search", placeholder: "Find a part: U6, USB, 10k…", "aria-label": "Find a part on this board", autocomplete: "off" });
  const count = h("div", { class: "bl-count" });
  const list = h("div", { class: "bl-list", role: "listbox", "aria-multiselectable": "true", "aria-label": "Parts on this board" });
  host.append(h("div", { class: "bl-head" }, h("label", { class: "search", html: ic("search", 15) }, q), count), list);
  const open = new Set();
  let started = false;
  const rows = new Map();

  function render() {
    list.textContent = ""; rows.clear();
    const s = q.value.trim().toLowerCase();
    const parts = Object.values(board.parts || {}).filter((p) => !s || `${p.ref} ${p.value || ""} ${p.footprint || ""}`.toLowerCase().includes(s));
    const groups = new Map();
    for (const p of parts) { const k = kindOf(p); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(p); }
    count.textContent = s ? `${parts.length} of ${Object.keys(board.parts || {}).length} parts` : `${parts.length} parts`;
    if (!parts.length) { list.append(h("div", { class: "empty" }, s ? "No part matches that." : "This board has no parts list.")); return; }
    if (!started) { started = true; const first = KIND_ORDER.find((k) => groups.has(k)); if (first) open.add(first); }
    for (const k of KIND_ORDER) {
      const g = groups.get(k); if (!g) continue;
      g.sort((a, b) => byRef(a.ref, b.ref));
      const isOpen = s || open.has(k) || groups.size <= 3;
      const head = h("button", { type: "button", class: "bl-g" + (isOpen ? " open" : ""), "aria-expanded": isOpen ? "true" : "false", onclick: () => { open.has(k) ? open.delete(k) : open.add(k); render(); } },
        h("span", { class: "chev", html: ic("chev", 14) }), h("span", { class: "bl-gt" }, k), h("span", { class: "n" }, String(g.length)));
      list.append(head);
      if (!isOpen) continue;
      const box = h("div", { class: "bl-rows" });
      for (const p of g) {
        const eye = h("button", { type: "button", class: "ib xs eye", "aria-label": `Hide ${p.ref}`, title: "Hide / show", onclick: (e) => { e.stopPropagation(); actions.hide([p.ref], !viewer.hidden.has(p.ref)); } });
        const row = h("div", { class: "bl-row", role: "option", tabindex: "0", "data-ref": p.ref },
          h("b", {}, p.ref), h("span", { class: "v" }, p.value || ""), eye);
        row.addEventListener("mouseenter", () => viewer.setHover(p.ref, { silent: true }));
        row.addEventListener("mouseleave", () => viewer.setHover(null, { silent: true }));
        row.addEventListener("click", (e) => actions.pick(p.ref, { add: e.shiftKey || e.metaKey || e.ctrlKey, focus: true }));
        row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); actions.pick(p.ref, { add: e.shiftKey, focus: true }); } });
        rows.set(p.ref, { row, eye });
        box.append(row);
      }
      list.append(box);
    }
    sync();
  }
  // selection and visibility, without rebuilding the list
  function sync() {
    const sel = new Set(viewer.selected), hid = viewer.hidden;
    for (const [r, { row, eye }] of rows) {
      row.classList.toggle("on", sel.has(r)); row.setAttribute("aria-selected", sel.has(r) ? "true" : "false");
      row.classList.toggle("off", hid.has(r));
      eye.innerHTML = ic(hid.has(r) ? "eyeOff" : "eye", 14);
    }
  }
  function reveal(ref) {
    if (!rows.has(ref)) { const p = board.parts[ref]; if (p) { open.add(kindOf(p)); render(); } }
    rows.get(ref)?.row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  q.addEventListener("input", render);
  return { render, sync, reveal };
}

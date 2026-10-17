// "This board" tab: every part on the board grouped by type, with refdes + value. Hover lights the part up in 3D,
// click selects it (shift / cmd adds to the selection), the eye hides or shows it.
import { h } from "../common.js";
import { ic } from "./icons.js";
import { kindOf, KIND_ORDER, byRef } from "./kinds.js";

// Until parts.json is in (the editor creates the list only then), the pane shows skeleton rows.
const CSS = `
.bl-sk{display:block;background:linear-gradient(90deg,#eef0f3 0%,#eef0f3 30%,#f6f7f9 50%,#eef0f3 70%,#eef0f3 100%) 0 0/250% 100%;animation:bl-sk 1.4s linear infinite}
@keyframes bl-sk{from{background-position:100% 0}to{background-position:-150% 0}}
.bl-pre{display:flex;flex-direction:column;min-height:0;flex:1}
.bl-pre .bl-head .bl-sk-search{height:34px;border-radius:10px}
.bl-sk-count{width:54px;height:10px;border-radius:5px}
.bl-sk-row{display:flex;align-items:center;gap:10px;height:34px;padding:0 8px}
.bl-sk-dot{width:18px;height:18px;border-radius:50%;flex:none}
.bl-sk-t{display:flex;flex-direction:column;gap:5px;flex:1}
.bl-sk-a{height:9px;border-radius:5px;width:46px}
.bl-sk-b{height:8px;border-radius:4px;width:70%}
.bl-list>.bl-in{animation:bl-in .24s ease both}
@keyframes bl-in{from{opacity:0}}
@media (prefers-reduced-motion:reduce){.bl-sk{animation:none;background:#eef0f3}.bl-list>.bl-in{animation:none}}
`;
function injectCss() {
  if (document.getElementById("bl-style")) return;
  const s = document.createElement("style"); s.id = "bl-style"; s.textContent = CSS; document.head.append(s);
}
function skeleton(host) {
  injectCss();
  const rows = Array.from({ length: 9 }, (_, i) => `<div class="bl-sk-row"><i class="bl-sk bl-sk-dot"></i><span class="bl-sk-t"><i class="bl-sk bl-sk-a"></i><i class="bl-sk bl-sk-b" style="width:${[72, 54, 64, 48, 70, 58, 66, 50, 62][i]}%"></i></span></div>`).join("");
  const r = document.createElement("div");
  r.className = "bl-pre"; r.setAttribute("aria-busy", "true"); r.setAttribute("aria-label", "Loading the parts on this board");
  r.innerHTML = `<div class="bl-head"><i class="bl-sk bl-sk-search"></i><i class="bl-sk bl-sk-count"></i></div><div class="bl-list">${rows}</div>`;
  host.append(r);
}
(() => {
  const host = typeof document !== "undefined" && document.getElementById("paneBoard");
  if (host && !host.childElementCount) skeleton(host);
})();

export function createBoardList(host, { board, viewer, actions }) {
  injectCss();
  host.querySelectorAll(":scope > .bl-pre").forEach((n) => n.remove());
  const q = h("input", { type: "search", placeholder: "Find a part: U6, USB, 10k…", "aria-label": "Find a part on this board", autocomplete: "off" });
  const count = h("div", { class: "bl-count" });
  const list = h("div", { class: "bl-list", role: "listbox", "aria-multiselectable": "true", "aria-label": "Parts on this board" });
  host.append(h("div", { class: "bl-head" }, h("label", { class: "search", html: ic("search", 15) }, q), count), list);
  const open = new Set();
  let started = false;
  const rows = new Map();

  let first = true;
  function render() {
    list.textContent = ""; rows.clear();
    const fade = first; first = false;
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
      if (fade) head.classList.add("bl-in");
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
      if (fade) box.classList.add("bl-in");
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

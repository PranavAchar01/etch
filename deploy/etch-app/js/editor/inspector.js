// Inspector card (top-right of the canvas) for the selection: one part shows its refdes, value, footprint, side,
// position (mm from the board's top-left corner), rotation, lock / visibility toggles, a one-line "what it does"
// from Etch (asked once per part, cached), and Ask / Change / Hide / Isolate. Several parts show "Parts (n)".
import { h, esc } from "../common.js";
import { ic } from "./icons.js";

const firstSentence = (t) => String(t || "").replace(/`([^`]+)`/g, "$1").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/\s+/g, " ").trim().split(/(?<=[.!?])\s+/)[0] || "";

export function createInspector(el, { board, viewer, api, actions }) {
  const what = new Map(); // ref -> line | Promise
  const key = (ref) => `etch-what:${board.id}:${ref}`;
  let refs = [], whatT = 0;

  function whatItDoes(ref, line) {
    if (what.has(ref) && typeof what.get(ref) === "string") { line.textContent = what.get(ref); line.classList.remove("wait"); return; }
    try { const c = localStorage.getItem(key(ref)); if (c) { what.set(ref, c); line.textContent = c; return; } } catch (e) { /* not kept */ }
    line.textContent = "Asking Etch what it does…"; line.classList.add("wait");
    clearTimeout(whatT);
    // only once the selection has settled: every question counts against the hourly limit
    whatT = setTimeout(async () => {
      if (refs.length !== 1 || refs[0] !== ref) return;
      try {
        const r = await api.ask({ board: board.id, ref, question: "In one short sentence, what does this part do on this board?" });
        const s = firstSentence(r.answer);
        what.set(ref, s); try { localStorage.setItem(key(ref), s); } catch (e) { /* not kept */ }
        if (refs[0] === ref) { line.textContent = s; line.classList.remove("wait"); }
      } catch (e) { if (refs[0] === ref) { line.textContent = e.message; line.classList.remove("wait"); line.classList.add("err"); } }
    }, 450);
  }

  function pos(p) {
    const r = viewer.boardRect();
    if (!p?.pos_mm || !r) return "—";
    return `${(p.pos_mm[0] - r.x0).toFixed(2)}, ${(p.pos_mm[1] - r.y0).toFixed(2)} mm`;
  }
  const iconBtn = (name, label, on, f, cls = "") => h("button", { type: "button", class: "ib sm " + cls + (on ? " on" : ""), title: label, "aria-label": label, "aria-pressed": on ? "true" : "false", html: ic(name, 16), onclick: f });
  const act = (name, label, f, disabled, why) => h("button", { type: "button", class: "act lq-btn", disabled: disabled || null, title: why || label, onclick: f, html: `${ic(name, 15)}<span>${label}</span>` });

  function render() {
    el.textContent = "";
    if (!refs.length) { el.hidden = true; return; }
    el.hidden = false;
    const hidden = viewer.hidden, iso = viewer.isolated;
    const allHidden = refs.every((r) => hidden.has(r));
    const isoOn = !!iso && iso.length === refs.length && refs.every((r) => iso.includes(r));
    const locked = refs.every((r) => actions.isLocked(r));
    const why = actions.changeBlock();
    const head = h("div", { class: "in-head" },
      h("div", { class: "in-t" }, refs.length === 1 ? h("span", {}, h("b", {}, refs[0]), board.parts[refs[0]]?.value ? h("span", { class: "in-v" }, " · " + board.parts[refs[0]].value) : null) : h("b", {}, `Parts (${refs.length})`)),
      h("div", { class: "in-icons" },
        iconBtn(locked ? "lock" : "unlock", locked ? "Unlock" : "Lock", locked, () => { actions.lock(refs, !locked); render(); }),
        iconBtn(allHidden ? "eyeOff" : "eye", allHidden ? "Show" : "Hide", allHidden, () => { actions.hide(refs, !allHidden); render(); }),
        iconBtn("close", "Clear selection", false, () => actions.clear(), "x")));
    el.append(head);
    if (refs.length === 1) {
      const p = board.parts[refs[0]] || { ref: refs[0] };
      const line = h("div", { class: "in-what" });
      el.append(line);
      whatItDoes(refs[0], line);
      const rows = [["Footprint", String(p.footprint || "—").split(":").pop()], ["Side", p.side || "—"], ["Position", pos(p)], ["Rotation", p.rot != null ? `${Math.round(p.rot * 10) / 10}°` : "—"]];
      el.append(h("dl", { class: "in-rows" }, rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", { title: k === "Footprint" ? p.footprint || "" : null }, v)])));
    } else {
      const list = h("div", { class: "in-list" });
      for (const r of refs.slice(0, 18)) list.append(h("button", { type: "button", class: "chip", onclick: () => actions.select([r]) }, r));
      if (refs.length > 18) list.append(h("span", { class: "more" }, `+${refs.length - 18}`));
      el.append(list);
    }
    el.append(h("div", { class: "in-acts" },
      act("ask", "Ask", () => actions.ask(refs)),
      act("change", "Change", () => actions.change(refs), !!why || locked, why || (locked ? "Unlock the part to change it" : "")),
      act(allHidden ? "eye" : "eyeOff", allHidden ? "Show" : "Hide", () => { actions.hide(refs, !allHidden); render(); }),
      act("isolate", isoOn ? "Show all" : "Isolate", () => { actions.isolate(isoOn ? null : refs); render(); })));
    actions.liquid?.(el);
  }
  return {
    set(list) { refs = list || []; render(); },
    render,
    get refs() { return refs; },
  };
}
export { esc };

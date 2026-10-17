// Minimal stand-in for the agent dock (js/ui/dock.js), same API, plain DOM. Used only until the real dock lands:
// a pill with two eyes and a status line that opens (hover / focus) into a panel with the step trace, the answer,
// cards that need a decision, and the composer (mic + text).
import { createVoice } from "../voice.js";

const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const mdLite = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/\n{2,}/g, "<br><br>");

export function createDock(host, { onSend, onMention, partScreenPos } = {}) {
  const root = el("div", "dk"); root.dataset.state = "idle"; root.tabIndex = -1;
  const pill = el("div", "dk-pill");
  const eyes = el("div", "dk-eyes"); eyes.innerHTML = "<i></i><i></i>";
  const status = el("div", "dk-status", "Ready");
  const mic = el("button", "dk-mic"); mic.type = "button"; mic.title = "Talk to Etch (hold Space)"; mic.setAttribute("aria-label", "Talk to Etch");
  mic.innerHTML = '<svg width="16" height="16" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><rect x="6.5" y="2.5" width="5" height="8.5" rx="2.5"/><path d="M4 8.5a5 5 0 0 0 10 0M9 13.5v2.2"/></svg>';
  pill.append(eyes, status, mic);
  const panel = el("div", "dk-panel");
  const loaderEl = el("div", "dk-loader"); loaderEl.hidden = true;
  const steps = el("div", "dk-steps");
  const answers = el("div", "dk-answers");
  const cards = el("div", "dk-cards");
  const form = el("form", "dk-form");
  const input = el("input"); input.placeholder = "Ask Etch, or tell it what to change"; input.setAttribute("aria-label", "Message Etch"); input.maxLength = 400;
  const send = el("button", "dk-send", "Send"); send.type = "submit";
  form.append(input, send);
  panel.append(loaderEl, steps, cards, answers, form);
  root.append(panel, pill);
  host.append(root);

  let pinned = false, loaderT = 0, loaderStart = 0;
  const open = (on) => root.classList.toggle("open", on || pinned);
  root.addEventListener("mouseenter", () => open(true));
  root.addEventListener("mouseleave", () => { if (!root.contains(document.activeElement)) open(false); });
  root.addEventListener("focusin", () => open(true));
  root.addEventListener("focusout", () => setTimeout(() => { if (!root.contains(document.activeElement) && !root.matches(":hover")) open(false); }, 0));
  form.addEventListener("submit", (e) => { e.preventDefault(); const t = input.value.trim(); if (!t) return; input.value = ""; onSend?.(t, { via: "typed" }); });

  // eyes look at a part on screen, or loosely at the cursor
  let look = null;
  const aim = (x, y) => { const r = eyes.getBoundingClientRect(); const dx = x - (r.left + r.width / 2), dy = y - (r.top + r.height / 2), d = Math.hypot(dx, dy) || 1, k = Math.min(1, d / 300); eyes.style.setProperty("--ex", (dx / d * 2.2 * k).toFixed(2) + "px"); eyes.style.setProperty("--ey", (dy / d * 1.6 * k).toFixed(2) + "px"); };
  addEventListener("pointermove", (e) => { if (!look) aim(e.clientX, e.clientY); });
  function lookAt(ref) {
    look = ref;
    const p = ref && partScreenPos?.(ref);
    if (p) aim(p.x, p.y);
    clearTimeout(lookAt.t); lookAt.t = setTimeout(() => (look = null), 2500);
  }

  const stepEls = new Map();
  function step({ id, kind = "step", title, detail, status: st = "running", progress, count, ref } = {}) {
    let s = stepEls.get(id);
    if (!s) { s = el("div", "dk-step"); s.innerHTML = '<span class="dk-dot"></span><span class="dk-t"></span><span class="dk-n"></span><div class="dk-d"></div><div class="dk-bar"><i></i></div>'; steps.append(s); stepEls.set(id, s); while (steps.children.length > 14) { const f = steps.firstElementChild; stepEls.forEach((v, k) => v === f && stepEls.delete(k)); f.remove(); } }
    s.dataset.kind = kind; s.dataset.status = st;
    if (title != null) s.querySelector(".dk-t").textContent = title;
    if (detail != null) s.querySelector(".dk-d").textContent = detail;
    s.querySelector(".dk-n").textContent = count != null ? String(count) : "";
    s.querySelector(".dk-bar").hidden = progress == null;
    if (progress != null) s.querySelector(".dk-bar i").style.width = Math.round(Math.max(0, Math.min(1, progress)) * 100) + "%";
    if (ref) lookAt(ref);
    status.textContent = s.querySelector(".dk-t").textContent;
  }
  const ans = new Map();
  function bindRefs(box) {
    for (const b of box.querySelectorAll("[data-ref]")) {
      b.onmouseenter = () => { onMention?.(b.dataset.ref, { hover: true }); lookAt(b.dataset.ref); };
      b.onmouseleave = () => onMention?.(null, { hover: true });
      b.onclick = () => onMention?.(b.dataset.ref, { hover: false });
    }
  }
  const api = {
    setState(s) { root.dataset.state = s; },
    setStatus(t) { status.textContent = t; },
    setLevel(v) { root.style.setProperty("--lvl", String(Math.max(0, Math.min(1, v || 0)))); },
    step,
    loader(on, label) {
      clearInterval(loaderT); loaderEl.hidden = !on;
      if (on) { loaderStart = performance.now(); const tick = () => { loaderEl.textContent = `${label || "Working"} · ${((performance.now() - loaderStart) / 1000).toFixed(1)} s`; }; tick(); loaderT = setInterval(tick, 100); }
    },
    answerStart(id, { ref } = {}) { const a = el("div", "dk-ans"); a.dataset.ref = ref || ""; answers.prepend(a); while (answers.children.length > 3) answers.lastElementChild.remove(); ans.set(id, { el: a, text: "" }); if (ref) lookAt(ref); },
    answerAppend(id, chunk) { const a = ans.get(id); if (!a) return; a.text += chunk; a.el.innerHTML = mdLite(a.text); },
    answerEnd(id, { mentions = [], followups = [], seconds, thoughts = [] } = {}) {
      const a = ans.get(id); if (!a) return;
      let html = mdLite(a.text);
      for (const r of [...mentions].sort((x, y) => y.length - x.length)) html = html.replace(new RegExp(`(^|[^A-Za-z0-9_>-])(${r.replace(/[.*+?^${}()|[\]\\-]/g, "\\$&")})(?![A-Za-z0-9_-])`, "g"), `$1<button type="button" class="dk-ref" data-ref="${esc(r)}">$2</button>`);
      a.el.innerHTML = (thoughts.length ? `<div class="dk-meta">Worked for ${seconds ?? 0}s · ${esc(thoughts.join(" · "))}</div>` : "") + html;
      if (followups.length) { const f = el("div", "dk-follow"); for (const q of followups) { const b = el("button", "dk-chip", q); b.type = "button"; b.onclick = () => onSend?.(q, { via: "followup" }); f.append(b); } a.el.append(f); }
      bindRefs(a.el);
      if (mentions[0]) lookAt(mentions[0]);
    },
    ask({ id, question, options = [], multi = false }) {
      return new Promise((done) => {
        const c = el("div", "dk-card"); c.dataset.id = id || "";
        c.append(el("div", "dk-q", question));
        const row = el("div", "dk-opts"), picked = new Set();
        for (const o of options) {
          const label = typeof o === "string" ? o : o.label, val = typeof o === "string" ? o : o.id ?? o.label;
          const b = el("button", "dk-chip", label); b.type = "button";
          b.onclick = () => { if (!multi) { c.remove(); done(val); return; } picked.has(val) ? picked.delete(val) : picked.add(val); b.classList.toggle("on"); };
          row.append(b);
        }
        c.append(row);
        if (multi) { const ok = el("button", "dk-primary", "Done"); ok.type = "button"; ok.onclick = () => { c.remove(); done([...picked]); }; c.append(ok); }
        const skip = el("button", "dk-ghost", "Skip"); skip.type = "button"; skip.onclick = () => { c.remove(); done(null); }; c.append(skip);
        cards.append(c); pinned = true; open(true);
        const unpin = () => { if (!cards.children.length) { pinned = false; open(false); } };
        const mo = new MutationObserver(() => { if (!c.isConnected) { mo.disconnect(); unpin(); } }); mo.observe(cards, { childList: true });
      });
    },
    recommend({ id, title, detail, confidence = 0.7, actions = [] }) {
      return new Promise((done) => {
        const c = el("div", "dk-card rec"); c.dataset.id = id || "";
        c.append(el("div", "dk-q", title));
        if (detail) c.append(el("div", "dk-d", detail));
        const m = el("div", "dk-conf"); m.innerHTML = `<i style="width:${Math.round(confidence * 100)}%"></i><span>${Math.round(confidence * 100)}% sure</span>`; c.append(m);
        const row = el("div", "dk-opts");
        const acts = actions.length ? actions : [{ id: "accept", label: "Do it" }, { id: "cancel", label: "Not now" }];
        acts.forEach((a, i) => { const b = el("button", i ? "dk-ghost" : "dk-primary", a.label); b.type = "button"; b.onclick = () => { c.remove(); if (!cards.children.length) { pinned = false; open(false); } done(a.id); }; row.append(b); });
        c.append(row); cards.append(c); pinned = true; open(true);
      });
    },
    say(text) { status.textContent = String(text || "").slice(0, 140); voice.say(text); },
    listen() { if (voice.listening) voice.stop(); else voice.start(); },
    clear() { steps.textContent = ""; stepEls.clear(); answers.textContent = ""; ans.clear(); },
    // like the real dock: opening from code needs { force: true } (a click); closing always works
    expand(on, { force = false } = {}) { if (on && !force) return; pinned = !!on; open(!!on); },
    root,
  };
  // voice: the shared mic / STT / TTS logic; heard text goes out like typed text
  const voice = createVoice({
    onHeard: (t) => onSend?.(t, { via: "voice" }),
    onState: (s) => { if (s === "listening") api.setState("userTalking"); else if (s === "transcribing") api.setState("heard"); else if (s === "speaking") api.setState("speaking"); else if (s === "idle" && /userTalking|heard|speaking/.test(root.dataset.state)) api.setState("idle"); },
    onLevel: (_who, v) => api.setLevel(v),
    onError: (t) => { status.textContent = t; },
  });
  voice.bind(mic);
  return api;
}

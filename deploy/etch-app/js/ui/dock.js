// The helper: Etch's eyes, a small button at the bottom centre that stays quiet until it is clicked. It never speaks,
// pops a bubble, opens or emotes on its own: no step bubbles, no status chatter, no captions; build progress belongs
// to the build view (render loader, job pill). Idle, the eyes only blink and glance. Clicking them (or "/", or the
// focusable eyes button) melts the pill open into the helper panel: three starts ("I have a question about this",
// "... about that", "Can you change this?"), a free-text box and the mic. "This" is the selected part; with nothing
// selected, or for "that", the helper enters a pointing state (crosshair, eyes follow the pointer) until a part on the
// board is clicked, and the question or change request is sent with that ref. Replies stream into the panel only
// after the person asked. Escape, the x or a click outside puts it away; it stays closed until clicked again.
//
//   const dock = createDock(hostEl, { onSend(text, { via, ref, mode }), onMention(ref, { hover }), partScreenPos(ref),
//     selection() -> refs, partLabel(ref) -> "U6 · ESP32", pointable() -> bool })
//   mode is "ask" | "change" | null (free text: the page decides). The page calls dock.pointed(ref) when a part is
//   picked; it returns true when the helper was waiting for one.
//   see the method list at the bottom of createDock (open, close, request, help, pointed, setState, setStatus, step,
//   loader, answerStart, answerAppend, answerEnd, ask, recommend, say, listen, clear, expand, lookAt, emote).
// Everything that writes into the panel (step, answer*, say, cards) only lands in a turn the person started; say()
// is spoken aloud only when that turn was spoken. Cards (Accept a change, pick an option) open the panel, since they
// only come from something the person just did.
//
// Shapes live in one goo layer (pill, panel), content sits above it and follows the same springs. Every frame only
// writes clip-path / transform / opacity; layout is read once per change through ResizeObservers.
import { spring, tick, reducedMotion, onReducedMotion, clamp, lerp, smooth } from "./spring.js";
import { gooLayer, squish, wobble } from "./goo.js";
import { createEyes } from "./eyes.js";
import { createVoiceAdapter } from "./voice-adapter.js";
import { el, icon, pixelLoader, timeline, stagger, streamAnswer, approvalCard, recommendCard, bindMentions, fmtSecs } from "./trace.js";

const OPEN_SPRING = { stiffness: 250, damping: 21 };
const PILL_H = 56, GAP = 8, EYES_W = 46, MIC = 40, PAD = 8, SHUT_W = PAD * 2 + EYES_W;
const STATES = new Set(["idle", "userTalking", "heard", "thinking", "working", "speaking", "success", "confused", "error", "offline"]);
const VOICE = new Set(["userTalking", "heard", "speaking"]);
const PLACEHOLDER = "Ask me anything, or describe a change";
const CHANGES = ["Swap it for an equivalent part", "Move it closer to the board edge", "Remove it"];
const plain = (s) => String(s || "").replace(/`([^`]+)`/g, "$1").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/\s+/g, " ").trim();

// what a step is, as an eyes activity (only acted out while the panel is open)
const ACTS = [
  [/wait|queue|in line/i, "wait"], [/screen|search|scan|looking for|find/i, "scan"], [/repair|fix/i, "repair"],
  [/check|gate|verif|test|parity|benchmark|inspect|\b(erc|drc)\b/i, "inspect"], [/rout|plac|track|layout/i, "route"],
  [/render|3d/i, "render"], [/export|writ|gerber|fab file|zip|sav|copy/i, "export"], [/pick|choos|select|gather|using \d|adding/i, "pick"],
  [/read|open|load|parts\.json|look/i, "read"],
];
function activityOf(d) {
  if (d.kind === "reasoning") return "think";
  for (const [re, a] of ACTS) if (re.test(String(d.title || ""))) return a;
  return "think";
}

export function createDock(hostEl, opts = {}) {
  const host = hostEl || document.body;
  let W = 0, MAXH = 0;

  // ---------- DOM ----------
  const root = el("div", "ag-dock");
  root.dataset.open = "false";
  const goo = el("div", "ag-dock-goo");
  goo.setAttribute("aria-hidden", "true");
  const panelBlob = el("div", "ag-blob ag-blob-panel"), pillBlob = el("div", "ag-blob ag-blob-pill");
  goo.append(panelBlob, pillBlob);
  gooLayer(goo, { id: "ag-goo-dock", blur: 9, alpha: 22, shift: -10, after: "drop-shadow(0 10px 22px rgba(22,28,45,.13)) drop-shadow(0 1px 2px rgba(22,28,45,.12))" });

  const phLine = el("div", "ag-ph-line");
  const phLoader = pixelLoader({ size: "sm" });
  const header = el("div", "ag-ph", phLine, phLoader.el);
  const headWrap = el("div", "ag-phw", header);
  const turnsEl = el("div", "ag-turns");
  const emptyT = el("div", "ag-empty-t"), emptyD = el("div", "ag-empty-d");
  const empty = el("div", "ag-empty", emptyT, emptyD);
  const scroll = el("div", "ag-scroll", empty, turnsEl);
  const newPill = el("button", "ag-newpill lq-btn");
  newPill.type = "button"; newPill.hidden = true;

  // the helper's context bar: the pointing state, what "this" is, the three starts, quick changes
  const startBtn = (label, kind) => { const b = el("button", "ag-start lq-btn", label); b.type = "button"; b.dataset.kind = kind; squish(b); return b; };
  const sThis = startBtn("I have a question about this", "this");
  const sThat = startBtn("I have a question about that", "that");
  const sChange = startBtn("Can you change this?", "change");
  const starts = el("div", "ag-starts", sThis, sThat, sChange);
  const pointCancel = el("button", "ag-point-x lq-btn", "Cancel");
  pointCancel.type = "button";
  const pointT = el("span", "ag-point-t");
  const pointBar = el("div", "ag-pointing", el("span", "ag-point-dot"), pointT, pointCancel);
  pointBar.setAttribute("role", "status");
  const targetT = el("span", "ag-target-t");
  const targetX = el("button", "ag-target-x", icon("close"));
  targetX.type = "button"; targetX.setAttribute("aria-label", "Stop pointing at this part");
  const targetEl = el("div", "ag-target", el("span", "ag-target-k", "About"), targetT, targetX);
  const sugg = el("div", "ag-sugg", ...CHANGES.map((c) => { const b = el("button", "ag-follow lq-btn", c); b.type = "button"; b.dataset.change = c; return b; }));
  const ctx = el("div", "ag-ctx", pointBar, targetEl, sugg, starts);

  const inner = el("div", "ag-panel-in", headWrap, scroll, newPill, ctx);
  const panel = el("div", "ag-panel", inner);
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Etch helper");
  panel.inert = true;

  // the eyes are the button that summons the helper
  const eyesWrap = el("button", "ag-pill-eyes ag-summon");
  eyesWrap.type = "button";
  eyesWrap.setAttribute("aria-label", "Ask Etch for help (press /)");
  eyesWrap.setAttribute("aria-expanded", "false");
  eyesWrap.title = "Ask Etch · press /";
  const eyes = createEyes(eyesWrap);
  const statusText = el("span", "ag-status-t");
  // while listening: a small live waveform of the mic level next to "Listening…" (only after the person asked to talk)
  const wave = el("span", "ag-wave");
  wave.setAttribute("aria-hidden", "true");
  const waveBars = Array.from({ length: 5 }, () => el("i"));
  wave.append(...waveBars);
  const status = el("div", "ag-status", wave, statusText);
  status.setAttribute("aria-live", "polite");
  const input = el("input", "ag-input");
  input.type = "text"; input.placeholder = PLACEHOLDER; input.setAttribute("aria-label", "Message Etch"); input.maxLength = 400;
  input.autocomplete = "off";
  const send = el("button", "ag-send lq-btn", icon("send"));
  send.type = "submit"; send.setAttribute("aria-label", "Send");
  const form = el("form", "ag-compose", input, send);
  const mic = el("button", "ag-mic lq-btn", icon("mic"));
  mic.type = "button"; mic.setAttribute("aria-label", "Talk to Etch (or hold Space)");
  const pill = el("div", "ag-pill", eyesWrap, status, mic, form);
  const closeBtn = el("button", "ag-x lq-btn", icon("close"));
  closeBtn.type = "button"; closeBtn.setAttribute("aria-label", "Close the helper"); closeBtn.title = "Close · Esc";
  inner.append(closeBtn); squish(closeBtn);
  root.append(goo, pill, panel);
  host.append(root);
  squish(send); squish(mic);
  form.inert = mic.inert = true;

  // ---------- springs ----------
  const o = spring(0, { preset: OPEN_SPRING, precision: 0.001, onUpdate: draw });
  const pw = spring(SHUT_W, { preset: { stiffness: 300, damping: 22 }, precision: 0.05, onUpdate: draw });
  const hs = spring(140, { preset: { stiffness: 260, damping: 26 }, precision: 0.05, onUpdate: draw });
  const pk = spring({ x: 1, y: 1 }, { preset: "wobbly", precision: 0.0008, onUpdate: (v) => { const s = v.x === 1 && v.y === 1 ? "" : `${v.x} ${v.y}`; pillBlob.style.scale = s; eyesWrap.style.scale = s; } });
  let collapsedW = SHUT_W;

  function draw() {
    const ov = clamp(o.value, 0, 1.08), oc = clamp(ov);
    const ph = Math.min(MAXH, hs.value) * oc;
    if (ph < 1) { panelBlob.style.clipPath = panel.style.clipPath = "inset(50% 50% 50% 50%)"; }
    else {
      const visW = lerp(Math.min(collapsedW, W) * 0.62, W, oc) * (ov > 1 ? 1 + (ov - 1) * 0.15 : 1);
      const side = Math.max(0, (W - visW) / 2);
      const c = `inset(${(MAXH - ph).toFixed(1)}px ${side.toFixed(1)}px 0px ${side.toFixed(1)}px round 22px)`;
      panelBlob.style.clipPath = c; panel.style.clipPath = c;
    }
    const a = smooth(0.45, 1, oc);
    inner.style.opacity = a.toFixed(3);
    inner.style.transform = `translateY(${((1 - oc) * 12).toFixed(1)}px)`;
    const p = clamp(pw.value, SHUT_W - 6, W), side = (W - p) / 2;
    pillBlob.style.clipPath = pill.style.clipPath = `inset(0px ${side.toFixed(1)}px round ${PILL_H / 2}px)`;
    eyesWrap.style.transform = `translateX(${(side + PAD).toFixed(1)}px)`;
    status.style.transform = `translateX(${(side + PAD + EYES_W + 8).toFixed(1)}px)`;
    status.style.opacity = (1 - smooth(0.02, 0.4, oc)).toFixed(3);
    form.style.opacity = smooth(0.5, 1, oc).toFixed(3);
    mic.style.opacity = smooth(0.35, 1, oc).toFixed(3);
    mic.style.transform = `translateX(${(W - side - PAD - MIC - oc * 2).toFixed(1)}px) scale(${lerp(0.7, 1.1, oc).toFixed(3)})`;
  }

  function layout() {
    W = Math.round(Math.min(460, innerWidth - 24));
    // phones: a bottom sheet of at most ~45% of the screen, so the workplane stays in view
    MAXH = Math.round(innerWidth <= 768 ? clamp(innerHeight * 0.45 - PILL_H - GAP, 160, 540) : clamp(innerHeight - 170, 180, 540));
    root.style.width = W + "px";
    root.style.height = MAXH + GAP + PILL_H + "px";
    panelBlob.style.height = panel.style.height = MAXH + "px";
    inner.style.maxHeight = MAXH + "px";
    sizeCollapsed();
    pw.jump(open ? W : collapsedW);
    draw();
  }
  // shut, the pill is just the eyes; it only widens for "Listening…" after the person started talking
  function sizeCollapsed() {
    const room = W - (PAD * 2 + EYES_W + 8 + 12);
    if (status.style.maxWidth !== room + "px") status.style.maxWidth = room + "px";
    const t = statusText.textContent;
    const sw = t ? Math.min(status.offsetWidth || 120, room) : 0;
    collapsedW = Math.round(t ? clamp(PAD + EYES_W + 8 + sw + 14 + PAD, SHUT_W, W) : SHUT_W);
    if (!open) pw.set(collapsedW);
  }

  // ---------- open / close: only the person does either ----------
  let open = false;
  function setOpen(v, { focus = true } = {}) {
    v = !!v;
    if (v === open) { if (v && focus) focusInput(); return; }
    open = v;
    root.dataset.open = String(open);
    eyesWrap.setAttribute("aria-expanded", String(open));
    panel.inert = !open; form.inert = !open; mic.inert = !open;
    o.config(OPEN_SPRING).set(open ? 1 : 0);
    if (!open) { endPoint(); sizeCollapsed(); if (root.contains(document.activeElement)) document.activeElement.blur(); }
    else { paintHelp(); stick(true); if (focus) focusInput(); }
    pw.set(open ? W : collapsedW);
    wobblePill(open ? 0.5 : 0.6);
    paintState(); paintActivity(); retarget();
  }
  const focusInput = () => setTimeout(() => { if (open && !pointing) input.focus({ preventScroll: true }); }, 30);
  eyesWrap.addEventListener("click", (e) => { e.stopPropagation(); setOpen(!open); });
  closeBtn.addEventListener("click", (e) => { e.stopPropagation(); setOpen(false); });
  // Escape: first out of pointing, then the helper away
  const onEsc = (e) => {
    if (e.key !== "Escape" || !open || e.defaultPrevented) return;
    const a = document.activeElement;
    if (a && a !== document.body && !root.contains(a)) return;
    e.preventDefault();
    if (pointing) { endPoint(); focusInput(); } else setOpen(false);
  };
  document.addEventListener("keydown", onEsc);
  // a click anywhere outside the helper puts it away (except while it waits for a part to be pointed at)
  const outside = (e) => { if (open && !pointing && !root.contains(e.target)) setOpen(false); };
  document.addEventListener("pointerdown", outside, true);
  // "/" anywhere outside a text field summons it
  const isTyping = () => { const a = document.activeElement; return !!a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT" || a.isContentEditable); };
  const onSlash = (e) => { if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !isTyping()) { e.preventDefault(); setOpen(true); } };
  document.addEventListener("keydown", onSlash);

  function wobblePill(s = 1) { pk.impulse({ x: 0.9 * s, y: -1.4 * s }); }

  // ---------- "this", "that" and pointing ----------
  let mode = null, target = null, pointing = null;
  const selRef = () => { const s = opts.selection?.() || []; return s.length ? s[0] : null; };
  const label = (r) => (opts.partLabel ? opts.partLabel(r) : r) || r;
  const canPoint = () => (opts.pointable ? !!opts.pointable() : true);
  function paintHelp() {
    const pt = canPoint();
    emptyT.textContent = pt ? "Hi, what can I help with?" : "Hi, what should we build?";
    emptyD.textContent = pt ? "Pick a part, or point at one on the board, and ask me anything about it. I can change it for you too."
      : "Tell me what your board should do and I'll build it. Ask me anything along the way.";
    starts.hidden = !pt || !!pointing;
    const s = selRef();
    sThis.title = s ? `Ask about ${label(s)}` : "Point at a part, then ask";
    sChange.title = s ? `Change ${label(s)}` : "Point at a part, then say what to change";
    pointBar.hidden = !pointing;
    pointT.textContent = pointing === "change" ? "Click the part you want changed" : "Click a part on the board to point at it";
    targetEl.hidden = !target || !!pointing;
    if (target) targetT.textContent = label(target);
    sugg.hidden = mode !== "change" || !target || !!pointing;
    for (const b of [sThis, sThat, sChange]) b.setAttribute("aria-pressed", String(!pointing && !!target && ((b === sChange) === (mode === "change")) && mode != null));
    input.placeholder = pointing ? "Pointing…" : mode === "ask" && target ? `Ask about ${target}, or press Enter: what does it do?`
      : mode === "change" && target ? `What should I change about ${target}?` : target ? `Ask about ${target}, or describe a change` : PLACEHOLDER;
  }
  function setTarget(ref, m) {
    target = ref || null; mode = target ? m : null;
    lookRef = target; retarget();
    paintHelp(); focusInput();
  }
  // "this": the selection, or point at it first; "that": always point
  function help(kind) {
    setOpen(true, { focus: false });
    if (!canPoint()) { focusInput(); return; }
    const m = kind === "change" ? "change" : "ask";
    const s = kind === "that" ? null : selRef();
    if (s) setTarget(s, m); else point(m);
  }
  starts.addEventListener("click", (e) => { const b = e.target.closest("[data-kind]"); if (b) help(b.dataset.kind); });
  const followPointer = (e) => eyes.lookAt(e.clientX, e.clientY);
  function point(m) {
    pointing = m;
    root.classList.add("pointing"); document.body.classList.add("ag-pointing");
    document.addEventListener("pointermove", followPointer, { passive: true });
    if (root.contains(document.activeElement)) document.activeElement.blur();
    eyes.react("tilt");
    retarget(); paintHelp();
  }
  function endPoint() {
    if (!pointing) return;
    pointing = null;
    root.classList.remove("pointing"); document.body.classList.remove("ag-pointing");
    document.removeEventListener("pointermove", followPointer);
    eyes.lookAt(null); retarget(); paintHelp();
  }
  pointCancel.addEventListener("click", () => { endPoint(); focusInput(); });
  targetX.addEventListener("click", () => { target = null; mode = null; lookRef = null; retarget(); paintHelp(); focusInput(); });
  sugg.addEventListener("click", (e) => {
    const b = e.target.closest("[data-change]"); if (!b || !target) return;
    const c = b.dataset.change, r = target;
    doSend(/^remove/i.test(c) ? `Remove ${r}` : /^move/i.test(c) ? `Move ${r} closer to the board edge` : `Swap ${r} for an equivalent part`, "typed", "change");
  });

  // ---------- size tracking ----------
  const ro = new ResizeObserver(() => { hs.set(inner.offsetHeight || 140); sizeCollapsed(); });
  ro.observe(inner); ro.observe(status);
  // The timeline follows new items only while the reader is at the bottom (within 40 px). Scrolled up, they stay put
  // and a "N new updates" pill counts what arrived below. Rows updated in place keep their scroll anchor.
  let stickRaf = 0, pinned = true, unseen = 0;
  const atBottom = () => scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight <= 40;
  function toBottom() { scroll.scrollTop = scroll.scrollHeight; pinned = true; unseen = 0; paintPill(); }
  function paintPill() {
    newPill.hidden = !unseen;
    if (unseen) newPill.replaceChildren(`${unseen} new update${unseen === 1 ? "" : "s"}`, icon("chev"));
  }
  scroll.addEventListener("scroll", () => { pinned = atBottom(); if (pinned && unseen) { unseen = 0; paintPill(); } }, { passive: true });
  newPill.addEventListener("click", () => { scroll.scrollTo({ top: scroll.scrollHeight, behavior: reducedMotion() ? "auto" : "smooth" }); unseen = 0; pinned = true; paintPill(); });
  function stick(force, fresh = false) {
    if (force) pinned = true;
    else if (!pinned && fresh) { unseen++; paintPill(); }
    if (stickRaf || !pinned) return;
    stickRaf = requestAnimationFrame(() => { stickRaf = 0; if (pinned) toBottom(); });
  }
  new ResizeObserver(() => {
    if (!pinned && atBottom()) { pinned = true; unseen = 0; paintPill(); }
    if (pinned && open) stick();
  }).observe(turnsEl);

  // ---------- state: the eyes only act it out while the helper is open ----------
  let base = "idle", overlay = null, overlayT = 0, statusBase = "", statusTemp = "", tempT = 0;
  let awaitingVoice = false, speakDone = [];
  const SAD = new Set(["confused", "error", "offline"]);
  function paintState() {
    let s = overlay || base;
    if (s === "speaking" && SAD.has(base)) s = base;
    else if (s === "speaking" && awaitingVoice) s = "thinking";
    // shut, the eyes stay calm (blinks and glances); only the person's own voice turn shows
    if (!open && !(overlay && VOICE.has(overlay))) s = "idle";
    eyes.setState(s);
    root.dataset.state = s;
  }
  function setOverlay(s, ms = 0) {
    clearTimeout(overlayT);
    overlay = s; paintState();
    if (s && ms) overlayT = setTimeout(() => { overlay = null; paintState(); }, ms);
  }
  // the pill's text line is only ever the person's own voice turn ("Listening…", "Got it", a mic problem)
  function paintStatus() {
    const t = statusTemp;
    if (statusText.textContent === t) return;
    statusText.textContent = t;
    statusText.title = t;
    root.classList.toggle("voice", !!t);
    statusText.classList.remove("ag-in"); void statusText.offsetWidth; statusText.classList.add("ag-in");
    sizeCollapsed();
  }
  function tempStatus(t, ms) { statusTemp = t; paintStatus(); clearTimeout(tempT); if (ms) tempT = setTimeout(() => { if (statusTemp === t) { statusTemp = ""; paintStatus(); } }, ms); }
  // the panel's headline is what the person's request is doing ("Looking at J1"); the live row alone carries the loader
  let curLine = "", loaderLabel = "", loading = false, liveR = null;
  function paintLine() {
    const t = statusBase || (loading && !liveR ? loaderLabel : "") || curLine || "";
    if (phLine.textContent !== t) { phLine.textContent = t; phLine.title = t; }
    const showLoader = loading && !liveR;
    if (showLoader && !phLoader.running) phLoader.start(); else if (!showLoader && phLoader.running) phLoader.stop();
    header.hidden = !t && !showLoader;
  }
  function pickLive() {
    let r = null;
    if (turn) for (const tl of turn.tls) for (const x of tl.rows.values()) if (x.data.status === "running" && !x.dupOf && x.el.isConnected) r = x;
    if (r !== liveR) {
      if (liveR) liveR.live(false);
      liveR = r;
      if (r) r.live(true);
      paintLine();
    }
    paintActivity();
  }
  function paintActivity() {
    let a = null;
    if (liveR) a = activityOf(liveR.data);
    else if (loading && /wait|line|queue/i.test(loaderLabel)) a = "wait";
    else if (base === "thinking" || base === "working") a = "think";
    eyes.setActivity(open ? a : null);
  }
  // one-shot looks only while the person is looking at the helper
  const fx = (f) => { if (open) f(); };

  // ---------- eyes follow parts ----------
  let hoverRef = null, lookRef = null, clickRef = null, clickT = 0, answerRef = null, eyeJob = null;
  function refNow() {
    if (pointing) return null;
    if (hoverRef) return hoverRef;
    if (lookRef) return lookRef;
    if (clickRef) return clickRef;
    let r = null;
    for (const s of steps.values()) if (s.data.status === "running" && s.data.ref) r = s.data.ref;
    return r || answerRef;
  }
  function retarget() {
    if (eyeJob || !opts.partScreenPos) { if (!opts.partScreenPos) eyes.lookAt(null); return; }
    eyeJob = tick(() => {
      const r = refNow();
      if (!r) { if (!pointing) eyes.lookAt(null); eyeJob = null; return false; }
      let p = null;
      try { p = opts.partScreenPos(r); } catch (e) { p = null; }
      if (p && isFinite(p.x) && isFinite(p.y)) eyes.lookAt(p.x, p.y); else eyes.lookAt(null);
      return true;
    });
  }
  bindMentions(root, (ref, m) => {
    if (m.hover === true) hoverRef = ref;
    else if (m.hover === false) { if (hoverRef === ref) hoverRef = null; }
    else { clickRef = ref; clearTimeout(clickT); clickT = setTimeout(() => { clickRef = null; retarget(); }, 1800); }
    retarget();
    if (opts.onMention) opts.onMention(ref, m.hover === undefined ? {} : { hover: m.hover });
  });

  // ---------- turns: one per thing the person asked ----------
  const steps = new Map(), answers = new Map(), cards = new Map();
  let turn = null;
  function fold(t, seconds) { if (t) for (const tl of t.tls) if (!tl.finished && !tl.running) tl.done(tl === t.tl ? seconds : undefined); }
  function newTurn(text, via, ref) {
    if (turn) { turn.el.classList.add("old"); fold(turn); }
    const t = { el: el("div", "ag-turn lq-in"), tl: null, tls: [], user: !!text, via };
    if (text) {
      const u = el("div", "ag-you", el("span", "ag-you-t", text));
      if (ref && !new RegExp(`\\b${ref}\\b`).test(text)) u.prepend(el("span", "ag-you-ref", ref));
      if (via === "voice") u.prepend(icon("mic", "ag-you-ic"));
      t.el.append(u);
    }
    t.el.addEventListener("click", (e) => {
      if (t.el.classList.contains("old") && !t.el.classList.contains("full") && !e.target.closest("button, a, input")) t.el.classList.add("full");
    });
    turnsEl.append(t.el);
    empty.hidden = true;
    while (turnsEl.children.length > 8) turnsEl.firstChild.remove();
    turn = t;
    pickLive();
    stick(!!text, true);
    return t;
  }
  const cur = () => turn || newTurn();
  function tlOf(t) {
    if (!t.tl) { t.tl = timeline({ verb: t.user ? "Thought for" : "Ready in" }); t.el.append(t.tl.el); t.tls.push(t.tl); }
    return t.tl;
  }
  function place(t, node) { if (t.tl && !t.tl.running) t.tl.done(); t.tl = null; stagger(node); t.el.append(node); stick(false, true); }
  function doSend(text, via, m = mode) {
    text = String(text || "").trim();
    if (!text) return;
    const ref = target;
    newTurn(text, via, ref);
    curLine = ""; statusBase = "";
    paintLine();
    mode = null; paintHelp();
    if (opts.onSend) opts.onSend(text, { via, ref, mode: m });
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    let t = input.value.trim(); input.value = "";
    if (!t && mode === "ask" && target) t = `What does ${target} do on this board?`;
    doSend(t, "typed");
  });

  // ---------- voice: only ever started by the person ----------
  let spaceWaiting = false;
  const waveHist = [0, 0, 0, 0, 0];
  const voice = createVoiceAdapter({
    onState(s, d) {
      if (s === "listening") {
        setOverlay("userTalking"); tempStatus("Listening…"); mic.classList.add("rec"); root.classList.add("listening"); input.placeholder = "Listening…";
        if (spaceWaiting) { spaceWaiting = false; dispatchEvent(new KeyboardEvent("keyup", { code: "Space", key: " " })); }
      } else {
        mic.classList.remove("rec"); root.classList.remove("listening"); paintHelp(); waveBars.forEach((b) => (b.style.transform = ""));
        if (s === "transcribing") { setOverlay("heard"); tempStatus("Got it…"); }
        else if (s === "speaking") { setOverlay("speaking"); tempStatus(""); }
        else if (s === "idle") { if (overlay === "heard") setOverlay("heard", 1100); else setOverlay(null); if (statusTemp !== "Got it") tempStatus(""); eyes.setLevel(0); }
        else if (s === "off") { setOverlay(null); tempStatus(d ? plain(d) : "", d ? 3200 : 0); }
      }
      if (s !== "speaking") { awaitingVoice = false; if (s === "idle" || s === "off" || s === "listening") { const f = speakDone; speakDone = []; f.forEach((r) => r()); } paintState(); }
    },
    onVadEnd() { setOverlay("heard", 1100); tempStatus("Got it", 900); eyes.react("nod"); },
    onLevel(who, x) {
      if (who === "etch" && x > 0.01 && awaitingVoice) { awaitingVoice = false; paintState(); }
      if (who === "you" && root.classList.contains("listening")) {
        waveHist.push(clamp(x)); waveHist.shift();
        waveBars.forEach((b, i) => { b.style.transform = `scaleY(${(0.18 + 0.82 * waveHist[i]).toFixed(3)})`; });
      }
      if ((who === "you" && overlay === "userTalking") || (who === "etch" && overlay === "speaking")) eyes.setLevel(x);
      mic.style.setProperty("--lv", who === "you" ? clamp(x).toFixed(3) : "0");
    },
    onHeard(text) { setOverlay("heard", 1100); tempStatus("Got it", 900); doSend(text, "voice"); },
    onInterim(text) { tempStatus(text ? `“${text.length > 48 ? "…" + text.slice(-46) : text}”` : "Listening…"); },
    // what the voice layer would caption is already in the panel; a mic problem shows briefly on the pill
    onCaption() {},
    onError(line) { eyes.react("squish"); tempStatus(plain(line), 3200); },
  });
  mic.addEventListener("pointerdown", (e) => { if (e.button !== 0) return; e.preventDefault(); voice.toggle(); });
  mic.addEventListener("pointerup", () => voice.release());
  mic.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); if (!e.repeat) voice.toggle(); } });
  mic.addEventListener("keyup", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); } });

  // Space (outside text fields and other controls) is push-to-talk while the helper is open. Space on a focused
  // control keeps its normal meaning and does not reach voice.js's window listener.
  const onControl = () => { const a = document.activeElement; return !!a && a !== document.body && !!a.closest("button, a[href], summary, [role=button], [role=tab], [role=radio], [role=checkbox], [role=switch], [role=option], [role=menuitem]"); };
  const kd = (e) => {
    if (e.code !== "Space" || isTyping()) return;
    if (onControl()) { e.stopPropagation(); return; }
    if (!open) return;
    e.preventDefault();
    if (e.repeat) return;
    if (!voice.created) { spaceWaiting = false; voice.start({ held: true }); }
  };
  const ku = (e) => {
    if (e.code !== "Space" || isTyping()) return;
    if (onControl()) { e.stopPropagation(); return; }
    if (!open) return;
    if (!voice.listening) spaceWaiting = true;
    setTimeout(() => { spaceWaiting = false; }, 1500);
  };
  document.addEventListener("keydown", kd);
  document.addEventListener("keyup", ku);

  // ---------- reduced motion ----------
  const paintRM = () => root.classList.toggle("rm", reducedMotion());
  const offRM = onReducedMotion(paintRM);
  paintRM();
  const onResize = () => layout();
  addEventListener("resize", onResize, { passive: true });
  layout();
  paintState(); paintLine(); paintHelp();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { sizeCollapsed(); draw(); });

  // ---------- public API ----------
  const api = {
    el: root,
    eyes,
    get open() { return open; },
    get pointing() { return !!pointing; },
    get state() { return overlay || base; },

    // the person's own actions (a click, a key, a button elsewhere on the page)
    open() { setOpen(true); },
    close() { setOpen(false); },
    help,
    // a request the page started for the person (Delete, a dropped part, the inspector's Ask): their turn in the panel
    request(text, { ref = null, via = "action" } = {}) { setOpen(true, { focus: false }); newTurn(text, via, ref); curLine = ""; statusBase = ""; paintLine(); },
    // a part was picked on the board: true when the helper was waiting for one
    pointed(ref) {
      if (!pointing || !ref) return false;
      const m = pointing;
      endPoint();
      setTarget(ref, m);
      eyes.react("nod");
      return true;
    },

    setState(s) {
      if (!STATES.has(s)) return;
      base = s;
      if (/^(idle|success|confused|error|offline)$/.test(s)) setTimeout(() => { if (base === s) fold(turn); }, 300);
      if (s === "success") fx(() => { eyes.emote("delight"); wobblePill(1); });
      paintActivity();
      if (overlay === "heard" && s !== "idle") setOverlay(null);
      paintState();
    },
    setStatus(text) { if (!turn) return; statusBase = plain(text); paintLine(); },
    emote(kind) { fx(() => { eyes.emote(kind); if (kind === "hop" || kind === "delight") wobblePill(0.8); }); },
    setLevel(v) { eyes.setLevel(v); },

    step(d) {
      if (!d || d.id == null || !turn) return;
      const kind = d.kind === "coding" ? "tool" : d.kind || "step";
      const data = { ...d, kind, status: d.status || "running" };
      let s = steps.get(d.id);
      const was = s ? s.data.status : null;
      if (s) { s.data = { ...s.data, ...data }; s.tl.row(s.data); }
      else {
        const tl = tlOf(cur());
        s = { data, tl };
        steps.set(d.id, s);
        tl.row(data);
        stick(false, true);
      }
      const st = s.data.status;
      if (st === "failed" && was !== "failed") fx(() => eyes.emote("wince"));
      else if (st === "done" && was === "running" && (s.data.kind === "task" || /\bpassed\b/i.test(s.data.title || ""))) fx(() => eyes.react("nod"));
      if (was === "running" && st !== "running") setTimeout(pickLive, 250); else pickLive();
      retarget(); stick();
    },

    loader(on, label) {
      if (on) { loading = true; loaderLabel = String(label || "Working").replace(/\s*·\s*[\d.]+\s*s$/, ""); root.classList.add("loading"); }
      else { loading = false; loaderLabel = ""; root.classList.remove("loading"); }
      paintLine(); paintActivity();
    },

    answerStart(id, { ref } = {}) {
      const t = cur();
      t.hadTl = t.tls.length > 0;
      if (t.tl) { const tl = t.tl; t.tl = null; if (!tl.running) tl.done(); }
      const a = streamAnswer({ ref, onFollowup: (f) => doSend(f, "followup", "ask") });
      place(t, a.el);
      answers.set(id, { a });
      answerRef = ref || null;
      retarget(); stick();
      return a;
    },
    answerAppend(id, chunk) {
      let x = answers.get(id);
      if (!x) { api.answerStart(id); x = answers.get(id); }
      x.a.append(chunk);
      stick();
    },
    answerEnd(id, info = {}) {
      const x = answers.get(id);
      if (!x) return;
      const t = cur();
      x.a.end(t.hadTl ? { ...info, seconds: null, thoughts: null } : info);
      for (const tl of t.tls) if (!tl.finished && !tl.running) tl.done(info.seconds);
      fx(() => eyes.emote("pleased"));
      setTimeout(() => { if (answerRef && answers.get(id)) { answerRef = null; retarget(); } }, 2600);
      stick();
    },

    // a decision the person's own request needs: it opens the helper on it
    ask({ id, question, options = [], multi = false } = {}) {
      const key = id ?? "ask:" + Math.random();
      if (cards.has(key)) cards.get(key).cancel();
      const c = approvalCard({ question, options, multi });
      place(cur(), c.el);
      cards.set(key, c);
      setOpen(true, { focus: false });
      c.promise.finally(() => { if (cards.get(key) === c) cards.delete(key); });
      return c.promise;
    },
    recommend({ id, title, detail, confidence, actions } = {}) {
      const key = id ?? "rec:" + Math.random();
      if (cards.has(key)) cards.get(key).cancel();
      const c = recommendCard({ title, detail, confidence, actions });
      place(cur(), c.el);
      cards.set(key, c);
      setOpen(true, { focus: false });
      c.promise.finally(() => { if (cards.get(key) === c) cards.delete(key); });
      return c.promise;
    },

    // a reply line in the person's current turn; spoken aloud only when they spoke the request. note: false speaks
    // without writing it again (an answer that is already in the panel). Outside a turn it goes nowhere.
    say(text, { note = true } = {}) {
      const t = plain(text);
      if (!t || !turn) return Promise.resolve();
      if (note) place(turn, el("div", "ag-say", t));
      if (turn.via !== "voice") return Promise.resolve();
      const spoken = voice.say(t, { force: true });
      if (!spoken) return Promise.resolve();
      awaitingVoice = true; paintState();
      return new Promise((r) => { const tm = setTimeout(r, 60000); speakDone.push(() => { clearTimeout(tm); r(); }); });
    },
    // the person asked to talk (the start screen's mic): listen, and show it on the pill
    listen() { voice.start({ auto: true }); },

    clear() {
      for (const c of cards.values()) c.cancel();
      cards.clear(); steps.clear(); answers.clear();
      turnsEl.replaceChildren(); turn = null; empty.hidden = false; liveR = null; unseen = 0; paintPill();
      api.loader(false);
      answerRef = lookRef = hoverRef = clickRef = null; target = null; mode = null; endPoint(); retarget();
      curLine = ""; statusBase = ""; tempStatus(""); paintLine(); paintHelp();
      base = "idle"; setOverlay(null);
    },
    // expand(true) only for an explicit user action: expand(true, { force: true })
    expand(v, { force = false } = {}) { if (v && !force) return; setOpen(!!v); },
    // hold the eyes on a part (partScreenPos is polled each frame while set); null lets go
    lookAt(ref) { lookRef = ref || target || null; retarget(); },

    destroy() {
      for (const c of cards.values()) c.cancel();
      voice.stop(); offRM(); ro.disconnect(); endPoint();
      document.removeEventListener("keydown", kd); document.removeEventListener("keyup", ku);
      document.removeEventListener("pointerdown", outside, true); document.removeEventListener("keydown", onEsc); document.removeEventListener("keydown", onSlash);
      removeEventListener("resize", onResize);
      eyes.destroy(); root.remove();
    },
  };
  // a little wobble when the board changes under an open helper
  api.nudge = () => fx(() => { wobblePill(1); wobble(eyesWrap, 0.4); });
  return api;
}

export { fmtSecs };

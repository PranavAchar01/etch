// The agent dock: a floating pill at the bottom centre with Etch's eyes, a mic and a one-line status. Hover or focus
// melts it open into a panel with the current step, the trace, tool chips, task rows, the last answer and the
// composer; leaving lets the panel drain back into the pill. While collapsed, each running step buds off the pill as
// a small bubble and drops back in when it is done. Voice is primary: click the mic, or hold Space, to talk.
//
//   const dock = createDock(hostEl, { onSend(text, { via }), onMention(ref, { hover }), partScreenPos(ref) })
//   see the method list at the bottom of createDock (setState, setStatus, setLevel, step, loader, answerStart,
//   answerAppend, answerEnd, ask, recommend, say, listen, clear, expand, lookAt).
//
// Shapes live in one goo layer (pill, panel, bubbles), content sits above it and follows the same springs. Every
// frame only writes clip-path / transform / opacity; layout is read once per change through ResizeObservers.
import { spring, tick, reducedMotion, onReducedMotion, clamp, lerp, smooth } from "./spring.js";
import { gooLayer, spawnBlob, squish, wobble } from "./goo.js";
import { createEyes } from "./eyes.js";
import { createVoiceAdapter, speakable } from "./voice-adapter.js";
import { el, icon, pixelLoader, timeline, stagger, streamAnswer, approvalCard, recommendCard, bindMentions, fmtSecs } from "./trace.js";

const OPEN_SPRING = { stiffness: 250, damping: 21 }, LEAVE_GRACE = 60;
const PILL_H = 56, GAP = 8, EYES_W = 46, MIC = 40, PAD = 8;
const STATES = new Set(["idle", "userTalking", "heard", "thinking", "working", "speaking", "success", "confused", "error", "offline"]);
const DEFAULT_STATUS = "Ask Etch · hold Space to talk";
const plain = (s) => String(s || "").replace(/`([^`]+)`/g, "$1").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/\s+/g, " ").trim();

// what a step is, as an eyes activity and a pill line of at most three words
const ACTS = [
  [/wait|queue|in line/i, "wait", "Waiting in line"],
  [/screen|search|scan|looking for|find/i, "scan", "Screening parts"],
  [/repair|fix/i, "repair", "Fixing the design"],
  [/\b(erc|drc)\b/i, "inspect", (t) => `Checking ${/drc/i.test(t) ? "DRC" : "ERC"}`],
  [/check|gate|verif|test|parity|benchmark|inspect/i, "inspect", "Checking the design"],
  [/rout|plac|track|layout/i, "route", (t) => (/rout/i.test(t) ? "Routing traces" : "Placing parts")],
  [/render|3d/i, "render", "Rendering in 3D"],
  [/export|writ|gerber|fab file|zip|sav|copy/i, "export", (t) => (/copy/i.test(t) ? "Copying the board" : "Writing files")],
  [/pick|choos|select|gather|using \d|adding/i, "pick", "Picking parts"],
  [/read|open|load|parts\.json|look/i, "read", "Reading the board"],
  [/send|start/i, "think", "Starting up"],
];
function activityOf(d) {
  const t = String(d.title || "");
  if (d.kind === "reasoning") return ["think", "Thinking"];
  for (const [re, a, w] of ACTS) if (re.test(t)) return [a, typeof w === "function" ? w(t) : w];
  return ["think", "Working"];
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
  const empty = el("div", "ag-empty", el("div", "ag-empty-t", "What should we look at?"), el("div", "ag-empty-d", "Ask about any part, or ask for a change. Click the mic or hold Space to talk."));
  const scroll = el("div", "ag-scroll", empty, turnsEl);
  const newPill = el("button", "ag-newpill lq-btn");
  newPill.type = "button"; newPill.hidden = true;
  const inner = el("div", "ag-panel-in", headWrap, scroll, newPill);
  const panel = el("div", "ag-panel", inner);
  panel.setAttribute("role", "region");
  panel.setAttribute("aria-label", "Etch agent");
  panel.inert = true;

  const bubbles = el("div", "ag-bubbles");
  const eyesWrap = el("div", "ag-pill-eyes");
  const eyes = createEyes(eyesWrap);
  const statusText = el("span", "ag-status-t", DEFAULT_STATUS);
  const pillLoader = pixelLoader({ size: "xs" });
  const status = el("div", "ag-status", pillLoader.el, statusText);
  status.setAttribute("aria-live", "polite");
  const input = el("input", "ag-input");
  input.type = "text"; input.placeholder = "Ask Etch or describe a change"; input.setAttribute("aria-label", "Message Etch"); input.maxLength = 400;
  input.autocomplete = "off";
  const send = el("button", "ag-send lq-btn", icon("send"));
  send.type = "submit"; send.setAttribute("aria-label", "Send");
  const form = el("form", "ag-compose", input, send);
  const mic = el("button", "ag-mic lq-btn", icon("mic"));
  mic.type = "button"; mic.setAttribute("aria-label", "Talk to Etch (or hold Space)");
  const badge = el("span", "ag-badge");
  badge.hidden = true;
  const pill = el("div", "ag-pill", eyesWrap, status, mic, form);
  const closeBtn = el("button", "ag-x lq-btn", icon("close"));
  closeBtn.type = "button"; closeBtn.setAttribute("aria-label", "Close (work goes on)"); closeBtn.title = "Close · work goes on in the background";
  inner.append(closeBtn); squish(closeBtn);
  // the pill comes first in tab order: mic, then (once open) the composer and the panel
  root.append(goo, pill, panel, bubbles, badge); // the badge sits outside the pill's clip
  host.append(root);
  squish(send); squish(mic);

  // ---------- springs ----------
  const o = spring(0, { preset: OPEN_SPRING, precision: 0.001, onUpdate: draw });
  const pw = spring(220, { preset: { stiffness: 300, damping: 22 }, precision: 0.05, onUpdate: draw });
  const hs = spring(140, { preset: { stiffness: 260, damping: 26 }, precision: 0.05, onUpdate: draw });
  const pk = spring({ x: 1, y: 1 }, { preset: "wobbly", precision: 0.0008, onUpdate: (v) => { const s = v.x === 1 && v.y === 1 ? "" : `${v.x} ${v.y}`; pillBlob.style.scale = s; eyesWrap.style.scale = s; } });
  let collapsedW = 220;

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
    const p = clamp(pw.value, 120, W), side = (W - p) / 2;
    pillBlob.style.clipPath = pill.style.clipPath = `inset(0px ${side.toFixed(1)}px round ${PILL_H / 2}px)`;
    eyesWrap.style.transform = `translateX(${(side + PAD).toFixed(1)}px)`;
    badge.style.translate = `${(side + PAD + 26).toFixed(1)}px 0`;
    status.style.transform = `translateX(${(side + PAD + EYES_W + 8).toFixed(1)}px)`;
    status.style.opacity = (1 - smooth(0.02, 0.4, oc)).toFixed(3);
    form.style.opacity = smooth(0.5, 1, oc).toFixed(3);
    mic.style.transform = `translateX(${(W - side - PAD - MIC - oc * 2).toFixed(1)}px) scale(${lerp(1, 1.1, oc).toFixed(3)})`;
  }

  function layout() {
    W = Math.round(Math.min(460, innerWidth - 24));
    // phones: a bottom sheet of at most ~45% of the screen, so the workplane stays in view during a build
    MAXH = Math.round(innerWidth <= 768 ? clamp(innerHeight * 0.45 - PILL_H - GAP, 160, 540) : clamp(innerHeight - 170, 180, 540));
    root.style.width = W + "px";
    root.style.height = MAXH + GAP + PILL_H + "px";
    panelBlob.style.height = panel.style.height = MAXH + "px";
    inner.style.maxHeight = MAXH + "px";
    sizeCollapsed();
    pw.jump(open ? W : collapsedW);
    draw();
  }
  // the collapsed pill grows (springily) to fit its status line, up to the dock's width; longer lines end in "…" and
  // keep the whole text in a tooltip. The text never runs under the mic.
  function sizeCollapsed() {
    const room = W - (PAD * 2 + EYES_W + 8 + MIC + 12);
    if (status.style.maxWidth !== room + "px") status.style.maxWidth = room + "px";
    const sw = Math.min(status.offsetWidth || 160, room);
    collapsedW = Math.round(clamp(PAD + EYES_W + 8 + sw + 12 + MIC + PAD, 170, W));
    if (!open) pw.set(collapsedW);
  }

  // ---------- open / close ----------
  let asked = false;
  let open = false, hover = false, focusIn = false, forced = null, pending = 0, suppress = false, closeT = 0, dismissed = false;
  const typing = () => document.activeElement === input || input.value.trim() !== "";
  // Dismissed (the x, Escape, a click outside): only the pointer coming back or a click reopens it; running work and
  // waiting cards carry on in the background (the pill shows "1 waiting").
  function want() {
    if (forced === true) return true;
    if (forced === false) return false;
    if (dismissed) return hover;
    return hover || focusIn || pending > 0 || typing();
  }
  function dismiss() {
    forced = null; hover = false; focusIn = false; dismissed = true; suppress = inside;
    clearTimeout(closeT);
    if (root.contains(document.activeElement)) document.activeElement.blur();
    update();
  }
  function update() {
    const w = want();
    if (w === open) return;
    open = w;
    root.dataset.open = String(open);
    panel.inert = !open;
    form.inert = !open;
    // one spring both ways, so closing feels exactly as quick as opening
    o.config(OPEN_SPRING).set(open ? 1 : 0);
    if (!open) sizeCollapsed();
    pw.set(open ? W : collapsedW);
    if (open) { dropBubble(0); stick(true); }
    else wobblePill(0.6);
    paintBadge();
  }
  function paintBadge() {
    const show = pending > 0 && !open;
    badge.hidden = !show;
    if (show) badge.textContent = `${pending} waiting`;
  }
  let inside = false;
  root.addEventListener("pointerover", () => {
    inside = true;
    if (suppress) return;
    clearTimeout(closeT);
    if (!hover) { hover = true; dismissed = false; update(); }
  });
  root.addEventListener("pointerout", (e) => {
    if (e.relatedTarget && root.contains(e.relatedTarget)) return;
    inside = false; suppress = false;
    clearTimeout(closeT);
    // a short grace only absorbs pointer jitter across the edge (or the gap between panel and pill)
    closeT = setTimeout(() => { hover = false; update(); }, LEAVE_GRACE);
  });
  root.addEventListener("focusin", (e) => { focusIn = true; if (e.target === input) dismissed = false; if (forced === false) forced = null; update(); });
  root.addEventListener("focusout", (e) => {
    if (e.relatedTarget && root.contains(e.relatedTarget)) return;
    setTimeout(() => { if (!root.contains(document.activeElement)) { focusIn = false; update(); } }, 0);
  });
  input.addEventListener("input", update);
  // Escape puts the dock away from anywhere, unless something else outside it has focus (a menu, a dialog)
  const onEsc = (e) => {
    if (e.key !== "Escape" || !open || e.defaultPrevented) return;
    const a = document.activeElement;
    if (a && a !== document.body && !root.contains(a)) return;
    e.preventDefault();
    dismiss();
  };
  document.addEventListener("keydown", onEsc);
  closeBtn.addEventListener("click", (e) => { e.stopPropagation(); dismiss(); });
  // a click anywhere outside the dock (the board, say) puts it away
  const outside = (e) => { if (open && !root.contains(e.target)) dismiss(); };
  document.addEventListener("pointerdown", outside, true);
  // touch: a tap on the pill opens / closes it
  pill.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "touch" || e.target.closest(".ag-mic, .ag-compose")) return;
    if (open) { dismiss(); return; }
    forced = true; hover = false; dismissed = false; update();
  });
  pill.addEventListener("click", (e) => { if (!open && !e.target.closest(".ag-mic")) { dismissed = false; suppress = false; hover = true; update(); } });
  bubbles.addEventListener("click", () => { forced = null; hover = true; dismissed = false; update(); });

  function wobblePill(s = 1) { pk.impulse({ x: 0.9 * s, y: -1.4 * s }); }

  // ---------- size tracking ----------
  const ro = new ResizeObserver(() => {
    hs.set(inner.offsetHeight || 140);
    sizeCollapsed();
  });
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
  scroll.addEventListener("scroll", () => {
    pinned = atBottom();
    if (pinned && unseen) { unseen = 0; paintPill(); }
  }, { passive: true });
  newPill.addEventListener("click", () => { scroll.scrollTo({ top: scroll.scrollHeight, behavior: reducedMotion() ? "auto" : "smooth" }); unseen = 0; pinned = true; paintPill(); });
  // force: the person just sent something or opened the dock; fresh: a new item arrived (counts while scrolled up)
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

  // ---------- bubbles (collapsed only) ----------
  let bub = null, bubT = 0;
  function showBubble(text, { id = null, kind = "step", caption = false, done = false } = {}) {
    if (open || !text) return;
    clearTimeout(bubT);
    const pillTop = MAXH + GAP;
    if (!bub) {
      const c = el("div", "ag-bub");
      bubbles.append(c);
      bub = { c, id, blob: null };
    }
    bub.id = id;
    const c = bub.c;
    c.className = "ag-bub" + (caption ? " caption" : "") + (done ? " done" : "");
    c.replaceChildren(...[caption ? null : done ? icon("done", "is-done") : kind === "answer" ? icon("spark", "is-spark") : el("span", "ag-spin"), el("span", "ag-bub-t", text)].filter(Boolean));
    c.style.maxWidth = W - 24 + "px";
    const w = c.offsetWidth, h = c.offsetHeight;
    const to = { x: W / 2, y: pillTop - 12 - h / 2 };
    if (!bub.blob) bub.blob = spawnBlob(goo, { from: { x: W / 2, y: pillTop + PILL_H / 2 }, to, w, h, radius: Math.min(h / 2, 20), content: c, className: "ag-blob-bub" });
    else { bub.blob.resize(w, h); bub.blob.moveTo(to.x, to.y); }
  }
  function dropBubble(delay = 0) {
    clearTimeout(bubT);
    if (!bub) return;
    const b = bub;
    bubT = setTimeout(() => {
      if (bub !== b) return;
      bub = null;
      b.blob.absorb({ x: W / 2, y: MAXH + GAP + PILL_H / 2 }).then(() => { b.c.remove(); if (!open) { wobblePill(1); eyes.react("blink"); } });
    }, delay);
  }

  // ---------- state ----------
  let base = "idle", overlay = null, overlayT = 0, statusBase = "", statusTemp = "", actWords = "";
  function paintState() {
    const s = overlay || base;
    eyes.setState(s);
    root.dataset.state = s;
  }
  function setOverlay(s, ms = 0) {
    clearTimeout(overlayT);
    overlay = s; paintState();
    if (s && ms) overlayT = setTimeout(() => { overlay = null; paintState(); }, ms);
  }
  function paintStatus() {
    const t = statusTemp || actWords || statusBase || DEFAULT_STATUS;
    if (statusText.textContent === t) return;
    statusText.textContent = t;
    statusText.title = t;
    statusText.classList.remove("ag-in"); void statusText.offsetWidth; statusText.classList.add("ag-in");
  }
  // The headline is the current activity ("Looking at J1"); the step itself is the live row at the bottom of the
  // timeline, which alone carries the loader. With no live row (waiting in line, say) the header shows the loader.
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
  // ---------- the mascot acts out what Etch is doing ----------
  function paintActivity() {
    let a = null, words = "";
    if (liveR) [a, words] = activityOf(liveR.data);
    else if (loading && /wait|line|queue/i.test(loaderLabel)) [a, words] = ["wait", "Waiting in line"];
    else if (base === "thinking") [a, words] = ["think", "Thinking"];
    else if (base === "working") [a, words] = ["think", "Working"];
    eyes.setActivity(a);
    if (words !== actWords) { actWords = words; paintStatus(); }
  }

  // ---------- eyes follow parts ----------
  let hoverRef = null, lookRef = null, clickRef = null, clickT = 0, answerRef = null, eyeJob = null;
  function refNow() {
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
      if (!r) { eyes.lookAt(null); eyeJob = null; return false; }
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

  // ---------- turns ----------
  const steps = new Map(), answers = new Map(), cards = new Map();
  let turn = null;
  // One chronological flow per turn: the person's message, then every step, card and answer in the order they
  // happened. Steps go into the turn's current timeline; a card or an answer closes it, and later steps open a new one.
  function fold(t, seconds) { if (t) for (const tl of t.tls) if (!tl.finished && !tl.running) tl.done(tl === t.tl ? seconds : undefined); }
  function newTurn(text, via) {
    if (turn) { turn.el.classList.add("old"); fold(turn); }
    const t = { el: el("div", "ag-turn lq-in"), tl: null, tls: [], user: !!text };
    if (text) {
      const u = el("div", "ag-you", el("span", "ag-you-t", text));
      if (via === "voice") u.prepend(icon("mic", "ag-you-ic"));
      t.el.append(u);
    }
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
  function doSend(text, via) {
    text = String(text || "").trim();
    if (!text) return;
    newTurn(text, via);
    asked = true; // sending is a user gesture, so Etch may answer out loud from here on
    curLine = ""; statusBase = "";
    paintLine();
    if (opts.onSend) opts.onSend(text, { via });
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const t = input.value; input.value = "";
    doSend(t, "typed"); update();
  });

  // ---------- voice ----------
  let spaceWaiting = false, fakeSpeak = 0;
  const voice = createVoiceAdapter({
    onState(s, d) {
      if (s === "listening") {
        setOverlay("userTalking"); statusTemp = "Listening…"; paintStatus(); mic.classList.add("rec");
        if (spaceWaiting) { spaceWaiting = false; dispatchEvent(new KeyboardEvent("keyup", { code: "Space", key: " " })); }
      } else {
        mic.classList.remove("rec");
        if (s === "transcribing") { setOverlay("heard"); statusTemp = "Got it…"; }
        else if (s === "speaking") { setOverlay("speaking"); statusTemp = ""; }
        else if (s === "idle") { if (overlay !== "heard") setOverlay(null); statusTemp = ""; eyes.setLevel(0); }
        else if (s === "off") { setOverlay(null); statusTemp = ""; if (d) caption(d); eyes.react("squish"); }
        paintStatus();
      }
    },
    onLevel(who, x) {
      if ((who === "you" && overlay === "userTalking") || (who === "etch" && overlay === "speaking")) eyes.setLevel(x);
      mic.style.setProperty("--lv", who === "you" ? clamp(x).toFixed(3) : "0");
    },
    onHeard(text) { setOverlay("heard", 1100); statusTemp = ""; paintStatus(); doSend(text, "voice"); },
    onInterim(text) { statusTemp = text ? `“${text.length > 48 ? "…" + text.slice(-46) : text}”` : "Listening…"; paintStatus(); },
    onCaption(text) { caption(text); },
    onError(line) { eyes.react("squish"); caption(line); },
  });
  // what Etch says shows as a bubble over the collapsed pill; the open panel already has it in the timeline
  function caption(text) {
    const t = plain(text);
    if (!t) return;
    showBubble(t.length > 140 ? t.slice(0, 137) + "…" : t, { caption: true });
    dropBubble(Math.max(2600, t.split(" ").length * 380));
  }
  mic.addEventListener("pointerdown", (e) => { if (e.button !== 0) return; e.preventDefault(); voice.toggle(); });
  mic.addEventListener("pointerup", () => voice.release());
  mic.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); if (!e.repeat) voice.toggle(); } });
  mic.addEventListener("keyup", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); } });

  // Space anywhere (outside text fields and other controls) is push-to-talk. Space on a focused control keeps its
  // normal meaning and does not reach voice.js's window listener.
  const isTyping = () => { const a = document.activeElement; return !!a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT" || a.isContentEditable); };
  const onControl = () => { const a = document.activeElement; return !!a && a !== document.body && !!a.closest("button, a[href], summary, [role=button], [role=tab], [role=radio], [role=checkbox], [role=switch], [role=option], [role=menuitem]"); };
  const kd = (e) => {
    if (e.code !== "Space" || isTyping()) return;
    if (onControl()) { e.stopPropagation(); return; }
    e.preventDefault();
    if (e.repeat) return;
    if (!voice.created) { spaceWaiting = false; voice.start({ held: true }); }
  };
  const ku = (e) => {
    if (e.code !== "Space" || isTyping()) return;
    if (onControl()) { e.stopPropagation(); return; }
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
  paintState(); paintLine();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { sizeCollapsed(); draw(); });

  // ---------- public API ----------
  const api = {
    el: root,
    eyes,
    get open() { return open; },
    get state() { return overlay || base; },

    setState(s) {
      if (!STATES.has(s)) return;
      base = s;
      // the agent settling down folds the finished timeline into its one-line summary
      if (/^(idle|success|confused|error|offline)$/.test(s)) setTimeout(() => { if (base === s) fold(turn); }, 300);
      if (s === "success") { eyes.emote("delight"); wobblePill(1); }
      paintActivity();
      // the agent moving on clears the dock's own short-lived voice looks
      if (overlay === "heard" && s !== "idle") setOverlay(null);
      paintState();
    },
    setStatus(text) { statusBase = plain(text); paintStatus(); paintLine(); },
    // one-shot reactions for the editor: "surprise" | "delight" | "wince" | "hop" | "nod" | "shake" | "pleased"
    emote(kind) { eyes.emote(kind); if (kind === "hop" || kind === "delight") wobblePill(0.8); },
    setLevel(v) { eyes.setLevel(v); },

    step(d) {
      if (!d || d.id == null) return;
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
      if (st === "running") showBubble(s.data.title, { id: d.id, kind });
      else if (bub && bub.id === d.id) { showBubble(s.data.title, { id: d.id, kind, done: st === "done" }); dropBubble(st === "failed" ? 1400 : 650); }
      if (st === "failed" && was !== "failed") {
        // a failed check: a wince, then a determined look
        eyes.emote("wince");
        setTimeout(() => { if (base === "working") eyes.emote("nod"); }, 900);
      } else if (st === "done" && was !== "done" && /\bpassed\b/i.test(s.data.title || "")) eyes.react("nod");
      else if (st === "done" && was === "running" && s.data.kind === "task") eyes.react("nod");
      // a finished step keeps its check for a beat before the loader moves on
      if (was === "running" && st !== "running") setTimeout(pickLive, 250); else pickLive();
      retarget(); stick();
    },

    loader(on, label) {
      if (on) {
        loading = true;
        loaderLabel = String(label || "Working").replace(/\s*·\s*[\d.]+\s*s$/, "");
        pillLoader.start();
        root.classList.add("loading");
      } else {
        loading = false; loaderLabel = "";
        pillLoader.stop();
        root.classList.remove("loading");
      }
      paintLine();
    },

    answerStart(id, { ref } = {}) {
      const t = cur();
      t.hadTl = t.tls.length > 0;
      if (t.tl) { const tl = t.tl; t.tl = null; if (!tl.running) tl.done(); }
      const a = streamAnswer({ ref, onFollowup: (f) => doSend(f, "followup") });
      place(t, a.el);
      answers.set(id, { a, bubbleAt: 0 });
      answerRef = ref || null;
      retarget();
      showBubble("Answering…", { kind: "answer", id: "answer:" + id });
      stick();
      return a;
    },
    answerAppend(id, chunk) {
      let x = answers.get(id);
      if (!x) { api.answerStart(id); x = answers.get(id); }
      x.a.append(chunk);
      const now = performance.now();
      if (!open && now - x.bubbleAt > 300) {
        x.bubbleAt = now;
        const p = plain(x.a.text);
        showBubble(p.length > 90 ? p.slice(0, 88).replace(/\s+\S*$/, "") + "…" : p, { kind: "answer", id: "answer:" + id });
      }
      stick();
    },
    answerEnd(id, info = {}) {
      const x = answers.get(id);
      if (!x) return;
      const t = cur();
      // the folded timeline above already says how long it took and how many steps; no second meta line
      x.a.end(t.hadTl ? { ...info, seconds: null, thoughts: null } : info);
      for (const tl of t.tls) if (!tl.finished && !tl.running) tl.done(info.seconds);
      const first = speakable(x.a.text, 1);
      eyes.emote("pleased");
      if (!open && first) { showBubble(first.length > 110 ? first.slice(0, 108) + "…" : first, { kind: "answer", id: "answer:" + id, done: true }); dropBubble(4200); }
      setTimeout(() => { if (answerRef && answers.get(id)) { answerRef = null; retarget(); } }, 2600);
      stick();
    },

    ask({ id, question, options = [], multi = false } = {}) {
      const key = id ?? "ask:" + Math.random();
      if (cards.has(key)) cards.get(key).cancel();
      const c = approvalCard({ question, options, multi });
      place(cur(), c.el);
      cards.set(key, c);
      pending++; if (forced === false) forced = null; update(); paintBadge();
      eyes.react("surprise");
      c.promise.finally(() => { if (cards.get(key) === c) cards.delete(key); pending = Math.max(0, pending - 1); update(); paintBadge(); });
      return c.promise;
    },
    recommend({ id, title, detail, confidence, actions } = {}) {
      const key = id ?? "rec:" + Math.random();
      if (cards.has(key)) cards.get(key).cancel();
      const c = recommendCard({ title, detail, confidence, actions });
      place(cur(), c.el);
      cards.set(key, c);
      pending++; if (forced === false) forced = null; update(); paintBadge();
      eyes.react("blink");
      c.promise.finally(() => { if (cards.get(key) === c) cards.delete(key); pending = Math.max(0, pending - 1); update(); paintBadge(); });
      return c.promise;
    },

    say(text) {
      const t = plain(text);
      if (!t) return;
      const spoken = voice.say(t, { force: opts.speak === "always" || asked });
      if (!spoken) {
        // nothing is spoken before the person has used their voice; the eyes still "say" it with the caption
        caption(t);
        clearInterval(fakeSpeak);
        const t0 = performance.now(), dur = Math.max(1400, t.split(" ").length * 300);
        setOverlay("speaking");
        fakeSpeak = setInterval(() => {
          const k = performance.now() - t0;
          if (k > dur || overlay !== "speaking") { clearInterval(fakeSpeak); eyes.setLevel(0); if (overlay === "speaking") setOverlay(null); return; }
          eyes.setLevel(0.25 + 0.3 * Math.abs(Math.sin(k / 95)));
        }, 50);
      }
    },
    listen() { voice.start({ auto: true }); },

    clear() {
      for (const c of cards.values()) c.cancel();
      cards.clear(); steps.clear(); answers.clear();
      turnsEl.replaceChildren(); turn = null; empty.hidden = false; liveR = null; unseen = 0; paintPill();
      api.loader(false);
      dropBubble(0);
      answerRef = lookRef = hoverRef = clickRef = null; retarget();
      curLine = ""; statusBase = ""; statusTemp = ""; paintStatus(); paintLine();
      base = "idle"; setOverlay(null);
      turn = null;
    },
    expand(v) {
      if (v) { forced = true; suppress = false; dismissed = false; }
      else { forced = null; hover = false; suppress = inside; if (root.contains(document.activeElement)) document.activeElement.blur(); focusIn = false; }
      update();
    },
    // extra: hold the eyes on a part (partScreenPos is polled each frame while set); null lets go
    lookAt(ref) { lookRef = ref || null; retarget(); },

    destroy() {
      for (const c of cards.values()) c.cancel();
      voice.stop(); offRM(); ro.disconnect();
      document.removeEventListener("keydown", kd); document.removeEventListener("keyup", ku);
      document.removeEventListener("pointerdown", outside, true); document.removeEventListener("keydown", onEsc);
      removeEventListener("resize", onResize);
      eyes.destroy(); root.remove();
    },
  };
  // react to the agent appearing to change the board: a little wobble on the pill
  api.nudge = () => { wobblePill(1); wobble(statusText, 0.6); };
  return api;
}

export { fmtSecs };

// Etch's mascot: two rounded eyes on a soft dark body, nothing else. All expression comes from the eyes' shape, size
// and position, blinks, and the body's squash, stretch and tilt. Every change goes through springs, so moving from
// one state to another is a morph, never a cut.
//
//   const eyes = createEyes(el)
//   eyes.setState("idle" | "userTalking" | "heard" | "thinking" | "working" | "speaking" | "success" | "confused" | "error" | "offline")
//   eyes.setLevel(0..1)      mic or voice level (userTalking pulses, speaking bounces)
//   eyes.lookAt(x, y)        look at a point in viewport px; lookAt(null) lets go
//   eyes.blink()             one blink
//   eyes.react(kind)         "nod" | "bounce" | "shake" | "surprise" | "squish" | "tilt" | "blink"
//   eyes.setActivity(kind)   what Etch is doing, acted out with the eyes (null stops): "scan" | "read" | "pick" |
//                            "route" | "inspect" | "repair" | "export" | "render" | "wait" | "think"
//   eyes.emote(kind)         one-shot feeling: "surprise" | "delight" | "wince" | "hop" | "nod" | "shake" | "pleased" | "yawn"
//
// Cost: one rAF job that only runs while a spring moves or a state needs continuous motion (thinking, talking,
// speaking). Settled idle costs a blink timer and a CSS breathing animation on the compositor.
import { spring, tick, reducedMotion, onReducedMotion, clamp, lerp } from "./spring.js";

const NS = "http://www.w3.org/2000/svg";
const BASE = { w: 15, h: 22, gap: 25, cy: -1, lLo: 0, lLi: 0, rLo: 0, rLi: 0, happy: 0, sL: 1, sR: 1, tilt: 0, bsx: 1, bsy: 1, by: 0, dim: 0, flat: 0, dots: 0 };
const STATES = {
  idle: {},
  userTalking: { w: 16.5, h: 25, sL: 1.05, sR: 1.05, bsx: 1.03, bsy: 1.03, by: 1 },
  heard: { happy: 0.62, h: 21, lLo: 0.08, lLi: 0.08, rLo: 0.08, rLi: 0.08 },
  thinking: { h: 20, lLo: 0.18, lLi: 0.3, rLo: 0.18, rLi: 0.3, dots: 1 },
  working: { w: 16.5, h: 15, lLo: 0.08, lLi: 0.5, rLo: 0.08, rLi: 0.5 },
  speaking: { happy: 0.18, h: 21 },
  success: { happy: 1, w: 17, h: 23 },
  confused: { sL: 1.12, sR: 0.7, tilt: -10, rLo: 0.34, rLi: 0.12 },
  error: { h: 15, lLo: 0.55, rLo: 0.55, lLi: 0.1, rLi: 0.1, dim: 0.35, bsy: 0.94, by: 2.5, tilt: 0 },
  offline: { flat: 1, dim: 1, bsy: 0.95, by: 1 },
};
// where each state looks when nothing in particular holds its gaze (x, y in -1..1)
const GAZE = { userTalking: [0, 0.45], thinking: [0, -0.8], working: [0, 0.2], confused: [0.35, -0.4], error: [0, 0.6], offline: [0, 0.55], heard: [0, 0.15] };
// activities: a shape on top of the state, plus a procedural gaze / body motion in frame()
const ACT = {
  scan: { w: 16, h: 20 },
  read: { h: 18, lLo: 0.16, lLi: 0.16, rLo: 0.16, rLi: 0.16 },
  pick: { w: 16, h: 23 },
  route: { h: 15, lLo: 0.3, lLi: 0.42, rLo: 0.3, rLi: 0.42 },
  inspect: { sR: 0.74, rLo: 0.5, rLi: 0.5, sL: 1.1, h: 21, tilt: 4 },
  repair: { h: 17, lLo: 0.05, lLi: 0.55, rLo: 0.05, rLi: 0.55 },
  export: { h: 19, happy: 0.2 },
  render: { w: 18.5, h: 26 },
  wait: { h: 20, lLo: 0.6, lLi: 0.6, rLo: 0.6, rLi: 0.6, bsy: 0.97 },
  think: { h: 20, lLo: 0.18, lLi: 0.3, rLo: 0.18, rLi: 0.3, dots: 1 },
};
// states that take over from an activity (the agent talks, listens or reacts)
const ACT_STATES = new Set(["idle", "working", "thinking"]);
const EMOTE = {
  surprise: { w: 18, h: 28, ms: 650 },
  delight: { happy: 1, w: 17.5, h: 23, ms: 1000 },
  pleased: { happy: 0.75, h: 19, ms: 1300 },
  wince: { h: 9, lLo: 0.6, lLi: 0.6, rLo: 0.6, rLi: 0.6, ms: 520 },
  yawn: { h: 6, w: 16, bsy: 1.14, bsx: 0.94, by: -2, ms: 1500 },
};
const BLINKS = new Set(["idle", "userTalking", "thinking", "working", "speaking", "confused", "error"]);
const LIVE = new Set(["thinking", "userTalking", "speaking"]);
export const EYE_STATES = Object.keys(STATES);

function eyePath(cx, cy, w, h, lidL, lidR, happy, open) {
  const k = 0.64, hw = w / 2, x0 = cx - hw, x1 = cx + hw;
  let yt = cy - h / 2, yb = cy + h / 2;
  // a blink is mostly the top lid coming down
  yt += (1 - open) * h * 0.78; yb -= (1 - open) * h * 0.22;
  if (yb - yt < 1.2) { const m = (yt + yb) / 2; yt = m - 0.6; yb = m + 0.6; }
  const hh = yb - yt;
  const ym = (yt + yb) / 2 + happy * hh * 0.18;
  const topL = yt + lidL * (ym - yt) * 0.9, topR = yt + lidR * (ym - yt) * 0.9, topC = (topL + topR) / 2;
  // happy: the bottom edge arches up under the top, leaving a ∩
  const botC = lerp(yb, Math.min(topC + Math.max(2.4, hh * 0.36), ym), happy);
  const f = (n) => Math.round(n * 100) / 100;
  return `M${f(x0)} ${f(ym)}C${f(x0)} ${f(ym - k * (ym - topL))} ${f(cx - k * hw)} ${f((topL + topC) / 2)} ${f(cx)} ${f(topC)}`
    + `C${f(cx + k * hw)} ${f((topR + topC) / 2)} ${f(x1)} ${f(ym - k * (ym - topR))} ${f(x1)} ${f(ym)}`
    + `C${f(x1)} ${f(ym + k * (botC - ym))} ${f(cx + k * hw)} ${f(botC)} ${f(cx)} ${f(botC)}`
    + `C${f(cx - k * hw)} ${f(botC)} ${f(x0)} ${f(ym + k * (botC - ym))} ${f(x0)} ${f(ym)}Z`;
}

let uid = 0;
export function createEyes(host, opts = {}) {
  const id = "ag-eyes-" + ++uid;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "-46 -36 92 72");
  svg.setAttribute("class", "ag-eyes");
  svg.setAttribute("overflow", "visible");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", opts.label || "Etch");
  svg.innerHTML = `<defs><linearGradient id="${id}-g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2d3445"/><stop offset="1" stop-color="#151922"/></linearGradient></defs>`
    + `<g class="ag-eyes-body"><rect x="-40" y="-30" width="80" height="60" rx="25" fill="url(#${id}-g)"/>`
    + `<path d="M-26 -24.5 Q0 -30.5 26 -24.5" stroke="#fff" stroke-opacity=".10" stroke-width="3" fill="none" stroke-linecap="round"/>`
    + `<g class="ag-eyes-pair"><path class="ag-eye"/><path class="ag-eye"/></g>`
    + `<g class="ag-eyes-glint" opacity="0"><circle r="3.4" fill="none" stroke="#9fc0ff" stroke-width="1.4"/><path d="M2.4 2.4 5.4 5.4" stroke="#9fc0ff" stroke-width="1.6" stroke-linecap="round"/><circle cx="-1.1" cy="-1.1" r=".9" fill="#fff"/></g></g>`
    + `<g class="ag-eyes-dots"><circle r="2.6"/><circle r="2.6"/><circle r="2.6"/></g>`;
  host.append(svg);
  const body = svg.querySelector(".ag-eyes-body"), pair = svg.querySelector(".ag-eyes-pair");
  const [eL, eR] = svg.querySelectorAll(".ag-eye");
  const dotsG = svg.querySelector(".ag-eyes-dots"), dots = dotsG.querySelectorAll("circle");
  const glint = svg.querySelector(".ag-eyes-glint");

  let state = "idle", level = 0, lvS = 0, look = null, cursor = null, cursorAt = 0, glance = null;
  // a little softer than before, so moving between states reads as a morph you can follow
  const main = spring({ ...BASE }, { preset: { stiffness: 170, damping: 19 }, precision: 0.002 });
  const gaze = spring({ x: 0, y: 0 }, { preset: { stiffness: 240, damping: 24 }, precision: 0.002 });
  const kick = spring({ by: 0, sq: 0, rot: 0, ex: 0, es: 0 }, { preset: { stiffness: 320, damping: 12 }, precision: 0.004 });
  const bl = spring(0, { preset: "blink", precision: 0.004 });
  let job = null, last = "", act = null, actT0 = 0, flashS = null, flashT = 0;
  const pickT = { at: 0, x: 0, y: 0, cx: 0, cy: 0 };
  const springs = [main, gaze, kick, bl];

  // first: draw even before the host is in the document (the dock builds the pill before attaching it)
  function frame(dt, now, first = false) {
    if (!svg.isConnected && !first) { job = null; return false; }
    const red = reducedMotion();
    lvS += (level - lvS) * (1 - Math.exp(-dt * 16));
    if (lvS < 0.002 && level === 0) lvS = 0;
    const t = now / 1000, m = main.value, g = gaze.value, kv = kick.value, lv = red ? 0 : lvS;
    let gx = g.x, gy = g.y;
    if ((state === "thinking" && !act) && !red && !look) { gx += 0.55 * Math.sin(t * 1.25); gy += 0.08 * Math.sin(t * 2.1); }
    let aBy = 0;
    const doing = act && ACT_STATES.has(state) && !red;
    if (doing) {
      const k = (now - actT0) / 1000;
      if (act === "scan") { gx = 0.95 * Math.sin(k * 7.5); gy = 0.12 + 0.05 * Math.sin(k * 15); }
      else if (act === "read") { const p = (k % 0.95) / 0.95, line = Math.floor(k / 0.95) % 4; gx = p < 0.86 ? -0.75 + 1.5 * (p / 0.86) : 0.75 - 1.5 * ((p - 0.86) / 0.14); gy = -0.25 + line * 0.2; }
      else if (act === "pick") {
        if (now > pickT.at) { pickT.at = now + 300 + Math.random() * 260; pickT.x = Math.random() * 1.8 - 0.9; pickT.y = Math.random() * 1.2 - 0.45; if (Math.random() < 0.45) setTimeout(blink, 90); }
        const f = 1 - Math.exp(-dt * 28); pickT.cx += (pickT.x - pickT.cx) * f; pickT.cy += (pickT.y - pickT.cy) * f; gx = pickT.cx; gy = pickT.cy;
      } else if (act === "route") { gx = 0.8 * Math.sin(k * 1.7); gy = 0.42 * Math.sin(k * 3.4); }
      else if (act === "inspect") { gx = 0.25 + 0.2 * Math.sin(k * 0.9); gy = 0.35 + 0.08 * Math.sin(k * 1.7); }
      else if (act === "repair") { if (k < 1.1) { gx = 0.3 * Math.sin(k * 26) * (1.1 - k); gy = 0.1; } else { gx = 0.15 * Math.sin(k * 2); gy = 0.3; } }
      else if (act === "export") { gx = 0.1 * Math.sin(k * 3); gy = 0.62; aBy = -3.2 * Math.abs(Math.sin(k * 5.2)); }
      else if (act === "render") { gx = 0.65 * Math.cos(k * 1.15); gy = 0.42 * Math.sin(k * 1.15); }
      else if (act === "wait") { gx = 0.5 * Math.sin(k * 0.45); gy = 0.25; }
      else if (act === "think") { gx = 0.55 * Math.sin(k * 1.25); gy = -0.75 + 0.08 * Math.sin(k * 2.1); }
    }
    const talk = state === "userTalking" ? lv : 0, spk = state === "speaking" ? lv : 0;
    const es = 1 + kv.es + talk * 0.16;
    const flat = m.flat;
    const w = lerp(m.w, 17, flat) * es * (1 + spk * 0.05), h = lerp(m.h, 3, flat) * es * (1 + spk * 0.24);
    const gap = m.gap - Math.abs(gx) * 3;
    // activities move a little further than plain gaze so they read at pill size
    const reach = doing ? 1.35 : 1;
    const ex = gx * 8 * reach + kv.ex, ey = gy * 6 * reach + flat * 2;
    const sL = m.sL * (1 - gx * 0.08), sR = m.sR * (1 + gx * 0.08);
    const open = 1 - bl.value;
    const dL = eyePath(-gap / 2, m.cy, w * sL, h * sL, m.lLo, m.lLi, m.happy, open);
    const dR = eyePath(gap / 2, m.cy, w * sR, h * sR, m.rLi, m.rLo, m.happy, open);
    const by = m.by + kv.by - spk * 4.5 + aBy;
    const bsy = m.bsy + kv.sq + talk * 0.05 + spk * 0.05, bsx = m.bsx - kv.sq * 0.6 + talk * 0.03;
    const tilt = m.tilt + kv.rot + gx * (doing ? 5 : 3);
    const bodyT = `translate(${(gx * 1.5).toFixed(2)} ${(30 + by).toFixed(2)}) rotate(${tilt.toFixed(2)}) scale(${bsx.toFixed(4)} ${bsy.toFixed(4)}) translate(0 -30)`;
    const key = dL + dR + bodyT + m.dim.toFixed(3) + m.dots.toFixed(3) + ex.toFixed(2) + ey.toFixed(2);
    if (key !== last) {
      last = key;
      eL.setAttribute("d", dL); eR.setAttribute("d", dR);
      body.setAttribute("transform", bodyT);
      pair.setAttribute("transform", `translate(${ex.toFixed(2)} ${ey.toFixed(2)})`);
      const eo = (1 - 0.55 * m.dim).toFixed(3);
      eL.setAttribute("fill-opacity", eo); eR.setAttribute("fill-opacity", eo);
      body.setAttribute("opacity", (1 - 0.3 * m.dim).toFixed(3));
    }
    if (m.dots > 0.01) {
      dotsG.style.display = "";
      for (let i = 0; i < 3; i++) {
        const a = (red ? 0.6 : t * 2.4) + (i * Math.PI * 2) / 3, z = Math.sin(a);
        dots[i].setAttribute("cx", (Math.cos(a) * 13).toFixed(2));
        dots[i].setAttribute("cy", (-38 + z * 3.5 + by).toFixed(2));
        dots[i].setAttribute("r", (2.6 * m.dots * (0.75 + 0.25 * (z + 1) / 2)).toFixed(2));
        dots[i].setAttribute("fill-opacity", (m.dots * (0.45 + 0.55 * (z + 1) / 2)).toFixed(3));
      }
    } else dotsG.style.display = "none";
    // the glint: a small lens catching the light while Etch screens parts
    const gl = doing && act === "scan" ? 1 : 0;
    glint.setAttribute("opacity", String(gl));
    if (gl) glint.setAttribute("transform", `translate(${(gap / 2 + ex + 6).toFixed(2)} ${(m.cy + ey - 7).toFixed(2)}) scale(${(0.8 + 0.25 * Math.abs(Math.sin(t * 7.5))).toFixed(3)})`);
    const alive = springs.some((s) => s.moving) || (LIVE.has(state) && !red) || lvS > 0 || m.dots > 0.01 && !red || doing;
    if (!alive) job = null;
    return alive;
  }
  const wake = () => { if (!job) job = tick(frame); };
  const setS = (s, v, o) => { s.set(v, o); wake(); };

  // ---- gaze: a part being talked about beats the state's own direction, which beats the cursor ----
  let rect = null, rectAt = 0;
  const center = () => {
    const n = performance.now();
    if (!rect || n - rectAt > 250) { rect = svg.getBoundingClientRect(); rectAt = n; }
    return [rect.left + rect.width / 2, rect.top + rect.height / 2];
  };
  const toward = (x, y, gain = 1) => {
    const [cx, cy] = center(), dx = x - cx, dy = y - cy, d = Math.hypot(dx, dy);
    if (d < 1) return [0, 0];
    const f = (d / (d + 140)) * gain;
    return [clamp((dx / d) * f, -1, 1), clamp((dy / d) * f * 0.9, -1, 1)];
  };
  function aim() {
    let x = 0, y = 0;
    if (look) [x, y] = toward(look[0], look[1]);
    else if (glance) [x, y] = glance;
    else if (GAZE[state]) {
      [x, y] = GAZE[state];
      if (state === "userTalking" && cursor) { const c = toward(cursor[0], cursor[1], 0.35); x += c[0]; y = y * 0.7 + c[1] * 0.3; }
    } else if (cursor && (state === "idle" || state === "speaking" || state === "success")) [x, y] = toward(cursor[0], cursor[1], 0.45);
    const tg = gaze.target;
    if (Math.abs(tg.x - x) > 0.012 || Math.abs(tg.y - y) > 0.012) setS(gaze, { x, y });
  }
  const onMove = (e) => {
    cursor = [e.clientX, e.clientY]; cursorAt = performance.now();
    if (!look && (state === "idle" || state === "userTalking" || state === "speaking" || state === "success")) { glance = null; aim(); }
  };
  addEventListener("pointermove", onMove, { passive: true });
  const onScroll = () => { rect = null; };
  addEventListener("scroll", onScroll, { passive: true, capture: true });
  addEventListener("resize", onScroll, { passive: true });

  // ---- blinks and idle glances on timers (no frame loop while waiting) ----
  let blinkT = 0, glanceT = 0;
  function blink() {
    bl.set(1); wake();
    // bored blinks are slow
    setTimeout(() => { bl.set(0); wake(); }, reducedMotion() ? 120 : act === "wait" ? 260 : 70);
  }
  function scheduleBlink() {
    clearTimeout(blinkT);
    blinkT = setTimeout(() => {
      if (!svg.isConnected) return;
      if (BLINKS.has(state) && !document.hidden) { blink(); if (Math.random() < 0.18) setTimeout(blink, 240); }
      scheduleBlink();
    }, 2300 + Math.random() * 3800);
  }
  function scheduleGlance() {
    clearTimeout(glanceT);
    glanceT = setTimeout(() => {
      if (!svg.isConnected) return;
      if (state === "idle" && !look && performance.now() - cursorAt > 2500 && !document.hidden) {
        glance = [(Math.random() * 2 - 1) * 0.7, (Math.random() * 2 - 1) * 0.4 - 0.05];
        aim();
        setTimeout(() => { glance = null; aim(); }, 600 + Math.random() * 900);
      }
      scheduleGlance();
    }, 3500 + Math.random() * 4500);
  }
  scheduleBlink(); scheduleGlance(); setTimeout(() => resetIdle(), 0);

  function react(kind) {
    switch (kind) {
      case "nod": kick.impulse({ by: 75, sq: -2.4 }); break;
      case "bounce": kick.impulse({ by: -150, sq: 1.6 }); break;
      case "hop": kick.impulse({ by: -210, sq: 2.2 }); break;
      case "shake": kick.impulse({ ex: 85, rot: 40 }); break;
      case "surprise": kick.impulse({ es: 3, sq: 1.4 }); break;
      case "squish": kick.impulse({ sq: -3 }); break;
      case "tilt": kick.impulse({ rot: 180 }); break;
      case "blink": blink(); break;
      default: return;
    }
    wake();
  }
  function paintClass() {
    svg.dataset.state = state;
    svg.classList.toggle("breathe", state === "idle" && !reducedMotion());
  }
  // the shape target: the state, then the activity (while Etch is busy or idle), then a one-shot emote on top
  function shape() {
    setS(main, { ...BASE, ...STATES[state], ...(act && ACT_STATES.has(state) ? ACT[act] : {}), ...(flashS || {}) });
  }
  function flash(sh, ms) {
    clearTimeout(flashT);
    const { ms: _ms, ...rest } = sh; void _ms;
    flashS = rest; shape();
    flashT = setTimeout(() => { flashS = null; shape(); }, ms);
  }
  function setActivity(a) {
    a = ACT[a] ? a : null;
    if (a === act) return;
    act = a; actT0 = performance.now(); pickT.at = 0;
    if (a) svg.dataset.act = a; else delete svg.dataset.act;
    shape(); aim(); wake();
    resetIdle();
  }
  function emote(kind) {
    const e = EMOTE[kind];
    if (e) flash(e, reducedMotion() ? Math.min(e.ms, 600) : e.ms);
    if (kind === "surprise") react("surprise");
    else if (kind === "delight") { react("bounce"); setTimeout(blink, 500); }
    else if (kind === "pleased") react("nod");
    else if (kind === "wince") { react("squish"); setTimeout(() => react("shake"), 160); }
    else if (kind === "hop") react("hop");
    else if (kind === "nod" || kind === "shake") react(kind);
    wake();
  }
  // long idle: a yawn after a minute with nothing happening
  let idleT = 0;
  function resetIdle() {
    clearTimeout(idleT);
    if (state !== "idle" || act) return;
    idleT = setTimeout(() => { if (state === "idle" && !act && svg.isConnected && !document.hidden) { emote("yawn"); resetIdle(); } }, 60000);
  }
  function setState(s) {
    if (!STATES[s]) s = "idle";
    if (s === state) return;
    const prev = state;
    state = s; glance = null;
    shape();
    resetIdle();
    aim(); paintClass();
    if (s === "heard") { react("nod"); setTimeout(blink, 120); }
    else if (s === "success") react("hop");
    else if (s === "confused") react("tilt");
    else if (s === "error" || s === "offline") react("squish");
    else if (s === "userTalking") react("surprise");
    else if (s === "working" && prev === "thinking") react("blink");
    wake();
  }
  const offRM = onReducedMotion(() => { paintClass(); wake(); });
  paintClass();
  frame(0, performance.now(), true);
  requestAnimationFrame(() => wake());

  return {
    el: svg,
    get state() { return state; },
    setState,
    setLevel(v) { level = clamp(+v || 0); if (level > 0 || lvS > 0) wake(); },
    lookAt(x, y) {
      if (x == null || y == null || !isFinite(x) || !isFinite(y)) look = null;
      else look = [x, y];
      aim();
    },
    blink,
    react,
    emote,
    setActivity,
    get activity() { return act; },
    destroy() {
      clearTimeout(blinkT); clearTimeout(glanceT); clearTimeout(idleT); clearTimeout(flashT); offRM();
      removeEventListener("pointermove", onMove); removeEventListener("scroll", onScroll, { capture: true }); removeEventListener("resize", onScroll);
      springs.forEach((s) => s.stop()); svg.remove();
    },
  };
}

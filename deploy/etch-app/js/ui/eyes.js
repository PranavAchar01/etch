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
  error: { flat: 1, dim: 0.55, bsy: 0.97 },
  offline: { flat: 1, dim: 1, bsy: 0.95, by: 1 },
};
// where each state looks when nothing in particular holds its gaze (x, y in -1..1)
const GAZE = { userTalking: [0, 0.45], thinking: [0, -0.8], working: [0, 0.2], confused: [0.35, -0.4], error: [0, 0.4], offline: [0, 0.55], heard: [0, 0.15] };
const BLINKS = new Set(["idle", "userTalking", "thinking", "working", "speaking", "confused"]);
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
    + `<g class="ag-eyes-pair"><path class="ag-eye"/><path class="ag-eye"/></g></g>`
    + `<g class="ag-eyes-dots"><circle r="2.6"/><circle r="2.6"/><circle r="2.6"/></g>`;
  host.append(svg);
  const body = svg.querySelector(".ag-eyes-body"), pair = svg.querySelector(".ag-eyes-pair");
  const [eL, eR] = svg.querySelectorAll(".ag-eye");
  const dotsG = svg.querySelector(".ag-eyes-dots"), dots = dotsG.querySelectorAll("circle");

  let state = "idle", level = 0, lvS = 0, look = null, cursor = null, cursorAt = 0, glance = null;
  const main = spring({ ...BASE }, { preset: { stiffness: 210, damping: 21 }, precision: 0.002 });
  const gaze = spring({ x: 0, y: 0 }, { preset: { stiffness: 240, damping: 24 }, precision: 0.002 });
  const kick = spring({ by: 0, sq: 0, rot: 0, ex: 0, es: 0 }, { preset: { stiffness: 320, damping: 12 }, precision: 0.004 });
  const bl = spring(0, { preset: "blink", precision: 0.004 });
  let job = null, last = "";
  const springs = [main, gaze, kick, bl];

  function frame(dt, now) {
    if (!svg.isConnected) { job = null; return false; }
    const red = reducedMotion();
    lvS += (level - lvS) * (1 - Math.exp(-dt * 16));
    if (lvS < 0.002 && level === 0) lvS = 0;
    const t = now / 1000, m = main.value, g = gaze.value, kv = kick.value, lv = red ? 0 : lvS;
    let gx = g.x, gy = g.y;
    if (state === "thinking" && !red && !look) { gx += 0.55 * Math.sin(t * 1.25); gy += 0.08 * Math.sin(t * 2.1); }
    const talk = state === "userTalking" ? lv : 0, spk = state === "speaking" ? lv : 0;
    const es = 1 + kv.es + talk * 0.16;
    const flat = m.flat;
    const w = lerp(m.w, 17, flat) * es, h = lerp(m.h, 3, flat) * es * (1 + spk * 0.14);
    const gap = m.gap - Math.abs(gx) * 3;
    const ex = gx * 8 + kv.ex, ey = gy * 6 + flat * 2;
    const sL = m.sL * (1 - gx * 0.08), sR = m.sR * (1 + gx * 0.08);
    const open = 1 - bl.value;
    const dL = eyePath(-gap / 2, m.cy, w * sL, h * sL, m.lLo, m.lLi, m.happy, open);
    const dR = eyePath(gap / 2, m.cy, w * sR, h * sR, m.rLi, m.rLo, m.happy, open);
    const by = m.by + kv.by - spk * 2.6;
    const bsy = m.bsy + kv.sq + talk * 0.03 + spk * 0.02, bsx = m.bsx - kv.sq * 0.6 + talk * 0.03;
    const tilt = m.tilt + kv.rot + gx * 3;
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
    const alive = springs.some((s) => s.moving) || (LIVE.has(state) && !red) || lvS > 0 || m.dots > 0.01 && !red;
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
    setTimeout(() => { bl.set(0); wake(); }, reducedMotion() ? 120 : 70);
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
  scheduleBlink(); scheduleGlance();

  function react(kind) {
    switch (kind) {
      case "nod": kick.impulse({ by: 55, sq: -1.8 }); break;
      case "bounce": kick.impulse({ by: -110, sq: 1.1 }); break;
      case "shake": kick.impulse({ ex: 60 }); break;
      case "surprise": kick.impulse({ es: 2.2 }); break;
      case "squish": kick.impulse({ sq: -2.2 }); break;
      case "tilt": kick.impulse({ rot: 140 }); break;
      case "blink": blink(); break;
      default: return;
    }
    wake();
  }
  function paintClass() {
    svg.dataset.state = state;
    svg.classList.toggle("breathe", state === "idle" && !reducedMotion());
  }
  function setState(s) {
    if (!STATES[s]) s = "idle";
    if (s === state) return;
    const prev = state;
    state = s; glance = null;
    setS(main, { ...BASE, ...STATES[s] });
    aim(); paintClass();
    if (s === "heard") { react("nod"); setTimeout(blink, 120); }
    else if (s === "success") react("bounce");
    else if (s === "confused") react("tilt");
    else if (s === "error" || s === "offline") react("squish");
    else if (s === "userTalking") react("surprise");
    else if (s === "working" && prev === "thinking") react("blink");
    wake();
  }
  const offRM = onReducedMotion(() => { paintClass(); wake(); });
  paintClass();
  frame(0, performance.now());

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
    destroy() {
      clearTimeout(blinkT); clearTimeout(glanceT); offRM();
      removeEventListener("pointermove", onMove); removeEventListener("scroll", onScroll, { capture: true }); removeEventListener("resize", onScroll);
      springs.forEach((s) => s.stop()); svg.remove();
    },
  };
}

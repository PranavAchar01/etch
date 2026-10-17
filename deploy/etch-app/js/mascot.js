// Etch's mascot for the board view: the same round green pixel face with headphones as the build app's landing page
// (drawn the same way, on a 32x30 canvas scaled up with crisp pixels), tuned for the light theme and given states:
//   idle      slow blink, gentle bob
//   listening the face leans in and swells with the mic level, eyes wide
//   thinking  eyes glance side to side, three dots above
//   speaking  the mouth opens with the voice level
// createMascot(canvas) animates one canvas; mascotFrame() is a still for small avatars.

const W = 32, H = 30;
const hash = (x, y) => { const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); };
const INK = [3, 12, 6], EDGE = [8, 58, 26], LIT = [150, 240, 178], BODY = [52, 214, 98], SPECK = [38, 186, 82];
const PHONES = [176, 186, 181], PHONES_DK = [96, 106, 102], CHEEK = [255, 140, 175];

function draw(g, T, st, lv) {
  const img = g.createImageData(W, H), d = img.data;
  const set = (x, y, c) => { if (x < 0 || y < 0 || x >= W || y >= H) return; const i = (y * W + x) * 4; d[i] = c[0]; d[i + 1] = c[1]; d[i + 2] = c[2]; d[i + 3] = 255; };
  const bob = st === "listening" ? -1 : Math.round(Math.sin(T * (st === "thinking" ? 5 : 2.4)) * 0.9);
  const grow = st === "listening" ? 1 + 0.06 * lv : 1;
  const inB = (x, y) => {
    const dx = (x - 15.5) / (11.2 * grow), dy = (y - 17 - bob) / (10.2 * grow), a = Math.atan2(dy, dx);
    return dx * dx + dy * dy < 1 + 0.07 * (hash(Math.round(a * 5), 3) - 0.3) + 0.05 * Math.sin(a * 9 + T * 2);
  };
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    if (!inB(x, y)) continue;
    const edge = !inB(x + 1, y) || !inB(x - 1, y) || !inB(x, y + 1) || !inB(x, y - 1), lit = (x - 11) ** 2 + (y - 11 - bob) ** 2 < 18;
    set(x, y, edge ? EDGE : lit ? LIT : hash(x, y + Math.floor(T * 4)) > 0.9 ? SPECK : BODY);
  }
  // headphones: band over the top, cups on both sides
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) { const r = Math.hypot(x - 15.5, (y - 15 - bob) * 1.05); if (r > 12.2 && r < 13.6 && y < 13 + bob) set(x, y, PHONES); }
  for (const c0 of [2, 27]) for (let y = 13; y < 21; y++) for (let x = c0; x < c0 + 3; x++) set(x, y + bob, x === c0 + 1 || y === 13 || y === 20 ? PHONES_DK : PHONES);
  // eyes
  const ey = 16 + bob, blink = st === "idle" ? (T % 4.2) > 4.05 : st === "speaking" ? (T % 3.1) > 3.0 : false;
  const look = st === "thinking" ? Math.round(Math.sin(T * 2.2) * 1.4) : 0, tall = st === "listening" ? 4 : 3;
  for (const ex0 of [11, 19]) {
    const ex = ex0 + look;
    if (blink) { set(ex, ey + 1, INK); set(ex + 1, ey + 1, INK); continue; }
    for (let k = 0; k < tall; k++) { set(ex, ey - (tall - 3) + k, INK); set(ex + 1, ey - (tall - 3) + k, INK); }
    set(ex, ey - (tall - 3), [255, 255, 255]);
  }
  for (const bx of [8, 22]) { set(bx, ey + 4, CHEEK); set(bx + 1, ey + 4, CHEEK); }
  // mouth
  const talk = st === "speaking" && lv > 0.18, wide = talk && lv > 0.55;
  if (talk) for (const [x, y] of wide ? [[14, 21], [15, 21], [16, 21], [17, 21], [15, 22], [16, 22], [15, 23], [16, 23]] : [[15, 21], [16, 21], [15, 22], [16, 22]]) set(x, y + bob, INK);
  else if (st === "listening") { set(15, 21 + bob, INK); set(16, 21 + bob, INK); set(15, 22 + bob, INK); set(16, 22 + bob, INK); }
  else if (st === "thinking") { set(15, 21 + bob, INK); set(16, 21 + bob, INK); }
  else { set(14, 20 + bob, INK); set(15, 21 + bob, INK); set(16, 21 + bob, INK); set(17, 20 + bob, INK); }
  // thinking dots
  if (st === "thinking") for (let i = 0; i < 3; i++) if (Math.floor(T * 3) % 4 > i) { set(22 + i * 2, 3, EDGE); }
  g.putImageData(img, 0, 0);
}

let still = null;
// a still frame (idle, eyes open) as a data URL, for avatars in the thread
export function mascotFrame() {
  if (still) return still;
  const c = document.createElement("canvas"); c.width = W; c.height = H;
  draw(c.getContext("2d"), 1, "idle", 0);
  return (still = c.toDataURL("image/png"));
}

export function createMascot(cv) {
  cv.width = W; cv.height = H;
  const g = cv.getContext("2d");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let st = "idle", lv = 0, target = 0, alive = true, last = 0;
  const T0 = performance.now();
  function frame(now) {
    if (!alive) return;
    requestAnimationFrame(frame);
    if (document.hidden || now - last < (st === "idle" ? 66 : 33)) return; // idle at 15 fps, active at 30
    last = now;
    lv += (target - lv) * 0.5;
    draw(g, reduce ? 1 : (now - T0) / 1000, st, lv);
  }
  requestAnimationFrame(frame);
  return {
    set state(s) { st = s || "idle"; },
    get state() { return st; },
    level(x) { target = Math.max(0, Math.min(1, x)); },
    dispose() { alive = false; },
  };
}

// What sits on the empty workplane of a new board (board.html?new=1) until the first GLB arrives: a faint "ghost
// board" in the middle whose copper traces keep routing themselves (stroke-dash drawing on a ~8 s loop) with mm ticks
// lighting up along its top edge, and a few real library boards resting at the edges at slight angles, drifting a
// few px on springs. While a design build runs it turns into the board taking shape: the outline takes the size the
// engine reports (assembly:built board {w, h}) and each picked part pops in as a small grey placeholder.
// Reduced motion: everything is static. Never takes pointer events.
//
//   const bd = createStartBackdrop(stageEl, { boards: [{ slug, title }, …] });
//   bd.building(true); bd.size({ w: 52, h: 38 }); bd.parts([{ ref: "U1", value: "ESP32" }, …]); bd.remove();
import { spring, reducedMotion } from "../ui/spring.js";
import { boardFiles, keyBackdrop } from "../common.js";

const NS = "http://www.w3.org/2000/svg";
const W = 400, H = 260;
// traces between pads, in the ghost's 400 x 260 box: Manhattan routes with 45° corners, like a router would draw them
const TRACES = [
  "M70 70 H150 L170 90 V130", "M70 100 H120 L140 120 H170", "M230 130 V90 L250 70 H330", "M230 160 H280 L300 180 H330",
  "M170 160 V190 L150 210 H90", "M200 190 V215 H300", "M110 130 V170", "M300 100 V140 L320 160",
];
const PADS = [[70, 70], [70, 100], [330, 70], [330, 180], [90, 210], [300, 215], [110, 130], [110, 170], [300, 100], [320, 160]];
const hash = (s) => { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return (h >>> 0) / 4294967296; };

export function createStartBackdrop(host, { boards = [] } = {}) {
  const root = document.createElement("div");
  root.className = "sbd";
  root.setAttribute("aria-hidden", "true");
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "sbd-ghost");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  let html = `<rect class="sbd-outline" x="30" y="30" width="${W - 60}" height="${H - 60}" rx="16"/>`;
  html += `<g class="sbd-ticks">`;
  for (let i = 0; i <= 10; i++) {
    const x = 30 + (i * (W - 60)) / 10;
    html += `<line x1="${x}" y1="22" x2="${x}" y2="${i % 5 ? 26 : 18}" style="--k:${i}"/>`;
    if (i % 2 === 0) html += `<text x="${x}" y="13" style="--k:${i}">${i * 10}</text>`;
  }
  html += `</g><g class="sbd-traces">${TRACES.map((d, i) => `<path d="${d}" pathLength="100" style="--k:${i}"/>`).join("")}</g>`;
  html += `<g class="sbd-pads">${PADS.map(([x, y], i) => `<circle cx="${x}" cy="${y}" r="5" style="--k:${i}"/>`).join("")}</g>`;
  html += `<g class="sbd-chips"><rect x="170" y="110" width="60" height="70" rx="6"/><rect x="60" y="150" width="24" height="16" rx="3"/><rect x="290" y="60" width="26" height="18" rx="3"/></g>`;
  html += `<g class="sbd-parts"></g>`;
  svg.innerHTML = html;
  const outline = svg.querySelector(".sbd-outline"), partsG = svg.querySelector(".sbd-parts");
  root.append(svg);

  // library boards at the edges, each on its own slow drift spring
  const spots = [[0.06, 0.12, -7], [0.8, 0.08, 6], [0.04, 0.7, 5], [0.82, 0.68, -5], [0.44, 0.84, 3]];
  const drifts = [];
  boards.slice(0, spots.length).forEach((b, i) => {
    const [x, y, rot] = spots[i];
    const t = document.createElement("div");
    t.className = "sbd-thumb";
    t.style.left = x * 100 + "%"; t.style.top = y * 100 + "%"; t.style.rotate = rot + "deg";
    const img = new Image(); img.decoding = "async"; img.alt = "";
    img.onload = () => { const cv = keyBackdrop(img); t.append(cv || img); t.classList.add("in"); };
    img.onerror = () => t.remove();
    img.src = boardFiles(b.slug).thumb;
    root.append(t);
    const s = spring({ x: 0, y: 0 }, { preset: "gentle", precision: 0.01, onUpdate: (v) => { t.style.translate = `${v.x.toFixed(2)}px ${v.y.toFixed(2)}px`; } });
    drifts.push(s);
  });
  let driftT = 0;
  const drift = () => {
    if (!reducedMotion()) for (const s of drifts) s.set({ x: (Math.random() - 0.5) * 12, y: (Math.random() - 0.5) * 10 });
    driftT = setTimeout(drift, 2600 + Math.random() * 1800);
  };
  driftT = setTimeout(drift, 400);
  host.append(root);

  let shown = new Set(), box = { x: 30, y: 30, w: W - 60, h: H - 60 };
  return {
    el: root,
    building(on) { root.classList.toggle("building", !!on); },
    // the engine's board size in mm: keep the ghost's 400 x 260 box, fit an outline of that aspect into it
    size(b) {
      if (!b || !b.w || !b.h) return;
      const k = Math.min((W - 60) / b.w, (H - 60) / b.h);
      box = { w: b.w * k, h: b.h * k, x: (W - b.w * k) / 2, y: (H - b.h * k) / 2 };
      for (const [a, v] of [["x", box.x], ["y", box.y], ["width", box.w], ["height", box.h]]) outline.setAttribute(a, v.toFixed(1));
      root.classList.add("sized");
    },
    // each picked part becomes a small grey placeholder at a stable spot inside the outline, popping in one by one
    parts(list = []) {
      let n = 0;
      for (const p of list) {
        const ref = typeof p === "string" ? p : p?.ref;
        if (!ref || shown.has(ref)) continue;
        shown.add(ref);
        const big = /^(U|IC|J|USB|MOD)/i.test(ref), w = big ? 34 : 14, h = big ? 26 : 9;
        const x = box.x + 14 + hash(ref + "x") * (box.w - 28 - w), y = box.y + 14 + hash(ref + "y") * (box.h - 28 - h);
        const g = document.createElementNS(NS, "g");
        g.setAttribute("class", "sbd-part");
        g.style.setProperty("--d", `${(n++ * 70) % 1400}ms`);
        g.innerHTML = `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w}" height="${h}" rx="${big ? 4 : 2}"/>` + (big ? `<text x="${(x + w / 2).toFixed(1)}" y="${(y + h / 2 + 3).toFixed(1)}">${ref.replace(/[<&>]/g, "")}</text>` : "");
        partsG.append(g);
      }
    },
    remove() {
      clearTimeout(driftT);
      for (const s of drifts) s.stop?.();
      root.classList.add("gone");
      setTimeout(() => root.remove(), 600);
    },
  };
}

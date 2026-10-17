// Selection overlay (SVG over the canvas): black dot handles at the selection's bounding-box corners and edge
// midpoints, a height handle on top, a dashed footprint on the workplane with dashed guides down to it and out to the
// rulers, and the box's width / depth / height in mm. The 3D outline itself is drawn by the viewer.
import * as THREE from "three";

const NS = "http://www.w3.org/2000/svg";
const mk = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v); return e; };

export function createSelectionOverlay(svg, viewer) {
  let refs = [];
  const g = mk("g"); svg.append(g);
  function set(list) { refs = list || []; draw(); }
  function draw() {
    g.textContent = "";
    if (!refs.length) return;
    const box = new THREE.Box3();
    for (const r of refs) { const b = viewer.partBox(r); if (b) box.union(b); }
    if (box.isEmpty()) return;
    const { w, h } = viewer.size;
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    const P = (x, y, z) => viewer.worldToScreen(new THREE.Vector3(x, y, z));
    const { min: a, max: b } = box;
    const wp = viewer.workplane;
    const y0 = a.y, y1 = b.y;
    const bottom = [[a.x, a.z], [b.x, a.z], [b.x, b.z], [a.x, b.z]];
    const foot = bottom.map(([x, z]) => P(x, 0, z));
    const base = bottom.map(([x, z]) => P(x, y0, z));
    const top = bottom.map(([x, z]) => P(x, y1, z));
    if (base.some((p) => p.behind)) return;
    const line = (p, q, cls) => g.append(mk("line", { x1: p.x.toFixed(1), y1: p.y.toFixed(1), x2: q.x.toFixed(1), y2: q.y.toFixed(1), class: cls }));
    const poly = (pts, cls) => g.append(mk("polygon", { points: pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" "), class: cls }));
    // dashed footprint on the workplane, verticals from the box down to it, guides out to the rulers
    poly(foot, "so-foot");
    for (let i = 0; i < 4; i++) line(top[i], foot[i], "so-guide");
    if (wp) {
      line(P(a.x, 0, b.z), P(a.x, 0, wp.z1), "so-guide faint"); line(P(b.x, 0, b.z), P(b.x, 0, wp.z1), "so-guide faint");
      line(P(a.x, 0, a.z), P(wp.x0, 0, a.z), "so-guide faint"); line(P(a.x, 0, b.z), P(wp.x0, 0, b.z), "so-guide faint");
    }
    poly(base, "so-box");
    // handles: corners, edge midpoints, height
    const mid = (p, q) => ({ x: (p.x + q.x) / 2, y: (p.y + q.y) / 2 });
    for (let i = 0; i < 4; i++) {
      const m = mid(base[i], base[(i + 1) % 4]);
      g.append(mk("circle", { cx: m.x.toFixed(1), cy: m.y.toFixed(1), r: 3.2, class: "so-h mid" }));
    }
    for (const p of base) g.append(mk("circle", { cx: p.x.toFixed(1), cy: p.y.toFixed(1), r: 4.4, class: "so-h" }));
    const tc = P((a.x + b.x) / 2, y1, (a.z + b.z) / 2), tl = P((a.x + b.x) / 2, y1 + (b.y - a.y) * 0.25 + 0.0015, (a.z + b.z) / 2);
    line(tc, tl, "so-stem");
    g.append(mk("circle", { cx: tl.x.toFixed(1), cy: tl.y.toFixed(1), r: 4.4, class: "so-h top" }));
    // dimensions in mm
    const label = (p, txt, cls = "") => { const t = mk("text", { x: p.x.toFixed(1), y: p.y.toFixed(1), class: "so-dim " + cls }); t.textContent = txt; g.append(t); };
    const mm = (v) => (v * 1000).toFixed(v * 1000 < 10 ? 2 : 1);
    const front = mid(foot[2], foot[3]), side = mid(foot[1], foot[2]);
    label({ x: front.x, y: front.y + 18 }, `${mm(b.x - a.x)} mm`);
    label({ x: side.x + 10, y: side.y + 4 }, `${mm(b.z - a.z)} mm`, "start");
    if (Math.hypot(tl.x - tc.x, tl.y - tc.y) > 14) label({ x: tl.x + 10, y: tl.y + 4 }, `${mm(b.y - a.y)} mm`, "start");
  }
  viewer.on("frame", draw);
  return { set, draw, get refs() { return refs; } };
}

// Where things go while the board is being made, in board millimetres (x right, y down): a believable placement for
// the picked parts (modules and connectors along the top edge, then each function group packed in rows, the way a
// person would start a layout), tracks between their real pads (an MST over the parts plus a few extra nets, routed
// with 45° corners, some through a pair of vias), a free spot for a part added to an existing board, and the tray
// the parts wait in. The real positions replace all of this when the board's own model arrives.
import { hash } from "./stage.js";

// a footprint's body rect in footprint mm: the model's footprint (box: [x0, h0, y0, x1, h1, y1]) and its pads
export function bodyRect(info) {
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  const add = (a, b, c, d) => { x0 = Math.min(x0, a); y0 = Math.min(y0, b); x1 = Math.max(x1, c); y1 = Math.max(y1, d); };
  if (info?.box) add(info.box[0], info.box[2], info.box[3], info.box[5]);
  for (const p of info?.pads || []) { const r = Math.max(p[2], p[3]) / 2; add(p[0] - r, p[1] - r, p[0] + r, p[1] + r); }
  if (x0 > x1) { const c = info?.court || [-1, -1, 1, 1]; add(c[0], c[1], c[2], c[3]); }
  return [x0, y0, x1, y1];
}
// footprint mm -> board mm for a part at (x, y) turned by rot (radians, KiCad's counter-clockwise = scene +Y)
export function toBoardPt(px, py, rot, x, y) { const c = Math.cos(rot), s = Math.sin(rot); return [px + x * c + y * s, py - x * s + y * c]; }
export function placedRect(info, t) {
  const [a, b, c, d] = bodyRect(info);
  const pts = [[a, b], [c, b], [a, d], [c, d]].map(([x, y]) => toBoardPt(t.x, t.y, t.rot, x, y));
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}
export function placedPads(info, t) {
  return (info?.pads || []).map((p) => {
    const [x, y] = toBoardPt(t.x, t.y, t.rot, p[0], p[1]);
    return { x, y, w: p[2], h: p[3], shape: p[4], drill: p[5], rot: (p[6] * Math.PI) / 180 + t.rot, npth: !!p[7] };
  });
}
export function placedSilk(info, t) {
  return (info?.silk || []).map((s) => {
    const pts = [];
    for (let i = 1; i + 1 < s.length; i += 2) pts.push(toBoardPt(t.x, t.y, t.rot, s[i], s[i + 1]));
    return { w: s[0], pts };
  });
}

const pre = (ref) => (String(ref).match(/^[A-Za-z_]+/) || [""])[0].toUpperCase();
const isEdge = (p) => /^(J|USB|CN|P|CONN)$/.test(pre(p.ref)) || /Connector_USB|BarrelJack|USB_C|USB_Micro/.test(p.key || "");
const isModule = (p) => /RF_Module|Espressif|Module:/.test(p.key || "");

// a board size that fits the parts with room to route (mm)
export function estimateSize(parts) {
  let area = 0, maxW = 0, maxH = 0;
  for (const p of parts) {
    const [a, b, c, d] = bodyRect(p.info), w = c - a + 1.6, h = d - b + 1.6;
    area += w * h; maxW = Math.max(maxW, w); maxH = Math.max(maxH, h);
  }
  const A = area * 2.3 + 140;
  let w = Math.sqrt(A * 1.35), h = A / w;
  w = Math.max(w, maxW + 6); h = Math.max(h, maxH + 6);
  return { w: Math.ceil(Math.min(160, Math.max(22, w))), h: Math.ceil(Math.min(120, Math.max(16, h))) };
}

// shelf packing inside the board (centred on 0, 0): returns { place: Map(ref -> {x, y, rot}), fits }
export function pack(parts, { w, h }, margin = 2) {
  const groups = [];
  for (const p of parts) if (!groups.includes(p.group || "")) groups.push(p.group || "");
  const items = parts.map((p) => {
    const [a, b, c, d] = bodyRect(p.info);
    return { p, a, b, w: c - a, h: d - b, area: (c - a) * (d - b), first: isModule(p) ? 0 : isEdge(p) ? 1 : 2, g: groups.indexOf(p.group || "") };
  }).sort((x, y) => x.first - y.first || x.g - y.g || y.area - x.area || String(x.p.ref).localeCompare(String(y.p.ref), undefined, { numeric: true }));
  const gap = 1.3, X0 = -w / 2 + margin, X1 = w / 2 - margin, Y1 = h / 2 - margin;
  const rows = [];
  let row = null, y = -h / 2 + margin, fits = true;
  for (const it of items) {
    if (!row || row.x + it.w > X1 + 1e-6 || (row.first < 2 && it.first === 2 && row.items.length)) {
      if (row) y += row.h + gap * 1.4;
      row = { x: X0, y, h: 0, items: [], first: it.first };
      rows.push(row);
    }
    row.items.push({ it, x: row.x });
    row.x += it.w + gap;
    row.h = Math.max(row.h, it.h);
  }
  const place = new Map();
  if (rows.length) {
    const last = rows[rows.length - 1];
    if (last.y + last.h > Y1 + 1e-6) fits = false;
    // spread the rows over the board's height, centre each row
    const used = last.y + last.h - rows[0].y, spare = Math.max(0, (h - 2 * margin) - used), step = rows.length > 1 ? spare / (rows.length - 1) : spare / 2;
    rows.forEach((r, i) => {
      const width = r.x - gap - X0, dx = (X1 - X0 - width) / 2, dy = rows.length > 1 ? step * i : step;
      for (const { it, x } of r.items) {
        // anchor = footprint origin: shift by the body rect's offset from it, sit each item on the row's top line
        const cx = x + dx - it.a, cy = r.y + dy - it.b + (r.first < 2 ? 0 : (r.h - it.h) / 2);
        place.set(it.p.ref, { x: cx, y: cy, rot: 0 });
      }
    });
  }
  return { place, fits };
}

// tracks between parts: [{ key, pts: [[x, y]…], w, vias: [[x, y, s]] }]
export function planTraces(placed) {
  // placed: [{ ref, center: [x, y], pads: [{x, y, w, h, rot, npth}], power }]
  const P = placed.filter((p) => p.pads.some((q) => !q.npth));
  if (P.length < 2) return [];
  const d = (a, b) => Math.hypot(a.center[0] - b.center[0], a.center[1] - b.center[1]);
  // Prim's MST
  const inT = new Set([0]), edges = [];
  while (inT.size < P.length) {
    let best = null;
    for (const i of inT) for (let j = 0; j < P.length; j++) if (!inT.has(j)) { const dd = d(P[i], P[j]); if (!best || dd < best[2]) best = [i, j, dd]; }
    inT.add(best[1]); edges.push([best[0], best[1]]);
  }
  // a second net for parts with several pins
  for (let i = 0; i < P.length; i++) {
    if (P[i].pads.length < 3) continue;
    const near = P.map((q, j) => [j, d(P[i], q)]).filter(([j]) => j !== i).sort((a, b) => a[1] - b[1]);
    const extra = Math.min(near.length, P[i].pads.length >= 8 ? 3 : 1);
    for (let k = 0; k < extra; k++) { const j = near[k + 1]?.[0]; if (j == null) continue; if (!edges.some(([a, b]) => (a === i && b === j) || (a === j && b === i))) edges.push([i, j]); }
  }
  const used = new Map();
  const pickPad = (part, toward) => {
    let best = null, bd = 1e9;
    for (const q of part.pads) {
      if (q.npth) continue;
      const k = `${part.ref}:${q.x.toFixed(2)},${q.y.toFixed(2)}`;
      const dd = Math.hypot(q.x - toward[0], q.y - toward[1]) + (used.get(k) || 0) * 3.5;
      if (dd < bd) { bd = dd; best = { q, k }; }
    }
    used.set(best.k, (used.get(best.k) || 0) + 1);
    return best.q;
  };
  const out = [];
  for (const [i, j] of edges) {
    const A = P[i], B = P[j];
    const pa = pickPad(A, B.center), pb = pickPad(B, [pa.x, pa.y]);
    const key = `${A.ref}-${B.ref}-${pa.x.toFixed(1)}`;
    const pts = route(pa, A.center, pb, B.center, hash(key));
    const w = A.power || B.power ? 0.45 : 0.25;
    const len = pathLen(pts);
    if (len > 12 && hash(key + "v") < 0.3) {
      // through the back: top track to a via, the other end comes up through a second via
      const [a, rest] = splitAt(pts, len * 0.38), [, b] = splitAt(rest, pathLen(rest) * 0.55);
      const va = a[a.length - 1], vb = b[0];
      out.push({ key: key + "a", pts: a, w, vias: [[va[0], va[1], pathLen(a)]] });
      out.push({ key: key + "b", pts: b, w, vias: [[vb[0], vb[1], 0]] });
    } else out.push({ key, pts, w, vias: [] });
  }
  return out;
}
function pathLen(pts) { let l = 0; for (let i = 1; i < pts.length; i++) l += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); return l; }
function splitAt(pts, at) {
  let l = 0;
  for (let i = 1; i < pts.length; i++) {
    const s = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
    if (l + s >= at) { const k = (at - l) / (s || 1), m = [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * k, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * k]; return [[...pts.slice(0, i), m], [m, ...pts.slice(i)]]; }
    l += s;
  }
  return [pts, [pts[pts.length - 1]]];
}
// pad -> short escape away from its part -> 45° route -> escape -> pad
export function route(pa, ca, pb, cb, r) {
  const esc = (p, c) => {
    const dx = p.x - c[0], dy = p.y - c[1];
    const horiz = Math.abs(dx) * (p.h + 0.1) > Math.abs(dy) * (p.w + 0.1);
    const len = 0.5 + (horiz ? p.w : p.h) / 2;
    return horiz ? [p.x + Math.sign(dx || 1) * len, p.y] : [p.x, p.y + Math.sign(dy || 1) * len];
  };
  const a = [pa.x, pa.y], b = [pb.x, pb.y], e1 = esc(pa, ca), e2 = esc(pb, cb);
  const dx = e2[0] - e1[0], dy = e2[1] - e1[1], ax = Math.abs(dx), ay = Math.abs(dy), sx = Math.sign(dx), sy = Math.sign(dy);
  let mid;
  if (ax > ay) mid = r < 0.5 ? [e1[0] + sx * (ax - ay), e1[1]] : [e1[0] + sx * ay, e1[1] + sy * ay];
  else mid = r < 0.5 ? [e1[0], e1[1] + sy * (ay - ax)] : [e1[0] + sx * ax, e1[1] + sy * ax];
  const pts = [a, e1, mid, e2, b];
  // drop repeats and straight-through points
  const out = [];
  for (const p of pts) {
    const q = out[out.length - 1];
    if (q && Math.hypot(p[0] - q[0], p[1] - q[1]) < 0.05) continue;
    if (out.length >= 2) { const o = out[out.length - 2]; const cr = (q[0] - o[0]) * (p[1] - o[1]) - (q[1] - o[1]) * (p[0] - o[0]); if (Math.abs(cr) < 1e-4) out.pop(); }
    out.push(p);
  }
  return out;
}

// a free spot for a w x h part on an existing board, as close as possible to (ax, ay)
export function findSpot(occupied, rect, w, h, ax, ay) {
  const [X0, Y0, X1, Y1] = rect, m = 1.2, g = 0.5;
  const free = (cx, cy) => {
    const a = cx - w / 2, b = cy - h / 2, c = cx + w / 2, d = cy + h / 2;
    if (a < X0 + m || b < Y0 + m || c > X1 - m || d > Y1 - m) return false;
    for (const o of occupied) if (a < o[2] + g && c > o[0] - g && b < o[3] + g && d > o[1] - g) return false;
    return true;
  };
  const step = 0.75, maxR = Math.max(X1 - X0, Y1 - Y0);
  for (let r = 0; r <= maxR; r += step) {
    const n = Math.max(1, Math.round((2 * Math.PI * r) / step));
    for (let i = 0; i < n; i++) { const t = (i / n) * Math.PI * 2, x = ax + Math.cos(t) * r, y = ay + Math.sin(t) * r; if (free(x, y)) return [x, y]; }
  }
  return null;
}

// tray slots in rows (tray mm: x right, z towards the viewer), one slot per footprint, its parts fanned behind it
export function trayLayout(slots, maxW) {
  const gap = 4, rows = [];
  let row = null;
  for (const s of slots) {
    const sw = Math.max(7, s.dw) + gap;
    if (!row || (row.w + sw > maxW && row.items.length)) { row = { w: 0, d: 0, items: [] }; rows.push(row); }
    row.items.push({ s, x: row.w + sw / 2 }); row.w += sw; row.d = Math.max(row.d, Math.max(7, s.dd) + gap);
  }
  const W = Math.max(30, ...rows.map((r) => r.w)) + 6, D = rows.reduce((a, r) => a + r.d, 0) + 6;
  const pos = new Map();
  let z = -D / 2 + 3;
  for (const r of rows) {
    const off = -r.w / 2;
    for (const { s, x } of r.items) pos.set(s.key, [off + x, z + r.d / 2]);
    z += r.d;
  }
  return { w: W, d: D, pos };
}

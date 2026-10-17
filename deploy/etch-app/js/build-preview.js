// The board taking shape in the 3D scene while Etch builds it (board-viewer.js mounts it; viewer.build is this API).
// A new board: a translucent solder-mask slab at the reported size with a soft light sweeping across it; each picked
// part arrives as a rounded block sized from its footprint class (0402 / 0603 chips, SOT, SOIC / QFN, modules,
// connectors, through-hole headers) and drops in on a spring, packed in rows on the slab; routing draws copper traces
// between them, the checks run a shimmer over the slab. When the real GLB is loaded, finish() moves each block onto
// its part (matched by refdes), stretches it to the part's size and crossfades it into the real part, and the slab
// into the real board. A change on an existing board: no slab, only the new or changed parts arrive as blocks on the
// board's top face. Same scene lights as the board. Reduced motion: no drops, sweeps or shimmer, short fades.
//
//   const b = createBuildPreview(THREE, ctx);   // ctx: { scene, invalidate(), reduced, parts(), partBox(ref),
//                                               //        boardRect() (world, metres), boardTop() (world y), hasModel() }
//   b.start({ onBoard }); b.size({ w, h }); b.parts([{ ref, value, footprint }]); b.phase("route" | "check");
//   await b.finish();  b.cancel();  b.step(dt) from the render loop -> true while something moves
export function createBuildPreview(THREE, ctx) {
  const root = new THREE.Group();
  root.name = "etch-build-preview";
  ctx.scene.add(root);
  const MM = 0.001;
  const geoCache = new Map();
  let st = null; // the running preview

  // ---- rounded blocks: an extruded rounded rectangle with a small bevel, cached per size ----
  function block(w, d, h) {
    const key = `${w.toFixed(2)}x${d.toFixed(2)}x${h.toFixed(2)}`;
    if (geoCache.has(key)) return geoCache.get(key);
    const r = Math.min(w, d) * 0.18, bev = Math.min(h * 0.22, r * 0.8, 0.5);
    const s = new THREE.Shape(), x = -w / 2 + bev, y = -d / 2 + bev, W = w - 2 * bev, D = d - 2 * bev, rr = Math.max(0.05, Math.min(r, W / 2, D / 2));
    s.moveTo(x + rr, y); s.lineTo(x + W - rr, y); s.quadraticCurveTo(x + W, y, x + W, y + rr); s.lineTo(x + W, y + D - rr);
    s.quadraticCurveTo(x + W, y + D, x + W - rr, y + D); s.lineTo(x + rr, y + D); s.quadraticCurveTo(x, y + D, x, y + D - rr);
    s.lineTo(x, y + rr); s.quadraticCurveTo(x, y, x + rr, y);
    const g = new THREE.ExtrudeGeometry(s, { depth: Math.max(0.01, h - 2 * bev), bevelEnabled: true, bevelThickness: bev, bevelSize: bev, bevelSegments: 2, curveSegments: 3 });
    g.rotateX(-Math.PI / 2); // extrude along +y
    g.translate(0, bev, 0); // sits on y = 0
    g.scale(MM, MM, MM);
    g.computeVertexNormals();
    geoCache.set(key, g);
    return g;
  }

  // footprint class -> size (mm) and colour
  const BODY = 0x2a2e35, TAN = 0xc8b48e, METAL = 0xb9bfc7, BLACK = 0x1c1d20, WHITE = 0xeeeeea;
  function classify(p) {
    const ref = String(p.ref || ""), t = `${p.value || ""} ${p.footprint || ""} ${p.piece || ""} ${p.name || ""}`.toLowerCase();
    const pre = (ref.match(/^[A-Za-z_]+/) || [""])[0].toUpperCase();
    const n = +(t.match(/1x0?(\d+)/) || [])[1] || 0;
    if (/wroom|wrover|module|esp32|rp2040.*mod|nrf52.*mod|_mod\b/.test(t) || pre === "MOD") return { w: 18, d: 25.5, h: 3.1, c: METAL, k: "module" };
    if (/pin.?header|pinheader|header|conn_?\d|1x\d+/.test(t) && !/usb/.test(t)) { const k = Math.max(2, Math.min(20, n || 4)); return { w: 2.54 * k, d: 2.54, h: 8.5, c: BLACK, k: "header" }; }
    if (/usb|type.?c|jack|rj45|barrel|connector/.test(t) || pre === "J" || pre === "USB" || pre === "P" || pre === "CN") return { w: 9, d: 7.4, h: 3.3, c: METAL, k: "connector" };
    if (/qfn|dfn|lga/.test(t)) return { w: 5, d: 5, h: 0.9, c: BODY, k: "qfn" };
    if (/soic|tssop|ssop|so-?\d|msop|qfp/.test(t)) return { w: 5, d: 4, h: 1.6, c: BODY, k: "soic" };
    if (/sot|sod-?\d|sc-?70|to-?92|to-?252|dpak/.test(t) || pre === "Q") return { w: 3, d: 1.5, h: 1.1, c: BODY, k: "sot" };
    if (pre === "U" || pre === "IC") return { w: 5, d: 5, h: 1.2, c: BODY, k: "ic" };
    if (pre === "SW" || /button|switch|tact/.test(t)) return { w: 6, d: 6, h: 3.5, c: BLACK, k: "button" };
    if (pre === "Y" || pre === "X" || /crystal|xtal/.test(t)) return { w: 3.2, d: 2.5, h: 0.8, c: METAL, k: "crystal" };
    if (pre === "BT" || pre === "BAT" || /battery|cr20|coin/.test(t)) return { w: 20, d: 20, h: 4, c: METAL, k: "battery" };
    if (pre === "BZ" || /buzzer|piezo/.test(t)) return { w: 12, d: 12, h: 7, c: BLACK, k: "buzzer" };
    if (pre === "D" || pre === "LED" || /\bled\b/.test(t)) return { w: 1.6, d: 0.8, h: 0.6, c: /red/.test(t) ? 0xd64545 : WHITE, k: "led" };
    if (/0402/.test(t)) return { w: 1, d: 0.5, h: 0.35, c: pre === "R" ? BLACK : TAN, k: "chip" };
    if (/0805/.test(t)) return { w: 2, d: 1.25, h: 0.5, c: pre === "R" ? BLACK : TAN, k: "chip" };
    if (/1206/.test(t)) return { w: 3.2, d: 1.6, h: 0.55, c: pre === "R" ? BLACK : TAN, k: "chip" };
    if (pre === "R" || pre === "C" || pre === "L" || pre === "FB") return { w: 1.6, d: 0.8, h: 0.45, c: pre === "R" ? BLACK : TAN, k: "chip" };
    return { w: 3, d: 3, h: 1, c: BODY, k: "part" };
  }

  // ---- materials ----
  const slabMat = new THREE.MeshStandardMaterial({ color: 0x1f7a4a, roughness: 0.5, metalness: 0.05, transparent: true, opacity: 0, depthWrite: false });
  const edgeMat = new THREE.LineBasicMaterial({ color: 0x8fd9b0, transparent: true, opacity: 0 });
  const copper = new THREE.MeshStandardMaterial({ color: 0xd59b4a, roughness: 0.32, metalness: 0.85, transparent: true, opacity: 0.95 });
  const scanTex = (() => {
    const c = document.createElement("canvas"); c.width = 128; c.height = 4;
    const g = c.getContext("2d"), gr = g.createLinearGradient(0, 0, 128, 0);
    gr.addColorStop(0, "rgba(190,235,255,0)"); gr.addColorStop(0.5, "rgba(220,245,255,0.9)"); gr.addColorStop(1, "rgba(190,235,255,0)");
    g.fillStyle = gr; g.fillRect(0, 0, 128, 4);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
  })();
  const scanMat = new THREE.MeshBasicMaterial({ map: scanTex, transparent: true, opacity: 0, depthWrite: false, toneMapped: false });

  // a critically damped-ish spring on a few numbers, stepped by step(dt)
  const sp = (v, k = 170, c = 15) => ({ v, x: v, t: v, vel: 0, k, c });
  function stepSp(s, dt) {
    if (ctx.reduced) { s.x = s.t; s.vel = 0; return false; }
    for (let left = dt; left > 1e-6; left -= 1 / 240) { const h = Math.min(1 / 240, left); s.vel += (-s.k * (s.x - s.t) - s.c * s.vel) * h; s.x += s.vel * h; }
    if (Math.abs(s.x - s.t) < 1e-6 && Math.abs(s.vel) < 1e-5) { s.x = s.t; s.vel = 0; return false; }
    return true;
  }

  function clear() {
    for (const o of [...root.children]) { root.remove(o); o.traverse?.((m) => { if (m.material && m.material !== slabMat && m.material !== copper && m.material !== scanMat && m.material !== edgeMat) m.material.dispose?.(); }); }
    st = null;
  }

  // ---- start: a slab for a new board (on the empty workplane), or the existing board's top face ----
  function start({ onBoard = false, w = 46, h = 32 } = {}) {
    clear();
    st = { onBoard, w, h, t: 0, blocks: new Map(), order: [], traces: [], phase: "pick", fade: sp(0, 90, 18), sw: sp(w), sh: sp(h), done: false };
    if (!onBoard) {
      const slab = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), slabMat);
      slab.renderOrder = 1;
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)), edgeMat);
      const scan = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), scanMat);
      scan.rotation.x = -Math.PI / 2; scan.renderOrder = 3;
      root.add(slab, edges, scan);
      Object.assign(st, { slab, edges, scan });
    }
    st.fade.t = 1;
    ctx.invalidate();
  }
  const topY = () => (st?.onBoard ? ctx.boardTop() : 1.6 * MM);
  // the area blocks are packed into, in world metres: the slab, or the existing board's outline
  function area() {
    if (st.onBoard) { const r = ctx.boardRect(); if (r) return r; }
    const w = st.sw.t * MM, h = st.sh.t * MM;
    return { x0: -w / 2, z0: -h / 2, x1: w / 2, z1: h / 2 };
  }
  function size(b) {
    if (!st || !b || !b.w || !b.h || st.onBoard) return;
    st.sw.t = b.w; st.sh.t = b.h; st.sized = true;
    layout();
    ctx.invalidate();
  }

  // shelf packing, biggest first, a 2 mm margin; on an existing board the new parts go along its free edge
  function layout() {
    if (!st) return;
    const a = area(), m = 2 * MM, W = a.x1 - a.x0 - 2 * m, gap = 1.2 * MM;
    const list = [...st.blocks.values()].filter((b) => !b.final).sort((p, q) => q.size.w * q.size.d - p.size.w * p.size.d);
    let x = 0, z = 0, row = 0;
    for (const b of list) {
      const bw = b.size.w * MM, bd = b.size.d * MM;
      if (x > 0 && x + bw > W) { x = 0; z += row + gap; row = 0; }
      b.tx.t = a.x0 + m + x + bw / 2;
      b.tz.t = st.onBoard ? a.z1 - m - z - bd / 2 : a.z0 + m + z + bd / 2;
      x += bw + gap; row = Math.max(row, bd);
    }
    // a new board's blocks are centred on the slab
    if (!st.onBoard && list.length) {
      const zs = list.map((b) => b.tz.t), xs = list.map((b) => b.tx.t);
      const dz = -(Math.min(...zs) + Math.max(...zs)) / 2, dx = -(Math.min(...xs) + Math.max(...xs)) / 2;
      for (const b of list) { b.tz.t += dz * 0.9; b.tx.t += dx * 0.9; }
    }
  }

  // ---- parts: each one a block that drops in with a spring, staggered ----
  function addParts(list = []) {
    if (!st) return;
    let i = 0;
    for (const p of list) {
      const ref = typeof p === "string" ? p : p?.ref;
      if (!ref || st.blocks.has(ref)) continue;
      const size = classify(typeof p === "string" ? { ref } : p);
      const mat = new THREE.MeshStandardMaterial({ color: size.c, roughness: size.c === METAL ? 0.35 : 0.55, metalness: size.c === METAL ? 0.7 : 0.05, transparent: true, opacity: 0, emissive: 0x000000 });
      const mesh = new THREE.Mesh(block(size.w, size.d, size.h), mat);
      mesh.renderOrder = 2;
      root.add(mesh);
      const b = { ref, size, mesh, mat, tx: sp(0), tz: sp(0), ty: sp(14 * MM, 150, 11), op: sp(0, 140, 20), sx: sp(1), sy: sp(1), sz: sp(1), delay: (st.order.length ? 0.07 : 0) * i++, final: false };
      st.blocks.set(ref, b); st.order.push(ref);
    }
    layout();
    for (const b of st.blocks.values()) if (b.tx.x === 0 && b.tz.x === 0) { b.tx.x = b.tx.t; b.tz.x = b.tz.t; }
    for (const b of st.blocks.values()) { b.ty.t = 0; b.op.t = 1; }
    ctx.invalidate();
  }

  // ---- routing: copper traces between neighbouring blocks, drawn one after another ----
  function route() {
    if (!st || st.traces.length) return;
    st.tRoute = st.t;
    const list = st.order.map((r) => st.blocks.get(r)).filter(Boolean);
    if (list.length < 2) return;
    const y = topY() + 0.04 * MM, w = 0.3 * MM;
    let k = 0;
    for (let i = 0; i < list.length - 1 && k < 24; i++) {
      const a = list[i], b = list[(i + 1 + (i % 3)) % list.length];
      if (a === b) continue;
      const ax = a.tx.t, az = a.tz.t, bx = b.tx.t, bz = b.tz.t;
      const pts = [[ax, az], [bx, az], [bx, bz]]; // an L route
      for (let s = 0; s < 2; s++) {
        const [x0, z0] = pts[s], [x1, z1] = pts[s + 1];
        const len = Math.hypot(x1 - x0, z1 - z0); if (len < 0.4 * MM) continue;
        const seg = new THREE.Mesh(new THREE.BoxGeometry(1, 0.035 * MM, w), copper);
        seg.position.set((x0 + x1) / 2, y, (z0 + z1) / 2);
        seg.rotation.y = -Math.atan2(z1 - z0, x1 - x0);
        seg.scale.set(ctx.reduced ? len : 1e-6, 1, 1);
        seg.renderOrder = 1;
        seg.userData = { len, x0, z0, x1, z1, at: k * 0.12 };
        root.add(seg); st.traces.push(seg); k++;
      }
    }
    ctx.invalidate();
  }
  function phase(p) {
    if (!st) return;
    st.phase = p;
    if (p === "route") route();
    ctx.invalidate();
  }

  // ---- the frame: slab size and fade, the sweep, drops, trace drawing, the shimmer ----
  function step(dt) {
    if (!st) return false;
    st.t += dt;
    let busy = !ctx.reduced && !st.done; // the sweep and shimmer keep running while the build does
    busy = stepSp(st.fade, dt) || busy;
    busy = stepSp(st.sw, dt) || busy; busy = stepSp(st.sh, dt) || busy;
    if (st.slab) {
      const w = st.sw.x * MM, h = st.sh.x * MM, th = 1.6 * MM;
      st.slab.scale.set(w, th, h); st.slab.position.set(st.slab.userData.ox || 0, th / 2, st.slab.userData.oz || 0);
      st.edges.scale.copy(st.slab.scale); st.edges.position.copy(st.slab.position);
      const shimmer = st.phase === "check" && !ctx.reduced ? 0.06 * (0.5 + 0.5 * Math.sin(st.t * 6)) : 0;
      slabMat.opacity = (0.5 + shimmer) * st.fade.x; edgeMat.opacity = 0.55 * st.fade.x;
      slabMat.emissive.setRGB(shimmer * 1.6, shimmer * 2.4, shimmer * 2);
      // the sweep: a soft band crossing the slab every 2.6 s
      const u = ctx.reduced ? 0.5 : (st.t % 2.6) / 2.6;
      st.scan.scale.set(Math.max(w * 0.22, 4 * MM), h * 0.98, 1);
      st.scan.position.set(-w / 2 + u * w + (st.slab.userData.ox || 0), th + 0.06 * MM, st.slab.userData.oz || 0);
      scanMat.opacity = (st.done || ctx.reduced ? 0 : 0.55) * st.fade.x * Math.sin(Math.PI * u);
    }
    const y0 = topY();
    for (const b of st.blocks.values()) {
      if (b.delay > 0) { b.delay -= dt; busy = true; continue; }
      for (const s of [b.tx, b.tz, b.ty, b.op, b.sx, b.sy, b.sz]) busy = stepSp(s, dt) || busy;
      b.mesh.position.set(b.tx.x, (b.final ? b.fy : y0) + b.ty.x, b.tz.x);
      b.mesh.scale.set(b.sx.x, b.sy.x, b.sz.x);
      b.mat.opacity = Math.max(0, Math.min(1, b.op.x)) * (b.final ? 1 : st.fade.x || 1);
      const sh = st.phase === "check" && !ctx.reduced && !b.final ? 0.05 * (0.5 + 0.5 * Math.sin(st.t * 6 - b.tx.x * 900)) : 0;
      b.mat.emissive.setRGB(sh, sh * 1.4, sh * 1.8);
    }
    for (const s of st.traces) {
      const d = s.userData, f = ctx.reduced ? 1 : Math.max(0, Math.min(1, (st.t - st.tRoute - d.at) / 0.35));
      if (f < 1) busy = true;
      const len = Math.max(1e-6, d.len * f);
      s.scale.x = len;
      s.position.set(d.x0 + ((d.x1 - d.x0) * f) / 2, s.position.y, d.z0 + ((d.z1 - d.z0) * f) / 2);
      s.material.opacity = 0.95 * (st.fade.x || 0);
    }
    if (st.fadeParts) busy = st.fadeParts(dt) || busy;
    if (st.done && !busy) { clear(); return false; }
    return busy;
  }

  // ---- finish: blocks onto their real parts, stretched to their size, then crossfaded into them ----
  function finish() {
    if (!st) return Promise.resolve();
    const real = ctx.parts();
    st.done = true; st.phase = "done";
    const fades = [];
    for (const b of st.blocks.values()) {
      const box = real[b.ref] ? ctx.partBox(b.ref) : null;
      b.final = true;
      if (!box) { b.fy = topY(); b.op.t = 0; b.sx.t = b.sz.t = 0.4; b.sy.t = 0.4; continue; }
      const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
      b.fy = box.min.y; b.ty.x = (b.mesh.position.y - box.min.y); b.ty.t = 0;
      b.tx.t = c.x; b.tz.t = c.z;
      b.sx.t = Math.max(0.2, s.x / (b.size.w * MM)); b.sz.t = Math.max(0.2, s.z / (b.size.d * MM)); b.sy.t = Math.max(0.2, s.y / (b.size.h * MM));
      b.op.t = 1;
      fades.push({ b, mats: real[b.ref].mats || [], t: ctx.reduced ? 0.1 : -0.45 }); // the real part fades in once the block is there
    }
    // the slab grows to the real board's outline, then fades into it
    const r = ctx.boardRect();
    if (st.slab && r) {
      st.sw.t = (r.x1 - r.x0) / MM; st.sh.t = (r.z1 - r.z0) / MM;
      st.slab.userData.ox = (r.x0 + r.x1) / 2; st.slab.userData.oz = (r.z0 + r.z1) / 2;
    }
    for (const f of fades) for (const m of f.mats) { m.userData.op ??= { t: m.transparent, o: m.opacity }; m.transparent = true; m.opacity = 0; m.needsUpdate = true; }
    let fadeT = ctx.reduced ? 0.3 : -0.35;
    st.fadeParts = (dt) => {
      let busy = false; fadeT += dt;
      st.fade.t = fadeT > 0 ? 0 : 1;
      for (const f of fades) {
        f.t += dt;
        const k = Math.max(0, Math.min(1, f.t / 0.4));
        for (const m of f.mats) { if (!m.userData.op) continue; m.opacity = m.userData.op.o * k; if (k >= 1) { m.transparent = m.userData.op.t; m.opacity = m.userData.op.o; m.needsUpdate = true; delete m.userData.op; } }
        f.b.op.t = 1 - k;
        if (k < 1) busy = true;
      }
      return busy || fadeT < 0.4;
    };
    ctx.invalidate();
    return new Promise((res) => setTimeout(res, ctx.reduced ? 300 : 1400));
  }
  function cancel() {
    if (!st) return;
    st.done = true;
    for (const b of st.blocks.values()) { b.final = true; b.fy = topY(); b.op.t = 0; }
    st.fade.t = 0;
    ctx.invalidate();
  }

  return {
    start, size, parts: addParts, phase, finish, cancel, step,
    get active() { return !!st && !st.done; },
    get blocks() { return st ? [...st.blocks.keys()] : []; },
  };
}

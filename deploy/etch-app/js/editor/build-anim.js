// The board being made, in the board viewer's own 3D scene, staged to the build's real events (docs/API.md "Events"):
//  1. screening and picking: the candidate parts appear as their real KiCad 3D models, hovering and slowly turning in
//     a tray above the workplane; rejected variants sink away; the pick leaves one tidy slot per footprint.
//  2. placing: an FR4 core grows to the planned size, a copper pour wipes in from the middle, each part's pads flash
//     onto the copper and the part flies from the tray onto them on a spring arc and touches down.
//  3. routing: copper tracks draw themselves pad to pad with a hot, softly blooming leading edge; vias pop.
//  4. checks: a light sweeps the board for every gate; a failed one pulses red where it failed, then the repair lifts
//     the parts nearby, re-seats them and re-draws their tracks.
//  5. fab files: the solder mask washes over (wet and translucent at its front), the silkscreen prints in.
//  6. the real board: every part flies to its real place (matched by refdes), the core takes the real outline, and
//     the real model crossfades in under it; the camera has been easing onto the viewer's home view all along.
// A change to an existing board animates only the new or changed parts and their tracks on the real board, which
// dims a little during the checks. Any orbit / pan / zoom hands the camera back to the person for good. Reduced
// motion: plain fades, no flights, sweeps or camera moves. The glow drops out on slow GPUs.
//
//   import * as buildAnim from "./build-anim.js";
//   buildAnim.createBuildAnim({ viewer });             // once, after the viewer exists
//   buildAnim.start({ mode: "design" | "modify", board });
//   buildAnim.event(ev);                               // every /etch/job event
//   buildAnim.finish({ glbReady });                    // on render:done / lead:done (glbReady: the new GLB is loaded)
import * as THREE from "three";
import { stageOf, reducedMotion, clamp, smooth, easeOut, easeInOut, approach, hash, springStep } from "./build-anim/stage.js";
import { loadIndex, resolve, loadModel, instance, setFade } from "./build-anim/parts3d.js";
import { createBoard, OCCLUDE_LAYER, MASK_COLORS } from "./build-anim/board.js";
import { createGlow, createScan, contactShadow } from "./build-anim/fx.js";
import { bodyRect, estimateSize, pack, planTraces, placedPads, placedSilk, placedRect, findSpot, trayLayout, route as routePath } from "./build-anim/layout.js";

const UP = new THREE.Vector3(0, 1, 0);
const LIGHT = new THREE.Vector2(0.5, -0.7); // where shadows fall per metre of height (the viewer's key light)
const HOME = { az: 25, el: 50 };
let current = null;

export function createBuildAnim({ viewer }) {
  if (current) current.dispose();
  const st = stageOf(viewer);
  let run = null; // the running animation
  let glow = null, quality = 2, perf = { t: 0, n: 0, sum: 0 };

  function ensureGlow() { if (!glow && quality >= 2) glow = createGlow(st.renderer); return glow; }
  st.after((renderer, scene, camera) => { if (run && run.glowOn && quality >= 2 && ensureGlow()) glow.render(scene, camera); });

  const api = {
    get active() { return !!run; },
    start(o = {}) { if (run) run.kill(); run = createRun(o); return api; },
    event(e) { if (run && e) run.event(e); },
    finish(o = {}) { if (run) run.finish(o); },
    cancel() { if (run) run.cancel(); },
    dispose() { if (run) run.kill(); run = null; glow?.dispose(); glow = null; if (current === api) current = null; },
  };
  current = api;

  // ------------------------------------------------------------------------------------------------------------
  function createRun({ mode = "design", board: host = {}, ref = null, at = null } = {}) {
    const reduced = reducedMotion();
    const modify = (mode === "modify" || mode === "change") && !viewer.isEmpty && !!viewer.boardRect();
    const root = new THREE.Group(); root.name = "etch-build-anim";
    st.scene.add(root);
    const frame = new THREE.Group(); root.add(frame);
    const partsAtStart = viewer.parts;
    let rect, top;
    if (modify) {
      frame.matrixAutoUpdate = false;
      viewer.root.updateMatrixWorld(true);
      frame.matrix.copy(viewer.root.matrixWorld);
      const r = viewer.boardRect();
      rect = [r.x0, r.y0, r.x1, r.y1]; top = viewer.boardTopLocal;
    } else {
      const a = viewer.emptyArea || { x0: -0.05, z0: -0.03, x1: 0.05, z1: 0.03 };
      const w = Math.min(60, (a.x1 - a.x0) * 600), h = Math.min(40, (a.z1 - a.z0) * 600);
      rect = [-w / 2, -h / 2, w / 2, h / 2]; top = 0.0016;
    }
    const board = createBoard(frame, { rect, top, substrate: !modify, mask: host?.meta?.board?.mask in MASK_COLORS ? host.meta.board.mask : "green" });
    if (!modify) { board.jump("core", 0); board.boardG.visible = false; }
    const scan = createScan(board.mm, rect);
    const R = { // the animation's state
      phase: "open", t: 0, queue: [], act: null, rush: 1, finishing: null, done: false, killed: false, lastEvent: 0,
      seenParts: partsAtStart, hidden: null, parts: new Map(), cands: new Map(), slots: [], picked: false, placed: false, iteration: 0, failed: [], sized: !modify ? null : { w: rect[2] - rect[0], h: rect[3] - rect[1] },
      cam: { owned: !reduced, az: modify ? null : 20, el: modify ? null : 58, box: null, drift: 0 }, dim: { x: 0, to: 0 }, saved: null,
    };
    const realOld = new Map(); // modify: real parts being replaced -> saved state
    const dimmed = [];

    // ---------------- tray ----------------
    const tray = new THREE.Group(); tray.name = "etch-build-tray"; root.add(tray);
    const trayTex = document.createElement("canvas");
    const trayMap = new THREE.CanvasTexture(trayTex); trayMap.colorSpace = THREE.SRGBColorSpace;
    const trayTop = new THREE.MeshPhysicalMaterial({ color: 0xffffff, map: trayMap, roughness: 0.4, metalness: 0, clearcoat: 0.55, clearcoatRoughness: 0.28, transparent: true, opacity: 0 });
    const traySide = new THREE.MeshPhysicalMaterial({ color: 0xe9eef3, roughness: 0.45, metalness: 0, clearcoat: 0.4, clearcoatRoughness: 0.3, transparent: true, opacity: 0 });
    let slab = null, trayDims = { w: 56, d: 18 };
    const trayMM = new THREE.Group(); trayMM.scale.setScalar(0.001); tray.add(trayMM);
    const trayScan = createScan(trayMM, [-28, -9, 28, 9]);
    const trayPose = { pos: new THREE.Vector3(), tilt: 0.3, s: { x: 0, v: 0, to: 1 }, show: { x: 0, to: 1 } };
    const trayGoal = new THREE.Vector3();
    function trayHome(placing) {
      // above the middle while screening; behind the board's far edge, raised, while parts fly
      const bw = board.worldBox();
      if (modify || placing) {
        const zEdge = bw.min.z - trayDims.d / 2000 - 0.008;
        trayGoal.set((bw.min.x + bw.max.x) / 2, Math.max(bw.max.y, 0.002) + 0.012, zEdge);
      } else trayGoal.set(0, 0.014, 0);
    }
    function buildSlab() {
      if (slab) { tray.remove(slab); slab.geometry.dispose(); }
      const { w, d } = trayDims, r = 3.2, bev = 0.7, s = new THREE.Shape(), x = -w / 2, y = -d / 2;
      s.moveTo(x + r, y); s.lineTo(x + w - r, y); s.absarc(x + w - r, y + r, r, -Math.PI / 2, 0); s.lineTo(x + w, y + d - r); s.absarc(x + w - r, y + d - r, r, 0, Math.PI / 2);
      s.lineTo(x + r, y + d); s.absarc(x + r, y + d - r, r, Math.PI / 2, Math.PI); s.lineTo(x, y + r); s.absarc(x + r, y + r, r, Math.PI, Math.PI * 1.5);
      const g = new THREE.ExtrudeGeometry(s, { depth: 2.4 - bev * 2, bevelEnabled: true, bevelThickness: bev, bevelSize: bev, bevelSegments: 3, curveSegments: 14 });
      g.rotateX(Math.PI / 2); g.translate(0, -bev, 0); g.scale(0.001, 0.001, 0.001);
      slab = new THREE.Mesh(g, [trayTop, traySide]); slab.renderOrder = -1;
      tray.add(slab);
      trayScan.resize([-w / 2 + 1.5, -d / 2 + 1.5, w / 2 - 1.5, d / 2 - 1.5]);
      trayMap.repeat.set(1 / w, 1 / d); trayMap.offset.set(0.5, 0.5);
    }
    function paintTray(pos) {
      const { w, d } = trayDims, k = 12;
      const tw = Math.round(w * k), th = Math.round(d * k);
      if (trayTex.width !== tw || trayTex.height !== th) { trayTex.width = tw; trayTex.height = th; trayMap.dispose(); }
      const g = trayTex.getContext("2d");
      const bg = g.createLinearGradient(0, 0, 0, trayTex.height); bg.addColorStop(0, "#f7f9fb"); bg.addColorStop(1, "#eef2f6");
      g.fillStyle = bg; g.fillRect(0, 0, trayTex.width, trayTex.height);
      g.strokeStyle = "rgba(120,140,165,0.22)"; g.lineWidth = 1; g.beginPath(); g.roundRect(1.6 * k, 1.6 * k, (w - 3.2) * k, (d - 3.2) * k, 2.2 * k); g.stroke();
      for (const [key, [sx, sz]] of pos) {
        const sl = R.slots.find((s) => s.key === key); if (!sl) continue;
        const cx = (sx + w / 2) * k, cy = (d / 2 - sz) * k, rw = (Math.max(6, sl.dw) / 2 + 1.2) * k, rd = (Math.max(6, sl.dd) / 2 + 1.2) * k;
        const gr = g.createRadialGradient(cx, cy, 0, cx, cy, Math.max(rw, rd));
        gr.addColorStop(0, "rgba(150,168,190,0.22)"); gr.addColorStop(0.75, "rgba(150,168,190,0.10)"); gr.addColorStop(1, "rgba(150,168,190,0)");
        g.fillStyle = gr; g.beginPath(); g.ellipse(cx, cy, rw, rd, 0, 0, Math.PI * 2); g.fill();
        g.strokeStyle = "rgba(255,255,255,0.9)"; g.lineWidth = 1.2; g.beginPath(); g.ellipse(cx, cy + 1, rw * 0.98, rd * 0.98, 0, 0.15 * Math.PI, 0.85 * Math.PI); g.stroke();
      }
      trayMap.needsUpdate = true;
    }
    // slots: one per footprint key, each with its items in a small grid
    function relayoutTray() {
      const maxW = modify ? Math.max(50, (rect[2] - rect[0]) * 0.9) : Math.max(56, (R.sized?.w || 50) * 1.05);
      const lay = trayLayout(R.slots, Math.min(120, maxW));
      const changed = Math.abs(lay.w - trayDims.w) > 0.5 || Math.abs(lay.d - trayDims.d) > 0.5 || !slab;
      trayDims = { w: lay.w, d: lay.d };
      if (changed) buildSlab();
      paintTray(lay.pos);
      for (const sl of R.slots) {
        const [sx, sz] = lay.pos.get(sl.key) || [0, 0];
        const n = sl.items.length, cols = Math.ceil(Math.sqrt(n)), sp = sl.cell + 1.1;
        sl.items.forEach((it, i) => {
          const c = i % cols, r = Math.floor(i / cols), rows = Math.ceil(n / cols);
          it.slot = new THREE.Vector3((sx + (c - (cols - 1) / 2) * sp) / 1000, 0, (sz + (r - (rows - 1) / 2) * sp) / 1000);
        });
      }
    }
    function slotFor(key, info) {
      let sl = R.slots.find((s) => s.key === key);
      if (!sl) {
        const [a, b, c, d] = bodyRect(info), dim = Math.max(c - a, d - b, 0.6), disp = clamp(6.2 / dim, 0.42, 3.2);
        sl = { key, info, disp, cell: Math.max(c - a, d - b) * disp, items: [], dw: 0, dd: 0 };
        R.slots.push(sl);
      }
      const n = sl.items.length + 1, cols = Math.ceil(Math.sqrt(n)), rows = Math.ceil(n / cols);
      sl.dw = cols * (sl.cell + 1.1); sl.dd = rows * (sl.cell + 1.1);
      return sl;
    }

    // ---------------- parts ----------------
    // one animated part: { ref, key, info, holder, inst, shadow, mode, fade, ... }
    function makeItem(p, { candidate = false } = {}) {
      const res = resolve(p);
      if (!res) return null;
      const it = { ref: p.ref || null, value: p.value || p.n || "", group: p.group || "", key: res.key, info: res.info, candidate, mode: "wait", fade: { x: 0, to: 1 },
        holder: new THREE.Group(), inner: new THREE.Group(), inst: null, shadow: contactShadow(), spin: hash(p.ref || p.k || res.key) * Math.PI * 2, bob: hash((p.ref || "") + "b") * 6.28,
        target: null, fly: null, lift: { x: 0, v: 0, to: 0 }, landed: 0, scale: 1, ready: false };
      it.holder.add(it.inner); root.add(it.holder); root.add(it.shadow);
      const rc = bodyRect(res.info); it.fw = (rc[2] - rc[0]) / 1000; it.fd = (rc[3] - rc[1]) / 1000; it.fc = [(rc[0] + rc[2]) / 2000, (rc[1] + rc[3]) / 2000];
      it.holder.visible = false; it.shadow.visible = false;
      loadModel(res.key, res.info).then((tpl) => {
        if (R.killed || !tpl) return;
        it.inst = instance(tpl);
        it.inst.obj.traverse((o) => { if (o.isMesh) { o.layers.enable(OCCLUDE_LAYER); o.raycast = () => {}; } });
        it.inner.add(it.inst.obj);
        setFade(it.inst.mats, 0);
        it.ready = true;
        st.kick();
      });
      return it;
    }
    function trayItem(it, sl) {
      sl.items.push(it); it.sl = sl; it.mode = it.mode === "wait" ? "tray" : it.mode; it.scale = sl.disp;
    }
    // world pose of a board spot (board mm) and a turn
    const _v = new THREE.Vector3(), _q = new THREE.Quaternion(), _fq = new THREE.Quaternion(), _m = new THREE.Matrix4();
    function boardPose(t, lift = 0, out = { p: new THREE.Vector3(), q: new THREE.Quaternion() }) {
      frame.updateMatrixWorld(true);
      out.p.set(t.x / 1000, top + lift, t.y / 1000).applyMatrix4(frame.matrixWorld);
      frame.matrixWorld.decompose(_v, _fq, new THREE.Vector3());
      out.q.copy(_fq).multiply(_q.setFromAxisAngle(UP, t.rot || 0));
      return out;
    }
    function trayPoseOf(it, now, out = { p: new THREE.Vector3(), q: new THREE.Quaternion() }) {
      const s = it.slot || new THREE.Vector3();
      const hover = 0.0028 + Math.sin(now * 1.3 + it.bob) * 0.00035;
      out.p.set(s.x, hover - it.box0 * it.scale, s.z);
      tray.localToWorld(out.p);
      out.q.copy(tray.quaternion).multiply(_q.setFromAxisAngle(UP, it.spin));
      return out;
    }

    // ---------------- camera ----------------
    const cam = R.cam;
    const onUser = () => { cam.owned = false; };
    viewer.controls.addEventListener("start", onUser);
    viewer.canvas.addEventListener("wheel", onUser, { passive: true, capture: true });
    viewer.canvas.addEventListener("pointerdown", onUser, { capture: true });
    const camTmp = new THREE.PerspectiveCamera();
    function fitDist(dir, box) {
      const { w: W, h: H } = viewer.size, narrow = W < 560, fx = narrow ? 0.8 : 0.62, fy = narrow ? 0.5 : 0.65;
      const tgt = box.getCenter(new THREE.Vector3()), pts = [];
      for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) pts.push(new THREE.Vector3(x, y, z));
      const c = camTmp; c.fov = viewer.camera.fov || 32; c.aspect = Math.max(0.2, W / H); c.near = 1e-4; c.far = 100; c.updateProjectionMatrix();
      let d = box.getSize(new THREE.Vector3()).length() * 2 || 0.2;
      for (let i = 0; i < 8; i++) {
        c.position.copy(tgt).addScaledVector(dir, d); c.lookAt(tgt); c.updateMatrixWorld();
        let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
        for (const p of pts) { const q = p.clone().project(c); x0 = Math.min(x0, q.x); x1 = Math.max(x1, q.x); y0 = Math.min(y0, q.y); y1 = Math.max(y1, q.y); }
        d *= 0.35 + 0.65 * Math.max(Math.max(-x0, x1) / fx, Math.max(-y0, y1) / fy);
      }
      return { tgt, d };
    }
    const dirOf = (az, el) => { const a = THREE.MathUtils.degToRad(az), e = THREE.MathUtils.degToRad(el); return new THREE.Vector3(Math.sin(a) * Math.cos(e), Math.sin(e), Math.cos(a) * Math.cos(e)); };
    function partsBox(box, only) {
      for (const it of R.parts.values()) {
        if (only && !only.has(it)) continue;
        if (!it.target) continue;
        const p = boardPose(it.target).p; box.expandByPoint(p); box.expandByPoint(p.clone().add(new THREE.Vector3(0, (it.box1 || 0.002), 0)));
      }
      return box;
    }
    function camGoal() {
      const b = board.worldBox();
      const ph = R.phase;
      let box = b.clone(), az = HOME.az, el = HOME.el;
      if (modify) {
        if (ph === "open" || ph === "screen" || ph === "place") { box.union(trayBox()); az = cam.az0; el = cam.el0; }
        else if (ph === "route" || ph === "check") {
          const nb = partsBox(new THREE.Box3()); if (!nb.isEmpty()) { const c = nb.getCenter(new THREE.Vector3()), s = Math.max(nb.getSize(new THREE.Vector3()).length(), b.getSize(new THREE.Vector3()).length() * 0.55) / 2; box = new THREE.Box3(c.clone().subScalar(s), c.clone().addScalar(s)); }
          az = cam.az0 + Math.sin(R.t * 0.08) * 4; el = cam.el0;
        } else { box = new THREE.Box3().setFromObject(viewer.root); az = HOME.az; el = HOME.el; }
      } else {
        partsBox(box);
        if (ph === "open" || ph === "screen") { box.union(trayBox()); az = 20; el = 60; }
        else if (ph === "place") { box.union(trayBox()); az = 22; el = 53; }
        else if (ph === "route" || ph === "check") { az = 20 + Math.sin(R.t * 0.07) * 6 + 4; el = 49; }
        else if (ph === "mask") { az = 29; el = 44; }
        else { az = HOME.az; el = HOME.el; }
      }
      return { box, az, el };
    }
    function trayBox() { tray.updateMatrixWorld(true); const b = new THREE.Box3(); const c = new THREE.Vector3(); const { w, d } = trayDims; for (const x of [-w / 2, w / 2]) for (const z of [-d / 2, d / 2]) for (const y of [0, 7]) b.expandByPoint(tray.localToWorld(c.set(x / 1000, y / 1000, z / 1000))); return b; }
    {
      const off = viewer.camera.position.clone().sub(viewer.target), s = new THREE.Spherical().setFromVector3(off);
      cam.az0 = THREE.MathUtils.radToDeg(s.theta); cam.el0 = 90 - THREE.MathUtils.radToDeg(s.phi);
      cam.cur = { az: cam.az0, el: cam.el0, d: off.length(), tgt: viewer.target.clone() };
      if (modify) { cam.az0 = THREE.MathUtils.clamp(cam.az0, -60, 80); cam.el0 = THREE.MathUtils.clamp(cam.el0, 32, 70); }
    }
    function stepCamera(dt) {
      if (!cam.owned || viewer.ortho) return;
      if (viewer.moving) { // the viewer's own spring is moving it: follow along, don't fight
        const off = viewer.camera.position.clone().sub(viewer.target), s = new THREE.Spherical().setFromVector3(off);
        Object.assign(cam.cur, { az: THREE.MathUtils.radToDeg(s.theta), el: 90 - THREE.MathUtils.radToDeg(s.phi), d: off.length() }); cam.cur.tgt.copy(viewer.target);
        return;
      }
      const g = camGoal(), dir = dirOf(g.az, g.el), f = fitDist(dir, g.box);
      const tau = R.finishing ? 0.9 : 1.15;
      let daz = g.az - cam.cur.az; while (daz > 180) daz -= 360; while (daz < -180) daz += 360;
      cam.cur.az = approach(cam.cur.az, cam.cur.az + daz, dt, tau);
      cam.cur.el = approach(cam.cur.el, g.el, dt, tau);
      cam.cur.d = approach(cam.cur.d, f.d, dt, tau);
      cam.cur.tgt.x = approach(cam.cur.tgt.x, f.tgt.x, dt, tau); cam.cur.tgt.y = approach(cam.cur.tgt.y, f.tgt.y, dt, tau); cam.cur.tgt.z = approach(cam.cur.tgt.z, f.tgt.z, dt, tau);
      const c = viewer.camera;
      viewer.target.copy(cam.cur.tgt);
      c.position.copy(cam.cur.tgt).addScaledVector(dirOf(cam.cur.az, cam.cur.el), cam.cur.d);
      c.lookAt(cam.cur.tgt);
      const near = Math.max(1e-5, cam.cur.d * 0.01);
      if (Math.abs(c.near - near) > near * 0.05) { c.near = near; c.far = Math.max(cam.cur.d * 20, 2); c.updateProjectionMatrix(); }
    }

    // ---------------- dimming the real board (modify, during checks) ----------------
    function realMaterials(filter) {
      const out = new Set();
      viewer.root.traverse((o) => { if (o.isMesh && !o.userData.etchAnim && (!filter || filter(o))) for (const m of Array.isArray(o.material) ? o.material : [o.material]) out.add(m); });
      return [...out];
    }
    function applyDim(k) {
      if (!modify) return;
      if (!dimmed.length && k > 0) for (const m of realMaterials()) if (m.color) dimmed.push({ m, c: m.color.clone() });
      for (const d of dimmed) d.m.color.copy(d.c).multiplyScalar(1 - 0.32 * k);
    }

    // ---------------- the timeline: events queue steps, each runs for at least its time ----------------
    function q(name, min, run2, extra = {}) { R.queue.push({ name, min, run: run2, ...extra }); }
    function speed() { return R.rush * (R.queue.length > 3 ? 1.9 : R.queue.length > 1 ? 1.35 : 1); }
    function stepQueue(dt) {
      const a = R.act;
      if (a) { a.t += dt * speed(); if (a.tick) a.tick(dt * speed(), a); if (a.t < a.min || (a.wait && !a.wait())) return true; R.act = null; }
      const next = R.queue.shift();
      if (!next) return false;
      next.t = 0; R.act = next;
      try { next.run?.(next); } catch (e) { console.warn("build animation:", e); }
      return true;
    }

    // ---------------- events ----------------
    let gateIt = 0;
    function event(e) {
      if (R.done || R.killed) return;
      R.lastEvent = R.t;
      const k = `${e.agent}:${e.type}`;
      switch (k) {
        case "lead:start": break;
        case "classifier:batch":
          if (!reduced && !R.picked && trayScan.busy === false) trayScan.sweep({ ok: true, dur: 0.55 });
          for (const p of (e.pieces || []).filter((x) => x.yes)) addCandidate(p, { exactOnly: true });
          break;
        case "filter:done":
          for (const p of e.kept || []) addCandidate(typeof p === "string" ? { k: p, n: p } : p);
          { const rej = (e.rejected || []).map((p) => (typeof p === "string" ? { k: p, n: p } : p)); for (const p of rej) addCandidate(p, { reject: true }); }
          break;
        case "pieces:show": addCandidate({ k: e.k, n: e.n, f: e.f }); break;
        case "picker:pick": pick(Array.isArray(e.parts) ? e.parts : []); break;
        case "assembly:iteration":
          if (e.phase === "place-route-check") { R.iteration = e.iteration || R.iteration + 1; if (!R.placed) place(); else rerouteAll(); }
          else if (e.phase === "decide" && e.decision && e.decision !== "done") repair();
          break;
        case "assembly:gate": if (!R.placed) place(); gate(e); break;
        case "assembly:built": if (e.board?.w && e.board?.h && !modify) resize(e.board); break;
        case "picker:repair": if (R.placed) repair(); break;
        case "export:done": if (!R.placed) place(); fab(); break;
        case "render:start": q("settle", 0.2, () => { R.phase = "final"; }); break;
        case "lead:error": case "lead:cancelled": cancel(); break;
        case "lead:done": if (!e.ok && !R.finishing) cancel(); break;
      }
      st.kick();
    }

    // 1. screening: candidates appear in the tray (exact footprints only from the classifier's raw batches)
    function addCandidate(p, { exactOnly = false, reject = false } = {}) {
      if (R.picked || reduced) return;
      const key0 = String(p.k || p.n || "");
      if (!key0 || R.cands.has(key0)) return;
      const res = resolve({ piece: p.k, value: p.n, name: p.n, footprint: p.f && /:/.test(p.f) ? p.f : "" });
      if (!res || (exactOnly && res.guessed)) return;
      if ([...R.cands.values()].some((c) => c.key === res.key && !c.rejected)) return; // one per footprint is enough
      if (R.cands.size >= 12) return;
      const it = makeItem({ ref: null, piece: p.k, value: p.n, footprint: res.key });
      if (!it) return;
      R.cands.set(key0, it);
      q("cand", 0.12, () => {
        R.phase = "screen";
        const sl = slotFor(it.key, it.info); trayItem(it, sl); relayoutTray();
      });
      if (reject) q("reject", 0.35, () => { it.rejected = true; leave(it); });
    }
    // 2a. the pick: one slot per footprint, as many items as parts use it
    function pick(list) {
      if (R.picked) return;
      R.picked = true;
      const want = modify ? list.filter((p) => p.group === "added" || p.group === "changed" || (!host?.parts?.[p.ref] && p.group !== "kept")) : list;
      q("pick", reduced ? 0.1 : 0.9, () => {
        R.phase = "screen";
        // reuse a candidate for each footprint's first part, let the rest of the candidates go
        const pool = new Map();
        for (const c of R.cands.values()) if (!c.rejected) { if (!pool.has(c.key)) pool.set(c.key, []); pool.get(c.key).push(c); }
        const keep = new Set();
        R.slots = [];
        for (const p of want) {
          if (!p.ref || R.parts.has(p.ref)) continue;
          const res = resolve(p); if (!res) continue;
          let it = pool.get(res.key)?.shift();
          if (it) { keep.add(it); it.ref = p.ref; it.value = p.value || it.value; it.group = p.group || ""; it.candidate = false; }
          else it = makeItem(p);
          if (!it) continue;
          R.parts.set(p.ref, it);
          if (!reduced) trayItem(it, slotFor(it.key, it.info));
        }
        for (const c of R.cands.values()) if (!keep.has(c) && c.mode !== "leave") leave(c);
        if (!reduced) relayoutTray();
      }, { wait: () => [...R.parts.values()].every((it) => it.ready) || R.act.t > 2.5 });
    }
    // 2b. placing: the core grows, copper wipes in, parts fly onto their pads
    function place() {
      if (R.placed) return;
      R.placed = true;
      if (!R.picked) pick([]);
      q("layout", 0.05, () => {
        R.phase = "place";
        if (modify) layoutModify(); else layoutDesign(R.sized || estimateSize([...R.parts.values()]));
        trayHome(true);
      });
      if (!modify) {
        q("grow", reduced ? 0.4 : 1.15, () => { board.boardG.visible = true; board.set("core", 1); if (!reduced) { board.growIn(); board.jump("core", 1); } });
        q("pour", reduced ? 0.4 : 0.85, () => board.set("pour", 1));
      }
      q("fly", 0.4, (a) => {
        const list = [...R.parts.values()].filter((it) => it.target).sort((x, y) => (y.fw * y.fd) - (x.fw * x.fd));
        const gap = reduced ? 0 : clamp(2.2 / Math.max(1, list.length), 0.06, 0.16);
        list.forEach((it, i) => launch(it, i * gap));
        a.min = (reduced ? 0.5 : 0.95) + gap * list.length;
      });
      route();
    }
    function layoutDesign(size) {
      const list = [...R.parts.values()];
      let s = { ...size }, res = pack(list, s);
      for (let i = 0; i < 6 && !res.fits && !R.sized; i++) { s = { w: Math.ceil(s.w * 1.08), h: Math.ceil(s.h * 1.12) }; res = pack(list, s); }
      if (Math.abs(s.w - (rect[2] - rect[0])) > 0.01 || Math.abs(s.h - (rect[3] - rect[1])) > 0.01) {
        rect = [-s.w / 2, -s.h / 2, s.w / 2, s.h / 2];
        board.resize(rect, { animate: board.get("core") > 0.01 });
        scan.resize(rect);
      }
      for (const it of list) it.target = res.place.get(it.ref) || { x: 0, y: 0, rot: 0 };
      applyPads();
    }
    function layoutModify() {
      viewer.root.updateMatrixWorld(true);
      const inv = new THREE.Matrix4().copy(viewer.root.matrixWorld).invert();
      const occ = [];
      const info = viewer.info?.parts || {};
      for (const [r, p] of Object.entries(viewer.parts)) {
        const b = new THREE.Box3().setFromObject(p.node).applyMatrix4(inv);
        if (!b.isEmpty()) occ.push([b.min.x * 1000, b.min.z * 1000, b.max.x * 1000, b.max.z * 1000, r]);
      }
      for (const [r, p] of Object.entries(info)) if (!viewer.parts[r] && p.pos_mm && p.side !== "bottom") occ.push([p.pos_mm[0] - 1, p.pos_mm[1] - 1, p.pos_mm[0] + 1, p.pos_mm[1] + 1, r]);
      const cx = (rect[0] + rect[2]) / 2, cy = (rect[1] + rect[3]) / 2;
      const anchorOf = () => {
        if (at?.mm) return at.mm;
        const near = ref && info[ref]?.pos_mm; if (near) return near;
        return [cx, cy];
      };
      for (const it of R.parts.values()) {
        const old = info[it.ref];
        if (old?.pos_mm && it.group === "changed") { it.target = { x: old.pos_mm[0], y: old.pos_mm[1], rot: ((old.rot || 0) * Math.PI) / 180 }; replaceOld(it.ref); continue; }
        const [a, b, c, d] = bodyRect(it.info), w = c - a, h = d - b, [ax, ay] = anchorOf();
        const spot = findSpot(occ, rect, w + 0.8, h + 0.8, ax, ay) || [ax, ay];
        it.target = { x: spot[0] - (a + c) / 2, y: spot[1] - (b + d) / 2, rot: 0 };
        occ.push([spot[0] - w / 2, spot[1] - h / 2, spot[0] + w / 2, spot[1] + h / 2, it.ref]);
      }
      R.occ = occ;
      applyPads();
    }
    function applyPads() {
      const placed = [], silk = [];
      for (const it of R.parts.values()) {
        if (!it.target) continue;
        const pads = placedPads(it.info, it.target), pr = placedRect(it.info, it.target);
        placed.push({ ref: it.ref, pads, center: [(pr[0] + pr[2]) / 2, (pr[1] + pr[3]) / 2], rect: pr, power: /^(J|USB|U2|VR|PS)/.test(it.ref) || it.group === "power" });
        silk.push({ strokes: placedSilk(it.info, it.target), label: it.ref, lx: (pr[0] + pr[2]) / 2, ly: pr[1] - 0.35, size: Math.min(pr[2] - pr[0], 8) + 2 });
      }
      R.placedPads = placed;
      board.setPads(placed);
      if (!modify) board.setSilk(silk);
    }
    function launch(it, delay) {
      it.flyAt = R.t + delay; it.mode = "queued";
      board.showPads(it.ref, delay + (reduced ? 0 : 0.18));
    }
    // 3. routing
    function plan() {
      if (!modify) return planTraces(R.placedPads || []);
      // a change: each new part's pads reach out to the nearest existing parts
      const out = [], occ = R.occ || [];
      for (const pl of R.placedPads || []) {
        const others = occ.filter((o) => o[4] !== pl.ref && !R.parts.has(o[4])).map((o) => ({ o, d: Math.hypot((o[0] + o[2]) / 2 - pl.center[0], (o[1] + o[3]) / 2 - pl.center[1]) })).sort((a, b) => a.d - b.d).slice(0, 3);
        const pads = pl.pads.filter((p) => !p.npth);
        others.forEach(({ o }, i) => {
          const pa = pads[i % Math.max(1, pads.length)]; if (!pa) return;
          const tx = clamp(pa.x, o[0], o[2]), ty = clamp(pa.y, o[1], o[3]);
          const pb = { x: tx, y: ty, w: 0.6, h: 0.6 };
          const pts = routePath(pa, pl.center, pb, [(o[0] + o[2]) / 2, (o[1] + o[3]) / 2], hash(pl.ref + i));
          out.push({ key: `${pl.ref}-${o[4]}`, pts, w: 0.3, vias: [] });
        });
      }
      return out;
    }
    function route() {
      q("route", 0.3, (a) => {
        R.phase = "route";
        const list = plan(), span = reduced ? 0.01 : clamp(list.length * 0.11, 1.4, 4.2);
        board.speed = reduced ? 1e4 : 28;
        board.setTraces(list, { span });
        a.min = (reduced ? 0.4 : span + 1.0);
        board.pulsing = true;
      }, { wait: () => [...R.parts.values()].every((it) => it.mode !== "queued" && it.mode !== "fly") });
    }
    function rerouteAll() {
      q("reroute", 0.3, (a) => {
        R.phase = "route";
        board.eraseAll();
        a.min = 0.7;
      });
      q("redraw", 0.3, (a) => { const list = plan(), span = reduced ? 0.01 : clamp(list.length * 0.08, 1.2, 3.2); board.setTraces(list, { span }); a.min = reduced ? 0.3 : span + 0.8; });
    }
    // 4. checks
    function gate(e) {
      const ok = e.ok !== false;
      q("gate", reduced ? 0.15 : 0.5, () => {
        R.phase = "check";
        R.dim.to = 1;
        if (reduced) return;
        const spots = ok ? [] : failSpots(e);
        if (!ok) R.failed = spots;
        scan.sweep({ ok, dur: R.queue.filter((x) => x.name === "gate").length > 2 ? 0.45 : 0.62, spots });
      }, { wait: () => !scan.busy || R.rush > 1 });
      if (!ok && !reduced) q("fail", 0.9, null);
    }
    function failSpots(e) {
      // where a clearance or unconnected error would be: between the closest pair of placed parts
      const P = R.placedPads || [];
      const n = Math.max(1, Math.min(3, parseInt(String(e.value || "1"), 10) || 1));
      const pairs = [];
      for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) pairs.push([Math.hypot(P[i].center[0] - P[j].center[0], P[i].center[1] - P[j].center[1]), i, j]);
      pairs.sort((a, b) => a[0] - b[0]);
      const out = pairs.slice(0, n).map(([, i, j]) => [(P[i].center[0] + P[j].center[0]) / 2, (P[i].center[1] + P[j].center[1]) / 2, 7, P[i].ref, P[j].ref]);
      if (!out.length) out.push([(rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2, 8]);
      return out;
    }
    function repair() {
      if (R.repairedIt === R.iteration) return;
      R.repairedIt = R.iteration;
      q("repair", reduced ? 0.3 : 1.7, (a) => {
        R.phase = "check";
        const refs = new Set(R.failed.flatMap((s) => [s[3], s[4]]).filter(Boolean));
        for (const it of R.parts.values()) if (refs.has(it.ref)) { it.lift.to = 0.0035; it.liftUntil = R.t + 0.75; }
        // the repaired layout: nudge the two parts apart a little and re-draw their tracks
        const moved = [...refs].map((r) => R.parts.get(r)).filter(Boolean);
        if (moved.length === 2 && moved[0].target && moved[1].target) {
          const [A, B] = moved, dx = A.target.x - B.target.x, dy = A.target.y - B.target.y, l = Math.hypot(dx, dy) || 1, k = 0.9;
          A.nudge = { x: A.target.x + (dx / l) * k, y: A.target.y + (dy / l) * k }; B.nudge = { x: B.target.x - (dx / l) * k, y: B.target.y - (dy / l) * k };
        }
        board.reroute(board.traces.filter((t) => [...refs].some((r) => t.key.startsWith(r + "-") || t.key.includes("-" + r + "-"))).map((t) => t.key), 1.1);
        a.tick = () => {
          for (const it of moved) if (it.nudge && R.t > it.liftUntil - 0.35) { Object.assign(it.target, it.nudge); it.nudge = null; applyPads(); }
        };
      });
      q("unfail", 0.05, () => { R.failed = []; R.dim.to = modify ? 0.6 : 0; });
    }
    function resize(b) {
      R.sized = { w: b.w, h: b.h };
      if (!R.placed) return;
      q("resize", reduced ? 0.2 : 0.9, () => {
        const before = new Map([...R.parts.values()].map((it) => [it.ref, it.target && { ...it.target }]));
        layoutDesign(R.sized);
        const moved = [...R.parts.values()].some((it) => { const o = before.get(it.ref); return o && (Math.abs(o.x - it.target.x) > 0.05 || Math.abs(o.y - it.target.y) > 0.05); });
        if (moved) { for (const it of R.parts.values()) if (it.mode === "board") it.slide = 1; board.setTraces(plan(), { span: reduced ? 0.01 : 1.6 }); }
        trayHome(true);
      });
    }
    // 5. fab files
    function fab() {
      if (R.fabbed) return;
      R.fabbed = true;
      q("mask", reduced ? 0.5 : 1.9, () => { R.phase = "mask"; R.dim.to = 0; board.pulsing = false; if (modify) board.set("maskTint", 1); else board.set("mask", 1); });
      if (!modify) q("silk", reduced ? 0.4 : 1.4, () => board.set("silk", 1));
    }
    // 6. the real board
    function finish({ glbReady = false } = {}) {
      if (R.finishing || R.done) return;
      R.finishing = { glbReady, since: R.t, morph: null };
      R.rush = reduced ? 4 : 1.8;
      if (!R.placed) place();
      if (!R.fabbed) fab();
      st.kick();
    }
    function replaceOld(r) {
      const p = viewer.parts[r]; if (!p || realOld.has(r)) return;
      realOld.set(r, { node: p.node, y: p.node.position.y, mats: p.mats.map((m) => ({ m, o: m.opacity, t: m.transparent, d: m.depthWrite })), k: 0 });
    }

    // ---------------- the morph into the real model ----------------
    // hide what is new in the real model the moment it is loaded (before it is ever drawn): everything for a new
    // board, the animated parts for a change
    function hideReal() {
      if (R.hidden || viewer.parts === partsAtStart) return;
      R.hidden = [];
      const hide = new Set();
      if (!modify) {
        for (const m of realMaterials()) hide.add(m);
        for (const o of st.scene.children) if (o.isMesh && o.renderOrder === -1 && o.material?.map && o.material.isMeshBasicMaterial) hide.add(o.material); // the board's soft shadow
      }
      else for (const r of R.parts.keys()) { const p = viewer.parts[r]; if (p) for (const m of p.mats) hide.add(m); }
      for (const m of hide) { R.hidden.push({ m, o: m.opacity, t: m.transparent, d: m.depthWrite }); m.transparent = true; m.opacity = 0; m.needsUpdate = true; }
    }
    function takeover() {
      const F = R.finishing;
      hideReal();
      F.morph = { t: 0, saved: R.hidden || [], targets: new Map() };
      const M = F.morph;
      const info = viewer.info?.parts || {};
      viewer.root.updateMatrixWorld(true);
      const realOf = (ref) => viewer.parts[ref];
      // real places for the animated parts
      const rootQ = new THREE.Quaternion(); viewer.root.matrixWorld.decompose(new THREE.Vector3(), rootQ, new THREE.Vector3());
      for (const it of R.parts.values()) {
        const p = info[it.ref], real = realOf(it.ref);
        if (!real || !p?.pos_mm || p.side === "bottom") continue;
        const pos = new THREE.Vector3(p.pos_mm[0] / 1000, viewer.boardTopLocal, p.pos_mm[1] / 1000).applyMatrix4(viewer.root.matrixWorld);
        const q2 = rootQ.clone().multiply(new THREE.Quaternion().setFromAxisAngle(UP, ((p.rot || 0) * Math.PI) / 180));
        M.targets.set(it, { p: pos, q: q2 });
      }
      // the core's last move: onto the real outline (new boards)
      if (!modify) {
        const r = viewer.boardRect();
        if (r) {
          const a = new THREE.Vector3(r.x0 / 1000, viewer.boardTopLocal, r.y0 / 1000).applyMatrix4(viewer.root.matrixWorld), b = new THREE.Vector3(r.x1 / 1000, viewer.boardTopLocal, r.y1 / 1000).applyMatrix4(viewer.root.matrixWorld);
          const w0 = rect[2] - rect[0], h0 = rect[3] - rect[1], w1 = Math.abs(b.x - a.x) * 1000, h1 = Math.abs(b.z - a.z) * 1000;
          M.frame0 = { p: frame.position.clone(), s: frame.scale.clone() };
          // rect is centred on 0, so: scale about the centre, move the centre onto the real one, lift onto its top
          M.frame1 = { p: new THREE.Vector3((a.x + b.x) / 2, Math.min(a.y, b.y) - top + 0.00004, (a.z + b.z) / 2), s: new THREE.Vector3(w1 / w0, 1, h1 / h0) };
        }
      }
      for (const [r, o] of realOld) { for (const s of o.mats) { s.m.opacity = s.o; s.m.transparent = s.t; s.m.depthWrite = s.d; s.m.needsUpdate = true; } o.node.position.y = o.y; }
      realOld.clear();
      applyDim(0); R.dim.x = R.dim.to = 0;
      R.phase = "final";
      for (const it of R.parts.values()) {
        const t = M.targets.get(it);
        it.morphFrom = { p: it.holder.position.clone(), q: it.holder.quaternion.clone(), s: it.holder.scale.x };
        it.morphTo = t || null;
        if (!t) it.mode = it.mode === "leave" ? "leave" : "morphOut";
        else it.mode = "morph";
      }
      for (const c of R.cands.values()) if (c.mode !== "leave") leave(c);
    }
    function stepMorph(dt) {
      const M = R.finishing.morph, dur = reduced ? 0.6 : 1.35;
      M.t += dt / dur;
      const k = clamp(M.t), mv = easeInOut(clamp(k / 0.62)), realK = smooth((k - 0.4) / 0.3), fadeK = smooth((k - 0.6) / 0.4);
      if (M.frame1) {
        frame.position.lerpVectors(M.frame0.p, M.frame1.p, mv);
        frame.scale.lerpVectors(M.frame0.s, M.frame1.s, mv);
      }
      for (const s of M.saved) {
        s.m.opacity = s.o * realK;
        if (realK >= 1 && !s.back) { s.back = true; s.m.transparent = s.t; s.m.depthWrite = s.d; s.m.needsUpdate = true; }
      }
      board.jump("fade", 1 - fadeK);
      if (!modify) board.jump("core", 1 - fadeK);
      for (const it of R.parts.values()) {
        if (it.mode === "morph" && it.morphTo) {
          const lift = Math.sin(Math.PI * mv) * 0.0035 * (reduced ? 0 : 1);
          it.holder.position.lerpVectors(it.morphFrom.p, it.morphTo.p, mv).y += lift;
          it.holder.quaternion.slerpQuaternions(it.morphFrom.q, it.morphTo.q, mv);
          it.holder.scale.setScalar(THREE.MathUtils.lerp(it.morphFrom.s, 1, mv));
          if (it.inst) setFade(it.inst.mats, 1 - fadeK);
          it.shadow.material.opacity *= 1 - fadeK * 0.2;
        } else if (it.mode === "morphOut") { it.fade.to = 0; }
      }
      tray.visible = trayPose.show.x > 0.01;
      trayPose.show.to = 0;
      if (k >= 1) {
        for (const s of M.saved) { s.m.opacity = s.o; s.m.transparent = s.t; s.m.depthWrite = s.d; s.m.needsUpdate = true; }
        M.saved = []; R.hidden = [];
        end();
      }
    }

    // ---------------- per-frame ----------------
    const pose = { p: new THREE.Vector3(), q: new THREE.Quaternion() }, pose2 = { p: new THREE.Vector3(), q: new THREE.Quaternion() };
    const _b1 = new THREE.Vector3(), _b2 = new THREE.Vector3();
    function leave(it) { it.mode = "leave"; it.fade.to = 0; it.leaveT = R.t; }
    function stepItem(it, dt, now) {
      if (!it.ready) return true;
      if (it.box0 === undefined) { const b = new THREE.Box3().setFromObject(it.inst.obj); it.box0 = b.isEmpty() ? 0 : b.min.y; it.box1 = b.isEmpty() ? 0.002 : b.max.y; }
      let moving = false;
      const fr = it.fade, fs = (it.mode === "leave" || it.mode === "morphOut" ? 2.2 : 2.6) * dt;
      if (fr.x !== fr.to) { fr.x = fr.to > fr.x ? Math.min(fr.to, fr.x + fs) : Math.max(fr.to, fr.x - fs); moving = true; }
      if (it.mode !== "morph") setFade(it.inst.mats, smooth(fr.x));
      it.holder.visible = fr.x > 0.002 && it.mode !== "wait";
      it.spin += dt * (it.mode === "tray" || it.mode === "leave" ? 0.42 : 0);
      let shadowOn = it.holder.visible, sh = 0, ground = null, sx = it.fw, sz = it.fd;
      if (it.mode === "tray" || (it.mode === "leave" && !it.target)) {
        trayPoseOf(it, now, pose);
        if (it.mode === "leave") pose.p.y -= 0.004 * smooth((R.t - (it.leaveT || R.t)) / 0.7);
        it.holder.position.copy(pose.p); it.holder.quaternion.copy(pose.q); it.holder.scale.setScalar(it.scale);
        ground = "tray"; sh = 0.0028; sx *= it.scale; sz *= it.scale;
        moving = true;
      } else if (it.mode === "queued") {
        trayPoseOf(it, now, pose); it.holder.position.copy(pose.p); it.holder.quaternion.copy(pose.q); it.holder.scale.setScalar(it.scale);
        ground = "tray"; sh = 0.0028; sx *= it.scale; sz *= it.scale;
        if (R.t >= it.flyAt) {
          if (reduced) { it.mode = "board"; it.fade.x = 0; it.fade.to = 1; }
          else { it.mode = "fly"; it.fly = { t: 0, from: { p: pose.p.clone(), q: pose.q.clone(), s: it.scale }, dur: 0.78 + hash(it.ref) * 0.2 }; }
        }
        moving = true;
      } else if (it.mode === "fly") {
        const f = it.fly; f.t += dt / f.dur;
        const k = clamp(f.t), e = easeInOut(k);
        boardPose(it.target, 0, pose2);
        const p0 = f.from.p, p3 = pose2.p, dist = p0.distanceTo(p3), hgt = 0.006 + dist * 0.42;
        _b1.copy(p0).addScaledVector(UP, hgt); _b2.copy(p3).addScaledVector(UP, hgt * 0.9);
        const u = 1 - e;
        it.holder.position.set(0, 0, 0).addScaledVector(p0, u * u * u).addScaledVector(_b1, 3 * u * u * e).addScaledVector(_b2, 3 * u * e * e).addScaledVector(p3, e * e * e);
        it.holder.quaternion.slerpQuaternions(f.from.q, pose2.q, easeOut(k * 1.15));
        // a little bank into the turn
        const bank = Math.sin(Math.PI * k) * 0.22;
        it.holder.quaternion.multiply(_q.setFromAxisAngle(_v.set(1, 0, 0.4).normalize(), bank * (hash(it.ref + "k") > 0.5 ? 1 : -1)));
        it.holder.scale.setScalar(THREE.MathUtils.lerp(f.from.s, 1, easeInOut(clamp(k * 1.25))));
        ground = "board"; sh = it.holder.position.y - p3.y;
        if (k >= 1) { it.mode = "board"; it.lift.x = 0.0009; it.lift.v = -0.12; it.lift.to = 0; it.landed = R.t; }
        moving = true;
      } else if (it.mode === "board" || (it.mode === "leave" && it.target)) {
        if (it.liftUntil && R.t > it.liftUntil) { it.lift.to = 0; it.liftUntil = 0; }
        if (!springStep(it.lift, dt, 260, 0.5)) moving = true;
        boardPose(it.target, Math.max(-0.0003, it.lift.x), pose);
        if (it.slide) { it.holder.position.lerp(pose.p, 1 - Math.exp(-dt * 7)); it.holder.quaternion.slerp(pose.q, 1 - Math.exp(-dt * 7)); if (it.holder.position.distanceTo(pose.p) < 1e-5) it.slide = 0; moving = true; }
        else { it.holder.position.copy(pose.p); it.holder.quaternion.copy(pose.q); }
        it.holder.scale.setScalar(1);
        ground = "board"; sh = Math.max(0, it.lift.x);
      } else if (it.mode === "morph" || it.mode === "morphOut") {
        ground = "board"; sh = 0; moving = true;
      }
      // the contact shadow: on the tray or the board, softer and lighter the higher the part is
      if (shadowOn && quality >= 1 && ground) {
        const s = it.shadow, h = Math.max(0, sh);
        const c = it.holder.position;
        if (ground === "tray") {
          s.quaternion.copy(tray.quaternion);
          _v.set(c.x, c.y, c.z); tray.worldToLocal(_v); _v.y = 0.00002; tray.localToWorld(_v);
          s.position.copy(_v);
        } else {
          s.quaternion.copy(it.mode === "fly" ? _fq.identity() : frameQuat());
          const gy = boardPose({ x: 0, y: 0, rot: 0 }, 0, pose2).p.y + 0.00008;
          s.position.set(c.x + LIGHT.x * h, gy, c.z + LIGHT.y * h);
        }
        // centre of the body, not the footprint origin
        _v.set(it.fc[0] * it.holder.scale.x, 0, it.fc[1] * it.holder.scale.x).applyQuaternion(it.holder.quaternion); s.position.x += _v.x; s.position.z += _v.z;
        const grow2 = 1 + h * 55;
        s.scale.set(Math.max(1e-4, sx * 1.55 * grow2), 1, Math.max(1e-4, sz * 1.55 * grow2));
        s.rotation.y = 0;
        const land = it.landed ? clamp((R.t - it.landed) / 0.3) : 1;
        s.material.opacity = clamp(fr.x) * (ground === "tray" ? 0.34 : 0.58) * Math.exp(-h / 0.006) * (0.85 + 0.15 * land) * (R.finishing?.morph ? 1 - smooth((R.finishing.morph.t - 0.4) / 0.6) : 1);
        s.visible = s.material.opacity > 0.003;
      } else it.shadow.visible = false;
      return moving;
    }
    const frameQuat = () => { frame.updateMatrixWorld(true); frame.matrixWorld.decompose(_v, _fq, new THREE.Vector3()); return _fq; };

    let lastIdle = 0;
    const off = st.add((dt, now) => {
      if (R.killed) return false;
      R.t += dt;
      const t = now / 1000;
      // GPU budget: drop the glow, then the shadows, when frames run long
      perf.sum += dt; perf.n++;
      if (perf.sum > 1.5) { const avg = perf.sum / perf.n; if (avg > 1 / 42 && quality > 0 && R.t > 2) quality--; perf.sum = 0; perf.n = 0; }
      let live = stepQueue(dt) || R.queue.length > 0;
      // tray: rises in, glides to its place
      if (!R.trayInit) { trayHome(false); tray.position.copy(trayGoal); trayPose.pos.copy(trayGoal); R.trayInit = true; tray.visible = !reduced; }
      if (reduced) trayPose.show.to = 0;
      else if (R.placed && [...R.parts.values()].every((it) => it.mode !== "tray" && it.mode !== "queued") && R.act?.name !== "fly") trayPose.show.to = 0;
      trayPose.show.x = approach(trayPose.show.x, trayPose.show.to, dt, 0.25);
      springStep(trayPose.s, dt, 140, 0.8);
      for (const k of ["x", "y", "z"]) trayPose.pos[k] = approach(trayPose.pos[k], trayGoal[k], dt, 0.45);
      tray.position.copy(trayPose.pos); tray.position.y += (1 - trayPose.show.x) * -0.006;
      tray.rotation.set(trayPose.tilt, 0, 0);
      tray.scale.setScalar(0.92 + 0.08 * trayPose.show.x);
      trayTop.opacity = traySide.opacity = trayPose.show.x;
      const opaque = trayPose.show.x > 0.995;
      if (trayTop.transparent === opaque) { trayTop.transparent = traySide.transparent = !opaque; trayTop.depthWrite = traySide.depthWrite = opaque || trayPose.show.x > 0.5; trayTop.needsUpdate = traySide.needsUpdate = true; }
      tray.visible = trayPose.show.x > 0.003 && !!slab;
      if (Math.abs(trayPose.show.x - trayPose.show.to) > 0.002) live = true;
      // parts
      for (const it of [...R.cands.values(), ...R.parts.values()]) if (stepItem(it, dt, t)) live = true;
      for (const [k, c] of R.cands) if (c.mode === "leave" && c.fade.x <= 0 && !c.target) { root.remove(c.holder, c.shadow); R.cands.delete(k); }
      // board layers, scan, rings
      if (board.step(dt, { speed: speed() })) live = true;
      if (scan.step(dt * Math.min(1.6, speed()))) live = true;
      if (scan.stepRings(dt)) live = true;
      if (trayScan.step(dt)) live = true;
      // the old part being replaced in a change floats up and away
      for (const o of realOld.values()) {
        o.k = Math.min(1, o.k + dt / 0.8);
        o.node.position.y = o.y + smooth(o.k) * 0.004;
        for (const s of o.mats) { if (!s.m.transparent) { s.m.transparent = true; s.m.needsUpdate = true; } s.m.opacity = s.o * (1 - smooth(o.k)); }
        if (o.k < 1) live = true;
      }
      // dim the real board during checks
      R.dim.x = approach(R.dim.x, R.dim.to, dt, 0.35);
      if (modify && Math.abs(R.dim.x - R.dim.to) > 1e-3) { applyDim(R.dim.x); live = true; } else if (modify && dimmed.length) applyDim(R.dim.x);
      // waiting on the engine (the router can take minutes): every few seconds re-route one track, like it would
      if (R.phase === "route" && !R.queue.length && (!R.act || R.act.t >= R.act.min) && board.drawn && R.t - R.lastEvent > 6 && R.t - lastIdle > 3.2 && !reduced) {
        lastIdle = R.t;
        const ts = board.traces; if (ts.length) board.reroute([ts[Math.floor(hash(String(R.t)) * ts.length)].key], 0.9);
      }
      // finishing: once the queue has run out and the real model is in, morph into it
      // the real model is in: the viewer frames it from here on (main eases it home), the rest morphs into it
      if (viewer.parts !== R.seenParts) { R.seenParts = viewer.parts; cam.owned = false; if (!R.finishing) finish({ glbReady: true }); hideReal(); }
      const F = R.finishing;
      if (F) {
        if (!F.morph) {
          const loaded = !!R.hidden || (F.glbReady && R.t - F.since > 0.05 && !viewer.isEmpty);
          if (loaded && !R.hidden) hideReal();
          if (loaded && !R.queue.length && (!R.act || R.act.t >= R.act.min)) { R.act = null; takeover(); }
          else if (loaded && R.queue.length) { R.rush = 3.5; }
          else if (!loaded && R.t - F.since > 25) cancel();
        } else stepMorph(dt);
        live = true;
      }
      R.glowOn = !reduced && quality >= 2 && (board.traces.length > 0 || scan.busy || trayScan.busy);
      stepCamera(dt);
      return live || cam.owned;
    }, { always: false });

    function cancel() {
      if (R.done) return;
      R.done = true;
      for (const [r, o] of realOld) { for (const s of o.mats) { s.m.opacity = s.o; s.m.transparent = s.t; s.m.depthWrite = s.d; s.m.needsUpdate = true; } o.node.position.y = o.y; }
      realOld.clear();
      applyDim(0);
      // fade everything out, then clean up
      let k = 1;
      off();
      const offFade = st.add((dt) => {
        k -= dt / 0.55;
        const a = Math.max(0, k);
        board.jump("fade", a); if (!modify) board.jump("core", Math.min(board.get("core"), a));
        board.step(0);
        for (const it of [...R.cands.values(), ...R.parts.values()]) { if (it.inst) setFade(it.inst.mats, a * smooth(it.fade.x)); it.shadow.material.opacity *= a; }
        trayTop.opacity = traySide.opacity = Math.min(trayTop.opacity, a);
        if (k <= 0) { offFade(); cleanup(); return false; }
        return true;
      });
    }
    function end() { R.done = true; off(); cleanup(); }
    function cleanup() {
      if (R.killed) return;
      R.killed = true;
      for (const [, o] of realOld) { for (const s of o.mats) { s.m.opacity = s.o; s.m.transparent = s.t; s.m.depthWrite = s.d; s.m.needsUpdate = true; } o.node.position.y = o.y; }
      for (const s of R.hidden || []) { s.m.opacity = s.o; s.m.transparent = s.t; s.m.depthWrite = s.d; s.m.needsUpdate = true; }
      applyDim(0);
      viewer.controls.removeEventListener("start", onUser);
      viewer.canvas.removeEventListener("wheel", onUser, { capture: true });
      viewer.canvas.removeEventListener("pointerdown", onUser, { capture: true });
      st.scene.remove(root);
      root.traverse((o) => { if (o.isMesh && o.material && !Array.isArray(o.material) && o.material.map === null && o.geometry && !o.geometry.userData?.shared) { /* geometry of instances is shared with the model cache */ } });
      for (const it of [...R.cands.values(), ...R.parts.values()]) { it.shadow.material.dispose(); if (it.inst) for (const m of it.inst.mats) m.dispose(); }
      board.dispose(); scan.dispose(); trayScan.dispose();
      slab?.geometry.dispose(); trayTop.dispose(); traySide.dispose(); trayMap.dispose();
      if (run === self) run = null;
      viewer.invalidate();
    }
    const self = {
      event,
      finish,
      cancel,
      kill() { off(); cleanup(); },
    };
    // a new board starts from above, sliding the tray in; a change starts where the person is looking
    q("open", reduced ? 0.05 : 0.6, () => { R.phase = modify ? "place" : "open"; trayPose.show.to = reduced ? 0 : 1; if (!slab) { buildSlab(); paintTray(new Map()); } });
    loadIndex().then(() => st.kick());
    if (!reduced) { R.phase = modify ? "place" : "open"; }
    // events can arrive before the index has loaded: hold them until it has
    const pending = [];
    let indexed = false;
    loadIndex().then(() => { indexed = true; for (const e of pending.splice(0)) event(e); });
    self.event = (e) => { if (!indexed) pending.push(e); else event(e); };
    return self;
  }

  return api;
}

// module-level shortcuts for the editor: buildAnim.start / event / finish on the last created animation
export function start(o) { return current?.start(o); }
export function event(e) { current?.event(e); }
export function finish(o) { current?.finish(o); }
export function cancel() { current?.cancel(); }

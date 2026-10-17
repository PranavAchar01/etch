// What sits on the empty workplane of a new board (board.html?new=1) until the build starts: a crisp board outline
// (an edge-cut line with corner marks) in the middle, where the board will go, and a small kit of real KiCad parts
// (their own 3D models, the same ones the build animation uses) floating just above the workplane round it, each
// with a soft contact shadow, slowly turning and bobbing. Drawn in the viewer's 3D scene with its lights.
// While a build runs (building(true)) it is b-roll behind the render loader: the kit stays, dimmed, and drifts and
// turns a little more; the outline fades back. The build's real picks (picker:pick parts) swap into the kit's slots,
// one cross-fade per slot, so what floats is what Etch chose; it never stands in for the board's layout. When the
// real board loads, remove() fades it all out. With BUILD_ANIM on in main.js, editor/build-anim.js draws instead and
// main.js removes this at lead:start. Reduced motion: everything is still. Never takes pointer events.
//
//   const bd = createStartBackdrop(stageEl, { boards });   // boards: accepted for compatibility, not drawn
//   bd.attach(viewer); bd.building(true); bd.size({ w: 52, h: 38 }); bd.parts([...]); bd.remove();
import * as THREE from "three";
import { stageOf, reducedMotion, approach, smooth, hash } from "./build-anim/stage.js";
import { loadIndex, resolve, loadModel, instance, setFade } from "./build-anim/parts3d.js";
import { contactShadow } from "./build-anim/fx.js";
import { bodyRect } from "./build-anim/layout.js";

// the kit: [footprint, x, z (fractions of the drawing area), turn (deg)]
const KIT = [
  ["RF_Module:ESP32-WROOM-32", 0.1, 0.3, -14], ["Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", 0.9, 0.22, 18],
  ["Button_Switch_THT:SW_PUSH_6mm", 0.88, 0.72, -8], ["LED_THT:LED_D5.0mm", 0.07, 0.8, 0], ["Capacitor_THT:CP_Radial_D6.3mm_P2.50mm", 0.94, 0.47, 0],
  ["Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", 0.28, 0.93, 12], ["Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", 0.72, 0.08, 90],
  ["Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", 0.3, 0.07, -22], ["Sensor:Aosong_DHT11_5.5x12.0_P2.54mm", 0.7, 0.93, 8],
];

export function createStartBackdrop(host, { boards = [] } = {}) {
  void boards;
  const el = document.createElement("div");
  el.className = "sbd";
  el.setAttribute("aria-hidden", "true");
  el.style.pointerEvents = "none";
  host.append(el);
  let st = null, off = null, root = null, outline = null, items = [], state = { show: 0, to: 1, build: 0, size: null, gone: false };
  let picks = [], swapped = new Set();
  const reduced = reducedMotion();

  // the outline: a dashed edge-cut rounded rectangle with corner marks, drawn into a texture on the workplane
  function outlineMesh(w, h) {
    const k = 16, pad = 6, cv = document.createElement("canvas");
    cv.width = Math.round((w + pad * 2) * k); cv.height = Math.round((h + pad * 2) * k);
    const g = cv.getContext("2d");
    g.strokeStyle = "rgba(26,120,190,0.55)"; g.lineWidth = 0.32 * k; g.setLineDash([1.6 * k, 1.1 * k]); g.lineCap = "round";
    g.beginPath(); g.roundRect(pad * k, pad * k, w * k, h * k, 2.2 * k); g.stroke();
    g.setLineDash([]); g.strokeStyle = "rgba(26,120,190,0.7)"; g.lineWidth = 0.4 * k;
    for (const [x, y, sx, sy] of [[0, 0, 1, 1], [w, 0, -1, 1], [0, h, 1, -1], [w, h, -1, -1]]) {
      const cx = (pad + x) * k, cy = (pad + y) * k, L = 3.2 * k, o = 1.6 * k;
      g.beginPath(); g.moveTo(cx - sx * o, cy - sy * o + sy * L); g.lineTo(cx - sx * o, cy - sy * o); g.lineTo(cx - sx * o + sx * L, cy - sy * o); g.stroke();
    }
    const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
    const geo = new THREE.PlaneGeometry((w + pad * 2) / 1000, (h + pad * 2) / 1000); geo.rotateX(-Math.PI / 2);
    const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: t, transparent: true, opacity: 0, depthWrite: false, toneMapped: false }));
    m.position.y = 0.00003; m.renderOrder = -1;
    return m;
  }

  function attach(viewer) {
    if (st) return;
    st = stageOf(viewer);
    root = new THREE.Group(); root.name = "etch-start-backdrop";
    st.scene.add(root);
    const a = viewer.emptyArea || { x0: -0.05, z0: -0.03, x1: 0.05, z1: 0.03 };
    const W = (a.x1 - a.x0) * 1000, H = (a.z1 - a.z0) * 1000;
    outline = outlineMesh(W * 0.56, H * 0.56);
    outline.position.x = (a.x0 + a.x1) / 2; outline.position.z = (a.z0 + a.z1) / 2;
    root.add(outline);
    loadIndex().then(() => {
      if (state.gone) return;
      for (const [fp, fx, fz, turn] of KIT) {
        const res = resolve({ footprint: fp }); if (!res) continue;
        const it = { holder: new THREE.Group(), shadow: contactShadow(), inst: null, x: a.x0 + fx * (a.x1 - a.x0), z: a.z0 + fz * (a.z1 - a.z0), turn: (turn * Math.PI) / 180, ph: hash(fp) * 6.28, rc: bodyRect(res.info), y0: 0, key: res.key, f: 1, next: null };
        root.add(it.holder, it.shadow);
        items.push(it);
        loadModel(res.key, res.info).then((tpl) => {
          if (!tpl || state.gone || it.key !== res.key) return; // a real pick took this slot first
          it.inst = instance(tpl);
          it.holder.add(it.inst.obj);
          it.y0 = -tpl.box.min.y; // leads clear of the workplane
          setFade(it.inst.mats, 0);
          st.kick();
        });
      }
      swapIn();
    });
    let t = 0;
    off = st.add((dt) => {
      t += dt;
      state.show = approach(state.show, state.gone ? 0 : state.to, dt, state.gone ? 0.18 : 0.5);
      state.build = approach(state.build, state.building ? 1 : 0, dt, 0.4);
      outline.material.opacity = state.show * (1 - state.build * 0.6);
      let i = 0, busy = false;
      const b = state.build;
      for (const it of items) {
        i++;
        // a real pick waiting for this slot: fade the old part out, then put the new one in and fade it back
        if (it.next) {
          it.f = approach(it.f, 0, dt, reduced ? 0.01 : 0.25);
          if (it.f < 0.02 && it.next.tpl) {
            if (it.inst) { it.holder.remove(it.inst.obj); for (const m of it.inst.mats) m.dispose(); }
            const n = it.next; it.next = null;
            it.inst = instance(n.tpl); it.holder.add(it.inst.obj);
            it.y0 = -n.tpl.box.min.y; it.rc = bodyRect(n.info); it.key = n.key;
          }
          busy = true;
        } else if (it.f < 0.999) { it.f = approach(it.f, 1, dt, reduced ? 0.01 : 0.35); busy = true; }
        if (!it.inst) continue;
        const a2 = smooth(state.show * 1.4 - i * 0.05);
        // b-roll while building: a slow wander round its spot and a slow turn, on top of the bob
        const bob = reduced ? 0 : Math.sin(t * 0.9 + it.ph) * 0.0004;
        const dx = reduced ? 0 : b * Math.sin(t * 0.11 + it.ph) * 0.003, dz = reduced ? 0 : b * Math.cos(t * 0.09 + it.ph * 1.3) * 0.002;
        const lift = 0.0016 + it.y0 + bob + b * 0.0012 - (1 - a2) * 0.002;
        it.holder.position.set(it.x + dx, lift, it.z + dz);
        it.holder.rotation.set(0, it.turn + (reduced ? 0 : Math.sin(t * 0.25 + it.ph) * 0.16 + b * t * 0.08), 0);
        const vis = a2 * it.f * (1 - b * 0.38); // dimmed behind the loader
        setFade(it.inst.mats, vis);
        const [x0, y0, x1, y1] = it.rc, cx = (x0 + x1) / 2000, cz = (y0 + y1) / 2000, c = Math.cos(it.holder.rotation.y), s = Math.sin(it.holder.rotation.y);
        const h = lift - it.y0;
        it.shadow.position.set(it.x + dx + cx * c + cz * s + 0.5 * h, 0.00005, it.z + dz - cx * s + cz * c - 0.7 * h);
        it.shadow.rotation.y = it.holder.rotation.y;
        it.shadow.scale.set(((x1 - x0) / 1000) * 1.6, 1, ((y1 - y0) / 1000) * 1.6);
        it.shadow.material.opacity = vis * 0.32;
        it.shadow.visible = vis > 0.01;
      }
      if (state.gone && state.show < 0.005) { cleanup(); return false; }
      return !reduced || busy || Math.abs(state.show - state.to) > 0.002 || Math.abs(state.build - (state.building ? 1 : 0)) > 0.002;
    });
  }
  // the build's real picks into the kit: each resolvable footprint not already floating takes the next slot
  function swapIn() {
    if (!items.length || state.gone) return;
    let slot = 0;
    for (const p of picks) {
      const res = resolve(p); if (!res || swapped.has(res.key) || items.some((it) => it.key === res.key)) continue;
      while (slot < items.length && swapped.has(items[slot].key)) slot++;
      if (slot >= items.length) break;
      const it = items[slot++], n = { key: res.key, info: res.info, tpl: null };
      swapped.add(res.key); it.key = res.key; it.next = n;
      loadModel(res.key, res.info).then((tpl) => { if (!tpl) { if (it.next === n) it.next = null; return; } n.tpl = tpl; st?.kick(); });
    }
    picks = [];
    st?.kick();
  }
  function cleanup() {
    off?.(); off = null;
    if (root) { st.scene.remove(root); outline.material.map.dispose(); outline.material.dispose(); outline.geometry.dispose(); for (const it of items) { it.shadow.material.dispose(); if (it.inst) for (const m of it.inst.mats) m.dispose(); } }
    root = null; items = [];
    el.remove();
    st?.kick();
  }

  return {
    el,
    attach,
    building(on) { state.building = !!on; st?.kick(); },
    // the board's planned size in mm: the outline takes that aspect (kept inside the drawing area)
    size(b) {
      if (!b?.w || !b?.h || !outline || !st) return;
      const a = st.viewer.emptyArea; if (!a) return;
      const W = (a.x1 - a.x0) * 1000 * 0.56, H = (a.z1 - a.z0) * 1000 * 0.56, k = Math.min(W / b.w, H / b.h);
      const m = outlineMesh(b.w * k, b.h * k); m.position.copy(outline.position);
      m.material.opacity = outline.material.opacity;
      root.remove(outline); outline.material.map.dispose(); outline.material.dispose(); outline.geometry.dispose();
      outline = m; root.add(m); st.kick();
    },
    // picker:pick parts ({ ref, piece, value, footprint? }): the real picks float in the kit's place
    parts(list) { picks = (Array.isArray(list) ? list : []).filter((p) => p && p.group !== "kept"); swapIn(); },
    remove() { state.gone = true; if (!st) el.remove(); else st.kick(); },
  };
}

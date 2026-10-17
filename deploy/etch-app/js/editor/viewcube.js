// View cube + camera buttons for the board editor.
//
// Coordinate convention (matches the board viewer):
//   - World Y is up. The board lies in the XZ plane, component side facing +Y.
//   - TOP    = camera on +Y looking down (-Y).
//   - BOTTOM = camera on -Y looking up.
//   - FRONT  = camera on +Z looking along -Z.
//   - BACK   = camera on -Z looking along +Z.
//   - RIGHT  = camera on +X looking along -X.
//   - LEFT   = camera on -X looking along +X.
// Each of the 26 hit regions is a direction vector (sx, sy, sz) with
// components in {-1, 0, 1}: 6 faces, 12 edges, 8 corners. Snapping places the
// main camera at controls.target + normalize(dir) * currentDistance.
// Region names passed to onSnap are lowercase, joined with "-" in the order
// vertical, depth, horizontal: "top", "front-right", "top-back-left", ...
//
// API
//   createViewCube(hostEl, { camera, controls, onSnap }) -> { update(), dispose() }
//   createCameraButtons(hostEl, { home, fit, zoomIn, zoomOut, toggleOrtho })
//     -> { setOrtho(bool), dispose() }
//   springCamera(camera, controls, toPosition, toTarget, ms = 450) -> Promise<void>

import * as THREE from "three";

const SIZE = 90;
const BAND = 0.32; // edge/corner band width as a fraction of the half-size
const FONT = '600 40px "Figtree", system-ui, sans-serif';

const reducedMotion = () =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ------------------------------------------------------------------ spring */

const running = new WeakMap(); // camera -> cancel fn

/**
 * Critically damped spring of the main camera to a new position/target.
 * The view direction is slerped around the target so the camera swings
 * around the board instead of cutting through it; distance and target lerp.
 * Resolves when settled (or immediately with reduced motion / ms <= 0).
 */
export function springCamera(camera, controls, toPosition, toTarget, ms = 450) {
  running.get(camera)?.();
  const target = controls ? controls.target : new THREE.Vector3();
  const fromTarget = target.clone();
  const endTarget = (toTarget || target).clone();
  const fromOff = camera.position.clone().sub(fromTarget);
  const toOff = toPosition.clone().sub(endTarget);
  const fromLen = fromOff.length() || 1e-6;
  const toLen = toOff.length() || 1e-6;
  const fromDir = fromOff.clone().divideScalar(fromLen);
  const toDir = toOff.clone().divideScalar(toLen);
  const qFull = new THREE.Quaternion().setFromUnitVectors(fromDir, toDir);
  const qIdent = new THREE.Quaternion();
  const q = new THREE.Quaternion();
  const dir = new THREE.Vector3();

  const apply = (s) => {
    q.slerpQuaternions(qIdent, qFull, s);
    dir.copy(fromDir).applyQuaternion(q);
    target.lerpVectors(fromTarget, endTarget, s);
    camera.position.copy(target).addScaledVector(dir, fromLen + (toLen - fromLen) * s);
    camera.lookAt(target);
    if (controls) controls.update();
  };

  if (!ms || ms <= 0 || reducedMotion()) {
    apply(1);
    return Promise.resolve();
  }

  // x(t) = 1 - (1 + wt) e^{-wt}; wt = 7.4 puts it within 0.5% at t = ms.
  const omega = 7.4 / (ms / 1000);
  return new Promise((resolve) => {
    let raf = 0;
    let done = false;
    const t0 = performance.now();
    const finish = () => {
      if (done) return;
      done = true;
      cancelAnimationFrame(raf);
      controls?.removeEventListener?.("start", finish);
      if (running.get(camera) === finish) running.delete(camera);
      resolve();
    };
    const step = (now) => {
      if (done) return;
      const t = (now - t0) / 1000;
      const wt = omega * t;
      let s = 1 - (1 + wt) * Math.exp(-wt);
      if (t * 1000 >= ms * 1.15) s = 1;
      apply(s);
      if (s >= 1) finish();
      else raf = requestAnimationFrame(step);
    };
    running.set(camera, finish);
    controls?.addEventListener?.("start", finish); // user grabbed the view
    raf = requestAnimationFrame(step);
  });
}

/* --------------------------------------------------------------- view cube */

const FACES = [
  // BoxGeometry material order: +X, -X, +Y, -Y, +Z, -Z
  "RIGHT", "LEFT", "TOP", "BOTTOM", "FRONT", "BACK",
];

function regionName(s) {
  const parts = [];
  if (s.y) parts.push(s.y > 0 ? "top" : "bottom");
  if (s.z) parts.push(s.z > 0 ? "front" : "back");
  if (s.x) parts.push(s.x > 0 ? "right" : "left");
  return parts.join("-");
}

function faceCanvas(label, withText) {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d");
  const grad = g.createLinearGradient(0, 0, 0, 256);
  grad.addColorStop(0, "#f4f5f7");
  grad.addColorStop(1, "#e4e6ea");
  g.fillStyle = grad;
  g.fillRect(0, 0, 256, 256);
  g.strokeStyle = "rgba(60,66,78,0.18)";
  g.lineWidth = 6;
  g.strokeRect(3, 3, 250, 250);
  if (withText) {
    g.fillStyle = "#454b56";
    g.textAlign = "center";
    g.textBaseline = "middle";
    let px = 40;
    g.font = FONT;
    while (g.measureText(label).width > 200 && px > 20) {
      px -= 2;
      g.font = FONT.replace("40px", px + "px");
    }
    g.fillText(label, 128, 131);
  }
  return c;
}

export function createViewCube(hostEl, { camera, controls, onSnap } = {}) {
  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(SIZE, SIZE);
  renderer.setClearColor(0x000000, 0);
  if ("outputColorSpace" in renderer) renderer.outputColorSpace = THREE.SRGBColorSpace;
  const canvas = renderer.domElement;
  canvas.className = "vc-cube";
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", "View cube: click a face, edge or corner to look from that side, drag to orbit");
  Object.assign(canvas.style, {
    width: SIZE + "px", height: SIZE + "px", display: "block",
    cursor: "pointer", touchAction: "none",
  });
  hostEl.appendChild(canvas);

  const scene = new THREE.Scene();
  const cam = new THREE.PerspectiveCamera(26, 1, 0.1, 20);
  scene.add(cam);
  scene.add(new THREE.HemisphereLight(0xffffff, 0xb4b9c2, 2.1));
  const key = new THREE.DirectionalLight(0xffffff, 1.1);
  key.position.set(0.6, 1, 1.2);
  cam.add(key); // light follows the viewer so the facing side stays bright

  const textures = FACES.map((l) => {
    const t = new THREE.CanvasTexture(faceCanvas(l, false));
    if ("colorSpace" in t) t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = 4;
    return t;
  });
  const mats = textures.map((map) => new THREE.MeshLambertMaterial({ map }));
  const geo = new THREE.BoxGeometry(2, 2, 2);
  const cube = new THREE.Mesh(geo, mats);
  scene.add(cube);

  const edgeGeo = new THREE.EdgesGeometry(geo);
  const edgeMat = new THREE.LineBasicMaterial({ color: 0x8a909b, transparent: true, opacity: 0.55 });
  cube.add(new THREE.LineSegments(edgeGeo, edgeMat));

  const hlMat = new THREE.MeshBasicMaterial({
    color: 0x3b82f6, transparent: true, opacity: 0.42, depthWrite: false,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  });
  const hlGeo = new THREE.BoxGeometry(1, 1, 1);
  const hl = new THREE.Mesh(hlGeo, hlMat);
  hl.visible = false;
  scene.add(hl);

  let disposed = false;
  let dirty = true;
  let hover = null; // {x,y,z}

  fontsReady().then(() => {
    if (disposed) return;
    FACES.forEach((l, i) => {
      textures[i].image = faceCanvas(l, true);
      textures[i].needsUpdate = true;
    });
    dirty = true;
    render();
  });

  const tmpOff = new THREE.Vector3();
  function syncCamera() {
    // Mirror the main camera orientation exactly (including roll).
    cam.quaternion.copy(camera.quaternion);
    cam.position.set(0, 0, 8.8).applyQuaternion(cam.quaternion);
  }

  function render() {
    if (disposed) return;
    syncCamera();
    renderer.render(scene, cam);
    dirty = false;
  }

  let lastQ = new THREE.Quaternion(2, 0, 0, 0);
  function update() {
    if (disposed) return;
    if (!dirty && lastQ.equals(camera.quaternion)) return;
    lastQ.copy(camera.quaternion);
    render();
  }

  function setHover(s) {
    const same = (a, b) => (!a && !b) || (a && b && a.x === b.x && a.y === b.y && a.z === b.z);
    if (same(s, hover)) return;
    hover = s;
    if (s) {
      const inner = 1 - BAND;
      const span = (v) => (v === 0 ? [2 * inner, 0] : [BAND * 1.02, v * (inner + BAND / 2 + 0.01)]);
      const [sx, px] = span(s.x), [sy, py] = span(s.y), [sz, pz] = span(s.z);
      hl.scale.set(sx, sy, sz);
      hl.position.set(px, py, pz);
      hl.visible = true;
      canvas.title = regionName(s).replace(/-/g, " ");
    } else {
      hl.visible = false;
      canvas.removeAttribute("title");
    }
    dirty = true;
    render();
  }

  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function pick(ev) {
    const r = canvas.getBoundingClientRect();
    ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    syncCamera();
    ray.setFromCamera(ndc, cam);
    const hit = ray.intersectObject(cube, false)[0];
    if (!hit) return null;
    const p = hit.point;
    const q = (v) => (v > 1 - BAND ? 1 : v < -(1 - BAND) ? -1 : 0);
    const s = { x: q(p.x), y: q(p.y), z: q(p.z) };
    // The face the ray hit is always part of the region.
    const n = hit.face.normal;
    if (Math.abs(n.x) > 0.5) s.x = Math.sign(n.x);
    if (Math.abs(n.y) > 0.5) s.y = Math.sign(n.y);
    if (Math.abs(n.z) > 0.5) s.z = Math.sign(n.z);
    return s;
  }

  function snap(s) {
    const target = controls ? controls.target : new THREE.Vector3();
    const dist = camera.position.distanceTo(target) || 1;
    const dir = new THREE.Vector3(s.x, s.y, s.z).normalize();
    if (!s.x && !s.z) dir.z = 1e-4; // avoid the exact pole (keeps "up" defined)
    dir.normalize();
    const to = target.clone().addScaledVector(dir, dist);
    const name = regionName(s);
    springCamera(camera, controls, to, target, 450);
    onSnap?.(name);
  }

  // Drag-to-orbit / click-to-snap.
  let drag = null;
  const ORBIT_SPEED = 0.012;
  const up = new THREE.Vector3(0, 1, 0);
  const right = new THREE.Vector3();
  const rq = new THREE.Quaternion();

  function onDown(ev) {
    if (ev.button !== undefined && ev.button !== 0) return;
    canvas.setPointerCapture?.(ev.pointerId);
    drag = { x: ev.clientX, y: ev.clientY, moved: false, id: ev.pointerId };
    ev.preventDefault();
  }
  function onMove(ev) {
    if (!drag) {
      setHover(pick(ev));
      return;
    }
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 3) return;
    if (!drag.moved) {
      drag.moved = true;
      running.get(camera)?.();
      setHover(null);
      canvas.style.cursor = "grabbing";
    }
    drag.x = ev.clientX;
    drag.y = ev.clientY;
    orbit(dx, dy);
  }
  function onUp(ev) {
    if (!drag) return;
    const wasDrag = drag.moved;
    canvas.releasePointerCapture?.(drag.id);
    drag = null;
    canvas.style.cursor = "pointer";
    if (wasDrag) {
      setHover(pick(ev));
      return;
    }
    const s = pick(ev);
    if (s) snap(s);
  }
  function onLeave() {
    if (!drag) setHover(null);
  }

  function orbit(dx, dy) {
    const target = controls ? controls.target : new THREE.Vector3();
    tmpOff.copy(camera.position).sub(target);
    rq.setFromAxisAngle(up, -dx * ORBIT_SPEED);
    tmpOff.applyQuaternion(rq);
    right.crossVectors(up, tmpOff).normalize();
    const len = tmpOff.length();
    const polar = Math.acos(THREE.MathUtils.clamp(tmpOff.y / len, -1, 1));
    let dPolar = -dy * ORBIT_SPEED;
    const minP = 0.001, maxP = Math.PI - 0.001;
    dPolar = THREE.MathUtils.clamp(polar - dPolar, minP, maxP) - polar;
    rq.setFromAxisAngle(right, -dPolar);
    tmpOff.applyQuaternion(rq);
    camera.position.copy(target).add(tmpOff);
    camera.lookAt(target);
    if (controls) controls.update();
    update();
  }

  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", onUp);
  canvas.addEventListener("pointerleave", onLeave);
  const onChange = () => update();
  controls?.addEventListener?.("change", onChange);

  render();

  return {
    update,
    dispose() {
      if (disposed) return;
      disposed = true;
      controls?.removeEventListener?.("change", onChange);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointercancel", onUp);
      canvas.removeEventListener("pointerleave", onLeave);
      textures.forEach((t) => t.dispose());
      mats.forEach((m) => m.dispose());
      geo.dispose(); edgeGeo.dispose(); edgeMat.dispose(); hlGeo.dispose(); hlMat.dispose();
      renderer.dispose();
      renderer.forceContextLoss?.();
      canvas.remove();
    },
  };
}

function fontsReady() {
  if (!document.fonts) return Promise.resolve();
  const load = document.fonts.load ? document.fonts.load(FONT, "TOPBACK").catch(() => {}) : null;
  return Promise.all([document.fonts.ready, load]).then(() => {}, () => {});
}

/* ---------------------------------------------------------- camera buttons */

const CSS = `
.vc-buttons{display:flex;flex-direction:column;gap:8px;align-items:center}
.vc-btn{width:34px;height:34px;border-radius:50%;border:1px solid rgba(20,24,32,.12);
  background:#fff;color:#3a404b;display:grid;place-items:center;padding:0;margin:0;cursor:pointer;
  box-shadow:0 1px 2px rgba(20,24,32,.06),0 2px 6px rgba(20,24,32,.05);
  transition:transform .32s cubic-bezier(.34,1.56,.64,1),background-color .15s ease,border-color .15s ease,color .15s ease;
  -webkit-tap-highlight-color:transparent}
.vc-btn svg{width:18px;height:18px;display:block;pointer-events:none}
.vc-btn:hover{background:#f5f6f8;border-color:rgba(20,24,32,.2);color:#1d222b}
.vc-btn:active{transform:scale(.92);transition-duration:.08s;transition-timing-function:ease-out}
.vc-btn:focus-visible{outline:2px solid #3b82f6;outline-offset:2px}
.vc-btn[aria-pressed="true"]{color:#2563eb;border-color:rgba(37,99,235,.35);background:#eff5ff}
.vc-sep{width:16px;height:1px;background:rgba(20,24,32,.1);margin:0}
@media (prefers-reduced-motion: reduce){.vc-btn,.vc-btn:active{transition:none;transform:none}}
`;

function injectCss() {
  if (document.getElementById("vc-style")) return;
  const s = document.createElement("style");
  s.id = "vc-style";
  s.textContent = CSS;
  document.head.appendChild(s);
}

const svg = (body) =>
  `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

const ICONS = {
  home: svg('<path d="M3.5 9.2 10 3.8l6.5 5.4"/><path d="M5.3 8v7.7h3.3v-4.4h2.8v4.4h3.3V8"/>'),
  fit: svg('<path d="M3.5 7.5v-4h4M12.5 3.5h4v4M16.5 12.5v4h-4M7.5 16.5h-4v-4"/><rect x="7" y="7" width="6" height="6" rx="1"/>'),
  zoomIn: svg('<path d="M10 4.5v11M4.5 10h11"/>'),
  zoomOut: svg('<path d="M4.5 10h11"/>'),
  // Perspective: back face smaller than the front. Ortho: equal, parallel faces.
  persp: svg('<rect x="3.5" y="7" width="9.5" height="9.5" rx=".8"/><rect x="10" y="3.5" width="6.5" height="6.5" rx=".6"/><path d="M3.5 7 10 3.5M13 7l3.5-3.5M13 16.5l3.5-6.5"/>'),
  ortho: svg('<rect x="3.5" y="7" width="9.5" height="9.5" rx=".8"/><path d="M3.5 7 7 3.5h9.5V13L13 16.5M13 7l3.5-3.5"/>'),
};

export function createCameraButtons(hostEl, { home, fit, zoomIn, zoomOut, toggleOrtho } = {}) {
  injectCss();
  const wrap = document.createElement("div");
  wrap.className = "vc-buttons";
  wrap.setAttribute("role", "toolbar");
  wrap.setAttribute("aria-orientation", "vertical");
  wrap.setAttribute("aria-label", "Camera");

  const make = (label, icon, fn) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "vc-btn";
    b.title = label;
    b.setAttribute("aria-label", label);
    b.innerHTML = icon;
    b.addEventListener("click", (e) => fn?.(e));
    wrap.appendChild(b);
    return b;
  };

  make("Home view", ICONS.home, home);
  make("Fit all", ICONS.fit, fit);
  make("Zoom in", ICONS.zoomIn, zoomIn);
  make("Zoom out", ICONS.zoomOut, zoomOut);
  let ortho = false;
  const orthoBtn = make("Switch to orthographic", ICONS.persp, () => toggleOrtho?.(!ortho));
  orthoBtn.setAttribute("aria-pressed", "false");

  function setOrtho(on) {
    ortho = !!on;
    orthoBtn.innerHTML = ortho ? ICONS.ortho : ICONS.persp;
    orthoBtn.setAttribute("aria-pressed", String(ortho));
    const label = ortho ? "Switch to perspective" : "Switch to orthographic";
    orthoBtn.title = label;
    orthoBtn.setAttribute("aria-label", label);
  }

  hostEl.appendChild(wrap);
  return {
    setOrtho,
    dispose() {
      wrap.remove();
    },
  };
}

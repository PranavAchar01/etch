// Etch 3D board viewer (2026-10-16). The finished board as KiCad's own GLB export (library STEP models on every
// part, copper tracks, pads, silk, solder mask), shown at the same hero angle as the 2D KiCad renders:
// kicad-cli --rotate -40,0,25 --perspective. Orbit / pan / zoom with mouse or touch; tap a part to see what it is.
// 2026-10-17: zoom to the cursor down to a single via, eased reset, and the board's real copper pours.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

const REF = /^[A-Z]{1,3}\d{1,3}$/;
const LIFT = (ref) => (/^(J|BZ|BT|U)/.test(ref) ? 0.010 : /^(Q|D|C)/.test(ref) ? 0.0065 : 0.0035); // metres, like the 2D lifts
const LED_RGB = { red: 0xff3a2a, green: 0x3dff6e, blue: 0x4a7dff, yellow: 0xffd23a, orange: 0xff8a2a, white: 0xffffff, purple: 0xc04aff, pink: 0xff5ab8, cyan: 0x3affff, magenta: 0xff3aff };

const ARM_INFO = {
  name: "SO-101 arm", value: "", why: "The arm this board drives over the STS3215 servo bus on J2 (shown folded at rest).",
  source: "https://github.com/TheRobotStudio/SO-ARM100/tree/a758567c3978dfeefe282ede0500085a48fe8f78/Simulation/SO101", licence: "Apache-2.0, TheRobotStudio SO-ARM100",
};

export function createViewer(host) {
  const W = 1380, H = 874;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.92;
  renderer.setSize(W, H, false);
  const cv = renderer.domElement;
  cv.id = "v3c";
  host.appendChild(cv);
  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.42;
  const key = new THREE.DirectionalLight(0xffffff, 1.6);
  key.position.set(-0.4, 1, 0.8);
  scene.add(key, new THREE.HemisphereLight(0xcfe8d6, 0x0a120c, 0.35));
  const cam = new THREE.PerspectiveCamera(45, W / H, 0.001, 10);
  const ctl = new OrbitControls(cam, cv);
  ctl.enableDamping = true;
  ctl.dampingFactor = 0.12;
  ctl.rotateSpeed = 0.8;
  ctl.zoomSpeed = 0.9;
  ctl.screenSpacePanning = true;
  ctl.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
  // 2026-10-17 (his ask "move around the board"): the wheel / pinch zooms toward the point under the cursor, so a
  // close look at one pad or via is one gesture; right-drag or two fingers pan, double-click resets (app.js)
  ctl.zoomToCursor = true;
  let dirty = true, model = null, arm = null, parts = {}, home = null, explode = 0, picked = null, leds = {}, info = {}, onPick = null, alive = true;
  ctl.addEventListener("change", () => (dirty = true));
  // QA round 5: half the pixel ratio while the board is dragged or zoomed, full again once the damping settles
  let ratio = 1, dragging = false, settle = false;
  let anim = null, onStart = null;
  ctl.addEventListener("start", () => { anim = null; if (onStart) onStart(); dragging = true; settle = false; const r = Math.max(0.75, ratio / 2); if (renderer.getPixelRatio() !== r) { renderer.setPixelRatio(r); renderer.setSize(W, H, false); dirty = true; } });
  ctl.addEventListener("end", () => { dragging = false; settle = true; });

  function fitRatio() {
    const r = cv.getBoundingClientRect(), s = r.width ? r.width / W : 1;
    ratio = Math.min(2, Math.max(0.75, (window.devicePixelRatio || 1) * s));
    if (dragging) return;
    renderer.setPixelRatio(ratio);
    renderer.setSize(W, H, false);
    dirty = true;
  }

  // QA round 5: KiCad's GLB has ~10k primitives (one per STEP solid face set), and three made a mesh and a material
  // for each: ~10k draw calls and state changes a frame, CPU-bound at ~38 fps while dragging. Each refdes node (and
  // each board node) now becomes one mesh per material, with the materials shared inside the node.
  function mergeNode(n, isPart, ref) {
    n.updateMatrixWorld(true);
    const inv = new THREE.Matrix4().copy(n.matrixWorld).invert(), meshes = [], mats = new Map(), groups = new Map();
    n.traverse((o) => { if (o.isMesh) meshes.push(o); });
    for (const o of meshes) {
      const src = Array.isArray(o.material) ? o.material[0] : o.material;
      let m = mats.get(src);
      if (!m) { m = material(src, !isPart); mats.set(src, m); }
      if (o === n) {
        // the node is itself a mesh (KiCad's mask/copper layers): keep it in place, only swap the material
        o.material = m;
        if (isPart) o.userData.ref = ref;
        if (!isPart && m.transparent) o.renderOrder = 2;
        continue;
      }
      const g = o.geometry.clone().applyMatrix4(new THREE.Matrix4().multiplyMatrices(inv, o.matrixWorld));
      if (!groups.has(m)) groups.set(m, []);
      groups.get(m).push(g);
      o.parent.remove(o);
    }
    const out = [];
    for (const [m, gs] of groups) {
      let merged = gs.length === 1 ? gs[0] : null;
      if (!merged) {
        const names = Object.keys(gs[0].attributes).filter((k) => gs.every((g) => g.attributes[k]));
        const mixed = gs.some((g) => g.index) && gs.some((g) => !g.index);
        const ready = gs.map((g) => { const h = mixed && g.index ? g.toNonIndexed() : g; for (const k of Object.keys(h.attributes)) if (!names.includes(k)) h.deleteAttribute(k); h.morphAttributes = {}; return h; });
        merged = mergeGeometries(ready, false);
      }
      const parts2 = merged ? [merged] : gs; // merge refused (attribute mismatch): keep the pieces as they were
      for (const g of parts2) {
        const mesh = new THREE.Mesh(g, m);
        if (isPart) mesh.userData.ref = ref;
        if (!isPart && m.transparent) mesh.renderOrder = 2;
        n.add(mesh);
      }
      out.push(m);
    }
    return out;
  }

  // KiCad's GLB colours, made to read like a real board: glossy dark-green mask the copper shows through,
  // ENIG gold pads, copper tracks, matte FR4 edge, plastic part bodies (the STEP colours come in as metal)
  function material(m, onBoard) {
    const c = m.color, a = m.opacity;
    const near = (r, g, b, e = 0.03) => Math.abs(c.r - r) < e && Math.abs(c.g - g) < e && Math.abs(c.b - b) < e;
    const lin = (hex) => new THREE.Color(hex);
    if (onBoard) {
      if (m.transparent && c.g > c.r * 4) return new THREE.MeshPhysicalMaterial({ color: lin(0x064a22), transparent: true, opacity: 0.8, roughness: 0.55, metalness: 0, clearcoat: 0.45, clearcoatRoughness: 0.25, envMapIntensity: 0.5, depthWrite: false });
      if (near(0.7, 0.61, 0.0, 0.05)) return new THREE.MeshStandardMaterial({ color: lin(0xd9b45a), metalness: 1, roughness: 0.26 });
      if (near(0.5, 0.5, 0.5)) return new THREE.MeshStandardMaterial({ color: lin(0xe8a462), metalness: 1, roughness: 0.28 });
      if (c.r > 0.9 && c.g > 0.9) return new THREE.MeshStandardMaterial({ color: lin(0xe8ebe6), metalness: 0, roughness: 0.85 });
      if (a < 1 && c.r > 0.3) return new THREE.MeshStandardMaterial({ color: lin(0x6b6a45), metalness: 0, roughness: 0.8 });
      return new THREE.MeshStandardMaterial({ color: c.clone(), metalness: 0.1, roughness: 0.7 });
    }
    const max = Math.max(c.r, c.g, c.b), min = Math.min(c.r, c.g, c.b);
    const metal = max > 0.35 && max - min < 0.12; // pins and leads: light, unsaturated
    return new THREE.MeshStandardMaterial({ color: c.clone(), metalness: metal ? 0.95 : 0, roughness: metal ? 0.32 : 0.42, transparent: a < 1, opacity: a });
  }

  // the top and bottom copper heights in KiCad's GLB (its track meshes), in the wrapper node's frame
  function copperHeights(top) {
    top.updateMatrixWorld(true);
    const inv = new THREE.Matrix4().copy(top.matrixWorld).invert(), b = new THREE.Box3(), g = new THREE.Box3();
    for (const n of top.children) {
      if (REF.test(n.name || "")) continue;
      n.traverse((o) => {
        if (!o.isMesh) return;
        const c = (Array.isArray(o.material) ? o.material[0] : o.material).color;
        if (Math.abs(c.r - 0.5) > 0.03 || Math.abs(c.g - 0.5) > 0.03 || Math.abs(c.b - 0.5) > 0.03) return;
        if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
        g.copy(o.geometry.boundingBox).applyMatrix4(new THREE.Matrix4().multiplyMatrices(inv, o.matrixWorld));
        b.union(g);
      });
    }
    return b.isEmpty() ? null : { top: b.max.y, bot: b.min.y };
  }

  // the board's real copper pours (KiCad zone fills from the .kicad_pcb, see /etch/layers): flat copper just under
  // the tracks' top face, so the mask shows them the way a real board does (lighter where copper is, dark clearance
  // gaps round every track and pad). KiCad's GLB frame: x = board x, z = board y, metres; the pours come in um.
  let pourMat = null;
  function addPours(top, pours, cu) {
    pourMat = pourMat || new THREE.MeshStandardMaterial({ color: 0x3a2a14, metalness: 0.35, roughness: 0.6, side: THREE.DoubleSide });
    for (const [side, y] of [["F", cu.top - 10e-6], ["B", cu.bot + 5e-6]]) {
      const pos = [], idx = [];
      for (const p of pours[side] || []) {
        let pts = [];
        for (let i = 0; i + 1 < p.length; i += 2) pts.push(new THREE.Vector2(p[i] / 1e6, p[i + 1] / 1e6));
        if (pts.length > 3 && pts[0].equals(pts[pts.length - 1])) pts.pop();
        if (pts.length < 3) continue;
        const tri = THREE.ShapeUtils.triangulateShape(pts, []), base = pos.length / 3;
        for (const v of pts) pos.push(v.x, y, v.y);
        for (const t of tri) idx.push(base + t[0], base + t[1], base + t[2]);
      }
      if (!idx.length) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      g.setIndex(idx);
      const nrm = new Float32Array(pos.length); for (let i = 1; i < nrm.length; i += 3) nrm[i] = side === "F" ? 1 : -1;
      g.setAttribute("normal", new THREE.BufferAttribute(nrm, 3));
      const m = new THREE.Mesh(g, pourMat); m.name = "pour_" + side;
      top.add(m);
    }
  }

  async function load(buf, meta, target, armBuf, layers) {
    if (model) { scene.remove(model.outer); model = null; }
    arm = null; armMats = []; armFade = 1;
    const gltf = await new GLTFLoader().parseAsync(buf, "");
    const root = gltf.scene;
    parts = {};
    let top = root; // KiCad wraps everything in one unnamed node; the refdes nodes sit under it
    while (top.children.length === 1 && !REF.test(top.children[0].name || "")) top = top.children[0];
    const cu = copperHeights(top);
    for (const n of [...top.children]) {
      const isPart = REF.test(n.name || "");
      const mats = mergeNode(n, isPart, n.name);
      if (isPart) parts[n.name] = { node: n, y0: n.position.y, mats };
    }
    if (layers?.pours && cu) addPours(top, layers.pours, cu);
    // glTF is Y-up; KiCad's 3D frame is Z-up out of the board. inner: glTF -> KiCad frame, outer: the view rotation
    const box = new THREE.Box3().setFromObject(root), ctr = box.getCenter(new THREE.Vector3());
    root.position.sub(ctr);
    const inner = new THREE.Group(); inner.rotation.x = Math.PI / 2; inner.add(root);
    if (armBuf) {
      // the SO-101 this board drives (TheRobotStudio SO-ARM100, folded rest pose), standing on the desk beside the board.
      // Its GLB is Z-up in metres; the board's glTF frame is Y-up, so turn it once and set it on the board plane.
      const a = (await new GLTFLoader().parseAsync(armBuf, "")).scene;
      a.rotation.x = -Math.PI / 2;
      a.updateMatrixWorld(true);
      const ab = new THREE.Box3().setFromObject(a);
      a.position.set(box.max.x - ctr.x + 0.02 - ab.min.x, box.min.y - ctr.y - ab.min.y, -(ab.min.z + ab.max.z) / 2);
      // own material copies so the arm can fade out on a close look at the board without touching the board's
      a.traverse((o) => { if (o.isMesh) { o.userData.arm = true; o.material = o.material.clone(); armMats.push(o.material); } });
      inner.add(a);
      arm = a;
    }
    const outer = new THREE.Group(); outer.rotation.set(THREE.MathUtils.degToRad(-40), 0, THREE.MathUtils.degToRad(25), "XYZ"); outer.add(inner);
    scene.add(outer);
    model = { outer, inner, root, size: box.getSize(new THREE.Vector3()) };
    info = meta || {};
    for (const [r, p] of Object.entries(parts)) {
      const col = /^D\d/.test(r) && LED_RGB[String(info.parts?.[r]?.value || "").toLowerCase()];
      if (col) for (const m of p.mats) if (!m.metalness && m.color.r > m.color.g * 3) { m.color.setHex(col).multiplyScalar(0.55); m.transparent = true; m.opacity = 0.88; m.roughness = 0.2; }
    }
    frame(target);
    fitRatio();
    dirty = true;
  }

  // camera straight down the KiCad view axis, distance set so the board fills the same box the 2D render used;
  // a view offset puts the board's centre on that box's centre, so orbiting still turns about the board
  function frame(target) {
    const t = target || [140, 90, 1100, 700];
    cam.clearViewOffset(); cam.up.set(0, 1, 0); ctl.target.set(0, 0, 0);
    const pts = [], b = new THREE.Box3().setFromObject(model.outer);
    for (const x of [b.min.x, b.max.x]) for (const y of [b.min.y, b.max.y]) for (const z of [b.min.z, b.max.z]) pts.push(new THREE.Vector3(x, y, z));
    let d = 0.2;
    for (let i = 0; i < 6; i++) {
      cam.position.set(0, 0, d); cam.lookAt(0, 0, 0); cam.updateMatrixWorld(); cam.updateProjectionMatrix();
      let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
      for (const p of pts) { const q = p.clone().project(cam); x0 = Math.min(x0, q.x); x1 = Math.max(x1, q.x); y0 = Math.min(y0, q.y); y1 = Math.max(y1, q.y); }
      // symmetric about the board centre, so a model beside the board (the SO-101) stays in frame too
      d *= Math.max((2 * Math.max(-x0, x1)) / ((t[2] / W) * 2), (2 * Math.max(-y0, y1)) / ((t[3] / H) * 2));
    }
    cam.position.set(0, 0, d); cam.lookAt(0, 0, 0);
    cam.setViewOffset(W, H, W / 2 - (t[0] + t[2] / 2), H / 2 - (t[1] + t[3] / 2), W, H);
    cam.updateProjectionMatrix();
    // close enough to read a 0.3 mm via, far enough to see the whole desk (was 0.3 to 3 x the framing distance)
    ctl.minDistance = d * 0.02; ctl.maxDistance = d * 3.5;
    clip();
    home = { pos: cam.position.clone(), tgt: ctl.target.clone() };
    ctl.update();
  }

  // near / far follow the zoom, so a close look is not clipped and the depth buffer stays fine at the board
  function clip() {
    const dist = cam.position.distanceTo(ctl.target);
    const near = Math.max(1e-5, dist * 0.02), far = dist * 12 + 0.5;
    if (Math.abs(near - cam.near) > near * 0.05 || Math.abs(far - cam.far) > far * 0.05) { cam.near = near; cam.far = far; cam.updateProjectionMatrix(); }
  }
  function setExplode(x) { if (Math.abs(x - explode) < 1e-4) return; explode = x; for (const [r, p] of Object.entries(parts)) p.node.position.y = p.y0 + LIFT(r) * explode; dirty = true; }
  function setLeds(on) {
    const k = JSON.stringify(on); if (k === JSON.stringify(leds)) return; leds = on;
    for (const [r, p] of Object.entries(parts)) {
      if (!/^D\d/.test(r)) continue;
      const v = on[r], col = LED_RGB[String(info.parts?.[r]?.value || "").toLowerCase()] ?? 0xff4030;
      for (const m of p.mats) if (!m.metalness) { m.emissive.setHex(v ? col : 0x000000); m.emissiveIntensity = v ? 0.9 : 0; }
    }
    if (picked) highlight(picked);
    dirty = true;
  }
  function highlight(ref) {
    for (const [r, p] of Object.entries(parts)) {
      for (const m of p.mats) {
        if (r === ref) { m.emissive.setHex(0x1f8a3a); m.emissiveIntensity = 0.45; }
        else if (!(leds[r] && !m.metalness)) { m.emissive.setHex(0x000000); m.emissiveIntensity = 0; }
      }
    }
    dirty = true;
  }

  // a tap (not a drag) picks the part under the finger
  const ray = new THREE.Raycaster(), down = { x: 0, y: 0, t: 0 };
  cv.addEventListener("pointerdown", (e) => { down.x = e.clientX; down.y = e.clientY; down.t = performance.now(); });
  cv.addEventListener("pointerup", (e) => {
    if (!model || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6 || performance.now() - down.t > 500) return;
    const r = cv.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), cam);
    const hit = ray.intersectObjects(Object.values(parts).map((p) => p.node).concat(arm ? [arm] : []), true)[0];
    const ref = hit?.object.userData.ref || (hit?.object.userData.arm ? "ARM" : null);
    picked = ref === picked ? null : ref;
    if (picked) highlight(picked); else highlight(null);
    if (onPick) onPick(picked, picked ? (picked === "ARM" ? ARM_INFO : info.parts?.[picked]) : null, picked ? screenOf(picked) : null);
  });
  function screenOf(ref) {
    const n = ref === "ARM" ? arm : parts[ref]?.node; if (!n) return null;
    const b = new THREE.Box3().setFromObject(n), c = b.getCenter(new THREE.Vector3()).project(cam);
    return [((c.x + 1) / 2) * W, ((1 - c.y) / 2) * H];
  }
  // eased back to the starting view (double-click, or the RESET VIEW button)
  function reset() { if (!home) return; anim = { t0: performance.now(), pos: cam.position.clone(), tgt: ctl.target.clone() }; dirty = true; }
  // zooming in on the board fades the SO-101 out (gone below ~45% of the home distance) so it never fills the
  // camera; zooming toward the arm itself keeps it
  let armMats = [], armFade = 1;
  const armBox = new THREE.Box3(), boardBox = new THREE.Box3();
  function fadeArm() {
    if (!arm || !home) return;
    const z = cam.position.distanceTo(ctl.target) / home.pos.distanceTo(home.tgt);
    armBox.setFromObject(arm); boardBox.setFromObject(model.root);
    const atArm = armBox.distanceToPoint(ctl.target) < boardBox.distanceToPoint(ctl.target);
    const f = atArm ? 1 : THREE.MathUtils.smoothstep(z, 0.45, 0.75);
    if (Math.abs(f - armFade) < 0.01 && !(f === 1 && armFade !== 1) && !(f === 0 && armFade !== 0)) return;
    armFade = f; arm.visible = f > 0.01;
    for (const m of armMats) { m.transparent = f < 1; m.opacity = f; m.depthWrite = f >= 1; m.needsUpdate = true; }
  }
  function moved() { return !!home && (cam.position.distanceTo(home.pos) > 1e-4 * home.pos.length() || ctl.target.distanceTo(home.tgt) > 1e-4 * home.pos.length()); }

  function tick() {
    if (!alive) return;
    if (anim) {
      const u = Math.min(1, (performance.now() - anim.t0) / 450), e = 1 - Math.pow(1 - u, 3);
      cam.position.lerpVectors(anim.pos, home.pos, e); ctl.target.lerpVectors(anim.tgt, home.tgt, e);
      if (u >= 1) anim = null;
      dirty = true;
    }
    if (ctl.update()) dirty = true;
    else if (settle) { settle = false; fitRatio(); }
    if (dirty && model) { clip(); fadeArm(); renderer.render(scene, cam); dirty = false; }
    requestAnimationFrame(tick);
  }
  tick();
  addEventListener("resize", fitRatio);
  // QA round 4: load() ran fitRatio while the viewer was still hidden (width 0), so the canvas stayed at 2x of
  // 1380 px (2760 x 1748) on a ~736 px box and rotation fell to ~30 fps; refit whenever the box changes size
  if (window.ResizeObserver) new ResizeObserver(fitRatio).observe(cv);
  return {
    load, setExplode, setLeds, reset, moved, canvas: cv,
    set onPick(f) { onPick = f; },
    set onStart(f) { onStart = f; },
    clearPick() { picked = null; highlight(null); },
    pickedRef: () => picked,
    // one frame as a PNG data URL (for checking the look; the canvas keeps no buffer between frames)
    snap(bg = "#020402") { renderer.render(scene, cam); const c = document.createElement("canvas"); c.width = cv.width; c.height = cv.height; const g = c.getContext("2d"); g.fillStyle = bg; g.fillRect(0, 0, c.width, c.height); g.drawImage(cv, 0, 0); return c.toDataURL("image/png"); },
    screenOf,
    dispose() { alive = false; renderer.dispose(); cv.remove(); },
  };
}

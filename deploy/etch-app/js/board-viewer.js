// Etch board viewer for the Explore library and board view (2026-10-17). A sibling of viewer3d.js (which the build
// app keeps using unchanged): fills its container, light studio look with an infinite mm grid floor, coloured axes,
// a soft contact shadow, orbit / two-finger pan / pinch zoom, parts picked by their GLB node name (parts.json `node`
// is the source of truth, so refdes like USB-UART1 or SW_BOOT1 work), hover highlight, live solder mask /
// silkscreen / pad finish recolour, and the clicked 3D point returned for comment anchors. Renders on demand.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";

export const MASKS = { green: 0x0a5428, purple: 0x4b2a83, red: 0xa82222, yellow: 0xd4b21c, blue: 0x173f93, white: 0xf1f1ee, black: 0x161616 };
export const SILKS = { white: 0xf5f5f1, black: 0x1b1b1b, yellow: 0xf3d43c };
export const FINISHES = {
  HASL: { color: 0xcfd3d7, metalness: 1, roughness: 0.3 },
  ENIG: { color: 0xdfb85e, metalness: 1, roughness: 0.24 },
};
const HOVER = new THREE.Color(0x2f6bff).multiplyScalar(0.22), SELECT = new THREE.Color(0x2f6bff).multiplyScalar(0.42);
// fallback when there's no parts.json: refdes-looking node names (U6, R12, USB-UART1, SW_BOOT1, LED3A)
const REFLIKE = /^[A-Z][A-Za-z0-9]*([_-][A-Za-z0-9]+)*\d+[A-Za-z]?$/;
const sanitize = (s) => String(s || "").replace(/\s/g, "_").replace(/[\[\]\.:\/]/g, "");

let loader = null;
function gltfLoader() {
  if (!loader) { loader = new GLTFLoader(); loader.setMeshoptDecoder(MeshoptDecoder); }
  return loader;
}

export function createBoardViewer(host) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  const cv = renderer.domElement;
  cv.className = "bv-canvas";
  host.appendChild(cv);
  const labels = document.createElement("div");
  labels.className = "bv-labels";
  host.appendChild(labels);

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.55;
  const key = new THREE.DirectionalLight(0xffffff, 1.5);
  key.position.set(-0.5, 1, 0.7);
  const fill = new THREE.HemisphereLight(0xffffff, 0xd8d8d2, 0.7);
  scene.add(key, fill);
  const cam = new THREE.PerspectiveCamera(32, 1, 0.001, 50);
  cam.position.set(-0.08, 0.1, 0.16);

  // trackpads: two-finger scroll pans, pinch (wheel + ctrlKey) zooms; a mouse wheel still zooms. This listener goes
  // on before OrbitControls' own so it can take the scroll first.
  let ctl = null;
  cv.addEventListener("wheel", (e) => {
    if (!ctl || e.ctrlKey || e.metaKey || e.deltaMode !== 0) return; // pinch / mouse wheel lines -> OrbitControls zooms
    const mouseLike = e.deltaX === 0 && Math.abs(e.deltaY) >= 40 && Number.isInteger(e.deltaY);
    if (mouseLike) return;
    e.preventDefault(); e.stopImmediatePropagation();
    anim = null;
    ctl._pan(-e.deltaX, -e.deltaY);
    ctl.update();
    dirty = true;
  }, { passive: false });
  ctl = new OrbitControls(cam, cv);
  ctl.enableDamping = true;
  ctl.dampingFactor = 0.12;
  ctl.rotateSpeed = 0.75;
  ctl.zoomSpeed = 0.9;
  ctl.panSpeed = 0.9;
  ctl.screenSpacePanning = true;
  ctl.zoomToCursor = true;
  // all the way round, underside included: the floor steps aside while the camera is below it
  ctl.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };

  let dirty = true, alive = true, model = null, anim = null, home = null;
  let parts = {}, info = {}, hovered = null, selected = null, extent = 0.08;
  const ev = { pick: null, hover: null, frame: null, start: null, ready: null };
  ctl.addEventListener("change", () => (dirty = true));
  ctl.addEventListener("start", () => { anim = null; if (ev.start) ev.start(); });

  // ---------- floor: a fading mm grid, red X / green Z axes, ruler labels, contact shadow ----------
  const gridMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { minor: { value: 0.005 }, major: { value: 0.02 }, fade: { value: 0.2 }, floor: { value: new THREE.Color(0xeeeeec) }, line: { value: new THREE.Color(0xe2e2df) }, line2: { value: new THREE.Color(0xd2d2ce) } },
    vertexShader: "varying vec3 vW; void main(){ vec4 w = modelMatrix * vec4(position,1.0); vW = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }",
    fragmentShader: `uniform float minor, major, fade; uniform vec3 floor, line, line2; varying vec3 vW;
      float gridLine(vec2 p, float s){ vec2 g = abs(fract(p / s - 0.5) - 0.5) / fwidth(p / s); return 1.0 - min(min(g.x, g.y), 1.0); }
      void main(){
        float d = length(vW.xz);
        float a = 1.0 - smoothstep(fade * 0.55, fade, d);
        float m1 = gridLine(vW.xz, minor), m2 = gridLine(vW.xz, major);
        vec3 c = mix(floor, line, m1 * 0.8); c = mix(c, line2, m2 * 0.9);
        gl_FragColor = vec4(c, a * 0.95);
        #include <colorspace_fragment>
      }`,
  });
  const grid = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), gridMat);
  grid.rotation.x = -Math.PI / 2; grid.renderOrder = -2;
  const axisMat = (c) => new THREE.LineBasicMaterial({ color: c, transparent: true, opacity: 0.85, depthWrite: false });
  const axisGeo = (a, b) => new THREE.BufferGeometry().setFromPoints([a, b]);
  const axX = new THREE.Line(axisGeo(new THREE.Vector3(-1, 0, 0), new THREE.Vector3(1, 0, 0)), axisMat(0xe5484d));
  const axZ = new THREE.Line(axisGeo(new THREE.Vector3(0, 0, -1), new THREE.Vector3(0, 0, 1)), axisMat(0x30a46c));
  axX.renderOrder = axZ.renderOrder = -1;
  const shadowTex = (() => {
    const c = document.createElement("canvas"); c.width = c.height = 256;
    const g = c.getContext("2d"); g.filter = "blur(22px)"; g.fillStyle = "rgba(0,0,0,0.9)";
    g.beginPath(); g.roundRect(48, 48, 160, 160, 18); g.fill();
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
  })();
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: shadowTex, transparent: true, opacity: 0.32, depthWrite: false, toneMapped: false }));
  shadow.rotation.x = -Math.PI / 2; shadow.position.y = 0.00005; shadow.renderOrder = -1;
  const floorGroup = new THREE.Group(); floorGroup.add(grid, axX, axZ);
  scene.add(floorGroup, shadow);
  let rulers = [];
  function buildFloor() {
    const step = extent / 4;
    grid.scale.set(extent * 6, extent * 6, 1);
    gridMat.uniforms.minor.value = step / 4; gridMat.uniforms.major.value = step; gridMat.uniforms.fade.value = extent * 2.4;
    axX.scale.setScalar(extent * 1.25); axZ.scale.setScalar(extent * 1.25);
    labels.textContent = ""; rulers = [];
    const fx = model ? model.size.x / 2 + step * 0.4 : 0, fz = model ? model.size.z / 2 + step * 0.4 : 0;
    for (let i = -4; i <= 4; i++) {
      if (!i) continue;
      const mm = Math.round(i * step * 1000);
      for (const ax of ["x", "z"]) {
        if (Math.abs(i * step) < (ax === "x" ? fx : fz)) continue; // under the board
        const el = document.createElement("span"); el.className = "bv-ruler " + ax; el.textContent = `${mm} mm`;
        labels.appendChild(el);
        rulers.push({ el, p: ax === "x" ? new THREE.Vector3(i * step, 0, 0) : new THREE.Vector3(0, 0, i * step) });
      }
    }
  }
  let floorOn = true, photo = false;
  function setFloor(on) { floorOn = on; floorGroup.visible = on && !photo; labels.style.display = floorGroup.visible ? "" : "none"; dirty = true; }
  function setPhoto(on) { photo = on; renderer.toneMappingExposure = on ? 1.12 : 1.0; key.intensity = on ? 2.0 : 1.5; setFloor(floorOn); }

  // ---------- size: follow the container ----------
  let W = 1, H = 1;
  function fit() {
    const r = host.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width)), hh = Math.max(1, Math.round(r.height));
    if (w === W && hh === H) return;
    W = w; H = hh;
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setSize(W, H, false);
    cv.style.width = W + "px"; cv.style.height = H + "px";
    cam.aspect = W / H; cam.updateProjectionMatrix();
    dirty = true;
  }
  new ResizeObserver(fit).observe(host);
  fit();

  // ---------- materials ----------
  const boardMats = { mask: [], silk: [], pad: [], copper: [] };
  let look = { mask: "green", silk: "white", finish: "HASL" };
  function classify(m) {
    const n = (m.name || "").toLowerCase(), c = m.color || new THREE.Color(1, 1, 1), a = m.opacity ?? 1;
    if (/mask/.test(n)) return "mask";
    if (/silk|legend/.test(n)) return "silk";
    if (/pad|finish|enig|hasl|plating|gold/.test(n)) return "pad";
    if (/copper|track|_cu|cu_/.test(n)) return "copper";
    if (/body|fr4|substrate|core|board|edge/.test(n)) return "body";
    const near = (r, g, b, e = 0.05) => Math.abs(c.r - r) < e && Math.abs(c.g - g) < e && Math.abs(c.b - b) < e;
    const tan = c.r > 0.25 && c.g > 0.25 && c.b < c.g * 0.85 && Math.abs(c.r - c.g) < 0.12;
    if ((m.transparent || a < 1) && !tan && !(c.r > 0.85 && c.g > 0.85 && c.b > 0.85)) return "mask";
    if (near(0.7, 0.61, 0.0, 0.08) || (c.r > 0.6 && c.g > 0.45 && c.b < 0.15)) return "pad";
    if (near(0.5, 0.5, 0.5, 0.03)) return "pad"; // KiCad's copper: exposed pads, and tracks seen through the mask
    if (c.r > 0.82 && c.g > 0.82 && c.b > 0.82) return "silk";
    return "body";
  }
  // KiCad writes its board-layer colours (mask, silk, FR4) as sRGB values in glTF's linear baseColorFactor; read them
  // back as sRGB so the 3D view matches KiCad's own render (otherwise a dark green mask comes out pale)
  let kicad = false, startFinish = "ENIG";
  const own = (c) => (c ? (kicad ? c.clone().convertSRGBToLinear() : c.clone()) : null);
  function boardMaterial(kind, src) {
    let m;
    // the board keeps the GLB's own colours until someone picks a swatch
    if (kind === "mask") m = new THREE.MeshPhysicalMaterial({ color: own(src.color) || MASKS.green, transparent: true, opacity: Math.max(0.9, src.opacity ?? 0.9), roughness: 0.45, metalness: 0, clearcoat: 0.35, clearcoatRoughness: 0.3, envMapIntensity: 0.6, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2 });
    else if (kind === "silk") m = new THREE.MeshStandardMaterial({ color: own(src.color) || SILKS.white, roughness: 0.85, metalness: 0, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4 });
    else if (kind === "pad") m = new THREE.MeshStandardMaterial({ ...FINISHES[startFinish] }); // KiCad renders bare copper as gold
    else if (kind === "copper") m = new THREE.MeshStandardMaterial({ color: own(src.color) || 0x7a7a7a, metalness: 0.5, roughness: 0.5 }); // seen through the mask: a lighter tint, like KiCad
    else m = new THREE.MeshStandardMaterial({ color: own(src.color) || 0x8a7d4a, roughness: 0.8, metalness: 0, transparent: false });
    m.userData.kind = kind;
    if (boardMats[kind]) boardMats[kind].push(m);
    return m;
  }
  function partMaterial(src) {
    const c = src.color ? src.color.clone() : new THREE.Color(0.2, 0.2, 0.2), a = src.opacity ?? 1;
    const max = Math.max(c.r, c.g, c.b), min = Math.min(c.r, c.g, c.b);
    const metal = src.metalness > 0.5 || (max > 0.35 && max - min < 0.12 && !src.map); // pins, shields: light and unsaturated
    // part colours come from the STEP models and are already linear
    return new THREE.MeshStandardMaterial({ color: c, map: src.map || null, metalness: metal ? 0.6 : 0.05, roughness: metal ? 0.36 : 0.48, transparent: a < 1, opacity: a, emissive: 0x000000 });
  }
  // which swatches the board's own colours are closest to (only to mark them; nothing is recoloured)
  function nearest(table, col) {
    let best = null, bd = 1e9;
    for (const [k, hex] of Object.entries(table)) { const c = new THREE.Color(hex); const d = (c.r - col.r) ** 2 + (c.g - col.g) ** 2 + (c.b - col.b) ** 2; if (d < bd) { bd = d; best = k; } }
    return best;
  }
  function guessLook(meta) {
    const b = meta.board || {}, f = String(b.finish || "").toUpperCase();
    const pad = boardMats.pad[0]?.color;
    return {
      mask: MASKS[b.mask] ? b.mask : boardMats.mask[0] ? nearest(MASKS, boardMats.mask[0].color) : "green",
      silk: SILKS[b.silk] ? b.silk : boardMats.silk[0] ? nearest(SILKS, boardMats.silk[0].color) : "white",
      finish: FINISHES[f] ? f : startFinish,
    };
  }
  function setMask(name) { look.mask = name; for (const m of boardMats.mask) { m.color.setHex(MASKS[name]); m.opacity = name === "white" ? 0.95 : name === "black" ? 0.94 : 0.9; } dirty = true; refreshMini(); }
  function setSilk(name) { look.silk = name; for (const m of boardMats.silk) m.color.setHex(SILKS[name]); dirty = true; refreshMini(); }
  function setFinish(name) { look.finish = name; const f = FINISHES[name]; for (const m of boardMats.pad) { m.color.setHex(f.color); m.metalness = f.metalness; m.roughness = f.roughness; } dirty = true; refreshMini(); }

  // ---------- geometry: one mesh per material per part (KiCad GLBs have ~10k primitives) ----------
  function floatGeo(g) {
    const out = new THREE.BufferGeometry();
    for (const [k, a] of Object.entries(g.attributes)) {
      if (a.array instanceof Float32Array && !a.isInterleavedBufferAttribute && !a.normalized) { out.setAttribute(k, a.clone()); continue; }
      const n = a.count, s = a.itemSize, arr = new Float32Array(n * s);
      for (let i = 0; i < n; i++) {
        arr[i * s] = a.getX(i); if (s > 1) arr[i * s + 1] = a.getY(i); if (s > 2) arr[i * s + 2] = a.getZ(i); if (s > 3) arr[i * s + 3] = a.getW(i);
      }
      out.setAttribute(k, new THREE.BufferAttribute(arr, s));
    }
    if (g.index) out.setIndex(new THREE.BufferAttribute(Uint32Array.from(g.index.array), 1));
    return out;
  }
  function mergeInto(target, meshes, matFor, ref) {
    target.updateMatrixWorld(true);
    const inv = new THREE.Matrix4().copy(target.matrixWorld).invert(), groups = new Map(), made = [];
    for (const o of meshes) {
      const src = Array.isArray(o.material) ? o.material[0] : o.material;
      const m = matFor(src);
      if (!m) continue;
      if (o === target) { // the node is itself a mesh: keep it where it is, only swap the material
        o.material = m; if (ref) o.userData.ref = ref; if (m.userData.kind === "mask") o.renderOrder = 2; made.push(o); continue;
      }
      const g = floatGeo(o.geometry).applyMatrix4(new THREE.Matrix4().multiplyMatrices(inv, o.matrixWorld));
      if (!g.attributes.normal) g.computeVertexNormals();
      if (!groups.has(m)) groups.set(m, []);
      groups.get(m).push(g);
      o.parent.remove(o);
    }
    for (const [m, gs] of groups) {
      let merged = null;
      if (gs.length > 1) {
        const names = Object.keys(gs[0].attributes).filter((k) => gs.every((g) => g.attributes[k] && g.attributes[k].itemSize === gs[0].attributes[k].itemSize));
        const mixed = gs.some((g) => g.index) && gs.some((g) => !g.index);
        const ready = gs.map((g) => { const x = mixed && g.index ? g.toNonIndexed() : g; for (const k of Object.keys(x.attributes)) if (!names.includes(k)) x.deleteAttribute(k); x.morphAttributes = {}; return x; });
        try { merged = mergeGeometries(ready, false); } catch (e) { merged = null; }
      }
      for (const g of merged ? [merged] : gs) {
        g.computeBoundingSphere(); g.computeBoundingBox();
        const mesh = new THREE.Mesh(g, m);
        if (ref) mesh.userData.ref = ref;
        if (m.userData.kind === "mask") mesh.renderOrder = 2;
        target.add(mesh);
        made.push(mesh);
      }
    }
    return made;
  }

  // ---------- load ----------
  const glbRoot = new THREE.Group(); // the GLB's own frame (metres, Y-up), placed on the floor
  scene.add(glbRoot);
  let pickables = [], boardMeshes = [], boardTop = 0;
  async function load(buf, { parts: partInfo = {}, meta = {}, layers = null } = {}) {
    const gltf = await gltfLoader().parseAsync(buf, "");
    const root = gltf.scene;
    glbRoot.clear(); parts = {}; pickables = []; boardMeshes = []; hovered = selected = null;
    for (const k of Object.keys(boardMats)) boardMats[k] = [];
    info = { parts: partInfo, meta };
    const asset = gltf.parser?.json?.asset || {};
    kicad = /kicad/i.test(`${asset.generator || ""} ${asset.extras?.generator || ""}`);
    startFinish = FINISHES[String(meta.board?.finish || "").toUpperCase()] ? String(meta.board.finish).toUpperCase() : "ENIG";
    glbRoot.add(root);
    root.updateMatrixWorld(true);

    // parts: GLB node names from parts.json (`node`, else the refdes); refdes-looking names when there is none
    const want = new Map();
    for (const [ref, p] of Object.entries(partInfo)) { const n = p.node || ref; want.set(n, ref); want.set(sanitize(n), ref); }
    const nodes = [];
    (function walk(o, depth) {
      const nm = o.name || "", base = nm.replace(/_\d+$/, "");
      const ref = want.get(nm) ?? want.get(base) ?? (!want.size && depth > 0 && REFLIKE.test(nm) && !o.isMesh ? nm : null);
      if (ref && !parts[ref]) { nodes.push([o, ref]); return; }
      for (const c of o.children) walk(c, depth + 1);
    })(root, 0);
    for (const [n, ref] of nodes) {
      const meshes = []; n.traverse((o) => { if (o.isMesh) meshes.push(o); });
      const mats = new Map();
      const made = mergeInto(n, meshes, (src) => { if (!mats.has(src)) mats.set(src, partMaterial(src)); return mats.get(src); }, ref);
      parts[ref] = { node: n, meshes: made, mats: [...mats.values()] };
      pickables.push(...made);
    }
    // everything else is the board: mask, silk, pads, copper, FR4
    const rest = [];
    root.traverse((o) => { if (o.isMesh && !o.userData.ref) rest.push(o); });
    const bm = new Map();
    boardMeshes = mergeInto(root, rest, (src) => { const k = classify(src); const key = k === "body" ? src : k; if (!bm.has(key)) bm.set(key, boardMaterial(k, src)); return bm.get(key); }, null);
    pickables.push(...boardMeshes);
    if (layers?.pours) addPours(root, layers.pours);

    // place: board centred on the origin, sitting on the floor
    const box = new THREE.Box3().setFromObject(root), ctr = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
    glbRoot.position.set(-ctr.x, -box.min.y, -ctr.z);
    glbRoot.updateMatrixWorld(true);
    // board footprint (without tall parts) for the shadow
    const bb = new THREE.Box3(); for (const m of boardMeshes) bb.expandByObject(m);
    boardTop = bb.isEmpty() ? 0 : glbRoot.worldToLocal(new THREE.Vector3(0, bb.max.y, 0)).y;
    const fp = bb.isEmpty() ? size : bb.getSize(new THREE.Vector3());
    shadow.scale.set(fp.x * 1.6, fp.z * 1.6, 1);
    const span = Math.max(size.x, size.z);
    extent = Math.max(0.08, Math.ceil((span * 0.75) / 0.02) * 0.02);
    model = { size, span };
    buildFloor();
    frameHome();
    look = guessLook(meta);
    dirty = true;
    refreshMini();
    return { parts: Object.keys(parts), look: { ...look } };
  }

  // copper pours from /etch/layers (live-* boards): flat copper just under the tracks' top face
  function addPours(root, pours) {
    const b = new THREE.Box3();
    for (const m of boardMeshes) if (m.material.userData.kind === "copper") b.expandByObject(m);
    if (b.isEmpty()) return;
    const inv = new THREE.Matrix4().copy(root.matrixWorld).invert(); b.applyMatrix4(inv);
    const mat = new THREE.MeshStandardMaterial({ color: 0x3a2a14, metalness: 0.35, roughness: 0.6, side: THREE.DoubleSide });
    for (const [side, y] of [["F", b.max.y - 10e-6], ["B", b.min.y + 5e-6]]) {
      const pos = [], idx = [];
      for (const p of pours[side] || []) {
        const pts = [];
        for (let i = 0; i + 1 < p.length; i += 2) pts.push(new THREE.Vector2(p[i] / 1e6, p[i + 1] / 1e6));
        if (pts.length > 3 && pts[0].equals(pts[pts.length - 1])) pts.pop();
        if (pts.length < 3) continue;
        const tri = THREE.ShapeUtils.triangulateShape(pts, []), base = pos.length / 3;
        for (const v of pts) pos.push(v.x, y, v.y);
        for (const t of tri) idx.push(base + t[0], base + t[1], base + t[2]);
      }
      if (!idx.length) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3)); g.setIndex(idx); g.computeVertexNormals();
      root.add(new THREE.Mesh(g, mat));
    }
  }

  // ---------- camera ----------
  // the same 3/4 view as the KiCad thumb (kicad-cli render --rotate -40,0,-25), so the poster crossfades in place;
  // the distance fits the board's box to about 65% of the canvas height (and 62% of its width), centred
  function homeView() {
    const az = THREE.MathUtils.degToRad(25), el = THREE.MathUtils.degToRad(50);
    const dir = new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el));
    const box = new THREE.Box3();
    if (model) box.setFromObject(glbRoot); else box.set(new THREE.Vector3(-0.03, 0, -0.03), new THREE.Vector3(0.03, 0.002, 0.03));
    const tgt = box.getCenter(new THREE.Vector3());
    const pts = [];
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) pts.push(new THREE.Vector3(x, y, z));
    const c = cam.clone(); c.aspect = Math.max(0.2, W / H); c.near = 1e-4; c.far = 100; c.updateProjectionMatrix();
    const fx = W < 560 ? 0.8 : 0.62, fy = W < 560 ? 0.5 : 0.65;
    let d = box.getSize(new THREE.Vector3()).length() * 2 || 0.2;
    for (let i = 0; i < 8; i++) {
      c.position.copy(tgt).addScaledVector(dir, d); c.lookAt(tgt); c.updateMatrixWorld();
      let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
      for (const p of pts) { const q = p.clone().project(c); x0 = Math.min(x0, q.x); x1 = Math.max(x1, q.x); y0 = Math.min(y0, q.y); y1 = Math.max(y1, q.y); }
      // symmetric about the centre so the board sits in the middle of the canvas
      const k = Math.max(Math.max(-x0, x1) / fx, Math.max(-y0, y1) / fy);
      d *= 0.35 + 0.65 * k; // damped: perspective makes the extent non-linear in distance
    }
    return { pos: tgt.clone().addScaledVector(dir, d), tgt };
  }
  function frameHome() {
    home = homeView();
    cam.position.copy(home.pos); ctl.target.copy(home.tgt);
    const d = home.pos.distanceTo(home.tgt);
    ctl.minDistance = d * 0.03; ctl.maxDistance = d * 4;
    clip(); ctl.update();
  }
  function clip() {
    const dist = cam.position.distanceTo(ctl.target);
    const near = Math.max(1e-5, dist * 0.01), far = Math.max(dist * 20, extent * 12);
    if (Math.abs(near - cam.near) > near * 0.05 || Math.abs(far - cam.far) > far * 0.05) { cam.near = near; cam.far = far; cam.updateProjectionMatrix(); }
  }
  function tween(pos, tgt, ms = 520) { anim = { t0: performance.now(), ms, p0: cam.position.clone(), t0v: ctl.target.clone(), pos, tgt }; dirty = true; }
  function reset() { if (!model) return; home = homeView(); tween(home.pos, home.tgt); }
  // named views for the view menu and tests: "home", "top", "front", "side", "under"
  function view(name) {
    if (!model) return;
    if (name === "home") return reset();
    const d = cam.position.distanceTo(ctl.target), t = ctl.target.clone();
    const dir = { top: [0, 1, 0.0001], front: [0, 0.18, 1], side: [1, 0.18, 0], under: [0.0001, -1, 0.35] }[name];
    if (!dir) return;
    tween(t.clone().addScaledVector(new THREE.Vector3(...dir).normalize(), d), t);
  }
  function focus(ref) {
    const p = parts[ref]; if (!p) return;
    const b = new THREE.Box3().setFromObject(p.node), c = b.getCenter(new THREE.Vector3());
    const dir = cam.position.clone().sub(ctl.target).normalize();
    const d = Math.max(b.getSize(new THREE.Vector3()).length() * 4, model.span * 0.55);
    tween(c.clone().add(dir.multiplyScalar(d)), c);
  }

  // ---------- picking + hover ----------
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  function hit(x, y) {
    const r = cv.getBoundingClientRect();
    ndc.set(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, cam);
    return ray.intersectObjects(pickables, false)[0] || null;
  }
  function paint() {
    for (const [r, p] of Object.entries(parts)) {
      const c = r === selected ? SELECT : r === hovered ? HOVER : null;
      for (const m of p.mats) { if (c) m.emissive.copy(c); else m.emissive.setHex(0); }
    }
    dirty = true;
  }
  function setHover(ref) { if (ref === hovered) return; hovered = ref; cv.style.cursor = ref ? "pointer" : ""; paint(); if (ev.hover) ev.hover(ref); }
  function select(ref) { if (ref === selected) return; selected = ref; paint(); }
  let pend = null, dragging = false;
  const down = { x: 0, y: 0, t: 0, id: -1 };
  cv.addEventListener("pointerdown", (e) => { down.x = e.clientX; down.y = e.clientY; down.t = performance.now(); down.id = e.pointerId; dragging = true; });
  addEventListener("pointerup", () => { dragging = false; });
  cv.addEventListener("pointermove", (e) => {
    if (e.pointerType !== "mouse" || (dragging && e.buttons)) return;
    pend = [e.clientX, e.clientY];
  });
  cv.addEventListener("pointerleave", () => { pend = null; setHover(null); });
  cv.addEventListener("pointerup", (e) => {
    if (!model || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6 || performance.now() - down.t > 600) return;
    const h = hit(e.clientX, e.clientY);
    const local = h ? glbRoot.worldToLocal(h.point.clone()) : null;
    const ref = h?.object.userData.ref || (local ? padPart(local) : null);
    const r = cv.getBoundingClientRect();
    if (ev.pick) ev.pick({ ref, part: ref ? info.parts[ref] || { ref } : null, anchor: local ? [local.x, local.y, local.z] : null, screen: [e.clientX - r.left, e.clientY - r.top] });
  });

  // ---------- projection for overlays (pins, cards, labels) ----------
  const tmp = new THREE.Vector3();
  function toScreen(v) {
    v.project(cam);
    return { x: ((v.x + 1) / 2) * W, y: ((1 - v.y) / 2) * H, behind: v.z > 1 };
  }
  function project(anchor) { tmp.set(anchor[0], anchor[1], anchor[2]); glbRoot.localToWorld(tmp); return toScreen(tmp); }
  // a part with no 3D body in the GLB (its model didn't resolve) is still clickable on its pads: the nearest
  // body-less part from parts.json `pos_mm` (GLB x = board x, z = board y, metres) within 4 mm of the click
  function padPart(local) {
    let best = null, bd = 4;
    for (const [ref, p] of Object.entries(info.parts || {})) {
      if (parts[ref] || !p.pos_mm) continue;
      const d = Math.hypot(local.x * 1000 - p.pos_mm[0], local.z * 1000 - p.pos_mm[1]);
      if (d < bd) { bd = d; best = ref; }
    }
    return best;
  }
  function partAnchor(ref) {
    const p = parts[ref];
    if (!p) { const q = info.parts?.[ref]; return q?.pos_mm ? [q.pos_mm[0] / 1000, boardTop, q.pos_mm[1] / 1000] : null; }
    const b = new THREE.Box3().setFromObject(p.node), c = b.getCenter(new THREE.Vector3()); c.y = b.max.y;
    const l = glbRoot.worldToLocal(c); return [l.x, l.y, l.z];
  }
  function placeLabels() {
    if (!floorGroup.visible) return;
    for (const r of rulers) {
      const s = toScreen(tmp.copy(r.p));
      const off = s.behind || s.x < -40 || s.x > W + 40 || s.y < -20 || s.y > H + 20;
      r.el.style.transform = `translate(${s.x.toFixed(1)}px, ${s.y.toFixed(1)}px) translate(-50%, 4px)`;
      r.el.style.opacity = off ? "0" : "";
    }
  }

  // ---------- minimap: a top-down still of the board with the camera drawn on it ----------
  let mini = null;
  function attachMinimap(canvas) { mini = { cv: canvas, g: canvas.getContext("2d"), img: null }; refreshMini(); }
  let miniT = 0;
  function refreshMini() {
    if (!mini || !model) return;
    clearTimeout(miniT);
    miniT = setTimeout(() => {
      const w = mini.cv.width, hh = mini.cv.height, a = w / hh, half = Math.max(model.size.z * 0.7, model.size.x * 0.7 / a);
      const oc = new THREE.OrthographicCamera(-half * a, half * a, half, -half, 0.001, 10);
      oc.position.set(0, 2, 0); oc.up.set(0, 0, -1); oc.lookAt(0, 0, 0);
      const rt = new THREE.WebGLRenderTarget(w, hh, { samples: 4 }); rt.texture.colorSpace = THREE.SRGBColorSpace;
      const fv = floorGroup.visible; floorGroup.visible = false; shadow.visible = false;
      renderer.setRenderTarget(rt); renderer.setClearColor(0xf1f1ef, 1); renderer.clear(); renderer.render(scene, oc);
      const px = new Uint8Array(w * hh * 4); renderer.readRenderTargetPixels(rt, 0, 0, w, hh, px);
      renderer.setRenderTarget(null); renderer.setClearColor(0x000000, 0); rt.dispose();
      floorGroup.visible = fv; shadow.visible = true;
      const img = mini.g.createImageData(w, hh);
      for (let y = 0; y < hh; y++) img.data.set(px.subarray((hh - 1 - y) * w * 4, (hh - y) * w * 4), y * w * 4);
      mini.img = img; mini.half = half; mini.a = a;
      dirty = true;
    }, 60);
  }
  function drawMini() {
    if (!mini?.img) return;
    const { g, cv: c, img, half, a } = mini, w = c.width, hh = c.height;
    g.putImageData(img, 0, 0);
    const sx = (x) => ((x / (half * a)) + 1) / 2 * w, sz = (z) => ((z / half) + 1) / 2 * hh;
    const tx = sx(ctl.target.x), tz = sz(ctl.target.z);
    const dir = new THREE.Vector3().subVectors(ctl.target, cam.position); const len = Math.hypot(dir.x, dir.z) || 1;
    const ang = Math.atan2(dir.z / len, dir.x / len), fovh = Math.atan(Math.tan(THREE.MathUtils.degToRad(cam.fov / 2)) * cam.aspect);
    const R = Math.min(w, hh) * 0.36;
    g.save(); g.translate(tx, tz);
    g.fillStyle = "rgba(47,107,255,0.14)"; g.strokeStyle = "rgba(47,107,255,0.65)"; g.lineWidth = 1.2;
    g.beginPath(); g.moveTo(-Math.cos(ang) * R * 0.55, -Math.sin(ang) * R * 0.55);
    g.arc(-Math.cos(ang) * R * 0.55, -Math.sin(ang) * R * 0.55, R, ang - fovh, ang + fovh); g.closePath(); g.fill(); g.stroke();
    g.fillStyle = "#2f6bff"; g.beginPath(); g.arc(0, 0, 2.6, 0, Math.PI * 2); g.fill();
    g.restore();
  }

  // ---------- loop: render only when something changed ----------
  let firstFrame = false;
  function tick() {
    if (!alive) return;
    requestAnimationFrame(tick);
    if (anim) {
      const u = Math.min(1, (performance.now() - anim.t0) / anim.ms), e = 1 - Math.pow(1 - u, 3);
      cam.position.lerpVectors(anim.p0, anim.pos, e); ctl.target.lerpVectors(anim.t0v, anim.tgt, e);
      if (u >= 1) anim = null;
      dirty = true;
    }
    if (ctl.update()) dirty = true;
    if (pend && model) { const [x, y] = pend; pend = null; const h = hit(x, y); setHover(h?.object.userData.ref || null); }
    if (!dirty || !model) return;
    dirty = false;
    clip();
    const above = cam.position.y > 1e-4; // the floor is at y = 0
    floorGroup.visible = floorOn && !photo && above; shadow.visible = above;
    labels.style.display = floorGroup.visible ? "" : "none";
    renderer.render(scene, cam);
    placeLabels();
    drawMini();
    if (ev.frame) ev.frame();
    if (!firstFrame) { firstFrame = true; if (ev.ready) ev.ready(); }
  }
  requestAnimationFrame(tick);

  return {
    load, reset, view, focus, select, setHover, project, partAnchor,
    refAt: (x, y) => hit(x, y)?.object.userData.ref || null, // client coords -> refdes under them

    setMask, setSilk, setFinish, setFloor, setPhoto, attachMinimap,
    get look() { return { ...look }; },
    get parts() { return parts; },
    get size() { return { w: W, h: H }; },
    canvas: cv,
    invalidate() { dirty = true; },
    on(name, f) { ev[name] = f; },
    dispose() { alive = false; renderer.dispose(); cv.remove(); labels.remove(); },
  };
}

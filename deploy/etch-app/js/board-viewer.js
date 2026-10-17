// Etch board viewer for the board editor (2026-10-17). Fills its container: a light-blue mm workplane (1 mm fine
// grid, 10 mm major lines, mm ruler labels from the board's top-left corner) with the board sitting on it, a soft
// contact shadow, orbit / two-finger pan / pinch zoom, perspective or orthographic camera, spring-animated view
// changes (any direction, so a view cube can snap to faces, edges and corners), parts picked by their GLB node name
// (parts.json `node` is the source of truth, so refdes like USB-UART1 or SW_BOOT1 work), hover highlight,
// multi-select with crisp cyan edge outlines, hide / isolate, live solder mask / silkscreen / pad finish recolour,
// board-millimetre picking for drops, and the clicked 3D point returned for comment anchors. Renders on demand.
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
const ACCENT = 0x1aa3ff;
const HOVER = new THREE.Color(ACCENT).multiplyScalar(0.2), SELECT = new THREE.Color(ACCENT).multiplyScalar(0.3), MARK = new THREE.Color(0x19c2ff).multiplyScalar(0.34);
// fallback when there's no parts.json: refdes-looking node names (U6, R12, USB-UART1, SW_BOOT1, LED3A)
const REFLIKE = /^[A-Z][A-Za-z0-9]*([_-][A-Za-z0-9]+)*\d+[A-Za-z]?$/;
const sanitize = (s) => String(s || "").replace(/\s/g, "_").replace(/[\[\]\.:\/]/g, "");
const REDUCED = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

let loader = null;
function gltfLoader() {
  if (!loader) { loader = new GLTFLoader(); loader.setMeshoptDecoder(MeshoptDecoder); }
  return loader;
}

export function createBoardViewer(host) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance", preserveDrawingBuffer: false });
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
  const persp = new THREE.PerspectiveCamera(32, 1, 0.001, 50);
  persp.position.set(-0.08, 0.1, 0.16);
  const ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, -10, 50);
  let cam = persp;

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
  ctl.minZoom = 0.05; ctl.maxZoom = 60;
  // all the way round, underside included: the workplane steps aside while the camera is below it
  ctl.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };

  let dirty = true, alive = true, model = null, anim = null, home = null;
  let parts = {}, info = {}, hovered = null, extent = 0.08;
  let selected = new Set(), marked = new Set(), hidden = new Set(), isolated = null;
  const ev = { pick: [], hover: [], frame: [], start: [], ready: [], camera: [] };
  const emit = (name, ...a) => { for (const f of ev[name]) f(...a); };
  ctl.addEventListener("change", () => (dirty = true));
  ctl.addEventListener("start", () => { anim = null; emit("start"); });

  // ---------- workplane: light blue, 1 mm fine grid, 10 mm major lines, origin at the board's top-left corner ----------
  const planeMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: {
      origin: { value: new THREE.Vector2(0, 0) }, halfSize: { value: new THREE.Vector2(0.1, 0.1) }, centre: { value: new THREE.Vector2(0, 0) },
      base: { value: new THREE.Color(0xd6ecf8) }, fine: { value: new THREE.Color(0xbcdcef) }, major: { value: new THREE.Color(0x8dc2e3) }, edge: { value: new THREE.Color(0x5fa8d6) },
    },
    vertexShader: "varying vec3 vW; void main(){ vec4 w = modelMatrix * vec4(position,1.0); vW = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }",
    fragmentShader: `uniform vec2 origin, halfSize, centre; uniform vec3 base, fine, major, edge; varying vec3 vW;
      float gridLine(vec2 p, float s, float w){ vec2 q = p / s; vec2 g = abs(fract(q - 0.5) - 0.5) / (fwidth(q) * w); return 1.0 - min(min(g.x, g.y), 1.0); }
      void main(){
        vec2 mm = (vW.xz - origin) * 1000.0;
        // fine lines fade out once they get denser than a few pixels apart
        float density = max(fwidth(mm.x), fwidth(mm.y));
        float fineA = gridLine(mm, 1.0, 1.0) * (1.0 - smoothstep(0.18, 0.42, density));
        float majorA = gridLine(mm, 10.0, 1.3) * (1.0 - smoothstep(2.0, 4.5, density) * 0.6);
        vec3 c = mix(base, fine, fineA * 0.9);
        c = mix(c, major, majorA);
        vec2 d = abs(vW.xz - centre) - halfSize;
        float out1 = max(d.x, d.y);
        float px = max(fwidth(vW.x), fwidth(vW.z));
        float border = 1.0 - smoothstep(0.0, px * 1.6, abs(out1 + px));
        c = mix(c, edge, border * 0.9);
        float a = 1.0 - smoothstep(-px * 0.5, px * 0.5, out1);
        gl_FragColor = vec4(c, a);
        #include <colorspace_fragment>
      }`,
  });
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), planeMat);
  plane.rotation.x = -Math.PI / 2; plane.renderOrder = -2;
  const shadowTex = (() => {
    const c = document.createElement("canvas"); c.width = c.height = 256;
    const g = c.getContext("2d"); g.filter = "blur(22px)"; g.fillStyle = "rgba(10,40,70,0.9)";
    g.beginPath(); g.roundRect(48, 48, 160, 160, 18); g.fill();
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
  })();
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: shadowTex, transparent: true, opacity: 0.3, depthWrite: false, toneMapped: false }));
  shadow.rotation.x = -Math.PI / 2; shadow.position.y = 0.00005; shadow.renderOrder = -1;
  const floorGroup = new THREE.Group(); floorGroup.add(plane);
  scene.add(floorGroup, shadow);
  let rulers = [], wp = { x0: -0.05, z0: -0.05, x1: 0.05, z1: 0.05, ox: 0, oz: 0 };
  function buildFloor() {
    // the board's own corner (KiCad's top-left) is 0 mm; the plane reaches 20 mm past the board, in whole 10 mm steps
    const r = boardWorldRect() || emptyRect;
    const ox = r ? r.x0 : -0.03, oz = r ? r.z0 : -0.03, w = r ? r.x1 - r.x0 : 0.06, d = r ? r.z1 - r.z0 : 0.06;
    const pad = 0.02, step = 0.01;
    const x0 = ox - pad, z0 = oz - pad, x1 = ox + Math.ceil((w + pad) / step) * step, z1 = oz + Math.ceil((d + pad) / step) * step;
    wp = { x0, z0, x1, z1, ox, oz };
    plane.scale.set(x1 - x0, z1 - z0, 1);
    plane.position.set((x0 + x1) / 2, 0, (z0 + z1) / 2);
    planeMat.uniforms.origin.value.set(ox, oz);
    planeMat.uniforms.halfSize.value.set((x1 - x0) / 2, (z1 - z0) / 2);
    planeMat.uniforms.centre.value.set((x0 + x1) / 2, (z0 + z1) / 2);
    labels.textContent = ""; rulers = [];
    const span = Math.max(x1 - x0, z1 - z0), every = span > 0.25 ? 0.02 : 0.01;
    const mk = (txt, p, ax) => { const el = document.createElement("span"); el.className = "bv-ruler " + ax; el.textContent = txt; labels.appendChild(el); rulers.push({ el, p, ax }); };
    for (let x = Math.ceil((x0 - ox) / every) * every; ox + x <= x1 + 1e-6; x += every) mk(String(Math.round(x * 1000)), new THREE.Vector3(ox + x, 0, z1), "x");
    for (let z = Math.ceil((z0 - oz) / every) * every; oz + z <= z1 + 1e-6; z += every) mk(String(Math.round(z * 1000)), new THREE.Vector3(x0, 0, oz + z), "z");
  }
  let floorOn = true;
  function setFloor(on) { floorOn = on; floorGroup.visible = on; labels.style.display = on ? "" : "none"; dirty = true; }

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
    persp.aspect = W / H; persp.updateProjectionMatrix();
    orthoFrustum();
    dirty = true;
  }
  // the orthographic frustum matches what the perspective camera sees at the target's distance
  let orthoHalf = 0.05;
  function orthoFrustum() {
    const a = W / H;
    ortho.left = -orthoHalf * a; ortho.right = orthoHalf * a; ortho.top = orthoHalf; ortho.bottom = -orthoHalf;
    ortho.updateProjectionMatrix();
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
    allBoard.push(m);
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
  function setMask(name) { look.mask = name; for (const m of boardMats.mask) { m.color.setHex(MASKS[name]); m.opacity = name === "white" ? 0.95 : name === "black" ? 0.94 : 0.9; } dirty = true; }
  function setSilk(name) { look.silk = name; for (const m of boardMats.silk) m.color.setHex(SILKS[name]); dirty = true; }
  function setFinish(name) { look.finish = name; const f = FINISHES[name]; for (const m of boardMats.pad) { m.color.setHex(f.color); m.metalness = f.metalness; m.roughness = f.roughness; } dirty = true; }


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
  const glbRoot = new THREE.Group(); // the GLB's own frame (metres, Y-up), placed on the workplane
  scene.add(glbRoot);
  let pickables = [], boardMeshes = [], boardTop = 0, boardLocal = null;
  async function load(buf, { parts: partInfo = {}, meta = {}, layers = null, keepView = false } = {}) {
    const gltf = await gltfLoader().parseAsync(buf, "");
    const root = gltf.scene;
    emptyRect = null;
    glbRoot.clear(); parts = {}; pickables = []; boardMeshes = []; hovered = null;
    const keepSel = keepView ? new Set(selected) : new Set();
    selected = new Set(); marked = new Set(); outlines.clear();
    for (const k of Object.keys(boardMats)) boardMats[k] = [];
    allBoard.length = 0;
    info = { parts: partInfo, meta };
    const asset = gltf.parser?.json?.asset || {};
    kicad = /kicad/i.test(`${asset.generator || ""} ${asset.extras?.generator || ""}`);
    startFinish = FINISHES[String(meta.board?.finish || "").toUpperCase()] ? String(meta.board.finish).toUpperCase() : "ENIG";
    glbRoot.position.set(0, 0, 0);
    glbRoot.add(root);
    glbRoot.updateMatrixWorld(true);

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

    // place: board centred on the origin, sitting on the workplane
    const box = new THREE.Box3().setFromObject(root), ctr = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
    glbRoot.position.set(-ctr.x, -box.min.y, -ctr.z);
    glbRoot.updateMatrixWorld(true);
    // board outline (without tall parts) for the shadow, the workplane origin and drops
    const bb = new THREE.Box3(); for (const m of boardMeshes) bb.expandByObject(m);
    boardTop = bb.isEmpty() ? 0 : glbRoot.worldToLocal(new THREE.Vector3(0, bb.max.y, 0)).y;
    boardLocal = bb.isEmpty() ? null : { min: glbRoot.worldToLocal(bb.min.clone()), max: glbRoot.worldToLocal(bb.max.clone()) };
    const fp = bb.isEmpty() ? size : bb.getSize(new THREE.Vector3());
    shadow.scale.set(fp.x * 1.6, fp.z * 1.6, 1);
    const span = Math.max(size.x, size.z);
    extent = Math.max(0.08, Math.ceil((span * 0.75) / 0.02) * 0.02);
    model = { size, span };
    buildFloor();
    if (!keepView) frameHome();
    look = guessLook(meta);
    for (const r of hidden) if (parts[r]) parts[r].node.visible = false;
    if (isolated) applyIsolate();
    if (keepSel.size) select([...keepSel].filter((r) => parts[r] || partInfo[r]));
    dirty = true;
    return { parts: Object.keys(parts), look: { ...look } };
  }

  // no board yet (a new board before Etch has built it): just a w x d mm workplane, the camera set back by `back` so
  // reset() can ease it in once there is something to show
  let emptyRect = null;
  function empty({ w = 90, d = 64, back = 1.7 } = {}) {
    glbRoot.clear(); parts = {}; pickables = []; boardMeshes = []; hovered = null;
    selected = new Set(); marked = new Set(); outlines.clear();
    for (const k of Object.keys(boardMats)) boardMats[k] = [];
    info = { parts: {}, meta: {} };
    boardLocal = null; boardTop = 0;
    emptyRect = { x0: -w / 2000, z0: -d / 2000, x1: w / 2000, z1: d / 2000 };
    shadow.scale.set(1e-6, 1e-6, 1);
    model = { size: new THREE.Vector3(w / 1000, 0.002, d / 1000), span: Math.max(w, d) / 1000, empty: true };
    extent = Math.max(0.08, Math.ceil((model.span * 0.75) / 0.02) * 0.02);
    buildFloor();
    frameHome();
    const off = persp.position.clone().sub(ctl.target).multiplyScalar(back);
    persp.position.copy(ctl.target).add(off); ortho.position.copy(persp.position);
    ctl.maxDistance = Math.max(ctl.maxDistance, off.length() * 1.5);
    clip(); ctl.update();
    dirty = true;
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

  // ---------- board millimetres (KiCad board coordinates: GLB x = board x, GLB z = board y) ----------
  function boardWorldRect() {
    if (!boardLocal) return null;
    const a = glbRoot.localToWorld(boardLocal.min.clone()), b = glbRoot.localToWorld(boardLocal.max.clone());
    return { x0: Math.min(a.x, b.x), z0: Math.min(a.z, b.z), x1: Math.max(a.x, b.x), z1: Math.max(a.z, b.z) };
  }
  // the board's outline in KiCad mm
  function boardRect() {
    if (!boardLocal) return null;
    return { x0: boardLocal.min.x * 1000, y0: boardLocal.min.z * 1000, x1: boardLocal.max.x * 1000, y1: boardLocal.max.z * 1000 };
  }
  const plTmp = new THREE.Plane(), hitV = new THREE.Vector3();
  // client point -> where it lands on the board's top face, in KiCad mm, and whether that is on the board
  function pickBoard(x, y) {
    if (!model) return null;
    setRay(x, y);
    const top = glbRoot.localToWorld(new THREE.Vector3(0, boardTop, 0)).y;
    plTmp.set(new THREE.Vector3(0, 1, 0), -top);
    if (!ray.ray.intersectPlane(plTmp, hitV)) return null;
    const l = glbRoot.worldToLocal(hitV.clone()), r = boardRect();
    const mm = [l.x * 1000, l.z * 1000];
    return { mm, inside: !!r && mm[0] >= r.x0 && mm[0] <= r.x1 && mm[1] >= r.y0 && mm[1] <= r.y1, rect: r };
  }
  // KiCad mm (on the board's top face, or `up` mm above it) -> canvas pixels
  function mmToScreen(xmm, ymm, up = 0) { tmp.set(xmm / 1000, boardTop + up / 1000, ymm / 1000); glbRoot.localToWorld(tmp); return toScreen(tmp); }

  // ---------- camera ----------
  // the same 3/4 view as the KiCad thumb (kicad-cli render --rotate -40,0,-25), so the poster crossfades in place;
  // the distance fits the board's box to about 65% of the canvas height (and 62% of its width), centred
  const HOME_DIR = (() => { const az = THREE.MathUtils.degToRad(25), el = THREE.MathUtils.degToRad(50); return new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el)); })();
  function visBox(refs) {
    const box = new THREE.Box3();
    if (refs && refs.length) { for (const r of refs) { const b = partBox(r); if (b) box.union(b); } }
    else if (model?.empty && emptyRect) { box.set(new THREE.Vector3(emptyRect.x0, 0, emptyRect.z0), new THREE.Vector3(emptyRect.x1, 0.002, emptyRect.z1)); }
    else if (model) { box.setFromObject(glbRoot); }
    if (box.isEmpty()) box.set(new THREE.Vector3(-0.03, 0, -0.03), new THREE.Vector3(0.03, 0.002, 0.03));
    return box;
  }
  function fitDistance(dir, box, fx = W < 560 ? 0.8 : 0.62, fy = W < 560 ? 0.5 : 0.65) {
    const tgt = box.getCenter(new THREE.Vector3());
    const pts = [];
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) pts.push(new THREE.Vector3(x, y, z));
    const c = persp.clone(); c.aspect = Math.max(0.2, W / H); c.near = 1e-4; c.far = 100; c.updateProjectionMatrix();
    let d = box.getSize(new THREE.Vector3()).length() * 2 || 0.2;
    for (let i = 0; i < 8; i++) {
      c.position.copy(tgt).addScaledVector(dir, d); c.lookAt(tgt); c.updateMatrixWorld();
      let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
      for (const p of pts) { const q = p.clone().project(c); x0 = Math.min(x0, q.x); x1 = Math.max(x1, q.x); y0 = Math.min(y0, q.y); y1 = Math.max(y1, q.y); }
      const k = Math.max(Math.max(-x0, x1) / fx, Math.max(-y0, y1) / fy);
      d *= 0.35 + 0.65 * k; // damped: perspective makes the extent non-linear in distance
    }
    return { pos: tgt.clone().addScaledVector(dir, d), tgt, d };
  }
  function homeView() { return fitDistance(HOME_DIR.clone(), visBox()); }
  function frameHome() {
    home = homeView();
    persp.position.copy(home.pos); ortho.position.copy(home.pos); ctl.target.copy(home.tgt);
    const d = home.d;
    ctl.minDistance = d * 0.03; ctl.maxDistance = d * 4;
    orthoHalf = d * Math.tan(THREE.MathUtils.degToRad(persp.fov / 2)); ortho.zoom = 1; orthoFrustum();
    clip(); ctl.update();
  }
  function clip() {
    const dist = cam.position.distanceTo(ctl.target);
    if (cam.isOrthographicCamera) { const far = Math.max(dist * 4, extent * 12); if (Math.abs(cam.far - far) > far * 0.05) { cam.near = -far; cam.far = far; cam.updateProjectionMatrix(); } return; }
    const near = Math.max(1e-5, dist * 0.01), far = Math.max(dist * 20, extent * 12);
    if (Math.abs(near - cam.near) > near * 0.05 || Math.abs(far - cam.far) > far * 0.05) { cam.near = near; cam.far = far; cam.updateProjectionMatrix(); }
  }
  // spring-driven camera moves: direction swings round the target on a great circle, distance / zoom / target ease in
  // with a little overshoot. Reduced motion: a short ease, no overshoot.
  // orbit-style interpolation in (azimuth, polar) so a swing to the top view lands square to the board
  const sph = (d) => ({ phi: Math.acos(THREE.MathUtils.clamp(d.y, -1, 1)), theta: Math.atan2(d.x, d.z) });
  function rotateTowards(a, b, t) {
    const A = sph(a), B = sph(b);
    let dt = B.theta - A.theta;
    while (dt > Math.PI) dt -= 2 * Math.PI;
    while (dt < -Math.PI) dt += 2 * Math.PI;
    const th = A.theta + dt * t, ph = THREE.MathUtils.clamp(A.phi + (B.phi - A.phi) * t, 1e-4, Math.PI - 1e-4);
    return new THREE.Vector3(Math.sin(ph) * Math.sin(th), Math.cos(ph), Math.sin(ph) * Math.cos(th));
  }
  function spring({ dir, dist, tgt, zoom }) {
    const d0 = cam.position.clone().sub(ctl.target), dist0 = d0.length();
    anim = {
      x: 0, v: 0, dir0: d0.normalize(), dir1: (dir || d0).clone().normalize(), dist0, dist1: dist ?? dist0,
      t0: ctl.target.clone(), t1: (tgt || ctl.target).clone(), z0: cam.zoom, z1: zoom ?? cam.zoom, last: performance.now(),
    };
    dirty = true;
  }
  function stepAnim(now) {
    const a = anim, dt = Math.min(0.12, (now - a.last) / 1000); a.last = now;
    if (REDUCED) { a.x = Math.min(1, a.x + dt / 0.22); }
    else { const k = 210, c = 2 * Math.sqrt(k) * 0.72; for (let left = dt; left > 1e-6; left -= 1 / 240) { const h = Math.min(1 / 240, left); a.v += (-k * (a.x - 1) - c * a.v) * h; a.x += a.v * h; } }
    const u = REDUCED ? 1 - Math.pow(1 - a.x, 3) : a.x;
    const dir = rotateTowards(a.dir0, a.dir1, u);
    ctl.target.lerpVectors(a.t0, a.t1, u);
    const dist = a.dist0 + (a.dist1 - a.dist0) * u;
    cam.position.copy(ctl.target).addScaledVector(dir, Math.max(1e-4, dist));
    if (cam.isOrthographicCamera) { cam.zoom = Math.max(0.02, a.z0 + (a.z1 - a.z0) * u); cam.updateProjectionMatrix(); }
    const settled = REDUCED ? a.x >= 1 : Math.abs(a.x - 1) < 0.002 && Math.abs(a.v) < 0.02;
    if (settled) { cam.position.copy(ctl.target).addScaledVector(a.dir1, a.dist1); if (cam.isOrthographicCamera) { cam.zoom = a.z1; cam.updateProjectionMatrix(); } anim = null; }
    dirty = true;
  }
  // a view straight down or straight up is nudged a hair towards the viewer so the orbit keeps its "up"
  function safeDir(d) { const v = new THREE.Vector3(...(Array.isArray(d) ? d : [d.x, d.y, d.z])).normalize(); if (Math.abs(v.y) > 0.9995) v.set(0, Math.sign(v.y), 0.002).normalize(); return v; }
  function reset() {
    if (!model) return;
    home = homeView();
    spring({ dir: home.pos.clone().sub(home.tgt), dist: home.d, tgt: home.tgt, zoom: 1 });
  }
  // look along any direction (from the target towards the camera), keeping the distance: view cube faces, edges, corners
  function lookFrom(d, { fitBox = false } = {}) {
    if (!model) return;
    const dir = safeDir(d);
    if (fitBox) { const f = fitDistance(dir, visBox()); spring({ dir, dist: f.d, tgt: f.tgt, zoom: 1 }); return; }
    spring({ dir });
  }
  // named views for buttons and tests
  function view(name) {
    if (name === "home") return reset();
    const dir = { top: [0, 1, 0], front: [0, 0.18, 1], side: [1, 0.18, 0], right: [1, 0, 0], left: [-1, 0, 0], back: [0, 0, -1], bottom: [0, -1, 0], under: [0.0001, -1, 0.35] }[name];
    if (dir) lookFrom(dir, { fitBox: name === "top" || name === "bottom" });
  }
  // fit everything (or some parts) in view from the current direction
  function fitView(refs) {
    if (!model) return;
    const dir = cam.position.clone().sub(ctl.target).normalize();
    const f = fitDistance(dir, visBox(refs), refs?.length ? 0.55 : undefined, refs?.length ? 0.55 : undefined);
    if (refs?.length) f.d = Math.max(f.d, model.span * 0.6);
    spring({ dir, dist: f.d, tgt: f.tgt, zoom: 1 });
  }
  function zoomBy(f) {
    if (!model) return;
    if (cam.isOrthographicCamera) spring({ zoom: Math.min(ctl.maxZoom, Math.max(ctl.minZoom, cam.zoom * f)) });
    else { const d = cam.position.distanceTo(ctl.target); spring({ dist: Math.min(ctl.maxDistance, Math.max(ctl.minDistance, d / f)) }); }
  }
  function focus(ref) {
    const b = partBox(ref); if (!b) return;
    const c = b.getCenter(new THREE.Vector3());
    const dir = cam.position.clone().sub(ctl.target).normalize();
    const d = Math.max(b.getSize(new THREE.Vector3()).length() * 4, model.span * 0.55);
    spring({ dir, dist: d, tgt: c, zoom: cam.isOrthographicCamera ? Math.max(1, cam.zoom) : undefined });
  }
  // perspective <-> orthographic, keeping what's on screen the same size at the target
  function setOrtho(on) {
    if (on === !!cam.isOrthographicCamera) return;
    anim = null;
    const tanH = Math.tan(THREE.MathUtils.degToRad(persp.fov / 2));
    const dir = cam.position.clone().sub(ctl.target).normalize();
    if (on) {
      const d = persp.position.distanceTo(ctl.target);
      orthoHalf = d * tanH; ortho.zoom = 1; orthoFrustum();
      ortho.position.copy(persp.position); ortho.quaternion.copy(persp.quaternion); ortho.up.copy(persp.up);
      cam = ortho;
    } else {
      const d = orthoHalf / (tanH * ortho.zoom);
      persp.position.copy(ctl.target).addScaledVector(dir, d); persp.quaternion.copy(ortho.quaternion);
      cam = persp;
    }
    ctl.object = cam;
    clip(); ctl.update(); dirty = true;
    emit("camera", cam);
  }

  // ---------- picking + hover ----------
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  function setRay(x, y) {
    const r = cv.getBoundingClientRect();
    ndc.set(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, cam);
  }
  function hit(x, y) {
    setRay(x, y);
    const vis = pickables.filter((m) => { const r = m.userData.ref; return !r || (!hidden.has(r) && (!isolated || isolated.has(r))); });
    return ray.intersectObjects(vis, false)[0] || null;
  }
  function paint() {
    for (const [r, p] of Object.entries(parts)) {
      const c = selected.has(r) ? SELECT : r === hovered ? HOVER : marked.has(r) ? MARK : null;
      for (const m of p.mats) { if (c) m.emissive.copy(c); else m.emissive.setHex(0); }
    }
    dirty = true;
  }
  function setHover(ref, { silent = false } = {}) { if (ref === hovered) return; hovered = ref; cv.style.cursor = ref ? "pointer" : ""; paint(); if (!silent) emit("hover", ref); }

  // selection: an emissive tint plus a crisp cyan outline along the part's hard edges
  const outlineMat = new THREE.LineBasicMaterial({ color: ACCENT, transparent: true, opacity: 0.95, depthTest: true });
  const outlineBack = new THREE.LineBasicMaterial({ color: ACCENT, transparent: true, opacity: 0.22, depthTest: false });
  const outlines = new Map(); // ref -> [LineSegments]
  function outline(ref, on) {
    const p = parts[ref]; if (!p) return;
    if (on && !outlines.has(ref)) {
      const ls = [];
      for (const m of p.meshes) {
        const eg = new THREE.EdgesGeometry(m.geometry, 32);
        const a = new THREE.LineSegments(eg, outlineMat), b = new THREE.LineSegments(eg, outlineBack);
        a.renderOrder = 5; b.renderOrder = 6; a.raycast = b.raycast = () => {};
        m.add(a, b); ls.push(a, b);
      }
      outlines.set(ref, ls);
    }
    const ls = outlines.get(ref); if (ls) for (const l of ls) l.visible = on;
  }
  function select(refs) {
    const next = new Set(Array.isArray(refs) ? refs : refs ? [refs] : []);
    for (const r of selected) if (!next.has(r)) outline(r, false);
    for (const r of next) outline(r, true);
    selected = next; paint();
  }
  function mark(refs) { marked = new Set(refs || []); paint(); }

  // ---------- hide / isolate ----------
  const allBoard = [];
  function setHidden(ref, on) {
    if (on) hidden.add(ref); else hidden.delete(ref);
    if (parts[ref]) parts[ref].node.visible = !on && (!isolated || isolated.has(ref));
    dirty = true;
  }
  function applyIsolate() {
    for (const [r, p] of Object.entries(parts)) p.node.visible = !hidden.has(r) && (!isolated || isolated.has(r));
    for (const m of allBoard) {
      if (!m.userData.orig) m.userData.orig = { transparent: m.transparent, opacity: m.opacity, depthWrite: m.depthWrite };
      const o = m.userData.orig;
      if (isolated) { m.transparent = true; m.opacity = Math.min(o.opacity, 0.28); m.depthWrite = false; }
      else { m.transparent = o.transparent; m.opacity = o.opacity; m.depthWrite = o.depthWrite; }
      m.needsUpdate = true;
    }
    dirty = true;
  }
  function isolate(refs) { isolated = refs && refs.length ? new Set(refs) : null; applyIsolate(); }

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
    if (!model || e.button !== 0 || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6 || performance.now() - down.t > 600) return;
    const h = hit(e.clientX, e.clientY);
    const local = h ? glbRoot.worldToLocal(h.point.clone()) : null;
    const ref = h?.object.userData.ref || (local ? padPart(local) : null);
    const r = cv.getBoundingClientRect();
    emit("pick", { ref, part: ref ? info.parts[ref] || { ref } : null, anchor: local ? [local.x, local.y, local.z] : null, screen: [e.clientX - r.left, e.clientY - r.top], shift: e.shiftKey || e.metaKey || e.ctrlKey });
  });

  // ---------- projection for overlays (handles, cards, labels) ----------
  const tmp = new THREE.Vector3();
  function toScreen(v) {
    v.project(cam);
    return { x: ((v.x + 1) / 2) * W, y: ((1 - v.y) / 2) * H, behind: v.z > 1 || v.z < -1 };
  }
  function worldToScreen(v) { return toScreen(tmp.copy(v)); }
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
  // world-space box of a part: its 3D body, or a 2 mm pad-sized box at its parts.json position
  function partBox(ref) {
    const p = parts[ref];
    if (p) { const b = new THREE.Box3().setFromObject(p.node); return b.isEmpty() ? null : b; }
    const q = info.parts?.[ref]; if (!q?.pos_mm) return null;
    const c = glbRoot.localToWorld(new THREE.Vector3(q.pos_mm[0] / 1000, boardTop, q.pos_mm[1] / 1000));
    return new THREE.Box3(c.clone().add(new THREE.Vector3(-0.001, 0, -0.001)), c.clone().add(new THREE.Vector3(0.001, 0.0004, 0.001)));
  }
  function partAnchor(ref) {
    const b = partBox(ref); if (!b) return null;
    const c = b.getCenter(new THREE.Vector3()); c.y = b.max.y;
    const l = glbRoot.worldToLocal(c); return [l.x, l.y, l.z];
  }
  function placeLabels() {
    if (!floorGroup.visible) return;
    for (const r of rulers) {
      const s = toScreen(tmp.copy(r.p));
      const off = s.behind || s.x < -40 || s.x > W + 40 || s.y < -20 || s.y > H + 20;
      const shift = r.ax === "x" ? "translate(-50%, 6px)" : r.ax === "z" ? "translate(calc(-100% - 8px), -50%)" : "translate(calc(-100% - 8px), 6px)";
      r.el.style.transform = `translate(${s.x.toFixed(1)}px, ${s.y.toFixed(1)}px) ${shift}`;
      r.el.style.opacity = off ? "0" : "";
    }
  }

  // ---------- loop: render only when something changed ----------
  let firstFrame = false;
  function tick() {
    if (!alive) return;
    requestAnimationFrame(tick);
    const now = performance.now();
    if (anim) stepAnim(now);
    if (ctl.update()) dirty = true;
    if (pend && model) { const [x, y] = pend; pend = null; const h = hit(x, y); setHover(h?.object.userData.ref || null); }
    if (!dirty || !model) return;
    dirty = false;
    clip();
    const above = cam.position.y - ctl.target.y > -1e-5 || cam.position.y > 1e-4; // the workplane is at y = 0
    floorGroup.visible = floorOn && above; shadow.visible = above;
    labels.style.display = floorGroup.visible ? "" : "none";
    renderer.render(scene, cam);
    placeLabels();
    emit("frame");
    if (!firstFrame) { firstFrame = true; emit("ready"); }
  }
  requestAnimationFrame(tick);

  return {
    load, empty, reset, view, lookFrom, fitView, zoomBy, focus, setOrtho, select, mark, setHover, project, partAnchor, partBox,
    setHidden, isolate, worldToScreen, pickBoard, mmToScreen, boardRect,
    refAt: (x, y) => hit(x, y)?.object.userData.ref || null, // client coords -> refdes under them
    setMask, setSilk, setFinish, setFloor,
    get look() { return { ...look }; },
    get parts() { return parts; },
    get info() { return info; },
    get size() { return { w: W, h: H }; },
    get camera() { return cam; },
    get target() { return ctl.target; },
    get ortho() { return !!cam.isOrthographicCamera; },
    get selected() { return [...selected]; },
    get hidden() { return new Set(hidden); },
    get isolated() { return isolated ? [...isolated] : null; },
    get moving() { return !!anim; },
    get floor() { return floorOn; },
    get isEmpty() { return !!model?.empty; },
    get workplane() { return { ...wp }; },
    controls: ctl,
    canvas: cv,
    invalidate() { dirty = true; },
    on(name, f) { (ev[name] = ev[name] || []).push(f); return () => { ev[name] = ev[name].filter((x) => x !== f); }; },
    dispose() { alive = false; renderer.dispose(); cv.remove(); labels.remove(); },
  };
}

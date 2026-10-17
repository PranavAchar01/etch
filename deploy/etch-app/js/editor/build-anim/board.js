// The board being made, layer by layer, in board millimetres (x right, y down = scene +z, y = 0 on the board's top
// face): FR4 core with a woven-glass texture and a lit edge, a copper pour that wipes in from the middle (with real
// clearance around pads and tracks), ENIG pads per part, copper tracks drawn by a shader (a hot leading edge that
// cools to copper), tented vias that pop, a solder mask that washes over (translucent at its front, clearcoated),
// and silkscreen that prints in under a moving head. Each layer has its own progress uniform; build-anim.js drives
// them from the build events.
//
//   const b = createBoard(frame, { rect: [x0, y0, x1, y1], top, substrate: true, mask: "green" });
//   b.resize(rect); b.setPads(placed); b.setTraces(list); b.step(dt); b.dispose();
import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { clamp, springStep, hash } from "./stage.js";

export const GLOW_LAYER = 2, OCCLUDE_LAYER = 3;
export const MASK_COLORS = { green: 0x0a5428, purple: 0x4b2a83, red: 0xa82222, yellow: 0xd4b21c, blue: 0x173f93, white: 0xf1f1ee, black: 0x161616 };
const ENIG = 0xdfb85e, COPPER = 0xd4875a, FR4 = 0xcdbf86, HOT = new THREE.Color(1.0, 0.62, 0.32);
const Z = { pour: 0.012, hole: 0.006, pad: 0.012, padH: 0.036, trace: 0.03, via: 0.05, mask: 0.06, silk: 0.068 };

// ---------- textures (procedural, made once) ----------
let tex = null;
function textures() {
  if (tex) return tex;
  // woven glass: warp and weft bundles with a little noise, one tile = 1.2 mm
  const c = document.createElement("canvas"); c.width = c.height = 256;
  const g = c.getContext("2d");
  g.fillStyle = "#c9bb83"; g.fillRect(0, 0, 256, 256);
  for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) {
    const x = i * 64, y = j * 64, warp = (i + j) % 2 === 0;
    const grd = warp ? g.createLinearGradient(x, y, x, y + 64) : g.createLinearGradient(x, y, x + 64, y);
    grd.addColorStop(0, "rgba(120,104,60,0.28)"); grd.addColorStop(0.5, "rgba(255,248,214,0.22)"); grd.addColorStop(1, "rgba(120,104,60,0.28)");
    g.fillStyle = grd; g.fillRect(x + 2, y + 2, 60, 60);
    g.strokeStyle = "rgba(255,250,225,0.10)"; g.lineWidth = 1;
    for (let k = 6; k < 60; k += 7) { g.beginPath(); if (warp) { g.moveTo(x + k, y + 3); g.lineTo(x + k, y + 61); } else { g.moveTo(x + 3, y + k); g.lineTo(x + 61, y + k); } g.stroke(); }
  }
  const id = g.getImageData(0, 0, 256, 256);
  for (let i = 0; i < id.data.length; i += 4) { const n = (Math.random() - 0.5) * 10; id.data[i] += n; id.data[i + 1] += n; id.data[i + 2] += n * 0.7; }
  g.putImageData(id, 0, 0);
  const weave = new THREE.CanvasTexture(c);
  weave.colorSpace = THREE.SRGBColorSpace; weave.wrapS = weave.wrapT = THREE.RepeatWrapping; weave.anisotropy = 4;
  weave.repeat.set(1 / 1.2, 1 / 1.2);
  // soft contact shadow (a blurred rounded square)
  const s = document.createElement("canvas"); s.width = s.height = 128;
  const sg = s.getContext("2d"); sg.filter = "blur(11px)"; sg.fillStyle = "rgba(8,22,36,1)";
  sg.beginPath(); sg.roundRect(30, 30, 68, 68, 12); sg.fill();
  const shadow = new THREE.CanvasTexture(s); shadow.colorSpace = THREE.SRGBColorSpace;
  tex = { weave, shadow };
  return tex;
}
export function shadowTexture() { return textures().shadow; }

// ---------- shapes ----------
function roundRect(w, h, r, cx = 0, cy = 0) {
  r = Math.max(0.001, Math.min(r, w / 2 - 1e-4, h / 2 - 1e-4));
  const s = new THREE.Shape(), x = cx - w / 2, y = cy - h / 2;
  s.moveTo(x + r, y); s.lineTo(x + w - r, y); s.absarc(x + w - r, y + r, r, -Math.PI / 2, 0, false);
  s.lineTo(x + w, y + h - r); s.absarc(x + w - r, y + h - r, r, 0, Math.PI / 2, false);
  s.lineTo(x + r, y + h); s.absarc(x + r, y + h - r, r, Math.PI / 2, Math.PI, false);
  s.lineTo(x, y + r); s.absarc(x + r, y + r, r, Math.PI, Math.PI * 1.5, false);
  return s;
}
// shape coords (x, y) -> board (x, 0, y): ShapeGeometry lies in XY, rotate it onto the board
const toBoard = (g) => g.rotateX(-Math.PI / 2); // (shapes here are symmetric in y)
function rectUV(g, x0, y0, w, h) {
  const p = g.attributes.position, uv = new Float32Array(p.count * 2);
  for (let i = 0; i < p.count; i++) { uv[i * 2] = (p.getX(i) - x0) / w; uv[i * 2 + 1] = 1 - (p.getZ(i) - y0) / h; }
  g.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  return g;
}

// ---------- shader patches: a progress uniform per layer, in board mm (object space) ----------
function patch(mat, { uniforms, decl = "", vert = "", frag = "", emissive = "", rough = "" }) {
  mat.userData.u = uniforms;
  mat.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, uniforms);
    sh.vertexShader = sh.vertexShader.replace("#include <common>", "#include <common>\nvarying vec2 vBP;\n" + decl)
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvBP = position.xz;\n" + vert);
    sh.fragmentShader = sh.fragmentShader.replace("#include <common>", "#include <common>\nvarying vec2 vBP;\n" + decl +
      "\nfloat bnoise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f*f*(3.0-2.0*f); float a = fract(sin(dot(i, vec2(127.1,311.7)))*43758.5453), b = fract(sin(dot(i+vec2(1,0), vec2(127.1,311.7)))*43758.5453), c = fract(sin(dot(i+vec2(0,1), vec2(127.1,311.7)))*43758.5453), d = fract(sin(dot(i+vec2(1,1), vec2(127.1,311.7)))*43758.5453); return mix(mix(a,b,f.x), mix(c,d,f.x), f.y); }\n")
      .replace("#include <alphamap_fragment>", "#include <alphamap_fragment>\n" + frag)
      .replace("#include <emissivemap_fragment>", "#include <emissivemap_fragment>\n" + emissive)
      .replace("#include <roughnessmap_fragment>", "#include <roughnessmap_fragment>\n" + rough);
  };
  mat.customProgramCacheKey = () => "etch-build-" + (mat.userData.key || mat.type) + decl.length + frag.length;
  return mat;
}

export function createBoard(frame, { rect, top = 0.0016, substrate = true, mask = "green", thick = 1.6 } = {}) {
  textures();
  const mm = new THREE.Group(); mm.scale.setScalar(0.001); mm.position.y = top; mm.name = "etch-build-board";
  frame.add(mm);
  const boardG = new THREE.Group(); mm.add(boardG);   // grows and resizes (core, pour, mask, silk)
  const layG = new THREE.Group(); mm.add(layG);       // pads, tracks, vias: board mm, never stretched
  let R = { x0: rect[0], y0: rect[1], x1: rect[2], y1: rect[3] };
  const W = () => R.x1 - R.x0, H = () => R.y1 - R.y0, C = () => [(R.x0 + R.x1) / 2, (R.y0 + R.y1) / 2];
  const disposables = [];
  const keep = (x) => { disposables.push(x); return x; };

  // ---- FR4 core ----
  const coreMat = keep(new THREE.MeshPhysicalMaterial({ color: FR4, map: tex.weave, roughness: 0.62, metalness: 0, clearcoat: 0.18, clearcoatRoughness: 0.5, transparent: true, opacity: 1 }));
  let core = null;
  // ---- copper pour, revealed from the middle out ----
  const pourCanvas = document.createElement("canvas"), pourTex = keep(new THREE.CanvasTexture(pourCanvas));
  const pourMat = keep(patch(new THREE.MeshStandardMaterial({ color: COPPER, metalness: 1, roughness: 0.36, alphaMap: pourTex, alphaTest: 0.5, transparent: false, emissive: 0x000000 }), {
    uniforms: { uRev: { value: 0 }, uC: { value: new THREE.Vector2() }, uE: { value: new THREE.Vector2(1, 1) }, uHot: { value: HOT }, uFade: { value: 1 } },
    decl: "uniform float uRev, uFade; uniform vec2 uC, uE; uniform vec3 uHot;",
    frag: "float rr = length((vBP - uC) / uE) * 0.7071; float edge = uRev * 1.3 - 0.12 + (bnoise(vBP * 0.35) - 0.5) * 0.12; if (rr > edge || uFade < 0.01) discard; float rim = smoothstep(edge - 0.07, edge, rr) * step(uRev, 0.999);",
    emissive: "totalEmissiveRadiance += uHot * rim * 2.2;",
  }));
  pourMat.userData.key = "pour";
  let pour = null;
  // ---- mask, washed over (alpha opening at pads; lighter where copper lies under it) ----
  const maskCanvas = document.createElement("canvas"), maskTex = keep(new THREE.CanvasTexture(maskCanvas));
  maskTex.colorSpace = THREE.SRGBColorSpace;
  const maskMat = keep(patch(new THREE.MeshPhysicalMaterial({ color: 0xffffff, map: maskTex, roughness: 0.42, metalness: 0, clearcoat: 0.7, clearcoatRoughness: 0.18, transparent: true, depthWrite: false, opacity: 0.94, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2 }), {
    uniforms: { uWash: { value: 0 }, uMin: { value: new THREE.Vector2() }, uSize: { value: new THREE.Vector2(1, 1) } },
    decl: "uniform float uWash; uniform vec2 uMin, uSize;",
    frag: "vec2 mq = (vBP - uMin) / uSize; float ms = (mq.x * 0.62 + mq.y * 0.38) + (bnoise(vBP * 0.22) - 0.5) * 0.06; float mfront = uWash * 1.25 - 0.12; float mcov = smoothstep(mfront + 0.015, mfront - 0.02, ms); if (mcov < 0.003) discard; float mwet = smoothstep(mfront - 0.22, mfront, ms) * step(uWash, 0.999); diffuseColor.a *= mcov * mix(1.0, 0.45, mwet); if (diffuseColor.a < 0.01) discard;",
    rough: "roughnessFactor = mix(roughnessFactor, 0.08, mwet);",
  }));
  maskMat.userData.key = "mask";
  const maskHex = MASK_COLORS[mask] ?? MASK_COLORS.green;
  let maskMesh = null;
  // ---- silkscreen, printed in along y ----
  const silkCanvas = document.createElement("canvas"), silkTex = keep(new THREE.CanvasTexture(silkCanvas));
  silkTex.colorSpace = THREE.SRGBColorSpace; silkTex.anisotropy = 8;
  const silkMat = keep(patch(new THREE.MeshStandardMaterial({ color: 0xffffff, map: silkTex, roughness: 0.82, metalness: 0, transparent: true, alphaTest: 0.35, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4, emissive: 0x000000 }), {
    uniforms: { uPrint: { value: 0 }, uMin: { value: new THREE.Vector2() }, uSize: { value: new THREE.Vector2(1, 1) } },
    decl: "uniform float uPrint; uniform vec2 uMin, uSize;",
    frag: "float sq = (vBP.y - uMin.y) / uSize.y; float shead = uPrint * 1.06 - 0.03; if (sq > shead) discard; float sglow = exp(-abs(shead - sq) * 60.0) * step(uPrint, 0.999);",
    emissive: "totalEmissiveRadiance += vec3(0.9, 0.95, 1.0) * sglow * 1.4;",
  }));
  silkMat.userData.key = "silk";
  let silkMesh = null;

  // ---- grow / resize: rebuild at the new size, start from the old size's scale, spring to 1 ----
  const grow = { x: { x: substrate ? 0.02 : 1, v: 0, to: 1 }, z: { x: substrate ? 0.02 : 1, v: 0, to: 1 } };
  function build() {
    const w = W(), h = H(), [cx, cy] = C();
    boardG.position.set(cx, 0, cy);
    for (const m of [core, pour, maskMesh, silkMesh]) if (m) { boardG.remove(m); m.geometry.dispose(); }
    core = pour = maskMesh = silkMesh = null;
    if (substrate) {
      const bev = 0.14, g = new THREE.ExtrudeGeometry(roundRect(w, h, 1.0), { depth: thick - bev * 2, bevelEnabled: true, bevelThickness: bev, bevelSize: bev * 0.8, bevelSegments: 2, curveSegments: 10 });
      g.rotateX(Math.PI / 2); g.translate(0, -bev, 0); // top face (plus bevel) at y = 0
      core = new THREE.Mesh(g, coreMat); core.name = "core"; boardG.add(core);
    }
    const pg = rectUV(toBoard(new THREE.ShapeGeometry(roundRect(w - 0.8, h - 0.8, 0.7), 8)), -w / 2, -h / 2, w, h);
    pg.translate(0, Z.pour, 0);
    pour = new THREE.Mesh(pg, pourMat); pour.renderOrder = 0; boardG.add(pour);
    const mg = rectUV(toBoard(new THREE.ShapeGeometry(roundRect(w + 0.02, h + 0.02, 1.0), 10)), -w / 2, -h / 2, w, h);
    mg.translate(0, Z.mask, 0);
    maskMesh = new THREE.Mesh(mg, maskMat); maskMesh.renderOrder = 2; maskMesh.visible = false; boardG.add(maskMesh);
    const sgm = rectUV(toBoard(new THREE.PlaneGeometry(w, h)), -w / 2, -h / 2, w, h);
    sgm.translate(0, Z.silk, 0);
    silkMesh = new THREE.Mesh(sgm, silkMat); silkMesh.renderOrder = 3; silkMesh.visible = false; boardG.add(silkMesh);
    pourMat.userData.u.uC.value.set(0, 0); pourMat.userData.u.uE.value.set(w / 2, h / 2);
    maskMat.userData.u.uMin.value.set(-w / 2, -h / 2); maskMat.userData.u.uSize.value.set(w, h);
    silkMat.userData.u.uMin.value.set(-w / 2, -h / 2); silkMat.userData.u.uSize.value.set(w, h);
    paint();
  }
  // pour clearance + mask openings + mask tint + silk, from the pads, tracks and parts we know
  let padList = [], traceList = [], silkList = [], tintCanvas = null;
  function canvasFor(cv, pxmm, t) {
    const w = W(), h = H(), k = Math.min(pxmm, 4096 / Math.max(w, h));
    const cw = Math.max(4, Math.round(w * k)), ch = Math.max(4, Math.round(h * k));
    // a texture's storage is fixed once uploaded: a new size needs a fresh one
    if (cv.width !== cw || cv.height !== ch) { cv.width = cw; cv.height = ch; t.dispose(); }
    const g = cv.getContext("2d");
    g.setTransform(k, 0, 0, k, -R.x0 * k, -R.y0 * k);
    return g;
  }
  function padPath(g, p, grow = 0) {
    g.save(); g.translate(p.x, p.y); g.rotate(-p.rot);
    const w = p.w + grow * 2, h = p.h + grow * 2;
    g.beginPath();
    if (p.shape === 1) g.ellipse(0, 0, w / 2, h / 2, 0, 0, Math.PI * 2);
    else g.roundRect(-w / 2, -h / 2, w, h, p.shape === 2 ? Math.min(w, h) / 2 : p.shape === 3 ? Math.min(w, h) * 0.25 + grow : Math.min(0.05 + grow, Math.min(w, h) / 2));
    g.restore();
  }
  function paint() {
    // copper pour: white = copper; clearance (black) round pads and tracks, a margin at the edge
    let g = canvasFor(pourCanvas, 14, pourTex);
    g.fillStyle = "#000"; g.fillRect(R.x0, R.y0, W(), H());
    g.fillStyle = "#fff"; g.beginPath(); g.roundRect(R.x0 + 0.5, R.y0 + 0.5, W() - 1, H() - 1, 0.7); g.fill();
    g.fillStyle = "#000"; g.strokeStyle = "#000"; g.lineCap = "round"; g.lineJoin = "round";
    for (const p of padList) { padPath(g, p, 0.32); g.fill(); }
    for (const t of traceList) { g.lineWidth = t.w + 0.6; g.beginPath(); t.pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.stroke(); }
    pourTex.needsUpdate = true;
    // mask colour: darker over bare core, lighter over copper, open at pads (alpha 0)
    const base = new THREE.Color(maskHex), dark = base.clone().multiplyScalar(0.86), light = base.clone().lerp(new THREE.Color(0x7fbf5a), mask === "green" ? 0.22 : 0.12);
    g = canvasFor(maskCanvas, 12, maskTex);
    g.fillStyle = "#" + dark.getHexString(); g.fillRect(R.x0 - 1, R.y0 - 1, W() + 2, H() + 2);
    // copper under the mask reads through as a lighter tint of it
    const tint = tintCanvas || (tintCanvas = document.createElement("canvas"));
    tint.width = pourCanvas.width; tint.height = pourCanvas.height;
    const tg = tint.getContext("2d"); tg.drawImage(pourCanvas, 0, 0); tg.globalCompositeOperation = "multiply"; tg.fillStyle = "#" + light.getHexString(); tg.fillRect(0, 0, tint.width, tint.height);
    tg.globalCompositeOperation = "source-over";
    g.globalCompositeOperation = "lighten"; g.drawImage(tint, R.x0, R.y0, W(), H()); g.globalCompositeOperation = "source-over";
    g.strokeStyle = "#" + light.clone().multiplyScalar(1.12).getHexString(); g.lineCap = "round"; g.lineJoin = "round";
    for (const t of traceList) { g.lineWidth = t.w; g.beginPath(); t.pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.stroke(); }
    g.globalCompositeOperation = "destination-out";
    for (const p of padList) { padPath(g, p, 0.05); g.fill(); }
    g.globalCompositeOperation = "source-over";
    maskTex.needsUpdate = true;
    // silkscreen: footprint outlines and refdes, white on transparent
    g = canvasFor(silkCanvas, 22, silkTex);
    g.clearRect(R.x0 - 1, R.y0 - 1, W() + 2, H() + 2);
    g.strokeStyle = g.fillStyle = "#f6f6f2"; g.lineCap = "round"; g.lineJoin = "round";
    for (const s of silkList) {
      for (const st of s.strokes) { g.lineWidth = Math.max(0.12, st.w); g.beginPath(); st.pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.stroke(); }
      if (s.label) {
        const fs = clamp(s.size * 0.28, 0.7, 1.15);
        g.font = `600 ${fs}px Figtree, system-ui, sans-serif`; g.textAlign = "center"; g.textBaseline = "bottom";
        g.fillText(s.label, s.lx, s.ly);
      }
    }
    silkTex.needsUpdate = true;
  }

  // ---- pads: one merged mesh per part (gold) plus its drilled holes (dark) ----
  const padMat = keep(new THREE.MeshStandardMaterial({ color: ENIG, metalness: 1, roughness: 0.24, emissive: 0x000000, transparent: true, opacity: 1 }));
  const holeMat = keep(new THREE.MeshStandardMaterial({ color: 0x0c0c0b, roughness: 1, metalness: 0, transparent: true, opacity: 1 }));
  const padGroups = new Map(); // ref -> { mesh, holes, pop }
  function padShape(p) {
    if (p.shape === 1) { const s = new THREE.Shape(); s.absellipse(0, 0, p.w / 2, p.h / 2, 0, Math.PI * 2, false, 0); return s; }
    const r = p.shape === 2 ? Math.min(p.w, p.h) / 2 : p.shape === 3 ? Math.min(p.w, p.h) * 0.25 : 0.04;
    return roundRect(p.w, p.h, r);
  }
  // placed: [{ ref, pads: [{x, y, w, h, shape, drill, rot, npth}] }] in board mm (already transformed)
  function setPads(placed) {
    const keepRefs = new Set(placed.map((p) => p.ref));
    for (const [ref, pg] of padGroups) if (!keepRefs.has(ref)) { layG.remove(pg.mesh); pg.mesh.geometry.dispose(); if (pg.holes) { layG.remove(pg.holes); pg.holes.geometry.dispose(); } padGroups.delete(ref); }
    padList = [];
    for (const pl of placed) {
      padList.push(...pl.pads.filter((p) => !p.npth));
      const sig = pl.pads.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)},${p.rot.toFixed(2)}`).join(";");
      const old = padGroups.get(pl.ref);
      if (old && old.sig === sig) continue;
      if (old) { layG.remove(old.mesh); old.mesh.geometry.dispose(); if (old.holes) { layG.remove(old.holes); old.holes.geometry.dispose(); } }
      const gs = [], hs = [];
      for (const p of pl.pads) {
        if (p.drill > 0) {
          const hg = new THREE.CircleGeometry(p.drill / 2, 20); hg.rotateX(-Math.PI / 2); hg.translate(p.x, Z.hole, p.y); hs.push(hg);
        }
        if (p.npth) continue;
        const s = padShape(p);
        if (p.drill > 0) { const hole = new THREE.Path(); hole.absarc(0, 0, p.drill / 2, 0, Math.PI * 2, true); s.holes.push(hole); }
        const g = new THREE.ExtrudeGeometry(s, { depth: Z.padH - Z.pad, bevelEnabled: false, curveSegments: 12 });
        g.rotateX(Math.PI / 2); g.translate(0, Z.padH, 0); // shape y -> +z, top at padH
        g.rotateY(p.rot); g.translate(p.x, 0, p.y);
        gs.push(g);
      }
      if (!gs.length && !hs.length) continue;
      const mesh = new THREE.Mesh(gs.length ? mergeGeometries(gs.map((g) => g.index ? g.toNonIndexed() : g)) : new THREE.BufferGeometry(), padMat);
      const holes = hs.length ? new THREE.Mesh(mergeGeometries(hs.map((g) => g.index ? g.toNonIndexed() : g)), holeMat) : null;
      for (const g of gs.concat(hs)) g.dispose();
      layG.add(mesh); if (holes) layG.add(holes);
      const pg = { mesh, holes, sig, pop: { x: old ? 1 : 0, v: 0, to: 1 }, at: old ? -1 : null, center: pl.center };
      if (!old) { mesh.visible = false; if (holes) holes.visible = false; }
      padGroups.set(pl.ref, pg);
    }
    paint();
  }
  // a part's pads appear (stencil flash): call when its part starts to fly in
  function showPads(ref, delay = 0) { const pg = padGroups.get(ref); if (pg && pg.at === null) pg.at = clock + delay; }
  function padsShown(ref) { const pg = padGroups.get(ref); return !!pg && pg.at !== null && clock >= pg.at; }

  // ---- tracks: one ribbon mesh, per-track draw state in a texture (start time, erase time) ----
  const MAXT = 1024;
  const stateData = new Float32Array(MAXT * 4).fill(-1);
  const stateTex = keep(new THREE.DataTexture(stateData, MAXT, 1, THREE.RGBAFormat, THREE.FloatType));
  stateTex.needsUpdate = true;
  const tu = { uState: { value: stateTex }, uT: { value: 0 }, uSpeed: { value: 30 }, uHot: { value: HOT }, uPulse: { value: 0 }, uPulseOn: { value: 0 }, uMaskCol: { value: new THREE.Color(maskHex).lerp(new THREE.Color(0x9ad07a), 0.25) }, uMaskAmt: { value: 0 }, uFade: { value: 1 } };
  const traceVert = `
    attribute float aS; attribute float aId; attribute float aLen;
    uniform sampler2D uState; uniform float uT, uSpeed;
    varying float vS, vHead, vTail, vLen, vId;`;
  const traceVertBody = `
    vec4 tst = texelFetch(uState, ivec2(int(aId + 0.5), 0), 0);
    vS = aS; vLen = aLen; vId = aId;
    vHead = tst.x < -0.5 ? -1.0 : (uT - tst.x) * uSpeed;
    vTail = tst.y < -0.5 ? -1e4 : (uT - tst.y) * uSpeed * 2.2;`;
  const traceFragDecl = `
    uniform float uT, uPulse, uPulseOn, uMaskAmt, uFade; uniform vec3 uHot, uMaskCol;
    varying float vS, vHead, vTail, vLen, vId;
    float traceHeat(){ float d = vHead - vS; return exp(-max(d, 0.0) / 1.1) * (1.0 - smoothstep(vLen, vLen + 6.0, vHead)); }
    float tracePulse(){ float on = step(0.62, fract(sin(vId * 12.9898 + floor(uPulse * 0.25) * 4.1) * 43758.5)); float p = fract((vS - uPulse * 14.0) / 22.0); return on * uPulseOn * smoothstep(0.0, 0.06, p) * (1.0 - smoothstep(0.06, 0.16, p)) * step(vS, vLen); }`;
  const traceMat = keep(new THREE.MeshStandardMaterial({ color: COPPER, metalness: 1, roughness: 0.27, emissive: 0x000000, transparent: true, depthWrite: true }));
  traceMat.userData.u = tu;
  traceMat.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, tu);
    sh.vertexShader = sh.vertexShader.replace("#include <common>", "#include <common>\n" + traceVert).replace("#include <begin_vertex>", "#include <begin_vertex>\n" + traceVertBody);
    sh.fragmentShader = sh.fragmentShader.replace("#include <common>", "#include <common>\n" + traceFragDecl)
      .replace("#include <alphamap_fragment>", "#include <alphamap_fragment>\nif (vS > vHead || vS < vTail) discard; float th = traceHeat(); float tp = tracePulse(); diffuseColor.rgb = mix(diffuseColor.rgb, uMaskCol, uMaskAmt); diffuseColor.a *= uFade;")
      .replace("#include <metalnessmap_fragment>", "#include <metalnessmap_fragment>\nmetalnessFactor = mix(metalnessFactor, 0.0, uMaskAmt);")
      .replace("#include <roughnessmap_fragment>", "#include <roughnessmap_fragment>\nroughnessFactor = mix(roughnessFactor, 0.32, uMaskAmt);")
      .replace("#include <emissivemap_fragment>", "#include <emissivemap_fragment>\ntotalEmissiveRadiance += uHot * (th * 5.0 + tp * 1.6) * uFade;");
  };
  traceMat.customProgramCacheKey = () => "etch-build-trace";
  // the same tracks on the glow layer: only the hot head and the pulses
  const glowMat = keep(new THREE.ShaderMaterial({
    uniforms: tu, transparent: true, depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending,
    vertexShader: traceVert + "\nvoid main(){" + traceVertBody + "\ngl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
    fragmentShader: traceFragDecl + "\nvoid main(){ if (vS > vHead || vS < vTail) discard; float h = traceHeat() * 1.4 + tracePulse() * 0.7; if (h < 0.004) discard; gl_FragColor = vec4(uHot * h * uFade, 1.0); }",
  }));
  let traceMesh = null, traceGlow = null, traces = []; // { id, pts, w, len, t0, tk, via }
  function ribbon(t) {
    // three vertices across (left edge, crown, right edge) per point: a crowned copper strip with mitred corners
    const pts = t.pts, n = pts.length, pos = [], nor = [], s = [], ids = [], lens = [], idx = [];
    let acc = 0;
    for (let i = 0; i < n; i++) {
      const [x, y] = pts[i];
      if (i) acc += Math.hypot(x - pts[i - 1][0], y - pts[i - 1][1]);
      const a = pts[Math.max(0, i - 1)], b = pts[Math.min(n - 1, i + 1)];
      let dx0 = x - a[0], dy0 = y - a[1], dx1 = b[0] - x, dy1 = b[1] - y;
      const l0 = Math.hypot(dx0, dy0) || 1, l1 = Math.hypot(dx1, dy1) || 1;
      dx0 /= l0; dy0 /= l0; dx1 /= l1; dy1 /= l1;
      if (i === 0) { dx0 = dx1; dy0 = dy1; } if (i === n - 1) { dx1 = dx0; dy1 = dy0; }
      let tx = dx0 + dx1, ty = dy0 + dy1; const tl = Math.hypot(tx, ty) || 1; tx /= tl; ty /= tl;
      const px = -ty, py = tx, miter = 1 / Math.max(0.5, px * -dy0 + py * dx0), hw = (t.w / 2) * miter;
      for (const side of [-1, 0, 1]) {
        pos.push(x + px * hw * side, Z.trace + (side ? 0 : 0.012), y + py * hw * side);
        const nx = px * side * 0.55, nz = py * side * 0.55, nl = Math.hypot(nx, 1, nz);
        nor.push(nx / nl, 1 / nl, nz / nl);
        s.push(acc); ids.push(t.id); lens.push(t.len);
      }
      if (i) { const o = (i - 1) * 3; idx.push(o, o + 3, o + 1, o + 1, o + 3, o + 4, o + 1, o + 4, o + 2, o + 2, o + 4, o + 5); }
    }
    return { pos, nor, s, ids, lens, idx };
  }
  function rebuildTraces() {
    if (traceMesh) { layG.remove(traceMesh, traceGlow); traceMesh.geometry.dispose(); }
    traceMesh = traceGlow = null;
    if (!traces.length) return;
    const P = [], N = [], S = [], I = [], L = [], X = [];
    for (const t of traces) {
      const r = ribbon(t), base = P.length / 3;
      P.push(...r.pos); N.push(...r.nor); S.push(...r.s); I.push(...r.ids); L.push(...r.lens); for (const k of r.idx) X.push(k + base);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(P, 3)); g.setAttribute("normal", new THREE.Float32BufferAttribute(N, 3));
    g.setAttribute("aS", new THREE.Float32BufferAttribute(S, 1)); g.setAttribute("aId", new THREE.Float32BufferAttribute(I, 1)); g.setAttribute("aLen", new THREE.Float32BufferAttribute(L, 1));
    g.setIndex(X); g.computeBoundingSphere();
    traceMesh = new THREE.Mesh(g, traceMat); traceMesh.renderOrder = 1;
    traceGlow = new THREE.Mesh(g, glowMat); traceGlow.layers.set(GLOW_LAYER); traceGlow.frustumCulled = false;
    layG.add(traceMesh, traceGlow);
  }
  let nextId = 0;
  // list: [{ pts: [[x, y], …], w, via?: [x, y] }]; draw: start drawing them now, spread over `span` seconds
  function setTraces(list, { span = 3, keepDrawn = false } = {}) {
    const old = new Map(traces.map((t) => [t.key, t]));
    traces = [];
    stateData.fill(-1);
    nextId = 0;
    const order = list.map((t, i) => ({ t, i, k: hash(t.key || i) })).sort((a, b) => a.k - b.k);
    let at = 0;
    const gap = list.length > 1 ? span / list.length : 0;
    for (const { t } of order) {
      let len = 0; for (let i = 1; i < t.pts.length; i++) len += Math.hypot(t.pts[i][0] - t.pts[i - 1][0], t.pts[i][1] - t.pts[i - 1][1]);
      const prev = keepDrawn && t.key ? old.get(t.key) : null;
      const tr = { ...t, id: nextId++, len, t0: prev ? prev.t0 : clock + at + 0.05, tk: -1 };
      if (!prev) at += gap;
      traces.push(tr);
      stateData[tr.id * 4] = tr.t0; stateData[tr.id * 4 + 1] = -1;
    }
    stateTex.needsUpdate = true;
    traceList = traces;
    rebuildTraces(); paint();
    planVias();
  }
  // erase some tracks (they retract from their start), then draw them again `again` seconds later
  function reroute(keys, again = 0.6) {
    const ks = new Set(keys);
    for (const t of traces) if (ks.has(t.key)) { t.tk = clock; stateData[t.id * 4 + 1] = clock; t.redrawAt = clock + again; }
    stateTex.needsUpdate = true;
  }
  function drawnAll() { return traces.every((t) => (clock - t.t0) * tu.uSpeed.value > t.len); }
  function eraseAll() { for (const t of traces) { t.tk = clock; stateData[t.id * 4 + 1] = clock; } stateTex.needsUpdate = true; }

  // ---- vias: tented rings, they pop when their track's head passes them ----
  const viaRing = keep(new THREE.RingGeometry(0.16, 0.33, 24)); viaRing.rotateX(-Math.PI / 2);
  const viaHole = keep(new THREE.CircleGeometry(0.16, 16)); viaHole.rotateX(-Math.PI / 2);
  const viaMat = keep(new THREE.MeshStandardMaterial({ color: ENIG, metalness: 1, roughness: 0.25, emissive: new THREE.Color(1, 0.6, 0.3), emissiveIntensity: 0 }));
  const viaGlowMat = keep(new THREE.MeshBasicMaterial({ color: HOT, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false }));
  const vias = new THREE.InstancedMesh(viaRing, viaMat, 256), viaH = new THREE.InstancedMesh(viaHole, holeMat, 256), viaG = new THREE.InstancedMesh(viaRing, viaGlowMat, 256);
  vias.count = viaH.count = viaG.count = 0; vias.position.y = viaH.position.y = Z.via; viaH.position.y = Z.via - 0.002;
  viaG.layers.set(GLOW_LAYER); viaG.position.y = Z.via + 0.002; viaG.frustumCulled = vias.frustumCulled = viaH.frustumCulled = false;
  layG.add(vias, viaH, viaG);
  let viaList = [];
  function planVias() {
    viaList = [];
    for (const t of traces) for (const v of t.vias || []) viaList.push({ x: v[0], y: v[1], t, s: v[2] ?? t.len, sp: { x: 0, v: 0, to: 0 } });
    vias.count = viaH.count = viaG.count = Math.min(256, viaList.length);
  }
  const m4 = new THREE.Matrix4(), q0 = new THREE.Quaternion(), v3 = new THREE.Vector3(), sc = new THREE.Vector3();
  function stepVias(dt) {
    for (let i = 0; i < vias.count; i++) {
      const v = viaList[i], head = (clock - v.t.t0) * tu.uSpeed.value, tail = v.t.tk < 0 ? -1e4 : (clock - v.t.tk) * tu.uSpeed.value * 2.2;
      v.sp.to = head >= v.s && tail < v.s ? 1 : 0;
      springStep(v.sp, dt, 260, 0.45);
      const k = Math.max(0, v.sp.x);
      m4.compose(v3.set(v.x, 0, v.y), q0, sc.set(k, 1, k));
      vias.setMatrixAt(i, m4); viaH.setMatrixAt(i, m4);
      const glow = v.sp.to ? clamp(1 - (head - v.s) / 6) : 0;
      m4.compose(v3.set(v.x, 0, v.y), q0, sc.set(k * (1 + glow * 1.6), 1, k * (1 + glow * 1.6)));
      viaG.setMatrixAt(i, m4);
      viaGlowMat.opacity = 1;
    }
    vias.instanceMatrix.needsUpdate = viaH.instanceMatrix.needsUpdate = viaG.instanceMatrix.needsUpdate = true;
  }

  // ---- progress of each layer ----
  const P = {
    pour: { x: 0, to: 0 }, mask: { x: 0, to: 0 }, silk: { x: 0, to: 0 }, maskTint: { x: 0, to: 0 }, fade: { x: 1, to: 1 }, core: { x: substrate ? 1 : 0, to: substrate ? 1 : 0 },
  };
  const rate = { pour: 1 / 1.1, mask: 1 / 1.7, silk: 1 / 1.3, maskTint: 1 / 1.2, fade: 1 / 0.6, core: 1 / 0.6 };
  let clock = 0, pulseOn = 0;
  function step(dt, { speed = 1 } = {}) {
    clock += dt;
    let moving = false;
    for (const k of ["x", "z"]) { if (!springStep(grow[k], dt * speed, 120, 0.78)) moving = true; }
    boardG.scale.set(Math.max(0.001, grow.x.x), 1, Math.max(0.001, grow.z.x));
    for (const [k, p] of Object.entries(P)) {
      const r = rate[k] * speed * dt;
      if (p.x < p.to) { p.x = Math.min(p.to, p.x + r); moving = true; } else if (p.x > p.to) { p.x = Math.max(p.to, p.x - r); moving = true; }
    }
    pourMat.userData.u.uRev.value = P.pour.x; pour && (pour.visible = P.pour.x > 0.001);
    maskMat.userData.u.uWash.value = P.mask.x; maskMesh && (maskMesh.visible = P.mask.x > 0.001);
    silkMat.userData.u.uPrint.value = P.silk.x; silkMesh && (silkMesh.visible = P.silk.x > 0.001);
    tu.uMaskAmt.value = P.maskTint.x;
    const fade = P.fade.x;
    tu.uFade.value = fade;
    pourMat.userData.u.uFade.value = fade;
    for (const m of [padMat, holeMat]) m.opacity = fade;
    silkMat.opacity = fade; maskMat.opacity = 0.94 * fade;
    coreMat.opacity = P.core.x; coreMat.transparent = P.core.x < 0.999; coreMat.depthWrite = P.core.x > 0.5;
    if (core) core.visible = P.core.x > 0.002;
    // tracks: the clock, re-draws that are due, idle pulses once everything is drawn
    tu.uT.value = clock;
    let dirtyState = false;
    for (const t of traces) if (t.redrawAt && clock >= t.redrawAt) { t.t0 = clock; t.tk = -1; t.redrawAt = 0; stateData[t.id * 4] = clock; stateData[t.id * 4 + 1] = -1; dirtyState = true; }
    if (dirtyState) stateTex.needsUpdate = true;
    pulseOn += ((pulsing && drawnAll() ? 1 : 0) - pulseOn) * Math.min(1, dt * 2);
    tu.uPulse.value = clock; tu.uPulseOn.value = pulseOn;
    if (traces.length && !drawnAll()) moving = true;
    // pads: pop in with a gold glint once their part is on its way
    let glint = 0;
    for (const pg of padGroups.values()) {
      if (pg.at === null) continue;
      const on = clock >= pg.at;
      pg.mesh.visible = on; if (pg.holes) pg.holes.visible = on;
      if (!on) { moving = true; continue; }
      if (!springStep(pg.pop, dt * speed, 320, 0.55)) moving = true;
      const k = Math.max(0.001, pg.pop.x);
      pg.mesh.scale.set(1, k, 1);
      glint = Math.max(glint, clamp(1 - (clock - pg.at) / 0.5));
    }
    padMat.emissive.setRGB(0.55 * glint, 0.4 * glint, 0.18 * glint);
    stepVias(dt);
    return moving || pulseOn > 0.01;
  }
  let pulsing = false;

  build();
  return {
    mm, layG, boardG,
    get rect() { return [R.x0, R.y0, R.x1, R.y1]; },
    get clock() { return clock; },
    resize(rect, { animate = true } = {}) {
      const ow = W(), oh = H();
      R = { x0: rect[0], y0: rect[1], x1: rect[2], y1: rect[3] };
      build();
      if (animate) { grow.x.x *= ow / W(); grow.z.x *= oh / H(); } else { grow.x.x = grow.z.x = 1; grow.x.v = grow.z.v = 0; }
    },
    // grow the core from nothing (new boards): x first, then y
    growIn() { grow.x.x = 0.02; grow.z.x = 0.02; grow.x.to = 1; grow.z.to = 1; grow.z.v = -2; },
    get grown() { return Math.abs(grow.x.x - 1) < 0.02 && Math.abs(grow.z.x - 1) < 0.02; },
    set(k, v) { P[k].to = v; },
    jump(k, v) { P[k].x = P[k].to = v; },
    get(k) { return P[k].x; },
    setPads, showPads, padsShown,
    setSilk(list) { silkList = list; paint(); },
    setTraces, reroute, eraseAll,
    get traces() { return traces; },
    get drawn() { return drawnAll(); },
    set speed(v) { tu.uSpeed.value = v; },
    set pulsing(v) { pulsing = v; },
    step,
    // the outline's world box (for the camera and the scan)
    worldBox() { mm.updateMatrixWorld(true); const b = new THREE.Box3(); const c = new THREE.Vector3(); for (const [x, y] of [[R.x0, R.y0], [R.x1, R.y0], [R.x0, R.y1], [R.x1, R.y1]]) for (const yy of [-thick, 1]) b.expandByPoint(mm.localToWorld(c.set(x, yy, y))); return b; },
    coreMaterial: coreMat, maskMaterial: maskMat,
    dispose() {
      frame.remove(mm);
      mm.traverse((o) => { if (o.geometry && !disposables.includes(o.geometry)) o.geometry.dispose(); });
      for (const d of disposables) d.dispose?.();
      vias.dispose(); viaH.dispose(); viaG.dispose();
    },
  };
}

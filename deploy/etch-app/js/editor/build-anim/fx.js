// Light effects for the build animation, all cheap enough for 60 fps:
//  - glow: a selective bloom. Only what is on the glow layer (the tracks' hot leading edges, popping vias, the scan
//    line) is drawn into a half-size target (the animated parts write depth first so they hide what is behind them),
//    blurred at half and quarter size and added onto the frame after the viewer's own render. The viewer's render,
//    tone mapping and antialiasing are left exactly as they are.
//  - scan: a light sweep across the board (an additive band over a fine grid), cyan for a check that passes, red for
//    one that fails, plus red pulses where it failed.
//  - contact shadows: a soft dark blob under each part, sharper and darker as it touches down.
import * as THREE from "three";
import { GLOW_LAYER, OCCLUDE_LAYER, shadowTexture } from "./board.js";

const FS_VERT = "varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }";

export function createGlow(renderer) {
  const size = new THREE.Vector2();
  let rt = null, w = 0, h = 0;
  const mk = (a, b) => new THREE.WebGLRenderTarget(a, b, { type: THREE.HalfFloatType, depthBuffer: false, samples: 0 });
  const blur = new THREE.ShaderMaterial({
    uniforms: { tMap: { value: null }, uDir: { value: new THREE.Vector2() } }, depthTest: false, depthWrite: false,
    vertexShader: FS_VERT,
    fragmentShader: `uniform sampler2D tMap; uniform vec2 uDir; varying vec2 vUv;
      void main(){ vec4 c = texture2D(tMap, vUv) * 0.2270270270;
        c += (texture2D(tMap, vUv + uDir * 1.3846153846) + texture2D(tMap, vUv - uDir * 1.3846153846)) * 0.3162162162;
        c += (texture2D(tMap, vUv + uDir * 3.2307692308) + texture2D(tMap, vUv - uDir * 3.2307692308)) * 0.0702702703;
        gl_FragColor = c; }`,
  });
  const comp = new THREE.ShaderMaterial({
    uniforms: { tA: { value: null }, tB: { value: null }, uK: { value: 1 } }, depthTest: false, depthWrite: false, transparent: true,
    blending: THREE.CustomBlending, blendEquation: THREE.AddEquation, blendSrc: THREE.OneFactor, blendDst: THREE.OneFactor, blendSrcAlpha: THREE.ZeroFactor, blendDstAlpha: THREE.OneFactor,
    vertexShader: FS_VERT,
    fragmentShader: `uniform sampler2D tA, tB; uniform float uK; varying vec2 vUv;
      void main(){ vec3 c = texture2D(tA, vUv).rgb * 0.9 + texture2D(tB, vUv).rgb * 1.25; c = 1.0 - exp(-c * uK); gl_FragColor = vec4(c, 0.0); }`,
  });
  const copy = new THREE.ShaderMaterial({ uniforms: { tMap: { value: null } }, depthTest: false, depthWrite: false, vertexShader: FS_VERT, fragmentShader: "uniform sampler2D tMap; varying vec2 vUv; void main(){ gl_FragColor = texture2D(tMap, vUv); }" });
  const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), blur); quad.frustumCulled = false;
  const qs = new THREE.Scene(); qs.add(quad);
  const qc = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const depthOnly = new THREE.MeshBasicMaterial({ colorWrite: false });
  const prevClear = new THREE.Color();
  function ensure() {
    renderer.getDrawingBufferSize(size);
    const a = Math.max(2, size.x >> 1), b = Math.max(2, size.y >> 1);
    if (rt && a === w && b === h) return;
    if (rt) for (const t of Object.values(rt)) t.dispose();
    w = a; h = b;
    // the scene pass needs depth (parts in front hide the glow behind them); the blur targets don't
    const scenePass = new THREE.WebGLRenderTarget(w, h, { type: THREE.HalfFloatType, depthBuffer: true });
    rt = { s: scenePass, a: mk(w, h), q1: mk(w >> 1, h >> 1), q2: mk(w >> 1, h >> 1) };
  }
  function pass(mat, src, dst, dx, dy) {
    quad.material = mat;
    if (mat === blur) { blur.uniforms.tMap.value = src; blur.uniforms.uDir.value.set(dx, dy); } else mat.uniforms.tMap.value = src;
    renderer.setRenderTarget(dst); renderer.render(qs, qc);
  }
  let strength = 1;
  return {
    set strength(v) { strength = v; },
    render(scene, camera) {
      ensure();
      const prevRT = renderer.getRenderTarget(), prevAuto = renderer.autoClear, mask = camera.layers.mask, prevOverride = scene.overrideMaterial;
      renderer.getClearColor(prevClear); const prevAlpha = renderer.getClearAlpha();
      const prevSort = renderer.sortObjects;
      try {
        renderer.autoClear = false;
        renderer.setClearColor(0x000000, 0);
        renderer.setRenderTarget(rt.s); renderer.clear(true, true, false);
        camera.layers.set(OCCLUDE_LAYER); scene.overrideMaterial = depthOnly; renderer.render(scene, camera);
        scene.overrideMaterial = null; camera.layers.set(GLOW_LAYER); renderer.render(scene, camera);
        camera.layers.mask = mask;
        // half: blur h + v; quarter: copy down, blur h + v
        pass(blur, rt.s.texture, rt.a, 1 / w, 0); pass(blur, rt.a.texture, rt.s, 0, 1 / h);
        pass(copy, rt.s.texture, rt.q1); pass(blur, rt.q1.texture, rt.q2, 2 / w, 0); pass(blur, rt.q2.texture, rt.q1, 0, 2 / h);
        pass(blur, rt.q1.texture, rt.q2, 4 / w, 0); pass(blur, rt.q2.texture, rt.q1, 0, 4 / h);
        comp.uniforms.tA.value = rt.s.texture; comp.uniforms.tB.value = rt.q1.texture; comp.uniforms.uK.value = strength;
        quad.material = comp; renderer.setRenderTarget(null); renderer.render(qs, qc);
      } finally {
        camera.layers.mask = mask; scene.overrideMaterial = prevOverride;
        renderer.setRenderTarget(prevRT); renderer.autoClear = prevAuto; renderer.setClearColor(prevClear, prevAlpha); renderer.sortObjects = prevSort;
      }
    },
    dispose() { if (rt) for (const t of Object.values(rt)) t.dispose(); blur.dispose(); comp.dispose(); copy.dispose(); quad.geometry.dispose(); depthOnly.dispose(); },
  };
}

// a sweep over a w x h mm board, centred on (cx, cy) in board mm, just above the top face
export function createScan(parent, rect) {
  const u = { uX: { value: -1 }, uA: { value: 0 }, uCol: { value: new THREE.Color(0.35, 0.85, 1.0) }, uDir: { value: 1 }, uSize: { value: new THREE.Vector2(1, 1) } };
  const vert = "varying vec2 vUv; varying vec2 vP; uniform vec2 uSize; void main(){ vUv = uv; vP = (uv - 0.5) * uSize; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }";
  const frag = (k) => `uniform float uX, uA, uDir; uniform vec3 uCol; varying vec2 vUv; varying vec2 vP;
    void main(){ float x = uDir > 0.0 ? vUv.x : 1.0 - vUv.x; float d = x - uX; float band = exp(-d * d / 0.00035);
      float trail = d < 0.0 ? exp(d / 0.09) : 0.0; vec2 g = abs(fract(vP * 0.8) - 0.5); float grid = smoothstep(0.43, 0.5, max(g.x, g.y));
      float edge = smoothstep(0.0, 0.02, vUv.y) * smoothstep(1.0, 0.98, vUv.y);
      float a = (band * (${k ? "1.4" : "0.55"} + grid * 1.2) + trail * grid * 0.22) * uA * edge; if (a < 0.002) discard; gl_FragColor = vec4(uCol * a, 1.0); }`;
  const mat = (k) => new THREE.ShaderMaterial({ uniforms: u, vertexShader: vert, fragmentShader: frag(k), transparent: true, depthWrite: false, blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneFactor, blendSrcAlpha: THREE.ZeroFactor, blendDstAlpha: THREE.OneFactor });
  const geo = new THREE.PlaneGeometry(1, 1); geo.rotateX(-Math.PI / 2);
  const a = new THREE.Mesh(geo, mat(false)), b = new THREE.Mesh(geo, mat(true));
  b.layers.set(GLOW_LAYER);
  a.renderOrder = b.renderOrder = 8;
  const g = new THREE.Group(); g.add(a, b); g.visible = false;
  parent.add(g);
  // red pulses where a check failed
  const ringU = [];
  const ringGeo = new THREE.PlaneGeometry(1, 1); ringGeo.rotateX(-Math.PI / 2);
  const rings = new THREE.Group(); parent.add(rings);
  function place(r) { const w = r[2] - r[0], h = r[3] - r[1]; g.position.set((r[0] + r[2]) / 2, 0.2, (r[1] + r[3]) / 2); g.scale.set(w + 2, 1, h + 2); u.uSize.value.set(w + 2, h + 2); }
  place(rect);
  const runs = []; // { t, dur, ok, spots }
  let t = 0;
  return {
    resize: place,
    // one sweep; returns its duration
    sweep({ ok = true, dur = 0.7, spots = [] } = {}) { runs.push({ at: Math.max(t, runs.length ? runs[runs.length - 1].at + runs[runs.length - 1].dur * 0.8 : t), dur, ok, spots, dir: runs.length % 2 ? -1 : 1 }); },
    get busy() { return runs.length > 0; },
    step(dt) {
      t += dt;
      const r = runs[0];
      if (!r) { g.visible = false; return false; }
      const k = (t - r.at) / r.dur;
      if (k < 0) { g.visible = false; return true; }
      g.visible = true;
      u.uX.value = -0.08 + k * 1.16; u.uDir.value = r.dir;
      u.uA.value = Math.min(1, k * 6, (1.15 - k) * 6);
      u.uCol.value.setRGB(...(r.ok ? [0.32, 0.86, 1.0] : [1.0, 0.22, 0.16]));
      if (!r.ok && !r.flashed && k > 0.45) {
        r.flashed = true;
        for (const s of r.spots) {
          const uu = { uT: { value: 0 } };
          const m = new THREE.ShaderMaterial({ uniforms: uu, transparent: true, depthWrite: false, blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneFactor, blendSrcAlpha: THREE.ZeroFactor, blendDstAlpha: THREE.OneFactor,
            vertexShader: "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
            fragmentShader: `uniform float uT; varying vec2 vUv; void main(){ float r = length(vUv - 0.5) * 2.0; float a = 0.0;
              for (int i = 0; i < 3; i++) { float p = fract(uT * 1.1 - float(i) * 0.33); a += exp(-pow((r - p) * 9.0, 2.0)) * (1.0 - p); }
              a += exp(-r * r * 6.0) * 0.55 * (0.6 + 0.4 * sin(uT * 18.0)); a *= smoothstep(1.0, 0.85, r) * min(1.0, (2.2 - uT) * 2.0); if (a < 0.003) discard; gl_FragColor = vec4(vec3(1.0, 0.18, 0.12) * a, 1.0); }` });
          const ms = new THREE.Mesh(ringGeo, m); ms.position.set(s[0], 0.25, s[1]); ms.scale.setScalar(s[2] || 6); ms.renderOrder = 9;
          const mg = new THREE.Mesh(ringGeo, m); mg.position.copy(ms.position); mg.scale.copy(ms.scale); mg.layers.set(GLOW_LAYER);
          rings.add(ms, mg); ringU.push({ uu, meshes: [ms, mg], m });
        }
      }
      if (k >= 1.15) runs.shift();
      return true;
    },
    stepRings(dt) {
      for (let i = ringU.length - 1; i >= 0; i--) {
        const r = ringU[i]; r.uu.uT.value += dt;
        if (r.uu.uT.value > 2.2) { for (const m of r.meshes) rings.remove(m); r.m.dispose(); ringU.splice(i, 1); }
      }
      return ringU.length > 0;
    },
    dispose() { parent.remove(g, rings); geo.dispose(); ringGeo.dispose(); a.material.dispose(); b.material.dispose(); for (const r of ringU) r.m.dispose(); },
  };
}

// a contact shadow: a soft blob on a surface, positioned each frame by its owner
export function contactShadow() {
  const m = new THREE.MeshBasicMaterial({ map: shadowTexture(), transparent: true, opacity: 0, depthWrite: false, toneMapped: false, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -8 });
  const g = contactShadow.geo || (contactShadow.geo = (() => { const p = new THREE.PlaneGeometry(1, 1); p.rotateX(-Math.PI / 2); return p; })());
  const mesh = new THREE.Mesh(g, m); mesh.renderOrder = 4; mesh.raycast = () => {};
  return mesh;
}

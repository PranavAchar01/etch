// One shared hook into a board viewer's scene for the 3D add-ons (the build animation, the new-board backdrop):
// per-frame updates that run inside the viewer's own render (scene.onBeforeRender, so they see this frame's camera
// and never lag a frame), after-render passes (the glow composite), and "keep rendering while I animate".
//
//   const st = stageOf(viewer);
//   const off = st.add((dt, now) => keepGoing);   // called right before each render; return true to keep rendering
//   st.after((renderer, scene, camera) => {});    // right after each render
import * as THREE from "three";

const stages = new WeakMap();

export function stageOf(viewer) {
  if (stages.has(viewer)) return stages.get(viewer);
  const scene = viewer.scene, renderer = viewer.renderer;
  const tickers = new Set(), afters = new Set();
  let last = performance.now(), live = false;
  const prev = scene.onBeforeRender;
  scene.onBeforeRender = function (r, s, cam, rt) {
    if (rt) return; // only the viewer's own frame, not passes into render targets
    const now = performance.now(), dt = Math.min(0.05, Math.max(0, (now - last) / 1000));
    last = now;
    live = false;
    for (const f of [...tickers]) { try { if (f(dt, now, cam)) live = true; } catch (e) { tickers.delete(f); console.warn("build animation:", e); } }
    // what the tickers moved has to be in this frame's matrices (the renderer updated them before this hook)
    s.updateMatrixWorld();
    cam.updateMatrixWorld();
    if (prev) prev.call(this, r, s, cam, rt);
  };
  const offFrame = viewer.on("frame", () => {
    for (const f of [...afters]) { try { f(renderer, scene, viewer.camera); } catch (e) { afters.delete(f); console.warn("build animation:", e); } }
    if (live || tickers.size && [...tickers].some((f) => f.always)) viewer.invalidate();
  });
  const st = {
    THREE, scene, renderer, viewer,
    get camera() { return viewer.camera; },
    add(f, { always = false } = {}) { f.always = always; tickers.add(f); last = performance.now(); viewer.invalidate(); return () => tickers.delete(f); },
    after(f) { afters.add(f); return () => afters.delete(f); },
    kick() { viewer.invalidate(); },
    dispose() { scene.onBeforeRender = prev || (() => {}); offFrame(); stages.delete(viewer); },
  };
  stages.set(viewer, st);
  return st;
}

export const reducedMotion = () => typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

// small math helpers shared by the add-ons
export const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const smooth = (x) => { x = clamp(x); return x * x * (3 - 2 * x); };
export const easeOut = (x) => 1 - Math.pow(1 - clamp(x), 3);
export const easeInOut = (x) => { x = clamp(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
// frame-rate independent exponential approach
export const approach = (cur, target, dt, tau) => cur + (target - cur) * (1 - Math.exp(-dt / Math.max(1e-4, tau)));
export function hash(s) { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return (h >>> 0) / 4294967296; }
// a damped spring on a number: { x, v, to } stepped with a fixed sub-step
export function springStep(s, dt, k = 170, zeta = 0.62) {
  const c = 2 * Math.sqrt(k) * zeta;
  for (let left = dt; left > 1e-6; left -= 1 / 240) { const h = Math.min(1 / 240, left); s.v += (-k * (s.x - s.to) - c * s.v) * h; s.x += s.v * h; }
  return Math.abs(s.x - s.to) < 1e-4 && Math.abs(s.v) < 1e-3;
}

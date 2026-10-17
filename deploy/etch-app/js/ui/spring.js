// Tiny spring physics for every moving thing in the agent UI.
// One shared requestAnimationFrame loop runs only while something is moving and stops when everything settles.
// prefers-reduced-motion: springs jump straight to their target (callers fade with CSS instead) and impulses are ignored.

const mq = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
let reduced = !!(mq && mq.matches);
const rmSubs = new Set();
if (mq) {
  const on = (e) => { reduced = e.matches; for (const f of rmSubs) f(reduced); };
  if (mq.addEventListener) mq.addEventListener("change", on); else if (mq.addListener) mq.addListener(on);
}
export const reducedMotion = () => reduced;
export function onReducedMotion(f) { rmSubs.add(f); return () => rmSubs.delete(f); }

// stiffness / damping pairs (mass 1). critical = no overshoot; bouncy and wobbly overshoot on purpose.
export const presets = {
  critical: { stiffness: 260, damping: 32.2 },
  smooth: { stiffness: 190, damping: 24 },
  gentle: { stiffness: 90, damping: 17 },
  snappy: { stiffness: 620, damping: 44 },
  bouncy: { stiffness: 330, damping: 15 },
  wobbly: { stiffness: 440, damping: 11 },
  blink: { stiffness: 1400, damping: 60 },
};
const cfgOf = (p) => (typeof p === "string" ? presets[p] || presets.smooth : { ...presets.smooth, ...(p || {}) });

// ---- shared ticker: fn(dt, now) runs every frame until it returns false ----
const jobs = new Set();
let raf = 0, last = 0;
function frame(now) {
  const dt = Math.min(0.05, Math.max(0, (now - last) / 1000)) || 1 / 60;
  last = now;
  for (const j of Array.from(jobs)) {
    let keep;
    try { keep = j(dt, now); } catch (e) { keep = false; setTimeout(() => { throw e; }); }
    if (keep === false) jobs.delete(j);
  }
  raf = jobs.size ? requestAnimationFrame(frame) : 0;
}
export function tick(fn) {
  jobs.add(fn);
  if (!raf) { last = performance.now(); raf = requestAnimationFrame(frame); }
  return () => jobs.delete(fn);
}
export const activeJobs = () => jobs.size;

/**
 * spring(initial, { preset | stiffness, damping, mass, precision, onUpdate(value, s), onRest(value, s), ignoreReduced })
 * initial: a number, or an object of numbers ({x: 0, y: 0}). s.value is the live value (number or object).
 * s.set(target, { preset }) animates; s.jump(target) snaps; s.impulse(velocity) kicks; s.moving tells if it runs.
 */
export function spring(initial, opts = {}) {
  const scalar = typeof initial === "number";
  const val = {}, vel = {}, tgt = {};
  const keys = scalar ? ["v"] : Object.keys(initial);
  for (const k of keys) { val[k] = tgt[k] = scalar ? initial : initial[k]; vel[k] = 0; }
  let cfg = cfgOf(opts.preset || opts);
  const prec = opts.precision ?? 0.002;
  let stopTick = null;
  const out = () => (scalar ? val.v : val);
  const emit = () => opts.onUpdate && opts.onUpdate(out(), s);
  const merge = (dst, src) => { if (typeof src === "number") dst.v = src; else for (const k in src) if (k in dst) dst[k] = src[k]; };

  function step(dt) {
    const n = Math.max(1, Math.ceil(dt / (1 / 120))), h = dt / n, m = cfg.mass || 1;
    let busy = false;
    for (const k of keys) {
      let x = val[k], v = vel[k];
      const t = tgt[k];
      for (let i = 0; i < n; i++) { const f = -cfg.stiffness * (x - t) - cfg.damping * v; v += (f / m) * h; x += v * h; }
      if (Math.abs(v) < prec * 8 && Math.abs(x - t) < prec) { x = t; v = 0; } else busy = true;
      val[k] = x; vel[k] = v;
    }
    emit();
    if (!busy) { stopTick = null; opts.onRest && opts.onRest(out(), s); return false; }
    return true;
  }
  function run() { if (!stopTick) stopTick = tick(step); }

  const s = {
    get value() { return out(); },
    get target() { return scalar ? tgt.v : { ...tgt }; },
    get velocity() { return scalar ? vel.v : { ...vel }; },
    get moving() { return !!stopTick; },
    set(to, o = {}) {
      if (o.preset || o.stiffness) cfg = cfgOf(o.preset || o);
      merge(tgt, to);
      if (reduced && !opts.ignoreReduced) return s.jump(to);
      let differs = false;
      for (const k of keys) if (val[k] !== tgt[k] || vel[k] !== 0) differs = true;
      if (differs) run();
      return s;
    },
    jump(to) {
      if (to != null) { merge(tgt, to); merge(val, to); }
      for (const k of keys) { val[k] = tgt[k]; vel[k] = 0; }
      if (stopTick) { stopTick(); stopTick = null; }
      emit(); opts.onRest && opts.onRest(out(), s);
      return s;
    },
    impulse(dv) {
      if (reduced && !opts.ignoreReduced) return s;
      if (typeof dv === "number") vel[keys[0]] += dv; else for (const k in dv) if (k in vel) vel[k] += dv[k];
      run();
      return s;
    },
    config(p) { cfg = cfgOf(p); return s; },
    stop() { if (stopTick) { stopTick(); stopTick = null; } return s; },
  };
  return s;
}

// small math helpers shared by the kit
export const clamp = (x, a = 0, b = 1) => (x < a ? a : x > b ? b : x);
export const lerp = (a, b, t) => a + (b - a) * t;
export const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a)); return t * t * (3 - 2 * t); };

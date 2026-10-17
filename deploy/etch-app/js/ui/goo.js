// The liquid look: an SVG "goo" filter (blur, then a steep alpha threshold) so solid shapes inside a goo layer melt
// together when they come close and pinch apart when they separate. Plus the small behaviours built on it:
//   gooFilter()        inject the filter once, returns the CSS filter value
//   spawnBlob()        a blob that buds off a parent, stretches while it travels, settles; .absorb() drops it back in
//   squish(el)         press squish + wobble back for any button (.lq-btn are bound by bindLiquid())
//   liquidTabs(el)     segmented control whose indicator stretches between tabs and pinches thin while it moves
// Content never goes inside the goo layer (it would blur); it sits in a layer above that follows the same springs.
import { spring, reducedMotion, clamp, lerp } from "./spring.js";

const SVGNS = "http://www.w3.org/2000/svg";
const made = new Map();

// blur: how far shapes reach for each other (px). alpha/shift: the threshold (higher alpha = crisper edge).
export function gooFilter({ id = "ag-goo", blur = 9, alpha = 22, shift = -10 } = {}) {
  if (made.has(id)) return made.get(id);
  let host = document.getElementById("ag-goo-defs");
  if (!host) {
    host = document.createElementNS(SVGNS, "svg");
    host.id = "ag-goo-defs";
    host.setAttribute("aria-hidden", "true");
    host.setAttribute("width", "0");
    host.setAttribute("height", "0");
    host.style.cssText = "position:absolute;width:0;height:0;overflow:hidden;pointer-events:none";
    host.append(document.createElementNS(SVGNS, "defs"));
    document.body.append(host);
  }
  const f = document.createElementNS(SVGNS, "filter");
  f.id = id;
  for (const [k, v] of Object.entries({ x: "-25%", y: "-25%", width: "150%", height: "150%", "color-interpolation-filters": "sRGB" })) f.setAttribute(k, v);
  const b = document.createElementNS(SVGNS, "feGaussianBlur");
  b.setAttribute("in", "SourceGraphic"); b.setAttribute("stdDeviation", String(blur)); b.setAttribute("result", "blur");
  const m = document.createElementNS(SVGNS, "feColorMatrix");
  m.setAttribute("in", "blur"); m.setAttribute("mode", "matrix");
  m.setAttribute("values", `1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 ${alpha} ${shift}`);
  f.append(b, m);
  host.firstChild.append(f);
  const css = `url(#${id})`;
  made.set(id, css);
  return css;
}

// make an element a goo layer (its children merge). Extra CSS filters (a drop shadow) go after the goo.
export function gooLayer(el, opts = {}) {
  el.classList.add("lq-goo");
  el.style.filter = [gooFilter(opts), opts.after || ""].join(" ").trim();
  return el;
}

/**
 * spawnBlob(layer, { from: {x, y}, to: {x, y}, w, h, radius, className, content, contentLayerOffset })
 * x/y are centres in the layer's coordinates. `content` (optional) is an element in a non-goo layer above that
 * should ride along: it gets the same translate and fades in once the blob has budded off.
 * Returns { el, moveTo(x, y), resize(w, h), absorb({x, y}) -> Promise, remove() }.
 */
export function spawnBlob(layer, o) {
  const el = document.createElement("div");
  el.className = "lq-blob" + (o.className ? " " + o.className : "");
  let w = o.w || 40, h = o.h || 40;
  const size = () => { el.style.width = w + "px"; el.style.height = h + "px"; el.style.borderRadius = (o.radius ?? Math.min(w, h) / 2) + "px"; };
  size();
  layer.append(el);
  const from = o.from || o.to;
  const content = o.content || null;
  let gone = false, fadeIn = 0;
  const s = spring({ x: from.x, y: from.y, k: 0.25, rs: 1 }, {
    preset: { stiffness: 230, damping: 17 },
    precision: 0.01,
    onUpdate: (v, sp) => {
      const vel = sp.velocity;
      const sp2 = Math.hypot(vel.x, vel.y);
      const st = reducedMotion() ? 0 : clamp(sp2 / 1400, 0, 0.32);
      const ang = sp2 > 1 ? Math.atan2(vel.y, vel.x) : 0;
      const k = Math.max(0.05, v.k);
      // stretch along the direction of travel, thin across it
      el.style.transform = `translate(${v.x - w / 2}px, ${v.y - h / 2}px) rotate(${ang}rad) scale(${k * (1 + st) * v.rs}, ${k / (1 + st)}) rotate(${-ang}rad)`;
      if (content) {
        fadeIn = clamp((v.k - 0.7) / 0.3);
        content.style.transform = `translate(${v.x - w / 2}px, ${v.y - h / 2}px) scale(${lerp(0.92, 1, fadeIn) * v.rs})`;
        content.style.opacity = gone ? String(Math.min(fadeIn, 1 - clamp((1 - v.k) * 3))) : String(fadeIn);
      }
    },
  });
  s.set({ x: o.to.x, y: o.to.y, k: 1 });
  if (reducedMotion()) { el.classList.add("lq-fade"); if (content) content.classList.add("lq-fade"); }
  return {
    el,
    get x() { return s.target.x; },
    get y() { return s.target.y; },
    moveTo(x, y) { s.set({ x, y }); },
    // new size: the blob takes it at once and springs its scale from the old ratio, so it swells liquidly
    resize(nw, nh) {
      if (nw === w && nh === h) return;
      const ratio = w / nw;
      w = nw; h = nh; size();
      s.jump({ rs: ratio }); s.set({ rs: 1 }, { preset: { stiffness: 300, damping: 16 } });
    },
    absorb(into = from) {
      gone = true;
      return new Promise((done) => {
        s.config({ stiffness: 260, damping: 24 });
        s.set({ x: into.x, y: into.y, k: 0.2 });
        const fin = () => { el.remove(); done(); };
        if (reducedMotion()) { el.style.opacity = "0"; if (content) content.style.opacity = "0"; setTimeout(fin, 180); return; }
        const t0 = performance.now();
        const wait = () => { if (!s.moving || performance.now() - t0 > 900) fin(); else requestAnimationFrame(wait); };
        requestAnimationFrame(wait);
      });
    },
    remove() { s.stop(); el.remove(); },
  };
}

// ---- press squish: wider and shorter on press, wobbles back on release. Uses the `scale` property so it never
// fights a transform set elsewhere. ----
export function squish(el, { amount = 1 } = {}) {
  if (el.dataset.lq) return () => {};
  el.dataset.lq = "1";
  const s = spring({ x: 1, y: 1 }, { preset: "wobbly", precision: 0.0008, onUpdate: (v) => { el.style.scale = v.x === 1 && v.y === 1 ? "" : `${v.x} ${v.y}`; } });
  let down = false;
  const press = () => { if (el.disabled) return; down = true; s.set({ x: 1 + 0.05 * amount, y: 1 - 0.1 * amount }, { preset: "snappy" }); };
  const release = () => { if (!down) return; down = false; s.config("wobbly"); s.set({ x: 1, y: 1 }); s.impulse({ x: -1.2 * amount, y: 1.6 * amount }); };
  const kd = (e) => { if ((e.key === "Enter" || e.key === " ") && !e.repeat) press(); };
  const ku = (e) => { if (e.key === "Enter" || e.key === " ") release(); };
  el.addEventListener("pointerdown", press);
  el.addEventListener("pointerup", release);
  el.addEventListener("pointerleave", release);
  el.addEventListener("pointercancel", release);
  el.addEventListener("keydown", kd);
  el.addEventListener("keyup", ku);
  return () => {
    for (const [t, f] of [["pointerdown", press], ["pointerup", release], ["pointerleave", release], ["pointercancel", release], ["keydown", kd], ["keyup", ku]]) el.removeEventListener(t, f);
    delete el.dataset.lq; s.stop(); el.style.scale = "";
  };
}
// a gentle wobble on demand (something changed under the user's eyes)
export function wobble(el, strength = 1) {
  const s = spring({ x: 1, y: 1 }, { preset: "wobbly", precision: 0.0008, onUpdate: (v) => { el.style.scale = v.x === 1 && v.y === 1 ? "" : `${v.x} ${v.y}`; } });
  s.impulse({ x: 0.9 * strength, y: -1.3 * strength });
}
export function bindLiquid(root = document) {
  root.querySelectorAll(".lq-btn:not([data-lq])").forEach((b) => squish(b));
}

/**
 * liquidTabs(el, { value, onChange(value) })
 * el holds buttons with data-value (role="tab" is added). A goo indicator behind them stretches from the old tab to
 * the new one: the leading edge runs ahead, the trailing edge catches up, and the middle thins while stretched.
 * Returns { value, set(value), destroy() }.
 */
export function liquidTabs(el, { value, onChange } = {}) {
  el.classList.add("lq-tabs");
  el.setAttribute("role", el.getAttribute("role") || "tablist");
  const tabs = () => Array.from(el.querySelectorAll("[data-value]"));
  const ind = document.createElement("div");
  ind.className = "lq-tabs-ind";
  ind.setAttribute("aria-hidden", "true");
  const capL = document.createElement("i"), capR = document.createElement("i"), mid = document.createElement("i");
  capL.className = capR.className = "lq-cap"; mid.className = "lq-mid";
  ind.append(mid, capL, capR);
  el.prepend(ind);
  gooLayer(ind, { id: "ag-goo-tabs", blur: 3.2, alpha: 18, shift: -7 });

  let cur = value ?? tabs()[0]?.dataset.value, geo = { l: 0, r: 0, h: 0, top: 0 }, first = true;
  // one spring per edge: the edge in front is quick, the one behind is lazy
  const fast = { stiffness: 520, damping: 34 }, slow = { stiffness: 190, damping: 21 };
  const redraw = () => draw();
  const eL = spring(0, { precision: 0.05, onUpdate: redraw });
  const eR = spring(0, { precision: 0.05, onUpdate: redraw });
  function draw() {
    const h = geo.h, l = eL.value, r = Math.max(eR.value, l + h * 0.2);
    const width = r - l, want = Math.max(1, geo.r - geo.l);
    const thin = clamp(1 / Math.sqrt(Math.max(1, width / want)), 0.42, 1);
    capL.style.transform = `translate(${l}px, ${geo.top}px)`;
    capR.style.transform = `translate(${r - h}px, ${geo.top}px)`;
    mid.style.transform = `translate(${l + h / 2}px, ${geo.top + (h * (1 - thin)) / 2}px) scale(${Math.max(0, width - h) / 100}, ${thin})`;
  }
  function measure() {
    const t = tabs().find((b) => b.dataset.value === cur);
    if (!t) return;
    const l = t.offsetLeft, w = t.offsetWidth, h = t.offsetHeight, top = t.offsetTop;
    const oldL = geo.l, oldR = geo.r;
    geo = { l, r: l + w, h, top };
    capL.style.width = capL.style.height = capR.style.width = capR.style.height = h + "px";
    mid.style.height = h + "px";
    if (first) { first = false; eL.jump(l); eR.jump(l + w); draw(); return; }
    if (l === oldL && l + w === oldR) { draw(); return; }
    const right = l > oldL || l + w > oldR;
    eL.config(right ? slow : fast).set(l);
    eR.config(right ? fast : slow).set(l + w);
  }
  function paint() {
    for (const b of tabs()) {
      const on = b.dataset.value === cur;
      b.classList.toggle("on", on);
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    }
  }
  function set(v, fire = false) {
    if (v === cur && !first) return;
    cur = v; paint(); measure();
    if (fire && onChange) onChange(v);
  }
  const click = (e) => { const b = e.target.closest("[data-value]"); if (b && el.contains(b)) { set(b.dataset.value, true); b.focus(); } };
  const key = (e) => {
    const list = tabs(), i = list.findIndex((b) => b.dataset.value === cur);
    let j = -1;
    if (e.key === "ArrowRight") j = (i + 1) % list.length;
    else if (e.key === "ArrowLeft") j = (i - 1 + list.length) % list.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = list.length - 1;
    if (j < 0) return;
    e.preventDefault(); set(list[j].dataset.value, true); list[j].focus();
  };
  el.addEventListener("click", click);
  el.addEventListener("keydown", key);
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => measure()) : null;
  if (ro) { ro.observe(el); tabs().forEach((b) => ro.observe(b)); }
  paint();
  requestAnimationFrame(measure);
  return {
    get value() { return cur; },
    set: (v) => set(v, false),
    destroy() { el.removeEventListener("click", click); el.removeEventListener("keydown", key); if (ro) ro.disconnect(); ind.remove(); },
  };
}

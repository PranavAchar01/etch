// Shared helpers for the Explore library and the board view: URL params, where the library lives, board file URLs,
// a small Cache Storage layer (so a prefetched board opens instantly), Markdown for Etch's answers, icons, toasts.

export const Q = new URLSearchParams(location.search);
export const $ = (id) => document.getElementById(id);
export const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : String(c));
  return el;
};

// ?world=fixtures loads the test fixture library; anything else is the real one in world/
const W = (Q.get("world") || "world").replace(/[^a-z0-9_-]/gi, "") || "world";
export const WORLD = W + "/";
// keep ?world= / ?mock= / ?api= on every link between the pages
export function carry(params = {}) {
  const p = new URLSearchParams();
  for (const k of ["world", "mock", "api"]) if (Q.get(k)) p.set(k, Q.get(k));
  for (const [k, v] of Object.entries(params)) if (v != null && v !== "") p.set(k, v);
  const s = p.toString();
  return s ? "?" + s : "";
}
// Vercel serves /explore (cleanUrls); python's http.server only knows explore.html. Link the way we were reached.
const HTML = /\.html$/.test(location.pathname);
export const page = (name) => (name === "index" ? (HTML ? "index.html" : "/") : HTML ? name + ".html" : name);

export function slugOf(id) {
  return String(id).replace(/^gh:/, "").toLowerCase().replace(/\//g, "__").replace(/[^a-z0-9_.-]/g, "-");
}
export const isLive = (id) => /^live-\d+$/.test(String(id));
export function boardFiles(slug) {
  const b = `${WORLD}boards/${slug}/`;
  return { meta: b + "meta.json", parts: b + "parts.json", thumb: b + "thumb.webp", glb: b + "board.glb" };
}

// ---- Cache Storage: prefetched / visited board files. The service worker (sw.js) reads the same cache. ----
export const BOARD_CACHE = "etch-boards-v1";
const mem = new Map(); // url -> Promise<Response clone source> for this page's lifetime
const hasCaches = typeof caches !== "undefined";
async function fromCache(url) {
  if (!hasCaches) return null;
  try { return (await (await caches.open(BOARD_CACHE)).match(url)) || null; } catch (e) { return null; }
}
async function putCache(url, res) {
  if (!hasCaches || !res.ok) return;
  try { await (await caches.open(BOARD_CACHE)).put(url, res); } catch (e) { /* quota or private mode */ }
}
// fetch through the cache: answer from it at once when we have it, refresh it in the background
export function cachedFetch(url, { refresh = true } = {}) {
  if (mem.has(url)) return mem.get(url).then((r) => r.clone());
  const p = (async () => {
    const hit = await fromCache(url);
    if (hit) {
      if (refresh) fetch(url, { cache: "no-cache" }).then((r) => r.ok && putCache(url, r)).catch(() => {});
      return hit;
    }
    const r = await fetch(url);
    if (r.ok) putCache(url, r.clone());
    return r;
  })();
  mem.set(url, p);
  p.catch(() => mem.delete(url));
  return p.then((r) => r.clone());
}
// warm a board: its small files always, the GLB too unless the connection asks us to save data
const warmed = new Set();
export function prefetchBoard(slug, { glb = true } = {}) {
  const f = boardFiles(slug);
  const save = navigator.connection && (navigator.connection.saveData || /2g/.test(navigator.connection.effectiveType || ""));
  const urls = [f.meta, f.parts].concat(glb && !save ? [f.glb] : []);
  for (const u of urls) {
    if (warmed.has(u)) continue;
    warmed.add(u);
    (async () => { if (!(await fromCache(u))) { const r = await fetch(u, { priority: "low" }).catch(() => null); if (r && r.ok) await putCache(u, r); } })();
  }
}

// ---- text ----
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
// Etch's answers: Markdown with **bold** and `code` only; refdes it mentions become buttons that light the part up
export function md(text, refs = null) {
  let s = esc(text);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  if (refs && refs.size) {
    const names = [...refs].sort((a, b) => b.length - a.length).map((r) => r.replace(/[.*+?^${}()|[\]\\-]/g, "\\$&"));
    const re = new RegExp(`(^|[^A-Za-z0-9_-])(${names.join("|")})(?![A-Za-z0-9_-])`, "g");
    s = s.split(/(<[^>]+>)/).map((t) => (t.startsWith("<") ? t : t.replace(re, (m, a, r) => `${a}<button class="ref" data-ref="${r}">${r}</button>`))).join("");
  }
  return s.split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
}
export function ago(sec) {
  const d = Math.max(0, Date.now() / 1000 - sec);
  if (d < 45) return "just now";
  if (d < 3600) return Math.round(d / 60) + " min ago";
  if (d < 86400) return Math.round(d / 3600) + " h ago";
  return Math.round(d / 86400) + " d ago";
}
export const fmtStars = (n) => (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, "") + "k" : String(n || 0));

// ---- toast ----
let toastT = 0;
export function toast(msg) {
  let t = document.querySelector(".toast");
  if (!t) { t = h("div", { class: "toast", role: "status" }); document.body.append(t); }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastT);
  toastT = setTimeout(() => t.classList.remove("show"), 2200);
}

// ---- service worker: on in production, opt-in on localhost (?sw=1) so edits show on reload while developing ----
export function registerSW() {
  if (!("serviceWorker" in navigator)) return;
  const local = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  if (local && Q.get("sw") !== "1") {
    navigator.serviceWorker.getRegistrations().then((rs) => rs.forEach((r) => r.unregister())).catch(() => {});
    return;
  }
  addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}

// ---- icons (16px stroke, currentColor) ----
const P = {
  sidebar: '<rect x="2.5" y="3" width="13" height="12" rx="2.2"/><path d="M7 3v12"/>',
  undo: '<path d="M6 4.5 3 7.5l3 3"/><path d="M3.5 7.5H11a3.5 3.5 0 0 1 0 7H8"/>',
  redo: '<path d="m12 4.5 3 3-3 3"/><path d="M14.5 7.5H7a3.5 3.5 0 0 0 0 7h3"/>',
  history: '<path d="M3 9a6 6 0 1 0 1.8-4.3"/><path d="M3 3.5v3h3"/><path d="M9 6v3.2l2.2 1.4"/>',
  refresh: '<path d="M14.5 9A5.5 5.5 0 1 1 13 5.2"/><path d="M14 2.5v3.2h-3.2"/>',
  search: '<circle cx="8" cy="8" r="4.8"/><path d="m11.6 11.6 3.4 3.4"/>',
  panel: '<rect x="2.5" y="3" width="13" height="12" rx="2.2"/><path d="M11 3v12"/>',
  plus: '<path d="M9 3.5v11M3.5 9h11"/>',
  expand: '<path d="M10.5 3.5h4v4M7.5 14.5h-4v-4M14.5 3.5l-4.5 4.5M3.5 14.5 8 10"/>',
  send: '<path d="M9 14V4.5M4.8 8.6 9 4.4l4.2 4.2"/>',
  up: '<path d="M5.5 8.3V14H3.8a.8.8 0 0 1-.8-.8V9.1c0-.4.4-.8.8-.8zm0 0L8.3 3c1.2 0 2 .9 1.8 2l-.4 2h3.7c.9 0 1.6.9 1.4 1.8l-1 4.2c-.2.6-.7 1-1.4 1H5.5"/>',
  down: '<path d="M5.5 9.7V4H3.8a.8.8 0 0 0-.8.8v4.1c0 .4.4.8.8.8zm0 0L8.3 15c1.2 0 2-.9 1.8-2l-.4-2h3.7c.9 0 1.6-.9 1.4-1.8l-1-4.2c-.2-.6-.7-1-1.4-1H5.5"/>',
  close: '<path d="m5 5 8 8M13 5l-8 8"/>',
  trash: '<path d="M3.5 5h11M7.5 5V3.5h3V5M5 5l.7 9.2c0 .5.5.8 1 .8h4.6c.5 0 1-.3 1-.8L13 5"/>',
  chev: '<path d="m7 4.5 4.5 4.5L7 13.5"/>',
  download: '<path d="M9 3v8.5M5.3 8 9 11.7 12.7 8M3.5 14.5h11"/>',
  github: '<path d="M9 2.2a6.9 6.9 0 0 0-2.2 13.4c.4.1.5-.1.5-.3v-1.2c-1.9.4-2.3-.9-2.3-.9-.3-.8-.8-1-.8-1-.6-.4 0-.4 0-.4.7 0 1 .7 1 .7.6 1.1 1.6.8 2 .6 0-.4.2-.8.4-1-1.5-.2-3.1-.8-3.1-3.4 0-.7.3-1.4.7-1.9-.1-.2-.3-.9.1-1.8 0 0 .6-.2 1.9.7a6.5 6.5 0 0 1 3.4 0c1.3-.9 1.9-.7 1.9-.7.4.9.1 1.6.1 1.8.4.5.7 1.1.7 1.9 0 2.6-1.6 3.2-3.1 3.4.2.2.5.6.5 1.3v1.9c0 .2.1.4.5.3A6.9 6.9 0 0 0 9 2.2z"/>',
  star: '<path d="m9 2.8 1.9 3.9 4.2.6-3 3 .7 4.2L9 12.5l-3.8 2 .7-4.2-3-3 4.2-.6z"/>',
  link: '<path d="M7.5 10.5a3 3 0 0 0 4.2 0l2.1-2.1a3 3 0 0 0-4.2-4.2l-.8.8M10.5 7.5a3 3 0 0 0-4.2 0L4.2 9.6a3 3 0 0 0 4.2 4.2l.8-.8"/>',
  grid: '<rect x="3" y="3" width="5" height="5" rx="1"/><rect x="10" y="3" width="5" height="5" rx="1"/><rect x="3" y="10" width="5" height="5" rx="1"/><rect x="10" y="10" width="5" height="5" rx="1"/>',
  mic: '<rect x="6.5" y="2.5" width="5" height="8.5" rx="2.5"/><path d="M4 8.5a5 5 0 0 0 10 0M9 13.5v2.2"/>',
  info: '<circle cx="9" cy="9" r="6.2"/><path d="M9 8.2v4M9 5.8v.1"/>',
};
export const icon = (name, size = 18) => `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || ""}</svg>`;
// Etch's mark: a black rounded square with an etched trace and a via
export const LOGO = '<svg class="logo-mark" viewBox="0 0 20 20" aria-hidden="true"><rect width="20" height="20" rx="5" fill="#111"/><path d="M5 13.5h4l3-3.5V6" stroke="#fff" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="5.4" r="1.6" fill="#fff"/><circle cx="5" cy="13.5" r="1.3" fill="#fff"/></svg>';

// KiCad's raytraced stills come on a lavender gradient backdrop; key it out (each row's backdrop colour follows the
// edge pixel that continues the gradient) so a thumb sits on the page's own light background. Same as board.html's poster.
export function keyBackdrop(img) {
  const w = img.naturalWidth, hh = img.naturalHeight; if (!w || !hh) return null;
  const cv = document.createElement("canvas"); cv.width = w; cv.height = hh;
  const g = cv.getContext("2d", { willReadFrequently: true }); g.drawImage(img, 0, 0);
  try {
    const d = g.getImageData(0, 0, w, hh), p = d.data;
    let br = p[0], bg = p[1], bb = p[2];
    for (let y = 0; y < hh; y++) {
      const a = y * w * 4, z = (y * w + w - 1) * 4;
      const da = Math.abs(p[a] - br) + Math.abs(p[a + 1] - bg) + Math.abs(p[a + 2] - bb), dz = Math.abs(p[z] - br) + Math.abs(p[z + 1] - bg) + Math.abs(p[z + 2] - bb);
      const e = da <= dz ? a : z; if (Math.min(da, dz) < 24) { br = p[e]; bg = p[e + 1]; bb = p[e + 2]; }
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4, dr = p[i] - br, dg = p[i + 1] - bg, db = p[i + 2] - bb;
        const t = (Math.sqrt(dr * dr + dg * dg + db * db) - 10) / 26;
        if (t >= 1) continue;
        if (t <= 0) { p[i + 3] = 0; continue; }
        const k = t * t * (3 - 2 * t), l = (p[i] + p[i + 1] + p[i + 2]) / 3;
        p[i] = p[i + 1] = p[i + 2] = l * 0.6; p[i + 3] = Math.round(k * 160);
      }
    }
    g.putImageData(d, 0, 0);
  } catch (e) { return null; }
  return cv;
}

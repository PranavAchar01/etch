// Etch service worker: instant Explore and board pages. Precaches the app shell (CSS, JS, vendored three.js) and
// answers the library (index.json) and board files (meta, parts, thumb, GLB) stale-while-revalidate from the same
// Cache Storage that Explore's prefetch fills. Page navigations, config.json, the build app (index.html, app.js,
// viewer3d.js) and the backend are never touched.
const SHELL = "etch-shell-v1";
const BOARDS = "etch-boards-v1";
const SHELL_FILES = [
  "css/editor.css", "js/common.js", "js/explore.js", "js/board.js", "js/board-viewer.js", "js/etch-api.js", "js/mock-api.js", "js/voice.js", "js/mascot.js",
  "vendor/three/build/three.module.min.js", "vendor/three/examples/jsm/loaders/GLTFLoader.js", "vendor/three/examples/jsm/controls/OrbitControls.js",
  "vendor/three/examples/jsm/environments/RoomEnvironment.js", "vendor/three/examples/jsm/utils/BufferGeometryUtils.js", "vendor/three/examples/jsm/libs/meshopt_decoder.module.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k.startsWith("etch-") && k !== SHELL && k !== BOARDS).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

const base = new URL(self.registration.scope).pathname;
function kind(url) {
  if (url.origin !== location.origin) return null;
  const p = url.pathname.startsWith(base) ? url.pathname.slice(base.length) : url.pathname.replace(/^\//, "");
  if (/^(css|js|vendor)\//.test(p)) return SHELL;
  if (/^[a-z0-9_-]+\/(index\.json|boards\/[^/]+\/(meta\.json|parts\.json|thumb\.webp|board\.glb))$/i.test(p) && !/^(api|etch)\//.test(p)) return BOARDS;
  return null;
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || req.mode === "navigate") return;
  const url = new URL(req.url);
  const cache = kind(url);
  if (!cache) return;
  const key = url.origin + url.pathname; // ignore ?v= busting on our own assets
  e.respondWith((async () => {
    const c = await caches.open(cache);
    const hit = await c.match(key);
    const net = fetch(req).then((r) => { if (r.ok && r.status === 200) c.put(key, r.clone()); return r; });
    if (hit) { e.waitUntil(net.catch(() => {})); return hit; }
    return net;
  })());
});

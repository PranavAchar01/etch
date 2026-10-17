// Etch service worker: instant Explore and board pages. Precaches the app shell (CSS, JS, vendored three.js) and
// answers the library (index.json), board files (meta, parts, thumb, GLB) and part renders (parts/*.webp)
// stale-while-revalidate, the board files from the same Cache Storage that Explore's prefetch fills. Page navigations (explore, board, kit), config.json and the backend are
// never touched.
const SHELL = "etch-shell-v8";
const BOARDS = "etch-boards-v1";
const PARTS = "etch-parts-v1"; // the parts panel's static renders (parts/*.webp)
const SHELL_FILES = [
  "css/explore.css", "css/editor.css", "css/agent.css", "css/new-board.css", "js/common.js", "js/explore.js", "js/editor/main.js", "js/editor/viewcube.js", "js/editor/parts-panel.js", "js/editor/agent-bridge.js", "js/editor/selection.js", "js/editor/inspector.js", "js/editor/board-list.js", "js/editor/drop.js", "js/editor/icons.js", "js/editor/kinds.js", "js/editor/dock-loader.js", "js/editor/dock-stub.js", "js/editor/new-board.js", "js/editor/start-backdrop.js", "js/editor/render-loader.js",
  "js/ui/dock.js", "js/ui/eyes.js", "js/ui/goo.js", "js/ui/spring.js", "js/ui/trace.js", "js/ui/voice-adapter.js", "js/ui/job-pill.js", "js/board-viewer.js", "js/build-preview.js", "js/my-boards.js", "js/etch-api.js", "js/mock-api.js", "js/voice.js", "js/mascot.js",
  "vendor/three/build/three.module.min.js", "vendor/three/examples/jsm/loaders/GLTFLoader.js", "vendor/three/examples/jsm/controls/OrbitControls.js",
  "vendor/three/examples/jsm/environments/RoomEnvironment.js", "vendor/three/examples/jsm/utils/BufferGeometryUtils.js", "vendor/three/examples/jsm/libs/meshopt_decoder.module.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k.startsWith("etch-") && k !== SHELL && k !== BOARDS && k !== PARTS).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

const base = new URL(self.registration.scope).pathname;
function kind(url) {
  if (url.origin !== location.origin) return null;
  const p = url.pathname.startsWith(base) ? url.pathname.slice(base.length) : url.pathname.replace(/^\//, "");
  if (/^(css|js|vendor)\//.test(p)) return SHELL;
  if (/^parts\/[^/]+\.webp$/i.test(p)) return PARTS;
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

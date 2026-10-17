// Parts tab for the board editor: a family dropdown, an expanding search field and a 2-column grid of
// draggable part tiles. The editor owns the drag ghost and the drop; this module only reports drag start/end.
// Each tile shows the part's isolated 3D render (`thumb`, a static webp from library/render_parts.py) over a
// skeleton block that stays until the image has decoded; a part with no render keeps the neutral skeleton block.
//
//   const panel = createPartsPanel(hostEl, { onDragStart(part, evt), onDragEnd(part, evt) });
//   panel.filter('led');  panel.dispose();

const CATALOG_URL = new URL('./parts-catalog.json', import.meta.url);
const DRAG_THRESHOLD = 4;
const APP_ROOT = new URL('../../', import.meta.url);
const MAX_LOADS = 4; // images decoding at once
let catalogPromise = null;

function loadCatalog() {
  if (!catalogPromise) {
    catalogPromise = fetch(CATALOG_URL).then((r) => {
      if (!r.ok) throw new Error(`parts catalog ${r.status}`);
      return r.json();
    }).catch((err) => { catalogPromise = null; throw err; });
  }
  return catalogPromise;
}

const CSS = `
.pp-root{--pp-ink:#1d2433;--pp-mute:#6b7385;--pp-line:#e6e8ee;--pp-bg:#f6f7f9;--pp-tile:#fff;--pp-accent:#2f6fed;
  --pp-shadow:0 1px 2px rgba(20,28,45,.06),0 2px 8px rgba(20,28,45,.06);
  display:flex;flex-direction:column;height:100%;min-height:0;background:var(--pp-bg);color:var(--pp-ink);
  font-family:"Figtree",system-ui,sans-serif;font-size:13px;-webkit-font-smoothing:antialiased;position:relative}
.pp-head{padding:14px 14px 10px;display:flex;flex-direction:column;gap:10px;flex:none}
.pp-title{font-family:"Averia Serif Libre",Georgia,serif;font-size:19px;font-weight:700;margin:0;letter-spacing:.01em}
.pp-bar{display:flex;align-items:center;gap:8px;position:relative;height:36px}
.pp-dd{flex:1;min-width:0;position:relative;margin-right:44px;transition:opacity .18s ease}
.pp-dd-btn{all:unset;box-sizing:border-box;display:flex;align-items:center;gap:8px;width:100%;height:36px;padding:0 10px 0 12px;
  background:#fff;border:1px solid var(--pp-line);border-radius:10px;cursor:pointer;box-shadow:var(--pp-shadow);
  font:600 13px "Figtree",system-ui,sans-serif;color:var(--pp-ink);transition:border-color .15s ease}
.pp-dd-btn:hover{border-color:#cfd3dc}
.pp-dd-btn:focus-visible,.pp-search-btn:focus-visible,.pp-tile:focus-visible,.pp-opt:focus-visible{outline:2px solid var(--pp-accent);outline-offset:2px}
.pp-dd-label{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pp-dd-count{color:var(--pp-mute);font-weight:500;font-variant-numeric:tabular-nums}
.pp-chev{width:14px;height:14px;flex:none;transition:transform .2s cubic-bezier(.2,.8,.2,1)}
.pp-dd[data-open] .pp-chev{transform:rotate(180deg)}
.pp-menu{position:absolute;left:0;right:0;top:calc(100% + 6px);z-index:20;background:#fff;border:1px solid var(--pp-line);
  border-radius:12px;box-shadow:0 10px 30px rgba(20,28,45,.14);padding:5px;margin:0;list-style:none;max-height:min(440px,70vh);overflow:auto;
  opacity:0;transform:translateY(-4px) scale(.98);transform-origin:top center;pointer-events:none;
  transition:opacity .14s ease,transform .18s cubic-bezier(.2,.8,.2,1)}
.pp-dd[data-open] .pp-menu{opacity:1;transform:none;pointer-events:auto}
.pp-opt{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;cursor:pointer;font-weight:500}
.pp-opt:hover,.pp-opt[data-active]{background:#f1f3f7}
.pp-opt[aria-selected="true"]{color:var(--pp-accent);font-weight:600}
.pp-opt span:first-child{flex:1}
.pp-opt .pp-dd-count{font-size:12px}
.pp-search{position:absolute;right:0;top:0;height:36px;width:36px;display:flex;align-items:center;background:#fff;
  border:1px solid var(--pp-line);border-radius:10px;box-shadow:var(--pp-shadow);overflow:hidden;box-sizing:border-box;
  transition:width .26s cubic-bezier(.2,.8,.2,1)}
.pp-root[data-searching] .pp-search{width:100%}
.pp-root[data-searching] .pp-dd{opacity:0;pointer-events:none}
.pp-search-btn{all:unset;flex:none;width:34px;height:34px;display:grid;place-items:center;cursor:pointer;color:var(--pp-mute);border-radius:10px}
.pp-search-btn:hover{color:var(--pp-ink)}
.pp-search-btn svg{width:16px;height:16px}
.pp-input{all:unset;flex:1;min-width:0;height:34px;font:500 13px "Figtree",system-ui,sans-serif;color:var(--pp-ink);padding-right:4px}
.pp-input::-webkit-search-cancel-button,.pp-input::-webkit-search-decoration{-webkit-appearance:none;appearance:none;display:none}
.pp-input::placeholder{color:#9aa1b1}
.pp-clear{all:unset;flex:none;width:30px;height:34px;display:none;place-items:center;cursor:pointer;color:var(--pp-mute)}
.pp-root[data-searching] .pp-clear{display:grid}
.pp-clear svg{width:13px;height:13px}
.pp-scroll{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:4px 14px 16px;scrollbar-width:thin}
.pp-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.pp-tile{position:relative;display:flex;flex-direction:column;background:var(--pp-tile);border-radius:14px;
  box-shadow:0 0 0 1px #ececec,0 1px 2px rgba(17,17,17,.03),0 4px 14px rgba(17,17,17,.03);
  cursor:grab;user-select:none;-webkit-user-select:none;touch-action:none;overflow:hidden;contain:layout paint;
  transition:transform .32s cubic-bezier(.34,1.56,.64,1),box-shadow .2s ease}
.pp-tile:hover{box-shadow:0 0 0 1px #e4e4e4,0 2px 4px rgba(17,17,17,.04),0 12px 28px rgba(17,17,17,.07)}
.pp-tile[data-press]{transform:scale(1.04,.92);transition:transform .12s cubic-bezier(.3,.7,.4,1)}
.pp-tile[data-lift]{transform:translateY(-4px) scale(1.05);cursor:grabbing;
  box-shadow:0 0 0 1px #e4e4e4,0 6px 10px rgba(17,17,17,.08),0 16px 32px rgba(17,17,17,.16)}
.pp-art{position:relative;aspect-ratio:1/1;margin:6px 6px 0;display:grid}
.pp-art>*{grid-area:1/1;width:100%;height:100%}
.pp-art>img{object-fit:contain;display:block;pointer-events:none;-webkit-user-drag:none;opacity:0;transition:opacity .18s ease-out}
.pp-art>img[data-loaded]{opacity:1}
.pp-art>.pp-sk{border-radius:10px;transition:opacity .18s ease-out}
.pp-art[data-ready]>.pp-sk{opacity:0}
.pp-sk{display:block;background:linear-gradient(90deg,#eef0f3 0%,#eef0f3 30%,#f6f7f9 50%,#eef0f3 70%,#eef0f3 100%) 0 0/250% 100%;
  animation:pp-sk 1.4s linear infinite}
@keyframes pp-sk{from{background-position:100% 0}to{background-position:-150% 0}}
.pp-art[data-none]>.pp-sk{animation:none;background:#eef0f3}
.pp-sk-tile{cursor:default}
.pp-sk-tile .pp-name{display:flex;justify-content:center;align-items:center}
.pp-sk-tile .pp-name .pp-sk{width:60%;height:8px;border-radius:4px}
.pp-sk-bar{height:12px;border-radius:6px;width:96px}
.pp-sk-n{display:inline-block;width:18px;height:10px;border-radius:5px;vertical-align:middle}
.pp-root[data-loading] .pp-dd-btn,.pp-root[data-loading] .pp-search-btn{cursor:default}
.pp-name{flex:none;height:28px;padding:0 8px;font:600 12px/28px "Figtree",system-ui,sans-serif;text-align:center;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--pp-ink)}
.pp-empty{padding:32px 8px;text-align:center;color:var(--pp-mute);line-height:1.5}
.pp-note{font-size:12px;color:var(--pp-mute);padding:0 2px 8px}
@media (prefers-reduced-motion:reduce){.pp-root *{transition-duration:0s!important}.pp-sk{animation:none;background:#eef0f3}}
`;

function injectCss() {
  if (document.getElementById('pp-style')) return;
  const s = document.createElement('style');
  s.id = 'pp-style';
  s.textContent = CSS;
  document.head.appendChild(s);
}

const ICON_SEARCH = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="7" cy="7" r="4.6"/><path d="M10.6 10.6 14 14"/></svg>';
const ICON_X = '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M3 3l8 8M11 3l-8 8"/></svg>';
const ICON_CHEV = '<svg class="pp-chev" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 5.5 7 9l3.5-3.5"/></svg>';

const SK_TILES = 8;
const skTile = () => '<div class="pp-tile pp-sk-tile" aria-hidden="true"><div class="pp-art"><i class="pp-sk"></i></div><div class="pp-name"><i class="pp-sk"></i></div></div>';
const SK_GRID = Array.from({ length: SK_TILES }, skTile).join('');

// Until the editor creates the panel (it waits for the API config first), the empty pane shows a skeleton.
function preSkeleton() {
  const host = typeof document !== 'undefined' && document.getElementById('paneParts');
  if (!host || host.childElementCount) return;
  injectCss();
  const r = el('div', 'pp-root pp-pre');
  r.setAttribute('aria-busy', 'true');
  r.innerHTML = `<div class="pp-head"><h2 class="pp-title">Parts</h2><div class="pp-bar"><div class="pp-dd"><div class="pp-dd-btn"><span class="pp-dd-label"><i class="pp-sk pp-sk-bar"></i></span>${ICON_CHEV}</div></div><div class="pp-search"><span class="pp-search-btn">${ICON_SEARCH}</span></div></div></div><div class="pp-scroll"><div class="pp-grid">${SK_GRID}</div></div>`;
  host.appendChild(r);
}

function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
}

export function createPartsPanel(hostEl, { onDragStart, onDragEnd } = {}) {
  injectCss();
  hostEl.querySelectorAll(':scope > .pp-pre').forEach((n) => n.remove());
  const ac = new AbortController();
  const on = (t, type, fn, opts) => t.addEventListener(type, fn, { signal: ac.signal, ...opts });

  const root = el('div', 'pp-root');
  root.setAttribute('data-loading', '');
  root.setAttribute('aria-busy', 'true');
  root.innerHTML = `
    <div class="pp-head">
      <h2 class="pp-title">Parts</h2>
      <div class="pp-bar">
        <div class="pp-dd">
          <button class="pp-dd-btn" type="button" aria-haspopup="listbox" aria-expanded="false">
            <span class="pp-dd-label"><i class="pp-sk pp-sk-bar" aria-label="Loading parts"></i></span><span class="pp-dd-count"><i class="pp-sk pp-sk-n"></i></span>${ICON_CHEV}
          </button>
          <ul class="pp-menu" role="listbox" tabindex="-1"></ul>
        </div>
        <div class="pp-search">
          <button class="pp-search-btn" type="button" aria-label="Search parts">${ICON_SEARCH}</button>
          <input class="pp-input" type="search" placeholder="Search parts" aria-label="Search parts" tabindex="-1" autocomplete="off" spellcheck="false">
          <button class="pp-clear" type="button" aria-label="Close search">${ICON_X}</button>
        </div>
      </div>
    </div>
    <div class="pp-scroll"><div class="pp-note" hidden></div><div class="pp-grid" role="list">${SK_GRID}</div></div>`;
  hostEl.appendChild(root);

  const dd = root.querySelector('.pp-dd');
  const ddBtn = root.querySelector('.pp-dd-btn');
  const ddLabel = root.querySelector('.pp-dd-label');
  const ddCount = ddBtn.querySelector('.pp-dd-count');
  const menu = root.querySelector('.pp-menu');
  const searchBtn = root.querySelector('.pp-search-btn');
  const input = root.querySelector('.pp-input');
  const clearBtn = root.querySelector('.pp-clear');
  const scroller = root.querySelector('.pp-scroll');
  const note = root.querySelector('.pp-note');
  const grid = root.querySelector('.pp-grid');

  let catalog = { families: [], parts: [] };
  let family = null;
  let query = '';
  let disposed = false;
  let drag = null;
  const partOf = new WeakMap();

  // images load through a small queue (a few at a time), started when a tile nears the viewport
  const queue = [];
  let loading = 0;
  const io = 'IntersectionObserver' in window
    ? new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        io.unobserve(e.target);
        enqueue(e.target);
      }
    }, { root: scroller, rootMargin: '200px 0px' })
    : null;
  const thumbUrl = (part) => new URL(part.thumb, APP_ROOT).href;
  // decoded images by key, so switching families back and forth shows them at once
  const decoded = new Map();
  function enqueue(tile, front = false) {
    const part = partOf.get(tile);
    if (!part || tile.dataset.queued) return;
    tile.dataset.queued = '1';
    if (front) queue.unshift(tile); else queue.push(tile);
    pump();
  }
  function pump() {
    while (loading < MAX_LOADS && queue.length) {
      const tile = queue.shift();
      if (!tile.isConnected) continue;
      loading++;
      loadImg(tile).finally(() => { loading--; pump(); });
    }
  }
  // a part with no render keeps a still, neutral skeleton block (never a drawing)
  function noImage(art) { art.setAttribute('data-none', ''); }
  function reveal(art, img) {
    art.appendChild(img);
    requestAnimationFrame(() => {
      img.setAttribute('data-loaded', '');
      art.setAttribute('data-ready', '');
      setTimeout(() => art.querySelector(':scope > .pp-sk')?.remove(), 220);
    });
  }
  function fetchImg(url) {
    const img = new Image();
    img.alt = '';
    img.decoding = 'async';
    img.draggable = false;
    img.src = url;
    return (img.decode ? img.decode() : new Promise((ok, no) => { img.onload = ok; img.onerror = no; })).then(() => img);
  }
  function loadImg(tile) {
    const part = partOf.get(tile);
    const art = tile.querySelector('.pp-art');
    if (!part?.thumb) { noImage(art); return Promise.resolve(); }
    const url = thumbUrl(part);
    const got = decoded.get(url);
    const p = got ? Promise.resolve(got.cloneNode()) : fetchImg(url).then((img) => { decoded.set(url, img); return img.cloneNode(); });
    return p.then((img) => { if (!disposed && tile.isConnected) reveal(art, img); })
      .catch(() => { if (!disposed) noImage(art); });
  }
  // the first family's images start loading as soon as the catalog arrives, before their tiles scroll in
  function prefetch(parts) {
    for (const p of parts.slice(0, 24)) {
      if (!p.thumb) continue;
      const url = thumbUrl(p);
      if (!decoded.has(url)) fetchImg(url).then((img) => decoded.set(url, img)).catch(() => {});
    }
  }

  // ---------- dropdown ----------
  function renderMenu() {
    menu.innerHTML = '';
    const opts = [{ id: '__all', label: 'All parts', count: catalog.parts.length }, ...catalog.families];
    for (const f of opts) {
      const li = el('li', 'pp-opt');
      li.setAttribute('role', 'option');
      li.tabIndex = -1;
      li.dataset.id = f.id;
      li.setAttribute('aria-selected', String(f.id === family));
      li.innerHTML = `<span></span><span class="pp-dd-count">${f.count}</span>`;
      li.firstChild.textContent = f.label;
      menu.appendChild(li);
    }
  }
  function familyInfo(id) {
    if (id === '__all') return { label: 'All parts', count: catalog.parts.length };
    return catalog.families.find((f) => f.id === id) || { label: '', count: 0 };
  }
  function openMenu(open) {
    if (open) { dd.setAttribute('data-open', ''); } else { dd.removeAttribute('data-open'); }
    ddBtn.setAttribute('aria-expanded', String(open));
    if (open) {
      const sel = menu.querySelector('[aria-selected="true"]') || menu.firstElementChild;
      sel?.focus();
    }
  }
  function setFamily(id) {
    family = id;
    const f = familyInfo(id);
    ddLabel.textContent = f.label;
    ddCount.textContent = f.count;
    menu.querySelectorAll('.pp-opt').forEach((o) => o.setAttribute('aria-selected', String(o.dataset.id === id)));
    scroller.scrollTop = 0;
    render();
  }
  on(ddBtn, 'click', () => { if (!root.hasAttribute('data-loading')) openMenu(!dd.hasAttribute('data-open')); });
  on(ddBtn, 'keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); openMenu(true); }
  });
  on(menu, 'click', (e) => {
    const o = e.target.closest('.pp-opt');
    if (!o) return;
    setFamily(o.dataset.id);
    openMenu(false);
    ddBtn.focus();
  });
  on(menu, 'keydown', (e) => {
    const items = [...menu.querySelectorAll('.pp-opt')];
    const i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const n = items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length];
      n.focus();
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (items[i]) { setFamily(items[i].dataset.id); openMenu(false); ddBtn.focus(); }
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      openMenu(false);
      if (e.key === 'Escape') ddBtn.focus();
    }
  });
  on(document, 'pointerdown', (e) => {
    if (dd.hasAttribute('data-open') && !dd.contains(e.target)) openMenu(false);
  });

  // ---------- search ----------
  function openSearch(open, { focus = true } = {}) {
    if (open) {
      root.setAttribute('data-searching', '');
      input.tabIndex = 0;
      if (focus) setTimeout(() => input.focus(), 30);
    } else {
      root.removeAttribute('data-searching');
      input.tabIndex = -1;
      if (input.value) { input.value = ''; query = ''; render(); }
    }
  }
  on(searchBtn, 'click', () => {
    if (root.hasAttribute('data-searching')) input.focus(); else openSearch(true);
  });
  on(clearBtn, 'click', () => { openSearch(false); searchBtn.focus(); });
  on(input, 'input', () => { query = input.value; scroller.scrollTop = 0; render(); });
  on(input, 'keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); openSearch(false); searchBtn.focus(); }
  });
  on(input, 'blur', () => { if (!input.value) setTimeout(() => { if (!input.value && document.activeElement !== input) openSearch(false); }, 120); });

  // ---------- grid ----------
  function matches(part, terms) {
    const hay = `${part.name} ${part.value || ''} ${part.footprint} ${part.key}`.toLowerCase();
    return terms.every((t) => hay.includes(t));
  }
  function visibleParts() {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length) return catalog.parts.filter((p) => matches(p, terms));
    if (family === '__all') return catalog.parts;
    return catalog.parts.filter((p) => p.family === family);
  }
  function makeTile(part) {
    const t = el('div', 'pp-tile');
    t.setAttribute('role', 'listitem');
    t.tabIndex = 0;
    t.dataset.key = part.key;
    const label = part.value && !part.name.includes(part.value) ? `${part.name} · ${part.value}` : part.name;
    t.title = `${label}\n${part.footprint.split(':').pop()}`;
    t.setAttribute('aria-label', `${label}. Press Enter to place.`);
    const art = el('div', 'pp-art', '<i class="pp-sk" aria-hidden="true"></i>');
    const name = el('div', 'pp-name');
    name.textContent = part.name;
    t.append(art, name);
    partOf.set(t, part);
    return t;
  }
  function render() {
    if (io) io.disconnect();
    const list = visibleParts();
    const q = query.trim();
    note.hidden = !q;
    if (q) note.textContent = `${list.length} result${list.length === 1 ? '' : 's'} for “${q}”`;
    if (!list.length) {
      grid.replaceChildren();
      const empty = el('div', 'pp-empty');
      empty.textContent = q ? 'No parts match that. Try a shorter word, like “led” or “usb”.' : 'No parts here yet.';
      grid.appendChild(empty);
      return;
    }
    const frag = document.createDocumentFragment();
    const tiles = list.map(makeTile);
    tiles.forEach((t) => frag.appendChild(t));
    grid.replaceChildren(frag);
    queue.length = 0;
    for (const t of tiles) {
      const part = partOf.get(t);
      if (!part.thumb) noImage(t.querySelector('.pp-art'));
      else if (decoded.has(thumbUrl(part))) loadImg(t); // already decoded: no skeleton flash
      else if (io) io.observe(t); else enqueue(t);
    }
  }

  // ---------- drag (pointer events) ----------
  function clearTile(t) { t.removeAttribute('data-press'); t.removeAttribute('data-lift'); }
  on(grid, 'pointerdown', (e) => {
    if (e.button !== 0 || drag) return;
    const tile = e.target.closest('.pp-tile');
    if (!tile) return;
    e.preventDefault();
    tile.focus({ preventScroll: true });
    tile.setAttribute('data-press', '');
    drag = { tile, part: partOf.get(tile), id: e.pointerId, x: e.clientX, y: e.clientY, started: false };
    try { tile.setPointerCapture(e.pointerId); } catch { /* capture can fail on synthetic events */ }
  });
  on(grid, 'pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id || drag.started) return;
    if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < DRAG_THRESHOLD) return;
    drag.started = true;
    drag.tile.removeAttribute('data-press');
    drag.tile.setAttribute('data-lift', '');
    onDragStart?.(drag.part, e);
  });
  function endDrag(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const d = drag;
    drag = null;
    clearTile(d.tile);
    try { d.tile.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    if (d.started) onDragEnd?.(d.part, e);
  }
  on(grid, 'pointerup', endDrag);
  on(grid, 'pointercancel', endDrag);
  on(grid, 'lostpointercapture', (e) => { if (drag && drag.id === e.pointerId) endDrag(e); });
  on(grid, 'keydown', (e) => {
    const tile = e.target.closest('.pp-tile');
    if (!tile) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      const part = partOf.get(tile);
      tile.setAttribute('data-press', '');
      setTimeout(() => tile.removeAttribute('data-press'), 140);
      onDragStart?.(part, { keyboard: true, target: tile });
      return;
    }
    const tiles = [...grid.querySelectorAll('.pp-tile')];
    const i = tiles.indexOf(tile);
    const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 2, ArrowUp: -2 }[e.key];
    if (step != null && tiles[i + step]) { e.preventDefault(); tiles[i + step].focus(); }
  });
  on(grid, 'dragstart', (e) => e.preventDefault());

  // ---------- load ----------
  loadCatalog().then((c) => {
    if (disposed) return;
    catalog = c;
    root.removeAttribute('data-loading');
    root.removeAttribute('aria-busy');
    renderMenu();
    const first = c.families[0]?.id || '__all';
    prefetch(first === '__all' ? c.parts : c.parts.filter((p) => p.family === first));
    setFamily(first);
    if (pendingFilter != null) api.filter(pendingFilter);
  }).catch(() => {
    if (disposed) return;
    root.removeAttribute('data-loading');
    root.removeAttribute('aria-busy');
    ddLabel.textContent = 'Parts';
    ddCount.textContent = '';
    grid.innerHTML = '<div class="pp-empty">Could not load the parts list. Reload the page to try again.</div>';
  });

  let pendingFilter = null;
  const api = {
    filter(text) {
      const v = String(text ?? '');
      if (!catalog.parts.length) { pendingFilter = v; return; }
      pendingFilter = null;
      input.value = v;
      query = v;
      openSearch(!!v, { focus: false });
      scroller.scrollTop = 0;
      render();
    },
    dispose() {
      disposed = true;
      ac.abort();
      if (io) io.disconnect();
      drag = null;
      root.remove();
    },
  };
  return api;
}

preSkeleton();

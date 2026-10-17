// "How Etch works": a round "?" button (bottom right, clear of every other control) that melts open into a large
// panel with an interactive diagram of the whole stack: you, the static site, the tunnel, the backend on the Mac,
// the agents, the KiCad engine, voice, and the offline library pipeline.
//
//   <script type="module" src="js/ui/stack-help.js"></script>    (self-mounting, nothing else to wire)
//
// Diagram: inline SVG copper traces (gold, rounded right angles, pulses in the direction data flows) under HTML node
// cards on a faint workplane grid. Hover lights a node's traces, click expands it into a small card and lights its
// path, the tabs replay a request or a library build as a glowing packet. Drag pans, wheel / pinch zooms,
// double-click resets. Phones get a vertical stack that scrolls. All motion runs on spring.js (one shared rAF loop
// that stops when everything settles); prefers-reduced-motion shows a static diagram with fades.
import { spring, tick, reducedMotion, clamp, lerp } from "./spring.js";
import { gooLayer, spawnBlob, squish, wobble, liquidTabs } from "./goo.js";
import { createEyes } from "./eyes.js";

const NS = "http://www.w3.org/2000/svg";
const INK = "#16191d", GREEN = "#1a7f4b", GOLD = "#d9b45a", BLUE = "#2f6bff";

// ---------------------------------------------------------------- the stack (checked against the READMEs)
const W = 1420, H = 790, NW = 164, NH = 52;
const GROUPS = [
  { id: "you", label: "You", x: 28, y: 172, w: 144, h: 176, col: 0, tint: INK },
  { id: "browser", label: "Browser · Vercel", x: 210, y: 84, w: 202, h: 390, col: 1, tint: BLUE },
  { id: "tunnel", label: "Tunnel", x: 458, y: 189, w: 188, h: 100, col: 2, tint: INK },
  { id: "backend", label: "Backend · Mac", x: 688, y: 84, w: 188, h: 312, col: 3, tint: GREEN },
  { id: "agents", label: "Agents", x: 928, y: 84, w: 188, h: 312, col: 4, tint: GOLD },
  { id: "voice", label: "Voice", x: 928, y: 434, w: 188, h: 172, col: 4, tint: BLUE },
  { id: "engine", label: "Engine · KiCad 10", x: 1168, y: 84, w: 188, h: 452, col: 5, tint: GREEN },
  { id: "library", label: "Library pipeline · offline", x: 458, y: 660, w: 898, h: 100, col: 6, tint: GOLD },
];
// [id, group, x, y, label, subtitle, bullets]
const NODES = [
  ["you", "you", 40, 204, "You", "voice + text", ["Type or talk to Etch", "Describe a board, or point at a part"]],
  ["dock", "browser", 230, 120, "Agent dock", "chat pill", ["Streams every agent step", "Polls the build job", "js/ui/dock.js"]],
  ["bvoice", "browser", 230, 190, "Voice", "mic + speaker", ["Records you, plays replies", "Space to talk", "js/voice.js"]],
  ["editor", "browser", 230, 260, "Board editor", "board.html", ["Parts, layout, changes", "Vanilla ES modules, no build", "js/editor/"]],
  ["viewer", "browser", 230, 330, "3D viewer", "three.js", ["Plays the build part by part", "Loads the board GLB", "js/board-viewer.js"]],
  ["explore", "browser", 230, 400, "Explore library", "explore.html", ["Open-source KiCad boards", "Static files from world/"]],
  ["tunnel", "tunnel", 470, 225, "Cloudflare tunnel", "quick tunnel", ["Public URL to the Mac", "*.trycloudflare.com", "deploy/tunnel.sh"]],
  ["api", "backend", 700, 120, "etch_public.py", "Python API", ["Every /etch/* route, port 3439", "Jobs polled via /etch/job", "deploy/etch_public.py"]],
  ["queue", "backend", 700, 190, "Build queue", "one run at a time", ["Small queue, per-IP limits", "Daily run cap"]],
  ["routes", "backend", 700, 330, "Board routes", "explore.py", ["Library board views", "Changes via /etch/change", "deploy/explore.py"]],
  ["lead", "agents", 940, 120, "Lead + sub-agents", "Nemotron · OpenAI", ["Write and repair board specs", "Nemotron on Crusoe, OpenAI fallback", "agent/lead.py"]],
  ["classifier", "agents", 940, 190, "Part classifier", "e5-small, local", ["Which part families an ask needs", "Embeddings + logistic regression", "agent/classifier.py"]],
  ["picker", "agents", 940, 260, "Part picker", "parts + wiring", ["Picks real parts from the catalog", "Wires pins, sets values", "agent/live.py"]],
  ["scout", "agents", 940, 330, "Part scout", "missing parts", ["Searches LCSC, GitHub, You.com", "Only real files, never generated", "agent/parts_live.py"]],
  ["stt", "voice", 940, 470, "Parakeet", "speech-to-text, local", ["Transcribes your voice", "sherpa-onnx, port 3421", "studio/stt/"]],
  ["tts", "voice", 940, 540, "OpenAI speech", "cedar voice", ["Speaks the agent's replies", "Via /etch/tts"]],
  ["placer", "engine", 1180, 120, "Placer + router", "KiCad 10", ["Greedy placer, A* grid router", "Runs in KiCad's own Python", "engine/kicad_side/"]],
  ["checks", "engine", 1180, 190, "ERC / DRC", "kicad-cli", ["KiCad's own checks", "Schematic parity on DRC"]],
  ["sim", "engine", 1180, 260, "ngspice sim", "transient", ["Simulates the same netlist", "Writes sim.json"]],
  ["glb", "engine", 1180, 330, "3D export", "kicad-cli GLB", ["Board, bare board, tracks", "One GLB per part", "engine/etchlib/glb.py"]],
  ["inplace", "engine", 1180, 470, "In-place edits", "library boards", ["Edits the author's own project", "Keeps their layout", "agent/import_edit.py"]],
  ["crawl", "library", 1180, 696, "GitHub crawl", "licensed repos", ["Finds KiCad boards on GitHub", "Allow-listed licences only", "library/crawl.py"]],
  ["fetch", "library", 940, 696, "Fetch", "pinned commit", ["Board files + every 3D model", "library/fetch.py"]],
  ["prerender", "library", 700, 696, "Prerender", "KiCad renders + GLB", ["Thumb, GLB and parts per board", "library/render.py"]],
  ["world", "library", 470, 696, "world/", "static files", ["Committed, served by Vercel", "deploy/etch-app/world/"]],
].map(([id, g, x, y, label, sub, info]) => ({ id, g, x, y, w: id === "you" ? 120 : NW, h: id === "you" ? 128 : NH, label, sub, info }));
const NODE = Object.fromEntries(NODES.map((n) => [n.id, n]));

// [id, from, to, points, flags]  both: data goes both ways; ret: the result coming back
const EDGES = [
  ["e1", "you", "dock", [[160, 236], [196, 236], [196, 146], [230, 146]]],
  ["e2", "you", "bvoice", [[160, 276], [206, 276], [206, 216], [230, 216]]],
  ["e3", "dock", "tunnel", [[394, 146], [452, 146], [452, 235], [470, 235]]],
  ["e4", "bvoice", "tunnel", [[394, 216], [440, 216], [440, 245], [470, 245]]],
  ["e5", "editor", "tunnel", [[394, 286], [416, 286], [416, 255], [470, 255]]],
  ["e6", "tunnel", "viewer", [[470, 265], [440, 265], [440, 356], [394, 356]], { ret: 1 }],
  ["e7", "explore", "editor", [[230, 426], [220, 426], [220, 286], [230, 286]]],
  ["e8", "tunnel", "api", [[634, 240], [668, 240], [668, 140], [700, 140]], { both: 1 }],
  ["e9", "tunnel", "routes", [[634, 262], [680, 262], [680, 356], [700, 356]]],
  ["e10", "api", "queue", [[784, 172], [784, 190]]],
  ["e11", "queue", "lead", [[864, 216], [896, 216], [896, 146], [940, 146]]],
  ["e12", "api", "stt", [[864, 126], [922, 126], [922, 496], [940, 496]], { both: 1 }],
  ["e13", "api", "tts", [[864, 138], [910, 138], [910, 566], [940, 566]]],
  ["e14", "lead", "classifier", [[1022, 172], [1022, 190]]],
  ["e15", "classifier", "picker", [[1022, 242], [1022, 260]]],
  ["e16", "picker", "scout", [[1022, 312], [1022, 330]], { both: 1 }],
  ["e17", "picker", "placer", [[1104, 286], [1140, 286], [1140, 146], [1180, 146]]],
  ["e18", "placer", "checks", [[1262, 172], [1262, 190]]],
  ["e19", "checks", "sim", [[1262, 242], [1262, 260]]],
  ["e20", "sim", "glb", [[1262, 312], [1262, 330]]],
  ["e21", "inplace", "checks", [[1180, 496], [1158, 496], [1158, 216], [1180, 216]]],
  ["e22", "routes", "inplace", [[784, 382], [784, 628], [1262, 628], [1262, 522]]],
  ["e23", "glb", "api", [[1344, 356], [1384, 356], [1384, 56], [846, 56], [846, 120]], { ret: 1 }],
  ["e24", "crawl", "fetch", [[1180, 722], [1104, 722]]],
  ["e25", "fetch", "prerender", [[940, 722], [864, 722]]],
  ["e26", "prerender", "world", [[700, 722], [634, 722]]],
  ["e27", "world", "explore", [[470, 722], [312, 722], [312, 452]]],
].map(([id, from, to, pts, f = {}]) => ({ id, from, to, pts, both: !!f.both, ret: !!f.ret }));
const EDGE = Object.fromEntries(EDGES.map((e) => [e.id, e]));

// the packet's routes: [edge, reversed]
const ROUTES = {
  request: [["e1"], ["e3"], ["e8"], ["e10"], ["e11"], ["e14"], ["e15"], ["e17"], ["e18"], ["e19"], ["e20"], ["e23"], ["e8", 1], ["e6"]],
  library: [["e24"], ["e25"], ["e26"], ["e27"]],
};
// phones: the groups in reading order, with the flow between neighbours
const STACK = ["you", "browser", "tunnel", "backend", "agents", "engine", "voice", "library"];
const STACK_NOTE = { voice: "with the API", library: "feeds Explore" };

// ---------------------------------------------------------------- styles
const CSS = `
.sh-fab { position: fixed; right: 24px; bottom: 24px; width: 48px; height: 48px; z-index: 55; translate: 0 0; transform-origin: 50% 60%; }
.sh-fab.sh-in-stage { position: absolute; z-index: 12; }
.sh-fab.sh-in-stage.sh-over-nb { z-index: 21; }
.sh-fab.sh-away { opacity: 0; pointer-events: none; }
.sh-fab-goo { position: absolute; left: -24px; top: -24px; width: 96px; height: 96px; pointer-events: none; }
.sh-fab-goo i { position: absolute; left: 24px; top: 24px; width: 48px; height: 48px; border-radius: 50%; background: #fff; will-change: transform; }
.sh-fab-goo i.d { left: 42px; top: 42px; width: 12px; height: 12px; }
.sh-btn { position: absolute; inset: 0; border: 0; border-radius: 50%; background: none; cursor: pointer; padding: 0; display: grid; place-items: center;
  font: 700 22px/1 "Averia Serif Libre", Georgia, serif; color: ${INK}; -webkit-tap-highlight-color: transparent; }
.sh-btn:focus-visible { outline: 2px solid ${BLUE}; outline-offset: 3px; }
.sh-btn span { display: block; translate: 0 1px; }
@media (max-width: 560px) { .sh-fab:not(.sh-in-stage) { right: 16px; bottom: 16px; } }

.sh-ov { position: fixed; inset: 0; z-index: 1000; font: 14px/1.4 "Figtree", system-ui, -apple-system, "Segoe UI", sans-serif; color: ${INK}; -webkit-font-smoothing: antialiased; }
.sh-ov *, .sh-ov *::before, .sh-ov *::after { box-sizing: border-box; }
.sh-dim { position: absolute; inset: 0; touch-action: none; background: rgba(22, 25, 29, .26); -webkit-backdrop-filter: blur(3px) saturate(1.1); backdrop-filter: blur(3px) saturate(1.1); opacity: 0; }
.sh-goo { position: absolute; left: 0; top: 0; transform-origin: 0 0; pointer-events: none; }
.sh-goo .lq-blob, .sh-goo .sh-blob { position: absolute; left: 0; top: 0; background: #fff; will-change: transform; }
.sh-pan { position: absolute; left: 50%; top: 50%; width: min(1240px, calc(100vw - 48px)); height: min(820px, calc(100vh - 48px)); translate: -50% -50%;
  border-radius: 22px; display: flex; flex-direction: column; overflow: hidden; background: transparent; }
.sh-pan.sh-solid { background: #fff; box-shadow: 0 2px 6px rgba(22, 25, 29, .08), 0 24px 60px rgba(22, 25, 29, .2); }
.sh-pan-in { display: flex; flex-direction: column; flex: 1; min-height: 0; opacity: 0; }
.sh-hd { display: flex; align-items: center; gap: 16px; padding: 16px 16px 12px 24px; flex: none; }
.sh-hd h2 { margin: 0; font: 700 24px/1.1 "Averia Serif Libre", Georgia, serif; letter-spacing: -.01em; }
.sh-hd p { margin: 3px 0 0; color: #5a6672; font-size: 13.5px; }
.sh-hd .sh-grow { flex: 1; }
.sh-x { flex: none; width: 40px; height: 40px; border: 0; border-radius: 50%; background: #f1f3f6; color: #4a545e; font: 400 22px/1 system-ui; cursor: pointer; display: grid; place-items: center; transition: background-color .15s; }
.sh-x:hover { background: #e6e9ee; }
.sh-x:focus-visible, .sh-ov .lq-tabs [data-value]:focus-visible, .sh-n:focus-visible { outline: 2px solid ${BLUE}; outline-offset: 2px; }
.sh-ov .lq-tabs { position: relative; display: inline-flex; gap: 2px; padding: 4px; border-radius: 999px; background: #f1f3f6; isolation: isolate; flex: none; }
.sh-ov .lq-tabs [data-value] { position: relative; z-index: 1; border: 0; background: none; cursor: pointer; border-radius: 999px; padding: 8px 14px; font: 600 13px/1.1 "Figtree", system-ui, sans-serif; color: #5a6672; transition: color .2s; white-space: nowrap; }
.sh-ov .lq-tabs [data-value]:hover { color: #2b303b; }
.sh-ov .lq-tabs [data-value].on { color: ${INK}; }
.sh-ov .lq-tabs-ind { position: absolute; inset: 0; pointer-events: none; z-index: 0; filter: drop-shadow(0 1px 2px rgba(20, 25, 40, .14)); }
.sh-ov .lq-tabs-ind > i { position: absolute; left: 0; top: 0; background: #fff; will-change: transform; }
.sh-ov .lq-tabs-ind .lq-cap { border-radius: 50%; }
.sh-ov .lq-tabs-ind .lq-mid { width: 100px; transform-origin: 0 0; }
.sh-ov .lq-tabs [data-value] i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; background: currentColor; opacity: .35; vertical-align: 1px; }
.sh-ov .lq-tabs [data-value].on i { background: ${GOLD}; opacity: 1; }

.sh-vp { position: relative; flex: 1; min-height: 0; margin: 0 12px 12px; border-radius: 16px; overflow: hidden; background: #f5f8fb; touch-action: none; cursor: grab; user-select: none; -webkit-user-select: none; }
.sh-vp.sh-drag { cursor: grabbing; }
.sh-world { position: absolute; left: 0; top: 0; width: ${W}px; height: ${H}px; transform-origin: 0 0; will-change: transform; }
.sh-world > svg { position: absolute; left: 0; top: 0; overflow: visible; }
.sh-grid { position: absolute; left: -${W}px; top: -${H}px; width: ${W * 3}px; height: ${H * 3}px; pointer-events: none;
  background-image: linear-gradient(rgba(72, 152, 204, .2) 1px, transparent 1px), linear-gradient(90deg, rgba(72, 152, 204, .2) 1px, transparent 1px),
    linear-gradient(rgba(72, 152, 204, .09) 1px, transparent 1px), linear-gradient(90deg, rgba(72, 152, 204, .09) 1px, transparent 1px);
  background-size: 40px 40px, 40px 40px, 8px 8px, 8px 8px; background-position: -1px -1px; }
.sh-hint { position: absolute; left: 12px; bottom: 10px; font-size: 12px; color: #5a6672; background: rgba(255, 255, 255, .8); padding: 4px 10px; border-radius: 999px; pointer-events: none; }

.sh-g { position: absolute; border-radius: 18px; background: rgba(255, 255, 255, .55); box-shadow: inset 0 0 0 1px rgba(22, 25, 29, .07); pointer-events: none; }
.sh-g b { position: absolute; left: 14px; top: 10px; font: 600 10.5px/1 "Figtree", system-ui, sans-serif; letter-spacing: .08em; text-transform: uppercase; color: #5a6672; white-space: nowrap; }
.sh-g b::before { content: ""; display: inline-block; width: 6px; height: 6px; border-radius: 2px; margin-right: 6px; vertical-align: 1px; background: var(--t); }

.sh-n { position: absolute; display: flex; flex-direction: column; justify-content: center; gap: 2px; padding: 0 12px 0 24px; border: 0; border-radius: 12px; background: #fff; color: ${INK}; text-align: left; cursor: pointer;
  box-shadow: 0 1px 2px rgba(22, 25, 29, .08), 0 3px 10px rgba(22, 25, 29, .05), inset 0 0 0 1px rgba(22, 25, 29, .06); transition: opacity .25s, box-shadow .25s; font: inherit; }
.sh-n::before { content: ""; position: absolute; left: 11px; top: 50%; width: 6px; height: 6px; margin-top: -3px; border-radius: 50%; background: var(--t); }
.sh-n b { font: 600 13.5px/1.15 "Figtree", system-ui, sans-serif; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sh-n span { font-size: 11.5px; color: #5a6672; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sh-n.up { box-shadow: 0 2px 4px rgba(22, 25, 29, .08), 0 12px 26px rgba(47, 107, 255, .16), inset 0 0 0 1px rgba(47, 107, 255, .25); }
.sh-n.sel { box-shadow: 0 0 0 2px ${GOLD}, 0 10px 24px rgba(217, 180, 90, .28); }
.sh-n.dim { opacity: .32; }
.sh-n.lit { box-shadow: 0 0 0 2px ${GOLD}, 0 6px 18px rgba(217, 180, 90, .32); }
.sh-n.ping { animation: sh-ping .7s ease-out; }
@keyframes sh-ping { 0% { box-shadow: 0 0 0 0 rgba(217, 180, 90, .9), 0 0 0 2px ${GOLD}; } 100% { box-shadow: 0 0 0 16px rgba(217, 180, 90, 0), 0 0 0 2px ${GOLD}; } }
.sh-n.sh-you { align-items: center; justify-content: flex-end; padding: 0 0 14px; text-align: center; }
.sh-n.sh-you::before { display: none; }
.sh-eyes { position: absolute; left: 50%; top: 14px; width: 74px; height: 58px; translate: -50% 0; pointer-events: none; }
.sh-eyes .ag-eyes { display: block; width: 100%; height: 100%; overflow: visible; transform-origin: 50% 85%; }
.sh-eyes .ag-eyes .ag-eye { fill: #f6f8fc; }
.sh-eyes .ag-eyes .ag-eyes-dots circle { fill: ${INK}; }

.sh-tr path { fill: none; stroke-linecap: round; stroke-linejoin: round; }
.sh-tr .h { stroke: #f5f8fb; stroke-width: 8; }
.sh-tr .b { stroke: ${GOLD}; stroke-width: 2.6; opacity: .6; transition: opacity .25s, stroke-width .25s; }
.sh-tr .p { stroke: #fff3c4; stroke-width: 2.2; stroke-dasharray: 3 27; opacity: 0; transition: opacity .3s; }
.sh-tr .pad { fill: ${GOLD}; stroke: #b48f3a; stroke-width: 1; transition: opacity .25s; }
.sh-live .sh-tr .p { opacity: .95; animation: sh-flow 1.6s linear infinite; }
.sh-live .sh-tr .p.r { animation-direction: reverse; }
.sh-tr.hot .b, .sh-tr.lit .b { opacity: 1; stroke-width: 3.2; }
.sh-live .sh-tr.hot .p { animation-duration: .55s; stroke: #fffbe8; }
.sh-tr.dim .b { opacity: .16; }
.sh-tr.dim .p, .sh-tr.dim .pad { opacity: .12; }
@keyframes sh-flow { from { stroke-dashoffset: 30; } to { stroke-dashoffset: 0; } }
.sh-pk { pointer-events: none; }

.sh-card { position: absolute; z-index: 5; border-radius: 14px; background: #fff; overflow: hidden; cursor: default; user-select: text; -webkit-user-select: text;
  box-shadow: 0 0 0 2px ${GOLD}, 0 18px 40px rgba(22, 25, 29, .18); }
.sh-card-in { width: 248px; padding: 12px 14px 12px 16px; opacity: 0; }
.sh-card-in b { display: block; font: 700 16px/1.2 "Averia Serif Libre", Georgia, serif; }
.sh-card-in > span { display: block; font-size: 12px; color: #5a6672; margin-top: 1px; }
.sh-card-in ul { margin: 8px 0 0; padding: 0; list-style: none; }
.sh-card-in li { position: relative; padding-left: 13px; font-size: 13px; line-height: 1.35; margin-top: 4px; color: #2b303b; }
.sh-card-in li::before { content: ""; position: absolute; left: 0; top: .5em; width: 5px; height: 5px; border-radius: 50%; background: ${GOLD}; }
.sh-card-in li.f { font: 500 12px/1.35 ui-monospace, "SF Mono", Menlo, monospace; color: ${GREEN}; }

/* phones: a vertical stack that scrolls inside the panel */
.sh-stk { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; margin: 0 8px 8px; border-radius: 16px; position: relative; padding: 14px 12px 24px; background: #f5f8fb; }
.sh-stk { background-image: linear-gradient(rgba(72, 152, 204, .2) 1px, transparent 1px), linear-gradient(90deg, rgba(72, 152, 204, .2) 1px, transparent 1px),
    linear-gradient(rgba(72, 152, 204, .09) 1px, transparent 1px), linear-gradient(90deg, rgba(72, 152, 204, .09) 1px, transparent 1px);
  background-size: 40px 40px, 40px 40px, 8px 8px, 8px 8px; background-position: -1px -1px; background-attachment: local; }
.sh-sg { position: relative; border-radius: 18px; background: rgba(255, 255, 255, .7); box-shadow: inset 0 0 0 1px rgba(22, 25, 29, .07); padding: 30px 10px 10px; }
.sh-sg > b { position: absolute; left: 14px; top: 11px; font: 600 10.5px/1 "Figtree", system-ui, sans-serif; letter-spacing: .08em; text-transform: uppercase; color: #5a6672; }
.sh-sg > b::before { content: ""; display: inline-block; width: 6px; height: 6px; border-radius: 2px; margin-right: 6px; vertical-align: 1px; background: var(--t); }
.sh-sg > b em { font-style: normal; text-transform: none; letter-spacing: 0; color: #8a949e; margin-left: 6px; }
.sh-sgn { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.sh-stk .sh-n { position: relative; height: 52px; width: auto; min-width: 0; }
.sh-stk .sh-n.sh-you { grid-column: 1 / -1; height: 124px; }
.sh-ov .sh-s { display: none; }
.sh-stk .sh-card { position: relative; grid-column: 1 / -1; z-index: 1; }
.sh-stk .sh-card-in { width: auto; }
.sh-lk { position: relative; height: 26px; }
.sh-lk svg { position: absolute; left: 50%; top: 0; width: 12px; height: 26px; margin-left: -6px; overflow: visible; }
.sh-lk.gap { height: 14px; }
.sh-pkd { position: absolute; left: 0; top: 0; width: 18px; height: 18px; margin: -9px 0 0 -9px; border-radius: 50%; pointer-events: none; z-index: 6; opacity: 0;
  background: radial-gradient(circle, #fffbe8 0 28%, rgba(217, 180, 90, .9) 40%, rgba(217, 180, 90, 0) 72%); }

@media (max-width: 700px) {
  .sh-pan { width: calc(100vw - 16px); height: calc(100dvh - 16px); border-radius: 22px; }
  .sh-hd { flex-wrap: wrap; gap: 10px 12px; padding: 14px 12px 10px 16px; }
  .sh-hd h2 { font-size: 21px; }
  .sh-hd .sh-grow { display: none; }
  .sh-hd > div:first-child { flex: 1; min-width: 0; }
  .sh-ov .lq-tabs { order: 3; width: 100%; overflow-x: auto; scrollbar-width: none; }
  .sh-ov .lq-tabs [data-value] { padding: 8px 11px; font-size: 12.5px; }
  .sh-ov .sh-l { display: none; }
  .sh-ov .sh-s { display: inline; }
}
.sh-rm .sh-g, .sh-rm .sh-n, .sh-rm .sh-tr, .sh-rm .sh-pan-in, .sh-rm .sh-dim, .sh-rm .sh-card-in { transition: opacity .28s ease !important; }
@media (prefers-reduced-motion: reduce) {
  .sh-live .sh-tr .p { animation: none; opacity: 0; }
  .sh-n.ping { animation: none; }
}
`;

// ---------------------------------------------------------------- helpers
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const sv = (tag, attrs = {}) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const isTyping = () => { const a = document.activeElement; return !!a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT" || a.isContentEditable); };
const phone = () => matchMedia("(max-width: 700px)").matches;
const tintOf = (gid) => (GROUPS.find((g) => g.id === gid) || {}).tint || INK;

// rounded right-angle path through orthogonal points
function roundPath(pts, r = 11) {
  let d = `M${pts[0][0]} ${pts[0][1]}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const [px, py] = pts[i - 1], [cx, cy] = pts[i], [nx, ny] = pts[i + 1];
    const l1 = Math.hypot(cx - px, cy - py), l2 = Math.hypot(nx - cx, ny - cy);
    const rr = Math.min(r, l1 / 2, l2 / 2);
    const ax = cx - ((cx - px) / l1) * rr, ay = cy - ((cy - py) / l1) * rr;
    const bx = cx + ((nx - cx) / l2) * rr, by = cy + ((ny - cy) / l2) * rr;
    d += ` L${ax} ${ay} Q${cx} ${cy} ${bx} ${by}`;
  }
  const z = pts[pts.length - 1];
  return d + ` L${z[0]} ${z[1]}`;
}

// what a node touches, and its whole path through the stack
function neighbours(id) {
  const nodes = new Set([id]), edges = new Set();
  for (const e of EDGES) if (e.from === id || e.to === id) { edges.add(e.id); nodes.add(e.from); nodes.add(e.to); }
  return { nodes, edges };
}
function pathOf(id) {
  const nodes = new Set([id]), edges = new Set();
  const walk = (start, fwd) => {
    const q = [start];
    while (q.length) {
      const n = q.shift();
      for (const e of EDGES) {
        if (e.ret) continue;
        const a = fwd ? e.from : e.to, b = fwd ? e.to : e.from;
        if (a !== n) continue;
        edges.add(e.id);
        if (!nodes.has(b)) { nodes.add(b); q.push(b); }
      }
    }
  };
  walk(id, true); walk(id, false);
  // anything that ends in the engine's 3D export comes back to the 3D viewer
  if (nodes.has("glb") || id === "viewer") {
    if (id === "viewer") { walk("glb", false); nodes.add("glb"); }
    for (const e of ["e23", "e8", "e6"]) { edges.add(e); nodes.add(EDGE[e].from); nodes.add(EDGE[e].to); }
  }
  return { nodes, edges };
}

// ---------------------------------------------------------------- the button
let css = null, fab = null, btn = null, panel = null;
function injectCss() {
  if (css) return;
  css = el("style"); css.id = "sh-css"; css.textContent = CSS; document.head.append(css);
}

function mountButton() {
  injectCss();
  fab = el("div", "sh-fab");
  const goo = el("div", "sh-fab-goo");
  goo.setAttribute("aria-hidden", "true");
  const core = el("i"), drops = [0, 1, 2].map(() => el("i", "d"));
  goo.append(core, ...drops);
  gooLayer(goo, { id: "sh-goo-fab", blur: 4, alpha: 20, shift: -8, after: "drop-shadow(0 6px 10px rgba(22, 25, 29, .16)) drop-shadow(0 1px 2px rgba(22, 25, 29, .14))" });
  btn = el("button", "sh-btn", "<span aria-hidden=\"true\">?</span>");
  btn.type = "button";
  btn.setAttribute("aria-label", "How Etch works");
  btn.setAttribute("aria-haspopup", "dialog");
  btn.setAttribute("aria-keyshortcuts", "Shift+?");
  btn.title = "How Etch works (?)";
  fab.append(goo, btn);

  // hover: the button lifts and swells, three droplets bud out of its top edge and melt back on leave
  const ANG = [-150, -90, -30].map((a) => (a * Math.PI) / 180);
  const hv = spring({ lift: 0, swell: 1, out: 0 }, {
    preset: { stiffness: 300, damping: 17 },
    onUpdate: (v) => {
      fab.style.translate = `0 ${-v.lift}px`;
      core.style.transform = `scale(${v.swell})`;
      drops.forEach((d, i) => {
        const r = v.out * (21 + i % 2 * 3);
        d.style.transform = `translate(${Math.cos(ANG[i]) * r}px, ${Math.sin(ANG[i]) * r}px) scale(${0.4 + v.out * 0.6})`;
      });
    },
  });
  hv.jump({ lift: 0, swell: 1, out: 0 });
  const on = () => hv.set({ lift: 3, swell: 1.07, out: 1 });
  const off = () => hv.set({ lift: 0, swell: 1, out: 0 });
  btn.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") on(); });
  btn.addEventListener("pointerleave", off);
  btn.addEventListener("focus", () => { if (btn.matches(":focus-visible")) on(); });
  btn.addEventListener("blur", off);
  squish(fab);
  btn.addEventListener("click", () => { off(); open(); });
  // inside the board's 3D stage: presses on the button never reach the orbit controls or the selection
  for (const t of ["pointerdown", "click", "dblclick", "contextmenu"]) fab.addEventListener(t, (e) => e.stopPropagation());
  fab.addEventListener("wheel", (e) => e.stopPropagation(), { passive: true });

  placeButton();
  const re = () => requestAnimationFrame(placeButton);
  addEventListener("resize", re);
  if (typeof ResizeObserver === "function") {
    const ro = new ResizeObserver(re);
    const watch = () => { for (const s of ["#stage", "#br", ".jp"]) { const n = document.querySelector(s); if (n && !n.dataset.shWatched) { n.dataset.shWatched = "1"; ro.observe(n); } } };
    watch();
    const mo = new MutationObserver(() => { watch(); re(); });
    mo.observe(document.body, { childList: true });
    const st = document.getElementById("stage");
    if (st) mo.observe(st, { childList: true });
  }
  // the job pill and the dock slide in late; check again once they have settled
  setTimeout(placeButton, 900); setTimeout(placeButton, 2500);
}

// Bottom right, clear of everything else. On the board editor that corner belongs to the parts panel and the
// Settings / Snap grid row, so the button sits in the 3D stage just above that row (or below it on phones, where
// the row moves to the top right). It is lifted over the job pill or the dock when one of them reaches it.
const overlaps = (a, b, pad = 8) => a.left < b.right + pad && a.right > b.left - pad && a.top < b.bottom + pad && a.bottom > b.top - pad;
function placeButton() {
  if (!fab) return;
  const stage = document.getElementById("stage"), br = document.getElementById("br");
  if (stage && br && br.offsetParent) {
    if (fab.parentNode !== stage) stage.append(fab);
    fab.classList.add("sh-in-stage");
    const sr = stage.getBoundingClientRect(), r = br.getBoundingClientRect();
    // the new-board start screen covers the stage (and that row): the button goes over it, in the stage's corner
    const nb = stage.querySelector(":scope > .nb");
    const start = !!(nb && nb.getClientRects().length && getComputedStyle(nb).visibility !== "hidden" && +getComputedStyle(nb).opacity > 0.05);
    fab.classList.toggle("sh-over-nb", start);
    const below = r.top + r.height / 2 < sr.top + sr.height / 2;
    // (on phones the row sits at the top, under the start card, so the button drops to the stage's bottom corner)
    let top = start && below ? sr.height - 14 - 48 : below ? r.bottom - sr.top + 12 : r.top - sr.top - 12 - 48;
    const right = Math.max(8, Math.round(sr.right - r.right));
    for (const s of [".jp", ".ag-pill"]) {
      const o = document.querySelector(s);
      if (!o || !o.getClientRects().length) continue;
      const or = o.getBoundingClientRect();
      const me = { left: sr.right - right - 48, right: sr.right - right, top: sr.top + top, bottom: sr.top + top + 48 };
      if (or.width && overlaps(me, or)) top = Math.min(top, or.top - sr.top - 12 - 48);
    }
    Object.assign(fab.style, { top: top + "px", right: right + "px", bottom: "auto" });
    return;
  }
  if (fab.parentNode !== document.body) document.body.append(fab);
  fab.classList.remove("sh-in-stage");
  fab.style.top = ""; fab.style.right = ""; fab.style.bottom = "";
  const me = fab.getBoundingClientRect();
  let bottom = innerWidth <= 560 ? 16 : 24;
  for (const s of [".jp", ".ag-pill"]) {
    const o = document.querySelector(s);
    if (!o || !o.getClientRects().length) continue;
    const or = o.getBoundingClientRect();
    if (or.width && overlaps(me, or)) bottom = Math.max(bottom, Math.round(innerHeight - or.top + 12));
  }
  fab.style.bottom = bottom + "px";
}

// ---------------------------------------------------------------- the panel
function open() {
  if (panel) return;
  panel = buildPanel();
}
function close() { if (panel) panel.close(); }

function buildPanel() {
  const rm = reducedMotion();
  const timers = new Set(), offs = [];
  const later = (f, ms) => { const t = setTimeout(() => { timers.delete(t); f(); }, ms); timers.add(t); return t; };
  const prevFocus = document.activeElement;

  const ov = el("div", "sh-ov" + (rm ? " sh-rm" : ""));
  ov.setAttribute("role", "dialog");
  ov.setAttribute("aria-modal", "true");
  ov.setAttribute("aria-labelledby", "sh-title");
  const dim = el("div", "sh-dim");
  const goo = el("div", "sh-goo");
  const blob = el("div", "sh-blob");
  goo.append(blob);
  const pan = el("section", "sh-pan");
  const inner = el("div", "sh-pan-in");
  inner.innerHTML = `<header class="sh-hd"><div><h2 id="sh-title">How Etch works</h2><p>From your words to a checked board in 3D.</p></div><span class="sh-grow"></span>
    <div class="sh-tabs" aria-label="Show"><button type="button" data-value="overview">Overview</button><button type="button" data-value="request"><i></i><span class="sh-l">Follow a request</span><span class="sh-s">Follow request</span></button><button type="button" data-value="library"><i></i><span class="sh-l">Build a library board</span><span class="sh-s">Library board</span></button></div>
    <button type="button" class="sh-x" aria-label="Close">×</button></header>`;
  pan.append(inner);
  ov.append(dim, goo, pan);
  document.body.append(ov);
  fab.classList.add("sh-away");
  btn.setAttribute("aria-expanded", "true");

  // ---- the diagram (desktop) or the stack (phones) ----
  let dia = null;
  const mountBody = () => {
    if (dia) dia.destroy();
    dia = phone() ? buildStack(inner, { rm, later }) : buildDiagram(inner, { rm, later });
  };
  mountBody();
  const mq = matchMedia("(max-width: 700px)");
  const onMq = () => { mountBody(); dia.build(true); dia.play(tabs.value); };
  mq.addEventListener("change", onMq);
  offs.push(() => mq.removeEventListener("change", onMq));

  const tabs = liquidTabs(inner.querySelector(".sh-tabs"), { value: "overview", onChange: (v) => dia.play(v) });
  offs.push(() => tabs.destroy());

  // ---- the morph: the button's white circle swells into the panel (goo), and reverses on close ----
  // the goo layer renders at half resolution (scaled up 2x): the blur and threshold cost a quarter as much
  gooLayer(goo, { id: "sh-goo-pan", blur: 5, alpha: 22, shift: -10, after: "drop-shadow(0 12px 22px rgba(22, 25, 29, .18))" });
  Object.assign(goo.style, { width: innerWidth / 2 + "px", height: innerHeight / 2 + "px", transform: "scale(2)" });
  const B = () => { const r = btn.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width || 48, h: r.height || 48 }; };
  const P = () => { const r = pan.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; };
  let b0 = B(), p0 = P(), closing = false, done = false;
  const prog = (v) => clamp((v.w - b0.w) / Math.max(1, p0.w - b0.w));
  const draw = (v) => {
    const p = prog(v);
    const rad = lerp(24, 22, clamp(p * 3));
    Object.assign(blob.style, { width: v.w / 2 + "px", height: v.h / 2 + "px", borderRadius: rad / 2 + "px", transform: `translate(${v.x / 2}px, ${v.y / 2}px)` });
    const t = v.y - p0.y, l = v.x - p0.x, r = p0.x + p0.w - (v.x + v.w), bt = p0.y + p0.h - (v.y + v.h);
    pan.style.clipPath = p >= 0.999 ? "" : `inset(${Math.max(0, t)}px ${Math.max(0, r)}px ${Math.max(0, bt)}px ${Math.max(0, l)}px round ${rad}px)`;
    inner.style.opacity = String(clamp((p - 0.55) / 0.4));
    dim.style.opacity = String(clamp(p * 1.4));
  };
  const m = spring({ ...b0 }, { preset: { stiffness: 340, damping: 30 }, precision: 0.3, onUpdate: draw });
  const settleOpen = () => {
    if (closing) return;
    goo.style.display = "none";
    pan.classList.add("sh-solid");
    pan.style.clipPath = "";
    inner.style.opacity = "1";
    dim.style.opacity = "1";
    dia.build();
    inner.querySelector(".sh-x").focus({ preventScroll: true });
  };
  if (rm) {
    goo.style.display = "none";
    pan.classList.add("sh-solid");
    inner.style.opacity = "0"; dim.style.opacity = "0";
    void inner.offsetWidth;
    requestAnimationFrame(() => { inner.style.opacity = "1"; dim.style.opacity = "1"; settleOpen(); });
  } else {
    draw(m.value);
    const drops = [0.3, 0.62].map((f, i) => spawnBlob(goo, {
      from: { x: (b0.x + b0.w / 2) / 2, y: (b0.y + b0.h / 2) / 2 },
      to: { x: lerp(b0.x, p0.x + p0.w * (0.82 - i * 0.2), f) / 2, y: lerp(b0.y, p0.y + p0.h * (0.78 - i * 0.18), f) / 2 },
      w: 14 - i * 3, h: 14 - i * 3,
    }));
    later(() => drops.forEach((d) => d.remove()), 520);
    m.set({ ...p0 });
    // settle as soon as the shape is there (the spring's last fractions of a pixel are invisible)
    const stop = tick(() => {
      if (closing) return false;
      if (!m.moving || (prog(m.value) > 0.995 && Math.abs(m.velocity.w) < 40)) { m.stop(); m.jump({ ...p0 }); settleOpen(); return false; }
      return true;
    });
    offs.push(stop);
  }

  function shut() {
    if (closing) return;
    closing = true;
    dia.stop();
    const finish = () => {
      if (done) return;
      done = true;
      for (const t of timers) clearTimeout(t);
      offs.forEach((f) => f());
      dia.destroy();
      ov.remove();
      removeEventListener("keydown", onKey, true);
      fab.classList.remove("sh-away");
      btn.setAttribute("aria-expanded", "false");
      panel = null;
      if (!rm) wobble(fab, 0.9);
      if (prevFocus === btn || !prevFocus || prevFocus === document.body || !document.contains(prevFocus)) btn.focus({ preventScroll: true });
      else prevFocus.focus({ preventScroll: true });
    };
    if (rm) { inner.style.opacity = "0"; dim.style.opacity = "0"; pan.style.opacity = "0"; pan.style.transition = "opacity .2s"; later(finish, 220); return; }
    b0 = B(); p0 = P();
    fab.classList.add("sh-away");
    pan.classList.remove("sh-solid");
    goo.style.display = "";
    Object.assign(goo.style, { width: innerWidth / 2 + "px", height: innerHeight / 2 + "px" });
    m.jump({ ...p0 });
    m.config({ stiffness: 360, damping: 32 });
    m.set({ ...b0 });
    const d = spawnBlob(goo, { from: { x: (p0.x + p0.w * 0.8) / 2, y: (p0.y + p0.h * 0.8) / 2 }, to: { x: (b0.x + b0.w / 2) / 2, y: (b0.y + b0.h / 2) / 2 }, w: 12, h: 12 });
    later(() => d.remove(), 360);
    const t0 = performance.now();
    tick(() => {
      if (done) return false;
      if (prog(m.value) < 0.02 || !m.moving || performance.now() - t0 > 900) { m.stop(); finish(); return false; }
      return true;
    });
  }

  // ---- closing and keys ----
  inner.querySelector(".sh-x").addEventListener("click", shut);
  dim.addEventListener("pointerdown", shut);
  // the page under the panel does not scroll or zoom while it is open
  ov.addEventListener("wheel", (e) => { if (!e.target.closest(".sh-stk")) e.preventDefault(); }, { passive: false });
  ov.addEventListener("pointerdown", (e) => { if (e.target === ov) shut(); });
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); if (!dia.closeCard()) shut(); return; }
    if (e.key === "?" && !isTyping()) { e.preventDefault(); e.stopImmediatePropagation(); shut(); return; }
    if (e.key === "Tab") {
      const f = Array.from(ov.querySelectorAll("button, [tabindex='0']")).filter((n) => !n.disabled && n.getClientRects().length && n.tabIndex >= 0);
      if (!f.length) return;
      const i = f.indexOf(document.activeElement);
      if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && (i === -1 || i === f.length - 1)) { e.preventDefault(); f[0].focus(); }
    }
  };
  addEventListener("keydown", onKey, true);
  // the page's own shortcuts (delete a part, hold Space to talk) stay out while the panel is open
  for (const t of ["keydown", "keyup"]) ov.addEventListener(t, (e) => e.stopPropagation());
  const onResize = () => { if (!closing && dia.fit) dia.fit(true); };
  addEventListener("resize", onResize);
  offs.push(() => removeEventListener("resize", onResize));

  return { close: shut, get dia() { return dia; }, el: ov };
}

// ---------------------------------------------------------------- diagram (desktop)
function buildDiagram(host, { rm, later }) {
  const vp = el("div", "sh-vp");
  const world = el("div", "sh-world");
  const grid = el("div", "sh-grid");
  const gLayer = el("div"), nLayer = el("div");
  const svg = sv("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, "aria-hidden": "true" });
  svg.innerHTML = `<defs><radialGradient id="sh-pkg"><stop offset="0" stop-color="#fffbe8"/><stop offset=".35" stop-color="#ffe9a6"/><stop offset=".6" stop-color="${GOLD}" stop-opacity=".55"/><stop offset="1" stop-color="${GOLD}" stop-opacity="0"/></radialGradient></defs>`;
  world.append(grid, gLayer, svg, nLayer);
  const hint = el("div", "sh-hint", "Drag to pan · scroll to zoom · double-click to reset");
  vp.append(world, hint);
  host.append(vp);

  // groups
  const gEls = {};
  for (const g of GROUPS) {
    const d = el("div", "sh-g", `<b>${esc(g.label)}</b>`);
    Object.assign(d.style, { left: g.x + "px", top: g.y + "px", width: g.w + "px", height: g.h + "px", opacity: "0" });
    d.style.setProperty("--t", g.tint);
    gLayer.append(d);
    gEls[g.id] = d;
  }
  // traces: halo (so a crossing reads as one trace passing over another), copper, pulses, pads
  const eEls = {};
  for (const e of EDGES) {
    const g = sv("g", { class: "sh-tr" });
    const d = roundPath(e.pts);
    const h = sv("path", { d, class: "h" }), b = sv("path", { d, class: "b" }), p = sv("path", { d, class: "p" });
    g.append(h, b, p);
    if (e.both) g.append(sv("path", { d, class: "p r" }));
    const a = e.pts[0], z = e.pts[e.pts.length - 1];
    g.append(sv("circle", { cx: a[0], cy: a[1], r: 3.2, class: "pad" }), sv("circle", { cx: z[0], cy: z[1], r: 3.2, class: "pad" }));
    svg.append(g);
    const len = b.getTotalLength();
    const pads = Array.from(g.querySelectorAll(".pad"));
    eEls[e.id] = { g, b, h, len, pads };
    if (!rm) { for (const x of [b, h]) { x.style.strokeDasharray = `${len} ${len}`; x.style.strokeDashoffset = String(len); } pads.forEach((c) => { c.style.opacity = "0"; }); }
    else g.style.opacity = "0";
  }
  const pk = sv("g", { class: "sh-pk", opacity: "0" });
  pk.innerHTML = `<circle r="15" fill="url(#sh-pkg)"/><circle r="3.6" fill="#fffdf2"/>`;
  svg.append(pk);

  // nodes
  const nEls = {}, lifts = {};
  let eyes = null;
  for (const n of NODES) {
    const b = el("button", "sh-n" + (n.id === "you" ? " sh-you" : ""), `<b>${esc(n.label)}</b><span>${esc(n.sub)}</span>`);
    b.type = "button";
    b.dataset.id = n.id;
    b.setAttribute("aria-expanded", "false");
    b.setAttribute("aria-label", `${n.label}, ${n.sub}`);
    Object.assign(b.style, { left: n.x + "px", top: n.y + "px", width: n.w + "px", height: n.h + "px", opacity: "0" });
    b.style.setProperty("--t", tintOf(n.g));
    if (n.id === "you") {
      const eh = el("span", "sh-eyes");
      b.prepend(eh);
      eyes = createEyes(eh, { label: "Etch's eyes" });
    }
    nLayer.append(b);
    nEls[n.id] = b;
    lifts[n.id] = spring(0, { preset: { stiffness: 380, damping: 22 }, precision: 0.02, onUpdate: (v) => { b.style.transform = v ? `translateY(${v}px)` : ""; } });
  }

  // ---- view: pan / zoom on one spring ----
  let vw = 0, vh = 0, fitK = 1;
  const view = spring({ x: 0, y: 0, k: 1 }, { preset: { stiffness: 240, damping: 30 }, precision: 0.05, onUpdate: (v) => { world.style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.k})`; } });
  function fit(jump = false) {
    vw = vp.clientWidth; vh = vp.clientHeight;
    if (!vw || !vh) return;
    // the drawn content spans x 20..1410, y 44..770
    const bx = 20, by = 44, bw = 1390, bh = 726;
    fitK = Math.min((vw - 24) / bw, (vh - 20) / bh);
    const to = { k: fitK, x: (vw - bw * fitK) / 2 - bx * fitK, y: (vh - bh * fitK) / 2 - by * fitK };
    jump ? view.jump(to) : view.set(to, { preset: { stiffness: 240, damping: 30 } });
  }
  fit(true);
  const zoomAt = (cx, cy, factor, jump = false) => {
    const t = jump ? view.value : view.target;
    const k = clamp(t.k * factor, fitK * 0.6, Math.max(2.4, fitK * 3));
    const f = k / t.k;
    const to = { k, x: cx - (cx - t.x) * f, y: cy - (cy - t.y) * f };
    jump ? view.jump(to) : view.set(to);
  };
  const local = (e) => { const r = vp.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  const onWheel = (e) => {
    e.preventDefault();
    const [x, y] = local(e);
    const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
    zoomAt(x, y, Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0018)));
  };
  vp.addEventListener("wheel", onWheel, { passive: false });
  const ptrs = new Map();
  let drag = null, moved = false, vel = { x: 0, y: 0 }, lastT = 0, pinch = null;
  vp.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".sh-card")) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    ptrs.set(e.pointerId, local(e));
    moved = false;
    if (ptrs.size === 1) { const v = view.value; drag = { sx: e.clientX, sy: e.clientY, x: v.x, y: v.y, id: e.pointerId }; vel = { x: 0, y: 0 }; lastT = performance.now(); }
    else if (ptrs.size === 2) {
      const [a, b] = Array.from(ptrs.values());
      pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1 };
      drag = null;
    }
  });
  vp.addEventListener("pointermove", (e) => {
    if (!ptrs.has(e.pointerId)) return;
    const prev = ptrs.get(e.pointerId), cur = local(e);
    ptrs.set(e.pointerId, cur);
    if (pinch && ptrs.size === 2) {
      const [a, b] = Array.from(ptrs.values());
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]) || 1;
      zoomAt((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, d / pinch.d, true);
      pinch.d = d; moved = true;
      return;
    }
    if (!drag || drag.id !== e.pointerId) return;
    const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
    if (!moved && Math.hypot(dx, dy) < 5) return;
    if (!moved) { moved = true; vp.classList.add("sh-drag"); try { vp.setPointerCapture(e.pointerId); } catch (err) { /* already gone */ } }
    const now = performance.now(), dt = Math.max(1, now - lastT);
    vel = { x: lerp(vel.x, ((cur[0] - prev[0]) / dt) * 1000, 0.4), y: lerp(vel.y, ((cur[1] - prev[1]) / dt) * 1000, 0.4) };
    lastT = now;
    view.jump({ x: drag.x + dx, y: drag.y + dy, k: view.value.k });
  });
  const up = (e) => {
    if (!ptrs.has(e.pointerId)) return;
    ptrs.delete(e.pointerId);
    if (ptrs.size < 2) pinch = null;
    if (drag && drag.id === e.pointerId) {
      if (moved && performance.now() - lastT < 80) { const v = view.value; view.set({ x: v.x + vel.x * 0.16, y: v.y + vel.y * 0.16, k: v.k }, { preset: { stiffness: 120, damping: 22 } }); }
      drag = null;
    }
    vp.classList.remove("sh-drag");
  };
  vp.addEventListener("pointerup", up);
  vp.addEventListener("pointercancel", up);
  vp.addEventListener("dblclick", (e) => { if (!e.target.closest(".sh-card")) fit(); });
  vp.addEventListener("click", (e) => { if (moved) return; if (!e.target.closest(".sh-n, .sh-card")) closeCard(); });

  // ---- highlight state: selection > hover > follow ----
  let hoverId = null, selId = null, mode = "overview", lit = null;
  function paint() {
    let focus = null, hot = null;
    if (hoverId) { const nb = neighbours(hoverId); focus = selId ? new Set([...nb.nodes, ...pathOf(selId).nodes]) : nb.nodes; hot = nb.edges; }
    else if (selId) { const p = pathOf(selId); focus = p.nodes; hot = p.edges; }
    else if (lit) { focus = lit.nodes; hot = lit.edges; }
    for (const id in nEls) {
      nEls[id].classList.toggle("dim", !!focus && !focus.has(id));
      nEls[id].classList.toggle("sel", id === selId);
      nEls[id].classList.toggle("lit", !!lit && !hoverId && !selId && lit.on.has(id));
    }
    for (const id in eEls) {
      const g = eEls[id].g;
      g.classList.toggle("hot", !!hot && hot.has(id) && !lit);
      g.classList.toggle("lit", !!lit && lit.edgesOn.has(id) && !hoverId && !selId);
      g.classList.toggle("dim", !!hot && !hot.has(id));
    }
  }
  for (const id in nEls) {
    const b = nEls[id];
    b.addEventListener("pointerenter", (e) => { if (e.pointerType !== "mouse" || drag) return; hoverId = id; lifts[id].set(-4); b.classList.add("up"); paint(); });
    b.addEventListener("pointerleave", () => { if (hoverId === id) hoverId = null; if (selId !== id) lifts[id].set(0); b.classList.remove("up"); paint(); });
    b.addEventListener("focus", () => { if (b.matches(":focus-visible")) { hoverId = id; lifts[id].set(-4); paint(); } });
    b.addEventListener("blur", () => { if (hoverId === id) hoverId = null; if (selId !== id) lifts[id].set(0); paint(); });
    b.addEventListener("click", (e) => { e.stopPropagation(); if (moved) return; selId === id ? closeCard() : openCard(id); });
  }

  // ---- card: the node swells into a small card ----
  let card = null;
  function openCard(id) {
    if (card) closeCard(true);
    const n = NODE[id], b = nEls[id];
    selId = id;
    b.setAttribute("aria-expanded", "true");
    lifts[id].set(-4);
    const c = el("div", "sh-card");
    c.id = "sh-card";
    c.setAttribute("role", "note");
    const ci = el("div", "sh-card-in", `<b>${esc(n.label)}</b><span>${esc(n.sub)}</span><ul>${n.info.map((t) => `<li${/[/]/.test(t) && !/ /.test(t) ? ' class="f"' : ""}>${esc(t)}</li>`).join("")}</ul>`);
    c.append(ci);
    b.setAttribute("aria-controls", "sh-card");
    Object.assign(c.style, { left: n.x + "px", top: n.y + "px", width: n.w + "px", height: n.h + "px" });
    nLayer.append(c);
    const cw = 248, ch = ci.offsetHeight;
    const tx = clamp(n.x - 6, 12, W - cw - 12), ty = clamp(n.y - 6, 12, H - ch - 12);
    const s = spring({ x: n.x, y: n.y, w: n.w, h: n.h }, {
      preset: { stiffness: 330, damping: 22 }, precision: 0.1,
      onUpdate: (v) => {
        Object.assign(c.style, { left: v.x + "px", top: v.y + "px", width: Math.max(20, v.w) + "px", height: Math.max(20, v.h) + "px" });
        ci.style.opacity = String(clamp((v.w - n.w) / (cw - n.w) * 1.6 - 0.6));
      },
    });
    s.set({ x: tx, y: ty, w: cw, h: ch });
    if (rm) ci.style.opacity = "1";
    card = { id, el: c, s, n };
    paint();
  }
  function closeCard(instant = false) {
    if (!card) return false;
    const { id, el: c, s, n } = card;
    card = null;
    selId = null;
    nEls[id].setAttribute("aria-expanded", "false");
    nEls[id].removeAttribute("aria-controls");
    if (hoverId !== id) lifts[id].set(0);
    paint();
    if (instant || rm) { s.stop(); c.remove(); return true; }
    s.config({ stiffness: 420, damping: 32 });
    s.set({ x: n.x, y: n.y, w: n.w, h: n.h });
    later(() => { s.stop(); c.remove(); }, 260);
    return true;
  }

  // ---- build-in: groups slide in column by column, traces draw, then pulses start ----
  const springs = [];
  let built = false;
  function build(instant = false) {
    if (built) return;
    built = true;
    if (instant || rm) {
      for (const g of GROUPS) { gEls[g.id].style.opacity = "1"; }
      for (const n of NODES) nEls[n.id].style.opacity = "1";
      for (const id in eEls) { const x = eEls[id]; x.g.style.opacity = "1"; for (const p of [x.b, x.h]) { p.style.strokeDasharray = ""; p.style.strokeDashoffset = ""; } x.pads.forEach((c) => { c.style.opacity = ""; }); }
      svg.classList.add("sh-live"); world.classList.add("sh-live");
      if (eyes) eyes.emote("pleased");
      return;
    }
    for (const g of GROUPS) {
      const kids = NODES.filter((n) => n.g === g.id).map((n) => nEls[n.id]);
      const s = spring({ o: 0, y: 26 }, {
        preset: { stiffness: 210, damping: 19 }, precision: 0.01,
        onUpdate: (v) => {
          gEls[g.id].style.opacity = String(clamp(v.o)); gEls[g.id].style.translate = `0 ${v.y}px`;
          kids.forEach((k, i) => { k.style.opacity = String(clamp(v.o * 1.15 - i * 0.06)); k.style.translate = `0 ${v.y * (1 + i * 0.25)}px`; });
        },
        onRest: (v) => { if (v.o === 1) { gEls[g.id].style.translate = ""; kids.forEach((k) => { k.style.translate = ""; k.style.opacity = "1"; }); } },
      });
      springs.push(s);
      later(() => s.set({ o: 1, y: 0 }), 40 + g.col * 75);
    }
    const colOf = (id) => GROUPS.find((g) => g.id === NODE[id].g).col;
    for (const e of EDGES) {
      const x = eEls[e.id];
      const s = spring(0, {
        preset: { stiffness: 70, damping: 14 }, precision: 0.002,
        onUpdate: (p) => {
          const off = String(x.len * (1 - clamp(p))); x.b.style.strokeDashoffset = off; x.h.style.strokeDashoffset = off;
          x.pads[0].style.opacity = String(clamp(p * 8)); x.pads[1].style.opacity = String(clamp((p - 0.85) * 8));
        },
        onRest: () => {
          x.pads.forEach((c) => { c.style.opacity = ""; }); x.b.style.strokeDasharray = ""; x.h.style.strokeDasharray = ""; x.b.style.strokeDashoffset = ""; x.h.style.strokeDashoffset = ""; },
      });
      springs.push(s);
      later(() => s.set(1), 260 + Math.max(colOf(e.from), colOf(e.to) - (e.ret ? 3 : 0)) * 75);
    }
    later(() => { svg.classList.add("sh-live"); if (eyes) eyes.emote("pleased"); }, 1150);
  }

  // ---- follow: one glowing packet along a real route ----
  let player = null;
  function stopPlay() {
    if (player) { player.stop(); player = null; }
    pk.setAttribute("opacity", "0");
    lit = null;
    paint();
  }
  function play(which) {
    stopPlay();
    mode = which;
    if (which === "overview" || !ROUTES[which]) return;
    closeCard(true);
    const route = ROUTES[which].map(([id, rev]) => ({ e: EDGE[id], x: eEls[id], rev: !!rev }));
    const seq = [route[0].rev ? route[0].e.to : route[0].e.from, ...route.map((r) => (r.rev ? r.e.from : r.e.to))];
    const all = { nodes: new Set(seq), edges: new Set(route.map((r) => r.e.id)) };
    lit = { nodes: all.nodes, edges: all.edges, on: new Set([seq[0]]), edgesOn: new Set() };
    const ping = (id) => {
      const b = nEls[id];
      lit.on.add(id);
      b.classList.remove("ping"); void b.offsetWidth; b.classList.add("ping");
      if (!rm) wobble(b, 0.55);
      if (id === "you" && eyes) eyes.emote("hop");
      if (id === "viewer" || id === "explore") { if (eyes) eyes.emote("delight"); }
      paint();
    };
    paint();
    if (rm) {
      // static: each step fades in after the last
      let i = 0;
      const step = () => {
        if (mode !== which) return;
        if (i < route.length) { lit.edgesOn.add(route[i].e.id); ping(seq[i + 1]); i++; player.t = later(step, 520); }
        else player.t = later(() => { if (mode === which) play(which); }, 1800);
      };
      player = { t: 0, stop() { clearTimeout(this.t); } };
      ping(seq[0]);
      player.t = later(step, 400);
      return;
    }
    const SPEED = 560;
    let i = 0, t = 0, dwell = 0.25, pause = 0;
    ping(seq[0]);
    const place = () => {
      const r = route[Math.min(i, route.length - 1)];
      const d = r.rev ? r.x.len - t : t;
      const p = r.x.b.getPointAtLength(clamp(d, 0, r.x.len));
      pk.setAttribute("transform", `translate(${p.x} ${p.y})`);
    };
    pk.setAttribute("opacity", "1");
    place();
    const stop = tick((dt) => {
      if (pause > 0) { pause -= dt; if (pause <= 0) { lit.on = new Set([seq[0]]); lit.edgesOn = new Set(); i = 0; t = 0; dwell = 0.25; pk.setAttribute("opacity", "1"); ping(seq[0]); place(); } return true; }
      if (dwell > 0) { dwell -= dt; return true; }
      const r = route[i];
      if (t === 0) { lit.edgesOn.add(r.e.id); paint(); }
      // ease in and out over each trace so the packet reads as hopping node to node
      t = Math.min(r.x.len, t + SPEED * dt * (0.55 + 0.9 * Math.sin(Math.PI * clamp(t / r.x.len, 0.04, 0.96))));
      place();
      if (t >= r.x.len) {
        ping(seq[i + 1]);
        i++; t = 0; dwell = 0.14;
        if (i >= route.length) { pause = 1.6; later(() => { if (player && pause > 0) pk.setAttribute("opacity", "0"); }, 500); }
      }
      return true;
    });
    player = { stop };
  }

  return {
    build, play, fit, closeCard: () => closeCard(),
    stop() { stopPlay(); },
    destroy() {
      stopPlay();
      springs.forEach((s) => s.stop());
      Object.values(lifts).forEach((s) => s.stop());
      view.stop();
      if (card) { card.s.stop(); card = null; }
      if (eyes) eyes.destroy();
      vp.remove();
    },
    // for tests
    get nodes() { return nEls; }, get edges() { return eEls; }, get view() { return view.value; },
  };
}

// ---------------------------------------------------------------- stack (phones)
function buildStack(host, { rm, later }) {
  const box = el("div", "sh-stk");
  const nEls = {};
  let eyes = null;
  const linkSvg = (dir = 1) => `<svg viewBox="0 0 12 26" aria-hidden="true" class="sh-tr"><path class="h" d="M6 0V26"/><path class="b" d="M6 0V26"/><path class="p${dir < 0 ? " r" : ""}" d="M6 0V26"/></svg>`;
  STACK.forEach((gid, gi) => {
    const g = GROUPS.find((x) => x.id === gid);
    if (gi) {
      const flow = !(gid === "voice" || gid === "library");
      box.append(el("div", "sh-lk" + (flow ? "" : " gap"), flow ? linkSvg() : ""));
    }
    const sec = el("section", "sh-sg", `<b>${esc(g.label)}${STACK_NOTE[gid] ? `<em>${esc(STACK_NOTE[gid])}</em>` : ""}</b>`);
    sec.style.setProperty("--t", g.tint);
    const gridEl = el("div", "sh-sgn");
    for (const n of NODES.filter((x) => x.g === gid)) {
      const b = el("button", "sh-n" + (n.id === "you" ? " sh-you" : ""), `<b>${esc(n.label)}</b><span>${esc(n.sub)}</span>`);
      b.type = "button";
      b.dataset.id = n.id;
      b.setAttribute("aria-expanded", "false");
      b.setAttribute("aria-label", `${n.label}, ${n.sub}`);
      b.style.setProperty("--t", tintOf(n.g));
      if (n.id === "you") { const eh = el("span", "sh-eyes"); b.prepend(eh); eyes = createEyes(eh, { label: "Etch's eyes" }); }
      gridEl.append(b);
      nEls[n.id] = b;
    }
    sec.append(gridEl);
    sec.style.opacity = "0";
    box.append(sec);
  });
  const dot = el("div", "sh-pkd");
  box.append(dot);
  host.append(box);

  let selId = null, lit = null, card = null;
  function paint() {
    const focus = selId ? pathOf(selId).nodes : lit ? lit.nodes : null;
    for (const id in nEls) {
      nEls[id].classList.toggle("dim", !!focus && !focus.has(id));
      nEls[id].classList.toggle("sel", id === selId);
      nEls[id].classList.toggle("lit", !!lit && !selId && lit.on.has(id));
    }
  }
  function openCard(id) {
    closeCard(true);
    selId = id;
    const n = NODE[id], b = nEls[id];
    b.setAttribute("aria-expanded", "true");
    const c = el("div", "sh-card");
    c.setAttribute("role", "note");
    const ci = el("div", "sh-card-in", `<b>${esc(n.label)}</b><span>${esc(n.sub)}</span><ul>${n.info.map((t) => `<li${/[/]/.test(t) && !/ /.test(t) ? ' class="f"' : ""}>${esc(t)}</li>`).join("")}</ul>`);
    c.append(ci);
    // after the row the node sits in, so the grid keeps its shape
    const sibs = Array.from(b.parentNode.children).filter((x) => x.classList.contains("sh-n"));
    const idx = sibs.indexOf(b), rowEnd = id === "you" ? b : sibs[Math.min(sibs.length - 1, idx + (idx % 2 ? 0 : 1))];
    rowEnd.after(c);
    const ch = ci.offsetHeight;
    c.style.height = "0px";
    const s = spring({ h: 0 }, { preset: { stiffness: 340, damping: 26 }, precision: 0.2, onUpdate: (v) => { c.style.height = Math.max(0, v.h) + "px"; ci.style.opacity = String(clamp(v.h / ch * 1.5 - 0.5)); }, onRest: (v) => { if (v.h === ch) c.style.height = ""; } });
    s.set({ h: ch });
    if (rm) { ci.style.opacity = "1"; c.style.height = ""; }
    card = { id, el: c, s, ch };
    paint();
  }
  function closeCard(instant = false) {
    if (!card) return false;
    const { id, el: c, s } = card;
    card = null; selId = null;
    nEls[id].setAttribute("aria-expanded", "false");
    paint();
    if (instant || rm) { s.stop(); c.remove(); return true; }
    s.jump({ h: c.offsetHeight }); s.set({ h: 0 });
    later(() => { s.stop(); c.remove(); }, 300);
    return true;
  }
  for (const id in nEls) nEls[id].addEventListener("click", (e) => { e.stopPropagation(); selId === id ? closeCard() : openCard(id); });
  box.addEventListener("click", (e) => { if (!e.target.closest(".sh-n, .sh-card")) closeCard(); });

  const springs = [];
  let built = false;
  function build(instant = false) {
    if (built) return;
    built = true;
    const secs = Array.from(box.querySelectorAll(".sh-sg"));
    if (instant || rm) { secs.forEach((s) => { s.style.opacity = "1"; }); box.classList.add("sh-live"); return; }
    secs.forEach((sec, i) => {
      const s = spring({ o: 0, y: 22 }, { preset: { stiffness: 220, damping: 20 }, precision: 0.01, onUpdate: (v) => { sec.style.opacity = String(clamp(v.o)); sec.style.translate = `0 ${v.y}px`; }, onRest: (v) => { if (v.o === 1) sec.style.translate = ""; } });
      springs.push(s);
      later(() => s.set({ o: 1, y: 0 }), 40 + i * 70);
    });
    later(() => { box.classList.add("sh-live"); if (eyes) eyes.emote("pleased"); }, 40 + secs.length * 70 + 200);
  }

  let player = null;
  function stopPlay() { if (player) { player.stop(); player = null; } dot.style.opacity = "0"; lit = null; paint(); }
  function play(which) {
    stopPlay();
    if (!ROUTES[which]) return;
    closeCard(true);
    const route = ROUTES[which].map(([id, rev]) => ({ e: EDGE[id], rev: !!rev }));
    const seq = [route[0].rev ? route[0].e.to : route[0].e.from, ...route.map((r) => (r.rev ? r.e.from : r.e.to))];
    lit = { nodes: new Set(seq), on: new Set() };
    const at = (id) => { const b = nEls[id]; let x = b.offsetWidth / 2, y = b.offsetHeight / 2, n = b; while (n && n !== box) { x += n.offsetLeft; y += n.offsetTop; n = n.offsetParent; } return [x, y]; };
    const ping = (id) => {
      const b = nEls[id];
      lit.on.add(id);
      b.classList.remove("ping"); void b.offsetWidth; b.classList.add("ping");
      if (!rm) wobble(b, 0.5);
      const br = b.getBoundingClientRect(), vr = box.getBoundingClientRect();
      if (br.top < vr.top + 20 || br.bottom > vr.bottom - 20) box.scrollTo({ top: box.scrollTop + br.top - vr.top - vr.height / 2 + br.height / 2, behavior: rm ? "auto" : "smooth" });
      paint();
    };
    let i = 0;
    if (rm) {
      const step = () => { if (i < seq.length) { ping(seq[i++]); player.t = later(step, 560); } else player.t = later(() => play(which), 1800); };
      player = { t: 0, stop() { clearTimeout(this.t); } };
      step();
      return;
    }
    let t = 0, dwell = 0.2, pause = 0, a = at(seq[0]), b = at(seq[1]);
    ping(seq[0]);
    dot.style.opacity = "1";
    const put = (p) => { dot.style.transform = `translate(${p[0]}px, ${p[1]}px)`; };
    put(a);
    const stop = tick((dt) => {
      if (pause > 0) { pause -= dt; if (pause <= 0) { lit.on = new Set(); i = 0; t = 0; a = at(seq[0]); b = at(seq[1]); dot.style.opacity = "1"; ping(seq[0]); put(a); dwell = 0.2; } return true; }
      if (dwell > 0) { dwell -= dt; return true; }
      const len = Math.max(1, Math.hypot(b[0] - a[0], b[1] - a[1]));
      t = Math.min(1, t + (dt * 420) / Math.max(120, len));
      const e = t * t * (3 - 2 * t);
      put([lerp(a[0], b[0], e), lerp(a[1], b[1], e)]);
      if (t >= 1) {
        i++; ping(seq[i]); t = 0; dwell = 0.12;
        if (i >= seq.length - 1) { pause = 1.6; later(() => { if (pause > 0) dot.style.opacity = "0"; }, 500); }
        else { a = at(seq[i]); b = at(seq[i + 1]); }
      }
      return true;
    });
    player = { stop };
  }
  return {
    build, play, closeCard: () => closeCard(), stop: stopPlay, fit: null,
    destroy() { stopPlay(); springs.forEach((s) => s.stop()); if (card) card.s.stop(); if (eyes) eyes.destroy(); box.remove(); },
    get nodes() { return nEls; },
  };
}

// ---------------------------------------------------------------- mount
function init() {
  if (document.querySelector(".sh-fab")) return;
  mountButton();
  // "?" (Shift+/) opens it from anywhere you are not typing
  addEventListener("keydown", (e) => {
    if (e.key !== "?" || e.metaKey || e.ctrlKey || e.altKey || isTyping() || panel) return;
    e.preventDefault();
    open();
  });
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
else init();

export { open as openStackHelp, close as closeStackHelp };
// inspection from tests
window.__stackHelp = { open, close, get panel() { return panel; }, place: placeButton };

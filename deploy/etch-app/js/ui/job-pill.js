// "Leave while it builds, come back": the editor keeps the running build in localStorage ("etch-running-job":
// { job, run, token, title, startedAt, design, text, ref, board, mock, duration, stages }) and the build keeps going
// on the engine. Other pages (Explore, the new-board start screen) show a small liquid pill at the bottom centre
// with Etch's eyes at work: "Building <title> · 1:42 · Return →", following GET /etch/job every 2 s for the stage
// (a mock build runs on its stored clock). When it is done the pill turns to "<title> is ready · Open" with a happy
// bounce. Clicking goes back to the editor: the finished board, or the editor picking the running build back up
// (board.html?resume=1). The × forgets it (the build itself still finishes on the engine).
//
//   import { mountJobPill, saveJob, readJob, clearJob } from "./ui/job-pill.js";
//   mountJobPill({ job: api.job, open: (state, done) => url });
import { createEyes } from "./eyes.js";
import { squish, wobble } from "./goo.js";
import { spring, reducedMotion } from "./spring.js";

const KEY = "etch-running-job";
export function readJob() {
  try { const s = JSON.parse(localStorage.getItem(KEY) || "null"); return s && s.job ? s : null; } catch (e) { return null; }
}
export function saveJob(s) { try { localStorage.setItem(KEY, JSON.stringify({ ...readJob(), ...s })); } catch (e) { /* not kept */ } }
export function clearJob() { try { localStorage.removeItem(KEY); } catch (e) { /* nothing */ } }

const CSS = `
.jp { position: fixed; left: 50%; bottom: 18px; z-index: 70; transform: translateX(-50%); display: flex; align-items: center; gap: 10px; height: 56px; padding: 0 8px 0 10px; max-width: calc(100vw - 32px);
  border-radius: 28px; background: #fff; color: #171b24; font: 500 14px/1 "Figtree", system-ui, sans-serif; box-shadow: 0 10px 22px rgba(22, 28, 45, .13), 0 1px 2px rgba(22, 28, 45, .12); transform-origin: 50% 100%; }
.jp.rm { transition: opacity .2s; }
.jp-eyes { flex: none; width: 46px; height: 36px; }
.jp-eyes .ag-eyes { display: block; width: 100%; height: 100%; overflow: visible; transform-origin: 50% 85%; }
.jp-eyes .ag-eye { fill: #f6f8fc; }
.jp-eyes .ag-eyes-dots circle { fill: #171b24; }
.jp-tx { display: flex; align-items: center; gap: 6px; min-width: 0; white-space: nowrap; }
.jp-t { overflow: hidden; text-overflow: ellipsis; max-width: 32vw; font-weight: 600; }
.jp-m { color: #6c717c; font-variant-numeric: tabular-nums; }
.jp-s { color: #6c717c; overflow: hidden; text-overflow: ellipsis; max-width: 22vw; }
.jp-go { flex: none; height: 40px; padding: 0 16px; border: 0; border-radius: 20px; background: #171b24; color: #fff; font: 600 13.5px "Figtree", system-ui, sans-serif; cursor: pointer; }
.jp.ready .jp-go { background: #1f7ae0; }
.jp-x { flex: none; width: 32px; height: 32px; border: 0; border-radius: 50%; background: #f1f3f6; color: #4a545e; font: 400 18px/1 system-ui; cursor: pointer; display: grid; place-items: center; }
.jp-go:focus-visible, .jp-x:focus-visible { outline: 2px solid #1f7ae0; outline-offset: 2px; }
@media (max-width: 560px) { .jp-s { display: none; } .jp-t { max-width: 34vw; } .jp { gap: 8px; } }
`;
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export function mountJobPill({ job = null, open = () => "board.html?resume=1", host = document.body, onDismiss = () => {}, onReady = () => {} } = {}) {
  const st = readJob();
  if (!st) return null;
  if (!document.getElementById("jp-css")) { const s = document.createElement("style"); s.id = "jp-css"; s.textContent = CSS; document.head.append(s); }
  const root = document.createElement("div");
  root.className = "jp"; root.setAttribute("role", "status"); root.setAttribute("aria-live", "polite");
  root.innerHTML = `<span class="jp-eyes"></span><span class="jp-tx"><span class="jp-w">Building</span> <span class="jp-t"></span><span class="jp-m"></span><span class="jp-s"></span></span><button class="jp-go" type="button">Return →</button><button class="jp-x" type="button" aria-label="Forget this build">×</button>`;
  const $ = (c) => root.querySelector(c);
  $(".jp-t").textContent = st.title || "your board";
  host.append(root);
  const eyes = createEyes($(".jp-eyes"), { label: "Etch is building" });
  eyes.setState("working");
  squish($(".jp-go")); squish($(".jp-x"));
  // in: rise and settle on a spring
  const s = spring({ y: 26, k: 0.6 }, { preset: "bouncy", precision: 0.002, onUpdate: (v) => { root.style.translate = `0 ${v.y}px`; root.style.scale = String(v.k); root.style.opacity = String(Math.min(1, v.k * 1.4 - 0.4)); } });
  if (reducedMotion()) root.classList.add("rm");
  s.set({ y: 0, k: 1 });

  let done = false, since = 0, stage = "", timer = 0, alive = true;
  const elapsed = () => Math.max(0, (Date.now() - (st.startedAt || Date.now())) / 1000);
  function paint() {
    if (done) return;
    $(".jp-m").textContent = `· ${mmss(elapsed())}`;
    $(".jp-s").textContent = stage ? `· ${stage}` : "";
  }
  function ready(ok = true) {
    if (done) return;
    done = true;
    root.classList.add("ready");
    $(".jp-w").textContent = "";
    $(".jp-m").textContent = ok ? "is ready" : "stopped";
    $(".jp-s").textContent = "";
    $(".jp-go").textContent = ok ? "Open" : "See why";
    eyes.setState(ok ? "success" : "confused"); eyes.react(ok ? "bounce" : "shake");
    wobble(root, 1.1);
    const first = !readJob()?.finished;
    saveJob({ finished: Date.now(), ok });
    if (ok && first) { try { onReady(readJob() || st); } catch (e) { /* the page's own business */ } }
  }
  async function poll() {
    if (!alive || done) return;
    const now = readJob();
    if (!now || now.job !== st.job) { remove(); return; } // finished or forgotten in another tab
    if (st.mock) {
      const t = elapsed();
      for (const [at, label] of st.stages || []) if (at <= t) stage = label;
      if (t >= (st.duration || 12)) ready(true);
    } else if (job) {
      try {
        const r = await job(st.job, since);
        since = r.next ?? since;
        if (r.stage) stage = r.stage;
        if (r.run && r.run !== st.run) { st.run = r.run; saveJob({ run: r.run }); }
        for (const ev of r.events || []) if (ev.agent === "lead" && (ev.type === "done" || ev.type === "error" || ev.type === "cancelled")) st.ok = ev.type === "done" && ev.ok !== false;
        if (r.done) ready(st.ok !== false && r.state !== "failed" && r.state !== "cancelled");
      } catch (e) { if (e?.status === 404) { remove(); clearJob(); return; } }
    }
    paint();
    if (!done) timer = setTimeout(poll, 2000);
  }
  if (st.finished) { done = false; ready(st.ok !== false); } else { paint(); poll(); }
  const tick = setInterval(paint, 1000);

  $(".jp-go").addEventListener("click", () => { const url = open(readJob() || st, done); if (done) clearJob(); location.href = url; });
  $(".jp-x").addEventListener("click", () => { clearJob(); onDismiss(); remove(); });
  function remove() {
    alive = false; clearTimeout(timer); clearInterval(tick);
    s.set({ y: 26, k: 0.6 });
    setTimeout(() => { try { eyes.destroy(); } catch (e) { /* gone */ } root.remove(); }, reducedMotion() ? 0 : 260);
  }
  return { el: root, remove, get done() { return done; } };
}

// ?new=1&mock=1's scripted design build ends on this library board (js/editor/main.js loads it)
export const DEMO = "gh:OLIMEX/ESP32-POE/HARDWARE/ESP32-PoE-hardware-revision-M2";
// where the pill (or a link) goes: a finished build's board, or the editor picking the running build back up
export const jobBoardId = (s) => (s.mock ? (s.design ? DEMO : s.board) : s.run || s.board);
export function jobHref(s, done, page, carry) {
  if (done) return page("board") + carry({ id: s.mock ? (s.design ? DEMO : s.board) : s.run || s.board });
  return page("board") + carry(s.design || !s.board ? { resume: 1 } : { id: s.board, resume: 1 });
}

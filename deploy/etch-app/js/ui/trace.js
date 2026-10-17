// Agent activity primitives: everything the agent does gets a visible element.
//   pixelLoader()      pixel grid shimmer + elapsed time while waiting
//   thinkingTrace()    expandable trace of step / reasoning / search / coding rows
//   timeline()         one turn's chronological rows: noise grouped into summary lines, folds to one line when done
//   toolChip()         small chip for one tool call ("Read parts.json")
//   taskRow()          running / failed / completed rows with counts and progress
//   streamAnswer()     streamed answer: **bold**, `code`, refdes chips, mentions, follow-ups
//   approvalCard()     a decision: options (single or multi), Skip / Continue -> Promise
//   recommendCard()    a proposed change with a confidence meter, Accept / Alternatives -> Promise
//   bindMentions()     one delegated listener that turns refdes chip hovers and clicks into callbacks
// New elements arrive with the .lq-in liquid entrance (see css/agent.css); nothing here reads layout per frame.
import { spring, clamp, reducedMotion } from "./spring.js";
import { squish } from "./goo.js";

// ---- tiny DOM helpers ----
export function el(tag, cls, ...kids) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  for (const k of kids.flat()) if (k != null && k !== false) n.append(k.nodeType ? k : String(k));
  return n;
}
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const escRe = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const P = {
  step: '<circle cx="8" cy="8" r="2.6" fill="currentColor"/>',
  reasoning: '<path d="M8 2.2c.5 2.6 1.4 3.6 4 4.1-2.6.5-3.5 1.5-4 4.1-.5-2.6-1.4-3.6-4-4.1 2.6-.5 3.5-1.5 4-4.1Z" fill="currentColor"/><circle cx="12.4" cy="12.2" r="1.2" fill="currentColor"/>',
  search: '<circle cx="7" cy="7" r="3.9" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="m10 10 3.4 3.4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
  tool: '<path d="M5.6 4.6 2.4 8l3.2 3.4M10.4 4.6 13.6 8l-3.2 3.4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
  task: '<rect x="2.6" y="2.6" width="10.8" height="10.8" rx="3.2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5.6 8h4.8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
  done: '<path d="m4.2 8.4 2.5 2.5 5.1-5.6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
  failed: '<path d="m5 5 6 6M11 5l-6 6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
  chev: '<path d="m4.5 6.5 3.5 3.5 3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
  close: '<path d="m4.5 4.5 7 7M11.5 4.5l-7 7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
  mic: '<rect x="5.6" y="1.8" width="4.8" height="8.2" rx="2.4" fill="currentColor"/><path d="M3.4 7.6a4.6 4.6 0 0 0 9.2 0M8 12.4v2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
  send: '<path d="M8 13V3.4M4 7.2 8 3.2l4 4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
  spark: '<path d="M8 1.8c.6 3.3 1.6 4.4 4.9 5-3.3.6-4.3 1.7-4.9 5-.6-3.3-1.6-4.4-4.9-5 3.3-.6 4.3-1.7 4.9-5Z" fill="currentColor"/>',
};
export function icon(name, cls = "") {
  const s = el("span", "ag-ic" + (cls ? " " + cls : ""));
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = `<svg viewBox="0 0 16 16" width="16" height="16">${P[name] || P.step}</svg>`;
  return s;
}
function spinner() { const s = el("span", "ag-spin"); s.setAttribute("aria-hidden", "true"); return s; }
function statusIcon(kind, status) {
  if (status === "running") return spinner();
  if (status === "failed") return icon("failed", "is-failed");
  if (status === "done") return icon("done", "is-done");
  return icon(kind);
}
export function fmtSecs(s) {
  s = Math.max(0, +s || 0);
  if (s < 60) return s.toFixed(1) + "s";
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return `${m}m ${String(r).padStart(2, "0")}s`;
}

// items added in a burst enter one after another rather than all at once
let lastIn = 0;
export function stagger(node, gap = 70) {
  const now = performance.now();
  const at = Math.min(now + 420, Math.max(now, lastIn + gap));
  lastIn = at;
  if (at - now > 1) node.style.animationDelay = Math.round(at - now) + "ms";
  return node;
}

// a failed step's title in plain words: "Copying the board into Etch's own design" -> "Couldn't copy the board"
const ADD_E = /(?:at|ac|ak|av|iz|ar|it|os|ut|us|ag|ir|ov)$/;
const verb = (w) => {
  let v = w.toLowerCase();
  if (v.length > 3 && /([bdgmnprt])\1$/.test(v)) v = v.slice(0, -1);
  else if (ADD_E.test(v) && v.length <= 7) v += "e";
  return v;
};
export function failTitle(t) {
  const s = String(t || "").trim();
  const m = /^([A-Z][a-z]+)ing\b\s*(.*)$/.exec(s);
  if (!m) return s;
  const [main, ...tail] = m[2].split(" · ");
  const rest = main.split(/\s+(?:into|to|from|for|with|in|on|onto|using|via|so)\s+/i)[0]
    .replace(/\b([a-z]{3,})ing\b/g, (_, w) => verb(w)).split(/\s+/).filter(Boolean).slice(0, 4).join(" ");
  return `Couldn't ${verb(m[1])}${rest ? " " + rest : ""}${tail.length ? " · " + tail.join(" · ") : ""}`;
}
// a long technical error gets a plain first line; the raw text stays one click away
export function plainError(msg) {
  const m = String(msg || "").replace(/\s+/g, " ").trim();
  if (!m) return "";
  if (m.length <= 60 && !/[\[\]{}<>_]|::|\w:\w/.test(m)) return m;
  const R = [
    [/pins?\s*\[[^\]]*\]\s*do(?:es)? not exist on\s*\S*conn/i, "A connector had the wrong pin count"],
    [/do(?:es)? not exist on/i, "A part was wired to pins it doesn't have"],
    [/footprint[^.]*not found|no (?:such )?footprint/i, "A part's footprint couldn't be found"],
    [/clearance|\bDRC\b/i, "The layout broke a spacing rule"],
    [/unconnected|\bERC\b/i, "Some pins aren't connected"],
    [/timed? ?out/i, "It took too long"],
    [/\b429\b|too many requests/i, "Etch is busy right now"],
    [/licen[cs]e/i, "The board's licence doesn't allow this"],
    [/spec error|invalid spec|schema/i, "The design spec had a mistake"],
  ];
  for (const [re, t] of R) if (re.test(m)) return t;
  const first = m.split(/(?<=[.!?])\s|:\s/)[0];
  return first.length <= 70 && !/[\[\]{}_]|::/.test(first) ? first : "Something went wrong in this step";
}

// ---- pixel loader: a 3x3 grid whose cells light in a diagonal wave, plus a label and an elapsed timer ----
export function pixelLoader({ label = "", timer = true, size = "md" } = {}) {
  const grid = el("span", "ag-pix ag-pix-" + size);
  grid.setAttribute("aria-hidden", "true");
  for (let i = 0; i < 9; i++) { const c = el("i"); c.style.setProperty("--d", ((i % 3) + Math.floor(i / 3)) * 110 + "ms"); grid.append(c); }
  const lab = el("span", "ag-pix-label ag-shimmer", label);
  const time = el("span", "ag-pix-time");
  const root = el("span", "ag-loader", grid, lab, timer ? time : null);
  root.setAttribute("role", "status");
  let t0 = 0, iv = 0;
  const paint = () => { time.textContent = fmtSecs((performance.now() - t0) / 1000); };
  const api = {
    el: root,
    get seconds() { return t0 ? (performance.now() - t0) / 1000 : 0; },
    get running() { return !!iv; },
    start(l) {
      if (l != null) lab.textContent = l;
      if (!iv) { t0 = performance.now(); if (timer) { paint(); iv = setInterval(paint, 100); } else iv = -1; }
      root.classList.add("on");
      return api;
    },
    setLabel(l) { lab.textContent = l || ""; },
    stop() { if (iv > 0) clearInterval(iv); iv = 0; root.classList.remove("on"); return api.seconds; },
  };
  return api;
}

// ---- thinking trace ----
const KIND_LABEL = { step: "Step", reasoning: "Reasoning", search: "Search", tool: "Coding", coding: "Coding", task: "Task" };
function refChip(ref) {
  const b = el("button", "ag-ref", ref);
  b.type = "button"; b.dataset.ref = ref;
  return b;
}
export function traceRow(d) {
  const ic = el("span", "ag-tr-ic");
  const title = el("span", "ag-tr-t");
  const detail = el("span", "ag-tr-d");
  const raw = el("span", "ag-tr-raw");
  const meta = el("span", "ag-tr-m");
  const fill = el("i", "ag-bar-fill"), bar = el("span", "ag-bar ag-tr-bar", fill);
  const row = el("li", "ag-tr-row lq-in", ic, el("span", "ag-tr-txt", title, detail, raw, bar), meta);
  raw.hidden = true; bar.hidden = true;
  let loader = null, liveT = 0, times = 1;
  const paintMeta = () => {
    const n = api.data;
    const m = loader ? fmtSecs(loader.seconds) : n.seconds != null ? fmtSecs(n.seconds) : n.count != null ? String(n.count) : "";
    meta.textContent = times > 1 ? `×${times}${m ? " · " + m : ""}` : m;
  };
  const toggleRaw = (e) => {
    if (row.dataset.expand == null || (e.target.closest && e.target.closest(".ag-ref"))) return;
    if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    raw.hidden = !raw.hidden;
    row.setAttribute("aria-expanded", raw.hidden ? "false" : "true");
  };
  row.addEventListener("click", toggleRaw);
  row.addEventListener("keydown", toggleRaw);
  const api = {
    el: row,
    data: {},
    get key() { return `${api.data.title}|${api.data.detail}`; },
    bump() { times++; paintMeta(); wobbleIn(row); return times; },
    // the one live row in the dock carries the loader and the elapsed time
    live(on) {
      if (on && !loader) {
        loader = pixelLoader({ timer: false, size: "xs" }).start();
        ic.replaceChildren(loader.el);
        liveT = setInterval(paintMeta, 100); paintMeta();
      } else if (!on && loader) {
        loader.stop(); loader = null; clearInterval(liveT);
        const k = api.data.kind === "coding" ? "tool" : api.data.kind || "step";
        ic.replaceChildren(api.data.status === "running" ? icon(k, "is-live") : statusIcon(k, api.data.status));
        paintMeta();
      }
    },
    update(p) {
      const prev = api.data;
      const n = (api.data = { ...prev, ...p });
      const kind = n.kind === "coding" ? "tool" : n.kind || "step";
      const st = n.status || "running";
      row.dataset.kind = kind; row.dataset.status = st;
      if (!loader && (prev.status !== n.status || prev.kind !== n.kind)) ic.replaceChildren(st === "running" ? icon(kind, "is-live") : statusIcon(kind, st));
      const failed = st === "failed";
      title.replaceChildren(String((failed ? failTitle(n.title) : n.title) || KIND_LABEL[kind] || ""));
      if (n.ref) title.append(" ", refChip(n.ref));
      const plain = failed ? plainError(n.detail) : String(n.detail || "");
      detail.textContent = plain;
      detail.hidden = !plain;
      const full = String(n.detail || "").trim();
      const more = failed && full && full !== plain;
      raw.textContent = more ? full : "";
      if (more) { row.dataset.expand = ""; row.tabIndex = 0; row.setAttribute("aria-expanded", raw.hidden ? "false" : "true"); row.title = "Show the details"; }
      else { delete row.dataset.expand; row.removeAttribute("tabindex"); row.removeAttribute("aria-expanded"); row.removeAttribute("title"); raw.hidden = true; }
      const pr = st === "running" && n.progress != null ? +n.progress : null;
      bar.hidden = pr == null;
      if (pr != null) fill.style.transform = `scaleX(${clamp(pr > 1 ? pr / 100 : pr)})`;
      paintMeta();
      return api;
    },
  };
  return api.update(d);
}
function wobbleIn(n) { n.classList.remove("ag-bump"); void n.offsetWidth; n.classList.add("ag-bump"); }

// ---- timeline: one turn's rows in the order they happened ----
const GATE = /^(.+?) (passed|failed)$/;
const isGate = (d) => (d.kind || "step") === "step" && GATE.test(d.title || "");
// rows people rarely read once they are done: tool calls, searches, passed checks
const lowValue = (d) => d.status === "done" && (d.kind === "tool" || d.kind === "coding" || d.kind === "search" || isGate(d));
const lc = (s) => s.charAt(0).toLowerCase() + s.slice(1);
// While Etch works a turn shows two things: one collapsed summary of everything finished ("12 steps · screened 304
// parts · 2 tries · 41s", click to expand) and the step running now, live, at the bottom. A finished step folds into
// the summary by itself a moment after its check appears. Failures show as an "N issues" chip on the summary; only a
// turn that ends on a failure keeps that failure in view.
const LIVE_MAX = 2;
export function timeline({ verb = "Thought for" } = {}) {
  const sumText = el("span", "ag-tl-st");
  const issues = el("span", "ag-tl-iss");
  issues.hidden = true;
  const sum = el("button", "ag-tl-sum", icon("spark", "is-spark"), sumText, issues, icon("chev", "ag-chev"));
  sum.type = "button"; sum.hidden = true;
  const list = el("ol", "ag-tl-list");          // finished rows (hidden until the summary is opened)
  const live = el("ol", "ag-tl-live");          // what is running now
  const root = el("div", "ag-tl", sum, list, live);
  const rows = new Map(), all = [];
  const t0 = performance.now();
  let finished = false, sumT = 0;
  const setOpen = (o) => { root.classList.toggle("open", !!o); sum.setAttribute("aria-expanded", o ? "true" : "false"); };
  sum.addEventListener("click", () => setOpen(!root.classList.contains("open")));

  function groupSummary(g) {
    let gates = 0; const titles = [];
    for (const r of g.rows) { if (isGate(r.data)) gates++; else titles.push(String(r.data.title || "")); }
    const bits = titles.slice(0, 3).map((x, i) => (i ? lc(x) : x));
    if (titles.length > 3) bits.push(`+${titles.length - 3} more`);
    if (gates) bits.push(`${gates} check${gates === 1 ? "" : "s"} passed`);
    g.text.textContent = bits.join(" · ");
  }
  function makeGroup(before) {
    const text = el("span", "ag-tl-gt"), gl = el("ol", "ag-tl-gl");
    const gh = el("button", "ag-tl-gh", icon("done", "is-done"), text, icon("chev", "ag-chev"));
    gh.type = "button"; gh.setAttribute("aria-expanded", "false");
    const li = el("li", "ag-tl-group", gh, gl);
    gh.addEventListener("click", () => { const o = !li.classList.contains("open"); li.classList.toggle("open", o); gh.setAttribute("aria-expanded", o ? "true" : "false"); });
    const g = { li, gl, text, rows: [] };
    li._g = g;
    list.insertBefore(li, before);
    return g;
  }
  // inside the expanded summary, low-value neighbours still share one line
  function regroup(r) {
    if (!r.el.isConnected || !lowValue(r.data) || r.el.parentElement !== list) return;
    const prev = r.el.previousElementSibling, next = r.el.nextElementSibling;
    const lowRow = (n) => n && n._r && lowValue(n._r.data);
    let g = null;
    if (prev && prev._g) g = prev._g;
    else if (lowRow(prev)) { g = makeGroup(prev); g.gl.append(prev); g.rows.push(prev._r); }
    else if (next && next._g) { next._g.gl.prepend(r.el); next._g.rows.unshift(r); groupSummary(next._g); return; }
    else if (lowRow(next)) { g = makeGroup(r.el); g.gl.append(r.el, next); g.rows.push(r, next._r); groupSummary(g); return; }
    if (!g && isGate(r.data)) g = makeGroup(r.el);
    if (!g) return;
    g.gl.append(r.el); g.rows.push(r); groupSummary(g);
  }
  // the summary line: steps, the biggest numbers worth knowing, tries, time
  let finalInfo = null;
  function paintSum(final) {
    if (!final && finished && finalInfo) final = finalInfo;
    const real = all.filter((r) => !r.dupOf);
    const doneN = real.filter((r) => r.data.status !== "running").length;
    const s = (performance.now() - t0) / 1000;
    const bits = [];
    const tries = Math.max(0, ...real.map((r) => +(/\btry (\d+)/i.exec(r.data.title || "") || [])[1] || 0));
    const scr = real.map((r) => /screen\w*\s+(\d[\d,]*)\s+(.*?parts)/i.exec(r.data.title || "")).find(Boolean);
    if (final) {
      const build = real.some((r) => r.data.kind === "task" || r.data.kind === "tool");
      bits.push(tries ? `Built in ${tries} tr${tries === 1 ? "y" : "ies"} · ${fmtSecs(final.s)}` : `${build && verb === "Thought for" ? "Done in" : verb} ${fmtSecs(final.s)}`);
      bits.push(`${real.length} step${real.length === 1 ? "" : "s"}`);
      if (scr) bits.push(`screened ${scr[1]} parts`);
      const last = all[all.length - 1];
      if (last.data.status === "failed") bits.push("stopped on a problem");
      else if (real.some((r) => isGate(r.data))) bits.push("every check passes");
    } else {
      bits.push(`${doneN} step${doneN === 1 ? "" : "s"}`);
      if (scr) bits.push(`screened ${scr[1]} parts`);
      if (tries > 1) bits.push(`${tries} tries`);
      bits.push(fmtSecs(s));
    }
    sumText.textContent = bits.join(" · ");
    const nIss = real.filter((r) => r.data.status === "failed").length;
    issues.hidden = !nIss || (final && all[all.length - 1].data.status === "failed" && nIss === 1);
    issues.textContent = `${nIss} issue${nIss === 1 ? "" : "s"}`;
    sum.hidden = doneN === 0 && !final;
  }
  // a finished row folds from the live area into the summary (a quick liquid fold, then it is gone from view)
  function settle(r) {
    clearTimeout(r.foldT);
    r.foldT = setTimeout(() => {
      if (r.data.status === "running" || r.el.parentElement !== live) return;
      const go = () => { r.el.classList.remove("ag-fold"); list.append(r.el); regroup(r); paintSum(); };
      if (reducedMotion()) go(); else { r.el.classList.add("ag-fold"); setTimeout(go, 240); }
    }, 250);
  }
  function capLive() {
    // never more than a couple of live lines: older running rows wait in the summary
    const runningEls = [...live.children].filter((n) => n._r && n._r.data.status === "running");
    while (runningEls.length > LIVE_MAX) { const n = runningEls.shift(); list.append(n); }
  }
  const api = {
    el: root, rows,
    get finished() { return finished; },
    get running() { return all.some((r) => r.data.status === "running"); },
    row(d) {
      let r = rows.get(d.id);
      if (r) r.update(d);
      else {
        r = traceRow(d); r.el._r = r;
        stagger(r.el);
        rows.set(d.id, r); all.push(r);
        live.append(r.el);
      }
      if (r.data.status === "failed") {
        const twin = all.find((o) => o !== r && o.data.status === "failed" && o.key === r.key && !o.dupOf);
        if (twin && !r.dupOf) { r.dupOf = twin; twin.bump(); r.el.remove(); }
      }
      if (finished && r.data.status === "running") api.reopen();
      if (r.data.status === "running") { if (r.el.parentElement === list) live.append(r.el); capLive(); }
      else if (r.el.parentElement === live) settle(r);
      paintSum();
      clearInterval(sumT);
      if (api.running && !finished) sumT = setInterval(() => paintSum(), 1000);
      return r;
    },
    reopen() { finished = false; finalInfo = null; root.classList.remove("done"); },
    done(seconds) {
      if (!all.length) return;
      finished = true;
      clearInterval(sumT);
      const last = all[all.length - 1];
      for (const r of all) {
        clearTimeout(r.foldT);
        // a turn that ends on a failure keeps that failure in view below the summary
        if (r === last && r.data.status === "failed") { live.append(r.el); continue; }
        if (r.el.parentElement === live) list.append(r.el);
        regroup(r);
      }
      finalInfo = { s: seconds ?? (performance.now() - t0) / 1000 };
      paintSum(finalInfo);
      sum.hidden = false;
      root.classList.add("done");
      setOpen(false);
    },
  };
  return api;
}
export function thinkingTrace({ open = false, label = "Thinking" } = {}) {
  const loader = pixelLoader({ timer: false, size: "sm" });
  const head = el("button", "ag-tr-head");
  head.type = "button";
  const title = el("span", "ag-tr-title ag-shimmer", label);
  const meta = el("span", "ag-tr-meta");
  const chev = icon("chev", "ag-chev");
  const lead = el("span", "ag-tr-lead", loader.el);
  head.append(lead, title, meta, chev);
  const list = el("ol", "ag-tr-list");
  const body = el("div", "ag-tr-body", el("div", "ag-tr-inner", list));
  const root = el("div", "ag-trace lq-in", head, body);
  const rows = new Map();
  let t0 = performance.now(), finished = false;
  loader.start();
  const setOpen = (o) => { root.classList.toggle("open", !!o); head.setAttribute("aria-expanded", o ? "true" : "false"); };
  head.addEventListener("click", () => setOpen(!root.classList.contains("open")));
  setOpen(open);
  const count = () => { const n = rows.size; meta.textContent = n ? `${n} step${n === 1 ? "" : "s"}` : ""; };
  const api = {
    el: root,
    rows,
    setOpen,
    get open() { return root.classList.contains("open"); },
    row(d) {
      let r = rows.get(d.id);
      if (r) r.update(d);
      else { r = traceRow(d); rows.set(d.id, r); list.append(r.el); count(); }
      if (finished && d.status === "running") api.reopen();
      return r;
    },
    reopen() { finished = false; root.classList.remove("done"); lead.replaceChildren(loader.el); loader.start(); title.textContent = label; title.classList.add("ag-shimmer"); },
    done(seconds) {
      finished = true; loader.stop();
      const s = seconds ?? (performance.now() - t0) / 1000;
      root.classList.add("done");
      lead.replaceChildren(icon("spark", "is-spark"));
      title.classList.remove("ag-shimmer");
      title.textContent = `Thought for ${fmtSecs(s)}`;
      count();
    },
    get finished() { return finished; },
  };
  count();
  return api;
}

// ---- tool chip ----
export function toolChip(d) {
  const ic = el("span", "ag-chip-ic"), t = el("span", "ag-chip-t"), c = el("span", "ag-chip-n");
  const chip = el("span", "ag-chip lq-in", ic, t, c);
  const api = {
    el: chip, data: {},
    update(p) {
      const prev = api.data, n = (api.data = { ...prev, ...p });
      chip.dataset.status = n.status || "running";
      if (prev.status !== n.status) ic.replaceChildren(statusIcon("tool", n.status || "running"));
      t.textContent = n.title || "Working";
      t.classList.toggle("ag-shimmer", (n.status || "running") === "running");
      c.textContent = n.count != null ? String(n.count) : "";
      c.hidden = n.count == null;
      chip.title = n.detail || "";
      return api;
    },
  };
  return api.update(d);
}

// ---- task row: status, title, count (counts up), progress bar, detail ----
export function taskRow(d) {
  const ic = el("span", "ag-task-ic"), t = el("span", "ag-task-t"), n = el("span", "ag-task-n"), det = el("span", "ag-task-d");
  const fill = el("i", "ag-bar-fill"), bar = el("span", "ag-bar", fill);
  const row = el("div", "ag-task lq-in", ic, el("span", "ag-task-txt", t, det), n, bar);
  row.setAttribute("role", "listitem");
  const cnt = spring(0, { preset: "critical", precision: 0.4, onUpdate: (v) => { n.textContent = Math.round(v).toLocaleString(); } });
  const prog = spring(0, { preset: "smooth", precision: 0.001, onUpdate: (v) => { fill.style.transform = `scaleX(${clamp(v)})`; } });
  const api = {
    el: row, data: {},
    update(p) {
      const prev = api.data, x = (api.data = { ...prev, ...p });
      const st = x.status || "running";
      row.dataset.status = st;
      if (prev.status !== st) ic.replaceChildren(statusIcon("task", st));
      t.textContent = x.title || "";
      t.classList.toggle("ag-shimmer", st === "running");
      if (x.ref && !t.querySelector(".ag-ref")) t.append(" ", refChip(x.ref));
      det.textContent = x.detail || ""; det.hidden = !x.detail;
      if (x.count != null) { n.hidden = false; if (prev.count == null) cnt.jump(0); cnt.set(+x.count || 0); } else n.hidden = true;
      const pr = x.progress != null ? +x.progress : st === "done" ? (prev.progress != null ? 1 : null) : null;
      bar.hidden = pr == null;
      if (pr != null) prog.set(pr > 1 ? pr / 100 : pr);
      return api;
    },
  };
  return api.update(d);
}

// ---- markdown-lite with refdes chips ----
export const REF_SRC = "(?:USB-[A-Z]+|SW|LED|BZ|TP|FB|IC|CN|RN|LS|BT|MH|JP|DS|VR|RV|ANT|MK|SPK|BAT|CON|[RCLDQUJPKFYXTHM])\\d{1,4}";
function refRegex(extra) {
  const names = (extra || []).filter(Boolean).map(escRe).sort((a, b) => b.length - a.length);
  return new RegExp(`(?<![\\w-])(${names.length ? names.join("|") + "|" : ""}${REF_SRC})(?![\\w-])`, "g");
}
const chipHtml = (r) => `<button type="button" class="ag-ref" data-ref="${esc(r)}">${esc(r)}</button>`;
function inline(s, re) {
  return s.split(/(`[^`\n]+`)/g).map((p) => {
    if (p.length > 1 && p[0] === "`" && p[p.length - 1] === "`") return `<code>${esc(p.slice(1, -1))}</code>`;
    // bold spans first, then refs linked on the raw text with everything around them escaped
    return p.split(/(\*\*[^*\n]+\*\*)/g).map((b) => {
      const bold = b.length > 4 && b.startsWith("**") && b.endsWith("**");
      const x = linkRefs(bold ? b.slice(2, -2) : b, re);
      return bold ? `<strong>${x}</strong>` : x;
    }).join("");
  }).join("");
}
function linkRefs(p, re) {
  let out = "", i = 0;
  re.lastIndex = 0;
  for (let m; (m = re.exec(p)); ) { out += esc(p.slice(i, m.index)) + chipHtml(m[1]); i = m.index + m[0].length; }
  return out + esc(p.slice(i));
}
export function renderMd(text, { refs, caret = false } = {}) {
  const re = refRegex(refs);
  const lines = String(text || "").replace(/\r/g, "").split("\n");
  const blocks = [];
  let para = [], list = [];
  const flushP = () => { if (para.length) blocks.push(`<p>${para.map((l) => inline(l, re)).join("<br>")}</p>`); para = []; };
  const flushL = () => { if (list.length) blocks.push(`<ul>${list.map((l) => `<li>${inline(l, re)}</li>`).join("")}</ul>`); list = []; };
  for (const ln of lines) {
    const m = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(ln);
    if (m) { flushP(); list.push(m[1]); }
    else if (!ln.trim()) { flushP(); flushL(); }
    else { flushL(); para.push(ln); }
  }
  flushP(); flushL();
  let html = blocks.join("");
  if (caret) {
    const c = '<span class="ag-caret" aria-hidden="true"></span>';
    html = html ? html.replace(/<\/(p|li)>(<\/ul>)?$/, (all) => c + all) : c;
  }
  return html;
}

// ---- streamed answer ----
export function streamAnswer({ ref, onFollowup } = {}) {
  const text = el("div", "ag-ans-text");
  const meta = el("div", "ag-ans-meta");
  const ments = el("div", "ag-ans-ments");
  const follow = el("div", "ag-ans-follow");
  const root = el("div", "ag-ans lq-in", text, ments, meta, follow);
  root.setAttribute("aria-live", "polite");
  meta.hidden = ments.hidden = follow.hidden = true;
  // words arrive in bursts from the engine; they are shown at an even, readable pace (one every WORD_MS)
  const WORD_MS = 40;
  let buf = "", shown = 0, raf = 0, pace = 0, live = true, pendingEnd = null, refs = ref ? [ref] : [];
  const paint = () => { raf = 0; text.innerHTML = renderMd(buf.slice(0, shown), { refs, caret: live }); };
  const later = () => { if (!raf) raf = requestAnimationFrame(paint); };
  const WORD = /\S+\s*|\s+/y;
  const reveal = () => {
    WORD.lastIndex = shown;
    const m = WORD.exec(buf);
    shown = m ? WORD.lastIndex : buf.length;
    later();
    if (shown >= buf.length) { clearInterval(pace); pace = 0; if (pendingEnd) { const e = pendingEnd; pendingEnd = null; finish(e); } }
  };
  function finish({ mentions, followups, seconds, thoughts } = {}) {
    live = false;
    const ms = (mentions || []).map((m) => (typeof m === "string" ? { ref: m } : m)).filter((m) => m && m.ref);
    refs = [...new Set([...refs, ...ms.map((m) => m.ref)])];
    if (raf) cancelAnimationFrame(raf);
    paint();
    // parts already linked in the text (or just the selected one) need no extra row
    const said = new Set([...text.querySelectorAll(".ag-ref")].map((c) => c.dataset.ref));
    const extra = ms.filter((m) => !said.has(m.ref) && !(ms.length === 1 && m.ref === ref));
    if (extra.length) {
      ments.hidden = false;
      ments.replaceChildren(el("span", "ag-ans-k", "Also"), ...extra.map((m) => { const c = refChip(m.ref); if (m.label) { c.append(el("span", "ag-ref-l", m.label)); } return c; }));
    }
    const bits = [];
    if (seconds != null) bits.push(`Answered in ${fmtSecs(seconds)}`);
    const nT = Array.isArray(thoughts) ? thoughts.length : thoughts;
    if (nT) bits.push(`${nT} thought${nT === 1 ? "" : "s"}`);
    if (bits.length) {
      meta.hidden = false;
      meta.replaceChildren(el("span", "", bits.join(" · ")));
      if (Array.isArray(thoughts) && thoughts.length) {
        const ol = el("ol", "ag-ans-th", ...thoughts.map((t) => el("li", "", String(t))));
        ol.hidden = true;
        const b = el("button", "ag-ans-show", "Show thinking");
        b.type = "button"; b.setAttribute("aria-expanded", "false");
        b.addEventListener("click", () => { ol.hidden = !ol.hidden; b.setAttribute("aria-expanded", ol.hidden ? "false" : "true"); b.textContent = ol.hidden ? "Show thinking" : "Hide thinking"; });
        meta.append(el("span", "", " · "), b);
        root.insertBefore(ol, follow);
      }
    }
    // at most two short follow-ups
    const short = (followups || []).map(String).filter((f) => f.trim().split(/\s+/).length <= 5).slice(0, 2);
    if (short.length) {
      follow.hidden = false;
      follow.replaceChildren(...short.map((f) => {
          const b = el("button", "ag-follow lq-btn lq-in", f);
          b.type = "button"; stagger(b);
          squish(b);
          b.addEventListener("click", () => onFollowup && onFollowup(f));
          return b;
      }));
    }
    root.dispatchEvent(new CustomEvent("ag-ended", { bubbles: true }));
  }
  paint();
  return {
    el: root,
    get text() { return buf; },
    get live() { return live; },
    append(chunk) {
      buf += String(chunk ?? "");
      if (reducedMotion()) { shown = buf.length; later(); return; }
      if (!pace) { reveal(); pace = setInterval(reveal, WORD_MS); }
    },
    end(info = {}) { if (shown < buf.length) pendingEnd = info; else finish(info); },
  };
}

// ---- mentions: one delegated listener; hover fires once per chip, clicks fire with hover undefined ----
export function bindMentions(root, cb) {
  let cur = null;
  const refOf = (t) => { const c = t && t.closest ? t.closest(".ag-ref") : null; return c && root.contains(c) ? c.dataset.ref : null; };
  const over = (e) => { const r = refOf(e.target); if (r !== cur) { if (cur) cb(cur, { hover: false }); cur = r; if (r) cb(r, { hover: true }); } };
  const out = (e) => { if (!cur) return; const r = refOf(e.relatedTarget); if (r !== cur) { cb(cur, { hover: false }); cur = r; if (r) cb(r, { hover: true }); } };
  const click = (e) => { const r = refOf(e.target); if (r) { e.preventDefault(); cb(r, {}); } };
  const fin = (e) => { const r = refOf(e.target); if (r && r !== cur) { if (cur) cb(cur, { hover: false }); cur = r; cb(r, { hover: true }); } };
  const fout = (e) => { if (cur && refOf(e.target) === cur && refOf(e.relatedTarget) !== cur) { cb(cur, { hover: false }); cur = null; } };
  root.addEventListener("pointerover", over);
  root.addEventListener("pointerout", out);
  root.addEventListener("click", click);
  root.addEventListener("focusin", fin);
  root.addEventListener("focusout", fout);
  return () => { for (const [t, f] of [["pointerover", over], ["pointerout", out], ["click", click], ["focusin", fin], ["focusout", fout]]) root.removeEventListener(t, f); };
}

// ---- cards ----
function cardShell(cls, kicker) {
  const root = el("div", "ag-card lq-in " + cls);
  root.setAttribute("role", "group");
  const head = el("div", "ag-card-head", el("span", "ag-card-k", kicker));
  root.append(head);
  return { root, head };
}
function settleCard(root, summary) {
  root.classList.add("settled");
  root.querySelectorAll("button").forEach((b) => (b.disabled = true));
  const s = el("div", "ag-card-sum lq-in", icon("done", "is-done"), el("span", "", summary));
  root.append(s);
}
const optOf = (o, i) => (typeof o === "string" ? { value: o, label: o } : { value: o.value ?? o.id ?? o.label ?? String(i), label: o.label ?? o.value ?? o.id ?? String(o), detail: o.detail });

/** approvalCard({ question, options, multi }) -> { el, promise, cancel() }; resolves string[] or null (skipped). */
export function approvalCard({ question = "", options = [], multi = false, kicker = "Your call" } = {}) {
  const { root } = cardShell("ag-ask", kicker);
  const qid = "ag-q-" + Math.random().toString(36).slice(2, 8);
  const q = el("div", "ag-card-q", question); q.id = qid;
  root.setAttribute("aria-labelledby", qid);
  const opts = options.map(optOf);
  const box = el("div", "ag-opts");
  box.setAttribute("role", multi ? "group" : "radiogroup");
  box.setAttribute("aria-labelledby", qid);
  const picked = new Set();
  const btns = opts.map((o, i) => {
    const b = el("button", "ag-opt lq-btn", el("span", "ag-opt-box", icon("done")), el("span", "ag-opt-txt", el("span", "ag-opt-l", o.label), o.detail ? el("span", "ag-opt-d", o.detail) : null));
    b.type = "button";
    b.setAttribute("role", multi ? "checkbox" : "radio");
    b.setAttribute("aria-checked", "false");
    b.style.animationDelay = i * 50 + "ms";
    b.classList.add("lq-in");
    squish(b, { amount: 0.6 });
    b.addEventListener("click", () => {
      if (multi) { if (picked.has(o.value)) picked.delete(o.value); else picked.add(o.value); }
      else { picked.clear(); picked.add(o.value); }
      paint();
    });
    return b;
  });
  box.append(...btns);
  const skip = el("button", "ag-btn ghost lq-btn", "Skip"), go = el("button", "ag-btn primary lq-btn", "Continue");
  skip.type = go.type = "button";
  squish(skip); squish(go);
  const acts = el("div", "ag-card-acts", skip, go);
  root.append(q, box, acts);
  const paint = () => {
    btns.forEach((b, i) => { const on = picked.has(opts[i].value); b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false"); });
    go.disabled = picked.size === 0;
  };
  paint();
  let done;
  const promise = new Promise((r) => (done = r));
  let open = true;
  const finish = (val) => {
    if (!open) return; open = false;
    settleCard(root, val ? opts.filter((o) => val.includes(o.value)).map((o) => o.label).join(", ") : "Skipped");
    done(val);
  };
  skip.addEventListener("click", () => finish(null));
  go.addEventListener("click", () => { if (picked.size) finish(opts.map((o) => o.value).filter((v) => picked.has(v))); });
  return { el: root, promise, cancel: () => finish(null), get open() { return open; }, focus: () => (btns[0] || go).focus() };
}

/** recommendCard({ title, detail, confidence 0..1 or 0..100, actions }) -> { el, promise, cancel() }; resolves an action or null.
 *  One line of title, one line of detail, a small confidence pill, Accept / Alternatives and a dismiss x. An action
 *  with id cancel / dismiss / skip becomes the x. */
export function recommendCard({ title = "", detail = "", confidence = null, actions } = {}) {
  const root = el("div", "ag-card ag-rec lq-in");
  root.setAttribute("role", "group");
  const conf = confidence == null ? null : clamp(+confidence > 1 ? +confidence / 100 : +confidence);
  const q = el("div", "ag-rec-t", title);
  q.title = title;
  root.setAttribute("aria-label", title);
  const close = el("button", "ag-card-x lq-btn", icon("close"));
  close.type = "button"; close.setAttribute("aria-label", "Dismiss");
  squish(close);
  const head = el("div", "ag-rec-head", q);
  if (conf != null) {
    const pill = el("span", "ag-rec-pct", Math.round(conf * 100) + "%");
    pill.dataset.level = conf >= 0.75 ? "high" : conf >= 0.45 ? "mid" : "low";
    pill.title = "How sure Etch is that this works";
    pill.setAttribute("aria-label", `Confidence ${Math.round(conf * 100)}%`);
    head.append(pill);
  }
  head.append(close);
  root.append(head);
  if (detail) { const d = el("div", "ag-rec-d", detail); d.title = detail; root.append(d); }
  const norm = (actions && actions.length ? actions : [{ id: "accept", label: "Accept" }, { id: "alternatives", label: "Alternatives" }])
    .map((a, i) => (typeof a === "string" ? { id: a, label: a, primary: i === 0 } : { id: a.id ?? a.value ?? a.label, label: a.label ?? a.id, primary: a.primary ?? i === 0 }));
  const isX = (a) => /^(cancel|dismiss|skip|no)$/i.test(String(a.id));
  const xAct = norm.find(isX);
  const acts = norm.filter((a) => !isX(a));
  let done; const promise = new Promise((r) => (done = r));
  let open = true;
  const SAID = { accept: "Accepted", alternatives: "Asked for alternatives" };
  const finish = (v, label) => {
    if (!open) return; open = false;
    root.classList.add("settled");
    root.querySelectorAll("button").forEach((b) => (b.disabled = true));
    const s = el("div", "ag-card-sum lq-in", icon(v == null || isX({ id: v }) ? "close" : "done", v == null || isX({ id: v }) ? "" : "is-done"), el("span", "", v == null || isX({ id: v }) ? "Dismissed" : SAID[v] || label));
    root.append(s);
    done(v);
  };
  const row = el("div", "ag-card-acts", ...acts.map((a) => {
    const b = el("button", "ag-btn lq-btn " + (a.primary ? "primary" : "soft"), a.label);
    b.type = "button"; squish(b);
    b.addEventListener("click", () => finish(a.id, a.label));
    return b;
  }));
  root.append(row);
  close.addEventListener("click", () => finish(xAct ? xAct.id : null));
  return { el: root, promise, cancel: () => finish(null), get open() { return open; }, focus: () => row.querySelector("button")?.focus() };
}

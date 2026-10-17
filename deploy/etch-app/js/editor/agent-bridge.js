// Agent bridge: turns the board editor's questions and change requests into Etch API calls (deploy/API.md) and shows
// every step of the work in the dock: steps, tool chips, task rows, the streamed answer, spoken lines and states.
//
//   const bridge = createAgentBridge({ dock, api, board, viewer, highlight, select });
//   bridge.ask("What does this do?", { ref: "U6" });      // POST /etch/ask, streamed into the dock
//   bridge.change("add a buzzer", { ref: "J1" });         // recommend card, then /etch/live or /etch/change + /etch/job
//   bridge.design("an ESP32 weather station");           // a new board: POST /etch/live {mode:"design"} + /etch/job
//   const load = bridge.onLoad([{ id: "board", title: "Loading the board" }, …]); load.done("board");
//   bridge.dispose();
//
// With ?mock=1, no backend, or a mock job id, change() and design() play a scripted build (the "full successful design
// run" from API.md, with a failed DRC on the first try) so the whole flow can be shown without the engine. Both hand
// the finished run to viewer.reload({ run, design, done }): on render:done (the GLB is there) and on lead:done ok.
// Page hooks besides onEvent: onStage("Picking parts") when the job's own stage changes (the build loader's label),
// onLost(msg) when the build is lost (gone from the server, or the engine stays unreachable past the grace).

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const cap = (s) => { s = String(s || "").trim(); return s ? s[0].toUpperCase() + s.slice(1) : s; };
const plain = (md) => String(md || "").replace(/\*\*|`/g, "").replace(/\s+/g, " ").trim();
let seq = 0;
// an event field that may be a list of part objects ({ref, piece, value, …}), strings or a count, as short text
function refList(list, max = 8) {
  const refs = (Array.isArray(list) ? list : []).map((p) => (typeof p === "string" ? p.split(/\s+/)[0] : p?.ref || p?.n || p?.value || "")).filter(Boolean);
  // R1, R2, R3, R4 -> R1–R4
  const groups = new Map(), other = [];
  for (const r of refs) { const m = /^([A-Za-z_]+)(\d+)$/.exec(r); if (m) { if (!groups.has(m[1])) groups.set(m[1], []); groups.get(m[1]).push(+m[2]); } else other.push(r); }
  const out = [];
  for (const [p, ns] of groups) {
    const u = [...new Set(ns)].sort((a, b) => a - b);
    for (let i = 0; i < u.length; i++) { let j = i; while (j + 1 < u.length && u[j + 1] === u[j] + 1) j++; out.push(j - i >= 2 ? `${p}${u[i]}–${p}${u[j]}` : j > i ? `${p}${u[i]}, ${p}${u[j]}` : `${p}${u[i]}`); i = j; }
  }
  const all = [...out, ...other];
  return all.length > max ? `${all.slice(0, max).join(", ")} and ${all.length - max} more` : all.join(", ");
}
const countOf = (v) => (Array.isArray(v) ? v.length : typeof v === "number" ? v : 0);
// a gate's value as a few words: numbers and strings as they are, lists as a count, objects by their first fields
function brief(v) {
  if (v == null || v === "") return "";
  if (Array.isArray(v)) return v.length ? `${v.length} item${v.length === 1 ? "" : "s"}` : "none";
  if (typeof v === "object") return Object.entries(v).slice(0, 2).map(([k, x]) => `${k} ${typeof x === "object" ? countOf(x) || "" : x}`.trim()).join(", ");
  const t = String(v); return t.length > 60 ? t.slice(0, 58) + "…" : t;
}
const nid = (p) => `${p}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

// the first one or two sentences of an answer, for the spoken line
function firstSentences(md, n = 2) {
  const s = plain(md).match(/[^.!?]+[.!?]+(\s|$)|[^.!?]+$/g) || [];
  let out = "";
  for (const x of s.slice(0, n)) { if (out && (out + x).length > 220) break; out += x; }
  return out.trim().slice(0, 300);
}

// what a change request becomes on the recommend card: "add a buzzer" + J1 -> "Add a buzzer near J1?"
function summarize(text, ref) {
  let t = String(text || "").replace(/\s+/g, " ").trim().replace(/[.!?]+$/, "");
  if (ref && !new RegExp(`\\b${ref}\\b`, "i").test(t)) {
    if (/\bit\b/i.test(t)) t = t.replace(/\bit\b/i, ref);
    else if (/^(add|put|place)\b/i.test(t)) t += ` near ${ref}`;
    else t = `${ref}: ${t}`;
  }
  return cap(t) + "?";
}
function confidence(text, ref, kind) {
  const t = String(text || "").toLowerCase();
  let c = 0.74;
  if (ref) c += 0.08;
  if (/^(add|remove|replace|swap|make|change|move|use)\b/.test(t)) c += 0.05;
  if (/\b(better|nicer|improve|optimi[sz]e|best|somehow|whatever)\b/.test(t)) c -= 0.12;
  if (t.length > 160) c -= 0.06;
  if (t.split(/\s+/).length < 3) c -= 0.05;
  if (kind === "gh") c -= 0.05;
  return Math.round(Math.min(0.9, Math.max(0.6, c)) * 100) / 100;
}

// ---- benchmarks: units, labels and the sim.json numbers (API.md: picker pick benchmarks, benchmarks check, sim.json) ----
// "Check that D1 blinks 1-3 Hz", "test whether …": a bench run, not a question or a change
const BENCH = /^(please\s+|can you\s+|etch,?\s+)*(check|test|verify|measure|benchmark)\s+(that|whether|if|the|how)\b/i;
function unitOf(check) {
  const c = String(check || "").toLowerCase();
  if (/hz|blink|freq|rate/.test(c)) return "Hz";
  if (/_ma\b|ma_|current|_ma$/.test(c)) return "mA";
  if (/duty/.test(c)) return "%";
  if (/volt|_v\b|_v$/.test(c)) return "V";
  return "";
}
function labelOf(check) {
  const c = String(check || "");
  const m = /^([A-Z]+\d+)[_:]?(.*)$/.exec(c);
  const what = (x) => (/blink|hz/i.test(x) ? "blink rate" : /peak/i.test(x) ? "peak current" : /ma|current/i.test(x) ? "current" : /duty/i.test(x) ? "duty" : x.replace(/_/g, " "));
  if (m) return `${m[1]} ${what(m[2])}`.trim();
  if (/^supply/i.test(c)) return /peak/i.test(c) ? "Supply peak" : "Supply current";
  return cap(c.replace(/_/g, " "));
}
const num = (v) => (v == null || v === "" || !isFinite(+v) ? null : +v);
const judge = (v, min, max) => (v == null ? "unmeasured" : min == null && max == null ? "measured" : (min == null || v >= +min) && (max == null || v <= +max) ? "pass" : "fail");
// the value a planned benchmark key names inside sim.json, if any
function simValue(sim, key) {
  if (!sim) return null;
  const k = String(key || "");
  if (/^supply_ma_(avg|peak)$/i.test(k)) return num(sim[k.toLowerCase()]);
  const m = /^([A-Z]+\d+)[_:](blink_hz|avg_ma|peak_ma|duty|hz|ma)$/i.exec(k);
  if (!m || !sim.leds || !sim.leds[m[1]]) return null;
  const f = { hz: "blink_hz", ma: "avg_ma" }[m[2].toLowerCase()] || m[2].toLowerCase();
  const v = num(sim.leds[m[1]][f]);
  return f === "duty" && v != null && v <= 1 ? v * 100 : v;
}

// ---- the scripted build for mock mode ----
const PREFIX = [[/buzzer|beeper|piezo/i, "BZ", "Buzzer"], [/\bled\b|light/i, "D", "LED"], [/resistor/i, "R", "Resistor"],
  [/capacitor|cap\b/i, "C", "Capacitor"], [/button|switch/i, "SW", "Push button"], [/connector|header|jack/i, "J", "Connector"],
  [/crystal|oscillator/i, "Y", "Crystal"], [/sensor/i, "U", "Sensor"]];
function newPart(text, parts) {
  const [, p, name] = PREFIX.find(([re]) => re.test(text)) || [null, "U", "Part"];
  let n = 1; while (parts && parts[p + n]) n++;
  return { ref: p + n, name };
}
// a new board from a description: the parts a small powered board with a microcontroller would pick
const DESIGN_PARTS = [["U1", "ESP32-WROOM-32E", "controller"], ["J1", "USB-C receptacle", "power"], ["U2", "AP2112K-3.3", "power"],
  ["C1", "10 µF", "power"], ["C2", "100 nF", "controller"], ["R1", "10 kΩ", "controller"], ["D1", "LED 0603", "status"], ["R2", "1 kΩ", "status"], ["SW1", "Tactile switch", "controller"]];
function simulate({ text, ref, board, design = false, startedAt = 0 }) {
  const parts = board?.parts || {};
  const adding = !design && /^(add|put|place|include)\b/i.test(String(text).trim());
  const add = adding ? newPart(text, parts) : { ref: ref || Object.keys(parts)[0] || "U1", name: (ref && parts[ref]?.value) || "Part" };
  const refs = Object.keys(parts).slice(0, 6);
  const run = "live-" + Date.now();
  const E = []; let t = 0;
  // what the firmware sim would report for this board (sim.json)
  const simData = design ? { leds: { D1: { peak_ma: 6.2, avg_ma: 3.1, blink_hz: 2.0, duty: 0.5 } }, supply_ma_avg: 84, supply_ma_peak: 236 }
    : { leds: {}, supply_ma_avg: 162, supply_ma_peak: 410 };
  const at = (dt, agent, type, f = {}, stage) => { t += dt; E.push({ ev: { agent, type, t: Math.round(t * 100) / 100, run, ...f }, stage }); };
  const lib = ["Device", "Buzzer_Beeper", "Connector", "LED_SMD", "Resistor_SMD", "Capacitor_SMD"];
  at(0.1, "lead", "start", design ? { ask: text } : { ask: text, base: `/engine/out/${board?.id || run}` }, "starting the engine");
  at(0.3, "lead", "say", { text: design ? "On it. I'll screen KiCad's parts for this, pick them, then place, route and check the board." : adding ? `On it. I'll add ${add.name.toLowerCase()} ${add.ref}${ref ? ` near ${ref}` : ""}, then place, route and check the board again.` : `On it. I'll change ${add.ref}, then place, route and check the board again.` });
  at(0.3, "classifier", "start", { candidates: 1840, batches: 12, backend: "kicad", symbol_libraries: 214, symbols: 1840, libraries_kept: 9 }, "screening parts");
  for (let i = 0; i < 12; i++) {
    at(0.16, "classifier", "batch", { seconds: 0.15, pieces: [0, 1, 2].map((j) => ({ k: `kicad:${lib[(i + j) % 6]}:P${i}${j}`, n: `${lib[(i + j) % 6]} ${i * 3 + j}`, f: lib[(i + j) % 6], yes: (i + j) % 4 === 0 })) });
  }
  at(0.2, "classifier", "done", { yes: 9, no: 1831, seconds: 2.1, backend: "kicad", families: 4 });
  const kept = design ? DESIGN_PARTS.slice(0, 5).map(([, v]) => ({ k: "kicad:" + v, n: v, f: "" }))
    : [{ k: "kicad:Buzzer_Beeper:Buzzer", n: `${add.name} 12 mm`, f: "Buzzer_Beeper" }, { k: "kicad:Resistor_SMD:R_0603", n: "Resistor 0603", f: "Resistor_SMD" }, { k: "kicad:Device:Q_NPN", n: "NPN transistor", f: "Package_TO_SOT_SMD" }];
  at(0.2, "filter", "done", { seconds: 0.1, kept,
    rejected: design ? [{ k: "kicad:RF_Module:ESP32-S3-WROOM-1", n: "ESP32-S3-WROOM-1", f: "RF_Module", why: "more than this board needs" }]
      : [{ k: "kicad:Buzzer_Beeper:Big", n: `${add.name} 30 mm`, f: "Buzzer_Beeper", why: "too big for the board" }] });
  for (const { n } of kept) at(0.2, "pieces", "show", { k: "kicad:" + n, n, f: "", img: "" });
  at(0.3, "picker", "start", { model: "etch", kept: kept.length }, "picking parts");
  const benchmarks = design
    ? [{ check: "D1_blink_hz", min: 1, max: 3, text: "D1 blink rate" }, { check: "D1_avg_ma", min: 1, max: 10, text: "D1 current" }, { check: "supply_ma_avg", max: 120, text: "Supply current" }]
    : [{ check: "supply_ma_avg", max: 250, text: "Supply current" }, { check: "supply_ma_peak", max: 500, text: "Supply peak" }];
  at(0.7, "picker", "pick", { model: "etch", seconds: 0.7, benchmarks,
    parts: design ? DESIGN_PARTS.map(([r, v, g]) => ({ ref: r, piece: "kicad:" + v, value: v, group: g, why: g === "power" ? "powers the board" : g === "status" ? "shows it is on" : "runs what you asked for" })) : [{ ref: add.ref, piece: "kicad:" + add.name, value: add.name, group: adding ? "added" : "changed", why: "what you asked for" },
      { ref: "Q" + (Object.keys(parts).filter((r) => /^Q\d/.test(r)).length + 1), piece: "kicad:Device:Q_NPN", value: "NPN", group: "driver", why: `drives ${add.ref} from a GPIO` },
      ...refs.filter((r) => r !== add.ref).map((r) => ({ ref: r, piece: "", value: parts[r]?.value || "", group: "kept", why: "already on the board" }))] });
  at(0.2, "pieces", "confirm", { parts: design ? DESIGN_PARTS.map(([r, v]) => ({ k: "kicad:" + v, refs: [r], img: "" })) : [{ k: "kicad:" + add.name, refs: [add.ref], img: "" }], drop: [] });
  if (!design) at(0.2, "assembly", "change", { added: adding ? [`${add.ref} ${add.name}`] : [], kept: Object.keys(parts).length });
  const gates = ["ERC", "DRC", "unconnected", "parity", "routed", "3D models"];
  for (const it of [1, 2]) {
    at(0.3, "assembly", "iteration", { iteration: it, phase: "place-route-check" }, it === 1 ? "placing and routing" : `placing and routing, try ${it}`);
    for (const g of gates) {
      const bad = it === 1 && g === "DRC";
      at(0.15, "assembly", "gate", { iteration: it, gate: g, ok: !bad, value: bad ? "2 clearance errors" : g === "routed" ? "100%" : "0" }, "checking");
    }
    // the benchmarks the picker planned are gates too (the firmware sim measures them on every try)
    if (it === 2) for (const b of benchmarks) { const v = simValue(simData, b.check); at(0.12, "assembly", "gate", { iteration: it, gate: "benchmark", check: b.check, ok: judge(v, b.min, b.max) === "pass", value: v }, "checking"); }
    at(0.3, "assembly", "built", { iteration: it, seconds: 1.4, board: { w: 52, h: 38 }, tracks: 118 + it * 4, stages_s: {} });
    at(0.2, "assembly", "iteration", { iteration: it, phase: "decide", decision: it === 1 ? "repair" : "done", seconds: 1.9 });
    if (it === 1) at(0.5, "picker", "repair", { iteration: 1, seconds: 0.5, parts: 2 + refs.length }, "repairing");
  }
  at(0.4, "export", "done", { zip: `/engine/out/${run}/fab.zip`, zip_bytes: 184320, project: ["board.kicad_pro", "board.kicad_pcb", "board.kicad_sch"], gerbers: ["F_Cu.gbr", "B_Cu.gbr", "F_Mask.gbr", "B_Mask.gbr", "F_Silkscreen.gbr", "Edge_Cuts.gbr", "drill.drl"], bom_lines: 14, seconds: 0.4 }, "exporting files");
  at(0.2, "render", "start", { quality: "preview" }, "rendering in 3D");
  at(1.0, "render", "done", { frames: `/engine/out/${run}/frames.json`, count: 48, quality: "preview", seconds: 1.0, lock_wait_s: 0 }, "finishing");
  at(0.2, "lead", "say", { text: design ? "Done. Your board is built and every check passes after one repair." : `Done. ${add.ref} is ${adding ? "on the board" : "changed"} and every check passes after one repair.` });
  at(0.1, "lead", "done", { ok: true, iterations: 2, timings: { total_s: Math.round(t * 10) / 10 }, classifier: "kicad", route: "modify", board: `/engine/out/${run}` }, "done");
  // startedAt (epoch ms): a build resumed after the page was left picks up where the clock says it is
  const t0 = performance.now() - (startedAt ? Math.max(0, Date.now() - startedAt) : 0);
  return {
    id: "mock-" + run,
    simData,
    duration: t,
    stages: E.filter((x) => x.stage).map((x) => [x.ev.t, x.stage]),
    poll(since) {
      const el = (performance.now() - t0) / 1000;
      let n = since; while (n < E.length && E[n].ev.t <= el) n++;
      let stage = "starting the engine"; for (let i = 0; i < n; i++) if (E[i].stage) stage = E[i].stage;
      const done = n >= E.length;
      return { events: E.slice(since, n).map((x) => x.ev), next: n, done, state: done ? "done" : "running", stage, run };
    },
  };
}

// a scripted bench run (POST /etch/live {mode: "bench"}): the asked-for check plus the supply, measured on the sim
function simulateBench({ text, board }) {
  const run = "live-" + Date.now();
  const E = []; let t = 0;
  const at = (dt, agent, type, f = {}, stage) => { t += dt; E.push({ ev: { agent, type, t: Math.round(t * 100) / 100, run, ...f }, stage }); };
  const ref = (/\b([A-Z]{1,3}\d{1,3})\b/.exec(text) || [])[1] || Object.keys(board?.parts || {}).find((r) => /^D\d/.test(r)) || "D1";
  const nums = (String(text).replace(/\b[A-Z]{1,3}\d{1,3}\b/g, "").match(/\d+(?:\.\d+)?/g) || []).map(Number);
  const hz = /hz|blink|flash/i.test(text), ma = /ma\b|current/i.test(text);
  const [min, max] = nums.length >= 2 ? [Math.min(nums[0], nums[1]), Math.max(nums[0], nums[1])] : nums.length ? (/at least|above|over|more than/i.test(text) ? [nums[0], null] : [null, nums[0]]) : hz ? [1, 3] : [null, 20];
  const unit = hz ? "Hz" : ma ? "mA" : "Hz";
  const value = hz || !ma ? 2.0 : 3.1;
  at(0.2, "lead", "start", { ask: text, base: `/engine/out/${board?.id || run}` }, "starting the sim");
  at(0.4, "lead", "say", { text: `Checking ${ref} on the simulated board.` });
  const supply = /supply|board current|total current/i.test(text);
  const ask = supply ? { check: /peak/i.test(text) ? "supply_ma_peak" : "supply_ma_avg", label: /peak/i.test(text) ? "Supply peak" : "Supply current", unit: "mA", value: /peak/i.test(text) ? 236 : 84 }
    : { check: `${ref}_${unit === "Hz" ? "blink_hz" : "avg_ma"}`, label: `${ref} ${unit === "Hz" ? "blink rate" : "current"}`, unit, value };
  const ok = judge(ask.value, min, max) === "pass";
  at(1.2, "benchmarks", "check", { said: text, ...ask, min, max, pass: ok, result: ok ? "in range" : "out of range" }, "measuring");
  if (!supply) at(0.5, "benchmarks", "check", { said: "supply current", check: "supply_ma_avg", label: "Supply current", max: 250, value: 84, unit: "mA", pass: true, result: "in range" }, "measuring");
  at(0.3, "benchmarks", "done", { passed: (ok ? 1 : 0) + (supply ? 0 : 1), failed: ok ? 0 : 1, unmeasured: 0, seconds: 2.4 });
  at(0.1, "lead", "done", { ok: true, iterations: 0, timings: { total_s: Math.round(t * 10) / 10 }, route: "bench", board: `/engine/out/${run}` }, "done");
  const t0 = performance.now();
  return {
    id: "mock-bench-" + run, duration: t, stages: E.filter((x) => x.stage).map((x) => [x.ev.t, x.stage]),
    poll(since) {
      const el = (performance.now() - t0) / 1000;
      let n = since; while (n < E.length && E[n].ev.t <= el) n++;
      let stage = "starting the sim"; for (let i = 0; i < n; i++) if (E[i].stage) stage = E[i].stage;
      const done = n >= E.length;
      return { events: E.slice(since, n).map((x) => x.ev), next: n, done, state: done ? "done" : "running", stage, run };
    },
  };
}

export function createAgentBridge({ dock, api, board, viewer = null, highlight = () => {}, select = () => {}, onEvent = () => {}, onJob = () => {}, onJobEnd = () => {}, onStage = () => {}, onLost = () => {} } = {}) {
  if (!dock) throw new Error("createAgentBridge needs a dock");
  const timers = new Set();
  let disposed = false, jobGen = 0, askGen = 0;
  let current = null; // { id, sim } of the build being followed, for cancel()
  // read each time: a new board (kind "new") becomes a live-* or library board once its design build is loaded
  const kindOf = () => board?.kind || (/^live-/.test(String(board?.id)) ? "live" : "gh");
  const partCount = () => Object.keys(board?.parts || {}).length;
  const isMock = () => !!api?.status?.mock || (api?.hasBackend && !api.hasBackend());

  // a dock loader that shows how long it has been running
  function loader(label) {
    const t0 = performance.now();
    const tick = () => dock.loader(true, `${label} · ${((performance.now() - t0) / 1000).toFixed(1)} s`);
    tick();
    const iv = setInterval(tick, 100); timers.add(iv);
    return () => { clearInterval(iv); timers.delete(iv); dock.loader(false); };
  }
  // one pending "go back to idle" at a time; any new work cancels it
  let settleTm = null;
  const busy = () => { if (settleTm) { clearTimeout(settleTm); timers.delete(settleTm); settleTm = null; } };
  const later = (ms, f) => { busy(); const tm = settleTm = setTimeout(() => { timers.delete(tm); settleTm = null; if (!disposed) f(); }, ms); timers.add(tm); };
  async function speak(text, then = "idle") {
    if (!text) { dock.setState(then); return; }
    dock.setState("speaking");
    const p = dock.say(text);
    if (p && typeof p.then === "function") await p.catch(() => {});
    else await sleep(Math.min(5000, 400 + text.split(/\s+/).length * 220));
    if (!disposed) dock.setState(then);
  }
  const isNetwork = (e) => e instanceof TypeError || /reachable|failed to fetch|network|load failed/i.test(String(e?.message));
  // 401 brings up the access-code prompt through the api client; true when a code was given
  async function promptCode() {
    if (!api?.authed) return false;
    try { const r = await api.authed("/etch/auth", { method: "POST" }); return r.ok; } catch (e) { return false; }
  }
  // show an error the API.md way: limits and refusals as is (confused), no engine (offline)
  function explain(e, stepId, title, kind = "step") {
    const msg = String(e?.message || "Something went wrong.");
    if (stepId) dock.step({ id: stepId, kind, title, detail: msg, status: "failed" });
    if (isNetwork(e)) {
      dock.setState("offline"); dock.setStatus("Etch's engine is offline");
      dock.say("I can't reach Etch's engine right now. Try again in a minute.");
      return;
    }
    dock.setStatus(e?.status === 429 ? "Busy" : e?.status === 503 ? "Out of answers" : "Could not do that");
    dock.setState("confused");
    dock.say(msg);
    later(4000, () => dock.setState("idle"));
  }

  function followups(ref, mentions) {
    const out = [];
    if (ref) out.push(`What does ${ref} connect to?`, `Can I replace ${ref} with something cheaper?`);
    else out.push("What is the main chip?", "How is this board powered?");
    const other = (mentions || []).find((m) => m !== ref);
    if (other) out.push(`What does ${other} do?`);
    return out.slice(0, 3);
  }

  async function ask(text, { ref = null } = {}, retried = false) {
    if (!retried && BENCH.test(String(text || "").trim())) return bench(text);
    const gen = ++askGen;
    busy();
    const id = nid("ask");
    const readId = id + ":read";
    const readTitle = ref ? `Reading ${ref} and its neighbours` : "Reading the parts list";
    dock.setState("thinking");
    dock.setStatus(ref ? `Looking at ${ref}` : "Looking at the board");
    dock.step({ id: readId, kind: "step", title: readTitle, detail: ref ? board?.parts?.[ref]?.value || "" : `${partCount()} parts`, status: "running", ref: ref || undefined });
    const stop = loader("Thinking");
    let r;
    try {
      const body = { board: board?.id, question: text };
      if (ref) body.ref = ref;
      r = await api.ask(body);
    } catch (e) {
      stop();
      if (e?.status === 401 && !retried) {
        dock.step({ id: readId, kind: "step", title: readTitle, detail: "Needs your access code", status: "failed" });
        dock.setStatus("Waiting for your access code");
        if (await promptCode()) return ask(text, { ref }, true);
      }
      explain(e, readId, readTitle);
      return null;
    }
    stop();
    if (disposed || gen !== askGen) return r;
    dock.step({ id: readId, kind: "step", title: readTitle, detail: ref ? board?.parts?.[ref]?.value || "" : `${partCount()} parts`, status: "done", ref: ref || undefined });
    const thoughts = r.thoughts || [];
    thoughts.forEach((t, i) => dock.step({ id: `${id}:t${i}`, kind: "reasoning", title: t, status: "done" }));
    const mentions = r.mentions || [];
    const first = mentions.find((m) => m !== ref) || mentions[0] || ref;
    if (first) { highlight(first); dock.lookAt?.(first); }
    dock.setStatus("Answering");
    dock.answerStart(id, { ref });
    for (const w of String(r.answer || "").match(/\S+\s*|\s+/g) || []) {
      if (disposed || gen !== askGen) break;
      dock.answerAppend(id, w);
      await sleep(25);
    }
    dock.answerEnd(id, { mentions, followups: followups(ref, mentions), seconds: r.seconds, thoughts });
    if (disposed || gen !== askGen) return r;
    dock.setStatus("");
    await speak(firstSentences(r.answer));
    return r;
  }

  // ---- change(): recommend card, start the run, follow /etch/job ----
  async function change(text, { ref = null, at = null } = {}) {
    if (BENCH.test(String(text || "").trim())) return bench(text);
    const rid = nid("chg");
    busy();
    dock.setState("thinking");
    dock.setStatus("Planning the change");
    const live = kindOf() === "live";
    const choice = await dock.recommend({ id: rid, title: summarize(text, ref), confidence: confidence(text, ref, kindOf()),
      detail: live
        ? "Etch adds it to this design, then places, routes and checks the whole board again and renders the new version. About a minute."
        : "Etch first turns this board into its own design (its licence and parts must allow that), then makes the change on the copy and checks it.",
      actions: [{ id: "accept", label: "Accept" }, { id: "alternatives", label: "Alternatives" }, { id: "cancel", label: "Cancel" }] });
    if (disposed) return null;
    const pick = String(choice?.id ?? choice ?? "").toLowerCase();
    if (pick === "alternatives") {
      const opts = [`${cap(text)}, keeping the layout as close as it is`, `${cap(text)}, as small as it can be`, "Just tell me how I would do it"];
      const ans = await dock.ask({ id: rid + ":alt", question: "Which would you rather?", options: opts, multi: false });
      if (disposed) return null;
      const a = String(Array.isArray(ans) ? ans[0] : ans?.label ?? ans ?? "");
      if (!a) return cancelled(rid);
      if (a === opts[2]) return ask(`How would I ${text.replace(/[.?!]+$/, "")}?`, { ref });
      return run(a, ref, at);
    }
    if (pick !== "accept") return cancelled(rid);
    return run(text, ref, at);
  }
  function cancelled(rid) {
    dock.step({ id: rid + ":cancel", kind: "step", title: "Change cancelled", status: "done" });
    dock.setStatus(""); dock.setState("idle");
    return null;
  }

  async function run(text, ref, at, retried = false) {
    const gen = ++jobGen;
    busy();
    const sid = nid("send");
    const kind = kindOf();
    const title = kind === "live" ? "Sending the change to Etch" : "Copying the board into Etch's own design";
    dock.setState("working");
    dock.setStatus("Starting the change");
    dock.step({ id: sid, kind: "tool", title, detail: kind === "live" ? `modify ${board?.id}` : board?.id, status: "running", ref: ref || undefined });
    let sim = null, j;
    const stop = loader("Starting");
    try {
      if (isMock()) {
        await sleep(500);
        if (board?.meta?.copyable === false) { const e = new Error("This board's licence does not allow copying it, so Etch cannot change it."); e.status = 403; throw e; }
        sim = simulate({ text, ref, board });
        j = { job: sim.id };
      } else if (kind === "live") {
        j = await api.changeLive({ run: board.id, text: ref && !new RegExp(`\\b${ref}\\b`).test(text) ? `${ref}: ${text}` : text });
      } else {
        const body = { board: board.id, text };
        if (ref) body.ref = ref;
        j = await api.change(body);
      }
      if (!sim && String(j.job || "").startsWith("mock-")) { sim = simulate({ text, ref, board }); j.job = sim.id; }
    } catch (e) {
      stop();
      if ((e?.status === 401 || /access code/i.test(e?.message)) && !retried) {
        dock.step({ id: sid, kind: "tool", title, detail: "Needs your access code", status: "failed" });
        if (await promptCode()) return run(text, ref, at, true);
      }
      explain(e, sid, title, "tool");
      return null;
    }
    stop();
    dock.step({ id: sid, kind: "tool", title, detail: `job ${j.job}`, status: "done" });
    jobStarted(j, sim, { text, ref, design: false });
    return follow(j.job, sim, gen, { text, ref, at });
  }

  // A backend restart (deploy/restart.sh also bounces the tunnel) takes the engine away for 20 s or so: keep
  // following through FOLLOW_GRACE_MS of misses. A run that ends with no lead:done / lead:error / cancel (the
  // job was lost, job gbRVE5w_9kCwfMDt 10-17) gets a failed step, so the trace never reads "Done" over an
  // empty board.
  const FOLLOW_GRACE_MS = 90000;
  async function follow(jobId, sim, gen, req) {
    const map = mapper(jobId, { design: !!req?.design, bench: !!req?.bench, simData: sim?.simData || null });
    current = { id: jobId, sim, gen };
    let since = 0, down = 0, last = null, stage = "", ended = false;
    const lost = (msg) => {
      dock.step({ id: jobId + ":lost", kind: "step", title: "Lost track of this build", detail: msg, status: "failed" });
      dock.setState("confused"); dock.say(msg); dock.setStatus("Build lost");
      try { onLost(msg); } catch (e) { /* the page's own business */ }
    };
    while (!disposed && gen === jobGen) {
      let r;
      try { r = sim ? sim.poll(since) : await api.job(jobId, since); down = 0; }
      catch (e) {
        if (e?.status === 404) { map.end(); lost("Etch lost track of this build. Start it again, or open it from the library."); return null; }
        if (!down) down = Date.now();
        if (Date.now() - down > FOLLOW_GRACE_MS) {
          map.end(); explain(e, jobId + ":poll", "Following the build");
          try { onLost(""); } catch (e2) { /* the page's own business */ }
          return null;
        }
        await sleep(1500); continue;
      }
      if (disposed || gen !== jobGen) break;
      for (const ev of r.events || []) { const out = map(ev); if (out) last = out; }
      if (r.stage && r.stage !== stage) {
        stage = r.stage; dock.setStatus(cap(stage));
        try { onStage(cap(stage)); } catch (e) { /* the page's own business */ }
      }
      since = r.next ?? since + (r.events || []).length;
      if (r.done) { ended = true; break; }
      await sleep(400);
    }
    map.end();
    if (ended && !map.finished()) lost("The build stopped before it finished a board. Try it again.");
    const out = last ? { ...last, job: jobId, request: req } : null;
    if (!disposed && gen === jobGen) { try { onJobEnd(out); } catch (e) { /* the page's own business */ } }
    return out;
  }
  // a build was started: the page keeps { job, token, mock clock } so it can be followed again after leaving
  function jobStarted(j, sim, req) {
    try { onJob({ job: j.job, token: j.token || "", mock: !!sim, duration: sim?.duration || 0, stages: sim?.stages || [], ...req }); } catch (e) { /* not kept */ }
  }
  // resume({ job, mock, startedAt, text, ref, design }): follow a build started before the page was left; its events
  // replay from the start (since=0), so the dock shows every step again
  function resume(stt = {}) {
    const gen = ++jobGen;
    busy();
    dock.setState("working");
    dock.setStatus("Picking the build back up");
    const sim = stt.mock || String(stt.job || "").startsWith("mock-") ? simulate({ text: stt.text || "", ref: stt.ref || null, board, design: !!stt.design, startedAt: stt.startedAt }) : null;
    return follow(sim ? sim.id : stt.job, sim, gen, { text: stt.text || "", ref: stt.ref || null, design: !!stt.design });
  }

  // one build event -> dock calls (API.md "Build events")
  function mapper(jid, { design = false, bench: benchRun = false, simData = null } = {}) {
    const S = (k) => `${jid}:${k}`;
    // the benchmarks card for this run: planned rows, gate results, bench checks, then sim.json numbers
    let card = null, simDone = false;
    const planned = new Map();
    const B = () => card || (card = dock.bench ? dock.bench(jid) : null);
    async function addSim(run) {
      if (simDone) return;
      simDone = true;
      let sim = simData;
      if (!sim && run && api?.authed) {
        try { const r = await api.authed(`/etch/files/engine/out/${run}/sim.json`); sim = r.ok ? await r.json() : null; } catch (e) { sim = null; }
      }
      if (!sim || disposed) return;
      const c = B(); if (!c) return;
      // IPC-2221 / IPC-2221B / IR drop from the build's own copper (the Benchmarks panel)
      if (run && api?.authed) api.authed(`/etch/files/engine/out/${run}/layout.json`).then((r) => (r.ok ? r.json() : null)).then((lay) => { if (lay && !disposed) dock.benchPhysics?.(jid, lay, sim); }).catch(() => {});
      // planned rows the gates did not settle take their value from the sim
      for (const [k, p] of planned) {
        const v = simValue(sim, k);
        if (v != null && (!c.has(k) || p.open)) { c.row(k, { value: v, status: judge(v, p.min, p.max) }); p.open = false; }
      }
      // then what the sim measured that no benchmark covers, with no target
      const covered = (s) => [...planned.keys()].some((k) => k.toLowerCase().startsWith(s.toLowerCase()));
      for (const [ref, l] of Object.entries(sim.leds || {})) {
        if (num(l.blink_hz) && !covered(ref + "_blink") && !covered(ref + "_hz")) c.row(`${ref}_blink_hz`, { label: `${ref} blink rate`, value: num(l.blink_hz), unit: "Hz", status: "measured" });
        if (num(l.avg_ma) != null && !covered(ref + "_avg") && !covered(ref + "_ma")) c.row(`${ref}_avg_ma`, { label: `${ref} current`, value: num(l.avg_ma), unit: "mA", status: "measured" });
      }
      if (num(sim.supply_ma_avg) != null && !covered("supply_ma_avg")) c.row("supply_ma_avg", { label: "Supply current", value: num(sim.supply_ma_avg), unit: "mA", status: "measured" });
      if (num(sim.supply_ma_peak) != null && !covered("supply_ma_peak")) c.row("supply_ma_peak", { label: "Supply peak", value: num(sim.supply_ma_peak), unit: "mA", status: "measured" });
    }
    // after a build: a nod for all pass, a wince for a fail (the numbers stay in the Benchmarks panel)
    function verdict() {
      const c = card; if (!c) return;
      const { pass, total, failed } = c.summary;
      if (!total) return;
      setTimeout(() => { if (!disposed) dock.emote?.(failed ? "wince" : "nod"); }, 900);
    }
    let batches = 0, nb = 0, yes = 0, pieces = 0, queued = false, render = null, picked = [], result = null, benched = false;
    const step = (o) => dock.step(o);
    const fn = (ev) => {
      const k = `${ev.agent}:${ev.type}`;
      try { onEvent(ev); } catch (e) { /* a page hook never stops the build view */ }
      if (queued && k !== "lead:queued") { queued = false; dock.loader(false); }
      switch (k) {
        case "lead:queued": queued = true; dock.setState("working"); dock.loader(true, `Waiting in line · ${ev.position} ahead`); break;
        case "lead:start": step({ id: S("start"), kind: "step", title: ev.base ? "Starting the change" : "Starting the build", detail: ev.ask, status: "done" }); dock.setState("working"); break;
        case "lead:say": if (ev.text) dock.say(ev.text); break;
        case "classifier:start": batches = ev.batches || 0; step({ id: S("cls"), kind: "tool", title: `Screening ${ev.candidates} KiCad parts`, detail: `${batches} batches`, status: "running", count: 0, progress: 0 }); break;
        case "classifier:batch":
          nb++; yes += (ev.pieces || []).filter((p) => p.yes).length;
          step({ id: S("cls"), kind: "tool", title: `Screening KiCad parts`, detail: `batch ${nb} of ${batches || "?"} · ${yes} fit`, status: "running", count: yes, progress: batches ? nb / batches : undefined });
          break;
        case "classifier:done":
          step({ id: S("cls"), kind: "tool", title: ev.backend === "stored board" ? "Built before, loading it" : "Screened KiCad parts", detail: `${ev.yes} fit, ${ev.no} set aside · ${ev.seconds} s`, status: "done", count: ev.yes, progress: 1 });
          break;
        case "filter:done":
          step({ id: S("filter"), kind: "tool", title: `Kept ${countOf(ev.kept)} variants, set ${countOf(ev.rejected)} aside`,
            detail: [...(ev.kept || []).map((p) => (typeof p === "string" ? p : p?.n || p?.k || "")), ...(ev.rejected || []).map((p) => (typeof p === "string" ? p : `${p?.n || p?.k || ""}${p?.why ? ` (${p.why})` : ""}`))].filter(Boolean).slice(0, 6).join(", "), status: "done" });
          break;
        case "pieces:show": pieces++; step({ id: S("pieces"), kind: "step", title: `Gathered ${pieces} candidate part${pieces > 1 ? "s" : ""}`, detail: ev.n, status: "done", count: pieces }); break;
        case "pieces:confirm": step({ id: S("pieces"), kind: "step", title: `Using ${countOf(ev.parts)} new part${countOf(ev.parts) === 1 ? "" : "s"}`, detail: refList((ev.parts || []).flatMap((p) => (Array.isArray(p?.refs) ? p.refs : [p]))) + (countOf(ev.drop) ? ` · ${countOf(ev.drop)} dropped` : ""), status: "done", count: pieces }); break;
        case "picker:start": step({ id: S("pick"), kind: "task", title: "Picking parts", detail: `from ${ev.kept} kept variants`, status: "running" }); break;
        case "picker:pick":
          picked = Array.isArray(ev.parts) ? ev.parts : [];
          for (const b of Array.isArray(ev.benchmarks) ? ev.benchmarks : []) {
            if (!b || !b.check) continue;
            const p = { label: b.text ? cap(String(b.text)).slice(0, 40) : labelOf(b.check), min: num(b.min), max: num(b.max), unit: b.unit || unitOf(b.check), open: true };
            planned.set(b.check, p);
            B()?.row(b.check, { label: p.label, min: p.min, max: p.max, unit: p.unit, status: "pending" });
          }
          step({ id: S("pick"), kind: "task", title: `Picked ${countOf(picked)} parts${ev.recipe ? " (known circuit)" : ""}`, detail: refList(picked), status: "done", count: picked.length, ref: picked[0]?.ref });
          if (ev.say) dock.say(ev.say);
          break;
        case "sourcing:start": step({ id: S("src:" + ev.what), kind: "tool", title: `Fetching ${ev.what}`, detail: ev.search, status: "running" }); break;
        case "sourcing:done": step({ id: S("src:" + ev.what), kind: "tool", title: ev.ok ? `Fetched ${ev.what}` : `Could not fetch ${ev.what}`, detail: ev.provenance?.source_url || ev.provenance?.route || "", status: ev.ok ? "done" : "failed" }); break;
        case "assembly:change": if (!(ev.added || []).length) { step({ id: S("change"), kind: "step", title: "Changing the existing parts", detail: ev.kept != null ? `${ev.kept} parts kept` : "", status: "done" }); break; }
          step({ id: S("change"), kind: "step", title: `Adding ${refList(ev.added) || "parts"}`, detail: ev.kept != null ? `${ev.kept} parts kept` : "", status: "done" }); break;
        case "assembly:iteration":
          if (ev.phase === "place-route-check") step({ id: S("try:" + ev.iteration), kind: "task", title: `Placing and routing · try ${ev.iteration}`, status: "running" });
          else if (ev.phase === "decide") {
            if (ev.decision === "done") step({ id: S("try:" + ev.iteration), kind: "task", title: `Placing and routing · try ${ev.iteration}`, detail: "every check passed", status: "done" });
            else step({ id: S("decide:" + ev.iteration), kind: "reasoning", title: ev.decision === "re-plan" ? "Checks failed, picking the parts again" : "Some checks failed, repairing the design", status: "done" });
          }
          break;
        case "assembly:gate":
          // the firmware test lives in the benchmarks card, not the step list
          if (/^firmware/i.test(String(ev.gate))) {
            const n = num(String(ev.value ?? "").match(/\d+/)?.[0]);
            B()?.row("firmware_tests", { label: ev.gate.replace(/^f/, "F"), value: n, unit: n != null ? "tests" : "", status: ev.ok ? "pass" : "fail", result: brief(ev.value) });
            break;
          }
          if (ev.check && (planned.has(ev.check) || /bench/i.test(ev.gate))) {
            const p = planned.get(ev.check) || {};
            const v = num(ev.value);
            B()?.row(ev.check, { label: p.label || labelOf(ev.check), min: p.min, max: p.max, unit: p.unit || unitOf(ev.check), value: v, status: v == null ? "unmeasured" : ev.ok ? "pass" : "fail" });
            if (planned.has(ev.check)) planned.get(ev.check).open = v == null;
          } else if (/^(erc|drc|unconnected|routed)$/i.test(String(ev.gate))) B()?.check(ev.gate, !!ev.ok);
          step({ id: S(`gate:${ev.iteration}:${ev.gate}`), kind: "step", title: `${ev.gate} ${ev.ok ? "passed" : "failed"}`, detail: ev.ok && (ev.value === 0 || ev.value === "0") ? "no issues" : brief(ev.value), status: ev.ok ? "done" : "failed" }); break;
        case "assembly:built": step({ id: S("try:" + ev.iteration), kind: "task", title: `Placing and routing · try ${ev.iteration}`, detail: [ev.board?.w ? `${Math.round(ev.board.w)}×${Math.round(ev.board.h)} mm` : "", ev.tracks != null ? `${countOf(ev.tracks) || ev.tracks} tracks` : "", ev.seconds != null ? `${Math.round(ev.seconds)} s` : ""].filter(Boolean).join(" · "), status: "done" }); break;
        case "picker:repair": {
          const n = countOf(ev.parts), refs = Array.isArray(ev.parts) ? refList(ev.parts, 6) : "";
          step({ id: S("repair:" + ev.iteration), kind: "reasoning", title: "Fixing the design after the failed checks", detail: [refs ? `${refs} reviewed` : n ? `${n} parts reviewed` : "", ev.seconds != null ? `${Math.round(ev.seconds)} s` : ""].filter(Boolean).join(" · "), status: "done" });
          break;
        }
        case "export:done": step({ id: S("export"), kind: "tool", title: "Wrote the fab files", detail: [`${countOf(ev.gerbers)} Gerbers`, ev.bom_lines != null ? `${countOf(ev.bom_lines) || ev.bom_lines} BOM lines` : "", ev.zip_bytes ? `${Math.round(ev.zip_bytes / 1024)} KB zip` : ""].filter(Boolean).join(" · "), status: "done" }); break;
        case "render:start": render = loader("Rendering in 3D"); step({ id: S("render"), kind: "task", title: "Rendering in 3D", detail: ev.quality, status: "running" }); break;
        case "render:done":
          if (render) { render(); render = null; }
          step({ id: S("render"), kind: "task", title: "Rendered in 3D", detail: [ev.count != null ? `${countOf(ev.count) || ev.count} frames` : "", ev.seconds != null ? `${Math.round(ev.seconds)} s` : ""].filter(Boolean).join(" · "), status: "done" });
          viewer?.reload?.({ run: ev.run, frames: ev.frames, design });
          addSim(ev.run);
          break;
        case "benchmarks:check": {
          const key = ev.check || ev.label || ev.said;
          const v = num(ev.value);
          B()?.row(key, { label: ev.label ? cap(String(ev.label)).slice(0, 40) : labelOf(key), min: num(ev.min), max: num(ev.max), unit: ev.unit || unitOf(key), value: v, status: ev.pass === true ? "pass" : ev.pass === false ? "fail" : v == null ? "unmeasured" : "measured", result: ev.result });
          if (planned.has(key)) planned.get(key).open = false;
        }
          step({ id: S("bench:" + (ev.check || ev.label)), kind: "task", title: brief(ev.label || ev.said || ev.check), detail: [`${brief(ev.value) || "?"} ${ev.unit || ""}`.trim(), brief(ev.result)].filter(Boolean).join(" · "), status: ev.pass === false ? "failed" : "done" }); break;
        case "benchmarks:done": benched = true; if (benchRun) verdict();
          step({ id: S("bench"), kind: "task", title: `Checks: ${countOf(ev.passed) || ev.passed || 0} passed, ${countOf(ev.failed) || ev.failed || 0} failed${ev.unmeasured ? `, ${countOf(ev.unmeasured) || ev.unmeasured} not measured` : ""}`, status: ev.failed ? "failed" : "done" }); break;
        case "lead:done":
          if (ev.ok) {
            result = { ok: true, board: ev.board, run: ev.run, parts: picked };
            if (!benchRun) Promise.resolve(addSim(ev.run || String(ev.board || "").split("/").pop())).then(verdict);
            step({ id: S("done"), kind: "step", title: benchRun ? "Checks done" : design ? "Board built" : "Board updated", detail: `${ev.iterations} tr${ev.iterations === 1 ? "y" : "ies"} · ${ev.timings?.total_s ?? "?"} s`, status: "done" });
            dock.setState("success"); dock.setStatus("Done");
            if (design) viewer?.reload?.({ run: ev.run, design, done: true });
            const added = design ? null : (picked.find((p) => p.group === "added") || picked.find((p) => p.group === "changed"))?.ref;
            if (added) { highlight(added); dock.lookAt?.(added); }
            later(2500, () => dock.setState("idle"));
          } else {
            result = { ok: false, error: ev.error, run: ev.run };
            step({ id: S("done"), kind: "step", title: "The checks did not all pass", detail: ev.error || "", status: "failed" });
            dock.setState("confused");
            if (ev.error) dock.say(ev.error);
            later(4000, () => dock.setState("idle"));
          }
          break;
        case "lead:error":
          result = { ok: false, error: ev.error };
          step({ id: S("done"), kind: "step", title: "Stopped with an error", detail: ev.error || "", status: "failed" });
          dock.setState("error"); dock.say(ev.error || "The engine stopped with an error.");
          break;
        case "lead:cancelled":
          result = { ok: false, cancelled: true };
          step({ id: S("done"), kind: "step", title: "Cancelled", status: "done" });
          dock.setState("idle"); dock.setStatus("Cancelled");
          break;
        default: return null; // events not in API.md are ignored
      }
      return result;
    };
    fn.end = () => { if (render) render(); if (queued) dock.loader(false); };
    fn.finished = () => result !== null || benched; // a benchmarks run ends on benchmarks:done
    return fn;
  }

  // ---- design(): a new board from a description, POST /etch/live {mode: "design", text}, followed like a change ----
  async function design(text, retried = false) {
    text = String(text || "").replace(/\s+/g, " ").trim();
    if (!text) return null;
    const gen = ++jobGen;
    busy();
    const sid = nid("design");
    const title = "Sending your description to Etch";
    dock.setState("working");
    dock.setStatus("Starting the build");
    dock.step({ id: sid, kind: "tool", title, detail: text.length > 90 ? text.slice(0, 88) + "…" : text, status: "running" });
    let sim = null, j;
    const stop = loader("Starting");
    try {
      if (isMock()) {
        await sleep(500);
        sim = simulate({ text, board: { parts: {} }, design: true });
        j = { job: sim.id };
      } else {
        const r = await api.authed("/etch/live", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "design", text }) });
        const b = await r.json().catch(() => ({}));
        if (!r.ok || b.ok === false) {
          const e = new Error(b.error || (r.status === 429 ? "Etch is busy right now. Try again in a bit." : `error ${r.status}`));
          e.status = r.status; throw e;
        }
        j = b;
        try { const t = JSON.parse(localStorage.getItem("etch-tokens") || "{}"); t[j.job] = j.token; localStorage.setItem("etch-tokens", JSON.stringify(t)); } catch (e) { /* not remembered */ }
      }
      if (!sim && String(j.job || "").startsWith("mock-")) { sim = simulate({ text, board: { parts: {} }, design: true }); j.job = sim.id; }
    } catch (e) {
      stop();
      if (e?.status === 401 && !retried) {
        dock.step({ id: sid, kind: "tool", title, detail: "Needs your access code", status: "failed" });
        if (await promptCode()) return design(text, true);
      }
      explain(e, sid, title, "tool");
      return null;
    }
    stop();
    dock.step({ id: sid, kind: "tool", title, detail: `job ${j.job}`, status: "done" });
    jobStarted(j, sim, { text, design: true });
    return follow(j.job, sim, gen, { text, design: true });
  }

  // ---- bench(): re-run the benchmarks on this build, POST /etch/live {mode: "bench", text, base} ----
  async function bench(text) {
    text = String(text || "").replace(/\s+/g, " ").trim();
    const gen = ++jobGen;
    busy();
    const sid = nid("bench");
    const title = "Sending the check to Etch";
    const kind = kindOf();
    dock.setState("working");
    dock.setStatus("Running the check");
    if (!isMock() && kind !== "live") {
      // an imported board has no Etch build to simulate yet
      dock.step({ id: sid, kind: "tool", title: "Benchmarks need an Etch build", detail: "Change or build this board with Etch first; then its sim can be checked.", status: "failed" });
      dock.setStatus(""); dock.setState("confused");
      dock.say("Benchmarks run on an Etch build. Ask for a change to this board first, then I can check it.");
      later(4000, () => dock.setState("idle"));
      return null;
    }
    dock.step({ id: sid, kind: "tool", title, detail: text, status: "running" });
    let sim = null, j;
    const stop = loader("Starting");
    try {
      if (isMock()) { await sleep(400); sim = simulateBench({ text, board }); j = { job: sim.id }; }
      else {
        const r = await api.authed("/etch/live", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "bench", text, base: `/engine/out/${board.id}` }) });
        j = await r.json().catch(() => ({}));
        if (!r.ok || j.ok === false) { const e = new Error(j.error || `error ${r.status}`); e.status = r.status; throw e; }
      }
    } catch (e) {
      stop();
      explain(e, sid, title, "tool");
      return null;
    }
    stop();
    dock.step({ id: sid, kind: "tool", title, detail: `job ${j.job}`, status: "done" });
    return follow(j.job, sim, gen, { text, bench: true });
  }

  // ---- onLoad(): the page-load work as agent steps ----
  function onLoad(steps = []) {
    const id = nid("load");
    const list = steps.map((s, i) => ({ id: s.id || `${id}:${i}`, title: s.title, detail: s.detail || "", status: s.status || null, kind: s.kind || "step" }));
    let stop = null, loading = null;
    const draw = () => {
      if (disposed) return;
      let shownRunning = false;
      for (const s of list) {
        if (s.status === "done" || s.status === "failed") { dock.step({ id: s.id, kind: s.kind, title: s.title, detail: s.detail, status: s.status }); continue; }
        if (shownRunning) break; // later steps appear as the earlier ones finish
        dock.step({ id: s.id, kind: s.kind, title: s.title, detail: s.detail, status: "running" });
        shownRunning = true;
        if (loading !== s.id) { if (stop) stop(); stop = loader(s.title); loading = s.id; }
      }
      if (!shownRunning) {
        if (stop) { stop(); stop = null; loading = null; }
        const failed = list.some((s) => s.status === "failed");
        dock.setState(failed ? "confused" : "idle");
        dock.setStatus(failed ? "Something did not load" : "");
      } else {
        dock.setState("working");
        dock.setStatus(list.find((s) => !s.status || s.status === "running")?.title || "");
      }
    };
    const find = (k) => (typeof k === "number" ? list[k] : list.find((s) => s.id === k)) || list.find((s) => s.status !== "done" && s.status !== "failed");
    const set = (k, status, patch = {}) => { const s = find(k); if (s) { Object.assign(s, patch, status ? { status } : {}); draw(); } };
    draw();
    return {
      done: (k, detail) => set(k, "done", detail != null ? { detail } : {}),
      fail: (k, detail) => set(k, "failed", detail != null ? { detail } : {}),
      update: (k, patch) => set(k, null, patch),
      add: (s) => { list.push({ id: s.id || `${id}:${list.length}`, title: s.title, detail: s.detail || "", status: s.status || null, kind: s.kind || "step" }); draw(); },
    };
  }

  // the cancel button: a real build is stopped on the engine (its lead:cancelled event then ends the follow); a mock
  // build just stops being followed
  async function cancel() {
    const c = current;
    if (!c || c.gen !== jobGen) return { ok: false };
    if (c.sim) { jobGen++; current = null; dock.loader(false); dock.setState("idle"); dock.setStatus("Cancelled"); return { ok: true }; }
    const r = await api.cancel(c.id);
    if (r && r.ok === false && r.error) throw new Error(r.error);
    return { ok: true };
  }

  function dispose() {
    disposed = true; jobGen++; askGen++;
    for (const t of timers) { clearTimeout(t); clearInterval(t); }
    timers.clear();
    try { dock.loader(false); } catch (e) { /* dock already gone */ }
  }

  return { ask, change, design, bench, resume, onLoad, cancel, dispose };
}

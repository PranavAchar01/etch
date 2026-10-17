// Agent bridge: turns the board editor's questions and change requests into Etch API calls (deploy/API.md) and shows
// every step of the work in the dock: steps, tool chips, task rows, the streamed answer, spoken lines and states.
//
//   const bridge = createAgentBridge({ dock, api, board, viewer, highlight, select });
//   bridge.ask("What does this do?", { ref: "U6" });      // POST /etch/ask, streamed into the dock
//   bridge.change("add a buzzer", { ref: "J1" });         // recommend card, then /etch/live or /etch/change + /etch/job
//   const load = bridge.onLoad([{ id: "board", title: "Loading the board" }, …]); load.done("board");
//   bridge.dispose();
//
// With ?mock=1, no backend, or a mock job id, change() plays a scripted build (the "full successful design run" from
// API.md, with a failed DRC on the first try) so the whole flow can be shown without the engine.

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const cap = (s) => { s = String(s || "").trim(); return s ? s[0].toUpperCase() + s.slice(1) : s; };
const plain = (md) => String(md || "").replace(/\*\*|`/g, "").replace(/\s+/g, " ").trim();
let seq = 0;
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

// ---- the scripted build for mock mode ----
const PREFIX = [[/buzzer|beeper|piezo/i, "BZ", "Buzzer"], [/\bled\b|light/i, "D", "LED"], [/resistor/i, "R", "Resistor"],
  [/capacitor|cap\b/i, "C", "Capacitor"], [/button|switch/i, "SW", "Push button"], [/connector|header|jack/i, "J", "Connector"],
  [/crystal|oscillator/i, "Y", "Crystal"], [/sensor/i, "U", "Sensor"]];
function newPart(text, parts) {
  const [, p, name] = PREFIX.find(([re]) => re.test(text)) || [null, "U", "Part"];
  let n = 1; while (parts && parts[p + n]) n++;
  return { ref: p + n, name };
}
function simulate({ text, ref, board }) {
  const parts = board?.parts || {};
  const adding = /^(add|put|place|include)\b/i.test(String(text).trim());
  const add = adding ? newPart(text, parts) : { ref: ref || Object.keys(parts)[0] || "U1", name: (ref && parts[ref]?.value) || "Part" };
  const refs = Object.keys(parts).slice(0, 6);
  const run = "live-" + Date.now();
  const E = []; let t = 0;
  const at = (dt, agent, type, f = {}, stage) => { t += dt; E.push({ ev: { agent, type, t: Math.round(t * 100) / 100, run, ...f }, stage }); };
  const lib = ["Device", "Buzzer_Beeper", "Connector", "LED_SMD", "Resistor_SMD", "Capacitor_SMD"];
  at(0.1, "lead", "start", { ask: text, base: `/engine/out/${board?.id || run}` }, "starting the engine");
  at(0.3, "lead", "say", { text: adding ? `On it. I'll add ${add.name.toLowerCase()} ${add.ref}${ref ? ` near ${ref}` : ""}, then place, route and check the board again.` : `On it. I'll change ${add.ref}, then place, route and check the board again.` });
  at(0.3, "classifier", "start", { candidates: 1840, batches: 12, backend: "kicad", symbol_libraries: 214, symbols: 1840, libraries_kept: 9 }, "screening parts");
  for (let i = 0; i < 12; i++) {
    at(0.16, "classifier", "batch", { seconds: 0.15, pieces: [0, 1, 2].map((j) => ({ k: `kicad:${lib[(i + j) % 6]}:P${i}${j}`, n: `${lib[(i + j) % 6]} ${i * 3 + j}`, f: lib[(i + j) % 6], yes: (i + j) % 4 === 0 })) });
  }
  at(0.2, "classifier", "done", { yes: 9, no: 1831, seconds: 2.1, backend: "kicad", families: 4 });
  at(0.2, "filter", "done", { seconds: 0.1,
    kept: [{ k: "kicad:Buzzer_Beeper:Buzzer", n: `${add.name} 12 mm`, f: "Buzzer_Beeper" }, { k: "kicad:Resistor_SMD:R_0603", n: "Resistor 0603", f: "Resistor_SMD" }, { k: "kicad:Device:Q_NPN", n: "NPN transistor", f: "Package_TO_SOT_SMD" }],
    rejected: [{ k: "kicad:Buzzer_Beeper:Big", n: `${add.name} 30 mm`, f: "Buzzer_Beeper", why: "too big for the board" }] });
  for (const n of [`${add.name} 12 mm`, "Resistor 0603", "NPN transistor"]) at(0.2, "pieces", "show", { k: "kicad:" + n, n, f: "", img: "" });
  at(0.3, "picker", "start", { model: "etch", kept: 3 }, "picking parts");
  at(0.7, "picker", "pick", { model: "etch", seconds: 0.7, benchmarks: [],
    parts: [{ ref: add.ref, piece: "kicad:" + add.name, value: add.name, group: adding ? "added" : "changed", why: "what you asked for" },
      { ref: "Q" + (Object.keys(parts).filter((r) => /^Q\d/.test(r)).length + 1), piece: "kicad:Device:Q_NPN", value: "NPN", group: "driver", why: `drives ${add.ref} from a GPIO` },
      ...refs.filter((r) => r !== add.ref).map((r) => ({ ref: r, piece: "", value: parts[r]?.value || "", group: "kept", why: "already on the board" }))] });
  at(0.2, "pieces", "confirm", { parts: [{ k: "kicad:" + add.name, refs: [add.ref], img: "" }], drop: [] });
  at(0.2, "assembly", "change", { added: adding ? [`${add.ref} ${add.name}`] : [], kept: Object.keys(parts).length });
  const gates = ["ERC", "DRC", "unconnected", "parity", "routed", "3D models"];
  for (const it of [1, 2]) {
    at(0.3, "assembly", "iteration", { iteration: it, phase: "place-route-check" }, it === 1 ? "placing and routing" : `placing and routing, try ${it}`);
    for (const g of gates) {
      const bad = it === 1 && g === "DRC";
      at(0.15, "assembly", "gate", { iteration: it, gate: g, ok: !bad, value: bad ? "2 clearance errors" : g === "routed" ? "100%" : "0" }, "checking");
    }
    at(0.3, "assembly", "built", { iteration: it, seconds: 1.4, board: { w: 52, h: 38 }, tracks: 118 + it * 4, stages_s: {} });
    at(0.2, "assembly", "iteration", { iteration: it, phase: "decide", decision: it === 1 ? "repair" : "done", seconds: 1.9 });
    if (it === 1) at(0.5, "picker", "repair", { iteration: 1, seconds: 0.5, parts: 2 + refs.length }, "repairing");
  }
  at(0.4, "export", "done", { zip: `/engine/out/${run}/fab.zip`, zip_bytes: 184320, project: ["board.kicad_pro", "board.kicad_pcb", "board.kicad_sch"], gerbers: ["F_Cu.gbr", "B_Cu.gbr", "F_Mask.gbr", "B_Mask.gbr", "F_Silkscreen.gbr", "Edge_Cuts.gbr", "drill.drl"], bom_lines: 14, seconds: 0.4 }, "exporting files");
  at(0.2, "render", "start", { quality: "preview" }, "rendering in 3D");
  at(1.0, "render", "done", { frames: `/engine/out/${run}/frames.json`, count: 48, quality: "preview", seconds: 1.0, lock_wait_s: 0 }, "finishing");
  at(0.2, "lead", "say", { text: `Done. ${add.ref} is ${adding ? "on the board" : "changed"} and every check passes after one repair.` });
  at(0.1, "lead", "done", { ok: true, iterations: 2, timings: { total_s: Math.round(t * 10) / 10 }, classifier: "kicad", route: "modify", board: `/engine/out/${run}` }, "done");
  const t0 = performance.now();
  return {
    id: "mock-" + run,
    poll(since) {
      const el = (performance.now() - t0) / 1000;
      let n = since; while (n < E.length && E[n].ev.t <= el) n++;
      let stage = "starting the engine"; for (let i = 0; i < n; i++) if (E[i].stage) stage = E[i].stage;
      const done = n >= E.length;
      return { events: E.slice(since, n).map((x) => x.ev), next: n, done, state: done ? "done" : "running", stage, run };
    },
  };
}

export function createAgentBridge({ dock, api, board, viewer = null, highlight = () => {}, select = () => {} } = {}) {
  if (!dock) throw new Error("createAgentBridge needs a dock");
  const timers = new Set();
  let disposed = false, jobGen = 0, askGen = 0;
  const kind = board?.kind || (/^live-/.test(String(board?.id)) ? "live" : "gh");
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
    const rid = nid("chg");
    busy();
    dock.setState("thinking");
    dock.setStatus("Planning the change");
    const live = kind === "live";
    const choice = await dock.recommend({ id: rid, title: summarize(text, ref), confidence: confidence(text, ref, kind),
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
    return follow(j.job, sim, gen, { text, ref, at });
  }

  async function follow(jobId, sim, gen, req) {
    const map = mapper(jobId);
    let since = 0, misses = 0, last = null, stage = "";
    while (!disposed && gen === jobGen) {
      let r;
      try { r = sim ? sim.poll(since) : await api.job(jobId, since); misses = 0; }
      catch (e) {
        if (e?.status === 404) { dock.setState("confused"); dock.say("Etch lost track of this build. Open it from the library instead."); dock.setStatus(""); return null; }
        if (++misses >= 8) { explain(e, jobId + ":poll", "Following the build"); return null; }
        await sleep(800); continue;
      }
      if (disposed || gen !== jobGen) break;
      for (const ev of r.events || []) { const out = map(ev); if (out) last = out; }
      if (r.stage && r.stage !== stage) { stage = r.stage; dock.setStatus(cap(stage)); }
      since = r.next ?? since + (r.events || []).length;
      if (r.done) break;
      await sleep(400);
    }
    map.end();
    return last ? { ...last, job: jobId, request: req } : null;
  }

  // one build event -> dock calls (API.md "Build events")
  function mapper(jid) {
    const S = (k) => `${jid}:${k}`;
    let batches = 0, nb = 0, yes = 0, pieces = 0, queued = false, render = null, picked = [], result = null;
    const step = (o) => dock.step(o);
    const fn = (ev) => {
      const k = `${ev.agent}:${ev.type}`;
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
          step({ id: S("filter"), kind: "tool", title: `Kept ${(ev.kept || []).length} variants, set ${(ev.rejected || []).length} aside`,
            detail: [...(ev.kept || []).map((p) => p.n), ...(ev.rejected || []).map((p) => `${p.n} (${p.why})`)].slice(0, 6).join(", "), status: "done" });
          break;
        case "pieces:show": pieces++; step({ id: S("pieces"), kind: "step", title: `Gathered ${pieces} candidate part${pieces > 1 ? "s" : ""}`, detail: ev.n, status: "done", count: pieces }); break;
        case "pieces:confirm": step({ id: S("pieces"), kind: "step", title: `Using ${(ev.parts || []).length} new part${(ev.parts || []).length === 1 ? "" : "s"}`, detail: (ev.parts || []).map((p) => (p.refs || []).join(" ")).join(", ") + ((ev.drop || []).length ? ` · ${(ev.drop || []).length} dropped` : ""), status: "done", count: pieces }); break;
        case "picker:start": step({ id: S("pick"), kind: "task", title: "Picking parts", detail: `from ${ev.kept} kept variants`, status: "running" }); break;
        case "picker:pick":
          picked = ev.parts || [];
          step({ id: S("pick"), kind: "task", title: `Picked ${picked.length} parts${ev.recipe ? " (known circuit)" : ""}`, detail: picked.slice(0, 8).map((p) => `${p.ref} ${p.value || ""}`.trim()).join(", "), status: "done", count: picked.length, ref: picked[0]?.ref });
          if (ev.say) dock.say(ev.say);
          break;
        case "sourcing:start": step({ id: S("src:" + ev.what), kind: "tool", title: `Fetching ${ev.what}`, detail: ev.search, status: "running" }); break;
        case "sourcing:done": step({ id: S("src:" + ev.what), kind: "tool", title: ev.ok ? `Fetched ${ev.what}` : `Could not fetch ${ev.what}`, detail: ev.provenance?.source_url || ev.provenance?.route || "", status: ev.ok ? "done" : "failed" }); break;
        case "assembly:change": if (!(ev.added || []).length) { step({ id: S("change"), kind: "step", title: "Changing the existing parts", detail: ev.kept != null ? `${ev.kept} parts kept` : "", status: "done" }); break; }
          step({ id: S("change"), kind: "step", title: `Adding ${(ev.added || []).join(", ")}`, detail: ev.kept != null ? `${ev.kept} parts kept` : "", status: "done" }); break;
        case "assembly:iteration":
          if (ev.phase === "place-route-check") step({ id: S("try:" + ev.iteration), kind: "task", title: `Placing and routing · try ${ev.iteration}`, status: "running" });
          else if (ev.phase === "decide") {
            if (ev.decision === "done") step({ id: S("try:" + ev.iteration), kind: "task", title: `Placing and routing · try ${ev.iteration}`, detail: "every check passed", status: "done" });
            else step({ id: S("decide:" + ev.iteration), kind: "reasoning", title: ev.decision === "re-plan" ? "Checks failed, picking the parts again" : "Some checks failed, repairing the design", status: "done" });
          }
          break;
        case "assembly:gate": step({ id: S(`gate:${ev.iteration}:${ev.gate}`), kind: "step", title: `${ev.gate} ${ev.ok ? "passed" : "failed"}`, detail: ev.value != null ? String(ev.value) : "", status: ev.ok ? "done" : "failed" }); break;
        case "assembly:built": step({ id: S("try:" + ev.iteration), kind: "task", title: `Placing and routing · try ${ev.iteration}`, detail: `${ev.board?.w}×${ev.board?.h} mm · ${ev.tracks} tracks · ${ev.seconds} s`, status: "done" }); break;
        case "picker:repair": step({ id: S("repair:" + ev.iteration), kind: "reasoning", title: "Fixing the design after the failed checks", detail: `${ev.parts} parts reviewed · ${ev.seconds} s`, status: "done" }); break;
        case "export:done": step({ id: S("export"), kind: "tool", title: "Wrote the fab files", detail: `${(ev.gerbers || []).length} Gerbers · ${ev.bom_lines} BOM lines · ${Math.round((ev.zip_bytes || 0) / 1024)} KB zip`, status: "done" }); break;
        case "render:start": render = loader("Rendering in 3D"); step({ id: S("render"), kind: "task", title: "Rendering in 3D", detail: ev.quality, status: "running" }); break;
        case "render:done":
          if (render) { render(); render = null; }
          step({ id: S("render"), kind: "task", title: "Rendered in 3D", detail: `${ev.count} frames · ${ev.seconds} s`, status: "done" });
          viewer?.reload?.({ run: ev.run, frames: ev.frames });
          break;
        case "benchmarks:check": step({ id: S("bench:" + (ev.check || ev.label)), kind: "task", title: ev.label || ev.said || ev.check, detail: `${ev.value ?? "?"} ${ev.unit || ""} · ${ev.result}`.trim(), status: ev.pass === false ? "failed" : "done" }); break;
        case "benchmarks:done": step({ id: S("bench"), kind: "task", title: `Checks: ${ev.passed} passed, ${ev.failed} failed${ev.unmeasured ? `, ${ev.unmeasured} not measured` : ""}`, status: ev.failed ? "failed" : "done" }); break;
        case "lead:done":
          if (ev.ok) {
            result = { ok: true, board: ev.board, run: ev.run, parts: picked };
            step({ id: S("done"), kind: "step", title: "Board updated", detail: `${ev.iterations} tr${ev.iterations === 1 ? "y" : "ies"} · ${ev.timings?.total_s ?? "?"} s`, status: "done" });
            dock.setState("success"); dock.setStatus("Done");
            const added = (picked.find((p) => p.group === "added") || picked.find((p) => p.group === "changed"))?.ref;
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
    return fn;
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

  function dispose() {
    disposed = true; jobGen++; askGen++;
    for (const t of timers) { clearTimeout(t); clearInterval(t); }
    timers.clear();
    try { dock.loader(false); } catch (e) { /* dock already gone */ }
  }

  return { ask, change, onLoad, dispose };
}

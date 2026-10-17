// Client for Etch's board endpoints (deploy/EXPLORE_API.md section 2). Same API base and access code as app.js:
// ?api= or config.json, the code sent as X-Etch-Code and remembered under the same localStorage key.
// Falls back to mock-api.js with ?mock=1, when no API base is configured, or when an endpoint is missing (404) or
// the engine cannot be reached, so the board view always works end to end.
import { Q, h } from "./common.js";
import * as mock from "./mock-api.js";

let API = "", CODE = "", ready = null;
const listeners = new Set();
export const status = { mock: false, why: "" };
try { CODE = localStorage.getItem("etch-code") || ""; } catch (e) { /* private mode */ }

export function onStatus(f) { listeners.add(f); f(status); }
function useMock(why) {
  if (status.mock && status.why) return;
  status.mock = true; status.why = why;
  for (const f of listeners) f(status);
}

export function init() {
  if (ready) return ready;
  ready = (async () => {
    if (Q.get("mock") === "1") { useMock("mock mode (?mock=1)"); return; }
    let cfg = {};
    if (!Q.get("api")) cfg = await fetch("config.json", { cache: "no-store" }).then((r) => r.json()).catch(() => ({}));
    API = String(Q.get("api") || cfg.api || "").replace(/\/$/, "");
    if (!API) useMock("no Etch backend configured");
  })();
  return ready;
}
export const hasBackend = () => !!API;

// ---- access code gate (light) ----
let gate = null;
function askCode(msg) {
  if (!gate) {
    const input = h("input", { id: "gcode", type: "password", autocomplete: "off", placeholder: "Access code", "aria-label": "Access code" });
    const err = h("div", { class: "gate-err" });
    const form = h("form", { class: "gate-card" },
      h("div", { class: "gate-t" }, "Enter your Etch access code"),
      h("div", { class: "gate-s" }, "Asking Etch and pinning comments use the Etch engine. The code is remembered in this browser."),
      input, err,
      h("div", { class: "gate-row" },
        h("button", { type: "button", class: "btn outline", onclick: () => finish(false) }, "Use demo answers"),
        h("button", { type: "submit", class: "btn dark" }, "Continue")));
    const wrap = h("div", { class: "gate" }, form);
    document.body.append(wrap);
    let done = null;
    const finish = (ok) => { wrap.classList.remove("show"); const d = done; done = null; if (d) d(ok); };
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const c = input.value.trim(); if (!c) return;
      err.textContent = "Checking…";
      try {
        const r = await fetch(API + "/etch/auth", { method: "POST", headers: { "X-Etch-Code": c } });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.ok) { err.textContent = j.error || "That code did not work."; return; }
        CODE = c; try { localStorage.setItem("etch-code", c); } catch (er) { /* not remembered */ }
        finish(true);
      } catch (er) { err.textContent = "Etch's engine is not reachable right now."; }
    });
    gate = { wrap, input, err, wait: () => new Promise((ok) => (done = ok)) };
  }
  gate.err.textContent = msg || "";
  gate.wrap.classList.add("show");
  setTimeout(() => gate.input.focus(), 30);
  return gate.wait();
}

// authenticated fetch against the backend (also used for a live-* board's GLB / parts / layers)
export async function authed(path, opt = {}, retry = true) {
  await init();
  if (!API) throw new Error("no backend");
  const r = await fetch(API + path, { ...opt, headers: { ...(opt.headers || {}), "X-Etch-Code": CODE } });
  if (r.status === 401 && retry) {
    const ok = await askCode(CODE ? "That code did not work. Try again." : "");
    if (ok) return authed(path, opt, false);
  }
  return r;
}

// one JSON call; any reason the real endpoint can't answer falls through to the mock
async function call(path, body, mockFn) {
  await init();
  if (status.mock) return mockFn();
  let r;
  try {
    r = await authed(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (e) { useMock("Etch's engine is not reachable"); return mockFn(); }
  if (r.status === 401) { useMock("no access code"); return mockFn(); }
  // only a missing route falls back to the mock: an engine without the board routes answers 404/405 with no JSON
  // error, or a plain "not found". The routes' own 404s ("no such board", "That comment is gone.") and every other
  // error (403, 409, 422, 429, 503…) are real answers, shown as is.
  if (r.status === 404 || r.status === 405) {
    const j = await r.clone().json().catch(() => ({}));
    const e = String(j.error || "").trim().toLowerCase();
    if (!e || e === "not found") { useMock("this Etch engine has no board endpoints yet"); return mockFn(); }
  }
  return parse(r);
}
// API.md: errors are {"error": "..."} with a 4xx/5xx status, written to be shown as is (429 limits included)
async function parse(r) {
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) {
    const e = new Error(j.error || (r.status === 429 ? "Too many requests right now. Try again in a bit." : `error ${r.status}`));
    e.status = r.status; throw e;
  }
  return j;
}

export const setBoard = (board, parts, meta) => mock.setBoard(board, parts, meta);
export const ask = (p) => call("/etch/ask", p, () => mock.ask(p));
export const listComments = (board) => call(`/etch/comments?board=${encodeURIComponent(board)}`, undefined, () => mock.listComments(board));
export const createComment = (p) => call("/etch/comments", p, () => mock.createComment(p));
export const reply = (p) => call("/etch/comments/reply", p, () => mock.reply(p));
// a comment that is already gone (404 "That comment is gone.") is as good as deleted
export async function deleteComment(p) {
  try { return await call("/etch/comments/delete", p, () => mock.deleteComment(p)); }
  catch (e) { if (e.status === 404) return { ok: true, gone: true }; throw e; }
}
// the cancel token for a build, kept the way app.js keeps it
function keepToken(j) {
  try { const t = JSON.parse(localStorage.getItem("etch-tokens") || "{}"); t[j.job] = j.token; localStorage.setItem("etch-tokens", JSON.stringify(t)); } catch (e) { /* not remembered */ }
}
// POST /etch/change: any board. For gh: boards Etch copies the board into a new live-* run and changes that; its
// 403/409/422/429 errors are written for people and shown as is. Followed with /etch/job.
export async function change(p) {
  const j = await call("/etch/change", p, () => mock.change(p));
  if (j.job && !j.mock) keepToken(j);
  return j;
}
export const MAX_TEXT = 400; // API.md: questions, comments and replies over 400 characters are refused
export async function changeLive({ run, text }) {
  await init();
  if (status.mock || !API) return mock.change({ board: run, text });
  const r = await authed("/etch/live", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "modify", text, base: `/engine/out/${run}` }) });
  if (r.status === 401) throw new Error("Etch needs your access code to change this board.");
  const j = await parse(r);
  keepToken(j);
  return { ok: true, ...j };
}
// GET /etch/job: { events, next, done, state, stage, run }
export async function job(id, since = 0) {
  await init();
  if (status.mock || !API || String(id).startsWith("mock-")) return mock.job(id, since);
  return parse(await authed(`/etch/job?id=${encodeURIComponent(id)}&since=${since}`));
}

// ---- voice (API.md): /etch/health has no auth; /etch/stt takes raw Float32 16 kHz mono; /etch/tts returns audio/mpeg ----
export async function health() {
  await init();
  if (!API) throw new Error("no backend");
  const r = await fetch(API + "/etch/health", { cache: "no-store" });
  return r.json();
}
export async function stt(buf) {
  const r = await authed("/etch/stt", { method: "POST", body: buf });
  if (r.status === 503) throw new Error("Voice is offline right now. Type instead.");
  return parse(r);
}
export function tts(text) {
  return authed("/etch/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: String(text).slice(0, 400) }) })
    .then((r) => (r.ok ? r.blob() : null)).catch(() => null);
}

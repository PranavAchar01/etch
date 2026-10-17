// Client for Etch's board endpoints (deploy/EXPLORE_API.md section 2). API base: ?api= or config.json (which may point at config_url, the live backend URL that the tunnel publishes).
// Falls back to mock-api.js with ?mock=1, when no API base is configured, or when an endpoint is missing (404).
// When the configured engine can't be reached, calls throw an error marked `offline` instead of answering with demo text.
import { Q } from "./common.js";
import * as mock from "./mock-api.js";

let API = "", ready = null;
const listeners = new Set();
export const status = { mock: false, why: "" };

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
    if (!Q.get("api")) cfg = await fetch("config.json", { cache: "no-store" }).then((r) => r.json()).then((c) => (c.config_url ? fetch(c.config_url, { cache: "no-store" }).then((r) => r.json()) : c)).catch(() => ({}));
    API = String(Q.get("api") || cfg.api || "").replace(/\/$/, "");
    if (!API) useMock("no Etch backend configured");
  })();
  return ready;
}
export const hasBackend = () => !!API;

// fetch against the backend (also used for a live-* board's GLB / parts / layers)
export async function backend(path, opt = {}) {
  await init();
  if (!API) throw new Error("no backend");
  return fetch(API + path, opt);
}

// one JSON call; any reason the real endpoint can't answer falls through to the mock
async function call(path, body, mockFn) {
  await init();
  if (status.mock) return mockFn();
  let r;
  try {
    r = await backend(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (e) {
    // a configured engine that is down is reported as offline, never answered with demo text
    const err = new Error("Etch's engine is not reachable right now."); err.offline = true; throw err;
  }
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
// the cancel token for a build, kept in localStorage (etch-tokens) by job id
function keepToken(j) {
  try { const t = JSON.parse(localStorage.getItem("etch-tokens") || "{}"); t[j.job] = j.token; localStorage.setItem("etch-tokens", JSON.stringify(t)); } catch (e) { /* not remembered */ }
}
// POST /etch/cancel {job, token}: stop a build this browser started (the token was kept by keepToken)
export async function cancel(job) {
  let token = "";
  try { token = JSON.parse(localStorage.getItem("etch-tokens") || "{}")[job] || ""; } catch (e) { /* no token kept */ }
  const r = await backend("/etch/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job, token }) });
  return parse(r);
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
  const r = await backend("/etch/live", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "modify", text, base: `/engine/out/${run}` }) });
  const j = await parse(r);
  keepToken(j);
  return { ok: true, ...j };
}
// GET /etch/job: { events, next, done, state, stage, run }
export async function job(id, since = 0) {
  await init();
  if (status.mock || !API || String(id).startsWith("mock-")) return mock.job(id, since);
  return parse(await backend(`/etch/job?id=${encodeURIComponent(id)}&since=${since}`));
}

// ---- voice (API.md): /etch/stt takes raw Float32 16 kHz mono; /etch/tts returns audio/mpeg ----
export async function health() {
  await init();
  if (!API) throw new Error("no backend");
  const r = await fetch(API + "/etch/health", { cache: "no-store" });
  return r.json();
}
export async function stt(buf) {
  const r = await backend("/etch/stt", { method: "POST", body: buf });
  if (r.status === 503) throw new Error("Voice is offline right now. Type instead.");
  return parse(r);
}
export function tts(text) {
  return backend("/etch/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: String(text).slice(0, 400) }) })
    .then((r) => (r.ok ? r.blob() : null)).catch(() => null);
}

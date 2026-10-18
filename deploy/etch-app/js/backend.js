// Which Etch backend this page talks to, and the "Backend" dialog that changes it.
// Order: ?api= in the URL (this visit only) > the URL saved in this browser (Backend dialog, localStorage) >
// config.json (which may point at config_url, where the official backend's tunnel URL is published).
// Anyone running their own backend (README: "Use etch.chat with your own backend") pastes its tunnel URL here.
import { Q, h } from "./common.js";

const KEY = "etch.backend";

export function saved() {
  try { return localStorage.getItem(KEY) || ""; } catch { return ""; }
}
function store(v) {
  try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); return true; } catch { return false; }
}

// https anywhere; plain http only for this machine (browsers block http from an https page otherwise)
export function normalize(raw) {
  let s = String(raw || "").trim();
  if (!s) throw new Error("Paste your backend's URL.");
  if (!/^[a-z]+:\/\//i.test(s)) s = (/^(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/i.test(s) ? "http://" : "https://") + s;
  let u;
  try { u = new URL(s); } catch { throw new Error("That is not a URL."); }
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(u.hostname);
  if (u.protocol !== "https:" && !(u.protocol === "http:" && local)) throw new Error("Use an https:// URL (http only works for localhost).");
  if (u.username || u.password) throw new Error("Leave the user name and password out of the URL.");
  if (u.search || u.hash) throw new Error("Use the bare URL, without ? or #.");
  return (u.origin + u.pathname).replace(/\/+$/, "");
}

let official = null;
function officialApi() {
  official ??= fetch("config.json", { cache: "no-store" })
    .then((r) => r.json())
    .then((c) => (c.config_url ? fetch(c.config_url, { cache: "no-store" }).then((r) => r.json()) : c))
    .then((c) => String(c.api || "").replace(/\/$/, ""))
    .catch(() => "");
  return official;
}

// -> { api, source: "link" | "custom" | "official" }
export async function resolveApi() {
  const link = Q.get("api");
  if (link) {
    try { return { api: normalize(link), source: "link" }; } catch { /* a bad ?api= falls through */ }
  }
  const mine = saved();
  if (mine) return { api: mine, source: "custom" };
  return { api: await officialApi(), source: "official" };
}

// GET /etch/health on a backend -> { ok, text }
async function check(api) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 8000);
  try {
    const r = await fetch(api + "/etch/health", { cache: "no-store", signal: ctl.signal });
    if (!r.ok) return { ok: false, text: `It answered HTTP ${r.status}. Is this an Etch backend?` };
    const j = await r.json();
    if (!j.ok) return { ok: false, text: "It answered, but not as an Etch backend." };
    return { ok: true, text: `Connected. ${j.running ?? 0} build${j.running === 1 ? "" : "s"} running, voice ${j.stt ? "on" : "off"}.` };
  } catch (e) {
    return { ok: false, text: e.name === "AbortError" ? "No answer in 8 seconds." : "Can't reach it. Is the backend running, and does its CORS allow this site (ETCH_ORIGINS)?" };
  } finally {
    clearTimeout(t);
  }
}

let dlg = null;
export function openBackendDialog() {
  dlg?.remove();
  const input = h("input", { class: "be-in", id: "beIn", type: "text", inputmode: "url", spellcheck: "false", autocomplete: "off", placeholder: "https://your-tunnel.trycloudflare.com", value: saved() });
  const msg = h("p", { class: "be-msg", role: "status", "aria-live": "polite" });
  const say = (t, kind = "") => { msg.textContent = t; msg.dataset.kind = kind; };
  const test = async () => {
    let api;
    try { api = normalize(input.value); } catch (e) { say(e.message, "bad"); return null; }
    say("Checking…");
    const r = await check(api);
    say(r.text, r.ok ? "ok" : "bad");
    return r.ok ? api : null;
  };
  const reload = () => {
    // a ?api= in the address would still win over the saved choice: drop it
    const u = new URL(location.href);
    u.searchParams.delete("api");
    location.replace(u.toString());
  };
  dlg = h("dialog", { class: "be-dlg", "aria-labelledby": "beT" },
    h("form", { method: "dialog", onsubmit: async (e) => { e.preventDefault(); const api = await test(); if (api && store(api)) reload(); else if (api) say("This browser blocks local storage, so the choice can't be saved. Use the ?api= link instead.", "bad"); } },
      h("h2", { id: "beT" }, "Backend"),
      h("p", { class: "be-lede" }, "Etch builds boards on a backend server. Leave this empty to use the official one, or paste the URL of your own (",
        h("a", { href: "https://github.com/PranavAchar01/etch#use-etchchat-with-your-own-backend", target: "_blank", rel: "noopener" }, "how to run one"), ")."),
      h("label", { for: "beIn" }, "Backend URL"),
      input,
      msg,
      h("div", { class: "be-row" },
        h("button", { class: "btn ghost sm", type: "button", onclick: () => { store(""); reload(); } }, "Use official"),
        h("span", { class: "grow" }),
        h("button", { class: "btn ghost sm", type: "button", onclick: test }, "Test"),
        h("button", { class: "btn primary sm", type: "submit" }, "Save")),
      h("button", { class: "be-x", type: "button", "aria-label": "Close", onclick: () => dlg.close() }, "×")));
  dlg.addEventListener("close", () => dlg?.remove());
  document.body.append(dlg);
  dlg.showModal();
  input.focus();
  resolveApi().then(({ api, source }) => {
    if (source === "link") say(`This visit uses ${api} from the link.`);
    else if (source === "official") say(api ? "Using the official backend." : "No backend is configured; the page runs in demo mode.");
  });
}

// the header button: "Backend", or "Your backend" while a custom one is in use
export function mountBackendButton(el) {
  if (!el) return;
  resolveApi().then(({ source }) => {
    el.textContent = source === "official" ? "Backend" : "Your backend";
    el.title = source === "official" ? "Use your own Etch backend" : "Using your own Etch backend. Click to change.";
    el.classList.toggle("be-custom", source !== "official");
  });
  el.addEventListener("click", openBackendDialog);
}

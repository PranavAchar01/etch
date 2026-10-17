// Etch's voice for the board view: mic capture, STT upload, TTS queue and error lines. Talk: click the mic to toggle,
// hold it or hold Space to push-to-talk; after Etch answers a spoken turn it listens again by itself. Speech-to-text
// and text-to-speech go through the Etch engine (POST /etch/stt raw Float32 16 kHz mono, POST /etch/tts). Without
// speech-to-text (mock mode, offline, no route) the browser's speech recognition stands in for listening; speaking
// only ever uses the Etch voice, and when it can't be reached the caption shows instead.
import { Q } from "./common.js";
import * as api from "./etch-api.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

// 16 kHz box filter
function to16k(x, rate) {
  if (Math.abs(rate - 16000) < 1) return x;
  const r = rate / 16000, n = Math.floor(x.length / r), y = new Float32Array(n);
  for (let i = 0; i < n; i++) { const a = Math.floor(i * r), b = Math.min(x.length, Math.max(a + 1, Math.floor((i + 1) * r))); let s = 0; for (let k = a; k < b; k++) s += x[k]; y[i] = s / (b - a); }
  return y;
}
// the part of an answer worth saying out loud: plain text, the first sentence or two
export function speakable(md, max = 2) {
  const t = String(md || "").replace(/`([^`]+)`/g, "$1").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/\s+/g, " ").trim();
  const s = t.split(/(?<=[.!?])\s+/).slice(0, max).join(" ");
  return s.length > 380 ? s.slice(0, 377).replace(/\s+\S*$/, "") + "…" : s;
}

/**
 * createVoice({ onHeard(text), onState(state, detail), onLevel(who, level), onCaption(text), onError(line) })
 * state: "idle" | "listening" | "transcribing" | "speaking" | "off"
 */
export function createVoice(h) {
  const v = { engine: null, tts: false, available: true, vmode: false, talked: false };
  let rec = null, starting = false, sayN = 0, pumping = false, cur = null, vctx = null;
  const VQ = [];
  const state = (s, d) => h.onState?.(s, d);
  const level = (who, x) => h.onLevel?.(who, x);
  const errLine = (t) => { h.onError?.(t); say(t); };

  // ---- which engine: Etch's (needs the backend with stt up) or the browser's ----
  v.ready = (async () => {
    await api.init();
    let backend = false;
    if (api.hasBackend() && Q.get("mock") !== "1") {
      const hl = await api.health().catch(() => null);
      if (hl) v.tts = true; // speech comes from the Etch engine whenever it answers, even with speech-to-text down
      if (hl && hl.stt !== false) backend = true;
      else if (hl && hl.stt === false && !SR) { v.available = false; state("off", "Voice is offline right now. Type instead."); }
    }
    v.engine = backend ? "etch" : SR ? "browser" : null;
    if (!v.engine && v.available) { v.available = false; state("off", "Voice isn't available in this browser. Type instead."); }
    return v.engine;
  })();

  // ---- speaking: a queue so every line finishes; the mic cuts it short ----
  function stop() { sayN++; VQ.length = 0; if (cur) cur.stop(); cur = null; level("etch", 0); try { speechSynthesis.cancel(); } catch (e) { /* none */ } }
  function say(text) {
    const t = String(text || "").trim(); if (!t) return;
    VQ.push({ text: t, audio: v.tts && v.talked ? api.tts(t).catch(() => null) : null });
    while (VQ.length > 3) VQ.shift();
    pump();
  }
  async function pump() {
    if (pumping) return; pumping = true;
    try { while (VQ.length) await play(VQ.shift(), sayN); } finally { pumping = false; }
    if (!rec && !VQ.length) state("idle");
    autoListen();
  }
  function hold(sec, n) {
    return new Promise((ok) => {
      const t0 = performance.now(), item = { stop: () => (item.done = true) }; cur = item;
      const step = () => {
        if (item.done || n !== sayN || performance.now() - t0 > sec * 1000) { level("etch", 0); if (cur === item) cur = null; return ok(); }
        level("etch", 0.35 + 0.3 * Math.abs(Math.sin(performance.now() / 90))); requestAnimationFrame(step);
      };
      step();
    });
  }
  async function play(it, n) {
    const est = Math.max(1.6, it.text.split(" ").length * 0.33);
    h.onCaption?.(it.text);
    if (!v.talked) return; // nothing is spoken before the person has talked to Etch (the text still shows)
    state("speaking");
    await v.ready;
    if (n !== sayN) return;
    // only the Etch voice is ever spoken; without it the caption shows and the eyes still move
    if (v.tts) {
      const audio = it.audio || api.tts(it.text).catch(() => null);
      const blob = await Promise.race([audio, sleep(15000).then(() => null)]);
      if (n !== sayN) return;
      if (blob) return playBlob(blob, est, n);
    }
    return hold(est, n);
  }
  function playBlob(blob, est, n) {
    return new Promise((done) => {
      const a = new Audio(URL.createObjectURL(blob));
      let fin = false, wd = 0, an = null, buf = null;
      const end = () => { if (fin) return; fin = true; clearTimeout(wd); a.pause(); level("etch", 0); if (cur === item) cur = null; URL.revokeObjectURL(a.src); done(); };
      const item = { stop: end }; cur = item;
      const tick = () => {
        if (fin) return;
        if (an) { an.getByteTimeDomainData(buf); let s = 0; for (const x of buf) { const d = (x - 128) / 128; s += d * d; } level("etch", Math.min(1, Math.sqrt(s / buf.length) * 5)); }
        else level("etch", 0.35 + 0.3 * Math.abs(Math.sin(performance.now() / 90)));
        requestAnimationFrame(tick);
      };
      a.onplay = tick; a.onended = end; a.onerror = end;
      wd = setTimeout(end, (est * 2.5 + 10) * 1000);
      (async () => {
        try {
          if (!vctx) vctx = new AudioContext();
          if (vctx.state !== "running") await Promise.race([vctx.resume(), sleep(400)]);
          if (vctx.state === "running") { an = vctx.createAnalyser(); an.fftSize = 512; buf = new Uint8Array(512); vctx.createMediaElementSource(a).connect(an); an.connect(vctx.destination); }
        } catch (e) { an = null; }
        if (fin) return;
        try { await a.play(); } catch (e) { if (!fin) { fin = true; clearTimeout(wd); if (cur === item) cur = null; await hold(est, n); done(); } }
      })();
    });
  }

  // ---- listening ----
  async function start(opt = {}) {
    if (rec || starting || !v.available) return;
    starting = true;
    await v.ready;
    if (!v.engine) { starting = false; return; }
    stop(); v.talked = true;
    let ac = null;
    try {
      ac = new AudioContext(); ac.resume().catch(() => {});
      const ms = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: true } });
      if (ac.state !== "running") await Promise.race([ac.resume(), sleep(600)]);
      const src = ac.createMediaStreamSource(ms), sp = ac.createScriptProcessor(4096, 1, 1), mute = ac.createGain(), chunks = [];
      mute.gain.value = 0;
      const r = { ms, ac, sp, chunks, t: performance.now(), auto: !!opt.auto, held: !!opt.held, spoke: 0, quietSince: 0, label: ms.getAudioTracks()[0]?.label || "", sr: null, said: "" };
      sp.onaudioprocess = (e) => {
        const x = e.inputBuffer.getChannelData(0); if (v.engine === "etch") chunks.push(new Float32Array(x));
        let s = 0; for (const y of x) s += y * y; const rms = Math.sqrt(s / x.length);
        level("you", Math.min(1, rms * 12));
        if (!r.auto || rec !== r) return; // hands free: stop after a pause that follows speech, give up on silence
        const t = performance.now();
        if (rms > 0.012) { r.spoke = r.spoke || t; r.quietSince = 0; } else if (!r.quietSince) r.quietSince = t;
        if (r.spoke && r.quietSince && t - r.quietSince > 1300) finish();
        else if (!r.spoke && t - r.t > 7000) finish(true);
      };
      src.connect(sp); sp.connect(mute); mute.connect(ac.destination);
      if (v.engine === "browser") {
        // the browser transcribes while we listen; the analyser above only drives the level meter
        const sr = new SR(); sr.lang = navigator.language || "en-US"; sr.interimResults = true; sr.continuous = true;
        sr.onresult = (e) => { let t = ""; for (const res of e.results) t += res[0].transcript; r.said = t.trim(); h.onInterim?.(r.said); };
        sr.onerror = (e) => { if (e.error === "not-allowed" || e.error === "service-not-allowed") r.srErr = "I can't hear you: the microphone is blocked. You can type instead."; };
        sr.onend = () => { r.srDone?.(); };
        try { sr.start(); r.sr = sr; } catch (e) { /* already running */ }
      }
      rec = r; ac = null;
      v.vmode = v.vmode || !opt.held;
      state("listening", { held: !!opt.held, auto: !!opt.auto });
    } catch (e) {
      if (ac) ac.close().catch(() => {});
      v.vmode = false; state("idle");
      errLine("I can't hear you: the microphone is blocked. You can type instead.");
    }
    starting = false;
  }
  async function finish(giveUp = false) {
    if (!rec) return;
    const r = rec; rec = null; level("you", 0);
    r.sp.disconnect(); r.ms.getTracks().forEach((t) => t.stop());
    const rate = r.ac.sampleRate; await r.ac.close().catch(() => {});
    if (giveUp) { if (r.sr) try { r.sr.abort(); } catch (e) { /* done */ } v.vmode = false; state("idle"); return; }
    if (v.engine === "browser") {
      state("transcribing");
      if (r.sr) { await Promise.race([new Promise((ok) => { r.srDone = ok; try { r.sr.stop(); } catch (e) { ok(); } }), sleep(2500)]); }
      if (r.srErr) { v.vmode = false; state("idle"); return errLine(r.srErr); }
      const text = r.said.trim();
      if (!text) { state("idle"); return say("I didn't catch that. Try again, or type it."); }
      state("idle"); h.onHeard?.(text); return;
    }
    const n = r.chunks.reduce((s, c) => s + c.length, 0), raw = new Float32Array(n); let o = 0; for (const c of r.chunks) { raw.set(c, o); o += c.length; }
    const all = to16k(raw, rate);
    let peak = 0; for (const x of all) { const a = Math.abs(x); if (a > peak) peak = a; }
    if (all.length < 16000 * 0.3) { state("idle"); return say("I didn't catch that. Click the mic, speak, then click it again."); }
    if (peak < 0.015) { state("idle"); return errLine("That was too quiet for me to hear. Check that the right microphone is picked, then try again."); }
    if (peak < 0.5) { const g = Math.min(20, 0.5 / peak); for (let i = 0; i < all.length; i++) all[i] *= g; }
    const clip = all.length > 16000 * 45 ? all.slice(0, 16000 * 45) : all; // the engine takes 45 s at most
    state("transcribing");
    try {
      const j = await api.stt(clip.buffer.slice(clip.byteOffset, clip.byteOffset + clip.byteLength));
      state("idle");
      const text = (j.text || "").trim();
      if (text) h.onHeard?.(text);
      else if (j.error) errLine(j.error);
      else say("I didn't catch that. Try again, or type it.");
    } catch (e) {
      state("idle");
      if (/offline|503/i.test(e.message)) { v.available = !!SR; if (SR) v.engine = "browser"; errLine("Voice is offline right now. Type instead."); }
      else errLine(e.message);
    }
  }
  function autoListen() {
    if (!v.vmode || rec || starting || !v.listenAfter) return;
    v.listenAfter = false;
    setTimeout(() => { if (!VQ.length && !cur && !pumping && v.vmode) start({ auto: true }); }, 350);
  }

  // ---- controls: click toggles, a held press is push-to-talk, Space is push-to-talk ----
  let downAt = 0;
  function bind(btn) {
    btn.addEventListener("pointerdown", (e) => { e.preventDefault(); if (rec) { v.vmode = false; finish(); return; } downAt = performance.now(); start(); });
    btn.addEventListener("pointerup", () => { if (rec && !rec.auto && performance.now() - downAt > 600) finish(); });
    btn.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); if (rec) finish(); else start(); } });
  }
  const typing = () => { const a = document.activeElement; return a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable); };
  addEventListener("keydown", (e) => { if (e.code === "Space" && !typing() && !e.repeat && !document.activeElement?.classList?.contains("micbig")) { e.preventDefault(); downAt = 0; start({ held: true }); } });
  addEventListener("keyup", (e) => { if (e.code === "Space" && !typing() && !document.activeElement?.classList?.contains("micbig")) { e.preventDefault(); if (rec) finish(); } });

  Object.defineProperty(v, "listening", { get: () => !!rec });
  return Object.assign(v, {
    bind, start, stop: () => { v.vmode = false; stop(); if (rec) finish(true); }, say,
    // say an answer to a spoken turn, then listen again (voice mode)
    answer(md) { v.listenAfter = true; say(speakable(md)); },
  });
}

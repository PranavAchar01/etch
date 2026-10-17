// A thin wrapper over ../voice.js for the dock. voice.js stays as it is; this adds what the dock needs on top:
//   - created lazily, so a page that never talks never checks the engine or the microphone
//   - toggle() / release() drive voice.js's own click-to-toggle and hold-to-talk logic (through bind() on a
//     detached button), so stopping a recording goes through voice.js's finish() exactly as its mic button does
//   - say(text, { force }) can speak before the person has used the mic (voice.js waits for that by default)
//   - end of speech: voice activity detection on the mic level ends a clicked recording by itself once the person
//     stops talking (createVad below). Holding the mic or Space is push-to-talk and always wins over it.
import { createVoice, speakable } from "../voice.js";

export { speakable };

/**
 * createVad({ onEnd(reason) }) -> { start(), stop(), level(x, now), hold(on), stats }
 * x is voice.js's mic level (rms * 12, 0..1), one value per audio buffer (~85 ms).
 *   - the noise floor is measured over the first 250 ms, then follows the quiet parts (rises slowly, drops fast)
 *   - speech starts once the level stays above max(floor * 3, 0.04) for 150 ms
 *   - the turn ends after continuous silence: 900 ms, a little longer after long speech (people pause more), but
 *     never before 600 ms of speech in total; 30 s is a hard cap
 *   - while held (push-to-talk) it never ends the turn
 */
export function createVad({ onEnd } = {}) {
  const calib = [];
  let on = false, held = false, t0 = 0, last = 0, floor = 0.02, above = 0, speaking = false, speech = 0, quiet = 0, ended = false;
  const stats = { reason: "", speechMs: 0, silenceMs: 0, floor: 0 };
  const end = (reason) => {
    if (ended) return;
    ended = true; on = false;
    Object.assign(stats, { reason, speechMs: Math.round(speech), silenceMs: Math.round(quiet), floor: +floor.toFixed(4) });
    onEnd?.(reason);
  };
  return {
    stats,
    get speaking() { return speaking; },
    start() { calib.length = 0; on = true; ended = false; t0 = last = performance.now(); floor = 0.02; above = 0; speaking = false; speech = 0; quiet = 0; stats.reason = ""; },
    stop() { on = false; },
    hold(v) { held = !!v; },
    level(x, now = performance.now()) {
      if (!on) return;
      const dt = Math.min(250, Math.max(0, now - last)); last = now;
      // the first quarter second only calibrates the floor (the room, the fan, the mic's hiss)
      if (now - t0 < 250) { calib.push(x); floor = Math.min(0.2, Math.max(0.004, calib.reduce((a, b) => a + b, 0) / calib.length)); return; }
      const thr = Math.max(floor * 3, 0.04);
      if (x > thr) {
        above += dt;
        if (above >= 150 && !speaking) speaking = true;
        if (speaking) { speech += dt; quiet = 0; }
      } else {
        above = 0;
        // the floor only learns from quiet input
        floor = x < floor ? floor + (x - floor) * 0.3 : floor + (x - floor) * 0.04;
        floor = Math.min(0.2, Math.max(0.004, floor));
        if (speaking) quiet += dt;
      }
      if (held) return;
      if (now - t0 > 30000) return end("cap");
      const need = speech > 4000 ? 1150 : 900;
      if (speaking && speech >= 600 && quiet >= need) end("silence");
    },
  };
}

export function createVoiceAdapter(handlers) {
  let v = null, proxy = null, pressAt = 0;
  const vad = createVad({
    // the same path as letting go of push-to-talk: voice.js's finish(), so STT runs and onHeard fires
    onEnd(reason) {
      if (!v || !v.listening) return;
      handlers.onVadEnd?.(reason, vad.stats);
      proxy.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    },
  });
  const wrapped = {
    ...handlers,
    onState(s, d) {
      if (s === "listening") { vad.start(); vad.hold(!!(d && d.held) || spaceDown || pressAt > 0); }
      else vad.stop();
      handlers.onState?.(s, d);
    },
    onLevel(who, x) {
      if (who === "you") vad.level(x);
      handlers.onLevel?.(who, x);
    },
  };
  // push-to-talk: while Space or the mic is physically held the person decides when the turn ends
  let spaceDown = false;
  const kd = (e) => { if (e.code === "Space" && !e.repeat) { spaceDown = true; vad.hold(true); } };
  const ku = (e) => { if (e.code === "Space") { spaceDown = false; vad.hold(pressAt > 0); } };
  const get = () => {
    if (v) return v;
    v = createVoice(wrapped);
    proxy = document.createElement("button");
    v.bind(proxy);
    addEventListener("keydown", kd, true);
    addEventListener("keyup", ku, true);
    return v;
  };
  const fire = (type) => proxy.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "mouse" }));
  let holdT = 0;
  return {
    vad,
    get created() { return !!v; },
    get listening() { return !!(v && v.listening); },
    get talked() { return !!(v && v.talked); },
    get available() { return !v || v.available; },
    ready() { return get().ready; },
    // like pressing voice.js's mic: starts listening, or finishes the current recording. A press held past 600 ms
    // turns into push-to-talk, which switches the auto end off until it is let go.
    toggle() {
      get();
      const was = v.listening;
      fire("pointerdown");
      clearTimeout(holdT);
      if (!was) { pressAt = performance.now(); holdT = setTimeout(() => { if (pressAt) vad.hold(true); }, 600); }
    },
    // like letting go of it: finishes a recording that was held down long enough to be push-to-talk
    release() {
      clearTimeout(holdT);
      pressAt = 0;
      vad.hold(spaceDown);
      if (v) fire("pointerup");
    },
    start(o) { if (o && o.held) vad.hold(true); return get().start(o); },
    stop() { if (v) v.stop(); },
    say(text, { force = false } = {}) {
      const vv = get();
      if (force) vv.talked = true;
      vv.say(text);
      return vv.talked;
    },
    answer(md) { get().answer(md); },
    destroy() { removeEventListener("keydown", kd, true); removeEventListener("keyup", ku, true); },
  };
}

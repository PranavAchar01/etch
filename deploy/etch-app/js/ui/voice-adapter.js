// A thin wrapper over ../voice.js for the dock. voice.js stays as it is; this adds what the dock needs on top:
//   - created lazily, so a page that never talks never checks the engine or the microphone
//   - toggle() / release() drive voice.js's own click-to-toggle and hold-to-talk logic (through bind() on a
//     detached button), so stopping a recording goes through voice.js's finish() exactly as its mic button does
//   - say(text, { force }) can speak before the person has used the mic (voice.js waits for that by default)
import { createVoice, speakable } from "../voice.js";

export { speakable };

export function createVoiceAdapter(handlers) {
  let v = null, proxy = null;
  const get = () => {
    if (v) return v;
    v = createVoice(handlers);
    proxy = document.createElement("button");
    v.bind(proxy);
    return v;
  };
  const fire = (type) => proxy.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "mouse" }));
  return {
    get created() { return !!v; },
    get listening() { return !!(v && v.listening); },
    get talked() { return !!(v && v.talked); },
    get available() { return !v || v.available; },
    ready() { return get().ready; },
    // like pressing voice.js's mic: starts listening, or finishes the current recording
    toggle() { get(); fire("pointerdown"); },
    // like letting go of it: finishes a recording that was held down long enough to be push-to-talk
    release() { if (v) fire("pointerup"); },
    start(o) { return get().start(o); },
    stop() { if (v) v.stop(); },
    say(text, { force = false } = {}) {
      const vv = get();
      if (force) vv.talked = true;
      vv.say(text);
      return vv.talked;
    },
    answer(md) { get().answer(md); },
  };
}

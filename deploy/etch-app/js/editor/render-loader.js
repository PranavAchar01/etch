// "Rendering your board": Etch's eyes and the dock's pixel loader over the 3D view while a board's model is fetched
// and put in the scene (straight after a build, after a change, and on a direct board.html?id= load).
//   const rl = createRenderLoader(stage); rl.show(); ... rl.hide() once viewer.load resolved; rl.fail("...") on error
import { pixelLoader } from "../ui/trace.js";
import { createEyes } from "../ui/eyes.js";

export function createRenderLoader(stage, { label = "Rendering your board" } = {}) {
  const root = document.createElement("div");
  root.className = "rl";
  const card = document.createElement("div");
  card.className = "rl-card";
  const eyesHost = document.createElement("span");
  eyesHost.className = "rl-eyes";
  const loader = pixelLoader({ label, timer: false, size: "md" });
  const err = document.createElement("p");
  err.className = "rl-err";
  err.setAttribute("role", "alert");
  card.append(eyesHost, loader.el, err);
  root.append(card);
  stage.append(root);
  let eyes = null, gen = 0, failT = 0;

  const api = {
    get shown() { return root.classList.contains("on"); },
    show(text) {
      gen++; clearTimeout(failT);
      root.classList.remove("fail"); err.textContent = "";
      loader.start(text || label);
      if (!eyes) eyes = createEyes(eyesHost, { label: "" });
      eyes.setState("working"); eyes.setActivity?.("render");
      root.classList.add("on");
    },
    // after the frame that draws the new model, so the board is on screen before the loader fades; the timer covers
    // a background tab, where frames are paused
    hide() {
      const g = gen;
      const go = () => {
        if (g !== gen || !root.classList.contains("on")) return;
        root.classList.remove("on");
        loader.stop(); eyes?.setActivity?.(null); eyes?.setState("idle");
      };
      requestAnimationFrame(() => requestAnimationFrame(go));
      setTimeout(go, 300);
    },
    fail(text = "Couldn't render this board.") {
      const g = ++gen;
      loader.stop();
      err.textContent = text;
      eyes?.setActivity?.(null); eyes?.setState("confused");
      root.classList.add("on", "fail");
      failT = setTimeout(() => { if (g === gen) root.classList.remove("on"); }, 6000);
    },
  };
  return api;
}

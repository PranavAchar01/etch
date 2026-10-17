// The agent dock: js/ui/dock.js (with css/agent.css) when it is there, else the minimal stand-in with the same API
// (dock-stub.js).
export async function loadDock() {
  try {
    const m = await import("../ui/dock.js");
    if (!document.querySelector('link[href*="agent.css"]')) {
      const l = document.createElement("link"); l.rel = "stylesheet"; l.href = new URL("../../css/agent.css", import.meta.url).href; document.head.append(l);
    }
    return m.createDock;
  } catch (e) { return (await import("./dock-stub.js")).createDock; }
}

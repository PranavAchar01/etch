// Scripted demo run. Every time is in seconds at 1x speed; the studio derives the
// whole screen from a single clock, so any speed or scrub position renders the same.
// Board units are millimetres on a 60 x 40 mm two-layer board.

export type Pt = [number, number];
export type AgentId = "lead" | "schematic" | "layout" | "verify" | "source";

export const BOARD = { w: 60, h: 40 };
export const END = 136;
export const FINALE = 116;

export const AGENTS: Record<AgentId, { name: string; model: string }> = {
  lead: { name: "Lead", model: "Nemotron 3 Super" },
  schematic: { name: "Schematic", model: "Nemotron 3 Nano" },
  layout: { name: "Layout", model: "Nemotron 3 Nano" },
  verify: { name: "Verifier", model: "Nemotron 3 Nano" },
  source: { name: "Sourcing", model: "Nemotron 3 Nano" },
};

export interface Pad {
  at: Pt;
  w: number;
  h: number;
  round?: boolean;
}

export interface Part {
  ref: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  t: number;
  pads: Pad[];
  swap?: { t: number; label: string };
  moves?: { t0: number; t1: number; dx: number }[];
  silk?: boolean;
}

export interface Trace {
  id: string;
  pts: Pt[];
  w: number;
  t: number;
  dur: number;
  widen?: { t: number; w: number };
  flow?: { color: string; speed: number; amps?: string; at?: Pt };
}

export interface Check {
  id: string;
  label: string;
  spec: string;
  writtenT: number;
  runs: { t: number; ok: boolean; value: string }[];
}

export interface Step {
  t: number;
  dur: number;
  agent: AgentId;
  kind: "plan" | "build" | "check" | "fix" | "add" | "drift" | "done";
  title: string;
  reason: string;
  focus: Pt;
}

export const ITERS: { t: number; label: string }[] = [
  { t: 28, label: "v1" },
  { t: 35, label: "v2" },
  { t: 43, label: "v3" },
  { t: 55, label: "v4" },
  { t: 67, label: "v5" },
  { t: 79, label: "v6" },
  { t: 91, label: "v7" },
  { t: 103, label: "v8" },
  { t: 113, label: "done" },
];

const ic = (x: number, y: number, w: number, h: number, perSide: number, sides: ("l" | "r" | "t" | "b")[]): Pad[] => {
  const pads: Pad[] = [];
  for (const s of sides) {
    for (let i = 0; i < perSide; i++) {
      const f = (i + 1) / (perSide + 1);
      if (s === "l") pads.push({ at: [x, y + h * f], w: 0.9, h: 0.35 });
      if (s === "r") pads.push({ at: [x + w, y + h * f], w: 0.9, h: 0.35 });
      if (s === "t") pads.push({ at: [x + w * f, y], w: 0.35, h: 0.9 });
      if (s === "b") pads.push({ at: [x + w * f, y + h], w: 0.35, h: 0.9 });
    }
  }
  return pads;
};

const two = (a: Pt, b: Pt, w = 0.9, h = 1.1): Pad[] => [
  { at: a, w, h },
  { at: b, w, h },
];

export const ROWS = [0, 1, 2, 3, 4, 5].map((i) => 6.27 + 5.5 * i);

const headers: Part[] = ROWS.map((y, i) => ({
  ref: `J${i + 2}`,
  label: `Servo ${i + 1}`,
  x: 52,
  y: y - 1.5,
  w: 7.6,
  h: 3,
  t: 13 + 0.35 * i,
  silk: true,
  pads: [53.3, 55.8, 58.3].map((px) => ({ at: [px, y] as Pt, w: 1.7, h: 1.7, round: px !== 53.3 })),
}));

export const PARTS: Part[] = [
  {
    ref: "J1",
    label: "USB-C",
    x: 0,
    y: 15,
    w: 7.5,
    h: 9.4,
    t: 9.5,
    pads: [17, 19.5, 20.5, 23].map((y) => ({ at: [7, y] as Pt, w: 1, h: 0.6 })),
    moves: [
      { t0: 79.5, t1: 81, dx: 6 },
      { t0: 84, t1: 85.5, dx: -6 },
    ],
  },
  { ref: "C1", label: "10µF", x: 10.4, y: 8.8, w: 2.2, h: 1.2, t: 10.2, pads: two([10.9, 9.4], [12.1, 9.4], 0.7, 1.1) },
  {
    ref: "U1",
    label: "AMS1117-3.3",
    x: 14,
    y: 6,
    w: 3,
    h: 3,
    t: 10.6,
    pads: [
      { at: [14, 7.5], w: 0.9, h: 0.5 },
      { at: [17, 7.5], w: 0.9, h: 0.5 },
      { at: [15.5, 9], w: 0.5, h: 0.9 },
    ],
    swap: { t: 37, label: "AP2112K-3.3" },
  },
  {
    ref: "C2",
    label: "22µF",
    x: 18.3,
    y: 8.6,
    w: 1.2,
    h: 2.2,
    t: 11,
    pads: two([18.9, 9.2], [18.9, 10.4], 1.1, 0.7),
    swap: { t: 93, label: "22µF ✓" },
  },
  { ref: "U2", label: "ESP32-S3", x: 24, y: 16, w: 8, h: 8, t: 12, pads: ic(24, 16, 8, 8, 7, ["l", "r", "t", "b"]) },
  ...headers,
  { ref: "R1", label: "1k", x: 12, y: 33, w: 2.4, h: 1.2, t: 15.4, pads: two([12.4, 33.6], [14, 33.6], 0.7, 1.1) },
  { ref: "D2", label: "LED", x: 16.2, y: 33, w: 2.4, h: 1.2, t: 15.7, pads: two([16.6, 33.6], [18.2, 33.6], 0.7, 1.1) },
  { ref: "D1", label: "TVS", x: 10.3, y: 12, w: 2.4, h: 1.3, t: 57, pads: two([10.6, 12.65], [12.4, 12.65], 0.7, 1.1) },
  { ref: "R2", label: "Shunt", x: 30, y: 1.6, w: 3.2, h: 1.8, t: 105, pads: two([30.5, 2.5], [32.7, 2.5], 0.8, 1.6) },
  { ref: "U3", label: "INA219", x: 36, y: 6, w: 3, h: 3, t: 105.4, pads: ic(36, 6, 3, 3, 2, ["l", "r", "t", "b"]) },
];

const pwm = (i: number): Pt[] => {
  const yp = 17 + 1.2 * i;
  const yr = ROWS[i];
  const dy = Math.abs(yr - yp);
  return [
    [32, yp],
    [36, yp],
    [36 + dy, yr],
    [53.3, yr],
  ];
};

export const TRACES: Trace[] = [
  { id: "vbus", pts: [[7.4, 17], [9, 17], [9, 7.5], [14, 7.5]], w: 0.5, t: 16, dur: 1.2, flow: { color: "#ffb347", speed: 5, amps: "0.25 A", at: [9, 12] } },
  {
    id: "servo",
    pts: [[9, 7.5], [9, 2.5], [55.8, 2.5], [55.8, 33.8]],
    w: 0.25,
    t: 17,
    dur: 2.2,
    widen: { t: 45, w: 1.5 },
    flow: { color: "#ffe2a8", speed: 11, amps: "3.0 A · +8 °C", at: [24, 2.5] },
  },
  { id: "c1", pts: [[9, 9.4], [10.9, 9.4]], w: 0.5, t: 17.6, dur: 0.4 },
  { id: "v33", pts: [[17, 7.5], [22, 7.5], [22, 12], [26, 12], [26, 16]], w: 0.4, t: 18.5, dur: 1, flow: { color: "#7fe0ff", speed: 4, amps: "3.24 V", at: [22, 10] } },
  { id: "c2", pts: [[18.9, 7.5], [18.9, 9.2]], w: 0.4, t: 19.3, dur: 0.3 },
  { id: "dp", pts: [[7.4, 19.5], [24, 19.5]], w: 0.2, t: 19.6, dur: 0.8, flow: { color: "#c8a6ff", speed: 9 } },
  { id: "dm", pts: [[7.4, 20.5], [24, 20.5]], w: 0.2, t: 19.8, dur: 0.8, flow: { color: "#c8a6ff", speed: 9 } },
  ...[0, 1, 2, 3, 4, 5].map((i) => ({ id: `pwm${i}`, pts: pwm(i), w: 0.2, t: 21 + 0.45 * i, dur: 0.9, flow: { color: "#9dffb4", speed: 7 } })),
  { id: "led", pts: [[24, 22], [19.5, 22], [19.5, 33.6], [18.2, 33.6]], w: 0.25, t: 24.4, dur: 0.9, flow: { color: "#9dffb4", speed: 3 } },
  { id: "rd", pts: [[14, 33.6], [16.6, 33.6]], w: 0.25, t: 25.3, dur: 0.4 },
  { id: "gndj", pts: [[7.4, 23], [9.5, 23]], w: 0.5, t: 25.6, dur: 0.3 },
  { id: "tvs", pts: [[9, 12.65], [10.6, 12.65]], w: 0.5, t: 57.6, dur: 0.5 },
  { id: "sda", pts: [[36.8, 9], [36.8, 13], [30.5, 13], [30.5, 16]], w: 0.2, t: 106, dur: 0.9, flow: { color: "#c8a6ff", speed: 6 } },
  { id: "scl", pts: [[38.2, 9], [38.2, 14], [31.5, 14], [31.5, 16]], w: 0.2, t: 106.4, dur: 0.9, flow: { color: "#c8a6ff", speed: 6 } },
  { id: "sense", pts: [[32.7, 3.3], [32.7, 4.5], [37.5, 4.5], [37.5, 6]], w: 0.2, t: 106.8, dur: 0.7 },
];

export const VIAS: Pt[] = [
  [9.5, 23],
  [13.2, 9.4],
  [15.5, 10.4],
  [20, 10.4],
  [11, 33.6],
  [13.4, 12.65],
  [27, 19],
  [29, 19],
  [27, 21],
  [29, 21],
];
export const POUR = { t: 68, dur: 3 };

export const CHECKS: Check[] = [
  { id: "erc", label: "Schematic rules", spec: "ERC · 0 errors", writtenT: 1.6, runs: [{ t: 28.2, ok: true, value: "0 errors" }, { t: 40.4, ok: true, value: "0 errors" }, { t: 61.4, ok: true, value: "0 errors" }, { t: 108, ok: true, value: "0 errors" }] },
  { id: "parity", label: "Layout matches schematic", spec: "parity · 0 mismatches", writtenT: 2.3, runs: [{ t: 28.6, ok: true, value: "0" }, { t: 73.4, ok: true, value: "0" }, { t: 108.45, ok: true, value: "0" }] },
  { id: "drc", label: "Fab rules", spec: "JLCPCB 2-layer · 0 violations", writtenT: 3, runs: [{ t: 29, ok: true, value: "0" }, { t: 50.4, ok: true, value: "0" }, { t: 73, ok: true, value: "0" }, { t: 108.9, ok: true, value: "0" }] },
  { id: "rail", label: "3.3 V rail under load", spec: "3.20 to 3.40 V at 250 mA", writtenT: 3.7, runs: [{ t: 29.6, ok: false, value: "3.11 V" }, { t: 40, ok: true, value: "3.24 V" }, { t: 109.35, ok: true, value: "3.24 V" }] },
  { id: "heat", label: "Servo trace heating", spec: "under 10 °C rise at 3 A", writtenT: 4.4, runs: [{ t: 30, ok: false, value: "+68 °C" }, { t: 50, ok: true, value: "+8 °C" }, { t: 109.8, ok: true, value: "+8 °C" }] },
  { id: "esd", label: "USB surge protection", spec: "TVS diode on VBUS", writtenT: 5.1, runs: [{ t: 30.4, ok: false, value: "missing" }, { t: 61, ok: true, value: "D1" }, { t: 110.25, ok: true, value: "D1" }] },
  { id: "stock", label: "Every part in stock", spec: "100+ units at LCSC", writtenT: 5.8, runs: [{ t: 30.8, ok: false, value: "C2: 0" }, { t: 97, ok: true, value: "all" }, { t: 110.7, ok: true, value: "all" }] },
  { id: "fit", label: "Fits the enclosure", spec: "60 × 40 mm, USB-C on the edge", writtenT: 6.5, runs: [{ t: 31.2, ok: true, value: "fits" }, { t: 82, ok: false, value: "USB-C 6 mm in" }, { t: 87, ok: true, value: "fits" }, { t: 111.15, ok: true, value: "fits" }] },
];

export const STEPS: Step[] = [
  { t: 0.4, dur: 6, agent: "lead", kind: "plan", title: "Turning your sentence into checks", reason: "A board is done only when every check below passes. No check, no claim.", focus: [30, 20] },
  { t: 7.6, dur: 7, agent: "layout", kind: "build", title: "Building the baseline board", reason: "USB-C in, a 3.3 V regulator, an ESP32-S3 and six servo headers.", focus: [30, 20] },
  { t: 16, dur: 9, agent: "layout", kind: "build", title: "Routing the copper", reason: "Power first, then USB data, then one signal line to each servo.", focus: [36, 12] },
  { t: 28, dur: 5, agent: "verify", kind: "check", title: "Baseline v1: 4 of 8 checks pass", reason: "The rail sags, the servo trace runs hot, there is no surge part and one capacitor is out of stock.", focus: [15, 7.5] },
  { t: 33, dur: 2, agent: "lead", kind: "plan", title: "Re-plan: fix the rail first", reason: "Everything downstream runs on 3.3 V, so that failure goes first.", focus: [15, 7.5] },
  { t: 35, dur: 7, agent: "schematic", kind: "fix", title: "Swapped the regulator", reason: "The AMS1117 sagged to 3.11 V at 250 mA. The AP2112K holds 3.24 V.", focus: [15.5, 7.5] },
  { t: 43, dur: 8, agent: "layout", kind: "fix", title: "Widened the servo power trace", reason: "Six servos pull 3 A. At 0.25 mm the trace heats up 68 °C. At 1.5 mm it stays under 10.", focus: [32, 2.5] },
  { t: 55, dur: 7, agent: "schematic", kind: "add", title: "Added surge protection on USB", reason: "A static zap from the cable would otherwise hit the regulator directly.", focus: [11.5, 12.65] },
  { t: 67, dur: 7, agent: "layout", kind: "add", title: "Poured ground on the back layer", reason: "The 3 A servo current gets a short, wide path back to USB.", focus: [40, 30] },
  { t: 79, dur: 9, agent: "verify", kind: "drift", title: "Caught drift and rolled it back", reason: "The layout agent pulled USB-C off the edge to shorten a trace. The spec says edge-mounted.", focus: [9, 20] },
  { t: 91, dur: 7, agent: "source", kind: "fix", title: "Swapped C2 for an in-stock part", reason: "LCSC had none of the first 22 µF capacitor. The replacement has 48,000.", focus: [18.9, 9.8] },
  { t: 103, dur: 9, agent: "schematic", kind: "add", title: "Added a servo current sensor", reason: "The arm can now tell when a joint stalls, before the servo cooks itself.", focus: [37.5, 7.5] },
  { t: 113, dur: 3, agent: "lead", kind: "done", title: "Done: 8 of 8 checks pass", reason: "Stopped after 8 iterations. Nothing left to fix, so no human needed.", focus: [30, 20] },
];

// What each agent is doing, for the roster. Each entry holds until the agent's next entry.
export const ACTIVITY: { t: number; agent: AgentId; text: string }[] = [
  { t: 0, agent: "lead", text: "reading the request" },
  { t: 1.5, agent: "lead", text: "writing the definition of done" },
  { t: 7, agent: "lead", text: "waiting on baseline" },
  { t: 7, agent: "schematic", text: "writing the netlist" },
  { t: 9, agent: "schematic", text: "idle" },
  { t: 7.5, agent: "layout", text: "placing parts" },
  { t: 16, agent: "layout", text: "routing" },
  { t: 27, agent: "layout", text: "idle" },
  { t: 28, agent: "verify", text: "running 8 checks" },
  { t: 30.5, agent: "source", text: "checking LCSC stock" },
  { t: 31.5, agent: "source", text: "idle" },
  { t: 31.5, agent: "verify", text: "4 of 8 pass" },
  { t: 33, agent: "lead", text: "re-planning" },
  { t: 35, agent: "lead", text: "watching for drift" },
  { t: 35, agent: "schematic", text: "swapping U1" },
  { t: 39.5, agent: "verify", text: "re-running rail sim" },
  { t: 42, agent: "schematic", text: "idle" },
  { t: 43, agent: "layout", text: "widening servo bus" },
  { t: 49.5, agent: "verify", text: "re-running heat check" },
  { t: 52, agent: "layout", text: "idle" },
  { t: 55, agent: "schematic", text: "adding D1" },
  { t: 60.5, agent: "verify", text: "re-running ESD check" },
  { t: 63, agent: "schematic", text: "idle" },
  { t: 67, agent: "layout", text: "pouring ground" },
  { t: 72.5, agent: "verify", text: "re-running DRC" },
  { t: 75, agent: "layout", text: "shortening a trace" },
  { t: 79.5, agent: "layout", text: "moved J1 inward" },
  { t: 81.5, agent: "verify", text: "fit check failed" },
  { t: 83, agent: "lead", text: "rolling back J1" },
  { t: 86, agent: "layout", text: "idle" },
  { t: 86, agent: "lead", text: "watching for drift" },
  { t: 87, agent: "verify", text: "fit passes" },
  { t: 91, agent: "source", text: "finding a stocked C2" },
  { t: 96.5, agent: "verify", text: "re-running stock check" },
  { t: 98, agent: "source", text: "idle" },
  { t: 103, agent: "schematic", text: "adding U3 + shunt" },
  { t: 106, agent: "layout", text: "routing I²C" },
  { t: 107.5, agent: "verify", text: "full re-run" },
  { t: 108, agent: "layout", text: "idle" },
  { t: 108, agent: "schematic", text: "idle" },
  { t: 112, agent: "verify", text: "8 of 8 pass" },
  { t: 113, agent: "lead", text: "stopped: done" },
  { t: 116, agent: "verify", text: "power-on simulation" },
];

export const BOARD_TYPES = [
  { id: "arm", name: "Robot arm controller", blurb: "servos, USB-C, ESP32", sentence: { does: "drives 6 servos", power: "USB-C", size: "60 × 40 mm" } },
  { id: "lipo", name: "LiPo charger", blurb: "single cell, USB-C in", sentence: { does: "charges a 1S LiPo at 1 A", power: "USB-C", size: "30 × 20 mm" } },
  { id: "motor", name: "Motor driver", blurb: "2 brushed DC motors", sentence: { does: "drives 2 DC motors at 2 A", power: "a 12 V barrel jack", size: "50 × 35 mm" } },
  { id: "sensor", name: "Sensor breakout", blurb: "I²C, Qwiic, 3.3 V", sentence: { does: "breaks out a BME280 on Qwiic", power: "3.3 V from the host", size: "25 × 25 mm" } },
  { id: "iot", name: "Wi-Fi sensor node", blurb: "ESP32, battery, sleep", sentence: { does: "logs temperature over Wi-Fi", power: "a coin cell", size: "40 × 30 mm" } },
  { id: "led", name: "LED strip driver", blurb: "WS2812, 5 V, 10 A", sentence: { does: "drives 300 WS2812 LEDs", power: "a 5 V 10 A supply", size: "45 × 30 mm" } },
  { id: "keyboard", name: "Macro keypad", blurb: "9 keys, RP2040", sentence: { does: "reads 9 hot-swap keys", power: "USB-C", size: "60 × 60 mm" } },
  { id: "drone", name: "Flight controller", blurb: "IMU, 4 ESC outputs", sentence: { does: "runs 4 ESCs from an IMU", power: "a 4S battery", size: "36 × 36 mm" } },
  { id: "audio", name: "Audio amplifier", blurb: "class-D, 2 × 3 W", sentence: { does: "drives two 3 W speakers", power: "USB-C", size: "40 × 25 mm" } },
  { id: "psu", name: "Bench power supply", blurb: "adjustable 1 to 12 V", sentence: { does: "outputs 1 to 12 V at 2 A", power: "a 20 V USB-PD charger", size: "70 × 50 mm" } },
] as const;

export const OUTPUTS = [
  { name: "Gerbers + drill", file: "etch-arm-v8-gerbers.zip", note: "JLCPCB ready" },
  { name: "Bill of materials", file: "etch-arm-v8-bom.csv", note: "23 parts, all stocked" },
  { name: "Pick and place", file: "etch-arm-v8-cpl.csv", note: "for assembly" },
  { name: "3D model", file: "etch-arm-v8.glb", note: "open in any viewer" },
  { name: "Check report", file: "etch-arm-v8-report.json", note: "8 of 8 pass, 8 iterations" },
];

export const clamp = (v: number, a = 0, b = 1) => Math.min(b, Math.max(a, v));
export const ease = (v: number) => {
  const x = clamp(v);
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
};

export function partOffset(p: Part, T: number): number {
  let dx = 0;
  for (const m of p.moves ?? []) dx += m.dx * ease((T - m.t0) / (m.t1 - m.t0));
  return dx;
}

export function checkState(c: Check, T: number): { state: "unwritten" | "pending" | "running" | "pass" | "fail"; value?: string } {
  if (T < c.writtenT) return { state: "unwritten" };
  let last: Check["runs"][number] | undefined;
  for (const r of c.runs) {
    if (T >= r.t - 0.45 && T < r.t) return { state: "running", value: last?.value };
    if (r.t <= T) last = r;
  }
  if (!last) return { state: "pending" };
  return { state: last.ok ? "pass" : "fail", value: last.value };
}

export function activeStep(T: number): Step | undefined {
  let s: Step | undefined;
  for (const st of STEPS) if (st.t <= T && T < st.t + st.dur) s = st;
  return s;
}

export function activity(agent: AgentId, T: number): string {
  let txt = "waiting";
  for (const a of ACTIVITY) if (a.agent === agent && a.t <= T) txt = a.text;
  return txt;
}

export function pathLen(pts: Pt[]): number {
  let L = 0;
  for (let i = 1; i < pts.length; i++) L += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  return L;
}

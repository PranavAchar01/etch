// Real 3D part models for the build animation and the new-board backdrop: parts3d/index.json (written by
// library/export_part_glbs.py) maps each catalog footprint to its meshopt GLB (the footprint's own KiCad STEP model,
// anchored at the footprint origin on the board's top face, y = 0), its pads, silkscreen and courtyard (footprint
// mm, x right, y down). A build event's part ({ref, piece, value, footprint}) resolves to a footprint by its piece
// key ("kicad:Device:R@R_0603_1608Metric"), its footprint, or, for parts with neither, a well-known package for what
// it is (an ESP32 module, a USB-C receptacle, a 0603 resistor...). Materials match js/board-viewer.js.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";

const BASE = new URL("../../../parts3d/", import.meta.url).href;
let indexP = null, byName = null, index = null, loader = null;
const models = new Map(); // file -> Promise<{ scene, box }>

export function loadIndex() {
  if (!indexP) {
    indexP = fetch(BASE + "index.json").then((r) => (r.ok ? r.json() : { parts: {} })).catch(() => ({ parts: {} })).then((j) => {
      index = j.parts || {};
      byName = new Map();
      for (const k of Object.keys(index)) byName.set(k.split(":")[1].toLowerCase(), k);
      return index;
    });
  }
  return indexP;
}

// what it is -> a catalog footprint that looks like it (used only when the event names no footprint we have)
const GUESS = [
  [/esp32.?s3/, "Espressif:ESP32-S3-WROOM-1"], [/esp32.?c3/, "Espressif:ESP32-C3-MINI-1"], [/esp32|wroom|wrover/, "RF_Module:ESP32-WROOM-32"],
  [/pico|rp2040/, "Module:RaspberryPi_Pico_Common_THT"], [/nrf52|mdbt|bluetooth module/, "RF_Module:Raytac_MDBT50Q"],
  [/usb.?c|type.?c/, "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"], [/micro.?usb|micro.?b|\busb\b/, "Connector_USB:USB_Micro-B_Molex_47346-0001"],
  [/barrel|dc.?jack/, "Connector_BarrelJack:BarrelJack_Horizontal"], [/terminal/, "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2_1x02_P5.00mm_Horizontal"],
  [/\bjst\b|\bph\b/, "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical"],
  [/buzzer|beeper|piezo|sounder/, "Buzzer_Beeper:Buzzer_12x9.5RM7.6"], [/ws2812|neopixel|addressable/, "LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm"],
  [/ams1117|sot.?223/, "Package_TO_SOT_SMD:SOT-223-3_TabPin2"], [/ap2112|sot.?23.?5|ldo|regulator|xc6206|mcp1700/, "Package_TO_SOT_SMD:SOT-23-5"],
  [/crystal|xtal|\bmhz\b|oscillator/, "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm"], [/dht|humidity/, "Sensor:Aosong_DHT11_5.5x12.0_P2.54mm"],
  [/bme|bmp|pressure|lga/, "Package_LGA:Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering"], [/ldr|photoresistor|light sensor/, "OptoDevice:R_LDR_5.0x4.1mm_P3mm_Vertical"],
  [/potentiometer|\bpot\b|trimmer/, "Potentiometer_THT:Potentiometer_Bourns_3296W_Vertical"], [/coin|cr20|battery/, "Battery:BatteryHolder_Keystone_3034_1x20mm"],
  [/tqfp|atmega|qfp/, "Package_QFP:TQFP-32_7x7mm_P0.8mm"], [/qfn/, "Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm"], [/tssop/, "Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm"],
  [/\bdip\b|ne555|\b555\b/, "Package_DIP:DIP-8_W7.62mm"], [/to.?220/, "Package_TO_SOT_THT:TO-220-3_Vertical"], [/to.?92/, "Package_TO_SOT_THT:TO-92_Inline"],
];
const SIZE = (t) => (/0402/.test(t) ? "0402_1005Metric" : /0805/.test(t) ? "0805_2012Metric" : /1206/.test(t) ? "1206_3216Metric" : /0603/.test(t) ? "0603_1608Metric" : null);
function guess(p) {
  const ref = String(p.ref || ""), pre = (ref.match(/^[A-Za-z]+/) || [""])[0].toUpperCase();
  const t = `${p.value || ""} ${p.piece || ""} ${p.name || ""} ${p.footprint || ""}`.toLowerCase();
  for (const [re, fp] of GUESS) if (re.test(t)) return fp;
  const sz = SIZE(t);
  if (/\bled\b|led_|^led/.test(t) || pre === "LED") return /5\.?0?mm|5mm/.test(t) ? "LED_THT:LED_D5.0mm" : `LED_SMD:LED_${sz && sz !== "0402_1005Metric" ? sz : "0603_1608Metric"}`;
  if (pre === "SW" || pre === "BTN" || pre === "BUT" || /switch|button|tact/.test(t)) return /smd|tl3342/.test(t) ? "Button_Switch_SMD:SW_SPST_TL3342" : "Button_Switch_THT:SW_PUSH_6mm";
  if (pre === "Q" || pre === "FET" || /transistor|npn|pnp|mosfet|q_n|q_p/.test(t)) return "Package_TO_SOT_SMD:SOT-23";
  if (pre === "Y" || pre === "X") return "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm";
  if (pre === "C" || /capacitor|\bcap\b|[µu]f\b|nf\b|pf\b/.test(t)) {
    if (/elec|polar|cp_|radial/.test(t)) return "Capacitor_SMD:CP_Elec_5x5.3";
    const uf = parseFloat((t.match(/([\d.]+)\s*[µu]f/) || [])[1]);
    return `Capacitor_SMD:C_${sz || (uf >= 10 ? "0805_2012Metric" : "0603_1608Metric")}`;
  }
  if (pre === "R" || /resistor|[kmΩ]\s*ohm|\d\s*k\b|ω/.test(t)) return `Resistor_SMD:R_${sz || "0603_1608Metric"}`;
  if (pre === "L" || /inductor/.test(t)) return `Inductor_SMD:L_${sz === "0805_2012Metric" ? sz : "0603_1608Metric"}`;
  if (pre === "F" || /fuse/.test(t)) return "Fuse:Fuse_1206_3216Metric";
  if (pre === "D" || /diode|schottky/.test(t)) return "Diode_SMD:D_SOD-123";
  if (pre === "J" || pre === "P" || pre === "CN" || pre === "CONN" || /header|connector|conn_/.test(t)) {
    const n = Math.max(2, Math.min(8, +(t.match(/1x0?(\d)/) || [])[1] || 4));
    return `Connector_PinHeader_2.54mm:PinHeader_1x0${n}_P2.54mm_Vertical`;
  }
  if (pre === "TP") return "TestPoint:TestPoint_Pad_D1.0mm";
  if (pre === "U" || pre === "IC") return /soic|so-?8/.test(t) ? "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" : "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm";
  return "Resistor_SMD:R_0603_1608Metric";
}

// a part from an event -> { key, info } (info: { f, box, pads, silk, court }), after loadIndex()
export function resolve(p) {
  if (!index) return null;
  const names = [];
  const fp = String(p.footprint || "");
  if (fp) names.push(fp, fp.split(":").pop());
  const piece = String(p.piece || p.k || "");
  if (piece.includes("@")) { const f = piece.split("@").pop(); names.push(f, f.split(":").pop()); }
  for (const n of names) {
    if (index[n]) return { key: n, info: index[n] };
    const k = byName.get(n.toLowerCase());
    if (k) return { key: k, info: index[k] };
  }
  // a footprint we don't have, but its package is clear from its name (OLIMEX_RLC-FP:C_0603_5MIL_DWS)
  const k = guess({ ...p, footprint: fp || piece });
  return index[k] ? { key: k, info: index[k], guessed: true } : null;
}

function gltfLoader() { if (!loader) { loader = new GLTFLoader(); loader.setMeshoptDecoder(MeshoptDecoder); } return loader; }

// the editor's part material (board-viewer.js partMaterial): STEP colours are linear; light unsaturated = metal
export function partMaterial(src) {
  const c = src.color ? src.color.clone() : new THREE.Color(0.2, 0.2, 0.2), a = src.opacity ?? 1;
  const max = Math.max(c.r, c.g, c.b), min = Math.min(c.r, c.g, c.b);
  const metal = src.metalness > 0.5 || (max > 0.35 && max - min < 0.12 && !src.map);
  return new THREE.MeshStandardMaterial({ color: c, map: src.map || null, metalness: metal ? 0.6 : 0.05, roughness: metal ? 0.36 : 0.48, transparent: a < 1, opacity: a });
}

// a footprint with no KiCad 3D model: a plain body from its courtyard, so it still reads as a part in the tray
function bodyFor(key, info) {
  const [x0, y0, x1, y1] = info.court || [-1, -1, 1, 1];
  const w = (x1 - x0) * 0.86, d = (y1 - y0) * 0.86, cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const g = new THREE.Group();
  const buzzer = /Buzzer|Indicator/.test(key), pot = /Potentiometer/.test(key), bat = /Battery/.test(key);
  if (/TestPoint/.test(key)) return g;
  if (buzzer) {
    const r = Math.min(w, d) / 2, h = +(key.match(/H(\d+(\.\d+)?)mm/) || [])[1] || Math.min(7.5, r * 1.1);
    const body = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 48, 1), new THREE.MeshStandardMaterial({ color: 0x141518, roughness: 0.55, metalness: 0.05 }));
    body.position.set(cx, h / 2, cy);
    const top = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.16, r * 0.16, 0.2, 24), new THREE.MeshStandardMaterial({ color: 0x050505, roughness: 0.9 }));
    top.position.set(cx, h + 0.01, cy);
    g.add(body, top);
  } else {
    const h = bat ? 3.2 : pot ? 4.8 : 3;
    const geo = new THREE.BoxGeometry(w, h, d);
    const m = new THREE.MeshStandardMaterial(bat ? { color: 0xb8bdc4, metalness: 0.7, roughness: 0.32 } : pot ? { color: 0x2e5aa8, roughness: 0.5 } : { color: 0x1d1e22, roughness: 0.55 });
    const body = new THREE.Mesh(geo, m); body.position.set(cx, h / 2, cy); g.add(body);
  }
  g.scale.setScalar(0.001);
  g.position.set(0, 0, 0);
  const wrap = new THREE.Group(); wrap.add(g);
  return wrap;
}

// the model of a footprint: a template scene (shared geometry) plus its box in metres; null when it can't load
export function loadModel(key, info) {
  const id = info?.f || "body:" + key;
  if (!models.has(id)) {
    const p = (info?.f ? fetch(BASE + encodeURIComponent(info.f)).then((r) => { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); }).then((b) => gltfLoader().parseAsync(b, "")).then((g) => {
      const mats = new Map();
      g.scene.traverse((o) => {
        if (!o.isMesh) return;
        const src = Array.isArray(o.material) ? o.material[0] : o.material;
        if (!mats.has(src)) mats.set(src, partMaterial(src));
        o.material = mats.get(src);
      });
      return g.scene;
    }) : Promise.resolve(bodyFor(key, info || {}))).then((scene) => {
      scene.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(scene);
      if (box.isEmpty()) box.set(new THREE.Vector3(-0.0005, 0, -0.0005), new THREE.Vector3(0.0005, 0.0003, 0.0005));
      return { scene, box };
    }).catch(() => null);
    models.set(id, p);
  }
  return models.get(id);
}

// one placed copy: shared geometry, its own materials (so each part can fade on its own)
export function instance(tpl) {
  const o = tpl.scene.clone(true);
  const mats = [];
  o.traverse((m) => {
    if (!m.isMesh) return;
    m.material = m.material.clone();
    m.material.userData.baseOpacity = m.material.opacity;
    m.material.userData.baseTransparent = m.material.transparent;
    mats.push(m.material);
  });
  return { obj: o, mats };
}

// fade a set of materials (keeps each one's own opacity as its 100 %)
export function setFade(mats, a) {
  for (const m of mats) {
    const base = m.userData.baseOpacity ?? 1, want = a < 0.999 || m.userData.baseTransparent;
    if (m.transparent !== want) { m.transparent = want; m.depthWrite = !want || a > 0.6; m.needsUpdate = true; }
    m.opacity = base * a;
  }
}

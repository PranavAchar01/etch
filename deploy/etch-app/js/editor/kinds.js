// What kind of part a refdes / footprint / value says it is, for the "This board" list and short descriptions.
const KINDS = [
  ["Modules", /RF_Module|WROOM|WROVER|ESP32|nRF52|RP2040|STM32|ATmega|SAMD|MODULE/i],
  ["Connectors", /USB|Connector|PinHeader|PinSocket|Socket|RJ45|Jack|BAT|^J\d|^P\d|^CN|^CON|^X\d/i],
  ["Chips", /^U\d|^IC|Package_|SOIC|QFN|TSSOP|SOT-?23-[5-9]|MSOP|QFP|BGA/i],
  ["Buttons", /Button|Switch|^SW|^BUT|^S\d/i],
  ["LEDs", /LED|^D?L?ED|^LED|^ACT|^PWR_LED/i],
  ["Diodes", /^D\d|Diode|^TVS|^ZD/i],
  ["Transistors", /^Q\d|^T\d|Transistor|MOSFET/i],
  ["Crystals", /Crystal|^Y\d|^XTAL|^Q?Z\d/i],
  ["Inductors", /^L\d|^FB|^FR|Inductor|Ferrite/i],
  ["Capacitors", /^C\d|^CP|Capacitor/i],
  ["Resistors", /^R\d|^RN|^RM|^RA|Resistor/i],
  ["Jumpers", /Jumper|^SJ|^JP|_E\d|_E$/i],
  ["Test points & holes", /TestPoint|^TP|MountingHole|^H\d|^FID|Fiducial/i],
];
export function kindOf(p) {
  const ref = String(p.ref || ""), fp = String(p.footprint || ""), v = String(p.value || "");
  if (KINDS[0][1].test(fp) || KINDS[0][1].test(v)) return "Modules";
  for (const [k, re] of KINDS) if (re.test(ref)) return k;
  for (const [k, re] of KINDS) if (re.test(fp) || re.test(v)) return k;
  return "Other";
}
export const KIND_ORDER = KINDS.map((k) => k[0]).concat("Other");
// natural refdes order: R2 before R10
export const byRef = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });

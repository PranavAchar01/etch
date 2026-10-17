"""Every piece Etch may put on a board, read from the KiCad libraries installed with KiCad 10.

A piece is one symbol + one footprint + its STEP model, all from the installed KiCad libraries
(SharedSupport/symbols, footprints, 3dmodels). Nothing here is drawn or invented by Etch.

Stage 0 (library pre-filter, plain code): of the 223 symbol libraries only the families a small
discrete through-hole board uses are expanded. Generic symbols (Device:R, Device:LED, ...) are
paired with each matching THT footprint; part-number symbols (2N3904, 1N4148, LDR03, ...) bring
their own default footprint. Each piece then carries hard facts the later stages need:
  - model:  absolute STEP path, or None (no 3D model -> rejected later)
  - pins:   symbol pin number -> pin name, and whether every pin has a pad on the footprint
  - spice:  the ngspice mapping Etch can simulate, or None plus the reason

  uv run --project engine python -m agent.kicad_catalog         # (re)build engine/parts/kicad_catalog.json
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

from pathlib import Path

from etchlib import symbols
from etchlib.kicad import FOOTPRINT_DIR, MODEL_DIR, SYMBOL_DIR, VENDOR_LIBS, footprint_root
from etchlib.parts import read_index
from etchlib.sim import zener_model

from . import config

OUT = config.ENGINE / "parts/kicad_catalog.json"

# (symbol, family, footprint library, footprint-name regex)
GENERIC = [
    (
        "Device:R",
        "resistor",
        "Resistor_THT",
        r"R_Axial_DIN0(204|207|309)_L[\d.]+mm_D[\d.]+mm_P(5\.08|7\.62|10\.16|2\.54)mm_(Horizontal|Vertical)$",
    ),
    (
        "Device:C",
        "capacitor",
        "Capacitor_THT",
        r"C_Disc_D(3\.0|3\.4|4\.3|5\.0)mm_W[\d.]+mm_P(2\.50|5\.00)mm$",
    ),
    (
        "Device:C_Polarized",
        "electrolytic capacitor",
        "Capacitor_THT",
        r"CP_Radial_D(4\.0|5\.0|6\.3|8\.0)mm_P(1\.50|2\.00|2\.50|3\.50)mm$",
    ),
    ("Device:LED", "LED", "LED_THT", r"LED_D(3\.0|5\.0)mm(_Clear|_FlatTop)?$"),
    # a leaded NTC thermistor (10k at 25 C) sits in the same axial resistor footprint
    ("Device:Thermistor_NTC", "thermistor", "Resistor_THT", r"R_Axial_DIN0204_L3\.6mm_D1\.6mm_P2\.54mm_Vertical$"),
    ("Sensor:DHT11", "humidity sensor", "Sensor", r"Aosong_DHT11_5\.5x12\.0_P2\.54mm$"),
    (
        "Device:Buzzer",
        "buzzer",
        "Buzzer_Beeper",
        r"(Buzzer|MagneticBuzzer|Indicator)_.*",
    ),
    (
        "Device:R_Potentiometer_Trim",
        "trimmer potentiometer",
        "Potentiometer_THT",
        r"Potentiometer_(Bourns_3386P|Bourns_3296W|Piher_PT-10-V10|Bourns_3386F)_Vertical$",
    ),
    (
        "Switch:SW_Push",
        "push button",
        "Button_Switch_THT",
        r"SW_PUSH_(6mm|6mm_H\d+mm|12mm)$",
    ),
    (
        "Device:Battery_Cell",
        "battery holder",
        "Battery",
        r"BatteryHolder_(Keystone_(3000|3002|3034|1058)|LINX_BAT-HLD-012-SMT|MPD_BC2003).*",
    ),
    (
        "Connector_Generic:Conn_01x02",
        "2-pin connector",
        "Connector_JST",
        r"JST_(PH|XH)_[BS]2B-(PH-K|XH-A)_1x02_P[\d.]+mm_(Vertical|Horizontal)$",
    ),
    (
        "Connector_Generic:Conn_01x02",
        "2-pin connector",
        "Connector_PinHeader_2.54mm",
        r"PinHeader_1x02_P2\.54mm_(Vertical|Horizontal)$",
    ),
    (
        "Connector_Generic:Conn_01x02",
        "2-pin connector",
        "TerminalBlock_Phoenix",
        r"TerminalBlock_Phoenix_MKDS-1,5-2(-5\.08)?_1x02_P5\.(00|08)mm_Horizontal$",
    ),
    (
        "Connector_Generic:Conn_01x03",
        "3-pin connector",
        "Connector_JST",
        r"JST_(PH|XH)_[BS]3B-(PH-K|XH-A)_1x03_P[\d.]+mm_(Vertical|Horizontal)$",
    ),
    (
        "Connector_Generic:Conn_01x03",
        "3-pin connector",
        "Connector_PinHeader_2.54mm",
        r"PinHeader_1x03_P2\.54mm_Vertical$",
    ),
    (
        "Connector:USB_C_Receptacle_PowerOnly_6P",
        "USB-C power input",
        "Connector_USB",
        r"USB_C_Receptacle_GCT_USB4125-xx-x-0190_6P_TopMnt_Horizontal$",
    ),
    # common-cathode 5 mm RGB LED: Kingbright L-154A4SUREQBFZGEW pin order (1 red A, 2 K, 3 green A, 4 blue A)
    ("Device:LED_RKGB", "RGB LED", "LED_THT", r"LED_D5\.0mm-4_RGB_Wide_Pins$"),
    # a plug-in USB microcontroller module: the laptop talks to it over its own USB port
    ("MCU_Module:RaspberryPi_Pico", "USB microcontroller", "Module", r"RaspberryPi_Pico_Common_THT$"),
    # the same module with Wi-Fi and Bluetooth (CYW43439): same 40-pin footprint and pinout as the Pico
    ("MCU_Module:RaspberryPi_Pico_W", "USB microcontroller", "Module", r"RaspberryPi_Pico_Common_THT$"),
    # quad tri-state buffer (74HC126 function and pinout; KiCad names the symbol 74LS126): half-duplex servo bus
    ("74xx:74LS126", "tri-state buffer", "Package_DIP", r"DIP-14_W7\.62mm$"),
    # 3.3 V LDO for the ESP32 boards (Espressif: a single 3.3 V supply of at least 500 mA; AMS1117 is 1 A)
    ("Regulator_Linear:AMS1117-3.3", "LDO regulator", "Package_TO_SOT_SMD", r"SOT-223-3_TabPin2$"),
    # USB-C with the USB 2.0 data pair, for the ESP32-S3 / C3 native USB (power-only 6P above has no D+/D-)
    (
        "Connector:USB_C_Receptacle_USB2.0_16P",
        "USB-C data connector",
        "Connector_USB",
        r"USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal$",
    ),
    (
        "Connector:Barrel_Jack",
        "DC barrel jack",
        "Connector_BarrelJack",
        r"BarrelJack_(Horizontal|CUI_PJ-102AH_Horizontal)$",
    ),
]

# plug-in modules: a published open-source breakout board that plugs into a KiCad library socket on our board.
# The socket is the KiCad part; the module's pinout and provenance come from its published design files.
MODULES = [
    {
        "lib_id": "Connector_Generic:Conn_01x03",
        "footprint": "Connector_PinSocket_2.54mm:PinSocket_1x03_P2.54mm_Vertical",
        "family": "mic module",
        "name": "Electret mic + MAX4466 amplifier module (Adafruit 1063) / 3-pin socket",
        "descr": "electret microphone with MAX4466 auto-biased amplifier; plugs in: VCC, GND, OUT (VCC/2 + audio)",
        "pins": {"1": "VCC", "2": "GND", "3": "OUT"},
        "provenance": {
            "design": "Adafruit Electret Microphone Amplifier - MAX4466 with Adjustable Gain, product 1063",
            "repo": "github.com/adafruit/Adafruit-MAX4466-Electret-Mic-Amplifier-PCBs",
            "file": "Adafruit MAX4466 Mic Amp.brd",
            "sha256": "227be8cd74544c4b2c4ab8508931ec2b849cdebf56dfcb59ea1f73af5f2268c9",
            "pinout": "JP1 pin 1 VCC, pin 2 GND, pin 3 OUT (from U1 pin 4); 1x03 0.1 in header",
            "licence": "CC BY-SA (Limor Fried / Ladyada for Adafruit Industries)",
            "socket": "KiCad 10 library Connector_PinSocket_2.54mm:PinSocket_1x03_P2.54mm_Vertical",
        },
    },
]

MODULES.append(
    {
        "lib_id": "Connector_Generic:Conn_01x06",
        "footprint": "Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical",
        "family": "OLED display module",
        "name": "SSD1306 128x32 I2C OLED module (Adafruit 931) / 6-pin socket",
        "descr": "monochrome SSD1306 OLED, I2C, own 3.3 V regulator and level shifter; plugs in: VIN, 3V3, GND, RST, SCL, SDA",
        "pins": {"1": "VIN", "2": "3V3", "3": "GND", "4": "RST", "5": "SCL", "6": "SDA"},
        "provenance": {
            "design": "Adafruit Monochrome 128x32 I2C OLED graphic display (SSD1306), product 931, STEMMA QT rev B",
            "repo": "github.com/adafruit/Adafruit-128x32-I2C-OLED-Breakout-PCB",
            "file": "Adafruit OLED 128x32 Mono I2C STEMMA QT rev B.sch",
            "sha256": "5eb32b8ec3ff34e178e23045bda916a54dcd9bc4071beecc53417c713b740b7d",
            "pinout": "JP1 pin 1 VCC (VIN), 2 3.3V out, 3 GND, 4 RESET, 5 SCL, 6 SDA; 1x06 0.1 in header",
            "licence": "CC BY-SA (Limor Fried / Ladyada for Adafruit Industries)",
            "socket": "KiCad 10 library Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical",
        },
    }
)

# manufacturer libraries cloned into pcb-loop/vendor/ (etchlib.kicad.VENDOR_LIBS): symbol, footprint and STEP all
# from the vendor's own repo at a pinned tag. vendor/espressif-kicad-libraries/PROVENANCE.md has repo, tag, commit.
ESPRESSIF_REPO = "github.com/espressif/kicad-libraries"
ESPRESSIF_TAG = "3.2.1"
ESPRESSIF_COMMIT = "1dfc3110895c9cd62daf332f49c49ee0ee200831"
VENDORED = [
    # (symbol, family, footprint, chip, datasheet Wi-Fi receive current as a load in ohms at 3.3 V)
    ("Espressif:ESP32-S3-WROOM-1", "Wi-Fi microcontroller module", "Espressif:ESP32-S3-WROOM-1", "esp32s3", 35),
    ("Espressif:ESP32-C3-MINI-1", "Wi-Fi microcontroller module", "Espressif:ESP32-C3-MINI-1", "esp32c3", 40),
]
# GPIOs the firmware must never drive: strapping pins wired to the BOOT button, and the native USB pair
ESP_RESERVED = {"esp32s3": (0, 19, 20), "esp32c3": (2, 8, 9, 18, 19)}


def _esp_provenance(lib_id: str, fp: str, model: str | None) -> dict[str, Any]:
    base = f"https://{ESPRESSIF_REPO}/blob/{ESPRESSIF_TAG}"
    name = fp.split(":", 1)[1]
    return {
        "route": "Espressif KiCad library (vendored, pinned tag)",
        "source_url": f"{base}/footprints/Espressif.pretty/{name}.kicad_mod",
        "symbol_url": f"{base}/symbols/Espressif.kicad_sym",
        "model_url": f"{base}/3dmodels/espressif.3dshapes/{Path(model).name}" if model else None,
        "version": f"espressif/kicad-libraries tag {ESPRESSIF_TAG} (commit {ESPRESSIF_COMMIT[:12]})",
        "licence": "CC-BY-SA-4.0 with the KiCad libraries exception (Espressif LICENSE.md)",
        "repo": ESPRESSIF_REPO,
        "commit": ESPRESSIF_COMMIT,
    }


# part-number libraries: keep symbols whose own default footprint is one of these THT packages
SPECIFIC = [
    ("Transistor_BJT", "BJT transistor", r"Package_TO_SOT_THT:TO-92"),
    ("Transistor_FET", "MOSFET", r"Package_TO_SOT_THT:TO-92"),
    ("Diode", "diode", r"Diode_THT:D_(DO-35|DO-41|DO-15)"),
    (
        "Sensor_Optical",
        "light sensor",
        r"OptoDevice:(R_LDR|.*TO-18)|LED_THT:LED_D(3|5)\.0mm",
    ),
    ("Timer", "timer IC", r"Package_DIP:DIP-8"),
    ("Regulator_Linear", "linear regulator", r"Package_TO_SOT_THT:TO-92"),
    # L293D / SN754410: quad half-H bridge for two DC motors, DIP-16
    ("Driver_Motor", "motor driver", r"Package_DIP:DIP-16_W7\.62mm$"),
]

BJT_CARDS = {
    "2N3904": "2N3904",
    "2N3906": "2N3906",
    "2N2222": "2N2222",
    "2N2222A": "2N2222",
    "PN2222A": "2N2222",
}
DIODE_CARDS = [
    (r"^1N4148", "D1N4148"),
    (r"^1N914", "D1N4148"),
    (r"^1N400[1-7]", "D1N4007"),
    (r"^1N581[789]", "D1N5819"),
]


def _props(block: str) -> dict[str, str]:
    return {
        k: v for k, v in re.findall(r'\(property "([^"]+)" "((?:[^"\\]|\\.)*)"', block)
    }


def _symbols(lib: str) -> dict[str, dict[str, str]]:
    """Top-level symbols of one library: name -> {Footprint, Description, extends}."""
    text = (SYMBOL_DIR / f"{lib}.kicad_sym").read_text()
    out = {}
    starts = [m for m in re.finditer(r'\n\t\(symbol "([^"]+)"\n', text)]
    for k, m in enumerate(starts):
        end = starts[k + 1].start() if k + 1 < len(starts) else len(text)
        block = text[m.end() : end]
        p = _props(block)
        ext = re.search(r'\(extends "([^"]+)"\)', block[:400])
        out[m.group(1)] = {**p, "extends": ext.group(1) if ext else ""}
    # derived symbols inherit an empty footprint/description from the parent
    for name, p in out.items():
        par = out.get(p["extends"]) if p["extends"] else None
        if par:
            for key in ("Footprint", "Description"):
                if not p.get(key):
                    p[key] = par.get(key, "")
    return out


_FP_CACHE: dict[str, tuple[set[str], str | None, str]] = {}


def _footprint(fp: str) -> tuple[set[str], str | None, str]:
    """pad numbers, absolute STEP path (or None) and description of one installed footprint."""
    if fp in _FP_CACHE:
        return _FP_CACHE[fp]
    lib, name = fp.split(":", 1)
    path = footprint_root(lib) / f"{lib}.pretty" / f"{name}.kicad_mod"
    if not path.exists():
        _FP_CACHE[fp] = (set(), None, "")
        return _FP_CACHE[fp]
    text = path.read_text()
    pads = set(re.findall(r'\(pad "([^"]*)"', text)) - {""}
    model = None
    # the model KiCad shows: the first one not marked (hide yes)
    m = None
    for mm in re.finditer(r'\(model "\$\{KICAD\d*_3DMODEL_DIR\}/([^"]+)"', text):
        if "(hide yes)" not in text[mm.end() : mm.end() + 40]:
            m = mm
            break
    if m:
        step = MODEL_DIR / re.sub(r"\.wrl$", ".step", m.group(1))
        model = str(step) if step.exists() else None
    elif lib in VENDOR_LIBS:
        # vendor footprints point at the KiCad PCM install (${KICADn_3RD_PARTY}/3dmodels/<package>/<x>.3dshapes/..)
        v = re.search(r'\(model "\$\{KICAD\d*_3RD_PARTY\}/3dmodels/[^/]+/([^"]+)"', text)
        if v:
            step = VENDOR_LIBS[lib] / "3dmodels" / v.group(1)
            model = str(step) if step.exists() else None
    d = re.search(r'\(descr "((?:[^"\\]|\\.)*)"', text)
    _FP_CACHE[fp] = (pads, model, d.group(1) if d else "")
    return _FP_CACHE[fp]


def _pin_names(lib_id: str) -> dict[str, str]:
    return {p.number: p.name for p in symbols.pins(lib_id)}


def _by_name(pins: dict[str, str], *names: str) -> list[str] | None:
    inv = {}
    for num, nm in pins.items():
        inv.setdefault(nm.upper(), num)
    out = []
    for n in names:
        if n.upper() not in inv:
            return None
        out.append(inv[n.upper()])
    return out


def spice_for(
    lib_id: str, family: str, pins: dict[str, str], fp: str = ""
) -> tuple[dict[str, Any] | None, str]:
    """The ngspice mapping for a piece, or (None, why not)."""
    name = lib_id.split(":", 1)[1].upper()
    nums = sorted(pins, key=lambda s: (len(s), s))
    if family in ("resistor",):
        return {"prefix": "R", "pins": nums[:2]}, "resistor"
    if family == "light sensor" and (lib_id.startswith("Sensor_Optical:LDR") or ":R_LDR_" in fp):
        # a CdS cell is a resistor whose value follows the light; the scenarios set dark / light
        return {
            "prefix": "R",
            "pins": nums[:2],
            "role": "ldr",
            "dark": "1Meg",
            "light": "1k",
        }, "LDR: dark 1 Mohm, lit 1 kohm (datasheet order of magnitude)"
    if family in ("capacitor", "electrolytic capacitor"):
        return {"prefix": "C", "pins": nums[:2]}, "capacitor"
    if family == "LED":
        ak = _by_name(pins, "A", "K")
        return (
            (
                {"prefix": "D", "pins": ak, "model": "LED_{colour}"},
                "LED, colour card from its value",
            )
            if ak
            else (None, "pins not named A/K")
        )
    if family == "RGB LED":
        rgbk = _by_name(pins, "RA", "GA", "BA", "K")
        return (
            ({"prefix": "D", "pins": rgbk, "role": "rgb"}, "RGB LED: red, green and blue diodes on one cathode")
            if rgbk
            else (None, "pins not named RA/GA/BA/K")
        )
    if family == "Wi-Fi microcontroller module":
        v3 = _by_name(pins, "3V3")
        if not v3:
            return None, "no 3V3 pin"
        chip = "esp32c3" if "C3" in name else "esp32s3"
        keep = set(ESP_RESERVED[chip])
        gpio = {}
        for n, nm in pins.items():
            g = re.match(r"GPIO(\d+)", nm.upper())
            if g and int(g.group(1)) not in keep:
                gpio[n] = f"GPIO{g.group(1)}"
        ohms = {lid.split(":")[1].upper(): o for lid, *_, o in VENDORED}.get(name, 35)
        return {
            "prefix": "U",
            "pins": v3,
            "role": "mcu",
            "rail": True,
            "chip": chip,
            "reserved": [n for n, nm in pins.items() if (g := re.match(r"GPIO(\d+)", nm.upper())) and int(g.group(1)) in keep],
            "gnd": [n for n, nm in pins.items() if nm.upper() == "GND"],
            "gpio": gpio,
            "v_io": 3.3,
            "r_out": 50,
            "idd_ohms": ohms,
        }, "runs from its 3V3 pin: Wi-Fi receive current as a load, GPIO outputs from the tested firmware (3.3 V, ~50 ohm)"
    if family == "LDO regulator":
        vi = _by_name(pins, "VI", "VO", "GND")
        if not vi:
            return None, "no VI/VO/GND pins"
        # AMS1117-3.3 datasheet: 3.3 V out, about 1.1 V dropout, about 5 mA quiescent; input current = load + Iq
        return {"prefix": "U", "pins": vi, "role": "ldo", "vout": 3.3, "dropout": 1.1, "iq_ohms": 1000}, (
            "3.3 V out, 1.1 V dropout, load current drawn from the input plus about 5 mA quiescent")
    if family == "USB-C data connector":
        vb = _by_name(pins, "VBUS", "GND")
        return (
            ({"prefix": "V", "pins": vb, "role": "power_in"}, "5 V from VBUS (D+/D- not simulated)")
            if vb
            else (None, "no VBUS/GND pins")
        )
    if family == "USB microcontroller":
        pw = _by_name(pins, "VBUS", "VSYS", "3V3")
        if not pw:
            return None, "no VBUS/VSYS/3V3 pins"
        gpio = {n: nm for n, nm in pins.items() if nm.upper().startswith("GPIO")}
        return {
            "prefix": "U",
            "pins": pw,
            "role": "mcu",
            "gnd": [n for n, nm in pins.items() if nm.upper() in ("GND", "AGND")],
            "gpio": gpio,
            "v_io": 3.3,
            "r_out": 50,
        }, "USB power in; GPIO outputs driven by the tested firmware (3.3 V through ~50 ohm)"
    if family == "diode":
        ak = _by_name(pins, "A", "K")
        if not ak:
            return None, "pins not named A/K"
        for pat, card in DIODE_CARDS:
            if re.match(pat, name):
                return {"prefix": "D", "pins": ak, "model": card}, f"{card} card"
        z = zener_model(name)
        if z:
            return {
                "prefix": "D",
                "pins": ak,
                "model": name,
            }, "zener: generic card at its nominal voltage"
        return None, "no SPICE card for this diode"
    if family == "BJT transistor":
        card = BJT_CARDS.get(name)
        cbe = _by_name(pins, "C", "B", "E")
        if not cbe:
            return None, "pins not named C/B/E"
        if not card:
            return None, f"no SPICE card for {name}"
        return {"prefix": "Q", "pins": cbe, "model": card}, f"{card} published card"
    if family == "trimmer potentiometer":
        return {"prefix": "P", "pins": nums[:3]}, "two resistors meeting at the wiper (pin 2), set to mid travel"
    if family == "tri-state buffer":
        vg = _by_name(pins, "VCC", "GND")
        if not vg:
            return None, "no VCC/GND pins"
        # the gates switch the servo line, which ngspice does not need to see: the board's firmware test covers
        # those pins; here the chip is its supply draw (74HC126: well under 1 mA static at 3.3 V)
        return {"prefix": "R", "pins": vg, "role": "load", "ohms": 40000}, "supply draw only (logic covered by the firmware test)"
    if family == "humidity sensor":
        vg = _by_name(pins, "VDD", "GND")
        if not vg:
            return None, "no VDD/GND pins"
        # DHT11 datasheet: 0.5 to 2.5 mA while measuring; the one-wire data line is covered by the firmware test
        return {"prefix": "R", "pins": vg, "role": "load", "ohms": 2200}, "supply draw while measuring (about 1.5 mA)"
    if family == "thermistor":
        return {"prefix": "R", "pins": nums[:2]}, "NTC thermistor at 25 C (its value, 10k)"
    if family == "motor driver":
        vg = _by_name(pins, "VCC1", "GND")
        if not vg:
            return None, "no VCC1/GND pins"
        # L293D / SN754410 share the DIP-16 pinout. Logic draw on VCC1 (up to 24 mA with outputs off) plus the four
        # half-H outputs as switches to VCC2 / GND set by the enable and input pins (engine/etchlib/sim.py
        # "hbridge"), so a motor on two outputs draws real current in simulation
        order = ["16", "8", "4", "1", "2", "3", "7", "6", "9", "10", "11", "15", "14"]
        if not all(n in pins for n in order):
            return {"prefix": "R", "pins": vg, "role": "load", "ohms": 250}, "logic supply draw only"
        return {"prefix": "U", "pins": order, "role": "hbridge", "ohms": 250}, "half-H outputs switched by EN/A, typical drops"
    if family == "OLED display module":
        return {"prefix": "R", "pins": ["1", "3"], "role": "load", "ohms": 250}, "OLED module as its supply draw (about 20 mA)"
    if family == "buzzer":
        return {
            "prefix": "R",
            "pins": nums[:2],
            "role": "load",
            "ohms": 160,
        }, "buzzer as its rated load (5 V, ~30 mA)"
    if family == "push button":
        return {
            "prefix": "S",
            "pins": nums[:2],
            "role": "switch",
        }, "switch (pressed / released scenarios)"
    if family in ("2-pin connector", "battery holder", "DC barrel jack"):
        return {
            "prefix": "V",
            "pins": nums[:2],
            "role": "power_in",
        }, "power in (pin 1 +) or a load"
    if family == "USB-C power input":
        vb = _by_name(pins, "VBUS", "GND")
        return (
            ({"prefix": "V", "pins": vb, "role": "power_in"}, "5 V from VBUS")
            if vb
            else (None, "no VBUS/GND pins")
        )
    if family == "mic module":
        return {
            "prefix": "X",
            "pins": ["1", "2", "3"],
            "role": "mic",
        }, "mic amp module: 0.5 mA, output at VCC/2 plus the sound (quiet / speaking scenarios)"
    if family == "3-pin connector":
        return {
            "prefix": "X",
            "pins": nums[:3],
            "role": "passive",
        }, "signal connector (not driven in simulation)"
    return None, f"no SPICE model for a {family}"


def _key(lib_id: str, fp: str) -> str:
    return "kicad:" + lib_id + "@" + fp.split(":", 1)[1]


def _piece(lib_id: str, family: str, fp: str, descr: str) -> dict[str, Any]:
    pads, model, fdescr = _footprint(fp)
    pins = _pin_names(lib_id)
    sp, sp_note = spice_for(lib_id, family, pins, fp)
    return {
        "key": _key(lib_id, fp),
        "lib_id": lib_id,
        "footprint": fp,
        "family": family,
        "name": lib_id.split(":", 1)[1] + " / " + fp.split(":", 1)[1],
        "descr": (descr or fdescr)[:110],
        "pins": pins,
        "pads_ok": bool(pins) and set(pins) <= pads,
        "model": model,
        "spice": sp,
        "spice_note": sp_note,
        "source": "kicad-lib",
    }


def build() -> dict[str, Any]:
    t0 = time.time()
    n_sym = 0
    for f in SYMBOL_DIR.glob("*.kicad_sym"):
        n_sym += f.read_text().count('\n\t(symbol "')
    n_fp_libs = len(list(FOOTPRINT_DIR.glob("*.pretty")))
    pieces: list[dict[str, Any]] = []
    used_libs = set()
    for lib_id, family, fplib, rx in GENERIC:
        used_libs.add(lib_id.split(":")[0])
        for f in sorted((FOOTPRINT_DIR / f"{fplib}.pretty").glob("*.kicad_mod")):
            if re.match(rx, f.stem):
                pieces.append(_piece(lib_id, family, f"{fplib}:{f.stem}", ""))
    for lib, family, fprx in SPECIFIC:
        used_libs.add(lib)
        for name, p in _symbols(lib).items():
            fp = p.get("Footprint", "")
            if fp and re.match(fprx, fp):
                pieces.append(
                    _piece(f"{lib}:{name}", family, fp, p.get("Description", ""))
                )
    for lib_id, family, fp, _chip, _ohms in VENDORED:
        if not footprint_root(fp.split(":")[0]).exists():
            continue  # vendor clone missing: the pieces are simply not offered
        pc = _piece(lib_id, family, fp, f"{lib_id.split(':')[1]} Wi-Fi + Bluetooth LE module (Espressif)")
        pc["source"] = "vendor-lib"
        pc["provenance"] = _esp_provenance(lib_id, fp, pc["model"])
        pieces.append(pc)
    for m in MODULES:
        pads, model, _ = _footprint(m["footprint"])
        sp, note = spice_for(m["lib_id"], m["family"], m["pins"], m["footprint"])
        pieces.append({
            "key": _key(m["lib_id"], m["footprint"]),
            "lib_id": m["lib_id"],
            "footprint": m["footprint"],
            "family": m["family"],
            "name": m["name"],
            "descr": m["descr"],
            "pins": m["pins"],
            "pads_ok": set(m["pins"]) <= pads,
            "model": model,
            "spice": sp,
            "spice_note": note,
            "source": "module",
            "provenance": m["provenance"],
        })
    seen, uniq = set(), []
    for p in pieces:
        if p["key"] not in seen:
            seen.add(p["key"])
            uniq.append(p)
    data = {
        "kicad": "10",
        "built_s": round(time.time() - t0, 2),
        "stats": {
            "symbol_libraries": len(list(SYMBOL_DIR.glob("*.kicad_sym"))),
            "symbols": n_sym,
            "footprint_libraries": n_fp_libs,
            "libraries_kept": sorted(used_libs),
            "pieces": len(uniq),
        },
        "pieces": uniq,
    }
    OUT.write_text(json.dumps(data, indent=0))
    return data


def load() -> dict[str, Any]:
    if not OUT.exists():
        return build()
    return read_index(OUT)


if __name__ == "__main__":
    d = build()
    s = d["stats"]
    fam: dict[str, int] = {}
    for p in d["pieces"]:
        fam[p["family"]] = fam.get(p["family"], 0) + 1
    print(json.dumps(s), d["built_s"], "s", file=sys.stderr)
    print(json.dumps(fam, indent=1), file=sys.stderr)
    ok = [p for p in d["pieces"] if p["model"] and p["pads_ok"] and p["spice"]]
    print("usable (model + pads + spice):", len(ok), file=sys.stderr)

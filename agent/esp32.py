"""ESP32 boards: Espressif's reference power circuit around a bare module, the "ESP32 wifi sensor" recipe, and the
swap that moves a Pico W recipe onto an ESP32-S3 when the ask wants Wi-Fi or Bluetooth.

Preference (QA ESP32 round, 2026-10-17):
- the ask names "esp32" (or "s3"): ESP32-S3-WROOM-1; it names "c3": ESP32-C3-MINI-1;
- the ask wants Wi-Fi / Bluetooth / wireless on a recipe that used the Pico W (strip_knob, motor_car): ESP32-S3,
  because it is the module with native USB (no USB-UART chip), 2.4 GHz Wi-Fi + BLE 5, and ESP-SR support;
- the ask names "pico", or the ESP32 pieces are missing from the catalog: the Pico W stays.
live._auto_mcu (a model-picked design with lonely sensor nets) still adds a Pico / Pico W: a bare module needs
the whole power block below, which that path does not place.

The power block follows Espressif's ESP32-S3 / ESP32-C3 hardware design guidelines: one 3.3 V rail from an LDO
rated well over 500 mA (AMS1117-3.3, 1 A), 10 uF at the power entrance, 22 uF + 0.1 uF on the module's 3V3 pin,
EN (CHIP_PU) on a 10 k / 1 uF RC so the chip starts after the rail settles, a RESET button on EN, the BOOT strap
pin (GPIO0 on the S3, GPIO9 on the C3) pulled up with a BOOT button to ground, and USB-C to the native USB pins
through 22 ohm series resistors, with 5.1 k pull-downs on CC1/CC2 so a USB-C charger supplies 5 V.
Not included: an ESD/TVS diode on VBUS and the data pair (no such part in the catalog yet).
"""

from __future__ import annotations

import re
from typing import Any

S3 = "kicad:Espressif:ESP32-S3-WROOM-1@ESP32-S3-WROOM-1"
C3 = "kicad:Espressif:ESP32-C3-MINI-1@ESP32-C3-MINI-1"
LDO = "kicad:Regulator_Linear:AMS1117-3.3@SOT-223-3_TabPin2"
USBC16 = "kicad:Connector:USB_C_Receptacle_USB2.0_16P@USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"
R = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
CP = "kicad:Device:C_Polarized@CP_Radial_D5.0mm_P2.00mm"
C_DISC = "kicad:Device:C@C_Disc_D5.0mm_W2.5mm_P2.50mm"
SW = "kicad:Switch:SW_Push@SW_PUSH_6mm"
LED = "kicad:Device:LED@LED_D5.0mm"
DHT = "kicad:Sensor:DHT11@Aosong_DHT11_5.5x12.0_P2.54mm"
NTC = "kicad:Device:Thermistor_NTC@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
HDR3 = "kicad:Connector_Generic:Conn_01x03@PinHeader_1x03_P2.54mm_Vertical"

# per chip: module piece, power and strap pins, GPIO number -> module pin, and the pin pools Etch hands out
# (strapping pins, the USB pair, UART0 and the S3's octal-flash / JTAG pins are never handed out)
CHIP = {
    "esp32s3": {
        "piece": S3,
        "name": "ESP32-S3",
        "gnd": ("1", "40", "41"),
        "v3": "2",
        "en": "3",
        "boot": ("27", 0),
        "dn": "13",
        "dp": "14",
        "pin": {
            4: "4",
            5: "5",
            6: "6",
            7: "7",
            15: "8",
            16: "9",
            17: "10",
            18: "11",
            8: "12",
            3: "15",
            46: "16",
            9: "17",
            10: "18",
            11: "19",
            12: "20",
            13: "21",
            14: "22",
            21: "23",
            47: "24",
            48: "25",
            45: "26",
            0: "27",
            2: "38",
            1: "39",
        },
        "digital": (4, 5, 6, 7, 15, 16, 17, 18, 8, 9, 10, 11, 12, 13, 14, 21, 47, 48),
        "adc": (
            1,
            2,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
        ),  # ADC1 only: ADC2 is unusable while Wi-Fi runs
        "pullups": (),
    },
    "esp32c3": {
        "piece": C3,
        "name": "ESP32-C3",
        "gnd": ("1", "2", "11", "14"),
        "v3": "3",
        "en": "8",
        "boot": ("23", 9),
        "dn": "26",
        "dp": "27",
        "pin": {
            0: "12",
            1: "13",
            2: "5",
            3: "6",
            4: "18",
            5: "19",
            6: "20",
            7: "21",
            8: "22",
            9: "23",
            10: "16",
        },
        "digital": (4, 5, 6, 7, 10, 0, 1, 3),
        "adc": (3, 4, 0, 1),  # ADC1; GPIO2 is a strapping pin
        "pullups": (
            (2, "5"),
            (8, "22"),
        ),  # GPIO2 and GPIO8 must read 1 at reset for the ROM loader
    },
}

WIRELESS = r"\b(bluetooth|ble|wi-?fi|wireless|internet|online|iot|esp32\w*)\b"


def chip_for(ask: str) -> str | None:
    """Which ESP32 the ask wants, or None when it does not want one (or names the Pico)."""
    a = ask.lower()
    if re.search(r"\bpico\b", a):
        return None
    if re.search(r"\b(esp32-?c3|c3)\b", a):
        return "esp32c3"
    if re.search(WIRELESS, a):
        return "esp32s3"
    return None


class _Refs:
    def __init__(self, parts: list[dict[str, Any]]):
        self.used = {p["ref"] for p in parts}

    def __call__(self, prefix: str) -> str:
        n = 1
        while f"{prefix}{n}" in self.used:
            n += 1
        self.used.add(f"{prefix}{n}")
        return f"{prefix}{n}"


def core(
    chip: str, gpio_pins: dict[str, str], refs: _Refs, power_led: bool = True
) -> list[dict[str, Any]]:
    """The module plus Espressif's reference power, reset, boot and USB parts. `gpio_pins` is module pin -> net.
    USB-C comes first so the simulation takes it as the supply."""
    c = CHIP[chip]
    name = c["name"]
    boot_pin, boot_gpio = c["boot"]
    mod_pins = {
        **{g: "GND" for g in c["gnd"]},
        c["v3"]: "P3V3",
        c["en"]: "EN",
        boot_pin: "BOOT",
        c["dn"]: "USB_DN_MCU",
        c["dp"]: "USB_DP_MCU",
        **gpio_pins,
    }
    j, u = refs("J"), refs("U")
    parts = [
        {
            "ref": j,
            "piece": USBC16,
            "value": "USB-C",
            "pins": {
                **{p: "VBUS" for p in ("A4", "A9", "B4", "B9")},
                **{p: "GND" for p in ("A1", "A12", "B1", "B12", "SH")},
                "A5": "CC1",
                "B5": "CC2",
                "A6": "USB_DP",
                "B6": "USB_DP",
                "A7": "USB_DN",
                "B7": "USB_DN",
            },
            "group": "power",
            "why": f"USB-C: 5 V in, and the {name}'s native USB for flashing and serial (no USB-UART chip needed).",
        },
        {
            "ref": refs("R"),
            "piece": R,
            "value": "5.1k",
            "pins": {"1": "CC1", "2": "GND"},
            "group": "power",
            "why": "CC1 pull-down: tells a USB-C source this is a sink, so it turns on 5 V.",
        },
        {
            "ref": refs("R"),
            "piece": R,
            "value": "5.1k",
            "pins": {"1": "CC2", "2": "GND"},
            "group": "power",
            "why": "CC2 pull-down, for the cable flipped the other way.",
        },
        {
            "ref": refs("R"),
            "piece": R,
            "value": "22",
            "pins": {"1": "USB_DP", "2": "USB_DP_MCU"},
            "group": "power",
            "why": "Series resistor on USB D+, as Espressif's design guide places near the chip.",
        },
        {
            "ref": refs("R"),
            "piece": R,
            "value": "22",
            "pins": {"1": "USB_DN", "2": "USB_DN_MCU"},
            "group": "power",
            "why": "Series resistor on USB D-.",
        },
        {
            "ref": refs("C"),
            "piece": CP,
            "value": "10u",
            "pins": {"1": "VBUS", "2": "GND"},
            "group": "power",
            "why": "10 uF at the power entrance (Espressif design guide).",
        },
        {
            "ref": refs("U"),
            "piece": LDO,
            "value": "AMS1117-3.3",
            "pins": {"3": "VBUS", "2": "P3V3", "1": "GND"},
            "group": "power",
            "why": "3.3 V regulator, 1 A: Espressif asks for at least 500 mA for the Wi-Fi transmit bursts.",
        },
        {
            "ref": refs("C"),
            "piece": CP,
            "value": "22u",
            "pins": {"1": "P3V3", "2": "GND"},
            "group": "power",
            "why": "Bulk capacitor on the 3.3 V rail: the LDO needs it to stay stable, and it feeds Wi-Fi bursts.",
        },
        {
            "ref": refs("C"),
            "piece": C_DISC,
            "value": "100n",
            "pins": {"1": "P3V3", "2": "GND"},
            "group": "power",
            "why": "0.1 uF decoupling on the module's 3V3 pin.",
        },
        {
            "ref": u,
            "piece": c["piece"],
            "value": f"{name} module",
            "pins": mod_pins,
            "group": "power",
            "why": f"{name} module (Espressif): the logic, 2.4 GHz Wi-Fi and Bluetooth LE, with its antenna "
            "at the board edge.",
        },
        {
            "ref": refs("R"),
            "piece": R,
            "value": "10k",
            "pins": {"1": "P3V3", "2": "EN"},
            "group": "power",
            "why": "EN pull-up (half of the reference RC delay).",
        },
        {
            "ref": refs("C"),
            "piece": C_DISC,
            "value": "1u",
            "pins": {"1": "EN", "2": "GND"},
            "group": "power",
            "why": "EN delay capacitor: with the 10k it holds the chip in reset until 3.3 V has settled.",
        },
        {
            "ref": refs("SW"),
            "piece": SW,
            "value": "RESET",
            "pins": {"1": "EN", "2": "GND"},
            "group": "input",
            "why": "RESET: pulls EN low to restart the chip.",
        },
        {
            "ref": refs("R"),
            "piece": R,
            "value": "10k",
            "pins": {"1": "P3V3", "2": "BOOT"},
            "group": "power",
            "why": f"Pull-up on the GPIO{boot_gpio} strapping pin, so it boots the program in flash.",
        },
        {
            "ref": refs("SW"),
            "piece": SW,
            "value": "BOOT",
            "pins": {"1": "BOOT", "2": "GND"},
            "group": "input",
            "why": f"BOOT: hold it (GPIO{boot_gpio} low) while tapping RESET to enter the USB flashing loader.",
        },
    ]
    for g, pin in c["pullups"]:
        net = f"STRAP{g}"
        mod_pins[pin] = net
        parts.append(
            {
                "ref": refs("R"),
                "piece": R,
                "value": "10k",
                "pins": {"1": "P3V3", "2": net},
                "group": "power",
                "why": f"Pull-up on strapping pin GPIO{g}: it must read 1 at reset.",
            }
        )
    if power_led:
        a = "PWR_A"
        parts += [
            {
                "ref": refs("R"),
                "piece": R,
                "value": "100",
                "pins": {"1": "P3V3", "2": a},
                "group": "output",
                "why": "Sets the power LED current.",
            },
            {
                "ref": refs("D"),
                "piece": LED,
                "value": "green",
                "pins": {"2": a, "1": "GND"},
                "group": "output",
                "why": "Shows the 3.3 V rail is up.",
            },
        ]
    return parts


FINE_PITCH = {
    "track": 0.2,
    "clearance": 0.15,
}  # the 16P USB-C has 0.5 mm pad pitch; the module 1.27 mm


def esp32_sensor(ask: str) -> dict[str, Any] | None:
    """ESP32 Wi-Fi sensor: the module on its reference power block, a DHT11 (temperature + humidity) or a 10k NTC
    (temperature only) or, with no sensor named, a 3-pin sensor header; readings over USB serial and Wi-Fi."""
    a = ask.lower()
    chip = chip_for(a)
    if not chip:
        return None
    sensorish = r"\b(sensor|sensing|temperature|thermo\w*|temp|humid\w*|weather|monitor|station)\b"
    if not re.search(r"\besp32", a) and not re.search(sensorish, a):
        return None
    if re.search(r"\b(strip|neopixels?|ws2812\w*|motors?|car|robot|leds?|light|lamp|arm|servo\w*|mic\w*)\b", a):
        return None  # other recipes build these; a Wi-Fi ask moves them onto the ESP32 through swap()
    from .recipes import BEYOND, EXTRAS  # recipes imports this module

    own = r"temperature|thermo\w*|humidity|weather|bluetooth|wi-?fi|esp32"
    if not all(re.fullmatch(own, w.group(0)) for w in BEYOND.finditer(a)) or re.search(
        EXTRAS, a
    ):
        return None
    c = CHIP[chip]
    humid = bool(re.search(r"\b(humid\w*|weather)\b", a))
    temp = humid or bool(
        re.search(r"\b(temperature|thermo\w*|temp|how (hot|cold|warm))\b", a)
    )
    refs = _Refs([])
    if humid:
        g = c["digital"][0]
        sense = {c["pin"][g]: "DHT_DATA"}
    elif temp:
        g = c["adc"][0]
        sense = {c["pin"][g]: "TEMP_ADC"}
    else:
        g = c["digital"][0]
        sense = {c["pin"][g]: "SENSOR_IO"}
    parts = core(chip, sense, refs)
    if humid:
        parts += [
            {
                "ref": refs("U"),
                "piece": DHT,
                "value": "DHT11",
                "pins": {"1": "P3V3", "2": "DHT_DATA", "4": "GND"},
                "group": "sensor",
                "why": f"DHT11: temperature and humidity on one data wire (GPIO{g}).",
            },
            {
                "ref": refs("R"),
                "piece": R,
                "value": "10k",
                "pins": {"1": "P3V3", "2": "DHT_DATA"},
                "group": "sensor",
                "why": "Pull-up the DHT11 datasheet asks for on its data line.",
            },
        ]
        what = f"a DHT11 temperature and humidity sensor on GPIO{g}"
    elif temp:
        parts += [
            {
                "ref": refs("R"),
                "piece": R,
                "value": "10k",
                "pins": {"1": "P3V3", "2": "TEMP_ADC"},
                "group": "sensor",
                "why": f"Divider top: with the 10k NTC about half of 3.3 V reaches GPIO{g} (ADC1) at 25 C.",
            },
            {
                "ref": "TH1",
                "piece": NTC,
                "value": "10k",
                "pins": {"1": "TEMP_ADC", "2": "GND"},
                "group": "sensor",
                "why": "NTC thermistor: its resistance falls as it warms, so the ADC voltage tracks temperature.",
            },
        ]
        what = f"a 10k NTC thermistor on GPIO{g} (ADC1)"
    else:
        parts.append(
            {
                "ref": refs("J"),
                "piece": HDR3,
                "value": "sensor",
                "pins": {"1": "P3V3", "2": "SENSOR_IO", "3": "GND"},
                "group": "sensor",
                "why": f"Sensor header: 3.3 V, GPIO{g}, GND. Plug any 3.3 V sensor in here.",
            }
        )
        what = f"whatever sensor you plug into the 3-pin header on GPIO{g}"
    return {
        "name": f"{chip}_wifi_sensor",
        "title": f"{c['name']} SENSOR",
        "chip": chip,
        "voltage": 5.0,
        "rules": dict(FINE_PITCH),
        "parts": parts,
        "scenarios": [{"name": "room"}],
        "benchmarks": [
            {
                "check": "room.D1.avg_ma",
                "min": 2,
                "max": 20,
                "text": "3.3 V rail up (power LED on)",
            },
            {
                "check": "room.supply_ma_avg",
                "min": 60,
                "max": 500,
                "text": f"{c['name']} drawing its Wi-Fi receive current, whole board inside USB's 500 mA",
            },
        ],
        "say": f"An {c['name']} module reads {what} and prints it over USB; add your Wi-Fi name and it serves the "
        "readings as JSON too. USB-C feeds an AMS1117 3.3 V regulator, with Espressif's EN delay, RESET and BOOT "
        "buttons.",
    }


def swap(
    design: dict[str, Any], catalog: dict[str, dict[str, Any]], chip: str
) -> dict[str, Any] | None:
    """The same design with its Pico / Pico W replaced by an ESP32 module on the reference power block: Pico GPIO
    nets move to free ESP32 GPIOs (analog ones to ADC1), VBUS/VSYS nets become the USB-C VBUS, the Pico's 3V3 net
    becomes the LDO's P3V3. None when the design has no Pico or a net cannot be placed."""
    c = CHIP[chip]
    if c["piece"] not in catalog or LDO not in catalog or USBC16 not in catalog:
        return None
    idx = next(
        (
            i
            for i, p in enumerate(design["parts"])
            if str(p.get("piece", "")).startswith("kicad:MCU_Module:RaspberryPi_Pico")
        ),
        None,
    )
    if idx is None:
        return None
    pico = design["parts"][idx]
    names = (catalog.get(pico["piece"]) or {}).get("pins") or {}
    rename: dict[str, str] = {}
    gpio_nets: list[tuple[str, int, bool]] = []  # (net, pico gpio, analog)
    for num, net in pico["pins"].items():
        nm = str(names.get(str(num), "")).upper()
        if nm in ("VBUS", "VSYS"):
            rename[net] = "VBUS"
        elif nm == "3V3":
            rename[net] = "P3V3"
        elif m := re.match(r"GPIO(\d+)(_ADC\d)?", nm):
            gpio_nets.append((net, int(m.group(1)), bool(m.group(2))))
    free_d = [g for g in c["digital"]]
    free_a = [g for g in c["adc"]]
    placed: dict[str, int] = {}
    moved: dict[int, int] = {}
    for net, old, analog in sorted(
        gpio_nets, key=lambda x: not x[2]
    ):  # analog first: ADC pins are scarcer
        pool = free_a if analog else free_d
        if not pool:
            return None
        g = pool.pop(0)
        for other in (free_a, free_d):
            if g in other:
                other.remove(g)
        placed[net] = g
        moved[old] = g
    d = {k: v for k, v in design.items()}
    rest = [
        dict(p, pins={k: rename.get(v, v) for k, v in p["pins"].items()})
        for i, p in enumerate(design["parts"])
        if i != idx
    ]
    refs = _Refs(rest + [{"ref": pico["ref"]}])
    refs.used.discard(pico["ref"])
    lit = {n for q in rest if q["piece"] == R and "P3V3" in q["pins"].values() for n in q["pins"].values()}
    has_led = any(str(q["piece"]).startswith("kicad:Device:LED@") and lit & set(q["pins"].values()) for q in rest)
    block = core(
        chip,
        {c["pin"][g]: net for net, g in placed.items()},
        refs,
        power_led=not has_led,
    )
    d["parts"] = block + rest
    d["chip"] = chip
    d["rules"] = dict(FINE_PITCH)
    d["benchmarks"] = [dict(b) for b in d.get("benchmarks") or []]
    for b in d["benchmarks"]:
        if "supply_ma" in b.get("check", "") and b.get("max") is not None:
            b["max"] = (
                b["max"] + 100
            )  # the module's Wi-Fi receive current (about 95 mA) on top of the old budget
    say = str(d.get("say", ""))
    say = re.sub(r"\bA Pico W\b|\bA Pico\b", f"An {c['name']}", say)
    say = re.sub(r"\bthe Pico W?\b", f"the {c['name']}", say)

    def gp(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return f"GPIO{moved.get(n, n)}"

    say = re.sub(r"\bGP(\d+)\b", gp, say)
    for k, adc_gpio in enumerate((26, 27, 28)):
        if adc_gpio in moved:
            say = re.sub(rf"\bADC{k}\b", f"GPIO{moved[adc_gpio]} (ADC)", say)
    d["say"] = (
        say.rstrip()
        + " Power: USB-C into an AMS1117 3.3 V regulator, with RESET and BOOT buttons."
    )
    return d

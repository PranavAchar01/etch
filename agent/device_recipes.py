"""Device recipes built from the full parts library: keyboards, macro pads, keypads and numpads.

Every piece here is a real library part with recorded provenance (agent/kicad_library.py):
- MX hotswap switch: ai03 MX_V2 footprint library (MIT), MX-Hotswap-1U with its socket STEP model
- Choc v2 hotswap switch: marbastlib (CERN-OHL-P)
- 1N4148 diode, WS2812B, Alps EC11E encoder, Raspberry Pi Pico: KiCad official libraries (CC-BY-SA 4.0 with the
  KiCad library exception)

The matrix is the usual COL2ROW layout (QMK / KMK): column line, switch, diode anode, diode cathode, row line."""

from __future__ import annotations

import math
import re
from typing import Any

PICO = "kicad:MCU_Module:RaspberryPi_Pico@RaspberryPi_Pico_Common_THT"
MX = "kicad:Switch:SW_Push@MX-Hotswap-1U"
CHOC = "kicad:Switch:SW_Push@SW_choc_v2_HS_CPG135001S30_1u"
DIODE = "kicad:Diode:1N4148@D_DO-35_SOD27_P7.62mm_Horizontal"
WS2812B = "kicad:LED:WS2812B@LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm"
EC11 = "kicad:Device:RotaryEncoder_Switch@ROT_Alps_EC11E-Switch"
LED = "kicad:Device:LED@LED_D5.0mm"
R = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
CP = "kicad:Device:C_Polarized@CP_Radial_D5.0mm_P2.00mm"

# Pico physical pins wired to GPIO, in board order (GND 3/8/13/18/23/28/38, 3V3 36, VSYS 39, VBUS 40)
PICO_GPIO = [
    1,
    2,
    4,
    5,
    6,
    7,
    9,
    10,
    11,
    12,
    14,
    15,
    16,
    17,
    19,
    20,
    21,
    22,
    24,
    25,
    26,
    27,
    29,
    31,
    32,
    34,
]
# the key block sits to the Pico's right, so matrix lines take the right-hand pin row first (pins 21-34) and
# no line has to route around the module (the first 9-key build spent 331 s routing ROW0 from pin 1)
MATRIX_GPIO = [34, 32, 31, 29, 27, 26, 25, 24, 22, 21, 20, 19, 17, 16, 15, 14, 12, 11, 10, 9, 7, 6, 5, 4, 2, 1]
KEY_PITCH = 19.05  # MX and Choc spacing, centre to centre

KEYS_RX = re.compile(
    r"\b(key ?boards?|macro ?pads?|key ?pads?|num ?pads?|number ?pads?|stream ?decks?|hot ?swap|"
    r"mechanical (key|switch)\w*|\d+ ?%|(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|sixteen)"
    r"[ -]?(keys?|pads?|buttons? macro)\b)"
)
WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "twelve": 12, "sixteen": 16,
}  # fmt: skip
NOT_KEYS = re.compile(
    r"\b(piano|keyboard (synth|instrument)|midi keyboard|music|musical|synth\w*)\b"
)


def _count(a: str) -> int:
    m = re.search(
        r"\b(\d+|" + "|".join(WORDS) + r")[ -]?(keys?|pads?|buttons?|switch\w*)\b", a
    )
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else WORDS[m.group(1)]
        if 1 <= n <= 104:
            return n
    if re.search(r"\b(macro ?pad|stream ?deck)", a):
        return 9
    if re.search(r"\b(num ?pad|number ?pad|key ?pad)", a):
        return 17
    if re.search(r"\b40 ?%", a):
        return 47
    if re.search(r"\b65 ?%", a):
        return 68
    return 61  # a regular / 60% board: 5 rows of up to 14


def _grid(n: int) -> tuple[int, int]:
    """(rows, cols) for n keys, at most 14 columns and within the Pico's 26 GPIO."""
    if n <= 4:
        return 1, n
    cols = min(14, math.ceil(math.sqrt(n)))
    rows = math.ceil(n / cols)
    return rows, cols


def keyboard(ask: str) -> dict[str, Any] | None:
    """A key matrix on a Raspberry Pi Pico: N hotswap switches, one 1N4148 per key (so any keys can be held at
    once), optional per-key WS2812B RGB and an EC11 knob."""
    a = ask.lower()
    if not KEYS_RX.search(a) or NOT_KEYS.search(a):
        return None
    n = _count(a)
    rows, cols = _grid(n)
    rgb = bool(
        re.search(
            r"\b(rgb|neopixel|ws2812|backlit|backlight|underglow|rainbow|led|leds|light\w*)\b",
            a,
        )
    )
    knob = bool(re.search(r"\b(knob|encoder|volume|dial|ec11)\b", a))
    choc = bool(re.search(r"\b(choc|low ?profile|kailh)\b", a))
    sw = CHOC if choc else MX
    free = list(MATRIX_GPIO)
    row_pin = {f"ROW{r}": free.pop(0) for r in range(rows)}
    col_pin = {f"COL{c}": free.pop(0) for c in range(cols)}
    extra: dict[str, str] = {str(p): net for net, p in {**row_pin, **col_pin}.items()}
    extra.update({"36": "P3V3", "40": "VBUS"})
    if rgb:
        extra[str(free.pop(0))] = "RGB_DIN"
    if knob:
        for net in ("ENC_A", "ENC_B", "ENC_SW"):
            extra[str(free.pop(0))] = net
    pins = {"3": "GND", "38": "GND", **extra}
    parts: list[dict[str, Any]] = [
        {
            "ref": "U1",
            "piece": PICO,
            "pins": pins,
            "group": "power",
            "why": "USB-C power and the USB keyboard link; it scans the key matrix (KMK or QMK firmware).",
        }
    ]
    for i in range(n):
        r, c = divmod(i, cols)
        parts.append(
            {
                "ref": f"SW{i + 1}",
                "piece": sw,
                "pins": {"1": f"COL{c}", "2": f"K{i + 1}"},
                "group": "keys",
                "why": ("Kailh Choc v2" if choc else "Cherry MX")
                + " hotswap socket: plug in any switch, no solder.",
            }
        )
        parts.append(
            {
                "ref": f"D{i + 1}",
                "piece": DIODE,
                "value": "1N4148",
                "pins": {"2": f"K{i + 1}", "1": f"ROW{r}"},
                "group": "keys",
                "why": "Stops ghosting so any keys can be held together.",
            }
        )
    leds = 0
    if rgb:
        leds = n if n <= 16 else 8
        for i in range(leds):
            din = "RGB_DIN" if i == 0 else f"RGB_D{i}"
            parts.append(
                {
                    "ref": f"LED{i + 1}",
                    "piece": WS2812B,
                    "pins": {"1": "VBUS", "2": f"RGB_D{i + 1}", "3": "GND", "4": din},
                    "group": "lights",
                    "why": "Per-key RGB" if leds == n else "RGB underglow",
                }
            )
        parts.append(
            {
                "ref": "C1",
                "piece": CP,
                "value": "100u",
                "pins": {"1": "VBUS", "2": "GND"},
                "group": "lights",
                "why": "Bulk capacitor on the LED supply, as the WS2812B datasheet recommends.",
            }
        )
    if knob:
        parts.append(
            {
                "ref": "ENC1",
                "piece": EC11,
                "pins": {
                    "A": "ENC_A",
                    "B": "ENC_B",
                    "C": "GND",
                    "S1": "ENC_SW",
                    "S2": "GND",
                },
                "group": "keys",
                "why": "EC11 knob with a push switch, for volume or scrolling.",
            }
        )
    parts += [
        {
            "ref": "R1",
            "piece": R,
            "value": "100",
            "pins": {"1": "P3V3", "2": "PWR_A"},
            "group": "power",
            "why": "Sets the power LED current.",
        },
        {
            "ref": "DP1",
            "piece": LED,
            "value": "green",
            "pins": {"2": "PWR_A", "1": "GND"},
            "group": "power",
            "why": "Shows the board is powered.",
        },
    ]
    blocks: list[dict[str, Any]] = [
        {"name": "power", "valign": "center", "rows": [["U1"], ["R1", "DP1"]]},
    ]
    keys: list[list[str]] = [["@title", "@title"]]
    for r in range(rows):
        idx = [i for i in range(r * cols, min(n, (r + 1) * cols))]
        keys.append([f"SW{i + 1}" for i in idx])
        keys.append([f"D{i + 1}" for i in idx])
    if knob:
        keys[0].append("ENC1")
    # each switch row plus its diode row spans one key pitch: DO-35 courtyard 2.59 mm, block gap 2.5 mm
    sw_h = KEY_PITCH - 2.59 - 2 * 2.5
    blocks.append({"name": "keys", "pitch": KEY_PITCH, "row_h": [0.0] + [sw_h, 0.0] * rows, "rows": keys})
    if leds:
        lr = [f"LED{i + 1}" for i in range(leds)]
        blocks.append({"name": "lights", "rows": [lr[k : k + cols] for k in range(0, leds, cols)] + [["C1"]]})
    kind = "macro pad" if n <= 16 else "numpad" if n <= 24 else "keyboard"
    return {
        "name": "keyboard",
        "title": f"{n}-KEY {kind.upper()}",
        "voltage": 5.0,
        "parts": parts,
        "layout_blocks": blocks,
        "rules": {"matrix": {"rows": rows, "cols": cols, "diode_direction": "COL2ROW"}},
        "scenarios": [{"name": "on"}],
        "benchmarks": [
            {"check": "on.DP1.avg_ma", "min": 2, "text": "Power LED on"},
            {"check": "on.DP1.peak_ma", "max": 25, "text": "Power LED under 25 mA"},
        ],
        "firmware": {"commands": [], "inputs": sorted(row_pin) + sorted(col_pin)},
        "say": f"A {n}-key {kind} on a Raspberry Pi Pico: {rows}x{cols} matrix of "
        + ("Choc" if choc else "MX")
        + " hotswap sockets with a 1N4148 per key"
        + (f", {leds} WS2812B RGB LEDs" if leds else "")
        + (", an EC11 knob" if knob else "")
        + ".",
    }


DEVICE_RECIPES = (keyboard,)

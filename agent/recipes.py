"""Lego routing: common asks are assembled from known-good blocks in plain code, with no model call.

  match(ask, kept) -> (design, recipe name) or None

A design here has the same shape the picker returns (parts, firmware, scenarios, benchmarks, say), so it goes
through the same assembly, place and route, ERC, DRC, firmware test and ngspice gates as any other design.
Anything that does not match a recipe goes to the picker.
"""

from __future__ import annotations

import json
import re
from typing import Any

PICO = "kicad:MCU_Module:RaspberryPi_Pico@RaspberryPi_Pico_Common_THT"
PICO_W = "kicad:MCU_Module:RaspberryPi_Pico_W@RaspberryPi_Pico_Common_THT"
RGB = "kicad:Device:LED_RKGB@LED_D5.0mm-4_RGB_Wide_Pins"
LED = "kicad:Device:LED@LED_D5.0mm"
R = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
CP = "kicad:Device:C_Polarized@CP_Radial_D5.0mm_P2.00mm"
NPN = "kicad:Transistor_BJT:2N3904@TO-92_Inline"
MIC = "kicad:Connector_Generic:Conn_01x03@PinSocket_1x03_P2.54mm_Vertical"
HDR2 = "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical"
BUZZ = "kicad:Device:Buzzer@Buzzer_12x9.5RM7.6"
LDR = "kicad:Sensor_Optical:A1050@R_LDR_D6.4mm_P3.4mm_Vertical"

COLOURS = {
    "red": ("R",),
    "green": ("G",),
    "blue": ("B",),
    "yellow": ("R", "G"),
    "orange": ("R", "G"),
    "purple": ("R", "B"),
    "magenta": ("R", "B"),
    "pink": ("R", "B"),
    "cyan": ("G", "B"),
    "white": ("R", "G", "B"),
    "off": (),
}
COLOUR_RX = "|".join(COLOURS)


def _pico(extra: dict[str, str]) -> dict[str, Any]:
    pins = {"3": "GND", "38": "GND", **extra}
    return {
        "ref": "U1",
        "piece": PICO,
        "pins": pins,
        "group": "power",
        "why": "USB power, the laptop link and the logic.",
    }


def _rgb_block(group: str = "output") -> list[dict[str, Any]]:
    """RGB LED on Pico pins 20/21/22 (GPIO15/16/17), each die through its own resistor (3.3 V drive)."""
    return [
        {
            "ref": "D1",
            "piece": RGB,
            "value": "RGB",
            "pins": {"1": "LED_R_A", "2": "GND", "3": "LED_G_A", "4": "LED_B_A"},
            "group": group,
            "why": "One LED that shows red, green or blue.",
        },
        {
            "ref": "R1",
            "piece": R,
            "value": "150",
            "pins": {"1": "LED_R", "2": "LED_R_A"},
            "group": group,
            "why": "Limits the red die current.",
        },
        {
            "ref": "R2",
            "piece": R,
            "value": "68",
            "pins": {"1": "LED_G", "2": "LED_G_A"},
            "group": group,
            "why": "Limits the green die current.",
        },
        {
            "ref": "R3",
            "piece": R,
            "value": "33",
            "pins": {"1": "LED_B", "2": "LED_B_A"},
            "group": group,
            "why": "Limits the blue die current.",
        },
    ]


RGB_GPIO = {"20": "LED_R", "21": "LED_G", "22": "LED_B"}


def _nets(colour: str) -> list[str]:
    return [f"LED_{c}" for c in COLOURS[colour]]


def voice_led(ask: str) -> dict[str, Any] | None:
    a = ask.lower()
    if not re.search(
        r"\b(speak|speaking|talk|talking|voice|sound|noise|clap|loud|mic|microphone|sing|hear)",
        a,
    ):
        return None
    if not re.search(r"\b(led|light|lights|glow|colou?r)", a):
        return None
    loud, quiet = "red", "green"
    # "red whenever I speak, green when I don't": the colour before the sound word is the loud one
    m = re.search(
        rf"\b({COLOUR_RX})\b[^.,;]*?\b(speak|talk|voice|sound|noise|clap|loud|sing)", a
    )
    if m:
        loud = m.group(1)
    q = re.search(
        rf"\b({COLOUR_RX})\b[^.,;]*?\b(don'?t|do not|quiet|silen|not|stop|otherwise|else)",
        a,
    )
    if q and q.group(1) != loud:
        quiet = q.group(1)
    elif quiet == loud:
        quiet = "off"
    parts = [
        _pico({**RGB_GPIO, "31": "MIC_OUT", "36": "P3V3"}),
        {
            "ref": "MK1",
            "piece": MIC,
            "value": "MAX4466",
            "pins": {"1": "P3V3", "2": "GND", "3": "MIC_OUT"},
            "group": "sensor",
            "why": "Electret mic and amplifier module plugs in here.",
        },
        *_rgb_block(),
    ]
    return {
        "name": "voice_led",
        "title": "VOICE LED",
        "voltage": 5.0,
        "parts": parts,
        "firmware": {
            "mode": "sound",
            "input_net": "MIC_OUT",
            "loud_high": _nets(loud),
            "quiet_high": _nets(quiet),
        },
        "benchmarks": [],
        "say": f"A Pico listens to a mic amplifier and turns the LED {loud} while you speak, {quiet} when it is quiet.",
    }


def pitch_rainbow(ask: str) -> dict[str, Any] | None:
    """Singing or humming lights an RGB LED in the colour of the note: low red through high violet (Pranav's
    prompt family, 10-17). Same mic + RGB board as voice_led; the firmware finds the pitch (firmware.py, pitch
    mode) instead of just loud or quiet."""
    a = ask.lower()
    if not re.search(
        r"\b(sing|sings|singing|sung|hum|humming|whistl\w*|voice|mic|microphone|note|notes|pitch\w*)\b",
        a,
    ):
        return None
    if not re.search(
        r"\b(pitch\w*|notes?|rainbow|higher|lower|frequenc\w*|tones?|tuner)\b", a
    ):
        return None
    if not re.search(r"\b(led|light|lights|lit|glow\w*|colou?rs?|rainbow|hue)\b", a):
        return None
    from . import firmware  # noqa: PLC0415

    bands = firmware.pitch_bands({"R": "LED_R", "G": "LED_G", "B": "LED_B"})
    parts = [
        _pico({**RGB_GPIO, "31": "MIC_OUT", "36": "P3V3"}),
        {
            "ref": "MK1",
            "piece": MIC,
            "value": "MAX4466",
            "pins": {"1": "P3V3", "2": "GND", "3": "MIC_OUT"},
            "group": "sensor",
            "why": "Electret mic and amplifier module (MAX4466 with gain trim) plugs in here; its output rests at "
            "half the supply and carries your voice.",
        },
        *_rgb_block(),
    ]
    return {
        "name": "pitch_rainbow",
        "title": "PITCH RAINBOW",
        "voltage": 5.0,
        "parts": parts,
        "firmware": {
            "mode": "pitch",
            "input_net": "MIC_OUT",
            "bands": bands,
            "quiet_high": [],
        },
        "benchmarks": [],
        "say": "A Pico listens through a mic amplifier, works out the pitch you sing and lights the RGB LED in that "
        "rainbow colour: red for low notes, then yellow, green, cyan, blue, and violet for the highest. "
        "Silence turns it off.",
    }


def typed_rgb(ask: str) -> dict[str, Any] | None:
    a = ask.lower()
    # "an RGB LED controlled by a Pico": no keys named, so the laptop picks the colour with r / g / b (sweep 10-17,
    # the picker wired the resistors into the GPIO pins three times)
    rgb_pico = bool(
        re.search(r"\brgb\b", a) and re.search(r"\b(pico|rp2040|microcontroller)\b", a)
    )
    # typing on a computer, not pressing a button on the board
    if not rgb_pico and not re.search(
        r"\b(type|types|typing|typed|keyboard|laptop|computer|serial|usb)\b", a
    ):
        return None
    pairs = re.findall(
        rf"\b(?:type|types|press|send|hit|enter)\s+(?:a |an |the )?[\"'“]?(\w)\b[\"'”]?[^.;]*?\b({COLOUR_RX})\b",
        a,
    )
    # "type r for red and g for green": later keys in the same list without their own verb
    pairs += [
        (k, c)
        for k, c in re.findall(
            rf"[\"'“]?\b(\w)\b[\"'”]?\s+(?:for|=|is|makes it|turns it)\s+({COLOUR_RX})\b",
            a,
        )
        if k not in ("a", "i")
    ]
    seen: dict[str, str] = {}
    for key, col in pairs:
        seen.setdefault(key, col)
    if not seen:
        # "type red and it turns red": the firmware reads one character, so the key is the colour's first letter
        words = re.findall(rf"\b(?:type|types|send|enter)\s+[\"'“]?({COLOUR_RX})\b", a)
        firsts = [w[0] for w in words]
        if words and len(set(firsts)) == len(set(words)):
            seen = {w[0]: w for w in words}
    if not seen and rgb_pico:
        seen = {"r": "red", "g": "green", "b": "blue"}
    if not seen:
        return None
    cmds = [
        {"input": k, "high": _nets(c), "does": f"turns the LED {c}"}
        for k, c in seen.items()
    ]
    return {
        "name": "rgb_typing",
        "title": "TYPE RGB",
        "voltage": 5.0,
        "parts": [_pico(dict(RGB_GPIO)), *_rgb_block()],
        "firmware": {"commands": cmds},
        "benchmarks": [],
        "say": "A Pico reads what you type and sets the RGB LED: "
        + ", ".join(f"{k} {c}" for k, c in seen.items())
        + ".",
    }


def _astable(
    left: tuple[str, str], right: tuple[str, str] | None
) -> list[dict[str, Any]]:
    """Two-transistor astable at about 1.5 Hz (47k, 10u). left/right = (ref, colour) of the LED on each side."""
    parts = [
        {
            "ref": "R3",
            "piece": R,
            "value": "47k",
            "pins": {"1": "VCC", "2": "Q1_B"},
            "group": "timing",
            "why": "Sets Q1's half of the blink.",
        },
        {
            "ref": "R4",
            "piece": R,
            "value": "47k",
            "pins": {"1": "VCC", "2": "Q2_B"},
            "group": "timing",
            "why": "Sets Q2's half of the blink.",
        },
        {
            "ref": "C1",
            "piece": CP,
            "value": "10u",
            "pins": {"1": "Q1_C", "2": "Q2_B"},
            "group": "timing",
            "why": "Times the blink with R4.",
        },
        {
            "ref": "C2",
            "piece": CP,
            "value": "10u",
            "pins": {"1": "Q2_C", "2": "Q1_B"},
            "group": "timing",
            "why": "Times the blink with R3.",
        },
        {
            "ref": "Q1",
            "piece": NPN,
            "pins": {"3": "Q1_C", "2": "Q1_B", "1": "GND"},
            "group": "timing",
            "why": "Switches the first LED.",
        },
        {
            "ref": "Q2",
            "piece": NPN,
            "pins": {"3": "Q2_C", "2": "Q2_B", "1": "GND"},
            "group": "timing",
            "why": "Switches the second side.",
        },
        {
            "ref": "R1",
            "piece": R,
            "value": "470",
            "pins": {"1": "VCC", "2": "LED1_A"},
            "group": "output",
            "why": "Limits the LED current.",
        },
        {
            "ref": left[0],
            "piece": LED,
            "value": left[1].upper(),
            "pins": {"2": "LED1_A", "1": "Q1_C"},
            "group": "output",
            "why": "Blinks.",
        },
    ]
    if right:
        parts += [
            {
                "ref": "R2",
                "piece": R,
                "value": "470",
                "pins": {"1": "VCC", "2": "LED2_A"},
                "group": "output",
                "why": "Limits the LED current.",
            },
            {
                "ref": right[0],
                "piece": LED,
                "value": right[1].upper(),
                "pins": {"2": "LED2_A", "1": "Q2_C"},
                "group": "output",
                "why": "Blinks in turn.",
            },
        ]
    else:
        parts.append(
            {
                "ref": "R2",
                "piece": R,
                "value": "1k",
                "pins": {"1": "VCC", "2": "Q2_C"},
                "group": "timing",
                "why": "Collector load for Q2.",
            }
        )
    return parts


def blinker(ask: str) -> dict[str, Any] | None:
    a = ask.lower()
    if not re.search(r"\b(blink\w*|flash\w*|police|alternat\w*|strobe)\b", a):
        return None
    if re.search(r"\b(type|laptop|speak|button|dark|buzz|beep|fan|motor)\b", a):
        return None
    cols = [
        c
        for c in re.findall(rf"\b({COLOUR_RX})\b", a)
        if c in ("red", "green", "blue", "yellow", "white")
    ]
    two = (
        "police" in a
        or re.search(r"\b(two|2|alternat\w*|pair)\b", a)
        or len(set(cols)) >= 2
    )
    if "police" in a and len(set(cols)) < 2:
        cols = ["red", "blue"]
    c1 = cols[0] if cols else "red"
    c2 = next((c for c in cols if c != c1), c1)
    parts = [
        {
            "ref": "J1",
            "piece": HDR2,
            "pins": {"1": "VCC", "2": "GND"},
            "group": "power",
            "why": "5 V in.",
        }
    ]
    parts += _astable(("D1", c1), ("D2", c2) if two else None)
    bench = [
        {"check": "D1.blink_hz", "min": 0.5, "max": 4, "text": "D1 blinks"},
        {
            "check": "D1.duty",
            "min": 0.2,
            "max": 0.8,
            "text": "D1 on about half the time",
        },
        {"check": "D1.peak_ma", "max": 25, "text": "D1 under 25 mA"},
    ]
    if two:
        bench += [
            {"check": "D2.blink_hz", "min": 0.5, "max": 4, "text": "D2 blinks"},
            {"check": "D2.peak_ma", "max": 25, "text": "D2 under 25 mA"},
        ]
    return {
        "name": "police_flasher" if two else "blinker",
        "title": "POLICE FLASH" if "police" in a else "BLINKER",
        "voltage": 5.0,
        "parts": parts,
        "benchmarks": bench,
        "say": f"A two-transistor astable blinks {'a ' + c1 + ' and a ' + c2 + ' LED in turn' if two else 'a ' + c1 + ' LED'} at about 1.5 Hz.",
    }


def dark_beeper(ask: str) -> dict[str, Any] | None:
    a = ask.lower()
    if not (
        re.search(r"\b(dark|night|lights? (go|goes|turn\w*) off)\b", a)
        and re.search(r"\b(beep\w*|buzz\w*|alarm|sound)\b", a)
    ):
        return None
    parts = [
        {
            "ref": "J1",
            "piece": HDR2,
            "pins": {"1": "VCC", "2": "GND"},
            "group": "power",
            "why": "5 V in.",
        },
        {
            "ref": "LDR1",
            "piece": LDR,
            "pins": {"1": "Q1_B", "2": "GND"},
            "group": "sensor",
            "why": "Resistance rises in the dark.",
        },
        {
            "ref": "R2",
            "piece": R,
            "value": "100k",
            "pins": {"1": "VCC", "2": "Q1_B"},
            "group": "sensor",
            "why": "Divider top: lifts the base in the dark.",
        },
        {
            "ref": "Q1",
            "piece": NPN,
            "pins": {"3": "BUZ_N", "2": "Q1_B", "1": "GND"},
            "group": "driver",
            "why": "Switches the buzzer.",
        },
        {
            "ref": "BZ1",
            "piece": BUZZ,
            "pins": {"1": "VCC", "2": "BUZ_N"},
            "group": "output",
            "why": "Beeps.",
        },
    ]
    return {
        "name": "dark_beeper",
        "title": "DARK BEEPER",
        "voltage": 5.0,
        "parts": parts,
        "scenarios": [
            {"name": "dark", "ldr": "dark"},
            {"name": "light", "ldr": "light"},
        ],
        "benchmarks": [
            {"check": "dark.BZ1.avg_ma", "min": 5, "text": "beeps in the dark"},
            {"check": "light.BZ1.avg_ma", "max": 0.5, "text": "silent in the light"},
        ],
        "say": "A light sensor and one transistor sound the buzzer when it gets dark.",
    }


def dark_light(ask: str) -> dict[str, Any] | None:
    """Night light: the dark_beeper sensor and switch, driving an LED through a 220 ohm resistor. With a button in
    the ask (sweep 10-17: "a night light with one led and a button" lost its button), a push button feeds the LED
    side, so the LED lights in the dark while it is pressed."""
    a = ask.lower()
    if not (
        re.search(r"\b(dark|night|nightlight|lights? (go|goes|turn\w*) off|dusk)\b", a)
        and re.search(r"\b(leds?|lights?|lamp|glow\w*)\b", a)
        and not re.search(r"\b(beep\w*|buzz\w*|alarm|sound)\b", a)
    ):
        return None
    colour = next((c for c in COLOURS if re.search(rf"\b{c}\b", a)), None)
    button = bool(re.search(r"\b(button|push ?button|switch|press\w*)\b", a))
    led_vcc = "VCC_SW" if button else "VCC"
    parts = [
        {
            "ref": "J1",
            "piece": HDR2,
            "pins": {"1": "VCC", "2": "GND"},
            "group": "power",
            "why": "5 V in.",
        },
        {
            "ref": "LDR1",
            "piece": LDR,
            "pins": {"1": "Q1_B", "2": "GND"},
            "group": "sensor",
            "why": "Resistance rises in the dark.",
        },
        {
            "ref": "R2",
            "piece": R,
            "value": "47k",
            "pins": {"1": "VCC", "2": "Q1_B"},
            "group": "sensor",
            "why": "Divider top: lifts the base in the dark.",
        },
        {
            "ref": "Q1",
            "piece": NPN,
            "pins": {"3": "LED_K", "2": "Q1_B", "1": "GND"},
            "group": "driver",
            "why": "Switches the LED.",
        },
        {
            "ref": "R3",
            "piece": R,
            "value": "220",
            "pins": {"1": led_vcc, "2": "LED_A"},
            "group": "output",
            "why": "Sets the LED current.",
        },
        {
            "ref": "D1",
            "piece": LED,
            "value": colour,
            "pins": {"2": "LED_A", "1": "LED_K"},
            "group": "output",
            "why": "Lights in the dark.",
        },
    ]
    if button:
        parts.append(
            {
                "ref": "SW1",
                "piece": SW,
                "pins": {"1": "VCC", "2": "VCC_SW"},
                "group": "input",
                "why": "Push button: the LED side only gets power while it is pressed.",
            }
        )
    scen = [{"name": "dark", "ldr": "dark"}, {"name": "light", "ldr": "light"}]
    bench = [
        {"check": "dark.D1.avg_ma", "min": 2, "text": "LED on in the dark"},
        {"check": "light.D1.avg_ma", "max": 0.5, "text": "LED off in the light"},
        {"check": "dark.D1.peak_ma", "max": 25, "text": "LED under 25 mA"},
    ]
    if button:
        scen = [dict(x, switch="pressed") for x in scen] + [{"name": "released", "ldr": "dark", "switch": "released"}]
        bench.append({"check": "released.D1.avg_ma", "max": 0.5, "text": "LED off with the button up"})
    return {
        "name": "night_light",
        "title": "NIGHT LIGHT",
        "voltage": 5.0,
        "parts": parts,
        "scenarios": scen,
        "benchmarks": bench,
        "say": "A light sensor and one transistor turn the LED on when the room gets dark"
        + (", while you hold the button." if button else "."),
    }


SW = "kicad:Switch:SW_Push@SW_PUSH_6mm"


def button_led(ask: str) -> dict[str, Any] | None:
    """Press to light: push button in series with a 220 ohm resistor and an LED."""
    a = ask.lower()
    if not (
        re.search(r"\b(button|push|press\w*|switch)\b", a)
        and re.search(r"\b(leds?|light\w*|lamp|glow\w*)\b", a)
        and not re.search(
            r"\b(blink\w*|flash\w*|dark|night|type|typing|buzz\w*|beep\w*)\b", a
        )
    ):
        return None
    colour = next((c for c in COLOURS if re.search(rf"\b{c}\b", a)), None)
    return {
        "name": "button_light",
        "title": "PRESS LIGHT",
        "voltage": 5.0,
        "parts": [
            {
                "ref": "J1",
                "piece": HDR2,
                "pins": {"1": "VCC", "2": "GND"},
                "group": "power",
                "why": "5 V in.",
            },
            {
                "ref": "SW1",
                "piece": SW,
                "pins": {"1": "VCC", "2": "SW_OUT"},
                "group": "input",
                "why": "Closes while pressed.",
            },
            {
                "ref": "R1",
                "piece": R,
                "value": "220",
                "pins": {"1": "SW_OUT", "2": "LED_A"},
                "group": "output",
                "why": "Sets the LED current.",
            },
            {
                "ref": "D1",
                "piece": LED,
                "value": colour,
                "pins": {"2": "LED_A", "1": "GND"},
                "group": "output",
                "why": "Lights while the button is held.",
            },
        ],
        "scenarios": [
            {"name": "pressed", "switch": "pressed"},
            {"name": "released", "switch": "released"},
        ],
        "benchmarks": [
            {"check": "pressed.D1.avg_ma", "min": 2, "text": "LED on while pressed"},
            {
                "check": "released.D1.avg_ma",
                "max": 0.5,
                "text": "LED off when released",
            },
            {"check": "pressed.D1.peak_ma", "max": 25, "text": "LED under 25 mA"},
        ],
        "say": "Press the button and the LED lights. One resistor keeps it safe.",
    }


BUF126 = "kicad:74xx:74LS126@DIP-14_W7.62mm"  # 74HC126 function and pinout (KiCad's symbol is named 74LS126)
CP_BULK = "kicad:Device:C_Polarized@CP_Radial_D8.0mm_P3.50mm"
C_DISC = "kicad:Device:C@C_Disc_D5.0mm_W2.5mm_P2.50mm"
BARREL_IN = "kicad:Connector:Barrel_Jack@BarrelJack_Horizontal"
SERVO_HDR = "kicad:Connector_Generic:Conn_01x03@PinHeader_1x03_P2.54mm_Vertical"


def voice_arm(ask: str) -> dict[str, Any] | None:
    """SO-101 voice arm: a Pico hears a keyword on the MAX4466 mic and drives the STS3215 bus servos over a
    half-duplex link. The link is the Feetech/Waveshare pattern: one 74HC126 gate puts the Pico's TX on the bus
    while TX_EN is high, a second gate hands the bus back to the Pico's RX while RX_EN is high."""
    a = ask.lower()
    if not re.search(r"\b(arm|so-?10[01]|servo|robot)", a):
        return None
    if not re.search(
        r"\b(voice|speak|talk|say|word|command|punch|jab|wave|listen|mic)", a
    ):
        return None
    parts = [
        _pico(
            {
                "1": "SERVO_TX",
                "2": "SERVO_RX",
                "4": "TX_EN",
                "5": "RX_EN",
                "20": "LED_LISTEN",
                "31": "MIC_OUT",
                "36": "P3V3",
            }
        ),
        {
            "ref": "MK1",
            "piece": MIC,
            "value": "MAX4466",
            "pins": {"1": "P3V3", "2": "GND", "3": "MIC_OUT"},
            "group": "sensor",
            "why": "Electret mic and amplifier module plugs in here: the Pico listens for the commands.",
        },
        {
            "ref": "U2",
            "piece": BUF126,
            "value": "74HC126",
            "pins": {
                "1": "TX_EN",
                "2": "SERVO_TX",
                "3": "SERVO_DATA",
                "4": "RX_EN",
                "5": "DATA_IN",
                "6": "SERVO_RX",
                "7": "GND",
                "9": "GND",
                "10": "GND",
                "12": "GND",
                "13": "GND",
                "14": "P3V3",
            },
            "group": "bus",
            "why": "Half-duplex servo bus: one gate sends the Pico's TX, another hands the line back to RX.",
        },
        {
            "ref": "R1",
            "piece": R,
            "value": "1k",
            "pins": {"1": "SERVO_DATA", "2": "DATA_IN"},
            "group": "bus",
            "why": "Limits current into the receive gate when a servo answers.",
        },
        {
            "ref": "R2",
            "piece": R,
            "value": "10k",
            "pins": {"1": "P3V3", "2": "SERVO_DATA"},
            "group": "bus",
            "why": "Holds the servo line high while nobody is talking on it.",
        },
        {
            "ref": "C2",
            "piece": C_DISC,
            "value": "100n",
            "pins": {"1": "P3V3", "2": "GND"},
            "group": "bus",
            "why": "Decouples the buffer chip.",
        },
        {
            "ref": "J1",
            "piece": BARREL_IN,
            "value": "7.4V",
            "pins": {"1": "VSERVO", "2": "GND"},
            "group": "power",
            "why": "Servo power in (STS3215: 7.4 V supply, about 2 A per moving joint).",
        },
        {
            "ref": "C1",
            "piece": CP_BULK,
            "value": "470u",
            "pins": {"1": "VSERVO", "2": "GND"},
            "group": "power",
            "why": "Bulk capacitor: rides through the current spikes when several servos start at once.",
        },
        {
            "ref": "J2",
            "piece": SERVO_HDR,
            "value": "STS3215 bus",
            "pins": {"1": "GND", "2": "VSERVO", "3": "SERVO_DATA"},
            "group": "bus",
            "why": "Servo bus header: GND, V+, DATA, daisy-chained to the six SO-101 joints.",
        },
        {
            "ref": "D1",
            "piece": LED,
            "value": "green",
            "pins": {"1": "GND", "2": "LED_LISTEN_A"},
            "group": "output",
            "why": "Lights while the Pico hears speech.",
        },
        {
            "ref": "R3",
            "piece": R,
            "value": "100",
            "pins": {"1": "LED_LISTEN", "2": "LED_LISTEN_A"},
            "group": "output",
            "why": "Limits the listen LED current.",
        },
    ]
    return {
        "name": "voice_arm",
        "title": "VOICE ARM",
        "firmware_pack": "so101_voice_arm",  # agent/firmware_packs/<name>, added to the export zip
        "voltage": 5.0,
        "parts": parts,
        "firmware": {
            "mode": "sound",
            "input_net": "MIC_OUT",
            "loud_high": ["LED_LISTEN"],
            "quiet_high": [],
        },
        "benchmarks": [],
        "say": "A Pico listens for punch, jab, wave and rest on a MAX4466 mic and drives the SO-101's STS3215 servos "
        "over a half-duplex 74HC126 bus.",
    }


OLED = "kicad:Connector_Generic:Conn_01x06@PinSocket_1x06_P2.54mm_Vertical"  # Adafruit 931 SSD1306 I2C module socket
NTC = "kicad:Device:Thermistor_NTC@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
DHT = "kicad:Sensor:DHT11@Aosong_DHT11_5.5x12.0_P2.54mm"
TRIM = "kicad:Device:R_Potentiometer_Trim@Potentiometer_Bourns_3296W_Vertical"
DISPLAY_RX = r"\b(oled|display|screen|lcd)\b"


def _oled(ref: str = "J1") -> dict[str, Any]:
    """SSD1306 128x32 I2C module (Adafruit 931 pinout: VIN 3V3 GND RST SCL SDA) on the Pico's I2C0 (GP4/GP5)."""
    return {
        "ref": ref,
        "piece": OLED,
        "value": "SSD1306 OLED",
        "pins": {"1": "P3V3", "3": "GND", "5": "SCL", "6": "SDA"},
        "group": "output",
        "why": "The OLED module plugs in here and shows the reading (I2C on GP4/GP5).",
    }


EXTRAS = (
    r"\b(beep\w*|buzz\w*|alarm|button|switch|fan|relay|motor|speaker|siren|vibrat\w*)\b"
)


def _only(a: str, own: str) -> bool:
    """True when every BEYOND word in the ask is one this recipe builds itself, and it asks for no extra output
    or input the recipe would quietly drop (QA 10-17: "a thermometer with a beeper" lost the beeper)."""
    return all(
        re.fullmatch(own, w.group(0)) for w in BEYOND.finditer(a)
    ) and not re.search(EXTRAS, a)


def temp_display(ask: str) -> dict[str, Any] | None:
    """Thermometer: a 10k NTC divider on ADC0, optionally an SSD1306 OLED on I2C0, read by a Pico."""
    a = ask.lower()
    if not re.search(
        r"\b(temperature|thermo\w*|temp|humid\w*|weather)\b|\bhow (hot|cold|warm)\b", a
    ):
        return None
    if not _only(a, r"temperature|thermo\w*|humidity|weather|display|oled|lcd|screen"):
        return None
    shows = bool(re.search(DISPLAY_RX, a))
    humid = bool(re.search(r"\b(humid\w*|weather)\b", a))
    sense = {"4": "DHT_DATA"} if humid else {"31": "TEMP_ADC"}
    parts = [
        _pico({**sense, "36": "P3V3", **({"6": "SDA", "7": "SCL"} if shows else {})})
    ]
    if humid:
        parts += [
            {
                "ref": "U2",
                "piece": DHT,
                "value": "DHT11",
                "pins": {"1": "P3V3", "2": "DHT_DATA", "4": "GND"},
                "group": "sensor",
                "why": "DHT11: temperature and humidity on one data wire (GP2).",
            },
            {
                "ref": "R1",
                "piece": R,
                "value": "10k",
                "pins": {"1": "P3V3", "2": "DHT_DATA"},
                "group": "sensor",
                "why": "Pull-up the DHT11 datasheet asks for on its data line.",
            },
        ]
    else:
        parts += [
            {
                "ref": "R1",
                "piece": R,
                "value": "10k",
                "pins": {"1": "P3V3", "2": "TEMP_ADC"},
                "group": "sensor",
                "why": "Divider top: with the 10k NTC it puts about half of 3.3 V on ADC0 at 25 C.",
            },
            {
                "ref": "TH1",
                "piece": NTC,
                "value": "10k",
                "pins": {"1": "TEMP_ADC", "2": "GND"},
                "group": "sensor",
                "why": "NTC thermistor: its resistance falls as it warms, so the ADC voltage tracks the temperature.",
            },
        ]
    if shows:
        parts.append(_oled())
    else:
        parts += [
            {
                "ref": "R2",
                "piece": R,
                "value": "100",
                "pins": {"1": "P3V3", "2": "PWR_A"},
                "group": "output",
                "why": "Sets the power LED current.",
            },
            {
                "ref": "D1",
                "piece": LED,
                "value": "green",
                "pins": {"2": "PWR_A", "1": "GND"},
                "group": "output",
                "why": "Shows the board is powered.",
            },
        ]
    out = "J1" if shows else "D1"
    return {
        "name": "weather" if humid else "thermometer",
        "title": "WEATHER" if humid else "THERMOMETER",
        "voltage": 5.0,
        "parts": parts,
        "scenarios": [{"name": "room"}],
        "benchmarks": [
            {
                "check": f"room.{out}.avg_ma",
                "min": 2,
                "max": 40,
                "text": "OLED module powered" if shows else "Power LED on",
            },
            {
                "check": "room.supply_ma_avg",
                "max": 100,
                "text": "Whole board under 100 mA",
            },
        ],
        "say": (
            "A Pico reads a DHT11 temperature and humidity sensor on GP2"
            if humid
            else "A Pico reads a 10k NTC thermistor on ADC0"
        )
        + (
            " and shows the reading on an SSD1306 OLED over I2C."
            if shows
            else " and prints the reading over USB."
        ),
    }


def soil_display(ask: str) -> dict[str, Any] | None:
    """Soil moisture: a two-prong resistive probe on a header under a 10k pull-up into ADC0 (wet soil pulls the
    reading down), optionally an SSD1306 OLED on I2C0 and a USB-C power-only input on VBUS (the
    Pico's own Schottky then feeds VSYS).
    Field run live-1792267825704: the picker put the OLED's VIN and GND on GPIO pins and left the probe unread."""
    a = ask.lower()
    if not re.search(r"\b(soil|moisture|plants?)\b", a):
        return None
    if re.search(r"\bwater(s|ing)?\b|\bpump\w*\b", a):
        return None  # watering needs a pump driver this recipe does not build
    if not _only(a, r"soil|moisture|display|oled|lcd|screen"):
        return None
    shows = bool(re.search(DISPLAY_RX, a))
    usbc = bool(re.search(r"\busb[- ]?c\b", a))
    parts = [
        _pico(
            {
                "31": "SOIL_ADC",
                "36": "P3V3",
                **({"6": "SDA", "7": "SCL"} if shows else {}),
                **({"40": "VCC"} if usbc else {}),
            }
        ),
        {
            "ref": "R1",
            "piece": R,
            "value": "10k",
            "pins": {"1": "P3V3", "2": "SOIL_ADC"},
            "group": "sensor",
            "why": "Pull-up for the probe: dry soil (high resistance) reads near 3.3 V, wet soil pulls it down.",
        },
        {
            "ref": "C1",
            "piece": C_DISC,
            "value": "100n",
            "pins": {"1": "SOIL_ADC", "2": "GND"},
            "group": "sensor",
            "why": "Smooths the probe reading for the ADC.",
        },
        {
            "ref": "J2",
            "piece": HDR2,
            "value": "soil probe",
            "pins": {"1": "SOIL_ADC", "2": "GND"},
            "group": "sensor",
            "why": "The two-prong soil probe plugs in here; the soil between the prongs is the bottom of the divider.",
        },
    ]
    if usbc:
        parts += [
            {
                "ref": "J3",
                "piece": USBC,
                "pins": {
                    "A9": "VCC",
                    "B9": "VCC",
                    "A12": "GND",
                    "B12": "GND",
                    "SH": "GND",
                    "A5": "CC1",
                    "B5": "CC2",
                },
                "group": "power",
                "why": "5 V in over USB-C (power only).",
            },
            {
                "ref": "R2",
                "piece": R,
                "value": "5.1k",
                "pins": {"1": "CC1", "2": "GND"},
                "group": "power",
                "why": "CC1 pull-down: tells the charger to send 5 V.",
            },
            {
                "ref": "R3",
                "piece": R,
                "value": "5.1k",
                "pins": {"1": "CC2", "2": "GND"},
                "group": "power",
                "why": "CC2 pull-down, for either plug orientation.",
            },
        ]
    if shows:
        parts.append(_oled())
    else:
        parts += [
            {
                "ref": "R4",
                "piece": R,
                "value": "100",
                "pins": {"1": "P3V3", "2": "PWR_A"},
                "group": "output",
                "why": "Sets the power LED current.",
            },
            {
                "ref": "D2",
                "piece": LED,
                "value": "green",
                "pins": {"2": "PWR_A", "1": "GND"},
                "group": "output",
                "why": "Shows the board is powered.",
            },
        ]
    out = "J1" if shows else "D2"
    return {
        "name": "soil_moisture",
        "title": "SOIL MOISTURE",
        "voltage": 5.0,
        "parts": parts,
        "scenarios": [{"name": "room"}],
        "benchmarks": [
            {
                "check": f"room.{out}.avg_ma",
                "min": 2,
                "max": 40,
                "text": "OLED module powered" if shows else "Power LED on",
            },
            {
                "check": "room.supply_ma_avg",
                "max": 100,
                "text": "Whole board under 100 mA",
            },
        ],
        "say": "A Pico reads a resistive soil probe on ADC0"
        + (
            " and shows the moisture on an SSD1306 OLED over I2C"
            if shows
            else " and prints the moisture over USB"
        )
        + (", powered from a USB-C input." if usbc else "."),
    }


def heart_rate(ask: str) -> dict[str, Any] | None:
    """Fingertip pulse sensor: an LED shines through the finger onto a light sensor read on ADC0; optional OLED."""
    a = ask.lower()
    if not re.search(r"\bheart ?(rate|beat)s?\b|\bheart monitor|\b(pulse|bpm)\b", a):
        return None
    if not _only(a, r"heart|pulse|display|oled|lcd|screen"):
        return None
    shows = bool(re.search(DISPLAY_RX, a))
    parts = [
        _pico(
            {
                "31": "PULSE_ADC",
                "36": "P3V3",
                **({"6": "SDA", "7": "SCL"} if shows else {}),
            }
        ),
        {
            "ref": "R1",
            "piece": R,
            "value": "150",
            "pins": {"1": "P3V3", "2": "LED_A"},
            "group": "sensor",
            "why": "Sets the LED that shines through the fingertip to about 8 mA.",
        },
        {
            "ref": "D1",
            "piece": LED,
            "value": "red",
            "pins": {"2": "LED_A", "1": "GND"},
            "group": "sensor",
            "why": "Shines through the fingertip; each heartbeat changes how much light gets through.",
        },
        {
            "ref": "LDR1",
            "piece": LDR,
            "pins": {"1": "PULSE_ADC", "2": "GND"},
            "group": "sensor",
            "why": "Sits under the fingertip: its resistance follows the light coming through.",
        },
        {
            "ref": "R2",
            "piece": R,
            "value": "10k",
            "pins": {"1": "P3V3", "2": "PULSE_ADC"},
            "group": "sensor",
            "why": "Divider top, so the light changes show up as a voltage on ADC0.",
        },
    ]
    if shows:
        parts.append(_oled("J1"))
    return {
        "name": "pulse_sensor",
        "title": "PULSE",
        "voltage": 5.0,
        "parts": parts,
        "scenarios": [{"name": "finger", "ldr": "light"}],
        "benchmarks": [
            {"check": "finger.D1.avg_ma", "min": 2, "text": "Sensor LED on"},
            {"check": "finger.D1.peak_ma", "max": 25, "text": "Sensor LED under 25 mA"},
        ],
        "say": "Rest a fingertip over the red LED and the light sensor: the Pico reads each heartbeat as a small change "
        "in light on ADC0"
        + (
            " and shows the BPM on an SSD1306 OLED."
            if shows
            else " and prints it over USB."
        ),
    }


def strip_knob(ask: str) -> dict[str, Any] | None:
    """Addressable RGB strip (WS2812) on a 3-pin header, data from GP15 through 330 ohm, a knob on ADC0."""
    a = ask.lower()
    if not re.search(r"\b(strip|neopixels?|ws2812\w*|addressable)\b", a):
        return None
    if not _only(a, r"bluetooth|wi-?fi|display|screen"):
        return None
    wireless = bool(re.search(r"\b(bluetooth|wi-?fi|wireless|phone|app)\b", a))
    knob = bool(re.search(r"\b(knob|dial|pot\w*|turn)\b", a))
    parts = [
        dict(
            _pico(
                {
                    "20": "STRIP_GPIO",
                    "36": "P3V3",
                    "40": "VBUS",
                    **({"31": "KNOB_ADC"} if knob else {}),
                }
            ),
            **(
                {
                    "piece": PICO_W,
                    "why": "Pico W: USB power, the logic, and Wi-Fi / Bluetooth for the phone.",
                }
                if wireless
                else {}
            ),
        ),
        {
            "ref": "R1",
            "piece": R,
            "value": "330",
            "pins": {"1": "STRIP_GPIO", "2": "STRIP_DIN"},
            "group": "output",
            "why": "Series resistor on the strip's data line (the usual WS2812 guard against ringing).",
        },
        {
            "ref": "J2",
            "piece": SERVO_HDR,
            "value": "WS2812 strip",
            "pins": {"1": "VBUS", "2": "STRIP_DIN", "3": "GND"},
            "group": "output",
            "why": "Strip header: 5 V, DATA, GND. On USB power keep it to about 15 LEDs.",
        },
        {
            "ref": "C1",
            "piece": CP_BULK,
            "value": "470u",
            "pins": {"1": "VBUS", "2": "GND"},
            "group": "power",
            "why": "Bulk capacitor across the strip supply, as the WS2812 guides recommend.",
        },
        {
            "ref": "R2",
            "piece": R,
            "value": "100",
            "pins": {"1": "P3V3", "2": "PWR_A"},
            "group": "output",
            "why": "Sets the power LED current.",
        },
        {
            "ref": "D1",
            "piece": LED,
            "value": "green",
            "pins": {"2": "PWR_A", "1": "GND"},
            "group": "output",
            "why": "Shows the board is powered.",
        },
    ]
    if knob:
        parts.append(
            {
                "ref": "RV1",
                "piece": TRIM,
                "value": "10k",
                "pins": {"1": "P3V3", "2": "KNOB_ADC", "3": "GND"},
                "group": "input",
                "why": "The knob: its wiper voltage on ADC0 sets the colour or brightness.",
            }
        )
    return {
        "name": "strip_knob",
        "title": "STRIP" + (" KNOB" if knob else ""),
        "voltage": 5.0,
        "parts": parts,
        "scenarios": [{"name": "on"}],
        "benchmarks": [
            {"check": "on.D1.avg_ma", "min": 2, "text": "Power LED on"},
            {"check": "on.D1.peak_ma", "max": 25, "text": "Power LED under 25 mA"},
        ],
        "say": ("A Pico W" if wireless else "A Pico")
        + " drives a WS2812 strip from GP15 through 330 ohm"
        + (", and a 10k knob on ADC0 sets it." if knob else "."),
    }


def plain_led(ask: str) -> dict[str, Any] | None:
    """Just an LED: 5 V header, 220 ohm, LED (QA 10-17: "a led" and "LED!!! RED!!!" went to the model)."""
    a = re.sub(r"[^a-z ]+", " ", ask.lower())
    a = re.sub(r"\s+", " ", a).strip()
    m = re.fullmatch(
        r"(?:(?:i want|make|build|give me)\s+)?(?:a|an|one|just a|single)?\s*(?:("
        + COLOUR_RX
        + r")\s+)?"
        r"(?:led|light)(?:\s+(" + COLOUR_RX + r"))*(?:\s+(?:board|light|please))*",
        a,
    )
    if not m:
        return None
    colour = m.group(1) or m.group(2) or "red"
    if colour not in ("red", "green", "blue", "yellow", "white", "orange"):
        return None
    return {
        "name": "plain_led",
        "title": f"{colour.upper()} LED",
        "voltage": 5.0,
        "parts": [
            {
                "ref": "J1",
                "piece": HDR2,
                "pins": {"1": "VCC", "2": "GND"},
                "group": "power",
                "why": "5 V in.",
            },
            {
                "ref": "R1",
                "piece": R,
                "value": "220",
                "pins": {"1": "VCC", "2": "LED_A"},
                "group": "output",
                "why": "Sets the LED current to about 10 to 14 mA.",
            },
            {
                "ref": "D1",
                "piece": LED,
                "value": colour,
                "pins": {"2": "LED_A", "1": "GND"},
                "group": "output",
                "why": "Lights whenever the board has power.",
            },
        ],
        "benchmarks": [
            {"check": "D1.avg_ma", "min": 2, "text": "LED on"},
            {"check": "D1.peak_ma", "max": 25, "text": "LED under 25 mA"},
        ],
        "say": f"A {colour} LED with a 220 ohm resistor, lit whenever 5 V is on the header.",
    }


L293D = "kicad:Driver_Motor:L293D@DIP-16_W7.62mm"
MOTOR_TB = "kicad:Connector_Generic:Conn_01x02@TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"
# the motor command table: key -> (left motor, right motor), each "f" forward, "b" back or "" stopped
CAR_MOVES = {
    "f": ("f", "f", "drives forward"),
    "b": ("b", "b", "backs up"),
    "l": ("", "f", "turns left"),
    "r": ("f", "", "turns right"),
    "s": ("", "", "stops"),
}


def motor_car(ask: str) -> dict[str, Any] | None:
    """Two-motor car: Pico W, an L293D (TI) on GP10-GP13, two screw-terminal motor headers. Motors run from USB
    VBUS, so small 3-6 V hobby gear motors only. Typed commands f/b/l/r/s (or over Wi-Fi in the user's own code)."""
    a = ask.lower()
    car = re.search(
        r"\b(car|rover|robot ?car|buggy|tank|two (dc )?motors|2 (dc )?motors|motor driver)\b",
        a,
    )
    if not car or not re.search(r"\b(car|rover|buggy|motors?|drive\w*)\b", a):
        return None
    if not all(
        re.fullmatch(r"motors?|bluetooth|wi-?fi", w.group(0))
        for w in BEYOND.finditer(a)
    ):
        return None
    if re.search(EXTRAS, re.sub(r"\bmotors?\b", "", a)):
        return None
    # GP10..GP13 set direction; GP8/GP9 drive the L293D enables with PWM for speed (QA round 4)
    gp = {
        "14": "AIN1",
        "15": "AIN2",
        "16": "BIN1",
        "17": "BIN2",
        "11": "ENA",
        "12": "ENB",
    }
    parts = [
        dict(
            _pico({**gp, "36": "P3V3", "40": "VBUS", "8": "GND", "13": "GND"}),
            piece=PICO_W,
            why="Pico W: USB power, the logic, and Wi-Fi for steering from a phone later.",
        ),
        {
            "ref": "U2",
            "piece": L293D,
            "value": "L293D",
            "pins": {
                "16": "VBUS",
                "8": "VBUS",
                "4": "GND",
                "5": "GND",
                "12": "GND",
                "13": "GND",
                "1": "ENA",
                "9": "ENB",
                "2": "AIN1",
                "7": "AIN2",
                "10": "BIN1",
                "15": "BIN2",
                "3": "M1_A",
                "6": "M1_B",
                "11": "M2_A",
                "14": "M2_B",
            },
            "group": "output",
            "why": "TI L293D quad half-H driver with built-in flyback diodes: two motors, both directions.",
        },
        {
            "ref": "M1",
            "piece": MOTOR_TB,
            "value": "left motor",
            "pins": {"1": "M1_A", "2": "M1_B"},
            "load_ohms": 33,
            "group": "output",
            "why": "Left motor screw terminal (a 3-6 V gear motor, about 33 ohm).",
        },
        {
            "ref": "M2",
            "piece": MOTOR_TB,
            "value": "right motor",
            "pins": {"1": "M2_A", "2": "M2_B"},
            "load_ohms": 33,
            "group": "output",
            "why": "Right motor screw terminal.",
        },
        {
            "ref": "C1",
            "piece": CP_BULK,
            "value": "470u",
            "pins": {"1": "VBUS", "2": "GND"},
            "group": "power",
            "why": "Bulk capacitor so motor start-up surges do not brown out the Pico.",
        },
        {
            "ref": "R1",
            "piece": R,
            "value": "100",
            "pins": {"1": "P3V3", "2": "PWR_A"},
            "group": "output",
            "why": "Sets the power LED current.",
        },
        {
            "ref": "D1",
            "piece": LED,
            "value": "green",
            "pins": {"2": "PWR_A", "1": "GND"},
            "group": "output",
            "why": "Shows the board is powered.",
        },
    ]
    nets = {"f": ("AIN1", "BIN1"), "b": ("AIN2", "BIN2")}
    cmds = [
        {
            "input": k,
            "high": ([nets[lm][0], "ENA"] if lm else [])
            + ([nets[rm][1], "ENB"] if rm else []),
            "does": does,
        }
        for k, (lm, rm, does) in CAR_MOVES.items()
    ]
    bench = [{"check": "idle.D1.avg_ma", "min": 2, "text": "Power LED on"}]
    for m in ("M1", "M2"):
        bench += [
            {
                "check": f"idle.{m}.avg_ma",
                "min": -1,
                "max": 1,
                "text": f"{m} still at power-up",
            },
            {
                "check": f"cmd_f.{m}.avg_ma",
                "min": 25,
                "max": 200,
                "text": f"{m} turns forward",
            },
            {
                "check": f"cmd_b.{m}.avg_ma",
                "min": 25,
                "max": 200,
                "text": f"{m} driven in reverse (current size; the firmware test checks the direction pins)",
            },
            {"check": f"cmd_s.{m}.avg_ma", "min": -1, "max": 1, "text": f"{m} stops"},
        ]
    bench += [
        {
            "check": "cmd_l.M1.avg_ma",
            "min": -1,
            "max": 1,
            "text": "Left turn: left motor stopped",
        },
        {
            "check": "cmd_l.M2.avg_ma",
            "min": 25,
            "text": "Left turn: right motor forward",
        },
        {
            "check": "cmd_f.supply_ma_peak",
            "max": 500,
            "text": "Both motors under the 500 mA USB limit",
        },
    ]
    return {
        "name": "motor_car",
        "title": "MOTOR CAR",
        "voltage": 5.0,
        "parts": parts,
        "firmware": {"commands": cmds, "drives": "motors", "pwm": ["ENA", "ENB"]},
        "benchmarks": bench,
        "say": "A Pico W drives two motors through an L293D: type f, b, l, r or s, and 1 to 9 for speed (PWM on the "
        "driver's enable pins). The motors run from USB 5 V, so "
        "use small 3-6 V gear motors; bigger motors need their own battery on VCC2.",
    }


FLYBACK = "kicad:Diode:1N4001@D_DO-41_SOD81_P10.16mm_Horizontal"
SWITCH_NPN = "kicad:Transistor_BJT:PN2222A@TO-92_Inline"


def _threshold_c(a: str) -> float:
    """The temperature the ask names ("past 80 deg", "above 80 C", "176 F"), in C; 80 when none is named."""
    for m in re.finditer(
        r"(\d{2,3}(?:\.\d)?)\s*(°|º|deg\w*)?\s*(c\b|celsius|f\b|fahrenheit)?", a
    ):
        if (
            m.group(2)
            or m.group(3)
            or re.search(
                r"(past|above|over|exceeds?|hits?|reach\w*|than|beyond)\s*$",
                a[: m.start()],
            )
        ):
            v = float(m.group(1))
            return round((v - 32) / 1.8, 1) if (m.group(3) or "").startswith("f") else v
    return 80.0


def usb_fan(ask: str) -> dict[str, Any] | None:
    """A fan the computer switches over USB when it gets hot (Pranav's prompt family, 10-17): Pico on USB serial,
    PN2222A low-side switch with a base resistor and pull-down, 1N4001 flyback diode across the fan, 5 V fan from
    USB VBUS, bulk capacitor. A host script (fan_host.py) reads the CPU temperature with hysteresis and a
    failsafe, and the firmware turns the fan on by itself when the computer goes quiet."""
    a = ask.lower()
    if not re.search(r"\b(fans?|cooling|cooler|blower)\b", a):
        return None
    if not re.search(
        r"\b(temp\w*|hot|hotter|heat|deg\w*|celsius|fahrenheit|°|thermal|cpu)\b|\d+\s*°?\s*[cf]\b",
        a,
    ):
        return None
    if not re.search(
        r"\b(computer|pc|laptop|usb|host|script|serial|desktop|mac|python)\b", a
    ):
        return None
    if re.search(
        r"\b(display|oled|lcd|screen|bluetooth|wi-?fi|relay|pump|motors)\b", a
    ):
        return None
    on_at = _threshold_c(a)
    off_at = round(on_at - 5, 1)
    parts = [
        _pico({"20": "FAN_ON", "40": "VBUS", "8": "GND"}),
        {
            "ref": "R1",
            "piece": R,
            "value": "1k",
            "pins": {"1": "FAN_ON", "2": "Q1_B"},
            "group": "output",
            "why": "Base resistor: about 2.6 mA from the 3.3 V pin, plenty to saturate the switch at fan current.",
        },
        {
            "ref": "R2",
            "piece": R,
            "value": "10k",
            "pins": {"1": "Q1_B", "2": "GND"},
            "group": "output",
            "why": "Pull-down: the fan stays off while the Pico boots and its pin floats.",
        },
        {
            "ref": "Q1",
            "piece": SWITCH_NPN,
            "value": "PN2222A",
            "pins": {"1": "GND", "2": "Q1_B", "3": "FAN_N"},
            "group": "output",
            "why": "Low-side switch for the fan (600 mA part, the fan draws about 100 mA).",
        },
        {
            "ref": "D1",
            "piece": FLYBACK,
            "value": "1N4001",
            "pins": {"1": "VBUS", "2": "FAN_N"},
            "group": "output",
            "why": "Flyback diode across the fan motor: catches the voltage kick when the switch turns off.",
        },
        {
            "ref": "M1",
            "piece": HDR2,
            "value": "5V fan",
            "pins": {"1": "VBUS", "2": "FAN_N"},
            "load_ohms": 50,
            "group": "output",
            "why": "Fan header: a small 5 V DC fan (about 100 mA), red lead on pin 1 (+5 V).",
        },
        {
            "ref": "C1",
            "piece": CP_BULK,
            "value": "470u",
            "pins": {"1": "VBUS", "2": "GND"},
            "group": "power",
            "why": "Bulk capacitor so the fan's start-up surge does not brown out the Pico.",
        },
    ]
    cmds = [
        {"input": "1", "high": ["FAN_ON"], "does": "fan on"},
        {"input": "0", "high": [], "does": "fan off"},
    ]
    bench = [
        {"check": "idle.M1.avg_ma", "min": -1, "max": 1, "text": "Fan off at power-up"},
        {
            "check": "cmd_1.M1.avg_ma",
            "min": 60,
            "max": 200,
            "text": "Fan spins when the computer says hot",
        },
        {
            "check": "cmd_0.M1.avg_ma",
            "min": -1,
            "max": 1,
            "text": "Fan stops when the computer says cool",
        },
        {
            "check": "failsafe.M1.avg_ma",
            "min": 60,
            "text": "Fan spins when the computer goes quiet (failsafe)",
        },
        {
            "check": "cmd_1.supply_ma_peak",
            "max": 500,
            "text": "Under the 500 mA USB limit",
        },
    ]
    return {
        "name": "usb_fan",
        "title": "USB FAN",
        "voltage": 5.0,
        "parts": parts,
        "firmware": {
            "commands": cmds,
            "drives": "fan",
            "failsafe": {"after_s": 15, "high": ["FAN_ON"]},
            "host": {"file": "fan_host.py", "set": {"ON_AT": on_at, "OFF_AT": off_at}},
        },
        "benchmarks": bench,
        "say": f"A Pico on USB switches a 5 V fan through a transistor with a flyback diode. fan_host.py on your "
        f"computer reads the CPU temperature and turns the fan on at {on_at:g} C and off below {off_at:g} C. If "
        "the computer goes quiet for 15 seconds, the board turns the fan on by itself.",
    }


PICO_RECIPES = (temp_display, soil_display, heart_rate, strip_knob, motor_car, usb_fan)

RECIPES = (
    voice_arm,
    usb_fan,
    motor_car,
    temp_display,
    soil_display,
    heart_rate,
    strip_knob,
    plain_led,
    pitch_rainbow,
    voice_led,
    typed_rgb,
    dark_beeper,
    dark_light,
    button_led,
    blinker,
)


# things no small recipe builds: an ask that names one goes to the picker instead of getting a recipe board that
# quietly drops it (QA 10-17: "a weather station with ... sensors, a display, a lipo charger ... and a button"
# came back as the press-to-light board)
BEYOND = re.compile(
    r"\b(temperature|humidity|pressure|thermo\w*|display|oled|lcd|screen|charg\w*|lipo|li-?ion|bluetooth|wi-?fi|"
    r"esp32|motors?|pump|relay|gps|acceler\w*|gyro\w*|heart|pulse|soil|moisture|ultrasonic|distance|speaker|"
    r"sd card|touch|weather|clock|timer display|keypad)\b"
)


def _beyond_recipes(ask: str) -> bool:
    a = ask.lower()
    return bool(BEYOND.search(a)) or len(re.findall(r",|\band\b", a)) >= 4


FILLER = re.compile(
    r"\b(?:um+|uh+|erm|hmm+|like|you know|i guess|i mean|yeah|okay|ok|so|basically|actually|kind of|sort of|"
    r"hey etch|thanks|thank you)\b\s*,?",
    re.I,
)


def _defill(ask: str) -> str:
    """Spoken fillers out (QA 10-17: "um so like I want, uh, a board that, you know, lights up red when it's
    dark" counted 5 commas as a long multi-part ask and skipped the night-light recipe)."""
    a = FILLER.sub(" ", ask)
    a = re.sub(r"\s*,(\s*,)+", ",", a)
    return re.sub(r"\s+", " ", a).strip(" ,")


def match(
    ask: str, catalog: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], str, list[dict[str, Any]]] | None:
    """(design, recipe name, the catalog pieces it uses) for the first recipe that fits the ask."""
    ask = _defill(ask) or ask
    beyond = _beyond_recipes(ask)
    from . import esp32

    chip = esp32.chip_for(ask)
    for fn in (esp32.esp32_sensor, *RECIPES):
        if (
            beyond
            and fn is not voice_arm
            and fn not in PICO_RECIPES
            and fn is not esp32.esp32_sensor
        ):
            continue
        d = fn(ask)
        if d and chip and fn in (strip_knob, motor_car):
            # Wi-Fi / Bluetooth / "esp32" on a Pico W recipe: the same board on an ESP32 module (esp32.py has
            # the preference); the Pico W stays when the ESP32 pieces are missing
            d = esp32.swap(d, catalog, chip) or d
        if d and all(p["piece"] in catalog for p in d["parts"]):
            used = list({p["piece"]: catalog[p["piece"]] for p in d["parts"]}.values())
            return d, fn.__name__, used
    return None


def _is_led(p: dict[str, Any]) -> bool:
    return str(p.get("piece", "")).startswith("kicad:Device:LED@")


def recolour(request: str, base: dict[str, Any]) -> dict[str, Any] | None:
    """'make the LED blue' on a board with one plain LED: the same design with that LED's colour changed."""
    a = request.lower().strip(" .!")
    m = re.fullmatch(
        rf"(?:please\s+)?(?:make|turn|change|set)\s+(?:the\s+|my\s+|it\s+)?(?:(?:status|listening|indicator)\s+)?(?:(?:led|light|lamp)s?\s+)?(?:to\s+|into\s+)?({COLOUR_RX})",
        a,
    )
    leds = [
        p for p in base.get("parts", []) if _is_led(p) and p.get("group") != "status"
    ]
    if (
        not m
    ):  # "use a blue led instead", "swap the led for a blue one", "a blue led please"
        m = re.fullmatch(
            rf"(?:please\s+|now\s+|can you\s+)*(?:(?:use|swap(?:\s+(?:the\s+|it\s+)?(?:led|light)\s+(?:for|to))|switch\s+to|give\s+it|put\s+in)\s+)?"
            rf"(?:a\s+|an\s+)?({COLOUR_RX})\s+(?:led|light|one)(?:\s+instead)?(?:\s+please)?",
            a,
        )
    if not m or not leds:
        return None
    if len(leds) > 1 and len({str(p.get("value")) for p in leds}) > 1:
        return None  # LEDs of different colours: which one is meant is a design call
    return _colour_leds(base, [p["ref"] for p in leds], m.group(1))


def _colour_leds(base: dict[str, Any], refs: list[str], colour: str) -> dict[str, Any]:
    """The design with the LEDs in refs set to colour (sweep 10-17: "use a blue led instead" on a board with two
    identical LEDs means both; "add a second led and make it blue" means the new one)."""
    d = json.loads(json.dumps(base))
    for p in d["parts"]:
        if p["ref"] in refs:
            p["value"] = colour
    d["say"] = f"Same board, {', '.join(refs)} {'is' if len(refs) == 1 else 'are'} now {colour}."
    for ref in refs:
        _blue_drive(d, next(p for p in d["parts"] if p["ref"] == ref), colour)
    return d


def _blue_drive(d: dict[str, Any], led: dict[str, Any], colour: str) -> None:
    leds = [led]
    if colour in ("blue", "white"):
        # QA round 6: a blue or white LED on a 3.3 V Pico pin through 100 ohm got under 1 mA (about 3 V of its 3.3 V
        # is the LED's own drop), so its series resistor drops to 47 ohm as on the motor car's headlights
        pico = {n for q in d["parts"] if q["piece"] == PICO for n in q["pins"].values()}
        anode = leds[0]["pins"].get("2")
        for q in d["parts"]:
            nets = list(q["pins"].values())
            if (
                q["piece"] == R
                and anode in nets
                and any(n in pico and n not in SUPPLY + ("GND",) for n in nets)
            ):
                try:
                    if sim_ohms(q.get("value", "")) > 47:
                        q["value"] = "47"
                        d["say"] += (
                            f" {q['ref']} is now 47 ohm so it still lights from the 3.3 V pin."
                        )
                except ValueError:
                    pass


def sim_ohms(v: str) -> float:
    """'4.7k' / '100' / '1M' as ohms."""
    t = str(v).strip().lower().replace("ohm", "").replace("ω", "").strip()
    mult = {"k": 1e3, "m": 1e6, "r": 1.0}.get(t[-1:])
    return float(t[:-1]) * mult if mult else float(t)


# ---------------------------------------------------------------- edits on a built board, no model call
# Each edit takes the current design and returns the whole new design (same shape as a recipe), so the new
# board still goes through place, route, ERC, DRC and ngspice. Anything not covered returns None and the
# change goes to the picker as before.

USBC = "kicad:Connector:USB_C_Receptacle_PowerOnly_6P@USB_C_Receptacle_GCT_USB4125-xx-x-0190_6P_TopMnt_Horizontal"
BARREL = "kicad:Connector:Barrel_Jack@BarrelJack_Horizontal"
LED3 = "kicad:Device:LED@LED_D3.0mm"
JST2 = "kicad:Connector_Generic:Conn_01x02@JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical"
SUPPLY = ("VCC", "VBUS", "P3V3")
PLEASE = r"(?:please\s+|can you\s+|could you\s+|now\s+|and\s+|also\s+)*"


def _copy(base: dict[str, Any]) -> dict[str, Any]:
    d = json.loads(json.dumps(base))
    d.pop("say", None)
    return d


def _next_ref(d: dict[str, Any], prefix: str) -> str:
    used = {p["ref"] for p in d["parts"]}
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def _power_in(d: dict[str, Any]) -> dict[str, Any] | None:
    """The 2-pin header (or swapped-in jack) that brings 5 V in on VCC/GND."""
    for p in d["parts"]:
        nets = set(p.get("pins", {}).values())
        if p.get("group") == "power" and p["piece"] != PICO and {"VCC", "GND"} <= nets:
            return p
    return None


def _main_led(d: dict[str, Any]) -> dict[str, Any] | None:
    leds = [p for p in d["parts"] if _is_led(p) and p.get("group") != "status"]
    return leds[0] if len(leds) == 1 else None


def _led_path(d: dict[str, Any], led: dict[str, Any]) -> tuple[str, str] | None:
    """(high side, low side) of the LED's branch: the net that feeds its series resistor, and its cathode net."""
    anode, cathode = led["pins"].get("2"), led["pins"].get("1")
    for p in d["parts"]:
        if p["piece"] == R and anode in p["pins"].values():
            other = [n for n in p["pins"].values() if n != anode]
            if other:
                return other[0], cathode
    return None


def add_buzzer(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    if not re.fullmatch(
        PLEASE
        + r"(?:add|put|include|give it)\s+(?:a|an|one)?\s*(?:buzzer|beeper|piezo|sound|beep)(?:\s+(?:too|as well|that beeps with (?:the|it)|when it lights up|with the led|that goes with the led))?",
        a,
    ):
        return None
    if any(p["piece"] == PICO for p in base["parts"]) or any(
        p.get("group") == "buzzer" for p in base["parts"]
    ):
        return None  # a GPIO cannot drive a buzzer directly: the picker adds a transistor stage
    led = _main_led(base)
    path = led and _led_path(base, led)
    if not path:
        return None
    hi, lo = path
    d = _copy(base)
    ref = _next_ref(d, "BZ")
    for q in d[
        "parts"
    ]:  # QA 10-17: a 47k base pull-up starved the LED once the buzzer shared the transistor
        if q["piece"] == NPN:
            base_net = q["pins"].get("2")
            for r in d["parts"]:
                if (
                    r["piece"] == R
                    and base_net in r["pins"].values()
                    and set(r["pins"].values()) & set(SUPPLY)
                    and str(r.get("value", "")).lower() in ("47k", "100k", "68k")
                ):
                    r["value"] = "10k"
                    r["why"] = (
                        r.get("why", "")
                        + " 10k so the transistor saturates with the buzzer added."
                    )
    d["parts"].append(
        {
            "ref": ref,
            "piece": BUZZ,
            "pins": {"1": hi, "2": lo},
            "group": "buzzer",
            "why": f"Beeps whenever {led['ref']} is lit (wired across the LED branch).",
        }
    )
    for b in list(base.get("benchmarks", [])):
        if b["check"].endswith(f".{led['ref']}.avg_ma"):
            nb = dict(
                b,
                check=b["check"].replace(f".{led['ref']}.", f".{ref}."),
                text=b["text"].replace("LED", "buzzer"),
            )
            if "min" in nb:
                nb["min"] = 5
            d["benchmarks"].append(nb)
    d["say"] = f"Added {ref}, a buzzer that beeps whenever {led['ref']} is on."
    return d


def add_status_led(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    if not re.fullmatch(
        PLEASE
        + r"(?:add|put|include)\s+(?:a|an|one)?\s*(?:power|status|on|pilot)\s*(?:indicator\s*)?(?:led|light|indicator)(?:\s+(?:too|as well))?",
        a,
    ):
        return None
    if any(p.get("group") == "status" for p in base["parts"]):
        return None
    d = _copy(base)
    nets = {n for p in d["parts"] for n in p["pins"].values()}
    sup = next((n for n in SUPPLY if n in nets), None)
    if not sup:
        pico = next((p for p in d["parts"] if p["piece"] == PICO), None)
        if not pico or pico["pins"].get("36") not in (None, "P3V3"):
            return None
        pico["pins"]["36"] = sup = "P3V3"
    dref, rref = _next_ref(d, "D"), _next_ref(d, "R")
    d["parts"] += [
        {
            "ref": rref,
            "piece": R,
            "value": "1k",
            "pins": {"1": sup, "2": f"{dref}_A"},
            "group": "status",
            "why": f"Limits {dref} to a few mA.",
        },
        {
            "ref": dref,
            "piece": LED,
            "value": "green",
            "pins": {"2": f"{dref}_A", "1": "GND"},
            "group": "status",
            "why": "Shows the board has power.",
        },
    ]
    for sc in d.get("scenarios", []) or [{"name": None}]:
        if sc.get("name"):
            d["benchmarks"].append(
                {
                    "check": f"{sc['name']}.{dref}.avg_ma",
                    "min": 1,
                    "text": f"power LED on ({sc['name']})",
                }
            )
            break
    d["say"] = f"Added a green power LED, {dref}, with {rref}."
    return d


REMOVABLE = {
    "buzzer": ("buzzer", "beeper", "piezo", "sound"),
    "status": ("power led", "status led", "power light", "status light", "indicator"),
}


def remove_added(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    m = re.fullmatch(
        PLEASE
        + r"(?:remove|drop|delete|take out|take off|get rid of|lose)\s+(?:the|that|this)?\s*(.+?)(?:\s+again)?",
        a,
    )
    if not m:
        return None
    what = m.group(1).strip()
    group = next(
        (g for g, words in REMOVABLE.items() if any(w in what for w in words)), None
    )
    if not group:
        return None
    gone = {p["ref"] for p in base["parts"] if p.get("group") == group}
    if not gone:
        return None
    d = _copy(base)
    d["parts"] = [p for p in d["parts"] if p["ref"] not in gone]
    d["benchmarks"] = [
        b
        for b in d.get("benchmarks", [])
        if not any(f".{r}." in b["check"] for r in gone)
    ]
    d["_dropped"] = sorted(gone)
    d["say"] = f"Removed {', '.join(sorted(gone))}."
    return d


def swap_power(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    m = re.fullmatch(
        PLEASE
        + r"(?:swap|switch|change|move|make)\s+(?:it\s+|the\s+power\s+(?:input\s+)?|power\s+)?(?:to|over to|for|into|use)?\s*(?:a\s+|an\s+)?(usb[- ]?c|barrel(?: jack)?|dc jack|header|pin header)(?:\s+(?:power|input|connector|port))?",
        a,
    )
    m = m or re.fullmatch(
        PLEASE
        + r"(?:use|power it (?:from|with|over|by))\s+(?:a\s+|an\s+)?(usb[- ]?c|barrel(?: jack)?|dc jack)(?:\s+(?:instead|power|connector))?",
        a,
    )
    if not m:
        return None
    kind = (
        "usbc"
        if m.group(1).startswith("usb")
        else "barrel"
        if "jack" in m.group(1) or "barrel" in m.group(1)
        else "header"
    )
    j = _power_in(base)
    if not j:
        return None
    want = {"usbc": USBC, "barrel": BARREL, "header": HDR2}[kind]
    if j["piece"] == want:
        return None
    d = _copy(base)
    d["parts"] = [
        p
        for p in d["parts"]
        if not (
            p.get("group") == "power"
            and p["ref"].startswith("R")
            and "CC" in "".join(p["pins"].values())
        )
    ]
    for p in d["parts"]:
        if p["ref"] != j["ref"]:
            continue
        p["piece"] = want
        if kind == "usbc":
            p["pins"] = {
                "A9": "VCC",
                "B9": "VCC",
                "A12": "GND",
                "B12": "GND",
                "SH": "GND",
                "A5": "CC1",
                "B5": "CC2",
            }
            p["why"] = "5 V in over USB-C (power only)."
        else:
            p["pins"] = {"1": "VCC", "2": "GND"}
            p["why"] = "5 V in from a barrel jack." if kind == "barrel" else "5 V in."
    if kind == "usbc":
        r1, r2 = _next_ref(d, "R"), None
        d["parts"].append(
            {
                "ref": r1,
                "piece": R,
                "value": "5.1k",
                "pins": {"1": "CC1", "2": "GND"},
                "group": "power",
                "why": "CC1 pull-down: tells the charger to send 5 V.",
            }
        )
        r2 = _next_ref(d, "R")
        d["parts"].append(
            {
                "ref": r2,
                "piece": R,
                "value": "5.1k",
                "pins": {"1": "CC2", "2": "GND"},
                "group": "power",
                "why": "CC2 pull-down, for either plug orientation.",
            }
        )
    d["say"] = {
        "usbc": f"{j['ref']} is now a USB-C power input, with the two 5.1k CC resistors.",
        "barrel": f"{j['ref']} is now a barrel jack.",
        "header": f"{j['ref']} is a pin header again.",
    }[kind]
    return d


def shrink(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    if not re.fullmatch(
        PLEASE
        + r"(?:make\s+(?:it|the board|this)\s+(?:smaller|tinier|more compact|compact)|shrink\s+(?:it|the board)|smaller(?:\s+board)?)(?:\s+please)?",
        a,
    ):
        return None
    d = _copy(base)
    changed = []
    for p in d["parts"]:
        if p["piece"] == LED:
            p["piece"] = LED3
            changed.append(f"{p['ref']} to a 3 mm LED")
        elif p["piece"] == HDR2:
            p["piece"] = JST2
            changed.append(f"{p['ref']} to a 2 mm JST-PH plug")
    if not changed:
        return None
    d["say"] = "Smaller parts: " + ", ".join(changed) + "."
    return d


MASKS = {
    "green": "#08502A",
    "red": "#8C1A1A",
    "blue": "#123C8C",
    "black": "#141414",
    "white": "#E8E8E8",
    "purple": "#4B2378",
    "yellow": "#C8A21E",
    "matte black": "#141414",
}
COIN = "kicad:Device:Battery_Cell@BatteryHolder_Keystone_1058_1x2032"
HDR3 = "kicad:Connector_Generic:Conn_01x03@PinHeader_1x03_P2.54mm_Vertical"
NUMS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "another": 1,
    "a second": 1,
    "one more": 1,
    "two": 2,
    "2": 2,
    "three": 3,
    "3": 3,
}


def board_colour(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    """Solder-mask colour: written into the board's stackup, so the renders and the KiCad files both carry it."""
    m = re.fullmatch(
        PLEASE
        + r"(?:make|turn|change|paint|set|i want)\s+(?:the\s+)?(?:board|pcb|solder ?mask|mask)"
        r"(?:\s+colou?r)?\s+(?:to\s+|into\s+|be\s+)?(?:a\s+)?(matte black|"
        + "|".join(MASKS)
        + r")"
        r"(?:\s+(?:board|pcb|solder ?mask|mask))?(?:\s+instead)?",
        a,
    )
    m = m or re.fullmatch(
        PLEASE
        + r"(?:a\s+|use\s+(?:a\s+)?)?(matte black|"
        + "|".join(MASKS)
        + r")\s+(?:board|pcb|solder ?mask)"
        r"(?:\s+instead)?",
        a,
    )
    m = m or re.fullmatch(
        PLEASE + r"(?:make|turn|paint)\s+it\s+(matte black|black)(?:\s+instead)?", a
    )  # no black LEDs
    if not m:
        return None
    colour = m.group(1)
    d = _copy(base)
    if d.get("mask") == MASKS[colour]:
        return None
    d["mask"] = MASKS[colour]
    d["say"] = f"The solder mask is now {colour}. Same circuit, same checks."
    return d


def add_led(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    """Another LED in parallel with the main one, each with its own copy of the series resistor."""
    m = re.fullmatch(
        PLEASE
        + r"(?:add|put|include|give it)\s+(a second|one more|another|an extra|a|an|one|two|three|2|3)\s+"
        r"(?:more\s+)?(?:("
        + COLOUR_RX
        + r")\s+)?(?:leds?|lights?)(?:\s+(?:too|as well|next to it|that lights with it))?(?:\s+please)?",
        a,
    )
    if not m:
        return None
    leds = [
        p
        for p in base["parts"]
        if _is_led(p) and p.get("group") != "status" and p["piece"] == LED
    ]
    led = next((p for p in leds if _led_path(base, p)), None)
    path = led and _led_path(base, led)
    if not path:
        return None
    hi, lo = path
    res = next(
        p
        for p in base["parts"]
        if p["piece"] == R and led["pins"].get("2") in p["pins"].values()
    )
    count = NUMS.get(m.group(1), 1)
    colour = m.group(2) if m.group(2) and m.group(2) != "off" else led.get("value")
    d = _copy(base)
    added = []
    for _ in range(count):
        dref = _next_ref(d, "D")
        rref = _next_ref(d, "R")
        d["parts"] += [
            {
                "ref": rref,
                "piece": R,
                "value": res.get("value"),
                "pins": {"1": hi, "2": f"{dref}_A"},
                "group": led.get("group", "output"),
                "why": f"Its own resistor, the same value as {res['ref']}, so {dref} gets the same current.",
            },
            {
                "ref": dref,
                "piece": led["piece"],
                "value": colour,
                "pins": {"2": f"{dref}_A", "1": lo},
                "group": led.get("group", "output"),
                "why": f"Lights together with {led['ref']}.",
            },
        ]
        for b in list(base.get("benchmarks", [])):
            if f".{led['ref']}." in b["check"] or b["check"].startswith(
                f"{led['ref']}."
            ):
                d["benchmarks"].append(
                    dict(
                        b,
                        check=re.sub(
                            rf"(^|\.){led['ref']}\.", rf"\g<1>{dref}.", b["check"]
                        ),
                        text=b["text"].replace(led["ref"], dref),
                    )
                )
        added.append(f"{dref} with {rref}")
    d["say"] = (
        "Added "
        + ", ".join(added)
        + f", wired in parallel with {led['ref']} so they light together."
    )
    return d


def remove_switch(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    """'remove the button' (sweep 10-17): a two-pin push button in series between two nets is replaced by a wire
    (its two nets merge), so what it switched is simply always on; its pressed/released scenarios and the
    "released" checks go with it. A button read by a microcontroller is a design call and goes to the picker."""
    if not re.fullmatch(
        PLEASE
        + r"(?:remove|drop|delete|take out|take off|get rid of|lose)\s+(?:the\s+|that\s+)?(?:push\s*)?(?:button|switch)",
        a,
    ):
        return None
    sws = [p for p in base["parts"] if p["piece"] == SW]
    if len(sws) != 1 or any(p["piece"] in (PICO, PICO_W) for p in base["parts"]):
        return None
    sw = sws[0]
    keep, gone = sw["pins"].get("1"), sw["pins"].get("2")
    if not keep or not gone or keep == gone:
        return None
    if gone in SUPPLY + ("GND",):
        keep, gone = gone, keep
    d = _copy(base)
    d["parts"] = [p for p in d["parts"] if p["ref"] != sw["ref"]]
    for p in d["parts"]:
        p["pins"] = {k: (keep if v == gone else v) for k, v in p["pins"].items()}
    scen = [x for x in d.get("scenarios") or [] if x.get("switch") != "released"]
    d["scenarios"] = [{k: v for k, v in x.items() if k != "switch"} for x in scen]
    names = {x.get("name") for x in d["scenarios"]}
    d["benchmarks"] = [
        b
        for b in d.get("benchmarks", [])
        if f".{sw['ref']}." not in f".{b['check']}" and (b["check"].split(".")[0] in names or "." not in b["check"])
    ]
    d["_dropped"] = [sw["ref"]]
    d["say"] = f"Removed {sw['ref']}: what it switched is now always on."
    return d


def remove_part(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    """Remove a part by its reference (D2, R3, BZ1) or as 'the second/extra/last LED', with the resistor that
    only served it. The microcontroller and the power input stay: without them nothing else works."""
    m = re.fullmatch(
        PLEASE
        + r"(?:remove|drop|delete|take out|take off|get rid of|lose)\s+(?:the\s+|that\s+|part\s+)?"
        r"(?:([a-z]{1,3}\d{1,3})|(?:second|extra|other|last|new|added)\s+(led|light|resistor|buzzer|header))",
        a,
    )
    if not m:
        return None
    refs = {p["ref"].lower(): p for p in base["parts"]}
    if m.group(1):
        target = refs.get(m.group(1))
    else:
        kind = m.group(2)
        pool = [
            p
            for p in base["parts"]
            if (kind in ("led", "light") and _is_led(p))
            or (kind == "resistor" and p["piece"] == R)
            or (kind == "buzzer" and p["piece"] == BUZZ)
            or (kind == "header" and p["piece"] in (HDR3,))
        ]
        target = (
            pool[-1]
            if len(pool) >= 2 or kind in ("buzzer", "header") and pool
            else None
        )
    if not target or target["piece"] == PICO or target is _power_in(base):
        return None
    gone = {target["ref"]}
    nets = set(target["pins"].values())
    left = [p for p in base["parts"] if p["ref"] not in gone]
    for p in left:  # a resistor whose other end now goes nowhere
        if p["piece"] != R:
            continue
        own = [
            n
            for n in p["pins"].values()
            if n in nets and n not in SUPPLY and n != "GND"
        ]
        if own and not any(
            n in q["pins"].values() for q in left if q is not p for n in own
        ):
            gone.add(p["ref"])
    d = _copy(base)
    d["parts"] = [p for p in d["parts"] if p["ref"] not in gone]
    pins_on: dict[str, int] = {}
    for p in d["parts"]:
        for n in p["pins"].values():
            pins_on[n] = pins_on.get(n, 0) + 1
    if any(
        c == 1 and n in nets and n not in SUPPLY and n not in ("GND", "P3V3")
        for n, c in pins_on.items()
    ):
        return None  # a part in the middle of a circuit: what replaces it is a design call, so the picker makes it
    d["benchmarks"] = [
        b
        for b in d.get("benchmarks", [])
        if not any(re.search(rf"(^|\.){r}\.", b["check"]) for r in gone)
    ]
    if not d["benchmarks"] and base.get("benchmarks"):
        return None  # it was the part every check measures: the picker has to rethink the board
    d["_dropped"] = sorted(gone)
    d["say"] = f"Removed {', '.join(sorted(gone))}."
    return d


def coin_cell(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    """Run the board from a CR2032 instead of the 5 V header (boards without a Pico: a Pico needs USB or 5 V)."""
    if not re.fullmatch(
        PLEASE
        + r"(?:add|use|put|power it (?:from|with|off|on)|run it (?:from|on|off)|make it run on|swap to|switch to)\s+"
        r"(?:a\s+|an\s+)?(?:coin[- ]?cell|cr2032|button cell|coin battery|watch battery)(?:\s+(?:battery|holder|instead))*",
        a,
    ):
        return None
    if any(p["piece"] == PICO for p in base["parts"]):
        return None
    j = _power_in(base)
    if not j or j["piece"] == COIN:
        return None
    d = _copy(base)
    for p in d["parts"]:
        if p["ref"] == j["ref"]:
            p["piece"], p["pins"], p["value"] = COIN, {"1": "VCC", "2": "GND"}, "CR2032"
            p["why"] = "CR2032 coin cell holder (Keystone 1058): 3 V in."
    d["parts"] = [
        p
        for p in d["parts"]
        if not (
            p.get("group") == "power"
            and p["ref"].startswith("R")
            and "CC" in "".join(p["pins"].values())
        )
    ]
    d["voltage"] = 3.0
    d["say"] = f"{j['ref']} is now a CR2032 coin cell holder, so the board runs at 3 V."
    # sweep 10-17: at 3 V an LED's ~2 V drop leaves a third of the 5 V headroom, so a 220 ohm LED resistor gave
    # 1.7 mA, under the 2 mA "on" check; the series resistors of LEDs fed from the supply drop to 68 ohm
    anodes = {p["pins"].get("2") for p in d["parts"] if _is_led(p)}
    eased = []
    for q in d["parts"]:
        nets = set(q["pins"].values())
        if q["piece"] == R and nets & anodes and nets & {"VCC", "VCC_SW"}:
            try:
                if sim_ohms(q.get("value", "")) > 68:
                    q["value"] = "68"
                    eased.append(q["ref"])
            except ValueError:
                pass
    if eased:
        d["say"] += f" {', '.join(eased)} {'is' if len(eased) == 1 else 'are'} now 68 ohm so the LEDs stay bright."
    return d


def add_header(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    """A 3-pin breakout header: supply, the main signal and ground, for probing or chaining boards."""
    if not re.fullmatch(
        PLEASE
        + r"(?:add|put|include)\s+(?:a|an|one)?\s*(?:extra\s+|3[- ]pin\s+|three[- ]pin\s+|breakout\s+|debug\s+|expansion\s+)*"
        r"(?:pin\s+)?(?:header|connector|breakout)(?:\s+(?:too|as well))?",
        a,
    ):
        return None
    if any(p["piece"] == HDR3 and p.get("group") == "breakout" for p in base["parts"]):
        return None
    nets = [n for p in base["parts"] for n in p["pins"].values()]
    sup = next((n for n in (*SUPPLY, "P3V3") if n in nets), None)
    if not sup:
        return None
    led = _main_led(base)
    sig = (led and led["pins"].get("2")) or next(
        (n for n in nets if n not in (*SUPPLY, "P3V3", "GND") and nets.count(n) >= 2),
        "GND",
    )
    d = _copy(base)
    ref = _next_ref(d, "J")
    d["parts"].append(
        {
            "ref": ref,
            "piece": HDR3,
            "pins": {"1": sup, "2": sig, "3": "GND"},
            "group": "breakout",
            "why": f"Breakout header: {sup}, {sig} and GND, to probe the board or chain another one.",
        }
    )
    d["say"] = f"Added {ref}, a 3-pin header with {sup}, {sig} and GND."
    return d


def _cmd_board(base: dict[str, Any]) -> dict[str, Any] | None:
    """The Pico part of a typed-command board (motor car), else None."""
    if not (base.get("firmware") or {}).get("commands"):
        return None
    return next((p for p in base["parts"] if p["piece"] in (PICO, PICO_W)), None)


def mcu_extra(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    """Edits on a typed-command Pico board (QA round 4, motor car). Headlights: LEDs on a spare GPIO (GP18) that the
    firmware lights on every moving command, off on stop. Header: 3.3 V, a spare GPIO (GP20) and ground for a
    sensor. The generic add_led / add_header would hang a second power LED or the power-LED net on them."""
    mcu = _cmd_board(base)
    if not mcu:
        return None
    used = set(mcu["pins"])
    light = re.fullmatch(
        PLEASE
        + r"(?:add|put|include|give it)\s+(?:some\s+)?(a|an|one|two|2|a pair of)?\s*(?:("
        + COLOUR_RX
        + r")\s+)?"
        r"(?:head\s*lights?|front lights?|leds?|lights?)(?:\s+(?:too|as well|on the front|at the front))?",
        a,
    )
    hdr = re.fullmatch(
        PLEASE
        + r"(?:add|put|include)\s+(?:a|an|one)?\s*(?:extra\s+|3[- ]pin\s+|three[- ]pin\s+|sensor\s+|"
        r"breakout\s+|expansion\s+|gpio\s+)*(?:pin\s+)?(?:header|connector|breakout|port)(?:\s+(?:too|as well|for a sensor))?",
        a,
    )
    d = _copy(base)
    cmds = d["firmware"]["commands"]
    if light and "24" not in used:
        n = 2 if (light.group(1) or "") in ("two", "2", "a pair of") else 1
        colour = light.group(2) or "white"
        if colour not in ("red", "green", "blue", "yellow", "white", "orange"):
            return None
        ohms = (
            "47" if colour in ("white", "blue", "green") else "330"
        )  # 3.3 V drive: white/blue/green (InGaN, ~3 V) leave ~0.3 V
        next(p for p in d["parts"] if p["ref"] == mcu["ref"])["pins"]["24"] = "HEAD"
        refs = []
        for _ in range(n):
            dref, rref = _next_ref(d, "D"), _next_ref(d, "R")
            d["parts"] += [
                {
                    "ref": rref,
                    "piece": R,
                    "value": ohms,
                    "pins": {"1": "HEAD", "2": f"{dref}_A"},
                    "group": "output",
                    "why": f"Sets {dref}'s current from the 3.3 V pin (well under the Pico's 12 mA per pin).",
                },
                {
                    "ref": dref,
                    "piece": LED,
                    "value": colour,
                    "pins": {"2": f"{dref}_A", "1": "GND"},
                    "group": "output",
                    "why": "Headlight: on while the car moves, off when it stops.",
                },
            ]
            refs.append(dref)
            d.setdefault("benchmarks", []).extend(
                [
                    {
                        "check": f"idle.{dref}.avg_ma",
                        "min": -1,
                        "max": 0.5,
                        "text": f"{dref} headlight off at power-up",
                    },
                    {
                        "check": f"cmd_f.{dref}.avg_ma",
                        "min": 1,
                        "max": 12,
                        "text": f"{dref} headlight on while driving",
                    },
                    {
                        "check": f"cmd_s.{dref}.avg_ma",
                        "min": -1,
                        "max": 0.5,
                        "text": f"{dref} headlight off on stop",
                    },
                ]
            )
        for c in cmds:
            if c.get("high") and str(c["input"])[:1] != "s":
                c["high"] = list(c["high"]) + ["HEAD"]
        d["say"] = (
            f"Added {' and '.join(refs)} as {colour} headlights on GP18: they light on f, b, l and r and go "
            "off on s. The firmware is regenerated and tested."
        )
        return d
    if hdr and "26" not in used:
        if any(
            p["piece"] == HDR3 and p.get("group") == "breakout" for p in base["parts"]
        ):
            return None
        next(p for p in d["parts"] if p["ref"] == mcu["ref"])["pins"]["26"] = "AUX1"
        jref = _next_ref(d, "J")
        d["parts"].append(
            {
                "ref": jref,
                "piece": HDR3,
                "value": "3V3 GP20 GND",
                "pins": {"1": "P3V3", "2": "AUX1", "3": "GND"},
                "group": "breakout",
                "why": "Sensor header: 3.3 V, spare pin GP20 and ground (a bump switch or a "
                "distance sensor).",
            }
        )
        d.setdefault("firmware", {}).setdefault("inputs", []).append("AUX1")
        d["say"] = (
            f"Added {jref}, a 3-pin sensor header: 3.3 V, GP20 and ground. GP20 stays an input for your sensor (the motor car firmware prints it on ?)."
        )
        return d
    return None


def sound_buzzer(a: str, base: dict[str, Any]) -> dict[str, Any] | None:
    """'add a buzzer' on a Pico sound board (the voice arm): a GPIO cannot drive a buzzer, so a 2N3904 switches it
    from 3.3 V, its base fed from the listening LED's pin through 4.7k. It beeps whenever the board hears a command
    and the LED's timing and current stay as they were. No firmware change and no model call (QA round 6)."""
    if not re.fullmatch(
        PLEASE
        + r"(?:add|put|include|give it)\s+(?:a|an|one)?\s*(?:buzzer|beeper|piezo|beep)(?:\s+(?:too|as well|"
        r"that beeps(?: when it (?:hears|gets) (?:a |the )?(?:command|word|voice|me|sound)s?)?))?",
        a,
    ):
        return None
    fw = base.get("firmware") or {}
    if fw.get("mode") != "sound" or not fw.get("loud_high"):
        return None
    if not any(p["piece"] == PICO for p in base["parts"]) or any(
        p.get("group") == "buzzer" or p["piece"] == BUZZ for p in base["parts"]
    ):
        return None
    drive = fw["loud_high"][0]
    nets = {n for p in base["parts"] for n in p["pins"].values()}
    if drive not in nets or "P3V3" not in nets:
        return None
    d = _copy(base)
    rref, qref, bref = _next_ref(d, "R"), _next_ref(d, "Q"), _next_ref(d, "BZ")
    d["parts"] += [
        {
            "ref": rref,
            "piece": R,
            "value": "4.7k",
            "pins": {"1": drive, "2": f"{qref}_B"},
            "group": "buzzer",
            "why": f"Base resistor: about 0.5 mA from the {drive} pin, so the LED on that pin is unchanged.",
        },
        {
            "ref": qref,
            "piece": NPN,
            "pins": {"1": "GND", "2": f"{qref}_B", "3": f"{bref}_N"},
            "group": "buzzer",
            "why": "Switches the buzzer (a GPIO pin cannot supply its current).",
        },
        {
            "ref": bref,
            "piece": BUZZ,
            "pins": {"1": "P3V3", "2": f"{bref}_N"},
            "group": "buzzer",
            "why": "Beeps while the board hears a command.",
        },
    ]
    names = [s.get("name") for s in d.get("scenarios", [])]
    if "quiet" in names and "speaking" in names:
        d.setdefault("benchmarks", []).extend(
            [
                {
                    "check": f"quiet.{bref}.avg_ma",
                    "max": 0.5,
                    "text": f"{bref} silent when nobody speaks",
                },
                {
                    "check": f"speaking.{bref}.avg_ma",
                    "min": 5,
                    "text": f"{bref} beeps on a command",
                },
            ]
        )
    d["say"] = (
        f"Added {bref}, a buzzer that beeps whenever the board hears a command ({qref} switches it)."
    )
    return d


EDITS = (
    mcu_extra,
    recolour,
    board_colour,
    sound_buzzer,
    add_buzzer,
    add_status_led,
    add_led,
    remove_added,
    remove_switch,
    remove_part,
    swap_power,
    coin_cell,
    add_header,
    shrink,
)


def edit(
    request: str, base: dict[str, Any], catalog: dict[str, dict[str, Any]] | None = None
) -> tuple[dict[str, Any], str] | None:
    """(new design, edit name) for a change Etch can make by itself on the current board, else None."""
    a = re.sub(r"\s+", " ", request.lower()).strip(" .!?")
    one = _edit_one(request, a, base, catalog)
    if one:
        return one
    # several changes in one ask ("add a second led and make it blue"): each clause must be an edit Etch knows,
    # applied in order on the previous result; "it"/"them" after an addition means the new parts
    clauses = [c for c in _CLAUSE.split(a) if c.strip()]
    if len(clauses) < 2:
        return None
    d, names, dropped, new_refs = base, [], [], []
    for c in clauses:
        c = c.strip(" ,.")
        m = re.fullmatch(rf"(?:make|turn|change|set)\s+(?:it|them|that|those|the new ones?)\s+(?:to\s+|into\s+)?({COLOUR_RX})", c)
        new_leds = [r for r in new_refs if any(p["ref"] == r and _is_led(p) for p in d["parts"])]
        if m and new_leds:
            nd, name = _colour_leds(d, new_leds, m.group(1)), "recolour"
        else:
            hit = _edit_one(c, c, d, catalog)
            if not hit:
                return None
            nd, name = hit
        dropped += nd.pop("_dropped", [])
        before = {p["ref"] for p in d["parts"]}
        new_refs = [p["ref"] for p in nd["parts"] if p["ref"] not in before]
        says = [x for x in (d.get("say") if d is not base else None, nd.get("say")) if x]
        nd["say"] = " ".join(says)
        d, names = nd, names + [name]
    if dropped:
        d["_dropped"] = sorted(set(dropped))
    return d, "+".join(names)


_CLAUSE = re.compile(
    r"\s*,\s*(?:and\s+|then\s+)?|\s+(?:and|then)\s+(?=(?:also\s+)?(?:add|remove|drop|delete|make|use|swap|change|"
    r"turn|put|run|power|give|switch|set|take|get rid)\b)"
)


def _edit_one(
    request: str, a: str, base: dict[str, Any], catalog: dict[str, dict[str, Any]] | None
) -> tuple[dict[str, Any], str] | None:
    for fn in EDITS:
        d = fn(request if fn is recolour else a, base)
        if d and (catalog is None or all(p["piece"] in catalog for p in d["parts"])):
            return d, fn.__name__
    return None

"""Part selection: which catalog families a board ask needs. One function, swappable backends.

  select_parts(ask, pieces) -> Selection(families, kept, backend, seconds)

Backends (ETCH_CLASSIFIER, default "table"):
  table      a keyword table in plain code: the families each phrase implies. Runs in well under a millisecond,
             never calls a model, and is the default because it is exact on the asks Etch serves.
  decisions  the OpenAI Decisions API (POST /v1/decisions, gpt-6-luna): one call with one yes/no predicate per
             family, about 0.85 s. One attempt, a 6 s timeout, no retry and no fallback: if it fails the build
             stops and says why.

Both return families; the kept list is then the canonical pieces of those families (one good footprint per
kind, not 48 variants), so the picker reads a short list.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from . import config

BACKEND = os.environ.get("ETCH_CLASSIFIER", "table")
DECISIONS_MODEL = os.environ.get("ETCH_DECISIONS_MODEL", "gpt-6-luna")

ALWAYS = ("resistor", "capacitor", "electrolytic capacitor")

# phrase -> families it needs. Order does not matter; every matching row adds its families.
TABLE: list[tuple[str, tuple[str, ...]]] = [
    (
        r"\brgb\b|colou?rs?\b|\b(red|green|blue|yellow|purple|cyan|magenta|white)\b.*\b(red|green|blue|yellow|purple|cyan|magenta|white)\b",
        ("RGB LED",),
    ),
    (
        r"\bleds?\b|\blights? up\b|\blight\b|\bglow|\bblink|\bflash|\bindicator|\blamp",
        ("LED",),
    ),
    (
        r"\bblink|\bflash|\bflicker|\bstrobe|\bpolice|\balternat|\boscillat|\bastable",
        ("BJT transistor", "electrolytic capacitor"),
    ),
    (r"\bbuzz|\bbeep|\balarm|\bsiren|\btone\b", ("buzzer", "BJT transistor")),
    (
        r"\bdark|\bnight|\blight sensor|\bldr\b|\bphoto|\bsunlight|\bbright",
        ("light sensor", "BJT transistor"),
    ),
    (r"\bbutton|\bpress(?!\s+\w\b)|\bpush\b|\bswitch\b", ("push button",)),
    (
        r"\bspeak|\btalk|\bvoice|\bsound|\bnois|\bclap|\bloud|\bmic(rophone)?\b|\baudio|\bsing\b|\bhear\b|\bhearing",
        ("mic module", "USB microcontroller", "RGB LED"),
    ),
    (
        r"\btype\b|\btyping|\bkeyboard|\blaptop|\bcomputer|\busb\b|\bserial|\bcommand|\bcode\b|\bprogram|\bmicrocontroller|\bpico\b|\barduino",
        ("USB microcontroller",),
    ),
    (
        r"\bfan\b|\bmotor\b|\brelay\b|\bsolenoid",
        ("2-pin connector", "BJT transistor", "diode"),
    ),
    (r"\bbattery level|\bbattery indicator|\bvoltage level|\bzener", ("diode",)),
    (
        r"\bknob|\badjust|\bdimm|\btrim|\bpot(entiometer)?\b|\bsensitiv",
        ("trimmer potentiometer",),
    ),
    (r"\bcoin cell|\bcr2032|\bbattery\b", ("battery holder",)),
    # boards that read or show something need a microcontroller to do it
    (r"\boled\b|\bdisplay|\bscreen\b|\blcd\b|\bshows? (the )?(reading|temperature|value|number)", ("OLED display module", "USB microcontroller")),
    (r"\btemperature|\bthermo|\bthermistor|\bhow (hot|cold)|\btemp\b", ("thermistor", "USB microcontroller")),
    (r"\bhumid\w*|\bdht\d*|\bweather", ("humidity sensor", "USB microcontroller")),
    (r"\bheart|\bpulse|\bbpm\b", ("light sensor", "LED", "USB microcontroller")),
    (r"\bmotor driver|\bh-?bridge|\b(two|2|dc) motors?\b|\bwheels?\b|\brobot car|\bl293", ("motor driver", "2-pin connector", "USB microcontroller")),
    (r"\bbluetooth|\bwi-?fi|\bwireless|\besp32|\bphone\b|\binternet|\bonline", ("USB microcontroller",)),
    (r"\bstrip\b|\bneopixel|\bws2812", ("USB microcontroller", "BJT transistor")),
    (r"\busb-?c\b", ("USB-C power input",)),
    (r"\bbarrel|\bwall (plug|adapter)|\b9 ?v\b|\b12 ?v\b", ("DC barrel jack",)),
]

# the footprint Etch builds with for each family (the rest stay in the library for the picker to ask for)
CANON: dict[str, tuple[str, ...]] = {
    "resistor": ("R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical",),
    "capacitor": ("C@C_Disc_D5.0mm_W2.5mm_P2.50mm",),
    "electrolytic capacitor": ("C_Polarized@CP_Radial_D5.0mm_P2.00mm",),
    "LED": ("LED@LED_D5.0mm",),
    "BJT transistor": ("2N3904@", "2N3906@"),
    "diode": ("1N4148@", "1N4007@", "1N5819@"),
    "buzzer": ("Buzzer@Buzzer_12x9.5RM7.6",),
    "light sensor": ("A1050@",),
    "push button": ("SW_Push@SW_PUSH_6mm",),
    "battery holder": ("BatteryHolder_Keystone_1058_1x2032",),
    "2-pin connector": (
        "Conn_01x02@PinHeader_1x02_P2.54mm_Vertical",
        "TerminalBlock_Phoenix_MKDS-1,5-2-5.08",
    ),
    "trimmer potentiometer": ("Potentiometer_Bourns_3296W_Vertical",),
    "humidity sensor": ("DHT11@",),
    "thermistor": ("Thermistor_NTC@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical",),
    "motor driver": ("L293D@",),
}


@dataclass
class Selection:
    families: list[str]
    kept: list[dict[str, Any]]
    backend: str
    seconds: float
    detail: dict[str, Any]


def _table(ask: str, families: list[str]) -> tuple[set[str], dict[str, Any]]:
    a = ask.lower()
    got: set[str] = set()
    hits = []
    for rx, fams in TABLE:
        if re.search(rx, a):
            got |= set(fams)
            hits.append(rx.split("|")[0])
    return got & set(families), {"rules_matched": len(hits)}


def _decisions(ask: str, families: list[str]) -> tuple[set[str], dict[str, Any]]:
    body = {
        "model": DECISIONS_MODEL,
        "input": ask,
        "questions": [
            {
                "type": "predicate",
                "name": f"f{i}",
                "instructions": f"A small through-hole circuit board that does this needs at least one {f}.",
            }
            for i, f in enumerate(families)
        ],
    }
    req = urllib.request.Request(
        config.BASE_URL.rstrip("/") + "/decisions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {config.api_key()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            ans = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(
            f"OpenAI Decisions API call failed ({e}); not retried, no fallback"
        ) from e
    probs = {a["name"]: float(a.get("probability", 0)) for a in ans.get("answers", [])}
    got = {f for i, f in enumerate(families) if probs.get(f"f{i}", 0) >= 0.5}
    return got, {
        "model": DECISIONS_MODEL,
        "probabilities": {families[int(k[1:])]: round(v, 3) for k, v in probs.items()},
    }


def _power(got: set[str]) -> set[str]:
    """Exactly one way in for power: the USB microcontroller when there is one, else a coin cell holder when
    asked, else a 2-pin power header."""
    power = {"battery holder", "USB-C power input", "DC barrel jack"}
    if "USB microcontroller" in got:
        return got - power
    if not got & power:
        got = got | {"2-pin connector"}
    return got


def _is(key: str, want: str) -> bool:
    """'2N3904@' = that symbol on any footprint; 'LED@LED_D5.0mm' = exactly that symbol and footprint;
    'BatteryHolder_...' = exactly that footprint."""
    if want.endswith("@"):
        return f":{want}" in key
    return key.endswith((f":{want}", f"@{want}", f":{want.split('@')[-1]}"))


def canonical(pieces: list[dict[str, Any]], families: set[str]) -> list[dict[str, Any]]:
    out = []
    for p in pieces:
        f = p["family"]
        if f not in families:
            continue
        want = CANON.get(f)
        if want and not any(_is(p["key"], w) for w in want):
            continue
        out.append(p)
    return out


def select_parts(
    ask: str, pieces: list[dict[str, Any]], backend: str | None = None
) -> Selection:
    backend = backend or BACKEND
    families = sorted({p["family"] for p in pieces if p["family"] not in ALWAYS})
    t0 = time.perf_counter()
    if backend == "decisions":
        got, detail = _decisions(ask, families)
    elif backend == "table":
        got, detail = _table(ask, families)
    else:
        raise ValueError(
            f"unknown ETCH_CLASSIFIER backend {backend!r} (table or decisions)"
        )
    got = _power(got | set(ALWAYS))
    secs = time.perf_counter() - t0
    usable = [
        p for p in pieces if p.get("model") and p.get("pads_ok") and p.get("spice")
    ]
    return Selection(
        sorted(got), canonical(usable, got), backend, round(secs, 4), detail
    )

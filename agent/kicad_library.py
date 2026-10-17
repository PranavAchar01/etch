"""The whole parts library: every symbol in every installed library, with its footprint and 3D model, searchable.

agent/kicad_catalog.py keeps the curated pieces Etch simulates well (304 of them, one good footprint per kind).
This module adds everything else so a board never has to leave the machine for a common part:

  sources (in this order; the first source that has a key wins)
    1. the official KiCad 10 libraries installed with KiCad (SharedSupport/symbols, footprints, 3dmodels)
    2. manufacturer libraries vendored in pcb-loop/vendor/ (Espressif, pinned tag)
    3. parts fetched earlier into engine/parts/cache/<LIB>/ (LCSC / EasyEDA, GitHub), each with PROVENANCE.json
    4. open-source libraries in engine/parts/extra_libs/<source>/ listed in extra_libs/MANIFEST.json
       (sources with a non-commercial or unknown licence are skipped)

  For each symbol: its default footprint, else the first footprint its ki_fp_filters match whose pads cover the
  symbol's pins (one with a STEP model first). The STEP model is referenced in place (never copied). Every piece
  records where it came from and its licence; copyleft licences (CC-BY-SA, GPL) are flagged.

Nothing is drawn or invented here: a symbol with no footprint or no pins is left out, and a footprint with no
STEP model stays in the library with model None (lookups say so).

  uv run --project engine python -m agent.kicad_library          # (re)build engine/parts/kicad_library.json
  uv run --project engine python -m agent.kicad_library "1N4148"  # search it

The JSON is generated (gitignored): load() builds it on first use when it is missing or older than the libraries.
"""

from __future__ import annotations

import fnmatch
import functools
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from etchlib.kicad import FOOTPRINT_DIR, MODEL_DIR, PARTS_CACHE, SYMBOL_DIR, VENDOR_LIBS

from . import config

OUT = config.ENGINE / "parts/kicad_library.json"
# where the build finds the symbol / footprint libraries that are not KiCad's own (read by etchlib.kicad)
LIB_PATHS = config.ENGINE / "parts/lib_paths.json"
VERSION = 4  # bump when the record format changes: load() rebuilds an older file
EXTRA = Path(os.environ.get("ETCH_EXTRA_LIBS", str(config.ENGINE / "parts/extra_libs")))
if not EXTRA.exists():
    # the fetch job writes into the main checkout; a worktree or a deploy copy reads it there
    _main = Path.home() / "helloworld/pcb-loop/engine/parts/extra_libs"
    if _main.exists():
        EXTRA = _main

KICAD_LICENCE = "CC-BY-SA-4.0 with the KiCad libraries exception (https://www.kicad.org/libraries/license/)"
KICAD_REPOS = {
    "symbol": "https://gitlab.com/kicad/libraries/kicad-symbols/-/blob/master/{lib}.kicad_sym",
    "footprint": "https://gitlab.com/kicad/libraries/kicad-footprints/-/blob/master/{lib}.pretty/{name}.kicad_mod",
    "model": "https://gitlab.com/kicad/libraries/kicad-packages3D/-/blob/master/{path}",
}
BAD_LICENCE = re.compile(
    r"\bNC\b|non-?commercial|unknown|proprietary|all rights reserved", re.I
)
COPYLEFT = re.compile(r"CC-?BY-?SA|GPL|copyleft|share-?alike", re.I)

# library name -> the family the rest of Etch understands (ref letters, layout tiers, SPICE cards)
LIB_FAMILY = {
    "Diode": "diode",
    "Transistor_BJT": "BJT transistor",
    "Transistor_FET": "MOSFET",
    "Timer": "timer IC",
    "Driver_Motor": "motor driver",
    "Sensor_Optical": "light sensor",
    "Buzzer_Beeper": "buzzer",
    "Battery": "battery holder",
    "Relay": "relay",
    "Relay_SolidState": "relay",
    "Display_Character": "display",
    "Display_Graphic": "display",
    "LED": "addressable LED",
    "Switch": "switch",
    "Connector_Audio": "audio jack",
    "Connector_Card": "card socket",
    "Connector_USB": "USB connector",
    "Power_Protection": "ESD protection",
    "Battery_Management": "battery charger",
    "Regulator_Linear": "linear regulator",
    "Regulator_Switching": "switching regulator",
    "Interface_USB": "USB interface IC",
    "Memory_Flash": "flash memory",
    "Memory_EEPROM": "EEPROM",
    "Oscillator": "oscillator",
    "Device": "passive",
    "Amplifier_Audio": "audio amplifier",
    "Audio": "audio IC",
    "Sensor": "sensor",
    "Sensor_Humidity": "humidity sensor",
    "Sensor_Temperature": "temperature sensor",
    "Sensor_Pressure": "pressure sensor",
    "Sensor_Motion": "motion sensor",
    "Sensor_Magnetic": "magnetic sensor",
    "Sensor_Gas": "gas sensor",
    "Sensor_Current": "current sensor",
    "Sensor_Proximity": "proximity sensor",
    "Sensor_Audio": "microphone",
    "Interface_Optical": "IR receiver",
    "RF_Module": "radio module",
    "RF_Bluetooth": "radio module",
    "RF_WiFi": "radio module",
    "RF_GPS": "GPS module",
    "MCU_RaspberryPi": "microcontroller",
    "MCU_Microchip_ATmega": "microcontroller",
    "MCU_Module": "microcontroller module",
    "Connector_Generic": "connector",
    "Connector": "connector",
    "Motor": "motor",
    "Mechanical": "mechanical",
}
# symbols that are not parts
SKIP_LIBS = {"power", "pspice", "Simulation_SPICE", "Graphic", "Mechanical"}

# family words a plain ask uses -> the words the index holds ("mic" finds "microphone")
SYNONYMS = {
    "mic": "microphone",
    "oled": "ssd1306",
    "neopixel": "ws2812b",
    "ws2812": "ws2812b",
    "headphone": "jack",
    "earphone": "jack",
    "microsd": "micro sd",
    "sd": "sd",
    "thermistor": "ntc",
    "imu": "mpu6050",
    "accelerometer": "accelerometer",
    "hotswap": "hotswap",
    "trrs": "trrs",
    "encoder": "rotary encoder",
    "esd": "usblc6",
    "ldr": "r_photo photoresistor",
    "photoresistor": "r_photo",
    "midi": "din 5",
    "fader": "slide potentiometer",
    "pot": "potentiometer",
    "knob": "potentiometer",
    "optocoupler": "6n138",
    "psram": "aps6404",
    "dac": "pcm5102",
    "codec": "wm8731",
}

_PROP = re.compile(r'\(property "([^"]+)" "((?:[^"\\]|\\.)*)"')
_PIN = re.compile(
    r'\(pin (\w+) \w+.*?\(name "((?:[^"\\]|\\.)*)".*?\(number "((?:[^"\\]|\\.)*)"', re.S
)
_SYM = re.compile(r'\n\t\(symbol "([^"]+)"\n')


def _tok(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", s.lower())


# ---------------------------------------------------------------- sources


def _sources() -> list[dict[str, Any]]:
    """[{name, symbols: [paths], fp_roots: {lib: dir holding <lib>.pretty}, model_dir, licence, provenance}]"""
    out = [
        {
            "name": "kicad",
            "symbols": sorted(SYMBOL_DIR.glob("*.kicad_sym")),
            "fp_roots": {p.stem: FOOTPRINT_DIR for p in FOOTPRINT_DIR.glob("*.pretty")},
            "model_dir": MODEL_DIR,
            "licence": KICAD_LICENCE,
            "route": "KiCad 10 official library (installed with KiCad)",
        }
    ]
    for lib, root in VENDOR_LIBS.items():
        if (root / "symbols").exists():
            prov = (
                (root / "PROVENANCE.md").read_text()[:400]
                if (root / "PROVENANCE.md").exists()
                else ""
            )
            out.append(
                {
                    "name": f"vendor:{lib}",
                    "symbols": sorted((root / "symbols").glob("*.kicad_sym")),
                    "fp_roots": {
                        p.stem: root / "footprints"
                        for p in (root / "footprints").glob("*.pretty")
                    },
                    "model_dir": root / "3dmodels",
                    "licence": "CC-BY-SA-4.0 with the KiCad libraries exception (vendor LICENSE)",
                    "route": f"{lib} manufacturer library (vendored, pinned tag)",
                    "note": prov,
                }
            )
    if PARTS_CACHE.exists():
        for d in sorted(PARTS_CACHE.iterdir()):
            sym = d / f"{d.name}.kicad_sym"
            if not sym.exists():
                continue
            pv = d / "PROVENANCE.json"
            meta = json.loads(pv.read_text()) if pv.exists() else {}
            lic = str(
                meta.get("licence")
                or (
                    "LCSC/EasyEDA part data (easyeda2kicad)"
                    if d.name.startswith("LCSC_")
                    else ""
                )
            )
            if BAD_LICENCE.search(lic) or not lic:
                continue
            out.append(
                {
                    "name": f"cache:{d.name}",
                    "symbols": [sym],
                    "fp_roots": {d.name: d},
                    "model_dir": d,
                    "licence": lic,
                    "route": meta.get("route")
                    or ("LCSC / EasyEDA" if d.name.startswith("LCSC_") else "GitHub"),
                    "meta": meta,
                }
            )
    man = EXTRA / "MANIFEST.json"
    if man.exists():
        try:
            entries = json.loads(man.read_text())
        except ValueError:
            entries = []
        if isinstance(entries, dict):
            lst = entries.get("sources") or entries.get("libraries")
            if isinstance(lst, dict):
                lst = [{"name": k, **v} for k, v in lst.items() if isinstance(v, dict)]
            entries = (
                lst
                if isinstance(lst, list)
                else [
                    {"name": k, **v} for k, v in entries.items() if isinstance(v, dict)
                ]
            )
        kicad_libs = {p.stem for p in SYMBOL_DIR.glob("*.kicad_sym")} | {
            p.stem for p in FOOTPRINT_DIR.glob("*.pretty")
        }
        for e in entries if isinstance(entries, list) else []:
            if not isinstance(e, dict):
                continue
            lic = str(e.get("licence") or e.get("license") or "")
            flag = str(
                e.get("flag") or e.get("licence_flag") or e.get("license_flag") or ""
            )
            if (
                not lic
                or BAD_LICENCE.search(lic)
                or re.search(r"\b(nc|unknown|skip|non-?commercial)\b", flag, re.I)
            ):
                continue
            root = None
            for k in ("dir", "path", "folder", "slug", "name", "source", "id"):
                v = e.get(k)
                if isinstance(v, str) and v and (EXTRA / Path(v).name).is_dir():
                    root = EXTRA / Path(v).name
                    break
            if root is None:
                continue
            junk = re.compile(
                r"/(\.git|tests?|deps|demo|kicad-footprint-generator|node_modules)/"
            )
            syms = sorted(
                f
                for f in root.rglob("*.kicad_sym")
                if not junk.search(str(f)) and f.stem not in kicad_libs
            )
            fps = {
                p.stem: p.parent
                for p in root.rglob("*.pretty")
                if p.is_dir()
                and not junk.search(str(p) + "/")
                and p.stem not in kicad_libs
            }
            if not fps:
                continue
            out.append(
                {
                    "name": f"extra:{root.name}",
                    "symbols": syms,
                    "fp_roots": fps,
                    "model_dir": root,
                    "licence": lic,
                    "route": f"open-source library {e.get('name') or root.name}",
                    "meta": {
                        k: e.get(k)
                        for k in ("url", "version", "commit", "repo", "tag", "flag")
                        if e.get(k)
                    },
                }
            )
    return out


# ---------------------------------------------------------------- symbols and footprints


def _symbols(path: Path) -> dict[str, dict[str, Any]]:
    """name -> {props, pins {num: name}, kinds {num: electrical type}} for the top-level symbols of one file."""
    text = path.read_text(errors="replace")
    starts = list(_SYM.finditer(text))
    raw: dict[str, dict[str, Any]] = {}
    for k, m in enumerate(starts):
        end = starts[k + 1].start() if k + 1 < len(starts) else len(text)
        block = text[m.end() : end]
        ext = re.search(r'\(extends "([^"]+)"\)', block[:300])
        props = dict(_PROP.findall(block))
        pins, kinds = {}, {}
        for kind, name, num in _PIN.findall(block):
            pins.setdefault(num, name)
            kinds.setdefault(num, kind)
        raw[m.group(1)] = {
            "props": props,
            "pins": pins,
            "kinds": kinds,
            "extends": ext.group(1) if ext else "",
        }

    def resolve(name: str, depth: int = 0) -> dict[str, Any]:
        s = raw[name]
        par = s["extends"]
        if not par or par not in raw or depth > 6:
            return s
        base = resolve(par, depth + 1)
        props = {**base["props"], **{k: v for k, v in s["props"].items() if v}}
        return {
            "props": props,
            "pins": s["pins"] or base["pins"],
            "kinds": s["kinds"] or base["kinds"],
            "extends": par,
        }

    return {n: resolve(n) for n in raw}


class Footprints:
    """'Lib:Name' -> (pads, STEP path or None, description), parsed on first use; filter matching over names."""

    def __init__(self, sources: list[dict[str, Any]]):
        self.where: dict[str, tuple[Path, dict[str, Any]]] = {}
        names: list[str] = []
        for src in sources:
            for lib, root in src["fp_roots"].items():
                d = root / f"{lib}.pretty"
                if not d.is_dir():
                    continue
                for f in d.glob("*.kicad_mod"):
                    fid = f"{lib}:{f.stem}"
                    if fid not in self.where:
                        self.where[fid] = (f, src)
                        names.append(fid)
        self.blob = "\n".join(sorted(names))
        self.cache: dict[str, tuple[set[str], str | None, str]] = {}
        self.filters: dict[str, list[str]] = {}
        self.note: dict[str, str] = {}
        self.step_maps: dict[str, dict[str, str]] = {}
        self.dir_maps: dict[Path, list[str]] = {}

    def info(self, fid: str) -> tuple[set[str], str | None, str]:
        if fid in self.cache:
            return self.cache[fid]
        hit = self.where.get(fid)
        if not hit:
            self.cache[fid] = (set(), None, "")
            return self.cache[fid]
        f, src = hit
        text = f.read_text(errors="replace")
        pads = set(re.findall(r'\(pad "([^"]*)"', text)) - {""}
        model = None
        for mm in re.finditer(r'\(model "([^"]+)"', text):
            if "(hide yes)" in text[mm.end() : mm.end() + 60]:
                continue
            raw = mm.group(1)
            cands = []
            rel = re.sub(r"^\$\{[A-Z0-9_]+\}/", "", raw)
            for base in (
                MODEL_DIR,
                src["model_dir"],
                f.parent.parent,
                f.parent.parent.parent,
            ):
                cands.append(base / rel)
                cands.append(base / re.sub(r"^3dmodels/", "", rel))
            if raw.startswith("/"):
                cands.insert(0, Path(raw))
            for c in cands:
                for step in (
                    c.with_suffix(".step"),
                    c.with_suffix(".stp"),
                    c.with_suffix(".STEP"),
                ):
                    if step.is_file():
                        model = str(step)
                        break
                if model:
                    break
            if model is None and src["name"] != "kicad":
                # the library's own path variable (${KISWITCH_3DMODEL_DIR}...): the same file name inside it
                model = self._steps(src).get(Path(rel).stem.lower())
            if model:
                break
        if model is None and src["name"] != "kicad":
            # a library that ships one STEP per footprint next to it (or in a sibling 3dshapes folder)
            model = self._steps(src).get(f.stem.lower())
        if model is None:
            model = self._same_package(f.stem, text)
            if model:
                self.note[fid] = (
                    f"3D model of the same package outline from the KiCad library ({Path(model).stem}); "
                    "the footprint's own model is not shipped"
                )
        d = re.search(r'\(descr "((?:[^"\\]|\\.)*)"', text)
        self.cache[fid] = (pads, model, d.group(1) if d else "")
        return self.cache[fid]

    def _steps(self, src: dict[str, Any]) -> dict[str, str]:
        """stem -> STEP path for every model a non-KiCad library ships (scanned once per source)."""
        name = src["name"]
        if name not in self.step_maps:
            m: dict[str, str] = {}
            roots = {Path(src["model_dir"])} | {
                Path(r) for r in src["fp_roots"].values()
            }
            for r in roots:
                if r.is_dir():
                    for c in r.rglob("*"):
                        if c.suffix.lower() in (".step", ".stp") and c.is_file():
                            m.setdefault(c.stem.lower(), str(c))
            self.step_maps[name] = m
        return self.step_maps[name]

    def _same_package(self, stem: str, text: str) -> str | None:
        """A KiCad STEP for the same package and body (QFN-16 3x3 mm, 0.5 mm pitch) when this exact variant's model
        is not shipped: only the exposed pad or a pad-size suffix differs. Matched on package, size and pitch."""
        mm = re.search(r'\(model "\$\{[A-Z0-9_]+\}/([^/"]+\.3dshapes)/', text)
        parts = stem.split("_")
        pitch = next(
            (i for i, w in enumerate(parts) if re.fullmatch(r"P\d+(\.\d+)?mm", w)), None
        )
        if not mm or pitch is None or pitch < 1:
            return None
        prefix = "_".join(parts[: pitch + 1]).lower()
        d = MODEL_DIR / mm.group(1)
        if d not in self.dir_maps:
            self.dir_maps[d] = (
                sorted(str(c) for c in d.glob("*.step")) if d.is_dir() else []
            )
        for c in self.dir_maps[d]:
            if Path(c).stem.lower().startswith(prefix):
                return c
        return None

    def match(self, filters: str, pins: set[str], k: int = 1) -> list[str]:
        """Footprints the symbol's ki_fp_filters allow whose numbered pads are exactly its pins (extra unnumbered
        mounting pads are fine), ones with a STEP model first, in filter order; up to k of them. When nothing fits
        exactly, the first footprint whose pads cover the pins."""
        if not filters.strip():
            return []
        if filters not in self.filters:
            found: list[str] = []
            for flt in filters.split():
                tr = fnmatch.translate(flt)
                rx = tr[4:-3] if tr.startswith("(?s:") else flt
                pat = re.compile(r"^(?:[^:\n]+:)?(" + rx + r")$", re.M | re.I)
                found += [m.group(0) for m in pat.finditer(self.blob)]
            self.filters[filters] = list(dict.fromkeys(found))
        exact, cover = [], []
        for fid in self.filters[filters][:400]:
            pads, model, _ = self.info(fid)
            if pins and not pins <= pads:
                continue
            extra = {p for p in pads - pins if p[:1].isdigit()}
            (exact if not extra else cover).append((not model, fid))
        exact.sort(key=lambda t: t[0])
        cover.sort(key=lambda t: t[0])
        return (
            [f for m, f in exact if not m][:k]
            or [f for _, f in exact[:1]]
            or [f for _, f in cover[:1]]
        )


# ---------------------------------------------------------------- build


def _family(lib: str, name: str, descr: str, kw: str) -> str:
    if lib == "Device":
        n = name.upper()
        for pre, fam in (
            ("R_POT", "potentiometer"),
            ("R", "resistor"),
            ("C_POL", "electrolytic capacitor"),
            ("CP", "electrolytic capacitor"),
            ("C", "capacitor"),
            ("LED", "LED"),
            ("L", "inductor"),
            ("CRYSTAL", "crystal"),
            ("RESONATOR", "crystal"),
            ("BUZZER", "buzzer"),
            ("SPEAKER", "speaker"),
            ("THERMISTOR", "thermistor"),
            ("FUSE", "fuse"),
            ("D", "diode"),
            ("Q", "transistor"),
            ("BATTERY", "battery"),
            ("MICROPHONE", "microphone"),
            ("ROTARY_ENCODER", "rotary encoder"),
        ):
            if n.startswith(pre):
                return fam
    if lib.startswith("Connector") and re.search(r"usb", name, re.I):
        return "USB connector"
    return (
        LIB_FAMILY.get(lib)
        or LIB_FAMILY.get(lib.split("_")[0])
        or lib.replace("_", " ").lower()
    )


def _spice(
    lib_id: str, family: str, pins: dict[str, str], fp: str
) -> tuple[dict[str, Any], str]:
    from .kicad_catalog import spice_for

    try:
        sp, note = spice_for(lib_id, family, pins, fp)
    except Exception:  # a symbol with unusual pins: the generic mapping below
        sp, note = None, ""
    if sp:
        return sp, note
    nums = sorted(pins, key=lambda s: (len(s), s))
    if family in ("switch",) and len(nums) >= 2:
        return {
            "prefix": "S",
            "pins": nums[:2],
            "role": "switch",
        }, "switch (pressed / released scenarios)"
    return (
        {"prefix": "X", "pins": nums, "role": "passive"},
        "not simulated (no SPICE model in the library): wiring checked by ERC, DRC and netlist parity",
    )


def _prov(src: dict[str, Any], lib: str, fid: str, model: str | None) -> dict[str, Any]:
    lic = src["licence"]
    p: dict[str, Any] = {
        "route": src["route"],
        "licence": lic,
        "copyleft": bool(COPYLEFT.search(lic)),
    }
    if src["name"] == "kicad":
        flib, fname = fid.split(":", 1)
        p["symbol_url"] = KICAD_REPOS["symbol"].format(lib=lib)
        p["source_url"] = KICAD_REPOS["footprint"].format(lib=flib, name=fname)
        if model and str(MODEL_DIR) in model:
            p["model_url"] = KICAD_REPOS["model"].format(
                path=os.path.relpath(model, MODEL_DIR)
            )
        p["version"] = "KiCad 10 libraries as installed"
    else:
        p.update(
            {
                k: v
                for k, v in (src.get("meta") or {}).items()
                if isinstance(v, (str, int, float))
            }
        )
    return p


VARIANTS = 30  # footprints kept per generic symbol (Conn_01x02 as JST PH, JST XH, pin header, screw terminal...)


def _add(pieces, seen, skipped, fps, src, lib, name, s, fid) -> None:
    key = f"kicad:{lib}:{name}@{fid.split(':', 1)[1]}"
    if key in seen:
        skipped["duplicate"] += 1
        return
    seen.add(key)
    props, pins = s["props"], s["pins"]
    pads, model, fdescr = fps.info(fid)
    descr = props.get("Description", "") or fdescr
    kw = props.get("ki_keywords", "")
    fam = _family(lib, name, descr, kw)
    lib_id = f"{lib}:{name}"
    sp, note = _spice(lib_id, fam, pins, fid)
    ds = props.get("Datasheet", "")
    pieces.append(
        {
            "key": key,
            "lib_id": lib_id,
            "footprint": fid,
            "family": fam,
            "name": f"{name} / {fid.split(':', 1)[1]}",
            "descr": descr[:160],
            "kw": kw[:160],
            "pins": pins,
            "pads_ok": set(pins) <= pads,
            "model": model,
            "spice": sp,
            "spice_note": note,
            "datasheet": ds[:200] if ds not in ("~", "") else "",
            "source": "kicad-lib" if src["name"] == "kicad" else src["name"],
            "provenance": {
                **_prov(src, lib, fid, model),
                **({"model_note": fps.note[fid]} if fid in fps.note else {}),
            },
        }
    )


# Footprint-only libraries (kiswitch, marbastlib footprints with no symbol of their own) are paired with the KiCad
# generic symbol of the same kind, accepted only when that symbol's pin numbers are exactly the footprint's numbered
# pads. footprint name pattern -> generic KiCad symbols to try, in order.
PAIRS = (
    (
        r"hotswap|^hs_|^sw_.*(mx|choc|kailh|alps|gateron|cherry)|^(sw_)?(mx|choc)\b|^sw_",
        ("Switch:SW_Push", "Switch:SW_SPST"),
    ),
    (
        r"ec11|^rot_|rotaryencoder|encoder",
        ("Device:RotaryEncoder_Switch", "Device:RotaryEncoder"),
    ),
    (r"sk6812|ws2812", ("LED:SK6812MINI", "LED:WS2812B", "LED:SK6812")),
    (
        r"type-?c|usb_?c",
        (
            "Connector:USB_C_Receptacle_USB2.0_16P",
            "Connector:USB_C_Receptacle_USB2.0_14P",
        ),
    ),
    (
        r"jack|trrs|pj-?3",
        (
            "Connector_Audio:AudioJack4",
            "Connector_Audio:AudioJack3",
            "Connector_Audio:AudioJack2",
        ),
    ),
    (r"crystal|xtal", ("Device:Crystal_GND24", "Device:Crystal")),
    (r"\bdin\b|din-?5|midi", ("Connector:DIN-5_180degree", "Connector:DIN-5")),
    (
        r"micro.?sd|\btf\b|sd.?card",
        ("Connector:Micro_SD_Card", "Connector:Micro_SD_Card_Det1"),
    ),
    (r"pot|potentiometer|fader|slide", ("Device:R_Potentiometer",)),
    (r"^led", ("Device:LED",)),
    (r"^(con_|conn|jst|header)", ()),
)


def _pair_footprints(pieces, seen, skipped, fps, kisyms) -> int:
    used = {p["footprint"] for p in pieces}
    n = 0
    for fid, (f, src) in list(fps.where.items()):
        if src["name"] == "kicad" or fid in used:
            continue
        stem = fid.split(":", 1)[1].lower()
        for rx, syms in PAIRS:
            if not re.search(rx, stem):
                continue
            pads, model, _ = fps.info(fid)
            if not model:
                break
            num = {p for p in pads if p[:1].isdigit()}
            cands = list(syms)
            if not syms and num and all(p.isdigit() for p in num):
                # a plain connector: Conn_01xNN with NN = its numbered pads
                cands = [f"Connector_Generic:Conn_01x{len(num):02d}"]
            for lid in cands:
                s = kisyms.get(lid)
                if not s:
                    continue
                pins = set(s["pins"])
                if pins <= pads and not (num - pins):
                    lib, name = lid.split(":", 1)
                    before = len(pieces)
                    _add(pieces, seen, skipped, fps, src, lib, name, s, fid)
                    if len(pieces) > before:
                        pieces[-1]["provenance"]["symbol"] = (
                            f"KiCad official {lid} ({KICAD_LICENCE})"
                        )
                        n += 1
                    break
            break
    return n


def build() -> dict[str, Any]:
    t0 = time.time()
    sources = _sources()
    fps = Footprints(sources)
    t_fp = time.time() - t0
    pieces: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped = {"no pins": 0, "no footprint": 0, "duplicate": 0}
    n_sym = 0
    kisyms: dict[str, dict[str, Any]] = {}
    for src in sources:
        for sf in src["symbols"]:
            lib = sf.stem
            if lib in SKIP_LIBS or lib.endswith("_IEEE"):
                continue
            for name, s in _symbols(sf).items():
                n_sym += 1
                if src["name"] == "kicad":
                    kisyms[f"{lib}:{name}"] = s
                pins = s["pins"]
                if (
                    not pins
                    or all(k == "power_in" for k in s["kinds"].values())
                    and len(pins) == 1
                ):
                    skipped["no pins"] += 1
                    continue
                props = s["props"]
                fid = props.get("Footprint", "").strip()
                if fid and fid not in fps.where:
                    # a vendor symbol names its own library nickname differently: try the same footprint name
                    alt = [
                        w for w in fps.where if w.split(":", 1)[1] == fid.split(":")[-1]
                    ]
                    fid = alt[0] if alt else ""
                fids = [fid] if fid else []
                if not fid or not fps.info(fid)[1]:
                    # a generic symbol (R, Conn_01x04, SW_Push, AudioJack3...) gets every exact footprint with a model
                    k = VARIANTS if not fid else 1
                    fids += [
                        f
                        for f in fps.match(props.get("ki_fp_filters", ""), set(pins), k)
                        if f not in fids
                    ]
                if not fids:
                    skipped["no footprint"] += 1
                    continue
                for fid in fids:
                    _add(pieces, seen, skipped, fps, src, lib, name, s, fid)
    paired = _pair_footprints(pieces, seen, skipped, fps, kisyms)
    with_model = sum(1 for p in pieces if p["model"])
    data = {
        "version": VERSION,
        "built_s": round(time.time() - t0, 2),
        "footprint_scan_s": round(t_fp, 2),
        "stats": {
            "sources": [
                s["name"] for s in sources if not s["name"].startswith("cache:")
            ]
            + [
                f"cache ({sum(1 for s in sources if s['name'].startswith('cache:'))} libraries)"
            ],
            "symbols_read": n_sym,
            "footprints": len(fps.where),
            "pieces": len(pieces),
            "footprint_only_paired": paired,
            "with_3d_model": with_model,
            "skipped": skipped,
        },
        "pieces": pieces,
    }
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    paths: dict[str, dict[str, str]] = {"symbols": {}, "footprints": {}}
    for src in sources:
        if src["name"].startswith("extra:"):
            for sf in src["symbols"]:
                paths["symbols"].setdefault(sf.stem, str(sf))
            for lib, root in src["fp_roots"].items():
                paths["footprints"].setdefault(lib, str(root))
    LIB_PATHS.write_text(json.dumps(paths, indent=1))
    load.cache_clear()
    return data


def _stale() -> bool:
    if not OUT.exists():
        return True
    try:
        with open(OUT) as f:
            head = f.read(40)
        if f'"version":{VERSION},' not in head:
            return True
    except OSError:
        return True
    return False


@functools.lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    """The library, plus a token index over name, footprint, description and keywords. Builds it when missing."""
    from etchlib.parts import read_index

    data = build() if _stale() else read_index(OUT)
    # the curated catalog's pieces win over the same key here (they carry the hand-checked SPICE mappings)
    from . import kicad_catalog

    cur = {p["key"]: p for p in kicad_catalog.load()["pieces"]}
    pieces = [cur.get(p["key"], p) for p in data["pieces"]]
    have = {p["key"] for p in pieces}
    pieces += [p for k, p in cur.items() if k not in have]
    by_key = {p["key"]: p for p in pieces}
    inv: dict[str, set[int]] = {}
    by_fp: dict[str, list[int]] = {}
    for i, p in enumerate(pieces):
        name = p["lib_id"].split(":", 1)[1].lower()
        fpn = p["footprint"].split(":", 1)[-1].lower()
        words = set(
            _tok(
                f"{p['lib_id']} {fpn} {p.get('descr', '')} {p.get('kw', '')} {p['family']}"
            )
        )
        words.add(name)
        words.add(_compact(name))
        words |= {w for w in re.split(r"[_\-/]", name) if w}
        for w in words:
            inv.setdefault(w, set()).add(i)
        by_fp.setdefault(_compact(fpn), []).append(i)
    return {
        **data,
        "pieces": pieces,
        "by_key": by_key,
        "inv": inv,
        "vocab": sorted(inv),
        "by_fp": by_fp,
    }


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ---------------------------------------------------------------- lookup


def _usable(p: dict[str, Any]) -> bool:
    return bool(p.get("model") and p.get("pads_ok") and p.get("spice"))


def _prefixed(L: dict[str, Any], t: str, cap: int = 60) -> set[int]:
    """Pieces with an index word that starts with t ("w25q128" finds W25Q128JVS, "ec11" finds the EC11E footprint)."""
    import bisect

    vocab, inv = L["vocab"], L["inv"]
    out: set[int] = set()
    j = bisect.bisect_left(vocab, t)
    while j < len(vocab) and vocab[j].startswith(t) and len(out) < 4000:
        out |= inv[vocab[j]]
        j += 1
        cap -= 1
        if cap <= 0:
            break
    return out


def search(query: str, k: int = 8, usable: bool = True) -> list[dict[str, Any]]:
    """Top-k pieces for a free-text query ("SSD1306 OLED", "1N4148 SOD-123", "WS2812B"). A part number that is a
    symbol name scores far above words that only appear in a description. Exact, deterministic, no model call."""
    L = load()
    pieces, inv = L["pieces"], L["inv"]
    q = query.lower()
    for a, b in SYNONYMS.items():
        if re.search(rf"\b{re.escape(a)}\b", q):
            q += " " + b
    toks = [t for t in _tok(q) if len(t) >= 2 and t not in _STOP]
    # "MPU-6050", "USBLC6-2SC6" and "6.35 mm" also as one word
    toks += [
        _compact(m)
        for m in re.findall(r"[a-z]+[\-_ ]?\d[\w\-]*", q)
        if _compact(m) not in toks
    ]
    if not toks:
        return []
    score: dict[int, float] = {}
    for t in dict.fromkeys(toks):
        pn = bool(re.search(r"\d", t) and re.search(r"[a-z]", t)) and len(t) >= 4
        hits = inv.get(t, set())
        if pn and len(hits) < 5:
            hits = hits | _prefixed(L, t)
        if not hits or len(hits) > 6000:
            continue
        w = 1.0 / (1.0 + len(hits) ** 0.5)
        strong = pn or (len(t) >= 6 and len(hits) < 30)
        for i in hits:
            score[i] = score.get(i, 0.0) + w * (6.0 if strong else 1.0)
    # a plain word that names the symbol or its family ("crystal", "relay", "speaker") outranks description words
    for t in toks:
        if not re.search(r"\d", t):
            for i in list(score):
                p = pieces[i]
                if (
                    _compact(p["lib_id"].split(":", 1)[1]) == t
                    or t in p["family"].split()
                ):
                    score[i] += 3
    # a symbol name that IS one of the query's part numbers (or starts with it) wins outright
    for t in toks:
        if len(t) >= 4 and re.search(r"\d", t):
            for i in list(score):
                nm = _compact(pieces[i]["lib_id"].split(":", 1)[1])
                if nm == t:
                    score[i] += 20
                elif nm.startswith(t) or (t.startswith(nm) and len(nm) >= 5):
                    score[i] += 8
    ranked = sorted(
        score, key=lambda i: (-score[i], not _usable(pieces[i]), pieces[i]["key"])
    )
    out = []
    for i in ranked:
        p = pieces[i]
        if usable and not _usable(p):
            continue
        out.append(p)
        if len(out) >= k:
            break
    return out


_STOP = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "with",
    "for",
    "to",
    "of",
    "on",
    "in",
    "that",
    "my",
    "me",
    "it",
    "is",
    "make",
    "build",
    "board",
    "small",
    "some",
    "when",
    "shows",
    "show",
    "can",
    "i",
    "want",
    "like",
    "using",
    "use",
    "pcb",
    "circuit",
    "module",
    "chip",
    "part",
    "parts",
    "kicad",
    "lib",
}

# words in a part name that are a part number: "DIOD1N4148" -> 1N4148, "SSD1306_OLED" -> SSD1306
_PN = re.compile(
    r"(?:[A-Z]{1,6}-?\d{1,6}[A-Z0-9\-]*|\d[A-Z]{1,3}\d{2,6}[A-Z0-9\-]*)", re.I
)
_CONN = re.compile(
    r"(?:conn\w*|(?:pin)?\s*header|socket)[\s_:]*0?(\d{1,2})\s*x\s*0?(\d{1,2})", re.I
)


def _generic_conn(text: str) -> dict[str, Any] | None:
    """Conn_01x02, "Conn 01x04", "pin header 1x6", "2x5 header" -> the local KiCad Connector_Generic piece on a
    2.54 mm pin header (JST or screw terminal when the text says so). Never leaves the machine."""
    m = _CONN.search(text) or re.search(
        r"\b0?(\d{1,2})\s*x\s*0?(\d{1,2})\b.*\b(?:header|conn|connector|socket)",
        text,
        re.I,
    )
    if not m:
        return None
    r, c = int(m.group(1)), int(m.group(2))
    if r > c:
        r, c = c, r
    sym = f"Conn_{r:02d}x{c:02d}" if r == 1 else f"Conn_{r:02d}x{c:02d}_Odd_Even"
    L = load()
    cands = [
        p
        for p in L["pieces"]
        if p["lib_id"] == f"Connector_Generic:{sym}" and _usable(p)
    ]
    if not cands:
        return None
    t = text.lower()
    want = (
        "JST_PH"
        if "ph" in re.findall(r"[a-z]+", t)
        else "JST_XH"
        if "xh" in t
        else "TerminalBlock"
        if "screw" in t or "terminal" in t
        else "PinSocket"
        if "socket" in t
        else "PinHeader"
    )
    pref = [
        p
        for p in cands
        if want in p["footprint"]
        and "Vertical" in p["footprint"]
        and "SMD" not in p["footprint"]
    ]
    pref = pref or [p for p in cands if want in p["footprint"]] or cands
    return sorted(
        pref, key=lambda p: ("P2.54mm" not in p["footprint"], len(p["footprint"]))
    )[0]


def resolve(text: str) -> dict[str, Any] | None:
    """One usable piece for a part the picker or an ask named ("1N4148", "DIOD1N4148", "Conn_01x02", "Conn 01x02",
    "CP_Radial_D5.0mm", "SSD1306 OLED"), or None. Part numbers inside a longer token are found too."""
    hit = _generic_conn(text)
    if hit:
        return hit
    L = load()
    # a footprint name ("CP_Radial_D5.0mm", "R_0805_2012Metric"): the generic Device symbol on it
    fp = _compact(text.split(":")[-1].split("@")[-1])
    if len(fp) >= 6:
        keys = [f for f in L["by_fp"] if f.startswith(fp)]
        if keys:
            idx = [i for f in sorted(keys, key=len)[:5] for i in L["by_fp"][f]]
            ok = [L["pieces"][i] for i in idx if _usable(L["pieces"][i])]
            if ok:
                return sorted(
                    ok,
                    key=lambda p: (
                        not p["lib_id"].startswith("Device:"),
                        len(p["lib_id"]),
                    ),
                )[0]
    t = text.replace("_", " ").replace("@", " ")
    cands = []
    for m in _PN.finditer(t.upper()):
        s = m.group(0)
        cands.append(s)
        # a vendor prefix glued to the number: DIOD1N4148 -> 1N4148
        for k in range(1, min(6, len(s) - 3)):
            if s[k].isdigit():
                cands.append(s[k:])
                break
    for c in dict.fromkeys(cands):
        cc = _compact(c)
        if len(cc) < 4:
            continue
        got = search(c, 1)
        if got and _compact(got[0]["lib_id"].split(":", 1)[1]).startswith(cc[:6]):
            return got[0]
    return None


def stats() -> dict[str, Any]:
    L = load()
    fam: dict[str, int] = {}
    for p in L["pieces"]:
        fam[p["family"]] = fam.get(p["family"], 0) + 1
    return {
        **L["stats"],
        "built_s": L["built_s"],
        "usable": sum(1 for p in L["pieces"] if _usable(p)),
        "families": len(fam),
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "--build":
        t = time.time()
        load()
        t1 = time.time()
        for p in search(" ".join(sys.argv[1:]), 8):
            print(p["key"], "|", p["family"], "|", p.get("descr", "")[:60])
        print(
            f"load {t1 - t:.2f}s, search {(time.time() - t1) * 1000:.1f} ms",
            file=sys.stderr,
        )
    else:
        d = build()
        print(json.dumps(d["stats"], indent=1), d["built_s"], "s", file=sys.stderr)

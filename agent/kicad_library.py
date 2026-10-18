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

from etchlib.kicad import FOOTPRINT_DIR, MODEL_DIR, PARTS_CACHE, SYMBOL_DIR, VENDOR_LIBS, _extra_paths, footprint_root

from . import config
from .piece_tags import tags_for

OUT = config.ENGINE / "parts/kicad_library.json"
# where the build finds the symbol / footprint libraries that are not KiCad's own (read by etchlib.kicad)
LIB_PATHS = config.ENGINE / "parts/lib_paths.json"
VERSION = 8  # 8: Device families by whole prefix token (encoder, crystal, resonator). 7: tags + fp_ok on every piece. bump when the record format changes: load() rebuilds an older file


def _extra_dir() -> Path:
    """engine/parts/extra_libs (fetched by engine/parts/fetch_extra_libs.py; only MANIFEST.json is committed).
    A worktree or deploy copy without the fetched folders reads the main checkout's."""
    if os.environ.get("ETCH_EXTRA_LIBS"):
        return Path(os.environ["ETCH_EXTRA_LIBS"])
    for d in (
        config.ENGINE / "parts/extra_libs",
        Path.home() / "helloworld/pcb-loop/engine/parts/extra_libs",
    ):
        if d.is_dir() and any(c.is_dir() for c in d.iterdir()):
            return d
    return config.ENGINE / "parts/extra_libs"


EXTRA = _extra_dir()
# GPL libraries are listed in the manifest with their flag but not ingested unless asked for (ETCH_ALLOW_GPL=1)
ALLOW_GPL = os.environ.get("ETCH_ALLOW_GPL") == "1"

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
    "usb-c": "usb_c_receptacle_usb2.0_16p",
    "type-c": "usb_c_receptacle_usb2.0_16p",
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
    man = Path(os.environ.get("ETCH_EXTRA_MANIFEST", str(EXTRA / "MANIFEST.json")))
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
            if re.search(r"\bA?GPL", f"{lic} {flag}") and not ALLOW_GPL:
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
                        **{
                            k: e.get(k)
                            for k in ("url", "version", "commit", "repo", "tag")
                            if e.get(k)
                        },
                        "licence_flag": flag,
                        # CC-BY / CC-BY-SA ask for credit wherever the part ends up (the export's attribution list)
                        "attribution": f"{e.get('name') or root.name} ({e.get('url', '')}"
                        + (f" @ {str(e.get('commit'))[:12]}" if e.get("commit") else "")
                        + f"), {lic}",
                        "attribution_required": bool(re.search(r"CC-?BY", lic, re.I)),
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
        self._tok: dict[str, list[str]] = {}
        self._buckets: dict[str, list[str]] | None = None
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
        # a maker-prefixed name ("InvenSense_QFN-24_4x4mm_P0.5mm"): the generic package with the same body and pitch
        pk = next(
            (i for i, w in enumerate(parts) if re.match(r"^[A-Za-z]+-\d+", w)), None
        )
        size = next(
            (w for w in parts if re.fullmatch(r"\d+(\.\d+)?x\d+(\.\d+)?mm", w)), None
        )
        if not pk or not size:
            return None
        pkg = parts[pk].split("-1EP")[0].lower()
        for c in self.dir_maps[d]:
            cs = Path(c).stem
            if (
                cs.lower().startswith(pkg)
                and f"_{size}_" in f"_{cs}_"
                and f"_{parts[pitch]}" in cs
            ):
                return c
        return None

    def _lines(self, flt: str) -> str:
        """The part of the blob a filter token can match: a token that starts with two literal characters only
        matches lines whose name (or whole 'Lib:Name', since '*' also crosses the colon) starts with them, so it
        scans that bucket in blob order instead of all ~18k lines."""
        head = flt[:2]
        if len(head) < 2 or any(c in head for c in "*?[") or ":" in flt:
            return self.blob
        if self._buckets is None:
            b: dict[str, list[str]] = {}
            for line in self.blob.split("\n"):
                for key in {line[:2].lower(), line.split(":", 1)[-1][:2].lower()}:
                    b.setdefault(key, []).append(line)
            self._buckets = b
        return "\n".join(self._buckets.get(head.lower(), ()))

    def match(self, filters: str, pins: set[str], k: int = 1) -> list[str]:
        """Footprints the symbol's ki_fp_filters allow whose numbered pads are exactly its pins (extra unnumbered
        mounting pads are fine), ones with a STEP model first, in filter order; up to k of them. When nothing fits
        exactly, the first footprint whose pads cover the pins."""
        if not filters.strip():
            return []
        if filters not in self.filters:
            # each filter token is a regex over the whole ~18k-footprint blob, and tokens like "R_*" or "C_*" recur
            # across hundreds of symbols: cache per token, and keep the list already in hobby order
            found: list[str] = []
            for flt in filters.split():
                hit = self._tok.get(flt)
                if hit is None:
                    tr = fnmatch.translate(flt)
                    rx = tr[4:-3] if tr.startswith("(?s:") else flt
                    pat = re.compile(r"^(?:[^:\n]+:)?(" + rx + r")$", re.M | re.I)
                    hit = self._tok[flt] = [m.group(0) for m in pat.finditer(self._lines(flt))]
                found += hit
            self.filters[filters] = sorted(dict.fromkeys(found), key=_hobby_rank)
        exact, cover = [], []
        for fid in self.filters[filters][:400]:
            pads, model, _ = self.info(fid)
            if pins and not pins <= pads:
                continue
            extra = {p for p in pads - pins if p[:1].isdigit()}
            (exact if not extra else cover).append((not model, fid))
        exact.sort(key=lambda t: (t[0], _hobby_rank(t[1])))
        cover.sort(key=lambda t: (t[0], _hobby_rank(t[1])))
        return (
            [f for m, f in exact if not m][:k]
            or [f for _, f in exact[:1]]
            or [f for _, f in cover[:1]]
        )


_HOBBY = (
    re.compile(r"(PinHeader|PinSocket)_\d+x\d+_P2\.54mm_Vertical$"),
    re.compile(r"JST_(PH|XH)_B\d+B-\w+-K_1x\d+_P\d\.\d+mm_Vertical$"),
    re.compile(r"TerminalBlock_Phoenix_MKDS-1,5-\d+-5\.08_1x\d+_P5\.08mm_Horizontal$"),
)


@functools.lru_cache(maxsize=None)
def _hobby_rank(fid: str) -> int:
    """Generic symbols keep the footprints people solder first: a 2.54 mm pin header or socket, JST PH/XH,
    a 5.08 mm screw terminal, then any other through-hole footprint, then the rest (so Conn_01x08 always has
    its PinSocket_1x08 and Conn_01x40 its PinHeader_1x40 among the kept variants)."""
    name = fid.split(":")[-1]
    for i, rx in enumerate(_HOBBY):
        if rx.search(name):
            return i
    return 3 if "SMD" not in name and "SMT" not in name else 4


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
            ("DIAC", "diode"),
            ("D", "diode"),
            ("Q", "transistor"),
            ("BATTERY", "battery"),
            ("MICROPHONE", "microphone"),
            ("ROTARYENCODER", "rotary encoder"),
            ("ROTARY_ENCODER", "rotary encoder"),
        ):
            # one- and two-letter prefixes are whole name tokens (R, R_Small, CP_Small), so RotaryEncoder,
            # Resonator and Crystal are not read as a resistor or a capacitor (QA 10-17: an EC11 got an R card)
            if n == pre or n.startswith(pre + "_") or len(pre) > 2 and n.startswith(pre):
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
    pieces[-1]["tags"] = tags_for(pieces[-1])


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


def _fp_ok(fid: str) -> bool:
    """True when the footprint file this piece names exists where the pipeline will look for it."""
    lib, _, name = fid.partition(":")
    return bool(lib and name) and (footprint_root(lib) / f"{lib}.pretty" / f"{name}.kicad_mod").is_file()


_PACKAGE_TAGS = frozenset({"tht", "smd"})


def _function_tags(p: dict[str, Any]) -> list[str]:
    """A piece's tags that say what it does (package tags only say how it is soldered)."""
    return [t for t in p.get("tags", ()) if t not in _PACKAGE_TAGS]


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
    by_name: dict[str, str] = {}  # footprint name -> first library id with it (was a scan of every footprint per symbol)
    for w in fps.where:
        by_name.setdefault(w.split(":", 1)[1], w)
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
                    fid = by_name.get(fid.split(":")[-1], "")
                fids = [fid] if fid else []
                if not fid or not fps.info(fid)[1]:
                    # a generic symbol (R, Conn_01x04, SW_Push, AudioJack3...) gets every exact footprint with a model
                    k = VARIANTS if not fid else 1
                    # a specific part (it names its own footprint) only takes an alternate of the same package
                    # ("SOIC-8_3.9x4.9mm..."); never a different jack or module that happens to have the same pads
                    pkg = "_".join(fid.split(":")[-1].split("_")[:2]) if fid else ""
                    fids += [
                        f
                        for f in fps.match(props.get("ki_fp_filters", ""), set(pins), k)
                        if f not in fids
                        and (not pkg or f.split(":")[-1].startswith(pkg))
                    ]
                if not fids:
                    skipped["no footprint"] += 1
                    continue
                for fid in fids:
                    _add(pieces, seen, skipped, fps, src, lib, name, s, fid)
    paired = _pair_footprints(pieces, seen, skipped, fps, kisyms)
    with_model = sum(1 for p in pieces if p["model"])
    # the footprint paths the pipeline will open (etchlib.kicad.footprint_root reads lib_paths.json): write them
    # first, then record per piece whether its footprint file really resolves there
    paths: dict[str, dict[str, str]] = {"symbols": {}, "footprints": {}}
    for src in sources:
        if src["name"].startswith("extra:"):
            for sf in src["symbols"]:
                paths["symbols"].setdefault(sf.stem, str(sf))
            for lib, root in src["fp_roots"].items():
                paths["footprints"].setdefault(lib, str(root))
    tmp = LIB_PATHS.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(paths, indent=1))
    tmp.replace(LIB_PATHS)
    _extra_paths.cache_clear()
    for p in pieces:
        p["fp_ok"] = _fp_ok(p["footprint"])
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
            "footprint_resolves": sum(1 for p in pieces if p["fp_ok"]),
            "tagged": sum(1 for p in pieces if _function_tags(p)),
            "tags": len({t for p in pieces for t in p["tags"]}),
            "skipped": skipped,
        },
        "pieces": pieces,
    }
    # atomic: two jobs that both find the library stale must never read a half-written file
    tmp = OUT.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    tmp.replace(OUT)
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
    for p in pieces:
        # curated pieces come from kicad_catalog without the build's tags and footprint check
        if "tags" not in p:
            p["tags"] = tags_for(p)
        if "fp_ok" not in p:
            p["fp_ok"] = _fp_ok(p["footprint"])
    by_key = {p["key"]: p for p in pieces}
    inv: dict[str, set[int]] = {}
    by_fp: dict[str, list[int]] = {}
    by_name: dict[str, list[int]] = {}
    for i, p in enumerate(pieces):
        by_name.setdefault(_compact(p["lib_id"].split(":", 1)[1]), []).append(i)
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
    by_tag = _tag_index(pieces)
    return {
        **data,
        "pieces": pieces,
        "by_tag": by_tag,
        "by_key": by_key,
        "inv": inv,
        "vocab": sorted(inv),
        "by_fp": by_fp,
        "by_name": by_name,
    }


def _tag_index(pieces: list[dict[str, Any]]) -> dict[str, list[str]]:
    """tag -> piece keys, best first: usable pieces (3D model, pads match, SPICE, footprint resolves), then pieces
    whose library or family says the tag (a relay in Relay before a relay driver IC), then routable sizes (never
    01005/0402/WLP/BGA first), then hobby footprints (_hobby_rank: header, JST, terminal), then through-hole before
    SMD, then by key so the order never changes between runs."""
    from .piece_tags import primary_tags

    rows: dict[str, list[tuple[int, int, int, str]]] = {}
    for p in pieces:
        prim = primary_tags(p)
        use = 0 if _usable(p) else 1
        # hand-solderable first: 01005/0201/0402, WLP and BGA parts do not route on a hobby board
        tiny = 1 if _TINY.search(p["footprint"]) else 0
        pkg = 0 if "tht" in p["tags"] else 2 if "smd" in p["tags"] else 1
        hob = _hobby_rank(p["footprint"])
        for t in p["tags"]:
            rows.setdefault(t, []).append((use, 0 if t in prim else 1, tiny, hob, pkg, p["key"]))
    return {t: [r[-1] for r in sorted(v)] for t, v in sorted(rows.items())}


def tagged(tag: str, k: int = 20, usable: bool = True) -> list[dict[str, Any]]:
    """The best k pieces carrying this tag (see _tag_index for the order)."""
    L = load()
    out = []
    for key in L["by_tag"].get(tag, ()):
        p = L["by_key"][key]
        if usable and not _usable(p):
            break  # usable pieces sort first: the rest are not
        out.append(p)
        if len(out) >= k:
            break
    return out


def tag_stats() -> dict[str, Any]:
    L = load()
    n = len(L["pieces"])
    tagged_n = sum(1 for p in L["pieces"] if _function_tags(p))
    return {
        "pieces": n,
        "tagged": tagged_n,
        "coverage": round(tagged_n / n, 4) if n else 0.0,
        "tags": len(L["by_tag"]),
        "fp_ok": sum(1 for p in L["pieces"] if p.get("fp_ok")),
        "usable": sum(1 for p in L["pieces"] if _usable(p)),
    }


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ---------------------------------------------------------------- lookup


def _usable(p: dict[str, Any]) -> bool:
    return bool(p.get("model") and p.get("pads_ok") and p.get("spice") and p.get("fp_ok"))


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


# common names that mean one specific library piece. Every target is a real KiCad or ingested-library part; the
# tests check each one resolves offline with a model. A module people buy as a breakout (OLED, TFT, HC-SR04,
# SHT31, MAX30102) is the KiCad pin socket it plugs into, named by its pin count, never a drawn module.
ALIASES: tuple[tuple[str, str, str], ...] = (
    (
        r"\b(cherry )?mx\b.*\b(switch|hot ?swap|key)|\bkey ?switch|\bmechanical (key )?switch",
        "Switch:SW_Push",
        "MX-Hotswap-1U",
    ),
    (
        r"\bchoc\b|\bkailh low ?profile",
        "Switch:SW_Push",
        "SW_choc_v2_HS_CPG135001S30_1u",
    ),
    (
        r"\b1n4148\b.*\bsod-?123\b|\bsod-?123\b.*\b1n4148\b",
        "Diode:1N4148W",
        "D_SOD-123",
    ),
    (
        r"\busb[ -]?c\b|\btype[ -]?c\b",
        "Connector:USB_C_Receptacle_USB2.0_16P",
        "GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
    ),
    (r"\btrrs\b", "SparkFun-Connector:Audio_Jack_TRRS_3.5mm_SMD", ""),
    (
        r"\b(3\.5 ?mm|headphone|earphone|audio) (audio )?jack\b|\bheadphone (out|output)\b",
        "Connector_Audio:AudioJack3",
        "CUI_SJ1-3523N",
    ),
    (
        r"\bspeaker( connector| header| terminal)?\b",
        "Connector_Generic:Conn_01x02",
        "JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical",
    ),
    (
        r"\b(lipo|li-?ion|battery) (connector|jack|plug)|\bjst[ -]?ph\b",
        "Connector_Generic:Conn_01x02",
        "JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical",
    ),
    (
        r"\bst7789\b|\btft\b|\bips (display|screen)\b",
        "Connector_Generic:Conn_01x08",
        "PinSocket_1x08_P2.54mm_Vertical",
    ),
    (
        r"\bssd1306\b|\boled\b",
        "Connector_Generic:Conn_01x06",
        "PinSocket_1x06_P2.54mm_Vertical",
    ),
    (
        r"\bhc-?sr04\b|\bultrasonic\b",
        "Connector_Generic:Conn_01x04",
        "PinSocket_1x04_P2.54mm_Vertical",
    ),
    (
        r"\bsht3[01]\b|\bmax3010[12]\b|\bgy-\w+\b",
        "Connector_Generic:Conn_01x04",
        "PinSocket_1x04_P2.54mm_Vertical",
    ),
    (
        r"\bdht22\b|\bam2302\b",
        "Connector_Generic:Conn_01x03",
        "PinSocket_1x03_P2.54mm_Vertical",
    ),
    (
        r"\bsoil\b.*\b(probe|sensor)\b|\bmoisture (probe|sensor)\b",
        "Connector_Generic:Conn_01x03",
        "PinHeader_1x03_P2.54mm_Vertical",
    ),
    (r"\bservo\b", "Connector_Generic:Conn_01x03", "PinHeader_1x03_P2.54mm_Vertical"),
    (
        r"\bneopixel ring\b|\bled ring\b|\bled strip (connector|header)\b",
        "Connector_Generic:Conn_01x03",
        "JST_PH_B3B-PH-K_1x03_P2.00mm_Vertical",
    ),
    (
        r"\b(ir|infrared) receiver\b|\btsop38",
        "Interface_Optical:TSOP382xx",
        "Vishay_MINICAST-3Pin",
    ),
    (
        r"\bntc\b|\bthermistor\b",
        "Device:Thermistor_NTC",
        "R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical",
    ),
    (
        r"\b(ldr|photoresistor|light dependent resistor)\b",
        "Device:R_Photo",
        "R_LDR_5.1x4.3mm_P3.4mm_Vertical",
    ),
    (r"\brelay\b", "Relay:SANYOU_SRD_Form_C", ""),
    (
        r"\b(slide )?fader\b|\bslide pot\w*",
        "Connector_Generic:Conn_01x03",
        "PinHeader_1x03_P2.54mm_Vertical",
    ),
    (
        r"\b(pot|potentiometer|knob)\b",
        "Device:R_Potentiometer",
        "Potentiometer_Bourns_3386W_Horizontal",
    ),
    (
        r"\b(tactile|push|reset|boot|nav\w*) ?(button|switch)\b|\bbutton\b",
        "Switch:SW_Push",
        "SW_PUSH_6mm",
    ),
    (
        r"\b(rotary )?encoder\b|\bec11\b",
        "Device:RotaryEncoder_Switch",
        "ROT_Alps_EC11E-Switch",
    ),
    (
        r"\bmicro ?sd\b|\bsd card\b|\btf card\b",
        "Connector:Micro_SD_Card_Det1",
        "Hirose_DM3AT-SF-PEJM5",
    ),
    (r"\b(12 ?mhz )?crystal\b", "Device:Crystal", "HC49-U_Vertical"),
    (r"\bbuzzer\b|\bbeeper\b|\bpiezo\b", "Device:Buzzer", "Buzzer_12x9.5RM7.6"),
    (
        r"\b(\d+(\.\d+)?k? ?ohm|\d+(\.\d+)?k) resistor\b|\bresistor\b",
        "Device:R",
        "R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical",
    ),
    (
        r"\bpro ?micro\b|\bnice!?\s?nano\b|\belite-?c\b",
        "Connector_Generic:Conn_01x12",
        "PinSocket_1x12_P2.54mm_Vertical",
    ),
)


def _alias(text: str) -> dict[str, Any] | None:
    t = text.lower().replace("_", " ")
    by_key = load()["by_key"]
    for rx, lid, fp in ALIASES:
        if re.search(rx, t):
            hits = [
                p
                for p in by_key.values()
                if p["lib_id"] == lid and fp in p["footprint"] and _usable(p)
            ]
            if hits:
                # a footprint whose name starts with the wanted one beats a longer name that only contains it
                # ("PinHeader_1x03" must not land on "FanPinHeader_1x03")
                return sorted(
                    hits,
                    key=lambda p: (
                        not p["footprint"].split(":")[-1].startswith(fp),
                        len(p["footprint"]),
                        p["key"],
                    ),
                )[0]
    return None


def resolve(text: str) -> dict[str, Any] | None:
    """One usable piece for a part the picker or an ask named ("1N4148", "DIOD1N4148", "Conn_01x02", "Conn 01x02",
    "CP_Radial_D5.0mm", "SSD1306 OLED"), or None. Part numbers inside a longer token are found too."""
    L = load()
    # an exact piece key ("kicad:Device:LED@LED_D5.0mm") is that piece, footprint included
    if (p := L["by_key"].get(text.strip())) is not None and _usable(p):
        return p
    hit = _generic_conn(text)
    if hit:
        return hit
    # the text IS a symbol name ("RP2040", "TL072", "1N4148W"): that symbol, before any footprint-name match
    exact = [
        L["pieces"][i]
        for i in L["by_name"].get(_compact(text.split(":")[-1].split("@")[0]), [])
        if _usable(L["pieces"][i])
    ]
    if exact:
        return sorted(exact, key=lambda p: (_hobby_rank(p["footprint"]), p["key"]))[0]
    if hit := _alias(text):
        return hit
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
        nm = _compact(got[0]["lib_id"].split(":", 1)[1]) if got else ""
        # the symbol is that part (W25Q128 -> W25Q128JVS) or a variant of it (TP4056 -> TP4056-42-ESOP8);
        # a longer name that only shares the stem (RP2040-Zero -> RP2040) is a different part
        if nm and (nm.startswith(cc) or (cc.startswith(nm) and len(cc) - len(nm) <= 2)):
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


# ---------------------------------------------------------------- what an ask needs

# function words in an ask -> the parts that do that job, as search text over the WHOLE library. These only add
# candidates (the picker still chooses); nothing here narrows what any stage can reach.
FUNCTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        r"\bair ?tags?|\btracker|\bbeacon|\bfinder\b|\bble\b|\bbluetooth tag",
        (
            "Raspberry Pi Pico W",
            "ESP32-C3-MINI-1",
            "buzzer",
            "Battery_Cell",
            "push button",
            "kicad:Device:LED@LED_D5.0mm",
        ),
    ),
    (
        r"\bkey ?board|\bmacro ?pad|\bkey ?pad|\bnum ?pad|\b\d+ ?(key|pad)s?\b|\bmechanical\b|\bhot ?swap|\bkeys?\b",
        (
            "mechanical key switch",
            "1N4148",
            "Raspberry Pi Pico",
            "choc switch",
            "EC11 rotary encoder",
            "WS2812B",
        ),
    ),
    (
        r"\bsynth\w*|\bsampler\b|\bdrum machine|\bmidi\b|\bgroove ?box|\binstrument|\bmusic|\bsequencer|\basr-?10\b",
        (
            "Raspberry Pi Pico",
            "PCM5102A",
            "TL072",
            "3.5mm audio jack",
            "potentiometer",
            "6N138",
            "push button",
            "mechanical key switch",
            "PAM8403",
            "W25Q128JVS",
            "rotary encoder",
            "WS2812B",
        ),
    ),
    (
        r"\bguitar|\bpedal|\bfuzz|\boverdrive|\bdistortion|\breverb|\bdelay pedal",
        (
            "TL072",
            "NE5532",
            "3.5mm audio jack",
            "potentiometer",
            "push button",
            "LED",
            "1N4148",
        ),
    ),
    (
        r"\bmp3|\bmp4|\bmusic player|\bmedia player|\bpodcast|\bwalkman|\bipod|\bplay(s|ing)? (music|songs|audio)",
        (
            "micro SD card",
            "PCM5102A",
            "MAX98357A",
            "3.5mm audio jack",
            "Raspberry Pi Pico",
            "push button",
            "TP4056",
            "JST PH battery connector",
            "speaker connector",
            "OLED",
        ),
    ),
    (
        r"\bspeaker|\bamplif|\bamp\b|\bheadphone|\baudio|\bsound|\bplay",
        ("PAM8403", "MAX98357A", "speaker connector", "3.5mm audio jack", "TPA6132"),
    ),
    (r"\bmic\b|\bmicrophone|\bvoice|\bclap|\blisten", ("MAX9814", "microphone")),
    (
        r"\bscreen|\bdisplay|\boled|\blcd|\btft|\bvideo|\bmp4",
        ("SSD1306 OLED", "ST7789 TFT display"),
    ),
    (
        r"\bdrone|\bquadcopter|\bmultirotor|\bfly",
        (
            "AO3400",
            "MPU6050",
            "ESP32-S3",
            "JST PH battery connector",
            "TP4056",
            "1N5819",
            "AP2112K-3.3",
        ),
    ),
    (
        r"\brobot ?arm|\bservo|\bgripper|\bpan.?tilt|\banimatron",
        (
            "servo header",
            "PCA9685",
            "Raspberry Pi Pico",
            "DC barrel jack",
            "electrolytic capacitor",
        ),
    ),
    (
        r"\brobot|\bcar\b|\bwheel|\bmotor|\brover|\btank\b",
        ("DRV8833", "TB6612FNG", "L293D", "screw terminal 2 pin", "HC-SR04"),
    ),
    (
        r"\bweather|\bbarometer|\bpressure|\baltitude",
        ("BME280", "BMP280", "ESP32-WROOM-32E", "SSD1306 OLED"),
    ),
    (
        r"\btemperature|\bthermo|\bhumid|\bclimate",
        ("NTC thermistor", "DHT22", "SHT31", "BME280"),
    ),
    (
        r"\bsoil|\bplant|\bwater(s|ing)?\b|\bgarden|\birrigat|\bpump",
        ("soil moisture probe", "AO3400", "relay", "1N4001", "screw terminal 2 pin"),
    ),
    (
        r"\bheart|\bpulse|\bbpm|\bspo2|\boximeter",
        ("MAX30102", "LDR photoresistor", "LED"),
    ),
    (
        r"\block\b|\bdoor\b|\bsolenoid|\bsmart lock|\bkeyless",
        ("relay", "AO3400", "1N4001", "push button", "buzzer", "ESP32-WROOM-32E"),
    ),
    (
        r"\bwi-?fi|\bbluetooth|\bwireless|\binternet|\bonline|\bphone app|\besp32|\bsmart\b|\biot\b|\bremote\b",
        ("ESP32-WROOM-32E", "ESP32-S3", "ESP32-C3", "AP2112K-3.3", "USB-C receptacle"),
    ),
    (r"\binfrared|\bir\b|\btv remote", ("IR receiver TSOP38238", "IR LED")),
    (
        r"\bmotion|\btilt|\baccel|\bgyro|\bimu\b|\bstep counter|\bpedometer|\bbalanc",
        ("MPU6050",),
    ),
    (r"\bdistance|\bultrasonic|\bparking|\bobstacle|\bradar", ("HC-SR04",)),
    (
        r"\bbattery|\blipo|\bli-?ion|\bportable|\bwearable|\brechargeable|\bcharg",
        ("TP4056", "MCP73831", "JST PH battery connector", "AP2112K-3.3"),
    ),
    (
        r"\busb-?c\b|\busb\b|\btype-?c",
        ("USB-C receptacle", "USBLC6-2SC6", "5.1k resistor"),
    ),
    (
        r"\brgb\b|\bneopixel|\bws2812|\baddressable|\brainbow|\bled strip|\bunderglow",
        ("WS2812B", "SK6812 mini-e", "neopixel ring connector"),
    ),
    (r"\bbuzz|\bbeep|\balarm|\bsiren|\bchime", ("buzzer",)),
    (r"\blight sensor|\bdark|\bnight|\bldr\b|\bbright", ("LDR photoresistor",)),
    (r"\bknob|\bdial|\bvolume|\bencoder", ("rotary encoder", "potentiometer")),
    (
        r"\bclock|\btime\b|\btimer|\balarm clock|\brtc\b",
        ("DS3231", "Crystal 32.768kHz", "SSD1306 OLED"),
    ),
    (r"\bserial|\buart|\bftdi|\bprogrammer|\bch340", ("CH340G",)),
    (r"\bgps\b|\btracker", ("GPS module socket", "ESP32-WROOM-32E")),
    (r"\bcamera|\bphoto booth|\bvision", ("ESP32-S3", "FPC connector")),
)

_CORE = ("Raspberry Pi Pico",)
_TINY = re.compile(r"01005|0201Metric|0402Metric|_0201_|_0402_|WLP-|BGA", re.I)


def shortlist(ask: str, k: int = 40) -> list[dict[str, Any]]:
    """The library pieces an ask most plausibly needs, from every source the library holds (KiCad, the ingested
    extra libraries, fetched cache parts): the parts for each function the ask implies, then the best hit for
    each content word and part number in it. Usable pieces only (footprint pads, 3D model, simulation entry);
    deterministic, no model call, a few milliseconds."""
    a = ask.lower()
    queries: list[str] = []
    for rx, qs in FUNCTIONS:
        if re.search(rx, a):
            queries += qs
    # part numbers and plain nouns from the ask itself ("with an MPU6050", "a TL072 preamp", "a relay")
    queries += [
        m.group(0)
        for m in _PN.finditer(ask)
        if re.search(r"\d", m.group(0)) and len(m.group(0)) >= 4
    ]
    words = [w for w in _tok(a) if len(w) >= 3 and w not in _STOP and not w.isdigit()]
    queries += words
    queries += list(_CORE)
    out: dict[str, dict[str, Any]] = {}
    for q in dict.fromkeys(queries):
        hit = resolve(q)
        if hit is None and q in words:
            got = search(q, 1)
            # a plain word only counts when it names the part or its family, not when it is in some description
            hit = (
                got[0]
                if got
                and (q in got[0]["family"].split() or q in _compact(got[0]["lib_id"]))
                else None
            )
        elif hit is None:
            got = search(q, 1)
            hit = got[0] if got else None
        if hit is not None and _TINY.search(hit["key"]):
            # 01005 / 0201 / 0402 pads do not route at hobby rules (live-1792272486984: "LED" resolved to
            # LED_01005 and the tracker failed routing GND and LED_A twice); take the hobby-sized variant
            L = load()
            same = [
                p
                for p in L["pieces"]
                if p["lib_id"] == hit["lib_id"] and _usable(p) and not _TINY.search(p["key"])
            ]
            hit = min(same, key=lambda p: (_hobby_rank(p["footprint"]), p["key"]), default=None)
        if hit is not None:
            out.setdefault(hit["key"], hit)
        if len(out) >= k:
            break
    return list(out.values())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "--build":
        t = time.time()
        load()
        t1 = time.time()
        for p in (
            shortlist(" ".join(sys.argv[1:]))
            if "--ask" in sys.argv
            else search(" ".join(sys.argv[1:]), 8)
        ):
            print(p["key"], "|", p["family"], "|", p.get("descr", "")[:60])
        print(
            f"load {t1 - t:.2f}s, search {(time.time() - t1) * 1000:.1f} ms",
            file=sys.stderr,
        )
    else:
        d = build()
        print(json.dumps(d["stats"], indent=1), d["built_s"], "s", file=sys.stderr)

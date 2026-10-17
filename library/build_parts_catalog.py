"""Build the board editor's Parts tab catalog.

Reads engine/parts/index.json (the verified parts) and engine/parts/kicad_catalog.json (the KiCad pieces),
groups every part into a beginner family, and writes deploy/etch-app/js/editor/parts-catalog.json.
The image of a part is the 16-hex name of its prerendered footprint in engine/parts/piece_renders (same
rule as agent/piece_render._png), served by the backend at /etch/piece/<hex>.png, or null when none exists. `thumb` is the isolated 3D render
from library/render_parts.py, a static file next to the app (parts/<footprint>.webp), or absent when none exists.

  python3 library/build_parts_catalog.py
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from render_parts import OUT as THUMBS, safe_name

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "engine/parts/index.json"
KICAD = ROOT / "engine/parts/kicad_catalog.json"
RENDERS = ROOT / "engine/parts/piece_renders"
OUT = ROOT / "deploy/etch-app/js/editor/parts-catalog.json"

# Must match agent/piece_render.py (VERSION, VIEW, W, H).
R_VERSION = "v2"
R_VIEW = ("--rotate", "-40,0,25", "--perspective", "--zoom", "0.62", "--quality", "basic")
R_W, R_H = 480, 380

FAMILIES = [
    ("basics", "Basics"),
    ("lights", "LEDs and lights"),
    ("switches", "Buttons and switches"),
    ("chips", "Chips"),
    ("sensors", "Sensors"),
    ("connectors", "Connectors"),
    ("modules", "Modules"),
    ("power", "Power"),
    ("sound", "Sound"),
    ("other", "Other"),
]

# First match wins. Each rule is a regex over a lowercase "family | category | lib_id | footprint | name".
RULES = [
    ("chips", r"interface_usb|ch340"),
    ("sound", r"buzzer|beeper|speaker|\bmic\b|microphone"),
    ("modules", r"rf_module|espressif|mcu_module|wi-fi|module|oled|display"),
    ("sensors", r"sensor|thermistor|ntc|ldr|photo|humidity|bme280|dht"),
    ("lights", r"\bled\b|led:|ws2812|rgb"),
    ("switches", r"switch|sw_push|sw_spdt|button|tact"),
    ("power", r"regulator|ldo|battery|barrel|power|charger|polyfuse|fuse|cr2032"),
    ("connectors", r"connector|conn_|pinheader|jst|terminal|usb|testpoint"),
    ("chips", r"timer|transistor|mosfet|bjt|\bic\b|mcu|74xx|4xxx|op-amp|amplifier|driver|buffer|isolator|interface"),
    ("basics", r"resistor|capacitor|inductor|ferrite|potentiometer|crystal|diode|passive|timing"),
]

NICE = {
    "R": "Resistor",
    "C": "Capacitor",
    "C_Polarized": "Electrolytic capacitor",
    "L": "Inductor",
    "LED": "LED",
    "Buzzer": "Buzzer",
    "Battery_Cell": "Battery holder",
    "R_Potentiometer_Trim": "Trimmer pot",
    "SW_Push": "Push button",
}


def render_hex(footprint: str) -> str | None:
    h = hashlib.sha1(f"{R_VERSION}|{footprint}|{R_VIEW}|{R_W}x{R_H}".encode()).hexdigest()[:16]
    return h if (RENDERS / f"{h}.png").exists() else None


def thumb_of(footprint: str) -> str | None:
    name = safe_name(footprint) + ".webp"
    return f"parts/{name}" if (THUMBS / name).exists() else None


def family_of(*fields: str) -> str:
    hay = " | ".join(f or "" for f in fields).lower()
    for fam, rx in RULES:
        if re.search(rx, hay):
            return fam
    return "other"


def short_pkg(footprint: str) -> str:
    name = footprint.split(":", 1)[-1]
    name = re.sub(r"^(R|C|CP|L|LED|D|SW)_", "", name)
    toks = [t for t in name.split("_") if t][:3]
    return " ".join(toks)


def piece_name(lib_id: str, footprint: str, family: str = "") -> str:
    sym = lib_id.split(":", 1)[-1]
    m = re.match(r"Conn_(\d+)x(\d+)", sym)
    if m and "module" in family.lower():
        return family[:1].upper() + family[1:]
    if m:
        n = int(m.group(1)) * int(m.group(2))
        base = f"{n}-pin connector"
    else:
        base = NICE.get(sym, sym.replace("_", " "))
    pkg = short_pkg(footprint)
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
    if pkg and (squash(pkg) in squash(base) or squash(base).startswith(squash(pkg.split()[0]))):
        pkg = ""
    return f"{base} ({pkg})" if pkg else base


def pin_list(pins) -> list[str]:
    if isinstance(pins, dict):
        return list(pins.keys())
    return [str(p) for p in (pins or [])]


def pin_names(pins) -> dict[str, str] | None:
    if isinstance(pins, dict) and any(pins.values()):
        return {k: v for k, v in pins.items() if v}
    return None


def build() -> dict:
    parts: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for p in json.loads(INDEX.read_text())["parts"]:
        fp = p["footprint"]
        row = {
            "key": p["key"],
            "name": p["name"][:1].upper() + p["name"][1:],
            "family": family_of(p.get("category", ""), p["lib_id"], fp, p["name"]),
            "footprint": fp,
            "pins": pin_list(p.get("pins")),
            "img": render_hex(fp),
        }
        if thumb_of(fp):
            row["thumb"] = thumb_of(fp)
        v = p.get("value")
        if v and v != p["name"] and len(v) <= 16:
            row["value"] = v
        parts.append(row)
        seen.add((p["lib_id"], fp))

    for p in json.loads(KICAD.read_text())["pieces"]:
        fp, lib = p["footprint"], p["lib_id"]
        if (lib, fp) in seen:
            continue
        seen.add((lib, fp))
        row = {
            "key": p["key"],
            "name": piece_name(lib, fp, p.get("family", "")),
            "family": family_of(p.get("family", ""), lib, fp),
            "footprint": fp,
            "pins": pin_list(p.get("pins")),
            "img": render_hex(fp),
        }
        if thumb_of(fp):
            row["thumb"] = thumb_of(fp)
        names = pin_names(p.get("pins"))
        if names:
            row["pinNames"] = names
        parts.append(row)

    order = {fid: i for i, (fid, _) in enumerate(FAMILIES)}
    parts.sort(key=lambda r: order[r["family"]])  # stable: verified parts stay first within a family
    counts = {fid: 0 for fid, _ in FAMILIES}
    for r in parts:
        counts[r["family"]] += 1
    families = [{"id": fid, "label": label, "count": counts[fid]} for fid, label in FAMILIES if counts[fid]]
    return {"families": families, "parts": parts}


def main() -> None:
    cat = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cat, separators=(",", ":"), ensure_ascii=False) + "\n")
    with_img = sum(1 for p in cat["parts"] if p["img"])
    with_thumb = sum(1 for p in cat["parts"] if p.get("thumb"))
    print(f"{len(cat['parts'])} parts ({with_thumb} with 3D thumbs, {with_img} with piece renders) -> {OUT.relative_to(ROOT)}")
    for f in cat["families"]:
        print(f"  {f['label']}: {f['count']}")


if __name__ == "__main__":
    main()

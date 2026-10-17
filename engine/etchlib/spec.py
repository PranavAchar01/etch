"""Load a board spec (YAML), apply `extends` diffs, and resolve parts against the index."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .parts import load_index

DEFAULT_RULES = {"track": 0.3, "clearance": 0.2, "via_d": 0.6, "via_drill": 0.3, "edge_clearance": 0.5}


class SpecError(ValueError):
    pass


@dataclass
class Part:
    ref: str
    key: str
    value: str
    lib_id: str
    footprint: str
    model: str | None
    pins: dict[str, str]
    why: str
    spice: dict[str, Any] | None
    source: str
    at: list[float] | None = None
    edge: str | None = None


@dataclass
class Spec:
    raw: dict[str, Any]
    path: Path
    base: Path | None
    parts: list[Part] = field(default_factory=list)
    added_refs: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.raw["name"]

    @property
    def board(self) -> dict[str, Any]:
        return self.raw["board"]

    @property
    def rules(self) -> dict[str, Any]:
        return {**DEFAULT_RULES, **self.raw.get("rules", {})}

    def nets(self) -> list[str]:
        return sorted({n for p in self.parts for n in p.pins.values()})


def _read(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or "name" not in data:
        raise SpecError(f"{path}: a spec needs at least a name")
    return data


def load_spec(path: str | Path) -> Spec:
    path = Path(path).resolve()
    raw = _read(path)
    base_path = None
    added: list[str] = []
    if "extends" in raw:
        base_path = (path.parent / raw["extends"]).resolve()
        base = load_spec(base_path).raw
        merged = copy.deepcopy(base)
        for k, v in raw.items():
            if k in ("extends", "add_parts", "add_benchmarks", "remove_parts", "add_layout_blocks"):
                continue
            merged[k] = v
        removed = set(raw.get("remove_parts", []))
        merged["parts"] = [p for p in merged["parts"] if p["ref"] not in removed]
        known = {p["ref"] for p in merged["parts"]}
        for p in raw.get("add_parts", []):
            if p["ref"] in known:
                raise SpecError(f"{p['ref']} already exists in {raw['extends']}")
            merged["parts"].append(p)
            added.append(p["ref"])
        merged["benchmarks"] = merged.get("benchmarks", []) + raw.get("add_benchmarks", [])
        if raw.get("add_layout_blocks"):
            # a feature adds its own function block(s) to the parent's row layout
            merged["layout"]["blocks"] = merged["layout"]["blocks"] + raw["add_layout_blocks"]
        merged["extends"] = raw["extends"]
        raw = merged
    for key in ("board", "parts"):
        if key not in raw:
            raise SpecError(f"{path}: missing '{key}'")
    spec = Spec(raw=raw, path=path, base=base_path, added_refs=added)
    spec.parts = [_resolve(p) for p in raw["parts"]]
    refs = [p.ref for p in spec.parts]
    if len(refs) != len(set(refs)):
        raise SpecError("duplicate reference designators")
    return spec


def _resolve(p: dict[str, Any]) -> Part:
    index = load_index()
    entry = index.get(p["part"])
    if entry is None:
        raise SpecError(f"{p['ref']}: part '{p['part']}' is not in parts/index.json (try `etch.py find`)")
    pins = {str(k): str(v) for k, v in p["pins"].items()}
    unknown = set(pins) - set(entry["pins"])
    if unknown:
        raise SpecError(f"{p['ref']}: pins {sorted(unknown)} do not exist on {entry['lib_id']}")
    return Part(
        ref=p["ref"],
        key=p["part"],
        value=str(
            p.get("value") or entry["value"] or entry["name"]
        ),  # never blank: KiCad fills a blank footprint value with the ref
        lib_id=entry["lib_id"],
        footprint=entry["footprint"],
        model=entry.get("model"),
        pins=pins,
        why=p.get("why") or entry.get("why") or entry["name"],
        # a spec may refine the SPICE mapping of one placement, e.g. a connector that feeds a load
        # instead of bringing power in: spice: {role: load, ohms: 71}
        # (or give one where the index has none, e.g. a TO-92 2N3904: {prefix: Q, pins: ['3','2','1'], model: 2N3904})
        spice={**(entry.get("spice") or {}), **p["spice"]} if p.get("spice") else entry.get("spice"),
        source=entry.get("source", "kicad-lib"),
        at=p.get("at"),
        edge=p.get("edge"),
    )


# Prompt to spec. A keyword router over known-good templates. An LLM can replace
# `pick_template` later; the templates stay the source of verified circuits.
TEMPLATES: dict[str, dict[str, Any]] = {}


def _astable_led_template() -> dict[str, Any]:
    here = Path(__file__).resolve().parent.parent / "specs/led_flasher.yaml"
    return _read(here)


TEMPLATES["astable_led"] = {"keywords": ["flash", "blink", "flasher", "led"], "build": _astable_led_template}


def pick_template(prompt: str) -> str:
    words = prompt.lower()
    best, score = None, 0
    for name, t in TEMPLATES.items():
        s = sum(1 for k in t["keywords"] if k in words)
        if s > score:
            best, score = name, s
    if best is None:
        raise SpecError("no template matches that prompt yet; write a spec YAML by hand")
    return best


def spec_from_prompt(prompt: str, name: str | None = None) -> dict[str, Any]:
    tname = pick_template(prompt)
    spec = TEMPLATES[tname]["build"]()
    spec["prompt"] = prompt
    spec["template"] = tname
    if name:
        spec["name"] = name
    color = next((c for c in ("green", "blue", "yellow", "white") if c in prompt.lower()), None)
    if color and tname == "astable_led":
        for p in spec["parts"]:
            if p["part"].startswith("led_red"):
                p["part"] = p["part"].replace("red", color)
                p["value"] = color.upper()
    return spec

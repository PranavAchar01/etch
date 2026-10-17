"""Thin layer over the Etch engine (engine/etchlib): build, gate verdicts, specs of built boards."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

import yaml
from etchlib import sim, symbols
from etchlib.build import _bench, _parse_drc, _parse_erc, build
from etchlib.kicad import cli
from etchlib.parts import load_index
from etchlib.spec import SpecError, load_spec

from . import config

# The gate registry: every benchmark line must name one of these checks.
GATES = {
    "erc_errors": "kicad-cli ERC error count",
    "drc_violations": "kicad-cli DRC violation count",
    "unconnected": "DRC unconnected items",
    "parity": "schematic vs board parity mismatches (DRC parity + netlist compare)",
    "blink_hz": "ngspice: first LED blink rate in Hz",
    "led_peak_ma": "ngspice: first LED peak current mA",
    "duty": "ngspice: first LED on fraction 0..1",
    "led2_peak_ma": "ngspice: second LED peak current mA",
    "alternate": "ngspice: fraction of time both LEDs are lit (lower = alternating)",
    "supply_ma_avg": "ngspice: average current drawn from the first power-in connector, mA",
    "supply_ma_peak": "ngspice: peak supply current, mA",
}


def new_run_dir(prefix: str = "agent") -> Path:
    ms = int(time.time() * 1000)
    while True:  # two runs started in the same millisecond take the next free one (QA round 4)
        d = config.OUT / f"{prefix}-{ms}"
        try:
            d.mkdir(parents=True)
            return d
        except FileExistsError:
            ms += 1


def url(p: Path) -> str:
    return "/" + str(Path(p).resolve().relative_to(config.ROOT))


def board_dir(ref: str | None) -> Path | None:
    """Accept a build dir, an assembly.json path or URL (/engine/out/x/assembly.json)."""
    if not ref:
        return None
    p = Path(ref)
    if (
        not p.exists()
    ):  # a URL path like /engine/out/x/assembly.json is relative to pcb-loop
        p = config.ROOT / str(ref).lstrip("/")
    p = p.resolve()
    if p.is_file():
        p = p.parent
    if config.OUT.resolve() not in p.parents or not (p / "assembly.json").exists():
        raise ValueError(f"not an engine-built board: {ref}")
    return p


def resolved_spec(bdir: Path) -> dict[str, Any]:
    """The full (merged) spec of any engine-built board, from its assembly.json."""
    raw = copy.deepcopy(json.loads((bdir / "assembly.json").read_text())["spec"])
    raw.pop("extends", None)
    return raw


def resolved_spec_from_file(path: Path) -> dict[str, Any]:
    raw = copy.deepcopy(load_spec(path).raw)
    raw.pop("extends", None)
    return raw


def _pin_label(lib_id: str, nums: list[str]) -> str:
    """'1=K,2=A' when the symbol names its pins, else just the numbers."""
    try:
        names = {p.number: p.name for p in symbols.pins(lib_id)}
    except Exception:  # noqa: BLE001  (a library we cannot read still lists its pin numbers)
        names = {}
    out = []
    for n in nums:
        nm = names.get(n, "")
        out.append(f"{n}={nm}" if nm and nm not in ("~", n) else n)
    return ",".join(out[:14]) + (f",...({len(out)})" if len(out) > 14 else "")


def catalog() -> str:
    """Compact parts list for prompts: key | pins (number=name) | simulatable | category | name."""
    rows = []
    for p in load_index().values():
        ps = _pin_label(p["lib_id"], p["pins"])
        role = (p.get("spice") or {}).get("role")
        sim_ok = "sim" if p.get("spice") else "nosim"
        if role:
            sim_ok += f":{role}"
        rows.append(f"{p['key']} | pins {ps} | {sim_ok} | {p['category']} | {p['name']}")
    return "\n".join(rows)


def part_pins(key: str) -> dict[str, Any] | None:
    p = load_index().get(key)
    if not p:
        return None
    return {
        "key": key,
        "lib_id": p["lib_id"],
        "pins": p["pins"],
        "spice": p.get("spice"),
    }


def validate(spec_path: Path) -> str | None:
    try:
        load_spec(spec_path)
    except (SpecError, KeyError, TypeError, ValueError) as e:
        return f"spec error: {e}"
    return None


def verdict(rep: dict[str, Any]) -> dict[str, Any]:
    c = rep["checks"]
    fails = [b for b in rep["benchmarks"] if not b["pass"]]
    ok = not fails and not rep["unrouted"] and c["models"]["missing"] == 0
    return {
        "pass": ok,
        "mode": rep["mode"],
        "seconds": rep["total_s"],
        "erc_errors": c["erc"]["errors"],
        "drc_violations": c["drc"]["violations"],
        "drc_types": c["drc"]["types"],
        "unconnected": c["drc"]["unconnected"],
        "parity": c["drc"]["parity"] + c["netlist"]["mismatches"],
        "models_missing": c["models"]["missing"],
        "unrouted": rep["unrouted"],
        "route_timed_out": bool((rep.get("route") or {}).get("timed_out")),
        "route_s": (rep.get("route") or {}).get("seconds"),
        "sim_ok": c["sim"].get("ok"),
        "sim_error": c["sim"].get("error"),
        "benchmarks": [
            {
                "check": b["check"],
                "value": b["value"],
                "pass": b["pass"],
                "text": b["text"],
            }
            for b in rep["benchmarks"]
        ],
        "failed": [f"{b['text']} (got {b['value']})" for b in fails],
    }


def routed_value(v: dict[str, Any]) -> Any:
    """The "routed" gate's value: the unrouted nets, said plainly when the router ran out of time (its copper so far
    stays on the board) rather than a bare list that reads like the board cannot be routed."""
    if v.get("route_timed_out"):
        return f"router timed out after {v.get('route_s')} s; unrouted: {v['unrouted']}"
    return v["unrouted"] or 0


def run_build(
    spec_path: Path, out: Path, base: Path | None = None, glb: bool = False
) -> dict[str, Any]:
    rep = build(spec_path, out, base_dir=base, glb_export=glb)
    return verdict(rep)


def run_checks(bdir: Path) -> dict[str, Any]:
    """Re-run the gates (ERC, DRC with parity, ngspice) on an existing board without rebuilding it."""
    t0 = time.time()
    raw = resolved_spec(bdir)
    sp = bdir / "spec.resolved.yaml"
    sp.write_text(yaml.safe_dump(raw, sort_keys=False, width=110))
    spec = load_spec(sp)
    pcb = next(bdir.glob("*.kicad_pcb"))
    sch = next(bdir.glob("*.kicad_sch"))
    erc_p, drc_p = bdir / "erc.json", bdir / "drc.json"
    # interactive (a viewer is waiting): no heavy lock, kicad-cli under nice instead
    cli(
        "sch",
        "erc",
        "--format",
        "json",
        "--severity-all",
        "-o",
        str(erc_p),
        str(sch),
    )
    cli(
        "pcb",
        "drc",
        "--format",
        "json",
        "--severity-all",
        "--schematic-parity",
        "-o",
        str(drc_p),
        str(pcb),
    )
    s = sim.simulate(spec, bdir)
    erc, drc = _parse_erc(erc_p), _parse_drc(drc_p)
    rep_path = bdir / "report.json"
    netlist = (
        json.loads(rep_path.read_text())["checks"]["netlist"]
        if rep_path.exists()
        else {"mismatches": 0}
    )
    checks = {
        "erc": erc,
        "drc": drc,
        "netlist": netlist,
        "sim": {k: v for k, v in s.items() if k != "wave"},
    }
    bench = _bench(spec, checks)
    fails = [b for b in bench if not b["pass"]]
    return {
        "pass": not fails,
        "seconds": round(time.time() - t0, 2),
        "erc_errors": erc["errors"],
        "drc_violations": drc["violations"],
        "unconnected": drc["unconnected"],
        "parity": drc["parity"] + netlist.get("mismatches", 0),
        "sim": {
            k: checks["sim"].get(k)
            for k in ("ok", "blink_hz", "led_peak_ma", "duty", "supply_ma_avg", "error")
            if k in checks["sim"]
        },
        "benchmarks": [
            {
                "check": b["check"],
                "value": b["value"],
                "pass": b["pass"],
                "text": b["text"],
            }
            for b in bench
        ],
        "failed": [f"{b['text']} (got {b['value']})" for b in fails],
    }


def spec_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    b = {p["ref"]: p for p in before.get("parts", [])}
    a = {p["ref"]: p for p in after.get("parts", [])}
    nets_b = {n for p in b.values() for n in p["pins"].values()}
    nets_a = {n for p in a.values() for n in p["pins"].values()}
    return {
        "added": [
            f"{r} {a[r]['part']} {a[r].get('value', '')}".strip()
            for r in a
            if r not in b
        ],
        "removed": [r for r in b if r not in a],
        "changed": [r for r in a if r in b and a[r] != b[r]],
        "new_nets": sorted(nets_a - nets_b),
        "board": None
        if before.get("board") == after.get("board")
        else {"from": before.get("board"), "to": after.get("board")},
        "benchmarks_added": [
            x.get("text", x["check"])
            for x in after.get("benchmarks", [])
            if x not in before.get("benchmarks", [])
        ],
    }

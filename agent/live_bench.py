"""Company benchmarks: a spoken list of requirements -> measured pass/fail on the built board.

A model (gpt-5-mini) only MAPS each spoken requirement onto one of the checks below and its limit.
Every value is then measured by plain code from the real build outputs (layout.json, the saved
.kicad_pcb, report.json, sim.json, the gerbers and the export zip). A requirement that maps onto no
check is reported as "cannot measure", never guessed.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from . import llm
from .events import Stream

MODEL = "gpt-5-mini"

# name -> (unit, what it measures)
CHECKS: dict[str, tuple[str, str]] = {
    "drc_violations": ("", "KiCad DRC violations"),
    "erc_errors": ("", "KiCad ERC errors"),
    "unconnected": ("", "unrouted connections left"),
    "parity": ("", "schematic vs board mismatches"),
    "board_width_mm": ("mm", "board width"),
    "board_height_mm": ("mm", "board height"),
    "board_area_mm2": ("mm2", "board area"),
    "copper_layers": ("", "copper layers"),
    "min_track_mm": ("mm", "narrowest track"),
    "power_track_mm": ("mm", "narrowest VCC/GND track"),
    "track_length_mm": ("mm", "total track length"),
    "via_count": ("", "number of vias"),
    "min_via_drill_mm": ("mm", "smallest via drill"),
    "mounting_holes": ("", "mounting holes"),
    "fiducials": ("", "fiducial marks"),
    "part_count": ("", "parts on the board"),
    "models_missing": ("", "parts without a 3D model"),
    "ground_pour": ("", "GND copper pour zones"),
    "gerber_layers": ("", "Gerber files written"),
    "drill_file": ("", "drill file written (1 = yes)"),
    "bom_lines": ("", "BOM lines exported"),
    "build_s": ("s", "place + route + check time"),
    "supply_ma_avg": ("mA", "average supply current (ngspice)"),
    "supply_ma_peak": ("mA", "peak supply current (ngspice)"),
}

MAP_FN = {
    "name": "submit_checks",
    "description": "Map each spoken requirement onto one check and its limit.",
    "parameters": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "said": {
                            "type": "string",
                            "description": "the requirement as spoken",
                        },
                        "check": {
                            "type": "string",
                            "description": "a check name from the list, or NONE",
                        },
                        "min": {"type": "number"},
                        "max": {"type": "number"},
                        "label": {
                            "type": "string",
                            "description": "short plain label, max 28 characters",
                        },
                    },
                    "required": ["said", "check", "label"],
                },
            }
        },
        "required": ["items"],
    },
}


def _pcb(out: Path) -> str:
    return next(out.glob("*.kicad_pcb")).read_text()


def _len(pts: list[list[float]]) -> float:
    return sum(
        ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(pts, pts[1:])
    )


def measure(
    out: Path, verdict: dict[str, Any], export: dict[str, Any] | None
) -> dict[str, float]:
    """Every value in CHECKS, measured from the files the build wrote."""
    lay = json.loads((out / "layout.json").read_text())
    rep = json.loads((out / "report.json").read_text())
    pcb = _pcb(out)
    tracks = lay["tracks"]
    power = [t["width"] for t in tracks if t["net"] in ("VCC", "GND")]
    drills = [
        float(d)
        for d in re.findall(
            r"\(via\s*\(at [^)]*\)\s*\(size [^)]*\)\s*\(drill ([0-9.]+)\)", pcb
        )
    ]
    fps = re.findall(r'\n\s*\(footprint "([^"]+)"', pcb)
    zones = re.findall(r'\n\s*\(zone\s*\(net "([^"]*)"', pcb) or re.findall(
        r'\(zone[\s\S]{0,120}?\(net_name "([^"]*)"', pcb
    )
    gerb = sorted((out / "gerbers").glob("*")) if (out / "gerbers").exists() else []
    sim: dict[str, Any] = {}
    try:
        sim = json.loads((out / "sim.json").read_text())
    except (OSError, ValueError):
        pass
    w, h = float(lay["board"]["w"]), float(lay["board"]["h"])
    vals: dict[str, float] = {
        "drc_violations": verdict["drc_violations"],
        "erc_errors": verdict["erc_errors"],
        "unconnected": verdict["unconnected"],
        "parity": verdict["parity"],
        "board_width_mm": w,
        "board_height_mm": h,
        "board_area_mm2": round(w * h, 1),
        "copper_layers": len(set(re.findall(r'\(\d+ "(\w\.Cu)" signal\)', pcb))) or 2,
        "min_track_mm": min((t["width"] for t in tracks), default=0.0),
        "power_track_mm": min(power, default=0.0),
        "track_length_mm": round(sum(_len(t["pts"]) for t in tracks), 1),
        "via_count": len(drills),
        "min_via_drill_mm": min(drills, default=0.0),
        "mounting_holes": sum(1 for f in fps if f.startswith("MountingHole:")),
        "fiducials": sum(1 for f in fps if f.startswith("Fiducial:")),
        "part_count": len(lay["parts"]),
        "models_missing": verdict["models_missing"],
        "ground_pour": sum(1 for z in zones if z == "GND"),
        "gerber_layers": sum(1 for f in gerb if f.suffix != ".drl"),
        "drill_file": int(any(f.suffix == ".drl" for f in gerb)),
        "bom_lines": (export or {}).get("bom_lines", 0),
        "build_s": round(float(rep["total_s"]), 2),
    }
    for (
        k,
        v,
    ) in (
        sim.items()
    ):  # every numeric ngspice metric (dark.BZ1.avg_ma, supply_ma_avg, ...)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            vals[k] = round(float(v), 3)
    return vals


def run(
    stream: Stream,
    said: str,
    out: Path,
    verdict: dict[str, Any],
    export: dict[str, Any] | None = None,
) -> dict[str, Any]:
    t0 = time.time()
    vals = measure(out, verdict, export)
    t_measure = time.time() - t0
    sim_names = sorted(k for k in vals if k not in CHECKS)
    # which part each simulated ref is (J2 = the fan's connector), so "the fan gets 30 mA" maps onto J2.avg_ma
    legend = []
    try:
        refs = {k.split(".")[-2] for k in sim_names if "." in k}
        for p in json.loads((out / "design.json").read_text())["parts"]:
            if p["ref"] in refs:
                legend.append(f"{p['ref']} = {p.get('why') or p['piece'].split('@')[0].split(':')[-1]}")
    except (OSError, ValueError, KeyError):
        pass
    msgs = [
        {
            "role": "system",
            "content": "You map a company's board requirements onto measurable checks. Use ONLY these check "
            "names or the simulation metric names listed after them (or NONE if nothing measures it; never "
            "stretch a check to fit):\n"
            + "\n".join(f"- {k} ({u or 'count'}): {d}" for k, (u, d) in CHECKS.items())
            + "\nSimulation metrics on this board: "
            + ", ".join(sim_names[:80])
            + ("\nWhat those parts are: " + "; ".join(legend) if legend else "")
            + "\nA requirement about one part (the fan, an LED, the buzzer) uses that part's own metric "
            "(REF.avg_ma, from the list above), never the whole-board supply current."
            + "\nUse min for 'at least', max for 'at most / under / no more than'. Zero-tolerance items "
            "(no DRC errors) are max 0. Units: mm, mA, seconds.",
        },
        {"role": "user", "content": f"Requirements: {said}"},
    ]
    t1 = time.time()
    ans, _ = llm.call_json(stream, "benchmarks", MODEL, msgs, MAP_FN, effort="low")
    t_map = time.time() - t1
    rows = []
    for it in ans.get("items", []):
        chk = it.get("check", "NONE")
        lo, hi = it.get("min"), it.get("max")
        if chk not in vals:
            row = {**it, "value": None, "pass": None, "result": "cannot measure"}
        else:
            v = vals[chk]
            ok = (lo is None or v >= lo) and (hi is None or v <= hi)
            unit = CHECKS.get(chk, ("mA" if chk.endswith("_ma") else "", ""))[0]
            row = {
                **it,
                "value": v,
                "unit": unit,
                "pass": ok,
                "result": "pass" if ok else "fail",
            }
        rows.append(row)
        stream.emit("benchmarks", "check", **row)
    res = {
        "rows": rows,
        "passed": sum(1 for r in rows if r["pass"] is True),
        "failed": sum(1 for r in rows if r["pass"] is False),
        "unmeasured": sum(1 for r in rows if r["pass"] is None),
        "measure_s": round(t_measure, 3),
        "map_s": round(t_map, 2),
        "seconds": round(time.time() - t0, 2),
    }
    stream.emit("benchmarks", "done", **{k: v for k, v in res.items() if k != "rows"})
    (out / "company_benchmarks.json").write_text(json.dumps(res, indent=1))
    return res

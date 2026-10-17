"""The build pipeline: spec -> schematic -> placed and routed board -> checks -> exports."""

from __future__ import annotations

import concurrent.futures as cf
import json
import re
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import glb, schematic, sim
from .kicad import ENGINE_DIR, FOOTPRINT_DIR, cli, footprint_root, kicad_python
from .spec import Spec, load_spec


class Timer:
    def __init__(self) -> None:
        self.t0 = time.time()
        self.marks: dict[str, float] = {}
        self._last = self.t0

    def mark(self, name: str) -> None:
        now = time.time()
        self.marks[name] = round(now - self._last, 3)
        self._last = now

    def total(self) -> float:
        return round(time.time() - self.t0, 3)


LAYOUT_FULL_TIMEOUT_S = 420
LAYOUT_INCREMENTAL_TIMEOUT_S = 300
# the router stops this long before kicad_python would kill layout.py, so the copper it has is saved and the
# report says "router timed out" instead of the whole build crashing with nothing (QA 10-17)
ROUTE_BUDGET_MARGIN_S = 60


def _base_state(spec: Spec, base_dir: Path | None) -> tuple[dict[str, Any] | None, str]:
    """Placements and copper we can keep from a previous build of the base spec."""
    if base_dir is None:
        return None, "full"
    lay = base_dir / "layout.json"
    if not lay.exists():
        raise FileNotFoundError(f"{lay} not found; build the base spec first")
    base = json.loads(lay.read_text())
    if not spec.raw.get("layout") and base["board"] != {k: spec.board[k] for k in ("w", "h")}:
        return None, "full (board size changed)"
    new_pins = {(p.ref, num): net for p in spec.parts for num, net in p.pins.items()}
    fps = {p.ref: p.footprint for p in spec.parts}
    base_pins = {(pd["ref"], pd["num"]): pd["net"] for pd in base["pads"] if pd["net"]}
    broken = {net for key, net in base_pins.items() if new_pins.get(key) != net}
    broken |= {pd["net"] for pd in base["pads"] if fps.get(pd["ref"]) != base["footprints"].get(pd["ref"])}
    keep_parts = {r: v for r, v in base["parts"].items() if r in fps and fps[r] == base["footprints"].get(r)}
    keep = {
        "layout": {**base["board"], **{k: base.get(k) for k in ("holes", "title_box", "slots")}}
        if spec.raw.get("layout")
        else None,
        "parts": keep_parts,
        "tracks": [t for t in base["tracks"] if t["net"] not in broken],
        "vias": [v for v in base["vias"] if v["net"] not in broken],
    }
    mode = "incremental" if not broken else f"incremental (rerouted nets: {', '.join(sorted(broken))})"
    return keep, mode


def _parse_erc(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    vio = [v for s in data.get("sheets", []) for v in s.get("violations", [])]
    return {
        "errors": sum(v["severity"] == "error" for v in vio),
        "warnings": sum(v["severity"] == "warning" for v in vio),
        "types": sorted({v["type"] for v in vio}),
    }


def _parse_drc(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    vio = data.get("violations", [])
    errs = [v for v in vio if v["severity"] == "error"]
    warns = [v for v in vio if v["severity"] == "warning"]
    return {
        "violations": len(errs),
        "warnings": len(warns),
        "unconnected": len(data.get("unconnected_items", [])),
        # unused pins get an "unconnected-(...)" net in the schematic and none on the board; same filter as _netlist_parity
        "parity": sum("unconnected-(" not in v.get("description", "") for v in data.get("schematic_parity", [])),
        "types": sorted({v["type"] for v in vio}),
    }


def _netlist_parity(xml_path: Path, pads: list[dict[str, Any]]) -> dict[str, Any]:
    """Independent check: the schematic netlist (kicad-cli export) equals the board's pad nets."""
    root = ET.parse(xml_path).getroot()
    sch = {}
    for net in root.iter("net"):
        name = net.get("name", "").lstrip("/")
        for node in net.iter("node"):
            sch[(node.get("ref"), node.get("pin"))] = name
    pcb = {(p["ref"], p["num"]): p["net"] for p in pads if p["net"]}
    sch = {k: v for k, v in sch.items() if not v.startswith("unconnected-")}
    diff = sorted({k for k in set(sch) | set(pcb) if sch.get(k) != pcb.get(k)})
    return {"mismatches": len(diff), "examples": [list(d) for d in diff[:5]], "pins_checked": len(pcb)}


def _bench(spec: Spec, checks: dict[str, Any]) -> list[dict[str, Any]]:
    """The spec's benchmarks measured, plus the simulation benchmarks code derives from the circuit itself.

    A benchmark that names a metric this board's simulation does not produce (a model-written "OLED.avg_ma" on a
    part that is not a modelled load, "dark.while.BUZ1.avg_ma", any simulation metric on a board with nothing to
    simulate) can never be met by repairing the circuit: it is reported as not measurable (pass None) instead of
    failed (QA 10-17: 67 % of failed model-written benchmarks read None or ~0, and the repair loop fixed 3 of 103).
    A real simulation error (ok False) still fails them, so the error reaches the repair step."""
    sim_res = checks["sim"]
    gates = {
        "drc_violations": checks["drc"]["violations"],
        "unconnected": checks["drc"]["unconnected"],
        "erc_errors": checks["erc"]["errors"],
        "parity": checks["drc"]["parity"] + checks["netlist"]["mismatches"],
    }
    flat = dict(gates)
    flat.update({k: v for k, v in sim_res.items() if isinstance(v, (int, float)) and not isinstance(v, bool)})
    sim_ran = bool(sim_res.get("ok"))
    out = []
    for b in spec.raw.get("benchmarks", []):
        val = flat.get(b["check"])
        row = {"check": b["check"], "text": b.get("text", b["check"]), "value": val}
        if val is None and sim_ran and b["check"] not in gates:
            why = sim_res.get("note") if sim_res.get("simulated") is False else "this board's simulation has no such metric"
            row.update(pass_=None, status="not measurable")
            row["text"] += f" (not measurable: {why})"
        else:
            row["pass_"] = bool(val is not None and b.get("min", -1e18) <= val <= b.get("max", 1e18))
        row["pass"] = row.pop("pass_")
        out.append(row)
    return out + _sim_bench(sim_res)


def _sim_bench(sim_res: dict[str, Any]) -> list[dict[str, Any]]:
    """Benchmarks code writes from the circuit (sim.supply_budget), never from a model: per scenario, the supply
    current inside the band the parts' datasheets allow (MCU running current, regulator quiescent, chip supply
    currents, pull-ups and other resistors, loads, LEDs); and every LED under its absolute maximum current."""
    if not sim_res.get("ok") or sim_res.get("simulated") is False:
        return []
    rows = []
    budget = sim_res.get("budget") or {}
    multi = len(budget) > 1 or any(n != "default" for n in budget)
    for name, bud in budget.items():
        key = f"{name}.supply_ma_avg" if multi else "supply_ma_avg"
        val = sim_res.get(key)
        lo, hi = float(bud["floor_ma"]), float(bud["ceiling_ma"])
        text = f"supply current inside the parts' datasheet budget, {lo:g} to {hi:g} mA" + (f" ({name})" if multi else "")
        rows.append({"check": key, "text": text, "value": val, "pass": val is not None and lo <= val <= hi, "by": "code"})
    if sim_res.get("led_peak_ma_max") is not None:
        from .sim import LED_ABS_MAX_MA

        val = sim_res["led_peak_ma_max"]
        rows.append(
            {
                "check": "led_peak_ma_max",
                "text": f"every LED under its {LED_ABS_MAX_MA:g} mA absolute maximum",
                "value": val,
                "pass": val <= LED_ABS_MAX_MA,
                "by": "code",
            }
        )
    return rows


def _prune_dead_vias(pcb: Path, report: Path) -> int:
    """Remove vias that DRC calls dangling or unconnected after the fill (stitching vias only ever
    land in the pour, so a dead one is one the copper could not reach). Returns how many."""
    rep = json.loads(report.read_text())
    dead = set()
    for v in rep.get("violations", []) + rep.get("unconnected_items", []):
        if v.get("type") in ("via_dangling", "unconnected_items"):
            for it in v.get("items", []):
                if it.get("description", "").lower().startswith("via"):
                    dead.add(it.get("uuid"))
    if not dead:
        return 0
    text = pcb.read_text()
    blocks = re.findall(r"\t\(via\n(?:\t\t.*\n)+?\t\)\n", text)
    n = 0
    for b in blocks:
        m = re.search(r'\(uuid "([^"]+)"\)', b)
        if m and m.group(1) in dead:
            text = text.replace(b, "", 1)
            n += 1
    pcb.write_text(text)
    return n


def _solid_starved_pads(pcb: Path, report: Path) -> int:
    """A through-hole GND pad whose thermal relief the fill could only reach with one spoke
    (tight TO-92 pitch between tracks) gets a solid zone connection instead, the usual fix."""
    rep = json.loads(report.read_text())
    pads = {
        it["uuid"]
        for v in rep.get("violations", [])
        if v.get("type") == "starved_thermal"
        for it in v.get("items", [])
        if "pad" in it.get("description", "").lower()
    }
    text = pcb.read_text()
    n = 0
    for u in pads:
        key = f'\t\t\t(uuid "{u}")\n'
        if key in text and "(zone_connect" not in text[text.index(key) - 300 : text.index(key)]:
            text = text.replace(key, "\t\t\t(zone_connect 2)\n" + key, 1)
            n += 1
    pcb.write_text(text)
    return n


def build(
    spec_path: str | Path, out_dir: str | Path, base_dir: str | Path | None = None, glb_export: bool = True
) -> dict[str, Any]:
    timer = Timer()
    spec = load_spec(spec_path)
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    name = spec.name
    keep, mode = _base_state(spec, Path(base_dir).resolve() if base_dir else None)
    timer.mark("spec")

    sym_uuids = schematic.write_schematic(spec, out)
    timer.mark("schematic")

    pcb = out / f"{name}.kicad_pcb"
    raw_parts = {q["ref"]: q for q in spec.raw["parts"]}
    job = {
        "out_pcb": str(pcb),
        "fp_root": str(FOOTPRINT_DIR),
        "board": spec.board,
        "rules": spec.rules,
        "title": spec.raw.get("title"),
        "grid": 0.2,
        "route_budget_s": (LAYOUT_INCREMENTAL_TIMEOUT_S if keep else LAYOUT_FULL_TIMEOUT_S) - ROUTE_BUDGET_MARGIN_S,
        "layout": spec.raw.get("layout"),
        "base_layout": keep.get("layout") if keep else None,
        "parts": [
            {
                "ref": p.ref,
                "value": p.value,
                "footprint": p.footprint,
                "fp_root": str(footprint_root(p.footprint.split(":")[0])),
                "model": p.model,
                "pads": p.pins,
                "sym_uuid": sym_uuids[p.ref],
                "edge": p.edge,
                "rot": raw_parts[p.ref].get("rot", 0),
                "at": (
                    [keep["parts"][p.ref][k] for k in ("x", "y", "rot")]
                    if keep and p.ref in keep["parts"]
                    else (None if spec.raw.get("layout") else p.at)
                ),
            }
            for p in spec.parts
        ],
        "keep": {"tracks": keep["tracks"], "vias": keep["vias"]} if keep else None,
    }
    job_path = out / "layout_job.json"
    res_path = out / "layout_result.json"
    job_path.write_text(json.dumps(job, indent=1))
    # a full rebuild of an 18-part board (voice arm) routes ~317-337 s, over the default 300 s (QA round 3/4);
    # incremental builds keep 300 s, full rebuilds get 420 s (the backend's own limit is 15 min)
    kicad_python(
        ENGINE_DIR / "kicad_side/layout.py",
        str(job_path),
        str(res_path),
        timeout=LAYOUT_INCREMENTAL_TIMEOUT_S if keep else LAYOUT_FULL_TIMEOUT_S,
    )
    lay = json.loads(res_path.read_text())
    if spec.raw.get("layout"):
        # the outline was fitted to the parts by the row placer; everything after uses that size
        spec.raw["board"].update(w=lay["board"]["w"], h=lay["board"]["h"])
    timer.mark("place_route")

    if spec.raw.get("finish"):
        # optional: template outline, GND pours, mounting holes and silk, before the checks see the board
        fin = {
            **spec.raw["finish"],
            "w": spec.board["w"],
            "h": spec.board["h"],
            "fp_root": str(footprint_root("MountingHole")),
        }
        if spec.raw.get("layout"):
            fin["holes"] = lay["holes"]
            fin["geometry"] = {
                "pads": lay["pads"],
                "tracks": lay["tracks"],
                "vias": lay["vias"],
                "courts": [v["court"] for v in lay["parts"].values()],
                "title_box": lay.get("title_box"),
            }
        fin_path = out / "finish_job.json"
        fin_path.write_text(json.dumps(fin, indent=1))
        kicad_python(ENGINE_DIR / "kicad_side/finish.py", str(pcb), str(fin_path))
        fpt = out / "fp-lib-table"
        for lib, used in (("MountingHole", fin.get("holes")), ("Fiducial", fin.get("fiducials"))):
            if used and fpt.exists() and f'"{lib}"' not in fpt.read_text():
                # holes and fiducials come from KiCad's own libraries; register them so DRC can check against them
                mh = footprint_root(lib) / f"{lib}.pretty"
                row = f'\t(lib\n\t\t(name "{lib}")\n\t\t(type "KiCad")\n\t\t(uri "{mh}")\n\t\t(options "")\n\t\t(descr "")\n\t)\n'
                fpt.write_text(fpt.read_text().rstrip()[:-1] + row + ")\n")
        if "pour" in fin:
            # fill the pours with KiCad's own filler and save, so DRC, gerbers and renders all see copper
            cli(
                "pcb",
                "drc",
                "--refill-zones",
                "--save-board",
                "--severity-all",
                "--format",
                "json",
                "-o",
                str(out / "fill_drc.json"),
                str(pcb),
            )
            fixed = _solid_starved_pads(pcb, out / "fill_drc.json")
            if fin.get("stitch"):
                fixed += _prune_dead_vias(pcb, out / "fill_drc.json")
            if fixed:
                # solid-connect starved pads, drop stitching vias the fill could not reach, then refill
                cli(
                    "pcb",
                    "drc",
                    "--refill-zones",
                    "--save-board",
                    "--severity-all",
                    "--format",
                    "json",
                    "-o",
                    str(out / "fill_drc.json"),
                    str(pcb),
                )
        timer.mark("finish")

    # checks and fab outputs in parallel
    erc_p, drc_p, net_p = out / "erc.json", out / "drc.json", out / "netlist.xml"
    gerb = out / "gerbers"
    if gerb.exists():
        shutil.rmtree(gerb)
    gerb.mkdir()
    sch_file = out / f"{name}.kicad_sch"

    def fab() -> None:
        cli("pcb", "export", "gerbers", "-o", str(gerb) + "/", str(pcb), nice=True)
        cli("pcb", "export", "drill", "-o", str(gerb) + "/", str(pcb), nice=True)

    # A few seconds of kicad-cli on a small board is not a heavy step (renders keep the heavy lock);
    # waiting here on another agent's render made live builds stall for minutes.
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        f_erc = ex.submit(cli, "sch", "erc", "--format", "json", "--severity-all", "-o", str(erc_p), str(sch_file))
        f_drc = ex.submit(
            cli,
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
        f_net = ex.submit(cli, "sch", "export", "netlist", "--format", "kicadxml", "-o", str(net_p), str(sch_file))
        f_sim = ex.submit(sim.simulate, spec, out)
        f_fab = ex.submit(fab)
        t_par = time.time()
        for f in (f_erc, f_drc, f_net, f_fab):
            f.result()
        sim_res = f_sim.result()
        checks_s = round(time.time() - t_par, 3)
        timer.mark("checks_sim_fab")
        glb_info = {"skipped": True}
        if glb_export:
            reuse = []
            refs = [p.ref for p in spec.parts]
            if keep and base_dir:
                # unchanged parts keep their GLB from the base build (same place, same model)
                for r in refs:
                    src = Path(base_dir).resolve() / "parts" / f"{r}.glb"
                    if r in keep["parts"] and src.exists():
                        (out / "parts").mkdir(exist_ok=True)
                        shutil.copy2(src, out / "parts" / f"{r}.glb")
                        reuse.append(r)
            glb_info = glb.export_all(pcb, out, [r for r in refs if r not in reuse])
            glb_info["reused_from_base"] = reuse
            timer.mark("glb")

    checks = {
        "models": _model_check(pcb),
        "erc": _parse_erc(erc_p),
        "drc": _parse_drc(drc_p),
        "netlist": _netlist_parity(net_p, lay["pads"]),
        "sim": {k: v for k, v in sim_res.items() if k not in ("wave",)},
    }
    (out / "sim.json").write_text(json.dumps(sim_res, indent=1))

    footprints = {p.ref: p.footprint for p in spec.parts}
    (out / "layout.json").write_text(
        json.dumps(
            {
                "board": {"w": spec.board["w"], "h": spec.board["h"]},
                "holes": lay.get("holes", []),
                "title_box": lay.get("title_box"),
                "slots": lay.get("slots", {}),
                "parts": lay["parts"],
                "footprints": footprints,
                "pads": lay["pads"],
                "tracks": lay["tracks"],
                "vias": lay["vias"],
            },
            indent=1,
        )
    )

    frame = None
    if glb_export and (out / "board.glb").exists():
        b = glb.bounds(out / "board.glb")
        frame = {
            "units": "m",
            "up": "+Y (glTF)",
            "bounds": b["min"] + b["max"],
            "note": "kicad-cli GLB: x right, board top face +Y, board y (down) maps to +Z; "
            "origin as measured in bounds; part GLBs and board_bare.glb share this frame",
        }

    assembly = make_assembly(spec, lay, checks, frame, glb_url(out))
    (out / "assembly.json").write_text(json.dumps(assembly, indent=1))
    if spec.added_refs or keep:
        new_steps = [
            s
            for s in assembly["steps"]
            if (s["t"] == "part" and s["ref"] not in (keep or {}).get("parts", {}))
            or (s["t"] in ("trace", "via") and s.get("_new"))
        ]
        diff = {
            "name": name,
            "base": str(spec.raw.get("extends")),
            "mode": mode,
            "steps": new_steps,
            "checks": assembly["checks"],
        }
        (out / "assembly.diff.json").write_text(json.dumps(_strip(diff), indent=1))
    (out / "assembly.json").write_text(json.dumps(_strip(assembly), indent=1))
    timer.mark("write")

    report = {
        "name": name,
        "mode": mode,
        "total_s": timer.total(),
        "stages_s": timer.marks,
        "layout_s": lay["timing_s"],
        "checks_parallel_s": checks_s,
        "glb": glb_info,
        "unrouted": lay["unrouted"],
        "route": lay.get("route") or {},
        "checks": checks,
        "benchmarks": _bench(spec, checks),
        "sizes": _sizes(out),
    }
    (out / "report.json").write_text(json.dumps(report, indent=1))
    for tmp in (job_path,):
        tmp.unlink(missing_ok=True)
    return report


def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def _sizes(out: Path) -> dict[str, int]:
    sizes = {}
    for p in sorted(out.rglob("*")):
        if p.is_file():
            sizes[str(p.relative_to(out))] = p.stat().st_size
    return sizes


def _model_check(pcb: Path) -> dict[str, Any]:
    """Every footprint's 3D model must be an absolute path to a real STEP file (no env vars, no .wrl)."""
    paths = re.findall(r'\(model "([^"]+)"', pcb.read_text())
    bad = sorted({m for m in paths if not (m.lower().endswith((".step", ".stp")) and Path(m).is_file())})
    return {"count": len(paths), "missing": len(bad), "bad": bad}


def glb_url(out: Path) -> str:
    """board.glb as a URL path from the pcb-loop root (the app serves that folder), for example
    /engine/out/led_flasher/board.glb. Falls back to the bare file name outside pcb-loop."""
    root = ENGINE_DIR.parent
    try:
        return "/" + str((out.resolve() / "board.glb").relative_to(root))
    except ValueError:
        return "board.glb"


def make_assembly(
    spec: Spec, lay: dict[str, Any], checks: dict[str, Any], frame: dict[str, Any] | None, board_url: str = "board.glb"
) -> dict:
    W, H = spec.board["w"], spec.board["h"]
    board = {
        "w": W,
        "h": H,
        "thickness": spec.board.get("thickness", 1.6),
        "outline": [[0, 0], [W, 0], [W, H], [0, H]],
        "layers": spec.board.get("layers", 2),
        # finished board from kicad-cli with the real library STEP models; component nodes are
        # named by reference (R1, Q1...) so the app can split it into parts
        "glb": board_url,
        "glb_bare": board_url.replace("board.glb", "board_bare.glb"),
    }
    if frame:
        board["glb_frame"] = frame
    steps: list[dict[str, Any]] = [{"t": "board"}]
    for p in spec.parts:
        pl = lay["parts"][p.ref]
        nets = []
        for num in sorted(p.pins, key=lambda s: (len(s), s)):
            if p.pins[num] not in nets:
                nets.append(p.pins[num])
        steps.append(
            {
                "t": "part",
                "ref": p.ref,
                "value": p.value,
                "lib_id": p.lib_id,
                "footprint": p.footprint,
                "glb": f"parts/{p.ref}.glb",
                "x": pl["x"],
                "y": pl["y"],
                "rot": pl["rot"],
                "side": pl["side"],
                "why": p.why,
                "nets": nets,
                "source": p.source,
            }
        )
    new_keys = {json.dumps(t, sort_keys=True) for t in lay.get("new_tracks", [])}
    new_vkeys = {json.dumps(v, sort_keys=True) for v in lay.get("new_vias", [])}
    for net in sorted({t["net"] for t in lay["tracks"]} | {v["net"] for v in lay["vias"]}):
        for t in lay["tracks"]:
            if t["net"] == net:
                steps.append(
                    {
                        "t": "trace",
                        "net": net,
                        "layer": t["layer"],
                        "width": t["width"],
                        "pts": t["pts"],
                        "_new": json.dumps(t, sort_keys=True) in new_keys,
                    }
                )
        for v in lay["vias"]:
            if v["net"] == net:
                steps.append(
                    {
                        "t": "via",
                        "net": net,
                        "x": v["x"],
                        "y": v["y"],
                        "d": v["d"],
                        "_new": json.dumps(v, sort_keys=True) in new_vkeys,
                    }
                )
    sim_keys = ("blink_hz", "led_peak_ma", "duty", "led2_peak_ma", "alternate", "supply_ma_avg", "engine", "models")
    return {
        "name": spec.name,
        "spec": spec.raw,
        "board": board,
        "steps": steps,
        "checks": {
            "erc": {"errors": checks["erc"]["errors"], "warnings": checks["erc"]["warnings"]},
            "drc": {"violations": checks["drc"]["violations"], "unconnected": checks["drc"]["unconnected"]},
            "sim": {k: checks["sim"][k] for k in sim_keys if k in checks["sim"]},
            "models": {"count": checks["models"]["count"], "missing": checks["models"]["missing"]},
        },
    }

"""Etch board engine CLI.

uv run etch.py build specs/led_flasher.yaml --out out/led_flasher
uv run etch.py build specs/led_flasher_alt.yaml --out out/led_flasher_alt --base out/led_flasher
uv run etch.py prompt "a PCB that flashes a red LED" --spec specs/from_prompt.yaml
uv run etch.py find "red LED 0805"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from etchlib.build import build
from etchlib.parts import find_part
from etchlib.spec import SpecError, spec_from_prompt


def _summary(rep: dict) -> str:
    c = rep["checks"]
    s = c["sim"]
    lines = [
        f"{rep['name']}: {rep['mode']} build in {rep['total_s']} s",
        f"  stages: {rep['stages_s']}",
        (
            f"  ERC errors {c['erc']['errors']} warnings {c['erc']['warnings']} | DRC violations {c['drc']['violations']}"
            f" warnings {c['drc']['warnings']} unconnected {c['drc']['unconnected']} parity {c['drc']['parity']}"
            f" | netlist mismatches {c['netlist']['mismatches']} | 3D models {c['models']['count']}"
            f" ({c['models']['missing']} missing)"
        ),
        (
            f"  sim: blink {s.get('blink_hz')} Hz, LED peak {s.get('led_peak_ma')} mA, duty {s.get('duty')},"
            f" supply avg {s.get('supply_ma_avg')} mA"
        ),
    ]
    for b in rep["benchmarks"]:
        tag = "SKIP" if b["pass"] is None else ("PASS" if b["pass"] else "FAIL")
        lines.append(f"  [{tag}] {b['text']} ({b['value']})")
    if rep["unrouted"]:
        lines.append(f"  UNROUTED nets: {rep['unrouted']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="etch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="spec YAML to KiCad board, checks, GLBs and assembly.json")
    b.add_argument("spec")
    b.add_argument("--out", required=True)
    b.add_argument("--base", help="previous build dir of the spec this one extends (incremental)")
    b.add_argument("--no-glb", action="store_true", help="skip the GLB exports")
    p = sub.add_parser("prompt", help="plain prompt to a spec YAML (template router)")
    p.add_argument("text")
    p.add_argument("--spec", required=True)
    p.add_argument("--name")
    f = sub.add_parser("find", help="look up a part by plain name")
    f.add_argument("name")
    f.add_argument("--lcsc")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "build":
            base = args.base
            rep = build(args.spec, args.out, base_dir=base, glb_export=not args.no_glb)
            print(_summary(rep))
            ok = all(x["pass"] is not False for x in rep["benchmarks"]) and not rep["unrouted"]
            ok = ok and rep["checks"]["models"]["missing"] == 0
            return 0 if ok else 2
        if args.cmd == "prompt":
            spec = spec_from_prompt(args.text, args.name)
            Path(args.spec).write_text(yaml.safe_dump(spec, sort_keys=False, width=110))
            print(f"wrote {args.spec} from template {spec['template']}")
            return 0
        if args.cmd == "find":
            res = find_part(args.name, lcsc_id=args.lcsc)
            if res.get("part"):
                res["part"] = {k: res["part"][k] for k in ("key", "name", "lib_id", "footprint", "model")}
            print(json.dumps(res, indent=1))
            return 0 if res["status"] == "found" else 1
    except SpecError as e:
        print(f"spec error: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""The agent's tools. build_board and add_feature are loops: a sub-agent writes the spec (or the
spec diff), plain code runs the gates (load + ERC + DRC + parity + ngspice + routing + 3D models),
and every failure goes back to the sub-agent until the gates pass or ETCH_MAX_ITERS runs out.
Gates are code, never a model."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import yaml

from . import config, llm, parts_live, provenance, render
from . import engine_api as eng
from .events import Stream

EXAMPLE = (config.ENGINE / "specs/led_flasher.yaml").read_text()

RULES = """Spec rules (the engine rejects anything else):
- Every part's `part` is a key from the CATALOG. `pins` maps that part's pin numbers (from the CATALOG) to net names.
  Unlisted pins are left unconnected (no-connect). Use GND for ground. Keep refs unique (R1, C1, D1, Q1, J1, U1...).
- Exactly one power source must be the first connector with role power_in (for example jst_ph_2, pins 1 = +, 2 = GND)
  if you want simulation. Simulation runs only if EVERY part is marked `sim` in the CATALOG; otherwise every
  simulation benchmark fails. Parts marked `nosim` are fine for boards whose benchmarks are only gate counts.
- Benchmarks: a list of {check, min?, max?, text}. `check` must be one of the GATES. Always include
  erc_errors max 0, drc_violations max 0, unconnected max 0, parity max 0.
  Add simulation benchmarks only for what the board really has: led2_peak_ma and alternate only with two LEDs.
  Give numeric targets honest slack (blink_hz about +-50% of the target), since component values are E-series.
- Pins are listed as number=name. Diodes conduct from A (anode) to K (cathode): a series reverse-polarity
  diode has A on the battery + net and K on the board supply net.
- Pin keys are strings ("1", "2"). Leave `value` out unless the part needs one (resistors, capacitors).
- board: {w, h} in mm, 2 layers. About 40 mm^2 of board per small SMD part, more for connectors and THT;
  if routing fails (unrouted nets) or DRC reports clearance/courtyard problems, make the board bigger.
- Connectors that take a cable should say edge: left (or right/top/bottom).
- Each part has a one-sentence `why` in plain words."""


def _gates_text() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in eng.GATES.items())


def _emit_gates(stream: Stream, agent: str, it: int, v: dict[str, Any]) -> None:
    for name, ok, val in [
        ("ERC", v["erc_errors"] == 0, v["erc_errors"]),
        ("DRC", v["drc_violations"] == 0, v["drc_violations"]),
        ("unconnected", v["unconnected"] == 0, v["unconnected"]),
        ("parity", v["parity"] == 0, v["parity"]),
        ("routed", not v["unrouted"], eng.routed_value(v)),
        ("3D models", v["models_missing"] == 0, v["models_missing"]),
        ("ngspice", bool(v["sim_ok"]), v.get("sim_error") or "ok"),
    ]:
        stream.emit(agent, "gate", iteration=it, gate=name, ok=ok, value=val)
    for b in v["benchmarks"]:
        stream.emit(
            agent,
            "gate",
            iteration=it,
            gate=b["text"],
            check=b["check"],
            ok=b["pass"],
            value=b["value"],
        )


def _feedback(v: dict[str, Any]) -> str:
    lines = ["The gates FAILED on that version:"]
    if v["failed"]:
        lines += [f"- benchmark failed: {f}" for f in v["failed"]]
    if v.get("route_timed_out"):
        lines.append(f"- {eng.routed_value(v)} (the copper it laid is kept)")
    elif v["unrouted"]:
        lines.append(
            f"- unrouted nets (board too small or too crowded): {v['unrouted']}"
        )
    if v["drc_violations"]:
        lines.append(f"- DRC violations {v['drc_violations']}: {v['drc_types']}")
    if v["erc_errors"]:
        lines.append(f"- ERC errors {v['erc_errors']}")
    if v["models_missing"]:
        lines.append("- a 3D model is missing (use parts from the CATALOG only)")
    if v.get("sim_error"):
        lines.append(f"- simulation: {v['sim_error']}")
    lines.append("Fix it and submit the whole corrected YAML again.")
    return "\n".join(lines)


SPEC_FN = {
    "name": "submit_spec",
    "description": "Submit the board spec as YAML text.",
    "parameters": {
        "type": "object",
        "properties": {
            "yaml": {"type": "string", "description": "the full spec YAML"},
            "note": {"type": "string"},
        },
        "required": ["yaml"],
    },
}

FEATURE_FN = {
    "name": "submit_feature",
    "description": "Submit the feature as a YAML diff against the base board.",
    "parameters": {
        "type": "object",
        "properties": {
            "yaml": {
                "type": "string",
                "description": "YAML with add_parts, optional remove_parts, add_benchmarks, optional board",
            },
            "note": {
                "type": "string",
                "description": "one sentence: what changed and why",
            },
        },
        "required": ["yaml"],
    },
}


def _pcb_parts(out: Path) -> dict[str, str]:
    """Read back ref -> footprint name from the saved .kicad_pcb (what really got built)."""
    found: dict[str, str] = {}
    for pcb in out.glob("*.kicad_pcb"):
        for block in re.split(r'\n\s*\(footprint "', pcb.read_text())[1:]:
            fp = block.split('"', 1)[0]
            m = re.search(r'\(property "Reference" "([^"]+)"', block)
            if m:
                found[m.group(1)] = fp.split(":")[-1]
    return found


def _change_kept(
    before: dict[str, Any], intent: dict[str, Any], after: dict[str, Any], out: Path
) -> str | None:
    """None if the change the writer first made for the request is still in the passing build.

    intent = {"added": refs first added, "swapped": {ref: first new part}}. Catches the
    known failure where a repair quietly reverts the requested swap so the gates pass."""
    b = {p["ref"]: p for p in before.get("parts", [])}
    a = {p["ref"]: p for p in after.get("parts", [])}
    built = _pcb_parts(out)
    idx = eng.load_index()
    problems = []
    for ref in intent["added"]:
        if ref not in a:
            problems.append(f"{ref} was added for the request but is gone now")
        elif built and ref not in built:
            problems.append(f"{ref} is in the spec but not on the saved board")
    for ref in intent["swapped"]:
        if ref not in a:
            problems.append(f"{ref} was swapped for the request but is gone now")
            continue
        if a[ref]["part"] == b[ref]["part"]:
            problems.append(
                f"{ref} is back to the original part {b[ref]['part']}: the requested change was reverted"
            )
            continue
        old_fp = (idx.get(b[ref]["part"]) or {}).get("footprint", "")
        new_fp = (idx.get(a[ref]["part"]) or {}).get("footprint", "")
        if built.get(ref) and new_fp and old_fp != new_fp:
            if built[ref] != new_fp.split(":")[-1]:
                problems.append(
                    f"{ref} on the saved board has footprint {built[ref]}, not the new part's {new_fp}"
                )
    if a == b or not (set(a) ^ set(b) or any(a[r] != b[r] for r in a if r in b)):
        problems.append("the board is identical to the base")
    return "; ".join(problems) or None


def _intent(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    b = {p["ref"]: p for p in before.get("parts", [])}
    return {
        "added": [p["ref"] for p in after["parts"] if p["ref"] not in b],
        "swapped": {
            p["ref"]: p["part"]
            for p in after["parts"]
            if p["ref"] in b and p["part"] != b[p["ref"]]["part"]
        },
    }


def _loop(
    stream: Stream,
    agent: str,
    messages: list[dict[str, Any]],
    fn: dict[str, Any],
    make_spec,
    out: Path,
    base: Path | None,
    before: dict[str, Any] | None,
) -> dict[str, Any]:
    """Shared write -> gate -> repair loop."""
    t0 = time.time()
    last_fail = None
    v: dict[str, Any] | None = None
    spec_path = out / "spec.yaml"
    intent: dict[str, Any] | None = None
    for it in range(1, config.MAX_ITERS + 1):
        stream.emit(
            agent, "iteration", iteration=it, max=config.MAX_ITERS, phase="write"
        )
        try:
            ans, _ = llm.call_json(stream, agent, config.SUB_MODEL, messages, fn)
            text = ans.get("yaml") or ""
            messages.append({"role": "assistant", "content": f"```yaml\n{text}\n```"})
            data = yaml.safe_load(text)
            if not isinstance(data, dict):
                raise ValueError("YAML must be a mapping")
            spec = make_spec(data)
        except (
            Exception
        ) as e:  # bad YAML or bad answer is a gate failure like any other
            fb = f"That answer could not be used: {str(e)[:300]}. Submit the whole YAML again."
            stream.emit(
                agent,
                "gate",
                iteration=it,
                gate="spec parses",
                ok=False,
                value=str(e)[:200],
            )
            messages.append({"role": "user", "content": fb})
            continue
        spec_path.write_text(yaml.safe_dump(spec, sort_keys=False, width=110))
        err = eng.validate(spec_path)
        stream.emit(
            agent,
            "gate",
            iteration=it,
            gate="spec loads (parts, pins)",
            ok=err is None,
            value=err or "ok",
        )
        if err:
            messages.append(
                {
                    "role": "user",
                    "content": f"{err}\nCheck part keys and pin numbers against the CATALOG and submit again.",
                }
            )
            continue
        after = eng.resolved_spec_from_file(spec_path)
        if before is not None and intent is None:
            intent = _intent(before, after)  # what the writer first did for the request
        if before is not None:
            stream.emit(
                agent,
                "diff",
                iteration=it,
                **eng.spec_diff(before, after),
                note=ans.get("note"),
            )
        else:
            stream.emit(
                agent,
                "diff",
                iteration=it,
                added=[f"{p['ref']} {p['part']}" for p in after["parts"]],
                board=after.get("board"),
                note=ans.get("note"),
            )
        stream.emit(
            "engine",
            "tool_call",
            tool="etch.build",
            iteration=it,
            incremental=base is not None,
        )
        try:
            v = eng.run_build(spec_path, out, base=base)
        except Exception as e:
            msg = str(e)[-600:]
            stream.emit(
                "engine",
                "tool_result",
                tool="etch.build",
                iteration=it,
                ok=False,
                error=msg,
            )
            messages.append(
                {
                    "role": "user",
                    "content": f"The build crashed: {msg}\nFix the spec and submit again.",
                }
            )
            continue
        stream.emit(
            "engine",
            "tool_result",
            tool="etch.build",
            iteration=it,
            ok=v["pass"],
            seconds=v["seconds"],
            mode=v["mode"],
        )
        _emit_gates(stream, agent, it, v)
        if v["pass"] and intent is not None:
            lost = _change_kept(before, intent, after, out)
            stream.emit(
                agent,
                "gate",
                iteration=it,
                gate="requested change present",
                ok=lost is None,
                value=lost or "ok",
            )
            if lost:
                v = {**v, "pass": False, "failed": [*v["failed"], "requested change present"]}
                messages.append(
                    {
                        "role": "user",
                        "content": f"The gates pass, but the request is no longer met: {lost}. "
                        "Never revert or drop the requested change to make gates pass. Keep the change and fix "
                        "the failure another way; if the new part truly cannot be simulated, say so in note.",
                    }
                )
                stream.emit(
                    agent, "iteration", iteration=it, phase="decide", decision="repair (change reverted)"
                )
                continue
        if v["pass"]:
            stream.emit(
                agent,
                "iteration",
                iteration=it,
                phase="decide",
                decision="done",
                seconds=round(time.time() - t0, 1),
            )
            return {
                "ok": True,
                "iterations": it,
                "gates": v,
                "seconds": round(time.time() - t0, 1),
            }
        fb = _feedback(v)
        sig = json.dumps([v["failed"], v["unrouted"], v["drc_types"]])
        if sig == last_fail:
            fb += "\nThis is the SAME failure as last time: change the approach (different parts, topology or board size)."
            decision = "re-plan"
        else:
            decision = "repair"
        last_fail = sig
        stream.emit(
            agent,
            "iteration",
            iteration=it,
            phase="decide",
            decision=decision,
            failed=v["failed"],
            unrouted=v["unrouted"],
        )
        messages.append({"role": "user", "content": fb})
    stream.emit(
        agent,
        "iteration",
        iteration=config.MAX_ITERS,
        phase="decide",
        decision="stop (out of iterations)",
    )
    return {
        "ok": False,
        "iterations": config.MAX_ITERS,
        "gates": v,
        "seconds": round(time.time() - t0, 1),
    }


def _finish(
    stream: Stream, out: Path, res: dict[str, Any], do_render: bool
) -> dict[str, Any]:
    has_board = (out / "assembly.json").exists()
    if has_board:
        # where every part on the board came from (no part is ever generated)
        idx = eng.load_index()
        rows = []
        for sp in eng.resolved_spec(out)["parts"]:
            e = idx.get(sp["part"]) or {}
            rows.append(
                {
                    "ref": sp["ref"],
                    "part": sp["part"],
                    "name": e.get("name"),
                    **provenance.short(e),
                }
                if e
                else {"ref": sp["ref"], "part": sp["part"], "route": "unknown"}
            )
        res["parts"] = rows
        stream.emit("engine", "parts", board=eng.url(out), parts=rows)
    res.update(
        board=eng.url(out) if has_board else None,
        assembly=eng.url(out / "assembly.json") if has_board else None,
        checks_pass=res["ok"],
    )
    if has_board and do_render:
        r = render.start(out)
        r.pop("_proc", None)
        res["render"] = r
        res["frames"] = r.get("frames")
        stream.emit("render", "render", **r)
    return res


def build_board(
    stream: Stream,
    prompt: str | None = None,
    spec: str | None = None,
    do_render: bool = True,
) -> dict[str, Any]:
    out = eng.new_run_dir("agent")
    stream.emit("spec-writer", "start", prompt=prompt, spec=spec, out=eng.url(out))
    if spec:
        path = config.ENGINE / "specs" / Path(spec).name
        t0 = time.time()
        v = eng.run_build(path, out)
        _emit_gates(stream, "spec-writer", 1, v)
        return _finish(
            stream,
            out,
            {
                "ok": v["pass"],
                "iterations": 1,
                "gates": v,
                "seconds": round(time.time() - t0, 1),
            },
            do_render,
        )
    name = (
        re.sub(r"[^a-z0-9]+", "_", (prompt or "board").lower()).strip("_")[:28]
        or "board"
    )
    messages = [
        {
            "role": "system",
            "content": "You are the spec writer in Etch, an agent loop that designs real KiCad boards. "
            "Write a complete board spec in YAML that meets the request, using only CATALOG parts. Plain code then "
            "places, routes and checks it (ERC, DRC, parity, ngspice) and returns any failures to you.\n\n"
            + RULES,
        },
        {
            "role": "user",
            "content": f"REQUEST: {prompt}\n\nGATES:\n{_gates_text()}\n\nCATALOG (key | pins | sim | category | name):\n"
            f"{eng.catalog()}\n\nWORKED EXAMPLE (a verified board; reuse its circuit if it fits the request):\n{EXAMPLE}\n"
            f"Use name: {name}. Submit with submit_spec.",
        },
    ]

    def make(data: dict[str, Any]) -> dict[str, Any]:
        data["name"] = name
        data.setdefault("prompt", prompt)
        data.setdefault(
            "rules",
            {
                "track": 0.3,
                "clearance": 0.2,
                "via_d": 0.6,
                "via_drill": 0.3,
                "edge_clearance": 0.5,
            },
        )
        for k in ("board", "parts", "benchmarks"):
            if k not in data:
                raise ValueError(f"missing '{k}'")
        return data

    res = _loop(stream, "spec-writer", messages, SPEC_FN, make, out, None, None)
    return _finish(stream, out, res, do_render)


def add_feature(
    stream: Stream, request: str, board: str, do_render: bool = True
) -> dict[str, Any]:
    base = eng.board_dir(board)
    before = eng.resolved_spec(base)
    out = eng.new_run_dir("agent")
    (out / "base.yaml").write_text(yaml.safe_dump(before, sort_keys=False, width=110))
    stream.emit(
        "feature-writer", "start", request=request, base=eng.url(base), out=eng.url(out)
    )
    base_yaml = yaml.safe_dump(
        {
            k: before[k]
            for k in ("name", "power", "board", "parts", "benchmarks")
            if k in before
        },
        sort_keys=False,
        width=110,
    )
    messages = [
        {
            "role": "system",
            "content": "You are the feature writer in Etch, an agent loop that edits real KiCad boards. "
            "Given a base board spec and a request, write ONLY the change as YAML with these keys:\n"
            "  add_parts: [ {ref, part, value?, pins: {pin: net}, edge?, why} ]\n"
            "  remove_parts: [ref, ...]   (to rewire an existing part, remove it and add it back with the same ref)\n"
            "  add_benchmarks: [ {check, min?, max?, text} ]   (check from GATES)\n"
            "  board: {w, h, thickness: 1.6, layers: 2}   (only if more room is needed)\n"
            "Every base benchmark must still pass after the change (regressions fail the gate). New nets need new "
            "names; reuse existing net names (VCC, GND...) to connect to the base circuit. Never revert or drop the "
            "requested change to make gates pass; a gate checks that it is still on the saved board. Plain code builds it "
            "(incremental place and route), runs ERC, DRC, parity and ngspice, and returns failures to you.\n\n"
            + RULES,
        },
        {
            "role": "user",
            "content": f"REQUEST: {request}\n\nBASE BOARD:\n{base_yaml}\nGATES:\n{_gates_text()}\n\n"
            f"CATALOG (key | pins | sim | category | name):\n{eng.catalog()}\n\nSubmit with submit_feature.",
        },
    ]

    def make(data: dict[str, Any]) -> dict[str, Any]:
        if "benchmarks" in data:  # a common slip: keep only the genuinely new ones
            new = [
                b
                for b in data.pop("benchmarks") or []
                if b not in before.get("benchmarks", [])
            ]
            data["add_benchmarks"] = (data.get("add_benchmarks") or []) + new
        allowed = {"add_parts", "remove_parts", "add_benchmarks", "board"}
        bad = set(data) - allowed
        if bad:
            raise ValueError(
                f"unexpected keys {sorted(bad)}; use only {sorted(allowed)}"
            )
        if not data.get("add_parts") and not data.get("remove_parts"):
            raise ValueError("the diff changes nothing")
        spec = {
            "name": f"{before['name'][:24]}_plus",
            "extends": "base.yaml",
            "title": before.get("title"),
            "prompt": request,
            "description": f"{before.get('description', '')} Feature: {request}",
        }
        spec.update({k: v for k, v in data.items() if v})
        return spec

    res = _loop(stream, "feature-writer", messages, FEATURE_FN, make, out, base, before)
    res["base"] = eng.url(base)
    return _finish(stream, out, res, do_render)


def find_part(stream: Stream, query: str) -> dict[str, Any]:
    res = parts_live.find_part(query, stream)
    p = res.get("part") or {}
    slim = {
        k: p.get(k)
        for k in (
            "key",
            "name",
            "lib_id",
            "footprint",
            "model",
            "pins",
            "source",
            "lcsc",
            "mpn",
            "datasheet",
            "category",
        )
        if p.get(k)
    }
    out = {k: v for k, v in res.items() if k not in ("part",)}
    out["part"] = slim or None
    out["ok"] = res["status"] in ("found", "ok")
    if not out["ok"]:
        out["say"] = (
            f"No published part found for '{query}'. Nothing was generated in its place."
        )
    return out


def run_checks(stream: Stream, board: str) -> dict[str, Any]:
    bdir = eng.board_dir(board)
    stream.emit("engine", "tool_call", tool="etch.checks", board=eng.url(bdir))
    v = eng.run_checks(bdir)
    for b in v["benchmarks"]:
        stream.emit(
            "engine",
            "gate",
            gate=b["text"],
            check=b["check"],
            ok=b["pass"],
            value=b["value"],
        )
    v["ok"] = v["pass"]
    v["board"] = eng.url(bdir)
    return v


def render_board(stream: Stream, board: str) -> dict[str, Any]:
    r = render.start(eng.board_dir(board))
    r.pop("_proc", None)
    stream.emit("render", "render", **r)
    return r


LEAD_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "build_board",
            "description": "Design, place, route and verify a NEW board. "
            "A spec-writer sub-agent writes the spec and repairs it until ERC, DRC, parity and ngspice pass.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "what the board must do",
                    },
                    "spec": {
                        "type": "string",
                        "description": "a saved spec file name instead of a prompt",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_feature",
            "description": "Change the CURRENT board (add parts, protection, "
            "connectors, LEDs...). A feature-writer sub-agent edits the spec and loops on gate failures.",
            "parameters": {
                "type": "object",
                "properties": {"request": {"type": "string"}},
                "required": ["request"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_part",
            "description": "Live part lookup: local KiCad library, then LCSC "
            "(easyeda2kicad), then GitHub, then web search (You.com when keyed, else open-source SearXNG). Found parts become usable KiCad parts (footprint + STEP).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "part name, number or LCSC id",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_checks",
            "description": "Re-run ERC, DRC, parity and ngspice on the current board.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render",
            "description": "Render the current board's KiCad frames and explode sprites.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

"""The lead agent: reads the request, calls tools (each heavy tool runs its own sub-agent loop),
tracks the current board, and finishes with a short spoken-style summary."""

from __future__ import annotations

import json
from typing import Any

from . import config, keyboard, llm, tools
from .events import Stream

SYSTEM = """You are the lead agent of Etch, which designs real KiCad circuit boards with a loop of agents.
Act only through tools; never invent numbers, quote only values a tool returned.
- build_board for a new board (pass the user's words as prompt, or spec for a saved spec file).
- add_feature to change the current board. If the request names a specific part that may not be in the
  library, call find_part first, then add_feature.
- find_part for part lookups.
- run_checks to re-verify the current board.
- render only if the user asks for frames again (build_board and add_feature start a render themselves).
Every part is a published file (KiCad libraries, LCSC, GitHub); nothing is ever generated. If find_part
returns not_found, say plainly that no real part was found; never invent one or a stand-in.
If a tool reports ok=false, decide: retry once with a clearer request, or stop and say what failed.
For a published design that exists on GitHub (for example a named keyboard such as a Corne), use the keyboard
tools in this order: find_design, source_parts, check_board; if check_board fails, call refine ONCE and then
check_board again (failures refine could not fix stay reported, never retried); then render_top; then finish.
Call one tool per turn.
Each keyboard tool takes a "say" sentence that is spoken aloud while it runs.
Finish with at most two short plain sentences (under 45 words in all) for a voice to read: what was built and
what still fails, if anything. No file paths, no markdown, no dashes as punctuation."""


def _slim(res: dict[str, Any]) -> dict[str, Any]:
    """What the lead sees of a tool result (keeps its context small)."""
    g = res.get("gates") or {}
    keep = {
        k: res.get(k)
        for k in (
            "ok",
            "status",
            "board",
            "iterations",
            "seconds",
            "source",
            "part",
            "base",
            "provenance",
            "say",
        )
        if res.get(k) is not None
    }
    if g:
        keep["gates"] = {
            "pass": g.get("pass"),
            "failed": g.get("failed"),
            "unrouted": g.get("unrouted"),
            "benchmarks": [
                (b["check"], b["value"], b["pass"]) for b in g.get("benchmarks", [])
            ],
        }
    for k in ("benchmarks", "failed", "tiers"):
        if k in res and k not in keep:
            keep[k] = res[k]
    return keep


def run(stream: Stream, request: str, board: str | None = None) -> dict[str, Any]:
    state = {"board": board}
    stream.emit(
        "lead",
        "start",
        request=request,
        board=board,
        model=config.MODEL,
        sub_model=config.SUB_MODEL,
        base_url=config.BASE_URL,
    )
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": f"Current board: {board or 'none yet'}\nRequest: {request}",
        },
    ]
    results: list[dict[str, Any]] = []
    for _ in range(config.LEAD_MAX_STEPS):
        msg = llm.chat(
            stream, "lead", config.MODEL, msgs, tools=tools.LEAD_TOOLS + keyboard.TOOLS
        )
        msgs.append(msg.model_dump(exclude_none=True))
        if msg.tool_calls and (msg.content or "").strip():
            stream.emit("lead", "say", text=msg.content.strip(), final=False)
        if not msg.tool_calls:
            stream.emit("lead", "say", text=msg.content, final=True)
            out = {
                "ok": all(r.get("ok", True) for r in results) if results else True,
                "say": msg.content,
                "board": state["board"],
                "results": results,
            }
            stream.emit("lead", "done", **out)
            return out
        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            stream.emit("lead", "tool_call", tool=name, args=args)
            if name in keyboard.NAMES and (args.get("say") or "").strip():
                stream.emit("lead", "say", text=args["say"].strip(), final=False, tool=name)
            try:
                if name in keyboard.NAMES:
                    res = keyboard.dispatch(
                        stream, name, args, state.setdefault("kb", {}), request
                    )
                else:
                    res = dispatch(stream, name, args, state)
            except Exception as e:
                res = {"ok": False, "error": str(e)[:400]}
            if res.get("board") and res.get("ok"):
                state["board"] = res["board"]
            stream.emit(
                "lead",
                "tool_result",
                tool=name,
                ok=res.get("ok"),
                board=res.get("board"),
            )
            seen = res if name in keyboard.NAMES else _slim(res)
            results.append({"tool": name, **seen})
            msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(seen, default=str)[:6000],
                }
            )
    out = {
        "ok": False,
        "say": "I ran out of steps before finishing.",
        "board": state["board"],
        "results": results,
    }
    stream.emit("lead", "done", **out)
    return out


def dispatch(
    stream: Stream, name: str, args: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    if name == "build_board":
        return tools.build_board(
            stream, prompt=args.get("prompt"), spec=args.get("spec")
        )
    if name == "add_feature":
        if not state.get("board"):
            return {"ok": False, "error": "no current board; build one first"}
        return tools.add_feature(
            stream, args.get("request") or args.get("feature") or "", state["board"]
        )
    if name == "find_part":
        return tools.find_part(stream, args.get("query") or args.get("name") or "")
    if name == "run_checks":
        if not state.get("board"):
            return {"ok": False, "error": "no current board"}
        return tools.run_checks(stream, state["board"])
    if name == "render":
        if not state.get("board"):
            return {"ok": False, "error": "no current board"}
        return tools.render_board(stream, state["board"])
    return {"ok": False, "error": f"unknown tool {name}"}

"""Etch agent CLI. Prints the step stream as JSON lines; the last line is the `done` event.

  uv run --project engine --with openai python -m agent run "build an LED flasher" [--board /engine/out/x]
  uv run --project engine --with openai python -m agent tool add_feature '{"request": "...", "board": "/engine/out/x"}'
  uv run --project engine --with openai python -m agent tool find_part '{"query": "TP4056"}'

Run from the pcb-loop folder.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import config, lead
from .events import Stream


def main() -> int:
    ap = argparse.ArgumentParser(prog="agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="lead agent on a free-form request")
    r.add_argument("request")
    r.add_argument("--board")
    t = sub.add_parser("tool", help="one tool directly (what the voice tools call)")
    t.add_argument(
        "name",
        choices=["build_board", "add_feature", "find_part", "run_checks", "render"],
    )
    t.add_argument("args", nargs="?", default="{}")
    a = ap.parse_args()

    run_id = f"{a.cmd}-{int(time.time() * 1000)}"
    logs = config.OUT / "agent-logs"
    logs.mkdir(parents=True, exist_ok=True)
    stream = Stream(run_id, log=logs / f"{run_id}.jsonl")
    try:
        if a.cmd == "run":
            out = lead.run(stream, a.request, a.board)
        else:
            args = json.loads(a.args)
            state = {"board": args.pop("board", None) or args.pop("base", None)}
            stream.emit(
                "lead",
                "start",
                tool=a.name,
                args=args,
                model=config.MODEL,
                sub_model=config.SUB_MODEL,
                base_url=config.BASE_URL,
            )
            out = lead.dispatch(stream, a.name, args, state)
            stream.emit("lead", "done", **out)
    except Exception as e:
        stream.emit("lead", "error", error=f"{type(e).__name__}: {str(e)[:500]}")
        return 1
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())

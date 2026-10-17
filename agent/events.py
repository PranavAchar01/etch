"""Step stream: one JSON object per line on stdout (the server relays each line as an SSE event),
mirrored to <run dir>/agent.jsonl so a finished run can be replayed.

Every event has: ts, run, agent (lead | spec-writer | feature-writer | part-scout | engine | render),
type (start | llm | tool_call | tool_result | iteration | gate | diff | part | render | say | done | error).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any


class Stream:
    def __init__(self, run: str, log: Path | None = None, echo: bool = True) -> None:
        self.run = run
        self.t0 = time.time()
        self.log = log
        self.echo = echo
        self.lock = threading.Lock()
        self.events: list[dict[str, Any]] = []

    def set_log(self, log: Path) -> None:
        with self.lock:
            self.log = log
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a") as f:
                for e in self.events:
                    f.write(json.dumps(e) + "\n")

    def emit(self, agent: str, type_: str, **data: Any) -> dict[str, Any]:
        ev = {
            "ts": round(time.time(), 3),
            "t": round(time.time() - self.t0, 2),
            "run": self.run,
            "agent": agent,
            "type": type_,
            **data,
        }
        line = json.dumps(ev, default=str)
        with self.lock:
            self.events.append(ev)
            if self.echo:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            if self.log:
                with self.log.open("a") as f:
                    f.write(line + "\n")
        return ev

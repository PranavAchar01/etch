"""Hard caps on a live run (crash-fidget, 2026-10-17).

Prod job zM-s8mWOeq3rjtE3 ("create a fidget toy") sat on "Picking parts from 5 kept variants" for 15 minutes: the
engine went silent and nothing in the engine itself ever said so. The backend's own 15-minute timer was the only
cap, and it ended with a bare stderr line. This watchdog runs beside every live design/modify run and stops it with
an honest message when either cap trips:

* a stage cap: no new event for STAGE_CAPS_S[agent] seconds, where agent is the agent of the last event (the stage
  the run is in). Caps sit above the longest real silence seen in 164 prod runs (assembly:gate 420 s while routing,
  part-scout:llm 190 s, picker 42 s).
* a run cap: RUN_CAP_S in total (under the backend's 15-minute kill, so the page gets words, not a dead process).

On a trip it emits lead:say (the honest message), lead:error and lead:done ok=False with error "timeout: <stage>",
writes live_result.json, and ends the process with exit code 0 (the run did finish: with a timeout).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .events import Stream

RUN_CAP_S = float(os.environ.get("ETCH_RUN_CAP_S", str(13 * 60)))
DEFAULT_STAGE_CAP_S = 150.0
STAGE_CAPS_S = {
    "assembly": 540.0,  # place, route, check: routing a dense board is silent for up to ~7 min
    "engine": 540.0,
    "render": 360.0,
    "part-scout": 240.0,
    "benchmarks": 240.0,
    "sim": 240.0,
}
STAGE_WORDS = {
    "lead": "starting the build",
    "classifier": "screening the library",
    "filter": "screening the library",
    "pieces": "screening the library",
    "picker": "picking the parts",
    "blocks": "picking the parts",
    "part-scout": "finding a part online",
    "assembly": "placing, routing and checking the board",
    "engine": "placing, routing and checking the board",
    "render": "rendering the board",
    "benchmarks": "measuring the board",
    "sim": "simulating the board",
}


def stage_cap(agent: str) -> float:
    return STAGE_CAPS_S.get(agent, DEFAULT_STAGE_CAP_S)


def message(stage: str, secs: float, total: bool) -> str:
    words = STAGE_WORDS.get(stage, "this step")
    if total:
        return (
            f"I stopped this build after {round(secs / 60)} minutes: it was still {words}, and a build that takes "
            "this long is stuck, not slow. Try again, or describe the board a little "
            "more simply."
        )
    return (
        f"I stopped this build: {words} went {round(secs)} s without any progress. That is the model or the "
        "router hanging, not your description. Try again in a minute."
    )


def stop_children() -> None:
    """SIGTERM this process's own children (router, kicad-cli, ngspice) by parent PID, never by name."""
    import subprocess

    try:
        subprocess.run(["pkill", "-TERM", "-P", str(os.getpid())], timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


class Watchdog:
    """Watches stream.events; trips at most once. exit_fn is os._exit in a real run and a stub in tests."""

    def __init__(
        self,
        stream: Stream,
        out: Path,
        ask: str,
        run_cap_s: float = RUN_CAP_S,
        poll_s: float = 2.0,
        now: Callable[[], float] = time.time,
        exit_fn: Callable[[int], Any] = os._exit,
    ) -> None:
        self.stream, self.out, self.ask = stream, out, ask
        self.run_cap_s, self.poll_s, self.now, self.exit_fn = (
            run_cap_s,
            poll_s,
            now,
            exit_fn,
        )
        self.t0 = now()
        self.tripped: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def last(self) -> tuple[str, float]:
        ev = self.stream.events[-1] if self.stream.events else None
        if ev is None:
            return "lead", self.t0
        return str(ev.get("agent") or "lead"), float(ev.get("ts") or self.t0)

    def finished(self) -> bool:
        return any(
            e.get("agent") == "lead" and e.get("type") == "done"
            for e in self.stream.events[-5:]
        )

    def check(self) -> str | None:
        """One look. Returns the trip reason ("stage:<agent>" or "run") or None."""
        if self.tripped or self.finished():
            return None
        t = self.now()
        agent, ts = self.last()
        if t - self.t0 > self.run_cap_s:
            return self.trip(agent, t - self.t0, total=True)
        if t - ts > stage_cap(agent):
            return self.trip(agent, t - ts, total=False)
        return None

    def trip(self, agent: str, secs: float, total: bool) -> str:
        self.tripped = "run" if total else f"stage:{agent}"
        say = message(agent, secs, total)
        err = f"timeout: {'run' if total else agent} after {round(secs)} s"
        self.stream.emit("lead", "say", text=say)
        self.stream.emit(
            "lead", "error", error=err, stage=agent, seconds=round(secs, 1)
        )
        tm = {"total_s": round(self.now() - self.t0, 2)}
        self.stream.emit(
            "lead", "done", ok=False, iterations=0, timings=tm, classifier={}, error=err
        )
        try:
            res = {
                "ask": self.ask,
                "ok": False,
                "iterations": 0,
                "timings": tm,
                "classifier": {},
                "verdict": None,
                "board": str(self.out),
                "export": None,
                "render": None,
                "error": err,
            }
            (self.out / "live_result.json").write_text(json.dumps(res, indent=1))
        except OSError:
            pass
        if self.exit_fn is os._exit:
            stop_children()
        self.exit_fn(0)
        return self.tripped

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_s):
            if self.check():
                return

    def start(self) -> "Watchdog":
        self._thread = threading.Thread(
            target=self._loop, name="etch-watchdog", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

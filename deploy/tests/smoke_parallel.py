"""Smoke test: start a LOCAL copy of the public backend (never the live one on :3439), fire three real builds at
once, and show that their engine processes overlap in time (no queue).

  python deploy/tests/smoke_parallel.py [--port 3499] [--timeout 900]

Prints one JSON line per sample (health: running, heavy slots in use) and a summary with each job's spawn, first
event and end time, the overlap window, and the final states. Exit 0 when all three engines were running at the
same moment. The server it starts is stopped by its own PID at the end; unfinished runs are cancelled first.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASKS = [
    "a 555 timer led flasher",
    "a usb-c powered white led night light",
    "a 3.3v regulator board with a power led",
]


def call(port: int, path: str, body: dict | None = None, timeout: float = 40) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"http": e.code, **json.loads(e.read() or b"{}")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3499)
    ap.add_argument("--timeout", type=float, default=900)
    a = ap.parse_args()
    if a.port == 3439:
        sys.exit("3439 is the live backend; pick another port")
    env = dict(os.environ, ETCH_PUBLIC_PORT=str(a.port))
    srv = subprocess.Popen(
        [sys.executable, str(ROOT / "deploy/etch_public.py")],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    jobs: dict[str, dict] = {}
    try:
        for _ in range(50):
            try:
                call(a.port, "/etch/health", timeout=2)
                break
            except OSError:
                time.sleep(0.2)
        t0 = time.time()
        posted: list[dict] = []

        def post(text: str) -> None:
            posted.append(
                {
                    "text": text,
                    "t": round(time.time() - t0, 2),
                    **call(a.port, "/etch/live", {"mode": "design", "text": text}),
                }
            )

        ts = [threading.Thread(target=post, args=(t,)) for t in ASKS]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        for p in posted:
            if not p.get("job"):
                print(json.dumps({"post_failed": p}))
                return 1
            jobs[p["job"]] = {
                "text": p["text"],
                "token": p["token"],
                "posted_s": p["t"],
                "next": 0,
                "first_event_s": None,
                "end_s": None,
                "state": None,
                "run": None,
            }
        peak = 0
        while time.time() - t0 < a.timeout and any(
            j["end_s"] is None for j in jobs.values()
        ):
            h = call(a.port, "/etch/health")
            peak = max(peak, h.get("running", 0) + h.get("fast_running", 0))
            print(
                json.dumps(
                    {
                        "t": round(time.time() - t0, 1),
                        "running": h.get("running"),
                        "fast_running": h.get("fast_running"),
                        "queue": h.get("queue"),
                        "heavy_in_use": h.get("heavy_in_use"),
                        "heavy_slots": h.get("heavy_slots"),
                    }
                ),
                flush=True,
            )
            for jid, j in jobs.items():
                if j["end_s"] is not None:
                    continue
                r = call(a.port, f"/etch/job?id={jid}&since={j['next']}")
                if r.get("events") and j["first_event_s"] is None:
                    j["first_event_s"] = round(time.time() - t0, 1)
                j["next"], j["state"], j["run"] = (
                    r.get("next", j["next"]),
                    r.get("state"),
                    r.get("run"),
                )
                if any(e.get("type") == "queued" for e in r.get("events", [])):
                    j["saw_queued_event"] = True
                if r.get("done"):
                    j["end_s"] = round(time.time() - t0, 1)
            time.sleep(5)
        for jid, j in jobs.items():
            if j["end_s"] is None:
                call(a.port, "/etch/cancel", {"job": jid, "token": j["token"]})
                j["state"] = "cancelled by smoke timeout"
        starts = [
            j["first_event_s"] for j in jobs.values() if j["first_event_s"] is not None
        ]
        ends = [
            j["end_s"] if j["end_s"] is not None else a.timeout for j in jobs.values()
        ]
        overlap = round(min(ends) - max(starts), 1) if len(starts) == 3 else None
        summary = {
            "jobs": [
                {k: v for k, v in j.items() if k not in ("token", "next")}
                for j in jobs.values()
            ],
            "peak_running": peak,
            "all_three_running_together_s": overlap,
            "parallel": bool(overlap and overlap > 0 and peak >= 3),
        }
        print(json.dumps({"summary": summary}, indent=1))
        return 0 if summary["parallel"] else 1
    finally:
        time.sleep(1)
        srv.send_signal(signal.SIGTERM)
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()


if __name__ == "__main__":
    sys.exit(main())

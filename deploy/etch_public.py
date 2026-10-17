"""Etch public backend: the real engine (agent/live.py) behind a small, locked-down HTTP API.

The public app (deploy/etch-app on Vercel) talks to this through a Cloudflare quick tunnel. Unlike
studio/flux_server.py it does NOT serve the pcb-loop folder: only the endpoints below exist.

  GET  /etch/health                      engine up?  {ok, busy, queue, stt}
  POST /etch/live   {mode, text, base}   start a run -> {job}; one run at a time, small queue
  GET  /etch/job?id=..&since=N           the run's events from index N (polling; quick tunnels do not carry SSE)
  POST /etch/stt    raw f32 16 kHz PCM   local Parakeet speech-to-text (studio/stt/stt_server.cjs on :3421)
  GET  /etch/files/engine/out/live-*/frames/*.png|frames.json, /sim.json   board renders for the page
  POST /etch/cancel {job, token}         stop a run (its own process group); token only goes to its starter
  GET  /etch/builds                      the library: running + recent builds (no codes, IPs or paths)
  GET  /etch/run?id=live-N               a finished build's events (from its run.jsonl) for replay
  GET  /etch/thumb?id=live-N             a build's last render frame, read only inside engine/out/live-*/
  GET  /etch/zip?id=live-N               a build's fab zip (KiCad project, Gerbers, BOM)
  GET  /etch/layers?id=live-N            the board's copper pours (zone fills) for the 3D view
  GET  /etch/piece/<16 hex>.png          one KiCad library part, pre-rendered (engine/parts/piece_renders)
  POST /etch/ask    {board, ref?, question, thread?}  ask about a board or part -> {answer, mentions, thoughts}
  GET  /etch/comments?board=ID           comment threads pinned to parts (deploy/explore.py, EXPLORE_API.md)
  POST /etch/comments {board, ref, anchor, text}, /etch/comments/reply {id, text}, /etch/comments/delete {id}
  POST /etch/change {board, ref?, text}  change a board: live-* -> modify run; gh: -> copied into a live-* run

The API is open (no access code since 2026-10-17). Per-IP rate limits and a global daily cap protect the API keys the engine spends.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import explore

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "engine" / "out"
PORT = int(os.environ.get("ETCH_PUBLIC_PORT", "3439"))
STT_URL = os.environ.get("ETCH_STT_URL", "http://127.0.0.1:3421/stt")
ORIGIN_RE = re.compile(
    os.environ.get(
        "ETCH_ORIGINS",
        r"^(https://etch-app[a-z0-9-]*\.vercel\.app|http://(localhost|127\.0\.0\.1)(:\d+)?)$",
    )
)
HOME = str(Path.home())
HEAVY_LOCK = Path(os.environ.get("ETCH_HEAVY_LOCK", str(Path.home() / "helloworld/.heavy-lock")))

RUNS_PER_IP_HOUR = int(os.environ.get("ETCH_RUNS_PER_IP_HOUR", "10"))
RUNS_PER_DAY = int(os.environ.get("ETCH_RUNS_PER_DAY", "80"))
STT_PER_IP_10MIN = 60
QUEUE_MAX = 3
RUN_TIMEOUT_S = 15 * 60
MAX_TEXT = 400
ASKS_PER_IP_10MIN = int(os.environ.get("ETCH_ASKS_PER_IP_10MIN", "40"))
MAX_STT_BYTES = 16000 * 4 * 45  # 45 s of audio
LONG_POLL_MAX_S = 25.0  # under Cloudflare's ~100 s idle cut and any proxy's 60 s read timeout



# ---------------------------------------------------------------- rate limits
class Limiter:
    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: float, take: bool = True) -> bool:
        now = time.time()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < now - window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            if take:
                q.append(now)
            return True


LIM = Limiter()


# ---------------------------------------------------------------- jobs: one engine run at a time
class Job:
    def __init__(self, args: list[str], ip: str, mode: str = "design", text: str = ""):
        self.id = secrets.token_urlsafe(12)
        self.args, self.ip = args, ip
        self.mode, self.text = mode, text
        # handed only to the browser that started the run: it is what lets that browser cancel it
        self.token = secrets.token_urlsafe(18)
        self.events: list[dict] = []
        self.done = False
        self.created = time.time()
        self.ended: float | None = None
        self.state = "queued"  # queued | running | done | failed | cancelled
        self.stage = "waiting in line"
        self.run: str | None = (
            None  # the engine's live-* folder, once its first event names it
        )
        self.proc: subprocess.Popen | None = None
        self.cancelled = False
        self.lock = threading.Lock()
        # long-poll: GET /etch/job?wait=N sleeps on this until a new event or the end of the run
        self.cond = threading.Condition(self.lock)
        self.lane = "full"  # "fast": a stored board or a code-only recipe/edit, outside the full-build line
        self.started: float | None = None  # when the engine process was spawned (after the wait in line)
        self.first_event: float | None = None
        self.last_stage: tuple[str, float] | None = None
        self.stages: list[dict] = []  # [{stage, s}] wall seconds per public stage, for the timing event

    def emit(self, ev: dict):
        now = time.time()
        with self.cond:
            if self.started and self.first_event is None:
                self.first_event = now
            self.events.append(ev)
            self.cond.notify_all()
        run = ev.get("run")
        if isinstance(run, str) and RUN_RE.match(run):
            self.run = run
        s = stage_of(ev)
        if s:
            base = re.sub(r",? ?(try \d+|\(\d+ ahead\))$", "", s)
            if self.last_stage and self.last_stage[0] != base:
                self.stages.append({"stage": self.last_stage[0], "s": round(now - self.last_stage[1], 2)})
            if not self.last_stage or self.last_stage[0] != base:
                self.last_stage = (base, now)
            self.stage = s

    def finish(self) -> None:
        """Mark the run ended and wake every long-poll waiting on it."""
        now = time.time()
        with self.cond:
            self.ended = now
            self.done = True
            self.cond.notify_all()

    def timing(self) -> dict:
        """The server's own clock on this run (additive event `lead:timing`, docs/API.md)."""
        st = self.started or self.created
        if self.last_stage:  # close the stage the run ended in
            self.stages.append({"stage": self.last_stage[0], "s": round(time.time() - self.last_stage[1], 2)})
            self.last_stage = None
        return {
            "agent": "lead",
            "type": "timing",
            "t": 0,
            "lane": self.lane,
            "queued_s": round(st - self.created, 2),
            "spawn_to_first_event_s": round(self.first_event - st, 2) if self.first_event else None,
            "run_s": round(time.time() - st, 2),
            "total_s": round(time.time() - self.created, 2),
            "stages": self.stages,
        }

    def public(self) -> dict:
        """What any signed-in viewer may see about this run: no IP, no token, no paths."""
        return {
            "id": self.run or self.id,
            "job": self.id,
            "run": self.run,
            "text": self.text[:160],
            "mode": self.mode,
            "state": self.state,
            "stage": self.stage,
            "started": round(self.created, 1),
            "ended": round(self.ended, 1) if self.ended else None,
            "thumb": bool(self.run and thumb_path(self.run)),
            "zip": bool(self.run and zip_path(self.run)),
            "lane": self.lane,
        }


RUN_RE = re.compile(r"^live-\d{6,20}$")


def stage_of(ev: dict) -> str | None:
    """A short, plain stage name for the builds list."""
    k = f"{ev.get('agent')}:{ev.get('type')}"
    if k == "lead:queued":
        return f"waiting in line ({ev.get('position', 1)} ahead)"
    if k == "picker:pick" and ev.get("recipe") == "stored board":
        return "built before, loading it"
    if k in ("lead:start", "lead:bench", "classifier:start", "classifier:batch"):
        return "screening parts"
    if k in ("classifier:done", "filter:done", "picker:start"):
        return "picking parts"
    if k == "pieces:confirm":
        return "placing and routing"
    if k == "picker:pick":
        return "placing and routing"
    if k == "assembly:iteration" and ev.get("phase") == "place-route-check":
        return f"placing and routing, try {ev.get('iteration', 1)}"
    if k == "assembly:built":
        return "checking"
    if k == "picker:repair":
        return "repairing"
    if k == "sourcing:start":
        return "fetching a part"
    if k == "export:done":
        return "exporting files"
    if k == "render:start":
        return "rendering in 3D"
    if k == "render:done":
        return "finishing"
    if k == "benchmarks:check":
        return "running your checks"
    return None


class Classifier:
    """ETCH_CLASSIFIER=1: one long-lived NVIDIA prompt classifier process for the whole backend
    (agent/task_router.py --serve), so the ~740 MB model loads once, not once per build. classify() returns
    the scores as a JSON string for the build's ETCH_CLASSIFIED, or None (router off for that build)."""

    ARGS = [
        "uv", "run", "--quiet", "--project", "engine",
        "--with", "torch", "--with", "transformers", "--with", "safetensors",
        "--with", "sentencepiece", "--with", "huggingface_hub",
        "nice", "-n", "10", "python", "-m", "agent.task_router", "--serve",
    ]

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.ready = False

    def _start(self) -> None:
        self.proc = subprocess.Popen(
            self.ARGS, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, start_new_session=True,
        )
        head = json.loads(self.proc.stdout.readline() or "{}")
        self.ready = bool(head.get("ready"))
        print(f"classifier: {head}", flush=True)

    def warm(self) -> None:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()

    def classify(self, text: str) -> str | None:
        if os.environ.get("ETCH_CLASSIFIER") != "1":
            return None
        try:
            with self.lock:
                if self.proc is None or self.proc.poll() is not None:
                    self._start()
                if not self.ready:
                    return None
                self.proc.stdin.write(json.dumps({"ask": text}) + "\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
            res = json.loads(line)
            return None if "error" in res else json.dumps(res)
        except (OSError, ValueError):
            return None


CLASSIFIER = Classifier()


JOBS: dict[str, Job] = {}
WAITING: list[Job] = []
STATE = {"running": None, "fast": None}
RUN_LOCK = threading.Lock()
Q_LOCK = threading.Lock()
# Fast lane (report rank 4): a stored board (0.05 s) or a code-only recipe or edit never waits behind a full
# picker build or a 400 s ESP32 route. It has its own lock, so at most one fast and one full engine run at once.
FAST_WAITING: list[Job] = []
FAST_LOCK = threading.Lock()
LANES = {
    "full": (WAITING, RUN_LOCK, "running"),
    "fast": (FAST_WAITING, FAST_LOCK, "fast"),
}
_CATALOG_KEYS: dict[str, dict] | None = None


def catalog_keys() -> dict[str, dict]:
    global _CATALOG_KEYS
    if _CATALOG_KEYS is None:
        try:
            pieces = json.loads((ROOT / "engine/parts/kicad_catalog.json").read_text())["pieces"]
            _CATALOG_KEYS = {p["key"]: p for p in pieces}
        except (OSError, ValueError, KeyError):
            _CATALOG_KEYS = {}
    return _CATALOG_KEYS


def lane_for(mode: str, text: str, base: Path | None) -> str:
    """'fast' when the engine will answer from code alone: a stored board, a recipe, or a known edit on the base
    (the same plain-code checks agent/live.py makes first, under a millisecond, no model). Anything else, or any
    doubt, is a full build."""
    try:
        import sys as _sys

        if str(ROOT) not in _sys.path:
            _sys.path.insert(0, str(ROOT))
        from agent import board_store, prefilter, recipes  # stdlib-only modules

        if mode == "design":
            pf = prefilter.screen(text)
            if pf and pf.stop:
                return "fast"  # refused with no model call
            ask = pf.ask if pf else text
            if not os.environ.get("ETCH_NO_STORE") and board_store.lookup(ask):
                return "fast"
            return "fast" if recipes.match(ask, catalog_keys()) else "full"
        if mode == "modify" and base is not None:
            d = json.loads((base / "design.json").read_text())
            return "fast" if recipes.edit(text, d, catalog_keys() or None) else "full"
    except Exception:  # noqa: BLE001 - a guess only; the full lane is always correct
        return "full"
    return "full"


def clean(s: str) -> str:
    return str(s).replace(HOME, "~")


def worker(job: Job):
    waiting, run_lock, slot = LANES[job.lane]
    with Q_LOCK:
        waiting.append(job)
    try:
        while True:
            if job.cancelled:  # cancelled while waiting in line: nothing was started
                with Q_LOCK:
                    if job in waiting:
                        waiting.remove(job)
                return
            with Q_LOCK:
                pos = waiting.index(job)
            if pos == 0 and run_lock.acquire(timeout=1.0):
                if job.cancelled:
                    run_lock.release()
                    continue
                break
            job.emit(
                {
                    "agent": "lead",
                    "type": "queued",
                    "position": pos + (1 if STATE[slot] else 0),
                    "t": 0,
                }
            )
            time.sleep(1.0)
        with Q_LOCK:
            waiting.remove(job)
        STATE[slot] = job.id
        job.state, job.stage = "running", "starting the engine"
        job.started = time.time()
        # a file, so a chatty stderr can never block the stdout pipe
        errf = tempfile.TemporaryFile(mode="w+")
        # own session = own process group (pgid == proc.pid): cancel and the timeout signal exactly this
        # run's processes (uv, the engine's python, KiCad, ngspice) and nothing else
        env = dict(os.environ)
        scored = CLASSIFIER.classify(job.text) if job.mode == "design" else None
        if scored:
            env["ETCH_CLASSIFIED"] = scored
        proc = subprocess.Popen(
            job.args,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=errf,
            text=True,
            start_new_session=True,
        )
        job.proc = proc
        timer = threading.Timer(RUN_TIMEOUT_S, lambda: stop_group(job))
        timer.start()
        ok = None
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if job.cancelled:
                continue
            job.emit(ev)
            if ev.get("agent") == "lead" and ev.get("type") == "done":
                ok = bool(ev.get("ok", True))
            if ev.get("agent") == "benchmarks" and ev.get("type") == "done":
                ok = True
        timer.cancel()
        rc = proc.wait()
        errf.seek(0)
        err = errf.read()
        errf.close()
        if job.cancelled:
            pass
        elif rc != 0:
            tail = [x for x in err.splitlines() if x.strip()][-1:] or ["engine stopped"]
            job.emit(
                {
                    "agent": "lead",
                    "type": "error",
                    "t": 0,
                    "error": clean(tail[0])[:300],
                }
            )
            job.state, job.stage = "failed", "stopped with an error"
        else:
            job.state = "done" if ok is not False else "failed"
            job.stage = "done" if ok is not False else "checks did not all pass"
    except Exception as e:  # report to the page instead of hanging the poll
        job.emit({"agent": "lead", "type": "error", "t": 0, "error": clean(e)[:300]})
        job.state, job.stage = "failed", "stopped with an error"
        with Q_LOCK:
            if job in waiting:
                waiting.remove(job)
    finally:
        if job.cancelled:
            job.state, job.stage = "cancelled", "cancelled"
            job.emit({"agent": "lead", "type": "cancelled", "t": 0})
        if STATE[slot] == job.id:
            STATE[slot] = None
            run_lock.release()
        job.emit(job.timing())
        job.finish()


def lock_owner() -> int | None:
    try:
        return int((HEAVY_LOCK / "owner").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop_group(job: Job) -> dict:
    """Stop one run: SIGTERM its own process group, SIGKILL whatever is left after a grace period.

    The heavy lock is released only when its owner PID belonged to this run's group (the engine's
    render step takes it with its own PID); a lock held by any other job is never touched.
    """
    proc = job.proc
    if proc is None:
        return {"signalled": False}
    pgid = proc.pid  # start_new_session=True makes the child a group leader
    owner = lock_owner()
    ours = False
    if owner:
        try:
            ours = os.getpgid(owner) == pgid
        except ProcessLookupError:
            ours = False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.time() + 6
    while time.time() < deadline and group_alive(pgid):
        time.sleep(0.2)
    killed = False
    if group_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
            killed = True
        except ProcessLookupError:
            pass
    released = False
    if ours and owner and lock_owner() == owner and not pid_alive(owner):
        shutil.rmtree(HEAVY_LOCK, ignore_errors=True)
        released = True
    return {
        "signalled": True,
        "pgid": pgid,
        "sigkill": killed,
        "lock_released": released,
    }


def cancel_job(job: Job) -> dict:
    with job.lock:
        if job.done:
            return {
                "ok": False,
                "error": "That build already finished.",
                "state": job.state,
            }
        job.cancelled = True
    job.stage = "cancelling"
    res = stop_group(job)
    if job.run:
        try:  # remembered on disk so the library still says cancelled after a restart
            (OUT / job.run / "cancelled.json").write_text(
                json.dumps({"cancelled": time.time()})
            )
        except OSError:
            pass
    return {"ok": True, "lock_released": res.get("lock_released", False)}


# ---------------------------------------------------------------- library: builds on disk + the job table
def thumb_path(run: str) -> Path | None:
    if not RUN_RE.match(run):
        return None
    fr = OUT / run / "frames"
    if not fr.is_dir():
        return None
    pngs = sorted(fr.glob("f[0-9][0-9][0-9].png"))
    p = (pngs[-1] if pngs else fr / "hi.png").resolve()
    return p if p.is_file() and OUT.resolve() in p.parents else None


VIEW_LOCK = (
    threading.Lock()
)  # one viewer/export job at a time: each is a few seconds of kicad-cli


def viewer(cmd: str, run: str) -> dict | None:
    """agent/viewer.py in the engine venv (GLB, parts sheet, checked export). Not part of a build."""
    if not RUN_RE.match(run) or not (OUT / run / "design.json").is_file():
        return None
    with VIEW_LOCK:
        proc = subprocess.run(
            [
                "nice",
                "-n",
                "10",
                str(ROOT / "engine/.venv/bin/python3"),
                "-m",
                "agent.viewer",
                cmd,
                str(OUT / run),
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'engine'}"},
            capture_output=True,
            text=True,
            timeout=120,
        )
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"ok": False, "error": "viewer failed"}


def cached_view(run: str, name: str) -> Path | None:
    """view/board.glb or view/parts.json when it is at least as new as the board and design files."""
    d = OUT / run
    p = d / "view" / name
    try:
        pcb = next(x for x in d.glob("*.kicad_pcb") if not x.name.startswith("_"))
        src = max(pcb.stat().st_mtime, (d / "design.json").stat().st_mtime)
        if p.stat().st_mtime < src:
            return None
    except (StopIteration, OSError):
        return None
    p = p.resolve()
    return p if OUT.resolve() in p.parents else None


FILL_RE = re.compile(
    r'\(filled_polygon\s*\(layer "([FB])\.Cu"\)(.*?)\(pts(.*?)\)\s*\)', re.S
)
XY_RE = re.compile(r"\(xy ([-\d.e]+) ([-\d.e]+)\)")


def board_layers(run: str) -> dict | None:
    """The board's real copper pours (KiCad zone fills, F.Cu and B.Cu) for the 3D view, read from the .kicad_pcb.

    Each pour is one fractured outline as a flat list of micrometre integers [x0, y0, x1, y1, ...] in KiCad board
    coordinates (the same frame KiCad's GLB export uses). Cached as view/layers.json next to board.glb.
    """
    if not RUN_RE.match(run):
        return None
    hit = cached_view(run, "layers.json")
    if hit:
        return json.loads(hit.read_text())
    d = OUT / run
    try:
        pcb = next(x for x in d.glob("*.kicad_pcb") if not x.name.startswith("_"))
    except StopIteration:
        return None
    pours: dict[str, list[list[int]]] = {"F": [], "B": []}
    for m in FILL_RE.finditer(pcb.read_text()):
        pts = [round(float(v) * 1000) for xy in XY_RE.findall(m.group(3)) for v in xy]
        if len(pts) >= 6:
            pours[m.group(1)].append(pts)
    out = {"ok": True, "units": "um", "pours": pours}
    try:
        (d / "view").mkdir(exist_ok=True)
        (d / "view" / "layers.json").write_text(json.dumps(out, separators=(",", ":")))
    except OSError:
        pass
    return out


def zip_path(run: str) -> Path | None:
    if not RUN_RE.match(run):
        return None
    zs = sorted((OUT / run).glob("*-fab.zip"))
    if not zs:
        return None
    p = zs[0].resolve()
    return p if p.is_file() and OUT.resolve() in p.parents else None


DISK_CACHE: dict[str, tuple[float, dict]] = {}
DISK_STAGE = {
    "done": "done",
    "failed": "checks did not all pass",
    "cancelled": "cancelled",
    "stopped": "stopped before it finished",
}


NOT_A_BOARD = ("prefilter:", "no board in the ask", "mains voltage")


def disk_info(d: Path) -> dict | None:
    try:
        mt = d.stat().st_mtime
    except OSError:
        return None
    hit = DISK_CACHE.get(d.name)
    if hit and hit[0] == mt:
        return hit[1]
    ask, mode, ok, total = "", "design", None, None
    res = d / "live_result.json"
    if res.is_file():
        try:
            r = json.loads(res.read_text())
            ask = str(r.get("ask", ""))
            ok = bool(r.get("ok"))
            total = (r.get("timings") or {}).get("total_s")
            if str(r.get("error") or "").startswith(NOT_A_BOARD):
                # answered with a say and no board (junk, injection, SQL, 500 LEDs, mains): never a library card,
                # so nobody's "ignore previous instructions" shows up on everyone's library (QA round 4)
                DISK_CACHE[d.name] = (mt, None)
                return None
        except (OSError, ValueError):
            pass
    try:
        with open(d / "run.jsonl") as f:
            first = json.loads(f.readline() or "{}")
        ask = ask or str(first.get("ask", ""))
        if first.get("base"):
            mode = "modify"
        if first.get("type") == "bench":
            mode = "bench"
    except (OSError, ValueError):
        pass
    if not ask and not res.is_file():
        return None  # an empty or foreign folder
    if (d / "cancelled.json").is_file():
        state = "cancelled"
    elif ok is None:
        state = "stopped"  # no result and no live job: interrupted, listed as failed
    else:
        state = "done" if ok else "failed"
    info = {
        "id": d.name,
        "job": None,
        "run": d.name,
        "text": clean(ask)[:160],
        "mode": mode,
        "state": "failed" if state == "stopped" else state,
        "stage": DISK_STAGE[state],
        "started": round(int(d.name[5:]) / 1000, 1),
        "ended": round(mt, 1),
        "seconds": total,
        "thumb": thumb_path(d.name) is not None,
        "zip": zip_path(d.name) is not None,
    }
    DISK_CACHE[d.name] = (mt, info)
    return info


def library(limit: int = 60) -> list[dict]:
    by_run: dict[str, dict] = {}
    out: list[dict] = []
    dirs = sorted(
        (p for p in OUT.glob("live-*") if RUN_RE.match(p.name) and p.is_dir()),
        key=lambda p: int(p.name[5:]),
        reverse=True,
    )[:limit]
    hidden = set()
    for d in dirs:
        info = disk_info(d)
        if info:
            info = dict(info)
            by_run[d.name] = info
            out.append(info)
        elif (d / "live_result.json").is_file():
            hidden.add(d.name)
    for j in list(JOBS.values()):
        if j.run in hidden:
            continue
        pub = j.public()
        if j.run and j.run in by_run:
            row = by_run[j.run]
            row["job"] = j.id
            if not j.done or j.state == "cancelled":
                row.update(state=pub["state"], stage=pub["stage"])
        elif not j.done or (j.run and (OUT / j.run).is_dir()):
            # a finished job whose run folder is gone (archived) has nothing to show: drop it
            out.append(pub)
        # a run cancelled while still in line never made a folder: it drops out once it ends
    rank = {"running": 0, "queued": 1}
    out.sort(key=lambda b: (rank.get(b["state"], 2), -b["started"]))
    return out


def run_events(run: str) -> list[dict] | None:
    if not RUN_RE.match(run):
        return None
    p = (OUT / run / "run.jsonl").resolve()
    if OUT.resolve() not in p.parents or not p.is_file():
        return None
    evs = []
    root = str(ROOT)
    for line in p.read_text().splitlines():
        line = line.replace(root, "").replace(HOME, "~")
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev.pop("ts", None)
        evs.append(ev)
    # a board rendered after its build (live render) logs that to render.jsonl only: replay its last finished
    # render too, so the library opens it with its picture and 3D board
    rp = OUT / run / "render.jsonl"
    if rp.is_file() and not any(
        e.get("agent") == "render" and e.get("type") == "done" for e in evs
    ):
        for line in reversed(rp.read_text().splitlines()):
            try:
                ev = json.loads(line.replace(root, "").replace(HOME, "~"))
            except json.JSONDecodeError:
                continue
            if ev.get("agent") == "render" and ev.get("type") == "done":
                ev.pop("ts", None)
                # its own t counts from the render's start; show it just before "lead done" instead
                at = next(
                    (
                        i
                        for i in range(len(evs) - 1, -1, -1)
                        if evs[i].get("agent") == "lead"
                        and evs[i].get("type") == "done"
                    ),
                    len(evs),
                )
                if evs:
                    ev["t"] = evs[min(at, len(evs) - 1)].get("t", ev.get("t"))
                evs.insert(at, ev)
                break
    return evs


def gc_jobs():
    while True:
        time.sleep(300)
        for k, j in list(JOBS.items()):
            if j.done and time.time() - j.created > 3600:
                JOBS.pop(k, None)


PIECE_RE = re.compile(r"^/etch/piece/([0-9a-f]{16})\.png$")
PIECES = ROOT / "engine/parts/piece_renders"
FILE_RE = re.compile(
    r"^/engine/out/(live-\d+)/(frames/(f\d{3}\.png|frames\.json)|sim\.json)$"
)
BASE_RE = re.compile(r"^/?engine/out/(live-\d+)/?$")


class H(BaseHTTPRequestHandler):
    server_version = "etch"
    sys_version = ""

    def log_message(self, fmt, *a):
        pass

    # ---- helpers
    def ip(self) -> str:
        return self.headers.get("CF-Connecting-IP") or self.client_address[0]

    def cors(self):
        o = self.headers.get("Origin", "")
        if o and ORIGIN_RE.match(o):
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Vary", "Origin")
            self.send_header(
                "Access-Control-Allow-Headers", "Content-Type"
            )
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Max-Age", "600")

    def send_json(self, obj, code=200):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self, cap: int) -> bytes | None:
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n > cap:
            return None
        return self.rfile.read(n) if n else b""

    # ---- verbs
    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/etch/health":
            return self.send_json(
                {
                    "ok": True,
                    "busy": bool(STATE["running"]),
                    "queue": len(WAITING),
                    "fast_busy": bool(STATE["fast"]),
                    "fast_queue": len(FAST_WAITING),
                    "stt": stt_up(),
                }
            )
        if u.path == "/etch/comments":
            q = urllib.parse.parse_qs(u.query)
            try:
                board = explore.load_board((q.get("board") or [""])[0])
            except explore.ExploreError as e:
                return self.send_json({"error": str(e)}, e.status)
            return self.send_json({"ok": True, "comments": comments().list(board.id)})
        if u.path == "/etch/job":
            q = urllib.parse.parse_qs(u.query)
            j = JOBS.get(q.get("id", [""])[0])
            if not j:
                return self.send_json({"error": "no such run"}, 404)
            try:
                since = max(0, int((q.get("since", ["0"])[0] or "0")))
                wait = min(LONG_POLL_MAX_S, max(0.0, float((q.get("wait", ["0"])[0] or "0"))))
            except ValueError:
                return self.send_json({"error": "bad request"}, 400)
            with j.cond:
                # long-poll (additive): hold the request until an event past `since` exists or the run ends
                if wait > 0:
                    j.cond.wait_for(lambda: len(j.events) > since or j.done, timeout=wait)
                evs = j.events[since:]
                n = len(j.events)
            return self.send_json(
                {
                    "events": evs,
                    "next": n,
                    "done": j.done and since + len(evs) >= n,
                    "state": j.state,
                    "stage": j.stage,
                    "run": j.run,
                }
            )
        if u.path == "/etch/builds":
            return self.send_json({"builds": library(), "now": round(time.time(), 1)})
        if u.path == "/etch/run":
            run = urllib.parse.parse_qs(u.query).get("id", [""])[0]
            evs = run_events(run)
            if evs is None:
                return self.send_json({"error": "no such build"}, 404)
            return self.send_json({"events": evs, "build": disk_info(OUT / run)})
        if u.path == "/etch/thumb":
            run = urllib.parse.parse_qs(u.query).get("id", [""])[0]
            p = thumb_path(run) if RUN_RE.match(run) else None
            if not p:
                return self.send_json({"error": "not found"}, 404)
            return self.blob(p.read_bytes(), "image/png", "public, max-age=600")
        if u.path == "/etch/zip":
            run = urllib.parse.parse_qs(u.query).get("id", [""])[0]
            p = zip_path(run) if RUN_RE.match(run) else None
            if not p:
                return self.send_json({"error": "not found"}, 404)
            return self.blob(p.read_bytes(), "application/zip", "no-store", p.name)
        if u.path == "/etch/layers":
            res = board_layers(urllib.parse.parse_qs(u.query).get("id", [""])[0])
            if res is None:
                return self.send_json({"error": "not found"}, 404)
            return self.send_json(res)
        if u.path in ("/etch/glb", "/etch/parts", "/etch/export", "/etch/export.zip"):
            run = urllib.parse.parse_qs(u.query).get("id", [""])[0]
            # already built (a stored board copies them in, a reopened build made them before): send the file,
            # no engine process (that start-up alone was about 3 s)
            if u.path in ("/etch/glb", "/etch/parts") and RUN_RE.match(run):
                hit = cached_view(
                    run, "board.glb" if u.path == "/etch/glb" else "parts.json"
                )
                if hit:
                    if u.path == "/etch/glb":
                        return self.blob(
                            hit.read_bytes(), "model/gltf-binary", "no-store"
                        )
                    return self.send_json({"ok": True, **json.loads(hit.read_text())})
            cmd = {"/etch/glb": "glb", "/etch/parts": "parts"}.get(u.path, "export")
            res = viewer(cmd, run)
            if res is None:
                return self.send_json({"error": "not found"}, 404)
            if u.path == "/etch/glb":
                if not res.get("ok"):
                    return self.send_json(
                        {"error": res.get("error", "no glb")[:200]}, 500
                    )
                return self.blob(
                    (OUT / run / "view/board.glb").read_bytes(),
                    "model/gltf-binary",
                    "no-store",
                )
            if u.path == "/etch/export.zip":
                if not res.get("ok"):
                    return self.send_json(
                        {"error": "checks failed", "reasons": res.get("reasons", [])},
                        409,
                    )
                p = OUT / run / "view" / Path(res["zip"]).name
                return self.blob(p.read_bytes(), "application/zip", "no-store", p.name)
            res.pop("zip", None)
            res.pop("glb", None)
            if "error" in res:
                res["error"] = (
                    str(res["error"]).replace(str(ROOT), "").replace(HOME, "~")
                )
            return self.send_json(res)
        if m := PIECE_RE.match(u.path):
            # library part renders: public KiCad library art, content-addressed, so cache for a long time
            p = PIECES / f"{m.group(1)}.png"
            if not p.is_file():
                return self.send_json({"error": "not found"}, 404)
            return self.blob(
                p.read_bytes(), "image/png", "public, max-age=604800, immutable"
            )
        if u.path.startswith("/etch/files/"):
            return self.file(u.path[len("/etch/files") :])
        return self.send_json({"error": "not found"}, 404)

    def blob(self, data: bytes, ctype: str, cache: str, name: str | None = None):
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        if name:
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        self.wfile.write(data)

    def file(self, rel: str):
        m = FILE_RE.match(rel)
        if not m:
            return self.send_json({"error": "not found"}, 404)
        p = (ROOT / rel.lstrip("/")).resolve()
        if OUT.resolve() not in p.parents or not p.is_file():
            return self.send_json({"error": "not found"}, 404)
        data = p.read_bytes()
        ctype = "image/png" if p.suffix == ".png" else "application/json"
        if p.name == "frames.json":
            d = json.loads(data)
            d.pop("pcb", None)
            data = json.dumps(d).encode()
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Cache-Control",
            "public, max-age=3600" if p.suffix == ".png" else "no-store",
        )
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        ip = self.ip()
        if self.path == "/etch/cancel":
            try:
                body = json.loads(self.body(4096) or b"{}")
            except (json.JSONDecodeError, TypeError):
                return self.send_json({"error": "bad request"}, 400)
            j = JOBS.get(str(body.get("job", "")))
            if not j:
                return self.send_json({"error": "no such run"}, 404)
            # only the browser that started a run holds its token, so viewers of the library cannot stop it
            if not hmac.compare_digest(
                str(body.get("token", "")).encode(), j.token.encode()
            ):
                return self.send_json(
                    {"error": "Only the person who started this build can cancel it."},
                    403,
                )
            res = cancel_job(j)
            return self.send_json(res, 200 if res.get("ok") else 409)
        if self.path == "/etch/stt":
            if not LIM.allow("stt:" + ip, STT_PER_IP_10MIN, 600):
                return self.send_json(
                    {
                        "error": "Too many voice requests. Wait a few minutes, or type instead."
                    },
                    429,
                )
            b = self.body(MAX_STT_BYTES)
            if b is None:
                return self.send_json(
                    {"error": "That recording is too long. Keep it under 45 seconds."},
                    413,
                )
            try:
                data = urllib.request.urlopen(
                    urllib.request.Request(STT_URL, data=b), timeout=60
                ).read()
                return self.send_json(data)
            except OSError:
                return self.send_json(
                    {"error": "Voice is offline right now. Type instead."}, 503
                )
        if self.path == "/etch/tts":
            if not LIM.allow("tts:" + ip, TTS_PER_IP_10MIN, 600):
                return self.send_json(
                    {
                        "error": "Too many voice requests. Wait a few minutes, or type instead."
                    },
                    429,
                )
            try:
                text = str(json.loads(self.body(4096) or b"{}").get("text", ""))
            except (json.JSONDecodeError, TypeError):
                return self.send_json({"error": "bad request"}, 400)
            text = re.sub(r"\s+", " ", text).strip()[:MAX_TEXT]
            if not text:
                return self.send_json({"error": "no text"}, 400)
            try:
                audio = tts(text)
            except OSError:
                return self.send_json(
                    {"error": "Voice is offline right now. Type instead."}, 503
                )
            self.send_response(200)
            self.cors()
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
            return
        if self.path == "/etch/live":
            raw = self.body(8192)
            try:
                body = json.loads(raw or b"{}")
            except (json.JSONDecodeError, TypeError):
                return self.send_json({"error": "bad request"}, 400)
            mode = body.get("mode")
            text = re.sub(r"\s+", " ", str(body.get("text", ""))).strip()[:MAX_TEXT]
            if mode not in ("design", "modify", "bench") or not text:
                return self.send_json(
                    {"error": "Tell me what your board should do."}, 400
                )
            args = [
                "uv",
                "run",
                "--quiet",
                "--project",
                "engine",
                "--with",
                "openai",
                "python",
                "-m",
                "agent.live",
                mode,
                text,
                "--events",
            ]
            if mode != "design":
                m = BASE_RE.match(str(body.get("base", "")))
                base = (OUT / m.group(1)) if m else None
                if not base or not base.is_dir():
                    return self.send_json(
                        {"error": "Build a board first, then tell me what to change."},
                        400,
                    )
                if not (base / "design.json").is_file():
                    return self.send_json(
                        {
                            "error": "That version did not finish, so there is nothing to change yet."
                        },
                        400,
                    )
                args += ["--base", str(base)]
            return self.start_build(ip, args, mode, text)
        if self.path in EXPLORE_POST:
            try:
                return EXPLORE_POST[self.path](self, ip)
            except explore.ExploreError as e:
                return self.send_json({"error": str(e)}, e.status)
        return self.send_json({"error": "not found"}, 404)

    def start_build(self, ip: str, args: list[str], mode: str, text: str, room: bool = False):
        """Queue an engine run. room=True only checks capacity (nothing is started)."""
        base = Path(args[args.index("--base") + 1]) if "--base" in args else None
        lane = lane_for(mode, text, base) if mode in ("design", "modify") else "full"
        if len(LANES[lane][0]) >= QUEUE_MAX:
            return self.send_json(
                {"error": "I am busy with other builds. Try again in a minute."},
                429,
            )
        if not LIM.allow(
            "runs:" + ip, RUNS_PER_IP_HOUR, 3600, take=False
        ) or not LIM.allow("runs:day", RUNS_PER_DAY, 86400, take=False):
            return self.send_json(
                {
                    "error": "That is all the builds I can do for now. Try again later."
                },
                429,
            )
        if room:
            return None
        LIM.allow("runs:" + ip, RUNS_PER_IP_HOUR, 3600)
        LIM.allow("runs:day", RUNS_PER_DAY, 86400)
        job = Job(args, ip, mode, text)
        job.lane = lane
        JOBS[job.id] = job
        threading.Thread(target=worker, args=(job,), daemon=True).start()
        return self.send_json({"ok": True, "job": job.id, "token": job.token})

    # ---- Explore + board view (EXPLORE_API.md section 2)
    def json_body(self, cap: int = 8192) -> dict:
        try:
            body = json.loads(self.body(cap) or b"{}")
        except (json.JSONDecodeError, TypeError, ValueError):
            raise explore.ExploreError("bad request")
        if not isinstance(body, dict):
            raise explore.ExploreError("bad request")
        return body

    def ask_allowed(self, ip: str) -> None:
        if not LIM.allow("ask:" + ip, ASKS_PER_IP_10MIN, 600):
            raise explore.ExploreError("Too many questions. Wait a few minutes, then ask again.", 429)

    def post_ask(self, ip: str):
        body = self.json_body()
        board = explore.load_board(body.get("board"))
        ref = explore.check_ref(board, body.get("ref"))
        question = user_text(body.get("question"))
        history = []
        tid = body.get("thread")
        if isinstance(tid, str) and explore.COMMENT_RE.match(tid):
            c = comments().get(tid)
            if c and c["board"] == board.id:
                history = c["messages"]
                ref = ref or c["ref"]
        self.ask_allowed(ip)
        res = explore.ask(board, question, ref, history)
        return self.send_json({"ok": True, **res})

    def post_comment(self, ip: str):
        body = self.json_body()
        board = explore.load_board(body.get("board"))
        ref = explore.check_ref(board, body.get("ref"))
        anchor = explore.check_anchor(body.get("anchor"))
        text = user_text(body.get("text"))
        store = comments()
        if store.count(board.id) >= explore.MAX_COMMENTS_PER_BOARD:
            raise explore.ExploreError("This board has as many comments as it can hold.", 409)
        self.ask_allowed(ip)
        now = int(time.time())
        res = explore.ask(board, text, ref)
        msgs = [
            {"role": "user", "text": text, "at": now},
            {"role": "assistant", "text": res["answer"], "at": int(time.time()), "mentions": res["mentions"],
             "thoughts": res["thoughts"], "seconds": res["seconds"]},
        ]
        return self.send_json({"ok": True, "comment": store.create(board.id, ref, anchor, msgs)})

    def post_reply(self, ip: str):
        body = self.json_body()
        cid = body.get("id")
        store = comments()
        c = store.get(cid) if isinstance(cid, str) and explore.COMMENT_RE.match(cid) else None
        if not c:
            raise explore.ExploreError("That comment is gone.", 404)
        text = user_text(body.get("text"))
        if len(c["messages"]) + 2 > explore.MAX_MESSAGES:
            raise explore.ExploreError("This thread is full. Start a new comment.", 409)
        board = explore.load_board(c["board"])
        ref = c["ref"] if c["ref"] in board.parts else None
        self.ask_allowed(ip)
        now = int(time.time())
        res = explore.ask(board, text, ref, c["messages"])
        msgs = [
            {"role": "user", "text": text, "at": now},
            {"role": "assistant", "text": res["answer"], "at": int(time.time()), "mentions": res["mentions"],
             "thoughts": res["thoughts"], "seconds": res["seconds"]},
        ]
        updated = store.append(cid, msgs)
        if not updated:
            raise explore.ExploreError("That comment is gone.", 404)
        return self.send_json({"ok": True, "comment": updated})

    def post_delete(self, ip: str):
        body = self.json_body()
        cid = body.get("id")
        if not (isinstance(cid, str) and explore.COMMENT_RE.match(cid) and comments().delete(cid)):
            raise explore.ExploreError("That comment is gone.", 404)
        return self.send_json({"ok": True})

    def post_change(self, ip: str):
        body = self.json_body()
        board = explore.load_board(body.get("board"))
        ref = explore.check_ref(board, body.get("ref"))
        text = re.sub(r"\s+", " ", str(body.get("text") or "")).strip()[:MAX_TEXT]
        if not text:
            raise explore.ExploreError("Tell me what to change.")
        if ref:
            text = f"{ref}: {text}"[:MAX_TEXT]
        if board.kind == "live":
            base = board.run_dir
        else:
            design = explore.import_design(board)  # 403 / 409 / 422 with the reason
            if (busy := self.start_build(ip, [], "modify", text, room=True)) is not None:
                return busy
            base = explore.seed_run(board, design)
        args = ["uv", "run", "--quiet", "--project", "engine", "--with", "openai", "python", "-m", "agent.live",
                "modify", text, "--events", "--base", str(base)]
        return self.start_build(ip, args, "modify", text)


_COMMENTS: list = []
_COMMENTS_LOCK = threading.Lock()


def comments() -> explore.Comments:
    with _COMMENTS_LOCK:
        if not _COMMENTS:
            _COMMENTS.append(explore.Comments(explore.comments_path()))
        return _COMMENTS[0]


def user_text(v) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(v or "")).strip()
    if not text:
        raise explore.ExploreError("Type a question first.")
    if len(text) > MAX_TEXT:
        raise explore.ExploreError(f"Keep it under {MAX_TEXT} characters.")
    return text


EXPLORE_POST = {
    "/etch/ask": H.post_ask,
    "/etch/comments": H.post_comment,
    "/etch/comments/reply": H.post_reply,
    "/etch/comments/delete": H.post_delete,
    "/etch/change": H.post_change,
}


# newest OpenAI speech snapshot; cedar is the male voice OpenAI recommends for best quality
TTS_MODEL = os.environ.get("ETCH_TTS_MODEL", "gpt-4o-mini-tts-2025-12-15")
TTS_VOICE = os.environ.get("ETCH_TTS_VOICE", "cedar")
TTS_PER_IP_10MIN = 120
TTS_INSTRUCTIONS = (
    "You are Etch, a friendly, confident American hardware engineer in his twenties. Speak warmly and clearly, "
    "natural conversational pace, light energy, no robotic cadence."
)


METER = ROOT / "qa" / "openai-meter.jsonl"
BUDGET_FILE = ROOT / "qa" / "openai-budget.json"


def tts_guard() -> None:
    """Same overnight budget as agent/llm.py: refuse once the metered total reaches it."""
    try:
        b = float(json.loads(BUDGET_FILE.read_text())["budget_usd"])
    except (OSError, ValueError, KeyError):
        return
    try:
        s = sum(
            json.loads(x).get("usd", 0.0)
            for x in METER.read_text().splitlines()
            if x.strip()
        )
    except OSError:
        s = 0.0
    if s >= b:
        raise OSError("OpenAI budget reached")


def tts_meter(text: str) -> None:
    # gpt-4o-mini-tts: $0.60 / 1M text tokens in, about $0.015 per audio minute out (openai.com/api/pricing);
    # speech runs about 15 characters a second, priced 1.5x high to stay on the safe side
    usd = (len(text) / 4 * 0.60 / 1e6 + len(text) / 15 / 60 * 0.015) * 1.5
    METER.parent.mkdir(parents=True, exist_ok=True)
    with METER.open("a") as f:
        f.write(
            json.dumps(
                {
                    "ts": round(time.time(), 1),
                    "model": TTS_MODEL,
                    "agent": "tts",
                    "chars": len(text),
                    "usd": round(usd, 6),
                }
            )
            + "\n"
        )


def tts(text: str) -> bytes:
    import sys

    sys.path.insert(0, str(ROOT))
    from agent.config import key_for  # OpenAI TTS: the OpenAI key, whatever provider the engine's models use

    tts_guard()
    tts_meter(text)

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=json.dumps(
            {
                "model": TTS_MODEL,
                "voice": TTS_VOICE,
                "input": text,
                "instructions": TTS_INSTRUCTIONS,
                "response_format": "mp3",
            }
        ).encode(),
        headers={
            "Authorization": "Bearer " + key_for("openai-api-key"),
            "Content-Type": "application/json",
        },
    )
    return urllib.request.urlopen(req, timeout=30).read()


def stt_up() -> bool:
    import socket

    u = urllib.parse.urlparse(STT_URL)
    try:
        with socket.create_connection((u.hostname, u.port), timeout=0.5):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    threading.Thread(target=gc_jobs, daemon=True).start()
    if os.environ.get("ETCH_CLASSIFIER") == "1":
        threading.Thread(target=CLASSIFIER.warm, daemon=True).start()
    print(f"etch public backend on http://127.0.0.1:{PORT}", flush=True)
    # ETCH_BIND=0.0.0.0 inside a container (docs/DEPLOY.md); the Mac keeps loopback behind the tunnel
    ThreadingHTTPServer((os.environ.get("ETCH_BIND", "127.0.0.1"), PORT), H).serve_forever()

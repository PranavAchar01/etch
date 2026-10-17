"""Etch public backend: the real engine (agent/live.py) behind a small, locked-down HTTP API.

The public app (deploy/etch-app on Vercel) talks to this through a Cloudflare quick tunnel. Unlike
studio/flux_server.py it does NOT serve the pcb-loop folder: only the endpoints below exist.

  GET  /etch/health                      engine up?  {ok, busy, queue, stt}
  POST /etch/auth                        check the access code
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

Every POST and job poll needs the header X-Etch-Code. The code lives in the macOS Keychain
(service etch-app-access-code) or ETCH_ACCESS_CODE; it is never in the repo or the page bundle.
Per-IP rate limits and a global daily cap protect the API keys the engine spends.
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
AUTH_FAILS_PER_IP_10MIN = 10
QUEUE_MAX = 3
RUN_TIMEOUT_S = 15 * 60
MAX_TEXT = 400
MAX_STT_BYTES = 16000 * 4 * 45  # 45 s of audio


def access_code() -> str:
    if os.environ.get("ETCH_ACCESS_CODE"):
        return os.environ["ETCH_ACCESS_CODE"]
    return (
        subprocess.check_output(
            [
                "security",
                "find-generic-password",
                "-a",
                os.environ.get("USER", ""),
                "-s",
                "etch-app-access-code",
                "-w",
            ]
        )
        .decode()
        .strip()
    )


CODE = access_code()


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

    def emit(self, ev: dict):
        with self.lock:
            self.events.append(ev)
        run = ev.get("run")
        if isinstance(run, str) and RUN_RE.match(run):
            self.run = run
        s = stage_of(ev)
        if s:
            self.stage = s

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


JOBS: dict[str, Job] = {}
WAITING: list[Job] = []
STATE = {"running": None}
RUN_LOCK = threading.Lock()
Q_LOCK = threading.Lock()


def clean(s: str) -> str:
    return str(s).replace(HOME, "~")


def worker(job: Job):
    with Q_LOCK:
        WAITING.append(job)
    try:
        while True:
            if job.cancelled:  # cancelled while waiting in line: nothing was started
                with Q_LOCK:
                    if job in WAITING:
                        WAITING.remove(job)
                return
            with Q_LOCK:
                pos = WAITING.index(job)
            if pos == 0 and RUN_LOCK.acquire(timeout=1.0):
                if job.cancelled:
                    RUN_LOCK.release()
                    continue
                break
            job.emit(
                {
                    "agent": "lead",
                    "type": "queued",
                    "position": pos + (1 if STATE["running"] else 0),
                    "t": 0,
                }
            )
            time.sleep(1.0)
        with Q_LOCK:
            WAITING.remove(job)
        STATE["running"] = job.id
        job.state, job.stage = "running", "starting the engine"
        # a file, so a chatty stderr can never block the stdout pipe
        errf = tempfile.TemporaryFile(mode="w+")
        # own session = own process group (pgid == proc.pid): cancel and the timeout signal exactly this
        # run's processes (uv, the engine's python, KiCad, ngspice) and nothing else
        proc = subprocess.Popen(
            job.args,
            cwd=ROOT,
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
            if job in WAITING:
                WAITING.remove(job)
    finally:
        if job.cancelled:
            job.state, job.stage = "cancelled", "cancelled"
            job.emit({"agent": "lead", "type": "cancelled", "t": 0})
        job.ended = time.time()
        if STATE["running"] == job.id:
            STATE["running"] = None
            RUN_LOCK.release()
        job.done = True


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
                "Access-Control-Allow-Headers", "Content-Type, X-Etch-Code"
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

    def authed(self) -> bool:
        got = self.headers.get("X-Etch-Code", "")
        return bool(got) and hmac.compare_digest(got.strip().encode(), CODE.encode())

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
                    "stt": stt_up(),
                }
            )
        if u.path == "/etch/job":
            if not self.authed():
                return self.send_json({"error": "code"}, 401)
            q = urllib.parse.parse_qs(u.query)
            j = JOBS.get(q.get("id", [""])[0])
            if not j:
                return self.send_json({"error": "no such run"}, 404)
            since = max(0, int((q.get("since", ["0"])[0] or "0")))
            with j.lock:
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
            if not self.authed():
                return self.send_json({"error": "code"}, 401)
            return self.send_json({"builds": library(), "now": round(time.time(), 1)})
        if u.path == "/etch/run":
            if not self.authed():
                return self.send_json({"error": "code"}, 401)
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
            if not self.authed():
                return self.send_json({"error": "code"}, 401)
            run = urllib.parse.parse_qs(u.query).get("id", [""])[0]
            p = zip_path(run) if RUN_RE.match(run) else None
            if not p:
                return self.send_json({"error": "not found"}, 404)
            return self.blob(p.read_bytes(), "application/zip", "no-store", p.name)
        if u.path == "/etch/layers":
            if not self.authed():
                return self.send_json({"error": "code"}, 401)
            res = board_layers(urllib.parse.parse_qs(u.query).get("id", [""])[0])
            if res is None:
                return self.send_json({"error": "not found"}, 404)
            return self.send_json(res)
        if u.path in ("/etch/glb", "/etch/parts", "/etch/export", "/etch/export.zip"):
            if not self.authed():
                return self.send_json({"error": "code"}, 401)
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
        if self.path == "/etch/auth":
            if not LIM.allow(
                "authfail:" + ip, AUTH_FAILS_PER_IP_10MIN, 600, take=False
            ):
                return self.send_json(
                    {"error": "Too many tries. Wait 10 minutes, then try again."}, 429
                )
            if self.authed():
                return self.send_json({"ok": True})
            LIM.allow("authfail:" + ip, AUTH_FAILS_PER_IP_10MIN, 600)
            return self.send_json(
                {"ok": False, "error": "That code did not work."}, 401
            )
        if not self.authed():
            LIM.allow("authfail:" + ip, AUTH_FAILS_PER_IP_10MIN, 600)
            return self.send_json({"error": "code"}, 401)
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
            if len(WAITING) >= QUEUE_MAX:
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
            LIM.allow("runs:" + ip, RUNS_PER_IP_HOUR, 3600)
            LIM.allow("runs:day", RUNS_PER_DAY, 86400)
            job = Job(args, ip, mode, text)
            JOBS[job.id] = job
            threading.Thread(target=worker, args=(job,), daemon=True).start()
            return self.send_json({"job": job.id, "token": job.token})
        return self.send_json({"error": "not found"}, 404)


TTS_VOICE = os.environ.get("ETCH_TTS_VOICE", "ballad")
TTS_PER_IP_10MIN = 120
TTS_INSTRUCTIONS = (
    "You are Etch, a friendly, confident hardware engineer. Speak warmly and clearly, "
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
    with METER.open("a") as f:
        f.write(
            json.dumps(
                {
                    "ts": round(time.time(), 1),
                    "model": "gpt-4o-mini-tts",
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
    from agent.config import api_key

    tts_guard()
    tts_meter(text)

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=json.dumps(
            {
                "model": "gpt-4o-mini-tts",
                "voice": TTS_VOICE,
                "input": text,
                "instructions": TTS_INSTRUCTIONS,
                "response_format": "mp3",
            }
        ).encode(),
        headers={
            "Authorization": "Bearer " + api_key(),
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
    print(f"etch public backend on http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

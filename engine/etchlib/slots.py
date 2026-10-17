"""Parallel live runs: a cross-process counting semaphore for the CPU-heavy steps, plus safe shared-file writes.

Every live build is its own process (deploy/etch_public.py spawns one per POST /etch/live, no queue). Model calls
run fully in parallel; only the local heavy steps (KiCad place/route/ERC/DRC, ngspice, 3D renders, GLB and fab
export) take a slot here, so many runs can wait on the model while N heavy steps interleave.

The semaphore is N lock files under engine/out/.heavy-slots/ held with fcntl.flock: the kernel drops a slot the
moment its process dies, so a cancelled or killed run (SIGKILL of its process group) never leaves a stale slot.
Inside one process the slot is re-entrant and shared by its threads: one run holds at most one slot.

This is NOT the ~/helloworld/.heavy-lock protocol (that one is for batch jobs) and it never touches .render-lock.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

ENGINE_DIR = Path(__file__).resolve().parent.parent
SLOT_DIR = Path(os.environ.get("ETCH_HEAVY_SLOT_DIR", str(ENGINE_DIR / "out" / ".heavy-slots")))


def slot_count() -> int:
    """ETCH_HEAVY_SLOTS, else max(2, cpu_count // 3) (3 on a 10-core Mac)."""
    try:
        n = int(os.environ.get("ETCH_HEAVY_SLOTS", "0"))
    except ValueError:
        n = 0
    return n if n > 0 else max(2, (os.cpu_count() or 4) // 3)


_mu = threading.Lock()
_held: dict[str, Any] = {"fd": None, "depth": 0, "slot": None}


def _try_take(n: int) -> tuple[int, int] | None:
    SLOT_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        fd = os.open(SLOT_DIR / f"slot-{i}.lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            continue
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        return fd, i
    return None


@contextlib.contextmanager
def heavy_slot(wait_s: float = 1800, poll_s: float = 0.25) -> Iterator[float]:
    """Hold one heavy slot for the block. Yields the seconds spent waiting for it."""
    t0 = time.time()
    while True:
        with _mu:
            if _held["fd"] is not None:  # this process already holds a slot: share it
                _held["depth"] += 1
                break
            got = _try_take(slot_count())
            if got:
                _held["fd"], _held["slot"] = got
                _held["depth"] = 1
                break
        if time.time() - t0 > wait_s:
            raise TimeoutError(f"no heavy slot free after {wait_s:.0f} s")
        time.sleep(poll_s)
    try:
        yield round(time.time() - t0, 2)
    finally:
        with _mu:
            _held["depth"] -= 1
            if _held["depth"] == 0:
                fd = _held["fd"]
                _held["fd"] = _held["slot"] = None
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)


def slots_in_use(n: int | None = None) -> int:
    """How many slots some process holds right now (a probe: it takes and drops any free one at once)."""
    n = slot_count() if n is None else n
    if not SLOT_DIR.is_dir():
        return 0
    busy = 0
    for i in range(n):
        p = SLOT_DIR / f"slot-{i}.lock"
        if not p.exists():
            continue
        fd = os.open(p, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            busy += 1
        finally:
            os.close(fd)
    return busy


# ---------------------------------------------------------------- shared files written by parallel runs
@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Exclusive flock on <path>.lock, for a read-modify-write of a file other runs also write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a per-process temp name and rename, so a reader never sees half a file and two writers never
    share a temp file."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def update_json(
    path: Path,
    fn: Callable[[Any], Any],
    default: Callable[[], Any],
    indent: int | None = 1,
    read: Callable[[Path], Any] | None = None,
    trailer: str = "",
) -> Any:
    """Locked read-modify-write of a shared JSON file. fn gets the current data (or default()) and returns the
    new data, which is written atomically and returned."""
    path = Path(path)
    with file_lock(path):
        try:
            data = (read or (lambda p: json.loads(p.read_text())))(path)
        except (OSError, ValueError):
            data = default()
        data = fn(data)
        atomic_write_text(path, json.dumps(data, indent=indent) + trailer)
    return data

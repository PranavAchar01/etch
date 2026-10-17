"""Paths to the local KiCad 10 install, subprocess helpers and the heavy-job lock."""

from __future__ import annotations

import contextlib
import functools
import json
import os
import subprocess
import sys
import time
from pathlib import Path

KICAD_APP = Path(os.environ.get("ETCH_KICAD_APP", str(Path.home() / "Applications/KiCad/KiCad.app")))
KICAD_CLI = KICAD_APP / "Contents/MacOS/kicad-cli"
KICAD_PY = KICAD_APP / "Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
SHARED = KICAD_APP / "Contents/SharedSupport"
if sys.platform.startswith("linux") and "ETCH_KICAD_APP" not in os.environ:
    # KiCad 10 from the official Ubuntu PPA: kicad-cli on PATH, pcbnew in the system Python, libraries in /usr/share
    KICAD_CLI = Path(os.environ.get("ETCH_KICAD_CLI", "/usr/bin/kicad-cli"))
    KICAD_PY = Path(os.environ.get("ETCH_KICAD_PY", "/usr/bin/python3"))
    SHARED = Path(os.environ.get("ETCH_KICAD_SHARE", "/usr/share/kicad"))
SYMBOL_DIR = SHARED / "symbols"
FOOTPRINT_DIR = SHARED / "footprints"
MODEL_DIR = SHARED / "3dmodels"

ENGINE_DIR = Path(__file__).resolve().parent.parent
HEAVY_LOCK = Path(os.environ.get("ETCH_HEAVY_LOCK", str(Path.home() / "helloworld/.heavy-lock")))

# Parts fetched at run time (LCSC via easyeda2kicad, GitHub) live in parts/cache/<LIB>/ as
# <LIB>.kicad_sym, <LIB>.pretty/ and <LIB>.3dshapes/. Their library nickname starts with one of these.
PARTS_CACHE = ENGINE_DIR / "parts/cache"
CACHE_PREFIXES = ("LCSC_", "GH_")


# Manufacturer libraries cloned at a pinned tag into pcb-loop/vendor/ (provenance in each folder's PROVENANCE.md):
# library nickname -> clone root holding symbols/<lib>.kicad_sym, footprints/<lib>.pretty and 3dmodels/.
VENDOR_DIR = ENGINE_DIR.parent / "vendor"
VENDOR_LIBS = {"Espressif": VENDOR_DIR / "espressif-kicad-libraries"}


@functools.lru_cache(maxsize=1)
def _extra_paths() -> dict[str, dict[str, str]]:
    """Open-source libraries ingested from parts/extra_libs (written by agent/kicad_library.py)."""
    f = ENGINE_DIR / "parts/lib_paths.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return {"symbols": {}, "footprints": {}}


def symbol_lib_path(lib: str) -> Path:
    if lib.startswith(CACHE_PREFIXES):
        return PARTS_CACHE / lib / f"{lib}.kicad_sym"
    if lib in VENDOR_LIBS:
        return VENDOR_LIBS[lib] / "symbols" / f"{lib}.kicad_sym"
    extra = _extra_paths()["symbols"].get(lib)
    if extra and not (SYMBOL_DIR / f"{lib}.kicad_sym").exists():
        return Path(extra)
    return SYMBOL_DIR / f"{lib}.kicad_sym"


def footprint_root(lib: str) -> Path:
    """Folder that holds <lib>.pretty."""
    if lib in VENDOR_LIBS:
        return VENDOR_LIBS[lib] / "footprints"
    if lib.startswith(CACHE_PREFIXES):
        return PARTS_CACHE / lib
    extra = _extra_paths()["footprints"].get(lib)
    if extra and not (FOOTPRINT_DIR / f"{lib}.pretty").exists():
        return Path(extra)
    return FOOTPRINT_DIR


# Every export runs at lowest priority, as the heavy-job protocol asks.
NICE = ["nice", "-n", "19", "taskpolicy", "-b"]


class ToolError(RuntimeError):
    pass


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    nice: bool = False,
    check: bool = True,
    timeout: float = 300,
) -> subprocess.CompletedProcess[str]:
    full = (NICE + cmd) if nice else cmd
    proc = subprocess.run(full, check=False, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise ToolError(f"{' '.join(cmd[:3])} failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    return proc


def cli(*args: str, nice: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run([str(KICAD_CLI), *args], nice=nice, check=check)


def kicad_python(script: Path, *args: str, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    """Run a script under KiCad's bundled Python (it has the pcbnew bindings)."""
    return run([str(KICAD_PY), str(script), *args], timeout=timeout)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def heavy_lock(wait_s: float = 600, poll_s: float = 2.0):
    """~/helloworld/.heavy-lock protocol: atomic mkdir, owner PID, stale-lock takeover."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            HEAVY_LOCK.mkdir()
            (HEAVY_LOCK / "owner").write_text(str(os.getpid()))
            break
        except FileExistsError:
            owner = HEAVY_LOCK / "owner"
            try:
                pid = int(owner.read_text().strip())
            except (OSError, ValueError):
                pid = -1
            if pid > 0 and _pid_alive(pid):
                if time.monotonic() > deadline:
                    raise ToolError(f"heavy lock held by live PID {pid}; gave up after {wait_s:.0f} s")
                time.sleep(poll_s)
                continue
            # Stale lock: owner is dead (or the file is unreadable). Take it over.
            with contextlib.suppress(OSError):
                owner.unlink()
            with contextlib.suppress(OSError):
                HEAVY_LOCK.rmdir()
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            (HEAVY_LOCK / "owner").unlink()
        with contextlib.suppress(OSError):
            HEAVY_LOCK.rmdir()

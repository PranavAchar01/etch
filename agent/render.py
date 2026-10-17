"""KiCad 'basic' frames, then sprites (so explode works on live builds), in a background process.

Runs studio/kicad_frames.py --sprites at nice 19 / background QoS, small frames (1380 x 1020 by
default). The PID is written to <frames dir>/render.pid; frames.json and sprites.json appear as they finish.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import config
from .engine_api import url

MIN_FREE_GIB = 12.0  # small evaluation-size step: allowed at 12 GiB free and up (heavy steps need 15)


def free_gib() -> float:
    return shutil.disk_usage(str(config.ROOT)).free / 2**30


def start(bdir: Path) -> dict[str, Any]:
    pcb = next(bdir.glob("*.kicad_pcb"))
    out = bdir / "frames"
    if (out / "sprites.json").exists():
        return {
            "status": "ready",
            "frames": url(out / "frames.json"),
            "sprites": url(out / "sprites.json"),
        }
    free = free_gib()
    if free < MIN_FREE_GIB:
        return {
            "status": "skipped",
            "reason": f"only {free:.1f} GiB free (render needs {MIN_FREE_GIB})",
        }
    out.mkdir(exist_ok=True)
    # background priority: nice everywhere, plus macOS taskpolicy -b (absent on Linux)
    bg = ["taskpolicy", "-b"] if sys.platform == "darwin" else []
    cmd = [
        "nice",
        "-n",
        "19",
        *bg,
        "uv",
        "run",
        "--quiet",
        "--with",
        "numpy",
        "--with",
        "scipy",
        "--with",
        "pillow",
        "python",
        str(config.ROOT / "studio/kicad_frames.py"),
        str(pcb),
        str(out),
        "--w",
        str(config.FRAME_W),
        "--h",
        str(config.FRAME_H),
        "--jobs",
        "4",
        "--sprites",
    ]
    log = (out / "render.log").open("w")
    p = subprocess.Popen(
        cmd,
        cwd=str(config.ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (out / "render.pid").write_text(str(p.pid))
    return {
        "status": "started",
        "pid": p.pid,
        "frames": url(out / "frames.json"),
        "sprites": url(out / "sprites.json"),
        "free_gib": round(free, 1),
        "_proc": p,
    }


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True

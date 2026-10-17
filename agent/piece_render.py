"""One real KiCad render per catalog piece, shown on the page the moment the piece is confirmed.

Each piece is its own tiny board: the KiCad library footprint (with its library 3D model) on a green square,
rendered by kicad-cli at basic quality on a transparent background. Renders are cached on disk by a hash of
the piece key and view, so a piece costs one kicad-cli call the first time it is ever used and a file read
after that.

  uv run --project engine python -m agent.piece_render KEY [KEY ...]   # render (or reuse) pieces
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from etchlib.kicad import FOOTPRINT_DIR
from etchlib.kicad import (
    KICAD_CLI as KICAD,  # macOS app bundle or Linux /usr/bin/kicad-cli
)

from . import config

CACHE = config.ENGINE / "parts/piece_renders"
VIEW = ("--rotate", "-40,0,25", "--perspective", "--zoom", "0.62", "--quality", "basic")
W, H = 480, 380
VERSION = "v2"  # bump to re-render every piece after a view change

HEAD = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(general (thickness 1.6) (legacy_teardrops no))
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal) (2 "B.Cu" signal) (13 "F.Paste" user) (15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen") (7 "B.SilkS" user "B.Silkscreen") (1 "F.Mask" user) (3 "B.Mask" user)
\t\t(25 "Edge.Cuts" user) (31 "F.CrtYd" user "F.Courtyard") (29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user) (33 "B.Fab" user)
\t)
\t(setup
\t\t(stackup
\t\t\t(layer "F.SilkS" (type "Top Silk Screen") (color "White"))
\t\t\t(layer "F.Mask" (type "Top Solder Mask") (color "#0A6A38E6") (thickness 0.01))
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "core") (thickness 1.51) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
\t\t\t(layer "B.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (color "#0A6A38E6") (thickness 0.01))
\t\t\t(copper_finish "ENIG")
\t\t)
\t\t(pad_to_mask_clearance 0)
\t)
\t(net 0 "")
"""


def _png(footprint: str) -> Path:
    """Cache path, keyed by the footprint (two symbols on one footprint share a render)."""
    h = hashlib.sha1(f"{VERSION}|{footprint}|{VIEW}|{W}x{H}".encode()).hexdigest()[:16]
    return CACHE / f"{h}.png"


def cached(footprint: str) -> Path | None:
    p = _png(footprint)
    return p if p.exists() else None


def _mod_path(footprint: str) -> Path:
    lib, name = footprint.split(":", 1)
    return FOOTPRINT_DIR / f"{lib}.pretty" / f"{name}.kicad_mod"


def board_text(footprint: str, mod_text: str | None = None) -> str:
    """A one-part board: the library footprint at the origin on a square of board 1.5 mm past its outline."""
    mod = mod_text if mod_text is not None else _mod_path(footprint).read_text()
    xs, ys = [], []
    for m in re.finditer(
        r"\((?:start|end|at|xy|center|mid)\s+(-?[\d.]+)\s+(-?[\d.]+)", mod
    ):
        xs.append(float(m.group(1)))
        ys.append(float(m.group(2)))
    x0, x1 = (min(xs), max(xs)) if xs else (-2.0, 2.0)
    y0, y1 = (min(ys), max(ys)) if ys else (-2.0, 2.0)
    x0, y0, x1, y1 = x0 - 1.5, y0 - 1.5, x1 + 1.5, y1 + 1.5
    # kicad-cli frames the board, not the 3D body: a tall part (a 5 mm LED stands 9 mm) on a small square is cut
    # off at the top, so the square is at least 14 mm on a side
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half = max(7.0, (x1 - x0) / 2, (y1 - y0) / 2)
    if x1 - x0 < 14:
        x0, x1 = cx - half, cx + half
    if y1 - y0 < 14:
        y0, y1 = cy - half, cy + half
    body = re.sub(
        r"^\(footprint\s+\"[^\"]*\"",
        f'(footprint "{footprint}"\n\t\t(at 0 0)',
        mod.strip(),
        count=1,
    )
    body = re.sub(
        r"\n\s*\((?:version|generator|generator_version)\s+[^()]*\)", "", body
    )
    edge = (
        f"\t(gr_rect (start {x0:.2f} {y0:.2f}) (end {x1:.2f} {y1:.2f}) "
        '(stroke (width 0.1) (type default)) (fill no) (layer "Edge.Cuts"))\n'
    )
    return HEAD + "\t" + body + "\n" + edge + ")\n"


def render(footprint: str, nice: str = "10") -> tuple[Path, float, bool]:
    """(png, seconds, from_cache). kicad-cli runs under nice so a long batch job keeps most of the machine."""
    png = _png(footprint)
    if png.exists():
        return png, 0.0, True
    CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    # unique scratch names: the batch job and a live build may render the same piece at the same moment
    tag = f"{os.getpid()}-{threading.get_ident()}"
    src = png.with_name(f"{png.stem}.{tag}.kicad_pcb")
    tmp = png.with_name(f"{png.stem}.{tag}.tmp.png")
    src.write_text(board_text(footprint))
    try:
        subprocess.run(
            [
                "nice",
                "-n",
                "10",
                str(KICAD),
                "pcb",
                "render",
                *VIEW,
                "--use-board-stackup-colors",
                "--background",
                "transparent",
                "-w",
                str(W),
                "-h",
                str(H),
                "-o",
                str(tmp),
                str(src),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        tmp.replace(png)
    finally:
        src.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
        for prl in CACHE.glob(f"{png.stem}.{tag}*.kicad_prl"):
            prl.unlink(missing_ok=True)
    return png, round(time.time() - t0, 2), False


def render_many(pieces: list[dict[str, Any]], jobs: int = 4):
    """Yield (piece, png, seconds, cached) as each finishes; cached pieces come back first and at once."""
    hits = [p for p in pieces if cached(p["footprint"])]
    for p in hits:
        yield p, _png(p["footprint"]), 0.0, True
    todo = [p for p in pieces if p not in hits]
    if not todo:
        return
    with ThreadPoolExecutor(jobs) as ex:
        futs = {ex.submit(render, p["footprint"]): p for p in todo}
        for f in as_completed(futs):
            p = futs[f]
            try:
                png, s, c = f.result()
            except (subprocess.SubprocessError, OSError):
                continue  # a piece with no render just shows no tile; the board itself is unaffected
            yield p, png, s, c


if __name__ == "__main__":
    from . import kicad_catalog

    by = {p["key"]: p for p in kicad_catalog.load()["pieces"]}
    for k in sys.argv[1:]:
        print(k, render(by[k]["footprint"] if k in by else k))

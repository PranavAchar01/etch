"""Pre-render every KiCad library footprint that ships a 3D model, plus the footprint library index.

A heavy batch job (Pranav's ask, 2026-10-16): run it under `nice -n 19 taskpolicy -b`. It works in chunks; each
chunk holds ~/helloworld/.heavy-lock (etchlib.kicad.heavy_lock, which waits for any live owner such as a video
render), and between chunks it pauses while swap is over 10 GB. Resumable: a footprint already in the render
cache is skipped. The 291 catalog footprints go first.

  nice -n 19 taskpolicy -b uv run --project engine python -m agent.prerender_all          # render everything
  uv run --project engine python -m agent.prerender_all --index-only                       # just the index

Writes engine/parts/footprint_library.json: one row per footprint, keyed by "Lib:Name", with its description,
tags, pad count, 3D model and provenance (the official KiCad 10 footprint and 3D model libraries, shipped in
~/Applications/KiCad/KiCad.app; licence CC-BY-SA 4.0 with the KiCad libraries exception).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from etchlib.kicad import FOOTPRINT_DIR, MODEL_DIR, heavy_lock

from . import config, kicad_catalog, piece_render

INDEX = config.ENGINE / "parts/footprint_library.json"
PROGRESS = config.ENGINE / "parts/piece_renders/progress.json"
CHUNK = 150
SWAP_MAX_MB = 10 * 1024
PROVENANCE = {
    "source": "KiCad 10 official footprint and 3D model libraries (gitlab.com/kicad/libraries/kicad-footprints, "
    "kicad-packages3D), as shipped in KiCad.app",
    "licence": "CC-BY-SA 4.0 with the KiCad libraries exception",
}


def _model_file(mod: str) -> Path | None:
    m = re.search(r'\(model\s+"?([^"\s)]+)', mod)
    if not m:
        return None
    rel = re.sub(r"^\$\{KICAD\d*_3DMODEL_DIR\}/", "", m.group(1))
    p = MODEL_DIR / rel
    for cand in (p, p.with_suffix(".step"), p.with_suffix(".wrl")):
        if cand.exists():
            return cand
    return None


def build_index() -> list[dict]:
    rows = []
    for lib in sorted(FOOTPRINT_DIR.glob("*.pretty")):
        for f in sorted(lib.glob("*.kicad_mod")):
            mod = f.read_text(errors="replace")
            model = _model_file(mod)
            if not model:
                continue
            fp = f"{lib.stem}:{f.stem}"
            d = re.search(r'\(descr\s+"((?:[^"\\]|\\.)*)"', mod)
            t = re.search(r'\(tags\s+"((?:[^"\\]|\\.)*)"', mod)
            rows.append(
                {
                    "footprint": fp,
                    "library": lib.stem,
                    "descr": d.group(1) if d else "",
                    "tags": t.group(1) if t else "",
                    "pads": len(set(re.findall(r'\(pad\s+"([^"]+)"', mod))),
                    "tht": "thru_hole" in mod,
                    "model": str(model.relative_to(MODEL_DIR)),
                    "render": piece_render._png(fp).name,
                    "provenance": PROVENANCE,
                }
            )
    INDEX.write_text(
        json.dumps(
            {"count": len(rows), "provenance": PROVENANCE, "footprints": rows}, indent=0
        )
    )
    return rows


def _swap_mb() -> float:
    out = subprocess.run(
        ["sysctl", "vm.swapusage"], capture_output=True, text=True, check=False
    ).stdout
    m = re.search(r"used = ([\d.]+)M", out)
    return float(m.group(1)) if m else 0.0


def main(argv: list[str]) -> None:
    rows = build_index()
    print(f"index: {len(rows)} footprints with a 3D model -> {INDEX}", flush=True)
    if "--index-only" in argv:
        return
    first = {p["footprint"] for p in kicad_catalog.load()["pieces"]}
    order = [r["footprint"] for r in rows if r["footprint"] in first] + [
        r["footprint"] for r in rows if r["footprint"] not in first
    ]
    t0, done, failed = time.time(), 0, []
    while True:
        todo = [fp for fp in order if not piece_render.cached(fp) and fp not in failed]
        if not todo:
            break
        while (s := _swap_mb()) > SWAP_MAX_MB:
            print(f"swap {s / 1024:.1f} GB > 10 GB: paused", flush=True)
            time.sleep(120)
        with heavy_lock(wait_s=7 * 24 * 3600, poll_s=30):
            for fp in todo[:CHUNK]:
                try:
                    piece_render.render(fp, nice="19")
                    done += 1
                except (subprocess.SubprocessError, OSError) as e:
                    failed.append(fp)
                    print(f"failed {fp}: {str(e)[:120]}", flush=True)
        PROGRESS.write_text(
            json.dumps(
                {
                    "rendered_this_run": done,
                    "left": len(todo) - min(CHUNK, len(todo)),
                    "failed": failed,
                    "elapsed_s": round(time.time() - t0),
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        )
        print(
            f"{done} rendered, {len(todo) - min(CHUNK, len(todo))} left, {time.time() - t0:.0f} s",
            flush=True,
        )
    print(f"all done: {done} rendered this run, {len(failed)} failed", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])

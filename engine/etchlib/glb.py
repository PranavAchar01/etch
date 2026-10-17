"""GLB exports through kicad-cli: bare board, copper tracks, and one GLB per part."""

from __future__ import annotations

import concurrent.futures as cf
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import pygltflib

from .kicad import cli

# the finished board: real library STEP models plus copper, mask and silk; nodes are named by reference
FULL_FLAGS = [
    "--subst-models",
    "--include-tracks",
    "--include-pads",
    "--include-zones",
    "--include-silkscreen",
    "--include-soldermask",
]
BOARD_FLAGS = ["--no-components", "--include-pads", "--include-silkscreen", "--include-soldermask", "--include-zones"]
TRACK_FLAGS = ["--no-board-body", "--no-components", "--include-tracks"]


def _export(pcb: Path, out: Path, flags: list[str]) -> tuple[str, float, str]:
    t0 = time.time()
    # kicad-cli writes a fixed "$tempfile$" name next to the output, so parallel jobs need their own dir
    tmp = Path(tempfile.mkdtemp(prefix=".glb-", dir=out.parent))
    try:
        proc = cli("pcb", "export", "glb", "-f", *flags, "-o", str(tmp / out.name), str(pcb), nice=True, check=False)
        if (tmp / out.name).exists():
            shutil.move(str(tmp / out.name), str(out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = proc.returncode == 0 and out.exists()
    msg = "" if ok else (proc.stderr or proc.stdout)[-500:]
    # kicad-cli prints "Could not add 3D model" when a model path is wrong; treat that as a failure
    if "Could not add 3D model" in (proc.stdout + proc.stderr):
        msg = "missing 3D model: " + (proc.stdout + proc.stderr)[-300:]
    return str(out), time.time() - t0, msg


def export_all(pcb: Path, out_dir: Path, refs: list[str], workers: int = 4) -> dict[str, Any]:
    parts_dir = out_dir / "parts"
    parts_dir.mkdir(exist_ok=True)
    jobs = [
        (out_dir / "board.glb", FULL_FLAGS),
        (out_dir / "board_bare.glb", BOARD_FLAGS),
        (out_dir / "tracks.glb", TRACK_FLAGS),
    ]
    for ref in refs:
        jobs.append((parts_dir / f"{ref}.glb", ["--no-board-body", "--component-filter", ref]))
    results = {}
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for path, secs, msg in ex.map(lambda j: _export(pcb, j[0], j[1]), jobs):
            results[Path(path).name if "parts" not in path else "parts/" + Path(path).name] = {
                "s": round(secs, 2),
                "error": msg or None,
                "bytes": Path(path).stat().st_size if Path(path).exists() else 0,
            }
    return {"wall_s": round(time.time() - t0, 2), "files": results}


def _node_matrix(node: pygltflib.Node) -> np.ndarray:
    if node.matrix:
        return np.array(node.matrix, dtype=float).reshape(4, 4).T
    m = np.eye(4)
    if node.scale:
        m = m @ np.diag([*node.scale, 1.0])
    if node.rotation:
        x, y, z, w = node.rotation
        r = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ]
        )
        rm = np.eye(4)
        rm[:3, :3] = r
        m = rm @ m
    if node.translation:
        t = np.eye(4)
        t[:3, 3] = node.translation
        m = t @ m
    return m


def bounds(path: Path) -> dict[str, Any]:
    """World-space bounds from accessor min/max (cheap: no vertex decode)."""
    g = pygltflib.GLTF2().load(str(path))
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    names = []

    def walk(ni: int, parent: np.ndarray):
        nonlocal lo, hi
        node = g.nodes[ni]
        m = parent @ _node_matrix(node)
        if node.name:
            names.append(node.name)
        if node.mesh is not None:
            for prim in g.meshes[node.mesh].primitives:
                acc = g.accessors[prim.attributes.POSITION]
                if acc.min is None:
                    continue
                for corner in np.array(np.meshgrid(*zip(acc.min, acc.max))).T.reshape(-1, 3):
                    w = m @ np.append(corner, 1.0)
                    lo = np.minimum(lo, w[:3])
                    hi = np.maximum(hi, w[:3])
        for c in node.children or []:
            walk(c, m)

    for root in g.scenes[g.scene or 0].nodes:
        walk(root, np.eye(4))
    return {
        "min": lo.round(5).tolist(),
        "max": hi.round(5).tolist(),
        "nodes": len(g.nodes),
        "meshes": len(g.meshes),
        "names": names[:20],
    }

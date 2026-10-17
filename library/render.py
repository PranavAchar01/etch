"""Prerender fetched boards into deploy/etch-app/world/boards/<slug>/ (board.glb, thumb.webp, parts.json, meta.json).

    python3 library/render.py [--limit N] [--only slug ...] [--workers 3] [--force] [--budget-mb 150]

Per board:
  1. kicad-cli pcb export glb (library STEP models, copper, mask, silk; nodes named by refdes)
  2. library/optimize.mjs: dedup + join primitives per material + quantisation + meshopt, no simplification
     (PLAN.md rule 2), with the scene graph kept (no flatten/join/instancing), so every footprint stays its
     own named node; boards over --max-glb-mb (20) after that are skipped, never simplified
  3. kicad-cli pcb render (raytraced, perspective, 3/4 view) -> 1280x800 PNG -> 640x400 WebP
  4. parts.json from the .kicad_pcb (+ schematic descriptions), positions measured in the GLB frame
  5. meta.json records models {total, resolved, hidden, missing: [{ref, path, reason}]} (from fetch.py's
     models.json plus every model kicad-cli could not load) and identical (PLAN.md rule 4)
Only boards fetch.py marked identical are rendered unless --allow-missing.
Failures are logged to library/cache/render_log.json and the board is skipped.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import os
import re
import shutil
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

from common import LIB_DIR, BOARDS_JSON, scrub, RENDER_LOG, SRC, WORK, WORLD, kicad_cli, load_json, log, save_json
from pcbinfo import board_info, footprint_models, glb_bounds, glb_nodes

GLB_FLAGS = [
    "--subst-models",
    "--include-tracks",
    "--include-pads",
    "--include-zones",
    "--include-silkscreen",
    "--include-soldermask",
]
NODE_DIR = LIB_DIR / "cache" / "node"
NODE_LIBS = ["@gltf-transform/core@4.5.1", "@gltf-transform/functions@4.5.1", "@gltf-transform/extensions@4.5.1",
             "meshoptimizer@0.22.0"]
MAX_BYTES = 20_000_000  # --max-glb-mb: a board still bigger after lossless compression is skipped, not simplified
RENDER_W, RENDER_H = 1280, 800


def kicad_env(cli: Path) -> dict[str, str]:
    """Point every historical 3D-model variable at the installed library, so KiCad 5-9 boards find models."""
    shared = cli.parent.parent / "SharedSupport"
    env = dict(os.environ)
    models = str(shared / "3dmodels")
    for v in ["KISYS3DMOD", "KICAD_3DMODEL_DIR"] + [f"KICAD{n}_3DMODEL_DIR" for n in range(6, 12)]:
        env.setdefault(v, models)
    for v in ["KICAD_FOOTPRINT_DIR"] + [f"KICAD{n}_FOOTPRINT_DIR" for n in range(6, 12)]:
        env.setdefault(v, str(shared / "footprints"))
    return env


def run(cmd: list[str], *, timeout: int, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["nice", "-n", "10", *cmd], capture_output=True, text=True, timeout=timeout, env=env,
                              cwd=cwd)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"timeout after {timeout}s")


def kicad_version(cli: Path) -> str:
    p = subprocess.run([str(cli), "version"], capture_output=True, text=True)
    return p.stdout.strip().split()[-1] if p.returncode == 0 and p.stdout.strip() else "unknown"


def ensure_node_libs() -> None:
    if (NODE_DIR / "node_modules" / "@gltf-transform" / "functions").exists():
        return
    NODE_DIR.mkdir(parents=True, exist_ok=True)
    (NODE_DIR / "package.json").write_text('{"private": true}')
    subprocess.run(["npm", "install", "--silent", *NODE_LIBS], cwd=NODE_DIR, check=True)


def optimize(raw: Path, out: Path, max_bytes: int = MAX_BYTES) -> tuple[bool, str]:
    """library/optimize.mjs: lossless-looking compression only (join prims per material, quantize, meshopt)."""
    out.unlink(missing_ok=True)
    p = run(["node", str(LIB_DIR / "optimize.mjs"), str(raw), str(out)], timeout=1800)
    if p.returncode != 0 or not out.exists():
        return False, (p.stderr or p.stdout)[-300:]
    if out.stat().st_size > max_bytes:
        return False, f"GLB is {out.stat().st_size / 1e6:.1f} MB after lossless compression (cap {max_bytes / 1e6:.0f} MB)"
    return True, ""


def thumb(cli: Path, env: dict, pcb: Path, work: Path, out: Path, size_mm: list[float]) -> tuple[bool, str]:
    png = work / "thumb.png"
    png.unlink(missing_ok=True)
    cmd = [str(cli), "pcb", "render", "-o", str(png), "--width", str(RENDER_W), "--height", str(RENDER_H),
           "--side", "top", "--perspective", "--quality", "high", "--use-board-stackup-colors", "--background", "opaque",
           "--rotate", "-40,0,65" if size_mm[1] > size_mm[0] * 1.25 else "-40,0,-25",  # long axis across the frame
           "--zoom", "0.8", str(pcb)]
    p = run(cmd, timeout=600, env=env)
    if not png.exists():
        return False, "render: " + (p.stderr or p.stdout)[-300:]
    q = run(["cwebp", "-quiet", "-q", "82", "-resize", "640", "400", str(png), "-o", str(out)], timeout=120)
    if q.returncode != 0 or not out.exists():
        return False, "cwebp: " + q.stderr[-200:]
    return True, ""


def match_node(ref: str, names: dict[str, list[float]]) -> str | None:
    if ref in names:
        return ref
    for sep in ("_", ".", " ", ":", "-"):
        for n in names:
            if n.startswith(ref + sep):
                return n
    return None


def render_board(b: dict, cli: Path, env: dict, kver: str, force: bool, max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    slug = b["slug"]
    src = SRC / slug
    pcb = src / "board.kicad_pcb"
    out = WORLD / "boards" / slug
    work = WORK / slug
    if not pcb.exists():
        return {"ok": False, "error": "not fetched"}
    if not force and (out / "meta.json").exists() and (out / "board.glb").exists() and (out / "thumb.webp").exists():
        return {"ok": True, "cached": True}
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    try:
        info = board_info(pcb, src / "sch")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"parse: {e}"}
    if not info["parts"]:
        return {"ok": False, "error": "no footprints"}

    raw = work / "raw.glb"
    raw.unlink(missing_ok=True)
    p = run([str(cli), "pcb", "export", "glb", "-f", *GLB_FLAGS, "-o", str(raw), str(pcb)], timeout=900, env=env)
    # kicad-cli exits non-zero when any model fails to load but still writes the GLB; judge by the file
    if not raw.exists() or raw.stat().st_size < 1000:
        return {"ok": False, "error": "export glb: " + (p.stderr or p.stdout)[-300:].strip()}
    cli_out = p.stdout + p.stderr
    models = load_json(src / "models.json", {"total": 0, "resolved": 0, "hidden": 0, "missing": []})
    # models that resolved to a file but that kicad-cli could not load also count as missing
    known = {m["ref"] for m in models["missing"]}
    # "No model for filename '<path>'": a model file that resolved but that kicad-cli (OpenCASCADE) cannot read
    by_path: dict[str, list[str]] = {}
    try:
        for e in footprint_models(pcb):
            if not e["hidden"]:
                by_path.setdefault(e["path"], []).append(e["ref"])
    except Exception:  # noqa: BLE001
        pass
    for bad in dict.fromkeys(re.findall(r"No model for filename '([^']+)'", cli_out)):
        for ref in by_path.get(bad, ["?"]):
            models["missing"].append({"ref": ref, "path": scrub(bad),
                                      "reason": "kicad-cli could not read the model file"})
            known.add(ref)
            models["identical"] = False
    for line in cli_out.splitlines():
        m = re.search(r"Could not add 3D model for (.+?)\.?$", line.strip())
        if m and m.group(1) not in known:
            known.add(m.group(1))
            models["missing"].append({"ref": m.group(1), "path": "?", "reason": "kicad-cli could not load the model file"})
            models["identical"] = False
    models["resolved"] = max(0, models["total"] - len(models["missing"]))
    identical = bool(models.get("identical", not models["missing"]))

    opt = work / "board.glb"
    ok, msg = optimize(raw, opt, max_bytes)
    if not ok:
        return {"ok": False, "error": "optimize: " + msg, "raw_bytes": raw.stat().st_size}

    th = work / "thumb.webp"
    ok, msg = thumb(cli, env, pcb, work, th, info["size_mm"])
    if not ok:
        return {"ok": False, "error": msg}

    # positions in the GLB frame: node origins where a node exists, else the pcb position shifted by the
    # median pcb->GLB offset measured on the parts that do have nodes
    names = glb_nodes(opt)
    offs = []
    for ref, part in info["parts"].items():
        n = match_node(ref, names)
        part["node"] = n
        if n:
            g = names[n]
            offs.append((g[0] * 1000 - part["pcb_mm"][0], g[2] * 1000 - part["pcb_mm"][1]))
    lo, hi = glb_bounds(opt)
    if offs:
        ox = statistics.median(o[0] for o in offs)
        oy = statistics.median(o[1] for o in offs)
    else:  # no models at all: line the outline centre up with the GLB centre
        bx = (info["bbox_mm"][0] + info["bbox_mm"][2]) / 2
        by = (info["bbox_mm"][1] + info["bbox_mm"][3]) / 2
        ox, oy = (lo[0] + hi[0]) / 2 * 1000 - bx, (lo[2] + hi[2]) / 2 * 1000 - by
    parts = {}
    for ref, part in sorted(info["parts"].items(), key=lambda kv: kv[0]):
        n = part["node"]
        pos = [names[n][0] * 1000, names[n][2] * 1000] if n else [part["pcb_mm"][0] + ox, part["pcb_mm"][1] + oy]
        parts[ref] = {
            "ref": ref, "value": part["value"], "footprint": part["footprint"], "description": part["description"],
            "pos_mm": [round(pos[0], 3), round(pos[1], 3)], "side": part["side"], "rot": part["rot"], "node": n,
        }

    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(opt, out / "board.glb")
    shutil.copy2(th, out / "thumb.webp")
    save_json(out / "parts.json", {"parts": parts}, indent=None)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha = b["commit"]
    meta = {
        "id": b["id"], "slug": slug, "title": b["title"], "owner": b["owner"], "repo": b["repo"],
        "category": b["category"], "stars": b["stars"], "license": b["license"], "copyable": b["copyable"],
        "layers": info["layers"], "size_mm": info["size_mm"], "parts": len(parts),
        "glb_bytes": (out / "board.glb").stat().st_size,
        "url": f"https://github.com/{b['owner']}/{b['repo']}",
        "commit": sha,
        "pcb_path": b["pcb_path"],
        "description": b["description"],
        "license_url": f"https://github.com/{b['owner']}/{b['repo']}/blob/{sha[:7]}/{b.get('license_path') or 'LICENSE'}",
        "author_url": f"https://github.com/{b['owner']}",
        "topics": b["topics"],
        "board": {"mask": info["mask"], "silk": info["silk"], "finish": info["finish"],
                  "thickness_mm": info["thickness_mm"]},
        "models": {"total": models["total"], "resolved": models["resolved"], "hidden": models.get("hidden", 0),
                   "missing": models["missing"]},
        "identical": identical,
        "glb_bounds_m": {"min": [round(v, 5) for v in lo], "max": [round(v, 5) for v in hi]},
        "rendered": now,
        "kicad": kver,
    }
    save_json(out / "meta.json", meta)
    shutil.rmtree(work, ignore_errors=True)
    return {"ok": True, "seconds": round(time.time() - t0, 1), "glb_bytes": meta["glb_bytes"],
            "raw_bytes": raw.stat().st_size if raw.exists() else None, "nodes_matched": len(offs),
            "parts": len(parts), "missing_models": len(models["missing"]), "identical": identical}


def world_bytes() -> int:
    return sum(f.stat().st_size for f in WORLD.rglob("*") if f.is_file()) if WORLD.exists() else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after this many boards rendered OK (incl. cached)")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--allow-missing", action="store_true",
                    help="also render boards with unresolved 3D models (identical: false); default renders only "
                         "boards where every model KiCad would draw resolved")
    ap.add_argument("--budget-mb", type=float, default=150.0, help="stop when world/ grows past this")
    ap.add_argument("--max-glb-mb", type=float, default=MAX_BYTES / 1e6,
                    help="skip boards bigger than this after compression")
    args = ap.parse_args()

    cli = kicad_cli()
    if not cli:
        raise SystemExit("kicad-cli not found (install KiCad, or set KICAD_CLI)")
    env = kicad_env(cli)
    ensure_node_libs()
    kver = kicad_version(cli)
    boards = [b for b in load_json(BOARDS_JSON, []) if (SRC / b["slug"] / "board.kicad_pcb").exists()]
    if args.only:
        boards = [b for b in boards if b["slug"] in args.only]
    elif not args.allow_missing:
        boards = [b for b in boards if load_json(SRC / b["slug"] / "models.json", {}).get("identical")]
    logd: dict[str, Any] = load_json(RENDER_LOG, {})
    if not args.retry_failed and not args.force and not args.only:
        boards = [b for b in boards if logd.get(b["slug"], {}).get("ok", True)]

    done = sum(1 for b in boards if (WORLD / "boards" / b["slug"] / "meta.json").exists())
    todo = iter(boards)
    with cf.ThreadPoolExecutor(args.workers) as ex:
        running: dict[cf.Future, dict] = {}

        def submit() -> bool:
            for b in todo:
                running[ex.submit(render_board, b, cli, env, kver, args.force, int(args.max_glb_mb * 1e6))] = b
                return True
            return False

        for _ in range(args.workers):
            submit()
        while running:
            fut = next(cf.as_completed(running))
            b = running.pop(fut)
            try:
                res = fut.result()
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "error": f"crash: {e}"}
            if res.get("error"):
                res["error"] = scrub(res["error"])
            if not res.get("cached"):
                logd[b["slug"]] = res
                save_json(RENDER_LOG, logd)
                if res["ok"]:
                    done += 1
                log(f"{b['slug']:55s} " + (f"ok {res['glb_bytes'] / 1e6:.2f} MB {res['seconds']}s nodes "
                                           f"{res['nodes_matched']}/{res['parts']}" if res["ok"] else f"SKIP {res['error'][:160]}"))
            size = world_bytes() / 1e6
            if (args.limit and done >= args.limit) or size > args.budget_mb:
                log(f"stopping: {done} boards, world/ {size:.1f} MB")
                break
            submit()
        for f in running:
            f.cancel()
    log(f"rendered boards: {done}, world/ {world_bytes() / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

"""Prerender fetched boards into deploy/etch-app/world/boards/<slug>/ (board.glb, thumb.webp, parts.json, meta.json).

    python3 library/render.py [--limit N] [--only slug ...] [--workers 3] [--force] [--budget-mb 150]
    python3 library/render.py --thumbs-only [--only slug ...] [--workers 4]   # thumbnails only

Per board:
  1. kicad-cli pcb export glb (library STEP models, copper, mask, silk; nodes named by refdes)
  2. library/optimize.mjs: dedup + join primitives per material + quantisation + meshopt, no simplification
     (PLAN.md rule 2), with the scene graph kept (no flatten/join/instancing), so every footprint stays its
     own named node; boards over --max-glb-mb (20) after that are skipped, never simplified
  3. kicad-cli pcb render (raytraced, perspective, 3/4 view, one fixed look; zoom/pan from basic-quality probes so
     every board fills the same share of the frame) -> 1280x800 PNG -> 640x400, +-15% exposure trim -> WebP
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

import numpy as np
from PIL import Image, ImageFilter

from common import LIB_DIR, BOARDS_JSON, scrub, RENDER_LOG, SRC, WORK, WORLD, kicad_cli, load_json, log, save_json
from pcbinfo import board_info, footprint_models, glb_bounds, glb_nodes
from thumbcheck import board_mask, luminance, measure

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


# One look for every thumbnail: the follow_pcb_editor preset on a scratch copy of the board with every layer
# visible (old boards hide layers in their (layers ...) block, newer ones in a .kicad_prl, which is left out), so
# no project's own plot or visibility settings leak in; explicit lights, the board's own stackup colours, the same
# floor/contact shadow, perspective and camera elevation. Long boards turn 90 degrees about Z so the long axis runs
# across the frame.
THUMB_LOOK = ["--side", "top", "--perspective", "--preset", "follow_pcb_editor", "--use-board-stackup-colors",
              "--background", "opaque", "--floor", "--light-top", "0.30", "--light-bottom", "0.10",
              "--light-side", "0.17", "--light-camera", "0.22", "--light-side-elevation", "60"]
THUMB_ROT = ("-40,0,-25", "-40,0,65")
THUMB_FILL = 0.78    # board bbox / frame, on whichever axis binds
THUMB_Y = 0.12       # target mean linear luminance of the board pixels
THUMB_GAIN = 0.15    # exposure normalisation is clamped to +-15%
PAN_X, PAN_Y = 0.049, -0.040  # image shift (fraction of width / height) per --pan unit at --zoom 1, measured


def _frame(png: Path) -> Image.Image:
    """Kicad-cli writes 1264x784 for a 1280x800 request; crop the backdrop to 16:10 so the resize keeps shape."""
    im = Image.open(png).convert("RGB")
    w, h = im.size
    cw = min(w, round(h * 1.6))
    ch = min(h, round(cw / 1.6))
    x, y = (w - cw) // 2, (h - ch) // 2
    return im.crop((x, y, x + cw, y + ch))


def thumb(cli: Path, env: dict, pcb: Path, work: Path, out: Path, size_mm: list[float]) -> tuple[bool, str]:
    # a scratch project: the board with its layer "hide" flags dropped and its .kicad_pro (text variables), no
    # .kicad_prl (kicad-cli also writes one there, never into the source folder)
    proj = work / "thumbproj"
    shutil.rmtree(proj, ignore_errors=True)
    proj.mkdir(parents=True)
    text = pcb.read_text(errors="replace")
    i = text.find("(layers")  # the board's layer table (the first "(layers" in the file), up to its closing paren
    j, depth = i, 0
    while 0 <= j < len(text):
        depth += {"(": 1, ")": -1}.get(text[j], 0)
        if depth == 0:
            text = text[:i] + re.sub(r"\s+hide\)", ")", text[i:j]) + text[j:]
            break
        j += 1
    (proj / "board.kicad_pcb").write_text(text)
    if (pcb.parent / "board.kicad_pro").exists():
        shutil.copy2(pcb.parent / "board.kicad_pro", proj / "board.kicad_pro")
    board = proj / "board.kicad_pcb"
    rot = THUMB_ROT[1] if size_mm[1] > size_mm[0] * 1.25 else THUMB_ROT[0]
    png = work / "thumb.png"

    def render(quality: str, zoom: float, pan: tuple[float, float]) -> tuple[bool, str]:
        png.unlink(missing_ok=True)
        cmd = [str(cli), "pcb", "render", "-o", str(png), "--width", str(RENDER_W), "--height", str(RENDER_H),
               *THUMB_LOOK, "--quality", quality, "--rotate", rot, "--zoom", f"{zoom:.4f}",
               "--pan", f"{pan[0]:.4f},{pan[1]:.4f},0", str(board)]
        p = run(cmd, timeout=600, env=env)
        return png.exists(), (p.stderr or p.stdout)[-300:]

    # framing: fast basic-quality probes, measured like the final image, set zoom and pan so the board's bbox
    # fills THUMB_FILL of the binding axis, centred (the image shift per pan unit scales with zoom)
    # first probe sets the zoom; later ones move the pan with a secant estimate of image shift per pan unit
    # (perspective makes it vary per board), re-zooming only when the fill is off by more than 0.03
    zoom, pan = 0.6, [0.0, 0.0]
    gain = [PAN_X * zoom, PAN_Y * zoom]
    prev = None
    for k in range(6):
        ok, err = render("basic", zoom, tuple(pan))
        if not ok:
            return False, "render: " + err
        m = measure(_frame(png))
        if os.environ.get("THUMB_DEBUG"):
            log(f"{pcb.parent.name[:40]} probe z {zoom:.3f} pan {pan[0]:.2f},{pan[1]:.2f} -> fill {m['fill']:.3f} "
                f"c {m['cx']:.3f},{m['cy']:.3f}")
        if m["px"] == 0:
            break
        c = [m["cx"] - 0.5, m["cy"] - 0.5]
        if abs(m["fill"] - THUMB_FILL) < 0.015 and max(abs(c[0]), abs(c[1])) < 0.01:
            break
        if prev:
            for ax in (0, 1):
                dp = pan[ax] - prev[0][ax]
                if abs(dp) > 0.05:
                    g = (c[ax] - prev[1][ax]) / dp
                    if g * gain[ax] > 0:
                        gain[ax] = g
        prev = (list(pan), c)
        if k > 0:
            pan = [pan[ax] - c[ax] / gain[ax] for ax in (0, 1)]
        if k == 0 or abs(m["fill"] - THUMB_FILL) > 0.03:
            z1 = min(20.0, max(0.1, zoom * THUMB_FILL / max(m["fill"], 1e-3)))
            gain = [g * z1 / zoom for g in gain]
            zoom, prev = z1, None
    ok, err = render("high", zoom, tuple(pan))
    if not ok:
        return False, "render: " + err
    if os.environ.get("THUMB_DEBUG"):
        m = measure(_frame(png))
        log(f"{pcb.parent.name[:40]} high  z {zoom:.3f} -> fill {m['fill']:.3f} c {m['cx']:.3f},{m['cy']:.3f}")

    # light exposure normalisation on the board pixels only (feathered mask), backdrop untouched
    im = _frame(png).resize((640, 400), Image.LANCZOS)
    rgb = np.asarray(im)
    mask = board_mask(rgb)
    if mask.any():
        gain = float(np.clip(THUMB_Y / max(float(luminance(rgb)[mask].mean()), 1e-4), 1 - THUMB_GAIN, 1 + THUMB_GAIN))
        a = np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2)),
                       np.float32)[..., None] / 255
        f = rgb.astype(np.float32) / 255
        lin = np.where(f <= 0.04045, f / 12.92, ((f + 0.055) / 1.055) ** 2.4)
        lin = np.clip(lin * (1 + (gain - 1) * a), 0, 1)
        f = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)
        im = Image.fromarray(np.round(f * 255).astype(np.uint8))
    small = work / "thumb640.png"
    im.save(small)
    q = run(["cwebp", "-quiet", "-q", "82", str(small), "-o", str(out)], timeout=120)
    shutil.rmtree(proj, ignore_errors=True)
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


def rethumb(b: dict, cli: Path, env: dict) -> dict[str, Any]:
    """--thumbs-only: re-render thumb.webp for an already rendered board; GLB, parts and meta stay as they are."""
    slug = b["slug"]
    out = WORLD / "boards" / slug
    meta = load_json(out / "meta.json", {})
    pcb = SRC / slug / "board.kicad_pcb"
    if not meta or not pcb.exists():
        return {"ok": False, "error": "not rendered yet"}
    t0 = time.time()
    work = WORK / (slug + "__thumb")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    th = work / "thumb.webp"
    ok, msg = thumb(cli, env, pcb, work, th, meta["size_mm"])
    if ok:
        shutil.copy2(th, out / "thumb.webp")
    shutil.rmtree(work, ignore_errors=True)
    return {"ok": ok, "error": msg, "seconds": round(time.time() - t0, 1)}


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
    ap.add_argument("--thumbs-only", action="store_true",
                    help="re-render thumb.webp for boards already in world/ (no GLB, parts or meta changes)")
    args = ap.parse_args()

    cli = kicad_cli()
    if not cli:
        raise SystemExit("kicad-cli not found (install KiCad, or set KICAD_CLI)")
    env = kicad_env(cli)
    if args.thumbs_only:
        slugs = args.only or sorted(p.parent.name for p in (WORLD / "boards").glob("*/meta.json"))
        with cf.ThreadPoolExecutor(args.workers) as ex:
            futs = {ex.submit(rethumb, {"slug": s}, cli, env): s for s in slugs}
            for fut in cf.as_completed(futs):
                res = fut.result()
                log(f"{futs[fut]:55s} " + (f"thumb ok {res['seconds']}s" if res["ok"] else f"SKIP {scrub(res['error'])[:160]}"))
        return
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

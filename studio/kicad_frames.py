"""Render a build as KiCad 'basic' frames, one per placed part.

Each frame is the real board with only the first N footprints present, rendered by
kicad-cli at basic quality. Frames render in parallel, so a whole build costs about
one render per CPU core.

  python3 studio/kicad_frames.py BOARD.kicad_pcb OUTDIR [--order U1,J1,...] [--w 1600 --h 900] [--jobs N]
      [--zoom 1.3] [--rotate -40,0,25] [--tracks-first]

--zoom and --rotate change the camera for every frame (defaults are the studio camera; a bottom view is
--rotate 140,0,-25). --tracks-first keeps the copper on every frame instead of adding it last.
"""

import argparse
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

KICAD = os.path.join(os.environ.get("ETCH_KICAD_APP", os.path.expanduser("~/Applications/KiCad/KiCad.app")), "Contents/MacOS/kicad-cli")
VIEW = [
    "--rotate",
    "-40,0,25",
    "--perspective",
    "--zoom",
    "1.3",
    "--quality",
    "basic",
]
ORDER = ["U", "J", "Q", "D", "L", "Y", "SW", "C", "R", "F", "TP", "H"]
MASK = None  # --mask-color: solder-mask colour for the render copies only (the board file is not changed)
POUR = True  # False: leave copper pours out of the render copies, so the traces read as etched lines instead of a copper sheet


def mask_color(text, color=None):
    """Swap the stackup solder-mask colour in a render copy, e.g. a lighter, more translucent green so traces read
    through the mask as on a real board. Only the temporary file handed to kicad-cli changes."""
    color = color or MASK
    if not color:
        return text
    return re.sub(r'(\(layer "[FB]\.Mask"\s*\(type "[^"]*"\)\s*\(color )"[^"]*"', lambda m: m.group(1) + f'"{color}"', text)


def blocks(text, head):
    """Yield (start, end) spans of top-level s-expressions that open with `(head`."""
    i = 0
    pat = re.compile(r"\n\t?\(" + head + r"[\s\"]")
    while m := pat.search(text, i):
        s = m.start() + 1
        depth, j, instr = 0, s, False
        while True:
            c = text[j]
            if c == '"' and text[j - 1] != "\\":
                instr = not instr
            elif not instr:
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        yield s, j + 1
        i = j + 1


def ref_of(fp):
    m = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', fp) or re.search(
        r'\(fp_text\s+reference\s+"?([^"\s)]+)', fp
    )
    return m.group(1) if m else ""


def rank(ref):
    p = re.match(r"[A-Z]+", ref).group(0) if re.match(r"[A-Z]+", ref) else ""
    return (
        ORDER.index(p) if p in ORDER else len(ORDER),
        int(re.sub(r"\D", "", ref) or 0),
    )


def set_view(zoom=1.3, rotate="-40,0,25"):
    VIEW[1], VIEW[4] = rotate, str(zoom)


def frames(pcb, out, order=None, w=1600, h=900, jobs=None, tracks_last=True, tracks_first=False):
    text = Path(pcb).read_text()
    fps = [(s, e, ref_of(text[s:e])) for s, e in blocks(text, "footprint")]
    # footprints with no 3D model (test points, holes, logos) stay on the bare board
    modeled = {r for s, e, r in fps if "(model" in text[s:e]}
    fps = [f for f in fps if f[2] in modeled]
    refs = order or sorted((r for _, _, r in fps), key=rank)
    trk = [
        (s, e)
        for head in ("segment", "via", "arc") + (("zone",) if POUR else ())
        for s, e in blocks(text, head)
    ]
    pour = [] if POUR else list(blocks(text, "zone"))  # a list: the render threads all read it
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    def variant(n, with_tracks):
        keep = set(refs[:n])
        cut = [(s, e) for s, e, r in fps if r not in keep]
        if not with_tracks and not tracks_first:
            cut += trk
        cut += pour
        cut.sort()
        parts, i = [], 0
        for s, e in cut:
            parts.append(text[i:s])
            i = e
        parts.append(text[i:])
        return "".join(parts)

    steps = [(0, False, "board")] + [
        (n, False, refs[n - 1]) for n in range(1, len(refs) + 1)
    ]
    if tracks_last and not tracks_first:
        steps.append((len(refs), True, "traces"))

    def render(k):
        n, wt, label = steps[k]
        src = out / f"f{k:03d}.kicad_pcb"
        src.write_text(mask_color(variant(n, wt)))
        png = out / f"f{k:03d}.png"
        t0 = time.time()
        subprocess.run(
            [
                "nice",
                "-n",
                "10",
                KICAD,
                "pcb",
                "render",
                *VIEW,
                "-w",
                str(w),
                "-h",
                str(h),
                "-o",
                str(png),
                str(src),
            ],
            capture_output=True,
            check=True,
        )
        src.unlink()
        return {
            "i": k,
            "label": label,
            "png": png.name,
            "s": round(time.time() - t0, 2),
        }

    t0 = time.time()
    meta = {"pcb": str(pcb), "w": w, "h": h, "total": len(steps), "labels": [s[2] for s in steps], "frames": [], "done": False}

    def write():
        tmp = out / "frames.json.tmp"
        tmp.write_text(json.dumps(meta))
        tmp.replace(out / "frames.json")

    write()
    with ThreadPoolExecutor(jobs or os.cpu_count()) as ex:
        # frames are published as they finish so the app can start playing at once
        for f in as_completed([ex.submit(render, k) for k in range(len(steps))]):
            meta["frames"].append(f.result())
            meta["frames"].sort(key=lambda x: x["i"])
            write()
    # one sharp copy of the finished board for zooming in
    n, wt, _ = steps[-1]
    src = out / "hi.kicad_pcb"
    src.write_text(mask_color(variant(n, wt)))
    subprocess.run(["nice", "-n", "10", KICAD, "pcb", "render", *VIEW, "-w", str(w * 2), "-h", str(h * 2), "-o", str(out / "hi.png"), str(src)],
                   capture_output=True)
    src.unlink()
    meta.update(done=True, hi="hi.png", wall_s=round(time.time() - t0, 2))
    write()
    return meta


def at_of(fp):
    m = re.search(r"\(at\s+([-\d.]+)\s+([-\d.]+)", fp)
    return [float(m.group(1)), float(m.group(2))] if m else [0.0, 0.0]


def board_to_px(parts):
    """Homography from board mm to image px, fitted on the low parts (their centres sit nearly on the board)."""
    import numpy as np

    low = [p for p in parts if p["px"] < 6000] or parts
    if len(low) < 4:
        return None
    A = []
    for p in low:
        (x, y), (u, v) = p["mm"], (p["cx"], p["cy"])
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    h = np.linalg.svd(np.array(A))[2][-1]
    return [round(float(v), 9) for v in (h / h[-1])]


def sprites(pcb, out, meta, pad=3):
    """Cut every placed part out of the frames as its own sprite, plus a routed board with no parts.

    Frame k shows parts 1..k, so the pixels that change from frame k-1 to k are part k.
    The app stacks base + sprites to rebuild the board and lifts each sprite to explode it.
    Needs numpy, scipy and pillow (`uv run --with numpy --with scipy --with pillow`).
    """
    import numpy as np
    from PIL import Image
    from scipy import ndimage as nd

    out = Path(out)
    text = Path(pcb).read_text()
    pos = {ref_of(text[s:e]): at_of(text[s:e]) for s, e in blocks(text, "footprint")}
    keep_tracks = text
    for s, e in sorted((s, e) for s, e in blocks(text, "footprint") if "(model" in text[s:e])[::-1]:
        keep_tracks = keep_tracks[:s] + keep_tracks[e:]
    src = out / "base.kicad_pcb"
    src.write_text(mask_color(keep_tracks))
    subprocess.run(["nice", "-n", "10", KICAD, "pcb", "render", *VIEW, "-w", str(meta["w"]), "-h", str(meta["h"]),
                    "-o", str(out / "base.png"), str(src)], capture_output=True, check=True)
    src.unlink()
    load = lambda k: np.asarray(Image.open(out / f"f{k:03d}.png").convert("RGBA")).astype(np.int16)
    # copper layer on its own: what routing adds to the bare board
    bare, routed = load(0), np.asarray(Image.open(out / "base.png").convert("RGBA")).astype(np.int16)
    cu = routed.astype(np.uint8).copy()
    cu[..., 3] = np.where(np.abs(routed - bare).max(axis=2) > 8, cu[..., 3], 0)
    Image.fromarray(cu, "RGBA").save(out / "copper.png", compress_level=1)
    parts, prev = [], bare
    for k, label in enumerate(meta["labels"]):
        if k == 0 or label == "traces":
            continue
        cur = load(k)
        d = np.abs(cur - prev).max(axis=2)
        prev = cur
        # renders jitter by a subpixel between runs, which lights up thin edges all over the board;
        # keep only solid blobs of change (the new part) and their soft edges
        strong = nd.binary_opening(d > 30, iterations=2)
        lab, n = nd.label(strong)
        if n:
            sz = nd.sum(strong, lab, range(1, n + 1))
            big = [i + 1 for i, v in enumerate(sz) if v >= max(150, 0.12 * sz.max())]
            strong = np.isin(lab, big)
        m = (d > 8) & nd.binary_dilation(strong, iterations=4)
        if not m.any():
            continue
        ys, xs = np.nonzero(m)
        y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, m.shape[0])
        x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, m.shape[1])
        # grow the mask a little so anti-aliased edges come along
        mm = m[y0:y1, x0:x1]
        g = mm.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            g |= np.roll(mm, (dy, dx), axis=(0, 1))
        rgba = cur[y0:y1, x0:x1].astype(np.uint8).copy()
        rgba[..., 3] = np.where(g, rgba[..., 3], 0)
        name = f"s{k:03d}.png"
        Image.fromarray(rgba, "RGBA").save(out / name, optimize=False, compress_level=1)
        parts.append({"ref": label, "png": name, "x": int(x0), "y": int(y0), "w": int(x1 - x0), "h": int(y1 - y0),
                      "cx": round(float(xs.mean()), 1), "cy": round(float(ys.mean()), 1), "px": int(m.sum()),
                      "mm": pos.get(label, [0, 0])})
    sp = {"w": meta["w"], "h": meta["h"], "bare": "f000.png", "base": "base.png", "copper": "copper.png", "parts": parts, "H": board_to_px(parts)}
    (out / "sprites.json").write_text(json.dumps(sp))
    return sp


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("pcb")
    a.add_argument("out")
    a.add_argument("--order")
    a.add_argument("--w", type=int, default=1600)
    a.add_argument("--h", type=int, default=900)
    a.add_argument("--jobs", type=int)
    a.add_argument("--sprites", action="store_true")
    a.add_argument("--zoom", type=float, default=1.3)
    a.add_argument("--rotate", default="-40,0,25")
    a.add_argument("--tracks-first", action="store_true")
    a.add_argument("--ortho", action="store_true", help="orthographic camera (flat top-down when --rotate 0,0,0)")
    a.add_argument("--stackup-colors", action="store_true", help="mask and silk colours from the board stackup")
    a.add_argument("--mask-color", help="render the solder mask in this #RRGGBBAA colour (render copies only)")
    a.add_argument("--quality", default="basic", choices=["basic", "high", "user", "job_settings"],
                   help="kicad-cli render quality; high = raytraced (about 20 s a frame at 1380x1020)")
    x = a.parse_args()
    set_view(x.zoom, x.rotate)
    MASK = x.mask_color
    if x.ortho:
        VIEW.remove("--perspective")
    if x.stackup_colors:
        VIEW.append("--use-board-stackup-colors")
    VIEW[VIEW.index("--quality") + 1] = x.quality
    a2 = Path(x.out) / "frames.json"
    if x.sprites and a2.exists() and json.loads(a2.read_text()).get("done") and json.loads(a2.read_text())["w"] == x.w:
        m = json.loads(a2.read_text())
    else:
        m = frames(x.pcb, x.out, x.order.split(",") if x.order else None, x.w, x.h, x.jobs, tracks_first=x.tracks_first)
    if x.sprites:
        t1 = time.time()
        sp = sprites(x.pcb, x.out, m)
        print(json.dumps({"sprites": len(sp["parts"]), "sprites_s": round(time.time() - t1, 2)}))
    per = [f["s"] for f in m["frames"]]
    print(
        json.dumps(
            {
                "frames": len(per),
                "wall_s": m["wall_s"],
                "per_frame_avg_s": round(sum(per) / len(per), 2),
            }
        )
    )

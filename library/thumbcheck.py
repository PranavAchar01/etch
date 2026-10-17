"""Measure board thumbnails (framing + exposure) and lay them out on a contact sheet.

    python3 library/thumbcheck.py stats [slug ...]            # bbox fill and board luminance, plus the spread
    python3 library/thumbcheck.py sheet OUT.png slug ...      # contact sheet of world/boards/<slug>/thumb.webp

The board mask mirrors explore.js whiteThumb(): the backdrop is a vertical lavender gradient, taken per row from
the outermost pixels; a pixel is board when it is far from that row's backdrop colour and is not just the backdrop
darkened (the contact shadow).
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import WORLD  # noqa: E402

HI = 46.0  # same colour distance explore.js treats as "pure board"


def board_mask(rgb: np.ndarray) -> np.ndarray:
    p = rgb.astype(np.float32)
    h, w, _ = p.shape
    edge = np.stack([p[:, 0], p[:, w - 1]], 1)  # (h, 2, 3)
    ref = np.median(p[: max(4, h // 20), [0, w - 1]].reshape(-1, 3), 0)
    bg = np.empty((h, 3), np.float32)
    last = ref
    for y in range(h):  # rows where the board touches an edge keep the last good colour
        d = np.abs(edge[y] - last).sum(1)
        i = int(d.argmin())
        if d[i] < 24:
            last = edge[y, i]
        bg[y] = last
    b = bg[:, None, :]
    dist = np.sqrt(((p - b) ** 2).sum(2))
    bb = (b * b).sum(2)
    k = (p * b).sum(2) / np.maximum(bb, 1)
    off = np.sqrt(((p - k[..., None] * b) ** 2).sum(2)) / np.maximum(1, k * np.sqrt(bb))
    shadow = (k > 0.42) & (k < 1.06) & (off < 0.035)
    return (dist >= HI) & ~shadow


def luminance(rgb: np.ndarray) -> np.ndarray:
    p = rgb.astype(np.float32) / 255.0
    lin = np.where(p <= 0.04045, p / 12.92, ((p + 0.055) / 1.055) ** 2.4)
    return lin @ np.array([0.2126, 0.7152, 0.0722], np.float32)  # linear-light Y, 0..1


def measure(img: Image.Image) -> dict:
    rgb = np.asarray(img.convert("RGB"))
    m = board_mask(rgb)
    h, w = m.shape
    ys, xs = np.nonzero(m)
    if not len(xs):
        return {"fill": 0.0, "fw": 0.0, "fh": 0.0, "cx": 0.5, "cy": 0.5, "lum": 0.0, "px": 0}
    # ignore stray specks: 0.2 / 99.8 percentiles instead of the raw extremes
    x0, x1 = np.percentile(xs, [0.2, 99.8])
    y0, y1 = np.percentile(ys, [0.2, 99.8])
    fw, fh = (x1 - x0 + 1) / w, (y1 - y0 + 1) / h
    return {"fill": float(max(fw, fh)), "fw": float(fw), "fh": float(fh), "cx": float((x0 + x1) / 2 / w),
            "cy": float((y0 + y1) / 2 / h), "lum": float(luminance(rgb)[m].mean()), "px": int(m.sum())}


def thumb_path(slug: str) -> Path:
    return WORLD / "boards" / slug / "thumb.webp"


def spread(rows: list[dict], key: str) -> str:
    v = [r[key] for r in rows]
    return (f"{key}: min {min(v):.3f} max {max(v):.3f} mean {statistics.mean(v):.3f} "
            f"sd {statistics.pstdev(v):.3f}")


def stats(slugs: list[str]) -> list[dict]:
    rows = []
    for s in slugs:
        r = measure(Image.open(thumb_path(s)))
        r["slug"] = s
        rows.append(r)
    return rows


def sheet(paths: list[Path], out: Path, cols: int = 4, tw: int = 320, th: int = 200) -> None:
    rows = (len(paths) + cols - 1) // cols
    cap = 16
    im = Image.new("RGB", (cols * tw, rows * (th + cap)), "white")
    d = ImageDraw.Draw(im)
    for i, p in enumerate(paths):
        x, y = (i % cols) * tw, (i // cols) * (th + cap)
        t = Image.open(p).convert("RGB")
        r = measure(t)
        im.paste(t.resize((tw, th), Image.LANCZOS), (x, y))
        d.text((x + 4, y + th + 2), f"{p.parent.name[:34]}  fill {r['fill']:.2f}  Y {r['lum']:.3f}", fill="black")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("stats", "sheet"):
        raise SystemExit(__doc__)
    if sys.argv[1] == "sheet":
        sheet([thumb_path(s) if not s.endswith((".webp", ".png")) else Path(s) for s in sys.argv[3:]],
              Path(sys.argv[2]))
        return
    slugs = sys.argv[2:] or sorted(p.parent.name for p in (WORLD / "boards").glob("*/thumb.webp"))
    rows = stats(slugs)
    for r in rows:
        print(f"{r['slug'][:60]:60s} fill {r['fill']:.3f} (w {r['fw']:.3f} h {r['fh']:.3f}) "
              f"centre {r['cx']:.2f},{r['cy']:.2f} Y {r['lum']:.3f}")
    print(f"n={len(rows)}")
    for k in ("fill", "lum", "cx", "cy"):
        print(spread(rows, k))


if __name__ == "__main__":
    main()

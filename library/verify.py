"""Check every rendered board: files present, GLB in metres, parts.json nodes exist in the GLB.

    python3 library/verify.py [--fix]

--fix blanks leftover kicad-cli "=>[...]" node names in GLBs made before optimize.mjs did it.
"""

from __future__ import annotations

from common import WORLD, load_json, log, save_json
import sys

from pcbinfo import glb_bounds, glb_clear_names, glb_json, glb_nodes


def main() -> None:
    idx = load_json(WORLD / "index.json", {"boards": []})
    bad = 0
    total = 0
    for row in idx["boards"]:
        d = WORLD / "boards" / row["slug"]
        errs = []
        for f in ("meta.json", "parts.json", "thumb.webp", "board.glb"):
            if not (d / f).exists():
                errs.append(f"missing {f}")
        if errs:
            log(f"{row['slug']}: {errs}")
            bad += 1
            continue
        if "--fix" in sys.argv and glb_clear_names(d / "board.glb"):
            meta = load_json(d / "meta.json", {})
            meta["glb_bytes"] = (d / "board.glb").stat().st_size
            save_json(d / "meta.json", meta)
        parts = load_json(d / "parts.json", {})["parts"]
        names = glb_nodes(d / "board.glb")
        g = glb_json(d / "board.glb")
        lo, hi = glb_bounds(d / "board.glb")
        ext = (hi[0] - lo[0]) * 1000, (hi[2] - lo[2]) * 1000
        sx, sy = row["size_mm"]
        if not (0.5 * sx <= ext[0] <= 3 * sx + 20 and 0.5 * sy <= ext[1] <= 3 * sy + 20):
            errs.append(f"GLB extent {ext[0]:.1f} x {ext[1]:.1f} mm vs board {sx} x {sy} mm")
        dangling = [p["ref"] for p in parts.values() if p["node"] and p["node"] not in names]
        if dangling:
            errs.append(f"nodes missing from GLB: {dangling[:5]}")
        extra = [n for n in names if n not in parts]
        if extra:
            errs.append(f"GLB names that are not refdes: {extra[:5]}")
        if "EXT_meshopt_compression" not in g.get("extensionsUsed", []):
            errs.append("not meshopt-compressed")
        total += 1
        if errs:
            bad += 1
            log(f"{row['slug']}: " + "; ".join(errs))
    log(f"verified {total} boards, {bad} with problems")


if __name__ == "__main__":
    main()

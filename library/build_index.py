"""Write deploy/etch-app/world/index.json from every rendered board's meta.json.

    python3 library/build_index.py [--prune]

--prune deletes board folders that are incomplete (missing glb/thumb/parts/meta).
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil

from common import CATEGORIES, WORLD, load_json, log, save_json

ROW_KEYS = ["id", "slug", "title", "owner", "repo", "category", "stars", "license", "copyable", "layers", "size_mm",
            "parts", "glb_bytes"]
NEEDED = ("meta.json", "parts.json", "thumb.webp", "board.glb")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prune", action="store_true")
    args = ap.parse_args()
    rows = []
    for d in sorted((WORLD / "boards").glob("*")):
        if not d.is_dir():
            continue
        if not all((d / f).exists() for f in NEEDED):
            log(f"incomplete: {d.name}" + (" (removed)" if args.prune else ""))
            if args.prune:
                shutil.rmtree(d)
            continue
        meta = load_json(d / "meta.json", None)
        if not meta:
            continue
        meta["glb_bytes"] = (d / "board.glb").stat().st_size
        rows.append({k: meta.get(k) for k in ROW_KEYS})
    rows.sort(key=lambda r: (-(r["stars"] or 0), r["title"].lower()))
    counts = {c: 0 for c, _ in CATEGORIES}
    for r in rows:
        counts[r["category"] if r["category"] in counts else "everything-else"] += 1
    index = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(rows),
        "categories": [{"id": c, "label": label, "count": counts[c]} for c, label in CATEGORIES],
        "boards": rows,
    }
    save_json(WORLD / "index.json", index, indent=None)
    log(f"index.json: {len(rows)} boards; " + ", ".join(f"{c} {n}" for c, n in counts.items() if n))


if __name__ == "__main__":
    main()

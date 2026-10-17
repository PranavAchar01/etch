"""Write deploy/etch-app/world/index.json from every rendered board's meta.json, and library/manifest.json
(one row per crawled board: queued / rendered / skipped, with the reason and the 3D-model summary).

    python3 library/build_index.py [--prune]

Only boards whose meta.json says identical: true are indexed. --prune deletes board folders that are
incomplete (missing glb/thumb/parts/meta) or not identical.
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil

from common import BOARDS_JSON, CATEGORIES, LIB_DIR, RENDER_LOG, SRC, WORLD, load_json, log, save_json, scrub

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
        if not meta.get("identical") or "models" not in meta:
            log(f"not identical (or rendered under the old rules): {d.name}" + (" (removed)" if args.prune else ""))
            if args.prune:
                shutil.rmtree(d)
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
    write_manifest({r["slug"] for r in rows})
    log(f"index.json: {len(rows)} boards; " + ", ".join(f"{c} {n}" for c, n in counts.items() if n))


def write_manifest(rendered: set[str]) -> None:
    """library/manifest.json: every crawled board with status queued / rendered / skipped, the reason, the
    3D-model summary and output size. Rows already in the manifest are kept (seed.py rebuilds the cache from
    them) and updated from boards.json, fetch.py's models.json, render_log.json and world/."""
    from crawl import denied

    path = LIB_DIR / "manifest.json"
    old = {m["slug"]: m for m in load_json(path, {}).get("boards", [])}
    crawled = {b["slug"]: b for b in load_json(BOARDS_JSON, [])}
    rlog = load_json(RENDER_LOG, {})
    rows = []
    for slug in dict.fromkeys([*old, *crawled]):
        b = crawled.get(slug) or old[slug]
        if denied(b["owner"]):
            continue
        fetch = b.get("fetch") or {}
        models = load_json(SRC / slug / "models.json", None)
        d = WORLD / "boards" / slug
        row = {k: b.get(k) for k in ("id", "slug", "owner", "repo", "commit", "pcb_path", "license", "copyable",
                                     "category", "stars")}
        row["url"] = f"https://github.com/{b['owner']}/{b['repo']}/tree/{b['commit']}"
        reason = None
        if slug in rendered:
            meta = load_json(d / "meta.json", {})
            models = meta.get("models", models)
            status = "rendered"
            row["glb_bytes"] = (d / "board.glb").stat().st_size
            row["bytes"] = sum(x.stat().st_size for x in d.iterdir() if x.is_file())
        elif fetch and not fetch.get("ok"):
            status, reason = "skipped", scrub("fetch: " + fetch.get("error", "?"))
        elif slug in rlog and not rlog[slug].get("ok"):
            status, reason = "skipped", scrub(rlog[slug].get("error", "?"))[:300]
        elif models is not None and not models.get("identical"):
            n = sum(1 for m in models["missing"] if "KiCad shows none" not in m.get("reason", ""))
            status, reason = "skipped", f"not identical: {n} of {models['total']} 3D models not found"
        elif slug not in crawled:
            status, reason = "skipped", "no longer selected by crawl.py (library/tool repo or repo filter)"
        else:
            status = "queued"
        row["status"], row["reason"] = status, reason
        if models is not None:
            row["identical"] = bool(models.get("identical"))
            row["models"] = {"total": models["total"], "resolved": models["resolved"],
                             "hidden": models.get("hidden", 0), "missing": len(models["missing"])}
        rows.append(row)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    save_json(path, {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(rows), "counts": counts, "boards": rows,
    })
    log("manifest.json: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()

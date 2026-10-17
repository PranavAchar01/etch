"""Write deploy/etch-app/world/index.json from every rendered board's meta.json, and refresh
library/manifest.json (every crawled board with its render status, model counts and output size).

    python3 library/build_index.py [--prune]

--prune deletes board folders that are incomplete (missing glb/thumb/parts/meta).
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil

from common import BOARDS_JSON, CATEGORIES, LIB_DIR, RENDER_LOG, WORLD, load_json, log, save_json

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
    write_manifest()


def write_manifest() -> None:
    """Merge the manifest with what fetch.py and render.py recorded. A board is `rendered` only when its
    folder is complete; `identical` comes from its meta.json (false whenever a model is missing)."""
    path = LIB_DIR / "manifest.json"
    man = load_json(path, {"boards": []})
    fetched = {b["slug"]: b.get("fetch") for b in load_json(BOARDS_JSON, [])}
    rlog = load_json(RENDER_LOG, {})
    for m in man["boards"]:
        slug = m["slug"]
        d = WORLD / "boards" / slug
        f, r = fetched.get(slug), rlog.get(slug)
        for k in ("error", "identical", "models", "glb_bytes", "bytes"):
            m.pop(k, None)
        if f and f.get("ok"):
            m["unresolved_models"] = f.get("unresolved_models", 0)
        if all((d / x).exists() for x in NEEDED):
            meta = load_json(d / "meta.json", {})
            mo = meta.get("models") or {}
            m["status"] = "rendered"
            m["identical"] = bool(meta.get("identical"))
            if mo:
                m["models"] = {"total": mo.get("total", 0), "resolved": mo.get("resolved", 0),
                               "missing": len(mo.get("missing", []))}
            m["glb_bytes"] = (d / "board.glb").stat().st_size
            m["bytes"] = sum(x.stat().st_size for x in d.iterdir() if x.is_file())
        elif f is not None and not f.get("ok"):
            m["status"], m["error"] = "failed", "fetch: " + str(f.get("error", ""))[:200]
        elif r is not None and not r.get("ok"):
            m["status"], m["error"] = "failed", str(r.get("error", ""))[:200]
        else:
            m["status"] = "queued"
    man["count"] = len(man["boards"])
    save_json(path, man)
    st: dict[str, int] = {}
    for m in man["boards"]:
        st[m["status"]] = st.get(m["status"], 0) + 1
    log("manifest.json: " + ", ".join(f"{k} {v}" for k, v in sorted(st.items())))


if __name__ == "__main__":
    main()

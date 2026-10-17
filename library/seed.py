"""Rebuild library/cache/{repos,boards}.json from library/manifest.json, pinned at the manifest's commits.

    python3 library/seed.py [--only slug ...]

crawl.py searches GitHub again and pins each repo to its current default-branch head, so a fresh crawl
gives a different list. This keeps the exact boards and commits the manifest already lists: per repo it
reads the repo record, the file tree at the pinned commit and the licence path (3 core API calls), then
writes the same board records crawl.py would (ids, slugs and categories taken from the manifest).
"""

from __future__ import annotations

import argparse

from common import (
    BOARDS_JSON,
    LIB_DIR,
    REPOS_JSON,
    copyable,
    gh,
    load_json,
    log,
    save_json,
)
from crawl import board_records, repo_record

KEEP_EXT = (
    ".kicad_pro",
    ".kicad_sch",
    ".pro",
    ".sch",
    ".step",
    ".stp",
    ".stpz",
    ".wrl",
    ".wrz",
    ".vrml",
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    manifest = load_json(LIB_DIR / "manifest.json", {}).get("boards", [])
    if args.only:
        manifest = [m for m in manifest if m["slug"] in args.only]
    cache = {r["full_name"]: r for r in load_json(REPOS_JSON, [])}
    by_repo: dict[str, list[dict]] = {}
    for m in manifest:
        by_repo.setdefault(f"{m['owner']}/{m['repo']}", []).append(m)

    boards: list[dict] = []
    for full, rows in by_repo.items():
        sha = rows[0]["commit"]
        rec = cache.get(full)
        if not rec or rec.get("commit") != sha or "files" not in rec:
            item = gh(f"repos/{full}")
            if not item:
                log(f"{full}: repo gone")
                continue
            rec = repo_record(item)
            rec["commit"] = sha
            tree = gh(f"repos/{full}/git/trees/{sha}?recursive=1") or {}
            entries = tree.get("tree") or []
            rec["tree_truncated"] = bool(tree.get("truncated"))
            rec["files"] = {
                e["path"]: e.get("size", 0)
                for e in entries
                if e.get("type") == "blob" and e["path"].lower().endswith(KEEP_EXT)
            }
            sizes = {
                e["path"]: e.get("size", 0) for e in entries if e.get("type") == "blob"
            }
            rec["pcbs"] = [
                {"path": r["pcb_path"], "size": sizes.get(r["pcb_path"], 0)}
                for r in rows
            ]
            lic = gh(f"repos/{full}/license")
            rec["license_path"] = (lic or {}).get("path") or "LICENSE"
            rec["status"] = "ok"
            cache[full] = rec
            save_json(REPOS_JSON, list(cache.values()))
        # the manifest is the source of truth for ids, slugs, licence and category
        made = {b["pcb_path"]: b for b in board_records(rec)}
        for r in rows:
            b = made.get(r["pcb_path"])
            if not b:
                continue
            b.update(
                {
                    k: r[k]
                    for k in ("id", "slug", "category", "stars", "license", "commit")
                }
            )
            b["copyable"] = copyable(r["license"])
            boards.append(b)
        log(f"{full:55s} {len(rows)} board(s) @ {sha[:7]}")
    if args.only:
        old = {b["slug"]: b for b in load_json(BOARDS_JSON, [])}
        old.update({b["slug"]: b for b in boards})
        boards = list(old.values())
    save_json(BOARDS_JSON, boards)
    log(f"boards.json: {len(boards)} boards from {len(by_repo)} repos")


if __name__ == "__main__":
    main()

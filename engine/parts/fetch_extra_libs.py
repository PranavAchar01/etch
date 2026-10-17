#!/usr/bin/env python3
"""Reproduce engine/parts/extra_libs/ from extra_libs/MANIFEST.json.

The library data is NOT committed (see the repo whitelist .gitignore); this script
re-creates it. Downloaded content is untrusted data: nothing from it is executed,
git hooks/filters are not run from it, and only counts files by extension.

  python3 fetch_extra_libs.py            # clone any missing source at its pinned commit
  python3 fetch_extra_libs.py --rescan   # recompute counts/sizes/commits into MANIFEST.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "extra_libs"
MANIFEST = ROOT / "MANIFEST.json"
MODEL_EXT = {".step", ".stp", ".wrl"}


def git(*args: str, cwd: Path | None = None) -> str:
    # -c core.hooksPath=/dev/null keeps repo-provided hooks from ever running
    cmd = ["nice", "-n", "19", "git", "-c", "core.hooksPath=/dev/null", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def fetch(src: dict) -> None:
    dest = ROOT / src["name"]
    if dest.exists():
        print(f"ok      {src['name']} (present)")
        return
    print(f"clone   {src['name']} @ {src['commit'][:10]}")
    dest.mkdir(parents=True)
    git("init", "-q", cwd=dest)
    git("remote", "add", "origin", src["url"], cwd=dest)
    git("fetch", "-q", "--depth", "1", "origin", src["commit"], cwd=dest)
    git("-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD", cwd=dest)


def scan(dest: Path) -> dict:
    counts = {"symbols": 0, "footprints": 0, "models_step": 0, "models_wrl": 0}
    size = 0
    for p in dest.rglob("*"):
        if ".git" in p.parts or not p.is_file():
            continue
        size += p.stat().st_size
        ext = p.suffix.lower()
        if ext == ".kicad_mod":
            counts["footprints"] += 1
        elif ext == ".kicad_sym":
            counts["symbols"] += 1
        elif ext in (".step", ".stp"):
            counts["models_step"] += 1
        elif ext == ".wrl":
            counts["models_wrl"] += 1
    counts["models"] = counts["models_step"] + counts["models_wrl"]
    counts["size_mb"] = round(size / 1e6, 1)
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rescan", action="store_true")
    args = ap.parse_args()
    data = json.loads(MANIFEST.read_text())
    for src in data["sources"]:
        fetch(src)
        if args.rescan:
            dest = ROOT / src["name"]
            src["commit"] = git("rev-parse", "HEAD", cwd=dest)
            src.update(scan(dest))
    if args.rescan:
        MANIFEST.write_text(json.dumps(data, indent=2) + "\n")
        print("MANIFEST.json updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())

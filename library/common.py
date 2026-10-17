"""Shared helpers for the board-library pipeline: paths, gh calls, slugs, licence policy, s-expressions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

LIB_DIR = Path(__file__).resolve().parent
ROOT = LIB_DIR.parent
CACHE = LIB_DIR / "cache"
SRC = CACHE / "src"
WORK = CACHE / "work"
REPOS_JSON = CACHE / "repos.json"
BOARDS_JSON = CACHE / "boards.json"
RENDER_LOG = CACHE / "render_log.json"
WORLD = ROOT / "deploy" / "etch-app" / "world"

CATEGORIES = [
    ("keyboards", "Keyboards"),
    ("iot", "IoT"),
    ("computing", "Computing"),
    ("dev-boards", "Dev boards"),
    ("audio", "Audio"),
    ("robotics", "Robotics"),
    ("tools", "Tools"),
    ("rf", "RF"),
    ("retro", "Retro"),
    ("lighting", "Lighting"),
    ("displays", "Displays"),
    ("power", "Power"),
    ("wearables", "Wearables"),
    ("everything-else", "Everything else"),
]

# licences whose terms allow copying and modifying the design (contract section 1)
ALLOW_EXACT = {
    "MIT", "Apache-2.0", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CERN-OHL-P-2.0", "CERN-OHL-W-2.0",
    "CERN-OHL-S-2.0", "MPL-2.0", "Unlicense", "0BSD",
}
ALLOW_PREFIX = ("BSD-", "GPL-", "LGPL-")


def copyable(spdx: str | None) -> bool:
    return bool(spdx) and (spdx in ALLOW_EXACT or spdx.startswith(ALLOW_PREFIX))


def slugify(board_id: str) -> str:
    s = board_id.removeprefix("gh:").lower().replace("/", "__")
    return re.sub(r"[^a-z0-9_.-]", "-", s)


def log(*a: Any) -> None:
    print(*a, file=sys.stderr, flush=True)


def load_json(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def save_json(p: Path, data: Any, indent: int | None = 1) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=indent, ensure_ascii=False))
    tmp.replace(p)


# ---------------------------------------------------------------- GitHub via gh

def _wait_for_reset(kind: str) -> None:
    try:
        rl = json.loads(subprocess.run(["gh", "api", "rate_limit"], capture_output=True, text=True).stdout)
        r = rl["resources"][kind]
        if r["remaining"] > 0:
            time.sleep(5)
            return
        delay = max(5, r["reset"] - time.time() + 2)
    except Exception:
        delay = 60
    log(f"  rate limited ({kind}), sleeping {delay:.0f}s")
    time.sleep(min(delay, 3700))


def gh(path: str, *, search: bool = False, tries: int = 6) -> Any:
    """`gh api path` -> parsed JSON. None on 404/409/451; retries with backoff on 403/429/5xx."""
    for attempt in range(tries):
        p = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github+json", path],
                           capture_output=True, text=True)
        if p.returncode == 0:
            return json.loads(p.stdout) if p.stdout.strip() else None
        err = (p.stderr + p.stdout)[-400:]
        if re.search(r"HTTP (404|409|451|422)", err):
            return None
        if re.search(r"HTTP (403|429)|rate limit", err, re.I):
            _wait_for_reset("search" if search else "core")
            continue
        time.sleep(2 * (attempt + 1))
    log(f"  gh failed: {path}: {err.strip()[:200]}")
    return None


# ---------------------------------------------------------------- tiny s-expression parser

_TOK = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse_sexpr(text: str) -> list:
    """Parse a KiCad s-expression file into nested lists. Strings stay str; atoms stay str."""
    stack: list[list] = [[]]
    pos, n = 0, len(text)
    m = _TOK.match
    while pos < n:
        t = m(text, pos)
        if not t:
            if text[pos:].strip() == "":
                break
            pos += 1
            continue
        pos = t.end()
        if t.group(1):
            stack.append([])
        elif t.group(2):
            if len(stack) > 1:
                done = stack.pop()
                stack[-1].append(done)
        elif t.group(3) is not None:
            stack[-1].append(t.group(3).replace('\\"', '"').replace("\\\\", "\\"))
        else:
            stack[-1].append(t.group(4))
    return stack[0][0] if stack[0] else []


def children(node: list, tag: str) -> list[list]:
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == tag]


def child(node: list, tag: str) -> list | None:
    for c in node[1:]:
        if isinstance(c, list) and c and c[0] == tag:
            return c
    return None


def prop(node: list, name: str) -> str | None:
    """KiCad 6+ (property "Name" "value" ...) lookup."""
    for c in children(node, "property"):
        if len(c) > 2 and c[1] == name:
            return c[2]
    return None


def kicad_cli() -> Path | None:
    """kicad-cli from $KICAD_CLI, $ETCH_KICAD_APP, /Applications, ~/Applications or the Homebrew Caskroom."""
    apps = [os.environ.get("ETCH_KICAD_APP", ""), "/Applications/KiCad/KiCad.app",
            str(Path.home() / "Applications/KiCad/KiCad.app")]
    apps += [str(p) for p in sorted(Path("/opt/homebrew/Caskroom/kicad").glob("*/KiCad/KiCad.app"), reverse=True)]
    cands = [os.environ.get("KICAD_CLI", "")] + [a + "/Contents/MacOS/kicad-cli" for a in apps if a]
    for p in cands:
        if p and Path(p).exists():
            return Path(p)
    return None

"""Load the repo's .env (created by setup.sh from .env.example) into os.environ.

Variables already set (non-empty) in the environment always win, so launchd, Docker (-e / env_file) and the shell
override the file. Only KEY=VALUE lines are read: blank lines and # comments are skipped, an optional
leading "export " is allowed, and matching single or double quotes around the value are removed.
Nothing in the file is executed. The file is git-ignored (the repo .gitignore is a whitelist).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load(path: Path | None = None) -> None:
    # an empty ETCH_* (docker compose env_file passes blank lines as "") means "use the default", never ""
    for k in [k for k, v in os.environ.items() if k.startswith("ETCH_") and v == ""]:
        del os.environ[k]
    p = path or Path(os.environ.get("ETCH_ENV_FILE", str(ROOT / ".env")))
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        if raw.lstrip().startswith("#"):
            continue
        m = _LINE.match(raw)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        if v and k not in os.environ:
            os.environ[k] = v

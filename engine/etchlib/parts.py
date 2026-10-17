"""Parts lookup: curated index first, then a GitHub-hosted library, then a web search hook.

No keys live in this file. The web hook takes a callable so a search API client can be
plugged in at run time.
"""

from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .kicad import KICAD_APP

INDEX_PATH = Path(__file__).resolve().parent.parent / "parts/index.json"
# parts the agent fetched and verified at run time (same entry format, source says where from)
AGENT_INDEX_PATH = Path(__file__).resolve().parent.parent / "parts/agent_index.json"
CACHE_DIR = Path(__file__).resolve().parent.parent / "parts/cache"
# every piece read from the installed KiCad libraries (agent/kicad_catalog.py), keyed kicad:<lib_id>@<footprint>
KICAD_CATALOG_PATH = Path(__file__).resolve().parent.parent / "parts/kicad_catalog.json"
REPO_ROOT = Path(__file__).resolve().parents[2]

# The JSON indexes record absolute paths from the machine that built them; read_index points them at
# this checkout and this machine's KiCad (a no-op on the machine that built them).
_KICAD_RE = re.compile(r'"/[^"]*?/KiCad\.app/')
_REPO_RE = re.compile(r'"/[^"]*?/(engine/parts/cache|vendor)/')


def read_index(path: Path) -> Any:
    kicad = json.dumps(str(KICAD_APP))[:-1]
    root = json.dumps(str(REPO_ROOT))[:-1]
    s = _KICAD_RE.sub(lambda m: kicad + "/", path.read_text())
    s = _REPO_RE.sub(lambda m: f"{root}/{m.group(1)}/", s)
    return json.loads(s)

SearchFn = Callable[[str], list[dict[str, Any]]]


@functools.lru_cache(maxsize=1)
def load_index() -> dict[str, dict[str, Any]]:
    data = read_index(INDEX_PATH)
    parts = {p["key"]: p for p in data["parts"]}
    if AGENT_INDEX_PATH.exists():
        for p in read_index(AGENT_INDEX_PATH).get("parts", []):
            parts.setdefault(p["key"], p)
    if KICAD_CATALOG_PATH.exists():
        for p in read_index(KICAD_CATALOG_PATH).get("pieces", []):
            if p.get("model") and p.get("pads_ok"):
                parts.setdefault(p["key"], _piece_entry(p))
    for k, e in _REGISTERED.items():
        parts.setdefault(k, e)
    return parts


def _piece_entry(p: dict[str, Any]) -> dict[str, Any]:
    """A library piece (kicad_catalog / kicad_library format) as an index entry."""
    return {
        "key": p["key"],
        "name": p["name"],
        "aliases": [],
        "lib_id": p["lib_id"],
        "footprint": p["footprint"],
        "category": p["family"],
        "value": None,
        "spice": p.get("spice"),
        "why": None,
        "model": p["model"],
        "pins": sorted(p["pins"], key=lambda s: (len(s), s)),
        "source": p.get("source") or "kicad-lib",
        **({"provenance": p["provenance"]} if p.get("provenance") else {}),
    }


# pieces from the full parts library (agent/kicad_library.py: every KiCad library, the extra libraries, fetched
# cache parts) that a design in this process uses; the 39k-piece library is not loaded into the index wholesale
_REGISTERED: dict[str, dict[str, Any]] = {}


def register(pieces: list[dict[str, Any]]) -> None:
    """Make library pieces resolvable by load_index() (and so by the spec builder) in this process."""
    idx = load_index()
    for p in pieces:
        if p["key"] in idx or not (p.get("model") and p.get("pads_ok")):
            continue
        e = _piece_entry(p)
        _REGISTERED[p["key"]] = e
        idx[p["key"]] = e


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower().replace("-", "")))


def search_index(name: str, limit: int = 5) -> list[tuple[float, dict[str, Any]]]:
    q = _tokens(name)
    if not q:
        return []
    scored = []
    for p in load_index().values():
        best = 0.0
        for label in [p["name"], p["key"], *p["aliases"]]:
            t = _tokens(label)
            if not t:
                continue
            inter = len(q & t)
            score = inter / len(q | t) + (0.5 if label.lower() == name.lower() else 0.0)
            best = max(best, score)
        if best > 0:
            scored.append((round(best, 3), p))
    scored.sort(key=lambda s: -s[0])
    return scored[:limit]


LCSC_SOURCE = "LCSC via easyeda2kicad"


def from_easyeda(lcsc_id: str, allow_network: bool = False) -> dict[str, Any]:
    """Second tier: easyeda2kicad (github.com/uPesy/easyeda2kicad.py) converts an LCSC part
    into a KiCad symbol, footprint and 3D model. It downloads from EasyEDA, so it only runs
    when the caller passes allow_network=True."""
    if not re.fullmatch(r"C\d{2,9}", lcsc_id):
        raise ValueError("LCSC ids look like C2040")
    out = CACHE_DIR / lcsc_id
    cmd = ["uvx", "easyeda2kicad", "--full", f"--lcsc_id={lcsc_id}", f"--output={out}/{lcsc_id}"]
    if not allow_network:
        return {"status": "needs_network", "command": cmd, "source": LCSC_SOURCE}
    if shutil.which("uvx") is None:
        return {"status": "error", "reason": "uvx not on PATH", "source": LCSC_SOURCE}
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
    files = sorted(str(p) for p in out.rglob("*") if p.is_file())
    steps = [f for f in files if f.lower().endswith((".step", ".stp"))]
    status = "ok" if proc.returncode == 0 else "error"
    if status == "ok" and not steps:
        # never generate a body: without the manufacturer's STEP the part is not usable in 3D
        status = "no_model"
    return {
        "status": status,
        "model": steps[0] if steps else None,
        "files": files,
        "log": proc.stdout[-800:] + proc.stderr[-800:],
        "source": LCSC_SOURCE,
    }


def find_part(
    name: str,
    *,
    lcsc_id: str | None = None,
    search: SearchFn | None = None,
    allow_network: bool = False,
    min_score: float = 0.34,
) -> dict[str, Any]:
    """Resolve a plain part name.

    1. parts/index.json (verified against the local KiCad install)
    2. easyeda2kicad by LCSC id (GitHub-hosted converter), when an id is given
    3. `search(query)`: any web search callable returning [{title, url, snippet}], for
       example a You.com API client. Results are returned as leads, never trusted as parts.
    """
    hits = search_index(name)
    if hits and hits[0][0] >= min_score:
        score, p = hits[0]
        return {
            "status": "found",
            "source": "kicad-lib",
            "score": score,
            "part": p,
            "alternatives": [h[1]["key"] for h in hits[1:]],
        }
    if lcsc_id:
        res = from_easyeda(lcsc_id, allow_network=allow_network)
        if res["status"] in ("ok", "needs_network"):
            return {"status": res["status"], "source": LCSC_SOURCE, "easyeda": res}
    if search is not None:
        leads = search(f"{name} KiCad footprint symbol 3D model")
        return {"status": "leads", "source": "web", "leads": leads[:10]}
    return {"status": "not_found", "closest": [(s, p["key"]) for s, p in hits]}

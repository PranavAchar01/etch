"""Where every part came from. Nothing in Etch is generated: each symbol, footprint and 3D model is a
file someone published (KiCad's own libraries, LCSC/EasyEDA, a GitHub repo). This module describes that
origin for any part so the step stream and the UI can show it."""

from __future__ import annotations

import functools
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any

from etchlib.kicad import KICAD_CLI

KICAD_LICENCE = "CC-BY-SA-4.0 with the KiCad libraries exception"


@functools.cache
def kicad_version() -> str:
    try:
        out = subprocess.run(
            [str(KICAD_CLI), "version"], capture_output=True, text=True, timeout=20
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    m = re.search(r"\d+\.\d+(\.\d+)?", out)
    return m.group(0) if m else "unknown"


@functools.cache
def easyeda2kicad_version() -> str:
    try:
        out = subprocess.run(
            [
                "uvx",
                "--from",
                "easyeda2kicad",
                "python",
                "-c",
                "import importlib.metadata as m; print(m.version('easyeda2kicad'))",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return out or "unknown"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def kicad_lib(entry: dict[str, Any]) -> dict[str, Any]:
    """Provenance of a part from KiCad's bundled libraries (tagged per KiCad release on GitLab)."""
    v = kicad_version()
    base = "https://gitlab.com/kicad/libraries"
    sym_lib = entry["lib_id"].split(":")[0]
    fp_lib = entry["footprint"].split(":")[0]
    model = entry.get("model")
    model_url = None
    if model and "3dmodels/" in model:
        model_url = (
            f"{base}/kicad-packages3D/-/blob/{v}/{model.split('3dmodels/', 1)[1]}"
        )
    return {
        "route": "local KiCad library index",
        "source_url": f"{base}/kicad-footprints/-/blob/{v}/{fp_lib}.pretty/{entry['footprint'].split(':', 1)[1]}.kicad_mod",
        "symbol_url": f"{base}/kicad-symbols/-/blob/{v}/{sym_lib}.kicad_sym",
        "model_url": model_url,
        "version": f"KiCad {v} library tag",
        "licence": KICAD_LICENCE,
    }


def of(entry: dict[str, Any]) -> dict[str, Any]:
    """Provenance for any index entry (fetched parts carry their own, recorded at fetch time)."""
    if entry.get("provenance"):
        return entry["provenance"]
    if entry.get("source", "kicad-lib") == "kicad-lib":
        return kicad_lib(entry)
    return {
        "route": entry.get("source"),
        "source_url": None,
        "version": None,
        "licence": "unknown",
    }


def short(entry: dict[str, Any]) -> dict[str, Any]:
    p = of(entry)
    return {
        k: p.get(k)
        for k in ("route", "source_url", "version", "licence", "model_url")
        if p.get(k)
    }

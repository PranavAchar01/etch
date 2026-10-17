"""Stored boards: every board that passes every gate is saved, and an ask that matches one is answered from it.

The match is local and plain code (under a millisecond): the ask's content words must overlap a stored ask's
by at least 80 % (Jaccard), and the words that change the circuit (colours, digits, part words) must be
identical. A hit copies the stored, already verified board into the new run, so nothing is rebuilt.
Seeded only from boards Pranav approved (TYPE RGB, live-1792202999059) and new boards that pass.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from . import config

STORE = config.ENGINE / "parts/board_store.json"
STOP = {
    "a", "an", "the", "and", "or", "but", "if", "when", "whenever", "while", "it", "its", "it's", "is", "are", "be", "to", "of", "on", "in", "for", "with", "my", "me", "i", "you", "can", "could", "please", "make", "build", "create", "want", "need", "would", "like", "etch", "hey", "hi", "um", "uh", "so", "that", "this", "board", "pcb", "circuit", "one", "some", "let", "let's", "think", "see", "right", "just", "also", "then", "there", "here", "do", "does"
}
KEY = re.compile(
    r"^(red|green|blue|yellow|purple|cyan|magenta|white|orange|pink|off|\d+|led|leds|rgb|buzzer|beep\w*|dark|light|"
    r"button|fan|motor|speak\w*|talk\w*|voice|sound|type|typing|blink\w*|flash\w*|police|battery|usb|laptop)$"
)


def _words(ask: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", ask.lower()) if w not in STOP}


def _load() -> list[dict[str, Any]]:
    try:
        return json.loads(STORE.read_text())["boards"]
    except (OSError, ValueError, KeyError):
        return []


def save(ask: str, out: Path, recipe: str | None = None) -> None:
    rows = [r for r in _load() if r["run"] != out.name]
    rows.append({"ask": ask, "run": out.name, "recipe": recipe, "saved": time.strftime("%Y-%m-%d %H:%M:%S")})
    STORE.write_text(json.dumps({"boards": rows}, indent=1))


def lookup(ask: str) -> dict[str, Any] | None:
    w = _words(ask)
    k = {x for x in w if KEY.match(x)}
    best, score = None, 0.0
    for r in _load():
        rw = _words(r["ask"])
        if {x for x in rw if KEY.match(x)} != k or not (config.OUT / r["run"] / "design.json").exists():
            continue
        j = len(w & rw) / max(1, len(w | rw))
        if j > score:
            best, score = r, j
    return {**best, "score": round(score, 3)} if best and score >= 0.8 else None


def copy_into(row: dict[str, Any], out: Path) -> None:
    src = config.OUT / row["run"]
    for f in src.iterdir():
        if f.name in ("run.jsonl", "live_result.json"):
            continue
        dst = out / f.name
        if f.is_dir():
            shutil.copytree(f, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(f, dst)
    fj = out / "frames" / "frames.json"
    if fj.exists():  # the frame list names its board file; point it at the copy
        meta = json.loads(fj.read_text())
        meta["pcb"] = str(out / Path(meta.get("pcb", "")).name)
        fj.write_text(json.dumps(meta))

"""Etch agent layer: a lead agent plus sub-agents on any OpenAI-compatible endpoint (OpenAI now, Crusoe Nemotron later)."""

import sys
from pathlib import Path

_ENGINE = str(Path(__file__).resolve().parent.parent / "engine")
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)

"""Model endpoint settings. Switching from OpenAI to Crusoe (Nemotron) is env only.

ETCH_BASE_URL         OpenAI-compatible base URL (default https://api.openai.com/v1)
ETCH_MODEL            lead agent model
ETCH_SUB_MODEL        sub-agent model (spec writer, feature writer, part scout)
ETCH_API_KEY_SOURCE   macOS Keychain service name that holds the key (default openai-api-key)
ETCH_MAX_ITERS        repair iterations per build / feature loop (default 4)
ETCH_LEAD_MAX_STEPS   tool calls the lead may make per request (default 8)
ETCH_REASONING_EFFORT / ETCH_SUB_REASONING_EFFORT   OpenAI reasoning effort (medium / low); ignored off OpenAI

Keys are only ever read from the Keychain (or an env var of the same purpose); never from a file.
"""

from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # pcb-loop/
ENGINE = ROOT / "engine"
OUT = ENGINE / "out"

BASE_URL = os.environ.get("ETCH_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get(
    "ETCH_MODEL", "gpt-5-mini"
)  # closest to Nemotron 3 Super (research/model-pick.md)
SUB_MODEL = os.environ.get(
    "ETCH_SUB_MODEL", "gpt-5-nano"
)  # closest to Nemotron 3 Nano / 3.5 Lightning
KEY_SOURCE = os.environ.get("ETCH_API_KEY_SOURCE", "openai-api-key")
MAX_ITERS = int(os.environ.get("ETCH_MAX_ITERS", "4"))
LEAD_MAX_STEPS = int(os.environ.get("ETCH_LEAD_MAX_STEPS", "8"))
# optional, for reasoning models that accept it (OpenAI gpt-5 family): minimal|low|medium|high
REASONING = os.environ.get("ETCH_REASONING_EFFORT", "medium")
SUB_REASONING = os.environ.get("ETCH_SUB_REASONING_EFFORT", "low")

# render size: small on purpose (disk is tight)
FRAME_W = int(os.environ.get("ETCH_FRAME_W", "1380"))
FRAME_H = int(os.environ.get("ETCH_FRAME_H", "1020"))


def keychain(service: str) -> str:
    try:
        return (
            subprocess.check_output(
                [
                    "security",
                    "find-generic-password",
                    "-a",
                    os.environ["USER"],
                    "-s",
                    service,
                    "-w",
                ],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except subprocess.CalledProcessError:
        return ""


@functools.lru_cache(maxsize=1)
def api_key() -> str:
    env = {"openai-api-key": "OPENAI_API_KEY", "crusoe-api-key": "CRUSOE_API_KEY"}.get(
        KEY_SOURCE
    )
    if env and os.environ.get(env):
        return os.environ[env]
    k = keychain(KEY_SOURCE)
    if not k:
        raise RuntimeError(
            f"no API key in Keychain item '{KEY_SOURCE}' (set ETCH_API_KEY_SOURCE)"
        )
    return k


def you_key() -> str:
    return os.environ.get("YOU_API_KEY") or keychain("you-api-key")

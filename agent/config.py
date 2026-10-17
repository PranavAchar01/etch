"""Model endpoint settings. Primary = Crusoe Managed Inference (Nemotron), fallback = OpenAI.

ETCH_BASE_URL         OpenAI-compatible base URL (default https://api.inference.crusoecloud.com/v1)
ETCH_MODEL            lead agent model (default nvidia/NVIDIA-Nemotron-3-Super-120B-A12B)
ETCH_SUB_MODEL        sub-agent model (default nvidia/Nemotron-3.5-Lightning-30B-A3B)
ETCH_API_KEY_SOURCE   Keychain service that holds the key (default jarvis-vault/Crusoe)
ETCH_PRIMARY_TIMEOUT  seconds per primary attempt (default 30); a timed-out, 429 or 5xx primary call is tried once
                      more on the primary, then the fallback takes over
ETCH_RATE_PER_MIN     client-side cap on primary (Crusoe) requests per rolling minute, shared by every Etch process
                      on the machine (default 28; Crusoe's free tier allows 30)
ETCH_MAX_TOKENS       output cap per primary call (default 4000); a runaway generation ends instead of timing out
ETCH_FALLBACK         "openai" (default) or "off"
ETCH_THINKING         "off" (default) or "on": Nemotron reasoning on non-OpenAI endpoints. Off is about 8x faster
                      (Super 3.5 s vs 27 s on a picker-sized call) and the analog solver does the numbers.
ETCH_FALLBACK_BASE_URL / ETCH_FALLBACK_MODEL / ETCH_FALLBACK_SUB_MODEL / ETCH_FALLBACK_KEY_SOURCE
                      the fallback endpoint (default OpenAI gpt-5-mini / gpt-5-nano, Keychain openai-api-key)
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

CRUSOE_URL = "https://api.inference.crusoecloud.com/v1"
OPENAI_URL = "https://api.openai.com/v1"
BASE_URL = os.environ.get("ETCH_BASE_URL", CRUSOE_URL)
MODEL = os.environ.get(
    "ETCH_MODEL", "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B"
)  # research/model-pick.md (lead)
SUB_MODEL = os.environ.get(
    "ETCH_SUB_MODEL", "nvidia/Nemotron-3.5-Lightning-30B-A3B"
)  # research/model-pick.md (sub-agents)
KEY_SOURCE = os.environ.get("ETCH_API_KEY_SOURCE", "jarvis-vault/Crusoe")
PRIMARY_TIMEOUT = float(os.environ.get("ETCH_PRIMARY_TIMEOUT", "30"))
# hedge: a primary (Crusoe) call with no answer after this many seconds also goes to the fallback model in
# parallel, and the first answer wins (the logged Nemotron picker p90 is 10.9 s; stalls cost 42-44 s). 0 = off.
HEDGE_S = float(os.environ.get("ETCH_HEDGE_S", "12"))
RATE_PER_MIN = int(os.environ.get("ETCH_RATE_PER_MIN", "28"))
MAX_TOKENS = int(os.environ.get("ETCH_MAX_TOKENS", "4000"))
FALLBACK = os.environ.get("ETCH_FALLBACK", "openai").lower()
THINKING = os.environ.get("ETCH_THINKING", "off").lower() == "on"
FALLBACK_BASE_URL = os.environ.get("ETCH_FALLBACK_BASE_URL", OPENAI_URL)
FALLBACK_MODEL = os.environ.get("ETCH_FALLBACK_MODEL", "gpt-5-mini")
FALLBACK_SUB_MODEL = os.environ.get("ETCH_FALLBACK_SUB_MODEL", "gpt-5-nano")
FALLBACK_KEY_SOURCE = os.environ.get("ETCH_FALLBACK_KEY_SOURCE", "openai-api-key")
MAX_ITERS = int(os.environ.get("ETCH_MAX_ITERS", "4"))
LEAD_MAX_STEPS = int(os.environ.get("ETCH_LEAD_MAX_STEPS", "8"))
# optional, for reasoning models that accept it (OpenAI gpt-5 family): minimal|low|medium|high
REASONING = os.environ.get("ETCH_REASONING_EFFORT", "medium")
SUB_REASONING = os.environ.get("ETCH_SUB_REASONING_EFFORT", "low")

# render size: small on purpose (disk is tight)
FRAME_W = int(os.environ.get("ETCH_FRAME_W", "1380"))
FRAME_H = int(os.environ.get("ETCH_FRAME_H", "1020"))


def keychain(service: str) -> str:
    """Read a Keychain password by service: first the item under this user's account (the old
    openai-api-key style), then any account (jarvis vault items are stored under account "-")."""
    for acct in (["-a", os.environ.get("USER", "")], []):
        try:
            out = (
                subprocess.check_output(
                    ["security", "find-generic-password", *acct, "-s", service, "-w"],
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        if out:
            return out
    return ""


# env var that may carry a key in place of the Keychain (Linux / Docker); Keychain source -> env name
KEY_ENV = {
    "openai-api-key": "OPENAI_API_KEY",
    "crusoe-api-key": "CRUSOE_API_KEY",
    "jarvis-vault/Crusoe": "CRUSOE_API_KEY",
    "you-api-key": "YOU_API_KEY",
    "jarvis-vault/You.com": "YOU_API_KEY",
}


@functools.lru_cache(maxsize=8)
def key_for(source: str) -> str:
    env = KEY_ENV.get(source)
    if env and os.environ.get(env):
        return os.environ[env]
    return keychain(source)


def api_key() -> str:
    k = key_for(KEY_SOURCE)
    if not k:
        raise RuntimeError(
            f"no API key in Keychain item '{KEY_SOURCE}' or env {KEY_ENV.get(KEY_SOURCE, '?')} (set ETCH_API_KEY_SOURCE)"
        )
    return k


def fallback_key() -> str:
    return key_for(FALLBACK_KEY_SOURCE)


def fallback_model(model: str) -> str:
    """The fallback model for a primary model id: lead -> FALLBACK_MODEL, everything else that is
    not already an OpenAI id -> FALLBACK_SUB_MODEL."""
    if model == MODEL:
        return FALLBACK_MODEL
    if model == SUB_MODEL:
        return FALLBACK_SUB_MODEL
    if model.startswith(("gpt-", "o1", "o3", "o4")):
        return model
    return FALLBACK_SUB_MODEL


def you_key() -> str:
    return (
        os.environ.get("YOU_API_KEY")
        or keychain("jarvis-vault/You.com")
        or keychain("you-api-key")
    )

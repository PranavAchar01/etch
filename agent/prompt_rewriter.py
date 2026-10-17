"""OpenAI prompt rewriter in front of the NVIDIA prompt classifier (agent/task_router.py).

User asks are messy ("cretae a gps tracker like an airtag", "mp4 player", "done picking parts?"). One small, fast
OpenAI call turns the raw ask into one clean, explicit sentence of the kind the classifier scores well: spelling
fixed, the device named, its key parts and constraints stated, and a question or chat message labelled as such.

The rewrite feeds the classifier and the router only. The design itself keeps the user's own text (the picker, the
regex prefilter, recipes and the device catalog all match on the raw ask).

Fail open: no key, an HTTP error, a bad answer or more than ETCH_REWRITE_TIMEOUT seconds (default 1.5) and the raw
text is used, so latency and reliability never get worse. Results are cached by exact text, including answers that
arrive after the deadline (the next identical ask hits the cache). ETCH_REWRITE=0 turns it off.

Stdlib only: the backend (deploy/etch_public.py) runs on the system python with no packages.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import http.client
import urllib.parse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

from agent import config

MODEL = os.environ.get("ETCH_REWRITE_MODEL", "gpt-4.1-mini")
TIMEOUT_S = float(os.environ.get("ETCH_REWRITE_TIMEOUT", "1.5"))
KEY_SOURCE = os.environ.get("ETCH_REWRITE_KEY_SOURCE", "openai-api-key")
URL = os.environ.get("ETCH_REWRITE_URL", config.OPENAI_URL) + "/chat/completions"
DEVICES = Path(
    os.environ.get("ETCH_DEVICES_FILE", str(config.ROOT / "engine/parts/devices.json"))
)
KINDS = ("board", "question", "chat")
MAX_IN = 1000
MAX_OUT = 400
CACHE_MAX = 512

PROMPT = """You rewrite one message typed into Etch, a tool that designs small through-hole circuit boards (PCBs) from a
text description. Your output is read by a prompt classifier that scores task type and complexity, so write the
single clean, explicit sentence that classifier handles best.

Rules:
- Fix spelling and grammar. Expand terse asks ("mp4 player", "keyboard") into a full request.
- For a board request: start with "Design a circuit board for", name the device, then its likely key parts and any
  constraints the user gave (size, battery, wireless, number of LEDs/keys, chips). Keep every chip, module or
  topology word the user wrote (555, esp32, arduino, pico, op amp, "no microcontroller") exactly. Do not invent
  numbers or constraints the user did not give, and do not mention what they left out; key parts may be the
  obvious ones for that device.
- For a question about a build, the app or progress ("done picking parts?", "why did it fail"): start with
  "Question about the current build:" and restate the question. Do not turn it into a board request.
- For greetings, thanks or other chat: start with "Chat message, not a board request:" and restate it.
- Name the device in plain, common words ("gps tracker", "mp4 player", "macropad"), never a product code you made up.
- One sentence, at most 40 words. English.

Answer with JSON only: {"kind": "board" | "question" | "chat", "text": "..."}"""

_lock = threading.Lock()
_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rewrite")
_devices: dict[str, Any] = {}


def enabled() -> bool:
    return os.environ.get("ETCH_REWRITE", "1") != "0"


def aliases() -> list[str]:
    """Every base-device alias in the device catalog (engine/parts/devices.json, entries with variant null), longest
    first. Variants are left out: the catalog adds them from words in the ask. Empty when the file is not there."""
    if "aliases" not in _devices:
        al: set[str] = set()
        try:
            for d in json.loads(DEVICES.read_text())["devices"]:
                if d.get("variant") is None:
                    al.update(" ".join(a.lower().split()) for a in d.get("aliases") or [] if a and a.strip())
        except (OSError, ValueError, KeyError, TypeError):
            pass
        _devices["aliases"] = sorted(al, key=lambda a: (-len(a), a))
    return _devices["aliases"]


def _words(s: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", s.lower())) + " "


def _longest(text: str) -> str | None:
    t = _words(text)
    for a in aliases():
        if _words(a) in t:
            return a
    return None


_SUBJECT = re.compile(
    r"circuit board for (?:an? |the )?(.+?)(?:,| with | including | that | which | using | to | similar | like |\.|$)",
    re.I,
)


def device_for(text: str, raw: str = "") -> str | None:
    """The catalog alias (engine/parts/devices.json) naming the device: the longest alias inside the rewrite's
    subject ("Design a circuit board for <subject>, ..."), else the longest one in the raw ask, else None. Matched
    in code (the catalog's own whole-word rule) so the model never has to carry the 329-device list, and the
    subject comes first so a part named later ("a controller", "a timer") never wins over the device."""
    m = _SUBJECT.search(text)
    return (_longest(m.group(1)) if m else None) or (_longest(raw) if raw else None)


_idle: list[http.client.HTTPSConnection] = []  # keep-alive connections not in use, shared by every thread


def _post(body: bytes, key: str) -> bytes:
    """POST over a reused keep-alive connection (no TLS handshake per ask: p50 1.5 s -> 1.0 s measured). A
    connection the server closed while idle fails at once; that one is dropped and a fresh one tried."""
    u = urllib.parse.urlsplit(URL)
    hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for attempt in (0, 1):
        with _lock:
            c = _idle.pop() if _idle and not attempt else None
        c = c or http.client.HTTPSConnection(u.netloc, timeout=TIMEOUT_S + 5)
        try:
            c.request("POST", u.path, body, hdr)
            r = c.getresponse()
            data = r.read()
        except (http.client.HTTPException, OSError):
            c.close()
            if attempt:
                raise
            continue
        with _lock:
            if len(_idle) < 4:
                _idle.append(c)
            else:
                c.close()
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return data
    raise RuntimeError("unreachable")


def warm() -> None:
    """Open one keep-alive connection and read the key ahead of the first ask (backend start). Never raises."""
    if not enabled():
        return

    def go() -> None:
        try:
            config.key_for(KEY_SOURCE)
            c = http.client.HTTPSConnection(urllib.parse.urlsplit(URL).netloc, timeout=10)
            c.connect()
            with _lock:
                _idle.append(c)
        except Exception:  # noqa: BLE001 - warming is best effort
            pass

    _pool.submit(go)


def _call(text: str, key: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": text},
        ],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 200,
    }
    if MODEL.startswith(
        "gpt-5"
    ):  # reasoning models: as little thinking as the model allows
        body["reasoning_effort"] = "none" if MODEL.startswith("gpt-5.") else "minimal"
    else:
        body["temperature"] = 0
    msg = json.loads(_post(json.dumps(body).encode(), key))["choices"][0]["message"]["content"]
    return parse(msg, text)


def parse(content: str, raw: str = "") -> dict[str, Any]:
    """Validate the model's JSON. Raises ValueError on anything unusable."""
    d = json.loads(content)
    if not isinstance(d, dict):
        raise ValueError("not an object")
    kind = str(d.get("kind", "")).lower()
    text = " ".join(str(d.get("text") or "").split())
    if kind not in KINDS or not text or len(text) > MAX_OUT:
        raise ValueError("bad kind or text")
    return {"kind": kind, "text": text, "device": device_for(text, raw) if kind == "board" else None}


def _remember(raw: str, res: dict[str, Any]) -> None:
    with _lock:
        _cache[raw] = res
        _cache.move_to_end(raw)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def _work(raw: str, key: str) -> dict[str, Any]:
    res = _call(raw, key)
    _remember(raw, res)  # kept even when the caller already gave up waiting
    return res


def rewrite(raw: str) -> dict[str, Any]:
    """Always returns a dict. "text" is what the classifier should read: the rewrite when ok, else the raw ask.
    Fields: raw, text, kind, device, ok, cached, seconds, model, and error when not ok. Never raises."""
    raw = (raw or "")[:MAX_IN]
    t0 = time.time()
    base: dict[str, Any] = {"raw": raw, "model": MODEL}

    def fail(err: str) -> dict[str, Any]:
        return {
            **base,
            "text": raw,
            "kind": None,
            "device": None,
            "ok": False,
            "cached": False,
            "seconds": round(time.time() - t0, 3),
            "error": err,
        }

    if not enabled():
        return fail("off")
    if not raw.strip():
        return fail("empty")
    with _lock:
        hit = _cache.get(raw)
    if hit:
        return {
            **base,
            **hit,
            "ok": True,
            "cached": True,
            "seconds": round(time.time() - t0, 3),
        }
    try:
        key = config.key_for(KEY_SOURCE)
    except Exception as e:  # noqa: BLE001 - fail open
        return fail(f"key: {type(e).__name__}")
    if not key:
        return fail("no key")
    try:
        res = _pool.submit(_work, raw, key).result(timeout=TIMEOUT_S)
    except FutureTimeout:
        return fail("timeout")
    except Exception as e:  # noqa: BLE001 - HTTP, JSON or validation error: fail open
        return fail(type(e).__name__)
    return {
        **base,
        **res,
        "ok": True,
        "cached": False,
        "seconds": round(time.time() - t0, 3),
    }


def event(res: dict[str, Any]) -> dict[str, Any]:
    """The run event (agent lead, type rewrite) for a rewrite() result: no key, nothing but the texts."""
    ev = {
        "agent": "lead",
        "type": "rewrite",
        "t": 0,
        "ok": res["ok"],
        "model": res["model"],
        "seconds": res["seconds"],
        "cached": res["cached"],
        "kind": res["kind"],
        "device": res["device"],
        "text": res["text"],
    }
    if not res["ok"]:
        ev["error"] = res.get("error")
    return ev


if __name__ == "__main__":
    import sys

    for a in sys.argv[1:]:
        print(json.dumps(rewrite(a)))

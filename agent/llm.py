"""OpenAI-compatible chat client. The same code talks to OpenAI or to Crusoe Managed Inference;
only ETCH_BASE_URL / ETCH_MODEL / ETCH_SUB_MODEL / ETCH_API_KEY_SOURCE change."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from . import config
from .events import Stream

_client: Any = (
    None  # openai.OpenAI, imported on first use (a stored board never needs it)
)


class ModelError(RuntimeError):
    """A model call failed on the primary endpoint and on the fallback (when one is configured)."""


# Provider order (2026-10-17): Crusoe (Nemotron) first; on an error or timeout the same call goes once to
# the OpenAI fallback, visibly (an `llm_fallback` event), and later calls in this process stay on the
# fallback for FALLBACK_STICKY_S so one dead endpoint does not cost every call a timeout.
FALLBACK_STICKY_S = 600
PRIMARY_TRIES = (
    2  # one more primary attempt after a timeout, 429 or 5xx before the fallback
)
_clients: dict[str, Any] = {}
_fallback_since: float | None = None


# ---- OpenAI spend meter (Pranav's overnight rule, 2026-10-17: the account keeps at least $70).
# Every call's returned token usage is priced at the published per-1M rates and appended to
# qa/openai-meter.jsonl; once the total reaches the budget in qa/openai-budget.json, calls stop.
METER = config.ROOT / "qa" / "openai-meter.jsonl"
BUDGET_FILE = config.ROOT / "qa" / "openai-budget.json"
PRICES = {  # USD per 1M tokens (input, output), openai.com/api/pricing
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5": (1.25, 10.00),
}


def spent() -> float:
    try:
        return sum(
            json.loads(x).get("usd", 0.0)
            for x in METER.read_text().splitlines()
            if x.strip()
        )
    except OSError:
        return 0.0


def budget() -> float | None:
    try:
        return float(json.loads(BUDGET_FILE.read_text())["budget_usd"])
    except (OSError, ValueError, KeyError):
        return None


def meter(
    model: str, agent: str, tin: int, tout: int, usd: float | None = None
) -> float:
    if usd is None:
        pin, pout = PRICES.get(model, PRICES["gpt-5"])  # unknown model: price it high
        usd = (tin * pin + tout * pout) / 1e6
    METER.parent.mkdir(parents=True, exist_ok=True)
    with METER.open("a") as f:
        f.write(
            json.dumps(
                {
                    "ts": round(time.time(), 1),
                    "model": model,
                    "agent": agent,
                    "in": tin,
                    "out": tout,
                    "usd": round(usd, 6),
                }
            )
            + "\n"
        )
    return usd


def _is_openai(base_url: str) -> bool:
    return "api.openai.com" in base_url


def guard(base_url: str | None = None) -> None:
    b = budget()
    if _is_openai(base_url or config.BASE_URL) and b is not None and spent() >= b:
        raise ModelError(
            f"OpenAI budget reached (${b:.2f}); model calls are stopped for tonight."
        )


def _client_for(base_url: str, key: str) -> Any:
    c = _clients.get(base_url)
    if c is None:
        from openai import OpenAI

        c = _clients[base_url] = OpenAI(
            base_url=base_url, api_key=key, timeout=45, max_retries=0
        )
    return c


def client() -> Any:
    return _client_for(config.BASE_URL, config.api_key())


def _is_openai_reasoning(model: str, base_url: str | None = None) -> bool:
    return _is_openai(base_url or config.BASE_URL) and bool(
        re.match(r"(gpt-5|o\d)", model)
    )


def _fallback_ready() -> bool:
    return (
        config.FALLBACK == "openai"
        and config.FALLBACK_BASE_URL != config.BASE_URL
        and bool(config.fallback_key())
    )


def _status(e: Exception) -> int | None:
    return getattr(e, "status_code", None)


# ---- Client-side pacing for the primary endpoint. Crusoe's free tier allows 30 requests per minute and no
# card will be added, so every Etch process on this machine (backend, QA builds) shares one rolling window
# kept in a small file under an flock. A request waits for a slot instead of drawing a 429.
RATE_FILE = config.OUT / ".primary-rate.json"
RATE_WINDOW_S = 60.0
RETRY_AFTER_MAX_S = 20.0


def _pace(base_url: str, now=time.time, sleep=time.sleep) -> float:
    """Block until the rolling window has a free slot, then record this request. Returns seconds waited."""
    if _is_openai(base_url) or config.RATE_PER_MIN <= 0:
        return 0.0
    import fcntl

    RATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    waited = 0.0
    with open(RATE_FILE.with_suffix(".lock"), "a") as lk:
        while True:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                try:
                    stamps = json.loads(RATE_FILE.read_text())
                except (OSError, ValueError):
                    stamps = []
                t = now()
                stamps = [s for s in stamps if t - s < RATE_WINDOW_S]
                if len(stamps) < config.RATE_PER_MIN:
                    stamps.append(t)
                    RATE_FILE.write_text(json.dumps(stamps))
                    return round(waited, 2)
                wait = RATE_WINDOW_S - (t - min(stamps)) + 0.05
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
            sleep(wait)
            waited += wait


def _retry_after(e: Exception) -> float:
    """Seconds a 429 asks us to wait (Retry-After header), capped; 2 s when it gives none."""
    h = getattr(getattr(e, "response", None), "headers", None) or {}
    try:
        return min(float(h.get("retry-after", 2)), RETRY_AFTER_MAX_S)
    except (TypeError, ValueError):
        return 2.0


def _retryable(e: Exception) -> bool:
    s = _status(e)
    return s is None or s == 429 or s >= 500  # None = timeout / connection error


def _create(base_url: str, key: str, model: str, kw: dict[str, Any], effort, timeout):
    """One request to one endpoint. Off OpenAI, reasoning_effort is never sent; a named tool_choice that
    the server rejects with a 5xx/4xx (Crusoe's Lightning 500s on it) is retried once as "auto" (the
    caller still reads a structured answer from the tool call or the JSON in the text)."""
    kw = dict(kw, model=model)
    if _is_openai_reasoning(model, base_url) and effort:
        kw["reasoning_effort"] = effort
    if not _is_openai(base_url):
        # Nemotron's chat template switch; thinking off keeps a picker call to a few seconds
        kw["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": config.THINKING}
        }
        if config.MAX_TOKENS > 0:
            kw.setdefault("max_tokens", config.MAX_TOKENS)
    guard(base_url)
    c = _client_for(base_url, key).with_options(timeout=timeout, max_retries=0)
    try:
        _pace(base_url)
        return c.chat.completions.create(**kw)
    except Exception as e:
        tc = kw.get("tool_choice")
        if (
            not _is_openai(base_url)
            and isinstance(tc, dict)
            and (_status(e) or 0) in (400, 422, 500)
        ):
            name = tc.get("function", {}).get("name", "")
            msgs = list(kw["messages"]) + [
                {
                    "role": "system",
                    "content": f"Answer by calling the tool `{name}` exactly once with valid JSON arguments.",
                }
            ]
            _pace(base_url)
            return c.chat.completions.create(
                **dict(kw, tool_choice="auto", messages=msgs)
            )
        raise


def _metered(base_url: str, key: str, model: str, kw, effort, timeout, agent: str):
    resp = _create(base_url, key, model, kw, effort, timeout)
    if _is_openai(base_url):
        u = resp.usage
        meter(model, agent, getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0)
    return resp


def _hedged(stream: Stream, agent: str, model: str, kw, effort, ptimeout: float, timeout: float | None):
    """One primary request. When it has not answered after config.HEDGE_S and a fallback is configured, the same
    request also goes to the fallback model and the first answer wins: a stalled Crusoe call costs about 12 s
    instead of the 30 s timeout plus a retry (report rank 2). Returns (response, fallback model or None). A
    primary error inside the hedge window raises as before (the caller retries or falls back)."""
    import concurrent.futures as cf

    if config.HEDGE_S <= 0 or not _fallback_ready() or _is_openai(config.BASE_URL) or ptimeout <= config.HEDGE_S:
        return _create(config.BASE_URL, config.api_key(), model, kw, effort, ptimeout), None
    pool = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-hedge")
    try:
        prim = pool.submit(_create, config.BASE_URL, config.api_key(), model, kw, effort, ptimeout)
        if cf.wait([prim], timeout=config.HEDGE_S).done:
            return prim.result(), None  # an answer, or the primary's own error for the caller's retry/fallback
        fb_model = config.fallback_model(model)
        stream.emit(agent, "llm_hedge", model=model, to=fb_model, after_s=config.HEDGE_S)
        fb = pool.submit(
            _metered, config.FALLBACK_BASE_URL, config.fallback_key(), fb_model, kw, effort, timeout or 45, agent
        )
        pending = {prim, fb}
        err: Exception | None = None
        while pending:
            done, pending = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for f in done:
                try:
                    r = f.result()
                except Exception as e:  # the other request may still answer
                    stream.emit(agent, "llm_error", model=model if f is prim else fb_model, error=str(e)[:300])
                    err = e
                    continue
                stream.emit(agent, "llm_hedge_won", winner="primary" if f is prim else "fallback")
                return r, (None if f is prim else fb_model)
        raise ModelError(f"{model} and the hedged {fb_model} both failed: {str(err)[:200]}") from err
    finally:
        pool.shutdown(wait=False)  # the losing request finishes in the background and is dropped


def chat(
    stream: Stream,
    agent: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
    effort: str | None = None,
    timeout: float | None = None,
) -> Any:
    """One completion; emits an `llm` event with model, provider, latency and token counts."""
    global _fallback_since
    kw: dict[str, Any] = {"messages": messages}
    if tools:
        kw["tools"] = tools
        if tool_choice is not None:
            kw["tool_choice"] = tool_choice
    effort = effort or (config.REASONING if agent == "lead" else config.SUB_REASONING)
    t0 = time.time()
    base, used = config.BASE_URL, model
    sticky = (
        _fallback_since is not None
        and time.time() - _fallback_since < FALLBACK_STICKY_S
    )
    resp = None
    metered = False  # a hedged fallback call meters itself (the losing call is billed too)
    if not (sticky and _fallback_ready()):
        ptimeout = timeout or 45
        if not _is_openai(base):
            ptimeout = min(ptimeout, config.PRIMARY_TIMEOUT)
        tries = 1 if _is_openai(base) else PRIMARY_TRIES
        for attempt in range(1, tries + 1):
            ta = time.time()
            try:
                resp, hedged = _hedged(stream, agent, model, kw, effort, ptimeout, timeout)
                if hedged:
                    base, used, metered = config.FALLBACK_BASE_URL, hedged, True
                break
            except ModelError:
                raise  # the hedge already tried the primary and the fallback
            except Exception as e:
                secs = round(time.time() - ta, 1)
                stream.emit(
                    agent, "llm_error", model=model, seconds=secs, error=str(e)[:300]
                )
                if attempt < tries and _retryable(e):
                    wait = _retry_after(e) if _status(e) == 429 else 0.0
                    stream.emit(
                        agent,
                        "llm_retry",
                        model=model,
                        attempt=attempt + 1,
                        wait_s=wait,
                    )
                    time.sleep(wait)
                    continue
                if not _fallback_ready():
                    raise ModelError(
                        f"{model} failed after {secs} s ({type(e).__name__}): {str(e)[:200]}. No fallback configured."
                    ) from e
                if (
                    _status(e) != 429
                ):  # a rate limit clears in seconds; do not park the process on OpenAI
                    _fallback_since = time.time()
                stream.emit(
                    agent,
                    "llm_fallback",
                    model=model,
                    to=config.fallback_model(model),
                    reason=f"{type(e).__name__}: {str(e)[:160]}",
                )
                break
    if resp is None:
        base, used = config.FALLBACK_BASE_URL, config.fallback_model(model)
        t1 = time.time()
        try:
            resp = _create(base, config.fallback_key(), used, kw, effort, timeout or 45)
        except Exception as e:
            secs = round(time.time() - t1, 1)
            stream.emit(
                agent, "llm_error", model=used, seconds=secs, error=str(e)[:300]
            )
            raise ModelError(
                f"{used} (fallback) failed after {secs} s ({type(e).__name__}): {str(e)[:200]}."
            ) from e
    u = resp.usage
    if _is_openai(base) and not metered:
        meter(
            used,
            agent,
            getattr(u, "prompt_tokens", 0) or 0,
            getattr(u, "completion_tokens", 0) or 0,
        )
    msg = resp.choices[0].message
    stream.emit(
        agent,
        "llm",
        model=used,
        provider="openai"
        if _is_openai(base)
        else "crusoe"
        if "crusoe" in base
        else base,
        fallback=base != config.BASE_URL,
        seconds=round(time.time() - t0, 2),
        tokens_in=getattr(u, "prompt_tokens", None),
        tokens_out=getattr(u, "completion_tokens", None),
        tool_calls=[c.function.name for c in (msg.tool_calls or [])],
    )
    return msg


def call_json(
    stream: Stream,
    agent: str,
    model: str,
    messages: list[dict[str, Any]],
    fn: dict[str, Any],
    effort: str | None = None,
    timeout: float | None = None,
) -> tuple[dict[str, Any], Any]:
    """Ask for one structured answer through a forced tool call; fall back to JSON in the text
    (some open-model servers ignore a named tool_choice)."""
    tool = {"type": "function", "function": fn}
    msg = chat(
        stream,
        agent,
        model,
        messages,
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": fn["name"]}},
        effort=effort,
        timeout=timeout,
    )
    if msg.tool_calls:
        return json.loads(msg.tool_calls[0].function.arguments or "{}"), msg
    text = re.sub(r"<think>.*?</think>", "", msg.content or "", flags=re.DOTALL)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"{agent}: model returned no structured answer: {text[:300]}")
    return json.loads(m.group(0)), msg

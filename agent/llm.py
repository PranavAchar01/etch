"""OpenAI-compatible chat client. The same code talks to OpenAI or to Crusoe Managed Inference;
only ETCH_BASE_URL / ETCH_MODEL / ETCH_SUB_MODEL / ETCH_API_KEY_SOURCE change."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from . import config
from .events import Stream

_client: Any = None  # openai.OpenAI, imported on first use (a stored board never needs it)


class ModelError(RuntimeError):
    """A model call failed. The run stops and says so; nothing is retried or swapped for another model."""


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


def guard() -> None:
    b = budget()
    if "api.openai.com" in config.BASE_URL and b is not None and spent() >= b:
        raise ModelError(
            f"OpenAI budget reached (${b:.2f}); model calls are stopped for tonight."
        )


def client() -> Any:
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(
            base_url=config.BASE_URL,
            api_key=config.api_key(),
            # fail fast: one attempt, never a silent retry or a slower fallback model (Pranav, 2026-10-16)
            timeout=45,
            max_retries=0,
        )
    return _client


def _is_openai_reasoning(model: str) -> bool:
    return "api.openai.com" in config.BASE_URL and bool(re.match(r"(gpt-5|o\d)", model))


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
    """One completion; emits an `llm` event with model, latency and token counts."""
    kw: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        kw["tools"] = tools
        if tool_choice is not None:
            kw["tool_choice"] = tool_choice
    effort = effort or (config.REASONING if agent == "lead" else config.SUB_REASONING)
    if _is_openai_reasoning(model) and effort:
        kw["reasoning_effort"] = effort
    guard()
    t0 = time.time()
    c = client().with_options(timeout=timeout, max_retries=0) if timeout else client()
    try:
        resp = c.chat.completions.create(**kw)
    except Exception as e:
        secs = round(time.time() - t0, 1)
        stream.emit(agent, "llm_error", model=model, seconds=secs, error=str(e)[:300])
        raise ModelError(
            f"{model} failed after {secs} s ({type(e).__name__}): {str(e)[:200]}. Not retried."
        ) from e
    u = resp.usage
    if "api.openai.com" in config.BASE_URL:
        meter(
            model,
            agent,
            getattr(u, "prompt_tokens", 0) or 0,
            getattr(u, "completion_tokens", 0) or 0,
        )
    msg = resp.choices[0].message
    stream.emit(
        agent,
        "llm",
        model=model,
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
    text = msg.content or ""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"{agent}: model returned no structured answer: {text[:300]}")
    return json.loads(m.group(0)), msg

"""Latency report P1/P6 (2026-10-17): hedge near the p95 and never to the same model, a 503 capacity_unavailable
switches Super to Lightning at once, and Crusoe's rate-limit headers pause every process before a 429."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import config, llm
from agent.events import Stream

SUPER, LIGHT = "nvidia/Super", "nvidia/Lightning"


def _resp():
    msg = SimpleNamespace(tool_calls=None, content="hi")
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


class E503(Exception):
    status_code = 503

    def __init__(self):
        super().__init__("Error code: 503 - capacity_unavailable")


@pytest.fixture(autouse=True)
def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "_fallback_since", None)
    monkeypatch.setattr(llm, "_capacity_out", {})
    monkeypatch.setattr(llm, "_durations", {})
    monkeypatch.setattr(llm, "METER", tmp_path / "meter.jsonl")
    monkeypatch.setattr(llm, "RATE_FILE", tmp_path / "rate.json")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "BASE_URL", config.CRUSOE_URL)
    monkeypatch.setattr(config, "MODEL", SUPER)
    monkeypatch.setattr(config, "SUB_MODEL", LIGHT)
    # the live env: the fallback is the same Super on a second Crusoe project
    monkeypatch.setattr(config, "FALLBACK", "openai")
    monkeypatch.setattr(config, "FALLBACK_BASE_URL", config.CRUSOE_URL + "/")
    monkeypatch.setattr(config, "FALLBACK_MODEL", SUPER)
    monkeypatch.setattr(config, "FALLBACK_SUB_MODEL", LIGHT)
    monkeypatch.setattr(config, "api_key", lambda: "k1")
    monkeypatch.setattr(config, "fallback_key", lambda: "k2")


def test_hedge_never_goes_to_the_same_super():
    assert config.fallback_model(SUPER) == SUPER
    assert llm.hedge_model(SUPER) == LIGHT
    assert llm.hedge_model(LIGHT) == LIGHT


def test_hedge_waits_for_the_p95():
    assert llm.hedge_after(SUPER) == config.HEDGE_S == 28.0
    for s in [14.0] * 17 + [20.0, 33.0, 40.0]:
        llm._record(SUPER, s)
    assert llm.hedge_after(SUPER) == 33.0  # rolling p95 of 20 calls
    for _ in range(50):
        llm._record(LIGHT, 2.0)
    assert llm.hedge_after(LIGHT) == 12.0  # clamped: never a hair-trigger hedge


def test_503_switches_to_lightning_at_once_and_sticks(monkeypatch):
    calls = []

    def fake(base, key, model, kw, eff, to):
        calls.append((base, model))
        if model == SUPER:
            raise E503()
        return _resp()

    monkeypatch.setattr(llm, "_create", fake)
    s = Stream("t", echo=False)
    llm.chat(s, "picker", SUPER, [{"role": "user", "content": "x"}])
    assert calls == [
        (config.CRUSOE_URL, SUPER),
        (config.CRUSOE_URL, LIGHT),
    ]  # no second Super, no Super on project 2
    assert any(e["type"] == "llm_switch" for e in s.events)
    ev = [e for e in s.events if e["type"] == "llm"][0]
    assert ev["model"] == LIGHT and ev["fallback"] is False
    calls.clear()
    llm.chat(s, "picker", SUPER, [{"role": "user", "content": "y"}])
    assert calls == [
        (config.CRUSOE_URL, LIGHT)
    ]  # within CAPACITY_SWITCH_S: straight to Lightning


def test_rate_limit_headers_pause_every_process(tmp_path):
    t = [1000.0]
    assert (
        llm._dur("6m0s") == 360.0
        and llm._dur("250ms") == 0.25
        and llm._dur("1.5") == 1.5
    )
    assert (
        llm._note_limits(
            config.CRUSOE_URL,
            "k1",
            {"x-ratelimit-remaining-requests": "10"},
            now=lambda: t[0],
        )
        == 0
    )
    p = llm._note_limits(
        config.CRUSOE_URL,
        "k1",
        {"x-ratelimit-remaining-requests": "1", "x-ratelimit-reset-requests": "3s"},
        now=lambda: t[0],
    )
    assert p == 3.0
    slept = []
    waited = llm._pace(
        config.CRUSOE_URL, now=lambda: t[0], sleep=slept.append, key="k1"
    )
    assert slept == [3.0] and waited == 3.0
    assert (
        llm._note_limits(
            config.OPENAI_URL, "k1", {"x-ratelimit-remaining-requests": "0"}
        )
        == 0
    )


def test_send_reads_headers_from_the_raw_response(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "_note_limits", lambda b, k, h: seen.append(dict(h)))
    monkeypatch.setattr(config, "RATE_PER_MIN", 0)
    raw = SimpleNamespace(
        headers={"x-ratelimit-remaining-requests": "29"}, parse=lambda: "parsed"
    )
    c = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_raw_response=SimpleNamespace(create=lambda **kw: raw)
            )
        )
    )
    assert llm._send(c, config.CRUSOE_URL, "k1", {}) == "parsed"
    assert seen == [{"x-ratelimit-remaining-requests": "29"}]

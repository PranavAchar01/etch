"""Crusoe primary with OpenAI fallback (llm.chat). No network: _create is replaced."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent import config, llm
from agent.events import Stream


def _resp(args: dict | None = None, content: str = ""):
    tc = (
        [
            SimpleNamespace(
                function=SimpleNamespace(name="answer", arguments=json.dumps(args))
            )
        ]
        if args is not None
        else None
    )
    msg = SimpleNamespace(tool_calls=tc, content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


FN = {"name": "answer", "parameters": {"type": "object", "properties": {}}}


@pytest.fixture(autouse=True)
def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "_fallback_since", None)
    monkeypatch.setattr(llm, "METER", tmp_path / "meter.jsonl")
    monkeypatch.setattr(llm, "RATE_FILE", tmp_path / "rate.json")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "BASE_URL", config.CRUSOE_URL)
    monkeypatch.setattr(config, "FALLBACK", "openai")
    monkeypatch.setattr(config, "api_key", lambda: "k1")
    monkeypatch.setattr(config, "fallback_key", lambda: "k2")


def test_primary_ok_no_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(
        llm,
        "_create",
        lambda base, key, model, kw, eff, to: (
            calls.append((base, model)) or _resp({"a": 1})
        ),
    )
    s = Stream("t", echo=False)
    ans, _ = llm.call_json(
        s, "picker", config.MODEL, [{"role": "user", "content": "x"}], FN
    )
    assert ans == {"a": 1}
    assert calls == [(config.CRUSOE_URL, config.MODEL)]
    ev = [e for e in s.events if e["type"] == "llm"][0]
    assert ev["provider"] == "crusoe" and ev["fallback"] is False


def test_error_falls_back_and_sticks(monkeypatch):
    calls = []

    def fake(base, key, model, kw, eff, to):
        calls.append((base, model))
        if base == config.CRUSOE_URL:
            raise TimeoutError("slow")
        return _resp({"ok": True})

    monkeypatch.setattr(llm, "_create", fake)
    s = Stream("t", echo=False)
    ans, _ = llm.call_json(
        s, "picker", config.MODEL, [{"role": "user", "content": "x"}], FN
    )
    assert ans == {"ok": True}
    assert calls == [
        (config.CRUSOE_URL, config.MODEL),
        (config.CRUSOE_URL, config.MODEL),  # one retry on the primary
        (config.OPENAI_URL, config.FALLBACK_MODEL),
    ]
    assert any(e["type"] == "llm_retry" for e in s.events)
    assert any(e["type"] == "llm_fallback" for e in s.events)
    # next call goes straight to the fallback (sticky), sub model maps to the fallback sub model
    llm.call_json(
        s, "part-scout", config.SUB_MODEL, [{"role": "user", "content": "x"}], FN
    )
    assert calls[-1] == (config.OPENAI_URL, config.FALLBACK_SUB_MODEL)
    assert len(calls) == 4


def test_no_fallback_raises(monkeypatch):
    monkeypatch.setattr(config, "FALLBACK", "off")

    def fake(*a):
        raise RuntimeError("down")

    monkeypatch.setattr(llm, "_create", fake)
    with pytest.raises(llm.ModelError):
        llm.chat(
            Stream("t", echo=False),
            "lead",
            config.MODEL,
            [{"role": "user", "content": "x"}],
        )


def test_json_in_text_with_think_block(monkeypatch):
    monkeypatch.setattr(
        llm,
        "_create",
        lambda *a: _resp(None, '<think>{"bad": 1}</think> here {"value": 2}'),
    )
    ans, _ = llm.call_json(Stream("t", echo=False), "picker", config.MODEL, [], FN)
    assert ans == {"value": 2}


def test_reasoning_effort_only_on_openai():
    assert llm._is_openai_reasoning("gpt-5-mini", config.OPENAI_URL)
    assert not llm._is_openai_reasoning("gpt-5-mini", config.CRUSOE_URL)


def test_fallback_model_mapping():
    assert config.fallback_model(config.MODEL) == config.FALLBACK_MODEL
    assert config.fallback_model(config.SUB_MODEL) == config.FALLBACK_SUB_MODEL
    assert config.fallback_model("gpt-5") == "gpt-5"


def test_env_key_wins(monkeypatch):
    config.key_for.cache_clear()
    monkeypatch.setenv("CRUSOE_API_KEY", "env-key")
    try:
        assert config.key_for("jarvis-vault/Crusoe") == "env-key"
    finally:
        config.key_for.cache_clear()


def test_thinking_switch_only_off_openai(monkeypatch):
    sent = {}

    class C:
        def with_options(self, **_):
            return self

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            sent.update(kw)
            return _resp({"a": 1})

    monkeypatch.setattr(llm, "_client_for", lambda b, k: C())
    monkeypatch.setattr(llm, "guard", lambda b: None)
    llm._create(config.CRUSOE_URL, "k", config.MODEL, {"messages": []}, None, 5)
    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": config.THINKING}}
    sent.clear()
    llm._create(config.OPENAI_URL, "k", "gpt-5-mini", {"messages": []}, "low", 5)
    assert "extra_body" not in sent and sent["reasoning_effort"] == "low"


class _E429(Exception):
    status_code = 429

    def __init__(self, after="3"):
        super().__init__("rate limited")
        self.response = SimpleNamespace(headers={"retry-after": after})


def test_429_waits_and_retries_primary_without_sticking(monkeypatch):
    calls, slept = [], []
    monkeypatch.setattr(llm.time, "sleep", slept.append)

    def fake(base, key, model, kw, eff, to):
        calls.append(base)
        if len(calls) == 1:
            raise _E429("3")
        return _resp({"ok": 1})

    monkeypatch.setattr(llm, "_create", fake)
    ans, _ = llm.call_json(Stream("t", echo=False), "picker", config.MODEL, [], FN)
    assert ans == {"ok": 1} and calls == [config.CRUSOE_URL, config.CRUSOE_URL]
    assert slept == [3.0]
    assert llm._fallback_since is None


def test_429_twice_falls_back_but_does_not_stick(monkeypatch):
    calls = []

    def fake(base, key, model, kw, eff, to):
        calls.append(base)
        if base == config.CRUSOE_URL:
            raise _E429("99")
        return _resp({"ok": 1})

    monkeypatch.setattr(llm, "_create", fake)
    llm.call_json(Stream("t", echo=False), "picker", config.MODEL, [], FN)
    assert calls == [config.CRUSOE_URL, config.CRUSOE_URL, config.OPENAI_URL]
    assert llm._fallback_since is None
    assert llm._retry_after(_E429("99")) == llm.RETRY_AFTER_MAX_S


def test_400_goes_straight_to_fallback(monkeypatch):
    calls = []

    class E400(Exception):
        status_code = 400

    def fake(base, key, model, kw, eff, to):
        calls.append(base)
        if base == config.CRUSOE_URL:
            raise E400("bad")
        return _resp({"ok": 1})

    monkeypatch.setattr(llm, "_create", fake)
    llm.call_json(Stream("t", echo=False), "picker", config.MODEL, [], FN)
    assert calls == [config.CRUSOE_URL, config.OPENAI_URL]


def test_pace_holds_the_rolling_window(monkeypatch):
    monkeypatch.setattr(config, "RATE_PER_MIN", 3)
    clock = [1000.0]
    waits = []

    def sleep(s):
        waits.append(round(s, 2))
        clock[0] += s

    for _ in range(3):
        assert llm._pace(config.CRUSOE_URL, now=lambda: clock[0], sleep=sleep) == 0
        clock[0] += 1
    # 4th request in the same minute waits until the first stamp (t=1000) leaves the window
    w = llm._pace(config.CRUSOE_URL, now=lambda: clock[0], sleep=sleep)
    assert waits == [57.05] and w == 57.05
    assert llm._pace(config.OPENAI_URL) == 0  # OpenAI is never paced here


def test_max_tokens_only_off_openai(monkeypatch):
    sent = {}

    class C:
        def with_options(self, **_):
            return self

        chat = property(lambda self: self)
        completions = property(lambda self: self)

        def create(self, **kw):
            sent.clear()
            sent.update(kw)
            return _resp({"a": 1})

    monkeypatch.setattr(llm, "_client_for", lambda b, k: C())
    monkeypatch.setattr(llm, "guard", lambda b: None)
    llm._create(config.CRUSOE_URL, "k", config.MODEL, {"messages": []}, None, 5)
    assert sent["max_tokens"] == config.MAX_TOKENS
    llm._create(config.OPENAI_URL, "k", "gpt-5-mini", {"messages": []}, "low", 5)
    assert "max_tokens" not in sent


def test_hedge_fires_fallback_when_primary_stalls(monkeypatch):
    import threading

    gate = threading.Event()
    calls = []
    monkeypatch.setattr(config, "HEDGE_S", 0.05)

    def fake(base, key, model, kw, eff, to):
        calls.append(base)
        if base == config.CRUSOE_URL:
            gate.wait(2)  # a stalled primary
            return _resp({"from": "primary"})
        return _resp({"from": "fallback"})

    monkeypatch.setattr(llm, "_create", fake)
    s = Stream("t", echo=False)
    ans, _ = llm.call_json(s, "picker", config.MODEL, [], FN, timeout=40)
    gate.set()
    assert ans == {"from": "fallback"}
    assert calls == [config.CRUSOE_URL, config.OPENAI_URL]
    assert any(e["type"] == "llm_hedge" for e in s.events)
    ev = [e for e in s.events if e["type"] == "llm"][0]
    assert ev["fallback"] is True and ev["model"] == config.FALLBACK_MODEL
    assert llm._fallback_since is None  # one slow call does not park the process on OpenAI
    assert len((llm.METER).read_text().splitlines()) == 1  # metered once, by the hedge


def test_hedge_not_fired_when_primary_is_quick(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "HEDGE_S", 5)
    monkeypatch.setattr(
        llm, "_create", lambda base, key, model, kw, eff, to: calls.append(base) or _resp({"a": 1})
    )
    s = Stream("t", echo=False)
    assert llm.call_json(s, "picker", config.MODEL, [], FN, timeout=40)[0] == {"a": 1}
    assert calls == [config.CRUSOE_URL]
    assert not any(e["type"] == "llm_hedge" for e in s.events)


def test_hedge_primary_wins_after_fallback_fired(monkeypatch):
    import threading

    gate = threading.Event()
    monkeypatch.setattr(config, "HEDGE_S", 0.05)

    def fake(base, key, model, kw, eff, to):
        if base == config.CRUSOE_URL:
            threading.Event().wait(0.2)  # answers late, but before the fallback
            return _resp({"from": "primary"})
        gate.wait(2)
        return _resp({"from": "fallback"})

    monkeypatch.setattr(llm, "_create", fake)
    s = Stream("t", echo=False)
    ans, _ = llm.call_json(s, "picker", config.MODEL, [], FN, timeout=40)
    gate.set()
    assert ans == {"from": "primary"}
    assert [e for e in s.events if e["type"] == "llm"][0]["fallback"] is False

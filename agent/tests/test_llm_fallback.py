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
        (config.OPENAI_URL, config.FALLBACK_MODEL),
    ]
    assert any(e["type"] == "llm_fallback" for e in s.events)
    # next call goes straight to the fallback (sticky), sub model maps to the fallback sub model
    llm.call_json(
        s, "part-scout", config.SUB_MODEL, [{"role": "user", "content": "x"}], FN
    )
    assert calls[-1] == (config.OPENAI_URL, config.FALLBACK_SUB_MODEL)
    assert len(calls) == 3


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

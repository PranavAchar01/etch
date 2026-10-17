"""Prod "9 pad macro pad" (10-17): the picker on Nemotron Super timed out on both keys and the user saw a bare
timeout. Now: an output cap, one quick retry on the sub-model, then the missing-parts message."""

import pytest

from agent import config, live, llm


class S:
    def __init__(self):
        self.ev = []

    def emit(self, agent, kind, **k):
        self.ev.append((agent, kind, k))


def test_picker_retries_on_the_quick_model(monkeypatch):
    calls = []

    def fake(stream, agent, model, msgs, fn, effort=None, timeout=None, max_tokens=None):
        calls.append((model, max_tokens, timeout))
        if len(calls) == 1:
            raise llm.ModelError("Request timed out.")
        return {"parts": []}, None

    monkeypatch.setattr(llm, "call_json", fake)
    s = S()
    assert live._pick_call(s, config.MODEL, []) == {"parts": []}
    assert calls == [
        (config.MODEL, live.PICKER_MAX_TOKENS, live.PICKER_TIMEOUT),
        (config.SUB_MODEL, live.RETRY_MAX_TOKENS, live.RETRY_TIMEOUT),
    ]
    assert ("picker", "retry") in [(a, k) for a, k, _ in s.ev]


def test_two_timeouts_end_with_the_missing_parts_message(monkeypatch):
    def fake(*a, **k):
        raise llm.ModelError("Request timed out.")

    monkeypatch.setattr(llm, "call_json", fake)
    s = S()
    with pytest.raises(live.PickTimeout):
        live._pick_call(s, config.MODEL, [])
    says = [k.get("text") for a, kind, k in s.ev if kind == "say"]
    assert says == [live.SLOW_SAY]  # a timeout is not a missing part
    assert any(kind == "missing_parts" for _, kind, _ in s.ev)


def test_cap_sets_max_tokens_off_openai(monkeypatch):
    seen = {}

    class C:
        def with_options(self, **k):
            return self

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    seen.update(kw)
                    return "ok"

    monkeypatch.setattr(llm, "_client_for", lambda b, k: C())
    monkeypatch.setattr(llm, "_pace", lambda *a, **k: 0.0)
    monkeypatch.setattr(llm, "guard", lambda b: None)
    llm._create(config.CRUSOE_URL, "k", "m", {"messages": [], "_cap": 1234}, None, 5)
    assert seen["max_tokens"] == 1234 and "_cap" not in seen


def test_an_edit_that_times_out_ends_cleanly(monkeypatch, tmp_path):
    # the edit path raised PickTimeout uncaught after the missing-parts events (sweep 10-17)
    import json

    from agent import prefilter, recipes

    base = tmp_path / "base"
    base.mkdir()
    (base / "design.json").write_text(json.dumps({"parts": []}))
    monkeypatch.setattr(prefilter, "screen", lambda *a, **k: None)
    monkeypatch.setattr(live.eng, "url", str)
    monkeypatch.setattr(recipes, "edit", lambda *a, **k: None)
    monkeypatch.setattr(live, "classify", lambda *a, **k: {"kept": [], "yes": 0, "no": 0, "rejected": []})

    def boom(*a, **k):
        raise live.PickTimeout(live.MISSING_SAY)

    monkeypatch.setattr(live, "pick", boom)
    s = S()
    res = live.modify("make it fly", base, tmp_path / "out", s, do_render=False)
    assert res["ok"] is False and res["error"].startswith("missing parts")
    done = [k for a, kind, k in s.ev if (a, kind) == ("lead", "done")]
    assert done and done[0]["ok"] is False

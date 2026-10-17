"""Regression tests for crash-fidget (prod 10-17): zM-s8mWOeq3rjtE3 sat 15 min on "Picking parts", and
"done picking parts?" typed meanwhile (ygA7cMfan1ojs9QZ) became a new board."""

from __future__ import annotations

import json

import pytest

from agent import config, llm, prefilter
from agent.events import Stream
from agent.watchdog import Watchdog, stage_cap


@pytest.fixture(autouse=True)
def _no_prefilter_log(monkeypatch):
    monkeypatch.setattr(prefilter, "LOG", "")


@pytest.mark.parametrize(
    "text",
    ["done picking parts?", "are you done?", "is it done yet", "how long will this take?", "status?",
     "any update?", "hello", "thanks!", "still working?", "why is it taking so long", "is the board ready?",
     "what's taking so long", "Done?"],
)
def test_chat_is_not_a_board(text):
    v = prefilter.screen(text)
    assert v is not None and v.stop and v.kind == "chat"
    assert "did not start one" in v.say


@pytest.mark.parametrize(
    "text",
    ["create a fidget toy", "a fidget toy?", "can you make a timer that beeps when done?",
     "ready light for a coffee machine", "hi, a weather station", "test jig for a 555",
     "status led board with a button", "thanks, now add a buzzer", "a done indicator for the washer", "macropad"],
)
def test_board_asks_still_pass(text):
    v = prefilter.screen(text)
    assert v is None or v.kind != "chat"


def test_chat_takes_the_fast_refusal_lane():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy"))
    import etch_public

    assert etch_public.lane_for("design", "done picking parts?", None) == "fast"


def test_live_design_refuses_chat_without_a_model(tmp_path, monkeypatch):
    from agent import live

    monkeypatch.setattr(llm, "chat", lambda *a, **k: pytest.fail("no model call for chat"))
    st = Stream("t", echo=False)
    r = live.design("done picking parts?", tmp_path, st, do_render=False)
    assert r["ok"] is False and r["error"] == "prefilter: chat"
    assert not any(e["agent"] == "picker" for e in st.events)


def test_pace_gives_up_instead_of_sleeping_forever(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "RATE_FILE", tmp_path / ".primary-rate.json")
    monkeypatch.setattr(config, "RATE_PER_MIN", 1)
    clock = [1000.0]
    slept = []
    llm._pace(config.CRUSOE_URL, now=lambda: clock[0])
    stamps = tmp_path / ".primary-rate.json"

    def refill():
        stamps.write_text(json.dumps([clock[0]]))

    def sleep2(s):  # another process takes every slot that frees up
        slept.append(s)
        clock[0] += s
        refill()

    with pytest.raises(llm.ModelError, match="no free model slot"):
        llm._pace(config.CRUSOE_URL, now=lambda: clock[0], sleep=sleep2)
    assert sum(slept) <= llm.PACE_MAX_S + 1


class _Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _dog(st, tmp_path, clock, cap=780.0):
    exits = []
    d = Watchdog(st, tmp_path, "create a fidget toy", run_cap_s=cap, now=clock, exit_fn=exits.append)
    return d, exits


def test_watchdog_stops_a_silent_picker_with_words(tmp_path):
    import time

    clock = _Clock(time.time())
    st = Stream("t", echo=False)
    d, exits = _dog(st, tmp_path, clock)
    ev = st.emit("picker", "llm_switch", model="a", to="b")
    clock.t = ev["ts"] + stage_cap("picker") - 1
    assert d.check() is None
    clock.t = ev["ts"] + stage_cap("picker") + 1
    assert d.check() == "stage:picker"
    assert exits == [0]
    done = [e for e in st.events if e["agent"] == "lead" and e["type"] == "done"]
    assert done and done[0]["ok"] is False and done[0]["error"].startswith("timeout: picker")
    say = [e["text"] for e in st.events if e["type"] == "say"][-1]
    assert "picking the parts" in say
    assert json.loads((tmp_path / "live_result.json").read_text())["error"].startswith("timeout")
    assert d.check() is None  # trips once


def test_watchdog_gives_routing_its_time_but_caps_the_run(tmp_path):
    import time

    clock = _Clock(time.time())
    st = Stream("t", echo=False)
    d, exits = _dog(st, tmp_path, clock, cap=600)
    ev = st.emit("assembly", "gate", gate="route")
    clock.t = ev["ts"] + 420  # a 7-minute route is real (assembly:gate max 420 s in 164 prod runs)
    assert d.check() is None
    clock.t = d.t0 + 601
    assert d.check() == "run" and exits == [0]


def test_watchdog_quiet_after_done(tmp_path):
    clock = _Clock(1000.0)
    st = Stream("t", echo=False)
    d, exits = _dog(st, tmp_path, clock)
    st.emit("lead", "done", ok=True)
    clock.t += 10_000
    assert d.check() is None and exits == []

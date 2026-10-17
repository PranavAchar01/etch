"""agent/prompt_rewriter.py with the OpenAI call mocked: parsing, device match, cache, fail-open, off switch."""

from __future__ import annotations

import json
import time

import pytest

from agent import prompt_rewriter as pr
from agent import task_router

GOOD = {
    "kind": "board",
    "text": "Design a circuit board for a GPS tracker similar to an AirTag, with a GPS module.",
}


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    cat = tmp_path / "devices.json"
    cat.write_text(
        json.dumps(
            {
                "devices": [
                    {
                        "id": "gps_tracker",
                        "variant": None,
                        "aliases": ["gps tracker", "gps tag"],
                    },
                    {"id": "airtag", "variant": None, "aliases": ["air tag", "airtag"]},
                    {"id": "keyboard", "variant": None, "aliases": ["keyboard"]},
                    {
                        "id": "game_controller",
                        "variant": None,
                        "aliases": ["controller", "game controller"],
                    },
                    {
                        "id": "gps_tracker_screen",
                        "variant": "screen",
                        "aliases": ["gps tracker with a screen"],
                    },
                ]
            }
        )
    )
    monkeypatch.setattr(pr, "DEVICES", cat)
    monkeypatch.setattr(pr, "_devices", {})
    monkeypatch.setattr(pr, "_cache", pr.OrderedDict())
    monkeypatch.setattr(pr.config, "key_for", lambda src: "sk-test")
    monkeypatch.delenv("ETCH_REWRITE", raising=False)


def fake_call(answer, delay=0.0, calls=None):
    def call(text, key):
        if calls is not None:
            calls.append(text)
        time.sleep(delay)
        return pr.parse(json.dumps(answer), text)

    return call


def test_parse_rejects_bad_answers():
    for bad in [
        "[]",
        '{"kind": "maybe", "text": "x"}',
        '{"kind": "board", "text": ""}',
        "not json",
        json.dumps({"kind": "board", "text": "x" * 500}),
    ]:
        with pytest.raises(ValueError):
            pr.parse(bad)


def test_device_from_subject_not_a_later_part():
    t = "Design a circuit board for a keyboard, with key switches, a controller and a USB port."
    assert (
        pr.device_for(t) == "keyboard"
    )  # "controller" is longer but is a part, not the device
    assert (
        pr.device_for("Design a circuit board for a GPS tracker similar to an AirTag.")
        == "gps tracker"
    )
    assert (
        pr.device_for("Design a circuit board that beeps.", raw="make an airtag")
        == "airtag"
    )
    assert pr.device_for("Design a circuit board for a nuclear reactor.") is None
    assert (
        "gps tracker with a screen" not in pr.aliases()
    )  # variants are left to the catalog


def test_questions_and_chat_carry_no_device():
    r = pr.parse(
        json.dumps(
            {
                "kind": "question",
                "text": "Question about the current build: keyboard done?",
            }
        )
    )
    assert r == {
        "kind": "question",
        "text": "Question about the current build: keyboard done?",
        "device": None,
    }


def test_rewrite_ok_then_cached(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(pr, "_call", fake_call(GOOD, calls=calls))
    a = pr.rewrite("cretae a gps tracker like an airtag")
    assert (
        a["ok"]
        and not a["cached"]
        and a["kind"] == "board"
        and a["device"] == "gps tracker"
    )
    assert (
        a["text"] == GOOD["text"] and a["raw"] == "cretae a gps tracker like an airtag"
    )
    b = pr.rewrite("cretae a gps tracker like an airtag")
    assert b["ok"] and b["cached"] and b["text"] == GOOD["text"]
    assert len(calls) == 1  # exact-text cache
    pr.rewrite("cretae a gps tracker like an airtag ")  # any other text is a new call
    assert len(calls) == 2


def test_timeout_fails_open_and_late_answer_is_cached(monkeypatch):
    monkeypatch.setattr(pr, "TIMEOUT_S", 0.1)
    monkeypatch.setattr(pr, "_call", fake_call(GOOD, delay=0.4))
    t0 = time.time()
    r = pr.rewrite("mp4 player")
    assert time.time() - t0 < 0.35
    assert (
        not r["ok"]
        and r["error"] == "timeout"
        and r["text"] == "mp4 player"
        and r["kind"] is None
    )
    time.sleep(0.5)
    assert pr.rewrite("mp4 player")["cached"]


def test_errors_fail_open(monkeypatch):
    def boom(text, key):
        raise OSError("network down")

    monkeypatch.setattr(pr, "_call", boom)
    r = pr.rewrite("keyboard")
    assert not r["ok"] and r["text"] == "keyboard" and r["error"] == "OSError"
    monkeypatch.setattr(pr.config, "key_for", lambda src: "")
    assert pr.rewrite("keyboard")["error"] == "no key"


def test_off_switch_makes_no_call(monkeypatch):
    monkeypatch.setenv("ETCH_REWRITE", "0")
    monkeypatch.setattr(
        pr, "_call", lambda *a: pytest.fail("called with ETCH_REWRITE=0")
    )
    r = pr.rewrite("keyboard")
    assert not pr.enabled() and not r["ok"] and r["text"] == "keyboard"


def test_event_never_carries_the_key(monkeypatch):
    monkeypatch.setattr(pr, "_call", fake_call(GOOD))
    ev = pr.event(pr.rewrite("gps tracker"))
    assert (
        ev["agent"] == "lead" and ev["type"] == "rewrite" and ev["text"] == GOOD["text"]
    )
    assert "sk-test" not in json.dumps(ev)


def test_router_classifies_the_rewrite_in_process(monkeypatch):
    seen: list[str] = []
    monkeypatch.setenv("ETCH_CLASSIFIER", "1")
    monkeypatch.delenv("ETCH_CLASSIFIED", raising=False)
    monkeypatch.setattr(pr, "_call", fake_call(GOOD))

    def classify(text):
        seen.append(text)
        return {
            "task_type_1": "Text Generation",
            "prompt_complexity_score": 0.4,
            "seconds": 0.1,
            "load_seconds": 1,
        }

    monkeypatch.setattr(task_router, "classify", classify)
    rt = task_router.route("cretae a gps tracker like an airtag", False, "lead", "sub")
    assert seen == [GOOD["text"]]
    assert rt["route"] == "complex" and rt["rewrite"]["device"] == "gps tracker"


def test_router_with_precomputed_scores_does_not_rewrite(monkeypatch):
    monkeypatch.setenv("ETCH_CLASSIFIER", "1")
    monkeypatch.setenv(
        "ETCH_CLASSIFIED",
        json.dumps(
            {
                "task_type_1": "Chatbot",
                "prompt_complexity_score": 0.1,
                "seconds": 0.1,
                "load_seconds": 1,
            }
        ),
    )
    monkeypatch.setattr(
        pr, "_call", lambda *a: pytest.fail("the backend already rewrote this ask")
    )
    rt = task_router.route("keyboard", False, "lead", "sub")
    assert rt["route"] == "simple" and "rewrite" not in rt

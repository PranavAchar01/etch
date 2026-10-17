"""deploy/etch_public.py worker(): the prompt rewrite feeds the classifier, is logged as a run event and fails open.
The OpenAI call is mocked."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import etch_public  # noqa: E402

PY = sys.executable
DONE = "import json, os; print(json.dumps({'agent': 'lead', 'type': 'done', 'ok': True, 'rw': os.environ.get('ETCH_REWRITE_RESULT')}))"
CLEAN = "Design a circuit board for a GPS tracker similar to an AirTag, with a GPS module and a battery."


@pytest.fixture()
def rw(monkeypatch):
    mod = etch_public.rewriter()
    monkeypatch.setattr(mod, "_cache", mod.OrderedDict())
    monkeypatch.setattr(mod.config, "key_for", lambda src: "sk-test")
    monkeypatch.delenv("ETCH_REWRITE", raising=False)
    monkeypatch.setenv("ETCH_CLASSIFIER", "1")
    return mod


def run(text: str, seen: list[str]) -> etch_public.Job:
    def classify(t):
        seen.append(t)
        return json.dumps({"prompt_complexity_score": 0.4})

    job = etch_public.Job([PY, "-c", DONE], "ip", "design", text)
    etch_public.RUNNING["full"] += 1  # worker() releases the place it was given
    with (
        mock.patch.object(etch_public.CLASSIFIER, "classify", classify),
        mock.patch.object(etch_public, "save_terminal"),
    ):
        etch_public.worker(job)
    return job


def test_rewrite_feeds_the_classifier_and_is_logged(rw, monkeypatch):
    monkeypatch.setattr(
        rw,
        "_call",
        lambda text, key: rw.parse(json.dumps({"kind": "board", "text": CLEAN}), text),
    )
    seen: list[str] = []
    job = run("cretae a gps tracker like an airtag", seen)
    assert seen == [CLEAN]
    ev = [e for e in job.events if e.get("type") == "rewrite"]
    assert (
        len(ev) == 1
        and ev[0]["ok"]
        and ev[0]["text"] == CLEAN
        and ev[0]["kind"] == "board"
    )
    done = [e for e in job.events if e.get("type") == "done"][0]
    assert (
        json.loads(done["rw"])["text"] == CLEAN
    )  # handed to the build in ETCH_REWRITE_RESULT
    assert (
        job.text == "cretae a gps tracker like an airtag"
    )  # the design keeps the user's own words
    assert job.state == "done"
    timing = [e for e in job.events if e.get("type") == "timing"][0]
    assert (
        timing["spawn_to_first_event_s"] is not None
    )  # set by the engine's event, not the rewrite


def test_rewrite_failure_classifies_the_raw_text(rw, monkeypatch):
    def boom(text, key):
        raise TimeoutError

    monkeypatch.setattr(rw, "_call", boom)
    seen: list[str] = []
    job = run("mp4 player", seen)
    assert seen == ["mp4 player"]
    ev = [e for e in job.events if e.get("type") == "rewrite"][0]
    assert not ev["ok"] and ev["text"] == "mp4 player"
    assert job.state == "done"


def test_off_switch_and_classifier_off_skip_the_call(rw, monkeypatch):
    monkeypatch.setattr(rw, "_call", lambda *a: pytest.fail("no rewrite expected"))
    monkeypatch.setenv("ETCH_REWRITE", "0")
    seen: list[str] = []
    job = run("keyboard", seen)
    assert seen == ["keyboard"] and not [
        e for e in job.events if e.get("type") == "rewrite"
    ]
    monkeypatch.delenv("ETCH_REWRITE")
    monkeypatch.setenv("ETCH_CLASSIFIER", "0")
    seen.clear()
    job = run("keyboard", seen)
    assert seen == [] and not [e for e in job.events if e.get("type") == "rewrite"]

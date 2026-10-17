"""QA round 6: voice-arm edits that now run from code (no model call)."""

import json
from pathlib import Path

import pytest

from agent import recipes

ROOT = Path(__file__).resolve().parents[2]
ARM = ROOT / "engine/out/live-1792214061143/design.json"


@pytest.fixture
def arm():
    if not ARM.is_file():
        return recipes.voice_arm("a voice controlled robot arm")
    return json.loads(ARM.read_text())


@pytest.mark.parametrize("ask", ["add a buzzer", "please add a beeper", "add a buzzer that beeps when it hears a command"])
def test_sound_buzzer(arm, ask):
    d, name = recipes.edit(ask, arm)
    assert name == "sound_buzzer"
    new = {p["ref"]: p for p in d["parts"] if p["ref"] not in {q["ref"] for q in arm["parts"]}}
    assert {p["piece"] for p in new.values()} == {recipes.R, recipes.NPN, recipes.BUZZ}
    r = next(p for p in new.values() if p["piece"] == recipes.R)
    assert r["value"] == "4.7k" and arm["firmware"]["loud_high"][0] in r["pins"].values()
    assert d["firmware"] == arm["firmware"]  # no firmware change
    assert any(b["check"].startswith("speaking.BZ") for b in d["benchmarks"])


def test_sound_buzzer_once(arm):
    d, _ = recipes.edit("add a buzzer", arm)
    assert recipes.sound_buzzer("add a buzzer", d) is None


def test_sound_buzzer_not_on_plain_boards():
    base = recipes.dark_light("an LED that glows purple when it gets dark")
    hit = recipes.edit("add a buzzer", base)
    assert hit is None or hit[1] != "sound_buzzer"


def test_status_led_recolour_blue_lowers_resistor(arm):
    d, name = recipes.edit("make the status LED blue", arm)
    assert name == "recolour"
    assert next(p for p in d["parts"] if p["ref"] == "D1")["value"] == "blue"
    assert next(p for p in d["parts"] if p["ref"] == "R3")["value"] == "47"


def test_recolour_red_keeps_resistor(arm):
    d, _ = recipes.edit("make the led red", arm)
    assert next(p for p in d["parts"] if p["ref"] == "R3")["value"] == "100"


@pytest.mark.parametrize("v,ohms", [("4.7k", 4700), ("100", 100), ("1M", 1e6), ("220 ohm", 220)])
def test_sim_ohms(v, ohms):
    assert recipes.sim_ohms(v) == ohms

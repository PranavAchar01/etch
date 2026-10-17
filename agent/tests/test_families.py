"""Pranav's two prompt families (10-17): the singing rainbow (mic, pitch firmware, RGB LED) and the computer-
switched fan (USB serial Pico, transistor switch with flyback, host script with hysteresis and failsafe).
His wording plus three rephrasings each must reach the recipe, and the tested firmware must behave."""

import tempfile
from pathlib import Path

import pytest

from agent import fan_host, firmware, kicad_catalog, live, recipes

RAINBOW = [
    "create a pcb that lights up different colors of the rainbow when the user sings in different pitches",
    "a board with a microphone and an rgb led that changes colour with the pitch of my voice, low notes red and "
    "high notes violet",
    "make me a singing light: when I sing low it glows red and as I sing higher it moves through the rainbow",
    "build a pitch detector pcb that shows the note I'm singing as a rainbow colour on an rgb led",
]
FAN = [
    "create a pcb that spins a fan when from your computer you find the temp has increased past 80 deg",
    "a usb board that turns on a 5v fan when my computer says the cpu is hotter than 80 degrees",
    "build a fan controller my pc talks to over usb: a python script checks the temperature and the board spins "
    "the fan above 80 C",
    "I want a pcb that runs a cooling fan whenever my laptop's temperature goes past 80 degrees celsius, "
    "controlled from the computer",
]


@pytest.fixture(scope="module")
def cat():
    return {p["key"]: p for p in kicad_catalog.load()["pieces"]}


@pytest.mark.parametrize("ask", RAINBOW)
def test_rainbow_phrasings_reach_the_recipe(ask, cat):
    assert recipes.match(ask, cat)[1] == "pitch_rainbow"


@pytest.mark.parametrize("ask", FAN)
def test_fan_phrasings_reach_the_recipe(ask, cat):
    d, name, _ = recipes.match(ask, cat)
    assert name == "usb_fan"
    assert d["firmware"]["host"]["set"] == {"ON_AT": 80.0, "OFF_AT": 75.0}


@pytest.mark.parametrize(
    "ask",
    [
        "a light that goes red when I am talking and green when it is quiet",
        "a temperature sensor that shows the reading on a small oled screen",
        "a motor driver for two dc motors controlled over bluetooth",
    ],
)
def test_neighbours_keep_their_recipes(ask, cat):
    assert recipes.match(ask, cat)[1] not in ("pitch_rainbow", "usb_fan")


def test_threshold_units():
    assert recipes._threshold_c("fan on above 176 f from my pc") == 80.0
    assert recipes._threshold_c("spin the fan past 65 degrees") == 65.0
    assert recipes._threshold_c("a fan my computer turns on when hot") == 80.0


def _build(ask, cat):
    d, _, used = recipes.match(ask, cat)
    out = Path(tempfile.mkdtemp())
    res = firmware.build(d, used, out)
    assert not isinstance(res, str), res
    return d, used, res, out


def test_pitch_firmware_lights_each_band(cat):
    d, used, (scen, rep), _ = _build(RAINBOW[0], cat)
    assert rep["pass"] and len(rep["tests"]) == 1 + len(firmware.RAINBOW)
    assert {s["name"] for s in scen} >= {"quiet", "pitch_red", "pitch_violet"}
    assert all(s.get("tone_hz") for s in scen if s["name"] != "quiet")
    rows = firmware.benchmarks({**d, "scenarios": scen}, used)
    assert any(r["check"] == "pitch_violet.D1_B.avg_ma" and r.get("min") for r in rows)


def test_pitch_firmware_separates_neighbouring_notes(cat):
    d, _, _, out = _build(RAINBOW[0], cat)
    main_py = (out / "firmware" / "main.py").read_text()
    with tempfile.TemporaryDirectory() as td:
        lo = firmware.run(
            main_py, "", Path(td), sound="tone:145"
        )  # just under the red/yellow edge
        hi = firmware.run(main_py, "", Path(td), sound="tone:166")
    assert {g for g, v in lo.items() if v and g != 26} == {15}
    assert {g for g, v in hi.items() if v and g != 26} == {15, 16}


def test_fan_firmware_failsafe_and_host_script(cat):
    d, used, (scen, rep), out = _build(FAN[0], cat)
    assert rep["pass"]
    names = {t["input"] for t in rep["tests"]}
    assert {"link goes quiet", "computer keeps talking"} <= names
    assert {"failsafe", "heartbeat"} <= {s["name"] for s in scen}
    host = (out / "firmware" / "fan_host.py").read_text()
    assert "ON_AT = 80.0" in host and "OFF_AT = 75.0" in host


def test_host_hysteresis_and_failsafe():
    assert fan_host.decide(81, False) is True
    assert fan_host.decide(78, True) is True  # inside the band: keeps spinning
    assert fan_host.decide(78, False) is False  # inside the band: stays off
    assert fan_host.decide(74.9, True) is False
    assert fan_host.decide(None, False) is True  # no reading: fan on


def test_flyback_diode_across_the_fan_is_not_a_wiring_mistake(cat):
    d, _, used = recipes.match(FAN[0], cat)
    assert live._wiring_lint(d, used) == []

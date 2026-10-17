"""Every stage reaches the whole parts library (prod "9 pad macro pad", 10-17: the table classifier kept only
R / C / CP / Conn_01x02 from 14 libraries and the picker timed out). Needs the local KiCad install the library is
built from; skipped where it is missing."""

from __future__ import annotations

import pytest

from agent import config, kicad_library, live, recipes

pytestmark = pytest.mark.skipif(
    not kicad_library.OUT.exists() and not (config.ROOT / "engine").exists(),
    reason="parts library not built here",
)

CHOC = (
    "kicad:Switch:SW_Push@SW_choc_v2_HS_CPG135001S30_1u"  # marbastlib (extra library)
)
ESP = "kicad:Espressif:ESP32-WROOM-E@ESP32-WROOM-32E"  # Espressif library


@pytest.fixture(scope="module")
def cat():
    return live._cat()


def test_every_stage_sees_the_full_library(cat):
    lib = kicad_library.load()
    assert len(cat) >= len(lib["by_key"]) > 20000
    # reachable for an ask that has nothing to do with them
    assert CHOC in cat and ESP in cat
    keys = {
        p["key"]
        for p in kicad_library.shortlist(
            "a weather station that posts to my phone over wifi"
        )
    }
    assert ESP in keys


@pytest.mark.parametrize(
    "ask,n",
    [
        ("9 pad macro pad", 9),
        ("9 key macro pad for my desk", 9),
        ("make a regular keyboard", 61),
        ("a 60% hotswap keyboard", 61),
        ("a macro pad", 9),
        ("a numpad", 17),
        ("4-key macro pad with RGB", 4),
        ("mechanical keyboard with RGB", 61),
    ],
)
def test_keyboard_asks_route_to_the_keyboard_recipe(cat, ask, n):
    got = recipes.match(ask, cat)
    assert got is not None, ask
    d, name, used = got
    assert name == "keyboard"
    sw = [p for p in d["parts"] if p["ref"].startswith("SW")]
    di = [p for p in d["parts"] if p["piece"].startswith("kicad:Diode:1N4148")]
    assert len(sw) == n and len(di) == n
    assert any(
        p["piece"].startswith("kicad:MCU_Module:RaspberryPi_Pico") for p in d["parts"]
    )
    # every key's diode joins its switch, cathode on a row
    for s, dd in zip(sw, di):
        assert s["pins"]["2"] == dd["pins"]["2"] and dd["pins"]["1"].startswith("ROW")
    if "rgb" in ask.lower():
        assert any(p["piece"].startswith("kicad:LED:WS2812B") for p in d["parts"])


def test_choc_and_knob():
    d = recipes.match("low profile choc macro pad with a volume knob", live._cat())[0]
    assert any(p["piece"] == CHOC for p in d["parts"])
    assert any(
        p["piece"].startswith("kicad:Device:RotaryEncoder_Switch") for p in d["parts"]
    )


def test_music_keyboard_is_not_a_computer_keyboard(cat):
    got = recipes.match("a synth keyboard", cat)
    assert got is None or got[1] != "keyboard"


@pytest.mark.parametrize(
    "text,want",
    [
        ("9 mechanical switches (through-hole)", "kicad:Switch:"),
        ("1N4148", "kicad:Diode:1N4148@"),
        ("1N4148 SOD-123", "kicad:Diode:1N4148W@D_SOD-123"),
        ("DIOD1N4148", "kicad:Diode:1N4148"),
        ("Conn_01x02", "kicad:Connector_Generic:Conn_01x02@"),
        ("Conn_01x40", "kicad:Connector_Generic:Conn_01x40@"),
        ("servo header", "kicad:Connector_Generic:Conn_01x03@PinHeader_1x03"),
    ],
)
def test_descriptive_picks_resolve_through_the_library(text, want):
    p = live._lib_piece(text)
    assert p is not None and p["key"].startswith(want), (text, p and p["key"])

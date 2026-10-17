"""Edit sweep 10-17: the base board "a night light with one led and a button" keeps its button, and the edits the
sweep failed on (remove the button, blue on two LEDs, coin cell current, two edits in one ask) stay deterministic."""

import pytest

from agent import kicad_catalog, recipes

CAT = {p["key"]: p for p in kicad_catalog.load()["pieces"]}


@pytest.fixture
def base():
    d, name, _ = recipes.match("a night light with one led and a button", CAT)
    assert name == "dark_light"
    return d


def _refs(d):
    return {p["ref"]: p for p in d["parts"]}


def test_the_button_is_on_the_base_board(base):
    sw = _refs(base)["SW1"]
    assert set(sw["pins"].values()) == {"VCC", "VCC_SW"}
    assert _refs(base)["R3"]["pins"]["1"] == "VCC_SW"
    assert any(s.get("switch") == "released" for s in base["scenarios"])


def test_no_button_asked_no_button_built():
    d, _, _ = recipes.match("a night light with one led", CAT)
    assert "SW1" not in _refs(d) and _refs(d)["R3"]["pins"]["1"] == "VCC"


def test_remove_the_button(base):
    d, name = recipes.edit("remove the button", base, CAT)
    assert name == "remove_switch" and d["_dropped"] == ["SW1"]
    assert "SW1" not in _refs(d) and _refs(d)["R3"]["pins"]["1"] == "VCC"
    assert not any(s.get("switch") for s in d["scenarios"])
    assert not any(b["check"].startswith("released.") for b in d["benchmarks"])


def test_blue_on_two_leds(base):
    two, _ = recipes.edit("add a second led", base, CAT)
    two.pop("_dropped", None)
    d, name = recipes.edit("use a blue led instead", two, CAT)
    assert name == "recolour"
    assert [p["value"] for p in d["parts"] if p["ref"].startswith("D")] == ["blue", "blue"]


def test_coin_cell_keeps_the_led_bright(base):
    d, name = recipes.edit("run it off a coin cell", base, CAT)
    assert name == "coin_cell" and _refs(d)["R3"]["value"] == "68"


def test_two_edits_in_one_ask(base):
    d, name = recipes.edit("add a second led and make it blue", base, CAT)
    assert name == "add_led+recolour"
    assert _refs(d)["D2"]["value"] == "blue" and _refs(d)["D1"].get("value") != "blue"
    d, name = recipes.edit("add a buzzer, and remove the button", base, CAT)
    assert name == "add_buzzer+remove_switch" and "BZ1" in _refs(d) and d["_dropped"] == ["SW1"]


def test_one_unknown_clause_sends_the_whole_ask_on(base):
    assert recipes.edit("add a buzzer and make the board round", base, CAT) is None

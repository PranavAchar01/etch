"""typed_rgb routing (sweep 10-17): a bare 'RGB LED on a Pico' and key lists without a verb per key."""

import pytest

from agent import kicad_catalog, recipes


@pytest.fixture(scope="module")
def cat():
    return {p["key"]: p for p in kicad_catalog.load()["pieces"]}


def test_rgb_pico_defaults_to_rgb_keys(cat):
    d, name, _ = recipes.match("an RGB LED controlled by a Raspberry Pi Pico", cat)
    assert name == "typed_rgb" and "r red, g green, b blue" in d["say"]


def test_key_list_without_repeated_verb(cat):
    d, name, _ = recipes.match("type r for red and g for green on an RGB LED", cat)
    assert name == "typed_rgb" and "g green" in d["say"]


def test_plain_button_led_unchanged(cat):
    assert recipes.match("a button that lights up an LED", cat)[1] == "button_led"

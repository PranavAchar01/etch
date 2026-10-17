"""Device library: blocks wired by code (agent/blocks.py) and the device catalog (engine/parts/devices.json)."""

import json

import pytest

from agent import blocks, live


@pytest.fixture(scope="module")
def cat():
    return live._cat()


ASKS = {
    "airtag": "airtag",
    "gps tracker": "gps_tracker",
    "mp4 player": "mp4_player",
    "electronic piano": "piano",
    "macropad": "macropad",
    "smart watch": "smart_watch",
    "bluetooth speaker": "bluetooth_speaker",
    "game controller": "game_controller",
    "weather station": "weather_station",
}


@pytest.mark.parametrize("ask,did", sorted(ASKS.items()))
def test_his_asks_build_from_blocks_and_pass_the_gate(cat, ask, did):
    rec = blocks.match(ask, cat)
    assert rec is not None and rec[1] == f"device:{did}"
    design = rec[0]
    assert blocks.gate(design, ask) is None


def test_catalog_has_thousands_of_devices_in_many_categories():
    db = blocks.load_devices()
    assert db["count"] == len(db["devices"]) >= 2000
    assert len(db["categories"]) >= 20
    assert len({d["id"] for d in db["devices"]}) == db["count"]
    names = {b for d in db["devices"] for b, _ in d["blocks"]}
    assert names <= set(blocks.BLOCKS)


def test_modifier_words_add_blocks():
    plain, _ = blocks.device_design("gps tracker")
    screen, _ = blocks.device_design("a gps tracker with a screen")
    assert not any("OLED" in p.get("value", "") for p in plain["parts"])
    assert any("OLED" in p.get("value", "") for p in screen["parts"])


def test_wireless_devices_use_pico_w_and_never_vsys():
    d, _ = blocks.device_design("airtag")
    mcu = d["parts"][0]
    assert mcu["piece"] == blocks.PICO_W
    assert "39" not in mcu["pins"]
    nets = {v for p in d["parts"] for v in p["pins"].values()}
    assert "VSYS" not in nets


def test_battery_reaches_vbus_through_a_schottky():
    d, _ = blocks.device_design("rechargeable macropad")
    diodes = [p for p in d["parts"] if p["piece"] == blocks.SCHOTTKY]
    assert diodes and all(p["pins"]["1"] == "VBUS" for p in diodes)
    assert d["parts"][0]["pins"][blocks.VBUS_PIN] == "VBUS"


def test_named_chip_or_topology_goes_to_the_picker(cat):
    assert blocks.match("a 555 timer that blinks an LED", cat) is None
    assert blocks.match("esp32 weather station", cat) is None
    assert blocks.match("a clock with no microcontroller", cat) is None


def test_too_many_analog_inputs_is_refused():
    with pytest.raises(blocks.BlockError):
        blocks.compose(["joystick", "joystick"], "x")


def test_unknown_block_is_refused():
    with pytest.raises(blocks.BlockError):
        blocks.compose(["flux_capacitor"], "x")


def test_pins_are_unique_and_buses_land_on_their_pins():
    d = blocks.compose(
        [
            ["i2c_sensor", {"chip": "bme280"}],
            "gps",
            "sd_card",
            "lora",
            ["key_matrix", {"n": 9}],
        ],
        "x",
    )
    pins = d["parts"][0]["pins"]
    gpio = [n for k, n in pins.items() if n not in ("GND", "P3V3", "VBUS")]
    assert len(gpio) == len(set(gpio))
    assert pins["6"] == "SDA" and pins["7"] == "SCL" and pins["24"] == "SCK"


def test_coverage_report_is_current():
    from pathlib import Path

    rows = json.loads(
        (Path(__file__).parents[2] / "qa/devices-1017/results.json").read_text()
    )
    assert len(rows) == blocks.load_devices()["count"]
    assert sum(r["pass"] for r in rows) / len(rows) >= 0.95

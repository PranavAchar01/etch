"""Airtag-style tracker asks (10-17, live-1792271634725 refused, live-1792271675178 looped on a lonely ANT net)."""

from agent import kicad_library, live

ANT = "kicad:JLCPCB-Extended:RF, GPS, Antenna@ANT-TH_L25.0-W25.0"


def test_tracker_words_are_a_board_ask():
    assert live._has_words("cretae an airtag")
    assert live._has_words("make a ble beacon")


def test_tracker_shortlist_has_a_radio_module():
    keys = {p["key"] for p in kicad_library.shortlist("create the board for a device tracker similar to an airtag")}
    assert live.PICO_W_KEY in keys


def test_radio_fix_swaps_pico_and_drops_lonely_antenna():
    design = {
        "parts": [
            {"ref": "U1", "piece": live.PICO_KEY, "pins": {"3": "GND"}},
            {"ref": "ANT1", "piece": ANT, "pins": {"1": "ANT"}},
            {"ref": "BT1", "piece": "kicad:Device:Battery_Cell@BatteryHolder_Keystone_1058_1x2032", "pins": {"1": "VBAT", "2": "GND"}},
        ]
    }
    kept: list = []
    did = live._radio_fix(design, kept, "device tracker similar to an airtag")
    refs = {p["ref"]: p["piece"] for p in design["parts"]}
    assert "ANT1" not in refs
    assert refs["U1"] == live.PICO_W_KEY
    assert len(did) == 2


def test_radio_fix_leaves_wired_antenna_and_plain_asks():
    design = {
        "parts": [
            {"ref": "U1", "piece": live.PICO_KEY, "pins": {"3": "GND", "4": "RF"}},
            {"ref": "ANT1", "piece": ANT, "pins": {"1": "RF"}},
        ]
    }
    assert live._radio_fix(design, [], "a led blinker") == []
    assert len(design["parts"]) == 2

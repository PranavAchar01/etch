"""Picker repair (10-17): dangling single-pin nets the picker split by name are joined, MCU-named leftovers go on
the module, and a wrong-family ref is renamed instead of failing the iteration. One test per class."""

import copy

from agent import kicad_catalog, live

R = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
Q = "kicad:Transistor_BJT:PN2222A@TO-92_Inline"
CONN = "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical"


def _kept(*keys):
    cat = {p["key"]: p for p in kicad_catalog.load()["pieces"]}
    return [cat[k] for k in keys]


def _nets(d):
    return {p["ref"]: dict(p["pins"]) for p in d["parts"]}


def test_case_and_separator_spelling_joins():
    d = {
        "parts": [
            {"ref": "J1", "piece": CONN, "pins": {"1": "LED_A", "2": "GND"}},
            {"ref": "R1", "piece": R, "pins": {"1": "led-a", "2": "X"}},
            {"ref": "D1", "piece": live.LED_KEY, "pins": {"2": "X", "1": "GND"}},
        ]
    }
    live._merge_lonely(d, _kept(CONN, R, live.LED_KEY))
    assert _nets(d)["R1"]["1"] == _nets(d)["J1"]["1"]
    assert not live._lonely_nets(d)


def test_extended_name_joins_the_short_one():
    d = {
        "parts": [
            {"ref": "Q2", "piece": Q, "pins": {"1": "GND", "2": "Q2_B", "3": "LOAD"}},
            {"ref": "R1", "piece": R, "pins": {"1": "Q2_B_DRIVE", "2": "SIG"}},
            {"ref": "R2", "piece": R, "pins": {"1": "Q2_B_PULLDOWN", "2": "GND"}},
            {"ref": "J1", "piece": CONN, "pins": {"1": "SIG", "2": "LOAD"}},
        ]
    }
    notes = live._merge_lonely(d, _kept(Q, R, CONN))
    n = _nets(d)
    assert n["R1"]["1"] == n["R2"]["1"] == "Q2_B"
    assert notes and not live._lonely_nets(d)


def test_colour_pair_keeps_the_mcu_name():
    d = {
        "parts": [
            {
                "ref": "U1",
                "piece": live.PICO_KEY,
                "pins": {"3": "GND", "20": "GPIO_RED"},
            },
            {"ref": "R1", "piece": R, "pins": {"1": "R1_R", "2": "A"}},
            {"ref": "D1", "piece": live.LED_KEY, "pins": {"2": "A", "1": "GND"}},
        ]
    }
    live._merge_lonely(d, _kept(live.PICO_KEY, R, live.LED_KEY))
    assert _nets(d)["R1"]["1"] == "GPIO_RED"


def test_supply_alias_joins_the_rail_in_use():
    d = {
        "parts": [
            {"ref": "J1", "piece": CONN, "pins": {"1": "VCC", "2": "GND"}},
            {"ref": "R1", "piece": R, "pins": {"1": "VCC", "2": "A"}},
            {"ref": "R2", "piece": R, "pins": {"1": "+5V", "2": "A"}},
            {"ref": "D1", "piece": live.LED_KEY, "pins": {"2": "A", "1": "VSS"}},
        ]
    }
    live._merge_lonely(d, _kept(CONN, R, live.LED_KEY))
    n = _nets(d)
    assert n["R2"]["1"] == "VCC" and n["D1"]["1"] == "GND"


def test_never_shorts_two_pins_of_one_part():
    d = {
        "parts": [
            {"ref": "R1", "piece": R, "pins": {"1": "NODE", "2": "node"}},
            {"ref": "R2", "piece": R, "pins": {"1": "NODE", "2": "GND"}},
        ]
    }
    live._merge_lonely(d, _kept(R))
    assert _nets(d)["R1"] == {"1": "NODE", "2": "node"}


def test_mcu_named_leftovers_go_on_the_module():
    d = {
        "parts": [
            {"ref": "U1", "piece": live.PICO_KEY, "pins": {"3": "GND"}},
            {"ref": "M1", "piece": CONN, "pins": {"1": "MIC_OUT", "2": "GND"}},
            {"ref": "J2", "piece": CONN, "pins": {"1": "I2C_SDA", "2": "I2C_SCL"}},
            {"ref": "J3", "piece": CONN, "pins": {"1": "FAN_PWM", "2": "GND"}},
            {"ref": "J4", "piece": CONN, "pins": {"1": "RELAY_K", "2": "GND"}},
        ]
    }
    live._wire_hinted(d, _kept(live.PICO_KEY, CONN))
    u = _nets(d)["U1"]
    assert u.get("31") == "MIC_OUT"  # GPIO26 / ADC0
    assert u.get("6") == "I2C_SDA" and u.get("7") == "I2C_SCL"
    assert "FAN_PWM" in u.values()
    assert "RELAY_K" not in u.values()  # a coil end belongs on a transistor, not a GPIO


def test_remaining_dangling_net_names_a_likely_match():
    d = {
        "parts": [
            {"ref": "R1", "piece": R, "pins": {"1": "RELAY_COIL", "2": "GND"}},
            {"ref": "Q1", "piece": Q, "pins": {"1": "GND", "3": "RELAY_COI", "2": "B"}},
            {"ref": "R2", "piece": R, "pins": {"1": "RELAY_COIL", "2": "B"}},
        ]
    }
    assert "RELAY_COIL" in live._lonely_hint(d, "RELAY_COI")


def test_wrong_family_ref_is_renamed_not_failed():
    d = {
        "parts": [
            {"ref": "U1", "piece": live.LED_KEY, "pins": {"1": "GND", "2": "A"}},
            {"ref": "C1", "piece": R, "pins": {"1": "A", "2": "VCC"}},
            {"ref": "D1", "piece": live.LED_KEY, "pins": {"1": "GND", "2": "A"}},
        ],
        "benchmarks": [{"metric": "U1 on current", "ref": "U1"}],
    }
    live._fix_refs(d)
    refs = [p["ref"] for p in d["parts"]]
    assert refs[0].startswith("D") and refs[0] != "D1" and refs[1].startswith("R")
    assert live._ref_mismatch(d) == []
    assert d["benchmarks"][0]["ref"] == refs[0]


def test_no_change_on_a_clean_board():
    d = {
        "parts": [
            {"ref": "J1", "piece": CONN, "pins": {"1": "VCC", "2": "GND"}},
            {"ref": "R1", "piece": R, "pins": {"1": "VCC", "2": "A"}},
            {"ref": "D1", "piece": live.LED_KEY, "pins": {"2": "A", "1": "GND"}},
        ]
    }
    before = copy.deepcopy(d)
    assert live._merge_lonely(d, _kept(CONN, R, live.LED_KEY)) == []
    assert d == before

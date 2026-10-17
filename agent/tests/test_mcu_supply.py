"""A lonely 5 V / 3.3 V net on a board with a Pico gets the Pico's rail (sweep 10-17, relay on a Pico)."""

import copy

from agent import kicad_catalog, live

CONN = "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical"
Q = "kicad:Transistor_BJT:PN2222A@TO-92_Inline"

RELAY = {
    "parts": [
        {"ref": "U1", "piece": live.PICO_KEY, "pins": {"3": "GND", "1": "RELAY_IN"}},
        {"ref": "Q1", "piece": Q, "pins": {"1": "GND", "2": "RELAY_IN", "3": "COIL"}},
        {"ref": "J1", "piece": CONN, "pins": {"1": "VCC", "2": "COIL"}, "load_ohms": 70},
    ]
}


def _kept():
    cat = {p["key"]: p for p in kicad_catalog.load()["pieces"]}
    return [cat[live.PICO_KEY], cat[CONN], cat[Q]]


def test_lonely_vcc_goes_on_vbus():
    d = copy.deepcopy(RELAY)
    live._wire_mcu_supply(d, _kept())
    assert d["parts"][0]["pins"]["40"] == "VCC"


def test_power_input_connector_feeds_vsys():
    d = copy.deepcopy(RELAY)
    d["parts"][2] = {"ref": "J1", "piece": CONN, "pins": {"1": "VCC", "2": "GND"}}
    d["parts"][1]["pins"]["3"] = "GND"
    live._wire_mcu_supply(d, _kept())
    assert d["parts"][0]["pins"].get("39") == "VCC" and "40" not in d["parts"][0]["pins"]


def test_taken_rail_pin_left_alone():
    d = copy.deepcopy(RELAY)
    d["parts"][0]["pins"]["40"] = "VBUS5"
    live._wire_mcu_supply(d, _kept())
    assert d["parts"][0]["pins"]["40"] == "VBUS5"


def test_firmware_driven_net_off_the_mcu_gets_a_gpio():
    d = copy.deepcopy(RELAY)
    d["parts"][0]["pins"] = {"3": "GND"}
    d["parts"].append({"ref": "R1", "piece": "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical",
                       "pins": {"1": "RELAY_IN", "2": "GND"}})
    d["firmware"] = {"commands": [{"input": "1", "high": ["RELAY_IN"]}]}
    live._wire_named_gpio(d, _kept())
    assert "RELAY_IN" in d["parts"][0]["pins"].values()

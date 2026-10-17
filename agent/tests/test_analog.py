"""Deterministic analog solver (agent/analog.py): the dark-fan failure of run live-1792256898139 and GPIO drivers."""

from __future__ import annotations

import copy

import pytest

from agent import analog, kicad_catalog

R = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
LDR = "kicad:Sensor_Optical:A1050@R_LDR_D6.4mm_P3.4mm_Vertical"
CONN = "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical"
D = "kicad:Diode:1N4007@D_DO-41_SOD81_P10.16mm_Horizontal"

FAN = {
    "voltage": 5.0,
    "parts": [
        {"ref": "J1", "piece": CONN, "pins": {"1": "VCC", "2": "GND"}},
        {"ref": "R1", "piece": R, "value": "47k", "pins": {"1": "VCC", "2": "Q1_B"}},
        {"ref": "LDR1", "piece": LDR, "pins": {"1": "Q1_B", "2": "GND"}},
        {"ref": "R2", "piece": R, "value": "10k", "pins": {"1": "Q1_B", "2": "GND"}},
        {"ref": "Q1", "piece": analog.KEY_3904, "pins": {"1": "GND", "2": "Q1_B", "3": "Q1_C"}},
        {"ref": "D1", "piece": D, "pins": {"1": "VCC", "2": "Q1_C"}},
        {"ref": "J2", "piece": CONN, "pins": {"1": "VCC", "2": "Q1_C"}, "load_ohms": 2.0,
         "why": "connector to external fan"},
    ],
    "scenarios": [{"name": "light", "ldr": "light"}, {"name": "dark", "ldr": "dark"}],
    "benchmarks": [
        {"check": "dark.J2.avg_ma", "min": 5, "max": 200},
        {"check": "light.J2.avg_ma", "min": 0, "max": 0.5},
        {"check": "dark.J2.peak_ma", "min": 5, "max": 300},
        {"check": "dark.supply_ma_avg", "min": 5, "max": 300},
        {"check": "light.supply_ma_avg", "min": 0, "max": 1},
    ],
}


@pytest.fixture(scope="module")
def cat():
    return {p["key"]: p for p in kicad_catalog.load()["pieces"]}


def test_parse_and_format():
    assert analog.parse_value("4.7k") == 4700
    assert analog.parse_value("1Meg") == 1e6
    assert analog.parse_value("2k2") == 2200
    assert analog.parse_value("220") == 220
    assert analog.parse_value("red") is None
    assert analog.fmt(47000) == "47k" and analog.fmt(1e6) == "1Meg" and analog.fmt(820) == "820"


def test_fan_failure_is_diagnosed(cat):
    d = copy.deepcopy(FAN)
    sw = analog.find_switches(d, cat)
    assert len(sw) == 1 and sw[0].load_ref == "J2"
    rows = {r["scenario"]: r for r in analog.evaluate(sw[0], d, 5.0, set())}
    # R2 clamps the base: the Thevenin source is 0.88 V through 8.2k, far below what 2.4 A needs
    assert rows["dark"]["vth"] < 1.0 and not rows["dark"]["ok"]
    assert rows["light"]["ok"]


def test_fan_is_solved(cat):
    d = copy.deepcopy(FAN)
    res = analog.solve(d, [], cat, ask="create a board that spins a fan when its dark")
    refs = {p["ref"]: p for p in d["parts"]}
    assert refs["J2"]["load_ohms"] == 33  # a small 5 V fan, 150 mA
    assert refs["R1"]["value"] != "47k"  # the divider was resized for the new driver
    assert any("Darlington" in n for n in res.notes) or refs["Q1"]["piece"] == analog.KEY_2222
    assert not res.findings
    sw = analog.find_switches(d, cat)
    # after the fix every switch in the design passes its own check
    for s in sw:
        darl = any(p["piece"] == analog.KEY_2222 and p["pins"].get("2") == "Q1_E" for p in d["parts"])
        if s.q["ref"] == "Q1":
            assert all(r["ok"] for r in analog.evaluate(s, d, 5.0, set(), darl, "2N3904"))
    notes = analog.bands(d, [], cat)
    b = {x["check"]: x for x in d["benchmarks"]}
    assert b["dark.J2.avg_ma"]["min"] <= 125 <= b["dark.J2.avg_ma"]["max"]
    assert isinstance(notes, list)


def test_working_design_untouched(cat):
    d = copy.deepcopy(FAN)
    d["parts"] = [p for p in d["parts"] if p["ref"] != "R2"]
    d["parts"][-1].update(load_ohms=1000, why="indicator lamp")  # 5 mA
    d["parts"][1]["value"] = "47k"
    before = copy.deepcopy(d)
    res = analog.solve(d, [], cat, ask="light up when dark")
    assert d == before and not res.notes


def test_gpio_driver_resistor(cat):
    pico = next(k for k, p in cat.items() if (p.get("spice") or {}).get("role") == "mcu")
    gp = next(n for n, nm in cat[pico]["pins"].items() if str(nm).upper().startswith("GPIO"))
    d = {
        "voltage": 5.0,
        "parts": [
            {"ref": "U1", "piece": pico, "pins": {gp: "GP15"}},
            {"ref": "R1", "piece": R, "value": "47k", "pins": {"1": "GP15", "2": "Q1_B"}},
            {"ref": "Q1", "piece": analog.KEY_3904, "pins": {"1": "GND", "2": "Q1_B", "3": "Q1_C"}},
            {"ref": "J2", "piece": CONN, "pins": {"1": "VCC", "2": "Q1_C"}, "load_ohms": 33, "why": "motor"},
        ],
        "benchmarks": [{"check": "J2.avg_ma", "min": 50, "max": 200}],
    }
    res = analog.solve(d, [], cat, ask="spin a motor from a pico")
    r1 = next(p for p in d["parts"] if p["ref"] == "R1")
    assert analog.parse_value(r1["value"]) <= 1000
    assert res.notes


def test_backwards_flyback_is_flipped(cat):
    # run live-1792258602299: the repair put D1's anode on VCC; it shorted the supply (952 mA, fan 27 mA)
    d = copy.deepcopy(FAN)
    d1 = next(p for p in d["parts"] if p["ref"] == "D1")
    d1["pins"] = {"1": "Q1_C", "2": "VCC"}  # K on the collector, A on VCC
    res = analog.solve(d, [], cat, "spin a fan when dark")
    assert d1["pins"] == {"1": "VCC", "2": "Q1_C"}
    assert any("flyback diode was backwards" in n for n in res.notes)


def test_correct_flyback_untouched(cat):
    d = copy.deepcopy(FAN)
    res = analog.solve(d, [], cat, "spin a fan when dark")
    assert next(p for p in d["parts"] if p["ref"] == "D1")["pins"] == {"1": "VCC", "2": "Q1_C"}
    assert not any("flyback" in n for n in res.notes)


def test_power_input_on_load_net_is_moved_to_gnd(cat):
    # run live-1792259861853: J1 pin 2 on the motor net put the motor across the supply (200 mA when released)
    d = copy.deepcopy(FAN)
    j1 = d["parts"][0]
    j1["pins"] = {"1": "VCC", "2": "Q1_C"}
    res = analog.solve(d, [], cat, "spin a fan when dark")
    assert j1["pins"] == {"1": "VCC", "2": "GND"}
    assert any("power input pin 2" in n for n in res.notes)


def test_reversed_power_input_is_swapped(cat):
    d = copy.deepcopy(FAN)
    d["parts"][0]["pins"] = {"1": "GND", "2": "VCC"}
    analog.solve(d, [], cat, "spin a fan when dark")
    assert d["parts"][0]["pins"] == {"1": "VCC", "2": "GND"}


def test_good_power_input_and_load_connector_untouched(cat):
    d = copy.deepcopy(FAN)
    res = analog.solve(d, [], cat, "spin a fan when dark")
    assert d["parts"][0]["pins"] == {"1": "VCC", "2": "GND"}
    assert next(p for p in d["parts"] if p["ref"] == "J2")["pins"] == {"1": "VCC", "2": "Q1_C"}
    assert not any("power input" in n for n in res.notes)

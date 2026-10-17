"""A benchmark the simulation can never satisfy (prod run live-1792266957411, 10-17): the OLED connector is a fixed
250 ohm load across VCC/GND, 20 mA in every scenario, and the picker wrote "little activity when dry" next to
"~20 mA when wet". All hard checks passed; the board must pass with that check marked not checkable."""

import json
import tempfile
from pathlib import Path

from agent import kicad_catalog, live

PICO = "kicad:MCU_Module:RaspberryPi_Pico@RaspberryPi_Pico_Common_THT"
OLED = "kicad:Connector_Generic:Conn_01x06@PinSocket_1x06_P2.54mm_Vertical"
R = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"


def _kept():
    cat = {p["key"]: p for p in kicad_catalog.load()["pieces"]}
    return [cat[k] for k in (PICO, OLED, R)]


def _case(oled_pins=None, wet_value=20.0):
    design = {
        "parts": [
            {"ref": "U1", "piece": PICO, "pins": {"3": "GND", "40": "VCC", "20": "GPIO15", "36": "3V3"}},
            {
                "ref": "J1",
                "piece": OLED,
                "value": "OLED",
                "pins": oled_pins
                or {"1": "VCC", "2": "3V3", "3": "GND", "4": "GPIO15", "5": "GPIO16", "6": "GPIO17"},
            },
            {"ref": "R1", "piece": R, "pins": {"1": "VCC", "2": "SENSE_NODE"}},
        ],
        "scenarios": [{"name": "dry", "voltage": 5}, {"name": "wet", "voltage": 5}],
    }
    hard = [
        {"check": c, "value": 0, "pass": True, "text": c}
        for c in ("drc_violations", "erc_errors", "unconnected", "parity")
    ]
    v = {
        "pass": False,
        "unrouted": [],
        "models_missing": 0,
        "benchmarks": hard
        + [
            {"check": "dry.supply_ma_avg", "value": 20.25, "pass": True, "text": "supply (dry)"},
            {"check": "dry.J1.avg_ma", "value": 20.0, "pass": False, "text": "OLED little activity in dry scenario."},
            {"check": "wet.J1.avg_ma", "value": wet_value, "pass": True, "text": "OLED active ~20 mA when wet."},
        ],
        "failed": ["OLED little activity in dry scenario. (got 20.0)"],
    }
    out = Path(tempfile.mkdtemp())
    sim = {
        "scenarios": [{"name": "dry"}, {"name": "wet"}],
        "dry.J1.avg_ma": 20.0,
        "wet.J1.avg_ma": wet_value,
    }
    (out / "sim.json").write_text(json.dumps(sim))
    return design, v, out


def test_fixed_load_contradiction_is_not_checkable_and_the_board_passes():
    design, v, out = _case()
    assert live._static_benchmarks(design, v, out, _kept()) == ["dry.J1.avg_ma"]
    assert v["pass"] is True and v["failed"] == []
    row = next(b for b in v["benchmarks"] if b["check"] == "dry.J1.avg_ma")
    assert row["pass"] is None and row["status"] == "uncheckable"  # never shown as passed
    assert "benchmark dry.J1.avg_ma" not in live._failed_checks(v)


def test_a_switched_load_still_fails():
    # load pin 1 on a switched net: the current can change, so the failure is real
    design, v, out = _case({"1": "FAN_N", "3": "GND"})
    assert live._static_benchmarks(design, v, out, _kept()) == []
    assert v["pass"] is False


def test_a_load_that_changes_between_scenarios_still_fails():
    design, v, out = _case(wet_value=19.0)
    v["benchmarks"][-1]["pass"] = True
    assert live._static_benchmarks(design, v, out, _kept()) == []
    assert v["pass"] is False


def test_a_hard_failure_keeps_the_board_failing():
    design, v, out = _case()
    v["unrouted"] = ["SENSE_NODE"]
    live._static_benchmarks(design, v, out, _kept())
    assert v["pass"] is False

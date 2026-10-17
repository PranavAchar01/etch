"""Picker QA 10-17, second pass: made-up piece keys resolve to the real piece they name (no "pieces not in the KEPT
list" after repair), stacked parallel copies fold into one value, idle loads get no band they cannot meet, and the
soil-moisture ask has its own recipe. One test per class, from the field runs named in each."""

import json
import tempfile
from pathlib import Path

import pytest

from agent import kicad_catalog, live, recipes

R = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
PICO = live.PICO_KEY


def _cat(*keys):
    cat = {p["key"]: p for p in kicad_catalog.load()["pieces"]}
    return [cat[k] for k in keys]


@pytest.mark.parametrize(
    "made_up,pins,want",
    [
        # laptop motherboard run live-1792267068561: a colour after the real footprint
        ("kicad:Device:LED@LED_D5.0mm_GREEN", {"1": "GND", "2": "A"}, live.LED_KEY),
        # keyboard run live-1792265815786: D1-D4 on an SMD diode with a mangled footprint
        (
            "kicad:Device:D@D_SmallSignal_SMD_Diodes,D:DIOD1N4148",
            {"1": "ROW", "2": "COL"},
            "1N4148@",
        ),
        # nuclear reactor run live-1792266896652: bare symbol words and a footprint-only name
        ("SW_Push", {"1": "BTN", "2": "GND"}, "Switch:SW_Push@"),
        ("Buzzer", {"1": "BZ", "2": "GND"}, "Device:Buzzer@"),
        (
            "Conn_01x02",
            {"1": "VCC", "2": "GND"},
            "Conn_01x02@PinHeader_1x02_P2.54mm_Vertical",
        ),
        ("R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_VertICAL", {"1": "A", "2": "B"}, R),
        # esp32 plant run live-1792266628730: a 2x3 header by pin count
        (
            "kicad:Connector:Conn_02x03_Oval",
            {"1": "A", "2": "B", "3": "C"},
            "Conn_01x06@",
        ),
        # synth rerun live-1792268207874: a potentiometer named by a made-up footprint
        (
            "kicad:Device:RV_Axial_PD10_L6.3mm_P5.0mm_Vertical",
            {"1": "A", "2": "W", "3": "B"},
            "R_Potentiometer_Trim@",
        ),
        # USB-C night light live-1792266540593: an SMD receptacle name, wired by USB-C pin names
        (
            "kicad:Connector:USB_C@USB_C_Receptacle",
            {
                "A9": "VCC",
                "B9": "VCC",
                "A12": "GND",
                "B12": "GND",
                "A5": "CC1",
                "B5": "CC2",
            },
            "USB_C_Receptacle_PowerOnly_6P@",
        ),
    ],
)
def test_made_up_key_resolves_to_the_real_piece(made_up, pins, want):
    twin = live._twin_piece(made_up, pins, _cat(PICO, R))
    assert twin and want in twin["key"]


def test_twin_prefers_the_kept_piece():
    kept = _cat("kicad:Connector_Generic:Conn_01x06@PinSocket_1x06_P2.54mm_Vertical")
    twin = live._twin_piece(
        "kicad:Connector:PinHeader_2x03_P2.54mm_Vertical", {"1": "a"}, kept
    )
    assert twin["key"] == kept[0]["key"]


def test_twin_never_drops_a_used_pin():
    # a 2-pin LED cannot stand in for a part wired on three pins
    assert live._twin_piece("LED", {"1": "a", "2": "b", "3": "c"}, []) is None


def test_resolve_unknown_adds_the_twin_to_kept():
    class S:
        def emit(self, *a, **k):
            pass

    kept = _cat(PICO)
    keys = {p["key"] for p in kept}
    part = {"ref": "J1", "piece": "Conn_01x02", "pins": {"1": "VCC", "2": "GND"}}
    live._resolve_unknown(S(), [part], kept, keys, set())
    assert part["piece"] in keys and any(p["key"] == part["piece"] for p in kept)


def test_parallel_copies_fold_into_one_value():
    # prod job QbyBcUKTtsmLEaJr: the repair stacked R1b and R1c beside R1 to raise a current
    d = {
        "parts": [
            {
                "ref": "R1",
                "piece": R,
                "value": "10k",
                "pins": {"1": "AUDIO_OUT", "2": "AUDIO"},
            },
            {
                "ref": "R1b",
                "piece": R,
                "value": "10k",
                "pins": {"1": "AUDIO_OUT", "2": "AUDIO"},
            },
            {
                "ref": "R1c",
                "piece": R,
                "value": "10k",
                "pins": {"1": "AUDIO", "2": "AUDIO_OUT"},
            },
            {
                "ref": "R2",
                "piece": R,
                "value": "10k",
                "pins": {"1": "AUDIO", "2": "OUT2"},
            },
        ]
    }
    notes = live._fold_parallel(d)
    assert notes and [p["ref"] for p in d["parts"]] == ["R1", "R2"]
    assert d["parts"][0]["value"] == "3.33k"
    assert not live._wiring_lint(d, _cat(R))


def test_two_real_resistors_in_parallel_are_left_to_the_lint():
    d = {
        "parts": [
            {"ref": "R1", "piece": R, "value": "10k", "pins": {"1": "A", "2": "B"}},
            {"ref": "R2", "piece": R, "value": "10k", "pins": {"1": "A", "2": "B"}},
        ]
    }
    assert not live._fold_parallel(d) and len(d["parts"]) == 2


def _idle_case(sp_ma=0.0, supply=4.5, scenarios=None):
    rows = [
        {
            "check": "idle.supply_ma_avg",
            "min": 5,
            "max": 200,
            "value": supply,
            "pass": False,
            "text": "Average supply current including speaker load (idle)",
        },
        {
            "check": "idle.SP1.avg_ma",
            "min": 10,
            "max": 200,
            "value": sp_ma,
            "pass": sp_ma >= 10,
            "text": "Speaker average current band (idle)",
        },
    ]
    v = {
        "pass": False,
        "benchmarks": rows,
        "failed": [r["text"] for r in rows if r["pass"] is False],
    }
    sim = {
        "scenarios": [{"name": "idle"}],
        "idle.supply_ma_avg": supply,
        "idle.SP1.avg_ma": sp_ma,
        "loads": {"SP1": {"avg_ma": sp_ma}},
        "leds": {},
    }
    out = Path(tempfile.mkdtemp())
    (out / "sim.json").write_text(json.dumps(sim))
    design = {"parts": [], "scenarios": scenarios}
    return design, v, out


def test_idle_speaker_band_is_not_checkable():
    design, v, out = _idle_case()
    marked = live._static_benchmarks(design, v, out, [])
    assert sorted(marked) == ["idle.SP1.avg_ma", "idle.supply_ma_avg"]
    assert v["pass"] is True and all(
        b["status"] == "uncheckable" for b in v["benchmarks"]
    )


def test_a_load_that_draws_current_is_still_checked():
    design, v, out = _idle_case(sp_ma=3.0, supply=7.0)
    v["benchmarks"][0]["value"], v["benchmarks"][0]["min"] = 7.0, 50
    assert live._static_benchmarks(design, v, out, []) == []
    assert v["pass"] is False


def test_idle_band_minimum_comes_from_the_design():
    # synth rerun live-1792268207874: verdict rows carry no min, the band lives in design["benchmarks"]
    design, v, out = _idle_case()
    for b in v["benchmarks"]:
        b.pop("min"), b.pop("max")
    design["benchmarks"] = [
        {"check": "supply_ma_avg", "min": 5, "max": 50},
        {"check": "SP1.avg_ma", "min": 10, "max": 200},
    ]
    assert sorted(live._static_benchmarks(design, v, out, [])) == [
        "idle.SP1.avg_ma",
        "idle.supply_ma_avg",
    ]
    assert v["pass"] is True


def test_over_limit_idle_reading_stays_a_failure():
    design, v, out = _idle_case()
    v["benchmarks"][0].update(min=None, max=2, value=4.5)
    v["benchmarks"].pop()
    assert live._static_benchmarks(design, v, out, []) == []


@pytest.mark.parametrize(
    "ask",
    [
        "USB-C powered plant moisture sensor with an OLED",
        "a soil moisture sensor with a small display",
        "a plant moisture monitor",
    ],
)
def test_soil_asks_reach_the_recipe(ask):
    cat = {p["key"]: p for p in kicad_catalog.load()["pieces"]}
    got = recipes.match(ask, cat)
    assert got and got[1] == "soil_display"
    nets = {n for p in got[0]["parts"] for n in p["pins"].values()}
    assert "SOIL_ADC" in nets


def test_watering_is_not_the_sensor_recipe():
    assert (
        recipes.soil_display(
            "an esp32 board that waters my plants when the soil is dry"
        )
        is None
    )

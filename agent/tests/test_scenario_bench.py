"""Scenario benchmark expansion and stimulus-free scenarios (sweep 10-17, soil alarm)."""

from agent import live

R = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"


def _checks(benchmarks, scenarios):
    d = {
        "parts": [{"ref": "R1", "piece": R, "pins": {"1": "VCC", "2": "GND"}, "group": "sensor", "value": "1k"}],
        "benchmarks": benchmarks,
        "scenarios": scenarios,
    }
    return [(b["check"], b.get("min"), b.get("max")) for b in live.to_spec(d, "x")["benchmarks"][4:]]


SC = [{"name": "dry", "ldr": "dark"}, {"name": "wet", "ldr": "light"}]


def test_unprefixed_row_naming_one_scenario_stays_there():
    got = _checks(
        [
            {"check": "BZ1.avg_ma", "min": 10, "max": 30, "text": "buzzer on when dry"},
            {"check": "wet.BZ1.avg_ma", "min": 0, "max": 0.5, "text": "buzzer off when wet"},
        ],
        SC,
    )
    assert got == [("dry.BZ1.avg_ma", 10, 30), ("wet.BZ1.avg_ma", 0, 0.5)]


def test_unprefixed_row_skips_scenario_with_its_own_row():
    got = _checks(
        [
            {"check": "BZ1.avg_ma", "min": 10, "max": 30, "text": "buzzer sounds"},
            {"check": "wet.BZ1.avg_ma", "min": 0, "max": 0.5, "text": "quiet"},
        ],
        SC,
    )
    assert ("wet.BZ1.avg_ma", 10, 30) not in got and ("dry.BZ1.avg_ma", 10, 30) in got


def test_generic_row_still_holds_in_every_scenario():
    got = _checks([{"check": "D1.peak_ma", "max": 25, "text": "safe LED"}], SC)
    assert [c for c, *_ in got] == ["dry.D1.peak_ma", "wet.D1.peak_ma"]


def test_stimulus_free_scenarios_are_flagged():
    assert "change nothing" in live._scenario_note({"scenarios": [{"name": "dry"}, {"name": "wet"}]})
    assert live._scenario_note({"scenarios": SC}) == ""
    assert live._scenario_note({"scenarios": []}) == ""

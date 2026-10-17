"""to_spec value defaults (sweep 10-17: a blank NTC value reached ngspice as its symbol name)."""

from agent import live

NTC = "kicad:Device:Thermistor_NTC@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"


def _spec(value):
    p = {"ref": "TH1", "piece": NTC, "pins": {"1": "THERM", "2": "GND"}, "group": "sensor"}
    if value is not None:
        p["value"] = value
    return live.to_spec({"parts": [p], "benchmarks": []}, "x")["parts"][0]


def test_blank_ntc_gets_catalog_value():
    assert _spec(None)["value"] == "10k"
    assert _spec("Thermistor_NTC")["value"] == "10k"


def test_given_ntc_value_kept():
    assert _spec("4.7k")["value"] == "4.7k"

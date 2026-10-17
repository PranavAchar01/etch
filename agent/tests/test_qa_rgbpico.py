"""QA 10-17, run engine/out/live-1792255105003 (RGB LED board on a Raspberry Pi Pico, 3 of 8 gates):
LED parts simulate as D elements with a colour card, an LED drawn on a capacitor piece is sent back to the picker,
the auto-added Pico takes the power connector's VCC on VSYS, and the routed gate says when the router timed out.
No KiCad run needed (router tests: engine/tests/test_route_budget.py)."""

import copy
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from agent import engine_api, kicad_catalog, live
from etchlib import sim
from etchlib.spec import load_spec

NGSPICE = shutil.which("ngspice") or (
    "/opt/homebrew/bin/ngspice" if Path("/opt/homebrew/bin/ngspice").exists() else None
)
CONN = "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical"
RES = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
CAP = "kicad:Device:C@C_Disc_D5.0mm_W2.5mm_P2.50mm"
ASK = "Hey Edge, um can you build me a PCB?"

# the picker's first design in live-1792255105003 (spec.yaml there, before Etch added the Pico)
FAILING = {
    "title": "RGB PICO",
    "say": "USB-powered RGB LED controller using three GPIO-driven channels.",
    "parts": [
        {"ref": "U1", "piece": CONN, "pins": {"1": "VCC", "2": "GND"}, "group": "power"},
        {"ref": "R1", "piece": RES, "value": "150", "pins": {"1": "GPIO15", "2": "LED_RED_A"}, "group": "output"},
        {"ref": "R2", "piece": RES, "value": "68", "pins": {"1": "GPIO16", "2": "LED_GRN_A"}, "group": "output"},
        {"ref": "R3", "piece": RES, "value": "33", "pins": {"1": "GPIO17", "2": "LED_BLU_A"}, "group": "output"},
        {"ref": "D1", "piece": CAP, "value": "RED", "pins": {"1": "LED_RED_A", "2": "GND"}, "group": "output"},
        {"ref": "D2", "piece": CAP, "value": "GREEN", "pins": {"1": "LED_GRN_A", "2": "GND"}, "group": "output"},
        {"ref": "D3", "piece": CAP, "value": "BLUE", "pins": {"1": "LED_BLU_A", "2": "GND"}, "group": "output"},
    ],
}


@pytest.fixture(scope="module")
def cat():
    return {p["key"]: p for p in kicad_catalog.load()["pieces"]}


def _kept(d, cat):
    return [cat[k] for k in sorted({p["piece"] for p in d["parts"]})]


def _with_leds(d):
    """The repaired design: real LED pieces, pin 2 = anode, pin 1 = cathode."""
    d = copy.deepcopy(d)
    for p in d["parts"]:
        if p["ref"].startswith("D"):
            a = p["pins"]["1"]
            p.update(piece=live.LED_KEY, value=p["value"].lower(), pins={"2": a, "1": "GND"})
    return d


# (1) LED models -------------------------------------------------------------------------------------------------


@pytest.mark.skipif(not NGSPICE, reason="no ngspice")
@pytest.mark.parametrize("colour,vf", [("RED", 2.0), ("GREEN", 3.0), ("BLUE", 3.1)])
def test_led_card_forward_voltage(colour, vf):
    ckt = f"vf\n{sim.MODELS['LED_' + colour]}\nI1 0 a 10m\nD1 a 0 LED_{colour}\n.control\nop\nprint v(a)\n.endc\n.end\n"
    out = subprocess.run([NGSPICE, "-b", "-n"], input=ckt, capture_output=True, text=True).stdout
    got = float(re.search(r"v\(a\) = ([-\d.e+]+)", out).group(1))
    assert abs(got - vf) < 0.03, (colour, got)


def _spec(d, out: Path, scenarios=None):
    d = copy.deepcopy(d)
    d["scenarios"] = scenarios or [{"name": "on", "gpio": {"GPIO15": True, "GPIO16": True, "GPIO17": True}}]
    (out / "spec.yaml").write_text(yaml.safe_dump(live.to_spec(d, "TEST"), sort_keys=False))
    return load_spec(out / "spec.yaml")


def test_rgb_leds_are_d_elements_with_colour_models(cat):
    d = _with_leds(FAILING)
    kept = _kept(d, cat)
    assert live._auto_mcu(d, kept, ASK)
    with tempfile.TemporaryDirectory() as td:
        net, _, _ = sim.netlist(_spec(d, Path(td)), 0.01, 0.5e-3, {"name": "on", "gpio": {"GPIO15": True}})
    for ref, model in (("D1", "LED_RED"), ("D2", "LED_GREEN"), ("D3", "LED_BLUE")):
        assert re.search(rf"^D{ref} \S+ \S+ {model}$", net, re.M), net
        assert f".model {model} D(" in net
    assert not re.search(r"^C", net, re.M)


@pytest.mark.skipif(not NGSPICE, reason="no ngspice")
def test_rgb_board_on_connector_powered_pico_lights(cat):
    d = _with_leds(FAILING)
    kept = _kept(d, cat)
    live._auto_mcu(d, kept, ASK)
    scen = [
        {"name": "on", "gpio": {"GPIO15": True, "GPIO16": True, "GPIO17": True}},
        {"name": "off", "gpio": {}},
    ]
    with tempfile.TemporaryDirectory() as td:
        res = sim.simulate(_spec(d, Path(td), scen), Path(td))
    assert res["ok"], res
    for ref in ("D1", "D2", "D3"):
        assert 1 <= res[f"on.{ref}.avg_ma"] <= 25, (ref, res[f"on.{ref}.avg_ma"])
        assert res[f"off.{ref}.avg_ma"] <= 0.5
    assert res["on.supply_ma_avg"] > res["off.supply_ma_avg"] + 3  # the LED current is drawn from VCC via VSYS


def test_non_numeric_capacitor_value_is_a_clear_error(cat):
    d = copy.deepcopy(FAILING)
    live._fix_refs(d)  # D1..D3 on capacitor pieces become C1..C3, as they did in the failing run
    d["parts"].append(
        {
            "ref": "U2",
            "piece": live.PICO_KEY,
            "pins": {"39": "VCC", "3": "GND", "31": "GPIO15", "32": "GPIO16", "34": "GPIO17"},
            "group": "mcu",
        }
    )
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(ValueError, match=r"C\d: value 'RED' is not a capacitance .*LED part"):
            sim.netlist(_spec(d, Path(td)), 0.01, 0.5e-3, {})


def test_led_on_capacitor_piece_goes_back_to_the_picker(cat):
    d = copy.deepcopy(FAILING)
    kept = _kept(d, cat)
    keys = {p["key"] for p in kept}
    assert live._led_stand_ins(d, kept, keys) == ["D1", "D2", "D3"]
    assert live.LED_KEY in keys and any(p["key"] == live.LED_KEY for p in kept)
    assert live._led_stand_ins(_with_leds(d), kept, keys) == []
    # a plain capacitor or resistor is left alone
    assert live._led_stand_ins({"parts": [{"ref": "C1", "piece": CAP, "value": "100n"}]}, kept, keys) == []


# (2) the Pico's power pins ---------------------------------------------------------------------------------------


def test_auto_pico_takes_connector_vcc_on_vsys(cat):
    d = copy.deepcopy(FAILING)
    kept = _kept(d, cat)
    added = live._auto_mcu(d, kept, ASK)
    assert added and added["piece"] == live.PICO_KEY
    pico = next(p for p in d["parts"] if p["piece"] == live.PICO_KEY)
    assert pico["pins"].get("39") == "VCC"  # VSYS shares the power connector's rail
    assert "40" not in pico["pins"] and pico["pins"].get("36") != "VCC"  # never back-drive VBUS or 3V3 out
    assert {pico["pins"][k] for k in ("31", "32", "34")} | {pico["pins"].get(k) for k in pico["pins"]} >= {
        "GPIO15",
        "GPIO16",
        "GPIO17",
    }
    assert live._lonely_nets(d) == []  # the "VCC only on U1 pin 1" error of iterations 1 and 2 is gone


def test_auto_pico_without_power_input_still_feeds_vbus(cat):
    d = copy.deepcopy(FAILING)
    d["parts"][0]["pins"] = {"1": "SIG", "2": "GND"}  # no power connector on VCC
    d["parts"].append({"ref": "R4", "piece": RES, "value": "10k", "pins": {"1": "VCC", "2": "SIG"}})
    kept = _kept(d, cat)
    live._auto_mcu(d, kept, ASK)
    pico = next(p for p in d["parts"] if p["piece"] == live.PICO_KEY)
    assert pico["pins"].get("40") == "VCC" and "39" not in pico["pins"]


# (3) router: engine/tests/test_route_budget.py; here only the gate text


def test_routed_gate_says_router_timed_out():
    v = {"route_timed_out": True, "route_s": 360.2, "unrouted": ["GND", "VCC"]}
    assert engine_api.routed_value(v) == "router timed out after 360.2 s; unrouted: ['GND', 'VCC']"
    assert engine_api.routed_value({"route_timed_out": False, "unrouted": []}) == 0


# the verification build's last try (live-1792256195647): a USB-powered Pico with VCC on VBUS and nothing else on it
RGB_KEY = "kicad:Device:LED_RKGB@LED_D5.0mm-4_RGB_Wide_Pins"
PICO_RGB = {
    "title": "RGB PICO",
    "parts": [
        {"ref": "U1", "piece": live.PICO_KEY, "group": "mcu",
         "pins": {"40": "VCC", "3": "GND", "38": "GND", "20": "GPIO15", "21": "GPIO16", "22": "GPIO17"}},
        {"ref": "D1", "piece": RGB_KEY, "value": "RGB", "group": "output",
         "pins": {"1": "LED_R_A", "2": "GND", "3": "LED_G_A", "4": "LED_B_A"}},
        {"ref": "R1", "piece": RES, "value": "150", "pins": {"1": "GPIO15", "2": "LED_R_A"}, "group": "output"},
        {"ref": "R2", "piece": RES, "value": "68", "pins": {"1": "GPIO16", "2": "LED_G_A"}, "group": "output"},
        {"ref": "R3", "piece": RES, "value": "33", "pins": {"1": "GPIO17", "2": "LED_B_A"}, "group": "output"},
    ],
}


def test_lonely_vcc_on_the_picos_own_vbus_is_dropped(cat):
    d = copy.deepcopy(PICO_RGB)
    kept = _kept(d, cat)
    live._drop_lonely_gpio(d, kept)
    assert "40" not in d["parts"][0]["pins"]
    assert live._lonely_nets(d) == []
    # a supply net shared with another part stays
    d2 = copy.deepcopy(PICO_RGB)
    d2["parts"][1]["pins"]["2"] = "VCC"
    d2["parts"].append({"ref": "J1", "piece": CONN, "pins": {"1": "VCC", "2": "GND"}, "group": "power"})
    live._drop_lonely_gpio(d2, _kept(d2, cat))
    assert d2["parts"][0]["pins"]["40"] == "VCC"


@pytest.mark.skipif(not NGSPICE, reason="no ngspice")
def test_usb_pico_rgb_lights_each_die(cat):
    d = copy.deepcopy(PICO_RGB)
    live._drop_lonely_gpio(d, _kept(d, cat))
    scen = [{"name": "on", "gpio": {"GPIO15": True, "GPIO16": True, "GPIO17": True}}, {"name": "off", "gpio": {}}]
    with tempfile.TemporaryDirectory() as td:
        res = sim.simulate(_spec(d, Path(td), scen), Path(td))
    assert res["ok"], res
    for die in ("D1_R", "D1_G", "D1_B"):
        assert 1 <= res[f"on.{die}.avg_ma"] <= 25, (die, res[f"on.{die}.avg_ma"])
        assert res[f"off.{die}.avg_ma"] <= 0.5

"""Nixie tube driver (live-1792273685866, 10-17): ERC failed 3 on every try (MC34063 Vin alone on "VIN",
4001 VSS on its own "VSS" net, Pico VSYS on a connector-fed rail with no PWR_FLAG), ngspice failed "no LED or
load", and the 170 V nets had no high-voltage clearance."""

import copy

from agent import live

CONN = "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical"
R = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
MC = "kicad:Regulator_Switching:MC34063AD@SOIC-8_3.9x4.9mm_P1.27mm"
NOR = "kicad:4xxx:4001@DIP-14_W10.16mm"
D = "kicad:Diode:1N4148@D_DO-35_SOD27_P7.62mm_Horizontal"
CP = "kicad:Device:C_Polarized@CP_Radial_D5.0mm_P2.00mm"

# the picker's first design for the nixie prompt, before Etch's fix-ups (U3, the Pico, was added by _auto_mcu)
NIXIE = {
    "parts": [
        {
            "ref": "J1",
            "piece": CONN,
            "pins": {"1": "VCC", "2": "GND"},
            "group": "power",
        },
        {
            "ref": "U1",
            "piece": MC,
            "group": "power",
            "pins": {
                "1": "SW_C",
                "2": "SW_E",
                "3": "TC",
                "4": "GND",
                "5": "VFB",
                "6": "VIN",
                "7": "IPEAK",
                "8": "DCOUT",
            },
        },
        {
            "ref": "L1",
            "piece": R,
            "value": "220uH",
            "pins": {"1": "SW_E", "2": "SW_C"},
            "group": "power",
        },
        {
            "ref": "D1",
            "piece": D,
            "value": "1N4007",
            "pins": {"1": "DCOUT", "2": "SW_C"},
            "group": "power",
        },
        {
            "ref": "C1",
            "piece": CP,
            "value": "22uF",
            "pins": {"1": "DCOUT", "2": "GND"},
            "group": "power",
        },
        {
            "ref": "R2",
            "piece": R,
            "value": "100k",
            "pins": {"1": "DCOUT", "2": "VFB"},
            "group": "power",
        },
        {
            "ref": "R1",
            "piece": R,
            "value": "1.5k",
            "pins": {"1": "VFB", "2": "GND"},
            "group": "power",
        },
        {
            "ref": "U2",
            "piece": NOR,
            "group": "driver",
            "pins": {
                "1": "QA",
                "2": "QB",
                "5": "NC",
                "6": "NC",
                "7": "VSS",
                "8": "NC",
                "14": "VDD",
            },
        },
        {
            "ref": "R5",
            "piece": R,
            "value": "10k",
            "pins": {"1": "VSS", "2": "GND"},
            "group": "driver",
        },
        {
            "ref": "R4",
            "piece": R,
            "value": "10k",
            "pins": {"1": "VDD", "2": "GND"},
            "group": "driver",
        },
        {
            "ref": "J2",
            "piece": CONN,
            "pins": {"1": "HV_ANODE", "2": "NC"},
            "group": "output",
        },
    ]
}


def _kept():
    cat = live._cat()
    return [cat[k] for k in {p["piece"] for p in NIXIE["parts"]}]


def _pins(d, ref):
    return next(p for p in d["parts"] if p["ref"] == ref)["pins"]


def test_power_pins_wired_before_auto_mcu():
    d = copy.deepcopy(NIXIE)
    did = live._fix_power_pins(d, _kept())
    assert (
        _pins(d, "U1")["6"] == "VCC"
    )  # MC34063 Vin alone on "VIN" -> the J1 rail, not a GPIO
    assert _pins(d, "U2")["7"] == "GND"  # 4001 VSS on its own "VSS" net -> ground
    assert "R5" not in {
        p["ref"] for p in d["parts"]
    }  # the 10k between VSS and GND shorted itself out
    assert "NC" not in {
        n for p in d["parts"] for n in p["pins"].values()
    }  # "NC" is no net
    assert did
    added = live._auto_mcu(d, _kept(), "nixie driver")
    assert added is None or "VIN" not in added["say"]
    pico = [p for p in d["parts"] if p["piece"] in (live.PICO_KEY, live.PICO_W_KEY)]
    # the 170 V anode net is lonely too, but never goes on a 3.3 V GPIO pin
    assert all("HV_ANODE" not in p["pins"].values() for p in pico)


def test_supply_pin_with_company_is_left_alone():
    d = copy.deepcopy(NIXIE)
    _pins(d, "U1")["6"] = "VCC_SW"
    d["parts"].append({"ref": "C9", "piece": CP, "pins": {"1": "VCC_SW", "2": "GND"}})
    live._fix_power_pins(d, _kept())
    assert _pins(d, "U1")["6"] == "VCC_SW"


def test_hv_nets_and_clearance():
    d = copy.deepcopy(NIXIE)
    ask = "build a Nixie tube driver with 170V boost supply (High voltage, so creepage and clearance matter)"
    volts, nets = live._hv_nets(d, _kept(), ask)
    assert volts == 170
    # the named anode net, and the boost diode's both ends (switch node and output)
    assert {"HV_ANODE", "DCOUT", "SW_C"} <= set(nets)
    assert "GND" not in nets and "VCC" not in nets and "VFB" not in nets
    assert (
        live.hv_clearance(170) == 1.25
    )  # IPC-2221B table 6-1, B2 (external, uncoated)
    assert live.hv_clearance(400) == 2.5
    assert live._hv_nets(d, _kept(), "blink an LED")[1] == []


def test_power_block_wraps_into_rows():
    parts = [{"ref": f"R{i}", "piece": R, "group": "power"} for i in range(9)]
    (blk,) = live.auto_layout(parts, ["power"])
    assert len(blk["rows"]) <= 3

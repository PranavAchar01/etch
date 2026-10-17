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


def test_self_named_module_pins_are_left_open():
    design = {
        "parts": [
            {"ref": "U1", "piece": live.PICO_W_KEY, "pins": {"3": "GND", "24": "LED", "30": "RUN", "37": "3V3_EN", "35": "ADC_VREF", "33": "AGND"}},
            {"ref": "R1", "piece": "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical", "pins": {"1": "LED", "2": "GND"}},
        ]
    }
    kept = [live._cat()[live.PICO_W_KEY]]
    live._drop_lonely_gpio(design, kept)
    assert design["parts"][0]["pins"] == {"3": "GND", "24": "LED"}


# --- deterministic LED / drive repairs (10-17 tracker runs) ---------------------------------------------------

import copy  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from agent import engine_api as eng  # noqa: E402
from agent.events import Stream  # noqa: E402

LED = "kicad:Device:LED@LED_D5.0mm"
R = live.R_KEY
COIN = "kicad:Device:Battery_Cell@BatteryHolder_Keystone_1058_1x2032"
BTN = "kicad:Switch:SW_Push@SW_PUSH_6mm"
BUZ = "kicad:Device:Buzzer@Buzzer_D14mm_H7mm_P10mm"
GNDS = {"3": "GND", "38": "GND"}


def _kept(design):
    cat = live._cat()
    return [copy.deepcopy(cat[p["piece"]]) for p in design["parts"]]


def _nets(design):
    return {p["ref"]: dict(p["pins"]) for p in design["parts"]}


def test_lonely_cathode_goes_to_ground():
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_W_KEY, "pins": {**GNDS, "20": "GPIO15"}},
        {"ref": "R1", "piece": R, "pins": {"1": "GPIO15", "2": "LED1_A"}},
        {"ref": "D1", "piece": LED, "pins": {"1": "LED1_K", "2": "LED1_A"}},
    ]}
    live._fix_leds(d, _kept(d))
    assert _nets(d)["D1"] == {"1": "GND", "2": "LED1_A"}


def test_bare_led_gets_a_resistor_and_a_gpio():
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_W_KEY, "pins": dict(GNDS)},
        {"ref": "D1", "piece": LED, "pins": {"1": "K", "2": "A"}},
    ], "firmware": {"commands": [{"input": "1", "high": ["GPIO15"]}]}}
    kept = _kept(d)
    live._fix_leds(d, kept)
    live._wire_drives(d, kept)
    n = _nets(d)
    assert n["D1"] == {"1": "GND", "2": "A"}
    assert n["R1"] == {"1": "GPIO15", "2": "A"}
    assert n["U1"]["20"] == "GPIO15"  # the firmware's own net, on the pin of that name
    assert not live._lonely_nets(d)


def test_shorted_series_resistor_is_split():
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_W_KEY, "pins": {**GNDS, "20": "LED_NET"}},
        {"ref": "D1", "piece": LED, "pins": {"1": "GND", "2": "LED_NET"}},
        {"ref": "R1", "piece": R, "pins": {"1": "LED_NET", "2": "LED_NET"}},
    ]}
    live._fix_leds(d, _kept(d))
    n = _nets(d)
    assert n["R1"] == {"1": "LED_NET", "2": "D1_A"} and n["D1"]["2"] == "D1_A"
    assert not live._wiring_lint(d, _kept(d))


def test_anode_moves_behind_a_dangling_resistor():
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_W_KEY, "pins": {**GNDS, "20": "LED_NET"}},
        {"ref": "D1", "piece": LED, "pins": {"1": "GND", "2": "LED_NET"}},
        {"ref": "R1", "piece": R, "pins": {"1": "LED_NET", "2": "D1_A"}},
    ]}
    live._fix_leds(d, _kept(d))
    assert _nets(d)["D1"]["2"] == "D1_A" and not live._lonely_nets(d)


def test_rail_hung_led_moves_to_gpio_when_mcu_has_none():
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_W_KEY, "pins": {**GNDS, "40": "VBUS"}},
        {"ref": "J1", "piece": "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical", "pins": {"1": "VBUS", "2": "GND"}},
        {"ref": "R1", "piece": R, "pins": {"1": "VBUS", "2": "LED_A"}},
        {"ref": "D1", "piece": LED, "pins": {"1": "GND", "2": "LED_A"}},
    ], "firmware": {"commands": [{"input": "1", "high": ["GPIO0"]}, {"input": "2", "high": ["GPIO0", "GPIO1"]}]}}
    kept = _kept(d)
    live._wire_drives(d, kept)
    live._prune_commands(d)
    n = _nets(d)
    assert n["R1"]["1"] == "GPIO0" and n["U1"]["1"] == "GPIO0"
    assert d["firmware"]["commands"][1]["high"] == ["GPIO0"]  # GPIO1 drove nothing


def test_rail_led_stays_when_mcu_already_drives_something():
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_W_KEY, "pins": {**GNDS, "40": "VBUS", "20": "SIG"}},
        {"ref": "R2", "piece": R, "pins": {"1": "SIG", "2": "GND"}},
        {"ref": "R1", "piece": R, "pins": {"1": "VBUS", "2": "PWR_A"}},
        {"ref": "D1", "piece": LED, "pins": {"1": "GND", "2": "PWR_A"}},
    ]}
    assert live._wire_drives(d, _kept(d)) == []  # a power indicator is a real design


def test_button_and_buzzer_get_gpios():
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_W_KEY, "pins": {**GNDS, "36": "3V3"}},
        {"ref": "SW1", "piece": BTN, "pins": {"1": "3V3", "2": "GND"}},
        {"ref": "BZ1", "piece": BUZ, "pins": {"1": "BUZ", "2": "GND"}},
    ]}
    kept = _kept(d)
    live._wire_drives(d, kept)
    live._drop_lonely_gpio(d, kept)  # 3V3 now lands on the module alone: left open
    n = _nets(d)
    assert n["SW1"]["1"] != "3V3" and n["SW1"]["1"] in n["U1"].values()
    assert "BUZ" in n["U1"].values()
    assert not live._lonely_nets(d)


def test_lonely_battery_net_goes_to_vsys():
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_W_KEY, "pins": dict(GNDS)},
        {"ref": "BT1", "piece": COIN, "pins": {"1": "VBAT", "2": "GND"}},
    ]}
    live._wire_mcu_supply(d, _kept(d))
    assert _nets(d)["U1"]["39"] == "VBAT"



def test_lonely_module_uart_crosses_onto_pico_uart():
    # live-1792274500634 try 1: the GPS module's TxD1/RxD1 reached no MCU pin, and GP0 already drove the LED
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_W_KEY, "pins": {**GNDS, "1": "V_LED"}},
        {"ref": "J2", "piece": "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical",
         "pins": {"1": "TxD1", "2": "RxD1"}},
    ]}
    live._wire_hinted(d, _kept(d))
    u1 = _nets(d)["U1"]
    assert u1["2"] == "TxD1" and u1["11"] == "RxD1"


def test_lonely_reset_label_on_a_header_is_left_open():
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_W_KEY, "pins": {**GNDS, "20": "SIG"}},
        {"ref": "J1", "piece": "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical",
         "pins": {"1": "SIG", "2": "RUN"}},
    ]}
    live._drop_lonely_gpio(d, _kept(d))
    assert _nets(d)["J1"] == {"1": "SIG"}

# The first picker tries of the failing 10-17 runs, as saved: each must now pass the spec gate on try one.
RUNS = {
    "cretae a gps tracker like an airtag": [
        ("U1", live.PICO_W_KEY, {"3": "GND", "8": "GND", "38": "GND", "40": "VBUS"}),
        ("J1", "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical", {"1": "VBUS", "2": "GND"}),
        ("D1", LED, {"1": "K", "2": "LED_A"}),
        ("R1", R, {"1": "VBUS", "2": "LED_A"}),
        ("BT1", COIN, {"1": "VBAT", "2": "GND"}),
    ],
    "gps tracker": [
        ("U1", live.PICO_W_KEY, {"3": "GND", "38": "GND", "1": "GPS_RX", "2": "GPS_TX"}),
        ("J1", "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical", {"1": "GPS_TX", "2": "GPS_RX"}),
        ("D1", LED, {"1": "K", "2": "A"}),
    ],
    "make an airtag": [
        ("U1", live.PICO_W_KEY, {"3": "GND", "38": "GND", "20": "LED_NET"}),
        ("D1", LED, {"1": "GND", "2": "LED_NET"}),
        ("R1", R, {"1": "LED_NET", "2": "LED_NET"}),
    ],
}


@pytest.mark.parametrize("ask", sorted(RUNS))
def test_failing_tracker_tries_pass_spec_first_try(ask, tmp_path, monkeypatch):
    class Built(Exception):
        pass

    def stop(*a, **k):
        raise Built

    monkeypatch.setattr(eng, "run_build", stop)
    d = {
        "parts": [{"ref": r, "piece": k, "pins": dict(p), "group": "main"} for r, k, p in RUNS[ask]],
        "firmware": {"commands": [{"input": "1", "high": ["GPIO0"]}, {"input": "0", "high": []}]},
    }
    s = Stream("t", echo=False)
    live.assemble(s, d, [], Path(tmp_path), "", ask, _kept(d), max_iters=1)
    gate = [e for e in s.events if str(e.get("gate", "")).startswith("spec")][-1]
    assert gate["ok"], gate["value"]


def test_backwards_led_is_turned_around():
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_W_KEY, "pins": {**GNDS, "40": "VBUS"}},
        {"ref": "R1", "piece": R, "pins": {"1": "VBUS", "2": "LED1_A"}},
        {"ref": "D1", "piece": LED, "pins": {"1": "LED1_A", "2": "GND"}},
    ]}
    kept = _kept(d)
    live._fix_leds(d, kept)
    live._wire_drives(d, kept)
    n = _nets(d)
    assert n["D1"] == {"1": "GND", "2": "LED1_A"}
    assert n["R1"]["1"] in n["U1"].values() and n["R1"]["1"] != "VBUS"


def test_passive_hanging_off_a_rail_is_dropped():
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_W_KEY, "pins": {**GNDS, "40": "VBUS"}},
        {"ref": "C1", "piece": "kicad:Device:C@C_Disc_D3.0mm_W1.6mm_P2.50mm", "pins": {"1": "VBUS", "2": "GND"}},
        {"ref": "R2", "piece": R, "pins": {"1": "VBUS", "2": "ADJ"}},
    ]}
    assert live._drop_dangling_passives(d, _kept(d)) == ["R2"]
    assert [p["ref"] for p in d["parts"]] == ["U1", "C1"]


def test_shorted_button_gets_ground_and_its_pullup_net_reaches_the_mcu():
    d = {"parts": [
        {"ref": "U1", "piece": live.PICO_KEY, "pins": {**GNDS, "36": "VCC", "6": "SDA"}},
        {"ref": "R1", "piece": R, "pins": {"1": "VCC", "2": "BTN"}},
        {"ref": "SW1", "piece": BTN, "pins": {"1": "BTN", "2": "BTN"}},
    ]}
    live._wire_drives(d, _kept(d))
    n = _nets(d)
    assert n["SW1"] == {"1": "BTN", "2": "GND"}
    assert "BTN" in n["U1"].values()

"""Build parts/index.json from a curated list, keeping only entries whose symbol,
footprint and 3D model all exist in the local KiCad 10 install.

Run: uv run parts/build_index.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etchlib import symbols
from etchlib.kicad import FOOTPRINT_DIR, MODEL_DIR

R2 = {"prefix": "R", "pins": ["1", "2"]}
C2 = {"prefix": "C", "pins": ["1", "2"]}
LEDS = {"prefix": "D", "pins": ["2", "1"]}  # Device:LED pin 2 = anode, pin 1 = cathode
NPN = {"prefix": "Q", "pins": ["3", "1", "2"], "model": "2N3904"}  # C B E for Q_NPN_BEC
PNP = {"prefix": "Q", "pins": ["3", "1", "2"], "model": "2N3906"}
VIN = {"prefix": "V", "pins": ["1", "2"], "role": "power_in"}
SW = {"prefix": "S", "pins": ["1", "2"], "role": "switch"}

LED_MODELS = {
    "red": "LED_RED",
    "green": "LED_GREEN",
    "blue": "LED_BLUE",
    "yellow": "LED_YELLOW",
    "white": "LED_WHITE",
}

CURATED: list[dict] = []


def add(key, name, lib_id, footprint, category, aliases=(), value=None, spice=None, why=None):
    CURATED.append(
        {
            "key": key,
            "name": name,
            "aliases": list(aliases),
            "lib_id": lib_id,
            "footprint": footprint,
            "category": category,
            "value": value or name,
            "spice": spice,
            "why": why,
        }
    )


# Passives
for size, metric in [
    ("0402", "1005"),
    ("0603", "1608"),
    ("0805", "2012"),
    ("1206", "3216"),
]:
    add(
        f"resistor_{size}",
        f"resistor {size}",
        "Device:R",
        f"Resistor_SMD:R_{size}_{metric}Metric",
        "passive",
        [f"r {size}", f"res {size}"],
        "10k",
        R2,
        "Sets a current or a time constant.",
    )
    add(
        f"cap_{size}",
        f"capacitor {size}",
        "Device:C",
        f"Capacitor_SMD:C_{size}_{metric}Metric",
        "passive",
        [f"cap {size}", f"ceramic cap {size}", f"c {size}"],
        "100n",
        C2,
        "Stores charge for timing or decoupling.",
    )
add(
    "resistor_axial",
    "resistor axial THT",
    "Device:R",
    "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal",
    "passive",
    ["resistor tht"],
    "10k",
    R2,
)
add(
    "cap_elec_5x5",
    "electrolytic cap 5x5.3 SMD",
    "Device:C_Polarized",
    "Capacitor_SMD:CP_Elec_5x5.3",
    "passive",
    ["electrolytic smd"],
    "47u",
    C2,
)
add(
    "cap_elec_radial_5mm",
    "electrolytic cap radial 5mm",
    "Device:C_Polarized",
    "Capacitor_THT:CP_Radial_D5.0mm_P2.00mm",
    "passive",
    ["electrolytic tht"],
    "100u",
    C2,
)
add(
    "cap_tantalum_a",
    "tantalum cap case A",
    "Device:C_Polarized",
    "Capacitor_Tantalum_SMD:CP_EIA-3216-18_Kemet-A",
    "passive",
    ["tantalum"],
    "10u",
    C2,
)
add(
    "inductor_0805",
    "inductor 0805",
    "Device:L",
    "Inductor_SMD:L_0805_2012Metric",
    "passive",
    [],
    "2.2u",
)
add(
    "inductor_0603",
    "inductor 0603",
    "Device:L",
    "Inductor_SMD:L_0603_1608Metric",
    "passive",
    [],
    "1u",
)
add(
    "ferrite_0603",
    "ferrite bead 0603",
    "Device:FerriteBead_Small",
    "Inductor_SMD:L_0603_1608Metric",
    "passive",
    ["ferrite"],
    "600R@100MHz",
)
add(
    "ntc_0603",
    "NTC thermistor 0603",
    "Device:Thermistor_NTC",
    "Resistor_SMD:R_0603_1608Metric",
    "passive",
    ["thermistor"],
    "10k NTC",
)
add(
    "polyfuse_1206",
    "PTC polyfuse 1206",
    "Device:Polyfuse",
    "Fuse:Fuse_1206_3216Metric",
    "protection",
    ["fuse", "ptc"],
    "500mA",
)
add(
    "trimpot_3386p",
    "trimmer potentiometer 3386W",
    "Device:R_Potentiometer",
    "Potentiometer_THT:Potentiometer_Bourns_3386W_Horizontal",
    "passive",
    ["potentiometer", "trimpot"],
    "10k",
)

# LEDs
for color in ["red", "green", "blue", "yellow", "white"]:
    for size, metric in [("0603", "1608"), ("0805", "2012")]:
        add(
            f"led_{color}_{size}",
            f"{color} LED {size}",
            "Device:LED",
            f"LED_SMD:LED_{size}_{metric}Metric",
            "led",
            [f"{color} led", f"led {color} {size}"],
            color.upper() if color != "red" else "RED",
            dict(LEDS, model=LED_MODELS[color]),
            f"Lights up {color} when current flows through it.",
        )
add(
    "led_red_5mm",
    "red LED 5mm THT",
    "Device:LED",
    "LED_THT:LED_D5.0mm",
    "led",
    ["5mm led"],
    "RED",
    dict(LEDS, model="LED_RED"),
)
add(
    "led_red_3mm",
    "red LED 3mm THT",
    "Device:LED",
    "LED_THT:LED_D3.0mm",
    "led",
    ["3mm led"],
    "RED",
    dict(LEDS, model="LED_RED"),
)
add(
    "ws2812b",
    "WS2812B RGB LED",
    "LED:WS2812B",
    "LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
    "led",
    ["neopixel", "addressable led"],
)

# Transistors
add(
    "mmbt3904",
    "2N3904 SOT-23",
    "Transistor_BJT:MMBT3904",
    "Package_TO_SOT_SMD:SOT-23",
    "transistor",
    ["2n3904", "mmbt3904", "npn sot-23", "npn"],
    "MMBT3904",
    NPN,
    "Switches current on and off.",
)
add(
    "mmbt3906",
    "2N3906 SOT-23",
    "Transistor_BJT:MMBT3906",
    "Package_TO_SOT_SMD:SOT-23",
    "transistor",
    ["2n3906", "pnp sot-23", "pnp"],
    "MMBT3906",
    PNP,
)
add(
    "bc847",
    "BC847 SOT-23",
    "Transistor_BJT:BC847",
    "Package_TO_SOT_SMD:SOT-23",
    "transistor",
    ["bc847"],
    "BC847",
    NPN,
)
add(
    "bc857",
    "BC857 SOT-23",
    "Transistor_BJT:BC857",
    "Package_TO_SOT_SMD:SOT-23",
    "transistor",
    ["bc857"],
    "BC857",
    PNP,
)
add(
    "2n3904_to92",
    "2N3904 TO-92",
    "Transistor_BJT:2N3904",
    "Package_TO_SOT_THT:TO-92_Inline",
    "transistor",
    ["2n3904 tht"],
    "2N3904",
)
add(
    "2n3906_to92",
    "2N3906 TO-92",
    "Transistor_BJT:2N3906",
    "Package_TO_SOT_THT:TO-92_Inline",
    "transistor",
    ["2n3906 tht", "pnp tht"],
    "2N3906",
)
add(
    "2n7002",
    "2N7002 N-MOSFET SOT-23",
    "Transistor_FET:2N7002",
    "Package_TO_SOT_SMD:SOT-23",
    "transistor",
    ["2n7002", "nmos", "n-mosfet"],
)
add(
    "bss138",
    "BSS138 N-MOSFET SOT-23",
    "Transistor_FET:BSS138",
    "Package_TO_SOT_SMD:SOT-23",
    "transistor",
    ["bss138", "level shifter fet"],
)
add(
    "ao3401a",
    "AO3401A P-MOSFET SOT-23",
    "Transistor_FET:AO3401A",
    "Package_TO_SOT_SMD:SOT-23",
    "transistor",
    ["ao3401", "pmos", "p-mosfet"],
)
add(
    "irlz44n",
    "IRLZ44N MOSFET TO-220",
    "Transistor_FET:IRLZ44N",
    "Package_TO_SOT_THT:TO-220-3_Vertical",
    "transistor",
    ["irlz44n", "power mosfet"],
)

# Diodes
add(
    "1n4148w",
    "1N4148W SOD-123",
    "Diode:1N4148W",
    "Diode_SMD:D_SOD-123",
    "diode",
    ["1n4148", "signal diode"],
    spice={"prefix": "D", "pins": ["2", "1"], "model": "D1N4148"},
)
add(
    "1n4148_tht",
    "1N4148 DO-35",
    "Diode:1N4148",
    "Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal",
    "diode",
    ["1n4148 tht"],
    spice={"prefix": "D", "pins": ["2", "1"], "model": "D1N4148"},
)
add(
    "ss14",
    "SS14 Schottky SMA",
    "Diode:SS14",
    "Diode_SMD:D_SMA",
    "diode",
    ["ss14", "schottky"],
    spice={"prefix": "D", "pins": ["2", "1"], "model": "SS14"},
)
add(
    "1n5819ws",
    "1N5819WS Schottky SOD-323",
    "Diode:1N5819WS",
    "Diode_SMD:D_SOD-323",
    "diode",
    ["b5819w"],
    spice={"prefix": "D", "pins": ["2", "1"], "model": "D1N5819"},
)
add(
    "1n5819",
    "1N5819 Schottky DO-41",
    "Diode:1N5819",
    "Diode_THT:D_DO-41_SOD81_P10.16mm_Horizontal",
    "diode",
    ["1n5819"],
    spice={"prefix": "D", "pins": ["2", "1"], "model": "D1N5819"},
)
add(
    "bat54a",
    "BAT54A dual Schottky SOT-23",
    "Diode:BAT54A",
    "Package_TO_SOT_SMD:SOT-23",
    "diode",
    ["bat54"],
)
add(
    "1n4007",
    "1N4007 rectifier DO-41",
    "Diode:1N4007",
    "Diode_THT:D_DO-41_SOD81_P10.16mm_Horizontal",
    "diode",
    ["1n4007", "rectifier"],
    spice={"prefix": "D", "pins": ["2", "1"], "model": "D1N4007"},
)

# ICs
add(
    "ne555_soic8",
    "NE555 SOIC-8",
    "Timer:NE555D",
    "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    "ic",
    ["ne555", "555 timer", "555"],
    "NE555",
)
add(
    "ne555_dip8",
    "NE555 DIP-8",
    "Timer:NE555P",
    "Package_DIP:DIP-8_W7.62mm",
    "ic",
    ["ne555 dip"],
    "NE555",
)
add(
    "lm358_soic8",
    "LM358 dual op-amp SOIC-8",
    "Amplifier_Operational:LM358",
    "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    "ic",
    ["lm358", "op amp"],
)
add(
    "lmv321_sot235",
    "LMV321 op-amp SOT-23-5",
    "Amplifier_Operational:LMV321",
    "Package_TO_SOT_SMD:SOT-23-5",
    "ic",
    ["lmv321"],
)
add(
    "attiny85_soic8",
    "ATtiny85 SOIC-8",
    "MCU_Microchip_ATtiny:ATtiny85-20S",
    "Package_SO:SOIC-8_5.3x5.3mm_P1.27mm",
    "mcu",
    ["attiny85"],
)
add(
    "atmega328p_tqfp32",
    "ATmega328P TQFP-32",
    "MCU_Microchip_ATmega:ATmega328P-A",
    "Package_QFP:TQFP-32_7x7mm_P0.8mm",
    "mcu",
    ["atmega328p", "arduino chip"],
)
add(
    "rp2040",
    "RP2040 QFN-56",
    "MCU_RaspberryPi:RP2040",
    "Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm",
    "mcu",
    ["rp2040"],
)
add(
    "stm32f030f4",
    "STM32F030F4P6 TSSOP-20",
    "MCU_ST_STM32F0:STM32F030F4Px",
    "Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm",
    "mcu",
    ["stm32f030"],
)
add(
    "ch340c",
    "CH340C USB-UART SOIC-16",
    "Interface_USB:CH340C",
    "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
    "ic",
    ["ch340", "usb uart"],
)
add(
    "cd4017_soic16",
    "CD4017 decade counter SOIC-16",
    "4xxx:4017",
    "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
    "ic",
    ["4017", "decade counter"],
)
add(
    "74hc595_soic16",
    "74HC595 shift register SOIC-16",
    "74xx:74HC595",
    "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
    "ic",
    ["74hc595", "shift register"],
)
add(
    "pc817",
    "PC817 optocoupler DIP-4",
    "Isolator:PC817",
    "Package_DIP:DIP-4_W7.62mm",
    "ic",
    ["optocoupler"],
)
add(
    "bme280",
    "BME280 sensor LGA-8",
    "Sensor:BME280",
    "Package_LGA:Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering",
    "sensor",
    ["bme280", "humidity sensor", "pressure sensor"],
)
add(
    "mpu6050",
    "MPU-6050 IMU QFN-24",
    "Sensor_Motion:MPU-6050",
    "Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.7x2.6mm",
    "sensor",
    ["mpu6050", "imu"],
)

# Regulators and power
add(
    "ams1117_33",
    "AMS1117-3.3 SOT-223",
    "Regulator_Linear:AMS1117-3.3",
    "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
    "power",
    ["ams1117", "3.3v regulator", "ldo"],
)
add(
    "ap2112k_33",
    "AP2112K-3.3 SOT-23-5",
    "Regulator_Linear:AP2112K-3.3",
    "Package_TO_SOT_SMD:SOT-23-5",
    "power",
    ["ap2112"],
)
add(
    "mcp1700_33",
    "MCP1700-3.3 SOT-23",
    "Regulator_Linear:MCP1700x-330xxTT",
    "Package_TO_SOT_SMD:SOT-23",
    "power",
    ["mcp1700"],
)
add(
    "xc6206_33",
    "XC6206P332MR SOT-23",
    "Regulator_Linear:XC6206PxxxMR",
    "Package_TO_SOT_SMD:SOT-23",
    "power",
    ["xc6206"],
)
add(
    "l7805_to220",
    "L7805 5V regulator TO-220",
    "Regulator_Linear:L7805",
    "Package_TO_SOT_THT:TO-220-3_Vertical",
    "power",
    ["7805", "lm7805"],
)
add(
    "mcp73831",
    "MCP73831 Li-ion charger SOT-23-5",
    "Battery_Management:MCP73831-2-OT",
    "Package_TO_SOT_SMD:SOT-23-5",
    "power",
    ["lipo charger", "mcp73831"],
)

# Modules. KiCad 10 ships no nRF54L15 symbol or footprint, so find_part falls through
# to the easyeda2kicad hook for it.
add(
    "esp32_wroom_32",
    "ESP32-WROOM-32 module",
    "RF_Module:ESP32-WROOM-32",
    "RF_Module:ESP32-WROOM-32",
    "module",
    ["esp32", "wifi module"],
)
add(
    "esp32_c3_wroom_02",
    "ESP32-C3-WROOM-02 module",
    "RF_Module:ESP32-C3-WROOM-02",
    "RF_Module:ESP32-C3-WROOM-02",
    "module",
    ["esp32-c3"],
)
add(
    "nrf54l15_module",  # kept so the rejection is on record
    "nRF54L15 module",
    "RF_Module:nRF54L15",
    "RF_Module:nRF54L15",
    "module",
    ["nrf54l15", "nrf54"],
)
add(
    "mdbt50q_nrf52840",
    "nRF52840 module Raytac MDBT50Q",
    "RF_Module:MDBT50Q-1MV2",
    "RF_Module:Raytac_MDBT50Q",
    "module",
    ["nrf52840", "nrf52"],
)

# Connectors
for n in range(2, 7):
    add(
        f"jst_ph_{n}",
        f"JST-PH {n}-pin vertical",
        f"Connector_Generic:Conn_01x0{n}",
        f"Connector_JST:JST_PH_B{n}B-PH-K_1x0{n}_P2.00mm_Vertical",
        "connector",
        [f"jst ph {n}", f"jst-ph {n}-pin", f"jst {n} pin"]
        + (["battery connector", "lipo connector"] if n == 2 else []),
        f"JST PH {n}p",
        VIN if n == 2 else None,
        "Brings power in from a battery or USB lead." if n == 2 else None,
    )
add(
    "jst_ph_2_ra",
    "JST-PH 2-pin right angle THT",
    "Connector_Generic:Conn_01x02",
    "Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal",
    "connector",
    ["jst ph smd"],
    "JST PH 2p",
    VIN,
)
add(
    "jst_xh_2",
    "JST-XH 2-pin vertical",
    "Connector_Generic:Conn_01x02",
    "Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical",
    "connector",
    ["jst xh"],
    "JST XH 2p",
    VIN,
)
add(
    "jst_sh_4_qwiic",
    "JST-SH 4-pin (Qwiic)",
    "Connector_Generic:Conn_01x04",
    "Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal",
    "connector",
    ["qwiic", "stemma qt"],
)
add(
    "usb_c_power_6p",
    "USB-C receptacle power only 6P",
    "Connector:USB_C_Receptacle_PowerOnly_6P",
    "Connector_USB:USB_C_Receptacle_GCT_USB4125-xx-x-0190_6P_TopMnt_Horizontal",
    "connector",
    ["usb-c power", "usb c power"],
    "USB-C",
)
add(
    "usb_c_16p",
    "USB-C receptacle USB 2.0 16P",
    "Connector:USB_C_Receptacle_USB2.0_16P",
    "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
    "connector",
    ["usb-c", "usb c receptacle"],
    "USB-C",
)
add(
    "usb_micro_b",
    "Micro-USB B receptacle",
    "Connector:USB_B_Micro",
    "Connector_USB:USB_Micro-B_Molex_47346-0001",
    "connector",
    ["micro usb"],
)
for n in range(2, 9):
    add(
        f"header_1x{n:02d}",
        f"pin header 1x{n:02d} 2.54mm",
        f"Connector_Generic:Conn_01x{n:02d}",
        f"Connector_PinHeader_2.54mm:PinHeader_1x{n:02d}_P2.54mm_Vertical",
        "connector",
        [f"header {n} pin", f"1x{n} header"],
        f"1x{n:02d}",
        VIN if n == 2 else None,
    )
add(
    "header_2x03_isp",
    "AVR ISP header 2x03",
    "Connector_Generic:Conn_02x03_Odd_Even",
    "Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical",
    "connector",
    ["isp header"],
)
add(
    "header_2x05_swd",
    "SWD header 2x05 1.27mm",
    "Connector_Generic:Conn_02x05_Odd_Even",
    "Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical",
    "connector",
    ["swd header", "cortex debug"],
)
add(
    "screw_terminal_2",
    "screw terminal 2-pin 5mm",
    "Connector:Screw_Terminal_01x02",
    "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2_1x02_P5.00mm_Horizontal",
    "connector",
    ["screw terminal"],
    "2p",
    VIN,
)
add(
    "barrel_jack",
    "DC barrel jack",
    "Connector:Barrel_Jack",
    "Connector_BarrelJack:BarrelJack_Horizontal",
    "connector",
    ["barrel jack", "dc jack"],
)
add(
    "cr2032_holder",
    "CR2032 coin cell holder",
    "Device:Battery_Cell",
    "Battery:BatteryHolder_Keystone_3034_1x20mm",
    "power",
    ["coin cell", "cr2032"],
    "CR2032",
    VIN,
)

# Switches
add(
    "tact_6mm_tht",
    "tactile switch 6mm THT",
    "Switch:SW_Push",
    "Button_Switch_THT:SW_PUSH_6mm",
    "switch",
    ["tactile switch", "pushbutton", "push button", "button"],
    "SW_Push",
    SW,
    "A button you press to close the circuit.",
)
add(
    "tact_smd_tl3342",
    "tactile switch SMD TL3342",
    "Switch:SW_Push",
    "Button_Switch_SMD:SW_SPST_TL3342",
    "switch",
    ["smd button"],
    "SW_Push",
    SW,
)
add(
    "slide_spdt_pcm12",
    "slide switch SPDT PCM12",
    "Switch:SW_SPDT",
    "Button_Switch_SMD:SW_SPDT_PCM12",
    "switch",
    ["slide switch", "power switch"],
)

# Timing, sound, misc
add(
    "crystal_3225",
    "crystal 3225 4-pin",
    "Device:Crystal_GND24",
    "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
    "timing",
    ["crystal", "16mhz crystal"],
    "16MHz",
)
add(
    "crystal_hc49",
    "crystal HC-49/US THT",
    "Device:Crystal",
    "Crystal:Crystal_HC49-U_Vertical",
    "timing",
    ["hc49"],
    "16MHz",
)
add(
    "buzzer_12mm",
    "buzzer 12mm",
    "Device:Buzzer",
    "Buzzer_Beeper:Buzzer_12x9.5RM7.6",
    "sound",
    ["buzzer", "beeper"],
)
add(
    "mounting_hole_m3",
    "M3 mounting hole",
    "Mechanical:MountingHole",
    "MountingHole:MountingHole_3.2mm_M3",
    "mechanical",
    ["mounting hole"],
)
add(
    "testpoint_pad",
    "test point pad 1mm",
    "Connector:TestPoint",
    "TestPoint:TestPoint_Pad_D1.0mm",
    "mechanical",
    ["test point"],
)

MODEL_RE = re.compile(r'\(model\s+"([^"]+)"')


def resolve_model(fp_text: str) -> str | None:
    m = MODEL_RE.search(fp_text)
    if not m:
        return None
    path = re.sub(r"\$\{KICAD\d*_3DMODEL_DIR\}", str(MODEL_DIR), m.group(1))
    path = re.sub(r"\.wrl$", ".step", path)
    return path


def verify(entry: dict) -> tuple[dict | None, str]:
    if not symbols.exists(entry["lib_id"]):
        return None, f"symbol {entry['lib_id']} missing"
    lib, name = entry["footprint"].split(":", 1)
    fp = FOOTPRINT_DIR / f"{lib}.pretty" / f"{name}.kicad_mod"
    if not fp.exists():
        return None, f"footprint {entry['footprint']} missing"
    model = resolve_model(fp.read_text())
    if model is None and entry["category"] == "mechanical":
        pass  # holes and test pads have no body to render
    elif model is None or not Path(model).exists():
        return None, f"3D model missing ({model})"
    out = dict(entry)
    out["model"] = model
    out["pins"] = sorted({p.number for p in symbols.pins(entry["lib_id"])}, key=lambda s: (len(s), s))
    out["source"] = "kicad-lib"
    return out, "ok"


def main() -> None:
    ok, bad = [], []
    for e in CURATED:
        v, why = verify(e)
        (ok.append(v) if v else bad.append({"key": e["key"], "reason": why}))
    dest = Path(__file__).resolve().parent / "index.json"
    dest.write_text(
        json.dumps(
            {"kicad": "10.0.6", "count": len(ok), "parts": ok, "rejected": bad},
            indent=1,
        )
    )
    print(f"verified {len(ok)} / {len(CURATED)}")
    for b in bad:
        print("  rejected", b["key"], "-", b["reason"])


if __name__ == "__main__":
    main()

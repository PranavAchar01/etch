"""Reusable circuit blocks wired by code, the way the tracker fix wires a radio: every block is a small, fixed piece
of circuit (an LED and its resistor on a GPIO, a button with its pull-up, a buzzer on a transistor, USB-C power, a
LiPo charger, an I2C sensor socket, ...) that asks the microcontroller block for the pins it needs. compose() lays
the blocks onto one Raspberry Pi Pico (Pico W when the board is wireless), hands out pins (I2C0, UART0/1, SPI0 and
the ADC pins first, then free GPIO) and writes the firmware table, so the whole board comes out of code with no
model call and no lonely nets.

Rails: everything runs from the Pico's own rails. VBUS (pin 40, the module's 5 V output) feeds 5 V parts, 3V3 (pin
36) feeds 3.3 V parts and GND pin 3 drives ground. Batteries and the USB-C jack reach VBUS through a Schottky diode
(an OR with the Pico's own USB, which has its own diode). VSYS (pin 39) is a power INPUT on the KiCad symbol and
fails ERC when only a connector or a diode feeds it (crash-trends 10-17: 9 of 15 ERC errors), so no block uses it.

Modules (GPS, sensors, amplifiers, SD cards, displays) sit on pin sockets with the breakout's pin order, as the
OLED socket in agent/recipes.py does: every connector pin is passive in ERC and the 2.54 mm pitch routes cleanly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import config

# ------------------------------------------------------------------ pieces (all through-hole or 2.54 mm modules)
PICO = "kicad:MCU_Module:RaspberryPi_Pico@RaspberryPi_Pico_Common_THT"
PICO_W = "kicad:MCU_Module:RaspberryPi_Pico_W@RaspberryPi_Pico_Common_THT"
R = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
C = "kicad:Device:C@C_Disc_D5.0mm_W2.5mm_P2.50mm"
CP = "kicad:Device:C_Polarized@CP_Radial_D5.0mm_P2.00mm"
LED = "kicad:Device:LED@LED_D5.0mm"
RGB = "kicad:Device:LED_RKGB@LED_D5.0mm-4_RGB_Wide_Pins"
SW = "kicad:Switch:SW_Push@SW_PUSH_6mm"
MX = "kicad:Switch:SW_Push@MX-Hotswap-1U"
SIGNAL_DIODE = "kicad:Diode:1N4148@D_DO-35_SOD27_P7.62mm_Horizontal"
SCHOTTKY = "kicad:Diode:1N5817@D_DO-41_SOD81_P10.16mm_Horizontal"
FLYBACK = "kicad:Diode:1N4001@D_DO-41_SOD81_P10.16mm_Horizontal"
NPN = "kicad:Transistor_BJT:2N3904@TO-92_Inline"  # 1 E, 2 B, 3 C
BUZZ = "kicad:Device:Buzzer@Buzzer_12x9.5RM7.6"
USBC = "kicad:Connector:USB_C_Receptacle_PowerOnly_6P@USB_C_Receptacle_GCT_USB4125-xx-x-0190_6P_TopMnt_Horizontal"
# generic Connector library: the curated Connector_Generic 2-pin piece is a power input, and the analog pass
# grounds its pin 2, which would short a LiPo through its protection FET or a speaker's minus terminal
JST2 = "kicad:Connector:Conn_01x02_Socket@JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical"
COIN = "kicad:Device:Battery_Cell@BatteryHolder_Keystone_1058_1x2032"
TERM2 = "kicad:Connector_Generic:Conn_01x02@TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"
HDR3 = "kicad:Connector_Generic:Conn_01x03@PinHeader_1x03_P2.54mm_Vertical"
MOTOR = "kicad:Motor:Motor_DC@PinHeader_1x02_P2.54mm_Vertical"
OLED = "kicad:Connector_Generic:Conn_01x06@PinSocket_1x06_P2.54mm_Vertical"  # VIN 3V3 GND RST SCL SDA
MIC = (
    "kicad:Connector_Generic:Conn_01x03@PinSocket_1x03_P2.54mm_Vertical"  # VCC GND OUT
)
DHT = "kicad:Sensor:DHT11@Aosong_DHT11_5.5x12.0_P2.54mm"
NTC = "kicad:Device:Thermistor_NTC@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
LDR = "kicad:Sensor_Optical:A1050@R_LDR_D6.4mm_P3.4mm_Vertical"
POT = "kicad:Device:R_Potentiometer_Trim@Potentiometer_Bourns_3296W_Vertical"
EC11 = "kicad:Device:RotaryEncoder_Switch@ROT_Alps_EC11E-Switch"
WS2812B = "kicad:LED:WS2812B@LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm"
TSOP = "kicad:Interface_Optical:TSOP382xx@Vishay_MINICAST-3Pin"
NRF_HDR = "kicad:Connector_Generic:Conn_02x04_Odd_Even@PinSocket_2x04_P2.54mm_Vertical"


def SOCK(n: int) -> str:
    """A 1xN female header for a breakout module (generic Connector library, so no curated family attaches)."""
    return (
        f"kicad:Connector:Conn_01x{n:02d}_Socket@PinSocket_1x{n:02d}_P2.54mm_Vertical"
    )


# ------------------------------------------------------------------ Pico pin map (physical pin numbers)
GND_PIN, V3_PIN, VBUS_PIN = "3", "36", "40"
I2C0 = {"SDA": "6", "SCL": "7"}  # GP4 / GP5, the MicroPython default and live._PICO_I2C
UARTS = [("1", "2"), ("11", "12")]  # (TX, RX): UART0 GP0/GP1, UART1 GP8/GP9
SPI0 = {
    "MISO": "21",
    "SCK": "24",
    "MOSI": "25",
    "CS0": "22",
}  # GP16 / GP18 / GP19, CS GP17
ADC = ["31", "32", "34"]  # GP26 / GP27 / GP28
GPIO_ORDER = [
    "4", "5", "9", "10", "14", "15", "16", "17", "19", "20", "26", "27", "29",  # plain GPIO
    "11", "12", "1", "2", "6", "7", "21", "22", "24", "25",  # bus pins when their bus is unused
    "31", "32", "34",  # ADC pins as digital last
]  # fmt: skip

COLOURS = ("red", "green", "yellow", "orange", "white", "blue")


class BlockError(ValueError):
    """The blocks do not fit one Pico (too many pins or analog inputs, unknown block or option)."""


@dataclass
class Board:
    ask: str = ""
    wireless: bool = False
    parts: list[dict[str, Any]] = field(default_factory=list)
    needs: list[tuple[str, str]] = field(
        default_factory=list
    )  # (net, kind): gpio / adc / sda / scl / tx0 ...
    inputs: list[str] = field(default_factory=list)
    leds: list[str] = field(
        default_factory=list
    )  # GPIO nets that light an LED through its resistor
    drives: list[tuple[str, str]] = field(
        default_factory=list
    )  # (GPIO net, what it does) for other outputs
    say: list[str] = field(default_factory=list)
    used: dict[str, int] = field(default_factory=lambda: {"U": 1})  # U1 is the Pico
    uarts: int = 0
    spi_cs: int = 0
    i2c: bool = False

    # -------------------------------------------------------------- helpers
    def ref(self, prefix: str) -> str:
        self.used[prefix] = self.used.get(prefix, 0) + 1
        return f"{prefix}{self.used[prefix]}"

    def add(
        self,
        prefix: str,
        piece: str,
        pins: dict[str, str],
        group: str,
        why: str,
        value: str | None = None,
    ) -> str:
        ref = self.ref(prefix)
        p: dict[str, Any] = {
            "ref": ref,
            "piece": piece,
            "pins": pins,
            "group": group,
            "why": why,
        }
        if value:
            p["value"] = value
        self.parts.append(p)
        return ref

    def net(self, name: str) -> str:
        """A net name not used yet on this board (BTN, BTN2, BTN3 ...)."""
        taken = {n for (n, _) in self.needs} | {
            str(v) for p in self.parts for v in p["pins"].values()
        }
        if name not in taken:
            return name
        i = 2
        while f"{name}{i}" in taken:
            i += 1
        return f"{name}{i}"

    def gpio(self, name: str, kind: str = "gpio") -> str:
        n = self.net(name)
        self.needs.append((n, kind))
        return n

    def bus_i2c(self) -> tuple[str, str]:
        if not self.i2c:
            self.i2c = True
            self.needs += [("SDA", "sda"), ("SCL", "scl")]
        return "SDA", "SCL"

    def bus_uart(self, name: str) -> tuple[str, str]:
        """(net the module transmits on, net it receives on): module TX -> Pico RX and the other way round."""
        if self.uarts >= len(UARTS):
            raise BlockError("more than two serial modules on one Pico")
        i = self.uarts
        self.uarts += 1
        tx, rx = self.net(f"{name}_TX"), self.net(f"{name}_RX")
        self.needs += [(tx, f"rx{i}"), (rx, f"tx{i}")]
        return tx, rx

    def bus_spi(self, name: str) -> tuple[str, str, str, str]:
        """(MISO, MOSI, SCK, this module's CS)."""
        if not any(k == "sck" for _, k in self.needs):
            self.needs += [("MISO", "miso"), ("MOSI", "mosi"), ("SCK", "sck")]
        cs = self.net(f"{name}_CS")
        self.needs.append((cs, "cs0" if self.spi_cs == 0 else "gpio"))
        self.spi_cs += 1
        return "MISO", "MOSI", "SCK", cs

    # -------------------------------------------------------------- pin allocation + MCU part
    def mcu(self) -> dict[str, Any]:
        pins: dict[str, str] = {GND_PIN: "GND", "38": "GND"}
        wired = {str(v) for p in self.parts for v in p["pins"].values()}
        self.needs = [(n, k) for n, k in self.needs if n in wired]  # e.g. MISO when only write-only SPI parts
        fixed = {"sda": I2C0["SDA"], "scl": I2C0["SCL"], "miso": SPI0["MISO"], "mosi": SPI0["MOSI"],
                 "sck": SPI0["SCK"], "cs0": SPI0["CS0"], "tx0": UARTS[0][0], "rx0": UARTS[0][1],
                 "tx1": UARTS[1][0], "rx1": UARTS[1][1]}  # fmt: skip
        for net, kind in self.needs:
            if kind in fixed:
                pins[fixed[kind]] = net
        adc = [p for p in ADC if p not in pins]
        for net, kind in self.needs:
            if kind == "adc":
                if not adc:
                    raise BlockError(
                        "more than three analog inputs on one Pico (it has three ADC pins)"
                    )
                pins[adc.pop(0)] = net
        free = [p for p in GPIO_ORDER if p not in pins]
        for net, kind in self.needs:
            if kind in ("gpio", "in", "out"):
                if not free:
                    raise BlockError("the blocks need more GPIO pins than one Pico has")
                pins[free.pop(0)] = net
        nets = {str(v) for p in self.parts for v in p["pins"].values()}
        if "P3V3" in nets:
            pins[V3_PIN] = "P3V3"
        if "VBUS" in nets:
            pins[VBUS_PIN] = "VBUS"
        return {
            "ref": "U1",
            "piece": PICO_W if self.wireless else PICO,
            "pins": pins,
            "group": "power",
            "why": "Raspberry Pi Pico"
            + (" W: Wi-Fi and Bluetooth on the module" if self.wireless else "")
            + ": USB power, the 3.3 V rail and the program that runs every block.",
        }


# ------------------------------------------------------------------ the blocks
def status_led(b: Board, colour: str = "green", label: str = "status") -> None:
    """LED + resistor on a GPIO: GPIO -> resistor -> anode, cathode to GND."""
    colour = colour if colour in COLOURS else "green"
    n = b.gpio("LED_" + label.upper().replace(" ", "_")[:8])
    a = b.net(n + "_A")
    ohms = "68" if colour in ("blue", "white") else "220"
    b.add(
        "R",
        R,
        {"1": n, "2": a},
        "lights",
        f"Sets the {label} LED current from the 3.3 V pin.",
        ohms,
    )
    b.add(
        "D",
        LED,
        {"2": a, "1": "GND"},
        "lights",
        f"{colour.title()} {label} LED.",
        colour,
    )
    b.leds.append(n)


def leds(b: Board, n: int = 3, colours: str = "red,yellow,green") -> None:
    cs = [c.strip() for c in colours.split(",") if c.strip()] or ["red"]
    for i in range(int(n)):
        status_led(b, cs[i % len(cs)], f"light {i + 1}")


def rgb_led(b: Board) -> None:
    nets = [b.gpio(f"LED_{c}") for c in "RGB"]
    an = [b.net(f"{x}_A") for x in nets]
    for net, a, c, ohms in zip(nets, an, ("red", "green", "blue"), ("150", "68", "47")):
        b.add(
            "R", R, {"1": net, "2": a}, "lights", f"Sets the {c} die's current.", ohms
        )
    b.add(
        "D",
        RGB,
        {"1": an[0], "2": "GND", "3": an[1], "4": an[2]},
        "lights",
        "RGB LED: any colour.",
        "RGB",
    )
    b.leds += nets


def button(b: Board, n: int = 1, label: str = "button") -> None:
    """Push button from a GPIO to GND with a 10k pull-up to 3.3 V: reads 1 released, 0 pressed."""
    for _ in range(int(n)):
        net = b.gpio("BTN", "in")
        b.add(
            "R",
            R,
            {"1": "P3V3", "2": net},
            "controls",
            "Pull-up: the input reads high until the button is pressed.",
            "10k",
        )
        b.add(
            "SW",
            SW,
            {"1": net, "2": "GND"},
            "controls",
            f"{label.capitalize()}: pulls its GPIO low when pressed.",
        )
        b.inputs.append(net)


def buzzer(b: Board) -> None:
    """GPIO -> 4.7k -> 2N3904 base; the transistor sinks the buzzer from 3.3 V (a GPIO cannot drive it)."""
    net = b.gpio("BUZ", "out")
    q = b.ref("Q")
    base, low = b.net(f"{q}_B"), b.net("BZ_N")
    b.add(
        "R",
        R,
        {"1": net, "2": base},
        "sound",
        "Base resistor: about 0.5 mA from the GPIO.",
        "4.7k",
    )
    b.parts.append({"ref": q, "piece": NPN, "pins": {"1": "GND", "2": base, "3": low}, "group": "sound",
                    "why": "Switches the buzzer current."})  # fmt: skip
    b.add("BZ", BUZZ, {"1": "P3V3", "2": low}, "sound", "Beeps and plays alert tones.")
    b.drives.append((net, "beeps"))


def usb_c(b: Board) -> None:
    """USB-C power input: 5.1k on CC1/CC2 asks the charger for 5 V; a Schottky ORs it onto the Pico's VBUS."""
    vin = b.net("USB_5V")
    j = b.add("J", USBC, {"A9": vin, "B9": vin, "A12": "GND", "B12": "GND", "A5": b.net("CC1"), "B5": b.net("CC2")},
              "power", "USB-C power in.")  # fmt: skip
    pins = b.parts[-1]["pins"]
    b.add(
        "R",
        R,
        {"1": pins["A5"], "2": "GND"},
        "power",
        "5.1k on CC1: marks the board as a USB-C power sink.",
        "5.1k",
    )
    b.add(
        "R",
        R,
        {"1": pins["B5"], "2": "GND"},
        "power",
        "5.1k on CC2, for either plug orientation.",
        "5.1k",
    )
    b.add("D", SCHOTTKY, {"2": vin, "1": "VBUS"}, "power", f"Schottky from {j} onto VBUS: either USB port powers the board.", "1N5817")  # fmt: skip
    b.add(
        "C",
        CP,
        {"1": "VBUS", "2": "GND"},
        "power",
        "Bulk capacitor on the 5 V rail.",
        "47u",
    )


def lipo(b: Board) -> None:
    """LiPo cell + TP4056 charger module (with protection): charges from VBUS, its output reaches VBUS through a
    Schottky. Socket order follows the common 6-pad module: IN+, IN-, B+, B-, OUT+, OUT-."""
    bp, bn, out = b.net("BAT_P"), b.net("BAT_N"), b.net("BAT_OUT")
    b.add("J", SOCK(6), {"1": "VBUS", "2": "GND", "3": bp, "4": bn, "5": out, "6": "GND"}, "power",
          "TP4056 LiPo charger module (with DW01 protection): charges the cell from USB.", "TP4056 module")  # fmt: skip
    b.add(
        "J",
        JST2,
        {"1": bp, "2": bn},
        "power",
        "JST-PH socket for a 3.7 V LiPo cell.",
        "LiPo 3.7V",
    )
    b.add("D", SCHOTTKY, {"2": out, "1": "VBUS"}, "power", "Schottky: the battery runs the board when USB is out.", "1N5817")  # fmt: skip
    b.say.append("a rechargeable LiPo with a TP4056 charger")


def coin_cell(b: Board) -> None:
    v = b.net("VBAT")
    b.add("BT", COIN, {"1": v, "2": "GND"}, "power", "CR2032 coin cell.", "CR2032")
    b.add(
        "D",
        SCHOTTKY,
        {"2": v, "1": "VBUS"},
        "power",
        "Schottky: the cell runs the board when USB is out.",
        "1N5817",
    )
    b.say.append("a CR2032 coin cell")


def battery_pack(b: Board) -> None:
    v = b.net("VBAT")
    b.add(
        "J",
        TERM2,
        {"1": v, "2": "GND"},
        "power",
        "Screw terminal for a 3xAA pack (4.5 V).",
        "3xAA",
    )
    b.add(
        "D",
        SCHOTTKY,
        {"2": v, "1": "VBUS"},
        "power",
        "Schottky: the pack runs the board when USB is out.",
        "1N5817",
    )
    b.say.append("an AA battery pack")


I2C_MODULES = {
    "bme280": "BME280 temperature, humidity and pressure",
    "sht31": "SHT31 temperature and humidity",
    "mpu6050": "MPU-6050 accelerometer and gyro",
    "bno055": "BNO055 9-axis orientation",
    "lis3dh": "LIS3DH accelerometer",
    "qmc5883": "QMC5883L compass",
    "ds3231": "DS3231 real-time clock",
    "max30102": "MAX30102 heart-rate and SpO2",
    "bh1750": "BH1750 light level",
    "veml6075": "VEML6075 UV index",
    "tcs34725": "TCS34725 colour",
    "scd40": "SCD40 CO2",
    "sgp30": "SGP30 air quality (TVOC / eCO2)",
    "vl53l0x": "VL53L0X laser distance",
    "apds9960": "APDS9960 gesture and proximity",
    "ina219": "INA219 current and voltage",
    "pn532": "PN532 NFC reader",
    "mpr121": "MPR121 12-key capacitive touch",
    "ads1115": "ADS1115 4-channel ADC",
    "mlx90614": "MLX90614 infrared thermometer",
    "pcf8574": "PCF8574 I/O expander",
    "drv2605": "DRV2605 haptic driver",
    "pmsa003i": "PMSA003I particulate (PM2.5)",
    "as7341": "AS7341 spectral",
    "si7021": "Si7021 humidity",
    "tmp117": "TMP117 precision temperature",
    "lsm6ds3": "LSM6DS3 step-counting IMU",
}


def i2c_sensor(b: Board, chip: str = "bme280") -> None:
    """Breakout on the shared I2C0 bus: VCC GND SCL SDA (the usual 4-pin order; the breakout has its pull-ups)."""
    if chip not in I2C_MODULES:
        raise BlockError(f"unknown I2C module {chip}")
    sda, scl = b.bus_i2c()
    b.add("J", SOCK(4), {"1": "P3V3", "2": "GND", "3": scl, "4": sda}, "sensors",
          f"{I2C_MODULES[chip]} breakout on I2C0 (GP4/GP5).", f"{chip.upper()} module")  # fmt: skip


def display(b: Board, kind: str = "oled") -> None:
    if kind == "oled":
        sda, scl = b.bus_i2c()
        b.add("J", OLED, {"1": "P3V3", "3": "GND", "5": scl, "6": sda}, "display",
              "SSD1306 OLED module on I2C0 (GP4/GP5).", "SSD1306 OLED")  # fmt: skip
    elif kind in ("tft", "epaper"):
        miso, mosi, sck, cs = b.bus_spi("DISP")
        dc, rst = b.gpio("DISP_DC", "out"), b.gpio("DISP_RST", "out")
        name = "ST7789 1.3in colour TFT" if kind == "tft" else "2.13in e-paper"
        b.add("J", SOCK(8), {"1": "P3V3", "2": "GND", "3": sck, "4": mosi, "5": rst, "6": dc, "7": cs, "8": "P3V3"},
              "display", f"{name} module on SPI0.", name)  # fmt: skip
    elif kind == "segment":
        clk, dio = b.gpio("SEG_CLK", "out"), b.gpio("SEG_DIO", "out")
        b.add("J", SOCK(4), {"1": clk, "2": dio, "3": "VBUS", "4": "GND"}, "display",
              "TM1637 4-digit 7-segment display module.", "TM1637 display")  # fmt: skip
    elif kind == "matrix":
        miso, mosi, sck, cs = b.bus_spi("MTX")
        b.add("J", SOCK(5), {"1": "VBUS", "2": "GND", "3": mosi, "4": cs, "5": sck}, "display",
              "MAX7219 8x8 LED matrix module.", "MAX7219 matrix")  # fmt: skip
    elif kind == "lcd":
        sda, scl = b.bus_i2c()
        b.add("J", SOCK(4), {"1": "GND", "2": "VBUS", "3": sda, "4": scl}, "display",
              "16x2 character LCD with a PCF8574 I2C backpack.", "LCD1602 I2C")  # fmt: skip
    else:
        raise BlockError(f"unknown display {kind}")


def speaker(b: Board) -> None:
    """PAM8302 2.5 W class-D amplifier module driving a speaker: PWM audio from a GPIO through a 1k/10n low-pass
    into A+, SD on a GPIO to mute it. Socket order: VIN GND SD A+ A- SPK+ SPK-."""
    pwm = b.gpio("AUDIO", "out")
    en = b.gpio("AMP_SD", "out")
    f, sp, sn = b.net("AUDIO_F"), b.net("SPK_P"), b.net("SPK_N")
    b.add(
        "R",
        R,
        {"1": pwm, "2": f},
        "sound",
        "Low-pass with the 10n: turns PWM into audio.",
        "1k",
    )
    b.add("C", C, {"1": f, "2": "GND"}, "sound", "Low-pass capacitor.", "10n")
    b.add("J", SOCK(7), {"1": "VBUS", "2": "GND", "3": en, "4": f, "5": "GND", "6": sp, "7": sn}, "sound",
          "PAM8302 class-D amplifier module.", "PAM8302 amp")  # fmt: skip
    b.add(
        "J",
        JST2,
        {"1": sp, "2": sn},
        "sound",
        "Speaker plug (4-8 ohm, up to 2.5 W).",
        "speaker",
    )
    b.drives.append((en, "turns the speaker amplifier on"))


def key_matrix(b: Board, n: int = 12, switch: str = "tact") -> None:
    """N keys as a COL2ROW matrix (column, switch, 1N4148 anode, cathode on the row), as device_recipes.keyboard."""
    import math

    n = max(2, min(int(n), 64))
    cols = min(8, math.ceil(math.sqrt(n)))
    rows = math.ceil(n / cols)
    rn = [b.gpio("ROW", "in") for _ in range(rows)]
    cn = [b.gpio("COL", "in") for _ in range(cols)]
    piece = MX if switch == "mx" else SW
    for i in range(n):
        r, c = divmod(i, cols)
        k = b.net(f"K{i + 1}")
        b.add(
            "SW",
            piece,
            {"1": cn[c], "2": k},
            "keys",
            "Key switch." if switch == "mx" else "Key (tactile switch).",
        )
        b.add("D", SIGNAL_DIODE, {"2": k, "1": rn[r]}, "keys", "Stops ghosting so any keys can be held together.", "1N4148")  # fmt: skip
    b.inputs += rn + cn
    b.say.append(f"{n} keys in a {rows}x{cols} matrix")


def encoder(b: Board) -> None:
    a, bb, s = b.gpio("ENC_A", "in"), b.gpio("ENC_B", "in"), b.gpio("ENC_SW", "in")
    b.add("ENC", EC11, {"A": a, "B": bb, "C": "GND", "S1": s, "S2": "GND"}, "controls", "EC11 rotary encoder with a push switch.")  # fmt: skip
    b.inputs += [a, bb, s]


def pot(b: Board, label: str = "knob") -> None:
    w = b.gpio("POT", "adc")
    b.add(
        "RV",
        POT,
        {"1": "P3V3", "2": w, "3": "GND"},
        "controls",
        f"10k {label}: its wiper is read on an ADC pin.",
        "10k",
    )


def joystick(b: Board) -> None:
    x, y, s = b.gpio("JOY_X", "adc"), b.gpio("JOY_Y", "adc"), b.gpio("JOY_SW", "in")
    b.add("J", SOCK(5), {"1": "GND", "2": "P3V3", "3": x, "4": y, "5": s}, "controls",
          "Thumb joystick module: two axes on ADC pins and a push switch.", "joystick")  # fmt: skip
    b.inputs.append(s)


def ldr(b: Board) -> None:
    a = b.gpio("LIGHT", "adc")
    b.add(
        "R",
        R,
        {"1": "P3V3", "2": a},
        "sensors",
        "Divider top for the light sensor.",
        "10k",
    )
    b.add(
        "LDR",
        LDR,
        {"1": a, "2": "GND"},
        "sensors",
        "Light-dependent resistor: the ADC reads brightness.",
    )


def ntc(b: Board) -> None:
    a = b.gpio("TEMP", "adc")
    b.add(
        "R",
        R,
        {"1": "P3V3", "2": a},
        "sensors",
        "Divider top: half of 3.3 V on the ADC at 25 C.",
        "10k",
    )
    b.add("TH", NTC, {"1": a, "2": "GND"}, "sensors", "10k NTC thermistor.", "10k")


def dht(b: Board) -> None:
    d = b.gpio("DHT_DATA", "in")
    b.add(
        "U",
        DHT,
        {"1": "P3V3", "2": d, "4": "GND"},
        "sensors",
        "DHT11 temperature and humidity.",
        "DHT11",
    )
    b.add(
        "R",
        R,
        {"1": "P3V3", "2": d},
        "sensors",
        "Pull-up on the DHT11 data line.",
        "10k",
    )
    b.inputs.append(d)


def mic(b: Board) -> None:
    a = b.gpio("MIC", "adc")
    b.add("J", MIC, {"1": "P3V3", "2": "GND", "3": a}, "sensors", "Electret microphone module (MAX9814) on an ADC pin.", "MAX9814 mic")  # fmt: skip


ANALOG_MODULES = {
    "soil": ("soil moisture probe", 3),
    "water": ("water / rain sensor", 3),
    "flame": ("flame sensor", 3),
    "gas": ("MQ-2 gas sensor", 3),
    "uv": ("GUVA-S12SD UV sensor", 3),
    "pulse": ("pulse sensor", 3),
    "force": ("force-sensitive resistor", 3),
    "flex": ("flex sensor", 3),
    "current": ("ACS712 current sensor", 3),
    "emg": ("muscle (EMG) sensor", 3),
}


def analog_sensor(b: Board, kind: str = "soil") -> None:
    """3-pin analog module: VCC GND AOUT on an ADC pin."""
    if kind not in ANALOG_MODULES:
        raise BlockError(f"unknown analog sensor {kind}")
    a = b.gpio(kind.upper()[:5], "adc")
    b.add("J", SOCK(3), {"1": "P3V3", "2": "GND", "3": a}, "sensors", f"{ANALOG_MODULES[kind][0]} module on an ADC pin.", ANALOG_MODULES[kind][0])  # fmt: skip


DIGITAL_MODULES = {
    "pir": ("HC-SR501 PIR motion sensor", "VBUS"),
    "touch": ("TTP223 touch pad", "P3V3"),
    "tilt": ("SW-520D tilt switch module", "P3V3"),
    "hall": ("A3144 hall-effect sensor module", "P3V3"),
    "vibration": ("SW-420 vibration sensor", "P3V3"),
    "sound": ("sound-level detector module", "P3V3"),
    "ir_obstacle": ("IR obstacle sensor", "P3V3"),
    "line": ("TCRT5000 line sensor", "P3V3"),
    "reed": ("reed switch module", "P3V3"),
    "rain": ("rain detector (digital out)", "P3V3"),
    "radar": ("RCWL-0516 microwave radar", "VBUS"),
}


def digital_sensor(b: Board, kind: str = "pir") -> None:
    """3-pin module with a digital output: VCC OUT GND. A 5 V PIR's output is 3.3 V, safe for the Pico."""
    if kind not in DIGITAL_MODULES:
        raise BlockError(f"unknown digital sensor {kind}")
    name, rail = DIGITAL_MODULES[kind]
    o = b.gpio(kind.upper()[:6], "in")
    b.add(
        "J",
        SOCK(3),
        {"1": rail, "2": o, "3": "GND"},
        "sensors",
        f"{name} module.",
        name,
    )
    b.inputs.append(o)


def ultrasonic(b: Board) -> None:
    """HC-SR04: VCC TRIG ECHO GND; the 5 V echo comes down to 3.3 V through a 1k/2k divider."""
    trig, echo = b.gpio("TRIG", "out"), b.gpio("ECHO", "in")
    e5 = b.net("ECHO_5V")
    b.add("J", SOCK(4), {"1": "VBUS", "2": trig, "3": e5, "4": "GND"}, "sensors", "HC-SR04 ultrasonic distance sensor.", "HC-SR04")  # fmt: skip
    b.add(
        "R",
        R,
        {"1": e5, "2": echo},
        "sensors",
        "Echo divider top: 5 V echo down to 3.3 V.",
        "1k",
    )
    b.add("R", R, {"1": echo, "2": "GND"}, "sensors", "Echo divider bottom.", "2k")
    b.inputs.append(echo)
    b.drives.append((trig, "pings the distance sensor"))


def relay(b: Board) -> None:
    i = b.gpio("RELAY", "out")
    b.add("J", SOCK(3), {"1": "VBUS", "2": "GND", "3": i}, "outputs", "5 V relay module (opto-isolated input): switches a low-voltage load.", "relay module")  # fmt: skip
    b.drives.append((i, "switches the relay"))


def servo(b: Board, n: int = 1) -> None:
    for _ in range(int(n)):
        s = b.gpio("SERVO", "out")
        b.add(
            "J",
            HDR3,
            {"1": "GND", "2": "VBUS", "3": s},
            "outputs",
            "Servo plug: GND, 5 V, signal (SG90).",
            "servo",
        )
        b.drives.append((s, "moves the servo"))


def motor(b: Board, n: int = 1, label: str = "motor") -> None:
    """Low-side 2N3904 switch for a small DC motor from 5 V, 1N4001 across it for the flyback."""
    for _ in range(int(n)):
        g = b.gpio("MOTOR", "out")
        q = b.ref("Q")
        base, low = b.net(f"{q}_B"), b.net("MOT_N")
        b.add("R", R, {"1": g, "2": base}, "outputs", "Base resistor.", "1k")
        b.parts.append({"ref": q, "piece": NPN, "pins": {"1": "GND", "2": base, "3": low}, "group": "outputs",
                        "why": f"Switches the {label}."})  # fmt: skip
        b.add(
            "M",
            MOTOR,
            {"1": "VBUS", "2": low},
            "outputs",
            f"Small DC {label} (under 200 mA).",
            label,
        )
        b.add("D", FLYBACK, {"2": low, "1": "VBUS"}, "outputs", "Flyback diode: catches the motor's turn-off spike.", "1N4001")  # fmt: skip
        b.drives.append((g, f"runs the {label}"))


def neopixel(b: Board, n: int = 8) -> None:
    """WS2812B chain from VBUS, data through 330R, 100u bulk cap (the datasheet's circuit)."""
    n = max(1, min(int(n), 16))
    d = b.gpio("NPX", "out")
    din = b.net("NPX_DIN")
    b.add(
        "R", R, {"1": d, "2": din}, "lights", "Series resistor on the data line.", "330"
    )
    prev = din
    for i in range(n):
        pins = {"1": "VBUS", "3": "GND", "4": prev}
        if i < n - 1:
            prev = b.net(f"NPX_D{i + 1}")
            pins["2"] = prev
        b.add("LED", WS2812B, pins, "lights", "WS2812B addressable RGB LED.", "WS2812B")
    b.add(
        "C",
        CP,
        {"1": "VBUS", "2": "GND"},
        "lights",
        "Bulk capacitor on the LED supply.",
        "100u",
    )
    b.drives.append((d, "drives the RGB LEDs"))


def led_strip(b: Board) -> None:
    d = b.gpio("STRIP", "out")
    din = b.net("STRIP_DIN")
    b.add(
        "R",
        R,
        {"1": d, "2": din},
        "lights",
        "Series resistor on the strip's data line.",
        "330",
    )
    b.add("J", HDR3, {"1": "VBUS", "2": din, "3": "GND"}, "lights", "Plug for a WS2812B LED strip: 5 V, DIN, GND.", "LED strip")  # fmt: skip
    b.add(
        "C",
        CP,
        {"1": "VBUS", "2": "GND"},
        "lights",
        "Bulk capacitor for the strip.",
        "100u",
    )
    b.drives.append((d, "drives the LED strip"))


def sd_card(b: Board) -> None:
    miso, mosi, sck, cs = b.bus_spi("SD")
    b.add("J", SOCK(6), {"1": "GND", "2": "P3V3", "3": miso, "4": mosi, "5": sck, "6": cs}, "storage",
          "microSD card module on SPI0.", "microSD module")  # fmt: skip


def gps(b: Board) -> None:
    tx, rx = b.bus_uart("GPS")
    b.add("J", SOCK(4), {"1": "P3V3", "2": "GND", "3": tx, "4": rx}, "radio",
          "NEO-6M GPS module (with its patch antenna) on a UART.", "NEO-6M GPS")  # fmt: skip
    b.say.append("a NEO-6M GPS")


def uart_module(b: Board, kind: str = "hc05") -> None:
    names = {
        "hc05": ("HC-05 Bluetooth serial module", "BT"),
        "sim800": ("SIM800L GSM / SMS module", "GSM"),
        "dfplayer": ("DFPlayer Mini MP3 module", "MP3"),
        "fingerprint": ("optical fingerprint sensor", "FP"),
        "pm25": ("PMS5003 particulate sensor", "PM"),
        "lidar": ("TF-Luna lidar", "LIDAR"),
    }
    if kind not in names:
        raise BlockError(f"unknown serial module {kind}")
    name, short = names[kind]
    tx, rx = b.bus_uart(short)
    b.add(
        "J",
        SOCK(4),
        {"1": "VBUS", "2": "GND", "3": tx, "4": rx},
        "radio",
        f"{name} on a UART.",
        name,
    )


def lora(b: Board) -> None:
    miso, mosi, sck, cs = b.bus_spi("LORA")
    rst, irq = b.gpio("LORA_RST", "out"), b.gpio("LORA_IRQ", "in")
    b.add("J", SOCK(8), {"1": "P3V3", "2": "GND", "3": sck, "4": miso, "5": mosi, "6": cs, "7": rst, "8": irq},
          "radio", "RFM95W LoRa breakout (915 MHz) on SPI0.", "RFM95W LoRa")  # fmt: skip
    b.inputs.append(irq)
    b.say.append("a LoRa radio")


def nrf24(b: Board) -> None:
    miso, mosi, sck, cs = b.bus_spi("NRF")
    ce, irq = b.gpio("NRF_CE", "out"), b.gpio("NRF_IRQ", "in")
    b.add("J", NRF_HDR, {"1": "GND", "2": "P3V3", "3": ce, "4": cs, "5": sck, "6": mosi, "7": miso, "8": irq},
          "radio", "nRF24L01+ 2.4 GHz radio module.", "nRF24L01")  # fmt: skip
    b.add(
        "C",
        CP,
        {"1": "P3V3", "2": "GND"},
        "radio",
        "Reservoir capacitor the nRF24 needs on 3.3 V.",
        "10u",
    )
    b.inputs.append(irq)


def rfid(b: Board) -> None:
    miso, mosi, sck, cs = b.bus_spi("RFID")
    rst = b.gpio("RFID_RST", "out")
    b.add("J", SOCK(8), {"1": cs, "2": sck, "3": mosi, "4": miso, "6": "GND", "7": rst, "8": "P3V3"},
          "radio", "RC522 13.56 MHz RFID reader on SPI0.", "RC522 RFID")  # fmt: skip


def ir(b: Board, tx: bool = True, rx: bool = True) -> None:
    if rx:
        o = b.gpio("IR_RX", "in")
        b.add(
            "U",
            TSOP,
            {"1": o, "2": "GND", "3": "P3V3"},
            "radio",
            "TSOP38238 38 kHz IR receiver.",
            "TSOP38238",
        )
        b.inputs.append(o)
    if tx:
        g = b.gpio("IR_TX", "out")
        q = b.ref("Q")
        base, k, a = b.net(f"{q}_B"), b.net("IR_K"), b.net("IR_A")
        b.add("R", R, {"1": g, "2": base}, "radio", "Base resistor.", "1k")
        b.parts.append({"ref": q, "piece": NPN, "pins": {"1": "GND", "2": base, "3": k}, "group": "radio",
                        "why": "Pulses the IR LED harder than a GPIO can."})  # fmt: skip
        b.add(
            "R", R, {"1": "VBUS", "2": a}, "radio", "IR LED current, about 50 mA.", "68"
        )
        b.add(
            "D",
            LED,
            {"2": a, "1": k},
            "radio",
            "940 nm IR LED for remote-control codes.",
            "IR",
        )
        b.drives.append((g, "sends IR"))


def keypad(b: Board) -> None:
    """4x4 membrane keypad on an 8-pin socket: 4 rows, 4 columns."""
    nets = [b.gpio("KP_R", "in") for _ in range(4)] + [
        b.gpio("KP_C", "in") for _ in range(4)
    ]
    b.add(
        "J",
        SOCK(8),
        {str(i + 1): n for i, n in enumerate(nets)},
        "controls",
        "4x4 membrane keypad.",
        "4x4 keypad",
    )
    b.inputs += nets


def load_cell(b: Board) -> None:
    dt, sck = b.gpio("HX_DT", "in"), b.gpio("HX_SCK", "out")
    b.add("J", SOCK(4), {"1": "GND", "2": dt, "3": sck, "4": "P3V3"}, "sensors", "HX711 load-cell amplifier module.", "HX711")  # fmt: skip
    b.inputs.append(dt)


def wireless(b: Board) -> None:
    """Bluetooth LE and Wi-Fi: the Pico W's radio and antenna are on the module (as live._radio_fix does)."""
    b.wireless = True


BLOCKS: dict[str, Callable[..., None]] = {
    f.__name__: f
    for f in (
        status_led, leds, rgb_led, button, buzzer, usb_c, lipo, coin_cell, battery_pack, i2c_sensor, display,
        speaker, key_matrix, encoder, pot, joystick, ldr, ntc, dht, mic, analog_sensor, digital_sensor,
        ultrasonic, relay, servo, motor, neopixel, led_strip, sd_card, gps, uart_module, lora, nrf24, rfid, ir,
        keypad, load_cell, wireless,
    )
}  # fmt: skip

_LETTERS = "bcdefghijklmnpqrstuvwxyz"


def compose(
    blocks: list[Any],
    ask: str = "",
    title: str = "ETCH BOARD",
    name: str = "device",
    wireless: bool | None = None,
) -> dict[str, Any]:
    """The design for a list of blocks ([name, {params}] or "name"), every net wired by code. Raises BlockError."""
    b = Board(ask=ask)
    if wireless is None:
        from .live import _WIRELESS

        wireless = bool(re.search(_WIRELESS, ask.lower()))
    b.wireless = bool(wireless)
    specs = [
        (s, {}) if isinstance(s, str) else (s[0], dict(s[1] if len(s) > 1 else {}))
        for s in blocks
    ]
    if not any(n in ("status_led", "leds", "rgb_led") for n, _ in specs):
        specs.append(
            ("status_led", {})
        )  # every board shows it runs, and the firmware test has an LED to check
    for n, params in specs:
        fn = BLOCKS.get(n)
        if fn is None:
            raise BlockError(f"unknown block {n}")
        try:
            fn(b, **params)
        except TypeError as e:
            raise BlockError(f"{n}: {e}") from e
    u = b.mcu()
    parts = [u] + b.parts
    cmds = [
        {"input": str(i), "high": [n], "does": f"lights {n}"}
        for i, n in enumerate(b.leds[:9], 1)
    ]
    if len(b.leds) > 1:
        cmds.append({"input": "a", "high": list(b.leds), "does": "lights every LED"})
    for (net, does), letter in zip(b.drives, _LETTERS):
        cmds.append({"input": letter, "high": [net], "does": does})
    cmds.append({"input": "o", "high": [], "does": "turns everything off"})
    used = [n for n, _ in specs if n not in ("status_led",)]
    return {
        "name": name,
        "title": title.upper()[:24],
        "voltage": 5.0,
        "parts": parts,
        "scenarios": [{"name": "idle"}],
        "benchmarks": [],
        "firmware": {"commands": cmds, "inputs": sorted(set(b.inputs))},
        "blocks": [[n, p] for n, p in specs],
        "say": f"{title}: a Raspberry Pi Pico{' W' if b.wireless else ''}"
        + (f" with {', '.join(b.say)}" if b.say else "")
        + f", built from {len(specs)} wired blocks ({', '.join(dict.fromkeys(used)) or 'status LED'}).",
    }


# ------------------------------------------------------------------ device catalog
DEVICES = config.ROOT / "engine/parts/devices.json"
_cache: dict[str, Any] = {}


def _tok(s: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", s.lower().replace("-", " ").replace("/", " ")):
        if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            w = w[:-1]
        out.append(w)
    return out


def load_devices() -> dict[str, Any]:
    if "db" not in _cache:
        db = json.loads(Path(DEVICES).read_text())
        index: dict[tuple[str, ...], dict[str, Any]] = {}
        for d in db["devices"]:
            for a in [d["name"], *d.get("aliases", [])]:
                index.setdefault(tuple(_tok(a)), d)
        db["index"] = index
        db["longest"] = max(len(k) for k in index)
        _cache["db"] = db
    return _cache["db"]


# words in the ask that add a block to whatever device it names ("a gps tracker with a screen")
MODIFIERS: list[tuple[str, list[Any]]] = [
    (r"\b(oled|screen|display)\b", [["display", {"kind": "oled"}]]),
    (r"\b(colou?r (screen|display)|tft)\b", [["display", {"kind": "tft"}]]),
    (r"\be-?(paper|ink)\b", [["display", {"kind": "epaper"}]]),
    (r"\b(recharg\w*|lipo|li-?ion|rechargeable battery)\b", [["lipo", {}]]),
    (r"\b(usb-?c)\b", [["usb_c", {}]]),
    (r"\b(coin cell|cr2032)\b", [["coin_cell", {}]]),
    (r"\b(buzzer|beep\w*|chime|alarm)\b", [["buzzer", {}]]),
    (r"\b(speaker|audio out|sound out)\b", [["speaker", {}]]),
    (r"\b(gps)\b", [["gps", {}]]),
    (r"\b(lora)\b", [["lora", {}]]),
    (r"\b(rgb|neopixel|ws2812\w*)\b", [["rgb_led", {}]]),
    (r"\b(knob|encoder|dial)\b", [["encoder", {}]]),
    (r"\b(sd card|micro ?sd)\b", [["sd_card", {}]]),
    (r"\b(vibrat\w*|haptic)\b", [["motor", {"label": "vibration motor"}]]),
    (r"\b(temperature|humidity)\b", [["i2c_sensor", {"chip": "sht31"}]]),
    (r"\b(motion sensor|pir)\b", [["digital_sensor", {"kind": "pir"}]]),
    (r"\b(button)\b", [["button", {}]]),
    (r"\b(wi-?fi|bluetooth|ble|wireless)\b", [["wireless", {}]]),
]


def find(ask: str) -> dict[str, Any] | None:
    """The catalog device the ask names: the longest alias found as consecutive words, first one on a tie."""
    db = load_devices()
    t = _tok(ask)
    best: tuple[int, int] | None = None
    hit = None
    for i in range(len(t)):
        for n in range(min(db["longest"], len(t) - i), 0, -1):
            d = db["index"].get(tuple(t[i : i + n]))
            if d and (best is None or n > best[0]):
                best, hit = (n, i), d
                break
    return hit


def _merge(base: list[Any], extra: list[Any]) -> list[Any]:
    have = {
        (s[0], json.dumps(s[1] if len(s) > 1 else {}, sort_keys=True)) for s in base
    }
    names = {s[0] for s in base}
    out = list(base)
    for s in extra:
        n = s[0]
        if n in names and n not in ("i2c_sensor", "button", "display"):
            continue
        if n == "display" and "display" in names:
            continue
        key = (n, json.dumps(s[1] if len(s) > 1 else {}, sort_keys=True))
        if key not in have:
            out.append(s)
            have.add(key)
            names.add(n)
    return out


def device_design(ask: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(design, catalog entry) for an ask that names a catalog device, plus the blocks its modifier words add."""
    d = find(ask)
    if d is None:
        return None
    a = ask.lower()
    blocks = list(d["blocks"])
    for rx, add in MODIFIERS:
        if re.search(rx, a):
            blocks = _merge(blocks, add)
    try:
        design = compose(
            blocks, ask, d["name"], d["id"], wireless=d.get("wireless") or None
        )
    except BlockError:
        design = compose(
            d["blocks"], ask, d["name"], d["id"], wireless=d.get("wireless") or None
        )
    design["say"] = f"A {d['name']} from the device library. " + design["say"]
    return design, d


# an ask that names its own chip or topology ("a 555 timer", "an ESP32 clock", "no microcontroller") is not a
# catalog device even when a device word is in it: the picker builds what was named
_OWN_DESIGN = re.compile(
    r"\b(?:[a-z]{1,5}-?\d{3,}[a-z0-9]*|555|esp ?32|esp8266|arduino|attiny|atmega|stm32|nixie|op[ -]?amps?|mosfets?|"
    r"transistors? only|no (?:micro ?controller|mcu|chip|code)|without (?:a )?(?:micro ?controller|mcu|chip|code)|"
    r"analog only|discrete|schematic of|logic gates?|flip[ -]?flop)\b",
    re.I,
)


def match(
    ask: str, catalog: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], str, list[dict[str, Any]]] | None:
    """The recipe step's shape (recipes.match): (design, route, the catalog pieces it uses), or None."""
    if _OWN_DESIGN.search(ask):
        return None
    got = device_design(ask)
    if got is None:
        return None
    design, d = got
    if not all(p["piece"] in catalog for p in design["parts"]):
        return None
    used = list({p["piece"]: catalog[p["piece"]] for p in design["parts"]}.values())
    return design, f"device:{d['id']}", used


# ------------------------------------------------------------------ offline gate (the build loop's checks, no KiCad)
def gate(design: dict[str, Any], ask: str = "", work: Path | None = None) -> str | None:
    """None when the design passes every check the assembly loop makes before the KiCad build, run on the design
    exactly as composed: every piece in the library, no lonely nets before or after the fix-up chain, no part the
    fix-ups had to add, wiring lint, firmware build + test, firmware benchmarks, and spec loads. Or why not."""
    import copy
    import tempfile

    import yaml

    from . import engine_api, firmware, live
    from .events import Stream

    d = copy.deepcopy(design)
    cat = live._cat()
    if bad := sorted({p["piece"] for p in d["parts"] if p["piece"] not in cat}):
        return f"pieces not in the library: {bad}"
    live.register([cat[p["piece"]] for p in d["parts"]])  # what to_spec does for library pieces
    if bad := sorted({p["piece"] for p in d["parts"] if p["piece"] not in live.load_index()}):
        return f"pieces the spec builder cannot resolve (no model or pads): {bad}"
    if raw := live._lonely_nets(d):
        return f"lonely nets as composed: {raw}"
    nets_used = {str(v) for p in d["parts"] for v in p["pins"].values()}
    if "VSYS" in nets_used or any(
        p["pins"].get("39") for p in d["parts"] if p["piece"] in (PICO, PICO_W)
    ):
        return (
            "VSYS is a power input on the Pico symbol (ERC): rails come from VBUS / 3V3"
        )
    kept = [cat[k] for k in sorted({p["piece"] for p in d["parts"]})]
    keys = {p["key"] for p in kept}
    n0 = len(d["parts"])
    live._normalize_pins(d, kept)
    live._merge_lonely(d, kept)
    live._radio_fix(d, kept, ask)
    if live._auto_mcu(d, kept, ask):
        return "the fix-up chain had to add a microcontroller"
    live._wire_named_gpio(d, kept)
    if firmware.mcu_part(d, kept):
        live._wire_hinted(d, kept)
    firmware.tie_grounds(d, kept)
    live._wire_mcu_supply(d, kept)
    live._drop_lonely_gpio(d, kept)
    stand = live._led_stand_ins(d, kept, keys)
    live._fix_refs(d)
    live._fold_parallel(d)
    firmware.default_commands(d, kept)
    findings = live._analog(Stream("gate", echo=False), d, kept, keys, ask)
    if len(d["parts"]) != n0:
        return f"the fix-up chain changed the part count {n0} -> {len(d['parts'])}"
    if stand:
        return f"LED stand-ins: {stand}"
    if mism := live._ref_mismatch(d):
        return f"ref mismatch: {mism}"
    if lonely := live._lonely_nets(d):
        return f"lonely nets after fix-ups: {lonely}"
    if silent := live._no_sound_source(d, kept, ask):
        return silent
    if lint := live._wiring_lint(d, kept):
        return "wiring lint: " + "; ".join(lint)
    if findings:
        return "analog: " + "; ".join(findings)
    with tempfile.TemporaryDirectory() as td:
        out = Path(work or td)
        out.mkdir(parents=True, exist_ok=True)
        if firmware.mcu_part(d, kept):
            fw = firmware.build(d, kept, out)
            if isinstance(fw, str):
                return "firmware: " + fw
            rows = firmware.benchmarks(d, kept)
            if isinstance(rows, str):
                return "firmware benchmarks: " + rows
            d["scenarios"], _ = fw
            d["benchmarks"] = rows
        spec = live.to_spec(d, "GATE")
        (out / "spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False, width=110))
        return engine_api.validate(out / "spec.yaml")


# ------------------------------------------------------------------ the picker chooses blocks, code wires them
# crash-trends 10-17 (qa/crash-trends-1017.md): the model as netlist author passes 10/66, recipes 35/41; the repair
# loop turns 3 % of failures into passes. So before the netlist picker, the model is asked only WHICH blocks a board
# needs; compose() writes every net, firmware and benchmarks, and gate() checks it before the build.
BLOCK_HELP = {
    "status_led": "one LED + resistor on a GPIO (params: colour)",
    "leds": "n LEDs, each with its resistor (params: n, colours 'red,green')",
    "rgb_led": "one RGB LED, three resistors",
    "button": "n push buttons with pull-ups (params: n)",
    "buzzer": "piezo buzzer on a transistor (beeps, alarms, tones)",
    "speaker": "class-D amp module + speaker plug (music, voice, audio)",
    "usb_c": "USB-C power jack",
    "lipo": "LiPo cell + TP4056 charger (rechargeable)",
    "coin_cell": "CR2032 coin cell",
    "battery_pack": "AA battery pack terminal",
    "i2c_sensor": "I2C breakout (params: chip, one of " + ", ".join(I2C_MODULES) + ")",
    "display": "display (params: kind = oled | tft | epaper | segment | matrix | lcd)",
    "key_matrix": "n keys in a diode matrix (params: n, switch = tact | mx)",
    "encoder": "rotary encoder with push",
    "pot": "potentiometer knob on an ADC pin",
    "joystick": "2-axis thumb joystick (uses 2 of the 3 ADC pins)",
    "ldr": "light sensor",
    "ntc": "thermistor temperature sensor",
    "dht": "DHT11 temperature + humidity",
    "mic": "microphone module",
    "analog_sensor": "analog module (params: kind, one of " + ", ".join(ANALOG_MODULES) + ")",
    "digital_sensor": "digital module (params: kind, one of " + ", ".join(DIGITAL_MODULES) + ")",
    "ultrasonic": "HC-SR04 distance sensor",
    "relay": "relay module",
    "servo": "n servo plugs (params: n)",
    "motor": "n small DC motors on transistors (params: n, label e.g. fan, pump, vibration motor)",
    "neopixel": "n WS2812B RGB LEDs on the board (params: n up to 16)",
    "led_strip": "plug for an off-board WS2812B strip",
    "sd_card": "microSD module",
    "gps": "NEO-6M GPS module",
    "uart_module": "serial module (params: kind = hc05 | sim800 | dfplayer | fingerprint | pm25 | lidar)",
    "lora": "LoRa radio",
    "nrf24": "nRF24L01 2.4 GHz radio",
    "rfid": "RC522 RFID reader",
    "ir": "IR remote send and receive (params: tx, rx booleans)",
    "keypad": "4x4 membrane keypad",
    "load_cell": "HX711 load cell (weight)",
    "wireless": "Wi-Fi / Bluetooth (the Pico W radio, no extra part)",
}

BLOCKS_FN = {
    "name": "submit_blocks",
    "description": "The circuit blocks the board needs. Code wires them to a Raspberry Pi Pico.",
    "parameters": {
        "type": "object",
        "properties": {
            "fits": {
                "type": "boolean",
                "description": "false when the ask is not a microcontroller board (a 555 or transistor-only "
                "circuit, a named chip that is not a block, a power supply)",
            },
            "title": {"type": "string", "description": "silkscreen title, max 16 characters"},
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"block": {"type": "string"}, "params": {"type": "object"}},
                    "required": ["block"],
                },
            },
        },
        "required": ["fits", "blocks"],
    },
}


def block_prompt(ask: str) -> list[dict[str, str]]:
    lines = "\n".join(f"- {k}: {v}" for k, v in BLOCK_HELP.items())
    return [
        {
            "role": "system",
            "content": "You are Etch's block picker. A board is a Raspberry Pi Pico plus blocks; code writes every "
            "net, the firmware and the tests. Choose the fewest blocks that do what the ask says. At most 3 analog "
            "inputs (pot, joystick axes, ldr, ntc, mic, analog_sensor) and about 24 GPIO in all. A status LED is "
            "added for you. Blocks:\n" + lines,
        },
        {"role": "user", "content": f"Board request: {ask}"},
    ]


def from_answer(ans: dict[str, Any], ask: str) -> dict[str, Any] | None:
    """The composed design for a block-picker answer, or None when it does not fit blocks or fails the gate."""
    if not ans.get("fits") or not ans.get("blocks"):
        return None
    specs = []
    for b in ans["blocks"]:
        name = str(b.get("block", "")).strip()
        if name not in BLOCKS:
            return None
        params = b.get("params") or {}
        if not isinstance(params, dict):
            return None
        specs.append([name, params])
    title = str(ans.get("title") or ask)[:16] or "ETCH BOARD"
    try:
        d = compose(specs, ask, title, re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "board")
    except BlockError:
        return None
    return None if gate(d, ask) else d


def pick_blocks(stream: Any, ask: str, model: str, catalog: dict[str, dict[str, Any]]):
    """recipes.match's shape for a model-chosen block list: (design, "blocks", pieces) or None (go to the picker)."""
    from . import llm

    try:
        ans, _ = llm.call_json(stream, "picker", model, block_prompt(ask), BLOCKS_FN, timeout=60, max_tokens=800)
    except Exception as e:  # any model failure falls through to the netlist picker, as before blocks existed
        stream.emit("picker", "blocks_failed", reason=str(e)[:200])
        return None
    d = from_answer(ans, ask)
    stream.emit("picker", "blocks", answer=ans, used=bool(d))
    if d is None or not all(p["piece"] in catalog for p in d["parts"]):
        return None
    used = list({p["piece"]: catalog[p["piece"]] for p in d["parts"]}.values())
    return d, "blocks", used

"""Firmware for boards with a USB microcontroller module (Raspberry Pi Pico).

The picker gives a command table: which nets go high when a character arrives over USB serial.
Code turns it into MicroPython, runs that program under CPython with a stand-in `machine` module,
and records the pin levels it actually sets for each command. Those recorded levels, not the table,
become the ngspice scenarios, so the simulated LEDs are driven by the tested program.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import firmware_esp32

_FAKE_MACHINE = """
STATE = {}
class Pin:
    OUT = 1
    IN = 0
    PULL_UP = 1
    def __init__(self, n, mode=None, pull=None):
        self.n = n
        STATE.setdefault(n, 0)
    def value(self, v=None):
        if v is None:
            return STATE[self.n]
        STATE[self.n] = 1 if v else 0

# PWM on a pin: STATE holds the logic level the simulation uses (on while duty > 0), DUTY the 16-bit duty
DUTY = {}
class PWM:
    def __init__(self, pin, freq=1000, duty_u16=0):
        self.n = pin.n
        self.freq(freq)
        self.duty_u16(duty_u16)
    def freq(self, f=None):
        if f is not None:
            self.f = f
        return self.f
    def duty_u16(self, d=None):
        if d is None:
            return DUTY[self.n]
        DUTY[self.n] = max(0, min(65535, int(d)))
        STATE[self.n] = 1 if DUTY[self.n] else 0

# the microphone input: SOUND (set by the runner) is "quiet" or "speaking"; the test ends after LIMIT reads
import math, os, random
SOUND = os.environ.get("ETCH_SOUND", "quiet")
LIMIT = 6000
_reads = [0]
_rng = random.Random(7)
class ADC:
    def __init__(self, pin):
        self.pin = pin
    def read_u16(self):
        _reads[0] += 1
        if _reads[0] > LIMIT:
            raise SystemExit
        t = _reads[0] / 8000.0  # about 8 kHz sampling
        mid = 32768 + _rng.randint(-400, 400)  # the amp's VCC/2 bias plus hiss
        if SOUND == "speaking":
            mid += int(14000 * math.sin(2 * math.pi * 220 * t) * (0.6 + 0.4 * math.sin(2 * math.pi * 3 * t)))
        return max(0, min(65535, mid))
"""

_RUNNER = """
import json, sys, types
fake = types.ModuleType("machine")
exec(open(sys.argv[2]).read(), fake.__dict__)
sys.modules["machine"] = fake
try:
    exec(compile(open(sys.argv[1]).read(), "main.py", "exec"), {"__name__": "__main__"})
except SystemExit:
    pass
print("STATE " + json.dumps(fake.STATE))
print("DUTY " + json.dumps(fake.DUTY))
"""


def mcu_part(
    design: dict[str, Any], kept: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    by_key = {p["key"]: p for p in kept}
    for part in design.get("parts", []):
        piece = by_key.get(part.get("piece")) or {}
        if (piece.get("spice") or {}).get("role") == "mcu":
            return part, piece
    return None


def _gpio_by_net(part: dict[str, Any], piece: dict[str, Any]) -> dict[str, int]:
    """Net name -> GPIO number for every module pin the design wires to a GPIO."""
    out = {}
    reserved = {str(n) for n in (piece.get("spice") or {}).get("reserved") or []}  # ESP32 strap + USB pins
    for num, net in (part.get("pins") or {}).items():
        if str(num) in reserved:
            continue
        m = re.match(r"GPIO(\d+)", str(piece["pins"].get(str(num), "")))
        if m:
            out[str(net)] = int(m.group(1))
    return out


def generate(
    commands: list[dict[str, Any]],
    gpio: dict[str, int],
    title: str,
    pwm: list[str] | None = None,
    inputs: list[str] | None = None,
) -> str:
    pwm = sorted(n for n in (pwm or []) if n in gpio)
    if pwm:
        ins = {n: g for n, g in gpio.items() if n in (inputs or [])}
        return _generate_pwm(commands, {n: g for n, g in gpio.items() if n not in ins}, title, pwm, ins)
    gpio = {n: g for n, g in gpio.items() if n not in (inputs or [])}  # sensor pins stay inputs (never driven)
    table = {
        str(c["input"])[:1]: sorted({n for n in c.get("high", []) if n in gpio})
        for c in commands
        if c.get("input")
    }
    pins = "\n".join(
        f'    "{net}": Pin({n}, Pin.OUT),'
        for net, n in sorted(gpio.items(), key=lambda x: x[1])
    )
    cmds = "\n".join(f'    "{k}": {tuple(v)!r},' for k, v in table.items())
    keys = " or ".join(table)
    return f"""# {title}: MicroPython for the Raspberry Pi Pico on this board. Generated and tested by Etch.
# Flash: hold BOOTSEL, plug the Pico in, copy the MicroPython .uf2 onto it, then save this file as main.py
# (Thonny: File > Save as > Raspberry Pi Pico). Open the Pico's serial port and type {keys}.
import sys
from machine import Pin

PINS = {{
{pins}
}}
COMMANDS = {{
{cmds}
}}


def show(cmd):
    for net, pin in PINS.items():
        pin.value(1 if net in COMMANDS[cmd] else 0)


for pin in PINS.values():
    pin.value(0)
print("ready: type {keys}")
while True:
    ch = sys.stdin.read(1)
    if not ch:
        break
    if ch in COMMANDS:
        show(ch)
        print("ok", ch)
    elif ch not in "\\r\\n ":
        print("unknown", ch)
"""


def _generate_pwm(
    commands: list[dict[str, Any]], gpio: dict[str, int], title: str, pwm: list[str], inputs: dict[str, int]
) -> str:
    """Typed commands where some outputs are PWM (motor driver enables): digits 1-9 set the speed. Sensor header
    pins (inputs) are read with a pull-up and printed on ?."""
    table = {
        str(c["input"])[:1]: sorted({n for n in c.get("high", []) if n in gpio})
        for c in commands
        if c.get("input")
    }
    pins = "\n".join(
        f'    "{net}": Pin({n}, Pin.OUT),' for net, n in sorted(gpio.items(), key=lambda x: x[1]) if net not in pwm
    )
    pwms = "\n".join(f'    "{net}": PWM(Pin({gpio[net]}), freq=1000, duty_u16=0),' for net in pwm)
    cmds = "\n".join(f'    "{k}": {tuple(v)!r},' for k, v in table.items())
    sens = "\n".join(f'    "{net}": Pin({n}, Pin.IN, Pin.PULL_UP),' for net, n in sorted(inputs.items()))
    keys = ", ".join(table)
    return f"""# {title}: MicroPython for the Raspberry Pi Pico on this board. Generated and tested by Etch.
# Flash: hold BOOTSEL, plug the Pico in, copy the MicroPython .uf2 onto it, then save this file as main.py
# (Thonny: File > Save as > Raspberry Pi Pico). Open the Pico's serial port and type {keys}; 1 to 9 sets the
# speed (PWM duty on the driver's enable pins, 9 = full); ? prints the sensor header pins.
import sys
from machine import PWM, Pin

PINS = {{
{pins}
}}
SPEED_PINS = {{
{pwms}
}}
SENSORS = {{
{sens}
}}
COMMANDS = {{
{cmds}
}}
speed = 9
current = None


def duty():
    return 65535 * speed // 9


def show(cmd):
    for net, pin in PINS.items():
        pin.value(1 if net in COMMANDS[cmd] else 0)
    for net, pwm in SPEED_PINS.items():
        pwm.duty_u16(duty() if net in COMMANDS[cmd] else 0)


for pin in PINS.values():
    pin.value(0)
print("ready: type {keys}, 1-9 for speed")
while True:
    ch = sys.stdin.read(1)
    if not ch:
        break
    if ch in COMMANDS:
        current = ch
        show(ch)
        print("ok", ch)
    elif ch in "123456789":
        speed = int(ch)
        if current:
            show(current)
        print("speed", speed)
    elif ch == "?":
        print("sensors", {{net: pin.value() for net, pin in SENSORS.items()}})
    elif ch not in "\\r\\n ":
        print("unknown", ch)
"""


def generate_sound(
    fw: dict[str, Any], gpio: dict[str, int], adc_gpio: int, title: str
) -> str:
    loud = sorted({n for n in fw.get("loud_high", []) if n in gpio})
    quiet = sorted({n for n in fw.get("quiet_high", []) if n in gpio})
    pins = "\n".join(
        f'    "{net}": Pin({n}, Pin.OUT),'
        for net, n in sorted(gpio.items(), key=lambda x: x[1])
    )
    return f"""# {title}: MicroPython for the Raspberry Pi Pico on this board. Generated and tested by Etch.
# The microphone amplifier's output sits at half the supply and swings when there is sound. Every 50 ms the
# program measures that swing (peak to peak) on ADC{adc_gpio - 26} (GPIO{adc_gpio}); a swing over THRESHOLD means sound.
# Flash: hold BOOTSEL, plug the Pico in, copy the MicroPython .uf2 onto it, then save this file as main.py.
# If it reacts to room noise, raise THRESHOLD; if it misses quiet speech, lower it.
from machine import ADC, Pin

PINS = {{
{pins}
}}
LOUD = {tuple(loud)!r}
QUIET = {tuple(quiet)!r}
THRESHOLD = 6000  # peak-to-peak counts out of 65535 (about 0.3 V at 3.3 V)
WINDOW = 400  # samples per decision, about 50 ms
mic = ADC(Pin({adc_gpio}))


def show(on):
    for net, pin in PINS.items():
        pin.value(1 if net in on else 0)


show(QUIET)
while True:
    lo, hi = 65535, 0
    for _ in range(WINDOW):
        v = mic.read_u16()
        lo, hi = min(lo, v), max(hi, v)
    show(LOUD if hi - lo > THRESHOLD else QUIET)
"""


def run(main_py: str, keys: str, work: Path, sound: str = "quiet") -> dict[int, int]:
    """Feed `keys` to the program over stdin; return GPIO number -> level when input runs out."""
    (work / "main.py").write_text(main_py)
    (work / "fake_machine.py").write_text(_FAKE_MACHINE)
    (work / "runner.py").write_text(_RUNNER)
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(work / "runner.py"),
            str(work / "main.py"),
            str(work / "fake_machine.py"),
        ],
        input=keys,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env={"ETCH_SOUND": sound, "PATH": "/usr/bin:/bin"},
    )
    line = next((x for x in proc.stdout.splitlines() if x.startswith("STATE ")), None)
    if proc.returncode != 0 or line is None:
        raise RuntimeError(
            f"firmware test crashed: {(proc.stderr or proc.stdout)[-400:]}"
        )
    return {int(k): v for k, v in json.loads(line[6:]).items()}


def run_duty(main_py: str, keys: str, work: Path) -> dict[int, int]:
    """Like run(), but GPIO number -> 16-bit PWM duty for the pins the program drives with PWM."""
    run(main_py, keys, work)  # writes the files and raises on a crash
    proc = subprocess.run(
        [sys.executable, "-I", str(work / "runner.py"), str(work / "main.py"), str(work / "fake_machine.py")],
        input=keys,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env={"ETCH_SOUND": "quiet", "PATH": "/usr/bin:/bin"},
    )
    line = next((x for x in proc.stdout.splitlines() if x.startswith("DUTY ")), "DUTY {}")
    return {int(k): v for k, v in json.loads(line[5:]).items()}


def build(
    design: dict[str, Any], kept: list[dict[str, Any]], out: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]] | str:
    """(scenarios, report) for the board's tested firmware, or why it could not be built."""
    found = mcu_part(design, kept)
    if not found:
        return "the design has firmware but no microcontroller module part"
    part, piece = found
    chip = firmware_esp32.chip_of(piece)
    if (design.get("firmware") or {}).get("mode") == "sound":
        if chip:
            return "sound mode is Pico-only for now (its ADC sampling loop is tuned for the RP2040); use a Pico"
        return _build_sound(design, part, piece, out)
    commands = (design.get("firmware") or {}).get("commands") or []
    if chip and _readout(design, kept, part, piece):
        gpio = _gpio_by_net(part, piece)
        return firmware_esp32.build_readout(design, part, gpio, drivers(design, kept, gpio), chip, out)
    if _readout(design, kept, part, piece):
        if drv := drivers(design, kept, _gpio_by_net(part, piece)):
            return _build_circuitpython(
                design, part, _gpio_by_net(part, piece), drv, out
            )
        return _build_readout(design, part, piece, out)
    gpio = _gpio_by_net(part, piece)
    unknown = sorted({n for c in commands for n in c.get("high", []) if n not in gpio})
    if unknown:
        return f"firmware drives nets {unknown}, but no {part['ref']} GPIO pin is wired to them (GPIO nets: {sorted(gpio)})"
    pwm = [n for n in (design.get("firmware") or {}).get("pwm") or [] if n in gpio]
    inputs = [n for n in (design.get("firmware") or {}).get("inputs") or [] if n in gpio]
    main_py = generate(commands, gpio, str(design.get("title", "ETCH BOARD")), pwm, inputs)
    if chip:
        main_py = firmware_esp32.retarget(main_py, chip)
    gpio = {n: g for n, g in gpio.items() if n not in inputs}
    fw = out / "firmware"
    fw.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        idle = run(main_py, "", work)
        tests, scen = (
            [],
            [{"name": "idle", "gpio": {f"GPIO{n}": v for n, v in idle.items()}}],
        )
        for c in commands:
            k = str(c["input"])[:1]
            got = run(main_py, k + "\n", work)
            want = {gpio[n] for n in c.get("high", [])}
            ok = {n for n, v in got.items() if v} == want
            tests.append(
                {
                    "input": k,
                    "high_gpio": sorted(n for n, v in got.items() if v),
                    "expected": sorted(want),
                    "pass": ok,
                }
            )
            scen.append(
                {"name": f"cmd_{k}", "gpio": {f"GPIO{n}": v for n, v in got.items()}}
            )
        # switching between commands must not leave the previous output on
        seq = "".join(str(c["input"])[:1] for c in commands)
        last = run(main_py, seq, work)
        want_last = {gpio[n] for n in commands[-1].get("high", [])}
        tests.append(
            {
                "input": seq,
                "high_gpio": sorted(n for n, v in last.items() if v),
                "expected": sorted(want_last),
                "pass": {n for n, v in last.items() if v} == want_last,
            }
        )
        # speed: the first moving command at speed 5, then 9, sets the enable duty (QA round 4, motor car)
        mover = next((c for c in commands if set(c.get("high", [])) & set(pwm)), None)
        if mover:
            k = str(mover["input"])[:1]
            en = sorted(gpio[n] for n in set(mover["high"]) & set(pwm))
            for keys, want_duty in ((f"5{k}", 65535 * 5 // 9), (f"{k}5", 65535 * 5 // 9), (f"{k}59", 65535)):
                got = run_duty(main_py, keys, work)
                tests.append(
                    {
                        "input": keys,
                        "pwm_duty": {f"GPIO{g}": got.get(g) for g in en},
                        "expected_duty": want_duty,
                        "pass": all(got.get(g) == want_duty for g in en),
                    }
                )
    (fw / "main.py").write_text(main_py)
    report = {
        "pins": {n: f"GPIO{g}" for n, g in gpio.items()},
        "tests": tests,
        "pass": all(t["pass"] for t in tests),
    }
    (fw / "test.json").write_text(json.dumps(report, indent=1))
    if not report["pass"]:
        return "the firmware test failed: " + json.dumps(
            [t for t in tests if not t["pass"]]
        )
    return scen, report


def _build_sound(
    design: dict[str, Any], part: dict[str, Any], piece: dict[str, Any], out: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]] | str:
    """Sound-reactive firmware: test it with a quiet and a speaking microphone signal."""
    fwd = design["firmware"]
    gpio = _gpio_by_net(part, piece)
    adc_net = fwd.get("input_net")
    adc = gpio.pop(adc_net, None) if adc_net else None
    if adc is None or adc not in (26, 27, 28):
        return f"the microphone output net {adc_net!r} must be wired to an ADC pin (GPIO26, 27 or 28)"
    unknown = sorted(
        {
            n
            for k in ("loud_high", "quiet_high")
            for n in fwd.get(k, [])
            if n not in gpio
        }
    )
    if unknown:
        return f"firmware drives nets {unknown}, but no {part['ref']} GPIO pin is wired to them"
    main_py = generate_sound(fwd, gpio, adc, str(design.get("title", "ETCH BOARD")))
    fw = out / "firmware"
    fw.mkdir(exist_ok=True)
    tests, scen = [], []
    with tempfile.TemporaryDirectory() as td:
        for name, key, does in (
            ("quiet", "quiet_high", "nobody speaking"),
            ("speaking", "loud_high", "someone speaking"),
        ):
            got = run(main_py, "", Path(td), sound=name)
            got.pop(adc, None)
            want = {gpio[n] for n in fwd.get(key, [])}
            high = {n for n, v in got.items() if v}
            tests.append(
                {
                    "input": name,
                    "does": does,
                    "high_gpio": sorted(high),
                    "expected": sorted(want),
                    "pass": high == want,
                }
            )
            scen.append(
                {
                    "name": name,
                    "gpio": {f"GPIO{n}": v for n, v in got.items()},
                    "sound": name,
                    "inputs": [f"GPIO{adc}"],
                }
            )
    (fw / "main.py").write_text(main_py)
    report = {
        "pins": {
            **{n: f"GPIO{g}" for n, g in gpio.items()},
            adc_net: f"GPIO{adc} (ADC)",
        },
        "tests": tests,
        "pass": all(t["pass"] for t in tests),
    }
    (fw / "test.json").write_text(json.dumps(report, indent=1))
    if not report["pass"]:
        return "the firmware test failed: " + json.dumps(
            [t for t in tests if not t["pass"]]
        )
    return scen, report


ADC_GPIO = (26, 27, 28)


def generate_readout(inputs: dict[str, int], title: str) -> str:
    """A starting program for a board whose microcontroller reads sensors: it prints every wired input twice a
    second over USB serial (ADC pins as 0-65535 counts, the rest as 0/1), with the pin map at the top."""
    rows = "\n".join(
        (
            f'    "{net}": ADC(Pin({g})),'
            if g in ADC_GPIO
            else f'    "{net}": Pin({g}, Pin.IN),'
        )
        for net, g in sorted(inputs.items(), key=lambda x: x[1])
    )
    return f"""# {title}: MicroPython for the Raspberry Pi Pico on this board. Generated and tested by Etch.
# A starting program: it prints every input wired to the Pico twice a second over USB serial, so you can watch
# the sensors respond before writing your own logic. Flash: hold BOOTSEL, plug the Pico in, copy the MicroPython
# .uf2 onto it, then save this file as main.py.
import time
from machine import ADC, Pin

INPUTS = {{
{rows}
}}


def read(dev):
    return dev.read_u16() if hasattr(dev, "read_u16") else dev.value()


while True:
    print(" ".join(f"{{net}}={{read(dev)}}" for net, dev in INPUTS.items()))
    time.sleep(0.5)
"""


def _build_readout(
    design: dict[str, Any], part: dict[str, Any], piece: dict[str, Any], out: Path
):
    """No typed commands and no sound mode: the board's microcontroller reads what is wired to it. The generated
    program is run under the stand-in machine module; the test passes when it reads every wired input."""
    gpio = _gpio_by_net(part, piece)
    if not gpio:
        return f"the microcontroller {part['ref']} has no GPIO pin wired to anything"
    main_py = generate_readout(gpio, str(design.get("title", "ETCH BOARD")))
    # the test version stops after a few prints instead of looping forever
    test_py = main_py.replace("while True:", "for _ in range(3):").replace(
        "time.sleep(0.5)", "pass"
    )
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        (work / "main.py").write_text(test_py)
        (work / "fake_machine.py").write_text(_FAKE_MACHINE)
        (work / "runner.py").write_text(_RUNNER)
        proc = subprocess.run(
            [
                sys.executable,
                "-I",
                str(work / "runner.py"),
                str(work / "main.py"),
                str(work / "fake_machine.py"),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
    lines = [
        x for x in proc.stdout.splitlines() if "=" in x and not x.startswith("STATE ")
    ]
    ok = (
        proc.returncode == 0
        and len(lines) == 3
        and all(f"{n}=" in lines[0] for n in gpio)
    )
    tests = [
        {"input": "readout", "reads": sorted(gpio), "printed": lines[:1], "pass": ok}
    ]
    if not ok:
        return "the firmware test failed: " + (proc.stderr or proc.stdout)[-300:]
    fw = out / "firmware"
    fw.mkdir(exist_ok=True)
    (fw / "main.py").write_text(main_py)
    report = {
        "pins": {
            n: f"GPIO{g}" + (" (ADC)" if g in ADC_GPIO else "") for n, g in gpio.items()
        },
        "tests": tests,
        "pass": True,
    }
    (fw / "test.json").write_text(json.dumps(report, indent=1))
    scen = design.get("scenarios") or [{"name": "idle"}]
    return [dict(sc, gpio={}) if "gpio" not in sc else sc for sc in scen], report


CP_LIB = Path(__file__).resolve().parent / "firmware_packs" / "circuitpython_lib"
_SUPPLY = {"GND", "VBUS", "VSYS", "P3V3", "3V3", "VCC", "5V", "+5V", "+3V3"}
STRIP_LEDS = 15  # what the recipe's header note allows on USB power


def drivers(
    design: dict[str, Any], kept: list[dict[str, Any]], gpio: dict[str, int]
) -> list[dict[str, Any]]:
    """Parts that need a real protocol driver, not a raw pin read: a DHT11/DHT22 data line or a WS2812 (NeoPixel)
    data line on a GPIO. [{"kind": "dht11" | "dht22" | "neopixel", "ref", "gpio"}]."""
    by_key = {p["key"]: p for p in kept}
    res = [
        p
        for p in design.get("parts", [])
        if (by_key.get(p["piece"]) or {}).get("family") == "resistor"
    ]
    out = []
    for p in design.get("parts", []):
        label = f"{p.get('piece', '')} {p.get('value', '')}"
        pins = {str(k): str(v) for k, v in (p.get("pins") or {}).items()}
        names = (by_key.get(p.get("piece")) or {}).get("pins") or {}
        if m := re.search(r"DHT ?(11|22)", label, re.I):
            data = next(
                (n for k, n in pins.items() if "DATA" in str(names.get(k, "")).upper()),
                pins.get("2"),
            )
            if data in gpio:
                out.append(
                    {
                        "kind": f"dht{m.group(1)}",
                        "ref": p["ref"],
                        "gpio": gpio[data],
                        "net": data,
                    }
                )
        elif re.search(r"ws2812|neopixel|sk6812", label, re.I):
            for net in (n for n in pins.values() if n.upper() not in _SUPPLY):
                drive = net if net in gpio else None
                for r in res:
                    rn = [str(x) for x in r["pins"].values()]
                    if drive is None and len(rn) == 2 and net in rn:
                        other = rn[1] if rn[0] == net else rn[0]
                        drive = other if other in gpio else None
                if drive:
                    out.append(
                        {
                            "kind": "neopixel",
                            "ref": p["ref"],
                            "gpio": gpio[drive],
                            "net": drive,
                        }
                    )
                    break
    return out


def generate_circuitpython(
    inputs: dict[str, int], drv: list[dict[str, Any]], title: str
) -> str:
    """CircuitPython code.py for a board with a DHT sensor and/or a NeoPixel strip, on Adafruit's own libraries
    (lib/adafruit_dht.py, lib/neopixel.py). Prints the readings over USB serial; a knob (ADC) sets the strip's
    colour, otherwise the strip shows the temperature as a colour, otherwise it runs a slow rainbow."""
    dht = next((d for d in drv if d["kind"].startswith("dht")), None)
    strip = next((d for d in drv if d["kind"] == "neopixel"), None)
    used = {d["net"] for d in drv}
    adc = {
        n: g
        for n, g in sorted(inputs.items(), key=lambda x: x[1])
        if g in ADC_GPIO and n not in used
    }
    knob = next(iter(adc), None)
    lines = [
        f"# {title}: CircuitPython for the Raspberry Pi Pico on this board. Generated and tested by Etch.",
        "# Setup: install CircuitPython for the Pico (circuitpython.org/board/raspberry_pi_pico), then copy this file",
        "# as code.py and the lib/ folder onto the CIRCUITPY drive. The libraries are Adafruit's own (see",
        "# lib/PROVENANCE.md). Readings print over USB serial.",
        "import time",
        "",
        "import board",
    ]
    if adc:
        lines.append("import analogio")
    if dht:
        lines.append("import adafruit_dht")
    if strip:
        lines.append("import neopixel")
    lines.append("")
    if dht:
        lines.append(
            f"dht = adafruit_dht.{dht['kind'].upper()}(board.GP{dht['gpio']})  # {dht['ref']} data"
        )
    if strip:
        lines.append(
            f"strip = neopixel.NeoPixel(board.GP{strip['gpio']}, {STRIP_LEDS}, brightness=0.3, auto_write=False)"
            f"  # {strip['ref']}, {STRIP_LEDS} LEDs on USB power"
        )
    for n, g in adc.items():
        lines.append(f"{n.lower()} = analogio.AnalogIn(board.GP{g})")
    lines += [] if not strip else [
        "",
        "",
        "def wheel(pos):",
        '    """0-255 around the colour wheel -> (r, g, b)."""',
        "    pos %= 256",
        "    if pos < 85:",
        "        return (255 - pos * 3, pos * 3, 0)",
        "    if pos < 170:",
        "        pos -= 85",
        "        return (0, 255 - pos * 3, pos * 3)",
        "    pos -= 170",
        "    return (pos * 3, 0, 255 - pos * 3)",
    ]
    lines += [
        "",
        "",
        "step = 0",
        "temp_c = None",
        "while True:",
        "    out = []",
    ]
    if dht:
        lines += [
            "    try:",
            "        temp_c = dht.temperature",
            '        out.append(f"temperature_c={temp_c} humidity_pct={dht.humidity}")',
            "    except RuntimeError as e:",
            "        # the DHT protocol misses a read now and then; Adafruit's guide says to just try again",
            '        out.append(f"dht_retry={e}")',
        ]
    for n in adc:
        lines.append(f'    out.append(f"{n}={{{n.lower()}.value}}")')
    if strip:
        if knob:
            lines.append(
                f"    strip.fill(wheel({knob.lower()}.value >> 8))  # the knob picks the colour"
            )
        elif dht:
            lines.append("    # 15 C shows blue, 35 C shows red")
            lines.append(
                "    strip.fill(wheel(170 + int(max(0, min(20, (temp_c or 25) - 15)) * 4.25)) if temp_c is not None "
                "else (0, 0, 40))"
            )
        else:
            lines.append(
                f"    for i in range({STRIP_LEDS}):\n        strip[i] = wheel(step + i * 256 // {STRIP_LEDS})"
            )
        lines.append("    strip.show()")
        lines.append('    out.append(f"strip={strip[0]}")')
    lines += [
        '    print(" ".join(out))',
        "    step += 8",
        f"    time.sleep({2.0 if dht else 0.1})",
        "",
    ]
    return "\n".join(lines)


_CP_FAKES = """
import sys, types, json, os

def mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

class _Pin:
    def __init__(self, n): self.n = n
    def __repr__(self): return f"board.GP{self.n}"

board = mod("board")
for _n in range(29):
    setattr(board, f"GP{_n}", _Pin(_n))
mc = mod("microcontroller"); mc.Pin = _Pin

dio = mod("digitalio")
class Direction: INPUT = 0; OUTPUT = 1
class Pull: UP = 1; DOWN = 2
class DigitalInOut:
    def __init__(self, pin): self.pin = pin; self.direction = Direction.INPUT; self.value = False
    def switch_to_output(self, value=False): self.direction = Direction.OUTPUT; self.value = value
    def deinit(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
dio.Direction, dio.Pull, dio.DigitalInOut = Direction, Pull, DigitalInOut

ana = mod("analogio")
class AnalogIn:
    def __init__(self, pin): self.pin = pin; self.value = 40000  # a knob a bit past the middle
ana.AnalogIn = AnalogIn

# a DHT11 answer for 23.0 C and 41 % (bytes 41 0 23 0 checksum 64): 40 bits, each a ~50 us low then a high of
# ~26 us (0) or ~70 us (1); PulseIn hands them over low/high/low/high as the library expects
def _dht11_pulses(h=41, t=23):
    data = [h, 0, t, 0, (h + t) & 0xFF]
    out = []
    for byte in data:
        for i in range(7, -1, -1):
            out += [50, 70 if (byte >> i) & 1 else 26]
    return out + [50]

pio = mod("pulseio")
class PulseIn:
    def __init__(self, pin, maxlen=81, idle_state=True): self.pin = pin; self._q = []
    def pause(self): pass
    def clear(self): self._q = []
    def resume(self, trig=0): self._q = _dht11_pulses()
    def popleft(self): return self._q.pop(0)
    def __len__(self): return len(self._q)
    def __bool__(self): return bool(self._q)
    def deinit(self): pass
pio.PulseIn = PulseIn

NPX = {}
nw = mod("neopixel_write")
def neopixel_write(pin, buf): NPX[repr(getattr(pin, "pin", pin))] = list(bytes(buf))
nw.neopixel_write = neopixel_write

import time as _t
_t.sleep = lambda s: None  # the test does not wait for real time; the DHT's 2 s rule is kept by monotonic()
_clock = [0.0]
def _mono():
    _clock[0] += 2.5
    return _clock[0]
_t.monotonic = _mono
"""

_CP_RUNNER = """
import sys, json
sys.path[:0] = [sys.argv[3], sys.argv[4]]
exec(open(sys.argv[2]).read())
try:
    exec(compile(open(sys.argv[1]).read(), "code.py", "exec"), {"__name__": "__main__"})
except SystemExit:
    pass
print("NPX " + json.dumps(NPX))
"""


def _build_circuitpython(
    design: dict[str, Any],
    part: dict[str, Any],
    gpio: dict[str, int],
    drv: list[dict[str, Any]],
    out: Path,
):
    """code.py on Adafruit's libraries, run on the laptop with stand-ins for the chip modules only. Passes when the
    DHT driver decodes the replayed sensor answer (23 C, 41 %) and the strip gets a full frame of colour bytes."""
    code = generate_circuitpython(gpio, drv, str(design.get("title", "ETCH BOARD")))
    test_py = code.replace("while True:", "for _ in range(3):")
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        (work / "code.py").write_text(test_py)
        (work / "fakes.py").write_text(_CP_FAKES)
        (work / "runner.py").write_text(_CP_RUNNER)
        proc = subprocess.run(
            [
                sys.executable,
                "-I",
                str(work / "runner.py"),
                str(work / "code.py"),
                str(work / "fakes.py"),
                str(CP_LIB),
                str(CP_LIB / "test_only"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
    printed = [
        x for x in proc.stdout.splitlines() if "=" in x and not x.startswith("NPX ")
    ]
    npx = json.loads(
        next((x[4:] for x in proc.stdout.splitlines() if x.startswith("NPX ")), "{}")
        or "{}"
    )
    tests = []
    for d in drv:
        if d["kind"].startswith("dht"):
            ok = any(
                "temperature_c=23" in x and "humidity_pct=41" in x for x in printed
            )
            tests.append(
                {
                    "input": f"{d['ref']} DHT answer 23 C / 41 %",
                    "printed": printed[:1],
                    "pass": ok,
                }
            )
        else:
            frame = npx.get(f"board.GP{d['gpio']}") or []
            ok = len(frame) == 3 * STRIP_LEDS and any(frame)
            tests.append(
                {
                    "input": f"{d['ref']} strip frame",
                    "bytes": len(frame),
                    "first_pixel_grb": frame[:3],
                    "pass": ok,
                }
            )
    if proc.returncode != 0 or not printed or not all(t["pass"] for t in tests):
        return "the firmware test failed: " + (
            (proc.stderr or "")[-300:] or json.dumps(tests)[:300]
        )
    fw = out / "firmware"
    lib = fw / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (fw / "code.py").write_text(code)
    for f in (
        ("adafruit_dht.py", "LICENSE-adafruit_dht")
        if any(d["kind"].startswith("dht") for d in drv)
        else ()
    ):
        (lib / f).write_bytes((CP_LIB / f).read_bytes())
    for f in (
        ("neopixel.py", "LICENSE-neopixel")
        if any(d["kind"] == "neopixel" for d in drv)
        else ()
    ):
        (lib / f).write_bytes((CP_LIB / f).read_bytes())
    (lib / "PROVENANCE.md").write_bytes((CP_LIB / "PROVENANCE.md").read_bytes())
    report = {
        "runtime": "CircuitPython",
        "pins": {
            **{
                n: f"GPIO{g}" + (" (ADC)" if g in ADC_GPIO else "")
                for n, g in gpio.items()
            },
            **{d["net"]: f"GPIO{d['gpio']} ({d['kind']})" for d in drv},
        },
        "tests": tests,
        "pass": True,
    }
    (fw / "test.json").write_text(json.dumps(report, indent=1))
    scen = design.get("scenarios") or [{"name": "idle"}]
    return [dict(sc, gpio={}) if "gpio" not in sc else sc for sc in scen], report


def tie_grounds(design: dict[str, Any], kept: list[dict[str, Any]]) -> None:
    """Every pin named GND on the module joins GND (the schematic symbol already joins them). AGND stays
    apart: it is its own power pin on the symbol, and the module ties it to ground on its own board."""
    found = mcu_part(design, kept)
    if found:
        part, piece = found
        for pin in piece["spice"].get("gnd") or []:
            if piece["pins"].get(str(pin), "").upper() == "GND":
                part["pins"][str(pin)] = "GND"
            elif part["pins"].get(str(pin)) == "GND":
                del part["pins"][str(pin)]


def _channels(
    design: dict[str, Any], kept: list[dict[str, Any]], gpio: dict[str, int]
) -> list[tuple[str, str]]:
    """(metric ref, GPIO net) for every LED die whose anode resistor hangs off a GPIO net, e.g. ("D1_B", "LED_B")."""
    by_key = {p["key"]: p for p in kept}
    res = [
        p
        for p in design["parts"]
        if (by_key.get(p["piece"]) or {}).get("family") == "resistor"
    ]
    out = []
    for p in design["parts"]:
        fam = (by_key.get(p["piece"]) or {}).get("family")
        if fam == "RGB LED":
            dies = [
                (f"{p['ref']}_{c}", p["pins"].get(n))
                for c, n in (("R", "1"), ("G", "3"), ("B", "4"))
            ]
        elif fam == "LED":
            a = next(
                (
                    n
                    for n, nm in by_key[p["piece"]]["pins"].items()
                    if nm.upper() == "A"
                ),
                "1",
            )
            dies = [(p["ref"], p["pins"].get(a))]
        else:
            continue
        for ref, anode in dies:
            for r in res:
                nets = list(r["pins"].values())
                if anode in nets and len(nets) == 2:
                    drive = nets[1] if nets[0] == anode else nets[0]
                    if drive in gpio:
                        out.append((ref, drive))
    return out


def default_commands(design: dict[str, Any], kept: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """LEDs hang off Pico GPIO nets but the picker wrote no typed commands: without any, the board is treated as a
    readout, every GPIO is an input and no LED can ever light (QA 10-17, live-1792255105003: an RGB board whose
    three channels were "read" as ADC counts). Fill in the obvious program instead: digit N lights channel N,
    "a" lights all of them, "o" turns everything off. Returns the commands it wrote, or None when it left the
    design alone (no MCU, sound or "drives" firmware, commands already driving something, or no GPIO-driven LED)."""
    found = mcu_part(design, kept)
    if not found:
        return None
    fwd = design.get("firmware") or {}
    if fwd.get("mode") == "sound" or fwd.get("drives") or any(c.get("high") for c in fwd.get("commands") or []):
        return None
    part, piece = found
    nets = list(dict.fromkeys(net for _, net in _channels(design, kept, _gpio_by_net(part, piece))))
    if not nets:
        return None
    cmds = [{"input": str(i), "high": [n], "does": f"lights {n}"} for i, n in enumerate(nets[:9], 1)]
    if len(nets) > 1:
        cmds.append({"input": "a", "high": nets, "does": "lights every LED"})
    cmds.append({"input": "o", "high": [], "does": "turns every LED off"})
    design["firmware"] = {**fwd, "commands": cmds}
    return cmds


def _readout(
    design: dict[str, Any],
    kept: list[dict[str, Any]],
    part: dict[str, Any],
    piece: dict[str, Any],
) -> bool:
    """A board whose program only reads inputs: no command drives a net high, or no LED hangs off a GPIO net
    (a sensor plus display board). Sound boards always drive LEDs."""
    fwd = design.get("firmware") or {}
    if fwd.get("mode") == "sound" or fwd.get("drives"):
        return False  # sound boards drive LEDs; a "drives" board (motor car) drives off-board loads by command
    if not any(c.get("high") for c in fwd.get("commands") or []):
        return True
    return not _channels(design, kept, _gpio_by_net(part, piece))


def benchmarks(
    design: dict[str, Any], kept: list[dict[str, Any]]
) -> list[dict[str, Any]] | str:
    """ON / OFF checks per LED die and scenario, read from the firmware table: the simulation must light exactly
    what the tested program drives. A GPIO-driven die is ON at 1 mA or more (a 3.3 V pin lights a blue die at
    about 1.5 mA), OFF at 0.5 mA or less, and never above 25 mA."""
    part, piece = mcu_part(design, kept)
    gpio = _gpio_by_net(part, piece)
    ch = _channels(design, kept, gpio)
    fwd = design.get("firmware") or {}
    if _readout(design, kept, part, piece):
        # a readout board: the picker's own benchmarks stand (the program drives no outputs)
        return list(design.get("benchmarks") or [])
    if not ch and fwd.get("drives"):
        # command firmware driving loads, not LEDs (motor car): the design's own per-scenario load checks stand
        return list(design.get("benchmarks") or [])
    if not ch:
        return "no LED is driven from a GPIO net through its own resistor (GPIO net -> resistor -> LED anode)"
    commands = fwd.get("commands") or []
    if fwd.get("mode") == "sound":
        cases = [
            ("quiet", set(fwd.get("quiet_high", [])), "nobody speaking"),
            ("speaking", set(fwd.get("loud_high", [])), "someone speaking"),
        ]
        first = "speaking"
    else:
        cases = [("idle", set(), "nothing typed")] + [
            (f"cmd_{str(c['input'])[:1]}", set(c.get("high", [])), c.get("does", ""))
            for c in commands
        ]
        first = f"cmd_{str(commands[0]['input'])[:1]}" if commands else "idle"
    rows = []
    for name, high, does in cases:
        for ref, net in ch:
            if net in high:
                rows.append(
                    {
                        "check": f"{name}.{ref}.avg_ma",
                        "min": 1.0,
                        "text": f"{ref} lit: {does}",
                    }
                )
            else:
                rows.append(
                    {
                        "check": f"{name}.{ref}.avg_ma",
                        "max": 0.5,
                        "text": f"{ref} dark ({name})",
                    }
                )
    rows += [
        {"check": f"{first}.{ref}.peak_ma", "max": 25.0, "text": f"{ref} under 25 mA"}
        for ref, _ in ch
    ]
    return rows

"""Firmware for boards built on a bare Espressif Wi-Fi module (ESP32-S3-WROOM-1, ESP32-C3-MINI-1).

MicroPython, because its ESP32 port ships the `dht`, `neopixel` and `network` modules built in, so the program
needs no extra libraries on the board. A sensor board gets a readout program: it prints every reading over USB
serial twice a second and, when WIFI_SSID is filled in, also serves the latest readings as JSON on port 80.
Command boards (typed keys drive pins) reuse firmware.generate(): its MicroPython is chip-neutral, only the
header and flashing steps change.

Every program is run on the laptop under stand-in `machine`, `dht`, `neopixel`, `network` and `socket` modules
before the board is accepted, like the Pico programs in firmware.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

CHIPS = {
    "esp32s3": {
        "name": "ESP32-S3",
        "adc": tuple(
            range(1, 11)
        ),  # ADC1_CH0..9 = GPIO1..10 (ADC2 stops working while Wi-Fi is on)
        "fw": "https://micropython.org/download/ESP32_GENERIC_S3/",
        "usb": "native USB on GPIO19/GPIO20",
    },
    "esp32c3": {
        "name": "ESP32-C3",
        "adc": (0, 1, 2, 3, 4),  # ADC1_CH0..4
        "fw": "https://micropython.org/download/ESP32_GENERIC_C3/",
        "usb": "native USB Serial/JTAG on GPIO18/GPIO19",
    },
}
STRIP_LEDS = 15


def chip_of(piece: dict[str, Any]) -> str | None:
    return (piece.get("spice") or {}).get("chip")


def flash_steps(chip: str) -> str:
    c = CHIPS[chip]
    return (
        f"# Flash: hold BOOT, tap RESET (the {c['name']} enters its ROM loader over {c['usb']}), then\n"
        f"#   esptool.py --chip {chip} erase_flash && esptool.py --chip {chip} write_flash 0 <MicroPython .bin>\n"
        f"# (firmware: {c['fw']}). Tap RESET, then save this file as main.py (Thonny: MicroPython (ESP32))."
    )


def retarget(main_py: str, chip: str) -> str:
    """firmware.generate() output with the Pico header and BOOTSEL steps swapped for the ESP32 ones."""
    name = CHIPS[chip]["name"]
    lines = main_py.split("\n")
    body = [
        x
        for x in lines
        if not x.startswith("# Flash:") and not x.startswith("# (Thonny:")
    ]
    out = []
    for x in body:
        if x.startswith("# ") and "Raspberry Pi Pico" in x and not out:
            x = x.replace("the Raspberry Pi Pico", f"the {name} module")
            out.append(x)
            out.extend(flash_steps(chip).split("\n"))
            continue
        out.append(x.replace("the Pico's serial port", f"the {name}'s USB serial port"))
    return "\n".join(out)


def generate_readout(
    gpio: dict[str, int], drv: list[dict[str, Any]], title: str, chip: str
) -> str:
    """MicroPython main.py: DHT and NeoPixel through the built-in drivers, ADC pins as 0-65535 counts, other
    inputs as 0/1. Wi-Fi is off until WIFI_SSID is set."""
    c = CHIPS[chip]
    dht = next((d for d in drv if d["kind"].startswith("dht")), None)
    strip = next((d for d in drv if d["kind"] == "neopixel"), None)
    used = {d["net"] for d in drv}
    rows = []
    for net, g in sorted(gpio.items(), key=lambda x: x[1]):
        if net in used:
            continue
        if g in c["adc"]:
            rows.append(f'    "{net}": ADC(Pin({g}), atten=ADC.ATTN_11DB),')
        else:
            rows.append(f'    "{net}": Pin({g}, Pin.IN),')
    knob = next((n for n, g in gpio.items() if n not in used and g in c["adc"]), None)
    head = [
        f"# {title}: MicroPython for the {c['name']} module on this board. Generated and tested by Etch.",
        "# It prints every reading over USB serial twice a second. Fill in WIFI_SSID / WIFI_PASSWORD to also serve",
        "# the latest readings as JSON at http://<board ip>/ on your network (the IP is printed at start).",
        *flash_steps(chip).split("\n"),
        "import json",
        "import time",
        "",
        "from machine import ADC, Pin",
    ]
    if dht:
        head.append("import dht")
    if strip:
        head.append("import neopixel")
    head += [
        "",
        'WIFI_SSID = ""',
        'WIFI_PASSWORD = ""',
        "",
        "INPUTS = {",
        *rows,
        "}",
    ]
    if dht:
        head.append(
            f"sensor = dht.{dht['kind'].upper()}(Pin({dht['gpio']}))  # {dht['ref']} data"
        )
    if strip:
        head.append(
            f"strip = neopixel.NeoPixel(Pin({strip['gpio']}), {STRIP_LEDS})  # {strip['ref']} data"
        )
    body = [
        "",
        "",
        "def read(dev):",
        '    return dev.read_u16() if hasattr(dev, "read_u16") else dev.value()',
        "",
        "",
        "def wheel(pos):",
        "    pos %= 255",
        "    if pos < 85:",
        "        return (255 - pos * 3, pos * 3, 0)",
        "    if pos < 170:",
        "        pos -= 85",
        "        return (0, 255 - pos * 3, pos * 3)",
        "    pos -= 170",
        "    return (pos * 3, 0, 255 - pos * 3)",
        "",
        "",
        "def wifi():",
        "    if not WIFI_SSID:",
        "        return None",
        "    import network",
        "    import socket",
        "",
        "    wlan = network.WLAN(network.STA_IF)",
        "    wlan.active(True)",
        "    wlan.connect(WIFI_SSID, WIFI_PASSWORD)",
        "    for _ in range(40):",
        "        if wlan.isconnected():",
        "            break",
        "        time.sleep(0.25)",
        "    if not wlan.isconnected():",
        '        print("wifi: could not join", WIFI_SSID)',
        "        return None",
        "    s = socket.socket()",
        "    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)",
        '    s.bind(("0.0.0.0", 80))',
        "    s.listen(2)",
        "    s.settimeout(0)",
        '    print("wifi: serving readings at http://%s/" % wlan.ifconfig()[0])',
        "    return s",
        "",
        "",
        "def serve(srv, latest):",
        "    try:",
        "        conn, _ = srv.accept()",
        "    except OSError:",
        "        return",
        "    try:",
        "        conn.recv(512)",
        "        body = json.dumps(latest)",
        '        conn.send("HTTP/1.0 200 OK\\r\\nContent-Type: application/json\\r\\n\\r\\n" + body)',
        "    finally:",
        "        conn.close()",
        "",
        "",
        "srv = wifi()",
        "tick = 0",
        "while True:",
        "    latest = {net: read(dev) for net, dev in INPUTS.items()}",
    ]
    if dht:
        body += [
            "    try:",
            "        sensor.measure()",
            '        latest["temperature_c"] = sensor.temperature()',
            '        latest["humidity_pct"] = sensor.humidity()',
            "    except OSError:",
            '        latest["dht"] = "no answer"',
        ]
    if strip:
        if knob:
            body.append(f'    colour = wheel(latest["{knob}"] * 255 // 65535)')
        elif dht:
            body.append(
                '    colour = wheel(170 - 6 * int(latest.get("temperature_c", 25) - 10))'
            )
        else:
            body.append("    colour = wheel(tick * 8)")
        body += [
            "    for i in range(len(strip)):",
            "        strip[i] = tuple(v // 4 for v in colour)",
            "    strip.write()",
        ]
    body += [
        '    print(" ".join("%s=%s" % kv for kv in latest.items()))',
        "    if srv:",
        "        for _ in range(10):",
        "            serve(srv, latest)",
        "            time.sleep(0.05)",
        "    else:",
        "        time.sleep(0.5)",
        "    tick += 1",
        "",
    ]
    return "\n".join(head + body)


# stand-ins for the MicroPython ESP32 modules: the DHT answers 23 C / 41 %, the NeoPixel records its last frame,
# the WLAN joins at once, and the server socket hands over one HTTP request
_FAKES = """
import json, sys, types

def mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

machine = mod("machine")
class Pin:
    IN, OUT, PULL_UP = 0, 1, 2
    def __init__(self, n, mode=None, pull=None): self.n = n
    def value(self, v=None): return 0
machine.Pin = Pin
class ADC:
    ATTN_11DB = 3
    def __init__(self, pin, atten=None): self.pin = pin; self.atten = atten
    def read_u16(self): return 40000
machine.ADC = ADC

dht = mod("dht")
class _DHT:
    def __init__(self, pin): self.pin = pin
    def measure(self): pass
    def temperature(self): return 23
    def humidity(self): return 41
dht.DHT11 = dht.DHT22 = _DHT

neopixel = mod("neopixel")
FRAMES = {}
class NeoPixel:
    def __init__(self, pin, n): self.pin = pin; self.buf = [(0, 0, 0)] * n
    def __len__(self): return len(self.buf)
    def __setitem__(self, i, v): self.buf[i] = tuple(v)
    def write(self): FRAMES[self.pin.n] = list(self.buf)
neopixel.NeoPixel = NeoPixel

network = mod("network")
network.STA_IF = 0
class WLAN:
    def __init__(self, i): pass
    def active(self, on=None): return True
    def connect(self, ssid, pw): self.ssid = ssid
    def isconnected(self): return True
    def ifconfig(self): return ("192.168.4.20", "255.255.255.0", "192.168.4.1", "192.168.4.1")
network.WLAN = WLAN

socket = mod("socket")
socket.SOL_SOCKET, socket.SO_REUSEADDR = 1, 4
SERVED = []
class _Conn:
    def recv(self, n): return b"GET / HTTP/1.0\\r\\n\\r\\n"
    def send(self, s): SERVED.append(s)
    def close(self): pass
class _Srv:
    calls = 0
    def setsockopt(self, *a): pass
    def bind(self, a): pass
    def listen(self, n): pass
    def settimeout(self, t): pass
    def accept(self):
        _Srv.calls += 1
        if _Srv.calls == 1:
            return _Conn(), ("192.168.4.2", 5000)
        raise OSError(11)
socket.socket = _Srv

import time as _t
_t.sleep = lambda s: None
"""

_RUNNER = """
import json, sys
exec(open(sys.argv[2]).read(), globals())
try:
    exec(compile(open(sys.argv[1]).read(), "main.py", "exec"), {"__name__": "__main__"})
except SystemExit:
    pass
print("NPX " + json.dumps({str(k): v for k, v in FRAMES.items()}))
print("HTTP " + json.dumps(SERVED))
"""


def _run(
    code: str, wifi: bool
) -> tuple[subprocess.CompletedProcess[str], list[str], dict, list[str]]:
    test = code.replace("while True:", "for _ in range(3):")
    if wifi:
        test = test.replace('WIFI_SSID = ""', 'WIFI_SSID = "etch-test"', 1)
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        (work / "main.py").write_text(test)
        (work / "fakes.py").write_text(_FAKES)
        (work / "runner.py").write_text(_RUNNER)
        proc = subprocess.run(
            [
                sys.executable,
                "-I",
                str(work / "runner.py"),
                str(work / "main.py"),
                str(work / "fakes.py"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
    out = proc.stdout.splitlines()
    printed = [
        x for x in out if "=" in x and not x.startswith(("NPX ", "HTTP ", "wifi:"))
    ]
    npx = json.loads(next((x[4:] for x in out if x.startswith("NPX ")), "{}") or "{}")
    http = json.loads(next((x[5:] for x in out if x.startswith("HTTP ")), "[]") or "[]")
    return proc, printed, npx, http


def build_readout(
    design: dict[str, Any],
    part: dict[str, Any],
    gpio: dict[str, int],
    drv: list[dict[str, Any]],
    chip: str,
    out: Path,
):
    """(scenarios, report) or why not. Passes when serial prints every input three times, the DHT reading decodes,
    the strip gets a full frame, and with Wi-Fi on one HTTP request gets the readings as JSON."""
    if not gpio:
        return f"the microcontroller {part['ref']} has no GPIO pin wired to anything"
    code = generate_readout(gpio, drv, str(design.get("title", "ETCH BOARD")), chip)
    proc, printed, npx, _ = _run(code, wifi=False)
    if proc.returncode != 0:
        return "the firmware test failed: " + (proc.stderr or proc.stdout)[-300:]
    used = {d["net"] for d in drv}
    plain = [n for n in gpio if n not in used]
    tests = [
        {
            "input": "serial readout",
            "reads": sorted(plain),
            "printed": printed[:1],
            "pass": len(printed) == 3 and all(f"{n}=" in printed[0] for n in plain),
        }
    ]
    for d in drv:
        if d["kind"].startswith("dht"):
            tests.append(
                {
                    "input": f"{d['ref']} DHT answer 23 C / 41 %",
                    "printed": printed[:1],
                    "pass": bool(printed)
                    and "temperature_c=23" in printed[0]
                    and "humidity_pct=41" in printed[0],
                }
            )
        else:
            frame = npx.get(str(d["gpio"])) or []
            tests.append(
                {
                    "input": f"{d['ref']} strip frame",
                    "pixels": len(frame),
                    "pass": len(frame) == STRIP_LEDS and any(any(px) for px in frame),
                }
            )
    proc_w, _, _, http = _run(code, wifi=True)
    body = http[0].split("\r\n\r\n", 1)[-1] if http else ""
    try:
        served = json.loads(body) if body else {}
    except ValueError:
        served = {}
    tests.append(
        {
            "input": "Wi-Fi on: one HTTP GET",
            "served_keys": sorted(served),
            "pass": proc_w.returncode == 0
            and bool(http)
            and all(n in served for n in plain),
        }
    )
    kinds = {d["net"]: d["kind"] for d in drv}
    report = {
        "pins": {
            n: f"GPIO{g}"
            + (
                f" ({kinds[n]})"
                if n in kinds
                else " (ADC)"
                if g in CHIPS[chip]["adc"]
                else ""
            )
            for n, g in gpio.items()
        },
        "tests": tests,
        "pass": all(t["pass"] for t in tests),
        "runtime": f"MicroPython ({CHIPS[chip]['name']})",
    }
    if not report["pass"]:
        return "the firmware test failed: " + json.dumps(
            [t for t in tests if not t["pass"]]
        )
    fw = out / "firmware"
    fw.mkdir(exist_ok=True)
    (fw / "main.py").write_text(code)
    (fw / "test.json").write_text(json.dumps(report, indent=1))
    scen = design.get("scenarios") or [{"name": "idle"}]
    return [dict(sc, gpio={}) if "gpio" not in sc else sc for sc in scen], report

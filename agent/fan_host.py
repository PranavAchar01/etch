"""Etch fan board: run this on the computer. It reads the CPU temperature and tells the board over USB serial
to spin the fan above ON_AT and stop below OFF_AT (hysteresis, so the fan does not flick on and off at the edge).

  pip install pyserial psutil
  python fan_host.py                       # finds the Pico's serial port by itself
  python fan_host.py --port /dev/ttyACM0   # or name it
  python fan_host.py --temp-cmd "osx-cpu-temp"   # any command that prints a temperature in C (macOS has no
                                                 # built-in sensor API; osx-cpu-temp or istats print one)

Failsafe, both ends:
  - if this script cannot read a temperature, it turns the fan ON and keeps trying;
  - it repeats the fan state every PERIOD_S seconds; when the board hears nothing for 15 s (this script stopped,
    the computer slept, the cable came out) its own firmware turns the fan ON.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
import time

ON_AT = 80.0  # degrees C: fan on at or above this
OFF_AT = 75.0  # degrees C: fan off below this
PERIOD_S = 2.0
PICO_VID = 0x2E8A  # Raspberry Pi


def decide(
    temp: float | None, fan_on: bool, on_at: float = ON_AT, off_at: float = OFF_AT
) -> bool:
    """Fan state for this reading. No reading means fan on (failsafe)."""
    if temp is None:
        return True
    if temp >= on_at:
        return True
    if temp < off_at:
        return False
    return fan_on  # between the two thresholds: keep doing what it was doing


def read_temp(cmd: str | None) -> float | None:
    """Hottest CPU sensor in degrees C, or None when nothing could be read."""
    if cmd:
        try:
            out = subprocess.run(
                shlex.split(cmd),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        m = re.search(r"-?\d+(?:\.\d+)?", out)
        return float(m.group(0)) if m else None
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        return None
    read = getattr(psutil, "sensors_temperatures", None)
    temps = read() if read else {}
    vals = [t.current for rows in (temps or {}).values() for t in rows if t.current]
    return max(vals) if vals else None


def find_port() -> str | None:
    from serial.tools import list_ports  # noqa: PLC0415

    ports = list(list_ports.comports())
    pico = [p.device for p in ports if p.vid == PICO_VID]
    return (pico or [None])[0]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port")
    ap.add_argument("--on-at", type=float, default=ON_AT)
    ap.add_argument("--off-at", type=float, default=OFF_AT)
    ap.add_argument("--temp-cmd", help="command that prints the temperature in C")
    args = ap.parse_args()
    if args.off_at >= args.on_at:
        sys.exit("--off-at must be below --on-at")
    import serial  # noqa: PLC0415

    port = args.port or find_port()
    if not port:
        sys.exit("no Raspberry Pi Pico found on USB; plug the board in or pass --port")
    fan_on = False
    with serial.Serial(port, 115200, timeout=1) as link:
        print(
            f"fan board on {port}: on at {args.on_at:g} C, off below {args.off_at:g} C"
        )
        while True:
            temp = read_temp(args.temp_cmd)
            now = decide(temp, fan_on, args.on_at, args.off_at)
            if now != fan_on or temp is None:
                shown = "no reading (failsafe)" if temp is None else f"{temp:.1f} C"
                print(
                    f"{time.strftime('%H:%M:%S')} {shown}: fan {'ON' if now else 'off'}"
                )
            fan_on = now
            link.write(
                b"1" if fan_on else b"0"
            )  # sent every period: it doubles as the heartbeat
            time.sleep(PERIOD_S)


if __name__ == "__main__":
    main()

"""ngspice transient run built from the same part/net list as the board.

Models: the 2N3904/2N3906 cards are the widely published Fairchild/onsemi SPICE models.
The LED cards are generic colour-typical diode models, not fitted to the exact LED in the index: forward
voltage at 10 mA is 2.0 V red, 3.0 V green, 3.1 V blue (InGaN green and blue), 2.1 V yellow, 3.1 V white.
Results are labelled that way. Every LED part is a D element with one of these cards; its colour comes from the
part's value (agent/tests/test_qa_rgbpico.py checks the forward voltages in ngspice).
"""

from __future__ import annotations

import concurrent.futures as cf
import itertools
import math
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .spec import Spec

MODELS = {
    "2N3904": ".model 2N3904 NPN(IS=6.734f XTI=3 EG=1.11 VAF=74.03 BF=416.4 NE=1.259 ISE=6.734f IKF=66.78m "
    "XTB=1.5 BR=.7371 NC=2 ISC=0 IKR=0 RC=1 CJC=3.638p MJC=.3085 VJC=.75 FC=.5 CJE=4.493p MJE=.2593 "
    "VJE=.75 TR=239.5n TF=301.2p ITF=.4 VTF=4 XTF=2 RB=10)",
    "2N3906": ".model 2N3906 PNP(IS=1.41f XTI=3 EG=1.11 VAF=18.7 BF=180.7 NE=1.5 ISE=0 IKF=80m XTB=1.5 "
    "BR=4.977 NC=2 ISC=0 IKR=0 RC=2.5 CJC=9.728p MJC=.5776 VJC=.75 FC=.5 CJE=8.063p MJE=.3677 "
    "VJE=.75 TR=33.42n TF=179.3p ITF=.4 VTF=4 XTF=6 RB=10)",
    # IS fitted so Vf(10 mA) = 2.0 V red, 3.0 V green, 3.1 V blue / white, 2.1 V yellow (QA 10-17)
    "LED_RED": ".model LED_RED D(IS=1.23e-20 N=1.8 RS=8 BV=5 IBV=10u CJO=10p)",
    "LED_GREEN": ".model LED_GREEN D(IS=3.33e-22 N=2.5 RS=10 BV=5 IBV=10u CJO=10p)",
    "LED_YELLOW": ".model LED_YELLOW D(IS=5.71e-21 N=1.85 RS=9 BV=5 IBV=10u CJO=10p)",
    "LED_BLUE": ".model LED_BLUE D(IS=5.69e-22 N=2.6 RS=12 BV=5 IBV=10u CJO=10p)",
    "LED_WHITE": ".model LED_WHITE D(IS=5.69e-22 N=2.6 RS=12 BV=5 IBV=10u CJO=10p)",
    # plain diodes: widely circulated vendor-style cards (SS14 and 1N5819 are generic 1 A / 40 V Schottky fits)
    "D1N4148": ".model D1N4148 D(IS=2.52n N=1.752 RS=0.568 BV=100 IBV=100u CJO=4p TT=20n)",
    "D1N4007": ".model D1N4007 D(IS=7.02767n N=1.80803 RS=0.0341512 BV=1000 IBV=5u CJO=10p TT=4.32u)",
    "D1N5819": ".model D1N5819 D(IS=31.7u N=1.373 RS=0.051 BV=40 IBV=1m CJO=110p EG=0.69 XTI=2)",
    "SS14": ".model SS14 D(IS=31.7u N=1.373 RS=0.051 BV=40 IBV=1m CJO=110p EG=0.69 XTI=2)",
    # the widely published 2N2222 card (same family as the Fairchild 2N3904 card above)
    "2N2222": ".model 2N2222 NPN(IS=14.34f XTI=3 EG=1.11 VAF=74.03 BF=255.9 NE=1.307 ISE=14.34f IKF=.2847 "
    "XTB=1.5 BR=6.092 NC=2 ISC=0 IKR=0 RC=1 CJC=7.306p MJC=.3416 VJC=.75 FC=.5 CJE=22.01p MJE=.377 "
    "VJE=.75 TR=46.91n TF=411.1p ITF=.6 VTF=1.7 XTF=3 RB=10)",
}

# 1N47xxA zener series: nominal voltage by part number (datasheet table)
_1N47 = {
    28: 3.3,
    29: 3.6,
    30: 3.9,
    31: 4.3,
    32: 4.7,
    33: 5.1,
    34: 5.6,
    35: 6.2,
    36: 6.8,
    37: 7.5,
    38: 8.2,
    39: 9.1,
    40: 10,
    41: 11,
    42: 12,
    43: 13,
    44: 15,
    45: 16,
    46: 18,
    47: 20,
    48: 22,
    49: 24,
}


def zener_voltage(name: str) -> float | None:
    """Nominal zener voltage from a part number: BZX55C5V1 / BZX79-C3V3 / ZPD5.1 / ZMM5V1 / 1N4733A."""
    n = name.upper()
    m = re.search(r"(?:BZX\d\d-?[ABC]|BZV\d\d-?C|ZMM|ZPD|ZY)(\d+)[V.](\d*)", n)
    if m:
        return float(m.group(1) + "." + (m.group(2) or "0"))
    m = re.match(r"1N47(\d\d)", n)
    if m and int(m.group(1)) in _1N47:
        return _1N47[int(m.group(1))]
    return None


def zener_model(name: str) -> str | None:
    """A generic zener card at the part's nominal voltage (not a vendor fit; labelled so in the results)."""
    v = zener_voltage(name)
    if v is None:
        return None
    return f".model {name} D(IS=1e-14 N=1 RS=5 BV={v} IBV=5m CJO=50p)"


def card(model: str) -> str:
    if model in MODELS:
        return MODELS[model]
    z = zener_model(model)
    if z:
        return z
    raise ValueError(f"no SPICE card for {model}")


LED_COLOURS = ("RED", "GREEN", "YELLOW", "BLUE", "WHITE")


def led_model(model: str, value: str) -> str:
    if "{colour}" not in model:
        return model
    v = str(value).upper()
    col = next((c for c in LED_COLOURS if c in v), None)
    if col is None:
        col = {"AMBER": "YELLOW", "ORANGE": "YELLOW", "WARM": "WHITE", "COOL": "WHITE"}.get(
            next((k for k in ("AMBER", "ORANGE", "WARM", "COOL") if k in v), ""), "RED"
        )
    return model.replace("{colour}", col)


def _looks_like_led(value: Any) -> bool:
    v = str(value).upper()
    return "LED" in v or "RGB" in v or any(c in v for c in LED_COLOURS + ("AMBER", "ORANGE"))


def _node(net: str) -> str:
    return "0" if net.upper() in ("GND", "0", "VSS") else net


def scenarios(spec: Spec) -> list[dict[str, Any]]:
    """Named operating conditions, e.g. [{name: dark, ldr: dark}, {name: light, ldr: light}] or
    [{name: v3_0, voltage: 3.0}]. A board with an LDR and no scenarios gets dark + light automatically."""
    sc = list((spec.raw.get("sim") or {}).get("scenarios") or [])
    if not sc and any(p.spice and p.spice.get("role") == "ldr" for p in spec.parts):
        sc = [{"name": "dark", "ldr": "dark"}, {"name": "light", "ldr": "light"}]
    return sc or [{"name": "default"}]


def netlist(
    spec: Spec, t_stop: float, step: float, scen: dict[str, Any] | None = None
) -> tuple[str, list[str], list[str]]:
    scen = scen or {}
    v = float(scen.get("voltage", spec.raw.get("power", {}).get("voltage", 5.0)))
    sets = {str(k): str(x) for k, x in (scen.get("set") or {}).items()}
    lines = [f"* {spec.name} generated by etch, scenario {scen.get('name', 'default')}"]
    models: set[str] = set()
    leds: list[str] = []
    loads: list[str] = []
    supply = None
    for p in spec.parts:
        s = p.spice
        if not s:
            raise ValueError(f"{p.ref}: no spice mapping in the index; cannot simulate")
        nodes = [_node(p.pins.get(n, f"NC_{p.ref}_{n}")) for n in s["pins"]]
        pre = s["prefix"]
        role = s.get("role")
        if role == "mcu" and s.get("rail"):
            # a bare Wi-Fi module (ESP32-S3 / C3) on the board's own 3.3 V rail, not a supply: its draw is a
            # resistor sized to the datasheet Wi-Fi receive current, and its GPIO outputs drive from that rail
            # at the levels the tested firmware sets (high = rail through r_out, low = r_out to ground)
            (v3,) = nodes
            lines.append(f"R{p.ref}_IDD {v3} 0 {s.get('idd_ohms', 35)}")
            for k, gp in enumerate(s.get("gnd") or []):
                nd = _node(p.pins.get(gp, "GND"))
                if nd != "0":
                    lines.append(f"R{p.ref}_G{k} {nd} 0 0.01")
            levels = {str(k).upper(): bool(x) for k, x in (scen.get("gpio") or {}).items()}
            inputs = {str(g).upper() for g in scen.get("inputs") or []}
            for pin, name in (s.get("gpio") or {}).items():
                net = p.pins.get(pin)
                if not net or name.upper() in inputs or net.upper() in inputs:
                    continue
                hi = levels.get(name.upper(), levels.get(net.upper(), False))
                if hi or name.upper() in levels or net.upper() in levels:
                    lines.append(f"R{p.ref}_{name} {_node(net)} {v3 if hi else '0'} {s.get('r_out', 50)}")
            for pin in s.get("reserved") or []:
                # strapping and USB pins are inputs at boot: high impedance, a leak keeps the node defined
                net = p.pins.get(pin)
                if net and _node(net) != "0":
                    lines.append(f"R{p.ref}_RS{pin} {_node(net)} 0 1G")
            continue
        if role == "ldo":
            # fixed linear regulator (AMS1117-3.3): pins [VI, VO, GND]. Output = min(vout, VI - dropout) through a
            # sense source; an F source pulls the same current from VI (so the load counts against the supply),
            # plus the quiescent current as a resistor VI to GND
            vi, vo, g = nodes
            vout, drop = float(s.get("vout", 3.3)), float(s.get("dropout", 1.1))
            lines.append(f"B{p.ref} {p.ref}_O {g} V=max(0, min({vout}, V({vi},{g})-{drop}))")
            lines.append(f"R{p.ref}_O {p.ref}_O {p.ref}_S 0.05")
            lines.append(f"V{p.ref}_S {p.ref}_S {vo} DC 0")
            lines.append(f"F{p.ref} {vi} {g} V{p.ref}_S 1")
            lines.append(f"R{p.ref}_Q {vi} {g} {s.get('iq_ohms', 1000)}")
            continue
        if role == "mcu" and supply is None:
            # a USB microcontroller module is the board's supply: VBUS from the laptop, an internal 3.3 V
            # regulator (series 1.7 V so its current counts against VBUS), and GPIO outputs at the levels
            # the tested firmware sets for this scenario (high = 3.3 V through r_out, low = r_out to ground)
            supply = p.ref
            vbus, vsys, v3 = nodes
            reg = f"{p.ref}_3V3I"
            lines.append(f"V{p.ref} {vbus} 0 PWL(0 0 1m {v})")
            lines.append(f"V{p.ref}_LDO {vbus} {reg} DC {v - float(s.get('v_io', 3.3)):.3f}")
            lines.append(f"R{p.ref}_V3 {reg} {v3} 0.1")
            lines.append(f"R{p.ref}_SYS {vbus} {vsys} 0.5")
            for k, gp in enumerate(s.get("gnd") or []):
                nd = _node(p.pins.get(gp, "GND"))
                if nd != "0":
                    lines.append(f"R{p.ref}_G{k} {nd} 0 0.01")
            levels = {str(k).upper(): bool(x) for k, x in (scen.get("gpio") or {}).items()}
            inputs = {str(g).upper() for g in scen.get("inputs") or []}
            for pin, name in (s.get("gpio") or {}).items():
                net = p.pins.get(pin)
                if not net or name.upper().split("_")[0] in inputs:
                    continue  # an input (an ADC reading a microphone) is high impedance, not a driven pin
                hi = levels.get(name.upper(), levels.get(net.upper(), False))
                lines.append(f"R{p.ref}_{name} {_node(net)} {reg if hi else '0'} {s.get('r_out', 50)}")
            continue
        if role == "rgb":
            # three LED dice on one common cathode: pins [red A, green A, blue A, K]
            ra, ga, ba, k = nodes
            for col, an in (("R", ra), ("G", ga), ("B", ba)):
                m = {"R": "LED_RED", "G": "LED_GREEN", "B": "LED_BLUE"}[col]
                lines.append(f"D{p.ref}_{col} {an} {k} {m}")
                models.add(m)
                leds.append(f"{p.ref}_{col}")
            continue
        if role == "mcu" and (src := next((nodes[i] for i in (1, 0) if s["pins"][i] in p.pins), None)):
            # a module on a board the power connector already feeds (VSYS, or VBUS, wired to that rail): its own
            # 3.3 V regulator runs from that pin (current mirrored back onto it, so it counts against the supply)
            # and the GPIO outputs drive at the tested firmware's levels, as for the supply case above
            # (QA 10-17, live-1792255105003: such a Pico was passive and its LEDs never lit)
            v3 = nodes[2]
            reg = f"{p.ref}_3V3I"
            lines.append(f"B{p.ref} {p.ref}_O 0 V=max(0, min({s.get('v_io', 3.3)}, V({src})-0.3))")
            lines.append(f"V{p.ref}_S {p.ref}_O {reg} DC 0")
            lines.append(f"F{p.ref} {src} 0 V{p.ref}_S 1")
            lines.append(f"R{p.ref}_Q {src} 0 2k")
            lines.append(f"R{p.ref}_V3 {reg} {v3} 0.1")
            for k, gp in enumerate(s.get("gnd") or []):
                nd = _node(p.pins.get(gp, "GND"))
                if nd != "0":
                    lines.append(f"R{p.ref}_G{k} {nd} 0 0.01")
            levels = {str(k).upper(): bool(x) for k, x in (scen.get("gpio") or {}).items()}
            inputs = {str(g).upper() for g in scen.get("inputs") or []}
            for pin, name in (s.get("gpio") or {}).items():
                net = p.pins.get(pin)
                if not net or name.upper().split("_")[0] in inputs:
                    continue
                hi = levels.get(name.upper(), levels.get(net.upper(), False))
                lines.append(f"R{p.ref}_{name} {_node(net)} {reg if hi else '0'} {s.get('r_out', 50)}")
            continue
        if role == "mcu":
            role = "passive"
        if role == "power_in" and supply is not None:
            role = "passive"  # only the first power connector is a source; later ones carry signals
        if role == "power_in":
            supply = p.ref
            # supply ramps up over 1 ms like a real plug-in, which also kicks an astable off
            lines.append(f"V{p.ref} {nodes[0]} {nodes[1]} PWL(0 0 1m {v})")
        elif role == "load":
            # an off-board load on a connector (a fan, motor or buzzer), modelled as its rated resistance
            lines.append(f"R{p.ref} {nodes[0]} {nodes[1]} {sets.get(p.ref, s.get('ohms', 100))}")
            loads.append(p.ref)
        elif role == "hbridge":
            # L293D-style quad half-H driver. pins: VCC1 VCC2 GND EN12 1A 1Y 2A 2Y EN34 3A 3Y 4A 4Y.
            # Logic side: VCC1 to GND as its supply draw (R<ref>, so it still reports like a load). Each output:
            # with its enable high, input high closes a high-side switch to VCC2 and input low a low-side switch
            # to GND; enable low leaves the output open. Switch resistances give the datasheet's typical drops
            # (TI L293D: VOH = VCC2 - 1.4 V, VOL = 1.2 V at 0.6 A), so motor current is drawn from VCC2 and counts
            # against the supply.
            vcc1, vcc2, gnd, en12, a1, y1, a2, y2, en34, a3, y3, a4, y4 = nodes
            lines.append(f"R{p.ref} {vcc1} {gnd} {s.get('ohms', 250)}")
            loads.append(p.ref)
            for k, nd in enumerate((en12, en34, a1, a2, a3, a4)):
                if nd != "0":
                    lines.append(f"R{p.ref}_LK{k} {nd} 0 1G")
            for k, (a, y, en) in enumerate(((a1, y1, en12), (a2, y2, en12), (a3, y3, en34), (a4, y4, en34)), 1):
                hi, lo = f"{p.ref}_H{k}", f"{p.ref}_L{k}"
                lines.append(f"B{p.ref}_H{k} {hi} 0 V=u(V({en})-1.5)*u(V({a})-1.5)")
                lines.append(f"B{p.ref}_L{k} {lo} 0 V=u(V({en})-1.5)*(1-u(V({a})-1.5))")
                lines.append(f"S{p.ref}_H{k} {vcc2} {y} {hi} 0 ETCH_HB_HI")
                lines.append(f"S{p.ref}_L{k} {y} {gnd} {lo} 0 ETCH_HB_LO")
            if not any(x.startswith(".model ETCH_HB_HI") for x in lines):
                lines.append(".model ETCH_HB_HI SW(vt=0.5 vh=0.1 ron=7 roff=1G)")
                lines.append(".model ETCH_HB_LO SW(vt=0.5 vh=0.1 ron=6 roff=1G)")
        elif role == "switch":
            closed = scen.get("switch", "pressed") != "released"
            lines.append(f"R{p.ref} {nodes[0]} {nodes[1]} {'0.05' if closed else '1G'}")
        elif role == "mic":
            # electret microphone + amplifier module (Adafruit MAX4466 breakout, pins VCC, GND, OUT): it draws
            # about 0.5 mA, and its output rests at VCC/2 behind ~125 ohm; scenario sound=speaking adds a
            # 220 Hz, 1 V amplitude voice-band tone, sound=quiet a 10 mV hiss
            vcc, gnd, o = nodes
            amp = 1.0 if scen.get("sound") == "speaking" else 0.01
            hz = float(scen.get("tone_hz") or 220)  # pitch boards sing each band's own test tone
            lines.append(f"R{p.ref}_Q {vcc} {gnd} 6.6k")
            lines.append(f"E{p.ref} {p.ref}_MID {gnd} {vcc} {gnd} 0.5")
            lines.append(f"V{p.ref}_S {p.ref}_SIG {p.ref}_MID SIN(0 {amp} {hz:g})")
            lines.append(f"R{p.ref}_O {p.ref}_SIG {o} 125")
        elif role == "ldr":
            lit = scen.get("ldr", "light")
            lines.append(f"R{p.ref} {nodes[0]} {nodes[1]} {sets.get(p.ref, s.get(lit, '10k'))}")
        elif role == "passive":
            # not driven in simulation: a high-value leak to ground keeps each pin's node defined
            for k, nd in enumerate(nodes):
                if nd != "0":
                    lines.append(f"R{p.ref}_{k} {nd} 0 1G")
        elif pre == "P":
            # trimmer: two resistors meeting at the wiper (pins end, wiper, end), wiper at `pos` (default mid)
            total = sets.get(p.ref, p.value)
            pos = float(s.get("pos", 0.5))
            lines.append(f"R{p.ref}a {nodes[0]} {nodes[1]} {{{_val(total)}*{pos:.3f}+1}}")
            lines.append(f"R{p.ref}b {nodes[1]} {nodes[2]} {{{_val(total)}*{1 - pos:.3f}+1}}")
        elif pre in ("R", "C"):
            val = sets.get(p.ref, p.value)
            try:
                _val(val)
            except ValueError:
                # QA 10-17: three capacitors valued RED / GREEN / BLUE stood in for an RGB LED and gave ngspice
                # "cc1 led_red_a 0 red" (a C element whose value is a missing model). Say what is wrong instead.
                kind = "resistance" if pre == "R" else "capacitance"
                hint = " (an LED must use an LED part, not a resistor or capacitor)" if _looks_like_led(val) else ""
                raise ValueError(f"{p.ref}: value {val!r} is not a {kind}{hint}") from None
            lines.append(f"{pre}{p.ref} {' '.join(nodes)} {val}")
        elif pre == "D":
            m = led_model(s["model"], p.value)
            lines.append(f"D{p.ref} {' '.join(nodes)} {m}")
            models.add(m)
            if m.startswith("LED"):
                leds.append(p.ref)
        elif pre == "Q":
            lines.append(f"Q{p.ref} {' '.join(nodes)} {s['model']}")
            models.add(s["model"])
        else:
            raise ValueError(f"{p.ref}: unsupported spice prefix {pre}")
    if supply is None:
        raise ValueError("no power-in connector; nothing to simulate from")
    lines += [card(m) for m in sorted(models)]
    vecs = " ".join(
        [f"@d{r.lower()}[id]" for r in leds] + [f"i(v{supply.lower()})"] + [f"@r{r.lower()}[i]" for r in loads]
    )
    lines += [
        ".options method=gear reltol=1e-3",
        f".tran {step} {t_stop} 0 {step}",
        ".control",
        "set wr_singlescale",
        "set wr_vecnames",
        f"save all {vecs}",
        "run",
        f"wrdata {{OUT}} {vecs}",
        "quit",
        ".endc",
        ".end",
    ]
    return "\n".join(lines) + "\n", leds, loads


_SI = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12}


def _val(v: str) -> float:
    """'10k' -> 10000.0 (SPICE suffixes; a trailing unit letter such as 'ohm' is ignored)."""
    s = str(v).strip().lower().replace("ω", "").replace("ohm", "")
    m = re.match(r"^([\d.]+)\s*(meg|[tgkmunp])?", s)
    if not m:
        raise ValueError(f"cannot read value {v!r}")
    return float(m.group(1)) * _SI.get(m.group(2) or "", 1.0)


def _blinks(t: list[float], i: list[float], t_settle: float) -> dict[str, Any]:
    peak = max(i) if i else 0.0
    thr = 0.5 * peak
    rises = [t[k] for k in range(1, len(t)) if t[k] >= t_settle and i[k - 1] < thr <= i[k]]
    on = sum(t[k] - t[k - 1] for k in range(1, len(t)) if t[k] >= t_settle and i[k] >= thr and peak > 1e-5)
    span = t[-1] - t_settle
    periods = [b - a for a, b in itertools.pairwise(rises)]
    hz = (len(periods) / (rises[-1] - rises[0])) if len(periods) >= 1 else 0.0
    after = [x for tt, x in zip(t, i) if tt >= t_settle]
    return {
        "peak_ma": round(peak * 1e3, 3),
        "avg_ma": round(sum(after) / max(1, len(after)) * 1e3, 3),
        "blink_hz": round(hz, 3),
        "duty": round(on / span, 3) if span else 0,
        "blinks_counted": len(rises),
        "rise_times_s": [round(x, 4) for x in rises[:12]],
    }


def _run_one(spec: Spec, out_dir: Path, scen: dict[str, Any], t_stop: float, step: float) -> dict[str, Any]:
    ngspice = shutil.which("ngspice") or "/opt/homebrew/bin/ngspice"
    name = scen.get("name", "default")
    text, leds, loads = netlist(spec, t_stop, step, scen)
    sim_dir = out_dir / "sim"
    sim_dir.mkdir(exist_ok=True)
    data = sim_dir / f"tran_{name}.dat"
    cir = sim_dir / f"{spec.name}_{name}.cir"
    cir.write_text(text.replace("{OUT}", str(data)))
    t0 = time.time()
    proc = subprocess.run([ngspice, "-b", str(cir)], check=False, capture_output=True, text=True, timeout=120)
    elapsed = time.time() - t0
    (sim_dir / f"ngspice_{name}.log").write_text(proc.stdout[-20000:] + proc.stderr[-5000:])
    if proc.returncode != 0 or not data.exists():
        raise RuntimeError(f"scenario {name}: " + (proc.stderr[-800:] or proc.stdout[-800:]))
    rows = data.read_text().split("\n")
    cols = [list(map(float, r.split())) for r in rows[1:] if r.strip()]
    t = [c[0] for c in cols]
    settle = 1.0 if t_stop >= 3 else t_stop / 3
    outs, traces = {}, {}
    for k, r in enumerate(leds):
        i = [c[1 + k] for c in cols]
        traces[r] = i
        outs[r] = _blinks(t, i, settle)
    isup = [-c[1 + len(leds)] for c in cols]
    after = [x for tt, x in zip(t, isup) if tt >= settle]
    for k, r in enumerate(loads):
        i = [abs(c[2 + len(leds) + k]) for c in cols]
        traces[r] = i
        outs[r] = _blinks(t, i, settle)
    return {
        "name": name,
        "runtime_s": round(elapsed, 3),
        "leds": leds,
        "loads": loads,
        "outputs": outs,
        "supply_ma_avg": round(sum(after) / len(after) * 1e3, 3),
        "supply_ma_peak": round(max(after) * 1e3, 3),
        "_t": t,
        "_traces": traces,
        "scenario": {k: v for k, v in scen.items() if k != "name"},
    }


def simulate(spec: Spec, out_dir: Path, t_stop: float | None = None, step: float = 0.5e-3) -> dict[str, Any]:
    cfg = spec.raw.get("sim") or {}
    t_stop = float(t_stop or cfg.get("t_stop", 6.0))
    scs = scenarios(spec)
    t0 = time.time()
    try:
        netlist(spec, t_stop, step, scs[0])  # mapping errors surface before any process starts
        with cf.ThreadPoolExecutor(max_workers=max(1, len(scs))) as ex:
            runs = list(ex.map(lambda s: _run_one(spec, out_dir, s, t_stop, step), scs))
    except (ValueError, RuntimeError) as e:
        # a part with no SPICE mapping: the board still builds and the other gates still run,
        # but every simulation benchmark reads as missing (and so fails)
        return {"ok": False, "error": str(e)[:1200], "runtime_s": round(time.time() - t0, 3)}
    first = runs[0]
    if not first["leds"] and not first["loads"]:
        return {
            "ok": False,
            "error": "no LED or load in the circuit; nothing for the transient benchmarks to measure",
            "runtime_s": round(time.time() - t0, 3),
        }
    res: dict[str, Any] = {
        "ok": True,
        "engine": "ngspice",
        "runtime_s": round(time.time() - t0, 3),
        "t_stop_s": t_stop,
        "supply_v": float(spec.raw.get("power", {}).get("voltage", 5.0)),
        "models": "2N3904/2N3906/2N2222 published SPICE cards; LED = generic colour-typical diode; "
        "zeners = generic card at nominal voltage; buzzer/fan = rated load",
        "scenarios": [{"name": r["name"], **r["scenario"], "runtime_s": r["runtime_s"]} for r in runs],
        "leds": {r: first["outputs"][r] for r in first["leds"]},
    }
    multi = len(runs) > 1 or runs[0]["name"] != "default"
    for r in runs:
        pre = (r["name"] + ".") if multi else ""
        res[pre + "supply_ma_avg"] = r["supply_ma_avg"]
        res[pre + "supply_ma_peak"] = r["supply_ma_peak"]
        for ref, m in r["outputs"].items():
            for k in ("peak_ma", "avg_ma", "blink_hz", "duty"):
                res[f"{pre}{ref}.{k}"] = m[k]
    # legacy flat keys from the first scenario (first LED, first load)
    res.setdefault("supply_ma_avg", first["supply_ma_avg"])
    res.setdefault("supply_ma_peak", first["supply_ma_peak"])
    leds, loads = first["leds"], first["loads"]
    if loads:
        res["loads"] = {r: first["outputs"][r] for r in loads}
        res["load_hz"] = first["outputs"][loads[0]]["blink_hz"]
        res["load_peak_ma"] = first["outputs"][loads[0]]["peak_ma"]
        res["load_duty"] = first["outputs"][loads[0]]["duty"]
    t, traces = first["_t"], first["_traces"]
    if leds:
        f = first["outputs"][leds[0]]
        res["blink_hz"], res["led_peak_ma"], res["duty"] = f["blink_hz"], f["peak_ma"], f["duty"]
        if len(leds) > 1:
            res["led2_peak_ma"] = first["outputs"][leds[1]]["peak_ma"]
            a, b = traces[leds[0]], traces[leds[1]]
            ta, tb = 0.5 * max(a), 0.5 * max(b)
            settle = 1.0 if t_stop >= 3 else t_stop / 3
            both = sum(t[k] - t[k - 1] for k in range(1, len(t)) if t[k] >= settle and a[k] >= ta and b[k] >= tb)
            res["alternate"] = round(both / (t[-1] - settle), 3)  # fraction of time both are lit
    # decimated waveform for the front end (about 600 points), every scenario
    stride = max(1, len(t) // 600)
    res["wave"] = {
        "t": [round(x, 4) for x in t[::stride]],
        **{f"i_{r}_ma": [round(x * 1e3, 3) for x in traces[r][::stride]] for r in traces},
    }
    if multi:
        res["wave_by_scenario"] = {
            r["name"]: {f"i_{ref}_ma": [round(x * 1e3, 3) for x in tr[::stride]] for ref, tr in r["_traces"].items()}
            for r in runs
        }
    if leds and not math.isfinite(res["blink_hz"]):
        res["ok"] = False
    return res

"""Deterministic analog check and solver for the common transistor switch, run before every build attempt.

Topology: an NPN low-side switch (emitter on GND, a load between VCC and the collector) whose base is driven by
  * a sensor divider: fixed resistors to VCC / GND plus a variable element (LDR, switch) to VCC or GND, or
  * a microcontroller pin (a GPIO net, 3.3 V high) through a base resistor.
For every scenario it computes the base Thevenin voltage and resistance, the base current, the load current and
the transistor's limits, then (only when the design cannot work) picks E-series values or a stronger driver:
2N3904 -> PN2222A (higher current) -> a discrete Darlington (2N3904 into PN2222A) when a high-impedance sensor
divider cannot both saturate the load and switch off. Every change comes back as a numbered note with the part,
the old value and the new value, so the repair prompt and the step log can say exactly what moved and why.

Device numbers are datasheet minimums (onsemi 2N3904 and PN2222A data sheets):
  2N3904  Ic 200 mA continuous; hFE min 100 @ 10 mA, 60 @ 50 mA, 30 @ 100 mA
  PN2222A Ic 1 A abs max (design limit 600 mA here); hFE min 100 @ 150 mA, 40 @ 500 mA
Load models: a small 5 V DC fan draws about 100 to 200 mA (150 mA used), a small hobby motor about 200 mA running;
the picker's load_ohms is kept unless it is outside 30 to 400 mA for a fan or motor.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field
from typing import Any

VBE, VBE_DARL = 0.7, 1.3
VCE_SAT, VCE_SAT_DARL = 0.2, 0.9
OVERDRIVE = 2.0  # base current = 2 x Ic / hFE(min): saturates with margin
OFF_VB = 0.45  # base Thevenin voltage below which a single NPN is off
GPIO_V, GPIO_MAX_MA = (
    3.3,
    8.0,
)  # RP2040 GPIO high level; keep a pin's source current modest
KEY_3904 = "kicad:Transistor_BJT:2N3904@TO-92_Inline"
KEY_2222 = "kicad:Transistor_BJT:PN2222A@TO-92_Inline"

DRIVERS: dict[str, dict[str, Any]] = {
    "2N3904": {
        "key": KEY_3904,
        "ic_max_ma": 200.0,
        "hfe": [(10, 100), (50, 60), (100, 30), (200, 15)],
    },
    "2N2222": {
        "key": KEY_2222,
        "ic_max_ma": 600.0,
        "hfe": [(10, 75), (150, 100), (500, 40), (600, 30)],
    },
}
FAN_MA, MOTOR_MA = 150.0, 200.0
E12 = (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2)


def parse_value(v: Any) -> float | None:
    """'4.7k' -> 4700, '1Meg' -> 1e6, '220' -> 220, '2k2' -> 2200. None when not a resistance."""
    s = (
        str(v or "")
        .strip()
        .lower()
        .replace("ω", "")
        .replace("ohm", "")
        .replace(" ", "")
    )
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(meg|m|k|r)?(\d*)", s)
    if not m:
        return None
    num, suf, tail = m.groups()
    mult = {"meg": 1e6, "m": 1e6, "k": 1e3, "r": 1.0, None: 1.0}[suf]
    x = (
        float(num + ("." + tail if tail else ""))
        if tail and "." not in num
        else float(num)
    )
    return x * mult


def fmt(r: float) -> str:
    if r >= 1e6:
        x, suf = r / 1e6, "Meg"
    elif r >= 1e3:
        x, suf = r / 1e3, "k"
    else:
        x, suf = r, ""
    t = f"{x:.1f}".rstrip("0").rstrip(".")
    return t + suf


def e12(lo: float = 100.0, hi: float = 1e6) -> list[float]:
    out = []
    for dec in range(1, 7):
        for b in E12:
            r = round(b * 10**dec, 6)
            if lo <= r <= hi:
                out.append(r)
    return out


def hfe_min(model: str, ic_ma: float) -> float:
    pts = DRIVERS[model]["hfe"]
    if ic_ma <= pts[0][0]:
        return pts[0][1]
    for (i0, h0), (i1, h1) in itertools.pairwise(pts):
        if ic_ma <= i1:
            f = (math.log(ic_ma) - math.log(i0)) / (math.log(i1) - math.log(i0))
            return h0 + f * (h1 - h0)
    return pts[-1][1]


@dataclass
class Element:
    ref: str
    kind: str  # resistor | ldr | switch
    other: str  # the net at the far end from the base
    ohms: float | None = None
    states: dict[str, float] = field(default_factory=dict)  # ldr: dark/light ohms


@dataclass
class Switch:
    q: dict[str, Any]
    model: str
    base: str
    coll: str
    load_ref: str
    load_ohms: float
    elements: list[Element]
    darlington: dict[str, Any] | None = None


def _spice(piece: dict[str, Any] | None) -> dict[str, Any]:
    return (piece or {}).get("spice") or {}


def _pin_by_name(piece: dict[str, Any], name: str) -> str | None:
    return next(
        (n for n, nm in (piece.get("pins") or {}).items() if str(nm).upper() == name),
        None,
    )


def _gpio_nets(design: dict[str, Any], by_key: dict[str, dict[str, Any]]) -> set[str]:
    out = set()
    for p in design.get("parts", []):
        pc = by_key.get(p.get("piece")) or {}
        if _spice(pc).get("role") != "mcu":
            continue
        for num, net in (p.get("pins") or {}).items():
            if str(pc.get("pins", {}).get(str(num), "")).upper().startswith("GPIO"):
                out.add(str(net))
    return out


def _load_kind(part: dict[str, Any], ask: str) -> str:
    text = f"{part.get('why', '')} {part.get('value', '')} {ask}".lower()
    if re.search(r"\bfan\b", text):
        return "fan"
    if re.search(r"\bmotor\b", text):
        return "motor"
    return "load"


def find_switches(
    design: dict[str, Any], by_key: dict[str, dict[str, Any]]
) -> list[Switch]:
    parts = design.get("parts", [])
    on_net: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for p in parts:
        for num, net in (p.get("pins") or {}).items():
            on_net.setdefault(str(net), []).append((p, str(num)))
    out = []
    for q in parts:
        pc = by_key.get(q.get("piece")) or {}
        model = _spice(pc).get("model")
        if model not in DRIVERS:
            continue
        pe, pb, pcn = (_pin_by_name(pc, n) for n in ("E", "B", "C"))
        pins = q.get("pins") or {}
        if not (pe and pb and pcn) or pins.get(pe) != "GND":
            continue
        base, coll = str(pins.get(pb)), str(pins.get(pcn))
        load = None
        for p, _num in on_net.get(coll, []):
            if p is q:
                continue
            sp = _spice(by_key.get(p.get("piece")))
            nets = set(map(str, (p.get("pins") or {}).values()))
            if nets == {coll, "VCC"}:
                ohms = p.get("load_ohms") or (
                    sp.get("ohms") if sp.get("role") == "load" else None
                )
                if ohms:
                    load = (p, float(ohms))
        if not load:
            continue
        els, ok = [], True
        for p, num in on_net.get(base, []):
            if p is q:
                continue
            pins_p = {str(k): str(v) for k, v in (p.get("pins") or {}).items()}
            if len(pins_p) != 2:
                ok = False
                break
            other = next(v for k, v in pins_p.items() if k != num)
            sp = _spice(by_key.get(p.get("piece")))
            role = sp.get("role")
            if role == "ldr":
                els.append(
                    Element(
                        p["ref"],
                        "ldr",
                        other,
                        states={
                            "dark": parse_value(sp.get("dark")) or 1e6,
                            "light": parse_value(sp.get("light")) or 1e3,
                        },
                    )
                )
            elif role == "switch":
                els.append(Element(p["ref"], "switch", other))
            elif (
                sp.get("prefix") == "R"
                and not role
                and (r := parse_value(p.get("value")))
            ):
                els.append(Element(p["ref"], "resistor", other, ohms=r))
            else:
                ok = False
                break
        if ok and els:
            out.append(Switch(q, model, base, coll, load[0]["ref"], load[1], els))
    return out


def _thevenin(
    sw: Switch, scen: dict[str, Any], v: float, gpio: set[str], gpio_level: float
) -> tuple[float, float] | None:
    g_sum, i_sum = 0.0, 0.0
    for e in sw.elements:
        if e.other == "VCC":
            vo = v
        elif e.other == "GND":
            vo = 0.0
        elif e.other in gpio:
            vo = gpio_level
        else:
            return None
        if e.kind == "ldr":
            r = e.states.get(scen.get("ldr", "light"), 1e4)
        elif e.kind == "switch":
            r = 0.05 if scen.get("switch", "pressed") != "released" else 1e9
        else:
            r = e.ohms or 1e9
        g_sum += 1 / r
        i_sum += vo / r
    if g_sum == 0:
        return None
    return i_sum / g_sum, 1 / g_sum


def _want(
    design: dict[str, Any], load_ref: str, scen_names: list[str]
) -> dict[str, bool]:
    """Scenario -> load must be ON (True) / OFF (False), from the benchmarks; unknown scenarios are skipped."""
    want: dict[str, bool] = {}
    for b in design.get("benchmarks") or []:
        c = str(b.get("check", ""))
        m = re.fullmatch(rf"(?:(\w+)\.)?{re.escape(load_ref)}\.avg_ma", c)
        if not m:
            continue
        names = [m.group(1)] if m.group(1) else (scen_names or ["default"])
        on = b.get("min") is not None and float(b["min"]) >= 1
        off = b.get("max") is not None and float(b["max"]) <= 1
        for n in names:
            if on or off:
                want[n] = on
    return want


def _need_ib(model: str, iload_ma: float, darl: bool, od: float = OVERDRIVE) -> float:
    if darl:
        return (
            od
            * iload_ma
            / (hfe_min("2N2222", iload_ma) * hfe_min("2N3904", max(iload_ma / 40, 1)))
        )
    return od * iload_ma / hfe_min(model, iload_ma)


def evaluate(
    sw: Switch,
    design: dict[str, Any],
    v: float,
    gpio: set[str],
    darl: bool = False,
    model: str | None = None,
    od: float = OVERDRIVE,
) -> list[dict[str, Any]]:
    """Per scenario: Vth, Rth, Ib available, Ib needed (od x Ic / hFE min), load current; ok flags."""
    model = model or sw.model
    scens = [s for s in design.get("scenarios") or [] if s.get("name")]
    names = [s["name"] for s in scens]
    want = _want(design, sw.load_ref, names)
    vce = VCE_SAT_DARL if darl else VCE_SAT
    vbe = VBE_DARL if darl else VBE
    iload = max(v - vce, 0) / sw.load_ohms * 1000
    rows = []
    for s in scens or [{"name": "default"}]:
        n = s["name"]
        on = want.get(n, True if not scens else None)
        if on is None:
            continue
        th = _thevenin(sw, s, v, gpio, GPIO_V if on else 0.0)
        if th is None:
            return []
        vth, rth = th
        ib = max(vth - vbe, 0) / rth * 1000
        need = _need_ib(model, iload, darl, od)
        rows.append(
            {
                "scenario": n,
                "on": on,
                "vth": round(vth, 3),
                "rth": round(rth, 1),
                "ib_ma": round(ib, 4),
                "need_ma": round(need, 4),
                "iload_ma": round(iload, 1),
                "ok": (ib >= need) if on else (vth < (OFF_VB * (2 if darl else 1))),
            }
        )
    return rows


def _ic_limit(model: str) -> float:
    return DRIVERS[model]["ic_max_ma"] * (0.75 if model == "2N3904" else 0.85)


@dataclass
class Result:
    notes: list[str] = field(default_factory=list)
    added: list[dict[str, Any]] = field(
        default_factory=list
    )  # catalog pieces the design now uses
    findings: list[str] = field(
        default_factory=list
    )  # problems it could not fix (for the repair prompt)


def _fix_load(
    design: dict[str, Any], sw: Switch, ask: str, v: float, res: Result
) -> None:
    part = next(p for p in design["parts"] if p.get("ref") == sw.load_ref)
    if not part.get("load_ohms"):
        return
    kind = _load_kind(part, ask)
    if kind == "load":
        return
    rated = FAN_MA if kind == "fan" else MOTOR_MA
    i = v / float(part["load_ohms"]) * 1000
    if 30 <= i <= 400:
        return
    new = max(round(v / (rated / 1000)), 1)
    res.notes.append(
        f"{sw.load_ref} ({kind}) load {fmt(float(part['load_ohms']))} ohm = {i:.0f} mA at {v:g} V is not a small "
        f"{v:g} V {kind}; modelled as {new} ohm ({rated:.0f} mA rated)."
    )
    part["load_ohms"] = new
    sw.load_ohms = float(new)


def _fix_flyback(
    design: dict[str, Any], sw: Switch, by_key: dict[str, dict[str, Any]], res: Result
) -> None:
    """A diode across the load must point its cathode at the supply. Wired the other way it conducts the
    moment the switch closes and shorts the supply through the transistor; swap its two pins."""
    load = next(p for p in design["parts"] if p.get("ref") == sw.load_ref)
    supply = {n for n in (load.get("pins") or {}).values() if n != sw.coll}
    for p in design["parts"]:
        pc = by_key.get(p.get("piece")) or {}
        k, a = _pin_by_name(pc, "K"), _pin_by_name(pc, "A")
        pins = p.get("pins") or {}
        if not k or not a or pc.get("family") != "diode":
            continue
        if pins.get(a) in supply and pins.get(k) == sw.coll:
            pins[a], pins[k] = sw.coll, pins[a]
            res.notes.append(
                f"{p['ref']}: flyback diode was backwards (anode on {pins[k]}); cathode now on {pins[k]}, "
                f"anode on {sw.coll}, so it only clamps the {sw.load_ref} turn-off spike."
            )


def _fix_power_input(
    design: dict[str, Any], by_key: dict[str, dict[str, Any]], res: Result
) -> None:
    """The board's supply is its first power connector, pin 1 + and pin 2 GND. Sweep 10-17: the picker wired
    the power input's pin 2 to the motor net, which put the motor straight across the supply (always on)."""
    for p in design["parts"]:
        if _spice(by_key.get(p.get("piece"))).get("role") != "power_in" or p.get("load_ohms"):
            continue
        pins = p.get("pins") or {}
        if (pins.get("1"), pins.get("2")) == ("GND", "VCC"):
            pins["1"], pins["2"] = "VCC", "GND"
            res.notes.append(f"{p['ref']}: power input was reversed; pin 1 is now VCC and pin 2 GND.")
            return
        others = {
            n for q in design["parts"] if q is not p for n in (q.get("pins") or {}).values()
        }
        for pin, want in (("1", "VCC"), ("2", "GND")):
            have = pins.get(pin)
            if have and have not in ("VCC", "GND") and want in others:
                pins[pin] = want
                res.notes.append(
                    f"{p['ref']}: power input pin {pin} was on {have}; it is now {want} "
                    f"(pin 1 is +, pin 2 is GND), so {have} is no longer tied to the supply."
                )
        return


def _swap_model(
    design: dict[str, Any],
    sw: Switch,
    model: str,
    by_key: dict[str, dict[str, Any]],
    res: Result,
) -> None:
    key = DRIVERS[model]["key"]
    old_pc = by_key[sw.q["piece"]]
    new_pc = by_key[key]
    names = {nm: num for num, nm in old_pc["pins"].items()}
    sw.q["pins"] = {
        _pin_by_name(new_pc, nm): sw.q["pins"][num]
        for nm, num in names.items()
        if num in sw.q["pins"]
    }
    res.notes.append(
        f"{sw.q['ref']}: {sw.model} -> {'PN2222A' if model == '2N2222' else model} "
        f"({sw.load_ohms:g} ohm load needs {(5 - VCE_SAT) / sw.load_ohms * 1000:.0f} mA; "
        f"{sw.model} design limit {_ic_limit(sw.model):.0f} mA)."
    )
    sw.q["piece"] = key
    sw.model = model
    res.added.append(new_pc)


def _make_darlington(
    design: dict[str, Any], sw: Switch, by_key: dict[str, dict[str, Any]], res: Result
) -> None:
    """Q (2N3904) keeps its base; its emitter now drives a PN2222A whose collector takes the load."""
    pc1 = by_key[KEY_3904]
    pc2 = by_key[KEY_2222]
    used = {str(p.get("ref")) for p in design["parts"]}
    n = 1
    while f"Q{n}" in used:
        n += 1
    ref2 = f"Q{n}"
    q1 = sw.q
    mid = f"{q1['ref']}_E"
    q1["piece"] = KEY_3904
    q1["pins"] = {
        _pin_by_name(pc1, "B"): sw.base,
        _pin_by_name(pc1, "C"): sw.coll,
        _pin_by_name(pc1, "E"): mid,
    }
    design["parts"].append(
        {
            "ref": ref2,
            "piece": KEY_2222,
            "group": q1.get("group", "driver"),
            "pins": {
                _pin_by_name(pc2, "B"): mid,
                _pin_by_name(pc2, "C"): sw.coll,
                _pin_by_name(pc2, "E"): "GND",
            },
            "why": "Power stage of a Darlington pair so a weak sensor drives the load.",
        }
    )
    sw.model = "2N3904"
    sw.darlington = {"ref": ref2}
    res.added += [pc1, pc2]
    res.notes.append(
        f"{q1['ref']} + {ref2}: Darlington pair (2N3904 into PN2222A) so the sensor divider can drive "
        f"{(5 - VCE_SAT_DARL) / sw.load_ohms * 1000:.0f} mA and still switch off."
    )


def _divider(sw: Switch) -> tuple[Element, list[Element]] | None:
    """The fixed resistor(s) a solver may change: the ones on the side opposite the sensor."""
    var = [e for e in sw.elements if e.kind in ("ldr", "switch")]
    if len(var) != 1:
        return None
    side = "GND" if var[0].other == "VCC" else "VCC"
    fixed = [e for e in sw.elements if e.kind == "resistor"]
    return var[0], [e for e in fixed if e.other == side] + [
        e for e in fixed if e.other != side
    ]


def _set(
    design: dict[str, Any], ref: str, value: str | None, res: Result, why: str
) -> None:
    parts = design["parts"]
    p = next(x for x in parts if x.get("ref") == ref)
    if value is None:
        parts.remove(p)
        res.notes.append(f"{ref} ({p.get('value')}) removed: {why}.")
    elif str(p.get("value")) != value:
        res.notes.append(f"{ref}: {p.get('value')} -> {value} ({why}).")
        p["value"] = value


def _solve_divider(
    design: dict[str, Any],
    sw: Switch,
    v: float,
    gpio: set[str],
    res: Result,
    darl: bool,
    model: str,
) -> bool:
    d = _divider(sw)
    if not d:
        return False
    var, fixed = d
    side = "GND" if var.other == "VCC" else "VCC"
    main = [e for e in fixed if e.other == side]
    rest = [e for e in fixed if e.other != side]
    if len(main) != 1 or any(e.other not in ("VCC", "GND") for e in rest):
        return False
    best = None
    for drop_rest in (False, True):
        for r in e12(100, 1e6):
            trial = [var, Element(main[0].ref, "resistor", side, ohms=r)] + (
                [] if drop_rest else rest
            )
            t = Switch(
                sw.q, sw.model, sw.base, sw.coll, sw.load_ref, sw.load_ohms, trial
            )
            rows = evaluate(t, design, v, gpio, darl, model)
            if rows and all(x["ok"] for x in rows):
                on = [x["ib_ma"] / x["need_ma"] for x in rows if x["on"]] or [1]
                off = [
                    (OFF_VB * (2 if darl else 1)) / max(x["vth"], 1e-3)
                    for x in rows
                    if not x["on"]
                ] or [10]
                score = min(min(on), min(off)) - (0.5 if drop_rest else 0)
                if best is None or score > best[0]:
                    best = (score, r, drop_rest)
        if best:
            break
    if not best:
        return False
    _, r, drop_rest = best
    _set(
        design,
        main[0].ref,
        fmt(r),
        res,
        f"base divider sized so {sw.q['ref']} saturates when on and stays off when off",
    )
    if drop_rest:
        for e in rest:
            _set(design, e.ref, None, res, f"it shunted {sw.q['ref']}'s base drive")
    return True


def _solve_gpio(
    design: dict[str, Any], sw: Switch, v: float, res: Result, darl: bool, model: str
) -> bool:
    series = [
        e for e in sw.elements if e.kind == "resistor" and e.other not in ("VCC", "GND")
    ]
    if len(series) != 1:
        return False
    iload = (v - (VCE_SAT_DARL if darl else VCE_SAT)) / sw.load_ohms * 1000
    need = _need_ib(model, iload, darl)
    pull = [e for e in sw.elements if e.kind == "resistor" and e.other == "GND"]
    i_pull = sum((VBE_DARL if darl else VBE) / (e.ohms or 1e9) * 1000 for e in pull)
    total = need + i_pull
    if total > GPIO_MAX_MA:
        return False
    rmax = (GPIO_V - (VBE_DARL if darl else VBE)) / total * 1000
    cands = [r for r in e12(100, 1e6) if r <= rmax]
    if not cands:
        return False
    r = cands[-1]
    cur = series[0].ohms or 0
    if cur and cur <= rmax and (GPIO_V - VBE) / cur * 1000 <= GPIO_MAX_MA:
        return True
    _set(
        design,
        series[0].ref,
        fmt(r),
        res,
        f"GPIO base resistor sized for {need:.2f} mA base current",
    )
    return True


def solve(
    design: dict[str, Any],
    kept: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    ask: str = "",
) -> Result:
    """Check every recognised switch; change values / driver only where the current design cannot work."""
    res = Result()
    by_key = {p["key"]: p for p in kept} | catalog
    _fix_power_input(design, by_key, res)
    if KEY_2222 not in by_key or KEY_3904 not in by_key:
        return res
    v = float(design.get("voltage", 5.0) or 5.0)
    gpio = _gpio_nets(design, by_key)
    for sw in find_switches(design, by_key):
        _fix_load(design, sw, ask, v, res)
        _fix_flyback(design, sw, by_key, res)
        # step in only when the design fails at datasheet minimum gain with no overdrive (it would not switch
        # reliably on a real board); new values are then sized with the full 2x overdrive
        rows = evaluate(sw, design, v, gpio, od=1.0)
        iload = rows[0]["iload_ma"] if rows else (v - VCE_SAT) / sw.load_ohms * 1000
        over = iload > _ic_limit(sw.model)
        if rows and all(x["ok"] for x in rows) and not over:
            continue
        driven_by_gpio = any(e.other in gpio for e in sw.elements)
        plans = [(False, sw.model)] if not over else []
        if sw.model == "2N3904":
            plans.append((False, "2N2222"))
        plans.append((True, "2N3904"))
        done = False
        for darl, model in plans:
            if not darl and iload > _ic_limit(model):
                continue
            if darl and (v - VCE_SAT_DARL) / sw.load_ohms * 1000 > _ic_limit("2N2222"):
                continue
            snapshot = [
                dict(p, pins=dict(p.get("pins") or {})) for p in design["parts"]
            ]
            nres = Result()
            ok = (
                _solve_gpio(design, sw, v, nres, darl, model)
                if driven_by_gpio
                else _solve_divider(design, sw, v, gpio, nres, darl, model)
            )
            if ok:
                if darl:
                    _make_darlington(design, sw, by_key, nres)
                elif model != sw.model:
                    _swap_model(design, sw, model, by_key, nres)
                res.notes += nres.notes
                res.added += nres.added
                done = True
                break
            design["parts"] = snapshot
            sw.q = next(p for p in design["parts"] if p.get("ref") == sw.q.get("ref"))
        if not done:
            bad = [x for x in rows if not x["ok"]]
            res.findings.append(describe(sw, bad, over, iload))
    return res


def describe(sw: Switch, bad: list[dict[str, Any]], over: bool, iload: float) -> str:
    """One precise repair line: which part, what the numbers are, what they must become."""
    els = ", ".join(
        f"{e.ref} {fmt(e.ohms) if e.ohms else e.kind} to {e.other}" for e in sw.elements
    )
    parts = [
        f"{sw.q['ref']} ({sw.model}) drives {sw.load_ref} ({sw.load_ohms:g} ohm, {iload:.0f} mA); base network: {els}."
    ]
    if over:
        parts.append(
            f"{iload:.0f} mA is above the {sw.model} design limit of {_ic_limit(sw.model):.0f} mA: use PN2222A."
        )
    for x in bad:
        if x["on"]:
            parts.append(
                f"In {x['scenario']} the base gets {x['ib_ma'] * 1000:.0f} uA (Vth {x['vth']:.2f} V through "
                f"{fmt(x['rth'])}) but needs {x['need_ma'] * 1000:.0f} uA to carry {x['iload_ma']:.0f} mA."
            )
        else:
            parts.append(
                f"In {x['scenario']} the base sits at {x['vth']:.2f} V, so the load leaks on; it must stay below "
                f"{OFF_VB:.2f} V."
            )
    return " ".join(parts)


def bands(
    design: dict[str, Any],
    kept: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
) -> list[str]:
    """Make steady load benchmarks agree with the load model: an ON band that excludes the load's own rated
    current, or a peak / supply cap below it, can never pass. Only rows that contradict the model change."""
    by_key = {p["key"]: p for p in kept} | catalog
    v = float(design.get("voltage", 5.0) or 5.0)
    notes = []
    benches = design.get("benchmarks") or []
    for sw in find_switches(design, by_key):
        darl = bool(sw.darlington) or any(
            (p.get("pins") or {}).get(
                _pin_by_name(by_key.get(p.get("piece")) or {}, "B") or ""
            )
            == f"{sw.q['ref']}_E"
            for p in design["parts"]
        )
        iexp = (v - (VCE_SAT_DARL if darl else VCE_SAT)) / sw.load_ohms * 1000
        if any(
            re.search(
                rf"{re.escape(sw.load_ref)}\.(blink_hz|duty)$", str(b.get("check"))
            )
            for b in benches
        ):
            continue  # a blinking load: its average is not the steady current
        limit = DRIVERS["2N2222" if darl else sw.model]["ic_max_ma"]
        for b in benches:
            c = str(b.get("check", ""))
            lo, hi = b.get("min"), b.get("max")
            if (
                c.endswith(f"{sw.load_ref}.avg_ma")
                and lo is not None
                and float(lo) >= 1
            ):
                if not (float(lo) <= iexp <= float(hi if hi is not None else 1e9)):
                    b["min"], b["max"] = round(0.5 * iexp, 1), round(1.3 * iexp, 1)
                    notes.append(
                        f"{c}: band {lo}-{hi} mA -> {b['min']}-{b['max']} mA ({sw.load_ref} draws ~{iexp:.0f} mA)."
                    )
            elif (
                c.endswith(f"{sw.load_ref}.peak_ma")
                and hi is not None
                and float(hi) < 1.2 * iexp
            ):
                b["max"] = round(min(limit, max(1.3 * iexp, float(hi))), 1)
                notes.append(
                    f"{c}: max {hi} -> {b['max']} mA (transistor limit {limit:.0f} mA)."
                )
            elif (
                c.endswith("supply_ma_avg")
                and hi is not None
                and lo is not None
                and float(lo) >= 1
                and float(hi) < 1.1 * iexp
            ):
                b["max"] = round(1.3 * iexp + 20, 1)
                notes.append(
                    f"{c}: max {hi} -> {b['max']} mA (includes the ~{iexp:.0f} mA load)."
                )
    return notes

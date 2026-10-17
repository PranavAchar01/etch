"""Etch live: plain ask -> classifier -> picker -> assembly loop -> export, all on real tools.

  1. CLASSIFIER  every piece in the installed KiCad libraries (pre-filtered to the families a small
                 through-hole board uses, agent/kicad_catalog.py) gets a YES or NO from a small fast model
                 (gpt-5-nano). It stands in for Etch's own classifier. Batches run in parallel.
                 Then plain code drops YES pieces that cannot be built or checked (no STEP model, a pin
                 without a pad, no SPICE card), each with its reason.
  2. PICKER      a stronger model (gpt-5-mini) chooses parts and values from the kept set only, and wires
                 them (pins -> nets), groups them into function blocks and writes the benchmarks.
  3. ASSEMBLY    plain code turns that into a spec (row layout, pours, stitching, holes, fiducials, silk),
                 and the engine places, routes and checks it: ERC, DRC with schematic parity, ngspice in
                 every scenario, benchmarks. A failure goes back to the picker; the loop runs until it passes.
  4. EXPORT      KiCad project (.kicad_pro/.kicad_sch/.kicad_pcb), Gerbers + drill + BOM in one zip.

Every stage emits timed events on a Stream (SSE in the studio, run.jsonl on disk).

  uv run --project engine --with openai python -m agent.live design "a board that beeps when it gets dark"
  ... python -m agent.live modify "add a small fan" --base engine/out/live-...
  ... python -m agent.live bench "no DRC errors, board under 60 mm wide" --base engine/out/live-...
"""

from __future__ import annotations

import concurrent.futures as cf
import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import yaml
from etchlib.kicad import cli
from etchlib.parts import load_index, read_index, register

from . import (
    analog,
    board_store,
    config,
    firmware,
    kicad_catalog,
    kicad_library,
    llm,
    piece_render,
    prefilter,
    recipes,
    select,
    task_router,
)
from . import engine_api as eng
from .events import Stream

PICKER_MODEL = (
    config.MODEL
)  # ETCH_MODEL; one attempt per provider, OpenAI fallback in llm.chat
PICKER_EFFORT = "minimal"
PICKER_TIMEOUT = 40
# output caps (prod "9 pad macro pad", 10-17: an unfit KEPT list sent Nemotron Super on a long answer and both
# keys timed out): a design JSON is ~1-2.5k tokens; a capped answer ends instead of running into the timeout
PICKER_MAX_TOKENS = 3000
RETRY_MAX_TOKENS = 2000
RETRY_TIMEOUT = 25
SLOW_SAY = "The design model took too long on this one. Try again, it usually answers on the second go."
MISSING_SAY = (
    "Looks like we don't have all of the components for this board yet. Explore the library to see what we have, "
    "or try again in a few minutes."
)


class PickTimeout(RuntimeError):
    """The picker could not answer on the main model nor on the quick retry: most often the KEPT list does not fit
    the ask (parts missing from the library), so the run ends with the missing-parts message."""


BATCH = 24
MAX_ITERS = 3  # picker path only; worst case stays under a minute

TOP = ("LED", "RGB LED", "buzzer", "light sensor", "push button")
MID = ("resistor", "trimmer potentiometer")
POWER_FAMILIES = (
    "2-pin connector",
    "battery holder",
    "USB-C power input",
    "DC barrel jack",
)


# ---------------------------------------------------------------- stage 1: classifier


def _line(i: int, p: dict[str, Any]) -> str:
    return f"{i}: {p['family']} | {p['name']} | {p['descr'][:70]}"


CLASSIFY_FN = {
    "name": "answer",
    "description": "List the candidate numbers that are YES.",
    "parameters": {
        "type": "object",
        "properties": {"yes": {"type": "array", "items": {"type": "integer"}}},
        "required": ["yes"],
    },
}


BASIC_STOCK = ("resistor", "capacitor", "electrolytic capacitor")


def _cat() -> dict[str, dict[str, Any]]:
    """Every piece any stage may use, by key: the full parts library (all KiCad libraries, the ingested extra
    libraries and fetched cache parts, agent/kicad_library.py), with the curated catalog's entries on top. No stage
    screens against a short library list."""
    full = dict(kicad_library.load()["by_key"])
    full.update({p["key"]: p for p in kicad_catalog.load()["pieces"]})
    return full


def _lib_piece(text: str) -> dict[str, Any] | None:
    """A part named in words ("9 mechanical switches (through-hole)", "1N4148", "Conn_01x02") as one usable library
    piece, or None (then sourcing runs; nothing is guessed)."""
    t = re.sub(r"^\s*\d+\s*x?\s+", "", str(text or "")).strip()
    t = re.sub(r"\(([^)]*)\)", r" \1", t).strip()
    if not t:
        return None
    if (hit := kicad_library.resolve(t)) is not None:
        return hit
    # a description ("mechanical switches", "rotary encoder"): the first part of the function it names
    low = t.lower()
    for rx, qs in kicad_library.FUNCTIONS:
        if re.search(rx, low):
            return kicad_library.resolve(qs[0])
    return None


def classify(stream: Stream, ask: str, context: str = "") -> dict[str, Any]:
    """Which catalog pieces this ask can use. select.select_parts is the one swappable function: a keyword
    table in code by default (well under a millisecond), or the OpenAI Decisions API with ETCH_CLASSIFIER=decisions
    (one call, fails fast). The page still gets the screening marks, one batch per 24 pieces."""
    cat = kicad_catalog.load()
    pieces = [p for p in cat["pieces"] if p["family"] not in BASIC_STOCK]
    st = cat["stats"]
    t0 = time.time()
    stream.emit(
        "classifier",
        "start",
        model=select.BACKEND
        if select.BACKEND != "decisions"
        else select.DECISIONS_MODEL,
        backend=select.BACKEND,
        symbol_libraries=st["symbol_libraries"],
        symbols=st["symbols"],
        libraries_kept=st["libraries_kept"],
        candidates=len(pieces),
        batches=(len(pieces) + BATCH - 1) // BATCH,
    )
    sel = select.select_parts(f"{ask}\n{context}".strip(), cat["pieces"])
    keep = {p["key"] for p in sel.kept}
    fams = set(sel.families)
    for k in range(0, len(pieces), BATCH):
        stream.emit(
            "classifier",
            "batch",
            seconds=sel.seconds,
            pieces=[
                {
                    "k": p["key"],
                    "n": p["name"],
                    "f": p["family"],
                    "yes": p["family"] in fams,
                }
                for p in pieces[k : k + BATCH]
            ],
        )
    yes = sum(1 for p in pieces if p["family"] in fams)
    stream.emit(
        "classifier",
        "done",
        yes=yes,
        no=len(pieces) - yes,
        seconds=sel.seconds,
        backend=sel.backend,
        families=sel.families,
        detail=sel.detail,
    )
    kept = [p for p in cat["pieces"] if p["key"] in keep]
    # the table rules rank, never restrict: the parts for every function the ask implies come from the whole
    # library (all KiCad libraries, extra libraries, fetched cache parts), whatever the rules matched
    lib = [
        p
        for p in kicad_library.shortlist(f"{ask}\n{context}".strip())
        if p["key"] not in keep
    ]
    kept += lib
    lst = kicad_library.load()["stats"]
    stream.emit(
        "classifier",
        "library",
        pieces=lst.get("pieces"),
        symbols=lst.get("symbols_read"),
        footprints=lst.get("footprints"),
        added=[{"k": p["key"], "n": p["name"], "f": p["family"]} for p in lib],
    )
    rejected = [
        {
            **p,
            "reject": "a variant of a kept part"
            if p["family"] in fams
            else "not needed for this ask",
        }
        for p in cat["pieces"]
        if p["family"] in fams and p["key"] not in keep
    ]
    stream.emit(
        "filter",
        "done",
        kept=[{"k": p["key"], "n": p["name"], "f": p["family"]} for p in kept],
        rejected=[
            {"k": p["key"], "n": p["name"], "f": p["family"], "why": p["reject"]}
            for p in rejected[:60]
        ],
        seconds=round(time.time() - t0, 3),
    )
    return {
        "kept": kept,
        "rejected": rejected,
        "yes": yes,
        "no": len(pieces) - yes,
        "seconds": round(time.time() - t0, 3),
        "backend": sel.backend,
    }


class PieceShow:
    """Streams one real KiCad render per piece to the page as soon as it exists: candidates when screening ends,
    then confirm (the parts the design uses, with refs) or drop (candidates it did not use)."""

    def __init__(self, stream: Stream) -> None:
        self.stream = stream
        self.ex = cf.ThreadPoolExecutor(4)
        self.shown: dict[str, str] = {}
        self.sent: set[str] = set()
        self.lock = threading.Lock()

    def _one(self, p: dict[str, Any]) -> None:
        try:
            png, secs, hit = piece_render.render(p["footprint"])
        except (subprocess.SubprocessError, OSError) as e:
            self.stream.emit("pieces", "error", k=p["key"], error=str(e)[:200])
            return
        with self.lock:
            self.shown[p["key"]] = png.name
        self.stream.emit(
            "pieces",
            "show",
            k=p["key"],
            n=p["name"],
            f=p["family"],
            img=f"/etch/piece/{png.name}",
            seconds=secs,
            cached=hit,
        )

    def candidates(self, pieces: list[dict[str, Any]]) -> None:
        for p in pieces:
            if p.get("footprint") and p["key"] not in self.sent:
                self.sent.add(p["key"])
                self.ex.submit(self._one, p)

    def confirm(
        self, design: dict[str, Any], catalog: dict[str, dict[str, Any]]
    ) -> None:
        used: dict[str, list[str]] = {}
        for part in design.get("parts", []):
            used.setdefault(str(part.get("piece")), []).append(str(part.get("ref")))
        self.candidates([catalog[k] for k in used if k in catalog])
        self.ex.shutdown(
            wait=True
        )  # every used piece is on screen before placing starts (cached: instant)
        with self.lock:
            shown = dict(self.shown)
        self.stream.emit(
            "pieces",
            "confirm",
            parts=[
                {"k": k, "refs": r, "img": f"/etch/piece/{shown[k]}"}
                for k, r in used.items()
                if k in shown
            ],
            drop=[k for k in shown if k not in used],
        )


# ---------------------------------------------------------------- stage 2: picker

PICK_FN = {
    "name": "submit_design",
    "description": "The circuit: parts from the kept list, values, wiring, function groups, scenarios, benchmarks.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "short snake_case board name"},
            "title": {
                "type": "string",
                "description": "silkscreen title, max 16 characters, upper case",
            },
            "voltage": {"type": "number"},
            "parts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string"},
                        "piece": {
                            "type": "string",
                            "description": "a key from the kept list, exactly",
                        },
                        "value": {"type": "string"},
                        "pins": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                        "group": {"type": "string"},
                        "load_ohms": {
                            "type": "number",
                            "description": "only for a connector that drives an off-board load",
                        },
                        "why": {"type": "string"},
                    },
                    "required": ["ref", "piece", "pins", "group", "why"],
                },
            },
            "scenarios": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "ldr": {"type": "string", "enum": ["dark", "light"]},
                        "voltage": {"type": "number"},
                        "switch": {"type": "string", "enum": ["pressed", "released"]},
                    },
                    "required": ["name"],
                },
            },
            "firmware": {
                "type": "object",
                "description": "only for a board with a USB microcontroller: what each typed character does. "
                "Required whenever an LED hangs off a GPIO net, or the program only reads its pins and no LED lights",
                "properties": {
                    "commands": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "input": {
                                    "type": "string",
                                    "description": "one character typed on the laptop, e.g. 1",
                                },
                                "high": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "net names (wired to GPIO pins) driven high; every other GPIO net goes low",
                                },
                                "does": {"type": "string"},
                            },
                            "required": ["input", "high", "does"],
                        },
                    }
                },
                "required": ["commands"],
            },
            "benchmarks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "check": {"type": "string"},
                        "min": {"type": "number"},
                        "max": {"type": "number"},
                        "text": {"type": "string"},
                    },
                    "required": ["check", "text"],
                },
            },
            "say": {
                "type": "string",
                "description": "one short sentence on what you built, plain words",
            },
            "missing": {
                "type": "array",
                "description": "parts the ask needs that are NOT in the kept list (leave empty when nothing is missing)",
                "items": {
                    "type": "object",
                    "properties": {
                        "what": {
                            "type": "string",
                            "description": "plain name, e.g. tilt switch",
                        },
                        "search": {
                            "type": "string",
                            "description": "a common through-hole part number to fetch, e.g. SW-520D",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["switch", "two-pin passive"],
                        },
                    },
                    "required": ["what", "search", "kind"],
                },
            },
        },
        "required": ["name", "title", "voltage", "parts", "benchmarks", "say"],
    },
}

PICK_RULES = """Rules (code checks every one):
- Use ONLY piece keys from the KEPT list, copied exactly. Through-hole parts. The fewest parts that do the job well.
- Refs: R1.., C1.., D1.. (LEDs, RGB LEDs and diodes), U1.. (microcontroller module), Q1.., J1.. (connectors), SW1.., BZ1.. (buzzer), BT1.. (battery holder), RV1.. (trimmer).
- Power: exactly one power input, listed first, in group "power": a 2-pin connector with pin "1" = VCC and pin "2" = GND
  (or a battery holder pin "1" = VCC, "2" = GND; or USB-C: A9 and B9 = VCC, A12 and B12 = GND, A5 and B5 each to GND
  through its own 5.1k resistor, SH = GND). Supply net is VCC, ground net is GND.
- Boards the laptop CONTROLS (type a key, send a command, "when connected to my laptop"): the USB microcontroller
  (Raspberry Pi Pico) IS the power input and the USB link. It is U1, first, in group "power", pins: "3" and "38" = GND,
  "40" (VBUS) = VCC only when another part needs 5 V, and one GPIO pin per output to its own net. Use GPIO pins on the module's lower half:
  "20" (GPIO15), "21" (GPIO16), "22" (GPIO17), "24" (GPIO18). Do NOT add another power connector.
  Give firmware.commands: for each typed character, the GPIO nets that go high (e.g. input "1": high ["LED_B"]).
  Code writes the MicroPython, tests it, and simulates each command as scenario cmd_<input> plus idle (nothing
  typed). Do NOT write scenarios or benchmarks for it (give benchmarks []): code checks every LED die against the
  tested firmware in every scenario. Wire each LED die as GPIO net -> its own resistor -> anode.
- RGB LED (one 4-pin part, common cathode): pins "1" red anode, "2" cathode = GND, "3" green anode, "4" blue anode.
  Each anode gets its OWN series resistor from its GPIO net (3.3 V drive): red 150, green 68, blue 33.
  Value "RGB". Wire all three colours to GPIO pins even if the ask uses two, so the board can show any colour.
- Net names are UPPER_SNAKE (VCC, GND, Q1_B, BUZ_N). The SAME name on every pin of a net; every net touches 2+ pins.
- pins maps the piece's pin NUMBERS (as listed, "1", "2", ...) to net names. Use pin names from the list to wire correctly:
  BJT C/B/E, LED A (anode, toward +) and K, diode A and K, electrolytic pin 1 is +.
- Every LED gets a series resistor. LED value is its colour: RED, GREEN, YELLOW, BLUE or WHITE.
  Resistor and capacitor values are E-series strings: "220", "4.7k", "100k", "1Meg", "100n", "22u".
- A connector that drives an off-board load (fan, motor) is NOT a power input: give it load_ohms (its rated resistance).
  A small 5 V fan draws about 150 mA (load_ohms 33); a small 5 V hobby motor about 200 mA (load_ohms 25). Its ON
  benchmark band must include that current (fan: <REF>.avg_ma 75-200), and PN2222A (up to 600 mA) drives it, not 2N3904.
- Light-dependent boards: use an LDR and add scenarios dark (ldr: dark) and light (ldr: light).
  Battery level boards: add scenarios at several supply voltages (voltage: ...). Buttons: scenarios pressed / released.
- group: function blocks left to right, e.g. power, sensor, driver, output. Group "power" holds only the power input.
- Benchmarks: only these checks exist. Gate counts: drc_violations, erc_errors, unconnected, parity (always max 0;
  added for you). Simulation (ngspice, 6 s transient, every scenario): for EVERY LED and every load (buzzer, load
  connector) <REF>.avg_ma, <REF>.peak_ma, <REF>.blink_hz, <REF>.duty; also supply_ma_avg. With scenarios, prefix
  the scenario name: dark.BZ1.avg_ma, light.BZ1.avg_ma, v3_0.D1.avg_ma. Give honest slack (+-50 % on rates).
  duty is a FRACTION 0..1 (use min 0.2, max 0.8), never a percent. Currents are mA, rates Hz.
  Benchmarks state what the ASK needs, with wide limits; never predict exact values. ON = avg_ma at least 2;
  OFF = avg_ma at most 0.5; blinking = blink_hz 0.5 to 4 and duty 0.2 to 0.8; safe LED = peak_ma at most 25.
  At most 6 benchmarks.
- A module or load connector wired straight across the supply (OLED, sensor board, display: its load pins on VCC/3V3
  and GND, no transistor or GPIO switching it) is simulated as a FIXED resistance: it draws the same current in every
  scenario. Never write per-scenario benchmarks that expect it to change (no "display idle when dry, busy when
  wet"); at most one loose avg_ma band for it, or none.
- In an idle scenario (or with no scenarios at all, which simulates as "idle") nothing plays or drives a speaker,
  buzzer or motor: never give such a load, or the supply "including" it, a minimum current there; a max is fine.
- To change a current, change a part's VALUE. Never add copies of a part in parallel (no R1b, R1c beside R1).
- supply_ma_avg only as a loose sanity bound that counts every load: an LED 2-15 mA, a buzzer 10-30 mA, a fan or
  motor 50-200 mA. A load that follows a blink is on about half the time, so its avg_ma is about half its on current.
- Scenarios only when the ask is about light, battery voltage or a button. A plain blinker or fan has NO scenarios.
  Every scenario must set ldr, voltage or switch; a scenario with only a name changes nothing in the simulation.
  A sensor none of those drive (soil moisture, gas, ...) goes in missing; never stand it in with plain resistors.
- why: one plain sentence per part, at most 10 words.
- Never substitute a different kind of part for one the ask names (no LDR as a tilt sensor). If the ask needs a part
  that is NOT in the KEPT list (tilt switch, reed switch, ...), list it in missing with a common through-hole part
  number; Etch fetches it from a parts library and asks you again with it added. A switch-type sensor simulates as a
  switch: use scenarios with switch: pressed (e.g. tilted) and switch: released (e.g. level).

Known-good circuit patterns (textbook topologies; use the one that fits, then size values):
- Blinker / flasher (1-3 Hz): two-transistor astable multivibrator, wired exactly like this (net names are yours):
    R1 470: VCC - LED1_A ; D1 LED: A = LED1_A, K = Q1_C      (LED in SERIES with its resistor, never across it)
    R2 470: VCC - LED2_A ; D2 LED: A = LED2_A, K = Q2_C
    R3 47k: VCC - Q1_B   ; R4 47k: VCC - Q2_B
    C1 10u electrolytic: pin 1 (+) = Q1_C, pin 2 = Q2_B ; C2 10u electrolytic: pin 1 (+) = Q2_C, pin 2 = Q1_B
    Q1, Q2 2N3904: C = Qn_C, B = Qn_B, E = GND
  Each LED blinks at f ~ 0.72 / (R_base * C): 47k with 10u is about 1.5 Hz. With one LED, put a
  1k resistor from VCC to Q2_C instead of R2 + D2. A single transistor does NOT oscillate.
- Two-LED police flasher: the same astable with a red LED on one side and a blue LED on the other.
- Dark-activated (beep / light when dark): a divider at Q1 base: fixed resistor (47k-100k) from VCC to base, LDR from
  base to GND. Light: LDR is low, base is low, Q1 off. Dark: LDR is high, base rises, Q1 on. The load (buzzer + to VCC,
  - to Q1 collector; or LED + series resistor) sits between VCC and the collector. Emitter to GND.
- Fan or motor: NPN low-side switch. Base through 1k from a GPIO, or from a light divider with NO extra base-to-GND
  resistor (code then sizes the divider and adds a second transistor if the sensor is too weak), emitter GND, collector to the fan
  connector pin 2, connector pin 1 to VCC, a flyback diode (1N4148 or 1N400x) with K on VCC and A on the collector.
- Battery level indicator: one row per level: LED + series resistor (330-1k) + zener in series from VCC to GND
  (zener K toward VCC). A level lights when VCC > zener voltage + about 2 V. Pick zeners that split the range.
- Button light: push button in series with an LED + resistor across VCC and GND.
- Switch-triggered alarm (button, tilt or reed switch sounds a buzzer): switch from VCC to Q1_B through a 10k resistor,
  a 100k resistor from Q1_B to GND, buzzer + to VCC and - to Q1 collector, emitter GND. Never put a switch or the
  buzzer straight across VCC and GND."""


def _piece_line(p: dict[str, Any]) -> str:
    pins = ", ".join(
        f"{n}={nm}"
        for n, nm in sorted(p["pins"].items(), key=lambda kv: (len(kv[0]), kv[0]))
    )
    return f"{p['key']} | {p['family']} | pins {pins} | sim: {p['spice_note']}"


def _pick_call(stream: Stream, model: str, msgs: list) -> dict[str, Any]:
    """One picker answer: the main model with an output cap; on a timeout or model failure, one retry on the quick
    sub-model with a tight cap; when that fails too, the missing-parts event and PickTimeout."""
    try:
        ans, _ = llm.call_json(
            stream,
            "picker",
            model,
            msgs,
            PICK_FN,
            effort=PICKER_EFFORT,
            timeout=PICKER_TIMEOUT,
            max_tokens=PICKER_MAX_TOKENS,
        )
        return ans
    except (llm.ModelError, ValueError) as e:
        first = str(e)[:200]
    stream.emit(
        "picker",
        "retry",
        model=config.SUB_MODEL,
        reason=first,
        max_tokens=RETRY_MAX_TOKENS,
    )
    try:
        ans, _ = llm.call_json(
            stream,
            "picker",
            config.SUB_MODEL,
            msgs,
            PICK_FN,
            effort=PICKER_EFFORT,
            timeout=RETRY_TIMEOUT,
            max_tokens=RETRY_MAX_TOKENS,
        )
        return ans
    except (llm.ModelError, ValueError) as e:
        reason = f"{first} / retry: {str(e)[:200]}"
        # a timeout or an unparsed answer is a model failure, not a missing part (live-1792272020427: the GPS
        # tracker had every part kept and still told the user the library lacked them)
        say = MISSING_SAY if "missing" in reason.lower() else SLOW_SAY
        stream.emit("picker", "missing_parts", reason=reason, text=say)
        stream.emit("lead", "say", text=say)
        raise PickTimeout(say) from e


def pick(
    stream: Stream,
    ask: str,
    kept: list[dict[str, Any]],
    extra: str = "",
    model: str | None = None,
) -> tuple[dict[str, Any], list]:
    t0 = time.time()
    model = model or PICKER_MODEL
    stream.emit("picker", "start", model=model, kept=len(kept), effort=PICKER_EFFORT)
    msgs = [
        {
            "role": "system",
            "content": "You are Etch's picker. You design small through-hole circuit boards from KiCad library parts.\n"
            + PICK_RULES,
        },
        {
            "role": "user",
            "content": f"Board request: {ask}\n{extra}\nKEPT list (key | family | pins | simulation):\n"
            + "\n".join(_piece_line(p) for p in kept),
        },
    ]
    ans = _pick_call(stream, model, msgs)
    msgs.append({"role": "assistant", "content": json.dumps(ans)})
    stream.emit(
        "picker",
        "pick",
        seconds=round(time.time() - t0, 2),
        parts=[
            {k: p.get(k) for k in ("ref", "piece", "value", "group", "why")}
            for p in ans.get("parts", [])
        ],
        scenarios=ans.get("scenarios", []),
        benchmarks=ans.get("benchmarks", []),
        say=ans.get("say"),
    )
    return ans, msgs


# ---------------------------------------------------------------- stage 3: assembly


def _family(key: str) -> str:
    return load_index()[key]["category"]


def auto_layout(parts: list[dict[str, Any]], groups: list[str]) -> list[dict[str, Any]]:
    """Function blocks left to right; inside a block, outputs/sensors on top, resistors in the middle,
    transistors, diodes and capacitors at the bottom; the title sits in the top row."""
    by_group: dict[str, list[dict[str, Any]]] = {}
    for p in parts:
        by_group.setdefault(p["group"], []).append(p)
    order = [g for g in groups if g in by_group] + [
        g for g in by_group if g not in groups
    ]
    blocks, title_done = [], False
    widest = max(
        (g for g in order if g != "power"), key=lambda g: len(by_group[g]), default=None
    )
    for g in order:
        ps = by_group[g]
        if g == "power":
            blocks.append(
                {"name": "power", "valign": "center", "rows": [[p["ref"]] for p in ps]}
            )
            continue
        tiers = [[], [], []]
        for p in ps:
            f = _family(p["piece"])
            t = 0 if f in TOP or "connector" in f else 1 if f in MID else 2
            tiers[t].append(p["ref"])
        rows = []
        for tier in tiers:
            for k in range(0, len(tier), 4):
                rows.append(tier[k : k + 4])
        rows = [r for r in rows if r]
        if not title_done and g == widest:
            top = rows[0] if rows else []
            if len(top) <= 2:
                rows[0] = top[:1] + ["@title", "@title"] + top[1:]
            else:
                rows.insert(0, ["@title", "@title"])
            title_done = True
        blocks.append({"name": g, "rows": rows})
    return blocks


def _rot(key: str, group: str) -> int:
    f = _family(key)
    if group == "power" and "connector" in f:
        return 270
    if f == "resistor":
        return 90
    return 0


def to_spec(design: dict[str, Any], code: str) -> dict[str, Any]:
    idx = load_index()
    if missing := {p["piece"] for p in design["parts"] if p["piece"] not in idx}:
        # pieces from the full library (not the curated catalog): make them known to the engine's index
        cat = _cat()
        register([cat[k] for k in missing if k in cat])
    parts = []
    for p in design["parts"]:
        e = {
            "ref": p["ref"],
            "part": p["piece"],
            "value": str(p.get("value") or "") or None,
            "pins": {str(k): str(v) for k, v in p["pins"].items()},
            "rot": _rot(p["piece"], p["group"]),
            "why": p.get("why", ""),
        }
        if p.get("load_ohms"):
            e["spice"] = {"role": "load", "ohms": float(p["load_ohms"])}
        if _family(p["piece"]) == "thermistor" and not re.fullmatch(
            r"\d+(\.\d+)?\s*[kKmMR]?\w*", e["value"] or ""
        ):
            # the catalog's leaded NTC is 10k at 25 C; a blank value would become its symbol name in ngspice
            e["value"] = "10k"
        if e["value"] is None:
            e.pop("value")
        parts.append(e)
    groups = [g for g in ["power"] + [p["group"] for p in design["parts"]]]
    seen: list[str] = []
    for g in groups:
        if g not in seen:
            seen.append(g)
    bench = [
        {"check": "drc_violations", "max": 0, "text": "DRC clean"},
        {"check": "erc_errors", "max": 0, "text": "ERC has no errors"},
        {"check": "unconnected", "max": 0, "text": "all nets connected"},
        {"check": "parity", "max": 0, "text": "board matches schematic"},
    ]
    gate_names = {b["check"] for b in bench}
    scen = [s["name"] for s in design.get("scenarios") or [] if s.get("name")]
    for b in design.get("benchmarks", []):
        if b["check"] in gate_names:
            continue
        row = {
            k: b[k]
            for k in ("check", "min", "max", "text")
            if k in b and b[k] is not None
        }
        # with scenarios every simulated metric is named per scenario; an unprefixed one must hold in each,
        # unless its text names one scenario ("buzzer on when dry") or a scenario already has its own row for
        # that metric (sweep 10-17: "on when dry" was copied into wet next to "off when wet", which can never pass)
        if scen and not any(row["check"].startswith(n + ".") for n in scen):
            named = [
                n
                for n in scen
                if re.search(rf"\b{re.escape(n)}\b", row.get("text", ""), re.I)
            ]
            own = {
                n
                for n in scen
                for o in design.get("benchmarks", [])
                if o.get("check") == f"{n}.{row['check']}"
            }
            targets = named if len(named) == 1 else [n for n in scen if n not in own]
            bench += [
                {**row, "check": f"{n}.{row['check']}", "text": f"{row['text']} ({n})"}
                for n in targets
            ]
        else:
            bench.append(row)
    nets = {n for p in parts for n in p["pins"].values()}
    power_nets = [n for n in ("VCC", "GND") if n in nets]
    title = (
        re.sub(r"[^A-Z0-9 \-]", "", str(design.get("title", "ETCH BOARD")).upper())[
            :16
        ].strip()
        or "ETCH BOARD"
    )
    spec = {
        "name": re.sub(r"[^a-z0-9_]", "_", str(design.get("name", "board")).lower())[
            :40
        ]
        or "board",
        "title": None,
        "description": design.get("say", ""),
        "power": {
            "input": "first connector (pin 1 = +, pin 2 = GND)",
            "voltage": float(design.get("voltage", 5.0)),
        },
        "board": {"thickness": 1.6, "layers": 2},
        "rules": {
            "track": 0.3,
            "clearance": 0.2,
            "via_d": 0.6,
            "via_drill": 0.3,
            "edge_clearance": 0.5,
            "power_nets": power_nets,
            "power_track": 0.6,
            # a recipe with fine-pitch parts (ESP32 boards: 0.5 mm USB-C pads) narrows track and clearance
            **{
                k: float(v)
                for k, v in (design.get("rules") or {}).items()
                if k in ("track", "clearance")
            },
        },
        "layout": {
            "margin": 2.5,
            "gap": 2.5,
            "hole": {"r": 3.5, "inset": 3.5},
            # a recipe may lay out its own blocks (a key matrix wants a grid, not rows of four by family)
            "blocks": design.get("layout_blocks") or auto_layout(design["parts"], seen),
        },
        "finish": {
            "mask": "#08502AE6",
            "copper_finish": "ENIG",
            "outline": {"corner_r": 3.0},
            "pour": {
                "net": "GND",
                "layers": ["F.Cu", "B.Cu"],
                "clearance": 0.3,
                "min_width": 0.25,
                "thermal_gap": 0.3,
                "thermal_bridge": 0.4,
            },
            "stitch": {"pitch": 5.08},
            "fiducials": 3,
            "title": {"text": title, "sub": f"ETCH {code} REV A", "size": 1.2},
            "silk": [
                {
                    "text": "parts: KiCad 10 libraries",
                    "anchor": "bottom",
                    "size": 0.8,
                    "layer": "B.SilkS",
                }
            ],
        },
        "parts": parts,
        "benchmarks": bench,
    }
    if design.get("scenarios"):
        spec["sim"] = {
            "scenarios": [
                {k: v for k, v in s.items() if v is not None}
                for s in design["scenarios"]
            ]
        }
    return spec


def _metric_names(out: Path) -> list[str]:
    try:
        s = json.loads((out / "sim.json").read_text())
    except (OSError, ValueError):
        return []
    return sorted(
        k
        for k, v in s.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    )


def assemble(
    stream: Stream,
    design: dict[str, Any],
    msgs: list,
    out: Path,
    code: str,
    ask: str,
    kept: list[dict[str, Any]],
    must=None,
    max_iters: int = MAX_ITERS,
    prior: Path | None = None,
) -> dict[str, Any]:
    """must(design, out) -> None, or why the passing build does not do what was asked. prior: the board an
    edit starts from; its placement and unchanged copper seed the build (a full build runs if that fails)."""
    t0 = time.time()
    keys = {p["key"] for p in kept}
    tried: set[str] = set()  # made-up piece names already sent to sourcing once
    v: dict[str, Any] | None = None
    last_fail = None
    stuck_prev: dict[
        str, Any
    ] = {}  # failing benchmark -> (value, design values) on the previous built try
    for it in range(1, max_iters + 1):
        ti = time.time()
        stream.emit("assembly", "iteration", iteration=it, phase="place-route-check")
        for part in design.get("parts", []):
            # a key copied without its "kicad:" prefix (or other head) still names exactly one kept piece
            pc = str(part.get("piece", ""))
            if pc not in keys and len(hits := [k for k in keys if k.endswith(pc)]) == 1:
                part["piece"] = hits[0]
            # ... or with the wrong library in front ("Device:Conn_01x02@..." for "Connector_Generic:Conn_01x02@...")
            elif (
                pc not in keys
                and ":" in pc
                and len(
                    hits := [k for k in keys if k.endswith(":" + pc.split(":")[-1])]
                )
                == 1
            ):
                part["piece"] = hits[0]
        if unknown := [
            p for p in design.get("parts", []) if p.get("piece") not in keys
        ]:
            _resolve_unknown(stream, unknown, kept, keys, tried)
        _normalize_pins(design, kept)
        if joined := _merge_lonely(
            design, kept
        ):  # before _auto_mcu: a split name is not a reason to add a Pico
            stream.emit("assembly", "repair", iteration=it, kind="nets", joined=joined)
        if leds := _fix_leds(design, kept):  # before _auto_mcu: a lone cathode is not a signal
            keys.update(str(p["piece"]) for p in design.get("parts", []) if p.get("piece"))
            stream.emit("lead", "say", text="LEDs: " + "; ".join(leds) + ".")
        if radio := _radio_fix(design, kept, ask):
            keys.update(
                str(p["piece"]) for p in design.get("parts", []) if p.get("piece")
            )
            stream.emit("lead", "say", text="Radio: " + "; ".join(radio) + ".")
        if added := _auto_mcu(design, kept, ask):
            keys.add(added["piece"])
            stream.emit("lead", "say", text=added["say"])
        _wire_named_gpio(design, kept)
        if firmware.mcu_part(design, kept):
            _wire_hinted(design, kept)
        firmware.tie_grounds(design, kept)
        _wire_mcu_supply(design, kept)
        if drives := _wire_drives(design, kept):
            stream.emit("lead", "say", text="Wired " + "; ".join(drives) + ".")
        _drop_lonely_gpio(design, kept)
        if dropped := _drop_dangling_passives(design, kept):
            stream.emit("lead", "say", text=f"Dropped {', '.join(dropped)}: one end went nowhere.")
        _prune_commands(design)
        stand_ins = _led_stand_ins(
            design, kept, keys
        )  # before _fix_refs renames D1 on a capacitor to C1
        _fix_refs(design)
        if folded := _fold_parallel(design):
            stream.emit(
                "assembly", "repair", iteration=it, kind="parallel", joined=folded
            )
        firmware.default_commands(
            design, kept
        )  # GPIO-driven LEDs with no program: 1..N, a = all, o = off
        an_findings = _analog(stream, design, kept, keys, ask)
        bad = [
            p["piece"] for p in design.get("parts", []) if p.get("piece") not in keys
        ]
        err = None
        spec_path = out / "spec.yaml"
        (out / f"design-try{it}.json").write_text(
            json.dumps(design, indent=1)
        )  # what each try really was
        lonely = _lonely_nets(design)
        mism = _ref_mismatch(design)
        if bad:
            err = f"pieces not in the KEPT list: {bad}"
        elif stand_ins:
            err = (
                f"{', '.join(stand_ins)} stand in for LEDs on resistor or capacitor pieces, which simulate as the wrong "
                f"part. Use the LED piece {LED_KEY} (pin 2 = A anode, pin 1 = K cathode), one per colour, ref D1, "
                "D2, ..., value = its colour (red, green, blue). An RGB LED is three of them."
            )
        elif mism:
            err = f"ref and piece do not match (a capacitor must use a capacitor piece, and so on): {mism}"
        elif lonely:
            where = [
                f"{n} (only on {p.get('ref')} pin {k}{_lonely_hint(design, n)})"
                for n in lonely
                for p in design.get("parts", [])
                for k, v in (p.get("pins") or {}).items()
                if str(v) == n
            ]
            err = (
                "these nets touch only one pin, so they connect nothing. Give the pin at the OTHER end the SAME "
                f"net name (for a sensor output, the MCU input pin it feeds): {where}"
            )
        elif silent := _no_sound_source(design, kept, ask):
            err = silent
        elif lint := _wiring_lint(design, kept):
            err = (
                "wiring mistakes: "
                + "; ".join(lint)
                + "\nYour nets now:\n"
                + _wiring(design, kept)
            )
        elif firmware.mcu_part(design, kept) and isinstance(
            fw := firmware.build(design, kept, out), str
        ):
            err = fw + "\nYour nets now:\n" + _wiring(design, kept)
        elif firmware.mcu_part(design, kept) and isinstance(
            fb_rows := firmware.benchmarks(design, kept), str
        ):
            err = fb_rows
        else:
            if firmware.mcu_part(design, kept):
                design["scenarios"], fw_rep = fw
                design["benchmarks"] = fb_rows
                stream.emit(
                    "assembly",
                    "firmware",
                    iteration=it,
                    pins=fw_rep["pins"],
                    tests=fw_rep["tests"],
                )
                stream.emit(
                    "assembly",
                    "gate",
                    iteration=it,
                    gate=f"firmware test ({fw_rep.get('runtime', 'MicroPython')})",
                    ok=True,
                    value=f"{len(fw_rep['tests'])} key tests pass",
                )
            spec = to_spec(design, code)
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False, width=110))
            err = eng.validate(spec_path)
        stream.emit(
            "assembly",
            "gate",
            iteration=it,
            gate="spec loads (parts, pins)",
            ok=err is None,
            value=err or "ok",
        )
        if not err:
            try:
                seed = _layout_seed(spec)
                from_prior = (
                    seed is None
                    and prior is not None
                    and (prior / "layout.json").is_file()
                )
                seed = seed or (prior if from_prior else None)
                if seed:
                    stream.emit(
                        "assembly",
                        "seed",
                        iteration=it,
                        base=seed.name,
                        note="the edited board's placement and unchanged copper are kept"
                        if from_prior
                        else "same parts and nets as an earlier passing board: its placement and copper are reused",
                    )
                try:
                    v = eng.run_build(spec_path, out, base=seed)
                except Exception:
                    if not from_prior:
                        raise
                    v = {
                        "pass": False
                    }  # new parts the kept layout cannot take: full build below
                if from_prior and not v["pass"]:
                    stream.emit(
                        "assembly",
                        "seed",
                        iteration=it,
                        base=None,
                        note="kept layout failed a check: full build",
                        failed=_failed_checks(
                            v
                        ),  # which check sank the kept layout (report rank 1)
                        seconds=v.get("seconds"),
                    )
                    v = eng.run_build(spec_path, out)
            except Exception as e:
                err = f"the build crashed: {str(e)[-500:]}"
                stream.emit(
                    "assembly",
                    "gate",
                    iteration=it,
                    gate="build",
                    ok=False,
                    value=err[:300],
                )
        if err:
            fb = err + "\nFix it and submit the whole design again."
        else:
            rep = json.loads((out / "report.json").read_text())
            stream.emit(
                "assembly",
                "built",
                iteration=it,
                seconds=v["seconds"],
                stages_s=rep["stages_s"],
                board={"w": rep_board(out)[0], "h": rep_board(out)[1]},
                tracks=len(json.loads((out / "layout.json").read_text())["tracks"]),
            )
            for name, ok, val in [
                ("ERC", v["erc_errors"] == 0, v["erc_errors"]),
                ("DRC", v["drc_violations"] == 0, v["drc_violations"]),
                ("unconnected", v["unconnected"] == 0, v["unconnected"]),
                ("parity", v["parity"] == 0, v["parity"]),
                ("routed", not v["unrouted"], eng.routed_value(v)),
                ("3D models", v["models_missing"] == 0, v["models_missing"]),
                ("ngspice", bool(v["sim_ok"]), v.get("sim_error") or "ok"),
            ]:
                stream.emit(
                    "assembly", "gate", iteration=it, gate=name, ok=ok, value=val
                )
            static = _static_benchmarks(design, v, out, kept)
            stuck_prev, moved = _stuck_benchmarks(design, v, stuck_prev)
            static += moved
            if static:
                stream.emit(
                    "assembly",
                    "repair",
                    iteration=it,
                    kind="uncheckable",
                    checks=static,
                    note="these benchmarks measure a fixed load the simulation cannot change; not checked",
                )
            for b in v["benchmarks"]:
                ev = {"check": b["check"], "ok": b["pass"], "value": b["value"]}
                if b.get("status"):
                    ev["status"] = b["status"]
                stream.emit("assembly", "gate", iteration=it, gate=b["text"], **ev)
            lost = must(design, out) if (v["pass"] and must) else None
            if must and v["pass"]:
                stream.emit(
                    "assembly",
                    "gate",
                    iteration=it,
                    gate="requested change present",
                    ok=lost is None,
                    value=lost or "ok",
                )
            if v["pass"] and not lost:
                (out / "design.json").write_text(json.dumps(design, indent=1))
                _apply_mask(design, out)
                _layout_remember(spec, out)
                stream.emit(
                    "assembly",
                    "iteration",
                    iteration=it,
                    phase="decide",
                    decision="done",
                    seconds=round(time.time() - ti, 2),
                )
                return {
                    "ok": True,
                    "iterations": it,
                    "verdict": v,
                    "seconds": round(time.time() - t0, 2),
                }
            fb = (
                f"The checks pass, but the request is no longer met: {lost}. Never drop or revert the "
                "requested change to make checks pass; keep it and fix the problem another way. Submit the whole design again."
                if lost
                else _feedback(v, _metric_names(out))
                + "".join(f"\n- analog check: {f}" for f in an_findings)
                + _scenario_note(design)
                + "\nYour nets were:\n"
                + _wiring(design, kept)
            )
        sig = fb[:400]
        decision = "re-plan" if sig == last_fail else "repair"
        last_fail = sig
        stream.emit(
            "assembly",
            "iteration",
            iteration=it,
            phase="decide",
            decision=decision,
            why=fb[:400],
            seconds=round(time.time() - ti, 2),
        )
        if it == max_iters:
            break
        msgs.append({"role": "user", "content": fb})
        tr = time.time()
        try:
            design = _pick_call(stream, PICKER_MODEL, msgs)
        except PickTimeout:
            break  # the last built try's verdict stands
        msgs.append({"role": "assistant", "content": json.dumps(design)})
        stream.emit(
            "picker",
            "repair",
            iteration=it,
            seconds=round(time.time() - tr, 2),
            parts=[
                {k: p.get(k) for k in ("ref", "piece", "value", "group")}
                for p in design.get("parts", [])
            ],
            say=design.get("say"),
        )
    return {
        "ok": False,
        "iterations": max_iters,
        "verdict": v,
        "seconds": round(time.time() - t0, 2),
        "design": design,
    }


_RAIL_RX = re.compile(
    r"(?:GND\w*|AGND|DGND|VSS|VCC\w*|VDD\w*|VBUS|VIN|VSYS|P?3V3|\+?3\.?3V|\+?5V\d*|V5|\+?12V)",
    re.I,
)


def _static_benchmarks(
    design: dict[str, Any], v: dict[str, Any], out: Path, kept: list[dict[str, Any]]
) -> list[str]:
    """Failing benchmarks the simulation can never satisfy, marked in v (pass None, status "uncheckable") with
    v["pass"] recomputed. One case, kept narrow on purpose: a "load" part (fixed resistance in sim.py) whose load
    pins both sit on supply or ground rails draws the same current in every scenario, so per-scenario benchmarks
    on it that pass in one scenario and fail in another with the identical value contradict each other. A
    switched load (transistor, GPIO) or one that fails everywhere (fixable through load_ohms) is left alone.
    Field run live-1792266957411 burned 3 tries on "OLED little activity when dry" vs "OLED ~20 mA when wet"."""
    rows = v.get("benchmarks") or []
    if v.get("pass") or not any(b.get("pass") is False for b in rows):
        return []
    try:
        sim = json.loads((out / "sim.json").read_text())
    except (OSError, ValueError):
        return []
    scen = [s.get("name") for s in sim.get("scenarios") or [] if s.get("name")]
    marked = _idle_benchmarks(design, rows, sim, scen)
    if len(scen) < 2:
        return _remark(v, rows, marked)
    by_key = {p["key"]: p for p in kept}
    set_refs = {
        str(r) for s in design.get("scenarios") or [] for r in (s.get("set") or {})
    }  # a scenario that re-values the part makes it scenario-dependent
    static: set[str] = set()
    for part in design.get("parts", []):
        spice = (by_key.get(part.get("piece")) or {}).get("spice") or {}
        if spice.get("role") != "load" or part["ref"] in set_refs:
            continue
        nets = [part.get("pins", {}).get(str(n)) for n in spice.get("pins", [])]
        if nets and all(n and _RAIL_RX.fullmatch(n) for n in nets):
            static.add(part["ref"])
    groups: dict[str, list[dict[str, Any]]] = {}
    for b in rows:
        m = re.fullmatch(
            r"([A-Za-z0-9_]+)\.([A-Z]+\d+)\.(\w+)", str(b.get("check", ""))
        )
        if m and m.group(1) in scen and m.group(2) in static:
            groups.setdefault(f"{m.group(2)}.{m.group(3)}", []).append(b)
    for metric, bs in groups.items():
        vals = [sim.get(f"{s}.{metric}") for s in scen]
        if any(not isinstance(x, (int, float)) for x in vals):
            continue
        if max(vals) - min(vals) > 0.01 * max(1e-6, abs(max(vals))):
            continue  # it does change between scenarios: a real measurement
        if not any(b["pass"] is True for b in bs):
            continue  # fails everywhere: the load value can fix it
        for b in bs:
            if b["pass"] is False:
                b["pass"] = None
                b["status"] = "uncheckable"
                b["text"] += (
                    " (not checkable in simulation: fixed load, same in every scenario)"
                )
                marked.append(b["check"])
    return _remark(v, rows, marked)


def _remark(
    v: dict[str, Any], rows: list[dict[str, Any]], marked: list[str]
) -> list[str]:
    if marked:
        v["failed"] = [
            f"{b['text']} (got {b['value']})" for b in rows if b["pass"] is False
        ]
        v["uncheckable"] = marked
        v["pass"] = (
            not v["failed"] and not v.get("unrouted") and not v.get("models_missing")
        )
    return marked


def _part_values(design: dict[str, Any]) -> tuple:
    return tuple(
        sorted(
            (str(p.get("ref")), str(p.get("piece")), str(p.get("value")))
            for p in design.get("parts", [])
        )
    )


def _stuck_benchmarks(
    design: dict[str, Any], v: dict[str, Any], prev: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Third uncheckable case (prod job QsB-ok6GZtu7rQqc, 10-17): a current benchmark that fails with the same
    value on two built tries although the repair changed the parts or values in between is one the modelled loads
    cannot move; repairing it again only loops. Marked not checkable (v recomputed); returns the failing values
    to compare against on the next try, and the marked checks."""
    rows = v.get("benchmarks") or []
    vals = _part_values(design)
    marked = []
    for b in rows:
        if b.get("pass") is not False or not isinstance(b.get("value"), (int, float)):
            continue
        if not re.search(
            r"(?:^|\.)(supply_ma_avg|supply_ma_peak|avg_ma|peak_ma)$",
            str(b.get("check", "")),
        ):
            continue
        was = prev.get(b["check"])
        if (
            was
            and was[1] != vals
            and abs(was[0] - b["value"]) <= 0.01 * max(1e-6, abs(was[0]))
        ):
            b["pass"] = None
            b["status"] = "uncheckable"
            b["text"] += " (not checkable in simulation: no repair moves this reading)"
            marked.append(b["check"])
    now = {
        b["check"]: (b["value"], vals)
        for b in rows
        if b.get("pass") is False and isinstance(b.get("value"), (int, float))
    }
    if marked:
        _remark(v, rows, marked + list(v.get("uncheckable") or []))
    return now, marked


_SOUND_ASK = re.compile(
    r"\b(synth\w*|musical instrument|instrument|drum machine|sampler|theremin|sequencer|groovebox|keytar|"
    r"music (?:maker|box|board|machine)|make music|plays? (?:music|notes|tones|songs?|melod\w*)|tone generator|"
    r"noise ?maker|sound ?(?:effects?|board|maker))\b",
    re.I,
)
_SOUND_SOURCE = re.compile(
    r"555|NE555|LM555|TLC555|ICM7555|oscillat|XTAL|crystal|VCO|codec|DAC|PCM51|MAX98357|VS10[0-9]3|LM386|PAM8|TDA\d|"
    r"TPA\d|PT8211|ATTINY|ATMEGA|STM32|ESP32|RP2040|Pico|Arduino|74HC14|CD40\d\d|4093|40106|op.?amp|LM358|TL07|"
    r"Buzzer",
    re.I,
)


def _no_sound_source(
    design: dict[str, Any], kept: list[dict[str, Any]], ask: str
) -> str | None:
    """Functional sanity gate (prod job QsB-ok6GZtu7rQqc, "a music synthesizer ... intro producer", 10-17): a synth
    or instrument ask whose parts hold nothing that makes a signal (no MCU, oscillator, 555, codec/DAC or amp) is
    a passive network that cannot make sound. It goes back to the picker instead of being built."""
    if not _SOUND_ASK.search(ask) or firmware.mcu_part(design, kept):
        return None
    by_key = {p["key"]: p for p in kept}
    for part in design.get("parts", []):
        piece = str(part.get("piece", ""))
        info = by_key.get(piece) or {}
        text = " ".join(
            str(x)
            for x in (
                piece,
                part.get("value"),
                info.get("family"),
                info.get("description"),
            )
            if x
        )
        if _SOUND_SOURCE.search(text):
            return None
    return (
        "the ask is for an instrument that makes sound, but no part generates a signal: there is no microcontroller, "
        "oscillator (555), codec/DAC or amplifier, so the board is a passive network that stays silent. Add the "
        f"Raspberry Pi Pico ({PICO_KEY}) to make the tones (PWM on a GPIO through a resistor and capacitor to the "
        "audio jack or a buzzer), plus the controls the ask needs (buttons or keys on GPIO, knobs on ADC pins)"
    )


_IDLE = re.compile(r"idle|quiet|off|standby|rest|silent|sleep|default", re.I)


def _idle_benchmarks(
    design: dict[str, Any],
    rows: list[dict[str, Any]],
    sim: dict[str, Any],
    scen: list[str],
) -> list[str]:
    """Second uncheckable case (prod job QbyBcUKTtsmLEaJr, "a music synthesizer similar to an asr10", 10-17): in an
    idle scenario (named idle/quiet/off/..., or the simulator's own single "idle" when the design set none) nothing
    drives the speaker, so its modelled load reads 0 mA, and a band with a minimum on that load, or on the supply
    "including the speaker", asks the idle board to draw active current it never can. Marked only when every
    modelled load reads ~0 in that scenario and the failing row wants MORE than it got; an over-limit reading or
    a load that does draw current stays a real failure."""
    loads = [str(r) for r in (sim.get("loads") or {})]
    leds = (
        [str(r) for r in (sim.get("leds") or {})]
        if isinstance(sim.get("leds"), dict)
        else []
    )
    if not loads:
        return []
    # verdict rows carry no band: the minimum comes from the design's own benchmark (prefixed by scenario or not)
    bands: dict[str, Any] = {}
    for d in design.get("benchmarks") or []:
        if isinstance(d, dict):
            c = str(d.get("check", ""))
            bands[c] = d.get("min")
            bands.setdefault(
                re.sub(r"^[A-Za-z0-9_]+\.(?=supply_|[A-Z]+\d+\.)", "", c), d.get("min")
            )
    marked = []
    for b in rows:
        if b.get("pass") is not False:
            continue
        m = re.fullmatch(
            r"(?:([A-Za-z0-9_]+)\.)?(supply_ma_avg|supply_ma_peak|([A-Z]+\d+)\.(avg_ma|peak_ma))",
            str(b.get("check", "")),
        )
        if not m:
            continue
        sc = m.group(1) or (scen[0] if len(scen) == 1 else None)
        if (
            not sc
            or sc not in scen
            or not (_IDLE.search(sc) or not design.get("scenarios"))
        ):
            continue
        if m.group(3) and m.group(3) not in loads:
            continue
        if any(
            abs(float(sim.get(f"{sc}.{r}.avg_ma") or 0)) > 0.05 for r in loads + leds
        ):
            continue  # a load does draw current here: a real measurement
        val = b.get("value")
        lo = b.get("min", bands.get(f"{sc}.{m.group(2)}", bands.get(m.group(2))))
        if not isinstance(val, (int, float)) or lo is None or val >= float(lo):
            continue  # not a "wants more than the idle board draws" failure
        b["pass"] = None
        b["status"] = "uncheckable"
        b["text"] += (
            " (not checkable in simulation: nothing drives the load while idle)"
        )
        marked.append(b["check"])
    return marked


def _failed_checks(v: dict[str, Any]) -> list[str]:
    """The checks a build verdict failed, by name ("crashed" when the kept layout could not even build)."""
    if "erc_errors" not in v:
        return ["crashed"]
    out = [
        name
        for name, bad in (
            ("ERC", v.get("erc_errors")),
            ("DRC", v.get("drc_violations")),
            ("unconnected", v.get("unconnected")),
            ("parity", v.get("parity")),
            ("routed", v.get("unrouted")),
            ("3D models", v.get("models_missing")),
            ("ngspice", not v.get("sim_ok")),
        )
        if bad
    ]
    out += [
        f"benchmark {b.get('check')}"
        for b in v.get("benchmarks", [])
        if b.get("pass") is False
    ]
    return out


def _normalize_pins(design: dict[str, Any], kept: list[dict[str, Any]]) -> None:
    """Pins given by NAME ("C", "Pin_1", "K") become the piece's pin NUMBER when the name is unique on it."""
    by_key = {p["key"]: p for p in kept}
    for part in design.get("parts", []):
        piece = by_key.get(part.get("piece"))
        if not piece or not isinstance(part.get("pins"), dict):
            continue
        names: dict[str, list[str]] = {}
        for num, nm in piece["pins"].items():
            for alias in {
                str(nm).lower(),
                f"pin_{num}".lower(),
            }:  # a set: "Pin_1" is often both
                names.setdefault(alias, []).append(num)
        fixed = {}
        for k, net in part["pins"].items():
            k = str(k)
            if k in piece["pins"]:
                fixed[k] = net
            elif len(names.get(k.lower(), [])) == 1:
                fixed[names[k.lower()][0]] = net
            else:
                fixed[k] = net  # left as is: the spec gate names it
        part["pins"] = fixed


# piece family from its library category (the index mixes "LED"/"led", "BJT transistor"/"transistor", ...)
_FAMILY_WORDS = (
    ("C", ("capacitor",)),
    ("R", ("resistor",)),
    ("D", ("led", "diode")),
    ("Q", ("transistor", "mosfet")),
)


def _ref_letter_family(category: str) -> str | None:
    c = category.lower()
    if "potentiometer" in c:
        return None
    # whole words: "OLED display module" is not an LED (QA 10-17: the OLED socket was renamed D1)
    return next(
        (
            f
            for f, words in _FAMILY_WORDS
            if any(re.search(rf"\b{w}s?\b", c) for w in words)
        ),
        None,
    )


def _fix_refs(design: dict[str, Any]) -> None:
    """Rename a part whose ref letter is the wrong family (U1 on an RGB LED becomes D3) instead of failing the
    iteration on it. Mentions of the old ref in scenarios, benchmarks and notes are renamed with it."""
    idx = load_index()
    used = {str(p.get("ref")) for p in design.get("parts", [])}
    for p in design.get("parts", []):
        old = str(p.get("ref", ""))
        m = re.match(r"[A-Z]+", old)
        letter = m.group(0) if m else ""
        fam = _ref_letter_family(str(idx.get(p.get("piece"), {}).get("category") or ""))
        if not (
            fam and letter != fam
        ):  # "LED1" / "CLED1" on an LED simulate as the wrong element too
            continue
        n = 1
        while f"{fam}{n}" in used:
            n += 1
        new = f"{fam}{n}"
        used.add(new)
        p["ref"] = new
        pat = re.compile(rf"\b{re.escape(old)}\b")
        for k in ("scenarios", "benchmarks", "say", "layout_blocks"):
            if k in design:
                design[k] = json.loads(pat.sub(new, json.dumps(design[k])))


def _ref_mismatch(design: dict[str, Any]) -> list[str]:
    """R1 on a capacitor piece (or C1 on a resistor) builds and routes fine but simulates as the wrong part.
    Flags only a known-wrong pair: a resistor, capacitor, diode/LED or transistor piece under another of the
    R/C/D/Q/U letters. Parts fetched at run time ("fetched") and ICs, modules and sensors have no fixed letter."""
    out = []
    idx = load_index()
    for p in design.get("parts", []):
        m = re.match(r"[A-Z]+", str(p.get("ref", "")))
        letter = m.group(0) if m else ""
        fam = _ref_letter_family(str(idx.get(p.get("piece"), {}).get("category") or ""))
        if fam and letter in ("R", "C", "D", "Q", "U") and letter != fam:
            out.append(f"{p['ref']} is a {idx[p['piece']]['category']}")
    return out


def _wiring(design: dict[str, Any], kept: list[dict[str, Any]]) -> str:
    """The design's nets as the picker wrote them, with pin names, so a repair can see its own wiring."""
    by_key = {p["key"]: p for p in kept}
    nets: dict[str, list[str]] = {}
    for part in design.get("parts", []):
        names = (by_key.get(part.get("piece")) or {}).get("pins", {})
        for num, net in (part.get("pins") or {}).items():
            nm = names.get(str(num))
            nets.setdefault(str(net), []).append(
                f"{part['ref']}.{num}" + (f"({nm})" if nm and nm != str(num) else "")
            )
    return "\n".join(f"  {n}: {', '.join(v)}" for n, v in sorted(nets.items()))


def _fmt_ohms(x: float) -> str:
    for div, suf in ((1e6, "M"), (1e3, "k"), (1.0, "")):
        if x >= div:
            return f"{x / div:.3g}{suf}"
    return f"{x:.3g}"


def _fold_parallel(design: dict[str, Any]) -> list[str]:
    """A repair that "fixes" a current check by stacking copies of a resistor (R1, R1b, R1c in parallel across the
    same two nets; prod job QbyBcUKTtsmLEaJr 10-17) becomes ONE resistor with the combined value: the board the
    picker meant, without the parallel pattern the wiring lint rejects. Only copies named after the first part
    (R1 + R1b / R1_2 / R1-2) on the same piece and the same net pair are folded. Returns notes."""
    from .recipes import sim_ohms  # noqa: PLC0415

    parts = design.get("parts", [])
    notes: list[str] = []
    by_ref = {str(p.get("ref")): p for p in parts}
    drop: set[str] = set()
    for p in parts:
        ref = str(p.get("ref", ""))
        if ref in drop or not re.fullmatch(r"R\d+", ref):
            continue
        pair = sorted(map(str, (p.get("pins") or {}).values()))
        copies = [
            q
            for r, q in by_ref.items()
            if r != ref
            and re.fullmatch(rf"{ref}[_-]?[a-z0-9]+", r, re.I)
            and not re.fullmatch(r"R\d+", r)
            and q.get("piece") == p.get("piece")
            and sorted(map(str, (q.get("pins") or {}).values())) == pair
        ]
        if not copies or len(pair) != 2:
            continue
        try:
            g = sum(1.0 / sim_ohms(x.get("value")) for x in [p, *copies])
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        old = p.get("value")
        p["value"] = _fmt_ohms(1.0 / g)
        drop.update(str(q["ref"]) for q in copies)
        notes.append(
            f"{ref} {old} + {', '.join(str(q['ref']) for q in copies)} in parallel -> {ref} {p['value']}"
        )
    if drop:
        design["parts"] = [p for p in parts if str(p.get("ref")) not in drop]
    return notes


def _wiring_lint(design: dict[str, Any], kept: list[dict[str, Any]]) -> list[str]:
    """Structural mistakes no simulation is needed to see: shorted parts, parts in parallel that must be in series,
    transistors with collector, base or emitter tied together."""
    by_key = {p["key"]: p for p in kept}
    issues, two = [], {}
    for part in design.get("parts", []):
        pins = {str(k): str(v) for k, v in (part.get("pins") or {}).items()}
        ref = str(part.get("ref", ""))
        if len(pins) == 2 and not ref.startswith("J"):
            a, b = sorted(pins.values())
            if a == b:
                issues.append(f"{ref} has both pins on net {a}, so it is shorted out")
            two.setdefault((a, b), []).append(ref)
        if ref.startswith("Q"):
            nm = {
                str(n).upper(): pins.get(k)
                for k, n in (by_key.get(part.get("piece")) or {})
                .get("pins", {})
                .items()
            }
            for x, y in (("C", "E"), ("B", "E"), ("C", "B")):
                if nm.get(x) and nm.get(x) == nm.get(y):
                    issues.append(f"{ref} has {x} and {y} on the same net {nm[x]}")
    fam = {
        str(p.get("ref")): str((by_key.get(p.get("piece")) or {}).get("family") or "")
        for p in design.get("parts", [])
    }
    for (a, b), refs in two.items():
        # an LED, not any D: a flyback diode across a fan or coil is exactly right (fan board, 10-17)
        if len(refs) > 1 and any(
            r.startswith("D") and fam.get(r) != "diode" for r in refs
        ):
            issues.append(
                f"{' and '.join(refs)} sit side by side across {a} and {b}: an LED's resistor goes in SERIES (its own net between them), never across it"
            )
        elif len(refs) > 1 and {r[0] for r in refs} == {"R"}:
            issues.append(
                f"{' and '.join(refs)} are in parallel across {a} and {b}; each resistor in the pattern has its own pair of nets"
            )
    return issues


def _wire_named_gpio(design: dict[str, Any], kept: list[dict[str, Any]]) -> None:
    """The picker often names a net after the MCU pin it means (GPIO15, GP16) or drives it from the firmware, yet
    leaves it off the MCU's own pins, so the net ends on one resistor pin and the iteration fails as lonely (QA
    10-17, live-1792256372437: U1 had only GND and VCC, GPIO15/16/17 only on R1/R2/R3 pin 1, three tries in a row).
    Put each such net on the module pin of that name, or on the next free GPIO pin."""
    mp = firmware.mcu_part(design, kept)
    if not mp:
        return
    part, piece = mp
    names = {str(k): str(v) for k, v in (piece.get("pins") or {}).items()}
    pins = part.setdefault("pins", {})
    driven = {
        str(n)
        for c in (design.get("firmware") or {}).get("commands") or []
        for n in c.get("high") or []
    }
    on_parts = {
        str(n)
        for q in design.get("parts", [])
        for n in (q.get("pins") or {}).values()
        if str(n).upper() not in ("GND",) + _SUPPLY_5V + _SUPPLY_3V3
    }
    # a net the firmware drives belongs on the MCU even when it already joins two other pins (R and Q base)
    for net in _lonely_nets(design) + sorted(
        (driven & on_parts) - set(_lonely_nets(design))
    ):
        if net in pins.values():
            continue
        m = re.fullmatch(r"(?:GPIO|GP|IO|D)_?(\d+)", net.upper())
        if not m and net not in driven:
            continue
        want = f"GPIO{int(m.group(1))}" if m else None
        free = [
            n for n in names if n not in pins and names[n].upper().startswith("GPIO")
        ]
        hit = [
            n for n in free if want and re.fullmatch(want + r"(_.*)?", names[n].upper())
        ]
        if hit or free:
            pins[(hit or free)[0]] = net


def _drop_lonely_gpio(design: dict[str, Any], kept: list[dict[str, Any]]) -> None:
    """A net that lands only on one MCU GPIO pin wires nothing: it is an unused pin with a name. Leave the pin
    unconnected (like every other spare GPIO) instead of failing the whole iteration on it. The same goes for a
    supply net on the module's own power pin (VCC only on the Pico's VBUS): a USB-powered module whose rail
    nothing else uses (QA 10-17, live-1792256195647 failed its last try on "VCC only on U1 pin 40")."""
    mp = firmware.mcu_part(design, kept)
    if not mp:
        return
    part, piece = mp
    lonely = set(_lonely_nets(design))
    pins = part.get("pins") or {}
    # Any pin of the module whose net ends there is an unused pin, whatever its name: the picker often labels
    # RUN, 3V3_EN, ADC_VREF or AGND with their own names (live-1792272486984, a GPS tracker failed twice on
    # "3V3_EN only on U1 pin 37"). A module pin left open is how the module ships.
    for num in [n for n, net in pins.items() if str(net) in lonely]:
        del pins[num]


R_KEY = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"


def _net_count(design: dict[str, Any]) -> dict[str, int]:
    count: dict[str, int] = {}
    for p in design.get("parts", []):
        for net in (p.get("pins") or {}).values():
            count[str(net)] = count.get(str(net), 0) + 1
    return count


def _pin_named(piece: dict[str, Any], name: str) -> str | None:
    return next(
        (
            str(n)
            for n, nm in (piece.get("pins") or {}).items()
            if str(nm).upper() == name
        ),
        None,
    )


def _fix_leds(design: dict[str, Any], kept: list[dict[str, Any]]) -> list[str]:
    """Single LEDs the picker left half wired, repaired in code instead of another model try (10-17 tracker runs:
    live-1792273274109 "LED1_K only on D1 pin 1" twice, live-1792273514460 a lone LED on nets A and K,
    live-1792273474850 "R1 has both pins on net LED_NET"). Per LED: a series resistor with both ends on the anode's
    net is split (MCU side keeps the net, the anode gets its own); a dangling cathode goes to ground; a dangling
    anode joins a resistor end that dangles, or gets a new 220 ohm resistor whose far end the MCU drives; an anode
    on the drive net beside a resistor whose other end dangles moves to that end. Runs before _auto_mcu, so a lone
    cathode is never mistaken for a signal. Returns notes for the say line."""
    by_key = _cat() | {p["key"]: p for p in kept}
    parts = design.get("parts", [])
    count = _net_count(design)
    gnd = max(
        (n for n in count if _norm_net(n) in _GND_ALIASES),
        key=lambda n: count[n],
        default="GND",
    )
    notes: list[str] = []

    def fam(p: dict[str, Any]) -> str:
        return str((by_key.get(p.get("piece")) or {}).get("family") or "").lower()

    def fresh(base: str) -> str:
        names = set(_net_count(design))
        n, i = base, 2
        while n in names:
            n, i = f"{base}{i}", i + 1
        return n

    res = [p for p in parts if fam(p) == "resistor" and len(p.get("pins") or {}) == 2]
    for led in [p for p in parts if fam(p) == "led"]:
        piece = by_key[led["piece"]]
        a, k = _pin_named(piece, "A"), _pin_named(piece, "K")
        if not a or not k:
            continue
        ref, pins = str(led.get("ref")), led.setdefault("pins", {})
        if (
            a in pins
            and _norm_net(str(pins[a])) in _GND_ALIASES
            and _norm_net(str(pins.get(k, ""))) not in _GND_ALIASES
        ):
            # drawn backwards, anode on ground (live-1792274265002: "LED1_A" on pin 1, which is K)
            pins[a], pins[k] = pins.get(k), pins[a]
            if pins[a] is None:
                del pins[a]
            notes.append(f"{ref} turned around, its anode was on {pins[k]}")
        an = str(pins[a]) if a in pins else None
        for r in res:
            rp = r["pins"]
            if an and {str(v) for v in rp.values()} == {an}:
                new = fresh(f"{ref}_A")
                rp[sorted(rp)[-1]] = new
                pins[a] = new
                notes.append(
                    f"{r.get('ref')} was shorted on {an}, so {ref}'s anode now sits on {new} behind it"
                )
        count = _net_count(design)
        if k not in pins or count.get(str(pins[k]), 0) == 1:
            pins[k] = gnd
            notes.append(f"{ref} cathode to {gnd}")
        an = str(pins[a]) if a in pins else None
        count = _net_count(design)
        dangling = [  # (resistor, its dangling net) whose other end is wired
            (r, str(v))
            for r in res
            for v in r["pins"].values()
            if count.get(str(v)) == 1
            and str(v) != an
            and sum(count.get(str(w), 0) > 1 for w in r["pins"].values()) == 1
        ]
        if an is None or count.get(an) == 1:
            if dangling:
                pins[a] = dangling[0][1]
                notes.append(f"{ref} anode joined {dangling[0][0].get('ref')}")
            elif (
                not any(an and an in map(str, r["pins"].values()) for r in res)
                and R_KEY in by_key
            ):
                an = an or fresh(f"{ref}_A")
                pins[a] = an
                used = {str(p.get("ref")) for p in parts}
                n = 1
                while f"R{n}" in used:
                    n += 1
                drive = fresh(f"{ref}_DRV")
                parts.append(
                    {
                        "ref": f"R{n}",
                        "piece": R_KEY,
                        "value": "220",
                        "pins": {"1": drive, "2": an},
                        "group": led.get("group", "io"),
                        "why": f"Limits the current through {ref}: Etch added it because the LED had none.",
                    }
                )
                if by_key[R_KEY] not in kept:
                    kept.append(by_key[R_KEY])
                res.append(parts[-1])
                notes.append(f"added R{n}, 220 ohm, in series with {ref}")
        else:
            for r, net in dangling:
                if an in map(str, r["pins"].values()):
                    pins[a] = net
                    notes.append(f"{ref} anode moved behind {r.get('ref')}")
                    break
    return notes


def _wire_drives(design: dict[str, Any], kept: list[dict[str, Any]]) -> list[str]:
    """Outputs and buttons the microcontroller should run but does not reach get a free GPIO pin each (10-17
    tracker runs: an LED's resistor hung on VBUS, so "the microcontroller U1 has no GPIO pin wired to anything").
    An LED's drive end (its resistor's far end, or the anode when it has none), a buzzer's + and a button's live
    side move to a GPIO when that net dangles, or when it sits on a supply rail and no GPIO is wired yet (a button
    from a rail to ground is moved always: pressing it would short the rail). The new net takes a name the
    firmware already drives but nothing carries, so the picker's own program works. Returns notes."""
    mp = firmware.mcu_part(design, kept)
    if not mp:
        return []
    mcu, piece = mp
    by_key = _cat() | {p["key"]: p for p in kept}
    mpins = mcu.setdefault("pins", {})
    names = {str(k): str(v) for k, v in (piece.get("pins") or {}).items()}
    reserved = {str(n) for n in (piece.get("spice") or {}).get("reserved") or []}
    no_gpio = not firmware._gpio_by_net(mcu, piece)
    driven = list(
        dict.fromkeys(
            str(n)
            for c in (design.get("firmware") or {}).get("commands") or []
            if isinstance(c, dict)
            for n in c.get("high") or []
        )
    )
    parts = design.get("parts", [])
    notes: list[str] = []

    def fam(p: dict[str, Any]) -> str:
        return str((by_key.get(p.get("piece")) or {}).get("family") or "").lower()

    def rail(net: str) -> bool:
        return bool(_RAIL_RX.fullmatch(net)) and _norm_net(net) not in _GND_ALIASES

    def ground(net: str) -> bool:
        return _norm_net(net) in _GND_ALIASES

    count = _net_count(design)
    gnd = max((n for n in count if ground(n)), key=lambda n: count[n], default="GND")
    want: list[tuple[dict[str, Any], str, bool]] = []  # (part, pin, move even off a rail when GPIO is wired)
    res = [p for p in parts if fam(p) == "resistor" and len(p.get("pins") or {}) == 2]
    for p in parts:
        f, pins = fam(p), {str(k): str(v) for k, v in (p.get("pins") or {}).items()}
        pc = by_key.get(p.get("piece")) or {}
        if f == "led" and (a := _pin_named(pc, "A")) in pins:
            an = pins[a]
            r = next(
                (r for r in res if an in map(str, r["pins"].values()) and len(set(map(str, r["pins"].values()))) == 2),
                None,
            )
            want += [(r, k, False) for k, v in r["pins"].items() if str(v) != an] if r else [(p, a, False)]
        elif f == "buzzer" and (plus := _pin_named(pc, "+")) in pins:
            want.append((p, plus, False))
        elif f in ("push button", "switch") and len(pins) == 2:
            if len(set(pins.values())) == 1 and not ground(next(iter(pins.values()))):
                # both legs on one net (live-1792267239993, "SW1 BTN/BTN" beside its pull-up): the second leg
                # goes to ground, so a press pulls the input low
                k2 = sorted(p["pins"], key=str)[-1]
                p["pins"][k2] = gnd
                pins[str(k2)] = gnd
                notes.append(f"{p.get('ref')} pin {k2} to {gnd}, both legs were on one net")
            grounded = any(ground(v) for v in pins.values())
            want += [(p, k, True) for k, v in pins.items() if not ground(v) and grounded]
    for p, k, always in want:
        count = _net_count(design)
        net = str(p["pins"][k])
        if net in map(str, mpins.values()) and not rail(net):
            continue  # already reaches the microcontroller
        # a button's live side must reach the MCU even when a pull-up already shares its net
        if not (count.get(net) == 1 or (always and not rail(net)) or (rail(net) and (no_gpio or always))):
            continue
        free = [n for n in names if n not in mpins and n not in reserved and names[n].upper().startswith("GPIO")]
        free.sort(key=lambda n: "ADC" in names[n].upper())  # keep ADC pins for analog parts
        if not free:
            break
        spare = [n for n in driven if n not in count]
        if not rail(net) and (count.get(net) != 1 or net in driven or not spare):
            new = net  # a name of its own, or a net other parts share (a pull-up): keep it
        elif spare:
            new = spare[0]  # a net the firmware drives that nothing carries yet
        else:
            new = next((names[n] for n in free if names[n] not in count), f"{p.get('ref')}_IO")
        m = re.fullmatch(r"(?:GPIO|GP)_?(\d+)", new.upper())
        pin = next(
            (n for n in free if m and re.fullmatch(f"GPIO{int(m.group(1))}(_.*)?", names[n].upper())),
            free[0],
        )
        p["pins"][k] = new
        mpins[pin] = new
        notes.append(f"{p.get('ref')} to {mcu.get('ref')} {names[pin]}" + (f" instead of {net}" if rail(net) else ""))
    return notes


def _drop_dangling_passives(design: dict[str, Any], kept: list[dict[str, Any]]) -> list[str]:
    """A resistor or capacitor with one end on a supply rail or ground and the other on nothing connects nothing:
    a regulator's ADJ divider left behind when the regulator was dropped (live-1792274265002, "ADJ only on R2
    pin 2"). Runs after every wiring repair, so a dangling end that should reach the MCU already does."""
    by_key = _cat() | {p["key"]: p for p in kept}
    count = _net_count(design)
    out, keep = [], []
    for p in design.get("parts", []):
        nets = [str(n) for n in (p.get("pins") or {}).values()]
        fam = str((by_key.get(p.get("piece")) or {}).get("family") or "").lower()
        if (
            fam in ("resistor", "capacitor")
            and len(nets) == 2
            and sorted(count.get(n, 0) == 1 for n in nets) == [False, True]
            and any(_RAIL_RX.fullmatch(n) for n in nets if count.get(n, 0) > 1)
        ):
            out.append(str(p.get("ref")))
            continue
        keep.append(p)
    design["parts"] = keep
    return out


def _prune_commands(design: dict[str, Any]) -> None:
    """Firmware commands that drive a net no pin carries (GPIO1 when the board's one LED sits on GPIO0) can never
    pass the firmware test; they drive nothing, so those names are dropped from the command."""
    on = set(_net_count(design))
    for c in (design.get("firmware") or {}).get("commands") or []:
        if isinstance(c, dict) and isinstance(c.get("high"), list):
            c["high"] = [n for n in c["high"] if str(n) in on]


def _wire_mcu_supply(design: dict[str, Any], kept: list[dict[str, Any]]) -> None:
    """A 5 V or 3.3 V net that lands on one pin of a board whose microcontroller module has that rail free
    gets the module's rail (sweep 10-17, live-1792260040287: a Pico relay driver left "VCC only on J1 pin 1"
    three times; the coil's + belongs on the Pico's USB 5 V). 5 V goes on VBUS, or on VSYS when the lonely pin
    is itself the board's power input; 3.3 V goes on the 3V3 output. A taken rail pin is left alone."""
    mp = firmware.mcu_part(design, kept)
    if not mp:
        return
    part, piece = mp
    names = {str(v).upper(): str(k) for k, v in (piece.get("pins") or {}).items()}
    by_key = {p["key"]: p for p in kept}
    pins = part.setdefault("pins", {})
    for net in _lonely_nets(design):
        up = net.upper()
        owner = next(
            (
                q
                for q in design.get("parts", [])
                if net in (q.get("pins") or {}).values()
            ),
            None,
        )
        if owner is None or owner is part:
            continue
        if up in _SUPPLY_5V:
            fam = (by_key.get(owner.get("piece")) or {}).get("family", "")
            power_in = fam in _POWER_IN and not owner.get("load_ohms")
            pin = names.get("VSYS" if power_in else "VBUS")
        elif up in _SUPPLY_3V3:
            pin = names.get("3V3")
        elif re.search(r"BAT|VIN", up) and (by_key.get(owner.get("piece")) or {}).get(
            "family", ""
        ) in _POWER_IN:
            # a cell's + on its own net (VBAT, BAT+): the Pico runs from it through VSYS (10-17, live-1792273371898
            # failed "VBAT only on BT1 pin 1")
            pin = names.get("VSYS")
        else:
            continue
        if pin and pin not in pins:
            pins[pin] = net


PICO_KEY = "kicad:MCU_Module:RaspberryPi_Pico@RaspberryPi_Pico_Common_THT"
PICO_W_KEY = "kicad:MCU_Module:RaspberryPi_Pico_W@RaspberryPi_Pico_Common_THT"
_PICO_ADC = ("31", "32", "34")  # GPIO26/27/28
_PICO_I2C = {
    "SDA": "6",
    "SCL": "7",
}  # I2C1 on GPIO4/GPIO5 is I2C0 SDA/SCL in MicroPython's default map
_PICO_GPIO = (
    "1",
    "2",
    "4",
    "5",
    "9",
    "10",
    "11",
    "12",
    "14",
    "15",
    "16",
    "17",
    "19",
    "20",
    "21",
    "22",
    "24",
    "25",
    "26",
    "27",
    "29",
)
_SUPPLY_5V = ("VCC", "5V", "+5V", "VBUS", "V5")
_SUPPLY_3V3 = ("3V3", "+3V3", "P3V3", "VDD", "3.3V")
_ANALOG_FAMILIES = (
    "light sensor",
    "thermistor",
    "trimmer potentiometer",
    "mic module",
    "resistor",
)
_POWER_IN = ("2-pin connector", "battery holder", "USB-C power input", "DC barrel jack")


_WIRELESS = r"\b(bluetooth|ble|wi-?fi|wireless|phone|internet|online|esp32|air ?tags?|tracker|beacon|finder|iot|smart ?watch)\b"


def _radio_fix(
    design: dict[str, Any], kept: list[dict[str, Any]], ask: str
) -> list[str]:
    """A wireless ask needs a radio, not a loose antenna. The Pico W's radio and antenna are on the module, so a
    plain Pico becomes a Pico W (same footprint, same pins) and an antenna whose every pin ends on one pin is
    dropped (live-1792271675178, "device tracker similar to an airtag": Pico + ANT1 failed "ANT touches only
    one pin" three times with the same design). Returns what changed, for the say line."""
    did: list[str] = []
    wireless = bool(re.search(_WIRELESS, ask.lower()))
    lonely = set(_lonely_nets(design))
    keep = []
    for part in design.get("parts", []):
        key = str(part.get("piece", ""))
        nets = [str(n) for n in (part.get("pins") or {}).values()]
        if re.search(r"antenna|:ANT", key, re.I) and all(n in lonely for n in nets):
            did.append(
                f"dropped {part.get('ref')}, the antenna on the module does that job"
            )
            continue
        keep.append(part)
    design["parts"] = keep
    if wireless:
        cat = _cat()
        for part in keep:
            if part.get("piece") == PICO_KEY and PICO_W_KEY in cat:
                part["piece"] = PICO_W_KEY
                if cat[PICO_W_KEY] not in kept:
                    kept.append(cat[PICO_W_KEY])
                did.append(
                    f"{part.get('ref')} is a Pico W so the board has Bluetooth and Wi-Fi"
                )
    return did


def _auto_mcu(
    design: dict[str, Any], kept: list[dict[str, Any]], ask: str
) -> dict[str, Any] | None:
    """A design whose sensor, knob or display nets end on one pin, with no microcontroller to read or drive them,
    gets a Raspberry Pi Pico (Pico W when the ask wants Bluetooth or Wi-Fi): analog nets on ADC pins, SDA/SCL on
    I2C0, the rest on free GPIO, power from the Pico's own USB rails when nothing else powers the board."""
    if firmware.mcu_part(design, kept):
        return None
    lonely = [
        n
        for n in _lonely_nets(design)
        if n.upper() not in ("GND",) + _SUPPLY_5V + _SUPPLY_3V3
    ]
    if not lonely:
        return None
    cat = _cat()
    key = PICO_W_KEY if re.search(_WIRELESS, ask.lower()) else PICO_KEY
    if key not in cat:
        return None
    by_key = {p["key"]: p for p in kept} | cat
    fam_of_net: dict[str, set[str]] = {}
    for part in design.get("parts", []):
        fam = (by_key.get(part.get("piece")) or {}).get("family", "")
        for net in (part.get("pins") or {}).values():
            fam_of_net.setdefault(str(net), set()).add(fam)
    pins: dict[str, str] = {"3": "GND", "38": "GND"}
    adc, gpio = list(_PICO_ADC), list(_PICO_GPIO)
    for n in lonely:
        up = n.upper()
        if up in _PICO_I2C and _PICO_I2C[up] in gpio:
            pins[_PICO_I2C[up]] = n
            gpio.remove(_PICO_I2C[up])
        elif adc and fam_of_net.get(n, set()) & set(_ANALOG_FAMILIES):
            pins[adc.pop(0)] = n
        elif gpio:
            pins[gpio.pop(0)] = n
    nets = set(fam_of_net)
    powered = any(
        fam_of_net.get(n, set()) & set(_POWER_IN)
        for n in nets
        if n.upper() in _SUPPLY_5V
    )
    if v5 := next((n for n in nets if n.upper() in _SUPPLY_5V), None):
        # Not powered: VBUS hands the Pico's USB 5 V to the board. Powered from a connector or jack: the Pico runs
        # from that rail through VSYS (QA 10-17, live-1792255105003: VCC was left on the power connector alone and
        # the picker, who never sees the added Pico, failed "VCC only on U1 pin 1" twice). The 3V3 pin is the
        # regulator's output, so a 5 V rail never goes there.
        pins["39" if powered else "40"] = v5
    if v33 := next((n for n in nets if n.upper() in _SUPPLY_3V3), None):
        pins["36"] = v33  # 3V3 out
    used = {str(p.get("ref")) for p in design.get("parts", [])}
    n = 1
    while f"U{n}" in used:
        n += 1
    ref = f"U{n}"
    design.setdefault("parts", []).append(
        {
            "ref": ref,
            "piece": key,
            "pins": pins,
            "group": "mcu",
            "why": "Reads the sensors and drives the outputs: Etch added it because nothing on the board could.",
        }
    )
    if cat[key] not in kept:
        kept.append(cat[key])
    wired = ", ".join(sorted(v for k, v in pins.items() if v in lonely))
    name = "Pico W" if key == PICO_W_KEY else "Pico"
    return {
        "piece": key,
        "ref": ref,
        "say": f"This needs a microcontroller, so I added a Raspberry Pi {name} ({ref}) and wired {wired} to it.",
    }


LED_KEY = "kicad:Device:LED@LED_D5.0mm"
_LED_VALUE = re.compile(r"\b(led|rgb|red|green|blue|yellow|white|amber|orange)\b", re.I)


def _led_stand_ins(
    design: dict[str, Any], kept: list[dict[str, Any]], keys: set[str]
) -> list[str]:
    """Resistor or capacitor pieces used as LEDs: a D/LED ref, or a colour / "LED" / "RGB" value (QA 10-17,
    live-1792255105003: an RGB LED drawn as three capacitors valued RED, GREEN, BLUE, which ngspice read as
    "cc1 ... red"). The LED piece joins the kept list so the repair can use it."""
    idx = load_index()
    out = []
    for p in design.get("parts", []):
        fam = _ref_letter_family(str(idx.get(p.get("piece"), {}).get("category") or ""))
        if fam not in ("R", "C"):
            continue
        ref = str(p.get("ref", ""))
        if re.match(r"(D|LED)\d", ref) or _LED_VALUE.search(str(p.get("value") or "")):
            out.append(ref)
    if out and LED_KEY not in keys:
        led = next(
            (q for q in kicad_catalog.load()["pieces"] if q["key"] == LED_KEY), None
        )
        if led:
            kept.append(led)
            keys.add(LED_KEY)
    return out


def _lonely_nets(design: dict[str, Any]) -> list[str]:
    count: dict[str, int] = {}
    for p in design.get("parts", []):
        for net in (p.get("pins") or {}).values():
            count[str(net)] = count.get(str(net), 0) + 1
    return sorted(n for n, c in count.items() if c == 1)


_GND_ALIASES = ("GND", "AGND", "DGND", "VSS", "0V", "GROUND", "GND_PWR")
_COLOURS = {
    "R": "RED",
    "G": "GREEN",
    "B": "BLUE",
    "RED": "RED",
    "GREEN": "GREEN",
    "BLUE": "BLUE",
}
_GENERIC_TOKENS = {
    "GPIO",
    "GP",
    "PICO",
    "MCU",
    "PIN",
    "NET",
    "SIG",
    "IN",
    "OUT",
    "LED",
    "CTRL",
    "DRV",
}
_MCU_HINT = re.compile(
    r"(^|_)(GPIO\w*|GP\d+|PICO\w*|MCU\w*|ADC\w*|ALERT|INT|IRQ|PWM\w*|SIG|SENSE|SENSOR\w*|DATA|CTRL|EN|BTN\w*|"
    r"BUTTON\w*|SDA|SCL|TXD?\d*|RXD?\d*|UART\w*|MIC\w*|AUDIO\w*|FAN\w*|TEMP\w*)(_|$)"
)
# a module's TX feeds the MCU's RX: UART0 (GP1 RX pin 2, GP0 TX pin 1), then UART1 on GP9/GP8
_PICO_UART = {"TX": ("2", "12"), "RX": ("1", "11")}


def _norm_net(n: str) -> str:
    return re.sub(r"[\s\-_.]+", "_", n.strip().upper())


def _merge_lonely(design: dict[str, Any], kept: list[dict[str, Any]]) -> list[str]:
    """Repair dangling nets the picker split by name instead of failing the iteration on them (picker QA 10-17:
    "Q2_B" on the transistor and "Q2_B_DRIVE" on its resistor, "R1_R" on a resistor and "GPIO_RED" on the MCU,
    "Vcc" on one pin and "VCC" everywhere else, "+5V" alone beside a "VCC" rail). Rules, in order:
      1. same name up to case and separators: join the other spelling;
      2. one name extends the other at a separator (Q2_B / Q2_B_DRIVE): join the shorter;
      3. colour pairs (R1_R + GPIO_RED): two dangling nets that name the same colour and nothing else, on
         different parts, join under the MCU-side name;
      4. supply and ground aliases: a dangling 5 V, 3.3 V or ground alias joins the rail of that kind in use.
    Never joins two pins of the same part (that would short it). Returns "OLD -> NEW" notes. Runs before
    _auto_mcu; _wire_hinted then puts MCU-named leftovers on the module."""
    parts = design.get("parts", [])
    notes: list[str] = []

    def nets_now() -> dict[str, list[dict[str, Any]]]:
        on: dict[str, list[dict[str, Any]]] = {}
        for p in parts:
            for v in (p.get("pins") or {}).values():
                on.setdefault(str(v), []).append(p)
        return on

    def rename(old: str, new: str) -> None:
        for p in parts:
            pins = p.get("pins") or {}
            for k, v in list(pins.items()):
                if str(v) == old:
                    pins[k] = new
        for k in ("scenarios", "benchmarks", "firmware"):
            if k in design:
                design[k] = json.loads(
                    re.sub(
                        rf'"{re.escape(old)}"', json.dumps(new), json.dumps(design[k])
                    )
                )
        notes.append(f"{old} -> {new}")

    def safe(old: str, new: str, on: dict[str, list[dict[str, Any]]]) -> bool:
        return old != new and not any(p in on.get(new, []) for p in on.get(old, []))

    supply = {"5V": _SUPPLY_5V, "3V3": _SUPPLY_3V3, "GND": _GND_ALIASES}
    mp = firmware.mcu_part(design, kept)
    mcu = mp[0] if mp else None
    for _ in range(
        3
    ):  # a join can leave another dangling name matching; settle in a few passes
        changed = False
        on = nets_now()
        lonely = [n for n, ps in on.items() if len(ps) == 1]
        for n in lonely:
            on = nets_now()
            if len(on.get(n, [])) != 1:
                continue
            up = _norm_net(n)
            kind = next(
                (k for k, al in supply.items() if up in al or up.lstrip("+") in al),
                None,
            )
            others = [m for m in on if m != n]
            target = None
            if kind:  # rule 4 (also covers rule 1 for rails)
                rails = [
                    m
                    for m in others
                    if _norm_net(m) in supply[kind]
                    or _norm_net(m).lstrip("+") in supply[kind]
                ]

                def rank(m: str) -> int:
                    u = _norm_net(m)
                    al = supply[kind]
                    return (
                        al.index(u)
                        if u in al
                        else al.index(u.lstrip("+"))
                        if u.lstrip("+") in al
                        else 99
                    )

                rails.sort(key=lambda m: (-len(on[m]), rank(m)))
                target = rails[0] if rails else None
                if target and len(on[target]) == 1 and rank(n) < rank(target):
                    n, target = (
                        target,
                        n,
                    )  # two dangling rail names: keep the plain one (GND over VSS)
            if not target:  # rule 1
                target = next((m for m in others if _norm_net(m) == up), None)
            if not target:  # rule 2: Q2_B_DRIVE -> Q2_B, or Q2_B -> Q2_B_BASE when only the long one exists
                ext = [
                    m for m in others if up.startswith(_norm_net(m) + "_")
                ]  # the longer name joins
                ext.sort(key=lambda m: (len(on[m]) < 2, len(m)))
                target = ext[0] if ext else None
            if not target:  # rule 3
                toks = set(up.split("_")) - _GENERIC_TOKENS
                col = {_COLOURS[t] for t in toks if t in _COLOURS}
                rest = {
                    t
                    for t in toks
                    if t not in _COLOURS and not re.fullmatch(r"[RDQUC]\d+", t)
                }
                if len(col) == 1 and not rest:
                    mates = [
                        m
                        for m in others
                        if len(on[m]) == 1
                        and {
                            _COLOURS[t]
                            for t in set(_norm_net(m).split("_"))
                            if t in _COLOURS
                        }
                        == col
                    ]
                    if len(mates) == 1:
                        target = mates[0]
                        if mcu is not None and on[n][0] is mcu:
                            n, target = target, n  # keep the MCU-side name
            if target and safe(n, target, on):
                rename(n, target)
                changed = True
        if not changed:
            break
    return notes


def _wire_hinted(design: dict[str, Any], kept: list[dict[str, Any]]) -> None:
    """A dangling signal net whose name says it belongs to the microcontroller (MIC_OUT, FAN_PWM, SENSOR_TEMP,
    GPIO_ADC, I2C_SDA, BTN) goes on a free module pin: SDA/SCL on the Pico's I2C0 pins, analog-sounding nets on a
    free ADC pin, the rest on the next free GPIO."""
    part, piece = firmware.mcu_part(design, kept)
    names = {str(k): str(v) for k, v in (piece.get("pins") or {}).items()}
    pins = part.setdefault("pins", {})
    on_mcu = set(map(str, pins.values()))
    for net in _lonely_nets(design):
        up = _norm_net(net)
        if (
            net in on_mcu
            or up in _SUPPLY_5V + _SUPPLY_3V3 + _GND_ALIASES
            or not _MCU_HINT.search(up)
        ):
            continue
        free = [
            n for n in names if n not in pins and names[n].upper().startswith("GPIO")
        ]
        want: list[str] = []
        if re.search(r"(^|_)SDA($|_)", up) and piece.get("key") in (
            PICO_KEY,
            PICO_W_KEY,
        ):
            want = [_PICO_I2C["SDA"]]
        elif re.search(r"(^|_)SCL($|_)", up) and piece.get("key") in (
            PICO_KEY,
            PICO_W_KEY,
        ):
            want = [_PICO_I2C["SCL"]]
        elif (uart := re.search(r"(?:^|_)(TX|RX)D?\d*(?:$|_)", up)) and piece.get(
            "key"
        ) in (PICO_KEY, PICO_W_KEY):
            want = list(_PICO_UART[uart.group(1)])
        elif re.search(r"ADC|ANALOG|MIC|AUDIO|TEMP|SENSE|SENSOR|POT|LDR", up):
            want = [
                n for n in _PICO_ADC if piece.get("key") in (PICO_KEY, PICO_W_KEY)
            ] + [n for n in names if "ADC" in names[n].upper()]
        pick = next((n for n in want + free if n not in pins), None)
        if pick:
            pins[pick] = net
            on_mcu.add(net)


def _lonely_hint(design: dict[str, Any], net: str) -> str:
    """The nets a dangling name most likely meant, for the repair prompt."""
    import difflib  # noqa: PLC0415

    names = sorted(
        {
            str(v)
            for p in design.get("parts", [])
            for v in (p.get("pins") or {}).values()
        }
        - {net}
    )
    close = difflib.get_close_matches(net, names, n=2, cutoff=0.5)
    return f"; did you mean {' or '.join(close)}?" if close else ""


def rep_board(out: Path) -> tuple[float, float]:
    b = json.loads((out / "layout.json").read_text())["board"]
    return b["w"], b["h"]


def _analog(
    stream: Stream,
    design: dict[str, Any],
    kept: list[dict[str, Any]],
    keys: set[str],
    ask: str,
) -> list[str]:
    """Deterministic transistor-switch check (agent/analog.py) before every build: sizes base resistors, swaps a
    driver that cannot carry the load, and makes load benchmarks agree with the load model. Returns what it could
    not fix, for the repair prompt."""
    cat = _cat()
    res = analog.solve(design, kept, cat, ask)
    for pc in res.added:
        if pc["key"] not in keys:
            kept.append(pc)
            keys.add(pc["key"])
    notes = res.notes + analog.bands(design, kept, cat)
    if notes:
        # its own event, not "lead:say": the say line is read aloud, and these are engineering notes
        stream.emit("assembly", "analog", notes=notes, findings=res.findings)
    return res.findings


STIMULI = ("ldr", "voltage", "switch", "set", "gpio", "sound", "inputs")


def _scenario_note(design: dict[str, Any]) -> str:
    """Scenarios that set no stimulus simulate identically, so an on/off pair across them can never pass."""
    sc = [s for s in design.get("scenarios") or [] if s.get("name")]
    if len(sc) < 2 or any(s.get(k) is not None for s in sc for k in STIMULI):
        return ""
    names = ", ".join(s["name"] for s in sc)
    return (
        f"\n- scenarios {names} change nothing in the simulation (no ldr, voltage or switch), so every scenario "
        "gives the same numbers. Use a sensor part that one of those stimuli drives; if the ask needs a sensor "
        "that is not in the KEPT list, list it in missing instead of standing it in with plain resistors."
    )


def _feedback(v: dict[str, Any], metrics: list[str]) -> str:
    lines = ["The checks FAILED on that design:"]
    if v.get("route_timed_out"):
        # first, so it is inside the 400 characters the decide step shows
        lines.append(
            f"- {eng.routed_value(v)} (the machine was busy or the board is crowded; the copper it laid is kept)"
        )
    lines += [f"- benchmark failed: {f}" for f in v["failed"]]
    if v["unrouted"]:
        lines.append(
            f"- unrouted nets: {v['unrouted']} (fewer parts or a different arrangement)"
        )
    if v["drc_violations"]:
        lines.append(f"- DRC violations {v['drc_violations']}: {v['drc_types']}")
    if v["erc_errors"]:
        lines.append(
            f"- ERC errors {v['erc_errors']} (a pin wired wrong, or a power pin with no source)"
        )
    if v.get("sim_error"):
        lines.append(f"- simulation: {v['sim_error'][:500]}")
    if metrics:
        lines.append(
            "Simulation metrics that exist for this board: " + ", ".join(metrics[:60])
        )
    lines.append(
        "Fix the circuit or the values (or a benchmark that names a metric that does not exist) and submit the whole design again."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- stage 4: export


def export(stream: Stream, out: Path) -> dict[str, Any]:
    t0 = time.time()
    pcb = next(out.glob("*.kicad_pcb"))
    name = pcb.stem
    sch = out / f"{name}.kicad_sch"
    bom = out / f"{name}-bom.csv"
    # no DNP column: Etch never marks a part do-not-populate, so it was a blank cell on every row (QA 10-17)
    cli(
        "sch",
        "export",
        "bom",
        "--fields",
        "Reference,Value,Footprint,QUANTITY",
        "--labels",
        "Refs,Value,Footprint,Qty",
        "--exclude-dnp",
        "--output",
        str(bom),
        str(sch),
    )
    files = [
        out / f"{name}.kicad_pro",
        sch,
        pcb,
        out / "sym-lib-table",
        out / "fp-lib-table",
        bom,
    ]
    files += sorted((out / "gerbers").glob("*"))
    files += [
        f
        for f in (out / "firmware" / "main.py", out / "firmware" / "test.json")
        if f.exists()
    ]
    files += sorted(
        f for f in (out / "firmware").glob("*.py") if f.name != "main.py"
    )  # host scripts (fan_host.py)
    zp = out / f"{name}-fab.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f.exists():
                arc = f"{name}/" + (
                    f"{f.parent.name}/{f.name}"
                    if f.parent.name in ("gerbers", "firmware")
                    else f.name
                )
                z.write(f, arc)
    with bom.open() as fh:
        bom_rows = max(0, sum(1 for _ in csv.reader(fh)) - 1)
    gerbers = [
        f.name
        for f in sorted((out / "gerbers").glob("*"))
        if f.suffix in (".gtl", ".gbl", ".gto", ".gbo", ".gts", ".gbs", ".gm1", ".drl")
    ]
    res = {
        "zip": eng.url(zp),
        "zip_bytes": zp.stat().st_size,
        "project": [f.name for f in files[:3]],
        "gerbers": gerbers,
        "bom_lines": bom_rows,
        "seconds": round(time.time() - t0, 2),
    }
    stream.emit("export", "done", **res)
    return res


# ---------------------------------------------------------------- stage 5: render

RENDER_MASK = "#0A6A3880"  # a translucent mask so the copper traces read through it, render copies only (board file unchanged)


SOURCED_SPICE = {
    "switch": ("S", "switch", "switch (closed / open scenarios)"),
    "two-pin passive": ("R", "passive", "two-pin passive"),
}


_GENERIC = (  # plain words -> the stock through-hole part with a SPICE model
    (r"\bnpn\b", "2N3904"),
    (r"\bpnp\b", "2N3906"),
    (r"flyback|rectifier|\b1n400\d", "1N4001"),
    (r"n-?channel|\bmosfet\b", "BS170"),
    # the KiCad library's Pico modules (sweep 10-17: "Raspberry Pi Pico" went to web sourcing instead)
    (r"\bpico[ _-]?w\b", "RaspberryPi_Pico_W"),
    (r"raspberry[ _-]?pi[ _-]?pico|\bpico\b|\brp2040\b", "RaspberryPi_Pico"),
)


def _catalog_hit(text: str) -> dict[str, Any] | None:
    cat = [c for c in kicad_catalog.load()["pieces"] if c.get("spice")]
    nums = [
        t.upper()
        for t in re.findall(r"\b[A-Za-z]{0,4}\d[\dA-Za-z-]{2,}\b", text)
        if re.search(r"\d{3,}", t)
    ]
    for n in nums:
        for c in cat:
            if f":{n}@" in c["key"].upper() or c["key"].upper().split(":")[
                -1
            ].startswith(n):
                return c
    low = text.lower()
    for pat, pn in _GENERIC:
        if re.search(pat, low):
            if hit := next((c for c in cat if f":{pn}@" in c["key"]), None):
                return hit
    # then the whole library (every KiCad library, extra libraries, fetched cache parts) before any web fetch
    return _lib_piece(text)


def _sym_foot(pc: str) -> tuple[str, str]:
    """ "kicad:Device:LED@LED_D5.0mm_GREEN" -> ("LED", "LED_D5.0mm_GREEN"); "SW_Push" -> ("SW_Push", "")."""
    head, _, foot = pc.partition("@")
    return head.split(":")[-1], foot


# a generic symbol word the picker made up -> the symbol it means, by family (picker QA 10-17: "Device:D@D_SmallSignal_
# SMD_Diodes,D:DIOD1N4148" eight times, "LED@LED_THT5MM", "SW_Push@SW_PushButton_6mm_SMD", "Buzzer_Passive_Square_12mm")
_TWIN_FAMILY = (
    (r"^(LED|LED_SMALL|LED_DIFFUSED|LED_THT\w*|LED_\d\w*)$", ("LED",)),
    (r"^(D|D_SMALL|DIODE|D_SMALLSIGNAL|1N4148\w*)$", ("1N4148", "D", "1N4001")),
    (r"^(SW_PUSH\w*|BUTTON\w*|PUSHBUTTON\w*|SW_TACTILE\w*)$", ("SW_Push",)),
    (r"^BUZZER\w*$", ("Buzzer",)),
    (r"^(R|R_SMALL|R_US|RESISTOR)$", ("R",)),
    (r"^(C|C_SMALL|CAPACITOR)$", ("C",)),
    (
        r"^(RV|RV_\S*|R_POT\S*|POT\S*|POTENTIOMETER\S*|R_POTENTIOMETER\S*|TRIMPOT\S*)$",
        ("R_Potentiometer_Trim",),
    ),
)
_HEADER = re.compile(r"(?:CONN|PINHEADER|PINSOCKET)_0?(\d+)X0?(\d+)", re.I)


def _twin_rank(c: dict[str, Any], foot: str) -> tuple:
    """Closest footprint first; then the stock LED, vertical and shorter (plainer) footprints."""
    f = _sym_foot(c["key"])[1]
    common = len(os.path.commonprefix([f.lower(), foot.lower()]))
    return (-common, c["key"] != LED_KEY, "Horizontal" in f, len(f), c["key"])


def _twin_piece(
    pc: str, pins: dict | None, kept: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The real piece a made-up key means when it names the same symbol, family or header shape as one we have: a
    colour or a made-up footprint after a real symbol ("LED@LED_D5.0mm_GREEN"), a footprint-only name
    ("R_Axial_..._VertICAL"), a generic word ("D", "SW_Push", "Buzzer"), a header by pin count ("Conn_02x03_Oval" ->
    a 1x06 header), or a USB-C receptacle by its pin names. KEPT pieces first, then the KiCad catalog (through-hole,
    with a SPICE model). The twin must carry every pin the part uses (by number or by name), so the wiring stays."""
    sym, foot = _sym_foot(pc)
    foot = re.sub(
        r"_(RED|GREEN|BLUE|YELLOW|WHITE|AMBER|ORANGE|WARM\w*)$", "", foot, flags=re.I
    )  # a colour is a value
    used = {str(k) for k in (pins or {})}

    def fits(c: dict[str, Any]) -> bool:
        have = {str(k) for k in c.get("pins") or {}} | {
            str(v).lower() for v in (c.get("pins") or {}).values()
        }
        return all(u in have or u.lower() in have for u in used)

    cat = [c for c in kicad_catalog.load()["pieces"] if c.get("spice")]
    pools = (kept, cat)
    up = sym.upper()
    for pool in pools:  # same symbol, closest footprint
        same = [c for c in pool if _sym_foot(c["key"])[0].upper() == up and fits(c)]
        if same:
            same.sort(key=lambda c: _twin_rank(c, foot))
            return same[0]
    for pool in pools:  # the name is a footprint we have
        for c in pool:
            if _sym_foot(c["key"])[1].lower() in (sym.lower(), foot.lower()) and fits(
                c
            ):
                return c
    for pat, syms in _TWIN_FAMILY:
        if re.match(pat, up):
            for want in syms:
                for pool in pools:
                    hits = [
                        c for c in pool if _sym_foot(c["key"])[0] == want and fits(c)
                    ]
                    if hits:
                        return min(hits, key=lambda c: _twin_rank(c, foot))
    if m := _HEADER.search(f"{sym}@{foot}"):
        n = int(m.group(1)) * int(m.group(2))
        heads = []
        for pool in pools:
            for c in pool:
                cm = re.match(r"Conn_01x0?(\d+)$", _sym_foot(c["key"])[0])
                if cm and int(cm.group(1)) >= n and fits(c):
                    heads.append(
                        (
                            int(cm.group(1)),
                            pool is cat,
                            "PinHeader" not in c["key"],
                            "Vertical" not in c["key"],
                            c["key"],
                            c,
                        )
                    )
        if heads:
            return min(heads, key=lambda h: h[:5])[5]
    if "USB_C" in up:
        return next(
            (
                c
                for pool in pools
                for c in pool
                if "USB_C" in _sym_foot(c["key"])[0] and fits(c)
            ),
            None,
        )
    # last: the whole library's piece for this name (every KiCad library, extra libraries, fetched cache parts)
    lp = _lib_piece(f"{sym} {foot}".replace("_", " ").strip())
    return lp if lp is not None and fits(lp) else None


def _resolve_unknown(
    stream: Stream,
    unknown: list[dict[str, Any]],
    kept: list[dict[str, Any]],
    keys: set[str],
    tried: set[str],
) -> None:
    """Pieces the picker named that are not in the KEPT list ("2N3904@TO-92", "PIR_Sensor@HC-SR501"): the same
    real part from the KiCad catalog when it is there, else one sourcing attempt per name (LCSC / EasyEDA, GitHub,
    You.com; agent/parts_live.py). Nothing is guessed: a name that cannot be sourced stays unknown, the sourcing
    step says so, and the KEPT-list error goes back to the picker (sweep 10-17, PIR ask: three tries on made-up keys)."""
    todo: dict[str, list[dict[str, Any]]] = {}
    for part in unknown:
        pc = str(part.get("piece", ""))
        name = pc.split(":")[-1].split("@")[0]
        foot = pc.split("@")[-1] if "@" in pc else ""
        if twin := _twin_piece(pc, part.get("pins"), kept):
            part["piece"] = twin["key"]
            if twin["key"] not in keys:
                kept.append(twin)
                keys.add(twin["key"])
            stream.emit(
                "assembly", "repair", kind="pieces", joined=[f"{pc} -> {twin['key']}"]
            )
            continue
        hit = _catalog_hit(f"{name} {foot}".replace("_", " "))
        if hit:
            part["piece"] = hit["key"]
            if hit["key"] not in keys:
                kept.append(hit)
                keys.add(hit["key"])
                stream.emit(
                    "sourcing",
                    "done",
                    what=name,
                    ok=True,
                    key=hit["key"],
                    provenance={"route": "KiCad library (missed by the classifier)"},
                    seconds=0.0,
                )
        elif name and name not in tried:
            todo.setdefault(name, []).append(part)
    for name in list(todo)[:2]:
        tried.add(name)
        what = name.replace("_", " ")
        got = source_missing(stream, [{"what": what, "search": what}])
        if not got:
            continue
        for part in todo[name]:
            part["piece"] = got[0]["key"]
        if got[0]["key"] not in keys:
            kept.append(got[0])
            keys.add(got[0]["key"])


def source_missing(
    stream: Stream, missing: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """A part KiCad's libraries do not have: fetch the real vendor part (LCSC / EasyEDA via easyeda2kicad, or a
    published KiCad footprint on GitHub; agent/parts_live.py) into the repo-local library engine/parts/cache with its
    provenance, give it the plain switch model when it is a switch, and hand it back as a KEPT piece."""
    from . import parts_live

    got = []
    for m in missing[:2]:
        t0 = time.time()
        stream.emit("sourcing", "start", what=m.get("what"), search=m.get("search"))
        # the through-hole catalog first: "2N2222 or 2N3904", "NPN transistor", "flyback diode (1N4001)" are
        # already there with a SPICE model; the classifier just did not keep them for this ask
        if hit := _catalog_hit(f"{m.get('search') or ''} {m.get('what') or ''}"):
            got.append(hit)
            stream.emit(
                "sourcing",
                "done",
                what=m.get("what"),
                ok=True,
                key=hit["key"],
                provenance={"route": "KiCad library (missed by the classifier)"},
                seconds=round(time.time() - t0, 2),
            )
            continue
        res = parts_live.find_part(str(m.get("search") or m.get("what")), stream)
        if res.get("status") not in ("found", "ok") and m.get("what"):
            res = parts_live.find_part(
                f"{m.get('what')} through-hole", stream
            )  # second try: the plain name
        if res.get("status") not in ("found", "ok") and m.get("what"):
            res = parts_live.find_part(
                re.sub(r"\(.*?\)", "", str(m["what"])).strip(), stream
            )  # third: no notes
        part = res.get("part") or {}
        pins = part.get("pins") or []
        if res.get("status") in ("found", "ok") and part.get("source") == "kicad-lib":
            cat = _cat()
            if part["key"] in cat and cat[part["key"]].get("spice"):
                got.append(cat[part["key"]])
                stream.emit(
                    "sourcing",
                    "done",
                    what=m.get("what"),
                    ok=True,
                    key=part["key"],
                    provenance={"route": "KiCad library (missed by the classifier)"},
                    seconds=round(time.time() - t0, 2),
                )
                continue
        if (
            res.get("status") not in ("found", "ok")
            or len(pins) < 2
            or part.get("source") == "kicad-lib"
        ):
            stream.emit(
                "sourcing",
                "done",
                what=m.get("what"),
                ok=False,
                status=res.get("status"),
                reason=f"no real KiCad symbol and footprint found for {m.get('what')}; not drawing one",
                searched=[t.get("tier") for t in res.get("tiers") or []],
                seconds=round(time.time() - t0, 2),
            )
            # no verdict here: the caller says whether the board stops or goes ahead without it (QA 10-17: this
            # said "uses the closest part" and the next line said "stopped")
            m["not_found"] = True
            continue
        pre, role, note = SOURCED_SPICE.get(
            m.get("kind"), SOURCED_SPICE["two-pin passive"]
        )
        spice = {
            "prefix": pre,
            "pins": sorted(pins, key=lambda x: (len(x), x))[:2],
            "role": role,
        }
        idx_path = Path(__file__).resolve().parents[1] / "engine/parts/agent_index.json"
        idx = read_index(idx_path)
        for p in idx["parts"]:
            if p["key"] == part["key"] and not p.get("spice"):
                p["spice"] = spice
                p["spice_note"] = f"{m.get('what')} modelled as a {note}"
        idx_path.write_text(json.dumps(idx, indent=1))
        piece = {
            "key": part["key"],
            "family": str(m.get("what")),
            "name": part.get("name", part["key"]),
            # a switch is simulated across its first two pins only: offer just those, so a 3-pin slide
            # switch is wired as on/off on the pins the simulation actually switches
            "pins": {
                k: v
                for k, v in (part.get("pin_names") or {n: n for n in pins}).items()
                if m.get("kind") != "switch" or k in spice["pins"]
            },
            "spice": spice,
            "spice_note": f"{m.get('what')} modelled as a {note}",
        }
        got.append(piece)
        stream.emit(
            "sourcing",
            "done",
            what=m.get("what"),
            ok=True,
            key=part["key"],
            provenance=res.get("provenance"),
            seconds=round(time.time() - t0, 2),
        )
    return got


_ROOT = Path(__file__).resolve().parents[1]
LAYOUT_CACHE = _ROOT / "engine" / "out" / "_layout_seeds.json"


def _layout_key(spec: dict[str, Any]) -> str:
    """Same parts, footprints and nets (and the same board settings) give the same placement and copper."""
    import hashlib

    body = {k: spec.get(k) for k in ("board", "layout", "finish")}
    body["parts"] = sorted(json.dumps(p, sort_keys=True) for p in spec.get("parts", []))
    return hashlib.sha1(
        json.dumps(body, sort_keys=True, default=str).encode()
    ).hexdigest()[:20]


def _layout_seed(spec: dict[str, Any]) -> Path | None:
    """A passing earlier build of exactly this board: the engine's incremental mode keeps its placement and
    tracks (QA 10-17: the voice arm's first build spent 73 s routing a board it had routed before)."""
    try:
        run = json.loads(LAYOUT_CACHE.read_text()).get(_layout_key(spec))
    except (OSError, ValueError):
        return None
    if not run:
        return None
    # engine/out/_seeds/ holds seed builds kept out of the public library when QA runs are archived
    for d in (
        _ROOT / "engine" / "out" / run,
        _ROOT / "engine" / "out" / "_seeds" / run,
    ):
        if (d / "layout.json").is_file() and (d / "design.json").is_file():
            return d
    return None


def _layout_remember(spec: dict[str, Any], out: Path) -> None:
    if _layout_seed(spec) is not None:
        return  # the first passing layout stays the seed, so archiving later runs never loses it
    try:
        idx = json.loads(LAYOUT_CACHE.read_text())
    except (OSError, ValueError):
        idx = {}
    idx[_layout_key(spec)] = out.name
    tmp = LAYOUT_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, indent=0))
    tmp.replace(LAYOUT_CACHE)


def _apply_mask(design: dict[str, Any], out: Path) -> None:
    """A solder-mask colour asked for in an edit goes into the board's stackup (both mask layers), so the
    KiCad project, the renders and a fab house all see it."""
    mask = design.get("mask")
    if not mask or not re.fullmatch(r"#[0-9A-Fa-f]{6}", mask):
        return
    for pcb in out.glob("*.kicad_pcb"):
        txt = pcb.read_text()
        new = re.sub(
            r'(\(layer "[FB]\.Mask"\s*\(type "(?:Top|Bottom) Solder Mask"\)\s*\(color )"[^"]*"',
            rf'\1"{mask.upper()}E6"',
            txt,
        )
        if new != txt:
            pcb.write_text(new)


def _render_mask(out: Path) -> str:
    try:
        mask = json.loads((out / "design.json").read_text()).get("mask")
    except (OSError, ValueError):
        mask = None
    return (
        f"{mask.upper()}80"
        if mask and re.fullmatch(r"#[0-9A-Fa-f]{6}", mask)
        else RENDER_MASK
    )


def render(stream: Stream, out: Path, quality: str = "basic") -> dict[str, Any]:
    """Real KiCad renders of the finished board, one frame per part (what the studio drops in), timed."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "studio"))
    import kicad_frames as kf

    t0 = time.time()
    kf.MASK = _render_mask(out)
    kf.POUR = False  # the ground pour hides the traces under one copper sheet; render the traces themselves
    if "--use-board-stackup-colors" not in kf.VIEW:
        kf.VIEW.append(
            "--use-board-stackup-colors"
        )  # without it kicad-cli ignores the mask colour above
    kf.set_view(1.3, "-40,0,25")
    kf.VIEW[kf.VIEW.index("--quality") + 1] = quality
    pcb = next(out.glob("*.kicad_pcb"))
    stream.emit("render", "start", quality=quality)
    # live frames are 2/3 of the video size (the page scales them up): about 2x faster on a loaded Mac.
    # No heavy lock: an interactive render for someone watching the page is not a batch job, and waiting on
    # another job's lock stalled live builds for up to 10 minutes. kicad_frames already runs every render
    # under nice -n 10, so a long batch job (a video render) keeps most of the machine.
    t_lock = time.time()
    meta = kf.frames(pcb, out / "frames", w=920, h=680, jobs=6)
    res = {
        "frames": eng.url(out / "frames" / "frames.json"),
        "count": len(meta["frames"]),
        "quality": quality,
        "seconds": round(time.time() - t_lock, 2),  # rendering only
        "lock_wait_s": round(
            t_lock - t0, 2
        ),  # waiting for another job's heavy lock, shown apart
    }
    stream.emit("render", "done", **res)
    return res


# ---------------------------------------------------------------- the whole run


def _stored_export(out: Path) -> dict[str, Any]:
    """The same export summary a fresh build emits (the page's export card reads every field), from the copied files."""
    zp = next(out.glob("*-fab.zip"), None)
    if not zp:
        return {
            "zip": None,
            "zip_bytes": 0,
            "project": [],
            "gerbers": [],
            "bom_lines": 0,
            "seconds": 0.0,
        }
    with zipfile.ZipFile(zp) as z:
        names = [n.split("/", 1)[-1] for n in z.namelist()]
    bom = next(out.glob("*-bom.csv"), None)
    rows = 0
    if bom:
        with bom.open() as fh:
            rows = max(0, sum(1 for _ in csv.reader(fh)) - 1)
    exts = (".gtl", ".gbl", ".gto", ".gbo", ".gts", ".gbs", ".gm1", ".drl")
    return {
        "zip": eng.url(zp),
        "zip_bytes": zp.stat().st_size,
        "project": [n for n in names if "/" not in n][:3],
        "gerbers": sorted(
            n.split("/")[-1]
            for n in names
            if n.startswith("gerbers/") and n.endswith(exts)
        ),
        "bom_lines": rows,
        "seconds": 0.0,
    }


def _stored_gates(src: Path) -> list[dict[str, Any]]:
    """The gate events of a stored build's last try, from its own run.jsonl (empty if it has none)."""
    last: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    try:
        lines = (src / "run.jsonl").read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("agent") != "assembly":
            continue
        if e.get("type") == "iteration" and e.get("phase") == "place-route-check":
            cur = []
        elif e.get("type") == "gate":
            cur.append(
                {
                    k: x
                    for k, x in e.items()
                    if k not in ("ts", "t", "run", "agent", "type")
                }
            )
        elif e.get("type") == "iteration" and e.get("phase") == "decide":
            last = cur
    return last


def _stored(
    stream: Stream, ask: str, out: Path, row: dict[str, Any], t0: float
) -> dict[str, Any]:
    """Answer from a stored, verified board: copy it in and replay its parts, checks and frames."""
    board_store.copy_into(row, out)
    d = json.loads((out / "design.json").read_text())
    cat = _cat()
    stream.emit(
        "classifier",
        "done",
        yes=0,
        no=0,
        seconds=0,
        backend="stored board",
        stored=row["run"],
        score=row["score"],
    )
    show = PieceShow(stream)
    show.confirm(d, cat)
    stream.emit(
        "picker",
        "pick",
        seconds=0,
        recipe="stored board",
        parts=[
            {k: p.get(k) for k in ("ref", "piece", "value", "group", "why")}
            for p in d["parts"]
        ],
        say=d.get("say"),
    )
    rep = json.loads((out / "report.json").read_text())
    v = rep.get("verdict") or {}
    # replay the stored build's own final try (its real gate results), so the page shows "N of N checks pass"
    gates = _stored_gates(config.OUT / row["run"])
    stream.emit(
        "assembly", "iteration", iteration=1, phase="place-route-check", stored=True
    )
    stream.emit(
        "assembly",
        "built",
        iteration=1,
        seconds=0,
        stages_s=rep.get("stages_s", {}),
        board={"w": rep_board(out)[0], "h": rep_board(out)[1]},
        tracks=len(json.loads((out / "layout.json").read_text())["tracks"]),
        stored=True,
    )
    stream.emit(
        "assembly",
        "gate",
        iteration=1,
        gate="stored board (passed every gate when it was built)",
        ok=True,
        value=row["run"],
    )
    for g in gates:
        stream.emit("assembly", "gate", **{**g, "iteration": 1})
    stream.emit(
        "assembly",
        "iteration",
        iteration=1,
        phase="decide",
        decision="done",
        seconds=0,
        stored=True,
    )
    ex = _stored_export(out)
    stream.emit("export", "done", **ex)
    rd = None
    if (out / "frames" / "frames.json").exists():
        meta = json.loads((out / "frames" / "frames.json").read_text())
        rd = {
            "frames": eng.url(out / "frames" / "frames.json"),
            "count": len(meta["frames"]),
            "quality": "basic",
            "seconds": 0.0,
            "lock_wait_s": 0.0,
        }
        stream.emit("render", "start", quality="basic")
        stream.emit("render", "done", **rd)
    timings = {
        "lookup_s": round(time.time() - t0, 3),
        "classifier_s": 0.0,
        "picker_s": 0.0,
        "assembly_s": 0.0,
        "export_s": 0.0,
        "render_s": 0.0,
        "render_lock_wait_s": 0.0,
        "total_s": round(time.time() - t0, 3),
    }
    res = {
        "ask": ask,
        "ok": True,
        "iterations": 0,
        "timings": timings,
        "route": "stored board",
        "stored_from": row["run"],
        "classifier": {"yes": 0, "no": 0, "kept": 0, "rejected": 0},
        "verdict": v or None,
        "board": str(out),
        "export": ex,
        "render": rd,
    }
    (out / "live_result.json").write_text(json.dumps(res, indent=1, default=str))
    stream.emit(
        "lead",
        "done",
        **{k: res[k] for k in ("ok", "iterations", "timings", "classifier")},
        route="stored board",
        board=eng.url(out),
    )
    return res


_WORDS: set[str] | None = None


MAINS = re.compile(
    r"\b(mains|(?:1[12]0|2[234]0)\s*v(?:ac|olts?)?|line voltage|ac line|household (?:power|current))\b",
    re.I,
)


def _has_words(ask: str) -> bool:
    """True when the ask has at least one real word of 3+ letters (system word list, plus part names)."""
    global _WORDS
    if _WORDS is None:
        try:
            _WORDS = {w.strip().lower() for w in open("/usr/share/dict/words")}
        except OSError:
            _WORDS = set()
    toks = re.findall(r"[a-zA-Z]{3,}", ask)
    if re.search(r"\b[A-Za-z]{0,4}\d{3,}\b", ask):  # a part number (555, NE555, LM317)
        return True
    if not _WORDS:
        return bool(toks)
    extra = {
        "led",
        "leds",
        "usb",
        "esp",
        "oled",
        "lipo",
        "pcb",
        "rgb",
        "mosfet",
        "arduino",
        "servo",
        "bluetooth",
        "wifi",
        "airtag",
        "airtags",
        "tracker",
        "beacon",
        "gps",
        "ble",
        "iot",
        "smartwatch",
        "wearable",
    }
    return any(
        t.lower() in _WORDS or t.lower() in extra or t.lower().rstrip("s") in _WORDS
        for t in toks
    )


def _refuse(
    stream: Stream, ask: str, out: Path, t0: float, err: str, msg: str
) -> dict[str, Any]:
    """An honest stop with no model call: one say, then done (ok False)."""
    stream.emit("lead", "say", text=msg)
    tm = {"total_s": round(time.time() - t0, 2)}
    stream.emit(
        "lead", "done", ok=False, iterations=0, timings=tm, classifier={}, error=err
    )
    res = {
        "ask": ask,
        "ok": False,
        "iterations": 0,
        "timings": tm,
        "classifier": {},
        "verdict": None,
        "board": str(out),
        "export": None,
        "render": None,
        "error": err,
    }
    (out / "live_result.json").write_text(
        json.dumps(res, indent=1)
    )  # the library hides these by their error
    return res


def design(
    ask: str,
    out: Path,
    stream: Stream,
    code: str = "L1",
    context: str = "",
    do_render: bool = True,
) -> dict[str, Any]:
    """Stored board, else a Lego recipe, else the picker. Every new board goes through every gate."""
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    stream.emit("lead", "start", ask=ask)
    pf = None if context else prefilter.screen(ask)
    if pf and not pf.stop:
        # "LED LED LED", or a short ask in another language that names a recipe: rewritten, no model call
        stream.emit("lead", "say", text=pf.say)
        stream.emit("lead", "prefilter", kind=pf.kind, ask=pf.ask)
        ask = pf.ask
        pf = None
    no_board = not context and not _has_words(ask)
    mains = bool(MAINS.search(ask))
    if pf or no_board or mains:
        # injection, SQL, emoji, a huge paste or 500 LEDs (prefilter, QA round 4); "?" or keyboard mash: ask again
        # instead of spending model calls on a placeholder board. Mains voltage: refuse plainly (QA 10-17)
        msg = (
            pf.say
            if pf
            else "I only build low-voltage boards (USB or a battery, up to about 12 V): mains wiring can kill, so I "
            "will not lay out a board that touches it. I can build a low-voltage board that switches a certified "
            "relay module or smart plug instead."
            if mains
            else "I did not catch a board in that. Tell me what it should do, like a light that turns on when it gets dark."
        )
        err = (
            f"prefilter: {pf.kind}"
            if pf
            else "mains voltage"
            if mains
            else "no board in the ask"
        )
        return _refuse(stream, ask, out, t0, err, msg)
    # ETCH_NO_STORE=1 (QA sweeps): always build fresh instead of answering from a stored board
    hit = (
        board_store.lookup(ask)
        if not context and not os.environ.get("ETCH_NO_STORE")
        else None
    )
    if hit:
        stream.emit("lead", "say", text="I have built this one before. Here it is.")
        return _stored(stream, ask, out, hit, t0)
    stream.emit(
        "lead", "say", text="Screening the KiCad libraries for the parts this needs."
    )
    cl = classify(stream, ask, context)
    t_cls = time.time() - t0
    cat = _cat()
    show = PieceShow(stream)
    show.candidates(cl["kept"])
    t1 = time.time()
    rec = recipes.match(ask, cat) if not context else None
    # ETCH_CLASSIFIER=1: NVIDIA's prompt classifier picks the first picker model (repairs stay on the lead)
    rt = task_router.route(ask, bool(rec), config.MODEL, config.SUB_MODEL)
    if rt:
        stream.emit("lead", "router", **rt)
    route = "picker"
    if rec:
        d, route, used = rec
        have = {p["key"] for p in cl["kept"]}
        cl["kept"] = cl["kept"] + [p for p in used if p["key"] not in have]
        msgs = [
            {
                "role": "system",
                "content": "You are Etch's picker. You design small through-hole circuit boards from KiCad library parts.\n"
                + PICK_RULES,
            },
            {
                "role": "user",
                "content": f"Board request: {ask}\nKEPT list (key | family | pins | simulation):\n"
                + "\n".join(_piece_line(p) for p in cl["kept"]),
            },
            {"role": "assistant", "content": json.dumps(d)},
        ]
        stream.emit(
            "picker",
            "pick",
            seconds=round(time.time() - t1, 3),
            recipe=route,
            model=None,
            parts=[
                {k: p.get(k) for k in ("ref", "piece", "value", "group", "why")}
                for p in d["parts"]
            ],
            scenarios=d.get("scenarios", []),
            benchmarks=d.get("benchmarks", []),
            say=d.get("say"),
        )
    else:
        stream.emit(
            "lead", "say", text=f"{len(cl['kept'])} parts fit. Picking the circuit."
        )
        try:
            d, msgs = pick(
                stream, ask, cl["kept"], context, model=rt.get("model") if rt else None
            )
            if d.get("missing"):
                got = source_missing(stream, d["missing"])
                if not got:
                    names = ", ".join(str(m.get("what")) for m in d["missing"])
                    stream.emit(
                        "lead",
                        "say",
                        text=f"I could not find a real, sourced {names}, so I stopped instead of using a wrong part.",
                    )
                    stream.emit(
                        "lead",
                        "done",
                        ok=False,
                        iterations=0,
                        timings={"total_s": round(time.time() - t0, 2)},
                        classifier={},
                        error=f"missing part: {names}",
                    )
                    return {
                        "ask": ask,
                        "ok": False,
                        "iterations": 0,
                        "timings": {"total_s": round(time.time() - t0, 2)},
                        "classifier": {},
                        "verdict": None,
                        "board": str(out),
                        "export": None,
                        "render": None,
                        "error": f"missing part: {names}",
                    }
                if lost := [
                    str(m.get("what")) for m in d["missing"] if m.get("not_found")
                ]:
                    stream.emit(
                        "lead",
                        "say",
                        text=f"I could not find a real, sourced {', '.join(lost)}; the rest goes ahead and that part "
                        "uses the closest one KiCad has.",
                    )
                cl["kept"] = cl["kept"] + got
                d, msgs = pick(
                    stream,
                    ask,
                    cl["kept"],
                    context
                    + "\nFetched for this ask (now in the KEPT list): "
                    + ", ".join(f"{g['key']} ({g['family']})" for g in got),
                )
        except PickTimeout as e:
            stream.emit(
                "lead",
                "done",
                ok=False,
                iterations=0,
                timings={"total_s": round(time.time() - t0, 2)},
                classifier={},
                error=f"missing parts: {e}",
            )
            return {
                "ask": ask,
                "ok": False,
                "iterations": 0,
                "timings": {"total_s": round(time.time() - t0, 2)},
                "classifier": {},
                "verdict": None,
                "board": str(out),
                "export": None,
                "render": None,
                "error": f"missing parts: {e}",
            }
    t_pick = time.time() - t1
    show.confirm(d, {**cat, **{p["key"]: p for p in cl["kept"]}})
    stream.emit("lead", "say", text=d.get("say") or "Placing, routing and checking.")
    t2 = time.time()
    # a recipe is fixed wiring: if it fails a gate that is a bug to fix here, not something to ask a model about
    asm = assemble(
        stream,
        d,
        msgs,
        out,
        code,
        ask,
        cl["kept"],
        max_iters=1 if route != "picker" else MAX_ITERS,
    )
    t_asm = time.time() - t2
    ex = None
    t3 = time.time()
    if asm["ok"]:
        ex = export(stream, out)
    t_ex = time.time() - t3
    rd = render(stream, out) if (asm["ok"] and do_render) else None
    if asm["ok"] and rd and not context:
        board_store.save(ask, out, recipe=route)
    timings = {
        "classifier_s": round(t_cls, 3),
        "picker_s": round(t_pick, 2),
        "assembly_s": round(t_asm, 2),
        "export_s": round(t_ex, 2),
        "render_s": rd["seconds"] if rd else 0.0,
        "render_lock_wait_s": rd["lock_wait_s"] if rd else 0.0,
        "total_s": round(time.time() - t0, 2),
    }
    res = {
        "ask": ask,
        "ok": asm["ok"],
        "iterations": asm["iterations"],
        "timings": timings,
        "route": route,
        "classifier": {
            "backend": cl["backend"],
            "yes": cl["yes"],
            "no": cl["no"],
            "kept": len(cl["kept"]),
            "rejected": len(cl["rejected"]),
        },
        "verdict": asm["verdict"],
        "board": str(out),
        "export": ex,
        "render": rd,
    }
    (out / "live_result.json").write_text(json.dumps(res, indent=1, default=str))
    stream.emit(
        "lead",
        "done",
        **{k: res[k] for k in ("ok", "iterations", "timings", "classifier", "route")},
        board=eng.url(out),
    )
    return res


def _pcb_refs(out: Path) -> set[str]:
    try:
        pcb = next(out.glob("*.kicad_pcb")).read_text()
    except StopIteration:
        return set()
    return set(re.findall(r'\(property "Reference" "([^"]+)"', pcb))


def modify(
    request: str,
    base_out: Path,
    out: Path,
    stream: Stream,
    code: str = "L2",
    do_render: bool = True,
) -> dict[str, Any]:
    """Change a built board for real: screen parts for the change, re-pick with the base design
    held fixed, then a full place + route + check of the whole board (nothing is pasted on)."""
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    pf = prefilter.screen(request, edit=True)
    if pf and not pf.stop:
        request = pf.ask
    elif pf:
        stream.emit("lead", "start", ask=request, base=eng.url(base_out))
        return _refuse(stream, request, out, t0, f"prefilter: {pf.kind}", pf.say)
    base = json.loads((base_out / "design.json").read_text())
    stream.emit("lead", "start", ask=request, base=eng.url(base_out))
    if pf:
        stream.emit("lead", "say", text=pf.say)
    stream.emit("lead", "say", text="Screening parts for the change.")
    summary = "; ".join(
        f"{p['ref']} {p['piece']} {p.get('value', '')}" for p in base["parts"]
    )
    by_key = _cat()
    # an edit Etch knows (recolour, add a buzzer or power LED, remove it, swap the power input, smaller parts):
    # the new design comes from code, so no classifier and no model call, straight to place + route + checks
    hit = recipes.edit(request, base, by_key)
    rc, edit_name = hit if hit else (None, None)
    dropped = set(rc.pop("_dropped", [])) if rc else set()
    if rc:
        cl = {
            "kept": [by_key[p["piece"]] for p in rc["parts"]],
            "yes": 0,
            "no": 0,
            "rejected": [],
        }
        stream.emit(
            "classifier", "done", yes=0, no=0, seconds=0, backend=f"edit: {edit_name}"
        )
    else:
        cl = classify(
            stream, request, f"It is added to an existing board with: {summary}"
        )
    t_cls = time.time() - t0
    kept = list(cl["kept"])
    have = {p["key"] for p in kept}
    kept += [
        by_key[p["piece"]]
        for p in base["parts"]
        if p["piece"] not in have and p["piece"] in by_key
    ]
    t1 = time.time()
    extra = (
        "This CHANGES an existing, verified board. Current design (JSON):\n"
        + json.dumps(base)
        + "\nReturn the WHOLE design: every existing part with the same ref, piece and value, and the same "
        "pins unless the change has to rewire one. Add the new parts in a new group. Keep the existing "
        "scenarios and benchmarks and add benchmarks for the new function. Keep name and title. "
        "Never put the new load itself on a net of the existing circuit (no fan, motor or buzzer on an "
        "existing transistor or LED net): power it from the supply rails through its OWN transistor, "
        "with a flyback diode across a fan or motor. If it must follow an existing signal ('turns on "
        "with the blink'), drive its transistor's base from that signal through at least 4.7k so the "
        "original timing does not change. 'While it flashes' or 'while it runs' means whenever the "
        "board is powered."
    )
    if rc:
        # a known edit: no model call, one build
        d, msgs = rc, []
        stream.emit(
            "picker",
            "pick",
            seconds=0.0,
            recipe=edit_name,
            model=None,
            parts=[
                {k: p.get(k) for k in ("ref", "piece", "value", "group", "why")}
                for p in d["parts"]
            ],
            say=d.get("say"),
        )
    else:
        try:
            d, msgs = pick(stream, request, kept, extra)
        except PickTimeout as e:
            # the missing-parts events are already out; end the job cleanly, same shape as design()
            res = {
                "ask": request,
                "base": str(base_out),
                "route": "picker",
                "ok": False,
                "iterations": 0,
                "timings": {"total_s": round(time.time() - t0, 2)},
                "classifier": {},
                "verdict": None,
                "board": str(out),
                "export": None,
                "render": None,
                "error": f"missing parts: {e}",
            }
            stream.emit(
                "lead",
                "done",
                **{k: res[k] for k in ("ok", "iterations", "timings", "classifier")},
                error=res["error"],
            )
            return res
    t_pick = time.time() - t1
    base_refs = {p["ref"] for p in base["parts"]}
    new_refs = [p["ref"] for p in d.get("parts", []) if p["ref"] not in base_refs]

    def must(design: dict[str, Any], o: Path) -> str | None:
        refs = {p["ref"] for p in design.get("parts", [])}
        on_board = _pcb_refs(o)
        problems = [
            f"{r} (added for the request) is gone" for r in new_refs if r not in refs
        ]
        problems += [
            f"{r} is in the design but not on the saved board"
            for r in new_refs
            if r in refs and r not in on_board
        ]
        problems += [
            f"existing part {r} was dropped" for r in sorted(base_refs - refs - dropped)
        ]
        if not new_refs and not rc:
            problems.append("nothing was added for the request")
        return "; ".join(problems) or None

    stream.emit(
        "lead", "say", text=d.get("say") or "Re-placing and re-routing the whole board."
    )
    t2 = time.time()
    asm = assemble(
        stream,
        d,
        msgs,
        out,
        code,
        request,
        kept,
        must=must,
        max_iters=1 if rc else MAX_ITERS,
        prior=base_out,
    )
    t_asm = time.time() - t2
    final = json.loads((out / "design.json").read_text()) if asm["ok"] else d
    fb = {p["ref"]: p for p in base["parts"]}
    stream.emit(
        "assembly",
        "change",
        added=[
            f"{p['ref']} {p['piece'].split('@')[0].split(':')[-1]} {p.get('value', '')}".strip()
            for p in final["parts"]
            if p["ref"] not in fb
        ],
        kept=sum(
            1
            for p in final["parts"]
            if p["ref"] in fb and p["piece"] == fb[p["ref"]]["piece"]
        ),
        rewired=[
            p["ref"]
            for p in final["parts"]
            if p["ref"] in fb and p.get("pins") != fb[p["ref"]].get("pins")
        ],
        dropped=sorted(base_refs - {p["ref"] for p in final["parts"]}),
    )
    t3 = time.time()
    ex = export(stream, out) if asm["ok"] else None
    t_ex = time.time() - t3
    rd = render(stream, out) if (asm["ok"] and do_render) else None
    timings = {
        "classifier_s": round(t_cls, 2),
        "picker_s": round(t_pick, 2),
        "assembly_s": round(t_asm, 2),
        "export_s": round(t_ex, 2),
        "render_s": rd["seconds"] if rd else 0.0,
        "render_lock_wait_s": rd["lock_wait_s"] if rd else 0.0,
        "total_s": round(time.time() - t0, 2),
    }
    res = {
        "ask": request,
        "base": str(base_out),
        "route": f"edit: {edit_name}" if rc else "picker",
        "ok": asm["ok"],
        "iterations": asm["iterations"],
        "timings": timings,
        "classifier": {
            "yes": cl["yes"],
            "no": cl["no"],
            "kept": len(cl["kept"]),
            "rejected": len(cl["rejected"]),
        },
        "verdict": asm["verdict"],
        "board": str(out),
        "export": ex,
        "render": rd,
    }
    (out / "live_result.json").write_text(json.dumps(res, indent=1, default=str))
    stream.emit(
        "lead",
        "done",
        **{k: res[k] for k in ("ok", "iterations", "timings", "classifier")},
        board=eng.url(out),
    )
    return res


def main(argv: list[str]) -> None:
    """CLI. --events prints every event as a JSON line (the studio server relays them as SSE).

    design "ask"                 new board from the KiCad libraries
    modify "change" --base DIR   change a built board (full re-place, re-route, re-check)
    bench  "requirements" --base DIR   company benchmarks, measured on a built board
    """
    import argparse

    from . import live_bench

    a = argparse.ArgumentParser()
    a.add_argument("mode", choices=["design", "modify", "bench"])
    a.add_argument("text")
    a.add_argument("--base")
    a.add_argument("--code", default="L1")
    a.add_argument("--events", action="store_true")
    a.add_argument("--no-render", action="store_true")
    x = a.parse_args(argv)
    if x.mode == "bench":
        base = Path(x.base).resolve()
        st = Stream(base.name, log=base / "bench.jsonl", echo=x.events)
        v = json.loads((base / "live_result.json").read_text())
        st.emit("lead", "start", ask=x.text, base=eng.url(base))
        r = live_bench.run(st, x.text, base, v["verdict"], v.get("export"))
        st.emit(
            "lead",
            "done",
            ok=r["failed"] == 0,
            **{k: r[k] for k in ("passed", "failed", "unmeasured", "seconds")},
        )
        if not x.events:
            print(json.dumps(r, indent=1))
        return
    out = eng.new_run_dir("live")
    st = Stream(out.name, log=out / "run.jsonl", echo=x.events)
    if x.mode == "design":
        r = design(x.text, out, st, code=x.code, do_render=not x.no_render)
    else:
        r = modify(
            x.text,
            Path(x.base).resolve(),
            out,
            st,
            code=x.code,
            do_render=not x.no_render,
        )
    if not x.events:
        print(
            json.dumps(
                {
                    k: r[k]
                    for k in (
                        "ask",
                        "ok",
                        "iterations",
                        "timings",
                        "classifier",
                        "board",
                    )
                },
                indent=1,
            )
        )
        if r["verdict"]:
            print(json.dumps(r["verdict"]["benchmarks"], indent=0)[:3000])


if __name__ == "__main__":
    main(sys.argv[1:])

"""Crash 10-17 (live-1792274444325 trackpad, Nixie job R3DcFGa3We_iGdwo): a board with no LED failed with "ngspice
failed: no LED or load in the circuit", then the model-written "idle current draw" row read None and failed too.
Now any circuit simulates (supply probe, datasheet stand-ins for unmapped chips), a board with no supply reports
"not simulated" as a pass, the supply band comes from datasheet values in code, and a benchmark naming a metric
the simulation cannot produce is "not measurable" (pass None), never a failure."""

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent import engine_api, live
from etchlib import build, sim
from etchlib import spec as spec_mod
from etchlib.spec import load_spec

NGSPICE = shutil.which("ngspice") or (
    "/opt/homebrew/bin/ngspice" if Path("/opt/homebrew/bin/ngspice").exists() else None
)
PICO = "kicad:MCU_Module:RaspberryPi_Pico@RaspberryPi_Pico_Common_THT"
RES = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
CAP = "kicad:Device:C@C_Disc_D5.0mm_W2.5mm_P2.50mm"
CONN = "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical"
TINY = "test:ATtiny85-20P@DIP-8"


def _trackpad():
    """The failing board: a Pico on USB (VBUS = VCC), four 10k pull-ups, four 100n caps, no LED, no load."""
    gnd = {k: "GND" for k in ("3", "8", "13", "18", "23", "28", "38")}
    parts = [
        {
            "ref": "U1",
            "part": PICO,
            "pins": {
                **gnd,
                "40": "VCC",
                "20": "TP_X",
                "21": "TP_Y",
                "22": "TP_Z",
                "24": "TP_CLICK",
            },
        }
    ]
    for i, n in enumerate(("TP_X", "TP_Y", "TP_Z", "TP_CLICK"), 1):
        parts.append(
            {"ref": f"R{i}", "part": RES, "value": "10k", "pins": {"1": "VCC", "2": n}}
        )
        parts.append(
            {"ref": f"C{i}", "part": CAP, "value": "100n", "pins": {"1": n, "2": "GND"}}
        )
    return parts


def _load(tmp: Path, parts, benchmarks=(), scenarios=None) -> spec_mod.Spec:
    raw = {
        "name": "t",
        "board": {"thickness": 1.6, "layers": 2},
        "power": {"input": "first connector", "voltage": 5.0},
        "parts": parts,
        "benchmarks": list(benchmarks),
    }
    if scenarios:
        raw["scenarios"] = scenarios
    (tmp / "spec.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    return load_spec(tmp / "spec.yaml")


@pytest.fixture
def tiny_index(monkeypatch):
    """An ATtiny85 the index has no SPICE card for (as fetched parts arrive)."""
    real = spec_mod.load_index

    def idx():
        d = dict(real())
        d[TINY] = {
            "key": TINY,
            "lib_id": "MCU_Microchip_ATtiny:ATtiny85-20P",
            "footprint": "Package_DIP:DIP-8_W7.62mm",
            "name": "ATtiny85-20P",
            "value": "ATtiny85-20P",
            "pins": [str(i) for i in range(1, 9)],
        }
        return d

    monkeypatch.setattr(spec_mod, "load_index", idx)


def test_fallback_models_unmapped_chips():
    tiny = SimpleNamespace(
        lib_id="MCU_Microchip_ATtiny:ATtiny85-20P",
        value="ATtiny85",
        pins={"8": "VCC", "4": "GND", "5": "PB0"},
    )
    s, why = sim.fallback_spice(tiny)
    assert s["role"] == "ic" and s["pins"] == ["8", "4"] and s["idd_ma"] == 1.0, (
        s,
        why,
    )
    ch = SimpleNamespace(
        lib_id="Interface_USB:CH340G",
        value="CH340G",
        pins={"16": "VCC", "1": "GND", "2": "TX"},
    )
    assert sim.fallback_spice(ch)[0]["idd_ma"] == 12.0
    reg = SimpleNamespace(
        lib_id="Regulator_Linear:L7805",
        value="L7805",
        pins={"1": "VIN", "2": "GND", "3": "VCC"},
    )
    s, _ = sim.fallback_spice(reg)
    assert s["role"] == "ldo" and s["vout"] == 5.0 and s["pins"] == ["1", "3", "2"]
    ind = SimpleNamespace(lib_id="Device:L", value="10u", pins={"1": "A", "2": "B"})
    assert sim.fallback_spice(ind)[0]["prefix"] == "L"
    blob = SimpleNamespace(lib_id="Nope:Thing", value="x", pins={"1": "A"})
    assert sim.fallback_spice(blob)[0]["role"] == "passive"


def test_trackpad_budget_from_datasheets():
    with tempfile.TemporaryDirectory() as td:
        b = sim.supply_budget(_load(Path(td), _trackpad()))
    assert b["floor_ma"] == pytest.approx(sim.MCU_IDLE_MA / 2)
    refs = {t["ref"]: t["ma"] for t in b["terms"]}
    assert refs["R1"] == pytest.approx(0.5) and "C1" not in refs
    assert 30 < b["ceiling_ma"] < 40


@pytest.mark.skipif(not NGSPICE, reason="no ngspice")
def test_no_led_pico_board_simulates_with_supply_probe():
    with tempfile.TemporaryDirectory() as td:
        sp = _load(
            Path(td),
            _trackpad(),
            [
                {
                    "check": "idle.supply_ma_avg",
                    "min": 0,
                    "max": 200,
                    "text": "idle current draw",
                }
            ],
        )
        res = sim.simulate(sp, Path(td))
    assert res["ok"] and res["simulated"], res.get("error")
    assert "no LED" in res["probe"]
    supply = res.get("supply_ma_avg", res.get("idle.supply_ma_avg"))
    assert 15 < supply < 30, (
        supply
    )  # the Pico's running current (about 20 mA) plus four 10k pull-ups
    checks = {
        "sim": res,
        "drc": {"violations": 0, "unconnected": 0, "parity": 0},
        "erc": {"errors": 0},
        "netlist": {"mismatches": 0},
    }
    rows = build._bench(sp, checks)
    code = [r for r in rows if r.get("by") == "code"]
    assert code and all(r["pass"] for r in code), code
    assert all(r["pass"] is not False for r in rows), rows


@pytest.mark.skipif(not NGSPICE, reason="no ngspice")
def test_unmapped_attiny_draws_datasheet_current(tiny_index):
    parts = [
        {"ref": "J1", "part": CONN, "pins": {"1": "VCC", "2": "GND"}},
        {
            "ref": "U1",
            "part": TINY,
            "pins": {"8": "VCC", "4": "GND", "5": "PB0", "6": "PB1"},
        },
        {"ref": "R1", "part": RES, "value": "10k", "pins": {"1": "VCC", "2": "PB0"}},
        {"ref": "C1", "part": CAP, "value": "100n", "pins": {"1": "VCC", "2": "GND"}},
    ]
    with tempfile.TemporaryDirectory() as td:
        sp = _load(Path(td), parts)
        res = sim.simulate(sp, Path(td))
    assert res["ok"] and res["simulated"], res.get("error")
    assert "U1" in res["not_modelled"]
    supply = [v for k, v in res.items() if k.endswith("supply_ma_avg")][0]
    assert 0.8 < supply < 1.3, (
        supply
    )  # ATtiny85 about 1 mA; the pull-up into a high-impedance pin draws nothing


def test_nothing_to_simulate_is_a_pass_with_a_note():
    parts = [{"ref": "R1", "part": RES, "value": "10k", "pins": {"1": "A", "2": "B"}}]
    with tempfile.TemporaryDirectory() as td:
        sp = _load(
            Path(td), parts, [{"check": "supply_ma_avg", "max": 10, "text": "supply"}]
        )
        res = sim.simulate(sp, Path(td))
        assert (
            res["ok"]
            and res["simulated"] is False
            and res["note"].startswith("not simulated")
        ), res
        checks = {
            "sim": res,
            "drc": {"violations": 0, "unconnected": 0, "parity": 0},
            "erc": {"errors": 0},
            "netlist": {"mismatches": 0},
        }
        rows = build._bench(sp, checks)
    assert rows[0]["pass"] is None and rows[0]["status"] == "not measurable"


def test_unknown_metric_not_measurable_but_sim_error_still_fails():
    with tempfile.TemporaryDirectory() as td:
        sp = _load(
            Path(td),
            _trackpad(),
            [{"check": "OLED.avg_ma", "min": 1, "text": "OLED on"}],
        )
    gates = {
        "drc": {"violations": 0, "unconnected": 0, "parity": 0},
        "erc": {"errors": 0},
        "netlist": {"mismatches": 0},
    }
    ok = build._bench(
        sp, {"sim": {"ok": True, "simulated": True, "supply_ma_avg": 20.0}, **gates}
    )
    assert ok[0]["pass"] is None and "not measurable" in ok[0]["text"]
    bad = build._bench(sp, {"sim": {"ok": False, "error": "boom"}, **gates})
    assert bad[0]["pass"] is False


def test_verdict_ignores_not_measurable_rows():
    c = {
        "erc": {"errors": 0},
        "drc": {"violations": 0, "types": {}, "unconnected": 0, "parity": 0},
        "netlist": {"mismatches": 0},
        "models": {"missing": 0},
        "sim": {"ok": True},
    }
    rep = {
        "checks": c,
        "benchmarks": [{"check": "x", "text": "x", "value": None, "pass": None}, {"check": "y", "text": "y", "value": 1, "pass": True}],
        "unrouted": 0,
        "mode": "m",
        "total_s": 1,
    }
    assert engine_api.verdict(rep)["pass"] is True
    rep["benchmarks"].append({"check": "z", "text": "z", "value": 9, "pass": False})
    assert engine_api.verdict(rep)["pass"] is False


def test_model_supply_rows_dropped_recipe_rows_kept():
    design = {
        "name": "t",
        "title": "T",
        "voltage": 5,
        "parts": [{"ref": "J1", "piece": CONN, "pins": {"1": "VCC", "2": "GND"}, "group": "power"}],
        "benchmarks": live._by_model(
            {
                "benchmarks": [
                    {"check": "idle.supply_ma_avg", "min": 50, "max": 200, "text": "m"}
                ]
            }
        )["benchmarks"]
        + [{"check": "room.supply_ma_avg", "max": 300, "text": "recipe"}],
    }
    checks = [b["check"] for b in live.to_spec(design, "ask")["benchmarks"]]
    assert "idle.supply_ma_avg" not in checks and "room.supply_ma_avg" in checks

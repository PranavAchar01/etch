"""Fast checks for the pure-Python parts of the engine (no KiCad run).
The full build is exercised by `uv run etch.py build ...`, which runs real ERC, DRC and ngspice."""

import json
from pathlib import Path

from etchlib import sexpr
from etchlib.parts import find_part, load_index
from etchlib.sim import netlist
from etchlib.spec import load_spec, spec_from_prompt

ENGINE = Path(__file__).resolve().parent.parent


def test_index_has_100_verified_parts():
    idx = json.loads((ENGINE / "parts/index.json").read_text())
    assert idx["count"] == len(idx["parts"]) >= 100
    assert len(load_index()) >= 100
    for p in idx["parts"]:
        if p["category"] != "mechanical":
            assert p["model"].endswith(".step") and Path(p["model"]).is_file(), p["key"]


def test_find_part_tiers():
    assert find_part("red LED 0805")["part"]["key"] == "led_red_0805"
    lcsc = find_part("unknown widget", lcsc_id="C2040")
    assert lcsc["status"] == "needs_network"
    assert lcsc["source"] == "LCSC via easyeda2kicad"
    assert "--full" in lcsc["easyeda"]["command"]
    hits = find_part("unknown widget", search=lambda q: [{"title": q, "url": "https://example.com"}])
    assert hits["status"] == "leads" and hits["source"] == "web"


def test_alt_spec_extends_base():
    base = load_spec(ENGINE / "specs/led_flasher.yaml")
    alt = load_spec(ENGINE / "specs/led_flasher_alt.yaml")
    assert {p.ref for p in alt.parts} - {p.ref for p in base.parts} == {"D2", "R5"}
    assert set(alt.added_refs) == {"D2", "R5"}


def test_prompt_routes_to_template_and_colour():
    spec = spec_from_prompt("a board that flashes a green LED", "g")
    assert spec["template"] == "astable_led"
    assert any(p.get("part") == "led_green_0805" for p in spec["parts"])


def test_spice_netlist_uses_real_models():
    text, leds, _loads = netlist(load_spec(ENGINE / "specs/led_flasher.yaml"), 1.0, 1e-3)
    assert leds == ["D1"]
    assert ".model 2N3904 NPN" in text and "PWL(0 0 1m 5.0)" in text


def test_sexpr_round_trip():
    src = '(a (b "x y" 1.5) (c))'
    assert sexpr.parse(sexpr.dump(sexpr.parse(src))) == sexpr.parse(src)

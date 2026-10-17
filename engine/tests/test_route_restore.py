"""QA round 5: the round-3 router is live again, and full rebuilds get a longer layout timeout."""

import json
import sys
from pathlib import Path

import pytest

from etchlib import build, kicad

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE / "kicad_side"))
from router import Router, net_order

GREEN_BUTTON = ENGINE / "out/live-1792211134286/layout_result.json"
RULES = {"track": 0.3, "clearance": 0.2, "via_d": 0.6, "via_drill": 0.3, "edge_clearance": 0.5}


def test_router_has_no_round4_budget_fields():
    r = Router(10, 10, RULES, [])
    assert not hasattr(r, "deadline") and not hasattr(r, "h_weight")


@pytest.mark.skipif(not GREEN_BUTTON.exists(), reason="curated board not present")
def test_router_routes_curated_board_from_its_pads():
    res = json.loads(GREEN_BUTTON.read_text())
    pads = res["pads"]
    router = Router(res["board"]["w"], res["board"]["h"], RULES, pads, pitch=0.2)
    failed = [n for n in net_order(pads, router.nets) if router.route_net(n)[2]]
    assert failed == []
    assert router.tracks


def test_kicad_python_forwards_timeout(monkeypatch):
    seen = {}
    monkeypatch.setattr(kicad, "run", lambda cmd, **kw: seen.update(kw))
    kicad.kicad_python(Path("x.py"))
    assert seen["timeout"] == 300
    kicad.kicad_python(Path("x.py"), "a", timeout=420)
    assert seen["timeout"] == 420


class _Stop(Exception):
    pass


@pytest.mark.parametrize("keep,want", [(None, build.LAYOUT_FULL_TIMEOUT_S), ("incremental", 300)])
def test_layout_timeout_full_vs_incremental(monkeypatch, tmp_path, keep, want):
    spec = ENGINE / "specs/led_flasher.yaml"
    base = {"parts": {}, "tracks": [], "vias": [], "layout": None} if keep else None
    monkeypatch.setattr(build, "_base_state", lambda s, b: (base, "x"))
    monkeypatch.setattr(build.schematic, "write_schematic", lambda s, out: {p.ref: "u" for p in s.parts})
    seen = {}

    def fake(script, *args, timeout=300):
        seen["timeout"] = timeout
        raise _Stop

    monkeypatch.setattr(build, "kicad_python", fake)
    with pytest.raises(_Stop):
        build.build(spec, tmp_path / "out")
    assert seen["timeout"] == want
    assert build.LAYOUT_FULL_TIMEOUT_S >= 400

"""QA 10-17, run out/live-1792255105003: the router laid 0 tracks in 39.7 s. GPIO15 hit the 400k expansion cap on
every attempt and the old loop ripped up the last attempt too, so 19 good tracks became 0. route_all retries a
capped net with a larger search, keeps the best attempt, and keeps its copper when the wall-clock budget runs out."""

import json
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE / "kicad_side"))
import router as rt  # noqa: E402

RGB_PICO = ENGINE / "out/live-1792255105003/layout_result.json"
RULES = {
    "track": 0.3,
    "clearance": 0.2,
    "via_d": 0.6,
    "via_drill": 0.3,
    "edge_clearance": 0.5,
    "power_nets": ["VCC", "GND"],
    "power_track": 0.6,
}


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _FakeRouter:
    """Same surface route_all uses. Nets in `ok` route; "HARD" routes only with the retry search budget.
    Each route_net call costs one tick of the fake clock."""

    def __init__(self, ok, clock, nets):
        self.ok, self.clock, self.nets = ok, clock, nets
        self.tracks, self.vias, self.capped = [], [], False
        self.done: set[str] = set()

    def route_net(self, net, max_expand=rt.MAX_EXPAND):
        self.clock.t += 1
        self.capped = False
        if net in self.ok or (net == "HARD" and max_expand > rt.MAX_EXPAND):
            self.tracks.append(net)
            self.done.add(net)
            return [net], [], []
        self.capped = True
        return [], [], [net]

    def _groups(self, net):
        return [0] if net in self.done else [0, 1]


def test_capped_net_is_retried_with_a_larger_search():
    clock, nets = _Clock(), ["A", "HARD", "B"]
    router, info = rt.route_all(lambda: _FakeRouter({"A", "B"}, clock, nets), nets, clock=clock)
    assert info["failed"] == [] and info["attempts"] == 2
    assert sorted(info["new_tracks"]) == ["A", "B", "HARD"]


def test_best_attempt_is_kept_not_the_last():
    clock, nets = _Clock(), ["A", "B", "NEVER"]
    router, info = rt.route_all(lambda: _FakeRouter({"A", "B"}, clock, nets), nets, clock=clock)
    assert info["attempts"] == 4 and info["failed"] == ["NEVER"]
    assert sorted(info["new_tracks"]) == ["A", "B"]  # never empty because the last attempt failed
    assert info["capped"] == ["NEVER"]


def test_timeout_keeps_partial_copper_and_says_so():
    clock, nets = _Clock(), ["A", "B", "C", "D", "E"]
    router, info = rt.route_all(
        lambda: _FakeRouter({"A", "B", "C", "D", "E"}, clock, nets), nets, budget_s=2.5, clock=clock
    )
    assert info["timed_out"]
    assert info["new_tracks"] == ["A", "B", "C"]
    assert info["failed"] == ["D", "E"]


def test_no_round4_fields_on_router():
    r = rt.Router(10, 10, RULES, [])
    assert not hasattr(r, "deadline") and not hasattr(r, "h_weight")


@pytest.mark.skipif(not RGB_PICO.exists(), reason="failing run not present")
def test_rgb_pico_board_routes_every_net_from_its_pads():
    res = json.loads(RGB_PICO.read_text())
    pads, w, h = res["pads"], res["board"]["w"], res["board"]["h"]
    order = rt.net_order(pads, sorted({p["net"] for p in pads if p["net"]}))
    router, info = rt.route_all(lambda: rt.Router(w, h, RULES, pads, pitch=0.2), order, budget_s=300)
    assert not info["timed_out"]
    assert info["failed"] == [], info
    assert len(info["new_tracks"]) >= 19

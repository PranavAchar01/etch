"""QA 10-17, router speed (qa/router-bench-1017.md): the A* state is one cell on one layer with the heading kept per
state, the heuristic measures to boxes of targets when there are many, and route_net aims each search at the
NEAR_GROUPS nearest groups instead of every pad of the net."""

import math
import random
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE / "kicad_side"))
import router as rt  # noqa: E402

RULES = {"track": 0.3, "clearance": 0.2, "via_d": 0.6, "via_drill": 0.3, "edge_clearance": 0.5}


def _pad(ref, net, x, y, layers=("F.Cu", "B.Cu")):
    return {
        "ref": ref,
        "num": "1",
        "net": net,
        "x": x,
        "y": y,
        "rect": [x - 0.3, y - 0.3, x + 0.3, y + 0.3],
        "layers": list(layers),
    }


def _octile(dx, dy):
    dx, dy = abs(dx), abs(dy)
    return dx + dy + (math.sqrt(2) - 2) * min(dx, dy)


def test_state_is_cell_and_layer_not_heading():
    # a target nobody can reach: the search visits every open (layer, cell) once and gives up without hitting a
    # budget of one expansion per (layer, cell). With the heading in the state it needed up to 9x that and capped.
    r = rt.Router(10, 10, RULES, [_pad("A", "N", 5, 5)], pitch=0.25)
    net = r.net_id["N"]
    path = r._search(net, {(0, 20, 20)}, {(0, 0, 0)}, max_expand=2 * r.nx * r.ny)
    assert path is None and not r.capped


def test_heading_still_prices_turns():
    # open board, same row: the cheapest path is one straight run, no zig-zag, no via
    r = rt.Router(12, 6, RULES, [_pad("A", "N", 2, 3), _pad("B", "N", 10, 3)], pitch=0.25)
    t, v, rest = r.route_net("N")
    assert rest == [] and v == []
    assert len(t) == 1 and len(t[0]["pts"]) == 2
    path = r._search(r.net_id["N"], {(0, 8, 12)}, {(0, 40, 20)})
    cells = [(c[1], c[2]) for c in path]
    assert len(set(path)) == len(path)  # one visit per (layer, cell)
    assert all(max(abs(a[0] - b[0]), abs(a[1] - b[1])) == 1 for a, b in zip(cells, cells[1:]))
    turns = sum(
        1 for a, b, c in zip(cells, cells[1:], cells[2:]) if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1])
    )
    assert turns <= 2  # one diagonal leg and one straight leg, not a staircase


def test_box_heuristic_is_a_lower_bound():
    rnd = random.Random(7)
    cells = {(rnd.randrange(200), rnd.randrange(120)) for _ in range(300)}
    h = rt._heuristic(cells, 1.0)
    for _ in range(500):
        x, y = rnd.randrange(200), rnd.randrange(120)
        exact = min(_octile(tx - x, ty - y) for tx, ty in cells)
        assert h(x, y) <= exact + 1e-9
    assert all(h(x, y) == 0 for x, y in cells)
    few = {(3, 4), (50, 60)}
    assert rt._heuristic(few, 1.4)(3, 14) == 1.4 * 10


def test_nearest_groups_order_and_padless_last():
    base = {"pads": [_pad("A", "N", 0, 0)], "tracks": [{"pts": [[0, 0], [10, 0]]}]}
    groups = [
        base,
        {"pads": [_pad("B", "N", 30, 0)], "tracks": []},
        {"pads": [], "tracks": [{"pts": [[11, 0], [12, 0]]}]},
        {"pads": [_pad("C", "N", 12, 1)], "tracks": []},  # near the end of base's track, not its pad
        {"pads": [_pad("D", "N", 0, 8)], "tracks": []},
    ]
    assert rt._nearest_groups(base, groups) == [3, 4, 1, 2]


def _gnd_router():
    # five single-pad groups on one net in a row; the first route_net search must aim at the two nearest only
    pads = [_pad(f"P{i}", "GND", 2 + 4 * i, 4) for i in range(5)]
    return rt.Router(22, 8, RULES, pads, pitch=0.25)


def test_route_net_aims_at_nearest_groups_only():
    r = _gnd_router()
    seen = []
    search = r._search

    def spy(net, src, targets, max_expand=rt.MAX_EXPAND):
        seen.append({ix for (_, ix, _) in targets})
        return search(net, src, targets, max_expand)

    r._search = spy
    t, v, rest = r.route_net("GND")
    assert rest == []
    # every search sees at most NEAR_GROUPS groups' pads (one cell per pad, both layers share it)
    assert all(len(s) <= rt.NEAR_GROUPS for s in seen)
    assert len(r._groups("GND")) == 1


def test_route_net_falls_back_to_every_group_when_near_ones_are_walled_in():
    r = _gnd_router()
    calls = []
    search = r._search

    def spy(net, src, targets, max_expand=rt.MAX_EXPAND):
        calls.append(len({ix for (_, ix, _) in targets}))
        if len(calls) == 1:  # the near groups: "no way through", not out of budget
            r.capped = False
            return None
        return search(net, src, targets, max_expand)

    r._search = spy
    t, v, rest = r.route_net("GND")
    assert rest == []
    assert calls[0] == rt.NEAR_GROUPS and calls[1] == 4  # second try aims at all four other groups


def test_capped_near_search_does_not_retry_every_group():
    r = _gnd_router()
    calls = []

    def spy(net, src, targets, max_expand=rt.MAX_EXPAND):
        calls.append(1)
        r.capped = True
        return None

    r._search = spy
    t, v, rest = r.route_net("GND")
    assert len(calls) == 1 and len(rest) == 4

"""QA 10-17, keyboard (live-1792279042608) and Pico handheld (live-1792280000691): the row layout kept the Pico with
its GPIO column facing the board edge, so every matrix/button line had to go around the module and the router sat
for minutes. face_pins turns such a part 180 degrees in its cell; the via check uses a spatial index; one search is
capped to the budget time left."""

import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE / "kicad_side"))
import router as rt  # noqa: E402
import rows  # noqa: E402

RULES = {"track": 0.3, "clearance": 0.2, "via_d": 0.6, "via_drill": 0.3, "edge_clearance": 0.5}


def test_module_turns_its_pin_column_towards_what_it_drives():
    # "U1": pins on its left side (x=0) in local coords; the keys it drives sit to its right
    parts = {
        "U1": {"box": (-1, -1, 18, 20), "pads": [(0, y, f"N{y}") for y in range(0, 20, 4)]},
        "SW": {
            "box": (-1, -1, 1, 1),
            "pads": [(0, 0, "N0"), (0, 0.5, "N4"), (0, 1, "N8"), (0, 1.5, "N12"), (0, 2, "N16")],
        },
        "J1": {"box": (-1, -1, 1, 1), "pads": [(0, 0, "N0")], "fixed": True},
    }
    at = {"U1": (5.0, 5.0, 0), "SW": (60.0, 10.0, 0), "J1": (0.0, 0.0, 0)}
    out = rows.face_pins(parts, at)
    assert out["U1"][2] == 180
    # same courtyard centre: the cell and the outline do not move
    b0, b1 = rows.rot_box(parts["U1"]["box"], 0), rows.rot_box(parts["U1"]["box"], 180)
    c0 = (5.0 + (b0[0] + b0[2]) / 2, 5.0 + (b0[1] + b0[3]) / 2)
    c1 = (out["U1"][0] + (b1[0] + b1[2]) / 2, out["U1"][1] + (b1[1] + b1[3]) / 2)
    assert abs(c0[0] - c1[0]) < 1e-3 and abs(c0[1] - c1[1]) < 1e-3
    assert out["J1"] == at["J1"]  # fixed parts never turn


def test_part_already_facing_stays():
    parts = {
        "U1": {"box": (-1, -1, 18, 20), "pads": [(17, y, f"N{y}") for y in range(0, 20, 4)]},
        "SW": {"box": (-1, -1, 1, 1), "pads": [(0, 0, "N0"), (0, 2, "N16")]},
    }
    at = {"U1": (5.0, 5.0, 0), "SW": (60.0, 10.0, 0)}
    assert rows.face_pins(parts, at)["U1"] == at["U1"]


def _pads():
    pads = []
    for i in range(12):
        for j in range(6):
            x, y = 3 + i * 3.0, 3 + j * 4.0
            pads.append(
                {
                    "ref": f"P{i}_{j}",
                    "num": "1",
                    "net": f"N{j}",
                    "x": x,
                    "y": y,
                    "rect": [x - 0.6, y - 0.6, x + 0.6, y + 0.6],
                    "layers": ["F.Cu", "B.Cu"],
                }
            )
    return pads


def test_via_index_matches_full_scan():
    r = rt.Router(42, 30, RULES, _pads())
    for n in ("N0", "N3"):
        r.route_net(n)
    for net in range(len(r.nets)):
        for ix in range(0, r.nx, 7):
            for iy in range(0, r.ny, 5):
                r._via_cache.clear()
                fast = r.via_ok(net, ix, iy)
                full = (
                    rt.Router.via_ok.__wrapped__(r, net, ix, iy)
                    if hasattr(rt.Router.via_ok, "__wrapped__")
                    else _scan(r, net, ix, iy)
                )
                assert fast == full


def _scan(r, net, ix, iy):
    import math

    x, y = r.cell_xy(ix, iy)
    rv = r.via_d / 2
    if not (x - rv >= r.edge and y - rv >= r.edge and x + rv <= r.w - r.edge and y + rv <= r.h - r.edge):
        return False
    if r.idx(ix, iy) in r.no_via:
        return False
    for n, _l, kind, g in r.items:
        if kind == "rect" and rt._rect_dist(x, y, g) < r._clr(n, net) + rv:
            return False
        if kind == "seg" and n != net and rt._seg_dist(x, y, *g[:4]) < r._clr(n, net) + rv + g[4]:
            return False
        if kind == "via" and math.hypot(x - g[0], y - g[1]) < rv + g[2] + (r._clr(n, net) if n != net else 0.3):
            return False
    return True


def test_one_search_is_capped_by_the_time_left():
    seen = []

    class R:
        nets, tracks, vias, capped = ["A"], [], [], False

        def route_net(self, n, max_expand=rt.MAX_EXPAND):
            seen.append(max_expand)
            return [], [], []

        def _groups(self, n):
            return [0]

    ticks = iter([0.0] + [1.5] * 10)  # start, then 1.5 s gone: 0.5 s left for the search
    rt.route_all(lambda: R(), ["A"], budget_s=2.0, clock=lambda: next(ticks))
    assert seen[0] <= int(0.5 * rt.EXPANSIONS_PER_S) + 1


def test_retry_keeps_copper_away_from_the_failure():
    r = rt.Router(42, 30, RULES, _pads())
    base = (0, 0)
    for n in ("N0", "N5"):
        r.route_net(n)
    stuck = {"N5": [p for p in r.pads if p["net"] == "N5"][-1:]}
    keep_t, _ = rt._keep_away((1, r, ["N5"], [], base, stuck), 1.0)
    nets = {t["net"] for t in keep_t}
    assert "N5" not in nets and "N0" in nets

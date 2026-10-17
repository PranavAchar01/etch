"""Greedy placer: put each part where its pads land closest to already-placed pads of the
same nets, without courtyard overlap. Python 3.9 compatible. Units mm, y down."""

import math


def rot_pt(x, y, deg):
    """KiCad orientation: positive angle is counter-clockwise on screen (y down)."""
    a = math.radians(deg)
    return x * math.cos(a) + y * math.sin(a), -x * math.sin(a) + y * math.cos(a)


def rot_box(box, deg):
    xs, ys = [], []
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])):
        rx, ry = rot_pt(x, y, deg)
        xs.append(rx)
        ys.append(ry)
    return (min(xs), min(ys), max(xs), max(ys))


def _overlap(a, b, gap):
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _density(pts, w, h, cell):
    """Summed-area table of points per cell: rect(x0, y0, x1, y1) counts the points inside in O(1)."""
    nx, ny = int(w / cell) + 2, int(h / cell) + 2
    sat = [[0] * (nx + 1) for _ in range(ny + 1)]
    for x, y in pts:
        i, j = int(x / cell), int(y / cell)
        if 0 <= i < nx and 0 <= j < ny:
            sat[j + 1][i + 1] += 1
    for j in range(1, ny + 1):
        row, prev = sat[j], sat[j - 1]
        for i in range(1, nx + 1):
            row[i] += row[i - 1] + prev[i] - prev[i - 1]

    def rect(x0, y0, x1, y1):
        i0, j0 = max(0, min(nx, int(x0 / cell))), max(0, min(ny, int(y0 / cell)))
        i1, j1 = max(0, min(nx, int(x1 / cell) + 1)), max(0, min(ny, int(y1 / cell) + 1))
        return sat[j1][i1] - sat[j0][i1] - sat[j1][i0] + sat[j0][i0]

    return rect


def place(parts, w, h, gap=1.0, edge=0.6, step=0.5, avoid=None, avoid_cost=3.0):
    """parts: dicts with ref, box (local courtyard bbox), pads [(dx, dy, net)], optional at=[x,y,rot].
    Returns {ref: (x, y, rot)}. Parts with `at` are kept where they are. avoid: points (kept copper, sampled)
    that a new part should not sit on; each one under a candidate courtyard costs avoid_cost mm."""
    under = _density(avoid, w, h, step) if avoid else None
    placed = {}
    boxes = []
    net_pts = {}

    def commit(p, x, y, r):
        placed[p["ref"]] = (round(x, 3), round(y, 3), r)
        b = rot_box(p["box"], r)
        boxes.append((b[0] + x, b[1] + y, b[2] + x, b[3] + y))
        for dx, dy, net in p["pads"]:
            rx, ry = rot_pt(dx, dy, r)
            net_pts.setdefault(net, []).append((x + rx, y + ry))

    for p in parts:
        if p.get("at"):
            commit(p, p["at"][0], p["at"][1], int(p["at"][2]))
    todo = [p for p in parts if not p.get("at")]
    while todo:
        # most connections to what is already placed first, bigger parts break ties
        def key(p):
            links = sum(1 for _, _, n in p["pads"] if n in net_pts)
            area = (p["box"][2] - p["box"][0]) * (p["box"][3] - p["box"][1])
            return (-links, -area)

        todo.sort(key=key)
        p = todo.pop(0)
        best = None
        for r in (0, 90, 180, 270):
            b = rot_box(p["box"], r)
            x0 = edge - b[0]
            x1 = w - edge - b[2]
            y0 = edge - b[1]
            y1 = h - edge - b[3]
            if x1 < x0 or y1 < y0:
                continue
            edge_side = p.get("edge")
            if edge_side == "left":
                x1 = x0
            elif edge_side == "right":
                x0 = x1
            elif edge_side == "top":
                y1 = y0
            elif edge_side == "bottom":
                y0 = y1
            rp = [(rot_pt(dx, dy, r), net) for dx, dy, net in p["pads"]]
            nx = int((x1 - x0) / step) + 1
            ny = int((y1 - y0) / step) + 1
            for iy in range(ny):
                y = y0 + iy * step
                for ix in range(nx):
                    x = x0 + ix * step
                    cand = (b[0] + x, b[1] + y, b[2] + x, b[3] + y)
                    if any(_overlap(cand, o, gap) for o in boxes):
                        continue
                    cost = 0.0
                    for (rx, ry), net in rp:
                        pts = net_pts.get(net)
                        if pts:
                            cost += min(math.hypot(x + rx - qx, y + ry - qy) for qx, qy in pts)
                    cost += 0.05 * math.hypot(x - w / 2, y - h / 2) + 0.01 * (r != 0)
                    if under:
                        cost += avoid_cost * under(cand[0] - 0.6, cand[1] - 0.6, cand[2] + 0.6, cand[3] + 0.6)
                    if best is None or cost < best[0]:
                        best = (cost, x, y, r)
        if best is None:
            raise RuntimeError("no room on the board for %s; make the board bigger" % p["ref"])
        commit(p, best[1], best[2], best[3])
    return placed

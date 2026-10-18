"""Row placement: parts in function blocks, each block a grid of rows with one pitch, and the
board outline fitted around them with corner mounting holes. Pure Python 3.9 (runs under KiCad's
interpreter next to pcbnew). Units mm, y down.

layout (from the spec):
  margin: 2.0               parts area to board edge
  gap: 2.0                  between rows and between blocks
  hole: {r: 3.5, inset: 3.5}  corner M3 holes (keep-out radius, centre inset from both edges)
  blocks:
    - {name: power, valign: center, rows: [[J1]]}
    - {name: oscillator, pitch: 7.62, rows: [[D1, "@title", "@title", D2], [R2, R1, R3, R4], ...]}

A cell holds a reference, null (empty) or "@title" (the cells where the title silk goes).
Each part's courtyard is centred in its cell. Blocks sit left to right; valign center centres a
block on the tallest one. Then the outline is the parts' bounding box plus the margin, grown on
whichever side is cheaper until every corner hole clears every courtyard.
"""

import math

from placer import rot_box, rot_pt


def _cells(block, sizes, gap):
    rows = block["rows"]
    widths = [sizes[r][0] for row in rows for r in row if r and not str(r).startswith("@")]
    pitch = float(block.get("pitch") or (max(widths) + gap))
    ncol = max(len(row) for row in rows)
    bw = ncol * pitch
    out, titles, y = {}, [], 0.0
    for row in rows:
        refs = [r for r in row if r and not str(r).startswith("@")]
        rh = max([sizes[r][1] for r in refs] or [float(block.get("title_h", 5.0))])
        x0 = (bw - len(row) * pitch) / 2
        for j, r in enumerate(row):
            cx, cy = x0 + (j + 0.5) * pitch, y + rh / 2
            if r == "@title":
                titles.append((cx - pitch / 2, y, cx + pitch / 2, y + rh))
            elif r:
                out[r] = (cx, cy)
        y += rh + gap
    return out, titles, bw, y - gap


def _circle_hits_rect(cx, cy, r, b):
    nx = min(max(cx, b[0]), b[2])
    ny = min(max(cy, b[1]), b[3])
    return math.hypot(cx - nx, cy - ny) < r


def place_rows(layout, parts):
    """parts: {ref: {"box": local courtyard bbox, "rot": deg}}.
    Returns dict(at={ref: (x, y, rot)}, slots={ref: (cx, cy)}, w, h, holes, title_box)."""
    gap = float(layout.get("gap", 2.0))
    m = float(layout.get("margin", 2.0))
    sizes, rb = {}, {}
    for ref, p in parts.items():
        b = rot_box(p["box"], p["rot"])
        rb[ref] = b
        sizes[ref] = (b[2] - b[0], b[3] - b[1])
    blocks = []
    for blk in layout["blocks"]:
        blocks.append((blk,) + _cells(blk, sizes, gap))
    tallest = max(b[4] for b in blocks)
    centres, titles, x = {}, [], 0.0
    for blk, cells, tboxes, bw, bh in blocks:
        dy = (tallest - bh) / 2 if blk.get("valign") == "center" else 0.0
        for r, (cx, cy) in cells.items():
            centres[r] = (x + cx, dy + cy)
        titles += [(t[0] + x, t[1] + dy, t[2] + x, t[3] + dy) for t in tboxes]
        x += bw + gap
    missing = sorted(set(parts) - set(centres))
    if missing:
        raise RuntimeError("layout has no cell for " + ", ".join(missing))

    def court(r, ox, oy):
        cx, cy = centres[r]
        w, h = sizes[r]
        return (cx - w / 2 + ox, cy - h / 2 + oy, cx + w / 2 + ox, cy + h / 2 + oy)

    xs0 = min(centres[r][0] - sizes[r][0] / 2 for r in parts)
    ys0 = min(centres[r][1] - sizes[r][1] / 2 for r in parts)
    xs1 = max(centres[r][0] + sizes[r][0] / 2 for r in parts)
    ys1 = max(centres[r][1] + sizes[r][1] / 2 for r in parts)
    grow = {"l": 0.0, "r": 0.0, "t": 0.0, "b": 0.0}
    hole = layout.get("hole", {"r": 3.5, "inset": 3.5})
    hr, hi = float(hole.get("r", 3.5)) + 0.25, float(hole.get("inset", 3.5))

    def frame():
        ox, oy = m + grow["l"] - xs0, m + grow["t"] - ys0
        W = (xs1 - xs0) + 2 * m + grow["l"] + grow["r"]
        H = (ys1 - ys0) + 2 * m + grow["t"] + grow["b"]
        return ox, oy, W, H

    def corners(W, H):
        return {("l", "t"): (hi, hi), ("r", "t"): (W - hi, hi), ("l", "b"): (hi, H - hi), ("r", "b"): (W - hi, H - hi)}

    def clash(ox, oy, W, H, key):
        cx, cy = corners(W, H)[key]
        return any(_circle_hits_rect(cx, cy, hr, court(r, ox, oy)) for r in parts)

    if layout.get("holes", True):
        for _ in range(12):
            ox, oy, W, H = frame()
            bad = [k for k in corners(W, H) if clash(ox, oy, W, H, k)]
            if not bad:
                break
            sx, sy = bad[0]
            # smallest growth of the x side or the y side of that corner that clears it
            best = None
            for side in (sx, sy):
                base = grow[side]
                for k in range(1, 61):
                    grow[side] = base + 0.25 * k
                    if not clash(*frame(), bad[0]):
                        if best is None or k < best[1]:
                            best = (side, k)
                        break
                grow[side] = base
            if best is None:
                raise RuntimeError("no room for a mounting hole at corner " + "".join(bad[0]))
            grow[best[0]] += 0.25 * best[1]
    ox, oy, W, H = frame()
    # whole board on a 0.5 mm grid, the extra split evenly between opposite edges
    W2, H2 = math.ceil(W * 2 - 1e-6) / 2, math.ceil(H * 2 - 1e-6) / 2
    ox += (W2 - W) / 2
    oy += (H2 - H) / 2
    W, H = W2, H2
    at, slots = {}, {}
    for r, p in parts.items():
        cx, cy = centres[r][0] + ox, centres[r][1] + oy
        b = rb[r]
        slots[r] = (round(cx, 4), round(cy, 4))
        at[r] = (round(cx - (b[0] + b[2]) / 2, 4), round(cy - (b[1] + b[3]) / 2, 4), p["rot"])
    holes = [list(map(lambda v: round(v, 4), c)) for c in corners(W, H).values()] if layout.get("holes", True) else []
    tb = None
    if titles:
        tb = [
            min(t[0] for t in titles) + ox,
            min(t[1] for t in titles) + oy,
            max(t[2] for t in titles) + ox,
            max(t[3] for t in titles) + oy,
        ]
        tb = [round(v, 4) for v in tb]
    return {"at": at, "slots": slots, "w": W, "h": H, "holes": holes, "title_box": tb}


def face_pins(parts, at, min_gain=1.0):
    """Turn parts 180 degrees in their cell when that brings their pins closer to what they connect to.

    The row layout keeps each part at the rotation the spec gave it, so a Pico whose GPIO column faces the board
    edge sends every line around the module's ends: the 61-key keyboard and the Pico handheld (QA 10-17) sat in
    the router for minutes and left the ROW/COL and button nets unrouted. A half turn keeps the courtyard box, so
    the cell, the outline and the holes stay as they are.

    parts: {ref: {"box": local courtyard bbox, "pads": [(dx, dy, net)], "fixed": bool}}
    at: {ref: (x, y, rot)} from place_rows. Returns a new at. A part is turned only when the half-perimeter wire
    length of its nets drops by more than min_gain mm; "fixed" parts (edge connectors) are never turned."""
    at = dict(at)

    def pts(ref, x, y, r):
        out = []
        for dx, dy, n in parts[ref]["pads"]:
            rx, ry = rot_pt(dx, dy, r)
            out.append((x + rx, y + ry, n))
        return out

    world = {ref: pts(ref, *at[ref]) for ref in at if ref in parts}

    def hpwl(nets):
        tot = 0.0
        for n in nets:
            xy = [(x, y) for ps in world.values() for x, y, m in ps if m == n]
            if len(xy) > 1:
                xs, ys = [q[0] for q in xy], [q[1] for q in xy]
                tot += max(xs) - min(xs) + max(ys) - min(ys)
        return tot

    order = sorted((r for r in world if not parts[r].get("fixed")), key=lambda r: -len(parts[r]["pads"]))
    for ref in order:
        nets = {n for _x, _y, n in world[ref] if n}
        if not nets:
            continue
        x, y, r = at[ref]
        b = rot_box(parts[ref]["box"], r)
        cx, cy = x + (b[0] + b[2]) / 2, y + (b[1] + b[3]) / 2
        r2 = (int(r) + 180) % 360
        b2 = rot_box(parts[ref]["box"], r2)
        cand = (round(cx - (b2[0] + b2[2]) / 2, 4), round(cy - (b2[1] + b2[3]) / 2, 4), r2)
        before, kept = hpwl(nets), world[ref]
        world[ref] = pts(ref, *cand)
        if hpwl(nets) < before - min_gain:
            at[ref] = cand
        else:
            world[ref] = kept
    return at

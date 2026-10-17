"""Edit an existing KiCad board in place (an imported open-source project). Runs under KiCad's bundled Python.

    python3 edit_inplace.py inspect BOARD.kicad_pcb FACTS.json
    python3 edit_inplace.py apply   BOARD.kicad_pcb JOB.json RESULT.json

inspect writes what the planner and the plan check need: every footprint with its pads (number, net, pin
function, layers, position), the nets, the zones per net and the board's own rules.

apply changes only what the job names and keeps everything else the author made:
  - removes footprints (and the copper of nets that are left with a single pad),
  - changes values,
  - adds library footprints (with their STEP models) in the nearest free spot to an anchor: inside the board
    outline with edge clearance, clear of every courtyard on that side, clear of copper of other nets under its
    pads, outside rule areas,
  - puts pads on nets (new parts, and free pins of existing parts),
  - routes ONLY the new connections with the engine's grid router (router.py) in a window around them, on the
    outer layers with vias; a connection ends on existing copper of its net (pad, track, outer-layer pour, or a
    via into an inner-layer pour of that net). A connection with no way through stays a ratsnest line and is
    listed as unrouted: nothing is faked,
  - refills only the zones that touch the change, then saves.
Units are mm in board coordinates, y down.
"""

import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pcbnew  # noqa: E402

from placer import rot_box, rot_pt  # noqa: E402
from router import BLOCKED, FREE, LAYERS, Router, _rect_dist, _seg_dist  # noqa: E402

MM = pcbnew.FromMM
BUCKET = 2.0


def mm(v):
    return pcbnew.ToMM(v)


def V(x, y):
    return pcbnew.VECTOR2I(MM(x), MM(y))


def _box(bb):
    return (mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom()))


def _grow(b, d):
    return (b[0] - d, b[1] - d, b[2] + d, b[3] + d)


def _hit(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _union(a, b):
    if a is None:
        return b
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def copper_names(board):
    return [board.GetLayerName(lid) for lid in board.GetEnabledLayers().CuStack()]


def court(fp):
    """Courtyard box on the footprint's own side, else its bounding box without texts."""
    fp.BuildCourtyardCaches()
    poly = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
    if poly.OutlineCount():
        return _box(poly.BBox())
    return _box(fp.GetBoundingBox(False))


def pad_extra(pad, base):
    """How much more room than the board clearance this pad asks for (its own or its footprint's clearance)."""
    best = 0.0
    for v in (pad.GetLocalClearance(), pad.GetParentFootprint().GetLocalClearance() if pad.GetParentFootprint() else None):
        if v:
            best = max(best, mm(v) - base)
    return best


def is_tht(fp):
    return any(p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH) for p in fp.Pads())


def pad_layers(pad, cu):
    return [n for n, lid in cu if pad.IsOnLayer(lid)]


def part_record(board, fp, cu):
    pads = []
    for p in fp.Pads():
        pos = p.GetPosition()
        pads.append(
            {
                "num": p.GetNumber(),
                "net": p.GetNetname(),
                "fn": p.GetPinFunction(),
                "layers": pad_layers(p, cu),
                "x": round(mm(pos.x), 4),
                "y": round(mm(pos.y), 4),
            }
        )
    pos = fp.GetPosition()
    models = [m.m_Filename for m in fp.Models()]
    return {
        "ref": fp.GetReference(),
        "value": fp.GetValue(),
        "fpid": fp.GetFPIDAsString(),
        "x": round(mm(pos.x), 4),
        "y": round(mm(pos.y), 4),
        "rot": round(fp.GetOrientationDegrees(), 2),
        "side": "bottom" if fp.IsFlipped() else "top",
        "court": [round(v, 4) for v in court(fp)],
        "tht": is_tht(fp),
        "models": models,
        "pads": pads,
    }


def facts(board):
    cu = [(board.GetLayerName(lid), lid) for lid in board.GetEnabledLayers().CuStack()]
    parts = [part_record(board, fp, cu) for fp in board.GetFootprints()]
    nets = {}
    for p in parts:
        for q in p["pads"]:
            if q["net"]:
                nets.setdefault(q["net"], []).append(p["ref"] + "." + q["num"])
    zones = {}
    for z in board.Zones():
        if z.GetIsRuleArea() or not z.GetNetname():
            continue
        ls = [board.GetLayerName(lid) for lid in z.GetLayerSet().Seq() if pcbnew.IsCopperLayer(lid)]
        zones.setdefault(z.GetNetname(), set()).update(ls)
    ds = board.GetDesignSettings()
    nc = ds.m_NetSettings.GetDefaultNetclass()
    bb = board.GetBoardEdgesBoundingBox()
    return {
        "board": {
            "bbox": [round(v, 4) for v in _box(bb)],
            "w": round(mm(bb.GetWidth()), 3),
            "h": round(mm(bb.GetHeight()), 3),
            "copper": [c[0] for c in cu],
            "rules": {
                "clearance": round(mm(nc.GetClearance()), 4),
                "track": round(mm(nc.GetTrackWidth()), 4),
                "via_d": round(mm(nc.GetViaDiameter()), 4),
                "via_drill": round(mm(nc.GetViaDrill()), 4),
                "min_clearance": round(mm(ds.m_MinClearance), 4),
                "min_track": round(mm(ds.m_TrackMinWidth), 4),
                "edge_clearance": round(mm(ds.m_CopperEdgeClearance), 4),
            },
        },
        "parts": parts,
        "nets": nets,
        "zones": {k: sorted(v) for k, v in zones.items()},
        "tracks": len(board.GetTracks()),
    }


# ---------------------------------------------------------------- geometry of what is already on the board


class Grid:
    """Items bucketed by BUCKET-mm cells of their bounding box."""

    def __init__(self):
        self.cells = {}

    def add(self, box, item):
        for ix in range(int(math.floor(box[0] / BUCKET)), int(math.floor(box[2] / BUCKET)) + 1):
            for iy in range(int(math.floor(box[1] / BUCKET)), int(math.floor(box[3] / BUCKET)) + 1):
                self.cells.setdefault((ix, iy), []).append(item)

    def near(self, box):
        seen = set()
        for ix in range(int(math.floor(box[0] / BUCKET)), int(math.floor(box[2] / BUCKET)) + 1):
            for iy in range(int(math.floor(box[1] / BUCKET)), int(math.floor(box[3] / BUCKET)) + 1):
                for it in self.cells.get((ix, iy), ()):
                    if id(it) not in seen:
                        seen.add(id(it))
                        yield it


def _arc_points(t):
    s, m, e = t.GetStart(), t.GetMid(), t.GetEnd()
    return [(mm(s.x), mm(s.y)), (mm(m.x), mm(m.y)), (mm(e.x), mm(e.y))]


def copper_items(board, cu_names):
    """Every copper item as a dict: kind seg/circle/rect, geometry, net, layers. Zones are left out: they are
    refilled around anything new."""
    base = mm(board.GetDesignSettings().m_NetSettings.GetDefaultNetclass().GetClearance())
    lid = {board.GetLayerName(x): x for x in board.GetEnabledLayers().CuStack()}
    out = []
    for t in board.GetTracks():
        net = t.GetNetname()
        if t.GetClass() == "PCB_VIA":
            p = t.GetPosition()
            out.append({"kind": "circle", "c": (mm(p.x), mm(p.y)), "r": mm(t.GetWidth()) / 2,
                        "net": net, "layers": list(cu_names), "via": True})
            continue
        L = board.GetLayerName(t.GetLayer())
        pts = _arc_points(t) if t.GetClass() == "PCB_ARC" else [(mm(t.GetStart().x), mm(t.GetStart().y)),
                                                                  (mm(t.GetEnd().x), mm(t.GetEnd().y))]
        for a, b in zip(pts, pts[1:]):
            out.append({"kind": "seg", "a": a, "b": b, "half": mm(t.GetWidth()) / 2, "net": net, "layers": [L]})
    for fp in board.GetFootprints():
        for p in fp.Pads():
            ls = [n for n in cu_names if p.IsOnLayer(lid[n])]
            if not ls and p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH:
                continue
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                ls = list(cu_names)  # a bare hole: nothing may sit on it on any layer
            out.append({"kind": "rect", "r": _box(p.GetBoundingBox()), "net": p.GetNetname(), "layers": ls,
                        "ref": fp.GetReference(), "num": p.GetNumber(), "extra": pad_extra(p, base)})
    for d in board.GetDrawings():
        try:
            L = d.GetLayer()
        except Exception:
            continue
        if pcbnew.IsCopperLayer(L) and d.GetClass() in ("PCB_SHAPE", "PCB_TEXT", "PCB_TEXTBOX"):
            out.append({"kind": "rect", "r": _box(d.GetBoundingBox()), "net": getattr(d, "GetNetname", lambda: "")(),
                        "layers": [board.GetLayerName(L)]})
    return out


def item_box(it):
    if it["kind"] == "seg":
        a, b, h = it["a"], it["b"], it["half"]
        return (min(a[0], b[0]) - h, min(a[1], b[1]) - h, max(a[0], b[0]) + h, max(a[1], b[1]) + h)
    if it["kind"] == "circle":
        (x, y), r = it["c"], it["r"]
        return (x - r, y - r, x + r, y + r)
    return it["r"]


def rect_item_gap(r, it):
    """Distance from rect r to the copper of item it (0 when they overlap)."""
    if it["kind"] == "rect":
        o = it["r"]
        dx = max(o[0] - r[2], 0.0, r[0] - o[2])
        dy = max(o[1] - r[3], 0.0, r[1] - o[3])
        return math.hypot(dx, dy)
    if it["kind"] == "circle":
        return max(0.0, _rect_dist(it["c"][0], it["c"][1], r) - it["r"])
    a, b = it["a"], it["b"]
    if _seg_hits_rect(a, b, r):
        return 0.0
    corners = ((r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3]))
    d = min(min(_rect_dist(a[0], a[1], r), _rect_dist(b[0], b[1], r)),
            min(_seg_dist(cx, cy, a[0], a[1], b[0], b[1]) for cx, cy in corners))
    return max(0.0, d - it["half"])


def _seg_hits_rect(a, b, r):
    """Liang-Barsky clip: does segment a-b cross rect r?"""
    x0, y0 = a
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - r[0]), (dx, r[2] - x0), (-dy, y0 - r[1]), (dy, r[3] - y0)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


# ---------------------------------------------------------------- the board outline


class Outline:
    def __init__(self, board):
        self.poly = pcbnew.SHAPE_POLY_SET()
        self.ok = board.GetBoardPolygonOutlines(self.poly, True)
        self.vertices = []
        self.segs = []
        for i in range(self.poly.OutlineCount()):
            chains = [self.poly.Outline(i)] + [self.poly.Hole(i, h) for h in range(self.poly.HoleCount(i))]
            for ch in chains:
                pts = [(mm(ch.CPoint(k).x), mm(ch.CPoint(k).y)) for k in range(ch.PointCount())]
                self.vertices += pts
                self.segs += list(zip(pts, pts[1:] + pts[:1]))

    def inside(self, x, y):
        return self.poly.Contains(V(x, y))

    def rect_inside(self, r, edge):
        """r grown by edge lies inside the outline and no outline corner (a slot, a cut-out) is within it."""
        g = _grow(r, edge)
        n = max(2, int((g[2] - g[0]) / 0.5) + 1)
        m = max(2, int((g[3] - g[1]) / 0.5) + 1)
        pts = [(g[0] + (g[2] - g[0]) * i / (n - 1), y) for i in range(n) for y in (g[1], g[3])]
        pts += [(x, g[1] + (g[3] - g[1]) * j / (m - 1)) for j in range(m) for x in (g[0], g[2])]
        if not all(self.inside(x, y) for x, y in pts):
            return False
        return not any(g[0] < x < g[2] and g[1] < y < g[3] for x, y in self.vertices)


def keepout_areas(board):
    """Rule areas: (layers, box, zone) for anything that forbids footprints, pads, tracks or vias."""
    out = []
    for z in board.Zones():
        if not z.GetIsRuleArea():
            continue
        if not (z.GetDoNotAllowFootprints() or z.GetDoNotAllowPads() or z.GetDoNotAllowTracks()
                or z.GetDoNotAllowVias()):
            continue
        ls = [board.GetLayerName(lid) for lid in z.GetLayerSet().Seq() if pcbnew.IsCopperLayer(lid)]
        out.append({"layers": ls, "box": _box(z.GetBoundingBox()), "zone": z,
                    "tracks": bool(z.GetDoNotAllowTracks()), "vias": bool(z.GetDoNotAllowVias())})
    for fp in board.GetFootprints():
        for z in fp.Zones():
            if z.GetIsRuleArea() and (z.GetDoNotAllowTracks() or z.GetDoNotAllowVias() or z.GetDoNotAllowFootprints()):
                ls = [board.GetLayerName(lid) for lid in z.GetLayerSet().Seq() if pcbnew.IsCopperLayer(lid)]
                out.append({"layers": ls, "box": _box(z.GetBoundingBox()), "zone": z,
                            "tracks": bool(z.GetDoNotAllowTracks()), "vias": bool(z.GetDoNotAllowVias())})
    return out


def _in_zone(z, x, y):
    return z.Outline().Contains(V(x, y))


# ---------------------------------------------------------------- placing one new footprint


def load_fp(fp_root, fpid):
    lib, name = fpid.split(":", 1)
    fp = pcbnew.FootprintLoad(os.path.join(fp_root, lib + ".pretty"), name)
    if fp is None:
        raise RuntimeError("footprint not found: " + fpid)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def mod_area(fp_root, fpid):
    """Courtyard box area of a library footprint, read from its .kicad_mod text. (Loading many footprints only to
    measure them left pcbnew's SWIG wrappers returning untyped objects afterwards.)"""
    import re

    lib, name = fpid.split(":", 1)
    try:
        text = open(os.path.join(fp_root, lib + ".pretty", name + ".kicad_mod")).read()
    except OSError:
        return None
    xs, ys = [], []
    for blk in re.split(r"\(fp_", text)[1:]:
        if '"F.CrtYd"' not in blk[:2000]:
            continue
        for x, y in re.findall(r"\((?:start|end|xy|center|mid)\s+([-\d.]+)\s+([-\d.]+)", blk):
            xs.append(float(x))
            ys.append(float(y))
    return (max(xs) - min(xs)) * (max(ys) - min(ys)) if xs else None


def set_model(fp, path):
    olds = list(fp.Models())
    fp.Models().clear()
    for old in olds or [pcbnew.FP_3DMODEL()]:
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = path
        m.m_Scale, m.m_Rotation, m.m_Offset = old.m_Scale, old.m_Rotation, old.m_Offset
        m.m_Opacity, m.m_Show = old.m_Opacity, True
        fp.Models().push_back(m)


def flip(fp):
    try:
        fp.Flip(V(0, 0), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    except (AttributeError, TypeError):
        fp.Flip(V(0, 0), False)


def local_shape(fp, cu):
    """Courtyard and pad boxes relative to the footprint position, at orientation 0."""
    fp.SetPosition(V(0, 0))
    fp.SetOrientationDegrees(0)
    c = court(fp)
    pads = []
    for p in fp.Pads():
        ls = pad_layers(p, cu)
        if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            ls = [n for n, _ in cu]
        pads.append({"num": p.GetNumber(), "box": _box(p.GetBoundingBox()), "layers": ls,
                     "c": (mm(p.GetPosition().x), mm(p.GetPosition().y))})
    return c, pads


def hole_box(fp):
    """Box around a footprint's drilled pads: what a through-hole part takes up on its other side."""
    b = None
    for p in fp.Pads():
        if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            b = _union(b, _box(p.GetBoundingBox()))
    return _grow(b, 0.5) if b else None


class Placer:
    def __init__(self, board, outline, cu, rules, items):
        self.board, self.outline, self.cu, self.rules = board, outline, cu, rules
        self.cu_names = [n for n, _ in cu]
        self.courts = {"top": Grid(), "bottom": Grid()}
        for fp in board.GetFootprints():
            if len(fp.Pads()) == 0:
                continue  # logos and signs: silkscreen art, nothing a part cannot sit next to
            self.add_court(court(fp), "bottom" if fp.IsFlipped() else "top", hole_box(fp))
        self.copper = Grid()
        for it in items:
            self.copper.add(item_box(it), it)
        self.keepouts = keepout_areas(board)
        self.clear = max(rules["clearance"], rules["min_clearance"]) + 0.05
        self.edge = max(rules["edge_clearance"], 0.3) + 0.1
        self.bb = _box(board.GetBoardEdgesBoundingBox())  # read once: later SWIG calls for it can come back untyped

    def add_court(self, box, side, holes=None):
        self.courts[side].add(box, box)
        if holes:  # a through-hole part's leads come out on the other side
            other = "bottom" if side == "top" else "top"
            self.courts[other].add(holes, holes)

    def add_copper(self, items):
        for it in items:
            self.copper.add(item_box(it), it)

    def fits(self, cbox, pads, side, nets):
        g = _grow(cbox, 0.15)
        for o in self.courts[side].near(g):
            if _hit(g, o):
                return False
        for pd in pads:
            gb = _grow(pd["box"], self.clear + 2.0)
            for it in self.copper.near(gb):
                if it["net"] and it["net"] == nets.get(pd["num"]):
                    continue
                if not set(it["layers"]) & set(pd["layers"]):
                    continue
                if rect_item_gap(pd["box"], it) < self.clear + it.get("extra", 0.0):
                    return False
        if not self.outline.rect_inside(cbox, self.edge):
            return False
        for k in self.keepouts:
            if not _hit(k["box"], cbox):
                continue
            kb = k["box"]
            pts = [(cbox[0], cbox[1]), (cbox[2], cbox[1]), (cbox[2], cbox[3]), (cbox[0], cbox[3]),
                   ((cbox[0] + cbox[2]) / 2, (cbox[1] + cbox[3]) / 2)]
            if any(_in_zone(k["zone"], x, y) for x, y in pts):
                return False
            kc = ((kb[0] + kb[2]) / 2, (kb[1] + kb[3]) / 2)  # a keep-out smaller than the part
            if cbox[0] <= kc[0] <= cbox[2] and cbox[1] <= kc[1] <= cbox[3]:
                return False
        return True

    def place(self, fp, anchor, nets, toward=None, sides=("top", "bottom"), radius=None):
        """Nearest free spot to anchor (x, y) for footprint fp; toward: a point the pads should face (cost)."""

        R = radius or min(60.0, max(self.bb[2] - self.bb[0], self.bb[3] - self.bb[1]))
        step = 0.5
        n = int(R / step)
        offs = sorted(
            ((i * step, j * step) for i in range(-n, n + 1) for j in range(-n, n + 1)),
            key=lambda d: d[0] * d[0] + d[1] * d[1],
        )
        for side in sides:
            if side == "bottom":
                flip(fp)
            c0, pads0 = local_shape(fp, self.cu)
            cx0, cy0 = (c0[0] + c0[2]) / 2, (c0[1] + c0[3]) / 2
            shapes = {}
            for rot in (0, 90, 180, 270):
                cb = rot_box((c0[0] - cx0, c0[1] - cy0, c0[2] - cx0, c0[3] - cy0), rot)
                ps = []
                for pd in pads0:
                    b = rot_box((pd["box"][0] - cx0, pd["box"][1] - cy0, pd["box"][2] - cx0, pd["box"][3] - cy0), rot)
                    px, py = rot_pt(pd["c"][0] - cx0, pd["c"][1] - cy0, rot)
                    ps.append({"num": pd["num"], "box": b, "layers": pd["layers"], "c": (px, py)})
                shapes[rot] = (cb, ps, rot_pt(-cx0, -cy0, rot))
            best = None
            checked = 0
            for dx, dy in offs:
                d = math.hypot(dx, dy)
                if best and d > best[0] + 3.0:
                    break
                x, y = anchor[0] + dx, anchor[1] + dy
                if not self.outline.inside(x, y):
                    continue
                for rot, (cb, ps, _) in shapes.items():
                    checked += 1
                    cbox = (cb[0] + x, cb[1] + y, cb[2] + x, cb[3] + y)
                    pads = [{"num": p["num"], "layers": p["layers"],
                             "box": (p["box"][0] + x, p["box"][1] + y, p["box"][2] + x, p["box"][3] + y)} for p in ps]
                    if not self.fits(cbox, pads, side, nets):
                        continue
                    cost = d
                    if toward:
                        cost += 0.2 * min(math.hypot(x + p["c"][0] - toward[0], y + p["c"][1] - toward[1]) for p in ps)
                    cost += 0.05 * (rot % 180 != 0)
                    if best is None or cost < best[1]:
                        best = (d, cost, x, y, rot)
            if best:
                _, _, x, y, rot = best
                off = shapes[rot][2]
                fp.SetOrientationDegrees(rot)
                fp.SetPosition(V(x + off[0], y + off[1]))
                return {"side": side, "x": round(x, 3), "y": round(y, 3), "rot": rot, "checked": checked}
            if side == "bottom":
                flip(fp)
        return None


def place_ref(fp, placer, side):
    """The new part's reference on a free side of it (no pad, no other part, inside the board), else on the
    fab layer so the silkscreen never lands on copper."""
    t = fp.Reference()
    t.SetTextSize(pcbnew.VECTOR2I(MM(0.8), MM(0.8)))
    t.SetTextThickness(MM(0.12))
    t.SetTextAngleDegrees(0)
    c = court(fp)
    cx, cy = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
    outer = placer.cu_names[0] if side == "top" else placer.cu_names[-1]
    for x, y in ((cx, c[3] + 0.7), (cx, c[1] - 0.7), (c[0] - 1.6, cy), (c[2] + 1.6, cy)):
        t.SetPosition(V(x, y))
        bb = _grow(_box(t.GetBoundingBox()), 0.1)
        if not placer.outline.rect_inside(bb, 0.2):
            continue
        if any(_hit(bb, o) for o in placer.courts[side].near(bb)):
            continue
        if any(it["kind"] == "rect" and outer in it["layers"] and rect_item_gap(bb, it) < 0.15
               for it in placer.copper.near(_grow(bb, 0.5))):
            continue
        return True
    t.SetPosition(V(cx, cy))
    t.SetLayer(pcbnew.F_Fab if side == "top" else pcbnew.B_Fab)
    return False


# ---------------------------------------------------------------- routing the new connections


class LocalRouter(Router):
    """router.Router in a window of the board (origin at the window's corner), with a spatial index for the via
    check (the base class scans every item per via: fine on a 20-part board, far too slow on a real one)."""

    def __init__(self, *a, **k):
        self._vb = {}
        super().__init__(*a, **k)

    def _index(self):
        it = self.items[-1]
        n, _layers, kind, g = it
        if kind == "rect":
            b = g
        elif kind == "seg":
            b = (min(g[0], g[2]) - g[4], min(g[1], g[3]) - g[4], max(g[0], g[2]) + g[4], max(g[1], g[3]) + g[4])
        else:
            b = (g[0] - g[2], g[1] - g[2], g[0] + g[2], g[1] + g[2])
        for ix in range(int(b[0] // 1.0), int(b[2] // 1.0) + 1):
            for iy in range(int(b[1] // 1.0), int(b[3] // 1.0) + 1):
                self._vb.setdefault((ix, iy), []).append(it)

    def add_item(self, it):
        self.items.append(it)
        self._index()

    def _add_pad(self, p):
        super()._add_pad(p)
        self._index()

    def _mark_segment(self, net, L, x1, y1, x2, y2, half):
        super()._mark_segment(net, L, x1, y1, x2, y2, half)
        self._index()

    def _mark_via(self, net, x, y, d):
        super()._mark_via(net, x, y, d)
        self._index()

    def via_ok(self, net, ix, iy):
        key = (net, ix, iy)
        if key in self._via_cache:
            return self._via_cache[key]
        x, y = self.cell_xy(ix, iy)
        rv = self.via_d / 2
        ok = (x - rv >= self.edge and y - rv >= self.edge and x + rv <= self.w - self.edge
              and y + rv <= self.h - self.edge and self.idx(ix, iy) not in self.no_via)
        if ok:
            reach = self.clr + rv + 0.5
            seen = set()
            for bx in range(int((x - reach) // 1.0), int((x + reach) // 1.0) + 1):
                for by in range(int((y - reach) // 1.0), int((y + reach) // 1.0) + 1):
                    for it in self._vb.get((bx, by), ()):
                        if id(it) in seen:
                            continue
                        seen.add(id(it))
                        n, _l, kind, g = it
                        if kind == "rect":
                            if _rect_dist(x, y, g) < self.clr + rv:
                                ok = False
                        elif kind == "seg":
                            if n != net and _seg_dist(x, y, *g[:4]) < self.clr + rv + g[4]:
                                ok = False
                        elif math.hypot(x - g[0], y - g[1]) < rv + g[2] + (self.clr if n != net else 0.3):
                            ok = False
                        if not ok:
                            break
                    if not ok:
                        break
                if not ok:
                    break
        self._via_cache[key] = ok
        return ok


def _proj(px, py, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - a[0]) * vx + (py - a[1]) * vy) / L2))
    return a[0] + t * vx, a[1] + t * vy


def route_new(board, cu, outline, items, jobs, rules, grid=None, budget_s=120.0):
    """jobs: [{net, pads: [(ref, num)] new pads to join to the net's existing copper}]. Adds tracks and vias to
    the board. Returns (results, window box, counts)."""
    lid = {n: x for n, x in cu}
    outer = (cu[0][0], cu[-1][0])  # F.Cu, B.Cu
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}

    def pad_of(ref, num):
        fp = fps.get(ref)
        return next((p for p in fp.Pads() if p.GetNumber() == num), None) if fp else None

    # the window: every new pad plus the nearest existing copper of each net it joins
    win = None
    for j in jobs:
        for ref, num in j["pads"]:
            fp = fps.get(ref)
            for p in (fp.Pads() if fp else []):
                if p.GetNumber() == num:  # every copy of the pad
                    win = _union(win, _box(p.GetBoundingBox()))
    if win is None:
        return [], None, {"tracks": 0, "vias": 0}
    news = {(r, n) for j in jobs for r, n in j["pads"]}
    for j in jobs:
        c = [((it["r"][0] + it["r"][2]) / 2, (it["r"][1] + it["r"][3]) / 2) if it["kind"] == "rect"
             else it["a"] if it["kind"] == "seg" else it["c"]
             for it in items if it["net"] == j["net"] and (it.get("ref"), it.get("num")) not in news
             and set(it["layers"]) & set(outer)]  # inner-layer tracks cannot be reached, inner pours can
        for z in board.Zones():
            if not z.GetIsRuleArea() and z.GetNetname() == j["net"]:
                for L in z.GetLayerSet().Seq():
                    if pcbnew.IsCopperLayer(L) and z.HasFilledPolysForLayer(L):
                        fl = z.GetFilledPolysList(L)
                        for oi in range(fl.OutlineCount()):
                            ch = fl.Outline(oi)
                            c += [(mm(ch.CPoint(k).x), mm(ch.CPoint(k).y)) for k in range(0, ch.PointCount(), 4)]
        if c:
            mx, my = (win[0] + win[2]) / 2, (win[1] + win[3]) / 2
            nx, ny = min(c, key=lambda q: (q[0] - mx) ** 2 + (q[1] - my) ** 2)
            win = _union(win, (nx, ny, nx, ny))
    bb = _box(board.GetBoardEdgesBoundingBox())
    win = _grow(win, 4.0)
    win = (max(win[0], bb[0] - 0.5), max(win[1], bb[1] - 0.5), min(win[2], bb[2] + 0.5), min(win[3], bb[3] + 0.5))
    W, H = win[2] - win[0], win[3] - win[1]
    if grid is None:
        fine = rules["clearance"] < 0.18 or rules["track"] < 0.2  # dense boards need the fine grid to get anywhere
        grid = 0.1 if fine and W * H / 0.01 <= 160000 else 0.15 if fine and W * H / 0.0225 <= 260000 else 0.2
    ox, oy = win[0], win[1]

    def loc(x, y):
        return x - ox, y - oy

    clr = max(rules["clearance"], rules["min_clearance"]) + 0.03
    track = min(max(rules["track"], rules["min_track"], 0.15), 0.3)
    via_d = min(max(rules["via_d"], 0.45), 0.8)
    via_drill = min(max(rules["via_drill"], 0.2), via_d - 0.15)
    rr = {"track": track, "clearance": clr, "via_d": via_d, "via_drill": via_drill, "edge_clearance": 0.0,
          "via_cost": 6.0}

    # pads in the window, as router records (outer layers only)
    pads = []
    for fp in board.GetFootprints():
        if not _hit(_box(fp.GetBoundingBox(False)), _grow(win, 1.0)):
            continue
        for p in fp.Pads():
            b = _box(p.GetBoundingBox())
            if not _hit(b, win):
                continue
            ls = [L for L in outer if p.IsOnLayer(lid[L])]
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                ls = list(outer)
            if not ls:
                continue
            x, y = loc(mm(p.GetPosition().x), mm(p.GetPosition().y))
            e = pad_extra(p, rules["clearance"])
            pads.append({"ref": fp.GetReference(), "num": p.GetNumber(), "net": p.GetNetname(), "x": x, "y": y,
                         "rect": [b[0] - ox - e, b[1] - oy - e, b[2] - ox + e, b[3] - oy + e],
                         "layers": ["F.Cu" if L == outer[0] else "B.Cu" for L in ls]})
    # the router keeps two layers, F.Cu and B.Cu: map the board's outer copper onto them
    keep = []
    for k in keepout_areas(board):
        if not _hit(k["box"], win):
            continue
        ol = k["zone"].Outline().COutline(0)
        pts = [loc(mm(ol.CPoint(i).x), mm(ol.CPoint(i).y)) for i in range(ol.PointCount())]
        ls = ["F.Cu" if L == outer[0] else "B.Cu" for L in k["layers"] if L in outer]
        if ls and len(pts) >= 3:
            keep.append({"layers": ls, "pts": pts, "tracks": k["tracks"], "vias": k["vias"]})
    R = LocalRouter(W, H, rr, pads, pitch=grid, keepouts=keep)
    for it in items:  # nets that only have copper (no pad) in the window still need an id
        if it["net"] and it["net"] not in R.net_id:
            R.net_id[it["net"]] = len(R.nets)
            R.nets.append(it["net"])
    for j in jobs:
        if j["net"] not in R.net_id:
            R.net_id[j["net"]] = len(R.nets)
            R.nets.append(j["net"])
    tracks, vias = [], []
    for it in items:
        if not _hit(item_box(it), _grow(win, 1.0)):
            continue
        if it["kind"] == "seg":
            a, b = loc(*it["a"]), loc(*it["b"])
            if it["layers"][0] in outer:
                L = "F.Cu" if it["layers"][0] == outer[0] else "B.Cu"
                tracks.append({"net": it["net"], "layer": L, "width": it["half"] * 2, "pts": [list(a), list(b)]})
            else:  # inner-layer copper: no track is drawn there, but a via must keep clear of it
                R.add_item((R.net_id.get(it["net"], BLOCKED), set(), "seg", (a[0], a[1], b[0], b[1], it["half"])))
        elif it["kind"] == "circle":
            x, y = loc(*it["c"])
            vias.append({"net": it["net"], "x": x, "y": y, "d": it["r"] * 2, "drill": 0.3})
        elif it["kind"] == "rect" and not set(it["layers"]) & set(outer):
            r = it["r"]
            R.add_item((R.net_id.get(it["net"], BLOCKED), set(), "rect", (r[0] - ox, r[1] - oy, r[2] - ox, r[3] - oy)))
        elif it["kind"] == "rect" and it.get("ref") is None:  # copper graphics on an outer layer
            r = it["r"]
            g = (r[0] - ox, r[1] - oy, r[2] - ox, r[3] - oy)
            reach = R.clr + R.track / 2 + R.margin
            for ix, iy in R._cells_near(g[0], g[1], g[2], g[3], reach):
                x, y = R.cell_xy(ix, iy)
                if _rect_dist(x, y, g) < reach:
                    for L in (0, 1):
                        if outer[L] in it["layers"]:
                            R.own[L][R.idx(ix, iy)] = BLOCKED
            R.add_item((BLOCKED, set(), "rect", g))
    R.add_existing(tracks, vias)
    # a new track may end anywhere inside existing copper of its own net, even where the clearance map is closed
    for t in tracks:
        nid = R.net_id.get(t["net"])
        if nid is None:
            continue
        L = LAYERS.index(t["layer"])
        a, b = t["pts"]
        r = max(t["width"] / 2, grid * 0.75)
        for ix, iy in R._cells_near(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]), r):
            x, y = R.cell_xy(ix, iy)
            if _seg_dist(x, y, a[0], a[1], b[0], b[1]) <= r:
                R.padown[L][R.idx(ix, iy)] = nid
    for v in vias:
        nid = R.net_id.get(v["net"])
        if nid is not None:
            ix, iy = R.nearest(v["x"], v["y"])
            for L in (0, 1):
                R.padown[L][R.idx(ix, iy)] = nid
    # board edge: everything outside the outline, and the edge itself with its clearance, is closed
    edge = max(rules["edge_clearance"], 0.3)
    for (x1, y1), (x2, y2) in outline.segs:
        if _hit((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), _grow(win, edge + 1)):
            a, b = loc(x1, y1), loc(x2, y2)
            for L in (0, 1):
                R._mark_segment(BLOCKED, L, a[0], a[1], b[0], b[1], edge)
    for iy in range(R.ny):
        for ix in range(R.nx):
            x, y = R.cell_xy(ix, iy)
            if not outline.inside(x + ox, y + oy):
                i = R.idx(ix, iy)
                R.own[0][i] = BLOCKED
                R.own[1][i] = BLOCKED
                R.no_via.add(i)

    results = []
    t0 = time.time()
    n_tr, n_via = len(R.tracks), len(R.vias)
    # pads that are new copper for their net: the new parts' pads and newly wired free pins
    for j in sorted(jobs, key=lambda j: -len(j["pads"])):
        net = j["net"]
        nid = R.net_id[net]
        mask = R._mask(nid)
        targets = {}  # (L, ix, iy) -> how to finish there
        newset = set(j["pads"])
        for p in pads:
            if p["net"] == net and (p["ref"], p["num"]) not in newset:
                ix, iy = R.nearest(p["x"], p["y"])
                for L in p["layers"]:
                    targets[(LAYERS.index(L), ix, iy)] = ("pad", p)
        for t in R.tracks:
            if t["net"] != net:
                continue
            L = LAYERS.index(t["layer"])
            for a, b in zip(t["pts"], t["pts"][1:]):
                reach = max(t["width"] / 2 - grid * 0.5, 0.0) + grid * 0.75
                for ix, iy in R._cells_near(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]), reach):
                    x, y = R.cell_xy(ix, iy)
                    if _seg_dist(x, y, a[0], a[1], b[0], b[1]) <= reach and mask[L][R.idx(ix, iy)]:
                        targets.setdefault((L, ix, iy), ("track", (a, b)))
        for v in R.vias:
            if v["net"] == net:
                ix, iy = R.nearest(v["x"], v["y"])
                for L in (0, 1):
                    targets.setdefault((L, ix, iy), ("via", v))
        stitch = {}
        zone_cells(board, R, net, outer, lid, ox, oy, win, track, via_d, targets, stitch)
        done = set()
        # every copy of a pad: a 6 mm switch has two pads "1" and two pads "2", and each needs its own copper
        pending = []
        for rn in j["pads"]:
            inst = [q for q in pads if (q["ref"], q["num"]) == rn]
            if not inst:
                results.append({"net": net, "pin": f"{rn[0]}.{rn[1]}", "ok": False, "why": "pad outside the routing window"})
            for k, q in enumerate(inst):
                pending.append((rn, q, f"{rn[0]}.{rn[1]}" + (f"#{k + 1}" if len(inst) > 1 else "")))
        existing = any(it["net"] == net and (it.get("ref"), it.get("num")) not in newset for it in items) or any(
            z.GetNetname() == net and not z.GetIsRuleArea() for z in board.Zones())
        if not targets and existing:
            for rn, q, pin in pending:
                results.append({"net": net, "pin": pin, "ok": False,
                                "why": "no copper of the net within reach on an outer layer"})
            pending = []
        if not targets and pending:  # a net made only of new pads: grow it from its first pad
            rn, p, pin = pending.pop(0)
            ix, iy = R.nearest(p["x"], p["y"])
            for L in p["layers"]:
                targets[(LAYERS.index(L), ix, iy)] = ("pad", p)
            results.append({"net": net, "pin": pin, "ok": True, "how": "first pad"})
        while pending:
            rn, p, pin = pending.pop(0)
            ix, iy = R.nearest(p["x"], p["y"])
            src = {(LAYERS.index(L), ix, iy) for L in p["layers"]}
            hit = [s for s in src if s in targets]
            backup = False
            if pad_in_zone(board, net, rn, lid, (p["x"] + ox, p["y"] + oy)):
                how = "pour"
                near = {c: v for c, v in stitch.items() if len(p["layers"]) == 1 and c[0] == LAYERS.index(p["layers"][0])
                        and math.hypot(c[1] - ix, c[2] - iy) * R.g <= 3.0}
                if near:
                    path = R._search(nid, src, set(near), max_expand=30000)
                    if path:
                        R._commit(net, path, p, None, track)
                        cx, cy = R.cell_xy(path[-1][1], path[-1][2])
                        R._mark_via(nid, cx, cy, via_d)
                        R.vias.append({"net": net, "x": round(cx, 4), "y": round(cy, 4), "d": via_d, "drill": via_drill})
                        how = "pour + stitching via"
                path = None
                if how == "pour":
                    # the pour may not reach the pad once it is refilled around the new part (a starved thermal, a
                    # new island): a short track to other copper of the net as well, when there is one
                    alt = {c for c, v in targets.items() if v[0] != "zone"}
                    path = R._search(nid, src, alt, max_expand=80000) if alt else None
                if path is None:
                    results.append({"net": net, "pin": pin, "ok": True, "how": how})
                    for s_ in src:
                        targets[s_] = ("pad", p)
                    continue
                backup = True
            elif hit:
                results.append({"net": net, "pin": pin, "ok": True, "how": "touching"})
                continue
            elif time.time() - t0 > budget_s:
                results.append({"net": net, "pin": pin, "ok": False, "why": "routing time ran out"})
                continue
            else:
                path = R._search(nid, src, set(targets), max_expand=600000)
            if path is None:
                results.append({"net": net, "pin": pin, "ok": False,
                                "why": "no free way through" if not R.capped else "search limit reached"})
                continue
            kind, what = targets[path[-1]]
            end_pad = what if kind == "pad" else None
            new_t, new_v = R._commit(net, path, p, end_pad, track)
            L_end, ex, ey = path[-1]
            cx, cy = R.cell_xy(ex, ey)
            if kind == "track":
                px, py = _proj(cx, cy, what[0], what[1])
                if math.hypot(px - cx, py - cy) > 1e-4:
                    stub = {"net": net, "layer": LAYERS[L_end], "width": track,
                            "pts": [[round(cx, 4), round(cy, 4)], [round(px, 4), round(py, 4)]]}
                    R.tracks.append(stub)
                    new_t.append(stub)
            elif kind == "via-zone":
                v = {"net": net, "x": round(cx, 4), "y": round(cy, 4), "d": via_d, "drill": via_drill}
                R._mark_via(nid, cx, cy, via_d)
                R.vias.append(v)
                new_v.append(v)
            for t in new_t:  # the new copper is part of the net from now on
                L = LAYERS.index(t["layer"])
                for a, b in zip(t["pts"], t["pts"][1:]):
                    for ix2, iy2 in R._cells_near(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]), 0):
                        x, y = R.cell_xy(ix2, iy2)
                        if _seg_dist(x, y, a[0], a[1], b[0], b[1]) < 1e-6:
                            targets.setdefault((L, ix2, iy2), ("own", None))
            for s in src:
                targets[s] = ("pad", p)
            results.append({"net": net, "pin": pin, "ok": True, "how": ("pour + " if backup else "") + kind,
                            "tracks": len(new_t), "vias": len(new_v),
                            "length_mm": round(sum(math.hypot(b[0] - a[0], b[1] - a[1])
                                                   for t in new_t for a, b in zip(t["pts"], t["pts"][1:])), 2)})
            done.add(rn)
            mask = R._mask(nid)
    # write the new copper to the board
    netinfo = {}

    def net_of(n):
        if n not in netinfo:
            netinfo[n] = board.FindNet(n)
        return netinfo[n]

    for t in R.tracks[n_tr:]:
        for a, b in zip(t["pts"], t["pts"][1:]):
            tr = pcbnew.PCB_TRACK(board)
            tr.SetStart(V(a[0] + ox, a[1] + oy))
            tr.SetEnd(V(b[0] + ox, b[1] + oy))
            tr.SetWidth(MM(t["width"]))
            tr.SetLayer(lid[outer[0]] if t["layer"] == "F.Cu" else lid[outer[1]])
            tr.SetNet(net_of(t["net"]))
            board.Add(tr)
    for v in R.vias[n_via:]:
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(V(v["x"] + ox, v["y"] + oy))
        via.SetWidth(MM(v["d"]))
        via.SetDrill(MM(v["drill"]))
        via.SetNet(net_of(v["net"]))
        board.Add(via)
    return results, win, {"tracks": len(R.tracks) - n_tr, "vias": len(R.vias) - n_via, "grid": grid,
                          "window_mm": [round(W, 1), round(H, 1)]}


def pad_in_zone(board, net, rn, lid, at):
    """The new pad (the copy at board point `at`) sits inside an existing pour of its own net on one of its layers:
    the refill joins it."""
    fp = board.FindFootprintByReference(rn[0])
    pads = [p for p in fp.Pads() if p.GetNumber() == rn[1]] if fp else []
    pad = min(pads, key=lambda p: (mm(p.GetPosition().x) - at[0]) ** 2 + (mm(p.GetPosition().y) - at[1]) ** 2,
              default=None)
    if pad is None:
        return False
    pos = pad.GetPosition()
    for z in board.Zones():
        if z.GetIsRuleArea() or z.GetNetname() != net:
            continue
        for L in z.GetLayerSet().Seq():
            if pcbnew.IsCopperLayer(L) and pad.IsOnLayer(L) and z.HasFilledPolysForLayer(L):
                if z.GetFilledPolysList(L).Contains(pos):
                    return True
    return False


def zone_cells(board, R, net, outer, lid, ox, oy, win, track, via_d, targets, stitch=None):
    """Cells where a new track may end on a pour of the net: inside an outer-layer fill, or (with a via) above an
    inner-layer fill."""
    nid = R.net_id[net]
    mask = R._mask(nid)
    for z in board.Zones():
        if z.GetIsRuleArea() or z.GetNetname() != net:
            continue
        zb = _box(z.GetBoundingBox())
        if not _hit(zb, win):
            continue
        for L in z.GetLayerSet().Seq():
            if not pcbnew.IsCopperLayer(L) or not z.HasFilledPolysForLayer(L):
                continue
            fl = z.GetFilledPolysList(L)
            name = board.GetLayerName(L)
            inner = name not in outer
            m = (via_d / 2 + 0.15) if inner else (track / 2 + 0.1)
            mv = via_d / 2 + 0.15
            x0, y0 = max(zb[0], win[0]) - ox, max(zb[1], win[1]) - oy
            x1, y1 = min(zb[2], win[2]) - ox, min(zb[3], win[3]) - oy
            step = 2 if R.g < 0.15 else 1  # every other cell on a fine grid is plenty of targets
            for iy in range(max(0, int(y0 / R.g)), min(R.ny, int(y1 / R.g) + 1), step):
                for ix in range(max(0, int(x0 / R.g)), min(R.nx, int(x1 / R.g) + 1), step):
                    x, y = R.cell_xy(ix, iy)
                    Ls = (0, 1) if inner else (0 if name == outer[0] else 1,)
                    if not any(mask[Lr][R.idx(ix, iy)] for Lr in Ls):
                        continue
                    X, Y = x + ox, y + oy
                    if not all(fl.Contains(V(X + dx, Y + dy)) for dx, dy in ((0, 0), (m, 0), (-m, 0), (0, m), (0, -m))):
                        continue
                    if inner:
                        if not R.via_ok(nid, ix, iy):
                            continue
                        for Lr in Ls:
                            if mask[Lr][R.idx(ix, iy)]:
                                targets.setdefault((Lr, ix, iy), ("via-zone", name))
                    else:
                        targets.setdefault((Ls[0], ix, iy), ("zone", name))
                        # a via here lands in this pour: stitching for a new pad that sits in the pour of the
                        # other outer layer, so a refill that splits that pour still leaves it joined
                        other = 1 - Ls[0]
                        if stitch is not None and mask[other][R.idx(ix, iy)] and R.via_ok(nid, ix, iy) and all(
                                fl.Contains(V(X + dx, Y + dy)) for dx, dy in ((mv, 0), (-mv, 0), (0, mv), (0, -mv))):
                            stitch[(other, ix, iy)] = ("via-zone", name)


# ---------------------------------------------------------------- apply


def get_net(board, name):
    ni = board.FindNet(name)
    if ni is None:
        ni = pcbnew.NETINFO_ITEM(board, name)
        board.Add(ni)
    return ni


def refill(board, region):
    if region is None:
        return 0
    zs = pcbnew.ZONES()
    n = 0
    for z in board.Zones():
        if z.GetIsRuleArea():
            continue
        if _hit(_box(z.GetBoundingBox()), _grow(region, 1.0)):
            zs.push_back(z)
            n += 1
    if n:
        pcbnew.ZONE_FILLER(board).Fill(zs)
    return n


def apply(pcb_path, job_path, result_path):
    t0 = time.time()
    job = json.load(open(job_path))
    board = pcbnew.LoadBoard(pcb_path)
    cu = [(board.GetLayerName(lid), lid) for lid in board.GetEnabledLayers().CuStack()]
    cu_names = [n for n, _ in cu]
    f0 = facts(board)
    rules = f0["board"]["rules"]
    outline = Outline(board)
    region = None
    res = {"removed": [], "values": {}, "placed": {}, "not_placed": [], "assigned": [], "routes": [],
           "errors": []}

    # removals: the part and the copper of any net it leaves with a single pad
    for ref in job.get("remove", []):
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            res["errors"].append(f"{ref} is not on the board")
            continue
        region = _union(region, court(fp))
        nets = {p.GetNetname() for p in fp.Pads() if p.GetNetname()}
        board.Remove(fp)
        res["removed"].append(ref)
        for n in nets:
            left = sum(1 for f in board.GetFootprints() for p in f.Pads() if p.GetNetname() == n)
            if left <= 1:
                for t in list(board.GetTracks()):
                    if t.GetNetname() == n:
                        region = _union(region, _box(t.GetBoundingBox()))
                        board.Remove(t)
    for ref, v in (job.get("values") or {}).items():
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            res["errors"].append(f"{ref} is not on the board")
            continue
        res["values"][ref] = [fp.GetValue(), v]
        fp.SetValue(v)
    for a in job.get("assign", []):
        fp = board.FindFootprintByReference(a["ref"])
        pad = next((p for p in fp.Pads() if p.GetNumber() == a["pad"]), None) if fp else None
        if pad is None:
            res["errors"].append(f"{a['ref']}.{a['pad']} is not on the board")
            continue
        pad.SetNet(get_net(board, a["net"]))
        res["assigned"].append(f"{a['ref']}.{a['pad']}={a['net']}")
    t_edit = time.time()

    items = copper_items(board, cu_names)
    placer = Placer(board, outline, cu, rules, items)
    placed_at = {}
    kept_alive = []
    for a in job.get("add", []):
        nets = {k: v for k, v in (a.get("pads") or {}).items() if v}

        def make(c):
            f = load_fp(c["fp_root"], c["footprint"])
            f.SetReference(a["ref"])
            f.SetValue(a["value"])
            if c.get("model"):
                set_model(f, c["model"])
            for p in f.Pads():
                n = nets.get(p.GetNumber())
                if n:
                    p.SetNet(get_net(board, n))
            return f

        anchor = a.get("anchor")
        if a.get("near"):
            nf = board.FindFootprintByReference(a["near"])
            if nf is not None:
                c = court(nf)
                anchor = [(c[0] + c[2]) / 2, (c[1] + c[3]) / 2]
        # a part on a net made only of new parts (the resistor of an added LED) goes next to its partner
        fresh = set(job.get("new_nets") or [])
        partner_side = None
        for other, (px, py) in placed_at.items():
            theirs = set((next(x for x in job["add"] if x["ref"] == other).get("pads") or {}).values())
            if set(nets.values()) & theirs & fresh:
                anchor = [px, py]
                partner_side = res["placed"][other]["side"]
                break
        bb = f0["board"]["bbox"]
        if not anchor:
            anchor = [(bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2]
        toward = a.get("toward")
        side = partner_side or a.get("side")  # next to its partner means on the same side as it
        sides = ("top", "bottom") if side != "bottom" else ("bottom", "top")
        # the planned footprint first, then the same symbol in smaller footprints (same pads, same meaning)
        alts = []
        for c in a.get("alternatives") or []:
            area = mod_area(c["fp_root"], c["footprint"])
            if area:
                alts.append((area, c))
        cands = [a] + [c for _, c in sorted(alts, key=lambda t: t[0])][:6]
        # the planned footprint unless it lands far from where it belongs and a smaller one sits closer
        best = None  # (distance, got, fp, candidate)
        for c in cands:
            f = make(c)
            board.Add(f)
            g = placer.place(f, anchor, nets, toward=toward, sides=sides)
            board.Remove(f)
            kept_alive.append(f)  # a removed footprint freed here corrupts pcbnew's SWIG state for later calls
            if g:
                d = math.hypot(g["x"] - anchor[0], g["y"] - anchor[1])
                if best is None or d < best[0] - 2.0:
                    best = (d, g, f, c)
                if best[0] <= 8.0:
                    break
        got = fp = None
        if best:
            _, got, fp, c = best
            board.Add(fp)
            if c is not a:
                got["swapped_to"] = c["footprint"]
        if not got:
            res["not_placed"].append(a["ref"])
            continue
        cbox = court(fp)
        got["ref_on_silk"] = place_ref(fp, placer, got["side"])
        placer.add_court(cbox, got["side"], hole_box(fp))
        new_items = [it for it in copper_items_of(board, fp, cu, cu_names)]
        placer.add_copper(new_items)
        items += new_items
        placed_at[a["ref"]] = ((cbox[0] + cbox[2]) / 2, (cbox[1] + cbox[3]) / 2)
        region = _union(region, cbox)
        res["placed"][a["ref"]] = {**got, "court": [round(v, 3) for v in cbox], "anchor": anchor,
                                   "models": [m.m_Filename for m in fp.Models()]}
    t_place = time.time()

    jobs = {}
    for a in job.get("add", []):
        if a["ref"] not in res["placed"]:
            continue
        for num, net in (a.get("pads") or {}).items():
            if net:
                jobs.setdefault(net, []).append((a["ref"], num))
    for a in job.get("assign", []):
        jobs.setdefault(a["net"], []).append((a["ref"], a["pad"]))
    routed = {"tracks": 0, "vias": 0}
    if job.get("route", True) and jobs:
        rjobs = [{"net": n, "pads": ps} for n, ps in jobs.items()]
        results, win, routed = route_new(board, cu, outline, items, rjobs, rules, grid=job.get("grid"),
                                         budget_s=float(job.get("route_budget_s", 150)))
        res["routes"] = results
        if win:
            region = _union(region, win)
    t_route = time.time()
    res["zones_refilled"] = refill(board, region) if job.get("refill", True) else 0
    t_fill = time.time()
    pcbnew.SaveBoard(pcb_path, board)
    after = facts(pcbnew.LoadBoard(pcb_path))
    res.update(
        {
            "board": {"w": after["board"]["w"], "h": after["board"]["h"]},
            "new_tracks": routed.get("tracks", 0),
            "new_vias": routed.get("vias", 0),
            "route_info": routed,
            "region": [round(v, 3) for v in region] if region else None,
            "facts": after,
            "seconds": {
                "edit": round(t_edit - t0, 2),
                "place": round(t_place - t_edit, 2),
                "route": round(t_route - t_place, 2),
                "refill": round(t_fill - t_route, 2),
                "save": round(time.time() - t_fill, 2),
            },
        }
    )
    json.dump(res, open(result_path, "w"), indent=1)


def copper_items_of(board, fp, cu, cu_names):
    lid = dict(cu)
    out = []
    for p in fp.Pads():
        ls = [n for n in cu_names if p.IsOnLayer(lid[n])]
        if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            ls = list(cu_names)
        out.append({"kind": "rect", "r": _box(p.GetBoundingBox()), "net": p.GetNetname(), "layers": ls,
                    "ref": fp.GetReference(), "num": p.GetNumber(), "extra": 0.0})
    return out


def main(argv):
    if argv[0] == "inspect":
        board = pcbnew.LoadBoard(argv[1])
        # saved once by this KiCad, so every item has the uuid the DRC before / after comparison keys on (an older
        # file without uuids gets new random ones on every load)
        pcbnew.SaveBoard(argv[1], board)
        json.dump(facts(pcbnew.LoadBoard(argv[1])), open(argv[2], "w"), indent=1)
    elif argv[0] == "apply":
        apply(argv[1], argv[2], argv[3])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

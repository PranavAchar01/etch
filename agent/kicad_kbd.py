"""Keyboard board helper, run under KiCad's bundled Python (it has pcbnew). Prints JSON.

inspect  PCB                  footprints, pads, nets, models, outlines
assemble PCB OUT PLAN.json    copy PCB to OUT, add model-only footprints (switches, keycaps) from PLAN,
                              and apply model-path fixes; the design itself (copper, outline, footprints,
                              nets) is not changed
check    PCB                  outline containment, key count, switch-to-controller connectivity
"""

import json
import sys

import pcbnew

MM = 1e6


def mm(v):
    return round(v / MM, 4)


def fps_of(b):
    return list(b.GetFootprints())


def lib_of(f):
    return str(f.GetFPID().GetUniStringLibId())


def outlines(b):
    ol = pcbnew.SHAPE_POLY_SET()
    b.GetBoardPolygonOutlines(ol, True)
    return ol


def inspect(path):
    b = pcbnew.LoadBoard(path)
    out = []
    for f in fps_of(b):
        p = f.GetPosition()
        out.append(
            {
                "ref": f.GetReference(),
                "lib": lib_of(f),
                "value": f.GetValue(),
                "descr": f.GetLibDescription(),
                "tags": f.GetKeywords(),
                "x": mm(p.x),
                "y": mm(p.y),
                "rot": round(f.GetOrientationDegrees(), 2),
                "side": "bottom" if f.IsFlipped() else "top",
                "models": [m.m_Filename for m in f.Models()],
                "pads": [[pd.GetNumber(), pd.GetNetname()] for pd in f.Pads()],
            }
        )
    ol = outlines(b)
    polys = []
    for i in range(ol.OutlineCount()):
        bb = ol.Outline(i).BBox()
        polys.append(
            {
                "x": mm(bb.GetX()),
                "y": mm(bb.GetY()),
                "w": mm(bb.GetWidth()),
                "h": mm(bb.GetHeight()),
                "holes": ol.HoleCount(i),
            }
        )
    bb = b.GetBoardEdgesBoundingBox()
    return {
        "footprints": out,
        "outlines": polys,
        "board_bbox": [
            mm(bb.GetX()),
            mm(bb.GetY()),
            mm(bb.GetWidth()),
            mm(bb.GetHeight()),
        ],
        "nets": b.GetNetCount(),
        "tracks": len(list(b.GetTracks())),
        "layers": b.GetCopperLayerCount(),
    }


def assemble(path, out, plan_path):
    plan = json.load(open(plan_path))
    b = pcbnew.LoadBoard(path)
    fixed = 0
    fixes = plan.get("model_fixes", {})
    for f in fps_of(b):
        for m in f.Models():
            if m.m_Filename in fixes:
                m.m_Filename = fixes[m.m_Filename]
                fixed += 1
    added = 0
    for p in plan["parts"]:
        f = pcbnew.FOOTPRINT(b)
        f.SetReference(p["ref"])
        f.SetValue(p["value"])
        f.Reference().SetVisible(False)
        f.Value().SetVisible(False)
        f.SetAttributes(
            pcbnew.FP_SMD
            | pcbnew.FP_EXCLUDE_FROM_POS_FILES
            | pcbnew.FP_EXCLUDE_FROM_BOM
        )
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = p["model"]
        m.m_Offset = pcbnew.VECTOR3D(*p.get("offset", (0, 0, 0)))
        m.m_Rotation = pcbnew.VECTOR3D(*p.get("mrot", (0, 0, 0)))
        f.Models().push_back(m)
        b.Add(f)
        f.SetPosition(pcbnew.VECTOR2I(int(p["x"] * MM), int(p["y"] * MM)))
        f.SetOrientationDegrees(p.get("rot", 0))
        added += 1
    b.Save(out)
    return {"out": out, "added": added, "model_paths_fixed": fixed}


def check(path):
    b = pcbnew.LoadBoard(path)
    ol = outlines(b)
    outer = [ol.Outline(i) for i in range(ol.OutlineCount())]

    def in_outer(p):
        return any(c.PointInside(pcbnew.VECTOR2I(p.x, p.y)) for c in outer)

    cats = {"inside": [], "designed_cutout": [], "edge_mount": [], "panel_tab": [], "outside": []}
    for f in fps_of(b):
        lib = lib_of(f)
        if lib.startswith("etch:") or not lib:
            continue
        c = f.GetPosition()
        pts = [c] + [pd.GetPosition() for pd in f.Pads()]
        if "Breakaway_Tabs" in lib:
            k = "panel_tab"
        elif all(ol.Contains(pcbnew.VECTOR2I(p.x, p.y)) for p in pts):
            k = "inside"
        elif not in_outer(c):
            k = "outside"
        elif not ol.Contains(pcbnew.VECTOR2I(c.x, c.y)):
            k = "designed_cutout"
        else:
            k = "edge_mount"
        cats[k].append(f.GetReference())
    rows = sum(len(v) for v in cats.values())
    keys = [f for f in fps_of(b) if "keyswitch" in lib_of(f).lower()]
    plain = [f for f in keys if "rotary" not in lib_of(f).lower()]
    # matrix: a key is wired if one of its pad nets reaches a pad of the controller (the QFN-56)
    # directly or through one two-pad part
    mcu = [f for f in fps_of(b) if "QFN-56" in lib_of(f)]
    mcu_nets = {pd.GetNetname() for f in mcu for pd in f.Pads() if pd.GetNetname()}
    two = [f for f in fps_of(b) if len({pd.GetNetname() for pd in f.Pads()}) == 2]
    bridge = {}
    for f in two:
        a, c = list({pd.GetNetname() for pd in f.Pads()})
        bridge.setdefault(a, set()).add(c)
        bridge.setdefault(c, set()).add(a)

    def reaches(net):
        if not net:
            return False
        if net in mcu_nets:
            return True
        return any(n in mcu_nets for n in bridge.get(net, ()))

    wired = 0
    for f in plain:
        nets = [pd.GetNetname() for pd in f.Pads() if pd.GetNetname()]
        if len(nets) >= 2 and all(reaches(n) for n in set(nets)):
            wired += 1
    return {
        "footprints_checked": rows,
        "placement": {k: len(v) for k, v in cats.items()},
        "outside": cats["outside"],
        "examples": {k: v[:4] for k, v in cats.items() if k != "inside"},
        "outline_count": ol.OutlineCount(),
        "keys": len(plain),
        "encoder_alternates": len(keys) - len(plain),
        "controllers": [f.GetReference() for f in mcu],
        "keys_wired_to_controller": wired,
    }


def case_scene(out, paths):
    """A scene for the sourced case STEP files: a 4 mm square board under each case floor,
    the two halves side by side, centred, bottoms on the board top. Only for a top-down picture of the
    case; it is not a circuit board."""
    import re

    b = pcbnew.BOARD()
    placed = []
    gap = 12.0
    boxes = []
    for p in paths:
        t = open(p, errors="ignore").read()
        cp = {
            m[0]: [float(m[1]), float(m[2]), float(m[3])]
            for m in re.findall(
                r"#(\d+)\s*=\s*CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*([-\d.E+]+)\s*,\s*([-\d.E+]+)\s*,\s*([-\d.E+]+)", t
            )
        }
        q = [cp[i] for i in re.findall(r"VERTEX_POINT\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*\)", t) if i in cp]
        boxes.append([[min(r[i] for r in q) for i in range(3)], [max(r[i] for r in q) for i in range(3)]])
    x = -(sum(bx[1][0] - bx[0][0] for bx in boxes) + gap * (len(boxes) - 1)) / 2
    for p, (mn, mx_) in zip(paths, boxes):
        w = mx_[0] - mn[0]
        cx = x + w / 2
        f = pcbnew.FOOTPRINT(b)
        f.SetReference("CASE%d" % len(placed))
        f.Reference().SetVisible(False)
        f.Value().SetVisible(False)
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = p
        m.m_Offset = pcbnew.VECTOR3D(-(mn[0] + mx_[0]) / 2, -(mn[1] + mx_[1]) / 2, -mn[2])
        f.Models().push_back(m)
        b.Add(f)
        f.SetPosition(pcbnew.VECTOR2I(int(cx * MM), 0))
        placed.append({"file": p, "x": round(cx, 2), "size": [round(mx_[i] - mn[i], 2) for i in range(3)]})
        x += w + gap
    # one 4 mm square under the middle of each case floor, so the scene bbox is centred on the pair
    for pl in placed:
        r = pcbnew.PCB_SHAPE(b)
        r.SetShape(pcbnew.SHAPE_T_RECT)
        r.SetStart(pcbnew.VECTOR2I(int((pl["x"] - 2) * MM), int(-2 * MM)))
        r.SetEnd(pcbnew.VECTOR2I(int((pl["x"] + 2) * MM), int(2 * MM)))
        r.SetLayer(pcbnew.Edge_Cuts)
        b.Add(r)
    b.Save(out)
    return {"out": out, "placed": placed}


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "inspect":
        r = inspect(sys.argv[2])
    elif cmd == "assemble":
        r = assemble(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "case_scene":
        r = case_scene(sys.argv[2], json.loads(sys.argv[3]))
    else:
        r = check(sys.argv[2])
    print("JSON:" + json.dumps(r))

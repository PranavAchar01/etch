"""Build a .kicad_pcb from a job file. Runs under KiCad's bundled Python (pcbnew).

usage: python3 layout.py job.json result.json
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pcbnew  # noqa: E402

from placer import place, rot_box  # noqa: E402
from rows import place_rows  # noqa: E402
from router import Router, net_order, route_all  # noqa: E402

MM = pcbnew.FromMM


def to_mm(v):
    return pcbnew.ToMM(v)


def vec(x, y):
    return pcbnew.VECTOR2I(MM(x), MM(y))


def footprint_keepouts(fps):
    """Rule areas that come with footprints (a module's antenna keep-out): polygons the router must avoid."""
    out = []
    for fp in fps.values():
        for z in fp.Zones():
            if not z.GetIsRuleArea() or not (z.GetDoNotAllowTracks() or z.GetDoNotAllowVias()):
                continue
            ls = z.GetLayerSet()
            layers = [n for n, lid in (("F.Cu", pcbnew.F_Cu), ("B.Cu", pcbnew.B_Cu)) if ls.Contains(lid)]
            ol = z.Outline().COutline(0)
            pts = [(to_mm(ol.CPoint(i).x), to_mm(ol.CPoint(i).y)) for i in range(ol.PointCount())]
            if layers and len(pts) >= 3:
                out.append(
                    {
                        "layers": layers,
                        "pts": pts,
                        "tracks": bool(z.GetDoNotAllowTracks()),
                        "vias": bool(z.GetDoNotAllowVias()),
                    }
                )
    return out


def load_fp(fp_root, fpid):
    lib, name = fpid.split(":", 1)
    fp = pcbnew.FootprintLoad(os.path.join(fp_root, lib + ".pretty"), name)
    if fp is None:
        raise RuntimeError("footprint not found: " + fpid)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def courtyard_box(board, fp):
    fp.SetPosition(vec(0, 0))
    fp.SetOrientationDegrees(0)
    fp.BuildCourtyardCaches()
    bb = None
    for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
        poly = fp.GetCourtyard(layer)
        if poly.OutlineCount():
            b = poly.BBox()
            bb = b if bb is None else bb.Merge(b) or bb
    if bb is None:
        bb = fp.GetBoundingBox(False)
    return (to_mm(bb.GetX()), to_mm(bb.GetY()), to_mm(bb.GetRight()), to_mm(bb.GetBottom()))


def pad_record(ref, pad):
    bb = pad.GetBoundingBox()
    layers = [L for L, lid in (("F.Cu", pcbnew.F_Cu), ("B.Cu", pcbnew.B_Cu)) if pad.IsOnLayer(lid)]
    return {
        "ref": ref,
        "num": pad.GetNumber(),
        "net": pad.GetNetname(),
        "x": round(to_mm(pad.GetPosition().x), 4),
        "y": round(to_mm(pad.GetPosition().y), 4),
        "rect": [to_mm(bb.GetX()), to_mm(bb.GetY()), to_mm(bb.GetRight()), to_mm(bb.GetBottom())],
        "layers": layers,
    }


def _box(bb):
    return (to_mm(bb.GetX()), to_mm(bb.GetY()), to_mm(bb.GetRight()), to_mm(bb.GetBottom()))


def _hit(a, b, gap=0.15):
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def place_refs(fps, W, H, size=1.0, margin=0.5, keep_out=()):
    """Move each reference label to a free side of its part so silk never lands on another
    part or the board edge. If no side is free, the label goes to the fab layer (not printed)."""
    court = {}
    for ref, fp in fps.items():
        fp.BuildCourtyardCaches()
        poly = fp.GetCourtyard(pcbnew.F_CrtYd)
        court[ref] = _box(poly.BBox()) if poly.OutlineCount() else _box(fp.GetBoundingBox(False))
    taken = [tuple(b) for b in keep_out]  # title block and mounting holes
    for ref, fp in fps.items():
        t = fp.Reference()
        t.SetTextSize(pcbnew.VECTOR2I(MM(size), MM(size)))
        t.SetTextThickness(MM(0.15))
        t.SetTextAngleDegrees(0)
        c = court[ref]
        cx, cy = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
        # below the part first: in the tilted product view the near side is never hidden by the body
        cands = [(cx, c[3] + size * 0.75), (cx, c[1] - size * 0.75), (c[0] - size * 1.2, cy), (c[2] + size * 1.2, cy)]
        if (c[3] - c[1]) > 1.5 * (c[2] - c[0]):
            # a standing-up part (resistor on its side in a row): label beside it, where the next
            # row's tall parts cannot hide it
            cands = cands[2:] + cands[:2]
        placed = False
        for x, y in cands:
            t.SetPosition(vec(x, y))
            bb = _box(t.GetBoundingBox())
            if bb[0] < margin or bb[1] < margin or bb[2] > W - margin or bb[3] > H - margin:
                continue
            if any(_hit(bb, court[o]) for o in court if o != ref) or any(_hit(bb, tb) for tb in taken):
                continue
            taken.append(bb)
            placed = True
            break
        if not placed:
            t.SetPosition(vec(cx, cy))
            t.SetLayer(pcbnew.F_Fab)


def _seg_hits_box(p, q, bx, pad):
    """True when segment p-q comes within pad of box bx (sampled every 0.1 mm, plenty at a 0.2 mm grid)."""
    x0, y0, x1, y1 = bx[0] - pad, bx[1] - pad, bx[2] + pad, bx[3] + pad
    n = max(1, int(((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5 / 0.1))
    for k in range(n + 1):
        x, y = p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n
        if x0 <= x <= x1 and y0 <= y <= y1:
            return True
    return False


def _clear_under(keep, boxes, clearance):
    """Kept copper minus every net that crosses a newly placed part (QA 10-17: a buzzer added to the voice arm
    landed on kept tracks and failed DRC with 12 shorts). Those nets are routed again from scratch."""
    if not boxes or not keep:
        return keep
    hit = set()
    for t in keep.get("tracks", []):
        pad = t.get("width", 0.25) / 2 + clearance + 0.5
        pts = t["pts"]
        if any(_seg_hits_box(a, b, bx, pad) for a, b in zip(pts, pts[1:]) for bx in boxes):
            hit.add(t["net"])
    for v in keep.get("vias", []):
        pad = v.get("d", 0.6) / 2 + clearance + 0.5
        if any(_seg_hits_box((v["x"], v["y"]), (v["x"], v["y"]), bx, pad) for bx in boxes):
            hit.add(v["net"])
    if not hit:
        return keep
    return {
        **keep,
        "tracks": [t for t in keep.get("tracks", []) if t["net"] not in hit],
        "vias": [v for v in keep.get("vias", []) if v["net"] not in hit],
    }


def hv_net_class(pcb_path, rules):
    """High-voltage nets (rules.hv_nets) get an "HV" net class at rules.hv_clearance (IPC-2221B B2), so the zone
    filler and DRC keep it, plus a project rule: a pin-pitch exception for pairs that involve a pad (a fine-pitch part's pins sit closer than the board gap; the router keeps the
    full gap everywhere but the fan-out next to a pin, and pours always keep it). Creepage is not a separate
    rule: on one uncut surface the creepage path is the clearance, and KiCad 10's creepage check ignores the
    pad exception (it flagged a 2.54 mm resistor's own two pins), so it would fail every such part."""
    nets, hv = rules.get("hv_nets") or [], float(rules.get("hv_clearance") or 0)
    if not nets or hv <= float(rules["clearance"]):
        return
    pro = Path(pcb_path).with_suffix(".kicad_pro")
    d = json.loads(pro.read_text())
    ns = d.setdefault("net_settings", {})
    base = next(c for c in ns["classes"] if c["name"] == "Default")
    ns["classes"] = [c for c in ns["classes"] if c["name"] != "HV"] + [{**base, "name": "HV", "clearance": hv, "priority": 0}]
    ns["netclass_patterns"] = [p for p in ns.get("netclass_patterns") or [] if p.get("netclass") != "HV"] + [
        {"netclass": "HV", "pattern": n} for n in nets
    ]
    pro.write_text(json.dumps(d, indent=2) + "\n")
    lo = f"{float(rules['clearance']):g}mm"
    Path(pcb_path).with_suffix(".kicad_dru").write_text(
        "(version 1)\n"
        f'(rule "HV pin pitch" (constraint clearance (min {lo}))\n'
        "  (condition \"(A.NetClass == 'HV' || B.NetClass == 'HV') && (A.Type == 'Pad' || B.Type == 'Pad')"
        " && A.Type != 'Zone' && B.Type != 'Zone'\"))\n"
    )


def main(job_path, result_path):
    t0 = time.time()
    job = json.load(open(job_path))
    lay_cfg = job.get("layout")
    base_lay = job.get("base_layout")
    if base_lay:
        W, H = base_lay["w"], base_lay["h"]
    elif lay_cfg:
        W, H = None, None  # fitted to the parts below
    else:
        W, H = job["board"]["w"], job["board"]["h"]
    rules = job["rules"]
    board = pcbnew.NewBoard(job["out_pcb"])
    ds = board.GetDesignSettings()
    ds.SetBoardThickness(MM(job["board"].get("thickness", 1.6)))
    nc = ds.m_NetSettings.GetDefaultNetclass()
    nc.SetClearance(MM(rules["clearance"]))
    nc.SetTrackWidth(MM(rules["track"]))
    nc.SetViaDiameter(MM(rules["via_d"]))
    nc.SetViaDrill(MM(rules["via_drill"]))
    ds.m_CopperEdgeClearance = MM(rules.get("edge_clearance", 0.5))

    nets = {}
    for p in job["parts"]:
        for n in p["pads"].values():
            if n and n not in nets:
                ni = pcbnew.NETINFO_ITEM(board, n)
                board.Add(ni)
                nets[n] = ni

    fps = {}
    place_in = []
    for p in job["parts"]:
        fp = load_fp(p.get("fp_root") or job["fp_root"], p["footprint"])
        fp.SetReference(p["ref"])
        fp.SetValue(p["value"])
        if p.get("model"):
            # absolute path to the real library STEP, so the export never depends on KICAD*_3DMODEL_DIR
            # (iterating Models() yields copies in SWIG, so rebuild the list instead)
            olds = list(fp.Models())
            fp.Models().clear()
            for old in olds or [pcbnew.FP_3DMODEL()]:
                m = pcbnew.FP_3DMODEL()
                m.m_Filename = p["model"]
                m.m_Scale, m.m_Rotation, m.m_Offset = old.m_Scale, old.m_Rotation, old.m_Offset
                m.m_Opacity, m.m_Show = old.m_Opacity, True
                fp.Models().push_back(m)
        if p.get("sym_uuid"):
            fp.SetPath(pcbnew.KIID_PATH("/" + p["sym_uuid"]))
            fp.SetSheetname("Root")
            fp.SetSheetfile(os.path.basename(job["out_pcb"]).replace(".kicad_pcb", ".kicad_sch"))
        board.Add(fp)
        box = courtyard_box(board, fp)
        local_pads = []
        for pad in fp.Pads():
            net = p["pads"].get(pad.GetNumber(), "")
            if net:
                pad.SetNet(nets[net])
            local_pads.append((to_mm(pad.GetPosition().x), to_mm(pad.GetPosition().y), net))
        fps[p["ref"]] = fp
        place_in.append(
            {
                "ref": p["ref"],
                "box": box,
                "pads": [q for q in local_pads if q[2]],
                "at": p.get("at"),
                "edge": p.get("edge"),
                "rot": int(p.get("rot") or 0),
            }
        )
    t_place = time.time()
    rows_res = None
    fresh_boxes = []  # courtyards of parts new to an incremental board: kept copper under them is ripped up
    if base_lay:
        # incremental: same outline, kept parts where they were, a replaced part centred in its old slot
        placement = {}
        fresh = []  # parts the base board never had: placed into free space next to what they connect to
        for q in place_in:
            if q["at"]:
                placement[q["ref"]] = tuple(q["at"])
            elif q["ref"] in base_lay["slots"]:
                sx, sy = base_lay["slots"][q["ref"]]
                b = rot_box(q["box"], q["rot"])
                placement[q["ref"]] = (round(sx - (b[0] + b[2]) / 2, 4), round(sy - (b[1] + b[3]) / 2, 4), q["rot"])
            else:
                fresh.append(q)
        if fresh:
            hr = float((lay_cfg or {}).get("hole", {}).get("r", 3.5))
            blocks = [(base_lay.get("title_box"))] if base_lay.get("title_box") else []
            blocks += [(x - hr, y - hr, x + hr, y + hr) for x, y in base_lay.get("holes") or []]
            fixed = [
                {"ref": f"__keepout{i}", "box": tuple(bx), "pads": [], "at": [0, 0, 0]} for i, bx in enumerate(blocks)
            ]
            fixed += [{**q, "at": list(placement[q["ref"]])} for q in place_in if q["ref"] in placement]
            copper = []  # kept tracks sampled every 0.5 mm, so new parts prefer bare board
            for t in (job.get("keep") or {}).get("tracks", []):
                for (x0, y0), (x1, y1) in zip(t["pts"], t["pts"][1:]):
                    n = max(1, int(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / 0.5))
                    copper += [(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) for k in range(n + 1)]
            copper += [(v["x"], v["y"]) for v in (job.get("keep") or {}).get("vias", [])]
            got = place(fixed + [{**q, "at": None} for q in fresh], W, H, gap=0.8, edge=1.0, avoid=copper)
            for q in fresh:
                placement[q["ref"]] = got[q["ref"]]
                x, y, r = got[q["ref"]]
                b = rot_box(q["box"], r)
                fresh_boxes.append((x + b[0], y + b[1], x + b[2], y + b[3]))
        rows_res = {k: base_lay[k] for k in ("holes", "title_box", "slots")}
        rows_res["slots"] = dict(rows_res["slots"])
        for ref, (x, y, r) in placement.items():
            if ref not in rows_res["slots"]:
                b = rot_box(next(q["box"] for q in place_in if q["ref"] == ref), r)
                rows_res["slots"][ref] = (x + (b[0] + b[2]) / 2, y + (b[1] + b[3]) / 2)
    elif lay_cfg:
        rows_res = place_rows(lay_cfg, {q["ref"]: {"box": q["box"], "rot": q["rot"]} for q in place_in})
        W, H = rows_res["w"], rows_res["h"]
        placement = rows_res["at"]
    else:
        placement = place(place_in, W, H, gap=job.get("place_gap", 1.0))
    for ref, (x, y, r) in placement.items():
        fp = fps[ref]
        fp.SetOrientationDegrees(r)
        fp.SetPosition(vec(x, y))
    t_route = time.time()
    # outline
    corners = [(0, 0), (W, 0), (W, H), (0, H)]
    for (x1, y1), (x2, y2) in zip(corners, corners[1:] + corners[:1]):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(MM(0.1))
        s.SetStart(vec(x1, y1))
        s.SetEnd(vec(x2, y2))
        board.Add(s)

    keep_out = []
    if rows_res:
        if rows_res.get("title_box"):
            keep_out.append(rows_res["title_box"])
        hr = float((lay_cfg or {}).get("hole", {}).get("r", 3.5))
        keep_out += [(x - hr, y - hr, x + hr, y + hr) for x, y in rows_res["holes"]]
    place_refs(fps, W, H, keep_out=keep_out)
    pads = []
    for ref, fp in fps.items():
        for pad in fp.Pads():
            pads.append(pad_record(ref, pad))
    keepouts = footprint_keepouts(fps)
    keep = _clear_under(job.get("keep") or {}, fresh_boxes, float(rules["clearance"]))

    def make_router():
        r = Router(W, H, rules, pads, pitch=job.get("grid", 0.2), keepouts=keepouts)
        r.add_existing(keep.get("tracks", []), keep.get("vias", []))
        return r

    # rip-up and retry; the best attempt is kept (never an empty board after a failed last attempt), and the
    # wall-clock budget (set by build.py under the kicad_python timeout) stops routing with the copper so far kept
    budget = job.get("route_budget_s")
    order = net_order(pads, sorted({p["net"] for p in pads if p["net"]}))
    router, route_info = route_all(make_router, order, budget_s=float(budget) if budget else None)
    route_info["budget_s"] = budget
    new_tracks, new_vias = route_info.pop("new_tracks"), route_info.pop("new_vias")
    unrouted = []
    for n in router.nets:
        groups = router._groups(n)
        if len(groups) > 1:
            unrouted.append(n)
    t_done = time.time()

    for t in router.tracks:
        for a, b in zip(t["pts"], t["pts"][1:]):
            tr = pcbnew.PCB_TRACK(board)
            tr.SetStart(vec(*a))
            tr.SetEnd(vec(*b))
            tr.SetWidth(MM(t["width"]))
            tr.SetLayer(pcbnew.F_Cu if t["layer"] == "F.Cu" else pcbnew.B_Cu)
            tr.SetNet(nets[t["net"]])
            board.Add(tr)
    for v in router.vias:
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(vec(v["x"], v["y"]))
        via.SetWidth(MM(v["d"]))
        via.SetDrill(MM(v["drill"]))
        via.SetNet(nets[v["net"]])
        board.Add(via)

    if job.get("title"):
        txt = pcbnew.PCB_TEXT(board)
        txt.SetText(job["title"])
        txt.SetLayer(pcbnew.B_SilkS)
        txt.SetPosition(vec(W / 2, H - 1.6))
        txt.SetTextSize(pcbnew.VECTOR2I(MM(1.0), MM(1.0)))
        txt.SetTextThickness(MM(0.15))
        txt.SetMirrored(True)
        board.Add(txt)

    pcbnew.SaveBoard(job["out_pcb"], board)
    hv_net_class(job["out_pcb"], rules)
    parts_out = {}
    for ref, fp in fps.items():
        fp.BuildCourtyardCaches()
        cy = fp.GetCourtyard(pcbnew.F_CrtYd)
        cb = _box(cy.BBox()) if cy.OutlineCount() else _box(fp.GetBoundingBox(False))
        parts_out[ref] = {
            "court": [round(v, 4) for v in cb],
            "x": round(to_mm(fp.GetPosition().x), 4),
            "y": round(to_mm(fp.GetPosition().y), 4),
            "rot": round(fp.GetOrientationDegrees(), 3),
            "side": "bottom" if fp.IsFlipped() else "top",
        }
    res = {
        "board": {"w": W, "h": H},
        "holes": rows_res["holes"] if rows_res else [],
        "title_box": rows_res["title_box"] if rows_res else None,
        "slots": rows_res["slots"] if rows_res else {},
        "parts": parts_out,
        "pads": pads,
        "tracks": router.tracks,
        "vias": router.vias,
        "new_tracks": new_tracks,
        "new_vias": new_vias,
        "unrouted": unrouted,
        "route": route_info,
        "timing_s": {
            "load": round(t_place - t0, 3),
            "place": round(t_route - t_place, 3),
            "route": round(t_done - t_route, 3),
            "save": round(time.time() - t_done, 3),
        },
    }
    json.dump(res, open(result_path, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

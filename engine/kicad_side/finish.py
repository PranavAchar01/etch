"""Finish a placed and routed board the way the reference template does it.

    python3 finish.py board.kicad_pcb finish.json

Edits the .kicad_pcb s-expression text directly (the pcbnew SWIG layer in this KiCad build
crashes on zone and net-table calls). build.py then fills the pours with KiCad's own filler
(`kicad-cli pcb drc --refill-zones --save-board`) before any check runs.

finish.json keys (all optional):
  outline: {corner_r: 3.0}           rounded-rectangle Edge.Cuts at 0..W x 0..H (replaces the square)
  pour: {net: GND, layers: [F.Cu, B.Cu], clearance: 0.3, min_width: 0.25}
  holes: [[x, y], ...]               M3 mounting holes (MountingHole_3.2mm_M3 from the KiCad library)
  silk: [{text, x, y, size, layer}]  plain silkscreen text (title, credit)
  mask: Green                        solder mask colour in the board stackup
  copper_finish: ENIG                pad finish in the stackup (ENIG renders gold, HAL lead-free silver)
  title: {text, sub, size}           title and version silk, centred in the layout's title cells (title_box)
  stitch: {pitch: 5.08}              GND stitching vias on a grid wherever both pours have room (needs geometry)
  geometry: {pads, tracks, vias, courts, title_box}   from layout_result.json, for the stitching vias
"""

import json
import math
import os
import re
import sys
import uuid


def _uid(seed):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "etch-finish:" + seed))


def _strip_edge_lines(text):
    # layout.py draws the square outline as four gr_line items on Edge.Cuts
    pat = re.compile(r"\n\t\(gr_(?:line|arc)\n(?:\t\t.*\n)*?\t\t\(layer \"Edge\.Cuts\"\)\n(?:\t\t.*\n)*?\t\)")
    return pat.sub("", text)


def _stroke_tail(seed, layer="Edge.Cuts"):
    return f'\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n\t\t(layer "{layer}")\n\t\t(uuid "{_uid(seed)}")\n\t)\n'


def _line(a, b, seed):
    return f"\t(gr_line\n\t\t(start {a[0]:.4f} {a[1]:.4f})\n\t\t(end {b[0]:.4f} {b[1]:.4f})\n" + _stroke_tail(seed)


def _arc(c, r, a0, seed):
    pts = []
    for a in (a0, a0 + 45, a0 + 90):
        t = math.radians(a)
        pts.append((c[0] + r * math.cos(t), c[1] + r * math.sin(t)))
    (sx, sy), (mx, my), (ex, ey) = pts
    return (
        f"\t(gr_arc\n\t\t(start {sx:.4f} {sy:.4f})\n\t\t(mid {mx:.4f} {my:.4f})\n\t\t(end {ex:.4f} {ey:.4f})\n"
        + _stroke_tail(seed)
    )


def outline(W, H, r):
    return "".join(
        [
            _line((r, 0), (W - r, 0), "t"),
            _line((W, r), (W, H - r), "r"),
            _line((W - r, H), (r, H), "b"),
            _line((0, H - r), (0, r), "l"),
            _arc((W - r, r), r, 270, "tr"),
            _arc((W - r, H - r), r, 0, "br"),
            _arc((r, H - r), r, 90, "bl"),
            _arc((r, r), r, 180, "tl"),
        ]
    )


def zones(W, H, cfg):
    net = cfg.get("net", "GND")
    inset = 0.3
    pts = ((inset, inset), (W - inset, inset), (W - inset, H - inset), (inset, H - inset))
    xy = " ".join(f"(xy {x:.4f} {y:.4f})" for x, y in pts)
    out = []
    for layer in cfg.get("layers", ["F.Cu", "B.Cu"]):
        out.append(
            f'\t(zone\n\t\t(net "{net}")\n\t\t(layer "{layer}")\n\t\t(uuid "{_uid("zone" + layer)}")\n'
            f'\t\t(name "{net}_{layer[0]}")\n\t\t(hatch edge 0.5)\n'
            f"\t\t(connect_pads\n\t\t\t(clearance {cfg.get('clearance', 0.3)})\n\t\t)\n"
            f"\t\t(min_thickness {cfg.get('min_width', 0.25)})\n\t\t(filled_areas_thickness no)\n"
            f"\t\t(fill\n\t\t\t(thermal_gap {cfg.get('thermal_gap', 0.5)})\n\t\t\t(thermal_bridge_width {cfg.get('thermal_bridge', 0.5)})\n\t\t\t(island_removal_mode 0)\n\t\t)\n"
            f"\t\t(polygon\n\t\t\t(pts\n\t\t\t\t{xy}\n\t\t\t)\n\t\t)\n\t)\n"
        )
    return "".join(out)


def holes(pts, fp_root):
    src = open(os.path.join(fp_root, "MountingHole.pretty", "MountingHole_3.2mm_M3.kicad_mod")).read().strip()
    out = []
    for k, (x, y) in enumerate(pts, 1):
        body = src.replace('(footprint "MountingHole_3.2mm_M3"', '(footprint "MountingHole:MountingHole_3.2mm_M3"', 1)
        body = re.sub(r"\n\t\(version \d+\)\n\t\(generator [^\n]*\)", "", body)
        body = body.replace(
            '\n\t(layer "F.Cu")', f'\n\t(layer "F.Cu")\n\t(uuid "{_uid(f"H{k}")}")\n\t(at {x:.4f} {y:.4f})', 1
        )
        body = body.replace(
            '(property "Reference" "REF**"\n\t\t(at 0 -4.15 0)\n\t\t(layer "F.SilkS")',
            f'(property "Reference" "H{k}"\n\t\t(at 0 -4.15 0)\n\t\t(layer "F.SilkS")\n\t\t(hide yes)',
            1,
        )
        # board-only: no schematic symbol, so parity and the BOM leave it out
        body = body.replace(
            "(attr exclude_from_pos_files exclude_from_bom)",
            "(attr board_only exclude_from_pos_files exclude_from_bom)",
        )
        out.append("\t" + body.replace("\n", "\n\t") + "\n")
    return "".join(out)


def fiducial_spots(n, W, H, cfg):
    """Up to n fiducial spots (3 = two top corners and bottom left, the usual pick-and-place set),
    each as close to its corner as the holes, parts, pads and tracks allow."""
    g = cfg.get("geometry") or {}
    courts = [tuple(c) for c in g.get("courts", [])]
    if g.get("title_box"):
        courts.append(tuple(g["title_box"]))
    holes_ = cfg.get("holes", [])
    corners = [(0, 0), (W, 0), (0, H), (W, H)][: max(0, min(4, n))]
    if n == 3:
        corners = [(0, 0), (W, 0), (0, H)]
    out = []
    for cx, cy in corners:
        best = None
        for iy in range(0, int(min(H, 14) / 0.25)):
            for ix in range(0, int(min(W, 14) / 0.25)):
                x = abs(cx - (1.6 + ix * 0.25))
                y = abs(cy - (1.6 + iy * 0.25))
                if x < 1.6 or y < 1.6 or x > W - 1.6 or y > H - 1.6:
                    continue
                if any(math.hypot(x - hx, y - hy) < 3.5 + 1.3 for hx, hy in holes_):
                    continue
                if any(_rect_d(x, y, k) < 1.4 for k in courts):
                    continue
                if any(_rect_d(x, y, q["rect"]) < 1.7 for q in g.get("pads", [])):
                    continue
                if any(
                    _seg_d(x, y, a, b) < 1.25 + t["width"] / 2
                    for t in g.get("tracks", [])
                    for a, b in zip(t["pts"], t["pts"][1:])
                ):
                    continue
                if any(math.hypot(x - v["x"], y - v["y"]) < 1.9 for v in g.get("vias", [])):
                    continue
                if any(math.hypot(x - fx, y - fy) < 4 for fx, fy in out):
                    continue
                d = math.hypot(x - cx, y - cy)
                if best is None or d < best[0]:
                    best = (d, x, y)
        if best:
            out.append((round(best[1], 3), round(best[2], 3)))
    return out


def fiducials(pts, fp_root):
    """KiCad's own Fiducial_1mm_Mask2mm (1 mm bare copper, 2 mm mask opening), board-only, not in the BOM."""
    src = open(os.path.join(fp_root, "Fiducial.pretty", "Fiducial_1mm_Mask2mm.kicad_mod")).read().strip()
    out = []
    for k, (x, y) in enumerate(pts, 1):
        body = src.replace('(footprint "Fiducial_1mm_Mask2mm"', '(footprint "Fiducial:Fiducial_1mm_Mask2mm"', 1)
        body = re.sub(r"\n\t\(version \d+\)\n\t\(generator [^\n]*\)", "", body)
        body = body.replace(
            '\n\t(layer "F.Cu")', f'\n\t(layer "F.Cu")\n\t(uuid "{_uid(f"FID{k}")}")\n\t(at {x:.4f} {y:.4f})', 1
        )
        body = body.replace(
            '(property "Reference" "REF**"\n\t\t(at 0 -1.95 0)\n\t\t(layer "F.SilkS")',
            f'(property "Reference" "FID{k}"\n\t\t(at 0 -1.95 0)\n\t\t(layer "F.SilkS")\n\t\t(hide yes)',
            1,
        )
        body = body.replace("(attr smd exclude_from_bom)", "(attr smd board_only exclude_from_pos_files exclude_from_bom)")
        out.append("\t" + body.replace("\n", "\n\t") + "\n")
    return "".join(out)


def silk(items):
    out = []
    for k, it in enumerate(items):
        layer = it.get("layer", "F.SilkS")
        s = it.get("size", 1.0)
        th = max(0.12, s * 0.15)
        txt = it["text"].replace('"', "'")
        just = "\n\t\t\t(justify mirror)" if layer.startswith("B") else ""
        out.append(
            f'\t(gr_text "{txt}"\n\t\t(at {it["x"]:.4f} {it["y"]:.4f} 0)\n\t\t(layer "{layer}")\n'
            f'\t\t(uuid "{_uid("silk" + str(k) + txt)}")\n'
            f"\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {s} {s})\n\t\t\t\t(thickness {th:.3f})\n\t\t\t){just}\n\t\t)\n\t)\n"
        )
    return "".join(out)


def _rect_d(x, y, r):
    dx = max(r[0] - x, 0, x - r[2])
    dy = max(r[1] - y, 0, y - r[3])
    return math.hypot(dx, dy)


def _seg_d(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / L2))
    return math.hypot(px - a[0] - t * dx, py - a[1] - t * dy)


def stitch_vias(W, H, cfg):
    """GND vias on a regular grid in open board area: away from every pad, other-net track and via,
    part courtyard, mounting hole, the title and the edge. KiCad's zone filler then ties them into
    both pours; build.py drops any that the filler leaves unconnected."""
    st, g = cfg["stitch"], cfg["geometry"]
    net = cfg.get("pour", {}).get("net", "GND")
    pitch, d, drill = st.get("pitch", 5.08), st.get("d", 0.6), st.get("drill", 0.3)
    r, clr = d / 2, st.get("clearance", 0.45)
    keep_out = [tuple(c) for c in g.get("courts", [])]
    if g.get("title_box"):
        keep_out.append(tuple(g["title_box"]))
    hv_nets, hv = set(g.get("hv_nets") or []), float(g.get("hv_clearance") or 0)

    def gap(n):  # an HV item's neighbours keep the HV class gap (DRC flagged GND stitching next to 170 V)
        return max(clr, hv) if n in hv_nets else clr

    out, placed = [], [(v["x"], v["y"], gap(v.get("net"))) for v in g.get("vias", [])]
    nx, ny = int(W // pitch), int(H // pitch)
    x0, y0 = (W - (nx - 1) * pitch) / 2, (H - (ny - 1) * pitch) / 2
    for iy in range(ny):
        for ix in range(nx):
            x, y = x0 + ix * pitch, y0 + iy * pitch
            if x < 1.5 or y < 1.5 or x > W - 1.5 or y > H - 1.5:
                continue
            if any(math.hypot(x - hx, y - hy) < 3.5 + r + 0.5 for hx, hy in cfg.get("holes", [])):
                continue
            if any(_rect_d(x, y, k) < r + 0.3 for k in keep_out):
                continue
            if any(_rect_d(x, y, p["rect"]) < r + gap(p["net"]) + (0.3 if p["net"] == net else 0) for p in g["pads"]):
                continue
            if any(
                _seg_d(x, y, a, b) < r + t["width"] / 2 + gap(t["net"])
                for t in g["tracks"]
                if t["net"] != net
                for a, b in zip(t["pts"], t["pts"][1:])
            ):
                continue
            if any(math.hypot(x - vx, y - vy) < d + c for vx, vy, c in placed):
                continue
            placed.append((x, y, clr))
            out.append(
                f'\t(via\n\t\t(at {x:.4f} {y:.4f})\n\t\t(size {d})\n\t\t(drill {drill})\n\t\t(layers "F.Cu" "B.Cu")\n'
                f'\t\t(net "{net}")\n\t\t(uuid "{_uid(f"stitch{ix},{iy}")}")\n\t)\n'
            )
    return "".join(out)


def stackup(mask, silk="White", finish="HAL lead-free"):
    """A plain 2-layer 1.6 mm FR4 stackup with mask and silk colours, so renders show the real board colour."""
    L = [
        ("F.SilkS", "Top Silk Screen", f'(color "{silk}")'),
        ("F.Paste", "Top Solder Paste", ""),
        ("F.Mask", "Top Solder Mask", f'(color "{mask}") (thickness 0.01)'),
        ("F.Cu", "copper", "(thickness 0.035)"),
        ("dielectric 1", "core", '(thickness 1.51) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02)'),
        ("B.Cu", "copper", "(thickness 0.035)"),
        ("B.Mask", "Bottom Solder Mask", f'(color "{mask}") (thickness 0.01)'),
        ("B.Paste", "Bottom Solder Paste", ""),
        ("B.SilkS", "Bottom Silk Screen", f'(color "{silk}")'),
    ]
    rows = "".join(f'\t\t\t(layer "{n}" (type "{t}") {x})\n' for n, t, x in L)
    return f'\t\t(stackup\n{rows}\t\t\t(copper_finish "{finish}")\n\t\t\t(dielectric_constraints no)\n\t\t)\n'


def main(pcb_path, cfg_path):
    cfg = json.load(open(cfg_path))
    W, H = cfg["w"], cfg["h"]
    text = open(pcb_path).read().rstrip()
    assert text.endswith(")")
    text = text[:-1].rstrip() + "\n"
    add = ""
    if cfg.get("mask") and "(stackup" not in text:
        text = text.replace(
            "\t(setup\n", "\t(setup\n" + stackup(cfg["mask"], finish=cfg.get("copper_finish", "HAL lead-free")), 1
        )
    if "outline" in cfg:
        text = _strip_edge_lines(text)
        add += outline(W, H, cfg["outline"].get("corner_r", 3.0))
    if cfg.get("holes"):
        add += holes(cfg["holes"], cfg["fp_root"])
    items = list(cfg.get("silk", []))
    tb = (cfg.get("geometry") or {}).get("title_box")
    if cfg.get("title") and tb:
        t = cfg["title"]
        cx, cy = (tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2
        sz = t.get("size", 1.2)
        items.append({"text": t["text"], "x": cx, "y": cy - (0.7 * sz if t.get("sub") else 0), "size": sz})
        if t.get("sub"):
            items.append({"text": t["sub"], "x": cx, "y": cy + 0.9 * sz, "size": round(max(0.8, sz * 0.7), 2)})
    for it in items:
        # anchor: bottom = centred near the bottom edge (credit line on the back)
        if it.get("anchor") == "bottom":
            it["x"], it["y"] = W / 2, H - 1.6
    if items:
        add += silk(items)
    if cfg.get("fiducials") and cfg.get("geometry"):
        fids = fiducial_spots(int(cfg["fiducials"]), W, H, cfg)
        add += fiducials(fids, cfg["fp_root"])
        # stitching vias keep 2 mm away from each fiducial (the mask opening plus clearance)
        cfg["geometry"].setdefault("courts", []).extend([[x - 1.5, y - 1.5, x + 1.5, y + 1.5] for x, y in fids])
    if cfg.get("stitch") and cfg.get("geometry"):
        add += stitch_vias(W, H, cfg)
    if "pour" in cfg:
        add += zones(W, H, cfg["pour"])
    open(pcb_path, "w").write(text + add + ")\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

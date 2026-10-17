"""Read what the library needs from a .kicad_pcb (KiCad 5-10) and its schematics, and from a GLB's JSON chunk."""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any

from common import child, children, parse_sexpr, prop

MASK_COLORS = {"green", "red", "blue", "black", "white", "yellow", "purple", "matte black", "matte green", "orange"}


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _layer(fp: list) -> str:
    lay = child(fp, "layer")
    return lay[1] if lay and len(lay) > 1 else "F.Cu"


def _text(fp: list, kind: str) -> str | None:
    """KiCad 5-7 (fp_text reference "U1" ...) / (fp_text value ...)."""
    for c in children(fp, "fp_text"):
        if len(c) > 2 and c[1] == kind:
            return c[2]
    return None


def schematic_descriptions(sch_dir: Path) -> dict[str, str]:
    """refdes -> symbol description, from every .kicad_sch next to the board."""
    out: dict[str, str] = {}
    if not sch_dir.is_dir():
        return out
    for f in sorted(sch_dir.glob("*.kicad_sch")):
        try:
            tree = parse_sexpr(f.read_text(errors="replace"))
        except Exception:  # noqa: BLE001
            continue
        lib_desc: dict[str, str] = {}
        ls = child(tree, "lib_symbols")
        for s in children(ls, "symbol") if ls else []:
            d = prop(s, "Description") or prop(s, "ki_description")
            if d:
                lib_desc[s[1]] = d
        for s in children(tree, "symbol"):
            ref = prop(s, "Reference")
            if not ref or ref.startswith("#"):
                continue
            lid = child(s, "lib_id")
            d = prop(s, "Description") or (lib_desc.get(lid[1]) if lid else None)
            if d and ref not in out:
                out[ref] = d
    return out


def board_info(pcb: Path, sch_dir: Path | None = None) -> dict[str, Any]:
    tree = parse_sexpr(pcb.read_text(errors="replace"))
    version = child(tree, "version")
    gen = child(tree, "general")
    thick = _f(child(gen, "thickness")[1], 1.6) if gen and child(gen, "thickness") else 1.6

    layers = child(tree, "layers")
    cu = [ly for ly in (layers[1:] if layers else []) if isinstance(ly, list) and len(ly) > 1 and str(ly[1]).endswith(".Cu")]
    n_layers = len(cu) or 2

    mask, silk, finish = "green", "white", None
    setup = child(tree, "setup")
    stack = child(setup, "stackup") if setup else None
    if stack:
        for ly in children(stack, "layer"):
            name = ly[1] if len(ly) > 1 else ""
            col = child(ly, "color")
            if col and len(col) > 1:
                c = str(col[1]).strip()
                if name == "F.Mask":
                    mask = c.lower()
                elif name == "F.SilkS":
                    silk = c.lower()
            if name == "dielectric 1" or (child(ly, "type") and "core" in str(child(ly, "type")[1]).lower()):
                pass
        cf = child(stack, "copper_finish")
        if cf and len(cf) > 1 and str(cf[1]).strip().lower() not in ("none", ""):
            finish = cf[1]
    if mask.startswith("#"):
        mask = mask  # custom colour as hex
    elif mask not in MASK_COLORS:
        mask = mask or "green"

    # outline bounds from Edge.Cuts graphics
    xs: list[float] = []
    ys: list[float] = []

    def add_pt(n: list | None) -> None:
        if n and len(n) > 2:
            xs.append(_f(n[1]))
            ys.append(_f(n[2]))

    for tag in ("gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly", "gr_curve"):
        for g in children(tree, tag):
            lay = child(g, "layer")
            if not lay or lay[1] != "Edge.Cuts":
                continue
            if tag == "gr_circle":
                c, e = child(g, "center"), child(g, "end")
                if c and e:
                    r = ((_f(e[1]) - _f(c[1])) ** 2 + (_f(e[2]) - _f(c[2])) ** 2) ** 0.5
                    xs += [_f(c[1]) - r, _f(c[1]) + r]
                    ys += [_f(c[2]) - r, _f(c[2]) + r]
                continue
            for k in ("start", "end", "mid"):
                add_pt(child(g, k))
            pts = child(g, "pts")
            for p in children(pts, "xy") if pts else []:
                add_pt(p)

    # Edge.Cuts drawn inside footprints (board outlines and cut-outs shipped as footprints)
    import math

    for fp in children(tree, "footprint") + children(tree, "module"):
        at = child(fp, "at") or ["at", 0, 0]
        fx, fy = _f(at[1]), _f(at[2])
        rot = math.radians(_f(at[3]) if len(at) > 3 else 0.0)
        c, sn = math.cos(rot), math.sin(rot)

        def fp_pt(n: list | None) -> None:
            if n and len(n) > 2:
                lx, ly = _f(n[1]), _f(n[2])
                # KiCad rotates footprint-local coordinates clockwise on screen (y down)
                xs.append(fx + lx * c + ly * sn)
                ys.append(fy - lx * sn + ly * c)

        for tag in ("fp_line", "fp_rect", "fp_arc", "fp_poly", "fp_circle"):
            for g in children(fp, tag):
                lay = child(g, "layer")
                if not lay or lay[1] != "Edge.Cuts":
                    continue
                for k in ("start", "end", "mid", "center"):
                    fp_pt(child(g, k))
                pts = child(g, "pts")
                for pt in children(pts, "xy") if pts else []:
                    fp_pt(pt)

    parts: dict[str, dict] = {}
    descs = schematic_descriptions(sch_dir) if sch_dir else {}
    fps = children(tree, "footprint") + children(tree, "module")
    fxs: list[float] = []
    fys: list[float] = []
    for fp in fps:
        ref = prop(fp, "Reference") or _text(fp, "reference") or ""
        if not ref or ref.startswith("#") or "*" in ref or ref in parts:
            continue
        at = child(fp, "at") or ["at", 0, 0]
        x, y = _f(at[1]), _f(at[2])
        rot = _f(at[3]) if len(at) > 3 else 0.0
        fxs.append(x)
        fys.append(y)
        side = "bottom" if _layer(fp).startswith("B.") else "top"
        descr = child(fp, "descr")
        parts[ref] = {
            "ref": ref,
            "value": prop(fp, "Value") or _text(fp, "value") or "",
            "footprint": fp[1] if len(fp) > 1 and isinstance(fp[1], str) else "",
            "description": prop(fp, "Description") or descs.get(ref) or (descr[1] if descr and len(descr) > 1 else ""),
            "pcb_mm": [round(x, 4), round(y, 4)],
            "side": side,
            "rot": round(rot, 2),
            "has_model": bool(children(fp, "model")),
        }
    if not xs:
        xs, ys = fxs or [0.0], fys or [0.0]
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    return {
        "version": version[1] if version and len(version) > 1 else None,
        "layers": n_layers,
        "thickness_mm": round(thick, 3),
        "mask": mask,
        "silk": silk,
        "finish": finish,
        "bbox_mm": [round(v, 4) for v in bbox],
        "size_mm": [round(bbox[2] - bbox[0], 2), round(bbox[3] - bbox[1], 2)],
        "parts": parts,
    }


def footprint_models(pcb: Path) -> list[dict[str, Any]]:
    """Every (model ...) on every footprint: {ref, path, hidden}. Footprints without a model are not listed
    (no model is ever invented for them)."""
    tree = parse_sexpr(pcb.read_text(errors="replace"))
    out = []
    for fp in children(tree, "footprint") + children(tree, "module"):
        ref = prop(fp, "Reference") or _text(fp, "reference") or "?"
        for m in children(fp, "model"):
            if len(m) < 2 or not isinstance(m[1], str):
                continue
            hide = child(m, "hide")
            hidden = "hide" in m[2:] or bool(hide and (len(hide) < 2 or hide[1] == "yes"))
            out.append({"ref": ref, "path": m[1], "hidden": hidden})
    return out


# ---------------------------------------------------------------- GLB

def glb_json(path: Path) -> dict:
    data = path.read_bytes()
    magic, _ver, _len = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("not a GLB")
    clen, ctype = struct.unpack_from("<I4s", data, 12)
    return json.loads(data[20 : 20 + clen])


def _quat_mat(q: list[float]) -> list[list[float]]:
    x, y, z, w = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def _mat_from_node(n: dict) -> list[list[float]]:
    if "matrix" in n:
        m = n["matrix"]
        return [[m[c * 4 + r] for c in range(4)] for r in range(4)]
    s = n.get("scale", [1, 1, 1])
    r = _quat_mat(n.get("rotation", [0, 0, 0, 1]))
    t = n.get("translation", [0, 0, 0])
    return [[r[i][0] * s[0], r[i][1] * s[1], r[i][2] * s[2], t[i]] for i in range(3)] + [[0, 0, 0, 1]]


def _mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def glb_nodes(path: Path) -> dict[str, list[float]]:
    """Every named node -> its world-space origin [x, y, z] in metres."""
    g = glb_json(path)
    nodes = g.get("nodes", [])
    out: dict[str, list[float]] = {}
    eye = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]

    def walk(i: int, parent: list[list[float]]) -> None:
        n = nodes[i]
        m = _mul(parent, _mat_from_node(n))
        if n.get("name") and n["name"] not in out:
            out[n["name"]] = [m[0][3], m[1][3], m[2][3]]
        for c in n.get("children", []):
            walk(c, m)

    for sc in g.get("scenes", [{}])[g.get("scene", 0):][:1]:
        for r in sc.get("nodes", []):
            walk(r, eye)
    return out


NORM = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}


def glb_bounds(path: Path) -> tuple[list[float], list[float]]:
    """World AABB from accessor min/max of every mesh primitive (no vertex decode)."""
    g = glb_json(path)
    nodes, meshes, accs = g.get("nodes", []), g.get("meshes", []), g.get("accessors", [])
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    eye = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]

    def walk(i: int, parent: list[list[float]]) -> None:
        n = nodes[i]
        m = _mul(parent, _mat_from_node(n))
        if "mesh" in n:
            for p in meshes[n["mesh"]].get("primitives", []):
                a = accs[p["attributes"]["POSITION"]]
                if "min" not in a:
                    continue
                # KHR_mesh_quantization: normalized integer positions; min/max are in raw integer units
                k = NORM.get(a.get("componentType"), 1.0) if a.get("normalized") else 1.0
                amin = [v / k for v in a["min"]]
                amax = [v / k for v in a["max"]]
                for cx in (amin[0], amax[0]):
                    for cy in (amin[1], amax[1]):
                        for cz in (amin[2], amax[2]):
                            for k in range(3):
                                v = m[k][0] * cx + m[k][1] * cy + m[k][2] * cz + m[k][3]
                                lo[k] = min(lo[k], v)
                                hi[k] = max(hi[k], v)
        for c in n.get("children", []):
            walk(c, m)

    for sc in g.get("scenes", [{}])[g.get("scene", 0):][:1]:
        for r in sc.get("nodes", []):
            walk(r, eye)
    return lo, hi


if __name__ == "__main__":
    import sys

    info = board_info(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
    parts = info.pop("parts")
    print(json.dumps(info, indent=1))
    print(len(parts), "parts;", json.dumps(list(parts.values())[:3], indent=1))


def glb_clear_names(path: Path, pattern: str = r"^=>\[") -> int:
    """In place: blank node names matching pattern (kicad-cli's "=>[0:1:1:94]" board-layer nodes) and make the
    asset metadata neutral (generator "Etch", no copyright/extras). Returns the number of changes."""
    data = path.read_bytes()
    clen = struct.unpack_from("<I", data, 12)[0]
    g = json.loads(data[20 : 20 + clen])
    n = 0
    for node in g.get("nodes", []):
        if node.get("name") and re.match(pattern, node["name"]):
            del node["name"]
            n += 1
    asset = g.setdefault("asset", {"version": "2.0"})
    if asset.get("generator") != "Etch" or "copyright" in asset or "extras" in asset:
        asset["generator"] = "Etch"
        asset.pop("copyright", None)
        asset.pop("extras", None)
        n += 1
    if not n:
        return 0
    js = json.dumps(g, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    rest = data[20 + clen :]
    out = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + len(rest)) + struct.pack("<I4s", len(js), b"JSON") + js + rest
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(out)
    tmp.replace(path)
    return n

"""Export every Parts tab footprint as a small 3D part model for the editor's build animation.

For each footprint in deploy/etch-app/js/editor/parts-catalog.json: the one-footprint board from
library/render_parts.py -> `kicad-cli pcb export glb --no-board-body --subst-models` (its STEP model, cached in
library/cache/parts3d) -> moved so the footprint's anchor sits at the origin on the board's top face (y = 0, the
same frame KiCad puts a footprint in on a real board) -> flattened, one mesh per material, welded, lightly
simplified, quantized and meshopt-compressed -> deploy/etch-app/parts3d/<Lib>__<Footprint>.glb.

Also writes deploy/etch-app/parts3d/index.json: per footprint its file (null when KiCad has no 3D model for it),
model size, courtyard, pads (x, y, w, h, shape, through-hole drill, rotation) and front silkscreen strokes, all in
footprint millimetres (x right, y down, KiCad's own frame), so the animation can lay pads and silk itself.

  python3 library/export_part_glbs.py            # only what is missing
  python3 library/export_part_glbs.py --force    # everything
  python3 library/export_part_glbs.py --only USB

Needs KiCad 10 (as render_parts.py) and the gltf-transform libs in library/cache/node (render.py installs them).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from render_parts import APP, CATALOG, WORK, export, mod_file, safe_name

ROOT = Path(__file__).resolve().parent.parent
OUT = APP / "parts3d"
NODE_LIBS = ROOT / "library/cache/node"

# flatten -> join per material -> weld -> simplify (tiny error, only big meshes) -> quantize -> meshopt
OPTIMIZE = r"""
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const req = createRequire(path.join(process.env.NODE_LIBS, 'package.json'));
const load = (m) => import(pathToFileURL(req.resolve(m)).href);
const { NodeIO, Logger } = await load('@gltf-transform/core');
const { ALL_EXTENSIONS } = await load('@gltf-transform/extensions');
const F = await load('@gltf-transform/functions');
const { MeshoptEncoder, MeshoptDecoder, MeshoptSimplifier } = await load('meshoptimizer');
await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready, MeshoptSimplifier.ready]);
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });
const jobs = JSON.parse(process.argv[2]);
const out = {};
for (const [src, dst] of jobs) {
  try {
    const doc = await io.read(src);
    doc.setLogger(new Logger(Logger.Verbosity.ERROR));
    const root = doc.getRoot(), scene = root.listScenes()[0];
    // the footprint node sits at (0, board top, 0): move everything down so the board's top face is y = 0
    const tops = scene.listChildren();
    const fpn = root.listNodes().find((n) => n.getName() === 'REF**');
    const ty = fpn ? fpn.getWorldTranslation()[1] : 0;
    const wrap = doc.createNode('part').setTranslation([0, -ty, 0]);
    for (const n of tops) { scene.removeChild(n); wrap.addChild(n); }
    scene.addChild(wrap);
    await doc.transform(F.dedup(), F.flatten(), F.join({ keepNamed: false }), F.weld({}));
    let verts = 0;
    for (const m of root.listMeshes()) for (const p of m.listPrimitives()) verts += p.getAttribute('POSITION').getCount();
    if (verts > 12000) await doc.transform(F.simplify({ simplifier: MeshoptSimplifier, ratio: Math.max(0.25, 12000 / verts), error: 0.0004 }));
    const b = F.getBounds(scene);
    const box = [...b.min, ...b.max].map((v) => Math.round(v * 1e6) / 1e3);
    await doc.transform(
      F.prune({ keepLeaves: false, keepAttributes: false }),
      F.quantize({ quantizePosition: 14, quantizeNormal: 8, quantizeTexcoord: 12 }),
      F.meshopt({ encoder: MeshoptEncoder, level: 'high' }),
    );
    for (const n of root.listNodes()) n.setName('');
    const asset = root.getAsset(); asset.generator = 'Etch'; delete asset.copyright; delete asset.extras;
    await io.write(dst, doc);
    let v2 = 0;
    for (const m of root.listMeshes()) for (const p of m.listPrimitives()) v2 += p.getAttribute('POSITION').getCount();
    out[dst] = { verts: v2, box };
  } catch (e) { out[dst] = { error: String(e.message || e).split('\n')[0] }; }
}
process.stdout.write('\n@@' + JSON.stringify(out));
"""


# ---------- a tiny s-expression reader for .kicad_mod ----------
def sexpr(text: str):
    tok = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+', text)
    stack, cur = [], []
    for t in tok:
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        else:
            cur.append(t[1:-1] if t.startswith('"') else t)
    return cur[0] if cur else []


def kids(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def one(node, name):
    k = kids(node, name)
    return k[0] if k else None


def nums(node, n=None):
    v = [float(x) for x in node[1:] if isinstance(x, str) and re.fullmatch(r"-?[\d.]+(e-?\d+)?", x)]
    return v if n is None else (v + [0.0] * n)[:n]


def layer_of(node) -> str:
    l = one(node, "layer")
    return l[1] if l else ""


def r2(v: float) -> float:
    return round(v, 3)


def footprint_info(footprint: str) -> dict:
    fp = sexpr(mod_file(footprint).read_text())
    pads = []
    shapes = {"rect": 0, "circle": 1, "oval": 2, "roundrect": 3, "trapezoid": 0, "custom": 0}
    for p in kids(fp, "pad"):
        kind = p[2] if len(p) > 2 else "smd"
        shape = p[3] if len(p) > 3 else "rect"
        layers = one(p, "layers") or []
        if kind == "np_thru_hole" or not any("Cu" in str(l) for l in layers[1:]):
            # a mounting hole without copper is still drilled: keep it as a hole only
            if kind != "np_thru_hole":
                continue
        x, y, rot = nums(one(p, "at"), 3)
        w, h = nums(one(p, "size"), 2)
        dr = one(p, "drill")
        drill = 0.0
        if dr is not None:
            dv = nums(dr)
            drill = dv[0] if dv else 0.0
        front = any(str(l) in ("F.Cu", "*.Cu") for l in layers[1:])
        if not front and kind == "smd":
            continue
        pads.append([r2(x), r2(y), r2(w), r2(h), shapes.get(shape, 0), r2(drill), r2(rot), 1 if kind == "np_thru_hole" else 0])
    # front silkscreen as polylines (lines, rects, circles, arcs, polys), in footprint mm
    silk, crt = [], []

    def stroke_w(node):
        s = one(node, "stroke")
        w = one(s, "width") if s else one(node, "width")
        return r2(nums(w)[0]) if w else 0.12

    for kind in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly"):
        for g in kids(fp, kind):
            lay = layer_of(g)
            if lay not in ("F.SilkS", "F.CrtYd"):
                continue
            pts = []
            if kind == "fp_line":
                pts = [nums(one(g, "start"), 2), nums(one(g, "end"), 2)]
            elif kind == "fp_rect":
                (x0, y0), (x1, y1) = nums(one(g, "start"), 2), nums(one(g, "end"), 2)
                pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
            elif kind == "fp_circle":
                (cx, cy), (ex, ey) = nums(one(g, "center"), 2), nums(one(g, "end"), 2)
                r = math.hypot(ex - cx, ey - cy)
                n = max(12, min(40, int(r * 10)))
                pts = [[cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)] for i in range(n + 1)]
            elif kind == "fp_arc":
                (sx, sy), (mx, my), (ex, ey) = nums(one(g, "start"), 2), nums(one(g, "mid"), 2), nums(one(g, "end"), 2)
                pts = arc3(sx, sy, mx, my, ex, ey)
            elif kind == "fp_poly":
                p = one(g, "pts")
                pts = [nums(xy, 2) for xy in kids(p, "xy")] if p else []
                if pts:
                    pts.append(pts[0])
            if len(pts) < 2:
                continue
            flat = [r2(v) for xy in pts for v in xy]
            (silk if lay == "F.SilkS" else crt).append([stroke_w(g)] + flat)
    # courtyard box (falls back to pads)
    xs, ys = [], []
    for c in crt:
        xs += c[1::2]
        ys += c[2::2]
    if not xs:
        for p in pads:
            xs += [p[0] - p[2] / 2, p[0] + p[2] / 2]
            ys += [p[1] - p[3] / 2, p[1] + p[3] / 2]
    box = [r2(min(xs)), r2(min(ys)), r2(max(xs)), r2(max(ys))] if xs else [-1, -1, 1, 1]
    return {"pads": pads, "silk": silk, "court": box}


def arc3(sx, sy, mx, my, ex, ey):
    """Three-point arc -> polyline."""
    d = 2 * (sx * (my - ey) + mx * (ey - sy) + ex * (sy - my))
    if abs(d) < 1e-9:
        return [[sx, sy], [ex, ey]]
    ux = ((sx * sx + sy * sy) * (my - ey) + (mx * mx + my * my) * (ey - sy) + (ex * ex + ey * ey) * (sy - my)) / d
    uy = ((sx * sx + sy * sy) * (ex - mx) + (mx * mx + my * my) * (sx - ex) + (ex * ex + ey * ey) * (mx - sx)) / d
    r = math.hypot(sx - ux, sy - uy)
    a0, am, a1 = (math.atan2(y - uy, x - ux) for x, y in ((sx, sy), (mx, my), (ex, ey)))

    def norm(a):
        while a < a0:
            a += 2 * math.pi
        return a

    am, a1 = norm(am), norm(a1)
    if am > a1:  # goes the other way round
        a1 -= 2 * math.pi
    n = max(6, int(abs(a1 - a0) * r * 4))
    return [[ux + r * math.cos(a0 + (a1 - a0) * i / n), uy + r * math.sin(a0 + (a1 - a0) * i / n)] for i in range(n + 1)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    fps = sorted({p["footprint"] for p in json.loads(CATALOG.read_text())["parts"]})
    if a.only:
        fps = [f for f in fps if a.only.lower() in f.lower()]
    with ThreadPoolExecutor(a.jobs) as ex:
        infos = list(ex.map(lambda f: export(f, False), fps))
    todo = []
    for i in infos:
        dst = OUT / f"{i['name']}.glb"
        if i["kind"] == "model" and (a.force or a.only or not dst.exists()):
            todo.append([str(WORK / f"{i['name']}.glb"), str(dst)])
    script = WORK / "optimize_parts.mjs"
    script.write_text(OPTIMIZE)
    res = {}
    for k in range(0, len(todo), 20):
        r = subprocess.run(["node", str(script), json.dumps(todo[k:k + 20])], capture_output=True, text=True,
                           env={"NODE_LIBS": str(NODE_LIBS), "PATH": "/opt/homebrew/bin:/usr/bin:/bin"}, timeout=900)
        if r.returncode:
            raise SystemExit(r.stderr[-2000:])
        res.update(json.loads(r.stdout.rsplit("@@", 1)[-1] or "{}"))
    boxes_path = WORK / "parts3d_boxes.json"
    boxes = json.loads(boxes_path.read_text()) if boxes_path.exists() else {}
    for dst, v in res.items():
        if "error" in v:
            print("  fail", Path(dst).name, v["error"])
        else:
            boxes[Path(dst).name] = v["box"]
    boxes_path.write_text(json.dumps(boxes))
    idx_path = OUT / "index.json"
    index = json.loads(idx_path.read_text()).get("parts", {}) if idx_path.exists() and a.only else {}
    for i in infos:
        if i["kind"] == "missing" and not mod_file(i["footprint"]):
            continue
        dst = OUT / f"{i['name']}.glb"
        info = footprint_info(i["footprint"])
        has = i["kind"] == "model" and dst.exists()
        index[i["footprint"]] = {"f": dst.name if has else None, "box": boxes.get(dst.name) if has else None, **info}
    idx_path.write_text(json.dumps({"v": 1, "parts": dict(sorted(index.items()))}, separators=(",", ":")))
    files = list(OUT.glob("*.glb"))
    total = sum(p.stat().st_size for p in files)
    print(f"{len(files)} glb, {total / 1024:.0f} KB total, index {idx_path.stat().st_size / 1024:.0f} KB -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

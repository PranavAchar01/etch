"""Build deploy/etch-app/so101.glb: the SO-101 arm in a folded rest pose, straight from TheRobotStudio's MJCF and STL
files (vendored in player-two/vendor/so101, git-blob identical to SO-ARM100 Simulation/SO101 at the commit below).
Visual geoms only, each mesh decimated, merged into one mesh per colour so the page loads it fast.

  uv run --with trimesh --with fast-simplification --with numpy python -m agent.so101_glb
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT.parent / "player-two" / "vendor" / "so101"
OUT = ROOT / "deploy" / "etch-app" / "so101.glb"
REPO, COMMIT, LICENCE = "https://github.com/TheRobotStudio/SO-ARM100", "a758567c3978dfeefe282ede0500085a48fe8f78", "Apache-2.0"
KEEP = 0.06  # fraction of faces kept per mesh (quadric pass)
GRID = 0.0008  # m, vertex clustering grid
# folded rest pose (radians), inside each joint's range in the MJCF, so the arm stands compact beside the board
POSE = {"shoulder_lift": -1.6, "elbow_flex": 1.55, "wrist_flex": 0.9}


def _tf(el: ET.Element) -> np.ndarray:
    m = np.eye(4)
    if q := el.get("quat"):
        w, x, y, z = map(float, q.split())
        m[:3, :3] = trimesh.transformations.quaternion_matrix([w, x, y, z])[:3, :3]
    if p := el.get("pos"):
        m[:3, 3] = list(map(float, p.split()))
    return m


def main() -> None:
    tree = ET.parse(SRC / "so101_new_calib.xml").getroot()
    rgba = {m.get("name"): m.get("rgba") for m in tree.iter("material")}
    cache: dict[str, trimesh.Trimesh] = {}
    groups: dict[str, list[trimesh.Trimesh]] = {}

    def walk(body: ET.Element, parent: np.ndarray) -> None:
        world = parent @ _tf(body)
        j = body.find("joint")
        if j is not None and j.get("name") in POSE:
            ax = list(map(float, j.get("axis", "0 0 1").split()))
            world = world @ trimesh.transformations.rotation_matrix(POSE[j.get("name")], ax)
        for g in body.findall("geom"):
            if g.get("class") != "visual" or g.get("type") != "mesh":
                continue
            name = g.get("mesh")
            if name not in cache:
                m = trimesh.load_mesh(SRC / "assets" / f"{name}.stl")
                m = m.simplify_quadric_decimation(face_count=max(600, int(len(m.faces) * KEEP))) if len(m.faces) > 800 else m
                m.vertices = np.round(m.vertices / GRID) * GRID  # vertex clustering: quadric stalls on these CAD meshes
                m.merge_vertices()
                m.update_faces(m.nondegenerate_faces())
                m.update_faces(m.unique_faces())
                m.remove_unreferenced_vertices()
                cache[name] = m
            mesh = cache[name].copy()
            mesh.apply_transform(world @ _tf(g))
            groups.setdefault(rgba.get(g.get("material"), "1 0.82 0.12 1"), []).append(mesh)
        for b in body.findall("body"):
            walk(b, world)

    for b in tree.find("worldbody").findall("body"):
        walk(b, np.eye(4))

    scene = trimesh.Scene()
    for i, (col, meshes) in enumerate(sorted(groups.items())):
        m = trimesh.util.concatenate(meshes)
        c = [float(v) for v in col.split()]
        m.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            baseColorFactor=c, metallicFactor=0.0, roughnessFactor=0.55 if c[0] > 0.5 else 0.4))
        scene.add_geometry(m, node_name=f"so101_{'body' if c[0] > 0.5 else 'servos'}")
    OUT.write_bytes(scene.export(file_type="glb"))
    prov = {"source": REPO, "commit": COMMIT, "licence": LICENCE, "files": "Simulation/SO101 (so101_new_calib.xml + assets/*.stl)",
            "pose": "folded rest: " + json.dumps(POSE), "processing": f"visual geoms only, quadric decimation then 0.8 mm vertex clustering, merged per colour",
            "z_up": True, "units": "m", "faces": int(sum(len(g.faces) for g in scene.geometry.values())), "bytes": OUT.stat().st_size}
    OUT.with_name("so101.provenance.json").write_text(json.dumps(prov, indent=1))
    print(json.dumps(prov))


if __name__ == "__main__":
    sys.exit(main())

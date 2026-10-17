"""Prerender every Parts tab footprint as its real 3D component, isolated, for the board editor.

For each footprint in deploy/etch-app/js/editor/parts-catalog.json: a one-footprint .kicad_pcb ->
`kicad-cli pcb export glb --no-board-body` (the STEP model; or, with no model, its pads and silkscreen) ->
one headless Chromium session renders every GLB with three.js in the editor's look (js/board-viewer.js: room
environment, key + hemisphere light, ACES, the same part materials), one fixed 3/4 camera, auto-framed, a soft
contact shadow on white, 640px supersampled to 320px webp -> deploy/etch-app/parts/<footprint>.webp.

  python3 library/render_parts.py            # only what is missing
  python3 library/render_parts.py --force    # everything
  python3 library/render_parts.py --only R_0402

Then `python3 library/build_parts_catalog.py` records `thumb` for each part. Needs KiCad 10 and Playwright's
cached Chromium (any `playwright` npm package under ~/.npm/_npx or ~/.dev-browser works).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "deploy/etch-app"
CATALOG = APP / "js/editor/parts-catalog.json"
OUT = APP / "parts"
WORK = ROOT / "library/cache/parts3d"
KICAD_APP = Path("/opt/homebrew/Caskroom/kicad/10.0.7/KiCad/KiCad.app")
CLI = KICAD_APP / "Contents/MacOS/kicad-cli"
SHARED = KICAD_APP / "Contents/SharedSupport"
VENDOR = ROOT / "vendor/espressif-kicad-libraries"
TEMPLATE = SHARED / "template/Arduino_Nano/Arduino_Nano.kicad_pcb"
SIZE = 320


def safe_name(footprint: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", footprint.replace(":", "__")).strip("_")


def kicad_env() -> dict[str, str]:
    env = dict(os.environ)
    for v in ["KISYS3DMOD", "KICAD_3DMODEL_DIR"] + [f"KICAD{n}_3DMODEL_DIR" for n in range(6, 12)]:
        env[v] = str(SHARED / "3dmodels")
    for v in ["KICAD_FOOTPRINT_DIR"] + [f"KICAD{n}_FOOTPRINT_DIR" for n in range(6, 12)]:
        env[v] = str(SHARED / "footprints")
    return env


def mod_file(footprint: str) -> Path | None:
    lib, name = footprint.split(":", 1)
    for base in (SHARED / "footprints", VENDOR / "footprints"):
        p = base / f"{lib}.pretty" / f"{name}.kicad_mod"
        if p.exists():
            return p
    return None


def resolve_model(path: str) -> Path | None:
    """A footprint's (model ...) path -> an existing STEP (preferred) or WRL on this Mac."""
    rel = re.sub(r"^\$\{[^}]+\}/?", "", path)
    rel = rel.replace("3dmodels/com_github_espressif_kicad-libraries/", "")
    stem = Path(rel).with_suffix("")
    for base in (SHARED / "3dmodels", VENDOR / "3dmodels"):
        for ext in (".step", ".STEP", ".stp", ".wrl"):
            p = base / f"{stem}{ext}"
            if p.exists():
                return p
    return None


def footprint_sexpr(footprint: str) -> tuple[str, bool]:
    """The .kicad_mod as a board footprint at (0,0) with absolute model paths, and whether a model resolved."""
    src = mod_file(footprint).read_text()
    src = re.sub(r'^\(footprint\s+"[^"]*"', f'(footprint "{footprint}"', src.strip(), count=1)
    src = re.sub(r'\n\s*\((version|generator|generator_version)\s+[^\n]*\)', "", src)
    src = re.sub(r'(\(layer "F\.Cu"\))', r'\1\n\t(at 0 0)', src, count=1)
    has = False

    def fix(m: re.Match) -> str:
        nonlocal has
        p = resolve_model(m.group(1))
        if p:
            has = True
            return f'(model "{p}"'
        return m.group(0)

    src = re.sub(r'\(model\s+"([^"]+)"', fix, src)
    # no refdes on the tile: the reference text moves off the silkscreen
    src = re.sub(r'(\(property "Reference" "[^"]*"\s*\(at [^)]*\)\s*)\(layer "F\.SilkS"\)', r'\1(layer "F.Fab")', src, count=1)
    return src, has


def glb_mesh_count(path: Path) -> int:
    try:
        b = path.read_bytes()
        n = struct.unpack_from("<I", b, 12)[0]
        return len(json.loads(b[20:20 + n]).get("meshes", []))
    except Exception:
        return 0


HEADER = None


def board_text(fp_sexpr: str) -> str:
    global HEADER
    if HEADER is None:
        lines = TEMPLATE.read_text().splitlines()
        cut = next(i for i, l in enumerate(lines) if l.startswith("\t(footprint"))
        HEADER = "\n".join(lines[:cut])
    edge = '\t(gr_rect (start -40 -40) (end 40 40) (stroke (width 0.1) (type default)) (fill no) (layer "Edge.Cuts"))'
    return f"{HEADER}\n{fp_sexpr}\n{edge}\n)\n"


def export(footprint: str, force: bool) -> dict:
    name = safe_name(footprint)
    glb = WORK / f"{name}.glb"
    meta = WORK / f"{name}.json"
    if not force and glb.exists() and meta.exists():
        return json.loads(meta.read_text())
    if not mod_file(footprint):
        return {"footprint": footprint, "name": name, "kind": "missing"}
    sexpr, has = footprint_sexpr(footprint)
    pcb = WORK / f"{name}.kicad_pcb"
    pcb.write_text(board_text(sexpr))
    kind = "model" if has else "flat"
    base = [str(CLI), "pcb", "export", "glb", "-f", "--no-board-body", "-o", str(glb)]
    if has:
        subprocess.run(base + ["--subst-models", str(pcb)], capture_output=True, text=True, env=kicad_env(), timeout=240)
        if glb_mesh_count(glb) == 0:
            kind = "flat"
    if kind == "flat":
        subprocess.run(base + ["--no-components", "--include-pads", "--include-silkscreen", str(pcb)],
                       capture_output=True, text=True, env=kicad_env(), timeout=240)
    if glb_mesh_count(glb) == 0:
        kind = "missing"
    info = {"footprint": footprint, "name": name, "kind": kind, "has_model": has}
    meta.write_text(json.dumps(info))
    return info


def find_playwright() -> Path:
    cands = sorted(Path.home().glob(".npm/_npx/*/node_modules/playwright"), key=lambda p: p.stat().st_mtime, reverse=True)
    cands += [Path.home() / ".dev-browser/node_modules/playwright-core"]
    for c in cands:
        if (c / "package.json").exists():
            return c
    sys.exit("no playwright npm package found (npx playwright --version installs one)")


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<script type="importmap">{"imports":{"three":"/three/build/three.module.min.js","three/addons/":"/three/examples/jsm/"}}</script>
</head><body style="margin:0;background:#fff">
<script type="module">
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
const S = __SIZE__ * 2, OUT = __SIZE__;
// the editor's renderer, environment and lights (js/board-viewer.js)
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: true });
renderer.setPixelRatio(1); renderer.setSize(S, S, false);
renderer.setClearColor(0xffffff, 1);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.55;
const key = new THREE.DirectionalLight(0xffffff, 1.5); key.position.set(-0.5, 1, 0.7);
const fill = new THREE.HemisphereLight(0xffffff, 0xd8d8d2, 0.7);
scene.add(key, fill);
const cam = new THREE.PerspectiveCamera(32, 1, 1e-4, 10);
// one fixed 3/4 view for every part: the board's home azimuth, a little lower so sides and pins read
const az = THREE.MathUtils.degToRad(25), el = THREE.MathUtils.degToRad(40);
const DIR = new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el));
const ENIG = { color: 0xdfb85e, metalness: 1, roughness: 0.24 };
// part material, as the editor makes it (STEP colours are already linear)
function partMaterial(src) {
  const c = src.color ? src.color.clone() : new THREE.Color(0.2, 0.2, 0.2), a = src.opacity ?? 1;
  const max = Math.max(c.r, c.g, c.b), min = Math.min(c.r, c.g, c.b);
  const metal = src.metalness > 0.5 || (max > 0.35 && max - min < 0.12 && !src.map);
  return new THREE.MeshStandardMaterial({ color: c, map: src.map || null, metalness: metal ? 0.6 : 0.05, roughness: metal ? 0.36 : 0.48, transparent: a < 1, opacity: a });
}
// soft contact shadow, the editor's blurred rounded rect, in a neutral grey
const shadowTex = (() => {
  const c = document.createElement("canvas"); c.width = c.height = 256;
  const g = c.getContext("2d"); g.filter = "blur(20px)"; g.fillStyle = "rgba(20,28,45,0.9)";
  g.beginPath(); g.roundRect(52, 52, 152, 152, 22); g.fill();
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
})();
const shadow = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: shadowTex, transparent: true, opacity: 0.22, depthWrite: false, toneMapped: false }));
shadow.rotation.x = -Math.PI / 2; shadow.renderOrder = -1;
scene.add(shadow);
const small = document.createElement("canvas"); small.width = small.height = OUT;
const sg = small.getContext("2d"); sg.imageSmoothingEnabled = true; sg.imageSmoothingQuality = "high";
const loader = new GLTFLoader();
let current = null;

function fit(box) {
  const tgt = box.getCenter(new THREE.Vector3()), pts = [];
  for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) pts.push(new THREE.Vector3(x, y, z));
  let d = box.getSize(new THREE.Vector3()).length() * 2 || 0.02;
  for (let i = 0; i < 14; i++) {
    cam.position.copy(tgt).addScaledVector(DIR, d); cam.lookAt(tgt); cam.near = d * 0.01; cam.far = d * 20; cam.updateProjectionMatrix(); cam.updateMatrixWorld();
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    for (const p of pts) { const q = p.clone().project(cam); x0 = Math.min(x0, q.x); x1 = Math.max(x1, q.x); y0 = Math.min(y0, q.y); y1 = Math.max(y1, q.y); }
    // centre the projected box, then scale so its larger side fills 70% of the tile
    const k = Math.max(x1 - x0, y1 - y0) / 2 / 0.7;
    const off = new THREE.Vector3((x0 + x1) / 2, (y0 + y1) / 2, 0);
    const right = new THREE.Vector3().setFromMatrixColumn(cam.matrixWorld, 0), up = new THREE.Vector3().setFromMatrixColumn(cam.matrixWorld, 1);
    const half = d * Math.tan(THREE.MathUtils.degToRad(cam.fov / 2));
    tgt.addScaledVector(right, off.x * half).addScaledVector(up, off.y * half);
    d *= 0.35 + 0.65 * k;
  }
  cam.position.copy(tgt).addScaledVector(DIR, d); cam.lookAt(tgt); cam.updateMatrixWorld();
}

window.renderPart = async (url, kind) => {
  if (current) { scene.remove(current); current.traverse((o) => { o.geometry?.dispose(); }); }
  const gltf = await loader.loadAsync(url);
  const root = gltf.scene;
  const group = new THREE.Group(); group.add(root); scene.add(group); current = group;
  const metas = [];
  root.traverse((o) => { if (o.isMesh) metas.push(o); });
  if (kind === "flat") {
    // no 3D model: the footprint's pads and silkscreen on a light neutral plinth, never a green board
    for (const o of metas) {
      const src = Array.isArray(o.material) ? o.material[0] : o.material, n = (src.name || "").toLowerCase(), c = src.color || new THREE.Color(1, 1, 1);
      const silk = /silk|legend/.test(n) || (c.r > 0.82 && c.g > 0.82 && c.b > 0.82);
      o.material = silk ? new THREE.MeshStandardMaterial({ color: 0x3b4150, roughness: 0.85, metalness: 0, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4 })
                        : new THREE.MeshStandardMaterial({ ...ENIG });
    }
  } else {
    const mats = new Map();
    for (const o of metas) { const src = Array.isArray(o.material) ? o.material[0] : o.material; if (!mats.has(src)) mats.set(src, partMaterial(src)); o.material = mats.get(src); }
  }
  group.updateMatrixWorld(true);
  let box = new THREE.Box3().setFromObject(group);
  const ctr = box.getCenter(new THREE.Vector3());
  group.position.set(-ctr.x, -box.min.y, -ctr.z);
  group.updateMatrixWorld(true);
  if (kind === "flat") {
    const b = new THREE.Box3().setFromObject(group), sz = b.getSize(new THREE.Vector3());
    const pad = Math.max(sz.x, sz.z) * 0.12 + 0.0006, h = Math.max(0.0004, Math.max(sz.x, sz.z) * 0.06);
    const w = sz.x + pad * 2, dd = sz.z + pad * 2, r = Math.min(w, dd) * 0.12;
    const shape = new THREE.Shape();
    shape.moveTo(-w / 2 + r, -dd / 2); shape.lineTo(w / 2 - r, -dd / 2); shape.quadraticCurveTo(w / 2, -dd / 2, w / 2, -dd / 2 + r);
    shape.lineTo(w / 2, dd / 2 - r); shape.quadraticCurveTo(w / 2, dd / 2, w / 2 - r, dd / 2); shape.lineTo(-w / 2 + r, dd / 2);
    shape.quadraticCurveTo(-w / 2, dd / 2, -w / 2, dd / 2 - r); shape.lineTo(-w / 2, -dd / 2 + r); shape.quadraticCurveTo(-w / 2, -dd / 2, -w / 2 + r, -dd / 2);
    const geo = new THREE.ExtrudeGeometry(shape, { depth: h, bevelEnabled: true, bevelThickness: h * 0.25, bevelSize: h * 0.25, bevelSegments: 3, curveSegments: 6 });
    geo.rotateX(Math.PI / 2);
    const plinth = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0xeceef2, roughness: 0.7, metalness: 0 }));
    plinth.position.set((b.min.x + b.max.x) / 2, 0, (b.min.z + b.max.z) / 2);
    plinth.updateMatrixWorld(true);
    const pb = new THREE.Box3().setFromObject(plinth);
    plinth.position.y -= pb.min.y; // plinth sits on the floor
    group.position.y += pb.max.y - pb.min.y; // pads and silk sit on its top face
    scene.add(plinth); group.userData.plinth = plinth;
    plinth.updateMatrixWorld(true); group.updateMatrixWorld(true);
  }
  box = new THREE.Box3().setFromObject(group);
  if (group.userData.plinth) box.union(new THREE.Box3().setFromObject(group.userData.plinth));
  const sz = box.getSize(new THREE.Vector3());
  shadow.scale.set(Math.max(sz.x, 1e-4) * 1.55, Math.max(sz.z, 1e-4) * 1.55, 1);
  shadow.position.set((box.min.x + box.max.x) / 2, 0.000002, (box.min.z + box.max.z) / 2);
  fit(box);
  renderer.render(scene, cam);
  if (group.userData.plinth) { scene.remove(group.userData.plinth); group.userData.plinth.geometry.dispose(); }
  sg.fillStyle = "#fff"; sg.fillRect(0, 0, OUT, OUT);
  sg.drawImage(renderer.domElement, 0, 0, OUT, OUT);
  return small.toDataURL("image/webp", 0.82);
};
window.ready = true;
</script></body></html>"""

DRIVER = r"""
const { chromium } = require(process.env.PW);
const fs = require('fs'), path = require('path');
const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const THREE_DIR = process.env.THREE_DIR, WORK = process.env.WORK, OUT = process.env.OUT;
const types = { '.js': 'text/javascript', '.html': 'text/html', '.glb': 'model/gltf-binary' };
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined, args: ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'] });
  const page = await browser.newPage();
  page.on('console', (m) => { if (m.type() === 'error') console.error('page:', m.text()); });
  page.on('pageerror', (e) => console.error('pageerror:', e.message));
  await page.route('http://parts.local/**', (route) => {
    const u = new URL(route.request().url());
    let f;
    if (u.pathname === '/') return route.fulfill({ body: process.env.PAGE, contentType: 'text/html' });
    if (u.pathname.startsWith('/three/')) f = path.join(THREE_DIR, u.pathname.slice(7));
    else if (u.pathname.startsWith('/glb/')) f = path.join(WORK, decodeURIComponent(u.pathname.slice(5)));
    if (!f || !fs.existsSync(f)) return route.fulfill({ status: 404, body: '' });
    route.fulfill({ body: fs.readFileSync(f), contentType: types[path.extname(f)] || 'application/octet-stream' });
  });
  await page.goto('http://parts.local/');
  await page.waitForFunction(() => window.ready === true, null, { timeout: 30000 });
  let n = 0;
  for (const j of jobs) {
    try {
      const data = await page.evaluate(([u, k]) => window.renderPart(u, k), ['/glb/' + encodeURIComponent(j.name + '.glb'), j.kind]);
      fs.writeFileSync(path.join(OUT, j.name + '.webp'), Buffer.from(data.split(',')[1], 'base64'));
      n++;
      if (n % 20 === 0) console.log(`${n}/${jobs.length}`);
    } catch (e) { console.error('fail', j.name, e.message.split('\n')[0]); }
  }
  console.log(`rendered ${n}/${jobs.length}`);
  await browser.close();
})();
"""


def find_chrome() -> str:
    shells = sorted(Path.home().glob("Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"))
    return str(shells[-1]) if shells else ""


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
        infos = list(ex.map(lambda f: export(f, a.force), fps))
    # parallel kicad-cli runs sometimes come back without their STEP models: redo those one at a time
    for _ in range(2):
        redo = [i for i, x in enumerate(infos) if x.get("has_model") and x["kind"] != "model"]
        for i in redo:
            infos[i] = export(infos[i]["footprint"], True)
    kinds = {}
    for i in infos:
        kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
    print(f"{len(infos)} footprints: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    for i in infos:
        if i["kind"] != "model":
            print(f"  {i['kind']}: {i['footprint']}")
    todo = [i for i in infos if i["kind"] != "missing" and (a.force or a.only or not (OUT / f"{i['name']}.webp").exists())]
    if not todo:
        print("all renders present")
        return
    jobs = WORK / "jobs.json"
    jobs.write_text(json.dumps(todo))
    driver = WORK / "driver.cjs"
    driver.write_text(DRIVER)
    env = dict(os.environ, PW=str(find_playwright()), CHROME=find_chrome(), THREE_DIR=str(APP / "vendor/three"), WORK=str(WORK), OUT=str(OUT),
               PAGE=PAGE.replace("__SIZE__", str(SIZE)))
    subprocess.run(["node", str(driver), str(jobs)], env=env, check=True)
    (WORK / "kinds.json").write_text(json.dumps({i["footprint"]: i["kind"] for i in infos}, indent=0))
    total = sum(p.stat().st_size for p in OUT.glob("*.webp"))
    print(f"{len(list(OUT.glob('*.webp')))} webp, {total / 1024:.0f} KB total -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

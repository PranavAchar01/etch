// Shrink a kicad-cli GLB for the web while keeping one node per footprint, named by refdes.
//
//   node library/optimize.mjs in.glb out.glb [simplifyError=0.0005]
//
// kicad-cli writes one primitive (with its own accessors) per STEP face: a 60 x 55 mm board is ~50k accessors
// and ~17 MB. Here: dedup -> join primitives that share a material inside each mesh -> weld -> simplify ->
// quantize -> meshopt. The scene graph is kept as is (no flatten/join across nodes, no instancing), so
// footprint nodes keep their names and transforms. Needs the libs installed in library/cache/node
// (render.py installs them).

import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const req = createRequire(path.join(here, 'cache', 'node', 'package.json'));
const load = (m) => import(pathToFileURL(req.resolve(m)).href);

const { NodeIO } = await load('@gltf-transform/core');
const { ALL_EXTENSIONS } = await load('@gltf-transform/extensions');
const F = await load('@gltf-transform/functions');
const { MeshoptEncoder, MeshoptDecoder, MeshoptSimplifier } = await load('meshoptimizer');

const [, , input, output, errArg] = process.argv;
const error = Number(errArg ?? 0.0005);

await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready, MeshoptSimplifier.ready]);
const io = new NodeIO()
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });
const doc = await io.read(input);
const root = doc.getRoot();

// one material per distinct colour
await doc.transform(F.dedup());

// join primitives with the same material + attribute layout inside each mesh
for (const mesh of root.listMeshes()) {
  const groups = new Map();
  for (const prim of mesh.listPrimitives()) {
    const key = [prim.getMaterial()?.getName() ?? '', root.listMaterials().indexOf(prim.getMaterial()), prim.getMode(),
      prim.listSemantics().sort().join(','), prim.getIndices() ? 'i' : 'n'].join('|');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(prim);
  }
  for (const prims of groups.values()) {
    if (prims.length < 2) continue;
    const joined = F.joinPrimitives(prims);
    for (const p of prims) { mesh.removePrimitive(p); p.dispose(); }
    mesh.addPrimitive(joined);
  }
}

// Footprint nodes are the children of the scene's top node and carry the refdes. Everything below them
// (mesh nodes named "=>[0:1:1:3]", numbered STEP sub-assemblies) loses its name, so a click resolves by
// walking up to the first named ancestor and refdes are the only names in the file.
const clear = (n) => { for (const c of n.listChildren()) { c.setName(''); clear(c); } };
for (const scene of root.listScenes()) {
  for (const top of scene.listChildren()) {
    for (const fp of top.listChildren()) clear(fp);
  }
}

await doc.transform(
  F.dedup(),
  F.prune({ keepLeaves: false, keepAttributes: false }),
  F.weld({}),
  F.simplify({ simplifier: MeshoptSimplifier, error, lockBorder: false }),
  F.prune({ keepLeaves: false }),
  F.quantize({ quantizePosition: 14, quantizeNormal: 8 }),
  F.meshopt({ encoder: MeshoptEncoder, level: 'high' }),
);
await io.write(output, doc);

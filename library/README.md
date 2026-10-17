# Board library (World)

Offline pipeline that finds open-source KiCad boards on GitHub and prerenders them, so the web page only
serves static files and never converts or renders anything at view time. Output format:
`deploy/EXPLORE_API.md` section 1.

```
python3 library/crawl.py --limit 200     # GitHub search via `gh` -> library/cache/{repos,boards}.json
python3 library/fetch.py                 # pcb, project, schematics, every 3D model -> library/cache/src/<slug>/
python3 library/render.py                # kicad-cli GLB + raytraced thumb + parts.json + meta.json
python3 library/build_index.py --prune   # deploy/etch-app/world/index.json + library/manifest.json
```

All steps are incremental: re-running skips repos/boards already done (`--refresh`, `--force`,
`--retry-failed` to redo, `--only <slug>...` for single boards). Hand-picked boards pinned to an exact
commit live in `PINNED_BOARDS` in `crawl.py`. Failures and timings: `library/cache/render_log.json`.
`library/manifest.json` (committed) lists every crawled board as queued / rendered / skipped with the
reason and its 3D-model summary.

Needs: `gh` (authenticated), KiCad 9+ (`$ETCH_KICAD_APP`, `/Applications/KiCad/KiCad.app` or the Homebrew
Caskroom; or set `KICAD_CLI`), Node (`render.py` installs `@gltf-transform/*` + `meshoptimizer` into
`library/cache/node`), `cwebp` (`brew install webp`), `git`. Python is stdlib only.

## Identical rendering

A board in the library must look like the original project in KiCad: the same parts, the same 3D models,
the same colours.

- **Every `(model ...)` path is resolved**, never substituted: KiCad's stock variables -> the installed
  KiCad 3D library, then the official library at the version the variable names (`KICAD8_3DMODEL_DIR` ->
  kicad-packages3D 8.0.9, `KISYS3DMOD` -> 5.1.12 then the KiCad 4 library), fetched from blobless git
  clones; `${KIPRJMOD}` / relative paths -> the repo at the pinned commit; absolute paths and custom
  variables -> the longest matching path in the repo or the KiCad library, then the same file name in the
  repo, the author's own published library (`AUTHOR_LIBS`, e.g. OLIMEX/KiCAD) and the KiCad library. A
  `.wrl` with a `.step` twin uses the STEP (as `kicad-cli --subst-models` does). Offsets, scales,
  rotations and hidden models are left exactly as in the pcb.
- **No geometry is changed to save bytes.** GLBs are only compressed: primitives with the same material
  are joined inside each mesh, then 16-bit position / 10-bit normal quantisation and meshopt. No
  simplification, decimation or instancing. Boards over 25 MB are skipped, not simplified.
- **Only identical boards are published** (`render.py --allow-missing` overrides). `meta.json` records
  `models: {total, resolved, hidden, missing: [{ref, path, reason}]}` and `identical`. A model counts as
  missing when its file is in neither the repo nor any KiCad library, or when kicad-cli cannot read it. One
  case does not break identity: a path into a real KiCad library folder for a model the library never
  shipped (e.g. `Connector_USB.3dshapes/USB_C_Receptacle_HRO_TYPE-C-31-M-12.step`), because KiCad draws
  nothing there either; such entries stay listed under `missing` with that reason.
- Colours come from the board's own stackup (`--use-board-stackup-colors` for the thumbnail; mask, silk
  and finish in `meta.board`).

## Licence policy

- Repos with no licence or GitHub's `NOASSERTION` are never imported.
- Only allow-listed licences are imported (MIT, Apache-2.0, BSD-\*, 0BSD, CC0-1.0, CC-BY-4.0, CC-BY-SA-4.0,
  CERN-OHL-P/W/S-2.0, GPL-\*, LGPL-\*, MPL-2.0, Unlicense), so every board has `copyable: true`.
- Each board is pinned to the commit it was rendered from (`meta.commit`) and links its licence file and
  author (`license_url`, `author_url`).

## Output

`deploy/etch-app/world/` is committed and served by Vercel. `render.py` stops at `--budget-mb` (default
150). `library/cache/` is gitignored.

GLB frame: metres, glTF Y-up, x = KiCad x, z = KiCad y, in KiCad's page coordinates (the board is not
centred; `meta.glb_bounds_m` gives the bounds). Each footprint is a node named by its refdes (e.g. `U6`,
`USB-UART1`) whose children carry the meshes; names below a footprint node are cleared. The board itself
(copper, mask, silk, vias, pads) is in unnamed nodes. Files use `EXT_meshopt_compression` and
`KHR_mesh_quantization`, so three.js needs `loader.setMeshoptDecoder(MeshoptDecoder)`.

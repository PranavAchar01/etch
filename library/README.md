# Board library (World)

Offline pipeline that finds open-source KiCad boards on GitHub and prerenders them, so the web page only
serves static files and never converts or renders anything at view time. Output format:
`deploy/EXPLORE_API.md` section 1.

```
python3 library/crawl.py --limit 200     # GitHub search via `gh` -> library/cache/{repos,boards}.json
python3 library/fetch.py                 # pcb + project/schematics + repo-local 3D models -> library/cache/src/<slug>/
python3 library/render.py --limit 150    # kicad-cli GLB + raytraced thumb + parts.json + meta.json
python3 library/build_index.py --prune   # deploy/etch-app/world/index.json
```

All steps are incremental: re-running skips repos/boards already done (`--refresh`, `--force`,
`--retry-failed` to redo). Failures and timings are in `library/cache/render_log.json`.

Needs: `gh` (authenticated), KiCad 9+ (`/Applications/KiCad/KiCad.app`, or set `KICAD_CLI`), Node (`npx`
runs `@gltf-transform/cli`), `cwebp` (`brew install webp`). Python is stdlib only.

## Licence policy

- Repos with no licence or GitHub's `NOASSERTION` are never imported.
- Only allow-listed licences are imported (MIT, Apache-2.0, BSD-\*, 0BSD, CC0-1.0, CC-BY-4.0, CC-BY-SA-4.0,
  CERN-OHL-P/W/S-2.0, GPL-\*, LGPL-\*, MPL-2.0, Unlicense), so every board has `copyable: true`.
- Each board is pinned to the commit it was rendered from (`meta.commit`) and links its licence file and
  author (`license_url`, `author_url`).

## Output

`deploy/etch-app/world/` is committed and served by Vercel, so keep it small: GLBs are meshopt-compressed
(target < 1.5 MB, boards over 4 MB are skipped) and `render.py` stops at `--budget-mb` (default 150).
`library/cache/` is gitignored.

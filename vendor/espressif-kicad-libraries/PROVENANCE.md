# Provenance: Espressif KiCad library (vendored)

- Repo: https://github.com/espressif/kicad-libraries (Espressif Systems, official)
- Tag: 3.2.1
- Commit: 1dfc3110895c9cd62daf332f49c49ee0ee200831 (committed 2026-07-24)
- Licence: CC-BY-SA 4.0 with the same design exception as the KiCad libraries (see LICENSE.md here). Boards made
  with these symbols, footprints and models are not bound by the share-alike terms; the library files themselves are.
- Fetched: 2026-10-17 by the Etch ESP32 task (Pranav approved "yes add ESP32").

Fetch command (shallow, blob-filtered, sparse; only the modules Etch uses):

```
git clone --depth 1 --branch 3.2.1 --filter=blob:none --sparse https://github.com/espressif/kicad-libraries.git
git sparse-checkout set --no-cone /LICENSE.md /README.md /symbols/Espressif.kicad_sym \
  /footprints/Espressif.pretty/ESP32-S3-WROOM-1.kicad_mod /footprints/Espressif.pretty/ESP32-C3-MINI-1.kicad_mod \
  /3dmodels/espressif.3dshapes/ESP32-S3-WROOM-1.STEP /3dmodels/espressif.3dshapes/ESP32-C3-MINI-1.STEP
```

Files (sha256):

| file | sha256 |
|---|---|
| symbols/Espressif.kicad_sym | 5e9785368a1cccbd904f8ceeab1c9165a8ef82ab00c99bf21275dc91749356cc |
| footprints/Espressif.pretty/ESP32-S3-WROOM-1.kicad_mod | a99796419aec905e6b0a12ddf266a63d546754070c587acf10585d70a161dddc |
| footprints/Espressif.pretty/ESP32-C3-MINI-1.kicad_mod | f1899b54ab6c007d50e76334f3fb8f340a827bc1d26f1ba24003333c214a7626 |
| 3dmodels/espressif.3dshapes/ESP32-S3-WROOM-1.STEP | 02e192087a1a2268ccbe7bf667ef04884370fc76c994484bf85654dfa7407ef6 |
| 3dmodels/espressif.3dshapes/ESP32-C3-MINI-1.STEP | 3ca5ac24b6943edf816eb1e75c525a42f13118cd5546b70812c1d21149bcdcab |
| LICENSE.md | 6eb43c2548ac6714db47ccbd62354bd194e918f606b071a5e9893680b941d75a |
| README.md | 62a18fc654599bf96c12b7435a9e1f6ae7dc474365df0d0d2021ba961086580f |

How Etch uses it: engine/etchlib/kicad.py maps the library nickname `Espressif` here (VENDOR_LIBS); the
footprints point their 3D model at `${KICAD8_3RD_PARTY}/3dmodels/com_github_espressif_kicad-libraries/...`
(the KiCad PCM install path), which agent/kicad_catalog.py resolves to `3dmodels/` in this folder.
To update: re-run the clone at a newer tag into a new folder, check pins and pads, then swap the path.

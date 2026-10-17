# Etch engine

Spec YAML (or a plain prompt) in. Out: a real KiCad board, real checks, a 3D board, and an `assembly.json` that a three.js front end can play back like Lego: bare board, then each part, then copper, then checks.

## Run

```sh
cd ~/helloworld/pcb-loop/engine
uv sync
uv run etch.py build specs/led_flasher.yaml --out out/led_flasher
uv run etch.py build specs/led_flasher_alt.yaml --out out/led_flasher_alt --base out/led_flasher   # add-feature diff
uv run etch.py build specs/led_flasher.yaml --out out/quick --no-glb                               # skip 3D
uv run etch.py prompt "a board that flashes a green LED" --spec specs/green.yaml --name green_flash
uv run etch.py find "red LED 0805"            # index first
uv run etch.py find "odd part" --lcsc C2040   # then easyeda2kicad by LCSC id
uv run pytest                                 # fast unit checks (no KiCad run)
```

Exit code is 0 only when every benchmark passes, every net is routed and every 3D model resolves.

Needs: KiCad 10 at `~/Applications/KiCad/KiCad.app` (kicad-cli plus its bundled Python with `pcbnew`) and `ngspice` on PATH. Nothing else. The 3D export is plain `kicad-cli`, so an agent with no 3D tooling (for example a Nemotron model driving the CLI) gets the same result.

## Pipeline

1. `etchlib/spec.py` loads the spec. `extends`, `add_parts`, `remove_parts` and `add_benchmarks` make a feature spec from a base spec. Every part is resolved against `parts/index.json`.
2. `etchlib/schematic.py` writes a real `.kicad_sch`: embedded library symbols, a global label on every pin, and project-local `sym-lib-table` and `fp-lib-table` files.
3. `kicad_side/layout.py` runs inside KiCad's Python. It loads the real footprints and points each 3D model at the absolute library `.step` (no `${KICAD*_3DMODEL_DIR}` and no `.wrl`). Then it places the parts (`placer.py`, a greedy search over a 0.5 mm grid and 4 rotations), routes them (`router.py`, a 2-layer A* grid router with vias and up to 4 rip-up passes), moves the reference labels off other parts and the board edge, and saves the `.kicad_pcb`.
4. These run in parallel under the heavy lock (`~/helloworld/.heavy-lock`, everything at `nice -n 19 taskpolicy -b`):
   - `kicad-cli` ERC
   - DRC with `--schematic-parity`
   - netlist export
   - gerbers and drill files
   - ngspice transient
5. `etchlib/glb.py` runs these `kicad-cli pcb export glb` jobs in parallel:
   - `board.glb`: the finished board with `--subst-models --include-tracks --include-pads --include-zones --include-silkscreen --include-soldermask`. It holds the real library STEP bodies, and every component node is named by its reference (R1, Q1...), so the app can split the file into parts.
   - `board_bare.glb`: substrate, pads, mask and silk, with no components.
   - `tracks.glb`: copper tracks and vias only.
   - `parts/<ref>.glb`: one per part, via `--component-filter`. All of these share one frame.
6. `etchlib/build.py` writes `assembly.json`, `assembly.diff.json` (incremental builds only), `layout.json`, `sim.json` and `report.json` (stage timings, file sizes, benchmark results).

### Coordinates

- `assembly.json` uses mm, with the origin at the board's top-left corner and y pointing down.
- The GLBs are in metres, glTF Y-up. glTF x = board x, glTF z = board y, and the board's top face sits at y = +1.6 mm. Measured: J1 at (5.095, 8.445) mm in assembly.json is the node translation (5.10, 1.6, 8.44) mm in `board.glb`.
- `board.glb` in assembly.json is a URL path from the pcb-loop root, for example `/engine/out/led_flasher/board.glb`.

## Parts

- `parts/index.json` holds **100 verified parts**, built by `parts/build_index.py`. Each entry's symbol, footprint and STEP model were checked to exist in the installed KiCad 10.0.6 libraries.
- Two parts were rejected, and the index records both:
  - `mpu6050`: no 3D model.
  - `nrf54l15_module`: KiCad 10 has no nRF54L15 symbol.
- `find_part(name, lcsc_id=None, search=None, allow_network=False)` tries three sources in order:
  1. The index.
  2. easyeda2kicad (`uvx easyeda2kicad --full --lcsc_id=...`), source `"LCSC via easyeda2kicad"`. It pulls the manufacturer's STEP. If no STEP comes back, the status is `no_model`; a body is never generated.
  3. A web search callable you pass in, which returns leads only. No keys live in any file.
- 3D models only ever come from KiCad's library or the manufacturer's STEP. The build checks every `(model ...)` path in the `.kicad_pcb` and fails if one is missing or is not a `.step`.

## Measured (this Mac, 2026-10-15)

`led_flasher` is a 2-transistor astable on 5 V with a red LED: 10 parts on a 24 x 17 mm 2-layer board.

| build | total | place + route | checks + sim + fab | GLB |
|---|---|---|---|---|
| full, with GLB (4 runs) | 6.7 to 8.5 s | 0.8 to 1.7 s | 1.7 to 2.0 s | 3.2 to 4.7 s |
| full, `--no-glb` | 2.5 to 2.6 s | 0.8 to 1.4 s | 1.8 s | none |
| incremental alt, with GLB (adds D2, R5) | 5.4 to 5.8 s | 0.55 to 0.8 s | 2.0 s | 2.3 to 2.5 s |

Checks on all three boards (`led_flasher`, `led_flasher_alt`, and `green_flash` built from a prompt):
- ERC: 0 errors, 0 warnings.
- DRC: 0 violations, 0 warnings, 0 unconnected, schematic parity 0.
- Netlist mismatches: 0.
- 3D models: all resolve (0 missing).

ngspice results (2N3904 published model; generic LED model):

| board | blink | LED peak | duty | supply avg |
|---|---|---|---|---|
| led_flasher | 1.699 Hz | 13.59 mA | 0.445 | 9.23 mA |
| led_flasher_alt (two LEDs) | 1.872 Hz | 13.64 mA and 14.87 mA | | 15.18 mA |
| green_flash (from prompt) | 1.765 Hz | 11.66 mA | 0.401 | |

In `led_flasher_alt`, both LEDs are lit at once only 0.2 % of the time, so they alternate.

The incremental build keeps the base placement and every track whose net did not change. `assembly.diff.json` holds only the new steps: D2, R5, 4 traces and 2 vias. Base part GLBs are copied, not re-exported.

`out/led_flasher` sizes:

| file | size |
|---|---|
| board.glb | 857 KB |
| board_bare.glb | 345 KB |
| tracks.glb | 310 KB |
| parts/ (10 GLBs) | 344 KB |
| gerbers + drill | 152 KB |
| assembly.json | 14 KB |
| whole folder | 2.8 MB |

`assembly.json` has 40 steps: 1 board, 10 parts, 22 traces, 7 vias.

## What is real and what is not

Real:
- KiCad library symbols, footprints and STEP models.
- KiCad's own ERC and DRC, including schematic parity.
- Gerbers.
- ngspice, run on a netlist made from the same part and net list as the board.
- KiCad's own GLB export.

Not real, or not done:
- **Router.** The router is my own grid A*, not Freerouting. Freerouting needs Java, and Java is not installed. It is fine for small 2-layer boards but has not been tried on dense ones.
- **LED model.** The LED SPICE model is a generic colour-typical diode, not fitted to the exact part, so the LED currents are estimates.
- **No compression.** gltfpack is not installed, so the GLBs are not compressed.
- **easyeda2kicad.** It was not run over the network here. The command and the STEP check are wired up, but no LCSC part has been fetched yet.
- **Web search.** The hook is tested only with a stub callable. No You.com key is wired in.
- **Prompt routing.** Prompt to spec covers one template only (`astable_led`, with an LED colour swap). Any other circuit needs a hand-written spec.
- **Speed.** GLB export is the slowest stage, at about 2.4 s for `board.glb` alone. For live streaming, the front end can play parts from `parts/<ref>.glb` or split `board.glb` by node name while later steps finish.

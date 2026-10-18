# Etch

**Claude Code for circuit boards.** Say the board you want. A team of agents picks real KiCad parts, places and routes the board, checks it, and hands you a board you can send to a fab, in 3D.

**Live:** https://etch.chat

<p align="center"><img src="docs/media/build.gif" width="860" alt="Typing 'A calculator with an OLED screen' and Etch building it"></p>

## What you can do

<table>
<tr>
<td width="50%"><b>Build anything</b><br>One sentence in, a routed and checked KiCad board out. KiCad's own renders of the board as it's assembled:<br><img src="docs/media/build-render.gif" alt="KiCad render frames of the calculator board"></td>
<td width="50%"><b>See it in 3D</b><br>Every part is its real 3D model. Orbit, zoom, flip it over.<br><img src="docs/media/board-3d.gif" alt="The finished calculator board in 3D"></td>
</tr>
<tr>
<td><b>Ask about any part</b><br>Click a part and ask what it does. Answers are pinned to the part.<br><img src="docs/media/ask.gif" alt="Asking what U1 does"></td>
<td><b>Restyle it</b><br>Solder mask, silkscreen and finish change live.<br><img src="docs/media/restyle.gif" alt="Changing the mask colour and finish"></td>
</tr>
<tr>
<td><b>Start from an open-source board</b><br>Pick any board from the library and change it.<br><img src="docs/media/import.gif" alt="Opening an open-source ESP32 board in the editor"></td>
<td><b>Export real files</b><br>KiCad project, Gerbers, drill and BOM, checked again on export. Or the 3D model as GLB.<br><img src="docs/media/export.gif" alt="Exporting the KiCad project and Gerbers"></td>
</tr>
<tr>
<td colspan="2"><b>Explore 135 open-source boards</b><br>Real KiCad projects from GitHub, prerendered with every original 3D part model, each credited to its author and licence. Your own builds land in My boards.<br><img src="docs/media/library.gif" width="760" alt="Browsing the board library"></td>
</tr>
</table>

## How it works

<p align="center"><img src="docs/media/architecture.gif" width="860" alt="Animated architecture: browser, tunnel, backend, agents, KiCad engine and the library pipeline"></p>

This is the "How Etch works" diagram from the **?** button in the app, playing its two tours: a request travelling from you to a checked 3D board, and a library board being built.

1. **You ask**, by voice or text. Speech-to-text runs locally (Parakeet).
2. **The agent team works it out.** A picker chooses parts, values and wiring, a part scout finds anything missing, and the lead tracks progress and decides when it's done.
3. **The KiCad 10 engine builds it.** Placement and routing, then the fab files and the 3D model.
4. **The agents check their own work.** Every try runs ERC, DRC, schematic-to-PCB parity, unconnected nets, routing, 3D models, an ngspice simulation and the benchmarks the agents wrote for the request. A failure goes back to the picker as a repair, up to three tries, then Etch stops and says why.
5. **You get the board** in 3D, ready to question, restyle, change or export.

### Built on

| | How Etch uses it |
| --- | --- |
| **Crusoe Managed Inference** | The default endpoint for every agent call (`api.inference.crusoecloud.com/v1`), paced to 28 requests a minute across all agents, with OpenAI as a fallback. |
| **NVIDIA Nemotron** | Nemotron 3 Super 120B picks parts and repairs failed designs; Nemotron 3.5 Lightning 30B scouts parts. Thinking is turned off for speed. |
| **You.com** | Searches for parts that aren't in the KiCad library, so the scout can turn them into real KiCad parts. |

## Run it

Needs a Mac on Apple silicon with [Homebrew](https://brew.sh), a Crusoe API key (`CRUSOE_API_KEY`, or the Keychain entry `crusoe-api-key`) and an OpenAI API key for the fallback and spoken replies. A You.com key (`YOU_API_KEY`) turns on web part search.

```bash
git clone https://github.com/PranavAchar01/etch.git
cd etch
./setup.sh
./run.sh
```

`setup.sh` installs uv, ngspice, node, cloudflared and KiCad 10 if they're missing, builds the engine's Python env and downloads the local speech-to-text model (about 500 MB). It asks once for your OpenAI key and an access code of your choice, and stores both in the macOS Keychain.

`run.sh` starts speech-to-text, the backend and the page, then opens http://127.0.0.1:8766. Enter the access code you chose. `./run.sh --public` also opens a Cloudflare tunnel and redeploys the Vercel project linked in `deploy/etch-app` so the public page points at it.

## Layout

| Path | What |
| --- | --- |
| `deploy/etch-app/` | The static site Vercel serves: the Explore library and homepage (`explore.html`, `/` rewrites to it), the board editor (`board.html`, `board.html?new=1` builds a new board from a description) and the UI kit demo (`kit.html`), with `js/` and `css/`. |
| `docs/media/` | The GIFs in this README, cut from recordings of the live site. |
| `deploy/etch-app/world/` | The prerendered open-source boards Explore shows (GLB, thumbnail, parts, credits per board). Built by `library/`. |
| `deploy/etch_public.py` | The backend API the page calls (port 3439). Routes and build events: [deploy/API.md](deploy/API.md). Board view routes are in `deploy/explore.py`. |
| `library/` | Finds licensed KiCad boards on GitHub and prerenders them into `deploy/etch-app/world/` ([library/README.md](library/README.md)). |
| `deploy/*.sh` | How it runs on the host Mac: launchd restart, Cloudflare tunnel, Vercel redeploy, STT keeper. |
| `agent/` | The lead agent and sub-agents that write and repair board specs ([agent/README.md](agent/README.md)). |
| `engine/` | Spec in, verified KiCad board out ([engine/README.md](engine/README.md)). |
| `engine/parts/` | Part index, KiCad catalog, classifier, pre-rendered part images and parts fetched from LCSC or GitHub (`cache/`). |
| `studio/kicad_frames.py` | Board render frames for the page. |
| `studio/stt/stt_server.cjs` | Local speech-to-text (Parakeet on sherpa-onnx, port 3421). |
| `vendor/espressif-kicad-libraries/` | ESP32 parts from Espressif's KiCad library 3.2.1 (CC-BY-SA 4.0, see its PROVENANCE.md). |

Build outputs (`engine/out/`) and the usage meter (`qa/`) are created at run time and are not in the repo.

## Deploying Explore

`deploy/redeploy.sh` uploads `deploy/etch-app/` as is, including `world/` (about 155 MB). Before running it:

1. Restart the backend so it loads `deploy/explore.py` (`deploy/restart.sh`), and check `/etch/health`.
2. Open `explore.html` and `board.html?id=gh:OLIMEX/ESP32-POE/HARDWARE/ESP32-PoE-hardware-revision-M2` against the local backend (`?api=http://127.0.0.1:3439`). Ask a question, pin a comment on a part and delete it.
3. `world/` holds GPL, CC-BY-SA and CERN-OHL-S boards. Each board page shows its author, repo, commit and licence; keep those visible.

To add or refresh boards: see [library/README.md](library/README.md), then commit `world/` and `library/manifest.json`.

## Settings

Environment variables, all optional:

- `ETCH_MODEL` and `ETCH_SUB_MODEL`: models for the lead agent and the sub-agents (default `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B` and `nvidia/Nemotron-3.5-Lightning-30B-A3B`). `ETCH_BASE_URL` points at any OpenAI-compatible endpoint (default Crusoe Managed Inference). See [agent/config.py](agent/config.py).
- `CRUSOE_API_KEY`, `YOU_API_KEY`: used instead of the Keychain entries when set.
- `OPENAI_API_KEY` and `ETCH_ACCESS_CODE`: used instead of the Keychain entries when set.
- `ETCH_KICAD_APP`, `ETCH_STT_HOME`, `ETCH_HEAVY_LOCK`: written to `.etch.env` by `setup.sh`.

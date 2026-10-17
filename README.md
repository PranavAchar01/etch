# Etch

Say what circuit board you want and Etch designs it with real KiCad parts, then routes it, checks it (ERC, DRC, ngspice) and shows it in 3D. Live at https://etch-app-theta.vercel.app.

## Run it

Needs a Mac on Apple silicon with [Homebrew](https://brew.sh) and an OpenAI API key.

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
| `deploy/etch-app/` | The page (static: `index.html`, `app.js`, `viewer3d.js`). This folder is what Vercel serves. |
| `deploy/etch_public.py` | The backend API the page calls (port 3439). |
| `deploy/*.sh` | How it runs on the host Mac: launchd restart, Cloudflare tunnel, Vercel redeploy, STT keeper. |
| `agent/` | The lead agent and sub-agents that write and repair board specs ([agent/README.md](agent/README.md)). |
| `engine/` | Spec in, verified KiCad board out ([engine/README.md](engine/README.md)). |
| `engine/parts/` | Part index, KiCad catalog, classifier, pre-rendered part images and parts fetched from LCSC or GitHub (`cache/`). |
| `studio/kicad_frames.py` | Board render frames for the page. |
| `studio/stt/stt_server.cjs` | Local speech-to-text (Parakeet on sherpa-onnx, port 3421). |
| `vendor/espressif-kicad-libraries/` | ESP32 parts from Espressif's KiCad library 3.2.1 (CC-BY-SA 4.0, see its PROVENANCE.md). |

Build outputs (`engine/out/`) and the usage meter (`qa/`) are created at run time and are not in the repo.

## Settings

Environment variables, all optional:

- `ETCH_MODEL` and `ETCH_SUB_MODEL`: models for the lead agent and the sub-agents (default `gpt-5-mini` and `gpt-5-nano`). `ETCH_BASE_URL` points at any OpenAI-compatible endpoint. See [agent/config.py](agent/config.py).
- `OPENAI_API_KEY` and `ETCH_ACCESS_CODE`: used instead of the Keychain entries when set.
- `ETCH_KICAD_APP`, `ETCH_STT_HOME`, `ETCH_HEAVY_LOCK`: written to `.etch.env` by `setup.sh`.

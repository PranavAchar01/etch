# Deploying the Etch backend

Two ways to run it: the Mac setup it runs on today, and a Linux container.

## Mac

`./setup.sh` installs uv, ngspice, node, cloudflared and KiCad 10 and creates `.env` from `.env.example`.
Then `./run.sh` starts the API on 127.0.0.1:3439 (`./run.sh --tunnel` also opens a Cloudflare tunnel, see
README.md). Keys are read in this order: the process environment, then `.env`, then the macOS Keychain:

| what | env var / `.env` | Keychain service |
|---|---|---|
| Model endpoint (default Crusoe) | `CRUSOE_API_KEY`, or `ETCH_API_KEY` for any `ETCH_BASE_URL` | `crusoe-api-key` (or `ETCH_API_KEY_SOURCE`) |
| OpenAI (fallback, TTS, prompt rewriter, Decisions classifier) | `OPENAI_API_KEY` | `openai-api-key` |
| You.com part search (optional) | `YOU_API_KEY` | `you-api-key` |

A model server on localhost (Ollama, vLLM) needs no key.

The official backend runs under launchd (`com.etch.app.backend`) on the maintainers' Mac. Restart only the
backend after a code change there: `launchctl kickstart -k gui/$(id -u)/com.etch.app.backend`.

## Linux container

The `Dockerfile` builds Ubuntu 24.04 with KiCad 10 from the KiCad team's PPA
(`ppa:kicad/kicad-10.0-releases`: kicad-cli, the pcbnew Python module, symbols, footprints, 3D models),
ngspice, uv and the engine environment. The API listens on port 3439.

```sh
cp .env.example .env      # add your key
docker compose up --build
curl http://127.0.0.1:3439/etch/health
```

or without compose:

```sh
docker build -t etch-backend .
docker run --rm -p 127.0.0.1:3439:3439 --env-file .env -e ETCH_PUBLIC_PORT=3439 etch-backend
```

Keys reach the container only as environment variables when it starts. None are copied into the
image (`.dockerignore` keeps out `.env*`, `.etch.env`, keys, outputs, caches and `qa/`).

The image is large (KiCad 3D models are a few GB). Build outputs go to `/app/engine/out` inside the
container; mount a volume there to keep builds across restarts.

### Environment

| var | default | meaning |
|---|---|---|
| `ETCH_BIND` | `127.0.0.1` (image: `0.0.0.0`) | address the API binds |
| `ETCH_PUBLIC_PORT` | `3439` | API port |
| `ETCH_ORIGINS` | built-in list | comma-separated CORS origins allowed to call the API |
| `ETCH_HEAVY_LOCK` | `~/helloworld/.heavy-lock` (image: `/tmp/etch-heavy-lock`) | one-heavy-job lock directory |
| `ETCH_BASE_URL` | Crusoe `https://api.inference.crusoecloud.com/v1` | primary OpenAI-compatible endpoint |
| `ETCH_MODEL` / `ETCH_SUB_MODEL` | Nemotron 3 Super 120B / Nemotron 3.5 Lightning 30B | lead and sub-agent models |
| `ETCH_FALLBACK` | `openai` | `off` disables the automatic fallback |
| `ETCH_FALLBACK_MODEL` / `ETCH_FALLBACK_SUB_MODEL` | `gpt-5-mini` / `gpt-5-nano` | models used after a primary error |
| `ETCH_PRIMARY_TIMEOUT` | `40` | seconds before a primary call counts as failed |
| `ETCH_CLASSIFIER` | unset | `1` runs NVIDIA's prompt-task-and-complexity-classifier (NVIDIA Open Model License, ~740 MB download on first use, needs `torch` and `transformers`) before the first pick: complexity below `ETCH_CLASSIFIER_THRESHOLD` (default `0.3`) picks with `ETCH_SUB_MODEL`, the rest with `ETCH_MODEL`; repairs always use `ETCH_MODEL`. Without the packages it logs `route: off` and changes nothing |
| `ETCH_CLASSIFIER_DIR` / `ETCH_CLASSIFIER_THREADS` | HF cache / `2` | local model folder; CPU threads for the classifier |
| `ETCH_THINKING` | `off` | `on` turns Nemotron reasoning back on (about 8x slower per call) |
| `ETCH_KICAD_CLI` / `ETCH_KICAD_PY` / `ETCH_KICAD_SHARE` | `/usr/bin/kicad-cli`, `/usr/bin/python3`, `/usr/share/kicad` | KiCad paths on Linux |
| `ETCH_TTS_MODEL` / `ETCH_TTS_VOICE` | `gpt-4o-mini-tts-2025-12-15` / `cedar` | Etch's spoken voice (OpenAI `/v1/audio/speech`, needs `OPENAI_API_KEY`) |
| `ETCH_STT_URL` | `http://127.0.0.1:3421/stt` | speech-to-text server (not in the image; `/etch/stt` returns 503 without it) |

### Not in the container

- Speech-to-text (Parakeet via node, `studio/stt/`). Voice input falls back to typing.
- The Cloudflare tunnel. Run `cloudflared tunnel --url http://localhost:3439` on the host (or `scripts/tunnel.sh`), or put the container behind your own HTTPS proxy.

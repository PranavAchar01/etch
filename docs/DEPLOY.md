# Deploying the Etch backend

Two ways to run it: the Mac setup it runs on today, and a Linux container.

## Mac (current)

`./setup.sh` installs uv, ngspice, node, cloudflared and KiCad 10. Then `./run.sh` starts the API on
127.0.0.1:3439. On his Mac it runs under launchd (`com.etch.app.backend`) behind a Cloudflare tunnel.
Keys are read from the Keychain at run time, never from files:

| what | Keychain service | env var (wins when set) |
|---|---|---|
| Crusoe (default model provider) | `jarvis-vault/Crusoe` | `CRUSOE_API_KEY` |
| OpenAI (fallback, TTS, Decisions classifier) | `openai-api-key` | `OPENAI_API_KEY` |
| You.com part search (optional) | `jarvis-vault/You.com` | `YOU_API_KEY` |

Restart only the backend after a code change: `launchctl kickstart -k gui/$(id -u)/com.etch.app.backend`.

## Linux container

The `Dockerfile` builds Ubuntu 24.04 with KiCad 10 from the KiCad team's PPA
(`ppa:kicad/kicad-10.0-releases`: kicad-cli, the pcbnew Python module, symbols, footprints, 3D models),
ngspice, uv and the engine environment. The API listens on port 3439.

```sh
docker build -t etch-backend .
docker run --rm -p 3439:3439 \
  -e CRUSOE_API_KEY -e OPENAI_API_KEY -e YOU_API_KEY \
  -e ETCH_ORIGINS=https://your-frontend.example \
  etch-backend
curl http://127.0.0.1:3439/etch/health
```

Keys are passed only as environment variables when the container starts. None are copied into the
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
- The Cloudflare tunnel. Put the container behind your own HTTPS proxy.

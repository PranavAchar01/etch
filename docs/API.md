# Etch backend API

Base: the backend origin (port 3439 locally). All responses are JSON unless noted. CORS is limited to
`ETCH_ORIGINS`. Endpoints marked **code** need the header `X-Etch-Code: <access code>` and return
`401 {"error": "code"}` without it. Build ids look like `live-1792258295776`.

This document covers what the API does today. Changes stay backward compatible: new fields and new
event types can appear, existing ones keep their meaning.

## Endpoints

| method | path | code | body / query | response |
|---|---|---|---|---|
| GET | `/etch/health` | no | | `{ok, busy, queue, stt}` |
| POST | `/etch/auth` | checks it | | `200 {ok:true}`, `401 {ok:false,error}`, `429` after too many tries |
| POST | `/etch/live` | yes | `{mode, text, base?}` | `{job, token}` |
| GET | `/etch/job` | yes | `?id=<job>&since=<N>` | `{events, next, done, state, stage, run}` |
| POST | `/etch/cancel` | yes | `{job, token}` | `200 {ok:true,...}`, `403` wrong token, `409` cannot cancel |
| GET | `/etch/builds` | yes | | `{builds:[...], now}` running and recent builds |
| GET | `/etch/run` | yes | `?id=live-N` | `{events, build}` a finished build's events for replay |
| GET | `/etch/thumb` | no | `?id=live-N` | PNG, last render frame |
| GET | `/etch/zip` | yes | `?id=live-N` | ZIP, fab package (KiCad project, Gerbers, BOM) |
| GET | `/etch/layers` | yes | `?id=live-N` | copper pours (zone fills) for the 3D view |
| GET | `/etch/glb` | yes | `?id=live-N` | `model/gltf-binary` board model |
| GET | `/etch/parts` | yes | `?id=live-N` | `{ok, ...}` placed parts for the viewer |
| GET | `/etch/export` | yes | `?id=live-N` | export check result (`ok`, `reasons`) |
| GET | `/etch/export.zip` | yes | `?id=live-N` | ZIP when checks pass, else `409 {error, reasons}` |
| GET | `/etch/piece/<16 hex>.png` | no | | PNG of one KiCad library part (long cache) |
| GET | `/etch/files/engine/out/live-*/frames/*.png`, `.../frames.json`, `.../sim.json` | no | | render frames and sim data |
| POST | `/etch/stt` | yes | raw float32 16 kHz mono PCM, < 45 s | transcript JSON from the local STT server, `503` when offline |
| POST | `/etch/tts` | yes | `{text}` | `audio/mpeg`, `503` when offline |

`/etch/live` fields:

- `mode`: `design` (new board from `text`), `modify` (change the board `base`), `bench` (re-run the
  benchmarks on `base`).
- `text`: what the board should do, or the change. Whitespace is collapsed and length capped.
- `base`: a build id; required for `modify` and `bench`, and that build must have finished.
- Errors: `400` missing text or base, `429` queue full or rate limit hit.
- `token` goes only to the browser that started the run; keep it to cancel.

Polling `/etch/job`: start with `since=0`, then pass the returned `next`. Stop when `done` is true.
`state` is `queued | running | done | failed | cancelled`; `stage` is a short human label; `run` is the
build id once the engine names it.

## Events

Every event has `ts` (unix seconds), `t` (seconds since the run started), `run`, `agent`, `type`, plus
the fields below. The server itself adds `queued`, `error` and `cancelled` events with `agent: "lead"`.

### Run

| agent / type | fields |
|---|---|
| `lead start` | `ask` |
| `lead say` | `text` (the frontend speaks this aloud) |
| `lead done` | `ok, board, iterations, route, timings, classifier` |
| `lead error` | `error` |

### Part selection

| agent / type | fields |
|---|---|
| `classifier start` / `batch` / `done` | `backend, families, yes, no, seconds, detail` (done) |
| `filter done` | `kept, rejected, seconds` |
| `picker start` | `kept, model, effort` |
| `picker pick` | `parts, recipe, scenarios, benchmarks, say, model, seconds` |
| `picker repair` | `iteration, parts, say, seconds` |
| `pieces show` / `confirm` / `error` | per-part render (`k, img, cached`), confirmed list (`parts, drop`) |

### Model calls

| agent / type | fields |
|---|---|
| `<agent> llm` | `model, provider` (`crusoe` or `openai`), `fallback` (bool), `seconds, tokens_in, tokens_out, tool_calls` |
| `<agent> llm_error` | `model, error, seconds` (a primary call failed) |
| `<agent> llm_fallback` | `model, to, reason` (switched to OpenAI; stays switched for 10 minutes) |

### Sourcing parts not in the library

| agent / type | fields |
|---|---|
| `sourcing start` | `what, search` |
| `sourcing done` | `what, ok, key, provenance, seconds`; when `ok` is false also `status, reason, searched` (tiers tried). No part is drawn in that case. |
| `part-scout start` | `query` |
| `part-scout tool_call` | `tool` (`easyeda2kicad`, `github_code_search`, ...), tool-specific fields |
| `part-scout web` | `provider: "you.com"`, then either `query, hits, urls` (search) or `found, lcsc, footprint, kicad_footprint, text` (result). `footprint: null` means no real footprint was found and nothing was drawn. `text` reads like "scout you.com found X: LCSC C..., footprint ...". |

### Assembly loop

| agent / type | fields |
|---|---|
| `assembly seed` | `base, iteration, note` |
| `assembly iteration` | `iteration, phase, decision, why, seconds` |
| `assembly analog` | `notes` (what the analog solver changed: resistor values, transistor swap), `findings` (numeric checks that still fail). Quiet: not spoken. |
| `assembly firmware` | `iteration, pins, tests` |
| `assembly built` | `iteration, board, tracks, seconds, stages_s` |
| `assembly gate` | `iteration, gate, check, ok, value` |
| `benchmarks check` | one benchmark row (scenario, metric, value, band, ok) |
| `benchmarks done` | totals: passed, failed |

### Output

| agent / type | fields |
|---|---|
| `render start` / `render` / `done` | `quality`; done: `count, frames, seconds, lock_wait_s` |
| `export done` | `project, gerbers, bom_lines, zip, zip_bytes, seconds` |

Unknown agent/type pairs should be ignored by clients.

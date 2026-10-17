# Etch backend API

Base: the backend origin (port 3439 locally). All responses are JSON unless noted. CORS is limited to
`ETCH_ORIGINS`. There is no access code: every endpoint is open, protected by per-IP rate limits and a
daily run cap. Build ids look like `live-1792258295776`.

This document covers what the API does today. Changes stay backward compatible: new fields and new
event types can appear, existing ones keep their meaning.

## Endpoints

| method | path | body / query | response |
|---|---|---|---|
| GET | `/etch/health` | | `{ok, busy, queue, fast_busy, fast_queue, stt}` |
| POST | `/etch/live` | `{mode, text, base?}` | `{job, token}` |
| GET | `/etch/job` | `?id=<job>&since=<N>[&wait=<s>]` | `{events, next, done, state, stage, run, lane}` |
| POST | `/etch/cancel` | `{job, token}` | `200 {ok:true,...}`, `403` wrong token, `409` cannot cancel |
| GET | `/etch/builds` | | `{builds:[...], now}` running and recent builds |
| GET | `/etch/run` | `?id=live-N` | `{events, build}` a finished build's events for replay |
| GET | `/etch/thumb` | `?id=live-N` | PNG, last render frame |
| GET | `/etch/zip` | `?id=live-N` | ZIP, fab package (KiCad project, Gerbers, BOM) |
| GET | `/etch/layers` | `?id=live-N` | copper pours (zone fills) for the 3D view |
| GET | `/etch/glb` | `?id=live-N` | `model/gltf-binary` board model |
| GET | `/etch/parts` | `?id=live-N` | `{ok, ...}` placed parts for the viewer |
| GET | `/etch/export` | `?id=live-N` | export check result (`ok`, `reasons`) |
| GET | `/etch/export.zip` | `?id=live-N` | ZIP when checks pass, else `409 {error, reasons}` |
| GET | `/etch/piece/<16 hex>.png` | | PNG of one KiCad library part (long cache) |
| GET | `/etch/files/engine/out/live-*/frames/*.png`, `.../frames.json`, `.../sim.json` | | render frames and sim data |
| POST | `/etch/stt` | raw float32 16 kHz mono PCM, < 45 s | transcript JSON from the local STT server, `503` when offline |
| POST | `/etch/tts` | `{text}` | `audio/mpeg`, `503` when offline |

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

Long-poll (optional): add `wait=<seconds>` (max 25, `400` if not a number) and the request holds until there
is an event past `since`, the job finishes, or the wait runs out (then `events` is empty). Without `wait`
it answers at once, as before.

Lanes: `lane` is `fast` for jobs that need no model call (stopped by the prefilter, a stored board, a
recipe, or a deterministic edit) and `full` otherwise. Fast jobs have their own one-at-a-time slot, so they
do not wait behind a full build. `/etch/builds` rows carry `lane` too; `/etch/health` reports the fast
slot as `fast_busy` and `fast_queue`.

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
| `lead timing` | last event before `done`: `lane, queued_s, spawn_to_first_event_s, run_s, total_s, stages` (seconds per stage label) |
| `lead router` | only with `ETCH_CLASSIFIER=1`: `route` (`recipe`, `simple`, `complex`, or `off` when the classifier cannot load), `model` (first picker model, or null), `why, task_type, complexity` (0 to 1), `seconds, load_seconds` |

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
| `<agent> llm_hedge` | `model, to, after_s`: the primary had not answered after `after_s` (12 by default), so the fallback was started in parallel |
| `<agent> llm_hedge_won` | `winner` (model whose answer was used) |

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
| `assembly seed` | `base, iteration, note`; when the kept layout failed and a full build follows, also `failed` (check names) and `seconds` |
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

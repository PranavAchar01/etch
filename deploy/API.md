# Etch backend API

What `deploy/etch_public.py` serves today, and how the page (`deploy/etch-app/app.js`) uses each route. UI changes
should line up with this file. The board view routes (`/etch/ask`, `/etch/comments*`, `/etch/change`) are written in
`deploy/explore_api.py`, which `etch_public.py` hands those paths to; they are listed under "Board view" below and
designed in [EXPLORE_API.md](EXPLORE_API.md) (with the static `world/` library).

If you change a route, an event or a field here, change `etch_public.py` (or `explore_api.py`, or the engine that
emits the event) in the same commit.

## Basics

| | |
| --- | --- |
| Base URL | `config.json` → `api` (the Cloudflare tunnel URL in production). `?api=http://127.0.0.1:3439` overrides it; `./run.sh` opens the page that way. |
| Port | 3439 (`ETCH_PUBLIC_PORT`). Speech-to-text runs separately on 3421 and is reached only through `/etch/stt`. |
| CORS | Allowed origins: `https://etch-app*.vercel.app`, `http://localhost:*`, `http://127.0.0.1:*` (`ETCH_ORIGINS` overrides). A new deploy URL outside that pattern gets CORS errors. |
| Auth | Header `X-Etch-Code: <access code>` on every route except `/etch/health`, `/etch/thumb`, `/etch/piece/*` and `/etch/files/*`. The page asks for the code once and keeps it in `localStorage["etch-code"]`. |
| Errors | JSON `{"error": "..."}` with a 4xx/5xx status. `401 {"error": "code"}` means a missing or wrong code: show the code prompt (`app.js` `api()` does this). Most other `error` strings are written to be shown to the user as is. |
| Polling, not SSE | Cloudflare quick tunnels don't carry SSE, so build progress is polled from `/etch/job`. |
| Paths | Every path the API returns is relative to the repo root (`/engine/out/live-…/…`). Fetch build files through `/etch/files` + that path. |

### Limits

| What | Limit | Response when hit |
| --- | --- | --- |
| Builds (`/etch/live`) | 10 per IP per hour, 80 per day overall (`ETCH_RUNS_PER_IP_HOUR`, `ETCH_RUNS_PER_DAY`) | 429 |
| Queue | 1 build runs at a time and at most 3 wait in line | 429 "I am busy with other builds…" |
| Build time | 15 minutes, then the run is stopped | run ends `failed` |
| Request text | 400 characters (longer text is cut, whitespace collapsed) | |
| Speech-to-text | 60 requests per IP per 10 min, 45 s of audio per request | 429 / 413 |
| Text-to-speech | 120 requests per IP per 10 min, plus a daily spend guard | 429 / 503 |
| Wrong access codes | 10 per IP per 10 min | 429 on `/etch/auth` |
| Questions (`/etch/ask`, new comments, replies) | 60 per IP per 10 min, 1500 per day overall (`ETCH_ASKS_PER_IP_10MIN`, `ETCH_ASKS_PER_DAY`), plus the OpenAI budget guard | 429 / 503 |
| Comments | 300 per board, 60 messages per thread, 400 characters per message | 409 / cut |

## Ids

| Id | Looks like | Where it comes from |
| --- | --- | --- |
| job | `kUwJeQ1D0sYYAokT` (random) | `POST /etch/live`. Lives in memory for about an hour after the build ends; lost on backend restart. |
| token | random | `POST /etch/live`. Only the browser that started the build gets it; needed to cancel. The page keeps it in `localStorage["etch-tokens"]`. |
| run | `live-<ms timestamp>` e.g. `live-1792256382082` | The engine's output folder `engine/out/<run>/`. Appears on the job's events (`run` field) and in `/etch/builds`. Permanent. |
| board path | `/engine/out/live-1792256382082` | `lead:done` → `board`. Send it back as `base` to change that board. |

## Routes

### `GET /etch/health` (no auth)

```json
{ "ok": true, "busy": false, "queue": 0, "stt": true }
```

`busy`: a build is running. `queue`: builds waiting. `stt`: the voice service is up (hide or disable the mic when
false). The page polls this every 20 s for the ENGINE ONLINE / OFFLINE dot; a failed fetch means offline.

### `POST /etch/auth`

No body. Header `X-Etch-Code`. → `200 {"ok": true}` or `401 {"ok": false, "error": "That code did not work."}`;
`429` after too many wrong tries.

### `POST /etch/live` — start a build

```json
{ "mode": "design", "text": "a board that blinks one red LED from a coin cell" }
{ "mode": "modify", "text": "add a buzzer", "base": "/engine/out/live-1792256382082" }
{ "mode": "bench",  "text": "D1 must blink between 1 and 3 Hz", "base": "/engine/out/live-1792256382082" }
```

| mode | does |
| --- | --- |
| `design` | A new board from the request. |
| `modify` | Changes a finished board: full re-place, re-route and re-check. Needs `base`. |
| `bench` | Measures a finished board against the user's own checks (no new board). Needs `base`. |

`base` is the `board` path from that build's `lead:done` (`/engine/out/live-…`, leading slash optional).

→ `200 {"job": "…", "token": "…"}`. Errors: `400` "Tell me what your board should do." (no text or bad mode),
`400` "Build a board first…" (bad `base`), `400` "That version did not finish…" (base has no `design.json`), `429`
(queue full or limits).

### `GET /etch/job?id=<job>&since=<n>` — poll a build

```json
{ "events": [ … ], "next": 23, "done": false, "state": "running", "stage": "placing and routing", "run": "live-1792256382082" }
```

Pass `next` back as `since` to get only new events. The page polls every 0.4 s while a build runs.
`done` turns true once the build has ended and you have every event. `404` once the job is forgotten (about an hour
after it ends, or after a backend restart): open it from the library with `/etch/run` instead.

`state`: `queued` → `running` → `done` | `failed` | `cancelled`.

`stage`: a short label for cards and status lines. Values: `waiting in line`, `waiting in line (N ahead)`,
`starting the engine`, `screening parts`, `picking parts`, `built before, loading it`, `placing and routing`,
`placing and routing, try N`, `checking`, `repairing`, `fetching a part`, `exporting files`, `rendering in 3D`,
`finishing`, `running your checks`, `cancelling`, `done`, `checks did not all pass`, `stopped with an error`,
`cancelled`.

### `POST /etch/cancel`

```json
{ "job": "kUwJeQ1D0sYYAokT", "token": "…" }
```

→ `200 {"ok": true, "lock_released": true}`; `403` without the right token ("Only the person who started this build
can cancel it."); `404` unknown job; `409 {"ok": false, "error": "That build already finished.", "state": "done"}`.
The job then emits `lead:cancelled` and ends in state `cancelled`.

### `GET /etch/builds` — the library

```json
{ "now": 1792256500.2, "builds": [
  { "id": "live-1792256382082", "job": null, "run": "live-1792256382082",
    "text": "a board that blinks one red LED from a coin cell", "mode": "design",
    "state": "done", "stage": "done", "started": 1792256382.1, "ended": 1792256391.0,
    "seconds": 8.89, "thumb": true, "zip": true }
] }
```

Running and queued builds come first, then newest first, up to 60. A row with a `job` is still in memory, so it can
be followed live with `/etch/job`. `run` is `null` for a build still in line (no folder yet). `thumb` / `zip`: the
picture and the fab zip exist. Requests that weren't boards (junk, injection attempts, mains voltage) are never
listed. `seconds` only appears on finished builds read from disk.

### `GET /etch/run?id=<run>` — replay a finished build

```json
{ "events": [ … ], "build": { …same row as /etch/builds… } }
```

The build's full event log, same shape as `/etch/job` events but without `ts`. The page feeds them through the same
`onEvent` with `replay: true` to redraw the steps, gates and board. `404` "no such build".

### `GET /etch/thumb?id=<run>` (no auth)

PNG, the last render frame. Cached 10 min. `404` if the build has no render.

### Board files

| Route | Returns |
| --- | --- |
| `GET /etch/glb?id=<run>` | `model/gltf-binary`: the 3D board from KiCad (nodes named by refdes). Built on first request (a few seconds), cached after. |
| `GET /etch/parts?id=<run>` | `{"ok": true, "title", "name", "parts": {"<ref>": {ref, value, name, family, footprint, why, group, source, model, licence, route}}}`: what each part is and where it came from. `why` is the plain-English reason it's on the board. |
| `GET /etch/layers?id=<run>` | `{"ok": true, "units": "um", "pours": {"F": [[x0, y0, x1, y1, …], …], "B": […]}}`: copper pours as flat outlines in micrometres, KiCad board coordinates. |
| `GET /etch/export?id=<run>` | Runs the fab checks without downloading. `{"ok": true, "bytes", "files", "step", "checks": {erc_errors, drc_violations, unconnected, parity}, "seconds", "cached"?}` or `{"ok": false, "reasons": ["…"]}`. |
| `GET /etch/export.zip?id=<run>` | The checked fab zip (KiCad project, Gerbers, drill, BOM, `parts.csv`, README) as a download; `409 {"error": "checks failed", "reasons"}` when a check fails. |
| `GET /etch/zip?id=<run>` | The zip the build itself wrote (`*-fab.zip`), as a download. |
| `GET /etch/files/engine/out/<run>/frames/frames.json` (no auth) | Render frame list, below. |
| `GET /etch/files/engine/out/<run>/frames/fNNN.png` (no auth) | One render frame. |
| `GET /etch/files/engine/out/<run>/sim.json` (no auth) | ngspice results, below. |
| `GET /etch/piece/<16 hex>.png` (no auth) | A pre-rendered KiCad library part. The `img` field on `pieces:*` events gives the path. Cached for a week. |

Only those `/etch/files` paths are served; anything else is `404`.

`frames.json`:

```json
{ "w": 920, "h": 680, "total": 12, "labels": ["board", "J1", …],
  "frames": [{ "i": 0, "label": "board", "png": "f000.png", "s": 0.85 }, …], "done": true, "hi": "hi.png", "wall_s": 2.12 }
```

The board builds up one part per frame: frame 0 is the bare board, then one frame per `label`. `png` is relative to
the `frames/` folder.

`sim.json` (from the ngspice run):

```json
{ "ok": true, "engine": "ngspice", "supply_v": 5.0, "t_stop_s": 6.0,
  "leds": { "D1": { "peak_ma": 6.39, "avg_ma": 3.0, "blink_hz": 1.69, "duty": 0.46, "rise_times_s": [1.57, 2.16, …] } },
  "supply_ma_avg": 6.04, "supply_ma_peak": 11.28, "D1.blink_hz": 1.69, …, "wave": { "t": [ … ], … } }
```

The page uses `leds[ref].rise_times_s`, `blink_hz` and `duty` to light LEDs in the 3D view in time with the
simulation.

### Voice

`POST /etch/stt`: body is raw little-endian Float32 PCM, 16 kHz, mono (not JSON), at most 45 s. →
`{"text": "…", "seconds": 0.4}`. `503` "Voice is offline right now. Type instead." when the voice service is down.

`POST /etch/tts` `{"text": "…"}` (400 chars max) → `audio/mpeg`. The page uses it for Etch's spoken replies.

### Board view (`deploy/explore_api.py`)

`board` is either id kind from [EXPLORE_API.md](EXPLORE_API.md): `gh:<owner>/<repo>[/<subdir>]` (an imported board,
read from `etch-app/world/boards/<slug>/`) or `live-<ms>` (a build with a `design.json`). A bad id is `400` "That board
id is not valid."; an unknown board `404` "I don't know that board."; a `ref` that is not on the board `400` "That
part isn't on this board.". Every answer comes from `agent/llm.py` `call_json` with `ETCH_SUB_MODEL`, so it is
metered and stops at the OpenAI budget.

`POST /etch/ask` — a question about the board, or one part of it

```json
{ "board": "gh:FPGAwars/icezum", "ref": "U1", "question": "What does this part do?", "thread": "c_8f2a1b3c" }
```

`ref` and `thread` are optional. `ref` puts that part first in the model's context with its nets and nearest parts;
`thread` (a comment id on the same board) sends that thread's last messages along. `question` is cut to 400
characters. →

```json
{ "ok": true, "answer": "**U1 is the FPGA**, the chip …", "mentions": ["U1", "U3"],
  "thoughts": ["Finding U1 on the board", "Following its nets"], "seconds": 4.2 }
```

`answer` is 2-4 sentences of Markdown (bold and inline code only). `mentions`: refdes on this board the answer
talks about. `thoughts`: 2-3 short step titles. Errors: `400` "Ask me something about this board." (no question),
`429` "That is a lot of questions…", `503` "Etch has used up its question budget for now…" or "Etch can't answer
right now…", `502` "Etch could not put an answer together…".

`GET /etch/comments?board=<id>` → `{"ok": true, "comments": [comment, …]}` (oldest first; `[]` for a board with
none). A comment:

```json
{ "id": "c_8f2a1b3c", "board": "gh:FPGAwars/icezum", "n": 1, "ref": "U1", "anchor": [0.0123, 0.0016, 0.0081],
  "author": "You", "created": 1792256901,
  "messages": [
    { "role": "user", "text": "What does this part do?", "at": 1792256901 },
    { "role": "assistant", "text": "**U1 is the FPGA** …", "at": 1792256905,
      "thoughts": ["…"], "seconds": 4.2, "mentions": ["U1"] } ] }
```

`n` is the pin number: one more than the highest on the board. `anchor` is the clicked point in GLB metres.

| Route | Body | → |
| --- | --- | --- |
| `POST /etch/comments` | `{board, ref, anchor: [x, y, z], text}` | `{ok, comment}` with the question and its answer. `400` no or unknown `ref`, `anchor` not three finite numbers, empty `text`; `409` the board has 300 comments. Same limits and model errors as `/etch/ask`. |
| `POST /etch/comments/reply` | `{id, text, board?}` | `{ok, comment}` with the reply and its answer appended. `404` "That comment is gone." |
| `POST /etch/comments/delete` | `{id, board?}` | `{ok}`, also when it was already gone. |

`board` on reply/delete is optional (it skips looking the id up across boards). Comments are stored per board in
`engine/out/comments/<gh__slug | live-…>.json`.

`POST /etch/change` `{board, ref?, text}` — ask Etch to change a board

For a `live-*` board this is exactly `POST /etch/live` `{mode: "modify", text, base: "/engine/out/<run>"}` (same
checks, limits, queue and errors), with `ref` put in front of the text ("D1: make it blue") when the text does not
name it already → `200 {"ok": true, "job", "token"}`; follow it with `/etch/job`. For an imported `gh:` board:
`403` "This board's licence doesn't allow modified copies." when it isn't `copyable`, otherwise `501` "Changing
imported boards isn't available yet." (the page falls back to its demo flow on 501).

## Build events

Every event from `/etch/job` and `/etch/run` has `agent`, `type`, `t` (seconds since the build started) and `run`.
The page keys on `` `${agent}:${type}` `` (`feed()`, `stepLine()` and `onEvent()` in `app.js`). Events not listed
here can appear; ignore unknown ones.

In the order a design build sends them:

| Event | Fields | What the page does |
| --- | --- | --- |
| `lead:queued` | `position` | "N ahead" while waiting. Sent about once a second, never stored. |
| `lead:start` | `ask`, `base`? (set on a change) | Starts a new request; clears the part tiles. |
| `lead:say` | `text` | Etch's line: spoken (TTS) and shown. |
| `classifier:start` | `candidates`, `batches`, `backend`, `symbol_libraries`, `symbols`, `libraries_kept` | "screens N KiCad parts". |
| `classifier:batch` | `pieces: [{k, n, f, yes}]`, `seconds` | One batch of parts screened. |
| `classifier:done` | `yes`, `no`, `seconds`, `backend`, `families`, `stored`? | `backend: "stored board"` means it was built before and is being loaded. |
| `filter:done` | `kept: [{k, n, f}]`, `rejected: [{k, n, f, why}]`, `seconds` | Kept vs set-aside variants. |
| `pieces:show` | `k`, `n`, `f`, `img` | Adds a part tile. `img` is `/etch/piece/<hex>.png`; fetch it as `API + img`. |
| `picker:start` | `model`, `kept` | The model picks parts. |
| `picker:pick` | `parts: [{ref, piece, value, group, why}]`, `benchmarks: [{check, min?, max?, text}]`, `recipe`? , `model`, `seconds`, `say`? | The chosen parts. `recipe` set = a known circuit with no model call; `"stored board"` = built before. `why` feeds the part cards. |
| `pieces:confirm` | `parts: [{k, refs, img}]`, `drop: [k]` | Marks tiles as used (with refdes) or drops them. |
| `sourcing:start` | `what`, `search` | A part isn't in KiCad's libraries; fetching it. |
| `sourcing:done` | `ok`, `what`, `key`, `seconds`, `provenance: {route, source_url?, api_url?, licence?}` | Shows the fetched part's source card. |
| `assembly:iteration` | `iteration`, `phase: "place-route-check"` | Starts a try (a new gates column). |
| `assembly:gate` | `iteration`, `gate` (label), `ok`, `value`, `check`? | One check result: ERC, DRC, unconnected, parity, routed, 3D models, ngspice, plus one per benchmark. |
| `assembly:built` | `iteration`, `seconds`, `board: {w, h}` (mm), `tracks`, `stages_s` | Placed and routed. |
| `assembly:iteration` | `iteration`, `phase: "decide"`, `decision` (`done` / `repair` / `re-plan`), `seconds` | End of a try. |
| `picker:repair` | `iteration`, `seconds`, `parts` | The model fixed the design after failed gates; another try follows. |
| `assembly:change` | `added: ["BZ1 Buzzer …"]`, `kept` | What a change request added (modify mode). |
| `export:done` | `zip`, `zip_bytes`, `project: [files]`, `gerbers: [files]`, `bom_lines`, `seconds` | Fab files written. |
| `render:start` | `quality` | |
| `render:done` | `frames` (path to `frames.json`), `count`, `quality`, `seconds`, `lock_wait_s` | Loads the frames, then the 3D board (`/etch/glb`, `/etch/parts`, `/etch/layers`). |
| `benchmarks:check` | `said`, `check`, `label`, `min`?, `max`?, `value`, `unit`, `pass` (`true` / `false` / `null`), `result` (`pass` / `fail` / `cannot measure`) | Bench mode: one of the user's checks. |
| `benchmarks:done` | `passed`, `failed`, `unmeasured`, `seconds` | Bench mode summary. |
| `lead:done` | `ok`, `iterations`, `timings: {classifier_s, picker_s, assembly_s, export_s, render_s, total_s, …}`, `classifier`, `route`, `board`, `error`? | Last event. `ok` with `board` = success: remember `board` as the next `base` and fetch `sim.json`. `ok: false` = checks didn't all pass, or the request wasn't a board (then `error` says why and a `lead:say` explains). |
| `lead:error` | `error` | The engine crashed or was stopped. Last event. |
| `lead:cancelled` | | Cancelled. Last event. |

A full successful design run, abridged (the real one took 8.9 s):

```
lead:start → lead:say → classifier:start → classifier:batch ×12 → classifier:done → filter:done → pieces:show ×8
→ picker:pick → pieces:confirm → assembly:iteration(place-route-check) → assembly:gate ×15 → assembly:built
→ assembly:iteration(decide) → export:done → render:start → render:done → lead:done(ok)
```

## Typical page flows

**New board:** `POST /etch/live` → keep `job` and `token` → poll `/etch/job` with `since=next` until `done` → on
`render:done` load frames and the 3D board → on `lead:done` keep `board`.

**Change it:** `POST /etch/live` with `mode: "modify"` and `base` = the last `board`. Same polling. Each finished
version gets a thumbnail from `/etch/thumb?id=<run>` for the version strip.

**Library:** `GET /etch/builds` → for a row with a `job` that isn't finished, follow it live with `/etch/job`;
otherwise `GET /etch/run?id=<run>` and replay the events.

**Download:** `GET /etch/export?id=<run>` to check, then `GET /etch/export.zip?id=<run>`.

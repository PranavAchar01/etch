# Etch backend API

What `deploy/etch_public.py` serves today, and how the page (`deploy/etch-app/app.js`) uses each route. UI changes
should line up with this file. The board view routes (`/etch/ask`, `/etch/comments*`, `/etch/change`) are handled in
`etch_public.py`, with board loading, the model call, the comment store and board copying in `deploy/explore.py`; they
are listed under "Board view" below and designed in [EXPLORE_API.md](EXPLORE_API.md) (with the static `world/`
library).

If you change a route, an event or a field here, change `etch_public.py` (or `explore.py`, or the engine that emits
the event) in the same commit.

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
| Questions (`/etch/ask`, new comments, replies) | 40 per IP per 10 min (`ETCH_ASKS_PER_IP_10MIN`), plus the OpenAI budget guard | 429 / 503 |
| Question and comment text | 400 characters (not cut: longer text is refused) | 400 "Keep it under 400 characters." |
| Comments | 300 per board, 40 messages per thread (20 questions and their answers) | 409 |
| Changing a board (`/etch/change`) | Same as builds; imported boards up to 40 parts | 429 / 422 |

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

### Board view (`deploy/explore.py`)

The routes are in `etch_public.py`; loading boards, the model call, the comment store and copying an imported board
live in `deploy/explore.py`. All need `X-Etch-Code` like the other routes. POST bodies are JSON objects of at most
8 KB; a body that isn't a JSON object is `400` "bad request" (a larger body is not read, so the request fails as if
it were empty).

**Board ids.** `board` is either id kind from [EXPLORE_API.md](EXPLORE_API.md). The id is never used to build a path.

| Id | Resolves to |
| --- | --- |
| `gh:<owner>/<repo>[/<subdir>]` | The row with exactly that `id` in `etch-app/world/index.json`. Its `slug` names the folder `world/boards/<slug>/`, whose `meta.json` must carry the same `id`. Parts come from that folder's `parts.json`. Pin nets come from `library/cache/src/<slug>/board.kicad_pcb` when it is on hand. |
| `live-<ms>` | `engine/out/<run>/`, which must have a `design.json`. Parts and pin nets come from it, part names and reasons from `view/parts.json`. |

Anything else (a malformed id, a `gh:` id not in the index, a run with no `design.json`) is `404` "no such board".
A `ref`, where one is taken, is optional; if given it must be a refdes in the board's parts, otherwise `400` "That
part is not on this board.".

**Text.** `question` and comment `text` have control characters stripped and are trimmed, not cut. Empty is `400`
"Type a question first."; over 400 characters is `400` "Keep it under 400 characters.".

**Answers.** Every answer is one chat completion on the engine's endpoint and `ETCH_MODEL` (`agent/config.py`). It
checks the OpenAI budget first (`agent/llm.py` `guard`) and, on api.openai.com, is metered in `qa/openai-meter.jsonl`
under the name `explore`. The model gets the board as quoted data (up to 30,000 characters: title, licence or what the
build was asked for, then every part with value, footprint, description and pin nets; with a `ref`, that part first
with every pin, then the parts that share a net with it, skipping nets with more than 12 parts), plus the last 6
messages of the thread. Model errors:

| Status | `error` |
| --- | --- |
| 503 | "Etch has used up its answers for tonight." (the budget guard) |
| 503 | "Etch could not answer right now. Try again in a minute." (any other failure, or an empty answer) |

Questions are limited to 40 per IP per 10 min, shared by `/etch/ask`, new comments and replies: `429` "Too many
questions. Wait a few minutes, then ask again.". The limit is checked after the body is validated, so a refused
request does not use one up.

#### `POST /etch/ask` — a question about the board, or one part of it

```json
{ "board": "gh:OLIMEX/ESP32-POE", "ref": "U6", "question": "What does this part do?", "thread": "c_8f2a1b3c4d5e" }
```

`ref` and `thread` are optional. `thread` is a comment id: if it is a comment on the same board, its messages are
sent as history and its `ref` is used when the body has none. Any other `thread` is ignored. →

```json
{ "ok": true, "answer": "**U6 is the ESP32 module**, the board's main controller …", "mentions": ["U6", "J2"],
  "thoughts": ["Reading U6's connections", "Following its nets"], "seconds": 4.2 }
```

`answer`: Markdown with bold and inline code only (other markup is stripped), at most 4000 characters. `mentions`:
up to 20 refdes on this board, the ones the model listed plus any it names in the answer. `thoughts`: 1 to 4 short
step titles (at most 60 characters each). `seconds`: how long the answer took.

Errors: `400` bad body, ref or question; `404` "no such board"; `429`; `503`.

#### Comments

A comment is a thread pinned to a part:

```json
{ "id": "c_8f2a1b3c4d5e", "board": "gh:OLIMEX/ESP32-POE", "n": 1, "ref": "U6", "anchor": [0.0123, 0.0016, 0.0081],
  "author": "You", "created": 1792256901,
  "messages": [
    { "role": "user", "text": "What does this part do?", "at": 1792256901 },
    { "role": "assistant", "text": "**U6 is the ESP32 module** …", "at": 1792256905,
      "mentions": ["U6"], "thoughts": ["…"], "seconds": 4.2 } ] }
```

| Field | |
| --- | --- |
| `id` | `c_` and 12 hex characters. |
| `n` | The pin number. It counts up per board and is never reused, even after a delete. |
| `ref` | The part, or `null`. |
| `anchor` | The clicked point `[x, y, z]` in GLB metres (rounded to 6 decimals), or `null`. |
| `author` | Always `"You"`. |
| `created`, `at` | Unix seconds (whole). |
| `messages` | Alternating user and assistant messages. Assistant ones also carry `mentions`, `thoughts` and `seconds` from the answer. |

Comments are stored in one SQLite file, `engine/out/explore/comments.sqlite3` (`ETCH_EXPLORE_DB` overrides). It holds
the comments and, per board, the last pin number given out.

| Route | Body | → | Errors |
| --- | --- | --- | --- |
| `GET /etch/comments?board=<id>` | | `{ok, comments: [comment, …]}`, ordered by `n`; `[]` for a board with none | `404` "no such board" |
| `POST /etch/comments` | `{board, ref?, anchor?, text}` | `{ok, comment}`: the new thread with the question and its answer | `400` bad `ref`, bad `text`, or `anchor` not `null` or three finite numbers each within ±10: "That spot on the board is not valid."; `409` "This board has as many comments as it can hold." (300); `429`; `503` |
| `POST /etch/comments/reply` | `{id, text}` | `{ok, comment}` with the question and its answer appended | `404` "That comment is gone."; `400` bad `text`; `409` "This thread is full. Start a new comment." (40 messages); `429`; `503` |
| `POST /etch/comments/delete` | `{id}` | `{ok: true}` | `404` "That comment is gone." (also when it was already deleted) |

A new comment or reply is answered first and saved only when the answer comes back, so a `429` or `503` saves nothing.
A reply is answered with the whole thread as history and the comment's `ref` (dropped if the part is no longer on the
board). Other fields in the body (such as `board` on reply or delete) are ignored. Anyone with the access code can
delete any comment.

#### `POST /etch/change` — ask Etch to change a board

```json
{ "board": "live-1792256382082", "ref": "D1", "text": "make it blue" }
```

`ref` is optional. `text` has its whitespace collapsed and is cut to 400 characters; empty is `400` "Tell me what to
change.". With a `ref` the request becomes "D1: make it blue" (cut to 400 again). → `200 {"ok": true, "job", "token"}`;
follow it with `/etch/job`.

For a `live-*` board this is a modify run on that build, the same as `POST /etch/live` `{mode: "modify", text, base}`,
with the same queue and build limits (`429` "I am busy with other builds. Try again in a minute." or "That is all the
builds I can do for now. Try again later.").

For an imported `gh:` board Etch first turns it into its own design, then copies it into a new `live-*` run and
changes that run:

1. The board must be `copyable` (its licence is on the allow-list).
2. Its KiCad files must be on hand in `library/cache/src/<slug>/` (`board.kicad_pcb` and a `sch/` folder).
3. Every electrical part (a footprint with pads; refs starting with `#` or containing `*` are skipped) must be a
   piece in Etch's KiCad catalog, `engine/parts/kicad_catalog.json`, with the key `kicad:<symbol lib_id>@<footprint>`.
   Nothing is substituted. Pin nets come from the `.kicad_pcb`; a single power rail becomes `VCC`, ground nets
   become `GND`, and the supply voltage is read from the rail name (5.0 V if there is none).
4. At most 40 such parts.

| Status | `error` |
| --- | --- |
| 403 | "This board's licence does not allow copying it, so Etch cannot change it." |
| 409 | "Etch does not have this board's design files on hand, so it cannot change it yet." |
| 422 | "This board has N parts. Etch can change boards with up to 40 parts for now." |
| 422 | "Etch can only change boards built from parts in its own library. N of M parts here are not (R1, U2, … and more)." |
| 422 | "This board has no parts Etch can work with." |
| 429 | The build queue and limits, checked before anything is copied. |

The copy is a new `engine/out/live-<ms>/` with the `design.json` and an `imported.json` (`board`, `url`, `commit`,
`pcb_path`, `license`, `license_url`, `author_url`, `copied`). It has no `run.jsonl`, so it is not a card in
`/etch/builds`; the modify run on it is.

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

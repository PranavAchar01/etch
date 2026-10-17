# Explore + board view: data and API contract

The board library ("World") and the board view (3D board, pinned part comments, chat) are built
against this contract. Two halves:

1. **Static library data** — produced offline by `library/` (crawl GitHub, prerender with KiCad),
   served as plain files from `deploy/etch-app/world/`. No backend needed to browse or view boards.
2. **Backend endpoints** — routes for asking about a board, comments, and changes. **Built** in
   `deploy/explore_api.py` (hooked into `deploy/etch_public.py`); the served shapes are in
   [API.md](API.md) "Board view". The page still falls back to `deploy/etch-app/js/mock-api.js`
   (localStorage comments, answers built from `parts.json`) when the backend is unreachable, answers
   404/501, or the URL has `?mock=1`.

## Board ids

| Kind | Id | Example |
| --- | --- | --- |
| Imported open-source board | `gh:<owner>/<repo>` or `gh:<owner>/<repo>/<subdir>` | `gh:OLIMEX/ESP32-POE` |
| Etch build | `live-<ms>` (existing) | `live-1792255903767` |

On disk and in URLs an imported id becomes a slug: `owner__repo` or `owner__repo__sub__dir`
(lowercase, `/` → `__`, anything outside `[a-z0-9_.-]` → `-`).

## 1. Static library data (`deploy/etch-app/world/`)

```
world/
  index.json                   every board, small enough to load at once
  boards/<slug>/
    meta.json                  full board record (below)
    parts.json                 parts keyed by refdes, same shape as GET /etch/parts
    thumb.webp                 640x400 prerendered still of the 3D board (shown instantly)
    board.glb                  prerendered 3D board, meshopt-compressed, nodes named by refdes
```

`index.json`:

```json
{
  "generated": "2026-10-17T18:00:00Z",
  "count": 312,
  "categories": [{ "id": "keyboards", "label": "Keyboards", "count": 41 }],
  "boards": [
    {
      "id": "gh:OLIMEX/ESP32-POE",
      "slug": "olimex__esp32-poe",
      "title": "ESP32 POE",
      "owner": "OLIMEX",
      "repo": "ESP32-POE",
      "category": "dev-boards",
      "stars": 0,
      "license": "Apache-2.0",
      "copyable": true,
      "layers": 2,
      "size_mm": [52.0, 25.4],
      "parts": 37,
      "glb_bytes": 812345
    }
  ]
}
```

Categories (fixed set, in this order): `keyboards, iot, computing, dev-boards, audio, robotics,
tools, rf, retro, lighting, displays, power, wearables, everything-else`.

`meta.json` = the index row plus:

```json
{
  "url": "https://github.com/OLIMEX/ESP32-POE",
  "commit": "9f1c2e4...",
  "pcb_path": "hardware/esp32-s3-devkit.kicad_pcb",
  "description": "repo description from GitHub",
  "license_url": "https://github.com/OLIMEX/ESP32-POE/blob/9f1c2e4/LICENSE",
  "author_url": "https://github.com/OLIMEX",
  "topics": ["kicad", "esp32"],
  "board": { "mask": "green", "silk": "white", "finish": "HASL", "thickness_mm": 1.6 },
  "rendered": "2026-10-17T18:00:00Z",
  "kicad": "10.0.1"
}
```

`copyable` is true only for licences on the allow-list (MIT, Apache-2.0, BSD-*, CC0-1.0, CC-BY-4.0,
CC-BY-SA-4.0, CERN-OHL-P/W/S-2.0, GPL-*, LGPL-*, MPL-2.0, Unlicense). Repos with no licence are not
imported at all.

`parts.json`:

```json
{
  "parts": {
    "U6": {
      "ref": "U6", "value": "ESP32-WROOM-32E", "footprint": "RF_Module:ESP32-WROOM-32",
      "description": "symbol description from the schematic, if any",
      "pos_mm": [12.3, 8.1], "side": "top", "rot": 90,
      "node": "U6"
    }
  }
}
```

`node` is the GLB node name for the part (KiCad names it after the refdes). GLB units are metres,
Y-up; glTF x = board x, glTF z = board y.

## 2. Backend endpoints (built: `deploy/explore_api.py`, documented in [API.md](API.md) "Board view")

Status: `/etch/ask`, `GET/POST /etch/comments`, `/etch/comments/reply`, `/etch/comments/delete` are built.
`/etch/change` is built for `live-*` boards; for imported `gh:` boards it answers `501` "Changing imported boards
isn't available yet." (`403` when the licence doesn't allow copies) until an imported KiCad project can be turned
into an Etch `design.json` (see below). Differences from the first draft: reply/delete also accept an optional
`board`; assistant messages carry `thoughts`, `seconds` and `mentions` like the mock.

All take/return JSON, need the `X-Etch-Code` header like the other routes, and accept either board id
kind. Basics, ids, limits and error handling follow [API.md](API.md): errors are `{"error": "..."}` with a
4xx/5xx status, `401 {"error": "code"}` shows the access-code prompt, and other `error` strings are shown to
the user as is.

For a `live-*` board the UI does not need anything new to change it: "Ask Etch to change it" calls the
existing `POST /etch/live` with `mode: "modify"` and `base` = that build's `board` path, then polls
`/etch/job` (API.md "Change it"). `/etch/change` below is only needed for imported `gh:` boards.

### Ask about a board or a part

`POST /etch/ask`

```json
{ "board": "gh:OLIMEX/ESP32-POE", "ref": "U6", "question": "What does this part do?",
  "thread": "c_8f2a" }
```

`ref` and `thread` are optional (no `ref` = question about the whole board, from the left chat
panel). Response:

```json
{ "ok": true, "answer": "**U6 is the ESP32 module** — the board's main controller...",
  "mentions": ["U4", "J2"], "thoughts": ["Describing the development board"], "seconds": 9.1 }
```

`answer` is Markdown (bold + inline code only). `mentions` are refdes the UI highlights.
`thoughts` are short step titles shown collapsed above the answer ("Worked for 9s").

### Comments pinned to parts

`GET /etch/comments?board=<id>` →

```json
{ "ok": true, "comments": [
  { "id": "c_8f2a", "board": "gh:OLIMEX/ESP32-POE", "n": 1, "ref": "U6",
    "anchor": [0.0123, 0.0016, 0.0081], "author": "You", "created": 1792256901,
    "messages": [
      { "role": "user", "text": "What does this part do?", "at": 1792256901 },
      { "role": "assistant", "text": "**U6 is the ESP32 module**...", "at": 1792256910 }
    ] } ] }
```

`n` is the pin number shown on the board (1, 2, 3… per board). `anchor` is the clicked point in GLB
coordinates (metres).

`POST /etch/comments` `{ board, ref, anchor, text }` → `{ ok, comment }` — creates the thread and
answers it (same answer as `/etch/ask`), returning the comment with both messages.

`POST /etch/comments/reply` `{ id, text }` → `{ ok, comment }` — appends the user message and the
answer.

`POST /etch/comments/delete` `{ id }` → `{ ok }`.

### Ask Etch to change it

`POST /etch/change` `{ board, ref?, text }` → `{ ok, job, token }`. For a `live-*` board this is
`/etch/live` with `mode: "modify"` and `base` set. For an imported board the backend first copies
it into a new `live-*` run (only if `copyable`). Progress uses the existing `GET /etch/job`.

Not built yet for imported boards: `agent.live` modify mode reads the base run's `design.json` (Etch's own design:
every part as a KiCad catalog piece key `Lib:Symbol@Lib:Footprint` from `kicad_catalog`, a `pins` map pin → net,
a `group`, plus `title`/`name`/`voltage`), re-picks with it, then re-places and re-routes the whole board with
`auto_layout` (`prior=` only seeds a layout Etch made). An imported project has none of that: its symbols and
footprints are mostly project-local (`Icezum:C_0402`), not catalog pieces; its netlist would have to be parsed from
the `.kicad_pcb` / schematic; and boards of 100-200+ parts, 4 layers and hand placement are far outside what the
auto-placer and the 15-minute build can redo. Doing it needs a converter (KiCad project → `design.json` with
piece-key mapping) and a modify path that edits the imported `.kicad_pcb` in place instead of re-placing it.

# Explore + board view: data and API contract

The board library ("World") and the board view (3D board, pinned part comments, chat) are built
against this contract. Two halves:

1. **Static library data** — produced offline by `library/` (crawl GitHub, prerender with KiCad),
   served as plain files from `deploy/etch-app/world/`. No backend needed to browse or view boards.
2. **Backend endpoints** — routes for asking about a board, comments, and changes. **Built** in
   `deploy/explore.py`, with the routes in `deploy/etch_public.py`; the served shapes, errors and limits are in
   [API.md](API.md) "Board view". The page falls back to `deploy/etch-app/js/mock-api.js` (localStorage
   comments, answers built from `parts.json`) with `?mock=1`, when no backend is configured or reachable, or when
   the backend has no board routes.

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

## 2. Backend endpoints (built in `deploy/explore.py`; details in [API.md](API.md) "Board view")

All take and return JSON, need the `X-Etch-Code` header like the other routes, and accept either board id kind.
Errors are `{"error": "..."}` with a 4xx/5xx status and are shown to the user as is; `401 {"error": "code"}` shows
the access-code prompt. A `gh:` id is only looked up in `world/index.json` (its folder comes from the row's `slug`);
an id that isn't there, or a `live-*` run with no `design.json`, is `404` "no such board".

| Route | Body | Returns |
| --- | --- | --- |
| `POST /etch/ask` | `{board, ref?, question, thread?}` | `{ok, answer, mentions, thoughts, seconds}` |
| `GET /etch/comments?board=<id>` | | `{ok, comments}`, ordered by pin number `n` |
| `POST /etch/comments` | `{board, ref?, anchor?, text}` | `{ok, comment}` with the question and its answer |
| `POST /etch/comments/reply` | `{id, text}` | `{ok, comment}` with the reply and its answer appended |
| `POST /etch/comments/delete` | `{id}` | `{ok}`; `404` when the comment is gone |
| `POST /etch/change` | `{board, ref?, text}` | `{ok, job, token}`; follow it with `GET /etch/job` |

`answer` is Markdown (bold and inline code only). `mentions` are refdes the UI highlights. `thoughts` are short step
titles shown collapsed above the answer ("Worked for 9s"). A comment is `{id, board, n, ref, anchor, author,
created, messages}`: `n` is the pin number shown on the board, counting up per board and never reused; `anchor` is
the clicked point in GLB metres. Comments are kept in SQLite at `engine/out/explore/comments.sqlite3`
(`ETCH_EXPLORE_DB` overrides).

For a `live-*` board, `/etch/change` is a modify run on that build, the same as `POST /etch/live` with
`mode: "modify"` and `base` set (the page may call `/etch/live` directly, API.md "Change it"). For an imported `gh:`
board whose parts are all pieces in Etch's KiCad catalog (`engine/parts/kicad_catalog.json`, at most 40 electrical
parts), the backend turns the KiCad project into an Etch `design.json`, copies it into a new `live-*` run and changes
that with a full re-place. Every other copyable board (most real ones) is edited in place by `agent/import_edit.py`:
the author's own KiCad project is copied into a new `live-*` run and only the change is made on it (docs/API.md
"Changing an imported board"). Its KiCad files are fetched at the pinned commit first when they are not on hand. Not
`copyable` is `403`. A later change of that `live-*` run is in place too (its `imported.json` has `inplace: true`).

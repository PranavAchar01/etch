# Etch agent

A lead agent plus sub-agents (spec writer, feature writer, part scout) over OpenAI-style Chat Completions
tool calling. Plain code does every build and check (KiCad place and route, ERC, DRC, parity, ngspice);
the agents write specs and repair them from gate failures, up to 4 iterations.

Every part is a published file: KiCad's own libraries, LCSC through easyeda2kicad, or a GitHub repo
(mechanical parts such as keycaps and cases come from GitHub code search over .step/.stp files).
Nothing is generated. Each part carries its provenance (route, source URL, commit or version, licence,
STEP sha256), and a `parts` event lists it for every part on a built board. No real file means not_found.

## Run (from pcb-loop/)

    uv run --quiet --project engine --with openai python -m agent run "build an LED flasher"
    uv run --quiet --project engine --with openai python -m agent tool add_feature '{"request": "add a battery connector", "board": "/engine/out/agent-123"}'
    uv run --quiet --project engine --with openai python -m agent tool find_part '{"query": "TP4056"}'

Output is one JSON event per line (agent, type, t, ...); the last line is the `done` event.
Logs: engine/out/agent-logs/. The studio server exposes the same: POST /etch/agent (SSE for one run),
GET /etch/agent/stream (SSE, every run), and /etch/build, /etch/feature, /etch/search, /etch/bench.

## Switch to Nemotron on Crusoe

Only environment, no code change. Put the Crusoe key in Keychain first
(`security add-generic-password -a "$USER" -s crusoe-api-key -w`), then:

    export ETCH_BASE_URL=https://api.inference.crusoecloud.com/v1
    export ETCH_MODEL=nvidia/Nemotron-3-Super-120B-A12B
    export ETCH_SUB_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
    export ETCH_API_KEY_SOURCE=crusoe-api-key

Reasoning effort settings (ETCH_REASONING_EFFORT, ETCH_SUB_REASONING_EFFORT) only apply on OpenAI.
Today's defaults: gpt-5-mini (lead, medium) and gpt-5-nano (sub-agents, low), picked as the closest
OpenAI match; see research/model-pick.md. Restart studio/flux_server.py after changing the env.

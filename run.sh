#!/bin/bash
# Run Etch locally: speech-to-text (:3421), the backend (:3439) and the page (:8766).
#   ./run.sh            local only; opens http://127.0.0.1:8766
#   ./run.sh --public   also starts the Cloudflare tunnel (deploy/tunnel.sh), which points
#                       deploy/etch-app/config.json at it and redeploys the linked Vercel project
# Anything already listening on a port (e.g. the launchd jobs from deploy/restart.sh) is reused.
# Ctrl-C stops what this script started.
set -uo pipefail
R="$(cd "$(dirname "$0")" && pwd)"
cd "$R"
[ -f .etch.env ] || { echo "Run ./setup.sh first."; exit 1; }
# shellcheck disable=SC1091
source .etch.env
# a build needs KiCad's own libraries and the open-source ones setup.sh fetches: stop here, not mid-build
K="${ETCH_KICAD_APP:-}"
if [ -z "$K" ] || [ ! -x "$K/Contents/MacOS/kicad-cli" ] || [ ! -f "$K/Contents/SharedSupport/symbols/MCU_Module.kicad_sym" ]; then
  echo "KiCad 10 or its libraries are missing (ETCH_KICAD_APP=${K:-unset}). Run ./setup.sh."; exit 1
fi
python3 -c 'import json,sys; sys.exit(0 if json.load(open("engine/parts/lib_paths.json"))["symbols"] else 1)' 2>/dev/null \
  || { echo "The open-source KiCad libraries are not fetched. Run ./setup.sh."; exit 1; }
mkdir -p logs
PIDS=()
trap 'kill ${PIDS[@]+"${PIDS[@]}"} 2>/dev/null; exit 0' INT TERM EXIT

up() { /usr/sbin/lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
start() { # port name cmd...
  local port=$1 name=$2; shift 2
  if up "$port"; then echo "$name: already on :$port"; return; fi
  "$@" >"logs/$name.log" 2>&1 &
  PIDS+=($!)
  echo "$name: starting on :$port (logs/$name.log)"
}

start 3421 stt node studio/stt/stt_server.cjs
start 3439 backend engine/.venv/bin/python3 deploy/etch_public.py
start 8766 page engine/.venv/bin/python3 -m http.server 8766 --bind 127.0.0.1 --directory deploy/etch-app

for _ in $(seq 1 60); do
  curl -sf http://127.0.0.1:3439/etch/health >/dev/null && break
  sleep 1
done
echo "backend health: $(curl -s http://127.0.0.1:3439/etch/health)"

if [ "${1:-}" = "--public" ]; then
  deploy/tunnel.sh &
  PIDS+=($!)
  echo "tunnel: starting (deploy/logs/tunnel.log); public URL lands in deploy/tunnel-url.txt"
fi

URL="http://127.0.0.1:8766/?api=http://127.0.0.1:3439"
echo "Etch: $URL"
open "$URL" 2>/dev/null || true
wait

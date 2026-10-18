#!/bin/bash
# Run Etch locally: speech-to-text (:3421), the backend (:3439) and the page (:8766).
#   ./run.sh            local only; opens the page on this machine
#   ./run.sh --tunnel   also opens a Cloudflare quick tunnel to the backend and prints the etch.chat link that
#                       uses it (scripts/tunnel.sh). Use this to drive etch.chat with your own backend.
#   ./run.sh --public   the project's own host: deploy/tunnel.sh, which also republishes the official API URL
#                       (needs the maintainers' Vercel login; contributors want --tunnel)
# Ports come from .env (ETCH_PUBLIC_PORT, ETCH_PAGE_PORT, ETCH_STT_PORT). Anything already listening on a port
# is reused. Ctrl-C stops what this script started.
set -uo pipefail
R="$(cd "$(dirname "$0")" && pwd)"
cd "$R"
[ -f .etch.env ] || { echo "Run ./setup.sh first."; exit 1; }
# shellcheck disable=SC1091
source .etch.env
# read one KEY=VALUE from .env without sourcing it (blank values stay unset; the backend loads .env itself)
ENVF="$R/.env"
envget() { [ -f "$ENVF" ] && sed -n -E "s/^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=[[:space:]]*[\"']?([^\"'#[:space:]]*).*/\2/p" "$ENVF" | tail -1; }
API_PORT="${ETCH_PUBLIC_PORT:-$(envget ETCH_PUBLIC_PORT)}"; API_PORT="${API_PORT:-3439}"
PAGE_PORT="${ETCH_PAGE_PORT:-$(envget ETCH_PAGE_PORT)}"; PAGE_PORT="${PAGE_PORT:-8766}"
STT_PORT="${ETCH_STT_PORT:-$(envget ETCH_STT_PORT)}"; STT_PORT="${STT_PORT:-3421}"
export ETCH_PUBLIC_PORT="$API_PORT" ETCH_STT_PORT="$STT_PORT"
export ETCH_STT_URL="${ETCH_STT_URL:-http://127.0.0.1:$STT_PORT/stt}"
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

start "$STT_PORT" stt node studio/stt/stt_server.cjs
start "$API_PORT" backend engine/.venv/bin/python3 deploy/etch_public.py
start "$PAGE_PORT" page engine/.venv/bin/python3 deploy/serve_page.py "$PAGE_PORT"

for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$API_PORT/etch/health" >/dev/null && break
  sleep 1
done
echo "backend health: $(curl -s --max-time 5 "http://127.0.0.1:$API_PORT/etch/health")"

case "${1:-}" in
  --tunnel)
    scripts/tunnel.sh &
    PIDS+=($!)
    ;;
  --public)
    deploy/tunnel.sh &
    PIDS+=($!)
    echo "tunnel: starting (deploy/logs/tunnel.log); public URL lands in deploy/tunnel-url.txt"
    ;;
esac

URL="http://127.0.0.1:$PAGE_PORT/?api=http://127.0.0.1:$API_PORT"
echo "Etch: $URL"
[ -n "${ETCH_NO_OPEN:-}" ] || open "$URL" 2>/dev/null || true
wait

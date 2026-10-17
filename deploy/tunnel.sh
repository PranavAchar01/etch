#!/bin/bash
# Etch public tunnel (launchd: com.etch.app.tunnel). Starts a Cloudflare quick tunnel to the backend,
# reads its https://*.trycloudflare.com URL, and when it differs from the deployed one writes
# deploy/etch-app/config.json and redeploys the Vercel app so the page points at the new URL.
# Then it waits on cloudflared; launchd restarts this script (and so re-points) if the tunnel dies.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
PORT="${ETCH_PUBLIC_PORT:-3439}"
LOG="$D/logs/tunnel.log"
mkdir -p "$D/logs"
: > "$LOG"
/opt/homebrew/bin/cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >> "$LOG" 2>&1 &
CF=$!
echo "$CF" > "$D/cloudflared.pid"
trap 'kill "$CF" 2>/dev/null' TERM INT
URL=""
for _ in $(seq 1 90); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1)
  [ -n "$URL" ] && break
  kill -0 "$CF" 2>/dev/null || exit 1
  sleep 1
done
[ -z "$URL" ] && { echo "no tunnel URL" >> "$LOG"; kill "$CF"; exit 1; }
echo "$URL" > "$D/tunnel-url.txt"
# wait until the tunnel answers before pointing the app at it
for _ in $(seq 1 60); do
  curl -sf "$URL/etch/health" >/dev/null && break
  sleep 2
done
CUR=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("api",""))' "$D/etch-app/config.json" 2>/dev/null)
if [ "$CUR" != "$URL" ]; then
  printf '{"api": "%s"}\n' "$URL" > "$D/etch-app/config.json"
  "$D/redeploy.sh" >> "$D/logs/redeploy.log" 2>&1
fi
wait "$CF"

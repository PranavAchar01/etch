#!/bin/bash
# Etch public tunnel (launchd: com.etch.app.tunnel). Starts a Cloudflare quick tunnel to the backend,
# reads its https://*.trycloudflare.com URL, and when it differs from the published one writes
# deploy/api-config/config.json and redeploys the api-config Vercel project (see below).
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
# The frontend (Vercel etch-app, etch.chat, or Nithin's own project) reads config.json, which points at
# config_url = the api-config project. Only that tiny project is redeployed, so a new tunnel URL never
# ships an old copy of the frontend.
CUR=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("api",""))' "$D/api-config/config.json" 2>/dev/null)
if [ "$CUR" != "$URL" ]; then
  printf '{"api": "%s"}\n' "$URL" > "$D/api-config/config.json"
  (cd "$D/api-config" && PATH="/usr/local/bin:/opt/homebrew/bin:$PATH" vercel deploy --prod --yes 2>&1 | grep -E "Aliased|rror") >> "$D/logs/redeploy.log" 2>&1
fi
wait "$CF"

#!/bin/bash
# Expose your local Etch backend over HTTPS with Cloudflare, so etch.chat (or any Etch frontend) can use it.
#   scripts/tunnel.sh                 quick tunnel: no Cloudflare account, a random https://*.trycloudflare.com URL
#                                     that changes every time it starts
#   ETCH_TUNNEL_NAME=etch ETCH_TUNNEL_HOSTNAME=etch-api.example.com scripts/tunnel.sh
#                                     named tunnel: your own stable hostname on a domain in your Cloudflare account
#                                     (one-time setup in README.md, "Your own stable URL")
# Prints the URL to paste into etch.chat -> Backend, and a link that opens etch.chat already pointed at it.
set -u
R="$(cd "$(dirname "$0")/.." && pwd)"
# read one KEY=VALUE from .env without sourcing it (env vars win)
ENVF="$R/.env"
envget() { [ -f "$ENVF" ] && sed -n -E "s/^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=[[:space:]]*[\"']?([^\"'#[:space:]]*).*/\2/p" "$ENVF" | tail -1; }
PORT="${ETCH_PUBLIC_PORT:-$(envget ETCH_PUBLIC_PORT)}"; PORT="${PORT:-3439}"
FRONT="${ETCH_FRONTEND_URL:-$(envget ETCH_FRONTEND_URL)}"; FRONT="${FRONT:-https://etch.chat}"
ETCH_TUNNEL_NAME="${ETCH_TUNNEL_NAME:-$(envget ETCH_TUNNEL_NAME)}"
ETCH_TUNNEL_HOSTNAME="${ETCH_TUNNEL_HOSTNAME:-$(envget ETCH_TUNNEL_HOSTNAME)}"
command -v cloudflared >/dev/null || { echo "cloudflared is not installed: brew install cloudflared (or see https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)"; exit 1; }
curl -sf --max-time 5 "http://127.0.0.1:$PORT/etch/health" >/dev/null \
  || { echo "No Etch backend on 127.0.0.1:$PORT. Start it first with ./run.sh"; exit 1; }
mkdir -p "$R/logs"
LOG="$R/logs/tunnel.log"
: > "$LOG"

if [ -n "${ETCH_TUNNEL_NAME:-}" ]; then
  [ -n "${ETCH_TUNNEL_HOSTNAME:-}" ] || { echo "Set ETCH_TUNNEL_HOSTNAME too (the hostname you routed to the tunnel)."; exit 1; }
  cloudflared tunnel --no-autoupdate run --url "http://127.0.0.1:$PORT" "$ETCH_TUNNEL_NAME" >> "$LOG" 2>&1 &
  CF=$!
  URL="https://$ETCH_TUNNEL_HOSTNAME"
else
  cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >> "$LOG" 2>&1 &
  CF=$!
  URL=""
  for _ in $(seq 1 60); do
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1)
    [ -n "$URL" ] && break
    kill -0 "$CF" 2>/dev/null || { echo "cloudflared exited, see $LOG"; exit 1; }
    sleep 1
  done
  [ -n "$URL" ] || { echo "No tunnel URL after 60 s, see $LOG"; kill "$CF"; exit 1; }
fi
trap 'kill "$CF" 2>/dev/null' TERM INT EXIT

ok=""
for _ in $(seq 1 45); do
  curl -sf --max-time 5 "$URL/etch/health" >/dev/null && { ok=1; break; }
  kill -0 "$CF" 2>/dev/null || { echo "cloudflared exited, see $LOG"; exit 1; }
  sleep 2
done
[ -n "$ok" ] || echo "warning: $URL/etch/health does not answer yet (DNS can take a minute); see $LOG"
echo "$URL" > "$R/logs/tunnel-url.txt"
cat <<EOF

  Your backend:  $URL
  Health:        $URL/etch/health

  On etch.chat, open Backend (top right of Explore), paste the URL above and press Save.
  Or open this link, which uses your backend for that visit only:
    $FRONT/?api=$URL

  Ctrl-C stops the tunnel.
EOF
wait "$CF"

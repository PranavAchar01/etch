#!/bin/bash
# Restart the Etch public app's Mac side: backend (deploy/etch_public.py on :3439), the Cloudflare quick
# tunnel (re-points and redeploys the Vercel page if its URL changed) and the local STT keeper.
#   deploy/restart.sh          restart all three
#   deploy/restart.sh status   show PIDs, tunnel URL, health
#   deploy/restart.sh stop     stop all three (launchd jobs unloaded)
set -u
D="$(cd "$(dirname "$0")" && pwd)"
LA="$HOME/Library/LaunchAgents"
JOBS="com.etch.app.backend com.etch.app.tunnel com.etch.app.stt"
case "${1:-restart}" in
  status)
    for j in $JOBS; do launchctl list | grep -E "\s$j$" || echo "-  -  $j (not loaded)"; done | tee "$D/pids.txt"
    echo "cloudflared $(cat "$D/cloudflared.pid" 2>/dev/null)" | tee -a "$D/pids.txt"
    echo "tunnel: $(cat "$D/tunnel-url.txt" 2>/dev/null)"
    echo "page api: $(cat "$D/etch-app/config.json")"
    curl -s "$(cat "$D/tunnel-url.txt" 2>/dev/null)/etch/health"; echo
    ;;
  stop)
    for j in $JOBS; do launchctl bootout "gui/$(id -u)/$j" 2>/dev/null; done
    ;;
  restart)
    for j in $JOBS; do
      launchctl bootout "gui/$(id -u)/$j" 2>/dev/null
      sleep 1
      launchctl bootstrap "gui/$(id -u)" "$LA/$j.plist"
    done
    sleep 15; "$0" status
    ;;
esac

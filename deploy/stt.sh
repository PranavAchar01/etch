#!/bin/bash
# Keep local speech-to-text (studio/stt/stt_server.cjs, 127.0.0.1:3421) available for the public app
# (launchd: com.etch.app.stt). If something already serves :3421 (e.g. a studio session started it), wait;
# otherwise run our own and record its PID.
D="$(cd "$(dirname "$0")" && pwd)"
while /usr/sbin/lsof -nP -iTCP:3421 -sTCP:LISTEN >/dev/null 2>&1; do sleep 30; done
cd "$D/.."
echo $$ > "$D/stt.pid"
exec /usr/local/bin/node studio/stt/stt_server.cjs

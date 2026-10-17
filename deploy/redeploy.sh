#!/bin/bash
# Deploy deploy/etch-app (static page + config.json) to Vercel production (project etch-app).
set -eu
D="$(cd "$(dirname "$0")" && pwd)"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
cd "$D/etch-app"
echo "$(date '+%F %T') deploying with api=$(cat config.json)"
vercel deploy --prod --yes 2>&1 | grep -E "Aliased|Production|rror"

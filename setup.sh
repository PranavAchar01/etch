#!/usr/bin/env bash
# One-command setup: installs the exact toolchain + deps, then starts the dev server.
# Usage: ./setup.sh          (install + run dev server on http://localhost:3000)
#        ./setup.sh --build  (install + production build + start)
set -euo pipefail
cd "$(dirname "$0")"

NODE_MAJOR="$(cat .nvmrc)"
PNPM_VERSION="$(node -p "require('./package.json').packageManager.split('@')[1]" 2>/dev/null || echo 11.20.0)"

# 1. Node — use nvm/fnm if present, otherwise require a matching system Node.
if command -v fnm >/dev/null 2>&1; then
  fnm install "$NODE_MAJOR" && eval "$(fnm env)" && fnm use "$NODE_MAJOR"
elif [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
  . "${NVM_DIR:-$HOME/.nvm}/nvm.sh" && nvm install "$NODE_MAJOR" && nvm use "$NODE_MAJOR"
fi
if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'process.versions.node.split(".")[0]')" != "$NODE_MAJOR" ]; then
  echo "Need Node $NODE_MAJOR (found: $(node -v 2>/dev/null || echo none)). Install it via https://nodejs.org or nvm, then re-run." >&2
  exit 1
fi

# 2. pnpm — pinned to the version in package.json via corepack.
corepack enable >/dev/null 2>&1 || npm i -g corepack
corepack prepare "pnpm@$PNPM_VERSION" --activate

# 3. Exact dependencies from the lockfile.
pnpm install --frozen-lockfile

# 4. Run.
if [ "${1:-}" = "--build" ]; then
  pnpm build && pnpm start
else
  pnpm dev
fi

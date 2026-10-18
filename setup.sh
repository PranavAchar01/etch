#!/bin/bash
# One-time setup for running Etch on your own Mac (Apple silicon). Safe to re-run: every step skips what is
# already there.
#   ./setup.sh           install what is missing, then create .env from .env.example
#   ./setup.sh --check   only report which prerequisites are present
# Installs (Homebrew): uv, ngspice, node, cloudflared, KiCad 10 (cask, if no KiCad 10 is found); then the engine's
# Python env, the open-source KiCad libraries (about 1.2 GB at pinned commits) and the local speech-to-text
# model (about 500 MB). On Linux or Windows use Docker instead: see README.md, "Linux and Windows".
set -euo pipefail
R="$(cd "$(dirname "$0")" && pwd)"
cd "$R"
say() { printf '\n== %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
  echo "setup.sh is for macOS on Apple silicon. On Linux or Windows (WSL), run the backend in Docker:"
  echo "  cp .env.example .env   # add your keys"
  echo "  docker compose up --build"
  exit 1
fi

kicad_find() { # prints the path of a KiCad 10 app, or nothing
  local c
  for c in "${ETCH_KICAD_APP:-}" "$HOME/Applications/KiCad/KiCad.app" "/Applications/KiCad/KiCad.app" \
           $(ls -d "$(brew --caskroom 2>/dev/null)"/kicad/10.*/KiCad/KiCad.app 2>/dev/null | sort -V | tail -1); do
    [ -n "$c" ] && [ -x "$c/Contents/MacOS/kicad-cli" ] && { echo "$c"; return; }
  done
}

if [ "${1:-}" = "--check" ]; then
  miss=0
  chk() { if "$@" 2>/dev/null; then printf '  ok       %s\n' "$NAME"; else printf '  missing  %s\n' "$NAME"; miss=1; fi; }
  NAME="Homebrew (https://brew.sh)"; chk have brew
  NAME="python3 >= 3.11"; chk python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'
  for t in uv node ngspice cloudflared; do NAME="$t"; chk have "$t"; done
  K="$(kicad_find)"; NAME="KiCad 10${K:+ ($K)}"
  chk test -n "$K"
  NAME="engine Python env (engine/.venv)"; chk test -x engine/.venv/bin/python3
  NAME="open-source KiCad libraries"; chk python3 -c 'import json,sys; sys.exit(0 if json.load(open("engine/parts/lib_paths.json"))["symbols"] else 1)'
  NAME=".env"; chk test -f .env
  [ $miss = 0 ] && echo "Everything is in place. Start Etch with ./run.sh" || echo "Run ./setup.sh to install what is missing."
  exit 0
fi

have brew || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null || brew install python@3.12

say "Command-line tools"
for f in uv ngspice node cloudflared; do
  have "$f" && echo "$f: $(command -v "$f")" || brew install "$f"
done

say "KiCad 10"
KICAD_APP="$(kicad_find)"
if [ -z "$KICAD_APP" ]; then
  brew install --cask kicad
  KICAD_APP="$(kicad_find)"
fi
[ -n "$KICAD_APP" ] || { echo "KiCad 10 did not install. Get it from https://www.kicad.org/download/macos/ and re-run."; exit 1; }
# the engine and the render scripts default to ~/Applications/KiCad/KiCad.app: keep that path pointing at it
if [ "$KICAD_APP" != "$HOME/Applications/KiCad/KiCad.app" ] && [ ! -e "$HOME/Applications/KiCad/KiCad.app" ]; then
  mkdir -p "$HOME/Applications/KiCad" && ln -s "$KICAD_APP" "$HOME/Applications/KiCad/KiCad.app"
fi
[ -f "$KICAD_APP/Contents/SharedSupport/symbols/MCU_Module.kicad_sym" ] || { echo "KiCad at $KICAD_APP has no symbol libraries."; exit 1; }
V="$("$KICAD_APP/Contents/MacOS/kicad-cli" version)"
echo "KiCad $V at $KICAD_APP"
case "$V" in 10.*) ;; *) echo "Etch needs KiCad 10 (found $V). Install it with: brew install --cask kicad"; exit 1 ;; esac

say "Engine Python env (pinned by engine/uv.lock)"
uv sync --project engine --frozen

say "Open-source KiCad libraries (engine/parts/extra_libs, about 1.2 GB at pinned commits)"
python3 engine/parts/fetch_extra_libs.py
ETCH_KICAD_APP="$KICAD_APP" uv run --frozen --project engine python -m agent.kicad_library --build

say "Speech-to-text (Parakeet TDT 0.6B v3, sherpa-onnx)"
STT_HOME="$HOME/.cache/hyperframes"
SHERPA_REL="optional/sherpa-onnx-node@1.13.8-darwin-arm64"
MODEL_REL="parakeet/parakeet-tdt-0.6b-v3-int8"
if [ ! -f "$STT_HOME/$MODEL_REL/encoder.int8.onnx" ] || [ ! -d "$STT_HOME/$SHERPA_REL/node_modules/sherpa-onnx-node" ]; then
  STT_HOME="$R/.stt"
  if [ ! -d "$STT_HOME/$SHERPA_REL/node_modules/sherpa-onnx-node" ]; then
    mkdir -p "$STT_HOME/$SHERPA_REL"
    npm install --silent --no-audit --no-fund --prefix "$STT_HOME/$SHERPA_REL" \
      sherpa-onnx-node@1.13.8 sherpa-onnx-darwin-arm64@1.13.8
  fi
  if [ ! -f "$STT_HOME/$MODEL_REL/encoder.int8.onnx" ]; then
    T="$(mktemp -d)"
    curl -fL --progress-bar -o "$T/m.tar.bz2" \
      https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2
    tar -xjf "$T/m.tar.bz2" -C "$T"
    mkdir -p "$STT_HOME/$MODEL_REL"
    for f in encoder.int8.onnx decoder.int8.onnx joiner.int8.onnx tokens.txt; do
      mv "$T/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/$f" "$STT_HOME/$MODEL_REL/"
    done
    rm -rf "$T"
  fi
fi
echo "model in $STT_HOME"

say "Machine paths (.etch.env, no secrets)"
LOCK="$HOME/helloworld/.heavy-lock"
[ -d "$HOME/helloworld" ] || LOCK="$R/.heavy-lock"
KEEP="$(grep '^export ETCH_HEAVY_SLOTS=' .etch.env 2>/dev/null || true)"
cat > .etch.env <<EOF
export ETCH_KICAD_APP="$KICAD_APP"
export ETCH_STT_HOME="$STT_HOME"
export ETCH_HEAVY_LOCK="$LOCK"
EOF
[ -n "$KEEP" ] && echo "$KEEP" >> .etch.env
cat .etch.env

say "Settings and API keys (.env)"
if [ -f .env ]; then
  echo ".env already exists; left as is (compare with .env.example for new settings)"
else
  cp .env.example .env
  chmod 600 .env
  echo "created .env from .env.example"
fi
keyed() { # VAR keychain-service: is the key set in the environment, .env or the Keychain?
  [ -n "${!1:-}" ] && return 0
  grep -qE "^[[:space:]]*(export[[:space:]]+)?$1=[\"']?[^\"'#[:space:]]" .env && return 0
  [ -n "$2" ] && security find-generic-password -s "$2" -w >/dev/null 2>&1
}
if keyed CRUSOE_API_KEY crusoe-api-key || keyed ETCH_API_KEY "" \
   || grep -qE '^[[:space:]]*ETCH_BASE_URL=["'"'"']?http://(127\.0\.0\.1|localhost)' .env; then
  echo "model endpoint: configured"
else
  echo "model endpoint: NOT configured yet. Open .env and set CRUSOE_API_KEY, or ETCH_BASE_URL + ETCH_API_KEY"
  echo "  (any OpenAI-compatible endpoint; a local Ollama or vLLM needs no key). Where to get each key is in .env."
fi
keyed OPENAI_API_KEY openai-api-key && echo "OpenAI: configured" || echo "OpenAI: not set (optional: fallback, spoken replies)"
keyed YOU_API_KEY you-api-key && echo "You.com: configured" || echo "You.com: not set (optional: web part search)"

say "Done. Start Etch with ./run.sh  (./run.sh --tunnel to use it from etch.chat)"

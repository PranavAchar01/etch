#!/bin/bash
# One-time setup for Etch on a Mac (Apple silicon). Safe to re-run: every step skips what is already there.
#   ./setup.sh
# Installs: uv, ngspice, node, cloudflared (Homebrew), KiCad 10 (Homebrew cask, if no KiCad is found),
# the engine's Python env and the local speech-to-text model (~500 MB).
# Secrets go to the macOS Keychain, never to a file: OpenAI API key (openai-api-key) and the
# page's access code (etch-app-access-code).
set -euo pipefail
R="$(cd "$(dirname "$0")" && pwd)"
cd "$R"
say() { printf '\n== %s\n' "$*"; }

[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || { echo "Etch runs on macOS on Apple silicon."; exit 1; }
command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }

say "Command-line tools"
for f in uv ngspice node cloudflared; do
  command -v "$f" >/dev/null || brew install "$f"
done

say "KiCad 10"
KICAD_APP="${ETCH_KICAD_APP:-}"
# a cask KiCad can sit only in the Caskroom (installed as a dependency, or its /Applications copy removed)
cask_kicad() { ls -d "$(brew --caskroom)"/kicad/10.*/KiCad/KiCad.app 2>/dev/null | sort -V | tail -1; }
for c in "$HOME/Applications/KiCad/KiCad.app" "/Applications/KiCad/KiCad.app" "$(cask_kicad)"; do
  [ -z "$KICAD_APP" ] && [ -n "$c" ] && [ -x "$c/Contents/MacOS/kicad-cli" ] && KICAD_APP="$c"
done
if [ -z "$KICAD_APP" ]; then
  brew install --cask kicad
  KICAD_APP="/Applications/KiCad/KiCad.app"
  [ -x "$KICAD_APP/Contents/MacOS/kicad-cli" ] || KICAD_APP="$(cask_kicad)"
fi
# the engine and the render scripts default to ~/Applications/KiCad/KiCad.app: keep that path pointing at it
if [ "$KICAD_APP" != "$HOME/Applications/KiCad/KiCad.app" ] && [ ! -e "$HOME/Applications/KiCad/KiCad.app" ]; then
  mkdir -p "$HOME/Applications/KiCad" && ln -s "$KICAD_APP" "$HOME/Applications/KiCad/KiCad.app"
fi
[ -f "$KICAD_APP/Contents/SharedSupport/symbols/MCU_Module.kicad_sym" ] || { echo "KiCad at $KICAD_APP has no symbol libraries."; exit 1; }
V="$("$KICAD_APP/Contents/MacOS/kicad-cli" version)"
echo "KiCad $V at $KICAD_APP"
case "$V" in 10.*) ;; *) echo "Etch needs KiCad 10 (found $V)."; exit 1 ;; esac

say "Engine Python env"
uv sync --project engine

say "Open-source KiCad libraries (engine/parts/extra_libs, about 1.2 GB at pinned commits)"
python3 engine/parts/fetch_extra_libs.py
ETCH_KICAD_APP="$KICAD_APP" uv run --project engine python -m agent.kicad_library --build

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

say "Local settings (.etch.env, no secrets)"
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

say "Keychain secrets"
keychain_set() { # service prompt
  if security find-generic-password -a "$USER" -s "$1" -w >/dev/null 2>&1; then
    echo "$1: already in Keychain"
  else
    read -r -s -p "$2: " v; echo
    [ -n "$v" ] && security add-generic-password -a "$USER" -s "$1" -w "$v" && echo "$1: saved"
  fi
}
keychain_set openai-api-key "OpenAI API key"
keychain_set etch-app-access-code "Access code for the Etch page (you type it in the page)"

say "Done. Start Etch with ./run.sh"

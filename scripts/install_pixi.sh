#!/usr/bin/env bash
# Install Pixi when needed, then initialize the vendored splash_links environment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPLASH_DIR="$ROOT_DIR/splash_links"

usage() {
  cat <<'EOF'
Usage: ./scripts/install_pixi.sh

Installs the Pixi CLI with its official installer when Pixi is not already on
PATH, then runs `pixi install` for the vendored splash_links workspace.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ $# -ne 0 ]]; then
  usage >&2
  exit 2
fi

if [[ ! -f "$SPLASH_DIR/pixi.toml" ]]; then
  echo "Error: splash_links/pixi.toml not found under $ROOT_DIR" >&2
  exit 1
fi

PIXI_BIN="$(command -v pixi || true)"
if [[ -z "$PIXI_BIN" ]]; then
  if ! command -v curl >/dev/null 2>&1; then
    echo "Error: curl is required to download the official Pixi installer." >&2
    exit 1
  fi

  installer="$(mktemp "${TMPDIR:-/tmp}/pixi-install.XXXXXX")"
  cleanup() {
    rm -f "$installer"
  }
  trap cleanup EXIT

  echo "Pixi not found; downloading the official installer from pixi.sh..."
  curl --proto '=https' --tlsv1.2 -fsSL https://pixi.sh/install.sh -o "$installer"
  sh "$installer"

  PIXI_BIN="$(command -v pixi || true)"
  if [[ -z "$PIXI_BIN" && -x "${PIXI_HOME:-${HOME:?}/.pixi}/bin/pixi" ]]; then
    PIXI_BIN="${PIXI_HOME:-$HOME/.pixi}/bin/pixi"
  fi
  if [[ -z "$PIXI_BIN" ]]; then
    echo "Error: Pixi installed, but its executable could not be located." >&2
    echo "Open a new shell and rerun this script." >&2
    exit 1
  fi
else
  echo "Pixi already installed: $($PIXI_BIN --version)"
fi

echo "Initializing splash_links environment..."
"$PIXI_BIN" install --manifest-path "$SPLASH_DIR/pixi.toml"
echo "Ready. Start the complete stack with ./scripts/start_all.sh"

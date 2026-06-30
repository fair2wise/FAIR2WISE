#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UI_DIR="${FAIR2WISE_UI_DIR:-$ROOT_DIR/ui}"
API_URL="${VITE_F2W_AGENT_API_URL:-http://127.0.0.1:8090}"
HOST="${F2W_UI_HOST:-127.0.0.1}"
PORT="${F2W_UI_PORT:-5173}"

if [[ ! -d "$UI_DIR" ]]; then
  echo "error: FAIR2WISE UI directory not found: $UI_DIR" >&2
  echo "set FAIR2WISE_UI_DIR=/path/to/ui or restore $ROOT_DIR/ui" >&2
  exit 1
fi

cd "$UI_DIR"

echo "Starting FAIR2WISE UI"
echo "  url: http://$HOST:$PORT"
echo "  agent_api: $API_URL"

exec env VITE_F2W_AGENT_API_URL="$API_URL" \
  npm run dev -- --host "$HOST" --port "$PORT"

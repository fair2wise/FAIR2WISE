#!/usr/bin/env bash
# Permanently delete the local splash-links SQLite database.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"

usage() {
  echo "Usage: ./scripts/wipe_splash_db.sh"
  echo ""
  echo "Deletes the configured local splash-links SQLite database after typed confirmation."
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ $# -ne 0 ]]; then
  usage >&2
  exit 2
fi

SPLASH_REPO="${SPLASH_LINKS_REPO:-$ROOT_DIR/splash_links}"
SPLASH_DB="${SPLASH_LINKS_DB:-links.sqlite}"
SPLASH_PID_FILE="${SPLASH_LINKS_PID_FILE:-$RUN_DIR/splash_links.pid}"

if [[ "$SPLASH_REPO" != /* ]]; then
  SPLASH_REPO="$ROOT_DIR/$SPLASH_REPO"
fi
if [[ ! -d "$SPLASH_REPO" ]]; then
  echo "Error: splash_links workspace not found: $SPLASH_REPO" >&2
  exit 1
fi
SPLASH_REPO="$(cd "$SPLASH_REPO" && pwd -P)"

case "$SPLASH_DB" in
  :memory:|sqlite:///:memory:)
    echo "Error: an in-memory database cannot be wiped by this utility." >&2
    exit 1
    ;;
  sqlite:///*)
    SPLASH_DB="${SPLASH_DB#sqlite:///}"
    ;;
  *://*)
    echo "Error: only a local SQLite database can be wiped: $SPLASH_DB" >&2
    exit 1
    ;;
esac

if [[ "$SPLASH_DB" != /* ]]; then
  SPLASH_DB="$SPLASH_REPO/$SPLASH_DB"
fi

DB_PARENT="$(dirname "$SPLASH_DB")"
if [[ ! -d "$DB_PARENT" ]]; then
  echo "Error: database directory does not exist: $DB_PARENT" >&2
  exit 1
fi
SPLASH_DB="$(cd "$DB_PARENT" && pwd -P)/$(basename "$SPLASH_DB")"

case "$SPLASH_DB" in
  "$SPLASH_REPO"/*) ;;
  *)
    echo "Error: refusing to delete a database outside $SPLASH_REPO" >&2
    exit 1
    ;;
esac

if [[ ! -f "$SPLASH_DB" ]]; then
  echo "Error: database not found: $SPLASH_DB" >&2
  exit 1
fi

if [[ -f "$SPLASH_PID_FILE" ]]; then
  pid="$(cat "$SPLASH_PID_FILE" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    echo "Error: splash-links is running (PID $pid). Stop start_all.sh before wiping." >&2
    exit 1
  fi
fi

if python3 - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.2)
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", 8081)) == 0 else 1)
PY
then
  echo "Error: port 8081 is active. Stop splash-links before wiping its database." >&2
  exit 1
fi

echo "WARNING: this permanently deletes the entire splash-links database:"
echo "  $SPLASH_DB"
echo "The next 'pixi run serve' will create an empty database."
read -r -p "Type 'WIPE splash_links' to continue: " confirmation

if [[ "$confirmation" != "WIPE splash_links" ]]; then
  echo "Cancelled; database was not changed."
  exit 1
fi

rm -f "$SPLASH_DB" "$SPLASH_DB-wal" "$SPLASH_DB-shm" "$SPLASH_DB-journal"
echo "Splash database wiped."

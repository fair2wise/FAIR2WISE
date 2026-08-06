#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="fair2wise-smoke-$$"
UI_PORT="${F2W_SMOKE_UI_PORT:-15173}"
COMPOSE=(docker compose --project-name "$PROJECT_NAME" --project-directory "$ROOT_DIR")

cleanup() {
  "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

export F2W_UI_PORT="$UI_PORT"

echo "Validating Compose configuration"
"${COMPOSE[@]}" config --quiet

echo "Building and starting an isolated stack"
"${COMPOSE[@]}" up --build --wait

curl -fsS "http://127.0.0.1:${UI_PORT}/" >/dev/null
curl -fsS "http://127.0.0.1:${UI_PORT}/api/health" >/dev/null

graph_count() {
  "${COMPOSE[@]}" exec -T splash python -c \
    "import sqlite3; print(sqlite3.connect('/data/links.sqlite').execute('select count(*) from entities').fetchone()[0])"
}

FIRST_COUNT="$(graph_count)"
if [[ "$FIRST_COUNT" -le 0 ]]; then
  echo "error: seeded graph is empty" >&2
  exit 1
fi

for private_service in splash-db-init splash agent; do
  if "${COMPOSE[@]}" port "$private_service" 2>/dev/null | grep -q .; then
    echo "error: ${private_service} publishes a host port" >&2
    exit 1
  fi
done

echo "Recreating containers while retaining named volumes"
"${COMPOSE[@]}" down --remove-orphans
"${COMPOSE[@]}" up --wait

SECOND_COUNT="$(graph_count)"
if [[ "$SECOND_COUNT" -ne "$FIRST_COUNT" ]]; then
  echo "error: graph count changed after restart (${FIRST_COUNT} -> ${SECOND_COUNT})" >&2
  exit 1
fi

echo "Compose smoke test passed (${SECOND_COUNT} graph nodes; only frontend published)"

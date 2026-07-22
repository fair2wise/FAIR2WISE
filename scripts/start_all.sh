#!/usr/bin/env bash
# start_all.sh - Start splash-links, FAIR2WISE agent backend, and frontend.
# Usage: ./scripts/start_all.sh
# Stop all services: Ctrl+C

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"

SPLASH_HOST="127.0.0.1"
SPLASH_PORT="8081"
SPLASH_REPO="${SPLASH_LINKS_REPO:-$ROOT_DIR/splash_links}"
SPLASH_DB="${SPLASH_LINKS_DB:-links.sqlite}"
BACKEND_HOST="${F2W_AGENT_HOST:-127.0.0.1}"
BACKEND_PORT="${F2W_AGENT_PORT:-8090}"
FRONTEND_HOST="${F2W_UI_HOST:-127.0.0.1}"
FRONTEND_PORT="${F2W_UI_PORT:-5173}"

if [[ "$SPLASH_REPO" != /* ]]; then
  SPLASH_REPO="$ROOT_DIR/$SPLASH_REPO"
fi

BACKEND_SCRIPT="$SCRIPT_DIR/start_agent_backend.sh"
FRONTEND_SCRIPT="$SCRIPT_DIR/start_agent_frontend.sh"
SPLASH_PID_FILE="$RUN_DIR/splash_links.pid"
BACKEND_PID_FILE="$RUN_DIR/agent_backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/agent_frontend.pid"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

port_is_listening() {
  local port="$1"
  python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.2)
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
}

require_free_port() {
  local port="$1"
  local label="$2"
  if port_is_listening "$port"; then
    echo -e "${RED}Error: ${label} port ${port} already in use on 127.0.0.1.${NC}" >&2
    echo -e "${RED}Stop that process or rerun with another port.${NC}" >&2
    exit 1
  fi
}

stop_pid_file() {
  local pid_file="$1"
  local label="$2"

  if [[ ! -f "$pid_file" ]]; then
    return
  fi

  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  rm -f "$pid_file"

  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    return
  fi

  if kill -0 "$pid" 2>/dev/null; then
    echo -e "${YELLOW}Stopping stale ${label} from previous start_all run (PID ${pid})${NC}"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      if ! kill -0 "$pid" 2>/dev/null; then
        return
      fi
      sleep 0.2
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
}

cleanup() {
  if [[ "${CLEANUP_DONE:-0}" == "1" ]]; then
    return
  fi
  CLEANUP_DONE=1
  trap - SIGINT SIGTERM EXIT
  echo ""
  echo -e "${YELLOW}Shutting down FAIR2WISE frontend/backend and splash-links...${NC}"
  kill "${FRONTEND_PID:-}" "${BACKEND_PID:-}" "${SPLASH_PID:-}" 2>/dev/null || true
  wait "${FRONTEND_PID:-}" "${BACKEND_PID:-}" "${SPLASH_PID:-}" 2>/dev/null || true
  rm -f "$FRONTEND_PID_FILE" "$BACKEND_PID_FILE" "$SPLASH_PID_FILE"
  echo -e "${GREEN}Done.${NC}"
}
trap cleanup SIGINT SIGTERM EXIT

wait_for_http() {
  local url="$1"
  local label="$2"
  local pid="$3"
  local attempts="${4:-40}"

  if ! command -v curl >/dev/null 2>&1; then
    echo -e "${YELLOW}curl not found; skipping ${label} readiness check.${NC}"
    return
  fi

  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo -e "${GREEN}${label} ready: ${url}${NC}"
      return
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo -e "${RED}${label} exited during startup. Check logs above.${NC}" >&2
      exit 1
    fi
    sleep 0.5
  done

  echo -e "${RED}${label} did not pass readiness check: ${url}${NC}" >&2
  return 1
}

if ! command -v pixi >/dev/null 2>&1; then
  echo -e "${RED}Error: pixi is required to start splash-links.${NC}" >&2
  echo -e "${RED}Run ./scripts/install_pixi.sh once, then retry.${NC}" >&2
  exit 1
fi

if [[ ! -d "$SPLASH_REPO" || ! -f "$SPLASH_REPO/pixi.toml" ]]; then
  echo -e "${RED}Error: splash_links workspace not found: $SPLASH_REPO${NC}" >&2
  exit 1
fi

if [[ ! -x "$BACKEND_SCRIPT" ]]; then
  echo -e "${RED}Error: missing executable backend script: $BACKEND_SCRIPT${NC}" >&2
  exit 1
fi

if [[ ! -x "$FRONTEND_SCRIPT" ]]; then
  echo -e "${RED}Error: missing executable frontend script: $FRONTEND_SCRIPT${NC}" >&2
  exit 1
fi

mkdir -p "$RUN_DIR"
stop_pid_file "$FRONTEND_PID_FILE" "frontend"
stop_pid_file "$BACKEND_PID_FILE" "backend"
stop_pid_file "$SPLASH_PID_FILE" "splash-links"
require_free_port "$SPLASH_PORT" "Splash/database"
require_free_port "$BACKEND_PORT" "Backend"
require_free_port "$FRONTEND_PORT" "Frontend"

echo -e "${CYAN}==> Starting splash-links database (${SPLASH_HOST}:${SPLASH_PORT})...${NC}"
(cd "$SPLASH_REPO" && exec env SPLASH_LINKS_DB="$SPLASH_DB" pixi run serve) &
SPLASH_PID=$!
echo "$SPLASH_PID" > "$SPLASH_PID_FILE"
echo -e "${GREEN}Splash PID: ${SPLASH_PID}${NC}"

wait_for_http "http://${SPLASH_HOST}:${SPLASH_PORT}/splash_links/health" "Splash/database" "$SPLASH_PID" 120

echo -e "${CYAN}==> Starting FAIR2WISE agent backend (${BACKEND_HOST}:${BACKEND_PORT})...${NC}"
(cd "$ROOT_DIR" && "$BACKEND_SCRIPT") &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$BACKEND_PID_FILE"
echo -e "${GREEN}Backend PID: ${BACKEND_PID}${NC}"

wait_for_http "http://${BACKEND_HOST}:${BACKEND_PORT}/health" "Backend" "$BACKEND_PID" 60

echo -e "${CYAN}==> Starting FAIR2WISE frontend (${FRONTEND_HOST}:${FRONTEND_PORT})...${NC}"
(cd "$ROOT_DIR" && "$FRONTEND_SCRIPT") &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$FRONTEND_PID_FILE"
echo -e "${GREEN}Frontend PID: ${FRONTEND_PID}${NC}"

echo ""
echo -e "${GREEN}==========================================${NC}"
echo -e "${GREEN}  FAIR2WISE UI stack running${NC}"
echo -e "${GREEN}  Frontend : http://${FRONTEND_HOST}:${FRONTEND_PORT}${NC}"
echo -e "${GREEN}  Backend  : http://${BACKEND_HOST}:${BACKEND_PORT}${NC}"
echo -e "${GREEN}  Splash   : http://${SPLASH_HOST}:${SPLASH_PORT}${NC}"
echo -e "${GREEN}  Press Ctrl+C to stop all services.${NC}"
echo -e "${GREEN}==========================================${NC}"
echo ""

wait "$SPLASH_PID" "$BACKEND_PID" "$FRONTEND_PID"

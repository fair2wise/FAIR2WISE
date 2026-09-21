#!/usr/bin/env bash
# Daemonize the RSoXS JSON-KG agent + Vite UI so a Cursor session exit does
# not kill the stack. Does not start or stop paper extract (scripts/run.py).
# Prefer `docker compose up` as the documented product path.
#
# ── Process isolation note ────────────────────────────────────────────────────
# The paper extract process (scripts/run.py) must be started separately and
# will survive UI/agent restarts:
#
#   nohup python scripts/run.py ...  &   # background in current shell
#   # — or —
#   tmux new-session -d -s extract 'python scripts/run.py ...'
#
# `docker compose up` starts agent+UI only; it does NOT start extraction.
# `docker compose down` (or this script's `stop`) will NOT stop extraction.
# ─────────────────────────────────────────────────────────────────────────────
#
# Usage:
#   ./scripts/start_rsoxs_stack.sh
#   ./scripts/start_rsoxs_stack.sh stop
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"

BACKEND_PID_FILE="$LOG_DIR/rsoxs_backend.pid"
FRONTEND_PID_FILE="$LOG_DIR/rsoxs_frontend.pid"
BACKEND_LOG="$LOG_DIR/rsoxs_backend.stdout"
FRONTEND_LOG="$LOG_DIR/rsoxs_frontend.stdout"

AGENT_PORT="${F2W_AGENT_PORT:-8090}"

# UI port: try 5173 first, then 5175; never use 5174 (reserved for SSH).
if [[ "${F2W_UI_PORT:-}" == "5174" ]]; then
  echo "[ERROR] Port 5174 is reserved for SSH; use F2W_UI_PORT=5175 (or 5173)" >&2
  exit 1
fi
if [[ -n "${F2W_UI_PORT:-}" ]]; then
  UI_PORT="$F2W_UI_PORT"
elif ! lsof -ti:5173 >/dev/null 2>&1; then
  UI_PORT=5173
elif ! lsof -ti:5175 >/dev/null 2>&1; then
  UI_PORT=5175
else
  UI_PORT=5175
  echo "[WARN] Both ports 5173 and 5175 appear in use; defaulting to ${UI_PORT}" >&2
fi

is_extract_pid() {
  local pid="$1"
  local cmd
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$cmd" == *scripts/run.py* ]]
}

stop_pidfile() {
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
  if is_extract_pid "$pid"; then
    echo "refusing to stop paper extract PID ${pid}" >&2
    return
  fi
  if kill -0 "$pid" 2>/dev/null; then
    echo "Stopping ${label} (PID ${pid})"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      if ! kill -0 "$pid" 2>/dev/null; then
        return
      fi
      sleep 0.1
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
}

if [[ "${1:-}" == "stop" ]]; then
  stop_pidfile "$FRONTEND_PID_FILE" "RSoXS UI"
  stop_pidfile "$BACKEND_PID_FILE" "RSoXS agent"
  echo "RSoXS UI/agent stack stopped (paper extract was not touched)."
  exit 0
fi

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

# Prefer the harvest/runtime venv so `python3` in start_agent_backend.sh resolves.
if [[ -x "$ROOT_DIR/.venv-harvest/bin/python" ]]; then
  export PATH="$ROOT_DIR/.venv-harvest/bin:$PATH"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  export PATH="$ROOT_DIR/.venv/bin:$PATH"
fi

# Always probe; a bind copied from .env may already be unassigned.
unset CBORG_IPV6_BIND
export CBORG_FORCE_IPV6="${CBORG_FORCE_IPV6:-1}"
export CBORG_IP_FAMILY="${CBORG_IP_FAMILY:-ipv6}"
export F2W_BACKEND="${F2W_BACKEND:-cborg}"
export F2W_MODEL="${F2W_MODEL:-lbl/cborg-chat}"
export F2W_KG_MODE="${F2W_KG_MODE:-json}"
LATEST_OPS="storage/kg/matkg_bl1101_v1.json"
best=-1
for f in "$ROOT_DIR"/storage/kg/matkg_bl1101_v*.json; do
  base="$(basename "$f")"
  [[ "$base" == *bak* ]] && continue
  n="${base#matkg_bl1101_v}"
  n="${n%.json}"
  [[ "$n" =~ ^[0-9]+$ ]] || continue
  if (( n > best )); then
    best=$n
    LATEST_OPS="storage/kg/${base}"
  fi
done
LATEST_SCIENCE="storage/kg/matkg_rsoxs_v1.json"
best_s=-1
for f in "$ROOT_DIR"/storage/kg/matkg_rsoxs_v*.json; do
  base="$(basename "$f")"
  [[ "$base" == *bak* ]] && continue
  n="${base#matkg_rsoxs_v}"
  n="${n%.json}"
  [[ "$n" =~ ^[0-9]+$ ]] || continue
  if (( n > best_s )); then
    best_s=$n
    LATEST_SCIENCE="storage/kg/${base}"
  fi
done
export F2W_GRAPH="${F2W_GRAPH:-${LATEST_SCIENCE},${LATEST_OPS}}"
export F2W_SCHEMA="${F2W_SCHEMA:-storage/schema/rsoxs_schema.yaml}"
export F2W_WORKDIR="${F2W_WORKDIR:-runs/ui_session_rsoxs}"
export F2W_AGENT_HOST="${F2W_AGENT_HOST:-127.0.0.1}"
export F2W_AGENT_PORT="$AGENT_PORT"
export F2W_UI_HOST="${F2W_UI_HOST:-127.0.0.1}"
export F2W_UI_PORT="$UI_PORT"
export VITE_F2W_AGENT_API_URL="${VITE_F2W_AGENT_API_URL:-http://127.0.0.1:${AGENT_PORT}}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
# Local Tiled only. Never follow ALS production hosts from a leftover .env.
if [[ "${TILED_URI:-}" == *als.lbl.gov* ]]; then
  echo "warning: ignoring ALS TILED_URI; using http://127.0.0.1:8000" >&2
  unset TILED_URI
fi
export TILED_URI="${TILED_URI:-http://127.0.0.1:8000}"

stop_pidfile "$FRONTEND_PID_FILE" "RSoXS UI"
stop_pidfile "$BACKEND_PID_FILE" "RSoXS agent"

# ── Port conflict check (runs after stopping old stack) ───────────────────────
if lsof -ti:"$AGENT_PORT" >/dev/null 2>&1; then
  echo "[ERROR] Port ${AGENT_PORT} in use. Run: kill \$(lsof -ti:${AGENT_PORT})" >&2
  exit 1
fi
if lsof -ti:"$UI_PORT" >/dev/null 2>&1; then
  echo "[ERROR] Port ${UI_PORT} in use. Run: kill \$(lsof -ti:${UI_PORT})" >&2
  exit 1
fi
export VITE_PORT="$UI_PORT"

daemonize() {
  local pid_file="$1"
  local log_file="$2"
  shift 2
  # Double-fork + os.setsid so a Cursor/session hangup does not kill the stack.
  # Darwin has no setsid(1); Python's os.setsid() works on both Darwin and Linux.
  local cmd_json
  cmd_json="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@")"
  python3 - "$pid_file" "$log_file" "$cmd_json" <<'PY'
import json, os, sys, time
pid_file, log_file, cmd_json = sys.argv[1], sys.argv[2], sys.argv[3]
cmd = json.loads(cmd_json)
if os.fork() > 0:
    sys.exit(0)
os.setsid()
if os.fork() > 0:
    sys.exit(0)
os.umask(0)
os.chdir("/")
with open(pid_file, "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()) + "\n")
log = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(log, 1)
os.dup2(log, 2)
os.close(log)
devnull = os.open(os.devnull, os.O_RDONLY)
os.dup2(devnull, 0)
os.close(devnull)
os.execvp(cmd[0], cmd)
PY
  for _ in $(seq 1 50); do
    if [[ -s "$pid_file" ]]; then
      break
    fi
    sleep 0.05
  done
}

echo "Starting RSoXS agent+UI (daemonized)"
echo "  UI:    http://127.0.0.1:${UI_PORT}"
echo "  Agent: http://127.0.0.1:${AGENT_PORT}"
echo "  graph: ${F2W_GRAPH}"
echo "  logs:  ${LOG_DIR}/rsoxs_backend.stdout ${LOG_DIR}/rsoxs_frontend.stdout"

daemonize "$BACKEND_PID_FILE" "$BACKEND_LOG" "$ROOT_DIR/scripts/start_agent_backend.sh"
daemonize "$FRONTEND_PID_FILE" "$FRONTEND_LOG" "$ROOT_DIR/scripts/start_agent_frontend.sh"

echo "PIDs: agent=$(cat "$BACKEND_PID_FILE") ui=$(cat "$FRONTEND_PID_FILE")"
echo "Stop with: ./scripts/start_rsoxs_stack.sh stop"

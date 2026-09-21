#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HOST="${F2W_AGENT_HOST:-127.0.0.1}"
PORT="${F2W_AGENT_PORT:-8090}"
BACKEND="${F2W_BACKEND:-cborg}"
MODEL="${F2W_MODEL:-lbl/cborg-chat}"
KG_MODE="${F2W_KG_MODE:-splash}"
if [[ "$KG_MODE" == "json" ]]; then
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
  GRAPH="${F2W_GRAPHS:-${F2W_GRAPH:-${LATEST_SCIENCE},${LATEST_OPS}}}"
else
  GRAPH="${F2W_GRAPHS:-${F2W_GRAPH:-storage/kg/matkg_with_code.json}}"
fi
SEED_TERMS="${F2W_SEED_TERMS:-}"
SCHEMA="${F2W_SCHEMA:-storage/schema/matkg_schema.yaml}"
WORKDIR="${F2W_WORKDIR:-runs/ui_session_splash}"
SPLASH_REPO="${SPLASH_LINKS_REPO:-$ROOT_DIR/splash_links}"
if [[ "$SPLASH_REPO" != /* ]]; then
  SPLASH_REPO="$ROOT_DIR/$SPLASH_REPO"
fi
DOWNLOAD_DELAY="${F2W_DOWNLOAD_DELAY:-0}"
MAX_ROUNDS="${F2W_MAX_ROUNDS:-3}"
MAX_PAPERS="${F2W_MAX_PAPERS:-1}"
CANDIDATE_POOL="${F2W_CANDIDATE_POOL:-25}"
WORKERS="${F2W_WORKERS:-8}"
WORKFLOW_MODE="${F2W_WORKFLOW_MODE:-agentic}"
EXTRACTION_MODE="${F2W_EXTRACTION_MODE:-targeted}"
TARGETED_MAX_PAGES="${F2W_TARGETED_MAX_PAGES:-6}"
SPLASH_HEALTH_URL="${F2W_SPLASH_HEALTH_URL:-http://127.0.0.1:8081/splash_links/health}"

# CBorg allowlists a global IPv6. Probe at process start; never keep a stale bind.
export CBORG_FORCE_IPV6="${CBORG_FORCE_IPV6:-1}"
export CBORG_IP_FAMILY="${CBORG_IP_FAMILY:-ipv6}"
if [[ -n "${CBORG_IPV6_BIND:-}" ]]; then
  if ! python3 -c 'import os, socket, ipaddress
addr = os.environ.get("CBORG_IPV6_BIND", "").split("%", 1)[0]
try:
    ipaddress.IPv6Address(addr)
    sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    sock.bind((addr, 0))
    sock.close()
except OSError:
    raise SystemExit(1)
except ValueError:
    raise SystemExit(1)
'; then
    echo "warning: CBORG_IPV6_BIND is not assigned on this host; probing a current global IPv6" >&2
    unset CBORG_IPV6_BIND
  fi
fi

if [[ "$KG_MODE" != "splash" && "$KG_MODE" != "json" ]]; then
  echo "warning: F2W_KG_MODE=$KG_MODE overrides backend default splash" >&2
fi

if [[ "$KG_MODE" == "splash" && ! -d "$SPLASH_REPO" ]]; then
  echo "error: splash_links repo not found: $SPLASH_REPO" >&2
  echo "set SPLASH_LINKS_REPO=/path/to/splash_links" >&2
  exit 1
fi

if [[ "$KG_MODE" == "splash" ]] && command -v curl >/dev/null 2>&1; then
  if ! curl -fsS "$SPLASH_HEALTH_URL" >/dev/null 2>&1; then
    echo "warning: splash-links server not responding at $SPLASH_HEALTH_URL" >&2
    echo "start it in another terminal: cd \"$SPLASH_REPO\" && pixi run serve" >&2
  fi
fi

ARGS=(
  python3 -m app.modules.launchers.f2w_agent
  --backend "$BACKEND"
  --model "$MODEL"
  --kg-mode "$KG_MODE"
  --schema "$SCHEMA"
  --workdir "$WORKDIR"
  --splash-repo "$SPLASH_REPO"
  --download-delay "$DOWNLOAD_DELAY"
  --max-rounds "$MAX_ROUNDS"
  --max-papers "$MAX_PAPERS"
  --candidate-pool "$CANDIDATE_POOL"
  --workers "$WORKERS"
  --workflow-mode "$WORKFLOW_MODE"
  --extraction-mode "$EXTRACTION_MODE"
  --targeted-max-pages "$TARGETED_MAX_PAGES"
  --allow-splash-wipe
)
IFS=',' read -ra GRAPH_LIST <<< "$GRAPH"
for g in "${GRAPH_LIST[@]}"; do
  g="${g#"${g%%[![:space:]]*}"}"
  g="${g%"${g##*[![:space:]]}"}"
  if [[ -n "$g" ]]; then
    ARGS+=(--graph "$g")
  fi
done
if [[ -n "$SEED_TERMS" ]]; then
  ARGS+=(--seed-terms "$SEED_TERMS")
fi
ARGS+=(api --host "$HOST" --port "$PORT")
# Vite defaults to 5173; RSoXS UI uses 5175 when 5173 is taken.
ARGS+=(
  --cors-origin "http://127.0.0.1:5175"
  --cors-origin "http://localhost:5175"
  --cors-origin "http://127.0.0.1:5173"
  --cors-origin "http://localhost:5173"
)

echo "Starting FAIR2WISE agent API"
echo "  url: http://$HOST:$PORT"
echo "  kg_mode: $KG_MODE"
echo "  graph: $GRAPH"
echo "  schema: $SCHEMA"
echo "  seed_terms: ${SEED_TERMS:-<none>}"
echo "  workdir: $WORKDIR"
echo "  max_rounds: $MAX_ROUNDS"
echo "  max_papers: $MAX_PAPERS"
echo "  workers: $WORKERS"
echo "  workflow_mode: $WORKFLOW_MODE"
echo "  extraction_mode: $EXTRACTION_MODE"
echo "  targeted_max_pages: $TARGETED_MAX_PAGES"

exec "${ARGS[@]}"

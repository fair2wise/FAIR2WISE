#!/usr/bin/env bash
# ── FAIR2WISE local agent API launcher ────────────────────────────────────────
# Starts python -m app.modules.launchers.f2w_agent api (default :8090).
# Loads $ROOT/.env when present. Secrets stay in .env (gitignored).
#
# Process isolation: paper extract (scripts/run.py) must be started separately
# so it survives agent restarts and terminal closure:
#
#   nohup python scripts/run.py ...  &   # background in current shell
#   # — or —
#   tmux new-session -d -s extract 'python scripts/run.py ...'
#
# `docker compose up` starts agent+UI only; extraction is not affected.
# Stopping this script (or `docker compose down`) will NOT stop extraction.
#
# Accepted environment variables (defaults in parentheses):
#
#   Agent / LLM
#     F2W_AGENT_HOST          bind address (127.0.0.1)
#     F2W_AGENT_PORT          API port (8090)
#     F2W_BACKEND             cborg | cborg-openai | ollama (cborg)
#     F2W_MODEL               chat model (lbl/cborg-chat)
#     CBORG_API_KEY           required for CBorg; never log
#     CBORG_BASE_URL          CBorg-compatible base URL
#     CBORG_FORCE_IPV6        probe/bind IPv6 (1)
#     CBORG_IP_FAMILY         ipv6 (ipv6)
#     CBORG_IPV6_BIND         optional GUA; unset if the address is stale
#
#   Knowledge graphs (JSON mode is the local default)
#     F2W_KG_MODE             json | splash (json)
#     F2W_GRAPH               single graph path (legacy)
#     F2W_GRAPHS              comma-separated JSON paths (overrides latest vN)
#     F2W_SCHEMA              LinkML path (storage/schema/matkg_schema.yaml)
#     F2W_SEED_TERMS          optional cumulative extracted-terms JSON
#     F2W_WORKDIR             session dir (runs/ui_session_splash)
#     See storage/schema/README.md and mkdocs/docs/architecture.md
#
#   Splash (only when F2W_KG_MODE=splash)
#     SPLASH_LINKS_REPO       path to splash_links checkout
#     F2W_SPLASH_HEALTH_URL   health URL (http://127.0.0.1:8081/splash_links/health)
#
#   Orchestration / extract (API process; not the background paper harvest)
#     F2W_DOWNLOAD_DELAY      seconds between PDF attempts (0)
#     F2W_MAX_ROUNDS          (3)
#     F2W_MAX_PAPERS          (1)
#     F2W_CANDIDATE_POOL      (25)
#     F2W_WORKERS             extract workers (8)
#     F2W_WORKFLOW_MODE       agentic | deterministic (agentic)
#     F2W_EXTRACTION_MODE     targeted | full (targeted)
#     F2W_TARGETED_MAX_PAGES  (6)
#
#   Live Tiled Graph (experiment identity; not JSON ESAF nodes)
#     TILED_URI               GraphQL/catalog base (http://127.0.0.1:8765)
#     TILED_API_KEY           Tiled Apikey; this script defaults to a dev placeholder
#                             if unset — put a real key in .env for anything non-local
#     TILED_API_KEY_SCHEME    Authorization scheme (Apikey)
#     TILED_CATALOG_URI       SQLAlchemy catalog DSN (sqlite under storage/)
#     F2W_LIVE_TILED          1 after local Tiled is healthy
#     TILED_GRAPH_ENABLED     alias for enabling live Tiled
#
#   Optional chat extras (read by the API, not this script)
#     MP_API_KEY              Materials Project; never log
#     F2W_SOURCE_RAG          hybrid PDF/ops RAG
#     F2W_TRACE_ENABLED       write per-turn traces (1)
#     F2W_TRACE_DIR           trace root (runs/traces)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Load .env (if present) so TILED_API_KEY, TILED_CATALOG_URI, etc. are available
# without requiring the caller to pre-export them.
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

HOST="${F2W_AGENT_HOST:-127.0.0.1}"
PORT="${F2W_AGENT_PORT:-8090}"
BACKEND="${F2W_BACKEND:-cborg}"
MODEL="${F2W_MODEL:-lbl/cborg-chat}"
KG_MODE="${F2W_KG_MODE:-json}"
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

# ── CBorg IPv6 hardening ──────────────────────────────────────────────────────
# CBorg allowlists a global IPv6. Always probe at process start; never silently
# keep a stale bind address from a previous run or .env.
export CBORG_FORCE_IPV6="${CBORG_FORCE_IPV6:-1}"
export CBORG_IP_FAMILY="${CBORG_IP_FAMILY:-ipv6}"

# Detect any globally-routable IPv6 on a live interface.
# macOS: ifconfig; Linux: ip -6 addr show. Excludes loopback (::1) and
# link-local (fe80::) addresses which cannot reach api.cborg.lbl.gov.
_global_ipv6=""
if command -v ifconfig >/dev/null 2>&1; then
  _global_ipv6="$(ifconfig 2>/dev/null \
    | awk '/inet6/{gsub(/\/.*/, "", $2); addr=$2;
           if (addr !~ /^::1$/ && addr !~ /^fe80/) print addr}' \
    | head -1)"
elif command -v ip >/dev/null 2>&1; then
  _global_ipv6="$(ip -6 addr show 2>/dev/null \
    | awk '/inet6/ && !/::1/ && !/fe80/{gsub(/\/.*/, "", $2); print $2}' \
    | head -1)"
fi
if [[ -z "${_global_ipv6:-}" ]]; then
  echo "[WARN] No global IPv6 detected — CBorg may fall back to IPv4" >&2
fi

# If CBORG_IPV6_BIND is set, verify the address is still assigned to a live
# interface using ifconfig/ip. Never silently keep a stale bind.
if [[ -n "${CBORG_IPV6_BIND:-}" ]]; then
  _bind_clean="${CBORG_IPV6_BIND%%%*}"  # strip zone ID (%en0, %eth0, …)
  _bind_live=0
  if command -v ifconfig >/dev/null 2>&1; then
    ifconfig 2>/dev/null | grep -qF "$_bind_clean" && _bind_live=1 || true
  elif command -v ip >/dev/null 2>&1; then
    ip -6 addr show 2>/dev/null | grep -qF "$_bind_clean" && _bind_live=1 || true
  fi
  if [[ "$_bind_live" -eq 0 ]]; then
    echo "[WARN] CBORG_IPV6_BIND=${CBORG_IPV6_BIND} is not assigned to any interface; unsetting (fresh probe will run)" >&2
    unset CBORG_IPV6_BIND
  fi
fi
# ─────────────────────────────────────────────────────────────────────────────

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

# ── Port conflict check ───────────────────────────────────────────────────────
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "[ERROR] Port ${PORT} in use. Run: kill \$(lsof -ti:${PORT})" >&2
  exit 1
fi


# Prefer the project venv Python so all deps (dotenv, PyMuPDF, …) are
# available even when the shell's PATH doesn't have the venv activated.
# Falls back to python3 / python if the venv isn't present.
if [[ -x "$ROOT_DIR/.venv-harvest/bin/python" ]]; then
  PYTHON="$ROOT_DIR/.venv-harvest/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  PYTHON="python"
fi

# ── Local Tiled Graph (non-Docker path) ──────────────────────────────────────
# Must come after PYTHON is set above.
TILED_URI_LOCAL="${TILED_URI:-http://127.0.0.1:8765}"
TILED_HEALTH_URL="${TILED_URI_LOCAL}/api/v1/"
# Default catalog URI (SQLite, absolute path).  Can be overridden by .env or
# the environment; Tiled config uses ${TILED_CATALOG_URI} without bash defaults.
export TILED_CATALOG_URI="${TILED_CATALOG_URI:-sqlite+aiosqlite:///${ROOT_DIR}/storage/tiled_catalog.db}"
export TILED_API_KEY="${TILED_API_KEY:-devonlykeychangeinproduction00000000000000000000000000000000000000}"

if "$PYTHON" -c 'import tiled' >/dev/null 2>&1; then
  if ! curl -sf "$TILED_HEALTH_URL" > /dev/null 2>&1; then
    echo "Starting local Tiled server (F2W instance on :8765)..."
    mkdir -p "$ROOT_DIR/logs"
    nohup "$PYTHON" -m tiled serve config "$ROOT_DIR/storage/tiled_config.yml" \
      --host 127.0.0.1 --port 8765 \
      > "$ROOT_DIR/logs/tiled_$(date +%Y%m%d_%H%M%S).log" 2>&1 &
    # Wait up to 15s for Tiled to become healthy
    for _i in $(seq 1 15); do
      sleep 1
      curl -sf "$TILED_HEALTH_URL" > /dev/null 2>&1 && break
    done
  fi
  if curl -sf "$TILED_HEALTH_URL" > /dev/null 2>&1; then
    echo "Tiled healthy — seeding sim data (idempotent)..."
    "$PYTHON" scripts/ingest_tiled_sim.py --to-tiled --tiled-uri "$TILED_URI_LOCAL" || echo "warning: tiled seed failed (non-fatal)" >&2
    export F2W_LIVE_TILED=1
    export TILED_URI="$TILED_URI_LOCAL"
  else
    echo "warning: Tiled did not start; live Tiled disabled" >&2
  fi
fi

export F2W_TRACE_ENABLED="${F2W_TRACE_ENABLED:-1}"
export F2W_TRACE_DIR="${F2W_TRACE_DIR:-runs/traces}"

ARGS=(
  "$PYTHON" -m app.modules.launchers.f2w_agent
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

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HOST="${F2W_AGENT_HOST:-127.0.0.1}"
PORT="${F2W_AGENT_PORT:-8090}"
BACKEND="${F2W_BACKEND:-cborg}"
MODEL="${F2W_MODEL:-lbl/cborg-chat}"
KG_MODE="${F2W_KG_MODE:-splash}"
GRAPH="${F2W_GRAPH:-storage/kg/matkg_with_code.json}"
SEED_TERMS="${F2W_SEED_TERMS:-}"
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

if [[ "$KG_MODE" != "splash" ]]; then
  echo "warning: F2W_KG_MODE=$KG_MODE overrides backend default splash" >&2
fi

if [[ "$KG_MODE" == "splash" && ! -d "$SPLASH_REPO" ]]; then
  echo "error: splash_links repo not found: $SPLASH_REPO" >&2
  echo "set SPLASH_LINKS_REPO=/path/to/splash_links" >&2
  exit 1
fi

if [[ "$KG_MODE" == "splash" ]] && command -v curl >/dev/null 2>&1; then
  if ! curl -fsS "http://127.0.0.1:8081/docs" >/dev/null 2>&1; then
    echo "warning: splash-links server not responding at http://127.0.0.1:8081" >&2
    echo "start it in another terminal: cd \"$SPLASH_REPO\" && pixi run serve" >&2
  fi
fi

ARGS=(
  python3 f2w_agent.py
  --backend "$BACKEND"
  --model "$MODEL"
  --kg-mode "$KG_MODE"
  --graph "$GRAPH"
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
if [[ -n "$SEED_TERMS" ]]; then
  ARGS+=(--seed-terms "$SEED_TERMS")
fi
ARGS+=(api --host "$HOST" --port "$PORT")

echo "Starting FAIR2WISE agent API"
echo "  url: http://$HOST:$PORT"
echo "  kg_mode: $KG_MODE"
echo "  graph: $GRAPH"
echo "  seed_terms: ${SEED_TERMS:-<none>}"
echo "  workdir: $WORKDIR"
echo "  max_rounds: $MAX_ROUNDS"
echo "  max_papers: $MAX_PAPERS"
echo "  workers: $WORKERS"
echo "  workflow_mode: $WORKFLOW_MODE"
echo "  extraction_mode: $EXTRACTION_MODE"
echo "  targeted_max_pages: $TARGETED_MAX_PAGES"

exec "${ARGS[@]}"

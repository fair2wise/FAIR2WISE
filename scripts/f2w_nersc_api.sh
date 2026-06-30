#!/usr/bin/env bash
set -euo pipefail

cd "${F2W_REPO:-/pscratch/sd/m/mateo/f2wlocal}"
source .venv/bin/activate
if [[ -f ~/.f2w_nersc_env ]]; then
  source ~/.f2w_nersc_env
fi

F2W_KG_MODE="${F2W_KG_MODE:-splash}"
SPLASH_LINKS_REPO="${SPLASH_LINKS_REPO:-/pscratch/sd/m/mateo/splash_links}"

if [[ "$F2W_KG_MODE" == "splash" && ! -d "$SPLASH_LINKS_REPO" ]]; then
  echo "error: splash_links repo not found: $SPLASH_LINKS_REPO" >&2
  echo "set SPLASH_LINKS_REPO=/path/to/splash_links or use F2W_KG_MODE=json" >&2
  exit 1
fi

python3 f2w_agent.py \
  --backend "${F2W_BACKEND:-cborg}" \
  --model "${F2W_MODEL:-lbl/cborg-chat}" \
  --kg-mode "$F2W_KG_MODE" \
  --graph "${F2W_GRAPH:-storage/kg/matkg_xray_papers_cborg_chat.json}" \
  --seed-terms "${F2W_SEED_TERMS:-storage/terminology/extracted_terms_xray_papers_cborg_chat.json}" \
  --workdir "${F2W_WORKDIR:-runs/nersc_3agent_api}" \
  --splash-repo "$SPLASH_LINKS_REPO" \
  --download-delay "${F2W_DOWNLOAD_DELAY:-1.0}" \
  --max-rounds "${F2W_MAX_ROUNDS:-2}" \
  --max-papers "${F2W_MAX_PAPERS:-1}" \
  --candidate-pool "${F2W_CANDIDATE_POOL:-25}" \
  --workers "${F2W_WORKERS:-1}" \
  --allow-splash-wipe \
  api \
  --host "${F2W_AGENT_HOST:-127.0.0.1}" \
  --port "${F2W_AGENT_PORT:-8090}" \
  --cors-origin "${F2W_CORS_ORIGIN:-http://127.0.0.1:5173}" \
  --cors-origin "http://localhost:5173"

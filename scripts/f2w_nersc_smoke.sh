#!/usr/bin/env bash
set -euo pipefail

QUESTION="${*:-What is find_scattering_peaks used for?}"

# Required: the FAIR2WISE checkout on NERSC (no personal default; e.g. /pscratch/sd/<l>/<user>/f2wlocal).
if [[ -z "${F2W_REPO:-}" ]]; then
  echo "error: F2W_REPO is not set; export F2W_REPO=/path/to/f2wlocal (the FAIR2WISE checkout on NERSC)" >&2
  exit 2
fi
cd "$F2W_REPO"
source .venv/bin/activate
if [[ -f ~/.f2w_nersc_env ]]; then
  source ~/.f2w_nersc_env
fi

# JSON (read-only, no Splash service) is the default; the legacy editable mode needs
# F2W_KG_MODE=splash together with SPLASH_LINKS_REPO (owner decision 2026-11-09, Q7).
F2W_KG_MODE="${F2W_KG_MODE:-json}"
SPLASH_ARGS=()
if [[ "$F2W_KG_MODE" == "splash" ]]; then
  if [[ -z "${SPLASH_LINKS_REPO:-}" ]]; then
    echo "error: F2W_KG_MODE=splash needs SPLASH_LINKS_REPO=/path/to/splash_links (or use F2W_KG_MODE=json)" >&2
    exit 2
  fi
  if [[ ! -d "$SPLASH_LINKS_REPO" ]]; then
    echo "error: splash_links repo not found: $SPLASH_LINKS_REPO" >&2
    exit 1
  fi
  SPLASH_ARGS=(--splash-repo "$SPLASH_LINKS_REPO")
fi

python3 -m app.modules.launchers.f2w_agent \
  --backend "${F2W_BACKEND:-cborg}" \
  --model "${F2W_MODEL:-lbl/cborg-chat}" \
  --kg-mode "$F2W_KG_MODE" \
  --graph "${F2W_GRAPH:-storage/kg/matkg_xray_papers_cborg_chat.json}" \
  --seed-terms "${F2W_SEED_TERMS:-storage/terminology/extracted_terms_xray_papers_cborg_chat.json}" \
  --workdir "${F2W_WORKDIR:-runs/nersc_3agent}" \
  ${SPLASH_ARGS[@]+"${SPLASH_ARGS[@]}"} \
  --max-rounds "${F2W_MAX_ROUNDS:-1}" \
  --max-papers "${F2W_MAX_PAPERS:-1}" \
  --workers "${F2W_WORKERS:-1}" \
  ask "$QUESTION"

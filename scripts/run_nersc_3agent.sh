#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run the full FAIR2WISE 3-agent loop on NERSC.

Required env:
  NERSC_USER=mateo
  NERSC_REPO=/pscratch/sd/m/mateo/f2wlocal

Optional env:
  NERSC_HOST=perlmutter.nersc.gov
  SSH_OPTS="-o PubkeyAuthentication=no"
  F2W_BACKEND=cborg
  F2W_MODEL=lbl/cborg-chat
  F2W_KG_MODE=splash
  F2W_GRAPH=storage/kg/matkg_xray_papers_cborg_chat.json
  F2W_SEED_TERMS=storage/terminology/extracted_terms_xray_papers_cborg_chat.json
  SPLASH_LINKS_REPO=/pscratch/sd/m/mateo/splash_links
  F2W_WORKDIR=runs/nersc_3agent
  F2W_MAX_ROUNDS=3
  F2W_MAX_PAPERS=3
  F2W_CANDIDATE_POOL=25
  F2W_WORKERS=4

Usage:
  scripts/run_nersc_3agent.sh status
  scripts/run_nersc_3agent.sh ask "question text"
  scripts/run_nersc_3agent.sh chat

Remote secrets:
  Put CBORG_API_KEY, OPENALEX_EMAIL, GITHUB_TOKEN, etc. in ~/.f2w_nersc_env
  on NERSC. This script sources that file before running the agent launcher module.
EOF
}

need_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing env: $name" >&2
    exit 2
  fi
}

shell_quote() {
  printf "%q" "$1"
}

MODE="${1:-}"
if [[ -z "$MODE" || "$MODE" == "-h" || "$MODE" == "--help" ]]; then
  usage
  exit 0
fi
shift || true

case "$MODE" in
  status|ask|chat) ;;
  *)
    echo "Unknown mode: $MODE" >&2
    usage
    exit 2
    ;;
esac

QUESTION="$*"
if [[ "$MODE" == "ask" && -z "$QUESTION" ]]; then
  echo "ask mode needs a question" >&2
  exit 2
fi

need_env NERSC_USER
need_env NERSC_REPO

NERSC_HOST="${NERSC_HOST:-perlmutter.nersc.gov}"
SSH_OPTS="${SSH_OPTS:-}"
REMOTE="${NERSC_USER}@${NERSC_HOST}"

F2W_BACKEND="${F2W_BACKEND:-cborg}"
F2W_MODEL="${F2W_MODEL:-lbl/cborg-chat}"
F2W_KG_MODE="${F2W_KG_MODE:-splash}"
F2W_GRAPH="${F2W_GRAPH:-storage/kg/matkg_xray_papers_cborg_chat.json}"
F2W_SEED_TERMS="${F2W_SEED_TERMS:-storage/terminology/extracted_terms_xray_papers_cborg_chat.json}"
F2W_WORKDIR="${F2W_WORKDIR:-runs/nersc_3agent}"
F2W_MAX_ROUNDS="${F2W_MAX_ROUNDS:-3}"
F2W_MAX_PAPERS="${F2W_MAX_PAPERS:-3}"
F2W_CANDIDATE_POOL="${F2W_CANDIDATE_POOL:-25}"
F2W_WORKERS="${F2W_WORKERS:-4}"
SPLASH_LINKS_REPO="${SPLASH_LINKS_REPO:-/pscratch/sd/m/mateo/splash_links}"

CMD="cd $(shell_quote "$NERSC_REPO")"
CMD+=" && source .venv/bin/activate"
CMD+=" && if [[ -f ~/.f2w_nersc_env ]]; then source ~/.f2w_nersc_env; fi"
CMD+=" && python3 -m app.modules.launchers.f2w_agent"
CMD+=" --backend $(shell_quote "$F2W_BACKEND")"
CMD+=" --model $(shell_quote "$F2W_MODEL")"
CMD+=" --kg-mode $(shell_quote "$F2W_KG_MODE")"
CMD+=" --graph $(shell_quote "$F2W_GRAPH")"
CMD+=" --seed-terms $(shell_quote "$F2W_SEED_TERMS")"
CMD+=" --workdir $(shell_quote "$F2W_WORKDIR")"
CMD+=" --splash-repo $(shell_quote "$SPLASH_LINKS_REPO")"
CMD+=" --max-rounds $(shell_quote "$F2W_MAX_ROUNDS")"
CMD+=" --max-papers $(shell_quote "$F2W_MAX_PAPERS")"
CMD+=" --candidate-pool $(shell_quote "$F2W_CANDIDATE_POOL")"
CMD+=" --workers $(shell_quote "$F2W_WORKERS")"
if [[ "$F2W_KG_MODE" == "splash" ]]; then
  CMD+=" --allow-splash-wipe"
fi

if [[ "$MODE" == "ask" ]]; then
  CMD+=" ask $(shell_quote "$QUESTION")"
else
  CMD+=" $MODE"
fi

# shellcheck disable=SC2086
ssh -t $SSH_OPTS "$REMOTE" "bash -lc $(shell_quote "$CMD")"

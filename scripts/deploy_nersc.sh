#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Deploy/update FAIR2WISE extractor code on NERSC.

Required env:
  NERSC_USER=<username>
  NERSC_REPO=/pscratch/sd/<first-letter>/<username>/f2wlocal

Optional env:
  NERSC_HOST=perlmutter.nersc.gov
  NERSC_ENDPOINT=f2w-extractor
  NERSC_PDFS=runs/hpc_extract/pdfs
  NERSC_TERMS=runs/hpc_extract/terms.json
  NERSC_LOG=runs/hpc_extract/logs/f2w_academy.log
  F2W_BACKEND=cborg
  F2W_MODEL=lbl/cborg-chat
  F2W_MAX_WORKERS=4
  PYTHON_BIN=python3
  VENV_DIR=.venv
  SSH_OPTS="-o PubkeyAuthentication=no"

Usage:
  scripts/deploy_nersc.sh --sync-code
  scripts/deploy_nersc.sh --sync-code --setup
  scripts/deploy_nersc.sh --sync-code --sync-pdfs /local/pdfs --restart-endpoint
  scripts/deploy_nersc.sh --submit
  scripts/deploy_nersc.sh --all /local/pdfs

Notes:
  - Does not copy .env or secrets.
  - Uses rsync, so uncommitted local agent edits deploy too.
  - Submit runs locally through app.modules.launchers.academy_extractor.
EOF
}

need_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing env: $name" >&2
    exit 2
  fi
}

repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

SYNC_CODE=0
SETUP=0
RESTART_ENDPOINT=0
SUBMIT=0
PDF_SRC=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sync-code)
      SYNC_CODE=1
      shift
      ;;
    --setup)
      SETUP=1
      shift
      ;;
    --restart-endpoint)
      RESTART_ENDPOINT=1
      shift
      ;;
    --sync-pdfs)
      PDF_SRC="${2:-}"
      if [[ -z "$PDF_SRC" ]]; then
        echo "--sync-pdfs needs local PDF directory" >&2
        exit 2
      fi
      shift 2
      ;;
    --submit)
      SUBMIT=1
      shift
      ;;
    --all)
      SYNC_CODE=1
      SETUP=1
      RESTART_ENDPOINT=1
      SUBMIT=1
      PDF_SRC="${2:-}"
      if [[ -z "$PDF_SRC" ]]; then
        echo "--all needs local PDF directory" >&2
        exit 2
      fi
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

need_env NERSC_USER
need_env NERSC_REPO

NERSC_HOST="${NERSC_HOST:-perlmutter.nersc.gov}"
NERSC_ENDPOINT="${NERSC_ENDPOINT:-f2w-extractor}"
NERSC_PDFS="${NERSC_PDFS:-runs/hpc_extract/pdfs}"
NERSC_TERMS="${NERSC_TERMS:-runs/hpc_extract/terms.json}"
NERSC_LOG="${NERSC_LOG:-runs/hpc_extract/logs/f2w_academy.log}"
F2W_BACKEND="${F2W_BACKEND:-cborg}"
F2W_MODEL="${F2W_MODEL:-lbl/cborg-chat}"
F2W_MAX_WORKERS="${F2W_MAX_WORKERS:-4}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

LOCAL_REPO="$(repo_root)"
REMOTE="${NERSC_USER}@${NERSC_HOST}"
SSH_OPTS="${SSH_OPTS:-}"
RSYNC_RSH="ssh"
if [[ -n "$SSH_OPTS" ]]; then
  RSYNC_RSH="ssh $SSH_OPTS"
fi
REMOTE_PDFS="${NERSC_REPO}/${NERSC_PDFS}"
REMOTE_TERMS="${NERSC_REPO}/${NERSC_TERMS}"
REMOTE_LOG="${NERSC_REPO}/${NERSC_LOG}"
REMOTE_SCHEMA="${NERSC_REPO}/storage/schema/matkg_schema.yaml"

if [[ "$SYNC_CODE" -eq 1 ]]; then
  echo "Sync code -> ${REMOTE}:${NERSC_REPO}"
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "$REMOTE" "mkdir -p '$NERSC_REPO'"
  rsync -az \
    --rsh="$RSYNC_RSH" \
    --exclude '.git/' \
    --exclude '.agents/' \
    --exclude '.codex/' \
    --exclude 'skills-lock.json' \
    --include '.env.example' \
    --exclude '.env' \
    --exclude '.env.*' \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude '.mypy_cache/' \
    --exclude 'runs/' \
    --exclude '.cache/' \
    --exclude 'storage/knowledge_gaps/' \
    "$LOCAL_REPO"/ "$REMOTE":"$NERSC_REPO"/
fi

if [[ -n "$PDF_SRC" ]]; then
  if [[ ! -d "$PDF_SRC" ]]; then
    echo "PDF directory not found: $PDF_SRC" >&2
    exit 2
  fi
  echo "Sync PDFs -> ${REMOTE}:${REMOTE_PDFS}"
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "$REMOTE" "mkdir -p '$REMOTE_PDFS'"
  rsync -az --rsh="$RSYNC_RSH" "$PDF_SRC"/ "$REMOTE":"$REMOTE_PDFS"/
fi

if [[ "$SETUP" -eq 1 ]]; then
  echo "Install/update remote Python env"
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "$REMOTE" "bash -lc 'cd \"$NERSC_REPO\" && PYTHON_BIN=\"$PYTHON_BIN\" VENV_DIR=\"$VENV_DIR\" bash scripts/nersc_remote_setup.sh \"$NERSC_REPO\"'"
fi

if [[ "$RESTART_ENDPOINT" -eq 1 ]]; then
  echo "Restart Globus Compute endpoint: $NERSC_ENDPOINT"
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "$REMOTE" "bash -lc 'cd \"$NERSC_REPO\" && source .venv/bin/activate && globus-compute-endpoint restart \"$NERSC_ENDPOINT\"'"
fi

if [[ "$SUBMIT" -eq 1 ]]; then
  if [[ ! -f "$LOCAL_REPO/user_agent_handle.pkl" ]]; then
    echo "Missing user_agent_handle.pkl. Start dashboard first: python3 -m app.modules.launchers.user_agent --port 8000" >&2
    exit 2
  fi

  echo "Submit remote extraction"
  cd "$LOCAL_REPO"
  python3 -m app.modules.launchers.academy_extractor \
    --data-dir "$REMOTE_PDFS" \
    --output "$REMOTE_TERMS" \
    --backend "$F2W_BACKEND" \
    --model "$F2W_MODEL" \
    --schema-path "$REMOTE_SCHEMA" \
    --max-workers "$F2W_MAX_WORKERS" \
    --log-file "$REMOTE_LOG"
fi

echo "Done."

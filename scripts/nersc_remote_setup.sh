#!/usr/bin/env bash
set -euo pipefail

# Run on NERSC from repo root. Creates/updates the Python environment used by
# the Globus Compute endpoint worker.

REPO_DIR="${1:-$(pwd)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

cd "$REPO_DIR"

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(
        "Python 3.10+ required. Set PYTHON_BIN to a newer interpreter, e.g. "
        "`PYTHON_BIN=python3.11 bash scripts/nersc_remote_setup.sh`."
    )
PY

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(
        "Existing venv uses old Python. Remove .venv or set VENV_DIR to a new "
        "path after loading Python 3.10+."
    )
PY
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

mkdir -p runs/hpc_extract/pdfs runs/hpc_extract/logs

echo "NERSC repo ready: $REPO_DIR"
echo "Activate with: source $REPO_DIR/$VENV_DIR/bin/activate"

#!/usr/bin/env bash
# Run the jobs CI runs on a push to forge_merge, locally, against the COMMITTED tree (git archive HEAD).
#
# Usage: scripts/ci/push_gate.sh [--ui] [--docs] [--only contracts|quality|shards|lint]
# Environment: F2W_GATE_PYTHON_BIN  directory holding python, ruff, isort, mypy, flake8 (default: $PWD/.pixi/envs/dev/bin,
#                                   else the directory of `python3` on PATH); F2W_GATE_KEEP=1 keeps the export.
# Runs, in CI's order: tests/contracts, quality gate (ruff, isort, mypy, docstring style), flake8 baselines,
# the three unit shards with the known-failure comparison, and with --ui the Vitest suite + vite build,
# with --docs the strict MkDocs build. Uncommitted changes are NOT tested: commit first.
# Not covered (CI-only): the frozen-UI hash guard, changed-path filtering, import of the legacy entrypoints,
# the [ci full] jobs (Tiled suites, Playwright, Docling, coverage, slow), Linux-vs-macOS differences.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
BIN="${F2W_GATE_PYTHON_BIN:-}"
[ -n "$BIN" ] || { [ -x "$ROOT/.pixi/envs/dev/bin/python" ] && BIN="$ROOT/.pixi/envs/dev/bin" || BIN="$(dirname "$(command -v python3)")"; }
RUN_UI=0; RUN_DOCS=0; ONLY=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --ui) RUN_UI=1; shift ;; --docs) RUN_DOCS=1; shift ;;
    --only) ONLY="${2:?--only needs a name}"; shift 2 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
git -C "$ROOT" diff --quiet HEAD -- || echo "warning: uncommitted changes are not part of this run" >&2
X="$(mktemp -d "${TMPDIR:-/tmp}/f2w-push-gate.XXXXXX")"
[ "${F2W_GATE_KEEP:-0}" = 1 ] || trap 'rm -rf "$X"' EXIT
git -C "$ROOT" archive HEAD | tar -x -C "$X"
cd "$X"
want() { [ -z "$ONLY" ] || [ "$ONLY" = "$1" ]; }
step() { printf '\n=== %s ===\n' "$1"; }
cienv() { env -i PATH="$BIN:/usr/bin:/bin" HOME="$HOME" LANG=C.UTF-8 ${F2W_TILED_PYTHON:+F2W_TILED_PYTHON="$F2W_TILED_PYTHON"} HF_HUB_OFFLINE=1 "$@"; }
fail=0
failed=()
bad() { failed+=("$1"); fail=1; }
if want contracts; then
  step "Python contract tests"
  cienv python -m pytest tests/contracts -n auto -m "not slow" -q -p no:cacheprovider || bad "contracts"
fi
if want quality; then
  step "Code quality (ruff, isort, mypy, docstring style)"
  cienv ruff check app || bad "ruff"
  cienv isort --check-only app || bad "isort"
  # A venv without the OTLP exporter package reports exactly these two import-not-found errors; CI (pixi dev) has it.
  mypy_out="$(cienv mypy 2>&1)" && mypy_rc=0 || mypy_rc=$?
  printf '%s\n' "$mypy_out" | tail -5
  if [ "$mypy_rc" != 0 ]; then
    others="$(printf '%s\n' "$mypy_out" | grep ': error:' | grep -v 'opentelemetry.exporter.otlp.proto.http' || true)"
    [ -z "$others" ] || bad "mypy"
    [ -n "$others" ] || echo "mypy: only the two known local OTLP-exporter import errors (not a failure here, CI has the package)"
  fi
  cienv python scripts/ci/docstring_style.py --check > docstyle.txt || bad "docstring_style"
  tail -1 docstyle.txt
fi
if want lint; then
  step "flake8 baselines (syntax / undefined names, pyflakes on app/)"
  cienv flake8 . --select=E9,F63,F7,F82 --format='%(path)s %(code)s %(text)s' | sort > lint.txt || true
  diff -u docs/migration/reviews/H0/known_lint.txt lint.txt || bad "flake8 baseline"
  cienv flake8 app --exclude=app/modules/legacy,app/graphify-out,__pycache__ --select=F \
    --format='%(path)s %(code)s %(text)s' | sed -E 's/ from line [0-9]+//' | sort > pyflakes.txt || true
  diff -u docs/migration/reviews/H7/known_pyflakes.txt pyflakes.txt || bad "pyflakes baseline"
fi
if want shards; then
  for n in 1 2 3; do
    step "Unit tests, shard $n of 3"
    cienv F2W_SHARD="$n/3" python -m pytest -p scripts.ci.pytest_shard -n auto -m "not slow" -q -p no:cacheprovider --junitxml="shard$n.xml" || true
    test -s "shard$n.xml" || { echo "no JUnit file for shard $n" >&2; bad "shard $n (no JUnit)"; continue; }
    cienv python scripts/ci/compare_known_failures.py "shard$n.xml" docs/migration/reviews/H0/known_failures.json || bad "shard $n"
  done
fi
if [ "$RUN_UI" = 1 ]; then
  step "Frontend: Vitest and vite build (both base paths)"
  (cd ui && npm ci && npm test && npx vite build --base / && VITE_F2W_AGENT_API_URL=/fair2wise/api npx vite build --base /fair2wise/) || bad "frontend"
fi
if [ "$RUN_DOCS" = 1 ]; then
  step "MkDocs strict build"
  cienv mkdocs build --strict -f mkdocs/mkdocs.yml || bad "mkdocs"
fi
[ "$fail" = 0 ] && echo "push gate: all selected jobs green" || { echo "push gate: RED: ${failed[*]}" >&2; exit 1; }

# CI: what runs on a push, and what runs elsewhere

Commits land directly on `forge_merge`, so a red run there is the signal. The push path is
therefore kept to lint, type checks and unit tests, and aims at **under 5 minutes** wall
clock. Everything slow runs nightly, on dispatch, or on request.

## On every push (and pull request) to `forge_merge`

`.github/workflows/forge-merge-ci.yml`, workflow `forge_merge validation`. A newer push to the
same ref cancels the older run (`concurrency: ${{ github.workflow }}-${{ github.ref }}`).

| Job | What it checks | Measured cost |
|---|---|---|
| `guard` | no `.env`, frozen UI tree (`FROZEN_UI_TREE`), which job groups the change needs | about 15 s |
| `python-quality` | ruff, isort, `mypy --strict`, docstring-style gate | about 1 min (mypy 35 to 45 s) |
| `import-closure` | flake8 syntax/undefined names and pyflakes against the recorded sets, legacy entrypoints | about 45 s |
| `python-contracts` | `tests/contracts` (also inside the shards) | about 45 s |
| `python-baseline-suite` x3 | the not-slow unit suite, three shards, known-failure gate | 110 to 150 s per shard on 4 workers |
| `frontend` x2 | Vitest and `vite build` for both base paths | about 90 s |
| `docs-build` | `mkdocs build --strict` | about 15 s |

The sharding is `scripts/ci/pytest_shard.py` (`F2W_SHARD=i/3`, `crc32(test id) % 3`): every test runs
in exactly one shard. The whole not-slow suite is about 340 s on 4 workers in one job, which is why it is
split.

Path filters (`scripts/ci/changed_paths.py`, output of `guard`): a change that only touches Markdown or
`mkdocs/` skips the Python and frontend jobs; a change that only touches `ui/` skips the Python jobs; a Python-only
change skips the frontend jobs. An unknown base commit, dispatch and schedule run everything. The
frozen-UI guard always runs.

## Not on a push

| Moved out of the push path | Runs now | Local command |
|---|---|---|
| Tiled-backed suites (`python-tiled-suites`, about 260 tests, 7.5 min) | nightly, dispatch, commit message containing `[ci full]` | `pixi run -e dev test` with `F2W_TILED_PYTHON` set, see [Testing](testing.md) |
| Playwright mocked-backend suite (`playwright-mocked`) | same | `cd ui && npx playwright test` |
| Coverage (was serial inside `python-quality`, 28 min) | `python-nightly-full`, same triggers | `pytest -n auto --cov` |
| Docling model download and the tests that need the weights (about 7 tests) | `python-nightly-full` | `python scripts/fetch_docling_models.py --dest <dir>`, then `F2W_DOCLING_ARTIFACTS_PATH=<dir> pytest` |
| Full-size scale tests (`python-slow-suite`, unchanged) | nightly, dispatch | `pixi run -e dev test-slow` |

!!! warning "The nightly cron only fires from the default branch"

    GitHub runs `schedule` triggers from the workflow file on the default branch (`main`). `forge-merge-ci.yml`
    is not on `main`, so the cron does not fire until it is merged there. Until then use `workflow_dispatch`
    or put `[ci full]` in a commit message.

Pixi installs measured 8 to 15 s per job, so no pixi environment cache is used (restoring a multi-GB
cache would cost more than the install). npm is cached by `actions/setup-node`.

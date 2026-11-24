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
| Image builds, all three variants, amd64 and arm64 (`build-app.yml`: `build-candidates`, about 8 min, plus `image-static-tests`) on every `forge_merge` push and PR | `workflow_dispatch` of `build-app.yml` with ref `forge_merge`; push to `main` and `v*` tags (inside `publish-image.yml`, unchanged) | local build per `docker/profiles/README.md` |
| `image-static-tests` (`tests/images`, `tests/ci`) on `forge_merge` | inside the sharded unit suite on every push | `pytest tests/images tests/ci` |
| Full-size scale tests (`python-slow-suite`, unchanged) | nightly, dispatch | `pixi run -e dev test-slow` |

!!! warning "The nightly cron only fires from the default branch"

    GitHub runs `schedule` triggers from the workflow file on the default branch (`main`). `forge-merge-ci.yml`
    is not on `main`, so the cron does not fire until it is merged there. Until then use `workflow_dispatch`
    or put `[ci full]` in a commit message.

Pixi installs measured 8 to 15 s per job, so no pixi environment cache is used (restoring a multi-GB
cache would cost more than the install). npm is cached by `actions/setup-node`.

`publish-image.yml` is unchanged: its gate (`O06_APPROVED`, protected ref, the `ghcr-publish` and
`ghcr-als-prod` environments) still decides every publication.

## Before you push

`scripts/ci/push_gate.sh` runs the push-path jobs against the committed tree (`git archive HEAD`, a clean
environment, like CI): `tests/contracts`, the quality gate (ruff, isort, mypy, docstring style), the flake8
baselines and the three unit shards with the known-failure comparison. `--ui` adds the Vitest suite and both vite
builds, `--docs` the strict MkDocs build, `--only contracts|quality|lint|shards` runs one group. Uncommitted
changes are not tested.

Checklist for anyone (person or agent) pushing a change under `app/`, `tests/`, `ui/` or `scripts/`:

1. Commit, then run `scripts/ci/push_gate.sh` (at least `--only contracts` and `--only quality`; the full run
   before the last push of a series). A file under `ui/` or `tests/` that only mentions "Splash" in a comment or a
   fixture still has to be listed in `docs/migration/reviews/H8/splash_retirement.md`
   (`tests/contracts/legacy_ui/test_splash_retirement.py`); a UI change also needs `--ui`.
2. A change under `ui/` re-pins `FROZEN_UI_TREE` (workflow) and the short pin in `docs/migration/demo/GITHUB_READINESS.md`.
3. Playwright is not on the push path: run the specs you touched with `cd ui && npx playwright test <files>`.

What the script does not cover: the frozen-UI hash guard and changed-path filter, the `[ci full]` jobs (Tiled
suites, Playwright, Docling, coverage, slow tests), the import check of the legacy entrypoints, and any
macOS-versus-Linux difference. Tiled-backed tests skip locally unless `F2W_TILED_PYTHON` is set.

## Known flaky tests

| Test | Symptom | Seen on | Status |
|---|---|---|---|
| Vitest `GraphMockupLarge.test.tsx`, "Show more steps 300 → 1,000 → 2,000" | timeout | red once on commit `5407976` (the `fair2wise`-prefix leg); green on later UI runs | open; cause believed to be load-dependent timing; no fix applied |

Rerun a red leg once before treating it as a regression, and add a row here for any other test that fails once
and passes on the same code.

## Residual risk

A push to `forge_merge` is not proven against a real Tiled server, a real browser, the Docling weights or a
Docker build until the nightly, a dispatch or a `[ci full]` commit runs them (the nightly cron does not fire
until the workflow is on `main`). The common regressions are caught on the push path: Python tests that do not
need a Tiled server (the Tiled-backed tests skip), lint and types, and Vitest. Docs-only commits skip the Python
tests, so a Markdown file that a test pins is only caught by the next code push. Dockerfile and `docker/` changes are
covered on the push path by the static tests only; dispatch `build-app.yml` before a release.

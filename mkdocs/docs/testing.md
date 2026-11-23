# Testing

The repository has three test surfaces: root Python tests, Splash Links tests,
and frontend Vitest tests.

## Running the tests

Tests run in parallel by default (pytest-xdist, one worker per core):

```bash
pixi run -e dev test         # pytest -n auto -m "not slow": the default, fast run
pixi run -e dev test-full    # pytest -n auto: also the full-size scale tests
pixi run -e dev test-slow    # only the tests marked slow
```

- `-n` is deliberately not in `pytest.ini`: debug one test serially with
  `python -m pytest path::test -p no:cacheprovider`, and run coverage without `-n`.
- Run pytest from a clean export of the tree, not from a checkout holding a `.env`
  (entry points load it at import); CI uses `env -i` with a private `HOME`. The recipe
  is [below](#clean-export-recipe-and-the-tiled-backed-tests).
- Scale tests (`tests/operations/test_scale_*.py`, `test_figure_evidence.py`,
  `test_repeated_works.py`, `test_no_code_generation.py`) run on populations one tenth of
  the full size by default; every bound (scan bound, inline result bound, prompt budget) is scaled
  by the same factor (`Profile.n` in `tests/operations/scale_population.py`). The full-size variants
  carry `@pytest.mark.slow` (parameter id `full`) and run in `test-full` and in the nightly CI job.
- Server-backed graph tests share one seeded disposable Tiled server per xdist worker
  (`SeededServerPool` in `tests/fixtures/tiled_server.py`), reset to the seed before each test; tests that
  stop, restart or kill a server, or need an auth/ACL variant, start their own.
- Tests must be xdist-safe: no fixed ports, no shared fixed file paths (use `tmp_path`); a subprocess
  with a private `HOME` should pass `MPLCONFIGDIR` through, or it rebuilds matplotlib's font cache.

## Fast, slow and nightly

| Run | What it runs | Where |
|---|---|---|
| fast (default) | everything except `@pytest.mark.slow`: `-m "not slow"` | `pixi run -e dev test`; the sharded `python-baseline-suite` CI job on every push, `python-tiled-suites` nightly (see [CI](ci.md)) |
| slow | only the full-size scale tests (`tests/operations`, parameter id `full`: 60k-entity populations, a 15k-entity disposable Tiled server) | `pixi run -e dev test-slow` |
| full | fast and slow | `pixi run -e dev test-full` |
| nightly | the slow tests with a Tiled server, at 06:17 UTC and on manual dispatch | job `python-slow-suite` of `.github/workflows/forge-merge-ci.yml` |

`-n auto` uses every core. On a shared machine pass a count instead, for example `-n 8`
(pytest-xdist). Tests must stay xdist-safe, so any `-n` value is valid.

## Clean export recipe and the tiled-backed tests

Run from a `git archive` export placed outside the repository, under `env -i`, with the
variables written **inline** on the command line:

```bash
EXPORT=$(mktemp -d)                      # not under the repository: no .env in any parent
git -C /path/to/repo archive HEAD | tar -x -C "$EXPORT" && mkdir "$EXPORT/home" && cd "$EXPORT"
env -i HOME="$EXPORT/home" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  F2W_TILED_PYTHON=/path/to/tiled-server/bin/python \
  /path/to/dev/bin/python -m pytest -n 8 -p no:cacheprovider -q -m "not slow" --ignore=tests/deepeval
```

With the pixi environments the two interpreters are
`.pixi/envs/dev/bin/python` and `.pixi/envs/tiled-server/bin/python`
(`pixi install --locked -e dev` and `-e tiled-server`). Use the interpreter
as `python -m pytest`, never a `bin/pytest` script.

About 260 to 270 tests (the CI comment says ~260; the owner counted ~270) use the
disposable Tiled server of `tests/fixtures/tiled_server.py`. Without `F2W_TILED_PYTHON` they
**skip**, each with the reason "F2W_TILED_PYTHON is not set"; with it set, a server that
fails to start fails the test. The variable names an isolated `tiled[server]==0.2.18`
interpreter, never the app environment.

!!! warning "zsh does not word-split variables"

    Do not put the assignments in a shell variable (`ENVV="F2W_TILED_PYTHON=... python"`
    then `env -i $ENVV -m pytest`). zsh passes that variable as one word, so
    `F2W_TILED_PYTHON` is silently dropped (or the command fails) while bash would
    have split it. The symptom is a green run with about 270 skips. Check the summary
    line: if the Tiled tests were meant to run, the skipped count must be small.

## Documentation and CI-script checks

```bash
env -i HOME="$EXPORT/home" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  F2W_TILED_PYTHON=/path/to/tiled-server/bin/python \
  /path/to/dev/bin/python -m pytest -n 4 -p no:cacheprovider -q tests/docs tests/ci
/path/to/dev/bin/python -m evals.binary.import_companion --check
mkdocs build -f mkdocs/mkdocs.yml --strict
```

`tests/docs` pins the user-facing docs to the Dockerfile and the image README (the
gateway port, the three build commands, no page calling Splash the default source) and to
`docs/migration/task_status.json`; `tests/ci` checks the workflows, the release scripts and
the docstring style. `import_companion --check` exits 1 when the generated binary-eval case files are out of
date; the case manifest hashes the oracle source files, so editing a hashed file makes it stale.
Regenerate with `python -m evals.binary.import_companion` from the clean export and copy the
changed JSON back.

## Root Python suite

```bash
pixi run -e dev test
```

Install the locked development environment with
`pixi install --locked -e dev`; `pixi run -e dev python -m pytest ...` runs
any subset. Other tasks: `lint`, `typecheck`, `ruff`, `isort-check`,
`coverage`, `docs`.
Runtime containers install the pixi `default` environment from `pixi.lock`
with `--locked`. After editing a dependency in `pixi.toml`, run `pixi lock`
(pixi 0.84.2) and commit `pixi.lock`; `tests/packaging/test_pixi_lock.py`
checks the lock.

`pytest.ini` sets the repository on `pythonpath` and limits default discovery to
`tests/`, so the vendored Splash suite does not run implicitly.

Major coverage areas:

| Area | Representative test files |
|---|---|
| Agent API and workflow | `test_f2w_api.py`, `test_workflow_orchestrator.py`, `test_f2w_coordinator.py` |
| Download/extraction | `test_download_agent.py`, `test_extractor_agent.py`, `test_extract_terms*.py`, `test_harvest_rsoxs.py`, `test_harvest_xray.py`, `test_harvest_tomo.py`, `test_paper_finder.py` |
| KG conversion/retrieval | `test_json2kg*.py`, `test_bl1101_ingest.py`, `test_kg_rag_api*.py`, `test_build_context_filtering.py` |
| Splash integration | `test_splash_helpers.py`, `test_splash_fallback.py`, `test_kg_update.py` |
| Settings/sessions | `test_agent_settings.py`, session cases in `test_f2w_api.py` |
| Utilities/safety | `test_project_config.py`, `test_cborg_limiter.py` |
| Compatibility API | `test_fastapi_endpoints.py`, `test_chat_clients.py` |

Tests use temporary files and monkeypatched HTTP/LLM clients to keep the
deterministic suite offline.

## LLM quality tests

`tests/deepeval/test_llm_quality.py` contains higher-cost evaluation cases for:

- answer relevancy;
- faithfulness;
- retrieval context relevancy;
- hallucination;
- RAG versus baseline;
- citations;
- multi-turn behavior;
- context budgets; and
- sparse graphs.

The module collects with the rest of `tests/`. `deepeval` is a development
dependency (the pixi `dev` environment). Deterministic prompt/decompose/budget cases
always run. LLM-graded cases skip when `CBORG_API_KEY` is unset.

Run the full eval (including the judge) with credentials:

```bash
python3 -m pytest tests/deepeval
```

Do not interpret a deterministic unit-test pass as an LLM quality result.

## Splash Links suite

```bash
cd splash_links
pixi run test
```

It tests:

- SQL store CRUD and cascade behavior;
- GraphQL queries/mutations;
- embedding REST and nearest-neighbor search;
- local and remote CLIs;
- typed client serialization/error handling; and
- Tiled integration helpers.

The Pixi task enforces its configured coverage threshold.

## Frontend suite

```bash
cd ui
npm test
```

Frontend tests cover:

- graph subset selection and relationship helpers;
- session persistence/migration/search;
- settings normalization and API conversion;
- API error messages;
- citation parsing;
- node-category colors;
- publication links; and
- live API type/helper behavior.

Production compilation is a separate check:

```bash
npm run build
```

## Documentation checks

```bash
mkdocs build -f mkdocs/mkdocs.yml --strict
```

Strict mode catches missing navigation pages, unresolved internal links,
duplicate anchors, and plugin warnings.

## Docker Compose smoke test

With Docker running and `CBORG_API_KEY` exported:

```bash
./scripts/test_compose.sh
```

The script uses an isolated Compose project and temporary named volumes. It
builds and starts the stack, checks the UI and proxied API, confirms the seed
graph is populated, rejects published agent/Splash ports, recreates the stack
to verify persistence, and removes only its own test state.

For fresh-computer acceptance, install Docker and export the key on the clean
machine, then run only clone, `cd`, and `docker compose up`. Confirm a real chat
request succeeds and ports 8081/8090 are not reachable from the host.

## Recommended pre-change matrix

| Change | Minimum checks |
|---|---|
| Agent workflow/API | Targeted root tests, then full root suite |
| Extraction/schema/KG | Extraction + JSON2KG + retrieval tests |
| Splash store/schema/client | Splash suite and root Splash integration tests |
| UI behavior | `npm test` and `npm run build` |
| Shell scripts | `bash -n scripts/name.sh` and related pytest cases |
| Documentation | Strict MkDocs build |
| Compose/container changes | `./scripts/test_compose.sh` and strict Compose configuration validation |

External downloads, CBORG, OpenAlex, GitHub, NERSC, Docker registries, and
Globus should remain mocked in deterministic CI unless a job is explicitly an
integration test.

See the [Contributor guide](contributing.md) for the complete branch, lock, and
change-type workflow, and [Release and maintenance](release.md) for the full
release gate.

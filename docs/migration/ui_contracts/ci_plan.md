# forge_merge CI plan (T18A, proposed)

- **Status:** Proposed for H1. No file under `.github/` was created or changed.
- **Scope:** validation only. No image publication, deployment, docs publishing or README mutation. Image jobs follow the separately approved T24 / O06 policy (CI_CD_CONTRACT.md).
- **Baseline:** `222b1e7b130567dbf4a3242697ac2b465794a38d`; frozen UI tree `75a19b9823bc924a6391311191d3e41a5b4917ff`.

## 1. What exists today

**[Observed]** in the `forge_merge` working tree:

| Workflow | Triggers | What it does | Problem for forge_merge |
|---|---|---|---|
| `.github/workflows/build-app.yml` | `push` and `pull_request` on `main` only (`:3-7`) | Python 3.12, `pip install -r requirements/dev.txt`, `pip check` (`:15-24`); `black --check` on 3 paths (`:25-30`); flake8 hard-fail subset `E9,F63,F7,F82` (`:31-36`); `pytest` over the whole tree (`:37-38`); `mkdocs build --strict` (`:39-40`); **main push only:** `mkdocs gh-deploy --force` (`:41-43`) and `scripts/update_readme_tree.py` (`:44-46`) | Never runs for forge_merge pushes or PRs targeting forge_merge. Plain `pytest` would fail on the H0 baseline errors (15 `test_bl1101_ingest` errors from untracked data, 2 CBORG-credential tests, 1 pixi-order test; `docs/migration/reviews/H0/baseline_tests.md`). No frontend job at all. |
| `.github/workflows/publish-image.yml` | `push` to `main`, tags `v*` (`:3-6`) | Builds `Dockerfile` target `agent` and pushes to GHCR with `packages: write` (`:13-46`) | Out of scope. Must not be triggered from forge_merge until T24/O06 approves. |

Nothing in either workflow runs Vitest, `vite build`, or Playwright, although the frozen UI has 10 Vitest files and 9 Playwright specs (FRONTEND_COMPATIBILITY.md section 8).

## 2. Proposed workflow

A new, separate file. It does not modify `build-app.yml` or `publish-image.yml`, so their main-only guards stay exactly as they are (plan section 5.3: "preserve existing main-only documentation/deployment guards").

Design rules:

- Triggers: `push` to `forge_merge`, `pull_request` with base `forge_merge`, and manual `workflow_dispatch`. Never `pull_request_target`.
- `permissions: contents: read` only. No secrets are referenced. No job has registry, Pages or write tokens.
- Every Python job runs under a scrubbed environment. A guard step fails the job if `.env` exists, because `kg_rag_api.py:81`, `extract_terms.py:37` and `f2w_agent/cli.py` call `load_dotenv()` at import. The T18A harness also refuses to run next to a `.env` (`tests/contracts/legacy_ui/_legacy_ui_harness.py`, `install`).
- Known H0 baseline failures are **tracked, not skipped**. The full suite runs and a comparison step fails only on new failures, or on known failures that changed. It never uses `--deselect`, `-k not`, or `continue-on-error` on the contract jobs.
- The frozen-UI guard fails a PR that changes `ui/` unless it carries the owner-approved label `ui-change-approved` (plan 5.2: an updated client cannot be used to hide a broken backend contract). The D26 base-path rebuild uses CLI flags only, with no source change.
- Real-API browser and disposable-Tiled jobs are placeholders, disabled until T18B/T20A deliver their fixtures.

```yaml
# PROPOSED: .github/workflows/forge-merge-ci.yml  (not created; for H1 review)
name: forge_merge validation

on:
  push:
    branches: [ forge_merge ]
  pull_request:
    branches: [ forge_merge ]
  workflow_dispatch: {}

permissions:
  contents: read

concurrency:
  group: forge-merge-ci-${{ github.ref }}
  cancel-in-progress: true

env:
  PYTHON_VERSION: "3.12"
  NODE_VERSION: "20"
  FROZEN_UI_TREE: 75a19b9823bc924a6391311191d3e41a5b4917ff
  # Offline, deterministic defaults (also forced by tests/conftest.py).
  KG_RAG_RETRIEVAL_BACKEND: lexical
  KG_RAG_FORCE_CPU: "1"
  F2W_SOURCE_RAG: "0"
  F2W_LIVE_TILED: "0"

jobs:
  guard:
    name: Isolation and frozen-UI guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4          # pin to a reviewed SHA before enabling
        with: { fetch-depth: 0 }
      - name: No .env in the checkout
        run: test ! -e .env
      - name: Frozen UI tree unchanged
        if: ${{ !contains(github.event.pull_request.labels.*.name, 'ui-change-approved') }}
        run: |
          actual="$(git rev-parse HEAD:ui)"
          echo "ui tree: $actual"
          test "$actual" = "$FROZEN_UI_TREE"

  python-contracts:
    name: Python contract and parity tests
    needs: guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ env.PYTHON_VERSION }}", cache: pip }
      - name: Install reviewed lock
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements/dev.txt
          pip check
      - name: Contract suites (T01 shared contracts, T18A legacy UI, JSON KG)
        run: |
          env -i PATH="$PATH" HOME="$RUNNER_TEMP/home" TMPDIR="$RUNNER_TEMP" LANG=C.UTF-8 \
            python -m pytest tests/contracts -q -p no:cacheprovider --junitxml=contracts.xml
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: contracts-junit, path: contracts.xml }

  python-baseline-suite:
    name: Existing pytest suite vs H0 known failures
    needs: guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ env.PYTHON_VERSION }}", cache: pip }
      - run: |
          python -m pip install --upgrade pip
          pip install -r requirements/dev.txt
      - name: Full suite (failures recorded, not hidden)
        run: |
          set +e
          env -i PATH="$PATH" HOME="$RUNNER_TEMP/home" TMPDIR="$RUNNER_TEMP" LANG=C.UTF-8 \
            python -m pytest -q -p no:cacheprovider --junitxml=full.xml
          echo "pytest_rc=$?" >> "$GITHUB_ENV"
      - name: Compare with H0 known-failure set
        # TODO(T18A follow-up): add scripts/ci/compare_known_failures.py that
        # reads docs/migration/reviews/H0 known failures (3 failed + 15 errors at
        # 222b1e7) and fails on any new failing/erroring test id or any change in
        # a known failure's message. Until it exists, this step fails closed.
        run: python scripts/ci/compare_known_failures.py full.xml docs/migration/reviews/H0/known_failures.json
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: full-junit, path: full.xml }

  import-closure:
    name: Import closure and legacy entrypoints
    needs: guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ env.PYTHON_VERSION }}", cache: pip }
      - run: pip install -r requirements/dev.txt
      - name: Syntax / undefined names (existing hard-fail subset)
        run: flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
      - name: Legacy entrypoints importable with unchanged signatures
        run: |
          env -i PATH="$PATH" HOME="$RUNNER_TEMP/home" python - <<'EOF'
          import inspect
          from app.modules.f2w_agent import api
          from app.modules import kg_rag_api
          import app.modules.launchers.f2w_agent  # noqa: F401  (launcher module)
          sig = str(inspect.signature(api.create_app))
          assert sig == "(cfg: 'CoordinatorConfig', *, cors_origins: 'Optional[List[str]]' = None) -> 'FastAPI'", sig
          assert "run_api" in dir(api)
          assert str(inspect.signature(kg_rag_api.create_fastapi_app)).startswith("(graph_file: 'str'")
          print("ok")
          EOF
      # Placeholder: the T01/T03 import-closure check (FORGE embedding, no
      # module-state collisions) is added when app/api/main.py exists.

  frontend:
    name: Frontend unit tests and build (frozen UI)
    needs: guard
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: ui } }
    strategy:
      matrix:
        include:
          - name: standalone-dev
            base: /
            api: ""                     # client default http://127.0.0.1:8090 (liveAgent.ts:221)
          - name: fair2wise-prefix     # D26: configuration-only rebuild
            base: /fair2wise/
            api: /fair2wise/api
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "${{ env.NODE_VERSION }}", cache: npm, cache-dependency-path: ui/package-lock.json }
      - run: npm ci
      - run: npm test                    # vitest run, 10 files / 88 tests at H0
      - name: vite build (${{ matrix.name }})
        env:
          VITE_F2W_AGENT_API_URL: ${{ matrix.api }}
        run: npx vite build --base "${{ matrix.base }}"
      - name: Record dist manifest hash
        run: find dist -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum

  playwright-mocked:
    name: Existing Playwright suite (mocked backend)
    needs: frontend
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: ui } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "${{ env.NODE_VERSION }}", cache: npm, cache-dependency-path: ui/package-lock.json }
      - run: npm ci
      # playwright.config.ts pins channel 'chrome'; GitHub ubuntu runners ship
      # Google Chrome. Installing the 'chrome' channel keeps the config unchanged.
      - run: npx playwright install --with-deps chrome
      - run: npx playwright test --reporter=line
        env: { CI: "1" }
      - uses: actions/upload-artifact@v4
        if: failure()
        with: { name: playwright-report, path: ui/playwright-report }

  real-api-browser:
    name: Frozen UI against real FastAPI with fake inference (T18B)
    needs: [python-contracts, frontend]
    if: ${{ false }}   # enable when T18B delivers the live-backend Playwright project
    runs-on: ubuntu-latest
    steps:
      - run: echo "placeholder: start create_app() with the T18A fake harness, serve ui/dist, run the live-backend Playwright project; no route mocking for endpoints under test"

  disposable-tiled:
    name: Disposable Tiled 0.2.19 publication/edit tests (T20A/T18C)
    needs: python-contracts
    if: ${{ false }}   # enable when tests/fixtures/tiled_server.py exists
    runs-on: ubuntu-latest
    steps:
      - run: echo "placeholder: provision an owned ephemeral Tiled on 127.0.0.1, run edit/readback/ownership tests, destroy only owned paths; never central URLs or keys"

  docs-build:
    name: MkDocs strict build (no publish)
    needs: guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ env.PYTHON_VERSION }}", cache: pip }
      - run: pip install -r requirements/dev.txt
      - run: mkdocs build --strict -f mkdocs/mkdocs.yml
      # Deliberately no `mkdocs gh-deploy` and no scripts/update_readme_tree.py:
      # those stay main-push-only in build-app.yml.
```

## 3. Required checks for branch protection on forge_merge (proposed)

Required: `guard`, `python-contracts`, `python-baseline-suite`, `import-closure`, `frontend (standalone-dev)`, `frontend (fair2wise-prefix)`, `playwright-mocked`. `docs-build` is advisory until the mkdocs tree is reviewed. `real-api-browser` becomes required at H2 (T18B). `disposable-tiled` becomes required before H6 (T18C).

## 4. Open items before enabling

1. Pin every action (`actions/checkout`, `setup-python`, `setup-node`, `upload-artifact`) to a reviewed commit SHA (CI_CD_CONTRACT.md: "do not invent digest or action SHA values in a draft").
2. Write `scripts/ci/compare_known_failures.py` and `docs/migration/reviews/H0/known_failures.json` (test ids from `pytest-222b1e7.xml`). Until then `python-baseline-suite` fails closed by design.
3. `requirements/dev.txt` alone was not the H0 environment. H0 also installed `requirements/semantic.in` (baseline_tests.md "Environment"). The contract suite needs only `dev.txt` with `KG_RAG_RETRIEVAL_BACKEND=lexical` **[Inferred: the venv used here has both; not re-verified with dev.txt alone]**. Confirm on the first CI run.
4. The OpenAPI snapshot depends on FastAPI 0.141.1 / Pydantic 2.13.4 / Starlette 1.3.1 (versions in the run that produced it). A lock bump that changes schema rendering will fail `test_ui_openapi_matches_baseline`. That is intended: regenerate only after reviewing the diff.
5. The `black --check` and non-blocking flake8 steps from `build-app.yml` can be added later. They are not compatibility gates.

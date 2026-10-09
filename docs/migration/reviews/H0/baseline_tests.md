# H0 baseline test characterization

Executed 2026-10-09. Results are recorded as found. Pre-existing failures were
not fixed.

## Isolation

- Each commit was exported with `git archive <sha>` into a temporary directory.
  The repo `.env` is gitignored, so it is absent from the export.
  `kg_rag_api.py`, `extract_terms.py` and `f2w_agent/cli.py` call
  `load_dotenv()` at import. Running in the working checkout would load the
  developer's live CBORG, MP, GitHub, Tiled and Globus credentials into tests.
- Tests ran under `env -i` with only `PATH`, `LANG`, a throwaway `HOME`/`TMPDIR`
  and, for Playwright, its browser path. No API keys, catalogs, model servers or
  Tiled URIs were configured.
- `tests/conftest.py` already forces lexical retrieval, JSON graph source,
  `F2W_SOURCE_RAG=0` and `F2W_LIVE_TILED=0`.

## Environment

| Item | Value |
|---|---|
| Host | macOS 26.6.2, arm64 |
| Python | 3.12.11, fresh `uv venv` (uv 0.6.10) |
| Python deps | `requirements/dev.txt` + `requirements/semantic.in`; 221 packages, `uv pip check` clean; full list in `environment/python_freeze.txt` |
| Node | 20.20.2 (`ui/package.json` requires >=20; the default system node 18.18.2 does not qualify) |
| UI deps | `npm ci` from `ui/package-lock.json`; vitest 3.2.7, vite 6.4.3, @playwright/test 1.63.0, Chromium headless shell 153 |

## Commands

```bash
# Python, per export
env -i PATH=<venv>/bin:/usr/bin:/bin HOME=<tmp> TMPDIR=<tmp> LANG=en_US.UTF-8 \
  <venv>/bin/python -m pytest -q -p no:cacheprovider --junitxml=<out>.xml

# UI, in <export>/ui
npm ci && npm test && npm run build
PLAYWRIGHT_BROWSERS_PATH=<tmp> CI=1 npx playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=<tmp> CI=1 npx playwright test --reporter=line
```

## Results

| Suite | `222b1e7` (frozen baseline) | `2430320` / HEAD |
|---|---|---|
| pytest | 692 passed, 20 skipped, 3 failed, 15 errors | 733 passed, 20 skipped, 4 xfailed, 3 failed, 15 errors |
| Vitest | 10 files, 88 passed | not rerun (no UI changes) |
| `vite build` | succeeds (chunk-size warning only) | not rerun |
| Playwright (mocked backend) | 23 passed | not rerun |

The failure and error sets are identical in both pytest runs.

## Pre-existing failures

| Tests | Cause | Classification |
|---|---|---|
| `test_bl1101_ingest.py`: 15 errors | Module fixture asserts `storage/kg/matkg_bl1101_v7.json` exists. The file is a local data artifact, not tracked in Git. | Missing untracked data. Relevant to D31/T26 (committed versioned KG JSON). |
| `test_strip_ansi_and_helpers.py::TestMakeChatClient::test_cborg_backend`, `test_cborg_openai_alias` | Constructing the CBORG client raises `openai.OpenAIError: Missing credentials`. The tests pass only when an ambient `CBORG_API_KEY` is set. | Test-isolation defect: unit tests depend on a real credential being present. |
| `test_kg_update.py::test_splash_reimport_refuses_wipe_without_explicit_allow` | Returns `pixi not found on PATH` before the `--allow-splash-wipe` check the test expects. | Precondition ordering: tool check runs before argument validation. |

## Skips (20)

| Count | Reason |
|---|---|
| 10 | No `matkg_bl1101_v*.json` with `prov_metadata` in `storage/kg/` (same missing data as above) |
| 9 | `CBORG_API_KEY` not set: LLM-graded deepeval tests skipped by design |
| 1 | Opt-in live Tiled probe (`TILED_LIVE_PROBE`) |

## Limits

- Playwright specs mock the backend over the network. They establish UI
  behavior only, not backend/browser integration (plan section 3.1).
- No LLM, provider, Tiled or QA-policy behavior was exercised. The
  `f2w_llm_qa_baseline.json` characterization with fake inference is still
  outstanding for H0.
- Not run: `splash_links/_tests/`, inline `json2kg.py` tests outside `tests/`,
  deepeval LLM-graded cases.

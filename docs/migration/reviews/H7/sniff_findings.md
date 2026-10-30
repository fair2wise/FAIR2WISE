# T27 sniff-test findings (H7 review packet)

Status: **survey only, no code changed.** Dispositions below are *proposed*; the owner
confirms each as `fix`, `defer` (with owner approval) or `intentional` before H7.

Scope: `app/` excluding `app/modules/legacy/`, `__pycache__`, `graphify-out`
(290 files, ~86k LOC, 3,601 functions, 653 classes). Standard applied: ALS Photon Science
Computing dev standards (https://github.com/als-computing/claude_skills).

Method and limits: AST scan plus grep. mypy, flake8, isort and coverage are not installed
in the survey environment, so type-check errors, flake8 F-class findings, import-order
violations and line coverage were **not measured**. All counts are from the scan and are
approximate (the "silent handler" count in particular includes legitimate fallbacks).

## F0. Tooling gaps that block the T27 acceptance criteria

| # | Finding | Evidence | Proposed disposition |
|---|---|---|---|
| F0.1 | No mypy configuration or dependency; `mypy --strict` gate is undefined. | No `[tool.mypy]` in `pyproject.toml`; only `mypy-extensions` in `requirements/dev.txt`. | fix: add config (per-package overrides for `langchain*`, `tiled`), add to dev extras and CI |
| F0.2 | Coverage is not configured; `docs/migration/reviews/H7/coverage.txt` cannot be produced. | No `pytest-cov`/`coverage` settings in `pyproject.toml`, `pytest.ini` or requirements. | fix: add `pytest-cov`, `.coveragerc` omitting `legacy/`, generated files, migration scripts |
| F0.3 | CI lint is effectively off. | `build-app.yml:80,82` selects only `E9,F63,F7,F82`; the full pass is `--exit-zero`; `forge-merge-ci.yml:160` `|| true`. | fix: gate on a real flake8 (or ruff) selection once F-class findings are cleared |
| F0.4 | flake8 line-length inconsistent. | `.flake8` max 115; CI passes 127. | fix: one value |
| F0.5 | No isort config or check. | No isort in pyproject/CI. | fix: add check-only job |

## F1. Typing (standard section 1)

| # | Finding | Evidence | Proposed disposition |
|---|---|---|---|
| F1.1 | 82 functions lack a return annotation. | Worst: `agents/properties.py` (11), `literature/download_pdfs.py` (8), `kg_rag_api.py` (8), `extract_terms.py` (7), `json2kg.py` (6), `f2w_agent/api.py` (5). | fix |
| F1.2 | 30 functions have unannotated parameters. | `term_extractor/agent.py:9 build_graph(llm=None, tools=None)`, `canonicalization/migration.py:321,326,331 apply_*`, `literature/download_pdfs.py:498,513 _oa_get/_oa_post`, `extract_terms.py:166,269,272`, `kg_rag_api.py:1888,1925`, `deployment/_tiled_entry.py` (4). | fix |
| F1.3 | Legacy `typing` generics. | `Optional` imported in ~205 files, `List` ~153, `Dict` ~186, `Tuple` ~122, `Union` ~14; ~1,950 `Optional`/`Union` annotations. Standard requires `list[...]`, `dict[...]`, `X | None`. | fix (mechanical: ruff `UP006/UP007/UP045`) |
| F1.4 | `Any` is pervasive and none carries the inline justification T27 requires. | ~1,880 signature annotations mention `Any`: `f2w_agent/api.py` (141), `security/disclosure.py` (83), `bl1101_ingest.py` (59), `public.py` (42), `f2w_agent/trace.py` (40), `operations/knowledge.py` (38). | fix where the shape is fixed (`TypedDict`/dataclass per `json_kg_contract.md`); otherwise add the one-line reason |
| F1.5 | Untyped defaults on the public surface. | `public.py:159 credential: Any = None`; `api/sessions.py:389 http_client: Any = None`; `api/operations.py:146 publication: Any = None`. | fix (define a `Protocol`) |
| F1.6 | Suppression markers without reasons. | 30 `# type: ignore`, 78 `# noqa` (`kg_rag_api.py` has 12 ignores). | fix or annotate each |

## F2. Docstrings (standard section 2)

| # | Finding | Evidence | Proposed disposition |
|---|---|---|---|
| F2.1 | 322 public functions lack docstrings. | `bl1101_ingest.py` (32), `agent_runtime/supervisor.py` (23), `f2w_agent/trace.py` (20), `term_extractor/dashboard.py` (15), `graph/imports/tiled_json.py` (11), `agents/properties.py` (10), `runtime_bridge/qa_route.py` (9), `literature/download_pdfs.py` (7), `hybrid_rag/pipeline.py` (7), `hybrid_rag/index_store.py` (7). | fix; prioritise anything in a public or cross-module import (T27 exemption for single-caller helpers) |
| F2.2 | 25 public classes lack docstrings. | `RepoSpec`, `BlueprintDevice`, `BlueprintStage` (`bl1101_ingest.py`); `GitHubRepoRef`, `SourceFile`, `SourceBlock` (`term_extractor/source_repos.py`); `AuditReport`; `Observation`, `WithheldPromotion` (`canonicalization/growth.py`); `CleanupItem`; `Orchestrator`; `ChatClient`, `OllamaChatClient`; `ToolState`; four in `canonicalization/campaign/models.py`. | fix |
| F2.3 | 15 modules lack a module docstring. | Mostly `term_extractor/` (`tools`, `store`, `services`, `schema`, `orchestrator`) and `vision/__init__.py`. | fix |
| F2.4 | Docstring style consistency (Google for new code, Sphinx for existing, never mixed in a module) not surveyed. | n/a | fix: check per module during the read-through |
| F2.5 | Docstring-versus-implementation divergence (T27 item 3) needs a human read; the scan cannot detect it. | n/a | open: read-through of the hot modules in F3 |

## F3. Sniff test: structure and dead code

| # | Finding | Evidence | Proposed disposition |
|---|---|---|---|
| F3.1 | 75 functions exceed 80 lines; 47 exceed 25 branch points. | `f2w_agent/api.py`: `create_app` (575), `_ask_locked` (467), `_retired_deterministic_locked` (292), `_apply_linked_snippet_updates` (202), `_resume_extraction` (199), `_extract_rebuild_reload` (195). Also `agent_runtime/supervisor.py` `build_literature_supervisor` (371), `extract_terms.py` `process_page` (239) and `_extract_pub_metadata` (201), `f2w_agent/retrieval_agent.py` `query` (232), `agents/properties.py` `normalize` (224), `term_extractor/tools.py` `build_tools` (200), `kg_rag_api.py` `create_fastapi_app` (200) and `build_context` (178). | defer with owner approval for behaviour-preserving splits; document as intentional only where a closure over shared state is required (`create_app`, `build_tools`) |
| F3.2 | Oversized modules. | `f2w_agent/api.py` 7,396 lines; `bl1101_ingest.py` 2,398; `extract_terms.py` 2,107; `kg_rag_api.py` 1,999. | defer |
| F3.3 | Possible dead code: a function whose name says it is retired. | `f2w_agent/api.py:4922 _retired_deterministic_locked` (292 lines). | confirm callers; delete or record as intentional |
| F3.4 | Tests inside production modules. | `json2kg.py:571 test_build_graph_minimal`, `:620 test_cli`. | fix: move to `tests/` |
| F3.5 | Commented-out code. | `extract_terms.py:683,693`; `kg_rag_api.py:51`. | fix |
| F3.6 | Open TODO. | `term_extractor/dashboard.py:1007` (JS, event-driven `showConnection()`). The `agent_runtime/supervisor.py:92` TODO(T03) from AUD-18 is gone; the comment now documents that the default prompt is off the langgraph path. | defer / intentional |
| F3.7 | AUD-18 pyflakes items to re-verify. | `extraction/schema_selection.py:18`, `literature/download_pdfs.py:895`, `operations/corpus.py:65`. | fix if still present |
| F3.8 | Magic literals not surveyed. | n/a | open: read-through |

## F4. Error handling and logging (standard sections 5 and 6)

| # | Finding | Evidence | Proposed disposition |
|---|---|---|---|
| F4.1 | 17 `except ...: pass` handlers. | `cborg_http.py:40`; `term_extractor/monitored_agent.py:94,155,209`; `term_extractor/dashboard.py:1135,1245`; `f2w_agent/trace.py:526,700`; `f2w_agent/workflow_state.py:102`; `graph/snapshot.py:263`; `literature/staging.py:296`; `literature/download_pdfs.py:817`; `bl1101_ingest.py:2150,2155`. | fix: log at least at debug; or mark intentional with a comment where best-effort cleanup is the contract |
| F4.2 | ~148 handlers whose only body is `continue`/`return` with no call. | Worst: `f2w_agent/api.py` (9), `literature/openalex.py` (6), `f2w_agent/paper_evidence_agent.py` (6), `deployment/tiled_lifecycle.py` (6), `jobs/process_support.py` (5). Includes legitimate fallbacks. | fix after triage |
| F4.3 | 242 `except Exception` blocks; 34 of the files containing them define no logger. | e.g. `curation/resolution.py`, `jobs/stages.py`, `provenance/recorder.py`, `operations/{papers,graph,corpus}.py`, `schema/loading.py`, `agent_runtime/supervisor.py`, `json2kg.py`, `runtime_bridge/qa_{tool,route}.py`. | fix: add module logger and log before swallowing or re-raising |
| F4.4 | 128 `print()` calls. | `f2w_agent/coordinator.py` (61), `agents/chebi.py` (17), `kg_rag_api.py` (11), `run_pipeline_cborg.py` (7), `extract_terms.py` (5), `bl1101_ingest.py` (5). | fix for diagnostics; intentional where the output is the CLI's result |
| F4.5 | f-strings in log calls. | 48, of which 45 in `extract_terms.py`. The ALS examples use f-strings, so this is a note, not a violation. | intentional |

## F5. Paths, CLI, imports, tests

| # | Finding | Evidence | Proposed disposition |
|---|---|---|---|
| F5.1 | `os.path` used. | 39 uses: `extract_terms.py` (12), `term_extractor/orchestrator.py` (5), `deployment/tiled_lifecycle.py` (5), `literature/download_pdfs.py` (4), `term_extractor/store.py` (3). | fix (ruff `PTH`) |
| F5.2 | argparse CLIs, no Typer, no `[project.scripts]`. | 13 modules: `api/cli.py`, `f2w_agent/cli.py`, `bl1101_ingest.py`, `kg_rag_api.py`, `extract_terms.py`, `json2kg.py`, `tiled_sim.py`, `deployment/{gateway,_tiled_entry}.py`, `launchers/*`, `workers/__main__.py`, `run_pipeline_cborg.py`, `literature/download_pdfs.py`. | defer: rewriting CLIs at cutover adds risk; record as intentional legacy |
| F5.3 | Import ordering not stdlib / third-party / local with alphabetical order. | `extract_terms.py` interleaves `from abc`, `argparse`, `datetime` and puts `requests` among stdlib. Not measured repo-wide. | fix once isort is configured |
| F5.4 | Test style. | No `unittest.TestCase` (good); ~11 files use `class Test...` pytest classes. | intentional |
| F5.5 | Coverage unmeasured. | See F0.2. | open |

## Not findings (checked and clean)

- No bare `except:`.
- No hardcoded user paths or credential literals found by grep.
- `.env.example` exists; `load_dotenv` is confined to `dotenv_entry.py` and `tiled_sim.py`.
- `py.typed` present. No Prefect usage in `app/`, so the Prefect sections do not apply.
- AUD-13 CBORG test-isolation fix landed (`known_failures.json`, T28B); only the Splash test remains (H8).

## Suggested order of work

1. F0: tooling and CI gates, so every other finding becomes a measured number.
2. Mechanical fixes: F1.1-F1.3, F4.1, F4.4, F5.1, F3.4, F3.5, F3.7.
3. Docstrings (F2.1-F2.3), public surface first: `public.py`, `api/`, `bl1101_ingest.py`, `f2w_agent/trace.py`, `agent_runtime/supervisor.py`, `graph/imports/tiled_json.py`.
4. `Any` removal (F1.4, F1.5) by `TypedDict`/`Protocol`.
5. Human read-through of the F3.1 hot spots (F2.5, F3.8) and owner decisions on the deferred items.

## Progress log

### 2026-10-30: tooling and first docstring batch (no runtime behaviour changed)

- F0.1, F0.2, F0.5 (config and CI wiring): `[tool.ruff]`, `[tool.isort]`, `[tool.mypy]` (strict) and
  `[tool.coverage]` added to `pyproject.toml`; `requirements/quality.in` and `quality.txt` pin the tools
  (compiled against `dev.txt`); `python-quality` job in `forge-merge-ci.yml` uploads a `quality-report`
  artifact (ruff statistics, isort, mypy, coverage). The job reports only and does not gate. F0.3 and F0.4
  (flake8 selection, line length) are still open.
- Local baseline before any fixes (quality venv, runtime extras not installed): ruff 6,909 findings
  (`UP006` 3,507, `UP045` 1,914, `UP035` 659, `T201` 128, `D102` 109, `D103` 90, `D101` 25); isort 162 files;
  mypy `--strict` 852 errors in 172 files (`no-any-return` 146, `type-arg` 121, `no-untyped-def` 86).
  Coverage: not yet measured (first CI run).
- F2.1, F2.2 partly closed: ruff `D` is clean (except `D105`, excluded by project policy for magic methods in
  `app/public.py` and `app/api/`) for `app/public.py`, `app/api/`, `bl1101_ingest.py`, `f2w_agent/trace.py`
  and `graph/imports/tiled_json.py` (92 findings fixed, docstrings only). `agent_runtime/supervisor.py` has no
  ruff `D` findings; the earlier AST count of 23 was nested closures.
- Items to confirm in review: `bl1101_ingest.extract_markdown_software(repo_name)` and
  `attach_repo_terms(root)` are documented as unused parameters (candidates for removal); `RepoSpec.mode` is
  only recorded, never branched on.

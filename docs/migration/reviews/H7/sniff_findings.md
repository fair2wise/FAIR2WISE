# T27 sniff-test findings (H7 review packet)

Status: **every row has a closing state; H7 review is the owner's call (not approved here).** The last three `fix`
rows (F2.4, F2.5, F3.8) were closed with evidence on 2026-11-06 (`readthrough_F25_F38.md`, last log entry); the
read-through recorded 13 suspected code bugs and 4 contract docstrings that need an owner decision (listed there).
F1.4, F1.6, F4.2 and the coverage figure (F0.2/F5.5) were closed on 2026-11-04. The owner accepted every proposed disposition
below as written on 2026-11-03 (`owner_decisions_2026-11-03.md`, eighth set), so `fix` rows are decided fixes,
`defer` rows are deferred with owner approval and `intentional` rows are accepted as they are.

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
| F2.4 | Docstring style consistency (Google for new code, Sphinx for existing, never mixed in a module) not surveyed. | n/a | fix: check per module during the read-through. Done 2026-11-06: `scripts/ci/docstring_style.py`, 0 mixed modules (353 scanned), gated by `tests/ci/test_docstring_style.py` and the CI quality gate |
| F2.5 | Docstring-versus-implementation divergence (T27 item 3) needs a human read; the scan cannot detect it. | n/a | open: read-through of the hot modules in F3. Done 2026-11-06: six F3.1 modules read, 53 docstrings corrected, 13 suspected code bugs recorded for the owner (`readthrough_F25_F38.md`) |

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
| F3.8 | Magic literals not surveyed. | n/a | open: read-through. Done 2026-11-06: 58 named constants hoisted (112 occurrences, values unchanged), 53 literals justified in place (`readthrough_F25_F38.md`) |

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

### 2026-10-30 (later): docstring sweep and flake8 gate

- F2.1-F2.3 closed for ruff `D` under the project config (`D105`/`D107` ignored): 0 findings in `app/`
  (about 350 further docstrings across 96 files; 109 `.py` files changed in total this session).
  Verified by AST comparison against `HEAD` with docstrings stripped: code is identical in every file except
  four previously empty `__init__.py` files that now hold a docstring. Edits were hand-written; the only
  scripted pass rewrote docstring text for D200/D209/D212 and re-checked AST equality.
- Review items from the sweep: `term_extractor/tools.py` `@tool` docstrings are sent to the LLM as tool
  descriptions (blank line and closing quotes moved, wording kept; `fuzzy_merge_term` summary shortened);
  `LLMTermExtractor.__init__` Args corrected (`ollama_model` -> `model_name`); `term_extractor/agent.py`
  `build_graph` documents `llm` as required in practice (signature default `None` unchanged); `test_*`
  functions inside `agents/properties.py` and `json2kg.py` got docstrings (F3.4 still open: they belong in
  `tests/`); four Protocol stubs now carry a docstring above `...`.
- Descriptions that go beyond the old text and need a reviewer read: `SchemaHelper.validate_and_fix_term`,
  `plan_reextraction`, `ResolverConfig.model`, `work_id_for_paper`, `check_merge`, `reserve`, `load_helper`,
  `run_validation_stage`, `from_deployment.graph_max_records`, `jobs/publish_ports.py` `from_dict`/`as_dict`,
  `curation/routing.py` `model_route`, `hybrid_rag/ranker.py` `rank`.
- F0.3/F0.4: `forge-merge-ci.yml` gains a pyflakes (all `F` codes) gate on `app/` against
  `docs/migration/reviews/H7/known_pyflakes.txt` (25 recorded violations; list may only shrink);
  `build-app.yml` no longer overrides line length (115 from `.flake8`). `E`/`W` style codes stay ungated.
- Not run: tests needing `linkml_runtime`, `langgraph`, `academy`, `aiohttp`, `fitz` do not collect in the
  survey environment; the first `python-quality` / baseline CI run is the real check.

### 2026-10-30 (evening): UP rewrite, isort and typing pass

- F1.3 closed (commit `9bf262c`): `ruff --fix` for UP006/UP045/UP037/UP017, the `typing`/`datetime.timezone`
  imports those rewrites left unused removed, then isort (ALS order). 249 files. Full suite from a clean export:
  4,780 passed / 1 failed (known Splash) before; 4,779 / 2 after, the new failure being
  `test_entry_script_is_stdlib_only_at_import`, which pinned the old import order of `_tiled_entry.py`
  (expected list updated, `98645ce`). isort also reorders function-local imports (not behaviour-relevant).
- F1.1, F1.2, F1.6 mostly closed (commit `d3b002f`): mypy `--strict` with runtime packages visible went from
  642 to 91 errors; no-untyped-def 87 -> 6 (FastAPI routes, left on purpose), type-arg 122 -> 0. Annotation,
  import, `typing.cast` and helper-type changes only (AST check against HEAD; expected differences: a
  `Protocol` in `campaign/rounds.py`, `TypeVar`s in `extract_terms.py` and `provenance/recorder.py`, a PEP 695
  generic in `graph/identity_adapter.py` (needs Python >= 3.12, already required), an `OpenAlexNoData(dict)`
  base). 26 `# type: ignore[...]` in `app/`, each with a code; 9 new ones carry a reason.
- Reviewer decisions:
  - About 19 `cast(<ResponseModel>, guard.project_*(...))` in `f2w_agent/api.py` describe a dict as the
    declared FastAPI response model (FastAPI still validates it). Cleaner: type `legacy_ui.project_*` to return
    the model types.
  - `api/routes_common.py:71` `project(...) -> Any` causes 22 `no-any-return` errors in the route modules;
    narrowing it needs a check that no caller passes a non-dict payload.
  - Many `cast(T, optional_value)` assert invariants the code guarantees elsewhere (listed per file in the
    commit's diff); a runtime guard would be the strict alternative.
- Errors left (91): `no-any-return` 22 (mostly the `project()` case), `import-untyped` 14 (stubs added to
  `requirements/quality.in`: types-jsonschema, types-psutil, lxml-stubs; pynvml and rdkit have none),
  `assignment` 14, `arg-type` 9, `union-attr` 7, `no-untyped-def` 6, `index` 6, `import-not-found` 6.
  About 35 need a logic change (Optional narrowing, variable names reused with a different type), e.g.
  `jobs/publication.py` (`current_plan()` is `PlanRecord | None`), `graph/backends/tiled.py:215-221`,
  `graph/imports/tiled_json.py:685,690`, `agent_runtime/skills.py:182,184`, `api/sessions.py:280,403,404`,
  `operations/corpus.py:336-343`, `graph/snapshot.py:237`, `security/authorization.py:808`, and the reused
  names in `f2w_agent/api.py` (1671, 1691, 3869-4026, 7232, 7338, 6902).
- Pyflakes baseline now 18 entries. `f2w_agent/api.py:4987` `history` (F821) is still open: `history` is not
  defined in that scope; the `try/except TypeError` around `retrieval.query(question, history=history)` would
  not catch the resulting `NameError`. Owner decision needed.

### 2026-11-02: UP035, UP007, UP042, T201 and PTH closed in `app/` (ruff reports 0)

- `UP035` (179): `Sequence`, `Mapping`, `Callable`, `Iterable`, `Iterator` now come from `collections.abc`
  (identical on Python 3.12); `typing.ContextManager`/`AsyncContextManager` became
  `contextlib.AbstractContextManager`/`AbstractAsyncContextManager`. `UP007` (33): `Union[...]` rewritten as `X | Y`.
- `UP042` (19 classes in 12 files): `(str, Enum)` became `StrEnum`. This changes `str(member)`/`format(member)`
  from `Class.MEMBER` to the bare value. An instrumented full-suite run (4,888 tests) recorded zero such calls on
  any of these classes, and JSON encoding is unchanged; code paths the suite does not reach are unchecked.
- `T201` (128): command output now goes through `app/modules/cli_output.py`, the `f2w.output` logger
  (info to stdout, warning and above to stderr, bare message, installed on import, does not propagate), so the
  text and streams are byte-identical and callers that never configure logging see the same lines. Characterization
  tests written against the old `print` code first (38 + 13 + 6) pin the exact output. Left on `print` with a
  reason: `deployment/_tiled_entry.py` and `deployment/gateway.py` (stdlib-only protocol/build scripts the supervisor
  and image build parse or run before app code is importable) and `agents/chebi.py` (a plain `python chebi.py`
  script that imports no project code).
- `PTH` (82): `os.path`, `os.*` and builtin `open` replaced with `pathlib` at 3 `noqa` exceptions where the
  lexical `abspath` and `os.path.join("./x")` semantics differ from `Path` (`literature/legacy.py`,
  `term_extractor/store.py`, `extract_terms.py`). One accepted difference: `ExtractionOutput` given a bare
  filename (no directory) used to raise `FileNotFoundError` from `os.makedirs("")` and now succeeds; no real call
  site passes one.
- `known_pyflakes.txt` is unchanged (18 entries). Binary-case manifest hashes regenerated.

### 2026-11-03: owner accepted the dispositions; closing state per row (branch `forge_merge-T27c`)

The owner replied "Accept proposals as written (Recommended)" to the T27 question, so each row's proposed
disposition is now the owner's decision. Every row was re-measured against the branch tip before this entry
(ruff, flake8 `--select=F`, isort, mypy `--strict`, AST scans, grep); the earlier log entries were not trusted.

Gates at the end of this session: `ruff check app` 0, `mypy` 0 errors in 345 files, `isort --check-only app` clean,
`flake8 --select=F` 0 (`known_pyflakes.txt` is empty), `D`/`UP`/`PTH`/`T20` 0.

Commits (all on `forge_merge-T27c`, each with the Claude co-author trailer):

| Commit | Rows |
|---|---|
| `d8b73ee` move the six inline tests of `json2kg.py` to `tests/test_json2kg.py`; delete commented-out code in `kg_rag_api.py` | F3.4, F3.5 |
| `a812662` all pyflakes findings cleared, `known_pyflakes.txt` emptied | F3.7 |
| `9b040aa` isort clean (`corpus_qa.py` added to isort `extend_skip`, it is byte-pinned) | F5.3, F0.5 |
| `6caf6bd` three untyped public defaults typed | F1.5 |
| `b98ca0d` no silent `except: pass` left | F4.1 |
| `5c8bd60` module loggers and logging where a handler converts a failure into a fallback | F4.3, F4.2 (part) |
| `f759186` CI gate for ruff, isort and mypy | F0.1, F0.3, F0.5 |
| `c364ff8` delete the uncalled `_retired_deterministic_locked` shim; regenerate `graph_publication.json` | F3.3 |

Final state per row (fixed = done on this branch or earlier and re-verified; deferred and intentional = as accepted by
the owner through the 2026-11-03 reply):

| Row | Disposition | Final state |
|---|---|---|
| F0.1 mypy config | fix | fixed: `[tool.mypy]` strict, tools in the pixi `quality` feature; the CI `python-quality` job now gates `mypy` (see note 1) |
| F0.2 coverage config | fix | fixed (config, `coverage.txt`); the full-suite figure is now in `coverage.txt` (2026-11-04: 90.8%) |
| F0.3 CI lint | fix | fixed: pyflakes gate against an empty baseline, ruff and isort gates added; the `E`/`W` style codes stay ungated and `build-app.yml` keeps its report-only broad flake8 pass |
| F0.4 line length | fix | fixed (115 everywhere) |
| F0.5 isort | fix | fixed: 0 files, gated in CI |
| F1.1 return annotations | fix | fixed (mypy strict, 0 `no-untyped-def`) |
| F1.2 parameter annotations | fix | fixed |
| F1.3 legacy typing generics | fix | fixed (ruff `UP` 0) |
| F1.4 `Any` in signatures | fix | fixed 2026-11-04 (branch `forge_merge-T27d`): ANN401 765 -> 0 in `app` (corpus_qa.py exempt, SHA-256 pinned); 149 signatures keep `Any` on a line with `# noqa: ANN401  # Any: <reason>`; the rule is enabled in `pyproject.toml` so new code cannot add one |
| F1.5 untyped public defaults | fix | fixed: `credential: TiledCredential \| str \| None` (`public.py`, `application.py`), `publication: Publication \| None`, `http_client: httpx.Client \| None`; a `Protocol` was not needed |
| F1.6 suppression markers | fix | fixed 2026-11-04: all 44 `# type: ignore` carry a code; the 46 `# noqa` lines that lacked a reason: 45 now have one (22 `BLE001`, 9 `S608`, 9 `E402`, one each of `PLC0415`, `S314`, `SLF001`, `A002`, `S603`) and one unneeded `I001` was removed |
| F2.1 function docstrings | fix | fixed (ruff `D` 0 with `D105`/`D107` ignored by project policy) |
| F2.2 class docstrings | fix | fixed |
| F2.3 module docstrings | fix | fixed |
| F2.4 docstring style consistency | fix | fixed 2026-11-06: per-module classifier `scripts/ci/docstring_style.py`; `app/` has 208 Google, 0 Sphinx, 0 mixed, 145 marker-free modules (353); test and CI gate keep it at 0 mixed |
| F2.5 docstring vs implementation | open | done 2026-11-06 for the six in-scope F3.1 hot spots (`readthrough_F25_F38.md`): 411 symbols read, 53 docstrings corrected, 13 suspected code bugs and 4 diverging contract docstrings left for the owner |
| F3.1 long functions | defer | deferred with owner approval; code untouched (`create_app` and `build_tools` would stay as closures if ever split) |
| F3.2 oversized modules | defer | deferred with owner approval; code untouched (`f2w_agent/api.py` is 7,267 lines now) |
| F3.3 dead `_retired_deterministic_locked` | fix | fixed in `c364ff8`: the 292-line body had already gone, a ten-line shim was left and is now deleted; nothing called it |
| F3.4 tests in production modules | fix | fixed: `json2kg.py` tests moved (renamed, `test_cli` uses `monkeypatch` for `sys.argv`); `agents/properties.py` holds none any more |
| F3.5 commented-out code | fix | fixed for `kg_rag_api.py`; `extract_terms.py` is now under `legacy/` (out of scope); the other `ERA001` hits are comment tables, not code |
| F3.6 open TODO | defer / intentional | deferred: `term_extractor/dashboard.py:1005` (JS, event-driven `showConnection()`); the supervisor TODO is gone |
| F3.7 pyflakes | fix | fixed: 24 findings (unused imports and assignments, an f-string without placeholders, `field` shadowed by a loop variable in four loops of `f2w_agent/api.py`); the three AUD-18 sites named in the row and F821 `history` were already gone |
| F3.8 magic literals | open | done 2026-11-06 in the same six modules: 58 constants hoisted (112 occurrences, no value change, frozen-contract literals left and listed), see `readthrough_F25_F38.md` |
| F4.1 `except: pass` | fix | fixed: 25 handlers; 18 now log (debug, warning for the supervisor's audit-event emits), 5 `unlink` handlers became `unlink(missing_ok=True)`, the two `CancelledError` handlers keep their shutdown comment |
| F4.2 return/continue-only handlers | fix after triage | fixed 2026-11-04: 155 typed `continue`/`return` handlers triaged one by one; 126 are legitimate fallbacks for an expected condition (about 95 now carry a one-line reason), 29 hid a failure (parser, catalog, job, I/O on required data) and now log the exception type at debug (22) or warning (7), control flow unchanged |
| F4.3 `except Exception` without a logger | fix | fixed for every handler that swallows (17 modules got a module logger and a log call). Handlers that re-raise or chain (`raise X from exc`, bare `raise` after cleanup) were left alone on purpose; `_tiled_entry.py` (stdlib-only, import list pinned), `agents/chebi.py` (standalone script), `json2kg.py` and `download_pdfs.py` (already log via the root logger) were not changed |
| F4.4 `print()` | fix | fixed (ruff `T20` 0; the three stdlib-only scripts keep `print` with a reason) |
| F4.5 f-strings in log calls | intentional | intentional |
| F5.1 `os.path` | fix | fixed (ruff `PTH` 0) |
| F5.2 argparse CLIs | defer | deferred as intentional legacy; untouched |
| F5.3 import order | fix | fixed (isort clean) |
| F5.4 test classes | intentional | intentional |
| F5.5 coverage | fix | measured 2026-11-04: full not-slow suite 90.8% line coverage (49,840 statements); 10 modules are below the 80% target, seven of them in `commands/` (CLI entry points) (`coverage.txt`); no tests were written in this task |

Notes:

1. The CI gate step (ruff, isort, mypy) cannot be run here; ruff, isort and mypy were run locally with the pixi-lock
   versions in the cached venv and are clean. mypy depends on the installed runtime packages, so the first CI run is
   the check.
2. Behaviour changes: none intended. Log calls were added in `except` bodies (control flow identical; messages avoid
   provider detail in `model_catalog`, curation resolution, judge, audit-log writes, OpenAlex and paper-evidence
   handlers by logging only the exception type). `unlink()` inside `except FileNotFoundError` became
   `unlink(missing_ok=True)`. Loop variables renamed in `f2w_agent/api.py`. No wire format, OpenAPI document,
   `corpus_qa.py` or `ui/` change.
3. T27 is **not** marked ready for H7 review. Remaining `fix` rows: F1.4, F2.4, F2.5, F3.8 (F1.6, F4.2 and the
   coverage figure were closed on 2026-11-04, see below). F1.4 and the read-throughs (F2.5, F3.8) are the large ones.

### 2026-11-04: F1.6, F4.2 and the coverage figure (branch `forge_merge-T27e`)

| Commit | Rows |
|---|---|
| `057a441` every remaining `# noqa` has a reason | F1.6 |
| `7b96818` silent typed `except` handlers triaged; 29 now log | F4.2 |
| coverage report (this entry's commit) | F0.2, F5.5 |

- F1.6: 45 bare `# noqa` lines got `# noqa: CODE - reason` (codes unchanged); one unneeded `I001` (isort passes) was
  removed. No `# type: ignore` is missing a code (grep, 0). `ruff check app` 0.
- F4.2: 155 handlers matched (typed `except`, body only `continue` or a `return` without a call, `corpus_qa.py`,
  `legacy/` excluded). Class (a) legitimate fallback: 126 (unparsable optional or model-produced values,
  `FileNotFoundError`/`OSError` probes, URL/IP parsing, optional imports, status returned to the caller); (b) hides
  a failure: 29, each now logs only `type(exc).__name__` at debug (22) or warning (7: `supervisor.py` approval
  record, `api.py` x2 graph file, `workflow_state.py`, `journal.py` lease file, `index_store.py` meta,
  `hybrid_rag/sources.py` manifest). Twelve modules gained `logger = logging.getLogger(__name__)`; `bl1101_ingest.py`
  uses its existing `LOGGER`. `mypy` 0 errors (346 files), `ruff check app` 0.
- F0.2/F5.5: full not-slow suite from a clean export, `--cov=app` under xdist: 6330 passed, 45 skipped, 4 xfailed,
  0 failed. Line coverage 90.8% (45,245 of 49,840 statements), above the 80% target; 10 modules are below it
  (`commands/repair_corpus` 14.4, `run` 31.2, `tiled_server` 31.2, `harvest_rsoxs` 51.3, `build_kg_snapshot` 66.0,
  `paper_finder` 70.4, `corpus_qa` 72.8, `api/routes_library_harvest` 57.8, `library/probe` 76.2,
  `jobs/replacement_step` 79.6). Per-package and per-module numbers are in `coverage.txt`. Closing the gap needs new
  tests (not part of this task); the plan acceptance (no in-scope module below 80%) is therefore still unmet.
- No behaviour change; no test files, `corpus_qa.py`, `ui/` or frozen files touched.

### 2026-11-04: F1.4 `Any` in signatures (branch `forge_merge-T27d`)

- Measure: `python -m ruff check app --select ANN401 --statistics`: 765 at the branch point (forge_merge `a332d47`), **0
  after** (`--ignore-noqa`: 150, of which 149 carry a reason and one is `corpus_qa.py`, which is byte-pinned and listed
  in the ruff per-file ignores with the other pinned-file exemptions).
- Rule enabled: `select = [..., "ANN401"]` in `[tool.ruff.lint]`; per-file ignores only for `tests/`, `scripts/`,
  `examples/` and `corpus_qa.py`. No `app` module is exempt, so the list of unfinished modules is empty.
- Shared aliases live in `app/modules/contracts/json_types.py` (the one place `Any` is spelled for them, each with a
  comment): `JsonValue` (decoded JSON or a JSON-like record), `JsonObject` (`dict[str, Any]`) and `Untyped` (untyped
  third-party object, lazily imported attribute). They sit in `contracts` because `security/` modules may import only
  `contracts` (tests/disclosure and tests/runtime pin that). About 130 signatures use them.
- Real types introduced: `object` for parameters that are only inspected (about 300, each validated per site by
  mypy), the real class or a Protocol for injected collaborators (`Admission`, `ChatModel`/`BoundChatModel` in
  `agent_runtime/chat_model.py`, `ToolPack`/`ToolPackOne`, `EvidenceSource`, `UploadSource`, `RoundResolver`,
  `ToolInvocation`, `DocumentStore`, `JobService`, `TiledIdentityPort`, `DeploymentSettings`, `CurationStore`,
  `FolderRoots`, `KnowledgeOperations`, `JobHandle`, `ReadOutcome`, `sqlite3.Connection`/`Row`), PEP 695 generics for
  pass-through helpers (`note_retrieval`, `note_paper_evidence`, `_await_with_deadline`, `_choice`, `_guarded`), and
  concrete returns for `public.py`, `operations.py`, `application.py` and `supervisor.py` (about 15 redundant `cast`s
  removed as a result).
- Where the 149 retained `Any` signatures are, by reason: forwarded `**kwargs` (about 30: `FastAPI` route decorators,
  `JobService.register`, `F2WApplication`, `json.dumps`, settings constructors), PEP 562 `__getattr__` hooks (4),
  decorator pass-through (4), untyped third-party or donor objects (docling, pypdfium2, lxml, faiss, arxiv, pyalex,
  SentenceTransformer, networkx, donor `KnowledgeGraph`, `Orchestrator`, legacy `AgentPipelineService`), duck-typed
  collaborators that tests replace with fakes, the backend ports in `operations/graph.py` and `knowledge.py` (JSON
  and Tiled backends expose `graph_ids`/`snapshot`/`client` beyond `GraphPort`; no shared Protocol yet), and a few
  places where a real type needs a control-flow change (`curation/resolution.py` reply, `jobs/sqlite.py` `one`).
  Largest remaining: `operations/knowledge.py` 10, `literature/parsers.py` 7, `public.py` 6, `operations/graph.py` 6,
  `operations/reports.py` 5.
- Per top module (before -> after retained): `security/disclosure.py` 65 -> 0, `public.py` 49 -> 6,
  `runtime_bridge/qa_route.py` 27 -> 3, `operations/knowledge.py` 26 -> 10, `f2w_agent/api.py` 26 -> 2,
  `application.py` 18 -> 1, `api/operations.py` 18 -> 1, `vision/tools.py` 16 -> 3, `security/authorization.py` 15 -> 4,
  `api/legacy_ui.py` 15 -> 1, `literature/parsers.py` 13 -> 7, `f2w_agent/trace.py` 13 -> 1, `api/main.py` 13 -> 1,
  `operations/graph.py` 12 -> 6, `operations/reports.py` 11 -> 5, `operations/corpus.py` 11 -> 0,
  `agent_runtime/executor.py` 11 -> 3, `bl1101_ingest.py` 7 -> 4.
- No behaviour change: only annotations, imports under `TYPE_CHECKING`, removal of redundant `cast`s (one
  `cast(int, ...)` and a few `cast(Protocol, ...)` added where a value was already narrowed by a runtime check), and
  PEP 695 type parameters. The pinned OpenAPI fixtures (`tests/contracts/legacy_ui/fixtures/openapi_*.json`) pass
  unchanged. `mypy` 0 errors (348 files), `ruff check app` 0, isort unchanged (the one file that fails isort
  locally, `vision/docling_ocr.py`, fails on forge_merge too: environment-dependent third-party classification).
  Not-slow suite from a clean export: 6333 passed, 54 skipped, 4 xfailed, 0 failed. No test files, `corpus_qa.py`,
  `ui/` or frozen files touched.

### 2026-11-06: F2.4, F2.5 and F3.8 closed with evidence (branch `forge_merge-T27-sniff`)

The owner asked for the read-through to be done by an agent; the H7 gate stays with the owner, so this entry
prepares evidence and applies only safe fixes (docstring text and value-preserving constant hoists).

- F2.4: `scripts/ci/docstring_style.py` classifies every module under `app/` (`legacy/`, `graphify-out`,
  `__pycache__` excluded) as Google / Sphinx / mixed / none from docstring section markers (`Args:`/`Returns:`/
  `Raises:`/`Yields:`/`Attributes:` headers vs `:param`/`:returns:`/`:raises`/`:rtype:`/`:ivar` fields).
  Before: 353 modules, 208 Google, 0 Sphinx, 0 mixed, 145 none (confirmed by grep: no `:param`/`:returns:`/numpy
  `Parameters\n----` marker anywhere in `app/` outside `legacy/`, which has none either). After: identical, so no
  module needed converting. The gate: `tests/ci/test_docstring_style.py` (classifier unit tests on synthetic
  Google/Sphinx/mixed/none sources plus `test_app_has_no_mixed_module`) and `python scripts/ci/docstring_style.py
  --check` added to the CI `python-quality` Gate step (owner note: this is a new **blocking** line in that step; it
  is at 0 today and the same assertion already runs in the baseline suite via the new test).
- F2.5/F3.8: `readthrough_F25_F38.md`. The F3.1 hot-spot list minus `extract_terms.py` (now under `legacy/`):
  `f2w_agent/api.py`, `kg_rag_api.py`, `f2w_agent/retrieval_agent.py`, `agent_runtime/supervisor.py`,
  `agents/properties.py`, `term_extractor/tools.py`, read in full. 411 symbols compared with their docstrings;
  53 docstrings corrected (Google style kept); 13 suspected code bugs recorded with file:line, none changed
  (B1-B13 in the doc; the ones worth a ticket: `properties.py` `VALUE_UNIT_PATTERN` cannot parse `%` units so
  the extractor's efficiency path is broken end to end; `api.py` `_resume_download` hard-codes
  `validate_downloads=False`; `api.py` route `graph_neighborhood` ignores `hops` on the Tiled path;
  `kg_rag_api.py` `retrieve_nodes` seed slice is a no-op). Four diverging docstrings were deliberately not edited
  because they are contract text: the `graph_neighborhood` route description (frozen OpenAPI) and three `@tool`
  descriptions in `term_extractor/tools.py` (LLM-visible). 58 module-level constants hoisted (112 literal
  occurrences; values, types and evaluation time unchanged); 53 literals justified in place, including every
  OpenAPI default, route path, wire key/value, LangGraph node name and LLM tool vocabulary.
- Verification: AST comparison against `HEAD` with docstrings stripped and the new constants inlined back is
  identical for four files; the two differences are a removed function-local `MAX_SNIPPETS = 6` (now module
  level) and `/ 50.0` becoming `/ BEAM_PATH_INDEX_SPAN` (`50`, same float result). `ruff check app` 0,
  `isort --check-only app` clean, `mypy` 0 errors (352 files), `python -m evals.binary.import_companion --check`
  up to date, OpenAPI fixture unchanged. Full not-slow suite from a clean export: __SUITE__.
- No behaviour change intended; no test, `corpus_qa.py`, `ui/` or frozen file touched; docker untouched.

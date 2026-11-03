# T17B legacy audit (H8 review packet)

Status: **audit (T17B step 1); steps 2-5 partly executed after the owner's H4 approval of 2026-11-03,
see section 8 for what was done and what the audit got wrong.** The dispositions in sections 1-7 are
the original proposals; section 8 supersedes them where they differ.

Date: 2026-11-02. Tip audited: `forge_merge` at `1c9aa81`. Baseline: companion
`f2w_baseline_manifest.json` (B01-B36; archive `222b1e7`, 2026-09-29). "Pre-framework" means
present at the baseline commit or named by B01-B36; everything added by a migration task is
"framework".

## Classes

| Class | Meaning | Action (after H4) |
|---|---|---|
| (a) ported | a canonical framework module covers it | move the original to `app/modules/legacy/` with a deprecation notice |
| (b) needs porting | logic belongs in the framework, no canonical home yet | port with parity tests, then (a) |
| (c) operator script | lives in `scripts/` | promote to `[project.scripts]` if frequently used, otherwise leave |
| (d) retire | dead, superseded or out of scope | list here; delete only after H8 approval |
| (r) retained | the companion crosswalk names the original as the retained facade or canonical home | stays where it is; no move; typing and docstrings under T27 |

Class (r) is an audit finding, not a plan deviation: for 20 of the 36 baseline entries the
crosswalk's own instruction is "retain"/"retained ... facade" (for example B09 "kg_rag_api.py
facade", B15 "retained term_extractor/store.py", B18 "retain hybrid_rag/ first"). Moving those
to `legacy/` would break the facades the contract tests pin. The owner may reclassify any (r)
row to (a)/(b).

Importer counts are static (`grep` over `app/`, `scripts/`, `tests/`, excluding the file
itself, `__pycache__` and `graphify-out`); "app importers" lists only `app/` and `scripts/`.

## 1. Python files outside `app/modules/` (excluding `tests/`, `evals/`, `scripts/`, `splash_links/`, `AGENT/`)

| File | Lines | Origin | App importers | Class | Disposition |
|---|---|---|---|---|---|
| `app/api/*.py` (16 files) | - | framework (T02-T19, T24, T28) | - | framework | none |
| `app/public.py` | - | framework (T15, B30) | - | framework | none |
| `app/__init__.py` | - | framework | - | framework | none |
| `app/run_pipeline_cborg.py` | 186 | pre-framework (2025-11) | none (`pyproject.toml`: "Not packaged") | **(d)** | superseded by the T10 jobs pipeline and the `/v1/jobs` lifecycle; retire after H8, or move to `scripts/` if the owner still runs it |

## 2. Pre-framework files inside `app/modules/` (flat modules)

| File | Lines | First commit | App importers | Baseline | Class | Disposition |
|---|---|---|---|---|---|---|
| `extract_terms.py` | 2122 | 2025-06 | `run_pipeline_cborg.py`; `schema/registry.py` (entry-point default and `NORMALIZER_LEGACY` name only); `scripts/build_kg.sh` | - | **(b)** | the LinkML `SchemaHelper` normaliser and the CLI are the parts still named; port the normaliser into `app/modules/schema/` (or point `registry.py` at the canonical one), then move to `legacy/` |
| `json2kg.py` | 639 | 2025-06 | `graph/snapshot.py`, `graph/adoption.py`, `tiled_sim.py`, `bl1101_ingest.py`, `term_extractor/provenance.py`, `scripts/build_kg.sh` | B24 | **(r)** | crosswalk: "retain json2kg CLI facade + graph/snapshot services"; the snapshot builder imports its graph construction, so it is the canonical builder today |
| `kg_rag_api.py` | 2015 | 2026-06 | `api/main.py` (model API mount, B7), `security/authorization.py` | B09, B10 | **(r)** | retained facade, live on every profile as `/fair2wise/model-api`; the retrieval/graph services it wraps are the port |
| `tiled_graph.py` | 1054 | 2026-09 | `tiled_sim.py` only (`is_als_production_tiled_host`, `normalize_tiled_uri`, `tiled_graphql`) | B25 | **(a)** | canonical `graph/backends/tiled.py` and `tiled_client.py` exist; redirect the three `tiled_sim` imports, then move |
| `tiled_sim.py` | 1101 | 2026-09 | `scripts/promote_rsoxs_snapshot.py`, `scripts/ingest_tiled_sim.py`; snapshot tests | B26 | **(r)** | crosswalk: "existing fixture/operator path"; also the simulated-Tiled fixture for `tests/snapshots`; its production promotion role was taken over by `graph/releases.py` (T26) |
| `bl1101_ingest.py` | 2709 | 2026-09 | `tiled_sim.py`, `scripts/ingest_bl1101.py` | - | **(b)** | the schema mapping moved to `schema/bl1101_mapping.py` (T22D) but ingestion (GitHub repository walk, chunking, graph assembly) has no framework home; port into `literature/` or `graph/imports/` behind a parity test on the bl1101 fixtures, then move |
| `prov.py` | 273 | 2026-09 | none in `app/`; `scripts/ingest_rsoxs_v3.py` mirrors its constants | B31 | **(a)** | canonical `provenance/` package exists; move after `ingest_rsoxs_v3.py` is classified (section 4) |
| `project_config.py` | 111 | 2026-06 | `kg_rag_api.py`, `scripts/test_chat_apis.py` | B32 | **(r)** | crosswalk: "existing project_config + application instance config adapter" |
| `cborg_limiter.py` | 99 | 2026-06 | `kg_rag_api.py`, `model_catalog.py`, `extract_terms.py` | - | **(r)** | used by framework code (`model_catalog`); canonical as is |
| `cborg_http.py` | 174 | 2026-08 | 10 importers | - | **(r)** | hardened CBORG client used across the framework |
| `application.py`, `llm_admission.py`, `model_catalog.py`, `cli_output.py`, `dotenv_entry.py`, `validation_settings.py`, `resources.py` | - | 2026-10/11 | - | - | framework | none |

## 3. Pre-framework packages inside `app/modules/`

### `f2w_agent/` (B01-B08, B22, B27-B30, B33)

| File | Lines | Importers outside the package | Class | Disposition |
|---|---|---|---|---|
| `api.py` | 7408 | `api/main.py`, `api/legacy_ui.py`, contract tests | **(r)** | B01-B03: "keep `create_app` ... as facades"; the service seams were extracted into `application.py`, `agent_runtime/`, `jobs/`, `graph/`; the module stays the browser contract |
| `cli.py`, `coordinator.py`, `extractor_agent.py`, `multi_kg.py`, `prompts.py`, `retrieval_agent.py`, `paper_evidence_agent.py`, `trace.py` | - | framework modules (`api/`, `operations/`, `schema/registry`, `launchers/`, `extraction/strategies`, `contracts/`) | **(r)** | B04, B05, B07, B08, B27, B30: retained adapters named by the crosswalk |
| `orchestrator_agent.py` | 517 | none (package-internal) | **(a)** | B05 destination `agent_runtime/` routing exists (supervisor, policies); confirm `api.py` no longer routes through it, then move |
| `debate_agent.py` | 267 | none (package-internal) | **(a)** | B06 destination `curation/` exists; move after confirming `coordinator` no longer constructs it on a live path |
| `download_agent.py` | 917 | none (package-internal) | **(b)** | B22 and owner B29 (T29): switch to the hardened T05 downloader behind a parity test; move after T29 |
| `kg_update.py` | 512 | none (package-internal) | **(b)** | Save path; replaced by the journaled multi-write in T18D; move after T18D |
| `session_memory.py` | 529 | `api.py` only | **(r)** | B28: "existing session_memory path" retained |
| `workflow_state.py` | 120 | `api.py` only | **(r)** | B29: checkpointer adapter retained |
| `materials_project.py` | 258 | `retrieval_agent.py`, `security/disclosure.py` | **(r)** | B33: "retained optional compatibility evidence adapter" |

### `term_extractor/` (B11-B17)

| File | Lines | Importers outside the package | Class | Disposition |
|---|---|---|---|---|
| `agent.py`, `clients.py`, `message.py`, `models.py`, `orchestrator.py`, `prompts.py`, `provenance.py`, `schema.py`, `services.py`, `store.py`, `tools.py` | - | 27-157 each | **(r)** | B11-B17: "retained ... " in every destination; these are canonical for extraction today |
| `merge_index.py` | 301 | `extract_terms.py`, `canonicalization/measurement.py` | **(r)** | used by canonical canonicalization code |
| `dashboard.py` | 1329 | `launchers/`, `extractor_agent.py`, `user_agent.py`, `monitored_agent.py` | **(c)-like** | Streamlit/TUI operator surface behind `launchers/`; keep, promote through `launchers` if the owner uses it |
| `academy_agent.py`, `monitored_agent.py`, `user_agent.py` | 116 / 220 / 80 | `launchers/academy_extractor.py`, `launchers/user_agent.py`, `literature/openalex.py` | **(r)** | Academy/Globus Compute extraction path (optional `globus` extra); retained |
| `source_repos.py` | 454 | `orchestrator.py` only | **(r)** | internal to the retained orchestrator |

### `hybrid_rag/` (B18-B21)

All nine files are **(r)**: the crosswalk says "retain hybrid_rag/ first; later retrieval/
service facades". `context.py` (169 importers) and `evidence.py` (165) are the live evidence
model; `chunker.py`, `index_store.py`, `ranker.py`, `retriever.py` are package-internal. No
`retrieval/` package was created by the migration, so there is nothing to move them behind.

## 4. `scripts/` (class (c), with promotion and retirement proposals)

Reference counts are repository-wide (`app/`, `scripts/`, `tests/`, `docs/`, `docker/`,
`.github/`, `pyproject.toml`, `pixi.toml`). There is no `[project.scripts]` table today.

| Script | Lines | Last change | Refs | Proposal |
|---|---|---|---|---|
| `run.py` | 167 | 2026-10-29 | 28 | **promote** (`f2w-run`); B36 facade to the job service |
| `tiled_server.py` | 125 | 2026-10-14 | 28 | **promote** (`f2w-tiled-server`); used by the image and tests |
| `start_agent_backend.sh` | 297 | 2026-10-30 | 24 | keep (compose `agent` target) |
| `harvest_rsoxs.py` | 2487 | 2026-10-30 | 17 | **promote** (`f2w-harvest`); B23 operator path |
| `paper_finder.py` | 845 | 2026-09-21 | 17 | **promote** (`f2w-paper-finder`); also a declared API surface (`PAPER_FINDER_SURFACE`) |
| `corpus_qa.py` | 916 | 2026-09-25 | 16 | **promote** (`f2w-corpus-qa`); B35 |
| `download_pdfs.py` | 24 | 2026-10-13 | 16 | **promote** (`f2w-download-pdfs`); thin wrapper over `literature/download_pdfs.py` |
| `repair_corpus.py` | 482 | 2026-09-25 | 13 | **promote** (`f2w-repair-corpus`); B34 |
| `analyze_kgs.py` | 272 | 2026-06-02 | 11 | leave |
| `prepare_kg_release.py`, `build_kg_snapshot.py`, `adopt_legacy_kg.py`, `promote_rsoxs_snapshot.py` | 188 / 83 / 88 / 81 | 2026-10 | 8 / 2 / 2 / 5 | **promote** the release trio (`f2w-kg-snapshot`, `f2w-kg-release`, `f2w-kg-adopt`); `promote_rsoxs_snapshot.py` is superseded by `graph/releases.py` promotion -> **(d)** |
| `get_pdf_years.py`, `fetch_docling_models.py`, `index_source_rag.py`, `check_trace.py`, `annotation_report.py`, `run_eval_batch.py`, `harvest_xray.py`, `harvest_tomo.py` | - | 2026-09/10 | 1-8 | leave (developer / one-off analyses) |
| `ingest_bl1101.py`, `ingest_bl1101_v7.py`, `ingest_rsoxs_v3.py`, `build_v7_kg.py`, `ingest_tiled_sim.py` | 21 / 564 / 510 / 563 / 25 | 2026-09 | 4-7 | version-specific ingestion runs; **(d)** after T08C replaces per-version imports (D3.23 decides whether bl1101 v8/v9 and rsoxs_v3 are rebuilt first) |
| `list_missing_rsoxs_papers.py`, `match_als_zips.py` | 355 / 503 | 2026-09 | 7 / 5 | leave (corpus bookkeeping) |
| `test_chat_apis.py`, `test_compose.sh` | 88 / 53 | 2026-10 / 2026-09 | 4 / 2 | **(d)**: ad-hoc checks superseded by `tests/contracts` and `tests/deployment` |
| `scripts/app/modules/kg_rag_api.py` | - | 2026-10-09 | `DEV_MODEL_API_SURFACE` in `security/authorization.py` | **(d)**: a second copy of the model API declared as a dev surface; retire with the surface constant once the owner confirms it is unused |
| `wipe_splash_db.sh`, `reimport_merged_kg.sh`, `build_kg.sh` | 107 / 196 / 111 | 2026-07/08 | 2-4 | **(d)** with T18E (Splash wipe retired) and the Splash retirement gate (T16B) |
| `deploy_nersc.sh`, `f2w_nersc_api.sh`, `f2w_nersc_smoke.sh`, `run_nersc_3agent.sh`, `nersc_remote_setup.sh`, `write_nersc_env.sh` | - | 2026-06/10 | 1-3 | leave; NERSC operator scripts outside the ALS deployment contract (owner to confirm still used) |
| `start_agent_frontend.sh`, `start_all.sh`, `start_rsoxs_stack.sh`, `install_pixi.sh`, `run_local_container.sh` | - | 2026-07/11 | 0-7 | leave (developer launchers) |
| `update_readme_tree.py` | 251 | 2025-03 | 6 | **(d)** candidate: last changed before the project moved to MkDocs |

## 5. `app/modules/legacy/` today

Eight archived files (3,302 lines), not packaged (no `__init__.py`), excluded from ruff,
mypy and coverage. None carries the module-level deprecation notice T17B step 3 requires;
three carry an older `DEPRECATED(pymupdf)` comment.

| File | Lines | Repo references | Proposal |
|---|---|---|---|
| `build_onto.py` | 1111 | 3 (docs) | add notice; keep until H8 |
| `extract_terms.py`, `extract_terms_linkml.py` | 443 / 986 | 5 / 3 | add notice pointing at `term_extractor/` and `extraction/`; keep until H8 |
| `extract_terms_linkml_jun3.py` | **0** | 2 | **(d)**: empty file |
| `extracted_terms_json2kg_with_context.py` | 110 | 3 | add notice pointing at `json2kg.py`; keep until H8 |
| `json2kg.py` | 219 | 4 | add notice; keep until H8 |
| `kg_rag_ollama.py`, `kg_rag_ollama_nersc.py` | 229 / 204 | 3 / 2 | add notice pointing at `kg_rag_api.py`; **(d)** candidates (Ollama/NERSC path not in any image or profile) |

## 6. Packaging check (acceptance item)

`pyproject.toml` lists the packages explicitly (`f2w.modules.f2w_agent`,
`f2w.modules.hybrid_rag`, `f2w.modules.term_extractor` included; `legacy/` and
`run_pipeline_cborg.py` excluded by comment). The "clean `pip install fair2wise` installs no
stray top-level packages" check is `tests/packaging` territory and was not re-run for this
audit; it is part of step 5.

## 7. Summary and what the owner decides

| Class | Count | Notes |
|---|---|---|
| (a) ported, movable | 5 | `tiled_graph.py`, `prov.py`, `orchestrator_agent.py`, `debate_agent.py` (+ `extract_terms.py` once its normaliser is redirected) |
| (b) needs porting | 4 | `extract_terms.py` (normaliser), `bl1101_ingest.py`, `download_agent.py` (T29), `kg_update.py` (T18D) |
| (c) scripts to promote | 11 | section 4 "promote" rows |
| (d) retire candidates | 16 | `run_pipeline_cborg.py`; `promote_rsoxs_snapshot.py`; five version-specific ingest scripts; `test_chat_apis.py`, `test_compose.sh`; `scripts/app/modules/kg_rag_api.py`; three Splash shell scripts; `update_readme_tree.py`; `extract_terms_linkml_jun3.py`; two Ollama files |
| (r) retained by the crosswalk | 20 baseline entries, 35 files | no move; T27 covers typing and docstrings |

Owner decisions needed before step 2: confirm or reclassify every (r) row; approve the (d)
list for the H8 packet (no deletion before H8); say whether the NERSC scripts are still used;
confirm the `[project.scripts]` names proposed in section 4.

## 8. Execution record (T17B steps 2-5, branch `forge_merge-T17B`, owner approval 2026-11-03)

Approved: class-(a) moves, class-(b) ports, the 11 script promotions and moving `scripts/harvest_*.py` onto the
hardened downloader. **Not** approved and not touched: the 16-file retire list (d) and the (r) reclassification.

### Done

| Item | Result |
|---|---|
| `scripts/harvest_{rsoxs,xray,tomo}.py` | PDFs through `literature.legacy_download.hardened_download_pdf`; Crossref/OpenAlex lookups through the new `hardened_get_json` (policy on every hop, 8 MiB cap). No `requests`/`urllib.request` left in the three scripts (`tests/test_harvest_hardened.py`). |
| `prov.py` (a) | **Not moved to `legacy/`.** The audit's premise (a canonical twin in `provenance/`) is wrong: `provenance/` is the audit-log package; nothing else defines the PROV-O constants and helpers, and `docs/adr/schema-ownership.md` names the file the authority. Moved (history-preserving rename) to `app/modules/provenance/prov_o.py` instead; behaviour unchanged. |
| `extract_terms.py` (a+b) | Moved to `app/modules/legacy/extract_terms_cli.py` (the name `legacy/extract_terms.py` is taken by an older file). The canonical normaliser `term_extractor.schema.SchemaHelper` already is the registry default; the divergent copy stays as `NORMALIZER_LEGACY` (T22A F4). `legacy/` is now a package (`f2w.modules.legacy`) so that path works from a wheel. |
| 9 scripts promoted | `run`, `tiled_server`, `harvest_rsoxs`, `paper_finder`, `corpus_qa`, `repair_corpus`, `build_kg_snapshot`, `prepare_kg_release`, `adopt_legacy_kg` moved to `app/modules/commands/` with `[project.scripts]` entries; `f2w-download-pdfs` points at `literature.download_pdfs:main`. `scripts/<name>.py` stays as a launch facade. The audit's "11" promote rows are 10 console scripts (7 named singly plus the release trio). |

### Not done, with the reason (owner decision needed)

| File | Audit class | Finding |
|---|---|---|
| `tiled_graph.py` | (a) | Not only `tiled_sim` imports it: `f2w_agent/api.py` and `f2w_agent/retrieval_agent.py` import `TiledLookupGraph`, `lookup_experiment_identity`, `probe_tiled_status`, `load_tiled_config`, `coerce_local_tiled_uri`, `is_experiment_identity_question` and more, and `graph/backends/tiled*.py` has none of them (it is the write/import path). The live lookup and probe have no canonical home. |
| `orchestrator_agent.py`, `debate_agent.py` | (a) | The audit's own condition ("confirm `api.py` no longer routes through it") fails: `f2w_agent/api.py` constructs `WorkflowOrchestratorAgent` and `EvidenceDebateAgent` and imports private helpers; `coordinator.py` imports `EvidenceDebateAgent`. Both are exported lazily from `f2w_agent/__init__.py`. |
| `download_agent.py` | (b) | The port step is already done (T29: `hardened_download_pdf`, `tests/test_download_parity.py`). Moving it is blocked: `api.py`, `coordinator.py`, `f2w_agent/__init__.py` import `DownloadAgent` and four helpers live. |
| `kg_update.py` | (b) | Still imported by `api.py`, `coordinator.py` and `library/extraction.py`; `graph/legacy_save.py` covers only the Save path. |
| `bl1101_ingest.py` | (b) | `hybrid_rag/sources.py` imports `REPO_SPECS`, `tiled_sim.py` imports it; ingestion (GitHub walk, chunking, graph assembly) has no framework home. A real port is a multi-day task. |

Each of these is imported by `f2w_agent/api.py`, which class (r) retains as the browser contract, so moving them
under `legacy/` would make a retained, live module import from the archive. Options for the owner: (1) reclassify
them (r) (stay), (2) keep `legacy/` copies plus re-exporting shims at the old path (H8 already contemplates removing
shims), or (3) fund real ports: `tiled_lookup` under `graph/backends/`, `routing`/`debate` under `agent_runtime/`
and `curation/`, `kg_update` under `graph/`, `bl1101` under `graph/imports/`.

### Open items found while doing this

* `literature/download_pdfs.py` still has its own `requests`-based `download_pdf` (kept as the parity oracle of
  `tests/test_download_parity.py`) and `requests` resolvers (Unpaywall, OpenAlex, Crossref, Semantic Scholar, CORE,
  OSTI, Europe PMC). The `f2w-download-pdfs` CLI and `run_arxiv_workflow` still download through it. Decision:
  switch its two call sites to the hardened fetcher and keep the old function only in the test.
* `corpus_qa.py` is promoted byte-for-byte because `test_cli_is_unchanged` pins its SHA-256 (OP02); it is excluded
  from mypy strict and the ruff D/UP/PTH/T20 rules until that pin is re-baselined.
* `scripts/harvest_xray.py` and `scripts/harvest_tomo.py` stay in `scripts/` (audit: leave) but import the packaged
  `harvest_rsoxs`.

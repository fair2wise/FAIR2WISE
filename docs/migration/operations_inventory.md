# T19A: Knowledge Operations inventory and population semantics

**Gate:** H1. **Status:** inventory and proposal only. No Tool is registered and no behavior is changed. **Baseline:** `222b1e7b130567dbf4a3242697ac2b465794a38d` (clean export).

The machine-readable record is `operations_inventory.json`, with 48 items (OP01-OP48). Each item gives sha256, effects, inputs, population, counting unit, completeness, CLI flags, tests, and the proposed source -> service -> Tool mapping with a reason. This page summarizes it. Line references point into the baseline export.

**Method.** Static reading and AST analysis. A few read-only spot computations (`corpus_qa.audit_summary_stats` / `audit_zero_measurement_papers`, `analyze_kgs.load_kg` / `calculate_structural_metrics`) ran under `env -i` with the baseline venv on committed `storage/` files. No network, LLM, Tiled or `.env` access was involved.

## 1. Headline counts

| Disposition | Items |
| --- | --- |
| eligible_knowledge_operation | 5 (OP01-OP05) |
| cli_only | 13 |
| needs_owner | 5 (OP07, OP10, OP11, OP12, OP25) |
| out_of_scope | 25 (launchers, deployment, development, Splash, extraction) |

Coverage: all 36 `scripts/` entries (20 `.py`, 15 `.sh`, the stray `scripts/app/modules/kg_rag_api.py`), `slurm_scripts/run_ollama.sh`, `storage/terminology/parse_terms.py`, and 10 app-level entrypoints or deterministic summary functions.

- **Destructive (9):** `repair_corpus.py`, `ingest_rsoxs_v3.py`, `build_v7_kg.py`, `ingest_bl1101_v7.py`, `ingest_bl1101.py` (`--from-scratch` deletes the cache), `build_kg.sh` (overwrites any earlier `.bak`), `reimport_merged_kg.sh` (Splash wipe), `wipe_splash_db.sh`, `test_compose.sh` (its own volumes).
- **Load the repo `.env`:** 15 items, either via `load_dotenv` or by sourcing it. `write_nersc_env.sh` also copies secrets to NERSC.

## 2. Proposed Knowledge Operations (each has a reason; nothing is registered blindly)

| ID | Source | Service (proposed) | Tool | Skill | Counting unit | Completeness | Why eligible |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OP01 | `paper_evidence_agent.py:summarize_extracted_terms` 122-292 | `operations/papers.py` (moved unchanged) | `lit_summarize_extracted_terms` | summarize_literature | raw term records for one PDF filename, on eligible pages | full scan; display capped at 16 (API :4091) or 6 (:6355) | deterministic, page-cited and tested; first T19B wrapper |
| OP02 | `scripts/corpus_qa.py:audit_*` 173-799 | `curation/checks.py` + `operations/corpus.py` | `curate_audit_corpus` | audit_literature_corpus | per section (see 3.2) | full scan; example lists capped | read-only diagnostics with an existing CLI |
| OP03 | `scripts/analyze_kgs.py` 9-234 | `graph/metrics.py` + `operations/graph.py` | `kg_compute_statistics`, `kg_compare_snapshots` | query_knowledge_graph | distinct node ids; distinct (s,p,o) triples; papers taken from the filename | full per file; some files silently skipped | the only existing graph-metric code |
| OP04 | `scripts/list_missing_rsoxs_papers.py` 99-277 | `operations/corpus.py` | `lit_list_missing_papers` | audit_literature_corpus | PubTemp RTF publication records | full, but limited to PubTemp x manifest | verified and tested coverage report |
| OP05 | `scripts/get_pdf_years.py` 102-130 | `literature/dates.py` + `operations/corpus.py` | `lit_inspect_publication_dates` | audit_literature_corpus | PDF files (top level only) | non-recursive scan | deterministic; needs method/uncertainty output added |

Constraints shared by all five:
- Each existing CLI stays a facade over the same service.
- Tools return typed results with population, counting unit and coverage, plus an artifact reference for full results.
- No arbitrary paths reach the Tools: corpora and files are resolved by injected services.
- No repair, publication or ingestion follows from an analytics call.

## 3. Population semantics and counting-unit pitfalls

### 3.1 Counts that could be mistaken for exhaustive

1. **Tiled experiment-identity counts are capped at 800 per type (OP07).** `tiled_graph._page_entities` stops after `MAX_PAGES=8` x `PAGE_SIZE=100` (`tiled_graph.py:44-46,770-810`). The synthetic node text "Live Tiled GraphQL currently lists N ..." (`:812-824`, `:1017`) does not mention the cap.
2. **corpus_qa §8 histogram (OP02).** It increments once per (term, `source_metadata` entry) (`corpus_qa.py:766-771`), while its label says "unique DOI/year combos" (`:787`). This is **confirmed on baseline data**: `extracted_terms_xray_papers_cborg_chat.json` produces a histogram total of **155** against **4** distinct papers with a year (7 paper keys). Older terms files have no `source_metadata`, so their histogram is empty.
3. **"Papers" means PDF filename keys, not Works (OP02 §1/§5/§8, OP06 `unique_papers`).** arXiv and journal versions are counted separately. The checkpoint named `147papers` has 145 distinct source-paper keys.
4. **analyze_kgs (OP03).** The paper denominator is parsed from the filename (`:92-104`). Gemini and CBorg checkpoints in `storage/kg` are skipped silently (`:103-104`). Duplicate triples collapse in a set. Dangling endpoints inflate `avg_relations_per_entity` (none were found in the two KGs checked). KGs shaped as `nodes`/`edges` (OP10) load as empty.
5. **corpus_qa §6 under-merge detection** compares only adjacent labels after sorting (`:612-622`). It is a heuristic sample, not an all-pairs check.
6. **get_pdf_years (OP05)** uses `os.listdir` on one folder, so the harvest layout `papers/rsoxs/{year}/` is not covered.
7. **The source-RAG index defaults to a gold sample of 12 Works (OP16)** (`index_source_rag.py:43-55`, `hybrid_rag/sources.py:22`).
8. **summarize_extracted_terms omitted count (OP01).** In query-scoped mode, "N additional terms omitted" counts only the relevant terms that were not shown (`:282-284`).
9. **Top-k retrieval** (`kg_rag_api.retrieve_nodes` 1279-1334) and **search-capped harvests** (OP17-OP19) must never back a corpus total.

### 3.2 corpus_qa counting units, section by section

| Section | Unit |
| --- | --- |
| §1 duplicate works | filename keys; near-duplicate check only covers papers that have a `source_metadata` title |
| §2 stubs | term entries |
| §3 technique/measurement | term entries containing technique keywords |
| §4 numeric values | per-term relation dicts |
| §5 zero-measurement | filename keys |
| §6 aliases | term entries; adjacent label pairs |
| §7 relations | per-term relation dicts (not json2kg-deduplicated edges) |
| §8 summary | term entries; filename keys; per-term histogram increments |

### 3.3 D20 interaction (schema semantics)

- corpus_qa §3 describes `ExperimentalTechnique` terms that have paper evidence as "candidates for reclassification".
- corpus_qa §5 counts papers lacking `MEASUREMENT_TYPES` (`:36-39`). Three of those five names (`MeasurementInstance`, `ScatteringMeasurement`, `DiffractionMeasurement`) are not classes in any schema. On baseline terms files §5 reports **145/145** and **25/25** papers as "zero-measurement".
- Under D20 (no measurement/technique split; `RSoXSMeasurement is_a ExperimentalTechnique` preserved), these outputs are legacy diagnostics. A Tool must not present them as defects or recommend reclassification. See `docs/adr/schema-ownership.md`.

## 4. Destructive and repair paths (never agent Tools in this migration)

- `repair_corpus.py` (OP09). Writes a repaired copy and silently overwrites an earlier repaired output. It introduces values outside every schema: categories `Instrument` and `Detector` (`:120-123`), and predicates `characterized_by` and `simulates` (`:66-70`). CLI-only, with separate approval.
- `ingest_rsoxs_v3.py` (OP10) overwrites `matkg_rsoxs_v3.json` in a different JSON shape (`nodes`/`edges`). Its `RSOXS_SLOTS` list includes three non-schema slots.
- `build_v7_kg.py` (OP11) and `ingest_bl1101_v7.py` (OP12) both overwrite `storage/kg/matkg_bl1101_v7.json`, so the last one to run wins.
- `reimport_merged_kg.sh` (OP28) overwrites `matkg_with_code.json` and calls `splash_reimport(allow_wipe=True)`. Splash is deprecated (D03/D16).
- `wipe_splash_db.sh` (OP29) deletes the Splash SQLite database after a typed confirmation.
- Repo-file side effects of otherwise analytic scripts:
  - `list_missing_rsoxs_papers.py` overwrites the **tracked** `missing_rsoxs_papers.txt` by default.
  - `promote_rsoxs_snapshot.py` overwrites the tracked `storage/fixtures/rsoxs_snapshot_promote.json`.
  - `analyze_kgs.py` and `get_pdf_years.py` write CSV/JSON into the current directory.

## 5. Reconciliation with `companion/knowledge_operations_source_map.json`

- **Matches.** All 23 symbol line ranges match the baseline AST, and the file hashes match `f2w_baseline_inventory.json`.
- **D-1.** In the baseline inventory, `corpus_qa.py`, `repair_corpus.py`, `harvest_rsoxs.py` and `run.py` carry `retain_baseline_seam`, not `retain_cli_facade_T19A_review`. The inventory also omits the 15 `scripts/*.sh` files, `slurm_scripts/run_ollama.sh` and `scripts/.env.example`. It records `parse_terms.py` as `has_main_guard=false`, although the file has a guard at line 35.
- **D-2/D-3.** KNOWLEDGE_OPERATIONS asks to "preserve" source/method/uncertainty for get_pdf_years, but `guess_year` returns only a year, so this would be new output. The non-recursive scan is not documented.
- **D-4.** The histogram caveat is confirmed empirically (see 3.1, item 2).
- **D-5 to D-9.** Not in the source map:
  - corpus_qa §3/§5 vs D20;
  - analyze_kgs skipped checkpoints and filename denominators;
  - the Tiled 800 cap;
  - repair non-schema values, the v3 shape and the duplicate v7 writers;
  - list_missing overwriting a tracked file.
- **D-10.** `summarize_extracted_terms` does not call `_eligible_pages`. It reimplements eligibility (`:148-161`) with a different rule for `extraction_state=full`: the eligible pages come from the terms themselves rather than every page.
- **D-11.** The facade list in KNOWLEDGE_OPERATIONS section 2 omits `repair_corpus.py`, which is CLI-only here.

## 6. Tests available for parity

- **Existing:** `tests/test_paper_evidence_agent.py:170,216` (OP01), `tests/test_list_missing_rsoxs_papers.py` (OP04), `test_tiled_graph.py`, `test_tiled_sim.py`, `test_hybrid_rag.py`, `test_harvest_*.py`, `test_download_pdfs.py`, `test_match_als_zips.py`, `test_paper_finder.py`, `test_bl1101_ingest.py`, `test_wipe_splash_db_script.py`.
- **No tests:** `corpus_qa.py`, `analyze_kgs.py`, `get_pdf_years.py`, `repair_corpus.py`. T19B/T19C must capture CLI baselines for these first, including the repeated-paper fixture KNOWLEDGE_OPERATIONS requires for the histogram.

## 7. Open owner questions

1. **OQ1 (histogram):** keep the legacy CLI histogram and add a versioned distinct-paper histogram, or correct the CLI?
2. **OQ2 (corpus_qa §3/§5):** exclude them from the Tool output, or include them with a D20 caveat?
3. **OQ3 ("papers" unit):** count filename keys (current), WorkVersions, or distinct Works (`harvest_rsoxs.works_same_identity` / `collapse_near_duplicates`)?
4. **OQ4 (Tiled counts):** are experiment-identity counts in scope? If yes, require complete paging or an explicit `partial` status.
5. **OQ5 (retirement):** retire or keep `ingest_rsoxs_v3.py`, `build_v7_kg.py`, `ingest_bl1101_v7.py`, and the stray `scripts/app/modules/kg_rag_api.py`?
6. **OQ6 (list_missing output):** should the CLI keep writing the repo-root file while the Tool writes only artifacts?
7. **OQ7 (analyze_kgs skips):** should the legacy CLI keep skipping unrecognized checkpoint names silently?

## 8. T19C implementation record

T19C implements the graph and corpus operations after H3 (K1 distinct Works; K2 versioned histogram; K3 §3/§5 excluded; K4-K7 deferred). Code: `app/modules/operations/{graph,corpus,reports}.py`. Tests: `tests/operations/test_{operation_reports,work_identity,corpus_audit,graph_operations}.py`, `tests/graph/test_graph_operations_tiled.py`. Binary oracles OPS-01..04 bind these tests (all cases stay `proposed`).

| Tool | Method | Counting unit | Complete / partial | Backends |
| --- | --- | --- | --- | --- |
| `kg_compute_statistics` (OP03, RT-03) | `kg-statistics/1` | distinct node ids; distinct (s,p,o) triples; distinct Works (`work-identity/1`) | `ScanControl` bound/deadline -> `partial` + `ToolStatus.PARTIAL` | JSON (snapshot), Tiled (live, unisolated) |
| `kg_compare_snapshots` (OP03, RT-07) | `kg-growth/1` | as above; legacy growth with distinct-Work denominators | unread graphs listed; partial | JSON, Tiled |
| `kg_paper_subgraph` (K-10, RT-59) | `kg-paper-subgraph/1` | node ids citing one Work; internal/boundary triples | absent paper after a partial scan is `UNAVAILABLE`, not `NOT_FOUND` | JSON, Tiled |
| `kg_recent_entities` (K-11, RT-08) | `kg-recent/1` | node ids created after `since` | as above | Tiled (`createdAt`); JSON: `UNSUPPORTED_CAPABILITY` |
| `curate_audit_corpus` (OP02) | `corpus-audit/1` | per section, legacy units kept; distinct Works; `papers_per_year/2` | term-entry bound -> partial | extracted-terms file via injected resolver |

Legacy CLIs (`corpus_qa.py`, `analyze_kgs.py`) are byte-identical to the T19A baseline; parity tests compare Tool figures against them on synthetic and committed data. Not done in T19C: OP04 (`list_missing_rsoxs_papers`) and OP05 (`get_pdf_years`) depend on script-only PubTemp parsing and on PyMuPDF (E2); O-01, O-10, O-12, O-13, O-14 are literature-store/network/PDF-scan operator workflows outside GraphPort.

## 9. T19D scale and correctness evidence (for H4; the gate is not claimed)

Tests: `tests/operations/test_{scale_population,scale_completeness,scale_tiled,repeated_works,figure_evidence,no_code_generation,corpus_near_duplicates}.py`; populations in `tests/operations/scale_population.py` (ground truth by construction and set arithmetic, never by the operations code). OPS-01..04 bind them; every case stays `proposed`.

| Population | Size | x 1M-token context (4 chars/token) |
| --- | --- | --- |
| JSON graph | 60 000 entities, 190 900 edge records, 2 368 Works over 4 136 paper keys | ~12 |
| extracted-terms corpus | 60 000 term entries, 1 500 Works over 2 000 keys | ~11 |
| session terms | 120 000 records, 41 563 page-grounded for the active paper | ~2.5 |
| live Tiled 0.2.18 | 15 000 + 6 000 entities, 36 078 links, 600 FORGE rows | >2 |

Measured (Apple laptop, one process, no model, no socket): `kg_compute_statistics` 2.4 s (JSON) / 5.5 s (Tiled, complete paging); `kg_paper_subgraph` 2.2 s / 5.0 s; `kg_compare_snapshots` 4.4 s (2 x ~60k) / 10.1 s (Tiled); `kg_recent_entities` 5.1 s (Tiled); `curate_audit_corpus` 7.4-8.1 s; `lit_summarize_extracted_terms` 0.9-1.1 s. Resident memory added per operation in a fresh process: 277-430 MiB. Every inline result is at most 7000 characters.

Change made for scale: `curate_audit_corpus` §1 arXiv x journal near-duplicate check was O(arXiv x journal) at ~0.5 ms per pair (over 4 minutes for 3 000 keys); it now prunes by exact lower bounds and a banded edit distance (same pairs, order and scores as `corpus_qa`, parity-tested). It is still quadratic in pairs (~30 us/pair on adversarial titles).

Open: work-identity/1 does not merge a renamed copy with no DOI, arXiv id or title (no content-hash rule; disclosed as `works_identified_by_key_only`); operations count figure/table-derived records by their existing citation fields (`source_papers`, pages, snippets) and report no per-modality breakdown; a T09 EvidenceRef whose Work is cited only through `evidence[].work_id` is not counted. Real-paper review of K1 waits for the H3 B4 pilot labels.

## 10. T19E access closure: legacy CLI to new entry points (agent, API, SDK, CLI)

T19E closes agent, `/v1`, SDK and CLI access to the five T19C operations through one bundle, `app/modules/operations/knowledge.py` (`KnowledgeOperations`), with no new metric, population rule or model call. The frontend part of T19E and the T15 consumer path are not covered here. Evidence: `docs/migration/reviews/H6/operations_integration_evidence.md`; tests `tests/operations/test_knowledge_surfaces.py`, `tests/api_v1/test_v1_operations.py`, `tests/graph/test_knowledge_surfaces_tiled.py`.

**"One implementation with CLI wrappers".** The implementation is the T19C suite; the CLI wrappers are the `python -m app.api.cli ops ...` subcommands below. The legacy scripts are **not** modified (byte-identical to the T19A baseline, asserted by `test_cli_is_unchanged` and `test_legacy_block_matches_analyze_kgs_on_committed_graphs`): they stay as the parity oracle until T17B decides their retirement. Their output format differs from the new entry points (the new ones return the typed `OperationReport`, with counting units, coverage and distinct-Work denominators; the scripts print the legacy figures, including the overcounting histogram and the excluded §3/§5).

| Legacy CLI | Legacy invocation | New CLI wrapper | Same computation also at | Differences the caller sees |
| --- | --- | --- | --- | --- |
| `scripts/analyze_kgs.py` (OP03, structural metrics of each file in a hard-coded list) | `python scripts/analyze_kgs.py` (edits the file list in `__main__`) | `python -m app.api.cli --root R [--project-root P] ops statistics --graph <graph_id>` (one graph; `--graph-file` names files outside `storage/kg`) | Tool `kg_compute_statistics`; `GET /v1/operations/statistics?graph_id=`; `F2WClient.graph_statistics` | `legacy` block reproduces `calculate_structural_metrics`; adds distinct Works, duplicate/dangling/incomplete edge counts, `coverage`; graph named by id, not path |
| `scripts/analyze_kgs.py` (OP03, growth series) | same | `ops compare --graph A --graph B [...]` (2-12 graphs, in order) | Tool `kg_compare_snapshots`; `GET /v1/operations/compare?graph_ids=A&graph_ids=B`; `F2WClient.compare_graphs` | paper denominator is distinct Works (H3 K1), not the file name; unread graphs listed when partial |
| - (K-10, no script) | - | `ops paper-subgraph --graph G --paper <doi, arXiv id or key> [--max-nodes N] [--max-edges M]` | Tool `kg_paper_subgraph`; `GET /v1/operations/paper-subgraph`; `F2WClient.paper_subgraph` | new |
| - (K-11, no script) | - | `ops recent --graph G [--since T] [--limit N]` (Tiled graphs only; the CLI builds JSON ports, so this exits 2 `unsupported_capability` there and is served by the API/SDK over a Tiled port) | Tool `kg_recent_entities`; `GET /v1/operations/recent`; `F2WClient.recent_entities` | new |
| `scripts/corpus_qa.py` (OP02) | `python scripts/corpus_qa.py <terms.json> [--output report.json]` | `ops corpus-audit --corpus <name> [--section S ...]` (`<name>` is a file stem under `<project-root>/storage/terminology`) | Tool `curate_audit_corpus`; `GET /v1/operations/corpus-audit?corpus=`; `F2WClient.audit_corpus` | §3 and §5 excluded with reason (H3 K3); `papers_per_year/2` counts distinct Works beside the legacy histogram; term-entry bound makes the result `partial` |
| (no legacy equivalent: long scans) | - | `ops submit --operation <tool> --args '<json>' [--scope C ...]`, then `ops result <job_id>` | `POST /v1/operations/jobs` (`ops.run`), `GET /v1/operations/jobs/{id}`; `F2WClient.submit_knowledge_operation`, `knowledge_job_result` | the `knowledge_operation` job (T19C `register_operation_jobs`) scans the whole population with cancellation checkpoints; the stage data is the bounded report, the private artifact the full one |

Agent access: `runtime_bridge.bootstrap.build_read_registry(knowledge=...)` registers the five Tools and the `analyze_knowledge_graph` / `audit_literature_corpus` skills; `F2WApplication(knowledge=...)` and, in `F2W_RUNTIME=langgraph` mode, the legacy service's application (over the active runtime's JSON graph selection, T25D) carry them. The UI QA turn (`answer_via_supervisor`) still offers only `answer_from_knowledge` (its contract is the read verdict), so operation results reach a chat only through `F2WApplication.delegate` (SDK/`LiteratureAgent`) until the frontend part of T19E.

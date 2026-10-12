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

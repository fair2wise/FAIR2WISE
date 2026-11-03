# Paper job handlers: port notes (H8 review packet)

Status: handlers implemented in `app/modules/jobs/paper_handlers.py` (branch `forge_merge-T-handlers`);
tests use fakes only (`tests/jobs/test_paper_handlers.py`, `tests/api_v1/test_v1_paper_handlers.py`).
Owner decision 2026-11-03: the FORGE code is the donor; paper job kinds require `jobs.admin` only.

Donor: `als_knowledge_agent/graph/enrichment_agent.py` (`run_enrichment`, `run_topic_sweep`, `run_pdf_parse`),
`server.py` background runners. Routes RT-27..RT-32 (and J-11 for the PDF sweep).

## What each handler does

| Kind | Unit | Output (all proposals; nothing writes a graph) |
|---|---|---|
| `enrichment` | named material, similarity row, sample group, condition bin, material+technique search, citation set | `dataset_material_links`, `chemical_similarity`, `cross_beamline`, `similar_conditions`, `publication_links`, `citations` |
| `topic_sweep` | topic (cap 50 papers, hard cap 200; optional `year_from`) | `publication` (papers, authors, `authored_by`), `citations` |
| `pdf_parse` | paper (most cited first, DOI required, `paper_ids`/`limit`/`force`) | `paper_parse` (sections, figures, `has_section`/`has_figure`, licence status), `citations` from reference lists |

A proposal is a `graph_proposal` case in the curation review queue (`needs_review`, `curation.approve` decides;
`CurationStore.enqueue_for_review`). Identity is `(corpus, subject, kind:key)`; the content is the case evidence, so
re-running is idempotent and changed content reopens a decided case. Handlers are bound by trusted wiring
(`build_paper_handlers` + `register_paper_jobs`); a kind whose ports are not all configured is not registered and
submission still answers `unsupported_capability`.

## Donor behaviour deliberately not ported

| Donor part | Why | Replacement or gap |
|---|---|---|
| Graph upserts (`store.upsert_node/edge`, `kg_writer`) | A handler output is a proposal, never a graph write | Curation proposals |
| Process-global stop flags, `asyncio` runners, SSE event dicts | Replaced by the job lifecycle | Cancel between units, counters as progress, events |
| `_start_pipeline_run` / `_finish_pipeline_run` run entities | Provenance of a run is the job ledger | Job id travels with the worker context |
| Incremental skip via `get_scanned_dataset_ids`, `remember_node_path` | Needs the graph store | Settled units skip on resume; proposals are idempotent |
| Stage 5 vision patterns (Ollama vision) | Model call, no F2W vision port for this | Not offered; owner decision if wanted |
| Stage 4b `simulate_from_papers` (extraction + simulation service) | Model call plus an external simulation service (`paper_simulation`) | Not offered; the extract job kind covers extraction |
| Stage 7 `llm_*` and 8 `web_enrichment` | Donor "Phase 3 scaffolds": placeholders or no-ops whose results were discarded | None needed |
| `run_pdf_parse` `extract_entities`/`model` (LLM entity extraction, `kg_writer.link_section_entities`) | Paid or local model call, graph write | Use the `extract` job kind on the parsed paper |
| `oa_only` and `_is_open_access` (OpenAlex re-resolution) | The hardened acquirer is open-access only by construction and reports `no_oa_pdf` | Failure reason `no_oa_pdf` in `failure_reasons` |
| `_already_parsed` / `_current_parser` / `journal.checkpoint` | Parser identity and caching moved into `ParseService` (parser key = name, version, config hash) | `force` re-parses; otherwise a cached parse is reused |
| SMILES-derived material nodes (`_upsert_smiles_material`, `candidate_term` nodes) | Minting nodes from free text is a graph write and the vocabulary gate is the F2W rule | Only names/SMILES the vocabulary knows are proposed; others are counted (`material_names_unmatched`) |
| Tanimoto without RDKit (donor compared the first dot-fragment) | A silent heuristic that reports similarity it did not compute | No similarity function configured: stage skipped and reported (`chemical_similarity: skipped...`) |
| Author `paper_count` read-modify-write | Not expressible as an idempotent proposal | Not proposed; recomputable from `authored_by` edges |
| Section text in graph properties | Large text belongs in the parse store | Proposal carries name, page, level, length, SHA-256 and the parse `document_ref` |
| OpenAlex SUPPLEMENT (`openalex_supplement.json`) for citation targets | Replaced by A-01 (`enrich_corpus_rows`) | Citation proposals use corpus rows with `referenced_works` |

## Gaps that need an owner decision or a follow-up task

1. **Applying accepted proposals.** `graph_proposal` cases can be reviewed and decided, but nothing turns an accepted
   proposal into a `ChangePlan`/graph write yet. Needs an apply adapter (proposal -> `GraphPort.apply_plan`) and the
   node-id mapping from the donor-style ids used in proposals (`publication:<openalex>`, `author:<id>`,
   `material:<slug>`, md5-of-path dataset ids) to F2W graph ids.
2. **Production port adapters.** Shipped: `OpenAlexWorkSearch` (over `OpenAlexClient`), `CurationProposalSink`.
   Not shipped, because they need the deployment's live services: the Tiled `DatasetCatalog` (donor walked the catalog
   with the process Tiled client; reading it here would contact the ALS Tiled host), a `CorpusPublications` adapter
   over the F2W graph or ingested-paper index, and the wiring that builds the vocabulary matcher, `PaperAcquirer`
   and `ParseService` for the worker process. Until the wiring registers handlers, the routes keep answering
   `unsupported_capability`.
3. **Corpus selection for enrichment.** The F2W `entity_ids` parameter is passed to the catalog port as a path filter;
   the donor's `paths` scoping semantics (refuse rather than sweep everything when no path resolves) are the
   adapter's responsibility.
4. **Proposal volume.** Pair-wise edges are grouped (per material, sample, condition bin; at most 300 pairs per bin as
   in the donor), but a large catalog still yields thousands of review cases. Owner decision: an auto-approval policy
   for low-risk kinds (for example `similar_conditions`) versus review of every case.
5. **No spend.** All three kinds run without a paid model, so the budget ledger is not involved; adding the
   deferred model stages above would need `BudgetLedger` admission and a cost approval first.

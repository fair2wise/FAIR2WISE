# Knowledge Operations - integrated in migration v6

**Status:** integrated specification for T19A-T19E in Draft v6. Proposed targets remain design; no implementation, approval or runtime result is claimed.

## 1. Decision

Make reusable capabilities currently hidden in F2W scripts available as named, typed agent actions. Add an explicit **Knowledge Operations** responsibility under the existing proposed `app/modules/operations/` package. This includes paper summaries, corpus inventories, graph statistics, evidence-coverage audits, snapshot comparisons, and code-backed questions whose relevant population cannot fit in an LLM context.

The organizing rule is: **use code to inspect and compute over the authorized data; give the model bounded results and evidence to explain.** A natural-language question does not imply that the answer must be calculated by an LLM or inferred from a top-k retrieval sample.

This remains literature/KG scope. It does not authorize porting FORGE detector analysis, simulation, fitting, or experimental reduction. Existing F2W behaviors outside that scope remain subject to v3's compatibility requirements.

## 2. Architectural placement

`operations/` is a family of application-oriented services, not a new runtime, a second tool registry, or another mandatory tier in every request. Existing primitives stay with their domain owners; operations compose them when a task spans domains. The application facade still owns request routing, authorization, and lifecycle.

```text
app/
  public.py                         # shared public client / agent factories
  api/                              # FastAPI and existing-UI adapters
  modules/
    application.py                  # shared use cases, policy and routing
    contracts/
      operation_results.py          # proposed additions to shared contracts
    agent_runtime/                  # existing planned runtime / registry
    tools/
      literature.py                 # lit_summarize_paper, lit_summarize_corpus
      graph.py                      # kg_compute_statistics, kg_aggregate, kg_compare_snapshots
      curation.py                   # curate_audit_corpus
    operations/                     # NEWLY SPECIFIED responsibility
      __init__.py
      papers.py                     # bounded paper and multi-paper summaries
      corpus.py                     # inventory, completeness, missing-source reports
      graph.py                      # population queries, statistics, snapshot comparisons
      reports.py                    # typed report assembly; artifact registration
    literature/                     # source selection, bibliographic identity, parsing
    graph/                          # query primitives, graph metrics and backend adapters
    retrieval/                      # evidence lookup and context construction
    curation/                       # quality checks, review and repair plans
    artifacts/                      # report persistence and access-scoped references
    jobs/                           # bounded, resumable execution for expensive operations
    llm_adapters.py                  # existing F2W inference classes only
scripts/
  analyze_kgs.py                    # CLI wrapper over the same shared implementation
  corpus_qa.py                      # CLI wrapper; no second copy of checks
  list_missing_rsoxs_papers.py       # CLI wrapper with preserved source constraints
  get_pdf_years.py                  # CLI wrapper with provenance-aware year estimates
```

All new filenames and tool names in the target layout are proposals. Keep implementation physically in `app/modules/`; use the canonical import arrangement approved under v4 H1. Do not create empty modules solely to match this tree.

An example call path is `query_knowledge_graph` guidance -> `kg_compute_statistics` Tool -> `operations.graph` service -> existing graph primitives / GraphPort -> required Tiled Graph or an explicitly selected frozen JSON artifact. A simple direct SDK call reaches the same application service without invoking a planner.

## 3. Two different questions, two execution paths

**Evidence retrieval:** "What does this paper say about orientation?" Retrieve relevant eligible source passages/figures and answer under F2W's existing QA constraints.

**Population computation:** "How many distinct papers in this selected corpus report polarization, grouped by material?" Resolve corpus and source revisions, define the counting population, inspect all relevant records with code, deduplicate by the approved Work identity policy, compute the grouped result, and return counts with coverage and supporting references.

The population computation must not count only the top retrieval hits and call that a corpus total. The supervisor can combine both paths: compute the distribution first, then retrieve passages that illustrate or explain it.

A further distinction: an exact count of *recorded polarization fields* does not establish how many papers actually discuss polarization. If extraction coverage is incomplete, answer the recorded-data question and disclose missing or unprocessed sources. Do not silently convert an extraction audit into a claim about the full scientific literature.

## 4. Starting points verified in the uploaded F2W source

| Existing source | Proposed reuse | Important qualification |
| --- | --- | --- |
| `app/modules/f2w_agent/paper_evidence_agent.py:summarize_extracted_terms` | `operations/papers.py`; `lit_summarize_extracted_terms` or a typed mode of `lit_summarize_paper` | Already generates a deterministic, page-cited compact summary of extracted terms. It is not equivalent to a comprehensive abstract of the original paper. Preserve eligible pages, displayed/omitted counts, and citation checks. |
| Same file: `PaperEvidenceAgent`, `_eligible_pages`, `_citation_errors` | Source-bounded paper answering/summary service and old agent compatibility wrapper | Retain current F2W model configuration and page/citation eligibility rules. LLM prose must not weaken the source constraints. |
| `scripts/analyze_kgs.py:calculate_structural_metrics`, `calculate_growth_metrics` | Shared graph metrics; `kg_compute_statistics`, `kg_compare_snapshots` | Existing code computes graph structure and checkpoint growth. Structural counts are not measures of scientific truth. Characterize empty inputs, dangling endpoints, duplicate relations, denominators and schema/identity changes before exposing results. |
| `scripts/analyze_kgs.py:evaluate_kgs_independent` | Report orchestration in `operations/graph.py`; old script remains a CLI | Currently ties checkpoint metadata to selected filename conventions and prints/writes files. Replace implicit file parsing with explicit metadata in the new API, while preserving the legacy CLI interpretation during migration. |
| `scripts/corpus_qa.py:audit_*` | Shared curation checks plus `operations/corpus.py`; `curate_audit_corpus` | Keep existing audit categories. Reusable checks must not live only in `evals/`: agents, CLI and evaluation harness should call the same production checks. Diagnostics remain separate from repair/apply. |
| `scripts/list_missing_rsoxs_papers.py:collect_missing_citations`, `verify_written_against_rtf` | `operations/corpus.py`; `lit_list_missing_papers` | Existing behavior is specifically manifest/PubTemp-RTF constrained. Preserve matching and citation verification; do not relabel it as unrestricted corpus discovery without an explicit extension. |
| `scripts/get_pdf_years.py:guess_year`, `scan_folder` | Provenance-aware date inspection; `lit_inspect_publication_dates` | Guesses from arXiv filename, PDF metadata, then text. These are estimates, not authoritative journal publication years. Preserve source/method and uncertainty. |
| `scripts/repair_corpus.py:repair`, `step_merge_near_duplicates` | Existing v3 repair/curation services, referenced by reports | Keep repairs out of the read-only analytics path. An audit may propose a repair; it does not approve or apply one. |

### Source-level issue to capture, not silently hide

In `scripts/corpus_qa.py:audit_summary_stats`, the year histogram increments for each term's `source_metadata` entry (lines 758-771). No global DOI/year deduplication is applied to that histogram, despite the printed label describing unique DOI/year combinations. It can therefore count a repeatedly cited paper more than once. Before calling this a distinct-paper histogram, add a repeated-paper fixture, preserve the historical CLI baseline explicitly, and obtain approval for a versioned correction. Also distinguish raw extracted term counts from canonical entity counts.

Similarly, `analyze_kgs.py:load_kg` loads an entire JSON file and builds entity/relation sets. It is a useful source of metric definitions but is not by itself a scalable Tiled implementation. Port the behavior behind typed data access and bounded execution rather than presenting the existing script as already solving large-graph execution.

## 5. Proposed capabilities and Skill ownership

Reuse existing domain-prefixed Tool names and the single planned registry. Do not create an all-purpose `run_script`, `run_python`, or `operations_execute` escape hatch.

| Task-level Skill | Proposed Tools | Task intent |
| --- | --- | --- |
| `summarize_literature` (new) | `lit_summarize_paper`, `lit_summarize_corpus` | Produce source-grounded summaries with explicit scope/coverage; reuse the deterministic extracted-term summary as one mode. |
| `query_knowledge_graph` (extend) | `kg_compute_statistics`, `kg_aggregate`, `kg_compare_snapshots` | Counts, grouped statistics, structural measures and changes that require code over the selected population. |
| `audit_literature_corpus` (new or scoped guidance shared with repair) | `curate_audit_corpus`, `lit_list_missing_papers`, `lit_inspect_publication_dates` | Diagnose coverage and source availability without modifying scientific knowledge. |
| `answer_from_knowledge` (extend guidance) | Reuse approved retrieval and computation Tools | Route exhaustive/counting questions to computation, then interpret the bounded results under the existing F2W QA rules. |

`kg_aggregate` accepts a constrained typed specification: permitted entity/record types, filters, grouping dimensions, aggregation operators, missing-value policy, and deduplication unit. It does not accept arbitrary Python, shell commands, unrestricted SQL/GraphQL, or caller-provided file paths as authority. Expose a narrower Tool when intent or permissions differ substantially.

Graph topology operations such as connected components are additional algorithms, not automatically provided by the inspected structural-metrics script. Add them only with explicit implementation and resource tests.

## 6. The result contract: full computation, bounded context

Extend the existing ToolResult/JobHandle/evidence contracts; do not create a competing result stack. Proposed operation metadata:

- Operation ID/version, validated parameters, principal/corpus scope, start/end time, and source snapshot/index/manifest revisions.
- Population definition and counting unit: raw records, mentions, canonical entities, WorkVersions, or distinct Works.
- Input coverage: processed and expected counts when known, unresolved identities, unavailable/failed sources, and whether iteration completed. Unknown total is not zero.
- `complete`, `partial`, `estimated`, `awaiting_review`, `failed`, or existing equivalent statuses; record exactness separately from input coverage.
- Computed metrics and a bounded display preview; record display truncation independently of scan completeness.
- Access-scoped artifact references for complete results, large tables, membership sets, and a versioned computation manifest.
- Evidence/provenance references sufficient to drill from an aggregate to its contributing records and then to original source passages/figures where available.
- Explicit warnings about schema changes, missing evidence, year estimates, insufficient metadata, scope limits and unsupported operations.

An aggregate citation may point to a computation report and its snapshot membership, not just one illustrative paper. The model may explain a computed result, but it must not recalculate totals from examples, treat a displayed preview as the entire dataset, or claim a causal trend from a structural count.

A read-only scientific operation can still write a report artifact or incur model cost. Declare those effects under the existing policy. No KG publication, vocabulary promotion, or silent corpus ingestion follows from answering an analytics question.

## 7. Large-data execution

Push filtering/aggregation into Tiled only when the verified deployed API provides the needed capability. Otherwise use complete paged reads of authorized literature records or a verified frozen export, with streaming reducers or disk-backed intermediate state appropriate to the operation. Do not assume native Tiled aggregation, snapshot isolation, or transactions that were not verified.

Exact distinct counts may require bounded disk-backed sets; a top-k display reducer must run after exhaustive counting. If a live read cannot give a coherent revision, materialize a scoped snapshot or report that limitation rather than claim snapshot-exact totals. Cross-graph queries keep graph identity and an explicit cross-corpus deduplication policy; do not union ontologies or join only on labels.

Reuse `jobs/` for long scans, bulk summarization and report generation. Provide cancellation, deadline, memory/CPU/model budgets, resumable checkpoints where safe, and durable progress. Return a JobHandle quickly through existing frontend event adapters. Never block the API event loop with full-graph processing.

For many-paper summarization, process bounded units with existing F2W model classes, retain per-source evidence and coverage, then synthesize over those structured intermediates. Cache by source version, requested task, model/prompt/schema and operation revision. A hierarchy of summaries must retain links to original evidence; an earlier LLM summary is not an independent source.

## 8. Script promotion rules

1. Inventory all script-like entrypoints, not only the `scripts/` directory. Classify each as domain capability, operator maintenance, evaluation, development-only, destructive, or out-of-scope. A file existing in the repository is not sufficient authority to expose it to an agent.
2. Capture existing behavior, parameters, assumptions, output schemas, printed diagnostics, side effects and tests.
3. Extract callable computation from CLI parsing, printing, filesystem discovery and process startup. Reuse existing domain primitives rather than copying algorithms into `operations/`.
4. Return typed results. Resolve authorized corpus/source/artifact IDs through injected services; the model does not choose unrestricted paths, graph namespaces or credentials.
5. Register thin Tools with descriptions, when-to-use/when-not-to-use, input/output schema, effects, cost and evidence requirements. Map them to Skills and profile capabilities.
6. Keep existing CLI names/options as wrappers around the same service. Keep the F2W UI and current QA/model policies intact. FORGE uses the same public client/agents or FastAPI.
7. Test service/CLI/Tool/API parity before consolidating legacy implementation. Any behavior correction, including counting-unit changes, gets its own reviewed change and report version.

Known reviewed operations are the initial execution model. Generating and running arbitrary new Python to answer an unseen question is a separate future design requiring an isolated execution environment, no ambient credentials, bounded resources and controlled artifact/network access. It is not implicitly enabled by this addition. Extracted paper code also remains non-executable evidence unless separately authorized.

## 9. Proposed migration work packet: T19

T19A-T19E are now included in v4 `task_graph.json` and inherit the plan's `forge_merge` integration branch, sole owner approval, source locks, UI compatibility, Tiled requirement, and preserved F2W LLM/QA paths.

| Subtask | Dependencies and parallel work | Review / stopping point |
| --- | --- | --- |
| T19A - Inventory script capabilities and population semantics | Extend T00/T17; can run alongside baseline/UI capture. Inventory `summarize_extracted_terms` plus all eligible scripts and tests. | Present source-to-service-to-Tool mappings, effect classification and scope at H1. No blind script registration. |
| T19B - Contracts and unchanged first operation | Shared H1 contracts and T02/T03 runtime seam. Add result coverage/counting-unit contract. Wrap the existing deterministic term summary before changing its algorithm. | Include CLI/Tool/old-frontend demonstration at H2. Do not change eligible pages, citations or summarization policy. |
| T19C - Graph/corpus computation services | After H2; can run in parallel with T05/T06/T07/T09 using frozen GraphPort/document contracts. Real Tiled implementation depends on T08A; long jobs integrate with T10. | H3 approves metric definitions, deduplication, schema-specific criteria and any correction to legacy audit behavior. No auto-repair. |
| T19D - End-to-end scale and correctness evaluation | With T11 and T18C. Test a population much larger than the prompt budget on disposable Tiled plus frozen JSON; output remains bounded. | H4 verifies text/figure-informed reports, complete-versus-partial behavior and no double-counting. This supplements, not replaces, the existing text-and-figure pilot. |
| T19E - Consumer and compatibility closure | With T14/T15/T17. FORGE invokes the same direct/agent/API operations; CLI and frozen F2W UI still work. | H6 requires all in-scope script capabilities mapped to surviving implementations or owner-approved exceptions. H7 approves legacy cleanup. |

Required tests include repeated Work/source references; empty graphs; dangling endpoints; duplicate relations; unknown numeric values; incompatible units; only a retrieved subset available; paged-read interruption; concurrent source revision changes; cross-corpus identity collisions; denied corpus/artifacts; cancelled jobs; size-limited previews; and source-page eligibility for summaries. Use fake inference for deterministic parity and approved budgets for live summary evaluation. Test that code computes the answer from the full selected scope without serializing that scope into the planner prompt.

## 10. Acceptance statement

The migration is not complete when miscellaneous scripts are merely callable through subprocesses. It is complete when their useful behavior lives in tested reusable services; the agent can choose the corresponding typed actions; large-scope questions are computed outside model context; results carry scope, completeness, units and evidence; and existing CLI/UI consumers remain functional.

**One-line architecture description:** F2W supports both retrieval-backed answering and code-backed knowledge operations, with the same Skills/Tools, domain services, model policies, and evidence contracts.

## 11. Verification references

The following symbol locations were rechecked using Python AST parsing against the user-uploaded F2W archive. No source module was imported or executed. No runtime tests were run. Proposed APIs and organization are not claims about existing implementation.

Archive: `FAIR2WISE-rsoxs.zip`

Archive-recorded commit: `222b1e7b130567dbf4a3242697ac2b465794a38d` (not independently fetched).

Archive SHA-256: `d3e9d821e7878211f08399c3f69e7d9cf0f55f8a7f17397e737fa3a4292077a6`.

| Source file | Existing symbol | Source lines |
| --- | --- | --- |
| `app/modules/f2w_agent/paper_evidence_agent.py` | `summarize_extracted_terms` | 122-292 |
| `app/modules/f2w_agent/paper_evidence_agent.py` | `PaperEvidenceAgent` | 295-458 |
| `app/modules/f2w_agent/paper_evidence_agent.py` | `_eligible_pages` | 54-80 |
| `app/modules/f2w_agent/paper_evidence_agent.py` | `_citation_errors` | 88-119 |
| `scripts/analyze_kgs.py` | `load_kg` | 9-17 |
| `scripts/analyze_kgs.py` | `calculate_structural_metrics` | 19-48 |
| `scripts/analyze_kgs.py` | `calculate_growth_metrics` | 50-75 |
| `scripts/analyze_kgs.py` | `evaluate_kgs_independent` | 78-234 |
| `scripts/corpus_qa.py` | `audit` | 814-912 |
| `scripts/corpus_qa.py` | `audit_summary_stats` | 738-799 |
| `scripts/corpus_qa.py` | `audit_duplicate_works` | 173-278 |
| `scripts/corpus_qa.py` | `audit_stubs` | 281-343 |
| `scripts/corpus_qa.py` | `audit_technique_measurement` | 346-410 |
| `scripts/corpus_qa.py` | `audit_numeric_values` | 413-515 |
| `scripts/corpus_qa.py` | `audit_zero_measurement_papers` | 518-565 |
| `scripts/corpus_qa.py` | `audit_aliases` | 568-634 |
| `scripts/corpus_qa.py` | `audit_relations` | 637-735 |
| `scripts/list_missing_rsoxs_papers.py` | `collect_missing_citations` | 99-204 |
| `scripts/list_missing_rsoxs_papers.py` | `verify_written_against_rtf` | 219-277 |
| `scripts/get_pdf_years.py` | `guess_year` | 102-113 |
| `scripts/get_pdf_years.py` | `scan_folder` | 116-130 |
| `scripts/repair_corpus.py` | `repair` | 379-478 |
| `scripts/repair_corpus.py` | `step_merge_near_duplicates` | 248-363 |

## v4 refinements

All operations use frontend-selected F2W inference settings and shared provider admission. Complete artifacts are internal by default; user-facing exports contain only approved computed results, never raw paper bodies/crops or reconstructable source dumps. Source/schema versions and completeness are part of each result. T19D/H4 and T19E/H6 use the mandatory binary suite. Metric definitions that alter historical CLI results still require owner review, and current schema meanings are preserved; no measurement/technique split is included.

## Draft v6 integration requirements

Versioned JSON remains the KG creation artifact; Tiled 0.2.19 consumes it through the separate adapter in JSON_KG_PUBLICATION_SPEC.md. Preserve current scientific schema logic and inheritance; the measurement/technique split is not an acceptance requirement. Relevant exact cited quotations are allowed while complete source papers and original crops remain internal. Any earlier conditional wording about O01/O02/O04 in source-derived commentary is superseded by OWNER_CLARIFICATIONS_V6.md; source observations themselves remain unchanged. O03 remains pending alongside the deployment choices in OWNER_CLARIFICATIONS_V6.md, with donor behavior documented in FORGE_JOB_SETTINGS_AUDIT.md.

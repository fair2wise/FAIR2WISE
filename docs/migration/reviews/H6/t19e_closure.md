# T19E closure (H6): operation access on CLI, agent, SDK, API and frontend

**Date:** 2026-11-06. **Owner instruction (2026-11-06):** "finish ... T19E's loose ends". **Branch:** `forge_merge-T19E-closure` (not merged).
The agent/API/SDK/CLI part merged at `e92eac3` (2026-10-29); this closes the frontend part and the loose ends found by the inventory below. The T15 (FORGE consumer) items stay open by the owner's exclusion.

## 1. Inventory: operation x surface

Source: `docs/migration/operations_inventory.json` (OP01-OP48). Only the five `eligible_knowledge_operation` items (OP01-OP05) and the T19C additions K-10/K-11 are operations; the other 43 items are `cli_only`, `needs_owner` or `out_of_scope` (launchers, destructive scripts, deployment, extraction) and are `n/a` on every surface by T19A's disposition (they stay scripts or CLI under separate approval). Cells are state after this change; "(new)" marks what this branch added.

| Operation (inventory id) | CLI | Agent tool | SDK | API | Frontend |
| --- | --- | --- | --- | --- | --- |
| `lit_summarize_extracted_terms` (OP01) | done (new: `ops summarize`, `app/api/cli.py`) | done (`app/modules/operations/papers.py`, `runtime_bridge/bootstrap.py`) | done (`F2WClient.summarize_paper_terms`, `app/public.py`) | missing (needs a session-scoped route; see 3) | done (legacy chat flow, `runtime_bridge/summary_compat.py`) |
| `kg_compute_statistics` (OP03) | done (`ops statistics`) | done (`operations/graph.py`) | done (`graph_statistics`) | done (`GET /v1/operations/statistics`, V25) | done (new: `OperationsTab.tsx`) |
| `kg_compare_snapshots` (OP03) | done (`ops compare`) | done | done (`compare_graphs`) | done (V26) | done (new: `OperationsTab.tsx`) |
| `kg_paper_subgraph` (K-10) | done (`ops paper-subgraph`) | done | done (`paper_subgraph`) | done (V27) | done (new: `OperationsTab.tsx`) |
| `kg_recent_entities` (K-11) | done (JSON ports only: exits 2 `unsupported_capability`; Tiled graphs are API/SDK) | done | done (`recent_entities`) | done (V28) | done (new: `OperationsTab.tsx`; shows the 501 for JSON graphs) |
| `curate_audit_corpus` (OP02) | done (`ops corpus-audit`) | done (`operations/corpus.py`) | done (`audit_corpus`) | done (V29) | done (new: `OperationsTab.tsx`; corpus typed by name) |
| `knowledge_operation` job, whole population (T19C) | done (`ops submit`, `ops result`) | n/a (jobs are not agent Tools) | done (`submit_knowledge_operation`, `knowledge_job_result`) | done (`POST /v1/operations/jobs` V30, `GET .../jobs/{id}` V31) | missing (routes exist; a submit/poll panel is a follow-up, `ops.run` is admin-class) |
| operation catalog: operations, graph ids, corpus names (new) | done (new: `ops list`) | n/a (specs already in the registry) | done (new: `F2WClient.knowledge_catalog`) | missing (needs V-row, see 3) | n/a (graphs come from `/v1/graph/sources`; corpus is typed) |
| `lit_list_missing_papers` (OP04) | missing | missing | missing | missing | missing |
| `lit_inspect_publication_dates` (OP05) | missing | missing | missing | missing | missing |

**Counts (10 operation rows x 5 surfaces = 50 cells): 34 done, 13 missing, 3 n/a.**

| Surface | done | missing | n/a |
| --- | --- | --- | --- |
| CLI | 8 | 2 | 0 |
| Agent tool | 6 | 2 | 2 |
| SDK | 8 | 2 | 0 |
| API | 6 | 4 | 0 |
| Frontend | 6 | 3 | 1 |

Before this branch: 29 done, 18 missing (the five Operations frontend cells, `ops summarize`, `ops list`, `knowledge_catalog`), and the Operations frontend was entirely absent.

Missing and why: OP04/OP05 are not implemented on any surface (T19C record: they depend on script-only PubTemp parsing and PyMuPDF, E2; owner decision on E2 needed first). The job panel, the OP01 route and the catalog route are owner-gated or follow-ups (section 3).

## 2. T15-dependent items (FORGE consumer; excluded by the owner for now)

Not touched here; nothing on the FORGE side exists yet.

- FORGE importing F2W agents/services and the equivalent FastAPI calls from outside the checkout (T15 acceptance; needs Alex).
- `F2WClient.query_knowledge` and `trace_provenance`: raise `NotImplementedError` until the K-12 kg tools exist (no task schedules them; OPEN_DECISIONS B30).
- No `/v1` literature or evidence routes (search/read/figures) for a FORGE FastAPI consumer.
- Operation access from FORGE (running the five operations through its tool catalog, stream termination, cancellation and citation-ID contract tests) is covered by the same T15 acceptance. The F2W side (SDK methods, evidence bundle, contract tests) is in place since `875fbfd`.

## 3. Gaps listed, not closed (owner-gated)

| Gap | Why not closed | Policy row it would need |
| --- | --- | --- |
| `GET /v1/operations/catalog` (graph ids and corpus names for pickers) | A new route; the frontend instead reads graph ids from `GET /v1/graph/sources` (`graph_id`, added to the existing V80 DTO) and takes the corpus name as text | New row, entry capability `chat.ask`, same scope filter as `KnowledgeOperations.catalog` (owner-gated) |
| OP01 route for a session paper summary | Needs a session/paper-scope decision (the scope is trusted server code, never a request argument) | New row, `chat.ask`, object policy on the session (owner-gated) |
| Operations job panel (submit and poll) | Routes exist (V30/V31) but `ops.run` is not held by ordinary ALS users; showing it needs a capability flag, which is a DTO change | Add `can_submit_operation_jobs` to the V60 capability DTO (`ops.run`) |
| OP04 and OP05 operations | Need E2 and an owner decision on PubTemp inputs | none until implemented |

## 4. What changed

Backend:
- `GET /v1/capabilities` (V60) gains `can_run_operations` (`chat.ask`, the entry capability of V25-V29). The frontend shows the tab only on an explicit `true`.
- `GET /v1/graph/sources` (V80) rows gain `graph_id`: the id the operations know the graph by (JSON file `graph_id`, Tiled `f2w_graph_id`). No path or URL is added.
- `KnowledgeOperations.catalog(ctx)` (names only, scope-filtered, `chat.ask`), `F2WOperations.knowledge_catalog`, `F2WClient.knowledge_catalog`, CLI `ops list`.
- `F2WOperations.summarize_paper_terms` and CLI `ops summarize --terms T --manifest M --paper FILE [--max-terms N] [--query Q]`: the T19B Tool over one session's files, same typed result as the SDK method, schema-checked, no paths in the output.
- `app/modules/operations/__init__.py` exports `CompositeGraphPort`, `OPERATION_JOB_KIND`, `directory_corpus_names`, `directory_corpus_resolver`, `storage_graph_files`; `knowledge.py` `__all__` adds `CorpusLister` and `directory_corpus_names`.
- Route count unchanged (64 on `:local`, 47 on ALS). No policy row added. `docs/migration/api/README.md` rows V60 and V80 updated.

Frontend (`ui/`, new surface through existing `/v1` routes only; `tests/contracts/legacy_ui` pins untouched):
- `OperationsTab.tsx` and `operationsApi.ts`: a read-only tab calling `GET /v1/operations/{statistics,compare,paper-subgraph,recent,corpus-audit}`; shows status (`ok`/`partial`), population, coverage, metrics with counting units, warnings, excluded and preview; server refusals (401/403/404/501) are shown. Compare keeps the order the graphs were chosen in.
- `App.tsx`: tab "Operations" gated by `useCapabilities().can_run_operations === true` (unknown, older server and 401 hide it, unlike the Edit gate which shows when unknown).
- `apiHttp.ts` `v1Url` accepts list values as repeated parameters; `capabilitiesApi.ts` and `graphSourcesApi.ts` carry the new fields.
- Tests: `operationsApi.test.ts`, `OperationsTab.test.tsx`, updated capability and graph-source tests, Playwright `e2e/operations.spec.ts` (5 cases) with `mockAgent` options `canRunOperations` and `operations`.

Tests added or changed (Python): `tests/api_v1/test_v1_operations.py` (`ops list` and SDK catalog, scope filter and `chat.ask`, `ops summarize`), `test_v1_capabilities.py`, `test_v1_graph_sources.py`, `tests/packaging/test_public_api.py`.

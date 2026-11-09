# Operations tab, CLI and SDK

Knowledge operations are read-only measurements over a graph or an extracted-terms
corpus. One implementation (`app/modules/operations/`, reached through
`F2WOperations`) serves the agent tool, the `/v1/operations/*` routes, the SDK, the
CLI and the **Operations** tab, so every surface gives the same bounded result
(`status` `ok` or `partial`) and the same refusal. Nothing here writes to a graph.

## Operations tab

The **Operations** tab appears in the sidebar only when `GET /v1/capabilities`
answers `can_run_operations: true` (the caller holds `chat.ask`, the entry
capability of the `/v1/operations` reads). An older server, a missing answer or a
401 hides the tab (`ui/src/app/App.tsx`, `capabilitiesApi.ts`).

Pick an operation, fill in the form, run it:

| Operation | Needs | Route |
|---|---|---|
| Graph statistics | one graph | `GET /v1/operations/statistics` |
| Compare graphs | two to twelve graphs, in the order chosen | `GET /v1/operations/compare` |
| Paper subgraph | a graph and a DOI, arXiv id or paper key | `GET /v1/operations/paper-subgraph` |
| Recent entities | a live (Tiled) graph, optional `since` and limit | `GET /v1/operations/recent` |
| Corpus audit | a corpus name (a file stem under `storage/terminology`), optional sections | `GET /v1/operations/corpus-audit` |

The graph pickers list the sources of `GET /v1/graph/sources` that carry a
`graph_id`, JSON first. You see the status (`ok` or `partial`), the population
examined, coverage, a metrics table with the counting unit of every number,
warnings, what was excluded and a preview. Server refusals are shown as text:
"Not supported for this graph" for a 501 (for example Recent entities on a JSON
graph), "Not found" for a 404. The tab runs the five reads only; the
whole-population job routes (`POST /v1/operations/jobs`) have no panel yet.

## Command line

The operations CLI runs against the local profile only (ALS has no CLI sign-in):

```bash
python -m app.api.cli --root <ops-dir> ops list
python -m app.api.cli --root <ops-dir> ops statistics --graph rsoxs_v1
python -m app.api.cli --root <ops-dir> ops summarize --terms terms.json --manifest manifest.json --paper paper.pdf
```

| Command | Result |
|---|---|
| `ops list` | the operations, graph ids and corpus names this CLI can run (names only; graphs from `<project-root>/storage/kg` or `--graph-file`, corpora from `<project-root>/storage/terminology`) |
| `ops summarize --terms T --manifest M --paper FILE [--max-terms N] [--query Q]` | the deterministic summary of one extracted paper (`lit_summarize_extracted_terms`) over one session's files; schema-checked, no paths in the output |
| `ops statistics`, `compare`, `paper-subgraph`, `recent`, `corpus-audit` | the five reads (`--graph`, `--paper`, `--corpus`, `--section`, ...); `recent` needs a Tiled graph and exits 2 with `unsupported_capability` on JSON ports |
| `ops submit --operation NAME --args JSON`, `ops result <job_id>` | a whole-population `knowledge_operation` job and its bounded report |
| `ops status` | the ingestion monitor (needs `jobs.admin`) |

## SDK

`F2WClient.knowledge_catalog(ctx)` (`app/public.py`) returns
`{"operations": [{name, version, description}], "graphs": [...], "corpora": [...]}`,
the same as `ops list`; graphs and corpora outside the caller's scope are left out.
There is no `/v1` route for the catalog yet (it would need its own policy row,
an owner decision). The other methods are `graph_statistics`, `compare_graphs`,
`paper_subgraph`, `recent_entities`, `audit_corpus`, `run_knowledge_operation`,
`submit_knowledge_operation`, `knowledge_job_result` and `summarize_paper_terms`.

Tests: `tests/api_v1/test_v1_operations.py`, `tests/api_v1/test_v1_capabilities.py`.
The operation-by-surface table and the gaps that stay open (OP04, OP05, the job
panel, an OP01 route) are in `docs/migration/reviews/H6/t19e_closure.md`.

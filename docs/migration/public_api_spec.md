# F2W public interface specification (draft for H1)

Status: Proposed (T01). Names below are target API decisions, not existing
code (plan section 4.4). Types refer to `app/modules/contracts/`
(`CONTRACT_VERSION = 1.0.0-draft.1`).

## 1. Surfaces

| Surface | Entry | Consumers |
|---|---|---|
| Python client | `f2w.public.F2WClient` | FORGE (in-process), CLI, notebooks, workers |
| Agent factory | `f2w.public.create_literature_agent(...)` | FORGE delegating multi-step literature tasks |
| FastAPI | `f2w.api.main.create_app(settings) -> FastAPI` | FORGE over HTTP, new UI surfaces |
| Legacy UI API | `app/modules/f2w_agent/api.py:create_app(cfg, cors_origins=...)` | existing frontend; 19 routes frozen by T18A |
| Legacy model API | `app/modules/kg_rag_api.py:create_fastapi_app` | 6 Ollama/OpenAI-compatible routes; frozen by T18A |

All surfaces call the same application services. FastAPI holds no scientific
logic and no inference client of its own. Workers and CLI call services
directly, never HTTP to themselves.

## 2. Construction and lifecycle

```python
from f2w.public import F2WClient, create_literature_agent

client = F2WClient.from_settings(settings)        # explicit settings object; no .env read
with client:                                      # opens/closes owned resources
    hits = client.search_literature(ctx, query="RSoXS PEDOT:PSS", limit=20)

agent = create_literature_agent(
    client=client,
    sync_model=existing_sync_chat_client,          # term_extractor.clients.ChatClient instance
    async_model=existing_async_chat_client,        # kg_rag_api ChatClient instance
    checkpointer=checkpointer,                     # LangGraph checkpointer, injected
)
```

- Construction does not start Tiled, gateways or workers, and opens no network
  connection until a method needs one.
- Models are supplied by the caller from existing F2W classes and settings. The
  factories never choose a provider or model.
- Two clients in one process (two F2W clients, or an F2W and a FORGE client)
  have separate registries and state. Registries are frozen after construction.
- No nested event loops: async methods are async; sync wrappers run only
  where no loop is running.

## 3. Request context

Every call takes a `RequestContext`. Callers do not build authority:

- FastAPI builds it server-side from startup profile plus, on ALS, the verified
  Tiled identity (T25A).
- In-process callers obtain it from `client.context_for(...)`, which applies
  the same profile rules. On `:local` it returns the `local-operator` context.
  On ALS it requires a verified principal.
- `RequestContext` validation rejects `local_trusted` outside `:local` and
  unverified ALS principals.

## 4. Methods

Status column (2026-10-30, T15 F2W-side surface, owner decision B30): **implemented** means
the `F2WClient` method exists and goes through the same authorization path as the agent
tools or the `/v1` routes; **pending** names what is missing. Typed results are
`ToolResult` (status `ok`/`empty`/`partial`/`awaiting_review`/`denied`/`invalid`/
`unavailable`/`error`), the job DTOs, or the bundle DTO.

| Method | Effects | Returns | Status | Path / notes |
|---|---|---|---|---|
| `search_literature(ctx, query, limit, *, technique, material, year_from, year_to)` | network, read | `ToolResult` (external candidates) | implemented (T15 over T05) | `lit_search_papers` through the application executor; needs `literature=` (a `LiteratureToolset`); discovery only, never ingests; `limit > 25` is `invalid` |
| `resolve_work(ctx, identifier)` | network | `ToolResult` (work record) | implemented (T15 over T05) | DOI (bare, URL, `doi:`) and arXiv (`arXiv:<id>`, URL; via `10.48550/arXiv.<id>`) through `lit_get_papers_by_doi`; `empty` = unknown work; OpenAlex `W` ids and titles are `invalid` (use `search_literature`) |
| `read_paper(ctx, work_id)` | read private source | `ToolResult` (sections index) | implemented (T15 over T05) | `lit_read_paper`; needs the toolset's trusted `SourceAccessScope` (`denied` without one); agent-private: pass through `F2WClient.disclose` before user-facing output (D19) |
| `read_section(ctx, work_id, section)` | read private source | `ToolResult` (bounded text + locator) | implemented (T15 over T05) | `lit_read_paper_section`; `not_found` carries `available_sections`; agent-private as above |
| `get_evidence(ctx, evidence_ids)` | read | `ToolResult` (`EvidenceRef`s + candidate records) | implemented (T15 over T06) | candidate records of the application's `evidence=` source, `chat.ask` required; `partial` when some ids are unknown, `empty` when none is |
| `evidence_bundle(ctx, work_id, *, include_quotes=False)` | read | `LiteratureEvidenceBundle` | implemented (T15) | the read-only bundle of plan T15 (`app/modules/evidence/bundle.py`): source version + checksum, quantities with raw/normalized units, evidence ids and locators, caveats; JSON `to_json`/`from_json`; quotes only on request |
| `query_knowledge(ctx, question, graphs)` | read | `ToolResult` | **pending** | needs the K-12 `query_knowledge_graph` tools (`kg_find_entities`, `kg_get_summary`, `kg_read_paper`, plan section 10); T08 did not port them and no `plan_addenda` task (A6/A7) schedules them. `answer_from_knowledge` (direct read, QA model) and the T19C operations are the graph reads today |
| `answer_from_knowledge(ctx, question, ...)` | read, model inference | `ReadOutcome` (legacy verdict + citations) | implemented (T03) | wraps the existing QA path unchanged |
| `submit_ingest(ctx, request)` / `get_job` / `cancel_job` | write staged, network, model / read / write | `JobHandle` | implemented (C2) | forwarded to the shared `F2WOperations`; needs `operations=` |
| `plan_publication(ctx, snapshot_id, target)` | read | `JobHandle` (plan via `operations.current_plan`) | implemented (C2) | H5 gate; snapshot must be frozen JSON |
| `approve_plan(ctx, plan, *, job_id, approver_credential)` | write | `{"approval", "job"}` | implemented (C2) | `create_approval` + `Publication.submit_approval`; trusted operation, **not** an agent tool |
| `publish_plan(ctx, plan, approval)` | write graph (in the job) | `JobHandle` | implemented (T15 over T10) | `F2WOperations.apply_approval`: `approval.authorizes(plan)` checked, then the publication job re-checks human/capability/scope/staleness and is re-queued; the `ImportReceipt` id is read from `operations.job_view` once succeeded. No direct graph write from the SDK |
| `trace_provenance(ctx, record_id)` | read | `ToolResult` | **pending** | needs the K-12 `trace_provenance` tools (`kg_get_lineage`, `kg_get_connections`) and an execution-provenance lookup by `provenance_id`; neither is ported or scheduled (A6/A7). `get_evidence` answers the evidence side for candidate ids |
| `graph_statistics` / `compare_graphs` / `paper_subgraph` / `recent_entities` / `audit_corpus` / `run_knowledge_operation` / `submit_knowledge_operation` / `knowledge_job_result` | read / job | `ToolResult` / `JobHandle` | implemented (T19E) | the five T19C operations, same `F2WOperations` as `/v1/operations` |
| `summarize_paper_terms(ctx, scope, ...)` | read | `ToolResult` | implemented (T19B) | pinned `PaperScope`; authorizing executor |
| `cost_control(ctx)` / `set_cost_control(ctx, *, enabled, budget_cap_usd)` | read / write (audited) | control dict with `spend` | implemented (T31, B5) | `GET`/`PUT /v1/curation/cost-control`; `curation.approve`; the mutation is human-only |
| `bulk_review(ctx, decisions)` | write (audited) | `{"results", "accepted", "rejected", "failed"}` | implemented (T31, B5) | `POST /v1/curation/cases/bulk-review`; each item through `review_case` |
| `vocabulary_quality(ctx, *, limit)` | read | snapshot history (counts) | implemented (T31, B5) | `GET /v1/vocabulary/quality`; `chat.read_support` |
| `figure_scales(ctx, *, corpus, work_id, figure_id, limit)` / `figure_scale(ctx, record_id)` / `record_figure_scale(ctx, **fields)` | read / read / write (audited) | record dicts | implemented (T31, B5) | `GET`/`GET {id}`/`POST /v1/figures/scales`; reads `chat.read_support` within scope, recording `curation.approve` human-only |
| `folder_roots(ctx)` / `list_folder(ctx, root, path)` / `folder_index(ctx, root, path)` | read (local filesystem under fixed roots) | labels / entries / document inventory | implemented (T32, B4) | `GET /v1/folders/roots`, `/list`, `/index`; `ingest.acquire`; `:local` only (`DENIED` on ALS through the SDK, 404 over HTTP) |
| `disclose(payload)` | none | projected payload | implemented (T15) | `DisclosureProjector.project_api_response`, the projection the `/v1` routes apply (D19); the SDK returns agent-private typed results and the consumer projects before showing a person |

Errors raise `ContractError` subclasses with stable `ErrorCode`s, or return a
`ToolResult` with a failure status. Empty, unavailable, denied, invalid,
partial and awaiting-review stay distinguishable. A method whose backing is not
wired at construction (`literature=`, `evidence=`, `operations=`) raises
`ContractError(UNSUPPORTED_CAPABILITY)`, never a silent empty result.

Consumer example and contract test: `examples/forge_consumer.py` (both T15 paths against
offline fakes) and `tests/packaging/test_forge_consumer_contract.py` (import as `f2w.public`
from outside the checkout, isolation, stream termination, cancellation, error envelopes, id
stability across SDK and HTTP, bundle JSON round trip, `publish_plan`).

## 5. FastAPI mapping

`f2w.api.main` routes mirror the methods above under a versioned prefix (for
example `/v1/literature/search`, `/v1/jobs/{id}`, `/v1/publications/plans`),
served behind the `/fair2wise/api` gateway (T24A). As of 2026-10-30 the jobs,
publication, review, status and `/v1/operations` routes exist; **no
`/v1/literature/*` or `/v1/evidence/*` route exists yet** (the T15 literature and
evidence methods are SDK-only; T31 does not list them either), so FORGE's HTTP path
covers jobs/events/operations and the SDK path covers search/read/evidence. Job progress streams use the
existing SSE framing (`event:`/`data:` LF blocks) with `JobEvent` payloads.
Legacy routes keep their paths and shapes.

## 6. Out of scope

No MCP server, no FORGE imports in F2W, no generic code execution, no raw
GraphQL proxying, no admin credentials in responses.

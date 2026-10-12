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

| Method | Effects | Returns | Notes |
|---|---|---|---|
| `search_literature(ctx, query, limit)` | network, read | `ToolResult` (hits) | discovery only; never ingests |
| `resolve_work(ctx, identifier)` | network | `ToolResult` (Work) | DOI/arXiv/title resolution |
| `read_paper(ctx, work_id)` | read private source | `ToolResult` (summary, sections index) | user-facing output passes the disclosure projection (T21A) |
| `read_section(ctx, work_id, section)` | read private source | `ToolResult` (bounded text + evidence) | bounded; no full-text dump |
| `get_evidence(ctx, evidence_ids)` | read | `ToolResult` (`EvidenceRef`s) | |
| `query_knowledge(ctx, question, graphs)` | read | `ToolResult` | direct retrieval; no planning LLM |
| `answer_from_knowledge(ctx, question, ...)` | read, model inference | legacy-compatible answer + citations | wraps the existing QA path unchanged (T03) |
| `submit_ingest(ctx, request)` | write staged, network, model | `JobHandle` | requires ingest capability |
| `get_job(ctx, job_id)` / `cancel_job(ctx, job_id)` | read / write | `JobHandle` | |
| `plan_publication(ctx, snapshot_id, target)` | read | `ChangePlan` | snapshot must be frozen JSON |
| `approve_plan(ctx, plan)` | write | `ApprovalRecord` | trusted operation; **not** an agent tool |
| `publish_plan(ctx, plan, approval)` | write graph | `ImportReceipt` | checks `approval.authorizes(plan)` |
| `trace_provenance(ctx, record_id)` | read | `ToolResult` | evidence vs execution provenance kept distinct |

Errors raise `ContractError` subclasses with stable `ErrorCode`s, or return a
`ToolResult` with a failure status. Empty, unavailable, denied, invalid,
partial and awaiting-review stay distinguishable.

## 5. FastAPI mapping

`f2w.api.main` routes mirror the methods above under a versioned prefix (for
example `/v1/literature/search`, `/v1/jobs/{id}`, `/v1/publications/plans`),
served behind the `/fair2wise/api` gateway (T24A). Job progress streams use the
existing SSE framing (`event:`/`data:` LF blocks) with `JobEvent` payloads.
Legacy routes keep their paths and shapes.

## 6. Out of scope

No MCP server, no FORGE imports in F2W, no generic code execution, no raw
GraphQL proxying, no admin credentials in responses.

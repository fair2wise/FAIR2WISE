# Existing F2W frontend compatibility contract

**Draft v8 | Required at H1, H2, H6 and H7 | Static source observations, not executed test results**

Source: uploaded FAIR2WISE-rsoxs.zip, archive-recorded commit `222b1e7b130567dbf4a3242697ac2b465794a38d`. The full exact field/annotation/default source is in `f2w_ui_contracts.json`; route decorators are in `f2w_http_routes.json`. Proposed compatibility implementations below are design, clearly distinguished from current code. Read B01-B03 and B27-B30 in the source crosswalk.

## 1. Compatibility means the old frontend works

Keep the current UI source/build as a frozen integration client. It must work against the migrated backend without schema, stream-parser, session, graph-edit, citation or pending-action rewrites. D26 now explicitly requires deployment base/API URL configuration: rebuild the same frontend source with /fair2wise/ and /fair2wise/api, while retaining standalone developer URL support. That is an owner-required configuration change, not permission to alter UI behavior. An updated client passing against an updated backend is not sufficient evidence. Optional new UI capabilities may be additive; no existing behavior is silently removed because it is missing from a donor design diagram.

Compatibility does not require retaining unsafe behavior, deprecated service implementation, secrets in outputs or accidental bugs. Any necessary security or semantic change is an explicit owner-reviewed deviation with a migration path; do not relax authorization to make a test pass. Existing tests' mocked behaviors are not automatically stronger evidence than the implementation. Record and resolve discrepancies at H0/H1.

## 2. Existing API entrypoints and routes

UI entrypoint: `app/modules/f2w_agent/api.py:create_app` (L6611-L7031), with `run_api` immediately after it. `create_app(cfg, *, cors_origins=None)` currently constructs `AgentPipelineService`. Preserve the callable signature and existing launcher; it may forward to new `app/api/main.py` and domain/application factories. The new runtime must not own HTTP serialization or require the browser to understand LangGraph messages.

Browser base URL: `ui/src/app/components/data/liveAgent.ts:221`, `VITE_F2W_AGENT_API_URL` or `http://127.0.0.1:8090`, with trailing slash removed. Preserve root paths, configured base prefixes/proxy behavior, CORS/credential behavior and startup settings checks. Requiring `/api/v2` or a changed default port is not backward compatible.

### 2.1 Nineteen UI-agent route definitions

All source line references below are in `app/modules/f2w_agent/api.py`. These are static decorators; verify actual registered routes/OpenAPI in an isolated baseline app. A route's presence does not establish that every UI control calls it directly.

| Method | Existing path | Source handler / line | Consumer or obligation |
| --- | --- | --- | --- |
| GET | `/health` | `create_app.health` L6625 | Startup and diagnostics; existing launch/health clients |
| GET | `/settings` | `create_app.get_settings` L6645 | liveAgent.ts; agentSettings.ts; AppSettingsButton and App startup |
| PUT | `/settings` | `create_app.update_settings` L6650 | liveAgent.ts; agentSettings.ts; AppSettingsButton and App startup |
| POST | `/session/reset` | `create_app.reset_session` L6658 | liveAgent.ts; ChatSidebar session reset |
| DELETE | `/session/{session_id}` | `create_app.delete_session` L6663 | liveAgent.ts; chat-session deletion |
| GET | `/graph` | `create_app.graph` L6671 | liveAgent.ts; GraphMockup/graph loader |
| POST | `/graph/nodes/search` | `create_app.search_graph_nodes` L6676 | liveAgent.ts; relationship-target/node search |
| GET | `/graph/neighborhood/{node_id:path}` | `create_app.graph_neighborhood` L6686 | liveAgent.ts; citation/Tiled neighborhood exploration |
| GET | `/graph/node/{node_id}` | `create_app.graph_node` L6716 | liveAgent.ts; GraphMockup details, fields/snippets/relationships |
| PATCH | `/graph/node/{node_id}` | `create_app.patch_graph_node` L6751 | liveAgent.ts; GraphMockup details, fields/snippets/relationships |
| POST | `/graph/upload` | `create_app.upload_graph` L6763 | Published legacy API; graph upload/settings integration to characterize |
| POST | `/chat` | `create_app.chat` L6771 | liveAgent.ts normal request and pre-stream-event fallback |
| POST | `/chat/action` | `create_app.chat_action` L6790 | liveAgent.ts pending-action fallback |
| POST | `/publications/search` | `create_app.publication_search` L6806 | liveAgent.ts; publication discovery/candidate selection |
| POST | `/chat/stream` | `create_app.chat_stream` L6818 | liveAgent.ts; ChatSidebar progress/graph updates |
| POST | `/chat/action/stream` | `create_app.chat_action_stream` L6911 | liveAgent.ts; pending download/extraction action progress |
| GET | `/traces/{turn_id}` | `create_app.get_trace` L7002 | uiTelemetry.ts; trace inspection |
| POST | `/telemetry/ui` | `create_app.telemetry_ui` L7010 | uiTelemetry.ts event batch; no draft keystrokes |
| POST | `/annotations` | `create_app.create_annotation` L7019 | uiTelemetry.ts human pass/fail labels |

### 2.2 Six separate model-compatible routes

`app/modules/kg_rag_api.py:create_fastapi_app` is independent of the UI agent API. Preserve its contract and server/launcher behavior rather than confusing it with the UI backend.

- `POST /api/chat` - `create_fastapi_app.api_chat`, L246.
- `POST /v1/chat/completions` - `create_fastapi_app.openai_chat_completions`, L262.
- `GET /api/tags` - `create_fastapi_app.list_models`, L285.
- `GET /api/version` - `create_fastapi_app.ollama_version`, L294.
- `GET /v1/models` - `create_fastapi_app.openai_models`, L299.
- `GET /api/ps` - `create_fastapi_app.list_processes`, L314.

## 3. Request, response and graph shape

Capture Pydantic-generated schemas and live serialized fixtures; the supplied static 25-model inventory is not an OpenAPI golden file. Keep existing required/optional fields, aliases, constraints, empty arrays, nulls and omission semantics. Compare at the compatibility boundary, not by imposing the new internal ToolResult shape on old callers.

- **ChatRequest** (api.py L124-L145): `message`, `messages`, `session_id`, `client_turn_id`, `ui_settings`, `graph_source`, `json_graph_path`, `json_graph_paths`, `source_rag`, `use_live_tiled`. User/assistant history entries may have blank content; the backend filters them. Session and turn IDs follow the existing bounded identifier pattern. Preserve request overrides and setting precedence.
- **ChatActionRequest** (L228-L254): `decision=yes|no`, optional `kind=download|extraction`, candidate_index, messages, session/client/parent turn IDs, ui_settings and legacy graph options. Some fields are present but not consumed by every baseline path; freeze actual behavior rather than inventing functionality from a field name.
- **ChatResponse** (L210-L225): `status`, `answer`, `sufficient`, `node_ids`, `publications`, `confidence`, `rounds`, `graph`, `graph_source_requested`, `graph_source_used`, `workdir`, `pending`, `orchestration`, `turn_id`. Retain status distinctions, source provenance, confidence meaning and pending envelopes. Sanitize absolute path/credential disclosures through an owner-reviewed compatible policy where needed.
- **GraphPayload**: `nodes`, `edges`, `source_path`. GraphNode carries id/label/type, description, publications, formula/properties/extra_fields, source_papers, graph_id/graph_label, linked code, repository/commit/file/line provenance and license. GraphEdge uses `source`, `target`, `predicate`, not a new incompatible triple shape.
- **Graph editing**: preserve node-detail fields, relationship `action=add|remove`, linked-snippet alias `_action=upsert|unlink`, and response graph updates. Do not rename `_action` to a private internal field on the old endpoint. Baseline user clicks Save expect a completed edit response; returning a new asynchronous job envelope needs separate support, not a silent swap.
- **Settings**: preserve cborg/ollama provider/model choices, deterministic/agentic workflow, full/targeted extraction and page limits, selected JSON paths/graph IDs, source RAG/live-Tiled toggles, query caps/hops, available graphs, Tiled status/error and key-set indicator. Do not return the API key. Preserve old primary-path compatibility alongside multiple selected paths.

`f2w_ui_contracts.json` enumerates literal response statuses found in source; it is not a closed enum because dynamic/conditional values and error paths exist. Runtime fixtures must cover the actual states visible in the browser, including answered, insufficient, pending decisions, stopped actions and recoverable errors.

## 4. SSE and cancellation/replay contract

Sources: `liveAgent.ts:448-623`; `api.py:_sse`, `create_app.chat_stream`, `create_app.chat_action_stream`; `ChatSidebar.tsx` progress/result handling.

| Wire event | Preserved meaning |
| --- | --- |
| `progress` | JSON object with phase/message and turn_id. First event is turn_started; later phases may include graph updates and orchestration. |
| `complete` | Full legacy AgentChatResponse, even for an ordinary insufficient-evidence or awaiting-decision outcome. |
| `error` | Error response/message consistent with current client parsing; transport and model failures are not empty successful evidence. |

The client splits blocks on LF-LF and decodes fragmented UTF-8 chunks. Emit valid `event:`/`data:` framing with terminating blank line; do not send raw token deltas, an unnamed LangGraph stream, a new event vocabulary or different payload keys. Preserve phase `graph_update` with GraphPayload and node_ids: ChatSidebar accumulates nodes by ID and edges by signature. Other phases are progress rows; their order and stable meanings should survive the runtime rewrite.

The current client retries the non-streaming endpoint **only if no event has been received**; it does not retry after an event or after abort. An ended stream without complete is an error. Test startup failures, partial blocks, split codepoints, error after progress and abort. Do not let fallback repeat approved downloads/extractions/publications. Source inspection shows client_turn_id correlation, not a proven idempotency guarantee. A proposed replay ledger must be keyed by principal/session/operation/content, reject mismatched reuse, and preserve response semantics; document it as an improvement. Server coroutine cancellation is not proof that blocking parser/model work has stopped; add cooperative cancellation/reconciliation for required jobs.

Literal progress phases from AgentPipelineService are recorded in JSON; route-level `turn_started` is recorded separately. Do not erase legacy splash_reimport progress phases by simply removing the service: translate old-client progress meaning to the actual graph-publication/reload stage, document the legacy naming, and expose canonical stage names only on additive/new interfaces.

## 5. Splash service removal without breaking the editable graph UI

### Current source facts

`agentSettings.ts:221-227` converts browser `splash_links` into API `splash` and any non-JSON response back into splash_links. ChatRequest/Settings validation allow splash or json. `GraphMockup.tsx:663` enables editing when graphSource is not json. The API still contains legacy update/reimport paths. This is why replacing the server and changing only a settings enum would break the old application.

### Proposed H1 compatibility design

Use a dedicated legacy-wire adapter: the old editable graph selection maps to a **configured and authorized Tiled literature scope**, while the canonical internal backend is Tiled Graph. No new Splash adapter/service/dependency is created. Preserve the old enum on the old API when required by its client; add truthful canonical backend/capability metadata through new fields/interfaces. Do not pretend a Splash endpoint is running. Old host URLs/API keys are not silently repurposed; deployment mapping and access policy are explicit.

Preserve JSON mode separately, including read-only editing rules and the existing JSON-mode ingestion restrictions. Do not make choosing a local JSON graph accidentally publish it to Tiled or widen access. Preserve selected graph identity, source provenance and fallback notices.

For Tiled immutable/restricted mutation semantics, design an effective-node/revision and relationship projection behind the old API. A saved update must be readable immediately through the same public node ID (or a validated stable alias) and old citations/bookmarks must continue resolving. Test field updates, adding/removing incoming/outgoing relationships, snippet upsert/unlink and publication updates. No deleting/recreating unrelated neighbors, whole-graph resets or rewriting FORGE-owned records. User Save expresses a scoped edit intent under local-operator authority or verified ALS-admin authority, not blanket approval for an agent's corpus rewrite; preserve the expected completion UX through an owner-approved authorization design.

If real Tiled capabilities cannot satisfy safe edit parity, this is a **blocking design issue** for H1/H6. Do not hide the controls, report fake success, defer the workflow without approval, or require the deprecated Splash process to keep running. Capture concurrent edit/stale-revision handling and rollback behavior before production use.

## 6. Sessions, pending actions, citations and persistence

Session behavior is server- and browser-side. Server `AgentPipelineService._activate_session` binds memory/workflow per session under workdir; legacy no-session behavior uses __legacy__. Preserve active paper, topic, pending decisions and per-session traces across relevant operations. Reset/delete must not clear other sessions or published KG data. Check racing settings/session requests during a running job.

Browser keys to preserve or migrate reversibly:

| Key | Owner |
| --- | --- |
| `fair2wise-agent-settings-v1` | agentSettings.ts |
| `fair2wise-available-graphs-cache-v1` | agentSettings.ts |
| `fair2wise.chat.sessions.v2` | chatSessions.ts |
| `fair2wise.chat.messages.v1` | legacy history migration in chatSessions.ts |
| `fair2wise-publication-favorites-v1` | publicationFavorites.ts |
| `fair2wise.citeFocusNodeId` | ChatSidebar sessionStorage citation focus |

The browser's download-candidate selection/conversation flow is not the same as pending extraction Run/Skip buttons. Preserve candidate indices, recommended selection, active-paper follow-ups, answer-empty pending cards, pending kind and yes/no actions. Approval of a displayed candidate may not cause another candidate or version to be ingested after an internal rerank.

Preserve the literal KG/PDF/OPS/MP citation syntax interpreted by kgCitations.ts, graph prefix handling, code-fence exclusions and source metadata. Do not replace citations with bare numbered labels or turn retrieval IDs into new undocumented IDs. Multi-graph selection preserves `(graph_id, node_id)`; identical node IDs in two graphs must not be conflated. Graph viewer display limits and query retrieval limits are distinct settings, not interchangeable caps.

Preserve trace/annotation APIs, turn and parent-turn correlation, source-linked code/license fields, publication bookmarks and normal network-error presentation. No raw keystroke or secret logging to satisfy observability. The branch already supports ops docs, Materials Project evidence and live Tiled lookup: retain these baseline paths without expanding donor scope into experimental analysis.

## 7. Required compatibility cases

Every case gets baseline behavior, source references, candidate result, fixture hash, test command and pass/fail/skipped reason. Tests described here are **required work**, not already passing tests.

| Case | Scenario | Required evidence |
| --- | --- | --- |
| UI01 | Old frontend startup / settings / health | Existing base URL/CORS and response defaults; no required new client config |
| UI02 | CBORG/Ollama model changes | Same provider classes, aliases, defaults, round trip and errors |
| UI03 | JSON multi-graph selection | Primary/list selection, graph labels/IDs and opt-in demo behavior retained |
| UI04 | Legacy editable graph mode | Old splash/splash_links tokens work through approved Tiled mapping; no Splash service |
| UI05 | Ordinary grounded answer | Same evidence selection/policy/citations under fixed fake model |
| UI06 | Numerical insufficiency vs conceptual fallback | Preserve actual F2W distinction; no new unsupported numeric assertion or refusal-only policy |
| UI07 | Source RAG on/off / ops docs / MP | Existing optional modes and literal source citation labels retained |
| UI08 | Stream complete / graph_update | Legacy framing; first turn_started; graph merge; terminal full response |
| UI09 | Fragmented/failed stream | Split UTF-8 and blocks; early fallback only; no duplicate side effects |
| UI10 | Stop / cancelled action | No fallback on AbortSignal; explicit cancellation/reconciliation, no false success |
| UI11 | Paper candidate selection / decline | Same selected source/version/index and pending download flow |
| UI12 | Extraction Run / Skip / targeted pages | Correct pending kind, scope, progress and graph/answer reload |
| UI13 | Session switch, reload, reset, delete | Per-session isolation; legacy history migration; other sessions preserved |
| UI14 | Node search/detail and selected-source query | Graph-specific IDs, source_path and property/type overlay retention |
| UI15 | Edit node and publications | Same saved fields visible via old API; authorized scoped Tiled revision |
| UI16 | Add/remove relationships | Incoming/outgoing target, predicate and effective graph consistent |
| UI17 | Snippet upsert/unlink | _action alias, provenance, full text and license references preserved |
| UI18 | JSON read-only and graph upload | Restrictions retained; upload validation/result and settings selection compatible |
| UI19 | KG/PDF/OPS/MP citations and neighbors | Clickable known prefixes and old IDs; no invented labels |
| UI20 | Publication search / favorites | DOI/metadata/title links and storage persist across restart |
| UI21 | Query/viewer limits | Separate limits, full/filtered graph scope, no silent clipping change |
| UI22 | Traces / telemetry / annotations | Turn linkage and pass/fail labels; privacy filtering and no secret export |
| UI23 | Failures and unavailable providers | Existing error UI; partial/denied/unavailable not empty successful answer |
| UI24 | Model-compatible endpoints | Six separate endpoints retain legacy consumers/stream formats |
| UI25 | Concurrent sessions/settings/requests | No state/model/registry leakage between users or app instances |
| UI26 | Publish/restart/rollback | Old UI binds consistent graph/index; references survive version changes |
| UI27 | Existing live-Tiled graph exploration | Lookup toggles, identity/capabilities and error reporting remain supported |
| UI28 | Frozen UI + new backend + FORGE host | Same scenarios without monkeypatching away HTTP or hiding import collisions |

## 8. Tests, environments and gate evidence

Keep baseline tests unmodified as the first comparison. The snapshot contains these existing browser suites:

**Unit tests:**

- `ui/src/app/components/ChatSidebar.test.ts`
- `ui/src/app/components/GraphMockup.test.ts`
- `ui/src/app/components/agentApiErrors.test.ts`
- `ui/src/app/components/agentSettings.test.ts`
- `ui/src/app/components/chatSessions.test.ts`
- `ui/src/app/components/data/liveAgent.test.ts`
- `ui/src/app/components/kgCatalog.test.ts`
- `ui/src/app/components/kgCitations.test.ts`
- `ui/src/app/components/kgNodeColors.test.ts`
- `ui/src/app/components/publicationLinks.test.ts`

**Playwright specs:**

- `ui/e2e/chat-ask.spec.ts`
- `ui/e2e/citations.spec.ts`
- `ui/e2e/errors-responsive.spec.ts`
- `ui/e2e/kg-viewer.spec.ts`
- `ui/e2e/papers-bookmarks-docs.spec.ts`
- `ui/e2e/pending-ingest.spec.ts`
- `ui/e2e/sessions.spec.ts`
- `ui/e2e/settings.spec.ts`
- `ui/e2e/shell.spec.ts`

Existing `ui/e2e/fixtures/agent.ts` intercepts backend HTTP; passing those tests alone cannot verify migrated Python behavior. Add a separate live-backend Playwright project/fixture rather than weakening the old fixture. It uses the actual FastAPI server, deterministic fake inference/paper providers, temporary work/session/trace roots, authorized synthetic data and an isolated Tiled Graph server. No prod PDFs/SQLite files or paid endpoints by default. Preserve current Chrome-channel configuration or explicitly provision the approved test browser. Record Node/npm/Python/dependency/server versions; npm scripts in this snapshot include test, build and e2e (do not claim a nonexistent typecheck script was run).

Execution layers: (1) static contract/source checks; (2) Python legacy wire/QA/provider unit and integration tests; (3) frontend unit/build and existing mocked e2e; (4) frozen-UI live-backend tests with fake inference; (5) disposable real-Tiled publication/edit/ownership/recovery; (6) owner-approved scientific/visual-model evaluation and user pilot. Layers 1-3 do not replace 4-6. Preserve source/data/trace isolation and audit any fixture that defaults to /tmp, /private/tmp or a bundled catalog before running it.

**H1 / T18A:** baseline OpenAPI/payload/SSE/localStorage fixtures; branch CI plan; reviewed storage-token/edit mapping. **H2 / T18B:** frozen browser performs read/settings/stream/citation flow on actual API with fake inference. **H6 / T18C:** complete UI01-UI28 matrix, Tiled edits/pending/session/recovery and FORGE import/API demonstrations. **H7:** canary and rollback show compatibility shims still work; owner approves any later retirement.

Test artifacts must identify the frozen frontend hash, baseline/candidate backend commits, source-lock revisions and Tiled version. Attach Playwright traces/screenshots, schema/event diff and skipped/failing case explanations. Automated success is evidence for review, not owner approval.


## v4 owner-policy refinements (required, additive to the existing matrix)

Preserve current model selectors, schemas, SSE/events, graph edit behavior, settings/session/citation IDs and JSON mode. The application connects to a central Tiled Graph or explicitly manages its own instance; tests use its disposable provisioner. Existing splash/splash_links tokens may survive only as a reviewed wire adapter, not a service dependency.

Add binary cases for combined chat/background sync/async quotas, all-provider throttling, no automatic model upgrade and O03-approved job settings semantics. The source limiter's separate semaphores do not by themselves prove combined admission when the new runtime adds parallelism.

D19 is a source-delivery constraint: do not serve full papers/crops through downloads, evidence URLs, tool traces, SSE, graph properties or report exports. O02 is resolved: relevant exact cited excerpts remain visible. Preserve field shapes and citations; flag a narrow incompatible payload for owner decision rather than conceal it with an updated frontend. No user paper-serving feature is needed to make agent figure extraction complete.

Knowledge Operations must work through the frozen frontend and existing terminal event conventions; large result data stays in approved internal or user-safe artifacts. H2 includes an unchanged deterministic-summary case. H6 includes graph/corpus computation, exactness/coverage and no-auto-repair cases. Full parity runs the required binary suite against the installed API and actual disposable Tiled, not only mocked browser routes.

## Draft v8 integration requirements

Versioned JSON remains the KG creation artifact; Tiled 0.2.19 consumes it through the separate adapter in JSON_KG_PUBLICATION_SPEC.md. Preserve current scientific schema logic and inheritance; the measurement/technique split is not an acceptance requirement. Relevant exact cited quotations are allowed while complete source papers and original crops remain internal. Any earlier conditional wording about O01/O02/O04 in source-derived commentary is superseded by OWNER_CLARIFICATIONS_V8.md; source observations themselves remain unchanged. O03 remains pending alongside the deployment choices in OWNER_CLARIFICATIONS_V8.md, with donor behavior documented in FORGE_JOB_SETTINGS_AUDIT.md.

## Draft v8 deployment qualification

The production build uses Vite base /fair2wise/ and the existing VITE_F2W_AGENT_API_URL=/fair2wise/api. Keep rooted internal handlers and legacy method/path suffixes; a private gateway strips the API prefix once. Ansible forwards the external base prefix using the reviewed contract. Test the frozen source rebuilt with these settings under a non-localhost FQDN, deep links, docs, assets, SSE, error responses and citation selection. FQDN is not compiled in. API/asset misses do not become SPA HTML.

D27 retains the technical admin writer; D30/D34 settle caller access by profile. :local opens directly with all administrative controls available without login, including Save, ingestion, curation, approval and publication. No absent/expired/non-admin user credential restricts local features. On ALS, native Tiled sign-in is required; verified admins retain existing privileged flows and ordinary users are chat-only. Enforce ALS denial before queuing effects across legacy API/Tool/SDK paths. Preserve DTO/SSE/citation semantics and edit readback; never put the writer secret in public settings or traces.
## 10. Tiled identity and Git snapshot cases (v7)

Run the existing full workflows locally without a login and verify local-operator audit and backend-only Tiled service credentials. Then run ALS privileged workflows with a verified admin and chat/session/citation workflows with a non-admin. ALS cases cover missing/expired sign-in, wrong issuer, downgrade, forged roles/profile, pending ingestion denial, SDK parity and no unprotected stream fallback. Local mode must remain fully usable when user identity lookup is absent, and ALS mode must never inherit that exception. No-login local editing is now an explicit requirement; unauthenticated ALS editing remains forbidden.

Confirm admin edits reconcile into a new immutable JSON, which is then tested/committed. A production-profile disposable instance receives the identical commit-selected graph validated on staging, with separate authorization and receipt. Git promotion does not expose private source files or rewrite old evidence IDs. Unmatched/private source dependencies block the relevant capability explicitly rather than triggering a paid rebuild.

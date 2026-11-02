# T14: F2W FastAPI factory, `/v1` jobs/review/publication API and shared service contract

- **Status:** implemented on branch `forge_merge-T14-api` for review. Not merged. Real publication stays gated at H5.
- **Code:** `app/api/main.py` (factory), `app/api/operations.py` (shared contract), `app/api/policies.py` (route policies), `app/api/routes_*.py` (thin handlers), `app/api/origin_gate.py` (I-07), `app/api/upload_limits.py` (I-05), `app/api/cli.py` (operations CLI), `app/api/auth.py` and `app/api/sessions.py` (scope-change wiring, sign-in/sign-out origin checks), `app/api/folders.py` (T32 folder roots).
- **Tests:** `tests/api_v1/` (fake identity, `:local` and `:als-dev`), `tests/api_v1_live/` (real disposable Tiled 0.2.18; needs `F2W_TILED_PYTHON`).

## 1. Entrypoints

| Entrypoint | Behaviour |
| --- | --- |
| `app.modules.f2w_agent.api.create_app(cfg, *, cors_origins, deployment, startup_config, identity_port, credential_store, clock)` | Unchanged signature and routes: the 19 UI routes, ALS sign-in handlers, access control and disclosure projection. The frozen UI and T18A/T18B contract tests use it as before. |
| `app.api.main.create_api_app(cfg, ...)` | Calls `create_app` with the same arguments, then adds the 41 `/v1` routes (38 on an ALS profile: the three T32 folder routes are registered on `:local` only), the origin gate and the body cap on `/v1/` and `/auth/`, and the scope-change wiring. Extra arguments: `operations`, `operations_root`, `publication_gate`, `max_body_bytes`, `sse_poll_seconds`, `sse_max_seconds`, `judge_settings`, `knowledge_operations` (T19E), `folder_roots` (T32). |
| `python -m app.modules.f2w_agent.cli api` | Unchanged (legacy app). `api --jobs-api` serves `create_api_app`. |
| `python -m app.api.cli --root DIR jobs|cases|cost|vocab|scales|folders|audit|ops ...` | Calls `F2WOperations` directly with a `cli` context (local profile only). `ops statistics|compare|paper-subgraph|recent|corpus-audit|submit|result` are the T19E Knowledge Operation wrappers (graphs from `--project-root/storage/kg` or `--graph-file`, corpora from `storage/terminology`). `cases bulk-review`, `cost show|set`, `vocab quality` and `scales list|show|record` are the T31 wrappers; `folders roots|list|index` (T32) browse the roots named by `F2W_FOLDER_ROOTS`. |
| `F2WOperations` | Direct Python use (SDK, notebooks, FORGE). Same methods the routes call. |

With no `operations` argument the factory builds `build_operations(settings, <workdir>/operations)`: SQLite job, curation and audit stores, the figure-scale record table in the curation database, the cost-control file `cost_control.json` and the vocabulary-quality snapshot directory `vocab_quality/` (T31), **no job kind and no publication** (real pipeline handlers need H3, real publication needs H5). Trusted wiring registers kinds on `ops.jobs` and builds a `Publication` on it.

## 2. Shared service contract (`F2WOperations`)

Every method takes the server-resolved `RequestContext` first and forwards to the T10 services. The routes add HTTP validation, context resolution and response projection only.

**Projection (AUD-06).** Every `/v1` response body and every `job_event` SSE payload passes through the app's one `DisclosureProjector`, via `project_api_response` (`app/api/routes_common.project`): private keys (snippets, prompts, source text, crops, local paths, secrets) are dropped, `file:` URIs and served-artifact paths are dropped, absolute paths become opaque `f2w://` ids, secrets are redacted and every string is clipped to 600 characters (the B10 bound; the curation excerpt is cut to `EVIDENCE_EXCERPT_CHARS` first). Loopback Tiled targets, `sha256:` artifact refs, full event lists and the `corpus-audit` section names are kept. No `/v1` route has a turn's retrieved regions, so no verbatim quote is ever confirmed on `/v1`; the clip is the bound. `tests/api_v1/test_v1_disclosure.py` plants a canary in each store and asserts it never leaves.

| Area | Methods | Policy lives in |
| --- | --- | --- |
| Jobs | `submit_job`, `list_jobs`, `get_job`, `job_events`, `job_progress`, `cancel_job`, `resume_job`, `job_view` | `JobService` (kind capability, creator or `jobs.admin`, corpus scope) |
| Review | `curation_summary`, `list_cases`, `get_case`, `review_case`, `create_cost_approval`, `revoke_cost_approval` | `curation.reviewer.review_case`, `curation.approval.create_cost_approval` (human, `curation.approve`, case revision) |
| Publication | `submit_publication`, `current_plan`, `approve_publication`, `publication_pointer`, `rollback_publication` | `Publication` + `security.authorization.create_approval` / `verify_approval`, plus the H5 gate |
| Status | `audit_status`, `ops_status` | `jobs.admin` |
| Knowledge operations (T19E) | `run_knowledge_operation`, `graph_statistics`, `compare_graphs`, `paper_subgraph`, `recent_entities`, `audit_corpus`, `submit_knowledge_operation`, `knowledge_job_result`, `register_knowledge_jobs` | `operations.knowledge.KnowledgeOperations` (`chat.ask`; T19C scope rule per graph/corpus), `JobService` for the `knowledge_operation` kind (`ops.run`) |
| Scope | `notify_scope_change` | `JobService.notify_scope_change` (invalidates dependent approvals) |

The worker path uses the same rules: job stages call `StageContext.require`, which deployments wire to `security.authorization.WorkerAuthority.check` (fresh whoami with the initiator's credential on ALS). Tests: `test_v1_als.py::test_sdk_calls_apply_the_same_policy_as_http`, `::test_worker_reauthorizes_and_stops_when_the_initiator_lost_admin`, and the live `::test_real_role_downgrade_stops_the_worker_before_the_effect`.

### 2.1 Job UI mapping (`GET /v1/jobs/{id}/view`)

`ui_state` is derived from the job state and, for publication jobs, the plan/approval/receipt/pointer records. Staged never reads as published.

| `ui_state` | Meaning |
| --- | --- |
| `queued`, `running`, `paused`, `failed`, `cancelled`, `completed` | Job lifecycle |
| `awaiting_review` | Non-publication job waiting for a person |
| `staged` | A publication plan waits for approval; nothing is written to the target |
| `approved` | A valid approval for the current plan is recorded; nothing imported yet |
| `published` | Receipted and the active pointer of the target |
| `superseded` | Receipted, but no longer the active pointer (later publication or rollback) |
| `quarantined` | A package, snapshot or step output was quarantined |

### 2.2 Job event stream (`GET /v1/jobs/{id}/stream`)

Legacy SSE framing (`event:`/`data:` LF blocks, as `/chat/stream`) with `JobEvent` payloads: `event: job_event` with `id: <sequence>` per durable event (reconnect with `Last-Event-ID` or `?after=`), then exactly one `event: end` with `{"state", "reason"}`, where reason is `terminal`, `waiting` (awaiting review or paused), `timeout` or `denied`. The context is re-resolved before every poll, so a sign-out ends the stream.

## 3. Route policies (`app/api/policies.py`, DEP-19)

All 41 routes are declared with `RoutePolicy`; `app.api.auth.lookup_policy` resolves them for the legacy surface the factory extends, and `undeclared_routes` now also fails closed on routes it cannot inspect (mounts, lazily included routers). Rules:

- A route whose effect needs one fixed capability declares it, so the dependency refuses the caller first and, for an admin capability, re-runs the ALS role check.
- Routes whose capability depends on the job kind or on ownership declare no entry capability (signed-in on ALS) and rely on `JobService`; their mutating handlers re-run the ALS role check (`fresh_request_context`).
- A policy with `on_als=False` (V39-V41, the T32 folder routes) is registered by the factory on the `:local` profile only. An ALS profile has no such route, so it answers a plain 404 (`{"detail": "Not Found"}`) to everyone, signed in or not; the ALS image profiles also refuse the `F2W_FOLDER_ROOTS` setting (`forbidden_env`), and the factory ignores it with a warning if it is present anyway. The gateway (`docker/nginx.conf`) forwards every `/fair2wise/api/v1/...` path unchanged, so the 404 is the app's (`tests/gateway/test_gateway_prefix.py::test_t32_folder_routes_are_404_on_als_behind_the_gateway`).
- On `:local` the caller is the local operator with every capability (D34); no identity call is made. That includes `trace.read_any`, so `GET /traces/{turn_id}` returns the **raw** trace (full prompts with source page text) to every caller (owner decision 2026-10-12, AUD-16). This is fine for one trusted operator on loopback. Do not bind `:local` beyond loopback (`F2W_LOCAL_BIND=network`, or `--host` other than a loopback address) without accepting that everyone who can reach the address reads those traces; both `run_api` entry points log a WARNING when they do (`app.api.auth.warn_if_exposed`), and startup logs it when `F2W_LOCAL_BIND=network`. The policy itself is unchanged.

| ID | Route | Entry capability | Notes |
| --- | --- | --- | --- |
| V01 | POST /v1/jobs | signed in | kind capability in `JobService.submit`; fresh role check |
| V02 | GET /v1/jobs | signed in | only visible jobs |
| V03-V07 | GET /v1/jobs/{id}, /events, /stream, /progress, /view | signed in | creator or `jobs.admin`, corpus scope |
| V08 | POST /v1/jobs/{id}/cancel | signed in | visibility; fresh role check |
| V09 | POST /v1/jobs/{id}/resume | signed in | visibility plus kind capability or `jobs.admin`; fresh role check |
| V10-V12 | GET /v1/curation/summary, /cases, /cases/{id} | `curation.approve` | lists carry no evidence; one case carries a 600-char evidence excerpt |
| V13 | POST /v1/curation/cases/{id}/review | `curation.approve` | human only, bound to the shown revision (409 `stale_approval`), audited |
| V14-V15 | POST /v1/curation/cost-approvals, /{id}/revoke | `curation.approve` | human only |
| V16 | POST /v1/publications | `graph.publish` | frozen snapshot; H5 gate |
| V17 | GET /v1/publications/{id}/plan | signed in | job visibility |
| V18 | POST /v1/publications/{id}/approval | `curation.approve` | `create_approval` for the exact `plan_sha256` shown, approver's own credential; H5 gate |
| V19 | GET /v1/publications/pointer | `chat.read_support` | active pointer of one target |
| V20 | POST /v1/publications/rollback | `graph.publish` | human only, audited; H5 gate |
| V21 | GET /v1/audit/status | `jobs.admin` | `AuditRecorder.status` with `degraded` |
| V22 | GET /v1/ops/status | `jobs.admin` | O-07 monitor DTO |
| V23 | GET /v1/validation/judge-model | `settings.global` | deployment-wide validation judge (owner O-U2) plus the provider's live model list; added by T11B |
| V24 | PUT /v1/validation/judge-model | `settings.global` | set or clear the judge; refused unless the model is in the current live list; fresh role check (T11B) |
| V25-V29 | GET /v1/operations/statistics, /compare, /paper-subgraph, /recent, /corpus-audit | `chat.ask` | T19E: the five T19C operations inline (bounded result, `status` `ok` or `partial`); a graph or corpus outside a non-empty corpus scope is 403 (T19C rule); `/recent` on a JSON snapshot is 501 |
| V30 | POST /v1/operations/jobs | `ops.run` | submit a `knowledge_operation` job (whole population, no inline bound); fresh role check |
| V31 | GET /v1/operations/jobs/{id} | signed in | job visibility; the bounded report once computed |
| V32 | GET /v1/curation/cost-control | `curation.approve` | T31 (B5): the donor `cborg_control` switch and cap (`app/modules/curation/control.py`, `<operations>/cost_control.json`) with the `BudgetLedger.summary` spend against the cap; unreadable file reads as disabled |
| V33 | PUT /v1/curation/cost-control | `curation.approve` | human only; write-ahead audited (`cost_control`); fresh role check |
| V34 | POST /v1/curation/cases/bulk-review | `curation.approve` | T31: 1-200 decisions, each through `review_case` (human, bound to the shown revision, audited); one result per case (`ok` with the 600-char excerpt, or `error` with the code); a refused item never stops the others |
| V35 | GET /v1/vocabulary/quality | `chat.read_support` | T31: newest-first C-16 snapshots from `<operations>/vocab_quality/` (counts only; candidate vs canonical kept apart); nothing is computed on request |
| V36 | GET /v1/figures/scales | `chat.read_support` | T31: human-read scale records (`app/modules/curation/scale_records.py`) within the caller's corpus scope; a corpus outside a non-empty ALS scope is 403; identifiers and numbers only, no image, crop or path |
| V37 | POST /v1/figures/scales | `curation.approve` | human only; corpus in scope; append-only; audited (`figure_scale`); fresh role check |
| V38 | GET /v1/figures/scales/{id} | `chat.read_support` | one record; outside scope is 404 |
| V39 | GET /v1/folders/roots | `ingest.acquire`, `:local` only | T32 (B4): the root labels of `F2W_FOLDER_ROOTS` (never a path); absent (404) on ALS |
| V40 | GET /v1/folders/list?root=L&path=P | `ingest.acquire`, `:local` only | entries of one folder (directories first; links named, never followed; hidden entries skipped; 1000 entries max); `..`, absolute paths, backslashes, encoded separators and empty segments are 422; an unknown root, a missing folder, a file or a link is one fixed 404 |
| V41 | GET /v1/folders/index?root=L&path=P | `ingest.acquire`, `:local` only | bounded recursive PDF inventory (5000 documents, 20000 folders) without following links; read only: starting an ingest over it stays a job (`POST /v1/jobs`) |

Every admin capability is held only by verified Tiled admins on ALS, so all privileged `/v1` actions are admin-only there and trusted on `:local`.

## 4. H5 publication gate

`PublicationGate.rehearsal_targets` is empty by default. A publication target must be on the deployment's Tiled (`target_in_deployment`, else 403 `denied`) **and** in the allowlist (else 501 `unsupported_capability`, "gated at H5"). The allowlist is a Python constructor argument only: no environment variable, setting or request field reaches it. Tests set it to a loopback rehearsal URL with a fake `PublishStep`; no test writes to any Tiled. Central Tiled targets are never in it.

## 5. Browser-origin protection and the sign-out CSRF decision (T25C follow-up 1)

**Decision.** `POST /auth/sign-in` and `POST /auth/sign-out` (in every app that mounts them, including the legacy `create_app`) and every state-changing `/v1/` and `/auth/` request in `create_api_app` are refused with 403 `cross_site_request` before any effect when:

1. `Sec-Fetch-Site: cross-site` is present, or
2. an `Origin` header is present and is neither the request's own host (`Host`, or the gateway's `X-Forwarded-Host`/`X-Forwarded-Proto`) nor in the allowlist (the app's CORS origins; for the sign-in handlers, an explicit `allowed_origins` argument, default none: ALS is same-origin under `/fair2wise`).

A request with neither header (curl, SDK, tests) passes: it is not a browser CSRF vector and still needs a valid session or the local profile.

**Why not a session-bound CSRF token now.** Access contract section 9 asks for a CSRF token bound to the server session on ALS. The frozen UI has no sign-in page and cannot send a token, and changing it is out of scope. The session cookie is already `SameSite=Strict`, which stops cross-site requests from carrying it; the origin check adds protection against same-*site* siblings (other `*.lbl.gov` hosts), login CSRF, and simple (non-preflighted) POSTs. A synchronizer token arrives with the gateway sign-in UI (T24C); the handlers are ready to add it.

**Legacy routes (T28A, AUD-04).** Since T28A `create_app` itself applies the same origin gate to every state-changing request on the 19 legacy UI routes, and caps request bodies at 128 MiB (`LEGACY_MAX_BODY_BYTES`, the gateway's `client_max_body_size`). Same-origin browsers, the Vite development origins on `:local`, configured `cors_origins`, and non-browser callers see no change in any response; only cross-site state-changing requests (403 `cross_site_request`) and oversized bodies (413 `payload_too_large`) are refused before any route runs. On `als-dev`/`als-prod` an empty `cors_origins` no longer falls back to the localhost development origins with credentials (C7): it means no cross-origin browser access.

**Sessions are in memory (T25C follow-up 2).** `TiledSessionStore` lives in one process; a restart signs everyone out. Left as is; a persistent or shared store is a T24 deployment item.

## 6. Scope change wiring

`AccessRuntime.on_scope_change` listeners receive `{"subject", "reason"}` when:

- an ALS caller signs out (`reason: signed_out`; the subject is resolved from the session before it is dropped), or
- a verified admin is seen without the admin role at a later check (`reason: role_downgraded`).

`create_api_app` registers `F2WOperations.notify_scope_change`, which calls `JobService.notify_scope_change`; `Publication` then invalidates every valid approval the subject requested or approved. Workers stop at their next boundary through `WorkerAuthority`. Signing out one browser session invalidates that subject's approvals everywhere (fail closed).

## 7. Donor dispositions

| Port | Donor | Disposition in F2W |
| --- | --- | --- |
| I-01 | `als_knowledge_agent/server.py` | Not copied. Job, review, cost-approval and status routes map to `/v1` over `F2WOperations`. Graph/paper/vocabulary/enrichment/topic-sweep routes need their services (T05/T07/T08/T19) and are listed in Q5. Sim/reduction/dual-fit/live-session/global-reset routes are excluded. |
| I-02 | `graph/ingest_folders_routes.py` | Subprocess spawning replaced by job kinds (`ingest`) on `/v1/jobs`. Folder browsing and the read-only index inventory are `/v1/folders/roots`, `/list`, `/index` on `:local` only (T32, owner decision B4): roots fixed by `F2W_FOLDER_ROOTS`, containment by `LocalIntakePolicy`, 404 on ALS. The donor's index start/stop/status, domains and search routes (RT-64..RT-68) still wait for the ingest pipeline (H3). |
| I-03 | `graph/staged_import_routes.py` | Replaced by the `publish` job (`/v1/publications`, plan, approval, rollback, pointer). Spawned importer processes are not ported. |
| I-04 | `graph/term_campaign_routes.py` | Becomes a canonicalization campaign job kind once T07B apply paths pass H3; no kind is registered. The campaign status maps to `/v1/jobs/{id}/progress` and `/v1/curation/summary`. |
| I-05 | `src/forge/api/upload_limits.py` | Ported to `app/api/upload_limits.py`: body cap middleware (on `/v1/` and `/auth/`), `read_upload_limited`, `require_pdf`, `safe_child`. No environment read. The legacy `POST /graph/upload` keeps its frozen behaviour (Q4). |
| I-06 | `src/forge/api/auth.py` | Reference only. No admin key and no development bypass; authority comes from `security.authorization` on every path. |
| I-07 | `src/forge/api/origin_gate.py` | Ported to `app/api/origin_gate.py` with same-origin matching and `Sec-Fetch-Site`. |
| O-07 | `scripts/ingestion_monitor.py` | `GET /v1/ops/status` is the monitor DTO (jobs by state, review queue, budget, audit, publication configuration). The Streamlit dashboard itself stays a separate optional frontend (not ported). |

## 8. Deferred and open questions

- **Q1 (owner, T08B).** `app/modules/graph/imports` has no `writer_unauthorized` mapping for a writer 401/403 from Tiled (access contract section 12 asks for it). Not changed here (outside T14 ownership); the `/v1` routes would pass it through as a typed error once it exists.
- **Q2 (closed, C2 `083be8a`).** `app/public.py` `F2WClient.get_job`/`cancel_job`/`submit_ingest`/`plan_publication`/`approve_plan` forward to the shared `F2WOperations` (passed as `F2WClient(..., operations=...)`), so the same authorization runs as over HTTP; without operations they raise `UNSUPPORTED_CAPABILITY`. `tests/api_v1/test_v1_sdk_client.py` mirrors the HTTP permission tests. Only `create_literature_agent(sync_model=..., async_model=...)` still raises `NotImplementedError` (spec section 6).
- **Q3 (owner).** Should the 19 legacy UI routes also get the origin gate (access contract section 9)? It changes frozen behaviour for cross-origin dev setups.
- **Q4 (owner).** Should the legacy `POST /graph/upload` get a body cap (I-05)? It is uncapped today.
- **Q5 (T15/T17).** Remaining I-01 paper/graph routes need their services first; their dispositions belong in the T17 ledger.
- **Q6 (decided, B10).** Curation case detail shows a 600-character evidence excerpt to reviewers (admins); the owner accepted 600 characters (`OPEN_DECISIONS.md` B10). Since AUD-06 the excerpt also passes the projector's scrub (paths opaque, secrets redacted).
- **Q7 (T24).** In-memory sessions (restart signs out); persistent store and CSRF synchronizer token with the gateway sign-in UI (T24C).
- **Q8 (T10).** `JobService` has no scoped list; `F2WOperations.list_jobs` filters the store's list through the service's own visibility rule. A store-level scoped query would scale better.

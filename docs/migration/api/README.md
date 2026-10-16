# T14: F2W FastAPI factory, `/v1` jobs/review/publication API and shared service contract

- **Status:** implemented on branch `forge_merge-T14-api` for review. Not merged. Real publication stays gated at H5.
- **Code:** `app/api/main.py` (factory), `app/api/operations.py` (shared contract), `app/api/policies.py` (route policies), `app/api/routes_*.py` (thin handlers), `app/api/origin_gate.py` (I-07), `app/api/upload_limits.py` (I-05), `app/api/cli.py` (operations CLI), `app/api/auth.py` and `app/api/sessions.py` (scope-change wiring, sign-in/sign-out origin checks).
- **Tests:** `tests/api_v1/` (fake identity, `:local` and `:als-dev`), `tests/api_v1_live/` (real disposable Tiled 0.2.18; needs `F2W_TILED_PYTHON`).

## 1. Entrypoints

| Entrypoint | Behaviour |
| --- | --- |
| `app.modules.f2w_agent.api.create_app(cfg, *, cors_origins, deployment, startup_config, identity_port, credential_store, clock)` | Unchanged signature and routes: the 19 UI routes, ALS sign-in handlers, access control and disclosure projection. The frozen UI and T18A/T18B contract tests use it as before. |
| `app.api.main.create_api_app(cfg, ...)` | Calls `create_app` with the same arguments, then adds the 24 `/v1` routes, the origin gate and the body cap on `/v1/` and `/auth/`, and the scope-change wiring. Extra arguments: `operations`, `operations_root`, `publication_gate`, `max_body_bytes`, `sse_poll_seconds`, `sse_max_seconds`, `judge_settings`. |
| `python -m app.modules.f2w_agent.cli api` | Unchanged (legacy app). `api --jobs-api` serves `create_api_app`. |
| `python -m app.api.cli --root DIR jobs|cases|audit|ops ...` | Calls `F2WOperations` directly with a `cli` context (local profile only). |
| `F2WOperations` | Direct Python use (SDK, notebooks, FORGE). Same methods the routes call. |

With no `operations` argument the factory builds `build_operations(settings, <workdir>/operations)`: SQLite job, curation and audit stores, **no job kind and no publication** (real pipeline handlers need H3, real publication needs H5). Trusted wiring registers kinds on `ops.jobs` and builds a `Publication` on it.

## 2. Shared service contract (`F2WOperations`)

Every method takes the server-resolved `RequestContext` first and forwards to the T10 services. The routes add HTTP validation, context resolution and response projection only.

| Area | Methods | Policy lives in |
| --- | --- | --- |
| Jobs | `submit_job`, `list_jobs`, `get_job`, `job_events`, `job_progress`, `cancel_job`, `resume_job`, `job_view` | `JobService` (kind capability, creator or `jobs.admin`, corpus scope) |
| Review | `curation_summary`, `list_cases`, `get_case`, `review_case`, `create_cost_approval`, `revoke_cost_approval` | `curation.reviewer.review_case`, `curation.approval.create_cost_approval` (human, `curation.approve`, case revision) |
| Publication | `submit_publication`, `current_plan`, `approve_publication`, `publication_pointer`, `rollback_publication` | `Publication` + `security.authorization.create_approval` / `verify_approval`, plus the H5 gate |
| Status | `audit_status`, `ops_status` | `jobs.admin` |
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

All 24 routes are declared with `RoutePolicy`; `app.api.auth.lookup_policy` resolves them for the legacy surface the factory extends, and `undeclared_routes` now also fails closed on routes it cannot inspect (mounts, lazily included routers). Rules:

- A route whose effect needs one fixed capability declares it, so the dependency refuses the caller first and, for an admin capability, re-runs the ALS role check.
- Routes whose capability depends on the job kind or on ownership declare no entry capability (signed-in on ALS) and rely on `JobService`; their mutating handlers re-run the ALS role check (`fresh_request_context`).
- On `:local` the caller is the local operator with every capability (D34); no identity call is made.

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

Every admin capability is held only by verified Tiled admins on ALS, so all privileged `/v1` actions are admin-only there and trusted on `:local`.

## 4. H5 publication gate

`PublicationGate.rehearsal_targets` is empty by default. A publication target must be on the deployment's Tiled (`target_in_deployment`, else 403 `denied`) **and** in the allowlist (else 501 `unsupported_capability`, "gated at H5"). The allowlist is a Python constructor argument only: no environment variable, setting or request field reaches it. Tests set it to a loopback rehearsal URL with a fake `PublishStep`; no test writes to any Tiled. Central Tiled targets are never in it.

## 5. Browser-origin protection and the sign-out CSRF decision (T25C follow-up 1)

**Decision.** `POST /auth/sign-in` and `POST /auth/sign-out` (in every app that mounts them, including the legacy `create_app`) and every state-changing `/v1/` and `/auth/` request in `create_api_app` are refused with 403 `cross_site_request` before any effect when:

1. `Sec-Fetch-Site: cross-site` is present, or
2. an `Origin` header is present and is neither the request's own host (`Host`, or the gateway's `X-Forwarded-Host`/`X-Forwarded-Proto`) nor in the allowlist (the app's CORS origins; for the sign-in handlers, an explicit `allowed_origins` argument, default none: ALS is same-origin under `/fair2wise`).

A request with neither header (curl, SDK, tests) passes: it is not a browser CSRF vector and still needs a valid session or the local profile.

**Why not a session-bound CSRF token now.** Access contract section 9 asks for a CSRF token bound to the server session on ALS. The frozen UI has no sign-in page and cannot send a token, and changing it is out of scope. The session cookie is already `SameSite=Strict`, which stops cross-site requests from carrying it; the origin check adds protection against same-*site* siblings (other `*.lbl.gov` hosts), login CSRF, and simple (non-preflighted) POSTs. A synchronizer token arrives with the gateway sign-in UI (T24C); the handlers are ready to add it.

**Not changed:** the 19 legacy UI routes keep their frozen behaviour (no origin gate), because the frozen UI and its tests run from several origins. Gating them is an owner decision (open question Q3).

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
| I-02 | `graph/ingest_folders_routes.py` | Subprocess spawning replaced by job kinds (`ingest`) on `/v1/jobs`. Folder browsing/indexing needs an approved path-root policy and the ingest pipeline (H3): deferred. |
| I-03 | `graph/staged_import_routes.py` | Replaced by the `publish` job (`/v1/publications`, plan, approval, rollback, pointer). Spawned importer processes are not ported. |
| I-04 | `graph/term_campaign_routes.py` | Becomes a canonicalization campaign job kind once T07B apply paths pass H3; no kind is registered. The campaign status maps to `/v1/jobs/{id}/progress` and `/v1/curation/summary`. |
| I-05 | `src/forge/api/upload_limits.py` | Ported to `app/api/upload_limits.py`: body cap middleware (on `/v1/` and `/auth/`), `read_upload_limited`, `require_pdf`, `safe_child`. No environment read. The legacy `POST /graph/upload` keeps its frozen behaviour (Q4). |
| I-06 | `src/forge/api/auth.py` | Reference only. No admin key and no development bypass; authority comes from `security.authorization` on every path. |
| I-07 | `src/forge/api/origin_gate.py` | Ported to `app/api/origin_gate.py` with same-origin matching and `Sec-Fetch-Site`. |
| O-07 | `scripts/ingestion_monitor.py` | `GET /v1/ops/status` is the monitor DTO (jobs by state, review queue, budget, audit, publication configuration). The Streamlit dashboard itself stays a separate optional frontend (not ported). |

## 8. Deferred and open questions

- **Q1 (owner, T08B).** `app/modules/graph/imports` has no `writer_unauthorized` mapping for a writer 401/403 from Tiled (access contract section 12 asks for it). Not changed here (outside T14 ownership); the `/v1` routes would pass it through as a typed error once it exists.
- **Q2 (T04).** `app/public.py` `F2WClient.get_job`/`cancel_job`/`submit_ingest`/`plan_publication`/`approve_plan` still raise `NotImplementedError`. They can forward to `F2WOperations` without new logic; that file is T04's.
- **Q3 (owner).** Should the 19 legacy UI routes also get the origin gate (access contract section 9)? It changes frozen behaviour for cross-origin dev setups.
- **Q4 (owner).** Should the legacy `POST /graph/upload` get a body cap (I-05)? It is uncapped today.
- **Q5 (T15/T17).** Remaining I-01 paper/graph routes need their services first; their dispositions belong in the T17 ledger.
- **Q6 (owner, D19).** Curation case detail shows a 600-character evidence excerpt to reviewers (admins). Confirm the bound, or route it through the quote budget.
- **Q7 (T24).** In-memory sessions (restart signs out); persistent store and CSRF synchronizer token with the gateway sign-in UI (T24C).
- **Q8 (T10).** `JobService` has no scoped list; `F2WOperations.list_jobs` filters the store's list through the service's own visibility rule. A store-level scoped query would scale better.

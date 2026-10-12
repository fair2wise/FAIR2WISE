# Access contract: trusted-local and Tiled-authenticated ALS

- **Status:** Proposed (T25A, gate H1). Not approved. No code changed.
- **Fixed owner decisions:**
  - **D27:** F2W writes to Tiled through a configured admin account, the technical writer.
  - **D34:** `:local` is trusted full access. No sign-in. Audit actor is `local-operator`. The mode is selected server-side only, and the bind is loopback by default.
  - **D30:** `:als-dev` and `:als-prod` use native Tiled sign-in.
    - A caller who is not signed in gets sign-in and health only.
    - Ordinary users are chat-only.
    - Verified Tiled admins can do all privileged work.
    - The writer account is never used to establish the caller's role.
    - No allowlist and no OAuth2-proxy.
    - Never fall back to `local_trusted`.
- **Inputs:** `companion/AUTHORIZATION_SPEC.md`, plan section 4.13, `companion/deployment_profiles.json`, `docs/adr/tiled-deployment.md` (T20A, Proposed), `app/modules/contracts/context.py` and `changes.py` (T01 draft). Baseline facts: `current_state.md`. Disclosure classes: `docs/adr/content-disclosure.md` and `docs/migration/disclosure/route_field_map.json`.
- **Schemas:** `request_context.schema.json`, `approval_record.schema.json`. Both are consistent with the T01 Pydantic models; differences are listed in section 10.
- **Tags:** **[Proposed]** design for approval. **[Observed]** read in source or docs. **[Dependency T20A]** must be confirmed against the final Tiled 0.2.19 artifact.

## 1. Principles

1. Authority comes from **trusted startup configuration** plus, on ALS only, a **live verification against the profile's own Tiled**. Nothing in a request can raise authority: not a body, header, cookie claim, hostname, query string, model or Tool argument, saved job, or failed login.
2. One resolver builds a `RequestContext`. Every entry point uses it: FastAPI, SSE, direct Python/CLI, agent Tools, workers and FORGE delegates (LOCAL-04, AUTH-04). Hiding a control in the UI is never the enforcement.
3. Three roles are always recorded separately:
   - requester;
   - approver;
   - executor: the configured writer principal, never its token.
4. Fail closed. When identity or role evidence is unavailable, privileged effects are refused. There is no other mode, server or credential to fall back to.
5. Full access is not disclosure. Every profile applies the same D19 projection (content-disclosure ADR, LOCAL-09).

## 2. Profile and mode selection (startup only)

**[Proposed]** Names follow the T20A ADR section 4.1.

| Setting | Source | Values | Rule |
| --- | --- | --- | --- |
| `F2W_DEPLOYMENT_PROFILE` | Image entrypoint default; a trusted operator may override it at container start | `local`, `als-dev`, `als-prod` (`test` for pytest fixtures only) | Required. Unknown or empty → startup error. |
| `F2W_AUTHORIZATION_MODE` | Optional explicit value | `local_trusted`, `tiled_authenticated` | If set, must equal the profile's mode. Otherwise derived from the profile: `local` → `local_trusted`; `als-*` → `tiled_authenticated`. A mismatch is a startup error. |
| `F2W_TILED_URL` | External profiles | Fixed per profile (T20A section 5) | Must equal the profile URL. This is the only identity authority. |
| `F2W_LOCAL_BIND` | `:local` only | `loopback` (default) or `network` | See section 8. |

Startup validation runs in `security/authorization.py: load_access_policy(config)`. It produces an immutable `AccessPolicy` before the app accepts traffic.

- **S1.** If the profile is `als-dev`/`als-prod` and the mode is `local_trusted` from any source (env, config file, CLI flag, `.env`), startup is refused: `config_rejected: local_trusted not allowed on <profile>`. The `:als-*` image entrypoint never reads the ambient `.env` for these keys (T20A section 4.1, T24A).
- **S2.** If the profile is `local` and the mode is `tiled_authenticated`, startup is refused. A local profile with Tiled sign-in is not a supported variant (D34). [Proposed: owner may relax this later.]
- **S3.** If the profile is ALS and `F2W_TILED_URL` is not the profile URL, startup is refused. This replaces the silent host coercion (`tiled_graph.py:153-175`).
- **S4.** There is no runtime API that changes the profile, mode or Tiled target. `PUT /settings.tiled_uri` is removed from the writable settings (section 7). The browser `tiled_uri` value is ignored, with a typed `setting_not_writable` warning, so old UIs keep working.
- **S5.** A library or SDK factory (`application.py`) takes its `AccessPolicy` from the host's trusted configuration object, never from a delegated request (AUTHORIZATION_SPEC section 1).

## 3. RequestContext

Shape: `app/modules/contracts/context.py:RequestContext` (T01) and `request_context.schema.json`.

| Field | :local | ALS signed-in |
| --- | --- | --- |
| `profile` | `local` | `als-dev` / `als-prod` |
| `mode` | `local_trusted` | `tiled_authenticated` |
| `principal.subject` | `local-operator` | Tiled principal identifier from `whoami` **[Dependency T20A: confirm which field is stable: principal uuid vs identity id]** |
| `principal.issuer` | `null` | normalized profile Tiled base URL |
| `principal.is_admin` | `null` (no role check) | result of the live role check (section 5) |
| `principal.verified_at` | `null` | time of that check |
| `capabilities` | all (section 6) | chat group, plus the admin group if `is_admin` |
| `corpus_scope` | configured local corpora | configured corpora for this deployment |
| `request_id` | server-generated | server-generated |
| `run_id` / `deadline` / `budget_ref` | job/run binding, limits | same |
| proposed `session_id`, `tiled_target`, `actor_kind` | see section 10 | see section 10 |

An ALS caller who is **not signed in** gets **no RequestContext**. The resolver returns `Unauthenticated`. Only `GET /health` (reduced projection) and the sign-in/sign-out handlers accept that. Every other handler gets HTTP 401 `{code: "sign_in_required"}` before any model call, read or effect (AUTH-01). This matches T01, whose validator requires issuer, `verified_at` and a role result for ALS (`context.py:97-101`).

## 4. Resolution flow

```
resolve_access_context(transport_request, policy) -> RequestContext | Unauthenticated
  if policy.mode == local_trusted:
      return local_operator_context(policy, request_id, session_id)
      # no cookie/token is read; no Tiled user call; expired/non-admin/absent
      # credentials are ignored (LOCAL-01, LOCAL-05)
  # tiled_authenticated
  sess = server_session_store.get(cookie "f2w_session")      # opaque id only
  if sess is None: return Unauthenticated
  if sess.tiled_target != policy.tiled_target: drop session; return Unauthenticated   # AUTH-08
  if sess.access_expired(now): try refresh (section 5.4) else return Unauthenticated
  principal = verified principal from sess (cached role check, section 5.3)
  return RequestContext(profile, mode, principal, capabilities_for(principal), ...)
```

`local_operator_context` grants every capability in section 6. It never calls Tiled for user identity, so a test with the user-identity port disabled still passes (LOCAL-05). Local backend Tiled calls use the configured writer credential. If that credential is missing or invalid, graph-dependent operations fail with `writer_unauthorized`/`target_unavailable` and the user is never shown a sign-in screen (LOCAL-06).

## 5. ALS verification against the environment's Tiled

**[Proposed]**, with **[Dependency T20A]** items D-T1 to D-T9 below. The T20A ADR read **0.2.19rc2** because no final 0.2.19 artifact was found (its Q-T1). Every endpoint below comes from that rc2 reading. None was guessed, and each must be re-confirmed on the final artifact.

### 5.1 Sign-in

1. The backend reads `GET {tiled}/api/v1/` → `authentication.providers` and `links` (T20A section 3.1, rc2 `tiled/server/router.py:285-293`). The sign-in options are generated from that list, and nothing is hard-coded.
2. The F2W gateway serves the sign-in UI under `/fair2wise/` (AUTH-09). The user's credentials or provider redirect go to the profile Tiled's own provider flow, password or external/OIDC as the server advertises. F2W never stores passwords.
3. On success the backend holds the Tiled access/refresh tokens **server-side** in the session store, keyed by an opaque HttpOnly, Secure, SameSite cookie scoped to `/fair2wise`. The browser never receives Tiled tokens. Tokens never enter prompts, traces, graph JSON, audit records or `RequestContext`.
4. There is no generic Tiled proxy. Only sign-in/sign-out/refresh are forwarded, and none of them is an unauthenticated GraphQL/admin pass-through (AUTHORIZATION_SPEC section 3).

### 5.2 Identity

`GET {tiled}/api/v1/auth/whoami` is called with the **user's** access token (T20A C5, rc2 `tiled/server/authentication.py:1642-1680`). It is never called with the writer credential. The result gives `subject`, identities/provider, and `roles`.

### 5.3 Role check

- `is_admin` is true only if the whoami `roles` from the profile server contain the admin role **[D-T4: confirm the role name and representation]**.
- The check is cached per session for at most `F2W_ROLE_CHECK_MAX_AGE` (proposed default 60 s, an H1 approval item).
- It is **re-run unconditionally** at each protected boundary:
  - before job creation and acquisition;
  - before inference admission for privileged jobs;
  - before an approval is recorded;
  - before each import or graph write;
  - before a worker resumes an ALS job (AUTH-07).
- The writer's whoami (T20A C4) checks the writer only. It never feeds a user's `is_admin` (AUTH-06).

### 5.4 Expiry and refresh

- An expired access token is refreshed through the advertised refresh link **[D-T5]**.
- If the refresh fails, the caller is `Unauthenticated` and in-flight privileged work stops at its next boundary.
- Prior audit is kept.

### 5.5 Sign-out and revocation

- Sign-out deletes the F2W session and calls the advertised logout/revoke link where one exists **[D-T6]**.
- Server-side revocation is detected at the next whoami or refresh.

### 5.6 Must be confirmed on the final 0.2.19 (owner: T20A)

| ID | Question |
| --- | --- |
| D-T1 | Exact artifact and `about.library_version` on tiled-staging/tiled (T20A Q-T1). |
| D-T2 | `about.authentication` shape: provider list, modes (password vs external), and the link names for whoami, refresh, logout and apikey on the deployed config. |
| D-T3 | `whoami` response fields: which identifier is stable across sign-ins (principal uuid vs `identities[].id`); the issuer representation. |
| D-T4 | How admin is represented (`roles` entry name, scopes), and how `authentication.tiled_admins` maps to it on the central servers. |
| D-T5 | Access/refresh token lifetimes, refresh endpoint and rotation behaviour. |
| D-T6 | Whether logout/revocation invalidates outstanding access tokens immediately, or only at expiry. This sets how long a revoked admin can still act; it is the bound for `F2W_ROLE_CHECK_MAX_AGE`. |
| D-T7 | Whether external (OIDC) providers need a redirect URI under `/fair2wise`, and who registers it. |
| D-T8 | Whether per-user API keys exist on the central servers, so non-browser clients such as the model-compatible proxy can authenticate. Otherwise the proxy is browser-session only, or not exposed on ALS. |
| D-T9 | Whether the F2W writer is `admin` or a scoped service principal (T20A Q-T3). This affects `executed_as` and target scope only, never user roles. |

## 6. Capability groups

**[Proposed]** The capability strings are listed in `request_context.schema.json $defs.Capability`. T01 `ToolSpec.required_capabilities` uses the same strings.

| Group | Capabilities | :local | ALS not signed in | ALS user | ALS verified admin |
| --- | --- | --- | --- | --- | --- |
| Public | `health.read` (reduced projection), `auth.session` | yes (`auth.session` unused) | yes | yes | yes |
| Chat | `chat.ask`, `chat.read_support` (graph read, node detail, node search, neighborhood, publication search, bounded read-only Tools, citations), `session.own`, `trace.read_own`, `settings.personal`, `telemetry.write`, `annotation.write`, `chat.decline_pending` | yes | no | yes | yes |
| Admin | `settings.global`, `session.any`, `trace.read_any`, `ingest.acquire`, `ingest.extract`, `figures.process`, `ops.run`, `graph.edit`, `graph.upload`, `curation.approve`, `json.build`, `graph.publish`, `jobs.admin` | yes | no | no | yes |

Rules:

- `chat.read_support` never includes `source.read` (content-disclosure ADR section 2). Read-only Tools for ordinary users must have no `WRITE_*`, `DESTRUCTIVE` or `NETWORK` acquisition effect (`contracts/tools.py:19-29`).
- **Chat must not offer ingestion to ordinary users.** On ALS, a non-admin `/chat` gets no download/extraction pending card. It gets an answer or an insufficiency message. `/chat/action decision=yes kind=download|extraction` requires `ingest.acquire`/`ingest.extract`, and a non-admin is refused **before** job creation, download, extraction or budget reservation (AUTH-03).
- Ordinary chat recording (sessions, traces, telemetry) is F2W-local state. It is **never** written to Tiled with the writer key (AUTH-11).
- On :local, "all" means every function. Explicit approval steps, scientific validation, rate limits, immutable JSON and D19 projection still apply (LOCAL-09).

## 7. Route placement (34 inventoried routes)

Key: **A** allowed; **R** reduced/projected form; **D** denied (401 if not signed in, 403 `denied` if signed in without the capability); **N/E** not exposed in that image.

| ID | Route | Capability | :local | ALS anon | ALS user | ALS admin | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R01 | GET /health | `health.read` | A | R | R | A | The reduced form has status and readiness only: no paths, no model, no pending state (route_field_map R01). |
| R02 | GET /settings | `settings.personal` | A | D | R | A | Users see their own session settings and non-sensitive config. Paths, `tiled_uri` and `tiled_error` are admin only. The probe uses the writer path, never a user-chosen URL. |
| R03 | PUT /settings | personal fields: `settings.personal`; global fields: `settings.global` | A | D | R | A | Personal = backend/model/provider per session (LLM-01). Global = graph selection/paths, `source_rag`, `use_live_tiled`, `targeted_max_pages`, `kg_query_*`. `tiled_uri` is not writable in any profile (S4). Settings become per-session for users; today they are process-global (`current_state.md`). |
| R04 | POST /session/reset | `session.own` (`session.any` for others) | A | D | A (own) | A | Session ids are bound to the principal. |
| R05 | DELETE /session/{id} | `session.own` / `session.any` | A | D | A (own) | A | |
| R06 | GET /graph | `chat.read_support` | A | D | A | A | Projected GraphPayload. |
| R07 | POST /graph/nodes/search | `chat.read_support` | A | D | A | A | |
| R08 | GET /graph/neighborhood/{id} | `chat.read_support` | A | D | A | A | |
| R09 | GET /graph/node/{id} | `chat.read_support` | A | D | A | A | `json_graph_path` query: admin only on ALS (selects files). |
| R10 | PATCH /graph/node/{id} | `graph.edit` | A | D | D | A | Re-check admin at write time (AUTH-04, TIL-05). |
| R11 | POST /graph/upload | `graph.upload` | A | D | D | A | |
| R12 | POST /chat | `chat.ask` | A | D | A | A | No ingestion pending card for non-admins. |
| R13 | POST /chat/action | `chat.decline_pending` for `no`; `ingest.acquire`/`ingest.extract` for `yes` | A | D | `no` only | A | `yes` is an explicit approval by the caller. It is recorded with requester and approver. |
| R14 | POST /publications/search | `chat.read_support`; `include_external=true` also counts as a bounded read | A | D | A | A | The OpenAlex call is a read; it is rate-limited. |
| R15 | POST /chat/stream | as R12 | A | D | A | A | |
| R16 | POST /chat/action/stream | as R13 | A | D | `no` only | A | |
| R17 | GET /traces/{turn_id} | `trace.read_own` / `trace.read_any` | A (projected) | D | own, projected | any, projected | The raw trace is never served (content ADR section 4.3). |
| R18 | POST /telemetry/ui | `telemetry.write` | A | D | A | A | The actor is recorded from the context, not the payload. |
| R19 | POST /annotations | `annotation.write` | A | D | A (own turns) | A | `annotator` is replaced by the verified subject or `local-operator`. Owner question O-A1: should ALS evaluation labels be admin-only? |
| R20, R21 | POST /api/chat, /v1/chat/completions (app copy) | `chat.ask` | A (loopback) | D | A if D-T8 allows token auth, else N/E | same | Mounted under `/fair2wise/model-api` only where exposed. Same disclosure projection as /chat. |
| R22-R25 | GET /api/tags, /api/version, /v1/models, /api/ps (app copy) | `chat.ask` | A | D | A | A | Static, but D30 allows only sign-in and health before sign-in. |
| R26-R31 | `scripts/app/modules/kg_rag_api.py` duplicate (6 routes) | n/a | N/E | N/E | N/E | N/E | Proposed: not packaged in any image; developer script only. |
| R32-R34 | Paper Finder (/, /detected, /action) | `ingest.acquire` (operator tool) | A (loopback CLI, not via the gateway) | N/E | N/E | N/E | Not in ALS images. It keeps its loopback-only bind (`paper_finder.py:61-82`). |
| new | sign-in / sign-out / refresh handlers | `auth.session` | N/E (no local login) | A | A | A | Under `/fair2wise/`. |

Default-deny: a route without a declared capability is refused on ALS. A test enumerates `app.routes` against this table (AUTH-04, DEP-19).

## 8. Local exposure (loopback default)

- `:local` publishes the UI/API gateway on `127.0.0.1` only by default (LOCAL-08). Inside the container the gateway may listen on `0.0.0.0` so Docker can forward. The published host port binds `127.0.0.1:<port>`. The launcher and CLI already default to `127.0.0.1` (`cli.py:133`, `start_agent_backend.sh:81`).
- Wider exposure needs `F2W_LOCAL_BIND=network`. At startup the app then logs, and shows in `/health` and the UI banner: "every user who can reach this address has full application access, including publication to local Tiled."
- The managed Tiled server stays on `127.0.0.1:8765` and is not proxied through `/fair2wise` (T20A section 4).
- `docker-compose.yml:51`-style `8090:8090` all-interface publishing is not an approved `:local` default.

## 9. Browser-origin protection (all profiles)

**[Proposed]** This is not sign-in, so it does not conflict with D34.

- State-changing routes check `Origin`/`Host` against the configured gateway origin. On `:local` with loopback, `Host` must be `127.0.0.1`/`localhost` on the published port. This blocks DNS rebinding and cross-site POSTs, which otherwise get full local access.
- CORS: no wildcard with credentials. ALS is same-origin under `/fair2wise`.
- ALS state-changing requests also carry a CSRF token bound to the server session.

## 10. Consistency with `app/modules/contracts` (T01) and disagreements

The schemas mirror `context.py:Principal/RequestContext` and `changes.py:ApprovalRecord`. Field names, types, defaults, `extra="forbid"` and the profile/mode validators all match. The examples in both schemas validate under jsonschema **and** under the Pydantic models. This was checked with `docs/migration/disclosure/evidence/check_schemas.py`; jsonschema 4.26.0, pydantic 2.13.4.

Disagreements for the integrator (the Python files were not edited):

| # | Item | Pydantic today | Schema / this contract | Effect |
| --- | --- | --- | --- | --- |
| C1 | Local `requested_by` | Not constrained (`changes.py:130-136` checks `approved_by` only) | `const "local-operator"` | A local record with `requested_by="someone"` passes Pydantic, fails the schema (verified). Proposal: add the check. |
| C2 | ALS `requested_by` | May be `local-operator` | `not "local-operator"` | Same class of gap. |
| C3 | ALS admin result | `ApprovalRecord` has `role_checked_at` but no role-check **result** | Proposed `approver_is_admin` (must be true) and `role_check_evidence {method: tiled_whoami, tiled_target, identity_provider, roles, tiled_library_version}` | Without these, an approval cannot prove the approver *was* admin, only that a check happened. |
| C4 | Requester verification | No requester issuer | Proposed `requester_issuer` | AUTHORIZATION_SPEC asks for a verified requester on ALS. |
| C5 | Operation id | No job/operation reference | Proposed `operation_id` | Distinguishes local-operator operations without inventing identity (LOCAL-03). |
| C6 | `executed_as != approved_by` | Enforced (`changes.py:144-145`) | JSON Schema cannot express cross-field inequality. The schema only forbids `executed_as="local-operator"` on local. | Verified: the schema accepts `executed_as == approved_by` on ALS, Pydantic rejects it. Keep it as a Pydantic-only rule. |
| C7 | Unauthenticated ALS caller | Cannot be represented (validator requires issuer/verified_at/is_admin) | No RequestContext; the resolver returns `Unauthenticated` | Consistent by design. It must stay so: never mint a placeholder principal. |
| C8 | Principal evidence | `subject, issuer, is_admin, verified_at` | Proposed `identity_provider`, `roles`, `credential_expires_at` | Needed for revalidation and audit. They depend on D-T3/D-T4/D-T5. |
| C9 | Context binding | No session/target/actor fields | Proposed `session_id`, `tiled_target`, `actor_kind` | Needed for own-session/trace checks (R04/R05/R17) and environment binding (AUTH-08, LOCAL-10). |
| C10 | Capability vocabulary | Free `FrozenSet[str]` | Enumerated in the schema (section 6) | The integrator should freeze the names, or adopt another vocabulary consistently in `ToolSpec.required_capabilities`. |
| C11 | `expires_at` on ALS approvals | Optional | Optional; **[Proposed]** required on ALS with a short TTL | Policy choice for H1. |
| C12 | `EvidenceRef.match_type="normalized"` (`contracts/evidence.py:16-23`) | Exists | Content ADR Q1: normalized matches may be displayed as verbatim only under the recorded normalization profile (whitespace and hyphenation only) | Make sure the T21B projector reads `match_type` this way. |

Any record that sets a proposed extension field fails Pydantic (`extra="forbid"`) until C3, C4, C5, C8 and C9 are adopted or rejected.

**Integrator resolution (2026-10-12, `CONTRACT_VERSION` 1.0.0-draft.2):**

| # | Resolution |
| --- | --- |
| C1, C2 | Adopted. Local approvals require `requested_by == local-operator`; ALS approvals forbid local-operator as requester or approver. |
| C3 | Adopted. `approver_is_admin` must be `true` on ALS and `null` on local; `role_check_evidence` (`RoleCheckEvidence`, method `tiled_whoami`) added, optional. |
| C4 | Adopted and required on ALS: `requester_issuer`. |
| C5 | Adopted: optional `operation_id`. |
| C6 | Kept as a Pydantic-only rule (JSON Schema cannot express it). |
| C7 | Unchanged by design: no RequestContext for signed-out ALS callers. |
| C8 | Adopted: `Principal.identity_provider`, `roles`, `credential_expires_at`, all forbidden on local-operator. Field names still need confirming against the final Tiled build (D-T3 to D-T5). |
| C9 | Adopted: `RequestContext.session_id`, `tiled_target`, `actor_kind` (`ActorKind`). |
| C10 | Adopted: the section 6 vocabulary is frozen as `CAPABILITIES` (`CHAT_CAPABILITIES` + `ADMIN_CAPABILITIES`); unknown names are rejected. |
| C11 | Open: H1 policy choice. |
| C12 | Adopted in the `MatchType` docstring; the T21B projector must follow it. |

`check_schemas.py` re-run: all examples valid in both; every negative case rejected by both except C6 (Pydantic only).

## 11. Approvals, jobs and FORGE

- An `ApprovalRecord` is created only by an explicit human action:
  - local: the operator clicks; recorded as `local-operator`;
  - ALS: a verified admin acts, after a fresh role check.
  It binds to `plan_id`, `plan_sha256`, `target` and `scope` (`changes.py:148-157`). Before that action, no record exists. A model or Tool never creates one.
- A worker carries the recorded `RequestContext` (`actor_kind=worker`, `run_id`). Local workers need no token refresh. ALS workers revalidate the initiating subject's admin status at each protected boundary. Downgrade, expiry or revocation stops new effects; prior audit is kept (AUTH-07).
- A local approval, job grant or receipt can never authorize an ALS target. The receiving ALS deployment applies its own profile, verified admin and target (LOCAL-10, AUTH-08).
- FORGE calls inherit the receiving F2W deployment's policy. On ALS, a delegated call carries the verified caller context. A non-admin caller inside an admin-capable host cannot trigger ingestion or publication, or receive full papers (AUTH-10).

## 12. Failure modes (all fail closed)

| Condition | Behaviour | Never |
| --- | --- | --- |
| Not signed in (ALS) | 401 `sign_in_required`; health reduced | Run a model call, read, or effect |
| Access token expired, refresh fails | `Unauthenticated`; in-flight privileged work stops at the next boundary | Continue on a cached role past `F2W_ROLE_CHECK_MAX_AGE` |
| Revoked session / signed out | Same as expiry, detected at the next whoami/refresh | Reuse a stored token |
| Wrong issuer (staging session or token at prod, or any other server) | Session dropped; 401 | Validate against another server (AUTH-08) |
| Role downgrade | Next protected boundary returns 403 `denied`; the job pauses as `denied` | Finish the remaining writes |
| Tiled identity outage / whoami 5xx / timeout | Privileged actions 503 `unavailable` (retryable); chat for already-verified users continues only within the role-cache age; new sign-ins fail | Switch to `local_trusted`, another server, or the writer credential (LOCAL-07) |
| Request claims trust (`X-Local-Trusted`, `mode=local_trusted`, `is_admin=true`, local hostname, saved local grant) | Ignored; logged | Raise authority (AUTH-06, LOCAL-07) |
| Writer credential missing/invalid | `writer_unauthorized` on graph operations only; local users see an operation error, not sign-in | Use a user token to write; retry with another key |
| Startup: ALS + `local_trusted`, unknown profile, profile/URL mismatch | Refuse to start | Default to full access |
| Clock skew beyond tolerance on token expiry | Treated as expired | Accept |

## 13. Tests (cases in `binary_case_inventory.json`; all `not_run`)

| Case | Proves |
| --- | --- |
| LOCAL-01, LOCAL-02, LOCAL-05 | All 34 routes and all admin workflows work on :local with no, expired or non-admin credentials, with the user-identity port disabled |
| LOCAL-03, DEP-12 | Local ApprovalRecord: `local-operator` x2, writer `executed_as`, null issuer and role time (schema + Pydantic) |
| LOCAL-04 | Same policy via FastAPI, SDK, Tools and worker resume |
| LOCAL-06 | Missing local writer → graph-op error, no sign-in prompt, no credential in the browser |
| LOCAL-07 | Crafted trust claims on ALS are ignored; ALS + `local_trusted` startup refused; outage fails closed |
| LOCAL-08 | Default publish is `127.0.0.1`; `network` needs the explicit setting and shows the warning |
| LOCAL-09 | Disclosure projection is unchanged on :local |
| LOCAL-10, AUTH-08 | Local or staging approvals and receipts are rejected by prod |
| AUTH-01 | Anonymous ALS: only health (reduced) and sign-in |
| AUTH-02 | Non-admin chat, own sessions, own projected traces, personal settings |
| AUTH-03, AUTH-04 | Non-admin `yes`/edit/upload/global settings are denied before effects, via every entry point |
| AUTH-05 | Admin edit/import with verified requester/approver and separate `executed_as` |
| AUTH-06 | Forged `is_admin`, headers and writer identity don't elevate |
| AUTH-07 | Expiry, logout, downgrade and revocation block new effects mid-job |
| AUTH-09 | `/fair2wise` sign-in round trip; no broad Tiled proxy |
| AUTH-10 | FORGE delegation keeps the caller's capability |
| AUTH-11 | Chat recording performs zero Tiled writes |
| DEP-19 | Route table enforced; old UI controls fail truthfully |

ALS cases run first against fake identity (T25B), then against an application-owned disposable Tiled with real admin and non-admin principals (T25C), before H4/H6.

## 14. Open items

- **O-A1.** Should ALS `/annotations` be admin-only?
- **O-A2.** `F2W_ROLE_CHECK_MAX_AGE` value (depends on D-T6).
- **O-A3.** Whether the model-compatible proxy is exposed on ALS at all (depends on D-T8).
- **O-A4.** Owner may decide that `:local` + `tiled_authenticated` is a supported variant (S2).
- **T20A dependencies:** D-T1 to D-T9 (section 5.6).

# Current authentication and authorization state (baseline 222b1e7)

- **Status:** Inventory for T25A (gate H1). Describes the frozen baseline export only; nothing was changed.
- **Line basis:** file:line against `222b1e7`. On `forge_merge` HEAD, `app/modules/f2w_agent/api.py` shifts by +6 after line 140 and slightly more after line 3530 (`batch_id`); no auth code was added there.
- **Tags:** **[Observed]** read in source. **[Inferred]** reasoned, not run. No server was started.

## 1. Summary

There is **no caller authentication or authorization** on any of the 34 inventoried HTTP routes. Every route, including graph editing, graph upload, global settings, paper download/extraction/Splash re-import and full trace retrieval, is open to anyone who can reach the port. Protection today depends only on the bind address and published port. That is loopback in the default launcher and in `compose.yaml`, but **all interfaces** in `Dockerfile` and `docker-compose.yml`. The only credential in the system is one process-wide Tiled API key used for backend Tiled calls, and a request can redirect it to another host.

## 2. F2W agent API (`app/modules/f2w_agent/api.py`, 19 routes)

| Item | Observed | Ref |
| --- | --- | --- |
| Auth dependencies | None. No `Depends`, `Security`, API-key header, bearer, cookie or session check in `create_app`; grep for these finds nothing. | `api.py:6611-7031` |
| CORS | `CORSMiddleware(allow_origins=cors_origins or DEFAULT_CORS_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])`. The default origins are `localhost`/`127.0.0.1` on 5173/5175. CORS only limits which browser pages can read responses. It authenticates nothing, and non-browser clients ignore it. | `api.py:6603-6608,6616-6622`; launcher adds the same four origins, `scripts/start_agent_backend.sh:277-280`; CLI `--cors-origin`, `app/modules/f2w_agent/cli.py:135-140` |
| Bind | CLI default `--host 127.0.0.1` (`cli.py:133`). Launcher `HOST="${F2W_AGENT_HOST:-127.0.0.1}"` (`start_agent_backend.sh:81`). Image sets `F2W_AGENT_HOST=0.0.0.0` (`Dockerfile:41`). `compose.yaml:58` publishes `127.0.0.1:8090:8090`. `docker-compose.yml:51` publishes `8090:8090` on all host interfaces with `F2W_TRACE_ENABLED: "1"` (`:43`). | as listed |
| Gateway | nginx proxies `/api/` to `agent:8090/` with no auth (`docker/nginx.conf:14-24`). UI published on loopback (`compose.yaml:84`). | |
| Browser | The UI sends no credentials or Authorization header and has no login flow; grep of `ui/src` for `credentials:`, `Authorization`, `login` finds nothing. API base defaults to `http://127.0.0.1:8090` (`ui/src/app/components/data/liveAgent.ts:221`). | |
| Graph edit `PATCH /graph/node/{id}` | Only checks: graph source is not `json`, and the node and Splash entity exist. Then it writes Splash-links through unauthenticated GraphQL and saves the session graph. | `api.py:2978-3046`; `kg_update.py:57-77,270-299` |
| Graph upload `POST /graph/upload` | Writes caller JSON to `workdir/uploads/<basename>.json`. Shape is checked (`things`/`associations` lists); no size limit, no auth. | `api.py:3374-3394` |
| Settings `PUT /settings` | Process-global. Any caller changes backend/model, graph selection, `source_rag`, `use_live_tiled`, `targeted_max_pages` (1..100) and `tiled_uri` for every user. | `api.py:410-428,2726-2860` |
| Ingestion via `POST /chat/action[/stream]` | A `yes` on a pending card downloads papers, runs extraction, rebuilds the KG, wipes and re-imports Splash, and reloads retrieval. "Approval" is any caller's `decision=yes`. | `api.py:228-254,4885-5016,5504-5702,5919-6000`; `kg_update.py:119-200` |
| `auto_approve` | Not reachable over HTTP. `ChatRequest` has no such field (`api.py:124-145`). It is set only by CLI `--auto-approve` (`cli.py:110,167`) or by direct Python. | |
| Sessions | `session_id` is caller-chosen (`api.py:128-133`). It has no owner binding: any caller can reset or delete any session. | `api.py:6657-6668,4230-4258` |
| Traces | `GET /traces/{turn_id}` returns the full detail file to anyone. Turn ids are caller-chosen if they match the pattern, else uuid4. | `api.py:7001-7007`; `trace.py:346-350,778-823` |
| Telemetry / annotations | Open writes to `runs/traces/*`. `annotator` is a free-text label, not an identity. | `api.py:275-283,7009-7029`; `trace.py:789-810` |
| Secret handling in traces | Values of env vars whose names match `API_KEY|SECRET|TOKEN|PASSWORD|AUTHORIZATION|CREDENTIAL` are redacted before write. | `trace.py:44-47,206-253` |

## 3. Tiled

| Item | Observed | Ref |
| --- | --- | --- |
| Local Tiled config | `authentication: single_user_api_key: "${TILED_API_KEY}"`, `allow_anonymous_access: true`, CORS origins `:8090`/`:5175`. Per the T20A ADR, anonymous callers get `read:metadata`/`read:data`, so graph reads work unsigned and only writes need the key. | `storage/tiled_config.yml:14-22`; `docs/adr/tiled-deployment.md` section 2 |
| Hard-coded default key | Launcher exports `TILED_API_KEY="${TILED_API_KEY:-devonlykeychangeinproduction000…}"` and starts Tiled on `127.0.0.1:8765` if `tiled` imports. | `scripts/start_agent_backend.sh:216-231` |
| Backend client credential | One process-wide key from `os.environ["TILED_API_KEY"]`, sent as `Authorization: Apikey <key>` on every GraphQL call. | `app/modules/tiled_graph.py:183-195,748-768,982` |
| Request-selected target | `PUT /settings` `tiled_uri` goes through `coerce_local_tiled_uri`. This replaces `*.als.lbl.gov` with the default but **accepts any other http(s) host**. The settings probe (`GET /settings`, `tiled_graph.py:225-247`) and live lookups (`:962-1030`) then send the process key to that host. **[Inferred]** A caller can capture the backend Tiled key by pointing `tiled_uri` at a server they control and then calling `GET /settings`. | `api.py:2774-2779,2713-2718`; `tiled_graph.py:141-175,241` |
| Key redaction in errors | `_safe_error` removes the key value from error strings. | `tiled_graph.py:714-728` |
| User identity | None. F2W never calls Tiled `whoami` or any sign-in endpoint. There is no notion of an end user, role or admin anywhere in `app/`. | grep `whoami` in `app/` finds nothing |
| ALS hosts | Deliberately unreachable: `coerce_local_tiled_uri` and `tiled_sim.py:290-303` refuse ALS/non-loopback hosts. | `tiled_graph.py:153-175` |

## 4. Other HTTP surfaces

| Surface | Auth | Bind | Ref |
| --- | --- | --- | --- |
| Model-compatible proxy (`kg_rag_api.py`, 6 routes; duplicate in `scripts/app/modules/kg_rag_api.py`) | None | `uvicorn.run(..., host="0.0.0.0", port=11435)`, started by `--api` | `app/modules/kg_rag_api.py:245-330,1763,1797`; `scripts/app/modules/kg_rag_api.py:220-305` |
| Paper Finder (3 routes) | None, but **loopback enforced**: `local_bind` raises unless host is `127.0.0.1`, and it blocks ports 5174/5175/8090 | 127.0.0.1 | `scripts/paper_finder.py:61-82,795-819` |
| Splash-links GraphQL (writes from `kg_update.py`) | None. Requests carry only `Content-Type`. | `localhost:8081` default | `app/modules/f2w_agent/kg_update.py:57-77,220-226` |
| term_extractor agent dashboard (not in route inventory) | None; includes `POST /shutdown/<agent_id>` | `0.0.0.0:8000` default | `app/modules/term_extractor/dashboard.py:1080-1106,1273-1323`; `user_agent.py:23-27`. [Inferred] Dormant: no launcher reaches it. |

## 5. Gap to D30 / D34

| Requirement | Baseline | Gap |
| --- | --- | --- |
| D34: profile and mode chosen at startup only; reject request-selected trusted mode | No profile or mode concept. Behaviour depends on env vars read per call (`TILED_URI`, `TILED_API_KEY`, `F2W_LIVE_TILED`) and on request-mutable runtime settings. | Add `F2W_DEPLOYMENT_PROFILE`/authorization mode startup validation. Remove request control of the Tiled target. |
| D34: local full access, no sign-in, audit actor `local-operator` | Full access exists by accident: nothing is checked. There is no audit actor; approvals are anonymous `decision=yes`. | Inject `local_operator_context`. Record `requested_by`/`approved_by`/`executed_as`. Keep explicit approvals. |
| D34: loopback-default bind | Launcher and CLI default to loopback. The image binds `0.0.0.0` (`Dockerfile:41`), `docker-compose.yml:51` publishes 8090 on all interfaces, and the model proxy and the dashboard hard-code `0.0.0.0`. | `:local` must publish loopback only by default. Wider exposure needs explicit operator config and a warning. |
| D30: not signed in → sign-in and health only | Everything is open. | Add native Tiled sign-in, a session, and a default-deny route guard. |
| D30: ordinary users chat-only | All routes are open, including edit, upload, global settings, ingestion and traces. | Capability gate per route (access_contract.md section 7). |
| D30: verified Tiled admins only for privileged work | No identity or role lookup. | Verify `whoami` roles on the profile server for each protected effect. |
| D27/D30: never use the writer account to establish the caller's role | The only identity is the writer key, and it can be redirected. | Separate the writer credential (secret file, trusted path only) from user credentials. |
| D30: no allowlist / OAuth2-proxy; never fall back to local_trusted | Not applicable yet. The current hostname coercion silently swaps the target, which is a forbidden fallback in spirit (T20A). | Fail closed with typed errors. Never swap target or mode. |
| Session/trace ownership (AUTH-02 "own session history") | None. | Bind session ids and trace turn ids to the principal. |
| CSRF / cross-origin effects | `allow_credentials=True` with no credentials in use. Any page the browser loads can POST to `127.0.0.1:8090`, because a simple-request POST with a JSON body is only blocked from *reading* the response. [Inferred] Pydantic JSON parsing rejects `text/plain` bodies, so the realistic vectors are DNS rebinding and permissive origins. | Origin/Host checks on state-changing routes in all profiles (access_contract.md section 9). |

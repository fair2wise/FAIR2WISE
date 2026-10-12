# T18A: frozen F2W frontend API, SSE and persistence contracts

- **Gate:** H1. **Task:** T18A. **Status:** evidence for review. Not owner approval.
- **Baseline:** `222b1e7b130567dbf4a3242697ac2b465794a38d`, captured from a clean `git archive` export with no `.env`.
- **Frozen UI:** `ui/` tree `75a19b9823bc924a6391311191d3e41a5b4917ff`, the same at baseline and at the current `forge_merge` HEAD (`git rev-parse 222b1e7:ui HEAD:ui`). `vite build` dist manifest sha256 `1e7ee4ef887f7ac88b9f51617f2c70f744ac2b11c29fe6d0af7735621e3c3178` (from `docs/migration/reviews/H0/review_packet.md:22`; not rebuilt here).
- **Environment:** Python 3.12.11; fastapi 0.141.1, pydantic 2.13.4, starlette 1.3.1, httpx 0.28.1, pytest 9.1.1 (the H0 baseline venv: `requirements/dev.txt` + `requirements/semantic.in`). No Node step was needed for these Python deliverables.

## 1. What was captured

| Deliverable | Path | Content |
|---|---|---|
| UI OpenAPI | `docs/migration/ui_contracts/openapi_baseline.json` (= `tests/contracts/legacy_ui/fixtures/openapi_ui.json`) | `app.openapi()` of `f2w_agent.api.create_app` built in-process. OpenAPI 3.1.0, title `FAIR2WISE Agent Pipeline API` 0.1.0, 17 paths / 19 operations, 27 component schemas. sha256 `c01f7de5...331e4d`. |
| Model-API OpenAPI | `docs/migration/ui_contracts/openapi_model_api_baseline.json` (= `fixtures/openapi_model_api.json`) | `kg_rag_api.create_fastapi_app` with a fake chat client. Title `KG-RAG Chat Proxy`, 6 paths. sha256 `7d5e9bb8...1c81c2`. |
| Route registry | `tests/contracts/legacy_ui/fixtures/routes.json` | `app.routes` of both apps, including the FastAPI `/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc` extras. |
| HTTP fixtures | `tests/contracts/legacy_ui/fixtures/http/*.json` (66) | One recorded request/response pair per step. 58 against the UI app, covering all 19 routes, plus 8 against the 6 model routes. Index with status per case: `fixtures/index.json`. |
| SSE fixtures | `tests/contracts/legacy_ui/fixtures/sse/*.sse` (12) | Raw sanitized response bytes with exact LF framing (`.gitattributes`: `*.sse -text`). |
| Browser persistence | `docs/migration/ui_contracts/persistence_keys.json` (= `fixtures/persistence_keys.json`) | 6 storage keys with value schema, defaults, readers/writers and migration rules, each with file:line. |
| Contract suite | `tests/contracts/legacy_ui/test_legacy_ui_contract.py`, `_legacy_ui_harness.py`, `record_fixtures.py` | Replays every fixture against a fresh app and asserts the frozen shapes. |
| ADR | `docs/migration/ui_contracts/legacy_splash_to_tiled_adr.md` | Proposed legacy `splash`/`splash_links` to Tiled mapping; 6 STOP items. |
| CI plan | `docs/migration/ui_contracts/ci_plan.md` | Proposed non-deploy `forge_merge` workflow (YAML in the doc only). |

### 1.1 How the apps were built (isolation)

`_legacy_ui_harness.install` (all in-process; nothing listens on a port):

- Refuses to run if `.env` exists next to the code or in the CWD. `kg_rag_api.py:81`, `extract_terms.py:37` and `f2w_agent/cli.py` call `load_dotenv()` at import.
- Scrubs credential and endpoint env vars. Forces `KG_RAG_RETRIEVAL_BACKEND=lexical`, `F2W_SOURCE_RAG=0`, `F2W_LIVE_TILED=0`, and `F2W_TRACE_ENABLED=1` with `F2W_TRACE_DIR` in a temp workspace. Changes CWD into the workspace, which holds a synthetic `storage/kg/matkg_contract_v1.json` (3 nodes, 2 edges).
- Patches `socket.socket.connect`/`connect_ex`/`create_connection` to raise, so any network attempt fails the run.
- Fakes, following the existing `tests/test_f2w_api.py` patterns: `RetrievalAgent` (verdict chosen from the question text), `DownloadAgent` (2 synthetic candidates; candidate 2 fails to download), `ExtractorAgent` (targeted), `EvidenceDebateAgent`, `term_extractor.clients.make_chat_client` (one fake used by the real orchestrator classifier, `_chat_completion`, rewrite and so on), all `kg_update.splash_*` calls (in-memory Splash store), `load_splash_graph`, `rebuild_kg`, `splash_reimport`, `lookup_experiment_identity` (fake Tiled lookup graph) and `_search_openalex_publications` (raises). Everything else is real baseline code: `create_app`, `AgentPipelineService`, `WorkflowOrchestratorAgent`, session memory, workflow state, traces, `krag.KnowledgeGraph` and lexical `retrieve_nodes` for `/publications/search`, and the graph payload builders.
- The app is constructed as the launcher does (`f2w_agent/cli.py:175-177`) with `kg_mode="splash"` (the editable UI mode), `workflow_mode=agentic` and `extraction_mode=targeted`. The flow then switches to JSON mode through `PUT /settings` and back.
- Sanitizing: the temp workspace becomes `<WORKSPACE>`, the repo root `<REPO>`, the hostname `<HOST>` and `$HOME` `<HOME>`. Wall-clock fields (`started_at`, `finished_at`, `elapsed_seconds`, `created`, `*_ms`, `chatcmpl-<ms>` ids) become `<VOLATILE>`. Turn and session ids are fixed client values. SSE bytes needed no volatile substitution.

## 2. Commands and results

Recording (clean export copy, run twice and diffed):

```bash
S=<scratchpad>; EXP=<fresh copy of git archive 222b1e7 with tests/contracts/legacy_ui added>
cd $EXP && env -i PATH=$S/venv-baseline/bin:/usr/bin:/bin HOME=<tmp> TMPDIR=<tmp> LANG=en_US.UTF-8 \
  $S/venv-baseline/bin/python tests/contracts/legacy_ui/record_fixtures.py
```

Result: exit 0 on every run. Repeat runs gave byte-identical `fixtures/` trees (`diff -r` empty), three times before the final notes edit and twice after it.

Contract suite (fresh copy of the export, `ui/node_modules` excluded, `tests/contracts/legacy_ui` copied in, no `.env`):

```bash
env -i PATH=<venv>/bin:/usr/bin:/bin HOME=<tmp> <venv>/bin/python -m pytest tests/contracts/legacy_ui -q
```

Result at `222b1e7`: **138 passed, 0 failed, 0 skipped**, 6 warnings (SWIG/httpx deprecation only), about 8 s.

Sanity checks:

- Mutation test: switching `_sse` to CRLF framing and changing the `turn_started` message in the scratch copy made 101 tests error and the rest pass. Restored afterwards (`cmp` against the export).
- With a `.env` present, the harness refuses to start (RuntimeError).

The suite asserts:

- The 19 + 6 route sets.
- Exact OpenAPI equality for both apps.
- Frozen field order of 17 Pydantic models.
- The `_action` alias and the `splash|json`, `yes|no`, `add|remove`, `pass|fail` patterns.
- Status code, content type, CORS headers and JSON body of every one of the 66 cases. SSE fixtures are compared as raw bytes.
- Strict SSE framing (`event: <name>\ndata: <one-line JSON>\n\n`, no `\r`). The first event is `progress`/`turn_started` with the client turn id. Exactly one terminal `complete` or `error`, and it comes last.
- A Python port of the client parser (`liveAgent.ts:448-462,488-509`) gives identical events under 1, 2, 3, 7 and 64-byte fragmentation, including a split UTF-8 `μ`.
- Every `ChatResponse` carries all 14 fields in order, and `turn_id` echoes `client_turn_id`.
- Observed statuses: `direct_response, answered, awaiting_download_decision, awaiting_extraction_decision, stopped_by_user, no_pending_action, retrieval_error, stream_error`.
- All 21 companion progress phases plus `turn_started` are reached.
- Downloads and extraction happen only after explicit approval (fake call counts).
- Pending envelopes contain no `approval_token`.
- Every storage key literal is present at its cited file:line, and no other `*STORAGE_KEY`/`*CACHE_KEY` exists in `ui/src`.
- No fixture contains local paths, `scratchpad`, or key-like strings.

### 2.1 SSE transcripts captured

| Fixture | Route | Terminal | Path |
|---|---|---|---|
| `chat_stream_direct_response` | `/chat/stream` | `complete: direct_response` | mundane turn |
| `chat_stream_answered` | `/chat/stream` | `complete: answered` | turn_started, orchestrator_decision, retrieval_started/result, graph_update |
| `chat_stream_insufficient_to_download` | `/chat/stream` | `complete: awaiting_download_decision` | the full 14-event candidate-search/debate sequence |
| `chat_stream_error_empty_json_selection` | `/chat/stream` | `error: stream_error` | request-driven `ValueError` after orchestrator progress (HTTP 200) |
| `chat_stream_error_malformed_verdict` | `/chat/stream` | `error: stream_error` | **fault injection** (non-dict retrieval verdict) after `retrieval_started` |
| `chat_stream_retrieval_error` | `/chat/stream` | `error: retrieval_error` (full ChatResponse) | retrieval raises |
| `chat_stream_json_override_missing_file_falls_back` | `/chat/stream` | `complete: answered` | see finding F3 |
| `chat_action_stream_error_missing_candidate` | `/chat/action/stream` | `error: stream_error` | download yes without `candidate_index` |
| `chat_action_stream_download_yes` | `/chat/action/stream` | `complete: awaiting_extraction_decision` | approved download |
| `chat_action_stream_extraction_yes` | `/chat/action/stream` | `complete: answered` | extraction, kg_rebuild, splash_reimport, reload, re-retrieve |
| `chat_action_stream_download_failed_retry` | `/chat/action/stream` | `complete: awaiting_download_decision` | failed download; candidate marked `unavailable` |
| `chat_action_stream_decline` | `/chat/action/stream` | `complete: stopped_by_user` | decline |

## 3. Baseline failures vs regressions

- **This suite at `222b1e7`:** no failures, so none are pre-existing.
- **Repository-wide pre-existing failures** (unchanged; from `docs/migration/reviews/H0/baseline_tests.md`): 3 failed + 15 errors in the existing pytest suite, from missing untracked `matkg_bl1101_v7.json`, CBORG-credential-dependent unit tests, and `splash_reimport` pixi-check ordering. This suite neither touches nor depends on them.
- **Against an export of `2430320` (the pre-migration commit).** `app/modules/f2w_agent/api.py` is byte-identical at `2430320` and at `forge_merge` HEAD `a96f67e`. Since then `kg_rag_api.py` differs only by a comment (`28820b0`). Other `app/` files changed after `2430320` and were not exercised here. Result: 135 passed, **3 failed**: `test_ui_openapi_matches_baseline`, `test_pydantic_model_fields_frozen[ChatRequest]` and `test_replayed_response_matches_fixture[traces_get]`. All three come from one additive change, the new optional `ChatRequest.batch_id` (`app/modules/f2w_agent/api.py:141-146` at HEAD; 1-80 chars, `^[A-Za-z0-9][A-Za-z0-9_.-]*$`), which is passed into traces. It is not listed in `reviews/H0/pre_migration_changes.md`, which says the route decorators are unchanged; they are, but the request schema is not. It is backward compatible for the frozen UI, which never sends it, but it is a schema change. Classify it at H1 as an accepted additive pre-migration diff (and refresh the snapshot), or remove it. It is not a regression of old-client behaviour.

## 4. Findings

- **F1. OpenAPI vs `f2w_ui_contracts.json`.**
  - The static inventory names the snippet edit field `action`. On the wire it is `_action`: `alias="_action"`, `populate_by_name=True` (`api.py:300-309`), and OpenAPI shows only `_action`.
  - OpenAPI adds `HTTPValidationError` and `ValidationError`, which are not in the 25-model inventory.
  - The neighbourhood route is registered as `/graph/neighborhood/{node_id:path}` but published in OpenAPI as `/graph/neighborhood/{node_id}`, so an OpenAPI-generated client would lose the `:path` semantics.
  - `/chat/stream` and `/chat/action/stream` publish an empty `{}` 200 schema and a JSON content type. The SSE contract exists only in these fixtures.
  - `/health`, `/traces/{turn_id}`, `/telemetry/ui`, `/annotations` and `DELETE /session/{id}` have untyped object responses.
- **F2. The 23 literal statuses are incomplete for clients.** `stream_error` is a route-level synthetic status (`api.py:6886,6977`) and is not in `literal_response_statuses`. A ValueError on `/chat/stream` returns HTTP 200 with an SSE `error`. The same error on `/chat` returns HTTP 400 `{"detail": ...}`. Any other exception on `/chat` returns HTTP 500 `text/plain` "Internal Server Error" (fixture `chat_500_malformed_verdict`).
- **F3. Correction to `f2w_llm_qa_baseline.json` ST-12/ST-04.** A per-turn `json_graph_path` pointing to a missing file does **not** raise `ValueError('JSON graph mode requires a graph file')`. The file is skipped and the turn silently falls back to `self.graph_path()` (`api.py:5062-5069`). In splash runtime mode that is the session `kg.json`, so a JSON-mode turn is answered from the editable graph: status `answered`, `graph.source_path` `<WORKSPACE>/run/kg.json`, `graph_source_used` `splash` (taken from the verdict). The error is raised only when the selected path list normalizes to empty (`api.py:5060-5061`; fixtures `chat_stream_error_empty_json_selection`, `chat_400_empty_json_selection`).
- **F4. Edits are desired-state.** `linked_code_snippets` replaces the whole set: an omitted snippet is unlinked (`api.py:3331-3332`; fixture `graph_node_patch_unlink_and_remove`). `publications` replaces the list. Predicates are normalized (`Affects` becomes `rel:affects`).
- **F5. Publication duplication in node payloads.** `GraphNode.publications` includes both the full publication object and a `{source_paper}`-only stub derived from `source_papers` (fixtures `graph_node_get`, `graph_node_patch_*`). This is baseline behaviour the UI renders. Preserve it or change it only with review.
- **F6. Splash-mode `graph_id` is `"kg"`** (the basename of `<workdir>/kg.json`). JSON-mode graphs get `graph_id` from the file name (`contract_v1`). New backends must keep both.
- **F7. CORS.** The default origins are only `localhost`/`127.0.0.1` on ports 5173/5175, with `allow_credentials=True`. Other origins get a 400 on preflight (fixtures `cors_*`). The `/fair2wise` same-origin deployment does not need CORS **[Inferred]**.
- **F8. The settings round trip leaks defaults.** `GET /settings` reports `tiled_uri: "http://127.0.0.1:8765"` even with live Tiled disabled, and a list of 11 CBORG model ids from `config.yml`. `/health` and `ChatResponse.workdir`, `session_memory` and `graph_path` disclose absolute server paths (sanitized in the fixtures). This is the path-disclosure item already noted in FRONTEND_COMPATIBILITY.md section 3.
- **F9. Browser storage default asymmetry.** With no stored settings the browser defaults to `graphSource: 'json'`. Stored settings with any non-`json` value load as `'splash_links'`. On boot, `App.tsx:169` overwrites the stored settings from `GET /settings`, so the server's `graph_source` literal (`splash`/`json`) effectively drives editability (`GraphMockup.tsx:663`).
- **F10. The UI never calls `/health` or `/graph/upload`** (grep of `ui/src`). They are frozen anyway as published legacy API. `DELETE /session/{id}` has no Pydantic pattern on the path parameter. Invalid ids are rejected by the service with 400 `Invalid session ID`, not 422.

## 5. Limits

- Fake inference and providers mean the fixtures prove **wire shape and control flow**, not answer quality, citation selection under real retrieval, or model behaviour. The retrieval verdict is scripted. Real `RetrievalAgent` evidence selection is not exercised (T18B/H2 and the QA baseline cover that).
- `chat_stream_error_malformed_verdict` and `chat_500_malformed_verdict` use **fault injection** (a contract-violating fake retrieval) to reach the generic `except Exception` stream boundary. The other error cases are request-driven.
- Not exercised: client abort or cancellation of a running stream (there is no browser in this suite), live Tiled (`use_live_tiled=true`) with a real server, source-RAG on, Materials Project lookup, `include_external=true` OpenAlex search, an Ollama backend chat turn (settings only), multi-graph JSON selection with two graphs, uploads above size limits, concurrent requests and sessions, and the deterministic workflow mode. The download-candidate **chat reply** path (typing "download the first paper") is not recorded; only the action route is.
- The Splash store is an in-memory fake that mirrors the baseline call contract (`kg_update.splash_*`). Real Splash GraphQL behaviour, such as property merge vs replace on `updateEntity`, is **not** characterized.
- The OpenAPI snapshot is tied to the dependency versions above.
- The dist manifest hash is quoted from H0 and was not recomputed by this task.
- The ADR's Tiled facts come from the T20A reading of 0.2.19rc2, not the final 0.2.19 artifact.

# T18B: legacy UI fixture differences caused by the disclosure projection

- **Task:** T18B part 1 (access guards and disclosure projection wired into `app/modules/f2w_agent/api.py:create_app`).
- **Profile:** `:local` (the harness builds `create_app(cfg)` with no startup profile, so it resolves to `local`).
- **How the fixtures were refreshed:** `tests/contracts/legacy_ui/record_fixtures.py`, unchanged, from the T18B worktree (no `.env`, `env -i`, `F2W_RUNTIME` unset). Repeat runs give byte-identical fixtures.
- **Unchanged:** `openapi_ui.json`, `openapi_model_api.json`, `routes.json`, `index.json`, `persistence_keys.json`, every request side, every status code and content type, every CORS header, every progress event, every pending card, every answer of a successful turn, and 45 of the 66 HTTP cases (including `traces_get`, which stays raw for the local operator: `project_trace_for` returns the record for `trace.read_any`).
- **Changed:** 21 HTTP cases and their 10 SSE byte files. Each change below is produced by `DisclosureProjector`; no service behaviour changed. The changed set equals the set T21B predicted in `tests/disclosure/test_disclosure_legacy_fixtures.py::test_unchanged_fixtures_are_byte_identical_where_nothing_is_internal`, minus `traces_get`.

## Opaque path roots

Local paths become `f2w://<root>/<tail>`. Two roots exist, and only they are resolvable when the UI sends an id back (`app/api/legacy_ui.py:OpaquePaths.decode`; `..`, unknown roots and anything outside the root are refused with 400):

| Root | Server path | Example |
| --- | --- | --- |
| `workdir` | `CoordinatorConfig.workdir` | `<WORKSPACE>/run/kg.json` → `f2w://workdir/kg.json` |
| `workspace` | process working directory (`api.project_root()`, where `storage/kg` lives) | `<WORKSPACE>/storage/kg/matkg_contract_v1.json` → `f2w://workspace/storage/kg/matkg_contract_v1.json` |

Any other absolute path becomes `f2w://path/<basename>` and is not resolvable. Relative paths such as `storage/kg/x.json` (settings, available graphs) are unchanged. The label `workspace` is the one the T21B tests already use for the process root.

## Per-fixture differences

| Fixture(s) | Field | Before | After | Reason |
| --- | --- | --- | --- | --- |
| `health` | `workdir`, `session_memory`, `workflow_state` | absolute paths | `f2w://workdir`, `f2w://workdir/session_memory.json`, `f2w://workdir/workflow_state.json` | Internal-only paths (route_field_map R01). The local operator gets the full projection (`settings.global`), so every other key is unchanged. |
| `session_reset_named`, `session_reset_legacy` | `session_memory`, `workflow_state` | absolute paths | `f2w://workdir/...` (named session: `f2w://workdir/chat_sessions/contract-session-1/...`) | Internal-only paths (`project_paths_only`). |
| `graph_get` | `source_path` | `<WORKSPACE>/run/kg.json` | `f2w://workdir/kg.json` | GP.source_path internal-only. `kgCatalog.graphIdFromPath` reads only the file name, so `graph_id` handling in the UI is unaffected. |
| `graph_get_json_mode` | `source_path` | `<WORKSPACE>/storage/kg/matkg_contract_v1.json` | `f2w://workspace/storage/kg/matkg_contract_v1.json` | Same. |
| `graph_upload` | `graph.source_path`, `graph_path` | `<WORKSPACE>/run/uploads/contract_upload.json` | `f2w://workdir/uploads/contract_upload.json` | GP.source_path / GraphUploadResponse.graph_path internal-only (T21B KNOWN_CONFLICTS `graph_upload`). The value still contains `/uploads/`, so the UI overlay test (`GraphMockup.tsx:434-438`) still works, and `GET /graph/node?json_graph_path=f2w://workdir/uploads/...` now resolves it (round trip covered in `tests/api_wiring`). The recorded `graph_node_get_uploaded` request still sends the absolute path; that stays accepted (restricted to `storage/kg` and uploads as before), so its fixture is unchanged. |
| every `ChatResponse` (`chat_answered`, `chat_insufficient_to_download`, `chat_insufficient_for_failed_download`, `chat_action_decline`, `chat_action_no_pending`, and the `complete`/`error` event of `chat_stream_answered`, `chat_stream_direct_response`, `chat_stream_insufficient_to_download`, `chat_stream_json_override_missing_file_falls_back`, `chat_stream_retrieval_error`, `chat_action_stream_download_yes`, `chat_action_stream_extraction_yes`, `chat_action_stream_download_failed_retry`, `chat_action_stream_decline`) | `workdir` | `<WORKSPACE>/run` | `f2w://workdir` | CR.workdir internal-only. |
| same | `graph.source_path` | `<WORKSPACE>/run/kg.json` or `query:<WORKSPACE>/run/kg.json` | `f2w://workdir/kg.json` / `query:f2w://workdir/kg.json` | GP.source_path internal-only; the `query:` prefix is kept. |
| same | `rounds[]` | raw internal round state | ADR 4.2 summary (`_project_round`) | CR.rounds internal-only. Removed: `retrieval.answer` / `retrieval_after.answer` (the draft answer is already the top-level `answer`), `extraction_report.answer`, candidate `pdf_urls`, and the `routing` entry of direct responses (`{"routing": {...}}` becomes `{}`; the round count is kept). Kept: round numbers, statuses, counts, `selected` ids, `missing_topics`, candidate id/doi/title/year/score/repository/abstract (600-char cut), debate decision, extraction counts, file basenames. Key order inside the summary follows the projector. The frozen UI never reads `rounds` (no reference in `ui/src`). |
| `chat_stream_error_malformed_verdict` (fault injection) | terminal `error` `message` | `'list' object has no attribute 'get'` | `Internal error (error: internal; ref: turn-err-2)` | Raw exception text (ERR / SSE.error internal-only) becomes a typed message via `project_exception`. The reference is the turn id, so the message is deterministic and maps to the protected trace. Validation messages (`candidate_index is required to download a paper`, `JSON graph mode requires a graph file`) are `ValueError` texts and stay as written, so those fixtures are unchanged. |
| `chat_stream_retrieval_error` | `error` event `answer` | `Retrieval failed safely: synthetic retrieval failure` | `Retrieval failed safely (Internal error (error: internal; ref: turn-reterr-1)).` | `*_error` answers carry raw exception text after `failed safely:`; the projector keeps the head and replaces the text with a typed code (ADR 4.4). |
| the 10 changed SSE cases | `response.sse_sha256`, `response.sse_bytes`, `sse/<id>.sse` | | | The raw bytes follow the projected event data above; framing (`event: x\ndata: <one-line JSON>\n\n`, LF only) is unchanged and still passes the strict parser and the fragmentation tests. |

## Not changed, by design

- `traces_get`: raw on `:local` (owner decision: `trace.read_any` gets the raw trace). On ALS a non-admin gets `project_trace`.
- `settings_get` and the `settings_put_*` cases: settings paths are relative and the local operator holds `settings.global`; `tiled_uri` is kept (credentials and query stripped, none present). On ALS a non-admin gets `json_graph_path(s)`, `available_json_graphs`, `available_graphs[].path`, `tiled_uri` and `tiled_error` blanked.
- HTTP 4xx `detail` texts: all recorded ones are literal or `ValueError` messages and pass `project_error_message` unchanged. `chat_500_malformed_verdict` stays Starlette's plain `Internal Server Error`.
- The six model-compatible fixtures (`model_*`): `kg_rag_api.py` is a separate surface and is not wired by T18B.

## H2: quote verification wired (no fixture changes)

- **Task:** H2 gap 1 (`app/api/source_text.py`, `LegacyUiGuard.turn_projector`). Chat answers and chat stream `complete`/`error` events are now projected with the turn's retrieved PDF regions and a per-turn `QuoteVerifier` (reader run only through `SourceAccessScope.read` under a `QUOTE_VERIFICATION` / `PARSED_TEXT` grant from the guard's startup `SourceAccessAuthority`, bound to the route's `RequestContext`, revoked after projection).
- **Fixture differences: none.** Every frozen legacy UI fixture replays byte-identically in default mode; in `F2W_RUNTIME=langgraph` only the known `traces_get` `F2W_RUNTIME` settings key differs (as before H2). Reason: the T18A harness retrieval fake has no merged context and answers cite only `[KG: ...]`, and the recorded flows never reach the paper-evidence agent, so no turn has a PDF region and the projection is the T18B one.
- **Behaviour now covered by `tests/api_wiring/test_wiring_quotes.py`** (both runtime modes, `/chat` and `/chat/stream`): a verbatim quote of a retrieved `[PDF: f p.N]` chunk or extracted page keeps its quotation marks; a paraphrased quote loses them and stays a cited statement; an answer reproducing more than 40% of one region (O-D2 `max_region_fraction`) is clipped (`[… source text omitted; see citation …]` for unquoted text, `[quotation omitted: disclosure limit]` for a quote); KG/OPS-only answers are unchanged.
- **Region text:** a paper-evidence region is the full extracted page (PyMuPDF `get_text("text")`, as `PaperEvidenceAgent` reads it); a source-RAG region is the retrieved chunk text for that page from the retrieval agent's merged context. The 40% rule is therefore applied to the retained text the turn actually used, which is at most the page.

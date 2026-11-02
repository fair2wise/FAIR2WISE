# T18C: full frozen-frontend parity on actual Tiled (evidence for H6)

- **Gate:** H6 (stop before). **Task:** T18C. **Status:** evidence for review. Not owner approval; the H6 gate is not claimed.
- **Branch:** `forge_merge-T18C-parity`, based on `forge_merge` 9c282e5. Not merged.
- **Frozen client:** `HEAD:ui` = `2cc7ccd3778bac46794cbd1ef254211e617cc38d`, unchanged by T18C. No file under `ui/` was added or edited.
- **Backend:** `app.api.main.create_api_app` (T14 factory: the 19 legacy routes, access guard, disclosure projection, `/v1`), profile `:local`, fake deterministic inference.
- **Tiled:** disposable `tiled[server]==0.2.18` (O-T1) on 127.0.0.1 ephemeral ports via `tests/fixtures/tiled_server.py`; each server and its root destroyed at teardown (receipts kept with the run artifacts).
- **Versions:** Python 3.12.11, Node 20.20.2, Playwright Chromium 1243 (Chrome channel), `ui/node_modules` from `npm ci --offline`.

## 1. What runs

| Piece | Path | Role |
|---|---|---|
| Launcher | `tests/contracts/legacy_ui/closure/launch_full_backend.py` | T18A harness fakes (models, paper providers, retrieval answer text) plus the T18B markers. Starts a disposable Tiled and seeds beamline identity records (one ESAF, two Samples, two `rel:hasSample` links). The legacy live-Tiled lookup (`app/modules/tiled_graph.py`) is **real**. Editable ("splash") mode has two switchable conditions, set per request from `<state>/splash_mode`: `absent` (default) and `fake`. In `absent` the real `kg_update.splash_*` client runs against a bound, never-listening 127.0.0.1 port, the same as a host with no Splash service. In `fake` it uses the T18A in-memory Splash store, for baseline comparison only. Outbound sockets are limited to the Tiled port and that reserved port. The developer's servers (8011, 8765, 8002, 8004, 5057) are refused. A middleware records any response that carries the Tiled writer key. |
| Browser suite | `tests/e2e/live_backend/full/` (`playwright.full.config.ts`, `global-setup.ts`, `full.ts`, 5 `*.full.ts` files, 14 tests) | Builds the frozen UI once with `VITE_F2W_AGENT_API_URL` (configuration only) and serves it on 127.0.0.1:5175. No `page.route` or other handler mocking. Traces (`trace: 'on'`) and screenshots are kept in the artifact directory. |
| Closure tests | `tests/contracts/legacy_ui/closure/` (`test_closure_api.py`, `test_closure_tiled.py`) | In-process checks: six model endpoints, no-Splash edits, restart and replay, concurrency, factory coexistence. Disposable-Tiled checks: both Tiled modes through one adapter, JSON-to-Tiled import retention, edit reconciliation and rollback, legacy lookup on 0.2.18. |

**Location deviation (D-1).** The task graph gives T18C `ui/e2e/live-backend/full/`. Any file there changes `git rev-parse HEAD:ui`, which the CI frozen-UI guard checks. This suite therefore follows the T18B precedent and lives in `tests/e2e/live_backend/full/`. Its specs are named `*.full.ts` so the T18B config, which matches `*.spec.ts` recursively, does not pick them up. The T18B suite still runs 11/11.

### Commands

```bash
S=<scratchpad>; W=<worktree>; A=$S/t18c-live/<name>
cd $W/ui && npm ci --offline
cd $W && env -i PATH=/opt/homebrew/opt/node@20/bin:/usr/bin:/bin HOME=$A/home LANG=en_US.UTF-8 \
  NODE_PATH=$W/ui/node_modules PLAYWRIGHT_BROWSERS_PATH=$S/pw-browsers \
  F2W_LIVE_PYTHON=$S/venv-app/bin/python F2W_TILED_PYTHON=$S/venv-tiled/bin/python \
  F2W_LIVE_ARTIFACTS=$A [F2W_RUNTIME=langgraph] \
  ui/node_modules/.bin/playwright test -c tests/e2e/live_backend/full/playwright.full.config.ts
env -i PATH=$S/venv-app/bin:/usr/bin:/bin HOME=$S/home TMPDIR=$S/home LANG=en_US.UTF-8 \
  F2W_TILED_PYTHON=$S/venv-tiled/bin/python \
  $S/venv-app/bin/python -m pytest tests/contracts/legacy_ui/closure -q -p no:cacheprovider
```

## 2. Results

| Suite | Result |
|---|---|
| T18C browser suite, default runtime | 14/14 as expected: 13 passed, plus 1 expected failure (UI04 Tiled Save, `test.fail`) |
| T18C browser suite, `F2W_RUNTIME=langgraph` | 14/14 as expected (same split) |
| T18B live suite (`tests/e2e/live_backend`, regression) | 11/11 passed |
| Existing mocked suite (`ui/`, `CI=1`) | 23 passed, 3 skipped (prefix gateway specs, as before) |
| Frontend unit tests (`npm test`) | 97/97 passed in 11 files |
| Closure tests without Tiled | 14 passed, 3 skipped (need Tiled), 1 xfailed (F3) |
| Closure tests with Tiled | 3 passed (both Tiled modes, legacy lookup) |
| Disposable-Tiled job | 260 passed, 1 xfailed (F3) |
| Full pytest `--ignore=tests/deepeval` | 3 failed (known), 3021 passed, 135 skipped, 5 xfailed; `compare_known_failures.py`: 0 problems |

## 3. UI01-UI28 executed matrix

Status values: **pass**; **pass (baseline only)**, where the old semantics work but only through the T18A Splash fake; **fail/blocked**; **partial**; **not exercised**, with a reason. "T18B" means the T18B live specs, re-run here at 11/11. "T18A" means the frozen contract replay in `tests/contracts/legacy_ui/test_legacy_ui_contract.py`, run in the full suite.

| Case | Status | Evidence (this run) | Notes |
|---|---|---|---|
| UI01 startup/settings/health | pass | `tiled.full.ts` UI01/UI27, T18B, T18A `health`/`settings_*`/CORS fixtures | Base URL is configuration only. No login prompt on `:local`. |
| UI02 CBORG/Ollama model changes | pass | T18A `settings_put_ollama` and the frozen field lists; T18B checks that backend and model are unchanged after every test | Not driven through the settings dialog in T18C. |
| UI03 JSON multi-graph | pass | T18B `settings-graphs.spec.ts` (two graphs, chips from both) | T18B finding L2 still applies. |
| UI04 legacy editable mode on Tiled, no Splash | **fail/blocked** | `edit.full.ts`: no-Splash Save gives the legacy `502 Splash update failed`, the error shows in the panel, and nothing is written (pass). The Tiled-backed Save is an expected failure. `test_closure_api.py::test_ui04_*`: reads work; every edit kind gives 502 and no write. | **F1.** `PATCH /graph/node` still calls the Splash client (`api.py:update_graph_node`). The Tiled edit projection is not built: STOP-1..6 are unanswered (H1 `decision.json`). |
| UI05 ordinary grounded answer | pass | T18B `qa-citations`; `edit.full.ts` citation readback | Wire and rendering only (fake model). |
| UI06 numerical insufficiency vs conceptual fallback | not exercised | Only the insufficient path runs (`pending.full.ts`) | The distinction needs real retrieval and model behaviour; it belongs to the binary suite, not fake inference. |
| UI07 Source RAG / ops docs / MP | partial | Source RAG toggle: T18B. `[PDF: f p.N]` citations under the quote projection: `publications-disclosure.full.ts` and `tests/api_wiring/test_wiring_quotes.py` | OPS and MP citations are not driven in the browser. |
| UI08 stream complete / graph_update | pass | Every T18C stream is parsed strictly (`turn_started` first, one terminal event); `graph_update` node ids equal `complete` | |
| UI09 fragmented/failed stream | pass | T18B `stream-faults` (fragment, 503 fallback, errors); `test_ui10_retry_*`: a retry after a streamed action is refused with 400 | No duplicate side effect. |
| UI10 stop / cancelled action | partial | Stop during chat: T18B. Replay after an applied action gives `no_pending_action` or an explicit 400: `pending.full.ts`, `test_ui26_*` | Stop during an action stream is not driven. Cooperative cancellation of abandoned work is still unobserved (T18B L3). |
| UI11 candidate selection / decline | pass | `pending.full.ts`: "Download paper 1" downloads candidate 0 exactly once, the card shows both candidates, no `approval_token`; decline gives `stopped_by_user` and no download | |
| UI12 extraction Run / Skip / targeted | pass (wire); Tiled stage blocked | `pending.full.ts`: Run extraction gives one extract, one rebuild and one reimport; `splash_reimport_*` phases are kept; Skip runs no extraction | **F2.** The post-extraction `splash_reimport` is the T18A fake. Its scoped-publication replacement is STOP-4. |
| UI13 session switch/reload/reset/delete | pass | `sessions.full.ts`: two sessions with separate server pending state; switch, reload and storage keys; deleting A sends `DELETE /session/<A>`; B keeps answering | Legacy `fair2wise.chat.messages.v1` migration is covered by `chatSessions.test.ts` only. |
| UI14 node search/detail | pass | `edit.full.ts` uses the real `/graph/nodes/search` and `/graph/node`; T18A fixtures | |
| UI15 edit node / publications | **fail on Tiled**; pass (baseline only) | Fake: label and description saved, the same id reads back through `GET /graph/node`, and a later `[KG: matkg:TermA]` chip shows the edited label | F1. Publication list edits are not driven in the browser; T18A covers them. |
| UI16 add/remove relationships | **fail on Tiled**; pass (baseline only) | Fake: outgoing `rel:affects` added through node search; the `/graph` edge appears; removal from the UI deletes it | F1, STOP-1/STOP-2. |
| UI17 snippet upsert/unlink | **fail on Tiled**; pass (baseline only) | Fake: new snippet `_action: upsert` with no id; implicit unlink when it is omitted (as in the baseline) | F1, STOP-3. |
| UI18 JSON read-only and graph upload | pass | `edit.full.ts`: no Edit control in JSON mode; PATCH gives the legacy 400. Upload: T18A `graph_upload*` and T25C `test_local_graph_edit_and_tiled_uri_need_no_identity` | The frozen UI has no upload control (API-only route). |
| UI19 KG/PDF/OPS/MP citations and neighbors | pass (KG, PDF, Tiled ids); partial (OPS, MP) | `tiled.full.ts`: three Tiled identity chips, tooltip, focus; `GET /graph/neighborhood/<ESAF>` pages the disposable Tiled | **F6** (informational): the frozen UI served the focus from the turn graph and sent no neighbourhood request. |
| UI20 publication search / favorites | pass | `publications-disclosure.full.ts`: real `/publications/search` (`include_external: false`); the bookmark persists across reload | Favorites are browser-side by design. |
| UI21 query/viewer limits | not exercised in T18C | T18A settings fixtures (`kg_query_max_nodes`); mocked `kg-viewer.spec.ts` (1000/10000/All) | No live clipping check. |
| UI22 traces / telemetry / annotations | pass | `sessions.full.ts`: Pass posts `/annotations` with the turn id; `/traces/<turn>` returns 200; telemetry posted without question text | |
| UI23 failures / unavailable providers | pass | Retrieval error and stream error: T18B. Splash unavailable: explicit 502. Live Tiled off: falls back to the insufficient path, not an error. Identity outage: T25C. | |
| UI24 six model-compatible endpoints | pass (contract); **fail (D19)** | Section 4 | **F3.** |
| UI25 concurrent sessions/settings/requests | pass | `test_ui25_*`: 12 interleaved turns on one event loop keep their own pending state; `sessions.full.ts`; ALS per-user isolation in T25C `security_live` | |
| UI26 publish/restart/rollback | pass, with finding | `test_closure_tiled.py` in both modes: v1 import, acknowledged edit blocks re-import, edit reconciled into v2 (`edit_reconciliation` parent), rollback to v1, roll forward, replay is a no-op, FORGE records untouched, old citation id resolves at every step. `test_ui26_*`: pending decision survives a restart and applies once | **F4.** Real publication is H5-gated; only disposable targets were used. |
| UI27 live-Tiled exploration | pass | `tiled.full.ts` and `test_legacy_live_tiled_lookup_*` against real 0.2.18: probe `ok`, key flag only, identity hits and edges, read-only (Tiled fingerprint unchanged), key never in a response | **F7** (informational): the managed single-user server also answers unsigned reads. |
| UI28 frozen UI + new backend + FORGE host | partial | The browser suite runs on `create_api_app`; `test_ui28_*`: T14 factory, legacy factory and model proxy coexist; separate workdirs share no session | FORGE embedding is **blocked** on T15 (not merged). The installed `f2w` import is covered by T04 packaging tests (no install in this venv). |

Summary: **17 pass** (UI01-03, 05, 08, 09, 11, 13, 14, 18, 20, 22, 23, 25, 26, 27, plus UI12 on the wire). **4 fail or blocked on Tiled** (UI04, UI15, UI16, UI17; the old edit semantics still pass through the Splash fake). **1 contract pass with a D19 failure** (UI24). **4 partial** (UI07, UI10, UI19, UI28). **2 not exercised** (UI06, UI21), each with a reason.

## 4. Six model endpoint results (UI24)

`kg_rag_api.create_fastapi_app` with a fake chat client, compared field for field with the frozen T18A fixtures (`test_closure_api.py::test_ui24_*`):

| Endpoint | Result |
|---|---|
| `POST /api/chat` | 200; equals `model_api_chat`. Empty messages give 400 and equal `model_api_chat_400`. |
| `POST /v1/chat/completions` | 200; equals `model_openai_chat_completions` (the `chatcmpl-<ms>` id is normalized). Empty gives 400 and equals the fixture. |
| `GET /api/tags` | equals `model_api_tags` |
| `GET /api/version` | equals `model_api_version` |
| `GET /v1/models` | equals `model_v1_models` |
| `GET /api/ps` | equals `model_api_ps` |

The route set is exactly these six. `stream: true` still returns the single JSON body, as in the baseline.

**D19 (xfail, strict):** a model that echoes its prompt returns most of a one-page PDF window verbatim, because these routes are not behind the T21B projection (route map R20/R21: `conforms_to_D19_today: no`). See F3.

## 5. Acceptance items

| Acceptance (task graph) | Result |
|---|---|
| No removed old UI feature; no updated client hiding breakage | No `ui/` change. Edit controls remain and fail visibly (no fake success). |
| Tiled implements safe editable-mode parity without Splash | **Not met.** F1, blocked on STOP-1..6. |
| No unauthorized writes, duplicate effects or broken old citations | No writes on read paths; replays have no effect; old ids resolve after edit, re-import, rollback and roll-forward. ALS denial before effects: T25C `security_live` and T14 `api_v1_live` (section 7). |
| Deviations surfaced to the owner | Section 6. |
| Both Tiled modes through the same adapter | Managed (`from_managed`) and external (`ExternalTiledConfig` with a secret file and `from_external`) both use `TiledMutationClient` with the same importer. |
| Approved exact-quote projection (D19, O-D2) | Browser: the verified quote is shown verbatim; a page dump is clipped (`source text omitted`); no captured response except the raw `:local` trace carries the chunk; `GET /graph` has no chunk. The model proxy is the exception (F3). |
| SSE and graph exports serve no source bodies or crops | Met on the UI routes; checked over every captured exchange. |
| Versioned JSON to a separate Tiled import, with retention and edit reconciliation | Met (UI26 row), with F4. |
| D27 / D30 / D34 | Importer receipts are executed as the configured writer, approved by `local-operator`. ALS reader/admin: T25C. No per-user writer impersonation (`executed_as != approved_by`, T14 live). |
| Local full access without sign-in; ALS reader and admin separately; no login gate in local | Browser: no sign-in prompt and `/auth/sign-in` absent on `:local` (T25C). ALS sessions: section 7. |
| Publication only against disposable targets | Yes. No ALS host was contacted. |

## 6. Deviations and findings for the owner

| ID | Finding | Owner decision needed |
|---|---|---|
| **F1 (blocking H6)** | Legacy editable mode still depends on Splash: `update_graph_node` calls `kg_update.splash_*`. With no Splash, Save gives 502 and nothing is written (fails closed, no fake success). The Tiled revision projection the ADR proposed (`legacy_splash_to_tiled_adr.md` 2.3) and `edit_journal`'s "T18C builds the edit projection" are not implemented, because STOP-1..6 are unanswered. | Answer STOP-1..6. Then an implementation task (the shared `api.py` and graph modules are outside T18C's ownership) can build it, and the expected-failure spec will flip to a pass. |
| **F2** | The post-extraction `splash_reimport` (whole-DB wipe and reimport) is still the code path. The suite fakes it. | STOP-4: scoped publication of the new JSON version, and the timing change. |
| **F3** | The six-endpoint model proxy chat routes do not apply the disclosure projection (D19). An echoing model returns more than 40% of a page. `run_fastapi` also binds 0.0.0.0 without auth (route map). | Wrap R20/R21 in the projector, or retire or restrict them. |
| **F4** | Once an edit is `reconciled`, the edit journal no longer gates imports. Re-importing the pre-edit parent snapshot (rollback) is not refused and needs no reviewed resolution. The edit stays in v2 JSON but leaves Tiled. | Decide whether rollback below a reconciliation snapshot needs a `ConflictResolution`. **Closed by T23C (Q15):** it does; `reconciled` edits protect imports whose full lineage lacks their snapshot, and the `test_closure_tiled.py` pin now asserts the refusal. |
| **D-1** | Browser suite location is `tests/e2e/live_backend/full/`, not `ui/e2e/live-backend/full/`, to keep the frozen ui tree unchanged. | Accept the path, or approve a `ui-change-approved` change. |
| **D-2** | CI `FROZEN_UI_TREE` (`75a19b98…`) no longer equals `HEAD:ui` (`2cc7ccd3…`) since T24C `5ab7e62` changed `ui/src/App.tsx` (D26 base path). Pre-existing; the guard would fail on `forge_merge` without the label. T18C tested tree `2cc7ccd3`. | Update the pinned tree after confirming the T24C change. |
| F5 | Real-browser gaps still not covered here: Stop during an action stream, OPS/MP chips, viewer/query limits, publication-list edits, legacy history migration. | None; listed for H6 completeness. |
| F6 | Clicking a Tiled identity chip in an answered turn is served from the turn graph; the frozen UI sent no `/graph/neighborhood` request. | Informational. |
| F7 | The managed single-user Tiled (test/local profile) answers unsigned GraphQL reads, so the legacy `unsigned` status cannot occur there. | Informational; confirm it is intended for loopback `:local`. |

## 7. Repeat-suite results

- Full pytest `--ignore=tests/deepeval` (no Tiled): 3 failed, 3021 passed, 135 skipped, 5 xfailed. `compare_known_failures.py`: 3 known, 0 problems.
- Disposable-Tiled job (`F2W_TILED_PYTHON` set) over `tests/tiled tests/graph tests/graph_import tests/security_live tests/api_v1_live tests/contracts/legacy_ui/closure`: 260 passed, 1 xfailed (F3). This includes the T25C ALS reader/admin sessions on the legacy app (reader chat-only, privileged work denied before effects, admin effects, sign-out, expiry, downgrade, outage), the local no-login cases, and the T14 `/v1` live permissions. No disposable server was left running.

## 8. Limits

- Fake inference proves wire shape, client behaviour and rendering, not answer quality (UI06 and the binary suite are out of scope).
- The `fake` Splash condition is a comparison baseline, not parity evidence.
- No real publication, ALS host, CBORG/LLM call or Docker build was used.

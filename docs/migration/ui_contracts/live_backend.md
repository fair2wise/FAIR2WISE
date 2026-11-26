# T18B part 2: the frozen UI against the real legacy API

- **Gate:** H2. **Task:** T18B part 2. **Status:** evidence for review. Not owner approval.
- **Claim:** the frozen UI (tree `75a19b9823bc924a6391311191d3e41a5b4917ff`, unchanged) runs chat, streaming, citations and settings against the real `create_app` with fake inference. No `page.route` or other HTTP handler mocking is used for any endpoint under test.

## What runs

| Piece | Path | Role |
|---|---|---|
| Launcher | `tests/contracts/legacy_ui/runtime/launch_live_backend.py` | Builds the app with the T18A harness (`_legacy_ui_harness.install` + `make_ui_app`): the real routes, access guard (profile `:local`, no login), disclosure projection, service, orchestrator, sessions and traces. Only models, paper providers, Splash and Tiled are faked. Uses a temp workspace (synthetic `storage/kg/matkg_contract_v1.json` plus a second graph `matkg_contract_v2.json`), temp traces and session state. Outbound sockets are blocked. Serves uvicorn on `127.0.0.1` on an ephemeral port. `F2W_RUNTIME` is passed through. |
| Diff tool | `tests/contracts/legacy_ui/runtime/diff_capture.py` | Compares the launcher's `capture.jsonl` with the T18A fixture shapes. |
| Playwright project | `tests/e2e/live_backend/` (`playwright.live.config.ts`, `global-setup.ts`, `live.ts`, 4 specs) | Global setup starts the launcher, runs `vite build --outDir <artifacts>/dist` with `VITE_F2W_AGENT_API_URL=http://127.0.0.1:<port>` (configuration only), and serves the dist on `127.0.0.1:5175`. That is a default CORS origin, so real CORS is exercised. |

**Why outside `ui/`.** Adding files under `ui/` changes `git rev-parse HEAD:ui`, and CI enforces that hash. The specs import `@playwright/test` from `ui/node_modules` through `NODE_PATH`, and the build runs from `ui/` with its unchanged `vite.config.ts`. No file under `ui/` is added or changed.

## Fault injection (server side, marker in the chat message)

Each case below is noted in its spec. In all of them except the 503, the real handler produces every byte the browser parses.

| Marker / trigger | Where | Effect |
|---|---|---|
| `explode` (T18A harness) | retrieval fake raises | real `error` event, status `retrieval_error` |
| `crash the verdict` (T18A harness) | retrieval fake returns a non-dict | real `error` event, status `stream_error`, after progress events |
| `insufficient` (T18A harness) | retrieval verdict insufficient | real candidate search and debate, then `awaiting_download_decision` |
| `multi-graph` (launcher) | retrieval fake answer citing `matkg:TermA` (graph 1) and `matkg:Beta` (graph 2), with `selected_hits` per graph | the real `query_graph_payload` merges the two graphs |
| `[slow]` (launcher) | retrieval fake sleeps 30 s | lets the browser press Stop mid-stream |
| `[fragment]` (launcher `FaultMiddleware`) | the real SSE body is re-sent in 1/2/3/7-byte ASGI sends | browser parser under fragmentation |
| `[fallback-503]` (launcher `FaultMiddleware`) | `/chat/stream` answers 503 before any event. **This one response is synthetic.** | the client must fall back to the real `/chat` |

The launcher also records state that only the specs read, and that never reaches the browser. `observations.json` holds fake call counts, retrieval kwargs and the sha256 of every graph file. `capture.jsonl` holds every sanitized exchange.

## Specs (11 tests)

- `qa-citations.spec.ts`
  - Streamed answer: the request body is checked; the stream keeps strict LF framing; the first event is `turn_started` with `client_turn_id`; `graph_update` carries `node_ids`; there is exactly one terminal `complete:answered`; there is no fallback. In the UI: the answer text, a KG chip with a tooltip, the bibliography, the publication, "2 nodes" in the merged viewer, and click-to-focus. There is no login prompt, and nothing is published.
  - Second turn: same `session_id`, a new turn id, and the history is sent.
  - Mundane turn: `direct_response`, with no retrieval.
- `insufficient.spec.ts`: the candidate card is shown, with both papers and the "Ask in chat" copy. The phase sequence is checked, and there is no `approval_token`. One candidate search runs, and there are zero download, extract or Splash writes.
- `settings-graphs.spec.ts`
  - Source RAG: the checkbox triggers `PUT /settings` with `source_rag: true`, the server returns it, it survives a page reload, the next turn sends it, and the retrieval fake receives `source_rag=True`. Toggling it back gives `false`.
  - JSON multi-graph: selecting JSON with both graphs, saving and reloading drives the dialog from the server. The answer shows chips `Term A` and `Beta film`, references, a `Material` tooltip, and focus on `matkg:Beta`.
- `stream-faults.spec.ts`
  - The retrieval error event is shown as "Agent run failed" with the projected message, and no raw exception text reaches the browser.
  - `stream_error` after progress shows `ref: <turn id>`.
  - The fragmented stream renders the same answer.
  - The pre-first-event 503 falls back to `/chat` with the same turn id and body.
  - Stop: the browser aborts, the server records the disconnect, `/chat` is never called, there is no error bubble, and the next turn answers normally.

Every test that could write runs `expectNothingPublished`. It checks that:

- the Splash, rebuild, reimport, download and extract counters are unchanged;
- the `storage/kg` hashes are unchanged;
- the browser made no PATCH, `/graph/upload` or `/chat/action*` request;
- backend, model and live Tiled are unchanged (`cborg` / `lbl/cborg-chat` / off).

## Commands

```bash
S=<scratchpad>; W=<worktree>; A=$S/t18b-live/art-<mode>
cd $W/ui && npm ci
cd $W && env -i PATH=/opt/homebrew/opt/node@20/bin:/usr/bin:/bin HOME=$A/home \
  NODE_PATH=$W/ui/node_modules PLAYWRIGHT_BROWSERS_PATH=$S/pw-browsers \
  F2W_LIVE_PYTHON=$S/venv-baseline/bin/python F2W_LIVE_ARTIFACTS=$A [F2W_RUNTIME=langgraph] \
  ui/node_modules/.bin/playwright test -c tests/e2e/live_backend/playwright.live.config.ts
env -i PATH=$S/venv-baseline/bin:/usr/bin:/bin HOME=$A/home $S/venv-baseline/bin/python \
  tests/contracts/legacy_ui/runtime/diff_capture.py $A/backend/state/capture.jsonl --out $A/diff_report.json
```

The artifacts go to `$A`: `dist/`, `backend/server.log`, `backend/state/{capture.jsonl,observations.json}`, `screenshots/`, `test-results/` (a trace on failure) and `results.json`. Port 5175 must be free, so run this suite and the mocked suite one after the other. Its default dev server also uses 5175.

## Results (forge_merge e324df6 + these commits)

| Mode | Result |
|---|---|
| default (`F2W_RUNTIME` unset) | 11/11 passed |
| `F2W_RUNTIME=langgraph` | 11/11 passed |
| existing mocked suite (`ui/`, `CI=1`) | 23/23 passed |
| full pytest `--ignore=tests/deepeval` | 3 failed / 1860 passed / 11 skipped / 4 xfailed; `compare_known_failures.py`: 3 known, 0 problems |

## Baseline-vs-candidate diff

There are 66 captured exchanges per mode, excluding preflights. The two modes give identical summaries.

- **63 matched to a T18A fixture.** None has a framing error or an SSE invariant failure. There are no response-shape diffs, no differing phase sequences, no ChatResponse key-order diffs and no citation failures: every `[KG: id]` is in `node_ids` and in `graph`.
- **SSE event shape:** one diff, `complete.rounds[].retrieval.selected_hits` (added). It comes from the launcher's multi-graph fake. The real `RetrievalAgent` emits `selected_hits`, but the T18A fake does not, so the T18A fixtures never show it. The projected `rounds` summary keeps the field. The frozen UI never reads `rounds`.
- **Unmatched (3), as expected:**
  - `GET /graph/node/matkg:Beta`: there is no fixture for that literal path, and its shape matches `graph_node_get`.
  - The injected 503.
  - The cancelled `[slow]` stream, which ends after `retrieval_started` by design.
- **Request shape:** the T18A fixture requests were hand-written subsets. The real frozen UI also sends the full `ui_settings` snapshot, `messages`, `source_rag` and `use_live_tiled`. Its `PUT /settings` sends the whole settings payload, including `selected_graph_ids`. Its `/telemetry/ui` events carry `cite_type`/`source_kind`/`target`/`field`/`old`/`new` and no per-event `turn_id`. The server accepts all of it (HTTP 200).

## Findings

- **L1. The frozen UI keeps default graph paths the server does not have.** Saving JSON mode sends `json_graph_paths` with `matkg_rsoxs_v1.json` and `matkg_bl1101_v1.json` added. The server stores them as sent and logs `Skipping missing JSON graph` at query time. This is baseline behaviour and was left unchanged. It did not cause the multi-graph failure seen during development. A direct API probe with and without the missing paths gave the same result. The cause was L2.
- **L2. Multi-graph answers depend on `selected_hits` and on `retrieval._graphs`.** If the retrieval agent does not expose both, every selected id is resolved against the primary graph only, and nodes from the second graph silently drop out of `node_ids` and `graph`. The UI then shows a raw `matkg:Beta` chip typed `Entity`. The real `RetrievalAgent` provides both, and so does the launcher fake. Any replacement retrieval (T04+/LangGraph) must keep this contract. Consider a contract test for it.
- **L3. Cancellation.** When the browser aborts, Starlette ends the `StreamingResponse` without a final body. The next turn on the same service answers at once, so no lock stays held. Whether the abandoned retrieval coroutine is cancelled, or runs on until its 30 s delay ends, is not observed.

## Limits

- Fake inference: the browser checks prove wire shape, client behaviour and rendering, not answer quality.
- The `[fallback-503]` response and the `[fragment]` re-chunking are injected in middleware.
- Not exercised in the browser:
  - the download/extraction approval chat flow, because it would write to the fake Splash store; this is a read-path task;
  - graph edits;
  - live Tiled, Ollama, ALS profiles, and the `/fair2wise` prefix build.

## Library suite (Tier 2 E, 2026-11)

`tests/e2e/live_backend/library/` is the third live-backend project: `launch_library_backend.py` serves the `:local` API
over a generated library with a real worker (no Tiled, no key, no network), `global-setup.ts` builds and serves the UI as
the T18C setup does, and `flow.lib.ts` walks the library flows in the browser in the owner's order (schema panel,
extraction and coverage, export and download, node card with passages, Find supporting text, user-schema extraction and
Repair relations, harvest with a dropped PDF, a chat question during a pending offer). CI job: `playwright-library-live`
(`[ci full]`). The limits above still hold: inference is scripted, so the suite proves wire shape, client behaviour and
rendering, not extraction quality.

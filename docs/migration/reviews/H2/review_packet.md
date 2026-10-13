# Human review packet: H2 (runtime vertical slice)

Gate: H2
Approver: project owner
Status: **approved 2026-10-13** (see decision.json)

H2 asks the owner to approve the unchanged QA and summary path behind the new
runtime, the private-source boundary, no-login local access and fail-closed
ALS reader/admin guards. H2 blocks deep implementation replacement and any
switch of the default orchestration.

## 1. Branch and commits

`forge_merge`, local only, not pushed. `main`/`rsoxs` untouched. Frozen UI tree
`75a19b98...` unchanged. First-parent commits since H1 (`143e6fa`):

| Commit | Content |
|---|---|
| `1dfacd8` | Shared CBORG admission (H1-3, behind `F2W_SHARED_ADMISSION`) and fail closed without `CBORG_API_KEY` (H1-4) |
| `5c563f6` | T25B access context, effect guards, route policy table, whoami adapter |
| `bbdf95f`, `f332494`, `ecce5bb` | Owner decisions O-A5, O-A1, O-A8, O-D2, Tiled URL local-only, admin raw traces |
| `029bba0` | T02 LangGraph runtime, executor, approval node, checkpointer |
| `5e2a80a` | T21B source access and user-safe projection |
| `3396610` | Contracts 1.0.0-draft.3 (additive) |
| `e782456` | T03 existing QA path as a Tool/Skill; `F2W_RUNTIME` (default `legacy`) |
| `5708350`, `8898dac` | T19B paper-summary Tool and re-recorded fixtures |
| `5c451d7` | T18B part 1: guards and projection on the 19 legacy routes |
| `e324df6` | Quote verification against each turn's retrieved PDF regions; summary Tool live in `langgraph` mode |
| `d47825a` | T18B part 2: frozen UI against the real backend |

Owner decisions taken during H2 are in `../H1/owner_decisions.md`.

## 2. Evidence

| Check | Result |
|---|---|
| Full isolated pytest, `F2W_RUNTIME` unset | 1860 passed; only the 3 known failures (`compare_known_failures.py`: 0 problems) |
| Full isolated pytest, `F2W_RUNTIME=langgraph` | 1858 passed; the 3 known plus two `traces_get` checks that differ only by the trace snapshot recording `F2W_RUNTIME` (question Q2) |
| Frozen legacy UI contract on `:local` | 138/138; 31 fixture files updated for documented projection changes (`ui_contracts/projection_fixture_diffs.md`) |
| Live browser, frozen UI vs real backend, fake inference, no route mocking | 11/11 specs in both runtime modes (`ui_contracts/live_backend.md`) |
| Existing mocked Playwright suite | 23/23 |
| Baseline-vs-live traffic diff | 63/66 exchanges matched T18A fixtures; no framing, phase, key-order or citation differences; 3 unmatched as expected |
| QA parity (T03) | legacy, toggled, direct and delegated calls return identical evidence and graph IDs; `JUDGE_SYSTEM` hash unchanged |
| Summary parity (T19B) | byte-identical UI output through the Tool; no model call |
| Access (T25B + wiring) | `:local`: every route as local-operator, zero identity calls. Simulated ALS: anonymous 401 (except health and docs), non-admin denied before effects, admin allowed, forged trust headers ignored |
| Disclosure (T21B + quotes) | no PDF/crop serving; local paths become opaque `f2w://` ids; raw exceptions typed; relevant verified quotes keep quotation marks; a page dump beyond 40% of a page is clipped |

Every test used fake or mocked inference. No live model, Tiled server or
external service was called, except the incident in section 4.

## 3. Behavior changes a local user can see

All are owner-approved or required by fixed decisions:

- Absolute paths in responses become `f2w://` ids (accepted back by the server).
- `rounds` in chat responses is summarised (the UI does not read it).
- Raw exception text becomes a typed message with a reference id.
- Unverifiable or paraphrased "quotes" lose their quotation marks; verified
  quotes are kept; >40% of one page verbatim is clipped.
- Annotations record `local-operator`; telemetry events carry `actor`.
- CBORG clients refuse to start without `CBORG_API_KEY` (H1-4).

## 4. Incident

On 2026-10-12, worker test runs inside worktrees under the repo directory
loaded the real `.env` via `load_dotenv()`, and 9 `tests/deepeval` tests
called `api.cborg.lbl.gov`. All were refused with HTTP 403 ("IP not
recognized"); no model answered. Since then, worktrees live outside the repo,
tests run with `env -i` and `--ignore=tests/deepeval`. Proposal P1 below would
make this impossible for every user of the suite.

## 5. Decisions requested

### 5.1 For H2

| # | Question | Recommendation |
|---|---|---|
| Q1 | In `langgraph` mode, UI QA turns use the direct Tool call (the legacy orchestrator already chose the action). Route them through the planning supervisor instead (one extra model call per turn)? | keep direct |
| Q2 | Trace snapshots record `F2W_RUNTIME` when set. Keep it and refresh the `traces_get` fixture for `langgraph` mode? | keep and refresh |
| Q3 | When `F2W_DEPLOYMENT_PROFILE` is unset, default to `:local`? (ALS needs explicit config and refuses to start without identity/session config.) | yes |
| Q4 | On ALS, the frozen UI sends every settings field on Save, so a non-admin's Save is always 403. Fix by checking only changed fields now, or wait for per-user settings (T14/T18C)? | check only changed fields now |
| Q5 | `workflow_mode` (no effect in the UI API): keep admin-only? | yes |
| Q6 | O-A2: ALS role-check cache max age: 60 s default, capped at 900 s? | yes |
| Q7 | T02 capability-group names `literature_reader`, `curator`, `maintainer`, and default turn limits as neutral starting values | approve as provisional |
| Q8 | When a new chat message arrives while a write awaits approval, the pending write is dropped. Keep? | keep |
| Q9 | Approve H2 and unblock H3 preparation and the parallel ports (T04-T09 per the plan) | approve |

### 5.2 Proposals (no decision needed now unless you object)

- **P1.** `tests/conftest.py` guard: refuse live-provider tests unless `F2W_ALLOW_LIVE_TESTS=1`, so a nearby `.env` can never trigger real calls. Changes local runs, where deepeval currently uses your key automatically.
- **P2.** Enable the `real-api-browser` CI job with the T18B command.
- **P3.** Add a contract test that multi-graph answers keep per-graph identities (finding L2) before T04.
- **P4.** Enable `F2W_SHARED_ADMISSION` by default after a parity measurement in the next phase.

### 5.3 Still open, not blocking H2

Legacy splash-to-Tiled STOP items 1-6 (block T08B/T18C); Tiled 0.2.18 interim
pin (O-T1); per-user graph selection (planned); sign-in/sign-out handlers and
session store for ALS (T25C); the six model-compatible routes are not wired
(off on ALS by policy).

## 6. Resources and rollback

No paid or live model use beyond the refused calls in section 4. Rollback:
`git revert` of the merge commits above (no history reset, D10). Default
runtime remains `legacy`; `F2W_RUNTIME=langgraph` and `F2W_SHARED_ADMISSION`
are opt-in.

## 7. Requested authorization

Approve H2 with the answers to Q1-Q9. Not requested: any push, PR, image
build, Tiled access, live model evaluation or FORGE change.

## Owner decision

Decision: approved with Q1-Q9 answers (decision.json); T25D, T03B, T02B added to the plan first.
Limits: no push, publication, central Tiled or live model use.
Date: 2026-10-13

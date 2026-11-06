# Plan addenda

Owner-requested additions to `plan.md` (v8.0). The companion bundle,
including `companion/task_graph.json`, stays byte-for-byte as supplied; tasks
added here extend it and take the same gate and branch rules.

## A1 (2026-10-13, H2 decision): three tasks added before further porting

The owner approved H2 on the condition that these are in the plan before code
continues. They run first in the post-H2 phase, ahead of T04-T09.

### T25D: Per-user settings

- **Depends on:** H2. **Blocks:** T14, T18C, and any ALS deployment test of settings.
- **Requirement (owner, Q4):** "user settings should be allowed on ALS images.
  User sessions (different users, admin or not) should be able to have
  independent settings." Also covers per-user graph selection (owner, T25B
  follow-up).
- **Scope:** split settings into personal fields (backend/model, selected
  graphs, source RAG, query hops/node caps, live-Tiled lookup) stored per
  principal, and deployment-wide fields (Tiled URL, `extraction_mode`,
  `workflow_mode`, server default graph list) that stay admin-only on ALS.
  `GET`/`PUT /settings` read and write the caller's personal copy; a user who
  never saved inherits deployment defaults. On `:local` behavior is unchanged
  (the operator's settings are the server's).
- **Acceptance:** two ALS principals (admin and non-admin) save different
  personal settings and each turn uses its own; a non-admin's Save from the
  frozen UI succeeds for personal fields and ignores deployment-wide fields
  with a warning; admins change deployment-wide fields; frozen UI contract
  passes on `:local`; `PUT /settings` from the frozen UI no longer 403s for
  ALS non-admins.

### T03B: Route UI QA turns through the planning supervisor

- **Depends on:** H2, T03. **Blocks:** switching the default runtime (H6/H7).
- **Requirement (owner, Q1):** "lean towards using the supervisor".
- **Scope:** in `F2W_RUNTIME=langgraph`, ordinary UI QA turns go through
  `build_literature_supervisor` (which then calls `answer_from_knowledge`),
  selected by its own startup setting so it can be compared with the direct
  call. Responses still translate back to the frozen ChatResponse/SSE shapes.
- **Acceptance:** evidence/graph IDs, citations and statuses match the direct
  path on the T03 parity fixtures; the extra planning call goes through shared
  admission and is visible in traces; latency and model-call counts reported
  for both paths; frozen UI contract and live-browser suite pass.

### T02B: Pending approvals survive new chat messages

- **Depends on:** H2, T02. **Blocks:** T10 (first write-capable tools).
- **Requirement (owner, Q8):** option (b): when a new chat message arrives
  while an action awaits approval, answer the new message and keep the
  approval request open until the user approves or declines it.
- **Scope:** change the T02 default (which dropped the pending write) so the
  interrupted approval stays resumable on its thread while new turns proceed;
  approvals still bind to the exact plan hash and expire as configured; a
  stale plan is rejected at resume.
- **Acceptance:** new question answered while a write is pending; later
  approval applies the original plan exactly once; decline cancels it; a
  changed plan or expired approval is refused; no duplicate effects across
  restart.

## A2 (2026-10-14, owner request): legacy porting and framework consolidation

The owner requested a dedicated stage to systematically bring pre-existing F2W
code into the new module framework, with originals preserved in
`app/modules/legacy/` for backward compatibility and reference during the
transition.

**Design decisions (owner, 2026-10-14):**
- `app/modules/legacy/` serves both roles simultaneously: frozen originals kept
  for backward-compat and reference (read-only, no active development), while
  ported counterparts live in their canonical module locations. The legacy copies
  stay runnable until the gate that retires them is approved.
- A piece is retired only after the binary suite has passed against its ported
  replacement and the owner has approved the retirement at a gate.

### T17B: Audit, port and consolidate pre-framework F2W code

- **Depends on:** H4 (binary suite passed; canonical new-framework modules
  validated). **Blocks:** H8 (new gate, see below).
- **Requirement (owner, 2026-10-14):** every piece of application logic that
  belongs in the framework should live there; originals move to
  `app/modules/legacy/` and stay runnable until explicitly retired.
- **Scope:**
  1. **Audit.** Enumerate all Python files outside `app/modules/` (excluding
     `tests/`, `evals/`, `scripts/`, `splash_links/`, `AGENT/`) and all
     files inside `app/modules/` that pre-date the migration framework
     (identified by baseline inventory B01-B36). Classify each as:
     - **(a) already ported** — a canonical module covers this; original can
       move to `legacy/` now.
     - **(b) needs porting** — logic belongs in the framework but has no
       canonical home yet; port it before retiring the original.
     - **(c) operator script** — lives in `scripts/`; promote to a proper
       CLI entry point via `pyproject.toml` if frequently used, otherwise
       leave in place.
     - **(d) retire** — dead code, superseded, or out of scope; remove after
       owner approval.
  2. **Port class (b) files.** Implement the canonical module version; add
     tests proving behavioral parity with the original using the same
     fixtures as T11 where applicable.
  3. **Move class (a) originals to `app/modules/legacy/`.** Add a
     module-level deprecation notice pointing to the canonical location.
     Keep them importable and runnable; do not change their behavior.
  4. **Promote class (c) scripts.** Wire frequently-used scripts as
     `[project.scripts]` entry points in `pyproject.toml`; scripts that
     are one-off analyses stay as-is.
  5. **Mark class (d) for retirement.** Record each in the H8 review packet;
     do not delete until H8 is approved.
- **Acceptance:**
  - Audit report committed to `docs/migration/reviews/H8/legacy_audit.md`
    with one row per classified file.
  - Every class (b) port passes the T11 binary suite at parity with its
    original.
  - `app/modules/legacy/` contains the moved originals with deprecation
    notices; their existing tests still pass unchanged.
  - No class (d) file is deleted before H8 approval.
  - A clean `pip install fair2wise` installs no stray top-level packages;
    `from f2w.<module>` resolves to the canonical location, not the legacy
    copy.

### H8: Legacy consolidation gate

- **Evidence required:** legacy audit report; T11 binary suite passing for
  all class (b) ports; `app/modules/legacy/` inventory with deprecation
  markers; class (d) retirement list with owner sign-off per item; no
  regressions in the existing full test suite.
- **Blocks:** deleting any legacy file, removing any `legacy/` shim, or
  declaring the framework consolidation complete.
- **Safe work while waiting:** continued H5/H6/H7 deployment work; T17B
  audit and porting can run in parallel with the H5-H7 sequence after H4.
- **Approver:** project owner (same as H0-H7).

## A3 (2026-10-14, owner request): code quality closure before cutover

At the very end of the migration — after H6 and before H7 — a dedicated pass
ensures the codebase is production-ready in terms of documentation, typing and
test coverage. Nothing in this addendum authorizes new features, scientific
changes or schema modifications.

### T27: Docstring, typing and test-coverage closure

- **Depends on:** H6. **Blocks:** H7.
- **Scope:**
  1. **Docstrings.** Every public symbol in `app/` (`public.py`, `api/`,
     `modules/`) that lacks a docstring gets one. The bar is the WHY and the
     contract (preconditions, return shape, exceptions raised), not a prose
     repetition of the function name. Internal helpers that are only called
     from one place are exempt; anything that appears in a public or
     cross-module import is not.
  2. **Type annotations.** All function signatures in `app/` carry complete
     annotations (parameters and return types). `Any` is allowed only where
     the type genuinely cannot be narrowed (e.g. LangChain message payloads);
     each surviving `Any` gets a short inline comment explaining why. Run
     `mypy --strict` (or the project's configured checker) and fix or
     explicitly ignore every reported error.
  3. **Sniff test.** Do a full read-through of every module touched during
     the migration. Flag anything that smells wrong: dead branches, TODOs
     that were never resolved, commented-out code, magic literals, functions
     that are too long to reason about, and any place where the implementation
     diverges from what its docstring claims. Each flag is either fixed inline
     or recorded in the H7 review packet with a disposition (fix, defer with
     owner approval, or intentional).
  4. **Test coverage.** Run the full test suite with coverage measurement.
     Any module in `app/` with line coverage below 80% that is not already
     covered by the T11 binary suite gets additional unit or integration tests.
     Tests must exercise real behavior — not trivial pass-throughs — and must
     not duplicate assertions already in the binary suite. Exclude generated
     files, migration scripts and `app/modules/legacy/`.
- **Acceptance:**
  - `mypy` (or the configured checker) passes with no unresolved errors on
    `app/`.
  - Coverage report committed to `docs/migration/reviews/H7/coverage.txt`;
    no in-scope module below 80% line coverage.
  - Sniff-test findings committed to
    `docs/migration/reviews/H7/sniff_findings.md` with a disposition for
    every item.
  - All public symbols in `app/` have docstrings; every function signature
    has complete type annotations.
  - Full test suite green; no new skips introduced to reach the threshold.

## A4 (2026-10-15, H3 decision F5/F6/S4): samples, measurements and instruments as schema classes

At H3 the owner rejected candidate-only Sample/Measurement records: "it is
imperative to capture samples, measurements, and techniques". This overrides
the D20 rule that kept the measurement/technique split out of scope, in the
additive form the owner chose.

### T22C: Versioned additive schema change

- **Depends on:** H3 (with this decision), T22B. **Blocks:** applying F5/F6
  mappings in the T06 validation stage, T12 pilot extraction of samples and
  measurements.
- **Scope:**
  1. New LinkML classes, added to `matkg_schema.yaml` and `rsoxs_schema.yaml`
     (and the xray schema if it shares the terms) under a new schema version:
     - `Sample`: one paper-local physical specimen (label, composition, phase,
       processing state, preparation); links to the `Material` it is made of.
     - `Measurement`: one run reported in a paper; links to its technique
       (`ExperimentalTechnique`, including `RSoXSMeasurement`), its `Sample`(s),
       its `Instrument`, typed conditions, and the quantities it produced.
     - `Instrument`: a beamline, endstation or detector, shared across papers
       through canonicalization.
  2. `RSoXSMeasurement is_a ExperimentalTechnique` keeps its technique meaning;
     its description says so. No existing class, slot or node changes meaning.
  3. Licence line in every schema file set to the LBNL BSD-style licence (S4).
  4. Schema registry (T22B): new fingerprints and version, old version kept
     loadable; extraction mapping F5/F6 rows point at the new classes.
- **Acceptance:** all 12 tracked KGs validate unchanged under the new version;
  the extractor's Sample/Measurement/Instrument records map onto the new
  classes with located-quote evidence; graph JSON and Tiled backends round-trip
  the new node types; owner reviews the schema diff before it is used to build
  or publish any graph.

## A5 (2026-10-29, audit): findings to fix, hardening and points of concern

Status: **proposed by the integrating agent; nothing here is approved.** Owner
decides which items become tasks, which are accepted as-is, and which gate owns
each. Evidence: `docs/migration/reviews/audit/2026-10-29/` (independent code
audit, 20 findings AUD-01..AUD-16 plus notes; a fresh fake-model binary run).
Severity words are the auditor's; I verified AUD-01, 02, 03, 04, 09 and 10
against the code myself.

### 1. Where the plan stands

- **Gates:** H0-H3 approved (H3 on 2026-10-16, B4 to follow). H4-H8 open.
- **Tasks:** 47 of the 54 task-graph entries are merged on `forge_merge`
  (99 first-parent commits since the 222b1e7 baseline). Open: T07B (needs the
  C4 rerun), T12 (pilot), T13, T15 (with Alex), T16A/B, T19E, T24E, T26C, plus
  the addendum tasks T17B (H8), T22C-follow-ons, T27 (H7) and the seven Tiled
  editing tasks proposed in `tiled_graph_editing_plan.md` (awaiting Q1-Q16).
- **Tests:** 4,416 pass in one isolated run with the Tiled server tests
  included; 3 known failures carried from H0.
- **Images:** `:local` built and run for the first time on 2026-10-29. It
  exposed one real build bug (MkDocs `watch`, fixed in c9cf9b0) and the two
  size/reproducibility findings below. `:als-dev`/`:als-prod` are unbuilt.
- **Binary suite, fresh run with fakes:** 56 completed PASS, 52 blocked, gate
  NOT_PASS. Of the 52 blocked, **30 are blocked only on tasks that have since
  merged** (T08B, T10, T23B, T24B-D, T26B) and 22 on open work (T24E 7, pilot
  labels 9, O03 3, T13/T15/T26C 3). The manifest still records
  `H3: not_approved`, so all 108 cases count as gate-blocking.

### 2. Findings that need fixing

| ID | Sev. | Finding (evidence) | Fix | Owner |
|---|---|---|---|---|
| AUD-01 | high | Job stages never re-authorize in production. `StageContext.require` returns `None` when no authority is wired (`app/modules/jobs/pipeline.py:214-219`); neither `register` caller passes one (`jobs/publication.py:112`, `operations/reports.py:389`); `WorkerAuthority` is used only by tests. On ALS a running job keeps privileged effects after sign-out or downgrade, except where a publication approval is invalidated. | Wire `WorkerAuthority` into `JobService.register` by default; make an unwired `required_capability` a startup error, not a no-op; add an ALS downgrade-mid-job test. | T28A, before H4 |
| A5-1 | high | **Image content depends on the builder's disk.** `storage/terminology/*.json` is gitignored (`.gitignore:185`) but `Dockerfile` copies the whole directory; the local image carries 9 untracked harvest files (321 MB, ~141k paper snippets) that a CI build will not. `tests/images/test_images_context.py:111` asserts the directory ships. O-D1 says the directory "stays in Git", which the ignore rule contradicts. | Owner decision (section 4, PC-1); then either track the files or exclude `storage/terminology/*.json` in `.dockerignore` and fix the test. Add a release-manifest check that every shipped `storage/` file is tracked at the commit. | T28C |
| AUD-08 | medium | 7.1 GB of the 13.2 GB image is CUDA PyTorch: `requirements/semantic.in` pins `torch>=2.12.0` with no index, Linux resolves `torch 2.15.0+cu132` plus 3.3 GB of `nvidia-*` wheels, while the image sets `KG_RAG_FORCE_CPU=1`. `runtime.txt` and the Tiled constraints have no hashes; `pip check` fails (huggingface-hub vs click). | CPU wheel index for torch (or make semantic deps an opt-in extra and image); hash-lock both files (B24/B25). Target well under 4 GB. | T28C |
| AUD-07 + A5-2 | medium | Binary suite not reconciled after H3: `evals/binary/cases/index.json` says `H3: not_approved`; 30 cases blocked on merged tasks; 8 cases carry stale "unbuilt" text (JSON-03/04, REL-04, LLM-01, LOCAL-01/02/06, EXT-05). | Record the H3 approval in the manifest with the `decision.json` ref (human step, not agent self-approval); rebind the 30 cases to the landed tests; refresh the 8 texts; rerun and commit the report as H4 evidence. | T28B, before H4 |
| AUD-03 | medium | The legacy model-compatible API (`kg_rag_api.create_fastapi_app`) has no request context and no disclosure projector; `run_fastapi` binds `0.0.0.0:11435` (`kg_rag_api.py:339`); `project_model_chat` exists but is never called. Only nginx's 404 keeps it off ALS. T18C F3. | Apply the projector to the two chat routes; default bind to loopback; or retire the route behind a flag. | B6, T28A |
| AUD-04 | medium | Origin gate and body cap cover only `/v1/` and `/auth/` (`app/api/main.py:39`); legacy `/graph/upload` is unbounded; `create_app` falls back to dev localhost CORS origins with `allow_credentials=True` (`f2w_agent/api.py:6822`) so C7 holds only through preflight. | Apply the gate and a 128 MB cap to the 19 legacy routes (B8/B9); remove the CORS fallback on ALS profiles in code. | T28A |
| AUD-09 | medium | No CI job runs the Tiled-gated suites (~260 tests in 11 `skipif` modules); `forge-merge-ci.yml:86` runs plain pytest with `|| true`. | Add a CI job that installs the hash-checked Tiled wheel and sets `F2W_TILED_PYTHON`; drop `|| true` once compare_known_failures is the gate. | T28B |
| AUD-02 | medium | Import-time `load_dotenv()` remains in `app/run_pipeline_cborg.py:30`, `scripts/run.py:19`, `scripts/app/modules/kg_rag_api.py:59`, bypassing `F2W_DOTENV`; `COPY scripts/` ships the stale `scripts/app/modules/kg_rag_api.py` into every image. | Move the three to `dotenv_entry`; stop copying `scripts/app/` (or delete it under T17B). | T17B / T28C |
| AUD-11 | medium | README and four MkDocs pages still describe the Splash architecture, `frontend:80` and `KG_RAG_GRAPH_SOURCE=splash` as the default; MkDocs is built into all three images. `plan.md` says Tiled 0.2.19 in 18 places without the O-T1 note. | Docs refresh task; annotate plan D22/D24/D33 with O-T1. | T28D (or fold into T16A) |

### 3. Hardening (low; do with the nearest task)

- **AUD-05:** the 40% page rule is per response; ledger limits default to `None`, so a page can be reconstructed over several turns, and the echo guard ignores runs under 200 characters. Consistent with O-D2; recorded as a concern (PC-2).
- **AUD-06:** `/v1` responses use path-only projection; review excerpts rely on the 600-character cap (B10).
- **AUD-10:** approval TTL is 1 h on every profile (`app/api/operations.py:58`); B14 calls it a "24 h placeholder" and recommends none on `:local`. Code and register disagree.
- **AUD-12:** ADRs out of date: `schema-ownership.md` still forbids measurement-instance classes (overridden by T22C/A4); `job-and-approval-lifecycle.md` calls expiry "proposed"; every ADR still says `Status: Proposed` after H1.
- **AUD-13:** two of the three known failures are one-line test-isolation fixes carried since H0 (`TestMakeChatClient` reads an ambient `CBORG_API_KEY`). Fix them and shrink the list to one.
- **AUD-14:** `companion/task_graph.json` still marks every task `not_started`; T11B, T25C and T22C edited files outside their ownership; T11B re-pinned `FROZEN_UI_TREE` itself (under O-U2, but the pin change should be a separate integrator commit).
- **AUD-15:** the writer secret file is checked for readability only, not mode or owner.
- **AUD-16:** every `:local` user can read raw traces, including the network bind; fine for a trusted laptop, worth a note in the `:local` docs.
- **Notes:** stale `TODO(T03)` in `agent_runtime/supervisor.py:92`; 4 pyflakes items; startup does not assert `undeclared_routes == []`; T26A's release-contract questions Q2-Q6 were never copied into `OPEN_DECISIONS.md`; D1.5 derived tolerance is still 1% in code.
- **Local dev only:** Docker on Colima has no IPv6 by default, so CBORG refuses the container's IPv4 address. Fixed on this machine with Colima `network.address: true`, Docker IPv6 and a mounted `/etc/gai.conf`; `docker/profiles/README.md` should say so, and the image could offer `F2W_PREFER_IPV6=1`.

### 4. Points of concern for the owner

| # | Concern | Options |
|---|---|---|
| PC-1 | **Paper snippets in the image.** Shipping `storage/terminology/*.json` puts ~141k verbatim snippets from private papers into every image, including ALS images whose users are not all trusted. O-D1 accepted snippets in tracked KG JSON; the terminology harvest files are larger, untracked, and not needed by any image service (only legacy scripts read them). | (a) exclude them from images and keep them gitignored (recommended); (b) track them in Git and keep shipping, accepting the disclosure surface; (c) ship only on `:local`. |
| PC-2 | **D19 across turns.** No cumulative per-paper budget, so patient prompting can reconstruct a page. | Keep per-response rule (O-D2), or add a per-session per-paper cap with a stated number. |
| PC-3 | **Authority gap (AUD-01)** is the one place the ALS fail-closed story is not true today; it must be closed before any ALS deployment test (T24E), not just before H6. | Make T28A a hard precondition of H4. |
| PC-4 | **Image reproducibility.** Until A5-1/AUD-08 land, the digest recorded by T24D's release manifest does not describe what a developer built locally. | Treat local builds as untested until CI builds exist. |
| PC-5 | **Binary suite understates progress.** 56 of 108 cases pass today, and ~30 more are runnable; the report reads as 0/108 because the H3 approval was never recorded. | Approve the recording step in T28B. |
| PC-6 | **Approval TTL** (1 h everywhere) may expire approvals during long ALS reviews. | Confirm B14: none on `:local`, 24 h on ALS, or keep 1 h. |
| PC-7 | **Two legacy entry points still ignore `F2W_DOTENV`** and one is shipped in the image (AUD-02). | Retire under T17B or fix now. |

### 5. Proposed tasks (owner to approve)

- **T28A: Authorization and surface hardening** (before H4). AUD-01, AUD-03, AUD-04, AUD-15, startup `undeclared_routes` assertion. Acceptance: an ALS downgrade mid-job stops the job before its next effect; legacy routes carry the origin gate and body cap; model API projected and loopback-bound; all under existing fail-closed tests.
- **T28B: Binary suite reconciliation and CI** (before H4). Record the H3 approval (owner-signed), rebind the 30 cases, refresh stale texts, run the suite in CI with the Tiled wheel, fix the two trivial known failures. Acceptance: committed post-H3 report; `compare_known_failures` gates CI without `|| true`.
- **T28C: Image diet and reproducibility** (before H6; benefits T24E now). PC-1 decision applied, CPU torch or opt-in semantic extra, hash-locked requirements, drop `scripts/app/`, release-manifest check that shipped `storage/` files are tracked. Acceptance: `:local` under 4 GB; CI and local digests describe the same inputs; private-planting test covers terminology.
- **T28D: Documentation and register sync** (before H6). README/MkDocs rewritten for the three-image, JSON-default architecture; ADR statuses and the two stale ADRs; plan O-T1 annotations; `task_graph.json` statuses; T26A Q2-Q6 and the TTL mismatch into `OPEN_DECISIONS.md`.

### 6. Applied on 2026-10-29 (integrator record)

| Item | Commit | Note |
|---|---|---|
| AUD-08 CPU torch, baked MiniLM models | `a32e752` | semantic backend now works offline in every image; rebuild required |
| AUD-07 / A5-2 binary rebinding and stale texts | `f4f4746` (T28B) | 86 PASS / 22 blocked with fakes; H3 recording awaits the owner (`python -m evals.binary.approve`) |
| AUD-09 Tiled suites in CI, no `\|\| true` | `f4f4746` (T28B) | |
| AUD-13 known failures 3 -> 1 | `f4f4746` (T28B) | only the splash-wipe entry remains |
| AUD-01 stage re-authorization | `12156d1` (T28A) | `JobAuthority`; unwired capability is a startup error; live downgrade test |
| AUD-03 model API projection and loopback bind | `12156d1` (T28A) | T18C F3 expected failure now passes |
| AUD-04 legacy origin gate, 128 MiB cap, no ALS CORS fallback | `12156d1` (T28A) | B8/B9 applied as part of T28A |
| AUD-15 secret file mode/owner; startup route assertion | `12156d1` (T28A) | |
| AUD-02 dotenv entry points; stale `scripts/app` no longer shipped | T28C merge | hashed locks `runtime.lock.txt`/`constraints.lock.txt` with `--require-hashes`; `shipped_untracked_files` manifest warning (flips to refusal on PC-1) |
| AUD-10/11/12/14 docs, ADRs, plan notes, task status overlay | `e10e1b3` (T28D) | register D3.8-D3.12 added |
| AUD-06 full D19 projection on every `/v1` body and SSE event | sweep merge | 600-char clip, private keys and paths dropped |
| AUD-16 `:local` raw-trace warning when bound beyond loopback; docs | sweep merge | policy unchanged (owner-decided) |
| AUD-18 stale `TODO(T03)`; 4 pyflakes items | sweep merge | pyflakes 32 -> 28; two genuine NameErrors in legacy paths noted for T17B (`f2w_agent/api.py:4974` `history`, `term_extractor/monitored_agent.py:53` `UserAgent`) |

Still open from A5: A5-1/PC-1 (terminology files in the image, owner decision), AUD-05 (cross-turn D19 budget, PC-2), AUD-10 (TTL, D3.5/D3.8), AUD-14 process notes, D1.5 tolerance (D3.12), and the two legacy NameErrors (T17B). Full suite after T28A+T28B: 4,585 passed, 1 known failure.

Dependencies: T28A and T28B block H4. T28C and T28D block H6 and may run in
parallel with T12 and the Tiled editing tasks. All four take the existing
gate and branch rules; none needs a schema or UI change.

## A6 (2026-10-30, owner decisions O-E1 Q1-Q16, O-U3): Tiled graph editing

Source: `docs/migration/ui_contracts/tiled_graph_editing_plan.md` (sections 1-5, decision
table in section 6) and the owner's answers recorded verbatim in
`docs/migration/reviews/H1/owner_decisions.md` (batches 4-8). The seven tasks below are the
section-5 proposals, changed where an answer differs from the recommendation:

- **Q1 (revised):** one stable Tiled graph per corpus; the live version is named in a
  release marker; unchanged records keep their Tiled ids across versions.
- **Q7 (STOP-1): multi-write per Save**, not journal-and-defer. Every Save performs its
  recreate-links sequence on Tiled immediately, journaled and resumable, with a recovery
  job for half-moved links. Hence **Q12:** a Save is acknowledged only after the journal
  write *and* the Tiled effect; a partial failure returns the existing 502, marks the
  journal entry failed and leaves reconciliation to the recovery job. Q8 and Q14 apply to
  every Save.
- **Q13 (STOP-6): capability read.** The frozen UI reads the caller's capabilities and
  hides Save/edit controls for ALS non-admins (**O-U3, granted in principle**); the server
  403 stays as the backstop; the CI `FROZEN_UI_TREE` pin moves in the implementing change.
- **Q14: ignore and continue.** A foreign record that changes under a run never stops the
  run; the pre/post fingerprint report records the change in the receipt.
- Q2-Q6, Q8-Q11, Q15, Q16 as recommended (staged/switch/retire; purge as a separate
  admin-approved step, never for records with foreign links, one previous version kept;
  foreign links reported before approval; release marker written; ledger-pinned F2W reads
  during a switch; Save during a switch refused with 502; only F2W-owned links removed;
  implicit snippet unlink kept with a 409 stale-revision check; `:local` publishes
  automatically after extraction when the plan is safe, ALS waits for admin review;
  rollback below a carried-forward edit needs a reviewed resolution; one verified admin
  approves als-prod replacement or purge, revisited before the first production purge).

All tasks run only against disposable or managed `:local` Tiled servers until H5. Binary
cases TIL-08..TIL-21 are as listed in the editing plan; T18D adds a capability-read case
(TIL-22) for Q13.

### T20C: Pin edit-relevant Tiled 0.2.18 facts
- **Depends on:** T20B. **Blocks:** T08C, T08D.
- **Scope:** probes P1-P9 as `tests/tiled/test_tiled_edit_facts_0218.py`; section 4.2 notes
  into `capabilities_0.2.18.md`; capability flags `delete_cascades_foreign_links` and
  `blob_update_effective`.
- **Acceptance:** one test per fact; a server where a fact changes fails exactly that test.
- **Binary cases:** TIL-08.

### T08C: Corpus-scoped replacement import (stage, switch, retire, revive)
- **Depends on:** T20C, T08B, T10. **Blocks:** T18D, T18E, T10B.
- **Scope:** scope key = corpus (Q1), graph-id mapping for JSON unchanged, one-time adoption
  plan for the existing `:local` per-version imports; release marker (Q5); `f2w:Staged` /
  `f2w:Retired` types; two-phase stage and switch (Q2); revive-from-retired; boundary-link
  report in the plan hash (Q4); I7 read probes; ledger history per scope; ledger-pinned
  reader during a switch (Q6); abandon-plan.
- **Acceptance:** v4 to v5 on a seeded server with FORGE links leaves every foreign record
  byte-identical; unchanged nodes keep their Tiled UUIDs; boundary links are listed and
  require approval; a crash at every stage/switch point resumes without duplicates; F2W
  readers never see a mixed graph; type-filtered listings never return staged or retired
  F2W records.
- **Binary cases:** TIL-09..TIL-14.

### T08D: Guarded purge, corpus removal and the Save recovery job
- **Depends on:** T08C.
- **Scope:** purge plan with its own approval (Q3); per-record link check; owned links
  first; re-read before delete; corpus retire-all with typed scope confirmation; pointer to
  none. **Added by Q7:** the recovery job that finishes or reverts a Save whose multi-write
  sequence stopped half-way (journal entry `failed`), idempotent and resumable.
- **Acceptance:** a retired record with any foreign link is skipped and reported, never
  deleted; other scopes untouched; committed JSON untouched; re-import from Git restores the
  corpus; a Save interrupted after any Tiled write is reconciled by the recovery job to
  either the full effect or the pre-Save state, with the journal saying which.
- **Binary cases:** TIL-15, TIL-16, TIL-18 (extend: recovery).

### T23C: Edit reconciliation and rollback protection (F4/B26, Q15)
- **Depends on:** T23B, T08B. **Blocks:** T18D, T10B.
- **Scope:** full-lineage check; `reconciled` edits protect imports whose lineage lacks their
  snapshot; journal replay onto rebuilds with conflicts; content-derived edit ids for
  idempotent Save retries.
- **Acceptance:** rollback below a reconciliation snapshot is refused without a
  `ConflictResolution`; a rebuild keeps every edit or records a conflict; the T18C F4 pin
  flips.
- **Binary cases:** TIL-17, JSON-10 (extend).

### T18D: Legacy Save as journaled multi-write, plus the capability read (O-U3)
- **Depends on:** T23C, T08C, T25C. **Needs:** O-U3 (granted in principle; the UI diff is
  reviewed by the owner before merge).
- **Scope:** `update_graph_node` with no Splash calls; authorization first; the Save
  sequence (journal entry, Tiled writes, readback) executes immediately (Q7) and returns
  only after the Tiled effect (Q12); Q9 restriction to F2W-owned links; Q10 implicit unlink
  with the 409 revision check; Q8 refusal (502) during a switch; `/graph`, search and
  retrieval read the active graph plus any in-flight journal entries; "Publish edits" is
  no longer needed for Tiled effect but the journal still feeds `v<N+1>` snapshots.
  **UI (O-U3):** a capability read (`GET /v1/capabilities` or the existing session DTO)
  that hides Save/edit controls for ALS non-admins; `FROZEN_UI_TREE` updated in the same
  change; the server 403 remains (Q13).
- **Acceptance:** UI04, UI15, UI16, UI17 pass on Tiled in `tests/e2e/live_backend/full`;
  same id read back after Save; old citations resolve; a crash after the fsync but before
  the Tiled effect is answered on retry with the same readback once the recovery job has
  run; an ALS non-admin sees no Save control and gets a 403 with no journal entry if the
  request is forged.
- **Binary cases:** TIL-05 (extend), TIL-18, TIL-19, TIL-22 (new: non-admin UI shows no
  edit controls; server still 403).

### T18E: Post-extraction publication replaces `splash_reimport` (Q11)
- **Depends on:** T18D, T08C, T10.
- **Scope:** remove the wipe path and `--allow-splash-wipe` from the images; freeze
  `v<N+1>`, plan, then apply under the `:local` auto-approval policy only when the plan is
  safe; on ALS stop at review; keep the SSE phase names.
- **Acceptance:** UI12 Tiled stage passes; no delete outside the plan; ALS never
  auto-approves; an unsafe plan on `:local` waits for review; the last binary known
  failure (splash wipe) is retired.
- **Binary cases:** TIL-20.

### T10B: Rollback, replacement, job kinds and auto re-plan in the publication job
- **Depends on:** T08C, T23C. Absorbs owner decisions B5 (job kinds), B27, B28, D1.10.
- **Scope:** rollback = a replacement plan to the chosen version through revive, pointer
  history kept in step with the release marker (TIL-14); **Q14** foreign-change report in
  the receipt without failing the run; **B27** the runner re-plans automatically after a
  stale approval and waits for approval of the new plan; **B28** the two-pointer rollback
  toggle documented; **B5** job kinds for enrichment, topic sweep and PDF parse (J-11,
  RT-27..RT-32) on the `/v1/jobs` lifecycle with `jobs.admin`/`curation.approve`; **D1.10**
  the near-duplicate title check as a `knowledge_operation` job above 2,000 keys (inline
  below).
- **Acceptance:** v5 to v4 to v5 restores the same Tiled UUIDs; marker and pointer agree
  after every step; a foreign change during a run appears in the receipt and the run
  completes; a stale approval produces a new plan awaiting approval without a manual
  re-submit; each new job kind has a fake handler test and a route policy row.
- **Binary cases:** TIL-06 (extend), TIL-21.

## A7 (2026-10-30, owner decisions O-P2..O-P5, O-S2..O-S4, O-X3): parsing, schema follow-ups and small tasks

Work items the register walk-through produced that fit no existing task. Each cites its
decision in `docs/migration/reviews/H1/owner_decisions.md`.

### T29: Parsing, intake metadata and literature tooling
- **Depends on:** T05, T06. **Blocks:** H5 publication (B2), the pilot default parser.
- **Scope:**
  1. **B1:** Docling behind the pluggable parser interface; default once parity tests pass
     on the pilot papers; PyMuPDF stays available and default until then.
  2. **B2:** licensing / open-access metadata at intake (OpenAlex/Crossref fields plus the
     PDF's own notice) on the paper record; required before any H5 publication.
  3. **B5:** L-14 reference parsing, L-15 section projection, L-16 paper context, L-17
     citation links, as provenance-tagged statements (plan L-14/L-20 rule).
  4. **B3:** R-08 Langfuse tracer as an opt-in exporter behind a setting, off by default,
     with a disclosure review (traces hold source text); X-05 `seed_synthetic_kg.py` and
     X-07 `bench_kg.py` ported into `scripts/` as developer tools; A-01
     `openalex_supplement.json` retired once L-17 regenerates the citation links.
  5. **B29:** the legacy `DownloadAgent` switched to the hardened T05 downloader behind a
     parity test on the existing download fixtures.
- **Acceptance:** parser parity report on the pilot papers (Docling vs PyMuPDF; text,
  sections, tables); every intake record carries licence fields or an explicit `unknown`;
  Langfuse export is impossible without the setting and passes the disclosure projector;
  download parity test green; no live network in tests.

### T22D: Schema 0.3.1 and the bl1101 reconciliation (O-S2, O-S3)
- **Depends on:** T22C. **Blocks:** the pilot extraction (T12).
- **Scope:** **D1.4** normalized-value slot (converted value, unit, factor, conversion
  profile as provenance) as versioned additive change 0.3.1; **D1.3** bl1101 v3 mapping
  `Sample`, `Detector`, `Endstation` onto 0.3.0 `Sample`/`Instrument` (reviewed diff);
  **D1.5/D3.12** `RECIPROCAL_DERIVATION_REL_TOL = 0.02`; **D1.6** cited wins over derived
  with the derivation recorded; **D1.9** content-hash step in the K1 distinct-Work rule as
  a versioned rule change with parity tests; **D1.7** opt-in `--figures multimodal` flag on
  the labelling tool (figure crops to a vision-capable CBORG model), off by default.
- **Acceptance:** all tracked KGs validate under 0.3.1; bl1101 v3 round-trips; tolerance and
  precedence covered by unit tests; K1 parity tests pass with and without the hash step;
  the multimodal flag makes no network call unless given.

### T30: OCR engine for figures (V-05)
- **Depends on:** T09. **Scope:** a real OCR backend behind the existing figure-OCR
  interface, offline-capable in the images (model files baked like MiniLM or an opt-in
  extra), used by the scale and axis readers. **Acceptance:** fixture figures read within
  the FIG tolerances; images without the extra degrade to the current behaviour.
- **Implemented (owner decision 2026-11-03, "Docling's built-in OCR"):** `DoclingOcrEngine`
  (`app/modules/vision/docling_ocr.py`) behind the existing `OcrEngine` protocol; RapidOCR with the `torch`
  backend (no new dependency: `onnxruntime` is not locked); weights fetched at image build by
  `scripts/fetch_docling_models.py` into `<artifacts>/RapidOcr` with registry SHA256 checks; selection by
  `F2W_FIGURE_OCR_ENGINE` (default `auto`: on only when the weights exist). See `docker/profiles/README.md`
  ("Figure OCR in the images"). The real-weights fixture test skips where the weights are absent.

### T31: Missing `/v1` routes (B5)
- **Depends on:** T10, T19C. **Scope:** cost control, bulk review, vocabulary quality and
  figure-scale records as `/v1` routes with policies, DTO projection and SDK/CLI parity.
  **Acceptance:** route-policy table rows, `undeclared_routes` assertion unchanged,
  contract tests on both profiles.

### T32: `:local`-only folder routes (B4)
- **Scope:** folder browse/index routes on `:local` only, root directories fixed in
  configuration, disabled (404) on ALS images. **Acceptance:** path traversal tests;
  ALS gateway tests show 404.

### Finding from the B7 work (2026-10-30): ALS API process has no identity wiring in `run_api`

`app/api/main.py::run_api` calls `create_api_app(cfg, cors_origins)` without an identity port
or session store; `build_guard` then raises `StartupConfigError` on any ALS profile, and
`als_identity_wiring` (`app/api/sessions.py`) is only called from tests. So the ALS `api`
service cannot start today regardless of B7. This belongs to T24E/T25C and is a blocker for
the first ALS container run; the model-API mount already takes the main app's identity
port and session store, so it needs nothing extra once wired. Tracked as **D3.21** in
`OPEN_DECISIONS.md` (integrator to fix; no owner decision needed unless the wiring choice
changes the deployment contract).

### Applied directly on `forge_merge` (2026-10-30, integrator record)

| Decision | Commit | Note |
|---|---|---|
| PC-1 / D3.1 (O-D3) terminology files tracked and shipped | `7e3876a` | `.gitignore` rule dropped, nine files (163 MB) committed, `REFUSE_SHIPPED_UNTRACKED_FILES=True` |
| D3.5 / D3.8 / B14 (O-T2) approval TTL none on `:local`, 24 h on ALS | `120ef06` | `default_approval_ttl(settings)` |
| D1.5 / D3.12 (O-S2) derived-value tolerance 2% | `8b9ab77` | D1.6 (cited wins) was already the behaviour |
| D1.9 (O-S3) content-hash step, `work-identity/2` | `b3f14f3` | parity switch `use_content_hash=False` |
| D1.10 (O-S4) near-duplicate check as a job above 2,000 keys | `e59c9c1` | `ScanControl(job=True)` marks job runs |
| D1.7 (O-S3) `--figures multimodal` labelling flag | `f6934d7` | guide version 2; off by default |
| O06 D2.1-D2.10 (O-O6) publish workflow and approved tag rules; B17 SQLAlchemy 2.0.55; B25 click verified | `76e6f28` | owner still sets `O06_APPROVED` and the two GitHub environments (rules file section 6); `companion/CI_CD_CONTRACT.md` left as supplied |
| D3.21 ALS `api` service identity wiring | `62738e0` | `run_api` builds the Tiled identity port and session store off `:local` |
| D3.16 (O-X4) UI chat turn may answer figure questions with the knowledge operations | `f21a745` | rendered report as the answer text; no UI change |
| B27 (O-X3) automatic re-plan after a stale plan or approval | `45df5ea` | `RestartStages` in the runner |
| T22D (O-S2) schema 0.3.1 and bl1101 v3 | `8066633` | owner review of `docs/migration/reviews/H3/schema_diff_T22D.md` before 0.3.1 is used to build or publish |
| T24E container check finding: model-API mount wrote under the read-only app root | this record | `MissingNodeTracker` now writes under the workdir and disables itself on a read-only directory |
| B30 (O-P5) T15 F2W-side surface for FORGE | `875fbfd` | open gaps: no `/v1/literature` or `/v1/evidence` routes; K-12 kg tools unported (query_knowledge, trace_provenance pending); no durable evidence store before T08C/T10B; FORGE-side changes wait for the owner and Alex (`forge_spec.md`) |
| D3.17 (O-X4) default Tiled graph backend for the operations on every profile | `adb1c9e` | read-only; JSON wins an id collision; Tiled-discovery warnings are logged, not yet surfaced in `/v1/operations` |
| T26A Q2 adoption of legacy KG files (O-R1) | `aa78a21` | tool + 6 adopted releases; 53 files skipped on R1/R2/R3/R4/R7 or empty (D3.22-D3.24); no descriptor committed (Q6 open) |
| B7 (O-A9) model API on ALS for signed-in users | `bad6c39` | mounted in the API process; D-T8 (non-browser sign-in) still open; worker found `run_api` wires no identity port on ALS (see A7 note) |

## A8 (2026-11-02, owner request; B4/O-P4 kept, new UI exception O-U4): Library and Extraction tabs

Two additive left-sidebar tabs on `:local`: **Library** (reference PDFs of a mounted `papers/sourcedoc`
folder: file name, title, DOI, drag-and-drop upload, use for Extraction and QA) and **Extraction** (is one
running, its progress and logs; otherwise continue an existing KG or start a new KG for a topic with the
papers, model, KG name and schema chosen). Built to the ALS standards (typed Python, Google docstrings, Typer
CLIs, pytest; React with Tailwind, shadcn, Finch, TanStack Query, `@als-computing` tokens, JSDoc, Vitest and
React Testing Library). The owner decisions are fixed; this addendum records them and does not reopen them.

- **Scope:** `:local` only (B4/O-P4). On ALS every `/v1/library/*` route answers 404 and the tabs hide
  themselves (the probe-and-hide pattern of `judgeModel.ts`). ALS-admin support is **T44**, deferred.
- **O-U4 (new UI exception, granted by this request):** two additive Finch `HubSidebar` routes. The frozen-UI
  process applies: `ui-change-approved` label, the `FROZEN_UI_TREE` re-pin as its own integrator commit with a
  dated comment, `docs/migration/ui_contracts/` updated, no new `localStorage` keys.
- **Uploads** are written into the mounted papers/sourcedoc folder itself (read-write, uid 10001, optional
  mount) through `read_upload_limited`, `require_pdf`, `safe_child` and `LocalIntakePolicy`; deduped by sha256;
  no symlinks; no PDF download route; route paths are `/v1/library/...` so none matches `SOURCE_SERVING_PATH_RE`.
- **Title and DOI:** local first (PDF info title, DOI regex on pages 1-2, DOI in the file name; no network),
  plus an explicit **Verify** (OpenAlex through `intake.prepare_local_pdf`, existing quota breaker), cached.
- **QA:** Library feeds the `hybrid_rag` lexical source index only (no embeddings).
- **Execution:** new `/v1` job kinds run in the worker process, which gets a real factory (today it is
  `idle_service`, so no job runs in the images). Kinds: `library_verify`, `library_index`, `library_extract`.
- **KG bundles:** a KG built from the Extraction tab is a bundle in the state volume, registered as selectable
  in Settings, with an explicit export to `storage/kg`. Publication to Tiled stays the gated T10/H5 flow.
- **Continue a KG:** reverse-builds a terms JSON from any KG JSON (`kg_to_terms`, including the `nodes/edges`
  shape of `matkg_rsoxs_v3.json`).
- **Topic** is a label and a paper-picker filter and also selects the schema, locked for the life of the KG.
- **Relation to existing tasks:** T32 is superseded by T35 (same routes, same `:local`-only rule). T10B owns the
  publication job kinds; T34 only adds the generic worker factory. T29 owns parsing and intake metadata; T35 and
  T36 call the same functions. RT-17/RT-18 (`paper_route_ledger.md`) are covered by T35 for PDF upload; add-by-DOI
  stays with T31/T29.

### T33: Per-job log channel
- **Depends on:** T14 (merged). **Blocks:** T34, T39, T42.
- **Scope:** a `job_logs` table (job_id, seq, at, level, stage, message; bounded per job, messages scrubbed with
  the existing scrubbers); `JobStore.append_log`/`logs`, `StageContext.log`, `JobService.logs`;
  `GET /v1/jobs/{id}/logs?after=` and `event: job_log` on `/stream` with `Last-Event-ID` resume; a
  `JobLogHandler` keyed on the process's single active library job, not on the calling thread, because
  `Orchestrator.process_pdf` runs its own page thread pool. Additive schema; `JobEvent.progress` stays
  `dict[str, int]`.
- **Acceptance:** log lines survive a worker restart; concurrent jobs do not cross-capture; the cap is enforced
  and secrets are scrubbed; the route has a policy row, `undeclared_routes` stays empty, the route count rises;
  legacy OpenAPI and ledger tests are unchanged.

### T34: Worker factory, library job kinds and capabilities
- **Depends on:** T33. **Blocks:** T35, T36, T39.
- **Scope:** `app/modules/library/`; `register_library_jobs` called in `app/api/main.py` next to
  `register_knowledge_jobs` and in a new worker factory in `app/modules/workers/factories.py`;
  `F2W_WORKER_FACTORY` changed in `docker/profiles/local.json` only (the ALS profiles keep `idle_service`);
  capabilities `library.read`, `library.upload`, `library.index`, `library.extract` for the local operator
  only; a nonce input for "rebuild" (job identity is idempotent on identical inputs).
- **Acceptance:** a `:local` worker claims and completes a no-op library job; ALS images register no library
  kinds and refuse submissions of them; secrets in `inputs` or `params` are refused; identical submissions
  return the same job and a changed nonce a new one; `tests/images/test_images_profiles.py` updated.
- **Implementation note (2026-11-02):** `library_probe` (`library.read`) is the no-op kind the acceptance needs;
  `library_verify` (`library.upload`), `library_index` and `library_extract` are fail-closed stubs
  (`unsupported_capability`, "not implemented until T35/T36/T39") replaceable through `register_library_jobs(handlers=)`.
  The four capabilities are `LOCAL_ONLY_CAPABILITIES` in `contracts/context.py` (not in the ALS admin set). The worker
  factory is `library_service`, which refuses an ALS profile. No route and no policy row are added (kinds ride `POST /v1/jobs`);
  any later row uses V43 or higher. Details: `docs/migration/api/README.md` section 2.0.

### T35: Library catalog, upload and verify (supersedes T32)
- **Depends on:** T34, T29 (functions only). **Blocks:** T36, T38, T41.
- **Scope:** `F2W_LIBRARY_DIR` (the mount) and a sidecar `<F2W_WORKDIR>/library/catalog.sqlite`; a
  `LibraryService` (listing keyed by relpath and sha256; local metadata; upload; verify);
  `GET /v1/library`, `GET /v1/library/papers`, `POST /v1/library/papers` (multipart),
  `POST /v1/library/papers/{id}/verify`; verify-all as the `library_verify` job; an optional mount entry in the
  `:local` profile. Without the mount the status route says `mounted=false`.
- **Acceptance:** traversal, symlink, non-PDF, oversize and duplicate-upload tests; written files are owned by
  uid 10001; local metadata costs zero network calls (socket-blocking test); verify caches the registry title and
  reports an open quota breaker; no route path matches `SOURCE_SERVING_PATH_RE` and no route returns PDF bytes;
  the ALS gateway answers 404.

### T36: Library in the source index
- **Depends on:** T35, T34. **Blocks:** T38 (BM25 ranking), T41 (indexed badge).
- **Scope:** a `library_index` job builds chunks into the literature index directory with corpus `library`,
  `doc_id=lib:<relpath>`, `work_id` from the DOI or `lib:<sha256[:16]>`; `meta["docs"]` records sha256,
  `n_chunks`, title, DOI, pages and a status (`ok`, `no_text`); incremental by sha256, removed or changed
  documents pruned; atomic write under a file lock; `SourceIndex` reloads when `meta.json` changes; the index
  lives on the state volume and the profile sets `HYBRID_RAG_LITERATURE_INDEX`.
- **Acceptance:** an unchanged re-run does nothing; a replaced PDF is re-chunked and a deleted one pruned;
  scanned PDFs are reported `no_text` without failing the job; a QA question over a library fixture returns a
  library citation; a concurrent reader never sees a torn index; the existing `hybrid_rag` tests pass.

### T37: `kg_to_terms` and `TermStore` hardening
- **Depends on:** none. **Blocks:** T38, T39.
- **Scope:** reverse a flat KG JSON into a terms JSON, including an adapter for the `nodes/edges` shape (label to
  name, entityType to category, papers to source_papers, slots); `TermRecord` gains an `extra` passthrough and
  `RelationRecord` gains `evidence`; `TermStore._load` skips a bad record with a warning; reconstructed
  bookkeeping is marked `reconstructed`; stubs (no papers, no description) are not promoted;
  `has_code_snippet` edges are regenerated; the tool fails closed on `TermStore` key collisions; a Typer CLI.
- **Acceptance:** over every (terms file, KG file) pair on disk, terms to KG to `kg_to_terms` to `TermStore` to
  rebuild equals the KG up to ordering and newer fields; a continuation test (add two synthetic papers, the old
  subgraph is unchanged); an idempotence test for KGs with no terms file; existing extraction and `kg_update`
  tests pass unchanged.

### T38: KG bundles, schemas, models and cost estimate
- **Depends on:** T37, T35. **Blocks:** T39, T42.
- **Scope:** bundles at `<F2W_WORKDIR>/kgs/<name>/` (`terms.json`, `kg.json`, `manifest.json` with topic, schema,
  model, backend, papers with sha256, parent bundle and the `reconstructed` flag, `processed_pdfs.json`,
  `extraction_manifest.json`); schema choices from the schema registry, locked per bundle; additive
  registration in the Settings graph list (`list_storage_kg_json_files`, `_resolve_json_graph_path`) with a
  manifest-based schema lookup; `GET /v1/library/kgs|schemas|models` (models over `CachedModelCatalog`, never
  `PUT /settings`); `POST /v1/library/estimate` (pages times model) and a confirmation step; export of a bundle
  to a configured release directory as `matkg_<name>.json` with the schema name in its metadata; paper-picker
  ranking (keyword; BM25 once indexed); "continue" on a KG without a bundle runs `kg_to_terms` into a child bundle.
- **Acceptance:** `tests/contracts/legacy_ui` passes with bundles absent and present; the schema cannot change
  on continuation; export outside the configured directory is refused; the model route reports unavailable
  without a backend; the estimate is deterministic; bundle names are validated and collisions refused.

### T39: `library_extract` job (resumable per PDF)
- **Depends on:** T38, T37, T34. **Blocks:** T42.
- **Scope:** a chunked stage with one `WorkUnit` per PDF through `Orchestrator.process_pdf` (unit key = pdf
  sha256 plus extractor, schema and model revision), then `assign_importance`, `save` and
  `kg_update.rebuild_kg`, then the bundle manifest; model, paper list, KG name, schema and topic ride in
  `OperationInputs` (the model never goes through `PUT /settings`); a per-bundle lock allows one extraction per
  bundle; cancellation stops between PDFs; the same `processed_pdfs.json` and `extraction_manifest.json` the
  coordinator writes.
- **Acceptance:** a killed worker resumes without redoing finished PDFs; the unit key changes with model or
  schema; a fake-LLM end-to-end run yields the bundle `kg.json` equal to what the CLI produces for the same
  inputs; a failed PDF is recorded and the others finish; no network call without a configured backend; progress
  and logs are visible while the job runs.

### T40: UI foundations (O-U4)
- **Depends on:** T33, the T35 route contract (can start on mocked routes). **Blocks:** T41, T42.
- **Scope:** `libraryApi.ts` and `extractionApi.ts` following `liveAgent.ts` (`AGENT_API_BASE`,
  `agentApiErrors.ts`); the SSE reader exported from `liveAgent.ts` and reused for the job stream; TanStack
  Query hooks; a probe-and-hide hook; two `routes` entries in `ui/src/app/App.tsx` with labels short enough for
  the 6rem sidebar; Vitest widened to `.test.tsx` with `@testing-library/react`, `user-event`, `jest-dom`;
  first verify that switching tabs does not unmount the chat or graph state.
- **Acceptance:** mocked-fetch API tests; the tabs are hidden when the probe returns 404; switching tabs loses
  no chat or graph state (test); `npm run build` and the Dockerfile frontend-build stage pass.

### T41: Library tab
- **Depends on:** T40, T35 (T36 for the indexed badge). **Blocks:** T43.
- **Scope:** a shadcn table (file name, title, DOI, status; `truncate`), a keyboard-accessible native dropzone with
  an upload mutation, per-row and bulk Verify, "Index for QA", an indexed badge, empty and "not mounted" states;
  `@als-computing` tokens, no hardcoded hex, disabled styling, ARIA labels; JSDoc on every export.
- **Acceptance:** one component test per prop and state plus ARIA tests; a non-PDF drop is rejected client-side
  and a server error is shown; no new `localStorage` key.

### T42: Extraction tab
- **Depends on:** T40, T38, T39, T33. **Blocks:** T43.
- **Scope:** running view with progress and a live log pane (SSE, reconnect with `Last-Event-ID`, cancel),
  re-attaching after a page reload through `GET /v1/jobs?state=running&kind=library_extract`; idle view with
  continue-or-new, topic, schema (disabled when continuing), a paper picker (filter, count, select all), a model
  picker following the `JudgeModelSetting` pattern, KG name, cost confirmation and export. It never changes the
  Settings model.
- **Acceptance:** component tests for idle, running, finished, failed and no-backend; Start stays disabled until
  the name is valid, at least one paper is chosen and the cost is confirmed; the log pane is bounded; a reload
  during a run re-attaches.

### T43: UI and docs closure
- **Depends on:** T41, T42. **Blocks:** H6.
- **Scope:** Playwright specs modelled on `ui/e2e/judge-model.spec.ts` with `/v1` mocks in
  `ui/e2e/fixtures/agent.ts`, run in both configs (standalone and `/fair2wise` prefix); the `ui-change-approved`
  label; the `FROZEN_UI_TREE` re-pin as its own integrator commit with a dated comment in
  `.github/workflows/forge-merge-ci.yml`; `docs/migration/ui_contracts/`; mkdocs `ui-library.md` and
  `ui-extraction.md`; the capability and route-policy docs; `task_status.json`.
- **Acceptance:** `tests/ci` green with the new pin; both Playwright configs green; `mkdocs build --strict`
  and the frozen-UI contracts green.

### T44: ALS-admin support (deferred follow-up)
- **Depends on:** the ALS worker identity wiring (a worker factory with an identity port and credential lookup).
  **Scope:** enable the library routes and tabs for verified ALS admins, with a mount policy and a disclosure
  review for uploaded PDFs reaching other users through Source RAG. Not built under A8.

### T45: Harvester backend (`library_harvest` job, candidates, paywalled queue)
- **Depends on:** T34, T35, T33. **Blocks:** T46, T43.
- **Owner answers (2026-11-03):** the schema only **labels** the run (recorded with it so Extraction can default to
  it; it does not change search or ranking); open-access PDFs are **auto-downloaded** for tier A/B hits up to a
  maximum the operator sets; paywalled hits go to a **queue in the tab** (no server-side publisher request).
- **Scope:** new job kind `library_harvest` (capability `library.harvest`, `:local` only, runs in the worker).
  `params`: `topic` (1-200 chars), `category` (a library folder name, created on first filing), `schema`
  (registry id, label only, optional), `sources` (subset of `arxiv`, `openalex`; default both), `max_downloads`
  (1-200, default 20), `per_query` (1-100, default 50), `nonce`. Stages: search each source (arXiv through the
  pinned `arxiv` package, OpenAlex through `OpenAlexClient.fetch` under the shared quota breaker; a source that
  cannot run is logged and skipped), merge and dedupe by DOI/arXiv id, drop papers already in the library (by
  DOI), score each candidate against the topic (generic tiers: A = every topic term present and at least half in
  the title; B = at least 60 % of the terms present in title or abstract; C = the rest, never downloaded), then
  for tier A then B in score order download the open-access PDF (every hop checked by `NetworkPolicy`, byte and
  time limits, `%PDF-` check) and file it with `LibraryService.upload(..., folder=category)` (year folder and
  category manifest as in A8 uploads). A tier A/B hit without a usable open-access PDF becomes `paywalled`. Every
  candidate and its outcome is stored in a sidecar `<workdir>/library/harvest.sqlite` (run id = job id).
  Paywalled hits are completed by a person: the tab shows the landing URL (opened in the user's own browser),
  the user saves the PDF and drops it on the candidate, and the server files it like an upload.
- **Routes (policy rows V76-V79, `:local` only):** `GET /v1/library/harvest/runs`,
  `GET /v1/library/harvest/runs/{run_id}/candidates`, `POST /v1/library/harvest/candidates/{id}/pdf`
  (multipart `file`; files into the run's category), `POST /v1/library/harvest/candidates/{id}/dismiss`.
- **Acceptance:** fake arXiv and OpenAlex (no network in tests), a socket-blocking test; tiering, dedupe,
  max-downloads, paywalled/failed/in-library outcomes, per-source failure isolation, SSRF refusal of a private
  OA URL, filing by year, candidate PDF drop and dismiss, policy rows and route counts.

### T46: Harvester panel in the Library tab
- **Depends on:** T40, T41, T45. **Blocks:** T43.
- **Scope:** the Library tab gets two panels switched by a tablist ("Papers" with the table and uploader, and
  "Harvester"). Harvester: topic, category (existing or new), schema (label), sources, max downloads, Start;
  then a run view (progress, live log, cancel) and a results table per run with the status of every candidate
  (downloaded, in library, failed, skipped) and the **paywalled queue**: "Open publisher" link (new tab,
  `rel="noopener noreferrer"`), a PDF drop slot per candidate, Dismiss. Past runs are listed and reopened.
- **Acceptance:** component tests per state, ARIA tablist and `aria-pressed`/disabled styling, Playwright specs
  in both configs with mocks, no `localStorage` use.

### Order for A8
T33 and T37 first (parallel), then T34 and T40 against mocked routes, then T35 and T38, then T36 and T41, then
T39 and T42, then T43. Tests assert 31 `/v1` routes and 19 legacy routes, so route-count edits merge in the
order T33, T35, T38.

## Order after H2

T25D, T03B and T02B first (they can run in parallel), then the plan's
post-H2 ports (T04 packaging, T05 literature/parsing, T06 extraction/evidence,
T07 canonicalization, T08 graph ports and Tiled adapter, T09 paper figures,
T10 jobs, T11 binary evaluation, T20B Tiled lifecycle) toward H3/H4. A8 (T33-T44) is the
Library and Extraction tabs and runs after the job and worker foundations (T10, T14) are merged.

## Deployment notes for T24E

- **T24E requirement (OPEN_DECISIONS C8):** the ALS edge proxy (Ansible/Traefik)
  must overwrite `X-Forwarded-Proto` with the scheme the browser actually used,
  never pass a client-supplied value through. The in-container gateway forwards
  only the literal `http`/`https` it receives (`docker/nginx.conf`), and the API
  builds its URLs and same-origin checks from it.
- **T24E handoff landed without containers (2026-10-29):** `deploy/` holds the Ansible
  role/plays for the two ALS profiles (digest-only image, state + read-only writer-secret
  mounts, 0600 uid-10001 secret file, nginx edge overwriting `X-Forwarded-Proto`,
  `/fair2wise/healthz` checks, `--stop-timeout 120`, per-host digest record with rollback,
  backup/restore with manifest and sha256), the host tools `deploy/scripts/`, and
  `tests/deployment/` (static checks, restore/rollback harness on a real `JobStore`
  database, `F2W_CONTAINER_TESTS` stubs). No play has run against a host; `ansible-lint`
  is not in the venv, so the YAML is validated structurally. Real inventories, FQDNs and
  secret references are operator inputs (O07). The container-level checks and their
  commands are listed in `tests/deployment/README.md`; they wait on D3.11. D3.14 applied:
  the CI Tiled job installs `--require-hashes` from `constraints.lock.txt` like the image.
  D3.15 applied: the two supervisor tests with fixed sleeps now wait on events.

## Legacy modules retained for real ports (owner decision 2026-11-04)

T17B (H8) found that five modules the audit proposed archiving are imported by
`app/modules/f2w_agent/api.py`, which class (r) retains as the browser contract:
`tiled_graph.py`, `orchestrator_agent.py` with `debate_agent.py`, `download_agent.py`,
`kg_update.py` and `bl1101_ingest.py`. Owner decision, verbatim: "reclassify as retained, stay in
place. add a note in the addendum we want real ports". They are class (r) and stay where they are.

**Direction:** the owner wants real ports, not shims. Target homes from the T17B execution record
(`docs/migration/reviews/H8/legacy_audit.md` section 8): `tiled_lookup` (the live lookup and probe in
`tiled_graph.py`) under `graph/backends/`; routing and debate under `agent_runtime/` and `curation/`;
`kg_update` under `graph/`; `bl1101` ingestion under `graph/imports/` (a multi-day task). Each port needs its
own task, tests and owner scheduling before H8; none is started.

**Status 2026-11-06** (owner: "start the work now"; branch `forge_merge-T17B-ports`, not merged): `tiled_graph.py` ->
`graph/backends/tiled_lookup.py` and `f2w_agent/kg_update.py` -> `graph/kg_update.py` are ported (old paths are
deprecated alias shims; wire behaviour unchanged; legacy_ui contract tests unchanged). `download_agent.py` audited:
PDF bytes already flow only through the hardened downloader; its three metadata hops (pyalex, arxiv, Unpaywall) are
pinned by test and proposed as P1. `orchestrator_agent`/`debate_agent` (P2, design decision needed) and
`bl1101_ingest` (P3, multi-day) are proposals only. Record and proposals:
`docs/migration/reviews/H8/legacy_ports_plan.md`.

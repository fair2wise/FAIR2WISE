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

## Order after H2

T25D, T03B and T02B first (they can run in parallel), then the plan's
post-H2 ports (T04 packaging, T05 literature/parsing, T06 extraction/evidence,
T07 canonicalization, T08 graph ports and Tiled adapter, T09 paper figures,
T10 jobs, T11 binary evaluation, T20B Tiled lifecycle) toward H3/H4.

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

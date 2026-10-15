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

## Order after H2

T25D, T03B and T02B first (they can run in parallel), then the plan's
post-H2 ports (T04 packaging, T05 literature/parsing, T06 extraction/evidence,
T07 canonicalization, T08 graph ports and Tiled adapter, T09 paper figures,
T10 jobs, T11 binary evaluation, T20B Tiled lifecycle) toward H3/H4.

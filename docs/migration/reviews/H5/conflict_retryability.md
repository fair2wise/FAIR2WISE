# CONFLICT retryability audit (T-conflict-audit)

Branch `forge_merge-T-conflict-audit`, based on `forge_merge` d50f4fb. Line numbers are those at the base commit.

## Why

`RETRYABLE_CODES` contains `ErrorCode.CONFLICT`, so every `ContractError(ErrorCode.CONFLICT, ...)` is `retryable=True` unless it says otherwise. Since T-leasefix2, `PipelineRunner._run_stage` requeues a job on any retryable CONFLICT (up to `MAX_CONFLICT_REQUEUES = 5`, exponential backoff) instead of failing it. That is correct for a busy lock or a race that a re-read heals. It is wrong for a conflict that needs a human or a changed input: the job would bounce five times over minutes before failing. The same `retryable` flag is on the wire: `_contract_error_handler` (app/api/auth.py) puts it in every /v1 error body, and `ToolResult.retryable` feeds the agent executor's retry loop (executor.py:442-444).

## Method

Each of the 87 `ErrorCode.CONFLICT` hits in app/ was read in context. Six are not raise sites and need no decision: the status maps (auth.py:68, disclosure.py:200 and 207), the enum default (errors.py:46), the executor retry filter (executor.py:444) and the pipeline requeue test (pipeline.py:541). The other 81 hits are the 78 table rows (the four-line `contract_error` map is one row; `is_switch_refusal`, legacy_save.py:475, is a reader row). Rule: TRANSIENT means a plain re-run of the same job can succeed without anyone changing anything (a busy lock, another run's lease, a read that raced a writer, an optimistic-concurrency race the job re-reads); PERMANENT means the same inputs give the same refusal (stale source or plan hash, duplicate or foreign ownership, already-exists, state precondition, no history to roll back to, configuration).

## Table

| Site | Message | Raised by | Wire | Class | Reasoning | Decision |
|---|---|---|---|---|---|---|
| `app/modules/curation/repair.py:32` | repair planning aborted | RepairAborted | - | Permanent | planning aborted on inconsistent input | keep (already `retryable=False`) |
| `app/modules/curation/store.py:67` | uncertainty case lease is no longer owned | lease lost in case store | - | Permanent | ownership lost; the run must stop | keep (already `retryable=False`) |
| `app/modules/curation/store.py:389` | case is {state}, not awaiting review | resolve a case | - | Permanent | the case was already resolved/rejected; re-reading returns the same state | `retryable=False` (new) |
| `app/modules/literature/store.py:460` | destination exists | object store move | - | Permanent | never overwrites | keep (already `retryable=False`) |
| `app/modules/literature/legacy.py:453` | {key} holds different bytes | legacy import | - | Permanent | content clash | keep (already `retryable=False`) |
| `app/modules/literature/staging.py:206` | package import already running | `_exclusive` flock(LOCK_NB) | - | Transient | another worker's import holds the lock and ends | keep (default retryable) |
| `app/modules/literature/staging.py:241` | package changed during import; no receipt written | `import_package` re-hash | - | Transient | the file moved under the import; a re-run hashes the settled file | keep (default retryable) |
| `app/modules/canonicalization/apply_adapters.py:778-781` | stale_source / stale_vocabulary / plan_tampered / node_missing | `contract_error` map of `ApplyRefused` | - | Permanent | each needs a re-plan, a re-approval or a human; the body already carries `reason` | `retryable=False` (new) on the mapped error |
| `app/modules/graph/snapshots.py:115` | {path} does not match its manifest graph.sha256 | `load_snapshot` | - | Permanent | the file on disk is wrong; only a human or a new snapshot fixes it | `retryable=False` (new) |
| `app/modules/graph/edit_journal.py:148` | edit expects seq N+1 | `EditJournal.append` | - | Permanent | optimistic-concurrency: the same record never becomes valid; the caller must rebuild it | `retryable=False` (new) |
| `app/modules/graph/edit_journal.py:150` | edit must start at seq 1 | `EditJournal.append` | - | Permanent | as above | `retryable=False` (new) |
| `app/modules/graph/edit_journal.py:229` | import would lose N acknowledged edit(s) | `check_import` | - | Permanent | needs a human decision (fixed in the previous commit) | keep (already `retryable=False`) |
| `app/modules/graph/identity_adapter.py:159` | identity already bound differently | `IdentityLedger.bind` | - | Permanent | ownership/identity clash | `retryable=False` (new) |
| `app/modules/graph/imports/receipts.py:108` | a different receipt with this id already exists | `ReceiptStore.write` | - | Permanent | already-exists; receipts are immutable | `retryable=False` (new) |
| `app/modules/graph/backends/tiled.py:147` | Tiled entity no longer carries identity | `TiledGraphBackend._resolve` | - | Permanent | the ledger and the live row disagree; re-reading the same row gives the same answer | `retryable=False` (new) |
| `app/modules/graph/backends/tiled.py:153` | N Tiled entities claim (graph, entity) | `_resolve` | - | Permanent | duplicate ownership needs cleanup by a human | `retryable=False` (new) |
| `app/modules/graph/backends/tiled.py:262` | an entity disappeared during traversal | `_one_hop` | - | Transient | raced a writer between two reads; a fresh traversal succeeds | keep (default retryable) |
| `app/modules/graph/backends/tiled_mutations.py:99` | the {key} listing changed while it was read | `_scan` pagination | - | Transient | offset paging raced a writer; a re-scan succeeds | keep (default retryable) |
| `app/modules/graph/legacy_save.py:73` | StaleSave | legacy Save from an out-of-date copy | legacy route 409 | Permanent | legacy contract (Q10) | keep (already `retryable=False`) |
| `app/modules/graph/legacy_save.py:207` | the graph is being republished; try again | `_refuse_during_switch` | legacy Save | Transient | a switch in flight ends; `is_switch_refusal` depends on it | keep (already explicit True) |
| `app/modules/graph/legacy_save.py:220` | the active snapshot graph does not match its recorded hash | `base_graph` | - | Permanent | the file differs from the configured sha256 | `retryable=False` (new) |
| `app/modules/graph/legacy_save.py:475` | (reader) `is_switch_refusal` | isinstance check | - | n/a | reads `.retryable` to tell a switch refusal from other conflicts; the permanent edits above do not touch it | no change |
| `app/modules/graph/imports/save_recovery.py:66` | StaleRevision | record changed since the edit was made | legacy route 409 | Permanent | legacy contract (Q10) | keep (already `retryable=False`) |
| `app/modules/graph/imports/save_recovery.py:129` | edit is not a recorded, unapplied edit | `recover_save` | - | Permanent | the edit already moved on; same state on re-read | `retryable=False` (new) |
| `app/modules/graph/imports/save_recovery.py:132` | the graph is being republished; try again | `recover_save` | - | Transient | switch in flight ends | keep (already explicit True) |
| `app/modules/graph/imports/save_recovery.py:260` | the graph is being republished; recover afterwards | recovery | - | Transient | switch in flight ends | keep (already explicit True) |
| `app/modules/graph/imports/save_recovery.py:334` | cannot revert Save: no revision to restore | `recover_save` revert | - | Permanent | no earlier revision exists; needs a human | `retryable=False` (new) |
| `app/modules/graph/imports/journal.py:81` | another import holds the lease | `Lease.acquire` (O_EXCL race) | - | Transient | the holder finishes or its lease expires | keep (default retryable) |
| `app/modules/graph/imports/journal.py:86` | another import holds the lease | `Lease.acquire` | - | Transient | same | keep (default retryable) |
| `app/modules/graph/imports/journal.py:94` | the import lease was taken over; stopping | `Lease.check` | - | Transient | this run stops; a new run can re-acquire after expiry | keep (default retryable) |
| `app/modules/graph/imports/corpus_scope.py:196` | (unrecognized uri text) | `classify_scope` | - | Permanent | a row claims this scope with a uri that fits no state; a human must repair the target | `retryable=False` (new) |
| `app/modules/graph/imports/corpus_scope.py:211` | two live release markers | `classify_scope` | - | Permanent | inconsistent target | `retryable=False` (new) |
| `app/modules/graph/imports/corpus_scope.py:215` | two Tiled entities claim (scope, node) | `classify_scope` | - | Permanent | duplicate ownership | `retryable=False` (new) |
| `app/modules/graph/imports/corpus_scope.py:221` | two {kind} revisions | `classify_scope` | - | Permanent | inconsistent target | `retryable=False` (new) |
| `app/modules/graph/imports/corpus_scope.py:252` | the target changed while it was being read; retry | `read_scope_state` double read | - | Transient | raced a writer; the message already says retry | keep (default retryable) |
| `app/modules/graph/imports/corpus_scope.py:600` | the target changed while it was being read; retry | `plan_replacement` double read | - | Transient | same | keep (default retryable) |
| `app/modules/graph/imports/corpus_scope.py:603` | the target holds per-version imports ... adopt_legacy | `plan_replacement` | - | Permanent | needs a changed input (`adopt_legacy=True`) | `retryable=False` (new) |
| `app/modules/graph/imports/corpus_scope.py:676` | another plan is switching this scope | `ReleaseState.mark_switching` | - | Permanent | the other plan must be resumed or abandoned by an operator; the flag persists across crashes. Judgement call (could be a live switch that ends) | `retryable=False` (new) |
| `app/modules/graph/imports/corpus_scope.py:781` | Tiled entity no longer carries identity | `PinnedReader._record` | - | Permanent | identity changed under the pin | `retryable=False` (new) |
| `app/modules/graph/imports/corpus_scope.py:795` | pinned entity vanished from Tiled | `PinnedReader.get_entity` | - | Permanent | the pinned version is gone; a re-read does not bring it back | `retryable=False` (new) |
| `app/modules/graph/imports/corpus_scope.py:804` | pinned entity vanished from Tiled | `PinnedReader.entities` | - | Permanent | same | `retryable=False` (new) |
| `app/modules/graph/imports/corpus_scope.py:911` | the source snapshot no longer matches the plan | `apply_replacement` | - | Permanent | stale source hash; needs a new plan and approval | `retryable=False` (new) |
| `app/modules/graph/imports/corpus_scope.py:1206` | the switch of this plan has started; resume it | `abandon` | - | Permanent | needs the operator to choose resume | `retryable=False` (new) |
| `app/modules/graph/imports/corpus_purge.py:97` | (unrecognized uri text) | retired-row scan | - | Permanent | inconsistent target | `retryable=False` (new) |
| `app/modules/graph/imports/corpus_purge.py:495` | N open edit(s) block the removal | `plan_removal` | - | Permanent | needs a human resolution of the edits | `retryable=False` (new) |
| `app/modules/graph/imports/tiled_json.py:86` | import verification failed | `ImportVerificationError` | - | Transient | already explicit retryable=True: resume re-verifies | keep (already explicit True) |
| `app/modules/graph/imports/tiled_json.py:268` | the target changed while it was being read; retry | `read_state` double read | - | Transient | raced a writer | keep (default retryable) |
| `app/modules/graph/imports/tiled_json.py:280` | (unrecognized uri text) | `classify_state` | - | Permanent | inconsistent target | `retryable=False` (new) |
| `app/modules/graph/imports/tiled_json.py:288` | two Tiled entities claim (graph, node) | `classify_state` | - | Permanent | duplicate ownership | `retryable=False` (new) |
| `app/modules/graph/imports/tiled_json.py:294` | two {kind} revisions | `classify_state` | - | Permanent | inconsistent target | `retryable=False` (new) |
| `app/modules/graph/imports/tiled_json.py:621` | the source snapshot no longer matches the plan | `apply_import` | - | Permanent | stale source hash | `retryable=False` (new) |
| `app/modules/library/bundles.py:232` | a KG bundle with this name already exists | `BundleStore.create` | - | Permanent | already exists | `retryable=False` (new) |
| `app/modules/library/bundles.py:265` | a KG bundle with this name already exists | `fork` (name == parent) | - | Permanent | input error | `retryable=False` (new) |
| `app/modules/library/bundles.py:313` | a KG bundle with this name already exists | `continue_from_kg` | - | Permanent | already exists | `retryable=False` (new) |
| `app/modules/library/bundles.py:364` | the export target is a link | `export_kg` | - | Permanent | filesystem state needs a human | `retryable=False` (new) |
| `app/modules/library/bundles.py:372` | a different KG file with this name exists in the release directory | `export_kg` | - | Permanent | foreign file; never overwritten | `retryable=False` (new) |
| `app/modules/library/kg_service.py:119` | a KG with this name exists in the release directory | `KgService.continue_kg` | - | Permanent | already exists | `retryable=False` (new) |
| `app/modules/library/harvest.py:590` | candidate is {status}, not paywalled | `_paywalled` | - | Permanent | state precondition | `retryable=False` (new) |
| `app/modules/library/service.py:405` | no free file name for this upload | `upload` | - | Permanent | all candidate names taken after bounded attempts | `retryable=False` (new) |
| `app/modules/library/extraction.py:106` | another extraction is running for this KG bundle | `BundleBusy` | - | Transient | busy flock; explicit retryable=True | keep (already explicit True) |
| `app/modules/library/extraction.py:303` | (CaptureBusy text) | extraction log capture | - | Transient | another job is capturing; explicit retryable=True | keep (already explicit True) |
| `app/modules/jobs/service.py:274` | job is owned by a running worker | `cancel` | - | Transient | worker finishes; explicit True | keep (already explicit True) |
| `app/modules/jobs/replacement_step.py:261` | no verified receipt covers this version | `ReplacementStep` noop path | - | Permanent | needs a human to reconcile receipts | `retryable=False` (new) |
| `app/modules/jobs/replacement_step.py:267` | the live records are not the ones the covering receipt verified | same | - | Permanent | drift; needs a human | `retryable=False` (new) |
| `app/modules/jobs/publication.py:361` | another publisher holds this target | `_import` | - | Transient | competing publisher's lease; explicit True | keep (already explicit True) |
| `app/modules/jobs/publication.py:474` | another publisher holds this target | publish stage | - | Transient | same | keep (already explicit True) |
| `app/modules/jobs/publication.py:608` | another publisher holds this target | publish stage | - | Transient | same | keep (already explicit True) |
| `app/modules/jobs/publication.py:817` | there is no previous pointer to roll back to | `_previous_for` | /v1 rollback body | Permanent | history does not grow by waiting | `retryable=False` (new) |
| `app/modules/jobs/publication.py:824` | corpus has no earlier version | `_previous_for` | - | Permanent | reason `no_previous_corpus_version` | keep (already `retryable=False`) |
| `app/modules/jobs/publication.py:838` | this deployment moves a Tiled release marker ... | `rollback` | - | Permanent | reason `rollback_replacement_required` | keep (already `retryable=False`) |
| `app/modules/jobs/publication.py:877` | this deployment has no replacement importer; use rollback | `rollback_replacement` | /v1 body | Permanent | configuration, not timing | `retryable=False` (new) |
| `app/modules/jobs/store.py:94` | lease on {resource} is no longer held | `LeaseLost` | - | Permanent | explicit False | keep (already `retryable=False`) |
| `app/modules/jobs/store.py:103` | job cannot move from X to Y | `InvalidTransition` | - | Permanent | explicit False | keep (already `retryable=False`) |
| `app/modules/jobs/store.py:491` | job changed since it was read | `transition` expected_revision | - | Transient | explicit True (re-read, retry) | keep (already explicit True) |
| `app/modules/jobs/store.py:493` | job is owned by a running worker | `transition` | - | Transient | explicit True | keep (already explicit True) |
| `app/modules/jobs/publish_store.py:358` | active pointer changed since the job read it | `switch` CAS | - | Transient | explicit True: the job re-reads the pointer | keep (already explicit True) |
| `app/modules/jobs/publish_store.py:423` | there is no previous pointer to restore | `PublishStore.rollback` | /v1 body | Permanent | history does not grow by waiting | `retryable=False` (new) |
| `app/modules/jobs/publish_store.py:444` | corpus has no earlier version | `_rollback_corpus` | - | Permanent | explicit False + reason | keep (already `retryable=False`) |

## Counts

- Table rows: 78 (one covers four lines, one is a reader row).
- PERMANENT: 54, of which 42 rows changed to `retryable=False` in this task and 12 already were.
- TRANSIENT: 23, of which 13 already explicit `retryable=True` and 10 keep the implicit default (locked in by the AST test, see Tests).
- Owner-gated: none.

## Wire-visible changes

Only `/v1` bodies built by `_contract_error_handler` change: `"retryable": true` becomes `false` for the changed permanent sites (409 status and `code` are untouched). No legacy route is affected: `tests/contracts/legacy_ui` fixtures contain no CONFLICT body or `retryable`, the legacy Save sites (`StaleSave`, `StaleRevision`) were already `retryable=False`, and `is_switch_refusal` only reads the transient switch-in-flight sites, which are unchanged. Agent tools see `ToolResult.retryable=False` for the same sites, so the executor no longer retries an idempotent tool on them. No test asserted `retryable` on any changed site. Owner-gated legacy sites: none.

## Tests

`tests/jobs/test_conflict_permanence.py`: seven permanent clusters each drive the real raising code from a job stage and assert the first `work_one()` ends `FAILED` with zero `conflict_requeue` events (stale-source refusal mapping, identity clash, edit-journal sequence, snapshot-vs-manifest hash, bundle exists, duplicate final entities, unrecognized uri); three transient controls (flock held, import lease held, scan changed between reads) assert the job requeues. `test_every_conflict_site_states_its_retryability_or_is_a_known_transient` is an AST audit: any `ContractError(ErrorCode.CONFLICT, ...)` in app/ without an explicit `retryable=` must be in the allowlist of 10 reviewed transient sites, so a new site has to decide.

## Recommendation: flip the default (not done here)

The cleaner design is to take `CONFLICT` out of `RETRYABLE_CODES` and write `retryable=True` only at the transient sites. Of the 23 transient rows, 13 are already explicit and 10 implicit; the 54 permanent rows outnumber them and the failure mode is asymmetric: a forgotten `retryable=False` silently turns a human-needed conflict into five bounces, while a forgotten `retryable=True` fails a job once with a clear message that an operator can retry. Required changes: add `retryable=True` to the 10 implicit transient sites (the AST test lists them) and delete `retryable=False` noise at permanent sites (optional). Blast radius of the flip:

- `ContractError.__init__` default (errors.py:66) and `RETRYABLE_CODES` (errors.py:46); the `ErrorCode` docstring.

- `PipelineRunner._run_stage` (pipeline.py:541) and `_requeue_for_conflict`: only explicit-True conflicts requeue; tests/jobs/test_conflict_requeue.py already uses explicit values.

- `_contract_error_handler` (auth.py:380-386): `retryable` in every /v1 CONFLICT body; any SDK or client that retries on it changes behavior for implicit sites (none inspected here outside app/; the public client in app/public.py documents `retryable` only for UNAVAILABLE).

- Every `.retryable` reader: agent_runtime/executor.py:207,442-444 (idempotent tool retry), runtime_bridge/qa_tool.py:379, operations/reports.py:261, vision/tools.py:196, literature/tools.py:257, legacy_save.is_switch_refusal (its sites are explicit True, safe).

- Tests that assert `retryable` on a CONFLICT without an explicit flag: tests/library/test_library_extract.py:365 and tests/graph_import/test_legacy_save.py:185 (both sites are explicit True already, so unchanged). Third-party code subclassing `ContractError(CONFLICT)` would need review.

Recommended as a follow-up task with the owner, since it changes /v1 bodies for the 10 implicit transient sites from true to false unless they are made explicit in the same change.


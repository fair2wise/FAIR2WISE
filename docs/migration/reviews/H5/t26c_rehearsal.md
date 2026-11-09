# T26C rehearsal record (gate H5): exact Git snapshot promotion, rollback and recovery on disposable targets

Date: 2026-11-09. Branch: `forge_merge-T26C-rehearsal` (from `forge_merge` 83bca15). Executable evidence:
`tests/rehearsal/test_t26c_rehearsal.py` (14 tests).

**Scope and safety.** This is a rehearsal against DISPOSABLE targets. No live production write is made or authorized
(plan T26C). Nothing here touches Docker, `f2w-local`, a central or ALS Tiled, the network (a socket tripwire fails
the test on any non-loopback connect) or a model. The publication allowlist is unchanged. The "targets" are an in-memory
Tiled double and, when `F2W_TILED_PYTHON` is set, one Tiled 0.2.18 server that the existing test fixtures start on an
ephemeral `127.0.0.1` port and delete afterwards.

**Approvals in this evidence are test-harness approvals, not the owner's.** Inside the disposable test world the harness
calls the product's own `create_approval` as the explicit TEST approver identities `t26c-test-approver-staging`
(profile `als-dev`) and `t26c-test-approver-prod-disposable` (profile `als-prod`), verified by a fake identity port. They
authorize nothing outside the test world. No approval was made on behalf of the owner in any real deployment.

The profiles are the real ALS profiles with every ALS rule running (admin approver, verified identity, approval
bound to profile/target/plan, `corpus_scope`). The only override is `DeploymentSettings.for_disposable_test_target`
(loopback `127.0.0.1:<port>` only, Python-only entry point, unreachable from environment or requests).

## What the rehearsal verifies, in plan order

| # | Test | Verifies |
|---|---|---|
| 01 | `test_t26c_01_select_by_commit_path_sha256_and_mount_identical_bytes_into_two_targets` | A throwaway Git repo holds `synthetic/v1` and `othercorpus/v1` (commit c1) and `synthetic/v2` (c2). Each snapshot is selected by `(data_commit, path, sha256, git_blob)`, hashed independently with `git show` + `hashlib` and by `lookup_snapshot`. Identical bytes are exported to two target mounts (8 files per target: graph, manifest, validation, flat compat copy, for v1 and v2), re-hashed on both, idempotent on repeat. Git HEAD, refs and status are unchanged (the tooling writes nothing to Git). |
| 02 | `test_t26c_02_tampered_file_wrong_hash_wrong_commit_and_paths_outside_the_allowlist_are_rejected_typed` | Typed rejections, each leaving the mount empty: tampered file at a later commit `wrong_hash`; tampered graph against its manifest `manifest_mismatch`; wrong sha256, wrong git blob, malformed sha, v2 hash for the v1 path: `wrong_hash`; unknown commit `git`; branch, `HEAD`, abbreviation, upper-case id, a tree id: `bad_commit`; v2 path at c1 `missing`; `..` traversal `bad_path`; `.env`, `papers/`, `README.md`, `storage/source_index/` selected as the graph: `layout`; R1 allowlist (`check_paths`) names every outside path. Mount-side tamper: re-export is `export_conflict` and the import refuses the file ("graph.json no longer matches manifest graph.sha256", `INVALID_INPUT`). |
| 03 | `test_t26c_03_promote_v1_then_v2_with_distinct_target_approvals_and_receipts` | v1 then v2 promoted through the product's publication job on the staging-profile target and on the `als-prod` disposable target. Plan `input_sha256` equals the Git-pinned sha256 on both. Per target: own plan hash, approval id, receipt id, receipt target and approver; the staging receipt is not valid for the production-profile target (`receipt_valid_for`); a staging approval is `DENIED` on the other target's plan; a v1 approval is `STALE_APPROVAL` for the v2 plan. Read-back equals the committed graph (14 nodes/9 links for v1, 15/10 for v2). FORGE-owned rows byte-identical. |
| 04 | `test_t26c_04_crash_mid_import_then_replay_leaves_no_duplicate_facts[6 points]` | `fault=` crash at `stage-0`, `stage_effect-1`, `verify_staged-0`, `switch_effect-0`, `before_receipt-0`, `before_history-0` during v1 to v2 on the `als-prod` target; the dead owner's lease blocks a second run (`CONFLICT`); after lease expiry the replay resumes and verifies. No duplicate URIs, one release marker, nothing left staged, read-back equals v2, re-planning is a no-op. The source is the commit-selected release object, so the receipt records the data commit. (The 17-point matrix stays in `tests/graph_import/test_corpus_replacement.py`.) |
| 05 | `test_t26c_05_a_save_is_reconciled_into_a_new_committed_version_before_cross_environment_promotion` | A Save-style edit (node patch + new relationship) on live v2 is journaled and applied. Until reconciled it protects itself: re-importing v2 or rolling back to v1 is `CONFLICT` (direct plan and the job route; Tiled unchanged). `create_snapshot(edit_journal=...)` then freezes v3 (parent v2, relation `edit_reconciliation`), the fixture owner commits it (c4), and only then is it mounted and promoted: first to staging (edit survives), then identical bytes to the `als-prod` target with its own approval and receipt. |
| 06 | `test_t26c_06_rollback_by_selecting_the_earlier_committed_snapshot_and_by_the_replacement_rollback_route` | Rollback by selecting v1 again by `(commit, path, sha256)` (own plan, approval and receipt; every v1 Tiled id is live again; marker and pointer agree; FORGE rows unchanged), then the `rollback_replacement` route forward to v2 and back to v1. Pointer history `activate v1, activate v2, activate v1, rollback v2, rollback v1`. The pointer-only rollback is refused (`rollback_replacement_required`). |
| 07 | `test_t26c_07_a_rollback_of_one_corpus_never_touches_the_other_on_the_same_target` | The E2E finding (owner_decisions 2026-11-06). Two corpora with the SAME node ids on one target. The route rollback after the other corpus moved the pointer plans 0/0/0 operations and writes nothing. Moving `synthetic` to v2 and rolling it back by selecting v1 leaves every `othercorpus` entity and link row, its release marker and the FORGE rows byte-identical. Read-back equals each corpus's committed snapshot. |
| 08 | `test_t26c_08_zero_inference_the_tripwires_are_armed_and_nothing_tripped` | Provider admission, the CBORG key lookup, the OpenAI client kwargs and non-loopback `socket.connect` are counted tripwires (autouse for the whole module). The test proves they are armed (4 trips) and that a whole promotion trips none. Every other test in the module fails if a tripwire fires. |
| 09 | `test_t26c_09_second_production_profile_target_on_a_real_disposable_tiled_server` | v1, v2 and rollback to v1 through the importer on a real Tiled 0.2.18 (`als-prod` rules, loopback address): three plans, three receipts, every receipt records the data commit, read-back equals each committed graph, 17 seeded FORGE rows unchanged. Skipped without `F2W_TILED_PYTHON`. |

## Recorded results (run of 2026-11-09)

Command (clean export of the branch; inline env; see the recipe below): `pytest tests/rehearsal -s -v -n 0`.
Result: **14 passed in 90 s** serial (host load average 21 to 35 from other work; 81 s with `-n 4` at load 35 to 45;
the module builds its three snapshots once per worker). Per-test calls: 09 15.2 s, 01 14.1 s (+12.4 s one-time
fixture build), 02 10.1 s, 05 7.9 s, 03 6.2 s, 06 4.6 s, 07 4.0 s, 04 1.9 to 2.7 s each (6 points), 08 1.5 s. The
module is in the normal not-slow suite.

Data used (all synthetic; v1 graph 14 nodes / 9 edges, v2 15 / 10):

- `data_commit` v1 `69cfb710b8ad909d0b92529ba981bbaff622099c`, v2 `caea826675cc0efbb6a9e293353a748e739b8ff3`,
  v3 (edit reconciliation) `b4fde77502fe258d0e91be6f616785600b7c9168` (parent `caea8266...`).
- Graph sha256: v1 `34b9b8c08a32cbda485225210d444d65324c57c2ad6dac3270c4df1b7d126510`, v2
  `d0974b36d18b6aead8b67b540e5f61265b4f2e1885a9cdcd1109772f980ad95c`, v3
  `1d9dc70e66b46257217d250adf7e58396994bc2342b32c3b77545da66b7721b8`.

Receipts as printed (the same bytes, a different target, a different plan/approval/receipt). Commit ids, plan hashes and
receipt ids are not stable across runs (they embed timestamps and target revisions); the graph hashes are.

| Step | staging (`als-dev`, `127.0.0.1:59811`) | production-profile disposable (`als-prod`, `127.0.0.1:59822`) |
|---|---|---|
| v1 receipt | `receipt-c9e9079207f8b3d6c189`, approval `t26c-appr-52ab1d00db` | `receipt-aa55b58dc8027017e1fd`, approval `t26c-appr-cd2fec1abf` |
| v2 receipt | `receipt-2a99da7f2cc45ef57c5f`, approval `t26c-appr-68d2185775` | `receipt-f99d0e5b78e9a4d9a598`, approval `t26c-appr-860172a286` |
| v3 (edit reconciled) | `receipt-ff1de30faa7973715bf0` | `receipt-6d7cc87400044893fe64` |

Other recorded values: crash/replay receipt `receipt-7327171aad4ebfad9ad9` (data commit `caea8266...`, 15 entities,
10 links, 0 duplicate URIs); rollback by selection: receipt `receipt-df1f3c60bc70292aa3aa` (the first v1 receipt on that
target was `receipt-2212b5d1c192d73345ff`), 14 Tiled ids restored; isolation: 14 shared node ids, 24 `othercorpus` rows,
target 36 entities / 19 links, route rollback plan `0/0/0`, 0 writes; zero inference: 4 tripwires armed, 0 model calls, 0
non-loopback connects; real disposable server: `als-prod` rules, receipts `receipt-e087ed64e74d92989c67`,
`receipt-596b1ae5c5c276e95dbf`, `receipt-e977ed4bfbb1cb00fc1d`, 14 entities after rollback, 17 FORGE rows unchanged.

## Findings (gaps for H5, no product bug found)

1. **The publication job's receipt does not record the data commit.** `ReplacementPublishStep` loads a mounted
   directory, so `receipt.extras.data_commit` is `null` on the job path (test 03 prints it). The receipt is still bound to
   the Git bytes by `snapshot_sha256` (equals the pinned sha256) and `manifest_sha256`. The commit is recorded when the
   importer's source is the commit-selected release object (`lookup_snapshot`/`verify_git_release`; tests 04 and 09).
   Release contract section 8 says descriptors reference receipts by `receipt_sha256`, which works either way. Owner
   question: should the job path carry the commit (needs the export step to leave a commit record on the mount)? Not
   changed here; it is a design choice, not a defect.
2. **Edit reconciliation has no operator entry point of its own.** `create_snapshot(edit_journal=...)` (and the
   post-extraction freezer, relation `rebuild`) is the only path that replays the journal into a new version; there is no
   command or route that builds an `edit_reconciliation` version from the journal alone, and `f2w-kg-snapshot` has no
   journal option. The edit protection itself works (test 05). Also, the journal marks edits `reconciled` when the new
   snapshot is created, before the owner commits it; the rehearsal checks the commit exists before any export.
3. **The mount must include the flat compat copy.** Import refuses a mounted snapshot without
   `storage/kg/matkg_<corpus>_<id>.json` ("compat copy ... is missing"). The descriptor's artifact list includes it, so
   `f2w-kg-release export` mounts it. A hand-rolled export of only graph/manifest/validation fails.
4. **A path absent at the commit is `missing`, not a layout error.** `.env`/`papers/...` paths that do not exist at
   the commit report `missing`; committed ones report `layout`. Both are typed refusals and nothing is exported.
5. Approvals: a production-profile plan needs its own verified-admin approval per target and per plan; nothing here
   weakens that (the approval is `DENIED` across targets and `STALE_APPROVAL` across plans).

## How the owner re-runs this against their own second disposable target

The pytest module runs the whole sequence against self-contained disposable targets, with no host named anywhere. To
re-prove the code on a given checkout, from a clean export (never inside the repository directory: the repository
`.env` would leak into the run):

```sh
git archive <branch> | tar -x -C <export-dir> && cd <export-dir>
env -i HOME=<tmp> PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  F2W_TILED_PYTHON=<tiled-venv>/bin/python <app-venv>/bin/python -m pytest tests/rehearsal -s -v -n 0 -p no:cacheprovider
```

(Set `F2W_TILED_PYTHON` inline as shown; if it is unset test 09 skips and the other 13 still run.) `-s` prints one
`T26C|<label>|<json>` evidence line per area.

To mount the exact tested bytes into a second disposable target of their own, the owner uses the existing release tooling
(`f2w-kg-release`), with the target named only as a parameter:

```sh
# 1. Verify the committed bytes against the tested record and write the descriptor (data_commit is a full 40-hex id).
f2w-kg-release verify-committed --repo <repo> --record <release-dir>/<id>.release_record.json \
    --commit <DATA_COMMIT> --out <release-dir>
# 2. Independent check of the pin (must equal the descriptor's sha256).
git -C <repo> show <DATA_COMMIT>:storage/kg/<corpus>/<vN>/graph.json | shasum -a 256
# 3. Export identical bytes to the second target's mount (refuses a wrong hash/commit/path, never overwrites).
f2w-kg-release export --repo <repo> --descriptor <release-dir>/<id>.descriptor.json --dest <TARGET_MOUNT>
# 4. Re-hash what landed on the mount.
shasum -a 256 <TARGET_MOUNT>/storage/kg/<corpus>/<vN>/graph.json
```

Then publish through that target's normal publication flow (submit, review the plan, approve as that target's verified
admin, run, read back). The plan's `input_sha256` and the receipt's `snapshot_sha256` must equal step 2. The
disposable-target override used by the tests accepts only `http://127.0.0.1:<port>`; a target on another host needs the
product's real profile settings and is outside this rehearsal.

## What remains for H5

- Owner sign-off in `docs/migration/reviews/H5/decision.json` (not made here).
- The owner names the real production-profile target. Nothing in this rehearsal authorizes a write there; the real
  promotion needs the owner's own approval of the plan for that target, made in the product.
- Owner decisions on findings 1 and 2 (record the data commit on the job path; an operator entry point for edit
  reconciliation). Neither blocks the rehearsal result.
- H6 repeats package/container parity.

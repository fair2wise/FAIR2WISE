# Tiled graph editing plan (decision packet)

- **Status:** Proposed. For owner review. No application code, setting, endpoint or credential was changed.
- **Date:** 2026-10-16. **Branch:** `forge_merge-tiled-edit-plan` (from `forge_merge` 476877a).
- **Owner request (verbatim):** "we might need to plan deeper how tiled graph edits will happen. One case would be
  removing a previous version KG out of tiled graph and ingest the latest version (only done by admins, without
  affecting any other parts of the tiled graph)"
- **Builds on:** `legacy_splash_to_tiled_adr.md` (STOP-1..6), `../ui_reports/T18C_full_parity.md` (F1, F2, F4),
  `docs/adr/graph-mutation-semantics.md`, `docs/adr/tiled-deployment.md`,
  `docs/adr/evidence/tiled/capabilities_0.2.18.md`, `../companion/JSON_KG_PUBLICATION_SPEC.md`,
  `../companion/TILED_DEPLOYMENT_SPEC.md`, `../companion/FRONTEND_COMPATIBILITY.md`, `../json_kg_contract.md`,
  `../reviews/OPEN_DECISIONS.md` (A4, B26). Decisions D21, D27, D30, D31, D34; O-T1 (Tiled 0.2.18 interim pin).
- **Labels:** **[Verified]** = observed in a test or probe run against a disposable Tiled 0.2.18 server during this
  task (section 4.1). **[Source]** = read in the installed 0.2.18 package or in F2W code. **[Assumption]** = not
  checked. **[Proposed]** = design for your decision.

---

## 0. The short version

**What you asked for is possible on Tiled 0.2.18, but not by "delete version N, then import version N+1".**
Three facts force a different shape:

1. **Deleting a Tiled entity also deletes every link attached to it, including links other people made.** If FORGE
   linked a Sample to one of our Material nodes, deleting our node silently deletes FORGE's link. This happens even
   under Tiled's tag access policy, even for a link the deleting user cannot see. [Verified]
2. **Today every KG version gets its own graph id in Tiled** (`synthetic_v1`, `synthetic_v2`, `rsoxs_v4`,
   `rsoxs_v5`). Importing v2 leaves all of v1 in place; nothing removes it. [Verified] And because the graph id is a
   create-only property, a v1 record can never be "re-used" as a v2 record.
3. **Tiled has no transactions, no upsert and no in-place property edits.** Every change is a sequence of separate
   writes. [Verified]

**Recommended approach for version replacement (scenario a):**

- Give each **corpus** one stable scope in Tiled (`rsoxs`, not `rsoxs_v4`). Record which version is live in a small
  F2W "release marker" record in Tiled and in F2W's publication pointer.
- Replace N by N+1 as a **reviewed difference**, not a wipe: records that did not change stay exactly as they are
  (same Tiled ids, so FORGE links to them keep working); changed and removed records are **retired**, never
  deleted; new and changed records are **staged** out of sight first and then switched in.
- Retired records stay in Tiled, hidden from F2W readers and from type-based listings, so a rollback to N is a quick
  "switch back" and nobody's links are deleted.
- Physical deletion ("purge") is a **separate, later, admin-approved step**, and it skips any record that still has
  a link from another owner.
- Before anything is written, the plan lists every foreign link that touches a record we will retire. The admin
  approves that list together with the plan (the approval is bound to the plan hash).
- Before and after, F2W fingerprints every record it does not own. The import only gets a receipt if all of them are
  byte-identical.

**For UI Save (STOP-1):** record the edit in F2W's edit journal, show it immediately in the F2W UI, and carry it
into Tiled with the next reviewed publication. One code path writes to Tiled: the reviewed importer.

**What I need from you:** the decision table in section 6 (16 questions). The ones that change baseline behaviour
are marked there and in section 3.

---

## 1. Ownership and scoping model

### 1.1 Who writes to a shared Tiled graph

| Writer | What it writes | How F2W recognises it |
|---|---|---|
| **F2W literature importer** (this plan) | One entity per KG node, one link per KG edge, per corpus | Owner markers in properties (1.2) |
| **Other F2W corpora** (`rsoxs`, `bl1101`, ...) | Same shape, different scope | Same markers, different scope key: **foreign to this plan** |
| **FORGE** | Beamline identity: ESAF, Proposal, Sample, BlueskyRun, links such as `rel:hasSample`. Possibly links from its records to F2W literature records [Assumption: not confirmed with Alex] | No F2W markers: foreign |
| **Other Tiled users** | Anything their role allows. Without an access policy, any signed-in user can create graph records [Verified, T20B] | No F2W markers: foreign |
| **Catalog-bound entities** (`nodeId` set) | Entities tied to a Tiled catalog node; they inherit the node's access | F2W never binds `nodeId`: foreign |

On ALS, F2W writes as a Tiled admin (D27). **An admin bypasses every Tiled tag check** [Verified]. So Tiled's
access control does not protect anyone from F2W. F2W's own ownership check is the only guard, and this plan treats
it as a hard rule.

### 1.2 How F2W marks what it owns

**Today [Source: `identity_adapter.py`, `literature_writer.py`]:**

| Record | Marker |
|---|---|
| Entity | `uri = f2w:kg/<graph_id>/node/<node_id>`; properties `f2w_owner = "f2w-literature"`, `f2w_graph_id`, `f2w_id`. All three must agree or the record is treated as foreign. |
| Link | properties `f2w_owner`, `f2w_graph_id`, `f2w_edge_id` (hash of subject, predicate, object) |
| Revision in flight | `uri = f2w:kg/<graph_id>/pending/<node_id>/<plan tag>` (invisible to readers) |
| Superseded revision | `uri = f2w:kg/<graph_id>/retired/<node_id>/<plan tag>` (invisible to readers) |

Properties are create-only, so these markers are fixed at creation. The URI and the entity type can be changed
later with `updateEntity`.

**Proposed changes [Proposed]:**

1. **Scope key = corpus, not version.** `f2w_graph_id` (and the URI segment) becomes the corpus scope, for example
   `rsoxs`. This is what `bl1101` already does by accident: its graph id has no version suffix, so its imports
   converge in place. JSON mode keeps today's `rsoxs_v4` graph ids and file names unchanged (json_kg_contract.md
   section 3). Nothing has been published to an ALS server yet (H5 not passed), so this is free to change now.
2. **Release marker.** One F2W entity per scope, `uri = f2w:kg/<scope>/release`, type `f2w:Release`, properties
   `{snapshot_id, graph_sha256, manifest_sha256, plan_sha256, receipt_id, data_commit}`. Each publication creates a
   new marker and retires the old one in the same switch step. Anyone reading Tiled (FORGE too) can see which version
   is live without asking F2W.
3. **Neutral type for invisible records.** Staged and retired entities get `entityType` `f2w:Staged` or
   `f2w:Retired`. Their real category stays in the properties. Reason: other readers list entities by type. For
   example the legacy beamline lookup lists `entities(entityType: "Sample")` (`tiled_graph.py:44`). Without this,
   our retired "Material" records would still show up in anyone's "list Materials" query.
4. **Access tags for reading only.** Every F2W record is created with
   `accessBlob = {"tags": ["f2w_literature", "f2w_corpus_<corpus>"]}`, or whatever tags the ALS operators define
   (Q-T3). F2W never uses tags to decide ownership and never changes tags after creation, because:
   - an entity's access blob cannot be read back through GraphQL [Verified];
   - without an access policy, a blob change is silently ignored [Verified];
   - with a policy, the policy decides whether the change is allowed.
5. **No global changes.** F2W never calls `upsertNamespace` or `deleteNamespace` on a shared server. Namespaces are
   server-wide and change how everyone's predicates read back [Verified, T20B item 14].

### 1.3 Why one stable scope per corpus

Illustrative example: `rsoxs` v4 has 1,200 nodes and 3,000 edges. v5 changes 40 nodes, adds 60 and removes 10.
FORGE has made 3 links from its Sample records to v4 Material nodes.

| | Per-version graph id (today) | One scope per corpus (proposed) |
|---|---|---|
| Records written for v5 | 1,260 new entities + all ~3,000 links | 100 new entities (40 revised + 60 new) + the links that touch them |
| v4 records after the switch | All 1,200 must be retired by a new operation that does not exist yet | 50 retired (40 superseded + 10 removed); 1,150 untouched |
| FORGE's 3 links | All 3 end up on retired records | Only those touching the 50 changed or removed nodes |
| Tiled ids of unchanged nodes | All change | Unchanged |
| Rollback to v4 | Switch back to the v4 copy | Revive 50 retired records, retire 100 |

Under per-version ids, the only way to keep FORGE's links on live records would be to recreate them. F2W must not
do that, because F2W does not own them.

### 1.4 What "without affecting any other parts of the tiled graph" requires

The importer must guarantee eight invariants. Each one is checked by machine, not by reading counts.

| # | Invariant | How it is checked |
|---|---|---|
| I1 | Every record not owned by this scope is byte-identical before and after. That includes other F2W corpora, FORGE and all other users. Compared fields: id, uri, name, type, properties, endpoints, link access blob. | Complete double scan before (both reads must agree), then a fingerprint per foreign record (`foreign.json`, already in T08B). Re-scan after and compare every key. **Exists today.** |
| I2 | No foreign record disappears. That catches a cascade from a delete. | Same comparison: a missing key fails verification. **Exists today.** |
| I3 | F2W never calls `deleteEntity` on an entity that has a non-owned link. | Read the entity's incoming and outgoing links immediately before each delete. Refuse if any is foreign. **New (purge only).** |
| I4 | F2W writes only the record ids listed in the approved plan. | Intent journal against plan operations. An unexpected operation on resume is refused as stale. **Exists today.** |
| I5 | Every foreign link whose F2W end will be retired is listed in the plan, and the approval covers that list. | The "boundary link" report is part of the plan detail, so it is inside the plan hash. **New.** |
| I6 | No server-wide setting changes (namespaces, principals, tags). | The write client has no such methods; a test checks this. **Exists (no method); test new.** |
| I7 | Reads by other F2W corpora and by FORGE return the same results before and after, except where they follow a boundary link from I5. | Probe queries recorded in the receipt: the legacy beamline lookup and each other scope's node count. **New.** |
| I8 | The target is the profile's fixed Tiled server and the plan's scope. | `target_in_deployment`, client/target binding, approval bound to target and scope. **Exists today.** |

Before: plan-time scan and fingerprints; preview of the boundary links; the approval covers the hash.
During: per-target lease; fencing check before every write; intent journal.
After: re-scan; the I1, I2, I7 comparison; readback of every owned node and edge against the snapshot (already in
`verify_projection`); a receipt only when all of it passes.

A foreign record changed by someone else during the run (for example FORGE editing its own Sample) also fails I1.
F2W cannot tell who made a change, because Tiled 0.2.18 has no audit feed. The plan fails closed: no receipt, the
import is quarantined, and an admin re-runs verification and confirms the change is not ours (decision Q14).

---

## 2. Scenario catalogue

### 2.0 The common pipeline

Every Tiled write by F2W goes through the T08B importer driven by the T10 publication job:

```
plan (read-only)  ->  admin approval bound to plan hash  ->  stage  ->  verify staged  ->  switch
                  ->  verify final  ->  receipt  ->  activate pointer (+ index)
```

- **Plan** reads the whole target twice and builds the operation list, the boundary-link report and the
  foreign fingerprints. The plan hash binds snapshot bytes, target, scope, base revision, edit resolutions and the
  boundary report.
- **Approval** is a human action, never a model or tool (`Publication.submit_approval`).
  - `:local`: the local operator, no sign-in (D34).
  - ALS: a verified Tiled admin (D30); executed by the F2W writer account (D27). Receipts record `requested_by`,
    `approved_by` and `executed_as` separately.
  - An approval for one target never covers another. A staging receipt does not authorize production (D31,
    REL-03).
- **Stage** creates every new or revised entity under a `pending` URI with type `f2w:Staged`, plus the owned links
  that touch them. Readers ignore staged records.
- **Switch** is a short run of single-record updates:
  1. retire the old revision (URI and type);
  2. promote the staged revision (URI and real type);
  3. create new links between already-live endpoints;
  4. delete stale owned links;
  5. swap the release marker.
- **Verify** runs I1 to I8 plus the content readback. Only a full pass writes the receipt.
- **Activate** moves F2W's publication pointer and the retrieval index (T10). The old pair stays active until then.

T08B today interleaves stage and switch per node. Splitting them is task T08C (section 5).

### 2.1 Scenario (a): version replacement (the owner's case)

**Who.**
- `:local`: the local operator (D34).
- ALS: verified admins only. Plan, approve and purge are all admin actions. Readers are chat-only.
- On `als-prod`, the snapshot must be the committed one that passed staging (D31).

**Choosing the order.**

| Option | What happens | Verdict |
|---|---|---|
| **Delete N, then import N+1** | `deleteEntity` on every N record. Cascades into FORGE's links [Verified]. Between delete and import the corpus is empty for every reader. A crash leaves nothing. Rollback means a full re-import. | **Rejected.** Breaks "without affecting other parts" by construction. |
| **Import N+1, switch, retire N (purge later)** | Stage N+1 out of sight, switch in a short step, retire the replaced records but keep them. Optional, separate purge. | **Recommended.** |
| **Keep both with an active-version pointer, indefinitely** | Both versions live in Tiled. Readers choose by pointer. | Not as the end state: FORGE and type listings see two copies, and Tiled grows with every version. The recommended option keeps exactly one previous version revivable, which gives the same rollback benefit. |

**Steps (recommended).**

1. **Preconditions.** N+1 is a frozen, verified snapshot (manifest, validation, disclosure pass) whose lineage
   includes every acknowledged edit made against N (2.1.4). No publication lease is held on the scope.
2. **Plan** against the live scope. The admin sees a dry-run report, for example:
   ```
   rsoxs: v4 -> v5   target https://tiled-staging.als.lbl.gov/   plan 3f9c...e1
     unchanged 1,150 entities / 2,860 links  (left exactly as they are)
     revised      40 entities  (new revision staged; old one retired)
     added        60 entities, 210 links
     removed      10 entities  (retired, not deleted), 70 links deleted (all F2W-owned)
     boundary links (owned by others, touching records we retire):  3
       forge  Sample "P3HT blend 7" --rel:madeOf--> rsoxs:matkg:P3HT   (node revised in v5)
       ...
     foreign records fingerprinted: 4,912     other F2W scopes: bl1101 (unchanged)
     unreconciled UI edits: 0
   ```
3. **Approve.** The admin approves this exact hash. Any change before apply (new edit, other writer, new schema)
   makes the plan stale, and a new review is needed (T10, B27).
4. **Stage.** Nothing visible changes yet.
5. **Verify staged.** Read back the staged set against the snapshot.
6. **Switch** (2.0).
7. **Verify final**, then the **receipt**, which includes the identity ledger, the boundary report and the I7 probes.
8. **Activate** F2W's pointer and retrieval index. Reload the editable session view (2.1.6).

**2.1.1 Links from other owners that point at N's entities.** F2W cannot move them: link endpoints are fixed
[Verified], and the links are not ours. Three options:

- **(a)** Report them in the plan, require the approval to cover them, and **keep the retired record alive** so the
  link still resolves to exactly the record it was made against. Readers that follow it see a record whose URI says
  `retired`, and can map it to the live node through the ledger history (2.1.3). Purge skips it. **Recommended now**,
  because it needs nothing from FORGE.
- **(b)** **Stable anchor entities.** One extra F2W entity per logical node that never changes (properties only
  `{scope, node_id}`). Revisions link to their anchor, and FORGE links to anchors instead of revisions. Foreign links
  then never land on retired records. This needs a FORGE convention, so it is a question for Alex and the Tiled team
  (4.5). **Recommended as the follow-up.**
- **(c)** F2W deletes and recreates foreign links. **Rejected:** writes data F2W does not own.

**2.1.2 What happens today, measured.** When the current importer revises a node in place, a FORGE link to the
old revision stays on the retired record, and the import still reports `applied` and verified. [Verified, probe
P9.] That is consistent with I1, because the FORGE link itself is unchanged, but nobody is told. The boundary report
(I5) closes that gap.

**2.1.3 Stable identities across versions (IdentityLedger).**
- The public identity is `(scope, node_id)`, for example `(rsoxs, matkg:P3HT)`. The `[KG: matkg:P3HT]` citation,
  bookmarks, `fair2wise.citeFocusNodeId` and session histories carry only the node id, which json2kg keeps stable
  across rebuilds as long as the term is unchanged.
- Each receipt already stores a ledger `(graph, node) -> Tiled UUID`. [Proposed] Keep a per-scope **ledger history**:
  every receipt's ledger is appended to it, so any Tiled UUID ever issued (live, retired or purged) maps back to its
  `(scope, node_id)` and the receipts that used it.
- Old citations resolve to the live record of the same node id. A node removed in N+1 returns the legacy 404, as
  today when a graph is rebuilt without it.
- A node whose id changed in a rebuild (label change) shows up as removed plus added. A curated alias map is out of
  scope; such cases are listed in the plan report.

**2.1.4 UI edits carried from N into N+1.**
- An acknowledged edit made against N must be inside N+1's lineage, or the plan is refused (`check_import`, exists).
- If N+1 is an `edit_reconciliation` child of N, the edits are already in it.
- If N+1 is a **rebuild** from new extraction, the edit journal is replayed onto the rebuilt graph before freezing
  (json_kg_contract.md 6.2.3). An edit whose node no longer exists becomes a recorded conflict that the admin
  resolves as `carried` or `discarded`.
- [Proposed] Lineage must be the full ancestor chain, not just the parent, as `load_source` uses today (task T23C).

**2.1.5 Crash part-way.**

| Crash point | State left | Recovery |
|---|---|---|
| During plan or approval | Nothing written | Re-plan |
| During stage | Some staged records (invisible) | Re-run the same approved plan. It reconciles from a complete scan, creates no duplicates and does not need re-approval (already in T08B, tested at every intermediate point). Or **abandon**: delete our own staged records, which has no effect on others (check I3 first). |
| During switch | Readers see a mix of N and N+1 for that scope | Resume completes the switch. F2W's own reads stay on N through the ledger-pinned reader (2.1.6) until activation. |
| After switch, before receipt | Tiled holds N+1, no receipt | Resume re-verifies and writes the receipt (`already_applied` path exists) |
| After receipt, before activate | F2W pointer still on N | Resume activates |

The mixed window during the switch is unavoidable on 0.2.18 (no transactions). For 100 changed records it is about
200 small writes. The lease keeps it to one writer.

**2.1.6 What the frozen UI sees.**
- Until activation: v4, exactly as before.
- After activation: the next `GET /graph` / `GET /graph/node` returns v5. The editable session view
  (`_sync_session_graph_from_splash` today) is refreshed on activation.
- A Save during stage or switch is refused with the existing 502 `Splash update failed: ...` detail ("the graph is
  being republished; try again").
- [Proposed] The **ledger-pinned reader**: during a switch, F2W reads resolve nodes through the active receipt's
  ledger (`entity(id)` still returns a retired record by UUID), so F2W never shows a half-switched graph.

**2.1.7 Undo.** Rollback is scenario (e).

### 2.2 Scenario (b): a single node or relationship edit from the UI Save (STOP-1, STOP-2, STOP-5)

**Who.**
- `:local`: everyone, no sign-in (D34).
- ALS: verified Tiled admins. Non-admins get a 403 (scenario g).

**Steps (recommended, STOP-1 option b).**

1. Authorize **before anything else** (`graph.edit`). A denied caller leaves no journal entry.
2. Build the **effective record**: the active release's snapshot JSON (hash-checked against the receipt) plus the
   scope's acknowledged journal entries. This is the same view every F2W reader gets: `/graph`, search, node detail,
   chat retrieval.
3. Validate exactly as the baseline does: endpoints exist, no self-loops, predicate normalization.
   - [Proposed, STOP-2] Relationship `remove` acts only on F2W edges of this scope. A foreign link is never shown in
     the editable view, so it cannot be targeted. A forged request naming one gets the existing 400.
4. Take the scope's short edit lease and check that the effective revision has not changed since step 2.
5. Append the edit to the journal as `acknowledged` and fsync. The edit id is derived from the request content, so
   a client retry is a no-op.
6. Return the readback `GraphNode` from the effective record. This is synchronous, as today.
7. **No Tiled write at Save time.** The edit reaches Tiled with the next reviewed publication: the admin runs
   "publish edits", which reconciles the journal into `v<N+1>` with `edit_reconciliation`, then runs scenario (a).
   Optionally on `:local`, publication is automatic when the plan is "safe" (Q11).

**Crash part-way.**
- Before the fsync: nothing is recorded and the client sees an error. Same as a failed Save today.
- After the fsync but before the response: the edit exists. A retry with the same content is recognized and
  answered with the same readback.
- No half-written Tiled state is possible, because Save does not write to Tiled (STOP-5 goes away).

**Undo.**
- Before publication: an admin appends a compensating edit, or attaches a `discarded` resolution.
- After publication: a new edit, or a rollback (e).

**What the frozen UI sees.** Save works as today and the readback shows the change. Other F2W sessions see it on
their next read. **FORGE and raw Tiled readers do not see it until the next publication.** That is a baseline change:
in the Splash setup, Splash showed the edit at once.

**Options considered for STOP-1.**
- (a) Write a new revision to Tiled on every Save and move all its links. That is N writes per Save with no
  transaction. Every Save on a node that FORGE links to also leaves FORGE's link on a retired record.
- (c) Wait for Tiled property updates. Not in 0.2.18, and unknown for 0.2.19.
- (b) is the only option that never leaves Tiled half-written. It keeps one writer path, and each change to shared
  data is reviewed once, with the boundary report.

### 2.3 Scenario (c): snippet link and unlink (STOP-3)

**Who:** as (b).

**Observed baseline [Source].** The frozen UI never sends `_action: "unlink"`. Every snippet it saves carries
`_action: "upsert"`, and a removed snippet is simply left out of the list (`GraphMockup.tsx:797-804`). The server
unlinks anything missing (`api.py`, ADR 1.1 step 4).

**Steps.**
- Same as (b). An upsert without an id, or with a `temp:` id, mints `matkg:snippet...` with the baseline rule and
  journals a `snippet_upsert` (new node plus `rel:has_code_snippet` edge).
- An omitted snippet journals a `snippet_unlink` that removes the edge and keeps the snippet node, as the baseline
  does.
- At publication these become create-node, create-link and retire-link operations.

**STOP-3 pick.** Keep the implicit unlink. Requiring an explicit unlink would make snippet removal impossible from
the frozen UI, which never sends one.

Honest limit: the frozen client sends no base revision, so a stale browser tab cannot be detected reliably. The
in-request revision check (b, step 4) only catches two Saves racing each other. Mitigation: every unlink is
journaled with its before-state, so an admin can revert it, and the snippet entity is never deleted. The residual
risk is the same as the baseline's.

**Crash, undo, UI:** as (b).

### 2.4 Scenario (d): publication after extraction, replacing the Splash wipe (STOP-4)

**Today [Source]:** after an approved extraction, `splash_reimport` wipes the whole Splash DB and re-imports it
(`kg_update.py:119`, `api.py:5160,5827`; T18C F2).

**Who.**
- Extraction itself: unchanged (`:local` everyone; ALS admins).
- Publication to Tiled: as (a).

**Steps.**
1. Extraction writes records and rebuilds the session `kg.json`, as today. The chat answer is not delayed.
2. A new snapshot `v<N+1>` is frozen with `relation = rebuild`, with the edit journal replayed (2.1.4).
3. A replacement plan (scenario a) is computed automatically. It is read-only.
4. Approval depends on the profile:
   - `:local`: if the plan is **safe** (no boundary links, no other scope touched, no unresolved edits), it is
     approved by the local operator under a deployment auto-approval policy and applied. Otherwise it waits for review.
   - ALS: it always waits for an admin to approve the plan through the jobs API or CLI. The frozen UI has no
     plan-review screen.
5. SSE: the legacy phases `splash_reimport_started` and `splash_reimport_result` are kept and report the plan
   status: "published to Tiled", "publication awaiting admin review", or the error text.

**Crash:** as (a). The JSON snapshot is never affected by an import failure.

**Undo:** rollback (e).

**What the frozen UI sees:** the same progress rows and a completed answer. The Tiled graph may lag the session
graph until publication. That is the timing change STOP-4 asks you to accept.

### 2.5 Scenario (e): rollback to an earlier version, including F4

**Who:** as (a). On ALS: admins.

**Steps.**
1. Choose the target version M (usually N, just replaced).
2. The plan is a replacement with M as the source. [Proposed] The importer **revives** retired revisions whose
   content equals M's records (URI and type back to final). Their Tiled ids return unchanged, so foreign links that
   pointed at them are live again. Only records with no retired copy (already purged) are re-created.
3. **F4 / B26.** An edit reconciled into a snapshot that M's lineage does not include would be lost by this rollback.
   The plan is refused unless each such edit has a reviewed `ConflictResolution`:
   - `discarded`: the edit leaves Tiled and stays in the newer JSON;
   - `carried`: the reviewer confirms M already contains it.
   Today such edits no longer protect anything once `reconciled` (T18C pinned this behaviour). Fixing it is task
   T23C.
4. Approve, then stage, switch, verify, receipt, activate. F2W's pointer history records the rollback (T10;
   B28: a second rollback steps back one more entry).

**Crash, UI:** as (a).

**Undo:** roll forward, which is the same operation.

Note: the code rollback (image) and the data rollback (this) stay independent (REL-08).

### 2.6 Scenario (f): removing a whole corpus

**Who:**
- `:local`: the local operator.
- ALS: an admin, plus an explicit `remove-corpus` confirmation naming the scope. [Proposed] The plan must be
  generated with the corpus name typed in, so a wrong scope cannot be approved by accident.

**Steps.**
1. Plan "retire everything in scope S". The report shows counts, boundary links and open edits. Open edits block
   unless they are resolved as `discarded`.
2. Approve, then switch: retire all records, retire the marker, and set the F2W pointer for S to "none".
3. Verify I1 to I8. Every other scope must be untouched.
4. Optional **purge** (separate plan and approval): for each retired record, read its links, skip it if any is
   foreign (I3), delete its owned links, re-read, delete the entity. Skipped records are listed in the receipt.

**Crash:** resumable. A crash during purge leaves fewer retired records; nothing live changes.

**Undo:** before purge, revive (as e). After purge, re-import from the committed JSON in Git, which is never
touched.

**What the frozen UI sees:** the corpus disappears from the editable scope. If it was the configured editable scope,
Settings reports `edit_capability: unavailable` and Save gives the existing 400/502 path.

### 2.7 Scenario (g): non-admin attempts (STOP-6)

| Who | Action | Result |
|---|---|---|
| ALS signed-in reader | Save in the frozen UI | **403** with `detail` ("Editing the literature graph requires a Tiled admin"). Denied before any journal entry or Tiled read-for-write. The edit controls stay visible because the frozen UI cannot hide them. |
| ALS reader | Publish, approve, rollback, purge via `/v1` or CLI | Denied (`graph.publish` / `curation.approve` are admin capabilities) |
| ALS expired or forged session | Any privileged action | Sign-in required. Never falls back to local mode (D30, D34) |
| ALS reader writing **directly to Tiled** with their own token | Outside F2W | Tiled decides. Without an access policy, any user can write graph records [Verified, T20B], so central ACLs are required (Q-T3). F2W treats such records as foreign and never deletes them. A record forging F2W markers with a wrong URI is refused as `CONFLICT` at plan time (exists). |
| `:local` anyone | Everything | Allowed (D34). The actor is recorded as `local-operator`; `executed_as` is `single-user-api-key` (B16). |

---

## 3. STOP items: options and the pick

| STOP | Issue | Options | Pick | Changes baseline? |
|---|---|---|---|---|
| **STOP-1** | Link re-pointing on every Save | (a) multi-write move with a recovery job; (b) journal plus effective-record overlay, published to Tiled with the next reviewed publication; (c) wait for Tiled property updates; (d) = (b) plus automatic publication on `:local` | **(b)**, with (d) as the `:local` option (Q11). Ask the Tiled team about (c) in parallel. | **Yes:** FORGE and raw Tiled readers see edits only after publication. F2W users see them at once. |
| **STOP-2** | Removing links F2W does not own | restrict to F2W edges of the scope (400 for others); or delete every matching link | **Restrict** | **Yes** (narrows). In practice foreign links never appear in the editable view. |
| **STOP-3** | Implicit unlink of omitted snippets | keep it, with the in-request revision check; or require an explicit unlink | **Keep.** The frozen UI never sends an explicit unlink [Source]. | No |
| **STOP-4** | Wipe and reimport after extraction | scoped replacement publication (scenario d) | **Scoped publication**: automatic when safe on `:local`, admin review on ALS | **Yes:** Tiled lags the answer; on ALS it waits for review |
| **STOP-5** | Partial failure | acknowledge only after the journal and the Tiled effect; or (with STOP-1 b) acknowledge after the durable journal write, because Save writes nothing to Tiled | **Acknowledge after the fsynced journal write.** The existing 502 for journal or validation failure. Publication failures surface in the job, never as a false Save success. | No (same 502 text) |
| **STOP-6** | ALS non-admins still see edit controls | 403 with `detail`; or a later additive UI capability read | **403** | **Yes:** a new status code on this route |

New, not in the original six:
- **Save during a publication switch** is refused with the existing 502 (Q8).
- **`GraphPayload.source_path`** becomes `tiled://<scope>` (already ADR 2.1).

---

## 4. What Tiled 0.2.18 forces, and what 0.2.19 might change

### 4.1 Verified by a run during this task

Existing suites, run in this worktree with
`env -i PATH=$S/venv-app/bin:/usr/bin:/bin HOME=$S/home-plan TMPDIR=$S/home-plan F2W_TILED_PYTHON=$S/venv-tiled/bin/python $S/venv-app/bin/python -m pytest <paths> -q -p no:cacheprovider`:

- `tests/tiled/test_tiled_capabilities_0218.py`, `tests/graph_import`,
  `tests/contracts/legacy_ui/closure/test_closure_tiled.py`: **76 passed**.
- These pin:
  - create-only properties;
  - `accessBlob` required on update;
  - no upsert and duplicate URIs allowed;
  - `deleteEntity` cascades;
  - access blobs, not access tags;
  - the tag-policy behaviour;
  - importer crash and replay without duplicates;
  - FORGE sentinels untouched;
  - acknowledged edits block an import;
  - F4 (reconciled edits no longer protect a rollback).

New probes for this plan (scratchpad `edit-plan-probe/`, not committed; task T20C commits them):
**10 passed** against disposable 0.2.18 servers (single-user, and multi-user with `TagBasedAccessPolicy`).

| # | Fact | Observation |
|---|---|---|
| P1 | `deleteEntity` deletes **foreign** links attached to the deleted entity, in both directions | FORGE incoming and outgoing links read back `null`; the FORGE entity itself survives |
| P2 | Under `TagBasedAccessPolicy`, a tag owner deleting their own tagged entity **also deletes an admin-created link the owner cannot even read** | Owner reads the link: `null`. After the owner's `deleteEntity`, admin reads the link: `null`. Directly `deleteLink` on that link as owner: `Not permitted`. |
| P3 | Changing an entity's URI (how F2W retires) keeps every link, including foreign ones | The foreign link still reads back with the same endpoints; `incomingLinks` on the retired entity lists it |
| P4 | Link endpoints cannot change | `updateLink` with `objectId`: `unknown field 'objectId'` |
| P5 | Query surface | `entities(entityType, limit, offset)` only (no URI or property filter: `Unknown argument 'uri'`); `links(subjectId, predicate, objectId, limit, offset)` works for finding incoming links; mutations are exactly `createEntity, createLink, updateEntity, updateLink, deleteEntity, deleteLink, upsertNamespace, deleteNamespace`: **no batch, transaction or conditional write** |
| P6 | **An access-blob change is silently ignored without an access policy** | `updateLink` with a new blob: the response and a re-read both show the old blob, while a predicate change in the same call applied |
| P7 | An entity's access blob cannot be read | `Cannot query field 'accessBlob' on type 'Entity'` |
| P8 | Two real snapshots of one corpus get two graph ids, and importing v2 leaves v1 live | `create_snapshot` twice gave `synthetic_v1`, `synthetic_v2`; after importing both: 14 + 14 owned live entities; the v2 plan had 0 retire operations |
| P9 | Revising a node in place leaves a FORGE link on the retired revision and still verifies | Import `applied`; the FORGE link's object URI is `f2w:kg/synthetic_v1/retired/...`, same UUID as before |
| ACL | Tag owner may change the URI of their own tagged entity; reader may not update it (`Not permitted`); admin may delete anything | as stated |

### 4.2 Read in source (0.2.18 package)

- Links reference entities with `ForeignKey("entities.id", ondelete="CASCADE")` on both ends
  (`tiled/graph/orm.py:138,145`). This is why P1 and P2 happen; it is the database, not the resolver.
- `deleteEntity` checks only the entity's effective access blob (`graph/schema.py:508-519`). `deleteLink` checks the
  link's blob (`:561-570`).
- `_modify_access_blob` returns the **current** blob when no policy is configured (`graph/schema.py`), which
  explains P6.
- The T18C closure test built "v2" by copying v1's manifest, so it kept graph id `synthetic_v1`. That is why it
  exercised in-place convergence; `create_snapshot` would not (P8).

### 4.3 Assumptions (not checked)

- Postgres-backed central servers cascade the same way (the FK is declared, so likely, but not run).
- The central servers' access-control configuration (Q-T3) and whether non-admins can write graph records there.
- Whether FORGE creates links into F2W literature records, and with which predicates.
- Write throughput and rate limits on central servers for a few thousand sequential mutations.
- Final 0.2.19 behaviour. Only 0.2.19rc2 source was read (T20A), and none of the above was re-run on it.

### 4.4 Forced by 0.2.18, and what 0.2.19 might change

| Constraint on 0.2.18 | Forces in this plan | If 0.2.19 changes it |
|---|---|---|
| Create-only properties | Revisions; journal-only Save (STOP-1 b) | With `UpdateEntityInput.properties` (C3 detects it), edits could update in place; unchanged records would keep their ids even when edited, and boundary links would stop being a problem for edits. Republish would still be the reviewed path. |
| No transactions or conditional writes | Stage-then-switch; leases; mixed window; resumable plans | A batch or compare-and-swap would make the switch atomic and remove the pinned reader |
| Cascade on `deleteEntity`, ignoring link ACL | Never delete live records; purge checks links first; residual race | A "restrict" delete or owner-aware cascade would make purge safe without the race |
| Blob updates ignored without policy; entity blob unreadable | Tags set once and never relied on | rc2 moves to `accessTags`; needs a re-check, plus a catalog migration (T20B section 3.9) |
| No URI or property filters; offset paging without a snapshot | Complete double scans per plan; `complete_listing = False` | Filters would make plans cheaper; a snapshot or revision would make completeness provable |
| No URI uniqueness or upsert | Duplicate detection at plan time (`CONFLICT`) | A unique index would harden this |

### 4.5 Draft questions for the Tiled team

1. Does final 0.2.19 add `properties` to `UpdateEntityInput` / `UpdateLinkInput`?
2. Is there, or will there be, a batch or transaction mutation, or conditional updates (revision or etag)?
3. `deleteEntity` deletes every attached link through a database cascade, including links the caller cannot read or
   write under `TagBasedAccessPolicy` (we reproduced this on 0.2.18). Is that intended? Could there be a "refuse if
   linked" option, or a cascade limited to links the caller may delete?
4. On 0.2.18, `updateEntity`/`updateLink` fail without `accessBlob` (`UnsetType`), and without a policy a new blob is
   silently ignored. Are both fixed in 0.2.19? Will an entity's access tags be readable?
5. Can `entities` filter by URI (or URI prefix) and by property, and can listings be read from a consistent
   snapshot?
6. Can a URI be made unique per server or per namespace, or is an upsert planned?
7. Is there an audit or change feed, so we can tell our writes from other writers' during verification?
8. Central access control (Q-T3): which tags should mark F2W literature? Is the F2W writer an admin or a scoped
   service principal? Can ordinary users create or delete graph records on `tiled-staging` and `tiled`?
9. (With Alex) Does FORGE link to F2W literature records? Would FORGE link to stable F2W "anchor" entities instead
   of revisions (2.1.1 b)?
10. Expected write rate and limits for a few thousand mutations per publication on the central servers.
11. Is `upsertNamespace` server-wide on central servers? We plan never to call it there. Who registers `rel:` /
    `f2w:`?
12. When will 0.2.19 final be on PyPI or an approved index (Q-T1/Q-T2), so O-T1 can end?

---

## 5. Implementation tasks this implies (proposed addendum entries; `plan_addenda.md` not edited)

Proposed as **A5 (owner request 2026-10-16): Tiled graph editing**. Gate: your answers to section 6. All tasks run
only against disposable servers until H5.

### T20C: Pin edit-relevant Tiled 0.2.18 facts
- **Depends on:** T20B. **Blocks:** T08C, T08D.
- **Scope:** commit probes P1 to P9 as `tests/tiled/test_tiled_edit_facts_0218.py`. Add the 4.2 source notes to
  `capabilities_0.2.18.md`. Add C3-style capability flags `delete_cascades_foreign_links` and
  `blob_update_effective`.
- **Acceptance:** each fact is one test. On a server where a fact changes, exactly that test fails.
- **Binary cases:** TIL-08.

### T08C: Corpus-scoped replacement import (stage, switch, retire, revive)
- **Depends on:** Q1 to Q6, T20C, T08B, T10. **Blocks:** T18D, T18E, T10B.
- **Scope:**
  1. Scope key = corpus. Graph id mapping for JSON unchanged. One-time adoption plan for existing `:local`
     per-version imports.
  2. Release marker.
  3. `f2w:Staged` / `f2w:Retired` types.
  4. Two-phase stage and switch.
  5. Revive-from-retired.
  6. Boundary-link report in the plan hash.
  7. I7 read probes.
  8. Ledger history per scope.
  9. Ledger-pinned reader for F2W reads during a switch.
  10. Abandon-plan.
- **Acceptance:**
  - v4 to v5 on a seeded server with FORGE links leaves every foreign record byte-identical.
  - Unchanged nodes keep their Tiled UUIDs.
  - Boundary links are listed and require approval.
  - A crash at every stage and switch point resumes without duplicates.
  - F2W readers never see a mixed graph.
  - Type-filtered listings never return staged or retired F2W records.
- **Binary cases:** TIL-09 to TIL-14.

### T08D: Guarded purge and corpus removal
- **Depends on:** T08C, Q3, Q13.
- **Scope:** purge plan with a separate approval; per-record link check (I3); owned links first; re-read before
  delete; corpus retire-all with typed scope confirmation; pointer to none.
- **Acceptance:**
  - A retired record with any foreign link is skipped and reported, never deleted.
  - Other scopes are untouched.
  - Committed JSON is untouched.
  - Re-import from Git restores the corpus.
- **Binary cases:** TIL-15, TIL-16.

### T23C: Edit reconciliation and rollback protection (F4/B26)
- **Depends on:** T23B, T08B. **Blocks:** T18D, T10B.
- **Scope:**
  - Full-lineage check.
  - `reconciled` edits protect imports whose lineage lacks their snapshot.
  - Journal replay onto rebuilds, with conflicts.
  - Content-derived edit ids for idempotent Save retries.
- **Acceptance:**
  - Rollback below a reconciliation snapshot is refused without a `ConflictResolution`.
  - A rebuild keeps every edit or records a conflict.
  - The T18C F4 pin flips.
- **Binary cases:** TIL-17, JSON-10 (extend).

### T18D: Legacy Save on the journal and effective-record overlay
- **Depends on:** T23C, T08C (scope and pointer), T25C, STOP answers.
- **Scope:**
  - `update_graph_node` with no Splash calls.
  - Authorization first; effective record from the active snapshot plus the journal.
  - STOP-2 restriction; STOP-3 implicit unlink with the revision check; STOP-6 403.
  - Save refused during a switch.
  - `/graph`, search and retrieval read the same overlay.
  - "Publish edits" action (journal to `v<N+1>` to a T08C plan).
- **Acceptance:**
  - UI04, UI15, UI16 and UI17 pass on Tiled in `tests/e2e/live_backend/full` (the expected failure flips).
  - Same id read back after Save.
  - Old citations resolve.
  - A crash after the fsync is answered on retry.
  - An ALS non-admin gets a 403 with no journal entry.
- **Binary cases:** TIL-05 (extend), TIL-18, TIL-19.

### T18E: Post-extraction publication replaces `splash_reimport` (STOP-4)
- **Depends on:** T18D, T08C, T10, Q11.
- **Scope:**
  - Remove the wipe path and `--allow-splash-wipe` from images.
  - Freeze `v<N+1>`, plan, then apply under the `:local` auto-approval policy only when the plan is safe; on ALS
    stop at review.
  - Keep the SSE phase names.
- **Acceptance:**
  - UI12 Tiled stage passes.
  - No delete outside the plan.
  - ALS never auto-approves.
  - An unsafe plan on `:local` waits for review.
- **Binary cases:** TIL-20.

### T10B: Rollback and replacement in the publication job
- **Depends on:** T08C, T23C.
- **Scope:**
  - Rollback = a replacement plan to the chosen version through revive.
  - Pointer history kept in step with the Tiled release marker.
  - Foreign-change quarantine, with an admin re-verify (Q14).
- **Acceptance:**
  - v5 to v4 to v5 restores the same Tiled UUIDs.
  - The release marker and pointer agree after every step.
  - A foreign change during a run yields no receipt and a clear quarantine reason.
- **Binary cases:** TIL-06 (extend), TIL-21.

### Proposed binary cases

| Case | Title |
|---|---|
| TIL-08 | Edit-relevant 0.2.18 facts (cascade, blob-update, filters, endpoints) are pinned by tests |
| TIL-09 | Version replacement leaves every foreign record (FORGE, other F2W scopes, other users) byte-identical |
| TIL-10 | Unchanged nodes keep their Tiled ids across a version replacement |
| TIL-11 | Every foreign link touching a record to be retired is in the approved plan; a changed list makes the approval stale |
| TIL-12 | Crash at every stage and switch point resumes to the same verified state without duplicates |
| TIL-13 | F2W readers never observe a mixed N/N+1 graph; type-filtered listings never return staged or retired F2W records |
| TIL-14 | The release marker in Tiled names exactly the version F2W's pointer reports |
| TIL-15 | Purge never deletes a record that has a foreign link, and never deletes a foreign link |
| TIL-16 | Removing corpus A leaves corpus B and FORGE records unchanged; re-import from Git restores A |
| TIL-17 | Rollback below an edit-reconciliation snapshot requires a reviewed resolution (F4/B26) |
| TIL-18 | Save is acknowledged only after a durable journal write; retry after a crash returns the same readback |
| TIL-19 | ALS non-admin Save is a 403 with no journal entry and no Tiled access by the writer |
| TIL-20 | Post-extraction publication performs no wipe; ALS never auto-approves |
| TIL-21 | Rollback and roll-forward revive the same Tiled records; old citations resolve at every step |

---

## 6. Decision table

"Baseline" marks a choice that changes what the frozen UI or other Tiled readers see compared with the Splash
baseline.

| # | Question | Options | Recommendation |
|---|---|---|---|
| Q1 | How is a KG version represented in Tiled? | (a) one graph id per version, as today; (b) one stable scope per corpus, version in a release marker | **(b).** Only (b) keeps Tiled ids, and FORGE links, stable for unchanged nodes |
| Q2 | Order of version replacement | (a) delete N, then import; (b) import (staged), switch, retire N; (c) keep both live with a pointer | **(b)**, keeping the previous version retired and revivable |
| Q3 | Physical removal (purge) of retired records | never; after review, skipping records with foreign links; immediately | **After review, as a separate admin-approved step, never for records with foreign links**. Default: keep one previous version |
| Q4 | Foreign links on records we retire | (a) report, approve, keep the retired record alive; (b) stable anchor entities (needs FORGE); (c) recreate them | **(a) now; propose (b)** to Alex and the Tiled team |
| Q5 | Release marker record in Tiled | yes / no | **Yes** |
| Q6 | Mixed window during the switch | accept; F2W pinned reads through the ledger; wait for Tiled transactions | **Pinned reads for F2W, accept the short window for others, ask Tiled** (4.5 Q2) |
| Q7 | STOP-1: how Save reaches Tiled (**baseline**) | (a) multi-write per Save; (b) journal plus overlay, published with the next reviewed publication; (c) wait for 0.2.19 | **(b)**; ask about (c) |
| Q8 | Save while a publication is switching | refuse with the existing 502; queue | **Refuse** |
| Q9 | STOP-2: removing foreign links (**baseline, narrows**) | restrict to F2W edges; delete all matches | **Restrict** |
| Q10 | STOP-3: implicit snippet unlink | keep, with revision check; require explicit unlink | **Keep** (the frozen UI never sends an explicit unlink) |
| Q11 | STOP-4: publication after extraction (**baseline, timing**) | `:local` automatic when the plan is safe, ALS admin review; always manual | **Automatic-when-safe on `:local`, review on ALS** |
| Q12 | STOP-5: when a Save is acknowledged | after the fsynced journal write; after journal plus Tiled effect | **After the journal write** (follows from Q7) |
| Q13 | STOP-6: ALS non-admin Save (**baseline, new 403**) | 403 with `detail`; later UI capability read | **403** |
| Q14 | Foreign record changed during a run (we cannot tell who did it) | fail closed and quarantine for admin re-verify; ignore | **Fail closed** |
| Q15 | Rollback below a reconciled edit (F4/B26) | require a reviewed resolution; allow | **Require a resolution** |
| Q16 | Second approver for `als-prod` replacement or purge | one verified admin (D30); two different admins | **One admin now**, with `requested_by`/`approved_by` recorded. Revisit before the first production purge. |

---

## Appendix A: probe reproduction

The probes live outside the repository for this task. T20C commits them.

```bash
S=<scratchpad>
cd $S/wt-tiled-edit-plan
env -i PATH=$S/venv-app/bin:/usr/bin:/bin HOME=$S/home-plan TMPDIR=$S/home-plan \
  F2W_TILED_PYTHON=$S/venv-tiled/bin/python \
  $S/venv-app/bin/python -m pytest $S/edit-plan-probe -q -p no:cacheprovider -s --rootdir=$S/edit-plan-probe
# 10 passed; observations printed as JSON (section 4.1)
```

- Only disposable loopback servers on ephemeral ports were used.
- No ALS host and none of the ports 8011, 8765, 8002, 8004 or 5057 was contacted.
- No network call was made.

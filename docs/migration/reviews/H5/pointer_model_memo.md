# Decision memo: one active publication pointer per Tiled target, or one per corpus?

Date: 2026-11-06. For: the project owner. Status: **proposal only, nothing here is approved or decided.** No code was
changed. Code citations are `file:line` on branch `forge_merge` (c1c016b).

## 1. The problem in plain words

F2W records which published version of the knowledge graph is "the live one" in a small table called the *active
publication pointer*. Today there is **one pointer for the whole Tiled instance** (the "target"), even though that
Tiled can hold **several corpora** (separate knowledge graphs such as `e2e_demo` and `bl1101`), and each corpus has its
own live version, release marker and edit journal in Tiled and on disk.

What happened in the E2E rehearsal (`owner_decisions_2026-11-03.md`, "E2E rehearsal record (2026-11-06)";
`H5/local_publication.md`, "E2E rollback rehearsal finding"):

1. `e2e_demo/v1` published: pointer v1 = e2e_demo/v1.
2. `bl1101/v4` published: pointer v2 = bl1101/v4. The pointer now names only bl1101, although e2e_demo/v1 is still live in Tiled.
3. "Roll back" meant "go to the previous pointer" = e2e_demo/v1. That is a different corpus, so the plan had zero operations (nothing of e2e_demo had been retired). It only moved the pointer to v3 = e2e_demo/v1 (bug fixed in d25a32f). bl1101/v4 stays live in Tiled, but the pointer no longer mentions it.

Consequences visible today, all from the pointer being per target:

- **"Rollback" does not mean what a user expects.** It steps back through *publications of any corpus*, not through versions of one corpus (`publish_store.py:305-318`, `publication.py:794-799`). Rolling back bl1101 v4 to bl1101 v3 is impossible while an e2e_demo publication sits between them in history.
- **Save follows the last pointer move.** `LocalSaveProvider.resolve` takes the corpus from `store.pointer(target).snapshot_ref` (`local_save.py:111-115`). After the rollback, Save edits `e2e_demo`, not the bl1101 that was published last.
- **Agreement check can report a false disagreement.** `verify_agreement(target, scope)` compares the target's pointer with one corpus's marker (`publication.py:510-517`). For bl1101 right now it reports pointer `v1` vs marker `v4`.
- **Job labels mislead.** A receipted job is "superseded" when `pointer.receipt_id` differs (`operations.py:332-345`), so bl1101/v4's job reads "superseded" although its records are live.

## 2. The models

| | **A. Per target (today)** | **B. Per corpus scope** | **C. Per target, plus a per-corpus "live version" view** |
|---|---|---|---|
| Idea | One pointer; one history | One pointer and history per (target, corpus) | Keep A's pointer as "last activated"; add a read-only per-corpus view; make rollback corpus-aware |
| Rollback means | Step back one entry in the target's history, whichever corpus (B28 toggle of the last two pointers, `api/README.md` 3.1). Across corpora it is a no-op that only moves the pointer | Step back within one corpus: v4 to v3 of bl1101. Needs the corpus named (or implied by the route) | Same as B for the plan (previous version of the named corpus, taken from the history rows' `snapshot_ref`); the target pointer then also moves to that corpus (it is "last activated") |
| What Save edits | Corpus of the last pointer move (`local_save.py:111-115`) | No single answer: needs a rule (the one in-scope corpus, or an explicit choice) because the frozen UI has no corpus picker | Today's rule (last activated), plus the option to restrict by `F2W_CORPUS_SCOPE` as now (`local_save.py:119`); an explicit choice can be added later |
| `GET /v1/publications/pointer?target=` | One pointer (`routes_publications.py:82-86`, `operations.py:674-679`) | Needs `corpus=`; `target=` alone must change meaning (list, or 400) | Unchanged shape (additive field `live: {corpus: snapshot_ref/version}`); old clients keep working |
| FORGE consumers reading Tiled | Unaffected: they read Tiled records and released markers, which the pointer does not touch | Unaffected | Unaffected |
| Frozen UI and new graph sources | Graph sources (`json:<corpus>/<vN>`, `tiled:<id>`, V80) are built from storage and Tiled, not the pointer, and are read-only (`api/README.md` V80). Only editable-mode Save and `can_save` depend on the pointer (`local_save.py:148-154`) | Same, plus Save needs the new rule | Same as A |
| Schema and API changes | None | See 3; new key for `pub_pointers`/`pub_pointer_history`, `activate`/`rollback`/`previous_record`/`clear_pointer`, V19/V20/V53 gain `corpus` | No new table (the corpus is already in each history record's `snapshot_ref`); new query methods plus optional `corpus` on V19/V20/V53 |
| Rough size | 0 (docs only) | ~400 lines, about 10 source files, ~80 test references touched | ~150 lines, about 5 source files, ~10 new tests |
| Migration of the running container | None | Rewrite 1 pointer row and 3 history rows (below) | None required; optional backfill is derived on read |
| Risk | Keeps the confusions in section 1 | Highest: touches the activation compare-and-swap, crash matrix, approvals bound to `after_pointer_version`, and Save | Low to medium: additive, but "pointer = last activated" keeps one surprise (rolling back corpus X makes X the Save target) |

## 3. What each model needs in the code (checked by reading)

**State tables** (`publish_store.py:25-43`): `pub_pointers(name PK, version, record)`, `pub_pointer_history(name, version,
record, action, reason, actor, at; PK(name, version))`, `pub_receipts(... UNIQUE(plan_sha256, target))`. `name` is the target
string everywhere. The record JSON already holds `snapshot_ref = "<corpus>/<vN>"` (`publish_store.py:277-282`), which is
why C needs no new table.

**Code that reads or moves the pointer** (grep of `app/`): `publish_store.py` (`pointer` 247, `activate` 254-303 with
CAS and `receipt.target != name` guard at 273, `previous_record` 305, `rollback` 320, `clear_pointer` 343, `pointer_history`
362); `publication.py` (activate stage 444-451, `verify_agreement` 510, corpus-retire clears the pointer 580,
`_submit_operation` binds `after_pointer_version` 625, `submit` binds it 674, `rollback` V20 776, `rollback_replacement`
794); `operations.py` (332, 677, 687); `routes_publications.py` (82-93); `policies.py:80` (V19); `local_save.py:111`;
`corpus_purge.py:538` (pointer_to_none hook). Leases are already per target (`publication.py:321,434,567`), which
serialises publications; that stays correct under A, B and C.

**Per-corpus facts that already exist** (so B and C reuse them): release marker and scope revision per corpus
(`replacement_step.py:344-351`), `ReleaseState` and `scope_dir(state, target, scope)` giving
`publication-state/<target>/<corpus>/release.json` (`corpus_scope.py:644-690`, `755-757`), per-corpus edit journal
(`local_save.py:69-71`), marker check per corpus (`publication.py:476-500`).

**B in addition:** change the pointer key to (target, corpus) or encode it in `name`; the guard at `publish_store.py:273`
must then accept the pair; every `pointer(target)` caller above gains a corpus; `rollback_publication` and
`rollback_publication_replacement` (`operations.py:682-695`) gain a corpus; `retire` clears one corpus pointer (cleaner
than today); job `ui_state` becomes correct per corpus; Save needs a corpus-selection rule.

**C in addition:** `PublishStore.live_by_corpus(target)` and `previous_record(name, corpus=None)` (filter history by the
corpus prefix of `snapshot_ref`, skipping `retire` rows); `rollback_replacement(..., corpus=None)` (`publication.py:794`;
`None` keeps today's behaviour); optional `corpus` on V53/V20/V19; `verify_agreement` compares against the corpus's own
live entry; `ui_state` compares with the corpus's live receipt.

## 4. Migration of the state in the running container

Today (from the rehearsal record): `pub_pointers` has **1 row**: `name = <target>`, version 3, record =
e2e_demo/v1 (thin noop receipt `receipt-fe1e4d0bc34c17088635`). `pub_pointer_history` has v1 (e2e_demo/v1, activate),
v2 (bl1101/v4, activate), v3 (e2e_demo/v1, rollback). Receipts (`pub_receipts`) are keyed by (plan hash, target), which
no model changes. Per-corpus `release.json` files and Tiled are untouched by any model (691 entities / 1063 links,
markers unchanged).

- **A:** nothing.
- **C:** nothing required; the per-corpus live view is computed from history plus the markers. The only judgement is "what
  is live for bl1101": history v2 (bl1101/v4) and its marker agree, so the view is correct without a rewrite.
- **B:** create two pointer rows: e2e_demo (v1 or v3 = e2e_demo/v1) and bl1101 (v2 = bl1101/v4), and split the three
  history rows by corpus. The existing single row must not simply be renamed, or bl1101 would have no pointer. Version
  numbers can stay as they are (non-contiguous is fine for the `version < current` lookups). Risks: a mistake would make
  `rollback` or Save point at the wrong snapshot; needs a backup of the operational database and a verification query
  comparing every pointer to its marker (`verify_agreement`). A rollback of the migration is restoring the backup.

## 5. Test impact

About 80 references to pointers in tests: `tests/jobs/test_replacement_publication.py` (29), `test_publication.py` (17),
`tests/api_wiring/test_local_save.py` (10), `test_crash_matrix.py`, `test_post_extraction_publication.py`,
`tests/api_v1/test_v1_local.py`, `test_v1_als.py`, `test_v1_publication_replacement.py`, `tests/api_wiring/test_local_publication.py`.
**A:** none (add a docs/regression test of the no-op rollback, which already exists). **C:** existing tests keep passing
if `corpus=None` preserves behaviour; add a two-corpus history test, per-corpus rollback, `verify_agreement` with two
corpora and the job-label case. **B:** most of those tests change; the crash matrix is re-proved per corpus.

## 6. What the plan and specs say today

- `api/README.md` 2.1: "`published` | Receipted and the active pointer of the target"; "`superseded` | Receipted, but no longer the active pointer (later publication or rollback)".
- `api/README.md` section 3 (routes): V19 "active pointer of one target"; V20 rollback "human only, audited"; V53 "rollback as a replacement plan to the previous pointer's version".
- `api/README.md` 3.1: "The publication pointer of a target keeps its history"; "rollback is a toggle between the **last two pointers**" (B28); "The pointer is per *target*, the release marker and the scope revision are per *corpus*"; such a rollback "**moves the pointer and writes nothing to Tiled**"; the owner question on approving a no-op rollback is still open there.
- `plan_addenda.md` T10B: "pointer history kept in step with the release marker (TIL-14)"; "v5 to v4 to v5 restores the same Tiled UUIDs"; `OPEN_DECISIONS.md` B28: "A second publication rollback undoes the first ... Decided 2026-10-30 (O-X3): accept."
- `plan.md` T08 (line 442): "a new graph/index pair becomes active only after validation. Queries bind to a snapshot/index revision pair." T13 (line 468): "Rehearse rollback to the previous active snapshot while retaining new candidate packages and their history." Neither says whether "active" is per target or per corpus.
- `JSON_KG_PUBLICATION_SPEC.md` section 5, items 4-6: completion is keyed "by snapshot, adapter version and target/scope"; activation of "the graph/index revision" after verification; "Rollback/reactivation preserves earlier artifacts and audit history, and never modifies FORGE-owned operational records." Its scope language is per corpus; it never mentions a per-target pointer.
- `H5/local_publication.md` (T18D): "Corpus rule (one target, possibly several corpora): the corpus named by the active publication pointer of that target".

So the receipts, markers, journals and scope-bound approvals are already per corpus in the written design; only the pointer
(and the B28 toggle built on it) is per target, and the texts above are consistent with either reading.

## 7. Recommendation (for the owner to accept or change)

**Choose C now, keep B as a later option.** Reasons:

1. The harm today is semantic (what "rollback" and "live" mean with two corpora), not data loss: the spec, markers and approvals are already per corpus. C fixes the meaning with an additive change.
2. C needs no schema change and no migration of the running container, so it cannot damage the one state the owner has rehearsed against; B must rewrite pointer rows, touches the CAS and crash matrix, and still needs a Save rule the UI cannot express.
3. C keeps V19 and the frozen UI behaviour unchanged for existing callers, and the H5 packet's evidence (v1 to v3 history) stays valid.
4. If the owner later wants a real per-corpus pointer, C's per-corpus view and the corpus parameter on V19/V20/V53 are most of the work of B.

Accept A only if the project will never hold more than one corpus per Tiled in production (then document the no-op rollback and stop).

## 8. Questions for the owner

1. Will a production Tiled hold several corpora at once, or one corpus per Tiled? (If one, A suffices.)
2. Should "rollback" mean "previous version of one named corpus" (C and B) rather than "previous publication on the target"? Should `POST /rollback-replacement` without a corpus be refused when more than one corpus is live, or keep today's behaviour?
3. Which corpus does the UI Save edit when several are live: the last activated (today), the only one in `F2W_CORPUS_SCOPE`, or one the user picks (needs a UI change; the UI is frozen)?
4. After a corpus-X rollback, should Save and `GET pointer?target=` move to X (C as proposed), or stay on the last published corpus?
5. Is a no-op rollback (it writes nothing to Tiled) still subject to the V18 approval, or may it fall under `:local` safe-plan auto-approval? (Open in `api/README.md` 3.1.)
6. Does the B28 "last two pointers" toggle apply per corpus (C and B) or per target?
7. Should the "superseded" job label mean "another version of the same corpus is live" (proposed), instead of "another corpus was published later"?

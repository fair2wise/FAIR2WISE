# T07B canonicalization apply adapters (H4)

Owner decision 2026-11-06, thirteenth set: "go on this: T07B: canonicalization apply adapters" (supersedes the ninth-set
"Apply adapter for accepted proposals: Not yet"). Read as: wire the adapters now, behind the existing approval gate.
The C4 paid real-model rerun remains the owner's own run; no agent calls a paid model, and this code calls none.

## What applies what

```
proposal (campaign Decision / online Resolution / reviewer verdict)
  -> case in the curation queue: unresolved_term, or graph_proposal with proposal_kind = canonical_merge
     (CurationProposalSink + merge_proposal_content); curation.approve accepts it (review_case, bound to the case revision)
  -> ACCEPTED case (state resolved, needs_validation = 1)
  -> apply_adapters.accepted_merges   one AcceptedMerge per term; two targets for one term -> both held
  -> apply_adapters.build_plan        migration.plan_migration -> ScopedMergePlan
                                      (source_revision = hash of the live candidate rows,
                                       vocabulary_revision = Vocabulary.revision)
  -> MergeApplyService.apply_plan     apply_migration | apply_cleanup | apply_campaign
                                      -> migration.apply_scoped_plan -> gates.authorize_apply
  -> EditPortMergeTarget              one node_patch per candidate node through the existing journaled Save port
                                      (TiledSaveBackend: journal first, then Tiled). No new writer.
```

A merge adds `canonical_slug`, `canonical_name`, `canonical_category`, `canonical_vocabulary_revision`,
`canonical_plan_id`, `canonical_method` to each node. Nothing is deleted, renamed or re-linked; the source mention stays.
After a real apply the cases of the applied terms are `mark_validated` (they go back through validation, never straight
to publication).

Code: `app/modules/canonicalization/apply_adapters.py`; service on `F2WOperations.apply_accepted_merges`; route V81
`POST /v1/curation/merges/apply` (`app/api/routes_review.py`); `:local` wiring in `install_local_publication`
(Save port of the active publication).

## Gate

* Every route calls `apply_scoped_plan`; the gate order is unchanged: gate open (route gate AND extraction
  `canonical_merge`), plan hash, `ApprovalRecord` for the exact hash/target/scope, source and vocabulary revisions,
  approved vocabulary, per-operation safety re-check. The three routes use gates `apply_migration`, `apply_cleanup`,
  `apply_merge`; the test is parametrized over all three.
* All gates are CLOSED in this build (they open only by the owner editing `gates.py`/`H3_GATES` after the C4 rerun). A real
  apply therefore answers the typed refusal (`501 unsupported_capability`, `reason = gate_closed`) before anything is
  recorded. This holds on `:local` too; there is no profile exception, so "no production vocabulary activation before H5"
  holds on every profile.
* No auto-approval. The existing `:local` `auto_safe_plan` policy (`app/api/post_extraction.py`) covers publication
  replacement plans only; it is not extended. The approval is made by a human through `create_approval` for the
  `plan_sha256` a dry run showed, and the call is write-ahead audited (`merge_apply`).
* `dry_run` is the default everywhere and never touches a target.
* Workers, delegates and models are refused (`DENIED`); the corpus must be named and inside the caller's scope.
* No vocabulary is wired on the image (`vocabulary=None`): the route answers `unsupported_capability` until the owner wires an
  approved vocabulary (H5). The adapter never activates or promotes a vocabulary.

## Revisions

The plan binds `source_revision` and `vocabulary_revision`. `apply_plan` re-reads both before the route runs and refuses
with `stale_source` / `stale_vocabulary` (`RefusedApply`, an `ApplyRefused`; HTTP 409 with `reason`); the route's own
`authorize_apply` checks them again. A candidate vocabulary is refused (`vocabulary_not_approved`).

## Report fields (`ApplyReport`, `dry_run`, `route`, `gate`, `gate_open`, ...)

`accepted_cases`, `not_merge_proposals`, `merges`, `held` (accepted but not mergeable, with reason), `planned`, `plan_held`
(planner held for safety/unknown target), `applied`, `refused`, `unresolved_visible` (held + plan-held + refused surfaces),
`candidates_considered` (nodes read), `model_calls` (always 0), `validated_cases`, `refusal`, and, separately:

* `false_merges` / `false_merge_items`: an accepted fold that is a named negative control (`NAMED_NEGATIVE_CONTROLS`) or
  fails `check_merge`; always refused, counted so a regression is visible.
* `false_splits` / `false_split_items`: an accepted proposal whose surface is an exact approved alias of the proposed
  target but was held, so the merge that should have happened did not.

Bounds: at most `MAX_ACCEPTED_CASES` (1000) cases read, `MAX_PLAN_OPS` (500) operations per plan, one corpus per plan.

## Acceptance coverage (offline; `tests/canonicalization/test_canon_apply_adapters.py`, `tests/api_v1/test_v1_merge_apply.py`)

approved alias end to end; WTe2 -> MoTe2 held when proposed via verdict or via proposal; unresolved terms visible after
apply; bounded reads and zero model calls (adjudication/resolver entry points patched to fail); stale source and
vocabulary typed; same gate on all three routes; false merges and false splits as separate counts; V81 capability row,
dry run, `gate_closed`, not-wired 501.

## Still to show before H4 (the owner's C4 paid real-model rerun)

The harness here proves the plumbing with fixtures. The C4 rerun must still show, on the real model and real corpus:
false merges = 0 on the named negative controls, false-split rate, prompt/candidate sizes within budget, and that the
accepted proposals the owner reviews produce plans whose dry-run reports look right. Only then does the owner open the
gates.

## Owner-gated items

1. Capability of V81: `graph.publish` chosen (matches the other apply/publish rows V16, V20, V53-V55); alternative
   `jobs.admin` (the earlier paper-job-kinds-only rule) or `curation.approve`. A real apply also needs `curation.approve`
   (via `create_approval`).
2. Opening the gates (`apply_migration`, `apply_cleanup`, `apply_merge`, extraction `canonical_merge`) after the C4 rerun.
3. Which vocabulary is wired and approved on the image (H5); until then the route is a typed 501.
4. The node fields a merge writes (`canonical_*`) and whether H5 publication should project them.
5. Who emits `canonical_merge` proposals into the queue (campaign/online outcomes use `merge_proposal_content`; no emitter
   is switched on by this change).

## Suggested T07B note text

"T07B: apply adapters implemented (accepted case -> scoped plan -> apply_migration/cleanup/campaign -> Save port),
route V81, tests green; all gates stay closed; waiting on the owner's C4 paid rerun and gate decision (H4), vocabulary
wiring (H5), and the V81 capability choice."

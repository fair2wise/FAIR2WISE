# H5 note: `:local` publication into the container's own Tiled

Date: 2026-11-04. Branch: `forge_merge-T-localpub`.

## Owner decision

Ninth set (`docs/migration/reviews/owner_decisions_2026-11-03.md`), Tiled step of the end-to-end test:
"I want both :local-only publish into container's own tiled, and import exported json through the existing
json-to-tiled import path".

## What changed

Before: no production code built a `Publication`, a `PublicationGate` with targets, a `SaveTarget` or a
`PostExtractionPublisher`; every publication route answered 501 (gated at H5) and the legacy post-extraction
phases reported `skipped`.

Now (`app/api/local_publication.py`, called only from `run_api` through `create_api_app(local_publication=True)`
and from the worker factory):

- On profile `:local` only, when the managed loopback Tiled the image runs resolves to the same normalized base URL
  as `DeploymentSettings.tiled_target`, that one target is the whole allowlist.
- The real publication job is registered in the API process and in the worker process (same job database, audit
  log, publication tables and importer state under the operations root), over `ReplacementPublishStep`.
- The post-extraction publisher is wired when `F2W_CORPUS_SCOPE` names exactly one corpus.

## What stays gated (unchanged)

- ALS profiles: the allowlist is empty and nothing is wired, whatever the environment holds.
- Central and ALS Tiled targets are never in the allowlist. A target on another host, another loopback host or
  another port is 403 `denied` (not this deployment's Tiled); an in-deployment target that is not listed stays 501.
- The allowlist cannot be reached from an environment variable, a setting or a request: the gate field is a
  constructor argument; the environment only names the port of the image's own Tiled and a mismatch with the
  settings wires nothing.
- The writer credential is read by `tiled_lifecycle.writer_credential`, lazily per request, as before.

## UI Save on `:local` (T18D), wired lazily (2026-11-06, branch `forge_merge-T-savetarget`)

Owner decision 2026-11-04 (tenth set): "Wire lazily after the first :local publication". `SaveTarget` needs an
active published snapshot and an edit journal per corpus, which exist only after the first publication, so nothing
is bound at startup. `app/api/local_save.py` (`LocalSaveProvider`, built by `build_local_publication` and attached
to the legacy service by `create_api_app(local_publication=True)` unless an explicit `tiled_save` is given)
resolves the target **per Save request** from the deployment's own publication state:

- Target: the one Tiled the `:local` gate allows (same trust condition as the publication).
- Corpus rule (one target, possibly several corpora): the corpus named by the **active publication pointer** of
  that target (`PublishStore.pointer(target).snapshot_ref = "<corpus>/<vN>"`, the last publication activated on the
  image). The session graph the UI edits in its editable (`splash`-labelled) mode is that corpus's effective record
  (active snapshot plus in-flight journal entries), so the node being edited is a node of that corpus; a node of
  another corpus is the legacy 404, and the UI's selectable graph sources stay read-only (`source` -> 400). With
  `F2W_CORPUS_SCOPE` set, the pointer's corpus must be in scope (defence in depth: the publication job already
  refuses out-of-scope corpora).
- Base snapshot: `<workdir>/storage_kg/<corpus>/<vN>/graph.json` with the pointer's recorded `snapshot_sha256`;
  the importer's own `release.json` of that corpus must name the same snapshot as live. `TiledSaveBackend`
  hash-checks the base on every read (a mismatch is its `CONFLICT`, the route's 502, never a 500); `StaleSave`
  (409) and every other legacy_save rule are unchanged.
- Journal: per corpus, `<ops_root>/publication-state/<target>/<corpus>/edits.jsonl`. The publication step now
  reads the same file (`ReplacementPublishStep(edit_journals=...)`), so a later publication of an edited corpus is
  refused with `CONFLICT` until the edit is reconciled or a reviewed resolution is attached (owner: the
  import-time conflict block is sufficient; no separate "unresolved edits" surface).
- Credential: the managed lifecycle's writer reader, called lazily per request; never cached, logged or in a repr.
- Before the first publication, or when a check fails: `PATCH /graph/node/{id}` answers the typed 501
  (`UNSUPPORTED_CAPABILITY`, wording "JSON available, Tiled unavailable: ..."), `GET /v1/capabilities` reports
  `can_save = false` (the frozen UI hides the Save controls) and no Splash call is made. After a publication the
  next Save resolves the new target without a restart; a later publication is resolved anew.

Operator note: the image starts the API with `--kg-mode json` (read-only; Save is the legacy 400 there). To edit,
switch Settings -> graph source to the editable mode; the session graph is then rebuilt from the active publication.

Tests: `tests/api_wiring/test_local_save.py` (memory double and a real disposable Tiled).

## E2E rollback rehearsal finding (T13, 2026-11-06): no-op rollback with several corpora on one target

With `e2e_demo/v1` (pointer v1) and then `bl1101/v4` (pointer v2) published into the same `:local` Tiled, the owner's
`POST /v1/publications/rollback-replacement` planned a replacement of scope `e2e_demo` back to v1 with zero
operations (nothing of `e2e_demo` had been retired; its marker still named v1). The job imported as `noop` but
quarantined at `verify`: the importer returned the *old* receipt (plan `0c4fd3fe…`) while the step looked receipts
up by the *new* rollback plan's hash. Tiled was not changed at all; the pointer stayed at v2 (`bl1101/v4`).

Fixed on `forge_merge-T-nooprollback`: the step writes a thin `noop` receipt under the rollback plan's hash that
rests on the covering replacement receipt, verification of it also requires that covering receipt to be valid for
the current scope revision, and the TIL-14 marker check accepts exactly the covering plan and receipt the marker
names. Semantics to keep in mind with one pointer per target and several corpora: **a rollback moves the pointer; it
never removes a corpus** (that is `corpus_retire`). Details and the owner question (approval policy for a no-op
rollback) are in `docs/migration/api/README.md` section 3.1.

Operator note: the `pub_quarantine` row of the failed job is informational (it drives `ui_state = quarantined` of
that job only) and does not block later submissions, approvals or activations on the target. Re-submitting the
rollback with the same reason returns the same failed job (idempotent job identity), and resuming the failed job does
not heal it (its completed `import` stage is reused, so `verify` fails again; checked with the fix in place): after the
fix is deployed, submit the rollback again with another reason. That new job plans the same zero-operation plan, waits
for approval, writes the thin receipt and activates `e2e_demo/v1` as pointer version 3 without touching Tiled.

## Pointer Model C (owner decision 2026-11-09)

The per-target pointer stays "last activated"; a per-corpus live-version view is derived from the pointer history
(`GET /v1/publications/pointer` gains `corpora`), `rollback-replacement` and `rollback` take an optional `corpus` (the
previous version of that corpus), `verify_agreement` and the job `ui_state` compare per corpus. Details, errors and the
owner-gated open questions (corpus-less rollback with several corpora live; which corpus Save edits) are in
`docs/migration/api/README.md` section 3.2. Tests: `tests/jobs/test_replacement_publication.py` (two corpora, two
versions each, rollback of A while B stays live, rollback of B) and `tests/api_v1/test_v1_corpus_pointer.py`.

## Zero-operation rollback auto-approval (owner decision 2026-11-09, `:local` only)

On `:local` a rollback-replacement plan that is a no-op (zero creates/links/supersessions, a covering receipt valid
now) is approved by the deployment policy inside the job's `review` stage, with the local operator's normal
plan-bound approval carrying `reason = "auto_safe_plan"` (same marker as T18E; a human approval has no reason). ALS
profiles and every non-empty plan keep the human `POST /v1/publications/{job_id}/approval`. Wiring:
`build_local_publication` passes `noop_rollback_auto_approver(settings, clock=now)`; the hook itself answers nothing off
`:local`. See `docs/migration/api/README.md` section 3.1. Tests: `tests/jobs/test_replacement_publication.py`
(auto-approved with the record checked; non-empty plan and non-local profile still need the human; no policy by default).

## Not done / follow-ups for the owner

- The vocabulary revision on `:local` is the fixed label `local-unversioned` (no separately versioned vocabulary
  is deployed on the image); the schema revision is a digest of the registered schema fingerprints.
- The index builder on `:local` is a verified pointer to the frozen JSON graph (the JSON graph is the retrieval
  source on the image), not a separate search index.
- Exported bundles reach Tiled by adoption (`f2w-kg-adopt adopt`) of the flat `matkg_<name>.json` into
  `<workdir>/storage_kg`, then a publication; there is no one-click route from the library export to a publication.

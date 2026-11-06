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

## Not done / follow-ups for the owner

- `SaveTarget` (UI Save as journaled Tiled writes) is not wired: it needs an active published snapshot and an edit
  journal per corpus, which only exist after the first publication. Wiring it is a separate decision.
- The vocabulary revision on `:local` is the fixed label `local-unversioned` (no separately versioned vocabulary
  is deployed on the image); the schema revision is a digest of the registered schema fingerprints.
- The index builder on `:local` is a verified pointer to the frozen JSON graph (the JSON graph is the retrieval
  source on the image), not a separate search index.
- Exported bundles reach Tiled by adoption (`f2w-kg-adopt adopt`) of the flat `matkg_<name>.json` into
  `<workdir>/storage_kg`, then a publication; there is no one-click route from the library export to a publication.

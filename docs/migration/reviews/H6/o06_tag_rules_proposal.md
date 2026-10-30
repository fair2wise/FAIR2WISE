# O06 image tag rules: APPROVED (T24D)

**Status: APPROVED 2026-10-30 (O-O6).** The owner decided every open point of the former proposal in
`docs/migration/reviews/H1/owner_decisions.md`, register walk-through batches 16, 17 and 18 (decisions D2.1 to
D2.10; the register rows are in `docs/migration/reviews/OPEN_DECISIONS.md` section D2). This file keeps its name
(it is referenced from `tests/ci/test_workflows.py`, the workflows and the deployment docs) and now records the
rules in force. `.github/workflows/publish-image.yml` and `.github/workflows/build-app.yml` implement them.

One safety switch remains: the repository variable `O06_APPROVED` must be exactly `true` or every publish run
fails in its `gate` job before anything is written. The rules are approved; setting the variable is the owner's
act of switching publication on, once the two GitHub environments exist (section 6). This is not a pending
decision.

## 0. The decisions (owner's words summarised; verbatim in owner_decisions.md)

| # | Rule | Decision (2026-10-30, O-O6) |
| --- | --- | --- |
| D2.1 | Which events publish | A PR merge (push) to `main` publishes the tested images and moves **all three** channels `:local`, `:als-dev`, `:als-prod` to the new digest. Manual dispatch is no longer the only way to publish; it remains for promotions and rollbacks. |
| D2.2 | Reviewers | The owner is the sole required reviewer of both publish environments and may approve their own runs. |
| D2.3 | Production precondition | Any tested digest may reach `:als-prod`. No dev-before-prod rule. |
| D2.4 | Registry | `ghcr.io/fair2wise/fair2wise`, **public** (the images carry the tracked KG JSON and the terminology snippets, accepted knowing D3.1). |
| D2.5 | Tag forms | `sha-<40-hex commit>-<variant>`; on a release tag also `v<MAJOR>.<MINOR>.<PATCH>-<variant>`. |
| D2.6 | Legacy `agent` image | No longer published. `compose.splash.yaml` may still build it locally. |
| D2.7 | Retention | Keep everything: no registry retention policy, no artifact expiry beyond GitHub's own 90-day maximum. |
| D2.8 | Platforms | `linux/amd64` only; multi-arch later under O08. |
| D2.9 | Base images | Stay tag-referenced in the Dockerfile; CI resolves them once per build and records the digests in the release manifest. |
| D2.10 | Binary report | The newest committed binary evaluation report is referenced in the manifest and the publication record when one exists. Information only; never gating (H3 B). |

## 1. What the workflows do

| Workflow | Triggers | What it does | Credentials |
| --- | --- | --- | --- |
| `build-app.yml` | push to `forge_merge` and `main`, PRs to `main`/`forge_merge`, dispatch, and `workflow_call` from `publish-image.yml` | Builds `local`, `als-dev` and `als-prod` from one commit (amd64, D2.8). Runs `tests/images` and the docker/profiles/README.md post-build checks on each image. Writes `release-manifest.json` with the resolved base-image digests (D2.9) and the newest binary report reference (D2.10). On non-PR events it saves the tested images as a workflow artifact. A standalone push to `main` runs only the legacy test and docs jobs: that commit's images are built once, inside the publish workflow's call. | none (`contents: read`; only the main-only `docs-deploy` job gets `contents: write`) |
| `publish-image.yml` | push to `main`, push of a `v*` tag, `workflow_dispatch` (promote/rollback) | On a push: calls `build-app.yml` (`publish_candidate=true`), re-verifies the saved images, pushes them under immutable tags, then moves `:local` and `:als-dev` (environment `ghcr-publish`) and `:als-prod` (environment `ghcr-als-prod`) to the new digests. On dispatch: moves one channel to an already published digest by a registry re-tag (forward or rollback). | the job's `GITHUB_TOKEN` with `packages: write`, inside a GitHub environment |

Neither workflow deploys to a host. Moving a tag is not a rollout and not a graph import. Ansible rolls out by
digest (T24E, approved at H7). Graph snapshots are released separately by commit, path and hash (D31, T26B).

## 2. Which tags exist

All tags live in one public repository, `ghcr.io/fair2wise/fair2wise` (D2.4). The workflows derive it by
lowercasing `github.repository_owner`, so the repository must live under the `fair2wise` owner for the name to
match (the `GITHUB_TOKEN` can write only to packages linked to this repository).

| Kind | Form | Mutable | Written by | Purpose |
| --- | --- | --- | --- | --- |
| Digest | `ghcr.io/fair2wise/fair2wise@sha256:<64 hex>` | never | the registry | What Ansible deploys and what a rollback names. |
| Commit (immutable) | `sha-<commit>-<variant>`, with the full 40-hex commit (D2.5), e.g. `sha-0123…cdef-als-dev` | never; a different digest under an existing tag is refused | `publish` | Proves a digest was published from a verified candidate. Promotion accepts only digests that carry one. |
| Release (semver) | `v<MAJOR>.<MINOR>.<PATCH>-<variant>` (optional pre-release suffix, e.g. `v2.1.0-rc.1-als-prod`) | never | `publish` on a protected `vX.Y.Z` tag push | Human release name. Same digest as the commit tag. |
| Channel (profile) | `:local`, `:als-dev`, `:als-prod` | yes, only by the channel-move steps and the dispatch jobs | `publish`, `publish-als-prod`, `promote`, `promote-als-prod` | The three required tags (D23). All three follow every merge to `main` (D2.1). |

No `:latest`, branch-name or metadata-action tags are written. The legacy compose `agent` target is not
published (D2.6).

All three variants of a release come from one commit and one base-image digest per base image. They are
different images: the profile is baked in, and `:local` adds the Tiled server. They share the application layers.
Only `linux/amd64` is built, tested and published (D2.8); `promote_channel.py` refuses multi-platform indexes.

## 3. Who may create them

* **Nobody pushes to the registry by hand.** Tags are written only by `publish-image.yml` jobs. The package's
  GHCR settings give write access to this repository's Actions only; maintainers get no personal push rights.
* **Who triggers a publication:** whoever merges a pull request into `main` (or pushes a `vX.Y.Z` tag). Branch
  protection on `main` decides who that is.
* **Who approves:** the owner, as the sole required reviewer of both environments, and self-approval is allowed
  (D2.2). This is GitHub environment configuration, not YAML:
  * `ghcr-publish` covers `publish` (immutable tags, `:local`, `:als-dev`) and dispatch `promote` for `:local`
    and `:als-dev`.
  * `ghcr-als-prod` covers `publish-als-prod` and dispatch `promote-als-prod` for `:als-prod`.
  * Both environments limit deployment refs to `main` and `v*` tags.
* **Release tags `vX.Y.Z`:** maintainers create them, under a tag ruleset. Only maintainers may create them, and
  nobody may move or delete them (mkdocs/docs/release.md: never move a published tag).
* **Protected refs:** the gate requires `github.ref_protected == true`. The ref must also be the default branch or
  a `vMAJOR.MINOR.PATCH` tag.

## 4. Flow: merge, build, publish, move the channels

1. **Pull request.** `build-app.yml` builds the three candidates and runs every check, with read-only
   permissions. Images of a `pull_request` run are never saved and never published.
2. **Merge to `main` (push).** `publish-image.yml` starts:
   * `gate`: `O06_APPROVED` is `true`; the ref is the protected default branch or a protected `vX.Y.Z` tag.
   * `candidate`: `build-app.yml` is called with `publish_candidate=true` and builds, checks and saves the three
     images of this exact commit as artifacts of this run. (The standalone `build-app.yml` run for the same push
     skips its image jobs, so each `main` commit is built once.)
   * `publish` (environment `ghcr-publish`): downloads this run's manifest and images; checks that the manifest
     is a passed candidate of this commit and the UI tree matches `FROZEN_UI_TREE`; checks the archive hash;
     after `docker load`, checks that every image id and rootfs layer list equals the manifest. It then pushes
     `sha-<commit>-<variant>` for the three variants (plus `vX.Y.Z-<variant>` on a tag), reads each digest back
     and checks the pushed config carries the tested layers and commit. **An existing `sha-*` tag that resolves
     to other bytes refuses the publication; it is never overwritten.** It records `release-publication.json`
     (digests, tags, the manifest and its binary report reference) and then moves `:local` and `:als-dev` to the
     new digests with `promote_channel.py`.
   * `publish-als-prod` (environment `ghcr-als-prod`): moves `:als-prod` to this run's `als-prod` digest. Same
     commit, same tested bytes; no rebuild, no dev-before-prod precondition (D2.1, D2.3).
3. **Dispatch** (`variant`, `digest`, optional `rollback` + `reason`): moves one channel to an already published
   digest of that variant, i.e. its `sha-<commit>-<variant>` tag resolves to it. Forward moves must descend from
   the channel's current commit; a rollback must go to an ancestor. `:local`/`:als-dev` run in `ghcr-publish`,
   `:als-prod` in `ghcr-als-prod`. Any tested digest is eligible for `:als-prod` (D2.3).
4. **Ansible** (T24E) deploys the digest from the publication or promotion record, not the channel name.

Every registry writer shares one concurrency group (`publish-image-ghcr`, `cancel-in-progress: false`), so two
moves never race on a tag. If several `main` pushes queue on it, GitHub keeps only the newest pending writer; the
superseded run can be re-run later (its artifacts stay for 90 days) and the ancestry check in
`promote_channel.py` still stops an older commit from overtaking a newer one.

## 5. Rollback

* **Image rollback:** dispatch with `rollback=true`, `reason=<why>`, `digest=<previous digest>`, `variant`. The
  previous digest is in the channel-move records of the last publication (`publication/channel-<variant>.json`,
  `promotion.json`: `previous.digest`) and in every publication record.
  * The target must still be a published digest of that variant.
  * Its commit must be strictly older (an ancestor) than the channel's current commit.
  * For `:als-prod`, the rollback goes through `ghcr-als-prod` approval.
* **Host rollback** is separate. Ansible redeploys the previous digest (T24E rehearsal). Moving the tag alone
  changes no running host.
* **Data rollback** is separate and has its own scoped plan (DEPLOYMENT_STRATEGY.md section 8; OPEN_DECISIONS
  B26/B28). Image tags are not a data-version identifier.
* Immutable and release tags are never deleted by the workflows, and nothing expires (D2.7): no GHCR retention
  policy is configured and every digest a record names stays available as a rollback target. Workflow artifacts
  (manifests, images, publication and promotion records) keep GitHub's 90-day maximum.

## 6. Switching publication on

* The `gate` job checks `vars.O06_APPROVED == 'true'` first and fails if the variable is unset or has any other
  value. Every writer job checks the variable again before it logs in to the registry.
  * Only a repository admin can create or change this variable. It is plain configuration, not a secret.
* Before setting it, the owner creates the environments `ghcr-publish` and `ghcr-als-prod` with themselves as the
  required reviewer (self-review allowed, D2.2) and the deployment-ref policy `main` + `v*`. GitHub creates a
  missing environment without protection on first use, so **create both before setting `O06_APPROVED`.**
* Also on GitHub: the package `fair2wise` linked to the repository, visibility public (D2.4), Actions write
  access only; branch protection on `main`; a tag ruleset for `v*`.

## 7. What CI records (D2.9, D2.10)

`release-manifest.json` (`scripts/ci/release_manifest.py build`) carries, besides the per-variant identity and
checks: `base_images` (tag to resolved `repo@sha256:…` reference) and `base_image_digests` (tag to digest) for
`python:3.12-slim` and `node:20-bookworm-slim`; a base image that was not resolved to a digest is a refusal.
`binary_report` references the most recently committed `docs/migration/reviews/**/binary_report*.json` (path,
sha256, kind, verdict, `gating: false`) or is `null`. The publication record copies that reference. No verdict is
ever read to decide a build, a publication or a channel move.

## 8. Only verifiable on GitHub

* That `github.ref_protected`, environment reviewers and the deployment-ref policies behave as configured.
* That GHCR accepts the job token for this owner and package, and that the package is public.
* That `gh run download` reads the current run's artifacts with `actions: read` from a later job.
* That `docker save`/`load` keeps image ids, and push keeps rootfs diff ids, under the runner's Docker image store.
* That `imagetools create` re-tags a single-platform manifest without changing its digest. The workflow verifies
  this and refuses otherwise.
* Runner disk and artifact size for three images.

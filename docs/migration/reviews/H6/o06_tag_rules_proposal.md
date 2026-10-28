# O06 image tag rules: PROPOSAL (T24D)

**Status: PROPOSAL, not approved.** O06 is still open (plan.md O06; `docs/migration/deployment/open_choices.md`).
Nothing below is in force. `.github/workflows/publish-image.yml` implements these rules, but it cannot publish
anything until the owner approves them: it fails closed until the repository variable `O06_APPROVED` is exactly
`true` (see "Why it cannot publish yet"). This task stops before H6.

The proposal follows option A of `open_choices.md`, with one change. Images are published only from the protected
default branch or a protected release tag, not from `forge_merge` pushes. The owner may widen this (decision 1).

## 1. What the workflows do

| Workflow | Triggers | What it does | Credentials |
| --- | --- | --- | --- |
| `build-app.yml` | push to `main`/`forge_merge`, `v*` tags, PRs to `main`/`forge_merge`, dispatch | Builds `local`, `als-dev` and `als-prod` from one commit. Runs `tests/images` and the docker/profiles/README.md post-build checks on each image. Writes `release-manifest.json`. On non-PR events it also saves the tested images as a workflow artifact. | none (`contents: read`; only the main-only `docs-deploy` job gets `contents: write`) |
| `publish-image.yml` | `workflow_dispatch` only | `publish`: pushes the saved, re-verified images under immutable tags. `promote`: moves a channel tag by a registry re-tag. | the job's `GITHUB_TOKEN` with `packages: write`, inside a GitHub environment |

Neither workflow deploys to a host. Moving a tag is not a rollout and not a graph import. Ansible rolls out by
digest (T24E, approved at H7). Graph snapshots are released separately by commit, path and hash (D31, T26B).

## 2. Which tags exist

All tags live in one repository, `ghcr.io/<owner>/fair2wise`. With the normalized owner this is
`ghcr.io/fair2wise/fair2wise` (CI_CD_CONTRACT.md). The workflows lowercase `github.repository_owner`.

| Kind | Form | Mutable | Written by | Purpose |
| --- | --- | --- | --- | --- |
| Digest | `ghcr.io/fair2wise/fair2wise@sha256:<64 hex>` | never | the registry | What Ansible deploys and what a rollback names. |
| Commit (immutable) | `sha-<commit>-<variant>`, with the full 40-hex commit, e.g. `sha-0123…cdef-als-dev` | never; a different digest under an existing tag is refused | `publish` | Proves a digest was published from a verified candidate. Promotion accepts only digests that carry one. |
| Release (semver) | `v<MAJOR>.<MINOR>.<PATCH>-<variant>` (optional pre-release suffix, e.g. `v2.1.0-rc.1-als-prod`) | never | `publish` dispatched on a protected `vX.Y.Z` tag | Human release name. Same digest as the commit tag. |
| Channel (profile) | `:local`, `:als-dev`, `:als-prod` | yes, only by `promote` | `promote` / `promote-als-prod` | The three required tags (D23). |

No `:latest`, branch-name or metadata-action tags are written. The old workflow wrote these for the `agent`
target; the legacy `agent` compose target is no longer published (decision 6).

All three variants of a release come from one commit and one base-image digest. They are different images: the
profile is baked in, and `:local` adds the Tiled server. They share the application layers.

## 3. Who may create them

* **Nobody pushes to the registry by hand.** Tags are written only by `publish-image.yml` jobs. Proposed: the
  package's GHCR settings give write access to this repository's Actions only. Maintainers get no personal push
  rights.
* **Who can dispatch:** anyone with write access to the repository. Dispatching does not publish anything by
  itself.
* **Who approves:** the required reviewers of the job's environment.
  * `ghcr-publish` covers `publish`, and `promote` for `:local` and `:als-dev`.
  * `ghcr-als-prod` covers `:als-prod`. It has its own reviewer list and should include the owner.
  * Both environments limit deployment refs to the default branch and `v*` tags. Self-review is off.
* **Release tags `vX.Y.Z`:** maintainers create them, under a tag ruleset. Only maintainers may create them, and
  nobody may move or delete them (mkdocs/docs/release.md: never move a published tag).
* **Protected refs:** the gate requires `github.ref_protected == true`. The ref must also be the default branch or
  a `vMAJOR.MINOR.PATCH` tag.

## 4. Flow: build, publish, promote

1. **Build.** A push to `main` (or a `v*` tag) runs `build-app.yml`. It produces the three candidates, their checks
   and `release-manifest.json`. Pull requests get the same build and checks, but their images are never saved, and
   `publish` refuses any run whose event is `pull_request`.
2. **Publish** (dispatch `operation=publish`, `build_run_id=<run>`, on the same ref, environment `ghcr-publish`).
   The job checks:
   * the run is `build-app.yml`, concluded `success`, came from a `push` or `workflow_dispatch` event in this
     repository, and its `head_sha` and `head_branch` equal the dispatched ref;
   * the manifest is a passed candidate of that commit, and the UI tree matches `FROZEN_UI_TREE`;
   * the image archive hash is correct;
   * after `docker load`, every image id and rootfs layer list equals the manifest.

   It then pushes `sha-<commit>-<variant>` for the three variants, plus `vX.Y.Z-<variant>` on a tag. It reads each
   digest back and checks the pushed config carries the tested layers and commit. It records
   `release-publication.json` (digests, tags, the manifest and, optionally, a binary report). **No channel moves
   here.**
3. **Promote to dev** (dispatch `operation=promote`, `variant=als-dev` or `local`, `digest=sha256:…`, environment
   `ghcr-publish`). The target must be a published digest of that variant, i.e. its `sha-<commit>-<variant>` tag
   resolves to it. Its commit must descend from the channel's current commit. The job runs
   `docker buildx imagetools create --tag …:als-dev …@sha256:…` and checks that the channel resolves to exactly that
   digest.
4. **Promote dev to prod** (dispatch `operation=promote`, `variant=als-prod`, environment `ghcr-als-prod`).
   * **Re-tag, never rebuild.** `:als-prod` moves to the `als-prod` digest built and tested in the same run, from
     the same commit, as the `als-dev` digest now on `:als-dev`.
   * The job refuses unless `:als-dev` currently carries that same commit.
   * The prod image is not byte-identical to the dev image (its baked profile points at production Tiled). It is the
     sibling digest from the same tested build. No bytes are built after the tests ran.
5. **Ansible** (T24E) deploys the digest from the publication or promotion record, not the channel name.

One concurrency group (`publish-image-ghcr`, `cancel-in-progress: false`) serializes every registry write, so two
promotions never race on a tag. If several runs wait, GitHub keeps only the newest pending one, and the ancestry
check stops an older commit from overtaking a newer one.

## 5. Rollback

* **Image rollback:** dispatch `operation=promote`, `rollback=true`, `reason=<why>`, `digest=<previous digest>`.
  The previous digest is in the last promotion record (`previous.digest`) and in every publication record.
  * The target must still be a published digest of that variant.
  * Its commit must be strictly older (an ancestor) than the channel's current commit.
  * For `:als-prod`, the rollback goes through `ghcr-als-prod` approval. It does not require `:als-dev` to match.
* **Host rollback** is separate. Ansible redeploys the previous digest (T24E rehearsal). Moving the tag alone
  changes no running host.
* **Data rollback** is separate and has its own scoped plan (DEPLOYMENT_STRATEGY.md section 8; OPEN_DECISIONS
  B26/B28). Image tags are not a data-version identifier.
* Immutable and release tags are never deleted by the workflows. GHCR retention for old `sha-*` tags is an owner
  decision (decision 7). Never delete a digest that a promotion record still names as a rollback target.

## 6. Why it cannot publish yet

* `publish-image.yml` has no push, tag, schedule, `workflow_run` or `pull_request*` trigger. It runs only on
  `workflow_dispatch`.
* The `gate` job checks `vars.O06_APPROVED == 'true'` first and fails if the variable is unset or has any other
  value. Every writer job `needs: gate` and checks the variable again.
  * Only a repository admin can create or change this variable. Setting it is the owner's recorded act of approving
    these rules.
  * Repository variables are plain configuration, not secrets. Nothing secret is stored in them.
* Even with the variable set, the environments `ghcr-publish` and `ghcr-als-prod` must exist with required
  reviewers. GitHub creates a missing environment without protection on first use, so **create both with
  reviewers before setting `O06_APPROVED`.**

The fail-closed variable was chosen over leaving the workflow file absent. The rules stay reviewable and
statically tested (`tests/ci/test_workflows.py`), and approval becomes one auditable setting.

## 7. Decisions the owner must make

1. **Events per channel.** Is the default-branch/`v*` only rule right? Or should `:local` and `:als-dev` follow
   trusted `forge_merge` pushes (option A as written)? Should a `v*` tag publish and promote automatically, or stay
   manual as proposed?
2. **Reviewers** for `ghcr-publish` and `ghcr-als-prod`, and whether prevent-self-review is required.
3. **Prod precondition.** Must `:als-dev` carry the same commit at the moment of prod promotion (proposed), or is
   "was on `:als-dev` for at least N days" wanted? The second would need a promotion log.
4. **Registry name.** `ghcr.io/fair2wise/fair2wise` requires the repository to live under the `fair2wise`
   owner, because `GITHUB_TOKEN` can write only to packages linked to this repository. Also: package visibility,
   and linking the package to the repository.
5. **Tag forms.** Are `sha-<commit>-<variant>` and `v<MAJOR>.<MINOR>.<PATCH>-<variant>` acceptable? Is a full
   commit in the tag acceptable, or is a 12-hex abbreviation preferred?
6. **Legacy `agent` image.** The old workflow published the compose `agent` target on every `main` push. The
   proposal stops that. Confirm nobody pulls it.
7. **Retention** of `sha-*` tags and of the workflow artifacts. Images are kept 30 days and manifests 90 days, so
   a build older than 30 days must be rebuilt (a new candidate) before it can be published.
8. **Platforms (with O08).** Only `linux/amd64` is built and tested. Promotion refuses multi-platform indexes until
   O08 settles arm64.
9. **Base-image pinning.** CI resolves `python:3.12-slim` to a digest once per build and records it. It is not
   pinned in the Dockerfile. Should releases pin a reviewed digest instead?
10. **Binary report in the publication record.** It is optional and information only (H3 B): a verdict never gates
    a build or a publication. Should the publish run require *some* report reference?

## 8. Only verifiable on GitHub

* That `github.ref_protected`, environment reviewers and the deployment-ref policies behave as configured.
* That GHCR accepts the job token for this owner and package.
* That `gh run download` reads another run's artifact with `actions: read`.
* That `docker save`/`load` keeps image ids, and push keeps rootfs diff ids, under the runner's Docker image store.
* That `imagetools create` re-tags a single-platform manifest without changing its digest. The workflow verifies
  this and refuses otherwise.
* Runner disk and artifact size for three images.

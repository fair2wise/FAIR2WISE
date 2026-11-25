# GitHub push readiness audit (demo workstream W3)

Audited 2026-11-10 against `forge_merge` at `a4054b0` (886 commits, 1829 tracked files). Read-only: nothing was
pushed, no remote was added or changed, no network was used, `gh` is not installed. All commands ran in a worktree.
Secret values are never printed; shapes are masked to 4 characters. Not legal advice.

## Verdict: READY WITH FIXES

The branch can be pushed: nothing exceeds GitHub's 100 MB limit, no credential shape other than one item below
was found, and the workflows parse. The first push should wait for the owner to deal with these, in this order:

1. **Rotate the Materials Project API key hardcoded in `app/modules/legacy/extract_terms_linkml.py` (line 312, still
   at HEAD as a fallback when `MP_API_KEY` is unset).** It is a 32-character value assigned to `mp_api_key`; it is
   also in history in 3 commits (`cd68a35` 2025-06-27, `c53e526` 2025-11-24, `97b55b3` 2026-06-04, the last
   removing it from `extract_terms.py`). 33 local branches contain `c53e526`. Whether it is live was not tested
   (no network). Treat it as live. Remediation, none performed: (a) rotate the key at the Materials Project
   dashboard first; (b) replace the fallback with "no key, skip validation" in a normal commit; (c) a history
   rewrite (`git filter-repo --replace-text`) is optional once rotated, and only matters if the key must not be
   readable in old commits; the old repos on GitHub (origin fork and `fair2wise/FAIR2WISE`, both public per the H0
   audit) may already hold it, so rotation is the real fix.
2. **Decide the publisher-PDF history question (H0 E1) for the target repo.** `forge_merge` history holds 90 PDF
   blobs under `papers/` (206 MB, 92 PDF paths ever added across `papers/`, `polymer_papers*/`, `xray_papers/`),
   untracked at the tip by `f0c6ce3`. The H0 audit recorded both GitHub repos as public with these PDFs already
   served; pushing `forge_merge` to either adds no new PDF blobs only if those repos still carry them (not
   checked: no network). Pushing to a **new** repository would newly publish them. Options: push to the existing
   repo (no new exposure), or purge history before pushing to a new repo.
3. **Pick the target repository and know what it implies for the image name** (section 5): the workflows publish
   to `ghcr.io/<repository owner, lowercased>/fair2wise`, so the `matesuu/FAIRtoWISE-FORUM-AI` fork publishes
   `ghcr.io/matesuu/fair2wise`, not the approved `ghcr.io/fair2wise/fair2wise` (D2.4).
4. **The CI image is linux/amd64 only (D2.8).** The Apple-silicon demo machine would run it under emulation. No CI
   change was made (section 5 gives the options).

Non-blocking but the owner should know: personal and ALS staff emails in old KG versions, `*.lbl.gov` hostnames,
NERSC `pscratch` paths, a Google Maps key shape in an ALS web-page fixture (section 3).

## 1. What exists

### Remotes and branches

| Item | State |
|---|---|
| `git remote -v` | `origin` = `https://github.com/matesuu/FAIRtoWISE-FORUM-AI.git`; `fair2wise` and `upstream` = `https://github.com/fair2wise/FAIR2WISE.git` |
| Remote-tracking branches | none fetched (`git branch -r` empty); no branch has an upstream configured |
| Local branches | 33 (`forge_merge`, 30+ `forge_merge-T*`/`H*`/`A*` task branches, helper branches) |
| Tags | one local tag `v0.0.1` on `bd49e5f` (old commit). It matches the publish tag regex, so never run `git push --tags` |
| Does `forge_merge` exist on origin | unknown without network: run `git ls-remote --heads origin` |

### Workflows (`.github/workflows/`)

All three parse with `python yaml.safe_load` (build-app 4 jobs, forge-merge-ci 10 jobs, publish-image 6 jobs).
`actionlint` is not installed and `gh` is not installed, so semantic linting was not run. `tests/ci/test_workflows.py`
exists and runs in `image-static-tests`. Every third-party action is pinned to a commit SHA.

| File | Triggers | What it does | Secrets / variables / environments |
|---|---|---|---|
| `forge-merge-ci.yml` | push and PR on `forge_merge`; dispatch; nightly cron `17 6 * * *` (fires only once the file is on the default branch) | push path (target under 5 min, cancelled by a newer push, path-filtered): `guard` (no `.env`, frozen UI tree `56c4bda1...`, changed paths), contracts, unit suite in 3 shards vs known-failure set, quality gate (ruff, isort, mypy --strict), import closure, frontend build (2 base paths), MkDocs strict. Dispatch, nightly or `[ci full]` only: Tiled-backed suites, Playwright mocked, `python-nightly-full` (Docling weights, coverage), `python-slow-suite`. See `mkdocs/docs/ci.md` | none; `permissions: contents: read` |
| `build-app.yml` | push to `main`, PR to `main`, dispatch (use it for `forge_merge`), `workflow_call`; no longer on `forge_merge` pushes (CI split, `mkdocs/docs/ci.md`) | `image-static-tests` (tests/images, tests/ci) then `build-candidates`: builds `local`, `als-dev`, `als-prod` (amd64) from one commit, runs post-build checks with `--network none`, writes `release-manifest.json`, uploads artifacts `release-manifest` and (non-PR) `release-images` (`docker save`, 90 days). The legacy `test` job and `docs-deploy` (the only write job, `contents: write`) run only for main | none; `contents: read`; no registry login |
| `publish-image.yml` | push to **main**, push of a `v*` **tag**, dispatch (promote/rollback) | `gate` (needs variable `O06_APPROVED == "true"`, a **protected** ref, default branch or `vMAJOR.MINOR.PATCH[-suffix]` tag) -> `candidate` (calls build-app) -> `publish` (environment `ghcr-publish`) -> `publish-als-prod` (environment `ghcr-als-prod`) | variable `O06_APPROVED`; environments `ghcr-publish`, `ghcr-als-prod`; `GITHUB_TOKEN` with `packages: write` for ghcr login; no repository secret is referenced |

Key consequence: **a push to `forge_merge` runs lint, types and unit tests only; candidate images are built by dispatching `build-app.yml`; nothing is published to ghcr from `forge_merge`.**
The images only exist as workflow artifacts (`release-images`, a gzip `docker save` tar of the three variants,
90 days). Publishing needs a push of the default branch (`main`) or a `v*` tag, with the gate satisfied.

Pins and fetches in CI:

- pixi 0.66.0 with a sha256 check in `scripts/ci/install_pixi.sh` (x86_64 Linux only, matches the Dockerfile's
  `pixi-app` stage and the version that wrote `pixi.lock`); `pixi install --locked`.
- Tiled 0.2.18 wheel hash asserted against `pixi.lock` (O-T1).
- Docling weights: `scripts/fetch_docling_models.py` pulls two Hugging Face repos at pinned commits
  (`docling-layout-heron` `8f39ad3c`, `docling-models` `fc0f2d45`) via `huggingface_hub`, plus RapidOCR checkpoints
  resolved from the locked `rapidocr` registry and sha256-verified. The word `modelscope.cn` appears only in the
  owner decision record (`reviews/owner_decisions_2026-11-03.md` line 78, "allow it for both local and CI"), not in
  any workflow or script: the host is chosen by the rapidocr release, so it could not be confirmed offline.
  This fetch runs in the `python-baseline-suite` and `python-quality` jobs and in the Dockerfile `runtime-base`
  stage (so in every image build). The Dockerfile also downloads `sentence-transformers/paraphrase-MiniLM-L6-v2`
  from Hugging Face. GitHub runners are outside China; reachability and speed of modelscope.cn from them is the
  main unknown, a timeout or sha mismatch fails the job and the image build.
- Nightly slow job: `python-slow-suite`, no secrets, 120 min timeout, only after the push exists.

## 2. What a push would publish

| Measure | Value |
|---|---|
| `git count-objects -vH` | 4 packs, 239.82 MiB packed, 2.14 MiB loose; 13,557 packed objects |
| `forge_merge` reachable blobs | 5,904, 947 MB uncompressed |
| Blobs over 100 MB (hard limit) in forge_merge history | **0** (all refs: 0) |
| Blobs over 50 MB (warning) in history | **2**: `storage/terminology/extracted_terms_rsoxs_v1.json` (73.5 MB) and `..._v1_repaired.json` (52.8 MB). At the tip they are 70.1 MB and 50.4 MB. Expect two "large file" warnings, not a failure |
| Tracked at tip over 50 MB | the same two files |

Top 10 blobs in all refs (MB): 70.1 `extracted_terms_rsoxs_v1.json`, 50.4 `..._v1_repaired.json`, 41.9 `matkg_rsoxs_v4.json`,
27.7 `matkg_rsoxs_v3.json`, 23.4 `splash_links/links.sqlite`, 23.0 `extracted_terms_rsoxs_v1_20260917T044424Z.json`,
20.0 `matkg_rsoxs_v2.json`, 13.6 `matkg_with_code.json`, 13.1 `extracted_terms_aug21_580papers.json`, 13.0
`matkg_qwen3_235b_580papers.json`. Largest PDF: 12.9 MB (`papers/10.1002adma.202105707.pdf`). Full top 25 comes from
`git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectsize) %(rest)'`.

Tracked assets at the tip: `storage/kg` 78 files, 265 MB; `splash_links/links.sqlite` 23.4 MB; `storage/tiled_catalog.db`
0.8 MB (excluded from images by `.dockerignore`, E3); PDFs 0 (only `papers/rsoxs/beamline_blueprint/blueprint_*.html`
is allowed through the ignore); model weights 0; `node_modules` 0; `ui/dist` 0 (ignored). History also contains
`.sqlite`/`.db` files `splash_links/links.sqlite` and `storage/tiled_catalog.db`, no node_modules, no `ui/dist`, no `AGENT/`.

Git LFS: not installed here (`git lfs` unknown), not configured (`.gitattributes` has no `lfs` filter) and not needed
at these sizes. The 2025-06-27 commit `cd68a35` message mentions removing LFS artifacts; no pointer files were checked.
`.gitattributes` already pins `storage/kg/**`, `storage/terminology/*.json` and release dirs to `-text` (byte-exact
hashes, O-R1).

Licence posture:

- `LICENSE.txt` is the LBNL BSD-style "License Agreement" (Regents of the University of California, copyright 2025),
  copied into the image. `splash_links/LICENSE` is separate.
- O-D1 (2026-10-12, `docs/adr/content-disclosure.md`): tracked `storage/kg` JSON keeps verbatim snippets in Git and
  in the images. D2.4 accepts a **public** image on that basis. Papers are excluded from the tree (`papers/**` ignored)
  and from the image (`.dockerignore`, `**/*.pdf`), but not from history (section 2 item above).
- Dependency audit: `docs/migration/reviews/H0/license_dependency_audit.md`. Open items there include E2, PyMuPDF
  is AGPL-3.0 (served over a network; owner decision pending/"keep for now"), relevant to a public image.
- No root `NOTICE` or `THIRD_PARTY_NOTICES` file exists; the only third-party notice is the FORGE BSD-3 paragraph in
  `docs/migration/README.md` and `ui/ATTRIBUTIONS.md` (shadcn/ui MIT, Unsplash).

## 3. Secrets and private data scan (all 886 commits, every blob reachable from `forge_merge`, text files)

Method: every reachable blob (PDF, images, sqlite skipped) was read through `git cat-file --batch` and scanned with
regexes in a helper script (scratchpad, not committed). Counts are matches, not unique values.

| Check | Result |
|---|---|
| `sk-...` shapes | 1 match, `tests/test_model_policy.py` (a `monkeypatch.setenv` fake) |
| CBORG key assignments | 101 matches in 6 files (`README.md` 66, `.env.example` 10, test files, `mkdocs/docs/deployment.md`); every one is a placeholder (`your...`, `repl...`, `test...`, `from...`) |
| `Bearer ...` | 4 matches, all in `tests/evals_binary/label_fakes.py`, a fake leak-detection string |
| AWS `AKIA`, GitHub `ghp_`/`github_pat_`, `xox*`, `hf_`, private key blocks | **0** each |
| Google API key shape `AIza...` | 1 match in `tests/graph/fixtures/bl1101/als_11-0-1-2.html` (a saved ALS web page; a Maps embed key, third-party, not ours) |
| Generic `api_key/token = "<20+ chars>"` | 14 matches in 3 files: `mp_api_key` (**real-looking Materials Project key, see verdict item 1**, `extract_terms.py` and `legacy/extract_terms_linkml.py`) and fakes in `tests/snapshots/test_release_verification.py` |
| `.env`-like files ever added | only `.env.example` and `scripts/.env.example` (placeholders) plus unrelated `splash_links/alembic/env.py`; no `.env`, `.pem`, `.jwk`, `priv_key`, `api_keys.yml`, `users.yml` |
| `.sqlite`/`.db` ever added | `splash_links/links.sqlite`, `storage/tiled_catalog.db` |
| PDFs ever added | 92 paths, 90 blobs, 206 MB, all in `papers/`-style dirs (83 are Wiley DOIs per H0); removed from the tip |
| `/Users/<name>` | 276 matches in 25 files: `alice` 116, `mateo` 98 (README.md 4 at tip), `someone` 28, `david` 24 (docs/migration reviews, `scripts/match_als_zips.py`), `x`, `me`; most are test fixtures proving path scrubbing |
| `/home/<name>` | 35 matches in 4 files (Dockerfile 12, tests) |
| `pscratch` (NERSC) | 123 matches in 14 files (`config.yml` 26, `docs/.../splash_retirement.md` 20, `README.md` 19, `app/modules/graph/releases.py` 14, 3 NERSC scripts). Paths only, no credentials |
| Commit authors | `David Abramov <dabramov@lbl.gov>` 851; `davs.abramov@gmail.com` 2; `Mateo <mateo@...>` 33 with hostnames such as `MacBookPro.lan` and `*.dhcp.lbl.gov`, `*.dhcp.lbnl.us` (publicly visible once pushed, already visible on the public repos if shared history) |
| Personal emails in data | 120 `@gmail.com` matches in 43 files (paper author emails in KG/terminology JSON; 6 unique at tip); 462 `@lbl.gov`/`@lbnl.gov` matches in 36 files, mostly old `matkg_bl1101_v2..v9.json` (28-40 each). At the tip only 2 unique `@lbl.gov` addresses remain in `storage/kg` (v8/v9 were scrubbed per the 2026-11-03 decision, history was not). Test fixtures use fake `curator@lbl.gov`-style names |

Internal hostnames (`*.lbl.gov`, 3,310 matches in 152 files; mostly ALS web-page fixture, KG JSON and README):
`tiled.als.lbl.gov`, `tiled-staging.als.lbl.gov`, `tiled-dev.als.lbl.gov` (README, Dockerfile, `.env.example`,
`app/modules/deployment/tiled_config.py`, authorization and Tiled lookup modules, tests), `api.cborg.lbl.gov`,
`api-local.cborg.lbl.gov`, `cborg.lbl.gov` (`.env.example`, Dockerfile, README, HANDOFF.md,
`app/modules/free_cborg_models.json`), `als.lbl.gov`, `alshub.als.lbl.gov`, `alsusweb.lbl.gov`, `atap.lbl.gov`,
`cdn.lbl.gov`, `newscenter.lbl.gov`, `commons.lbl.gov`, `www.lbl.gov`, `henke.lbl.gov`, `www-cxro.lbl.gov`
(ontology and terminology data), and invented ones in tests (`fair2wise.example.lbl.gov`, `notals.lbl.gov`). The
Tiled and CBORG names are public-facing service names, not secrets; the owner should still confirm the ALS Tiled
hostnames may appear in a public repo.

`HANDOFF.md` is in `.gitignore` but exists in history (matches counted above, including 12 `/Users/` hits).

## 4. Attribution and AGENT/

- `Adapted from als-computing/forge_rsoxs_analysis` appears in 105 files under `app/`, `scripts/`, `tests/`, `ui/src`
  (64 identical one-line headers). The FORGE `LICENSE` in the donor copy is BSD 3-Clause, copyright 2026 The Regents of
  the University of California through LBNL. The notice text exists once, in `docs/migration/README.md` ("Third-party
  notice"); the per-file headers carry no copyright text. BSD-3 clause 1 requires the notice in source
  redistributions. Because the donor and this project share the Regents/LBNL copyright holder this is low risk, but a
  root `NOTICE` carrying the same paragraph is cheap (see section 6).
- `AGENT/` (the FORGE donor copy, contains `forge_rsoxs_analysis-main`) is **ignored** (`.gitignore:198 AGENT/`,
  confirmed by `git check-ignore -v AGENT/x`), has **0** tracked files and **0** files ever added in history. Not in
  the image either (Dockerfile COPY allowlist). Safe.
- `.run/*.pid`: three files were tracked although `.gitignore` already lists `.run/` and `*.pid`, so they showed as
  modified after every run. Fixed on this branch in `83ad20a` (`git rm --cached`, files kept on disk). History still
  holds 7 commits touching `.run`; harmless PIDs.

## 5. The image build in CI

Trace (push of a `vX.Y.Z[-suffix]` tag, or `main`): `publish-image.yml` `gate` -> `candidate` (= `build-app.yml` with
`publish_candidate=true`): `image-static-tests` (pixi dev env, `pytest tests/images tests/ci`) -> `build-candidates`
(150 min limit): free runner disk, `docker pull` of `python:3.12-slim` and `node:20-bookworm-slim` once and record
the digests, `docker buildx build --load --target local|als-dev|als-prod` with the base pinned by digest, labels
`org.opencontainers.image.revision` and `.source`; `scripts/ci/image_checks.py` on each image (no network);
`release_manifest.py build`; artifacts. `publish` job (environment `ghcr-publish`, 90 min): downloads this run's
artifacts, verifies manifest and image ids, `docker login ghcr.io` with `GITHUB_TOKEN`, `publish_candidate.py` pushes
immutable `sha-<40 hex>-<variant>` tags (plus `vX.Y.Z-<variant>` on a release tag), refuses to overwrite a sha tag
with other bytes, then `promote_channel.py` re-tags `:local` and `:als-dev`; `publish-als-prod` (environment
`ghcr-als-prod`) moves `:als-prod`. Digests are recorded in `release-publication.json`. No `:latest`.

Platforms: **amd64 only.** `pixi.lock` does carry `linux-aarch64` and `osx-arm64` for every environment (232 default,
144 tiled-server packages for aarch64) and the Dockerfile has no amd64 pins, so an arm64 image is feasible, but CI does
not build it, `install_pixi.sh` and the manifest tooling are x86-64 oriented, and `promote_channel.py` refuses
multi-platform indexes. Options for the Apple-silicon demo machine, cheapest first:

1. Run the CI amd64 image under Docker Desktop/Colima emulation (works, slower, extra risk for torch/onnx and Tiled
   startup; needs a rehearsal to prove it).
2. Build `linux/arm64` natively on the demo Mac from the tagged commit (`docker buildx build --platform linux/arm64
   --target local`), then `docker save` as the offline bundle (already the W3 fallback). Needs no CI change.
3. Add an arm64 job on a GitHub arm runner (`ubuntu-24.04-arm`, free for public repositories) in a separate workflow
   that builds and pushes only `:local` under a distinct tag, without touching D2.8. This is a design decision, so
   it was not done.

Duration (estimates, not measured; no CI run exists): forge-merge CI jobs run in parallel, roughly 15-40 min wall
clock; the candidate build compiles three images including the pixi environment, Docling weights and the UI, roughly
30-60 min on a 4-vCPU runner, plus `docker save | gzip -1` and artifact upload of all three images (size unknown, no
local docker here to measure); `publish` another 10-30 min. Under QEMU an arm64 build would be several times slower
than native.

What `O06_APPROVED=true` gates: the `gate` job fails the whole workflow unless the repository variable is exactly
`true`, and every writer job re-checks it before `docker login`. Unset (the default), a push of `main` or a `v*` tag
runs nothing past `gate` and nothing is published. It does not affect `forge_merge` pushes (they never reach publish).

Owner configuration needed before the first publish (repository admin, not YAML):

1. Repository variable `O06_APPROVED` = `true` (Settings > Secrets and variables > Actions > Variables).
2. Environments `ghcr-publish` and `ghcr-als-prod`, owner as required reviewer, deployment branches/tags limited to
   `main` and `v*`.
3. A tag ruleset protecting `v*` (the gate requires `github.ref_protected == true`; an unprotected tag fails). For a
   branch publish, `main` must be the default branch and protected.
4. Actions workflow permissions can stay read-only by default (each job declares its own); Actions must be enabled.
5. Packages: first publish creates the package `fair2wise` under the owner. Confirm its visibility is **public**
   (D2.4) and that it is linked to the repository, otherwise `GITHUB_TOKEN` pushes can be refused. No PAT is needed.
6. Optional: protect `forge_merge` so the PR/ui-change-approved label flow works.

### First push and first CI image: step list

1. Rotate the Materials Project key (verdict item 1) and decide the PDF-history question (item 2). Fix the fallback
   line in a normal commit if wanted.
2. Choose the repository (existing fork `origin`, or `fair2wise/FAIR2WISE`, or new) and read `git ls-remote --heads
   origin` to see what is already there. Do not `git push --tags` (tag `v0.0.1` is old).
3. Run the pre-push checklist below.
4. Push only the branch: `git push origin forge_merge` (about 240 MB; expect two 50 MB warnings, a few minutes).
   Triggers `forge_merge validation` only (image candidates: dispatch `Build and check image candidates` on `forge_merge`); no publish.
5. Watch the runs. First-run failure modes: the known-failure comparison (`docs/migration/reviews/H0/known_failures.json`
   was recorded on the Mac; Linux runners may differ), modelscope.cn or Hugging Face timeouts during the Docling fetch
   (rerun), pinned-action or pixi download failures, the frozen UI tree guard, `test ! -e .env`, disk space during the
   three-image build, Playwright `chrome` install, Tiled server tests needing ports.
6. Download the `release-images` artifact from the green run if only a bundle is needed (no ghcr, no admin setup):
   `gh run download <id> -n release-images`, verify `images.tar.gz.sha256`, `docker load`.
7. For a ghcr image: do the owner configuration above, then create a protected tag on the green commit, for example
   `git tag v0.1.0-demo.20261204 <commit>` and `git push origin v0.1.0-demo.20261204`. The tag must match
   `^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$`; the literal `demo-2026-12-xx` form is **rejected by the gate**.
   Approve the two environment prompts. Result: `ghcr.io/<owner>/fair2wise:sha-<commit>-local` and
   `...:v0.1.0-demo.20261204-local`, with `:local` moved.
8. Record the digest from `release-publication.json`; pull on the Mac by digest, run the smoke and rehearsal checks.
   Failure modes: gate error (variable or protection missing), environment waiting for approval, sha tag already exists
   with other bytes (refused by design), package permission denied.

## 6. Pre-push checklist (owner) and what the agent can prepare

Owner checklist:

- [ ] MP key rotated; no other credential to rotate (section 3).
- [ ] Target repo chosen; PDF-history decision made; `git ls-remote --heads origin` read.
- [ ] Decision on emails, `*.lbl.gov` Tiled names, `pscratch` paths in a public repo (all currently public in the
      existing repos? unchecked).
- [ ] Decision on the PyMuPDF AGPL item (H0 E2) if the image goes public.
- [ ] `git status` clean in the primary checkout; `git log origin/forge_merge..forge_merge` reviewed after fetch.
- [ ] Repository settings of section 5 done before any tag push.
- [ ] Choose how the Apple-silicon machine gets its image (options 1-3 above).

Agent can prepare without pushing (not done here unless noted):

- Done: untrack `.run/*.pid` (`83ad20a`).
- Root `NOTICE` with the FORGE BSD-3 paragraph and the shadcn/Unsplash lines.
- Replace the hardcoded `mp_api_key` fallback with an env-only lookup (after the owner rotates).
- A `workflow_dispatch`-only arm64 `:local` workflow (option 3) and an `docs/migration/demo` runbook for
  tag naming (`v0.1.0-demo.YYYYMMDD`).
- `.gitattributes` is already adequate; no LFS needed. `.gitignore` gaps: root `package-lock.json` is untracked in the
  primary checkout (decide track or ignore); `HANDOFF.md` is ignored but exists in history.
- A local dry run of the guards that CI applies (`FROZEN_UI_TREE`, no `.env`, `tests/images tests/ci`) in a clean
  export, to shorten the first-run surprise list.

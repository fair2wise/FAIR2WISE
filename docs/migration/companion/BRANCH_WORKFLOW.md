# Branch and worktree policy - forge_merge

**Draft v7 | Required by the project owner | No branch has been created by this handoff**

## 1. Source and integration branch

- Repository: `https://github.com/fair2wise/FAIR2WISE`.
- Base branch: `rsoxs` / `refs/heads/rsoxs`.
- Reviewed uploaded snapshot: `222b1e7b130567dbf4a3242697ac2b465794a38d` (ZIP comment, not independently verified Git object).
- F2W integration branch for this work: **`forge_merge`**.
- The uploaded ZIP contains no `.git`. Do not use `git init` to invent a baseline commit/history. Work in an actual clone containing the reviewed commit; if it cannot be obtained, stop at H0 with the source discrepancy.

All accepted migration commits must be on forge_merge. Main/rsoxs remain untouched by this work until the owner approves a later merge. Local branch creation, committing reviewed changes, pushing a branch, merging to a protected branch and deploying are distinct actions; this plan does not implicitly authorize a push, force-push, release or deployment.

## 2. Safe preparation in the real checkout

The following are inspection/preparation commands for the receiving agent, **not commands executed during plan drafting**. Run them individually, check each result, and stop on errors. Do not paste them into a script that ignores failures.

```bash
git rev-parse --show-toplevel
git remote -v
git status --short --branch

git fetch origin rsoxs

git cat-file -t 222b1e7b130567dbf4a3242697ac2b465794a38d
git rev-parse origin/rsoxs
git log -1 --format=fuller 222b1e7b130567dbf4a3242697ac2b465794a38d
git diff --stat 222b1e7b130567dbf4a3242697ac2b465794a38d origin/rsoxs

git show-ref --verify refs/heads/forge_merge
git ls-remote --heads origin refs/heads/forge_merge
git worktree list --porcelain
```

The local show-ref command returns a nonzero status when the branch is absent; treat that specific absence as a decision input, not permission to ignore arbitrary command failures. A failed ls-remote request is not proof that the remote branch does not exist. Do not log credential-bearing remote URLs in review packets. Do not auto-stash, reset, clean or overwrite the user's working tree.

Confirm that the commit is a commit object and compare its source tree with this archive/inventory. If origin/rsoxs has advanced, the owner decides whether to use the reviewed commit or first update the inventory to a newer base. Report added/removed files and relevant API/LLM/schema differences. An ancestry check can help but does not replace source equivalence. Never silently start from main.

### Branch absent locally and remotely

After verification and branch-location checks, create a dedicated integration worktree from the approved baseline:

```bash
git worktree add -b forge_merge ../FAIR2WISE-forge_merge 222b1e7b130567dbf4a3242697ac2b465794a38d
cd ../FAIR2WISE-forge_merge
git branch --show-current
git rev-parse HEAD
git status --short
```

Expected branch: forge_merge. HEAD must be the approved baseline before migration changes. The destination must be new and empty; do not delete a colliding directory. At H0 record repository, baseline, archive hash, branch, worktree and test-environment fingerprints.

### Branch already exists

Do not recreate it with `-B`, reset it, or discard its commits. Inspect its base, commit graph, pending work and existing worktree. If a remote-only branch exists, fetch that explicit branch and inspect it before creating an appropriate local tracking branch. Ask the owner to approve continuing a conflicting/diverged branch. Do not check out one branch in multiple worktrees with force options.

## 3. Parallel implementation and integration

The integration lead alone changes the forge_merge worktree. Parallel workers may use clearly named scratch branches such as `forge_merge-T05-literature`, `forge_merge-T09-paper-figures`, and `forge_merge-T18-ui-compat`, each from an approved integration commit. Their reviewed commits are merged or cherry-picked by the integration lead **into forge_merge**. Use hyphenated names: do not use `forge_merge/<task>` because the existing `forge_merge` ref conflicts with a branch hierarchy under that same name. A scratch branch is not an alternate delivery branch; no worker targets main/rsoxs.

Each worker records base commit, owned paths, task/contract version, fixture roots and the human gate that authorizes the work. Use different corpus/cache/job/Tiled namespaces per worker. One owner controls shared schemas, public DTOs, dependency locks, route registration and registry bootstrap. Never run concurrent publishers against one corpus. Agree the integration method at H1; avoid rewriting another worker's history. The FORGE repository may need its own consumer-adapter branch, recorded separately; do not claim the F2W branch contains FORGE-repository commits.

Recommended commit granularity: one contract/test seam, one forwarding adapter, one donor capability, or one mechanical move. Commit message includes task ID and explicit behavior change. Include provenance/license notices for adapted donor code. Do not combine a schema/identity migration, package rename and parser replacement in one commit. Gate packets record exact commits and test results, not merely filenames.

## 4. CI must run on the integration branch

The supplied `.github/workflows/build-app.yml` triggers on push/PR to main only. Extend its test trigger or add a non-deploy migration workflow covering pushes to forge_merge and PRs whose **base** is forge_merge. Include appropriate task branches if the chosen workflow needs pre-integration checks. Do not enable main-only MkDocs publication or README mutation on the migration branch.

Required check groups: Python contract/parity tests, import/dependency closure, frontend unit/build, existing mocked browser tests, new real-API browser tests, and required disposable-Tiled integration/recovery. Run heavy/potentially paid evaluation only with explicit approval; CI defaults to fake inference and isolated services. Configure protected-branch required checks with the owner; writing a workflow file alone does not configure repository protection.

## 5. Completion and cutover

Do not commit bundled PDFs, SQLite catalogs, .run PID files, credentials, caches or source archives as migration artifacts. The uploaded source contains runtime/data material that must not become a new baseline import by accident. Deliver code, sanitized fixtures, provenance, plans and review records only.

H7 approves a specific cutover and deletion list; it does not implicitly approve main/rsoxs merge, graph deletion, force-push or public release. Preserve the old UI and import/API compatibility facades through the canary. Final handoff names the forge_merge commit range, baseline commit, remaining deviations and rollback point.

## 8. Git data and the separate FORGE repository (v7)

Reviewed frozen JSON KGs and safe manifests are committed into the F2W integration branch alongside code, in separately reviewable changes. Export only allowlisted immutable commit/path/hash selections for deployment; no production extraction rebuild and no automatic Tiled write from git push. Raw PDFs/parsed bodies/crops, operational stores, secrets and local credentials are not committed. See GIT_KG_PROMOTION_SPEC.md.

FORGE donor repository: als-computing/forge_rsoxs_analysis. The project owner works with Alex to select the integration target/base, review and merge. No FORGE target branch has been specified; forge_merge is the F2W branch only. The agent may prepare/test a local consumer patch, but must obtain that coordination before a remote push/PR/merge. Record patch-ready, tested, submitted and merged statuses separately. F2W H0-H7 approval does not override FORGE branch protections or its merge ownership.

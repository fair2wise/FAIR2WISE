# GitHub Actions and image publication contract

**Draft v8. Requirements plus proposed promotion policy; not a ready-to-run workflow.**

## Fixed outputs

One repository image, provisionally the existing GHCR repository normalized to `ghcr.io/fair2wise/fair2wise`, with `local`, `als-dev`, `als-prod` variants. The respective final Docker targets contain: complete local app plus Tiled server; complete app without server using staging Tiled; complete app without server using production Tiled. All are built for `/fair2wise`. Only supported/verified platforms may be listed in the release manifest.

## Workflow responsibilities

1. **validate-source:** resolve the commit, install from the reviewed dependency lock, run existing and required binary/compatibility tests. Include forge_merge/PR-to-forge_merge triggers. Main-only docs/deploy side effects stay guarded.
2. **build-candidates:** a matrix over local, als-dev, als-prod builds all finals from that commit. Produce candidate digests/OCI artifacts with profile labels; do not give this job runtime Tiled/CBORG credentials. Build tooling/action versions are explicitly pinned after verification.
3. **test-candidates:** mount synthetic/open-source fixture content at runtime, fake inference, private temporary state. Run the published prefix, old UI workflow, SSE, required paper figures/parser dependencies, JSON-no-Tiled build, and real disposable Tiled import. ALS finals connect to disposable endpoints only under an explicit isolated test configuration; never exercise staging/production writes in ordinary CI. Test that production-profile defaults reject an unapproved environment mismatch.
4. **inspect-images:** inspect all layers and build contexts for papers/full text/crops/generated private snapshots/DBs/secrets. Verify local includes its server; ALS variants have no configured local-server runtime/startup and do not pull one when central Tiled is unavailable. Produce provenance/SBOM/check evidence using supported tools.
5. **publish-candidates:** a trusted job with minimum registry-write authority publishes content-addressed/commit-specific variant references and records the exact digests. An untrusted pull request cannot reach this job or runtime secrets. No dangerous pull_request_target execution of untrusted build scripts.
6. **promote-required-tags:** move `:local`, `:als-dev`, `:als-prod` only under the owner-approved O06 policy. Production promotion is protected and targets the tested digest. Concurrent workflows serialize per tag; stale builds must not move a channel backwards. GitHub Actions builds/publishes; Ansible owns live rollout.
7. **release-manifest:** record source commit, variant, digest, supported platforms, dependency and schema fingerprints, base path, Tiled mode/version and required deployment variables. Record paper corpus/snapshot imports separately; no corpus is in the artifact.

## Proposed trigger policy to approve at H1 (O06)

- Pull requests: test/build only; no public channel updates, secrets or deployment.
- Trusted forge_merge pushes: CI and candidate build. Propose local/dev publication after successful checks; do not assume a feature-branch push authorizes production rollout.
- Owner-approved release or explicit protected dispatch: publish/promote all required channels from an approved source, with an additional protection boundary for als-prod.

Choose precise events once with the owner and apply them consistently. Existing source main/v* triggers are evidence, not the decided future rule. Publishing an als-prod image is different from deploying it and from importing production data.

## Secrets and caching

GITHUB_TOKEN is for registry publication with reviewed permissions, not a Tiled admin credential. Use BuildKit secrets only when an authenticated dependency fetch genuinely requires them; runtime Tiled/LLM credentials never belong in build arguments or frontend variables. Do not upload mounted private papers, parser results, job state or privileged logs to caches/artifacts. Keep independent environment credentials and no production secret availability to local/dev CI.

## Platforms and reproducibility

Propose linux/amd64 and, where the parser/inference-client dependency lock passes, linux/arm64 for local use. Confirm deployed ALS host architecture before publishing its platform matrix. Do not claim cross-platform support from emulated compilation alone; required tests and unsupported dependencies must be reported. Pin base images by reviewed digest and dependencies by lock; do not invent digest or action SHA values in a draft. The three tags may differ in configuration layers and local server content while sharing the same application build.

## Acceptance

Three final variants exist, digest-based test/promotion is recorded, untrusted publishing is blocked, all user-facing paths work beneath /fair2wise, no corpus/secrets are in any layer, ALS builds never start local Tiled, and a protected production promotion can be rolled back by digest independently of data. H6 reviews the workflow evidence; H7 authorizes actual rollout.

## Git data artifacts and authorization regression

D31 commits versioned graph JSON/manifests to the F2W repository; it does not authorize baking them into images or importing them on a push. Add allowlisted-data/disclosure/secret checks and exact committed-byte hash validation. Test all image layers with a tracked graph fixture to prove corpus artifacts remain excluded. Record code image digest separately from the promoted data commit/path/hash and staging binary report.

T25 tests native Tiled verified-principal/admin guards and non-admin denial using isolated identity fixtures/servers. No PR receives live production user or writer credentials. T26 tests exact Git artifact export, wrong-hash rejection, zero-inference promotion and separate target receipts using temporary Git fixtures/disposable servers. The application host receives only allowlisted snapshot bytes via deployment mounts; no Git push credential or model call is needed to import.

Actual FORGE PR submission/merge is coordinated by the owner with Alex; F2W pipeline success does not trigger a remote FORGE merge. O06 only concerns image tag events, not who may write the KG: D30 settles that as authenticated admins.

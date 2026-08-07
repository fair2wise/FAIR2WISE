# Release and maintenance

This runbook defines how FAIR2WISE versions, validates, publishes, upgrades,
and rolls back releases. Deployment-specific backup commands are in
[Fresh-machine deployment](deployment.md).

## Current version sources

The repository does not currently expose one importable application-version
constant. Several component-local versions exist:

| Location | Scope |
|---|---|
| Git tag `vMAJOR.MINOR.PATCH` | Intended whole-repository release identity |
| Root `Dockerfile` labels | Agent and frontend image description |
| `ui/package.json` | Private UI package metadata |
| `splash_links/pyproject.toml` and `pixi.toml` | Vendored Splash package metadata |
| `splash_links/Containerfile` label | Splash container description |
| `storage/schema/matkg_schema.yaml` | LinkML data-model version |

These values are not automatically synchronized and must not be treated as
equivalent. For an operational deployment, record the Git tag, full commit
SHA, and immutable container digest. The Git tag is the release identity until
a central application version is introduced.

## Versioning policy

Use semantic versions for repository releases:

- **Patch** (`x.y.Z`): backward-compatible bug, security, documentation, or
  packaging correction; no intentional API or persisted-data break.
- **Minor** (`x.Y.0`): backward-compatible feature, optional graph field,
  additive endpoint, or forward migration that old data can survive.
- **Major** (`X.0.0`): incompatible HTTP contract, graph identity/dedup rule,
  required configuration, storage format, or database change.

Pre-release tags use SemVer suffixes such as `v2.1.0-rc.1`. Do not reuse or
move a published tag. A rebuilt artifact must get a new version or be
identified by a different commit SHA.

Changes to the MatKG LinkML schema have their own version. Increment that
schema version when its contract changes and explain whether old graph JSON is
still accepted. An application release may include a schema change without
sharing its exact version number.

## Release checklist

### Prepare

- Start from a clean, reviewed `main` at the intended commit.
- Select the SemVer impact from the public API, identifier, graph, and database
  consequences—not from the number of changed lines.
- Update user and operator documentation, including configuration defaults,
  security boundaries, provenance rules, and upgrade/rollback notes.
- Update relevant component versions/labels. Keep all Splash component version
  declarations aligned if Splash itself is being released.
- Regenerate and review every affected dependency lock.
- Confirm no secrets, local `.env`, session runs, caches, generated sites,
  unrelated SQLite files, or private publications are included.
- Review pending approvals and known limitations that should be called out in
  release notes.

### Validate

Run the complete deterministic checks:

```bash
python3 -m pip check
python3 -m pytest
cd ui && npm ci && npm test && npm run build && cd ..
cd splash_links && pixi run test && cd ..
mkdocs build -f mkdocs/mkdocs.yml --strict
docker compose config --quiet
```

With Docker running and a valid test key, run:

```bash
./scripts/test_compose.sh
```

For a release that changes extraction or retrieval quality, run the relevant
opt-in evaluation suite and record model/configuration inputs. For a migration,
exercise an upgrade on a copy of the previous database and verify the rollback
plan. Finally perform a manual UI smoke test covering health, a known-evidence
answer, graph search, a disposable graph edit, settings, and session switching.

### Tag and publish

Create an annotated tag only after validation succeeds:

```bash
git tag -a vX.Y.Z -m "FAIR2WISE vX.Y.Z"
git push origin vX.Y.Z
```

Confirm the tag resolves to the reviewed commit, the publishing job succeeds,
and the published image digest is recorded in the release notes. Publish notes
that include upgrade steps, data compatibility, migrations, dependency/security
changes, and known issues.

### Verify after publishing

- Pull or build from the tag in a clean checkout.
- Start the Compose stack with a disposable volume set.
- Confirm only the frontend host port is published.
- Verify `/healthz`, proxied `/api/health`, initial graph population, a real
  authorized CBORG request, restart persistence, and logs.
- Keep the prior release and pre-upgrade database backup until the observation
  window closes.

## Container publishing

`.github/workflows/publish-image.yml` runs on pushes to `main` and tags matching
`v*`. It logs into GitHub Container Registry and publishes the root
`Dockerfile`'s **agent** target as:

```text
ghcr.io/<owner>/<repository>:<metadata-generated-tag>
```

The exact tag set and OCI labels come from `docker/metadata-action`; inspect the
workflow output rather than guessing a tag. Consumers should pin the resulting
digest for a reproducible deployment.

!!! important "Compose still builds locally"

    The current publishing workflow does not publish the frontend target or
    the vendored Splash image. The canonical `compose.yaml` has `build:` entries
    for all services and therefore builds from a clone. Pulling the GHCR agent
    image alone is not a complete FAIR2WISE stack.

Before changing the workflow to publish the entire stack:

1. give agent, frontend, and Splash distinct image names;
2. publish all three from the same tag and architecture matrix;
3. add digest-pinned `image:` references without removing the supported local
   build path;
4. verify multi-architecture base-image and Pixi compatibility; and
5. update Compose upgrade/rollback and supply-chain documentation.

Local pre-publish builds can be inspected directly:

```bash
docker build --target agent -t fair2wise-agent:release-check .
docker build --target frontend -t fair2wise-frontend:release-check .
docker build -f splash_links/Containerfile \
  -t fair2wise-splash:release-check splash_links
```

## Dependency upgrades

Dependabot groups Docker, GitHub Actions, and Python proposals weekly. Grouping
reduces pull-request volume but broadens the regression surface, so split or
defer a group when the source of a failure is ambiguous.

Use this maintenance sequence:

1. Read upstream release notes, Python/Node support ranges, and security
   advisories.
2. Update the human-owned manifest, then regenerate its lock with the owning
   tool. See [Contributor guide](contributing.md#dependency-lock-updates).
3. Inspect the complete transitive diff and confirm removed packages are no
   longer imported.
4. Run the affected component suite and a production/container build.
5. For model, retrieval, extraction, or numerical dependencies, compare a
   representative output instead of relying only on imports and unit tests.
6. Upgrade one ecosystem at a time when practical so rollback is clear.

Do not widen a version range just to make resolution pass. Preserve Python
3.12, Node 20/npm 10, and the locked Pixi environment unless the runtime
support policy is intentionally changing.

## Database compatibility expectations

Splash owns the relational schema and applies Alembic migrations during service
startup for persistent SQLite and PostgreSQL databases. In-memory test stores
create a fresh schema instead.

Every database change must meet these expectations:

- Add a new Alembic revision; never rewrite a revision that may already have
  run on a user database.
- A new application release must start against a backed-up database from the
  previous supported release and migrate it automatically.
- Prefer additive tables, nullable columns, and indexes. Destructive or
  lossy transformations require a major release, an explicit export/restore
  path, and operator confirmation.
- Preserve entity UUIDs, MatKG URIs/IDs, link endpoints, properties, and source
  provenance unless a documented migration changes them.
- Test SQLite, and test PostgreSQL whenever dialect-specific types, extensions,
  or SQL are touched.
- Back up before first startup of a release containing migrations.

Application rollback is not the same as database downgrade. Once a forward
migration runs, an older image is not assumed to understand the new database.
The supported rollback is to stop services, restore the matching cold backup,
and start the earlier release. Do not rely on Alembic `downgrade` in production
unless that exact path was tested for the release.

Pre-Alembic databases are detected and stamped by Splash startup. Because a
stamp records history rather than reconstructing missing schema, any change to
that compatibility path needs a real legacy-database fixture.

MatKG JSON compatibility is separate from SQL compatibility. New node and edge
fields should be optional; the importer preserves non-core node fields in
Splash entity properties. Renaming/removing fields or changing identifiers
requires conversion tooling, mixed-version tests, and a major-version review.

## Routine maintenance and rollback

- Monitor image/base dependency updates and expiring external credentials.
- Check graph, run, and cache volume growth; use [Performance](performance.md)
  for safe inspection and cleanup.
- Periodically restore a backup into a disposable stack. An untested backup is
  not a rollback plan.
- Retain the seed database, schema, requirements locks, Pixi lock, npm lock,
  release tag, and image digests needed to recreate each supported release.
- Use the [deployment upgrade and rollback procedures](deployment.md#upgrade-procedure)
  for operational changes.

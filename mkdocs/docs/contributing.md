# Contributor guide

This guide defines the recommended change workflow for FAIR2WISE. It complements
the command-focused [Testing](testing.md) page and the component map in
[Repository reference](repository-reference.md).

## Branch and change workflow

The maintained integration branch is `main`. GitHub Actions runs for pushes and
pull requests targeting `main`.

1. Update local `main` without rewriting shared history:

    ```bash
    git switch main
    git pull --ff-only
    ```

2. Create a short-lived branch with one purpose:

    ```bash
    git switch -c feature/short-description
    ```

3. Make the smallest coherent change. Keep generated locks with their input
   manifests, tests with behavior, and documentation with public contract
   changes.
4. Review `git diff` and `git status`. Do not include `.env`, credentials,
   local databases other than the intentional seed, caches, `runs/`,
   `ui/dist/`, or `mkdocs/site/`.
5. Run the validation required for every changed surface.
6. Open a pull request to `main`, describe behavior and data-compatibility
   effects, and wait for the required CI job to pass.
7. Rebase or merge the latest `main` if the branch has drifted, rerun affected
   checks, and merge through the pull request. Never force-push `main`.

Use issue-oriented branch prefixes such as `feature/`, `fix/`, `docs/`, or
`maintenance/`. Commit messages should explain the behavior changed rather
than list files. Separate unrelated cleanup from a functional fix so failures
and rollbacks remain attributable.

### Pull-request description

Record at least:

- the user-visible or operator-visible outcome;
- affected services and data formats;
- validation commands and their results;
- new environment variables, external calls, or permissions;
- database migration, seed, or rollback implications; and
- screenshots for meaningful visual changes.

## Dependency-lock updates

### Python

Human-edited dependency inputs live in `requirements/*.in`. Generated
`requirements/*.txt` files are Python 3.12 locks and must not be hand-edited.
The root `requirements.txt` is only a compatibility include for
`requirements/runtime.txt`.

Use Python 3.12 and pip-tools from the development environment:

```bash
python3.12 -m pip install -r requirements/dev.txt
python3.12 -m piptools compile --strip-extras \
  -o requirements/runtime.txt requirements/runtime.in
python3.12 -m piptools compile --strip-extras \
  -o requirements/dev.txt requirements/dev.in
python3.12 -m pip check
```

When `runtime.in` changes, regenerate both `runtime.txt` and `dev.txt` because
`dev.in` includes the runtime inputs. Regenerate `globus.txt` or `legacy.txt`
only when its corresponding input changes:

```bash
python3.12 -m piptools compile --strip-extras \
  -o requirements/globus.txt requirements/globus.in
python3.12 -m piptools compile --strip-extras \
  -o requirements/legacy.txt requirements/legacy.in
```

`requirements/semantic.in` intentionally has no compiled lock. It is an
optional heavyweight FAISS/SentenceTransformer/Torch profile and is not part
of the runtime image. A change there requires an explicit semantic-retrieval
test environment and a note in the pull request.

Inspect lock diffs for unexpected transitive upgrades. A direct dependency
removal is incomplete until imports, Docker installation, tests, and relevant
documentation have also been checked.

### Web UI

`ui/package.json` declares npm dependencies and `ui/package-lock.json` is the
reproducible install. Use npm to update both files:

```bash
cd ui
npm install package-name@version
npm ci
npm test
npm run build
```

Do not edit `package-lock.json` manually. Commit it with `package.json` and
review changes to resolved packages and integrity hashes.

### Splash Links and container inputs

Splash dependencies are owned by `splash_links/pyproject.toml`,
`splash_links/pixi.toml`, and `splash_links/pixi.lock`. After changing a
manifest, refresh the Pixi lock from `splash_links/` and run `pixi run test`.

Base-image and GitHub Action versions live in `Dockerfile`,
`splash_links/Containerfile`, and `.github/workflows/`. Dependabot proposes
weekly grouped updates for Docker, Actions, and Python. Treat those pull
requests like application changes: inspect release notes, rebuild, and run the
surface-specific checks rather than merging solely because resolution
succeeded.

## Required validation by change type

The following matrix is the minimum. Add narrower regression tests whenever a
bug or compatibility boundary can be encoded deterministically.

| Change | Required validation |
|---|---|
| Any Python behavior | Relevant targeted tests, then `python3 -m pytest` |
| Agent/API/session workflow | Agent/API tests plus the full root suite |
| Extraction, schema, or graph conversion | Extraction, JSON2KG, retrieval, provenance tests, then full root suite |
| Splash store, GraphQL, importer, or migration | `cd splash_links && pixi run test` plus root Splash/KG-update tests |
| UI behavior or API client | `cd ui && npm test && npm run build` |
| Shell script | `bash -n scripts/name.sh` plus its associated test or a safe dry run |
| MkDocs or public behavior | `mkdocs build -f mkdocs/mkdocs.yml --strict` |
| Python requirement | Regenerated locks, clean install where practical, `pip check`, root tests |
| npm requirement | `npm ci`, UI tests, production build |
| Dockerfile, Compose, startup, or networking | `docker compose config --quiet` and `./scripts/test_compose.sh` |
| Database migration or seed | Migration tests, backup/upgrade/rollback exercise, Compose smoke test |

The current GitHub workflow installs `requirements/dev.txt`, checks selected
Black targets, runs flake8, executes root pytest, and builds MkDocs strictly.
UI, Splash, Compose, and live external-service checks are not all enforced by
that job, so contributors must run them when affected.

Before submitting Python, also run the formatting/lint checks that match the
changed files. The CI commands are visible in `.github/workflows/build-app.yml`.
Do not use a live CBORG, OpenAlex, GitHub, NERSC, or Globus request as the only
test for deterministic logic.

## Adding an Agent API route

The React-facing API is constructed by `create_app()` in
`app/modules/f2w_agent/api.py`.

1. Define bounded Pydantic request and response models near the existing wire
   models. Validate sizes, enum-like values, IDs, and optional fields at the
   boundary.
2. Put reusable behavior on `AgentPipelineService` or in a focused module.
   Keep the route handler limited to validation, service dispatch, and stable
   HTTP error mapping.
3. Register the route without an `/api` prefix. Local clients call the agent
   directly; container Nginx adds the public `/api/` gateway and strips that
   prefix before proxying.
4. Preserve the service lock/session rules for mutations. Do not bypass graph
   source checks or approval boundaries.
5. Add FastAPI/service tests under `tests/`, including invalid input and
   failure mapping.
6. If the UI uses the route, add wire types, one centralized client function,
   and mocked-fetch tests in `ui/src/app/components/data/liveAgent.ts` and its
   test.
7. Update [Agent API](agent-api.md), [UI API integration](ui-api.md), and the
   security documentation if the route reads secrets, mutates data, or expands
   external access.

For streaming operations, follow the existing Server-Sent Event contract:
`progress` events precede exactly one terminal `complete` or `error` event.

## Adding or changing graph fields

A graph field can cross six representations. Trace all of them rather than
changing only the UI or JSON serializer:

1. **Extraction:** update the LinkML schema when the field is domain-model
   data, then update term-extractor models, prompts, normalization, merge, and
   serialization rules.
2. **MatKG conversion:** preserve the value in `app/modules/json2kg.py` on the
   correct `thing` or `association`. Keep new fields optional so older JSON
   remains readable.
3. **Splash import/storage:** non-core node fields are stored in entity
   `properties` by `splash_links/scripts/import_kg.py`. A new SQL column or
   index instead requires a new Alembic revision and migration tests.
4. **Retrieval:** include the field in search text, ranking, evidence tests, or
   bounded context only when it improves grounded retrieval. Never mix
   publication metadata between source papers.
5. **Agent API:** add the field to `GraphNode` or the appropriate public model,
   raw-to-wire conversion, edit payloads, and Splash round-trip logic if the UI
   needs it.
6. **UI:** update `LiveGraphNode` or related types, details/editor rendering,
   category colors when applicable, and tests.

Do not change MatKG ID construction, publication deduplication, or edge triple
identity casually. Those rules are persistence and citation contracts; see
[Data provenance](provenance.md) and [Release and maintenance](release.md).

## Adding an agent

Core workflow agents live in `app/modules/f2w_agent/`; monitored extraction
agents live in `app/modules/term_extractor/`.

1. Give the agent one responsibility and typed/serializable `@action` results.
2. Keep external clients injectable or mockable, and fail closed when an
   evidence or approval decision is uncertain.
3. Instantiate the agent in `AgentPipelineService._rebuild_agents()` when it
   participates in the web/CLI workflow.
4. Add its action to orchestrator choices, prompts, deterministic validation,
   transition limits, and progress reporting. A model-proposed action must
   never bypass the deterministic guard.
5. Thread configuration through `CoordinatorConfig`, CLI flags, launcher
   environment variables, and `/settings` only where runtime changes are safe.
6. Add focused unit tests plus integrated routing/state tests.
7. Add a module under `app/modules/launchers/` only if the agent genuinely
   needs an independent executable entry point. Internal workflow agents do
   not need launchers.
8. Update the agent workflow, API, configuration, and repository reference.

## Adding a UI feature

1. Choose the state owner first: application/session state belongs near
   `App.tsx`; chat/workflow state belongs in `ChatSidebar.tsx`; durable browser
   data belongs in a versioned helper such as `chatSessions.ts`.
2. Reuse `components/ui/`, Finch shell components, graph helpers, publication
   components, and centralized API code before adding another abstraction.
3. Keep secrets out of browser code and all `VITE_*` values.
4. Abort requests or ignore stale completions when users can switch sessions,
   views, or settings during an operation.
5. Supply labels for icon-only actions, keyboard behavior, visible error
   states, and reduced-motion handling.
6. Add colocated Vitest coverage for pure logic and mocked network behavior.
7. Run `npm test` and `npm run build`; inspect any bundle-size warning rather
   than increasing its threshold without measurement.
8. Update the [UI user guide](ui-user-guide.md), [UI architecture](ui-architecture.md),
   and API documentation as appropriate.

See [Performance](performance.md) before rendering an unbounded graph or
adding a large frontend dependency.

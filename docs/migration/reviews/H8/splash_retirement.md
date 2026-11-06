# T16B Splash retirement: inventory, compatibility window and deletion list (H8 review packet)

Status: **preparation packet. Evidence for the owner's H8 decision, not approval.**
Nothing in this document approves a deletion, a cutover or a window; sections 6 and 7 are proposals written in the form the owner can
approve or amend. The code-side part done with this packet is limited to class (b) (section 3).

Date: 2026-11-06. Tip: `forge_merge` at `71e9bab`, branch `forge_merge-T16B-splash`. Owner instruction
(2026-11-06): "also work on T16B splash_links retirement". Governing decisions: plan D03 ("Splash is deprecated
and replaced by Tiled Graph ... No new Splash backend"), D16 ("already fully replaced ... do not require a live
Splash service"), plan section 5.2 and `docs/migration/companion/FRONTEND_COMPATIBILITY.md` section 5 (UI04:
"Old splash/splash_links tokens work through approved Tiled mapping; no Splash service"), plan H7 text ("agree a
compatibility window and deletion list. Keep import/API wrappers through the canary, with no obligation to run
deprecated Splash"), `docs/migration/ui_contracts/legacy_splash_to_tiled_adr.md`.

Pinned by `tests/contracts/legacy_ui/test_splash_retirement.py` (new) together with the existing
`tests/contracts/legacy_ui/test_legacy_ui_contract.py`, `tests/images/test_images_dockerfile.py`
(`test_splash_stays_only_in_the_legacy_agent_target`), `tests/test_docker_startup.py`, `tests/test_splash_fallback.py`,
`tests/test_splash_helpers.py` and `tests/coverage_t27/test_kg_update_graphql.py`. The new test also fails when a
code or configuration file that mentions Splash is missing from this inventory, so the deletion list cannot be
shorter than the tree.

## 1. Classes and counts

| Class | Meaning | Count |
|---|---|---|
| (a) wire vocabulary, stays | the legacy tokens the frozen browser sends and stores, and the adapter that maps them to the Tiled literature graph (D03/D16, 5.2) | 12 code/fixture locations (section 2) |
| (b) dead, removed now | Splash configuration nothing reads or executes; verified by grep, import graph and the full suite | 3 edits in 3 files (section 3) |
| (c) deletion list at cutover (H8) | live but legacy-only: the Splash client, the `agent` image target, `compose.splash.yaml`, the scripts, env names, docs | 10 code modules, 1 image stage, 1 compose file, 9 scripts, 1 CI/ignore line, 8 env names, 33 doc pages, 1 vendored tree of 65 files (section 4) |
| (d) owner decision | window length, fate of `splash_links/` and its `_tests`, `links.sqlite`, the `kg_rag` default, UI text, NERSC scripts, the diagnostic field | 9 questions (section 5) |

Counts are of files/locations; "mentions" are case-insensitive `splash` hits. Out of scope and not Splash-links
(listed so the completeness test knows them): `app/modules/bl1101_ingest.py` names the unrelated ALS repository
`als-computing/splash_tiled` as beamline metadata; `app/modules/tiled_sim.py` mentions Splash in a docstring about
Tiled GraphQL entity types; `.gitignore` ignores `splash_flows_globus/` and `splash_auth/` (other ALS projects);
`ui/ATTRIBUTIONS.md` is about Unsplash photos; `app/modules/security/disclosure.py` redacts any config subtree named
`splash` (a redaction rule, kept).

## 2. Class (a): legacy wire vocabulary that must stay

The browser snapshot is frozen (plan 5.2). Removing any token below breaks the shipped UI; the ADR maps them to the
Tiled literature graph, which is what the Tiled Save path (T18D) and post-extraction publication (T18E) already do.

| Location | Token / behaviour | Why it stays |
|---|---|---|
| `ui/src/app/components/agentSettings.ts` (`AgentGraphSource`, `graphSourceToApi`, `graphSourceFromApi`) | browser `graphSource=splash_links`; API `graph_source=splash`; any non-`json` answer maps back to `splash_links` | FRONTEND_COMPATIBILITY.md section 5; pinned by `test_graph_source_wire_tokens_in_ui_source` and the new test |
| `ui/src/app/components/data/liveAgent.ts` (`AgentSettings`, `AgentSettingsUpdate` types) | `graph_source: 'splash' \| 'json'` | same |
| `ui/src/app/components/AppSettingsButton.tsx` (`SettingOption id="splash_links"`) | the radio value is the storage token | token stays; its description text is a (d) question (section 5, Q6) |
| `ui/src/app/components/agentSettings.test.ts`, `ui/e2e/capabilities.spec.ts`, `ui/e2e/settings.spec.ts` | UI unit/e2e tests of the mapping | keep with the tokens |
| `app/modules/f2w_agent/api.py` `ChatRequest`, `ChatActionRequest`, `AgentSettingsUpdate` (`pattern="^(splash\|json)$"`) | API enum | OpenAPI snapshot `tests/contracts/legacy_ui/fixtures/openapi_ui.json`; `test_legacy_ui_contract.py::test_openapi_*` |
| `app/modules/f2w_agent/api.py` `initial_graph_source`, `runtime.graph_source == "splash"`, `/health.kg_mode`, `GET/PUT /settings.graph_source` | the non-`json` literal that keeps editing enabled in the browser (`GraphMockup.tsx` edit gate) | ADR section 2.1 |
| `app/modules/f2w_agent/api.py` SSE phases `splash_reimport_started`, `splash_reimport_result`, status `splash_error`, `_PUBLICATION_PHASE_MESSAGES`; `app/api/main.py`, `app/api/post_extraction.py` | legacy phase names now emitted for the Tiled publication stage (T18E) | FRONTEND_COMPATIBILITY.md section 4; `fixtures/sse/*.sse` |
| `app/modules/f2w_agent/api.py` error texts `Splash update failed: ...` (502), `requires splash graph mode` (400) | status literal and HTTP code | ADR 2.1 (wording may change, codes may not) |
| `app/api/routes_capabilities.py` (`getattr(runtime, "graph_source", "splash") != "json"`) | `can_save` is false in JSON mode | owner decision Q13 / O-U3 |
| `app/modules/runtime_bridge/retrieval.py`, `app/modules/runtime_bridge/legacy_adapter.py`, `app/modules/f2w_agent/retrieval_agent.py` | `graph_source` is `"json"` or `"splash"` on the reload path | the retrieval reload honours the same enum |
| `app/modules/graph/legacy_save.py`, `_NoSplash` / `_TILED_SAVE_NO_SPLASH` in `api.py` | the Tiled Save backend and the no-op stand-in that make every Splash call unreachable when a Save target exists | this *is* the compatibility adapter (T18D, Q7-Q14) |
| `tests/contracts/legacy_ui/fixtures/**` (71 fixture files), `tests/contracts/legacy_ui/_legacy_ui_harness.py`, `tests/contracts/legacy_ui/closure/launch_full_backend.py`, `tests/e2e/live_backend/**` | recorded baseline payloads with the tokens; the in-memory Splash fake and the "absent Splash" condition | frozen contract evidence; the `absent` condition is the retirement target state |

`storage_backend=tiled` diagnostic: the ADR (section 2.2) proposes an additive `graph_backend.storage_backend`
field on `/settings` and `/health`. **It is not implemented on `forge_merge`** (no `storage_backend` or
`graph_backend` string exists under `app/`); nothing was changed about it here. Whether to add it before the cutover
is Q9 in section 5.

## 3. Class (b): dead Splash configuration removed with this packet

Each item was checked three ways: no reader (`grep -rn` over `app/`, `scripts/`, `tests/`, `ui/`, `docs/`, `mkdocs/`,
`.github/`, the compose files and the Dockerfile), no importer in the module graph, and the full not-slow suite plus
the legacy-UI contract tests green afterwards.

| Removed | Was | Evidence it was dead |
|---|---|---|
| `config.yml` `paths.splash_links_repo` (env `SPLASH_LINKS_REPO`, default `splash_links`) and `paths.splash_links_db` (env `SPLASH_LINKS_DB`, default `links.sqlite`) | configuration keys | no `config_value("paths.splash_links_*")` call anywhere; the scripts read the two environment variables directly (`scripts/start_all.sh`, `scripts/start_agent_backend.sh`, the NERSC scripts), which is unchanged. `tests/test_project_config.py` does not enumerate them. |
| `mkdocs/mkdocs.yml` `watch: ../splash_links/src` | `mkdocs serve` live-reload path | no page autodocs `splash_links` (no `::: splash_links` directive); `mkdocs build --strict` never read it. |
| `.github/workflows/build-app.yml` Black check of `splash_links/scripts/import_kg.py` | CI formatting of one vendored file | formatting only; it made the main-branch CI depend on a file that is itself on the deletion list. The Black step still checks `app/modules/launchers` and `app/modules/f2w_agent/kg_update.py`. Not pinned by `tests/ci/test_workflows.py`. |

Nothing else qualified. Everything that looked dead is still referenced by a launcher, a test or a compose file and is
therefore class (c): in particular the `agent` Dockerfile stage is the build target of `compose.yaml` and
`docker-compose.yml` as well as `compose.splash.yaml`, and is pinned by `tests/images/test_images_dockerfile.py`;
`compose.splash.yaml` is read by `tests/test_docker_startup.py`, `tests/images/test_images_dockerfile.py` and
`scripts/test_compose.sh`; `kg_update.export_splash_graph_to_json` has no app caller but is exercised by
`tests/coverage_t27/test_kg_update_graphql.py` and belongs to the client family that leaves as one unit;
`CoordinatorConfig.splash_repo` is never read, but the `--splash-repo` flag that fills it is passed by four scripts
on the deletion list. `scripts/app/modules/kg_rag_api.py` is a stale pre-migration copy of the whole module (AUD-02,
legacy audit class (d)), not Splash-specific, and `tests/images/test_images_context.py` pins it as "tracked, never
shipped"; it is left for the legacy-audit deletion, not this one.

## 4. Class (c): the deletion list at cutover (H8)

Everything here runs only when an operator selects the legacy Splash mode (`F2W_KG_MODE=splash` with no Tiled Save
target, or `KG_RAG_GRAPH_SOURCE=splash`). None of the three supervised images (`local`, `als-dev`, `als-prod`) sets
either; `compose.yaml` and `docker-compose.yml` set `F2W_KG_MODE: json`; `scripts/start_agent_backend.sh` defaults to
`json`. No test starts a Splash process: `tests/test_splash_*.py` and `tests/coverage_t27/test_kg_update_graphql.py`
mock the HTTP layer, the contract harness installs an in-memory fake, and the closure launcher's default condition
is `absent` (a reserved port with nothing listening).

### 4.1 Python: the legacy Splash client and its callers

| File | What leaves | What stays |
|---|---|---|
| `app/modules/f2w_agent/kg_update.py` | `_splash_base_url`, `_splash_graphql`, `splash_uri_default`, `splash_find_entity_by_matkg_id`, `splash_update_entity`, `splash_create_entity`, `splash_create_link`, `splash_find_links`, `splash_delete_link`, `_splash_node_id`, `_splash_entity_to_node`, `load_splash_graph`, `export_splash_graph_to_json` (the GraphQL client over `urllib`) | `rebuild_kg`, `publish_extraction` (T18E) |
| `app/modules/kg_rag_api.py` | `SPLASH_LINKS_URI`, `SPLASH_LINKS_PAGE_SIZE`, `_splash_base_url`, `_splash_graphql`, `_SPLASH_ENTITIES_QUERY`, `_SPLASH_LINKS_QUERY`, `_splash_node_id`, `_splash_entity_to_node`, `_load_splash_links_graph`, the `splash`/`splash_links`/`splash-links` branch with `json_fallback`, CLI flags `--splash-uri`, `--splash-page-size` | the `graph_source` enum values themselves (class (a)): after cutover the three spellings must still be accepted and resolve to the JSON/Tiled path, never raise |
| `app/modules/f2w_agent/api.py` | `_sync_session_graph_from_splash` body that calls `kg_update.load_splash_graph`; the `_splash` property's `kg_update` branch (returning the stand-in unconditionally); the direct `self._splash.splash_*` call sites inside `update_node_properties`, `_apply_relationship_updates`, `_apply_snippet_updates` once only the Tiled path remains | tokens, phases, error codes, `_NoSplash` semantics (class (a)); `TiledSaveBackend` routing |
| `app/modules/f2w_agent/coordinator.py` | `kg_mode="splash"` seeding rule (copy session graph only if missing) and the two `splash reimport` log blocks (already Tiled publication under the old name), `splash_repo` field | `kg_mode` itself until the enum is retired with the wire vocabulary |
| `app/modules/f2w_agent/cli.py` | `--splash-repo`; `--kg-mode` choice `splash` only together with the scripts below | |
| `scripts/app/modules/kg_rag_api.py` | the whole stale copy (legacy audit (d), AUD-02) | - |

Tests that leave with the client: `tests/test_splash_helpers.py`, `tests/test_splash_fallback.py`,
`tests/coverage_t27/test_kg_update_graphql.py`, `tests/test_kg_update.py` (Splash parts), the Splash counters in
`tests/test_coordinator_output.py`, `tests/test_f2w_api.py` (Splash-path cases), `tests/api_wiring/test_wiring_tiled_save.py`
(the "legacy path" contrast cases), the `fake` condition and `SplashSwitch` in
`tests/contracts/legacy_ui/closure/launch_full_backend.py`, the Splash write counters in `tests/e2e/live_backend/live.ts`.
The frozen fixtures under `tests/contracts/legacy_ui/fixtures/` stay (class (a)). Stale JUnit artifacts
`tests/results/test_splash_helpers.xml`, `tests/results/test_splash_fallback.xml` go with their tests.

### 4.2 Image, compose, CI, ignore files

| File | What leaves | Notes |
|---|---|---|
| `Dockerfile` | stage `pixi` (`ghcr.io/prefix-dev/pixi:0.70.2`), and in stage `agent`: `COPY --from=pixi`, `COPY splash_links ./splash_links`, `pixi install --manifest-path splash_links/pixi.toml --locked`, `ENV SPLASH_LINKS_REPO`, the Splash comments | the `agent` stage itself is the `compose.yaml` developer target and may stay without Splash; `tests/images/test_images_dockerfile.py::test_splash_stays_only_in_the_legacy_agent_target` must be inverted to "no stage mentions Splash" |
| `compose.splash.yaml` | whole file (services `splash-db-init`, `splash`, Splash env on `agent`, volume `splash-data`) | `tests/test_docker_startup.py`, `tests/images/test_images_dockerfile.py::test_compose_*`, `scripts/test_compose.sh` and the docs below reference it |
| `.dockerignore` | `splash_links/links.sqlite` line (and `scripts/app` when the stale copy goes) | `tests/images/test_images_context.py` lists both paths as "tracked, never shipped" |
| `docker/profiles/README.md` | the `agent` row's Splash text and the pixi 0.70.2 note | |
| `.github/workflows/build-app.yml` | nothing left after (b) | `splash_links/.github/workflows/*` are inside the vendored tree and never run here |
| `pyproject.toml`, `pixi.toml` | nothing: no Splash dependency exists (pinned) | |

### 4.3 Scripts and environment names

| File | Splash role |
|---|---|
| `scripts/start_all.sh` | starts `pixi run serve` in `splash_links`, waits on `/splash_links/health`, then backend and UI (the legacy three-process developer stack) |
| `scripts/start_agent_backend.sh` | `F2W_KG_MODE=splash` branch: checks `SPLASH_LINKS_REPO`, probes `F2W_SPLASH_HEALTH_URL`, passes `--splash-repo`; default is `json` |
| `scripts/install_pixi.sh` | installs pixi and the `splash_links` environment (the CI installer `scripts/ci/install_pixi.sh` is unrelated and stays) |
| `scripts/test_compose.sh` | smoke-tests `compose.splash.yaml` (legacy audit (d)) |
| `scripts/f2w_nersc_api.sh`, `scripts/f2w_nersc_smoke.sh`, `scripts/run_nersc_3agent.sh` | default `F2W_KG_MODE=splash` and `SPLASH_LINKS_REPO=/pscratch/sd/m/mateo/splash_links` (Q7) |
| `scripts/.env.example` | `KG_RAG_GRAPH_SOURCE=splash`, `KG_RAG_SPLASH_URI`, `SPLASH_LINKS_REPO`, `SPLASH_LINKS_DB`, `KG_RAG_SPLASH_PAGE_SIZE` |
| `config.yml` | `kg_rag.retrieval.graph_source` default `splash` (Q5), `kg_rag.retrieval.splash_uri`, `kg_rag.retrieval.splash_page_size`, and the `env:` block's `KG_RAG_GRAPH_SOURCE: splash`, `KG_RAG_SPLASH_URI` |

Environment names to retire (reads in code and scripts, outside docs and tests): `F2W_KG_MODE` (value `splash`),
`F2W_SPLASH_HEALTH_URL`, `KG_RAG_GRAPH_SOURCE` (value `splash`), `KG_RAG_SPLASH_URI`, `SPLASH_LINKS_URI`,
`KG_RAG_SPLASH_PAGE_SIZE`, `SPLASH_LINKS_REPO`, `SPLASH_LINKS_DB`. The contract harness also clears `SPLASH_URI`.
Per plan 5.2 none of these values may be reinterpreted as a Tiled address.

### 4.4 Documentation

`README.md` (83 mentions: the `.env` block "Legacy Splash mode only", step 4 "(Legacy) Import into splash_links"
4a-4g, the launcher table), and 32 MkDocs pages: `mkdocs/docs/splash-links.md` (whole page, already marked legacy;
remove from the nav in `mkdocs/mkdocs.yml`), `graphql-cookbook.md`, `legacy.md`, `repository-reference.md`,
`tutorials.md`, `operations.md`, `deployment.md`, `architecture.md`, `knowledge-graph.md`, `getting-started.md`,
`release.md`, `security.md`, `contributing.md`, `configuration.md`, `scripts.md`, `agent-api.md`, `ui-api.md`,
`testing.md`, `index.md`, `current-architecture.md`, `ui-user-guide.md`, `ui-development.md`, `performance.md`,
`provenance.md`, `core_model.md`, `ui-architecture.md`, `frontend.md`, `about.md`, `ui-knowledge-graph.md`,
`retrieval.md`, `nersc.md`, `agent-workflow.md`. `tests/docs/test_doc_consistency.py` already forbids presenting
Splash as the default. Migration records under `docs/migration/` (36 Markdown, 22 JSON files) are history and stay.

### 4.5 The vendored service

`splash_links/` (65 tracked files, 24 MB, of which `links.sqlite` is 24.5 MB): `src/splash_links/` (FastAPI +
Strawberry GraphQL service, SQLAlchemy store, Alembic migrations, Typer CLI, a Tiled client), `frontend/` (its own
Vite app, including a stray `App.jsx.new`), `_tests/` (4 files, collected only by its own `pyproject.toml`
`testpaths = ["_tests"]`, never by the root `pytest.ini` `testpaths = tests`), `scripts/import_kg.py`,
`seed_db.py`, `update_readme_tree.py`, `examples/`, `mkdocs/`, `Containerfile`, `docker-compose.yml`, `pixi.toml`,
`pixi.lock`, `.github/workflows/`, `LICENSE`, `README.md`, `Notes.md`. Last substantive change 2026-08 (`98254b8`),
docs 2026-09 (`07b628e`). Its fate is Q2-Q4.

## 5. Class (d): decisions the owner must take

| # | Question | Options | Recommendation (not a decision) |
|---|---|---|---|
| Q1 | Compatibility window: how long after the production cutover do the Splash client (4.1), `compose.splash.yaml` and the `agent` Splash layers stay buildable? | (i) one canary cycle, (ii) a fixed calendar span, (iii) until the first post-cutover release tag | (i): keep import/API wrappers through the canary as the plan says, delete at the first green soak report; the wire vocabulary (class (a)) is not part of the window and stays indefinitely |
| Q2 | Does `splash_links/` leave the repository entirely or become an archived tag? | (i) `git rm -r` at cutover with a tag `splash_links-archive-<date>` on the last commit that has it, (ii) keep the tree read-only with a top-level deprecation README, (iii) move to its own archive repository | (i): the upstream project lives at its own ALS repository; the vendored copy exists only for the `agent` target and `start_all.sh` |
| Q3 | `splash_links/_tests/` | delete with the tree (they test the service, not F2W) or port anything F2W-specific first | delete with the tree; nothing in them imports `app` |
| Q4 | `splash_links/links.sqlite` (24.5 MB tracked binary seed) | delete with the tree; or export once to versioned MatKG JSON under `storage/kg/` as a legacy snapshot | audit per D16 before deletion ("Audit supplied legacy exports only if found"): confirm its content is already represented by a `storage/kg/matkg_*.json` version, record the result here, then delete; the `.dockerignore` line goes with it |
| Q5 | `config.yml` `kg_rag.retrieval.graph_source` default `splash` and `scripts/.env.example` `KG_RAG_GRAPH_SOURCE=splash` | flip the public default to `json` now, or at cutover | at cutover, as one change with 4.3; plan T05 forbids changing public defaults while documenting the adapter, and every supported launcher already overrides it to `json` |
| Q6 | UI text "Load the live knowledge graph from splash_links (requires a running splash server on :8081)." in `ui/src/app/components/AppSettingsButton.tsx` | leave (frozen UI), or change the description text only (the `splash_links` id/label stay) | change the description at cutover to name the Tiled literature graph; a text-only change, no token, no layout; record as a reviewed UI deviation |
| Q7 | NERSC scripts default to `F2W_KG_MODE=splash` and a personal `/pscratch/.../splash_links` path | flip to `json`, or retire the scripts | owner knows whether these NERSC runs still happen; if yes, flip the default to `json` at cutover and drop the path |
| Q8 | Enum retirement after the window: may `kg_rag_api` and `--kg-mode` stop accepting `splash` on *new* interfaces (`/v1`, CLI) while the frozen UI routes keep it? | yes with a typed capability error / no | yes on new interfaces only; the frozen routes keep `^(splash\|json)$` for as long as the browser snapshot is supported |
| Q9 | The ADR's proposed additive `graph_backend.storage_backend` diagnostic on `/settings` and `/health` | implement before cutover (OpenAPI additive diff) / drop the proposal | implement before cutover so operators can see `tiled` while the enum still says `splash`; additive and ignored by the frozen client |

## 6. Proposed compatibility window (for approval)

1. **Start:** the production cutover approved at H8 (first supervised image serving the UI with Tiled publication).
2. **During the window:** the legacy wire vocabulary (section 2) works unchanged; the Splash client code (4.1), the
   `agent` target's Splash layers, `compose.splash.yaml` and the scripts (4.3) stay in the tree and keep building,
   but nothing is required to run a Splash process and no supervised image, CI job or test starts one (that is the
   state today, pinned by the new test).
3. **End:** the first green canary/soak report after cutover (Q1 (i)), or the owner's chosen alternative.
4. **After the window:** sections 4.1-4.5 are deleted in one commit per sub-section, in this order: 4.5 (with the
   tag of Q2), 4.2, 4.3, 4.1, 4.4; the inverted image test and the updated inventory test are in the same change.
   Class (a) is untouched by every one of these commits.

## 7. Proposed deletion list (for approval)

In the order of section 6 step 4; each line is one owner tick.

- [ ] `splash_links/` entire tree (65 files) after the Q4 audit of `links.sqlite`; tag per Q2
- [ ] `Dockerfile`: stage `pixi`; Splash lines of stage `agent` (keep the stage as the compose developer target)
- [ ] `compose.splash.yaml`; `scripts/test_compose.sh`; the `compose.splash.yaml` cases in `tests/test_docker_startup.py` and `tests/images/test_images_dockerfile.py`
- [ ] `.dockerignore` `splash_links/links.sqlite`; `docker/profiles/README.md` Splash text
- [ ] `scripts/start_all.sh`, `scripts/install_pixi.sh`; Splash branch of `scripts/start_agent_backend.sh`; Splash defaults of the three NERSC scripts (Q7); Splash lines of `scripts/.env.example`
- [ ] `config.yml` `kg_rag.retrieval.splash_uri`, `kg_rag.retrieval.splash_page_size`, `env.KG_RAG_SPLASH_URI`; `graph_source` default to `json` (Q5)
- [ ] `app/modules/f2w_agent/kg_update.py` Splash client functions; `app/modules/kg_rag_api.py` Splash loader, constants and CLI flags; `app/modules/f2w_agent/api.py` `_sync_session_graph_from_splash` body and the `kg_update` branch of `_splash`; `coordinator.py` `splash_repo` and the Splash seeding rule; `cli.py` `--splash-repo`
- [ ] `scripts/app/modules/kg_rag_api.py` (with the legacy-audit (d) batch) and its pins in `tests/images/test_images_context.py`
- [ ] the tests named in 4.1 and the two stale JUnit files
- [ ] `README.md` Splash sections; `mkdocs/docs/splash-links.md` and its nav entry; Splash passages of the 31 other pages
- [ ] UI description text (Q6); `graph_backend.storage_backend` diagnostic (Q9) if approved

Never on the list: `agentSettings.ts` tokens, `liveAgent.ts` types, the `^(splash|json)$` patterns, the SSE phase
names, the `splash_error` status, the 400/502 codes, `routes_capabilities.py`, `legacy_save.py`, `_NoSplash`, the
frozen fixtures under `tests/contracts/legacy_ui/fixtures/`.

## 8. Verification of this packet

From a clean `git archive` export (never inside the repository directory):

- `tests/contracts/legacy_ui` (incl. the new `test_splash_retirement.py`): green, unchanged frozen fixtures.
- full not-slow suite: see the branch's final report for the count.
- `mypy` (strict, `pyproject.toml`), `ruff check app`, `isort --check-only app`: clean.
- `python -m evals.binary.import_companion --check`: up to date.
- The Dockerfile was not changed; `tests/images` were run anyway because the new test reads it.

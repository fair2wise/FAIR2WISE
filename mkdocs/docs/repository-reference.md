# Repository reference

This page maps maintained source and generated/artifact directories to their
responsibilities. It is intended as the starting point for code ownership and
change-impact analysis.

## Repository root

| Path | Responsibility |
|---|---|
| `README.md` | User-oriented project overview and operational examples |
| `config.yml` | Central non-secret defaults and environment mappings |
| `.env.example` | Local environment template |
| `requirements.in` / `requirements.txt` | Runtime dependency source and compiled Python 3.12 lock |
| `requirements-dev.in` / `requirements-dev.txt` | Development/tooling source and compiled lock |
| `requirements-globus.*` | Optional Globus Compute dependency source and lock |
| `requirements-semantic.*` | Optional FAISS/SentenceTransformer/Torch dependency source and lock |
| `run.py` | Local modular extractor CLI |
| `compose.yaml` | Canonical private-network application stack |
| `Dockerfile` | Agent and frontend image targets |
| `pytest.ini` | Root pytest discovery configuration |
| `mkdocs/` | This documentation site |

## Launcher package

`app/modules/launchers/`

| File | Responsibility |
|---|---|
| `f2w_agent.py` | Module entry point for the orchestrated CLI and Agent API |
| `user_agent.py` | Local Academy user agent and monitoring dashboard launcher |
| `academy_extractor.py` | Academy/Globus remote extraction client |
| `academy_auth.py` | Academy Globus authentication/token-cache helper |
| `__init__.py` | Launcher package marker |

Run these entry points from the repository root with `python3 -m`, for example
`python3 -m app.modules.launchers.f2w_agent status`.

## Agent workflow package

`app/modules/f2w_agent/`

| File | Responsibility |
|---|---|
| `api.py` | React-facing FastAPI models/routes and persistent `AgentPipelineService` |
| `cli.py` | Parser and `status`/`ask`/`chat`/`api` dispatch |
| `coordinator.py` | Configuration, session artifacts, CLI adapter, retained low-level loops |
| `orchestrator_agent.py` | Turn routing, LLM decision, deterministic validation/fallback |
| `retrieval_agent.py` | KG search, evidence test, context, strict sufficiency judge |
| `download_agent.py` | OpenAlex/arXiv search, ranking, URL resolution, PDF download/validation |
| `extractor_agent.py` | Async Academy wrapper around local full/targeted extraction |
| `debate_agent.py` | Evidence-gating decision and heuristic fallback |
| `paper_evidence_agent.py` | Page-bounded answers over active downloaded PDFs |
| `kg_update.py` | Terms-to-KG rebuild, Splash wipe/import/export/load |
| `session_memory.py` | Topic-aware durable conversational memory and compression |
| `workflow_state.py` | Atomic durable workflow/pending state |
| `__init__.py` | Package exports |

`api.py` is deliberately large because it owns the stateful orchestration
boundary and the wire contract. New endpoint-independent logic should normally
move into a focused module rather than expanding route handlers.

## Retrieval and conversion

| Path | Responsibility |
|---|---|
| `app/modules/kg_rag_api.py` | KG loader/search/ranking/context, LLM clients, compatibility API/CLI |
| `app/modules/json2kg.py` | Extracted term/code JSON to MatKG graph |
| `app/modules/project_config.py` | Cached YAML/env configuration helpers |
| `app/modules/cborg_limiter.py` | Shared synchronous/asynchronous CBORG concurrency cap |
| `app/modules/extract_terms.py` | Standalone schema-aware extraction pipeline |
| `app/run_pipeline_cborg.py` | Incremental model/checkpoint evaluation |

`scripts/app/modules/kg_rag_api.py` is a copied compatibility version. The
canonical maintained implementation is `app/modules/kg_rag_api.py`; avoid
editing only the script copy.

## Modular term extractor

`app/modules/term_extractor/`

| File | Responsibility |
|---|---|
| `orchestrator.py` | Processing lifecycle and full/targeted modes |
| `agent.py` | LangGraph agent/tool loop |
| `tools.py` | Schema/term/formula/ChEBI tools |
| `store.py` | Thread-safe cumulative term storage |
| `models.py` | Extraction dataclasses and serialization |
| `schema.py` | LinkML schema lookup, mapping, relation checks |
| `prompts.py` | Extraction instructions |
| `clients.py` | CBORG and Ollama adapters |
| `services.py` | Construct optional chemistry/property services |
| `provenance.py` | Publication and page-code provenance |
| `source_repos.py` | GitHub repository and source block extraction |
| `academy_agent.py` | Remote monitored extractor agent |
| `monitored_agent.py` | Academy lifecycle, telemetry, prompt bridge |
| `user_agent.py` | Dashboard-facing Academy user agent |
| `dashboard.py` | Flask/SSE monitoring dashboard and embedded UI |
| `message.py` | Academy registration/log/stats/prompt message models |
| `__init__.py` | Public extractor exports |

## Domain helpers

`app/modules/agents/`

| File | Responsibility |
|---|---|
| `chebi.py` | Load/query ChEBI OBO terms and mappings |
| `chem_checker.py` | Materials Project composition/chemistry checks with failure disabling |
| `properties.py` | Physical-property extraction and normalization |

These helpers are optional enrichment services; extraction must degrade
gracefully when their external datasets/APIs are unavailable.

## React application

`ui/src/`

| Path | Responsibility |
|---|---|
| `main.tsx` | React root |
| `app/App.tsx` | Application shell and top-level session/graph state |
| `app/components/ChatSidebar.tsx` | Current live conversation UI |
| `app/components/GraphMockup.tsx` | Current graph visualization/editor |
| `app/components/GraphCanvas.tsx` | Earlier canvas/mock-data graph renderer |
| `app/components/KGInfoPanel.tsx` | Hover details |
| `app/components/data/liveAgent.ts` | Agent API wire types/client |
| `app/components/chatSessions.ts` | Browser session storage |
| `app/components/agentSettings.ts` | Settings storage/normalization |
| `app/components/agentApiErrors.ts` | User-facing API diagnostics |
| `app/components/kgCitations.ts` | Parse and map `[KG: ...]` citations |
| `app/components/kgNodeColors.ts` | Schema categories and visual colors |
| `app/components/publicationLinks.ts` | DOI/arXiv/source URL resolution |
| `app/components/publicationFavorites.ts` | Saved publication state |
| `app/components/PublicationList.tsx` | Publication display |
| `app/components/PublicationFavoriteButton.tsx` | Bookmark interaction |
| `app/components/CodeBlock.tsx` | Code rendering |
| `app/components/AsciiOrb.tsx` | Empty/loading visual |
| `app/components/AppErrorMessage.tsx` | Shared destructive alert for failed agent runs |
| `app/components/AppNewChatButton.tsx` | New-session header action |
| `app/components/AppBookmarksButton.tsx` | Saved-publication header action |
| `app/components/App*Button.tsx` | Header actions for chats, papers, bookmarks, settings |
| `app/components/ui/` | Shared Radix/shadcn-style primitives |
| `app/components/figma/ImageWithFallback.tsx` | Figma image fallback compatibility helper |
| `app/components/data/mock*.ts` | Prototype fixtures/mock RAG |
| `app/index.ts`, `app/components/index.ts` | Public UI re-export surfaces |
| `styles/index.css` | Style entry point |
| `styles/globals.css`, `theme.css` | Global element rules and design tokens |
| `styles/fonts.css`, `tailwind.css` | Font and Tailwind layers |
| `vite.config.ts` | React/Tailwind/Figma-asset Vite plugins and `@` alias |

Tests sit beside UI helpers as `*.test.ts`.

## Splash Links workspace

| Path | Responsibility |
|---|---|
| `splash_links/src/splash_links/app.py` | FastAPI factory and embedding REST |
| `splash_links/src/splash_links/schema.py` | GraphQL schema |
| `splash_links/src/splash_links/store.py` | SQL model/store implementation |
| `splash_links/src/splash_links/main.py` | Service import target |
| `splash_links/src/splash_links/cli.py` | Local database CLI |
| `splash_links/src/splash_links/client/base.py` | Typed service client |
| `splash_links/src/splash_links/client/cli.py` | Remote client CLI |
| `splash_links/src/splash_links/client/tiled.py` | Tiled integration |
| `splash_links/alembic/` | SQL migrations |
| `splash_links/scripts/import_kg.py` | MatKG importer |
| `splash_links/scripts/seed_db.py` | Compose volume initializer for the tracked SQLite seed |
| `splash_links/links.sqlite` | Immutable graph seed used by root Compose deployment |
| `splash_links/frontend/` | Optional standalone Splash React/Vite frontend |
| `splash_links/pixi.toml` / `pixi.lock` | Reproducible environment/tasks |
| `splash_links/pyproject.toml` | Python package metadata |
| `splash_links/Containerfile` | Standalone Splash container |
| `splash_links/docker-compose.yml` | Splash + PostgreSQL stack |
| `splash_links/_tests/` | Service, store, CLI, client, and Tiled tests |
| `splash_links/mkdocs/` | Upstream Splash-local template docs; root docs are canonical for this repo |

`splash_links/links.sqlite` is the tracked Compose seed. Other SQLite files,
`.pixi/`, coverage, and cache files are local artifacts rather than source.

## Schema and data

| Directory | Contents |
|---|---|
| `storage/schema/` | LinkML MatKG schema |
| `storage/terminology/` | Extracted term datasets/checkpoints |
| `storage/kg/` | MatKG graph snapshots/checkpoints and HTML viewer |
| `storage/competency_questions/` | Evaluation question sets |
| `storage/knowledge_gaps/` | Missing-node event logs |
| `storage/ontologies/` | Optional ontology files such as ChEBI |
| `papers/` | Unified input publication collection |
| `runs/` | Mutable per-session working data |

Large JSON/PDF artifacts should not be read as executable source. Their schemas
and lifecycle are documented in [Knowledge graph](knowledge-graph.md) and
[Terminology extraction](term-extraction.md).

## Tests

Root tests follow feature ownership:

- `test_f2w_api.py` covers the integrated service contract and workflow;
- `test_workflow_orchestrator.py`, `test_*_agent.py`, and
  `test_f2w_coordinator.py` cover individual agent/state boundaries;
- `test_extract_terms*.py`, `test_extractor_agent.py`, and
  `test_source_repos.py` cover extraction;
- `test_json2kg*.py`, `test_kg_rag_api*.py`, and
  `test_build_context_filtering.py` cover graph construction/retrieval;
- `test_splash_*.py`, `test_kg_update.py`, and
  `test_wipe_splash_db_script.py` cover graph-service integration/safety;
- `test_project_config.py`, `test_cborg_limiter.py`, and helper tests cover
  infrastructure;
- `tests/deepeval/` contains opt-in LLM quality evaluations.

## Build and automation

| Path | Responsibility |
|---|---|
| `.github/workflows/build-app.yml` | Python format/lint/test and MkDocs deployment |
| `.github/workflows/publish-image.yml` | Build/publish the agent image target |
| `.github/dependabot.yml` | Docker, Actions, and pip update groups |
| `.pre-commit-config.yaml` | Local pre-commit tools |
| `scripts/` | Local, Docker, KG, and NERSC automation |
| `slurm_scripts/` | Batch-system launchers |

## Tooling directories

`.agents/`, `.codex/`, and `skills-lock.json` configure local coding-agent
tools. They are excluded from runtime images and remote deployment and are not
part of FAIR2WISE application behavior.

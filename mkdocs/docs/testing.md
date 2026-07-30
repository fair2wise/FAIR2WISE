# Testing

The repository has three test surfaces: root Python tests, Splash Links tests,
and frontend Vitest tests.

## Root Python suite

```bash
python3 -m pytest
```

`pytest.ini` sets the repository on `pythonpath` and limits default discovery to
`tests/`, so the vendored Splash suite does not run implicitly.

Major coverage areas:

| Area | Representative test files |
|---|---|
| Agent API and workflow | `test_f2w_api.py`, `test_workflow_orchestrator.py`, `test_f2w_coordinator.py` |
| Download/extraction | `test_download_agent.py`, `test_extractor_agent.py`, `test_extract_terms*.py` |
| KG conversion/retrieval | `test_json2kg*.py`, `test_kg_rag_api*.py`, `test_build_context_filtering.py` |
| Splash integration | `test_splash_helpers.py`, `test_splash_fallback.py`, `test_kg_update.py` |
| Settings/sessions | `test_agent_settings.py`, session cases in `test_f2w_api.py` |
| Utilities/safety | `test_project_config.py`, `test_cborg_limiter.py`, `test_wipe_splash_db_script.py` |
| Compatibility API | `test_fastapi_endpoints.py`, `test_chat_clients.py` |

Tests use temporary files and monkeypatched HTTP/LLM clients to keep the
deterministic suite offline.

## LLM quality tests

`tests/deepeval/test_llm_quality.py` contains higher-cost evaluation cases for:

- answer relevancy;
- faithfulness;
- retrieval context relevancy;
- hallucination;
- RAG versus baseline;
- citations;
- multi-turn behavior;
- context budgets; and
- sparse graphs.

Run these deliberately with the required model credentials and services:

```bash
python3 -m pytest tests/deepeval
```

Do not interpret a deterministic unit-test pass as an LLM quality result.

## Splash Links suite

```bash
cd splash_links
pixi run test
```

It tests:

- SQL store CRUD and cascade behavior;
- GraphQL queries/mutations;
- embedding REST and nearest-neighbor search;
- local and remote CLIs;
- typed client serialization/error handling; and
- Tiled integration helpers.

The Pixi task enforces its configured coverage threshold.

## Frontend suite

```bash
cd ui
npm test
```

Frontend tests cover:

- graph subset selection and relationship helpers;
- session persistence/migration/search;
- settings normalization and API conversion;
- API error messages;
- citation parsing;
- node-category colors;
- publication links; and
- live API type/helper behavior.

Production compilation is a separate check:

```bash
npm run build
```

## Documentation checks

```bash
mkdocs build -f mkdocs/mkdocs.yml --strict
```

Strict mode catches missing navigation pages, unresolved internal links,
duplicate anchors, and plugin warnings.

## Recommended pre-change matrix

| Change | Minimum checks |
|---|---|
| Agent workflow/API | Targeted root tests, then full root suite |
| Extraction/schema/KG | Extraction + JSON2KG + retrieval tests |
| Splash store/schema/client | Splash suite and root Splash integration tests |
| UI behavior | `npm test` and `npm run build` |
| Shell scripts | `bash -n scripts/name.sh` and related pytest cases |
| Documentation | Strict MkDocs build |
| Dockerfile | `docker build --check .`, then a full build when daemon/network are available |

External downloads, CBORG, OpenAlex, GitHub, NERSC, Docker registries, and
Globus should remain mocked in deterministic CI unless a job is explicitly an
integration test.

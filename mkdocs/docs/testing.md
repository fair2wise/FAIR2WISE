# Testing

The repository has three test surfaces: root Python tests, Splash Links tests,
and frontend Vitest tests.

## Root Python suite

```bash
python3 -m pytest
```

Install the compiled development environment with
`python3.12 -m pip install -r requirements/dev.txt`. Runtime containers install
the smaller lock through the root `requirements.txt` compatibility file.

Regenerate locks after editing an `.in` source file:

```bash
python3.12 -m piptools compile --strip-extras -o requirements/runtime.txt requirements/runtime.in
python3.12 -m piptools compile --strip-extras -o requirements/dev.txt requirements/dev.in
python3.12 -m piptools compile --strip-extras -o requirements/globus.txt requirements/globus.in
python3.12 -m piptools compile --strip-extras -o requirements/legacy.txt requirements/legacy.in
```

Run `python3.12 -m pip check` after installation.

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

## Docker Compose smoke test

With Docker running and `CBORG_API_KEY` exported:

```bash
./scripts/test_compose.sh
```

The script uses an isolated Compose project and temporary named volumes. It
builds and starts the stack, checks the UI and proxied API, confirms the seed
graph is populated, rejects published agent/Splash ports, recreates the stack
to verify persistence, and removes only its own test state.

For fresh-computer acceptance, install Docker and export the key on the clean
machine, then run only clone, `cd`, and `docker compose up`. Confirm a real chat
request succeeds and ports 8081/8090 are not reachable from the host.

## Recommended pre-change matrix

| Change | Minimum checks |
|---|---|
| Agent workflow/API | Targeted root tests, then full root suite |
| Extraction/schema/KG | Extraction + JSON2KG + retrieval tests |
| Splash store/schema/client | Splash suite and root Splash integration tests |
| UI behavior | `npm test` and `npm run build` |
| Shell scripts | `bash -n scripts/name.sh` and related pytest cases |
| Documentation | Strict MkDocs build |
| Compose/container changes | `./scripts/test_compose.sh` and strict Compose configuration validation |

External downloads, CBORG, OpenAlex, GitHub, NERSC, Docker registries, and
Globus should remain mocked in deterministic CI unless a job is explicitly an
integration test.

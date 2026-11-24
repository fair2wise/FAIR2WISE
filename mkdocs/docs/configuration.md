# Configuration

## Configuration sources

`config.yml` contains non-secret defaults. `.env` contains local overrides and
credentials. `.env.example` lists the supported common variables without real
secrets.

The helper `config_value("section.key", fallback, cast=...)` checks environment
names declared in `config.yml`, then configured defaults. `secret_env(...)`
only reads the named environment variables and intentionally ignores YAML
values.

Set `FAIR2WISE_CONFIG` to load a different YAML file.

## Credentials and external services

| Variable | Required when | Purpose |
|---|---|---|
| `CBORG_API_KEY` | Using CBORG | Hosted LLM authentication |
| `CBORG_BASE_URL` | Optional | CBORG-compatible base URL |
| `OPENALEX_EMAIL` | Recommended | OpenAlex polite-pool identity and Unpaywall lookup |
| `OPENALEX_API_KEY` | Optional | OpenAlex key, `Authorization: Bearer` header only; process environment only (never read from `.env`) |
| `MP_API_KEY` | Optional | Materials Project chemistry validation |
| `GITHUB_TOKEN` | Optional | Higher-rate GitHub source-code retrieval |
| `GLOBUS_COMPUTE_ENDPOINT_ID` | Remote extraction | Globus Compute endpoint |
| `GLOBUS_PROJECT_ID` | Remote extraction | Globus project identity |
| `ACADEMY_GLOBUS_CLIENT_ID` | Service auth only | Academy Globus client |
| `ACADEMY_GLOBUS_CLIENT_SECRET` | Service auth only | Academy Globus secret |

Never put secret values in `config.yml`.

Compose requires `CBORG_API_KEY` in the host `.env` (`env_file:`). Do not ARG
or `COPY .env` into the image. Leave `CBORG_IPV6_BIND` unset. The host
environment takes precedence over `.env` for interpolation only; secrets are
injected at container start.

## Service paths

| Variable | Default | Meaning |
|---|---|---|
| `SPLASH_LINKS_REPO` | `splash_links` | Vendored Splash workspace |
| `SPLASH_LINKS_DB` | `links.sqlite` | SQLite path or SQLAlchemy PostgreSQL URL |
| `KG_RAG_SPLASH_URI` | `splash://localhost:8081` | Splash client URI |
| `KG_RAG_GRAPH` | configured JSON under `storage/kg` | JSON graph and fallback |
| `PYSTOW_HOME` | `.cache/pystow` | Repository-local PyStow cache |
| `F2W_WORKDIR` | `runs/ui_session_splash` | Agent runtime files |

Relative Splash database paths are resolved from the `splash_links` working
directory by the standard launcher.

## Agent runtime

| Variable | Common default | Meaning |
|---|---:|---|
| `F2W_BACKEND` | `cborg` | `cborg` or `ollama` |
| `F2W_MODEL` | `lbl/cborg-chat` | Active LLM model |
| `CBORG_IP_FAMILY` | `ipv6` in Compose | CBORG transport family (`ipv6`, `ipv4`, or `auto`) |
| `CBORG_FORCE_IPV6` | `1` in Compose | Pin the HTTP client to IPv6 |
| `CBORG_IPV6_BIND` | unset (probe) | Optional source address; ignored if not assigned on the host |
| `F2W_KG_MODE` | `json` | `json` (default of the CLI, the start scripts, Compose and the images) or the legacy editable Splash graph (`splash`) |
| `F2W_RUNTIME` | `legacy` | `legacy` or `langgraph` (scoped LangGraph runtime); any other value is a startup error |
| `F2W_QA_ROUTE` | `supervisor` | In `langgraph` mode: `supervisor` (planning supervisor) or `direct` |
| `F2W_DEPLOYMENT_PROFILE` | `local` | `local`, `als-dev`, `als-prod`; fixed by the image profile (`docker/profiles/*.json`) |
| `F2W_GRAPH` | `storage/kg/matkg_with_code.json` | Comma-separated JSON graphs. Local `start_rsoxs_stack.sh` defaults to highest `matkg_rsoxs_vN.json` + highest `matkg_bl1101_vN.json`. Compose pins its own pair. |
| `F2W_SEED_TERMS` | empty | Cumulative terms seed |
| `F2W_MAX_ROUNDS` | `3` | Evidence-growth rounds |
| `F2W_MAX_PAPERS` | `1` in UI launcher | Maximum papers per round |
| `F2W_CANDIDATE_POOL` | `25` | Literature candidates searched |
| `F2W_DOWNLOAD_DELAY` | `0` in UI launcher | Delay between download attempts |
| `F2W_WORKERS` | `8` | Page extraction workers |
| `F2W_JOB_LOG_RETENTION_DAYS` | `30` | Worker only: per-job log files in `<jobs-db-dir>/job-logs/` older than this many days are deleted (`0` turns the age rule off). See [Job log files](operations.md#job-log-files) |
| `F2W_JOB_LOG_MAX_MIB` | `500` | Worker only: the job log directory is kept within this many MiB by deleting the oldest files first (`0` turns the size rule off) |
| `F2W_WORKFLOW_MODE` | `agentic` | `agentic` or `deterministic` |
| `F2W_EXTRACTION_MODE` | `targeted` | `targeted` or `full` |
| `F2W_TARGETED_MAX_PAGES` | `6` | Per-PDF targeted page cap |
| `F2W_DISCLOSE_SNIPPETS` | off | Set to `1` (also `true`, `yes`, `on`) to show bounded verbatim supporting passages on the node card (`GET /graph/node/{id}`: at most 20 per paper, 1,200 characters each, 6,000 characters in total per paper with whole passages dropped once the cap would be exceeded, 100 per node). Off: the card shows only the passage count. Owner opt-in (2026-11-23) for single-operator deployments only; leave it off on any multi-user or shared deployment. Read from the process environment at each response. |

## Retrieval

| Variable | Default/typical | Meaning |
|---|---:|---|
| `KG_RAG_GRAPH_SOURCE` | `json` everywhere (Compose, the start scripts, the images, `config.yml` and `scripts/.env.example`; owner decision 2026-11-09, Splash packet Q5) | `json` or the legacy editable `splash` |
| `TILED_URI` | `http://127.0.0.1:8000` | Live Tiled catalog base (no `/api/graphql` suffix). ALS/11.0.1.2 uses the beamline Tiled host. |
| `TILED_API_KEY` | unset | Tiled `Authorization: Apikey …` (or `TILED_API_KEY_SCHEME`). Never commit. Unsigned GraphQL returns empty lists. |
| `F2W_LIVE_TILED` | unset (on if `TILED_URI` is set) | Chat ESAF/proposal/sample/scan lookup via Tiled GraphQL. JSON sim is fallback. |
| `KG_RAG_RETRIEVAL_BACKEND` | `lexical` | Search implementation (`lexical` or `semantic`; semantic packages ship in the default install) |
| `KG_RAG_TOPK` | `12` | Final retrieval count |
| `KG_RAG_EMBED_MODEL` | `all-MiniLM-L6-v2` | SentenceTransformer model |
| `KG_RAG_FORCE_CPU` | false | Disable GPU FAISS use |
| `KG_RAG_BATCH` | device-dependent | Embedding batch size |
| `KG_RAG_ENABLE_BFS` | `1` | Enable graph expansion |
| `KG_RAG_MAX_HOPS` | `1` | BFS depth |
| `KG_RAG_STEPWISE` | `1` | Enable sub-question expansion |
| `KG_RAG_STEPWISE_MAX_STEPS` | `6` | Expansion cap |
| `KG_RAG_CTX_CHARS` | `16000` in config | Context character budget |
| `KG_RAG_STRUCT_CTX` | `1` | Include structured node/edge facts |
| `KG_RAG_GENERIC_PENALTY` | `0.8` | Down-rank generic names |
| `F2W_SOURCE_RAG` | `0` | Opt-in source RAG default (`1`/`true`/`on`). Alias: `HYBRID_RAG_ENABLED`. UI Settings can override per session. |

The UI's “Search Nodes” action calls the same active `KnowledgeGraph` search
dispatcher. Results report whether the active backend is `semantic` or
`lexical`.

## LLM settings

| Variable | Meaning |
|---|---|
| `KG_RAG_BACKEND` | Compatibility KG-RAG backend |
| `KG_RAG_CBORG_MODEL` | CBORG model |
| `KG_RAG_CBORG_BASE_URL` | KG-RAG-specific CBORG URL |
| `KG_RAG_OLLAMA_MODEL` | Ollama model |
| `KG_RAG_OLLAMA_URL` | Ollama chat endpoint |
| `KG_RAG_TEMPERATURE` | Answer generation temperature |
| `KG_RAG_LLM_TIMEOUT` | Request timeout in seconds |
| `KG_RAG_SHOW_BASELINE` | Also produce a non-RAG baseline |
| `EXTRACT_TERMS_BACKEND` | Extraction backend |
| `EXTRACT_TERMS_MODEL` | Extraction model (CBORG: must be a free LBL-hosted id, see below) |

## Model policy: free CBORG models only

Owner decision 2026-11-09 (`docs/migration/reviews/owner_decisions_2026-11-03.md`, seventeenth
set): "we should only allow free models via CBORG at the moment. We get 50 free usage credits per
month (mileage varies per model) on CBORG, so I want to avoid using those." The application
enforces this as a model policy (`app/modules/model_policy.py`):

- **Allowlist as data.** `app/modules/free_cborg_models.json` (shipped with the package) lists
  the free LBL-hosted ids taken from the CBORG models page on 2026-11-06 (`source`, `as_of`), the
  `default_model` (`lbl/cborg-chat`) and `aliases` (`mistral-large-4` is `lbl/cborg-coder-ultra`).
  Edit the JSON to change the policy; no code changes. `F2W_MODEL_POLICY_FILE` points a deployment
  at an edited copy. A missing or invalid file is an empty allowlist: every CBORG id is refused.
- **Spellings.** A bare id (`cborg-chat`, as the CBORG listing endpoint returns them) and its
  `lbl/` form (`lbl/cborg-chat`, as the chat API accepts them) are one model. An id with another
  provider prefix (`openai/`, `anthropic/`, `google/`, `amazon/`, ...) is never free.
- **Live refinement.** The live model list (`GET /models`, `GET /v1/library/models`, the judge
  route) is filtered to the allowlist; when the listing carries a price for a model, the model is
  allowed only if that price is 0. (On 2026-11-06 the listing carried no prices, so the allowlist
  alone decides until CBORG exposes them.)
- **Every CBORG call path refuses a non-free id before any request**, with a typed `denied`
  error (`reason: model_not_free`) that names the id and the policy and never a key: the CBORG chat
  clients (`term_extractor.clients`, `kg_rag_api`, the legacy extraction CLI), the extraction model
  (`f2w-run --model`, the `library_extract` job), the validation judge (`PUT /v1/validation/judge-model`,
  `--judge-model`), the pilot labelling CLI (labeller, judge, model under test), the binary runner,
  and the chat helper agents (`AdmittedChat`). Ollama and other local backends are unaffected.
- **Pickers.** The Settings panel's CBORG model dropdown and the judge dropdown show only free
  ids. A saved or configured paid id (an old browser preference, `KG_RAG_CBORG_MODEL`,
  `config.yml`) falls back to the default free model on the server and the panel shows a notice;
  the frozen `GET`/`PUT /settings` payload keeps its shape (values are filtered, never fields).

| Variable | Default | Meaning |
|---|---|---|
| `F2W_MODEL_POLICY_FILE` | packaged `free_cborg_models.json` | Another copy of the allowlist (same JSON shape) |
| `F2W_HELPER_AGENT_LLM` | unset (off) | Opt-in: let the orchestrator routing and evidence-debate helper agents call the model. Off, they take their deterministic path and make no model call, so chat spends no credits on classification or debate; extraction and question answering keep using the chosen free model. |

## Images: gateway port and profile

The three images expose one port, `F2W_GATEWAY_PORT` (default `8080`), and
serve everything under `/fair2wise`. The profile fixes
`F2W_DEPLOYMENT_PROFILE`, `F2W_AUTHORIZATION_MODE`, `F2W_TILED_MODE` and the
Tiled settings; a conflicting environment exits 78. `F2W_CORS_ORIGINS` is
required on the ALS images. The full table is in `docker/profiles/README.md`.

## Frontend and ports (source and Compose developer stack)

| Variable | Default | Meaning |
|---|---:|---|
| `VITE_F2W_AGENT_API_URL` | `http://127.0.0.1:8090` | Browser-visible agent API (unprefixed builds) |
| `F2W_PUBLIC_PREFIX` | unset | `/fair2wise` in images: Vite base/API/docs at build time, API `root_path=/fair2wise/api` at run time |
| `F2W_API_UPSTREAM` | `127.0.0.1:8090` | Gateway's private API `host:port` (`agent:8090` in Compose) |
| `F2W_AGENT_HOST` / `F2W_AGENT_PORT` | `127.0.0.1` / `8090` | Agent bind address |
| `F2W_UI_HOST` / `F2W_UI_PORT` | `127.0.0.1` / `5175` | Vite/UI bind; **never 5174** (SSH). 5173 is CORS-allowed |
| `FAIR2WISE_UI_DIR` | repository `ui/` | Alternate UI workspace |

Vite variables are read in the browser bundle, so the URL must be reachable
from the browser rather than only from the server process.

## Docker Compose overrides

| Variable | Default | Meaning |
|---|---:|---|
| `CBORG_API_KEY` | required | Agent LLM credential (host `.env` via `env_file:`; not baked into the image) |
| `F2W_UI_PORT` | `5175` | UI loopback port; never `5174` |
| `F2W_AGENT_PORT` | `8090` | Agent loopback port (browser origin) |
| `OPENALEX_EMAIL` | empty | Optional polite-pool identity |
| `MP_API_KEY` | empty | Optional Materials Project credential |
| `GITHUB_TOKEN` | empty | Optional GitHub rate-limit credential |

Compose sets the internal agent and Splash hostnames itself. Do not override
`KG_RAG_SPLASH_URI` with a host-local URL inside the containers.

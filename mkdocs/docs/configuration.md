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
| `MP_API_KEY` | Optional | Materials Project chemistry validation |
| `GITHUB_TOKEN` | Optional | Higher-rate GitHub source-code retrieval |
| `GLOBUS_COMPUTE_ENDPOINT_ID` | Remote extraction | Globus Compute endpoint |
| `GLOBUS_PROJECT_ID` | Remote extraction | Globus project identity |
| `ACADEMY_GLOBUS_CLIENT_ID` | Service auth only | Academy Globus client |
| `ACADEMY_GLOBUS_CLIENT_SECRET` | Service auth only | Academy Globus secret |

Never put secret values in `config.yml`.

Compose requires `CBORG_API_KEY` during interpolation. Export it before the
three project commands, or place it in an untracked `.env` file before running
`docker compose up`. The host environment takes precedence over `.env`.

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
| `F2W_KG_MODE` | `splash` | Editable Splash graph or JSON snapshot |
| `F2W_GRAPH` | `storage/kg/matkg_with_code.json` | Initial/fallback graph |
| `F2W_SEED_TERMS` | empty | Cumulative terms seed |
| `F2W_MAX_ROUNDS` | `3` | Evidence-growth rounds |
| `F2W_MAX_PAPERS` | `1` in UI launcher | Maximum papers per round |
| `F2W_CANDIDATE_POOL` | `25` | Literature candidates searched |
| `F2W_DOWNLOAD_DELAY` | `0` in UI launcher | Delay between download attempts |
| `F2W_WORKERS` | `8` | Page extraction workers |
| `F2W_WORKFLOW_MODE` | `agentic` | `agentic` or `deterministic` |
| `F2W_EXTRACTION_MODE` | `targeted` | `targeted` or `full` |
| `F2W_TARGETED_MAX_PAGES` | `6` | Per-PDF targeted page cap |

## Retrieval

| Variable | Default/typical | Meaning |
|---|---:|---|
| `KG_RAG_GRAPH_SOURCE` | `splash` | `splash` or `json` |
| `KG_RAG_RETRIEVAL_BACKEND` | `lexical` | Search implementation; semantic packages are not included in the app image |
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
| `EXTRACT_TERMS_MODEL` | Extraction model |

## Frontend and ports

| Variable | Default | Meaning |
|---|---:|---|
| `VITE_F2W_AGENT_API_URL` | `http://127.0.0.1:8090` | Browser-visible agent API |
| `F2W_AGENT_HOST` / `F2W_AGENT_PORT` | `127.0.0.1` / `8090` | Agent bind address |
| `F2W_UI_HOST` / `F2W_UI_PORT` | `127.0.0.1` / `5173` | Vite bind address |
| `FAIR2WISE_UI_DIR` | repository `ui/` | Alternate UI workspace |

Vite variables are read in the browser bundle, so the URL must be reachable
from the browser rather than only from the server process.

## Docker Compose overrides

| Variable | Default | Meaning |
|---|---:|---|
| `CBORG_API_KEY` | required | Agent LLM credential |
| `F2W_UI_PORT` | `5173` | Sole host-published port, bound to loopback |
| `OPENALEX_EMAIL` | empty | Optional polite-pool identity |
| `MP_API_KEY` | empty | Optional Materials Project credential |
| `GITHUB_TOKEN` | empty | Optional GitHub rate-limit credential |

Compose sets the internal agent and Splash hostnames itself. Do not override
`KG_RAG_SPLASH_URI` with a host-local URL inside the containers.

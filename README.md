# From FAIR to WISE: Creating Knowledge Graphs from Research Papers

## Overview

This repository builds materials-science knowledge graphs from research papers (PDFs). The main workflow is:

1. Collect PDFs into a domain folder (e.g. `polymer_papers/`, `xray_papers/`)
2. Extract schema-aligned terminology + publication metadata with an LLM → `storage/terminology/`
3. Convert extracted terms JSON into a MatKG graph JSON → `storage/kg/`
4. Import the MatKG JSON into `splash_links`
5. Query the database-backed graph via KG-RAG chat (CLI or Open WebUI)

**Optional — automated agent pipeline:** [`app.modules.launchers.f2w_agent`](app/modules/launchers/f2w_agent.py) launches the Academy workflow that answers from the KG when evidence suffices and otherwise searches for papers, requests approval, extracts terms, rebuilds the KG, and re-queries. See **[3-Agent KG-RAG Pipeline](#3-agent-kg-rag-pipeline)**.

---

## Recommended: run with Docker Compose

Docker Compose is the preferred way to run FAIR2WISE. A clone does **not**
depend on Docker images from the original developer's computer. Each computer
builds its own application images from the repository's `Dockerfile`,
`splash_links/Containerfile`, source code, and pinned requirements.

Install Docker Engine or Docker Desktop with Docker Compose v2, authorize the
computer for CBORG access, and make `CBORG_API_KEY` available in the shell.
Then the complete application starts in three commands:

```bash
git clone https://github.com/matesuu/FAIRtoWISE-FORUM-AI.git
cd FAIRtoWISE-FORUM-AI
docker compose up --build
```

Open `http://127.0.0.1:5173`. Only the frontend is exposed on the host; the
agent API and Splash database remain private inside the Compose network.

Instead of exporting the API key, create an untracked `.env` file before the
last command:

```bash
cp .env.example .env
```

Set `CBORG_API_KEY` in `.env` and never commit that file. The first startup
builds the images and copies the repository's tracked
`splash_links/links.sqlite` seed into a writable Docker volume. Later startups
reuse the built images and persistent data. See [Docker operation](#docker)
for lifecycle, diagnostics, persistence, and reset commands.

---

## Prerequisites

- Docker Engine or Docker Desktop with Docker Compose v2 for the three-command setup
- A CBORG API key supplied through the host environment or an untracked `.env`

For local development without Docker:

- Python 3.12
- Node.js 20+ (including npm); npm installs the pinned local Vite 6 toolchain from `ui/package.json`
- [Pixi](https://pixi.sh/latest/installation/) for the vendored `splash_links` database server
- A [CBORG](https://cborg.lbl.gov/) API key (default LLM backend). CBORG requires LBLnet/VPN or an authorized IP — see [CBORG IP authorization](https://api.cborg.lbl.gov/key/manage).
- Optional: [Ollama](https://ollama.com/) running locally for offline inference
- For the 3-agent pipeline: `academy-py`, `langgraph`, `langchain-core`, `langchain-openai` (included in `requirements.txt`)

---

## Setup

### Local development setup

### 1. Clone the repository

```bash
git clone https://github.com/fair2wise/FAIRtoWISE-FORUM-AI
cd FAIRtoWISE-FORUM-AI
```

### 2. Install dependencies

```bash
python3 --version  # should report Python 3.12
python3 -m pip install -r requirements.txt

# Install the UI dependencies, including Vite.
npm --version
cd ui
npm ci
cd ..
```

Install Pixi and initialize the `splash_links` environment once:

```bash
./scripts/install_pixi.sh
```

The script leaves an existing Pixi installation untouched. If Pixi is missing,
it downloads the official installer from `pixi.sh`, installs Pixi, and runs
`pixi install` against `splash_links/pixi.toml`.

### 3. Configure environment

Copy the example env file and fill in your credentials:

```bash
cp .env.example .env
```

Required keys in `.env`:

```env
CBORG_API_KEY=your-cborg-api-key
CBORG_BASE_URL=https://api.cborg.lbl.gov

# Optional — Materials Project API key for formula cross-check
MP_API_KEY=your-materials-project-key

# KG-RAG chat settings
KG_RAG_BACKEND=cborg
KG_RAG_CBORG_MODEL=lbl/cborg-chat
KG_RAG_GRAPH_SOURCE=splash
KG_RAG_SPLASH_URI=splash://localhost:8081
KG_RAG_SPLASH_PAGE_SIZE=1000
KG_RAG_GRAPH=storage/kg/matkg_xray_papers_cborg_chat.json
KG_RAG_RETRIEVAL_BACKEND=lexical
KG_RAG_LLM_TIMEOUT=120
KG_RAG_CTX_CHARS=6000
KG_RAG_SHOW_BASELINE=0
PYSTOW_HOME=.cache/pystow

# Optional — OpenAlex polite pool (download agent)
OPENALEX_EMAIL=you@example.com

# Optional — splash_links repo path (3-agent splash mode)
SPLASH_LINKS_REPO=splash_links
SPLASH_LINKS_DB=links.sqlite
```

> **Note:** runtime environment variables and CLI flags override `.env`; `.env` overrides `config.yml` defaults.

---

## LinkML "Core Model" Schema

An example schema for organic photovoltaics is at [`storage/schema/matkg_schema.yaml`](storage/schema/matkg_schema.yaml). Use it as a starting point for defining a schema for a different topic. The concept extraction passes this schema to the LLM to keep results structured and domain-aligned.

---

## LLM Backends

The code supports two backends:

| Backend | Description |
|---|---|
| `cborg` | LBL CBORG API (default). OpenAI-compatible. Requires `CBORG_API_KEY`. |
| `ollama` | Local Ollama instance. No API key needed. Requires Ollama running. |

CBORG is the default for both term extraction and KG-RAG chat. To use Ollama, pass `--backend ollama --model <model-name>` or set `KG_RAG_BACKEND=ollama` in `.env`.

---

## Step 1 — Collect PDFs

Place research paper PDFs in `polymer_papers/`. To download papers from arXiv or OpenAlex:

```bash
python3 scripts/download_pdfs.py --help
```

---

## Step 2 — [Concept Extraction](app/modules/extract_terms.py)

`extract_terms.py` is a schema-aware, parallel PDF term extraction engine. It produces structured, ontology-aligned JSON output integrating:

- CBORG or Ollama (OpenAI-compatible) LLM backends
- LinkML schema enforcement via `SchemaHelper`
- Chemical formula validation and repair
- ChEBI ontology enrichment
- Physical property extraction and normalization
- X-ray scattering code snippet extraction (`code_snippets`)
- Publication metadata extraction per PDF (title, authors, DOI, journal, volume, issue, pages, abstract, keywords)
- Parallel page-level processing with `ThreadPoolExecutor`
- Thread-safe incremental saving and exponential-backoff retries

### ChEBI ontology (optional)

ChEBI enrichment adds chemical formulas, SMILES, InChI, charge, and roles to extracted terms. Without it, extraction still works — enrichment is silently skipped.

To enable it, download the `.obo` file (~500 MB):

```bash
mkdir -p storage/ontologies
curl -L https://ftp.ebi.ac.uk/pub/databases/chebi/ontology/chebi.obo \
  -o storage/ontologies/chebi.obo
```

### Run extraction (CBORG, default)

Run the extractor on any PDF folder:

```bash
python3 app/modules/extract_terms.py \
  --pdf-dir polymer_papers/ \
  --output storage/terminology/extracted_terms_polymer.json
```

For a dedicated xray KG (or any other domain-specific folder):

```bash
python3 app/modules/extract_terms.py \
  --pdf-dir xray_papers/ \
  --output storage/terminology/extracted_terms_xray_papers_cborg_chat.json
```

Defaults:
- `--backend cborg`
- `--model lbl/cborg-chat`
- `--max-workers 4`
- optional ChEBI path from `--chebi-obo`, `CHEBI_OBO_PATH`, or `storage/ontologies/chebi.obo`

Target a single PDF by isolating it in a temp folder:

```bash
mkdir -p /tmp/single_pdf
cp xray_papers/XRAY1.pdf /tmp/single_pdf/

python3 app/modules/extract_terms.py \
  --pdf-dir /tmp/single_pdf/ \
  --output storage/terminology/extracted_terms_xray1.json
```

Show all options:

```bash
python3 app/modules/extract_terms.py --help
```

### Checkpoint evaluation pipeline

Runs extraction at 25 → 50 → 75 → 100% of papers, producing timestamped JSONs in `storage/terminology/` and KG files in `storage/kg/`:

```bash
python3 app/run_pipeline_cborg.py
```

Options:

```bash
python3 app/run_pipeline_cborg.py --help

# Dry run — print planned runs without executing
python3 app/run_pipeline_cborg.py --dry-run

# Organize PDFs into checkpoint folders first
python3 app/run_pipeline_cborg.py \
  --organize \
  --source-dir polymer_papers \
  --pdf-root polymer_papers

# Run with a specific model
python3 app/run_pipeline_cborg.py --models google/gemini-flash-lite
```

### What gets extracted

Each extracted terms JSON contains two top-level keys:

| Key | Description |
|---|---|
| `terms` | List of schema-aligned entities. Each entry carries: `term`, `definition`, `category`, `formula`, `relations`, `pages`, `source_papers`, `context_snippets`, `source_metadata` (per-PDF publication fields), plus legacy scalar fields (`publication_year`, `paper_title`, `authors`, `institutions`, `doi`, `journal`, `volume`, `issue`, `pages_range`, `abstract_text`, `keywords`) |
| `code_snippets` | List of peak-finding / scattering analysis code blocks extracted from embedded PDF text or paper-linked GitHub repositories. Each entry carries: `code_snippet`, `code_language`, `code_description`, `function_name`, `domain_features`, `source_paper`, `page`, and `source_metadata`; GitHub-derived snippets also carry repo/file/commit/license provenance |

Publication metadata (`paper_title`, `authors`, `doi`, etc.) is extracted from PDF metadata fields first, then from first-page text via regex. Fields not found are `null` or `[]`. Metadata is stored per source PDF in `source_metadata` so a term that appears in multiple papers does not smear one paper's authors/DOI onto another.

### Implementation details

- Pages processed in parallel with configurable workers (`--max-workers`, default `4`)
- Terms saved incrementally after every page (crash-safe)
- `SchemaHelper` fuzzy-matches LLM output to LinkML classes/slots
- `ChemicalFormulaValidator` validates and LLM-repairs invalid formulas
- ChEBI lookup enriches chemicals with SMILES, InChI, charge, roles
- `PhysicalPropertyExtractor` + `PropertyNormalizer` detect and standardize numerical properties
- Duplicate terms merged via LLM-guided fuzzy comparison
- 50-token provenance snippets link every node back to its source page and paper

---

## Step 3 — [Convert to Knowledge Graph](app/modules/json2kg.py)

Converts an extracted terms JSON into a MatKG-compatible JSON graph with `things` (nodes) and `associations` (edges). Publication metadata and `code_snippets` are carried into the graph, including per-source `source_metadata`.

### Polymer papers KG

```bash
python3 app/modules/json2kg.py \
  storage/terminology/extracted_terms_polymer.json \
  storage/kg/matkg_polymer.json
```

### Xray papers KG

```bash
python3 app/modules/json2kg.py \
  storage/terminology/extracted_terms_xray_papers_cborg_chat.json \
  storage/kg/matkg_xray_papers_cborg_chat.json
```

With verbose output (node/edge counts, snippet count):

```bash
python3 app/modules/json2kg.py \
  storage/terminology/extracted_terms_xray_papers_cborg_chat.json \
  storage/kg/matkg_xray_papers_cborg_chat.json \
  --verbose
```

### Implementation details

- Stable canonical IDs via `matkg:` prefix + regex-cleaned term name
- `source_metadata` preserved on term and `CodeSnippet` nodes (per-PDF provenance)
- Legacy scalar publication fields retained for backward compatibility
- `code_snippets` converted to `CodeSnippet` nodes and wired to term nodes from the same paper
- Missing edge targets auto-stubbed to prevent dangling edges
- Edges carry optional evidence strings
- Duplicate `(subject, predicate, object)` edges de-duplicated
- Integrated pytest suite validates ID generation, field retention, and CLI

---

## Step 4 — Import into splash_links

KG-RAG loads from the `splash_links` graph database by default (`KG_RAG_GRAPH_SOURCE=splash`). **After every KG JSON rebuild or metadata fix, you must re-import into splash_links and restart KG-RAG.** The splash DB is not updated automatically when `storage/kg/*.json` changes.

The vendored [`splash_links`](splash_links/) service is the graph persistence
layer used by FAIR2WISE. It stores nodes as entities and relationships as
directed, predicate-labelled links in `splash_links/links.sqlite`, then exposes
them through FastAPI and GraphQL at `/splash_links/graphql`. KG-RAG reads this
service through `splash://localhost:8081`; the JSON graph remains the configured
fallback when the service is unavailable.

Initialize its Pixi environment once from the FAIR2WISE root:

```bash
./scripts/install_pixi.sh
```

### 4a. Start the splash-links server

The combined launcher starts Splash first, waits for its health endpoint, and
then starts the agent backend and frontend:

```bash
./scripts/start_all.sh
```

For database-only development, run it directly from the vendored workspace:

```bash
cd splash_links
pixi run serve
```

The server listens on `http://localhost:8081`. Verify:

Use `pixi run serve-dev` instead when developing `splash_links` itself and you
want Uvicorn to reload automatically after source changes.

```bash
curl -s http://localhost:8081/docs -o /dev/null -w "%{http_code}\n"
# expect: 200
```

### 4b. Wipe stale DB before re-import (recommended)

If you are re-importing after a metadata or schema fix, clear the existing splash graph so stale entities (missing `source_metadata`, `code_snippet`, etc.) do not linger:

```bash
cd /path/to/f2wlocal
./scripts/wipe_splash_db.sh
./scripts/start_all.sh
```

> **Why wipe?** `import_kg.py` creates new entities; it does not delete old ones. A partial or pre-fix import leaves duplicate/stale records that KG-RAG may still load. The guarded reset script refuses to run while Splash is active and requires typed confirmation before deleting the database.

### 4c. Import the MatKG JSON

```bash
cd /path/to/f2wlocal/splash_links
pixi run python scripts/import_kg.py /path/to/f2wlocal/storage/kg/matkg_xray_papers_cborg_chat.json
```

If you are using the 3-agent pipeline, `splash_reimport()` is the preferred path. It clears the live splash graph through GraphQL and then runs the import script against the running server. That avoids the SQLite "readonly database" failure that can happen when `links.sqlite` is deleted while `pixi run serve` is still holding the file open.

Dry-run first (validate counts, no writes):

```bash
pixi run python scripts/import_kg.py --dry-run /path/to/f2wlocal/storage/kg/matkg_xray_papers_cborg_chat.json
```

Custom server URL:

```bash
pixi run python scripts/import_kg.py \
  --url http://localhost:8081 \
  /path/to/f2wlocal/storage/kg/matkg_xray_papers_cborg_chat.json
```

Expected output for the xray demo KG:

```
=== TOTALS: 206 entities, 107 links created, 0 links skipped ===
```

### 4d. Verify splash import

Quick GraphQL check:

```bash
curl -s http://localhost:8081/splash_links/graphql \
  -H 'Content-Type: application/json' \
  -d '{"query":"{ entities(limit:1) { name properties } }"}' | python3 -m json.tool
```

Confirm imported entities carry full `properties` (e.g. `source_metadata`, `code_snippet` on CodeSnippet nodes).

### 4e. Point KG-RAG at splash

In `.env` (or export before starting KG-RAG):

```env
KG_RAG_GRAPH_SOURCE=splash
KG_RAG_SPLASH_URI=splash://localhost:8081
KG_RAG_GRAPH=storage/kg/matkg_xray_papers_cborg_chat.json
KG_RAG_RETRIEVAL_BACKEND=lexical
```

`KG_RAG_GRAPH` is the JSON fallback path used when splash-links is unreachable.

### 4f. Restart KG-RAG after import

KG-RAG caches the graph in memory at startup. **Always restart** after splash re-import:

```bash
cd /path/to/f2wlocal
python3 app/modules/kg_rag_api.py --api --backend cborg --graph-source splash
```

### 4g. Helper scripts for the 3-agent pipeline

Use these scripts from `f2wlocal` when you want the automated download/extract/rebuild loop:

```bash
# Start splash-links from the vendored workspace
cd splash_links
pixi run serve

# Start the backend API used by the colocated UI
cd /Users/mateo/Desktop/f2wlocal
./scripts/start_agent_backend.sh

# Start the frontend dev server from ./ui
cd /Users/mateo/Desktop/f2wlocal
./scripts/start_agent_frontend.sh
```

Or manage splash-links, the agent backend, and the frontend together:

```bash
./scripts/start_all.sh
```

To permanently reset the local Splash database, first stop the stack and run
`./scripts/wipe_splash_db.sh`. The utility prints the exact database path and
requires typed confirmation before deleting it.

Defaults used by `start_agent_backend.sh`:

- `F2W_KG_MODE=splash`
- `F2W_MAX_PAPERS=1`
- `F2W_WORKERS=1`
- `F2W_DOWNLOAD_DELAY=0`
- `F2W_WORKDIR=runs/ui_session_splash`
- `SPLASH_LINKS_REPO=splash_links`
- `SPLASH_LINKS_DB=links.sqlite`

Override them for heavier runs:

```bash
F2W_MAX_PAPERS=3 F2W_WORKERS=8 ./scripts/start_agent_backend.sh
```

### JSON fallback (no splash server)

To bypass the database and load a local JSON file directly:

```env
KG_RAG_GRAPH_SOURCE=json
```

Or pass CLI flags:

```bash
python3 app/modules/kg_rag_api.py --graph-source json --graph storage/kg/matkg_xray_papers_cborg_chat.json
```

### splash_links troubleshooting

| Symptom | Fix |
|---|---|
| Wrong/missing code snippets or metadata in answers | Wipe `links.sqlite`, re-import KG JSON, restart KG-RAG |
| `splash-links unreachable` warning in KG-RAG logs | Start `pixi run serve` in `splash_links`, or set `KG_RAG_GRAPH_SOURCE=json` |
| Import succeeds but answers unchanged | Restart KG-RAG — old process still holds cached graph |
| Duplicate entities after multiple imports | Wipe `links.sqlite` before re-import |
| `import_kg.py` cannot connect | Confirm `curl http://localhost:8081/docs` returns 200 |

---

## Step 5 — [KG-RAG LLM Chat](app/modules/kg_rag_api.py)

Query the knowledge graph via retrieval-augmented generation. Supports CLI, one-shot, competency evaluation, and an Open WebUI-compatible FastAPI server.

### CLI — interactive REPL

```bash
python3 app/modules/kg_rag_api.py
```

Prompt appears:

```
Ask (exit to quit):
```

### CLI — one-shot question

```bash
python3 app/modules/kg_rag_api.py \
  --question "What is the role of P3HT crystallinity in OPV performance?"
```

### Web UI / agent bridge

The prototype UI in [`ui/`](ui/) talks to the agent pipeline through `http://127.0.0.1:8090/chat`.
It renders grounded answers, highlighted KG nodes, and relevant publication metadata returned by the backend.

Install UI dependencies once:

```bash
cd /Users/mateo/Desktop/f2wlocal/ui
npm ci
```

Vite 6 is declared in [`ui/package.json`](ui/package.json) and installed locally
by `npm ci`; no global Vite installation is required.

Run it with:

```bash
cd /Users/mateo/Desktop/f2wlocal
./scripts/start_agent_frontend.sh
```

`scripts/start_agent_frontend.sh` defaults to `ui/` and can still be pointed elsewhere with `FAIR2WISE_UI_DIR=/path/to/ui`.

`POST /chat` and `POST /chat/stream` responses include:

- `answer`: grounded answer text
- `node_ids`: selected KG node IDs for graph highlighting
- `publications`: deduped publication records from selected nodes, including `paper_title`, `authors`, `publication_year`, `doi`, `source_paper`, and `supporting_nodes` when available
- `graph`: current session graph payload for UI refresh

With a shorter timeout:

```bash
python3 app/modules/kg_rag_api.py \
  --timeout 60 \
  --question "What is P3HT?"
```

Reduce context size if responses are slow:

```bash
KG_RAG_CTX_CHARS=3000 python3 app/modules/kg_rag_api.py \
  --timeout 60 \
  --question "What is P3HT?"
```

### CLI — use a specific model

```bash
# CBORG (default)
python3 app/modules/kg_rag_api.py \
  --model lbl/cborg-chat \
  --question "What is P3HT?"

# Nova Micro (cheaper/faster)
python3 app/modules/kg_rag_api.py \
  --model nova-micro \
  --question "What is P3HT?"

# Ollama (local)
python3 app/modules/kg_rag_api.py \
  --backend ollama \
  --model deepseek-r1:70b \
  --question "What is P3HT?"
```

### CLI — use a specific KG

```bash
python3 app/modules/kg_rag_api.py \
  --graph storage/kg/matkg_lbl_cborg-chat_latest_100_20251008_010852.json \
  --question "What materials show high PCE?"
```

### CLI — show baseline (non-RAG) answer alongside KG-RAG

```bash
python3 app/modules/kg_rag_api.py \
  --show-baseline \
  --question "What is P3HT?"
```

### CLI — competency question evaluation

```bash
python3 app/modules/kg_rag_api.py --competency
```

Runs the full question set from `storage/competency_questions/thomas_f.txt`. Results saved incrementally to `storage/competency_questions/competency_results_qwen3_235b_580papers.json`.

### CLI argument reference

| Argument | Default | Description |
|---|---|---|
| `--graph-source` | `splash` | KG source (`splash` database or `json` file) |
| `--splash-uri` | `splash://localhost:8081` | `splash_links` service URI |
| `--splash-page-size` | `1000` | GraphQL page size for database graph loading |
| `--graph` | `KG_RAG_GRAPH` env | Path to KG JSON file when `--graph-source json` |
| `--question` | — | One-shot question, then exit |
| `--backend` | `cborg` | `ollama`, `cborg`, or `cborg-openai` |
| `--model` | from env | Model name for selected backend |
| `--timeout` | `120` | LLM request timeout in seconds |
| `--show-baseline` | off | Also generate non-RAG baseline answer |
| `--competency` | off | Run full competency question set |
| `--api` | off | Start FastAPI server on port 11435 |

### Environment variable reference

| Variable | Default | Description |
|---|---|---|
| `CBORG_API_KEY` | — | CBORG API key (required for cborg backend) |
| `CBORG_BASE_URL` | `https://api.cborg.lbl.gov` | CBORG API base URL |
| `CBORG_IP_FAMILY` | `ipv6` in Compose | Force CBORG traffic over the authorized IPv6 address; use `auto` or `ipv4` to override |
| `KG_RAG_BACKEND` | `cborg` | LLM backend (`cborg` or `ollama`) |
| `KG_RAG_CBORG_MODEL` | `lbl/cborg-chat` | CBORG model name |
| `KG_RAG_OLLAMA_MODEL` | `deepseek-r1:70b` | Ollama model name |
| `KG_RAG_GRAPH_SOURCE` | `splash` | KG source (`splash`, `splash_links`, `splash-links`, or `json`) |
| `KG_RAG_SPLASH_URI` | `splash://localhost:8081` | `splash_links` service URI |
| `KG_RAG_SPLASH_PAGE_SIZE` | `1000` | GraphQL page size for database graph loading |
| `KG_RAG_GRAPH` | `storage/kg/matkg_xray_papers_cborg_chat.json` | KG file to load when `KG_RAG_GRAPH_SOURCE=json` (also splash fallback path) |
| `KG_RAG_RETRIEVAL_BACKEND` | `lexical` | Retrieval method; semantic dependencies are not included in the app image |
| `KG_RAG_CTX_CHARS` | `16000` | Max chars of KG context per prompt |
| `KG_RAG_LLM_TIMEOUT` | `120` | LLM request timeout in seconds |
| `KG_RAG_SHOW_BASELINE` | `0` | Set to `1` to enable baseline responses |
| `PYSTOW_HOME` | `.cache/pystow` | Local PyStow cache (avoids home-dir writes) |

### Implementation details

- Hybrid retrieval: SentenceTransformer embeddings + FAISS IVF-Flat + weighted BFS
- Lexical retrieval is the lightweight default. `requirements-semantic.in`
  documents the optional packages for SentenceTransformer/FAISS retrieval;
  they are not installed by the app image.
- Multi-factor node scoring: semantic similarity, graph depth, lexical overlap, evidence count
- Context blocks include per-source `Source_Metadata`, KG triples, formulas, descriptions, and PDF snippets (page-cached)
- Legacy scalar publication fields suppressed when `source_metadata` exists, or when a node has multiple sources without per-source metadata (prevents metadata smear)
- LLM prompt enforces strict grounding: publication metadata (authors, year, DOI, journal) forbidden unless verbatim in retrieved context
- Reproduced code snippets must be followed by a standard disclaimer (`CODE_SNIPPET_DISCLAIMER` in `kg_rag_api.py`)
- Question decomposition for multi-clause queries
- Missing-node tracking logged to `storage/knowledge_gaps/`
- FastAPI proxy exposes `/api/chat`, `/api/tags`, `/api/ps` (Open WebUI-compatible)
- GPU auto-detect with CPU fallback for embeddings

---

## 3-Agent KG-RAG Pipeline

[`app.modules.launchers.f2w_agent`](app/modules/launchers/f2w_agent.py) launches an **Academy-based, self-growing KG-RAG chat** that wraps the manual Steps 1–5 workflow behind cooperating agents. A user asks a question; if the KG lacks sufficient evidence, the system can search for papers, request approval, download and extract selected evidence, rebuild the KG, and retry — up to `--max-rounds` times.

### Architecture

| Agent | Module | Role |
|---|---|---|
| **RetrievalAgent** | [`app/modules/f2w_agent/retrieval_agent.py`](app/modules/f2w_agent/retrieval_agent.py) | Retrieve KG context, judge whether evidence suffices (no inference/hallucination), answer if sufficient |
| **DownloadAgent** | [`app/modules/f2w_agent/download_agent.py`](app/modules/f2w_agent/download_agent.py) | Search OpenAlex, rank abstracts by relevance, download top-N open-access PDFs |
| **ExtractorAgent** | [`app/modules/f2w_agent/extractor_agent.py`](app/modules/f2w_agent/extractor_agent.py) | Run the LangGraph [`term_extractor`](app/modules/term_extractor/) pipeline on downloaded PDFs |

The **coordinator** ([`app/modules/f2w_agent/coordinator.py`](app/modules/f2w_agent/coordinator.py)) launches all three agents in one local Academy `Manager` and drives this loop per question:

```
User question
  → RetrievalAgent.query
      → sufficient? → print grounded answer, done
      → insufficient → DownloadAgent.find_and_download (OpenAlex)
          → 0 new PDFs? → stop (cannot gather more evidence)
          → PDFs → ExtractorAgent.extract
              → rebuild KG (json2kg) → reload KG → retry query
```

### Quick start (xray demo KG)

Use the repaired xray demo data already in `storage/`:

```bash
# Check configuration
python3 -m app.modules.launchers.f2w_agent status

# One-shot question (JSON KG mode — no splash-links server required)
KG_RAG_GRAPH_SOURCE=json python3 -m app.modules.launchers.f2w_agent \
  --backend cborg \
  --model lbl/cborg-chat \
  --kg-mode json \
  --graph storage/kg/matkg_xray_papers_cborg_chat.json \
  --seed-terms storage/terminology/extracted_terms_xray_papers_cborg_chat.json \
  --workdir runs/my_session \
  ask "What is find_scattering_peaks used for?"

# Interactive chat loop
KG_RAG_GRAPH_SOURCE=json python3 -m app.modules.launchers.f2w_agent \
  --graph storage/kg/matkg_xray_papers_cborg_chat.json \
  --seed-terms storage/terminology/extracted_terms_xray_papers_cborg_chat.json \
  --workdir runs/my_session \
  chat

# HTTP API for the repo-local UI
./scripts/start_agent_backend.sh
```

Then run the prototype UI:

```bash
./scripts/start_agent_frontend.sh
```

When the KG already contains enough evidence, the retrieval agent answers immediately — no download or extraction runs.

When evidence is missing, the loop downloads papers (default `--max-papers 3` per round), extracts terms into a cumulative session JSON, rebuilds `runs/<workdir>/kg.json`, reloads the in-memory graph, and re-queries.

### CLI reference

```bash
python3 -m app.modules.launchers.f2w_agent --help
python3 -m app.modules.launchers.f2w_agent ask --help   # global flags go before subcommand
```

| Subcommand | Description |
|---|---|
| `status` | Print resolved backend, graph, workdir, and loop limits |
| `ask <question>` | One-shot question through the full loop |
| `api` | FastAPI bridge for the prototype web chat UI |
| `chat` | Interactive REPL (`exit` to quit) |

| Flag | Default | Description |
|---|---|---|
| `--backend` | `cborg` | LLM backend (`cborg`, `cborg-openai`, `ollama`) |
| `--model` | env default | Model name |
| `--graph` | — | Initial KG JSON for retrieval |
| `--seed-terms` | — | Cumulative extracted-terms JSON (merged on each extract pass) |
| `--kg-mode` | `splash` | `splash` = use/re-import splash-links; `json` = rebuild + in-process reload only |
| `--workdir` | `runs/session` | Session dir: `pdfs/`, `terms.json`, `kg.json` |
| `--max-rounds` | `3` | Max download→extract→reload cycles per question |
| `--max-papers` | `3` | PDFs to download per round |
| `--candidate-pool` | `25` | OpenAlex candidates to rank before downloading |
| `--download-delay` | `1.0` | Seconds to wait between PDF download attempts |
| `--no-download-validation` | off | Disable best-effort LLM relevance validation for downloaded PDFs |
| `--workers` | `4` | Page-level extraction parallelism |
| `--schema` | `storage/schema/matkg_schema.yaml` | LinkML schema for extraction |
| `--chebi` | — | Optional ChEBI `.obo` path |
| `--splash-repo` | `splash_links` | Path to the vendored splash_links workspace (`--kg-mode splash`) |
| `--allow-splash-wipe` | off | Permit `--kg-mode splash` to delete `links.sqlite` before re-import |

### KG update modes

**JSON mode (explicit local fallback):**

- After each extraction pass, `json2kg.py` rebuilds `workdir/kg.json`.
- `RetrievalAgent.reload_kg()` reloads the graph in-process.
- Pass `--kg-mode json` when you want to skip splash-links.

**Splash mode (`--kg-mode splash`):**

- Same JSON rebuild, then wipes/re-imports into `splash_links` via `scripts/import_kg.py` (see **Step 4**).
- Requires a running splash-links server and a local `splash_links` checkout (`SPLASH_LINKS_REPO`).
- Requires `--allow-splash-wipe` before deleting `links.sqlite`; the run prints the DB path it is about to use.
- Heavier per round; use when you want the live splash DB to stay in sync.

### Extractor package (`term_extractor`)

The extractor agent uses [`app/modules/term_extractor/`](app/modules/term_extractor/), ported from the FAIR2WISE `bowen/academy_agent` branch with **local provenance patches**:

- Output JSON includes top-level `code_snippets` and per-term `source_metadata` (source-scoped publication fields).
- Publication metadata comes **only** from PDF-derived extraction (`provenance.py`); the term LLM never stamps authors/DOI/year.
- Code snippets come from embedded PDF code blocks and explicit GitHub repository links found in PDF text; set optional `GITHUB_TOKEN` for higher GitHub API rate limits.
- KG conversion uses the existing [`json2kg.py`](app/modules/json2kg.py) (preserves `source_metadata` + CodeSnippet nodes).

The legacy standalone extractor [`extract_terms.py`](app/modules/extract_terms.py) remains available for manual/batch runs. The monitored remote `TermExtractorAgent` + dashboard stack in `term_extractor/` is available for standalone Globus/NERSC extraction, but is **not** used by the local `app.modules.launchers.f2w_agent` loop.

### NERSC deploy helper

Use [`scripts/deploy_nersc.sh`](scripts/deploy_nersc.sh) to sync the current extractor code to Perlmutter, update the remote Python environment, optionally sync PDFs, restart the Globus Compute endpoint, and submit a Bowen/Academy remote extraction run. It intentionally excludes `.env`, `.venv`, run artifacts, and caches.

Set local shell variables first:

```bash
export NERSC_USER=<username>
export NERSC_REPO=/pscratch/sd/<first-letter>/<username>/f2wlocal
export NERSC_HOST=perlmutter.nersc.gov
export NERSC_ENDPOINT=f2w-extractor
```

First-time setup/update:

```bash
scripts/deploy_nersc.sh --sync-code --setup
```

Sync code and PDFs, then restart the endpoint:

```bash
scripts/deploy_nersc.sh --sync-code --sync-pdfs /local/path/to/pdfs --restart-endpoint
```

Submit extraction after `python3 -m app.modules.launchers.user_agent --port 8000` has written `user_agent_handle.pkl`:

```bash
scripts/deploy_nersc.sh --submit
```

One-command flow:

```bash
scripts/deploy_nersc.sh --all /local/path/to/pdfs
```

The script defaults to CBORG (`F2W_BACKEND=cborg`, `F2W_MODEL=lbl/cborg-chat`, `F2W_MAX_WORKERS=4`). Override those env vars before running if needed. Keep Globus/Academy/CBORG secrets in local `.env`; do not copy `.env` to NERSC.

For the full three-agent NERSC path, install only `requirements.txt`. Install `requirements-globus.txt` only when configuring the extractor-only Globus Compute endpoint path.

### Download relevance ranking

The download agent:

1. Searches OpenAlex with the question + `missing_topics`, plus focused missing-topic queries, and deduplicates by DOI/OpenAlex ID.
2. Reconstructs each candidate abstract from `abstract_inverted_index`.
3. Keeps works with open-access URLs, preferring OpenAlex `pdf_url` fields before `oa_url`.
4. Ranks candidates by LLM relevance score (title + abstract); falls back to lexical overlap if the LLM is unavailable.
5. Downloads the top `--max-papers` PDFs into `workdir/pdfs/`, skipping files already present.
6. Rejects non-PDF payloads by checking `%PDF-` bytes before saving.
7. Runs best-effort LLM validation on downloaded PDF preview text; semantic validation failures delete the PDF, while validator outages fail open.

Set `OPENALEX_EMAIL` in `.env` for polite-pool access.

The loop writes `workdir/downloads.jsonl` with OpenAlex ID/DOI/title, attempted URLs, score, destination path, and validation status for each attempted work.

Extraction uses `workdir/processed_pdfs.json` to avoid reprocessing PDFs already merged into `workdir/terms.json`. Each round stages only unprocessed PDFs under `workdir/extract_rounds/round_<n>/`.

### Sufficiency judgement

The retrieval agent uses a strict JSON judge prompt: answer **only** from retrieved KG/PDF context; no inference, no hallucinated metadata. If retrieved nodes have no direct source evidence, the verdict is forced insufficient without calling the LLM.

For the 3-agent loop, the retrieval agent now skips the judge LLM entirely when retrieved nodes have no direct source evidence (`source_papers`, snippets, code, or edge evidence), reports whether splash fell back to JSON, and treats judge/runtime failures as insufficient evidence rather than crashing the session.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `403 ip_not_authorized` from CBORG | Connect via LBLnet/VPN or authorize your IP at [api.cborg.lbl.gov/key/manage](https://api.cborg.lbl.gov/key/manage) |
| Answer immediately but question is out-of-domain | Expected when xray demo KG lacks coverage — loop should trigger download/extract |
| `downloaded 0 paper(s)` | No open-access PDFs matched; try broadening the question or increasing `--candidate-pool` |
| Splash mode import fails | Verify `SPLASH_LINKS_REPO`, run splash-links server, see **Step 4** |
| Wrong metadata in answers | Same provenance rules as Step 5 — verify `source_metadata` in terms JSON and re-import splash if needed |

---

## Open WebUI

Chat with the KG-RAG backend through a browser UI.

### 1. Install Open WebUI

Install in a separate virtual environment to avoid dependency conflicts:

```bash
python3.12 -m venv .venv-open-webui
source .venv-open-webui/bin/activate
pip3 install --upgrade pip
pip3 install open-webui
```

### 2. Start Open WebUI

```bash
source .venv-open-webui/bin/activate
open-webui serve --host 127.0.0.1 --port 8080
```

Open the UI at `http://localhost:8080`. First startup may take a minute to download the default embedding model.

### 3. Start the KG-RAG API server

In a separate terminal (outside the Open WebUI venv). Ensure splash-links is running (see **Step 4**) if using the default `splash` graph source:

```bash
cd /path/to/FAIRtoWISE-FORUM-AI
python3 app/modules/kg_rag_api.py --api --graph-source splash
```

This starts FastAPI on `http://0.0.0.0:11435`. Verify it is running:

```bash
curl http://localhost:11435/api/tags
```

Expected:

```json
{"models":[{"name":"kg-rag:latest","model":"kg-rag:latest","modified_at":"2025-09-17T00:00:00Z"}]}
```

Test a chat call:

```bash
curl -X POST http://localhost:11435/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model":"kg-rag:latest","messages":[{"role":"user","content":"What is P3HT?"}],"stream":false}'
```

### 4. Connect Open WebUI to the KG-RAG server

1. In Open WebUI go to **Admin Settings → Connections → Ollama API**
2. Set the URL to `http://localhost:11435`
3. Save → refresh model list → `kg-rag:latest` appears
4. Start chatting

### Troubleshooting Open WebUI connection errors

| Symptom | Fix |
|---|---|
| `Server Connection Error` | Verify `curl http://localhost:11435/api/tags` returns JSON |
| Port 11435 already in use | `lsof -i :11435` — kill stale process, restart from current repo code |
| Model list empty | Refresh connections in Admin Settings after server starts |
| Answers time out | Reduce `KG_RAG_CTX_CHARS` (e.g. `3000`) or increase `KG_RAG_LLM_TIMEOUT` |
| Wrong publication metadata in answers | Wipe `splash_links/links.sqlite`, re-import KG JSON, restart KG-RAG (see **Step 4**) |
| Invented author names in answers | Expected if context lacks authors — prompt forbids fabrication; verify `Source_Metadata` in context |
| `Invalid model name` | Check `CBORG_BASE_URL` is `https://api.cborg.lbl.gov` (not `api-local`), model is `lbl/cborg-chat` (no `:latest`) |
| `Authentication failed` | Verify `CBORG_API_KEY` is set in `.env` |

---

## Docker

The root [`compose.yaml`](compose.yaml) is the canonical FAIR2WISE deployment.
It runs a private four-service stack:

- `splash-db-init` copies the tracked SQLite seed into a new persistent volume;
- `splash` serves and updates that persistent SQLite graph;
- `agent` owns chat, retrieval, extraction, and graph updates; and
- `frontend` serves the React build and reverse-proxies `/api` to the agent.

The immutable seed is `splash_links/links.sqlite`. Existing Compose volumes are
preserved after initialization. During migration from the old JSON seed, the
initializer stores a recoverable `links.pre-seed-*.sqlite` backup inside the
`splash-data` volume before replacing the legacy database.

### How images work on another computer

Docker images are machine-local build artifacts and are not committed to Git.
After someone clones this repository, Compose reads the checked-in build files
and creates equivalent images for that computer:

| Service | Image source | Persistent state |
|---|---|---|
| `frontend` | Root `Dockerfile`, `frontend` target | None |
| `agent` | Root `Dockerfile`, `agent` target | `agent-runs`, `agent-cache` |
| `splash` | `splash_links/Containerfile` | `splash-data` |
| `splash-db-init` | `splash_links/Containerfile` | Initializes `splash-data`, then exits |

The tracked `splash_links/links.sqlite` file is essential to fresh-clone
startup because it is the immutable database seed. It is mounted read-only by
the initializer and copied into the writable `splash-data` volume; the running
Splash service never modifies the repository copy.

Builds use the container platform supported by the installed Docker runtime,
so a developer does not need an image exported from another machine.

### Start, stop, and rebuild

For foreground logs on first startup:

```bash
docker compose up --build
```

For normal background operation:

```bash
docker compose up -d
docker compose ps
docker compose logs -f
```

Use `docker compose up -d --build` after pulling source or dependency changes.
Stop the stack without deleting its databases and session data with:

```bash
docker compose down
```

Only `127.0.0.1:5173` is published. Ports `8081` and `8090` are reachable only
inside the Compose network.

### Health and diagnostics

```bash
curl -fsS http://127.0.0.1:5173/healthz
curl -fsS http://127.0.0.1:5173/api/health
docker compose logs agent
docker compose logs splash
docker compose logs frontend
```

Splash data, agent sessions, and caches survive `docker compose down` in named
volumes. Inspect private services without publishing their ports:

```bash
docker compose exec agent curl -fsS http://127.0.0.1:8090/health
docker compose exec splash python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8081/splash_links/health').read().decode())"
```

### Persistent data and clean reset

| Volume | Contents |
|---|---|
| `splash-data` | Writable Splash graph initialized from the tracked SQLite seed |
| `agent-runs` | Session files and workflow state |
| `agent-cache` | Application and model caches |

To deliberately erase all Compose-managed state and initialize a clean copy of
the tracked database seed on the next startup:

```bash
docker compose down --volumes
docker compose up -d
```

This reset is destructive. Normal `docker compose down` does not erase data.

### Configuration notes

- Compose automatically reads `.env` from the repository root.
- `CBORG_API_KEY` is required; optional keys and overrides are documented in
  [`.env.example`](.env.example).
- `CBORG_IP_FAMILY` defaults to `ipv6` in Compose for CBORG trusted-network
  authorization. Set it to `auto` or `ipv4` only when appropriate for the
  machine's authorized network.
- Override the sole host port with `F2W_UI_PORT`, for example
  `F2W_UI_PORT=8080 docker compose up -d`.
- Do not set container service URLs to `localhost`: Compose supplies the
  private `splash` and `agent` hostnames internally.

Run the isolated clean-volume smoke test with:

```bash
./scripts/test_compose.sh
```

---

## Scripts

| Script | Description |
|---|---|
| `scripts/download_pdfs.py` | Download PDFs from arXiv or OpenAlex by DOI/ID |
| `app.modules.launchers.f2w_agent` | Agent KG-RAG pipeline CLI/API launcher |
| `scripts/test_chat_apis.py` | Standalone CBORG API connectivity test |
| `scripts/analyze_kgs.py` | Evaluate KG JSON files: node/edge counts, coverage, growth rates |
| `scripts/get_pdf_years.py` | Estimate publication year for PDFs; writes `pdf_years.csv` |
| `scripts/update_readme_tree.py` | Regenerate the project tree block in this README |

---

## Tests

```bash
python3 -m pytest
```

Root tests live in `tests/`; the vendored Splash Links suite lives in
`splash_links/_tests/`. The `json2kg.py` module also has inline pytest tests
that validate ID generation, field retention, and CLI behavior.

---

## Project Structure

<!-- TREE START -->
<pre>
.
├── _tests
│   └── test_example.py
├── app
│   ├── modules
│   │   ├── __init__.py
│   │   ├── agents
│   │   │   ├── __init__.py
│   │   │   ├── chebi.py
│   │   │   ├── chem_checker.py
│   │   │   └── properties.py
│   │   ├── extract_terms.py
│   │   ├── f2w_agent
│   │   │   ├── __init__.py
│   │   │   ├── cli.py
│   │   │   ├── coordinator.py
│   │   │   ├── download_agent.py
│   │   │   ├── extractor_agent.py
│   │   │   ├── kg_update.py
│   │   │   └── retrieval_agent.py
│   │   ├── launchers
│   │   │   ├── __init__.py
│   │   │   ├── academy_auth.py
│   │   │   ├── academy_extractor.py
│   │   │   ├── f2w_agent.py
│   │   │   └── user_agent.py
│   │   ├── json2kg.py
│   │   ├── kg_rag_api.py
│   │   ├── term_extractor
│   │   │   ├── academy_agent.py
│   │   │   ├── agent.py
│   │   │   ├── orchestrator.py
│   │   │   ├── provenance.py
│   │   │   ├── store.py
│   │   │   └── …
│   │   └── legacy
│   │       ├── build_onto.py
│   │       ├── extract_terms_linkml_jun3.py
│   │       ├── extract_terms_linkml.py
│   │       ├── extract_terms.py
│   │       ├── extracted_terms_json2kg_with_context.py
│   │       ├── json2kg.py
│   │       ├── kg_rag_ollama_nersc.py
│   │       └── kg_rag_ollama.py
│   └── run_pipeline_cborg.py
├── Dockerfile
├── compose.yaml
├── docker
│   └── nginx.conf
├── mkdocs
│   ├── docs
│   │   ├── about.md
│   │   ├── assets
│   │   │   ├── als_style.css
│   │   │   └── images
│   │   │       ├── doe_logo.png
│   │   │       └── lbl_logo.png
│   │   ├── core_model.md
│   │   ├── index.md
│   │   ├── test.md
│   │   └── workflow.md
│   ├── mkdocs.yml
│   └── overrides
│       ├── assets
│       │   └── images
│       │       └── favicon.png
│       └── main.html
├── polymer_papers
│   └── *.pdf
├── pytest.ini
├── README.md
├── requirements.in
├── requirements.txt
├── requirements-dev.in
├── requirements-dev.txt
├── requirements-globus.in
├── requirements-globus.txt
├── scripts
│   ├── analyze_kgs.py
│   ├── download_pdfs.py
│   ├── get_pdf_years.py
│   ├── test_compose.sh
│   ├── test_chat_apis.py
│   └── update_readme_tree.py
└── storage
    ├── competency_questions
    │   └── thomas_f.txt
    ├── kg
    │   └── *.json
    ├── schema
    │   └── matkg_schema.yaml
    └── terminology
        └── *.json
</pre>
<!-- TREE END -->

---

## Features

### GitHub Actions `.github/workflows/build-app.yml`

Automates linting, pytest, and MkDocs build on push.

### MkDocs

Documentation at `mkdocs/`. Deploy with:

```bash
cd mkdocs
mkdocs serve        # local preview
mkdocs gh-deploy    # deploy to GitHub Pages (repo must be public)
```

### `.gitignore`

Pre-configured to exclude venvs, caches, secrets, and generated artifacts.

### Python requirements

Human-edited `.in` files declare direct dependencies. Their matching `.txt`
files are Python 3.12 locks generated by pip-tools. `requirements.txt` is the
application runtime used by Docker; `requirements-dev.txt` adds tests,
formatting, linting, documentation, and lock tooling. Globus Compute and
archived-module dependencies have separate optional locks. The heavyweight
FAISS/SentenceTransformer/Torch inputs are documented in
`requirements-semantic.in` and are not installed in the lexical container.

### flake8

```bash
python3 -m flake8 app/
```

### PyTest

```bash
python3 -m pytest
```

---

## LBNL Software Disclosure and Distribution

Copyright (c) 2025, The Regents of the University of California, through Lawrence Berkeley National Laboratory (subject to receipt of any required approvals from the U.S. Dept. of Energy). All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

(1) Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

(2) Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

(3) Neither the name of the University of California, Lawrence Berkeley National Laboratory, U.S. Dept. of Energy nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

You are under no obligation whatsoever to provide any bug fixes, patches, or upgrades to the features, functionality or performance of the source code ("Enhancements") to anyone; however, if you choose to make your Enhancements available either publicly, or directly to Lawrence Berkeley National Laboratory, without imposing a separate written license agreement for such Enhancements, then you hereby grant the following license: a non-exclusive, royalty-free perpetual license to install, use, modify, prepare derivative works, incorporate into other computer software, distribute, and sublicense such Enhancements or derivative works thereof, in binary and source code form.

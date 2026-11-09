# From FAIR to WISE: Creating Knowledge Graphs from Research Papers

## Overview

This repository builds materials-science knowledge graphs from research papers (PDFs). The main workflow is:

1. Collect PDFs in the unified `papers/` input folder
2. Extract schema-aligned terminology + publication metadata with an LLM → `storage/terminology/`
3. Convert extracted terms JSON into a versioned MatKG graph JSON → `storage/kg/` (the JSON KG is the default graph source)
4. Optionally publish an approved JSON snapshot into Tiled Graph through the `/v1/publications` path: on the `:local` image into the container's own Tiled (walk-through: [`mkdocs/docs/local-image.md`](mkdocs/docs/local-image.md)); every other target stays gated at H5. The legacy `splash_links` import is kept only as the legacy editable mode, see [Step 4](#step-4--legacy-import-into-splash_links)
5. Query the graph via KG-RAG chat (web UI, CLI or Open WebUI)

**Optional — automated agent pipeline:** [`app.modules.launchers.f2w_agent`](app/modules/launchers/f2w_agent.py) launches the Academy workflow that answers from the KG when evidence suffices and otherwise searches for papers, requests approval, extracts terms, rebuilds the KG, and re-queries. See **[3-Agent KG-RAG Pipeline](#3-agent-kg-rag-pipeline)**.

---

## Current architecture (`forge_merge`)

This branch carries the FORGE-to-F2W migration (`docs/migration/plan.md`,
`docs/migration/plan_addenda.md`). What runs today:

| Topic | Current state |
|---|---|
| Graph source | **JSON KG by default.** Every supported launch path sets `KG_RAG_GRAPH_SOURCE=json` / `F2W_KG_MODE=json` (`compose.yaml`, `scripts/start_agent_backend.sh`, `scripts/start_rsoxs_stack.sh`, the three images). `config.yml` and `scripts/.env.example` default `KG_RAG_GRAPH_SOURCE` to `json` as well (owner decision 2026-11-09, Splash packet Q5); `splash` stays an accepted value for the legacy editable mode. Tiled Graph is the experiment-identity source and the publication target; Splash is legacy (D03/D16). |
| Images | Three targets from the root `Dockerfile`: `:local` bundles a managed **Tiled 0.2.18** server (owner exception O-T1 to the plan's 0.2.19 pin), the UI, MkDocs and the API/worker; `:als-dev` and `:als-prod` carry no Tiled and use the central servers (`https://tiled-staging.als.lbl.gov`, `https://tiled.als.lbl.gov`). The legacy `agent` compose target still exists and is not one of the three images. |
| Ingress | One gateway (nginx, unprivileged uid 10001) on **port 8080** (`F2W_GATEWAY_PORT`), serving the UI, MkDocs and the same-origin API under **`/fair2wise`** (`/fair2wise/api`, `/fair2wise/healthz`). The API binds `127.0.0.1:8090` inside the container and is not published by the images. |
| Runtime | `F2W_RUNTIME=legacy` (default) keeps the existing F2W read path; `F2W_RUNTIME=langgraph` enables the scoped LangGraph runtime, where `F2W_QA_ROUTE=supervisor` (default) routes UI QA turns through the planning supervisor and `F2W_QA_ROUTE=direct` calls `answer_from_knowledge` directly (owner H2 Q1, O-R1 fallback). |
| APIs | `python -m app.modules.f2w_agent.cli api` serves the 19 frozen UI routes; `api --jobs-api` serves the same routes plus the 65 `/v1` routes (48 on an ALS profile; the folder and library routes are `:local` only) for jobs, review, publication, status, validation, operations, capabilities and graph sources (`docs/migration/api/README.md`). The images run the jobs API. On `:local` publication into the image's own Tiled is wired (`app/api/local_publication.py`); every other target stays gated at H5. |
| Judge model | The validation judge for the binary suite is a deployment-wide setting chosen in the settings panel from the provider's live model list (`GET/PUT /v1/validation/judge-model`, owner O-U2); it never runs inside chat. |
| Access | `:local`: trusted local operator, no sign-in (D34). ALS images: Tiled sign-in; only verified Tiled admins may ingest, approve, publish or edit (D30). |
| Graph view and UI Save | The KG viewer has a read-only **Source** selector (`json:<name>`, `json:<corpus>/<vN>`, `tiled:<graph_id>`; `GET /v1/graph/sources`, [`ui-knowledge-graph.md`](mkdocs/docs/ui-knowledge-graph.md)). A read-only **Operations** tab shows only when `GET /v1/capabilities` reports `can_run_operations` ([`ui-operations.md`](mkdocs/docs/ui-operations.md)). On `:local`, Save is wired lazily to the active publication: the image starts in the editable mode (`--kg-mode splash`, the legacy wire token; no Splash service is contacted), so Save is available as soon as a publication exists, and before a first publication Save answers a typed 501 and writes nothing ([`local-image.md`](mkdocs/docs/local-image.md#save-on-local)). |
| Splash | The legacy editable mode (`F2W_KG_MODE=splash`, `compose.splash.yaml`, `splash_links/`). It stays until its approved retirement gate (T16B/H7 or H8, `plan_addenda.md` A2); the retirement packet is `docs/migration/reviews/H8/splash_retirement.md` and nothing is deleted yet. On a Tiled-only ALS deployment a graph Save fails safely with the legacy 502 and writes nothing (T18C F1); the broader edit design is pending owner decision (OPEN_DECISIONS A4, Q1-Q16 of `docs/migration/ui_contracts/tiled_graph_editing_plan.md`). |
| KGs in images | KGs ship in the images as flat `storage/kg/matkg_*.json` copies only (owner exception E3, errata applied 2026-11-06); nested `<corpus>/v<N>/graph.json` copies, papers, `storage/source_index/` and secrets stay out (`Dockerfile`, `.dockerignore`). |

### Running the images

[`docker/profiles/README.md`](docker/profiles/README.md) is the authority for
image build, check and run commands, mounts and profile environment; do not
copy its run commands elsewhere. Build targets:

```sh
docker build --target local    -t ghcr.io/fair2wise/fair2wise:local .
docker build --target als-dev  -t ghcr.io/fair2wise/fair2wise:als-dev .
docker build --target als-prod -t ghcr.io/fair2wise/fair2wise:als-prod .
```

A running `:local` image answers at `http://127.0.0.1:8080/fair2wise/` with
the API health at `http://127.0.0.1:8080/fair2wise/api/health`. Images are
built by `.github/workflows/build-app.yml`; publishing (`publish-image.yml`)
is manual and gated by the O06 tag rules (OPEN_DECISIONS D2). As of
2026-10-29 only `:local` has been built and run, by the owner's machine; the
2026-11-06 end-to-end rehearsal (publish, approve, roll back) ran on it.

To start `:local` from a checkout with the papers folder as the Library and
`CBORG_API_KEY` taken from `.env`:

```sh
scripts/run_local_container.sh [image] [dotenv-file]   # default image f2w:local
```

It publishes `127.0.0.1:8080` only, mounts the state and Tiled volumes
(`f2w-local-state`, `f2w-local-tiled`), `papers/` read-only at `/data/papers`
and as the read-write Library, and honors `F2W_KG_RELEASE_DIR`,
`F2W_HOST_PORT`, `F2W_CONTAINER_NAME`, `F2W_PAPERS_DIR`,
`F2W_LIBRARY_HOST_DIR` and `OPENALEX_API_KEY`. The whole flow (harvest,
extraction, export, `f2w-kg-adopt`, publication, approval, rollback) is in
[`mkdocs/docs/local-image.md`](mkdocs/docs/local-image.md). Every image also
carries the `f2w-*` console commands of `pyproject.toml` (for example
`docker exec f2w-local f2w-kg-adopt --help`).

### Running from source

The sections below describe the developer paths: the Compose developer stack
(`compose.yaml`, agent + gateway with the JSON KG), the host scripts
(`scripts/start_rsoxs_stack.sh`, `scripts/start_agent_backend.sh`,
`scripts/start_agent_frontend.sh`) and the individual CLIs. Sections marked
**legacy** describe the Splash mode.

---

## Developer stack: Docker Compose

`compose.yaml` builds the `agent` and `frontend` targets of the root
`Dockerfile` and starts the agent API and the `/fair2wise` gateway with the
JSON KG. Secrets stay in a gitignored host `.env` and are injected at
**runtime** (`env_file:`). They are never copied into the image.

```bash
git clone https://github.com/matesuu/FAIRtoWISE-FORUM-AI.git
cd FAIRtoWISE-FORUM-AI
cp .env.example .env   # set CBORG_API_KEY; leave CBORG_IPV6_BIND unset
docker compose up
```

Open `http://127.0.0.1:5175/fair2wise/` (`http://127.0.0.1:5175/` redirects
there; the gateway container listens on 8080). The UI calls the same-origin
`/fair2wise/api`; the agent is also published at `http://127.0.0.1:8090` for
development tools. **Never bind host port 5174** (SSH). 5173 is CORS-allowed
if you override `F2W_UI_PORT` (OPEN_DECISIONS B20).

Defaults: CBORG (`lbl/cborg-chat`), JSON KG
`storage/kg/matkg_rsoxs_v1.json`, schema `storage/schema/rsoxs_schema.yaml`.
CBorg uses `CBORG_FORCE_IPV6=1` / `CBORG_IP_FAMILY=ipv6` and probes a currently
assigned global IPv6 at process start. Do not persist `CBORG_IPV6_BIND`.

On **macOS / Docker Desktop**, containers usually cannot source the Mac's
authorized IPv6. Use `./scripts/start_rsoxs_stack.sh` for CBorg on Darwin, or
Linux Docker with IPv6 enabled. Optional Linux host-network overlay:
`docker compose -f compose.yaml -f compose.hostnet.yaml up`.

**Legacy:** the Splash database stack remains available as
`docker compose -f compose.splash.yaml up` (UI on 5173, `/fair2wise` gateway,
Splash and agent ports private).

See [Docker operation](#docker) for lifecycle details. The MkDocs
[fresh-machine deployment](mkdocs/docs/deployment.md) runbook covers `.env`
setup and CBORG IPv6 authorization.

### Beamline 11.0.1.2 ops KG (separate from literature)

Replay the versioned ops graph (`beamline:` namespace, not `matkg_rsoxs_v1.json`):

```bash
python3 scripts/ingest_bl1101.py --from-scratch
```

Writes the next snapshot `storage/kg/matkg_bl1101_vN.json` (v1, then v2, …; never overwrites an existing `vN`). Schema: `storage/schema/bl1101_schema.yaml`. 8090 keeps the literature KG by default; to inspect ops later, pick the new JSON under Settings → JSON graph (multi-KG fan-out is not implemented).

---

## Prerequisites

- Docker Engine or Docker Desktop with Docker Compose v2 for `docker compose up`
- A CBORG API key in an untracked `.env` (never baked into the image)


For local development without Docker:

- [Pixi](https://pixi.sh/latest/installation/) (0.66.0 wrote `pixi.lock`); it provisions Python 3.12 and the locked Python dependencies
- Node.js 20+ (including npm); npm installs the pinned local Vite 6 toolchain from `ui/package.json`
- **Legacy only:** a separate Pixi workspace for the vendored `splash_links` database server (`F2W_KG_MODE=splash`)
- A [CBORG](https://cborg.lbl.gov/) API key (default LLM backend). CBORG requires LBLnet/VPN or an authorized IP — see [CBORG IP authorization](https://api.cborg.lbl.gov/key/manage).
- Optional: [Ollama](https://ollama.com/) running locally for offline inference
- For the 3-agent pipeline: `academy-py`, `langgraph`, `langchain-core`, `langchain-openai` (included in the pixi `default` environment)

---

## Setup

### Local development setup

### 1. Clone the repository

```bash
git clone https://github.com/fair2wise/FAIRtoWISE-FORUM-AI
cd FAIRtoWISE-FORUM-AI
```

### 2. Install dependencies

Dependencies are managed by pixi (`pixi.toml` + `pixi.lock` at the repository
root). For normal runtime use, install the locked `default` environment
(includes the semantic retrieval stack):

```bash
pixi install --locked
pixi run python --version  # should report Python 3.12
```

Contributors should install the `dev` environment (runtime plus tests, lint,
type checks, and docs tooling) and run commands through it:

```bash
pixi install --locked -e dev
pixi run -e dev test
```

Install the UI dependencies, including Vite:

```bash
npm --version
cd ui
npm ci
cd ..
```

**Legacy (Splash mode only):** install Pixi and initialize the `splash_links` environment once:

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
KG_RAG_GRAPH_SOURCE=json
KG_RAG_GRAPH=storage/kg/matkg_xray_papers_cborg_chat.json
# Legacy Splash mode only (KG_RAG_GRAPH_SOURCE=splash):
KG_RAG_SPLASH_URI=splash://localhost:8081
KG_RAG_SPLASH_PAGE_SIZE=1000
KG_RAG_RETRIEVAL_BACKEND=lexical
KG_RAG_LLM_TIMEOUT=120
KG_RAG_CTX_CHARS=6000
KG_RAG_SHOW_BASELINE=0
PYSTOW_HOME=.cache/pystow

# Optional — OpenAlex polite pool (download agent)
OPENALEX_EMAIL=you@example.com

# Optional — splash_links repo path (legacy 3-agent splash mode)
SPLASH_LINKS_REPO=splash_links
SPLASH_LINKS_DB=links.sqlite

# Optional — runtime selection (defaults: legacy; supervisor)
# F2W_RUNTIME=langgraph
# F2W_QA_ROUTE=supervisor
```

> **Note:** runtime environment variables and CLI flags override `.env`; `.env` overrides `config.yml` defaults.
> Inside the images the supervisor loads `F2W_DOTENV=<file>` once and the profile locks the deployment variables (`docker/profiles/README.md`); the installed CLI reads `.env` only from the current directory (O-P1).

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

Place all research paper PDFs in `papers/`. To download papers from arXiv or OpenAlex:

```bash
python3 scripts/download_pdfs.py --help
```

---

## Step 2 — [Concept Extraction](app/modules/legacy/extract_terms_cli.py)

`extract_terms_cli.py` (archived under `app/modules/legacy/` by T17B; canonical extraction is `term_extractor/`) is a schema-aware, parallel PDF term extraction engine. It produces structured, ontology-aligned JSON output integrating:

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
python3 app/modules/legacy/extract_terms_cli.py \
  --pdf-dir papers/ \
  --output storage/terminology/extracted_terms_polymer.json
```

For a dedicated xray KG (or any other domain-specific folder):

```bash
python3 app/modules/legacy/extract_terms_cli.py \
  --pdf-dir papers/ \
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
cp papers/XRAY1.pdf /tmp/single_pdf/

python3 app/modules/legacy/extract_terms_cli.py \
  --pdf-dir /tmp/single_pdf/ \
  --output storage/terminology/extracted_terms_xray1.json
```

Show all options:

```bash
python3 app/modules/legacy/extract_terms_cli.py --help
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
  --source-dir papers \
  --pdf-root papers

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

## Step 4 — (Legacy) Import into splash_links

> **Legacy.** Splash is deprecated and replaced by Tiled Graph (D03/D16). This
> section is kept because `F2W_KG_MODE=splash` is still the only editable
> graph mode until the Tiled edit path exists (T18C F1; pending owner decision,
> OPEN_DECISIONS A4) and until the Splash retirement gate (T16B/H7 or H8).
> Every supported launch path runs the JSON KG; the images contain no Splash.

In Splash mode (`KG_RAG_GRAPH_SOURCE=splash`; the `config.yml` default is `json` since the 2026-11-09 owner decision, Splash packet Q5) KG-RAG loads from the `splash_links` graph database. **After every KG JSON rebuild or metadata fix, you must re-import into splash_links and restart KG-RAG.** The splash DB is not updated automatically when `storage/kg/*.json` changes.

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

### 4b. No Splash wipe

There is no Splash wipe or reset tool any more (owner decision Q11, T18E): `scripts/wipe_splash_db.sh` and `--allow-splash-wipe` were removed. A rebuilt corpus reaches the graph store through post-extraction publication to Tiled (a reviewed, plan-bound replacement; see "KG update modes"), and nothing is deleted outside that plan. `import_kg.py` creates new entities and does not delete old ones, so re-importing into an existing Splash database can leave duplicates; start from a fresh `links.sqlite` (stop Splash, move the file aside yourself) if you need one.

### 4c. Import the MatKG JSON

```bash
cd /path/to/f2wlocal/splash_links
pixi run python scripts/import_kg.py /path/to/f2wlocal/storage/kg/matkg_xray_papers_cborg_chat.json
```

The 3-agent pipeline no longer re-imports into Splash after extraction: `kg_update.publish_extraction()` publishes the rebuilt corpus to Tiled instead (nothing is wiped).

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

Defaults used by `start_agent_backend.sh`:

- `F2W_KG_MODE=json` (`splash` is the legacy mode; the script checks the Splash health endpoint only then)
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

### JSON mode (default for every supported launcher)

To load a local JSON file directly, which is what `compose.yaml`, the start scripts and the images do:

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
| `--graph-source` | `json` (`config.yml` default; `splash` is the legacy database) | KG source (`json` file, or the legacy `splash` database) |
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
| `KG_RAG_GRAPH_SOURCE` | `splash` in `config.yml` (legacy); `json` in `compose.yaml`, the start scripts and the three images | KG source (`json`, or the legacy `splash`, `splash_links`, `splash-links`) |
| `KG_RAG_SPLASH_URI` | `splash://localhost:8081` | `splash_links` service URI |
| `KG_RAG_SPLASH_PAGE_SIZE` | `1000` | GraphQL page size for database graph loading |
| `KG_RAG_GRAPH` | `storage/kg/matkg_xray_papers_cborg_chat.json` | KG file to load when `KG_RAG_GRAPH_SOURCE=json` (also the legacy splash fallback path) |
| `F2W_RUNTIME` | `legacy` | `legacy` or `langgraph` (scoped LangGraph runtime, T02) |
| `F2W_QA_ROUTE` | `supervisor` | In `langgraph` mode: `supervisor` (planning supervisor, T03B) or `direct` |
| `F2W_DEPLOYMENT_PROFILE` | `local` | `local`, `als-dev` or `als-prod`; fixed by the image profile (`docker/profiles/`) |
| `KG_RAG_RETRIEVAL_BACKEND` | `lexical` | Retrieval method (`lexical` or `semantic`; semantic packages ship in the default install) |
| `KG_RAG_CTX_CHARS` | `16000` | Max chars of KG context per prompt |
| `KG_RAG_LLM_TIMEOUT` | `120` | LLM request timeout in seconds |
| `KG_RAG_SHOW_BASELINE` | `0` | Set to `1` to enable baseline responses |
| `PYSTOW_HOME` | `.cache/pystow` | Local PyStow cache (avoids home-dir writes) |

### Implementation details

- Hybrid retrieval: SentenceTransformer embeddings + FAISS IVF-Flat + weighted BFS
- Lexical retrieval is the default backend. The pixi `default` environment
  includes SentenceTransformer/FAISS/Torch (CPU), so the
  default install and app image can enable `KG_RAG_RETRIEVAL_BACKEND=semantic`.
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
| `--kg-mode` | `json` | `json` = rebuild + in-process reload only (default); `splash` = legacy: use/re-import splash-links |
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

### KG update modes

**JSON mode (`--kg-mode json`, the default):**

- After each extraction pass, `json2kg.py` rebuilds `workdir/kg.json`.
- `RetrievalAgent.reload_kg()` reloads the graph in-process.
- Publication of an approved snapshot into Tiled is a separate, gated job (`/v1/publications`, H5), never an automatic side effect of extraction (H1-5).

**Legacy Splash mode (`--kg-mode splash`):**

- The `:local` image runs this mode by default (owner decision 2026-11-09; the CLI default stays `json`): the token is the editable-graph alias, backed by Tiled, and never contacts a Splash service when the `:local` Tiled save provider is attached (`mkdocs/docs/local-image.md#save-on-local`).

- Same JSON rebuild. The legacy `splash_reimport_started` / `splash_reimport_result` phases are kept and now report **post-extraction publication** (T18E, owner decision Q11): `v<N+1>` is frozen, a corpus-scoped replacement plan is computed and applied under the profile's approval policy. `:local` auto-approves **only a safe plan** (no boundary links, no legacy adoption) as the local operator with `reason: auto_safe_plan`; an unsafe plan waits for review; on ALS the plan always waits for one verified admin (never auto-approved). Without a configured Tiled publication target the phase reports `skipped`.
- There is no Splash wipe and no `--allow-splash-wipe`; node edits keep using the non-destructive Splash entity/link calls when no Tiled Save target is configured.
- Requires a running splash-links server and a local `splash_links` checkout (`SPLASH_LINKS_REPO`) for those edits.

### Extractor package (`term_extractor`)

The extractor agent uses [`app/modules/term_extractor/`](app/modules/term_extractor/), ported from the FAIR2WISE `bowen/academy_agent` branch with **local provenance patches**:

- Output JSON includes top-level `code_snippets` and per-term `source_metadata` (source-scoped publication fields).
- Publication metadata comes **only** from PDF-derived extraction (`provenance.py`); the term LLM never stamps authors/DOI/year.
- Code snippets come from embedded PDF code blocks and explicit GitHub repository links found in PDF text; set optional `GITHUB_TOKEN` for higher GitHub API rate limits.
- KG conversion uses the existing [`json2kg.py`](app/modules/json2kg.py) (preserves `source_metadata` + CodeSnippet nodes).

The legacy standalone extractor [`extract_terms_cli.py`](app/modules/legacy/extract_terms_cli.py) remains available for manual/batch runs. The monitored remote `TermExtractorAgent` + dashboard stack in `term_extractor/` is available for standalone Globus/NERSC extraction, but is **not** used by the local `app.modules.launchers.f2w_agent` loop.

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

For the full three-agent NERSC path, install only the pixi `default` environment. Install the isolated `globus` environment (`pixi install --locked -e globus`) only when configuring the extractor-only Globus Compute endpoint path.

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

In a separate terminal (outside the Open WebUI venv). `--graph-source json` is the `config.yml` default; the legacy `splash` source needs a running splash-links server, see **Step 4**:

```bash
cd /path/to/FAIRtoWISE-FORUM-AI
python3 app/modules/kg_rag_api.py --api --graph-source json --graph storage/kg/matkg_rsoxs_v1.json
```

This starts FastAPI on `http://127.0.0.1:11435` (loopback only; set `KG_RAG_API_HOST=0.0.0.0` or pass `--api-host 0.0.0.0` to serve the network). Since T28A the two chat routes of this model-compatible API apply the same disclosure projection as the UI routes (AUD-03, OPEN_DECISIONS B6); the API is not exposed under `/fair2wise` on ALS (B7). Verify it is running:

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

Two different things run in Docker:

1. **The three release images** `:local`, `:als-dev`, `:als-prod` (root
   `Dockerfile` targets `local`, `als-dev`, `als-prod`): one container, one
   supervisor, gateway on 8080 under `/fair2wise`. Commands, mounts and checks
   are in [`docker/profiles/README.md`](docker/profiles/README.md) (see
   [Current architecture](#current-architecture-forge_merge)).
2. **The developer Compose stack** below (`compose.yaml`): the legacy `agent`
   target plus the standalone `frontend` gateway image with the RSoXS JSON
   knowledge graph. `docker compose up` publishes loopback ports **5175**
   (gateway, UI at `/fair2wise/`) and **8090** (agent). Secrets come from host
   `.env` via `env_file:` and are never baked into the image.

**Legacy:** the Splash database stack is optional:

```bash
docker compose -f compose.splash.yaml up
```


### How the Compose images work on another computer

Docker images are machine-local build artifacts and are not committed to Git.
After someone clones this repository, Compose reads the checked-in build files
and creates equivalent images for that computer:

| Service | Compose file | Image source | Persistent state |
|---|---|---|---|
| `frontend` | `compose.yaml`, `compose.splash.yaml` | Root `Dockerfile`, `frontend` target (nginx gateway, container port 8080) | None |
| `agent` | `compose.yaml`, `compose.splash.yaml` | Root `Dockerfile`, `agent` target (legacy API-only target, root, no supervisor) | `agent-runs`, `agent-cache` |
| `splash` (legacy) | `compose.splash.yaml` | `splash_links/Containerfile` | `splash-data` |
| `splash-db-init` (legacy) | `compose.splash.yaml` | `splash_links/Containerfile` | Initializes `splash-data`, then exits |

**Legacy:** the tracked `splash_links/links.sqlite` file is the immutable
database seed of the Splash stack. It is mounted read-only by the initializer
and copied into the writable `splash-data` volume; the running Splash service
never modifies the repository copy. The three release images contain no
Splash, no catalog database and no `.env`.

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

Only `127.0.0.1:5175` (gateway; UI at `/fair2wise/`) and `127.0.0.1:8090`
(agent) are published. Never bind host **5174** (SSH). Override the UI port with
`F2W_UI_PORT` (5173 is CORS-allowed).


### Health and diagnostics

```bash
curl -fsS http://127.0.0.1:5175/healthz
curl -fsS http://127.0.0.1:5175/fair2wise/api/health
curl -fsS http://127.0.0.1:8090/health
docker compose logs agent
docker compose logs frontend
```


### Persistent data and clean reset

| Volume | Contents |
|---|---|
| `agent-runs` | Session files and workflow state |
| `agent-cache` | Application and model caches |

To deliberately erase all Compose-managed state (and, in the legacy Splash
stack, re-initialize the tracked database seed) on the next startup:

```bash
docker compose down --volumes
docker compose up -d
```

This reset is destructive. Normal `docker compose down` does not erase data.

### Configuration notes

- Compose reads secrets from the host `.env` via `env_file:` at container start.
  Do not `COPY .env` or ARG the API key into the image.
- `CBORG_API_KEY` is required in `.env`; optional keys are listed in
  [`.env.example`](.env.example).
- `CBORG_FORCE_IPV6=1` and `CBORG_IP_FAMILY=ipv6` are set by Compose. Leave
  `CBORG_IPV6_BIND` unset so the agent probes a live global IPv6. A stale bind
  fails with "Can't assign requested address".
- Default UI port is `5175`. Never use `5174`. Example:
  `F2W_UI_PORT=5173 docker compose up -d`.
- On Darwin, Docker Desktop typically cannot source the Mac's authorized IPv6;
  run `./scripts/start_rsoxs_stack.sh` for CBorg, or use Linux Docker.


Run the isolated clean-volume smoke test with:

```bash
./scripts/test_compose.sh
```

---

## Scripts

| Script | Description |
|---|---|
| `scripts/download_pdfs.py` | Download PDFs from arXiv or OpenAlex by DOI/ID |
| `scripts/run.py` | Run local modular term extraction against a PDF directory |
| `app.modules.launchers.f2w_agent` | Agent KG-RAG pipeline CLI/API launcher |
| `scripts/start_rsoxs_stack.sh` | Daemonize RSoXS JSON agent+UI (5175/8090); does not stop extract |
| `scripts/start_agent_backend.sh` | Start the agent API used by the UI |
| `scripts/run_local_container.sh` | Start the `:local` image on `127.0.0.1:8080` with the papers folder as the Library |
| `python -m app.api.cli --root DIR ops list\|summarize\|statistics\|...` | Knowledge operations on the CLI (local profile only), see [`ui-operations.md`](mkdocs/docs/ui-operations.md) |
| `scripts/test_chat_apis.py` | Standalone CBORG API connectivity test |
| `scripts/analyze_kgs.py` | Evaluate KG JSON files: node/edge counts, coverage, growth rates |
| `scripts/get_pdf_years.py` | Estimate publication year for PDFs; writes `pdf_years.csv` |
| `scripts/update_readme_tree.py` | Regenerate the project tree block in this README |

---

## Tests

```bash
pixi run -e dev test         # fast: pytest -n auto -m "not slow"
pixi run -e dev test-slow    # the full-size scale tests (nightly in CI)
```

Root tests live in `tests/`; the vendored Splash Links suite lives in
`splash_links/_tests/`. About 270 tests need an isolated Tiled 0.2.18 server and
skip unless `F2W_TILED_PYTHON` is set. Run from a clean `git archive` export
under `env -i` with the variables written inline (a shell variable holding the
assignments is not word-split by zsh and silently drops `F2W_TILED_PYTHON`).
The recipe, the zsh trap and the docs checks are in
[`mkdocs/docs/testing.md`](mkdocs/docs/testing.md).

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
│   │       ├── __init__.py
│   │       ├── build_onto.py
│   │       ├── extract_terms_cli.py
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
├── papers
│   └── *.pdf
├── pixi.toml
├── pixi.lock
├── pytest.ini
├── README.md
├── scripts
│   ├── analyze_kgs.py
│   ├── download_pdfs.py
│   ├── get_pdf_years.py
│   ├── run.py
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

### GitHub Actions

`.github/workflows/build-app.yml` runs the legacy test suite and builds and
checks the `local`, `als-dev` and `als-prod` image candidates (T24D), writing a
release manifest; it never logs in to a registry. `publish-image.yml` publishes
only on manual dispatch under the O06 tag rules (pending owner decision,
OPEN_DECISIONS D2). `forge-merge-ci.yml` runs the migration test suite,
including the Tiled 0.2.18 suites. See [`mkdocs/docs/release.md`](mkdocs/docs/release.md).

### MkDocs

Documentation at `mkdocs/`. The site is also built into all three images and
served by the gateway under `/fair2wise/docs/` (owner O-A7). Deploy with:

```bash
cd mkdocs
mkdocs serve        # local preview
mkdocs gh-deploy    # deploy to GitHub Pages (repo must be public)
```

Check the site the way CI and the image build do with
`mkdocs build -f mkdocs/mkdocs.yml --strict` from the repository root.

Maintainers should use the MkDocs [contributor guide](mkdocs/docs/contributing.md)
for branch, lock, validation, API/graph/agent/UI extension workflows; the
[release guide](mkdocs/docs/release.md) for versioning, container publishing,
dependency upgrades, and database compatibility; and the
[performance guide](mkdocs/docs/performance.md) for large graphs, retrieval,
context, workers, bundle size, caches, and volumes.

### `.gitignore`

Pre-configured to exclude venvs, caches, secrets, and generated artifacts.

### Python requirements

Dependencies are managed by [pixi](https://pixi.sh). `pixi.toml` and
`pixi.lock` at the repository root are the single source of truth (Python 3.12;
platforms linux-64, linux-aarch64, osx-arm64). The lock records the sha256 of
every conda package and PyPI artifact.

| Environment | Install command | Purpose |
|---|---|---|
| `default` | `pixi install --locked` | FAIR2WISE application runtime, including CPU torch, FAISS, and SentenceTransformer retrieval |
| `dev` | `pixi install --locked -e dev` | `default` plus pytest, black, flake8, DeepEval, mypy, ruff, isort, pytest-cov, and MkDocs |
| `docs` | `pixi install --locked -e docs` | MkDocs only |
| `globus` | `pixi install --locked -e globus` | Isolated Academy/Globus Compute endpoint dependencies (conflicts with linkml-runtime) |
| `legacy` | `pixi install --locked -e legacy` | `default` plus dependencies of archived modules |
| `tiled-server` | `pixi install --locked -e tiled-server` | Isolated Tiled 0.2.18 server (owner pin O-T1; needs pydantic-settings<2.12) |

Run commands through an environment with `pixi run -e dev <task>`. Tasks
(defined under `[feature.*.tasks]` in `pixi.toml`): `test`, `lint`,
`typecheck`, `ruff`, `isort-check`, `coverage`, `docs`. Anything else:
`pixi run -e dev python -m pytest ...`.

Docker images and CI install with `--locked`, so any package the lock does not
describe is refused. The app environment is built in the `app-env-build` stage
and copied into `runtime-base`; the Tiled server environment is built in
`tiled-server-build`. CI installs pixi with `scripts/ci/install_pixi.sh`
(pinned release, sha256-verified).

To change a dependency, edit `pixi.toml`, run `pixi lock` with pixi 0.66.0 (the
version that wrote the lock; the Dockerfile and `scripts/ci/install_pixi.sh`
use the same pin), and commit `pixi.lock`. Do not hand-edit the lock.
Application pins are exact; the semantic retrieval stack (faiss-cpu,
sentence-transformers, torch CPU build) is unpinned in `pixi.toml` because its
wheels are platform-specific, and is fixed by `pixi.lock`. `pyproject.toml`
extras remain as PyPI packaging metadata.

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

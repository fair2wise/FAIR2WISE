# From FAIR to WISE: Creating Knowledge Graphs from Research Papers

## Overview

This repository builds materials-science knowledge graphs from research papers (PDFs). The main workflow is:

1. Collect PDFs into `polymer_papers/`
2. Extract schema-aligned terminology with an LLM → `storage/terminology/`
3. Convert extracted terms JSON into a MatKG graph JSON → `storage/kg/`
4. Query the graph via KG-RAG chat (CLI or Open WebUI)

---

## Prerequisites

- Python 3.10+ (Python 3.12 recommended; Python 3.14 supported with lexical retrieval only)
- A [CBORG](https://cborg.lbl.gov/) API key (default LLM backend)
- Optional: [Ollama](https://ollama.com/) running locally for offline inference

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/fair2wise/FAIRtoWISE-FORUM-AI
cd FAIRtoWISE-FORUM-AI
```

### 2. Install dependencies

```bash
pip3 install -r requirements.txt
```

Runtime packages required by application code (not yet in `requirements.txt`) can be installed directly:

```bash
pip3 install aiohttp arxiv colorama faiss-cpu fastapi linkml-runtime \
             mp-api numpy obonet openai pyalex pymatgen PyMuPDF \
             python-dotenv PyYAML rapidfuzz rdflib requests \
             sentence-transformers torch uvicorn
```

### 3. Configure environment

Copy the example env file and fill in your credentials:

```bash
cp scripts/.env.example .env
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
KG_RAG_GRAPH=storage/kg/matkg_qwen3_235b_580papers.json
KG_RAG_RETRIEVAL_BACKEND=lexical
KG_RAG_LLM_TIMEOUT=120
KG_RAG_CTX_CHARS=6000
KG_RAG_SHOW_BASELINE=0
PYSTOW_HOME=.cache/pystow
```

> **Note:** `load_dotenv(override=True)` is used throughout, so `.env` values always take precedence over any shell environment variables.

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

- Ollama or CBORG (OpenAI-compatible) LLM backends
- LinkML schema enforcement via `SchemaHelper`
- Chemical formula validation and repair
- ChEBI ontology enrichment
- Physical property extraction and normalization
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

```bash
python3 app/run_pipeline_cborg.py
```

This runs the checkpoint evaluation pipeline (25 → 50 → 75 → 100 papers), producing timestamped JSON files in `storage/terminology/` and converted KG files in `storage/kg/`.

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

### Implementation details

- Pages processed in parallel with up to 50 workers
- Terms saved after every page (crash-safe)
- `SchemaHelper` fuzzy-matches LLM output to LinkML classes/slots
- `ChemicalFormulaValidator` validates and LLM-repairs invalid formulas
- ChEBI lookup enriches chemicals with SMILES, InChI, charge, roles
- `PhysicalPropertyExtractor` + `PropertyNormalizer` detect and standardize numerical properties
- Duplicate terms merged via LLM-guided fuzzy comparison
- 50-token provenance snippets link every node back to its source page and paper

---

## Step 3 — [Convert to Knowledge Graph](app/modules/json2kg.py)

Converts the extracted terms JSON into a MatKG-compatible JSON graph with `things` (nodes) and `associations` (edges).

```bash
python3 app/modules/json2kg.py \
  --input storage/terminology/extracted_terms_<run>.json \
  --output storage/kg/matkg_<run>.json
```

With verbose output:

```bash
python3 app/modules/json2kg.py \
  --input storage/terminology/extracted_terms_<run>.json \
  --output storage/kg/matkg_<run>.json \
  --verbose
```

### Implementation details

- Stable canonical IDs via `matkg:` prefix + regex-cleaned term name
- Full metadata preserved: formula, validation, properties, provenance
- Missing edge targets auto-stubbed to prevent dangling edges
- Edges carry optional evidence strings
- Duplicate `(subject, predicate, object)` edges de-duplicated
- Integrated pytest suite validates ID generation, field retention, and CLI

---

## Step 4 — [KG-RAG LLM Chat](app/modules/kg_rag_ollama_api.py)

Query the knowledge graph via retrieval-augmented generation. Supports CLI, one-shot, competency evaluation, and an Open WebUI-compatible FastAPI server.

### CLI — interactive REPL

```bash
python3 app/modules/kg_rag_ollama_api.py
```

Prompt appears:

```
Ask (exit to quit):
```

### CLI — one-shot question

```bash
python3 app/modules/kg_rag_ollama_api.py \
  --question "What is the role of P3HT crystallinity in OPV performance?"
```

With a shorter timeout:

```bash
python3 app/modules/kg_rag_ollama_api.py \
  --timeout 60 \
  --question "What is P3HT?"
```

Reduce context size if responses are slow:

```bash
KG_RAG_CTX_CHARS=3000 python3 app/modules/kg_rag_ollama_api.py \
  --timeout 60 \
  --question "What is P3HT?"
```

### CLI — use a specific model

```bash
# CBORG (default)
python3 app/modules/kg_rag_ollama_api.py \
  --model lbl/cborg-chat \
  --question "What is P3HT?"

# Nova Micro (cheaper/faster)
python3 app/modules/kg_rag_ollama_api.py \
  --model nova-micro \
  --question "What is P3HT?"

# Ollama (local)
python3 app/modules/kg_rag_ollama_api.py \
  --backend ollama \
  --model deepseek-r1:70b \
  --question "What is P3HT?"
```

### CLI — use a specific KG

```bash
python3 app/modules/kg_rag_ollama_api.py \
  --graph storage/kg/matkg_lbl_cborg-chat_latest_100_20251008_010852.json \
  --question "What materials show high PCE?"
```

### CLI — show baseline (non-RAG) answer alongside KG-RAG

```bash
python3 app/modules/kg_rag_ollama_api.py \
  --show-baseline \
  --question "What is P3HT?"
```

### CLI — competency question evaluation

```bash
python3 app/modules/kg_rag_ollama_api.py --competency
```

Runs the full question set from `storage/competency_questions/thomas_f.txt`. Results saved incrementally to `storage/competency_questions/competency_results_qwen3_235b_580papers.json`.

### CLI argument reference

| Argument | Default | Description |
|---|---|---|
| `--graph` | `KG_RAG_GRAPH` env | Path to KG JSON file |
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
| `KG_RAG_BACKEND` | `cborg` | LLM backend (`cborg` or `ollama`) |
| `KG_RAG_CBORG_MODEL` | `lbl/cborg-chat` | CBORG model name |
| `KG_RAG_OLLAMA_MODEL` | `deepseek-r1:70b` | Ollama model name |
| `KG_RAG_GRAPH` | `storage/kg/matkg_qwen3_235b_580papers.json` | KG file to load |
| `KG_RAG_RETRIEVAL_BACKEND` | `lexical` (Python 3.14), `semantic` otherwise | Retrieval method |
| `KG_RAG_CTX_CHARS` | `16000` | Max chars of KG context per prompt |
| `KG_RAG_LLM_TIMEOUT` | `120` | LLM request timeout in seconds |
| `KG_RAG_SHOW_BASELINE` | `0` | Set to `1` to enable baseline responses |
| `PYSTOW_HOME` | `.cache/pystow` | Local PyStow cache (avoids home-dir writes) |

### Implementation details

- Hybrid retrieval: SentenceTransformer embeddings + FAISS IVF-Flat + weighted BFS
- Lexical retrieval available (no FAISS/Torch) for Python 3.14 stability
- Multi-factor node scoring: semantic similarity, graph depth, lexical overlap, evidence count
- Context blocks include KG triples, formulas, descriptions, and PDF snippets (page-cached)
- Question decomposition for multi-clause queries
- Missing-node tracking logged to `storage/knowledge_gaps/`
- FastAPI proxy exposes `/api/chat`, `/api/tags`, `/api/ps` (Open WebUI-compatible)
- GPU auto-detect with CPU fallback for embeddings

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

In a separate terminal (outside the Open WebUI venv):

```bash
cd /path/to/FAIRtoWISE-FORUM-AI
python3 app/modules/kg_rag_ollama_api.py --api
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
| `Invalid model name` | Check `CBORG_BASE_URL` is `https://api.cborg.lbl.gov` (not `api-local`), model is `lbl/cborg-chat` (no `:latest`) |
| `Authentication failed` | Verify `CBORG_API_KEY` is set in `.env` |

---

## Docker

The container runs the KG-RAG FastAPI server on port `11435` using Python 3.12 (avoids Python 3.14 native ML stack instability). CBORG is the default backend. `storage/` is mounted as a volume so KG files and outputs persist outside the container.

### 1. Build the image

```bash
docker build -t kg-rag-api .
```

### 2. Run the API server

```bash
docker run -d \
  --name kg-rag \
  -p 11435:11435 \
  -e CBORG_API_KEY=your-cborg-api-key \
  -v $(pwd)/storage:/app/storage \
  kg-rag-api
```

Verify it is running:

```bash
curl http://localhost:11435/api/tags
```

Expected:

```json
{"models":[{"name":"kg-rag:latest","model":"kg-rag:latest","modified_at":"2025-09-17T00:00:00Z"}]}
```

### 3. Run a one-shot question

```bash
docker run --rm \
  -e CBORG_API_KEY=your-cborg-api-key \
  -v $(pwd)/storage:/app/storage \
  kg-rag-api \
  python3 app/modules/kg_rag_ollama_api.py \
    --question "What is P3HT?" \
    --timeout 60
```

### 4. Run term extraction pipeline

```bash
docker run --rm \
  -e CBORG_API_KEY=your-cborg-api-key \
  -v $(pwd)/storage:/app/storage \
  -v $(pwd)/polymer_papers:/app/polymer_papers \
  kg-rag-api \
  python3 app/run_pipeline_cborg.py
```

### 5. Override defaults

All `KG_RAG_*` env vars can be overridden at runtime:

```bash
docker run -d \
  --name kg-rag \
  -p 11435:11435 \
  -e CBORG_API_KEY=your-cborg-api-key \
  -e KG_RAG_CBORG_MODEL=nova-micro \
  -e KG_RAG_CTX_CHARS=3000 \
  -e KG_RAG_LLM_TIMEOUT=60 \
  -v $(pwd)/storage:/app/storage \
  kg-rag-api
```

### 6. Mount ChEBI ontology (optional)

```bash
docker run -d \
  --name kg-rag \
  -p 11435:11435 \
  -e CBORG_API_KEY=your-cborg-api-key \
  -v $(pwd)/storage:/app/storage \
  -v $(pwd)/storage/ontologies:/app/storage/ontologies \
  kg-rag-api
```

### 7. View logs

```bash
docker logs -f kg-rag
```

### 8. Stop and remove

```bash
docker stop kg-rag && docker rm kg-rag
```

---

## Scripts

| Script | Description |
|---|---|
| `scripts/download_pdfs.py` | Download PDFs from arXiv or OpenAlex by DOI/ID |
| `scripts/test_chat_apis.py` | Standalone CBORG API connectivity test |
| `scripts/analyze_kgs.py` | Evaluate KG JSON files: node/edge counts, coverage, growth rates |
| `scripts/get_pdf_years.py` | Estimate publication year for PDFs; writes `pdf_years.csv` |
| `scripts/update_readme_tree.py` | Regenerate the project tree block in this README |

---

## Tests

```bash
python3 -m pytest
```

Tests live in `_tests/`. The `json2kg.py` module also has inline pytest tests that validate ID generation, field retention, and CLI behavior.

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
│   │   ├── json2kg.py
│   │   ├── kg_rag_ollama_api.py
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
├── requirements.txt
├── scripts
│   ├── analyze_kgs.py
│   ├── download_pdfs.py
│   ├── get_pdf_years.py
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

### `requirements.txt`

Dev tooling dependencies: `black`, `flake8`, `mkdocs`, `mkdocs-material`, `pre-commit`, `pytest`.

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

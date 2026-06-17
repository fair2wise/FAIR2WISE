# From FAIR to WISE: Weaving Intelligent Scientific Ecosystems

## Getting Started

Clone and/or fork this repository.

```bash
git clone https://github.com/fair2wise/FAIR2WISE
cd FAIR2WISE
```

Use **Python 3.12** for local installs. The torch / faiss / sentence-transformers stack is unstable on Python 3.14.

```bash
pip install -r requirements.txt
```

All LLM calls in this branch go to a local [Ollama](https://ollama.com/) instance. No API keys are required for Ollama.

**Reviewer default model:** `llama3.2:latest` — used in `docker-compose.yml`, `.env.example`, `kg_rag_ollama_api.py`, and the `extract_terms.py` `__main__` block. It is a lightweight choice for quick smoke tests (~2 GB). The paper's reported extraction/RAG results used `qwen3:235b`, which requires substantial memory (Mac Studio M3 Ultra, 256 GB in the paper).

**Code default mismatch:** if you construct `LLMTermExtractor(...)` directly without `model_name=`, the class default is still `gemma3:27b`. Use `python app/modules/extract_terms.py`, `run_extraction(model=...)`, or pass `model_name=` explicitly to use `llama3.2:latest`.

## LinkML "Core Model" Schema

An example core model for organic photovoltaics is in [storage/schema/matkg_schema.yaml](storage/schema/matkg_schema.yaml). Concept extraction uses this schema in the LLM call to keep results structured and relevant.

## Pipeline Overview

The reproducible workflow has four entry points, run in order:

1. [scripts/download_pdfs.py](scripts/download_pdfs.py) — fetch PDFs into a corpus directory
2. [app/modules/extract_terms.py](app/modules/extract_terms.py) — extract schema-aligned terms from PDFs
3. [app/modules/json2kg.py](app/modules/json2kg.py) — convert extracted terms JSON to a MatKG graph
4. [app/modules/kg_rag_ollama_api.py](app/modules/kg_rag_ollama_api.py) — KG-grounded chat (CLI or Open WebUI-compatible API)

The default knowledge graph is `storage/kg/matkg_qwen3_235b_580papers.json`. PDF snippet grounding reads from `polymer_papers/` at runtime (see [Corpus data](#corpus-data) — PDFs are not redistributed).

## Corpus data

Full-text PDFs are **not** included in this repository (publisher copyright). In their place, [polymer_papers/corpus_manifest.csv](polymer_papers/corpus_manifest.csv) lists the OPV corpus used in the paper: one row per paper with `filename`, `identifier_type` (`arxiv` or `doi`), and `identifier` (580 papers; filenames match those referenced in the bundled KG).

| What you can run without local PDFs | What needs PDFs in `polymer_papers/` |
|-------------------------------------|----------------------------------------|
| KG-RAG chat/API using the bundled graph (`storage/kg/…`) — KG triples, node text, and descriptions still ground answers | `extract_terms.py` — full re-extraction pipeline |
| Competency evaluation against the pre-built graph | PDF snippet blocks in KG-RAG context (`KG_RAG_PDF_DIR`) |

To obtain PDFs for re-extraction or snippet grounding, download them yourself (respecting publisher terms) into `polymer_papers/` using [scripts/download_pdfs.py](scripts/download_pdfs.py) or any source that matches the manifest filenames/identifiers. The manifest is the authoritative list of which papers the bundled graph was built from.

---

## [Download PDFs](scripts/download_pdfs.py)

Fetch papers from arXiv or OpenAlex by keyword into a local directory. Use this (or your own access) to populate `polymer_papers/` before running extraction; filenames should align with [polymer_papers/corpus_manifest.csv](polymer_papers/corpus_manifest.csv) when reproducing the paper corpus.

```bash
python scripts/download_pdfs.py --keyword "organic photovoltaics" --target ./polymer_papers
```

| Flag | Description | Default |
|------|-------------|---------|
| `--keyword` | Search term (required) | — |
| `--target` | Output directory for PDFs | `./pdfs` |
| `--max-results` | Maximum papers to download | `500` |
| `--source` | `arxiv` or `openalex` | `arxiv` |

---

## [Concept Extraction](app/modules/extract_terms.py)

Schema-aware terminology extraction from scientific PDFs. Integrates Ollama, LinkML schema validation, ChEBI enrichment, chemical-formula repair, and parallel page-level processing.

**Requires PDF files** in `data_dir` (not just the manifest). See [Corpus data](#corpus-data).

### Quick run (`__main__`)

```bash
python app/modules/extract_terms.py
```

Runs the `if __name__ == "__main__"` block with these defaults:

| Setting | Default |
|---------|---------|
| `model_name` | `llama3.2:latest` |
| `ollama_base_url` | `http://localhost:11434` |
| `data_dir` | `./polymer_papers` |
| `output_file` | `./storage/terminology/extracted_terms_aug21.json` |
| `schema_path` | `./storage/schema/matkg_schema.yaml` |
| `max_workers` | `4` |

To change paths, model, or workers for a one-off run, edit those values in the `__main__` block.

### Programmatic run (`run_extraction`)

For a different folder, model, or output path without editing source:

```python
# from repo root: PYTHONPATH=app python your_script.py
from pathlib import Path
from modules.extract_terms import run_extraction

run_extraction(
    Path("./polymer_papers"),
    Path("./storage/terminology/my_terms.json"),
    model="llama3.2:latest",
    ollama_url="http://localhost:11434",
    schema_path="storage/schema/matkg_schema.yaml",
    temperature=0.0,
    context_length=50,
    max_workers=4,
)
```

Key arguments: `pdf_dir`, `output_json`, `model`, `ollama_url`, `schema_path`, `temperature`, `context_length`, `max_workers`.

Ollama must be running with the model pulled (e.g. `ollama pull llama3.2`).

`llama3.2:latest` is a lightweight default for quick reviewer testing and will produce a sparse graph; the paper's reported results used `qwen3:235b`, which requires substantial memory (Mac Studio M3 Ultra, 256 GB in the paper).

**Optional:** set `MP_API_KEY` in `.env` for Materials Project chemical-formula validation/enrichment. Without it, local formula parsing still runs; Materials Project cross-check is skipped.

---

## [Convert to KG](app/modules/json2kg.py)

Transform enriched terms JSON into a MatKG-compatible graph (nodes + edges).

```bash
python app/modules/json2kg.py \
    storage/terminology/extracted_terms_aug21_580papers.json \
    storage/kg/my_graph.json
```

| Argument / flag | Description |
|-----------------|-------------|
| `input_json` | Path to extracted-terms JSON (positional) |
| `output_json` | Path for output graph JSON (positional) |
| `--verbose`, `-v` | Increase logging verbosity |

---

## [KG-RAG LLM Chat](app/modules/kg_rag_ollama_api.py)

Hybrid semantic + graph retrieval over a local KG JSON file, with optional PDF snippet grounding and an Ollama-compatible FastAPI server for [Open WebUI](https://docs.openwebui.com/).

PDF snippets are loaded from `KG_RAG_PDF_DIR` (default `polymer_papers/`) when matching files exist; without local PDFs, retrieval still uses KG structure and node metadata from the graph JSON. See [Corpus data](#corpus-data).

### CLI flags

| Flag | Description |
|------|-------------|
| `--graph` | Path to KG JSON (default: `storage/kg/matkg_qwen3_235b_580papers.json`) |
| `--question` | One-shot question, then exit |
| `--competency` | Run full competency-question evaluation set |
| `--api` | Start FastAPI server on port 11435 |

### Interactive REPL

```bash
python app/modules/kg_rag_ollama_api.py
```

### One-shot question

```bash
python app/modules/kg_rag_ollama_api.py \
    --question "What is the role of P3HT crystallinity in OPV performance?"
```

### Competency evaluation

```bash
python app/modules/kg_rag_ollama_api.py --competency
```

Reads questions from `storage/competency_questions/thomas_f.txt` and writes incremental results under `storage/competency_questions/`.

### API server (Open WebUI)

```bash
python app/modules/kg_rag_ollama_api.py --api
```

Listens on `http://0.0.0.0:11435`. Exposes `/api/chat`, `/api/tags`, and `/api/ps` for Open WebUI's Ollama connection.

### Custom graph

```bash
python app/modules/kg_rag_ollama_api.py \
    --graph storage/kg/matkg_qwen3_235b_580papers.json \
    --question "How does annealing influence phase separation in P3HT:PCBM?"
```

### Environment variables (`KG_RAG_*`)

Read by `kg_rag_ollama_api.py` via `os.environ.get`. Compose and `.env.example` default `KG_RAG_OLLAMA_MODEL` to `llama3.2:latest` (matches the code default in `kg_rag_ollama_api.py`).

| Variable | Code default | Purpose |
|----------|---------|---------|
| `KG_RAG_OLLAMA_MODEL` | `llama3.2:latest` | Ollama model name |
| `KG_RAG_OLLAMA_URL` | `http://localhost:11434/api/chat` | Ollama chat endpoint |
| `KG_RAG_GRAPH` | `storage/kg/matkg_qwen3_235b_580papers.json` | Default KG JSON path |
| `KG_RAG_PDF_DIR` | `polymer_papers` | Directory of source PDFs |
| `KG_RAG_TOPK` | `12` | Semantic search top-k |
| `KG_RAG_EMBED_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformer model |
| `KG_RAG_BATCH` | (unset) | Optional embedding batch override |
| `KG_RAG_SNIP` | `1000` | PDF snippet length (chars) |
| `KG_RAG_CTX_CHARS` | `16000` | Total context character budget |
| `KG_RAG_FORCE_CPU` | (unset) | Force CPU for embeddings/FAISS |
| `KG_RAG_MAX_TEXT_CHARS` | `1024` | Max chars per node text field |
| `KG_RAG_ENABLE_BFS` | `1` | Enable BFS graph expansion |
| `KG_RAG_BFS_TOPK` | `24` | BFS seed top-k |
| `KG_RAG_MAX_HOPS` | `1` | Max BFS hops |
| `KG_RAG_STEPWISE` | `1` | Enable stepwise query decomposition |
| `KG_RAG_STEPWISE_MAX_STEPS` | `6` | Max decomposition steps |
| `KG_RAG_GENERIC_PENALTY` | `0.8` | Penalty for generic node labels |
| `KG_RAG_CONTEXT_VOLUME` | `150` | Max triples in context block |
| `KG_RAG_STRUCT_CTX` | `1` | Include structured context |
| `KG_RAG_DEBUG` | `0` | Verbose debug logging |
| `KG_RAG_PDF_CACHE` | `256` | LRU PDF page cache size |

---

## Docker

Runtime data is **not** baked into the image: `storage/` (graphs, terminology) and `polymer_papers/` (manifest and any PDFs you add locally) are volume-mounted. The repo ships `polymer_papers/corpus_manifest.csv` only; PDFs are not in git or the image.

1. Copy env defaults: `cp .env.example .env` and adjust if needed.
2. Ensure Ollama is running on the host with the target model pulled (e.g. `ollama pull llama3.2`).
3. Start services:

```bash
docker compose up --build
```

| Service | URL | Notes |
|---------|-----|-------|
| `kg-rag` | `http://127.0.0.1:11435` | KG-RAG FastAPI (localhost only) |
| `open-webui` | `http://127.0.0.1:8080` | Chat UI; `OLLAMA_BASE_URL=http://kg-rag:11435` inside compose |

Volumes: `./storage` and `./polymer_papers` are mounted into `kg-rag` (manifest from the clone; add PDFs locally for snippet grounding). Open WebUI state persists in the `open-webui` named volume.

---

## Project Structure
<!-- TREE START -->
<pre>
.
├── Dockerfile
├── LICENSE.txt
├── README.md
├── _tests
│   └── test_example.py
├── app
│   ├── modules
│   │   ├── __init__.py
│   │   ├── __pycache__
│   │   │   ├── __init__.cpython-311.pyc
│   │   │   └── extract_terms.cpython-311.pyc
│   │   ├── agents
│   │   │   ├── __init__.py
│   │   │   ├── chebi.py
│   │   │   ├── chem_checker.py
│   │   │   └── properties.py
│   │   ├── extract_terms.py
│   │   ├── json2kg.py
│   │   ├── kg_rag_ollama_api.py
│   │   └── legacy
│   │       ├── build_onto.py
│   │       ├── extract_terms.py
│   │       ├── extract_terms_linkml.py
│   │       ├── extract_terms_linkml_jun3.py
│   │       ├── extracted_terms_json2kg_with_context.py
│   │       ├── json2kg.py
│   │       ├── kg_rag_ollama.py
│   │       └── kg_rag_ollama_nersc.py
│   └── run_pipeline_cborg.py
├── docker-compose.yml
├── mkdocs
│   ├── docs
│   │   ├── about.md
│   │   ├── assets
│   │   │   ├── als_style.css
│   │   │   └── images
│   │   │       ├── doe_logo.png
│   │   │       └── lbl_logo.png
│   │   ├── core_model.md
│   │   ├── index.md
│   │   ├── test.md
│   │   └── workflow.md
│   ├── mkdocs.yml
│   └── overrides
│       ├── assets
│       │   └── images
│       │       └── favicon.png
│       └── main.html
├── polymer_papers
│   └── corpus_manifest.csv
├── pytest.ini
├── requirements.txt
├── scripts
│   ├── analyze_kgs.py
│   ├── download_pdfs.py
│   ├── get_pdf_years.py
│   ├── test_chat_apis.py
│   └── update_readme_tree.py
└── storage
    ├── competency_questions
    │   └── thomas_f.txt
    ├── kg
    │   ├── matkg_deepseek-r1_14b_100_20250918_095748.json
    │   ├── matkg_deepseek-r1_14b_25_20250915_185643.json
    │   ├── matkg_deepseek-r1_14b_50_20250916_162508.json
    │   ├── matkg_deepseek-r1_14b_75_20250917_143348.json
    │   ├── matkg_deepseek-r1_32b_100_20250922_191851.json
    │   ├── matkg_deepseek-r1_32b_25_20250919_065133.json
    │   ├── matkg_deepseek-r1_32b_50_20250920_125000.json
    │   ├── matkg_deepseek-r1_32b_75_20250921_180642.json
    │   ├── matkg_deepseek-r1_70b_100_20250929_004942.json
    │   ├── matkg_deepseek-r1_70b_25_20250925_103657.json
    │   ├── matkg_deepseek-r1_70b_50_20250926_144206.json
    │   ├── matkg_deepseek-r1_70b_75_20250927_214641.json
    │   ├── matkg_google_gemini-flash-lite_100_20251008_230232.json
    │   ├── matkg_google_gemini-flash-lite_25_20251008_185312.json
    │   ├── matkg_google_gemini-flash-lite_50_20251008_200746.json
    │   ├── matkg_google_gemini-flash-lite_75_20251008_213611.json
    │   ├── matkg_gpt-oss_120b_100_20250925_002056.json
    │   ├── matkg_gpt-oss_120b_25_20250923_213915.json
    │   ├── matkg_gpt-oss_120b_50_20250924_042317.json
    │   ├── matkg_gpt-oss_120b_75_20250924_135625.json
    │   ├── matkg_gpt-oss_20b_100_20251001_115740.json
    │   ├── matkg_gpt-oss_20b_25_20250930_172105.json
    │   ├── matkg_gpt-oss_20b_50_20251001_025118.json
    │   ├── matkg_gpt-oss_20b_75_20251001_115100.json
    │   ├── matkg_lbl_cborg-chat_latest_100_20251008_010852.json
    │   ├── matkg_lbl_cborg-chat_latest_25_20251007_224848.json
    │   ├── matkg_lbl_cborg-chat_latest_50_20251007_232938.json
    │   ├── matkg_lbl_cborg-chat_latest_75_20251008_001702.json
    │   ├── matkg_qwen3_235b_100_20251004_054233.json
    │   ├── matkg_qwen3_235b_147papers.json
    │   ├── matkg_qwen3_235b_257papers.json
    │   ├── matkg_qwen3_235b_25_20251001_120436.json
    │   ├── matkg_qwen3_235b_333papers.json
    │   ├── matkg_qwen3_235b_361papers.json
    │   ├── matkg_qwen3_235b_444papers.json
    │   ├── matkg_qwen3_235b_50_20251002_095006.json
    │   ├── matkg_qwen3_235b_580papers.json
    │   ├── matkg_qwen3_235b_75_20251003_084431.json
    │   └── view_kg.html
    ├── schema
    │   └── matkg_schema.yaml
    └── terminology
        ├── extracted_terms_aug21_147papers.json
        ├── extracted_terms_aug21_257papers.json
        ├── extracted_terms_aug21_333papers.json
        ├── extracted_terms_aug21_361papers.json
        ├── extracted_terms_aug21_444papers.json
        ├── extracted_terms_aug21_580papers.json
        ├── extracted_terms_cp25_deepseek-r1_20250908_201314.json
        ├── extracted_terms_cp25_deepseek-r1_20250908_212738.json
        ├── extracted_terms_cp25_deepseek-r1_20250908_224024.json
        ├── extracted_terms_cp25_deepseek-r1_20250910_113328.json
        ├── extracted_terms_cp50_deepseek-r1_20250912_174500.json
        ├── extracted_terms_cp50_deepseek-r1_20250912_192346.json
        ├── extracted_terms_deepseek-r1_14b_100_20250918_095748.json
        ├── extracted_terms_deepseek-r1_14b_25_20250915_185643.json
        ├── extracted_terms_deepseek-r1_14b_50_20250916_162508.json
        ├── extracted_terms_deepseek-r1_14b_75_20250917_143348.json
        ├── extracted_terms_deepseek-r1_32b_100_20250922_191851.json
        ├── extracted_terms_deepseek-r1_32b_25_20250919_065133.json
        ├── extracted_terms_deepseek-r1_32b_50_20250920_125000.json
        ├── extracted_terms_deepseek-r1_32b_75_20250921_180642.json
        ├── extracted_terms_deepseek-r1_70b_100_20250929_004942.json
        ├── extracted_terms_deepseek-r1_70b_25_20250925_103657.json
        ├── extracted_terms_deepseek-r1_70b_50_20250926_144206.json
        ├── extracted_terms_deepseek-r1_70b_75_20250927_214641.json
        ├── extracted_terms_google_gemini-flash-lite_100_20251008_230232.json
        ├── extracted_terms_google_gemini-flash-lite_25_20251008_185312.json
        ├── extracted_terms_google_gemini-flash-lite_50_20251008_200746.json
        ├── extracted_terms_google_gemini-flash-lite_75_20251008_213611.json
        ├── extracted_terms_gpt-oss_120b_100_20250925_002056.json
        ├── extracted_terms_gpt-oss_120b_25_20250923_213915.json
        ├── extracted_terms_gpt-oss_120b_50_20250924_042317.json
        ├── extracted_terms_gpt-oss_120b_75_20250924_135625.json
        ├── extracted_terms_gpt-oss_20b_100_20250930_160658.json
        ├── extracted_terms_gpt-oss_20b_100_20251001_115740.json
        ├── extracted_terms_gpt-oss_20b_25_20250930_032030.json
        ├── extracted_terms_gpt-oss_20b_25_20250930_172105.json
        ├── extracted_terms_gpt-oss_20b_50_20250930_110510.json
        ├── extracted_terms_gpt-oss_20b_50_20251001_025118.json
        ├── extracted_terms_gpt-oss_20b_75_20250930_160006.json
        ├── extracted_terms_gpt-oss_20b_75_20251001_115100.json
        ├── extracted_terms_lbl_cborg-chat_latest_100_20251008_010852.json
        ├── extracted_terms_lbl_cborg-chat_latest_25_20251007_224848.json
        ├── extracted_terms_lbl_cborg-chat_latest_50_20251007_232938.json
        ├── extracted_terms_lbl_cborg-chat_latest_75_20251008_001702.json
        ├── extracted_terms_qwen3_235b_100_20251004_054233.json
        ├── extracted_terms_qwen3_235b_25_20251001_120436.json
        ├── extracted_terms_qwen3_235b_50_20251002_095006.json
        ├── extracted_terms_qwen3_235b_75_20251003_084431.json
        └── parse_terms.py

21 directories, 133 files
</pre>
<!-- TREE END -->

## LBNL Software Disclosure and Distribution

Copyright (c) 2025, The Regents of the University of California, through Lawrence Berkeley National Laboratory (subject to receipt of any required approvals from the U.S. Dept. of Energy). All rights reserved.
 
Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:
 
(1) Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
 
(2) Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
 
(3) Neither the name of the University of California, Lawrence Berkeley National Laboratory, U.S. Dept. of Energy nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.
 
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 
You are under no obligation whatsoever to provide any bug fixes, patches, or upgrades to the features, functionality or performance of the source code ("Enhancements") to anyone; however, if you choose to make your Enhancements available either publicly, or directly to Lawrence Berkeley National Laboratory, without imposing a separate written license agreement for such Enhancements, then you hereby grant the following license: a non-exclusive, royalty-free perpetual license to install, use, modify, prepare derivative works, incorporate into other computer software, distribute, and sublicense such Enhancements or derivative works thereof, in binary and source code form.

# HANDOFF

Date: 2026-06-03
Repo: `/Users/mateo/Desktop/f2wlocal`
Mode: caveman terse

---

## User Goal

Make KG-RAG CLI chat use CBORG like term extraction. Do not hardwire chat to Ollama. Make CBORG default. Fix dependency/env/runtime issues blocking CLI chat. Keep Open WebUI pointed at KG-RAG on `11435`.

Latest user goal: collect SAXS/WAXS/GISAXS/GIWAXS algorithm/code resources as PDFs, extract terms/snippets from those PDFs, and create new KG JSON outputs.

---

## Completed This Session

### 1. CBORG connectivity fixed

- `CBORG_BASE_URL` in `.env` was pointing to `api-local.cborg.lbl.gov` (internal/VPN-only) — times out externally. Changed to `https://api.cborg.lbl.gov`.
- Model name `lbl/cborg-chat:latest` rejected by CBORG API. Correct name is `lbl/cborg-chat` (no `:latest`). Fixed in `.env`, `Dockerfile`, `scripts/.env.example`, and code default fallback in `kg_rag_api.py`.
- `load_dotenv()` → `load_dotenv(override=True)` in all three entry points so `.env` always wins over stale shell env vars:
  - `app/modules/kg_rag_api.py`
  - `app/modules/extract_terms.py`
  - `app/run_pipeline_cborg.py`

### 2. run_pipeline_cborg.py bad import fixed

```python
# was:
from modules.extract_terms_cborg import run_extraction
# fixed:
from modules.extract_terms import run_extraction
```

### 3. KG-RAG one-shot verified working

```bash
KG_RAG_CTX_CHARS=3000 python3 app/modules/kg_rag_api.py \
  --timeout 60 --question "What is P3HT?"
```

Output:
- KG loaded (15815 nodes, retrieval=lexical)
- 12 nodes selected
- CBORG responded with full grounded answer citing KG nodes
- No segfault, no PDF warnings, no timeout

### 4. KG-RAG API server running on 11435

```bash
python3 app/modules/kg_rag_api.py --api
```

- PID 20518 (as of 2026-06-02 session)
- `curl http://localhost:11435/api/tags` returns `kg-rag:latest`
- Open WebUI connected at `http://127.0.0.1:8080` (PID 20542)

### 5. scripts/analyze_kgs.py deduplicated

File had entire script body duplicated. First copy had incomplete 12-file list; second had complete 30-file list. Running unchanged would:
- Execute twice silently
- Write CSV/JSON twice (second overwrites first)
- First pass produce misleading partial comparative summary
Fixed by keeping only the complete second copy.

### 6. requirements.txt completed

All runtime deps added with `>=` version floors. Split into runtime/dev sections. `pip check` clean.

### 7. README rewritten

Full comprehensive setup guide:
- Prerequisites, clone, install, `.env` config
- ChEBI download note
- Full CLI arg + env var reference tables
- Open WebUI setup and troubleshooting table
- Docker steps (build, run, one-shot, pipeline, overrides, ChEBI mount, logs, stop)
- All `python` → `python3`, all `pip` → `pip3`

### 8. Dockerfile updated

- `KG_RAG_CBORG_MODEL=lbl/cborg-chat` (no `:latest`)
- Added `CBORG_BASE_URL=https://api.cborg.lbl.gov`
- Added `KG_RAG_CTX_CHARS=6000`
- `CMD python` → `CMD python3`
- `mkdir` now includes `storage/ontologies`

### 9. docs.md added (renamed from ref.md)

Repo reference document renamed `ref.md` → `docs.md`. All references in `HANDOFF.md` updated. Last updated note added.

### 10. Git committed (cd89460)

All changes committed to `main`. Push blocked by missing GitHub auth (HTTPS remote, no PAT/SSH configured). Commit is ready — just needs auth to push.

### 11. Algorithm/resource PDFs added

New PDFs in `polymer_papers/`:

| File | Purpose |
|---|---|
| `polymer_papers/2111.08645.pdf` | ArXiv paper from `resources.txt`: "Machine Learning-Assisted Analysis of Small Angle X-ray Scattering". Downloaded from listed arXiv URL. |
| `polymer_papers/resources_code_snippets.pdf` | Generated PDF from repo-local `resources_code_snippets.md`; contains algorithm/code snippets from resources/libs listed in `resources.txt` plus maintained docs for scattering/peak workflows. |
| `polymer_papers/scipy_docs.pdf` | Generated PDF compiling SciPy 1D peak-finding algorithms and snippets: `find_peaks`, `find_peaks_cwt`, `peak_prominences`, `peak_widths`, `argrelextrema`, `argrelmax`, `argrelmin`. |

Also added:

| File | Purpose |
|---|---|
| `resources_code_snippets.md` | Markdown source used to generate `resources_code_snippets.pdf`. |

Blocked downloads:
- IUCr GISAXS CNN page from `resources.txt` returned HTTP 403.
- ScienceDirect peak-detection page from `resources.txt` returned HTTP 403.
- User clarified: only use information from `resources.txt`; do not use outside mirrors.

### 12. Targeted extraction outputs

Successful/partial extraction files:

| File | Status |
|---|---|
| `storage/terminology/extracted_terms_resources_saxs_algorithms_20260603_181341.json` | Successful for `2111.08645.pdf`: 11 terms, 5/6 pages yielded terms. `resources_code_snippets.pdf` did not complete before user interruption. |
| `storage/terminology/extracted_terms_resources_code_snippets_20260603_182046.json` | Partial/ongoing at interruption: page 1 yielded 1 `xray_code_snippets` item; page 2 was running when interrupted. |
| `storage/terminology/extracted_terms_resources_saxs_algorithms_20260603_181120.json` | Failed sandbox run: CBORG connection errors, 0 terms. Ignore/delete later if desired. |
| `storage/kg/matkg_resources_saxs_algorithms_20260603_181120.json` | Empty KG from failed sandbox run. Ignore/delete later if desired. |

Terms confirmed from `2111.08645.pdf` include:
- Small angle X-ray scattering (SAXS)
- Wide angle X-ray scattering (WAXS)
- SCAN
- SASView
- Debye-Anderson-Brumberger (DAB) Model
- Polymer Excluded Volume model
- Teubner-Strey model
- Random Forest
- XGBoost

### 13. Runtime fix made during extraction

`app/modules/agents/chebi.py` changed so missing optional `storage/ontologies/chebi.obo` raises `FileNotFoundError` instead of calling `sys.exit(1)`.

Reason: `extract_terms.py` already catches exceptions and disables ChEBI lookup, but `sys.exit(1)` killed extraction before fallback.

### 14. README KG command fixed

README had wrong `json2kg.py` CLI:

```bash
python3 app/modules/json2kg.py --input INPUT --output OUTPUT
```

Actual CLI uses positional args:

```bash
python3 app/modules/json2kg.py INPUT OUTPUT
```

README now reflects actual `app/modules/json2kg.py` parser.

---

## Current State

### Services

| Service | PID | URL | Status |
|---|---|---|---|
| KG-RAG API | 20518 | `http://localhost:11435` | Running |
| Open WebUI | 20542 | `http://127.0.0.1:8080` | Running |

### .env (current values, no secrets)

```env
CBORG_BASE_URL=https://api.cborg.lbl.gov
KG_RAG_BACKEND=cborg
KG_RAG_CBORG_MODEL=lbl/cborg-chat
KG_RAG_GRAPH=storage/kg/matkg_qwen3_235b_580papers.json
KG_RAG_RETRIEVAL_BACKEND=lexical
KG_RAG_LLM_TIMEOUT=120
KG_RAG_SHOW_BASELINE=0
PYSTOW_HOME=.cache/pystow
```

> Note: `KG_RAG_CTX_CHARS` not yet in `.env` — set via shell or add manually. Recommend `6000`.

### Git state

```
branch: main
last commit: cd89460
push: pending (no GitHub auth configured)
```

Untracked (not committed, not ignored):
- `.venv-open-webui/` — Open WebUI venv, large, should stay untracked
- `instructions.md` — local doc
- `schema.md` — local doc
- `storage/knowledge_gaps/` — generated artifacts

---

## Remaining Issues

| # | Issue | Priority |
|---|---|---|
| 1 | `KG_RAG_CTX_CHARS` not in `.env` | Low — add `KG_RAG_CTX_CHARS=6000` |
| 2 | ChEBI `.obo` missing | Low — enrichment silently disabled; download ~500MB if needed |
| 3 | Test coverage shallow | Low — `_tests/` only has dummy `add()` test |
| 4 | GitHub push blocked | Medium — needs PAT or SSH key configured |
| 5 | Ctrl+C on API server throws error | Low — `KeyboardInterrupt` not caught in `--api` path; wrap `uvicorn.run()` in `try/except KeyboardInterrupt: sys.exit(0)` |

---

## CLI Usage

Default CBORG one-shot:
```bash
python3 app/modules/kg_rag_api.py --question "What is P3HT?"
```

Interactive REPL:
```bash
python3 app/modules/kg_rag_api.py
```

Reduced context (faster):
```bash
KG_RAG_CTX_CHARS=3000 python3 app/modules/kg_rag_api.py \
  --timeout 60 --question "What is P3HT?"
```

Ollama override:
```bash
python3 app/modules/kg_rag_api.py \
  --backend ollama --model llama3.1:8b \
  --question "What is P3HT?"
```

Baseline + KG-RAG:
```bash
python3 app/modules/kg_rag_api.py \
  --show-baseline --question "What is P3HT?"
```

Start API server:
```bash
python3 app/modules/kg_rag_api.py --api
```

Term extraction pipeline:
```bash
python3 app/run_pipeline_cborg.py
```

Extract terms from all PDFs in `polymer_papers/` into one combined JSON:

```bash
cd /Users/mateo/Desktop/f2wlocal && python3 -c 'from pathlib import Path; import os, sys; from dotenv import load_dotenv; sys.path.insert(0, str(Path("app").resolve())); load_dotenv(dotenv_path=Path(".env"), override=True); from modules.extract_terms import run_extraction; print(run_extraction(Path("polymer_papers"), Path("storage/terminology/extracted_terms_all_pdfs.json"), model=os.environ.get("KG_RAG_CBORG_MODEL") or "lbl/cborg-chat", backend="cborg", cborg_base=os.environ.get("CBORG_BASE_URL") or "https://api.cborg.lbl.gov", cborg_api_key=os.environ.get("CBORG_API_KEY"), schema_path="storage/schema/matkg_schema.yaml", temperature=0.0, context_length=80, max_workers=1))'
```

Create KG from combined extraction JSON:

```bash
python3 app/modules/json2kg.py \
  storage/terminology/extracted_terms_all_pdfs.json \
  storage/kg/matkg_all_pdfs.json
```

Create KG from latest extraction JSON:

```bash
TERMS=$(ls -t storage/terminology/extracted_terms_*.json | head -1)
KG="storage/kg/matkg_manual.json"
python3 app/modules/json2kg.py "$TERMS" "$KG"
```

Check extraction success:

```bash
python3 - <<'PY'
import json
data = json.load(open("storage/terminology/extracted_terms_all_pdfs.json"))
print("terms:", len(data.get("terms", [])))
print("xray_code_snippets:", len(data.get("xray_code_snippets", [])))
print("processed_files:", data.get("metadata", {}).get("processed_files"))
print("processed_pages_total:", data.get("metadata", {}).get("processed_pages_total"))
print("processed_pages_with_terms:", data.get("metadata", {}).get("processed_pages_with_terms"))
PY
```

Use new KG in KG-RAG:

```bash
KG_RAG_GRAPH=storage/kg/matkg_all_pdfs.json python3 app/modules/kg_rag_api.py \
  --question "What peak finding algorithms are available for SAXS or WAXS data?"
```

---

## Restart Services (if terminals closed)

```bash
cd /Users/mateo/Desktop/f2wlocal

# KG-RAG API
python3 app/modules/kg_rag_api.py --api &

# Open WebUI (installed at system Python 3.12)
/Users/mateo/Library/Python/3.12/bin/open-webui serve --host 127.0.0.1 --port 8080 &
```

Verify:
```bash
curl http://localhost:11435/api/tags
```

---

## Files Changed This Session

| File | Change |
|---|---|
| `app/modules/kg_rag_api.py` | `load_dotenv(override=True)`, model default fixed, code default fixed |
| `app/modules/extract_terms.py` | `load_dotenv(override=True)` |
| `app/modules/agents/chebi.py` | Missing optional ChEBI OBO now raises catchable `FileNotFoundError` instead of exiting extraction |
| `app/run_pipeline_cborg.py` | Bad import fixed, `load_dotenv(override=True)` |
| `scripts/analyze_kgs.py` | Duplicate body removed |
| `requirements.txt` | All runtime deps added with version floors |
| `README.md` | Full rewrite — comprehensive setup, CLI/env tables, Docker, ChEBI; later fixed `json2kg.py` CLI docs to positional args |
| `Dockerfile` | Model name, base URL, CTX_CHARS, python3, storage/ontologies |
| `.env` | CBORG_BASE_URL and KG_RAG_CBORG_MODEL corrected |
| `scripts/.env.example` | Model name fixed, KG_RAG_CTX_CHARS added |
| `.env.example` | Root copy synced with scripts/.env.example |
| `.gitignore` | Added `.webui_secret_key` |
| `.dockerignore` | New file |
| `docs.md` | New file (renamed from ref.md) |
| `resources_code_snippets.md` | New markdown source for algorithm/code-snippet PDF |
| `polymer_papers/2111.08645.pdf` | New SAXS ML paper from `resources.txt` |
| `polymer_papers/resources_code_snippets.pdf` | New generated snippets PDF for extraction/KG |
| `polymer_papers/scipy_docs.pdf` | New generated SciPy peak-finding docs PDF for extraction/KG |

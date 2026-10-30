# ADR: Minimal import, dependency extras and the parser boundary

Status: Accepted at H1 (2026-10-12) (T01; `docs/migration/reviews/H1/decision.json` H1-8: the ADR set is the H1 baseline)
Date: 2026-10-12
Superseded/amended by: H1-2 (distribution `fair2wise`, import namespace `f2w`, T04 `ff4beac`). O-T1: the managed Tiled server environment pins 0.2.18, not 0.2.19rc2. O-A7 (2026-10-12): the MkDocs site is built into all three images. AUD-08 fix `a32e752`: every image carries CPU-only torch and the two MiniLM models offline (`HF_HUB_OFFLINE=1`); profiles default `KG_RAG_RETRIEVAL_BACKEND=lexical`. Item 6 (`pip check` passes): the app environment currently fails `pip check` (huggingface-hub vs click), pending owner decision (OPEN_DECISIONS B25). Docling (item 3 `[parser]`) is pending owner decision (OPEN_DECISIONS B1).

## Context

Plan section 4.5: a minimal import must not initialize models, touch a corpus,
read `.env` files, open Tiled, build process pools or register tools. Heavy
components are isolated by extras/profiles, but the accepted full release must
install and exercise runtime, FastAPI, Tiled Graph and paper figures together.

### Observed

| Finding | Evidence |
|---|---|
| Importing `f2w_agent.api` takes about 4.8 s and imports torch, faiss, sentence_transformers, fitz (PyMuPDF), openai and academy | packaging prototype (`package-and-imports.md`) |
| 7 modules call `load_dotenv()` at import | `f2w_baseline_inventory.json` (`load_dotenv_at_import`) |
| `kg_rag_api.py` reads model/timeout env once at import (L107-117) | `f2w_llm_qa_baseline.json` INF items |
| `cborg_limiter.CBORG_MAX_CONCURRENCY` read once at import | `cborg_limiter.py:25-33` |
| Tiled 0.2.19rc2 requires `pydantic-settings<2.12`; the F2W lock pins `pydantic-settings==2.14.2` | `tiled-deployment.md`; `requirements/runtime.txt:306` |
| Docling is to replace PyMuPDF (owner, H0 E2); the plan records a Docling/Tiled pydantic-settings conflict in the donor environment | `license_dependency_audit.md` E2; plan section 3 |

## Decision

1. **Import layers.** `f2w` and `f2w.public` import only contracts and
   lightweight facades. Domain modules import heavy libraries lazily, inside
   the function or factory that needs them. `f2w.api.main.create_app()`
   imports route modules but defers model, embedding, parser and Tiled client
   construction to application startup.
2. **No import-time configuration.** Environment and config files are read by
   an explicit settings loader called at application startup, CLI entry, or
   test fixture setup. Values are passed down; nothing reads `os.environ` per
   request as mutable configuration.
3. **Extras** (names proposed): `fair2wise[api]` (FastAPI, uvicorn),
   `[agents]` (LangGraph, langchain-openai), `[semantic]` (sentence-transformers,
   FAISS, torch), `[parser]` (Docling, later replacing PyMuPDF), `[tiled]`
   (Tiled client), `[figures]` (paper-figure kernels), and `[full]` = all of
   them. Release acceptance uses `[full]` (plan T04).
4. **Tiled server is not an F2W Python dependency.** The application talks to
   Tiled over HTTP. The managed `:local` Tiled server runs in its own
   environment inside the image (`tiled-deployment.md`), so its
   `pydantic-settings<2.12` pin does not constrain the app lock.
5. **Parser worker boundary.** If Docling cannot co-install with the app lock,
   Docling runs in a separately locked worker environment with an explicit
   request/response contract. An ordinary subprocess of the same interpreter
   does not count as isolation (plan section 3).
6. **No `--no-deps`** and no transplanted donor lock. Every supported install
   passes `pip check`.

## Consequences

- T04 must measure import time and imported-module sets as acceptance tests
  (the contracts package already has such a test: `test_import_has_no_side_effects`).
- Moving env reads out of import time changes when configuration is read.
  Each move needs a parity test, because `kg_rag_api` and `cborg_limiter`
  behavior depends on import-time values today.

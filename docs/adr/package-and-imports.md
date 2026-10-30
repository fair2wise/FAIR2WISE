# ADR: Installable `f2w` namespace over the physical `app/` tree

Status: Accepted at H1 (2026-10-12) (T01; `docs/migration/reviews/H1/decision.json` H1-8: the ADR set is the H1 baseline)
Date: 2026-10-12
Superseded/amended by: approved verbatim as H1-2 ("install as fair2wise, import as f2w, with no files moved"); implemented by T04 (`ff4beac`). O-P1 (2026-10-14): the installed CLI reads `.env` only from the current directory; remaining import-time settings move to startup in one later cleanup task (T17B). AUD-02: import-time `load_dotenv()` still remains in `app/run_pipeline_cborg.py`, `scripts/run.py` and the stale `scripts/app/modules/kg_rag_api.py` copy (T17B/T28C).
Decision owner: project owner. Implemented by T04 after H2.

## Context

- D02 keeps code in `app/modules/`; no src-layout move. D07 requires FORGE to
  import F2W as a Python package and over FastAPI.
- FORGE's reduction service already has a top-level `app` package, so F2W must
  not install a generic `app` into a FORGE environment (plan section 4.3).
- The plan forbids loading one stateful module under two names, broad
  `sys.path` manipulation and `sys.modules` alias hacks.

### Observed at `22362e0` (HEAD; baseline `222b1e7` is the same for these points)

| Finding | Evidence |
|---|---|
| No `pyproject.toml`, `setup.py` or `app/__init__.py`; `app/modules/legacy/` has no `__init__.py` | repo root listing |
| 55 absolute `from app.modules...` imports in 21 files (28 nested inside functions); 7 `from modules...` imports; 81 relative imports | `git grep` counts |
| `f2w_agent/__init__.py:21-24` inserts the repo root, `app/` and `scripts/` into `sys.path` | source |
| `extract_terms.py:26` inserts `app/` into `sys.path`, then imports `modules.cborg_http`, `modules.agents.*` | source |
| `download_agent.py:32,674` imports `scripts/download_pdfs.py` as top-level `download_pdfs` | source |
| Schemas, KGs, `config.yml` are found through repo-root-relative paths, outside `app/` | `hybrid_rag/pipeline.py:25-44`, `json2kg.py`, `project_config.py` |
| **Double load today**: importing `app.modules.f2w_agent.api` and `app.modules.extract_terms` in one process loads `cborg_http` twice (`app.modules.cborg_http is not modules.cborg_http`) | runtime probe on clean export |

## Decision

1. **Distribution name** `fair2wise`; **canonical import namespace** `f2w`,
   mapped onto the physical tree with setuptools `package-dir = {"f2w" = "app"}`.
   No file moves. Public entry points become `f2w.public`, `f2w.api.main`,
   `f2w.modules.*` (plan section 4.3).
2. **All in-package imports become relative.** Rewrite the 55 `app.modules`
   and 7 `modules.` imports inside `app/modules/` to relative form. Relative
   imports resolve under whichever top-level name loaded the package, so the
   same code serves the installed `f2w` wheel and existing checkout launches
   that use `app.modules...` (`python -m app.modules.launchers...`, tests,
   `scripts/deploy_nersc.sh`).
3. **One process, one name.** A process uses `f2w.*` (installed) or `app.*`
   (checkout), never both. A guard in `f2w/__init__.py` raises if the other
   name is already in `sys.modules`. No alias finder and no `sys.modules`
   rewriting.
4. **Remove both `sys.path` hacks.** They add `site-packages/f2w`,
   `site-packages/scripts` and `site-packages/app` to `sys.path` when installed,
   which would let `import modules.x` resolve to a second copy.
5. **Scripts become facades over packaged modules (D12).** Code that
   `app/modules` imports from `scripts/` (today: `download_pdfs`) moves into
   `app/modules/...`. The script keeps its CLI and imports the canonical
   module. Tests patch the canonical module.
6. **Package data and resource discovery.** Approved schema/vocabulary assets
   ship as package data or resolve from an explicit configured root. Lookup
   must not depend on the current working directory or repo layout (T04).
7. **`app/run_pipeline_cborg.py`** stays a checkout-only script (it is outside
   `app.modules` and relies on `app/` being on `sys.path`). It is not packaged.
8. **No import-time `load_dotenv()`** in library code (see
   `optional-dependencies.md`). Only CLI entry points may load a `.env`, and
   only explicitly.

## Prototype evidence

Built in a scratch copy of HEAD; scripts are in `evidence/packaging/`.

| Check | Result |
|---|---|
| `rewrite_imports.py` | 59 imports rewritten to relative across `app/modules`; `run_pipeline_cborg.py` skipped |
| Wheel contents (`pyproject.prototype.toml`) | 72 entries; only `f2w/` and dist-info; no top-level `app` |
| Fresh venv install from runtime lock, `uv pip check` | compatible |
| Import `f2w.modules.f2w_agent.api`, `extract_terms`, `kg_rag_api`, `term_extractor.orchestrator` from outside the checkout | no `app.*`, no `modules.*`, no top-level script modules loaded; one `cborg_http` |
| Fake FORGE `app` package first on `sys.path` | untouched (`sys.modules["app"]` is still FORGE's) |
| `sys.path` after import | still gains `site-packages/f2w`, `/scripts`, `/app`: decision 4 is required |
| Full pytest on rewritten tree in checkout mode | 732 passed. One new failure: `test_download_agent_rate_limits_between_failed_urls`, because the prototype copied `download_pdfs` into the package while the test patched the top-level copy. Two module objects meant the patch missed and the test made real HTTP requests. Decision 5 resolves this. |
| `.env` in a project directory above the venv | **loaded** by `load_dotenv()` at import: an F2W install inside FORGE's repo would read FORGE's `.env`. Decision 8 is required. |
| Import cost of `f2w_agent.api` | 4.8 s; pulls in torch, FAISS, SentenceTransformers, PyMuPDF, openai, Academy (see `optional-dependencies.md`) |

## Consequences

- T04 performs the mechanical rewrite as its own commit (one mechanical move
  per commit, BRANCH_WORKFLOW section 3), with the full suite as the gate.
- 147 test imports keep `app.modules...` (checkout mode). String patch targets
  in tests (39) keep working because checkout mode still names modules `app.*`.
- Acceptance tests for T04: wheel and editable install outside the checkout;
  no `app`/`modules` top-level names; FORGE-collision fixture; one registry per
  application instance; old launchers still start.

## Open questions for H1

- Confirm the names `fair2wise` (distribution) and `f2w` (import namespace).
- Confirm checkout launches via `app.modules...` stay supported for the
  migration window (recommended), with removal a later owner decision.

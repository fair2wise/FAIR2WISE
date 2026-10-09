# H0 license, data and dependency audit (T00 step 5)

Audited 2026-10-09 against the frozen baseline `222b1e7`. Static audit of file
notices, package metadata and tracked assets. Not legal advice. Items marked
**escalate** need an owner decision before the work they affect.

## Escalations

### E1: Publisher PDFs tracked in a public repository (escalate)

- `papers/` tracks 90 PDFs (206 MB) at `222b1e7`, first added in `3109a2d`
  (2026-08-06). 83 have DOI prefix `10.1002` (Wiley journals: Adv. Sci.,
  Adv. Mater., Adv. Funct. Mater.). The other 7 are local names
  (`XRAY1-3`, `XRAY_MISC1`, `SCIPY_DOCS`, `PYFAI_DOCS`, `MISC_DOCS`).
- `fair2wise/FAIR2WISE` and `matesuu/FAIRtoWISE-FORUM-AI` are both **public**
  (GitHub API, 2026-10-09). The PDFs are therefore publicly redistributed now.
- This conflicts with D19 (source papers stay agent-private), D28 (papers are
  deployment-mounted) and D31 (papers stay out of Git). Per-article reuse
  licenses were not checked. Some Wiley articles are open access under
  CC BY; others are not.
- `.dockerignore` excludes `papers`, so they are not baked into images.
- **Not changed by this audit.** Removing the files from the tip is a normal
  commit. Removing them from history is a rewrite and force-push, which the
  plan forbids without explicit approval. Owner decides: keep, remove from the
  tip, or purge history (coordinated with the upstream repo owners).

### E2: PyMuPDF is AGPL-3.0 (escalate)

- `PyMuPDF>=1.27.2` is a direct runtime dependency (`requirements/runtime.in`
  line 21). The locked build is `pymupdf 1.28.0`, licensed
  "GNU AFFERO GPL 3.0 or Artifex Commercial License".
- It is imported in 10 modules at `222b1e7`: `kg_rag_api.py`, `extract_terms.py`,
  `term_extractor/provenance.py`, `term_extractor/orchestrator.py`,
  `f2w_agent/paper_evidence_agent.py`, `f2w_agent/download_agent.py`,
  `hybrid_rag/chunker.py` and three `legacy/` modules.
- The `:als-dev`/`:als-prod` images (D23-D25) serve the application over a
  network. AGPL section 13 then requires offering corresponding source to
  users. The repo is public, but the obligation also covers modifications and
  deployed versions. FORGE also uses PyMuPDF (plan L-12/L-13), so the
  migration does not introduce this. It does widen deployment.
- Owner decides whether AGPL compliance is acceptable (for example, a source
  link in the UI) or whether an Artifex license or an alternative parser is
  needed. Raise at H1 with the dependency lock (T04).

### E3: Corpus data baked into the agent image (escalate at H1, T24A)

- The `agent` stage of the `Dockerfile` runs `COPY . .` (line 64).
  `.dockerignore` excludes `papers` and `splash_links/links.sqlite`, but not:
  - `storage/kg/` (47 tracked KG JSON files, 151 MB)
  - `storage/terminology/` (58 tracked extracted-term files, 151 MB)
  - `storage/tiled_catalog.db` (tracked SQLite catalog)
- D28/D31 and the plan (sections 4.14, 13.4) require KG snapshots and
  operational databases to be runtime mounts, not image layers, even when
  tracked by Git. These files were not checked for source-text excerpts. That
  check belongs to T21A/T26A (disclosure review before commit-ready status).
- `splash_links/links.sqlite` is tracked in Git and excluded from the image.

## Repository and vendored notices

| Path | Notice |
|---|---|
| `LICENSE.txt` | "FAIR to WISE (F2W) Copyright (c) 2025, The Regents of the University of California, through Lawrence Berkeley National Laboratory". LBNL BSD-style 3-clause text. GitHub reports `NOASSERTION` because the text is not detected as a standard SPDX license. |
| `splash_links/LICENSE` | "Splash-ML Copyright (c) 2020, The Regents of the University of California, through Lawrence Berkeley National Laboratory". Vendored LBNL code; retain the notice. Splash is deprecated (D03/D16), but the vendored tree and its notice stay until a retirement is approved. |
| `storage/schema/*.yaml` | 6 LinkML schemas declare `license: MIT`. Preserve (D20). |
| FORGE donor | BSD-3-Clause, LBNL copyright 2026. Adapted code must keep the notice (plan T00 step 5, BRANCH_WORKFLOW section 3). The notice is in `docs/migration/README.md`. |

## External data and APIs used on literature paths

These sources are referenced in code. Their terms come from provider
documentation and were not re-verified in this audit:

| Source | Used in | Notes |
|---|---|---|
| ChEBI / OBO ontologies | `agents/chebi.py`, `extract_terms.py`, `term_extractor/services.py`, `legacy/build_onto.py` | ChEBI is published under CC BY 4.0 (attribution). |
| OpenAlex | `f2w_agent/download_agent.py`, `f2w_agent/api.py` | Metadata published as CC0. Uses `OPENALEX_EMAIL` polite pool. |
| arXiv | `f2w_agent/download_agent.py`, `f2w_agent/api.py` | Metadata CC0. Paper licenses vary per article. |
| Materials Project | `f2w_agent/materials_project.py` (B33) | API key `MP_API_KEY`. Data under CC BY 4.0. |
| CORE | `CORE_API_KEY` in `.env` | Open-access aggregator. Per-paper licenses vary. |

Downloaded papers inherit per-article licenses. That supports D19: agents may
read them, users get summaries, facts and bounded exact quotations.

## Python dependencies

Fresh `requirements/dev.txt` + `requirements/semantic.in` environment: 221
distributions (`environment/python_licenses.json`). Mostly MIT, BSD and
Apache-2.0. Non-permissive or unusual entries:

| Package | Version | License | Assessment |
|---|---|---|---|
| pymupdf | 1.28.0 | AGPL-3.0 or Artifex commercial | **E2** |
| bibtexparser | 1.4.4 | LGPLv3 or BSD | Choose BSD. No action. |
| certifi, pathspec, orjson, tqdm | | MPL-2.0 (some dual) | File-level copyleft. Unmodified use is fine. |
| pytest-repeat, pytest-rerunfailures | | MPL-2.0 | Dev only. |
| pymatgen-io-validation | 0.1.2 | "modified BSD" | Permissive. Confirm text if redistributed in an image. |

`uv pip check`: all installed packages compatible. Docling, Tiled and
LangGraph-donor dependencies are not in the baseline; their resolution is a
T04/H1 item (plan section 3, dependency risk).

## Node dependencies (`ui/package-lock.json`)

682 packages: 552 MIT, 48 ISC, 36 Apache-2.0, 20 BSD-3-Clause, 11 MPL-2.0, and
small counts of BlueOak-1.0.0, BSD-2-Clause, MIT-0, CC-BY-4.0, Unlicense. One
non-dev package, `browser-assert`, declares no license field. Low risk; note it
for the image SBOM in T24D.

## Follow-ups (no owner decision needed)

- Generate an SBOM per image variant in T24D. This audit is not an SBOM.
- Keep FORGE BSD-3 attribution on every adapted donor file (port manifest
  status records, plan section 14).
- Re-run this audit when T04 fixes the full-profile lock (Tiled 0.2.19,
  FastAPI, parser extras).

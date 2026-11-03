# Legacy code

Historical implementations remain under `app/modules/legacy/` for
reproducibility and reference. They are not imported by the current
`start_all.sh` stack.

| File | Historical purpose |
|---|---|
| `build_onto.py` | Convert SKOS/material graphs to ontology/OWL forms, optionally using ROBOT |
| `extract_terms.py` | Early Ollama-only term extractor |
| `extract_terms_cli.py` | Schema-aware standalone extractor (moved from `app/modules/extract_terms.py`, T17B; the `NORMALIZER_LEGACY` normaliser and `extract-terms-cli` entry point) |
| `extract_terms_linkml.py` | Earlier LinkML-aware extractor |
| `extract_terms_linkml_jun3.py` | Dated extraction snapshot |
| `extracted_terms_json2kg_with_context.py` | Earlier terms-to-KG converter preserving context |
| `json2kg.py` | Earlier graph converter and embedded tests |
| `kg_rag_ollama.py` | Early local Ollama KG-RAG implementation |
| `kg_rag_ollama_nersc.py` | NERSC-specific interactive KG-RAG implementation |

## Current replacements

| Legacy concern | Current implementation |
|---|---|
| Term extraction | `app/modules/term_extractor/` or `app/modules/legacy/extract_terms_cli.py` |
| Graph conversion | `app/modules/json2kg.py` |
| RAG and model clients | `app/modules/kg_rag_api.py` |
| NERSC agent workflow | `app.modules.launchers.f2w_agent` plus `scripts/run_nersc_3agent.sh` |
| Graph persistence | Versioned JSON KG in `storage/kg/` (default) with gated publication into Tiled Graph; the vendored `splash_links/` is the legacy editable mode |

## Change policy

- Do not add new application features only to legacy modules.
- Preserve legacy files when old experiment reproduction depends on them.
- If fixing a security or data-loss issue in a callable legacy path, document
  the reason and add a focused test.
- Do not assume output from a legacy converter has every publication,
  code-snippet, or source-scoped provenance field expected by the current UI.
- The copied `scripts/app/modules/kg_rag_api.py` is also non-canonical. Update
  `app/modules/kg_rag_api.py` first and either synchronize or retire the copy
  explicitly.

## Ontology path

`build_onto.py` is separate from the active property graph:

```mermaid
flowchart LR
    Terms[Extracted terms/SKOS] --> O[Legacy ontology builder]
    O --> G[OBO Graph JSON]
    G --> OWL[OWL/ROBOT post-processing]
```

The active chat application does not require this ontology conversion. It uses
MatKG JSON directly (and Splash Links in the legacy editable mode).

## Splash Links

`splash_links/`, `compose.splash.yaml`, `scripts/start_all.sh`
and the `--kg-mode splash` paths are the legacy
editable graph mode. They stay runnable until the Splash retirement gate
(T16B/H7 or H8, `docs/migration/plan_addenda.md` A2) and are never built into
the `:local`, `:als-dev` or `:als-prod` images. See
[Splash Links](splash-links.md).

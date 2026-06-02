You are working in the repository at `/Users/mateo/Desktop/f2wlocal`.

## Context

This repo builds materials science knowledge graphs from research paper PDFs. The pipeline is:
1. Extract terms from PDFs → `app/modules/extract_terms.py`
2. Convert terms to KG → `app/modules/json2kg.py`
3. Query via KG-RAG chat → `app/modules/kg_rag_ollama_api.py`

The LinkML schema is at `storage/schema/matkg_schema.yaml`. It defines classes (`Thing`, `Material`, `ExperimentalTechnique`, `ProcessingMethod`, etc.) and slots. The extraction script `app/modules/extract_terms.py` reads this schema via `SchemaView` and uses it to guide LLM-based extraction from PDFs.

## Task

### 1. Update `storage/schema/matkg_schema.yaml`

Add the following to the schema:

- A new class `XRayScatteringAnalysis` as a subclass of `ExperimentalTechnique` with:
  - `technique_type` slot usage serialized as `"xray_scattering"`
  - A new slot `scattering_technique` (string, required: false) — values should be one of: `SAXS`, `WAXS`, `GIWAXS`, `GISAXS`
  - A new slot `peak_positions` (string, multivalued, required: false) — list of observed peak positions (e.g. `"q = 0.38 Å⁻¹"`)
  - A new slot `d_spacing` (string, multivalued, required: false) — d-spacing values derived from peaks (e.g. `"d = 16.5 Å"`)
  - A new slot `peak_assignments` (string, multivalued, required: false) — crystallographic assignments (e.g. `"(100) lamellar peak"`, `"(010) π-π stacking"`)
  - A new slot `code_snippet` (string, required: false) — a verbatim code block extracted from the paper related to peak-finding or scattering data analysis
  - A new slot `code_language` (string, required: false) — programming language of the code snippet (e.g. `"python"`, `"matlab"`)
  - A new slot `code_description` (string, required: false) — plain-English description of what the code snippet does

All new slots should follow the existing slot pattern: `slot_uri: matkg:<slot_name>`, `annotations: owl: AnnotationAssertion`.

---

### 2. Update `app/modules/extract_terms.py`

The extraction script uses an LLM prompt to extract structured terms from PDF pages. It needs to be extended to:

1. **Detect and extract code snippets** from PDF pages that relate to x-ray scattering peak-finding (SAXS, WAXS, GIWAXS, GISAXS). Code may appear as monospaced blocks, algorithm listings, or inline code in figures/captions.

2. **Add a new extraction function** `extract_xray_code_snippets(page_text: str, client: ChatClient, schema_helper: SchemaHelper) -> List[Dict]` that:
   - Prompts the LLM to identify any code blocks in `page_text` related to x-ray scattering peak analysis
   - Returns a list of dicts with keys: `scattering_technique`, `peak_positions`, `d_spacing`, `peak_assignments`, `code_snippet`, `code_language`, `code_description`, `page`, `source_paper`
   - Returns an empty list if no relevant code is found
   - Uses the same retry/error handling pattern as existing extraction functions in the file

3. **Integrate into the main extraction loop** so that for each PDF page, after existing term extraction runs, `extract_xray_code_snippets()` is also called and results are saved to the output JSON under a top-level key `"xray_code_snippets"` alongside the existing `"terms"` key.

4. **Thread-safe saving** for `xray_code_snippets` using the same `_save_terms_threadsafe` pattern already in the file.

5. **Do not break existing extraction behavior.** The new extraction should be additive — existing term/property extraction is unchanged.

---

## Reference files to read before making changes

- `storage/schema/matkg_schema.yaml` — full schema
- `app/modules/extract_terms.py` — full extraction script
- `app/run_pipeline_cborg.py` — pipeline runner that calls `run_extraction()`

## Constraints

- Match existing code style exactly (same logging, retry decorator, prompt formatting, JSON parsing patterns)
- Use `python3` not `python`
- `load_dotenv(override=True)` already called at module top — do not add another
- New slots in schema must follow the exact YAML indentation and annotation pattern of existing slots
- Verify with `python3 -m py_compile app/modules/extract_terms.py` and validate schema loads cleanly with:

```bash
python3 -c "
from linkml_runtime.utils.schemaview import SchemaView
sv = SchemaView('storage/schema/matkg_schema.yaml')
print('schema OK', list(sv.all_classes().keys()))
"
```

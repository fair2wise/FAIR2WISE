# ADR: Schema ownership, loading, validation and versioning

- **Status:** Accepted at H1 (2026-10-12) (T22A; `docs/migration/reviews/H1/decision.json` H1-8: the ADR set is the H1 baseline). Implemented by T22B (`85b7e7c`).
- **Superseded/amended by:** H3 F5/F6 (2026-10-15/16; `plan_addenda.md` A4; T22C `2a80557`): the owner overrode the section 2.1 rule "Do not add measurement-instance classes" in its additive form. Schema set 0.3.0 adds `Sample`, `MeasurementRun` (with `RunCondition`) and `Instrument` as new classes; `RSoXSMeasurement is_a ExperimentalTechnique` keeps its technique meaning and no existing class, slot or node changes meaning. The 0.3.0 approval for builds and publishing, and the `MeasurementRun` name, are pending owner decision (OPEN_DECISIONS D1.1, D1.2). H3 S4: every schema licence line is LBNL BSD-style (Q4 below). Q1-Q3 and Q5 remain open.
- **Baseline:** `222b1e7b130567dbf4a3242697ac2b465794a38d` (clean export, inspected with linkml_runtime 1.11.1 / PyYAML 6.0.3 under `env -i`; no network, LLM or Tiled).
- **Inputs:** `docs/migration/schema_inventory.json` (asset hashes, callsites, findings F1-F9), `companion/SCHEMA_ARCHITECTURE.md`, `companion/DECISIONS.md` D20/D21, `plan.md` 4.9.
- **Owner:** project owner (D06). Shared LinkML changes are owned by the integration lead (plan.md task table).

## 1. Context

F2W already has a working schema stack. It has no explicit registry, and no artifact records which schema it was produced under.

- Scientific LinkML in `storage/schema/`: base `matkg_schema.yaml` (v0.2.0, 32 classes/83 slots), overlays `rsoxs_schema.yaml` (rsoxs_v1), `bl1101_schema.yaml` (bl1101_v2), `xray_schema.yaml` (xray_v1), and the extract overlays `rsoxs_schema_extract.yaml` and `xray_schema_extract.yaml`. Every overlay imports `matkg_schema` by filename. The base imports `linkml:types`.
- Non-LinkML YAML in the same directory: `prov_vocabulary.yaml` (reference vocabulary), `bl1101_motor_stage_map.yaml` (ingest mapping), `tiled_sim_seed.yaml` (simulation seed) and `xray_seed_instances.yaml` (examples). `SchemaDefinition` rejects all four.
- **Loading and normalization:** `term_extractor/schema.py:SchemaHelper` maps extracted terms onto schema classes and slots through aliases, a fuzzy fallback, `Thing`/`related_to` fallbacks and domain/range checks. It does not validate LinkML instances. `extract_terms.py` contains a divergent copy (F4).
- **Selection:** the extraction schema comes from `--schema` / `F2W_SCHEMA`. It is `rsoxs_schema.yaml` in compose and `matkg_schema.yaml` everywhere else (F3). For graphs, `multi_kg.schema_path_for_graph` picks a schema by filename pattern, but nothing reads the result (F2).
- **KG creation:** `json2kg` performs no LinkML validation. It emits `Unknown` categories and `rel:`-prefixed predicates, and existing graphs contain many categories and predicates outside every closure (F7).
- **Inheritance to keep:** `RSoXSMeasurement is_a ExperimentalTechnique` (`rsoxs_schema.yaml:21-38`). The class description is "A resonant soft X-ray scattering measurement or simulation of one (RSoXS, R-SoXS, P-RSoXS, CyRSoXS). Prefer this class over the parent ExperimentalTechnique ...". D20 requires this inheritance to stay. No test protects it today (F9).

## 2. Decision (proposed)

### 2.1 Preserve current semantics (D20)

- Keep every asset byte-identical, including the broken `rsoxs_schema_extract.yaml` (F1). Keep the current classes, inheritance, slot meanings, alias tables, fuzzy cutoff (80) and fallbacks. Keep the json2kg field mappings and the `rel:` predicate form (D21).
- **No measurement/technique split.** Do not add a rule that RSoXSMeasurement must not inherit ExperimentalTechnique. Do not add measurement-instance classes. *Amended by H3 F5/F6 (A4, T22C): measurement-instance classes (`Sample`, `MeasurementRun`, `Instrument`) are added additively in schema set 0.3.0; the rule against reclassifying existing nodes and against changing `RSoXSMeasurement` still stands.* The existing `corpus_qa.py` section 3 "reclassification candidates" and section 5 "zero-measurement papers" outputs are historical diagnostics. They are not acceptance criteria and must not trigger reclassification (see `operations_inventory.md`).
- Behavior that differs by entrypoint (F3, F4) is preserved as observed. Each path records which schema and which normalizer implementation it used, so the difference becomes visible rather than being silently unified. Any unification is a separate, reviewed behavior change.

### 2.2 Module layout: thin, and reusing what exists

| Proposed module | Responsibility | Reuses |
| --- | --- | --- |
| `app/modules/schema/registry.py` | Registry of approved assets. Key = (`schema_id`, `version` label, `content_fingerprint`), never a bare class name or the LinkML `id` alone (F1: `rsoxs` id collision; `ScatteringGeometry` has different parents in bl1101 and xray). Each entry records `classification` (scientific_linkml / reference_vocabulary / ingest_mapping / seed / example), `selectable_for` (extraction, graph-view, none) and `known_issues`. | File list and hashes in `schema_inventory.json`; `multi_kg._SCHEMA_BY_NAME` and patterns as the initial graph-to-schema mapping |
| `app/modules/schema/loading.py` | Resolve a registry entry to an absolute packaged path, independent of CWD (F5). Construct `SchemaView`. Compute the closure fingerprint. Refuse non-LinkML assets in the LinkML loader. | `linkml_runtime.SchemaView` (the same library and pin); `yaml.safe_load` for the mapping/seed loaders as today (`bl1101_ingest.load_motor_stage_map`, `tiled_sim.load_seed`) |
| `app/modules/schema/validation.py` | Adapters that call the **existing** `SchemaHelper` methods unchanged and produce a diagnostic report: contract errors, incomplete extraction, unknown source data and legacy warnings, kept as separate categories. | `term_extractor/schema.py:SchemaHelper`; `extract_terms.SchemaHelper` wrapped as a separately named legacy normalizer |
| `app/modules/schema/migrations.py` | Preview only: diff two registered fingerprints (classes/slots/is_a added, removed or changed). No automatic data transformation in this release. | none (new infrastructure) |

No donor (FORGE) Pydantic classes become a scientific authority. Existing callers (`Orchestrator`, `ExtractorAgent`, CLI `--schema`, `F2W_SCHEMA`) keep their parameters. The registry resolves them, and unknown paths keep working as "unregistered".

### 2.3 Fingerprints

- `content_fingerprint` = SHA-256 of the file bytes. `closure_fingerprint` = SHA-256 over the sorted `(schema name, file sha256)` pairs of the imports closure, plus the `linkml:types` / linkml_runtime version. A `version` label never stands in for a fingerprint, because `rsoxs_v1` labels two different files today.
- These artifacts get the fingerprint in an **adjacent manifest or metadata field**, so payloads stay compatible:
  - extraction outputs: terms JSON `metadata`, plus the extraction manifest consumed by `paper_evidence_agent`;
  - versioned JSON KG manifests (T23A/T23B; never inside `things`/`associations`);
  - canonicalization proposals and reports;
  - retrieval and source indexes (`storage/source_index/*` build metadata; `kg_rag_api` loaded-graph state);
  - Tiled import receipts (T08B checks the fingerprint before import);
  - Knowledge Operations results (`operation_results` provenance block, T19B).
- Each record also states the normalizer used (`term_extractor.schema.SchemaHelper` vs `extract_terms.SchemaHelper`) and `selection_source` (CLI flag, env, default or filename pattern).

### 2.4 Distinct contracts

| Contract | Authority | Validation style |
| --- | --- | --- |
| Scientific LinkML (base + overlays) | `storage/schema/*.yaml` via the registry | SchemaView closure; SchemaHelper normalization at extraction |
| Reference vocabularies (PROV-O) | `prov_vocabulary.yaml` + `app/modules/prov.py` | YAML shape checks (as in `tests/test_prov.py`); never imported into LinkML |
| Ingest mapping / seed / example data | `bl1101_motor_stage_map.yaml`, `tiled_sim_seed.yaml`, `xray_seed_instances.yaml` | Loader-specific YAML schemas; separate versions (`version: 4`, `seed: 20260916`) |
| Parser/job records | `term_extractor/models.py`, `message.py`, extraction manifest, `trace.py` (`SCHEMA_VERSION=1`), workflow state | Their own record versions; a mapping to LinkML requires an explicit, lossless adapter |
| API / UI DTOs | `f2w_agent/api.py` Pydantic models (`GraphPayload`, `GraphNode`, ...) | Frozen for old-UI compatibility (D11); adapters, not schema edits |

A record format version (for example the trace `SCHEMA_VERSION=1`) is not a LinkML version. The registry must not mix the two.

### 2.5 How mismatches surface without reclassifying old graphs

1. **Never retroactive.** Existing snapshots are imported and served as they are. Validation runs in report mode for any artifact without a recorded fingerprint, and the result is "unverified, legacy". It is never "invalid", and no categories are rewritten.
2. **Diagnostic categories.** `contract_error` covers a malformed payload or an unknown or unregistered schema on an artifact that claims a fingerprint. `legacy_warning` covers values outside the closure, such as `Unknown`, `Instrument`, `characterized_by` or association-class names used as predicates; today `matkg_with_code.json` has 7,878 of 15,986 nodes and 744 of 20,203 edges in this state. `incomplete_extraction` and `unknown_source_data` are separate categories. Counts in reports state their unit (nodes or edges).
3. **Fingerprint mismatch.** For new artifacts with a fingerprint, a mismatch against the registry is rejected explicitly at import or operation time ("schema X@fp not registered"). It is never interpreted through a default ontology. `matkg_schema.yaml` is the extraction default, not an interpretation fallback.
4. **Known-broken assets** (F1) are registered `selectable_for: none` with their failure recorded, so selecting them errors clearly instead of failing deep inside `class_induced_slots`.
5. **Corrections** (schema fixes, alias unification, the extract-overlay repair, any semantic change) require a new registered version, a source/data mapping, evaluation, old-UI compatibility and a rollback plan, and are reviewed separately (SCHEMA_ARCHITECTURE section 4). They do not block this migration.

## 3. Required tests (T22B)

- Class hierarchy preserved, including `RSoXSMeasurement` ancestors `[RSoXSMeasurement, ExperimentalTechnique, Thing]` and its induced slots.
- SchemaHelper outcomes frozen per entrypoint: aliases, fuzzy matching, `Thing`/`related_to` fallbacks, domain/range downgrade.
- Import resolution from a CWD outside the checkout.
- Fingerprints are stable and change on any byte change. `rsoxs_schema.yaml` and `rsoxs_schema_extract.yaml` produce different registry keys.
- A legacy graph without a fingerprint loads with warnings and identical node and edge counts and categories, with no retyping.
- An artifact with a stale or unknown fingerprint is rejected with an explicit error.
- Public DTOs are unchanged.

## 4. Consequences

- Positive: schema identity becomes explicit and auditable. The profile-dependent behavior (F3) becomes visible. Nothing that works today breaks.
- Negative: two normalizers and a broken extract overlay stay in place until the owner decides. Reports will show large legacy-warning counts for existing graphs, and reviewers must read these as conformance gaps, not as defects introduced by the migration.

## 5. Open owner questions

Carried from `schema_inventory.json`:

1. **Q1 (`rsoxs_schema_extract.yaml`):** keep it as known-broken, or approve a versioned fix?
2. **Q2 (local default schema):** should local-profile extraction default to `matkg_schema.yaml` or `rsoxs_schema.yaml`?
3. **Q3 (`extract_terms.py` normalizer):** keep its divergent behavior or unify it with `term_extractor/schema.py` (a reviewed behavior change)?
4. **Q4 (license):** the schemas declare MIT, while the repository `LICENSE.txt` is LBNL BSD-style. Which applies? *Answered at H3 (S4): LBNL BSD-style; applied in T22C (`license: BSD-3-Clause-LBNL`).*
5. **Q5 (xray graphs):** should xray graphs be bound to `xray_schema.yaml`?

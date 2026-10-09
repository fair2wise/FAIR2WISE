# Schema architecture with preserved scientific semantics

**Draft v6. O01 is resolved: preserve existing logic.** Loading, validation and versioning are in scope; the previously proposed measurement/technique semantic split is not.

## 1. What the proposed split would have meant

A technique describes a method/category, while a measurement-instance model would distinguish a particular observation with its sample and conditions. Changing the current inheritance would affect class validation, queries, record mapping and possibly old data. It is a scientific-model migration, not a prerequisite for the runtime refactor. Retain the supplied schema, including RSoXSMeasurement's current ExperimentalTechnique parent, and do not declare historical records invalid merely for following it.

## 2. Existing assets and implementations

Preserve storage/schema/matkg_schema.yaml, rsoxs_schema.yaml, rsoxs_schema_extract.yaml, bl1101_schema.yaml, xray_schema.yaml and xray_schema_extract.yaml with their actual import/overlay behavior. PROV reference vocabulary, motor/stage maps and seed instances are not all interchangeable LinkML schemas. Preserve their classification from storage/schema/README.md.

Reuse term_extractor/schema.py:SchemaHelper, f2w_agent/multi_kg.py schema selection and json2kg field mappings. Do not introduce donor Pydantic classes as a second scientific authority. Parser/job/tool/API DTOs can differ from LinkML but require lossless explicit adapters.

## 3. Proposed modules

- app/modules/schema/registry.py: approved schema/overlay identity and version selection.
- app/modules/schema/loading.py: import/resource loading independent of working directory.
- app/modules/schema/validation.py: adapters to current validation behavior, with explicit diagnostic reports.
- app/modules/schema/migrations.py: infrastructure for previewing future version changes; no automatic semantic transformation in this release.

Bind exact schema/import fingerprints to extraction records, versioned JSON KG manifests, canonicalization proposals, reports, indexes and Tiled import receipts. A version identifier does not make a changed schema equivalent. Reject unknown/mismatched schemas explicitly instead of interpreting them through a default ontology.

## 4. Validation compatibility

Freeze current validation outcomes and record pre-existing warnings at H0. New checks must distinguish contract errors, incomplete extraction, unknown source data and legacy warnings without silently altering meanings or relaxing required evidence. Binary cases bind to the actual approved schema version. Do not combine old-schema parity assertions with a new contradictory rule that RSoXSMeasurement must not inherit ExperimentalTechnique.

H3 approves case definitions and lossless F2W/FORGE field adapters under existing semantics. Proposed semantic improvements may be documented for a separate follow-on request but cannot become migration acceptance blockers. Any later owner-requested schema change requires a new version, source/data mapping, evaluation, old-UI compatibility and rollback plan.

## 5. Tasks and required binary checks

T22A blocks H1 on inventory/contracts. T22B reuses the current validator and adds registry/version binding after H2. T23B uses that contract to construct JSON snapshots; T08B checks it before Tiled import.

Test existing class hierarchy and validation outcomes, overlay selection, import/resource resolution outside the checkout, schema fingerprints on JSON/import artifacts, current graph identity preservation, stale schema rejection, unchanged public DTOs and no hidden retyping. No schema semantic migration is authorized by this document.

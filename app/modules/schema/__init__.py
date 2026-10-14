"""Schema registry, loading, validation reuse and version binding (T22B, ADR schema-ownership).

* :mod:`.registry` - discovers the existing ``storage/schema`` assets in place (nothing moves) and
  keys them by (schema id, version label, content fingerprint).
* :mod:`.loading` - resolves entries and loads them through the existing implementations
  (``term_extractor.schema.SchemaHelper``, ``extract_terms.SchemaHelper``, ``SchemaView``); computes the
  deterministic import-closure fingerprint.
* :mod:`.validation` - adapters over the existing ``SchemaHelper`` methods and a report-mode graph check
  that never reclassifies a legacy graph.
* :mod:`.migrations` - preview-only diff between two registered schemas.

No schema semantics change here (D20): ``RSoXSMeasurement is_a ExperimentalTechnique`` stays.
"""

# LinkML schemas and related YAML

This directory holds LinkML overlays, extract-time imports, seed data, and
reference vocabularies. Each science or ops graph uses its own overlay; graphs
are **never concatenated**. Retrieval fans out per JSON file and merges hit
lists tagged with `graph_id`.

Implementation: `app/modules/f2w_agent/multi_kg.py` (`graph_id_for_path`,
`schema_path_for_graph`) and `scripts/json2kg.py` / `app/modules/json2kg.py`.

## Which file for which graph

| File | Role | Live KG / namespace |
|---|---|---|
| `matkg_schema.yaml` | Shared MatKG classes and slots | Imported by all overlays |
| `rsoxs_schema.yaml` | RSoXS literature overlay (`RSoXSMeasurement`, resonant slots) | `storage/kg/matkg_rsoxs_vN.json`, `matkg:` |
| `rsoxs_schema_extract.yaml` | Extract-time import of the RSoXS overlay | Term extractor only |
| `bl1101_schema.yaml` | ALS 11.0.1.2 ops overlay (stages, motors, beam-path slots) | `storage/kg/matkg_bl1101_vN.json`, `beamline:` |
| `xray_schema.yaml` | X-ray *principles* overlay (not technique papers) | Planned `matkg_xray_vN.json`, `xray:` — do not merge into rsoxs/ops |
| `xray_schema_extract.yaml` | Extract-time import of `xray_schema.yaml` | Term extractor only |
| `xray_seed_instances.yaml` | Example principle nodes for tests / extract prompts | Not a harvested corpus |
| `bl1101_motor_stage_map.yaml` | Reproducible motor → `BeamlineStage` map for ops ingest | Used by `scripts/ingest_bl1101.py` |
| `tiled_sim_seed.yaml` | RNG seed and ESAF/proposal/sample templates for sim ingest | Tiled Graph / `scripts/ingest_tiled_sim.py` |
| `prov_vocabulary.yaml` | W3C PROV-O *reference* (not LinkML) | See below |
| `v0.3.0/matkg_schema.yaml`, `v0.3.0/rsoxs_schema.yaml` | Schema set 0.3.0 (T22C): additive Sample, MeasurementRun, Instrument and evidence slots | **Pending owner review**; selected only explicitly (`rsoxs_schema@0.3.0`). See below |
| `v0.3.1/matkg_schema.yaml`, `v0.3.1/rsoxs_schema.yaml`, `v0.3.1/bl1101_schema.yaml` | Schema set 0.3.1 (T22D): 0.3.0 plus the normalized-value slots on `QuantityValue` (D1.4) and bl1101_v3 with the Sample/Detector/Endstation mapping table (D1.3) | **Pending owner review**; selected only explicitly (`rsoxs_schema@0.3.1`, `bl1101_schema@0.3.1`). See below |

Do not point the RSoXS extractor at `xray_schema.yaml`, and do not harvest
X-ray fundamentals PDFs into `papers/rsoxs/`. Synchrotron tomography PDFs go
in `papers/tomo/` (`scripts/harvest_tomo.py`), not `papers/xray/` or
`papers/rsoxs/`.

## Extract vs viewer schemas

- **Viewer / json2kg** schemas (`rsoxs_schema.yaml`, `bl1101_schema.yaml`,
  `xray_schema.yaml`) are the source of truth for classes and slots.
- **Extract** schemas (`*_schema_extract.yaml`) exist so the term extractor can
  `imports:` a thin file. They must stay LinkML-valid: **no extra top-level
  keys**.

Pass `--schema storage/schema/<overlay>_extract.yaml` (or the overlay itself)
to `scripts/run.py`. Default agent startup uses `matkg_schema.yaml` unless
`F2W_SCHEMA` is set.

## `prov_vocabulary.yaml`

This file documents the locked `prov:` compact terms used in data
(`prov:wasGeneratedBy`, `prov:used`, …). Helpers live in `app/modules/prov.py`.

It is **not** a LinkML schema. LinkML `SchemaDefinition` rejects unknown
top-level keys such as `prov_vocabulary:`. Do **not** `imports:` this file and
do **not** paste a `prov_vocabulary:` block into `rsoxs_schema.yaml` or
`xray_schema.yaml`.

## Promotion snapshots

json2kg writes versioned files under `storage/kg/`:

- Literature: `matkg_rsoxs_v1.json` is kept; later extracts promote
  `matkg_rsoxs_vN.json` (live chat often uses the highest `vN`).
- Ops: `matkg_bl1101_v1.json` is kept; ingest snapshots are `matkg_bl1101_vN.json`.
- X-ray fundamentals: `matkg_xray_vN.json` when harvest/extract is explicitly
  requested. Distinct from the opt-in demo file
  `storage/kg/matkg_xray_papers_cborg_chat.json`.

v1 files are never deleted during promotion. Experiment identity (ESAF,
Proposal, Sample, BlueskyRun) is **not** stored in these JSON KGs from ops v8
onward — see `mkdocs/docs/architecture.md` (Multi-KG layout / Tiled identity).

## Schema set 0.3.0 (T22C, pending owner review)

`v0.3.0/` holds a later, additive version of `matkg_schema.yaml` (0.3.0) and
`rsoxs_schema.yaml` (rsoxs_v2). The files keep the base names, so
`imports: [matkg_schema]` resolves to the sibling of the same version. The
registry lists them as `matkg_schema@0.3.0` and `rsoxs_schema@0.3.0`, pending
owner review of `docs/migration/reviews/H3/schema_diff_T22C.md`. No default
path uses them: `registry.select`, snapshot builds and the default Tiled
supported set all refuse them unless the caller asks explicitly.
`bl1101_schema.yaml` and the xray schemas stay on the base set.

## Schema set 0.3.1 (T22D, pending owner review)

`v0.3.1/` is 0.3.0 plus two owner-decided additions (`owner_decisions.md` batch 13,
O-S2, 2026-10-30), each additive:

- **D1.4, normalized value.** `QuantityValue` gets four optional slots:
  `normalized_value`, `normalized_unit`, `conversion_factor` and
  `conversion_profile`. `numeric_value`/`unit` stay the value exactly as printed;
  the normalized slots are filled only from the closed conversion tables of
  `app/modules/evidence/units.py` (`normalize_quantity`, profile
  `forge-units-v1`) and left absent when the unit is unknown. `matkg_schema.yaml`
  is `0.3.1`, `rsoxs_schema.yaml` is `rsoxs_v3` (same overlay on the new base).
- **D1.3, bl1101 v3.** `v0.3.1/bl1101_schema.yaml` (`bl1101_v3`) imports the 0.3.1
  base and adds a mapping table as class annotations: ops `Sample` ->
  literature `Sample` (label kept as `sample_label`), `Detector` and `Endstation`
  -> `Instrument` with `instrument_category` `detector`/`endstation`. The ops
  classes keep their parents and slots (bl1101's own `Sample is_a Material`
  shadows the imported one inside the bl1101 closure, so the tracked bl1101
  graphs validate unchanged). The table is applied only by
  `app/modules/schema/bl1101_mapping.py` on request and round-trips exactly.
  `bl1101_motor_stage_map.yaml` and the ingest (`bl1101_v2`) are unchanged.

Registry ids: `matkg_schema@0.3.1`, `rsoxs_schema@0.3.1`, `bl1101_schema@0.3.1`.
Review doc: `docs/migration/reviews/H3/schema_diff_T22D.md`. The default schema
stays the base set until the owner decides otherwise (pilot).

## Version table

| Set | `matkg_schema` | `rsoxs_schema` | `bl1101_schema` | `xray_schema` | Status |
|---|---|---|---|---|---|
| base (`storage/schema/`) | 0.2.0 | rsoxs_v1 | bl1101_v2 | xray_v1 | in use; every default path |
| 0.3.0 (`v0.3.0/`, T22C) | 0.3.0 | rsoxs_v2 | (base) | (base) | pending owner review |
| 0.3.1 (`v0.3.1/`, T22D) | 0.3.1 | rsoxs_v3 | bl1101_v3 | (base) | pending owner review |

## Licence

Every LinkML schema here is under the repository licence (`LICENSE.txt`, LBNL
BSD-style, SPDX `BSD-3-Clause-LBNL`). Until H3 S4 the files said `license: MIT`.
The registry can rebuild the earlier bytes, so artifacts bound to those
fingerprints still bind.

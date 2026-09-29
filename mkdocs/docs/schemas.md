# LinkML schemas

Canonical files live in [`storage/schema/`](https://github.com/matesuu/FAIRtoWISE-FORUM-AI/tree/main/storage/schema)
(see that directory’s `README.md` in the repository). Each science or ops
graph uses its own overlay; graphs are **never concatenated**. Retrieval fans
out per JSON file and merges hit lists tagged with `graph_id`.

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

Do not point the RSoXS extractor at `xray_schema.yaml`, and do not harvest
X-ray fundamentals PDFs into `papers/rsoxs/`.

## Extract vs viewer schemas

- **Viewer / json2kg** schemas (`rsoxs_schema.yaml`, `bl1101_schema.yaml`,
  `xray_schema.yaml`) are the source of truth for classes and slots.
- **Extract** schemas (`*_schema_extract.yaml`) exist so the term extractor can
  `imports:` a thin file. They must stay LinkML-valid: **no extra top-level
  keys**.

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
  `matkg_rsoxs_vN.json`.
- Ops: `matkg_bl1101_v1.json` is kept; ingest snapshots are `matkg_bl1101_vN.json`.
- X-ray fundamentals: `matkg_xray_vN.json` when harvest/extract is explicitly
  requested. Distinct from the opt-in demo file
  `storage/kg/matkg_xray_papers_cborg_chat.json`.

v1 files are never deleted during promotion. Experiment identity is not stored
in these JSON KGs from ops v8 onward — see [system architecture](architecture.md).

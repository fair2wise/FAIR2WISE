# T22C schema diff for owner review: schema set 0.3.0

**What you are asked to do:** read this page and approve, amend or reject schema set 0.3.0. **Until you approve
it, nothing uses it.** No default build, extraction or publish path selects the new version. A graph can be
built or published with it only by naming it on purpose, twice: by schema id (`rsoxs_schema@0.3.0`) and by an
explicit flag (`allow_pending_review=True` / `allow_pending_review_schema=True`, or an explicit supported
fingerprint for a Tiled import). Approval is one change: set `review_status` of `SCHEMA_SETS["0.3.0"]` in
`app/modules/schema/registry.py` to `in_use`. Switching a default path to it would be a separate change.

Decisions this implements (`decision.json`, `plan_addenda.md` A4): F5/F6 ("it is imperative to capture samples,
measurements, and techniques"; additive; add an Instrument class), F2 (every value captured and tagged
reported/cited/derived/assumed, none withheld for its tag), F3 (uncertainty kind and sample qualifiers), F7
(evidence links), S4 (LBNL BSD-style licence), S5 (xray graphs bind `xray_schema.yaml`).

## 1. In one paragraph

Until now an F2W graph could say "PM6 is a material", "RSoXS is a technique" and "PM6 has a domain spacing", but
not "*this* annealed PM6:Y6 film was measured by RSoXS at beamline 11.0.1.2 at 284.8 eV and gave 25 nm, as
this sentence on page 3 says". Version 0.3.0 adds the classes and links for that sentence: a **Sample** (the
film), a **MeasurementRun** (that one measurement), an **Instrument** (the beamline), **RunCondition**s
(284.8 eV), a **QuantityValue** (25 nm, tagged `reported`), and the evidence (an **EvidenceQuote** located in a
**PaperSection**). Everything that existed keeps its meaning. RSoXSMeasurement is still a technique.

## 2. Where it lives and what did not change

| | Base set (in use) | Set 0.3.0 (pending your review) |
| --- | --- | --- |
| Files | `storage/schema/matkg_schema.yaml` (0.2.0), `rsoxs_schema.yaml` (rsoxs_v1), plus bl1101, xray and the extract overlays | `storage/schema/v0.3.0/matkg_schema.yaml` (0.3.0), `storage/schema/v0.3.0/rsoxs_schema.yaml` (rsoxs_v2) |
| Registry ids | `matkg_schema`, `rsoxs_schema`, ... | `matkg_schema@0.3.0`, `rsoxs_schema@0.3.0` |
| Used by default | yes, everywhere it was before | never |
| Closure fingerprint (first 16 hex; linkml-runtime 1.11.1) | matkg `8ec418bb4291d3b8`, rsoxs `3a8d9b223065abcd` | matkg `8da8fda4216aa724`, rsoxs `74a06af4c349864b` |

The 0.3.0 files keep the base file names, so the overlay's `imports: [matkg_schema]` finds the 0.3.0 base next to
it. A test (`tests/schema/test_schema_t22c.py`) checks every 0.2.0 class, slot and enum definition is identical in
0.3.0. There are only two exceptions, and both are intended: RSoXSMeasurement's description (section 4), and the
derived `domain_of` list of the reused `unit` slot, which now also names the two new classes that use it.
`RSoXSMeasurement`'s ancestors (`RSoXSMeasurement → ExperimentalTechnique → Thing`) and its induced slots are
unchanged.

**Not moved to 0.3.0:**

- `bl1101_schema.yaml` (ops graphs) defines its **own** `Sample` (`is_a Material`, "a sample class accepted at the
  endstation") and `Detector is_a Device`. If it imported the 0.3.0 base, two different `Sample` classes would meet
  in one closure. bl1101 graphs therefore keep binding `bl1101_schema.yaml` on the base set. Whether ops `Detector`
  and `Endstation` should later map to the literature `Instrument` is an open question (section 8).
- `xray_schema.yaml` / `xray_schema_extract.yaml` are a principles overlay ("do not reintroduce sample/device
  classes"). They share none of the new terms. Under S5 xray graphs bind `xray_schema.yaml`, which stays on the base
  set. (Binding xray graph files by name in `multi_kg.schema_path_for_graph` is not done here; see section 8.)
- `rsoxs_schema_extract.yaml` stays known-broken and byte-identical apart from the licence line (S1).

## 3. Added classes (7)

| Class | Parent | Means | Example |
| --- | --- | --- | --- |
| `Sample` | `Thing` | One physical specimen described in **one** paper. Samples from two papers are never the same node. It is made of `Material` nodes and is not itself a Material. | "annealed film": PM6:Y6, thin film, annealed at 150 C for 10 min, spin-coated from chloroform |
| `MeasurementRun` | `Measurement` | One measurement run reported in one paper: a technique applied to samples on an instrument under stated conditions, with the values it produced. | "RSoXS of the annealed film at 11.0.1.2 at 284.8 eV" |
| `Instrument` | `Thing` | A beamline, endstation or detector. Shared across papers (canonicalized by T07), unlike a Sample. Not a `Device` (Device stays "a device made from materials", such as a solar cell). | "ALS beamline 11.0.1.2", "Pilatus 1M" |
| `RunCondition` | `Condition` | One condition stated for one run: name, value as printed, and a number and unit when the text is a number and a unit. | photon energy = "284.8 eV" (284.8, eV); atmosphere = "vacuum" |
| `QuantityValue` | `Thing` | One value the paper states, exactly as printed, tagged with how the paper relates to it. Every value is kept whatever its tag (F2). | domain spacing 25 nm, `reported`; 40 nm from ref. [12], `cited`; thickness 105 ± 5 nm (standard deviation) |
| `EvidenceQuote` | `Thing` | One sentence quoted verbatim from one source version and **located** in its stored text (exact, or under the recorded normalization). An unlocated quote never becomes one. Located means lexical support, not proof the claim is true. | "The domain spacing is 25 nm for the annealed film." Results, characters 0-50, page 3, exact |
| `PaperSection` | `Thing` | One section of one parsed source version, which contains the quote. | "Results", page 3, of `work:...` |

**Why `MeasurementRun` and not `Measurement`.** A4 names the class `Measurement`, but matkg 0.2.0 **already has**
a class `Measurement` ("A measurement, observation, or characterization result", `is_a Thing`), and the tracked
RSoXS graphs hold 123 nodes of that category (38 in `matkg_rsoxs_v1.json`, 85 in `matkg_rsoxs_v2.json`). Redefining it as "one run" would change the meaning of an
existing class and of those nodes, which A4 forbids ("no existing class, slot or node changes meaning"). The run
class is therefore `MeasurementRun is_a Measurement`: every run is still a Measurement in the 0.2.0 sense, and
`Measurement` itself is untouched. If you prefer another name, it is a rename inside the 0.3.0 files only.

Similarly, the condition class is `RunCondition` rather than `MeasurementCondition`. Three tracked LLM-extraction
graphs already use the category `MeasurementCondition` for generic concepts.

## 4. Changed description (one)

`RSoXSMeasurement` (rsoxs_v2): *"A technique: resonant soft X-ray scattering, measured or simulated (RSoXS,
R-SoXS, P-RSoXS, CyRSoXS). This class names the technique, not a single run; one run that uses it is a
MeasurementRun whose run_of_technique points at this node. Prefer this class over the parent
ExperimentalTechnique ..."*. Its parent, slots and slot usage are unchanged. The rsoxs_v1 file keeps its old
wording.

## 5. Added slots (36) and enums (4)

The new names were checked against every category and predicate in the tracked KG and terms files, including at
the normalizer's fuzzy cutoff (QRatio 80). As a result, re-normalizing the tracked labels under 0.3.0 changes no
relation and moves no label onto a new slot. Only two category labels map differently: `Sample` and
`Instrumentation`, which now map to `Sample` and `Instrument` instead of `Thing`. Example values below come from
the synthetic test paper (`tests/schema/t22c_fixtures.py`).

**Sample** (all "as printed")

| Slot | Range | Example |
| --- | --- | --- |
| `sample_label` | string | "annealed film" |
| `sample_composition` | string, many | "PM6", "Y6" |
| `sample_phase` | string | "thin film" |
| `sample_processing_state` | string | "annealed at 150 C for 10 min" |
| `sample_preparation` | string, many | "spin-coated from chloroform" |
| `made_of_material` | → `Material`, many | → `matkg:PM6`, `matkg:Y6` (the same ids the term graph uses) |

**Instrument**

| Slot | Range | Example |
| --- | --- | --- |
| `instrument_category` | `InstrumentKindEnum`: beamline / endstation / detector | beamline (left empty when the paper does not say; never guessed) |

**MeasurementRun** (links)

| Slot | Range | Example |
| --- | --- | --- |
| `run_of_technique` | → `ExperimentalTechnique` (incl. `RSoXSMeasurement`) | → `matkg:RSoXS` (category RSoXSMeasurement) |
| `run_measured_sample` | → `Sample`, many | → the annealed film |
| `run_on_instrument` | → `Instrument` | → `matkg:ALSbeamline11012` |
| `run_stated_condition` | → `RunCondition`, many | → photon energy 284.8 eV |
| `run_produced_quantity` | → `QuantityValue`, many | → domain spacing 25 nm |

**RunCondition**: `condition_name` ("photon energy"), `condition_text` ("284.8 eV"), plus the shared
`numeric_value` (284.8), `unit` (existing slot, "eV") and `evidence_source_tag` (an assumed setting is `assumed`).

**QuantityValue** (F2, F3)

| Slot | Range | Example |
| --- | --- | --- |
| `quantity_label` | string | "film_thickness" |
| `numeric_value` | float, as printed, no conversion | 105.0 |
| `unit` (existing slot) | string, as printed | "nm" |
| `qualitative_value` | string | "face-on" |
| `uncertainty_value` | float ≥ 0, the paper's own uncertainty, never extraction confidence | 5.0 |
| `uncertainty_kind` | `UncertaintyKindEnum`: standard_deviation / standard_error / interval / unknown | standard_deviation |
| `evidence_source_tag` | `EvidenceSourceEnum`: reported / cited / derived / assumed; **required** | cited (for "Previous reports found ... 40 nm [12]") |
| `qualifier_sample` | → `Sample`, many (sample qualifier) | → the annealed film |
| `qualifier_processing_state` | string (sample qualifier) | "annealed" |
| `qualifier_temperature_k` | float, kelvin (sample qualifier) | 300 |

**Evidence links (F7)**

| Slot | Range | Example |
| --- | --- | --- |
| `supporting_quote` | any node → `EvidenceQuote`, many (value-to-evidence; also sample, run, instrument) | 25 nm → "The domain spacing is 25 nm for the annealed film." |
| `quote_located_in_section` | `EvidenceQuote` → `PaperSection` (quote-to-section) | → Results |
| `quote_text`, `quote_match_type` (`QuoteMatchEnum`: exact / normalized) | string, enum | the sentence; exact |
| `quote_char_start`, `quote_char_end`, `quote_page` | integers | 0, 50, 3 |
| `source_version`, `source_sha256`, `parser_revision` | strings | `sha256:…`, file hash, `synthetic-1.0-29f9eddc097a` |
| `section_label`, `section_page`, `work_id` | on `PaperSection` | "Results", 3, `work:synthetic-t22c-0001` |

**Enums.** `EvidenceSourceEnum` and `UncertaintyKindEnum` have exactly the values of the T06 record enums
(`app/modules/evidence/records.py`); a test keeps them equal. Per F8, a "reported" value in a sentence that
attributes it to other work is `cited`, and an assumed instrument value is `assumed` and never presented as the
paper's own measurement. `QuoteMatchEnum` and `InstrumentKindEnum` are new.

## 6. Licence (S4)

All six LinkML files in the base set, and both 0.3.0 files, now say `license: BSD-3-Clause-LBNL`. That is the
SPDX identifier of the LBNL BSD-style text in `LICENSE.txt`. A comment above it names the holder: "FAIR to WISE
(F2W) Copyright (c) 2025, The Regents of the University of California, through Lawrence Berkeley National
Laboratory. See LICENSE.txt." This changes the base files' fingerprints, which `decision.json` records as an
owner-directed change. The registry rebuilds each file's exact pre-S4 bytes, so a graph or snapshot bound to an
old fingerprint still binds (status `verified_pre_s4_licence`) and still imports through the default Tiled set.

Other MIT mentions in tracked files:

- **Changed:** the docstring of `app/modules/legacy/extracted_terms_json2kg_with_context.py` ("License: MIT"; F2W's
  own module) now points at LICENSE.txt.
- **Left, because they are not F2W's licence:** `repository_license: "MIT"` values in KG/terms JSON and test fixtures
  (licences of third-party GitHub repositories that code snippets came from; data); `ui/ATTRIBUTIONS.md` (shadcn/ui,
  third party); `package-lock.json` / `pixi.lock` and `docs/migration/reviews/H0/environment/python_licenses.json`
  (dependency licences); FORGE donor BSD-3-Clause attributions.
- **Left, because they are dated records:** `schema_inventory.json` (T22A), `reviews/H0/license_dependency_audit.md`,
  `reviews/H3/review_packet.md` and `decision.json`. They describe the state before S4. ADR Q4 now notes the answer.

## 7. What was checked (tests)

- **Tracked KGs unchanged** (`tests/schema/test_schema_t22c_tracked_kgs.py`). Covers the 12 versioned snapshots
  (`matkg_rsoxs_v1..v3`, `matkg_bl1101_v1..v9`). Under set 0.3.0 the rsoxs graphs bind `rsoxs_schema@0.3.0` and
  bl1101 keeps `bl1101_schema`. Every diagnostic category equals the old report, nothing is retyped, and every file's
  bytes are the same before and after. A cross-check runs all 12 against matkg 0.3.0 directly. The only difference
  anywhere is bl1101's own `Sample` nodes (3 to 241 per graph), which is why bl1101 is not moved.
- **Extractor records map onto the classes** (`tests/schema/test_schema_t22c_mapping.py`, adapter
  `app/modules/schema/experiment_mapping.py`). Each accepted Sample/Measurement/Observation record has a
  `supporting_quote` to a located `EvidenceQuote`, whose character span reproduces the quote in its `PaperSection`.
  Withheld records (invented quote) are not mapped. The cited value is kept, tagged `cited`. The payload is entirely
  inside the 0.3.0 closure and outside 0.2.0.
- **Round trips.** The JSON graph backend returns every new node and edge verbatim. A snapshot bound to
  `rsoxs_schema@0.3.0` is refused by the default Tiled supported set. With an explicit fingerprint it imports into
  the in-memory double and a disposable Tiled 0.2.18 server, with types, properties and links intact, a no-op
  re-plan, and the Tiled reader agreeing with the JSON reader.
- **Additivity.** Every 0.2.0 class, slot and enum is identical. The frozen T22B normalization outcomes hold under
  0.3.0. Defaults and entrypoints still resolve to the base set.

## 8. Open questions for you

1. **Name of the run class:** `MeasurementRun is_a Measurement` (section 3), or another name?
2. **bl1101 ops classes:** should ops `Detector`/`Endstation`/`Beamline` later map to the literature `Instrument`,
   and bl1101's `Sample` be reconciled with the paper-local `Sample`? That would need a bl1101 v3 and is not done.
3. **Normalized values:** `QuantityValue` stores the value as printed. The T06 records also carry a normalized
   value (nm, eV, ...). Should 0.3.0 get a slot for it, or does that stay in the candidate records?
4. **Extraction under 0.3.0:** if you later select 0.3.0 for extraction, the extractor prompt lists the new classes,
   and labels such as "Sample" and "Instrumentation" would normalize to the new classes. Is that wanted for
   literature extraction?
5. **S5 follow-up:** `multi_kg.schema_path_for_graph` does not yet bind xray graph file names to
   `xray_schema.yaml`. That is a separate, small change.
6. **Wiring into T06:** applying this mapping in the T06 validation stage (T06B), and turning F8's withholding of
   cited/assumed values into tags, waits for your approval of this diff.

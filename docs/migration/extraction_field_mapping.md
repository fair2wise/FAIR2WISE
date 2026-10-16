# Extraction field mapping (T06; H3 decided, T22C schema rows)

Status: **H3 decided (docs/migration/reviews/H3/decision.json).** Rows marked
`approved H3` point at classes and slots of schema set 0.3.0
(`storage/schema/v0.3.0/`, T22C). That set is additive and still **pending the
owner's review of `docs/migration/reviews/H3/schema_diff_T22C.md`**: no default
build, extraction or publish path uses it, and the T06 validation stage does not
apply these mappings yet (`app.modules.extraction.H3_GATES` stays closed; the
schema-side adapter is `app/modules/schema/experiment_mapping.py`).
`RSoXSMeasurement is_a ExperimentalTechnique` is preserved and keeps meaning a
technique; one run is the new `MeasurementRun` (F5 overrides the D20 rule that kept
measurement instances out of scope, in the additive form the owner chose).
Rows still marked `PENDING H3` were not part of T22C.

Scope: donor fields from E-01 (`pdf_extraction.py`), E-02 (`scientific_records.py`),
E-08 (`extraction.py`, observation half) and E-09 (evidence rows of
`paper_simulation.py`), against what F2W has today: the terms JSON written by
`extract_terms.LLMTermExtractor` (`term_extractor/models.py` `TermRecord` shape)
and the LinkML slots in `storage/schema/matkg_schema.yaml` / `rsoxs_schema.yaml`.

Where things live before H3:

- Donor fields with an existing F2W home are carried in the normalized
  `ExtractionResult` (`app/modules/extraction/contract.py`) and the candidate
  `EvidenceRecord` / `QuantityRecord` (`app/modules/evidence/records.py`).
- Donor fields with no approved home stay in `ExtractionResult.unmapped` or
  `side_outputs` (versioned by `contract_version` and `strategy_version`), and in
  candidate records. They are not dropped and they are not written to a graph.

Column key. **Optionality**: R = always present, O = optional, C = conditional.
**Status**: `existing` = the F2W field already has this meaning; `adapter` = carried
by a T06 record/contract field without any schema change; `PENDING H3` = a proposed
schema slot, class, or hard validation gate that needs owner approval.

## 1. Evidence and provenance (E-03, E-09)

| Donor field | F2W field (current or T06 record) | Intended meaning | Opt. | Validation | Status |
|---|---|---|---|---|---|
| evidence `text` (quote) | `EvidenceRef.quote`; terms JSON `context_snippets[].text` | Sentence as the source prints it | C | `locate_quote` exact/normalized in stored section text; a miss is `quote_not_located`, never an invented span | adapter (EvidenceRef is a T01 contract) |
| `publication_id` / `pub_path` | `EvidenceRef.work_id`; terms JSON `source_papers[]` | Work the quote comes from | R | Must equal the Work being extracted | adapter |
| (none in donor) | `EvidenceRef.source_version`, `source_sha256` | Exact source bytes the offsets refer to | R | `ParsedDocument.source_ref` / file sha256 | adapter |
| `section_name` (strategy label) | `EvidenceRecord.section_label` | The extractor's region label; a pointer only | O | Kept beside the resolved section; `section_label_mismatch` when the text is elsewhere | adapter |
| `section_path` (section node id) | `EvidenceRecord.section_id`; `SourceLocator.section` (name) | Section whose stored text contains the quote | C | Only a section whose text contains the quote; ties marked `quote_ambiguous` | adapter |
| `page` | `SourceLocator.page`; terms JSON `context_snippets[].page` | First page of the containing section | C | `None` (not 0) when the parser cannot say; `page_unknown` marker | adapter |
| `char_start`/`char_end` | `SourceLocator.char_span` | Offsets into the *stored* (truncated) section text | C | Present only for a located quote | adapter |
| `match_kind` (`exact`/`normalized`/`not_found`) | `EvidenceRef.match_type` (`EXACT`/`NORMALIZED`/`NONE`) + `normalization_profile` | How the quote matched | R | Normalized uses profile `forge-nfkc-glyph-ws-v1`; never shown as verbatim by the disclosure layer (which only trusts `ws-hyphen-v1`) | adapter |
| `is_verbatim_quote` | derived: `EvidenceRef.quote is not None` | A sentence, not a region label | R | — | adapter |
| `verified_quote_match` | `EvidenceRecord.verified_quote_match` | Quote found in the source text | R | Lexical only; not entailment | adapter |
| `prompt_version`, `extracted_by` | `EvidenceRecord.strategy`; `ExtractionResult.inference.prompt_version` | Which strategy/prompt proposed it | R | — | adapter |
| (parser provenance) | `EvidenceRef.parser_revision` (= `parser_key`), `EvidenceRecord.parser` | Parser/version/config that produced the text | R | `parser_revision_unknown` marker if version missing | adapter |
| `caveat` | `EvidenceRecord.caveat` | Strategy caveat | O | Bounded to 1000 chars | adapter |
| evidence node id (`md5(pub|text...)`) | `EvidenceRef.evidence_id` (`evidence:<16 hex>`) | Deterministic id, donor-compatible digest | R | Same inputs give the same id | adapter |
| `forge:extractedFrom`, `forge:supportedBy` edges | 0.3.0 `supporting_quote` (Thing → `EvidenceQuote`), `quote_located_in_section` (`EvidenceQuote` → `PaperSection`); records: `QuantityRecord.evidence_ids`, `EvidenceRecord.section_id` | Value-to-quote and quote-to-section links | C | Only a located quote becomes an `EvidenceQuote` | **approved H3** (F7); was PENDING H3 |

## 2. Reported values (E-02 `ScientificObservation`, E-08 `ExtractedValue`/`ReportedPeak`, E-09 quantity rows)

| Donor field | F2W field (current or T06 record) | Intended meaning | Opt. | Validation | Status |
|---|---|---|---|---|---|
| `quantity_label` / `quantity` (closed `QuantityName`) | `QuantityRecord.name`; terms JSON `properties[].property` | What was measured, as the strategy labels it | R | `dimension_mismatch` vs unit; donor names are not F2W slots | adapter; **PENDING H3**: mapping of each donor name (e.g. `domain_spacing`, `film_thickness`, `first_peak_q`) to an F2W property/slot |
| `value` | 0.3.0 `QuantityValue.numeric_value`; `NormalizedQuantity.raw_value`; terms JSON `properties[].value` | Number exactly as printed | C | Finite; `source_support` requires the same number next to an equivalent unit inside the quote | **approved H3** (F2: every value captured) |
| `unit` | `NormalizedQuantity.raw_unit`; LinkML `unit` (MaterialProperty) | Unit exactly as printed | C | Closed tables only; unknown unit → `unit_not_convertible` | existing (`unit`) + adapter |
| (normalized value) | `NormalizedQuantity.value`/`unit`/`factor`/`profile` | Value in nm, nm^-1, eV or deg with conversion provenance | C | `forge-units-v1`; refuses rather than guesses (no `pint`) | adapter; **PENDING H3**: whether a normalized value becomes a schema slot |
| `qualitative_value` | 0.3.0 `QuantityValue.qualitative_value`; `QuantityRecord.qualitative_value` | Non-numeric result as printed | C | A record needs a value or a qualitative value | **approved H3** (F2) |
| `uncertainty`, `uncertainty_kind` | 0.3.0 `QuantityValue.uncertainty_value` (≥ 0), `uncertainty_kind` (`UncertaintyKindEnum`: standard_deviation/standard_error/interval/unknown); `MeasuredUncertainty(value, kind)` | Uncertainty the paper states for the measurement | O | ≥ 0; never mixed with extraction confidence; `uncertainty_not_stated` marker | **approved H3** (F3) |
| chunk `confidence` / extraction `confidence` | `QuantityRecord.extraction_confidence`; `ChunkCoverage` | Confidence of the extraction step | O | [0, 1]; says nothing about the measurement | adapter |
| `status` / `observation_status` (reported/cited/derived/assumed) | 0.3.0 `evidence_source_tag` (`EvidenceSourceEnum`, required on `QuantityValue`, optional on `RunCondition`); `EvidenceSource` on `ReportedQuantity`/`QuantityRecord` | How the source relates to the value (evidence-source axis) | R | All values captured and tagged, none withheld for its tag (F2); a `reported` value inside an attributing sentence is tagged `cited` and an assumed instrument value `assumed` (F8; T06B changes today's withholding to tagging) | **approved H3** (F2/F8) |
| `sample_ids`, `sample_id`, `processing_state`, `temperature_k` | 0.3.0 sample qualifiers on `QuantityValue`: `qualifier_sample` (→ `Sample`), `qualifier_processing_state`, `qualifier_temperature_k`; records: `QuantityRecord.sample_id`, `processing_state` | Paper-local qualifiers | O | `source_support` qualifier check available; dangling sample refs withheld | **approved H3** (F3) |
| `measurement_id` | 0.3.0 `run_produced_quantity` (`MeasurementRun` → `QuantityValue`) | Observation-to-measurement link | O | Unknown measurement ref withheld | **approved H3** (F5) |
| `ReportedPeak.q_value`/`q_unit`, `d_spacing_value`/`d_spacing_unit`, `label`, `direction` | `ReportedQuantity` `peak_q[label]` / `peak_d_spacing[label]`; LinkML `peak_positions`, `d_spacing`, `peak_assignments` (strings) | Reflection positions as printed | C | Never derive q from d or back; reciprocal units via closed table | **PENDING H3**: whether to populate the existing string slots `peak_positions`/`d_spacing`/`peak_assignments` on `ExperimentalTechnique` |
| `location` | `ReportedQuantity.location_label` → `EvidenceRecord.section_label` | Region label | O | Pointer only, never evidence text | adapter |

## 3. Experiment structure (E-02 `SampleRecord`, `MeasurementRecord`; E-08 paper-level fields)

| Donor field | F2W field | Intended meaning | Opt. | Validation | Status |
|---|---|---|---|---|---|
| `SampleRecord` (`sample_id`, `label`, `composition`, `phase`, `processing_state`, `preparation`) | 0.3.0 `Sample` (`sample_label`, `sample_composition`, `sample_phase`, `sample_processing_state`, `sample_preparation`); `made_of_material` → `Material` per composition entry; `supporting_quote` | One paper-local physical sample | O | Quote must be located | **approved H3** (F5) |
| `MeasurementRecord.technique` | 0.3.0 `MeasurementRun.run_of_technique` → `ExperimentalTechnique`, or `RSoXSMeasurement` for the exact RSoXS aliases (existing classes, meaning unchanged) | Technique used | R | Free string; exact alias table only, no fuzzy class match | **approved H3** (F5/F6) |
| `MeasurementRecord.instrument` | 0.3.0 `MeasurementRun.run_on_instrument` → `Instrument` (`instrument_category`: beamline/endstation/detector; never guessed); not `Device` | Instrument used | O | Shared across papers by name, canonicalized in T07 | **approved H3** (F5/F6) |
| `MeasurementRecord.conditions` (`dict[str,str]`) | 0.3.0 `MeasurementRun.run_stated_condition` → `RunCondition is_a Condition` (`condition_name`, `condition_text` as printed, `numeric_value` + `unit` when the text is a number and a unit, `evidence_source_tag`) | Measurement conditions | O | Typed name/value/unit; no invented numbers | **approved H3** (F5) |
| `morphology`, `orientation`, `material_system`, `evidence` (E-08) | `ExtractionResult.unmapped`, `side_outputs.structure` | Paper-level structural reading | O | Closed enums; morphology `unknown` when not stated | **PENDING H3**: LinkML `Structure` class usage |

## 4. Entity mentions (E-01)

| Donor field | F2W field | Intended meaning | Opt. | Validation | Status |
|---|---|---|---|---|---|
| `ResolvedTerm.surface` | `ExtractedMention.surface`; terms JSON `term` | Text as the source/model gave it | R | Kept even when canonical | adapter |
| `ResolvedTerm.category` (`technique`/`analysis`/`instrument`/`material`) | `ExtractedMention.category` (donor taxon); terms JSON `category` (LinkML class) | Kind of entity | R | Not remapped | **approved H3** (F6): technique→`ExperimentalTechnique` (RSoXS → `RSoXSMeasurement`), analysis→`Method`, instrument→0.3.0 `Instrument`, material→`Material`/`ChemicalEntity` |
| `ResolvedTerm.canonical`, `slug`, `name` | `ExtractedMention.canonical`, `slug` | Resolved against an approved vocabulary | R | Default resolver keeps every term a candidate | **PENDING H3/T07**: approved vocabulary authority |
| `software` | `ExtractedMention.attributes.software` | Analysis software | O | — | adapter; **PENDING H3**: slot |
| `identity` (`explicit_definition`, `molecular_structure`, SMILES) | `ExtractedMention.attributes.identity`; terms JSON `smiles`/`chebi` | How identity was decided | O | SMILES kept as `explicit_smiles_unverified` (no RDKit check in T06) | adapter |
| `chunk_id`, `extracted_by` (`llm`/`keyword`) | `ExtractedMention.chunk_id`, `extracted_by`; `ChunkCoverage` | Where and how found | R | — | adapter |
| `chunks[].ok/error/requested_model/executed_model` | `ChunkCoverage.ok/error/settings`; `ExtractionResult.incomplete_chunks` | Coverage per chunk; failures visible | R | `ok=None` for the legacy extractor (it does not report page failures) | adapter |
| (F2W-only) `formula`, `formula_validation`, `chebi`, `smiles`, `inchi`, `inchikey`, `charge`, `mass`, `relations`, `properties`, code snippets | `ExtractedMention.attributes`, `side_outputs.terms`/`code_snippets` verbatim | Existing F2W side outputs | O | Unchanged | existing (must not be dropped) |

## 5. Hard gates proposed for H3 (none enabled)

1. Withhold a value whose quote is not located, is ambiguous, or does not lexically
   contain the same number and an equivalent unit. *Implemented as a validation
   stage that only writes candidates; making it a hard gate for graph writes is pending.*
2. Withhold a `reported` value inside a sentence attributing it to other work.
3. Withhold a unit that contradicts the quantity's dimension.
4. Never accept an `assumed` instrument-geometry value (beam energy, detector
   distance, pixel size, beam centre, incidence angle, wavelength) as paper
   evidence. A *reported* photon energy can still support `RSoXSMeasurement.photon_energy_eV`
   once mapped.
5. Whether `cited` and `derived` values may be published at all, and with which marker.

H3 decided (F8): gates 1 (quote located; number and unit in the quote) and 3 are hard
gates on graph writes. Gates 2 and 4 become tags instead (`cited`, `assumed`), and
every value is captured with its `evidence_source_tag` (F2).

## 6. Owner questions for H3 (answered 2026-10-16, decision.json; kept for the record)

- Approve or amend the donor quantity-name → F2W property/slot mapping (section 2).
- Approve the evidence-source axis slot and its allowed values.
- Approve populating `peak_positions` / `d_spacing` / `peak_assignments` from peaks.
- Approve (or defer) Sample/Measurement candidate records as schema classes.
- Approve the taxon → class table in section 4, including where `instrument` goes.
- Approve graph predicates for quote→section and value→evidence links.
- Decide which of the gates in section 5 become hard gates on graph writes.

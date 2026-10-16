# Human review packet: H3 (scientific contract and binary cases)

Gate: H3
Approver: project owner
Status: **approved 2026-10-16, B4 to follow** (see `decision.json`)

H3 asks the owner to approve the PASS/FAIL cases and oracles, the schema
binding under the preserved schema, the field mappings for extracted records,
and the merge behaviour. H3 blocks new validation mappings, automatic merges
and scientific publication. Nothing in this list is enabled today: every
extracted record is a candidate, every merge needs an approval for its exact
plan, and the binary gate reads NOT_PASS.

## 1. What is ready for review

| Area | Task | Where |
|---|---|---|
| Binary cases, runner, pre-H3 report | T11A | `evals/binary/`, `evidence/binary_report_pre_h3.{md,json,xml}`, `case_changes.md` |
| Schema registry and fingerprints | T22B | `app/modules/schema/`, `docs/adr/schema-ownership.md`, `docs/migration/schema_inventory.json` |
| Extraction and evidence bridge, field mapping | T06 | `app/modules/{extraction,evidence}/`, `docs/migration/extraction_field_mapping.md` |
| Canonicalization and merge measurement | T07 | `app/modules/canonicalization/`, `evidence/measurement_report.{md,json}` |
| Paper figures and tables | T09 | `app/modules/vision/`, `app/modules/science/` |
| Knowledge Operations inventory | T19A | `docs/migration/operations_inventory.md` |

`forge_merge` is local only. Full isolated suite at `e328b8a`: 2680 passed;
only the 3 known failures. Still running and not needed for H3: T10 (durable
jobs) and T25C (real ALS roles against a disposable Tiled); both feed H4.

## 2. Binary suite (T11A)

- **All 108 companion cases are kept,** each `proposed`. Pre-H3 run with fakes:
  44 completed PASS, 8 skipped (need the Tiled test server), 56 blocked with the
  missing task named. Gate NOT_PASS, which is correct before H3.
- **Gate rules:** binary. A skipped, timed-out, blocked, errored or
  invalid-judgment case is never a pass or a scientific failure. A required
  case that is only partly covered or unapproved keeps the gate closed. One run,
  no retry.
- **20 cases changed expectation,** each because of a recorded owner decision
  (`case_changes.md`). Flagged for a first look:
  - **REL-05** reverses the companion wording: tracked KG JSON stays in the
    image (O-D1); only PDFs, parsed bodies, crops, the source-RAG index,
    credentials, local paths and databases are forbidden.
  - **QUOTE-01:** no count limits plus the 40% single-page rule (O-D2).
  - **AUTH-02/AUTH-03** carry the per-user settings decision (H2 Q4).
  - **TIL-07/DEP-02** assume the interim Tiled 0.2.18 pin (O-T1).
- **Draft rubrics** for extraction, chat and figures need fixtures, labels and
  a judge (decisions B4-B7).

## 3. Decisions requested

### 3.1 Binary cases and rubrics

| # | Decision | Recommendation |
|---|---|---|
| B1 | Approve all 108 cases as the required set, with the 20 changed expectations | approve |
| B2 | Approve the gate rules and the repeat policy (1 run, no retry, all runs must pass) | approve |
| B3 | Is the 40% single-page/section rule the QUOTE-01 oracle? | yes |
| B4 | Pilot fixture set: which Works, gold records, gold questions and figures; who labels them (the labeler cannot be the implementing agent) | owner names about 12 Work families and a labeler |
| B5 | Extraction: is a record-level miss a FAIL, or is a partially complete record allowed? | FAIL for required fields, allowed for optional fields |
| B6 | Numeric claims: is lexical source support enough, or must entailment always be judged? | lexical match for the number and unit, plus a judged entailment check only when wording differs |
| B7 | Judge model for disputed semantic verdicts (through the existing F2W clients), and is a second human reviewer required for disputes? | a CBORG model you choose; human review for every disputed verdict |
| B8 | Figures: any coordinate tolerance? None is proposed | none |

### 3.2 Schema (T22B, preserved semantics)

| # | Decision | Recommendation |
|---|---|---|
| S1 | `rsoxs_schema_extract.yaml` (never loaded, does not resolve on its own) | keep as known-broken; a versioned fix later if wanted |
| S2 | Local default extraction schema: `matkg_schema.yaml` (today's default) or `rsoxs_schema.yaml` (what compose uses) | `rsoxs_schema.yaml` for RSoXS corpora, `matkg` otherwise (as the snapshot builder already binds) |
| S3 | `extract_terms.py` normalizer differs from `term_extractor/schema.py` (missing 23 category and 7 relation aliases) | unify later as a reviewed behaviour change; keep both now |
| S4 | Schema license: files say MIT, repo is LBNL BSD-style | your call (licensing) |
| S5 | Bind xray graphs to `xray_schema.yaml`? | yes |

### 3.3 Extraction field mapping (T06, `extraction_field_mapping.md`)

All eight items are proposals; none changes the schema until you approve.

| # | Item | Recommendation |
|---|---|---|
| F1 | Map FORGE quantity names (`domain_spacing`, `film_thickness`, `first_peak_q`, ...) onto F2W properties/slots | approve the table, reviewed per row |
| F2 | Evidence-source axis (reported/cited/derived/assumed): add a slot; may cited or assumed values be published? | add the slot; publish only reported and derived, keep cited and assumed as candidates |
| F3 | Slots for uncertainty kind and sample qualifiers | add both |
| F4 | Fill existing `peak_positions` / `d_spacing` / `peak_assignments` from extracted peaks | yes, reported values only |
| F5 | Sample and Measurement records: schema classes or candidate-only? | candidate-only for this release (D20) |
| F6 | FORGE category to F2W class table (instrument has no home yet) | approve; instrument stays a candidate |
| F7 | Graph links quote-to-section and value-to-evidence | approve, as evidence edges |
| F8 | Which validation rules become hard gates on graph writes | quote located, number+unit in quote, unit consistent with quantity |
| F9 | Wrap the `term_extractor` agent path (used after a UI extraction approval) as a strategy too? | yes, so it can be compared with the other paths |

### 3.4 Canonicalization (T07, `measurement_report.md`)

Fixture-only measurement with stand-in judges (not real models): the baseline
full-list prompt made 7 false merges with a permissive judge; the FAISS
shortlist hid 5 of 13 true matches from the judge and skipped every merge with
no index; the new candidate search made 12 correct merges, 0 false merges,
with prompts about a tenth of the size. The safety check is what blocks bad
merges.

| # | Decision | Recommendation |
|---|---|---|
| C1 | Which merge path becomes the default in extraction: baseline full-list, FAISS shortlist (pre-migration), or the new candidate search | new candidate search, after a real-model rerun (C4) |
| C2 | With no merge index: keep `no_match`, run the full list, or use lexical channels | report "index unavailable"; never skip silently |
| C3 | Approved vocabulary aliases skip the safety check (audited before activation); synonym aliases need a recorded reviewer exception | approve |
| C4 | Rerun the measurement with which real encoder and judge, and with the actual 222b1e7 prompt text | the pre-migration encoder (`paraphrase-MiniLM-L6-v2`) and the C1 judge |
| C5 | Similarity floor 0.85 holds some real variants for review ("Pilatus 2M" vs "PILATUS 2M detector") | keep, tune on pilot data |

### 3.5 Figures (T09)

| # | Decision | Recommendation |
|---|---|---|
| G1 | `figure_required` defaults to true (figures with no in-figure text fail unless waived) | yes |
| G2 | Add a physical-plausibility check before reading an axis as q | yes |
| G3 | Panel and scale-bar thresholds come from FORGE's corpus | validate on the pilot at H4 |

### 3.6 Knowledge Operations definitions (T19A)

| # | Decision | Recommendation |
|---|---|---|
| K1 | Counting unit for "papers": PDF filename keys (today), WorkVersions, or distinct Works | distinct Works for Tool output; legacy CLI unchanged |
| K2 | corpus_qa per-year histogram overcounts: correct the CLI or add a versioned distinct-paper histogram | add the versioned one; keep the CLI |
| K3 | corpus_qa §3/§5 encode a measurement-vs-technique split (D20 conflict) | exclude from Tool output |
| K4-K7 | Tiled experiment counts in scope; retiring one-off v3/v7 builders and `scripts/app/modules/kg_rag_api.py`; `list_missing` repo-root output; `analyze_kgs` silent skips | defer to T17B/H8 (legacy consolidation) |

## 4. What H3 approval unblocks

Executing the approved cases (T11B), applying approved field mappings in the
validation stage, the real-model merge rerun, and the integrated pilot (T12)
toward H4. Publication to any Tiled target still needs H5.

## 5. Not requested

Any push, image build, central Tiled access, or live model evaluation beyond
what B7/C4 approve. Approving H3 does not publish anything.

## Owner decision

Decision: approved ("Approve, B4 to follow"); rest of packet as recommended, with the F2/F8 changes in `decision.json`
Limits: B4 pilot papers and labeler open; C4 paid rerun needs owner go; publication still H5
Date: 2026-10-16

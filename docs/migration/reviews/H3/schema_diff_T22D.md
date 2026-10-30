# T22D schema diff for owner review: schema set 0.3.1 and bl1101 v3

**What you are asked to do:** read this page and approve, amend or reject schema set 0.3.1. **Until you approve
it, nothing uses it.** As with 0.3.0 (T22C), no default build, extraction or publish path selects it; a graph can be
built or published with it only by naming it twice (schema id `rsoxs_schema@0.3.1` / `bl1101_schema@0.3.1` plus
`allow_pending_review=True` / `allow_pending_review_schema=True`, or an explicit supported fingerprint for a Tiled
import). Approval is one change: set `review_status` of the `0.3.1` entry of `SCHEMA_SETS` in
`app/modules/schema/registry.py` to `in_use`. The default schema stays the base set until you decide otherwise
(you said you keep the default until the pilot).

Decisions this implements (`docs/migration/reviews/H1/owner_decisions.md`, batch 13, O-S2, 2026-10-30;
`plan_addenda.md` A7 T22D):

- **D1.4:** add a normalized-value slot to 0.3.0 (converted value, unit, factor and conversion profile as
  provenance), as a versioned additive change: 0.3.0 becomes 0.3.1.
- **D1.3:** reconcile bl1101 now: bl1101 v3 maps its `Sample`, `Detector` and `Endstation` onto 0.3.0's `Sample`
  and `Instrument`, as a reviewed diff before the pilot.

Not in this change: D1.5/D3.12 (2 % tolerance), D1.6, D1.7, D1.9, D1.10 (already on `forge_merge`).

## 1. In one paragraph

0.3.0 records a value exactly as printed ("3.4 Å", "0.2848 keV"). 0.3.1 lets the same `QuantityValue` also carry
the value in the normalized unit of its dimension ("0.34 nm", "284.8 eV") together with the factor that was applied
and the name of the conversion-table version that supplied it, so a normalized number is always traceable to the
printed one. The printed value stays the record; the normalized slots are filled only when the printed unit is in a
closed conversion table, and left absent otherwise (never guessed). Separately, the beamline-operations overlay gets
a version 3 that says, in a three-row table you can read in the file, which literature class each of its `Sample`,
`Detector` and `Endstation` records corresponds to: `Sample` to `Sample`, `Detector` and `Endstation` to
`Instrument` of kind `detector` / `endstation`. The ops classes themselves do not change; the table is applied only
on request and round-trips exactly. **Nothing in 0.3.0 or in bl1101 v2 changed meaning, and nothing was removed.**

## 2. Where it lives and what did not change

| | Base set (in use) | Set 0.3.0 (T22C, pending) | Set 0.3.1 (T22D, pending your review) |
| --- | --- | --- | --- |
| Files | `storage/schema/matkg_schema.yaml` (0.2.0), `rsoxs_schema.yaml` (rsoxs_v1), `bl1101_schema.yaml` (bl1101_v2), xray, extract overlays | `storage/schema/v0.3.0/matkg_schema.yaml` (0.3.0), `rsoxs_schema.yaml` (rsoxs_v2) | `storage/schema/v0.3.1/matkg_schema.yaml` (0.3.1), `rsoxs_schema.yaml` (rsoxs_v3), `bl1101_schema.yaml` (bl1101_v3) |
| Registry ids | `matkg_schema`, `rsoxs_schema`, `bl1101_schema`, ... | `matkg_schema@0.3.0`, `rsoxs_schema@0.3.0` | `matkg_schema@0.3.1`, `rsoxs_schema@0.3.1`, `bl1101_schema@0.3.1` |
| Used by default | yes, everywhere | never | never |
| Closure fingerprint (first 16 hex; linkml-runtime 1.11.1) | matkg `8ec418bb4291d3b8`, rsoxs `3a8d9b223065abcd`, bl1101 `34592ebf85b8ff54` | matkg `8da8fda4216aa724`, rsoxs `74a06af4c349864b` | matkg `a3d1830d92368c52`, rsoxs `be1206e889e08fd2`, bl1101 `a26c59efbaa229e1` |

The 0.3.1 files keep the base file names, so each overlay's `imports: [matkg_schema]` finds the 0.3.1 base next to
it (a test checks the closure hashes). 0.3.0 stays registered, loadable and selectable exactly as before; its files
are untouched. The xray schemas stay on the base set (they share none of these terms); under 0.3.1 an xray graph still
binds `xray_schema.yaml`.

**Additivity, checked by `tests/schema/test_schema_t22d.py`:**

- matkg 0.3.0 -> 0.3.1 and rsoxs_v2 -> rsoxs_v3: no class, slot or enum removed; no `is_a` changed; **no class
  added**; every 0.3.0 class, slot and enum definition is identical, except that `QuantityValue` lists four more
  slots. `preview_diff` reports exactly `slots_added = [conversion_factor, conversion_profile, normalized_unit,
  normalized_value]`.
- bl1101 v2 -> v3: every class keeps its parent, slots and induced slots; every slot definition is identical. The only
  differences are the version label, the header comment, the description of the overlay and of the three mapped
  classes, and the mapping annotations on `Sample`, `Detector` and `Endstation` (section 4). The classes and slots
  that appear "added" in the bl1101 diff are the six imported 0.3.x classes and their slots (`Instrument`,
  `MeasurementRun`, `QuantityValue`, `RunCondition`, `EvidenceQuote`, `PaperSection`), which come with the new base.
- `bl1101_motor_stage_map.yaml` (version 4) and the ingest (`bl1101_ingest.SCHEMA_VERSION = "bl1101_v2"`) are not
  changed: no motor or stage semantics move.

## 3. D1.4: the normalized-value slots (4 slots, all on `QuantityValue`, all optional)

| Slot | Range | Means | Example (printed "3.4 Å") |
| --- | --- | --- | --- |
| `normalized_value` | float | The printed value converted to the normalized unit of its dimension: `normalized_value = numeric_value * conversion_factor` | 0.34 |
| `normalized_unit` | string | The unit `normalized_value` is in: `nm` (length), `nm^-1` (reciprocal), `eV` (energy) or `deg` (angle) | nm |
| `conversion_factor` | float | The multiplicative factor applied, from the profile's closed tables | 0.1 |
| `conversion_profile` | string | Provenance: the version of the closed conversion tables that produced the two numbers | `forge-units-v1` |

Rules, as implemented in `app/modules/schema/experiment_mapping.py` (`normalized_slots`) and checked by tests:

- `numeric_value` and `unit` keep the value **exactly as printed** (their descriptions are unchanged: "no
  conversion"). The four new slots sit beside them; a 0.3.1 record with the four slots removed is byte-for-byte the
  0.3.0 record.
- The slots are filled only from `app.modules.evidence.units.normalize_quantity`, the existing E-05 code ("refuse,
  never guess"): a unit outside the closed tables, a non-finite number or a non-positive q leaves all four absent.
  Nothing new decides a conversion.
- They are filled only under a schema that declares all four (0.3.1); the 0.3.0 mapping output is unchanged (a test
  compares the two payloads).
- `RunCondition` keeps its printed `numeric_value`/`unit` only; the normalized slot is a `QuantityValue` slot. If you
  want it on conditions too, that is a one-line addition inside the 0.3.1 file (section 7, question 2).
- No enum, no new class, no change to `EvidenceSourceEnum`/`UncertaintyKindEnum`; the derived-value provenance of
  D1.5/D1.6 (`reciprocal_derivation`, same `profile` field) stays on the T06 record.

## 4. D1.3: bl1101 v3 and its mapping table (3 rows)

The table is written in the schema, not in code: each of the three classes carries two class annotations, and a
LinkML mapping line that says the same thing in the standard vocabulary. `app/modules/schema/bl1101_mapping.py`
reads the annotations and applies them; it has no table of its own.

| bl1101 class (unchanged parent) | annotation `f2w_maps_to_class` | annotation `f2w_maps_to_instrument_category` | LinkML mapping | What a mapped record gets |
| --- | --- | --- | --- | --- |
| `Sample` (`is_a Material`) | `Sample` | - | `close_mappings: [matkg:Sample]` | `category: Sample`, `sample_label` = the ops `name` |
| `Detector` (`is_a Device`) | `Instrument` | `detector` | `broad_mappings: [matkg:Instrument]` | `category: Instrument`, `instrument_category: detector` |
| `Endstation` (`is_a Device`) | `Instrument` | `endstation` | `broad_mappings: [matkg:Instrument]` | `category: Instrument`, `instrument_category: endstation` |

Each class also carries `f2w_mapping_decision: D1.3`. Two things worth knowing:

- **Why the ops `Sample` keeps `is_a Material` and shadows the literature `Sample`.** bl1101 v3 imports the 0.3.1
  base, which has the paper-local `Sample is_a Thing`. LinkML lets the importing overlay's definition win, so inside
  the bl1101 closure `Sample` is still the ops catalog class ("polymer thin films", "solid thin films", ...), with
  its parent and `part_of` slot. That is why all nine tracked bl1101 graphs validate under bl1101 v3 with a report
  identical to today's (section 6): nothing is retyped by validation. The relationship to the literature `Sample` is
  stated by the table (`close_mappings`, not `exact`: an ops catalog entry is a *class* of sample, a paper's `Sample`
  is one specimen). If you would rather the ops Sample become the literature Sample outright (`is_a Thing`, gaining
  `sample_label` etc.), that is a one-line change in the v3 file plus a decision on the 3-241 `Sample` nodes per ops
  graph, which this change deliberately does not make.
- **What the mapping does and does not do.** `map_ops_graph` turns every `Sample`/`Detector`/`Endstation` node into
  a 0.3.x record: `category` and `type` change, the ops class name moves into the existing `raw_category` slot, the
  one new slot (`sample_label` or `instrument_category`) is added, and everything else (id, name, description,
  `source_papers`, PV names, ophyd handles) is kept verbatim. A small `ops_mapping` provenance block records the
  schema (`bl1101_schema@0.3.1`, `bl1101_v3`, its sha256), the mapping version and the decision, so `unmap_ops_graph`
  restores the ops graph **exactly** (tests prove `unmap(map(G)) == G` on all nine tracked bl1101 graphs and that
  both directions are idempotent). Edges are passed through untouched: `hasDetector`, `hasEndstation`, `part_of`
  keep their beamline meaning and are not reinterpreted as literature links (`run_on_instrument` etc.). Nodes of
  any other class, and `Beamline`, pass through unchanged (section 7, question 3). The mapping is pure, writes
  nothing, and is refused without `allow_pending_review=True`; nothing calls it by default.

Mapped records are inside the `rsoxs_schema@0.3.1` and `matkg_schema@0.3.1` closures (no node outside it) and
outside the base closure by exactly the names `Sample` and `Instrument`.

## 5. Where 0.3.1 is filled from (extraction)

`run_validation_stage(..., schema_set="0.3.1", allow_pending_review=True)` builds the candidate 0.3.1 payload the
same way it builds the 0.3.0 one (`schema_selection.SELECTABLE_SCHEMA_SETS` now lists both), and every accepted
`QuantityValue` whose printed unit converts carries the four slots. The default stage (`schema_set=None`) maps
nothing, as before. `extraction_field_mapping.md` row "(normalized value)" now points at the 0.3.1 slots (was
PENDING H3).

## 6. What was checked (tests)

- **Every tracked KG validates under 0.3.1** (`tests/schema/test_schema_t22d_tracked_kgs.py`): all 57 tracked
  `storage/kg/*.json` pass with no contract error. The 12 promotion snapshots give a report **identical** to their
  base-set report: the rsoxs graphs under `rsoxs_schema@0.3.1`, the bl1101 graphs under `bl1101_schema@0.3.1`
  (which 0.3.0 could not offer). The 45 other graphs (LLM extraction graphs, `matkg_with_code`, the xray demo) are
  checked against `matkg_schema@0.3.1`: same node and edge counts, same contract/incomplete/source diagnostics, and
  no more nodes outside the closure than under the base. No KG file changes (hashes before and after).
- **Registration and binding** (`tests/schema/test_schema_t22d.py`): 0.3.1 is registered after 0.3.0 with its own
  files and fingerprints (eight distinct fingerprints across base, 0.3.0, 0.3.1); selection needs
  `allow_pending_review=True`; a fingerprint of 0.3.0 does not bind 0.3.1 or the reverse; the default Tiled supported
  set excludes 0.3.1; a snapshot build refuses it unless asked twice; every entrypoint default still resolves to the
  base set; 0.3.0 and the base set load and select as before.
- **Additivity**: as in section 2, by `preview_diff` and definition-by-definition comparison.
- **Normalized slot filled and round-tripped**: the T22C synthetic paper mapped onto 0.3.1 carries
  `normalized_value`/`normalized_unit`/`conversion_factor`/`conversion_profile` equal to `normalize_quantity`'s
  output on every `QuantityValue`, nothing else differs from the 0.3.0 payload, conditions are untouched, the payload
  validates inside the 0.3.1 closure, and the JSON graph backend returns every record verbatim
  (`tests/graph/test_graph_t22d_normalized.py`). The T06 stage fills the slots only on explicit 0.3.1 selection.
- **bl1101 v3 round trip** (`tests/schema/test_schema_t22d_bl1101_mapping.py`): the table read from the overlay is
  exactly the three rows above and names only classes and kinds the closure defines; bl1101 v2 has no table and is
  refused; `unmap(map(G)) == G` on all nine tracked bl1101 graphs; mapped records keep every ops field verbatim and
  validate inside the 0.3.1 literature closure.
- **Frozen T22B normalization outcomes** hold under both 0.3.1 schemas.

## 7. Open questions for you

1. **Approve 0.3.1?** One registry line, as described at the top. Switching any default to it is a separate change.
2. **Normalized slot on `RunCondition` too?** Not done (D1.4 names the quantity/value class). One line if wanted.
3. **`Beamline` -> `Instrument` (kind `beamline`)?** T22C section 8 asked about it; D1.3 names only `Sample`,
   `Detector` and `Endstation`, so `Beamline` is not mapped. One more annotated class if wanted.
4. **Ops `Sample`: table (as done) or `is_a Thing`?** See section 4.
5. **Edges under the mapping.** Ops predicates pass through unchanged. If a literature edge is wanted for
   `Endstation hasDetector Detector` (there is no 0.3.x slot for instrument-to-instrument containment), that is a
   schema addition, not a mapping change.

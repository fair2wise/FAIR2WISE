"""T22D (owner decisions D1.3/D1.4, O-S2): schema set 0.3.1 is additive over 0.3.0, registered beside it, never
chosen by a default path; the normalized-value slots are filled from ``normalize_quantity`` and nothing else
changes meaning. 0.3.0 and the base set stay loadable and selectable exactly as before."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.modules.evidence.units import CONVERSION_PROFILE, normalize_quantity
from app.modules.graph.imports import tiled_json as tj
from app.modules.schema.experiment_mapping import NORMALIZED_SLOTS, map_validated_records, normalized_slots
from app.modules.schema.loading import closure_files, closure_fingerprint, load_helper, load_schema_view
from app.modules.schema.migrations import preview_diff
from app.modules.schema.registry import (
    DEFAULT_SCHEMA_ID,
    ENTRYPOINT_DEFAULTS,
    LICENCE_ID,
    REVIEW_PENDING,
    SCHEMA_SETS,
    RegistryError,
)
from app.modules.schema.validation import check_binding, validate_graph
from tests.schema.t22c_fixtures import SOURCE_PAPER, WORK_ID, make_chunk, make_document

REPO = Path(__file__).resolve().parents[2]
NEW = ("matkg_schema@0.3.1", "rsoxs_schema@0.3.1", "bl1101_schema@0.3.1")
PREVIOUS = {"matkg_schema@0.3.1": "matkg_schema@0.3.0", "rsoxs_schema@0.3.1": "rsoxs_schema@0.3.0",
            "bl1101_schema@0.3.1": "bl1101_schema"}
NEW_SLOTS = sorted(NORMALIZED_SLOTS)
REVIEW_DOC = "docs/migration/reviews/H3/schema_diff_T22D.md"
T22C_CLASSES = ["EvidenceQuote", "Instrument", "MeasurementRun", "PaperSection", "QuantityValue", "RunCondition"]


def _mapped(registry, schema_id):
    from app.modules.extraction.record_validation import validate_against_document

    document = make_document()
    validation = validate_against_document(make_chunk(), document, work_id=WORK_ID, pub_path="publications/x",
                                           strategy="t22d-fixture")
    return document, validation, map_validated_records(
        validation, document, work_id=WORK_ID, source_paper=SOURCE_PAPER, entry=registry.get(schema_id),
        allow_pending_review=True)


# --- registration ---------------------------------------------------------------

def test_set_031_is_registered_after_030_with_its_own_files_and_fingerprints(registry):
    assert [s.version for s in SCHEMA_SETS] == ["0.3.0", "0.3.1"]
    new = next(s for s in SCHEMA_SETS if s.version == "0.3.1")
    assert new.members == ("matkg_schema", "rsoxs_schema", "bl1101_schema") and new.review_doc == REVIEW_DOC
    entries = registry.set_entries("0.3.1")
    assert [e.schema_id for e in entries] == list(NEW)
    for entry in entries:
        assert entry.path == f"storage/schema/v0.3.1/{entry.file}"
        assert entry.abs_path.parent == REPO / "storage" / "schema" / "v0.3.1"
        assert entry.review_status == REVIEW_PENDING and entry.pending_review
        assert entry.base_id == entry.schema_id.split("@")[0] and entry.pre_s4_sha256 is None
        assert entry.selectable_for == registry.get(entry.base_id).selectable_for
    assert registry.get("matkg_schema@0.3.1").version == "0.3.1"
    assert registry.get("rsoxs_schema@0.3.1").version == "rsoxs_v3"
    assert registry.get("bl1101_schema@0.3.1").version == "bl1101_v3"
    # every overlay imports the 0.3.1 base next to it, never the base file or the 0.3.0 file of the same name
    new_matkg = registry.get("matkg_schema@0.3.1").sha256
    for sid in ("rsoxs_schema@0.3.1", "bl1101_schema@0.3.1"):
        assert registry.get(sid).declared_imports == ("matkg_schema",)
        closure = dict(closure_files(registry.get(sid)))
        assert set(closure) == {"matkg_schema.yaml", registry.get(sid).file}
        assert closure["matkg_schema.yaml"] == new_matkg
    assert new_matkg not in {registry.get("matkg_schema").sha256, registry.get("matkg_schema@0.3.0").sha256}
    assert registry.id_collisions("0.3.1") == {}
    assert (REPO / REVIEW_DOC).is_file()
    fingerprints = {sid: closure_fingerprint(registry.get(sid)) for sid in NEW + tuple(PREVIOUS.values())}
    assert len(set(fingerprints.values())) == len(fingerprints)  # 0.3.1, 0.3.0 and base never share a fingerprint
    for sid in NEW:
        assert registry.resolve(f"storage/schema/v0.3.1/{registry.get(sid).file}").schema_id == sid
        assert registry.resolve(sid) is registry.get(sid)
        assert registry.by_fingerprint(registry.get(sid).sha256) == [registry.get(sid)]


def test_older_versions_stay_loadable_selectable_and_are_what_defaults_use(registry):
    for sid in ("matkg_schema", "rsoxs_schema", "bl1101_schema", "xray_schema"):
        entry = registry.get(sid)
        assert entry.schema_set is None and not entry.pending_review
        assert "Thing" in load_schema_view(entry).all_classes()
    assert registry.select("matkg_schema").schema_set is None
    assert registry.select("bl1101_schema", "graph-view").schema_set is None
    for sid in ("matkg_schema@0.3.0", "rsoxs_schema@0.3.0"):
        entry = registry.select(sid, allow_pending_review=True)
        view = load_schema_view(entry)
        assert "QuantityValue" in view.all_classes() and not set(NORMALIZED_SLOTS) & set(view.all_slots())
    assert "normalized_value" not in load_schema_view(registry.get("matkg_schema")).all_slots()
    assert registry.get(DEFAULT_SCHEMA_ID).schema_set is None
    assert all("v0.3" not in d.needle and registry.get(d.schema_id).schema_set is None for d in ENTRYPOINT_DEFAULTS)
    assert registry.for_graph("storage/kg/matkg_rsoxs_v3.json").schema_id == "rsoxs_schema"
    assert registry.for_graph("storage/kg/matkg_bl1101_v9.json").schema_id == "bl1101_schema"


def test_new_version_needs_explicit_selection_and_is_bound_exactly(registry):
    for sid in NEW:
        with pytest.raises(RegistryError, match="pending owner review"):
            registry.select(sid, registry.get(sid).selectable_for[0])
        assert registry.select(sid, registry.get(sid).selectable_for[0], allow_pending_review=True).schema_id == sid
    assert registry.for_graph("matkg_rsoxs_v2.json", schema_set="0.3.1").schema_id == "rsoxs_schema@0.3.1"
    assert registry.for_graph("matkg_bl1101_v9.json", schema_set="0.3.1").schema_id == "bl1101_schema@0.3.1"
    assert registry.for_graph("matkg_bl1101_v9.json", schema_set="0.3.0").schema_id == "bl1101_schema"
    assert registry.for_graph("matkg_xray_papers_cborg_chat.json", schema_set="0.3.1").schema_id == "xray_schema"
    new = registry.get("rsoxs_schema@0.3.1")
    fp = closure_fingerprint(new)
    assert check_binding(registry, new, fp) == "verified"
    from app.modules.schema.validation import SchemaBindingError

    with pytest.raises(SchemaBindingError, match="bound to registered schema 'rsoxs_schema@0.3.0'"):
        check_binding(registry, new, closure_fingerprint(registry.get("rsoxs_schema@0.3.0")))
    with pytest.raises(SchemaBindingError, match="bound to registered schema 'rsoxs_schema@0.3.1'"):
        check_binding(registry, registry.get("rsoxs_schema@0.3.0"), fp)
    supported = tj.default_supported_schema_fingerprints(registry)
    for sid in NEW:
        assert closure_fingerprint(registry.get(sid)) not in supported


def test_snapshot_build_refuses_the_pending_set_unless_asked_twice(registry, tmp_path):
    from app.modules.graph.snapshot import SnapshotError, SnapshotRequest, create_snapshot
    from tests.snapshots.snapshot_fakes import COMMIT, INPUTS

    root = tmp_path / "storage" / "kg"
    root.mkdir(parents=True)
    base = dict(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json", storage_kg=root,
                terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT, registry=registry)
    with pytest.raises(SnapshotError, match="pending owner review"):
        create_snapshot(SnapshotRequest(schema_id="rsoxs_schema@0.3.1", **base))
    assert not any(root.iterdir())
    made = create_snapshot(SnapshotRequest(schema_id="rsoxs_schema@0.3.1", allow_pending_review_schema=True, **base))
    block = made.manifest.schema_block
    assert (block.name, block.path, block.version) == ("rsoxs_schema@0.3.1", "storage/schema/v0.3.1/rsoxs_schema.yaml",
                                                       "rsoxs_v3")
    assert block.import_fingerprint == closure_fingerprint(registry.get("rsoxs_schema@0.3.1"))


# --- additive over 0.3.0 ----------------------------------------------------------------

@pytest.mark.parametrize("new", ["matkg_schema@0.3.1", "rsoxs_schema@0.3.1"])
def test_every_030_class_slot_and_enum_is_unchanged_in_031(registry, new):
    old = PREVIOUS[new]
    a, b = load_schema_view(registry.get(old)), load_schema_view(registry.get(new))
    diff = preview_diff(registry.get(old), registry.get(new))
    assert diff.classes_removed == [] and diff.slots_removed == [] and diff.is_a_changed == {}
    assert diff.classes_added == [] and diff.slots_added == NEW_SLOTS
    for name, cls in a.all_classes().items():
        before, after = dict(cls._as_dict), dict(b.get_class(name)._as_dict)
        if name == "QuantityValue":  # the only class that lists the new slots
            assert after["slots"][:len(before["slots"]) - 1] == before["slots"][:-1]
            assert set(after["slots"]) - set(before["slots"]) == set(NEW_SLOTS)
            before.pop("slots")
            after.pop("slots")
        assert before == after, name
        induced_before = [s.name for s in a.class_induced_slots(name)]
        induced_after = [s.name for s in b.class_induced_slots(name)]
        if name == "QuantityValue":
            assert set(induced_after) - set(induced_before) == set(NEW_SLOTS)
        else:
            assert induced_before == induced_after, name
    for name, slot in a.all_slots().items():
        assert slot._as_dict == b.get_slot(name)._as_dict, name
    assert set(a.all_enums()) == set(b.all_enums())
    for name in a.all_enums():
        assert a.get_enum(name)._as_dict == b.get_enum(name)._as_dict


def test_bl1101_v3_changes_only_the_mapping_table_and_version(registry):
    old, new = registry.get("bl1101_schema"), registry.get("bl1101_schema@0.3.1")
    a, b = load_schema_view(old), load_schema_view(new)
    diff = preview_diff(old, new)
    assert diff.classes_removed == [] and diff.slots_removed == [] and diff.is_a_changed == {}
    assert diff.classes_added == T22C_CLASSES  # from the imported 0.3.1 base; bl1101's own Sample shadows the new one
    assert diff.slots_added == sorted(set(load_schema_view(registry.get("matkg_schema@0.3.1")).all_slots())
                                      - set(load_schema_view(registry.get("matkg_schema")).all_slots()))
    for name, cls in a.all_classes().items():
        before, after = dict(cls._as_dict), dict(b.get_class(name)._as_dict)
        if name in ("Sample", "Detector", "Endstation"):
            for key in ("annotations", "broad_mappings", "close_mappings", "description"):
                before.pop(key, None)
                after.pop(key, None)
        assert before == after, name
        assert [s.name for s in a.class_induced_slots(name)] == [s.name for s in b.class_induced_slots(name)], name
    assert b.get_class("Sample").is_a == "Material"  # the ops Sample keeps its parent and shadows the 0.3.x one
    for name, slot in a.all_slots().items():
        before, after = dict(slot._as_dict), dict(b.get_slot(name)._as_dict)
        before.pop("domain_of", None)
        after.pop("domain_of", None)
        assert before == after, name
    # the motor/stage map is untouched by T22D
    assert registry.get("bl1101_motor_stage_map").sha256 == registry.get("bl1101_motor_stage_map").sha256
    from app.modules.schema.loading import load_yaml_asset

    assert load_yaml_asset(registry.get("bl1101_motor_stage_map"))["version"] == 4


def test_normalized_slots_have_the_intended_shape(registry):
    view = load_schema_view(registry.get("rsoxs_schema@0.3.1"))
    q = {s.name: s for s in view.class_induced_slots("QuantityValue")}
    assert set(NORMALIZED_SLOTS) <= set(q)
    for name in NORMALIZED_SLOTS:
        slot = view.get_slot(name)
        assert slot.domain == "QuantityValue" and slot.required is False and not slot.multivalued, name
        assert slot.slot_uri == f"matkg:{name}"
    assert q["normalized_value"].range == "float" and q["conversion_factor"].range == "float"
    assert q["normalized_unit"].range == "string" and q["conversion_profile"].range == "string"
    assert "no conversion" in " ".join(view.get_slot("numeric_value").description.split())
    assert "never guessed" in " ".join(view.get_slot("normalized_value").description.split())
    # the slot sits on QuantityValue only: RunCondition keeps the printed number and unit
    assert not set(NORMALIZED_SLOTS) & {s.name for s in view.class_induced_slots("RunCondition")}
    helper = load_helper(registry.get("rsoxs_schema@0.3.1"))
    assert set(NORMALIZED_SLOTS) <= set(helper.classes["QuantityValue"]["slots"])


def test_set_files_declare_the_lbnl_licence_and_point_at_the_review(registry):
    for sid in NEW:
        entry = registry.get(sid)
        raw = entry.abs_path.read_text(encoding="utf-8")
        assert load_schema_view(entry).schema.license == LICENCE_ID and "MIT" not in raw
        assert REVIEW_DOC in raw and "0.3.1" in raw


def test_frozen_t22b_normalization_outcomes_hold_under_031(registry):
    from tests.schema.test_schema_validation import FROZEN_RELATIONS, FROZEN_TAIL, TERMS, _outcome

    for sid, head in (("matkg_schema@0.3.1", "Thing"), ("rsoxs_schema@0.3.1", "RSoXSMeasurement")):
        helper = load_helper(registry.get(sid))
        assert [_outcome(helper, copy.deepcopy(t)) for t in TERMS] == [(head, "RSoXS", FROZEN_RELATIONS)] + FROZEN_TAIL


# --- the normalized slot is filled from normalize_quantity, only under 0.3.1 ---------------------------

def test_normalized_slots_helper_refuses_rather_than_guesses():
    assert normalized_slots(25.0, "nm") == {"normalized_value": 25.0, "normalized_unit": "nm",
                                            "conversion_factor": 1.0, "conversion_profile": CONVERSION_PROFILE}
    angstrom = normalized_slots(3.4, "Å")
    assert angstrom["normalized_unit"] == "nm" and angstrom["normalized_value"] == pytest.approx(0.34)
    assert angstrom["conversion_factor"] == normalize_quantity(3.4, "Å").factor
    kev = normalized_slots(0.2848, "keV")
    assert (kev["normalized_unit"], kev["normalized_value"]) == ("eV", pytest.approx(284.8))
    assert normalized_slots(25.0, "furlongs") == {}  # unknown unit: nothing, never a guess
    assert normalized_slots(None, "nm") == {} and normalized_slots(25.0, None) == {} and normalized_slots(25.0, "") == {}
    assert normalized_slots(-1.0, "nm^-1") == {}  # refused by normalize_quantity (q not positive)


def test_mapping_fills_the_slot_under_031_and_leaves_030_records_as_before(registry):
    _doc, _v, graph_031 = _mapped(registry, "rsoxs_schema@0.3.1")
    _doc, _v, graph_030 = _mapped(registry, "rsoxs_schema@0.3.0")
    values_031 = {(n["quantity_label"], n["numeric_value"]): n for n in graph_031["things"]
                  if n["category"] == "QuantityValue"}
    values_030 = {(n["quantity_label"], n["numeric_value"]): n for n in graph_030["things"]
                  if n["category"] == "QuantityValue"}
    assert set(values_031) == set(values_030) == {("domain_spacing", 25.0), ("film_thickness", 105.0),
                                                  ("domain_spacing", 40.0)}
    for key, node in values_031.items():
        expected = normalize_quantity(node["numeric_value"], node["unit"])
        assert node["normalized_value"] == expected.value and node["normalized_unit"] == expected.unit == "nm"
        assert node["conversion_factor"] == expected.factor == 1.0
        assert node["conversion_profile"] == CONVERSION_PROFILE == "forge-units-v1"
        # the printed value and unit are untouched and every other key is exactly the 0.3.0 record
        stripped = {k: v for k, v in node.items() if k not in NORMALIZED_SLOTS}
        assert stripped == values_030[key], key
        assert (node["numeric_value"], node["unit"]) == (values_030[key]["numeric_value"], values_030[key]["unit"])
    assert not any(set(NORMALIZED_SLOTS) & set(n) for n in graph_030["things"])
    # conditions keep number and unit as printed; the slot is a QuantityValue slot
    conditions = [n for n in graph_031["things"] if n["category"] == "RunCondition"]
    assert conditions and not any(set(NORMALIZED_SLOTS) & set(n) for n in conditions)
    # everything else in the payload is identical between the two versions
    strip = lambda g: {"things": [{k: v for k, v in n.items() if k not in NORMALIZED_SLOTS} for n in g["things"]],
                       "associations": g["associations"]}
    assert strip(graph_031) == graph_030
    assert _mapped(registry, "rsoxs_schema@0.3.1")[2] == graph_031  # deterministic


def test_mapped_031_graph_validates_inside_the_031_closure_and_warns_only_there_under_030(registry):
    _doc, _v, graph = _mapped(registry, "rsoxs_schema@0.3.1")
    report = validate_graph(copy.deepcopy(graph), registry.get("rsoxs_schema@0.3.1"), registry=registry)
    legacy = report.categories["legacy_warning"]
    assert report.status == "pass" and report.categories["contract_error"]["count"] == 0
    for key in ("nodes_with_category_outside_closure", "edges_with_predicate_outside_closure",
                "edges_failing_domain_range", "edges_with_missing_endpoint_node"):
        assert legacy[key] == 0, (key, legacy)
    # validation reads node properties as data: the same payload is also inside the 0.3.0 closure (no class or
    # predicate is new), so the extra slot values are a property-level difference only
    older = validate_graph(copy.deepcopy(graph), registry.get("rsoxs_schema@0.3.0"), registry=registry)
    assert older.status == "pass" and older.categories == report.categories


def test_t06_stage_fills_the_slot_only_on_explicit_031_selection(registry):
    from app.modules.evidence import InMemoryEvidenceSink
    from app.modules.extraction import ExtractionResult
    from app.modules.extraction.record_validation import run_validation_stage
    from app.modules.extraction.schema_selection import SELECTABLE_SCHEMA_SETS
    from tests.extraction.t06b_fixtures import PUB_PATH, control_quantities
    from tests.extraction.t06b_fixtures import make_document as t06b_document

    assert SELECTABLE_SCHEMA_SETS == ("0.3.0", "0.3.1")
    doc = t06b_document()
    result = ExtractionResult(strategy="controls", strategy_version="1", work_id="work:t22d", source_version=doc.source_ref,
                              parser_revision=doc.parser_key, complete=True, observations=tuple(control_quantities()))
    default = run_validation_stage(result, doc, InMemoryEvidenceSink(), pub_path=PUB_PATH)
    assert default.schema_mapping is None
    with pytest.raises(RegistryError, match="pending owner review"):
        run_validation_stage(result, doc, InMemoryEvidenceSink(), pub_path=PUB_PATH, schema_set="0.3.1")
    report = run_validation_stage(result, doc, InMemoryEvidenceSink(), pub_path=PUB_PATH, schema_set="0.3.1",
                                  allow_pending_review=True)
    mapping = report.schema_mapping
    assert (mapping["schema"], mapping["schema_set"], mapping["review_status"]) == (
        "rsoxs_schema@0.3.1", "0.3.1", "pending_owner_review")
    values = [n for n in mapping["payload"]["things"] if n["category"] == "QuantityValue"]
    assert values and len(values) == report.accepted
    for node in values:
        expected = normalize_quantity(node["numeric_value"], node["unit"]) if node.get("unit") else None
        if expected is not None and expected.converted:
            assert (node["normalized_value"], node["normalized_unit"], node["conversion_factor"],
                    node["conversion_profile"]) == (expected.value, expected.unit, expected.factor, CONVERSION_PROFILE)
        else:
            assert not set(NORMALIZED_SLOTS) & set(node), node
    assert any("normalized_value" in n for n in values)
    older = run_validation_stage(result, doc, InMemoryEvidenceSink(), pub_path=PUB_PATH, schema_set="0.3.0",
                                 allow_pending_review=True)
    assert not any(set(NORMALIZED_SLOTS) & set(n) for n in older.schema_mapping["payload"]["things"])

"""T22C: schema set 0.3.0 is additive, registered beside the base set, and never chosen by a default path."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.modules.evidence.records import EvidenceSource, UncertaintyKind
from app.modules.graph.imports import tiled_json as tj
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
from app.modules.schema.validation import check_binding

REPO = Path(__file__).resolve().parents[2]
INVENTORY = json.loads((REPO / "docs/migration/schema_inventory.json").read_text(encoding="utf-8"))
NEW = ("matkg_schema@0.3.0", "rsoxs_schema@0.3.0")
PAIRS = (("matkg_schema", "matkg_schema@0.3.0"), ("rsoxs_schema", "rsoxs_schema@0.3.0"))
NEW_CLASSES = ["EvidenceQuote", "Instrument", "MeasurementRun", "PaperSection", "QuantityValue", "RunCondition",
               "Sample"]
NEW_SLOTS = sorted("""sample_label sample_composition sample_phase sample_processing_state sample_preparation
    made_of_material instrument_category run_of_technique run_measured_sample run_on_instrument run_stated_condition
    run_produced_quantity condition_name condition_text quantity_label numeric_value qualitative_value
    uncertainty_value uncertainty_kind evidence_source_tag qualifier_sample qualifier_processing_state
    qualifier_temperature_k supporting_quote quote_text quote_match_type quote_char_start quote_char_end quote_page
    source_version source_sha256 parser_revision quote_located_in_section section_label section_page
    work_id""".split())
REVIEW_DOC = "docs/migration/reviews/H3/schema_diff_T22C.md"


def _enum_values(view, name):
    return set(view.get_enum(name).permissible_values)


# --- registration ---------------------------------------------------------------

def test_schema_set_is_registered_beside_the_base_set(registry):
    assert [s.version for s in SCHEMA_SETS][0] == "0.3.0"  # later sets (0.3.1, T22D) register after it
    entries = registry.set_entries("0.3.0")
    assert [e.schema_id for e in entries] == list(NEW)
    for entry in entries:
        assert entry.path == f"storage/schema/v0.3.0/{entry.file}"
        assert entry.abs_path.parent == REPO / "storage" / "schema" / "v0.3.0"
        assert entry.review_status == REVIEW_PENDING and entry.pending_review
        assert entry.base_id == entry.schema_id.split("@")[0] and entry.pre_s4_sha256 is None
        assert entry.selectable_for == registry.get(entry.base_id).selectable_for
    assert registry.get("matkg_schema@0.3.0").version == "0.3.0"
    assert registry.get("rsoxs_schema@0.3.0").version == "rsoxs_v2"
    assert registry.get("rsoxs_schema@0.3.0").declared_imports == ("matkg_schema",)
    assert [n for n, _ in closure_files(registry.get("rsoxs_schema@0.3.0"))] == ["matkg_schema.yaml",
                                                                                "rsoxs_schema.yaml"]
    # the sibling import resolves inside the set, never to the base file of the same name
    new_matkg = registry.get("matkg_schema@0.3.0").sha256
    assert dict(closure_files(registry.get("rsoxs_schema@0.3.0")))["matkg_schema.yaml"] == new_matkg
    assert new_matkg != registry.get("matkg_schema").sha256
    assert registry.id_collisions("0.3.0") == {}
    assert (REPO / REVIEW_DOC).is_file()


def test_old_version_stays_loadable_and_is_what_defaults_use(registry):
    for old, new in PAIRS:
        assert registry.select(old).schema_set is None
        assert closure_fingerprint(registry.get(old)) != closure_fingerprint(registry.get(new))
        assert "Sample" not in load_schema_view(registry.get(old)).all_classes()
    assert registry.get(DEFAULT_SCHEMA_ID).schema_set is None
    assert all("v0.3.0" not in d.needle and registry.get(d.schema_id).schema_set is None for d in ENTRYPOINT_DEFAULTS)
    assert registry.for_graph("storage/kg/matkg_rsoxs_v3.json").schema_id == "rsoxs_schema"
    assert registry.resolve("rsoxs_schema.yaml").schema_id == "rsoxs_schema"
    assert registry.resolve("storage/schema/rsoxs_schema.yaml").schema_id == "rsoxs_schema"


def test_new_version_needs_explicit_selection(registry, tmp_path):
    for sid in NEW:
        with pytest.raises(RegistryError, match="pending owner review"):
            registry.select(sid)
        assert registry.select(sid, allow_pending_review=True).schema_id == sid
    assert registry.resolve("storage/schema/v0.3.0/rsoxs_schema.yaml").schema_id == "rsoxs_schema@0.3.0"
    assert registry.resolve(str(REPO / "storage/schema/v0.3.0/matkg_schema.yaml")).schema_id == "matkg_schema@0.3.0"
    assert registry.resolve("rsoxs_schema@0.3.0") is registry.get("rsoxs_schema@0.3.0")
    fake = tmp_path / "storage" / "schema" / "v0.3.0" / "rsoxs_schema.yaml"
    fake.parent.mkdir(parents=True)
    fake.write_text("id: x\nname: x\nclasses: {}\n", encoding="utf-8")
    assert registry.resolve(str(fake)) is None  # same place name, different bytes


def test_publish_default_excludes_the_pending_set_and_keeps_pre_s4_fingerprints(registry):
    from app.modules.schema.loading import pre_s4_fingerprint

    supported = tj.default_supported_schema_fingerprints(registry)
    for sid in NEW:
        assert closure_fingerprint(registry.get(sid)) not in supported
    for sid in ("matkg_schema", "rsoxs_schema", "bl1101_schema", "xray_schema", "xray_schema_extract"):
        assert closure_fingerprint(registry.get(sid)) in supported
        assert pre_s4_fingerprint(registry.get(sid)) in supported


def test_binding_to_the_new_version_is_exact(registry):
    new = registry.get("rsoxs_schema@0.3.0")
    fp = closure_fingerprint(new)
    assert check_binding(registry, new, fp) == "verified"
    from app.modules.schema.validation import SchemaBindingError

    with pytest.raises(SchemaBindingError, match="bound to registered schema 'rsoxs_schema'"):
        check_binding(registry, new, closure_fingerprint(registry.get("rsoxs_schema")))
    with pytest.raises(SchemaBindingError, match="bound to registered schema 'rsoxs_schema@0.3.0'"):
        check_binding(registry, registry.get("rsoxs_schema"), fp)


def test_snapshot_build_refuses_the_pending_set_unless_asked_twice(registry, tmp_path):
    from app.modules.graph.snapshot import SnapshotError, SnapshotRequest, create_snapshot
    from tests.snapshots.snapshot_fakes import COMMIT, INPUTS

    root = tmp_path / "storage" / "kg"
    root.mkdir(parents=True)
    base = dict(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json", storage_kg=root,
                terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT, registry=registry)
    with pytest.raises(SnapshotError, match="pending owner review"):
        create_snapshot(SnapshotRequest(schema_id="rsoxs_schema@0.3.0", **base))
    assert not any(root.iterdir())
    made = create_snapshot(SnapshotRequest(schema_id="rsoxs_schema@0.3.0", allow_pending_review_schema=True, **base))
    block = made.manifest.schema_block
    assert (block.name, block.path, block.version) == ("rsoxs_schema@0.3.0", "storage/schema/v0.3.0/rsoxs_schema.yaml",
                                                       "rsoxs_v2")
    assert block.import_fingerprint == closure_fingerprint(registry.get("rsoxs_schema@0.3.0"))
    default = create_snapshot(SnapshotRequest(**base))  # no selection: the base set, as before
    assert default.manifest.schema_block.name == "matkg_schema"


# --- additive -------------------------------------------------------------------

@pytest.mark.parametrize("old,new", PAIRS)
def test_every_existing_class_slot_and_enum_is_unchanged(registry, old, new):
    a, b = load_schema_view(registry.get(old)), load_schema_view(registry.get(new))
    diff = preview_diff(registry.get(old), registry.get(new))
    assert diff.classes_removed == [] and diff.slots_removed == [] and diff.is_a_changed == {}
    assert diff.classes_added == NEW_CLASSES and diff.slots_added == NEW_SLOTS
    for name, cls in a.all_classes().items():
        before, after = cls._as_dict, b.get_class(name)._as_dict
        if name == "RSoXSMeasurement":  # only the description says "technique" now (scope item 2)
            before, after = dict(before), dict(after)
            before.pop("description")
            after.pop("description")
        assert before == after, name
        assert [s.name for s in a.class_induced_slots(name)] == [s.name for s in b.class_induced_slots(name)], name
    for name, slot in a.all_slots().items():
        before, after = dict(slot._as_dict), dict(b.get_slot(name)._as_dict)
        # domain_of is derived (which classes list the slot): reusing `unit` on new classes only adds new names
        used_before, used_after = before.pop("domain_of", []) or [], after.pop("domain_of", []) or []
        assert used_after[:len(used_before)] == used_before and set(used_after[len(used_before):]) <= set(NEW_CLASSES)
        assert before == after, name
    assert set(a.all_enums()) <= set(b.all_enums())
    for name in a.all_enums():
        assert a.get_enum(name)._as_dict == b.get_enum(name)._as_dict


def test_rsoxs_measurement_keeps_its_technique_meaning_and_says_so(registry):
    view = load_schema_view(registry.get("rsoxs_schema@0.3.0"))
    assert view.class_ancestors("RSoXSMeasurement") == ["RSoXSMeasurement", "ExperimentalTechnique", "Thing"]
    induced = [s.name for s in view.class_induced_slots("RSoXSMeasurement")]
    assert induced == INVENTORY["rsoxs_measurement_inheritance"]["induced_slots"]
    text = " ".join(view.get_class("RSoXSMeasurement").description.split())
    assert text.startswith("A technique:") and "not a single run" in text
    assert view.get_slot("run_of_technique").range == "ExperimentalTechnique"
    helper = load_helper(registry.get("rsoxs_schema@0.3.0"))
    assert helper.check_relation_validity("MeasurementRun", "run_of_technique", "RSoXSMeasurement")
    assert not helper.check_relation_validity("RSoXSMeasurement", "run_of_technique", "RSoXSMeasurement")


def test_new_classes_and_slots_have_the_intended_shape(registry):
    view = load_schema_view(registry.get("rsoxs_schema@0.3.0"))
    parents = {c: view.get_class(c).is_a for c in NEW_CLASSES}
    assert parents == {"EvidenceQuote": "Thing", "Instrument": "Thing", "MeasurementRun": "Measurement",
                       "PaperSection": "Thing", "QuantityValue": "Thing", "RunCondition": "Condition",
                       "Sample": "Thing"}
    links = {
        "made_of_material": ("Sample", "Material"), "run_of_technique": ("MeasurementRun", "ExperimentalTechnique"),
        "run_measured_sample": ("MeasurementRun", "Sample"), "run_on_instrument": ("MeasurementRun", "Instrument"),
        "run_stated_condition": ("MeasurementRun", "RunCondition"),
        "run_produced_quantity": ("MeasurementRun", "QuantityValue"), "qualifier_sample": ("QuantityValue", "Sample"),
        "supporting_quote": ("Thing", "EvidenceQuote"), "quote_located_in_section": ("EvidenceQuote", "PaperSection"),
    }
    for slot, (domain, rng) in links.items():
        s = view.get_slot(slot)
        assert (s.domain, s.range) == (domain, rng), slot
    sample_slots = {s.name for s in view.class_induced_slots("Sample")}
    assert {"sample_label", "sample_composition", "sample_phase", "sample_processing_state",
            "sample_preparation", "made_of_material"} <= sample_slots
    q = {s.name: s for s in view.class_induced_slots("QuantityValue")}
    assert q["evidence_source_tag"].required is True and q["evidence_source_tag"].range == "EvidenceSourceEnum"
    assert q["uncertainty_kind"].range == "UncertaintyKindEnum" and view.get_slot("uncertainty_value").minimum_value == 0
    assert {"qualifier_sample", "qualifier_processing_state", "qualifier_temperature_k"} <= set(q)
    assert _enum_values(view, "InstrumentKindEnum") == {"beamline", "endstation", "detector"}
    assert _enum_values(view, "QuoteMatchEnum") == {"exact", "normalized"}


def test_schema_enums_match_the_t06_record_enums(registry):
    """F2/F3: one vocabulary in the records and the schema; every value keeps its tag (none is withheld for it)."""
    view = load_schema_view(registry.get("matkg_schema@0.3.0"))
    assert _enum_values(view, "EvidenceSourceEnum") == {e.value for e in EvidenceSource}
    assert _enum_values(view, "UncertaintyKindEnum") == {e.value for e in UncertaintyKind}


def test_set_files_declare_the_lbnl_licence_and_point_at_the_review(registry):
    for sid in NEW:
        entry = registry.get(sid)
        raw = entry.abs_path.read_text(encoding="utf-8")
        assert load_schema_view(entry).schema.license == LICENCE_ID and "MIT" not in raw
        assert REVIEW_DOC in raw


# --- normalization under the new version ---------------------------------------------------

def test_frozen_t22b_normalization_outcomes_hold_under_the_new_version(registry):
    from tests.schema.test_schema_validation import FROZEN_RELATIONS, FROZEN_TAIL, TERMS, _outcome

    for sid, head in (("matkg_schema@0.3.0", "Thing"), ("rsoxs_schema@0.3.0", "RSoXSMeasurement")):
        helper = load_helper(registry.get(sid))
        assert [_outcome(helper, copy.deepcopy(t)) for t in TERMS] == [(head, "RSoXS", FROZEN_RELATIONS)] + FROZEN_TAIL


def test_graph_binding_under_a_set_is_explicit(registry):
    assert registry.for_graph("matkg_rsoxs_v2.json").schema_id == "rsoxs_schema"
    assert registry.for_graph("matkg_rsoxs_v2.json", schema_set="0.3.0").schema_id == "rsoxs_schema@0.3.0"
    assert registry.for_graph("matkg_bl1101_v9.json", schema_set="0.3.0").schema_id == "bl1101_schema"
    assert registry.for_graph("matkg_with_code.json", schema_set="0.3.0") is None
    with pytest.raises(RegistryError, match="not registered"):
        registry.for_graph("matkg_rsoxs_v2.json", schema_set="9.9.9")

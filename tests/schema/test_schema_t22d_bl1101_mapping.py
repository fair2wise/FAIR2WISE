"""T22D (D1.3): bl1101_v3 maps ops ``Sample``/``Detector``/``Endstation`` records onto the 0.3.x ``Sample`` and
``Instrument`` classes through the mapping table the overlay itself declares, and the mapping round-trips exactly
(bl1101 record -> 0.3.x record -> bl1101 record) on the tracked bl1101 graphs."""
from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from app.modules.schema.bl1101_mapping import (
    CLASS_ANNOTATION,
    KIND_ANNOTATION,
    PROVENANCE_KEY,
    MappingRow,
    OpsMappingError,
    map_ops_graph,
    map_ops_node,
    map_ops_nodes,
    mapped_categories,
    mapping_table,
    unmap_ops_graph,
    unmap_ops_nodes,
)
from app.modules.schema.loading import load_schema_view
from app.modules.schema.registry import RegistryError
from app.modules.schema.validation import categories_of, validate_graph

REPO = Path(__file__).resolve().parents[2]
KG = REPO / "storage" / "kg"
EXPECTED_TABLE = {
    "Sample": MappingRow("Sample", "Sample", None),
    "Detector": MappingRow("Detector", "Instrument", "detector"),
    "Endstation": MappingRow("Endstation", "Instrument", "endstation"),
}


@pytest.fixture(scope="module")
def v3(registry):
    return registry.get("bl1101_schema@0.3.1")


@pytest.fixture(scope="module")
def table(v3):
    return mapping_table(v3, allow_pending_review=True)


def _graph(name):
    return json.loads((KG / name).read_bytes().decode("utf-8"))


def test_the_table_is_read_from_the_overlay_and_is_exactly_the_three_reviewed_rows(registry, v3, table):
    assert table == EXPECTED_TABLE and mapped_categories(table) == ["Detector", "Endstation", "Sample"]
    view = load_schema_view(v3)
    for name, row in table.items():
        cls = view.get_class(name)
        assert cls.annotations[CLASS_ANNOTATION].value == row.target_class
        if row.instrument_category:
            assert cls.annotations[KIND_ANNOTATION].value == row.instrument_category
            assert cls.broad_mappings == ["matkg:Instrument"]
        else:
            assert cls.close_mappings == ["matkg:Sample"]
        assert cls.annotations["f2w_mapping_decision"].value == "D1.3"
    # the targets exist in the 0.3.1 base the overlay imports, and the kinds are InstrumentKindEnum values
    base = load_schema_view(registry.get("matkg_schema@0.3.1"))
    assert {r.target_class for r in table.values()} <= set(base.all_classes())
    kinds = set(base.get_enum("InstrumentKindEnum").permissible_values)
    assert {r.instrument_category for r in table.values() if r.instrument_category} <= kinds
    # no other class in the closure carries a mapping row
    annotated = {n for n, c in view.all_classes().items() if CLASS_ANNOTATION in (c.annotations or {})}
    assert annotated == set(EXPECTED_TABLE)


def test_bl1101_v2_has_no_table_and_the_pending_set_needs_explicit_selection(registry, v3):
    with pytest.raises(OpsMappingError, match="needs bl1101_v3"):
        mapping_table(registry.get("bl1101_schema"))
    with pytest.raises(RegistryError, match="pending owner review"):
        mapping_table(v3)
    with pytest.raises(RegistryError, match="pending owner review"):
        map_ops_nodes([], entry=v3)


@pytest.mark.parametrize("name", [f"matkg_bl1101_v{n}.json" for n in range(1, 10)])
def test_round_trip_on_every_tracked_bl1101_graph(registry, v3, name):
    payload = _graph(name)
    before = copy.deepcopy(payload)
    mapped = map_ops_graph(payload, entry=v3, allow_pending_review=True)
    assert payload == before  # input untouched
    nodes = mapped["things"]
    cats_before, cats_after = categories_of(before["things"]), categories_of(nodes)
    moved = sum(cats_before.get(c, 0) for c in EXPECTED_TABLE)
    assert moved > 0
    assert cats_after.get("Sample", 0) == cats_before.get("Sample", 0)  # ops Sample -> literature Sample, same name
    assert cats_after.get("Instrument", 0) == cats_before.get("Detector", 0) + cats_before.get("Endstation", 0)
    assert "Detector" not in cats_after and "Endstation" not in cats_after
    untouched = {c: n for c, n in cats_before.items() if c not in EXPECTED_TABLE}
    assert {c: n for c, n in cats_after.items() if c not in ("Sample", "Instrument")} == untouched
    assert mapped["associations"] == before["associations"] and mapped["metadata"] == before["metadata"]
    assert [n["id"] for n in nodes] == [n["id"] for n in before["things"]]
    # the reverse mapping gives back the ops graph exactly
    assert unmap_ops_graph(mapped) == before
    assert unmap_ops_graph(unmap_ops_graph(mapped)) == before  # idempotent on unmapped records
    assert map_ops_graph(mapped, entry=v3, allow_pending_review=True) == mapped  # and on mapped ones


def test_mapped_records_have_the_literature_shape_and_keep_every_ops_field(v3, table):
    payload = _graph("matkg_bl1101_v9.json")
    originals = {n["id"]: n for n in payload["things"] if n["category"] in EXPECTED_TABLE}
    mapped = {n["id"]: n for n in map_ops_nodes(originals.values(), entry=v3, allow_pending_review=True)}
    assert set(mapped) == set(originals)
    kinds = Counter()
    for node_id, node in mapped.items():
        original = originals[node_id]
        row = table[original["category"]]
        assert node["category"] == row.target_class and node["type"] == f"matkg:{row.target_class}"
        assert node["raw_category"] == original["category"]  # the ops class rides in the existing slot
        if row.target_class == "Instrument":
            assert node["instrument_category"] == row.instrument_category
            kinds[node["instrument_category"]] += 1
            assert "sample_label" not in node
        else:
            assert node["sample_label"] == original["name"] and "instrument_category" not in node
        provenance = node[PROVENANCE_KEY]
        assert (provenance["schema"], provenance["schema_version"], provenance["decision"]) == (
            "bl1101_schema@0.3.1", "bl1101_v3", "D1.3")
        assert provenance["schema_sha256"] == v3.sha256 and provenance["category"] == original["category"]
        assert ("type" in provenance) == ("type" in original)
        for key, value in original.items():
            if key not in ("category", "type", "raw_category"):
                assert node[key] == value, (node_id, key)  # ids, names, descriptions, PVs, sources: verbatim
    assert kinds == {"detector": 4, "endstation": 2}


def test_a_node_of_an_unmapped_class_passes_through_and_an_unreadable_provenance_is_refused(v3, table):
    motor = {"id": "beamline:Motor-sample-x", "name": "sample_x", "category": "Motor", "type": "beamline:Motor"}
    assert map_ops_node(motor, table, v3) == motor
    assert unmap_ops_nodes([motor]) == [motor]
    with pytest.raises(OpsMappingError, match="unreadable"):
        unmap_ops_nodes([{"id": "x", "category": "Instrument", PROVENANCE_KEY: "no"}])


def test_mapped_ops_nodes_validate_inside_the_literature_031_closure(registry, v3):
    """The mapped Sample/Instrument records are 0.3.x records: inside the rsoxs_schema@0.3.1 (and matkg 0.3.1)
    closure, with no node outside it. The same records are outside the base closure by exactly their new names."""
    payload = _graph("matkg_bl1101_v9.json")
    subset = {"things": [n for n in payload["things"] if n["category"] in EXPECTED_TABLE], "associations": []}
    mapped = map_ops_graph(subset, entry=v3, allow_pending_review=True)
    for sid in ("rsoxs_schema@0.3.1", "matkg_schema@0.3.1"):
        report = validate_graph(copy.deepcopy(mapped), registry.get(sid), registry=registry)
        assert report.status == "pass"
        assert report.categories["legacy_warning"]["nodes_with_category_outside_closure"] == 0, sid
    base = validate_graph(copy.deepcopy(mapped), registry.get("matkg_schema"), registry=registry)
    assert set(base.categories["legacy_warning"]["categories_outside_closure"]) == {"Sample", "Instrument"}
    # and the unmapped ops records are what the bl1101 overlay (v2 or v3) knows, not the literature base
    ops = validate_graph(copy.deepcopy(subset), registry.get("bl1101_schema@0.3.1"), registry=registry)
    assert ops.categories["legacy_warning"]["nodes_with_category_outside_closure"] == 0

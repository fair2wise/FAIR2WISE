"""T22D: the JSON graph backend round-trips QuantityValue records that carry the 0.3.1 normalized-value slots."""
import json

import pytest
from graph_contexts import make_ctx

from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.schema.experiment_mapping import NORMALIZED_SLOTS, map_validated_records
from app.modules.schema.registry import SchemaRegistry
from tests.schema.t22c_fixtures import SOURCE_PAPER, WORK_ID, make_chunk, make_document


@pytest.fixture(scope="module")
def graph():
    from app.modules.extraction.record_validation import validate_against_document

    registry = SchemaRegistry.discover()
    document = make_document()
    validation = validate_against_document(make_chunk(), document, work_id=WORK_ID, pub_path="publications/x",
                                           strategy="t22d-fixture")
    return map_validated_records(validation, document, work_id=WORK_ID, source_paper=SOURCE_PAPER,
                                 entry=registry.get("rsoxs_schema@0.3.1"), allow_pending_review=True)


def test_normalized_slots_round_trip_through_the_json_backend(graph, tmp_path):
    values = [n for n in graph["things"] if n["category"] == "QuantityValue"]
    assert values and all(set(NORMALIZED_SLOTS) <= set(n) for n in values)
    path = tmp_path / "matkg_rsoxs_v7.json"
    path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    backend = JsonGraphBackend([path])
    ctx = make_ctx()
    [gid] = backend.graph_ids()
    for node in values:
        rec = backend.get_entity(ctx, gid, node["id"])
        assert rec["data"] == node
        assert all(rec["data"][k] == node[k] for k in NORMALIZED_SLOTS)
        assert (rec["data"]["numeric_value"], rec["data"]["unit"]) == (node["numeric_value"], node["unit"])
    records = {r["id"]: r for r in backend.iter_entities(ctx, gid)}
    rebuilt = {"things": [records[n]["data"] for n in records],
               "associations": [e["data"] for e in backend.iter_edges(ctx, gid)]}
    assert rebuilt == graph
    assert path.read_bytes() == before

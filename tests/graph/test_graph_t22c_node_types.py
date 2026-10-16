"""T22C: the JSON graph backend round-trips the schema set 0.3.0 node types unchanged."""
import json

import pytest
from graph_contexts import make_ctx

from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.schema.registry import SchemaRegistry
from tests.schema.t22c_fixtures import mapped_graph

NEW_TYPES = {"Sample", "MeasurementRun", "Instrument", "RunCondition", "QuantityValue", "EvidenceQuote",
             "PaperSection"}


@pytest.fixture(scope="module")
def graph():
    return mapped_graph(SchemaRegistry.discover())[2]


def _write(path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def test_every_new_node_type_round_trips_through_the_json_backend(graph, tmp_path):
    assert NEW_TYPES <= {n["category"] for n in graph["things"]}
    path = _write(tmp_path / "matkg_rsoxs_v7.json", graph)
    before = path.read_bytes()
    backend = JsonGraphBackend([path])
    ctx = make_ctx()
    [gid] = backend.graph_ids()
    records = {r["id"]: r for r in backend.iter_entities(ctx, gid)}
    assert list(records) == [n["id"] for n in graph["things"]]
    for node in graph["things"]:
        rec = backend.get_entity(ctx, gid, node["id"])
        assert rec["data"] == node and rec["category"] == node["category"] and rec["name"] == node["name"]
        assert rec["citations"] == node["source_papers"] and rec["graph_id"] == gid
    edges = list(backend.iter_edges(ctx, gid))
    assert [e["data"] for e in edges] == graph["associations"]
    rebuilt = {"things": [records[n]["data"] for n in records], "associations": [e["data"] for e in edges]}
    assert rebuilt == graph
    assert _write(tmp_path / "again.json", rebuilt).read_bytes() == before
    assert path.read_bytes() == before  # read-only backend


def test_a_run_reaches_its_sample_technique_instrument_values_and_evidence(graph, tmp_path):
    backend = JsonGraphBackend([_write(tmp_path / "matkg_rsoxs_v7.json", graph)])
    ctx = make_ctx()
    [gid] = backend.graph_ids()
    run = next(n for n in graph["things"] if n["category"] == "MeasurementRun" and n["name"].startswith("RSoXS"))
    hood = backend.neighborhood(ctx, gid, run["id"], hops=3, max_nodes=200, direction="out")
    reached = {n["category"] for n in hood["nodes"]}
    assert {"Sample", "Material", "RSoXSMeasurement", "Instrument", "RunCondition", "QuantityValue",
            "EvidenceQuote", "PaperSection"} <= reached
    assert hood["missing"] == [] and not hood["truncated"]
    preds = {e["predicate"] for e in hood["edges"]}
    assert {"rel:run_of_technique", "rel:run_measured_sample", "rel:run_on_instrument", "rel:run_stated_condition",
            "rel:run_produced_quantity", "rel:supporting_quote", "rel:quote_located_in_section",
            "rel:made_of_material", "rel:qualifier_sample"} <= preds

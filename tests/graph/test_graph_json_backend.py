"""T08A: JSON GraphPort backend - parity with json2kg/multi_kg/KnowledgeGraph, scope, round trips."""
import json
import shutil

import pytest
from conftest import make_als_ctx, make_ctx
from graph_samples import JSON_KG_EXPECTED, nodes_payload, things_payload, write

from app.modules import kg_rag_api
from app.modules.contracts.errors import CapabilityError, ContractError, ErrorCode
from app.modules.contracts.ports import GraphPort
from app.modules.f2w_agent import multi_kg
from app.modules.graph.backends.json import CAPABILITIES, JsonGraphBackend
from app.modules.graph.snapshots import graph_id_for_snapshot, load_snapshot, serialize_like_json2kg


@pytest.fixture
def two_graphs(tmp_path):
    a = write(tmp_path / "kg" / "matkg_rsoxs_v4.json", things_payload())
    b = write(tmp_path / "kg" / "matkg_rsoxs_v3.json", nodes_payload())
    return JsonGraphBackend([a, b]), a, b


def test_protocol_and_capabilities(two_graphs):
    backend = two_graphs[0]
    assert isinstance(backend, GraphPort)
    caps = backend.capabilities()
    assert caps == CAPABILITIES and caps.backend == "json"
    assert caps.complete_listing and caps.bounded_traversal
    assert not any((caps.mutable_entity_properties, caps.batch_writes, caps.compare_and_swap,
                    caps.owned_deletion))
    with pytest.raises(CapabilityError) as err:
        backend.apply_plan(make_ctx(), None, None)
    assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_graph_ids_match_multi_kg(tmp_path):
    for name in ("matkg_rsoxs_v1.json", "matkg_rsoxs_v7.json", "matkg_bl1101_v9.json",
                 "matkg_xray_papers_cborg_chat.json", "matkg_custom_run.json", "plain.json"):
        assert graph_id_for_snapshot(tmp_path / name) == multi_kg.graph_id_for_path(name)
    # Versioned snapshot dirs map through their flat compatibility name, never to "graph".
    assert graph_id_for_snapshot(tmp_path / "rsoxs" / "v4" / "graph.json") == "rsoxs_v4"
    assert graph_id_for_snapshot(tmp_path / "bl1101" / "v10" / "graph.json") == "bl1101"
    two = JsonGraphBackend([tmp_path / "rsoxs" / "v4" / "graph.json", tmp_path / "matkg_rsoxs_v5.json"])
    assert two.graph_ids() == ["rsoxs_v4", "rsoxs_v5"]


def test_graph_id_collision_is_refused(tmp_path):
    with pytest.raises(ContractError) as err:
        JsonGraphBackend([tmp_path / "a" / "matkg_bl1101_v1.json", tmp_path / "b" / "matkg_bl1101_v2.json"])
    assert err.value.code is ErrorCode.INVALID_INPUT


def test_records_are_graph_scoped_and_keep_old_fields(two_graphs):
    backend, _a, _b = two_graphs
    ctx = make_ctx()
    p3ht = backend.get_entity(ctx, "rsoxs_v4", "matkg:P3HT")
    assert p3ht["graph_id"] == "rsoxs_v4" and p3ht["id"] == "matkg:P3HT" and p3ht["name"] == "P3HT"
    assert p3ht["citations"] == ["paper_a.pdf"]
    assert p3ht["data"]["publications"] == [{"source_paper": "paper_a.pdf", "doi": "10.1/a"}]
    assert p3ht["data"]["confidence"] == 0.7
    v3 = backend.get_entity(ctx, "rsoxs_v3", "rsoxs:PSbPMMA")
    # The node's own legacy graph_id field survives; the record's graph_id is the scope.
    assert v3["graph_id"] == "rsoxs_v3" and v3["data"]["graph_id"] == "legacy-gid"
    assert v3["name"] == "PS-b-PMMA" and v3["category"] == "Material" and v3["citations"] == ["p1"]
    # Ids never resolve across graphs, and labels are never joined.
    assert backend.get_entity(ctx, "rsoxs_v3", "matkg:P3HT") is None
    assert [h["id"] for h in backend.find_entities(ctx, "rsoxs_v3", "PS-b", limit=10)[0]] == ["rsoxs:PSbPMMA"]
    assert backend.find_entities(ctx, "rsoxs_v3", "P3HT", limit=10) == ([], True)
    assert [h["id"] for h in backend.find_entities(ctx, "rsoxs_v4", "RSoXS", limit=10)[0]] == ["matkg:RSoXS"]


def test_round_trip_preserves_every_node_edge_and_bytes(tmp_path):
    for expected in sorted(JSON_KG_EXPECTED.glob("*.graph.json")):
        path = tmp_path / ("matkg_" + expected.name.replace(".graph.json", ".json"))
        shutil.copyfile(expected, path)
        backend = JsonGraphBackend([path])
        gid = backend.graph_ids()[0]
        original = json.loads(expected.read_bytes())
        nodes = [r["data"] for r in backend.iter_entities(make_ctx(), gid)]
        edges = [r["data"] for r in backend.iter_edges(make_ctx(), gid)]
        rebuilt = {"things": nodes, "associations": edges}
        if "metadata" in original:
            rebuilt["metadata"] = original["metadata"]
        assert rebuilt == original, expected.name
        assert serialize_like_json2kg(rebuilt) == expected.read_bytes(), expected.name


def test_parity_with_knowledge_graph_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    path = tmp_path / "matkg_core.json"
    shutil.copyfile(JSON_KG_EXPECTED / "core_dict.graph.json", path)
    write(tmp_path / "matkg_sample.json", things_payload())
    for p in (path, tmp_path / "matkg_sample.json"):
        kg = kg_rag_api.KnowledgeGraph(str(p), graph_source="json")
        backend = JsonGraphBackend([p])
        gid = backend.graph_ids()[0]
        assert [r["id"] for r in backend.iter_entities(make_ctx(), gid)] == list(kg.nodes)
        for nid in kg.nodes:
            for hops in (1, 2, 3):
                seeds = [kg_rag_api.NodeScore(nid, 1.0)]
                reach = {n.id for n in kg.weighted_bfs(seeds, hops=hops)}
                hood = backend.neighborhood(make_ctx(), gid, nid, hops=hops, max_nodes=10_000, direction="out")
                assert {n["id"] for n in hood["nodes"]} | set(hood["missing"]) == reach
                assert not hood["truncated"]


def test_nodes_edges_family_and_duplicate_triples(two_graphs):
    backend = two_graphs[0]
    edges = list(backend.iter_edges(make_ctx(), "rsoxs_v3"))
    assert len(edges) == 2 and edges[0]["id"] != edges[1]["id"] and edges[1]["id"].endswith("#1")
    assert [e["data"]["type"] for e in edges] == ["t", "t2"]
    snap = load_snapshot(two_graphs[2])
    assert snap.family == "nodes_edges" and snap.raw["prov_metadata"] == {"generated": "synthetic"}


def test_neighborhood_bounds_truncation_and_missing(two_graphs):
    backend = two_graphs[0]
    ctx = make_ctx()
    full = backend.neighborhood(ctx, "rsoxs_v4", "matkg:P3HT", hops=3, max_nodes=100)
    assert [n["id"] for n in full["nodes"]] == ["matkg:P3HT", "matkg:RSoXS", "matkg:OPV"]
    assert full["missing"] == ["matkg:Ghost"] and not full["truncated"]
    assert all(e["graph_id"] == "rsoxs_v4" for e in full["edges"]) and len(full["edges"]) == 3
    capped = backend.neighborhood(ctx, "rsoxs_v4", "matkg:P3HT", hops=3, max_nodes=2)
    assert len(capped["nodes"]) == 2 and capped["truncated"]
    incoming = backend.neighborhood(ctx, "rsoxs_v4", "matkg:OPV", hops=1, max_nodes=10, direction="in")
    assert [n["id"] for n in incoming["nodes"]] == ["matkg:OPV", "matkg:RSoXS"]
    zero = backend.neighborhood(ctx, "rsoxs_v4", "matkg:P3HT", hops=0, max_nodes=10)
    assert [n["id"] for n in zero["nodes"]] == ["matkg:P3HT"] and zero["edges"] == []
    with pytest.raises(ContractError) as err:
        backend.neighborhood(ctx, "rsoxs_v4", "matkg:Nope", hops=1, max_nodes=10)
    assert err.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(ContractError):
        backend.neighborhood(ctx, "rsoxs_v4", "matkg:P3HT", hops=1, max_nodes=0)


def test_find_entities_flags_partial_results(two_graphs):
    backend = two_graphs[0]
    hits, complete = backend.find_entities(make_ctx(), "rsoxs_v4", "", limit=2)
    assert len(hits) == 2 and complete is False
    hits, complete = backend.find_entities(make_ctx(), "rsoxs_v4", "", limit=4)
    assert len(hits) == 4 and complete is True


def test_scope_blocks_other_corpora(two_graphs):
    backend = two_graphs[0]
    scoped = make_als_ctx(scope={"rsoxs_v3"})
    assert backend.get_entity(scoped, "rsoxs_v3", "rsoxs:RSoXS")["graph_id"] == "rsoxs_v3"
    for call in (lambda: backend.get_entity(scoped, "rsoxs_v4", "matkg:P3HT"),
                 lambda: backend.find_entities(scoped, "rsoxs_v4", "", limit=5),
                 lambda: backend.neighborhood(scoped, "rsoxs_v4", "matkg:P3HT", hops=1, max_nodes=5)):
        with pytest.raises(ContractError) as err:
            call()
        assert err.value.code is ErrorCode.DENIED
    with pytest.raises(ContractError) as err:
        backend.get_entity(make_ctx(), "bl1101", "x")
    assert err.value.code is ErrorCode.NOT_FOUND


def test_unavailable_graph_is_not_empty(tmp_path):
    backend = JsonGraphBackend([tmp_path / "matkg_rsoxs_v9.json"])
    for call in (lambda: backend.get_entity(make_ctx(), "rsoxs_v9", "x"),
                 lambda: backend.find_entities(make_ctx(), "rsoxs_v9", "x", limit=5)):
        with pytest.raises(ContractError) as err:
            call()
        assert err.value.code is ErrorCode.UNAVAILABLE and err.value.retryable
    (tmp_path / "matkg_rsoxs_v9.json").write_text("<html>", encoding="utf-8")
    with pytest.raises(ContractError) as err:
        backend.get_entity(make_ctx(), "rsoxs_v9", "x")
    assert err.value.code is ErrorCode.INVALID_INPUT


def test_empty_placeholder_is_a_real_empty_graph(tmp_path):
    path = write(tmp_path / "matkg_empty.json", {"things": [], "associations": []})
    backend = JsonGraphBackend([path])
    assert backend.find_entities(make_ctx(), "empty", "", limit=5) == ([], True)


def test_manifest_hash_is_checked_and_cache_reloads_explicitly(tmp_path):
    graph = write(tmp_path / "rsoxs" / "v4" / "graph.json", things_payload())
    snap = load_snapshot(graph)
    (graph.parent / "manifest.json").write_text(json.dumps({"graph": {"sha256": snap.sha256}}), encoding="utf-8")
    backend = JsonGraphBackend([graph])
    assert backend.get_entity(make_ctx(), "rsoxs_v4", "matkg:P3HT") is not None
    graph.write_text(graph.read_text(encoding="utf-8").replace("P3HT", "P3HX"), encoding="utf-8")
    # No silent re-read: the cached snapshot answers until reload().
    assert backend.get_entity(make_ctx(), "rsoxs_v4", "matkg:P3HT") is not None
    backend.reload()
    with pytest.raises(ContractError) as err:
        backend.get_entity(make_ctx(), "rsoxs_v4", "matkg:P3HT")
    assert err.value.code is ErrorCode.CONFLICT

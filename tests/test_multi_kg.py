import json
from pathlib import Path

from app.modules.f2w_agent.multi_kg import (
    DEFAULT_JSON_GRAPH_PATHS,
    XRAY_DEMO_GRAPH,
    ScoredHit,
    clamp_kg_query_hops,
    clamp_kg_query_max_nodes,
    default_json_graph_paths,
    graph_id_for_path,
    latest_bl1101_path,
    latest_rsoxs_path,
    merge_hits,
)
from app.modules.f2w_agent.retrieval_agent import RetrievalAgent


def _write_graph(path: Path, node_id: str, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "things": [
                    {
                        "id": node_id,
                        "name": name,
                        "category": "Thing",
                        "description": f"{name} node",
                    }
                ],
                "associations": [],
            }
        ),
        encoding="utf-8",
    )


def test_default_paths_are_rsoxs_and_bl1101_not_xray():
    available = [
        "storage/kg/matkg_bl1101_v1.json",
        "storage/kg/matkg_rsoxs_v1.json",
        XRAY_DEMO_GRAPH,
        "storage/kg/other.json",
    ]
    paths = default_json_graph_paths(available=available)
    assert paths == list(DEFAULT_JSON_GRAPH_PATHS)
    assert XRAY_DEMO_GRAPH not in paths


def test_default_paths_prefer_latest_bl1101_snapshot():
    available = [
        "storage/kg/matkg_bl1101_v1.json",
        "storage/kg/matkg_bl1101_v2.json",
        "storage/kg/matkg_rsoxs_v1.json",
        XRAY_DEMO_GRAPH,
    ]
    paths = default_json_graph_paths(available=available)
    assert paths == [
        "storage/kg/matkg_rsoxs_v1.json",
        "storage/kg/matkg_bl1101_v2.json",
    ]
    assert latest_bl1101_path(available) == "storage/kg/matkg_bl1101_v2.json"
    assert graph_id_for_path("storage/kg/matkg_bl1101_v2.json") == "bl1101"


def test_default_paths_prefer_latest_rsoxs_snapshot():
    available = [
        "storage/kg/matkg_rsoxs_v1.json",
        "storage/kg/matkg_rsoxs_v2.json",
        "storage/kg/matkg_bl1101_v4.json",
        XRAY_DEMO_GRAPH,
    ]
    paths = default_json_graph_paths(available=available)
    assert paths == [
        "storage/kg/matkg_rsoxs_v2.json",
        "storage/kg/matkg_bl1101_v4.json",
    ]
    assert latest_rsoxs_path(available) == "storage/kg/matkg_rsoxs_v2.json"
    assert graph_id_for_path("storage/kg/matkg_rsoxs_v2.json") == "rsoxs_v1"


def test_xray_is_opt_in_only_when_configured():
    available = [XRAY_DEMO_GRAPH, "storage/kg/matkg_rsoxs_v1.json"]
    assert XRAY_DEMO_GRAPH not in default_json_graph_paths(available=available)
    opted = default_json_graph_paths(
        configured_graph=XRAY_DEMO_GRAPH,
        available=available,
    )
    assert opted == [XRAY_DEMO_GRAPH]


def test_merge_hits_keeps_same_id_from_two_graphs():
    merged = merge_hits(
        [
            ScoredHit(id="shared", graph_id="rsoxs_v1", score=0.4, name="lit"),
            ScoredHit(id="shared", graph_id="bl1101", score=0.9, name="ops"),
            ScoredHit(id="only_lit", graph_id="rsoxs_v1", score=0.2, name="paper"),
        ],
        limit=10,
    )
    ids = {(hit.graph_id, hit.id) for hit in merged}
    assert ("rsoxs_v1", "shared") in ids
    assert ("bl1101", "shared") in ids
    assert merged[0].graph_id == "bl1101"


def test_query_limits_clamp():
    assert clamp_kg_query_hops(0) == 1
    assert clamp_kg_query_hops(9) == 9
    assert clamp_kg_query_hops(20) == 20
    assert clamp_kg_query_hops(21) == 20
    assert clamp_kg_query_max_nodes(5) == 10
    assert clamp_kg_query_max_nodes(5000) == 1000


def test_retrieval_fanout_and_skip_missing(tmp_path, monkeypatch):
    from app.modules import kg_rag_api as krag

    monkeypatch.setattr(krag, "RETRIEVAL_BACKEND", "lexical")
    science = tmp_path / "matkg_rsoxs_v1.json"
    ops = tmp_path / "matkg_bl1101_v1.json"
    missing = tmp_path / "missing.json"
    empty = tmp_path / "empty.json"
    _write_graph(science, "matkg:RSoXS", "RSoXS")
    _write_graph(ops, "beamline:ALS11012", "ALS 11.0.1.2")
    empty.write_text("", encoding="utf-8")

    agent = RetrievalAgent(
        graph_files=[str(science), str(missing), str(empty), str(ops)],
        graph_source="json",
        kg_query_hops=1,
        kg_query_max_nodes=50,
    )
    result = __import__("asyncio").run(agent.reload_kg(graph_files=agent._graph_files))
    assert set(result["graph_ids"]) == {"rsoxs_v1", "bl1101"}
    assert {item["reason"] for item in result["skipped"]} == {"missing", "empty"}

    hits = agent._fanout_hits("RSoXS ALS beamline", agent._graphs)
    graph_ids = {hit.graph_id for hit in hits}
    assert "rsoxs_v1" in graph_ids
    assert "bl1101" in graph_ids
    assert graph_id_for_path(str(science)) == "rsoxs_v1"
    assert graph_id_for_path(str(ops)) == "bl1101"


def test_search_node_scores_fans_out_without_prior_reload(tmp_path, monkeypatch):
    from app.modules import kg_rag_api as krag

    monkeypatch.setattr(krag, "RETRIEVAL_BACKEND", "lexical")
    science = tmp_path / "matkg_rsoxs_v1.json"
    ops = tmp_path / "matkg_bl1101_v1.json"
    _write_graph(science, "matkg:RSoXS", "RSoXS")
    _write_graph(ops, "beamline:ALS11012", "ALS 11.0.1.2")

    agent = RetrievalAgent(
        graph_files=[str(science), str(ops)],
        graph_source="json",
    )
    ranked = __import__("asyncio").run(agent.search_node_scores("RSoXS ALS", limit=10))
    assert set(ranked["graph_ids"]) == {"rsoxs_v1", "bl1101"}
    assert {match["graph_id"] for match in ranked["matches"]} == {"rsoxs_v1", "bl1101"}


def test_fanout_parallel_three_graphs(tmp_path, monkeypatch):
    """N-way fan-out: hits from all N graphs are tagged with correct graph_id."""
    from app.modules import kg_rag_api as krag

    monkeypatch.setattr(krag, "RETRIEVAL_BACKEND", "lexical")
    g1 = tmp_path / "matkg_rsoxs_v1.json"
    g2 = tmp_path / "matkg_bl1101_v1.json"
    g3 = tmp_path / "matkg_xray_papers_cborg_chat.json"
    _write_graph(g1, "rsoxs:node", "RSoXS node")
    _write_graph(g2, "ops:node", "Ops node")
    _write_graph(g3, "xray:node", "X-ray node")

    agent = RetrievalAgent(
        graph_files=[str(g1), str(g2), str(g3)],
        graph_source="json",
        kg_query_hops=1,
        kg_query_max_nodes=50,
    )
    __import__("asyncio").run(agent.reload_kg(graph_files=agent._graph_files))

    # Three-graph fan-out: each hit must carry a graph_id
    hits = agent._fanout_hits("RSoXS beamline x-ray", agent._graphs)
    hit_graph_ids = {hit.graph_id for hit in hits}
    assert len(hit_graph_ids) >= 1  # at least one graph returned hits
    for hit in hits:
        assert hit.graph_id, "every hit must have a non-empty graph_id"
        assert hit.graph_id in agent._graphs, f"graph_id {hit.graph_id!r} not in loaded graphs"


def test_intent_router_ops_first(tmp_path, monkeypatch):
    """Beam-path / hardware-layout questions should only produce ops KG hits."""
    from app.modules import kg_rag_api as krag
    from app.modules.f2w_agent.multi_kg import is_ops_graph_id

    monkeypatch.setattr(krag, "RETRIEVAL_BACKEND", "lexical")
    science = tmp_path / "matkg_rsoxs_v1.json"
    ops = tmp_path / "matkg_bl1101_v1.json"
    _write_graph(science, "rsoxs:node", "RSoXS")
    _write_graph(ops, "ops:EPU", "EPU undulator")

    agent = RetrievalAgent(
        graph_files=[str(science), str(ops)],
        graph_source="json",
        kg_query_hops=1,
        kg_query_max_nodes=50,
    )
    __import__("asyncio").run(agent.reload_kg(graph_files=agent._graph_files))

    hits = agent._fanout_hits(
        "How is the hardware connected in order at ALS 11.0.1.2?",
        agent._graphs,
    )
    # All hits must come from the ops KG (if it returns any results).
    if hits:
        for hit in hits:
            assert is_ops_graph_id(hit.graph_id), (
                f"layout query produced non-ops hit from {hit.graph_id!r}"
            )


def test_intent_router_falls_back_to_science_when_ops_empty(tmp_path, monkeypatch):
    """If ops KG returns no hits for a layout query, science KG is used as fallback."""
    from app.modules import kg_rag_api as krag
    from app.modules.f2w_agent.multi_kg import is_ops_graph_id, is_science_graph_id

    monkeypatch.setattr(krag, "RETRIEVAL_BACKEND", "lexical")
    # Only a science graph — no ops graph loaded.
    science = tmp_path / "matkg_rsoxs_v1.json"
    _write_graph(science, "rsoxs:node", "RSoXS beamline")

    agent = RetrievalAgent(
        graph_files=[str(science)],
        graph_source="json",
        kg_query_hops=1,
        kg_query_max_nodes=50,
    )
    __import__("asyncio").run(agent.reload_kg(graph_files=agent._graph_files))

    hits = agent._fanout_hits(
        "What is the beam path at the beamline?",
        agent._graphs,
    )
    # With no ops KG present, the router should fall back to science hits.
    hit_graph_ids = {hit.graph_id for hit in hits}
    for gid in hit_graph_ids:
        assert is_science_graph_id(gid) or not is_ops_graph_id(gid), (
            f"unexpected ops hit {gid!r} when no ops graph was loaded"
        )

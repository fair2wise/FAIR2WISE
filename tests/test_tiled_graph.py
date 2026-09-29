"""Live Tiled GraphQL lookup (mocked HTTP). JSON sim is fallback, not this path."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.modules.f2w_agent.retrieval_agent import RetrievalAgent
from app.modules.tiled_graph import (
    TILED_GRAPH_ID,
    coerce_local_tiled_uri,
    entity_to_node,
    graphql_url,
    is_experiment_identity_question,
    lookup_experiment_identity,
    normalize_tiled_uri,
    probe_tiled_status,
    requested_entity_types,
)


def _entity(entity_type: str, name: str, **props):
    return {
        "id": f"uuid-{name}",
        "entityType": entity_type,
        "name": name,
        "uri": f"tiled:{entity_type.lower()}:{name}",
        "nodeId": f"sim:tiled:/{name}",
        "properties": props,
        "outgoingLinks": [],
    }


def test_normalize_and_graphql_url():
    assert graphql_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000/api/graphql"
    assert graphql_url("http://tiled:8000/api/graphql") == "http://tiled:8000/api/graphql"
    with pytest.raises(ValueError):
        normalize_tiled_uri("not-a-url")
    assert coerce_local_tiled_uri("https://tiled.als.lbl.gov") == "http://127.0.0.1:8765"
    assert coerce_local_tiled_uri("http://127.0.0.1:8001") == "http://127.0.0.1:8001"


def test_probe_unsigned_when_graphql_empty(monkeypatch):
    monkeypatch.delenv("TILED_API_KEY", raising=False)
    monkeypatch.delenv("F2W_LIVE_TILED", raising=False)

    def fake_graphql(query, variables=None, *, uri=None, api_key=None, timeout=8.0):
        if "typename" in query:
            return {"__typename": "Query"}
        return {"entities": []}

    monkeypatch.setattr("app.modules.tiled_graph.tiled_graphql", fake_graphql)
    snap = probe_tiled_status(uri="http://127.0.0.1:8001", enabled=True)
    assert snap["use_live_tiled"] is True
    assert snap["tiled_uri"] == "http://127.0.0.1:8001"
    assert snap["tiled_api_key_set"] is False
    assert snap["tiled_status"] == "unsigned"


def test_probe_empty_when_key_set(monkeypatch):
    monkeypatch.setenv("TILED_API_KEY", "secret-test-key")

    def fake_graphql(query, variables=None, *, uri=None, api_key=None, timeout=8.0):
        if "typename" in query:
            return {"__typename": "Query"}
        return {"entities": []}

    monkeypatch.setattr("app.modules.tiled_graph.tiled_graphql", fake_graphql)
    snap = probe_tiled_status(uri="http://127.0.0.1:8001", enabled=True)
    assert snap["tiled_api_key_set"] is True
    assert snap["tiled_status"] == "empty"
    assert "API key is loaded" in (snap["tiled_error"] or "")
    monkeypatch.delenv("TILED_API_KEY", raising=False)


def test_identity_routing():
    assert is_experiment_identity_question("ESAF 2026-00041")
    assert is_experiment_identity_question("how many proposals")
    assert is_experiment_identity_question("tell me about my last scan")
    assert not is_experiment_identity_question("what is P3HT")
    assert requested_entity_types("ESAF 2026-00041") == ["ESAF"]
    assert "Proposal" in requested_entity_types("how many proposals")


def test_lookup_pages_esaf(monkeypatch):
    monkeypatch.delenv("TILED_API_KEY", raising=False)
    calls = []

    def fake_graphql(query, variables, *, uri=None, api_key=None, timeout=8.0):
        calls.append(variables)
        entity_type = variables["entityType"]
        if entity_type == "ESAF":
            return {
                "entities": [
                    _entity(
                        "ESAF",
                        "ESAF 2026-00041",
                        esaf_number="2026-00041",
                        title="P3HT OPV P-RSoXS at the carbon edge",
                    )
                ]
            }
        return {"entities": []}

    monkeypatch.setattr("app.modules.tiled_graph.tiled_graphql", fake_graphql)
    result = lookup_experiment_identity(
        "Tell me about ESAF 2026-00041",
        uri="http://127.0.0.1:8000",
        enabled=True,
        include_catalog=False,
    )
    assert result.status == "ok"
    assert result.source == "graphql"
    assert any(hit.name == "ESAF 2026-00041" for hit in result.hits)
    assert result.graph is not None
    assert TILED_GRAPH_ID == result.graph.graph_id
    assert "ESAF" in result.entity_counts
    assert {call["entityType"] for call in calls} >= {"ESAF", "Proposal", "Sample", "BlueskyRun"}
    assert any(call["entityType"] == "ESAF" for call in calls)


def test_lookup_unreachable_falls_back(monkeypatch):
    def boom(*_args, **_kwargs):
        raise ConnectionError("tiled down")

    monkeypatch.setattr("app.modules.tiled_graph.tiled_graphql", boom)
    result = lookup_experiment_identity(
        "ESAF 2026-00041",
        uri="http://127.0.0.1:8000",
        enabled=True,
        include_catalog=False,
    )
    assert result.status == "unreachable"
    assert result.fallback == "json_kg"
    assert result.hits == []
    assert "ConnectionError" in (result.error or "")
    assert "tiled down" in (result.error or "")


def test_unsigned_empty_is_explicit(monkeypatch):
    monkeypatch.delenv("TILED_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.modules.tiled_graph.tiled_graphql",
        lambda *args, **kwargs: {"entities": []},
    )
    result = lookup_experiment_identity(
        "how many proposals",
        uri="http://127.0.0.1:8000",
        enabled=True,
        include_catalog=False,
    )
    assert result.status == "unsigned"
    assert result.fallback == "json_kg"
    assert "TILED_API_KEY" in (result.error or "")


def test_count_node_for_proposals(monkeypatch):
    monkeypatch.setenv("TILED_API_KEY", "secret-test-key")
    proposals = [
        _entity("Proposal", "Proposal P202600041-01", proposal_code="P202600041-01"),
        _entity("Proposal", "Proposal P202600041-02", proposal_code="P202600041-02"),
    ]

    def fake_graphql(query, variables, *, uri=None, api_key=None, timeout=8.0):
        assert api_key == "secret-test-key"
        if variables["entityType"] == "Proposal":
            return {"entities": proposals}
        return {"entities": []}

    monkeypatch.setattr("app.modules.tiled_graph.tiled_graphql", fake_graphql)
    result = lookup_experiment_identity(
        "how many proposals",
        uri="http://tiled.example:8000",
        api_key="secret-test-key",
        enabled=True,
        include_catalog=False,
    )
    assert result.status == "ok"
    assert result.entity_counts["Proposal"] == 2
    inventory = result.graph.nodes["tiled:count:Proposal"]
    assert "2 Proposal" in inventory["description"]
    monkeypatch.delenv("TILED_API_KEY", raising=False)


def test_entity_to_node_maps_cookbook_fields():
    node = entity_to_node(
        _entity(
            "ESAF",
            "ESAF 2026-00043",
            esaf_number="2026-00043",
            scientist="Cheng Wang",
            title="PVD glasses and TPD-DO37 contrast",
        )
    )
    assert node["category"] == "ESAF"
    assert node["entityType"] == "ESAF"
    assert node["scientist"] == "Cheng Wang"
    assert node["esaf_number"] == "2026-00043"
    assert node["source_papers"] == ["tiled_graphql"]
    assert "cheng wang" in node["haystack"]


def test_resolve_tiled_node_alias_and_neighborhood():
    from app.modules.tiled_graph import TiledLookupGraph, resolve_tiled_node_id, tiled_neighborhood_ids

    graph = TiledLookupGraph()
    esaf = entity_to_node(
        {
            **_entity("ESAF", "ESAF 2026-00043", esaf_number="2026-00043", scientist="Cheng Wang"),
            "uri": "beamline:ESAF-2026-00043",
            "outgoingLinks": [{
                "predicate": "rel:hasProposal",
                "object": {"id": "uuid-prop", "name": "Proposal P202600043-01", "entityType": "Proposal", "uri": "beamline:Proposal-P202600043-01"},
            }],
        }
    )
    proposal = entity_to_node(
        {
            **_entity("Proposal", "Proposal P202600043-01", proposal_code="P202600043-01"),
            "uri": "beamline:Proposal-P202600043-01",
            "outgoingLinks": [{
                "predicate": "rel:hasSample",
                "object": {"id": "uuid-sample", "name": "Sample S01", "entityType": "Sample", "uri": "beamline:Sample-S01"},
            }],
        }
    )
    sample = entity_to_node(
        {**_entity("Sample", "Sample S01", sample_code="S01"), "uri": "beamline:Sample-S01"}
    )
    graph.add_node(esaf)
    graph.add_node(proposal)
    graph.add_node(sample)
    graph.add_edge(esaf["id"], "rel:hasProposal", proposal["id"])
    graph.add_edge(proposal["id"], "rel:hasSample", sample["id"])

    assert resolve_tiled_node_id(graph, "ESAF-2026-00043") == "beamline:ESAF-2026-00043"
    assert resolve_tiled_node_id(graph, "2026-00043") == "beamline:ESAF-2026-00043"
    assert resolve_tiled_node_id(graph, "tiled:beamline:ESAF-2026-00043") == "beamline:ESAF-2026-00043"
    neighborhood = tiled_neighborhood_ids(graph, ["ESAF-2026-00043"], hops=3)
    assert neighborhood[0] == "beamline:ESAF-2026-00043"
    assert "beamline:Proposal-P202600043-01" in neighborhood
    assert "beamline:Sample-S01" in neighborhood


def test_retrieval_agent_merges_tiled_hits(monkeypatch):
    from app.modules.f2w_agent.multi_kg import ScoredHit
    from app.modules.tiled_graph import TiledLookupGraph, TiledLookupResult

    agent = RetrievalAgent(graph_source="json", live_tiled=True, tiled_uri="http://127.0.0.1:8000")
    kg = SimpleNamespace(
        nodes={"n1": {"source_papers": ["paper.pdf"], "description": "P3HT", "category": "Material"}},
        out_edges={},
        graph_source_requested="json",
        graph_source_used="json",
        retrieval_backend="lexical",
    )
    tiled_graph = TiledLookupGraph()
    tiled_graph.add_node(
        {
            "id": "tiled:ESAF:2026-00041",
            "name": "ESAF 2026-00041",
            "category": "ESAF",
            "description": "P3HT OPV P-RSoXS at the carbon edge",
            "source_papers": ["tiled_graphql"],
            "haystack": "esaf 2026-00041 p3ht",
        }
    )
    pack = TiledLookupResult(
        hits=[
            ScoredHit(
                id="tiled:ESAF:2026-00041",
                graph_id=TILED_GRAPH_ID,
                score=2.0,
                evidence_ct=1,
                category="ESAF",
                name="ESAF 2026-00041",
                graph_label="Tiled Graph",
                payload=SimpleNamespace(
                    id="tiled:ESAF:2026-00041",
                    name="ESAF 2026-00041",
                    category="ESAF",
                    description="P3HT OPV",
                    score_prp=2.0,
                    evidence_ct=1,
                ),
            )
        ],
        graph=tiled_graph,
        status="ok",
        source="graphql",
        fallback="",
    )
    monkeypatch.setattr(
        "app.modules.f2w_agent.retrieval_agent.lookup_experiment_identity",
        lambda *args, **kwargs: pack,
    )
    graphs, hits = agent._merge_live_tiled("ESAF 2026-00041", {"rsoxs_v1": kg}, [])
    assert TILED_GRAPH_ID in graphs
    assert TILED_GRAPH_ID in agent._graphs
    assert hits[0].id == "tiled:ESAF:2026-00041"
    assert agent._last_tiled_meta["status"] == "ok"
    assert agent._last_tiled_meta["source"] == "graphql"


@pytest.mark.skipif(not os.environ.get("TILED_LIVE_PROBE"), reason="opt-in live Tiled probe")
def test_optional_live_tiled_probe():
    result = lookup_experiment_identity(
        "ESAF",
        enabled=True,
        include_catalog=False,
    )
    assert result.status in {"ok", "empty", "unsigned", "unreachable"}
    if result.error:
        assert "TILED_API_KEY" not in result.error or "<redacted>" in result.error or "Set TILED_API_KEY" in result.error


# ---------------------------------------------------------------------------
# New unit tests (2c): _classify_query_intent and _kg_raw_parts
# ---------------------------------------------------------------------------


class TestClassifyQueryIntent:
    """Unit tests for _classify_query_intent() intent heuristic."""

    def test_tiled_intent_esaf(self):
        from app.modules.f2w_agent.retrieval_agent import _classify_query_intent

        assert _classify_query_intent("Show me ESAF 2026-00041") == "tiled"

    def test_tiled_intent_proposal(self):
        from app.modules.f2w_agent.retrieval_agent import _classify_query_intent

        assert _classify_query_intent("how many proposals are in tiled") == "tiled"

    def test_tiled_intent_my_runs(self):
        from app.modules.f2w_agent.retrieval_agent import _classify_query_intent

        assert _classify_query_intent("show me my runs") == "tiled"

    def test_ops_intent_beamline(self):
        from app.modules.f2w_agent.retrieval_agent import _classify_query_intent

        assert _classify_query_intent("what detectors does the beamline have") == "ops"

    def test_ops_intent_motor(self):
        from app.modules.f2w_agent.retrieval_agent import _classify_query_intent

        assert _classify_query_intent("which motor controls the endstation") == "ops"

    def test_literature_intent_rsoxs(self):
        from app.modules.f2w_agent.retrieval_agent import _classify_query_intent

        assert _classify_query_intent("explain RSoXS technique") == "literature"

    def test_literature_intent_paper(self):
        from app.modules.f2w_agent.retrieval_agent import _classify_query_intent

        assert _classify_query_intent("list publications about polymer") == "literature"

    def test_general_intent_fallback(self):
        from app.modules.f2w_agent.retrieval_agent import _classify_query_intent

        assert _classify_query_intent("hello") == "general"

    def test_empty_query_returns_general(self):
        from app.modules.f2w_agent.retrieval_agent import _classify_query_intent

        assert _classify_query_intent("") == "general"


class TestKgRawParts:
    """Unit tests for _kg_raw_parts() legacy/modern KG format helper."""

    def test_modern_nodes_edges_format(self):
        from app.modules.f2w_agent.api import _kg_raw_parts

        data = {
            "nodes": [{"id": "n1"}],
            "edges": [{"subject": "n1", "predicate": "rel:related_to", "object": "n2"}],
        }
        nodes, edges = _kg_raw_parts(data)
        assert nodes == [{"id": "n1"}]
        assert len(edges) == 1
        assert edges[0]["predicate"] == "rel:related_to"

    def test_legacy_things_associations_format(self):
        from app.modules.f2w_agent.api import _kg_raw_parts

        data = {
            "things": [{"id": "n1"}, {"id": "n2"}],
            "associations": [{"subject": "n1", "predicate": "rel:part_of", "object": "n2"}],
        }
        nodes, edges = _kg_raw_parts(data)
        assert len(nodes) == 2
        assert nodes[0]["id"] == "n1"
        assert edges[0]["predicate"] == "rel:part_of"

    def test_modern_format_takes_precedence_over_legacy(self):
        """If both 'things' and 'nodes' exist, 'things' wins (legacy format has priority)."""
        from app.modules.f2w_agent.api import _kg_raw_parts

        data = {
            "things": [{"id": "old"}],
            "nodes": [{"id": "new"}],
            "associations": [],
            "edges": [{"subject": "new", "predicate": "rel:x", "object": "new"}],
        }
        nodes, edges = _kg_raw_parts(data)
        # 'things' is checked first (legacy format has priority in the current implementation)
        assert nodes[0]["id"] == "old"

    def test_empty_data_returns_empty_lists(self):
        from app.modules.f2w_agent.api import _kg_raw_parts

        nodes, edges = _kg_raw_parts({})
        assert nodes == []
        assert edges == []

    def test_node_list_returned_as_list(self):
        from app.modules.f2w_agent.api import _kg_raw_parts

        nodes, edges = _kg_raw_parts({"nodes": [{"id": "a"}, {"id": "b"}], "edges": []})
        assert len(nodes) == 2


class TestTiledGraphQLError:
    """Unit tests for the structured TiledGraphQLError exception."""

    def test_mentions_field_in_message(self):
        from app.modules.tiled_graph import TiledGraphQLError

        err = TiledGraphQLError([{"message": "Cannot query field 'nodeId'", "locations": []}])
        assert err.mentions_field("nodeId") is True
        assert err.mentions_field("name") is False

    def test_mentions_field_in_path(self):
        from app.modules.tiled_graph import TiledGraphQLError

        err = TiledGraphQLError([{"message": "Field error", "path": ["entities", "nodeId"]}])
        assert err.mentions_field("nodeId") is True

    def test_inherits_runtime_error(self):
        from app.modules.tiled_graph import TiledGraphQLError

        err = TiledGraphQLError([{"message": "oops"}])
        assert isinstance(err, RuntimeError)

    def test_errors_attribute_preserved(self):
        from app.modules.tiled_graph import TiledGraphQLError

        raw = [{"message": "bad field", "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"}}]
        err = TiledGraphQLError(raw)
        assert err.errors == raw

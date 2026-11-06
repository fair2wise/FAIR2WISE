"""Live-Tiled identity lookup against a fake GraphQL endpoint. ``requests.post`` is always replaced, so
nothing here can reach a real server (the default URI is a local loopback port)."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.modules.graph.backends import tiled_lookup as tg

URI = "http://127.0.0.1:9"  # never contacted: requests.post is faked in every test that could call out


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    for name in ("TILED_URI", "TILED_API_KEY", "TILED_API_KEY_SCHEME", "F2W_LIVE_TILED", "TILED_GRAPH_ENABLED"):
        monkeypatch.delenv(name, raising=False)

    def refuse(*a, **k):  # pragma: no cover - guards against accidental network use
        raise AssertionError("network call attempted")

    monkeypatch.setattr(tg.requests, "post", refuse)


class FakeGraphQL:
    """Dispatches on the query text; records every request."""

    def __init__(self, entities=None, errors=None, status=200):
        self.entities = entities or {}
        self.errors = errors
        self.status = status
        self.requests = []

    def __call__(self, url, json, headers, timeout):
        self.requests.append((url, json, headers, timeout))
        status, errors, entities = self.status, self.errors, self.entities

        class Resp:
            def raise_for_status(self_inner):
                if status >= 400:
                    raise RuntimeError(f"HTTP {status}")

            def json(self_inner):
                if errors is not None:
                    err = errors(json) if callable(errors) else errors
                    if err:
                        return {"errors": err}
                if "__typename" in json["query"]:
                    return {"data": {"__typename": "Query"}}
                if "entities(limit: 1)" in json["query"]:
                    flat = [e for rows in entities.values() for e in rows]
                    return {"data": {"entities": flat[:1]}}
                v = json["variables"]
                rows = entities.get(v["entityType"], [])
                return {"data": {"entities": rows[v["offset"]: v["offset"] + v["limit"]]}}

        return Resp()


def install(monkeypatch, fake):
    monkeypatch.setattr(tg.requests, "post", fake)
    return fake


ESAF = {"id": "e1", "entityType": "ESAF", "name": "ESAF 2026-00043", "uri": "tiled:esaf:2026-00043", "nodeId": "n1",
        "properties": {"esaf_number": "2026-00043", "scientist": "Ada", "title": "RSoXS beamtime"},
        "outgoingLinks": [{"predicate": "rel:has_proposal", "object": {"id": "p1", "entityType": "Proposal",
                                                                         "name": "P2026001234", "uri": ""}},
                          "junk", {"predicate": "x", "object": None}]}
PROPOSAL = {"id": "p1", "entityType": "Proposal", "name": "P2026001234", "uri": "",
            "properties": {"proposal_code": "P2026001234"}}


# ------------------------------------------------------------------ URIs, config, headers
def test_normalize_and_coerce_uris():
    assert tg.normalize_tiled_uri("http://h:1/api/graphql/") == "http://h:1"
    assert tg.normalize_tiled_uri("https://h/prefix/api/graphql") == "https://h/prefix"
    assert tg.normalize_tiled_uri("", default="http://d:2") == "http://d:2"
    for bad in ("ftp://h", "not a url", "http://"):
        with pytest.raises(ValueError):
            tg.normalize_tiled_uri(bad)
    assert tg.graphql_url("http://h:1/") == "http://h:1/api/graphql"
    assert tg.is_als_production_tiled_host("tiled.als.lbl.gov.") and tg.is_als_production_tiled_host("als.lbl.gov")
    assert not tg.is_als_production_tiled_host("notals.lbl.gov") and not tg.is_als_production_tiled_host(None)
    assert tg.coerce_local_tiled_uri("https://tiled.als.lbl.gov/") == tg.DEFAULT_TILED_URI
    assert tg.coerce_local_tiled_uri("garbage") == tg.DEFAULT_TILED_URI
    assert tg.coerce_local_tiled_uri("http://localhost:9000") == "http://localhost:9000"


def test_auth_headers_and_key_detection(monkeypatch):
    assert "Authorization" not in tg.auth_headers() and not tg.tiled_api_key_set()
    monkeypatch.setenv("TILED_API_KEY", "  SECRET ")
    assert tg.auth_headers()["Authorization"] == "Apikey SECRET" and tg.tiled_api_key_set()
    monkeypatch.setenv("TILED_API_KEY_SCHEME", "Bearer")
    assert tg.auth_headers()["Authorization"] == "Bearer SECRET"
    assert tg.auth_headers("other")["Authorization"] == "Bearer other" and "Authorization" not in tg.auth_headers("")


def test_load_config_precedence_between_flags_and_uri(monkeypatch):
    assert tg.load_tiled_config().enabled is False
    monkeypatch.setenv("TILED_URI", "http://h:5/api/graphql")
    cfg = tg.load_tiled_config()
    assert cfg.enabled is True and cfg.uri == "http://h:5"  # a configured URI enables lookup by default
    monkeypatch.setenv("F2W_LIVE_TILED", "0")
    assert tg.load_tiled_config().enabled is False
    monkeypatch.setenv("F2W_LIVE_TILED", "")
    monkeypatch.setenv("TILED_GRAPH_ENABLED", "yes")
    assert tg.load_tiled_config().enabled is True
    assert tg.load_tiled_config(enabled=False).enabled is False
    monkeypatch.setenv("TILED_API_KEY", "k")
    assert tg.load_tiled_config(uri="http://x:1").api_key_set is True
    assert tg._truthy(None) is None and tg._truthy(" ") is None and tg._truthy("maybe") is None
    assert tg._truthy("ON") is True and tg._truthy("off") is False


def test_config_snapshot_statuses():
    assert tg.TiledGraphConfig(False, "u", False).snapshot()["tiled_status"] == "disabled"
    assert tg.TiledGraphConfig(True, "u", False, uri_error="bad").snapshot()["tiled_status"] == "invalid_uri"
    assert tg.TiledGraphConfig(True, "u", False).snapshot()["tiled_status"] == "unsigned"
    assert tg.TiledGraphConfig(True, "u", True).snapshot()["tiled_status"] == "configured"


# ------------------------------------------------------------------ GraphQL transport and probe
def test_tiled_graphql_posts_with_auth_and_surfaces_errors(monkeypatch):
    fake = install(monkeypatch, FakeGraphQL(entities={"ESAF": [ESAF]}))
    data = tg.tiled_graphql("query { __typename }", uri=URI, api_key="K", timeout=2)
    assert data == {"__typename": "Query"}
    url, body, headers, timeout = fake.requests[0]
    assert url == f"{URI}/api/graphql" and headers["Authorization"] == "Apikey K" and timeout == 2
    install(monkeypatch, FakeGraphQL(errors=[{"message": "no field nodeId", "path": ["entities", "nodeId"]}, "junk"]))
    with pytest.raises(tg.TiledGraphQLError) as err:
        tg.tiled_graphql("q", uri=URI)
    assert err.value.mentions_field("nodeId") and not err.value.mentions_field("absent")
    assert tg.TiledGraphQLError([{"path": ["a", "b"]}]).mentions_field("b")
    install(monkeypatch, FakeGraphQL(status=500))
    with pytest.raises(RuntimeError):
        tg.tiled_graphql("q", uri=URI)


def test_safe_error_redacts_the_api_key(monkeypatch):
    monkeypatch.setenv("TILED_API_KEY", "TOPSECRET")
    text = tg._safe_error(RuntimeError("failed with TOPSECRET in url " + "x" * 400))
    assert "TOPSECRET" not in text and "<redacted>" in text and len(text) <= 300


def test_probe_status_variants(monkeypatch):
    assert tg.probe_tiled_status(enabled=False)["tiled_status"] == "disabled"
    install(monkeypatch, FakeGraphQL(entities={"ESAF": [ESAF]}))
    assert tg.probe_tiled_status(uri=URI, enabled=True)["tiled_status"] == "ok"
    install(monkeypatch, FakeGraphQL())
    unsigned = tg.probe_tiled_status(uri=URI, enabled=True)
    assert unsigned["tiled_status"] == "unsigned" and "TILED_API_KEY" in unsigned["tiled_error"]
    monkeypatch.setenv("TILED_API_KEY", "k")
    empty = tg.probe_tiled_status(uri=URI, enabled=True)
    assert empty["tiled_status"] == "empty" and "API key is loaded" in empty["tiled_error"]
    install(monkeypatch, FakeGraphQL(status=503))
    assert tg.probe_tiled_status(uri=URI, enabled=True)["tiled_status"] == "unreachable"


def test_probe_second_query_failure_depends_on_key(monkeypatch):
    class Flaky(FakeGraphQL):
        def __call__(self, url, json, headers, timeout):
            if "entities(limit: 1)" in json["query"]:
                raise ConnectionError("dropped")
            return super().__call__(url, json, headers, timeout)

    install(monkeypatch, Flaky())
    assert tg.probe_tiled_status(uri=URI, enabled=True)["tiled_status"] == "unsigned"
    monkeypatch.setenv("TILED_API_KEY", "k")
    assert tg.probe_tiled_status(uri=URI, enabled=True)["tiled_status"] == "unreachable"


def test_probe_reports_invalid_uri(monkeypatch):
    monkeypatch.setattr(tg, "coerce_local_tiled_uri", lambda value: (_ for _ in ()).throw(ValueError("bad uri")))
    out = tg.probe_tiled_status(uri="x", enabled=True)
    assert out["tiled_status"] == "invalid_uri"


# ------------------------------------------------------------------ questions and matching
def test_identity_question_detection_and_requested_types():
    assert tg.is_experiment_identity_question("What is ESAF 2026-00043?") and tg.is_experiment_identity_question("P20260012 info")
    assert tg.is_experiment_identity_question("show my last scan") and not tg.is_experiment_identity_question("what is RSoXS")
    assert not tg.is_experiment_identity_question("")
    assert tg.requested_entity_types("which samples and runs exist") == ["Sample", "BlueskyRun"]
    assert tg.requested_entity_types("anything about 2026-00043") == ["ESAF"]
    assert tg.requested_entity_types("P2026001234 details") == ["Proposal"]
    assert tg.requested_entity_types("unrelated words here") == list(tg.ENTITY_TYPES)


def test_entity_to_node_builds_description_aliases_and_identity_keys():
    node = tg.entity_to_node(ESAF)
    assert node["id"] == "tiled:esaf:2026-00043" and node["category"] == "ESAF" and node["esaf_number"] == "2026-00043"
    assert "ESAF 2026-00043" in node["description"] and "scientist Ada" in node["description"]
    assert "rsoxs beamtime" in node["haystack"] and node["nodeId"] == "n1"
    bare = tg.entity_to_node({"id": "g1", "entityType": "Sample", "properties": {"sample_code": "S1", "plan_name": "scan",
                                                                                 "scan_id": 4, "uid": "u-1",
                                                                                 "proposal_id": "P1"}})
    assert bare["id"] == "tiled:Sample:g1" and bare["name"] == "tiled:Sample:g1"
    assert all(bit in bare["description"] for bit in ("sample S1", "uid u-1", "scan_id 4", "plan scan", "proposal P1"))
    assert tg.entity_to_node({})["id"] == "tiled:Thing:unknown"
    assert tg.entity_to_node({"properties": "notadict", "entityType": None})["category"] == "Thing"
    aliases = tg.tiled_node_aliases("tiled:esaf:2026-00043", node)
    assert {"esaf 2026 00043", "2026 00043"} <= aliases and "abc" not in aliases


def test_tiled_lookup_candidates_and_resolution_prefers_typed_entities():
    assert tg.tiled_lookup_candidates("") == []
    assert tg.tiled_lookup_candidates("tiled:beamline:ESAF-2026-00043") == [
        "tiled:beamline:ESAF-2026-00043", "beamline:ESAF-2026-00043", "ESAF-2026-00043"]
    graph = tg.TiledLookupGraph()
    graph.add_node(tg.entity_to_node(ESAF))
    graph.add_node(tg.entity_to_node(PROPOSAL))
    graph.add_node({"id": "tiled:count:ESAF", "name": "inventory", "category": "ESAF"})
    graph.add_node({"name": "no id"})
    assert tg.resolve_tiled_node_id(graph, "tiled:esaf:2026-00043") == "tiled:esaf:2026-00043"
    assert tg.resolve_tiled_node_id(graph, "TILED:ESAF:2026-00043") == "tiled:esaf:2026-00043"
    assert tg.resolve_tiled_node_id(graph, "ESAF-2026-00043") == "tiled:esaf:2026-00043"  # matched through aliases
    assert tg.resolve_tiled_node_id(graph, "tiled:beamline:P2026001234") == "tiled:Proposal:p1"
    assert tg.resolve_tiled_node_id(graph, "abc") is None and tg.resolve_tiled_node_id(graph, "") is None
    assert tg.resolve_tiled_node_id(SimpleNamespace(nodes="bad"), "x") is None


def test_neighborhood_walks_undirected_edges_within_hops():
    graph = tg.TiledLookupGraph()
    for nid in ("a", "b", "c", "d"):
        graph.add_node({"id": nid, "name": nid * 4, "category": "Sample"})
    graph.add_edge("a", "rel:x", "b")
    graph.add_edge("c", "rel:y", "b")
    graph.add_edge("c", "rel:z", "d")
    graph.out_edges["bad"] = "not a list"
    graph.out_edges["c"].append("junk")
    assert tg.tiled_neighborhood_ids(graph, ["a"], hops=1) == ["a", "b"]
    assert set(tg.tiled_neighborhood_ids(graph, ["a"], hops=3)) == {"a", "b", "c", "d"}
    assert tg.tiled_neighborhood_ids(graph, ["a", "a", "zzzz"], hops=0) == ["a"]
    assert tg.tiled_neighborhood_ids(graph, ["zzzz"]) == [] and tg.tiled_neighborhood_ids(SimpleNamespace(nodes=[]), ["a"]) == []


def test_search_and_context_rendering_for_tiled_nodes():
    graph = tg.TiledLookupGraph()
    graph.add_node(tg.entity_to_node(ESAF))
    graph.add_node(tg.entity_to_node(PROPOSAL))
    graph.add_edge("tiled:esaf:2026-00043", "rel:has_proposal", "tiled:Proposal:p1")
    hits = graph.semantic_search("esaf 2026-00043", topk=5)
    assert hits[0].id == "tiled:esaf:2026-00043" and hits[0].score > 0 and graph.semantic_search("zzz qqq") == []
    ctx = graph.build_context(hits + [SimpleNamespace(id="tiled:Proposal:p1", name=None, category=None, description=None)],
                              char_budget=8000)
    assert "Structured_KG_Facts" in ctx and "has_proposal" in ctx and "## ESAF 2026-00043 (ESAF)" in ctx
    assert "uri: tiled:esaf:2026-00043" in ctx and "nodeId: n1" in ctx and "Description:" in ctx
    assert "Structured_KG_Facts" not in graph.build_context(hits, include_structured=False)
    tight = graph.build_context(hits, char_budget=len(ctx.split("\n\n")[0]) + 5)
    assert "## ESAF" not in tight  # nothing fits beyond the header


def test_match_score_components():
    node = tg.entity_to_node(ESAF)
    assert tg._match_score("2026-00043", node, tg._query_tokens("2026-00043")) >= 2.0
    assert tg._match_score("P2026001234", tg.entity_to_node(PROPOSAL), tg._query_tokens("p2026001234")) >= 1.8
    assert tg._match_score("esaf 2026-00043", {"name": "esaf 2026-00043", "haystack": "x"}, set()) == 1.6
    count = {"id": "tiled:count:ESAF", "name": "n", "haystack": ""}
    assert tg._match_score("how many", count, tg._query_tokens("how many")) >= 1.4
    assert tg._match_score("scans", {"name": "z", "category": "BlueskyRun", "haystack": "none"}, {"scans"}) > 0


# ------------------------------------------------------------------ paging and lookup
def test_page_entities_pages_and_falls_back_when_node_id_is_unsupported(monkeypatch):
    rows = [dict(ESAF, id=f"e{i}") for i in range(5)]

    def errors(body):
        return [{"message": "Cannot query field nodeId"}] if "nodeId" in body["query"] else None

    fake = install(monkeypatch, FakeGraphQL(entities={"ESAF": rows}, errors=errors))
    out = tg._page_entities("ESAF", uri=URI, api_key=None, page_size=2, max_pages=5)
    assert len(out) == 5
    assert "nodeId" in fake.requests[0][1]["query"] and "nodeId" not in fake.requests[1][1]["query"]
    capped = tg._page_entities("ESAF", uri=URI, api_key=None, page_size=1, max_pages=2)
    assert len(capped) == 2
    install(monkeypatch, FakeGraphQL(errors=[{"message": "denied"}]))
    with pytest.raises(tg.TiledGraphQLError):
        tg._page_entities("ESAF", uri=URI, api_key=None)


def test_page_entities_ignores_malformed_payloads(monkeypatch):
    monkeypatch.setattr(tg, "tiled_graphql", lambda *a, **k: {"entities": "nope"})
    assert tg._page_entities("ESAF", uri=URI, api_key=None) == []
    monkeypatch.setattr(tg, "tiled_graphql", lambda *a, **k: {"entities": [ESAF, "junk"]})
    assert tg._page_entities("ESAF", uri=URI, api_key=None) == [ESAF]


def test_lookup_gates_disabled_skipped_and_invalid(monkeypatch):
    assert tg.lookup_experiment_identity("esaf?", enabled=False).status == "disabled"
    assert tg.lookup_experiment_identity("what is RSoXS?", uri=URI, enabled=True).status == "skipped"
    monkeypatch.setattr(tg, "load_tiled_config", lambda **kw: tg.TiledGraphConfig(True, "d", False, uri_error="bad"))
    res = tg.lookup_experiment_identity("esaf?")
    assert res.status == "invalid_uri" and res.error == "bad"


def test_lookup_ok_builds_overlay_graph_with_counts_and_hits(monkeypatch):
    install(monkeypatch, FakeGraphQL(entities={"ESAF": [ESAF], "Proposal": [PROPOSAL]}))
    res = tg.lookup_experiment_identity("tell me about ESAF 2026-00043", uri=URI, api_key="K", enabled=True)
    assert res.status == "ok" and res.fallback == "" and res.source == "graphql"
    assert res.entity_counts == {"ESAF": 1, "Proposal": 1, "Sample": 0, "BlueskyRun": 0}
    assert res.hits[0].graph_id == "tiled" and "tiled:count:ESAF" in res.graph.nodes
    meta = res.meta()
    assert meta["used"] is True and meta["enabled"] is True and meta["graph_id"] == "tiled" and meta["fallback"] is None


def test_lookup_unsigned_empty_unreachable_and_no_match(monkeypatch):
    install(monkeypatch, FakeGraphQL())
    unsigned = tg.lookup_experiment_identity("esaf list", uri=URI, enabled=True, include_catalog=False)
    assert unsigned.status == "unsigned" and unsigned.fallback == "json_kg" and unsigned.meta()["used"] is False
    install(monkeypatch, FakeGraphQL(status=500))
    down = tg.lookup_experiment_identity("esaf list", uri=URI, enabled=True)
    assert down.status == "unreachable" and "HTTP 500" in down.error
    monkeypatch.setenv("TILED_API_KEY", "k")
    install(monkeypatch, FakeGraphQL(entities={"Sample": [{"id": "s", "entityType": "Sample", "name": "Quartz",
                                                           "properties": {}}]}))
    res = tg.lookup_experiment_identity("proposal", uri=URI, enabled=True, include_catalog=False)
    # the synthetic per-type inventory nodes always qualify, so an identity question never comes back "empty"
    assert res.status == "ok" and any(h.id.startswith("tiled:count:") for h in res.hits)


# ------------------------------------------------------------------ catalog supplement
class FakeNode:
    def __init__(self, metadata=None, children=None, uri="", item=None):
        self.metadata, self.children, self.uri, self.item = metadata, children or {}, uri, item

    def __iter__(self):
        return iter(self.children)

    def __getitem__(self, key):
        child = self.children[key]
        if isinstance(child, Exception):
            raise child
        return child


def _install_catalog(monkeypatch, root):
    client = SimpleNamespace(from_uri=lambda uri, **kw: root(uri, kw) if callable(root) else root)
    monkeypatch.setitem(sys.modules, "tiled", SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules, "tiled.client", client)


def test_catalog_runs_walks_nested_nodes_and_extracts_bluesky_runs(monkeypatch):
    run1 = FakeNode(metadata={"start": {"uid": "u-1234567890", "scan_id": 7, "plan_name": "scan",
                                        "sample": {"name": "film"}, "proposal": "P1"}}, uri="http://c/u1",
                    item={"id": "node-1"})
    run2 = FakeNode(metadata={"scan_id": 9, "uid": "u-2"})
    group = FakeNode(metadata={"kind": "folder"}, children={"r1": run1, "bad": RuntimeError("boom"), "r2": run2})
    root = FakeNode(metadata={"title": "root"}, children={"g": group})
    seen = {}
    _install_catalog(monkeypatch, lambda uri, kw: seen.update(uri=uri, kw=kw) or root)
    runs, error = tg._catalog_runs("last scan", uri=URI, api_key="K")
    assert error is None and seen["kw"] == {"api_key": "K"}
    assert [r["properties"]["uid"] for r in runs] == ["u-1234567890", "u-2"]
    first = runs[0]
    assert first["entityType"] == "BlueskyRun" and first["name"] == "scan 7 scan" and first["nodeId"] == "node-1"
    assert first["properties"]["sample_name"] == "film" and first["properties"]["catalog_path"] == ["g", "r1"]
    limited, _ = tg._catalog_runs("x", uri=URI, api_key=None, limit=1)
    assert len(limited) == 1


def test_catalog_runs_reports_missing_client_and_unreachable_catalog(monkeypatch):
    monkeypatch.setitem(sys.modules, "tiled.client", None)
    monkeypatch.setitem(sys.modules, "tiled", None)
    runs, error = tg._catalog_runs("q", uri=URI, api_key=None)
    assert runs == [] and "not installed" in error

    def refuse(uri, kw):
        raise ConnectionError("no catalog")

    _install_catalog(monkeypatch, refuse)
    runs, error = tg._catalog_runs("q", uri=URI, api_key=None)
    assert runs == [] and "catalog unreachable" in error


def test_lookup_adds_catalog_runs_when_graphql_has_none(monkeypatch):
    monkeypatch.setenv("TILED_API_KEY", "k")
    install(monkeypatch, FakeGraphQL(entities={"Sample": [{"id": "s", "entityType": "Sample", "name": "Quartz"}]}))
    run = FakeNode(metadata={"start": {"uid": "u-abcdef12", "scan_id": 3, "plan_name": "count"}})
    _install_catalog(monkeypatch, FakeNode(children={"r": run}))
    res = tg.lookup_experiment_identity("show the last scan", uri=URI, enabled=True)
    assert res.source == "graphql+catalog" and res.entity_counts["BlueskyRun"] == 1
    assert any(h.category == "BlueskyRun" for h in res.hits)
    _install_catalog(monkeypatch, FakeNode(children={}))
    none = tg.lookup_experiment_identity("show the last scan", uri=URI, enabled=True)
    assert none.source == "graphql"

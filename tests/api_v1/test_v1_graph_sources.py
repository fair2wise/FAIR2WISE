"""UI graph sources (owner instruction 2026-11-06): ``GET /v1/graph/sources`` (V80) and the ``source`` parameter.

Real JSON KG files, a fake Tiled scanner holding F2W literature rows (final, pending, retired, release marker,
foreign) and the real ``create_api_app`` on ``:local`` and ``:als-dev``. Pins: the default read is unchanged, both
kinds answer in the JSON node/edge wire shape, ids are server-allowlisted (no path, URL or Tiled target is
accepted), every source is read-only, a Tiled outage is typed and never an empty graph, the scan is cached.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest
from _v1_support import H

from app.api.policies import API_ROUTE_POLICIES
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.f2w_agent import graph_sources as gs
from app.modules.graph import identity_adapter as ida
from app.modules.graph import literature_writer as lw

GID = "e2e_demo"


def entity(tid: str, node_id: str, *, name: str = "", category: str = "Material", graph: str = GID,
           extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    props = ida.entity_properties(graph, node_id, {"id": node_id, "name": name or node_id, "category": category,
                                                   **(extra or {})})
    return {"id": tid, "name": name or node_id, "entityType": category, "uri": ida.entity_uri(graph, node_id),
            "properties": props}


def link(lid: str, subject_tid: str, obj_tid: str, predicate: str = "rel:related_to", graph: str = GID,
         key: str | None = None) -> Dict[str, Any]:
    return {"id": lid, "subjectId": subject_tid, "objectId": obj_tid, "predicate": predicate,
            "properties": ida.link_properties(graph, key or lid, {"subject": "x", "predicate": predicate,
                                                                  "object": "y"})}


def marker(snapshot: str = "v1", graph: str = GID) -> Dict[str, Any]:
    props = ida.entity_properties(graph, lw.RELEASE_NODE, {"snapshot_id": snapshot, "graph_sha256": "ab" * 32})
    return {"id": "t-marker", "name": "", "entityType": lw.RELEASE_TYPE, "uri": lw.release_uri(graph),
            "properties": props}


def seed_rows() -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    pending = entity("t-pend", "m:pending")
    pending["uri"] = lw.state_uri(GID, "m:pending", lw.PENDING, "tag1")
    retired = entity("t-ret", "m:retired")
    retired["uri"] = lw.state_uri(GID, "m:retired", lw.RETIRED, "tag0")
    retired["entityType"] = lw.RETIRED_TYPE
    forge = {"id": "t-forge", "name": "Sample", "entityType": "Sample", "uri": "forge:sample/1",
             "properties": {"note": "someone else's record"}}
    other = entity("t-other", "o:1", graph="other_corpus")
    entities = [entity("t-a", "m:a", name="Alpha film", extra={"description": "thin film of alpha"}),
                entity("t-b", "m:b", name="Beta resin", category="Process"),
                entity("t-c", "m:c", name="Gamma", category="Technique"),
                marker(), pending, retired, forge, other]
    links = [link("l-ab", "t-a", "t-b"), link("l-bc", "t-b", "t-c", predicate="rel:processed_by"),
             link("l-pend", "t-a", "t-pend"), link("l-ret", "t-a", "t-ret"),
             link("l-forge", "t-a", "t-forge")]
    return entities, links


class FakeScanner:
    """A Tiled graph client double: counts the scans, can fail like an unreachable server."""

    def __init__(self, entities: List[Dict[str, Any]], links: List[Dict[str, Any]]) -> None:
        self.entities, self.links = entities, links
        self.entity_scans = self.link_scans = 0
        self.error: Exception | None = None

    def scan_entities(self) -> List[Dict[str, Any]]:
        self.entity_scans += 1
        if self.error is not None:
            raise self.error
        return list(self.entities)

    def scan_links(self) -> List[Dict[str, Any]]:
        self.link_scans += 1
        return list(self.links)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# --------------------------------------------------------------------------- Tiled mapping (pure)

def test_only_final_records_of_a_graph_are_nodes_and_edges():
    entities, links = seed_rows()
    graphs = gs.build_tiled_graphs(entities, links)
    assert sorted(graphs) == [GID, "other_corpus"]
    demo = graphs[GID]
    assert [n["id"] for n in demo.nodes] == ["m:a", "m:b", "m:c"]          # no pending, retired, marker, forge
    assert [(e["subject"], e["predicate"], e["object"]) for e in demo.edges] == [
        ("m:a", "rel:related_to", "m:b"), ("m:b", "rel:processed_by", "m:c")]
    assert demo.revision.startswith("v1@abababababab") and demo.label == "e2e_demo / v1"
    assert graphs["other_corpus"].label == "other_corpus" and graphs["other_corpus"].revision == "unversioned"
    assert not demo.conflict


def test_node_payload_is_the_stored_json_node_without_reserved_markers():
    entities, links = seed_rows()
    node = gs.build_tiled_graphs(entities, links)[GID].nodes[0]
    assert node["name"] == "Alpha film" and node["category"] == "Material" and node["description"]
    assert not set(node) & ida.RESERVED_KEYS


def test_row_fields_top_up_a_payload_without_name_or_category():
    row = entity("t-x", "m:x")
    row["properties"] = {k: v for k, v in row["properties"].items() if k not in ("name", "category")}
    row["name"], row["entityType"] = "From row", "Sample"
    node = gs.tiled_node(row, "m:x")
    assert (node["name"], node["category"]) == ("From row", "Sample")


def test_two_final_entities_for_one_node_mark_the_graph_inconsistent_not_pick_one():
    entities, links = seed_rows()
    entities.append(entity("t-a2", "m:a"))
    assert gs.build_tiled_graphs(entities, links)[GID].conflict


def test_a_link_to_a_foreign_graph_entity_is_not_an_edge():
    entities, links = seed_rows()
    links.append(link("l-x", "t-a", "t-other"))
    assert len(gs.build_tiled_graphs(entities, links)[GID].edges) == 2


# --------------------------------------------------------------------------- catalog, ids, cache

def make_sources(tmp_path, scanner=None, clock=None, **kw) -> gs.GraphSources:
    kg = tmp_path / "kg"
    kg.mkdir(exist_ok=True)
    (kg / "matkg_one.json").write_text(json.dumps(H.SEED_GRAPH), encoding="utf-8")
    files = [gs.JsonFile("one", "one", "matkg_one.json", kg / "matkg_one.json")]
    return gs.GraphSources(flat_json=lambda: files, tiled=scanner, clock=clock or Clock(), **kw)


def test_listing_has_json_and_tiled_sources_all_read_only(tmp_path):
    entities, links = seed_rows()
    listing = make_sources(tmp_path, FakeScanner(entities, links)).listing().as_json()
    ids = [s["id"] for s in listing["sources"]]
    assert ids == ["json:one", "tiled:e2e_demo", "tiled:other_corpus"]
    assert all(s["read_only"] is True and s["kind"] in ("json", "tiled") for s in listing["sources"])
    demo = listing["sources"][1]
    assert (demo["node_count"], demo["edge_count"], demo["available"]) == (3, 2, True)
    assert listing["tiled"] == {"status": "ok", "detail": None}
    # T19E frontend closure: every row carries the Knowledge Operations graph_id (never a path or a URL).
    assert [s["graph_id"] for s in listing["sources"]] == ["one", "e2e_demo", "other_corpus"]


def test_without_a_scanner_tiled_is_not_offered(tmp_path):
    listing = make_sources(tmp_path).listing()
    assert listing.tiled_status == "not_offered" and [s.id for s in listing.sources] == ["json:one"]
    with pytest.raises(gs.SourceError) as exc:
        make_sources(tmp_path).graph("tiled:e2e_demo")
    assert (exc.value.code, exc.value.status) == ("unknown_source", 404)


@pytest.mark.parametrize("bad", [
    "", "one", "json:", "json:../../etc/passwd", "json:/etc/passwd", "json:..", "json:one/../../x",
    "json:one\\..\\x", "json:one%2f..%2fx", "tiled:http://evil.example/graph", "tiled://127.0.0.1:1/x",
    "http://127.0.0.1:8765", "file:///etc/passwd", "tiled:e2e_demo?x=1", "json:one\x00", "json:" + "a" * 200,
    "TILED:e2e_demo", "json:one ",
])
def test_a_path_url_or_malformed_id_is_unknown_and_never_touches_the_filesystem_or_tiled(tmp_path, bad):
    scanner = FakeScanner(*seed_rows())
    sources = make_sources(tmp_path, scanner)
    with pytest.raises(gs.SourceError) as exc:
        sources.graph(bad)
    assert (exc.value.code, exc.value.status) == ("unknown_source", 404)
    assert scanner.entity_scans == 0


def test_a_well_formed_but_unlisted_id_is_unknown(tmp_path):
    sources = make_sources(tmp_path, FakeScanner(*seed_rows()))
    for sid in ("json:nope", "tiled:nope", "json:synthetic/v9"):
        with pytest.raises(gs.SourceError) as exc:
            sources.graph(sid)
        assert exc.value.code == "unknown_source"


def test_the_tiled_scan_is_cached_for_the_ttl_and_shared_by_every_read(tmp_path):
    scanner, clock = FakeScanner(*seed_rows()), Clock()
    sources = make_sources(tmp_path, scanner, clock)
    sources.listing()
    sources.graph("tiled:e2e_demo")
    sources.graph("tiled:other_corpus")
    sources.listing()
    assert (scanner.entity_scans, scanner.link_scans) == (1, 1)
    clock.now += gs.TILED_TTL_SECONDS + 1
    sources.graph("tiled:e2e_demo")
    assert scanner.entity_scans == 2


def test_a_tiled_outage_is_typed_unavailable_never_an_empty_graph_and_is_not_hammered(tmp_path):
    scanner, clock = FakeScanner(*seed_rows()), Clock()
    scanner.error = ContractError(ErrorCode.UNAVAILABLE, "Tiled graph at http://127.0.0.1:8765 is unavailable")
    sources = make_sources(tmp_path, scanner, clock)
    listing = sources.listing()
    assert listing.tiled_status == "unavailable" and [s.id for s in listing.sources] == ["json:one"]
    assert "127.0.0.1" not in json.dumps(listing.as_json())                   # no target in the text
    with pytest.raises(gs.SourceError) as exc:
        sources.graph("tiled:e2e_demo")
    assert (exc.value.code, exc.value.status) == ("source_unavailable", 503) and "127.0.0.1" not in exc.value.message
    assert scanner.entity_scans == 1
    scanner.error = None
    clock.now += gs.TILED_ERROR_TTL_SECONDS + 0.1                              # recovers on the next window
    assert sources.graph("tiled:e2e_demo").nodes


def test_an_inconsistent_tiled_graph_is_listed_unavailable_and_refused(tmp_path):
    entities, links = seed_rows()
    entities.append(entity("t-a2", "m:a"))
    sources = make_sources(tmp_path, FakeScanner(entities, links))
    demo = next(s for s in sources.listing().sources if s.id == "tiled:e2e_demo")
    assert demo.available is False and demo.detail
    with pytest.raises(gs.SourceError) as exc:
        sources.graph("tiled:e2e_demo")
    assert exc.value.code == "source_inconsistent"


def test_the_json_cache_follows_the_file(tmp_path):
    sources = make_sources(tmp_path)
    first = sources.graph("json:one")
    assert sources.graph("json:one") is first
    path = tmp_path / "kg" / "matkg_one.json"
    path.write_text(json.dumps({"things": [{"id": "n1", "name": "N1"}], "associations": []}) + " ", encoding="utf-8")
    assert [n["id"] for n in sources.graph("json:one").nodes] == ["n1"]


def test_oversized_and_broken_json_are_typed(tmp_path, monkeypatch):
    sources = make_sources(tmp_path)
    monkeypatch.setattr(gs, "MAX_JSON_BYTES", 10)
    with pytest.raises(gs.SourceError) as exc:
        sources.graph("json:one")
    assert (exc.value.code, exc.value.status) == ("source_too_large", 413)
    monkeypatch.setattr(gs, "MAX_JSON_BYTES", 10**9)
    (tmp_path / "kg" / "matkg_one.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(gs.SourceError) as exc2:
        sources.graph("json:one")
    assert exc2.value.code == "source_unreadable" and str(tmp_path) not in exc2.value.message


def test_node_cap_truncates_and_drops_edges_to_cut_nodes():
    nodes = [{"id": f"n{i}", "name": f"N{i}"} for i in range(5)]
    edges = [{"subject": "n0", "predicate": "p", "object": "n1"}, {"subject": "n0", "predicate": "p", "object": "n4"}]
    graph = gs.build_source_graph("json:x", "json", "x", "x", "r", nodes, edges, cap=3)
    assert graph.truncated and len(graph.nodes) == 3 and len(graph.edges) == 1


def test_search_and_neighborhood_are_bounded_and_ranked(tmp_path):
    sources = make_sources(tmp_path, FakeScanner(*seed_rows()))
    graph = sources.graph("tiled:e2e_demo")
    hits = gs.GraphSources.search(graph, "alpha", 5)
    assert [n["id"] for n, _ in hits] == ["m:a"] and hits[0][1] == 0.8
    assert gs.GraphSources.search(graph, "thin film", 5)[0][1] == 0.3
    assert gs.GraphSources.search(graph, "   ", 5) == []
    one = gs.GraphSources.neighborhood(graph, "m:a", 1)
    assert one is not None and [n["id"] for n in one.nodes] == ["m:a", "m:b"]
    two = gs.GraphSources.neighborhood(graph, "m:a", 99)                        # hops clamp to MAX_HOPS
    assert two is not None and {n["id"] for n in two.nodes} == {"m:a", "m:b", "m:c"} and len(two.edges) == 2
    assert gs.GraphSources.neighborhood(graph, "nope", 1) is None


def test_adopted_releases_are_offered_only_under_a_real_snapshot_layout(tmp_path):
    root = tmp_path / "storage_kg"
    good = root / "synthetic" / "v1"
    good.mkdir(parents=True)
    (good / "graph.json").write_text(json.dumps({"nodes": [{"id": "a", "name": "A"}], "edges": []}))
    bad = root / "Bad Corpus" / "v1"
    bad.mkdir(parents=True)
    (bad / "graph.json").write_text("{}")
    (root / "synthetic" / "vX").mkdir()
    (root / "synthetic" / "vX" / "graph.json").write_text("{}")
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"nodes": [{"id": "leak"}], "edges": []}))
    (root / "synthetic" / "v2").mkdir()
    (root / "synthetic" / "v2" / "graph.json").symlink_to(outside)
    sources = gs.GraphSources(flat_json=lambda: [], release_root=root)
    assert [s.id for s in sources.listing().sources] == ["json:synthetic/v1"]
    assert [n["id"] for n in sources.graph("json:synthetic/v1").nodes] == ["a"]
    with pytest.raises(gs.SourceError):
        sources.graph("json:synthetic/v2")


def _release(root, corpus, snap):
    d = root / corpus / snap
    d.mkdir(parents=True)
    (d / "graph.json").write_text(json.dumps({"nodes": [{"id": "a", "name": "A"}], "edges": []}))


def _flat(tmp_path, *names):
    kg = tmp_path / "kg"
    kg.mkdir(exist_ok=True)
    files = []
    for name in names:
        (kg / f"matkg_{name}.json").write_text(json.dumps({"nodes": [], "edges": []}))
        files.append(gs.JsonFile(name, name, f"matkg_{name}.json", kg / f"matkg_{name}.json"))
    return files


def test_default_source_is_json_never_tiled_and_follows_a_fixed_rule(tmp_path):
    """Task 5: the viewer opens on a JSON KG. Live corpus release > newest rsoxs file > newest release > first file."""
    root = tmp_path / "storage_kg"
    _release(root, "qwen3_235b_580papers", "v1")
    _release(root, "rsoxs", "v3")
    _release(root, "rsoxs", "v4")
    files = _flat(tmp_path, "bl1101_v9", "rsoxs_v2", "rsoxs_v10")
    entities, links = seed_rows()

    def sources(live=None, flat=files, rel=root):
        out = gs.GraphSources(flat_json=lambda: flat, release_root=rel, tiled=FakeScanner(entities, links))
        out.set_editable((lambda c: c == live) if live else None)
        return out

    # (1) the newest release of the live corpus wins over everything, even a Tiled graph being listed
    assert sources(live="rsoxs").listing().default == "json:rsoxs/v4"
    assert sources(live="qwen3_235b_580papers").listing().default == "json:qwen3_235b_580papers/v1"
    # (2) no live corpus: the newest matkg_rsoxs_vN file (numeric, v10 > v2), as the Settings default picks
    assert sources().listing().default == "json:rsoxs_v10"
    # (3) no rsoxs file: the newest release of any corpus (corpus name, then version)
    assert sources(flat=_flat(tmp_path, "bl1101_v9")).listing().default == "json:rsoxs/v4"
    # (4) only flat files: the first one in listing order; nothing JSON: None
    assert sources(flat=_flat(tmp_path, "bl1101_v9", "zzz"), rel=None).listing().default == "json:bl1101_v9"
    assert sources(flat=[], rel=None).listing().default is None
    assert sources().listing().as_json()["default"] == "json:rsoxs_v10"


# --------------------------------------------------------------------------- routes (local)

def test_the_policy_row_is_the_same_entry_capability_as_get_graph():
    row = next(p for p in API_ROUTE_POLICIES if p.route_id == "V80")
    assert (row.method, row.path, row.capabilities) == ("GET", "/v1/graph/sources", frozenset({"chat.read_support"}))
    assert row.on_als is True


@pytest.fixture
def sources_of(local):
    return local.app.state.f2w_graph_sources


def test_default_graph_is_unchanged_without_a_source(local, sources_of):
    c = local.client()
    plain = c.get("/graph")
    assert plain.status_code == 200 and "X-F2W-Graph-Truncated" not in plain.headers
    assert not plain.json()["source_path"].startswith("source:")
    assert c.get("/graph", params={"source": ""}).content == plain.content
    sources_of.attach_tiled(FakeScanner(*seed_rows()))                          # wiring Tiled changes nothing here
    assert c.get("/graph").content == plain.content


def test_listing_route_local_has_the_workspace_file_and_never_a_path(local):
    body = local.client().get("/v1/graph/sources").json()
    assert [s["id"] for s in body["sources"]] == ["json:contract_v1"] and body["tiled"]["status"] == "not_offered"
    assert "storage" not in json.dumps(body) and str(local.app) not in json.dumps(body)


def test_json_source_reads_in_the_same_wire_shape_as_the_default(local):
    c = local.client()
    default = c.get("/graph").json()
    picked = c.get("/graph", params={"source": "json:contract_v1"})
    assert picked.status_code == 200
    body = picked.json()
    assert body["source_path"] == "source:json:contract_v1"
    assert [n["id"] for n in body["nodes"]] == [n["id"] for n in default["nodes"]]
    assert body["edges"] == default["edges"]
    assert set(body["nodes"][0]) == set(default["nodes"][0])
    node = c.get("/graph/node/matkg:Code1", params={"source": "json:contract_v1"}).json()
    assert node["code_snippet"].startswith("def gamma") and node["graph_id"]
    linked = c.get("/graph/node/matkg:TermA", params={"source": "json:contract_v1"}).json()
    assert [s["function_name"] for s in linked["linked_code_snippets"]] == ["gamma"]


def test_tiled_source_reads_search_node_and_neighborhood(local, sources_of):
    scanner = FakeScanner(*seed_rows())
    sources_of.attach_tiled(scanner)
    c = local.client()
    listing = c.get("/v1/graph/sources").json()
    assert [s["id"] for s in listing["sources"]] == ["json:contract_v1", "tiled:e2e_demo", "tiled:other_corpus"]
    src = {"source": "tiled:e2e_demo"}
    graph = c.get("/graph", params=src).json()
    assert [n["id"] for n in graph["nodes"]] == ["m:a", "m:b", "m:c"] and len(graph["edges"]) == 2
    assert graph["nodes"][0]["graph_id"] == GID and graph["nodes"][0]["type"] == "Material"
    assert graph["edges"][1] == {"source": "m:b", "target": "m:c", "predicate": "rel:processed_by"}
    found = c.post("/graph/nodes/search", params=src, json={"query": "beta", "limit": 5}).json()
    assert [r["node"]["id"] for r in found["results"]] == ["m:b"] and found["retrieval_backend"] == "lexical:source"
    assert c.get("/graph/node/m:c", params=src).json()["label"] == "Gamma"
    assert c.get("/graph/node/m:pending", params=src).status_code == 404
    hood = c.get("/graph/neighborhood/m:a", params={**src, "hops": 1}).json()
    assert [n["id"] for n in hood["nodes"]] == ["m:a", "m:b"]
    assert c.get("/graph/neighborhood/nope", params=src).status_code == 404
    assert scanner.entity_scans == 1                                            # one scan served all of the above


def test_edits_stay_refused_for_any_source_with_a_typed_error(local, sources_of):
    sources_of.attach_tiled(FakeScanner(*seed_rows()))
    c = local.client()
    before = c.get("/graph").content
    for sid in ("tiled:e2e_demo", "json:contract_v1", "tiled:unknown"):
        resp = c.patch("/graph/node/m:a", params={"source": sid}, json={"label": "changed"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "read_only_source"
    assert c.get("/graph").content == before


@pytest.mark.parametrize("sid", ["json:../../etc/passwd", "json:/etc/passwd", "tiled:http://evil.example",
                                 "tiled:nope", "x", "json:%2e%2e"])
def test_unknown_or_hostile_ids_are_404_on_every_read_route(local, sources_of, sid):
    sources_of.attach_tiled(FakeScanner(*seed_rows()))
    c = local.client()
    q = {"source": sid}
    for resp in (c.get("/graph", params=q), c.post("/graph/nodes/search", params=q, json={"query": "a"}),
                 c.get("/graph/node/m:a", params=q), c.get("/graph/neighborhood/m:a", params=q)):
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "unknown_source"


def test_tiled_outage_is_503_typed_on_reads_and_ok_in_the_listing(local, sources_of):
    scanner = FakeScanner(*seed_rows())
    scanner.error = ContractError(ErrorCode.UNAVAILABLE, "down")
    sources_of.attach_tiled(scanner)
    c = local.client()
    listing = c.get("/v1/graph/sources")
    assert listing.status_code == 200 and listing.json()["tiled"]["status"] == "unavailable"
    resp = c.get("/graph", params={"source": "tiled:e2e_demo"})
    assert resp.status_code == 503 and resp.json()["detail"]["code"] == "source_unavailable"


def test_a_source_wins_over_json_graph_path_and_json_graph_path_is_not_read(local):
    c = local.client()
    resp = c.get("/graph/node/matkg:Other", params={"source": "json:contract_v1", "json_graph_path": "/etc/passwd"})
    assert resp.status_code == 200 and resp.json()["id"] == "matkg:Other"


def test_adopted_releases_are_listed_on_local(local):
    workdir = local.app.state.f2w_agent_service.cfg.workdir
    snap = workdir / "storage_kg" / "synthetic" / "v1"
    snap.mkdir(parents=True)
    (snap / "graph.json").write_text(json.dumps({"nodes": [{"id": "r1", "name": "R1", "category": "Thing"}],
                                                 "edges": []}))
    c = local.client()
    ids = [s["id"] for s in c.get("/v1/graph/sources").json()["sources"]]
    assert "json:synthetic/v1" in ids
    assert [n["id"] for n in c.get("/graph", params={"source": "json:synthetic/v1"}).json()["nodes"]] == ["r1"]


# --------------------------------------------------------------------------- routes (ALS)

def test_als_reader_lists_json_only_and_anonymous_is_refused(als):
    assert als.client("anon").get("/v1/graph/sources").status_code == 401
    body = als.client("user").get("/v1/graph/sources").json()
    assert body["tiled"]["status"] == "not_offered"
    assert [s["id"] for s in body["sources"]] == ["json:contract_v1"]
    assert als.app.state.f2w_graph_sources.tiled_offered is False


def test_als_never_serves_a_tiled_source_or_a_release_directory(als):
    workdir = als.app.state.f2w_agent_service.cfg.workdir
    snap = workdir / "storage_kg" / "synthetic" / "v1"
    snap.mkdir(parents=True)
    (snap / "graph.json").write_text(json.dumps({"nodes": [{"id": "r1"}], "edges": []}))
    user = als.client("user")
    assert "json:synthetic/v1" not in [s["id"] for s in user.get("/v1/graph/sources").json()["sources"]]
    for sid in ("json:synthetic/v1", "tiled:e2e_demo"):
        resp = user.get("/graph", params={"source": sid})
        assert resp.status_code == 404 and resp.json()["detail"]["code"] == "unknown_source"


def test_als_non_admin_cannot_edit_and_a_source_does_not_change_that(als):
    resp = als.client("user").patch("/graph/node/matkg:TermA", params={"source": "json:contract_v1"},
                                    json={"label": "x"})
    assert resp.status_code == 403                                              # the capability check answers first


def test_the_editable_source_rule_marks_live_tiled_corpora_and_nothing_else(tmp_path):
    """Owner decision 2026-11-09 (eighteenth set): ``tiled:<corpus>`` is editable iff the corpus is live on the Save target."""
    sources = make_sources(tmp_path, FakeScanner(*seed_rows()))
    assert sources.editable(f"tiled:{GID}") is False, "no rule wired: every source is read-only"
    sources.set_editable(lambda corpus: corpus == GID)
    rows = {s["id"]: s for s in sources.listing().as_json()["sources"]}
    assert rows[f"tiled:{GID}"]["read_only"] is False
    assert rows["tiled:other_corpus"]["read_only"] is True and rows["json:one"]["read_only"] is True
    assert sources.editable(f"tiled:{GID}") is True
    assert not any(sources.editable(sid) for sid in ("json:one", "json:" + GID, "tiled:other_corpus",
                                                      f"tiled:{GID}/v1", "tiled:../x", "x", None))
    assert gs.read_only_error("json:one").detail()["code"] == gs.read_only_error("tiled:other_corpus").detail()["code"] == "read_only_source"
    assert "not a live publication" in gs.read_only_error("tiled:other_corpus").message

    def broken(_corpus: str) -> bool:
        raise RuntimeError("store down")
    sources.set_editable(broken)
    assert sources.editable(f"tiled:{GID}") is False, "a failing rule means read-only, never a listing failure"
    assert sources.listing().as_json()["sources"][1]["read_only"] is True
    sources.set_editable(None)
    assert sources.editable(f"tiled:{GID}") is False

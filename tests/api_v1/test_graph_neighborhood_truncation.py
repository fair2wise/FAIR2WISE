"""``X-F2W-Graph-Truncated`` on ``GET /graph/neighborhood/{id}``: set only when the node cap cut the neighborhood.

The body shape and status codes of the frozen legacy route are unchanged (``tests/contracts/legacy_ui``); the header is
additive. Covers the graph-source path (JSON and Tiled sources share it) and the default Tiled-lookup path.
"""
from __future__ import annotations

import pytest
from test_v1_graph_sources import FakeScanner, seed_rows

from app.modules.f2w_agent import api as api_mod
from app.modules.f2w_agent import graph_sources as gs
from app.modules.graph.backends.tiled_lookup import TiledLookupGraph

HEADER = "X-F2W-Graph-Truncated"


def _chain(length: int) -> TiledLookupGraph:
    kg = TiledLookupGraph()
    for i in range(length):
        kg.add_node({"id": f"n{i}", "name": f"N{i}", "category": "Thing"})
    for i in range(length - 1):
        kg.add_edge(f"n{i}", "rel:next", f"n{i + 1}")
    return kg


def test_source_path_flags_only_a_cut_neighborhood(local, monkeypatch):
    local.app.state.f2w_graph_sources.attach_tiled(FakeScanner(*seed_rows()))
    c = local.client()
    params = {"source": "tiled:e2e_demo", "hops": 3}                    # m:a - m:b - m:c, 3 nodes in all
    whole = c.get("/graph/neighborhood/m:a", params=params)
    assert whole.status_code == 200 and HEADER not in whole.headers and len(whole.json()["nodes"]) == 3
    monkeypatch.setattr(gs, "NEIGHBORHOOD_MAX_NODES", 3)                # exactly fitting is not a cut
    exact = c.get("/graph/neighborhood/m:a", params=params)
    assert HEADER not in exact.headers and exact.content == whole.content
    monkeypatch.setattr(gs, "NEIGHBORHOOD_MAX_NODES", 2)
    cut = c.get("/graph/neighborhood/m:a", params=params)
    assert cut.status_code == 200 and cut.headers[HEADER] == "true"
    assert [n["id"] for n in cut.json()["nodes"]] == ["m:a", "m:b"]
    near = c.get("/graph/neighborhood/m:a", params={**params, "hops": 1})   # hop limit, not the cap, ends it: no flag
    assert HEADER not in near.headers


def test_json_source_path_flags_a_cut(local, monkeypatch):
    c = local.client()
    src = {"source": "json:contract_v1", "hops": 4}
    full = c.get("/graph", params={"source": "json:contract_v1"}).json()
    start = max(full["nodes"], key=lambda n: sum(n["id"] in (e["source"], e["target"]) for e in full["edges"]))["id"]
    assert HEADER not in c.get(f"/graph/neighborhood/{start}", params=src).headers
    monkeypatch.setattr(gs, "NEIGHBORHOOD_MAX_NODES", 1)
    cut = c.get(f"/graph/neighborhood/{start}", params=src)
    assert cut.status_code == 200 and cut.headers[HEADER] == "true" and len(cut.json()["nodes"]) == 1


@pytest.fixture
def tiled_chain(local, monkeypatch):
    kg = _chain(10)
    monkeypatch.setattr(api_mod.AgentPipelineService, "ensure_tiled_lookup", lambda self, node_id: kg)
    return local.client()


def test_tiled_path_flags_only_a_cut_neighborhood(tiled_chain, monkeypatch):
    whole = tiled_chain.get("/graph/neighborhood/n0", params={"hops": 3})        # 4 nodes
    assert whole.status_code == 200 and HEADER not in whole.headers and len(whole.json()["nodes"]) == 4
    monkeypatch.setattr(api_mod, "NEIGHBORHOOD_MAX_NODES", 4)
    assert HEADER not in tiled_chain.get("/graph/neighborhood/n0", params={"hops": 3}).headers
    monkeypatch.setattr(api_mod, "NEIGHBORHOOD_MAX_NODES", 3)
    cut = tiled_chain.get("/graph/neighborhood/n0", params={"hops": 3})
    assert cut.status_code == 200 and cut.headers[HEADER] == "true" and len(cut.json()["nodes"]) == 3
    assert set(cut.json()) == set(whole.json())                                  # same body shape


def test_the_cut_flag_helpers_agree_with_the_plain_ones(monkeypatch):
    kg = _chain(10)
    payload, cut = api_mod.graph_neighborhood_and_cut(kg, "n0", 4)
    assert not cut and payload == api_mod.graph_neighborhood_from_lookup(kg, "n0", 4)
    monkeypatch.setattr(api_mod, "NEIGHBORHOOD_MAX_NODES", 2)
    assert api_mod.graph_neighborhood_and_cut(kg, "n0", 4)[1] is True

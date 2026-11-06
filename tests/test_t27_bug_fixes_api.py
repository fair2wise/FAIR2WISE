"""Regression tests for the T27 read-through bugs B1-B5 in ``f2w_agent/api.py``."""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app.modules.f2w_agent import api as api_mod
from app.modules.f2w_agent.coordinator import CoordinatorConfig
from app.modules.graph.backends.tiled_lookup import TiledLookupGraph


# B1 -------------------------------------------------------------------------------------------
def test_b1_resolve_runtime_json_graph_path_has_no_dead_flag(tmp_path):
    assert "missing_ok" not in inspect.signature(api_mod.AgentPipelineService._resolve_runtime_json_graph_path).parameters
    missing = tmp_path / "nope.json"
    empty = tmp_path / "empty.json"
    empty.write_text("")
    service = api_mod.AgentPipelineService(
        CoordinatorConfig(graph=str(missing), graphs=[str(empty)], workdir=tmp_path / "run", kg_mode="json", max_rounds=1)
    )
    for path in (missing, empty):
        with pytest.raises(FileNotFoundError):
            service._resolve_runtime_json_graph_path(str(path))
        assert service._try_resolve_runtime_json_graph_path(str(path)) is None


# B5 -------------------------------------------------------------------------------------------
def test_b5_core_node_keys_literal_has_no_duplicates():
    src = inspect.getsource(api_mod)
    block = re.search(r"_CORE_NODE_KEYS = \{(.*?)\n\}", src, re.S)
    assert block is not None
    keys = re.findall(r'"([^"]+)"', block.group(1))
    assert len(keys) == len(set(keys))


# B2 -------------------------------------------------------------------------------------------
def _chain(length: int) -> TiledLookupGraph:
    kg = TiledLookupGraph()
    for i in range(length):
        kg.add_node({"id": f"n{i}", "name": f"N{i}", "category": "Thing"})
    for i in range(length - 1):
        kg.add_edge(f"n{i}", "rel:next", f"n{i + 1}")
    return kg


def test_b2_neighborhood_honours_hops_and_clamps():
    kg = _chain(10)
    ids = lambda hops: {n.id for n in api_mod.graph_neighborhood_from_lookup(kg, "n0", hops).nodes}  # noqa: E731
    assert ids(1) == {"n0", "n1"}
    assert ids(2) == {"n0", "n1", "n2"}
    assert ids(0) == {"n0", "n1"}  # clamped up to 1
    assert ids(99) == {f"n{i}" for i in range(5)}  # clamped down to MAX_HOPS (4)
    default = {n.id for n in api_mod.graph_neighborhood_from_lookup(kg, "n0").nodes}
    assert default == {f"n{i}" for i in range(4)}  # unchanged default: CITE_FOCUS_HOPS (3)


def test_b2_neighborhood_node_cap(monkeypatch):
    monkeypatch.setattr(api_mod, "NEIGHBORHOOD_MAX_NODES", 3)
    payload = api_mod.graph_neighborhood_from_lookup(_chain(10), "n0", 4)
    assert len(payload.nodes) == 3 and payload.nodes[0].id == "n0"

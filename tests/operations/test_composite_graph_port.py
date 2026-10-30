"""D3.17: one GraphPort over the JSON graphs and a Tiled backend (``CompositeGraphPort``).

Fake Tiled client only (no server, no socket): id listing and routing, the JSON-first
collision rule, TTL and ``reload`` refresh, and an unreachable Tiled leaving the JSON graphs
intact with one recorded warning. ``from_deployment`` / ``resolve_tiled_settings`` are
checked for what they refuse (another target, a configuration error) without connecting.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO = Path(__file__).resolve().parents[2]
for extra in (REPO / "tests" / "graph",):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from graph_samples import things_payload  # noqa: E402

from app.modules.contracts import ErrorCode  # noqa: E402
from app.modules.contracts.errors import ContractError  # noqa: E402
from app.modules.deployment.tiled_config import ExternalTiledConfig, TiledSettings  # noqa: E402
from app.modules.graph import identity_adapter as ida  # noqa: E402
from app.modules.graph.backends.json import JsonGraphBackend  # noqa: E402
from app.modules.graph.backends.tiled import TiledGraphBackend  # noqa: E402
from app.modules.operations.graph import TOOL_COMPARE, TOOL_STATS, GraphOperationsTool  # noqa: E402
from app.modules.operations.knowledge import (  # noqa: E402
    CompositeGraphPort,
    DynamicJsonGraphs,
    KnowledgeOperations,
    resolve_tiled_settings,
)
from app.modules.security.authorization import DeploymentSettings  # noqa: E402
from tests.runtime.runtime_fakes import local_ctx  # noqa: E402


class FakeTiledClient:
    """Answers the ``entities``/``links`` listings of a 0.2.18 server from in-memory rows."""

    def __init__(self) -> None:
        self.entities: List[Dict[str, Any]] = []
        self.links: List[Dict[str, Any]] = []
        self.queries: List[str] = []
        self.fail: Any = None

    def add_graph(self, graph_id: str, payload: Dict[str, Any]) -> None:
        uuids: Dict[str, str] = {}
        for node in payload["things"]:
            tid = f"uuid-{graph_id}-{node['id']}"
            uuids[node["id"]] = tid
            self.entities.append({"id": tid, "name": node["name"], "entityType": node["category"],
                                  "uri": ida.entity_uri(graph_id, node["id"]),
                                  "properties": ida.entity_properties(graph_id, node["id"], node),
                                  "createdAt": "2026-01-01T00:00:00Z"})
        for n, (edge_id, edge) in enumerate(ida.assign_edge_ids(payload["associations"])):
            self.links.append({"id": f"link-{graph_id}-{n}", "predicate": edge["predicate"],
                               "subjectId": uuids.get(edge["subject"]), "objectId": uuids.get(edge["object"]),
                               "properties": ida.link_properties(graph_id, edge_id, edge)})

    def add_foreign(self) -> None:
        self.entities.append({"id": "forge-1", "name": "FORGE sample", "entityType": "Sample",
                              "uri": "forge:sample/1", "properties": {"owner": "forge"}, "createdAt": None})

    def query(self, document: str, variables: Dict[str, Any] = None) -> Dict[str, Any]:
        if self.fail is not None:
            raise self.fail
        self.queries.append(document)
        v = variables or {}
        rows = self.links if "links(" in document else self.entities
        page = rows[v["o"]:v["o"] + v["l"]]
        return {"links" if "links(" in document else "entities": page}


def _write(path: Path, payload: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def json_files(tmp_path):
    return [_write(tmp_path / "kg" / "syn_v1.json", things_payload())]


@pytest.fixture
def tiled():
    client = FakeTiledClient()
    client.add_graph("live_a", things_payload())
    client.add_graph("live_b", things_payload())
    client.add_foreign()
    return client


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _composite(json_files, client, **kw) -> CompositeGraphPort:
    backend = TiledGraphBackend(client, page_size=3) if client is not None else None
    return CompositeGraphPort(DynamicJsonGraphs(lambda: list(json_files)), backend, **kw)


def test_graph_ids_list_json_first_then_discovered_tiled_graphs(json_files, tiled):
    clock = Clock()
    port = _composite(json_files, tiled, clock=clock)
    assert port.graph_ids() == ["syn_v1", "live_a", "live_b"]  # FORGE rows never name a graph
    assert port.capabilities("syn_v1").backend == "json"
    assert port.capabilities("live_b").backend == "tiled"
    assert port.capabilities().backend == "json"
    assert port.resolve("syn_v1") is port.json_port
    assert isinstance(port.resolve("live_a"), TiledGraphBackend)
    with pytest.raises(ContractError) as err:
        port.resolve("nope")
    assert err.value.code is ErrorCode.NOT_FOUND
    assert all("{ uri properties }" in q for q in tiled.queries)  # discovery reads only identity fields
    assert port.warnings == ()


def test_discovery_is_cached_for_the_ttl_and_refreshed_by_reload(json_files, tiled):
    clock = Clock()
    port = _composite(json_files, tiled, ttl=30.0, clock=clock)
    port.graph_ids()
    n = len(tiled.queries)
    tiled.add_graph("live_c", things_payload())
    assert port.graph_ids() == ["syn_v1", "live_a", "live_b"] and len(tiled.queries) == n  # cached
    clock.now += 31
    assert port.graph_ids() == ["syn_v1", "live_a", "live_b", "live_c"] and len(tiled.queries) > n
    tiled.add_graph("live_d", things_payload())
    port.reload()
    assert "live_d" in port.graph_ids()


def test_operations_route_each_graph_id_to_its_backend(json_files, tiled):
    tool = GraphOperationsTool(_composite(json_files, tiled))
    ctx = local_ctx()
    json_report = tool.compute(ctx, TOOL_STATS, {"graph_id": "syn_v1"})
    tiled_report = tool.compute(ctx, TOOL_STATS, {"graph_id": "live_a"})
    assert json_report.coverage.exactness == "snapshot" and json_report.source_revisions["syn_v1"].startswith("sha256:")
    assert tiled_report.coverage.exactness == "live_unisolated"
    assert tiled_report.source_revisions["live_a"].startswith("tiled-live:live_a@")
    assert tiled_report.metrics["entities"] == json_report.metrics["entities"]  # same payload, two backends
    compare = tool.compute(ctx, TOOL_COMPARE, {"graph_ids": ["syn_v1", "live_b"]})
    assert compare.metrics and sorted(compare.source_revisions) == ["live_b", "syn_v1"]
    with pytest.raises(ContractError) as err:
        tool.compute(ctx, TOOL_STATS, {"graph_id": "live_zzz"})
    assert err.value.code is ErrorCode.NOT_FOUND


def test_json_graph_shadows_a_tiled_graph_of_the_same_id(json_files, tiled):
    tiled.add_graph("syn_v1", things_payload())
    port = _composite(json_files, tiled)
    assert port.graph_ids() == ["syn_v1", "live_a", "live_b"]
    assert port.resolve("syn_v1") is port.json_port
    assert len(port.warnings) == 1 and "shadowed" in port.warnings[0] and "syn_v1" in port.warnings[0]
    port.graph_ids()
    assert len(port.warnings) == 1  # recorded once


def test_unreachable_tiled_leaves_the_json_graphs_working_with_one_warning(json_files, tiled):
    tiled.fail = ContractError(ErrorCode.UNAVAILABLE, "Tiled graph at http://127.0.0.1:1 is unavailable")
    clock = Clock()
    port = _composite(json_files, tiled, ttl=10.0, clock=clock)
    tool = GraphOperationsTool(port)
    assert port.graph_ids() == ["syn_v1"]
    assert port.graph_ids() == ["syn_v1"]
    assert len(port.warnings) == 1 and "unavailable" in port.warnings[0].lower()
    assert tool.compute(local_ctx(), TOOL_STATS, {"graph_id": "syn_v1"}).metrics["entities"] > 0
    with pytest.raises(ContractError) as err:
        tool.compute(local_ctx(), TOOL_STATS, {"graph_id": "live_a"})
    assert err.value.code is ErrorCode.NOT_FOUND
    tiled.fail = None  # the server comes back: the next refresh lists it again
    clock.now += 11
    assert port.graph_ids() == ["syn_v1", "live_a", "live_b"]


def test_factory_returning_none_or_raising_keeps_json_only(json_files):
    port = CompositeGraphPort(DynamicJsonGraphs(lambda: list(json_files)), lambda: None)
    assert port.graph_ids() == ["syn_v1"] and len(port.warnings) == 1
    assert "not listed" in port.warnings[0]

    def boom():
        raise OSError("manifest unreadable")

    port = CompositeGraphPort(JsonGraphBackend(list(json_files)), boom)
    assert port.graph_ids() == ["syn_v1"]
    assert port.warnings == ("Tiled graph backend unavailable (OSError): manifest unreadable",)
    assert CompositeGraphPort(JsonGraphBackend(list(json_files))).graph_ids() == ["syn_v1"]


def test_discovery_cap_lists_what_was_seen_and_warns(json_files, tiled):
    port = _composite(json_files, tiled, max_scan=4)
    ids = port.graph_ids()
    assert ids[0] == "syn_v1" and "live_a" in ids
    assert any("discovery stopped" in w for w in port.warnings)


def _local(target: str = "http://127.0.0.1:59871") -> DeploymentSettings:
    return DeploymentSettings(profile="local", writer_identity="f2w-writer", tiled_target=target)


def test_resolve_tiled_settings_refuses_another_target_and_config_errors(tmp_path):
    settings = _local()
    external = TiledSettings(profile="test", mode="external",
                             external=ExternalTiledConfig(profile="test", url="http://127.0.0.1:59871"))
    tiled, reason = resolve_tiled_settings(settings, external)
    assert tiled is None and "profile" in reason  # settings for another profile are not used
    als = DeploymentSettings.for_disposable_test_target("als-dev", tiled_target="http://127.0.0.1:59872",
                                                        writer_identity="f2w-writer")
    tiled, reason = resolve_tiled_settings(als, {})  # external default is the ALS URL, not the rehearsal target
    assert tiled is None and "not the deployment's" in reason
    tiled, reason = resolve_tiled_settings(settings, {"F2W_TILED_URL": "http://127.0.0.1:59871"})
    assert tiled is None and "not configured" in reason  # F2W_TILED_URL is refused in managed mode
    tiled, reason = resolve_tiled_settings(settings, {"F2W_TILED_DATA_DIR": str(tmp_path),
                                                      "F2W_TILED_PYTHON": sys.executable})
    assert reason is None and tiled.mode == "managed" and tiled.managed.data_dir == tmp_path


def test_from_deployment_serves_json_when_no_managed_server_runs(tmp_path, monkeypatch):
    _write(tmp_path / "storage" / "kg" / "syn_v1.json", things_payload())
    knowledge = KnowledgeOperations.from_deployment(
        _local(), {"F2W_TILED_DATA_DIR": str(tmp_path / "tiled"), "F2W_TILED_PYTHON": sys.executable}, tmp_path)
    port = knowledge.graph.port
    assert isinstance(port, CompositeGraphPort)
    assert port.graph_ids() == ["syn_v1"]  # no manifest under the data dir: nothing is contacted
    assert port.warnings == ("no Tiled graph server is reachable; Tiled graphs are not listed",)
    assert knowledge.compute(local_ctx(), TOOL_STATS, {"graph_id": "syn_v1"}).metrics["entities"] > 0
    assert knowledge.corpus is not None
    assert not any(hasattr(knowledge, name) for name in ("apply_plan", "update_entity_properties", "delete_owned"))

    broken = KnowledgeOperations.from_deployment(_local(), {"F2W_TILED_URL": "http://127.0.0.1:59871"}, tmp_path)
    assert broken.graph.port.graph_ids() == ["syn_v1"]
    assert any("not configured" in w for w in broken.graph.port.warnings)

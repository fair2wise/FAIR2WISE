"""D3.17: ``create_api_app`` serves the deployment's Tiled graphs by default (managed server).

Skips unless ``F2W_TILED_PYTHON`` names an isolated tiled[server]==0.2.18 interpreter. A
disposable managed server is seeded and the trusted startup environment points the ``:local``
profile at its data dir; nothing else is passed, so the default
``KnowledgeOperations.from_deployment`` wiring locates the server by its manifest and serves
``/v1/operations/statistics`` for a Tiled graph beside the harness's JSON graph. The legacy
UI bundle (``build_knowledge_operations``) gets the same backend from the same configuration.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from graph_samples import nodes_payload, seed_graph, things_payload
from tiled_server import start_tiled_instance, tiled_python

from app.modules.graph.backends.tiled import TiledGraphBackend
from app.modules.graph.backends.tiled_client import TiledGraphClient
from app.modules.operations.graph import TOOL_STATS, compute_statistics
from app.modules.operations.knowledge import CompositeGraphPort

REPO = Path(__file__).resolve().parents[2]
for extra in (REPO / "tests" / "api_v1", REPO / "tests" / "security_live", REPO / "tests" / "contracts" / "legacy_ui"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import _legacy_ui_harness as H  # noqa: E402
from _v1_support import build_local  # noqa: E402
from live_support import loopback_only  # noqa: E402

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")

from app.modules.graph.snapshots import graph_id_for_snapshot  # noqa: E402

JSON_GRAPH_ID = graph_id_for_snapshot(Path(H.GRAPH_FILENAME))  # the harness's storage/kg graph


@pytest.fixture(scope="module")
def seeded(tiled_base):
    inst = start_tiled_instance(tiled_base)
    try:
        seed_graph(inst, "rsoxs_v4", things_payload())
        seed_graph(inst, "rsoxs_v3", nodes_payload())
        yield inst
    finally:
        inst.destroy()


def _point_environment_at(monkeypatch, inst) -> None:
    """The trusted startup values the ``:local`` image carries, bound to the test instance's data dir."""
    monkeypatch.setenv("F2W_DEPLOYMENT_PROFILE", "local")
    monkeypatch.setenv("F2W_TILED_DATA_DIR", str(inst.root))
    monkeypatch.setenv("F2W_TILED_PYTHON", str(tiled_python()))
    monkeypatch.delenv("F2W_TILED_MODE", raising=False)
    monkeypatch.delenv("F2W_TILED_URL", raising=False)


def test_default_wiring_serves_tiled_graphs_beside_the_json_graphs(monkeypatch, tmp_path, seeded):
    _point_environment_at(monkeypatch, seeded)
    live = build_local(monkeypatch, tmp_path, after_install=loopback_only)  # no knowledge_operations=
    port = live.ops.knowledge.graph.port
    assert isinstance(port, CompositeGraphPort)
    ids = port.graph_ids()
    assert ids[0] == JSON_GRAPH_ID and set(ids[1:]) == {"rsoxs_v3", "rsoxs_v4"}  # manifest located the server
    assert port.warnings == ()
    client = port.tiled_backend().client
    assert callable(client._credential) and "Apikey" not in repr(client)  # read per request, never shown
    c = live.client()
    stats = c.get("/v1/operations/statistics", params={"graph_id": "rsoxs_v4"})
    assert stats.status_code == 200, stats.text
    body = stats.json()
    direct = compute_statistics(TiledGraphBackend(TiledGraphClient.from_managed(seeded.server)),
                                live.ops.cli_context(), "rsoxs_v4")
    assert body["status"] == "ok" and body["data"]["metrics"] == direct.metrics
    assert body["data"]["metrics"]["entities"] == 4  # the FORGE seed sentinel never counts
    assert body["data"]["coverage"]["exactness"] == "live_unisolated"
    assert body["data"]["source_revisions"]["rsoxs_v4"].startswith("tiled-live:")
    json_stats = c.get("/v1/operations/statistics", params={"graph_id": JSON_GRAPH_ID})
    assert json_stats.status_code == 200, json_stats.text
    assert json_stats.json()["data"]["source_revisions"][JSON_GRAPH_ID].startswith("sha256:")
    compare = c.get("/v1/operations/compare", params={"graph_ids": [JSON_GRAPH_ID, "rsoxs_v4"]})
    assert compare.status_code == 200, compare.text
    missing = c.get("/v1/operations/statistics", params={"graph_id": "rsoxs_v9"})
    assert missing.status_code == 404, missing.text
    assert "FORGE" not in stats.text


def test_default_wiring_without_a_running_server_serves_json_only(monkeypatch, tmp_path, tiled_base):
    inst = start_tiled_instance(tiled_base, seed=False)
    try:
        seed_graph(inst, "rsoxs_v4", things_payload())
        _point_environment_at(monkeypatch, inst)
        live = build_local(monkeypatch, tmp_path, after_install=loopback_only)
        port = live.ops.knowledge.graph.port
        assert "rsoxs_v4" in port.graph_ids()
        inst.stop()
        port.reload()  # manifest says stopped: the factory finds no server, JSON stays served
        assert port.graph_ids() == [JSON_GRAPH_ID]
        assert port.warnings == ("no Tiled graph server is reachable; Tiled graphs are not listed",)
        c = live.client()
        assert c.get("/v1/operations/statistics", params={"graph_id": JSON_GRAPH_ID}).status_code == 200
        assert c.get("/v1/operations/statistics", params={"graph_id": "rsoxs_v4"}).status_code == 404
        inst.restart()
        port.reload()
        assert "rsoxs_v4" in port.graph_ids()  # the restarted server (new port) is located again
        assert c.get("/v1/operations/statistics", params={"graph_id": "rsoxs_v4"}).status_code == 200
    finally:
        inst.destroy()


def test_legacy_ui_bundle_gets_the_same_tiled_backend(monkeypatch, tmp_path, seeded):
    from app.modules.runtime_bridge.legacy_adapter import build_knowledge_operations

    _point_environment_at(monkeypatch, seeded)
    monkeypatch.chdir(tmp_path)
    runtime = SimpleNamespace(graph_source="json", json_graph_paths=[], tiled_uri="http://127.0.0.1:1")
    pipeline = SimpleNamespace(runtime=runtime, _runtime_json_paths=lambda: list(runtime.json_graph_paths))
    knowledge = build_knowledge_operations(pipeline)
    port = knowledge.graph.port
    assert isinstance(port, CompositeGraphPort)
    assert port.graph_ids() == ["rsoxs_v3", "rsoxs_v4"]  # the UI's tiled_uri is not the target; the config is
    assert port.tiled_backend().client.base_url == seeded.url
    report = knowledge.compute(knowledge_ctx(knowledge), TOOL_STATS, {"graph_id": "rsoxs_v4"})
    assert report.metrics["entities"] == 4
    monkeypatch.delenv("F2W_TILED_DATA_DIR")
    monkeypatch.setenv("F2W_TILED_URL", "http://127.0.0.1:1")  # refused in managed mode: JSON only
    assert build_knowledge_operations(pipeline).graph.port.graph_ids() == []


def knowledge_ctx(knowledge):
    from tests.runtime.runtime_fakes import local_ctx

    return local_ctx()

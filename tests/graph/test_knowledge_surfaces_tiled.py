"""T19E: statistics and recent entities on a disposable Tiled 0.2.18 server through every surface.

Skips unless ``F2W_TILED_PYTHON`` names an isolated tiled[server]==0.2.18 interpreter. One
application-owned loopback server is seeded (with FORGE records that must never count); the
same ``KnowledgeOperations`` over its ``TiledGraphBackend`` serves the agent Tool, the
supervisor route, ``/v1/operations``, the SDK and the job runner. Every surface returns the
direct computation's figures, flags the live read as unisolated, and keeps a capped scan
partial. (The CLI builds JSON ports only; Tiled reaches it through the API or SDK.)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from graph_samples import nodes_payload, seed_graph, things_payload
from langchain_core.messages import AIMessage
from tiled_server import start_tiled_instance, tiled_python

from app.modules.application import F2WApplication
from app.modules.contracts import ToolStatus
from app.modules.contracts.jobs import JobState
from app.modules.graph import identity_adapter as ida
from app.modules.graph.backends.tiled import TiledGraphBackend
from app.modules.graph.backends.tiled_client import TiledGraphClient
from app.modules.operations.graph import (
    TOOL_RECENT,
    TOOL_STATS,
    GraphOperationsTool,
    compute_statistics,
    recent_entities,
)
from app.modules.operations.knowledge import KnowledgeOperations
from app.modules.runtime_bridge.retrieval import LegacyRetrievalService
from tests.read_path.test_read_tool import SUCCESS, StubAgent
from tests.runtime.runtime_fakes import ScriptedChatModel, tool_call

REPO = Path(__file__).resolve().parents[2]
for extra in (REPO / "tests" / "api_v1", REPO / "tests" / "security_live"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from _v1_support import build_local  # noqa: E402
from live_support import loopback_only  # noqa: E402

from app import public  # noqa: E402

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")


@pytest.fixture(scope="module")
def seeded(tiled_base):
    inst = start_tiled_instance(tiled_base)
    try:
        sentinel = inst.seed_receipt["sentinel_id"]
        ids = seed_graph(inst, "rsoxs_v4", things_payload())
        seed_graph(inst, "rsoxs_v3", nodes_payload())
        inst.graphql("mutation($i: CreateLinkInput!) { createLink(input: $i) { id } }",
                     {"i": {"subjectId": ids["matkg:P3HT"], "predicate": "forge:usedIn", "objectId": sentinel,
                            "properties": {"owner": "forge"}}})
        inst.graphql("mutation($i: CreateEntityInput!) { createEntity(input: $i) { id } }",
                     {"i": {"entityType": "Material", "name": "P3HT",
                            "uri": ida.entity_uri("rsoxs_v4", "matkg:Fake"),
                            "properties": {"f2w_graph_id": "rsoxs_v4", "f2w_id": "matkg:Fake", "owner": "forge"}}})
        yield inst
    finally:
        inst.destroy()


def _tiled(inst, **kw):
    return TiledGraphBackend(TiledGraphClient.from_managed(inst.server), **kw)


def _knowledge(inst, **kw):
    return KnowledgeOperations(GraphOperationsTool(_tiled(inst, **kw.pop("backend", {})), **kw))


def _comparable(result):
    """A tool result minus what differs per call: provenance id, stamps, the live scan's start time."""
    out = dict(result)
    out.pop("provenance_id", None)
    data = dict(out.get("data") or {})
    for key in ("started_at", "finished_at"):
        data.pop(key, None)
    data["source_revisions"] = {g: r.split("@", 1)[0] for g, r in (data.get("source_revisions") or {}).items()}
    out["data"] = data
    return out


def _relay(messages) -> AIMessage:
    body = json.loads(messages[-1].content)
    metrics = (body.get("data") or {}).get("metrics")
    return AIMessage(content=json.dumps({"status": body["status"], "metrics": metrics}))


@pytest.fixture
def live(monkeypatch, tmp_path, seeded):
    return build_local(monkeypatch, tmp_path, knowledge_operations=_knowledge(seeded), after_install=loopback_only)


def test_statistics_and_recent_through_the_routes_match_the_direct_computation(live, seeded):
    c = live.client()
    ctx = live.ops.cli_context()
    stats = c.get("/v1/operations/statistics", params={"graph_id": "rsoxs_v4"})
    assert stats.status_code == 200, stats.text
    body = stats.json()
    direct = compute_statistics(_tiled(seeded), ctx, "rsoxs_v4")
    assert body["status"] == "ok" and body["data"]["metrics"] == direct.metrics
    assert body["data"]["metrics"]["entities"] == 4  # FORGE sentinel and the unmarked record never count
    assert body["data"]["coverage"]["exactness"] == "live_unisolated"
    assert body["warnings"][0].startswith("live read:")
    assert body["data"]["source_revisions"]["rsoxs_v4"].startswith("tiled-live:")
    recent = c.get("/v1/operations/recent", params={"graph_id": "rsoxs_v4", "limit": 2})
    assert recent.status_code == 200, recent.text
    rdata = recent.json()["data"]
    expected = recent_entities(_tiled(seeded), ctx, "rsoxs_v4", limit=2)
    assert rdata["metrics"] == expected.metrics and rdata["metrics"]["entities_created"] == 4
    assert len(rdata["preview"]["entities"]) == 2 and rdata["display_truncated"]  # preview cut, totals whole
    later = c.get("/v1/operations/recent", params={"graph_id": "rsoxs_v4", "since": "2999-01-01T00:00:00Z"}).json()
    assert later["data"]["metrics"]["entities_created"] == 0 and later["status"] == "ok"
    assert "FORGE" not in stats.text and "FORGE" not in recent.text


def test_sdk_agent_and_supervisor_agree_with_the_routes_on_tiled(live, seeded):
    c = live.client()
    http_stats = c.get("/v1/operations/statistics", params={"graph_id": "rsoxs_v4"}).json()
    http_recent = c.get("/v1/operations/recent", params={"graph_id": "rsoxs_v4", "limit": 3}).json()
    app = F2WApplication(settings=live.settings, retrieval=LegacyRetrievalService(StubAgent(SUCCESS)),
                         chat_model=ScriptedChatModel(script=[
                             tool_call(TOOL_STATS, {"graph_id": "rsoxs_v4"}, "s1"), _relay,
                             tool_call(TOOL_RECENT, {"graph_id": "rsoxs_v4", "limit": 3}, "r1"), _relay]),
                         knowledge=live.ops.knowledge)
    with public.F2WClient(app, owns_application=True, operations=live.ops) as client:
        ctx = live.ops.cli_context()
        sdk_stats = client.graph_statistics(ctx, "rsoxs_v4").model_dump(mode="json")
        sdk_recent = client.recent_entities(ctx, "rsoxs_v4", limit=3).model_dump(mode="json")
        assert _comparable(sdk_stats) == _comparable(http_stats)
        assert _comparable(sdk_recent) == _comparable(http_recent)
        actx = app.local_context()
        tool_stats = asyncio.run(app.executor.aexecute(actx, TOOL_STATS, {"graph_id": "rsoxs_v4"}))
        assert _comparable(tool_stats.model_dump(mode="json")) == _comparable(http_stats)
        planned = asyncio.run(app.delegate(actx, "how big is the live graph?", thread_id="t1"))
        assert _comparable(planned.tool_results[0].model_dump(mode="json")) == _comparable(http_stats)
        planned_recent = asyncio.run(app.delegate(actx, "what was added recently?", thread_id="t2"))
        assert _comparable(planned_recent.tool_results[0].model_dump(mode="json")) == _comparable(http_recent)
        assert json.loads(planned_recent.final_text)["metrics"]["entities_created"] == 4


def test_capped_live_scan_is_partial_on_the_routes_and_complete_as_a_job(monkeypatch, tmp_path, seeded):
    capped = build_local(monkeypatch, tmp_path, after_install=loopback_only,
                         knowledge_operations=_knowledge(seeded, max_records=4, backend={"page_size": 3}))
    c = capped.client()
    inline = c.get("/v1/operations/statistics", params={"graph_id": "rsoxs_v4"}).json()
    assert inline["status"] == "partial" and inline["data"]["coverage"]["completeness"] == "partial"
    assert any("relations were not read" in r for r in inline["data"]["coverage"]["reasons"])
    job = c.post("/v1/operations/jobs", json={"operation": TOOL_STATS, "args": {"graph_id": "rsoxs_v4"}}).json()
    assert capped.work() == (job["job_id"], JobState.SUCCEEDED)
    result = c.get(f"/v1/operations/jobs/{job['job_id']}").json()["result"]
    assert result["coverage"]["completeness"] == "complete"
    assert result["coverage"]["exactness"] == "live_unisolated"
    assert result["metrics"] == compute_statistics(_tiled(seeded), capped.ops.cli_context(), "rsoxs_v4").metrics
    job2 = c.post("/v1/operations/jobs", json={"operation": TOOL_RECENT, "args": {"graph_id": "rsoxs_v4"}}).json()
    assert capped.work() == (job2["job_id"], JobState.SUCCEEDED)
    recent = c.get(f"/v1/operations/jobs/{job2['job_id']}").json()["result"]
    assert recent["metrics"]["entities_created"] == 4 and recent["coverage"]["completeness"] == "complete"
    assert ToolStatus(inline["status"]) is ToolStatus.PARTIAL

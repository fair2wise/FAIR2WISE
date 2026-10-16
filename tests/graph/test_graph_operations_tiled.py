"""T19C: graph operations on a disposable Tiled 0.2.18 server agree with the JSON backend.

Skips unless ``F2W_TILED_PYTHON`` names an isolated tiled[server]==0.2.18 interpreter. The
seed includes a FORGE sentinel, a FORGE link from a literature entity and a forged
literature URI without the owner marker; none may be counted.
"""
import json

import pytest
from graph_contexts import make_ctx
from graph_samples import nodes_payload, seed_graph, things_payload, write
from tiled_server import start_tiled_instance, tiled_python

from app.modules.agent_runtime.executor import RuntimeLimits, ToolExecutor
from app.modules.agent_runtime.policies import InMemoryEventSink
from app.modules.contracts import ToolStatus
from app.modules.graph import identity_adapter as ida
from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.graph.backends.tiled import TiledGraphBackend
from app.modules.graph.backends.tiled_client import TiledGraphClient
from app.modules.operations.graph import (
    TOOL_STATS,
    GraphOperationsTool,
    compare_snapshots,
    compute_statistics,
    paper_subgraph,
    recent_entities,
)
from app.modules.operations.reports import ScanControl
from app.modules.tools.bootstrap import build_registry

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")


def _without_dangling(payload):
    """The seeder skips edges to missing nodes; compare against the same population in JSON."""
    nodes = payload.get("things") or payload.get("nodes")
    ids = {n["id"] for n in nodes}
    key = "associations" if "associations" in payload else "edges"
    return dict(payload, **{key: [e for e in payload[key] if e["subject"] in ids and e["object"] in ids]})


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
                     {"i": {"entityType": "Material", "name": "P3HT", "uri": ida.entity_uri("rsoxs_v4", "matkg:Fake"),
                            "properties": {"f2w_graph_id": "rsoxs_v4", "f2w_id": "matkg:Fake", "owner": "forge"}}})
        yield inst
    finally:
        inst.destroy()


def _tiled(inst, **kw):
    return TiledGraphBackend(TiledGraphClient.from_managed(inst.server), **kw)


@pytest.fixture
def json_backend(tmp_path):
    return JsonGraphBackend({"rsoxs_v4": write(tmp_path / "v4.json", _without_dangling(things_payload())),
                             "rsoxs_v3": write(tmp_path / "v3.json", nodes_payload())})


def test_statistics_match_json_and_exclude_forge_records(seeded, json_backend):
    ctx = make_ctx()
    live = compute_statistics(_tiled(seeded), ctx, "rsoxs_v4")
    frozen = compute_statistics(json_backend, ctx, "rsoxs_v4")
    assert live.metrics == frozen.metrics
    assert live.metrics["entities"] == 4  # matkg:Fake (no owner marker) and the FORGE sentinel are not counted
    assert live.coverage.completeness == "complete" and live.coverage.exactness == "live_unisolated"
    assert live.source_revisions["rsoxs_v4"].startswith("tiled-live:")
    v3 = compute_statistics(_tiled(seeded), ctx, "rsoxs_v3").metrics
    assert v3 == compute_statistics(json_backend, ctx, "rsoxs_v3").metrics


def test_live_result_warns_that_it_is_unisolated(seeded):
    executor = ToolExecutor(build_registry(None, include=(), extra=GraphOperationsTool(_tiled(seeded)).registrations(),
                                           name="t19c-tiled", freeze=True),
                            event_sink=InMemoryEventSink(), limits=RuntimeLimits(max_retries=0))
    result = executor.execute(make_ctx().model_copy(update={"capabilities": frozenset({"chat.ask"})}), TOOL_STATS, {"graph_id": "rsoxs_v4"})
    assert result.status is ToolStatus.OK and result.warnings[0].startswith("live read:")


def test_capped_tiled_scan_is_partial_not_complete(seeded):
    report = compute_statistics(_tiled(seeded, page_size=3), make_ctx(), "rsoxs_v4", ScanControl(4))
    assert report.coverage.completeness == "partial"
    assert any("relations were not read" in r for r in report.coverage.reasons)


def test_growth_and_paper_subgraph_match_json(seeded, json_backend):
    ctx = make_ctx()
    live = compare_snapshots(_tiled(seeded), ctx, ["rsoxs_v3", "rsoxs_v4"]).metrics
    assert live == compare_snapshots(json_backend, ctx, ["rsoxs_v3", "rsoxs_v4"]).metrics
    sub = paper_subgraph(_tiled(seeded), ctx, "rsoxs_v4", "paper_a.pdf")
    assert sub.metrics == paper_subgraph(json_backend, ctx, "rsoxs_v4", "paper_a.pdf").metrics


def test_recent_entities_uses_tiled_creation_times(seeded):
    report = recent_entities(_tiled(seeded), make_ctx(), "rsoxs_v4", limit=2)
    assert report.metrics["entities_created"] == report.metrics["entities_total"] == 4
    assert len(report.preview["entities"]) == 2 and report.display_truncated
    later = recent_entities(_tiled(seeded), make_ctx(), "rsoxs_v4", since="2999-01-01T00:00:00Z")
    assert later.metrics["entities_created"] == 0 and later.coverage.completeness == "complete"
    assert "FORGE" not in json.dumps(report.to_data())

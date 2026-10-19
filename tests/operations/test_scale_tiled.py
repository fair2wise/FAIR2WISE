"""T19D: whole-population graph operations on a disposable Tiled 0.2.18 server, in parity with frozen JSON.

Skips unless ``F2W_TILED_PYTHON`` names an isolated tiled[server]==0.2.18 interpreter. One
application-owned test server (``tests/fixtures/tiled_server.py``, loopback, own storage,
destroyed afterwards) is seeded with two versions of a synthetic graph of 15 000 and 6 000
literature entities (18x the legacy 800-row Tiled listing cap) plus FORGE-owned records
that must never count. The seeder batches GraphQL mutations (500 per request) with the
identity mapping T08B uses; it is a test helper, not the importer.

Parity: every operation that both backends support returns the same metrics from the live
Tiled scan as from the frozen JSON snapshot of the same population, equal to the
builder's ground truth; ``kg_recent_entities`` (Tiled only) counts every entity while its
preview is cut. A capped live scan is partial.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "fixtures"))

from tiled_server import start_tiled_instance, tiled_base, tiled_python  # noqa: E402,F401

from app.modules.agent_runtime.executor import RuntimeLimits, ToolExecutor  # noqa: E402
from app.modules.agent_runtime.policies import InMemoryEventSink  # noqa: E402
from app.modules.contracts import ToolStatus  # noqa: E402
from app.modules.graph import identity_adapter as ida  # noqa: E402
from app.modules.graph.backends.json import JsonGraphBackend  # noqa: E402
from app.modules.graph.backends.tiled import TiledGraphBackend  # noqa: E402
from app.modules.graph.backends.tiled_client import TiledGraphClient  # noqa: E402
from app.modules.operations.graph import (  # noqa: E402
    TOOL_STATS,
    GraphOperationsTool,
    compare_snapshots,
    compute_statistics,
    paper_subgraph,
    recent_entities,
)
from app.modules.operations.reports import MAX_RESULT_CHARS, ScanControl  # noqa: E402
from app.modules.tools.bootstrap import build_registry  # noqa: E402
from tests.operations import scale_population as sp  # noqa: E402
from tests.operations.scale_timing import measure  # noqa: E402
from tests.runtime.runtime_fakes import local_ctx  # noqa: E402

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")

BATCH = 500
UNBOUNDED = 10_000_000
LEGACY_TILED_CAP = 800  # tiled_graph._page_entities: MAX_PAGES=8 x PAGE_SIZE=100


def _clean(payload, keep_entities=None):
    """The population a Tiled graph can hold: complete triples between existing nodes only."""
    things = payload["things"][:keep_entities] if keep_entities else payload["things"]
    ids = {t["id"] for t in things}
    assoc = [a for a in payload["associations"]
             if a.get("subject") in ids and a.get("object") in ids and a.get("predicate")]
    return {"things": things, "associations": assoc}


def _batched(instance, kind, rows):
    """Create ``rows`` (``CreateEntityInput``/``CreateLinkInput`` dicts) with aliased mutations; return ids."""
    field, input_type = ("createEntity", "CreateEntityInput") if kind == "entity" else ("createLink", "CreateLinkInput")
    ids = []
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        decl = ", ".join(f"$i{j}: {input_type}!" for j in range(len(chunk)))
        body = " ".join(f"r{j}: {field}(input: $i{j}) {{ id }}" for j in range(len(chunk)))
        data = instance.graphql(f"mutation({decl}) {{ {body} }}", {f"i{j}": row for j, row in enumerate(chunk)})
        ids += [data[f"r{j}"]["id"] for j in range(len(chunk))]
    return ids


def _seed(instance, graph_id, payload):
    nodes = payload["things"]
    rows = [{"entityType": n["category"], "name": n["name"], "uri": ida.entity_uri(graph_id, n["id"]),
             "properties": ida.entity_properties(graph_id, n["id"], n)} for n in nodes]
    tiled_ids = dict(zip((n["id"] for n in nodes), _batched(instance, "entity", rows)))
    links = [{"subjectId": tiled_ids[e["subject"]], "predicate": e["predicate"], "objectId": tiled_ids[e["object"]],
              "properties": ida.link_properties(graph_id, eid, e)} for eid, e in ida.assign_edge_ids(payload["associations"])]
    _batched(instance, "link", links)
    return tiled_ids


@pytest.fixture(scope="module")
def live(tiled_base, tmp_path_factory):
    payload, _truth = sp.build_graph(n_entities=15_000, n_works=600, n_edges=22_000, seed=sp.SEED + 7)
    full = _clean(payload)
    older = _clean(payload, keep_entities=6_000)
    root = tmp_path_factory.mktemp("t19d-tiled-json")
    full_path = sp.write_json(root / "t2.json", full)
    frozen = JsonGraphBackend({"syn_t2": full_path, "syn_t1": sp.write_json(root / "t1.json", older)})
    inst = start_tiled_instance(tiled_base, seed=False)
    try:
        def seed_all():
            ids = _seed(inst, "syn_t2", full)
            _seed(inst, "syn_t1", older)
            forge = _batched(inst, "entity", [{"entityType": "ESAF", "name": f"FORGE record {i}",
                                               "uri": f"forge:t19d/{i}", "properties": {"owner": "forge"}}
                                              for i in range(300)])
            _batched(inst, "link", [{"subjectId": ids[full["things"][i]["id"]], "predicate": "forge:usedIn",
                                     "objectId": forge[i], "properties": {"owner": "forge"}} for i in range(300)])
            return len(forge) * 2

        forge_rows = measure("tiled/seed", seed_all,
                             population=f"{len(full['things']) + len(older['things'])} entities, "
                                        f"{len(full['associations']) + len(older['associations'])} links, 600 FORGE rows")
        yield {"inst": inst, "frozen": frozen, "full": full, "older": older, "forge_rows": forge_rows,
               "budget_multiple": sp.prompt_budget_multiple(full_path)}
    finally:
        inst.destroy()


def _tiled(live, **kw):
    return TiledGraphBackend(TiledGraphClient.from_managed(live["inst"].server), **kw)


def test_live_statistics_equal_frozen_json_over_the_whole_graph(live):
    ctx = local_ctx()
    report = measure("kg_compute_statistics/tiled", lambda: compute_statistics(
        _tiled(live), ctx, "syn_t2", ScanControl(UNBOUNDED)), population="15k entities, ~22k links, live paging")
    frozen = compute_statistics(live["frozen"], ctx, "syn_t2", ScanControl(UNBOUNDED))
    assert report.metrics == frozen.metrics
    full = live["full"]
    assert report.metrics["entities"] == len(full["things"]) > 18 * LEGACY_TILED_CAP
    assert report.metrics["edge_records"] == len(full["associations"])
    assert report.coverage.completeness == "complete" and report.coverage.exactness == "live_unisolated"
    assert live["budget_multiple"] > 2  # the live graph alone would fill two 1M-token contexts
    older = live["older"]
    every_row = (len(full["things"]) + len(full["associations"]) + len(older["things"]) +
                 len(older["associations"]) + live["forge_rows"])
    assert report.coverage.records_scanned == every_row  # complete paging reads every row, then filters


def test_live_tool_result_is_bounded_and_marked_unisolated(live):
    registry = build_registry(None, include=(), extra=GraphOperationsTool(_tiled(live), max_records=UNBOUNDED)
                              .registrations(), name="t19d-tiled", freeze=True)
    executor = ToolExecutor(registry, event_sink=InMemoryEventSink(), limits=RuntimeLimits(max_retries=0))
    result = executor.execute(local_ctx(), TOOL_STATS, {"graph_id": "syn_t2"})
    assert result.status is ToolStatus.OK and result.warnings[0].startswith("live read:")
    body = json.dumps(result.data, sort_keys=True)
    assert len(body) <= MAX_RESULT_CHARS and sp.leaked(body) == [] and "FORGE" not in body


def test_live_growth_and_subgraph_equal_frozen_json(live):
    ctx = local_ctx()
    grow = measure("kg_compare_snapshots/tiled", lambda: compare_snapshots(
        _tiled(live), ctx, ["syn_t1", "syn_t2"], ScanControl(UNBOUNDED)))
    assert grow.metrics == compare_snapshots(live["frozen"], ctx, ["syn_t1", "syn_t2"], ScanControl(UNBOUNDED)).metrics
    assert grow.metrics["steps"][1]["legacy_growth"]["new_entities"] == 9_000
    key = live["full"]["things"][0]["source_papers"][0].rsplit("/", 1)[-1]
    sub = measure("kg_paper_subgraph/tiled", lambda: paper_subgraph(
        _tiled(live), ctx, "syn_t2", key, max_nodes=3, control=ScanControl(UNBOUNDED)))
    assert sub.metrics == paper_subgraph(live["frozen"], ctx, "syn_t2", key, max_nodes=3,
                                         control=ScanControl(UNBOUNDED)).metrics
    assert sub.display_truncated and sub.metrics["entities"] > 3 and sub.completeness == "complete"


def test_recent_entities_count_every_live_entity_while_the_preview_is_cut(live):
    report = measure("kg_recent_entities/tiled", lambda: recent_entities(
        _tiled(live), local_ctx(), "syn_t2", limit=25, control=ScanControl(UNBOUNDED)))
    n = len(live["full"]["things"])
    assert report.metrics["entities_created"] == report.metrics["entities_total"] == n > LEGACY_TILED_CAP
    assert len(report.preview["entities"]) == 25 and report.display_truncated
    assert report.coverage.completeness == "complete"


@pytest.mark.parametrize("bound", [LEGACY_TILED_CAP, 20_000])
def test_capped_live_scan_is_partial(live, bound):
    report = compute_statistics(_tiled(live, page_size=250), local_ctx(), "syn_t2", ScanControl(bound))
    assert report.coverage.completeness == "partial" and report.coverage.records_scanned == bound
    assert any(f"scan bound of {bound}" in r for r in report.coverage.reasons)
    if bound < len(live["full"]["things"]):
        assert any("relations were not read" in r for r in report.coverage.reasons)
        assert report.metrics["relations"] == 0

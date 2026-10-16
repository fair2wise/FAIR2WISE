"""T19C OP03 / K-10 / RT-07 / RT-08: graph operations over the JSON GraphPort (Tiled: tests/graph)."""

from __future__ import annotations

import hashlib
import json
import socket
import sys
from pathlib import Path

import pytest

from app.modules.agent_runtime.executor import RuntimeLimits, ToolExecutor
from app.modules.agent_runtime.policies import InMemoryEventSink
from app.modules.contracts import Effect, ErrorCode, ToolStatus
from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.operations.graph import (
    ANALYZE_KNOWLEDGE_GRAPH_SKILL,
    GRAPH_SPECS,
    TOOL_COMPARE,
    TOOL_RECENT,
    TOOL_STATS,
    TOOL_SUBGRAPH,
    GraphOperationsTool,
    compare_snapshots,
    compute_statistics,
    legacy_growth_metrics,
    paper_subgraph,
    recent_entities,
    scan_graph,
    triples,
)
from app.modules.operations.reports import MAX_RESULT_CHARS, ScanControl
from app.modules.tools.bootstrap import build_registry
from tests.runtime.runtime_fakes import als_ctx, local_ctx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests" / "graph"))

import analyze_kgs  # noqa: E402
from graph_samples import nodes_payload, things_payload, write  # noqa: E402

KG = REPO / "storage" / "kg"
SERIES = ["matkg_deepseek-r1_70b_25_20250925_103657.json", "matkg_deepseek-r1_70b_50_20250926_144206.json",
          "matkg_deepseek-r1_70b_75_20250927_214641.json", "matkg_deepseek-r1_70b_100_20250929_004942.json"]
CANARY = "SYNTHETIC-DESCRIPTION-CANARY-5521"


def _payload():
    """things_payload plus duplicates, an incomplete edge, a self loop, a repeated Work and a canary description."""
    data = things_payload()
    data["things"][0]["description"] = CANARY
    data["things"].append({"id": "matkg:Film", "name": "Film", "category": "Material",
                           "source_papers": ["/abs/dir/paper_a.pdf", "10.1021_x.1.pdf"],
                           "context_snippets": [{"text": CANARY}]})
    data["things"].append({"id": "matkg:Blend", "name": "Blend", "category": "Material",
                           "source_papers": ["2306.07295v1.pdf"]})
    data["associations"] += [
        {"subject": "matkg:P3HT", "predicate": "rel:STUDIED_BY", "object": "matkg:RSoXS"},  # duplicate triple
        {"subject": "matkg:Film", "predicate": "", "object": "matkg:P3HT"},  # incomplete
        {"subject": "matkg:Film", "predicate": "rel:SAME_AS", "object": "matkg:Film"},  # self loop
        {"subject": "matkg:Film", "predicate": "rel:PART_OF", "object": "matkg:P3HT"},
    ]
    return data


@pytest.fixture
def backend(tmp_path):
    return JsonGraphBackend({"syn_v2": write(tmp_path / "syn_v2.json", _payload()),
                             "syn_v1": write(tmp_path / "syn_v1.json", things_payload()),
                             "nodes_v3": write(tmp_path / "nodes_v3.json", nodes_payload()),
                             "empty": write(tmp_path / "empty.json", {"things": [], "associations": []})})


def _executor(tool):
    registry = build_registry(None, include=(), extra=tool.registrations(), name="t19c", freeze=True)
    return ToolExecutor(registry, event_sink=InMemoryEventSink(), limits=RuntimeLimits(max_retries=0))


# -- statistics ------------------------------------------------------------------------------------

def test_statistics_counting_units_duplicates_dangling_and_works(backend):
    report = compute_statistics(backend, local_ctx(), "syn_v2")
    m = report.metrics
    assert (m["entities"], m["edge_records"], m["relations"]) == (6, 7, 5)
    assert (m["duplicate_edge_records"], m["incomplete_edge_records"], m["dangling_relations"]) == (1, 1, 1)
    assert m["self_loop_relations"] == 1 and m["isolated_entities"] == 2 and m["unique_predicates"] == 5
    assert m["paper_keys"] == 4 and m["distinct_works"] == 4  # paper_a, paper_b, 10.1021/x.1, arXiv 2306.07295
    assert m["entities_with_citations"] == 5
    assert set(report.counting_units) >= {"entities", "relations", "distinct_works", "legacy"}
    assert report.coverage.completeness == "complete" and report.coverage.exactness == "snapshot"
    assert report.source_revisions["syn_v2"].startswith("sha256:")
    assert report.preview["dangling_relations"] == [["matkg:OPV", "rel:RELATED_TO", "matkg:Ghost"]]


def test_statistics_never_echo_descriptions_snippets_or_paths(backend, tmp_path):
    body = json.dumps(compute_statistics(backend, local_ctx(), "syn_v2").to_data())
    assert CANARY not in body and "/abs/" not in body and str(tmp_path) not in body


def test_empty_graph_is_a_complete_zero_population(backend):
    report = compute_statistics(backend, local_ctx(), "empty")
    assert report.coverage.completeness == "complete" and report.metrics["entities"] == 0
    assert report.metrics["legacy"] == {"entity_relation_ratio": 0, "entities_with_relations_pct": 0,
                                        "avg_relations_per_entity": 0, "unique_predicates": 0}
    assert any("no entities" in w for w in report.warnings)


def test_nodes_edges_family_is_read(backend):
    m = compute_statistics(backend, local_ctx(), "nodes_v3").metrics
    assert (m["entities"], m["edge_records"], m["relations"], m["duplicate_edge_records"]) == (2, 2, 1, 1)


def test_capped_scan_is_partial_never_complete(backend):
    report = compute_statistics(backend, local_ctx(), "syn_v2", ScanControl(4))
    assert report.coverage.completeness == "partial" and report.coverage.records_scanned == 4
    assert any("scan bound of 4" in r for r in report.coverage.reasons)


@pytest.mark.parametrize("name", SERIES + ["matkg_bl1101_v1.json", "matkg_xray_papers_cborg_chat.json",
                                           "matkg_google_gemini-flash-lite_25_20260603_182319.json"])
def test_legacy_block_matches_analyze_kgs_on_committed_graphs(name):
    path = KG / name
    entities, relations, _ = analyze_kgs.load_kg(str(path))
    backend = JsonGraphBackend({"g": path})
    scan = scan_graph(backend, local_ctx(), "g")
    assert set(scan.entities) == entities and triples(scan)[0] == relations
    report = compute_statistics(backend, local_ctx(), "g")
    assert report.metrics["legacy"] == analyze_kgs.calculate_structural_metrics(entities, relations)
    assert (report.metrics["entities"], report.metrics["relations"]) == (len(entities), len(relations))


def test_cli_is_unchanged():
    inventory = json.loads((REPO / "docs" / "migration" / "operations_inventory.json").read_text(encoding="utf-8"))
    items = inventory["items"] if isinstance(inventory, dict) else inventory
    (op03,) = [item for item in items if item.get("id") == "OP03"]
    assert hashlib.sha256((REPO / "scripts" / "analyze_kgs.py").read_bytes()).hexdigest() == op03["sha256"]


# -- growth (RT-07) --------------------------------------------------------------------------------

def test_growth_series_matches_analyze_kgs_growth_with_work_denominators():
    backend = JsonGraphBackend({f"s{i}": KG / name for i, name in enumerate(SERIES)})
    ids = [f"s{i}" for i in range(len(SERIES))]
    report = compare_snapshots(backend, local_ctx(), ids)
    steps = report.metrics["steps"]
    assert [s["graph_id"] for s in steps] == ids and report.coverage.completeness == "complete"
    prev = None
    for name, step in zip(SERIES, steps):
        ents, rels, _ = analyze_kgs.load_kg(str(KG / name))
        prev_e, prev_r, prev_w = prev or (set(), set(), 0)
        expected = analyze_kgs.calculate_growth_metrics(ents, rels, prev_e, prev_r, step["distinct_works"], prev_w)
        assert step["legacy_growth"] == expected
        assert legacy_growth_metrics(ents, rels, prev_e, prev_r, 50, 25) == \
            analyze_kgs.calculate_growth_metrics(ents, rels, prev_e, prev_r, 50, 25)
        prev = (ents, rels, step["distinct_works"])
    assert "file name" in report.counting_units["legacy_growth"]


def test_growth_counts_added_and_removed(backend):
    report = compare_snapshots(backend, local_ctx(), ["syn_v1", "syn_v2"])
    step = report.metrics["steps"][1]
    assert step["legacy_growth"]["new_entities"] == 2 and step["removed_entities"] == 0
    assert step["new_works"] == 2 and step["removed_works"] == 0 and step["legacy_growth"]["entity_retention"] == 100.0
    other = compare_snapshots(backend, local_ctx(), ["nodes_v3", "syn_v1"])
    assert any("shares no node id" in w for w in other.warnings)


def test_growth_stops_partial_and_lists_unread_graphs(backend):
    report = compare_snapshots(backend, local_ctx(), ["syn_v1", "syn_v2", "nodes_v3"], ScanControl(9))
    assert report.coverage.completeness == "partial"
    assert any("graphs not read" in r for r in report.coverage.reasons)


# -- paper subgraph (K-10) -------------------------------------------------------------------------

def test_paper_subgraph_by_key_doi_and_arxiv(backend):
    report = paper_subgraph(backend, local_ctx(), "syn_v2", "paper_a.pdf")
    m = report.metrics
    assert m["work_id"] == "doi:10.1/a" and m["entities"] == 3  # P3HT, RSoXS, Film (cited by path)
    assert m["internal_relations"] == 3 and m["boundary_relations"] == 1 and m["neighbour_entities"] == 1  # RSoXS -> OPV
    assert report.preview["nodes"][0][0] in {"matkg:P3HT", "matkg:Film"}
    assert paper_subgraph(backend, local_ctx(), "syn_v2", "10.1/A").metrics["work_id"] == "doi:10.1/a"
    arxiv = paper_subgraph(backend, local_ctx(), "syn_v2", "arXiv:2306.07295").metrics
    assert arxiv["work_id"] == "arxiv:2306.07295" and arxiv["entities"] == 1
    capped = paper_subgraph(backend, local_ctx(), "syn_v2", "paper_a.pdf", max_nodes=1, max_edges=1)
    assert capped.display_truncated and capped.metrics == m and len(capped.preview["nodes"]) == 1


def test_unknown_paper_is_not_found_but_a_partial_scan_is_unknown(backend):
    with pytest.raises(Exception) as err:
        paper_subgraph(backend, local_ctx(), "syn_v2", "10.9/none")
    assert err.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(Exception) as err:
        paper_subgraph(backend, local_ctx(), "syn_v2", "10.9/none", control=ScanControl(2))
    assert err.value.code is ErrorCode.UNAVAILABLE


# -- recent (RT-08) --------------------------------------------------------------------------------

def test_recent_entities_is_unsupported_on_json_snapshots(backend):
    with pytest.raises(Exception) as err:
        recent_entities(backend, local_ctx(), "syn_v2")
    assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


# -- Tools -----------------------------------------------------------------------------------------

def test_specs_are_read_only_and_take_no_paths():
    assert [s.name for s in GRAPH_SPECS] == [TOOL_STATS, TOOL_COMPARE, TOOL_SUBGRAPH, TOOL_RECENT]
    for spec in GRAPH_SPECS:
        assert spec.effects == frozenset({Effect.READ_CORPUS}) and spec.idempotent
        assert spec.required_capabilities == frozenset({"chat.ask"}) and spec.input_schema["additionalProperties"] is False
        assert "path" not in json.dumps(spec.input_schema) and "file" not in json.dumps(spec.input_schema)
    assert ANALYZE_KNOWLEDGE_GRAPH_SKILL.tools == tuple(s.name for s in GRAPH_SPECS)


def test_tools_through_the_executor(backend, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "socket", no_network)
    executor = _executor(GraphOperationsTool(backend))
    ok = executor.execute(als_ctx(), TOOL_STATS, {"graph_id": "syn_v2"})
    assert ok.status is ToolStatus.OK and ok.data["metrics"]["relations"] == 5 and ok.data["model_calls"] == 0
    assert len(json.dumps(ok.data, sort_keys=True)) <= MAX_RESULT_CHARS
    grow = executor.execute(als_ctx(), TOOL_COMPARE, {"graph_ids": ["syn_v1", "syn_v2"]})
    assert grow.status is ToolStatus.OK and len(grow.data["metrics"]["steps"]) == 2
    sub = executor.execute(als_ctx(), TOOL_SUBGRAPH, {"graph_id": "syn_v2", "paper": "paper_b.pdf"})
    assert sub.status is ToolStatus.OK and sub.data["metrics"]["entities"] == 2
    recent = executor.execute(als_ctx(), TOOL_RECENT, {"graph_id": "syn_v2"})
    assert recent.status is ToolStatus.ERROR and recent.error_code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_tool_partial_scope_unknown_and_invalid(backend):
    partial = _executor(GraphOperationsTool(backend, max_records=3)).execute(local_ctx(), TOOL_STATS,
                                                                             {"graph_id": "syn_v2"})
    assert partial.status is ToolStatus.PARTIAL and partial.warnings[0].startswith("partial:")
    executor = _executor(GraphOperationsTool(backend, graph_ids=["syn_v1", "syn_v2"]))
    assert executor.execute(local_ctx(), TOOL_STATS, {"graph_id": "nodes_v3"}).error_code is ErrorCode.NOT_FOUND
    denied = executor.execute(als_ctx(corpus_scope=frozenset({"syn_v1"})), TOOL_STATS, {"graph_id": "syn_v2"})
    assert denied.status is ToolStatus.DENIED
    no_chat = executor.execute(als_ctx(capabilities=frozenset({"chat.read_support"})), TOOL_STATS,
                               {"graph_id": "syn_v1"})
    assert no_chat.status is ToolStatus.DENIED
    for name, args in [(TOOL_STATS, {"graph_id": "../x"}), (TOOL_STATS, {"graph_id": "syn_v1", "path": "x"}),
                       (TOOL_COMPARE, {"graph_ids": ["syn_v1"]}), (TOOL_SUBGRAPH, {"graph_id": "syn_v1"}),
                       (TOOL_SUBGRAPH, {"graph_id": "syn_v1", "paper": "x" * 301})]:
        assert executor.execute(local_ctx(), name, args).status is ToolStatus.INVALID, (name, args)


def test_backend_is_never_written(backend, tmp_path):
    before = {p.name: p.read_bytes() for p in tmp_path.glob("*.json")}
    executor = _executor(GraphOperationsTool(backend))
    for name, args in [(TOOL_STATS, {"graph_id": "syn_v2"}), (TOOL_COMPARE, {"graph_ids": ["syn_v1", "syn_v2"]}),
                       (TOOL_SUBGRAPH, {"graph_id": "syn_v2", "paper": "paper_a.pdf"})]:
        executor.execute(local_ctx(), name, args)
    assert {p.name: p.read_bytes() for p in tmp_path.glob("*.json")} == before

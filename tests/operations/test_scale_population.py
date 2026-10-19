"""T19D: every Knowledge Operation computes its totals over a population far larger than a prompt.

The populations (``scale_population``) are 10-12 times larger than a 1M-token model context
when serialised. Each operation runs over the whole population with code and no model, its
totals equal the ground truth computed independently by the fixture builder, its result
stays within the 7000-character inline bound, and it finishes within a generous wall-clock
and memory bound. The timings are recorded (``F2W_T19D_TIMINGS``) for the H4 evidence.

OPS-01 (complete-population count) and OPS-04 (bounded output, totals unchanged) bind here.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from app.modules.agent_runtime.executor import RuntimeLimits, ToolExecutor
from app.modules.agent_runtime.policies import InMemoryEventSink
from app.modules.contracts import ToolStatus
from app.modules.f2w_agent import paper_evidence_agent as pea
from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.literature.store import FileDocumentStore
from app.modules.operations.corpus import CorpusAuditTool, CorpusSource, audit_corpus, mapping_corpus_resolver
from app.modules.operations.graph import (
    HISTOGRAM_TOP,
    TOOL_STATS,
    TOOL_SUBGRAPH,
    GraphOperationsTool,
    compare_snapshots,
    compute_statistics,
    paper_subgraph,
)
from app.modules.operations.papers import TOOL_NAME as TOOL_SUMMARY
from app.modules.operations.papers import PaperScope, PaperSummaryTool, pinned_paper_scope, summarize_paper_terms
from app.modules.operations.reports import MAX_RESULT_CHARS, ScanControl, top_items
from app.modules.tools.bootstrap import build_registry
from tests.operations import scale_population as sp
from tests.operations.scale_timing import measure
from tests.runtime.runtime_fakes import local_ctx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import analyze_kgs  # noqa: E402

#: Wall-clock bound per operation over a 60 000-entity population (measured: a few seconds).
MAX_SECONDS = 90.0
#: Bound on the resident memory one operation adds to its process (measured: a few hundred MiB).
MAX_HELD_MIB = 2048.0
UNBOUNDED = 10_000_000


@pytest.fixture(autouse=True)
def _no_model_no_network(monkeypatch):
    """No operation may construct a chat client, call a model or open a socket."""
    from app.modules.term_extractor import clients

    def refuse(*a, **k):
        raise AssertionError("a Knowledge Operation called a model or the network")

    monkeypatch.setattr(clients, "make_chat_client", refuse)
    monkeypatch.setattr(pea.PaperEvidenceAgent, "_chat", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _backend(pop):
    return JsonGraphBackend({"syn_big": pop.path})


def _executor(*registrations):
    registry = build_registry(None, include=(), extra=list(registrations), name="t19d-scale", freeze=True)
    return ToolExecutor(registry, event_sink=InMemoryEventSink(), limits=RuntimeLimits(max_retries=0))


def _bounded(data):
    body = json.dumps(data, sort_keys=True, default=str)
    assert len(body) <= MAX_RESULT_CHARS
    return body


# -- the population exceeds any prompt -------------------------------------------------------------

def test_populations_are_far_larger_than_a_model_context(big_graph, big_corpus, big_session):
    assert big_graph.truth.entities >= 50_000 and big_graph.truth.edge_records >= 150_000
    assert big_corpus.truth.term_entries >= 50_000 and big_session.truth.term_count >= 40_000
    for pop in (big_graph, big_corpus):
        assert pop.budget_multiple > 5, pop.path.name  # > 5 x a 1M-token context at 4 characters/token
    assert sp.prompt_budget_multiple(big_session.path) > 2


# -- graph statistics (kg_compute_statistics) ------------------------------------------------------

def test_graph_statistics_equal_ground_truth_over_the_whole_graph(big_graph):
    truth = big_graph.truth
    report = measure("kg_compute_statistics/json", lambda: compute_statistics(
        _backend(big_graph), local_ctx(), "syn_big", ScanControl(UNBOUNDED)), population="60k entities, 191k edges")
    m = report.metrics
    for name in ("entities", "edge_records", "relations", "incomplete_edge_records", "duplicate_edge_records",
                 "dangling_relations", "self_loop_relations", "unique_predicates", "entities_with_relations",
                 "isolated_entities", "category_count", "paper_keys", "distinct_works", "entities_with_citations"):
        assert m[name] == getattr(truth, name), name
    assert m["entities_by_category"] == dict(top_items(truth.entities_by_category, HISTOGRAM_TOP))
    assert m["relations_by_predicate"] == dict(top_items(truth.relations_by_predicate, HISTOGRAM_TOP))
    entities, relations, _ = analyze_kgs.load_kg(str(big_graph.path))
    assert m["legacy"] == analyze_kgs.calculate_structural_metrics(entities, relations)
    cov = report.coverage
    assert cov.completeness == "complete" and cov.records_scanned == truth.entities + truth.edge_records
    assert report.model_calls == 0 and report.source_revisions["syn_big"].startswith("sha256:")


def test_statistics_tool_result_is_bounded_and_complete(big_graph):
    executor = _executor(*GraphOperationsTool(_backend(big_graph)).registrations())
    result = measure("kg_compute_statistics/tool", lambda: executor.execute(local_ctx(), TOOL_STATS,
                                                                           {"graph_id": "syn_big"}))
    assert result.status is ToolStatus.OK
    body = _bounded(result.data)
    assert result.data["metrics"]["entities"] == big_graph.truth.entities
    assert len(body) * 1000 < big_graph.path.stat().st_size  # the model sees < 0.1% of the population
    assert sp.leaked(body + json.dumps(result.warnings)) == []


# -- per-Work subgraph (kg_paper_subgraph) ---------------------------------------------------------

def _largest_works(truth, n=3):
    return sorted(truth.work_members, key=lambda w: (-len(truth.work_members[w]), w))[:n]


@pytest.mark.parametrize("rank", range(3))
def test_paper_subgraph_totals_equal_ground_truth_while_the_preview_is_cut(big_graph, rank):
    truth = big_graph.truth
    work = _largest_works(truth)[rank]
    ref = truth.work_keys[work][-1]  # cite the Work by its last-seen reference key (any key names the Work)
    report = measure(f"kg_paper_subgraph/json[{rank}]", lambda: paper_subgraph(
        _backend(big_graph), local_ctx(), "syn_big", ref, max_nodes=5, max_edges=5, control=ScanControl(UNBOUNDED)))
    expected = truth.subgraph(work)
    assert {k: report.metrics[k] for k in expected} == expected
    assert len(report.preview["nodes"]) == 5 < report.metrics["entities"]
    assert report.display_truncated and report.coverage.completeness == "complete"


# -- growth between two large versions (kg_compare_snapshots) --------------------------------------

def test_growth_between_two_large_versions_equals_set_arithmetic(big_graph, tmp_path):
    payload = json.loads(big_graph.path.read_text(encoding="utf-8"))
    v1 = dict(payload, things=payload["things"][5_000:], associations=payload["associations"][20_000:])
    extra = [{"id": f"syn:N{i:05d}", "name": f"New {i}", "category": "Category00",
              "source_papers": [f"10.5555_new.{i % 300}.pdf"]} for i in range(7_000)]
    v2 = dict(payload, things=payload["things"] + extra)
    backend = JsonGraphBackend({"v1": sp.write_json(tmp_path / "v1.json", v1),
                                "v2": sp.write_json(tmp_path / "v2.json", v2)})
    report = measure("kg_compare_snapshots/json", lambda: compare_snapshots(
        backend, local_ctx(), ["v1", "v2"], ScanControl(UNBOUNDED)), population="2 versions x ~60k entities")

    def sets(p):
        ids = {t["id"] for t in p["things"]}
        rel = {(a["subject"], a["predicate"], a["object"]) for a in p["associations"]
               if a.get("subject") and a.get("predicate") and a.get("object")}
        return ids, rel

    (e1, r1), (e2, r2) = sets(v1), sets(v2)
    first, second = report.metrics["steps"]
    assert (first["entities"], first["relations"], second["entities"], second["relations"]) == \
        (len(e1), len(r1), len(e2), len(r2))
    assert second["removed_entities"] == len(e1 - e2) == 0 and second["removed_relations"] == len(r1 - r2) == 0
    assert second["legacy_growth"]["new_entities"] == len(e2 - e1) == 12_000
    assert second["legacy_growth"]["new_relations"] == len(r2 - r1)
    only_dropped = sum(1 for members in big_graph.truth.work_members.values()
                       if all(int(m.rsplit("E", 1)[1]) < 5_000 for m in members))
    assert second["new_works"] == 300 + only_dropped and report.coverage.completeness == "complete"
    _bounded(report.to_data())


# -- corpus audit (curate_audit_corpus) ------------------------------------------------------------

def test_corpus_audit_equals_ground_truth_over_every_term_entry(big_corpus):
    truth = big_corpus.truth
    report = measure("curate_audit_corpus/json", lambda: audit_corpus(
        CorpusSource("syn_big", str(big_corpus.path)), control=ScanControl(UNBOUNDED)),
        population="60k term entries, 2000 paper keys, 1500 Works")
    dup, summary = report.metrics["duplicate_works"], report.metrics["summary"]
    assert dup["distinct_works"] == summary["distinct_works"] == truth.distinct_works
    assert dup["paper_keys"] == truth.paper_keys and dup["works_with_multiple_keys"] == truth.works_with_multiple_keys
    assert dup["legacy"]["arxiv_journal_near_dupes"] == 250  # every arXiv_-named preprint pairs with its journal
    assert summary["legacy"]["total_entities"] == truth.term_entries
    assert summary["legacy"]["total_papers"] == truth.legacy_total_papers  # legacy unit: full filename strings
    assert sum(summary["legacy"]["papers_per_year"].values()) == truth.legacy_year_increments
    assert summary["papers_per_year_v2"] == truth.works_per_year
    assert sum(summary["papers_per_year_v2"].values()) == truth.distinct_works
    cov = report.coverage
    assert cov.completeness == "complete" and cov.records_scanned == cov.records_expected == truth.term_entries


def test_corpus_tool_result_is_bounded_and_complete(big_corpus, tmp_path):
    tool = CorpusAuditTool(mapping_corpus_resolver({"syn_big": str(big_corpus.path)}),
                           artifact_store=FileDocumentStore(tmp_path / "artifacts"))
    result = measure("curate_audit_corpus/tool", lambda: _executor(tool.registration()).execute(
        local_ctx(), "curate_audit_corpus", {"corpus": "syn_big"}))
    assert result.status is ToolStatus.OK and len(result.artifact_refs) == 1
    body = _bounded(result.data)
    assert result.data["metrics"]["summary"]["distinct_works"] == big_corpus.truth.distinct_works
    assert sp.leaked(body + json.dumps(result.warnings)) == []


# -- paper summary (lit_summarize_extracted_terms) -------------------------------------------------

def test_paper_summary_counts_every_page_grounded_record(big_session):
    truth = big_session.truth
    scope = PaperScope(str(big_session.path), str(big_session.extra[0]), sp.ACTIVE)
    outcome = measure("lit_summarize_extracted_terms/json", lambda: summarize_paper_terms(scope),
                      population="120k session records, 41.5k for the active paper")
    s = outcome.summary
    assert s.term_count == outcome.legacy["term_count"] == truth.term_count
    assert s.displayed_term_count == 16 and s.omitted_term_count == truth.term_count - 16
    assert s.completeness == "partial" and s.scan_complete  # the scan is complete; only the display is capped
    assert s.summary_text.startswith(f"Extracted {truth.term_count} page-grounded terms")
    assert {p for p in s.used_pages} <= set(truth.eligible_pages)


def test_paper_summary_tool_result_is_bounded(big_session):
    tool = PaperSummaryTool()
    scope = PaperScope(str(big_session.path), str(big_session.extra[0]), sp.ACTIVE)
    with pinned_paper_scope(scope):
        result = measure("lit_summarize_extracted_terms/tool",
                         lambda: _executor(tool.registration()).execute(local_ctx(), TOOL_SUMMARY, {}))
    assert result.status is ToolStatus.OK and result.data["term_count"] == big_session.truth.term_count
    _bounded(result.data)
    assert len(result.evidence) == len(result.data["citations"]) <= 16


# -- time and memory -------------------------------------------------------------------------------

def _rss(op, *args):
    """Run ``op`` in a fresh interpreter (``scale_rss``) and return its JSON line."""
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG") if k in os.environ}
    proc = subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("scale_rss.py")), op, *map(str, args)],
                          cwd=REPO, env=env, capture_output=True, text=True,
                          timeout=600, check=False)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_every_operation_stays_within_time_and_memory_bounds(big_graph, big_corpus, big_session, timings):
    ref = big_graph.truth.work_keys[_largest_works(big_graph.truth)[0]][-1]
    runs = [
        ("kg_compute_statistics", (big_graph.path,), big_graph.truth.entities),
        ("kg_paper_subgraph", (big_graph.path, ref), len(big_graph.truth.work_members[_largest_works(big_graph.truth)[0]])),
        ("curate_audit_corpus", (big_corpus.path,), big_corpus.truth.distinct_works),
        ("lit_summarize_extracted_terms", (big_session.path, big_session.extra[0], sp.ACTIVE),
         big_session.truth.term_count),
    ]
    for op, args, expected in runs:
        row = _rss(op, *args)
        assert row["total"] == expected, row
        held = row["peak_mib"] - row["baseline_mib"]
        timings.setdefault(f"{op}/rss", {}).update(seconds=row["seconds"], baseline_mib=row["baseline_mib"],
                                                     peak_rss_mib=row["peak_mib"], held_mib=round(held, 1))
        assert held < MAX_HELD_MIB and row["seconds"] < MAX_SECONDS, row
    for name, row in timings.items():
        assert row.get("seconds", 0) < MAX_SECONDS, (name, row)

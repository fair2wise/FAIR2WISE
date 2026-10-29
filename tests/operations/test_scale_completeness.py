"""T19D: totals are never top-k samples; truncation never changes a total; a capped scan is partial.

On the large populations (``scale_population``):

* OPS-04: shortening the display (preview caps, smaller result bounds, ``max_nodes``,
  ``max_terms``) never changes a metric, ``coverage`` or the completeness flag, and the
  preview lists are visibly shorter than the totals they illustrate;
* OPS-03: an inline scan bound or deadline below the population size makes the result
  ``partial`` with ``ToolStatus.PARTIAL`` (never ``OK``), the exact number of records read
  and the reason; the counts are lower bounds; a Work missing from a partial scan is
  ``UNAVAILABLE``, not ``NOT_FOUND``;
* the same operation run as a job (5M-record bound) covers the whole population that the
  inline bound could not, and stores the full report as a private artifact.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.modules import llm_admission
from app.modules.agent_runtime.executor import RuntimeLimits, ToolExecutor
from app.modules.agent_runtime.policies import InMemoryEventSink
from app.modules.contracts import ContractError, ErrorCode, ToolStatus
from app.modules.contracts.jobs import JobState
from app.modules.graph.backends.json import JsonGraphBackend
from tests.jobs.jobs_fakes import local_settings
from app.modules.jobs.keys import OperationInputs
from app.modules.jobs.service import JobService
from app.modules.jobs.store import JobStore
from app.modules.literature.store import FileDocumentStore
from app.modules.operations.corpus import CorpusAuditTool, CorpusSource, audit_corpus, mapping_corpus_resolver
from app.modules.operations.graph import (
    TOOL_STATS,
    TOOL_SUBGRAPH,
    GraphOperationsTool,
    compute_statistics,
    paper_subgraph,
)
from app.modules.operations.papers import PaperScope, summarize_paper_terms
from app.modules.operations.reports import (
    MAX_PREVIEW_ITEMS,
    OPERATION_JOB_KIND,
    ScanControl,
    fit_report,
    operation_dispatch,
    register_operation_jobs,
    report_to_result,
)
from app.modules.tools.bootstrap import build_registry
from tests.operations import scale_population as sp
from tests.runtime.runtime_fakes import local_ctx

UNBOUNDED = 10_000_000


def _backend(pop):
    return JsonGraphBackend({"syn_big": pop.path})


def _executor(*registrations):
    registry = build_registry(None, include=(), extra=list(registrations), name="t19d-complete", freeze=True)
    return ToolExecutor(registry, event_sink=InMemoryEventSink(), limits=RuntimeLimits(max_retries=0))


@pytest.fixture(scope="module")
def stats(big_graph):
    return compute_statistics(_backend(big_graph), local_ctx(), "syn_big", ScanControl(UNBOUNDED))


# -- OPS-04: display truncation never changes a total -----------------------------------------------

@pytest.mark.parametrize("limit", [7000, 5000, 4000])
def test_smaller_result_bounds_shorten_previews_only(stats, big_graph, limit):
    wide = stats.model_copy(update={"preview": {
        "dangling_relations": [list(t) for t in sorted(big_graph.truth.triples)[:5000]],
        "examples": {"entities": sorted(big_graph.truth.entity_ids)[:5000]}}})
    fitted = fit_report(wide, limit)
    assert fitted.metrics == stats.metrics and fitted.coverage == stats.coverage
    assert fitted.completeness == "complete" and fitted.display_truncated
    assert len(json.dumps(fitted.to_data(), sort_keys=True)) <= limit
    shown = len(fitted.preview.get("dangling_relations", []))
    assert shown <= MAX_PREVIEW_ITEMS < big_graph.truth.relations
    assert any(w.startswith("display truncated") for w in fitted.warnings)


def test_preview_examples_are_not_the_totals(stats, big_graph):
    assert len(stats.preview["dangling_relations"]) == 10 < stats.metrics["dangling_relations"] == 400
    hist = stats.metrics["entities_by_category"]
    assert len(hist) == 25 < stats.metrics["category_count"] == big_graph.truth.category_count == 40
    assert sum(hist.values()) < stats.metrics["entities"]  # a top-25 histogram is not the population
    assert "top 25" in stats.counting_units["entities_by_category"]


@pytest.mark.parametrize("max_nodes", [1, 200])
def test_subgraph_node_cap_changes_the_preview_only(big_graph, max_nodes):
    truth = big_graph.truth
    work = max(truth.work_members, key=lambda w: (len(truth.work_members[w]), -w))
    report = paper_subgraph(_backend(big_graph), local_ctx(), "syn_big", truth.work_keys[work][0],
                            max_nodes=max_nodes, max_edges=1, control=ScanControl(UNBOUNDED))
    assert report.metrics["entities"] == len(truth.work_members[work])
    assert len(report.preview["nodes"]) == min(max_nodes, len(truth.work_members[work]))
    assert report.display_truncated is (max_nodes < len(truth.work_members[work]) or
                                        report.metrics["internal_relations"] > 1)
    assert report.completeness == "complete"


@pytest.mark.parametrize("max_terms", [1, 6, 16])
def test_summary_display_cap_never_changes_term_count(big_session, max_terms):
    scope = PaperScope(str(big_session.path), str(big_session.extra[0]), sp.ACTIVE)
    s = summarize_paper_terms(scope, max_terms=max_terms).summary
    assert s.term_count == big_session.truth.term_count and s.displayed_term_count == max_terms
    assert s.omitted_term_count == big_session.truth.term_count - max_terms
    assert s.scan_complete and s.completeness == "partial" and "display capped" in s.completeness_reason


def test_query_scoped_summary_keeps_the_full_count(big_session):
    scope = PaperScope(str(big_session.path), str(big_session.extra[0]), sp.ACTIVE)
    s = summarize_paper_terms(scope, max_terms=4, query="Kind3").summary
    assert s.term_count == big_session.truth.term_count  # relevance ranking never shrinks the population
    assert s.relevance_mode == "matched" and s.completeness == "partial"
    assert s.omitted_term_count == s.term_count - s.displayed_term_count
    assert s.legacy_omitted_note_count < s.omitted_term_count  # the legacy line counts matches only


# -- OPS-03: a capped or stopped scan is partial ---------------------------------------------------

@pytest.mark.parametrize("bound", [1, 60_000, 250_000])
def test_inline_bound_below_the_population_is_partial(big_graph, bound):
    truth = big_graph.truth
    total = truth.entities + truth.edge_records
    report = compute_statistics(_backend(big_graph), local_ctx(), "syn_big", ScanControl(bound))
    cov = report.coverage
    assert cov.records_scanned == bound and cov.completeness == "partial" and not cov.scan_complete
    assert any(f"scan bound of {bound}" in r for r in cov.reasons)
    assert report.metrics["entities"] <= truth.entities and report.metrics["relations"] <= truth.relations
    assert bound < total
    result = report_to_result(TOOL_STATS, report)
    assert result.status is ToolStatus.PARTIAL and result.warnings[0].startswith("partial:")
    assert "lower bounds" in result.warnings[0]


def test_bound_equal_to_the_population_is_complete(big_graph):
    truth = big_graph.truth
    report = compute_statistics(_backend(big_graph), local_ctx(), "syn_big",
                                ScanControl(truth.entities + truth.edge_records))
    assert report.coverage.completeness == "complete" and report.metrics["relations"] == truth.relations


def test_inline_tool_bound_is_partial_never_ok(big_graph):
    tool = GraphOperationsTool(_backend(big_graph), max_records=100_000)
    result = _executor(*tool.registrations()).execute(local_ctx(), TOOL_STATS, {"graph_id": "syn_big"})
    assert result.status is ToolStatus.PARTIAL and result.data["coverage"]["records_scanned"] == 100_000
    assert result.data["metrics"]["entities"] == big_graph.truth.entities  # entities read, edges cut short
    assert result.data["metrics"]["relations"] < big_graph.truth.relations


def test_deadline_stops_a_large_scan_as_partial(big_graph):
    ticks = {"n": 0}
    start = datetime(2026, 10, 19, tzinfo=timezone.utc)

    def clock():
        ticks["n"] += 1
        return start + timedelta(milliseconds=ticks["n"])

    control = ScanControl(UNBOUNDED, deadline=start + timedelta(seconds=30), clock=clock)
    report = compute_statistics(_backend(big_graph), local_ctx(), "syn_big", control)
    assert report.coverage.completeness == "partial" and report.coverage.records_scanned == 29_999
    assert any("deadline" in r for r in report.coverage.reasons)


def test_absent_work_after_a_partial_scan_is_unknown_not_absent(big_graph):
    truth = big_graph.truth
    late = max(truth.work_members, key=lambda w: min(int(m.rsplit("E", 1)[1]) for m in truth.work_members[w]))
    key = truth.work_keys[late][0]
    first_member = min(int(m.rsplit("E", 1)[1]) for m in truth.work_members[late])
    with pytest.raises(ContractError) as err:
        paper_subgraph(_backend(big_graph), local_ctx(), "syn_big", key, control=ScanControl(first_member))
    assert err.value.code is ErrorCode.UNAVAILABLE and err.value.retryable
    with pytest.raises(ContractError) as missing:
        paper_subgraph(_backend(big_graph), local_ctx(), "syn_big", "10.9999/never.cited",
                       control=ScanControl(UNBOUNDED))
    assert missing.value.code is ErrorCode.NOT_FOUND
    tool = GraphOperationsTool(_backend(big_graph), max_records=first_member)
    result = _executor(*tool.registrations()).execute(local_ctx(), TOOL_SUBGRAPH,
                                                      {"graph_id": "syn_big", "paper": key})
    assert result.status is ToolStatus.UNAVAILABLE and result.retryable


@pytest.mark.parametrize("bound", [1, 20_000])
def test_capped_corpus_audit_is_partial_and_says_how_much(big_corpus, bound):
    report = audit_corpus(CorpusSource("syn_big", str(big_corpus.path)), control=ScanControl(bound))
    cov = report.coverage
    assert cov.completeness == "partial" and cov.records_scanned == bound
    assert cov.records_expected == big_corpus.truth.term_entries
    assert f"{bound} of {big_corpus.truth.term_entries} term entries audited" in cov.reasons
    assert report.metrics["summary"]["legacy"]["total_entities"] == bound
    if bound == 1:
        tool = CorpusAuditTool(mapping_corpus_resolver({"syn_big": str(big_corpus.path)}), max_records=bound)
        result = _executor(tool.registration()).execute(local_ctx(), "curate_audit_corpus", {"corpus": "syn_big"})
        assert result.status is ToolStatus.PARTIAL


# -- jobs: the full population beyond the inline bound ---------------------------------------------

@pytest.fixture
def _admission():
    llm_admission.reset_controllers()
    yield
    llm_admission.reset_controllers()


@pytest.mark.usefixtures("_admission")
def test_job_covers_the_population_the_inline_bound_cannot(big_graph, big_corpus, tmp_path):
    truth = big_graph.truth
    graph_tool = GraphOperationsTool(_backend(big_graph), max_records=100_000)
    corpus_tool = CorpusAuditTool(mapping_corpus_resolver({"syn_big": str(big_corpus.path)}), max_records=10_000)
    inline = _executor(*graph_tool.registrations()).execute(local_ctx(), TOOL_STATS, {"graph_id": "syn_big"})
    assert inline.status is ToolStatus.PARTIAL
    store = JobStore(tmp_path / "jobs.sqlite3")
    service = JobService(store, settings=local_settings())
    artifacts = FileDocumentStore(tmp_path / "artifacts")
    register_operation_jobs(service, operation_dispatch(graph_tool, corpus_tool),
                            lambda job: local_ctx(corpus_scope=frozenset(job.inputs.corpus_scope)),
                            artifact_store=artifacts)
    ctx = local_ctx(capabilities=frozenset({"ops.run", "chat.ask"}))
    for operation, args, scope in [(TOOL_STATS, {"graph_id": "syn_big"}, ("syn_big",)),
                                   ("curate_audit_corpus", {"corpus": "syn_big"}, ("syn_big",))]:
        handle = service.submit(ctx, OPERATION_JOB_KIND,
                                OperationInputs(corpus_scope=scope, params={"operation": operation, "args": args}))
        job_id, state = service.work_one()
        assert job_id == handle.job_id and state is JobState.SUCCEEDED
        (stage,) = store.stages(job_id)
        assert stage.result["coverage"]["completeness"] == "complete" and stage.progress["complete"] == 1
        full = json.loads(artifacts.get(local_ctx(), stage.artifact_refs[0]))
        assert full["metrics"] == stage.result["metrics"]
        assert len(json.dumps(stage.result, sort_keys=True)) <= 7000
        if operation == TOOL_STATS:
            assert stage.progress["records_scanned"] == truth.entities + truth.edge_records
            assert full["metrics"]["relations"] == truth.relations
        else:
            assert stage.progress["records_scanned"] == big_corpus.truth.term_entries
            assert full["metrics"]["summary"]["distinct_works"] == big_corpus.truth.distinct_works

"""T19D: figure- and table-derived evidence is counted once, and every output passes the D19 projection.

T09 records (``app.modules.vision``) attach :class:`EvidenceRef` locators with a figure,
panel or table id to the records they support. The operations that report on papers
(``lit_summarize_extracted_terms``, ``curate_audit_corpus``, ``kg_compute_statistics``,
``kg_paper_subgraph``, ``kg_compare_snapshots``) must:

* count a figure- or table-derived record exactly like a text-derived one in its unit
  (raw term record, term entry, entity): once, never dropped and never added twice when
  the same record carries text and figure evidence;
* apply the same page eligibility to figure/table pages as to text pages (summary);
* never return the material behind that evidence: captions, table bodies, crops, crop or
  PDF paths, image bytes, snippets or page text (D19). Each output is checked raw, after
  ``check_no_source_material`` and after the user-facing ``DisclosureProjector``; the
  projection removes nothing the totals depend on.
"""

from __future__ import annotations

import json

import pytest

from app.modules.agent_runtime.executor import RuntimeLimits, ToolExecutor
from app.modules.agent_runtime.policies import InMemoryEventSink
from app.modules.contracts import ToolStatus
from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.literature.store import FileDocumentStore
from app.modules.operations.corpus import CorpusAuditTool, CorpusSource, audit_corpus, mapping_corpus_resolver
from app.modules.operations.graph import (
    TOOL_COMPARE,
    TOOL_STATS,
    TOOL_SUBGRAPH,
    GraphOperationsTool,
    compute_statistics,
    paper_subgraph,
)
from app.modules.operations.papers import TOOL_NAME as TOOL_SUMMARY
from app.modules.operations.papers import PaperScope, PaperSummaryTool, pinned_paper_scope, summarize_paper_terms
from app.modules.operations.reports import check_no_source_material
from app.modules.security.disclosure import DisclosureProjector, Viewer
from app.modules.tools.bootstrap import build_registry
from tests.operations import scale_population as sp
from tests.operations.summary_fakes import write_case
from tests.runtime.runtime_fakes import local_ctx

PAPER = "synthetic_fig_paper.pdf"


def _executor(*registrations):
    registry = build_registry(None, include=(), extra=list(registrations), name="t19d-figures", freeze=True)
    return ToolExecutor(registry, event_sink=InMemoryEventSink(), limits=RuntimeLimits(max_retries=0))


def _rec(name, pages, *, snippet_page=None, kind=None, text=sp.CANARY_SNIPPET):
    rec = {"term": name, "category": "Feature", "definition": f"Synthetic definition of {name}.", "pages": pages,
           "source_papers": [PAPER]}
    if snippet_page is not None:
        rec["context_snippets"] = [{"text": text, "source_paper": f"{sp.CANARY_PDF_PATH}/{PAPER}",
                                    "page": snippet_page}]
    if kind:
        rec.update(sp.figure_payload(kind, PAPER, pages[0], 1))
    return rec


# -- summary ---------------------------------------------------------------------------------------

def _summary_case(tmp_path, strip=False):
    records = [
        _rec("Lamellar spacing", [2], snippet_page=2),  # text
        _rec("Lamellar spacing", [3], snippet_page=3, kind="figure", text=sp.CANARY_CAPTION),  # same label, figure
        dict(_rec("Domain purity", [2], snippet_page=2), **{  # text + figure evidence in ONE record
            "evidence": [sp.figure_evidence(PAPER, 3, 2, "a"), sp.table_evidence(PAPER, 5, 1)]}),
        _rec("Peak position", [5], snippet_page=5, kind="table", text=sp.CANARY_TABLE),  # table
        _rec("Orientation map", [4], snippet_page=4, kind="figure", text=sp.CANARY_CAPTION),  # figure, ineligible page
        _rec("Scale bar", [3], snippet_page=9, kind="figure"),  # snippet page ineligible -> pages fallback
    ]
    if strip:
        records = [{k: v for k, v in r.items() if k not in ("evidence", "evidence_modality", "figure_caption",
                                                             "table_body", "figure_crop", "image_b64")}
                   for r in records]
    return write_case(tmp_path, {"extraction_state": "partial", "selected_pages": [2, 3, 5]}, records,
                      filename=PAPER)


def test_summary_counts_figure_and_table_records_once_under_page_eligibility(tmp_path):
    case = _summary_case(tmp_path / "fig")
    s = summarize_paper_terms(PaperScope(**case)).summary
    assert (s.term_count, s.displayed_term_count, s.completeness) == (5, 5, "complete")
    assert set(s.used_pages) == {2, 3, 5}
    assert all(c.endswith(("p.2]", "p.3]", "p.5]")) for c in s.citations)
    plain = summarize_paper_terms(PaperScope(**_summary_case(tmp_path / "plain", strip=True))).summary
    assert (plain.term_count, plain.summary_text) == (s.term_count, s.summary_text)  # T09 fields change nothing
    assert sp.leaked(json.dumps(s.to_data())) == []


def test_summary_at_scale_includes_figure_and_table_records(big_session):
    truth = big_session.truth
    assert truth.figure_records > 1_000 and truth.table_records > 1_000
    s = summarize_paper_terms(PaperScope(str(big_session.path), str(big_session.extra[0]), sp.ACTIVE)).summary
    assert s.term_count == truth.term_count  # the truth counts figure/table records exactly once each


def test_summary_tool_output_passes_the_projection(tmp_path):
    case = _summary_case(tmp_path)
    with pinned_paper_scope(PaperScope(**case)):
        result = _executor(PaperSummaryTool().registration()).execute(local_ctx(), TOOL_SUMMARY, {})
    assert result.status is ToolStatus.OK
    body = json.dumps({"data": result.data, "warnings": result.warnings,
                       "evidence": [e.model_dump(mode="json") for e in result.evidence]})
    assert sp.leaked(body) == [] and str(tmp_path) not in body
    for ref in result.evidence:  # page-cited, no figure crop/quote material
        assert ref.quote is None and ref.locator.page in {2, 3, 5} and ref.locator.bbox is None
    # The baseline summary prints the extracted *definitions* (T19B, unchanged); never snippets or captions.
    assert "Synthetic definition of Lamellar spacing." in result.data["summary_text"]


# -- graph -----------------------------------------------------------------------------------------

def _figure_graph(tmp_path, strip=False):
    work = "10.1021_syn.fig.pdf"
    things = [
        {"id": "syn:F1", "name": "Text only", "category": "Material", "source_papers": [work]},
        dict({"id": "syn:F2", "name": "Figure derived", "category": "Feature", "source_papers": [work]},
             **sp.figure_payload("figure", work, 3, 1)),
        dict({"id": "syn:F3", "name": "Table derived", "category": "Feature", "source_papers": [work]},
             **sp.figure_payload("table", work, 4, 1)),
        {"id": "syn:F4", "name": "Text and figure", "category": "Feature", "source_papers": [work, work],
         "evidence": [sp.figure_evidence(work, 3, 1, "b"), sp.table_evidence(work, 4, 1)],
         "context_snippets": [{"text": sp.CANARY_SNIPPET, "source_paper": work, "page": 2},
                              {"text": sp.CANARY_CAPTION, "source_paper": work, "page": 3, "figure_id": "fig-p003-01"}]},
        {"id": "syn:F5", "name": "Other paper", "category": "Material", "source_papers": ["10.1021_syn.other.pdf"]},
    ]
    if strip:
        things = [{k: v for k, v in t.items() if k in ("id", "name", "category", "source_papers")} for t in things]
    assoc = [{"subject": "syn:F1", "predicate": "rel:SHOWN_IN", "object": "syn:F2"},
             {"subject": "syn:F2", "predicate": "rel:TABULATED_IN", "object": "syn:F3"},
             {"subject": "syn:F4", "predicate": "rel:RELATED_TO", "object": "syn:F5"}]
    path = sp.write_json(tmp_path / ("plain.json" if strip else "fig.json"), {"things": things, "associations": assoc})
    return JsonGraphBackend({"g": path})


def test_graph_counts_figure_and_table_derived_entities_once(tmp_path):
    report = compute_statistics(_figure_graph(tmp_path), local_ctx(), "g")
    m = report.metrics
    assert (m["entities"], m["entities_with_citations"], m["paper_keys"], m["distinct_works"]) == (5, 5, 2, 2)
    sub = paper_subgraph(_figure_graph(tmp_path), local_ctx(), "g", "10.1021/syn.fig").metrics
    assert (sub["entities"], sub["internal_relations"], sub["boundary_relations"]) == (4, 2, 1)
    plain = compute_statistics(_figure_graph(tmp_path, strip=True), local_ctx(), "g")
    assert plain.metrics == m  # evidence modality neither adds nor removes anything
    assert sp.leaked(json.dumps(report.to_data())) == []


def test_graph_at_scale_counts_figure_entities_with_the_rest(big_graph):
    truth = big_graph.truth
    assert truth.figure_entities > 4_000 and truth.table_entities > 3_000
    # entities_with_citations in the truth counts each figure/table-derived entity once (no extra count).
    assert truth.entities_with_citations == sum(1 for i in range(truth.entities) if i % 50 != 7)


# -- corpus ----------------------------------------------------------------------------------------

def test_corpus_counts_figure_and_table_entries_once(tmp_path):
    key = "10.1021_syn.fig.pdf"
    meta = {key: {"doi": "10.1021/syn.fig", "paper_title": "Synthetic figure study", "publication_year": 2023}}
    terms = [dict(_rec(f"T{i}", [3], snippet_page=3, kind=kind), source_papers=[key], source_metadata=meta)
             for i, kind in enumerate([None, "figure", "table", "figure"])]
    path = sp.write_json(tmp_path / "terms.json", {"terms": terms})
    report = audit_corpus(CorpusSource("c", str(path)))
    s = report.metrics["summary"]
    assert (s["legacy"]["total_entities"], s["distinct_works"], s["papers_per_year_v2"]) == (4, 1, {"2023": 1})
    assert report.metrics["stubs"]["stub_signals_total"] == 0
    assert sp.leaked(json.dumps(report.to_data())) == []


# -- D19 projection of every operation output ------------------------------------------------------

@pytest.fixture(scope="module")
def outputs(big_graph, big_corpus, big_session, tmp_path_factory):
    """``{tool: (ToolResult, stored artifact bytes or b"")}`` for every operation over the large populations."""
    store = FileDocumentStore(tmp_path_factory.mktemp("t19d-artifacts"))
    payload = json.loads(big_graph.path.read_text(encoding="utf-8"))
    older = sp.write_json(tmp_path_factory.mktemp("t19d-older") / "older.json",
                          dict(payload, things=payload["things"][:30_000], associations=payload["associations"][:90_000]))
    graph = GraphOperationsTool(JsonGraphBackend({"syn_big": big_graph.path, "syn_older": older}), artifact_store=store)
    corpus = CorpusAuditTool(mapping_corpus_resolver({"syn_big": str(big_corpus.path)}), artifact_store=store)
    executor = _executor(*graph.registrations(), corpus.registration(), PaperSummaryTool().registration())
    ctx = local_ctx()
    work = max(big_graph.truth.work_members, key=lambda w: len(big_graph.truth.work_members[w]))
    runs = {
        TOOL_STATS: {"graph_id": "syn_big"},
        TOOL_COMPARE: {"graph_ids": ["syn_older", "syn_big"]},
        TOOL_SUBGRAPH: {"graph_id": "syn_big", "paper": big_graph.truth.work_keys[work][0], "max_nodes": 200},
        "curate_audit_corpus": {"corpus": "syn_big"},
    }
    out = {name: executor.execute(ctx, name, args) for name, args in runs.items()}
    with pinned_paper_scope(PaperScope(str(big_session.path), str(big_session.extra[0]), sp.ACTIVE)):
        out[TOOL_SUMMARY] = executor.execute(ctx, TOOL_SUMMARY, {})
    return {name: (res, b"".join(store.get(ctx, ref) for ref in res.artifact_refs)) for name, res in out.items()}


@pytest.mark.parametrize("tool", [TOOL_STATS, TOOL_COMPARE, TOOL_SUBGRAPH, "curate_audit_corpus", TOOL_SUMMARY])
def test_no_source_material_in_any_output_or_artifact(outputs, tool, big_graph, big_corpus, big_session):
    result, artifact = outputs[tool]
    assert result.status is ToolStatus.OK, result.warnings
    body = json.dumps({"data": result.data, "warnings": result.warnings,
                       "evidence": [e.model_dump(mode="json") for e in result.evidence]}, default=str)
    stored = artifact.decode("utf-8")
    for text in (body, stored):
        assert sp.leaked(text) == []
        for path in (big_graph.path, big_corpus.path, big_session.path):
            assert str(path.parent) not in text
    if tool != TOOL_SUMMARY:
        check_no_source_material(result.data)
        check_no_source_material(json.loads(stored))
        assert sp.CANARY_DEFINITION not in body + stored  # graph/corpus reports never echo definitions
        assert len(json.loads(stored).get("preview") or {}) >= len(result.data.get("preview") or {})


@pytest.mark.parametrize("tool", [TOOL_STATS, TOOL_COMPARE, TOOL_SUBGRAPH, "curate_audit_corpus"])
def test_disclosure_projection_keeps_every_total(outputs, tool):
    result, _ = outputs[tool]
    projected = DisclosureProjector().project_progress({"result": result.data}, Viewer.from_context(local_ctx()))
    data = projected["result"]
    for key in ("metrics", "coverage", "counting_units", "population", "source_revisions", "excluded"):
        assert data.get(key) == result.data.get(key), key
    assert sp.leaked(json.dumps(projected)) == []

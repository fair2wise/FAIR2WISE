"""T19B: operations.papers wraps summarize_extracted_terms unchanged and types its result."""

from __future__ import annotations

import json
import re
import socket

import pytest

from app.modules.f2w_agent import paper_evidence_agent as pea
from app.modules.operations.papers import (
    COUNTING_UNIT,
    MAX_RESULT_CHARS,
    PaperScope,
    summarize_paper_terms,
)
from tests.operations.summary_fakes import CASES, FILENAME, QUERY_TERMS, term, write_case

PDF_RE = re.compile(r"\[PDF:\s*([^]]+?)\s+p\.(\d+)\]")

#: (case, kwargs) pairs covering both legacy call shapes and every relevance mode.
CALLS = [(name, {}) for name in CASES] + [
    ("capped", {"max_terms": 6}),
    ("query_matched", {"max_terms": 6, "query": "How does ionic conductivity change?",
                       "missing_topics": ["electrolyte additive"], "relevant_node_names": ["Ionic conductivity"]}),
    ("query_matched", {"max_terms": 1, "query": "ionic conductivity electrolyte"}),
    ("query_nomatch", {"max_terms": 6, "query": "zzqx quokka", "missing_topics": ["unrelated"]}),
    ("query_nomatch", {"max_terms": 2, "relevant_node_names": ["Not present"]}),
]


@pytest.fixture(autouse=True)
def _no_model_no_network(monkeypatch):
    """The wrapped function must not build a chat client or open a socket."""
    from app.modules.term_extractor import clients

    def refuse(*a, **k):
        raise AssertionError("summarize_extracted_terms must not call a model or the network")

    monkeypatch.setattr(clients, "make_chat_client", refuse)
    monkeypatch.setattr(pea.PaperEvidenceAgent, "_chat", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _write(tmp_path, name):
    if name.startswith("query_"):
        return write_case(tmp_path / name, {"extraction_state": "partial", "selected_pages": [2, 3]}, QUERY_TERMS)
    entry, terms = CASES[name]
    return write_case(tmp_path / name, entry, terms)


def _scope(paths):
    return PaperScope(paths["terms_path"], paths["manifest_path"], paths["filename"], paper_id="syn-1")


@pytest.mark.parametrize("name,kw", CALLS, ids=[f"{n}-{i}" for i, (n, _) in enumerate(CALLS)])
def test_same_output_as_the_baseline_function(tmp_path, name, kw):
    paths = _write(tmp_path, name)
    baseline = pea.summarize_extracted_terms(paths["terms_path"], paths["manifest_path"], paths["filename"], **kw)
    outcome = summarize_paper_terms(_scope(paths), **kw)
    assert outcome.legacy == baseline
    assert json.dumps(outcome.legacy, sort_keys=False) == json.dumps(baseline, sort_keys=False)
    summary = outcome.summary
    assert summary.summary_text == baseline["answer"] and not summary.summary_truncated
    assert summary.source_paper == FILENAME and summary.paper_id == "syn-1"
    assert summary.counting_unit == COUNTING_UNIT == "raw_extracted_term_record"
    if baseline["status"] != "success":
        assert summary.outcome == "insufficient" and summary.term_count == 0
        return
    assert summary.outcome == "summarized" and summary.scan_complete
    assert summary.term_count == baseline["term_count"]
    assert summary.displayed_term_count == baseline["displayed_term_count"]
    assert summary.relevant_term_count == baseline["relevant_term_count"]
    assert summary.relevance_mode == baseline["relevance_mode"]
    assert list(summary.used_pages) == baseline["used_pages"]
    tokens = PDF_RE.findall(baseline["answer"])
    assert [t for t in summary.citations] == list(dict.fromkeys(
        m.group(0) for m in PDF_RE.finditer(baseline["answer"])))
    assert {int(p) for _, p in tokens} == set(baseline["used_pages"])
    assert {n for n, _ in tokens} == {FILENAME}


def test_page_eligibility_is_the_baseline_rule_including_d10(tmp_path):
    targeted = summarize_paper_terms(_scope(_write(tmp_path, "targeted"))).summary
    assert "Hidden beta" not in targeted.summary_text and "Other paper delta" not in targeted.summary_text
    assert "**Snippet gamma** (Method): Synthetic definition of Snippet gamma. [PDF: synthetic_paper.pdf p.3]" \
        in targeted.summary_text  # snippet page (this file only) wins over term page 1
    assert "(RawKind)" in targeted.summary_text and "spread over lines" in targeted.summary_text
    assert set(targeted.used_pages) <= {2, 3}

    assert summarize_paper_terms(_scope(_write(tmp_path, "latest_partial"))).summary.used_pages == (4,)

    # D-10: a full extraction without a top-level page list is bounded by the terms' own pages;
    # full.selected_pages=[1] is ignored. _eligible_pages (not used here) would say [1].
    paths = _write(tmp_path, "full_term_pages")
    full = summarize_paper_terms(_scope(paths)).summary
    assert full.used_pages == (2, 5) and full.term_count == 2 and full.extraction_state == "full"
    entry = json.loads(open(paths["manifest_path"]).read())["papers"][FILENAME]
    assert pea._eligible_pages(entry, 10) == ("full", [1])


def test_duplicate_labels_count_as_separate_records(tmp_path):
    summary = summarize_paper_terms(_scope(_write(tmp_path, "duplicates"))).summary
    assert summary.term_count == 3 and summary.summary_text.count("**Repeated label**") == 2


def test_capped_display_is_partial_never_exhaustive(tmp_path):
    paths = _write(tmp_path, "capped")
    full_cap = summarize_paper_terms(_scope(paths)).summary
    assert (full_cap.term_count, full_cap.displayed_term_count, full_cap.omitted_term_count) == (23, 16, 7)
    assert full_cap.completeness == "partial" and full_cap.legacy_omitted_note_count == 7
    assert full_cap.completeness_reason == "display capped at max_terms=16: 16 of 23 records listed"
    assert any("partial display: 16 of 23" in w for w in full_cap.warnings)
    six = summarize_paper_terms(_scope(paths), max_terms=6).summary
    assert (six.displayed_term_count, six.omitted_term_count, six.completeness) == (6, 17, "partial")


def test_complete_display_when_everything_is_listed(tmp_path):
    summary = summarize_paper_terms(_scope(_write(tmp_path, "duplicates"))).summary
    assert summary.completeness == "complete" and summary.completeness_reason is None
    assert summary.omitted_term_count == 0 and summary.warnings == ()


def test_query_scoped_omitted_note_is_not_read_as_the_total(tmp_path):
    paths = _write(tmp_path, "query_matched")
    summary = summarize_paper_terms(_scope(paths), max_terms=1, query="ionic conductivity electrolyte").summary
    # 4 records, 2 matched, 1 shown: legacy line says "1 additional terms omitted"
    assert (summary.term_count, summary.relevant_term_count, summary.displayed_term_count) == (4, 2, 1)
    assert summary.legacy_omitted_note_count == 1 and summary.omitted_term_count == 3
    assert summary.summary_text.endswith("- 1 additional terms omitted from this compact summary.")
    assert summary.completeness == "partial"
    assert "2 of 4 matched, capped at max_terms=1" in summary.completeness_reason
    assert any("counts only query-matched records" in w for w in summary.warnings)

    nomatch = summarize_paper_terms(_scope(paths), max_terms=6, query="zzqx quokka").summary
    assert nomatch.relevance_mode == "query_targeted_pages" and nomatch.displayed_term_count == 3
    assert "min(max_terms, 3)=3" in nomatch.completeness_reason


def test_unreadable_and_raising_inputs_pass_through(tmp_path):
    paths = _write(tmp_path, "targeted")
    missing = PaperScope(paths["terms_path"], str(tmp_path / "nope.json"), FILENAME)
    outcome = summarize_paper_terms(missing)
    assert outcome.legacy["status"] == "manifest_error" and outcome.summary.outcome == "unreadable"
    assert outcome.legacy == pea.summarize_extracted_terms(missing.terms_path, missing.manifest_path, FILENAME)

    bad = write_case(tmp_path / "bad", {"selected_pages": [1]}, "not a mapping")
    with pytest.raises(AttributeError) as direct:
        pea.summarize_extracted_terms(bad["terms_path"], bad["manifest_path"], bad["filename"])
    with pytest.raises(AttributeError) as wrapped:
        summarize_paper_terms(_scope(bad))
    assert str(wrapped.value) == str(direct.value)


def test_result_is_bounded_with_long_definitions(tmp_path):
    long_terms = [term(f"Long {i:02d}", [1], definition="lorem ipsum é " * 120) for i in range(16)]
    paths = write_case(tmp_path / "long", {"selected_pages": [1]}, long_terms)
    outcome = summarize_paper_terms(_scope(paths))
    summary = outcome.summary
    assert len(json.dumps(summary.to_data(), sort_keys=True)) <= MAX_RESULT_CHARS
    assert summary.summary_truncated and outcome.legacy["answer"].startswith(summary.summary_text)
    assert summary.summary_chars == len(outcome.legacy["answer"]) > MAX_RESULT_CHARS
    assert summary.term_count == 16 and summary.displayed_term_count == 16  # counts unaffected
    assert len(summary.citations) == 1 and any("truncated" in w for w in summary.warnings)

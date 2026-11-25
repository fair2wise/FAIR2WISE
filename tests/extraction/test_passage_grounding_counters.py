"""Supporting passages, step 2-3 (backend): data model, store union, json2kg pass-through, run counters, manifest block.

The extraction runs use the real ``Orchestrator`` and LangGraph tool loop with a scripted model (no model call).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage
import pytest

from app.modules import json2kg
from app.modules.library import bundles as B
from app.modules.term_extractor import orchestrator as orch_mod
from app.modules.term_extractor.models import ContextSnippet, TermRecord
from app.modules.term_extractor.orchestrator import Orchestrator
from app.modules.term_extractor.services import Services
from app.modules.term_extractor.store import TermStore
from tests.extraction.test_passage_capture_real_path import FakeMergeIndex, ToolAwareScript, call

SENTENCE_P2 = "The voxel grid divides the reconstruction volume into small cubes that hold the retrieved extinction."
PAGE1 = " ".join(["Cloud tomography needs many camera views of the same scene taken at the same time."] * 3)
PAGES = [PAGE1, SENTENCE_P2 + " It was used for every scene of the field campaign in the study."]


class FakeDoc:
    """The slice of ``PdfDocument`` the orchestrator uses."""

    def __init__(self, pages: list[str]) -> None:
        self.pages, self.page_count = pages, len(pages)

    def load_page(self, i: int) -> Any:  # noqa: ANN401 - fake page
        return SimpleNamespace(get_text=lambda: self.pages[i])

    def __len__(self) -> int:
        return self.page_count


def make_orchestrator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: list[Any], pages: list[str]) -> Orchestrator:
    model = ToolAwareScript(script=script)
    monkeypatch.setattr(orch_mod, "TermMergeIndex", FakeMergeIndex)
    monkeypatch.setattr(orch_mod, "build_services", lambda chebi_obo_path: Services(formula_checker=None, chebi_lookup=None))  # type: ignore[arg-type]
    monkeypatch.setattr(orch_mod, "make_chat_client", lambda **kw: SimpleNamespace(kw=kw))
    monkeypatch.setattr(Orchestrator, "_build_llm", lambda self: model)
    monkeypatch.setattr(orch_mod, "open_pdf", lambda path: FakeDoc(pages))
    monkeypatch.setattr(orch_mod.provenance, "extract_pub_metadata", lambda doc, path: {})
    monkeypatch.setattr(orch_mod.provenance, "extract_code_snippets", lambda *a, **k: [])
    return Orchestrator("scripted", str(tmp_path / "terms.json"), backend="ollama", chebi_obo_path=None,
                        schema_path="storage/schema/matkg_schema.yaml", max_workers=1)


def register(*terms: tuple[str, str | None]) -> list[Any]:
    calls = [call("register_term", {"term": t, "definition": f"{t} definition.", "category": "Thing",
                                    **({"context": c} if c else {})}, f"c{i}") for i, (t, c) in enumerate(terms)]
    return [AIMessage(content="", tool_calls=calls), AIMessage(content="done")]


@pytest.fixture
def pdf_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "pdfs"
    folder.mkdir()
    (folder / "clouds.pdf").write_bytes(b"%PDF-1.4 fake")
    return folder


# --------------------------------------------------------------------------------------------------- data model


def test_snippet_defaults_and_old_files_read_unchanged() -> None:
    old = {"term": "x", "context_snippets": [{"text": "A sentence.", "source_paper": "a.pdf", "page": 2}]}
    record = TermRecord.from_dict(old)
    snippet = record.context_snippets[0]
    assert (snippet.kind, snippet.verified) == ("quoted", False)
    assert record.to_dict()["context_snippets"] == old["context_snippets"]  # the old shape is written back


def test_snippet_extras_are_written_only_when_not_default() -> None:
    rec = TermRecord(term="x", definition="", category="Thing", context_snippets=[
        ContextSnippet("q", "a.pdf", 1, verified=True), ContextSnippet("l", "a.pdf", 2, kind="located", verified=True),
        ContextSnippet("p", "a.pdf", 3)])
    out = rec.to_dict()["context_snippets"]
    assert out == [{"text": "q", "source_paper": "a.pdf", "page": 1, "verified": True},
                   {"text": "l", "source_paper": "a.pdf", "page": 2, "kind": "located", "verified": True},
                   {"text": "p", "source_paper": "a.pdf", "page": 3}]
    assert TermRecord.from_dict(rec.to_dict()).context_snippets == rec.context_snippets


# --------------------------------------------------------------------------------------------------- store union


def _rec(*snips: ContextSnippet) -> TermRecord:
    return TermRecord(term="P3HT", definition="d", category="Thing", context_snippets=list(snips))


def test_union_appends_new_after_existing_but_verified_quoted_goes_before_located(tmp_path: Path) -> None:
    store = TermStore(str(tmp_path / "t.json"))
    store.upsert(_rec(ContextSnippet("loc one", "a.pdf", 1, kind="located", verified=True)))
    store.upsert(_rec(ContextSnippet("loc two", "a.pdf", 2, kind="located", verified=True)))
    store.upsert(_rec(ContextSnippet("quoted", "a.pdf", 3, kind="quoted", verified=True)))
    store.upsert(_rec(ContextSnippet("unverified", "b.pdf", 1)))
    got = [s.text for s in store.get("p3ht").context_snippets]
    assert got == ["quoted", "loc one", "loc two", "unverified"]


def test_union_dedupes_by_paper_page_text_and_keeps_quoted_when_both_kinds_arrive(tmp_path: Path) -> None:
    store = TermStore(str(tmp_path / "t.json"))
    store.upsert(_rec(ContextSnippet("same", "a.pdf", 1, kind="located", verified=True)))
    store.upsert(_rec(ContextSnippet("same", "a.pdf", 1, kind="quoted", verified=True)))
    store.upsert(_rec(ContextSnippet("same", "a.pdf", 1, kind="located", verified=True)))
    (only,) = store.get("p3ht").context_snippets
    assert (only.kind, only.verified) == ("quoted", True)
    store.upsert(_rec(ContextSnippet("same", "a.pdf", 2, kind="located")))      # another page: another passage
    assert len(store.get("p3ht").context_snippets) == 2


# --------------------------------------------------------------------------------------------------- json2kg


def _graph(terms: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return json2kg.build_graph(terms, [], **kw)


def test_json2kg_node_carries_kind_and_verified_through() -> None:
    snippets = [{"text": "A.", "source_paper": "a.pdf", "page": 1, "verified": True},
                {"text": "B.", "source_paper": "a.pdf", "page": 2, "kind": "located", "verified": True}]
    graph = _graph([{"term": "P3HT", "definition": "d", "category": "Thing", "context_snippets": snippets}])
    node = next(n for n in graph["things"] if n.get("name") == "P3HT")
    assert node["context_snippets"] == snippets


def test_json2kg_inputs_without_the_new_fields_are_unchanged() -> None:
    plain = [{"text": "A.", "source_paper": "a.pdf", "page": 1}]
    graph = _graph([{"term": "P3HT", "definition": "d", "category": "Thing", "context_snippets": plain}])
    node = next(n for n in graph["things"] if n.get("name") == "P3HT")
    assert node["context_snippets"] == plain


def test_json2kg_merge_existing_upgrades_a_located_passage_that_arrives_quoted() -> None:
    first = {"term": "P3HT", "definition": "d", "category": "Thing",
             "context_snippets": [{"text": "S.", "source_paper": "a.pdf", "page": 1, "kind": "located", "verified": True}]}
    second = {"term": "P3HT", "definition": "d", "category": "Thing",
              "context_snippets": [{"text": "S.", "source_paper": "a.pdf", "page": 1, "verified": True}]}
    graph = _graph([first, second], merge_existing=True)
    (node,) = [n for n in graph["things"] if n.get("name") == "P3HT"]
    assert node["context_snippets"] == [{"text": "S.", "source_paper": "a.pdf", "page": 1, "verified": True}]


# --------------------------------------------------------------------------------------------------- run counters


def test_run_summary_has_per_paper_and_total_counters(monkeypatch, tmp_path, pdf_dir, caplog) -> None:
    script = register(
        ("cloud tomography", "Cloud tomography needs many camera views of the same scene taken at the same time."),
        ("voxel grid", SENTENCE_P2),                      # quoted, found on page 2 though the model was on page 1
        ("ghost term", "A sentence the paper never contained at all."),   # rejected, mention not in the paper either
    )
    orch = make_orchestrator(monkeypatch, tmp_path, script, PAGES)
    with caplog.at_level(logging.INFO):
        summary = orch.process_directory(str(pdf_dir))
    row = {"terms": 3, "with_passage": 2, "quoted": 2, "located": 0, "rejected": 1, "uncovered": 1, "relations": 0, "relations_with_evidence": 0, "evidence_sentences": 0, "relations_without": 0}
    assert summary["passages"] == {"total": row, "papers": {"clouds.pdf": row}}
    voxel = orch.store.get("voxel grid").context_snippets[0]
    assert (voxel.kind, voxel.verified, voxel.page) == ("quoted", True, 2)     # page where found
    assert orch.store.get("ghost term").context_snippets == []
    assert any("passages clouds.pdf: 3 terms, 2 with a passage" in r.message for r in caplog.records)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "supporting passage" in r.message]
    assert warnings and "2 of 3 terms (67%)" in warnings[0].message          # below the 90% threshold
    saved = json.loads(Path(orch.store.output_file).read_text())["metadata"]["passages"]
    assert saved == {"clouds.pdf": row}


def test_no_warning_at_full_coverage_and_continued_runs_sum_never_overwrite(monkeypatch, tmp_path, pdf_dir, caplog) -> None:
    first = make_orchestrator(monkeypatch, tmp_path, register(("voxel grid", SENTENCE_P2)), PAGES)
    with caplog.at_level(logging.INFO):
        first.process_directory(str(pdf_dir))
    assert not [r for r in caplog.records if r.levelno == logging.WARNING and "supporting passage" in r.message]
    again = make_orchestrator(monkeypatch, tmp_path, register(("cloud tomography", PAGE1[:80]), ("other", None)), PAGES)
    summary = again.process_directory(str(pdf_dir))
    assert summary["passages"]["total"] == {"terms": 2, "with_passage": 1, "quoted": 1, "located": 0, "rejected": 0,
                                            "uncovered": 1, "relations": 0, "relations_with_evidence": 0, "evidence_sentences": 0, "relations_without": 0}      # this run only
    saved = json.loads(Path(again.store.output_file).read_text())["metadata"]["passages"]["clouds.pdf"]
    assert saved == {"terms": 3, "with_passage": 2, "quoted": 2, "located": 0, "rejected": 0, "uncovered": 1, "relations": 0, "relations_with_evidence": 0, "evidence_sentences": 0, "relations_without": 0}


def test_targeted_run_reports_passages_too(monkeypatch, tmp_path, pdf_dir) -> None:
    orch = make_orchestrator(monkeypatch, tmp_path, register(("voxel grid", SENTENCE_P2)), PAGES)
    result = orch.process_pdf_targeted(str(pdf_dir / "clouds.pdf"), query="voxel grid")
    assert result["passages"]["quoted"] == 1 and result["passages"]["terms"] == 1


# --------------------------------------------------------------------------------------------------- manifest block


def test_manifest_passages_block_is_summed_per_paper_on_continuation(tmp_path: Path) -> None:
    store = B.BundleStore(tmp_path / "bundles")
    schema = {"id": "matkg_schema@0.3.1", "version": "0.3.1", "sha256": "abc", "path": "storage/schema/v0.3.1/matkg_schema.yaml"}
    store.create("b", schema=schema, topic=None, model="m", backend="cborg", papers=[])
    row = {"terms": 4, "with_passage": 3, "quoted": 2, "located": 1, "rejected": 1, "uncovered": 1}
    paper = B.PaperRef("a.pdf", "sha", 3)
    store.record_extraction("b", schema_id="matkg_schema@0.3.1", model="m", backend="cborg", papers=[paper], passages={"a.pdf": row})
    store.record_extraction("b", schema_id="matkg_schema@0.3.1", model="m", backend="cborg", papers=[paper],
                            passages={"a.pdf": {"terms": 2, "with_passage": 2, "quoted": 2, "located": 0, "rejected": 0,
                                                "uncovered": 0}, "b.pdf": row})
    store.record_extraction("b", schema_id="matkg_schema@0.3.1", model="m", backend="cborg", papers=[paper])   # no block: untouched
    block = store.get("b").manifest["passages"]
    assert block["a.pdf"] == {"terms": 6, "with_passage": 5, "quoted": 4, "located": 1, "rejected": 1, "uncovered": 1}
    assert block["b.pdf"] == row
    assert set(store.get("b").manifest) >= {"schema", "papers", "updated_at"}


def test_relation_evidence_is_counted_per_paper_and_warns_below_half(monkeypatch, tmp_path, pdf_dir, caplog) -> None:
    relations = [{"relation": "part_of", "related_term": "reconstruction volume"},
                 {"relation": "related_to", "related_term": "ghost partner"},
                 {"relation": "related_to", "related_term": "another ghost"}]
    script = [AIMessage(content="", tool_calls=[call("register_term", {
        "term": "voxel grid", "definition": "d", "category": "Thing", "context": SENTENCE_P2, "relations": relations}, "c1")]),
        AIMessage(content="done")]
    orch = make_orchestrator(monkeypatch, tmp_path, script, PAGES)
    with caplog.at_level(logging.INFO):
        summary = orch.process_directory(str(pdf_dir))
    total = summary["passages"]["total"]
    assert (total["relations"], total["relations_with_evidence"], total["evidence_sentences"],
            total["relations_without"]) == (3, 1, 1, 2)
    (rel,) = [r for r in orch.store.get("voxel grid").relations if r.related_term == "reconstruction volume"]
    assert [(e["source_paper"], e["page"], e["kind"]) for e in rel.evidence] == [("clouds.pdf", 2, "located")]
    assert any("relation evidence clouds.pdf: 3 relations, 1 with a sentence naming both ends" in r.message for r in caplog.records)
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "relations (33%)" in r.message]
    assert warned
    saved = json.loads(Path(orch.store.output_file).read_text())
    assert saved["terms"][0]["relations"][0]["evidence"][0]["kind"] == "located"
    assert saved["metadata"]["passages"]["clouds.pdf"]["relations"] == 3

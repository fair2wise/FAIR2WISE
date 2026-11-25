"""Publication metadata: the library's own record is the source of truth, PDF Info junk is never shown (pilot 2026-11-25).

The plecos KG showed ``a11v6n1.pdf · Luiz Fernando · 2008``, ``f2w://path/2010-463.vp · Administrator · 2011``, a postal
address as authors and a 1904 monograph dated 2008: the PDF's Info fields, although every harvested paper's manifest row
holds the title, authors, year, DOI and venue the registry gave.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.modules.library.backfill import backfill
from app.modules.library.passages_job import LibraryPassages
from app.modules.library.probe import ProbeResult
from app.modules.library.publication_records import PublicationRecords, meta_from_row
from app.modules.term_extractor.orchestrator import Orchestrator
from app.modules.term_extractor.pub_clean import is_junk_author, is_junk_title, merge_known_publication, sanitize_pdf_meta
from tests.library.test_library_backfill import Source, read, row, write
from tests.library.test_library_service import FakeProbe, make, pdf


@pytest.mark.parametrize("title", ["a11v6n1.pdf", "f2w://path/2010-463.vp", "a11v6n1", "2010-463", "C:\\Users\\x\\paper.doc",
                                   "Microsoft Word - draft.docx", "untitled", "http://example.org/x", ""])
def test_junk_titles(title):
    assert is_junk_title(title)


@pytest.mark.parametrize("title", ["III. A Monograph of the Fishes of the Family Loricariidae", "Karyotype diversity in Ancistrus",
                                   "Phylogeny of the Loricariidae (Siluriformes)", "T"])
def test_real_titles_are_kept(title):
    assert not is_junk_title(title)


def test_junk_authors():
    assert is_junk_author("Administrator") and is_junk_author("admin") and is_junk_author("")
    assert is_junk_author("CENTRAL PARK WEST AT 79TH STREET, NEW YORK, NY 10024")
    assert not is_junk_author("Luiz Fernando") and not is_junk_author("J. Q. Public")


def test_the_library_record_wins_and_the_pdf_only_fills_what_it_lacks_without_junk():
    pdf_meta = {"paper_title": "f2w://path/2010-463.vp", "authors": ["Administrator"], "publication_year": 2011, "journal": None,
                "doi": None}
    known = {"paper_title": "Phylogeny of the Loricariidae", "authors": ["A. One", "B. Two"], "publication_year": 2010,
             "doi": "10.1/x", "journal": "Zootaxa"}
    assert merge_known_publication(pdf_meta, known) == {**pdf_meta, **known}
    # No record: the junk is dropped, the year (a PDF fact the record does not contradict) stays.
    merged = merge_known_publication(pdf_meta, None)
    assert merged["paper_title"] is None and merged["authors"] == [] and merged["publication_year"] == 2011
    assert sanitize_pdf_meta({"paper_title": "Real Title Here"}) == {"paper_title": "Real Title Here"}   # no keys invented


def write_manifest(lib: Path, rows: list[dict[str, Any]], category: str = "plecos") -> None:
    (lib / category).mkdir(parents=True, exist_ok=True)
    (lib / category / "manifest.json").write_text(json.dumps({"corpus": category, "papers": rows}), encoding="utf-8")


def test_records_read_the_manifest_by_file_name_and_never_guess_between_two_files(tmp_path):
    write_manifest(tmp_path, [
        row("a", title="Phylogeny of the Loricariidae", authors=["A. One"], publication_year=2010, venue="Zootaxa",
            abstract="An abstract.", pdf_path="papers/plecos/2010/a.pdf"),
        row("b", title="f2w://path/b.vp", authors=["Administrator"], publication_year=2011, pdf_path="papers/plecos/2011/b.pdf"),
        row("c", title="First", pdf_path="papers/plecos/2012/same.pdf", pdf_sha256="s1"),
        row("d", title="Second", pdf_path="papers/plecos/2013/same.pdf", pdf_sha256="s2"),
    ])
    records = PublicationRecords(tmp_path)
    assert records.for_file("a.pdf") == {"paper_title": "Phylogeny of the Loricariidae", "authors": ["A. One"], "publication_year": 2010,
                                         "doi": "10.1/a", "journal": "Zootaxa", "abstract_text": "An abstract."}
    assert records.for_file("b.pdf") == {"publication_year": 2011, "doi": "10.1/b"}   # junk title and author left out
    assert records.for_file("same.pdf") is None and records.for_file("same.pdf", "s2")["paper_title"] == "Second"
    assert records.for_file("missing.pdf") is None
    assert meta_from_row({"title": "x" * 3, "publication_year": "2010"}) == {"paper_title": "xxx"}


def test_the_extractor_uses_the_record_then_the_pdf(tmp_path):
    orch = Orchestrator.__new__(Orchestrator)
    orch.known_publications = lambda name: {"paper_title": "Real title of the paper", "publication_year": 1904} if name == "regan.pdf" else None
    junk = {"paper_title": "a11v6n1.pdf", "authors": ["Administrator"], "publication_year": 2008}
    got = orch._publication_metadata("regan.pdf", junk)
    assert got["paper_title"] == "Real title of the paper" and got["publication_year"] == 1904 and got["authors"] == []
    other = orch._publication_metadata("other.pdf", junk)
    assert other["paper_title"] is None and other["authors"] == [] and other["publication_year"] == 2008
    orch.known_publications = None
    assert orch._publication_metadata("x.pdf", {"paper_title": "Fine Title"}) == {"paper_title": "Fine Title"}

    def boom(name: str) -> None:
        raise RuntimeError("manifest unreadable")
    orch.known_publications = boom
    assert orch._publication_metadata("x.pdf", {"paper_title": "Fine Title"}) == {"paper_title": "Fine Title"}


def test_a_harvested_paper_gets_the_registry_title_in_the_catalog_and_the_venue_in_the_manifest(tmp_path):
    junk = ProbeResult(status="ok", pages=1, page_text="body", title="f2w://path/2010-463.vp")
    service, lib = make(tmp_path, probe=FakeProbe(default=junk))
    known = {"title": "Phylogeny of the Loricariidae", "doi": "10.1/x", "year": 2010, "source": "openalex", "authors": ["A. One"],
             "venue": "Zootaxa"}
    out = service.upload("a.pdf", pdf("one"), "plecos", known=known)
    assert out["paper"]["title"] == "Phylogeny of the Loricariidae"   # not the probe's file path
    manifest = json.loads((lib / "plecos" / "manifest.json").read_text())["papers"][0]
    assert manifest["title"] == "Phylogeny of the Loricariidae" and manifest["venue"] == "Zootaxa"
    # A plain upload keeps what the probe read (no registry spoke).
    plain = service.upload("b.pdf", pdf("two"), "plecos")
    assert plain["paper"]["title"] == "f2w://path/2010-463.vp"


def test_the_recovery_job_sets_the_library_record_on_the_terms_of_each_paper_and_a_second_run_changes_nothing(tmp_path):
    write_manifest(tmp_path, [row("a", title="Phylogeny of the Loricariidae", authors=["A. One"], publication_year=2010,
                                  venue="Zootaxa", pdf_path="papers/plecos/2010/a.pdf", pdf_sha256="sha-a")])
    job = LibraryPassages.__new__(LibraryPassages)
    job.service = SimpleNamespace(_require_root=lambda: tmp_path)
    job._records, job._shas = None, {"a.pdf": "sha-a"}
    terms = [{"term": "Ancistrus", "source_papers": ["a.pdf"],
              "source_metadata": {"a.pdf": {"paper_title": "f2w://path/2010-463.vp", "authors": ["Administrator"],
                                            "publication_year": 2011, "institutions": ["A lab"]}},
              "publications": []},
             {"term": "No metadata yet", "source_papers": ["a.pdf"]}]
    assert job._correct_publication(terms, "a.pdf") == 2
    fixed = terms[0]["source_metadata"]["a.pdf"]
    assert fixed == {"paper_title": "Phylogeny of the Loricariidae", "authors": ["A. One"], "publication_year": 2010,
                     "institutions": ["A lab"], "doi": "10.1/a", "journal": "Zootaxa"}
    assert terms[0]["publications"][0]["paper_title"] == "Phylogeny of the Loricariidae"
    assert terms[1]["source_metadata"]["a.pdf"]["paper_title"] == "Phylogeny of the Loricariidae"
    assert job._correct_publication(terms, "a.pdf") == 0   # idempotent


def test_backfill_repair_replaces_a_junk_title_and_fills_venue_and_year_but_the_default_run_does_not(tmp_path):
    rows = [row("a", title="f2w://path/a.vp", authors=["Kept"], abstract="kept", publication_year=None),
            row("b", title="Real title kept", authors=["Kept"], abstract="kept", publication_year=2001, venue="Journal")]
    path = write(tmp_path, rows)
    got = {"authors": ["X"], "abstract": "y", "title": "The registry's title", "venue": "Zootaxa", "year": 2010}
    oa = Source({"10.1/a": got, "10.1/b": got})
    plain = backfill(tmp_path, openalex=oa, arxiv=Source({}), sleep=lambda s: None, apply=True)
    assert plain.counts()["already_filled"] == 2 and oa.calls == []   # without --repair nothing is asked: unchanged behaviour
    report = backfill(tmp_path, openalex=oa, arxiv=Source({}), sleep=lambda s: None, apply=True, repair=True)
    a, b = read(tmp_path)
    assert a["title"] == "The registry's title" and a["venue"] == "Zootaxa" and a["publication_year"] == 2010
    assert a["authors"] == ["Kept"] and a["abstract"] == "kept"   # never replaced
    assert b["title"] == "Real title kept" and b["publication_year"] == 2001 and b["venue"] == "Journal"
    assert oa.calls == ["10.1/a"] and report.counts()["updated"] == 1 and report.counts()["already_filled"] == 1
    assert path.exists()

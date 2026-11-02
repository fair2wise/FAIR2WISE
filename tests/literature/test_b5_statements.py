"""T29 B5: L-14 reference parsing, L-15 sections, L-16 paper context, L-17 citation links (offline)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.modules.literature import citations, paper_context, references, sections
from app.modules.literature.parsed_document import ParsedDocument, ParsedSection
from app.modules.literature.statements import Basis

TEI = b"""<?xml version="1.0"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><back><div><listBibl>
<biblStruct><analytic><title type="main">Lamellar domains in blends</title>
<author><persName><forename>Ada</forename><surname>Lovelace</surname></persName></author></analytic>
<monogr><title level="j">Macromolecules</title><imprint><biblScope unit="volume">53</biblScope>
<biblScope unit="page" from="100" to="110"/><date when="2020-05-01"/></imprint></monogr>
<idno type="DOI">https://doi.org/10.1021/ACS.MACROMOL.0C01782</idno>
<note type="raw_reference">Lovelace A. Macromolecules 53, 100 (2020).</note></biblStruct>
<biblStruct><monogr><title level="m">An unstructured entry</title></monogr></biblStruct>
</listBibl></div></back></text></TEI>"""


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def post(self, url: str, **kw: Any) -> Any:
        self.calls.append(url)

        class R:
            content = TEI

            def raise_for_status(self) -> None: ...
        return R()


def test_parse_references_keeps_unstructured_entries():
    refs = references.parse_references_tei(TEI)
    assert [r.index for r in refs] == [1, 2]
    assert refs[0].doi == "10.1021/acs.macromol.0c01782" and refs[0].year == 2020
    assert refs[0].authors == ("Ada Lovelace",) and refs[0].first_page == "100"
    assert refs[1].doi == "" and refs[1].raw == "An unstructured entry"
    assert refs[1].key.startswith("title:")


def test_grobid_off_by_default_and_cached(tmp_path: Path):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    assert references.grobid_references(pdf) is None
    with pytest.raises(ValueError):
        references.grobid_references(pdf, base_url="http://grobid.invalid")
    http = FakeHttp()
    first = references.grobid_references(pdf, base_url="http://grobid.invalid/", http=http, cache_dir=tmp_path)
    again = references.grobid_references(pdf, base_url="http://grobid.invalid", http=http, cache_dir=tmp_path)
    assert first == again and http.calls == ["http://grobid.invalid/api/processReferences"]


def test_reference_statements_are_text_grounded_and_unresolved_stay_unresolved():
    refs = references.parse_references_tei(TEI)
    stm = references.reference_statements(refs, work_id="W1", known_dois={"10.1021/acs.macromol.0c01782"})
    assert all(s.basis is Basis.TEXT_GROUNDED and s.paper_asserted for s in stm)
    assert [s.resolved for s in stm] == [True, False]
    assert stm[1].attributes["raw"] == "An unstructured entry"
    assert len({s.statement_id for s in stm}) == 2


def _parsed(parser_version: str = "1", extra: str = "") -> dict[str, Any]:
    doc = ParsedDocument(source_ref="sha256:" + "a" * 64, markdown="", num_pages=2, parser="pymupdf",
                         parser_version=parser_version, sections=(
                             ParsedSection("Intro", "Fig. 2 shows data. " + extra, page=1),
                             ParsedSection("Intro", "again"), ParsedSection("Empty", "  ")))
    return doc.to_record()


def test_section_projection_unique_keys_and_versioning():
    parsed = _parsed()
    keys = [r["key"] for r in sections.section_records(parsed)]
    assert keys == ["Intro", "Intro [2]", "Empty"]
    stm = sections.section_statements(parsed, work_id="W1")
    assert len(stm) == 2 and all(s.basis is Basis.PARSER_OUTPUT and not s.paper_asserted for s in stm)
    assert stm[0].locator is not None and stm[0].locator.page == 1
    assert stm[0].attributes["projection_section_count"] == 2
    other = sections.section_statements(_parsed("2"), work_id="W1")
    assert other[0].attributes["projection_id"] != stm[0].attributes["projection_id"]
    assert sections.section_statements(parsed, work_id="W1") == stm  # deterministic


def test_active_sections_prefers_newest_complete_projection():
    rows = [
        {"projection_id": "a", "projection_section_count": 2, "projected_at": "1", "structural_id": "s1"},
        {"projection_id": "a", "projection_section_count": 2, "projected_at": "1", "structural_id": "s0"},
        {"projection_id": "b", "projection_section_count": 2, "projected_at": "2", "structural_id": "s0"},
        {"name": "legacy"},
    ]
    assert [r["structural_id"] for r in sections.active_sections(rows)] == ["s0", "s1"]
    assert sections.active_sections([{"name": "legacy"}, rows[2]]) == [{"name": "legacy"}]


def test_paper_context():
    assert paper_context.caption_figure_number("Fig. 5 GIWAXS profiles") == "5"
    assert paper_context.caption_figure_number("The pattern in Fig. 4 shows") == ""
    text = "Intro text. As shown in Figure 12, peaks shift. Fig. 1 shows the setup. More."
    assert paper_context.citing_sentence("1", [text]) == "Fig. 1 shows the setup."
    assert paper_context.citing_sentence("12", [text]).startswith("As shown in Figure 12")
    assert paper_context.render_paper_context() == ""
    block = paper_context.render_paper_context(title="T", abstract="x" * 900, domain="OPV",
                                               techniques=list("abcdefg"), citing="Fig. 1 shows.")
    assert "x" * 500 in block and "x" * 501 not in block and "techniques a, b, c, d, e" in block
    assert paper_context.figure_publications([{"subjectId": "P", "objectId": "F"}, {"objectId": "G"}]) == {"F": "P"}
    assert paper_context.sections_by_publication([("P", "s1"), ("P", "s2")], {"s1": "t", "s2": " "}) == {"P": ["t"]}
    stm = paper_context.figure_context_statements(
        work_id="W1", source_ref="sha256:x", captions={"f1": "Fig. 1 setup", "f9": "Fig. 9 none"},
        sections={"Results": text})
    assert len(stm) == 1 and stm[0].paper_asserted and stm[0].locator is not None
    assert stm[0].locator.section == "Results"


CORPUS: list[dict[str, Any]] = [
    {"id": "P1", "doi": "10.1000/aaa", "openalex_id": "W1", "referenced_works": ["https://openalex.org/W2", "W99"]},
    {"id": "P2", "doi": "10.1000/bbb"},
]


def test_text_citations_and_unresolved():
    secs = [("P1", "Refs", "see https://doi.org/10.1000/BBB. and doi 10.1000/aaa, also 10.9999/zzz;"),
            ("P2", "Refs", "10.1000/bbb"), ("PX", "Refs", "10.1000/aaa")]
    res = citations.link_citations_from_sections(CORPUS, secs)
    cited = [s for s in res.statements if s.resolved]
    assert [(s.subject, s.object) for s in cited] == [("P1", "P2")]
    assert cited[0].basis is Basis.TEXT_GROUNDED and cited[0].attributes["via"] == "reference_text"
    unresolved = [s for s in res.statements if not s.resolved]
    assert [s.object for s in unresolved] == ["doi:10.9999/zzz"]   # never minted as a Work
    assert res.counters["self_citations_skipped"] == 2 and res.counters["sections_without_a_parent"] == 1


def test_openalex_citations_are_external_enrichment():
    corpus = [dict(CORPUS[0]), {"id": "P2", "doi": "10.1000/bbb", "openalex_id": "W2"}]
    res = citations.link_citations_from_referenced_works(corpus)
    assert [(s.subject, s.object) for s in res.statements] == [("P1", "P2")]
    assert res.statements[0].basis is Basis.EXTERNAL_ENRICHMENT and not res.statements[0].paper_asserted
    assert res.counters["references_outside_corpus"] == 1


def test_a01_citation_links_regenerate_without_the_static_supplement():
    """A-01: DOI-ingested rows get openalex ids and referenced works from fetched OpenAlex records."""
    corpus = [{"id": "P1", "doi": "10.1000/aaa"}, {"id": "P2", "doi": "10.1000/BBB"}]
    asked: list[list[str]] = []

    def fetch(dois: list[str]) -> dict[str, dict[str, Any]]:
        asked.append(dois)
        return {"10.1000/aaa": {"id": "https://openalex.org/W1", "referenced_works": ["https://openalex.org/W2"]},
                "10.1000/bbb": {"id": "https://openalex.org/W2", "referenced_works": []}}

    assert not list(Path(__file__).parents[2].rglob("openalex_supplement.json"))
    rows = citations.enrich_corpus_rows(corpus, fetch)
    assert asked == [["10.1000/aaa", "10.1000/BBB"]]
    res = citations.link_citations_from_referenced_works(rows)
    assert [(s.subject, s.object) for s in res.statements] == [("P1", "P2")]
    assert citations.enrich_corpus_rows(rows, lambda d: pytest.fail("no refetch")) == rows  # own values win

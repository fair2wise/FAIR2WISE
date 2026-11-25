"""Passage coverage on table-heavy and scanned papers (owner decision 2026-11-25, approved in this order):

1. a short sentence that states a measurement or a count may stand as a passage (``Total length 50 mm.``);
2. a quoted model sentence that differs from the page by OCR slips is matched tolerantly, and the page's own text is stored;
3. a fact that lives in a table is evidenced by its row with the column header (``kind: table_row``);
4. pages with no text layer are reported per paper.
"""
from __future__ import annotations

from app.modules.term_extractor import passages as P

MONOGRAPH_PAGE = (
    "Plecostomus affinis Steind.\n"
    "Body oblong or elongate; first scute of lower lateral series posterior to clavicle and the head is depressed.\n"
    "Total length 50 mm.\n"
    "32-34 scutes in a longitudinal series.\n"
    "Ribs present, slender; vertebrae 5 + 9 + 16 (in P. verres).\n"
    "255 -- anomalus ..............\n"
    "Hist. Fish. v. p. 356.\n"
)

TABLE_PAGE = "\n".join([
    "8/36 Neotropical Ichthyology, 22(4):e240050, 2024 ni.bio.br | scielo.br/ni Review of Loricariidae cytogenetics",
    "TABLE 1 | (Continued)",
    "Species Locality 2n Karyotype formula Reference",
    "Ancistrus cirrhosus Rio Negro 3°06'12.8\"S 51°43'53.9\"W 50 22m/sm + 28st/a Mariotto et al. (2011)",
    "Ancistrus dolichopterus Rio Xingu 2°30'S 52°10'W 52 24m/sm + 28st/a Almeida et al. (2023)",
    "Hypostomus ancistroides Rio Mogi-Guacu 21°55'S 47°50'W 68 28m/sm + 40st/a Artoni et al. (1998)",
    "Hypostomus regani Rio Parana 22°40'S 53°10'W 72 30m/sm + 42st/a Rubert et al. (2008)",
    "Hypostomus strigaticeps Rio Tiete 23°10'S 48°40'W 72 32m/sm + 40st/a Kavalco et al. (2005)",
    "Rineloricaria latirostris Rio Passa Cinco 22°25'S 47°40'W 46 20m/sm + 26st/a Alves et al. (2005)",
    "Loricariichthys platymetopon Rio Parana 22°40'S 53°10'W 54 26m/sm + 28st/a Scavone et al. (1995)",
    "Harttia loricariformis Rio Paraiba 21°50'S 42°10'W 56 28m/sm + 28st/a Centofante et al. (2006)",
    "Neoplecostomus microps Rio Paraiba 22°30'S 44°30'W 54 24m/sm + 30st/a Kavalco et al. (2005)",
])


def test_short_factual_clauses_stand_as_passages_but_name_lists_and_leaders_do_not():
    verdicts = {s.text: s.reason for s in P.page_index(MONOGRAPH_PAGE).sentences}
    assert verdicts["Total length 50 mm."] is None
    assert verdicts["32-34 scutes in a longitudinal series."] is None
    assert verdicts["Plecostomus affinis Steind."] == P.REASON_FRAGMENT       # a name, not a measurement
    assert verdicts["255 -- anomalus .............."] == P.REASON_FRAGMENT     # a table-of-contents line
    assert P.factual_clause("Total length 50 mm.") and P.factual_clause("Diploid number 2n = 54.")
    assert not P.factual_clause("A, B, C, D 1990.") and not P.factual_clause("Fish. v. p. 356.")
    found = P.best_passages([MONOGRAPH_PAGE], "Total length", limit=1)
    assert found and found[0].text == "Total length 50 mm." and found[0].kind == "located"
    found = P.best_passages([MONOGRAPH_PAGE], "scutes", limit=1)
    assert found and found[0].text.startswith("32-34 scutes")


def test_a_quoted_sentence_with_ocr_slips_is_matched_and_the_pages_own_text_is_stored():
    page = ("The karyotype of Hypostomus ancistroides has a diploid number of 2n = 74 chromosomes with a fundamental "
            "number of 108 in the population from the Mogi-Guacu river basin of southeastern Brazil.")
    ocr_page = page.replace("karyotype", "karyotvpe").replace("fundamental", "fundarnental")
    sentence = ("The karyotype of Hypostomus ancistroides has a diploid number of 2n = 74 chromosomes with a fundamental "
                "number of 108 in the population from the Mogi-Guacu river basin of southeastern Brazil.")
    assert P.find_sentence([ocr_page], sentence) is None                         # the exact match fails on the slips
    hit = P.find_sentence_tolerant([ocr_page], sentence)
    assert hit is not None and hit[0] == 0
    grounding = P.ground_context(term="Hypostomus ancistroides", sentence=sentence, source_paper="r.pdf", page=1,
                                 page_text=ocr_page, paper_pages=[ocr_page])
    assert grounding.snippet is not None and grounding.snippet.kind == "quoted" and grounding.rejected is False
    assert "karyotvpe" in grounding.snippet.text and "fundarnental" in grounding.snippet.text   # the paper's wording
    # A different sentence that shares a few words is not a match.
    other = "The diploid number of this population was not reported in the earlier study of the river basin fauna here."
    assert P.find_sentence_tolerant([ocr_page], other) is None
    assert P.find_sentence_tolerant([ocr_page], "Too short to say.") is None


def test_a_fact_in_a_table_is_evidenced_by_its_row_with_the_column_header():
    assert P.table_like_page(TABLE_PAGE) and not P.table_like_page(MONOGRAPH_PAGE)
    found = P.best_passages([TABLE_PAGE], "Hypostomus ancistroides", limit=1)
    assert found and found[0].kind == P.KIND_TABLE_ROW
    assert found[0].text == ("Species Locality 2n Karyotype formula Reference | Hypostomus ancistroides Rio Mogi-Guacu 21°55'S 47°50'W "
                             "68 28m/sm + 40st/a Artoni et al. (1998)")
    assert found[0].page == 1
    grounding = P.ground_context(term="Rineloricaria latirostris", sentence=None, source_paper="rev.pdf", page=1,
                                 page_text=TABLE_PAGE, paper_pages=[TABLE_PAGE])
    assert grounding.snippet is not None and grounding.snippet.kind == "table_row"
    # The page header glued into the first row is cut off.
    header_row = P.table_row_passage(TABLE_PAGE, TABLE_PAGE.index("Ancistrus cirrhosus"))
    assert header_row is not None and header_row[0].startswith("Species Locality 2n Karyotype formula Reference | Ancistrus cirrhosus")
    # A sentence elsewhere in the paper still wins over a row.
    prose = ("In the prose of the discussion, Hypostomus ancistroides was collected in the Mogi-Guacu river and its karyotype "
             "was described in detail by the authors of that earlier study.")
    best = P.best_passages([TABLE_PAGE, prose], "Hypostomus ancistroides", limit=2)
    assert [p.kind for p in best] == ["located", P.KIND_TABLE_ROW]


def test_the_coverage_counts_a_table_row_as_located(tmp_path):
    from app.modules.term_extractor.store import TermStore

    store = TermStore(str(tmp_path / "t.json"))
    store.note_passage("rev.pdf", "a", kind="table_row")
    store.note_passage("rev.pdf", "b", kind="located")
    store.note_passage("rev.pdf", "c", kind=None)
    row = store.run_passage_stats()["rev.pdf"]
    assert (row["with_passage"], row["located"], row["quoted"], row["uncovered"]) == (2, 2, 0, 1)

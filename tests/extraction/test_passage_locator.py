"""Supporting passages, step 1: the tolerant locator, sentence rules, extension, clause window and ranking.

Deterministic tests embed short page texts so CI exercises every rule; the real-paper tests skip when the PDFs are
absent (CI has no papers).
"""
from __future__ import annotations

from pathlib import Path
import re
import time

import pytest

from app.modules.term_extractor import passages as P

PAPER = Path(
    "/Users/david/Documents/code/fork/f2w-mateo/FAIRtoWISE-FORUM-AI/runs/rsoxs_v1_continue_remaining8/"
    "10.1038_s41467-020-14549-w.pdf"
)

LONG_BODY = (
    "The film was prepared by spin coating a chlorobenzene solution of the polymer and the acceptor onto a "
    "silicon substrate that had been cleaned and treated beforehand."
)


def mention_texts(page: str, term: str, **kw: object) -> list[str]:
    return [page[s.start : s.end] for s in P.locate(page, term, **kw)]  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------------- squash / locate


def test_squash_maps_back_to_original_offsets() -> None:
    key, idx = P.squash("R-SoXS α")
    assert key == "rsoxsalpha"
    assert idx[0] == 0 and idx[1] == 2 and idx[-1] == 7


def test_squash_ligatures_subscripts_and_fullwidth() -> None:
    assert P.squash("ﬁlm")[0] == "film"
    assert P.squash("CH₄")[0] == "ch4"
    assert P.squash("Ｘ-ray")[0] == "xray"
    assert P.squash("µm")[0] == "mum"


@pytest.mark.parametrize(
    ("page", "term"),
    [
        ("We used R-SoXS here.", "RSoXS"),  # hyphen inside
        ("We used RSoXS here.", "R-SoXS"),
        ("a thin\nfilm was grown", "thin film"),  # line break
        ("an inter-\nface layer", "interface"),  # hyphenation across lines
        ("the ﬁlm was grown", "film"),  # ligature
        ("the α phase", "alpha phase"),  # Greek
        ("the alpha phase", "α phase"),
        ("the CH₄ line", "CH4"),  # subscript
        ("the Si₃N₄ window", "Si3N4"),
        ("polymer:fullerene blends", "polymer-fullerene"),  # punctuation tolerated
        ("the soft­ware", "software"),  # soft hyphen
    ],
)
def test_locator_variants_found(page: str, term: str) -> None:
    assert P.locate(page, term)


def test_plural_tail_and_possessive() -> None:
    page = "Several EL measurements and the EL measurement's value."
    assert len(P.locate(page, "EL measurement")) == 2
    assert len(P.locate("polymer blends and boxes", "box")) == 1
    assert len(P.locate("EL measurement", "EL measurements")) == 0


def test_boundary_rule_rejects_embedded_hits() -> None:
    assert P.locate("the RSoXSy method", "RSoXS") == []
    assert P.locate("pre-RSoXS data", "RSoXS")  # a hyphen is not a glued letter
    assert P.locate("xRSoXS data", "RSoXS") == []
    assert P.locate("the ABC123 sample", "ABC12") == []


def test_short_term_needs_case_sensitive_whole_token() -> None:
    page = "The D2 value rose. Later d2 and D23 and AD2 appeared. Then (D2) again."
    assert mention_texts(page, "D2") == ["D2", "D2"]


def test_abbreviation_counts_only_where_defined() -> None:
    term = "grazing-incidence wide-angle X-ray scattering (GIWAXS)"
    defined = "We used grazing-incidence wide-angle X-ray scattering (GIWAXS) here. GIWAXS shows peaks."
    forms = [s.form for s in P.locate(defined, term)]
    assert forms == ["long", "abbr", "abbr"]  # the "(GIWAXS)" in the definition counts too
    undefined = "GIWAXS shows peaks and the GIWAXS images were dark."
    assert P.locate(undefined, term) == []  # abbreviation alone, never defined on the page
    assert [s.form for s in P.locate(undefined, term, abbr_defined=True)] == ["abbr", "abbr"]
    assert P.locate(defined, term, abbr_defined=False)[0].form == "long"
    assert len(P.locate(defined, term, abbr_defined=False)) == 1


def test_spans_carry_sentence_bounds() -> None:
    page = "First sentence is here. The RSoXS data were taken at the beamline."
    span = P.locate(page, "RSoXS")[0]
    assert page[span.sentence_start : span.sentence_end] == "The RSoXS data were taken at the beamline."


# ---------------------------------------------------------------------------------------------------- sentences


def test_sentence_boundaries_and_abbreviations() -> None:
    text = (
        "Smith et al. showed this in Fig. 3 and Eq. 2, e.g. for films, i.e. thin ones. J. K. Rowling agreed. "
        "Then it ended! Really? Yes."
    )
    cuts = [text[a:b] for a, b in P.sentence_spans(text)]
    assert cuts == [
        "Smith et al. showed this in Fig. 3 and Eq. 2, e.g. for films, i.e. thin ones.",
        "J. K. Rowling agreed.",
        "Then it ended!",
        "Really?",
        "Yes.",
    ]


def test_line_break_is_a_space_and_hyphenation_is_repaired_for_display() -> None:
    text = "The inter-\nface layer was grown\non silicon. Next sentence follows."
    a, b = P.sentence_spans(text)[0]
    shown, offsets = P.display_text(text, a, b)
    assert shown == "The interface layer was grown on silicon."
    assert text[offsets[4]] == "i" and text[offsets[-1]] == "."


def test_page_number_and_header_lines_do_not_glue_to_body() -> None:
    text = "The film was thick enough for the study\n12\nNATURE COMMUNICATIONS | (2020) 11:833 | https://doi.org/x\nBody goes on here."
    parts = [text[a:b] for a, b in P.sentence_spans(text)]
    assert "12" in parts and any(p.startswith("NATURE") for p in parts)


def test_running_footer_line_does_not_glue_to_body() -> None:
    text = "The film was thick enough for the study.\n1808279 (3 of 8)\nIt is also noted that light harvesting improves."
    parts = [text[a:b] for a, b in P.sentence_spans(text)]
    assert parts[1] == "1808279 (3 of 8)"
    assert P.sentence_verdict(parts[1]) == P.REASON_HEADER


def test_running_journal_line_inside_a_sentence_splits_it() -> None:
    text = "The domains are essentially pure due to\nAdv. Mater. 2019, 31, 1808279\nthe contrast. Next sentence follows here."
    parts = [text[a:b] for a, b in P.sentence_spans(text)]
    assert "Adv. Mater. 2019, 31, 1808279" in parts
    assert P.sentence_verdict("Adv. Mater. 2019, 31, 1808279") == P.REASON_HEADER


def test_panel_label_figure_title_is_a_caption() -> None:
    page = (
        "Device stability of nonfullerene OSCs. a) J-V characteristics of devices annealed at elevated temperatures "
        "for ten minutes. b) Normalized efficiency as a function of annealing time in the glove box."
    )
    assert [c.reason for c in P.candidates([page], "nonfullerene OSCs")] == [P.REASON_CAPTION]


# ---------------------------------------------------------------------------------------------------- verdict rules


def test_sentence_verdicts() -> None:
    good = "The scattering contrast of the blend was measured at the carbon edge with soft X-rays."
    assert P.sentence_verdict(good) is None
    assert P.sentence_verdict("Department of Chemistry, University of Bern, Freiestrasse 3, Switzerland.") == P.REASON_HEADER
    assert P.sentence_verdict("Contact the author at someone@lab.edu for the many data files that exist.") == P.REASON_HEADER
    assert P.sentence_verdict("© The Author(s) 2020 licensed under a Creative Commons licence for use.") == P.REASON_HEADER
    assert (
        P.sentence_verdict("Smith, J. K. et al. Nature 123, 456-789 (2019) about many other kinds of films.")
        == P.REASON_CITATION
    )
    assert P.sentence_verdict("12") == P.REASON_PAGE_NUMBER
    assert P.sentence_verdict("I = k sin x dx = 2 + 3 = 4 = 5 + 6 + 7 = 8.") == P.REASON_FRAGMENT
    assert P.sentence_verdict("and then the sample was measured with the beam of the line there.") == P.REASON_MID_SENTENCE
    assert P.sentence_verdict("The sample was measured with the beam of the line there and") == P.REASON_NO_END
    assert P.sentence_verdict("(1) The sample was measured with the beam of the line there.") is None
    assert P.sentence_verdict("[1] The sample was measured with the beam of the line there.") is None


def test_figure_caption_and_continuation_rejected() -> None:
    page = (
        "Fig. 2 Absorption spectra of the neat films measured under nitrogen at room temperature. "
        "The solid and dashed black lines are fits to the data using a global model of the process. "
        "The next paragraph explains the absorption spectra of the neat films in great detail for the reader, so that the "
        "reasoning behind every fit parameter is clear."
    )
    reasons = [p.reason for p in P.candidates([page], "absorption spectra")]
    assert reasons[0] == P.REASON_CAPTION
    assert [p.reason for p in P.candidates([page], "global model")] == [P.REASON_CAPTION]
    assert reasons[-1] is None


def test_references_heading_and_after_rejected() -> None:
    body = (
        "Charge transfer in the blend is ultrafast and was resolved with pump probe spectroscopy in this work, "
        "which covers several blends."
    )
    refs = "References\n1. Doe, J. Charge transfer in the blend is ultrafast. Nature 1, 2-3 (2019)."
    pages = ["Intro text. " + body, body, "More. " + body + "\n" + refs, "Smith, A. Charge transfer was studied here well."]
    got = {(p.page, p.reason) for p in P.candidates(pages, "charge transfer")}
    assert (1, None) in got and (3, None) in got
    assert (3, P.REASON_REFERENCES) in got and (4, P.REASON_REFERENCES) in got
    assert P.references_start(pages) == (2, pages[2].index("References"))
    assert P.references_start(["no heading here"]) == (1, 0)


def test_verdicts_are_cached_per_page_text() -> None:
    text = "A page of text that is long enough to be a sentence for the cache test to be meaningful."
    assert P.page_index(text) is P.page_index(text)


# ---------------------------------------------------------------------------------------------------- extension / window


def test_short_sentence_extended_with_next_passing_sentence() -> None:
    page = "R-SoXS probes the blend in transmission. " + LONG_BODY
    (passage,) = [p for p in P.candidates([page], "RSoXS")]
    assert passage.accepted and not passage.clause_window
    assert passage.text == page and 120 <= passage.length <= 400
    assert passage.page == 1 and page[passage.start : passage.end] == page


def test_short_sentence_not_extended_when_next_fails_or_too_long() -> None:
    page = "R-SoXS probes the blend today. 12 = 3 + 4 = 5 + 6 = 7 + 8 = 9."
    (passage,) = P.candidates([page], "RSoXS")
    assert passage.reason == P.REASON_SHORT
    big = "R-SoXS probes the blend today. " + ("word " * 80).strip().capitalize() + "."
    assert P.candidates([big], "RSoXS")[0].reason == P.REASON_SHORT


LONG_SENT = (
    "Grazing-incidence wide-angle X-ray scattering (GIWAXS) measurements were carried out on all the films that "
    "had been prepared on silicon substrates, which were cleaned by sonication in several solvents for ten "
    "minutes each; the resulting patterns, collected with a two-dimensional detector at a fixed incidence angle "
    "of 0.12 degrees, show that the polymer chains adopt a face-on orientation with respect to the substrate "
    "and that the acceptor forms ordered domains of modest size, as discussed in the main text."
)


def test_long_sentence_gets_clause_window() -> None:
    assert len(LONG_SENT) > 400
    (passage,) = P.candidates([LONG_SENT], "GIWAXS", abbr_defined=True)
    assert passage.accepted and passage.clause_window
    assert passage.length <= 400
    assert "GIWAXS" in passage.text
    assert LONG_SENT[passage.start : passage.end].replace("\n", " ") == passage.text
    # never cuts inside a word: both ends are at word edges of the original
    assert passage.start == 0 or LONG_SENT[passage.start - 1] == " "
    assert passage.end == len(LONG_SENT) or LONG_SENT[passage.end] in " ,;:)"


def test_clause_window_keeps_mention_clause_and_cuts_at_clauses() -> None:
    shown = ("alpha beta gamma, " * 12) + "the key term sits here, and more words follow after it; " + ("delta epsilon, " * 20)
    s = shown.index("key term")
    a, b = P.clause_window(shown, s, s + 8)
    window = shown[a:b]
    assert "the key term sits here" in window and b - a <= 400
    assert window[0] != " " and window[-1] != " "


def test_clause_window_never_opens_with_a_bare_connector() -> None:
    shown = ("Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau, " * 4
             + "and which the key term sits here in the middle of the sentence, that we measured twice, "
             + "While more words follow after it and after the rest of the long sentence goes on, " * 3)
    s = shown.index("key term")
    a, b = P.clause_window(shown, s, s + 8, limit=120)
    window = shown[a:b]
    assert "key term" in window and b - a <= 120
    assert not re.match(r"(?i)(and|but|which|that|whereas|while)\b", window)


def test_clause_window_lowercase_start_stays_within_limit() -> None:
    shown = "Intro words here, some more words follow, the key term sits here, and then the sentence closes now."
    s = shown.index("key term")
    a, b = P.clause_window(shown, s, s + 8, limit=40)
    assert shown[a:b] == "the key term sits here,"  # moving back would exceed the limit, so the start stays


def test_clause_window_single_huge_clause_cuts_on_words() -> None:
    shown = " ".join(f"word{i}" for i in range(200))
    s = shown.index("word100")
    a, b = P.clause_window(shown, s, s + 7)
    assert b - a <= 400 and "word100" in shown[a:b]
    assert shown[a - 1] == " " or a == 0
    assert shown[b] == " " or b == len(shown)


# ---------------------------------------------------------------------------------------------------- ranking


def test_ranking_prefers_band_definition_words_position_then_page() -> None:
    inband_def = (
        "RSoXS is defined as resonant soft X-ray scattering and is used to measure phase composition in thin films "
        "of organic blends."
    )
    inband_plain = "Measurements with RSoXS were carried out on all of the polymer blend films prepared for this particular study of ours at the lab."
    near_start = "RSoXS data were collected from every one of the samples that were prepared for this particular study of ours here at the lab."
    pages = [inband_plain, inband_def, near_start]
    best = P.best_passages(pages, "RSoXS", limit=3)
    assert [p.page for p in best] == [2, 3, 1]
    assert all(p.kind == "located" and not p.clause_window for p in best)
    assert len(P.best_passages(pages, "RSoXS", limit=1)) == 1


def test_ranking_prefers_120_300_over_longer_and_earlier_page_last_tiebreak() -> None:
    long_one = "X " * 0 + "RSoXS " + "was used on the film " * 16 + "in this study."
    assert 300 < len(long_one) <= 400
    short_ok = "Measurements with RSoXS were carried out on all of the polymer blend films prepared for this study of ours here at the lab."
    best = P.best_passages([long_one, short_ok], "RSoXS")
    assert [p.page for p in best] == [2, 1]
    same = [short_ok, short_ok.replace("study", "work")]
    assert [p.page for p in P.best_passages(same, "RSoXS")] == [1, 2]


def test_candidates_report_rejected_with_reason_and_not_found_is_empty() -> None:
    pages = ["A short RSoXS line is all there is on this page.", "Nothing relevant on this page at all."]
    cands = P.candidates(pages, "RSoXS")
    assert [(c.page, c.reason) for c in cands] == [(1, P.REASON_SHORT)]
    assert P.candidates(pages, "P3HT:PCBM") == []
    assert P.best_passages(pages, "RSoXS") == []


# ---------------------------------------------------------------------------------------------------- design table (embedded)


def test_design_table_rules_on_embedded_text() -> None:
    abstract = (
        "Organic photovoltaics based on non-fullerene acceptors (NFAs) show record efficiency of 16 to 17% and "
        "increased photovoltage owing to the low driving force for interfacial charge transfer."
    )
    header = "Nature Communications | https://doi.org/10.1038/s41467-020-14549-w"
    refs = "References\n1. Cheng, P., Li, G., Zhan, X. & Yang, Y. Next-generation PCBM based thin film cells. Nat. Photon. 12, 131-142 (2018)."
    pages = [header + "\n" + abstract, "Body text on a middle page goes here.\n" + refs]
    assert P.best_passages(pages, "organic photovoltaics")[0].page == 1
    assert all(c.reason == P.REASON_HEADER for c in P.candidates(pages, "Nature Communications"))
    pcbm = P.candidates(pages, "PCBM")
    assert pcbm and all(c.reason == P.REASON_REFERENCES for c in pcbm)
    assert P.candidates(pages, "denoising diffusion probabilistic model (DDPM)") == []


# ---------------------------------------------------------------------------------------------------- real paper


@pytest.fixture(scope="module")
def paper_pages() -> list[str]:
    if not PAPER.exists():
        pytest.skip(f"real paper not available: {PAPER}")
    return P.page_texts(PAPER)


def accepted_pages(pages: list[str], term: str) -> list[int]:
    return [c.page for c in P.candidates(pages, term) if c.accepted]


def reasons(pages: list[str], term: str) -> set[str | None]:
    return {c.reason for c in P.candidates(pages, term)}


def test_real_page_texts_are_the_extractor_text(paper_pages: list[str]) -> None:
    from app.modules.literature.pdf_reader import open_pdf

    doc = open_pdf(PAPER)
    try:
        assert paper_pages == [doc.load_page(i).get_text() for i in range(doc.page_count)]
        assert P.page_texts(doc) == paper_pages  # accepts an open document
    finally:
        doc.close()
    assert len(paper_pages) == 10


def test_real_accepted_terms(paper_pages: list[str]) -> None:
    opv = [c for c in P.candidates(paper_pages, "organic photovoltaics") if c.accepted and c.page == 2]
    assert opv and opv[0].length == 181 and opv[0].text.startswith("Appropriately selected D:A combinations")
    assert 8 in accepted_pages(paper_pages, "GIWAXS")
    rsoxs = P.candidates(paper_pages, "RSoXS")
    assert any(c.accepted and c.page == 8 and "R-SoXS" in c.text for c in rsoxs)
    joined = [c for c in rsoxs if c.accepted and c.page == 8 and c.length == 280]
    assert joined and joined[0].text.count(". ") >= 1  # two sentences joined (first one is short)
    pf = P.candidates(paper_pages, "polymer-fullerene")
    assert any(c.accepted and c.page == 6 and c.length == 262 and "polymer:fullerene" in c.text for c in pf)
    a = [c.text for c in P.best_passages(paper_pages, "EL measurement")]
    b = [c.text for c in P.best_passages(paper_pages, "EL measurements")]
    assert a == b and a and P.best_passages(paper_pages, "EL measurements")[0].page == 7


def test_real_long_form_gives_clause_window(paper_pages: list[str]) -> None:
    term = "grazing-incidence wide-angle X-ray scattering (GIWAXS)"
    windows = [c for c in P.candidates(paper_pages, term) if c.accepted and c.clause_window]
    assert windows and all(c.length <= 400 and "X-ray scattering" in c.text for c in windows)


@pytest.mark.parametrize("term", ["PCBM", "nanostructure", "polymer blend", "thin film"])
def test_real_references_only_terms_are_rejected(paper_pages: list[str], term: str) -> None:
    got = P.candidates(paper_pages, term)
    assert got and {c.reason for c in got} == {P.REASON_REFERENCES}


def test_real_header_and_boilerplate_terms_are_rejected(paper_pages: list[str]) -> None:
    got = P.candidates(paper_pages, "Nature Communications")
    assert {c.reason for c in got} <= {P.REASON_HEADER, P.REASON_REFERENCES}
    assert sum(c.reason == P.REASON_HEADER for c in got) >= 10
    syn = P.candidates(paper_pages, "synchrotron")
    assert syn and not any(c.accepted for c in syn)


@pytest.mark.parametrize("term", ["P3HT:PCBM", "CH4", "denoising diffusion probabilistic model (DDPM)"])
def test_real_absent_terms_not_found(paper_pages: list[str], term: str) -> None:
    assert P.candidates(paper_pages, term) == []


def test_real_timing(paper_pages: list[str]) -> None:
    P.page_index.cache_clear()
    t0 = time.perf_counter()
    for text in paper_pages:
        P.page_index(text)
    build = time.perf_counter() - t0
    assert build < 0.5, build
    terms = ["RSoXS", "GIWAXS", "charge transfer", "polymer:fullerene", "EL measurement", "D2", "film"] * 286
    t0 = time.perf_counter()
    for term in terms[:2000]:
        P.candidates(paper_pages, term)
    assert time.perf_counter() - t0 < 5.0


# ---------------------------------------------------------------------------------------------------- grounding


def test_ground_context_quoted_located_and_rejected() -> None:
    pages = ["Opening words only on the first page of the paper.",
             "The scattering vector is the momentum transfer of the photon in the sample. Further words follow here, and they run on "
             "for a good while so that the pair of sentences is long enough to count."]
    claimed = "The scattering  vector is the momentum transfer of the photon in the sample."
    quoted = P.ground_context(term="scattering vector", sentence=claimed, source_paper="a.pdf", page=1,
                              page_text=pages[0], paper_pages=pages)
    assert quoted.snippet is not None and not quoted.rejected
    assert (quoted.snippet.kind, quoted.snippet.verified, quoted.snippet.page) == ("quoted", True, 2)
    wrong = P.ground_context(term="scattering vector", sentence="Something the paper never says about anything here.",
                             source_paper="a.pdf", page=1, page_text=pages[0], paper_pages=pages)
    assert wrong.rejected and wrong.snippet is not None and wrong.snippet.kind == "located" and wrong.snippet.page == 2
    nothing = P.ground_context(term="unseen", sentence="Nothing at all of this is in the paper text.",
                               source_paper="a.pdf", page=1, page_text=pages[0], paper_pages=pages)
    assert nothing.rejected and nothing.snippet is None
    none_given = P.ground_context(term="unseen", sentence=None, source_paper="a.pdf", page=1, page_text=pages[0],
                                  paper_pages=pages)
    assert not none_given.rejected and none_given.snippet is None


def test_ground_context_prefers_the_page_it_came_from_for_the_located_fallback() -> None:
    sentence = "RSoXS measurements were performed at the beamline with a calibrated detector in transmission mode at room temperature, after the films were aligned."
    pages = [sentence, "Later " + sentence]
    got = P.ground_context(term="RSoXS", sentence=None, source_paper="a.pdf", page=2, page_text=pages[1],
                           paper_pages=pages)
    assert got.snippet is not None and got.snippet.page == 2
    assert [p.page for p in P.best_passages(pages, "RSoXS", only_pages=[0])] == [1]

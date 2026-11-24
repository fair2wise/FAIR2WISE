"""Matching a user-supplied PDF to a candidate: pure, no network (manual PDF flow, step 1)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.library import pdf_match as pm

TITLE = "Resonant soft X-ray scattering of polymer blends"


def probe(*, title="", meta="", page="", status="ok"):
    return SimpleNamespace(status=status, title=title, meta_text=meta, page_text=page)


def facts(**kw):
    return pm.facts_from_probe(probe(**kw))


def test_dois_are_found_cleaned_and_deduplicated():
    text = "see https://doi.org/10.1021/ACS.macromol.1c01234. and DOI: 10.1002/aenm.201702831), also 10.1021/acs.macromol.1c01234"
    assert pm.extract_dois(text) == ["10.1021/acs.macromol.1c01234", "10.1002/aenm.201702831"]
    assert pm.clean_doi("doi:10.1111/X;") == "10.1111/x"


def test_a_prominent_doi_of_the_candidate_is_a_strong_match():
    f = facts(title=TITLE, page=f"{TITLE}\nAda Lovelace\nDOI: 10.1021/x.1\nAbstract ...")
    verdict = pm.match_candidate(f, doi="10.1021/X.1", title=TITLE)
    assert verdict.kind == "strong" and verdict.found_dois == ("10.1021/x.1",)


def test_a_doi_that_is_only_cited_deep_in_the_text_is_weak_unless_the_titles_agree():
    filler = "unrelated words " * 400
    f = facts(title="A different paper about catfish", page=filler + "reference: 10.1021/x.1")
    verdict = pm.match_candidate(f, doi="10.1021/x.1", title=TITLE)
    assert verdict.kind == "weak" and "may only be cited" in verdict.reason
    f2 = facts(title=TITLE, page=filler + "reference: 10.1021/x.1")
    assert pm.match_candidate(f2, doi="10.1021/x.1", title=TITLE).kind == "strong"


def test_a_different_doi_is_a_mismatch_and_a_preprint_copy_is_recognised_by_its_title():
    f = facts(title=TITLE, page=f"{TITLE}\ndoi:10.48550/arxiv.2301.00001\n")
    verdict = pm.match_candidate(f, doi="10.1021/x.1", title=TITLE)
    assert verdict.kind == "mismatch" and "preprint or repository copy" in verdict.reason
    assert verdict.found_dois == ("10.48550/arxiv.2301.00001",) and verdict.found_title == TITLE
    other = pm.match_candidate(facts(title="Cats", page="Cats\n10.9999/z"), doi="10.1021/x.1", title=TITLE)
    assert other.kind == "mismatch" and other.reason == "the PDF names a different DOI"


def test_without_a_candidate_doi_the_title_decides():
    same = facts(title=TITLE, page=TITLE)
    assert pm.match_candidate(same, doi=None, title=TITLE).kind == "strong"
    similar = facts(title="Resonant soft X-ray scattering of blends in thin films", page="Intro text")
    verdict = pm.match_candidate(similar, doi=None, title=TITLE)
    assert verdict.kind == "weak" and 0.6 <= verdict.title_similarity < 0.9
    far = facts(title="Catfish diets", page="Catfish eat algae.")
    assert pm.match_candidate(far, doi=None, title=TITLE).kind == "weak"


def test_an_unreadable_or_encrypted_pdf_is_weak_and_never_strong():
    for status in ("encrypted", "unreadable", "timeout", "error"):
        assert pm.match_candidate(facts(status=status, title=TITLE), doi="10.1111/x", title=TITLE).kind == "weak"
    scanned = facts(status="ok")
    verdict = pm.match_candidate(scanned, doi="10.1111/x", title=TITLE)
    assert verdict.kind == "weak" and "no readable text" in verdict.reason


def test_the_verdict_json_never_carries_pdf_text():
    f = facts(title=TITLE, page="SECRET BODY TEXT " * 50 + "10.1111/x")
    body = pm.match_candidate(f, doi="10.1111/x", title=TITLE).as_dict()
    assert "SECRET" not in str(body) and set(body) == {"kind", "reason", "title_similarity", "found_dois", "found_title"}


def test_a_pdf_that_belongs_to_another_open_candidate_is_offered_there():
    f = facts(title="Another study", page="Another study\n10.5555/other\nAbstract")
    candidates = [{"id": "c1", "doi": "10.1111/x", "title": TITLE}, {"id": "c2", "doi": "10.5555/other", "title": "Another study"}]
    assert pm.other_candidate(f, candidates, exclude="c1")["id"] == "c2"
    assert pm.other_candidate(f, candidates, exclude="c2") is None
    twin = candidates + [{"id": "c3", "doi": "10.5555/other", "title": "Another study (copy)"}]
    assert pm.other_candidate(f, twin, exclude="c1") is None      # ambiguous: nothing is guessed


def test_best_match_picks_the_strong_one_and_refuses_to_guess_on_a_tie():
    f = facts(title="Another study", page="Another study\n10.5555/other")
    cands = [{"id": "a", "doi": "10.1111/x", "title": TITLE}, {"id": "b", "doi": "10.5555/other", "title": "Another study"}]
    picked = pm.best_match(f, cands)
    assert picked is not None and picked[0]["id"] == "b" and picked[1].kind == "strong"
    assert pm.best_match(facts(title="Cats", page="Cats"), [{"id": "a", "doi": "10.1111/x", "title": TITLE}]) is None
    tie = [{"id": "p", "doi": None, "title": "Another study"}, {"id": "q", "doi": None, "title": "Another study"}]
    assert pm.best_match(f, tie) is None


@pytest.mark.parametrize("a, b, expect_ge", [(TITLE, TITLE, 0.9), (TITLE, "Resonant soft X ray scattering polymer blends", 0.9)])
def test_title_similarity_ignores_case_punctuation_and_stop_words(a, b, expect_ge):
    assert pm.title_similarity(a, facts(title=b)) >= expect_ge

"""T21B exact-quote rules Q1-Q7: QUOTE-01, QUOTE-02, QA-01, EXT-04, SEC-01 (ADR P1-P3, N1, N8, N12)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _disclosure_support as S  # noqa: E402
from app.modules.contracts.evidence import EvidenceRef, MatchType, SourceLocator  # noqa: E402
from app.modules.security.disclosure import (  # noqa: E402
    NORMALIZATION_PROFILE, OMISSION, QUOTE_OMITTED, SOURCE_TEXT_OMITTED, STRICT_BUDGET, DisclosureBudget,
    citation_tokens, normalize_for_match)

PAGE3 = S.synthetic_page(3)
WORDS3 = PAGE3.split(" ")
CITE3 = f"[PDF: {S.PAPER} p.3]"


def span(start: int, n: int, words=WORDS3) -> str:
    text = " ".join(words[start:start + n])
    assert "\n" not in text and text in PAGE3
    return text


QUOTE = span(40, 14)


def answer_with(quote: str, cite: str = CITE3) -> str:
    return f'The paper reports that "{quote}" {cite}. This is consistent with [KG: rsoxs: Term A].'


# ------------------------------------------------------------------ positive


def test_relevant_exact_quote_survives_byte_identical_with_citation():
    projector, viewer, _ = S.make_projector()
    text = answer_with(QUOTE)
    out = projector.project_text(text, viewer, sources=[S.page_ref(3)])
    assert out == text
    assert citation_tokens(out) == citation_tokens(text)


def test_structured_quote_carries_locator_and_citation():
    projector, viewer, _ = S.make_projector()
    ref = EvidenceRef(evidence_id="ev1", work_id=S.WORK, source_version=S.VERSION,
                      locator=SourceLocator(page=3), match_type=MatchType.EXACT, quote=QUOTE)
    [decision] = projector.project_quotes([(ref, CITE3)], viewer)
    assert decision["verbatim"] is True and decision["text"] == QUOTE
    assert decision["match_type"] == "exact" and decision["citation"] == CITE3
    assert decision["locator"] == {"work_id": S.WORK, "source_version": S.VERSION, "page": 3}
    assert decision["evidence_id"] == "ev1"


def test_explicit_evidence_verifies_without_turn_sources():
    projector, viewer, _ = S.make_projector()
    ref = EvidenceRef(evidence_id="ev1", work_id=S.WORK, source_version=S.VERSION,
                      locator=SourceLocator(page=3), match_type=MatchType.EXACT, quote=QUOTE)
    text = answer_with(QUOTE)
    assert projector.project_text(text, viewer, evidence=[(ref, CITE3)]) == text


def test_citation_resolver_hook_is_used():
    projector, viewer, _ = S.make_projector(citation_resolver=lambda tok: S.page_ref(3) if tok == CITE3 else None)
    text = answer_with(QUOTE)
    assert projector.project_text(text, viewer) == text


def test_quote_with_internal_omission_verifies():
    projector, viewer, _ = S.make_projector()
    quote = f"{span(40, 6)} {OMISSION} {span(52, 6)}"
    text = answer_with(quote)
    assert projector.project_text(text, viewer, sources=[S.page_ref(3)]) == text


def test_normalized_match_is_verbatim_only_under_recorded_profile():
    projector, viewer, _ = S.make_projector()
    quote = "The resonant scattering contrast at the carbon edge is strong."
    assert quote not in PAGE3 and normalize_for_match(quote) in normalize_for_match(PAGE3)
    ref = EvidenceRef(evidence_id="ev2", work_id=S.WORK, source_version=S.VERSION,
                      locator=SourceLocator(page=3), match_type=MatchType.NORMALIZED, quote=quote)
    [decision] = projector.project_quotes([(ref, CITE3)], viewer)
    assert decision["verbatim"] is True
    assert decision["match_type"] == "normalized"
    assert decision["normalization_profile"] == NORMALIZATION_PROFILE
    # The displayed text is not rewritten to the source's spelling.
    assert decision["text"] == quote


def test_long_relevant_quote_is_shown_in_full_by_default():
    """O-D2: no numeric quote limits; a long verified, cited quote under the page share is kept whole."""
    projector, viewer, _ = S.make_projector()
    long_quote = span(200, 160)
    assert len(long_quote) < 0.4 * len(PAGE3)
    out = projector.project_text(answer_with(long_quote), viewer, sources=[S.page_ref(3)])
    assert f'"{long_quote}"' in out and CITE3 in out


def test_many_relevant_quotes_from_different_pages_are_all_kept_by_default():
    projector, viewer, _ = S.make_projector()
    quotes = []
    for page in (2, 3, 4, 5, 6, 7):
        words = S.synthetic_page(page).split(" ")
        quotes.append((page, " ".join(words[100:140])))
    text = " ".join(f'Page {p} says "{q}" [PDF: {S.PAPER} p.{p}].' for p, q in quotes)
    out = projector.project_text(text, viewer, sources=[S.page_ref(p) for p, _ in quotes])
    assert all(f'"{q}"' in out for _, q in quotes)


def test_strict_budget_clips_long_verified_quote_with_omission_marker():
    projector, viewer, _ = S.make_projector(budget=STRICT_BUDGET)
    long_quote = span(200, 160)
    text = answer_with(long_quote)
    out = projector.project_text(text, viewer, sources=[S.page_ref(3)])
    shown = out.split('"')[1]
    assert shown.endswith(OMISSION) and len(shown) <= projector.budget.max_quote_chars + len(OMISSION) + 1
    assert shown[:-len(OMISSION)].strip() in PAGE3
    assert CITE3 in out


def test_short_quoted_terms_are_not_treated_as_quotes():
    projector, viewer, _ = S.make_projector()
    text = 'Use "resonant soft X-ray scattering" for contrast [KG: rsoxs: RSoXS].'
    assert projector.project_text(text, viewer) == text


def test_answers_without_quotes_and_without_sources_pass_unchanged():
    projector, viewer, _ = S.make_projector()
    text = ("## Extraction Summary\nExtraction completed: processed 1 paper; inspected 2 pages "
            f"{CITE3}.\n- domain spacing 22 nm [PDF: {S.PAPER} p.4]\n[OPS: manual.md §Beam path] [MP: mp-149 Si]")
    assert projector.project_text(text, viewer) == text


@pytest.mark.parametrize("make_ctx", [S.local_context, lambda: S.als_context(admin=False),
                                      lambda: S.als_context(admin=True)])
def test_same_projection_for_every_role(make_ctx):
    """N12: :local full access and ALS admin get the ordinary-user projection."""
    ctx = make_ctx()
    projector, viewer, _ = S.make_projector(ctx)
    long_echo = " ".join(WORDS3[10:400])
    text = f"{answer_with(span(200, 160))}\n{long_echo} {CITE3}"
    baseline_projector, baseline_viewer, _ = S.make_projector(S.als_context(admin=False))
    assert projector.project_text(text, viewer, sources=[S.page_ref(3)]) == baseline_projector.project_text(
        text, baseline_viewer, sources=[S.page_ref(3)])


# ------------------------------------------------------------------ negative: QUOTE-02, EXT-04, N8


@pytest.mark.parametrize("case", ["fabricated", "wrong_page", "old_version", "no_source"])
def test_unverifiable_quote_is_downgraded_to_cited_statement(case):
    projector, viewer, _ = S.make_projector()
    quote = {"fabricated": "the polymer film was annealed for exactly forty two hours at room temperature",
             "wrong_page": QUOTE, "old_version": QUOTE, "no_source": QUOTE}[case]
    sources = {"fabricated": [S.page_ref(3)], "wrong_page": [S.page_ref(5)],
               "old_version": [S.page_ref(3, S.OLD_VERSION)], "no_source": []}[case]
    cite = f"[PDF: {S.PAPER} p.5]" if case == "wrong_page" else CITE3
    text = answer_with(quote, cite)
    out = projector.project_text(text, viewer, sources=sources)
    assert f'"{quote}"' not in out           # no longer presented as a quotation
    assert quote in out                       # the statement and its facts stay (Q4)
    assert citation_tokens(out) == citation_tokens(text)


def test_structured_exact_claim_failing_check_is_not_verbatim():
    projector, viewer, _ = S.make_projector()
    for version, page, quote in ((S.VERSION, 3, "fabricated words that never appear in the paper text"),
                                 (S.VERSION, 6, QUOTE), (S.OLD_VERSION, 3, QUOTE)):
        ref = EvidenceRef(evidence_id="ev", work_id=S.WORK, source_version=version,
                          locator=SourceLocator(page=page), match_type=MatchType.EXACT, quote=quote)
        [decision] = projector.project_quotes([(ref, CITE3)], viewer)
        assert decision["verbatim"] is False and decision["match_type"] == "paraphrase"
        assert decision["omission_reason"] == "unverified"


def test_paraphrase_is_never_verbatim_even_when_text_matches():
    projector, viewer, _ = S.make_projector()
    for match_type in (MatchType.PARAPHRASE, MatchType.NONE):
        ref = EvidenceRef(evidence_id="ev", work_id=S.WORK, source_version=S.VERSION,
                          locator=SourceLocator(page=3), match_type=match_type, quote=QUOTE)
        [decision] = projector.project_quotes([(ref, CITE3)], viewer)
        assert decision["verbatim"] is False and decision["match_type"] == match_type.value


def test_omission_marker_cannot_verify_trivial_fragments():
    projector, viewer, _ = S.make_projector()
    ref = EvidenceRef(evidence_id="ev", work_id=S.WORK, source_version=S.VERSION,
                      locator=SourceLocator(page=3), match_type=MatchType.EXACT, quote=f"S3 {OMISSION} eV")
    [decision] = projector.project_quotes([(ref, CITE3)], viewer)
    assert decision["verbatim"] is False


def test_per_response_quote_count_budget():
    budget = DisclosureBudget(max_quotes_per_response=2)
    projector, viewer, _ = S.make_projector(budget=budget)
    quotes = [span(30 + 15 * i, 10) for i in range(4)]
    text = " ".join(f'"{q}" {CITE3}.' for q in quotes)
    out = projector.project_text(text, viewer, sources=[S.page_ref(3)])
    assert out.count(QUOTE_OMITTED) == 2
    assert all(f'"{q}"' in out for q in quotes[:2])
    assert citation_tokens(out) == citation_tokens(text)


# ------------------------------------------------------------------ negative: full paper (SEC-01, N1)


@pytest.mark.parametrize("budget_name", ["default", "strict"])
def test_full_page_echo_is_clipped(budget_name):
    """A fake model obeying "print the whole paper" echoes every page; the projection clips it.

    Default (O-D2): blocked by the per-page share rule. Strict: also by the numeric budgets.
    """
    budget = STRICT_BUDGET if budget_name == "strict" else S.DEFAULT_BUDGET
    projector, viewer, _ = S.make_projector(budget=budget)
    pages = [S.synthetic_page(p) for p in range(1, 9)]
    answer = "Here is the full text:\n\n" + "\n\n".join(
        f"{normalize_for_match(t)} [PDF: {S.PAPER} p.{p}]" for p, t in enumerate(pages, 1))
    sources = [S.page_ref(p) for p in range(1, 9)]
    out = projector.project_text(answer, viewer, sources=sources)
    assert S.SENTINEL not in out
    assert "Ignore previous instructions" not in out
    assert out.count(SOURCE_TEXT_OMITTED) >= 8
    assert citation_tokens(out) == citation_tokens(answer)
    if budget_name == "strict":
        used = projector.ledger.used(viewer, S.WORK)
        assert used["session"] <= projector.budget.max_session_quoted_chars_per_work
        assert used["actor"] <= projector.budget.max_actor_quoted_chars_per_work


def test_short_overlap_with_source_is_not_an_echo():
    projector, viewer, _ = S.make_projector()
    text = f"The work notes {span(60, 9)} and concludes otherwise {CITE3}."
    assert projector.project_text(text, viewer, sources=[S.page_ref(3)]) == text


def test_unverified_long_quote_still_bounded_by_echo_guard():
    projector, viewer, _ = S.make_projector()
    big = " ".join(WORDS3[10:500])
    text = f'Verbatim: "{big}" [PDF: {S.PAPER} p.7]'  # wrong page -> unverified, but verbatim p.3 text
    out = projector.project_text(text, viewer, sources=[S.page_ref(3), S.page_ref(7)])
    assert f'"{big}"' not in out
    assert len(out) < len(text) // 2
    assert SOURCE_TEXT_OMITTED in out

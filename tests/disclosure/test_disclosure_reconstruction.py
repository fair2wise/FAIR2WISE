"""T21B sequential reconstruction guard: SEC-05, QUOTE-02 (ADR Q5, Q6, N3).

A scripted attacker asks for adjacent spans of one paper, one request at a
time, in one session, across sessions and through chat answers, model-proxy
answers and structured quotes. Cumulative distinct verbatim chars per work stay
within the session/actor budget; later quotes become citations only.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _disclosure_support as S  # noqa: E402
from app.modules.contracts.evidence import EvidenceRef, MatchType, SourceLocator  # noqa: E402
from app.modules.security.disclosure import (  # noqa: E402
    QUOTE_OMITTED, SOURCE_TEXT_OMITTED, DisclosureBudget, DisclosureLedger, Viewer, normalize_for_match)

PAGES = {p: S.synthetic_page(p) for p in range(1, 9)}
CITE = "[PDF: {} p.{}]"


def adjacent_spans(page: int, n_words: int = 14, count: int = 50):
    """Consecutive non-overlapping verbatim spans of ``page`` (skipping the hyphenated line)."""
    words = [w for w in PAGES[page].split(" ")]
    out, i = [], 0
    while len(out) < count and i + n_words <= len(words):
        text = " ".join(words[i:i + n_words])
        if "\n" not in text:
            out.append(text)
        i += n_words
    return out


def shown_quotes(text: str):
    return [part for k, part in enumerate(text.split('"')) if k % 2 == 1]


def test_fifty_adjacent_span_requests_in_one_session_are_capped():
    budget = DisclosureBudget()
    projector, viewer, _ = S.make_projector(budget=budget)
    disclosed, refused = 0, 0
    spans = []
    for page in (3, 4, 5, 6):
        spans += [(page, s) for s in adjacent_spans(page)]
    spans = spans[:50 * 4]
    for page, quote in spans:
        text = f'Next part: "{quote}" {CITE.format(S.PAPER, page)}'
        out = projector.project_text(text, viewer, sources=[S.page_ref(page)])
        if QUOTE_OMITTED in out:
            refused += 1
        else:
            assert shown_quotes(out) == [quote]
            disclosed += len(quote)
    used = projector.ledger.used(viewer, S.WORK)
    assert used["session"] <= budget.max_session_quoted_chars_per_work
    assert disclosed <= budget.max_session_quoted_chars_per_work + budget.max_quote_chars
    assert refused > len(spans) // 2
    total_text = sum(len(normalize_for_match(t)) for t in PAGES.values())
    assert disclosed / total_text < 0.1


def test_new_sessions_do_not_reset_the_actor_budget():
    budget = DisclosureBudget()
    ledger = DisclosureLedger(budget)
    disclosed = 0
    spans = [(p, s) for p in range(1, 9) for s in adjacent_spans(p)]
    for i, (page, quote) in enumerate(spans):
        ctx = S.als_context(admin=False, session_id=f"sess-{i}", request_id=f"req-{i}")
        projector, viewer, _ = S.make_projector(ctx, budget=budget, ledger=ledger)
        out = projector.project_text(f'"{quote}" {CITE.format(S.PAPER, page)}', viewer,
                                     sources=[S.page_ref(page)])
        if QUOTE_OMITTED not in out:
            disclosed += len(quote)
    assert disclosed <= budget.max_actor_quoted_chars_per_work
    assert ledger.used(viewer, S.WORK)["actor"] <= budget.max_actor_quoted_chars_per_work


def test_repeating_the_same_quote_costs_nothing_extra():
    projector, viewer, _ = S.make_projector()
    quote = adjacent_spans(3)[2]
    for _ in range(30):
        out = projector.project_text(f'"{quote}" {CITE.format(S.PAPER, 3)}', viewer, sources=[S.page_ref(3)])
        assert shown_quotes(out) == [quote]
    assert projector.ledger.used(viewer, S.WORK)["session"] <= len(quote)


def test_other_actors_have_independent_budgets():
    budget = DisclosureBudget(max_session_quoted_chars_per_work=300, max_actor_quoted_chars_per_work=300)
    ledger = DisclosureLedger(budget)
    quotes = adjacent_spans(3)[:8]
    for subject in ("alice", "bob"):
        ctx = S.als_context(admin=False, subject=subject)
        projector, viewer, _ = S.make_projector(ctx, budget=budget, ledger=ledger)
        outs = [projector.project_text(f'"{q}" {CITE.format(S.PAPER, 3)}', viewer, sources=[S.page_ref(3)])
                for q in quotes]
        assert QUOTE_OMITTED not in outs[0]
        assert QUOTE_OMITTED in outs[-1]


def test_window_expiry_restores_budget():
    now = [1000.0]
    budget = DisclosureBudget(max_session_quoted_chars_per_work=200, max_actor_quoted_chars_per_work=200,
                              window_seconds=3600)
    ledger = DisclosureLedger(budget, clock=lambda: now[0])
    projector, viewer, _ = S.make_projector(budget=budget, ledger=ledger)
    quotes = adjacent_spans(3)[:5]
    outs = [projector.project_text(f'"{q}" {CITE.format(S.PAPER, 3)}', viewer, sources=[S.page_ref(3)])
            for q in quotes]
    assert QUOTE_OMITTED in outs[-1]
    now[0] += 3601
    out = projector.project_text(f'"{quotes[-1]}" {CITE.format(S.PAPER, 3)}', viewer, sources=[S.page_ref(3)])
    assert QUOTE_OMITTED not in out


def test_unquoted_adjacent_chunk_enumeration_is_capped():
    """A model that answers each "continue" with the next unquoted 400-char chunk."""
    budget = DisclosureBudget()
    projector, viewer, _ = S.make_projector(budget=budget)
    norm = normalize_for_match(PAGES[3]) + " " + normalize_for_match(PAGES[4])
    chunks = [norm[i:i + 400] for i in range(0, len(norm) - 400, 400)]
    leaked = 0
    for k, chunk in enumerate(chunks):
        start, end = chunk.find(" ") + 1, chunk.rfind(" ")
        body = chunk[start:end]
        out = projector.project_text(f"Continuing ({k}): {body} [PDF: {S.PAPER} p.3]", viewer,
                                     sources=[S.page_ref(3), S.page_ref(4)])
        if SOURCE_TEXT_OMITTED not in out:
            leaked += len(body)
    assert leaked <= budget.max_session_quoted_chars_per_work + 400
    assert projector.ledger.used(viewer, S.WORK)["session"] <= budget.max_session_quoted_chars_per_work


def test_structured_quotes_share_the_ledger_with_answers():
    budget = DisclosureBudget(max_session_quoted_chars_per_work=250, max_actor_quoted_chars_per_work=250)
    projector, viewer, _ = S.make_projector(budget=budget)
    quotes = adjacent_spans(3)[:6]
    for q in quotes[:2]:
        projector.project_text(f'"{q}" {CITE.format(S.PAPER, 3)}', viewer, sources=[S.page_ref(3)])
    refs = [(EvidenceRef(evidence_id=f"e{i}", work_id=S.WORK, source_version=S.VERSION,
                         locator=SourceLocator(page=3), match_type=MatchType.EXACT, quote=q),
             CITE.format(S.PAPER, 3)) for i, q in enumerate(quotes[2:])]
    decisions = projector.project_quotes(refs, viewer)
    assert any(d["omission_reason"] == "budget" and d["text"] is None for d in decisions)
    assert all(d["citation"] for d in decisions)


def test_viewer_without_session_is_still_capped_per_actor():
    budget = DisclosureBudget(max_actor_quoted_chars_per_work=200)
    ctx = S.local_context(session_id=None)
    projector, viewer, _ = S.make_projector(ctx, budget=budget)
    assert Viewer.from_context(ctx).ledger_keys() == [("actor", "local:local-operator")]
    outs = [projector.project_text(f'"{q}" {CITE.format(S.PAPER, 3)}', viewer, sources=[S.page_ref(3)])
            for q in adjacent_spans(3)[:5]]
    assert QUOTE_OMITTED in outs[-1]

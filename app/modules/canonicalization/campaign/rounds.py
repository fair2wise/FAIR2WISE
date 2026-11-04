"""Campaign stages and the agreement matrix (C-11, rounds part).

``run_collapse`` (fold contexts, quarantine malformed rows) -> ``run_route`` (quarantine non-terms)
-> ``run_round`` x N (bounded; retained votes) -> ``run_park`` (unresolved stay visible).
:func:`decide` is the donor agreement matrix. Its ``plan_merge`` action only means "may enter a
scoped plan"; nothing is applied here. Escalation tiers that need a paid or CLI reviewer are not
ported: a term nobody can act on becomes ``review`` (well supported) or ``park``.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign.py (BSD-3-Clause).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Protocol, cast

from ..quarantine import check_context_row, classify_surface
from ..vocabulary import Vocabulary
from .evidence import GraphRows, assemble, fold_terms
from .models import Decision, ParkedTerm, QuarantineRecord, RoundRecord, TermEvidence, TermVerdict, Vote, term_key
from .runner import CampaignRun

_ESCALATION_FRAMINGS = frozenset({"max_escalation", "cborg"})


def decide(
    votes: Sequence[Vote], *, pubs: int, min_pubs_review: int, min_pubs_paid: int | None = None
) -> tuple[str, str | None, str | None]:
    """The agreement matrix: ``(action, target_slug, quarantine_class)``.

    action is ``plan_merge | review | park | escalate | quarantine``; the first matching rule wins:

    1. Only ACCEPTED ``map_to`` votes count for a slug.
    2. One slug named by two or more different framings, no accepted vote for another: ``plan_merge``.
    3. Accepted votes for two or more slugs: ``escalate`` until an escalation vote exists, then
       ``review`` when ``pubs >= min_pubs_review`` else ``park``.
    4. Exactly one accepted map vote and every other vote an abstain/novel: ``plan_merge`` only when the
       term is in ``min_pubs_review`` publications, the vote is menu_first and a cluster vote names the
       same slug; otherwise as rule 3.
    5. Two or more votes reject the term as the same kind: ``quarantine``.
    6. Every vote says novel: ``park`` as ``novel_candidate``.
    7. Anything else: ``escalate`` while ``pubs >= min_pubs_paid`` and max escalation was not tried, else ``park``.
    """
    paid_floor = min_pubs_review if min_pubs_paid is None else min_pubs_paid
    map_votes = [v for v in votes if v.verdict == "map_to" and v.accepted]
    slugs = Counter(v.target_slug for v in map_votes)
    escalated = any(v.framing in _ESCALATION_FRAMINGS for v in votes)

    def contested() -> tuple[str, str | None, str | None]:
        if not escalated:
            return ("escalate", None, None)
        return ("review" if pubs >= min_pubs_review else "park", None, None)

    if len(slugs) == 1:
        (slug,) = slugs
        if len({v.framing for v in map_votes}) >= 2:
            return ("plan_merge", slug, None)
    if len(slugs) >= 2:
        return contested()
    if len(map_votes) == 1:
        lone = map_votes[0]
        others = [v for v in votes if v is not lone]
        if all(v.verdict in ("abstain", "novel") for v in others):
            corroborated = any(v.framing == "cluster" and v.target_slug == lone.target_slug for v in others)
            if pubs >= min_pubs_review and lone.framing == "menu_first" and corroborated:
                return ("plan_merge", lone.target_slug, None)
            return contested()
    rejects = Counter(v.reject_kind for v in votes if v.verdict == "reject_kind" and v.reject_kind)
    if rejects:
        kind, count = sorted(rejects.items(), key=lambda item: (-item[1], item[0]))[0]
        if count >= 2:
            return ("quarantine", None, kind)
    if votes and all(v.verdict == "novel" for v in votes):
        return ("park", None, "novel_candidate")
    if pubs >= paid_floor and not any(v.framing == "max_escalation" for v in votes):
        return ("escalate", None, None)
    return ("park", None, None)


def run_collapse(run: CampaignRun, rows: GraphRows, vocab: Vocabulary) -> None:
    """Quarantine malformed rows, fold the rest into terms and assemble evidence."""
    good = GraphRows([], rows.edges, rows.complete)
    for row in rows.nodes:
        problem = check_context_row(row)
        if problem is None:
            good.nodes.append(row)
        else:
            surface = str(row.get("surface")) if isinstance(row, dict) else repr(row)[:80]
            node = str(row.get("node_id") or "") if isinstance(row, dict) else ""
            category = str(row.get("category") or "unknown") if isinstance(row, dict) else "unknown"
            run.quarantine.append(
                QuarantineRecord(
                    term_key(category, surface),
                    surface,
                    category,
                    problem["code"],
                    problem["detail"],
                    (node,) if node else (),
                )
            )
    terms = fold_terms(good)
    for key, slot in sorted(terms.items()):
        run.evidence[key] = assemble(good, key, slot, vocab)
    run.pending = sorted(run.evidence)
    run.rounds.append(
        RoundRecord(
            f"{run.campaign_id}:collapse:deterministic",
            "collapse",
            "deterministic",
            counts={"rows": len(rows.nodes), "terms": len(run.evidence), "quarantined_rows": len(run.quarantine)},
        )
    )


def run_route(
    run: CampaignRun,
    vocab: Vocabulary,
    *,
    software_extra: Sequence[str] = (),
    generic_stoplist: Sequence[str] = (),
) -> None:
    """Quarantine surfaces that are not terms (software, procedures, sentences...). Reported, never dropped."""
    keep: list[str] = []
    before = len(run.quarantine)
    for key in run.pending:
        ev = run.evidence[key]
        reason = classify_surface(
            ev.surface, vocab, software_extra=software_extra, generic_stoplist=generic_stoplist
        )
        if reason:
            run.quarantine.append(QuarantineRecord(key, ev.surface, ev.category, reason, "", ev.node_ids))
        else:
            keep.append(key)
    run.pending = keep
    run.rounds.append(
        RoundRecord(
            f"{run.campaign_id}:route:deterministic",
            "route",
            "deterministic",
            counts={"quarantined": len(run.quarantine) - before, "routed": len(keep)},
        )
    )


class RoundResolver(Protocol):
    """The one resolver method ``run_round`` needs (``StructuredTermResolver`` or a test double)."""

    def resolve(self, ev: TermEvidence, *, round_key: str, framing: str = ...) -> TermVerdict: ...


def run_round(run: CampaignRun, resolver: RoundResolver, number: int) -> RoundRecord:
    """One bounded round: framing menu_first, then evidence_first for later rounds; retains every vote.

    At most ``run.max_terms_per_round`` terms are asked; the rest wait (still pending, still visible).
    """
    framing = "menu_first" if number % 2 == 1 else "evidence_first"
    round_key = f"{run.campaign_id}:round{number}:{framing}"
    asked, undecided, outcome = 0, [], cast(Counter[str], Counter())
    for key in run.pending:
        if asked >= run.max_terms_per_round:
            undecided.append(key)
            continue
        asked += 1
        ev = run.evidence[key]
        run.votes.setdefault(key, []).append(resolver.resolve(ev, round_key=round_key, framing=framing))
        action, slug, qclass = decide(
            run.vote_objects(key), pubs=ev.distinct_publications, min_pubs_review=run.min_pubs_review
        )
        outcome[action] += 1
        if action == "plan_merge" and slug:
            run.decisions[key] = Decision(
                key,
                ev.category,
                ev.node_ids,
                slug,
                f"campaign_round{min(number, 2)}_agree",
                max(v.confidence for v in run.votes[key] if v.accepted),
                tuple(f"{v.framing}:{v.verdict}:{v.target_slug}" for v in run.votes[key]),
            )
        elif action == "quarantine":
            run.quarantine.append(
                QuarantineRecord(
                    key, ev.surface, ev.category, qclass or "quarantine", "two votes agreed", ev.node_ids
                )
            )
        elif action == "review":
            if key not in run.review_queue:
                run.review_queue.append(key)
            undecided.append(key)
        else:
            undecided.append(key)
    run.pending = [k for k in undecided if k not in run.decisions]
    record = RoundRecord(
        round_key,
        f"round{number}",
        framing,
        resolver.config.model if hasattr(resolver, "config") else "",
        counts={"asked": asked, **dict(outcome)},
        input_tokens=getattr(resolver, "input_tokens", 0),
    )
    run.rounds.append(record)
    return record


def run_park(run: CampaignRun) -> dict[str, int]:
    """Whatever is still pending is parked with a reason and its votes. Nothing silently disappears."""
    parked_now = 0
    done = {p.term_key for p in run.parked}
    for key in run.pending:
        if key in done:
            continue
        ev = run.evidence[key]
        votes = tuple(f"{v.framing}:{v.verdict}:{v.target_slug or ''}" for v in run.votes.get(key, []))
        reason = (
            "awaiting review"
            if key in run.review_queue
            else "not asked (round budget)" if not run.votes.get(key) else "no agreeing votes"
        )
        run.parked.append(
            ParkedTerm(key, ev.surface, ev.category, reason, votes, ev.node_ids, ev.distinct_publications)
        )
        parked_now += 1
    run.rounds.append(
        RoundRecord(
            f"{run.campaign_id}:park:deterministic", "park", "deterministic", counts={"parked": parked_now}
        )
    )
    return {"parked": parked_now}


def fresh_votes(run: CampaignRun) -> dict[str, int]:
    """Vote counts per term, for reports."""
    return {k: len(v) for k, v in run.votes.items()}

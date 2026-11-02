"""Campaign planning and human review records (C-11, planning part).

``run_plan`` builds a scoped :class:`~..migration.ScopedMergePlan` from the run's proposed decisions
plus human review decisions. ``record_review_decision`` records a reviewer's choice; it does not
apply anything and is not an ``ApprovalRecord``. Applying a plan is a separate, gated step
(:func:`..migration.apply_campaign`) that needs an ``ApprovalRecord`` for the plan's exact hash.
Model-callable code never receives application authority: nothing here accepts or builds an approval.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from ..migration import PlanDraft, plan_migration
from ..vocabulary import Vocabulary
from .models import Decision, ReviewDecision
from .runner import CampaignRun

REVIEW_ACTIONS = ("approve", "reject", "retarget", "quarantine")


def record_review_decision(
    run: CampaignRun,
    *,
    term_key: str,
    action: str,
    target_slug: str | None,
    reviewer: str,
    decided_at: datetime,
    vocab: Vocabulary,
    note: str = "",
) -> ReviewDecision:
    """Append a human decision about one term (validated, not applied).

    Raises:
        ValueError: unknown term/action, missing reviewer, or a target slug not in the vocabulary.
    """
    if term_key not in run.evidence:
        raise ValueError("unknown term")
    if action not in REVIEW_ACTIONS:
        raise ValueError(f"unknown review action {action!r}")
    if not reviewer.strip():
        raise ValueError("a named reviewer is required")
    if action in ("approve", "retarget"):
        slug = target_slug or (run.decisions[term_key].target_slug if term_key in run.decisions else None)
        if not slug or vocab.get(slug) is None:
            raise ValueError("approve/retarget needs a target slug present in the vocabulary")
        target_slug = slug
    record = ReviewDecision(term_key, action, target_slug, reviewer.strip(), decided_at.isoformat(), note)
    run.reviews.append(record)
    return record


def effective_decisions(run: CampaignRun) -> list[Decision]:
    """Proposed decisions after human review: the latest review per term wins; rejects remove a proposal."""
    latest: dict[str, ReviewDecision] = {}
    for review in run.reviews:
        latest[review.term_key] = review
    out: dict[str, Decision] = dict(run.decisions)
    for key, review in latest.items():
        if review.action in ("reject", "quarantine"):
            out.pop(key, None)
        else:
            ev = run.evidence[key]
            out[key] = Decision(
                key,
                ev.category,
                ev.node_ids,
                str(review.target_slug),
                "campaign_human_review",
                1.0,
                (f"human:{review.action}:{review.target_slug}",),
                reviewer=review.reviewer,
            )
    return [out[k] for k in sorted(out)]


def run_plan(
    run: CampaignRun, vocab: Vocabulary, *, scope: Sequence[str], target: str, created_at: datetime
) -> PlanDraft:
    """Build the scoped plan; terms that fail the safety check are held with a reason, not planned.

    Raises:
        ValueError: the vocabulary revision differs from the one the run started with.
    """
    if vocab.revision != run.vocabulary_revision:
        raise ValueError("vocabulary changed during the campaign; start a new run")
    return plan_migration(
        effective_decisions(run),
        run.evidence,
        vocab,
        scope=scope,
        target=target,
        source_revision=run.source_revision,
        report_sha256=run.report_sha256(),
        created_at=created_at,
    )

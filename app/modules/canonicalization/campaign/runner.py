"""Campaign state and orchestration (C-11, runner part). Orchestration only; algorithms live elsewhere.

A :class:`CampaignRun` keeps everything a reviewer needs: evidence per term, quarantine records with
reason codes, every vote with its reason, parked (unresolved) terms, round records, review decisions
and growth observations. :func:`run_campaign` goes
contexts -> quarantine -> bounded rounds -> parking -> scoped plan and STOPS: it returns a plan draft,
never applies one, and never builds an approval.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign.py (BSD-3-Clause), split into
runner/rounds/planning. Not ported: CLI entry points, paid/CLI escalation tiers (round 3), the
catalog readers and the in-place ``apply`` flag.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from ..growth import ObservationLog
from ..migration import PlanDraft, sha256_json
from ..vocabulary import Vocabulary
from .evidence import GraphRows, RowSource, load_rows
from .models import (
    Decision,
    ParkedTerm,
    QuarantineRecord,
    ReviewDecision,
    RoundRecord,
    TermEvidence,
    TermVerdict,
    Vote,
)

DEFAULT_MAX_ROUNDS = 2
DEFAULT_MAX_TERMS_PER_ROUND = 500
DEFAULT_MIN_PUBS_REVIEW = 3


@dataclass
class CampaignRun:
    """Mutable campaign state. Nothing here touches a graph."""

    campaign_id: str
    vocabulary_revision: str
    source_revision: str
    evidence: dict[str, TermEvidence] = field(default_factory=dict)
    quarantine: list[QuarantineRecord] = field(default_factory=list)
    votes: dict[str, list[TermVerdict]] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)
    pending: list[str] = field(default_factory=list)
    parked: list[ParkedTerm] = field(default_factory=list)
    rounds: list[RoundRecord] = field(default_factory=list)
    reviews: list[ReviewDecision] = field(default_factory=list)
    review_queue: list[str] = field(default_factory=list)
    observations: ObservationLog = field(default_factory=ObservationLog)
    max_rounds: int = DEFAULT_MAX_ROUNDS
    max_terms_per_round: int = DEFAULT_MAX_TERMS_PER_ROUND
    min_pubs_review: int = DEFAULT_MIN_PUBS_REVIEW

    def vote_objects(self, key: str) -> list[Vote]:
        """Return the retained votes for `key` as ``Vote`` objects (empty if none)."""
        return [
            Vote(v.framing, v.verdict, v.target_slug, v.reject_kind, v.accepted) for v in self.votes.get(key, [])
        ]

    def report(self) -> dict[str, Any]:
        """Counts a reviewer can act on. Candidate terms are counted separately from canonical entities."""
        return {
            "campaign_id": self.campaign_id,
            "source_revision": self.source_revision,
            "vocabulary_revision": self.vocabulary_revision,
            "candidate_terms": len(self.evidence),
            "quarantined": len(self.quarantine),
            "quarantine_by_code": _count(q.reason_code for q in self.quarantine),
            "proposed_decisions": len(self.decisions),
            "parked": len(self.parked),
            "review_queue": len(self.review_queue),
            "rounds": [r.round_key for r in self.rounds],
            "votes_retained": sum(len(v) for v in self.votes.values()),
            "human_reviews": len(self.reviews),
        }

    def report_sha256(self) -> str:
        """Return the SHA-256 hash of :meth:`report` (via ``sha256_json``)."""
        return sha256_json(self.report())

    def to_json(self) -> str:
        """Everything retained (votes with reasons, quarantine, parked, rounds, reviews) as JSON."""
        data = {
            "report": self.report(),
            "quarantine": [q.__dict__ for q in self.quarantine],
            "votes": {k: [v.as_dict() for v in vs] for k, vs in self.votes.items()},
            "decisions": {k: d.as_dict() for k, d in self.decisions.items()},
            "parked": [p.__dict__ for p in self.parked],
            "review_queue": list(self.review_queue),
            "rounds": [r.__dict__ for r in self.rounds],
            "reviews": [r.__dict__ for r in self.reviews],
            "observations": json.loads(self.observations.to_json()),
        }
        return json.dumps(data, sort_keys=True, indent=1, default=list)

    def save(self, directory: Path) -> Path:
        """Write ``campaign_<id>.json`` under ``directory``. Reports are preserved, never overwritten silently."""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"campaign_{self.campaign_id}.json"
        path.write_text(self.to_json(), encoding="utf-8")
        return path


def _count(items: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return dict(sorted(out.items()))


def open_run(
    rows: GraphRows,
    vocabulary: Vocabulary,
    *,
    campaign_id: str,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_terms_per_round: int = DEFAULT_MAX_TERMS_PER_ROUND,
) -> CampaignRun:
    """Start a run bound to the rows' revision and the vocabulary's revision."""
    return CampaignRun(
        campaign_id,
        vocabulary.revision,
        rows.revision(),
        max_rounds=max_rounds,
        max_terms_per_round=max_terms_per_round,
    )


def campaign_status(run: CampaignRun) -> dict[str, Any]:
    """Stage-by-stage counts, for progress and review dashboards."""
    return {**run.report(), "pending": len(run.pending)}


@dataclass(frozen=True)
class CampaignOutcome:
    """Result of :func:`run_campaign`: the run state, the scoped plan draft and the report."""

    run: CampaignRun
    draft: PlanDraft
    report: dict[str, Any]


def run_campaign(
    source: RowSource,
    vocabulary: Vocabulary,
    resolver: Any,
    *,
    campaign_id: str,
    scope: Sequence[str],
    target: str,
    created_at: datetime,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_terms_per_round: int = DEFAULT_MAX_TERMS_PER_ROUND,
) -> CampaignOutcome:
    """Run contexts -> quarantine -> rounds -> park -> scoped plan. Never applies; never approves.

    Raises:
        IncompleteRead: the source read was partial.
    """
    from . import planning, rounds

    rows = load_rows(source)
    run = open_run(
        rows, vocabulary, campaign_id=campaign_id, max_rounds=max_rounds, max_terms_per_round=max_terms_per_round
    )
    rounds.run_collapse(run, rows, vocabulary)
    rounds.run_route(run, vocabulary)
    for number in range(1, max_rounds + 1):
        rounds.run_round(run, resolver, number)
    rounds.run_park(run)
    draft = planning.run_plan(run, vocabulary, scope=scope, target=target, created_at=created_at)
    return CampaignOutcome(run, draft, run.report())

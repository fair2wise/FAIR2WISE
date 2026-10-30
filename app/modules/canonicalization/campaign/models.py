"""Campaign records (C-07): structured evidence, verdicts, decisions and rounds with versioned ids.

All records are frozen dataclasses. ``Decision.from_dict`` rejects unknown keys on purpose: a plan
file with a misspelt field must not be applied. Nothing here performs I/O.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_types.py (BSD-3-Clause).
Changes: no provider-specific tiers (paid/CLI escalation is not ported), ``method`` names say
"plan", never "applied", and quarantine, parked and review records are added.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Any

MODELS_VERSION = "t07-campaign-1"

CAMPAIGN_METHODS = ("campaign_round1_agree", "campaign_round2_agree", "campaign_human_review")
FRAMINGS = ("menu_first", "evidence_first", "cluster", "deterministic")
VERDICTS = ("map_to", "novel", "reject_kind", "abstain")


def term_key(category: str, folded: str) -> str:
    """The one identity every table joins on: sha256 of ``category|folded``."""
    return hashlib.sha256(f"{category}|{folded}".encode()).hexdigest()


@dataclass(frozen=True)
class Snippet:
    """A source passage (publication and section) quoted as evidence for a term."""

    publication_id: str
    section_id: str
    text: str


@dataclass(frozen=True)
class MenuEntry:
    """A candidate vocabulary entry offered to a resolver as a possible mapping target."""

    slug: str
    display_name: str
    category: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class TermEvidence:
    """Everything a resolver may see about one term; the source mentions stay attached."""

    term_key: str
    category: str
    surface: str
    folded: str
    variants: tuple[str, ...]
    node_ids: tuple[str, ...]
    snippets: tuple[Snippet, ...]
    distinct_publications: int
    sections: int
    edges: int
    menu: tuple[MenuEntry, ...] = ()
    cluster_neighbours: tuple[str, ...] = ()

    def evidence_text(self) -> str:
        """The text a quote must be found in: snippets joined by newlines."""
        return "\n".join(s.text for s in self.snippets)


@dataclass(frozen=True)
class TermVerdict:
    """The outcome of one resolver round/framing for one term, with cost and acceptance."""

    term_key: str
    round_key: str
    framing: str
    verdict: str
    target_slug: str | None
    reject_kind: str | None
    evidence_quote: str
    confidence: float
    reason: str
    accepted: bool
    errors: tuple[str, ...] = ()
    request_sha: str = ""
    model: str = ""
    #: ``None`` means unknown (never zero) when the provider reports no cost.
    cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return the record as a plain dict."""
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    """A proposed mapping of one candidate term to one vocabulary slug. A decision applies nothing."""

    term_key: str
    category: str
    node_ids: tuple[str, ...]
    target_slug: str
    method: str
    confidence: float
    votes: tuple[str, ...]
    plan_id: str = ""
    reviewer: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the decision as a plain dict."""
        return asdict(self)

    @staticmethod
    def from_dict(row: dict[str, Any]) -> Decision:
        """Build a decision from a dict, converting list fields to tuples.

        Raises:
            TypeError: If ``row`` has unknown keys or lacks required fields.
        """
        return Decision(
            **{**row, "node_ids": tuple(row.get("node_ids") or ()), "votes": tuple(row.get("votes") or ())}
        )


@dataclass(frozen=True)
class RoundRecord:
    """Bookkeeping for one resolver round: stage, tier, model, counts and cost."""

    round_key: str
    stage: str
    tier: str
    model: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


@dataclass(frozen=True)
class Vote:
    """One signal about one term, reduced to what :func:`.rounds.decide` needs."""

    framing: str
    verdict: str
    target_slug: str | None
    reject_kind: str | None
    accepted: bool


@dataclass(frozen=True)
class QuarantineRecord:
    """A term set aside with a reason; quarantined terms are reported, never dropped."""

    term_key: str
    surface: str
    category: str
    reason_code: str
    detail: str = ""
    node_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParkedTerm:
    """An unresolved term kept visible with why, its votes and its source node ids."""

    term_key: str
    surface: str
    category: str
    reason: str
    votes: tuple[str, ...] = ()
    node_ids: tuple[str, ...] = ()
    distinct_publications: int = 0


@dataclass(frozen=True)
class ReviewDecision:
    """A human review decision about one term. Recording it does not apply anything."""

    term_key: str
    action: str  # approve | reject | retarget | quarantine
    target_slug: str | None
    reviewer: str
    decided_at: str
    note: str = ""

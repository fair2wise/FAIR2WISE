"""Online resolution of one mention (plan T07 "Online proposal").

exact identifier -> approved-vocabulary alias -> exact existing label -> typed bounded shortlist
-> constrained adjudication (only when needed) -> mandatory safety check -> accepted mapping or
review/unresolved.

Every :class:`Resolution` keeps the full :class:`SourceMention` (T06 candidates stay candidates).
An accepted resolution is a *mapping proposal*: this module never writes to a graph, never
merges, and never activates anything. Unresolved terms are returned (with their shortlist)
rather than dropped; an unavailable index is reported as such rather than as "no match".
Candidate (non-approved) vocabularies never produce an accepted mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..extraction.contract import ExtractedMention
from .adjudication import Judge, MentionVerdict, adjudicate_mention
from .candidate_search import CandidateSearch, Shortlist
from .merge_safety import check_merge
from .vocabulary import Vocabulary

ACCEPTED, REVIEW, UNRESOLVED = "accepted", "review", "unresolved"


@dataclass(frozen=True)
class SourceMention:
    """The mention exactly as extracted, retained on every resolution."""

    surface: str
    category: str = ""
    work_id: str = ""
    section: str | None = None
    page: int | None = None
    chunk_id: str | None = None
    quote: str = ""
    identifiers: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_extracted(cls, mention: ExtractedMention, work_id: str = "") -> SourceMention:
        """Wrap a T06 candidate; identifiers come from its native attributes (formula, chebi, smiles)."""
        ids = tuple(
            (k, str(v))
            for k, v in sorted(dict(mention.attributes).items())
            if k in ("smiles", "chebi", "formula", "entity_id", "slug") and v
        )
        return cls(
            mention.surface,
            mention.category,
            work_id,
            mention.section,
            mention.page,
            mention.chunk_id,
            mention.quote,
            ids + ((("slug", mention.slug),) if mention.slug else ()),
        )


class MutableBudget:
    """Counter shared across a batch of resolutions."""

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self.used = 0

    def take(self) -> bool:
        """Consume one call from the budget; return False once ``max_calls`` is reached."""
        if self.used >= self.max_calls:
            return False
        self.used += 1
        return True


@dataclass(frozen=True)
class Resolution:
    """Outcome for one mention."""

    mention: SourceMention
    status: str
    entity_id: str | None
    method: str
    reason: str
    shortlist: Shortlist | None = None
    verdict: MentionVerdict | None = None

    @property
    def is_proposal_only(self) -> bool:
        """Always true: a resolution never changes a graph."""
        return True


def resolve_mention(
    mention: SourceMention,
    *,
    vocabulary: Vocabulary | None = None,
    identifier_index: Mapping[tuple[str, str], str] | None = None,
    search: CandidateSearch | None = None,
    judge: Judge | None = None,
    budget: MutableBudget | None = None,
    category_map: Mapping[str, str] | None = None,
) -> Resolution:
    """Resolve one mention to a mapping proposal, a review item or a visible unresolved term."""
    surface = mention.surface.strip()
    if not surface:
        return Resolution(mention, UNRESOLVED, None, "none", "empty surface")
    for ident in mention.identifiers:
        target = (identifier_index or {}).get(ident)
        if target:
            return Resolution(mention, ACCEPTED, target, "identifier", f"exact identifier {ident[0]}")
    if vocabulary is not None:
        vocab_category = (category_map or {}).get(mention.category)
        entry = vocabulary.match(surface, vocab_category)
        if entry is not None:
            if vocabulary.approved:
                return Resolution(
                    mention,
                    ACCEPTED,
                    entry.slug,
                    "approved_alias",
                    f"approved vocabulary alias (revision {vocabulary.revision[:12]})",
                )
            return Resolution(
                mention,
                REVIEW,
                entry.slug,
                "candidate_alias",
                "matches a CANDIDATE vocabulary entry; the vocabulary is not approved",
            )
    if search is None:
        return Resolution(mention, UNRESOLVED, None, "none", "no candidate index configured")
    shortlist = search.search(surface, mention.category or None)
    if shortlist.status == "index_unavailable":
        return Resolution(
            mention, UNRESOLVED, None, "none", "candidate index unavailable (not 'no match')", shortlist
        )
    if not shortlist.candidates:
        return Resolution(mention, UNRESOLVED, None, "none", "no plausible existing entity", shortlist)
    exact = [c for c in shortlist.candidates if "exact" in c.channels]
    if len(exact) == 1:
        verdict = check_merge(
            exact[0].label,
            surface,
            canonical_class=exact[0].category or None,
            alias_class=mention.category or None,
        )
        if verdict.allowed:
            return Resolution(
                mention, ACCEPTED, exact[0].entity_id, "exact_label", "identical after folding", shortlist
            )
        return Resolution(mention, REVIEW, exact[0].entity_id, "exact_label", verdict.reason, shortlist)
    if judge is None:
        return Resolution(
            mention,
            UNRESOLVED,
            None,
            "needs_adjudication",
            f"{len(shortlist.candidates)} candidates, no judge configured",
            shortlist,
        )
    if budget is not None and not budget.take():
        return Resolution(
            mention, UNRESOLVED, None, "needs_adjudication", "adjudication budget exhausted", shortlist
        )
    verdict = adjudicate_mention(surface, mention.category, shortlist, judge=judge)
    if verdict.status == "match" and verdict.entity_id:
        chosen = next(c for c in shortlist.candidates if c.entity_id == verdict.entity_id)
        recheck = check_merge(
            chosen.label, surface, canonical_class=chosen.category or None, alias_class=mention.category or None
        )
        if recheck.allowed:
            return Resolution(
                mention, ACCEPTED, chosen.entity_id, "adjudicated", verdict.reason, shortlist, verdict
            )
        return Resolution(mention, REVIEW, chosen.entity_id, "adjudicated", recheck.reason, shortlist, verdict)
    if verdict.status in ("refused", "not_offered"):
        return Resolution(mention, REVIEW, verdict.entity_id, "adjudicated", verdict.reason, shortlist, verdict)
    return Resolution(
        mention, UNRESOLVED, None, "adjudicated", f"{verdict.status}: {verdict.reason}", shortlist, verdict
    )

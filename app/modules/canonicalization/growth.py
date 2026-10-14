"""Vocabulary growth (C-14): observations -> proposals -> reviewed promotion, kept distinct.

* :class:`Observation`: what a paper said (label, aliases, quote, document hash). Append-only.
* :class:`VocabularyProposal`: what *could* be promoted, plus every :class:`WithheldPromotion`
  with its reason. Proposing never changes a vocabulary.
* :func:`promote`: gated (``promote_vocabulary``), needs a :class:`~.vocabulary.VocabularyApproval`
  naming the proposal's exact revision, and returns a NEW *candidate* vocabulary. Activating that
  vocabulary is a separate, separately gated step (:func:`.audit.activate_vocabulary`).

Staging an observation and promoting it are independent: staging never promotes.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/vocabulary_growth.py (BSD-3-Clause).
Rewritten: SQLite registry replaced by an in-memory log with JSON round-trip; automatic
promotion removed (the donor promoted without review); materials are always withheld until a
structure identifier exists (H3 binary cases).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .gates import authorize_vocabulary_change
from .merge_safety import check_merge, is_acronym_of
from .vocabulary import CATEGORIES, VocabEntry, Vocabulary, VocabularyApproval, entity_slug

_BRACKETED = re.compile(r"\(([A-Z][A-Za-z0-9-]{1,11})\)")


def definitions(text: str) -> List[Tuple[str, str]]:
    """``(acronym, expansion)`` pairs where the words before ``(ACR)`` spell the acronym."""
    found: List[Tuple[str, str]] = []
    for match in _BRACKETED.finditer(text or ""):
        acronym = match.group(1)
        words = re.findall(r"[A-Za-z0-9\-]+", text[: match.start()])
        for take in range(min(len(words), len(acronym) + 3), 1, -1):
            expansion = " ".join(words[-take:])
            if is_acronym_of(acronym, expansion):
                found.append((acronym, expansion))
                break
    return found


@dataclass(frozen=True)
class Observation:
    label: str
    category: str
    publication: str
    aliases: Tuple[str, ...] = ()
    quote: str = ""
    document_hash: str = ""


@dataclass(frozen=True)
class WithheldPromotion:
    label: str
    category: str
    reason: str
    publications: int = 0


@dataclass(frozen=True)
class VocabularyProposal:
    """Reviewable additions to one base revision, with every withheld promotion preserved."""

    base_revision: str
    entries: Tuple[VocabEntry, ...]
    withheld: Tuple[WithheldPromotion, ...]
    min_publications: int
    revision: str = ""

    def __post_init__(self) -> None:
        body = json.dumps(
            {
                "base": self.base_revision,
                "min": self.min_publications,
                "entries": [e.as_dict() for e in self.entries],
                "withheld": [[w.label, w.category, w.reason, w.publications] for w in self.withheld],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        object.__setattr__(self, "revision", hashlib.sha256(body.encode()).hexdigest())


@dataclass
class ObservationLog:
    """Append-only observations; the only thing staging does."""

    observations: List[Observation] = field(default_factory=list)

    def observe(self, obs: Observation) -> None:
        self.observations.append(obs)

    def to_json(self) -> str:
        return json.dumps([o.__dict__ for o in self.observations], sort_keys=True, default=list)

    @staticmethod
    def from_json(text: str) -> "ObservationLog":
        return ObservationLog(
            [Observation(**{**o, "aliases": tuple(o.get("aliases") or ())}) for o in json.loads(text)]
        )


def propose_promotion(base: Vocabulary, log: ObservationLog, *, min_publications: int = 3) -> VocabularyProposal:
    """Group observations by (category, slug) and propose well-supported, collision-free entries."""
    groups: Dict[Tuple[str, str], List[Observation]] = {}
    for obs in log.observations:
        groups.setdefault((obs.category, entity_slug(obs.label)), []).append(obs)
    entries: List[VocabEntry] = []
    withheld: List[WithheldPromotion] = []
    working = list(base.entries)
    for (category, slug), observed in sorted(groups.items()):
        label = observed[0].label
        pubs = len({o.publication for o in observed})

        def hold(reason: str) -> None:
            withheld.append(WithheldPromotion(label, category, reason, pubs))

        if not slug:
            hold("empty slug")
        elif category not in CATEGORIES:
            hold(f"unsupported category {category!r}")
        elif category == "material":
            hold("material promotion needs a structure identifier (H3 binary case)")
        elif base.match(label) is not None:
            hold("already in vocabulary")
        elif pubs < min_publications:
            hold(f"supported by {pubs} publications, below {min_publications}")
        elif not any(o.quote for o in observed):
            hold("no source quote retained")
        else:
            aliases = tuple(
                sorted(
                    {
                        a
                        for o in observed
                        for a in o.aliases
                        if a
                        and check_merge(label, a, canonical_class=category, alias_class=category).allowed
                        and entity_slug(a) != slug
                    }
                )
            )
            entry = VocabEntry(slug, label, category, aliases=aliases)
            try:
                Vocabulary(base.version, [*working, entry])
            except ValueError as exc:
                hold(f"collision: {exc}")
                continue
            working.append(entry)
            entries.append(entry)
    return VocabularyProposal(base.revision, tuple(entries), tuple(withheld), min_publications)


def promote(base: Vocabulary, proposal: VocabularyProposal, approval: Optional[VocabularyApproval]) -> Vocabulary:
    """Gated promotion: a new CANDIDATE vocabulary (version + 1) containing the proposal's entries.

    Raises:
        ApplyRefused: gate closed, no approval, approval for another proposal revision, or the base moved.
    """
    authorize_vocabulary_change(
        "promote_vocabulary", proposal.revision, approval, current_revision=proposal.revision
    )
    if proposal.base_revision != base.revision:
        from .gates import ApplyRefused

        raise ApplyRefused("stale_vocabulary", "base vocabulary changed since the proposal")
    return Vocabulary(
        base.version + 1, [*base.entries, *proposal.entries], source=f"promotion:{proposal.revision}"
    )


def observation_summary(log: ObservationLog) -> Dict[str, Any]:
    """Counts for reports: observations and distinct labels (candidate counts, not canonical entities)."""
    return {
        "observations": len(log.observations),
        "distinct_labels": len({(o.category, entity_slug(o.label)) for o in log.observations}),
        "publications": len({o.publication for o in log.observations}),
    }

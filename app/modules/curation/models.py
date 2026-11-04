"""Uncertainty cases, resolution attempts and reviewer decisions (U-01, U-11 structure).

An uncertainty case is one source-located ambiguity that can be resolved independently of the
others. Its identity is a content hash, so reruns and overlapping chunks do not multiply it.
Its ``revision`` additionally covers the evidence text and the schema/vocabulary revisions it
was raised under: a reviewer decision binds to that revision and is stale once it moves.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/scientific_artifacts.py (``UncertaintyCase``,
``ResolutionAttempt``) (BSD-3-Clause), with an explicit corpus (tenant) on every case and cost
that may be unknown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from typing import Any


class CaseState(StrEnum):
    """Where a case is. ``resolved``/``rejected`` are terminal until the case revision changes."""

    PENDING = "pending"
    LEASED = "leased"
    RESOLVED = "resolved"
    NEEDS_REVIEW = "needs_review"
    RETRY_WAIT = "retry_wait"
    BUDGET_PAUSED = "budget_paused"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class Route(StrEnum):
    """Resolution route chosen by deterministic triage."""

    TRIAGE = "triage"
    LOCAL = "local"
    STRUCTURAL = "structural"
    LOCAL_MODEL = "local_model"
    REMOTE_MODEL = "remote_model"
    REVIEW = "review"
    EXTRACTION = "extraction"


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class UncertaintyCase:
    """One source-located scientific ambiguity.

    Attributes:
        corpus: Corpus (tenant) the case belongs to; every query is scoped by it.
        publication_id: Work identifier (DOI or internal id).
        document_hash: Source document checksum.
        source_kind / source_locator: Where in the source (for example ``chunk`` and ``page=4;span=120-180``).
        case_type: ``unresolved_term``, ``incomplete_extraction``...
        reason: Why it is uncertain.
        evidence: The source text supporting review (bounded by the caller).
        payload: Case-type specific JSON.
        priority: Higher first.
        vocabulary_revision / schema_revision: Revisions the case was raised under.
    """

    corpus: str
    publication_id: str
    document_hash: str
    source_kind: str
    source_locator: str
    case_type: str
    reason: str
    evidence: str
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 50
    vocabulary_revision: str = ""
    schema_revision: str = ""

    @property
    def id(self) -> str:
        """Stable case id (excludes evidence text so a changed source updates the case, not duplicates it)."""
        identity = {
            "corpus": self.corpus,
            "publication_id": self.publication_id.casefold().strip(),
            "document_hash": self.document_hash,
            "source_kind": self.source_kind,
            "source_locator": self.source_locator,
            "case_type": self.case_type,
            "reason": self.reason,
            "payload": self.payload,
        }
        return hashlib.sha256(_json(identity).encode()).hexdigest()

    @property
    def revision(self) -> str:
        """What a reviewer decision binds to: identity plus evidence, vocabulary and schema revisions."""
        body = [self.id, self.evidence, self.vocabulary_revision, self.schema_revision]
        return hashlib.sha256(_json(body).encode()).hexdigest()


@dataclass(frozen=True)
class ResolutionAttempt:
    """Immutable record of one resolver request and its validated proposal.

    ``cost_usd`` is ``None`` when the provider did not report a cost: unknown, never zero.
    """

    case_id: str
    provider: str
    requested_model: str
    executed_model: str
    request_fingerprint: str
    outcome: str  # proposed | abstained | invalid | failed | resolved (deterministic local)
    response: dict[str, Any]
    validation: dict[str, Any]
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


__all__ = ("CaseState", "ResolutionAttempt", "Route", "UncertaintyCase")

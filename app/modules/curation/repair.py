"""Plan targeted re-extraction of incomplete chunks (U-09 planning half).

Plans only the chunks named by ``incomplete_extraction`` cases, never the whole corpus, reuses
valid cached chunks, and states the cost (unknown when no estimate exists). Planning **aborts**
when the graph read it depends on is not provably complete: a page or capped read returns
fewer entities than exist, and "unobserved" must never be treated as "absent".

The plan is immutable and content-hashed so a reviewer approves the exact plan (the apply half
belongs with the extraction stage and the shared apply gate).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/chunk_reextraction.py (``plan_reextraction``)
(BSD-3-Clause). Placed here because ``extraction/`` is outside T10's ownership.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..contracts.errors import CapabilityError, ContractError, ErrorCode
from ..contracts.ports import GraphCapabilities


class IncompleteRead(ContractError):
    """The read that planning depends on was a page or capped result: planning aborted."""

    def __init__(self, detail: str) -> None:
        super().__init__(ErrorCode.CONFLICT, f"repair planning aborted: {detail}", retryable=False)


@dataclass(frozen=True)
class ChunkRepair:
    """One chunk to re-extract."""

    chunk_id: str
    case_id: str
    publication_id: str
    source_version: str


@dataclass(frozen=True)
class ReextractionPlan:
    """Immutable plan; ``plan_sha256`` is what an approval binds to."""

    corpus: str
    chunks: Tuple[ChunkRepair, ...]
    reused_chunks: Tuple[str, ...]
    estimated_cost_usd: Optional[float]
    extractor_version: str

    @property
    def plan_sha256(self) -> str:
        """SHA-256 of the canonical JSON of the plan content; an approval binds to this."""
        body = {
            "corpus": self.corpus,
            "chunks": [c.__dict__ for c in self.chunks],
            "reused": list(self.reused_chunks),
            "cost": self.estimated_cost_usd,
            "extractor": self.extractor_version,
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def plan_reextraction(
    *,
    corpus: str,
    cases: Iterable[Mapping[str, Any]],
    graph_read: Tuple[Sequence[Mapping[str, Any]], bool],
    capabilities: GraphCapabilities,
    valid_chunk_ids: Iterable[str] = (),
    extractor_version: str,
    cost_per_chunk_usd: Optional[float] = None,
    limit: int,
) -> ReextractionPlan:
    """Plan re-extraction for ``incomplete_extraction`` cases.

    Args:
        corpus: Corpus the plan covers.
        cases: Review cases; those not of type ``incomplete_extraction`` or from another corpus are skipped.
        graph_read: ``(entities, complete)`` exactly as ``GraphPort.find_entities`` returns it.
        capabilities: The backend's :class:`GraphCapabilities`; ``complete_listing`` is required.
        valid_chunk_ids: Chunks whose cached extraction is still valid (reused, not re-run).
        extractor_version: Extractor revision recorded in the plan.
        cost_per_chunk_usd: Estimate per chunk; ``None`` leaves the plan cost unknown (not zero).
        limit: Maximum chunks in the plan (an explicit bound; there is no whole-corpus default).

    Raises:
        ValueError: ``limit`` is below 1.
        CapabilityError: The backend cannot list completely.
        IncompleteRead: The read was a page or cap.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if not capabilities.complete_listing:
        raise CapabilityError("complete_listing", capabilities.backend)
    entities, complete = graph_read
    if not complete:
        raise IncompleteRead(f"read returned {len(entities)} entities but the listing is not complete")
    valid = set(valid_chunk_ids)
    chunks: List[ChunkRepair] = []
    reused: List[str] = []
    for case in sorted(cases, key=lambda c: (-int(c.get("priority", 50)), str(c["id"]))):
        if case.get("case_type") != "incomplete_extraction" or case.get("corpus") != corpus:
            continue
        chunk_id = str((case.get("payload") or {}).get("chunk_id") or case["source_locator"])
        if chunk_id in valid:
            reused.append(chunk_id)
            continue
        if len(chunks) >= limit:
            break
        chunks.append(
            ChunkRepair(chunk_id, str(case["id"]), str(case["publication_id"]), str(case["document_hash"]))
        )
    estimate = None if cost_per_chunk_usd is None else round(cost_per_chunk_usd * len(chunks), 6)
    return ReextractionPlan(corpus, tuple(chunks), tuple(sorted(set(reused))), estimate, extractor_version)


__all__ = ("ChunkRepair", "IncompleteRead", "ReextractionPlan", "plan_reextraction")

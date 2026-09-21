"""EvidenceRanker: one ranker over mixed, tagged evidence objects."""
from __future__ import annotations

from typing import List, Sequence

from .evidence import INTENT_WEIGHTS, Evidence


class EvidenceRanker:
    """Score KG / paper_chunk / tiled_run hits. Never concatenates corpora into one blob."""

    def rank(
        self,
        items: Sequence[Evidence],
        query: str,
        *,
        intent: str = "factual",
        limit: int = 24,
    ) -> List[Evidence]:
        weights = INTENT_WEIGHTS.get(intent) or INTENT_WEIGHTS["factual"]
        q = (query or "").casefold()
        ranked: List[Evidence] = []
        for item in items:
            kind_w = float(weights.get(item.source_kind, 0.2))
            score = float(item.score) * kind_w
            haystack = f"{item.text} {item.name} {item.locator} {item.heading}".casefold()
            if q and q[:40] in haystack:
                score += 0.25
            if intent == "layout" and item.source_type == "ops":
                score += 0.35
            if intent == "layout" and item.source_type == "literature":
                score *= 0.4
            if intent == "why_how" and item.source_kind == "paper_chunk":
                score += 0.15
            ranked.append(
                Evidence(
                    source_kind=item.source_kind,
                    source_id=item.source_id,
                    score=score,
                    text=item.text,
                    source_type=item.source_type,
                    paper_id=item.paper_id,
                    work_id=item.work_id,
                    page=item.page,
                    locator=item.locator,
                    heading=item.heading,
                    entity_ids=list(item.entity_ids),
                    matched_slots=dict(item.matched_slots),
                    retrieval_method=item.retrieval_method,
                    provenance=dict(item.provenance),
                    graph_id=item.graph_id,
                    name=item.name,
                    category=item.category,
                    payload=item.payload,
                )
            )
        ranked.sort(key=lambda ev: ev.score, reverse=True)
        # Keep distinct objects; drop exact duplicate source_ids only.
        seen: set[str] = set()
        uniq: List[Evidence] = []
        for item in ranked:
            if item.source_id in seen:
                continue
            seen.add(item.source_id)
            uniq.append(item)
            if len(uniq) >= max(1, limit):
                break
        return uniq

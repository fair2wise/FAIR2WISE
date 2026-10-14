"""Bounded, typed candidate retrieval for online resolution (new; not a donor port).

Replaces "paste every display name into the prompt" (the 222b1e7 baseline) and "embedding
top-k only" (the pre-migration FAISS shortlist) with one typed shortlist that combines:

* an exact channel (folded label/alias equality),
* a lexical channel (initialism, spelling similarity, shared identity words),
* an optional embedding channel (injected encoder, brute-force cosine; no FAISS needed).

Hard bounds: at most ``max_candidates`` (never above :data:`HARD_MAX_CANDIDATES`) and a token
budget for the candidate section of any prompt. Candidates are typed: when a category is
given only same-category entities are returned. An unavailable encoder is reported as
``degraded``; if that leaves nothing, the status is ``index_unavailable``, never a silent
empty success. A shortlist is a retrieval result, not identity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .embeddings import Encoder, EmbeddingCache, EmbeddingUnavailable, embed_texts_cached
from .merge_safety import fold, is_acronym_of, looks_like_an_acronym

HARD_MAX_CANDIDATES = 50
DEFAULT_MAX_CANDIDATES = 8
DEFAULT_TOKEN_BUDGET = 400
#: Embedding cosine floor to enter the shortlist (a floor on retrieval, not on identity).
EMBEDDING_FLOOR = 0.30
LEXICAL_FLOOR = 0.60


def estimate_tokens(text: str) -> int:
    """Cheap, model-independent token estimate (about four characters per token)."""
    return math.ceil(len(text) / 4)


@dataclass(frozen=True)
class IndexedEntity:
    """An existing entity that a mention may resolve to."""

    entity_id: str
    label: str
    category: str = ""
    aliases: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    """One shortlist row. ``channels`` records why it was retrieved."""

    entity_id: str
    label: str
    category: str
    score: float
    channels: Tuple[str, ...]
    matched_text: str = ""


@dataclass(frozen=True)
class Shortlist:
    """Result of one search.

    Attributes:
        status: ``ok``, ``empty`` (searched, nothing close) or ``index_unavailable``.
        candidates: Ranked, bounded candidates.
        truncated: Candidates were dropped by the count cap or the token budget.
        dropped: How many were dropped.
        degraded: Channels that could not run (for example ``embedding_unavailable``).
        token_estimate: Estimated tokens of the rendered candidate block.
    """

    mention: str
    category: str
    status: str
    candidates: Tuple[Candidate, ...] = ()
    truncated: bool = False
    dropped: int = 0
    degraded: Tuple[str, ...] = ()
    token_estimate: int = 0

    def render(self) -> str:
        """The candidate block used in prompts (ids and labels only)."""
        return "\n".join(f"- {c.entity_id}: {c.label}" for c in self.candidates)


class CandidateSearch:
    """Typed shortlist over a fixed entity list."""

    def __init__(
        self,
        entities: Sequence[IndexedEntity],
        *,
        encoder: Optional[Encoder] = None,
        model: str = "",
        revision: str = "",
        cache: Optional[EmbeddingCache] = None,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
    ) -> None:
        self.entities = list(entities)
        self.encoder = encoder
        self.model = model
        self.revision = revision
        self.cache = cache if cache is not None else EmbeddingCache()
        self.max_candidates = max(1, min(int(max_candidates), HARD_MAX_CANDIDATES))
        self.token_budget = max(1, int(token_budget))
        self._rows: List[Tuple[int, str]] = []  # (entity index, surface)
        for i, ent in enumerate(self.entities):
            for surface in dict.fromkeys((ent.label, *ent.aliases)):
                self._rows.append((i, surface))
        self._matrix: Optional[np.ndarray] = None
        self._embedding_error: Optional[str] = None
        self._built = False

    def _build(self) -> None:
        if self._built:
            return
        self._built = True
        if self.encoder is None:
            self._embedding_error = "no encoder configured"
            return
        try:
            batch = embed_texts_cached([s for _, s in self._rows], self.encoder, model=self.model,
                                       revision=self.revision, cache=self.cache)
        except EmbeddingUnavailable as exc:
            self._embedding_error = str(exc)
            return
        norms = np.linalg.norm(batch.vectors, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        self._matrix = batch.vectors / norms

    def search(self, mention: str, category: Optional[str] = None) -> Shortlist:
        """Return a bounded shortlist for ``mention`` (never raises for encoder faults)."""
        self._build()
        key = fold(mention)
        if not key:
            return Shortlist(mention, category or "", "empty")
        scores: Dict[int, Tuple[float, set, str]] = {}

        def bump(i: int, score: float, channel: str, text: str) -> None:
            cur = scores.get(i)
            if cur is None:
                scores[i] = (score, {channel}, text)
            else:
                best = (score, text) if score > cur[0] else (cur[0], cur[2])
                scores[i] = (best[0], cur[1] | {channel}, best[1])

        mention_acronym = looks_like_an_acronym(mention)
        for i, surface in self._rows:
            ent = self.entities[i]
            if category and ent.category and ent.category != category:
                continue
            sk = fold(surface)
            if not sk:
                continue
            if sk == key:
                bump(i, 1.0, "exact", surface)
                continue
            if (mention_acronym or looks_like_an_acronym(surface)) and (
                    is_acronym_of(mention, surface) or is_acronym_of(surface, mention)):
                bump(i, 0.95, "acronym", surface)
                continue
            ratio = SequenceMatcher(None, key, sk).ratio()
            tokens_a, tokens_b = set(key.split()), set(sk.split())
            overlap = len(tokens_a & tokens_b) / max(1, min(len(tokens_a), len(tokens_b)))
            lexical = max(ratio, 0.5 * overlap + 0.3 * ratio)
            if lexical >= LEXICAL_FLOOR:
                bump(i, lexical, "lexical", surface)

        degraded: List[str] = []
        if self._matrix is not None and self.encoder is not None:
            try:
                q = embed_texts_cached([mention], self.encoder, model=self.model, revision=self.revision,
                                       cache=self.cache).vectors[0]
                norm = float(np.linalg.norm(q)) or 1.0
                sims = self._matrix @ (q / norm)
                for row in np.argsort(-sims)[: self.max_candidates * 4]:
                    i, surface = self._rows[int(row)]
                    ent = self.entities[i]
                    if (category and ent.category and ent.category != category) or sims[row] < EMBEDDING_FLOOR:
                        continue
                    bump(i, float(sims[row]), "embedding", surface)
            except EmbeddingUnavailable as exc:
                degraded.append("embedding_unavailable")
                self._embedding_error = str(exc)
        else:
            degraded.append("embedding_unavailable")

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1][0], self.entities[kv[0]].entity_id))
        candidates = [Candidate(self.entities[i].entity_id, self.entities[i].label, self.entities[i].category,
                                round(s, 4), tuple(sorted(ch)), text) for i, (s, ch, text) in ranked]
        kept: List[Candidate] = []
        used = estimate_tokens(mention)
        for cand in candidates:
            cost = estimate_tokens(f"- {cand.entity_id}: {cand.label}\n")
            if len(kept) >= self.max_candidates or used + cost > self.token_budget:
                break
            kept.append(cand)
            used += cost
        dropped = len(candidates) - len(kept)
        if not kept:
            failed = self.encoder is not None and "embedding_unavailable" in degraded
            status = "index_unavailable" if failed or not self._rows else "empty"
        else:
            status = "ok"
        return Shortlist(mention, category or "", status, tuple(kept), dropped > 0, dropped, tuple(degraded),
                         used)

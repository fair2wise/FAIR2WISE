"""Paper-picker ranking for the Extraction tab (A8, T38).

Keyword ranking over title, file name and DOI: each distinct query token scores by field weight, matches
in the title count most. Ordering is deterministic (score descending, then name). The ranker is a small
protocol, so T36's BM25 over the indexed chunks can replace :class:`KeywordRanker` without a route change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any, Protocol

_TOKEN = re.compile(r"[a-z0-9]+")
_WEIGHTS = (("title", 3.0), ("name", 2.0), ("doi", 1.0))


def tokens(text: object) -> list[str]:
    """Lower-case alphanumeric tokens of ``text`` (distinct, order kept)."""
    seen: dict[str, None] = {}
    for tok in _TOKEN.findall(str(text or "").lower()):
        seen.setdefault(tok)
    return list(seen)


class PaperRanker(Protocol):
    """Ranks library papers (dicts with ``title``, ``name``, ``doi``) for a topic or query."""

    def rank(self, papers: Sequence[Mapping[str, Any]], query: str) -> list[tuple[float, Mapping[str, Any]]]:
        """``(score, paper)`` pairs, best first; papers with score 0 are kept after the matches."""
        ...


class KeywordRanker:
    """Token-overlap ranking (the pre-index default)."""

    def rank(self, papers: Sequence[Mapping[str, Any]], query: str) -> list[tuple[float, Mapping[str, Any]]]:
        """Score every paper; an empty query scores all 0 and orders by name."""
        wanted = tokens(query)
        scored: list[tuple[float, Mapping[str, Any]]] = []
        for paper in papers:
            score = 0.0
            for field, weight in _WEIGHTS:
                have = set(tokens(paper.get(field)))
                score += weight * sum(1 for t in wanted if t in have)
            scored.append((score, paper))
        scored.sort(key=lambda sp: (-sp[0], str(sp[1].get("name") or sp[1].get("title") or "").lower()))
        return scored


def rank_papers(papers: Sequence[Mapping[str, Any]], query: str, ranker: PaperRanker | None = None) -> list[Mapping[str, Any]]:
    """Papers ordered best match first (use ``ranker`` for BM25 once the library is indexed)."""
    return [p for _s, p in (ranker or KeywordRanker()).rank(papers, query)]


__all__ = ("KeywordRanker", "PaperRanker", "rank_papers", "tokens")

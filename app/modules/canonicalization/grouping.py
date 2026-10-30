"""Context-aware candidate grouping for offline campaigns (C-05) with memory caps.

Terms are embedded together with their evidence snippets (never the bare surface: bare
surfaces rank a polymorph pair above a typo pair), grouped greedy single-link *within each
category*, and only proposed. ``material`` groups are proposal-only. A group is
auto-applicable only for allow-listed categories where every member had context, and even
then only as input to the safety check and an approved plan.

Memory: the donor built one n x n float32 matrix per category (11k terms is ~480 MB). Here
:func:`estimate_matrix_bytes` makes that cost explicit; above ``max_matrix_bytes`` the same
greedy algorithm runs *row-wise* (one matrix-vector product per seed), which gives identical
groups with O(n*d) memory. Above ``max_terms_per_category`` the call refuses with
:class:`GroupingTooLarge` instead of silently degrading semantics: block the input first.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/term_grouping.py (BSD-3-Clause).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Set

import numpy as np

AUTO_GROUPABLE = frozenset({"technique", "instrument", "analysis"})
PROPOSAL_ONLY = frozenset({"material"})
#: Only meaningful for context-embedded vectors; see the donor docstring on bare surfaces.
DEFAULT_THRESHOLD = 0.78
DEFAULT_MAX_MATRIX_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_TERMS_PER_CATEGORY = 50000


class GroupingTooLarge(ValueError):
    """The input exceeds the hard cap; reduce it by blocking (never silently approximate)."""


@dataclass
class TermGroup:
    """Terms proposed as naming one thing."""

    category: str
    representative: str
    members: List[str] = field(default_factory=list)
    min_similarity: float = 1.0
    auto_applicable: bool = True

    def as_dict(self) -> Dict[str, Any]:
        """Return the group as a plain dict (similarity rounded to 4 places)."""
        return {
            "category": self.category,
            "representative": self.representative,
            "members": list(self.members),
            "min_similarity": round(self.min_similarity, 4),
            "auto_applicable": self.auto_applicable,
        }


def embedding_text(surface: str, snippets: Sequence[Dict[str, str]]) -> str:
    """Surface first, then up to three evidence snippets."""
    parts = [surface.strip()]
    for snippet in list(snippets)[:3]:
        text = str(snippet.get("snippet") or "").strip()
        if text:
            parts.append(text)
    return ". ".join(parts)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity; 0.0 for a zero vector."""
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0.0 or nb == 0.0 else dot / (na * nb)


def estimate_matrix_bytes(n: int) -> int:
    """Bytes of the full float32 similarity matrix for ``n`` terms."""
    return 4 * n * n


def estimate_grouping_memory(
    n: int, dim: int, *, max_matrix_bytes: int = DEFAULT_MAX_MATRIX_BYTES
) -> Dict[str, Any]:
    """Memory estimate a caller can show before running: mode, matrix bytes and vector bytes."""
    matrix = estimate_matrix_bytes(n)
    return {
        "terms": n,
        "dim": dim,
        "vector_bytes": 4 * n * dim,
        "matrix_bytes": matrix,
        "mode": "matrix" if matrix <= max_matrix_bytes else "rowwise",
        "peak_bytes": 4 * n * dim + (matrix if matrix <= max_matrix_bytes else 4 * n),
    }


def _normalise(vectors: Sequence[Sequence[float]]) -> "np.ndarray":
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


def group_terms(
    terms: Sequence[Dict[str, Any]],
    vectors: Sequence[Sequence[float]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    max_matrix_bytes: int = DEFAULT_MAX_MATRIX_BYTES,
    max_terms_per_category: int = DEFAULT_MAX_TERMS_PER_CATEGORY,
) -> List[TermGroup]:
    """Group ``terms`` by vector similarity within each category.

    Raises:
        ValueError: ``terms`` and ``vectors`` differ in length (they match by position).
        GroupingTooLarge: a category exceeds ``max_terms_per_category``.
    """
    if len(terms) != len(vectors):
        raise ValueError(f"{len(terms)} terms and {len(vectors)} vectors; they are matched by position")
    by_category: Dict[str, List[int]] = {}
    for index, term in enumerate(terms):
        by_category.setdefault(str(term.get("category") or "unknown"), []).append(index)
    groups: List[TermGroup] = []
    for category, indices in sorted(by_category.items()):
        if len(indices) > max_terms_per_category:
            raise GroupingTooLarge(f"{category}: {len(indices)} terms > cap {max_terms_per_category}; block first")
        ordered = sorted(indices, key=lambda i: -int(terms[i].get("mentions") or 0))
        unit = _normalise([vectors[i] for i in ordered])
        position = {index: row for row, index in enumerate(ordered)}
        full = unit @ unit.T if estimate_matrix_bytes(len(ordered)) <= max_matrix_bytes else None
        taken: Set[int] = set()
        for seed in ordered:
            if seed in taken:
                continue
            row = full[position[seed]] if full is not None else unit @ unit[position[seed]]
            members, lowest = [seed], 1.0
            for other in ordered:
                if other == seed or other in taken:
                    continue
                score = float(row[position[other]])
                if score >= threshold:
                    members.append(other)
                    lowest = min(lowest, score)
            taken.update(members)
            if len(members) == 1:
                continue
            groups.append(
                TermGroup(
                    category=category,
                    representative=str(terms[seed].get("surface") or ""),
                    members=[str(terms[i].get("surface") or "") for i in members],
                    min_similarity=lowest,
                    auto_applicable=(
                        category in AUTO_GROUPABLE
                        and category not in PROPOSAL_ONLY
                        and all(bool(terms[i].get("has_context")) for i in members)
                    ),
                )
            )
    return groups


def summarize(groups: Sequence[TermGroup]) -> Dict[str, Any]:
    """Counts a reviewer can act on, split by whether a group may ever be applied."""
    applicable = [g for g in groups if g.auto_applicable]
    proposal = [g for g in groups if not g.auto_applicable]
    return {
        "groups": len(groups),
        "auto_applicable": len(applicable),
        "proposal_only": len(proposal),
        "terms_merged_if_applied": sum(len(g.members) - 1 for g in applicable),
        "terms_in_proposals": sum(len(g.members) for g in proposal),
        "by_category": {
            c: sum(1 for g in groups if g.category == c) for c in sorted({g.category for g in groups})
        },
    }

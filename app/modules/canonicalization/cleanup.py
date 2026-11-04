"""Vocabulary cleanup services (C-12): classify, report, propose, triage, digest, scoped plan.

Four small services instead of one monolith, none of which mutates a graph:

* :func:`classify_node` / :func:`quality_report`: deterministic classification and counts
  (candidate-term counts are reported separately from canonical entity counts);
* :func:`propose_cleanup`: per-term proposals (deterministic alias hit, quarantine class, optional
  resolver vote). Rejects are *proposals to review*: a deletion plan is never produced;
* :func:`triage_proposal` / :func:`format_review_digest`: reviewer-facing buckets and text;
* :func:`plan_cleanup`: only ``map_to`` proposals become a scoped merge plan. Applying it goes through
  :func:`.migration.apply_cleanup` (same gate, same approval requirement). Whole-graph destructive
  cleanup does not exist here.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/vocab_cleanup.py (BSD-3-Clause), split into services.
Not ported: store walking, ``merge_novel`` (vocabulary edits go through :mod:`.growth`), the LLM judge
transport and the in-place ``apply_cleanup(apply=True)``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .campaign.evidence import GraphRows, assemble, fold_terms
from .campaign.models import Decision, TermEvidence
from .migration import PlanDraft, apply_cleanup, plan_migration  # noqa: F401 - apply_cleanup re-exported
from .quarantine import check_context_row, classify_surface
from .vocabulary import Vocabulary

if TYPE_CHECKING:
    from .campaign.rounds import RoundResolver

MIN_NOVEL_EDGES = 3
MIN_REJECT_EDGES = 5


@dataclass(frozen=True)
class NodeClass:
    """Deterministic class of one candidate node: canonical, alias, quarantine, malformed or unknown."""

    kind: str
    slug: str = ""
    detail: str = ""


def classify_node(node: dict[str, Any], vocab: Vocabulary) -> NodeClass:
    """Classify one candidate node against the vocabulary.

    Returns a ``malformed`` class for an invalid context row, ``canonical`` or ``alias`` for a
    vocabulary match, ``quarantine`` for a surface that fails the quarantine checks, else ``unknown``.
    """
    problem = check_context_row(node)
    if problem:
        return NodeClass("malformed", detail=problem["code"])
    surface = str(node["surface"])
    entry = vocab.match(surface)
    if entry is not None:
        exact = " ".join(surface.split()).casefold() == entry.display_name.casefold()
        return NodeClass("canonical" if exact else "alias", entry.slug)
    reason = classify_surface(surface, vocab)
    return NodeClass("quarantine", detail=reason) if reason else NodeClass("unknown")


def quality_report(rows: GraphRows, vocab: Vocabulary) -> dict[str, Any]:
    """Counts by class and category. ``candidate_terms`` (distinct folded terms) is not an entity count."""
    classes = Counter(classify_node(n, vocab).kind for n in rows.nodes)
    folded = fold_terms(GraphRows([n for n in rows.nodes if not check_context_row(n)], rows.edges))
    return {
        "vocabulary_revision": vocab.revision,
        "source_revision": rows.revision(),
        "complete": rows.complete,
        "candidate_nodes": len(rows.nodes),
        "candidate_terms": len(folded),
        "by_class": dict(sorted(classes.items())),
        "canonical_vocabulary_entries": len(vocab.entries),
        "duplicate_spellings": sum(1 for s in folded.values() if len(s["variants"]) > 1),
    }


@dataclass(frozen=True)
class CleanupItem:
    """The proposed cleanup action for one candidate term."""

    term_key: str
    surface: str
    category: str
    action: str  # map_to | reject_candidate | novel | keep
    target_slug: str = ""
    reason: str = ""
    method: str = "deterministic"
    edges: int = 0


@dataclass(frozen=True)
class CleanupProposal:
    """A set of cleanup items with the revisions they were computed from and their term evidence."""

    source_revision: str
    vocabulary_revision: str
    items: tuple[CleanupItem, ...]
    evidence: dict[str, TermEvidence]


def propose_cleanup(
    rows: GraphRows, vocab: Vocabulary, *, resolver: RoundResolver | None = None, limit: int = 0
) -> CleanupProposal:
    """Propose an action per candidate term; optionally one resolver vote for undecided terms."""
    folded = fold_terms(GraphRows([n for n in rows.nodes if not check_context_row(n)], rows.edges))
    items: list[CleanupItem] = []
    evidence: dict[str, TermEvidence] = {}
    for key, slot in sorted(folded.items()):
        if limit and len(items) >= limit:
            break
        ev = assemble(rows, key, slot, vocab)
        evidence[key] = ev
        entry = vocab.match(ev.surface)
        if entry is not None:
            items.append(
                CleanupItem(key, ev.surface, ev.category, "map_to", entry.slug, "vocabulary alias", edges=ev.edges)
            )
            continue
        reason = classify_surface(ev.surface, vocab)
        if reason:
            items.append(
                CleanupItem(key, ev.surface, ev.category, "reject_candidate", reason=reason, edges=ev.edges)
            )
            continue
        if resolver is not None:
            vote = resolver.resolve(ev, round_key="cleanup", framing="menu_first")
            if vote.accepted and vote.verdict == "map_to":
                items.append(
                    CleanupItem(
                        key,
                        ev.surface,
                        ev.category,
                        "map_to",
                        vote.target_slug or "",
                        vote.reason,
                        "resolver",
                        ev.edges,
                    )
                )
                continue
            if vote.accepted and vote.verdict == "novel":
                items.append(
                    CleanupItem(
                        key,
                        ev.surface,
                        ev.category,
                        "novel",
                        reason=vote.reason,
                        method="resolver",
                        edges=ev.edges,
                    )
                )
                continue
        items.append(CleanupItem(key, ev.surface, ev.category, "keep", reason="undecided", edges=ev.edges))
    return CleanupProposal(rows.revision(), vocab.revision, tuple(items), evidence)


def triage_proposal(
    proposal: CleanupProposal, *, min_novel_edges: int = MIN_NOVEL_EDGES, min_reject_edges: int = MIN_REJECT_EDGES
) -> dict[str, list[CleanupItem]]:
    """Reviewer buckets. Low-support novel/reject items are *deferred*, never silently discarded."""
    buckets: dict[str, list[CleanupItem]] = {
        "plan_candidates": [],
        "novel_review": [],
        "reject_review": [],
        "deferred": [],
        "kept": [],
    }
    for item in proposal.items:
        if item.action == "map_to":
            buckets["plan_candidates"].append(item)
        elif item.action == "novel":
            buckets["novel_review" if item.edges >= min_novel_edges else "deferred"].append(item)
        elif item.action == "reject_candidate":
            buckets["reject_review" if item.edges >= min_reject_edges else "deferred"].append(item)
        else:
            buckets["kept"].append(item)
    return buckets


def format_review_digest(triaged: dict[str, list[CleanupItem]], limit: int = 0) -> str:
    """Plain-text digest: counts per bucket and the first ``limit`` rows of each (all when ``limit`` is 0)."""
    lines = []
    for name, items in triaged.items():
        lines.append(f"{name}: {len(items)}")
        for item in (items[:limit] if limit else items):
            lines.append(
                f"  - {item.surface!r} [{item.category}] -> {item.target_slug or item.action}"
                f" ({item.reason or item.method}, edges={item.edges})"
            )
    return "\n".join(lines)


def plan_cleanup(
    proposal: CleanupProposal,
    vocab: Vocabulary,
    *,
    scope: Sequence[str],
    target: str,
    created_at: datetime,
    report_sha256: str,
) -> PlanDraft:
    """Scoped merge plan from ``map_to`` items only. Reject candidates are never planned (no deletion)."""
    if vocab.revision != proposal.vocabulary_revision:
        raise ValueError("vocabulary changed since the proposal")
    decisions = [
        Decision(
            i.term_key,
            i.category,
            proposal.evidence[i.term_key].node_ids,
            i.target_slug,
            "cleanup_" + i.method,
            1.0,
            (f"cleanup:{i.method}:{i.target_slug}",),
        )
        for i in proposal.items
        if i.action == "map_to"
    ]
    return plan_migration(
        decisions,
        proposal.evidence,
        vocab,
        scope=scope,
        target=target,
        source_revision=proposal.source_revision,
        report_sha256=report_sha256,
        created_at=created_at,
    )

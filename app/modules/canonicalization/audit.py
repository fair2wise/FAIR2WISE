"""Vocabulary audit (C-15): find unsafe aliases and cross-entry collisions BEFORE activation.

The audit is a report. :func:`activate_vocabulary` refuses unless the gate is open, the vocabulary
is approved by an explicit :class:`~.vocabulary.VocabularyApproval` for its exact revision, and the
audit has no blocking findings. It returns an :class:`ActivationRecord` describing what would be
activated; it holds no global state and nothing activates by default.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/vocabulary_audit.py (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .gates import ApplyRefused, authorize_vocabulary_change
from .merge_safety import check_merge, fold
from .vocabulary import Vocabulary, VocabularyApproval


@dataclass(frozen=True)
class AliasVerdict:
    """Finding about one alias. ``blocking`` findings prevent activation."""

    slug: str
    alias: str
    kind: str  # unsafe_alias | cross_entry_rendering | empty_alias
    reason: str
    blocking: bool = True
    other_slug: Optional[str] = None


@dataclass(frozen=True)
class AuditReport:
    """Alias-audit findings for one vocabulary revision."""

    vocabulary_revision: str
    findings: Tuple[AliasVerdict, ...] = ()
    checked_aliases: int = 0

    @property
    def clean(self) -> bool:
        """True when no finding is blocking."""
        return not any(f.blocking for f in self.findings)

    def as_dict(self) -> Dict[str, Any]:
        """Return the report as a plain dict, findings included."""
        return {
            "vocabulary_revision": self.vocabulary_revision,
            "clean": self.clean,
            "checked_aliases": self.checked_aliases,
            "findings": [f.__dict__ for f in self.findings],
        }


def audit_aliases(vocab: Vocabulary, *, reviewed_ok: Iterable[Tuple[str, str]] = ()) -> List[AliasVerdict]:
    """Aliases that are not plausible renderings of their own entry (synonyms need a recorded review).

    Args:
        vocab: Vocabulary whose entries' aliases are checked.
        reviewed_ok: ``(slug, alias)`` pairs a human already reviewed as acceptable synonyms.
    """
    ok = {(s, fold(a)) for s, a in reviewed_ok}
    out: List[AliasVerdict] = []
    for entry in vocab.entries:
        for alias in entry.aliases:
            if not fold(alias):
                out.append(AliasVerdict(entry.slug, alias, "empty_alias", "alias is empty after folding"))
                continue
            verdict = check_merge(
                entry.display_name, alias, canonical_class=entry.category, alias_class=entry.category
            )
            if not verdict.allowed and (entry.slug, fold(alias)) not in ok:
                out.append(AliasVerdict(entry.slug, alias, "unsafe_alias", verdict.reason))
    return out


def audit_cross_entry(vocab: Vocabulary) -> List[AliasVerdict]:
    """An alias of one entry that is a rendering of a DIFFERENT entry's name: a wrong merge waiting."""
    out: List[AliasVerdict] = []
    surfaces = [(e, s) for e in vocab.entries for s in (e.display_name, *e.aliases)]
    for entry in vocab.entries:
        for alias in entry.aliases:
            for other, surface in surfaces:
                if other.slug == entry.slug or other.category != entry.category:
                    continue
                if check_merge(surface, alias, canonical_class=other.category, alias_class=entry.category).allowed:
                    out.append(
                        AliasVerdict(
                            entry.slug,
                            alias,
                            "cross_entry_rendering",
                            f"also reads as {surface!r} of {other.slug}",
                            other_slug=other.slug,
                        )
                    )
                    break
    return out


def audit_vocabulary(
    vocab: Vocabulary, *, reviewed_ok: Iterable[Tuple[str, str]] = (), cross_entry: bool = True
) -> AuditReport:
    """Run every audit. Pure report; changes nothing."""
    findings = audit_aliases(vocab, reviewed_ok=reviewed_ok)
    if cross_entry:
        findings += audit_cross_entry(vocab)
    return AuditReport(vocab.revision, tuple(findings), sum(len(e.aliases) for e in vocab.entries))


@dataclass(frozen=True)
class ActivationRecord:
    """What an authorized activation would put in force (a record, not a side effect)."""

    vocabulary_revision: str
    approval_id: str
    audit_checked_aliases: int = 0
    notes: Tuple[str, ...] = field(default_factory=tuple)


def activate_vocabulary(
    vocab: Vocabulary, approval: Optional[VocabularyApproval], report: AuditReport
) -> ActivationRecord:
    """Authorize activation of an approved, audited vocabulary revision. Closed pre-H3.

    Raises:
        ApplyRefused: gate closed, approval missing/mismatched, vocabulary not approved, audit stale or
            blocking.
    """
    authorize_vocabulary_change("activate_vocabulary", vocab.revision, approval, current_revision=vocab.revision)
    if not vocab.approved:
        raise ApplyRefused("vocabulary_not_approved", "only an approved vocabulary revision can be activated")
    if report.vocabulary_revision != vocab.revision:
        raise ApplyRefused("stale_audit", "audit was run on a different revision")
    if not report.clean:
        raise ApplyRefused("audit_blocking", f"{sum(f.blocking for f in report.findings)} blocking findings")
    return ActivationRecord(vocab.revision, approval.approval_id, report.checked_aliases)

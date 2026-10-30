"""Scoped migration plans and the one authorized apply engine (C-13).

A :class:`ScopedMergePlan` is an immutable, hash-bound description of merges of candidate nodes into
vocabulary entries. It records the source revision (hash of the rows it was computed from) and the
vocabulary revision, and maps onto the shared ``ChangePlan`` contract so an ``ApprovalRecord`` binds
to its exact hash. Plans are scoped: an explicit corpus scope and a bounded number of operations;
there is no whole-graph form.

Every apply route (``apply_migration``, ``apply_cleanup``, ``apply_campaign``) goes through
:func:`apply_scoped_plan`, which calls :func:`~.gates.authorize_apply`: gate open, plan hash intact,
``ApprovalRecord`` authorizing exactly this plan, source and vocabulary revisions unchanged. It also
requires an *approved* vocabulary and re-runs the merge-safety check on every operation. Dry runs
never touch a target and never need an approval. Nothing in this module constructs an approval.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/vocab_migration.py (BSD-3-Clause).
Rewritten: plan binding, revision checks and the single gate; the donor's in-place store walk and
``apply=True`` flag are not ported.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Mapping, Protocol, Sequence

from ..contracts.changes import ApprovalRecord, ChangePlan
from .campaign.models import Decision, TermEvidence
from .gates import ApplyRefused, authorize_apply
from .merge_safety import check_merge
from .vocabulary import Vocabulary

PLAN_VERSION = "t07-plan-1"
MAX_PLAN_OPS = 500
_FORBIDDEN_SCOPES = frozenset({"*", "all", "everything", ""})


@dataclass(frozen=True)
class MergeOp:
    """Fold one candidate term (its nodes) into one vocabulary entry."""

    term_key: str
    category: str
    surface: str
    variants: tuple[str, ...]
    node_ids: tuple[str, ...]
    target_slug: str
    method: str
    confidence: float
    votes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict with tuples as lists and confidence rounded to 4 places."""
        return {
            "term_key": self.term_key,
            "category": self.category,
            "surface": self.surface,
            "variants": list(self.variants),
            "node_ids": list(self.node_ids),
            "target_slug": self.target_slug,
            "method": self.method,
            "confidence": round(self.confidence, 4),
            "votes": list(self.votes),
        }


class MergeTarget(Protocol):
    """A graph-side adapter. The default flow supplies none."""

    def source_revision(self) -> str:
        """Return the revision of the graph rows, compared with the plan's ``source_revision``."""
        ...

    def merge(self, op: MergeOp) -> None:
        """Fold the operation's nodes into its target vocabulary entry; may raise on failure."""
        ...


def sha256_json(value: Any) -> str:
    """Return the SHA-256 hex digest of ``value`` as canonical JSON (sorted keys, compact, sets as lists)."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=list).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ScopedMergePlan:
    """Immutable merge plan bound to a source revision, a vocabulary revision and a plan hash."""

    plan_id: str
    scope: tuple[str, ...]
    target: str
    source_revision: str
    vocabulary_revision: str
    ops: tuple[MergeOp, ...]
    report_sha256: str
    created_at: str
    plan_sha256: str
    version: str = PLAN_VERSION

    def body(self) -> dict[str, Any]:
        """Return the hashed content: every field except the id, hash and ``plan_sha256``."""
        return {
            "version": self.version,
            "scope": list(self.scope),
            "target": self.target,
            "source_revision": self.source_revision,
            "vocabulary_revision": self.vocabulary_revision,
            "ops": [o.as_dict() for o in self.ops],
            "report_sha256": self.report_sha256,
            "created_at": self.created_at,
        }

    @classmethod
    def build(
        cls,
        *,
        scope: Sequence[str],
        target: str,
        source_revision: str,
        vocabulary_revision: str,
        ops: Sequence[MergeOp],
        report_sha256: str,
        created_at: str,
    ) -> ScopedMergePlan:
        """Create a plan, refusing empty/whole-graph scope, empty plans and oversized plans."""
        scope_t = tuple(sorted({str(s).strip() for s in scope}))
        if not scope_t or any(s.lower() in _FORBIDDEN_SCOPES for s in scope_t):
            raise ValueError("a plan needs an explicit corpus scope; whole-graph plans are not supported")
        if not ops:
            raise ValueError("a plan needs at least one operation")
        if len(ops) > MAX_PLAN_OPS:
            raise ValueError(f"{len(ops)} operations exceeds the plan cap of {MAX_PLAN_OPS}; split by scope")
        if len({o.term_key for o in ops}) != len(ops):
            raise ValueError("duplicate term in plan")
        body = {
            "version": PLAN_VERSION,
            "scope": list(scope_t),
            "target": target,
            "source_revision": source_revision,
            "vocabulary_revision": vocabulary_revision,
            "ops": [o.as_dict() for o in ops],
            "report_sha256": report_sha256,
            "created_at": created_at,
        }
        digest = sha256_json(body)
        return cls(
            "t07-" + digest[:16],
            scope_t,
            target,
            source_revision,
            vocabulary_revision,
            tuple(ops),
            report_sha256,
            created_at,
            digest,
        )

    def verify_hash(self) -> bool:
        """Return True if ``plan_sha256`` matches the body and ``plan_id`` derives from it."""
        return sha256_json(self.body()) == self.plan_sha256 and self.plan_id == "t07-" + self.plan_sha256[:16]

    def to_change_plan(self) -> ChangePlan:
        """The shared ``ChangePlan`` an ``ApprovalRecord`` binds to (supersessions = merged terms)."""
        return ChangePlan(
            plan_id=self.plan_id,
            snapshot_id=f"campaign-{self.source_revision[:12]}",
            input_sha256=self.source_revision,
            target=self.target,
            corpus_scope=self.scope,
            base_revision=self.vocabulary_revision,
            creates=0,
            links=0,
            supersessions=len(self.ops),
            affected_ids=tuple(sorted({n for o in self.ops for n in o.node_ids})),
            validation_report_sha256=self.report_sha256,
            plan_sha256=self.plan_sha256,
        )


@dataclass(frozen=True)
class PlanDraft:
    """A plan plus everything held back from it, with reasons (held terms stay visible)."""

    plan: ScopedMergePlan | None
    held: tuple[tuple[str, str], ...] = ()


def plan_migration(
    decisions: Sequence[Decision],
    evidence: Mapping[str, TermEvidence],
    vocabulary: Vocabulary,
    *,
    scope: Sequence[str],
    target: str,
    source_revision: str,
    report_sha256: str,
    created_at: datetime,
) -> PlanDraft:
    """Turn proposed decisions into a scoped plan. Unsafe or unknown-target decisions are held, not dropped."""
    ops: list[MergeOp] = []
    held: list[tuple[str, str]] = []
    for d in decisions:
        ev = evidence.get(d.term_key)
        entry = vocabulary.get(d.target_slug)
        if ev is None:
            held.append((d.term_key, "no evidence record for this term"))
        elif entry is None:
            held.append((d.term_key, f"unknown vocabulary slug {d.target_slug!r}"))
        else:
            reason = unsafe_reason(entry, ev.variants or (ev.surface,))
            if reason:
                held.append((d.term_key, reason))
            else:
                ops.append(
                    MergeOp(
                        d.term_key,
                        d.category,
                        ev.surface,
                        tuple(ev.variants),
                        tuple(ev.node_ids),
                        d.target_slug,
                        d.method,
                        d.confidence,
                        tuple(d.votes),
                    )
                )
    plan = None
    if ops:
        plan = ScopedMergePlan.build(
            scope=scope,
            target=target,
            source_revision=source_revision,
            vocabulary_revision=vocabulary.revision,
            ops=ops,
            report_sha256=report_sha256,
            created_at=created_at.isoformat(),
        )
    return PlanDraft(plan, tuple(held))


def unsafe_reason(entry: Any, variants: Sequence[str]) -> str:
    """Why folding ``variants`` into ``entry`` is refused, or ``""``. Every variant must be safe."""
    names = (entry.display_name, *entry.aliases)
    for variant in variants:
        verdicts = [check_merge(n, variant, canonical_class=entry.category) for n in names]
        if not any(v.allowed for v in verdicts):
            return f"{variant!r} vs {entry.slug}: {verdicts[0].reason}"
    return ""


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of an apply route. ``dry_run`` results never touched a target."""

    dry_run: bool
    plan_id: str
    would_apply: tuple[str, ...]
    applied: tuple[str, ...] = ()
    refused: tuple[tuple[str, str], ...] = ()


class ApplyFailed(RuntimeError):
    """A target raised part-way; ``applied`` lists the terms already merged (operator must reconcile)."""

    def __init__(self, applied: Sequence[str], cause: BaseException) -> None:
        super().__init__(f"apply failed after {len(applied)} operations: {cause}")
        self.applied = tuple(applied)


def apply_scoped_plan(
    gate: str,
    plan: ScopedMergePlan,
    approval: ApprovalRecord | None,
    target: MergeTarget | None,
    vocabulary: Vocabulary,
    *,
    dry_run: bool = True,
    now: datetime | None = None,
) -> ApplyResult:
    """The only code path that may call ``MergeTarget.merge``. Default is a dry run.

    Real application requires an open gate, an ``ApprovalRecord`` for this plan hash, matching live
    source and vocabulary revisions, an approved vocabulary and passing per-operation safety checks.
    """
    refused: list[tuple[str, str]] = []
    for op in plan.ops:
        entry = vocabulary.get(op.target_slug)
        reason = (
            f"unknown slug {op.target_slug!r}"
            if entry is None
            else unsafe_reason(entry, op.variants or (op.surface,))
        )
        if reason:
            refused.append((op.term_key, reason))
    if dry_run:
        return ApplyResult(
            True,
            plan.plan_id,
            tuple(o.term_key for o in plan.ops if o.term_key not in dict(refused)),
            refused=tuple(refused),
        )
    if target is None:
        raise ApplyRefused("no_target", "a merge target adapter is required for a real apply")
    authorize_apply(
        gate,
        plan,
        approval,
        current_source_revision=target.source_revision(),
        current_vocabulary_revision=vocabulary.revision,
        now=now,
    )
    if not vocabulary.approved:
        raise ApplyRefused("vocabulary_not_approved", "apply needs an approved vocabulary revision")
    if refused:
        raise ApplyRefused("unsafe_operation", f"{len(refused)} operations fail the safety check: {refused[0]}")
    done: list[str] = []
    try:
        for op in plan.ops:
            target.merge(op)
            done.append(op.term_key)
    except Exception as exc:  # noqa: BLE001
        raise ApplyFailed(done, exc) from exc
    return ApplyResult(False, plan.plan_id, tuple(o.term_key for o in plan.ops), tuple(done))


def apply_migration(
    plan: ScopedMergePlan, approval: ApprovalRecord | None, target: MergeTarget | None, vocabulary: Vocabulary, *,
    dry_run: bool = True, now: datetime | None = None,
) -> ApplyResult:
    """Apply a migration plan (gate ``apply_migration``)."""
    return apply_scoped_plan("apply_migration", plan, approval, target, vocabulary, dry_run=dry_run, now=now)


def apply_cleanup(
    plan: ScopedMergePlan, approval: ApprovalRecord | None, target: MergeTarget | None, vocabulary: Vocabulary, *,
    dry_run: bool = True, now: datetime | None = None,
) -> ApplyResult:
    """Apply a cleanup plan (gate ``apply_cleanup``)."""
    return apply_scoped_plan("apply_cleanup", plan, approval, target, vocabulary, dry_run=dry_run, now=now)


def apply_campaign(
    plan: ScopedMergePlan, approval: ApprovalRecord | None, target: MergeTarget | None, vocabulary: Vocabulary, *,
    dry_run: bool = True, now: datetime | None = None,
) -> ApplyResult:
    """Apply a campaign plan (gate ``apply_merge``)."""
    return apply_scoped_plan("apply_merge", plan, approval, target, vocabulary, dry_run=dry_run, now=now)

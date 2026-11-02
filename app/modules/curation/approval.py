"""Explicit, authenticated cost approval for remote model resolution (U-05).

A :class:`CostApproval` binds an amount, the allowed models, the corpora and an expiry. It is
created only by :func:`create_cost_approval` from a *human* context (interactive or CLI) holding
``curation.approve``; workers, delegates and model/tool calls are refused, so an agent cannot
grant itself authority. :func:`check_cost_approval` is the single check every remote call makes.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/cborg_approval.py (``write_approval``,
``check_approval``, ``revoke_approval``) (BSD-3-Clause), with authenticated creation and
no on-disk approval file that any process could write.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
import math
import uuid

from pydantic import BaseModel, ConfigDict, Field

from ..contracts.context import LOCAL_OPERATOR, ActorKind, RequestContext
from ..contracts.errors import ContractError, ErrorCode
from .store import CurationStore


class CostApproval(BaseModel):
    """What a human authorized: models, corpora, amount and time window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str
    scope: tuple[str, ...]
    models: tuple[str, ...]
    cap_usd: float = Field(gt=0)
    expires_at: datetime
    approved_by: str
    issued_at: datetime
    stage: str = "resolution"

    @property
    def budget_scope(self) -> str:
        """Ledger scope that this approval's spend is counted under."""
        return f"approval:{self.approval_id}"


def create_cost_approval(
    ctx: RequestContext,
    store: CurationStore,
    *,
    scope: Iterable[str],
    models: Iterable[str],
    cap_usd: float,
    expires_at: datetime,
    now: datetime | None = None,
    approval_id: str | None = None,
) -> CostApproval:
    """Record an approval made by the human in ``ctx``.

    Raises:
        ContractError: ``DENIED`` for a worker/delegate/tool context, a missing capability or corpora outside
            the approver's scope; ``INVALID_INPUT`` for a non-finite cap or an expiry in the past.
    """
    if ctx.actor_kind not in (ActorKind.INTERACTIVE, ActorKind.CLI):
        raise ContractError(ErrorCode.DENIED, "cost approvals are made by a human, never by a worker or model")
    if not ctx.has("curation.approve"):
        raise ContractError(ErrorCode.DENIED, "curation.approve is required")
    corpora = tuple(sorted(set(scope)))
    if ctx.principal.subject != LOCAL_OPERATOR and not set(corpora) <= set(ctx.corpus_scope):
        raise ContractError(ErrorCode.DENIED, "corpus is outside the approver's scope")
    moment = now or datetime.now(UTC)
    if not corpora or not math.isfinite(cap_usd) or cap_usd <= 0 or expires_at <= moment:
        raise ContractError(
            ErrorCode.INVALID_INPUT, "approval needs corpora, a positive finite cap and a future expiry"
        )
    approval = CostApproval(
        approval_id=approval_id or uuid.uuid4().hex,
        scope=corpora,
        models=tuple(sorted(set(models))),
        cap_usd=float(cap_usd),
        expires_at=expires_at,
        approved_by=ctx.principal.subject,
        issued_at=moment,
    )
    store.save_cost_approval(approval.approval_id, approval.model_dump(mode="json"))
    return approval


def load_cost_approval(store: CurationStore, approval_id: str) -> tuple[CostApproval, str]:
    """``(approval, status)`` where status is ``valid`` or ``revoked``."""
    found = store.get_cost_approval(approval_id)
    if found is None:
        raise ContractError(ErrorCode.DENIED, "no such cost approval")
    return CostApproval.model_validate(found[0]), found[1]


def check_cost_approval(
    approval: CostApproval | None,
    store: CurationStore,
    *,
    corpus: str,
    model: str,
    amount_usd: float,
    now: datetime | None = None,
) -> None:
    """Refuse remote resolution unless the cost approval permits this spend.

    The approval must be valid, unexpired, cover corpus and model, and have headroom for
    ``amount_usd``.
    """
    if approval is None:
        raise ContractError(ErrorCode.DENIED, "remote resolution requires an explicit cost approval")
    stored, status = load_cost_approval(store, approval.approval_id)
    if status != "valid" or stored != approval:
        raise ContractError(ErrorCode.DENIED, "cost approval is revoked or does not match its record")
    if (now or datetime.now(UTC)) >= approval.expires_at:
        raise ContractError(ErrorCode.DENIED, "cost approval expired")
    if corpus not in approval.scope or model not in approval.models:
        raise ContractError(ErrorCode.DENIED, "cost approval does not cover this corpus or model")
    summary = store.db.one(
        "SELECT COALESCE(SUM(COALESCE(actual_usd, reserved_usd)), 0) FROM budget_ledger WHERE scope=? "
        "AND state IN ('reserved','settled','settled_unknown')",
        (approval.budget_scope,),
    )
    if float(summary[0]) + amount_usd > approval.cap_usd:
        raise ContractError(ErrorCode.BUDGET_EXHAUSTED, "cost approval cap would be exceeded")


__all__ = ("CostApproval", "check_cost_approval", "create_cost_approval", "load_cost_approval")

"""One gate for every apply route (STOP H3).

Nothing in this package applies a merge, activates a vocabulary, promotes a vocabulary entry
or changes a graph unless (a) the owner-controlled gate below is open AND (b) an explicit
``ApprovalRecord`` authorizes the exact plan hash AND (c) the plan's source and vocabulary
revisions still match. Every apply route calls :func:`authorize_apply`; there is no second
implementation. Before H3 all gates are closed and no default flow constructs an approval.

The gates are not configuration: opening one is an owner decision recorded in review
(``docs/migration/reviews/H3``), made by editing this module, never by environment or argument.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Optional

from ..contracts.changes import ApprovalRecord, ChangePlan
from ..extraction import H3_GATES as _EXTRACTION_GATES

_GATES: dict = {
    "apply_merge": False,
    "apply_cleanup": False,
    "apply_migration": False,
    "activate_vocabulary": False,
    "promote_vocabulary": False,
}

#: Read-only view of the canonicalization gates (all closed pre-H3).
CANONICALIZATION_GATES: Mapping[str, bool] = MappingProxyType(_GATES)


class ApplyRefused(PermissionError):
    """An apply route refused to act; ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def require_gate(name: str) -> None:
    """Raise :class:`ApplyRefused` unless the named gate and extraction ``canonical_merge`` are open."""
    if name not in _GATES:
        raise KeyError(f"unknown canonicalization gate {name!r}")
    if not _GATES[name]:
        raise ApplyRefused("gate_closed", f"{name} is disabled until owner approval at H3")
    if not _EXTRACTION_GATES.get("canonical_merge", False):
        raise ApplyRefused("gate_closed", "extraction canonical_merge gate is closed until H3")


def authorize_apply(
    gate: str,
    plan: Any,
    approval: Optional[ApprovalRecord],
    *,
    current_source_revision: str,
    current_vocabulary_revision: str,
    now: Optional[datetime] = None,
) -> ChangePlan:
    """The single authorization check shared by every apply route.

    Order: gate open; plan body hash intact; approval present and authorizing the exact plan hash,
    target and scope; source revision and vocabulary revision unchanged since the plan was made.

    Args:
        gate: Gate name from :data:`CANONICALIZATION_GATES`.
        plan: A plan exposing ``verify_hash()``, ``to_change_plan()``, ``source_revision`` and
            ``vocabulary_revision`` (see :mod:`.migration`).
        approval: The human approval record; ``None`` always refuses.
        current_source_revision / current_vocabulary_revision: Revisions observed now.
        now: Clock override for tests.

    Returns:
        The ``ChangePlan`` that was authorized.

    Raises:
        ApplyRefused: With code ``gate_closed``, ``plan_tampered``, ``approval_missing``,
            ``approval_mismatch``, ``stale_source`` or ``stale_vocabulary``.
    """
    require_gate(gate)
    if not plan.verify_hash():
        raise ApplyRefused("plan_tampered", "plan body does not match its plan hash")
    if approval is None:
        raise ApplyRefused("approval_missing", "an ApprovalRecord for this exact plan is required")
    change_plan = plan.to_change_plan()
    moment = now or datetime.now(timezone.utc)
    if not approval.authorizes(change_plan, moment):
        raise ApplyRefused("approval_mismatch", "approval does not authorize this plan hash/target/scope")
    if plan.source_revision != current_source_revision:
        raise ApplyRefused("stale_source", "source revision changed since the plan was made")
    if plan.vocabulary_revision != current_vocabulary_revision:
        raise ApplyRefused("stale_vocabulary", "vocabulary revision changed since the plan was made")
    return change_plan

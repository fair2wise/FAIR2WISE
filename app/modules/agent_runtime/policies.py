"""Write guards, approval checks and minimal durable audit events (task T02).

New F2W code (no donor equivalent). Plan section 6.3: minimal audit events and
write guards land in T02. Events record approval requests, approval outcomes,
denials and write attempts. They never carry tool arguments or results (which
may contain private source text); only a SHA-256 of the canonical arguments.

The event sink is injected. The default :class:`JsonlEventSink` appends one JSON
object per line to a configured path and fsyncs; it raises if it cannot write, so
the executor can block a write it cannot audit (plan section 11.3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Mapping, Protocol, Union, runtime_checkable
import uuid

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import (
    ApprovalRecord,
    ChangePlan,
    Effect,
    ErrorCode,
    RequestContext,
    ToolSpec,
)

AUDIT_EVENT_VERSION = 1

#: Effects that write somewhere and are audited.
WRITE_EFFECTS = frozenset({Effect.WRITE_STAGED, Effect.WRITE_GRAPH, Effect.DESTRUCTIVE})

#: Effects that additionally need an explicit human ApprovalRecord for the exact plan.
APPROVAL_EFFECTS = frozenset({Effect.WRITE_GRAPH, Effect.DESTRUCTIVE})


class AuditKind(str, Enum):
    """What an audit event records."""

    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    TOOL_DENIED = "tool_denied"
    WRITE_ATTEMPTED = "write_attempted"
    WRITE_SUCCEEDED = "write_succeeded"
    WRITE_FAILED = "write_failed"


class AuditEvent(BaseModel):
    """One audit record. Candidate for promotion to ``app.modules.contracts``.

    Attributes:
        version: Event schema version.
        event_id: Unique ID; deterministic for approval requests so replays can be deduplicated.
        kind: What happened.
        at: When it happened (UTC).
        request_id: Request correlation ID from the context.
        run_id: Run ID from the context, if any.
        thread_id: LangGraph thread, if the event came from a supervisor run.
        subject: Acting principal's subject.
        profile: Deployment profile value.
        tool: Canonical tool name, if any.
        tool_call_id: Model tool-call ID, if any.
        effects: The tool's declared effects.
        args_sha256: SHA-256 of canonical JSON arguments (never the arguments).
        plan_id: Change plan, if any.
        plan_sha256: Plan hash, if any.
        approval_id: Approval presented, if any.
        approved_by: Approver recorded on that approval, if any.
        error_code: Error code for denials and failures.
        detail: Short, non-sensitive explanation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = AUDIT_EVENT_VERSION
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    kind: AuditKind
    at: datetime
    request_id: str
    run_id: str | None = None
    thread_id: str | None = None
    subject: str
    profile: str
    tool: str | None = None
    tool_call_id: str | None = None
    effects: tuple[str, ...] = ()
    args_sha256: str | None = None
    plan_id: str | None = None
    plan_sha256: str | None = None
    approval_id: str | None = None
    approved_by: str | None = None
    error_code: ErrorCode | None = None
    detail: str = Field(default="", max_length=500)


def args_digest(args: Any) -> str:
    """SHA-256 of canonical JSON for ``args``."""
    body = json.dumps(args, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def make_event(
    kind: AuditKind,
    ctx: RequestContext,
    *,
    at: datetime,
    spec: ToolSpec | None = None,
    args: Any = None,
    plan: ChangePlan | None = None,
    approval: ApprovalRecord | None = None,
    error_code: ErrorCode | None = None,
    detail: str = "",
    thread_id: str | None = None,
    tool_call_id: str | None = None,
    event_id: str | None = None,
) -> AuditEvent:
    """Build an :class:`AuditEvent` from runtime objects."""
    fields = dict(
        kind=kind, at=at, request_id=ctx.request_id, run_id=ctx.run_id, thread_id=thread_id,
        subject=ctx.principal.subject, profile=ctx.profile.value, tool_call_id=tool_call_id,
        tool=spec.name if spec else None,
        effects=tuple(sorted(e.value for e in spec.effects)) if spec else (),
        args_sha256=args_digest(args) if args is not None else None,
        plan_id=plan.plan_id if plan else None, plan_sha256=plan.plan_sha256 if plan else None,
        approval_id=approval.approval_id if approval else None,
        approved_by=approval.approved_by if approval else None,
        error_code=error_code, detail=detail[:500],
    )
    if event_id:
        fields["event_id"] = event_id
    return AuditEvent(**fields)


@runtime_checkable
class EventSink(Protocol):
    """Receives audit events. ``emit`` must raise if the event was not recorded."""

    def emit(self, event: AuditEvent) -> None:
        """Record ``event`` durably or raise."""
        ...


class InMemoryEventSink:
    """Event sink for tests: keeps events in a list."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def emit(self, event: AuditEvent) -> None:
        """Append ``event``."""
        with self._lock:
            self.events.append(event)

    def kinds(self) -> list[AuditKind]:
        """Event kinds in emission order."""
        return [e.kind for e in self.events]


class FailingEventSink:
    """Sink that always fails, for testing that unauditable writes are blocked."""

    def emit(self, event: AuditEvent) -> None:
        """Raise ``OSError``."""
        raise OSError("audit sink unavailable")


class JsonlEventSink:
    """Append-only JSON-lines sink at a configured path.

    The file is opened per event in append mode and fsynced, so a crash loses at
    most the event being written. The parent directory must exist; nothing is
    created at import or construction.

    Args:
        path: Target file.
        fsync: Whether to fsync after each write (default ``True``).
    """

    def __init__(self, path: Union[str, Path], *, fsync: bool = True) -> None:
        self.path = Path(path)
        self._fsync = fsync
        self._lock = threading.Lock()

    def emit(self, event: AuditEvent) -> None:
        """Append ``event`` as one JSON line; raises ``OSError`` on failure."""
        line = event.model_dump_json() + "\n"
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                if self._fsync:
                    os.fsync(fh.fileno())


class NullEventSink:
    """Sink that records nothing. Writes are refused when it is the configured sink."""

    durable = False

    def emit(self, event: AuditEvent) -> None:
        """Discard ``event``."""


# -- approval checks ------------------------------------------------------------


class ApprovalDecision(BaseModel):
    """Outcome of an approval check."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    error_code: ErrorCode | None = None
    reason: str = ""


ALLOW = ApprovalDecision(allowed=True)


@runtime_checkable
class ApprovalCheck(Protocol):
    """Decides whether ``approval`` authorizes the ``plan`` a write tool call would apply."""

    def __call__(
        self,
        ctx: RequestContext,
        spec: ToolSpec,
        args: Mapping[str, Any],
        *,
        plan: ChangePlan | None,
        approval: ApprovalRecord | None,
        now: datetime,
    ) -> ApprovalDecision:
        """Return an :class:`ApprovalDecision`."""
        ...


class PlanApprovalCheck:
    """Default check: an ApprovalRecord from this profile that ``authorizes()`` the exact plan.

    Rules, in order:

    1. No plan -> ``DENIED`` (nothing to approve).
    2. No approval -> ``DENIED``.
    3. Approval made under another profile/mode -> ``DENIED``.
    4. Plan outside the request's corpus scope (when the context is scoped) -> ``DENIED``.
    5. ``plan_id``/``plan_sha256`` arguments disagree with the plan, or the
       approval does not ``authorizes(plan, now)`` (other plan, hash, target,
       scope, or expired) -> ``STALE_APPROVAL``.
    """

    def __call__(
        self,
        ctx: RequestContext,
        spec: ToolSpec,
        args: Mapping[str, Any],
        *,
        plan: ChangePlan | None,
        approval: ApprovalRecord | None,
        now: datetime,
    ) -> ApprovalDecision:
        """Apply the rules above."""
        if plan is None:
            return ApprovalDecision(allowed=False, error_code=ErrorCode.DENIED,
                                    reason="no change plan is available for this write")
        if approval is None:
            return ApprovalDecision(allowed=False, error_code=ErrorCode.DENIED,
                                    reason="this write needs an explicit approval of the exact plan")
        if approval.profile is not ctx.profile or approval.mode is not ctx.mode:
            return ApprovalDecision(allowed=False, error_code=ErrorCode.DENIED,
                                    reason="approval was made under a different deployment profile")
        if ctx.corpus_scope and not set(plan.corpus_scope) <= set(ctx.corpus_scope):
            return ApprovalDecision(allowed=False, error_code=ErrorCode.DENIED,
                                    reason="plan affects corpora outside this request's scope")
        for key in ("plan_id", "plan_sha256"):
            if key in args and args[key] != getattr(plan, key):
                return ApprovalDecision(allowed=False, error_code=ErrorCode.STALE_APPROVAL,
                                        reason=f"tool argument {key} does not match the plan")
        if not approval.authorizes(plan, now):
            return ApprovalDecision(
                allowed=False, error_code=ErrorCode.STALE_APPROVAL,
                reason="approval does not cover this plan (plan, hash, target, scope or expiry)")
        return ALLOW


def needs_approval(spec: ToolSpec) -> bool:
    """Whether calls to ``spec`` require a human approval of the exact plan."""
    return bool(spec.effects & APPROVAL_EFFECTS)


def is_write(spec: ToolSpec) -> bool:
    """Whether ``spec`` declares any write effect."""
    return bool(spec.effects & WRITE_EFFECTS)


def utcnow() -> datetime:
    """Timezone-aware current UTC time (the default clock)."""
    return datetime.now(UTC)

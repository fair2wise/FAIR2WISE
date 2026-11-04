"""Authorizing tool executor (task T02, R-04 execution mechanics).

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/graph.py
(``_call_key``, ``_already_called``, ``_execute_tools_node``) (BSD-3-Clause).
Copyright (c) 2026, The Regents of the University of California, through Lawrence
Berkeley National Laboratory. See the FORGE LICENSE for the full notice.

Kept from the donor: a stable call key and refusal of exact repeats within one
turn, answered from the first call's result instead of being re-run.

New, before any tool runs (in this order): the tool exists (canonical name), the
context holds every required capability (narrowed by an optional turn scope),
arguments validate against ``input_schema`` (``jsonschema`` Draft 2020-12), the
deadline has not passed, the call is not an exact repeat, the call and cost
budget allow it, and, for graph-write/destructive effects, an injected approval
check accepts an ``ApprovalRecord`` for the exact ``ChangePlan``. Writes are
audited before the effect; if the audit cannot be recorded the write is refused.

Failures become typed :class:`~app.modules.contracts.ToolResult` values.
Only transient failures (``UNAVAILABLE``/retryable, ``TimeoutError``,
``ConnectionError``) of idempotent tools are retried. Cancellation, interpreter
exits and LangGraph control-flow exceptions (``GraphBubbleUp``, which includes
``GraphInterrupt``) are never swallowed. Provider unavailability is reported as
``UNAVAILABLE``, never as an empty or successful result. Outputs larger than the
configured bound are replaced by a preview and, when a document store is
injected, an artifact reference.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
import inspect
import json
import logging
import sys
import time
from typing import TYPE_CHECKING, Any, TypeVar, cast

from ..contracts import (
    ApprovalRecord,
    ChangePlan,
    ContractError,
    CostClass,
    ErrorCode,
    RequestContext,
    ToolResult,
    ToolSpec,
    ToolStatus,
)
from .admission import ensure_admission
from .policies import (
    ApprovalCheck,
    AuditKind,
    EventSink,
    NullEventSink,
    PlanApprovalCheck,
    is_write,
    make_event,
    needs_approval,
    utcnow,
)
from .registry import RegisteredTool, ToolRegistry, effective_capabilities

if TYPE_CHECKING:
    from ..contracts.ports import DocumentStore
    from .admission import Admission

logger = logging.getLogger(__name__)

#: Budget units charged per call, by cost class.
COST_UNITS: Mapping[CostClass, int] = {
    CostClass.FREE: 0, CostClass.CHEAP: 1, CostClass.MODERATE: 5, CostClass.EXPENSIVE: 20,
}

_STATUS_FOR_CODE = {
    ErrorCode.UNAVAILABLE: ToolStatus.UNAVAILABLE,
    ErrorCode.INVALID_INPUT: ToolStatus.INVALID,
    ErrorCode.DENIED: ToolStatus.DENIED,
}


@dataclass(frozen=True)
class RuntimeLimits:
    """Per-turn runtime limits. Neutral starting values, not donor catalog budgets.

    Attributes:
        max_tool_calls: Tool invocations allowed per turn (repeats and refusals are free).
        max_cost_units: Sum of :data:`COST_UNITS` allowed per turn.
        max_model_calls: Supervisor model calls allowed per turn.
        max_output_chars: Largest serialized ``ToolResult.data`` returned inline.
        max_retries: Extra attempts for transient failures of idempotent tools.
        retry_backoff_s: Base backoff; attempt ``n`` waits ``retry_backoff_s * 2**(n-1)``.
        stale_tool_result_chars: Stale tool output kept in model context (message trimming).
        recursion_limit: LangGraph recursion limit suggested to callers.
        approval_request_ttl_s: How long an approval request stays open (seconds) before
            it is refused as expired; ``None`` means no runtime expiry (T02B).
    """

    max_tool_calls: int = 12
    max_cost_units: int = 60
    max_model_calls: int = 16
    max_output_chars: int = 8000
    max_retries: int = 2
    retry_backoff_s: float = 0.5
    stale_tool_result_chars: int = 120_000
    recursion_limit: int = 64
    approval_request_ttl_s: float | None = 86_400.0


@dataclass
class TurnLedger:
    """What this turn has already spent and seen. Rebuilt from message history each step."""

    calls_used: int = 0
    cost_used: int = 0
    seen: dict[str, ToolResult] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolInvocation:
    """Everything a tool implementation receives. Built by the executor, never by a model.

    Attributes:
        ctx: Server-built request context.
        spec: The tool's contract.
        args: Validated arguments (a copy).
        admission: Injected admission object; tools that call a provider hold
            ``admission.slot(provider)`` / ``aslot(provider)`` around the call.
        artifact_store: Optional document store for large outputs.
        deadline: Absolute deadline, if any.
        attempt: 1-based attempt number.
        tool_call_id: Model tool-call ID, if the call came from a supervisor.
        plan: Approved change plan, for approval-gated writes.
        approval: The approval that authorized ``plan``.
    """

    ctx: RequestContext
    spec: ToolSpec
    args: dict[str, Any]
    admission: Admission
    artifact_store: Any = None
    deadline: datetime | None = None
    attempt: int = 1
    tool_call_id: str | None = None
    plan: ChangePlan | None = None
    approval: ApprovalRecord | None = None


@dataclass(frozen=True)
class ToolOutcome:
    """Executor result plus bookkeeping for the supervisor.

    Attributes:
        result: The typed tool result.
        key: Stable call key.
        cost: Budget units charged.
        charged: Whether the tool was actually invoked.
        repeated: Whether this was a refused exact repeat.
        attempts: Invocation attempts made.
    """

    result: ToolResult
    key: str
    cost: int = 0
    charged: bool = False
    repeated: bool = False
    attempts: int = 0


def call_key(tool_name: str, tool_input: object) -> str:
    """Stable identity for one tool call, for repeat detection (donor ``_call_key``)."""
    try:
        return f"{tool_name}({json.dumps(tool_input, sort_keys=True, default=str)})"
    except (TypeError, ValueError):  # value is not JSON-serializable: fall back to repr
        return f"{tool_name}({tool_input!r})"


def is_control_flow(exc: BaseException) -> bool:
    """Whether ``exc`` must propagate untouched.

    True for every non-``Exception`` (``asyncio.CancelledError``, ``KeyboardInterrupt``,
    ``SystemExit``, ``GeneratorExit``), for ``ContractError(CANCELLED)``, and for
    LangGraph ``GraphBubbleUp`` (``GraphInterrupt``, ``ParentCommand``). LangGraph is
    looked up in ``sys.modules`` so this module does not import it: if it is not
    loaded, none of its exceptions can exist.
    """
    if not isinstance(exc, Exception):
        return True
    if isinstance(exc, ContractError) and exc.code is ErrorCode.CANCELLED:
        return True
    lg_errors = sys.modules.get("langgraph.errors")
    bubble = getattr(lg_errors, "GraphBubbleUp", None) if lg_errors is not None else None
    return bubble is not None and isinstance(exc, bubble)


def _fail(tool: str, code: ErrorCode, message: str, *, retryable: bool = False) -> ToolResult:
    status = _STATUS_FOR_CODE.get(code, ToolStatus.ERROR)
    return ToolResult(tool=tool, status=status, error_code=code, retryable=retryable, warnings=(message[:300],))


def classify_exception(tool: str, exc: Exception) -> ToolResult:
    """Map a tool exception to a typed result (control-flow exceptions must be filtered first)."""
    if isinstance(exc, ContractError):
        return _fail(tool, exc.code, str(exc), retryable=exc.retryable)
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return _fail(tool, ErrorCode.UNAVAILABLE, f"dependency unavailable ({type(exc).__name__})", retryable=True)
    logger.error("tool %s failed", tool, exc_info=exc)
    return _fail(tool, ErrorCode.INTERNAL, f"tool failed ({type(exc).__name__})")


class ToolExecutor:
    """Checks, invokes and records tool calls for one runtime.

    Args:
        registry: The runtime's tool registry (ideally frozen).
        approval_check: Decides approval-gated writes; default :class:`PlanApprovalCheck`.
        event_sink: Audit sink. ``None`` means no durable audit, so writes are refused.
        admission: Object with ``slot``/``aslot``; default no-op. Passed to tools.
        limits: :class:`RuntimeLimits`.
        artifact_store: Optional ``DocumentStore`` for oversized outputs.
        clock: Returns aware ``datetime`` now.
        sleep: Sync sleep used between retries.
        asleep: Async sleep used between retries.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        approval_check: ApprovalCheck | None = None,
        event_sink: EventSink | None = None,
        admission: Admission | None = None,
        limits: RuntimeLimits | None = None,
        artifact_store: DocumentStore | None = None,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], None] = time.sleep,
        asleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.registry = registry
        self.approval_check = approval_check or PlanApprovalCheck()
        self.event_sink = event_sink if event_sink is not None else NullEventSink()
        self.admission = ensure_admission(admission)
        self.limits = limits or RuntimeLimits()
        self.artifact_store = artifact_store
        self.clock = clock
        self._sleep = sleep
        self._asleep = asleep

    # -- public API -------------------------------------------------------------

    def execute(self, ctx: RequestContext, name: str, args: Mapping[str, Any], **kw: Any) -> ToolResult:  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
        """Run one call synchronously and return its :class:`ToolResult`."""
        return self.execute_call(ctx, name, args, **kw).result

    async def aexecute(self, ctx: RequestContext, name: str, args: Mapping[str, Any], **kw: Any) -> ToolResult:  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
        """Run one call asynchronously and return its :class:`ToolResult`."""
        return (await self.aexecute_call(ctx, name, args, **kw)).result

    def execute_call(
        self, ctx: RequestContext, name: str, args: Mapping[str, Any], *,
        ledger: TurnLedger | None = None, plan: ChangePlan | None = None,
        approval: ApprovalRecord | None = None, scope: frozenset[str] | None = None,
        tool_call_id: str | None = None, thread_id: str | None = None,
    ) -> ToolOutcome:
        """Run one call synchronously; the counterpart of :meth:`aexecute_call`.

        An ``async`` tool is run with ``asyncio.run`` when no event loop is running,
        and refused otherwise.
        """
        prep = self._prepare(ctx, name, args, ledger, plan, approval, scope, tool_call_id, thread_id)
        if isinstance(prep, ToolOutcome):
            return prep
        attempt, result = 0, None
        while True:
            attempt += 1
            inv = prep.invocation(attempt)
            result = self._invoke_sync(prep.entry, inv)
            if not self._should_retry(prep.entry.spec, result, attempt, inv.deadline):
                break
            self._sleep(self._backoff(attempt))
        return self._finish(prep, result, attempt, ledger)

    async def aexecute_call(
        self, ctx: RequestContext, name: str, args: Mapping[str, Any], *,
        ledger: TurnLedger | None = None, plan: ChangePlan | None = None,
        approval: ApprovalRecord | None = None, scope: frozenset[str] | None = None,
        tool_call_id: str | None = None, thread_id: str | None = None,
    ) -> ToolOutcome:
        """Check, invoke (with transient retries) and record one tool call.

        Args:
            ctx: Server-built request context.
            name: Canonical tool name.
            args: Arguments as supplied by the model or caller.
            ledger: Turn ledger to check and update (repeat/call/cost budgets).
            plan: Change plan this call would apply (approval-gated tools).
            approval: Approval presented for ``plan``.
            scope: Optional per-turn capability narrowing.
            tool_call_id: Model tool-call ID for audit correlation.
            thread_id: Supervisor thread for audit correlation.
        """
        prep = self._prepare(ctx, name, args, ledger, plan, approval, scope, tool_call_id, thread_id)
        if isinstance(prep, ToolOutcome):
            return prep
        attempt, result = 0, None
        while True:
            attempt += 1
            inv = prep.invocation(attempt)
            result = await self._invoke_async(prep.entry, inv)
            if not self._should_retry(prep.entry.spec, result, attempt, inv.deadline):
                break
            await self._asleep(self._backoff(attempt))
        return self._finish(prep, result, attempt, ledger)

    # -- preflight ----------------------------------------------------------------

    def _prepare(
        self, ctx: RequestContext, name: str, args: Mapping[str, Any], ledger: TurnLedger | None,
        plan: ChangePlan | None, approval: ApprovalRecord | None, scope: frozenset[str] | None,
        tool_call_id: str | None, thread_id: str | None,
    ) -> ToolOutcome | _Prepared:
        key = call_key(name, args)
        now = self.clock()
        entry = self.registry.get(name)
        if entry is None:
            return ToolOutcome(_fail(name, ErrorCode.NOT_FOUND, f"unknown tool {name!r}"), key)
        spec = entry.spec
        audit = _Audit(self, ctx, spec, args, thread_id, tool_call_id)

        missing = spec.required_capabilities - effective_capabilities(ctx, scope)
        if missing:
            audit.best_effort(AuditKind.TOOL_DENIED, now, ErrorCode.DENIED,
                              f"missing capabilities {sorted(missing)}")
            return ToolOutcome(_fail(name, ErrorCode.DENIED, "this request is not permitted to use this tool"),
                               key)

        if not isinstance(args, Mapping):
            return ToolOutcome(_fail(name, ErrorCode.INVALID_INPUT, "arguments must be an object"), key)
        problem = _validate(spec.input_schema, dict(args))
        if problem:
            return ToolOutcome(_fail(name, ErrorCode.INVALID_INPUT, f"invalid arguments: {problem}"), key)

        if ctx.deadline is not None and now >= ctx.deadline:
            return ToolOutcome(_fail(name, ErrorCode.BUDGET_EXHAUSTED, "request deadline has passed"), key)

        if ledger is not None and key in ledger.seen:
            prior = ledger.seen[key]
            note = ("repeated_call: identical call already made this turn; result repeated, not re-run. "
                    "Use it, try a materially different approach, or answer with what you have.")
            return ToolOutcome(prior.model_copy(update={"warnings": (*prior.warnings, note)}), key, repeated=True)

        cost = COST_UNITS.get(spec.cost_class, 0)
        if ledger is not None:
            if ledger.calls_used >= self.limits.max_tool_calls:
                return ToolOutcome(
                    _fail(name, ErrorCode.BUDGET_EXHAUSTED, "tool-call budget for this turn is used up"), key)
            if ledger.cost_used + cost > self.limits.max_cost_units:
                return ToolOutcome(
                    _fail(name, ErrorCode.BUDGET_EXHAUSTED, "cost budget for this turn is used up"), key)

        if needs_approval(spec):
            decision = self.approval_check(ctx, spec, args, plan=plan, approval=approval, now=now)
            if not decision.allowed:
                code = decision.error_code or ErrorCode.DENIED
                audit.best_effort(AuditKind.TOOL_DENIED, now, code, decision.reason, plan=plan, approval=approval)
                return ToolOutcome(_fail(name, code, decision.reason), key)

        if is_write(spec):
            if not getattr(self.event_sink, "durable", True):
                return ToolOutcome(_fail(name, ErrorCode.UNAVAILABLE,
                                         "no durable audit sink is configured; writes are refused"), key)
            try:
                audit.emit(AuditKind.WRITE_ATTEMPTED, now, None, "", plan=plan, approval=approval)
            except Exception as exc:  # noqa: BLE001 - an unauditable write must not run
                logger.error("audit sink failed; refusing write %s", name, exc_info=exc)
                return ToolOutcome(_fail(name, ErrorCode.UNAVAILABLE,
                                         "audit could not be recorded; write refused", retryable=True), key)

        return _Prepared(self, ctx, entry, dict(args), key, cost, plan, approval, tool_call_id, audit)

    # -- invocation ------------------------------------------------------------------

    def _invoke_sync(self, entry: RegisteredTool, inv: ToolInvocation) -> ToolResult:
        try:
            raw = entry.fn(inv)
            if inspect.isawaitable(raw):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    raw = asyncio.run(_await_with_deadline(raw, inv.deadline, self.clock))
                else:
                    _close(raw)
                    return _fail(entry.name, ErrorCode.INTERNAL,
                                 "async tool called synchronously inside a running event loop; use aexecute")
        except BaseException as exc:
            if is_control_flow(exc):
                raise
            if isinstance(exc, _DeadlineExceeded):
                return _fail(entry.name, ErrorCode.BUDGET_EXHAUSTED, "request deadline reached during the call")
            return classify_exception(entry.name, cast(Exception, exc))  # control-flow BaseExceptions re-raised above
        return self._normalize(entry.spec, raw)

    async def _invoke_async(self, entry: RegisteredTool, inv: ToolInvocation) -> ToolResult:
        try:
            if inspect.iscoroutinefunction(entry.fn) or inspect.iscoroutinefunction(
                    getattr(entry.fn, "__call__", None)):
                raw = await _await_with_deadline(entry.fn(inv), inv.deadline, self.clock)
            else:
                raw = await _await_with_deadline(asyncio.to_thread(entry.fn, inv), inv.deadline, self.clock)
                if inspect.isawaitable(raw):
                    raw = await _await_with_deadline(raw, inv.deadline, self.clock)
        except BaseException as exc:
            if is_control_flow(exc):
                raise
            if isinstance(exc, _DeadlineExceeded):
                return _fail(entry.name, ErrorCode.BUDGET_EXHAUSTED, "request deadline reached during the call")
            return classify_exception(entry.name, cast(Exception, exc))  # control-flow BaseExceptions re-raised above
        return self._normalize(entry.spec, raw)

    def _normalize(self, spec: ToolSpec, raw: object) -> ToolResult:
        if isinstance(raw, ToolResult):
            result = raw if raw.tool == spec.name else raw.model_copy(update={"tool": spec.name})
        elif raw is None or (isinstance(raw, Mapping) and not raw):
            result = ToolResult(tool=spec.name, status=ToolStatus.EMPTY)
        elif isinstance(raw, Mapping):
            result = ToolResult(tool=spec.name, status=ToolStatus.OK, data=dict(raw))
        else:
            return _fail(spec.name, ErrorCode.INTERNAL, f"tool returned unsupported type {type(raw).__name__}")
        if result.status in (ToolStatus.OK, ToolStatus.PARTIAL) and result.data is not None:
            problem = _validate(spec.output_schema, result.data)
            if problem:
                logger.error("tool %s output failed its output_schema: %s", spec.name, problem)
                return _fail(spec.name, ErrorCode.INTERNAL, "tool output did not match its declared schema")
        return result

    def _should_retry(
        self, spec: ToolSpec, result: ToolResult, attempt: int, deadline: datetime | None,
    ) -> bool:
        if not (result.retryable and spec.idempotent):
            return False
        if result.error_code not in (ErrorCode.UNAVAILABLE, ErrorCode.CONFLICT):
            return False
        if attempt > self.limits.max_retries:
            return False
        return deadline is None or self.clock() < deadline

    def _backoff(self, attempt: int) -> float:
        return cast(float, self.limits.retry_backoff_s * (2 ** (attempt - 1)))

    # -- completion ---------------------------------------------------------------

    def _finish(
        self, prep: _Prepared, result: ToolResult, attempts: int, ledger: TurnLedger | None,
    ) -> ToolOutcome:
        result = self._bound(prep.ctx, result)
        if is_write(prep.entry.spec):
            ok = result.status in (ToolStatus.OK, ToolStatus.EMPTY, ToolStatus.PARTIAL, ToolStatus.AWAITING_REVIEW)
            kind = AuditKind.WRITE_SUCCEEDED if ok else AuditKind.WRITE_FAILED
            try:
                prep.audit.emit(kind, self.clock(), result.error_code, "", plan=prep.plan, approval=prep.approval)
            except Exception as exc:  # noqa: BLE001 - effect already happened; surface, do not hide
                logger.error("audit sink failed after write %s", prep.entry.name, exc_info=exc)
                warnings = (*result.warnings, "audit of write outcome failed")
                result = result.model_copy(update={"warnings": warnings})
        if ledger is not None:
            ledger.calls_used += 1
            ledger.cost_used += prep.cost
            ledger.seen[prep.key] = result
        return ToolOutcome(result, prep.key, cost=prep.cost, charged=True, attempts=attempts)

    def _bound(self, ctx: RequestContext, result: ToolResult) -> ToolResult:
        if result.data is None:
            return result
        body = json.dumps(result.data, sort_keys=True, default=str)
        limit = self.limits.max_output_chars
        if len(body) <= limit:
            return result
        refs = result.artifact_refs
        warning = f"output of {len(body):,} characters exceeded the {limit:,}-character bound; preview only"
        if self.artifact_store is not None:
            ref = self.artifact_store.put(ctx, body.encode(), media_type="application/json",
                                          metadata={"tool": result.tool, "kind": "tool_output"})
            refs = (*refs, ref)
            warning += f"; full output stored as {ref}"
        data = {"truncated": True, "original_chars": len(body), "preview": body[: max(0, limit // 2)]}
        status = ToolStatus.PARTIAL if result.status is ToolStatus.OK else result.status
        return result.model_copy(update={"data": data, "status": status, "artifact_refs": refs,
                                         "warnings": (*result.warnings, warning)})


# -- helpers -------------------------------------------------------------------------


class _DeadlineExceeded(Exception):
    pass


_T = TypeVar("_T")


async def _await_with_deadline(awaitable: Awaitable[_T], deadline: datetime | None,
                               clock: Callable[[], datetime]) -> _T:
    if deadline is None:
        return await awaitable
    remaining = (deadline - clock()).total_seconds()
    if remaining <= 0:
        _close(awaitable)
        raise _DeadlineExceeded()
    try:
        return await asyncio.wait_for(awaitable, timeout=remaining)
    except TimeoutError:
        # wait_for's own timeout; an outer cancellation arrives as CancelledError instead.
        if clock() >= deadline:
            raise _DeadlineExceeded() from None
        raise


def _close(awaitable: object) -> None:
    close = getattr(awaitable, "close", None)
    if callable(close):
        close()


def _validate(schema: Mapping[str, Any], instance: object) -> str | None:
    from jsonschema import Draft202012Validator

    error = next(iter(Draft202012Validator(schema).iter_errors(instance)), None)
    if error is None:
        return None
    where = "/".join(str(p) for p in error.absolute_path) or "<root>"
    return f"{where}: {error.message}"[:300]


@dataclass
class _Audit:
    executor: ToolExecutor
    ctx: RequestContext
    spec: ToolSpec
    args: Any
    thread_id: str | None
    tool_call_id: str | None

    def emit(self, kind: AuditKind, at: datetime, code: ErrorCode | None, detail: str, *,
             plan: ChangePlan | None = None, approval: ApprovalRecord | None = None) -> None:
        args = dict(self.args) if isinstance(self.args, Mapping) else None
        self.executor.event_sink.emit(make_event(
            kind, self.ctx, at=at, spec=self.spec, args=args, plan=plan, approval=approval, error_code=code,
            detail=detail, thread_id=self.thread_id, tool_call_id=self.tool_call_id,
        ))

    def best_effort(
        self, kind: AuditKind, at: datetime, code: ErrorCode | None, detail: str, **kw: Any,  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
    ) -> None:
        try:
            self.emit(kind, at, code, detail, **kw)
        except Exception as exc:  # noqa: BLE001 - a denial stands even if it cannot be recorded
            logger.error("audit sink failed for %s on %s", kind.value, self.spec.name, exc_info=exc)


@dataclass
class _Prepared:
    executor: ToolExecutor
    ctx: RequestContext
    entry: RegisteredTool
    args: dict[str, Any]
    key: str
    cost: int
    plan: ChangePlan | None
    approval: ApprovalRecord | None
    tool_call_id: str | None
    audit: _Audit

    def invocation(self, attempt: int) -> ToolInvocation:
        return ToolInvocation(
            ctx=self.ctx, spec=self.entry.spec, args=dict(self.args), admission=self.executor.admission,
            artifact_store=self.executor.artifact_store, deadline=self.ctx.deadline, attempt=attempt,
            tool_call_id=self.tool_call_id, plan=self.plan, approval=self.approval,
        )


__all__: tuple[str, ...] = (
    "COST_UNITS", "RuntimeLimits", "ToolExecutor", "ToolInvocation", "ToolOutcome", "TurnLedger", "call_key",
    "classify_exception", "is_control_flow",
)

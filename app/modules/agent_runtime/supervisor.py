"""Literature supervisor graph factory (task T02, R-04).

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/graph.py
(``_supervisor_node``, ``_should_continue``, ``_execute_tools_node``,
``build_supervisor_graph``) (BSD-3-Clause).
Copyright (c) 2026, The Regents of the University of California, through Lawrence
Berkeley National Laboratory. See the FORGE LICENSE for the full notice.

Kept from the donor: a supervisor node that calls a tool-bound chat model on the
conversation, a conditional edge that ends when the reply has no tool calls, a
tool-execution node that loops back, repeated-call refusal, and in-node repair
and trimming of history committed by the same checkpoint write as the reply.

Not ported: the donor's simulation/science system prompts, discovery-budget
catalog heuristics, sub-agent fallback routing, Langfuse tracer and global
registries. The chat model is injected (for example the existing
``Orchestrator._build_llm`` ChatOpenAI, or a fake); this module never constructs
a provider. Model calls run inside the injected admission ``slot``/``aslot``.

Added: per-turn capability-based tool binding, the authorizing executor, a
durable approval step built on ``langgraph.types.interrupt`` /
``langgraph.types.Command(resume=...)`` (verified against the installed
langgraph 1.2.10), thread ownership checks and per-turn model-call/deadline
stops. Approval requests are raised in a node that completes (and is
checkpointed) before the interrupting node, because LangGraph re-runs an
interrupted node from its start on resume; the non-idempotent tool effect runs
only in the later ``execute_tools`` node, after the approval check.

Usage::

    graph = build_literature_supervisor(registry=reg, chat_model=llm, checkpointer=saver,
                                        event_sink=JsonlEventSink(path), limits=RuntimeLimits())
    cfg = thread_config(ctx, "t1")
    out = graph.invoke({"messages": [HumanMessage("...")]}, cfg, context=RunContext(ctx))
    if out.get("__interrupt__"):
        out = graph.invoke(Command(resume={tool_call_id: approval_record}), cfg, context=RunContext(ctx))

Pending approvals survive new chat messages (T02B, owner answer H2 Q8 option b). An
approval request is pinned in ``open_approvals`` (tool, arguments, exact plan, expiry from
``RuntimeLimits.approval_request_ttl_s``) and the call gets a placeholder ``ToolMessage`` so
the conversation stays well formed. A new message on the thread discards LangGraph's
unfinished interrupt task (``pregel/_loop.py`` ``_first``: "discard any unfinished tasks from
previous checkpoint"); the new turn is answered and, while requests remain open, the graph
ends in ``await_approval`` again, so ``Command(resume=...)`` keeps working. On resume the
decision is checked against the pinned plan; ``apply_approvals`` re-resolves the plan (a
changed plan or an expired request is refused), runs the write once, replaces the
placeholder in place and closes the request in the same checkpoint write, so a repeated
resume or a restart over the same saver cannot apply it again.

Resume values: ``{tool_call_id: ApprovalRecord | its JSON dict}``, or a single record when one
call is pending. Mapped keys decide only those requests; others stay open. A ``None`` or
non-record value declines; a non-mapping value (for example ``"reject"``) declines every open
request. Two langgraph 1.2.10 pitfalls: ``Command(resume=None)`` fails
(``UnboundLocalError`` in ``pregel/_loop.py``), and ``Command(resume={})`` is read as an empty
interrupt-id map, so the interrupt simply stays pending.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
import json
import logging
from typing import TYPE_CHECKING, Any, cast

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import get_runtime
from langgraph.types import interrupt

from ..contracts import ApprovalRecord, ChangePlan, ContractError, ErrorCode, RequestContext, ToolStatus
from ..contracts.ports import DocumentStore
from ..json_types import Untyped
from .admission import Admission, ensure_admission
from .chat_model import ChatModel
from .checkpointer import owner_key
from .executor import RuntimeLimits, ToolExecutor, ToolOutcome, TurnLedger, _fail, call_key
from .message_trimming import current_turn_start, is_orphan_repair, prepare_for_model
from .policies import (
    ApprovalCheck,
    AuditKind,
    EventSink,
    NullEventSink,
    PlanApprovalCheck,
    args_digest,
    make_event,
    needs_approval,
    utcnow,
)
from .registry import RegisteredTool, ToolRegistry, effective_capabilities
from .skills import SkillRegistry
from .state import LiteratureState, RunContext, as_run_context

if TYPE_CHECKING:
    from ..contracts.tools import ToolSpec

logger = logging.getLogger(__name__)

#: Default system prompt of :func:`build_literature_supervisor` when a caller injects none.
#: Not on the ``F2W_RUNTIME=langgraph`` path: ``application.F2WApplication`` (T03) always passes
#: ``runtime_bridge.bootstrap.QA_DELEGATION_PROMPT``, which only routes a question to the
#: ``answer_from_knowledge`` tool and relays its answer; the preserved F2W QA policy (JUDGE_SYSTEM,
#: numeric-claim and leeway rules, citation syntax, abstention) runs inside that tool, unchanged
#: (T03, T03B). This text is for direct ``build_literature_supervisor`` callers and tests. Keep it
#: free of FORGE supervisor/science/simulation prompt content.
PLACEHOLDER_SYSTEM_PROMPT = (
    "You are a literature assistant for FAIR2WISE. Use only the tools you are given. Base answers on "
    "the evidence the tools return and cite it. If the tools return nothing relevant, say so plainly."
)

RUNTIME_STOP_KEY = "f2w_runtime_stop"
APPROVAL_OUTCOME_KEY = "f2w_approval_outcome"
APPROVAL_PLACEHOLDER_PREFIX = "f2w_approval_pending:"
EXPIRED_KEY = "f2w_expired"

#: ``PlanResolver(ctx, tool_name, args) -> ChangePlan | None``: looks up the plan a write would apply.
PlanResolver = Callable[[RequestContext, str, Mapping[str, Any]], ChangePlan | None]

_FAILING = {ToolStatus.DENIED, ToolStatus.INVALID, ToolStatus.UNAVAILABLE, ToolStatus.ERROR}


def tool_definition(spec: ToolSpec) -> dict[str, Any]:
    """OpenAI-style function definition for ``bind_tools`` from a ``ToolSpec``."""
    description = f"{spec.description}\nWhen to use: {spec.when_to_use}\nWhen not to use: {spec.when_not_to_use}"
    return {"type": "function",
            "function": {"name": spec.name, "description": description, "parameters": spec.input_schema}}


def ledger_from_messages(messages: list[BaseMessage]) -> TurnLedger:
    """Rebuild the current turn's ledger from tool-message artifacts (donor ``_already_called`` idea)."""
    from ..contracts import ToolResult

    ledger = TurnLedger()
    for message in messages[current_turn_start(messages):]:
        art = getattr(message, "artifact", None)
        if getattr(message, "type", None) != "tool" or not isinstance(art, dict) or "call_key" not in art:
            continue
        if art.get("charged"):
            ledger.calls_used += 1
            ledger.cost_used += int(art.get("cost", 0))
        if not art.get("repeated") and art.get("result"):
            ledger.seen[art["call_key"]] = ToolResult.model_validate(art["result"])
    return ledger


def tool_message(call: Mapping[str, Any], outcome: ToolOutcome) -> ToolMessage:
    """Render an outcome as a bounded JSON ``ToolMessage``; the full result rides in ``artifact``."""
    r = outcome.result
    content = {
        "tool": r.tool, "status": r.status.value, "data": r.data, "warnings": list(r.warnings),
        "error_code": r.error_code.value if r.error_code else None, "retryable": r.retryable,
        "evidence_ids": r.evidence_ids[:50], "artifact_refs": list(r.artifact_refs)[:50],
    }
    if outcome.repeated:
        content["repeated_call"] = True
    artifact = {"call_key": outcome.key, "cost": outcome.cost, "charged": outcome.charged,
                "repeated": outcome.repeated, "result": r.model_dump(mode="json")}
    return ToolMessage(tool_call_id=call.get("id") or "", name=call.get("name"),
                       content=json.dumps(content, sort_keys=True, default=str), artifact=artifact,
                       status="error" if r.status in _FAILING else "success")


def _stop(code: ErrorCode, text: str) -> AIMessage:
    return AIMessage(content=f"[runtime] Stopped: {text}", response_metadata={RUNTIME_STOP_KEY: code.value})


def _refused(record: Mapping[str, Any], code: ErrorCode, message: str) -> ToolOutcome:
    return ToolOutcome(result=_fail(record["tool"], code, message), key=call_key(record["tool"], record["args"]))


def _parse_approval(value: object) -> ApprovalRecord | None:
    if isinstance(value, ApprovalRecord):
        return value
    if isinstance(value, Mapping):
        try:
            return ApprovalRecord.model_validate(dict(value))
        except ValueError as exc:
            logger.warning("approval record failed validation: %s", type(exc).__name__)
            return None
    return None


def build_literature_supervisor(
    *,
    registry: ToolRegistry,
    chat_model: ChatModel,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    event_sink: EventSink | None = None,
    limits: RuntimeLimits | None = None,
    system_prompt: str = PLACEHOLDER_SYSTEM_PROMPT,
    skills: SkillRegistry | None = None,
    admission: Admission | None = None,
    provider: str = "default",
    approval_check: ApprovalCheck | None = None,
    plan_resolver: PlanResolver | None = None,
    artifact_store: DocumentStore | None = None,
    clock: Callable[[], datetime] = utcnow,
) -> Untyped:
    """Build the compiled literature supervisor graph.

    Args:
        registry: Frozen tool registry for this application instance.
        chat_model: Injected LangChain ``BaseChatModel``-compatible model with
            ``bind_tools``/``invoke``/``ainvoke``. Never constructed here.
        checkpointer: LangGraph checkpoint saver (required for approvals and resume).
        event_sink: Durable audit sink; without one, write tools are refused.
        limits: :class:`RuntimeLimits`.
        system_prompt: Injected system prompt (default is a T03 placeholder).
        skills: Optional skill registry over ``registry``; its visible skills are
            appended to the system prompt per turn.
        admission: Object with ``slot``/``aslot``; model calls hold ``provider``'s slot.
        provider: Provider/account key passed to admission (for example ``"cborg"``).
        approval_check: Approval policy for graph-write/destructive tools.
        plan_resolver: Looks up the ``ChangePlan`` a write call would apply.
        artifact_store: Optional ``DocumentStore`` for oversized tool outputs.
        clock: Aware-UTC clock.

    Returns:
        A compiled ``StateGraph``. Invoke with ``context=RunContext(ctx)``.

    Raises:
        ValueError: If the registry is not frozen or ``skills`` is bound to another registry.
    """
    if not registry.frozen:
        raise ValueError("freeze the tool registry before building a supervisor")
    if skills is not None and skills.tool_registry is not registry:
        raise ValueError("skills must be built over the same tool registry")
    limits = limits or RuntimeLimits()
    sink = event_sink if event_sink is not None else NullEventSink()
    admission = ensure_admission(admission)
    check = approval_check or PlanApprovalCheck()
    executor = ToolExecutor(registry, approval_check=check, event_sink=sink, admission=admission, limits=limits,
                            artifact_store=artifact_store, clock=clock)

    def run_ctx() -> RunContext:
        return as_run_context(get_runtime().context)

    def thread_id() -> str | None:
        return (get_config().get("configurable") or {}).get("thread_id")

    def owner_update(state: LiteratureState, rc: RunContext) -> dict[str, Any]:
        key = owner_key(rc.request)
        current = state.get("owner")
        if current and current != key:
            raise ContractError(ErrorCode.DENIED, "this thread belongs to another principal or session")
        return {} if current else {"owner": key}

    def resolve_plan(ctx: RequestContext, name: str, args: object) -> ChangePlan | None:
        if plan_resolver is None or not isinstance(args, Mapping):
            return None
        return plan_resolver(ctx, name, args)

    # -- supervisor ---------------------------------------------------------------

    def prepare_call(state: LiteratureState) -> tuple[dict[str, Any], Any, list[BaseMessage], AIMessage | None]:
        rc = run_ctx()
        update = owner_update(state, rc)
        update.update(approvals={})  # open_approvals deliberately survive the new turn (T02B)
        for_model, repairs = prepare_for_model(state["messages"], budget_chars=limits.stale_tool_result_chars)
        turn = for_model[current_turn_start(for_model):]
        made = sum(1 for m in turn if isinstance(m, AIMessage) and not is_orphan_repair(m))
        stop = None
        deadline = rc.request.deadline
        if deadline is not None and clock() >= deadline:
            stop = _stop(ErrorCode.BUDGET_EXHAUSTED, "the request deadline has passed.")
        elif made >= limits.max_model_calls:
            stop = _stop(ErrorCode.BUDGET_EXHAUSTED, "the model-call budget for this turn is used up.")
        prompt = system_prompt
        if skills is not None:
            section = skills.render_prompt_section(rc.request, scope=rc.scope, tools=rc.tools)
            if section:
                prompt = f"{prompt}\n\n{section}"
        messages = list(for_model)
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=prompt), *messages]
        tools = [tool_definition(e.spec) for e in registry.visible_to(rc.request, scope=rc.scope)
                 if rc.tools is None or e.name in rc.tools]
        bound = chat_model.bind_tools(tools) if tools else chat_model
        update["messages"] = list(repairs)
        return update, bound, messages, stop

    def supervisor(state: LiteratureState) -> dict[str, Any]:
        update, bound, messages, stop = prepare_call(state)
        if stop is None:
            with admission.slot(provider):
                reply = bound.invoke(messages)
        else:
            reply = stop
        update["messages"].append(reply)
        return update

    async def asupervisor(state: LiteratureState) -> dict[str, Any]:
        update, bound, messages, stop = prepare_call(state)
        if stop is None:
            async with admission.aslot(provider):
                reply = await bound.ainvoke(messages)
        else:
            reply = stop
        update["messages"].append(reply)
        return update

    def route_after_supervisor(state: LiteratureState) -> str:
        calls = getattr(state["messages"][-1], "tool_calls", None) or []
        if not calls:
            # T02B: the turn is answered; re-raise any approval still open on this thread.
            return "await_approval" if state.get("open_approvals") else END
        for call in calls:
            entry = registry.get(call.get("name", ""))
            if entry is not None and needs_approval(entry.spec):
                return "request_approval"
        return "execute_tools"

    # -- approval (T02B: open approvals survive new turns) ---------------------------

    def placeholder_id(tool_call_id: str) -> str:
        return f"{APPROVAL_PLACEHOLDER_PREFIX}{tool_call_id}"

    def request_approval(state: LiteratureState) -> dict[str, Any]:
        rc = run_ctx()
        owner_update(state, rc)
        ctx, caps, tid = rc.request, effective_capabilities(rc.request, rc.scope), thread_id()
        opened: dict[str, dict[str, Any]] = {}
        placeholders: list[BaseMessage] = []
        now = clock()
        ttl = limits.approval_request_ttl_s
        for call in getattr(state["messages"][-1], "tool_calls", None) or []:
            entry = registry.get(call.get("name", ""))
            if entry is None or not needs_approval(entry.spec) or not entry.spec.required_capabilities <= caps:
                continue  # the executor denies these before any effect
            plan = resolve_plan(ctx, entry.name, call.get("args"))
            if plan is None:
                continue  # no plan to approve; the executor denies the write
            tcid = call.get("id") or ""
            expires = now + timedelta(seconds=ttl) if ttl is not None else None
            opened[tcid] = {"tool_call_id": tcid, "tool": entry.name, "args": dict(call.get("args") or {}),
                            "args_sha256": args_digest(call.get("args")), "plan": plan.model_dump(mode="json"),
                            "requested_at": now.isoformat(),
                            "expires_at": expires.isoformat() if expires is not None else None}
            # A placeholder result keeps the conversation well formed while the request stays open, so later
            # turns can call the model; it is replaced in place (same id) by the real outcome.
            placeholders.append(ToolMessage(
                id=placeholder_id(tcid), tool_call_id=tcid, name=entry.name, status="success",
                content=json.dumps({"tool": entry.name, "status": "awaiting_approval", "plan_id": plan.plan_id,
                                    "plan_sha256": plan.plan_sha256,
                                    "note": "Not applied. The user has been asked to approve or decline."}),
                artifact={APPROVAL_PLACEHOLDER_PREFIX: True}))
            try:
                sink.emit(make_event(AuditKind.APPROVAL_REQUESTED, ctx, at=now, spec=entry.spec,
                                     args=call.get("args"), plan=plan, thread_id=tid, tool_call_id=tcid,
                                     event_id=f"approval_requested:{tid}:{tcid}"))
            except Exception:  # noqa: BLE001 - the write itself is refused later if audit stays down
                logger.warning("approval_requested audit event for %s not recorded", entry.name, exc_info=True)
        return {"open_approvals": opened, "messages": placeholders}

    def unanswered_calls(state: LiteratureState) -> list[Mapping[str, Any]]:
        answered = {getattr(m, "tool_call_id", None) for m in state["messages"]
                    if getattr(m, "type", None) == "tool"}
        calls = getattr(state["messages"][-1], "tool_calls", None) or []
        if getattr(state["messages"][-1], "type", None) != "ai":
            last_ai = next((m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None)
            calls = getattr(last_ai, "tool_calls", None) or []
        return [c for c in calls if c.get("id") not in answered]

    def route_after_request(state: LiteratureState) -> str:
        return "execute_tools" if unanswered_calls(state) else "await_approval"

    def is_expired(record: Mapping[str, Any], now: datetime) -> bool:
        expires = record.get("expires_at")
        return expires is not None and now >= datetime.fromisoformat(expires)

    def await_approval(state: LiteratureState) -> dict[str, Any]:
        # LangGraph re-runs this node from its start on resume (langgraph.types.interrupt), so nothing
        # before interrupt() has effects; expired requests are closed here (no interrupt) or after it.
        open_ = state.get("open_approvals") or {}
        now = clock()
        live = {k: v for k, v in open_.items() if not is_expired(v, now)}
        expired = [k for k in open_ if k not in live]
        decided: dict[str, dict[str, Any] | None] = {}
        if live:
            shown = ("plan_id", "plan_sha256", "target", "corpus_scope", "base_revision", "creates", "links",
                     "supersessions")
            requests = [{"tool_call_id": p["tool_call_id"], "tool": p["tool"], "args_sha256": p["args_sha256"],
                         "expires_at": p.get("expires_at"), **{k: p["plan"].get(k) for k in shown}}
                        for p in live.values()]
            decision = interrupt({"kind": "approval_required", "requests": requests})
            rc = run_ctx()
            owner_update(state, rc)  # another principal cannot decide; the request stays open
            ctx, tid = rc.request, thread_id()
            now = clock()
            if isinstance(decision, (ApprovalRecord, Mapping)) and len(live) == 1 and not (
                    isinstance(decision, Mapping) and next(iter(live)) in decision):
                decision = {next(iter(live)): decision}
            if isinstance(decision, Mapping):
                targets = [k for k in live if k in decision]
            else:
                targets = list(live)  # a non-record value (for example "reject") declines every request
            supplied = decision if isinstance(decision, Mapping) else {}
            for key in targets:
                record = live[key]
                if is_expired(record, now):
                    expired.append(key)
                    continue
                entry = cast(RegisteredTool, registry.get(record["tool"]))
                plan = ChangePlan.model_validate(record["plan"])
                approval = _parse_approval(supplied.get(key))
                verdict = check(ctx, entry.spec, record["args"], plan=plan, approval=approval, now=now)
                kind = AuditKind.APPROVAL_GRANTED if verdict.allowed else AuditKind.APPROVAL_REJECTED
                try:
                    sink.emit(make_event(kind, ctx, at=now, spec=entry.spec, plan=plan, approval=approval,
                                         error_code=verdict.error_code, detail=verdict.reason, thread_id=tid,
                                         tool_call_id=key))
                except Exception:  # noqa: BLE001 - the executor re-checks and audits the write itself
                    logger.warning("%s audit event for %s not recorded", kind, entry.name, exc_info=True)
                decided[key] = approval.model_dump(mode="json") if approval is not None else None
        for key in expired:
            decided[key] = {EXPIRED_KEY: True}
        return {"approvals": decided}

    def route_after_await(state: LiteratureState) -> str:
        if state.get("approvals"):
            return "apply_approvals"
        return "await_approval" if state.get("open_approvals") else END

    def approval_outcomes(
        state: LiteratureState,
    ) -> tuple[
        RunContext,
        list[tuple[dict[str, Any], ChangePlan | None, ApprovalRecord | None, ToolOutcome | None]],
        str | None,
    ]:
        rc = run_ctx()
        owner_update(state, rc)
        open_ = state.get("open_approvals") or {}
        jobs: list[tuple[dict[str, Any], ChangePlan | None, ApprovalRecord | None, ToolOutcome | None]] = []
        for key, raw in (state.get("approvals") or {}).items():
            record = open_.get(key)
            if record is None:
                continue  # already applied or closed: never applied twice
            entry = registry.get(record["tool"])
            pinned = ChangePlan.model_validate(record["plan"])
            call = {"id": key, "name": record["tool"], "args": record["args"]}
            if isinstance(raw, Mapping) and raw.get(EXPIRED_KEY):
                jobs.append((call, None, None, _refused(record, ErrorCode.STALE_APPROVAL,
                                                        "the approval request expired before a decision")))
                continue
            approval = _parse_approval(raw)
            if approval is None:
                jobs.append((call, None, None, _refused(record, ErrorCode.DENIED,
                                                        "the user declined this write")))
                continue
            current = resolve_plan(rc.request, record["tool"], record["args"])
            if entry is None or current is None or current.plan_sha256 != pinned.plan_sha256 \
                    or current.plan_id != pinned.plan_id:
                jobs.append((call, None, None, _refused(record, ErrorCode.STALE_APPROVAL,
                                                        "the plan changed after approval was requested")))
                continue
            jobs.append((call, current, approval, None))
        return rc, jobs, thread_id()

    def finish_apply(state: LiteratureState,
                     results: Sequence[tuple[Mapping[str, Any], ToolMessage]]) -> dict[str, Any]:
        msgs: list[BaseMessage] = []
        tail = isinstance(state["messages"][-1], ToolMessage)
        notes = []
        for call, message in results:
            message.id = placeholder_id(call["id"])  # replace the placeholder in place
            msgs.append(message)
            body = json.loads(cast(str, message.content))
            notes.append(f"{call['name']} (plan {call['args'].get('plan_id', '?')}): {body.get('status')}"
                         + (f" ({body.get('error_code')})" if body.get("error_code") else ""))
        if notes and not tail:
            # The request was made in an earlier turn: report the outcome without another model call.
            msgs.append(AIMessage(content="[runtime] Approval outcome: " + "; ".join(notes) + ".",
                                  response_metadata={APPROVAL_OUTCOME_KEY: True}))
        return {"messages": msgs, "approvals": {},
                "open_approvals": {call["id"]: None for call, _ in results}}

    def apply_approvals(state: LiteratureState) -> dict[str, Any]:
        rc, jobs, tid = approval_outcomes(state)
        results = []
        for call, plan, approval, refused in jobs:
            outcome = refused or executor.execute_call(
                rc.request, call["name"], call["args"], ledger=TurnLedger(), plan=plan, approval=approval,
                scope=rc.scope, tool_call_id=call["id"], thread_id=tid)
            results.append((call, tool_message(call, outcome)))
        return finish_apply(state, results)

    async def aapply_approvals(state: LiteratureState) -> dict[str, Any]:
        rc, jobs, tid = approval_outcomes(state)
        results = []
        for call, plan, approval, refused in jobs:
            outcome = refused or await executor.aexecute_call(
                rc.request, call["name"], call["args"], ledger=TurnLedger(), plan=plan, approval=approval,
                scope=rc.scope, tool_call_id=call["id"], thread_id=tid)
            results.append((call, tool_message(call, outcome)))
        return finish_apply(state, results)

    def route_after_apply(state: LiteratureState) -> str:
        last = state["messages"][-1]
        if isinstance(last, ToolMessage):
            return "supervisor"  # the request belonged to this turn: let the model report the result
        return "await_approval" if state.get("open_approvals") else END

    # -- tool execution -------------------------------------------------------------

    def tool_calls_with_context(
        state: LiteratureState,
    ) -> tuple[RunContext, TurnLedger, list[tuple[Mapping[str, Any], ChangePlan | None, None]], str | None]:
        rc = run_ctx()
        owner_update(state, rc)
        ledger = ledger_from_messages(state["messages"])
        prepared: list[tuple[Mapping[str, Any], ChangePlan | None, None]] = []
        for call in unanswered_calls(state):
            entry = registry.get(call.get("name", ""))
            plan = None
            if entry is not None and needs_approval(entry.spec):
                plan = resolve_plan(rc.request, entry.name, call.get("args"))
            prepared.append((call, plan, None))  # approved writes run in apply_approvals, never here
        return rc, ledger, prepared, thread_id()

    def execute_tools(state: LiteratureState) -> dict[str, Any]:
        rc, ledger, prepared, tid = tool_calls_with_context(state)
        out = []
        for call, plan, approval in prepared:
            outcome = executor.execute_call(rc.request, call.get("name", ""), call.get("args", {}), ledger=ledger,
                                            plan=plan, approval=approval, scope=rc.scope,
                                            tool_call_id=call.get("id"), thread_id=tid)
            out.append(tool_message(call, outcome))
        return {"messages": out}

    async def aexecute_tools(state: LiteratureState) -> dict[str, Any]:
        rc, ledger, prepared, tid = tool_calls_with_context(state)
        out = []
        for call, plan, approval in prepared:
            outcome = await executor.aexecute_call(rc.request, call.get("name", ""), call.get("args", {}),
                                                   ledger=ledger, plan=plan, approval=approval, scope=rc.scope,
                                                   tool_call_id=call.get("id"), thread_id=tid)
            out.append(tool_message(call, outcome))
        return {"messages": out}

    graph = StateGraph(LiteratureState, context_schema=RunContext)
    graph.add_node("supervisor", RunnableLambda(supervisor, afunc=asupervisor, name="supervisor"))
    graph.add_node("request_approval", request_approval)
    graph.add_node("await_approval", await_approval)
    graph.add_node("apply_approvals", RunnableLambda(apply_approvals, afunc=aapply_approvals,
                                                     name="apply_approvals"))
    graph.add_node("execute_tools", RunnableLambda(execute_tools, afunc=aexecute_tools, name="execute_tools"))
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor", route_after_supervisor,
        {"execute_tools": "execute_tools", "request_approval": "request_approval",
         "await_approval": "await_approval", END: END})
    graph.add_conditional_edges("request_approval", route_after_request,
                                {"await_approval": "await_approval", "execute_tools": "execute_tools"})
    graph.add_conditional_edges("await_approval", route_after_await,
                                {"apply_approvals": "apply_approvals", "await_approval": "await_approval",
                                 END: END})
    graph.add_conditional_edges("apply_approvals", route_after_apply,
                                {"supervisor": "supervisor", "await_approval": "await_approval", END: END})
    graph.add_edge("execute_tools", "supervisor")
    return graph.compile(checkpointer=checkpointer)

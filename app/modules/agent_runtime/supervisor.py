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

Resume values: ``{tool_call_id: ApprovalRecord | its JSON dict}``, or a single record when one
call is pending. Anything else (for example ``"reject"`` or ``{}``) rejects every pending write.
Do not resume with ``None``: langgraph 1.2.10 treats ``Command(resume=None)`` as no resume and
fails (``UnboundLocalError`` in ``pregel/_loop.py``).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import get_runtime
from langgraph.types import interrupt

from app.modules.contracts import ApprovalRecord, ChangePlan, ContractError, ErrorCode, RequestContext, ToolStatus

from .admission import ensure_admission
from .checkpointer import owner_key
from .executor import RuntimeLimits, ToolExecutor, ToolOutcome, TurnLedger
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
from .registry import ToolRegistry, effective_capabilities
from .skills import SkillRegistry
from .state import LiteratureState, RunContext, as_run_context

# TODO(T03): placeholder only. T03 supplies the preserved F2W QA policy (prompts, evidence gates,
# citations, fallback/abstention). Do not replace this with FORGE supervisor/science/simulation prompts.
PLACEHOLDER_SYSTEM_PROMPT = (
    "You are a literature assistant for FAIR2WISE. Use only the tools you are given. Base answers on "
    "the evidence the tools return and cite it. If the tools return nothing relevant, say so plainly."
)

RUNTIME_STOP_KEY = "f2w_runtime_stop"

#: ``PlanResolver(ctx, tool_name, args) -> ChangePlan | None``: looks up the plan a write would apply.
PlanResolver = Callable[[RequestContext, str, Mapping[str, Any]], Optional[ChangePlan]]

_FAILING = {ToolStatus.DENIED, ToolStatus.INVALID, ToolStatus.UNAVAILABLE, ToolStatus.ERROR}


def tool_definition(spec: Any) -> Dict[str, Any]:
    """OpenAI-style function definition for ``bind_tools`` from a ``ToolSpec``."""
    description = f"{spec.description}\nWhen to use: {spec.when_to_use}\nWhen not to use: {spec.when_not_to_use}"
    return {"type": "function",
            "function": {"name": spec.name, "description": description, "parameters": spec.input_schema}}


def ledger_from_messages(messages: List[BaseMessage]) -> TurnLedger:
    """Rebuild the current turn's ledger from tool-message artifacts (donor ``_already_called`` idea)."""
    from app.modules.contracts import ToolResult

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


def _parse_approval(value: Any) -> Optional[ApprovalRecord]:
    if isinstance(value, ApprovalRecord):
        return value
    if isinstance(value, Mapping):
        try:
            return ApprovalRecord.model_validate(dict(value))
        except ValueError:
            return None
    return None


def build_literature_supervisor(
    *,
    registry: ToolRegistry,
    chat_model: Any,
    checkpointer: Any = None,
    event_sink: Optional[EventSink] = None,
    limits: Optional[RuntimeLimits] = None,
    system_prompt: str = PLACEHOLDER_SYSTEM_PROMPT,
    skills: Optional[SkillRegistry] = None,
    admission: Any = None,
    provider: str = "default",
    approval_check: Optional[ApprovalCheck] = None,
    plan_resolver: Optional[PlanResolver] = None,
    artifact_store: Any = None,
    clock: Callable[[], datetime] = utcnow,
) -> Any:
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

    def thread_id() -> Optional[str]:
        return (get_config().get("configurable") or {}).get("thread_id")

    def owner_update(state: LiteratureState, rc: RunContext) -> Dict[str, Any]:
        key = owner_key(rc.request)
        current = state.get("owner")
        if current and current != key:
            raise ContractError(ErrorCode.DENIED, "this thread belongs to another principal or session")
        return {} if current else {"owner": key}

    def resolve_plan(ctx: RequestContext, name: str, args: Any) -> Optional[ChangePlan]:
        if plan_resolver is None or not isinstance(args, Mapping):
            return None
        return plan_resolver(ctx, name, args)

    # -- supervisor ---------------------------------------------------------------

    def prepare_call(state: LiteratureState) -> Tuple[Dict[str, Any], Any, List[BaseMessage], Optional[AIMessage]]:
        rc = run_ctx()
        update = owner_update(state, rc)
        update.update(pending_approvals=[], approvals={})
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
            section = skills.render_prompt_section(rc.request, scope=rc.scope)
            if section:
                prompt = f"{prompt}\n\n{section}"
        messages = list(for_model)
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=prompt), *messages]
        tools = [tool_definition(e.spec) for e in registry.visible_to(rc.request, scope=rc.scope)]
        bound = chat_model.bind_tools(tools) if tools else chat_model
        update["messages"] = list(repairs)
        return update, bound, messages, stop

    def supervisor(state: LiteratureState) -> Dict[str, Any]:
        update, bound, messages, stop = prepare_call(state)
        if stop is None:
            with admission.slot(provider):
                reply = bound.invoke(messages)
        else:
            reply = stop
        update["messages"].append(reply)
        return update

    async def asupervisor(state: LiteratureState) -> Dict[str, Any]:
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
            return END
        for call in calls:
            entry = registry.get(call.get("name", ""))
            if entry is not None and needs_approval(entry.spec):
                return "request_approval"
        return "execute_tools"

    # -- approval -----------------------------------------------------------------

    def request_approval(state: LiteratureState) -> Dict[str, Any]:
        rc = run_ctx()
        owner_update(state, rc)
        ctx, caps, tid = rc.request, effective_capabilities(rc.request, rc.scope), thread_id()
        pending = []
        for call in getattr(state["messages"][-1], "tool_calls", None) or []:
            entry = registry.get(call.get("name", ""))
            if entry is None or not needs_approval(entry.spec) or not entry.spec.required_capabilities <= caps:
                continue  # the executor denies these before any effect
            plan = resolve_plan(ctx, entry.name, call.get("args"))
            if plan is None:
                continue  # no plan to approve; the executor denies the write
            pending.append({"tool_call_id": call.get("id"), "tool": entry.name,
                            "args_sha256": args_digest(call.get("args")), "plan": plan.model_dump(mode="json")})
            try:
                sink.emit(make_event(AuditKind.APPROVAL_REQUESTED, ctx, at=clock(), spec=entry.spec,
                                     args=call.get("args"), plan=plan, thread_id=tid, tool_call_id=call.get("id"),
                                     event_id=f"approval_requested:{tid}:{call.get('id')}"))
            except Exception:  # noqa: BLE001 - the write itself is refused later if audit stays down
                pass
        return {"pending_approvals": pending}

    def route_after_request(state: LiteratureState) -> str:
        return "await_approval" if state.get("pending_approvals") else "execute_tools"

    def await_approval(state: LiteratureState) -> Dict[str, Any]:
        pending = state.get("pending_approvals") or []
        shown = ("plan_id", "plan_sha256", "target", "corpus_scope", "base_revision", "creates", "links",
                 "supersessions")
        requests = [{"tool_call_id": p["tool_call_id"], "tool": p["tool"], "args_sha256": p["args_sha256"],
                     **{k: p["plan"].get(k) for k in shown}} for p in pending]
        payload = {"kind": "approval_required", "requests": requests}
        decision = interrupt(payload)  # replays from here on resume; nothing above has effects
        rc = run_ctx()
        owner_update(state, rc)
        ctx, tid = rc.request, thread_id()
        if isinstance(decision, (ApprovalRecord, Mapping)) and len(pending) == 1 and not (
                isinstance(decision, Mapping) and pending[0]["tool_call_id"] in decision):
            decision = {pending[0]["tool_call_id"]: decision}
        supplied = decision if isinstance(decision, Mapping) else {}
        approvals: Dict[str, Dict[str, Any]] = {}
        for p in pending:
            entry = registry.get(p["tool"])
            plan = ChangePlan.model_validate(p["plan"])
            approval = _parse_approval(supplied.get(p["tool_call_id"]))
            verdict = check(ctx, entry.spec, {}, plan=plan, approval=approval, now=clock())
            kind = AuditKind.APPROVAL_GRANTED if verdict.allowed else AuditKind.APPROVAL_REJECTED
            try:
                sink.emit(make_event(kind, ctx, at=clock(), spec=entry.spec, plan=plan, approval=approval,
                                     error_code=verdict.error_code, detail=verdict.reason, thread_id=tid,
                                     tool_call_id=p["tool_call_id"]))
            except Exception:  # noqa: BLE001 - the executor re-checks and audits the write itself
                pass
            if approval is not None:
                approvals[p["tool_call_id"]] = approval.model_dump(mode="json")
        return {"approvals": approvals, "pending_approvals": []}

    # -- tool execution -------------------------------------------------------------

    def tool_calls_with_context(state: LiteratureState):
        rc = run_ctx()
        owner_update(state, rc)
        ledger = ledger_from_messages(state["messages"])
        approvals = state.get("approvals") or {}
        calls = getattr(state["messages"][-1], "tool_calls", None) or []
        prepared = []
        for call in calls:
            entry = registry.get(call.get("name", ""))
            plan = approval = None
            if entry is not None and needs_approval(entry.spec):
                plan = resolve_plan(rc.request, entry.name, call.get("args"))
                approval = _parse_approval(approvals.get(call.get("id")))
            prepared.append((call, plan, approval))
        return rc, ledger, prepared, thread_id()

    def execute_tools(state: LiteratureState) -> Dict[str, Any]:
        rc, ledger, prepared, tid = tool_calls_with_context(state)
        out = []
        for call, plan, approval in prepared:
            outcome = executor.execute_call(rc.request, call.get("name", ""), call.get("args", {}), ledger=ledger,
                                            plan=plan, approval=approval, scope=rc.scope,
                                            tool_call_id=call.get("id"), thread_id=tid)
            out.append(tool_message(call, outcome))
        return {"messages": out, "approvals": {}}

    async def aexecute_tools(state: LiteratureState) -> Dict[str, Any]:
        rc, ledger, prepared, tid = tool_calls_with_context(state)
        out = []
        for call, plan, approval in prepared:
            outcome = await executor.aexecute_call(rc.request, call.get("name", ""), call.get("args", {}),
                                                   ledger=ledger, plan=plan, approval=approval, scope=rc.scope,
                                                   tool_call_id=call.get("id"), thread_id=tid)
            out.append(tool_message(call, outcome))
        return {"messages": out, "approvals": {}}

    graph = StateGraph(LiteratureState, context_schema=RunContext)
    graph.add_node("supervisor", RunnableLambda(supervisor, afunc=asupervisor, name="supervisor"))
    graph.add_node("request_approval", request_approval)
    graph.add_node("await_approval", await_approval)
    graph.add_node("execute_tools", RunnableLambda(execute_tools, afunc=aexecute_tools, name="execute_tools"))
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor", route_after_supervisor,
        {"execute_tools": "execute_tools", "request_approval": "request_approval", END: END})
    graph.add_conditional_edges("request_approval", route_after_request,
                                {"await_approval": "await_approval", "execute_tools": "execute_tools"})
    graph.add_edge("await_approval", "execute_tools")
    graph.add_edge("execute_tools", "supervisor")
    return graph.compile(checkpointer=checkpointer)

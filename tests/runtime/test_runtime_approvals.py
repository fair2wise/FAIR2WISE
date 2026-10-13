"""T02B: pending approvals survive new chat messages (owner answer H2 Q8, option b).

Fake model, in-memory saver; no provider, no network.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.modules.agent_runtime.checkpointer import thread_config
from app.modules.agent_runtime.executor import RuntimeLimits
from app.modules.agent_runtime.policies import AuditKind, InMemoryEventSink
from app.modules.agent_runtime.registry import ToolRegistry
from app.modules.agent_runtime.state import RunContext
from app.modules.agent_runtime.supervisor import APPROVAL_OUTCOME_KEY, build_literature_supervisor
from app.modules.contracts import ContractError, ErrorCode
from tests.runtime.runtime_fakes import (
    NOW,
    FakeClock,
    Recorder,
    ScriptedChatModel,
    local_approval,
    local_ctx,
    make_plan,
    read_spec,
    tool_call,
    write_spec,
)


class Plans:
    def __init__(self):
        self.plans = {"p1": make_plan(), "p2": make_plan(plan_id="p2", plan_sha256="e" * 64)}

    def __call__(self, ctx, tool, args):
        return self.plans.get(args.get("plan_id"))


def build(script, *, saver=None, sink=None, plans=None, clock=None, limits=None):
    rec = Recorder()
    reg = ToolRegistry()
    reg.register(read_spec(), rec.ok)
    reg.register(write_spec(), rec.ok)
    reg.freeze()
    model = ScriptedChatModel(script=list(script))
    graph = build_literature_supervisor(
        registry=reg, chat_model=model, checkpointer=saver or InMemorySaver(),
        event_sink=sink if sink is not None else InMemoryEventSink(), plan_resolver=plans or Plans(),
        clock=clock or FakeClock(), limits=limits)
    return graph, model, rec


def ask(text):
    return {"messages": [HumanMessage(content=text)]}


def results_for(out, call_id):
    return [json.loads(m.content) for m in out["messages"]
            if isinstance(m, ToolMessage) and m.tool_call_id == call_id]


def pending_then_question(**kw):
    """Turn 1 asks to publish p1 (interrupt); turn 2 asks an unrelated question while it is pending."""
    graph, model, rec = build([tool_call("kg_apply_plan", {"plan_id": "p1"}, "w1"),
                               AIMessage(content="answer to the new question")], **kw)
    ctx = local_ctx()
    cfg = thread_config(ctx, "t1")
    first = graph.invoke(ask("publish p1"), cfg, context=RunContext(ctx))
    assert first["__interrupt__"]
    out = graph.invoke(ask("what is RSoXS?"), cfg, context=RunContext(ctx))
    return graph, model, rec, ctx, cfg, out


def test_new_question_is_answered_while_write_stays_pending():
    graph, model, rec, ctx, cfg, out = pending_then_question()
    assert out["messages"][-1].content == "answer to the new question"
    assert rec.calls == [] and len(model.calls) == 2
    assert [r["tool_call_id"] for r in out["__interrupt__"][0].value["requests"]] == ["w1"]
    assert results_for(out, "w1")[0]["status"] == "awaiting_approval"
    assert graph.get_state(cfg).next == ("await_approval",)


def test_later_approval_applies_original_plan_exactly_once():
    sink = InMemoryEventSink()
    graph, model, rec, ctx, cfg, _ = pending_then_question(sink=sink)
    out = graph.invoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(ctx))
    assert len(rec.calls) == 1 and rec.calls[0].args == {"plan_id": "p1"}
    assert rec.calls[0].plan.plan_sha256 == make_plan().plan_sha256 and rec.calls[0].approval.approval_id == "a1"
    assert [r["status"] for r in results_for(out, "w1")] == ["ok"]  # placeholder replaced in place
    assert out["messages"][-1].response_metadata.get(APPROVAL_OUTCOME_KEY)
    assert "__interrupt__" not in out and not out.get("open_approvals")
    assert len(model.calls) == 2  # the outcome is reported without another model call
    assert sink.kinds().count(AuditKind.WRITE_SUCCEEDED) == 1
    # a repeated approval is a no-op: nothing is pending any more
    graph.invoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(ctx))
    graph.invoke(ask("and now?"), cfg, context=RunContext(ctx))
    assert len(rec.calls) == 1


@pytest.mark.parametrize("decision", ["decline", {"w1": None}])
def test_decline_cancels_the_pending_write(decision):
    graph, _, rec, ctx, cfg, _ = pending_then_question()
    out = graph.invoke(Command(resume=decision), cfg, context=RunContext(ctx))
    assert results_for(out, "w1")[0]["error_code"] == "denied" and rec.calls == []
    assert "__interrupt__" not in out
    out = graph.invoke(ask("next"), cfg, context=RunContext(ctx))
    assert "__interrupt__" not in out and rec.calls == []


def test_changed_plan_is_refused_at_resume():
    plans = Plans()
    graph, _, rec, ctx, cfg, _ = pending_then_question(plans=plans)
    plans.plans["p1"] = make_plan(plan_sha256="d" * 64)
    # an approval of the new hash does not cover the request, which pinned the old one
    out = graph.invoke(Command(resume={"w1": local_approval(plan_sha256="d" * 64)}), cfg, context=RunContext(ctx))
    assert results_for(out, "w1")[0]["error_code"] == "stale_approval" and rec.calls == []


def test_approval_of_pinned_plan_refused_when_plan_changed():
    plans = Plans()
    graph, _, rec, ctx, cfg, _ = pending_then_question(plans=plans)
    plans.plans["p1"] = make_plan(plan_sha256="d" * 64)
    out = graph.invoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(ctx))
    assert results_for(out, "w1")[0]["error_code"] == "stale_approval" and rec.calls == []


def test_expired_request_is_refused_at_resume():
    clock = FakeClock()
    limits = RuntimeLimits(approval_request_ttl_s=60)
    graph, _, rec, ctx, cfg, _ = pending_then_question(clock=clock, limits=limits)
    clock.now += timedelta(minutes=2)
    out = graph.invoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(ctx))
    assert results_for(out, "w1")[0]["error_code"] == "stale_approval" and rec.calls == []
    assert "__interrupt__" not in out


def test_expired_request_is_closed_by_the_next_turn():
    clock = FakeClock()
    graph, model, rec = build([tool_call("kg_apply_plan", {"plan_id": "p1"}, "w1"), AIMessage(content="hi")],
                              clock=clock, limits=RuntimeLimits(approval_request_ttl_s=60))
    ctx = local_ctx()
    cfg = thread_config(ctx, "t1")
    graph.invoke(ask("publish"), cfg, context=RunContext(ctx))
    clock.now += timedelta(minutes=2)
    out = graph.invoke(ask("hello"), cfg, context=RunContext(ctx))
    assert "__interrupt__" not in out and results_for(out, "w1")[0]["error_code"] == "stale_approval"
    assert not graph.get_state(cfg).values.get("open_approvals") and rec.calls == []


def test_expired_approval_record_is_refused():
    graph, _, rec, ctx, cfg, _ = pending_then_question()
    out = graph.invoke(Command(resume={"w1": local_approval(expires_at=NOW - timedelta(minutes=1))}),
                       cfg, context=RunContext(ctx))
    assert results_for(out, "w1")[0]["error_code"] == "stale_approval" and rec.calls == []


def test_restart_then_resume_has_no_duplicate_effects():
    saver = InMemorySaver()
    sink = InMemoryEventSink()
    graph, _, first_rec, ctx, cfg, _ = pending_then_question(saver=saver, sink=sink)
    restarted, model, rec = build([AIMessage(content="unused")], saver=saver, sink=sink)
    out = restarted.invoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(ctx))
    assert first_rec.calls == [] and len(rec.calls) == 1
    assert [r["status"] for r in results_for(out, "w1")] == ["ok"]
    again, _, rec2 = build([AIMessage(content="later")], saver=saver, sink=sink)
    again.invoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(ctx))
    again.invoke(ask("anything else?"), cfg, context=RunContext(ctx))
    assert rec2.calls == [] and sink.kinds().count(AuditKind.WRITE_SUCCEEDED) == 1


def test_another_principal_cannot_approve():
    graph, _, rec = build([tool_call("kg_apply_plan", {"plan_id": "p1"}, "w1"), AIMessage(content="answer")])
    owner = local_ctx()
    other = local_ctx(session_id="someone-else")
    cfg = {"configurable": {"thread_id": "shared"}}
    graph.invoke(ask("publish"), cfg, context=RunContext(owner))
    graph.invoke(ask("question"), cfg, context=RunContext(owner))
    with pytest.raises(ContractError) as err:
        graph.invoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(other))
    assert err.value.code is ErrorCode.DENIED and rec.calls == []
    out = graph.invoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(owner))
    assert len(rec.calls) == 1 and results_for(out, "w1")[0]["status"] == "ok"


def test_two_open_requests_are_decided_independently():
    graph, _, rec = build([tool_call("kg_apply_plan", {"plan_id": "p1"}, "w1"),
                           tool_call("kg_apply_plan", {"plan_id": "p2"}, "w2"),
                           AIMessage(content="answer")])
    ctx = local_ctx()
    cfg = thread_config(ctx, "t1")
    graph.invoke(ask("publish p1"), cfg, context=RunContext(ctx))
    out = graph.invoke(ask("publish p2 too"), cfg, context=RunContext(ctx))
    assert {r["tool_call_id"] for r in out["__interrupt__"][0].value["requests"]} == {"w1", "w2"}
    out = graph.invoke(Command(resume={"w2": local_approval(plan_id="p2", plan_sha256="e" * 64)}), cfg,
                       context=RunContext(ctx))
    assert [c.args for c in rec.calls] == [{"plan_id": "p2"}]
    assert [r["tool_call_id"] for r in out["__interrupt__"][0].value["requests"]] == ["w1"]
    out = graph.invoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(ctx))
    assert [c.args for c in rec.calls] == [{"plan_id": "p2"}, {"plan_id": "p1"}] and "__interrupt__" not in out


def test_async_resume_after_new_question():
    graph, _, rec = build([tool_call("kg_apply_plan", {"plan_id": "p1"}, "w1"), AIMessage(content="answer")])
    ctx = local_ctx()
    cfg = thread_config(ctx, "t1")

    async def main():
        await graph.ainvoke(ask("publish"), cfg, context=RunContext(ctx))
        mid = await graph.ainvoke(ask("question"), cfg, context=RunContext(ctx))
        assert mid["messages"][-1].content == "answer" and mid["__interrupt__"]
        return await graph.ainvoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(ctx))

    out = asyncio.run(main())
    assert len(rec.calls) == 1 and results_for(out, "w1")[0]["status"] == "ok"


def test_read_calls_beside_a_pending_write_still_run():
    both = AIMessage(content="", tool_calls=[
        {"name": "lit_echo", "args": {"q": "x"}, "id": "r1", "type": "tool_call"},
        {"name": "kg_apply_plan", "args": {"plan_id": "p1"}, "id": "w1", "type": "tool_call"}])
    graph, model, rec = build([both, AIMessage(content="read done; awaiting your approval")])
    ctx = local_ctx()
    out = graph.invoke(ask("look and publish"), thread_config(ctx, "t1"), context=RunContext(ctx))
    assert [c.spec.name for c in rec.calls] == ["lit_echo"]
    assert out["messages"][-1].content == "read done; awaiting your approval" and out["__interrupt__"]

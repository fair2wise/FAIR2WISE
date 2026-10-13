"""T02: literature supervisor graph with a fake tool-calling model (no provider, no network)."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.modules.agent_runtime.checkpointer import thread_config
from app.modules.agent_runtime.executor import RuntimeLimits
from app.modules.agent_runtime.policies import AuditKind, InMemoryEventSink
from app.modules.agent_runtime.registry import ToolRegistry
from app.modules.agent_runtime.skills import Skill, SkillRegistry
from app.modules.agent_runtime.state import RunContext
from app.modules.agent_runtime.supervisor import (
    PLACEHOLDER_SYSTEM_PROMPT,
    RUNTIME_STOP_KEY,
    build_literature_supervisor,
)
from app.modules.contracts import ContractError, ErrorCode
from tests.runtime.runtime_fakes import (
    NOW,
    PLAN_SHA,
    CountingAdmission,
    FakeClock,
    Recorder,
    ScriptedChatModel,
    als_ctx,
    local_approval,
    local_ctx,
    make_plan,
    read_spec,
    tool_call,
    write_spec,
)


class Plans:
    """Mutable plan store standing in for a trusted plan service."""

    def __init__(self, **plans):
        self.plans = plans or {"p1": make_plan()}

    def __call__(self, ctx, tool, args):
        return self.plans.get(args.get("plan_id"))


def build(script, *, rec=None, saver=None, sink=None, admission=None, limits=None, plans=None, skills=None,
          registry=None):
    rec = rec or Recorder()
    reg = registry
    if reg is None:
        reg = ToolRegistry()
        reg.register(read_spec(), rec.ok)
        reg.register(write_spec(), rec.ok)
        reg.freeze()
    model = ScriptedChatModel(script=list(script))
    graph = build_literature_supervisor(
        registry=reg, chat_model=model, checkpointer=saver or InMemorySaver(),
        event_sink=sink if sink is not None else InMemoryEventSink(), limits=limits, admission=admission,
        provider="cborg", plan_resolver=plans or Plans(), clock=FakeClock(), skills=skills)
    return graph, model, rec


def ask(text="question"):
    return {"messages": [HumanMessage(content=text)]}


def tool_messages(out):
    return [json.loads(m.content) for m in out["messages"] if isinstance(m, ToolMessage)]


# -- basic loop, exposure, admission -----------------------------------------------------


def test_read_turn_runs_tool_then_answers_through_admission():
    admission = CountingAdmission()
    graph, model, rec = build([tool_call("lit_echo", {"q": "x"}, "c1"), AIMessage(content="the answer")],
                              admission=admission)
    ctx = local_ctx()
    out = graph.invoke(ask(), thread_config(ctx, "t1"), context=RunContext(ctx))
    assert out["messages"][-1].content == "the answer"
    assert tool_messages(out)[0]["status"] == "ok" and len(rec.calls) == 1
    assert len(model.calls) == 2 and admission.sync == {"cborg": 2} and admission.held == 0
    assert isinstance(model.calls[0][0], SystemMessage)
    assert model.calls[0][0].content == PLACEHOLDER_SYSTEM_PROMPT


def test_async_turn_uses_async_admission():
    admission = CountingAdmission()
    graph, model, rec = build([tool_call("lit_echo", {"q": "x"}, "c1"), AIMessage(content="done")],
                              admission=admission)
    ctx = local_ctx()
    out = asyncio.run(graph.ainvoke(ask(), thread_config(ctx, "t1"), context=RunContext(ctx)))
    assert out["messages"][-1].content == "done" and len(rec.calls) == 1
    assert admission.aio == {"cborg": 2} and admission.sync == {}


def test_chat_only_context_sees_no_write_tools_and_hallucinated_write_is_denied():
    sink = InMemoryEventSink()
    graph, model, rec = build([tool_call("kg_apply_plan", {"plan_id": "p1"}, "w1"), AIMessage(content="ok")],
                              sink=sink)
    ctx = als_ctx("carol", admin=False)
    out = graph.invoke(ask(), thread_config(ctx, "t1"), context=RunContext(ctx))
    assert model.bound[0] == ["lit_echo"]
    assert "__interrupt__" not in out
    assert tool_messages(out)[0]["status"] == "denied" and rec.calls == []
    assert sink.kinds() == [AuditKind.TOOL_DENIED]


def test_admin_context_is_offered_write_tools():
    graph, model, _ = build([AIMessage(content="hi")])
    ctx = als_ctx("dave", admin=True)
    graph.invoke(ask(), thread_config(ctx, "t1"), context=RunContext(ctx))
    assert model.bound[0] == ["kg_apply_plan", "lit_echo"]


def test_turn_scope_narrows_tools():
    graph, model, _ = build([AIMessage(content="hi")])
    ctx = local_ctx()
    graph.invoke(ask(), thread_config(ctx, "t1"), context=RunContext(ctx, scope=frozenset({"chat.ask"})))
    assert model.bound[0] == ["lit_echo"]


def test_skills_rendered_per_capability():
    reg = ToolRegistry()
    rec = Recorder()
    reg.register(read_spec(), rec.ok)
    reg.register(write_spec(), rec.ok)
    reg.freeze()
    skills = SkillRegistry(reg)
    skills.register(Skill("search_literature", "Find papers.", "Paper questions.", ("lit_echo",)))
    skills.register(Skill("publish_graph", "Apply plans.", "After approval.", ("kg_apply_plan",)))
    skills.freeze()
    graph, model, _ = build([AIMessage(content="a"), AIMessage(content="b")], registry=reg, skills=skills)
    chat, local = als_ctx("erin"), local_ctx()
    graph.invoke(ask(), thread_config(chat, "t"), context=RunContext(chat))
    graph.invoke(ask(), thread_config(local, "t"), context=RunContext(local))
    assert "search_literature" in model.calls[0][0].content and "publish_graph" not in model.calls[0][0].content
    assert "publish_graph" in model.calls[1][0].content


def test_factory_preconditions():
    reg = ToolRegistry()
    with pytest.raises(ValueError, match="freeze"):
        build_literature_supervisor(registry=reg, chat_model=ScriptedChatModel())
    reg.freeze()
    other = ToolRegistry().freeze()
    with pytest.raises(ValueError, match="same tool registry"):
        build_literature_supervisor(registry=reg, chat_model=ScriptedChatModel(), skills=SkillRegistry(other))
    with pytest.raises(TypeError):
        build_literature_supervisor(registry=reg, chat_model=ScriptedChatModel(), admission=object())


def test_missing_request_context_is_denied():
    graph, model, _ = build([AIMessage(content="x")])
    with pytest.raises(ContractError) as err:
        graph.invoke(ask(), {"configurable": {"thread_id": "t"}})
    assert err.value.code is ErrorCode.DENIED and model.calls == []


# -- durable approval -----------------------------------------------------------------------


def _write_turn(**kw):
    return build([tool_call("kg_apply_plan", {"plan_id": "p1"}, "w1"), AIMessage(content="published")], **kw)


def test_approval_interrupt_then_matching_approval_writes_once():
    sink = InMemoryEventSink()
    graph, _, rec = _write_turn(sink=sink)
    ctx = local_ctx()
    cfg = thread_config(ctx, "t1")
    out = graph.invoke(ask("publish p1"), cfg, context=RunContext(ctx))
    request = out["__interrupt__"][0].value["requests"][0]
    assert request["tool_call_id"] == "w1" and request["plan_sha256"] == PLAN_SHA and request["creates"] == 2
    assert rec.calls == [] and sink.kinds() == [AuditKind.APPROVAL_REQUESTED]
    assert graph.get_state(cfg).next == ("await_approval",)

    out = graph.invoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(ctx))
    assert out["messages"][-1].content == "published"
    assert tool_messages(out)[0]["status"] == "ok" and len(rec.calls) == 1
    assert sink.kinds() == [AuditKind.APPROVAL_REQUESTED, AuditKind.APPROVAL_GRANTED,
                            AuditKind.WRITE_ATTEMPTED, AuditKind.WRITE_SUCCEEDED]
    assert rec.calls[0].approval.approval_id == "a1"


def test_resume_accepts_single_record_as_json_dict():
    graph, _, rec = _write_turn()
    ctx = local_ctx()
    cfg = thread_config(ctx, "t1")
    graph.invoke(ask(), cfg, context=RunContext(ctx))
    graph.invoke(Command(resume=local_approval().model_dump(mode="json")), cfg, context=RunContext(ctx))
    assert len(rec.calls) == 1


@pytest.mark.parametrize("resume, code", [
    ({"w1": local_approval(plan_sha256="c" * 64)}, "stale_approval"),
    ({"w1": local_approval(target="tiled-elsewhere")}, "stale_approval"),
    ({"w1": local_approval(expires_at=NOW - timedelta(minutes=1))}, "stale_approval"),
    ("reject", "denied"),  # langgraph 1.2.10 cannot resume with None; any non-record value rejects
    ({"w1": None}, "denied"),
    ({"w1": {"not": "an approval"}}, "denied"),
])
def test_resume_without_matching_approval_never_writes(resume, code):
    sink = InMemoryEventSink()
    graph, _, rec = _write_turn(sink=sink)
    ctx = local_ctx()
    cfg = thread_config(ctx, "t1")
    graph.invoke(ask(), cfg, context=RunContext(ctx))
    out = graph.invoke(Command(resume=resume), cfg, context=RunContext(ctx))
    assert tool_messages(out)[0]["error_code"] == code and rec.calls == []
    assert AuditKind.APPROVAL_REJECTED in sink.kinds() and AuditKind.WRITE_ATTEMPTED not in sink.kinds()


def test_plan_changed_after_approval_is_stale():
    plans = Plans()
    graph, _, rec = _write_turn(plans=plans)
    ctx = local_ctx()
    cfg = thread_config(ctx, "t1")
    graph.invoke(ask(), cfg, context=RunContext(ctx))
    plans.plans["p1"] = make_plan(plan_sha256="d" * 64)  # candidate changed while waiting
    out = graph.invoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(ctx))
    assert tool_messages(out)[0]["error_code"] == "stale_approval" and rec.calls == []


def test_write_without_resolvable_plan_is_denied_without_interrupt():
    graph, _, rec = build([tool_call("kg_apply_plan", {"plan_id": "missing"}, "w1"), AIMessage(content="x")])
    ctx = local_ctx()
    out = graph.invoke(ask(), thread_config(ctx, "t1"), context=RunContext(ctx))
    assert "__interrupt__" not in out and tool_messages(out)[0]["error_code"] == "denied" and rec.calls == []


def test_resume_after_restart_with_same_saver_and_thread():
    saver = InMemorySaver()
    graph, _, first_rec = _write_turn(saver=saver)
    ctx = local_ctx()
    cfg = thread_config(ctx, "t1")
    graph.invoke(ask(), cfg, context=RunContext(ctx))
    # "restart": a brand-new registry, model and graph over the same durable saver
    restarted, model, rec = build([AIMessage(content="published after restart")], saver=saver)
    out = restarted.invoke(Command(resume={"w1": local_approval()}), cfg, context=RunContext(ctx))
    assert out["messages"][-1].content == "published after restart"
    assert first_rec.calls == [] and len(rec.calls) == 1


def test_new_message_while_awaiting_approval_keeps_the_request_open():
    # T02B (owner Q8 option b) replaces the T02 behavior that repaired (dropped) the pending call.
    graph, model, rec = build([tool_call("kg_apply_plan", {"plan_id": "p1"}, "w1"),
                               AIMessage(content="here is the answer")])
    ctx = local_ctx()
    cfg = thread_config(ctx, "t1")
    graph.invoke(ask(), cfg, context=RunContext(ctx))
    out = graph.invoke(ask("another question"), cfg, context=RunContext(ctx))
    assert out["messages"][-1].content == "here is the answer" and rec.calls == []
    assert out["__interrupt__"][0].value["requests"][0]["tool_call_id"] == "w1"
    sent = model.calls[-1]
    answered = {m.tool_call_id for m in sent if isinstance(m, ToolMessage)}
    assert all(c["id"] in answered for m in sent for c in (getattr(m, "tool_calls", None) or []))


# -- isolation -------------------------------------------------------------------------------


def test_threads_and_principals_do_not_leak():
    graph, model, _ = build([AIMessage(content="a1"), AIMessage(content="b1"), AIMessage(content="a2")])
    alice, bob = als_ctx("alice"), als_ctx("bob")
    graph.invoke(ask("alice secret"), thread_config(alice, "t1"), context=RunContext(alice))
    graph.invoke(ask("bob question"), thread_config(bob, "t1"), context=RunContext(bob))
    graph.invoke(ask("alice again"), thread_config(alice, "t1"), context=RunContext(alice))
    bob_view = " ".join(str(m.content) for m in model.calls[1])
    assert "alice secret" not in bob_view
    alice_view = [m.content for m in model.calls[2] if isinstance(m, HumanMessage)]
    assert alice_view == ["alice secret", "alice again"]


def test_raw_thread_id_cannot_be_reused_by_another_principal():
    graph, model, _ = build([AIMessage(content="a")])
    cfg = {"configurable": {"thread_id": "shared"}}
    graph.invoke(ask("mine"), cfg, context=RunContext(als_ctx("alice")))
    with pytest.raises(ContractError) as err:
        graph.invoke(ask("let me in"), cfg, context=RunContext(als_ctx("mallory")))
    assert err.value.code is ErrorCode.DENIED and len(model.calls) == 1


def test_two_runtimes_in_one_process_are_independent():
    g1, m1, r1 = build([tool_call("lit_echo", {"q": "x"}, "c1"), AIMessage(content="one")])
    g2, m2, r2 = build([AIMessage(content="two")])
    ctx = local_ctx()
    g1.invoke(ask(), thread_config(ctx, "t"), context=RunContext(ctx))
    out = g2.invoke(ask(), thread_config(ctx, "t"), context=RunContext(ctx))
    assert out["messages"][-1].content == "two" and len(r1.calls) == 1 and r2.calls == []


# -- limits, repeats, deadline, cancellation -------------------------------------------------


def test_repeat_call_in_turn_is_refused_not_rerun():
    graph, _, rec = build([tool_call("lit_echo", {"q": "x"}, "c1"), tool_call("lit_echo", {"q": "x"}, "c2"),
                           AIMessage(content="done")])
    ctx = local_ctx()
    out = graph.invoke(ask(), thread_config(ctx, "t1"), context=RunContext(ctx))
    results = tool_messages(out)
    assert len(rec.calls) == 1 and results[1].get("repeated_call") is True


def test_same_call_allowed_again_in_a_new_turn():
    graph, _, rec = build([tool_call("lit_echo", {"q": "x"}, "c1"), AIMessage(content="d1"),
                           tool_call("lit_echo", {"q": "x"}, "c2"), AIMessage(content="d2")])
    ctx = local_ctx()
    cfg = thread_config(ctx, "t1")
    graph.invoke(ask("first"), cfg, context=RunContext(ctx))
    graph.invoke(ask("second"), cfg, context=RunContext(ctx))
    assert len(rec.calls) == 2


def test_tool_call_budget_spans_steps_of_a_turn():
    graph, _, rec = build([tool_call("lit_echo", {"q": "a"}, "c1"), tool_call("lit_echo", {"q": "b"}, "c2"),
                           AIMessage(content="done")], limits=RuntimeLimits(max_tool_calls=1))
    ctx = local_ctx()
    out = graph.invoke(ask(), thread_config(ctx, "t1"), context=RunContext(ctx))
    assert [r["status"] for r in tool_messages(out)] == ["ok", "error"]
    assert tool_messages(out)[1]["error_code"] == "budget_exhausted" and len(rec.calls) == 1


def test_model_call_budget_stops_the_loop():
    script = [tool_call("lit_echo", {"q": str(i)}, f"c{i}") for i in range(10)]
    graph, model, _ = build(script, limits=RuntimeLimits(max_model_calls=3))
    ctx = local_ctx()
    out = graph.invoke(ask(), thread_config(ctx, "t1"), context=RunContext(ctx))
    last = out["messages"][-1]
    assert last.response_metadata[RUNTIME_STOP_KEY] == "budget_exhausted" and len(model.calls) == 3


def test_deadline_stops_before_model_call():
    graph, model, _ = build([AIMessage(content="never")])
    ctx = local_ctx(deadline=NOW - timedelta(seconds=1))
    out = graph.invoke(ask(), thread_config(ctx, "t1"), context=RunContext(ctx))
    assert out["messages"][-1].response_metadata[RUNTIME_STOP_KEY] == "budget_exhausted" and model.calls == []


def test_cancellation_propagates_through_the_graph():
    done = []

    async def long_tool(inv):
        await asyncio.sleep(10)
        done.append(True)
        return {"never": True}

    reg = ToolRegistry()
    reg.register(read_spec(), long_tool)
    reg.freeze()
    admission = CountingAdmission()
    graph, _, _ = build([tool_call("lit_echo", {"q": "x"}, "c1"), AIMessage(content="no")], registry=reg,
                        admission=admission)
    ctx = local_ctx()

    async def main():
        task = asyncio.create_task(graph.ainvoke(ask(), thread_config(ctx, "t1"), context=RunContext(ctx)))
        await asyncio.sleep(0.05)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main())
    assert done == [] and admission.held == 0

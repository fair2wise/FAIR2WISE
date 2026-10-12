"""T03: F2WApplication ownership, context hooks, delegated runs, restart and cancellation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.modules.contracts import ContractError, ErrorCode, ToolStatus
from tests.read_path.read_path_fakes import ANSWER_TEXT, ANSWERED_Q, ask_tool_then_relay, local_settings
from tests.read_path.test_read_tool import SUCCESS, StubAgent
from tests.runtime.runtime_fakes import CountingAdmission, ScriptedChatModel, als_ctx


def _service(agent: Any):
    from app.modules.runtime_bridge.retrieval import LegacyRetrievalService

    return LegacyRetrievalService(agent)


def _app(agent: Any = None, **kw: Any):
    from app.modules.application import F2WApplication

    return F2WApplication(settings=local_settings(), retrieval=_service(agent or StubAgent(SUCCESS)), **kw)


def test_two_instances_share_no_registry_executor_ledger_or_checkpointer():
    a = _app(chat_model=ScriptedChatModel())
    b = _app(chat_model=ScriptedChatModel())
    assert a.registry is not b.registry and a.skills is not b.skills
    assert a.executor is not b.executor and a.ledger is not b.ledger
    assert a.checkpointer is not b.checkpointer and a.supervisor is not b.supervisor
    assert a.registry.get("answer_from_knowledge").fn is not b.registry.get("answer_from_knowledge").fn
    assert a.registry.frozen and b.registry.frozen
    asyncio.run(a.answer_from_knowledge(a.local_context(), "q?"))
    assert a.retrieval.calls == 1 and b.retrieval.calls == 0
    assert len(a.event_sink.events) == len(b.event_sink.events) == 0  # reads write no audit events


def test_default_admission_is_the_shared_provider_admission():
    from app.modules.llm_admission import ProviderAdmission

    assert isinstance(_app().admission, ProviderAdmission)


def test_local_context_is_local_operator_and_resolve_context_agrees():
    app = _app()
    ctx = app.local_context(session_id="s1")
    assert ctx.principal.subject == "local-operator" and ctx.has("chat.ask") and ctx.session_id == "s1"
    resolved = app.context(session_id="s1")
    assert resolved.principal == ctx.principal and resolved.capabilities == ctx.capabilities


def test_als_settings_require_a_verified_identity():
    from app.modules.application import F2WApplication
    from app.modules.security.authorization import DeploymentSettings, Unauthenticated

    app = F2WApplication(settings=DeploymentSettings(profile="als-dev", writer_identity="f2w-writer"),
                         retrieval=_service(StubAgent(SUCCESS)))
    with pytest.raises(ContractError) as exc:
        app.local_context()
    assert exc.value.code is ErrorCode.DENIED
    with pytest.raises((ContractError, Unauthenticated)):
        app.context(None)  # no identity port and no credential: fail closed, never local-operator


def test_als_chat_user_may_ask():
    outcome = asyncio.run(_app().answer_from_knowledge(als_ctx("alice"), "q?"))
    assert outcome.result.status is ToolStatus.OK


def test_delegate_needs_an_injected_chat_model():
    app = _app()
    with pytest.raises(ContractError) as exc:
        asyncio.run(app.delegate(app.local_context(), "q?", thread_id="t"))
    assert exc.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_delegated_run_uses_f2w_prompt_skill_and_admission():
    from app.modules.runtime_bridge.bootstrap import QA_DELEGATION_PROMPT

    model = ScriptedChatModel(script=ask_tool_then_relay(ANSWERED_Q))
    admission = CountingAdmission()
    agent = StubAgent(dict(SUCCESS, answer=ANSWER_TEXT))
    app = _app(agent, chat_model=model, admission=admission)
    out = asyncio.run(app.delegate(app.local_context(session_id="s"), ANSWERED_Q, thread_id="t1"))
    assert out.final_text == ANSWER_TEXT and out.evidence_ids == ["kg:g1:n1"] and agent.calls == 1
    system = model.calls[0][0].content
    assert system.startswith(QA_DELEGATION_PROMPT) and "answer_literature_question" in system
    assert model.bound[0] == ["answer_from_knowledge"]
    assert admission.aio == {"cborg": 2} and admission.held == 0  # two supervisor model calls, no QA slot


def test_delegated_tool_is_hidden_and_denied_without_chat_ask():
    from app.modules.contracts import AuthorizationMode, DeploymentProfile, Principal, RequestContext
    from tests.runtime.runtime_fakes import NOW, tool_call

    ctx = RequestContext(profile=DeploymentProfile.ALS_DEV, mode=AuthorizationMode.TILED_AUTHENTICATED,
                         principal=Principal(subject="bob", issuer="https://tiled-staging.als.lbl.gov/",
                                             is_admin=False, verified_at=NOW),
                         request_id="r", capabilities=frozenset({"chat.read_support"}))
    model = ScriptedChatModel(script=[tool_call("answer_from_knowledge", {"question": "q?"}, "c1")])
    agent = StubAgent(SUCCESS)
    app = _app(agent, chat_model=model)
    out = asyncio.run(app.delegate(ctx, "q?", thread_id="t"))
    assert model.bound == []  # no tools offered to this principal
    assert [r.status for r in out.tool_results] == [ToolStatus.DENIED] and agent.calls == 0


def test_restart_new_instance_same_checkpointer_continues_the_thread():
    from app.modules.agent_runtime.checkpointer import ManagedCheckpointer

    saver = ManagedCheckpointer()
    first = _app(chat_model=ScriptedChatModel(script=ask_tool_then_relay("first question")), checkpointer=saver)
    ctx = first.local_context(session_id="s1")
    asyncio.run(first.delegate(ctx, "first question", thread_id="t"))
    del first

    second_model = ScriptedChatModel(script=ask_tool_then_relay("second question", "c2"))
    second = _app(chat_model=second_model, checkpointer=saver)
    history = asyncio.run(second.thread_messages(second.local_context(session_id="s1"), "t"))
    assert [m.type for m in history] == ["human", "ai", "tool", "ai"]
    out = asyncio.run(second.delegate(second.local_context(session_id="s1"), "second question", thread_id="t"))
    assert len(out.reads) == 1 and out.reads[0].result.status is ToolStatus.OK
    seen = [m.content for m in second_model.calls[0] if m.type == "human"]
    assert seen == ["first question", "second question"]  # the restarted instance resumed the same thread
    # A different session cannot see that thread.
    assert asyncio.run(second.thread_messages(second.local_context(session_id="other"), "t")) == []
    assert saver.active_threads() == {}


def test_direct_call_cancellation_propagates_and_leaves_the_service_usable():
    started = asyncio.Event()

    class Slow(StubAgent):
        async def query(self, question, history=None, source_rag=None):
            self.calls += 1
            started.set()
            await asyncio.sleep(3600)

    agent = Slow(SUCCESS)
    app = _app(agent)

    async def main():
        task = asyncio.create_task(app.answer_from_knowledge(app.local_context(), "q?"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(app.ledger) == 0  # nothing recorded for a cancelled call
        agent.query = StubAgent(SUCCESS).query  # the next call works normally
        return await app.answer_from_knowledge(app.local_context(), "q?")

    assert asyncio.run(main()).result.status is ToolStatus.OK


def test_delegated_cancellation_propagates_and_releases_the_thread():
    from app.modules.agent_runtime.checkpointer import ManagedCheckpointer

    started = asyncio.Event()

    class Slow(StubAgent):
        async def query(self, question, history=None, source_rag=None):
            started.set()
            await asyncio.sleep(3600)

    saver = ManagedCheckpointer()
    app = _app(Slow(SUCCESS), chat_model=ScriptedChatModel(script=ask_tool_then_relay("q?")), checkpointer=saver)

    async def main():
        task = asyncio.create_task(app.delegate(app.local_context(session_id="s"), "q?", thread_id="t"))
        await started.wait()
        assert len(saver.active_threads()) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert saver.active_threads() == {}


def test_sync_wrapper_for_cli_callers():
    app = _app()
    assert app.answer_from_knowledge_sync(app.local_context(), "q?").result.status is ToolStatus.OK

    async def inside_loop():
        with pytest.raises(RuntimeError):
            app.answer_from_knowledge_sync(app.local_context(), "q?")

    asyncio.run(inside_loop())


def test_close_releases_only_an_owned_checkpointer():
    from app.modules.agent_runtime.checkpointer import ManagedCheckpointer

    injected = ManagedCheckpointer()
    with _app(chat_model=ScriptedChatModel(), checkpointer=injected):
        pass
    assert injected._saver is not None  # injected stays open
    owned = _app(chat_model=ScriptedChatModel())
    owned.close()
    assert owned._owned_checkpointer is None

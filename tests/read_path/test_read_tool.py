"""T03: the answer_from_knowledge Tool/Skill contract and the legacy -> ToolResult mapping."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest

from app.modules.contracts import (
    AuthorizationMode,
    ContractError,
    DeploymentProfile,
    Effect,
    ErrorCode,
    Principal,
    RequestContext,
    ToolStatus,
)
from app.modules.runtime_bridge.qa_tool import (
    ANSWER_FROM_KNOWLEDGE,
    LegacyResultLedger,
    verdict_to_result,
)
from tests.read_path.read_path_fakes import (
    ANSWERED_Q,
    QuestionKeyedClient,
    install_fake_inference,
    local_settings,
    retrieval_service,
    write_graph,
)
from tests.runtime.runtime_fakes import NOW


class StubAgent:
    """Returns (or raises) a scripted value from query."""

    def __init__(self, value: Any = None, *, error: BaseException = None) -> None:
        self.value = value
        self.error = error
        self.calls = 0
        self.live = []

    def set_live_tiled(self, enabled, *, uri=None):
        self.live.append((enabled, uri))

    async def query(self, question, history=None, source_rag=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.value


def _app(agent: Any, **kw: Any):
    from app.modules.application import F2WApplication
    from app.modules.runtime_bridge.retrieval import LegacyRetrievalService

    return F2WApplication(settings=local_settings(), retrieval=LegacyRetrievalService(agent), **kw)


def _ask(app, ctx=None, **args):
    ctx = ctx or app.local_context()
    return asyncio.run(app.answer_from_knowledge(ctx, args.pop("question", "q?"), **args))


SUCCESS: Dict[str, Any] = {"status": "success", "question": "q?", "sufficient": True, "answer": "A [KG: g1: N1].",
                           "missing_topics": [], "selected": ["n1"], "no_evidence": False,
                           "selected_hits": [{"id": "n1", "graph_id": "g1"}], "direct_evidence_count": 1,
                           "graph_ids": ["g1"]}


def test_spec_declares_read_and_inference_effects_and_chat_capability():
    spec = ANSWER_FROM_KNOWLEDGE
    assert spec.name == "answer_from_knowledge" and spec.domain == "retrieval"
    assert spec.effects == frozenset({Effect.READ_CORPUS, Effect.MODEL_INFERENCE})
    assert spec.required_capabilities == frozenset({"chat.ask"})
    assert spec.idempotent is True
    for token in ("[KG: <graph_id>: <name>]", "[PDF:", "[OPS:", "[MP:", "kg:<graph_id>:<node_id>"):
        assert token in spec.evidence_contract


def test_registry_and_skill_are_frozen_and_cover_the_tool():
    app = _app(StubAgent(SUCCESS))
    assert app.registry.frozen and [s.name for s in app.registry.list()] == ["answer_from_knowledge"]
    skill = app.skills.get("answer_literature_question")
    assert skill is not None and skill.tools == ("answer_from_knowledge",)
    assert [e.name for e in app.skills.resolve("answer_literature_question")] == ["answer_from_knowledge"]
    from app.modules.agent_runtime.registry import ToolRegistryError

    with pytest.raises(ToolRegistryError):
        app.registry.register(ANSWER_FROM_KNOWLEDGE, lambda inv: None)


def test_bootstrap_registers_nothing_until_called():
    import app.modules.runtime_bridge.bootstrap as boot

    assert not any(isinstance(v, type(_app(StubAgent()).registry)) for v in vars(boot).values())


@pytest.mark.parametrize("verdict, status, code, outcome", [
    (SUCCESS, ToolStatus.OK, None, "answered"),
    (dict(SUCCESS, sufficient=False, answer=None, missing_topics=["x"]), ToolStatus.PARTIAL, None, "insufficient"),
    (dict(SUCCESS, sufficient=False, answer=None, no_evidence=True), ToolStatus.EMPTY, None, None),
    (dict(SUCCESS, status="judge_error", sufficient=False, error="boom"), ToolStatus.UNAVAILABLE,
     ErrorCode.UNAVAILABLE, None),
    (dict(SUCCESS, status="context_error", sufficient=False), ToolStatus.ERROR, ErrorCode.INTERNAL, None),
    (dict(SUCCESS, status="weird"), ToolStatus.ERROR, ErrorCode.UNSUPPORTED_CAPABILITY, None),
    (["malformed", "verdict"], ToolStatus.ERROR, ErrorCode.INTERNAL, None),
])
def test_verdict_mapping_keeps_states_distinct(verdict, status, code, outcome):
    result = verdict_to_result(verdict, provenance_id="p")
    assert result.status is status and result.error_code is code
    assert (result.data or {}).get("outcome") == outcome
    if status in (ToolStatus.OK, ToolStatus.PARTIAL):
        assert result.evidence_ids == ["kg:g1:n1"]
    else:
        assert result.evidence == ()


def test_selected_without_hits_uses_the_single_loaded_graph():
    result = verdict_to_result({"status": "success", "sufficient": True, "answer": "a", "selected": ["n1", "n2"],
                                "graph_ids": ["g"]})
    assert result.evidence_ids == ["kg:g:n1", "kg:g:n2"]
    ambiguous = verdict_to_result({"status": "success", "sufficient": True, "answer": "a", "selected": ["n1"],
                                   "graph_ids": ["g", "h"]})
    assert ambiguous.evidence_ids == ["kg:n1"]


def test_all_failure_and_empty_states_are_distinguishable_end_to_end():
    outcomes = {}
    outcomes["ok"] = _ask(_app(StubAgent(SUCCESS)))
    outcomes["insufficient"] = _ask(_app(StubAgent(dict(SUCCESS, sufficient=False, answer=None))))
    outcomes["empty"] = _ask(_app(StubAgent(dict(SUCCESS, sufficient=False, no_evidence=True))))
    outcomes["provider"] = _ask(_app(StubAgent(dict(SUCCESS, status="judge_error", sufficient=False))))
    outcomes["no_graphs"] = _ask(_app(StubAgent(error=RuntimeError("No knowledge graphs loaded; cannot answer"))))
    outcomes["timeout"] = _ask(_app(StubAgent(error=TimeoutError())))
    outcomes["crash"] = _ask(_app(StubAgent(error=ValueError("bug"))))
    chat_only_without_ask = RequestContext(
        profile=DeploymentProfile.ALS_DEV, mode=AuthorizationMode.TILED_AUTHENTICATED,
        principal=Principal(subject="bob", issuer="https://tiled-staging.als.lbl.gov/", is_admin=False,
                            verified_at=NOW), request_id="r", capabilities=frozenset({"chat.read_support"}))
    denied_agent = StubAgent(SUCCESS)
    outcomes["denied"] = _ask(_app(denied_agent), ctx=chat_only_without_ask)
    invalid_agent = StubAgent(SUCCESS)
    app = _app(invalid_agent)
    outcomes["invalid"] = asyncio.run(app.answer_from_knowledge(app.local_context(), ""))
    pairs = {k: (v.result.status, v.result.error_code) for k, v in outcomes.items()}
    assert pairs == {
        "ok": (ToolStatus.OK, None),
        "insufficient": (ToolStatus.PARTIAL, None),
        "empty": (ToolStatus.EMPTY, None),
        "provider": (ToolStatus.UNAVAILABLE, ErrorCode.UNAVAILABLE),
        "no_graphs": (ToolStatus.UNAVAILABLE, ErrorCode.UNAVAILABLE),
        "timeout": (ToolStatus.UNAVAILABLE, ErrorCode.UNAVAILABLE),
        "crash": (ToolStatus.ERROR, ErrorCode.INTERNAL),
        "denied": (ToolStatus.DENIED, ErrorCode.DENIED),
        "invalid": (ToolStatus.INVALID, ErrorCode.INVALID_INPUT),
    }
    # Denied and invalid calls never reached the retrieval agent.
    assert denied_agent.calls == 0 and invalid_agent.calls == 0
    assert not outcomes["denied"].ran and not outcomes["invalid"].ran
    with pytest.raises(ContractError) as exc:
        outcomes["denied"].to_legacy()
    assert exc.value.code is ErrorCode.DENIED


def test_provider_unavailability_from_the_real_agent_is_never_empty_or_ok(tmp_path, monkeypatch):
    install_fake_inference(monkeypatch, QuestionKeyedClient(fail=ConnectionError("cborg down")))
    from app.modules.application import F2WApplication

    app = F2WApplication(settings=local_settings(), retrieval=retrieval_service([write_graph(tmp_path)]))
    outcome = asyncio.run(app.answer_from_knowledge(app.local_context(), ANSWERED_Q))
    assert outcome.legacy_verdict["status"] == "judge_error"
    assert outcome.result.status is ToolStatus.UNAVAILABLE and outcome.result.retryable is True
    assert outcome.result.data is None


def test_failed_call_is_not_retried_by_default():
    agent = StubAgent(dict(SUCCESS, status="judge_error", sufficient=False))
    _ask(_app(agent))
    assert agent.calls == 1  # the legacy path makes one attempt; the runtime does not add retries


def test_legacy_payload_and_exception_are_returned_unchanged():
    verdict = dict(SUCCESS, extra={"nested": [1, 2]})
    outcome = _ask(_app(StubAgent(verdict)))
    assert outcome.to_legacy() is verdict
    malformed = ["malformed", "verdict"]
    assert _ask(_app(StubAgent(malformed))).to_legacy() is malformed
    error = RuntimeError("synthetic retrieval failure")
    with pytest.raises(RuntimeError) as exc:
        _ask(_app(StubAgent(error=error))).to_legacy()
    assert exc.value is error


def test_large_answers_are_bounded_in_data_but_legacy_payload_is_intact():
    verdict = dict(SUCCESS, answer="x" * 20_000)
    outcome = _ask(_app(StubAgent(verdict)))
    assert outcome.result.status is ToolStatus.PARTIAL and outcome.result.data["truncated"] is True
    assert outcome.to_legacy()["answer"] == "x" * 20_000
    assert outcome.evidence_ids == ["kg:g1:n1"]


def test_cancellation_inside_the_tool_propagates():
    class Cancelling(StubAgent):
        async def query(self, question, history=None, source_rag=None):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        _ask(_app(Cancelling()))


def test_cancelled_contract_error_is_control_flow_not_a_result():
    with pytest.raises(ContractError) as exc:
        _ask(_app(StubAgent(error=ContractError(ErrorCode.CANCELLED, "operator cancelled"))))
    assert exc.value.code is ErrorCode.CANCELLED


def test_live_tiled_choice_is_applied_with_the_configured_uri():
    from app.modules.application import F2WApplication
    from app.modules.runtime_bridge.retrieval import LegacyRetrievalService

    agent = StubAgent(SUCCESS)
    service = LegacyRetrievalService(agent, tiled_uri_provider=lambda: "http://127.0.0.1:8765")
    app = F2WApplication(settings=local_settings(), retrieval=service)
    asyncio.run(app.answer_from_knowledge(app.local_context(), "q?", use_live_tiled=True))
    asyncio.run(app.answer_from_knowledge(app.local_context(), "q?"))
    assert agent.live == [(True, "http://127.0.0.1:8765")]  # omitted -> agent default untouched


def test_older_query_signatures_still_work():
    class Old:
        calls = []

        async def query(self, question):
            self.calls.append(question)
            return SUCCESS

    assert _ask(_app(Old()), history=[{"role": "user", "content": "hi"}], source_rag=True).to_legacy() is SUCCESS


def test_ledger_is_bounded():
    ledger = LegacyResultLedger(max_entries=2)
    from app.modules.runtime_bridge.qa_tool import LegacyEntry

    for i in range(5):
        ledger.put(f"p{i}", LegacyEntry(verdict=i))
    assert len(ledger) == 2 and ledger.get("p0") is None and ledger.get("p4").verdict == 4
    assert ledger.get("p4", pop=True).verdict == 4 and ledger.get("p4") is None

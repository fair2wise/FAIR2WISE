"""The chat helper agents' model calls are opt-in (owner decision 2026-11-09: avoid spending even the free credits).

Without ``F2W_HELPER_AGENT_LLM`` the orchestrator router and the evidence debater take their deterministic path
and never call the classifier or the asker; with it on they behave as before (the goldens run with it on, see
``tests/conftest.py``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.modules.model_policy import HELPER_LLM_FLAG, helper_model_calls_enabled
from app.modules.agent_runtime.routing import RoutingHooks, WorkflowRouter
from app.modules.curation.evidence import DebateHooks, EvidenceDebater, fallback_decide


@pytest.mark.parametrize("value,expected", [(None, False), ("", False), ("0", False), ("off", False), ("no", False),
                                            ("1", True), ("true", True), ("YES", True), (" on ", True)])
def test_flag_parsing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(HELPER_LLM_FLAG, raising=False)
    else:
        monkeypatch.setenv(HELPER_LLM_FLAG, value)
    assert helper_model_calls_enabled() is expected
    assert helper_model_calls_enabled({}) is False and helper_model_calls_enabled({HELPER_LLM_FLAG: "1"}) is True


def test_flag_name_and_default_off(monkeypatch):
    assert HELPER_LLM_FLAG == "F2W_HELPER_AGENT_LLM"
    monkeypatch.delenv(HELPER_LLM_FLAG, raising=False)
    assert helper_model_calls_enabled() is False
    docs = (Path(__file__).resolve().parents[2] / "mkdocs/docs/configuration.md").read_text()
    assert HELPER_LLM_FLAG in docs and "Opt-in" in docs


def _hooks(notes):
    return RoutingHooks(is_ops_layout=lambda text: False, note_precheck=lambda *a: notes.append(("precheck", a)),
                        note_error=lambda where, exc: notes.append((where, repr(exc))), bind_context=lambda fn: fn)


def _debate_hooks(notes):
    return DebateHooks(note_error=lambda where, exc: notes.append((where, repr(exc))), bind_context=lambda fn: fn)


FRESH = "What is the glass transition temperature of P3HT?"


def test_router_takes_the_deterministic_route_without_the_flag(monkeypatch):
    monkeypatch.delenv(HELPER_LLM_FLAG, raising=False)
    calls, notes = [], []

    def classifier(turn, state):
        calls.append(turn)
        return {"classification": "mundane_conversation", "reason": "x"}

    router = WorkflowRouter(classifier=classifier, hooks=_hooks(notes), model_calls_enabled=helper_model_calls_enabled)
    decision = asyncio.run(router.decide(FRESH, {"phase": "idle"}, None, None))
    assert calls == [], "the classifier (a model call) must not run without the opt-in flag"
    assert decision == router.fallback(FRESH, {"phase": "idle"}, None) or decision["action"] in {
        "retrieve_kg", "direct_response", "stop_insufficient"}
    assert all(n[0] == "precheck" for n in notes)  # a skipped call is not an error


def test_router_calls_the_classifier_with_the_flag(monkeypatch):
    monkeypatch.setenv(HELPER_LLM_FLAG, "1")
    calls = []

    def classifier(turn, state):
        calls.append(turn)
        return {"classification": "mundane_conversation", "reason": "greeting"}

    router = WorkflowRouter(classifier=classifier, hooks=_hooks([]), model_calls_enabled=helper_model_calls_enabled)
    decision = asyncio.run(router.decide(FRESH, {"phase": "idle"}, None, None))
    assert calls == [FRESH] and decision["action"] == "direct_response"


def test_debater_uses_the_heuristic_without_the_flag(monkeypatch):
    monkeypatch.delenv(HELPER_LLM_FLAG, raising=False)
    calls, notes = [], []
    probe = {"sufficient": False, "evidence_quality": 0.1}
    candidates = [{"title": "A", "score": 0.9}]

    def asker(question, probe_, cands, round_no):
        calls.append(question)
        return {"action": "answer", "reason": "model"}

    debater = EvidenceDebater(asker=asker, hooks=_debate_hooks(notes), model_calls_enabled=helper_model_calls_enabled)
    decision = asyncio.run(debater.decide("q", probe, candidates, 1))
    assert calls == [] and notes == []
    assert decision == fallback_decide(probe, candidates)


def test_debater_asks_with_the_flag(monkeypatch):
    monkeypatch.setenv(HELPER_LLM_FLAG, "true")
    calls = []

    def asker(question, probe_, cands, round_no):
        calls.append(question)
        return {"action": "answer", "reason": "model", "summary": "ok"}

    debater = EvidenceDebater(asker=asker, hooks=_debate_hooks([]), model_calls_enabled=helper_model_calls_enabled)
    asyncio.run(debater.decide("q", {"sufficient": True, "evidence_quality": 0.9}, [], 1))
    assert calls == ["q"]


def test_facades_make_no_model_call_by_default_and_call_with_the_flag(monkeypatch):
    """The legacy facades (the only construction sites) wire the opt-in predicate: off means no client at all."""
    from app.modules.f2w_agent.debate_agent import EvidenceDebateAgent
    from app.modules.f2w_agent.orchestrator_agent import WorkflowOrchestratorAgent
    from app.modules.term_extractor import clients

    made = []

    class Client:
        def chat(self, prompt, **kw):
            return '{"classification": "mundane_conversation", "reason": "r"}'

    def make(**kw):
        made.append(kw)
        return Client()

    monkeypatch.setattr(clients, "make_chat_client", make)
    monkeypatch.delenv(HELPER_LLM_FLAG, raising=False)
    orch = WorkflowOrchestratorAgent(backend="cborg", model="lbl/cborg-chat")
    asyncio.run(orch.decide(FRESH, {"phase": "idle"}, None, None))
    asyncio.run(EvidenceDebateAgent(backend="cborg", model="lbl/cborg-chat").decide(
        "q", {"sufficient": False, "evidence_quality": 0.1}, [{"title": "A", "score": 0.9}], 1))
    assert made == [], "no chat client may be built while F2W_HELPER_AGENT_LLM is off"
    monkeypatch.setenv(HELPER_LLM_FLAG, "1")
    asyncio.run(orch.decide(FRESH, {"phase": "idle"}, None, None))
    assert len(made) == 1

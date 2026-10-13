"""T03B: UI QA turns through the planning supervisor vs the direct call (fake models, no network)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

import pytest

from tests.read_path.read_path_fakes import (
    INSUFFICIENT_Q,
    QuestionKeyedClient,
    SECOND_KG,
    install_fake_inference,
    write_graph,
)
from tests.read_path.test_read_path_parity import QUESTIONS, _legacy_ids, _pipeline
from tests.runtime.runtime_fakes import ScriptedChatModel, tool_call


def test_route_setting_parses_and_defaults_to_supervisor():
    from app.modules.runtime_bridge.mode import RuntimeConfigError
    from app.modules.runtime_bridge.qa_route import QaRoute, qa_route

    assert qa_route({}) is QaRoute.SUPERVISOR
    assert qa_route({"F2W_QA_ROUTE": " "}) is QaRoute.SUPERVISOR
    assert qa_route({"F2W_QA_ROUTE": "Direct"}) is QaRoute.DIRECT
    assert qa_route({"F2W_QA_ROUTE": "supervisor"}) is QaRoute.SUPERVISOR
    with pytest.raises(RuntimeConfigError):
        qa_route({"F2W_QA_ROUTE": "planner"})


@pytest.fixture
def graphs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = install_fake_inference(monkeypatch, QuestionKeyedClient())
    return client, [write_graph(tmp_path / "kg"),
                    write_graph(tmp_path / "kg", SECOND_KG, name="matkg_fixture_ops_v1.json")]


class Planner:
    """Scripted planner that records admission state and call counts."""

    def __init__(self, question_override: str = "") -> None:
        self.calls = 0
        self.in_flight: List[int] = []
        self.override = question_override

    def model(self) -> ScriptedChatModel:
        from app.modules.llm_admission import get_controller

        def step(messages):
            self.calls += 1
            self.in_flight.append(get_controller("cborg").in_flight)
            question = self.override or messages[-1].content
            return tool_call("answer_from_knowledge", {"question": question}, f"p{self.calls}")

        return ScriptedChatModel(script=[step] * 50)


def _run(pipeline, questions) -> Dict[str, Any]:
    async def go():
        out = []
        start = time.perf_counter()
        for q in questions:
            out.append(await pipeline._query_retrieval(q))
        return out, time.perf_counter() - start

    verdicts, elapsed = asyncio.run(go())
    return {"verdicts": verdicts, "elapsed_ms": elapsed * 1000.0}


def test_supervisor_route_matches_direct_on_t03_parity_fixtures(graphs, tmp_path, monkeypatch):
    from app.modules.runtime_bridge import legacy_adapter
    from app.modules.runtime_bridge.qa_route import QaRoute

    judge, kgs = graphs
    planner = Planner()
    monkeypatch.setattr(legacy_adapter, "planning_model_provider", lambda p: (lambda m=planner.model(): m))
    legacy = _pipeline(tmp_path, kgs)
    monkeypatch.setenv("F2W_RUNTIME", "langgraph")
    monkeypatch.setenv("F2W_QA_ROUTE", "direct")
    direct = _pipeline(tmp_path, kgs, "direct")
    monkeypatch.delenv("F2W_QA_ROUTE")
    supervised = _pipeline(tmp_path, kgs, "sup")
    assert direct._read_path.route is QaRoute.DIRECT
    assert supervised._read_path.route is QaRoute.SUPERVISOR

    runs = {}
    for name, p in (("legacy", legacy), ("direct", direct), ("supervisor", supervised)):
        judge_before, planner_before = len(judge.calls), planner.calls
        runs[name] = _run(p, QUESTIONS)
        runs[name]["judge_calls"] = len(judge.calls) - judge_before
        runs[name]["planner_calls"] = planner.calls - planner_before
    for name, run in runs.items():
        print("T03B", name, "judge", run["judge_calls"], "planner", run["planner_calls"],
              f"{run['elapsed_ms']:.1f} ms")

    for i, question in enumerate(QUESTIONS):
        before = runs["legacy"]["verdicts"][i]
        assert runs["direct"]["verdicts"][i] == before
        assert runs["supervisor"]["verdicts"][i] == before  # same evidence, graph ids, answer, status
        assert _legacy_ids(runs["supervisor"]["verdicts"][i]) == _legacy_ids(before)
    assert runs["supervisor"]["planner_calls"] == len(QUESTIONS)  # exactly one planning call per turn
    assert runs["direct"]["planner_calls"] == runs["legacy"]["planner_calls"] == 0
    assert runs["supervisor"]["judge_calls"] == runs["direct"]["judge_calls"] == runs["legacy"]["judge_calls"]
    assert planner.in_flight == [1] * len(QUESTIONS)  # planning call held the shared cborg slot
    last = supervised._read_path.last_supervised
    assert last.fallback is None and len(last.delegated.reads) == 1
    assert supervised._read_path.application.ledger.__len__() == 0  # legacy payloads not retained


def test_supervisor_route_pins_the_ui_arguments(graphs, tmp_path, monkeypatch):
    from app.modules.runtime_bridge import legacy_adapter

    _judge, kgs = graphs
    planner = Planner(question_override="a paraphrase the model made up")
    monkeypatch.setattr(legacy_adapter, "planning_model_provider", lambda p: (lambda m=planner.model(): m))
    legacy = _pipeline(tmp_path, kgs)
    monkeypatch.setenv("F2W_RUNTIME", "langgraph")
    supervised = _pipeline(tmp_path, kgs, "sup")
    expected = _run(legacy, [INSUFFICIENT_Q])["verdicts"]
    assert _run(supervised, [INSUFFICIENT_Q])["verdicts"] == expected


def test_planner_failure_or_no_tool_call_falls_back_to_the_direct_call(graphs, tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage

    from app.modules.runtime_bridge import legacy_adapter

    _judge, kgs = graphs

    def boom(messages):
        raise ConnectionError("provider down")

    model = ScriptedChatModel(script=[boom, AIMessage(content="I'd rather chat.")])
    monkeypatch.setattr(legacy_adapter, "planning_model_provider", lambda p: (lambda: model))
    legacy = _pipeline(tmp_path, kgs)
    monkeypatch.setenv("F2W_RUNTIME", "langgraph")
    supervised = _pipeline(tmp_path, kgs, "sup")
    expected = _run(legacy, QUESTIONS[:2])["verdicts"]
    got = []
    reasons = []
    for q in QUESTIONS[:2]:
        got += _run(supervised, [q])["verdicts"]
        reasons.append(supervised._read_path.last_supervised.fallback)
    assert got == expected
    assert reasons == ["planner_error:ConnectionError", "no_tool_call"]


def test_planning_call_is_recorded_in_the_open_turn_trace(graphs, tmp_path, monkeypatch):
    from app.modules.f2w_agent import trace
    from app.modules.runtime_bridge import legacy_adapter

    _judge, kgs = graphs
    planner = Planner()
    monkeypatch.setattr(legacy_adapter, "planning_model_provider", lambda p: (lambda m=planner.model(): m))
    monkeypatch.setenv("F2W_TRACE_ENABLED", "1")
    monkeypatch.setenv("F2W_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("F2W_RUNTIME", "langgraph")
    supervised = _pipeline(tmp_path, kgs, "sup")

    async def go():
        collector = trace.open_turn(client_turn_id="t03b-1")
        await supervised._query_retrieval(QUESTIONS[0])
        return collector.data["llm_calls"]

    calls = asyncio.run(go())
    plans = [c for c in calls if c["label"] == "supervisor-plan"]
    assert len(plans) == 1 and plans[0]["error"] is None
    assert plans[0]["messages"][0]["role"] == "system"
    assert "answer_from_knowledge" in plans[0]["response"]


def test_delegated_outcome_distinguishes_answered_with_open_approval_from_blocked():
    from langchain_core.messages import AIMessage, HumanMessage

    from tests.read_path.test_application import _app

    app = _app(chat_model=ScriptedChatModel())
    write = AIMessage(content="", tool_calls=[{"name": "kg_apply_plan", "args": {}, "id": "w1",
                                               "type": "tool_call"}])
    earlier = [HumanMessage("apply it"), write, AIMessage(content="waiting")]
    answered = app._delegated_outcome("t", {
        "messages": [*earlier, HumanMessage("new question"), AIMessage(content="the answer")],
        "open_approvals": {"w1": {}}, "__interrupt__": [object()]})
    assert answered.interrupted and not answered.blocked and answered.answered
    assert answered.final_text == "the answer" and answered.open_approvals == ("w1",)
    blocked = app._delegated_outcome("t", {"messages": [HumanMessage("apply it"), write],
                                           "open_approvals": {"w1": {}}, "__interrupt__": [object()]})
    assert blocked.interrupted and blocked.blocked and not blocked.answered and blocked.final_text == ""
    plain = app._delegated_outcome("t", {"messages": [HumanMessage("q"), AIMessage(content="a")]})
    assert not plain.interrupted and not plain.blocked and plain.answered and plain.open_approvals == ()

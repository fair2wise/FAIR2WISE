"""T03 before/after harness: legacy vs direct vs delegated over one fixture KG, fake inference.

For each question the same evidence/graph IDs must come out of:

1. the legacy path: ``AgentPipelineService._query_retrieval`` with ``F2W_RUNTIME`` unset
   (the code that runs today);
2. the same service with ``F2W_RUNTIME=langgraph`` (the UI/CLI toggle);
3. the direct read call ``F2WApplication.answer_from_knowledge`` (no planning);
4. a delegated LangGraph task through the supervisor (scripted fake chat model) that
   calls the same tool.

(3) and (4) run in one application, so they must also hit the same retrieval
service instance.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple

import pytest

from tests.read_path.read_path_fakes import (
    ANSWER_TEXT,
    ANSWERED_Q,
    GRAPH_ID,
    INSUFFICIENT_Q,
    NO_EVIDENCE_Q,
    SECOND_KG,
    QuestionKeyedClient,
    SpyAgent,
    ask_tool_then_relay,
    install_fake_inference,
    local_settings,
    retrieval_service,
    write_graph,
)

QUESTIONS = [ANSWERED_Q, INSUFFICIENT_Q, NO_EVIDENCE_Q]


def _legacy_ids(verdict: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Evidence IDs and loaded graph IDs as the legacy verdict states them."""
    hits = verdict.get("selected_hits") or []
    return [f"kg:{h['graph_id']}:{h['id']}" for h in hits], list(verdict.get("graph_ids") or [])


def _pipeline(tmp_path, graphs, runtime: str = ""):
    from app.modules.f2w_agent import api as api_mod
    from app.modules.f2w_agent.coordinator import CoordinatorConfig

    cfg = CoordinatorConfig(backend="cborg", model="fake-judge", graph=str(graphs[0]),
                            graphs=[str(g) for g in graphs], kg_mode="json", workdir=tmp_path / f"run{runtime}",
                            workers=1)
    return api_mod.AgentPipelineService(cfg)


@pytest.fixture
def graphs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install_fake_inference(monkeypatch, QuestionKeyedClient())
    return [write_graph(tmp_path / "kg"),
            write_graph(tmp_path / "kg", SECOND_KG, name="matkg_fixture_ops_v1.json")]


def test_four_paths_agree_on_evidence_and_graph_ids(graphs, tmp_path, monkeypatch):
    from app.modules.application import F2WApplication
    from tests.runtime.runtime_fakes import ScriptedChatModel

    legacy = _pipeline(tmp_path, graphs)
    assert legacy._read_path is None
    monkeypatch.setenv("F2W_RUNTIME", "langgraph")
    toggled = _pipeline(tmp_path, graphs, "lg")
    assert toggled._read_path is not None
    monkeypatch.delenv("F2W_RUNTIME")

    service = retrieval_service(graphs)
    spy = SpyAgent(service.agent)
    service._agent = spy  # same service object; record what reaches the agent
    script = [step for i, q in enumerate(QUESTIONS) for step in ask_tool_then_relay(q, f"c{i}")]
    app = F2WApplication(settings=local_settings(), retrieval=service, chat_model=ScriptedChatModel(script=script))
    ctx = app.local_context(session_id="parity")

    async def run():
        rows = []
        for i, question in enumerate(QUESTIONS):
            before = await legacy._query_retrieval(question)
            via_toggle = await toggled._query_retrieval(question)
            direct = await app.answer_from_knowledge(ctx, question)
            delegated = await app.delegate(ctx, question, thread_id=f"parity-{i}")
            rows.append((question, before, via_toggle, direct, delegated))
        return rows

    rows = asyncio.run(run())
    report = []
    for question, before, via_toggle, direct, delegated in rows:
        expected_ids, expected_graphs = _legacy_ids(before)
        # (2) toggle returns the identical legacy verdict
        assert via_toggle == before
        # (3) direct: same verdict, and the typed result carries the same evidence
        assert direct.legacy_verdict == before
        assert len(delegated.reads) == 1
        assert delegated.reads[0].legacy_verdict == before
        if before.get("no_evidence"):
            assert direct.result.status.value == "empty" and direct.result.evidence == ()
            assert delegated.reads[0].result.status.value == "empty"
        else:
            assert direct.evidence_ids == expected_ids
            assert direct.result.data["graph_ids"] == expected_graphs
            assert delegated.reads[0].evidence_ids == expected_ids == delegated.evidence_ids
            assert delegated.reads[0].result.data["graph_ids"] == expected_graphs
        report.append((question, before["status"], before["sufficient"], expected_ids, expected_graphs,
                       direct.result.status.value))

    # Same retrieval service instance for direct and delegated calls (2 per question).
    tool_fn = app.registry.get("answer_from_knowledge").fn
    assert tool_fn.service.inner is app.retrieval is service
    assert service.calls == 2 * len(QUESTIONS)
    assert spy.questions == [q for q in QUESTIONS for _ in range(2)]

    for row in report:  # visible with -s; the before/after table in the T03 report
        print("PARITY", row)
    statuses = {q: s for q, *_, s in report}
    assert statuses == {ANSWERED_Q: "ok", INSUFFICIENT_Q: "partial", NO_EVIDENCE_Q: "empty"}
    answered = next(r for r in report if r[0] == ANSWERED_Q)
    assert answered[4] == [GRAPH_ID, "fixture_ops_v1"]
    assert answered[3], "the answered question must select evidence"
    # Delegated reply relays the tool answer verbatim, citation tokens intact.
    assert rows[0][4].final_text == ANSWER_TEXT


def test_answered_result_carries_citation_tokens_and_graph_scoped_evidence(graphs, monkeypatch):
    from app.modules.application import F2WApplication

    app = F2WApplication(settings=local_settings(), retrieval=retrieval_service(graphs))
    outcome = asyncio.run(app.answer_from_knowledge(app.local_context(), ANSWERED_Q))
    data = outcome.result.data
    assert data["outcome"] == "answered" and data["sufficient"] is True
    assert data["answer"] == ANSWER_TEXT
    assert data["citations"] == [f"[KG: {GRAPH_ID}: Resonant soft X-ray scattering]",
                                 f"[KG: {GRAPH_ID}: PEDOT:PSS]"]
    # The same node id in two graphs stays two pieces of evidence (graph_id, node id) identity.
    rsoxs = [e for e in outcome.evidence_ids if e.endswith(":matkg:RSoXS")]
    assert sorted(rsoxs) == ["kg:fixture_ops_v1:matkg:RSoXS", f"kg:{GRAPH_ID}:matkg:RSoXS"]
    assert all(e.match_type.value == "none" and e.quote is None for e in outcome.result.evidence)


def test_judge_receives_the_frozen_judge_system_prompt(graphs, monkeypatch):
    import hashlib
    import json
    from pathlib import Path

    from app.modules.application import F2WApplication
    from app.modules.f2w_agent.prompts import JUDGE_SYSTEM

    client = install_fake_inference(monkeypatch, QuestionKeyedClient())
    app = F2WApplication(settings=local_settings(), retrieval=retrieval_service(graphs))
    asyncio.run(app.answer_from_knowledge(app.local_context(), ANSWERED_Q))
    assert client.calls and client.calls[0][0] == {"role": "system", "content": JUDGE_SYSTEM}
    baseline = json.loads((Path(__file__).resolve().parents[2] / "docs/migration/f2w_llm_qa_baseline.json")
                          .read_text(encoding="utf-8"))
    qa = next(s for s in baseline["sections"] if s["section"] == "qa_policy_and_prompts")
    item = next(i for i in qa["items"] if i.get("id") == "QA-01")
    assert hashlib.sha256(JUDGE_SYSTEM.encode("utf-8")).hexdigest() == item["text_sha256"]


def test_leeway_path_is_preserved(graphs, monkeypatch):
    """Insufficient conceptual question with hits -> the existing leeway synthesis answers."""
    import json

    from app.modules.application import F2WApplication
    from app.modules.contracts.testing import FakeAsyncChatClient

    client = FakeAsyncChatClient(replies=[
        json.dumps({"sufficient": False, "answer": None, "missing_topics": ["history"]}),
        "Leeway answer citing [KG: fixture_v1: PEDOT:PSS].",
    ])
    install_fake_inference(monkeypatch, client)
    app = F2WApplication(settings=local_settings(), retrieval=retrieval_service(graphs))
    outcome = asyncio.run(app.answer_from_knowledge(app.local_context(), "Explain PEDOT:PSS morphology"))
    assert outcome.legacy_verdict["sufficient"] is True
    assert outcome.result.status.value == "ok"
    assert outcome.result.data["citations"] == ["[KG: fixture_v1: PEDOT:PSS]"]
    from app.modules.f2w_agent.prompts import JUDGE_SYSTEM, LEEWAY_SYSTEM

    assert [m[0]["content"] for m in client.calls] == [JUDGE_SYSTEM, LEEWAY_SYSTEM]

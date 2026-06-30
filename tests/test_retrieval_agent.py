import asyncio
from types import SimpleNamespace

from app.modules.f2w_agent import retrieval_agent
from app.modules.f2w_agent.retrieval_agent import RetrievalAgent, _parse_judge


class FakeKG:
    def __init__(self, *, direct_evidence=True):
        self.nodes = {
            "n1": {
                "source_papers": ["paper.pdf"] if direct_evidence else [],
                "description": "context",
            }
        }
        self.out_edges = {}
        self.graph_source_requested = "splash"
        self.graph_source_used = "json_fallback"
        self.context_calls = 0

    def build_context(self, infos, include_structured, char_budget, hint_terms):
        self.context_calls += 1
        return "Source_Papers: paper.pdf\nDescription: grounded context"


def test_parse_judge_handles_string_false_and_string_missing_topics():
    result = _parse_judge(
        '{"sufficient": "false", "answer": null, "missing_topics": "peak fitting"}'
    )

    assert result["sufficient"] is False
    assert result["missing_topics"] == ["peak fitting"]


def test_parse_judge_requires_answer_when_sufficient():
    result = _parse_judge('{"sufficient": true, "answer": null, "missing_topics": []}')

    assert result["sufficient"] is False


def test_query_no_evidence_skips_context_and_llm(monkeypatch):
    agent = RetrievalAgent(graph_source="splash")
    kg = FakeKG(direct_evidence=False)
    agent._kg = kg

    monkeypatch.setattr(
        retrieval_agent.krag,
        "retrieve_nodes",
        lambda question, kg: [SimpleNamespace(id="n1", evidence_ct=1)],
    )
    monkeypatch.setattr(
        retrieval_agent.krag,
        "make_chat_client",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM client should not be built")),
    )

    result = asyncio.run(agent.query("missing question"))

    assert result["sufficient"] is False
    assert result["no_evidence"] is True
    assert result["missing_topics"] == ["missing question"]
    assert result["direct_evidence_count"] == 0
    assert result["graph_source_used"] == "json_fallback"
    assert kg.context_calls == 0


def test_query_judge_error_returns_insufficient(monkeypatch):
    agent = RetrievalAgent(graph_source="json")
    agent._kg = FakeKG(direct_evidence=True)

    monkeypatch.setattr(
        retrieval_agent.krag,
        "retrieve_nodes",
        lambda question, kg: [SimpleNamespace(id="n1", evidence_ct=1)],
    )
    monkeypatch.setattr(
        retrieval_agent.krag,
        "make_chat_client",
        lambda backend, model=None: SimpleNamespace(model="fake"),
    )

    async def fail_llm(cli, messages, label):
        raise RuntimeError("judge unavailable")

    monkeypatch.setattr(retrieval_agent.krag, "call_llm", fail_llm)

    result = asyncio.run(agent.query("question"))

    assert result["status"] == "judge_error"
    assert result["sufficient"] is False
    assert result["missing_topics"] == ["question"]
    assert result["direct_evidence_count"] == 1
    assert "judge unavailable" in result["error"]


def test_query_sufficient_returns_source_metadata(monkeypatch):
    agent = RetrievalAgent(graph_source="json")
    agent._kg = FakeKG(direct_evidence=True)

    monkeypatch.setattr(
        retrieval_agent.krag,
        "retrieve_nodes",
        lambda question, kg: [SimpleNamespace(id="n1", evidence_ct=1)],
    )
    monkeypatch.setattr(
        retrieval_agent.krag,
        "make_chat_client",
        lambda backend, model=None: SimpleNamespace(model="fake"),
    )

    async def ok_llm(cli, messages, label):
        return '{"sufficient": true, "answer": "grounded [PDF: paper.pdf]", "missing_topics": []}'

    monkeypatch.setattr(retrieval_agent.krag, "call_llm", ok_llm)

    result = asyncio.run(agent.query("question"))

    assert result["status"] == "success"
    assert result["sufficient"] is True
    assert result["answer"] == "grounded [PDF: paper.pdf]"
    assert result["graph_source_requested"] == "splash"
    assert result["graph_source_used"] == "json_fallback"


def test_reload_kg_reports_graph_source(monkeypatch):
    agent = RetrievalAgent(graph_file="graph.json", graph_source="splash")

    class FakeLoadedKG:
        nodes = {"n1": {}}
        graph_source_requested = "splash"
        graph_source_used = "json_fallback"

    monkeypatch.setattr(agent, "_build_kg", lambda: FakeLoadedKG())

    result = asyncio.run(agent.reload_kg())

    assert result["nodes"] == 1
    assert result["graph_source_requested"] == "splash"
    assert result["graph_source_used"] == "json_fallback"

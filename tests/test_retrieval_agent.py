import asyncio
from types import SimpleNamespace

from app.modules.f2w_agent import retrieval_agent
from app.modules.f2w_agent.retrieval_agent import (
    JUDGE_SYSTEM,
    LAYOUT_LEEWAY_SYSTEM,
    RetrievalAgent,
    _NUMERIC_CLAIM_RE,
    _has_direct_evidence,
    _is_conceptual_question,
    _is_numeric_claim,
    _is_ops_layout_question,
    _parse_judge,
    build_judge_prompt,
    leeway_system_for,
)


class FakeKG:
    def __init__(self, *, direct_evidence=True, category="Unknown"):
        self.nodes = {
            "n1": {
                "source_papers": ["paper.pdf"] if direct_evidence else [],
                "description": "context" if direct_evidence else "",
                "category": category,
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


def test_judge_system_distinguishes_conceptual_from_numeric():
    assert "Conceptual / overview" in JUDGE_SYSTEM
    assert "RSoXSMeasurement" in JUDGE_SYSTEM
    assert "photon_energy" in JUDGE_SYSTEM
    assert "vocabulary/relation" in JUDGE_SYSTEM
    assert "Never invent authors" in JUDGE_SYSTEM
    assert "not a KG measurement fact" in JUDGE_SYSTEM
    assert "Use ONLY the Retrieved Context" not in JUDGE_SYSTEM
    assert "Do NOT infer, extrapolate, or guess" not in JUDGE_SYSTEM


def test_numeric_claim_re_does_not_match_beamline():
    assert _NUMERIC_CLAIM_RE.search("beamline") is None
    assert _NUMERIC_CLAIM_RE.search("what does the ALS RSoXS beamline specialize in") is None
    assert _is_numeric_claim("what does the ALS RSoXS beamline specialize in") is False
    assert _is_numeric_claim("what is the photon energy for this RSoXS measurement") is True
    assert _NUMERIC_CLAIM_RE.search("photon energy 285 eV") is not None


def test_is_conceptual_question_examples():
    assert _is_conceptual_question("teach me about RSoXS")
    assert _is_conceptual_question("you have been trained on RSoXS papers. Give me a summary")
    assert _is_conceptual_question("what is RSoXS")
    assert _is_conceptual_question("what does the ALS RSoXS beamline specialize in")
    assert _is_conceptual_question("what kind of samples are typically studied")
    assert _is_conceptual_question("give an example of analysis code")
    assert _is_conceptual_question("how does the analysis workflow work")
    assert not _is_conceptual_question("what is the photon energy for this RSoXS measurement")
    assert not _is_conceptual_question("a material not in the graph")


def test_build_judge_prompt_marks_overview_questions():
    prompt = build_judge_prompt("teach me about RSoXS", "## RSoXS\nA scattering technique.")
    assert "conceptual/overview" in prompt
    assert "teach me about RSoXS" in prompt


def test_has_direct_evidence_accepts_technique_category():
    kg = FakeKG(direct_evidence=False, category="RSoXSMeasurement")
    assert _has_direct_evidence(kg, SimpleNamespace(id="n1")) is True


def test_has_direct_evidence_rejects_empty_unknown_node():
    kg = FakeKG(direct_evidence=False, category="Unknown")
    assert _has_direct_evidence(kg, SimpleNamespace(id="n1")) is False


def test_query_conceptual_on_topic_nodes_still_call_judge(monkeypatch):
    agent = RetrievalAgent(graph_source="json")
    kg = FakeKG(direct_evidence=False, category="Unknown")
    agent._kg = kg

    monkeypatch.setattr(
        retrieval_agent.krag,
        "retrieve_nodes",
        lambda question, kg: [SimpleNamespace(id="n1", evidence_ct=0)],
    )
    monkeypatch.setattr(
        retrieval_agent.krag,
        "make_chat_client",
        lambda backend, model=None: SimpleNamespace(model="fake"),
    )

    async def ok_llm(cli, messages, label):
        assert "KG-RAG-judge" == label
        system = messages[0]["content"]
        assert "RSoXSMeasurement" in system
        return (
            '{"sufficient": true, "answer": '
            '"RSoXS is resonant soft X-ray scattering [KG: RSoXS]", "missing_topics": []}'
        )

    monkeypatch.setattr(retrieval_agent.krag, "call_llm", ok_llm)

    result = asyncio.run(agent.query("teach me about RSoXS"))

    assert result["sufficient"] is True
    assert result["no_evidence"] is False
    assert "RSoXS" in result["answer"]
    assert kg.context_calls == 1


def test_query_conceptual_insufficient_judge_uses_leeway(monkeypatch):
    agent = RetrievalAgent(graph_source="json")
    kg = FakeKG(direct_evidence=False, category="RSoXSMeasurement")
    agent._kg = kg

    monkeypatch.setattr(
        retrieval_agent.krag,
        "retrieve_nodes",
        lambda question, kg: [SimpleNamespace(id="n1", evidence_ct=0)],
    )
    monkeypatch.setattr(
        retrieval_agent.krag,
        "make_chat_client",
        lambda backend, model=None: SimpleNamespace(model="fake"),
    )

    labels = []

    async def llm(cli, messages, label):
        labels.append(label)
        if label == "KG-RAG-judge":
            return '{"sufficient": false, "answer": null, "missing_topics": ["photon_energy"]}'
        assert label == "KG-RAG-leeway"
        return (
            "The graph has no filled energy slots. Not a KG measurement fact: "
            "RSoXS is typically used on structured soft-matter films."
        )

    monkeypatch.setattr(retrieval_agent.krag, "call_llm", llm)

    result = asyncio.run(agent.query("what does the ALS RSoXS beamline specialize in"))

    assert result["sufficient"] is True
    assert result["no_evidence"] is False
    assert "Not a KG measurement fact" in result["answer"]
    assert labels == ["KG-RAG-judge", "KG-RAG-leeway"]


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


EVAL_LAYOUT_Q = "how is all of the hardware connected at 11.0.1.2, in order"


def test_layout_question_is_ops_layout_not_science():
    assert _is_ops_layout_question(EVAL_LAYOUT_Q)
    assert _is_ops_layout_question("what comes after M103")
    assert _is_ops_layout_question("describe the beam path at the beamline")
    assert not _is_ops_layout_question("what does the ALS RSoXS beamline specialize in")
    assert not _is_ops_layout_question("teach me about RSoXS")
    assert not _is_ops_layout_question("in order to measure RSoXS we need contrast")
    assert _is_conceptual_question(EVAL_LAYOUT_Q)


def test_layout_judge_prompt_and_leeway_are_ops_not_cyrsoxs():
    prompt = build_judge_prompt(EVAL_LAYOUT_Q, "### Knowledge graph `bl1101`")
    assert "class C" in prompt or "hardware layout" in prompt
    assert "CyRSoXS" in JUDGE_SYSTEM  # science class A still documented
    assert "class C" in JUDGE_SYSTEM.lower() or "Hardware layout" in JUDGE_SYSTEM
    system = leeway_system_for(EVAL_LAYOUT_Q)
    assert system == LAYOUT_LEEWAY_SYSTEM
    assert "related techniques such as NRSS, CyRSoXS" not in system
    assert "Do not mention CyRSoXS" in system
    assert "ordered" in system.lower()


def test_layout_retrieve_returns_ops_path_nodes(tmp_path, monkeypatch):
    from app.modules import kg_rag_api as krag
    from app.modules.bl1101_ingest import ingest_fixture, records_to_graph
    from tests.test_bl1101_ingest import TOPOLOGY_HTML

    monkeypatch.setattr(krag, "RETRIEVAL_BACKEND", "lexical")
    science = tmp_path / "matkg_rsoxs_v1.json"
    ops = tmp_path / "matkg_bl1101_v2.json"
    science.write_text(
        '{"things":[{"id":"matkg:CyRSoXS","name":"CyRSoXS","category":"Software",'
        '"description":"GPU RSXS simulator"}],"associations":[]}',
        encoding="utf-8",
    )
    graph = records_to_graph(
        ingest_fixture(TOPOLOGY_HTML, []),
        {"schema_version": "bl1101_v2", "id_prefix": "beamline"},
    )
    ops.write_text(__import__("json").dumps(graph), encoding="utf-8")

    agent = RetrievalAgent(
        graph_files=[str(science), str(ops)],
        graph_source="json",
        kg_query_hops=1,
        kg_query_max_nodes=50,
    )
    asyncio.run(agent.reload_kg(graph_files=agent._graph_files))
    hits = agent._fanout_hits(EVAL_LAYOUT_Q, agent._graphs)
    assert hits
    assert all(hit.graph_id.startswith("bl1101") for hit in hits)
    names = {hit.name.casefold() for hit in hits}
    assert "m103" in names
    assert any("exit slit" in name for name in names)
    assert "cyrsoxs" not in names
    ctx = agent._build_merged_context(EVAL_LAYOUT_Q, hits, agent._graphs)
    assert "M103" in ctx
    assert "exit slit" in ctx.lower()


def test_layout_user_prompts_are_ops_layout():
    assert _is_ops_layout_question("how is the hardware connected at the RSoXS beamline")
    assert _is_ops_layout_question("how is the hardware connected at 11.01.12")


def test_build_context_accepts_namespace_without_description(tmp_path, monkeypatch):
    from app.modules import kg_rag_api as krag

    monkeypatch.setattr(krag, "RETRIEVAL_BACKEND", "lexical")
    path = tmp_path / "ops.json"
    path.write_text(
        '{"things":[{"id":"beamline:stage-M103","name":"M103","category":"BeamlineStage",'
        '"description":"KB pair"}],"associations":[]}',
        encoding="utf-8",
    )
    kg = krag.KnowledgeGraph(str(path), graph_source="json")
    stub = SimpleNamespace(id="beamline:stage-M103", name="M103", category="BeamlineStage", score_prp=2.1)
    assert not hasattr(stub, "description")
    ctx = kg.build_context([stub], include_structured=False, char_budget=2000, hint_terms=["hardware"])
    assert "M103" in ctx
    assert "KB pair" in ctx


def test_v4_layout_question_builds_context_without_attributeerror(monkeypatch):
    from pathlib import Path

    from app.modules import kg_rag_api as krag

    v4 = Path(__file__).resolve().parents[1] / "storage/kg/matkg_bl1101_v4.json"
    if not v4.exists():
        return
    monkeypatch.setattr(krag, "RETRIEVAL_BACKEND", "lexical")
    agent = RetrievalAgent(
        graph_files=[str(v4)],
        graph_source="json",
        kg_query_hops=3,
        kg_query_max_nodes=50,
    )
    asyncio.run(agent.reload_kg(graph_files=agent._graph_files))
    question = "how is the hardware connected at the RSoXS beamline"
    hits = agent._fanout_hits(question, agent._graphs)
    assert hits
    ctx = agent._build_merged_context(question, hits, agent._graphs)
    assert ctx
    assert "M103" in ctx or "m103" in ctx.lower()
    assert "exit slit" in ctx.lower()

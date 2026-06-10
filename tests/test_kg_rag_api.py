import asyncio
import json
import time

from fastapi.testclient import TestClient
import pytest

from app.modules import kg_rag_api


def write_graph(tmp_path):
    graph = {
        "things": [
            {
                "id": "matkg:P3HT",
                "name": "P3HT",
                "category": "ConjugatedPolymer",
                "description": "Conjugated polymer for organic photovoltaics.",
                "source_papers": ["paper.pdf"],
                "publication_year": 2025,
            },
            {
                "id": "matkg:OPV",
                "name": "Organic photovoltaic device",
                "category": "Device",
                "description": "Solar cell device.",
                "source_papers": [],
            },
            {
                "id": "matkg:GenericMaterial",
                "name": "material",
                "category": "Material",
                "description": "Generic material.",
                "source_papers": [],
            },
        ],
        "associations": [
            {
                "subject": "matkg:P3HT",
                "predicate": "rel:has_application",
                "object": "matkg:OPV",
                "has_evidence": "p1",
            }
        ],
    }
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph))
    return path


def test_tokenize_entities_and_snippet_helpers():
    assert kg_rag_api._tokenize("P3HT in OPV devices!") == ["p3ht", "in", "opv", "devices"]
    assert kg_rag_api.extract_query_entities("How does P3HT improve OPV?")[:1] == [
        "How does P3HT improve OPV?"
    ]
    assert kg_rag_api.extract_query_entities("??") == []
    text = "alpha beta gamma delta epsilon"
    assert "delta" in kg_rag_api.snippet_text(text, 12, ["delta"])
    assert kg_rag_api.snippet_text("", 12, ["delta"]) == ""
    assert kg_rag_api.snippet_text(text, 0, ["delta"]) == ""
    assert kg_rag_api.format_domain_features(
        [{"feature_name": "q_range", "feature_value": "0.1-1.0", "feature_units": "A^-1"}]
    ) == "q_range: 0.1-1.0 A^-1"
    assert kg_rag_api.format_domain_features("bad") == ""


def test_knowledge_graph_lexical_search_and_bfs(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    graph_path = write_graph(tmp_path)

    kg = kg_rag_api.KnowledgeGraph(str(graph_path))
    seeds = kg.semantic_search("P3HT organic photovoltaics", topk=2)
    expanded = kg.weighted_bfs(seeds, hops=1)

    assert seeds[0].id == "matkg:P3HT"
    assert "matkg:OPV" in {node.id for node in expanded}
    assert kg.semantic_search("??", topk=2) == []
    assert kg.weighted_bfs([], hops=1) == []


def test_knowledge_graph_rejects_unknown_retrieval_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "bad-backend")
    graph_path = write_graph(tmp_path)

    try:
        kg_rag_api.KnowledgeGraph(str(graph_path))
    except ValueError as exc:
        assert "Unknown KG_RAG_RETRIEVAL_BACKEND" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_retrieve_nodes_ranks_and_caps_results(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "DEFAULT_K", 2)
    monkeypatch.setattr(kg_rag_api, "STEPWISE", False)
    monkeypatch.setattr(kg_rag_api, "ENABLE_BFS", True)
    graph_path = write_graph(tmp_path)
    kg = kg_rag_api.KnowledgeGraph(str(graph_path))

    results = kg_rag_api.retrieve_nodes("P3HT photovoltaics", kg)

    assert len(results) <= 2
    assert results[0].id == "matkg:P3HT"
    assert results[0].score_prp > 0


def test_retrieve_nodes_without_bfs(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "DEFAULT_K", 3)
    monkeypatch.setattr(kg_rag_api, "STEPWISE", True)
    monkeypatch.setattr(kg_rag_api, "ENABLE_BFS", False)
    graph_path = write_graph(tmp_path)
    kg = kg_rag_api.KnowledgeGraph(str(graph_path))

    results = kg_rag_api.retrieve_nodes("P3HT organic photovoltaic device", kg)

    assert {result.id for result in results} >= {"matkg:P3HT", "matkg:OPV"}
    assert all(result.depth == 0 for result in results)


def test_retrieve_nodes_caps_code_snippet_results(monkeypatch):
    class FakeKG:
        def semantic_search(self, q, topk=None):
            return [kg_rag_api.NodeScore(f"id{i}", 1.0) for i in range(10)]

        def weighted_bfs(self, seeds, hops):
            return []

        def build_nodeinfo(self, sem, graph, ents):
            code_nodes = [
                kg_rag_api.NodeInfo(f"code{i}", f"code {i}", "CodeSnippet", "", 1.0, 0.0, 0, 0.0, 0)
                for i in range(8)
            ]
            normal = kg_rag_api.NodeInfo("material", "P3HT", "Material", "", 0.9, 0.0, 0, 0.0, 0)
            return code_nodes + [normal]

    monkeypatch.setattr(kg_rag_api, "DEFAULT_K", 20)
    monkeypatch.setattr(kg_rag_api, "STEPWISE", False)
    monkeypatch.setattr(kg_rag_api, "ENABLE_BFS", False)

    results = kg_rag_api.retrieve_nodes("code snippets", FakeKG())

    assert sum(1 for result in results if result.category == "CodeSnippet") == 6
    assert any(result.id == "material" for result in results)


def test_nodeinfo_score_penalizes_generic_names_and_boosts_recent_nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    graph_path = write_graph(tmp_path)
    kg = kg_rag_api.KnowledgeGraph(str(graph_path))

    infos = kg.build_nodeinfo(
        [kg_rag_api.NodeScore("matkg:P3HT", 1.0), kg_rag_api.NodeScore("matkg:GenericMaterial", 1.0)],
        [],
        ["p3ht"],
    )
    by_id = {info.id: info for info in infos}

    assert by_id["matkg:GenericMaterial"].score_sem < by_id["matkg:P3HT"].score_sem
    assert by_id["matkg:P3HT"].score_prp > by_id["matkg:GenericMaterial"].score_prp


def test_decompose_splits_compound_questions():
    assert kg_rag_api.decompose("What is P3HT? compare OPV; then list papers") == [
        "What is P3HT",
        "compare OPV",
        "list papers",
    ]
    assert kg_rag_api.decompose("ok") == ["ok"]


def test_build_context_uses_structured_facts_without_pdf_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "")
    graph_path = write_graph(tmp_path)
    kg = kg_rag_api.KnowledgeGraph(str(graph_path))
    node_info = kg.build_nodeinfo(
        [kg_rag_api.NodeScore("matkg:P3HT", 0.9)],
        [kg_rag_api.NodeScore("matkg:P3HT", 1.0)],
        ["p3ht"],
    )

    context = kg.build_context(node_info, include_structured=True, char_budget=2000, hint_terms=["p3ht"])

    assert "Structured_KG_Facts" in context
    assert "(P3HT) -[has_application]-> (Organic photovoltaic device)" in context
    assert "## P3HT (ConjugatedPolymer)" in context
    assert "Publication_Year: 2025" in context


def test_build_context_renders_code_snippet_nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "")
    graph = {
        "things": [
            {
                "id": "matkg:Snippet",
                "name": "analyze snippet",
                "category": "CodeSnippet",
                "description": "Example code.",
                "source_papers": [],
                "function_name": "analyze",
                "code_domain": "scattering",
                "code_language": "python",
                "code_snippet": "def analyze(x):\n    return x",
                "domain_features": [{"feature_name": "q_range", "feature_value": "0.1-1.0"}],
            }
        ],
        "associations": [],
    }
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(graph))
    kg = kg_rag_api.KnowledgeGraph(str(graph_path))
    node_info = kg.build_nodeinfo(
        [kg_rag_api.NodeScore("matkg:Snippet", 0.9)],
        [],
        ["analyze"],
    )

    context = kg.build_context(node_info, include_structured=False, char_budget=2000, hint_terms=[])

    assert "Function: analyze" in context
    assert "Domain_Features:\n- q_range: 0.1-1.0" in context
    assert "```python\ndef analyze(x):\n    return x\n```" in context


def test_load_pdf_text_returns_empty_for_missing_file(tmp_path):
    missing = tmp_path / "missing.pdf"

    assert kg_rag_api.load_pdf_text(str(missing)) == ""


def test_missing_node_tracker_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tracker = kg_rag_api.MissingNodeTracker("graphs/example_graph.json")
    node = kg_rag_api.MissingNode("query", "entity", "reason", time.time())

    tracker.log(node)

    lines = tracker.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["query"] == "query"
    assert record["entity"] == "entity"
    assert record["reason"] == "reason"


def test_call_llm_timeout_error_message(monkeypatch):
    class SlowClient:
        model = "slow-model"

        async def chat(self, messages):
            return "late"

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        raise asyncio.TimeoutError

    old_timeout = kg_rag_api.LLM_TIMEOUT
    kg_rag_api.LLM_TIMEOUT = 0
    monkeypatch.setattr(kg_rag_api.asyncio, "wait_for", fake_wait_for)
    try:
        with pytest.raises(RuntimeError, match="KG-RAG call exceeded 0s"):
            asyncio.run(kg_rag_api.call_llm(SlowClient(), [{"role": "user", "content": "q"}], "KG-RAG"))
    finally:
        kg_rag_api.LLM_TIMEOUT = old_timeout


def test_fastapi_chat_returns_error_for_missing_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(
        kg_rag_api,
        "make_chat_client",
        lambda backend, model=None: type("Client", (), {"model": "fake", "chat": None})(),
    )
    graph_path = write_graph(tmp_path)
    app = kg_rag_api.create_fastapi_app(str(graph_path), backend="ollama")

    response = TestClient(app).post("/api/chat", json={"messages": []})

    assert response.status_code == 400
    assert response.json() == {"error": "No messages"}


def test_conversation_and_prompt_builders():
    conversation = kg_rag_api.Conversation("system")
    conversation.add("u1", "a1")

    messages = conversation.build("u2", prepend="extra")
    rag_prompt = kg_rag_api.build_rag_prompt("What is P3HT?", "## P3HT\ncontext")

    assert messages == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "system", "content": "extra"},
        {"role": "user", "content": "u2"},
    ]
    assert "Question:\nWhat is P3HT?" in rag_prompt
    assert "Retrieved Context:\n## P3HT" in rag_prompt
    assert kg_rag_api.build_baseline_prompt("Q?") == "Question: Q?\n\nAnswer:"


def test_make_chat_client_rejects_unknown_backend():
    try:
        kg_rag_api.make_chat_client(backend="unknown")
    except ValueError as exc:
        assert "Unknown KG-RAG LLM backend" in str(exc)
    else:
        raise AssertionError("Expected ValueError")

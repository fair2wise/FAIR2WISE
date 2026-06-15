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


def test_knowledge_graph_loads_splash_links_source(monkeypatch):
    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": self._data}

    def fake_post(url, json, timeout):
        assert url == "http://localhost:8080/splash_links/graphql"
        assert timeout == 30
        query = json["query"]
        offset = json["variables"]["offset"]
        if "entities" in query:
            entities = [
                {
                    "id": "uuid-p3ht",
                    "entityType": "ConjugatedPolymer",
                    "name": "P3HT",
                    "uri": "matkg:P3HT",
                    "properties": {
                        "description": "Conjugated polymer for organic photovoltaics.",
                        "source_papers": ["paper.pdf"],
                        "publication_year": 2025,
                    },
                },
                {
                    "id": "uuid-opv",
                    "entityType": "Device",
                    "name": "Organic photovoltaic device",
                    "uri": "matkg:OPV",
                    "properties": {"description": "Solar cell device."},
                },
            ]
            return FakeResponse({"entities": entities if offset == 0 else []})
        links = [
            {
                "id": "link-1",
                "subjectId": "uuid-p3ht",
                "predicate": "rel:has_application",
                "objectId": "uuid-opv",
                "properties": {"has_evidence": "p1"},
            }
        ]
        return FakeResponse({"links": links if offset == 0 else []})

    monkeypatch.setattr(kg_rag_api, "GRAPH_SOURCE", "splash")
    monkeypatch.setattr(kg_rag_api, "SPLASH_LINKS_URI", "splash://localhost:8080")
    monkeypatch.setattr(kg_rag_api, "SPLASH_LINKS_PAGE_SIZE", 1000)
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api.requests, "post", fake_post)

    kg = kg_rag_api.KnowledgeGraph("unused.json")

    assert set(kg.nodes) == {"matkg:P3HT", "matkg:OPV"}
    assert kg.out_edges["matkg:P3HT"][0]["object"] == "matkg:OPV"
    assert kg.semantic_search("P3HT photovoltaics", topk=2)[0].id == "matkg:P3HT"


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
        [kg_rag_api.NodeScore("matkg:P3HT", 0.9), kg_rag_api.NodeScore("matkg:OPV", 0.8)],
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


def test_build_context_prefers_source_scoped_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "")
    graph = {
        "things": [
            {
                "id": "matkg:SAXS",
                "name": "SAXS",
                "category": "ExperimentalTechnique",
                "description": "Small-angle X-ray scattering.",
                "source_papers": ["SCIPY_DOCS.pdf", "XRAY1.pdf"],
                "paper_title": "Machine Learning-Assisted Analysis of Small Angle X-ray Scattering",
                "doi": "arXiv:2111.08645v1",
                "authors": ["Tomaszewski P", "Yu S"],
                "source_metadata": {
                    "SCIPY_DOCS.pdf": {
                        "paper_title": "SciPy Peak Finding Algorithms for SAXS/WAXS/GISAXS/GIWAXS"
                    },
                    "XRAY1.pdf": {
                        "paper_title": "Machine Learning-Assisted Analysis of Small Angle X-ray Scattering",
                        "publication_year": 2021,
                        "doi": "arXiv:2111.08645v1",
                        "authors": ["Tomaszewski P", "Yu S"],
                    },
                },
            }
        ],
        "associations": [],
    }
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(graph))
    kg = kg_rag_api.KnowledgeGraph(str(graph_path))
    node_info = kg.build_nodeinfo([kg_rag_api.NodeScore("matkg:SAXS", 0.9)], [], ["saxs"])

    context = kg.build_context(node_info, include_structured=False, char_budget=3000, hint_terms=[])

    scipy_line = next(line for line in context.splitlines() if line.startswith("- SCIPY_DOCS.pdf"))
    xray_line = next(line for line in context.splitlines() if line.startswith("- XRAY1.pdf"))
    assert "SciPy Peak Finding Algorithms" in scipy_line
    assert "arXiv:2111.08645v1" not in scipy_line
    assert "Tomaszewski" not in scipy_line
    assert "arXiv:2111.08645v1" in xray_line
    assert "Paper_Title: Machine Learning-Assisted" not in context


def test_build_context_suppresses_scalar_metadata_across_multiple_sources(tmp_path, monkeypatch):
    # Legacy graph shape (no source_metadata) where a node aggregates two source
    # PDFs but carries scalar publication fields from only one of them. The
    # scalar fields must NOT be rendered, otherwise XRAY1's provenance would be
    # smeared onto the PYFAI_DOCS.pdf source.
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "")
    graph = {
        "things": [
            {
                "id": "matkg:silicon",
                "name": "silicon",
                "category": "ChemicalEntity",
                "description": "Calibrant for x-ray scattering geometry refinement.",
                "source_papers": ["XRAY1.pdf", "PYFAI_DOCS.pdf"],
                "paper_title": "Machine Learning-Assisted Analysis of Small Angle X-ray Scattering",
                "publication_year": 2021,
                "doi": "arXiv:2111.08645v1",
                "authors": ["Tomaszewski P", "Yu S"],
                "journal": "science as a way of examining nanostructures.",
            }
        ],
        "associations": [],
    }
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(graph))
    kg = kg_rag_api.KnowledgeGraph(str(graph_path))
    node_info = kg.build_nodeinfo([kg_rag_api.NodeScore("matkg:silicon", 0.9)], [], ["silicon"])

    context = kg.build_context(node_info, include_structured=False, char_budget=3000, hint_terms=[])

    assert "Source_Papers: XRAY1.pdf, PYFAI_DOCS.pdf" in context
    assert "Paper_Title:" not in context
    assert "arXiv:2111.08645v1" not in context
    assert "Tomaszewski" not in context
    assert "Authors:" not in context


def test_build_context_keeps_scalar_metadata_for_single_source(tmp_path, monkeypatch):
    # Single-source legacy node: scalar provenance is unambiguous and preserved.
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "")
    graph = {
        "things": [
            {
                "id": "matkg:massif",
                "name": "massif detection",
                "category": "ExperimentalTechnique",
                "description": "Peak-picking method on calibration rings.",
                "source_papers": ["PYFAI_DOCS.pdf"],
                "paper_title": "pyFAI Peak-Finding and Scattering Reduction Algorithms",
            }
        ],
        "associations": [],
    }
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(graph))
    kg = kg_rag_api.KnowledgeGraph(str(graph_path))
    node_info = kg.build_nodeinfo([kg_rag_api.NodeScore("matkg:massif", 0.9)], [], ["massif"])

    context = kg.build_context(node_info, include_structured=False, char_budget=3000, hint_terms=[])

    assert "Paper_Title: pyFAI Peak-Finding and Scattering Reduction Algorithms" in context


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


def test_fastapi_exposes_openwebui_model_discovery_and_openai_chat(tmp_path, monkeypatch):
    class FakeClient:
        model = "fake-cborg"

        async def chat(self, messages):
            assert messages[-1]["role"] == "user"
            return "grounded answer"

    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "")
    monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: FakeClient())
    graph_path = write_graph(tmp_path)
    app = kg_rag_api.create_fastapi_app(str(graph_path), backend="cborg")
    client = TestClient(app)

    tags = client.get("/api/tags")
    assert tags.status_code == 200
    assert tags.json()["models"][0]["name"] == "kg-rag:latest"

    version = client.get("/api/version")
    assert version.status_code == 200
    assert version.json()["version"]

    models = client.get("/v1/models")
    assert models.status_code == 200
    assert models.json()["data"][0]["id"] == "kg-rag:latest"

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "kg-rag:latest",
            "messages": [{"role": "user", "content": "Which papers mention P3HT?"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "grounded answer"


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

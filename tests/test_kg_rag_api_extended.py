"""
Extended unit tests for kg_rag_api.py — covers gaps identified by coverage analysis.

Tests 1–7:   KnowledgeGraph internals (cache, BFS, build_context, format_domain_features)
Tests 8–12:  FastAPI endpoints (/api/chat, /api/tags, /api/ps)
Tests 13–17: Chat clients (OllamaClient, CBorgClient error paths)
Tests 18–19: Retrieval helpers (stepwise, snippet_text fallback)
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.modules import kg_rag_api


# ---------------------------------------------------------------------------
# Shared graph fixture
# ---------------------------------------------------------------------------

def _write_graph(tmp_path, extra_nodes=None, extra_edges=None):
    graph = {
        "things": [
            {
                "id": "matkg:P3HT",
                "name": "P3HT",
                "category": "ConjugatedPolymer",
                "description": "Conjugated polymer for OPV.",
                "source_papers": ["paper.pdf"],
                "publication_year": 2024,
            },
            {
                "id": "matkg:OPV",
                "name": "Organic photovoltaic device",
                "category": "Device",
                "description": "Solar cell device.",
                "source_papers": [],
                "publication_year": 2023,
            },
            {
                "id": "matkg:PCE",
                "name": "Power Conversion Efficiency",
                "category": "Property",
                "description": "PCE metric for solar cells.",
                "source_papers": [],
            },
        ] + (extra_nodes or []),
        "associations": [
            {
                "subject": "matkg:P3HT",
                "predicate": "rel:has_application",
                "object": "matkg:OPV",
                "has_evidence": "p1",
            },
            {
                "subject": "matkg:OPV",
                "predicate": "rel:has_property",
                "object": "matkg:PCE",
                "has_evidence": "p2",
            },
        ] + (extra_edges or []),
    }
    p = tmp_path / "graph.json"
    p.write_text(json.dumps(graph))
    return p


# ---------------------------------------------------------------------------
# 1. _lexical_search cache hit — second call returns same object, no recompute
# ---------------------------------------------------------------------------

def test_lexical_search_cache_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    kg = kg_rag_api.KnowledgeGraph(str(_write_graph(tmp_path)))

    first = kg.semantic_search("P3HT polymer", topk=5)
    second = kg.semantic_search("P3HT polymer", topk=5)

    # same list object — cache was hit, not recomputed
    assert first is second


# ---------------------------------------------------------------------------
# 2. weighted_bfs multi-hop score attenuation
# ---------------------------------------------------------------------------

def test_weighted_bfs_multi_hop_score_attenuation(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    kg = kg_rag_api.KnowledgeGraph(str(_write_graph(tmp_path)))

    seeds = [kg_rag_api.NodeScore("matkg:P3HT", 1.0, depth=0)]
    results = kg.weighted_bfs(seeds, hops=2)

    by_id = {n.id: n for n in results}
    # seed itself is in results
    assert "matkg:P3HT" in by_id
    # OPV reached via hop-1 edge
    assert "matkg:OPV" in by_id
    # hop-1 node carries depth=1; seed carries depth=0
    assert by_id["matkg:P3HT"].depth == 0
    assert by_id["matkg:OPV"].depth == 1
    # PCE reached via hop-2 (OPV → PCE); depth=2 and score attenuated vs hop-1
    assert "matkg:PCE" in by_id
    assert by_id["matkg:PCE"].depth == 2
    assert by_id["matkg:PCE"].score < by_id["matkg:OPV"].score


# ---------------------------------------------------------------------------
# 3. weighted_bfs with empty seeds returns []
# ---------------------------------------------------------------------------

def test_weighted_bfs_empty_seeds(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    kg = kg_rag_api.KnowledgeGraph(str(_write_graph(tmp_path)))
    assert kg.weighted_bfs([], hops=3) == []


# ---------------------------------------------------------------------------
# 4. build_context respects char_budget — stops before exceeding limit
# ---------------------------------------------------------------------------

def test_build_context_respects_char_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "")
    kg = kg_rag_api.KnowledgeGraph(str(_write_graph(tmp_path)))

    nodes = kg.build_nodeinfo(
        [
            kg_rag_api.NodeScore("matkg:P3HT", 0.9),
            kg_rag_api.NodeScore("matkg:OPV", 0.8),
            kg_rag_api.NodeScore("matkg:PCE", 0.7),
        ],
        [],
        ["p3ht"],
    )

    # tiny budget — only first node should fit
    ctx = kg.build_context(nodes, include_structured=False, char_budget=80, hint_terms=[])
    assert len(ctx) <= 80 * 3   # some slack for section headers
    # with generous budget all nodes present
    ctx_full = kg.build_context(nodes, include_structured=False, char_budget=10_000, hint_terms=[])
    assert "P3HT" in ctx_full
    assert "Power Conversion Efficiency" in ctx_full


# ---------------------------------------------------------------------------
# 5. build_context skips CodeSnippet nodes with empty code_snippet body
# ---------------------------------------------------------------------------

def test_build_context_skips_empty_code_snippet(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "")

    graph = {
        "things": [
            {
                "id": "matkg:EmptySnip",
                "name": "empty snippet",
                "category": "CodeSnippet",
                "description": "No body.",
                "source_papers": [],
                "code_snippet": "",
                "function_name": "empty_fn",
                "code_language": "python",
            }
        ],
        "associations": [],
    }
    p = tmp_path / "g.json"
    p.write_text(json.dumps(graph))
    kg = kg_rag_api.KnowledgeGraph(str(p))

    nodes = kg.build_nodeinfo([kg_rag_api.NodeScore("matkg:EmptySnip", 1.0)], [], [])
    ctx = kg.build_context(nodes, include_structured=False, char_budget=5000, hint_terms=[])

    # node rendered but code block absent because body is empty
    assert "empty_fn" not in ctx


# ---------------------------------------------------------------------------
# 6. format_domain_features multiline=False returns single-line string
# ---------------------------------------------------------------------------

def test_format_domain_features_multiline_false():
    features = [
        {"feature_name": "q_range", "feature_value": "0.1-1.0", "feature_units": "A^-1"},
        {"feature_name": "technique", "feature_value": "SAXS"},
    ]
    result = kg_rag_api.format_domain_features(features, multiline=False)
    assert "\n" not in result
    assert "q_range: 0.1-1.0 A^-1" in result
    assert "technique: SAXS" in result


# ---------------------------------------------------------------------------
# 7. format_domain_features skips entries missing name or value
# ---------------------------------------------------------------------------

def test_format_domain_features_skips_incomplete_entries():
    features = [
        {"feature_name": "", "feature_value": "0.5"},        # empty name → skip
        {"feature_name": "q_range", "feature_value": ""},    # empty value → skip
        {"feature_name": "technique", "feature_value": "WAXS"},  # valid
    ]
    result = kg_rag_api.format_domain_features(features, multiline=False)
    assert "technique: WAXS" in result
    assert "q_range" not in result


# ---------------------------------------------------------------------------
# 8. /api/chat returns valid response when LLM mocked successfully
# ---------------------------------------------------------------------------

def test_fastapi_chat_returns_rag_response(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "")

    fake_cli = MagicMock()
    fake_cli.model = "fake-model"
    fake_cli.chat = AsyncMock(return_value="P3HT is used in OPV devices.")
    monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: fake_cli)

    app = kg_rag_api.create_fastapi_app(str(_write_graph(tmp_path)), backend="ollama")
    client = TestClient(app)

    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "What is P3HT?"}]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["message"]["role"] == "assistant"
    assert body["message"]["content"] == "P3HT is used in OPV devices."
    assert body["model"] == "fake-model"
    assert body["done"] is True


# ---------------------------------------------------------------------------
# 9. /api/chat logs MissingNode when all retrieved nodes have zero evidence
# ---------------------------------------------------------------------------

def test_fastapi_chat_logs_missing_node_on_zero_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "")

    # graph with a node that has no source_papers and no edges → evidence_ct == 0
    graph = {
        "things": [
            {
                "id": "matkg:Orphan",
                "name": "orphan node",
                "category": "Material",
                "description": "No evidence.",
                "source_papers": [],
            }
        ],
        "associations": [],
    }
    p = tmp_path / "sparse.json"
    p.write_text(json.dumps(graph))

    fake_cli = MagicMock()
    fake_cli.model = "m"
    fake_cli.chat = AsyncMock(return_value="answer")
    monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: fake_cli)

    logged = []

    app = kg_rag_api.create_fastapi_app(str(p), backend="ollama")

    # Patch tracker.log on the app's gap_tracker after creation by patching MissingNodeTracker.log
    original_log = kg_rag_api.MissingNodeTracker.log

    def capturing_log(self, node):
        logged.append(node)

    monkeypatch.setattr(kg_rag_api.MissingNodeTracker, "log", capturing_log)

    client = TestClient(app)
    client.post("/api/chat", json={"messages": [{"role": "user", "content": "orphan query"}]})

    assert any(n.reason == "no evidence in KG" for n in logged)


# ---------------------------------------------------------------------------
# 10. /api/chat logs [Domain Knowledge] fallback when LLM response contains it
# ---------------------------------------------------------------------------

def test_fastapi_chat_logs_domain_knowledge_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "")

    fake_cli = MagicMock()
    fake_cli.model = "m"
    fake_cli.chat = AsyncMock(
        return_value="Some answer.\n[Domain Knowledge] extra entity\nMore text."
    )
    monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: fake_cli)

    logged = []

    def capturing_log(self, node):
        logged.append(node)

    monkeypatch.setattr(kg_rag_api.MissingNodeTracker, "log", capturing_log)

    app = kg_rag_api.create_fastapi_app(str(_write_graph(tmp_path)), backend="ollama")
    TestClient(app).post(
        "/api/chat", json={"messages": [{"role": "user", "content": "Tell me about P3HT"}]}
    )

    assert any(n.reason == "llm_fallback" for n in logged)
    assert any(n.entity == "extra entity" for n in logged)


# ---------------------------------------------------------------------------
# 11. /api/tags returns kg-rag:latest model entry
# ---------------------------------------------------------------------------

def test_fastapi_tags_returns_kg_rag_model(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    fake_cli = MagicMock()
    fake_cli.model = "m"
    monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: fake_cli)

    app = kg_rag_api.create_fastapi_app(str(_write_graph(tmp_path)), backend="ollama")
    resp = TestClient(app).get("/api/tags")

    assert resp.status_code == 200
    names = [m["name"] for m in resp.json()["models"]]
    assert "kg-rag:latest" in names


# ---------------------------------------------------------------------------
# 12. /api/ps returns empty processes list
# ---------------------------------------------------------------------------

def test_fastapi_ps_returns_empty_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    fake_cli = MagicMock()
    fake_cli.model = "m"
    monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: fake_cli)

    app = kg_rag_api.create_fastapi_app(str(_write_graph(tmp_path)), backend="ollama")
    resp = TestClient(app).get("/api/ps")

    assert resp.status_code == 200
    assert resp.json() == {"processes": []}


# ---------------------------------------------------------------------------
# 13. OllamaClient.chat returns assistant content from mocked HTTP response
# ---------------------------------------------------------------------------

def test_ollama_client_chat_returns_content(monkeypatch):
    import aiohttp

    async def fake_post(self_sess, url, json=None, **kwargs):
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "message": {"role": "assistant", "content": "hello from ollama"}
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        return mock_resp

    with patch("aiohttp.ClientSession.post", fake_post):
        client = kg_rag_api.OllamaClient(url="http://localhost:11434/api/chat", model="test-model")
        result = asyncio.run(client.chat([{"role": "user", "content": "hi"}]))

    assert result == "hello from ollama"


# ---------------------------------------------------------------------------
# 14. OllamaClient.chat propagates HTTP error on non-200 response
# ---------------------------------------------------------------------------

def test_ollama_client_chat_raises_on_http_error(monkeypatch):
    import aiohttp

    async def fake_post(self_sess, url, json=None, **kwargs):
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=500
            )
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        return mock_resp

    with patch("aiohttp.ClientSession.post", fake_post):
        client = kg_rag_api.OllamaClient(url="http://localhost:11434/api/chat", model="test-model")
        with pytest.raises(Exception):
            asyncio.run(client.chat([{"role": "user", "content": "hi"}]))


# ---------------------------------------------------------------------------
# 15. CBorgClient.chat raises RuntimeError on APIConnectionError
# ---------------------------------------------------------------------------

def test_cborg_client_raises_on_connection_error(monkeypatch):
    import openai

    async def fake_create(**kwargs):
        raise openai.APIConnectionError(request=MagicMock())

    client = kg_rag_api.CBorgClient(model="lbl/test", api_key="fake-key")
    client.client.chat.completions.create = fake_create

    with pytest.raises(RuntimeError, match="CBORG connection failed"):
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))


# ---------------------------------------------------------------------------
# 16. CBorgClient.chat raises RuntimeError on APITimeoutError
# ---------------------------------------------------------------------------

def test_cborg_client_raises_on_timeout_error(monkeypatch):
    import openai

    async def fake_create(**kwargs):
        raise openai.APITimeoutError(request=MagicMock())

    client = kg_rag_api.CBorgClient(model="lbl/test", api_key="fake-key")
    client.client.chat.completions.create = fake_create

    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))


# ---------------------------------------------------------------------------
# 17. CBorgClient.chat raises RuntimeError on AuthenticationError
# ---------------------------------------------------------------------------

def test_cborg_client_raises_on_auth_error(monkeypatch):
    import openai

    async def fake_create(**kwargs):
        raise openai.AuthenticationError(
            message="bad key", response=MagicMock(), body={}
        )

    client = kg_rag_api.CBorgClient(model="lbl/test", api_key="fake-key")
    client.client.chat.completions.create = fake_create

    with pytest.raises(RuntimeError, match="authentication failed"):
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))


# ---------------------------------------------------------------------------
# 18. retrieve_nodes with STEPWISE expands seed set via decompose sub-questions
# ---------------------------------------------------------------------------

def test_retrieve_nodes_stepwise_expands_seeds(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "STEPWISE", True)
    monkeypatch.setattr(kg_rag_api, "STEPWISE_MAX_STEPS", 4)
    monkeypatch.setattr(kg_rag_api, "ENABLE_BFS", False)
    monkeypatch.setattr(kg_rag_api, "DEFAULT_K", 10)

    kg = kg_rag_api.KnowledgeGraph(str(_write_graph(tmp_path)))

    # Compound query — decompose splits on "and", giving sub-queries that
    # separately hit P3HT and OPV
    results = kg_rag_api.retrieve_nodes(
        "What is P3HT and what is the power conversion efficiency?", kg
    )
    ids = {r.id for r in results}
    assert "matkg:P3HT" in ids
    assert "matkg:PCE" in ids


# ---------------------------------------------------------------------------
# 19. snippet_text with no hint match falls back to txt[:length]
# ---------------------------------------------------------------------------

def test_snippet_text_no_hint_match_falls_back_to_start():
    text = "alpha beta gamma delta epsilon zeta"
    result = kg_rag_api.snippet_text(text, 11, ["zzznomatch"])
    assert result == text[:11]


# ---------------------------------------------------------------------------
# 20. STEPWISE=True + ENABLE_BFS=True combined path
#     Sub-question decomposition expands seeds; BFS then expands each seed's
#     neighbourhood. All three nodes (P3HT, OPV, PCE) must appear and
#     BFS-reached nodes must carry depth > 0.
# ---------------------------------------------------------------------------

def test_stepwise_and_bfs_combined(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "STEPWISE", True)
    monkeypatch.setattr(kg_rag_api, "STEPWISE_MAX_STEPS", 4)
    monkeypatch.setattr(kg_rag_api, "ENABLE_BFS", True)
    monkeypatch.setattr(kg_rag_api, "DEFAULT_K", 10)

    kg = kg_rag_api.KnowledgeGraph(str(_write_graph(tmp_path)))

    # Compound query — decompose splits on "and", giving sub-queries that
    # separately surface P3HT and PCE as seeds; BFS then hops to OPV.
    results = kg_rag_api.retrieve_nodes(
        "What is P3HT and what is the power conversion efficiency?", kg
    )
    ids = {r.id for r in results}

    # All three nodes reachable
    assert "matkg:P3HT" in ids
    assert "matkg:PCE" in ids
    assert "matkg:OPV" in ids

    # Nodes that were only reached via BFS carry depth > 0
    by_id = {r.id: r for r in results}
    bfs_only = [r for r in results if r.depth > 0]
    assert len(bfs_only) >= 1, "Expected at least one node expanded via BFS hops"


# ---------------------------------------------------------------------------
# 21. KnowledgeGraph constructor raises JSONDecodeError on malformed graph file
# ---------------------------------------------------------------------------

def test_knowledge_graph_raises_on_malformed_json(tmp_path, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json!!!", encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        kg_rag_api.KnowledgeGraph(str(bad))

    # Must be a JSON decode error, not a silent partial load
    assert "json" in type(exc_info.value).__name__.lower() or \
           "decode" in str(exc_info.value).lower() or \
           "json" in str(exc_info.value).lower()

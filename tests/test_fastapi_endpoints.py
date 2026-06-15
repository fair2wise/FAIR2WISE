"""
Unit tests for FastAPI endpoints in kg_rag_api.py:
  - POST /api/chat
  - POST /v1/chat/completions (OpenAI-compatible)
  - GET  /api/tags
  - GET  /api/version
  - GET  /v1/models
  - GET  /api/ps

Covers: success paths, error paths, gap tracking, response shapes.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.modules import kg_rag_api


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _write_graph(tmp_path):
    graph = {
        "things": [
            {
                "id": "matkg:P3HT",
                "name": "P3HT",
                "category": "ConjugatedPolymer",
                "description": "Conjugated polymer for OPV.",
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
    p = tmp_path / "graph.json"
    p.write_text(json.dumps(graph))
    return p


def _make_app(tmp_path, monkeypatch, *, llm_response="grounded answer"):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: "")
    fake_cli = MagicMock()
    fake_cli.model = "fake-model"
    fake_cli.chat = AsyncMock(return_value=llm_response)
    monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: fake_cli)
    app = kg_rag_api.create_fastapi_app(str(_write_graph(tmp_path)), backend="ollama")
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/chat
# ─────────────────────────────────────────────────────────────────────────────


class TestApiChat:
    def test_returns_assistant_response(self, tmp_path, monkeypatch):
        client = _make_app(tmp_path, monkeypatch)
        resp = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "What is P3HT?"}],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["message"]["role"] == "assistant"
        assert body["message"]["content"] == "grounded answer"
        assert body["done"] is True

    def test_empty_messages_returns_400(self, tmp_path, monkeypatch):
        client = _make_app(tmp_path, monkeypatch)
        resp = client.post("/api/chat", json={"messages": []})
        assert resp.status_code == 400
        assert resp.json() == {"error": "No messages"}

    def test_model_field_matches_client_model(self, tmp_path, monkeypatch):
        client = _make_app(tmp_path, monkeypatch)
        resp = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "test"}],
        })
        assert resp.json()["model"] == "fake-model"


# ─────────────────────────────────────────────────────────────────────────────
# POST /v1/chat/completions
# ─────────────────────────────────────────────────────────────────────────────


class TestOpenAIChatCompletions:
    def test_returns_openai_shaped_response(self, tmp_path, monkeypatch):
        client = _make_app(tmp_path, monkeypatch)
        resp = client.post("/v1/chat/completions", json={
            "model": "kg-rag:latest",
            "messages": [{"role": "user", "content": "What is P3HT?"}],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "chat.completion"
        assert body["model"] == "kg-rag:latest"
        assert body["choices"][0]["message"]["content"] == "grounded answer"
        assert body["choices"][0]["finish_reason"] == "stop"

    def test_empty_messages_returns_400(self, tmp_path, monkeypatch):
        client = _make_app(tmp_path, monkeypatch)
        resp = client.post("/v1/chat/completions", json={
            "model": "kg-rag:latest",
            "messages": [],
        })
        assert resp.status_code == 400

    def test_has_id_and_created_fields(self, tmp_path, monkeypatch):
        client = _make_app(tmp_path, monkeypatch)
        resp = client.post("/v1/chat/completions", json={
            "model": "kg-rag:latest",
            "messages": [{"role": "user", "content": "test"}],
        })
        body = resp.json()
        assert body["id"].startswith("chatcmpl-")
        assert isinstance(body["created"], int)


# ─────────────────────────────────────────────────────────────────────────────
# GET endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestGetEndpoints:
    def test_tags_returns_model_list(self, tmp_path, monkeypatch):
        client = _make_app(tmp_path, monkeypatch)
        resp = client.get("/api/tags")
        assert resp.status_code == 200
        names = [m["name"] for m in resp.json()["models"]]
        assert "kg-rag:latest" in names

    def test_version_returns_version_string(self, tmp_path, monkeypatch):
        client = _make_app(tmp_path, monkeypatch)
        resp = client.get("/api/version")
        assert resp.status_code == 200
        assert "version" in resp.json()

    def test_v1_models_returns_openai_format(self, tmp_path, monkeypatch):
        client = _make_app(tmp_path, monkeypatch)
        resp = client.get("/v1/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "list"
        assert body["data"][0]["id"] == "kg-rag:latest"
        assert body["data"][0]["object"] == "model"

    def test_ps_returns_empty_processes(self, tmp_path, monkeypatch):
        client = _make_app(tmp_path, monkeypatch)
        resp = client.get("/api/ps")
        assert resp.status_code == 200
        assert resp.json() == {"processes": []}


# ─────────────────────────────────────────────────────────────────────────────
# Gap tracking
# ─────────────────────────────────────────────────────────────────────────────


class TestGapTracking:
    def test_logs_missing_node_on_zero_evidence(self, tmp_path, monkeypatch):
        """When all retrieved nodes have zero evidence, a gap is logged."""
        graph = {
            "things": [{
                "id": "matkg:Orphan",
                "name": "orphan node",
                "category": "Material",
                "description": "No evidence.",
                "source_papers": [],
            }],
            "associations": [],
        }
        p = tmp_path / "sparse.json"
        p.write_text(json.dumps(graph))

        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
        monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda _: "")
        fake_cli = MagicMock()
        fake_cli.model = "m"
        fake_cli.chat = AsyncMock(return_value="answer")
        monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: fake_cli)

        logged = []
        monkeypatch.setattr(
            kg_rag_api.MissingNodeTracker, "log",
            lambda self, node: logged.append(node),
        )

        app = kg_rag_api.create_fastapi_app(str(p), backend="ollama")
        TestClient(app).post("/api/chat", json={
            "messages": [{"role": "user", "content": "orphan query"}],
        })

        assert any(n.reason == "no evidence in KG" for n in logged)

    def test_logs_domain_knowledge_fallback(self, tmp_path, monkeypatch):
        """[Domain Knowledge] in LLM response triggers gap logging."""
        client = _make_app(
            tmp_path, monkeypatch,
            llm_response="Answer.\n[Domain Knowledge] extra info\nMore.",
        )

        logged = []
        monkeypatch.setattr(
            kg_rag_api.MissingNodeTracker, "log",
            lambda self, node: logged.append(node),
        )

        client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "What is P3HT?"}],
        })

        assert any(n.reason == "llm_fallback" for n in logged)

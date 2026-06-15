"""
Unit tests for the splash-links fallback path in KnowledgeGraph.__init__().

When splash-links server is unreachable, the KG should gracefully fall back
to loading from the JSON file.

Covers:
  - Splash connection failure → JSON fallback
  - Splash GraphQL error → JSON fallback
  - Splash success (no fallback)
  - Unknown graph source raises ValueError
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.modules import kg_rag_api


def _write_json_graph(tmp_path):
    graph = {
        "things": [
            {
                "id": "matkg:Fallback",
                "name": "Fallback Node",
                "category": "Material",
                "description": "Loaded from JSON fallback.",
                "source_papers": [],
            }
        ],
        "associations": [],
    }
    p = tmp_path / "graph.json"
    p.write_text(json.dumps(graph))
    return p


class TestSplashFallbackToJson:
    def test_falls_back_on_connection_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "GRAPH_SOURCE", "splash")
        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
        monkeypatch.setattr(kg_rag_api, "SPLASH_LINKS_URI", "splash://localhost:9999")

        # Make _load_splash_links_graph raise
        monkeypatch.setattr(
            kg_rag_api, "_load_splash_links_graph",
            MagicMock(side_effect=ConnectionError("Connection refused")),
        )

        graph_path = _write_json_graph(tmp_path)
        kg = kg_rag_api.KnowledgeGraph(str(graph_path))

        assert "matkg:Fallback" in kg.nodes
        assert kg.nodes["matkg:Fallback"]["name"] == "Fallback Node"

    def test_falls_back_on_runtime_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "GRAPH_SOURCE", "splash")
        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
        monkeypatch.setattr(kg_rag_api, "SPLASH_LINKS_URI", "splash://localhost:9999")

        monkeypatch.setattr(
            kg_rag_api, "_load_splash_links_graph",
            MagicMock(side_effect=RuntimeError("GraphQL error")),
        )

        graph_path = _write_json_graph(tmp_path)
        kg = kg_rag_api.KnowledgeGraph(str(graph_path))

        assert "matkg:Fallback" in kg.nodes

    def test_no_fallback_on_splash_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "GRAPH_SOURCE", "splash")
        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
        monkeypatch.setattr(kg_rag_api, "SPLASH_LINKS_URI", "splash://localhost:8081")

        splash_data = {
            "things": [{
                "id": "matkg:SplashNode",
                "name": "From Splash",
                "category": "Material",
                "description": "Loaded from splash.",
                "source_papers": [],
            }],
            "associations": [],
        }
        monkeypatch.setattr(
            kg_rag_api, "_load_splash_links_graph",
            MagicMock(return_value=splash_data),
        )

        graph_path = _write_json_graph(tmp_path)
        kg = kg_rag_api.KnowledgeGraph(str(graph_path))

        # Should have splash data, not JSON fallback
        assert "matkg:SplashNode" in kg.nodes
        assert "matkg:Fallback" not in kg.nodes

    def test_splash_links_alias_accepted(self, tmp_path, monkeypatch):
        """GRAPH_SOURCE='splash_links' and 'splash-links' should both work."""
        for source in ("splash_links", "splash-links"):
            monkeypatch.setattr(kg_rag_api, "GRAPH_SOURCE", source)
            monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")

            splash_data = {
                "things": [{
                    "id": "matkg:X", "name": "X", "category": "Material",
                    "description": "", "source_papers": [],
                }],
                "associations": [],
            }
            monkeypatch.setattr(
                kg_rag_api, "_load_splash_links_graph",
                MagicMock(return_value=splash_data),
            )

            kg = kg_rag_api.KnowledgeGraph(str(_write_json_graph(tmp_path)))
            assert "matkg:X" in kg.nodes


class TestUnknownGraphSource:
    def test_raises_value_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "GRAPH_SOURCE", "unknown_source")
        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")

        with pytest.raises(ValueError, match="Unknown KG_RAG_GRAPH_SOURCE"):
            kg_rag_api.KnowledgeGraph(str(_write_json_graph(tmp_path)))

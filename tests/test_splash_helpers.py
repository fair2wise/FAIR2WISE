"""
Unit tests for splash-links helper functions:
  - _splash_base_url
  - _splash_graphql
  - _splash_node_id
  - _splash_entity_to_node
  - _load_splash_links_graph

Covers: main paths, edge cases, error branches.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.modules import kg_rag_api


# ─────────────────────────────────────────────────────────────────────────────
# _splash_base_url
# ─────────────────────────────────────────────────────────────────────────────


class TestSplashBaseUrl:
    def test_splash_scheme_converts_to_http(self):
        assert kg_rag_api._splash_base_url("splash://myhost:8081") == "http://myhost:8081"

    def test_http_scheme_passthrough(self):
        assert kg_rag_api._splash_base_url("http://localhost:8081") == "http://localhost:8081"

    def test_https_scheme_passthrough(self):
        assert kg_rag_api._splash_base_url("https://secure.host:443") == "https://secure.host:443"

    def test_trailing_slash_stripped(self):
        assert kg_rag_api._splash_base_url("splash://host:8081/") == "http://host:8081"

    def test_unsupported_scheme_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported splash-links URI scheme"):
            kg_rag_api._splash_base_url("ftp://host:21")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            kg_rag_api._splash_base_url("")

    def test_no_port(self):
        assert kg_rag_api._splash_base_url("splash://myhost") == "http://myhost"


# ─────────────────────────────────────────────────────────────────────────────
# _splash_graphql
# ─────────────────────────────────────────────────────────────────────────────


class TestSplashGraphql:
    def test_success_returns_data_payload(self, monkeypatch):
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {"data": {"entities": [{"id": "1"}]}}
        monkeypatch.setattr(kg_rag_api.requests, "post", lambda url, json, timeout: fake_resp)

        result = kg_rag_api._splash_graphql("splash://host:8081", "{ entities { id } }")
        assert result == {"entities": [{"id": "1"}]}

    def test_posts_to_correct_url(self, monkeypatch):
        captured = {}

        def capture_post(url, json, timeout):
            captured["url"] = url
            captured["json"] = json
            captured["timeout"] = timeout
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"data": {}}
            return resp

        monkeypatch.setattr(kg_rag_api.requests, "post", capture_post)
        kg_rag_api._splash_graphql("http://myhost:9090", "query Q { x }", {"limit": 10})

        assert captured["url"] == "http://myhost:9090/splash_links/graphql"
        assert captured["json"]["query"] == "query Q { x }"
        assert captured["json"]["variables"] == {"limit": 10}
        assert captured["timeout"] == 30

    def test_http_error_propagates(self, monkeypatch):
        fake_resp = MagicMock()
        fake_resp.raise_for_status.side_effect = Exception("500 Server Error")
        monkeypatch.setattr(kg_rag_api.requests, "post", lambda url, json, timeout: fake_resp)

        with pytest.raises(Exception, match="500 Server Error"):
            kg_rag_api._splash_graphql("splash://host:8081", "{ x }")

    def test_graphql_errors_raise_runtime_error(self, monkeypatch):
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {
            "data": None,
            "errors": [{"message": "field not found"}],
        }
        monkeypatch.setattr(kg_rag_api.requests, "post", lambda url, json, timeout: fake_resp)

        with pytest.raises(RuntimeError, match="splash-links GraphQL error"):
            kg_rag_api._splash_graphql("splash://host:8081", "{ bad }")

    def test_no_variables_sends_empty_dict(self, monkeypatch):
        captured = {}

        def capture_post(url, json, timeout):
            captured["variables"] = json.get("variables")
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"data": {}}
            return resp

        monkeypatch.setattr(kg_rag_api.requests, "post", capture_post)
        kg_rag_api._splash_graphql("splash://host:8081", "{ x }")
        assert captured["variables"] == {}


# ─────────────────────────────────────────────────────────────────────────────
# _splash_node_id
# ─────────────────────────────────────────────────────────────────────────────


class TestSplashNodeId:
    def test_prefers_uri(self):
        entity = {"id": "uuid-1", "uri": "matkg:P3HT", "properties": {"matkg_id": "matkg:alt"}}
        assert kg_rag_api._splash_node_id(entity) == "matkg:P3HT"

    def test_falls_back_to_matkg_id_in_properties(self):
        entity = {"id": "uuid-1", "properties": {"matkg_id": "matkg:fromprops"}}
        assert kg_rag_api._splash_node_id(entity) == "matkg:fromprops"

    def test_falls_back_to_uuid_id(self):
        entity = {"id": "uuid-1", "properties": {}}
        assert kg_rag_api._splash_node_id(entity) == "uuid-1"

    def test_no_properties_key(self):
        entity = {"id": "uuid-1"}
        assert kg_rag_api._splash_node_id(entity) == "uuid-1"

    def test_none_properties(self):
        entity = {"id": "uuid-1", "properties": None}
        assert kg_rag_api._splash_node_id(entity) == "uuid-1"

    def test_empty_uri_falls_through(self):
        entity = {"id": "uuid-1", "uri": "", "properties": {"matkg_id": "matkg:fallback"}}
        # Empty string is falsy, so should fall to matkg_id
        assert kg_rag_api._splash_node_id(entity) == "matkg:fallback"


# ─────────────────────────────────────────────────────────────────────────────
# _splash_entity_to_node
# ─────────────────────────────────────────────────────────────────────────────


class TestSplashEntityToNode:
    def test_basic_conversion(self):
        entity = {
            "id": "uuid-1",
            "name": "P3HT",
            "entityType": "ConjugatedPolymer",
            "uri": "matkg:P3HT",
            "properties": {
                "description": "A polymer.",
                "source_papers": ["paper.pdf"],
                "code_snippet": "def f(): pass",
            },
        }
        node = kg_rag_api._splash_entity_to_node(entity)
        assert node["id"] == "matkg:P3HT"
        assert node["name"] == "P3HT"
        assert node["category"] == "ConjugatedPolymer"
        assert node["description"] == "A polymer."
        assert node["source_papers"] == ["paper.pdf"]
        assert node["code_snippet"] == "def f(): pass"

    def test_spreads_all_properties(self):
        entity = {
            "id": "uuid-1",
            "name": "snip",
            "entityType": "CodeSnippet",
            "properties": {
                "description": "desc",
                "function_name": "my_func",
                "code_language": "python",
                "code_snippet": "x = 1",
                "domain_features": [{"feature_name": "a"}],
            },
        }
        node = kg_rag_api._splash_entity_to_node(entity)
        assert node["function_name"] == "my_func"
        assert node["code_language"] == "python"
        assert node["code_snippet"] == "x = 1"
        assert node["domain_features"] == [{"feature_name": "a"}]

    def test_missing_name_uses_node_id(self):
        entity = {"id": "uuid-1", "properties": {}}
        node = kg_rag_api._splash_entity_to_node(entity)
        assert node["name"] == "uuid-1"

    def test_missing_entity_type_falls_back_to_properties_category(self):
        entity = {"id": "uuid-1", "name": "X", "properties": {"category": "Material"}}
        node = kg_rag_api._splash_entity_to_node(entity)
        assert node["category"] == "Material"

    def test_missing_entity_type_and_category_defaults_to_thing(self):
        entity = {"id": "uuid-1", "name": "X", "properties": {}}
        node = kg_rag_api._splash_entity_to_node(entity)
        assert node["category"] == "Thing"

    def test_adds_empty_description_if_missing(self):
        entity = {"id": "uuid-1", "name": "X", "entityType": "Material", "properties": {}}
        node = kg_rag_api._splash_entity_to_node(entity)
        assert node["description"] == ""

    def test_none_properties_handled(self):
        entity = {"id": "uuid-1", "name": "X", "entityType": "Material", "properties": None}
        node = kg_rag_api._splash_entity_to_node(entity)
        assert node["id"] == "uuid-1"
        assert node["description"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# _load_splash_links_graph
# ─────────────────────────────────────────────────────────────────────────────


class TestLoadSplashLinksGraph:
    def _fake_post_factory(self, entities, links):
        """Build a fake requests.post that returns entities then links with pagination."""
        call_count = {"entities": 0, "links": 0}

        def fake_post(url, json, timeout):
            query = json["query"]
            offset = json["variables"].get("offset", 0)
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if "entities" in query:
                batch = entities[offset:] if call_count["entities"] == 0 else []
                call_count["entities"] += 1
                resp.json.return_value = {"data": {"entities": batch}}
            else:
                batch = links[offset:] if call_count["links"] == 0 else []
                call_count["links"] += 1
                resp.json.return_value = {"data": {"links": batch}}
            return resp

        return fake_post

    def test_loads_entities_and_links(self, monkeypatch):
        entities = [
            {"id": "u1", "entityType": "Material", "name": "A", "uri": "matkg:A",
             "properties": {"description": "mat A"}},
            {"id": "u2", "entityType": "Device", "name": "B", "uri": "matkg:B",
             "properties": {"description": "dev B"}},
        ]
        links = [
            {"id": "l1", "subjectId": "u1", "predicate": "rel:uses", "objectId": "u2",
             "properties": {"has_evidence": "p1"}},
        ]
        monkeypatch.setattr(
            kg_rag_api.requests, "post",
            self._fake_post_factory(entities, links),
        )
        monkeypatch.setattr(kg_rag_api, "SPLASH_LINKS_PAGE_SIZE", 1000)

        data = kg_rag_api._load_splash_links_graph("splash://localhost:8081")

        assert len(data["things"]) == 2
        assert len(data["associations"]) == 1
        assert data["associations"][0]["subject"] == "matkg:A"
        assert data["associations"][0]["object"] == "matkg:B"

    def test_skips_links_with_unknown_entity_ids(self, monkeypatch):
        entities = [
            {"id": "u1", "entityType": "Material", "name": "A", "uri": "matkg:A",
             "properties": {}},
        ]
        links = [
            {"id": "l1", "subjectId": "u1", "predicate": "rel:x", "objectId": "unknown-uuid",
             "properties": {}},
        ]
        monkeypatch.setattr(
            kg_rag_api.requests, "post",
            self._fake_post_factory(entities, links),
        )
        monkeypatch.setattr(kg_rag_api, "SPLASH_LINKS_PAGE_SIZE", 1000)

        data = kg_rag_api._load_splash_links_graph("splash://localhost:8081")
        assert len(data["associations"]) == 0

    def test_empty_graph(self, monkeypatch):
        monkeypatch.setattr(
            kg_rag_api.requests, "post",
            self._fake_post_factory([], []),
        )
        monkeypatch.setattr(kg_rag_api, "SPLASH_LINKS_PAGE_SIZE", 1000)

        data = kg_rag_api._load_splash_links_graph("splash://localhost:8081")
        assert data == {"things": [], "associations": []}

    def test_link_without_predicate_gets_default(self, monkeypatch):
        entities = [
            {"id": "u1", "entityType": "Material", "name": "A", "uri": "matkg:A", "properties": {}},
            {"id": "u2", "entityType": "Material", "name": "B", "uri": "matkg:B", "properties": {}},
        ]
        links = [
            {"id": "l1", "subjectId": "u1", "objectId": "u2", "properties": {}},
        ]
        monkeypatch.setattr(
            kg_rag_api.requests, "post",
            self._fake_post_factory(entities, links),
        )
        monkeypatch.setattr(kg_rag_api, "SPLASH_LINKS_PAGE_SIZE", 1000)

        data = kg_rag_api._load_splash_links_graph("splash://localhost:8081")
        assert data["associations"][0]["predicate"] == "rel:related_to"

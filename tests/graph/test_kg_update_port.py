"""T17B (H8): ``graph.kg_update``, the real port of ``f2w_agent/kg_update.py``.

The Splash/GraphQL behaviour itself is pinned by ``tests/coverage_t27/test_kg_update_graphql.py``;
this file pins the port: the new home, the legacy alias (patches at either path reach the callers),
import without side effects and the ``publish_state`` mapping the UI contract reads.
"""
from __future__ import annotations

import importlib
import sys
from urllib import request

import pytest

from app.modules.graph import kg_update


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*a, **k):  # pragma: no cover - guards against accidental network use
        raise AssertionError("network call attempted")

    monkeypatch.setattr(request, "urlopen", refuse)


def test_legacy_path_is_an_alias_of_the_new_module():
    legacy = importlib.import_module("app.modules.f2w_agent.kg_update")
    assert legacy is kg_update
    assert sys.modules["app.modules.f2w_agent.kg_update"] is sys.modules["app.modules.graph.kg_update"]
    for name in ("rebuild_kg", "publish_extraction", "load_splash_graph", "export_splash_graph_to_json",
                 "splash_find_entity_by_matkg_id", "splash_update_entity", "splash_create_entity",
                 "splash_create_link", "splash_find_links", "splash_delete_link", "splash_uri_default"):
        assert callable(getattr(kg_update, name))
    assert not hasattr(kg_update, "splash_reimport")  # the wipe-and-reimport stays gone (Q11)


def test_callers_share_the_module_so_a_patch_at_the_legacy_path_reaches_them(monkeypatch):
    from app.modules.f2w_agent import api as api_mod
    from app.modules.f2w_agent import coordinator as coord_mod
    from app.modules.f2w_agent import kg_update as legacy

    assert api_mod.kg_update is kg_update and coord_mod.kg_update is kg_update
    monkeypatch.setattr(legacy, "rebuild_kg", lambda t, k: {"status": "success", "nodes": 1, "edges": 2})
    assert coord_mod.kg_update.rebuild_kg("t", "k")["edges"] == 2


def test_import_reads_the_splash_uri_at_call_time(monkeypatch):
    monkeypatch.delenv("KG_RAG_SPLASH_URI", raising=False)
    monkeypatch.delenv("SPLASH_LINKS_URI", raising=False)
    assert kg_update.splash_uri_default() == "splash://localhost:8081"
    monkeypatch.setenv("SPLASH_LINKS_URI", "http://127.0.0.1:9")
    assert kg_update.splash_uri_default() == "http://127.0.0.1:9"


def test_publish_extraction_states():
    skipped = kg_update.publish_extraction("terms.json")
    assert skipped["status"] == "skipped" and skipped["publish_state"] == kg_update.PUBLISH_STATE_JSON_ONLY
    assert skipped["indicator"] == kg_update.JSON_ONLY_INDICATOR

    class Publisher:
        def __init__(self, status):
            self.status, self.calls = status, []

        def publish(self, ctx, terms_json):
            self.calls.append((ctx, terms_json))
            return {"status": self.status, "job_id": "j1"}

    for status, state in kg_update.PUBLISH_STATE_BY_STATUS.items():
        pub = Publisher(status)
        out = kg_update.publish_extraction("terms.json", publisher=pub, ctx=None)
        assert out["status"] == status and out["publish_state"] == state and out["job_id"] == "j1"
        assert pub.calls == [(None, "terms.json")]
    unknown = kg_update.publish_extraction("t", publisher=Publisher("weird"))
    assert "publish_state" not in unknown  # an unmapped status is passed through untouched

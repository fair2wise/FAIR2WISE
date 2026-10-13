"""T25D: per-user settings on ALS, unchanged server settings on ``:local``.

Runs the real legacy UI app with the T18A fakes and the T25B fake Tiled identity port
(as tests/api_wiring). No network, model or Tiled server.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO / "tests" / "api_wiring") not in sys.path:
    sys.path.insert(0, str(REPO / "tests" / "api_wiring"))

import _wiring_support as W  # noqa: E402
from _wiring_support import H, Deployment, make_cfg  # noqa: E402

from app.api.legacy_ui import SETTING_WARNING_HEADER  # noqa: E402
from app.modules.security.authorization import (  # noqa: E402
    SETTINGS_DEPLOYMENT_FIELDS, SETTINGS_USER_FIELDS, settings_update_plan)

ASK = "What is Term A?"


def _record_retrieval(monkeypatch, seen: List[Dict[str, Any]]) -> None:
    """Wrap the harness's fake RetrievalAgent so each query records the settings it was built with."""
    from app.modules.f2w_agent import api as api_mod

    base = api_mod.RetrievalAgent

    class Recording(base):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            names = ("backend", "model", "kg_query_hops", "kg_query_max_nodes", "source_rag", "live_tiled",
                     "graph_source")
            self._built = {k: kwargs.get(k) for k in names}

        async def query(self, question, *args, **kwargs):
            seen.append({**self._built, "source_rag_arg": kwargs.get("source_rag")})
            return await super().query(question, *args, **kwargs)

    monkeypatch.setattr(api_mod, "RetrievalAgent", Recording)


def _build_als(monkeypatch, tmp_path, seen: List[Dict[str, Any]]) -> Deployment:
    """``W.build_als`` with the recording retrieval agent."""
    from app.modules.f2w_agent import api as api_mod

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    _record_retrieval(monkeypatch, seen)
    port = W.FakeTiledIdentityPort(tiled_target=W.STAGING)
    store = W.InMemoryCredentialStore()
    for who, user in (("user", W.plain_user()), ("user2", W.other_user()), ("admin", W.admin_user())):
        port.tokens[f"token-{who}"] = user
        store.sessions[f"cookie-{who}"] = W.TiledCredential(f"token-{who}", tiled_target=W.STAGING)
    app = api_mod.create_app(make_cfg(ctx), deployment=W.staging_settings(), identity_port=port,
                             credential_store=store, clock=W.FakeClock())
    return Deployment(app=app, ctx=ctx, port=port, store=store)


def _frozen_ui_payload(settings: Dict[str, Any], **changes: Any) -> Dict[str, Any]:
    """What ``settingsToApiPayload`` sends: every field, from the GET response plus the user's edits."""
    s = {**settings, **changes}
    json_mode = s["graph_source"] == "json"
    path = s.get("json_graph_path") or "storage/kg/placeholder_default.json"
    return {
        "backend": s["backend"], "model": s["model"], "graph_source": s["graph_source"],
        "workflow_mode": s["workflow_mode"], "extraction_mode": s["extraction_mode"],
        "targeted_max_pages": s["targeted_max_pages"],
        "json_graph_path": path if json_mode else None,
        "json_graph_paths": (s.get("json_graph_paths") or [path]) if json_mode else [],
        "selected_graph_ids": s.get("selected_graph_ids") if json_mode and s.get("selected_graph_ids") else None,
        "kg_query_max_nodes": s["kg_query_max_nodes"], "kg_query_hops": s["kg_query_hops"],
        "source_rag": s["source_rag"], "use_live_tiled": s["use_live_tiled"],
        "tiled_uri": s.get("tiled_uri"),
    }


def _save(dep: Deployment, who: str, **changes: Any):
    client = dep.client(who)
    current = client.get("/settings").json()
    return client.put("/settings", json=_frozen_ui_payload(current, **changes))


@pytest.fixture
def seen() -> List[Dict[str, Any]]:
    return []


@pytest.fixture
def als(monkeypatch, tmp_path, seen) -> Deployment:
    return _build_als(monkeypatch, tmp_path, seen)


# ------------------------------------------------------------------ classification


def test_every_settings_field_is_classified():
    from app.modules.f2w_agent.api import AgentSettingsUpdate

    fields = set(AgentSettingsUpdate.model_fields)
    assert fields <= SETTINGS_USER_FIELDS | SETTINGS_DEPLOYMENT_FIELDS
    assert not SETTINGS_USER_FIELDS & SETTINGS_DEPLOYMENT_FIELDS
    doc = (REPO / "docs" / "migration" / "authentication" / "settings_fields.md").read_text(encoding="utf-8")
    for name in fields | {"provider"}:
        assert f"`{name}`" in doc, name


def test_update_plan():
    local = settings_update_plan({"model", "extraction_mode", "tiled_uri"}, local=True, admin=True)
    assert local.ignored == frozenset() and local.deployment == frozenset()
    assert "settings.global" in local.capabilities
    user = settings_update_plan({"model", "kg_query_hops", "extraction_mode", "tiled_uri", "new_field"},
                                local=False, admin=False)
    assert user.capabilities == {"settings.personal"} and user.personal == {"model", "kg_query_hops"}
    assert user.ignored == {"extraction_mode", "tiled_uri", "new_field"} and not user.deployment
    admin = settings_update_plan({"model", "extraction_mode", "tiled_uri"}, local=False, admin=True)
    assert admin.capabilities == {"settings.personal", "settings.global"}
    assert admin.deployment == {"extraction_mode"} and admin.ignored == {"tiled_uri"}


# ------------------------------------------------------------------ ALS principals


def test_two_principals_keep_independent_settings_and_turns_use_their_own(als, seen):
    assert _save(als, "user", backend="ollama", model="llama3:8b", kg_query_hops=1, source_rag=True) \
        .status_code == 200
    assert _save(als, "admin", backend="cborg", model="lbl/cborg-chat", kg_query_hops=3,
                 kg_query_max_nodes=50).status_code == 200
    user, admin = als.client("user").get("/settings").json(), als.client("admin").get("/settings").json()
    assert (user["backend"], user["model"], user["kg_query_hops"], user["source_rag"]) == \
        ("ollama", "llama3:8b", 1, True)
    assert (admin["backend"], admin["kg_query_hops"], admin["kg_query_max_nodes"], admin["source_rag"]) == \
        ("cborg", 3, 50, False)

    for who, session in (("user", "s-u1"), ("admin", "s-a1"), ("user", "s-u1"), ("admin", "s-a1")):
        seen.clear()
        resp = als.client(who).post("/chat", json={"message": ASK, "session_id": session})
        assert resp.status_code == 200, resp.text
        assert seen, "retrieval was not queried"
        used = seen[-1]
        if who == "user":
            assert (used["backend"], used["model"], used["kg_query_hops"], used["source_rag"]) == \
                ("ollama", "llama3:8b", 1, True)
        else:
            assert (used["backend"], used["kg_query_hops"], used["kg_query_max_nodes"], used["source_rag"]) == \
                ("cborg", 3, 50, False)

    seen.clear()
    raw = als.client("user").post("/chat/stream", json={"message": ASK, "session_id": "s-u2"}).text
    assert "complete" in raw and seen[-1]["backend"] == "ollama" and seen[-1]["kg_query_hops"] == 1


def test_principal_who_never_saved_inherits_deployment_defaults(als):
    before = als.client("user2").get("/settings").json()
    assert _save(als, "user", backend="ollama", kg_query_hops=1).status_code == 200
    assert als.client("user2").get("/settings").json() == before


def test_non_admin_frozen_ui_save_succeeds_and_ignores_deployment_fields(als):
    before = als.client("admin").get("/settings").json()
    resp = _save(als, "user", kg_query_hops=1, extraction_mode="full", workflow_mode="deterministic",
                 targeted_max_pages=9)
    assert resp.status_code == 200, resp.text
    assert resp.headers[SETTING_WARNING_HEADER] == \
        "setting_not_writable=extraction_mode,targeted_max_pages,workflow_mode"
    assert resp.json()["kg_query_hops"] == 1 and resp.json()["extraction_mode"] == before["extraction_mode"]
    after = als.client("admin").get("/settings").json()
    assert after == before
    # An unchanged echo of the deployment fields does not warn.
    assert SETTING_WARNING_HEADER not in _save(als, "user", kg_query_hops=2).headers


def test_non_admin_graph_selection_is_personal(als):
    resp = _save(als, "user", graph_source="json", selected_graph_ids=["matkg_contract"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["graph_source"] == "json"
    assert als.client("admin").get("/settings").json()["graph_source"] == "splash"
    assert als.client("user").get("/settings").json()["graph_source"] == "json"


def test_admin_changes_deployment_wide_fields(als):
    hops_before = als.client("user").get("/settings").json()["kg_query_hops"]
    assert hops_before != 3
    resp = _save(als, "admin", extraction_mode="full", targeted_max_pages=9, kg_query_hops=3)
    assert resp.status_code == 200 and SETTING_WARNING_HEADER not in resp.headers
    user = als.client("user").get("/settings").json()
    assert (user["extraction_mode"], user["targeted_max_pages"]) == ("full", 9)
    # The admin's personal hops did not change anyone else's.
    assert user["kg_query_hops"] == hops_before
    # The Tiled URL stays unwritable on ALS, for admins too.
    resp = als.client("admin").put("/settings", json={"tiled_uri": "http://127.0.0.1:9999"})
    assert resp.headers[SETTING_WARNING_HEADER] == "setting_not_writable=tiled_uri"


def test_personal_settings_persist_across_restart(monkeypatch, tmp_path, seen):
    first = _build_als(monkeypatch, tmp_path, seen)
    assert _save(first, "user", backend="ollama", model="llama3:8b", kg_query_hops=1).status_code == 200
    assert (first.workdir / "access" / "personal_settings.json").exists()
    second = _build_als(monkeypatch, tmp_path, seen)  # restart on the same workdir
    got = second.client("user").get("/settings").json()
    assert (got["backend"], got["model"], got["kg_query_hops"]) == ("ollama", "llama3:8b", 1)
    seen.clear()
    assert second.client("user").post("/chat", json={"message": ASK, "session_id": "s-r"}).status_code == 200
    assert seen[-1]["backend"] == "ollama" and seen[-1]["kg_query_hops"] == 1


# ------------------------------------------------------------------ :local


def test_local_settings_stay_server_wide(monkeypatch, tmp_path):
    local = W.build_local(monkeypatch, tmp_path)
    resp = _save(local, "anon", backend="ollama", kg_query_hops=1, extraction_mode="full")
    assert resp.status_code == 200 and SETTING_WARNING_HEADER not in resp.headers
    got = local.client("anon").get("/settings").json()
    assert (got["backend"], got["kg_query_hops"], got["extraction_mode"]) == ("ollama", 1, "full")
    assert not (local.workdir / "access" / "personal_settings.json").exists()

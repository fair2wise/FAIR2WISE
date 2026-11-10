"""A Tiled save target given at STARTUP (``create_api_app(tiled_save=...)``, the ALS wiring) never loads from Splash.

Twin of ``test_local_editable_default.py`` (which covers the ``:local`` provider). With ``cfg.tiled_save`` set the
session graph is the effective record on Tiled (D03/D16: Splash is deprecated; the ``splash`` token is only the legacy
wire name of the editable mode), so the retrieval loaders get ``json`` at startup and on every reload, and no Splash
HTTP call is made. Without a save target nothing changes: ALS with no ``tiled_save`` and the legacy ``agent`` profile
still pass ``splash`` through, JSON mode stays ``json``. Same tripwire as the ``:local`` test.
"""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import test_wiring_tiled_save as tws  # noqa: E402
from test_local_editable_default import _REAL_RETRIEVAL, _REAL_SPLASH, Tripwire  # noqa: E402
from test_wiring_tiled_save import template  # noqa: E402,F401

import _legacy_ui_harness as H  # noqa: E402
from app.api.auth import SESSION_COOKIE  # noqa: E402
from app.modules.f2w_agent import api as api_mod  # noqa: E402
from app.modules.graph import kg_update  # noqa: E402


@pytest.fixture
def tripwired(monkeypatch):
    """Real Splash client and real retrieval under the tripwire (the harness would otherwise fake both in memory)."""
    wire = Tripwire(monkeypatch)
    original_install = H.install

    def install(mp, ws, repo):
        ctx = original_install(mp, ws, repo)
        for name, fn in _REAL_SPLASH.items():
            mp.setattr(kg_update, name, fn)
        mp.setattr(api_mod, "RetrievalAgent", _REAL_RETRIEVAL)
        return ctx

    monkeypatch.setattr(H, "install", install)
    return wire


@pytest.mark.parametrize("build", ["build_local", "build_als"])
def test_startup_save_target_loaders_read_json_and_never_contact_splash(build, tripwired, monkeypatch, tmp_path, template):
    w = getattr(tws, build)(monkeypatch, tmp_path, template)
    client = w.client if build == "build_local" else TestClient(
        w.app, cookies={SESSION_COOKIE: "cookie-admin"}, raise_server_exceptions=False)
    service = w.app.state.f2w_agent_service
    assert service.tiled_save is not None and service.tiled_save_provider is None
    assert tripwired.hits == [], f"startup contacted Splash: {tripwired.hits}"
    assert service.runtime.graph_source == "splash"                              # the wire token is unchanged
    assert service._loader_source("splash") == "json"
    assert service.retrieval._graph_source == "json"
    for source in ("json", "splash", "splash"):                                  # the UI's source toggle
        assert client.put("/settings", json={"graph_source": source}).status_code == 200
    assert client.get("/settings").json()["graph_source"] == "splash"
    asyncio.run(service._reload_active_graphs())
    client.get("/graph/nodes/search", params={"q": "Term"})
    path = str(service.graph_path())
    assert asyncio.run(service._reload_retrieval(path, graph_source="splash", graph_files=[path]))[
        "graph_source_used"] == "json"
    assert tripwired.hits == [], f"retrieval contacted Splash: {tripwired.hits}"


def _service(*, tiled_save=None, provider=None):
    svc = object.__new__(api_mod.AgentPipelineService)
    svc.tiled_save = tiled_save
    svc.tiled_save_provider = provider
    return svc


def test_no_save_target_leaves_the_loader_source_unchanged():
    none = _service()
    assert [none._loader_source(s) for s in ("splash", "json")] == ["splash", "json"]
    with_target = _service(tiled_save=object())
    assert [with_target._loader_source(s) for s in ("splash", "json")] == ["json", "json"]
    with_provider = _service(provider=object())
    assert [with_provider._loader_source(s) for s in ("splash", "json")] == ["json", "json"]


def _recording_retrieval(monkeypatch):
    """Record the ``graph_source`` every ``RetrievalAgent`` is built with (the harness's fake is replaced)."""
    seen: list[str] = []

    def build(*args, **kwargs):
        seen.append(kwargs.get("graph_source"))
        return _REAL_RETRIEVAL(*args, **kwargs)

    monkeypatch.setattr(api_mod, "RetrievalAgent", build)
    return seen


def test_als_without_tiled_save_still_syncs_and_loads_from_splash(monkeypatch, tmp_path):
    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    seen = _recording_retrieval(monkeypatch)
    app = H.make_ui_app(ctx, kg_mode="splash")
    service = app.state.f2w_agent_service
    assert service.tiled_save is None and service.tiled_save_provider is None
    assert ctx.calls.get("splash.load_graph", 0) >= 1
    assert service._loader_source("splash") == "splash"
    assert seen and set(seen) == {"splash"}                                       # the loader still gets the Splash token


def test_json_mode_is_unchanged(monkeypatch, tmp_path):
    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    seen = _recording_retrieval(monkeypatch)
    app = H.make_ui_app(ctx, kg_mode="json")
    service = app.state.f2w_agent_service
    assert service._loader_source(service.runtime.graph_source) == "json"
    assert seen and set(seen) == {"json"}

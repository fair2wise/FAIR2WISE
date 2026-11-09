"""``:local`` starts in the editable KG mode (owner decision 2026-11-09, fifteenth set: "Default to editable on :local").

``docker/profiles/local.json`` passes ``--kg-mode splash`` (the wire token of the editable mode; it never contacts a
Splash service, owner choice "accept but alias to json/tiled"). Pins, with the real (unfaked) Splash client code and a
tripwire on every HTTP/socket path to a Splash service or a non-loopback host:

* the image starts with no publication: the graph view works from the JSON graph, ``can_save`` is false, PATCH is the
  typed 501, and nothing contacted Splash while loading the graph, syncing the session graph or building retrieval;
* every retrieval (re)load in the editable mode reads the session JSON, never ``splash-links`` (the ``splash`` token
  is aliased to ``json`` for the graph loader once a Tiled save provider is attached);
* after a publication Save works against the lazy Tiled target and the graph view shows that corpus;
* without a provider (ALS, library callers) the legacy behaviour is unchanged.
"""
import asyncio
import json
import socket
import sys
import urllib.request
from pathlib import Path

import pytest
import requests

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (HERE,):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from test_local_save import KEY, NODE, SCOPE, World, live_nodes, template  # noqa: E402,F401
from app.modules.f2w_agent import api as api_mod  # noqa: E402
from app.modules.graph import kg_update  # noqa: E402

_REAL_RETRIEVAL = api_mod.RetrievalAgent

#: The real client functions, captured before the legacy harness replaces them with in-memory fakes.
_REAL_SPLASH = {name: getattr(kg_update, name) for name in dir(kg_update) if name.startswith(("splash_", "load_splash"))}


class Tripwire:
    """Records every attempt to reach a Splash service or a non-loopback host and answers 'unreachable'."""

    def __init__(self, monkeypatch):
        self.hits: list[str] = []
        real_connect = socket.socket.connect
        outer = self

        def connect(sock, address, *a):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                host, port = address[0], address[1]
                if host not in ("127.0.0.1", "::1") or port in (8081, 8000):
                    outer.hits.append(f"socket {address!r}")
                    raise ConnectionRefusedError(address)
            return real_connect(sock, address, *a)

        def urlopen(req, *a, **k):
            outer.hits.append(f"urllib {getattr(req, 'full_url', req)}")
            raise urllib.error.URLError("splash unreachable (tripwire)")

        def send(session, request, **k):
            outer.hits.append(f"requests {request.url}")
            raise requests.ConnectionError("splash unreachable (tripwire)")

        monkeypatch.setattr(socket.socket, "connect", connect)
        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
        monkeypatch.setattr(requests.Session, "send", send)
        monkeypatch.setenv("SPLASH_LINKS_URI", "splash://splash.invalid:8081")
        monkeypatch.setenv("KG_RAG_SPLASH_URI", "splash://splash.invalid:8081")


@pytest.fixture
def editable_world(monkeypatch, tmp_path, template):
    """Build the ``:local`` app in the editable mode with the REAL Splash client and retrieval under the tripwire."""
    import _legacy_ui_harness as H

    def build():
        wire = Tripwire(monkeypatch)
        original_install = H.install

        def install(mp, ws, repo):
            ctx = original_install(mp, ws, repo)      # the harness fakes Splash and retrieval in memory ...
            for name, fn in _REAL_SPLASH.items():     # ... put the real code back before the app is built
                mp.setattr(kg_update, name, fn)
            mp.setattr(api_mod, "RetrievalAgent", _REAL_RETRIEVAL)
            return ctx

        monkeypatch.setattr(H, "install", install)
        return World(monkeypatch, tmp_path, template, kg_mode="splash"), wire

    return build


def test_starts_healthy_editable_with_no_publication_and_no_splash_contact(editable_world):
    w, wire = editable_world()
    assert wire.hits == [], f"startup contacted Splash: {wire.hits}"
    assert w.client.get("/health").status_code == 200
    assert w.client.get("/settings").json()["graph_source"] == "splash"          # the editable mode, wire token
    graph = w.client.get("/graph").json()
    assert {n["id"] for n in graph["nodes"]} >= {NODE}                           # the JSON graph, not an empty one
    assert w.can_save() is False
    resp = w.client.patch(f"/graph/node/{NODE}", json={"label": "too early"})
    assert resp.status_code == 501 and "JSON available, Tiled unavailable" in resp.json()["detail"]
    assert not w.journal_path().exists()
    assert wire.hits == [], f"a request contacted Splash: {wire.hits}"


def test_retrieval_loads_read_the_session_json_never_splash(editable_world):
    w, wire = editable_world()
    assert w.service.retrieval._graph_source == "json"                           # aliased: no splash-links loader
    for source in ("json", "splash", "splash"):                                  # the UI's source toggle, twice
        assert w.client.put("/settings", json={"graph_source": source}).status_code == 200
    asyncio.run(w.service._reload_active_graphs())
    w.client.get("/graph/nodes/search", params={"q": "Term"})
    path = str(w.service.graph_path())
    assert asyncio.run(w.service._reload_retrieval(path, graph_source="splash", graph_files=[path]))[
        "graph_source_used"] == "json"
    assert wire.hits == [], f"retrieval contacted Splash: {wire.hits}"


def test_save_after_the_first_publication_with_the_editable_default(editable_world):
    w, wire = editable_world()
    w.freeze("v1", w.payload)
    w.publish("v1")
    assert w.can_save() is True                                                   # no PUT /settings needed
    resp = w.client.patch(f"/graph/node/{NODE}", json={"label": "Edited on the default"})
    assert resp.status_code == 200, resp.text
    assert live_nodes(w.tiled())[NODE]["name"] == "Edited on the default"
    assert w.client.get(f"/graph/node/{NODE}").json()["label"] == "Edited on the default"
    shown = {n["id"]: n for n in w.client.get("/graph").json()["nodes"]}
    assert shown[NODE]["label"] == "Edited on the default"                        # the view is the effective record
    assert wire.hits == [], f"Save contacted Splash: {wire.hits}"


def test_the_graph_view_follows_the_active_publication_without_a_settings_toggle(editable_world):
    w, wire = editable_world()
    changed = json.loads(json.dumps(w.payload))
    changed["things"][0]["description"] = "published description"
    w.freeze("v1", changed)
    w.publish("v1")
    shown = {n["id"]: n for n in w.client.get("/graph").json()["nodes"]}
    assert shown[changed["things"][0]["id"]]["description"] == "published description"
    assert wire.hits == []


def test_without_a_save_provider_the_legacy_splash_behaviour_is_unchanged(monkeypatch, tmp_path):
    """ALS and library callers: ``kg_mode=splash`` still syncs from Splash and still loads ``splash`` in retrieval."""
    import _legacy_ui_harness as H

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    app = H.make_ui_app(ctx, kg_mode="splash")
    service = app.state.f2w_agent_service
    assert service.tiled_save is None and service.tiled_save_provider is None
    assert ctx.calls.get("splash.load_graph", 0) >= 1                            # the startup sync ran
    assert service._loader_source("splash") == "splash"


def test_a_save_target_given_at_startup_keeps_its_behaviour(monkeypatch, tmp_path):
    """``cfg.tiled_save`` (ALS wiring) is not the ``:local`` provider: the loader alias applies to the provider only."""
    svc = object.__new__(api_mod.AgentPipelineService)
    svc.tiled_save = object()                    # type: ignore[assignment]
    assert svc._loader_source("splash") == "splash"

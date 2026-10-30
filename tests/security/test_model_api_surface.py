"""AUD-03 (T28A): the model-compatible API carries access control, the D19 projection and a loopback bind.

* ``create_fastapi_app`` installs the access runtime on ``MODEL_API_SURFACE``: every route is declared
  (``undeclared_routes == []``), the local operator serves ``:local``, an ALS caller must be signed in,
  and a signed-in ALS user reaches all six routes, the chat routes included (R20/R21 ``on_als=True``,
  owner decision B7, O-A9, 2026-10-30; the images mount this app in the UI API process, see
  ``app/api/main.py`` and ``tests/gateway/test_gateway_prefix.py``).
* ALS startup without an identity port and session store is refused.
* Both chat routes pass through ``project_model_chat`` with the turn's PDF windows as source regions:
  an echoing model cannot return more than 40% of a window (O-D2) on either route.
* ``run_fastapi`` binds ``127.0.0.1`` unless ``KG_RAG_API_HOST`` or ``host=`` widens it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from security_fakes import (  # noqa: E402
    FakeTiledIdentityPort,
    InMemoryCredentialStore,
    STAGING,
    admin_user,
    local_settings,
    plain_user,
    staging_settings,
)

from app.api.auth import SESSION_COOKIE, undeclared_routes  # noqa: E402
from app.modules import kg_rag_api  # noqa: E402
from app.modules.security.authorization import StartupConfigError  # noqa: E402
from app.modules.security.tiled_identity import TiledCredential  # noqa: E402

PAGE = " ".join(f"Sentence {i} of the synthetic paper on resonant soft X-ray scattering of polymers."
                for i in range(20))


class Echo:
    """A model that returns its whole prompt (the worst case for source echo)."""

    model = "echo"

    async def chat(self, messages, *a, **k):
        return "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))


class Fixed:
    model = "fixed"

    async def chat(self, messages, *a, **k):
        return "A short grounded answer."


def _graph(tmp_path: Path) -> str:
    graph = {
        "things": [
            {"id": "matkg:P3HT", "name": "P3HT polymer", "category": "ConjugatedPolymer",
             "description": "Conjugated polymer studied with resonant soft X-ray scattering.",
             "source_papers": ["paper.pdf"], "publication_year": 2024},
        ],
        "associations": [],
    }
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    return str(path)


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """Lexical retrieval, a fake PDF page, no network, a JSON graph."""
    monkeypatch.setenv("KG_RAG_GRAPH_SOURCE", "json")
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical", raising=False)
    monkeypatch.setattr(kg_rag_api, "GRAPH_SOURCE", "json", raising=False)
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: PAGE, raising=False)
    monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: Echo())
    return _graph(tmp_path)


def _echoed(answer: str) -> int:
    return sum(1 for i in range(20) if f"Sentence {i} of the synthetic paper" in answer)


# ------------------------------------------------------------------ access control


def test_every_model_route_is_declared_and_local_serves_the_operator(offline):
    app = kg_rag_api.create_fastapi_app(offline, backend="ollama", deployment=local_settings())
    assert undeclared_routes(app) == []
    client = TestClient(app)
    assert client.get("/api/tags").status_code == 200
    assert client.get("/api/version").json() == {"version": "0.1.0"}
    assert client.get("/v1/models").status_code == 200 and client.get("/api/ps").json() == {"processes": []}
    assert client.post("/api/chat", json={"messages": []}).status_code == 400


def test_als_startup_without_identity_wiring_is_refused(offline):
    with pytest.raises(StartupConfigError, match="identity_port and credential_store"):
        kg_rag_api.create_fastapi_app(offline, backend="ollama", deployment=staging_settings())


def test_als_requires_sign_in_and_serves_signed_in_users_with_the_projection(offline):
    port = FakeTiledIdentityPort(tiled_target=STAGING)
    store = InMemoryCredentialStore()
    for who, user in (("user", plain_user()), ("admin", admin_user())):
        port.tokens[f"token-{who}"] = user
        store.sessions[f"cookie-{who}"] = TiledCredential(f"token-{who}", tiled_target=STAGING)
    app = kg_rag_api.create_fastapi_app(offline, backend="ollama", deployment=staging_settings(),
                                        identity_port=port, credential_store=store)
    assert undeclared_routes(app) == []
    anon = TestClient(app)
    assert anon.get("/api/tags").status_code == 401
    # B7: the chat routes are exposed on ALS; an anonymous caller gets the sign-in prompt, never a 403 or an answer.
    for path in ("/api/chat", "/v1/chat/completions"):
        resp = anon.post(path, json={"messages": [{"role": "user", "content": "P3HT"}]})
        assert resp.status_code == 401 and resp.json()["code"] == "sign_in_required", (path, resp.text)
    assert not port.calls  # no credential, nothing to verify
    pick = {"/api/chat": lambda b: b["message"]["content"],
            "/v1/chat/completions": lambda b: b["choices"][0]["message"]["content"]}
    for who in ("user", "admin"):
        signed = TestClient(app, cookies={SESSION_COOKIE: f"cookie-{who}"})
        assert signed.get("/api/tags").status_code == 200
        assert signed.get("/v1/models").status_code == 200
        for path in ("/api/chat", "/v1/chat/completions"):
            resp = signed.post(path, json={"messages": [{"role": "user", "content": "P3HT resonant scattering polymer"}]})
            assert resp.status_code == 200, (who, path, resp.text)  # R20/R21: on_als=True (B7)
            assert _echoed(pick[path](resp.json())) <= 8, (who, path)  # the D19 projection applies on ALS too
    assert port.calls  # identities were really verified


# ------------------------------------------------------------------ D19 projection


@pytest.mark.parametrize("path,pick", [
    ("/api/chat", lambda body: body["message"]["content"]),
    ("/v1/chat/completions", lambda body: body["choices"][0]["message"]["content"]),
])
def test_echoing_model_cannot_return_more_than_forty_percent_of_a_pdf_window(offline, path, pick):
    app = kg_rag_api.create_fastapi_app(offline, backend="ollama", deployment=local_settings())
    with kg_rag_api.capture_pdf_snippets() as snippets:  # the raw prompt really carries the window
        kg = kg_rag_api.KnowledgeGraph(offline)
        infos = kg_rag_api.retrieve_nodes("P3HT resonant scattering polymer", kg)
        raw = kg.build_context(infos, include_structured=False, char_budget=20_000, hint_terms=["P3HT"])
    assert snippets and _echoed(raw) >= 10
    resp = TestClient(app).post(path, json={"messages": [{"role": "user", "content": "P3HT resonant scattering polymer"}]})
    assert resp.status_code == 200
    answer = pick(resp.json())
    assert _echoed(answer) <= 8
    assert "source text omitted" in answer.lower() or _echoed(answer) == 0


def test_plain_answers_and_the_frozen_shapes_are_unchanged(offline, monkeypatch):
    monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: Fixed())
    app = kg_rag_api.create_fastapi_app(offline, backend="ollama", deployment=local_settings())
    client = TestClient(app)
    body = client.post("/api/chat", json={"messages": [{"role": "user", "content": "P3HT"}]}).json()
    assert body == {"model": "fixed", "message": {"role": "assistant", "content": "A short grounded answer."},
                    "done": True}
    body = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "P3HT"}]}).json()
    assert set(body) == {"id", "object", "created", "model", "choices"}
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "A short grounded answer."}


def test_model_turn_sources_expose_regions_without_text():
    sources = kg_rag_api.ModelTurnSources([("/abs/path/paper.pdf", "window text one"), ("x.pdf", "  ")])
    (ref,) = sources.refs()
    assert ref.work_id == "paper.pdf" and ref.citation == "[PDF: paper.pdf]"
    assert ref.source_version.startswith("kg-rag-snippet:sha256:")
    assert "window text" not in repr(ref)
    assert sources.work_ids() == ["paper.pdf"]
    assert sources.read(ref.source_version, ref.locator) == "window text one"
    assert sources.read("other", ref.locator) is None


# ------------------------------------------------------------------ bind address


def test_run_fastapi_binds_loopback_unless_widened_explicitly(monkeypatch, offline):
    seen = []
    monkeypatch.setattr(kg_rag_api.uvicorn, "run", lambda app, host, port: seen.append((host, port)))
    monkeypatch.setattr(kg_rag_api, "create_fastapi_app", lambda *a, **k: object())
    monkeypatch.delenv("KG_RAG_API_HOST", raising=False)
    kg_rag_api.run_fastapi(offline)
    monkeypatch.setenv("KG_RAG_API_HOST", "0.0.0.0")
    kg_rag_api.run_fastapi(offline)
    kg_rag_api.run_fastapi(offline, host="10.0.0.5", port=12000)
    assert seen == [("127.0.0.1", 11435), ("0.0.0.0", 11435), ("10.0.0.5", 12000)]

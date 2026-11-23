"""Task 4: ``GET /v1/ollama/models`` (policy V86), the proxy behind the settings panel's Ollama dropdown.

Fakes only: the Ollama server is an ``httpx.MockTransport``; nothing opens a socket. The route is read-only, has a
policy row, answers 200 with a state for every outcome, and never carries the Ollama URL.
"""
from __future__ import annotations

import json

import httpx

from app.api.auth import undeclared_routes
from app.api.policies import API_ROUTE_POLICIES
from app.modules import ollama_catalog

URL = "http://ollama.cluster.invalid:11434"
_REAL_CLIENT = httpx.Client  # captured before any test patches ``httpx.Client``


def _ollama(monkeypatch, handler, url=URL):
    if url is None:
        monkeypatch.delenv("OLLAMA_URL", raising=False)
    else:
        monkeypatch.setenv("OLLAMA_URL", url)
    monkeypatch.setattr(ollama_catalog.httpx, "Client",
                        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))


def _tags(models):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"models": [{"name": m} for m in models]})

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def test_policy_row_exists_and_every_route_is_declared(local):
    rows = [p for p in API_ROUTE_POLICIES if p.path == "/v1/ollama/models"]
    assert len(rows) == 1 and rows[0].method == "GET" and rows[0].capabilities == frozenset({"settings.personal"})
    assert undeclared_routes(local.app) == []
    methods = {m for r in local.app.routes if getattr(r, "path", "") == "/v1/ollama/models" for m in r.methods}
    assert methods == {"GET"}  # read-only: no other verb is routed


def test_lists_the_models_asked_fresh_on_every_call(local, monkeypatch):
    handler = _tags(["llama3:8b", "hf.co/org/model:Q4_K_M"])
    _ollama(monkeypatch, handler)
    c = local.client()
    first = c.get("/v1/ollama/models")
    assert first.status_code == 200
    assert first.json() == {"state": "ok", "models": ["hf.co/org/model:Q4_K_M", "llama3:8b"], "error": None}
    c.get("/v1/ollama/models")
    assert len(handler.seen) == 2  # refreshed per open: no server-side cache
    assert [r.method for r in handler.seen] == ["GET", "GET"] and handler.seen[0].url.path == "/api/tags"
    assert URL.split("//")[1].split(":")[0] not in first.text  # the host is not echoed


def test_every_state_is_a_200_without_the_url(local, monkeypatch):
    c = local.client()

    _ollama(monkeypatch, None, url=None)
    body = c.get("/v1/ollama/models").json()
    assert body["state"] == "unset" and body["models"] == [] and "OLLAMA_URL" in body["error"]

    _ollama(monkeypatch, _tags([]))
    assert c.get("/v1/ollama/models").json() == {"state": "empty", "models": [], "error": None}

    def down(request):
        raise httpx.ConnectError("could not reach ollama.cluster.invalid:11434 via 10.1.2.3")

    _ollama(monkeypatch, down)
    resp = c.get("/v1/ollama/models")
    assert resp.status_code == 200 and resp.json()["state"] == "unreachable"
    assert "cluster" not in resp.text and "10.1.2.3" not in resp.text and "11434" not in resp.text

    _ollama(monkeypatch, lambda request: httpx.Response(502, text="<html>bad gateway</html>"))
    body = c.get("/v1/ollama/models").json()
    assert body == {"state": "error", "models": [], "error": "Ollama answered HTTP 502"}


def test_the_route_forwards_nothing_from_the_caller(local, monkeypatch):
    handler = _tags(["a"])
    _ollama(monkeypatch, handler)
    local.client().get("/v1/ollama/models", params={"url": "http://169.254.169.254/", "path": "/api/pull"},
                       headers={"X-Ollama-Url": "http://evil.invalid"})
    assert [(r.method, str(r.url)) for r in handler.seen] == [("GET", URL + "/api/tags")]


def test_als_needs_a_signed_in_caller(als, monkeypatch):
    _ollama(monkeypatch, _tags(["a"]))
    assert als.client("anon").get("/v1/ollama/models").status_code == 401
    ok = als.client("user").get("/v1/ollama/models")
    assert ok.status_code == 200 and json.loads(ok.text)["models"] == ["a"]

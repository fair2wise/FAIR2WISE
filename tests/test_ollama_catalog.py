"""Ollama ``/api/tags`` listing: statuses, bounds, and nothing about the server's address in the result."""

from __future__ import annotations

import json

import httpx
import pytest

from app.modules import ollama_catalog as oc
from app.modules.ollama_catalog import list_ollama_models, ollama_base_url

URL = "http://ollama.cluster.invalid:11434"
ENV = {"OLLAMA_URL": URL}


def _fetch(status: int = 200, body: object = None, *, raw: bytes | None = None):
    payload = raw if raw is not None else json.dumps(body).encode()
    seen: list[tuple[str, float, int]] = []

    def fetch(url: str, timeout: float, max_bytes: int) -> tuple[int, bytes]:
        seen.append((url, timeout, max_bytes))
        return status, payload

    fetch.seen = seen  # type: ignore[attr-defined]
    return fetch


def test_unset_and_unusable_urls_are_unset_without_a_request():
    def boom(*_a):
        raise AssertionError("no request without a usable OLLAMA_URL")

    for env in ({}, {"OLLAMA_URL": "  "}, {"OLLAMA_URL": "ftp://h"}, {"OLLAMA_URL": "http://user:pw@h:1"},
                {"OLLAMA_URL": "not a url"}):
        listing = list_ollama_models(env, fetch=boom)
        assert listing.state == "unset" and listing.models == () and "OLLAMA_URL" in (listing.error or "")
        assert ollama_base_url(env) is None


def test_models_are_listed_sorted_unique_and_only_valid_names():
    body = {"models": [{"name": "qwen2.5:7b"}, {"model": "llama3:8b"}, {"name": "llama3:8b"}, {"name": ""},
                       {"name": "bad name with spaces"}, {"name": "x" * 300}, "junk", {"size": 1}]}
    fetch = _fetch(body=body)
    listing = list_ollama_models({"OLLAMA_URL": URL + "/"}, fetch=fetch)
    assert listing.state == "ok" and listing.models == ("llama3:8b", "qwen2.5:7b") and listing.error is None
    assert fetch.seen == [(URL + "/api/tags", oc.DEFAULT_TIMEOUT, oc.MAX_BODY_BYTES)]  # read-only GET, bounded


def test_model_count_is_capped():
    body = {"models": [{"name": f"m{i:04d}"} for i in range(oc.MAX_MODELS + 50)]}
    assert len(list_ollama_models(ENV, fetch=_fetch(body=body)).models) == oc.MAX_MODELS


def test_zero_models_is_empty_not_an_error():
    listing = list_ollama_models(ENV, fetch=_fetch(body={"models": []}))
    assert listing.state == "empty" and listing.models == () and listing.error is None


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused by ollama.cluster.invalid"), TimeoutError("slow"),
                                 OSError("no route to 10.0.0.7")])
def test_a_transport_fault_is_unreachable_and_its_text_is_not_shown(exc):
    def fetch(*_a):
        raise exc

    listing = list_ollama_models(ENV, fetch=fetch)
    assert listing.state == "unreachable" and listing.error == "Ollama did not answer"
    assert "cluster" not in json.dumps(listing.to_dict()) and "10.0.0.7" not in json.dumps(listing.to_dict())


def test_other_answers_are_errors_with_fixed_text():
    assert list_ollama_models(ENV, fetch=_fetch(502, {})).error == "Ollama answered HTTP 502"
    for raw in (b"", b"<html>proxy error</html>", b"[]", json.dumps({"models": "x"}).encode()):
        listing = list_ollama_models(ENV, fetch=_fetch(raw=raw))
        assert listing.state == "error" and listing.error == "Ollama sent no usable model list"


def test_the_listing_never_carries_the_url():
    for fetch in (_fetch(body={"models": [{"name": "a"}]}), _fetch(502, {}), _fetch(body={"models": []})):
        text = json.dumps(list_ollama_models(ENV, fetch=fetch).to_dict())
        assert "invalid" not in text and "11434" not in text and "http" not in text


def test_the_real_transport_is_a_bounded_get_that_does_not_follow_redirects(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "a:1"}]})
        return httpx.Response(404)

    real = httpx.Client
    monkeypatch.setattr(oc.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert list_ollama_models(ENV).models == ("a:1",)
    assert [r.method for r in requests] == ["GET"] and requests[0].url.path == "/api/tags"

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest"})

    monkeypatch.setattr(oc.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(redirect), **kw))
    assert list_ollama_models(ENV).state == "error"  # a redirect is not followed and is not a model list

    big = b'{"models": [' + b'{"name": "a"},' * 200_000 + b"{}]}"
    monkeypatch.setattr(oc.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=big)), **kw))
    assert list_ollama_models(ENV, max_bytes=1000).state == "error"  # over the byte cap

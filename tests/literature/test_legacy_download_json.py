"""``hardened_get_json``: the metadata-lookup transport the harvest scripts use (no network)."""

from __future__ import annotations

import httpx
import pytest

from app.modules.literature.legacy_download import JSONFetchError, hardened_get_json
from app.modules.literature.safe_fetch import NetworkPolicy

PUBLIC = NetworkPolicy(resolver=lambda host: ["93.184.216.34"])
URL = "https://api.openalex.org/works/doi:10.1/x"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_returns_decoded_json_and_sends_headers():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json={"id": "W1"})

    out = hardened_get_json(URL, headers={"User-Agent": "t/1"}, policy=PUBLIC, client=_client(handler))
    assert out == {"id": "W1"} and seen["ua"] == "t/1"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://api.crossref.org/x", "https://u:p@api.crossref.org/x",
                                 "https://api.crossref.org:8443/x", "http://127.0.0.1/x"])
def test_policy_violations_are_refused_before_any_request(url: str):
    calls: list[str] = []
    client = _client(lambda r: calls.append(str(r.url)) or httpx.Response(200, json={}))
    policy = NetworkPolicy(resolver=lambda host: ["10.0.0.5"]) if url.startswith("https://api") else PUBLIC
    with pytest.raises(JSONFetchError, match="blocked URL"):
        hardened_get_json(url, policy=policy, client=client)
    assert calls == []


def test_private_resolution_is_refused():
    with pytest.raises(JSONFetchError, match="private_address"):
        hardened_get_json(URL, policy=NetworkPolicy(resolver=lambda host: ["192.168.1.9"]),
                          client=_client(lambda r: httpx.Response(200, json={})))


def test_redirect_hops_are_rechecked():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8011/admin"})

    with pytest.raises(JSONFetchError, match="blocked URL"):
        hardened_get_json(URL, policy=PUBLIC, client=_client(handler))
    assert calls == [URL]


def test_safe_redirect_is_followed_and_loops_are_bounded():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/x"):
            return httpx.Response(301, headers={"location": "/y"})
        return httpx.Response(200, json=[1, 2])

    assert hardened_get_json(URL, policy=PUBLIC, client=_client(handler)) == [1, 2]
    loop = _client(lambda r: httpx.Response(302, headers={"location": str(r.url)}))
    with pytest.raises(JSONFetchError, match="too many redirects"):
        hardened_get_json(URL, policy=PUBLIC, client=loop)


@pytest.mark.parametrize("response, message", [
    (httpx.Response(500, content=b"oops"), "HTTP 500"),
    (httpx.Response(200, content=b"<html>"), "not valid JSON"),
    (httpx.Response(200, content=b"\xff\xfe"), "not valid JSON"),
    (httpx.Response(200, content=b"x" * 2048), "too large"),
])
def test_bad_answers_are_errors(response: httpx.Response, message: str):
    with pytest.raises(JSONFetchError, match=message):
        hardened_get_json(URL, policy=PUBLIC, client=_client(lambda r: response), max_bytes=1024)


def test_transport_errors_are_wrapped():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(JSONFetchError, match="network error"):
        hardened_get_json(URL, policy=PUBLIC, client=_client(handler))

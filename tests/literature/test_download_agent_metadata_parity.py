"""Old (pyalex / ``arxiv`` package / ``requests``) versus new (hardened getter) metadata hops, offline.

The old clients are driven for real with their transport replaced by a recorded answer (golden fixtures in
``fixtures/download_agent_metadata``, real-shaped, written by hand); the new helpers get the same answer through
``httpx.MockTransport``. Compared: the request (URL, auth/identity headers) and the parsed result.
No test here touches the network.
"""

from __future__ import annotations

from functools import partial
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import requests

from app.modules.literature import download_pdfs as dl
from app.modules.literature import legacy_download as ld
from app.modules.literature.legacy_download import (
    JSONFetchError,
    arxiv_search,
    hardened_get_bytes,
    hardened_get_json,
    openalex_search,
    openalex_work,
    parse_arxiv_atom,
    unpaywall_oa_pdfs,
)
from app.modules.literature.safe_fetch import NetworkPolicy

FIX = Path(__file__).parent / "fixtures" / "download_agent_metadata"
PUBLIC = NetworkPolicy(resolver=lambda host: ["93.184.216.34"])
KEY = "oa-secret-key-123"


def _fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


class Recorder:
    """An ``httpx`` mock transport that records requests and answers with fixed bytes."""

    def __init__(self, body: bytes, status: int = 200):
        self.body, self.status, self.requests = body, status, []

    def getter(self, fn: Any) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, content=self.body)

        return partial(fn, client=httpx.Client(transport=httpx.MockTransport(handler)), policy=PUBLIC)


# --------------------------------------------------------------------------------------- OpenAlex
class OldPyalexSession:
    """Replaces pyalex's requests session: records the request (applying pyalex's own auth) and replays a body."""

    def __init__(self, body: bytes):
        self.body, self.calls = body, []

    def get(self, url: str, auth: Any = None) -> Any:
        req = SimpleNamespace(headers={})
        auth(req)
        self.calls.append((url, dict(req.headers)))
        return SimpleNamespace(status_code=200, json=lambda: json.loads(self.body), raise_for_status=lambda: None)


@pytest.fixture
def pyalex_old(monkeypatch: pytest.MonkeyPatch) -> Any:
    from pyalex import config
    from pyalex import api as pyalex_api

    state = {}

    def install(body: bytes, *, email: str | None, api_key: str | None) -> OldPyalexSession:
        session = OldPyalexSession(body)
        monkeypatch.setattr(pyalex_api, "_get_requests_session", lambda: session)
        monkeypatch.setattr(config, "email", email)
        monkeypatch.setattr(config, "api_key", api_key)
        state["session"] = session
        return session

    return install


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


@pytest.mark.parametrize("oa_only", [True, False])
def test_openalex_search_matches_pyalex(pyalex_old: Any, oa_only: bool):
    from pyalex import Works

    body = _fixture("openalex_search.json")
    session = pyalex_old(body, email="curator@lbl.gov", api_key=KEY)
    text = "resonant soft x-ray scattering polymer"
    query = Works().search(text)
    old = list((query.filter(open_access={"is_oa": True}) if oa_only else query).get(per_page=25))
    old_url, old_headers = session.calls[0]

    rec = Recorder(body)
    new = openalex_search(text, 25, oa_only=oa_only, mailto="curator@lbl.gov", api_key=KEY,
                          get_json=rec.getter(hardened_get_json))
    new_request = rec.requests[0]

    assert [dict(r) for r in old] == new  # same records, same order, same relevance_score values
    assert [r["relevance_score"] for r in new] == [412.7, 97.25]
    new_query = _query(str(new_request.url))
    assert new_query.pop("mailto") == ["curator@lbl.gov"]  # the documented polite-pool parameter (added)
    assert new_query == _query(old_url)  # search, filter, per-page identical
    assert str(new_request.url).split("?")[0] == old_url.split("?")[0]
    # Identity/auth headers exactly as pyalex set them.
    assert new_request.headers["from"] == old_headers["From"]
    assert new_request.headers["authorization"] == old_headers["Authorization"] == f"Bearer {KEY}"


def test_openalex_work_matches_pyalex(pyalex_old: Any):
    from pyalex import Works

    body = _fixture("openalex_work.json")
    session = pyalex_old(body, email=None, api_key=None)
    old = Works()["W4385000001"]
    rec = Recorder(body)
    new = openalex_work("W4385000001", get_json=rec.getter(hardened_get_json))
    assert dict(old) == new
    assert str(rec.requests[0].url) == session.calls[0][0]
    assert "authorization" not in rec.requests[0].headers and "from" not in rec.requests[0].headers


def test_openalex_key_is_header_only_and_never_logged(caplog: pytest.LogCaptureFixture):
    rec = Recorder(_fixture("openalex_search.json"))
    with caplog.at_level(logging.DEBUG):
        openalex_search("graphene", 5, mailto="curator@lbl.gov", api_key=KEY, get_json=rec.getter(hardened_get_json))
        rec2 = Recorder(b"not json")
        with pytest.raises(JSONFetchError):
            openalex_search("graphene", 5, api_key=KEY, get_json=rec2.getter(hardened_get_json))
    assert KEY not in str(rec.requests[0].url) and KEY not in caplog.text
    assert rec.requests[0].headers["authorization"] == f"Bearer {KEY}"


def test_openalex_rejects_what_pyalex_rejects_and_unknown_shapes():
    for bad in (0, 201):
        with pytest.raises(ValueError):
            openalex_search("x", bad, get_json=lambda url, **kw: {"results": []})
    with pytest.raises(ValueError):
        openalex_search("x", 5, get_json=lambda url, **kw: {"oops": 1})
    with pytest.raises(ValueError):
        openalex_work("W1", get_json=lambda url, **kw: {"oops": 1})


# --------------------------------------------------------------------------------------- arXiv
def _old_arxiv(body: bytes, query: str, n: int) -> tuple[list[Any], list[str]]:
    import arxiv

    client = arxiv.Client(page_size=n, delay_seconds=0, num_retries=2)
    urls: list[str] = []

    def get(url: str, **kw: Any) -> Any:
        urls.append(url)
        return SimpleNamespace(status_code=200, content=body)

    client._session.get = get  # type: ignore[method-assign]
    search = arxiv.Search(query=query, max_results=n, sort_by=arxiv.SortCriterion.Relevance)
    return list(client.results(search)), urls


@pytest.mark.parametrize("query", ["all:scattering", "resonant soft x-ray & polymer (films)", 'au:"Doe, J" ti:é'])
def test_arxiv_search_matches_the_arxiv_package(query: str):
    body = _fixture("arxiv_atom.xml")
    old, old_urls = _old_arxiv(body, query, 3)
    rec = Recorder(body)
    new = arxiv_search(query, 3, get_bytes=rec.getter(hardened_get_bytes))

    assert str(rec.requests[0].url) == old_urls[0]  # byte-identical request URL
    assert len(new) == len(old) == 3  # the entry without <published> is skipped by both
    for o, n in zip(old, new, strict=True):
        assert n["short_id"] == o.get_short_id()
        assert n["pdf_url"] == o.pdf_url
        assert n["doi"] == str(o.doi or "")
        assert n["title"] == o.title
        assert n["summary"] == o.summary
        assert n["year"] == o.published.year
    assert [n["short_id"] for n in new] == ["2201.01234v2", "cond-mat/0201082v1", "2305.00001v1"]
    assert new[2]["pdf_url"] is None and new[2]["doi"] == ""


def test_arxiv_retries_like_the_package_but_not_on_policy_refusals():
    calls: list[str] = []

    def flaky(url: str, **kw: Any) -> bytes:
        calls.append(url)
        if len(calls) < 3:
            raise JSONFetchError("HTTP 503")
        return _fixture("arxiv_atom.xml")

    assert len(arxiv_search("q", 3, get_bytes=flaky)) == 3 and len(calls) == 3  # 1 try + 2 retries

    calls.clear()

    def down(url: str, **kw: Any) -> bytes:
        calls.append(url)
        raise JSONFetchError("HTTP 429")

    with pytest.raises(JSONFetchError):
        arxiv_search("q", 3, get_bytes=down)
    assert len(calls) == 3

    calls.clear()

    def blocked(url: str, **kw: Any) -> bytes:
        calls.append(url)
        raise JSONFetchError("blocked URL (private_address)")

    with pytest.raises(JSONFetchError):
        arxiv_search("q", 3, get_bytes=blocked)
    assert len(calls) == 1


BOMB = b"""<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>
<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1v1</id><updated>2020-01-01T00:00:00Z</updated>
<published>2020-01-01T00:00:00Z</published><title>&b;</title></entry></feed>"""
XXE = b"""<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY x SYSTEM "file:///etc/passwd">]>
<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1v1</id><updated>2020-01-01T00:00:00Z</updated>
<published>2020-01-01T00:00:00Z</published><title>&x;</title></entry></feed>"""


@pytest.mark.parametrize("body", [BOMB, XXE, BOMB.decode().encode("utf-16"), b"\xff\xfe\x00garbage", b"<feed", b"",
                                  b"<?xml version='1.0'?><!--c--><!DOCTYPE feed><feed/>"])
def test_atom_parser_refuses_entities_dtds_and_garbage(body: bytes):
    assert parse_arxiv_atom(body) == []


def test_atom_error_entries_are_skipped():
    err = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad</id>'
           b"<updated>2020-01-01T00:00:00Z</updated><published>2020-01-01T00:00:00Z</published>"
           b"<title>Error</title></entry></feed>")
    assert parse_arxiv_atom(err) == []


def test_atom_body_is_size_bounded():
    rec = Recorder(b"<feed>" + b"x" * 5000 + b"</feed>")
    with pytest.raises(JSONFetchError, match="too large"):
        hardened_get_bytes("https://export.arxiv.org/api/query?x=1", max_bytes=1000,
                           client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=rec.body))),
                           policy=PUBLIC)


# --------------------------------------------------------------------------------------- Unpaywall
@pytest.mark.parametrize("doi", ["10.1021/acsnano.2c01234", "https://doi.org/10.1002/(SICI)1097-4636(199805)40:2<198::AID-JBM3>3.0.CO;2-X",
                                 "http://dx.doi.org/10.1016/S0022-2860(99)00123-4", "10.1000/a b+c%2Fd"])
def test_unpaywall_matches_fetch_unpaywall_oa_pdfs(doi: str, monkeypatch: pytest.MonkeyPatch):
    body = _fixture("unpaywall.json")
    seen: list[requests.PreparedRequest] = []

    def fake_get(url: str, params: dict[str, str], timeout: float, headers: dict[str, str]) -> Any:
        seen.append(requests.Request("GET", url, params=params).prepare())
        return SimpleNamespace(ok=True, status_code=200, json=lambda: json.loads(body))

    monkeypatch.setattr(dl.requests, "get", fake_get)
    old = dl.fetch_unpaywall_oa_pdfs(doi, "curator@lbl.gov")
    rec = Recorder(body)
    new = unpaywall_oa_pdfs(doi, "curator@lbl.gov", get_json=rec.getter(hardened_get_json))

    assert new == old and old[0]  # same URLs, same order, same sources
    assert old[0][0] == "https://arxiv.org/pdf/2201.01234"
    assert str(rec.requests[0].url) == seen[0].url  # byte-identical request URL


@pytest.mark.parametrize("doi,email", [("", "curator@lbl.gov"), ("not-a-doi", "curator@lbl.gov"),
                                       ("10.1/x", "you@example.com"), ("10.1/x", "")])
def test_unpaywall_guards_match_without_any_request(doi: str, email: str):
    def boom(url: str, **kw: Any) -> Any:
        raise AssertionError("no request expected")

    assert unpaywall_oa_pdfs(doi, email, get_json=boom) == dl.fetch_unpaywall_oa_pdfs(doi, email) == ([], [])


@pytest.mark.parametrize("status,payload", [(404, b"{}"), (500, b""), (200, b"[1, 2]"), (200, b"nope")])
def test_unpaywall_failures_yield_empty_like_the_old_path(status: int, payload: bytes, monkeypatch: pytest.MonkeyPatch):
    def fake_get(url: str, params: dict[str, str], timeout: float, headers: dict[str, str]) -> Any:
        def js() -> Any:
            return json.loads(payload)

        return SimpleNamespace(ok=status < 400, status_code=status, json=js)

    monkeypatch.setattr(dl.requests, "get", fake_get)
    assert dl.fetch_unpaywall_oa_pdfs("10.1/x", "curator@lbl.gov") == ([], [])
    rec = Recorder(payload, status)
    assert unpaywall_oa_pdfs("10.1/x", "curator@lbl.gov", get_json=rec.getter(hardened_get_json)) == ([], [])


# --------------------------------------------------------------------------------------- policy
@pytest.mark.parametrize("call", ["openalex", "arxiv", "unpaywall"])
def test_every_hop_is_policy_checked_including_redirects(call: str):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/steal"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    get_json = partial(hardened_get_json, client=client, policy=PUBLIC)
    get_bytes = partial(hardened_get_bytes, client=client, policy=PUBLIC)
    with pytest.raises(JSONFetchError, match="blocked URL"):
        if call == "openalex":
            openalex_search("q", 5, get_json=get_json)
        elif call == "arxiv":
            arxiv_search("q", 5, get_bytes=get_bytes, retries=0)
        else:
            assert unpaywall_oa_pdfs("10.1/x", "curator@lbl.gov", get_json=get_json) == ([], [])  # swallowed
            raise JSONFetchError("blocked URL (unpaywall swallowed, as the old path)")
    assert seen == [{"openalex": "api.openalex.org", "arxiv": "export.arxiv.org", "unpaywall": "api.unpaywall.org"}[call]]


def test_hosts_used_are_exactly_the_three_documented():
    assert {urlsplit(u).hostname for u in (ld.OPENALEX_URL, ld.ARXIV_QUERY_URL, ld.UNPAYWALL_URL)} == {
        "api.openalex.org", "export.arxiv.org", "api.unpaywall.org"}

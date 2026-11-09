"""Old (pyalex / ``arxiv`` package / ``requests``) versus new (hardened helpers) for the harvest modules, offline.

Follow-on to ``test_download_agent_metadata_parity.py`` for ``harvest_rsoxs``, ``download_pdfs`` and the f2w API
(T17B/H8 legacy_ports_plan P1). The real old clients are driven with their transport replaced by golden fixtures
(``fixtures/harvest_metadata``, real-shaped, written by hand): pyalex's cursor ``paginate``, filter-before-search
order, ``Authors``/``Works()[id]``, and the ``arxiv`` client's pagination, sort, delay and retry rules. The new code
gets the same answers through ``httpx.MockTransport``. Compared: request URLs (the polite-pool ``mailto`` parameter is
the one documented addition), identity headers and results. No test here touches the network.
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

from app.modules.commands import harvest_rsoxs as hv
from app.modules.f2w_agent import api as api_mod
from app.modules.literature import download_pdfs as dp
from app.modules.literature import legacy_download as ld
from app.modules.literature.legacy_download import (
    ArxivHit,
    JSONFetchError,
    arxiv_results,
    hardened_get_bytes,
    hardened_get_json,
    openalex_get,
    openalex_pages,
    openalex_work,
)
from app.modules.literature.safe_fetch import NetworkPolicy

FIX = Path(__file__).parent / "fixtures" / "harvest_metadata"
AGENT_FIX = Path(__file__).parent / "fixtures" / "download_agent_metadata"
PUBLIC = NetworkPolicy(resolver=lambda host: ["93.184.216.34"])
MAILTO = "curator@lbl.gov"


def _fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


class Sequence:
    """An ``httpx`` mock transport answering the n-th request with the n-th ``(status, body)``; records requests."""

    def __init__(self, *answers: tuple[int, bytes]):
        self.answers, self.requests = list(answers), []

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.answers[min(len(self.requests), len(self.answers)) - 1]
        return httpx.Response(status, content=body)

    def getter(self, fn: Any) -> Any:
        return partial(fn, client=httpx.Client(transport=httpx.MockTransport(self._handler)), policy=PUBLIC)

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def _without_mailto(url: str) -> str:
    base, _, query = url.partition("?")
    kept = [part for part in query.split("&") if part and not part.startswith("mailto=")]
    return base + ("?" + "&".join(kept) if kept else "")


# --------------------------------------------------------------------------------------- OpenAlex
class OldPyalexSession:
    """Replaces pyalex's requests session: records each request (applying pyalex's own auth), replays bodies in order."""

    def __init__(self, bodies: list[bytes]):
        self.bodies, self.calls = bodies, []

    def get(self, url: str, auth: Any = None) -> Any:
        req = SimpleNamespace(headers={})
        auth(req)
        self.calls.append((url, dict(req.headers)))
        body = self.bodies[min(len(self.calls), len(self.bodies)) - 1]
        return SimpleNamespace(status_code=200, json=lambda: json.loads(body), raise_for_status=lambda: None)


@pytest.fixture
def pyalex_old(monkeypatch: pytest.MonkeyPatch) -> Any:
    from pyalex import api as pyalex_api
    from pyalex import config

    def install(*bodies: bytes, email: str | None = None) -> OldPyalexSession:
        session = OldPyalexSession(list(bodies))
        monkeypatch.setattr(pyalex_api, "_get_requests_session", lambda: session)
        monkeypatch.setattr(config, "email", email)
        monkeypatch.setattr(config, "api_key", None)
        return session

    return install


PAGES = (_fx("openalex_works_page1.json"), _fx("openalex_works_page2.json"))
SEARCH = "resonant soft x-ray scattering"
OA_FILTER = {"open_access": {"is_oa": True}}


def test_cursor_pagination_matches_pyalex(pyalex_old: Any):
    from pyalex import Works

    session = pyalex_old(*PAGES, email=MAILTO)
    old_pages = [[dict(r) for r in page] for page in Works().search(SEARCH).filter(open_access={"is_oa": True})
                 .paginate(per_page=2)]
    seq = Sequence((200, PAGES[0]), (200, PAGES[1]))
    new_pages = list(openalex_pages(search=SEARCH, filters=OA_FILTER, per_page=2, mailto=MAILTO,
                                    get_json=seq.getter(hardened_get_json)))

    assert new_pages == old_pages and [len(p) for p in new_pages] == [2, 1]
    assert [_without_mailto(u) for u in seq.urls] == [u for u, _ in session.calls]  # cursor=%2A, then the token
    assert "cursor=%2A" in seq.urls[0] and "cursor=IlsxNzAx" in seq.urls[1]
    assert all(_query(u)["mailto"] == [MAILTO] for u in seq.urls)
    for request, (_, headers) in zip(seq.requests, session.calls, strict=True):
        assert request.headers["from"] == headers["From"] == MAILTO
        assert "authorization" not in request.headers


def test_filter_before_search_order_matches_pyalex(pyalex_old: Any):
    from pyalex import Works

    aid = "https://openalex.org/A5100000001"
    session = pyalex_old(*PAGES)
    old = [dict(r) for page in Works().filter(authorships={"author": {"id": aid}}).search('"11.0.1.2"')
           .paginate(per_page=2) for r in page]
    seq = Sequence((200, PAGES[0]), (200, PAGES[1]))
    new = [r for page in openalex_pages(search='"11.0.1.2"', filters={"authorships": {"author": {"id": aid}}},
                                        search_first=False, per_page=2, get_json=seq.getter(hardened_get_json))
           for r in page]
    assert new == old and len(new) == 3
    assert seq.urls == [u for u, _ in session.calls]  # no mailto given: byte-identical
    assert seq.urls[0].index("filter=") < seq.urls[0].index("search=")


def test_get_without_filter_and_authors_match_pyalex(pyalex_old: Any):
    from pyalex import Authors, Works

    session = pyalex_old(_fx("openalex_authors.json"), PAGES[0])
    old_authors = [dict(a) for a in Authors().search("Cheng Wang").get(per_page=25)]
    old_works = [dict(w) for w in Works().search("near title").get(per_page=5)]
    seq = Sequence((200, _fx("openalex_authors.json")), (200, PAGES[0]))
    get = seq.getter(hardened_get_json)
    new_authors, cursor = openalex_get("authors", search="Cheng Wang", per_page=25, get_json=get)
    new_works, _ = openalex_get("works", search="near title", per_page=5, get_json=get)
    assert (new_authors, new_works) == (old_authors, old_works) and cursor is None
    assert seq.urls == [u for u, _ in session.calls]


def test_single_work_by_doi_url_matches_pyalex(pyalex_old: Any):
    from pyalex import Works

    body = (AGENT_FIX / "openalex_work.json").read_bytes()
    session = pyalex_old(body)
    doi = "https://doi.org/10.1021/acsnano.2c01234"
    old = dict(Works()[doi])
    seq = Sequence((200, body))
    assert openalex_work(doi, get_json=seq.getter(hardened_get_json)) == old
    assert seq.urls == [u for u, _ in session.calls]


def test_openalex_key_stays_in_the_header_and_out_of_logs(caplog: pytest.LogCaptureFixture):
    key = "oa-secret-key-456"
    seq = Sequence((200, PAGES[0]), (200, PAGES[1]))
    with caplog.at_level(logging.DEBUG):
        list(openalex_pages(search="x", per_page=2, api_key=key, mailto=MAILTO, get_json=seq.getter(hardened_get_json)))
    assert all(key not in u for u in seq.urls) and key not in caplog.text
    assert all(r.headers["authorization"] == f"Bearer {key}" for r in seq.requests)
    for call in (hv.Works, hv.Authors):  # the harvest builders never read or send a key at all
        assert "api_key" not in call.__code__.co_varnames


# the previous implementation of harvest_rsoxs._paginate_works, verbatim (the oracle, run on real pyalex queries)
def _old_paginate_works(query_obj: Any, per_query: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        if hasattr(query_obj, "paginate"):
            for page in query_obj.paginate(per_page=min(50, max(per_query, 1))):
                results.extend(page or [])
                if len(results) >= per_query:
                    break
        else:
            results = list(query_obj.get(per_page=min(per_query, 50)) or [])
    except Exception:  # noqa: BLE001
        try:
            results = list(query_obj.get(per_page=min(per_query, 50)) or [])
        except Exception:  # noqa: BLE001
            return []
    return (results or [])[:per_query]


@pytest.mark.parametrize("per_query", [1, 2, 3, 50])
def test_harvest_paginate_works_matches_the_pyalex_version(per_query: int, pyalex_old: Any, monkeypatch: pytest.MonkeyPatch):
    from pyalex import Works

    session = pyalex_old(*PAGES)
    old = [dict(r) for r in _old_paginate_works(Works().search(SEARCH).filter(open_access={"is_oa": True}), per_query)]
    seq = Sequence((200, PAGES[0]), (200, PAGES[1]))
    monkeypatch.setattr(hv, "hardened_get_json", seq.getter(hardened_get_json))
    new = hv._paginate_works(hv.Works().search(SEARCH).filter(open_access={"is_oa": True}), per_query)
    assert new == old and len(new) == min(per_query, 3)
    assert seq.urls == [u for u, _ in session.calls]
    assert _query(seq.urls[0])["per-page"] == [str(min(50, max(per_query, 1)))]


def test_harvest_paginate_failure_falls_back_to_one_page_then_empty(monkeypatch: pytest.MonkeyPatch):
    # paginate fails (first answer is a 500) -> one ``get`` page (second answer); both failing -> []
    seq = Sequence((500, b"{}"), (200, PAGES[0]))
    monkeypatch.setattr(hv, "hardened_get_json", seq.getter(hardened_get_json))
    got = hv._paginate_works(hv.Works().search(SEARCH), 5)
    assert [w["id"] for w in got] == ["https://openalex.org/W3100000001", "https://openalex.org/W3100000002"]
    assert "cursor=" in seq.urls[0] and "cursor=" not in seq.urls[1] and _query(seq.urls[1])["per-page"] == ["5"]
    down = Sequence((500, b"{}"))
    monkeypatch.setattr(hv, "hardened_get_json", down.getter(hardened_get_json))
    assert hv._paginate_works(hv.Works().search(SEARCH), 5) == [] and len(down.requests) == 2


def test_harvest_search_openalex_end_to_end_over_the_hardened_getter(monkeypatch: pytest.MonkeyPatch):
    seq = Sequence((200, PAGES[0]), (200, PAGES[1]))
    monkeypatch.setattr(hv, "hardened_get_json", seq.getter(hardened_get_json))
    out = hv.search_openalex(["RSoXS"], 3, MAILTO, require_pdf=False, classifier=lambda title, abstract: "A")
    assert [w["id"] for w in out] == [f"https://openalex.org/W310000000{i}" for i in (1, 2, 3)]
    assert _query(seq.urls[0])["filter"] == ["open_access.is_oa:true"] and _query(seq.urls[0])["search"] == ["RSoXS"]
    assert all(r.headers["from"] == MAILTO for r in seq.requests)


def test_search_openalex_publications_matches_pyalex(pyalex_old: Any, monkeypatch: pytest.MonkeyPatch):
    from pyalex import Works

    monkeypatch.setenv("OPENALEX_EMAIL", MAILTO)
    session = pyalex_old(PAGES[0], email=MAILTO)
    query = "GISAXS  of polymer films"
    old_works = [dict(w) for w in Works().search(api_mod._sanitize_openalex_search(query)).get(per_page=min(3 * 2, 50))]
    expected = [p for p in (api_mod._openalex_work_to_publication(w) for w in old_works)
                if p.get("paper_title") or p.get("doi")]
    seq = Sequence((200, PAGES[0]))
    monkeypatch.setattr(api_mod, "hardened_get_json", seq.getter(hardened_get_json))
    assert api_mod._search_openalex_publications(query, 3) == expected and expected
    assert _without_mailto(seq.urls[0]) == session.calls[0][0]
    assert seq.requests[0].headers["from"] == session.calls[0][1]["From"] == MAILTO

    monkeypatch.setattr(api_mod, "hardened_get_json", Sequence((500, b"")).getter(hardened_get_json))
    assert api_mod._search_openalex_publications(query, 3) == []  # failure is a miss, as before


def test_harvest_doi_lookup_matches_pyalex(pyalex_old: Any, monkeypatch: pytest.MonkeyPatch):
    from pyalex import Works

    body = (AGENT_FIX / "openalex_work.json").read_bytes()
    session = pyalex_old(body)
    doi = "https://doi.org/10.1021/acsnano.2c01234"
    expected = hv._normalize_openalex_work(dict(Works()[doi]))
    monkeypatch.setattr(hv, "_crossref_by_doi_or_title", lambda q: None)
    seq = Sequence((200, body))
    monkeypatch.setattr(hv, "hardened_get_json", seq.getter(hardened_get_json))
    assert hv._openalex_by_doi_or_title("10.1021/acsnano.2c01234", None) == expected and expected
    assert seq.urls == [u for u, _ in session.calls]


def test_harvest_author_search_matches_pyalex_urls(pyalex_old: Any, monkeypatch: pytest.MonkeyPatch):
    from pyalex import Authors, Works

    session = pyalex_old(_fx("openalex_authors.json"), PAGES[1])
    authors = [dict(a) for a in Authors().search("Cheng Wang").get(per_page=25)]
    aid = authors[0]["id"]
    list(Works().filter(authorships={"author": {"id": aid}}).search("RSoXS").paginate(per_page=5))
    empty = json.dumps({"meta": {"next_cursor": None}, "results": []}).encode()
    seq = Sequence((200, _fx("openalex_authors.json")), (200, PAGES[1]), (200, empty))
    monkeypatch.setattr(hv, "hardened_get_json", seq.getter(hardened_get_json))
    out = hv.search_openalex_authors(["Cheng Wang"], 5, None)
    assert seq.urls[:2] == [u for u, _ in session.calls]  # the authors page, then the first works page
    assert isinstance(out, list)  # which works survive the RSoXS classifier is the classifier's business, not the transport's


# --------------------------------------------------------------------------------------- arXiv
PAGE1, PAGE2, EMPTY = _fx("arxiv_page1.xml"), _fx("arxiv_page2.xml"), _fx("arxiv_empty.xml")


def _old_arxiv(answers: list[tuple[int, bytes]], query: str, max_results: int, *, sort: str, page_size: int,
               delay: float, retries: int, monkeypatch: pytest.MonkeyPatch) -> tuple[list[Any], list[str], list[float], Any]:
    import arxiv

    client = arxiv.Client(page_size=page_size, delay_seconds=delay, num_retries=retries)
    urls: list[str] = []
    sleeps: list[float] = []

    def get(url: str, **kw: Any) -> Any:
        urls.append(url)
        status, body = answers[min(len(urls), len(answers)) - 1]
        return SimpleNamespace(status_code=status, content=body)

    client._session.get = get  # type: ignore[method-assign]
    monkeypatch.setattr(arxiv.time, "sleep", sleeps.append)
    search = arxiv.Search(query=query, max_results=max_results, sort_by=getattr(arxiv.SortCriterion, sort),
                          sort_order=arxiv.SortOrder.Descending)
    try:
        return list(client.results(search)), urls, sleeps, None
    except Exception as exc:  # noqa: BLE001 - the oracle's failure mode is part of the comparison
        return [], urls, sleeps, exc


def _new_arxiv(answers: list[tuple[int, bytes]], query: str, max_results: int, *, sort_by: str, page_size: int,
               delay: float, retries: int) -> tuple[list[Any], list[str], list[float], Any]:
    seq = Sequence(*answers)
    sleeps: list[float] = []
    got: list[Any] = []
    try:
        for entry in arxiv_results(query, max_results, sort_by=sort_by, page_size=page_size, delay_seconds=delay,
                                   retries=retries, get_bytes=seq.getter(hardened_get_bytes), sleep=sleeps.append,
                                   clock=lambda: 0.0):
            got.append(entry)
    except JSONFetchError as exc:
        return got, seq.urls, sleeps, exc
    return got, seq.urls, sleeps, None


def _entry_matches(old: Any, new: dict[str, Any]) -> None:
    assert new["short_id"] == old.get_short_id() and new["pdf_url"] == old.pdf_url
    assert new["doi"] == str(old.doi or "") and new["title"] == old.title and new["summary"] == old.summary
    assert new["published"] == old.published and new["year"] == old.published.year


@pytest.mark.parametrize("sort,sort_by", [("SubmittedDate", "submittedDate"), ("Relevance", "relevance")])
def test_arxiv_pagination_sort_and_delay_match_the_package(sort: str, sort_by: str, monkeypatch: pytest.MonkeyPatch):
    answers = [(200, PAGE1), (200, PAGE2)]
    query = 'all:"RSoXS" AND ti:polymer'
    old, old_urls, old_sleeps, old_err = _old_arxiv(answers, query, 5, sort=sort, page_size=2, delay=15.0, retries=1,
                                                    monkeypatch=monkeypatch)
    new, new_urls, new_sleeps, new_err = _new_arxiv(answers, query, 5, sort_by=sort_by, page_size=2, delay=15.0,
                                                    retries=1)
    assert old_err is None and new_err is None
    assert new_urls == old_urls and len(new_urls) == 2  # page 1, then start=2 (byte-identical URLs)
    assert f"sortBy={sort_by}" in new_urls[0] and "start=2" in new_urls[1] and "max_results=2" in new_urls[0]
    assert len(new) == len(old) == 3
    for o, n in zip(old, new, strict=True):
        _entry_matches(o, n)
    assert [n["short_id"] for n in new] == ["2401.00001v1", "2312.09999v2", "cond-mat/0307001v1"]
    assert len(old_sleeps) == len(new_sleeps) == 1 and new_sleeps[0] == pytest.approx(old_sleeps[0], abs=0.5)
    assert new_sleeps[0] == 15.0


def test_arxiv_stops_at_max_results_without_another_request(monkeypatch: pytest.MonkeyPatch):
    answers = [(200, PAGE1), (200, PAGE2)]
    old, old_urls, _, _ = _old_arxiv(answers, "q", 2, sort="Relevance", page_size=2, delay=0, retries=1,
                                     monkeypatch=monkeypatch)
    new, new_urls, _, _ = _new_arxiv(answers, "q", 2, sort_by="relevance", page_size=2, delay=0, retries=1)
    assert len(old) == len(new) == 2 and new_urls == old_urls and len(new_urls) == 1


def test_arxiv_retry_rules_match_the_package(monkeypatch: pytest.MonkeyPatch):
    # an HTTP 503 on the first request, then a good first page: one retry of the same URL
    flaky = [(503, b""), (200, PAGE1)]
    old, old_urls, _, old_err = _old_arxiv(flaky, "q", 2, sort="Relevance", page_size=2, delay=0, retries=1,
                                           monkeypatch=monkeypatch)
    new, new_urls, _, new_err = _new_arxiv(flaky, "q", 2, sort_by="relevance", page_size=2, delay=0, retries=1)
    assert old_err is None and new_err is None and new_urls == old_urls and len(new_urls) == 2
    assert new_urls[0] == new_urls[1] and len(old) == len(new) == 2

    # permanent failure: the same number of attempts, then an error (arxiv.HTTPError / JSONFetchError)
    down = [(429, b"")]
    _, old_urls, _, old_err = _old_arxiv(down, "q", 5, sort="Relevance", page_size=2, delay=0, retries=1,
                                         monkeypatch=monkeypatch)
    _, new_urls, _, new_err = _new_arxiv(down, "q", 5, sort_by="relevance", page_size=2, delay=0, retries=1)
    assert old_err is not None and isinstance(new_err, JSONFetchError) and new_urls == old_urls and len(new_urls) == 2
    assert "HTTP 429" in str(new_err) and "429" in str(old_err)  # harvest_rsoxs._arxiv_is_throttle keys on the text
    assert hv._arxiv_is_throttle(new_err)

    # an empty page after the first one is retried like an error, then fails; entries before it were yielded
    empty_later = [(200, PAGE1), (200, EMPTY)]
    old, old_urls, _, old_err = _old_arxiv(empty_later, "q", 5, sort="Relevance", page_size=2, delay=0, retries=1,
                                           monkeypatch=monkeypatch)
    new, new_urls, _, new_err = _new_arxiv(empty_later, "q", 5, sort_by="relevance", page_size=2, delay=0, retries=1)
    assert old_err is not None and isinstance(new_err, JSONFetchError) and new_urls == old_urls and len(new_urls) == 3
    assert len(new) == 2  # page one was delivered before the failure (the harvest keeps and dedupes these)


def test_arxiv_empty_first_page_is_a_clean_stop(monkeypatch: pytest.MonkeyPatch):
    old, old_urls, _, old_err = _old_arxiv([(200, EMPTY)], "q", 5, sort="Relevance", page_size=2, delay=0, retries=1,
                                           monkeypatch=monkeypatch)
    new, new_urls, _, new_err = _new_arxiv([(200, EMPTY)], "q", 5, sort_by="relevance", page_size=2, delay=0, retries=1)
    assert old_err is None and new_err is None and old == new == [] and new_urls == old_urls and len(new_urls) == 1


def test_arxiv_hits_map_to_the_same_harvest_work_as_package_results(monkeypatch: pytest.MonkeyPatch):
    answers = [(200, PAGE1), (200, PAGE2)]
    old, *_ = _old_arxiv(answers, "q", 5, sort="SubmittedDate", page_size=2, delay=0, retries=1, monkeypatch=monkeypatch)
    new, *_ = _new_arxiv(answers, "q", 5, sort_by="submittedDate", page_size=2, delay=0, retries=1)
    old_works = [hv._work_from_arxiv_result(r) for r in old]
    new_works = [hv._work_from_arxiv_result(ArxivHit.from_entry(e)) for e in new]
    assert new_works == old_works and all(old_works)
    assert old_works[0]["publication_date"] == "2024-01-15" and old_works[1]["doi"] == "https://doi.org/10.1021/acs.macromol.3c09999"
    assert old_works[2]["doi"] == "https://doi.org/10.48550/arXiv.cond-mat/0307001v1"


def test_harvest_search_arxiv_end_to_end_over_the_hardened_getter(monkeypatch: pytest.MonkeyPatch):
    answers = [(200, PAGE1), (200, PAGE2)]
    old, old_urls, _, _ = _old_arxiv(answers, 'all:"RSoXS"', 5, sort="SubmittedDate", page_size=5, delay=0, retries=1,
                                     monkeypatch=monkeypatch)
    seq = Sequence(*answers)
    monkeypatch.setattr(hv, "hardened_get_bytes", seq.getter(hardened_get_bytes))
    monkeypatch.setattr(hv, "ARXIV_DELAY_SECONDS", 0.0)
    got = hv.search_arxiv(['all:"RSoXS"'], 5, classifier=lambda title, abstract: "A")
    # the API returned 2 of 3 on page one, so both the package and the helper ask for the next page
    assert seq.urls == old_urls and len(seq.urls) == 2  # page one (2 of 3), then start=2
    assert [w["id"] for w in got] == [hv._work_from_arxiv_result(r)["id"] for r in old]  # type: ignore[index]


def test_harvest_title_lookup_matches_the_package(monkeypatch: pytest.MonkeyPatch):
    title = "Resonant soft X-ray scattering of a conjugated polymer: a subtitle"
    query = f'ti:"{title.split(":")[0].strip()}"'
    answers = [(200, PAGE1)]
    old, old_urls, _, _ = _old_arxiv(answers, query, 5, sort="Relevance", page_size=5, delay=0, retries=1,
                                     monkeypatch=monkeypatch)
    expected = [str(r.pdf_url) for r in old if hv.titles_near_duplicate(title, str(r.title or ""), threshold=0.72)
                and hv._is_reliable_pdf_url(str(r.pdf_url or ""))]
    seq = Sequence(*answers)
    monkeypatch.setattr(hv, "hardened_get_bytes", seq.getter(hardened_get_bytes))
    monkeypatch.setattr(hv, "ARXIV_DELAY_SECONDS", 0.0)
    assert hv._arxiv_pdf_urls_for_title(title) == expected and expected
    assert seq.urls == old_urls


def test_download_pdfs_search_arxiv_matches_the_package_defaults(monkeypatch: pytest.MonkeyPatch):
    import arxiv

    answers = [(200, PAGE1), (200, PAGE2)]
    client = arxiv.Client()  # the old call: package defaults (100 / 3 s / 3 retries)
    urls: list[str] = []

    def get(url: str, **kw: Any) -> Any:
        urls.append(url)
        return SimpleNamespace(status_code=200, content=answers[min(len(urls), 2) - 1][1])

    client._session.get = get  # type: ignore[method-assign]
    old = list(client.results(arxiv.Search(query="polymers", max_results=500, sort_by=arxiv.SortCriterion.Relevance,
                                           sort_order=arxiv.SortOrder.Descending)))
    seq = Sequence(*answers)
    monkeypatch.setattr(ld, "hardened_get_bytes", seq.getter(hardened_get_bytes))
    monkeypatch.setattr(ld.time, "sleep", lambda s: None)
    new = list(dp.search_arxiv("polymers", 500))
    assert seq.urls == urls and "max_results=100" in urls[0]
    assert [(h.get_short_id(), h.doi, h.pdf_url) for h in new] == [(r.get_short_id(), str(r.doi or ""), r.pdf_url)
                                                                   for r in old]


def test_download_pdfs_openalex_workflow_queries_a_title_search(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # The old chain (``Works().search(title=..).per_page(n).execute()``) raises TypeError on pyalex 0.21, so there is
    # no old behaviour to compare; this pins the new request (title.search filter, cursor walk) and the file rules.
    seq = Sequence((200, PAGES[0]), (200, PAGES[1]))
    monkeypatch.setattr(ld, "hardened_get_json", seq.getter(hardened_get_json))
    got: list[tuple[str, str]] = []
    monkeypatch.setattr(dp, "download_pdf", lambda url, dest: got.append((url, Path(dest).name)) or True)
    monkeypatch.setattr(dp.time, "sleep", lambda s: None)
    dp.run_openalex_workflow("soft x-ray", str(tmp_path), 3)
    assert _query(seq.urls[0])["filter"] == ["title.search:soft x-ray"] and _query(seq.urls[0])["per-page"] == ["3"]
    assert got == []  # no work in the fixtures has both a DOI and a primary_location pdf_url


# --------------------------------------------------------------------------------------- Unpaywall in harvest_rsoxs
def test_harvest_unpaywall_step_matches_the_requests_oracle(monkeypatch: pytest.MonkeyPatch):
    body = (AGENT_FIX / "unpaywall.json").read_bytes()
    paper = {"doi": "https://doi.org/10.1021/acsnano.2c01234", "pdf_urls": []}
    muted = {"_openalex_oa_urls_for_doi": lambda *a, **k: ([], None, [])}
    for name, fn in muted.items():
        monkeypatch.setattr(hv, name, fn)
    for name in ("fetch_osti_oa_pdfs", "fetch_europepmc_search_oa_pdfs", "fetch_crossref_tdm_pdfs"):
        monkeypatch.setattr(hv.dl, name, lambda doi: ([], []))
    monkeypatch.setattr(hv.dl, "fetch_core_oa_pdfs", lambda doi, api_key=None: ([], []))
    seen: list[requests.PreparedRequest] = []

    def fake_get(url: str, params: dict[str, str], timeout: float, headers: dict[str, str]) -> Any:
        seen.append(requests.Request("GET", url, params=params).prepare())
        return SimpleNamespace(ok=True, status_code=200, json=lambda: json.loads(body))

    monkeypatch.setattr(dp.requests, "get", fake_get)
    monkeypatch.setattr(hv, "unpaywall_oa_pdfs", lambda doi, email, **kw: dp.fetch_unpaywall_oa_pdfs(doi, email))
    old = hv._resolve_oa_pdfs(dict(paper), MAILTO)  # the previous step: requests-based fetch_unpaywall_oa_pdfs
    monkeypatch.undo()

    for name, fn in muted.items():
        monkeypatch.setattr(hv, name, fn)
    for name in ("fetch_osti_oa_pdfs", "fetch_europepmc_search_oa_pdfs", "fetch_crossref_tdm_pdfs"):
        monkeypatch.setattr(hv.dl, name, lambda doi: ([], []))
    monkeypatch.setattr(hv.dl, "fetch_core_oa_pdfs", lambda doi, api_key=None: ([], []))
    seq = Sequence((200, body))
    monkeypatch.setattr(hv, "hardened_get_json", seq.getter(hardened_get_json))
    new = hv._resolve_oa_pdfs(dict(paper), MAILTO)
    assert new == old and old[0]
    assert seq.urls == [seen[0].url]  # byte-identical Unpaywall URL


# --------------------------------------------------------------------------------------- policy
@pytest.mark.parametrize("call", ["pages", "arxiv"])
def test_new_hops_check_policy_on_every_redirect(call: str):
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/steal"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(JSONFetchError, match="blocked URL"):
        if call == "pages":
            list(openalex_pages(search="q", per_page=5, get_json=partial(hardened_get_json, client=client, policy=PUBLIC)))
        else:
            list(arxiv_results("q", 5, retries=3, get_bytes=partial(hardened_get_bytes, client=client, policy=PUBLIC)))
    assert hosts == ["api.openalex.org" if call == "pages" else "export.arxiv.org"]  # one request: not retried

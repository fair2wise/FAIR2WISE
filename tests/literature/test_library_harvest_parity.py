"""Old (``arxiv`` package / ``OpenAlexClient``) versus new (hardened helpers) for the Library Harvester, offline.

Follow-on to ``test_harvest_metadata_parity.py`` (H8 legacy_ports_plan P1d). The real old clients are the oracles:
the ``arxiv`` package with its HTTP session replaced, and :class:`OpenAlexClient` over an ``httpx.MockTransport``,
both driven by hand-written golden fixtures (``fixtures/harvest_metadata``). The old searcher bodies are kept here
verbatim as the reference; the new ``arxiv_searcher`` / ``openalex_searcher`` get the same answers through the
hardened getters. Compared: request URLs (the key moves from the ``api_key`` parameter to the Authorization header,
the one owner-decided difference), candidates, and the pacing/429 rules. No test here touches the network.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.modules.library.harvest import Candidate, arxiv_queries, arxiv_searcher, clean_doi
from app.modules.literature import legacy_download as ld
from app.modules.literature.legacy_download import JSONFetchError, hardened_get_bytes, hardened_get_json
from app.modules.literature.safe_fetch import NetworkPolicy

FIX = Path(__file__).parent / "fixtures" / "harvest_metadata"
PUBLIC = NetworkPolicy(resolver=lambda host: ["93.184.216.34"])
PAGE1, PAGE2, EMPTY = ((FIX / n).read_bytes() for n in ("arxiv_page1.xml", "arxiv_page2.xml", "arxiv_empty.xml"))


@pytest.fixture(autouse=True)
def _limiter_on_a_fake_clock():
    """A fresh process-wide arXiv limiter on a fake clock per test; its pauses are recorded in ``ld.PAUSES``."""
    clock = [0.0]
    pauses: list[float] = []

    def sleep(seconds: float) -> None:
        pauses.append(seconds)
        clock[0] += seconds

    original = ld.ARXIV_LIMITER
    ld.ARXIV_LIMITER = ld.RateLimiter(ld.ARXIV_MIN_INTERVAL_SECONDS, clock=lambda: clock[0], sleep=sleep)
    yield pauses
    ld.ARXIV_LIMITER = original


class Wire:
    """An ``httpx`` mock transport answering the n-th request with the n-th ``(status, body, headers)``; records requests."""

    def __init__(self, *answers: tuple[int, bytes] | tuple[int, bytes, dict[str, str]]) -> None:
        self.answers, self.requests = list(answers), []

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers[min(len(self.requests), len(self.answers)) - 1]
        return httpx.Response(answer[0], content=answer[1], headers=answer[2] if len(answer) == 3 else None)  # type: ignore[misc]

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handler))

    def getter(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        return partial(fn, client=self.client(), policy=PUBLIC)

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


# ------------------------------------------------------------------------------------------------ arXiv
def old_arxiv_searcher(client: Any) -> Callable[[str, int], list[Candidate]]:
    """The pre-port ``arxiv_searcher`` body, verbatim, on an injected real ``arxiv.Client``."""
    import arxiv

    def search(topic: str, per_query: int) -> list[Candidate]:
        found: list[Candidate] = []
        for query in arxiv_queries(topic):
            request = arxiv.Search(query=query, max_results=per_query, sort_by=arxiv.SortCriterion.SubmittedDate,
                                   sort_order=arxiv.SortOrder.Descending)
            for result in client.results(request):
                arxiv_id = str(result.entry_id).rsplit("/", 1)[-1]
                found.append(Candidate(
                    title=" ".join(str(result.title).split()), source="arxiv", year=result.published.year,
                    doi=clean_doi(result.doi), arxiv_id=arxiv_id, abstract=" ".join(str(result.summary).split()),
                    landing_url=str(result.entry_id), pdf_urls=[f"https://arxiv.org/pdf/{arxiv_id}"]))
        return found

    return search


def _old_arxiv(answers: list[tuple[int, bytes]], topic: str, per_query: int,
               monkeypatch: pytest.MonkeyPatch) -> tuple[list[Candidate], list[str], list[float], Exception | None]:
    import arxiv

    client = arxiv.Client(page_size=min(per_query, 50), delay_seconds=3.0, num_retries=3)
    urls: list[str] = []
    sleeps: list[float] = []

    def get(url: str, **kw: Any) -> Any:
        urls.append(url)
        status, body = answers[min(len(urls), len(answers)) - 1]
        return SimpleNamespace(status_code=status, content=body)

    client._session.get = get  # type: ignore[method-assign]
    monkeypatch.setattr(arxiv.time, "sleep", sleeps.append)
    try:
        return old_arxiv_searcher(client)(topic, per_query), urls, sleeps, None
    except Exception as exc:  # noqa: BLE001 - the oracle's failure mode is part of the comparison
        return [], urls, sleeps, exc


def _new_arxiv(wire: Wire, topic: str, per_query: int) -> tuple[list[Candidate], Exception | None]:
    try:
        return arxiv_searcher(get_bytes=wire.getter(hardened_get_bytes))(topic, per_query), None
    except JSONFetchError as exc:
        return [], exc


def test_arxiv_searcher_matches_the_package_requests_and_candidates(monkeypatch: pytest.MonkeyPatch, _limiter_on_a_fake_clock):
    answers = [(200, PAGE1), (200, PAGE2)] * 2  # the phrase query, then the ANDed one: each is two pages
    topic = "resonant soft x-ray"
    old, old_urls, old_sleeps, old_err = _old_arxiv(answers, topic, 5, monkeypatch)
    wire = Wire(*answers)
    new, new_err = _new_arxiv(wire, topic, 5)

    assert old_err is None and new_err is None
    assert wire.urls == old_urls and len(old_urls) == 4  # page 1 and start=2 of both queries, byte-identical
    assert "sortBy=submittedDate" in old_urls[0] and "sortOrder=descending" in old_urls[0] and "max_results=5" in old_urls[0]
    assert new == old and len(new) == 6
    assert [c.arxiv_id for c in new[:3]] == ["2401.00001v1", "2312.09999v2", "0307001v1"]  # category dropped, as before
    assert new[0].title == "Resonant soft X-ray scattering of a conjugated polymer"
    assert new[1].doi == "10.1021/acs.macromol.3c09999" and new[0].doi is None
    assert new[0].landing_url == "http://arxiv.org/abs/2401.00001v1" and new[2].year == 2003
    # pacing: the package slept 3 s between its requests; the shared limiter does the same (no first-request pause)
    assert len(old_sleeps) == 3 and all(s == pytest.approx(3.0, abs=0.5) for s in old_sleeps)
    assert _limiter_on_a_fake_clock == [3.0, 3.0, 3.0]


def test_arxiv_per_query_page_size_is_capped_at_50_like_the_client(monkeypatch: pytest.MonkeyPatch):
    old, old_urls, _, _ = _old_arxiv([(200, EMPTY)], "rsoxs", 80, monkeypatch)
    wire = Wire((200, EMPTY))
    new, _ = _new_arxiv(wire, "rsoxs", 80)
    assert old == new == [] and wire.urls == old_urls and "max_results=50" in old_urls[0]


def test_arxiv_requests_are_three_seconds_apart_across_searches(_limiter_on_a_fake_clock):
    wire = Wire((200, EMPTY))
    search = arxiv_searcher(get_bytes=wire.getter(hardened_get_bytes))
    search("rsoxs", 5)
    search("rsoxs", 5)  # a second harvest right behind the first still waits
    assert _limiter_on_a_fake_clock == [3.0] and len(wire.urls) == 2


def test_arxiv_retries_and_failure_match_the_package(monkeypatch: pytest.MonkeyPatch):
    flaky = [(503, b""), (200, PAGE2)]
    old, old_urls, _, old_err = _old_arxiv(flaky, "rsoxs", 1, monkeypatch)
    wire = Wire(*flaky)
    new, new_err = _new_arxiv(wire, "rsoxs", 1)
    assert old_err is None and new_err is None and new == old and wire.urls == old_urls and len(old_urls) == 2

    down = [(429, b"")]  # no Retry-After: nothing says when to come back, so the same four attempts, then an error
    _, old_urls, _, old_err = _old_arxiv(down, "rsoxs", 1, monkeypatch)
    wire = Wire(*down)
    _, new_err = _new_arxiv(wire, "rsoxs", 1)
    assert old_err is not None and isinstance(new_err, JSONFetchError) and wire.urls == old_urls and len(old_urls) == 4


def test_arxiv_honours_retry_after_once(_limiter_on_a_fake_clock):
    wire = Wire((429, b"", {"Retry-After": "7"}), (200, PAGE2))
    found, err = _new_arxiv(wire, "rsoxs", 1)
    assert err is None and len(found) == 1 and len(wire.urls) == 2
    assert 7.0 in _limiter_on_a_fake_clock  # the header's wait, not only the 3 s spacing
    again = Wire((429, b"", {"Retry-After": "7"}))
    _, err = _new_arxiv(again, "rsoxs", 1)
    assert isinstance(err, JSONFetchError) and err.status == 429 and len(again.urls) == 4 * 2  # 4 attempts, one polite retry each


def test_arxiv_search_is_policy_checked_per_hop():
    blocked = partial(hardened_get_bytes, client=Wire((200, EMPTY)).client(),
                      policy=NetworkPolicy(resolver=lambda host: ["10.0.0.5"]))
    with pytest.raises(JSONFetchError, match="blocked URL"):
        arxiv_searcher(get_bytes=blocked)("rsoxs", 5)

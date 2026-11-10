"""Harvest politeness (owner decision 2026-11-09): arXiv spacing, ``Retry-After``, ``hardened_get_response``. No network."""

from __future__ import annotations

from functools import partial
import logging
import threading
from typing import Any

import httpx
import pytest

from app.modules.literature import legacy_download as ld
from app.modules.literature.legacy_download import (
    ARXIV_MIN_INTERVAL_SECONDS,
    RETRY_AFTER_CAP_SECONDS,
    HardenedResponse,
    JSONFetchError,
    RateLimiter,
    arxiv_results,
    arxiv_search,
    hardened_get_bytes,
    hardened_get_json,
    hardened_get_response,
    openalex_search,
    openalex_work,
    retry_after_wait,
    unpaywall_oa_pdfs,
)
from app.modules.literature.safe_fetch import NetworkPolicy

PUBLIC = NetworkPolicy(resolver=lambda host: ["93.184.216.34"])
ATOM = (b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
        b"<opensearch:totalResults>1</opensearch:totalResults><entry><id>http://arxiv.org/abs/2401.00001v1</id>"
        b"<updated>2024-01-15T00:00:00Z</updated><published>2024-01-15T00:00:00Z</published><title>T</title>"
        b'<summary>S</summary><link title="pdf" href="https://arxiv.org/pdf/2401.00001v1"/></entry></feed>')
WORKS = b'{"meta": {}, "results": [{"id": "W1", "relevance_score": 1.0}]}'
WORK = b'{"id": "W1"}'
UPW = b'{"best_oa_location": {"url_for_pdf": "https://arxiv.org/pdf/2201.01234", "host_type": "repository"}}'


class Sequence:
    """Answers the n-th request with the n-th ``(status, body, headers)``; records requests."""

    def __init__(self, *answers: tuple[int, bytes, dict[str, str]]):
        self.answers, self.requests = list(answers), []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body, headers = self.answers[min(len(self.requests), len(self.answers)) - 1]
        return httpx.Response(status, content=body, headers=headers)

    def getter(self, fn: Any) -> Any:
        return partial(fn, client=httpx.Client(transport=httpx.MockTransport(self.handler)), policy=PUBLIC)

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


# ------------------------------------------------------------------------------------ hardened_get_response
def test_response_carries_status_headers_and_body_and_hides_headers_in_repr():
    seq = Sequence((429, b"slow down", {"Retry-After": "7", "Set-Cookie": "secret=1"}))
    answer = seq.getter(hardened_get_response)("https://api.openalex.org/works?search=x")
    assert isinstance(answer, HardenedResponse) and answer.status == 429
    assert answer.body == b""  # a non-2xx body is not read
    assert answer.headers["retry-after"] == "7" and answer.retry_after_seconds() == 7.0
    assert "secret" not in repr(answer) and "Retry-After" not in repr(answer) and "429" in repr(answer)

    ok = Sequence((200, WORK, {"X-Rate": "1"}))
    answer = ok.getter(hardened_get_response)("https://api.openalex.org/works/W1")
    assert answer.status == 200 and answer.body == WORK and answer.headers["x-rate"] == "1"
    assert answer.retry_after_seconds() is None


def test_response_keeps_the_egress_rules():
    seq = Sequence((302, b"", {"location": "http://127.0.0.1/steal"}))
    with pytest.raises(JSONFetchError, match="blocked URL"):
        seq.getter(hardened_get_response)("https://api.openalex.org/works")
    big = Sequence((200, b"x" * 4096, {}))
    with pytest.raises(JSONFetchError, match="too large"):
        big.getter(hardened_get_response)("https://api.openalex.org/works", max_bytes=1024)
    with pytest.raises(JSONFetchError, match="blocked URL"):
        hardened_get_response("http://10.0.0.1/x", policy=PUBLIC)


def test_get_bytes_is_unchanged_but_its_error_carries_status_and_retry_after():
    seq = Sequence((503, b"", {"Retry-After": "12"}))
    with pytest.raises(JSONFetchError, match="^HTTP 503$") as info:
        seq.getter(hardened_get_bytes)("https://export.arxiv.org/api/query?x=1")
    assert info.value.status == 503 and info.value.retry_after == 12.0
    plain = Sequence((500, b"oops", {}))
    with pytest.raises(JSONFetchError, match="^HTTP 500$") as info:
        plain.getter(hardened_get_json)("https://api.unpaywall.org/v2/10.1/x")
    assert info.value.status == 500 and info.value.retry_after is None
    assert seq.getter(hardened_get_bytes).func is hardened_get_bytes  # the public getter, same signature
    assert JSONFetchError("not valid JSON").status is None  # other failures carry nothing
    ok = Sequence((200, WORK, {}))
    assert ok.getter(hardened_get_bytes)("https://api.openalex.org/works/W1") == WORK


@pytest.mark.parametrize("value,expected", [("30", 30.0), ("0", 0.0), (" 5 ", 5.0), ("", None), ("soon", None),
                                            ("Wed, 21 Oct 2015 07:28:00 GMT", 0.0), ("-3", None)])
def test_retry_after_header_parsing(value: str, expected: float | None):
    assert HardenedResponse(b"", 429, {"retry-after": value}).retry_after_seconds() == expected


def test_retry_after_http_date_is_relative_to_now():
    answer = HardenedResponse(b"", 503, {"retry-after": "Thu, 01 Jan 2026 00:01:40 GMT"})
    assert answer.retry_after_seconds(now=lambda: 1767225600.0) == 100.0  # 2026-01-01T00:00:00Z + 100 s


@pytest.mark.parametrize("exc,expected", [
    (JSONFetchError("HTTP 429", status=429, retry_after=5.0), 5.0),
    (JSONFetchError("HTTP 503", status=503, retry_after=0.0), 0.0),
    (JSONFetchError("HTTP 429", status=429, retry_after=3600.0), RETRY_AFTER_CAP_SECONDS),
    (JSONFetchError("HTTP 429", status=429), None),  # no Retry-After (a daily quota): fails as before
    (JSONFetchError("HTTP 503", status=503), None),
    (JSONFetchError("HTTP 500", status=500, retry_after=5.0), None),
    (JSONFetchError("network error (ConnectError)"), None),
    (RuntimeError("HTTP 429"), None),
])
def test_retry_after_wait_rule(exc: BaseException, expected: float | None):
    assert retry_after_wait(exc) == expected


# ------------------------------------------------------------------------------------ OpenAlex / Unpaywall
def test_openalex_waits_retry_after_once_and_repeats_the_identical_request(caplog: pytest.LogCaptureFixture):
    seq = Sequence((429, b"", {"Retry-After": "9"}), (200, WORKS, {}))
    sleeps: list[float] = []
    with caplog.at_level(logging.INFO):
        rows = openalex_search("graphene", 5, mailto="curator@lbl.gov", api_key="oa-secret-key-789",
                               get_json=seq.getter(hardened_get_json), sleep=sleeps.append)
    assert rows == [{"id": "W1", "relevance_score": 1.0}] and sleeps == [9.0]
    assert seq.urls[0] == seq.urls[1] and len(seq.urls) == 2
    assert dict(seq.requests[0].headers) == dict(seq.requests[1].headers)
    assert seq.requests[1].headers["authorization"] == "Bearer oa-secret-key-789"
    assert "oa-secret-key-789" not in caplog.text
    ours = [r.getMessage() for r in caplog.records if r.name == ld.logger.name]
    assert len(ours) == 1 and "api.openalex.org" in ours[0] and "429" in ours[0]
    assert "mailto" not in ours[0] and "Retry-After:" not in ours[0]  # host and status only: no URL, no header values


def test_openalex_second_limit_fails_as_before_and_cap_applies():
    seq = Sequence((429, b"", {"Retry-After": "600"}), (429, b"", {"Retry-After": "600"}), (200, WORKS, {}))
    sleeps: list[float] = []
    with pytest.raises(JSONFetchError, match="HTTP 429"):
        openalex_work("W1", get_json=seq.getter(hardened_get_json), sleep=sleeps.append)
    assert sleeps == [RETRY_AFTER_CAP_SECONDS] and len(seq.urls) == 2


def test_openalex_limit_without_retry_after_is_not_retried():
    seq = Sequence((429, b"", {}), (200, WORKS, {}))
    sleeps: list[float] = []
    with pytest.raises(JSONFetchError, match="HTTP 429"):
        openalex_search("x", 5, get_json=seq.getter(hardened_get_json), sleep=sleeps.append)
    assert sleeps == [] and len(seq.urls) == 1


def test_unpaywall_waits_retry_after_once_then_swallows_like_before():
    seq = Sequence((503, b"", {"Retry-After": "4"}), (200, UPW, {}))
    sleeps: list[float] = []
    urls, sources = unpaywall_oa_pdfs("10.1/x", "curator@lbl.gov", get_json=seq.getter(hardened_get_json), sleep=sleeps.append)
    assert urls == ["https://arxiv.org/pdf/2201.01234"] and sleeps == [4.0] and seq.urls[0] == seq.urls[1]
    again = Sequence((503, b"", {"Retry-After": "4"}))
    sleeps.clear()
    assert unpaywall_oa_pdfs("10.1/x", "curator@lbl.gov", get_json=again.getter(hardened_get_json), sleep=sleeps.append) == ([], [])
    assert sleeps == [4.0] and len(again.urls) == 2


def test_default_sleep_is_time_sleep_looked_up_at_call_time(monkeypatch: pytest.MonkeyPatch):
    slept: list[float] = []
    monkeypatch.setattr(ld.time, "sleep", slept.append)
    seq = Sequence((429, b"", {"Retry-After": "2"}), (200, WORK, {}))
    assert openalex_work("W1", get_json=seq.getter(hardened_get_json)) == {"id": "W1"} and slept == [2.0]


# ------------------------------------------------------------------------------------ arXiv spacing
def test_rate_limiter_spaces_waits_and_marks():
    ft = FakeTime()
    limiter = RateLimiter(3.0, clock=ft.clock, sleep=ft.sleep)
    assert limiter.wait() == 0.0  # first request: no pause
    limiter.mark()
    ft.now += 1.0
    assert limiter.wait() == 2.0 and ft.sleeps == [2.0]
    limiter.mark()
    ft.now += 10.0
    assert limiter.wait(15.0) == 5.0  # a caller's longer delay wins over the floor
    assert limiter.wait(1.0) == 3.0  # a shorter one is floored


def test_rate_limiter_is_shared_across_threads():
    ft = FakeTime()
    lock = threading.Lock()

    def sleep(seconds: float) -> None:
        with lock:
            ft.sleep(seconds)

    limiter = RateLimiter(3.0, clock=ft.clock, sleep=sleep)
    threads = [threading.Thread(target=lambda: (limiter.wait(), limiter.mark())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ft.sleeps) == 3 and all(s == pytest.approx(3.0) for s in ft.sleeps)  # 4 requests, 3 pauses of 3 s


def test_arxiv_requests_are_spaced_three_seconds_across_searches_in_one_process(monkeypatch: pytest.MonkeyPatch):
    ft = FakeTime()
    shared = RateLimiter(ARXIV_MIN_INTERVAL_SECONDS, clock=ft.clock, sleep=ft.sleep)
    monkeypatch.setattr(ld, "ARXIV_LIMITER", shared)
    seq = Sequence((200, ATOM, {}))
    get_bytes = seq.getter(hardened_get_bytes)
    assert len(arxiv_search("a", 1, get_bytes=get_bytes)) == 1  # the agent's call shape (delay_seconds 0 -> floor 3)
    ft.now += 0.5
    assert len(arxiv_search("b", 1, get_bytes=get_bytes)) == 1
    assert len(list(arxiv_results("c", 1, delay_seconds=1.0, get_bytes=get_bytes))) == 1
    assert ft.sleeps == [pytest.approx(2.5), pytest.approx(3.0)] and len(seq.urls) == 3
    assert [u.split("search_query=")[1][0] for u in seq.urls] == ["a", "b", "c"]  # same URLs, only paused


def test_arxiv_floor_applies_between_pages_and_a_longer_delay_is_kept():
    two_pages = ATOM.replace(b"<opensearch:totalResults>1<", b"<opensearch:totalResults>2<")
    ft = FakeTime()
    seq = Sequence((200, two_pages, {}), (200, ATOM, {}))
    got = list(arxiv_results("q", 2, page_size=1, delay_seconds=0.0, get_bytes=seq.getter(hardened_get_bytes),
                             sleep=ft.sleep, clock=ft.clock))
    assert len(got) == 2 and ft.sleeps == [3.0] and len(seq.urls) == 2
    ft = FakeTime()
    seq = Sequence((200, two_pages, {}), (200, ATOM, {}))
    list(arxiv_results("q", 2, page_size=1, delay_seconds=15.0, get_bytes=seq.getter(hardened_get_bytes),
                       limiter=RateLimiter(ARXIV_MIN_INTERVAL_SECONDS, clock=ft.clock, sleep=ft.sleep)))
    assert ft.sleeps == [15.0]


def test_arxiv_honours_retry_after_once_on_top_of_the_package_retry_rule():
    ft = FakeTime()
    seq = Sequence((503, b"", {"Retry-After": "10"}), (200, ATOM, {}))
    got = list(arxiv_results("q", 1, retries=0, get_bytes=seq.getter(hardened_get_bytes), sleep=ft.sleep, clock=ft.clock))
    assert len(got) == 1 and len(seq.urls) == 2 and seq.urls[0] == seq.urls[1]
    assert dict(seq.requests[0].headers) == dict(seq.requests[1].headers)
    assert ft.sleeps == [10.0]  # the Retry-After pause already covers the 3 s spacing


def test_arxiv_short_retry_after_is_topped_up_to_the_spacing():
    ft = FakeTime()
    seq = Sequence((429, b"", {"Retry-After": "1"}), (200, ATOM, {}))
    limiter = RateLimiter(ARXIV_MIN_INTERVAL_SECONDS, clock=ft.clock, sleep=ft.sleep)
    got = list(arxiv_results("q", 1, retries=0, get_bytes=seq.getter(hardened_get_bytes), limiter=limiter))
    assert len(got) == 1 and ft.sleeps == [1.0, 2.0] and len(seq.urls) == 2


def test_arxiv_bare_429_keeps_the_package_attempt_count():
    ft = FakeTime()
    seq = Sequence((429, b"", {}))
    with pytest.raises(JSONFetchError, match="HTTP 429"):
        list(arxiv_results("q", 1, retries=1, get_bytes=seq.getter(hardened_get_bytes), sleep=ft.sleep, clock=ft.clock))
    assert len(seq.urls) == 2 and ft.sleeps == [3.0]  # 1 try + 1 retry, spaced; no Retry-After, no extra attempt


def test_shared_limiter_defaults_are_the_real_clock_and_sleep(monkeypatch: pytest.MonkeyPatch):
    slept: list[float] = []
    monkeypatch.setattr(ld.time, "sleep", slept.append)
    limiter = RateLimiter(3.0)
    limiter.mark()
    limiter.wait()
    assert len(slept) == 1 and 0.0 < slept[0] <= 3.0

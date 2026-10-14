"""T05 slice 2: OpenAlex/S2/Crossref/arXiv/DDG providers, offline (httpx.MockTransport)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx
import pytest

from app.modules.contracts.errors import ErrorCode
from app.modules.literature import identifiers
from app.modules.literature.openalex import (
    EXTERNAL_RESULT, OpenAlexClient, OpenAlexQuotaPaused, OpenAlexSettings, ProviderUnavailable, QuotaBreaker,
    ResponseCache, is_not_found, is_quota_paused,
)
from app.modules.literature.resolver_batch import BatchResolver, ResolverCache, TokenBucket, title_similarity
from app.modules.literature.web_search import search_all
from tests.literature.lit_fakes import Router, openalex_work

OA = "api.openalex.org"
S2 = "api.semanticscholar.org"
CR = "api.crossref.org"


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_client(router: Router, *, clock: Optional[Clock] = None, **settings) -> OpenAlexClient:
    clock = clock or Clock()
    return OpenAlexClient(router.client(), cache=ResponseCache(":memory:", clock=clock),
                          breaker=QuotaBreaker(clock), settings=OpenAlexSettings(**settings),
                          sleep=lambda s: None, clock=clock)


def test_search_marks_results_external_and_caches():
    router = Router().add("GET", OA, "/works", (200, {"results": [openalex_work("10.1/a", "Lamellar films")]}))
    client = make_client(router, api_key="SECRET", mailto="real.person@lbl.gov")
    papers = client.search_papers("lamellar", technique="GISAXS", year_from=2010, year_to=2020)
    assert papers[0]["doi"] == "10.1/a" and papers[0]["record_kind"] == EXTERNAL_RESULT
    assert papers[0]["abstract"].startswith("small angle")
    params = router.calls[0].url.params
    assert params["search"] == "lamellar GISAXS"
    assert params["filter"] == "publication_year:>2009,publication_year:<2021"
    assert params["api_key"] == "SECRET" and params["mailto"] == "real.person@lbl.gov"
    client.search_papers("lamellar", technique="GISAXS", year_from=2010, year_to=2020)
    assert router.count(OA) == 1  # second answer from cache; key excludes credentials


def test_placeholder_email_is_not_sent():
    router = Router().add("GET", OA, "/works", (200, {"results": []}))
    make_client(router, mailto="your-email@example.com").search_papers("x")
    assert "mailto" not in router.calls[0].url.params


def test_unavailable_provider_is_not_an_empty_result():
    router = Router().add("GET", OA, "/works", (503, {"error": "down"}))
    client = make_client(router)
    with pytest.raises(ProviderUnavailable) as err:
        client.search_papers("anything")
    assert err.value.code is ErrorCode.UNAVAILABLE and err.value.retryable
    assert router.count(OA) == 3  # retried, then gave up
    router.routes.clear()
    router.add("GET", OA, "/works", (200, {"results": []}))
    assert client.search_papers("anything") == []  # failures are never cached


def test_quota_pause_opens_breaker_and_cache_still_answers(caplog):
    clock = Clock()
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        if request.url.params.get("search") == "cached":
            return httpx.Response(200, json={"results": [openalex_work("10.1/c", "Cached paper")]})
        return httpx.Response(429, headers={"Retry-After": "16902"}, text="Resets at midnight UTC")

    router = Router().add("GET", OA, "/works", handler)
    client = make_client(router, clock=clock, api_key="SECRET")
    assert client.search_papers("cached")
    with caplog.at_level(logging.INFO), pytest.raises(OpenAlexQuotaPaused) as err:
        client.search_papers("new topic")
    assert err.value.resume_at == pytest.approx(clock.now + 16902)
    assert "SECRET" not in caplog.text
    before = hits["n"]
    with pytest.raises(OpenAlexQuotaPaused):
        client.search_papers("another")
    assert hits["n"] == before  # no request spent rediscovering the quota
    assert client.search_papers("cached")  # cache answers while paused
    state = client.breaker.state()
    assert state and state["reason"] == "HTTP 429"
    clock.now += 17000
    assert client.breaker.state() is None


def test_short_retry_after_is_rate_limiting_not_quota():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"results": []})

    client = make_client(Router().add("GET", OA, "/works", handler))
    assert client.search_papers("x") == []
    assert client.breaker.state() is None


def test_402_pauses_until_midnight():
    client = make_client(Router().add("GET", OA, "/works", (402, {"error": "Payment required"})))
    data = client.fetch("works", {"search": "x"})
    assert is_quota_paused(data) and not data
    assert data.resume_at > client._clock()


def test_singleton_404_negative_cache():
    router = Router().add("GET", OA, "/works/doi:", (404, {"error": "not found"}))
    client = make_client(router)
    first = client.fetch("works/doi:10.9/missing")
    assert is_not_found(first)
    second = client.fetch("works/doi:10.9/missing")
    assert is_not_found(second) and router.count(OA) == 1


def test_lookup_dois_separates_not_found_from_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("10.1/ok"):
            return httpx.Response(200, json=openalex_work("10.1/ok", "Found"))
        if request.url.path.endswith("10.1/gone"):
            return httpx.Response(404)
        raise httpx.ConnectError("no route")

    client = make_client(Router().add("GET", OA, "/works/doi:", handler))
    lookup = client.lookup_dois(["10.1/ok", "https://doi.org/10.1/gone", "10.1234/down"])
    assert [p["doi"] for p in lookup.papers] == ["10.1/ok"]
    assert lookup.not_found == ["10.1/gone"] and lookup.unavailable == ["10.1234/down"]
    assert lookup.papers[0]["referenced_works"] == ["https://openalex.org/W9"]


def test_bulk_doi_batching_primes_single_doi_cache():
    def handler(request: httpx.Request) -> httpx.Response:
        dois = request.url.params["filter"][len("doi:"):].split("|")
        return httpx.Response(200, json={"results": [openalex_work(d, f"T {d}") for d in dois if d != "10.1/7"]})

    router = Router().add("GET", OA, "/works", handler)
    client = make_client(router)
    wanted = [f"10.1/{i}" for i in range(120)] + ["https://doi.org/10.1/3", "10.1/a|b"]
    result = client.fetch_works_by_doi(wanted)
    assert result.batches == 3 and "10.1/7" not in result.found and len(result.found) == 119
    sent = router.count(OA)
    hit = client.fetch("works", {"filter": "doi:10.1/5"})
    assert hit["results"][0]["doi"] == "https://doi.org/10.1/5" and router.count(OA) == sent


def test_bulk_stops_on_quota_and_reports_not_asked():
    client = make_client(Router().add("GET", OA, "/works", (429, {"error": "Insufficient credits"})))
    result = client.fetch_works_by_doi([f"10.2/{i}" for i in range(60)])
    assert result.stopped == "quota_paused" and len(result.not_asked) == 60 and result.resume_at


def test_citing_papers_and_related_unavailable():
    router = Router().add("GET", OA, "/works", (200, {"results": [openalex_work("10.1/z", "Citer")]}))
    router.add("POST", S2, "/graph/v1/paper/", (500, {"error": "busy"}))
    client = make_client(router)
    citing = client.find_citing_papers("W2741809807")
    assert citing[0]["title"] == "Citer"
    assert router.calls[0].url.params["filter"] == "cites:https://openalex.org/W2741809807"
    with pytest.raises(ProviderUnavailable) as err:
        client.find_related_papers("10.1/z")
    assert err.value.provider == "semantic_scholar"


def test_related_papers_shape():
    body = {"recommendedPapers": [{"title": "Rel", "year": 2021, "externalIds": {"DOI": "10.5/r"},
                                   "authors": [{"name": "B"}], "abstract": "x" * 900, "citationCount": 2}]}
    client = make_client(Router().add("POST", S2, "/graph/v1/paper/", (200, body)), s2_api_key="K")
    rel = client.find_related_papers("10.1/z", max_results=99)
    assert rel[0]["doi"] == "10.5/r" and len(rel[0]["abstract"]) == 500
    assert rel[0]["record_kind"] == EXTERNAL_RESULT


# -- batch resolver ------------------------------------------------------------------------------

@dataclass
class Ref:
    index: int
    title: str
    year: Optional[int] = None
    journal: str = ""
    volume: str = ""
    first_page: str = ""
    doi: str = ""

    @property
    def key(self) -> str:
        return f"title:{self.title.lower()}:{self.year}"


def _resolver(router: Router) -> BatchResolver:
    client = make_client(router)
    fast = TokenBucket(1e6, 1000)
    return BatchResolver(client, cache=ResolverCache(":memory:"), sleep=lambda s: None, buckets=(fast, fast, fast))


def test_batch_resolver_fallbacks_and_verified_negatives():
    def oa(request):
        if "10.1/oa" in request.url.path:
            return httpx.Response(200, json=openalex_work("10.1/oa", "OA"))
        return httpx.Response(404)

    def s2(request):
        ids = request.read().decode()
        return httpx.Response(200, json=[{"title": "S2 hit"} if "10.1/s2" in i else None
                                         for i in ids.split('"') if i.startswith("DOI:")])

    def cr(request):
        if "10.1/cr" in request.url.path:
            return httpx.Response(200, json={"message": {"DOI": "10.1/cr", "title": ["CR"]}})
        if "10.1/flaky" in request.url.path:
            return httpx.Response(503)
        return httpx.Response(404)

    router = Router().add("GET", OA, "/works/", oa).add("POST", S2, "/graph/v1/paper/batch", s2)
    router.add("GET", CR, "/works/", cr)
    resolver = _resolver(router)
    out = resolver.resolve_dois(["10.1/oa", "10.1/s2", "10.1/cr", "10.1/none", "10.1/flaky"])
    assert out["10.1/oa"].source == "openalex" and out["10.1/s2"].source == "semantic_scholar"
    assert out["10.1/cr"].source == "crossref"
    assert out["10.1/none"].record is None and out["10.1/none"].source == "none"   # verified negative
    assert "10.1/flaky" not in out                                                # could not ask: not cached
    assert resolver.cache.count(negative=True) == 1
    sent = len(router.calls)
    again = resolver.resolve_dois(["10.1/none", "10.1/oa"])
    assert again["10.1/none"].record is None and len(router.calls) == sent


def test_batch_resolver_respects_shared_quota_breaker():
    router = Router().add("GET", OA, "/works/", (429, {"error": "budget exceeded"}))
    router.add("POST", S2, "/graph/v1/paper/batch", (200, [None]))
    router.add("GET", CR, "/works/", (404, {}))
    resolver = _resolver(router)
    out = resolver.resolve_dois(["10.3/a"])
    assert resolver.openalex.breaker.state() is not None
    # OpenAlex could not be asked, so even "S2 and Crossref said no" is not a verified negative.
    assert "10.3/a" not in out
    assert resolver.cache.count() == 0


def test_reference_title_match_requires_title_and_year():
    def match(request):
        return httpx.Response(200, json={"data": [{"title": "Grazing incidence scattering of thin films",
                                                   "year": 2019}]})

    router = Router().add("GET", S2, "/graph/v1/paper/search/match", match)
    router.add("GET", CR, "/works", (200, {"message": {"items": []}}))
    resolver = _resolver(router)
    refs = [Ref(0, "Grazing-incidence scattering of thin films", 2019), Ref(1, "Grazing incidence scattering",
                                                                            2001)]
    out = resolver.resolve_references(refs)
    assert out[0].source == "s2_title" and 1 not in out
    assert title_similarity("A Study", "a-study") == 1.0


# -- web discovery -------------------------------------------------------------------------------

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>RSoXS of
 blends</title><id>http://arxiv.org/abs/2101.00001v1</id><summary>abs</summary></entry></feed>"""


def test_search_all_attribution_and_partial_outage():
    router = Router().add("GET", CR, "/works", (200, {"message": {"items": [
        {"DOI": "10.1/x", "title": ["Crossref hit"], "container-title": ["J"]}]}}))
    router.add("GET", "export.arxiv.org", "/api/query", (503, "down"))
    report = search_all("rsoxs", 5, http=router.client(), ddg=None)
    assert [r["source"] for r in report.results] == ["crossref"]
    assert report.providers["arxiv"].status == "unavailable" and report.providers["duckduckgo"].status == \
        "not_installed"
    assert report.partial and not report.all_unavailable
    assert report.results[0]["record_kind"] == EXTERNAL_RESULT


def test_search_all_total_outage_is_not_empty():
    router = Router().add("GET", CR, "/works", (500, "x")).add("GET", "export.arxiv.org", "/api/query", (500, "x"))

    def ddg(query, n):
        raise RuntimeError("ratelimited")

    report = search_all("q", http=router.client(), ddg=ddg)
    assert report.results == [] and report.all_unavailable


def test_search_all_arxiv_and_ddg_ok():
    router = Router().add("GET", "export.arxiv.org", "/api/query", (200, ATOM))
    router.add("GET", CR, "/works", (200, {"message": {"items": []}}))
    report = search_all("q", http=router.client(), ddg=lambda q, n: [{"title": "D", "href": "https://d.example"}])
    assert {r["source"] for r in report.results} == {"arxiv", "duckduckgo"}
    assert report.results[-1]["title"] == "RSoXS of blends"
    assert report.providers["crossref"].status == "empty"


# -- identifier resolution -----------------------------------------------------------------------

def test_identifier_resolution_distinguishes_outcomes():
    def handler(request: httpx.Request) -> httpx.Response:
        flt = request.url.params.get("filter", "")
        if flt == "doi:10.1234/found":
            return httpx.Response(200, json={"results": [openalex_work("10.1234/found", "F")]})
        if flt == "doi:10.48550/arXiv.2101.00001":
            return httpx.Response(200, json={"results": [openalex_work("10.48550/arXiv.2101.00001", "P")]})
        if flt == "doi:10.1234/down":
            return httpx.Response(502)
        return httpx.Response(200, json={"results": []})

    client = make_client(Router().add("GET", OA, "/works", handler))
    rec = identifiers.resolve(client, "https://doi.org/10.1234/found")
    assert rec["authors"][0] == {"name": "Ada Lovelace", "openalex_id": "A1", "affiliation": "LBNL"}
    assert rec["journal"] == "J. Synchrotron" and "is_oa" in rec and rec["abstract_snippet"]
    assert identifiers.resolve(client, "arXiv:2101.00001")["title"] == "P"
    with pytest.raises(identifiers.WorkNotFound):
        identifiers.resolve(client, "10.1234/missing")
    with pytest.raises(identifiers.ResolverUnavailable):
        identifiers.resolve(client, "10.1234/down")
    with pytest.raises(identifiers.UnrecognisedIdentifier):
        identifiers.resolve(client, "not an id")
    assert identifiers.classify_identifier("https://openalex.org/W123456") == {"kind": "openalex",
                                                                               "value": "W123456"}

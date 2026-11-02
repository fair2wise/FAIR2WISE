"""BatchResolver paths not covered by the provider tests: cache TTLs, retries, batches and DOI-less references."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.modules.literature import resolver_batch as rb
from app.modules.literature.resolver_batch import BatchResolver, Resolved, ResolverCache, TokenBucket
from tests.coverage_t27.helpers_literature import OA, CR, S2, Router, make_client, openalex_work


def _resolver(router, **kw):
    fast = TokenBucket(1e6, 1000)
    sleeps = []
    r = BatchResolver(make_client(router), cache=ResolverCache(":memory:"), sleep=sleeps.append,
                      buckets=(fast, fast, fast), **kw)
    r.slept = sleeps
    return r


@dataclass
class Ref:
    index: int
    title: str
    year: int | None = None
    journal: str = ""
    volume: str = ""
    first_page: str = ""
    doi: str = ""

    @property
    def key(self):
        return f"title:{rb._fold(self.title)}:{self.year}"


# ------------------------------------------------------------------ small units
def test_normalise_doi_prefixes():
    assert rb.normalise_doi("https://DOI.org/10.1/ABC") == "10.1/abc" and rb.normalise_doi("doi:10.2/x") == "10.2/x"
    assert rb.normalise_doi("https://dx.doi.org/10.3/y") == "10.3/y"
    assert rb.normalise_doi("http://dx.doi.org/10.3/y") == ""  # http://dx.doi.org is not a recognised prefix
    assert rb.normalise_doi("11.1/x") == "" and rb.normalise_doi("10.nosl") == "" and rb.normalise_doi(None) == ""


def test_token_bucket_blocks_until_refilled_and_validates_rate():
    with pytest.raises(ValueError):
        TokenBucket(0)
    t = {"now": 0.0}
    slept = []

    def sleep(s):
        slept.append(round(s, 3))
        t["now"] += s

    bucket = TokenBucket(2.0, 1, clock=lambda: t["now"], sleep=sleep)
    bucket.take()
    bucket.take()  # needs half a second to refill one token at 2/s
    assert slept == [0.5]
    assert TokenBucket(1.0, 0).capacity == 1


def test_cache_ttls_distinguish_positive_and_negative_rows_and_bad_timestamps(tmp_path):
    cache = ResolverCache(tmp_path / "sub" / "c.sqlite", positive_ttl=timedelta(days=90), negative_ttl=timedelta(days=30))
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    fresh = datetime.now(UTC).isoformat()
    cache.put(Resolved("doi:pos", "openalex", {"t": 1}, old))
    cache.put(Resolved("doi:neg", "none", None, old))
    cache.put(Resolved("doi:bad", "none", None, "not-a-date"))
    cache.put(Resolved("doi:new", "crossref", {"x": 1}, fresh, 0.5))
    assert cache.get("doi:pos").record == {"t": 1}          # positives live 90 days
    assert cache.get("doi:neg") is None                     # negatives only 30
    assert cache.get("doi:bad") is None and cache.get("doi:absent") is None
    assert cache.get("doi:new").cost_usd == 0.5
    assert (cache.count(), cache.count(negative=True), cache.count(negative=False)) == (4, 2, 2)
    cache.close()


def test_title_similarity_and_agreement_edges():
    assert rb.title_similarity("", "x") == 0.0 and rb.title_similarity("abc", "abc") == 1.0
    ref = Ref(0, "Grazing incidence scattering of thin films", 2019)
    assert rb._title_year_agree(ref, "Grazing-incidence scattering of thin films", None)
    assert rb._title_year_agree(ref, "Grazing incidence scattering of thin films", "n/a")  # unparsable year is tolerated
    assert not rb._title_year_agree(ref, "Grazing incidence scattering of thin films", 2015)
    assert rb._title_year_agree(Ref(0, ref.title, None), ref.title, 1999)
    assert not rb._title_year_agree(ref, "Completely different words here", 2019)
    assert rb._crossref_year({"published-print": {"date-parts": [[2020, 1]]}}) == 2020
    assert rb._crossref_year({"issued": {"date-parts": [[None]]}, "published-online": {"date-parts": [[2018]]}}) == 2018
    assert rb._crossref_year({}) is None


# ------------------------------------------------------------------ OpenAlex transport behaviour
def test_openalex_retries_transport_errors_then_gives_up():
    attempts = []

    def flaky(request):
        attempts.append(1)
        raise httpx.ConnectError("down")

    r = _resolver(Router().add("GET", OA, "/works/", flaky))
    assert r.openalex_singletons(["10.1/a"]) == {}
    assert len(attempts) == 3 and r.slept == [1, 2, 4]


def test_openalex_retry_after_and_server_errors_then_success():
    state = {"n": 0}

    def route(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "7"})
        if state["n"] == 2:
            return httpx.Response(500)
        return httpx.Response(200, json=openalex_work("10.1/a", "A"))

    r = _resolver(Router().add("GET", OA, "/works/", route))
    out = r.openalex_singletons(["10.1/a"])
    assert out["10.1/a"]["doi"] and r.slept == [7, 2] and r.requests["openalex"] == 3


def test_openalex_unexpected_status_and_quota_pause_stop_the_loop():
    r = _resolver(Router().add("GET", OA, "/works/", (400, {})))
    assert r.openalex_singletons(["10.1/a", "10.1/b"]) == {}
    q = _resolver(Router().add("GET", OA, "/works/", (429, {"error": "budget exceeded"})))
    assert q.openalex_singletons(["10.1/a", "10.1/b"]) == {}
    assert q.requests["openalex"] == 1  # the breaker stopped the second DOI from being asked
    assert q.openalex.breaker.state() is not None


def test_openalex_batch_handles_unsafe_dois_found_missing_and_failed_chunks():
    seen = []

    def works(request):
        if "filter" not in request.url.params:  # singleton lookups share the /works prefix
            return httpx.Response(404)
        flt = request.url.params["filter"]
        seen.append(flt)
        return httpx.Response(200, json={"results": [openalex_work("10.1/a", "A")]})

    router = Router().add("GET", OA, "/works", works).add("GET", OA, "/works/", (404, {}))
    r = _resolver(router)
    out = r.openalex_batch(["10.1/a", "10.1/b", "10.1/p|q", "10.1/c,d"])
    assert seen == ["doi:10.1/a|10.1/b"]
    assert out["10.1/a"]["doi"] and out["10.1/b"] is None  # answered without it: verified negative
    assert out["10.1/p|q"] is None and out["10.1/c,d"] is None  # unsafe ones go through singleton lookups
    assert r.cost_usd == pytest.approx(0.0001)

    bad = _resolver(Router().add("GET", OA, "/works", (400, {})))
    assert bad.openalex_batch(["10.1/a"]) == {}
    paused = _resolver(Router().add("GET", OA, "/works", (429, {"error": "budget exceeded"})))
    assert paused.openalex_batch([f"10.1/{i}" for i in range(120)]) == {}
    assert paused.requests["openalex"] == 1


def test_openalex_batch_splits_at_fifty():
    chunks = []

    def works(request):
        chunks.append(len(request.url.params["filter"].removeprefix("doi:").split("|")))
        return httpx.Response(200, json={"results": []})

    r = _resolver(Router().add("GET", OA, "/works", works))
    out = r.openalex_batch([f"10.1/{i}" for i in range(120)])
    assert chunks == [50, 50, 20] and len(out) == 120 and all(v is None for v in out.values())


# ------------------------------------------------------------------ Semantic Scholar / Crossref
def test_s2_batch_retries_statuses_uses_api_key_and_validates_shape(monkeypatch):
    state = {"n": 0, "headers": []}

    def post(request):
        state["n"] += 1
        state["headers"].append(request.headers.get("x-api-key"))
        if state["n"] == 1:
            return httpx.Response(429)
        if state["n"] == 2:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json=[{"title": "x"}, None])

    client = make_client(Router().add("POST", S2, "/graph/v1/paper/batch", post), s2_api_key="S2KEY")
    fast = TokenBucket(1e6, 1000)
    r = BatchResolver(client, sleep=lambda s: None, buckets=(fast, fast, fast))
    out = r.s2_batch(["DOI:10.1/a", "DOI:10.1/b"])
    assert out == {"DOI:10.1/a": {"title": "x"}, "DOI:10.1/b": None}
    assert state["headers"] == ["S2KEY"] * 3
    wrong_len = _resolver(Router().add("POST", S2, "/graph/v1/paper/batch", (200, [None])))
    assert wrong_len.s2_batch(["DOI:10.1/a", "DOI:10.1/b"]) == {}
    client_error = _resolver(Router().add("POST", S2, "/graph/v1/paper/batch", (400, {})))
    assert client_error.s2_batch(["DOI:10.1/a"]) == {} and client_error.requests["semantic_scholar"] == 1


def test_crossref_doi_outcomes_and_mailto():
    seen = []

    def route(request):
        seen.append(dict(request.url.params))
        path = request.url.path
        return {"/works/10.1/ok": httpx.Response(200, json={"message": {"DOI": "10.1/ok"}}),
                "/works/10.1/missing": httpx.Response(404)}.get(path, httpx.Response(500))

    client = make_client(Router().add("GET", CR, "/works/", route), mailto="real.person@lbl.gov")
    fast = TokenBucket(1e6, 1000)
    r = BatchResolver(client, buckets=(fast, fast, fast))
    assert r.crossref_doi("10.1/ok") == {"DOI": "10.1/ok"} and seen[0]["mailto"] == "real.person@lbl.gov"
    assert r.crossref_doi("10.1/missing") is None
    with pytest.raises(rb._Unanswered, match="HTTP 500"):
        r.crossref_doi("10.1/boom")


def test_crossref_transport_error_is_unanswered():
    def boom(request):
        raise httpx.ConnectError("down")

    r = _resolver(Router().add("GET", CR, "/works/", boom).add("GET", CR, "/works", boom))
    with pytest.raises(rb._Unanswered):
        r.crossref_doi("10.1/a")
    with pytest.raises(rb._Unanswered):
        r.crossref_bibliographic(Ref(0, "T"))


def test_crossref_bibliographic_returns_agreeing_item_only():
    items = [{"DOI": "10.9/wrong", "title": ["An unrelated paper title"], "issued": {"date-parts": [[2019]]}},
             {"DOI": "10.9/right", "title": ["Grazing incidence scattering of thin films"],
              "issued": {"date-parts": [[2019]]}}]
    queries = []

    def route(request):
        queries.append(request.url.params["query.bibliographic"])
        return httpx.Response(200, json={"message": {"items": items}})

    r = _resolver(Router().add("GET", CR, "/works", route))
    ref = Ref(0, "Grazing incidence scattering of thin films", 2019, journal="J Appl", volume="3", first_page="7")
    assert r.crossref_bibliographic(ref)["DOI"] == "10.9/right"
    assert queries[0] == "Grazing incidence scattering of thin films J Appl 2019 3 7"
    assert r.crossref_bibliographic(Ref(1, "Another title entirely different", 1990)) is None
    bad = _resolver(Router().add("GET", CR, "/works", (500, {})))
    with pytest.raises(rb._Unanswered):
        bad.crossref_bibliographic(ref)


def test_s2_title_match_statuses():
    ref = Ref(0, "Grazing incidence scattering of thin films", 2019)
    for status, expect in ((404, None),):
        assert _resolver(Router().add("GET", S2, "/graph/v1/paper/search/match", (status, {}))).s2_title_match(ref) is expect
    with pytest.raises(rb._Unanswered, match="HTTP 500"):
        _resolver(Router().add("GET", S2, "/graph/v1/paper/search/match", (500, {}))).s2_title_match(ref)
    empty = _resolver(Router().add("GET", S2, "/graph/v1/paper/search/match", (200, {"data": []})))
    assert empty.s2_title_match(ref) is None

    def boom(request):
        raise httpx.ConnectError("x")

    with pytest.raises(rb._Unanswered):
        _resolver(Router().add("GET", S2, "/graph/v1/paper/search/match", boom)).s2_title_match(ref)


# ------------------------------------------------------------------ orchestration
def test_resolve_dois_uses_batches_above_threshold_and_charges_cost():
    def works(request):
        return httpx.Response(200, json={"results": [openalex_work("10.1/0", "Zero")]})

    router = Router().add("GET", OA, "/works", works)
    router.add("POST", S2, "/graph/v1/paper/batch", lambda req: httpx.Response(
        200, json=[None for _ in range(req.read().decode().count("DOI:"))]))
    router.add("GET", CR, "/works/", (404, {}))
    r = _resolver(router)
    out = r.resolve_dois([f"10.1/{i}" for i in range(3)], prefer_batch_above=2)
    assert out["10.1/0"].source == "openalex" and out["10.1/0"].cost_usd == pytest.approx(0.0001 / 50)
    assert out["10.1/1"].source == "none" and out["10.1/1"].record is None
    assert r.resolve_dois(["junk", ""]) == {}


def test_resolve_dois_does_not_cache_negatives_when_a_source_was_unreachable():
    router = Router().add("GET", OA, "/works/", (404, {}))
    router.add("POST", S2, "/graph/v1/paper/batch", (503, {}))
    router.add("GET", CR, "/works/", (404, {}))
    r = _resolver(router)
    assert r.resolve_dois(["10.1/a"]) == {}  # Semantic Scholar could not be asked, so no verdict
    assert r.cache.count() == 0


def test_resolve_references_doi_first_then_title_paths_and_cache():
    def s2_match(request):
        title = request.url.params["query"]
        if "Unknown" in title:
            return httpx.Response(200, json={"data": []})
        if "Flaky" in title:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"title": title, "year": 2020}]})

    router = Router()
    router.add("GET", OA, "/works/", lambda req: httpx.Response(200, json=openalex_work("10.1/known", "Known")))
    router.add("GET", S2, "/graph/v1/paper/search/match", s2_match)
    router.add("GET", CR, "/works", (200, {"message": {"items": []}}))
    r = _resolver(router)
    refs = [Ref(0, "Has doi", 2020, doi="https://doi.org/10.1/known"),
            Ref(1, "Matching title for lookup", 2020),
            Ref(2, "Unknown reference title here", 2020),
            Ref(3, "Flaky reference title here", 2020),
            Ref(4, "", 2020)]
    out = r.resolve_references(refs)
    assert set(out) == {0, 1} and out[0].source == "openalex" and out[1].source == "s2_title"
    assert r.cache.get(refs[2].key).record is None  # S2 and Crossref both answered "no": verified negative
    assert r.cache.get(refs[3].key) is None  # S2 5xx: "could not ask" is not cached
    calls = len(router.calls)
    again = r.resolve_references(refs[1:3])
    assert set(again) == {1} and len(router.calls) == calls  # served from the cache

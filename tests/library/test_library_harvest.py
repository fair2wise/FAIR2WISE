"""T45: the Harvester (fake arXiv/OpenAlex and downloads only; no network)."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState
from app.modules.jobs import JobService, JobStore
from app.modules.library import LIBRARY_HARVEST_KIND, library_inputs, register_library_jobs
from app.modules.library.handlers import library_handlers
from app.modules.literature.legacy_download import JSONFetchError
from app.modules.library.harvest import (
    Candidate,
    HarvestStore,
    OPENALEX_RATE_LIMITED,
    Harvester,
    OpenAlexRateLimited,
    arxiv_queries,
    merge_candidates,
    pdf_downloader,
    score_candidate,
    topic_terms,
)
from app.modules.library.probe import ProbeResult
from app.modules.library.service import LibraryService
from tests.jobs.jobs_fakes import Clock
from tests.library.test_library_service import pdf
from tests.security.security_fakes import local_settings

TOPIC = "resonant soft x-ray scattering"


def cand(title: str, *, source: str = "arxiv", doi: str | None = None, arxiv_id: str | None = None, year: int | None = 2021,
         abstract: str = "", urls: list[str] | None = None) -> Candidate:
    return Candidate(title=title, source=source, year=year, doi=doi, arxiv_id=arxiv_id, abstract=abstract,
                     landing_url=f"https://doi.org/{doi}" if doi else f"https://arxiv.org/abs/{arxiv_id}",
                     pdf_urls=list(urls or []))


GOOD = "Resonant soft X-ray scattering of polymer blends"


# -- scoring -----------------------------------------------------------------------------------------------


def test_topic_terms_keep_phrases_and_drop_stop_words():
    assert topic_terms('The "soft x-ray" scattering of polymers') == ["soft x-ray", "scattering", "polymers"]
    assert topic_terms("") == [] and topic_terms("of the") == []


@pytest.mark.parametrize(("title", "abstract", "tier"), [
    (GOOD, "", "A"),
    ("Polymer morphology", "We use resonant soft X-ray scattering to map domains.", "B"),
    ("Scattering of light", "A study.", "C"),
    ("Soft matter", "Nothing relevant.", "C"),
])
def test_score_candidate_tiers(title, abstract, tier):
    score, got = score_candidate(TOPIC, title, abstract)
    assert got == tier and 0.0 <= score <= 1.0


def test_scores_order_by_relevance_and_unsearchable_topics_score_zero():
    assert score_candidate(TOPIC, GOOD)[0] > score_candidate(TOPIC, "Polymer morphology", GOOD)[0]
    assert score_candidate("of the", GOOD) == (0.0, "C")


def test_arxiv_queries_phrase_then_and():
    assert arxiv_queries("soft x-ray") == ['all:"soft x-ray"', 'all:"soft" AND all:"x-ray"']
    assert arxiv_queries("rsoxs") == ['all:"rsoxs"'] and arxiv_queries("of") == []


def test_merge_dedupes_by_doi_and_arxiv_id_and_unions_links():
    a = cand("A", source="arxiv", doi="10.1/x", arxiv_id="2101.00001v2", urls=["https://arxiv.org/pdf/2101.00001v2"])
    b = cand("A again", source="openalex", doi="10.1/X", urls=["https://repo.example/a.pdf"])
    c = cand("C", arxiv_id="2102.00002v1")
    d = cand("C v2", arxiv_id="2102.00002v3")
    out = merge_candidates([[a, c], [b, d, cand("")]])
    assert [x.title for x in out] == ["A", "C"] and out[0].pdf_urls == ["https://arxiv.org/pdf/2101.00001v2", "https://repo.example/a.pdf"]


# -- the job -----------------------------------------------------------------------------------------------


class Net:
    """Fake downloads: ``url -> bytes`` (served) or a status string (refused)."""

    def __init__(self, table: dict[str, Any]) -> None:
        self.table, self.calls = table, []

    def __call__(self, url: str, max_bytes: int) -> tuple[bytes | None, str]:
        self.calls.append(url)
        value = self.table.get(url, "failed:http_404")
        return (value, "ok") if isinstance(value, bytes) else (None, value)


class World:
    def __init__(self, tmp_path: Path, searchers: dict[str, Any], net: Net, *, clock: Clock | None = None,
                 link_resolver: Any = None) -> None:
        self.tmp = tmp_path
        self.lib = tmp_path / "papers"
        self.lib.mkdir()
        self.service = LibraryService(self.lib, tmp_path / "state" / "library" / "catalog.sqlite", settings=local_settings(),
                                      probe=lambda p: ProbeResult(status="ok", pages=2, page_text="x"))
        self.store = HarvestStore(tmp_path / "state" / "library" / "harvest.sqlite")
        self.net = net
        self.harvester = Harvester(self.service, self.store, searchers=searchers, downloader=net, delay=0.0,
                                   link_resolver=link_resolver)
        settings = local_settings()
        kwargs = {"lease_seconds": 60, "clock": clock} if clock else {}
        self.jobs = JobService(JobStore(tmp_path / "jobs.sqlite3", **kwargs), settings=settings)
        register_library_jobs(self.jobs, settings, handlers=library_handlers(self.service, harvester=self.harvester))

    def run(self, nonce: str = "1", **params: Any) -> tuple[Any, JobState]:
        merged = {"topic": TOPIC, "category": "rsoxs", **params}
        handle = self.jobs.submit(self.service.worker_context("s"), LIBRARY_HARVEST_KIND, library_inputs(merged, nonce=nonce))
        job_id, state = self.jobs.work_one("w")
        assert job_id == handle.job_id
        return handle, state

    def statuses(self, run_id: str) -> dict[str, str]:
        return {c["title"]: c["status"] for c in self.store.candidates(run_id)}


def fixed(*cands: Candidate):
    return lambda topic, per_query: [Candidate(**c.__dict__) for c in cands]


def boom(topic: str, per_query: int):
    raise RuntimeError("source is down")


PAPERS = (
    cand(GOOD, doi="10.1/a", year=2013, urls=["https://oa.example/a.pdf"]),
    cand("Resonant soft X-ray scattering: a review", doi="10.1/b", year=2019, urls=["https://oa.example/b.pdf"]),
    cand("Paywalled resonant soft X-ray scattering study", doi="10.1/p", urls=[]),
    cand("Polymer films", doi="10.1/c", abstract="uses resonant soft x-ray scattering", urls=["https://oa.example/c.pdf"]),
    cand("Unrelated topic", doi="10.1/u", urls=["https://oa.example/u.pdf"]),
)
SERVED = {f"https://oa.example/{n}.pdf": pdf(n) for n in "abcu"}


def test_harvest_downloads_a_and_b_files_by_year_and_writes_the_manifest(tmp_path):
    world = World(tmp_path, {"arxiv": fixed(*PAPERS)}, Net(SERVED))
    handle, state = world.run(sources=["arxiv"])
    assert state is JobState.SUCCEEDED
    assert world.statuses(handle.job_id) == {
        GOOD: "downloaded", "Resonant soft X-ray scattering: a review": "downloaded",
        "Polymer films": "downloaded", "Paywalled resonant soft X-ray scattering study": "paywalled",
        "Unrelated topic": "skipped"}
    assert (world.lib / "rsoxs" / "2013" / "10.1_a.pdf").is_file() and (world.lib / "rsoxs" / "2019" / "10.1_b.pdf").is_file()
    assert "https://oa.example/u.pdf" not in world.net.calls  # tier C is never downloaded
    manifest = json.loads((world.lib / "rsoxs" / "manifest.json").read_text())
    row = next(p for p in manifest["papers"] if p["doi"] == "https://doi.org/10.1/a")
    assert row["publication_year"] == 2013 and row["title"] == GOOD and row["pdf_path"] == "papers/rsoxs/2013/10.1_a.pdf"
    run = world.store.run(handle.job_id)
    assert run["topic"] == TOPIC and run["category"] == "rsoxs" and run["counts"]["downloaded"] == 3 and run["counts"]["paywalled"] == 1


def test_max_downloads_limits_downloads_but_a_paywalled_paper_still_reaches_the_queue(tmp_path):
    world = World(tmp_path, {"arxiv": fixed(*PAPERS)}, Net(SERVED))
    handle, state = world.run(sources=["arxiv"], max_downloads=1)
    assert state is JobState.SUCCEEDED
    counts = world.store.run(handle.job_id)["counts"]
    assert counts["downloaded"] == 1 and counts["skipped"] == 3 and counts["paywalled"] == 1
    assert len(world.net.calls) == 1


def test_papers_already_in_the_library_by_doi_are_not_downloaded_again(tmp_path):
    world = World(tmp_path, {"arxiv": fixed(*PAPERS)}, Net(SERVED))
    world.run(sources=["arxiv"])
    first_calls = len(world.net.calls)
    handle, state = world.run(nonce="2", sources=["arxiv"])
    assert state is JobState.SUCCEEDED and len(world.net.calls) == first_calls
    assert world.statuses(handle.job_id)[GOOD] == "in_library"


def test_a_failing_source_is_logged_and_the_others_still_run(tmp_path):
    world = World(tmp_path, {"arxiv": boom, "openalex": fixed(PAPERS[0])}, Net(SERVED))
    handle, state = world.run()
    assert state is JobState.SUCCEEDED and world.statuses(handle.job_id) == {GOOD: "downloaded"}
    logs = " ".join(entry.message for entry in world.jobs.store.logs(handle.job_id))
    assert "arxiv search failed" in logs
    status = world.store.run(handle.job_id)["source_status"]
    assert status["arxiv"]["state"] == "error" and status["openalex"] == {"state": "ok", "returned": 1, "message": status["openalex"]["message"]}


def test_an_empty_search_says_why_in_plain_words(tmp_path):
    def rate_limited(topic: str, per_query: int):
        raise OpenAlexRateLimited(OPENALEX_RATE_LIMITED)

    world = World(tmp_path, {"arxiv": fixed(), "openalex": rate_limited}, Net({}))
    handle, state = world.run(topic="plecostomus")
    assert state is JobState.FAILED      # a source that errors is an error, never "0 found"
    logs = [e.message for e in world.jobs.store.logs(handle.job_id)]
    assert OPENALEX_RATE_LIMITED in logs
    text = next(m for m in logs if m.startswith("search summary"))
    assert "arxiv returned 0" in text and f"openalex could not be searched ({OPENALEX_RATE_LIMITED})" in text
    assert "does not mean the topic has no papers" in text and "0 distinct papers" in text


def test_without_a_key_openalex_runs_and_the_log_and_summary_say_so(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    world = World(tmp_path, {"arxiv": fixed(), "openalex": fixed(cand(GOOD, source="openalex", doi="10.1/A"))}, Net({}))
    handle, state = world.run()
    assert state is JobState.SUCCEEDED
    logs = [e.message for e in world.jobs.store.logs(handle.job_id)]
    note = next(m for m in logs if m.startswith("OpenAlex: running without an API key"))
    assert "reduced rate (about 1 request per second, at most 1 request per search)" in note
    assert logs.index(note) < logs.index("openalex: 1 results")
    summary = next(m for m in logs if m.startswith("search summary"))
    assert "openalex returned 1 (running without an API key, reduced rate)" in summary
    assert "could not be searched" not in summary


def test_with_a_key_there_is_no_reduced_rate_note(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "k-test-value")
    world = World(tmp_path, {"arxiv": fixed(), "openalex": fixed(cand(GOOD, source="openalex", doi="10.1/A"))}, Net({}))
    handle, _ = world.run()
    text = " ".join(e.message for e in world.jobs.store.logs(handle.job_id))
    assert "without an API key" not in text and "k-test-value" not in text


def test_the_summary_counts_what_the_merge_dropped(tmp_path):
    dup = (cand(GOOD, doi="10.1/a", urls=["https://oa.example/a.pdf"]), cand("", doi="10.1/z"))
    world = World(tmp_path, {"arxiv": fixed(*dup), "openalex": fixed(cand(GOOD, source="openalex", doi="10.1/A"))}, Net(SERVED))
    handle, _ = world.run()
    text = next(e.message for e in world.jobs.store.logs(handle.job_id) if e.message.startswith("search summary"))
    assert "3 results in all, 1 dropped for having no title, 1 dropped as duplicates (same DOI or arXiv id), 1 distinct papers" in text


def test_every_source_failing_fails_the_job(tmp_path):
    world = World(tmp_path, {"arxiv": boom, "openalex": boom}, Net({}))
    handle, state = world.run()
    assert state is JobState.FAILED


def test_an_open_access_paper_that_cannot_be_fetched_is_never_called_paywalled(tmp_path):
    """Pilot 2026-11-24: 45 of 50 were 'paywalled'. A failed fetch of an open-access link is blocked or failed."""
    papers = (cand(GOOD, doi="10.1/n", urls=["https://pub.example/login"]),
              cand("Resonant soft X-ray scattering again", doi="10.1/m", urls=["https://down.example/x.pdf"]),
              cand("Resonant soft X-ray scattering blocked", doi="10.1/b", urls=["https://walled.example/y.pdf"]),
              cand("Resonant soft X-ray scattering no link", doi="10.1/l", urls=[]),
              cand("Resonant soft X-ray scattering closed", doi="10.1/c", urls=[]))
    papers[3].oa = True       # OpenAlex says open access but lists no direct PDF link
    papers[4].oa = False      # genuinely not open access
    net = Net({"https://pub.example/login": "refused:not_pdf", "https://down.example/x.pdf": "failed:network",
               "https://walled.example/y.pdf": "blocked:private_address"})
    world = World(tmp_path, {"arxiv": fixed(*papers)}, net)
    handle, _ = world.run(sources=["arxiv"])
    assert world.statuses(handle.job_id) == {
        GOOD: "failed", "Resonant soft X-ray scattering again": "failed",
        "Resonant soft X-ray scattering blocked": "blocked", "Resonant soft X-ray scattering no link": "failed",
        "Resonant soft X-ray scattering closed": "paywalled"}
    details = {r["title"]: r["detail"] for r in world.store.candidates(handle.job_id)}
    assert details["Resonant soft X-ray scattering blocked"] == (
        "open access, blocked by network policy (private_address) at walled.example")
    assert details[GOOD] == "open access, but the fetch failed (refused:not_pdf) at pub.example"
    assert details["Resonant soft X-ray scattering no link"] == "open access, but no direct PDF link was found"
    assert details["Resonant soft X-ray scattering closed"].startswith("not open access")
    counts = world.store.run(handle.job_id)["counts"]
    assert counts["blocked"] == 1 and counts["paywalled"] == 1 and counts["failed"] == 3


def test_openalex_is_oa_reads_the_flag_and_the_locations():
    from app.modules.library.harvest import openalex_is_oa

    assert openalex_is_oa({"open_access": {"is_oa": True}}) is True
    assert openalex_is_oa({"open_access": {"is_oa": False}, "locations": [{"is_oa": True}]}) is True
    assert openalex_is_oa({"open_access": {"is_oa": False}, "locations": [{"is_oa": False}]}) is False
    assert openalex_is_oa({"best_oa_location": {"is_oa": True}}) is True
    assert openalex_is_oa({"id": "W1"}) is None


def test_the_second_link_is_tried_when_the_first_fails(tmp_path):
    paper = cand(GOOD, doi="10.1/t", urls=["https://a.example/1.pdf", "https://b.example/2.pdf"])
    net = Net({"https://a.example/1.pdf": "failed:network", "https://b.example/2.pdf": pdf("t")})
    world = World(tmp_path, {"arxiv": fixed(paper)}, net)
    handle, _ = world.run(sources=["arxiv"])
    assert world.statuses(handle.job_id) == {GOOD: "downloaded"} and net.calls == ["https://a.example/1.pdf", "https://b.example/2.pdf"]


def test_a_resumed_job_keeps_finished_candidates(tmp_path):
    world = World(tmp_path, {"arxiv": fixed(*PAPERS)}, Net(SERVED))
    handle, _ = world.run(sources=["arxiv"])
    calls = len(world.net.calls)
    # Run the stage again for the same run id (what a re-claimed job does): nothing is fetched again.
    from types import SimpleNamespace
    ctx = SimpleNamespace(inputs=SimpleNamespace(params={"topic": TOPIC, "category": "rsoxs", "sources": ["arxiv"]}),
                          job=SimpleNamespace(job_id=handle.job_id), guard=lambda: None, log=lambda *a, **k: None,
                          progress=lambda **k: None)
    result = world.harvester.run(ctx)
    assert len(world.net.calls) == calls and result.progress["downloaded"] == 3


@pytest.mark.parametrize("params", [
    {"topic": ""}, {"topic": "of the"}, {"topic": "x" * 201}, {"category": "../x"}, {"category": ""},
    {"sources": []}, {"sources": ["pubmed"]}, {"max_downloads": 0}, {"max_downloads": 201}, {"max_downloads": True},
    {"per_query": 101}, {"schema": "bad schema!"},
])
def test_parse_rejects_bad_params(params):
    base = {"topic": TOPIC, "category": "rsoxs"}
    with pytest.raises(ContractError) as err:
        Harvester.parse({**base, **params})
    assert err.value.code is ErrorCode.INVALID_INPUT


def test_parse_defaults_and_the_schema_label():
    out = Harvester.parse({"topic": f"  {TOPIC} ", "category": "tomo", "schema": "matkg_schema@0.3.1"})
    assert out == {"topic": TOPIC, "category": "tomo", "schema": "matkg_schema@0.3.1", "sources": ["arxiv", "openalex"],
                   "max_downloads": 20, "per_query": 50}


def test_a_bad_submission_fails_the_job_without_searching(tmp_path):
    searched = []
    world = World(tmp_path, {"arxiv": lambda t, n: searched.append(t) or []}, Net({}))
    handle, state = world.run(category="bad name")
    assert state is JobState.FAILED and searched == []


# -- the paywalled queue -----------------------------------------------------------------------------------


def _run_with_paywalled(tmp_path):
    world = World(tmp_path, {"arxiv": fixed(*PAPERS)}, Net(SERVED))
    handle, _ = world.run(sources=["arxiv"])
    row = next(c for c in world.store.candidates(handle.job_id) if c["status"] == "paywalled")
    return world, handle, row


def test_a_supplied_pdf_is_filed_in_the_runs_category_by_the_candidates_year(tmp_path):
    world, handle, row = _run_with_paywalled(tmp_path)
    asked = world.harvester.supply_pdf(row["id"], "whatever.pdf", pdf("paywalled-copy"))  # nothing readable to check it by
    assert asked["filed"] is False and asked["needs_confirmation"] is True and not list(world.lib.rglob("whatever.pdf"))
    out = world.harvester.supply_pdf(row["id"], "whatever.pdf", pdf("paywalled-copy"), confirm=True)
    assert out["filed"] is True and out["duplicate"] is False and out["candidate"]["status"] == "filed"
    assert out["candidate"]["provenance"] == "user_supplied_confirmed"
    assert out["paper"]["relpath"].startswith("rsoxs/") and out["paper"]["relpath"].endswith("whatever.pdf")
    assert out["candidate"]["relpath"] == out["paper"]["relpath"] and out["manifest"]["updated"] is True
    with pytest.raises(ContractError) as again:  # no longer paywalled
        world.harvester.supply_pdf(row["id"], "x.pdf", pdf("other"), confirm=True)
    assert again.value.code is ErrorCode.CONFLICT


def test_dismiss_and_unknown_candidates(tmp_path):
    world, handle, row = _run_with_paywalled(tmp_path)
    assert world.harvester.dismiss(row["id"])["candidate"]["status"] == "dismissed"
    with pytest.raises(ContractError) as err:
        world.harvester.dismiss(row["id"])
    assert err.value.code is ErrorCode.CONFLICT
    with pytest.raises(ContractError) as missing:
        world.harvester.dismiss("0" * 16)
    assert missing.value.code is ErrorCode.NOT_FOUND


# -- the downloader ----------------------------------------------------------------------------------------


def test_the_downloader_refuses_private_addresses_without_connecting(monkeypatch):
    def blocked(*a: Any, **k: Any) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    download = pdf_downloader()
    for url in ("http://127.0.0.1/x.pdf", "http://10.0.0.5/x.pdf", "ftp://example.com/x.pdf", "http://user:pw@example.com/x.pdf"):
        data, status = download(url, 1000)
        assert data is None and status.startswith("blocked:")


def test_the_downloader_checks_redirect_hops_and_the_pdf_signature():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/go":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/internal.pdf"})
        if request.url.path == "/html":
            return httpx.Response(200, content=b"<html>login</html>")
        if request.url.path == "/big":
            return httpx.Response(200, content=b"%PDF-" + b"x" * 5000)
        return httpx.Response(200, content=b"%PDF-1.4 ok")

    from app.modules.literature.safe_fetch import NetworkPolicy

    public = NetworkPolicy(resolver=lambda host: ["93.184.216.34"])
    download = pdf_downloader(httpx.Client(transport=httpx.MockTransport(handler)), public)
    # the redirect hop that was refused is the one named (the first URL's host is example.com, the hop's is 127.0.0.1)
    assert download("http://example.com/go", 1000) == (None, "blocked:private_address@127.0.0.1")
    assert download("http://example.com/html", 1000) == (None, "refused:not_pdf")
    assert download("http://example.com/big", 1000) == (None, "refused:too_large")
    assert download("http://example.com/ok.pdf", 1000) == (b"%PDF-1.4 ok", "ok")


def _refused_by_openalex(status: int, detail: str | None = None):
    def search(topic: str, per_query: int):
        raise JSONFetchError(f"HTTP {status}", status=status, detail=detail)

    return search


def test_a_rejected_openalex_key_is_an_error_in_the_run_and_the_log_not_zero_results(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "not-a-real-key")
    said = "API key not found. Get a free key at https://openalex.org/settings/api-key."
    world = World(tmp_path, {"arxiv": fixed(), "openalex": _refused_by_openalex(401, said)}, Net({}))
    handle, state = world.run(topic="plecostomus")
    assert state is JobState.FAILED
    entries = world.jobs.store.logs(handle.job_id)
    failed = next(e for e in entries if e.message.startswith("openalex search failed"))
    assert failed.level == "error" and "HTTP 401" in failed.message and said in failed.message   # OpenAlex's own words
    assert "not-a-real-key" not in " ".join(e.message for e in entries)
    run = world.store.run(handle.job_id)
    assert run["counts"]["total"] == 0
    assert run["source_status"]["openalex"]["state"] == "error"
    assert run["source_status"]["arxiv"] == {"state": "ok", "returned": 0, "message": ""}


def test_an_error_in_one_source_with_results_from_the_other_stays_visible_on_the_run(tmp_path):
    world = World(tmp_path, {"arxiv": fixed(PAPERS[0]), "openalex": _refused_by_openalex(503)}, Net(SERVED))
    handle, state = world.run()
    assert state is JobState.SUCCEEDED
    status = world.store.run(handle.job_id)["source_status"]
    assert status["openalex"]["state"] == "error" and "HTTP 503" in status["openalex"]["message"]
    assert status["arxiv"]["state"] == "ok" and status["arxiv"]["returned"] == 1


def test_open_access_links_are_resolved_lazily_and_the_rule_is_recorded(tmp_path):
    from app.modules.library.oa_links import ResolvedLink

    paper = cand(GOOD, doi="10.1/r", urls=[])
    paper.oa = True
    paper.oa_locations = [{"pdf_url": "", "landing_page_url": "https://zenodo.org/records/9"}]
    asked = []

    def resolver(candidate):
        asked.append(candidate.title)
        yield ResolvedLink("https://zenodo.org/api/records/9/files/p.pdf/content", "zenodo_record")

    net = Net({"https://zenodo.org/api/records/9/files/p.pdf/content": pdf("resolved")})
    world = World(tmp_path, {"arxiv": fixed(paper)}, net, link_resolver=resolver)
    handle, _ = world.run(sources=["arxiv"])
    assert world.statuses(handle.job_id) == {GOOD: "downloaded"} and asked == [GOOD]
    row = world.store.candidates(handle.job_id)[0]
    assert row["link_rule"] == "zenodo_record" and row["detail"] == "found via zenodo_record"


def test_a_listed_link_that_works_never_triggers_a_lookup(tmp_path):
    paper = cand(GOOD, doi="10.1/s", urls=["https://a.example/1.pdf"])
    paper.oa = True
    paper.oa_locations = [{"pdf_url": "", "landing_page_url": "https://zenodo.org/records/9"}]

    def resolver(candidate):
        raise AssertionError("no lookup is needed when the listed link works")

    world = World(tmp_path, {"arxiv": fixed(paper)}, Net({"https://a.example/1.pdf": pdf("s")}), link_resolver=resolver)
    handle, _ = world.run(sources=["arxiv"])
    assert world.store.candidates(handle.job_id)[0]["link_rule"] == "arxiv"


def test_a_blocked_redirect_hop_names_that_hops_host(tmp_path):
    paper = cand(GOOD, doi="10.1/h", urls=["https://start.example/x.pdf"])
    paper.oa = True
    net = Net({"https://start.example/x.pdf": "blocked:private_address@cdn.hop.example"})
    world = World(tmp_path, {"arxiv": fixed(paper)}, net)
    handle, _ = world.run(sources=["arxiv"])
    row = world.store.candidates(handle.job_id)[0]
    assert row["status"] == "blocked"
    assert row["detail"] == "open access, blocked by network policy (private_address) at cdn.hop.example"


def test_an_open_access_paper_whose_locations_give_no_link_is_failed_not_paywalled(tmp_path):
    paper = cand(GOOD, doi="10.1/n", urls=[])
    paper.oa = True
    paper.oa_locations = [{"pdf_url": "", "landing_page_url": "https://doi.org/10.1/n"}]
    world = World(tmp_path, {"arxiv": fixed(paper)}, Net({}), link_resolver=lambda c: iter(()))
    handle, _ = world.run(sources=["arxiv"])
    row = world.store.candidates(handle.job_id)[0]
    assert row["status"] == "failed" and row["detail"] == "open access, but no direct PDF link was found"


def test_an_http_link_is_tried_over_https_first_and_the_upgrade_is_recorded(tmp_path):
    paper = cand(GOOD, doi="10.1/u", urls=["http://journal.example/a.pdf"])
    paper.oa = True
    net = Net({"https://journal.example/a.pdf": pdf("upgraded"), "http://journal.example/a.pdf": pdf("plain")})
    world = World(tmp_path, {"arxiv": fixed(paper)}, net)
    handle, _ = world.run(sources=["arxiv"])
    row = world.store.candidates(handle.job_id)[0]
    assert net.calls == ["https://journal.example/a.pdf"]          # the http link is not even asked when https works
    assert row["status"] == "downloaded" and row["link_rule"] == "arxiv+https_upgrade"
    assert row["detail"] == "found via arxiv+https_upgrade (http upgraded to https)"


def test_the_listed_http_link_is_the_fallback_where_https_does_not_work(tmp_path):
    paper = cand(GOOD, doi="10.1/f", urls=["http://old.example/a.pdf"])
    paper.oa = True
    net = Net({"https://old.example/a.pdf": "failed:network", "http://old.example/a.pdf": pdf("plain")})
    world = World(tmp_path, {"arxiv": fixed(paper)}, net)
    handle, _ = world.run(sources=["arxiv"])
    row = world.store.candidates(handle.job_id)[0]
    assert net.calls == ["https://old.example/a.pdf", "http://old.example/a.pdf"]
    assert row["status"] == "downloaded" and row["link_rule"] == "arxiv" and "upgraded" not in row["detail"]


def test_an_http_link_on_a_blocked_port_is_upgraded_and_what_the_listed_link_gave_is_reported_when_both_fail(tmp_path):
    paper = cand(GOOD, doi="10.1/v", urls=["http://journal.example:8080/a.pdf"])
    paper.oa = True
    other = cand("Resonant soft X-ray scattering private", doi="10.1/w", urls=["https://internal.example/b.pdf"])
    other.oa = True
    net = Net({"https://journal.example/a.pdf": "failed:network", "http://journal.example:8080/a.pdf": "blocked:port@journal.example",
               "https://internal.example/b.pdf": "blocked:private_address@internal.example"})
    world = World(tmp_path, {"arxiv": fixed(paper, other)}, net)
    handle, _ = world.run(sources=["arxiv"])
    rows = {r["title"]: r for r in world.store.candidates(handle.job_id)}
    assert net.calls[:2] == ["https://journal.example/a.pdf", "http://journal.example:8080/a.pdf"]
    assert rows[GOOD]["status"] == "blocked" and rows[GOOD]["detail"] == (
        "open access, blocked by network policy (port) at journal.example")
    assert rows[other.title]["status"] == "blocked" and net.calls.count("https://internal.example/b.pdf") == 1   # https links are never "upgraded"


def test_https_upgrade_only_changes_plain_http_and_drops_the_port():
    from app.modules.library.harvest import https_upgrade

    assert https_upgrade("http://host.example:8080/p/a.pdf?x=1") == "https://host.example/p/a.pdf?x=1"
    assert https_upgrade("http://host.example/a.pdf") == "https://host.example/a.pdf"
    assert https_upgrade("https://host.example:8443/a.pdf") is None
    assert https_upgrade("http://user:pw@host.example/a.pdf") is None
    assert https_upgrade("ftp://host.example/a.pdf") is None

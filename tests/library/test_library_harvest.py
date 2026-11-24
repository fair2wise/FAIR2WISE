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
from app.modules.library.harvest import (
    Candidate,
    HarvestStore,
    Harvester,
    arxiv_queries,
    merge_candidates,
    pdf_downloader,
    score_candidate,
    topic_terms,
)
from app.modules.library.probe import ProbeResult
from app.modules.library.service import LibraryService, VerifyUnavailable
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
    def __init__(self, tmp_path: Path, searchers: dict[str, Any], net: Net, *, clock: Clock | None = None) -> None:
        self.tmp = tmp_path
        self.lib = tmp_path / "papers"
        self.lib.mkdir()
        self.service = LibraryService(self.lib, tmp_path / "state" / "library" / "catalog.sqlite", settings=local_settings(),
                                      probe=lambda p: ProbeResult(status="ok", pages=2, page_text="x"))
        self.store = HarvestStore(tmp_path / "state" / "library" / "harvest.sqlite")
        self.net = net
        self.harvester = Harvester(self.service, self.store, searchers=searchers, downloader=net, delay=0.0)
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
    assert "arxiv search skipped" in logs


def test_an_empty_search_says_why_in_plain_words(tmp_path):
    def no_key(topic: str, per_query: int):
        raise VerifyUnavailable("OPENALEX_API_KEY is not configured")

    world = World(tmp_path, {"arxiv": fixed(), "openalex": no_key}, Net({}))
    handle, state = world.run(topic="plecostomus")
    assert state is JobState.SUCCEEDED
    logs = [e.message for e in world.jobs.store.logs(handle.job_id)]
    text = next(m for m in logs if m.startswith("search summary"))
    assert "arxiv returned 0" in text and "openalex could not be searched (OPENALEX_API_KEY is not configured)" in text
    assert "does not mean the topic has no papers" in text and "0 distinct papers" in text


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


def test_open_links_that_are_not_pdfs_become_paywalled_and_network_errors_fail(tmp_path):
    papers = (cand(GOOD, doi="10.1/n", urls=["https://pub.example/login"]),
              cand("Resonant soft X-ray scattering again", doi="10.1/m", urls=["https://down.example/x.pdf"]))
    net = Net({"https://pub.example/login": "refused:not_pdf", "https://down.example/x.pdf": "failed:network"})
    world = World(tmp_path, {"arxiv": fixed(*papers)}, net)
    handle, _ = world.run(sources=["arxiv"])
    assert world.statuses(handle.job_id) == {GOOD: "paywalled", "Resonant soft X-ray scattering again": "failed"}


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
    out = world.harvester.file_candidate_pdf(row["id"], "whatever.pdf", pdf("paywalled-copy"))
    assert out["duplicate"] is False and out["candidate"]["status"] == "filed"
    assert out["paper"]["relpath"].startswith("rsoxs/") and out["paper"]["relpath"].endswith("whatever.pdf")
    assert out["candidate"]["relpath"] == out["paper"]["relpath"] and out["manifest"]["updated"] is True
    with pytest.raises(ContractError) as again:  # no longer paywalled
        world.harvester.file_candidate_pdf(row["id"], "x.pdf", pdf("other"))
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
    assert download("http://example.com/go", 1000) == (None, "blocked:private_address")
    assert download("http://example.com/html", 1000) == (None, "refused:not_pdf")
    assert download("http://example.com/big", 1000) == (None, "refused:too_large")
    assert download("http://example.com/ok.pdf", 1000) == (b"%PDF-1.4 ok", "ok")

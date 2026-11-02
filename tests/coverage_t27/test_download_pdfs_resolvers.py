"""Open-access resolver selectors/fetchers and the streaming PDF download (HTTP replaced by fakes)."""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.modules.literature import download_pdfs as dp


class Resp:
    def __init__(self, payload=None, ok=True, status=200, chunks=(), headers=None, json_error=None):
        self.payload, self.ok, self.status_code = payload, ok, status
        self._chunks, self.headers, self._json_error = list(chunks), headers or {}, json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, size):
        return iter(self._chunks)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(dp.time, "sleep", lambda s: None)


ARXIV = "https://arxiv.org/pdf/2101.00001"


# ------------------------------------------------------------------ rate limiting and DOI helpers
def test_delay_floor_and_per_host_delays(monkeypatch):
    monkeypatch.setattr(dp, "_delay_floor", 1.0)
    dp.set_delay_floor("2.5")
    assert dp._delay_floor == 2.5 and dp.host_delay("unknown.example") == 2.5
    dp.set_delay_floor(-3)
    assert dp._delay_floor == 0.0
    dp.set_delay_floor("junk")
    assert dp._delay_floor == 1.0
    monkeypatch.setitem(dp.OA_HOST_DELAYS, "slow.example", 9.0)
    assert dp.host_delay("SLOW.example") == 9.0 and dp.host_delay("") == 1.0


def test_respect_rate_limit_sleeps_only_for_repeat_hits(monkeypatch):
    slept = []
    monkeypatch.setattr(dp.time, "sleep", slept.append)
    monkeypatch.setattr(dp, "_delay_floor", 5.0)
    monkeypatch.setattr(dp, "_last_host_hit", {})
    clock = iter([100.0, 101.0, 101.0, 200.0, 200.0])
    monkeypatch.setattr(dp.time, "monotonic", lambda: next(clock))
    dp._respect_rate_limit("https://a.example/x")  # first hit: no wait
    dp._respect_rate_limit("https://a.example/y")  # 1s later: waits the remaining 4s
    assert slept == [4.0]
    dp._respect_rate_limit("https://a.example/z")  # long gap: no wait
    assert slept == [4.0]


def test_bare_doi_and_contact_email_rules():
    assert dp.bare_doi("https://dx.doi.org/10.1/x") == "10.1/x" and dp.bare_doi("not-a-doi") is None
    assert dp.bare_doi(None) is None
    assert dp.is_usable_contact_email("dana.reader@lbl.gov")
    for placeholder in ("someone@lbl.gov", "me@lbl.gov", "dana@example.com", "dana@localhost", "your_email@x.org"):
        assert not dp.is_usable_contact_email(placeholder)
    assert not dp.is_usable_contact_email("not an email") and not dp.is_usable_contact_email("")


# ------------------------------------------------------------------ Unpaywall
def test_unpaywall_requires_real_email_and_handles_failures(monkeypatch):
    calls = []
    body = {"best_oa_location": {"url_for_pdf": ARXIV}, "oa_locations": []}
    monkeypatch.setattr(dp.requests, "get", lambda url, **kw: calls.append((url, kw)) or Resp(body))
    assert dp.fetch_unpaywall_oa_pdfs("nonsense", "dana.reader@lbl.gov") == ([], [])
    assert dp.fetch_unpaywall_oa_pdfs("10.1/x", "not-an-email") == ([], [])
    assert calls == []
    urls, sources = dp.fetch_unpaywall_oa_pdfs("https://doi.org/10.1/x", "dana.reader@lbl.gov")
    assert urls and calls[0][0] == "https://api.unpaywall.org/v2/10.1/x" and calls[0][1]["params"] == {"email": "dana.reader@lbl.gov"}
    monkeypatch.setattr(dp.requests, "get", lambda url, **kw: Resp(ok=False, status=404))
    assert dp.fetch_unpaywall_oa_pdfs("10.1/x", "dana.reader@lbl.gov") == ([], [])
    monkeypatch.setattr(dp.requests, "get", lambda url, **kw: (_ for _ in ()).throw(TimeoutError("t")))
    assert dp.fetch_unpaywall_oa_pdfs("10.1/x", "dana.reader@lbl.gov") == ([], [])
    monkeypatch.setattr(dp.requests, "get", lambda url, **kw: Resp(["not", "a", "dict"]))
    assert dp.fetch_unpaywall_oa_pdfs("10.1/x", "dana.reader@lbl.gov") == ([], [])


# ------------------------------------------------------------------ Semantic Scholar
def test_semantic_scholar_selector():
    assert dp.select_semantic_scholar_oa_pdfs(None) == ([], [])
    assert dp.select_semantic_scholar_oa_pdfs({"openAccessPdf": "bad"}) == ([], [])
    assert dp.select_semantic_scholar_oa_pdfs({"openAccessPdf": {"url": ARXIV}}) == ([ARXIV], ["semantic-scholar"])
    assert dp.select_semantic_scholar_oa_pdfs({"openAccessPdf": {"url": "https://publisher.example/p"}}) == ([], [])


def test_semantic_scholar_batch_dedupes_dois_chunks_and_survives_errors(monkeypatch):
    monkeypatch.setattr(dp, "S2_BATCH_SIZE", 2)
    posts = []

    def post(url, **kw):
        posts.append(kw["json"]["ids"])
        n = len(posts)
        if n == 2:
            return Resp(ok=False, status=500)
        if n == 3:
            raise RuntimeError("boom")
        return Resp([{"openAccessPdf": {"url": ARXIV}}, None])

    monkeypatch.setattr(dp, "_oa_post", post)
    dois = ["10.1/a", "https://doi.org/10.1/a", "bad", "10.1/b", "10.1/c", "10.1/d", "10.1/e"]
    out = dp.fetch_semantic_scholar_batch_oa_pdfs(dois)
    assert posts[0] == ["DOI:10.1/a", "DOI:10.1/b"] and len(posts) == 3
    assert out["10.1/a"] == ([ARXIV], ["semantic-scholar"]) and out["https://doi.org/10.1/a"] == ([ARXIV], ["semantic-scholar"])
    assert out["10.1/b"] == ([], []) and out["bad"] == ([], []) and out["10.1/c"] == ([], [])
    assert dp.fetch_semantic_scholar_batch_oa_pdfs(["bad"]) == {"bad": ([], [])}


def test_semantic_scholar_batch_ignores_non_list_payload(monkeypatch):
    monkeypatch.setattr(dp, "_oa_post", lambda url, **kw: Resp({"error": "rate"}))
    assert dp.fetch_semantic_scholar_batch_oa_pdfs(["10.1/a"]) == {"10.1/a": ([], [])}


# ------------------------------------------------------------------ CORE / OSTI / Europe PMC / Crossref
def test_core_selector_and_fetch(monkeypatch):
    payload = {"results": [{"downloadUrl": "https://core.ac.uk/download/1.pdf",
                            "links": [{"type": "display", "url": "https://core.ac.uk/display/1"},
                                      {"type": "pdf", "url": "https://zenodo.org/record/1/files/a.pdf"}, "junk"]}, "x"]}
    urls, sources = dp.select_core_oa_pdfs(payload)
    assert "https://core.ac.uk/download/1.pdf" in urls and "https://zenodo.org/record/1/files/a.pdf" in urls
    assert sources == ["core"] and not any("display" in u for u in urls)
    assert dp.select_core_oa_pdfs({"downloadUrl": "https://core.ac.uk/download/2.pdf"})[0] == ["https://core.ac.uk/download/2.pdf"]
    assert dp.select_core_oa_pdfs([{"download_url": "https://core.ac.uk/download/3.pdf"}, 5])[0]
    assert dp.select_core_oa_pdfs("nothing") == ([], [])

    seen = {}
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: seen.update(url=url, **kw) or Resp(payload))
    monkeypatch.delenv("CORE_API_KEY", raising=False)
    assert dp.fetch_core_oa_pdfs("10.1/x", None) == ([], [])
    assert dp.fetch_core_oa_pdfs("bad", "key") == ([], [])
    assert dp.fetch_core_oa_pdfs("10.1/x", " key ")[1] == ["core"]
    assert seen["headers"]["Authorization"] == "Bearer key" and "key" not in str(seen["params"])
    monkeypatch.setenv("CORE_API_KEY", "envkey")
    dp.fetch_core_oa_pdfs("10.1/x", None)
    assert seen["headers"]["Authorization"] == "Bearer envkey"
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: Resp(ok=False, status=429))
    assert dp.fetch_core_oa_pdfs("10.1/x", "k") == ([], [])
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert dp.fetch_core_oa_pdfs("10.1/x", "k") == ([], [])


def test_osti_selector_and_fetch(monkeypatch):
    urls, sources = dp.select_osti_oa_pdfs({"records": [{"osti_id": 12345, "links": [
        "https://www.osti.gov/servlets/purl/777", {"href": "https://www.osti.gov/biblio/888"}, {"url": None}, 5]}]})
    assert "https://www.osti.gov/servlets/purl/12345" in urls and "https://www.osti.gov/servlets/purl/888" in urls
    assert sources == ["osti"]
    assert dp.select_osti_oa_pdfs([{"ostiId": 9}])[0] == ["https://www.osti.gov/servlets/purl/9"]
    assert dp.select_osti_oa_pdfs({"osti_id": 5})[0] == ["https://www.osti.gov/servlets/purl/5"]
    assert dp.select_osti_oa_pdfs({"hits": [{"osti_id": 6}]})[0] == ["https://www.osti.gov/servlets/purl/6"]
    assert dp.select_osti_oa_pdfs({"other": 1}) == ([], [])
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: Resp([{"osti_id": 1}]))
    assert dp.fetch_osti_oa_pdfs("10.1/x")[1] == ["osti"] and dp.fetch_osti_oa_pdfs("nope") == ([], [])
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: Resp(ok=False, status=500))
    assert dp.fetch_osti_oa_pdfs("10.1/x") == ([], [])
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert dp.fetch_osti_oa_pdfs("10.1/x") == ([], [])


def test_europepmc_selector_and_fetch(monkeypatch):
    payload = {"resultList": {"result": [
        {"pmcid": "PMC123", "fullTextUrlList": {"fullTextUrl": [
            {"documentStyle": "html", "url": "https://europepmc.org/articles/PMC123"},
            {"documentStyle": "pdf", "url": "https://europepmc.org/articles/PMC123?pdf=render"}, "junk"]}}, "junk"]}}
    urls, sources = dp.select_europepmc_search_oa_pdfs(payload)
    assert urls and all("europepmc.org" in u for u in urls) and sources == ["europepmc"]
    single = {"resultList": {"result": {"pmcid": "PMC9"}}}
    assert dp.select_europepmc_search_oa_pdfs(single)[1] == ["europepmc"]
    assert dp.select_europepmc_search_oa_pdfs([]) == ([], []) and dp.select_europepmc_search_oa_pdfs({}) == ([], [])
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: Resp(payload))
    assert dp.fetch_europepmc_search_oa_pdfs("10.1/x")[1] == ["europepmc"]
    assert dp.fetch_europepmc_search_oa_pdfs("x") == ([], [])
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: Resp(["list"]))
    assert dp.fetch_europepmc_search_oa_pdfs("10.1/x") == ([], [])
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: Resp(ok=False, status=500))
    assert dp.fetch_europepmc_search_oa_pdfs("10.1/x") == ([], [])
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert dp.fetch_europepmc_search_oa_pdfs("10.1/x") == ([], [])


def test_crossref_tdm_selector_and_fetch(monkeypatch):
    msg = {"message": {"link": [
        {"intended-application": "text-mining", "content-type": "application/pdf", "URL": ARXIV},
        {"intended-application": "similarity-checking", "content-type": "application/pdf", "URL": "https://arxiv.org/pdf/1"},
        {"intended-application": "text-mining", "content-type": "text/xml", "URL": "https://arxiv.org/pdf/2"}, "junk"]}}
    assert dp.select_crossref_tdm_pdfs(msg) == ([ARXIV], ["crossref-tdm"])
    assert dp.select_crossref_tdm_pdfs({"link": [{"intended-application": "text-mining",
                                                  "content-type": "application/pdf", "URL": ARXIV}]})[0] == [ARXIV]
    assert dp.select_crossref_tdm_pdfs([]) == ([], [])
    urls = []
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: urls.append(url) or Resp(msg))
    assert dp.fetch_crossref_tdm_pdfs("10.1/a b")[1] == ["crossref-tdm"] and urls[0].endswith("10.1%2Fa%20b")
    assert dp.fetch_crossref_tdm_pdfs("x") == ([], [])
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: Resp(["x"]))
    assert dp.fetch_crossref_tdm_pdfs("10.1/a") == ([], [])
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: Resp(ok=False, status=404))
    assert dp.fetch_crossref_tdm_pdfs("10.1/a") == ([], [])
    monkeypatch.setattr(dp, "_oa_get", lambda url, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert dp.fetch_crossref_tdm_pdfs("10.1/a") == ([], [])


def test_oa_get_and_post_retry_only_retryable_statuses(monkeypatch):
    monkeypatch.setattr(dp, "_respect_rate_limit", lambda url: None)
    statuses = iter([503, 200])
    seen = []

    def fake_get(url, timeout, **kw):
        seen.append(timeout)
        return Resp(status=next(statuses))

    monkeypatch.setattr(dp.requests, "get", fake_get)
    monkeypatch.setattr(dp.requests, "post", lambda url, timeout, **kw: seen.append(timeout) or Resp(status=200))
    assert dp._oa_get.retry.wait.__class__  # tenacity wrapper present
    dp._oa_get.retry.sleep = lambda s: None
    with pytest.raises(Exception):
        dp._oa_get("https://x.example/", timeout=3)  # a retryable status raises RequestException-compatible error
    assert dp._oa_post("https://x.example/", json={}).status_code == 200 and seen[-1] == 30.0


# ------------------------------------------------------------------ streaming download
def test_download_pdf_rejects_html_and_empty_but_saves_real_pdf(monkeypatch, tmp_path):
    dest = tmp_path / "a.pdf"
    monkeypatch.setattr(dp.requests, "get", lambda *a, **k: Resp(chunks=[b"%P", b"DF-1.7 body", b"more"]))
    assert dp.download_pdf("https://x/a.pdf", str(dest)) is True
    assert dest.read_bytes() == b"%PDF-1.7 bodymore" and not (tmp_path / "a.pdf.part").exists()
    html = tmp_path / "b.pdf"
    monkeypatch.setattr(dp.requests, "get", lambda *a, **k: Resp(chunks=[b"<html>login</html>"],
                                                                 headers={"Content-Type": "text/html"}))
    assert dp.download_pdf("https://x/b.pdf", str(html), retries=1) is False
    assert not html.exists() and not (tmp_path / "b.pdf.part").exists()
    empty = tmp_path / "c.pdf"
    monkeypatch.setattr(dp.requests, "get", lambda *a, **k: Resp(chunks=[b"", b"%P"]))
    assert dp.download_pdf("https://x/c.pdf", str(empty), retries=1) is False and not empty.exists()


def test_download_pdf_retries_on_errors_then_gives_up(monkeypatch, tmp_path):
    attempts = []

    def get(*a, **k):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("reset")
        return Resp(chunks=[b"%PDF-1.4 ok"])

    monkeypatch.setattr(dp.requests, "get", get)
    assert dp.download_pdf("https://x/a.pdf", str(tmp_path / "a.pdf"), retries=3) is True and len(attempts) == 3
    monkeypatch.setattr(dp.requests, "get", lambda *a, **k: Resp(status=500))
    assert dp.download_pdf("https://x/a.pdf", str(tmp_path / "z.pdf"), retries=2) is False


# ------------------------------------------------------------------ workflows
def _arxiv_result(doi, short_id, url):
    return SimpleNamespace(doi=doi, get_short_id=lambda: short_id, pdf_url=url)


def test_arxiv_workflow_names_files_skips_existing_and_logs_failures(monkeypatch, tmp_path):
    results = [_arxiv_result("10.1/a", "1", "https://arxiv.org/pdf/1"), _arxiv_result("", "2101.2", "https://arxiv.org/pdf/2"),
               _arxiv_result("10.1/c", "3", "https://arxiv.org/pdf/3")]
    monkeypatch.setattr(dp, "search_arxiv", lambda keyword, n: results)
    target = tmp_path / "pdfs"
    target.mkdir()
    (target / "10.1_c.pdf").write_bytes(b"%PDF-existing")
    got = []
    monkeypatch.setattr(dp, "download_pdf", lambda url, dest: got.append((url, Path(dest).name)) or url.endswith("1"))
    dp.run_arxiv_workflow("kw", str(target), 5)
    assert got == [("https://arxiv.org/pdf/1", "10.1_a.pdf"), ("https://arxiv.org/pdf/2", "2101.2.pdf")]


def test_search_arxiv_builds_relevance_query(monkeypatch):
    captured = {}

    class Client:
        def results(self, search):
            captured["search"] = search
            return iter(["r"])

    fake = SimpleNamespace(Client=Client, Search=lambda **kw: SimpleNamespace(**kw),
                           SortCriterion=SimpleNamespace(Relevance="rel"), SortOrder=SimpleNamespace(Descending="desc"))
    monkeypatch.setitem(sys.modules, "arxiv", fake)
    assert list(dp.search_arxiv("polymers", 7)) == ["r"]
    assert (captured["search"].query, captured["search"].max_results, captured["search"].sort_by) == ("polymers", 7, "rel")


def test_openalex_workflow_skips_incomplete_and_existing_works(monkeypatch, tmp_path):
    items = [SimpleNamespace(id="W1", doi="10.1/a", primary_location=SimpleNamespace(pdf_url="https://x/a.pdf")),
             SimpleNamespace(id="W2", doi=None, primary_location=SimpleNamespace(pdf_url="https://x/b.pdf")),
             SimpleNamespace(id="W3", doi="10.1/c", primary_location=None),
             SimpleNamespace(id="W4", doi="10.1/d", primary_location=SimpleNamespace(pdf_url="https://x/d.pdf"))]

    class Query:
        def per_page(self, n):
            return self

        def execute(self):
            return items

    class Works:
        def search(self, title):
            return Query()

    import pyalex

    monkeypatch.setattr(pyalex, "Works", Works)
    target = tmp_path / "t"
    target.mkdir()
    (target / "10.1_d.pdf").write_bytes(b"%PDF")
    got = []
    monkeypatch.setattr(dp, "download_pdf", lambda url, dest: got.append(Path(dest).name) or True)
    dp.run_openalex_workflow("kw", str(target), 10)
    assert got == ["10.1_a.pdf"]


def test_main_dispatches_on_source(monkeypatch):
    calls = []
    monkeypatch.setattr(dp, "run_arxiv_workflow", lambda k, t, n: calls.append(("arxiv", k, t, n)))
    monkeypatch.setattr(dp, "run_openalex_workflow", lambda k, t, n: calls.append(("openalex", k, t, n)))
    monkeypatch.setattr(sys, "argv", ["prog", "--keyword", "x", "--max-results", "3"])
    dp.main()
    monkeypatch.setattr(sys, "argv", ["prog", "--keyword", "y", "--source", "openalex", "--target", "T"])
    dp.main()
    assert calls == [("arxiv", "x", "./pdfs", 3), ("openalex", "y", "T", 500)]

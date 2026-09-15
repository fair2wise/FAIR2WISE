import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import download_pdfs


class FakeResponse:
    def __init__(self, chunks, *, content_type="application/pdf"):
        self._chunks = chunks
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield from self._chunks


def test_download_pdf_saves_valid_pdf_response(tmp_path, monkeypatch):
    dest = tmp_path / "paper.pdf"
    resp = FakeResponse([b"%P", b"DF-1.4\nbody"])

    monkeypatch.setattr(download_pdfs.requests, "get", lambda *args, **kwargs: resp)
    monkeypatch.setattr(download_pdfs.time, "sleep", lambda seconds: None)

    assert download_pdfs.download_pdf("https://example.test/paper.pdf", str(dest))
    assert dest.read_bytes() == b"%PDF-1.4\nbody"
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_pdf_rejects_html_response(tmp_path, monkeypatch):
    dest = tmp_path / "landing-page.pdf"
    resp = FakeResponse(
        [b"<html><title>Article landing page</title></html>"],
        content_type="text/html",
    )

    monkeypatch.setattr(download_pdfs.requests, "get", lambda *args, **kwargs: resp)
    monkeypatch.setattr(download_pdfs.time, "sleep", lambda seconds: None)

    assert not download_pdfs.download_pdf("https://example.test/article", str(dest))
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_rewrite_maps_repository_landings_and_rejects_publishers():
    assert (
        download_pdfs.rewrite_oa_pdf_url("https://arxiv.org/abs/2401.00001")
        == "https://arxiv.org/pdf/2401.00001"
    )
    assert (
        download_pdfs.rewrite_oa_pdf_url(
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC11664917/pdf/jp4c05774.pdf"
        )
        == "https://europepmc.org/articles/PMC11664917?pdf=render"
    )
    assert (
        download_pdfs.rewrite_oa_pdf_url(
            "https://www.ncbi.nlm.nih.gov/pmc/articles/7560964"
        )
        == "https://europepmc.org/articles/PMC7560964?pdf=render"
    )
    assert (
        download_pdfs.rewrite_oa_pdf_url("https://www.osti.gov/servlets/purl/1656507")
        == "https://www.osti.gov/servlets/purl/1656507"
    )
    assert download_pdfs.rewrite_oa_pdf_url("https://www.osti.gov/biblio/1656507") is None
    assert (
        download_pdfs.rewrite_oa_pdf_url(
            "https://www.osti.gov/biblio/1656507", osti_biblio_ok=True
        )
        == "https://www.osti.gov/servlets/purl/1656507"
    )
    assert download_pdfs.rewrite_oa_pdf_url(
        "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/pol.20210414"
    ) is None
    assert download_pdfs.rewrite_oa_pdf_url(
        "https://pubmed.ncbi.nlm.nih.gov/29099210"
    ) is None
    assert (
        download_pdfs.rewrite_oa_pdf_url("https://core.ac.uk/download/pdf/123.pdf")
        == "https://core.ac.uk/download/pdf/123.pdf"
    )
    assert download_pdfs.rewrite_oa_pdf_url("https://core.ac.uk/reader/123") is None
    assert download_pdfs.is_reliable_pdf_url("https://www.osti.gov/servlets/purl/1656507")
    assert not download_pdfs.is_reliable_pdf_url(
        "https://iopscience.iop.org/article/10.1088/1361-648X/ac0194/pdf"
    )


def test_select_openalex_oa_pdfs_keeps_osti_not_publisher():
    urls, arxiv_id, sources = download_pdfs.select_openalex_oa_pdfs(
        {
            "doi": "https://doi.org/10.1021/acs.macromol.8b02198",
            "open_access": {
                "is_oa": True,
                "oa_url": "https://www.osti.gov/servlets/purl/1656507",
            },
            "best_oa_location": {
                "is_oa": True,
                "version": "submittedVersion",
                "pdf_url": "https://www.osti.gov/servlets/purl/1656507",
                "landing_page_url": "https://www.osti.gov/biblio/1656507",
            },
            "locations": [
                {
                    "is_oa": False,
                    "pdf_url": None,
                    "landing_page_url": "https://pubs.acs.org/doi/10.1021/acs.macromol.8b02198",
                }
            ],
        }
    )
    assert urls == ["https://www.osti.gov/servlets/purl/1656507"]
    assert sources == ["openalex"]
    assert arxiv_id is None


def test_select_openalex_oa_pdfs_tries_listed_publisher_pdf_url():
    urls, _, sources = download_pdfs.select_openalex_oa_pdfs(
        {
            "open_access": {
                "is_oa": True,
                "oa_url": "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/pol.20210414",
            },
            "best_oa_location": {
                "is_oa": True,
                "pdf_url": "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/pol.20210414",
            },
            "locations": [
                {
                    "is_oa": False,
                    "landing_page_url": "https://www.osti.gov/biblio/1976371",
                }
            ],
        }
    )
    assert urls == [
        "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/pol.20210414"
    ]
    assert sources == ["openalex"]


def test_select_openalex_oa_pdfs_prefers_repository_over_publisher():
    urls, _, _ = download_pdfs.select_openalex_oa_pdfs(
        {
            "best_oa_location": {
                "is_oa": True,
                "pdf_url": "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/example",
            },
            "locations": [
                {
                    "is_oa": True,
                    "pdf_url": "https://www.osti.gov/servlets/purl/1656507",
                }
            ],
        }
    )
    assert urls == [
        "https://www.osti.gov/servlets/purl/1656507",
        "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/example",
    ]


def test_listed_oa_pdf_url_rejects_scihub_and_non_pdf_landings():
    assert (
        download_pdfs.listed_oa_pdf_url(
            "https://sci-hub.se/10.1021/acs.macromol.8b02198"
        )
        is None
    )
    assert (
        download_pdfs.listed_oa_pdf_url(
            "https://pubs.acs.org/doi/10.1021/acs.macromol.8b02198"
        )
        is None
    )
    assert (
        download_pdfs.listed_oa_pdf_url(
            "https://pubs.acs.org/doi/pdf/10.1021/acs.macromol.8b02198"
        )
        == "https://pubs.acs.org/doi/pdf/10.1021/acs.macromol.8b02198"
    )


def test_select_openalex_pmcid_and_arxiv_abs_become_pdfs():
    urls, arxiv_id, sources = download_pdfs.select_openalex_oa_pdfs(
        {
            "ids": {"pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123456"},
            "locations": [
                {
                    "is_oa": True,
                    "landing_page_url": "https://arxiv.org/abs/1703.02896",
                    "pdf_url": None,
                }
            ],
        }
    )
    assert "https://arxiv.org/pdf/1703.02896" in urls
    assert "https://europepmc.org/articles/PMC123456?pdf=render" in urls
    assert arxiv_id == "1703.02896"
    assert sources == ["openalex"]


def test_select_unpaywall_skips_publisher_keeps_repository():
    urls, sources = download_pdfs.select_unpaywall_oa_pdfs(
        {
            "best_oa_location": {
                "is_oa": True,
                "host_type": "publisher",
                "url_for_pdf": "https://pubs.acs.org/doi/pdf/10.1021/example",
            },
            "oa_locations": [
                {
                    "is_oa": True,
                    "host_type": "repository",
                    "url_for_pdf": "https://www.osti.gov/servlets/purl/1656507",
                }
            ],
        }
    )
    assert urls[0] == "https://www.osti.gov/servlets/purl/1656507"
    assert "https://pubs.acs.org/doi/pdf/10.1021/example" in urls
    assert sources == ["unpaywall"]


class _JsonResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise download_pdfs.requests.HTTPError(str(self.status_code))


def test_host_delay_uses_cli_as_floor():
    download_pdfs.set_delay_floor(2.0)
    assert download_pdfs.host_delay("api.core.ac.uk") == 2.0
    download_pdfs.set_delay_floor(0.5)
    assert download_pdfs.host_delay("api.semanticscholar.org") == 1.2
    download_pdfs.set_delay_floor(1.0)


def test_semantic_scholar_batches_500_and_keys_by_input_doi(monkeypatch):
    dois = [f"10.1234/paper-{i}" for i in range(501)]
    calls = []

    def fake_post(url, **kwargs):
        ids = (kwargs.get("json") or {}).get("ids") or []
        calls.append(ids)
        assert kwargs.get("params") == {"fields": "openAccessPdf"}
        return _JsonResponse(
            [{"openAccessPdf": {"url": "https://arxiv.org/pdf/2401.00001"}} for _ in ids]
        )

    monkeypatch.setattr(download_pdfs.requests, "post", fake_post)
    monkeypatch.setattr(download_pdfs.time, "sleep", lambda seconds: None)
    result = download_pdfs.fetch_semantic_scholar_batch_oa_pdfs(dois)
    assert [len(chunk) for chunk in calls] == [500, 1]
    assert calls[0][0] == "DOI:10.1234/paper-0"
    assert calls[1] == ["DOI:10.1234/paper-500"]
    assert dois[0] in result and dois[500] in result
    assert result[dois[0]] == (["https://arxiv.org/pdf/2401.00001"], ["semantic-scholar"])
    assert result[dois[500]][1] == ["semantic-scholar"]


def test_semantic_scholar_drops_publisher_hosts(monkeypatch):
    def fake_post(url, **kwargs):
        return _JsonResponse(
            [
                {
                    "openAccessPdf": {
                        "url": "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/pol.20210414"
                    }
                }
            ]
        )

    monkeypatch.setattr(download_pdfs.requests, "post", fake_post)
    monkeypatch.setattr(download_pdfs.time, "sleep", lambda seconds: None)
    result = download_pdfs.fetch_semantic_scholar_batch_oa_pdfs(["10.1002/pol.20210414"])
    assert result["10.1002/pol.20210414"] == ([], [])


def test_core_sends_key_in_header_not_query(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _JsonResponse(
            {"results": [{"downloadUrl": "https://core.ac.uk/download/pdf/1.pdf"}]}
        )

    monkeypatch.setattr(download_pdfs.requests, "get", fake_get)
    monkeypatch.setattr(download_pdfs.time, "sleep", lambda seconds: None)
    urls, sources = download_pdfs.fetch_core_oa_pdfs(
        "10.1234/core-example", "secret-core-key"
    )
    assert "secret-core-key" not in captured["url"]
    params = captured["kwargs"].get("params") or {}
    assert "secret-core-key" not in str(params)
    assert "apiKey" not in params and "api_key" not in params
    headers = captured["kwargs"].get("headers") or {}
    assert headers.get("Authorization") == "Bearer secret-core-key"
    assert urls == ["https://core.ac.uk/download/pdf/1.pdf"]
    assert sources == ["core"]


def test_core_skips_without_key(monkeypatch):
    monkeypatch.delenv("CORE_API_KEY", raising=False)

    def fake_get(*args, **kwargs):
        raise AssertionError("CORE must not be called without a key")

    monkeypatch.setattr(download_pdfs.requests, "get", fake_get)
    urls, sources = download_pdfs.fetch_core_oa_pdfs("10.1234/x", "")
    assert urls == []
    assert sources == []


def test_osti_extracts_purl(monkeypatch):
    def fake_get(url, **kwargs):
        assert "osti.gov" in url
        assert kwargs.get("params") == {"doi": "10.1234/osti-example"}
        return _JsonResponse(
            [
                {
                    "osti_id": "1656507",
                    "links": [
                        {
                            "rel": "fulltext",
                            "href": "https://www.osti.gov/servlets/purl/1656507",
                        }
                    ],
                }
            ]
        )

    monkeypatch.setattr(download_pdfs.requests, "get", fake_get)
    monkeypatch.setattr(download_pdfs.time, "sleep", lambda seconds: None)
    urls, sources = download_pdfs.fetch_osti_oa_pdfs("https://doi.org/10.1234/osti-example")
    assert urls == ["https://www.osti.gov/servlets/purl/1656507"]
    assert sources == ["osti"]


def test_europepmc_search_extracts_pmcid_pdf(monkeypatch):
    def fake_get(url, **kwargs):
        assert "europepmc" in url
        assert kwargs["params"]["query"] == 'DOI:"10.1234/epmc-example"'
        return _JsonResponse(
            {
                "resultList": {
                    "result": [
                        {
                            "pmcid": "PMC123456",
                            "fullTextUrlList": {
                                "fullTextUrl": [
                                    {
                                        "documentStyle": "pdf",
                                        "url": "https://europepmc.org/articles/PMC123456?pdf=render",
                                    }
                                ]
                            },
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr(download_pdfs.requests, "get", fake_get)
    monkeypatch.setattr(download_pdfs.time, "sleep", lambda seconds: None)
    urls, sources = download_pdfs.fetch_europepmc_search_oa_pdfs(
        "10.1234/epmc-example"
    )
    assert "https://europepmc.org/articles/PMC123456?pdf=render" in urls
    assert sources == ["europepmc"]


def test_crossref_tdm_picks_allowlisted_pdf_only():
    urls, sources = download_pdfs.select_crossref_tdm_pdfs(
        {
            "link": [
                {
                    "URL": "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/example",
                    "content-type": "application/pdf",
                    "intended-application": "text-mining",
                },
                {
                    "URL": "https://www.osti.gov/servlets/purl/1656507",
                    "content-type": "application/pdf",
                    "intended-application": "text-mining",
                },
                {
                    "URL": "https://arxiv.org/pdf/2401.00001",
                    "content-type": "application/pdf",
                    "intended-application": "similarity-checking",
                },
            ]
        }
    )
    assert urls == ["https://www.osti.gov/servlets/purl/1656507"]
    assert sources == ["crossref-tdm"]

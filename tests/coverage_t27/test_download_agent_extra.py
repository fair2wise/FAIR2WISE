"""DownloadAgent helpers and search/rank/validate paths with fake arXiv, OpenAlex and chat backends."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("academy")

from app.modules.f2w_agent import download_agent as da
from app.modules.term_extractor import clients


# ------------------------------------------------------------------ pure helpers
def test_openalex_sanitiser_titles_and_env_parsing(monkeypatch):
    assert da._sanitize_openalex_search("what?  is *this*??") == "what is this"
    assert da._sanitize_openalex_search(None) == ""
    assert da._normalize_title("A Study: of P3HT!") == "a study of p3ht"
    monkeypatch.setenv("T27_FLAG", " Off ")
    assert da._env_bool("T27_FLAG", True) is False
    monkeypatch.setenv("T27_FLAG", "1")
    assert da._env_bool("T27_FLAG", False) is True
    monkeypatch.delenv("T27_FLAG")
    assert da._env_bool("T27_FLAG", True) is True
    monkeypatch.setenv("T27_FLOAT", "2.5")
    assert da._env_float("T27_FLOAT", 1.0) == 2.5
    monkeypatch.setenv("T27_FLOAT", "abc")
    assert da._env_float("T27_FLOAT", 1.0) == 1.0
    monkeypatch.delenv("T27_FLOAT")
    assert da._env_float("T27_FLOAT", 4.0) == 4.0


def test_abstract_reconstruction_and_candidate_abstract_preference():
    inv = {"world": [1], "hello": [0, 2]}
    assert da._reconstruct_abstract(inv) == "hello world hello"
    assert da._reconstruct_abstract(None) == "" and da._reconstruct_abstract({}) == ""
    assert da._candidate_abstract({"abstract": "given", "abstract_inverted_index": inv}) == "given"
    assert da._candidate_abstract({"abstract_inverted_index": inv}) == "hello world hello"


def test_pdf_urls_explicit_and_openalex_locations_deduped_in_order():
    assert da._pdf_urls({"pdf_urls": ["a", " ", "b"]}) == ["a", "b"]
    work = {
        "best_oa_location": {"pdf_url": "https://x/best.pdf"},
        "primary_location": {"pdf_url": "https://x/best.pdf"},
        "locations": ["junk", {"pdf_url": "https://x/loc.pdf"}, {"pdf_url": None}],
        "open_access": {"oa_url": "https://x/oa"},
    }
    assert da._pdf_urls(work) == ["https://x/best.pdf", "https://x/loc.pdf", "https://x/oa"]
    assert da._pdf_urls({}) == []


def test_is_valid_pdf_checks_size_and_magic(tmp_path):
    ok = tmp_path / "ok.pdf"
    ok.write_bytes(b"%PDF-1.7 body")
    html = tmp_path / "login.pdf"
    html.write_bytes(b"<html>login</html>")
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    assert da._is_valid_pdf(ok) and not da._is_valid_pdf(html) and not da._is_valid_pdf(empty)
    assert not da._is_valid_pdf(tmp_path / "missing.pdf")


def test_openalex_work_id_and_safe_name():
    assert da._openalex_work_id({"id": "https://openalex.org/W123/"}) == "W123"
    assert da._openalex_work_id({"id": "https://arxiv.org/abs/1"}) is None
    assert da._openalex_work_id({}) is None
    assert da._safe_name({"doi": "https://doi.org/10.1/a/b"}) == "10.1_a_b"
    assert da._safe_name({"id": "https://openalex.org/W9"}) == "W9"
    assert da._safe_name({}) == "openalex_work"


def test_prefer_pdf_urls_ranks_trusted_repositories_first():
    urls = ["https://example.org/x", "https://zenodo.org/record/1/files/a.pdf", "https://osti.gov/servlets/purl/1",
            "https://www.biorxiv.org/content/a.full.pdf", "https://europepmc.org/articles/PMC1?pdf=render",
            "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1/pdf", "https://journals.plos.org/x/file?type=printable",
            "https://arxiv.org/pdf/2101.00001", "https://other.org/paper.pdf"]
    ranked = da._prefer_pdf_urls(urls)
    assert ranked[0].startswith("https://arxiv.org") and ranked[-1] == "https://example.org/x"
    assert ranked[1].startswith("https://journals.plos.org") and ranked[2].startswith("https://www.ncbi")
    assert ranked.index("https://other.org/paper.pdf") == len(ranked) - 2


def test_repository_names_summary_and_lexical_score():
    for url, name in [("https://arxiv.org/pdf/1", "arXiv"), ("https://biorxiv.org/x", "bioRxiv"),
                      ("https://medrxiv.org/x", "medRxiv"), ("https://chemrxiv.org/x", "ChemRxiv"),
                      ("https://europepmc.org/x", "PMC"), ("https://journals.plos.org/x", "PLOS"),
                      ("https://hal.science/x", "HAL"), ("https://osti.gov/x", "OSTI"),
                      ("https://escholarship.org/x", "eScholarship"), ("https://zenodo.org/x", "Zenodo"),
                      ("https://random.org/x", "Open repository")]:
        assert da._repository_name([url]) == name
    s = da._candidate_summary({"id": "W1", "display_name": "T", "abstract_inverted_index": {"hi": [0]},
                               "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/1"},
                               "open_access": {"oa_url": "o"}, "_score": "0.5"})
    assert s["title"] == "T" and s["abstract"] == "hi" and s["repository"] == "arXiv" and s["score"] == 0.5
    assert da._lexical_score("x ray scattering", "X-ray scattering of films") == pytest.approx(1.0)
    assert da._lexical_score("", "text") == 0.0 and da._lexical_score("q", "") == 0.0


def test_dedupe_key_and_search_queries():
    assert da._dedupe_key({"doi": " 10.1/A "}) == "10.1/a"
    assert da._dedupe_key({"id": "W1"}) == "w1"
    assert da._dedupe_key({"x": 1}).startswith("{")
    qs = da._search_queries("base q", ["t1", "t2", "t1", " "])
    assert qs[0] == "base q t1 t2 t1" and "t1" in qs and qs.count("t1") == 1 and qs[-1] == "base q"
    assert len(da._search_queries("q", [f"t{i}" for i in range(20)])) == da.MAX_SEARCH_QUERIES


def test_manifest_helpers_and_pdf_preview(tmp_path, monkeypatch):
    target = tmp_path / "run" / "pdfs"
    assert da._download_manifest_path(target) == tmp_path / "run" / "downloads.jsonl"
    manifest = tmp_path / "deep" / "m.jsonl"
    rec = da._manifest_record(query="q", missing_topics=["m"], work={"id": "W", "doi": "d", "title": "t", "_score": 1},
                              dest=Path("a.pdf"), urls=["u"], attempted_urls=[], status="failed", reason="r")
    da._append_download_record(manifest, rec)
    da._append_download_record(manifest, rec)
    lines = manifest.read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["status"] == "failed" and json.loads(lines[0])["openalex_id"] == "W"

    class Doc:
        def __init__(self):
            self.closed = False

        def __len__(self):
            return 5

        def __getitem__(self, i):
            return SimpleNamespace(get_text=lambda kind: f"page{i} " + "z" * 3000)

        def close(self):
            self.closed = True

    doc = Doc()
    from app.modules.literature import pdf_reader

    monkeypatch.setattr(pdf_reader, "open_pdf", lambda p: doc)
    preview = da._extract_pdf_preview(tmp_path / "x.pdf")
    assert preview.startswith("page0") and "page1" in preview and "page2" not in preview and len(preview) == 4000
    assert doc.closed

    def broken(p):
        raise OSError("bad pdf")

    monkeypatch.setattr(pdf_reader, "open_pdf", broken)
    assert da._extract_pdf_preview(tmp_path / "x.pdf") == ""


# ------------------------------------------------------------------ configuration
def test_agent_defaults_from_environment(monkeypatch):
    monkeypatch.setenv("F2W_DOWNLOAD_DELAY_SECONDS", "0")
    monkeypatch.setenv("F2W_VALIDATE_DOWNLOADED_PDFS", "no")
    monkeypatch.setenv("OPENALEX_EMAIL", "me@example.org")
    monkeypatch.setenv("KG_RAG_BACKEND", "ollama")
    a = da.DownloadAgent()
    assert (a._download_delay_seconds, a._validate_downloads, a._mailto, a._backend) == (0.0, False, "me@example.org", "ollama")
    slept = []
    monkeypatch.setattr(da.time, "sleep", slept.append)
    a._sleep_between_downloads()
    b = da.DownloadAgent(download_delay_seconds=0.5, validate_downloads=True)
    b._sleep_between_download_attempts()
    assert slept == [0.5]


# ------------------------------------------------------------------ arXiv / OpenAlex search
def _atom_entry(short_id, title="Title  here", doi="", pdf_url=None, year=2024):
    pdf = f'<link title="pdf" href="{pdf_url}" rel="related" type="application/pdf"/>' if pdf_url else ""
    doi_tag = f"<arxiv:doi>{doi}</arxiv:doi>" if doi else ""
    return (f"<entry><id>http://arxiv.org/abs/{short_id}v1</id><updated>{year}-01-02T03:04:05Z</updated>"
            f"<published>{year}-01-02T03:04:05Z</published><title>{title}</title><summary>A  summary</summary>"
            f"{pdf}{doi_tag}</entry>")


def _atom(entries):
    return ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:arxiv="http://arxiv.org/schemas/atom">' + "".join(entries) + "</feed>").encode()


@pytest.fixture
def fake_arxiv(monkeypatch):
    state = {"results": {}, "fail_for": set(), "searches": []}

    def get_bytes(url, **kw):
        query = parse_qs(urlsplit(url).query)
        text = query["search_query"][0]
        state["searches"].append((text, int(query["max_results"][0])))
        if text in state["fail_for"]:
            from app.modules.literature.legacy_download import JSONFetchError

            raise JSONFetchError("HTTP 503")
        return _atom(state["results"].get(text, []))

    monkeypatch.setattr(da, "hardened_get_bytes", get_bytes)
    return state


def test_arxiv_candidates_are_normalised_deduped_and_pool_limited(fake_arxiv):
    fake_arxiv["results"] = {
        "q m1": [_atom_entry("2401.00001", doi="10.1/x", pdf_url="https://arxiv.org/pdf/2401.00001"),
                 _atom_entry("2401.00001", doi="10.1/x"),
                 _atom_entry("2401.00002")],
        "m1": [_atom_entry("2401.00003")],
    }
    agent = da.DownloadAgent(download_delay_seconds=0)
    out = agent._search_arxiv_candidates("q", ["m1"], pool=3)
    assert [w["id"] for w in out] == ["https://arxiv.org/abs/2401.00001v1", "https://arxiv.org/abs/2401.00002v1",
                                       "https://arxiv.org/abs/2401.00003v1"]
    assert out[0]["doi"] == "https://doi.org/10.1/x" and out[0]["title"] == "Title here"
    assert out[1]["doi"] == "https://doi.org/10.48550/arXiv.2401.00002v1"
    assert out[1]["pdf_urls"] == ["https://arxiv.org/pdf/2401.00002v1"] and out[0]["repository"] == "arXiv"
    assert agent._search_arxiv_candidates("q", ["m1"], pool=1)[0]["id"].endswith("00001v1")


def test_arxiv_search_survives_query_failures(fake_arxiv):
    fake_arxiv["fail_for"] = {"q m1"}
    fake_arxiv["results"] = {"m1": [_atom_entry("2401.00009")]}
    agent = da.DownloadAgent(download_delay_seconds=0)
    assert [w["id"] for w in agent._search_arxiv_candidates("q", ["m1"], pool=5)] == ["https://arxiv.org/abs/2401.00009v1"]
    assert fake_arxiv["searches"].count(("q m1", 5)) == 3  # one try plus the package's two retries


class FakeWorks:
    """``hardened_get_json`` stand-in serving recorded OpenAlex ``/works`` answers keyed by search text."""

    data: dict = {}
    fail_oa = False
    fail_all = False
    headers: list = []
    urls: list = []

    def __call__(self, url, **kw):
        query = parse_qs(urlsplit(url).query)
        oa = "filter" in query
        type(self).urls.append(url)
        type(self).headers.append(kw.get("headers"))
        if self.fail_all or (oa and self.fail_oa):
            raise RuntimeError("openalex down")
        return {"meta": {}, "results": self.data.get(query["search"][0], [])}


@pytest.fixture
def fake_openalex(monkeypatch):
    FakeWorks.data, FakeWorks.fail_oa, FakeWorks.fail_all = {}, False, False
    FakeWorks.headers, FakeWorks.urls = [], []
    fake = FakeWorks()
    monkeypatch.setattr(da, "hardened_get_json", fake)
    return fake


def test_search_candidates_merges_arxiv_first_then_openalex_dedupes_by_title_and_doi(fake_openalex, monkeypatch):
    arxiv_hit = {"id": "https://arxiv.org/abs/1", "doi": "https://doi.org/10.1/a", "title": "Shared Title",
                 "pdf_urls": ["https://arxiv.org/pdf/1"]}
    agent = da.DownloadAgent(download_delay_seconds=0, mailto="me@example.org")
    monkeypatch.setattr(agent, "_search_arxiv_candidates", lambda q, m, pool: [arxiv_hit])
    fake_openalex.data = {"q": [
        {"id": "W1", "doi": "https://doi.org/10.1/a", "title": "Other", "pdf_urls": ["https://zenodo.org/a.pdf"]},
        {"id": "W2", "title": "shared  title!", "pdf_urls": ["https://zenodo.org/b.pdf"]},
        {"id": "W3", "title": "No reliable url", "pdf_urls": ["https://publisher.example/p"]},
        {"id": "W4", "title": "Fresh", "pdf_urls": ["https://zenodo.org/c.pdf"]},
    ]}
    out = agent._search_candidates("q", [], pool=5)
    assert [w["id"] for w in out] == ["https://arxiv.org/abs/1", "W4"]
    assert out[1]["repository"] == "Zenodo" and FakeWorks.headers[0]["From"] == "me@example.org" and "mailto=me%40example.org" in FakeWorks.urls[0]
    assert agent._search_candidates("q", [], pool=1) == [arxiv_hit]  # pool already satisfied by arXiv


def test_search_one_falls_back_without_oa_filter(fake_openalex):
    agent = da.DownloadAgent(download_delay_seconds=0)
    fake_openalex.data = {"t": [{"id": "W"}]}
    fake_openalex.fail_oa = True
    assert agent._search_one("t", 10) == [{"id": "W"}]
    fake_openalex.fail_all = True
    assert agent._search_one("t", 10) == []


# ------------------------------------------------------------------ ranking and validation
class FakeChat:
    def __init__(self, reply=None, boom=None):
        self.reply, self.boom, self.prompts = reply, boom, []

    def chat(self, prompt, **kw):
        self.prompts.append((prompt, kw))
        if self.boom:
            raise self.boom
        return self.reply


@pytest.fixture
def chat(monkeypatch):
    def install(reply=None, boom=None):
        client = FakeChat(reply, boom)
        monkeypatch.setattr(clients, "make_chat_client", lambda **kw: client)
        return client

    return install


def test_rank_uses_llm_scores_and_sorts_descending(chat):
    client = chat('Here: {"0": 0.2, "1": 0.9}')
    agent = da.DownloadAgent(download_delay_seconds=0, backend="ollama", model="m")
    cands = [{"title": "weak", "abstract": "a" * 3000}, {"title": "strong", "abstract_inverted_index": {"x": [0]}}]
    ranked = agent._rank("question", cands)
    assert [w["title"] for w in ranked] == ["strong", "weak"] and ranked[0]["_score"] == 0.9
    assert "[1] TITLE: strong\nABSTRACT: x" in client.prompts[0][0]
    assert "QUESTION:\nquestion" in client.prompts[0][0]
    assert "a" * 1201 not in client.prompts[0][0]


def test_rank_falls_back_to_lexical_overlap_when_llm_fails(chat):
    chat(boom=TimeoutError("down"))
    agent = da.DownloadAgent(download_delay_seconds=0)
    cands = [{"title": "unrelated cats", "abstract": ""}, {"title": "x ray scattering", "abstract": "polymer films"}]
    ranked = agent._rank("x ray scattering of polymer", cands)
    assert ranked[0]["title"] == "x ray scattering" and ranked[0]["_score"] > ranked[1]["_score"]
    chat("no json here")  # reply without a JSON object scores everything 0.0 (no exception)
    assert [w["_score"] for w in agent._rank("q", [{"title": "t"}])] == [0.0]


def test_validate_downloaded_pdf_decisions(chat, tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1")
    work = {"title": "T", "abstract": "A"}
    off = da.DownloadAgent(download_delay_seconds=0, validate_downloads=False)
    assert off._validate_downloaded_pdf("q", work, pdf) is True
    agent = da.DownloadAgent(download_delay_seconds=0, validate_downloads=True)
    monkeypatch.setattr(da, "_extract_pdf_preview", lambda p: "")
    assert agent._validate_downloaded_pdf("q", work, pdf) is True  # nothing to judge: fail open
    monkeypatch.setattr(da, "_extract_pdf_preview", lambda p: "preview text")
    chat('{"is_paper": true, "is_relevant": true}')
    assert agent._validate_downloaded_pdf("q", work, pdf) is True
    chat('{"is_paper": true, "is_relevant": false, "reason": "off topic"}')
    assert agent._validate_downloaded_pdf("q", work, pdf) is False
    chat("garbage")
    assert agent._validate_downloaded_pdf("q", work, pdf) is False
    chat(boom=RuntimeError("llm"))
    assert agent._validate_downloaded_pdf("q", work, pdf) is True  # validator failure keeps the file


# ------------------------------------------------------------------ refresh and store forwarding
def test_refresh_candidate_urls_merges_openalex_unpaywall_and_rewrites(monkeypatch):
    full = {"id": "https://openalex.org/W5", "title": "Full Title", "doi": "https://doi.org/10.1/z",
            "publication_year": 2020, "abstract_inverted_index": {"abs": [0]},
            "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/2101.00001"},
            "open_access": {"oa_url": "https://zenodo.org/record/1/files/p.pdf"}}

    def get_json(url, **kw):
        if "api.unpaywall.org" in url:
            return {"best_oa_location": {"host_type": "repository", "is_oa": True,
                                         "url_for_pdf": "https://www.biorxiv.org/content/10.1101/x.full.pdf"}}
        assert urlsplit(url).path == "/works/W5"
        return full

    monkeypatch.setattr(da, "hardened_get_json", get_json)
    agent = da.DownloadAgent(download_delay_seconds=0, mailto="curator@lbl.gov")
    cand = {"id": "https://openalex.org/W5", "title": "", "pdf_urls": ["https://publisher.example/x"],
            "oa_url": "https://zenodo.org/record/1/files/p.pdf"}
    out = agent._refresh_candidate_urls(cand)
    assert out["title"] == "Full Title" and out["abstract"] == "abs" and out["doi"] == "https://doi.org/10.1/z"
    assert out["publication_year"] == 2020
    assert out["pdf_urls"][0] == "https://arxiv.org/pdf/2101.00001"  # arXiv ranked first, publisher URL dropped
    assert "https://publisher.example/x" not in out["pdf_urls"]
    assert len(out["pdf_urls"]) == len(set(out["pdf_urls"])) and any("biorxiv" in u for u in out["pdf_urls"])
    assert cand["title"] == ""  # input is not mutated


def test_refresh_tolerates_openalex_failure_and_non_openalex_ids(monkeypatch):
    def get_json(url, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(da, "hardened_get_json", get_json)
    agent = da.DownloadAgent(download_delay_seconds=0)
    out = agent._refresh_candidate_urls({"id": "https://openalex.org/W5", "pdf_urls": ["https://arxiv.org/pdf/2101.00001"]})
    assert out["pdf_urls"] == ["https://arxiv.org/pdf/2101.00001"]
    out = agent._refresh_candidate_urls({"id": "https://arxiv.org/abs/1", "doi": "10.1/x", "pdf_urls": []})
    assert out["pdf_urls"] == []


def test_forward_to_store_is_best_effort(tmp_path):
    calls = []
    index = SimpleNamespace(adopt_download=lambda ctx, root_id, target, dest, doi, source_url:
                            calls.append((ctx, root_id, doi, source_url)))
    agent = da.DownloadAgent(download_delay_seconds=0, legacy_index=index, store_context="ctx")
    agent._forward_to_store(tmp_path, tmp_path / "a.pdf", {"doi": "10.1/x"}, "https://u")
    assert calls[0][0] == "ctx" and calls[0][1].startswith("downloads-") and calls[0][2:] == ("10.1/x", "https://u")
    bad = SimpleNamespace(adopt_download=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store down")))
    da.DownloadAgent(download_delay_seconds=0, legacy_index=bad, store_context="c")._forward_to_store(
        tmp_path, tmp_path / "a.pdf", {})  # swallowed
    da.DownloadAgent(download_delay_seconds=0)._forward_to_store(tmp_path, tmp_path / "a.pdf", {})  # no store: no-op

"""harvest_rsoxs (T27f), part 1: text helpers, manifest merge, arXiv and OpenAlex searches (fake ``arxiv``/``pyalex``)."""
from __future__ import annotations

from datetime import datetime
import logging
import sys
import types
from typing import Any

import pytest

from app.modules.commands import harvest_rsoxs as hv

from .harvest_fakes import GOOD_ABSTRACT, GOOD_TITLE, ArxivResult, ArxivWorld, OpenAlexWorld

BAD_TITLE = "Cooking pasta for beginners"


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(hv.time, "sleep", calls.append)
    return calls


# -- facility, abstract, urls -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("blob", "facility", "beamline"), [
    ("Measured at Advanced Light Source beamline 11.0.1.2", "ALS", "11.0.1.2"),
    ("Advanced Light Source, beamline 11.0.1 end station", "ALS", "11.0.1.2"),
    ("Advanced Light Source", "ALS", None),
    ("Advanced Photon Source sector 29-ID", "APS", "29-ID"),
    ("Advanced Photon Source", "APS", None),
    ("SSRL beamline 13-3", "SSRL", "13-3"),
    ("Stanford Synchrotron Radiation Lightsource", "SSRL", None),
    ("Pohang Accelerator Laboratory", "PAL", None),
    ("NSLS-II 7-ID", "NSLS-II", None),
    ("Diamond Light Source I07", "Diamond", None),
    ("a lab bench", None, None), ("", None, None),
])
def test_detect_facility(blob, facility, beamline):
    assert hv.detect_facility(blob) == {"facility": facility, "beamline": beamline}


def test_reconstruct_abstract_orders_words_by_position():
    assert hv._reconstruct_abstract({"scattering": [1], "Resonant": [0], "x-ray": [2, 4], "soft": [3]}) == (
        "Resonant scattering x-ray soft x-ray")
    assert hv._reconstruct_abstract({"a": None}) == "" and hv._reconstruct_abstract(None) == ""
    assert hv._reconstruct_abstract(["not", "a", "dict"]) == "" and hv._reconstruct_abstract({}) == ""


def test_dedupe_key_prefers_doi_then_id_then_a_json_dump():
    assert hv._dedupe_key({"doi": " HTTPS://DOI.org/10.1/A ", "id": "x"}) == "https://doi.org/10.1/a"
    assert hv._dedupe_key({"id": " OA/W1 "}) == "oa/w1"
    assert hv._dedupe_key({"title": "t"}).startswith('{"title": "t"}')


def test_pdf_urls_collects_every_location_once_in_order():
    work = {"pdf_urls": ["https://a/1.pdf", " ", "https://a/1.pdf"], "best_oa_location": {"pdf_url": "https://b/2.pdf"},
            "primary_location": {"pdf_url": None}, "locations": [{"pdf_url": "https://c/3.pdf"}, "junk", {}],
            "open_access": {"oa_url": "https://b/2.pdf"}}
    assert hv._pdf_urls(work) == ["https://a/1.pdf", "https://b/2.pdf", "https://c/3.pdf"]
    assert hv._pdf_urls({"pdf_urls": "no", "best_oa_location": "no", "open_access": "no"}) == []


def test_reliable_pdf_urls_prefers_openalex_oa_locations_then_rewrites_listed_urls():
    work = {"pdf_urls": ["https://arxiv.org/pdf/2101.00001"]}
    assert any("arxiv.org" in u for u in hv._reliable_pdf_urls(work))
    assert hv._reliable_pdf_urls({}) == []
    assert hv._normalize_pdf_urls(["https://arxiv.org/pdf/2101.00001", "https://arxiv.org/pdf/2101.00001"]).count(
        "https://arxiv.org/pdf/2101.00001") <= 1


def test_europepmc_fallback_only_for_pmc_urls():
    out = hv._europepmc_fallback_urls(["https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/pdf/x.pdf",
                                       "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/", "https://x.org/a.pdf"])
    assert len(out) == 1 and "PMC1234567" in out[0]


def test_file_naming_helpers():
    assert hv._safe_name({"doi": "https://dx.doi.org/10.1/a b"}) == "10.1_a b"
    assert hv._safe_name({"id": "https://openalex.org/W42/"}) == "W42" and hv._safe_name({}) == "openalex_work"
    assert hv.doi_stem("") == "unknown" and hv.doi_stem(" https://doi.org/10.1/x ") == "10.1_x"
    assert hv._canonical_doi("doi.org/10.1/x") is None and hv._canonical_doi("10.1/X") == "https://doi.org/10.1/X"
    assert hv._canonical_doi(None) is None and hv._doi_key("10.1/X") == "https://doi.org/10.1/x"
    assert hv.year_from_work({"publication_date": "20xx"}) == "unknown"


def test_sha256_and_now(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    assert hv._sha256(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert datetime.fromisoformat(hv._now()).tzinfo is not None


# -- pdf preview ------------------------------------------------------------------------------------------------


class Doc:
    def __init__(self, n: int) -> None:
        self.page_count, self.closed, self.loaded = n, False, []

    def load_page(self, i: int):
        self.loaded.append(i)
        return types.SimpleNamespace(get_text=lambda: f" page{i}  text ")

    def close(self) -> None:
        self.closed = True


def _pdf(tmp_path):
    path = tmp_path / "p.pdf"
    path.write_bytes(b"%PDF")
    return path


def test_preview_reads_head_and_tail_pages(tmp_path, monkeypatch):
    import app.modules.literature.pdf_reader as reader

    doc = Doc(7)
    monkeypatch.setattr(reader, "open_pdf", lambda path: doc)
    assert hv.pdf_preview_text(_pdf(tmp_path)) == "page0 text page1 text page5 text page6 text"
    assert doc.loaded == [0, 1, 5, 6] and doc.closed
    short = Doc(3)
    monkeypatch.setattr(reader, "open_pdf", lambda path: short)
    hv.pdf_preview_text(_pdf(tmp_path))
    assert short.loaded == [0, 1, 2]
    one = Doc(1)
    monkeypatch.setattr(reader, "open_pdf", lambda path: one)
    hv.pdf_preview_text(_pdf(tmp_path), head_pages=3)
    assert one.loaded == [0]


def test_preview_of_a_missing_file_is_empty(tmp_path):
    assert hv.pdf_preview_text(tmp_path / "none.pdf") == "" and hv.pdf_preview_text(None) == ""  # type: ignore[arg-type]


def test_preview_falls_back_to_pypdf_and_skips_unreadable_pages(tmp_path, monkeypatch):
    import app.modules.literature.pdf_reader as reader

    def boom(path):
        raise RuntimeError("pdfium")

    monkeypatch.setattr(reader, "open_pdf", boom)

    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            if self.text is None:
                raise ValueError("bad page")
            return self.text

    class Reader:
        def __init__(self, path):
            self.pages = [Page("one"), Page(None), Page("three"), Page("four"), Page("five")]

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=Reader))
    assert hv.pdf_preview_text(_pdf(tmp_path)) == "one four five"

    def broken(path):
        raise RuntimeError("not a pdf")

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=broken))
    assert hv.pdf_preview_text(_pdf(tmp_path)) == ""
    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=lambda p: types.SimpleNamespace(
        pages=[Page("only")])))
    assert hv.pdf_preview_text(_pdf(tmp_path)) == "only"


# -- manifest ---------------------------------------------------------------------------------------------------


def test_manifest_defaults_roundtrip_and_extra_keys(tmp_path):
    path = tmp_path / "m" / "manifest.json"
    fresh = hv._load_manifest(path)
    assert fresh["corpus"] == "rsoxs" and fresh["papers"] == []
    fresh["papers"] = [{"title": "Low", "kg_priority": 0.1}, {"title": "High", "kg_priority": 0.9}]
    fresh["custom"] = {"keep": True}
    hv._save_manifest(path, fresh)
    assert not path.with_suffix(".json.tmp").exists()
    loaded = hv._load_manifest(path)
    assert loaded["custom"] == {"keep": True} and [p["title"] for p in loaded["papers"]] == ["High", "Low"]
    path.write_text('{"corpus": "other", "papers": null}')
    assert hv._load_manifest(path)["corpus"] == "other" and hv._load_manifest(path)["papers"] == []


def test_upsert_inserts_merges_and_keeps_an_on_disk_pdf():
    manifest: dict[str, Any] = {}
    hv._upsert_paper(manifest, {"doi": "10.1/A", "title": "t1"})
    assert manifest["papers"][0]["doi"] == "https://doi.org/10.1/A"
    hv._upsert_paper(manifest, {"arxiv_id": "2101.1", "title": "arx"})
    hv._upsert_paper(manifest, {"title": "no key"})
    hv._upsert_paper(manifest, {"arxiv_id": "2101.1", "title": "arx2"})
    assert [p["title"] for p in manifest["papers"]] == ["t1", "arx2", "no key"]
    manifest["papers"][0].update(pdf_path="papers/x.pdf", pdf_sha256="s", downloaded_at="d",
                                 ingestion={"status": "pending"})
    hv._upsert_paper(manifest, {"doi": "https://doi.org/10.1/a", "title": "again", "pdf_path": None,
                                "ingestion": {"status": "skipped", "error": "no oa"}})
    merged = manifest["papers"][0]
    assert merged["title"] == "again" and merged["pdf_path"] == "papers/x.pdf" and merged["pdf_sha256"] == "s"
    assert merged["downloaded_at"] == "d" and merged["ingestion"] == {"status": "pending", "error": None}
    hv._upsert_paper(manifest, {"doi": "10.1/a", "pdf_path": "papers/new.pdf"})
    assert manifest["papers"][0]["pdf_path"] == "papers/new.pdf"


def test_near_duplicates_collapse_to_the_journal_version_and_keep_both_as_versions():
    pre = {"doi": "https://doi.org/10.48550/arXiv.2101.00001", "title": GOOD_TITLE, "arxiv_id": "2101.00001",
           "publication_date": "2021-01-01"}
    journal = {"doi": "https://doi.org/10.1/j", "title": GOOD_TITLE + ".", "publication_date": "2022-01-01"}
    other = {"doi": "https://doi.org/10.1/o", "title": "Something entirely different about batteries"}
    out = hv.collapse_near_duplicates([pre, other, journal])
    assert len(out) == 2 and out[0]["doi"] == journal["doi"] and out[0]["arxiv_id"] == "2101.00001"
    assert {v["kind"] for v in out[0]["versions"]} == {"journal", "preprint"}
    again = hv.collapse_near_duplicates([journal, pre])  # journal first: the preprint is the loser
    assert again[0]["doi"] == journal["doi"] and len(again[0]["versions"]) == 2
    assert hv.titles_near_duplicate("", "x") is False and hv.titles_near_duplicate("the a", "an of") is False


def test_arxiv_id_from_work_looks_at_ids_and_locations():
    assert hv._arxiv_id_from_work({"doi": "https://doi.org/10.48550/arXiv.2101.00001"}) == "2101.00001"
    assert hv._arxiv_id_from_work({"primary_location": {"landing_page_url": "https://arxiv.org/abs/2102.00002"}}) == "2102.00002"
    assert hv._arxiv_id_from_work({"pdf_urls": ["https://arxiv.org/pdf/2103.00003"]}) == "2103.00003"
    assert hv._arxiv_id_from_work({"locations": ["junk"]}) is None


# -- arXiv search -----------------------------------------------------------------------------------------------


def test_arxiv_throttle_helpers():
    assert hv._arxiv_is_throttle(RuntimeError("HTTP 429 slow down")) and hv._arxiv_is_throttle(Exception("HTTP 503"))
    assert not hv._arxiv_is_throttle(Exception("HTTP 500"))
    assert [hv._arxiv_throttle_wait(n) for n in (-1, 0, 1, 2, 3, 9)] == [60.0, 60.0, 120.0, 180.0, 180.0, 180.0]


def test_work_from_arxiv_result_maps_fields_and_refuses_bad_urls():
    work = hv._work_from_arxiv_result(ArxivResult("2101.00001v1", doi="10.1/x", title="  A   title "))
    assert work["id"] == "https://arxiv.org/abs/2101.00001v1" and work["doi"] == "https://doi.org/10.1/x"
    assert work["title"] == "A title" and work["publication_year"] == 2021 and work["publication_date"] == "2021-05-04"
    assert work["pdf_urls"] == ["https://arxiv.org/pdf/2101.00001v1"] and work["source"] == "arxiv"
    kept = hv._work_from_arxiv_result(ArxivResult("2101.2", doi="https://doi.org/10.1/y", published=None))
    assert kept["doi"] == "https://doi.org/10.1/y" and kept["publication_date"] is None and kept["publication_year"] is None
    assert hv._work_from_arxiv_result(ArxivResult("2101.3"))["doi"] == "https://doi.org/10.48550/arXiv.2101.3"
    assert hv._work_from_arxiv_result(ArxivResult("2101.4", pdf_url="https://evil.example/x.pdf")) is None
    assert hv._work_from_arxiv_result(ArxivResult("", pdf_url="https://arxiv.org/pdf/")) is None
    assert hv._work_from_arxiv_result(ArxivResult("2101.5", pdf_url=None))["pdf_urls"] == ["https://arxiv.org/pdf/2101.5"]


def test_search_arxiv_filters_dedupes_and_backs_off_on_throttle(monkeypatch, sleeps, caplog):
    world = ArxivWorld({
        "q1": [ArxivResult("1"), ArxivResult("1"), ArxivResult("2", title=BAD_TITLE, summary="nothing"),
               ArxivResult("3", pdf_url="https://evil.example/x.pdf")],
        "q2": [RuntimeError("HTTP 429"), [ArxivResult("4", title=GOOD_TITLE + " II")]],
        "q3": RuntimeError("HTTP 500 server error"),
        "q4": RuntimeError("HTTP 503"),
    }).install(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="harvest_rsoxs"):
        out = hv.search_arxiv(["q1", "q2", "q3", "q4"], 80)
    assert [w["id"] for w in out] == ["https://arxiv.org/abs/1", "https://arxiv.org/abs/4"]
    assert world.clients[0]["page_size"] == 50 and world.clients[0]["delay_seconds"] == hv.ARXIV_DELAY_SECONDS
    assert world.calls.count("q2") == 2 and world.calls.count("q4") == hv.ARXIV_THROTTLE_MAX_RETRIES + 1
    assert sleeps == [60.0, 60.0, 120.0, 180.0, 180.0, 180.0]
    assert "arXiv search failed for 'q3'" in caplog.text and "backing off 60s" in caplog.text


def test_search_arxiv_uses_the_given_classifier(monkeypatch):
    ArxivWorld({"q": [ArxivResult("1", title="anything")]}).install(monkeypatch)
    assert hv.search_arxiv(["q"], 5, classifier=lambda t, a: "C") == []
    assert len(hv.search_arxiv(["q"], 5, classifier=lambda t, a: "A")) == 1


# -- OpenAlex search --------------------------------------------------------------------------------------------


def oa_work(title: str = GOOD_TITLE, doi: str | None = "https://doi.org/10.1/a", pdf: str | None = "https://arxiv.org/pdf/2101.00001",
            **extra: Any) -> dict[str, Any]:
    work: dict[str, Any] = {"display_name": title, "abstract_inverted_index": {"resonant": [0], "soft": [1], "x-ray": [2], "scattering": [3]}}
    if doi:
        work["doi"] = doi
    if pdf:
        work["pdf_urls"] = [pdf]
    return {**work, **extra}


def test_paginate_works_pages_get_fallback_and_failure(monkeypatch):
    world = OpenAlexWorld().install(monkeypatch)
    world.works["q"] = [{"n": i} for i in range(7)]
    query = sys.modules["pyalex"].Works().search("q")
    assert len(hv._paginate_works(query, 4)) == 4 and len(hv._paginate_works(query, 50)) == 7
    world.fail_paginate = True
    assert len(hv._paginate_works(sys.modules["pyalex"].Works().search("q"), 3)) == 3  # get() after paginate fails
    world.fail_paginate, world.no_paginate = False, True
    assert len(hv._paginate_works(sys.modules["pyalex"].Works().search("q"), 5)) == 5
    world.fail_get = True
    assert hv._paginate_works(sys.modules["pyalex"].Works().search("q"), 5) == []
    world.fail_paginate, world.no_paginate = True, False
    assert hv._paginate_works(sys.modules["pyalex"].Works().search("q"), 5) == []


def test_normalize_openalex_work_rebuilds_abstract_and_rejects_tier_c():
    work = hv._normalize_openalex_work(oa_work())
    assert work["title"] == GOOD_TITLE and work["abstract"] == "resonant soft x-ray scattering" and work["source"] == "openalex"
    assert work["pdf_urls"] and all("arxiv.org" in u for u in work["pdf_urls"])
    assert hv._normalize_openalex_work({"title": BAD_TITLE, "abstract": "cooking"}) is None
    assert hv._normalize_openalex_work(oa_work(), classifier=lambda t, a: "C") is None


def test_search_openalex_sets_the_contact_email_filters_oa_and_dedupes(monkeypatch):
    world = OpenAlexWorld().install(monkeypatch)
    world.works["RSoXS"] = [oa_work(), oa_work(doi="https://doi.org/10.1/b"), oa_work(BAD_TITLE, doi="https://doi.org/10.1/c", abstract_inverted_index={"pasta": [0]}),
                            oa_work(GOOD_TITLE + " II", doi="https://doi.org/10.1/d", pdf=None),
                            oa_work(GOOD_TITLE + " III", doi=None, id="https://openalex.org/W9")]
    world.works["q2"] = [oa_work(doi="https://doi.org/10.1/a")]
    out = hv.search_openalex(["RSoXS", "q2"], 10, "me@example.org")
    assert world.config.email == "me@example.org" and {"open_access": {"is_oa": True}} in world.filters
    assert [w.get("doi") for w in out] == ["https://doi.org/10.1/a", None]
    assert hv.search_openalex(["RSoXS"], 10, None, oa_only=False, require_pdf=False)[-1]["title"].endswith("II")
    assert len(world.filters) == 2  # no OA filter on the second call


def test_search_openalex_survives_failing_queries(monkeypatch, caplog):
    world = OpenAlexWorld().install(monkeypatch)
    world.works["ok"] = [oa_work()]
    world.works["dead"] = RuntimeError("down")
    world.search_failures["flaky"] = 1  # the first search() raises, the fallback search() works
    world.works["flaky"] = [oa_work(GOOD_TITLE + " flaky", doi="https://doi.org/10.1/f")]
    world.search_failures["gone"] = 2  # both attempts raise
    with caplog.at_level(logging.WARNING, logger="harvest_rsoxs"):
        out = hv.search_openalex(["dead", "flaky", "gone", "ok"], 5, None)
    assert [w["doi"] for w in out] == ["https://doi.org/10.1/f", "https://doi.org/10.1/a"]
    assert "OpenAlex fallback failed for 'gone'" in caplog.text and "OpenAlex search failed for 'flaky'" in caplog.text


def test_author_gate_requires_an_als_affiliation_for_cheng_wang_only():
    assert hv._author_is_target("Thomas Ferron", {"display_name": "Thomas D. Ferron"}) is False  # not a substring
    assert hv._author_is_target("Thomas Ferron", {"display_name": "Thomas Ferron"}) is True
    assert hv._author_is_target("Cheng Wang", {"display_name": "Cheng Wang"}) is False
    assert hv._author_is_target("Cheng Wang", {"display_name": "Cheng Wang", "x": "Advanced Light Source"}) is True
    assert hv._author_is_target("Cheng Wang", {"display_name": "Cheng Wang", "x": "Berkeley Lab"}) is True
    assert hv._author_is_target("Someone", {"display_name": "someone else"}) is True
    assert hv._author_is_target("Nobody", {"display_name": "Somebody"}) is False


def test_search_openalex_authors_follows_target_ids_only(monkeypatch, caplog):
    world = OpenAlexWorld().install(monkeypatch)
    world.authors["Cheng Wang"] = [{"id": "A2", "display_name": "Cheng Wang", "inst": "Lawrence Berkeley National Lab"},
                                   {"id": "A3", "display_name": "Cheng Wang"}, {"display_name": "Cheng Wang LBNL"}]
    world.authors["Thomas Ferron"] = RuntimeError("authors api down")
    world.works["RSoXS"] = [oa_work(pdf=None)]
    world.works['"resonant soft x-ray scattering"'] = [oa_work(pdf=None), oa_work(BAD_TITLE, doi="https://doi.org/10.1/z", abstract_inverted_index={"pasta": [0]})]
    world.works['"11.0.1.2"'] = [oa_work(GOOD_TITLE + " beamline", doi="https://doi.org/10.1/e", pdf=None)]
    with caplog.at_level(logging.INFO, logger="harvest_rsoxs"):
        out = hv.search_openalex_authors(["Cheng Wang", "Thomas Ferron"], 5, "a@b.org")
    assert [w["doi"] for w in out] == ["https://doi.org/10.1/a", "https://doi.org/10.1/e"]
    assert {"authorships": {"author": {"id": "A2"}}} in world.filters and all(f["authorships"]["author"]["id"] == "A2" for f in world.filters)
    assert "Authors search failed for 'Thomas Ferron'" in caplog.text
    world.authors["Cheng Wang"] = [{"id": "A9", "display_name": "Cheng Wang"}]
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="harvest_rsoxs"):
        assert hv.search_openalex_authors(["Cheng Wang"], 5, None) == []
    assert "No ALS-affiliated Cheng Wang ID" in caplog.text


def test_search_openalex_authors_tolerates_a_failing_works_query(monkeypatch, caplog):
    world = OpenAlexWorld().install(monkeypatch)
    world.authors["Thomas Ferron"] = [{"id": "A1", "display_name": "Thomas Ferron"}]
    world.search_failures["RSoXS"] = 1
    world.works['"resonant soft x-ray scattering"'] = [oa_work(pdf=None)]
    with caplog.at_level(logging.WARNING, logger="harvest_rsoxs"):
        out = hv.search_openalex_authors(["Thomas Ferron"], 5, None)
    assert len(out) == 1 and "author-works failed for A1 'RSoXS'" in caplog.text


def test_merge_candidates_dedupes_by_doi_and_title_and_ranks(monkeypatch):
    a = {"doi": "https://doi.org/10.1/a", "title": GOOD_TITLE, "abstract": GOOD_ABSTRACT, "publication_date": "2020-01-01"}
    same_title = {"doi": "https://doi.org/10.1/b", "title": GOOD_TITLE.upper(), "abstract": GOOD_ABSTRACT}
    same_doi = {"doi": "https://doi.org/10.1/a", "title": "Different title about RSoXS morphology"}
    other = {"doi": "https://doi.org/10.1/o", "title": "RSoXS morphology of organic solar cells", "abstract": "P3HT films"}
    out = hv.merge_candidates([a, same_title], [same_doi, other])
    assert len(out) == 2 and {w["doi"] for w in out} == {a["doi"], other["doi"]} and all("rsoxs_tier" in w for w in out)

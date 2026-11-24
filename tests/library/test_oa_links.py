"""Free-link resolution for open-access papers (owner decision 2026-11-24). Fakes only: no network."""
from __future__ import annotations

import pytest

from app.modules.library import oa_links as ol
from app.modules.literature.legacy_download import JSONFetchError


def work(*locations, best=None):
    return {"best_oa_location": best, "locations": list(locations)}


def loc(pdf="", landing="", oa=True):
    return {"pdf_url": pdf or None, "landing_page_url": landing or None, "is_oa": oa}


def test_only_open_access_locations_are_ever_returned():
    w = work(loc("https://closed.example/a.pdf", "https://closed.example/a", oa=False),
             loc("https://open.example/b", "https://open.example/b-landing"))
    assert ol.oa_locations(w) == [{"pdf_url": "https://open.example/b", "landing_page_url": "https://open.example/b-landing"}]
    assert ol.oa_locations({"locations": [loc(oa=False)]}) == []


def test_the_best_oa_location_counts_even_without_the_flag_and_duplicates_collapse():
    best = {"pdf_url": "https://b.example/x", "landing_page_url": None}
    w = work(best, loc("https://b.example/x"), best=best)
    assert ol.oa_locations(w) == [{"pdf_url": "https://b.example/x", "landing_page_url": ""}]


def test_any_pdf_url_shape_of_an_open_access_location_is_a_link_with_its_rule():
    locations = [{"pdf_url": "https://journal.example/download?id=7&format=pdf", "landing_page_url": ""}]
    links = list(ol.resolve_links(locations, set()))
    assert links == [ol.ResolvedLink("https://journal.example/download?id=7&format=pdf", "oa_pdf_url")]
    assert list(ol.resolve_links(locations, {"https://journal.example/download?id=7&format=pdf"})) == []


def test_a_zenodo_record_resolves_to_its_own_pdf_file():
    calls = []

    def get_json(url, **kw):
        calls.append(url)
        return {"files": [{"key": "data.csv", "links": {"self": "https://zenodo.org/api/records/99/files/data.csv/content"}},
                          {"key": "Paper.PDF", "links": {"self": "https://zenodo.org/api/records/99/files/Paper.PDF/content"}}]}

    locs = [{"pdf_url": "", "landing_page_url": "https://zenodo.org/records/99"}]
    links = list(ol.resolve_links(locs, set(), get_json=get_json))
    assert links == [ol.ResolvedLink("https://zenodo.org/api/records/99/files/Paper.PDF/content", "zenodo_record")]
    assert calls == ["https://zenodo.org/api/records/99"]


def test_a_zenodo_file_link_on_another_host_is_ignored():
    def get_json(url, **kw):
        return {"files": [{"key": "a.pdf", "links": {"self": "https://evil.example/a.pdf"}}]}

    locs = [{"pdf_url": "", "landing_page_url": "https://zenodo.org/record/5"}]
    assert list(ol.resolve_links(locs, set(), get_json=get_json)) == []


@pytest.mark.parametrize("landing, expected, rule", [
    ("https://www.biodiversitylibrary.org/part/12345", "https://www.biodiversitylibrary.org/partpdf/12345", "bhl_part"),
    ("https://www.biodiversitylibrary.org/item/678", "https://www.biodiversitylibrary.org/itempdf/678", "bhl_item"),
])
def test_biodiversity_heritage_library_parts_and_items_have_a_pdf_endpoint(landing, expected, rule):
    locs = [{"pdf_url": "", "landing_page_url": landing}]
    assert list(ol.resolve_links(locs, set())) == [ol.ResolvedLink(expected, rule)]


def test_a_bhl_page_without_a_pdf_endpoint_falls_through_to_the_landing_meta_rule():
    seen = []

    def get_bytes(url, **kw):
        seen.append(url)
        return b'<meta name="citation_pdf_url" content="https://repo.example/x.pdf">'

    locs = [{"pdf_url": "", "landing_page_url": "https://www.biodiversitylibrary.org/bibliography/4"}]
    links = list(ol.resolve_links(locs, set(), get_bytes=get_bytes))
    assert links == [ol.ResolvedLink("https://repo.example/x.pdf", "landing_citation_pdf_url")]


@pytest.mark.parametrize("page, expected", [
    (b'<html><meta name="citation_pdf_url" content="https://h.example/a.pdf"></html>', "https://h.example/a.pdf"),
    (b"<meta content='https://h.example/b.pdf' name='citation_pdf_url'>", "https://h.example/b.pdf"),
    (b'<meta NAME="Citation_PDF_URL" content="https://h.example/c.pdf?x=1&amp;y=2">', "https://h.example/c.pdf?x=1&y=2"),
    (b'<meta name="citation_pdf_url" content="/relative.pdf">', None),
    (b'<meta name="citation_pdf_url" content="javascript:alert(1)">', None),
    (b"<meta name='citation_title' content='x'>", None),
])
def test_the_citation_pdf_url_meta_tag_is_read_in_either_order_and_only_when_absolute_http(page, expected):
    assert ol.citation_pdf_url(page) == expected


def test_the_landing_rule_is_bounded_per_paper_and_a_failing_lookup_is_skipped():
    fetched, errors = [], []

    def get_bytes(url, **kw):
        fetched.append((url, kw.get("max_bytes")))
        if len(fetched) == 1:
            raise JSONFetchError("HTTP 500", status=500)
        return b'<meta name="citation_pdf_url" content="https://h.example/ok.pdf">'

    locs = [{"pdf_url": "", "landing_page_url": f"https://repo{i}.example/item"} for i in range(5)]
    links = list(ol.resolve_links(locs, set(), get_bytes=get_bytes, on_error=lambda *a: errors.append(a)))
    assert len(fetched) == ol.MAX_LANDING_FETCHES == 2 and all(size == ol.LANDING_MAX_BYTES for _, size in fetched)
    assert links == [ol.ResolvedLink("https://h.example/ok.pdf", "landing_citation_pdf_url")]
    assert errors[0][0] == "landing_citation_pdf_url" and errors[0][1] == "repo0.example"


def test_scihub_and_non_http_links_are_never_yielded():
    locs = [{"pdf_url": "https://sci-hub.se/x.pdf", "landing_page_url": ""},
            {"pdf_url": "ftp://files.example/x.pdf", "landing_page_url": ""}]
    assert list(ol.resolve_links(locs, set())) == []

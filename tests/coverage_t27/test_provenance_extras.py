"""Provenance helpers: metadata merge, publication-year/title/DOI heuristics, code snippet extraction."""

from __future__ import annotations

import datetime

import pytest

from app.modules.term_extractor import provenance as prov


class _Page:
    def __init__(self, text):
        self._text = text

    def get_text(self):
        return self._text


class _Doc:
    def __init__(self, pages, metadata=None):
        self._pages = pages
        self.metadata = metadata or {}

    @property
    def page_count(self):
        return len(self._pages)

    def load_page(self, n):
        return _Page(self._pages[n])


def test_clean_and_merge_never_overwrites_other_sources_or_blanks():
    assert prov.clean_pub_meta({"doi": "", "authors": ["", "A"], "keywords": [""], "journal": None,
                                "other": 1}) == {"authors": ["A"]}
    store = {"p1": {"doi": "10.1/x"}, "p2": {"doi": "other"}}
    assert prov.merge_into_source_metadata(store, "p1", {"journal": "J", "doi": ""}) is True
    assert store["p1"] == {"doi": "10.1/x", "journal": "J"} and store["p2"] == {"doi": "other"}
    assert prov.merge_into_source_metadata(store, "p1", {"journal": "J"}) is False
    assert prov.merge_into_source_metadata(store, "", {"journal": "J"}) is False
    assert prov.merge_into_source_metadata(store, "p3", None) is False


def test_publications_from_source_metadata_sorted_and_filtered():
    assert prov.publications_from_source_metadata("nope") == []
    out = prov.publications_from_source_metadata(
        {"b.pdf": {"doi": "d", "junk": 1}, "a.pdf": {"journal": "J"}, "c.pdf": "bad", " ": {"doi": "x"}}
    )
    assert out == [{"source_paper": "a.pdf", "journal": "J"}, {"source_paper": "b.pdf", "doi": "d"}]


def test_snippet_key_and_json_extraction():
    assert prov.snippet_key({"source_paper": "p", "page": 3, "code_snippet": " x \n"}) == ("p", "3", "x")
    assert prov.snippet_key({}) == ("", "", "")
    text = 'noise {"a": 1} text {"snippets": [{"function_name": "f"}], "x": {"y": {"z": 1}}} {bad json}'
    assert prov.extract_snippets_json_from_text(text)["snippets"][0]["function_name"] == "f"
    assert prov.extract_snippets_json_from_text("nothing") == {"snippets": []}
    assert prov.extract_snippets_json_from_text(None) == {"snippets": []}


class _Schema:
    def __init__(self, ctx="- peak_position: where\n- width: w\n- None", boom=False):
        self.ctx, self.boom = ctx, boom

    def get_code_domain_feature_context(self):
        if self.boom:
            raise RuntimeError("no schema")
        return self.ctx


def test_normalize_domain_features_keeps_only_schema_allowed_nonempty():
    feats = [
        {"feature_name": "peak_position", "feature_value": 1.5, "feature_units": "nm"},
        {"feature_name": "invented", "feature_value": "x"},
        {"feature_name": "width", "feature_value": ""},
        "garbage",
    ]
    assert prov.normalize_domain_features(feats, _Schema()) == [
        {"feature_name": "peak_position", "feature_value": "1.5", "feature_units": "nm",
         "feature_source_text": None}
    ]
    single = {"feature_name": "width", "feature_value": 2}
    assert len(prov.normalize_domain_features(single, _Schema())) == 1
    assert prov.normalize_domain_features([single], _Schema(boom=True)) == []


def test_prompt_truncates_page_text_and_survives_schema_failure():
    prompt = prov._prepare_code_snippet_prompt("a" * 7000 + "TAIL", _Schema(boom=True))
    assert "TAIL" in prompt and prompt.count("a") < 6100 + 200


PAGE = (
    "We analysed the scattering data with the following routine written for this study and "
    "used it on all of the samples in this paper as described in the methods section.\n"
    "import numpy as np\n"
    "def fit_peak(x, y):\n"
    "    return np.polyfit(x, y, 2)\n"
    "def fit_peak(x, y):\n"
    "    return 0\n"
    "function shape(a)\n"
    "  res = a;\n"
    "  end\n"
)


class _Chat:
    def __init__(self, response=None, fail=False):
        self.response, self.fail, self.calls = response, fail, []

    def chat(self, prompt, **kw):
        self.calls.append(kw)
        if self.fail:
            raise TimeoutError("slow")
        return self.response


def test_extract_code_snippets_dedups_functions_and_merges_llm_context():
    llm = ('{"snippets": [{"function_name": "FIT_PEAK", "authors": ["Ada"], '
           '"code_description": "quadratic fit", "domain_features": '
           '[{"feature_name": "width", "feature_value": "3"}]}, "skip-me"]}')
    chat = _Chat(llm)
    out = prov.extract_code_snippets(PAGE, chat, _Schema(), source_paper="p.pdf", page=4)
    assert [s["function_name"] for s in out] == ["fit_peak", "shape"]
    fit, shape = out
    assert fit["code_language"] == "python" and fit["authors"] == ["Ada"]
    assert fit["code_description"] == "quadratic fit"
    assert fit["domain_features"][0]["feature_name"] == "width"
    assert shape["code_language"] == "matlab" and shape["authors"] == []
    assert shape["code_description"] == "shape: extracted from p.pdf p.4"
    assert chat.calls == [{"temperature": 0.0, "timeout": 120}]


def test_extract_code_snippets_falls_back_to_regex_only_when_llm_fails():
    out = prov.extract_code_snippets(PAGE, _Chat(fail=True), _Schema())
    assert len(out) == 2 and all(s["domain_features"] == [] for s in out)


def test_extract_code_snippets_guards():
    assert prov.extract_code_snippets("", _Chat(), _Schema()) == []
    assert prov.extract_code_snippets("too short to bother", _Chat(), _Schema()) == []
    prose = " ".join(["word"] * 40)
    chat = _Chat("{}")
    assert prov.extract_code_snippets(prose, chat, _Schema()) == [] and chat.calls == []


def test_r_function_without_end_is_labelled_r():
    page = " ".join(["filler"] * 25) + "\nfunction(x) foo\nfunction calc(x) {\n  x + 1\n}\n"
    out = prov.extract_code_snippets(page, _Chat('{"snippets": []}'), _Schema())
    assert out and out[0]["code_language"] == "r"


YEAR = datetime.datetime.now(datetime.UTC).year


@pytest.mark.parametrize(
    ("text", "meta", "expected"),
    [
        ("Received: March 3, 2019\nAccepted 2020", {}, 2019),
        ("Accepted 12 June 2021 in final form", {}, 2021),
        ("Published 2018-05-06", {}, 2018),
        ("Revised in 2017 somewhere", {}, 2017),
        ("nothing useful", {"creationDate": "D:20150101000000"}, 2015),
        ("Reprinted May 2012 and Jun 2012 and March 2013", {}, 2012),
        ("cited 2001 and 2001 and 2005", {}, 2001),
        ("Published 1850", {}, None),
        ("", {}, None),
        (f"Published {YEAR + 5}", {"modDate": "x"}, None),
    ],
)
def test_publication_year_cascade(text, meta, expected):
    doc = _Doc([text] if text else [], meta)
    assert prov.extract_pub_metadata(doc, "/x/a.pdf")["publication_year"] == expected


def test_pub_metadata_from_pdf_fields_and_first_page():
    first = (
        "Journal of Applied Things\n"
        "Vol. 12, No. 3 pp. 45 - 67\n"
        "Abstract\n" + "We study things in considerable depth and detail. " * 3 + "\n"
        "Introduction\n"
    )
    doc = _Doc(
        [first, "see doi 10.1234/abc.def)."],
        {"title": " The Title ", "author": "A One; B Two and C Three", "keywords": "x, y; z",
         "subject": "doi:10.5555/zz."},
    )
    meta = prov.extract_pub_metadata(doc, "/p/a.pdf")
    assert meta["paper_title"] == "The Title"
    assert meta["authors"] == ["A One", "B Two", "C Three"]
    assert meta["doi"] == "10.5555/zz"
    assert meta["keywords"] == ["x", "y", "z"]
    assert meta["journal"].startswith("Journal of Applied Things")
    assert (meta["volume"], meta["issue"], meta["pages_range"]) == ("12", "3", "45-67")
    assert meta["abstract_text"].startswith("We study things")
    assert meta["institutions"] == []


def test_pub_metadata_infers_title_and_doi_from_text_when_metadata_empty():
    first = (
        "https://example.org/skip\n"
        "A Study of Thin Film Growth\n"
        "On Silicon Substrates\n"
        "J. Doe, A. Roe\n"
    )
    doc = _Doc([first, "second page doi 10.9999/abc, end"])
    meta = prov.extract_pub_metadata(doc, "a.pdf")
    assert meta["paper_title"] == "A Study of Thin Film Growth On Silicon Substrates"
    assert meta["doi"] == "10.9999/abc"
    assert meta["authors"] == [] and meta["journal"] is None


def test_pub_metadata_empty_document_is_all_blank():
    meta = prov.extract_pub_metadata(_Doc([]), "a.pdf")
    assert meta["paper_title"] is None and meta["doi"] is None and meta["publication_year"] is None

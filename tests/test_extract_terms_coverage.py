"""
Coverage-gap tests for extract_terms.py.

Targets the four large uncovered areas:
  A. OllamaChatClient / CBorgChatClient / make_chat_client  (lines 55–107)
  B. _extract_pub_metadata  — all 4 year-priority branches + title/doi/
     authors/journal/volume/abstract extraction  (lines 1417–1604)
  C. process_pdf  — happy path, fitz-open failure, per-page error,
     metadata backfill  (lines 1612–1703)
  D. process_directory  — empty dir, no-PDF dir, importance scoring,
     final save  (lines 1710–1743)
  E. _extract_and_attach_properties  (lines 1357–1393)
  F. _merge_term / existing-key path in process_page  (lines 1228–1288)
  G. CBorgChatClient.chat happy path  (line ~90)

All heavy I/O (fitz, requests, openai) is mocked; no real PDF or network needed.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from app.modules.extract_terms import (
    CBorgChatClient,
    LLMTermExtractor,
    OllamaChatClient,
    make_chat_client,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

class _DummyFormulaChecker:
    def validate(self, formula):
        return {"status": "ok", "canonical": formula}


class _DummySchema:
    def get_schema_context_for_llm(self):
        return "schema"

    def get_code_domain_feature_context(self):
        return "- q_range: Q range"

    def validate_and_fix_term(self, term):
        return term


class _DummyClient:
    def __init__(self, response="{}"):
        self.response = response
        self.calls = []

    def chat(self, prompt, *, temperature=0.0, timeout=240):
        self.calls.append(prompt)
        return self.response


def _bare_extractor(tmp_path, client=None, response="{}"):
    """LLMTermExtractor wired without real __init__."""
    e = LLMTermExtractor.__new__(LLMTermExtractor)
    e.model_name = "test"
    e.ollama_base_url = "http://localhost:11434"
    e.temperature = 0.0
    e.data_dir = str(tmp_path)
    e.output_file = str(tmp_path / "terms.json")
    e.context_length = 8
    e.max_workers = 1
    e.formula_checker = _DummyFormulaChecker()
    e.schema_helper = _DummySchema()
    e.terms_dict = {}
    e._bk_terms = {}
    e.code_snippets = []
    e._snippet_seen = set()
    e.chat_client = client or _DummyClient(response)
    e.prop_extractor = MagicMock()
    e.prop_extractor.extract.return_value = []
    e.prop_normalizer = MagicMock()
    e.chebi_lookup = None
    e.metadata = {
        "version": "test",
        "processed_files": 0,
        "processed_pages_total": 0,
        "processed_pages_with_terms": 0,
    }
    e._save_lock = threading.Lock()
    return e


def _fake_page(text: str) -> MagicMock:
    p = MagicMock()
    p.get_text.return_value = text
    return p


def _fake_doc(pages: list[str], metadata: dict | None = None) -> MagicMock:
    """Minimal fitz.Document stub."""
    doc = MagicMock()
    doc.page_count = len(pages)
    doc.metadata = metadata or {}
    doc.load_page.side_effect = lambda i: _fake_page(pages[i])
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# A. OllamaChatClient / CBorgChatClient / make_chat_client
# ─────────────────────────────────────────────────────────────────────────────

class TestOllamaChatClient:
    def test_chat_returns_content(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"message": {"content": "hello ollama"}}

        with patch("app.modules.extract_terms.requests.post", return_value=mock_resp) as mock_post:
            client = OllamaChatClient(model="llama3", base_url="http://localhost:11434")
            result = client.chat("test prompt", temperature=0.1, timeout=30)

        assert result == "hello ollama"
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        assert payload["model"] == "llama3"
        assert payload["options"]["temperature"] == 0.1

    def test_chat_raises_on_http_error(self):
        import requests as req
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.HTTPError("500")

        with patch("app.modules.extract_terms.requests.post", return_value=mock_resp):
            client = OllamaChatClient(model="llama3", base_url="http://localhost:11434")
            with pytest.raises(req.HTTPError):
                client.chat("prompt")

    def test_chat_returns_empty_string_on_missing_content(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"message": {}}  # no "content" key

        with patch("app.modules.extract_terms.requests.post", return_value=mock_resp):
            client = OllamaChatClient(model="llama3", base_url="http://localhost:11434")
            result = client.chat("prompt")

        assert result == ""


class TestCBorgChatClient:
    def test_chat_returns_last_choice_content(self):
        fake_choice = MagicMock()
        fake_choice.message.content = "cborg answer"
        fake_resp = MagicMock()
        fake_resp.choices = [fake_choice]

        with patch("app.modules.extract_terms.openai.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.return_value = fake_resp
            client = CBorgChatClient(model="lbl/cborg-chat", api_key="key")
            result = client.chat("hello", temperature=0.0, timeout=60)

        assert result == "cborg answer"

    def test_chat_returns_empty_string_when_content_is_none(self):
        fake_choice = MagicMock()
        fake_choice.message.content = None
        fake_resp = MagicMock()
        fake_resp.choices = [fake_choice]

        with patch("app.modules.extract_terms.openai.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.return_value = fake_resp
            client = CBorgChatClient(model="lbl/cborg-chat", api_key="key")
            result = client.chat("hello")

        assert result == ""


class TestMakeChatClient:
    def test_cborg_backend_returns_cborg_client(self):
        with patch("app.modules.extract_terms.openai.OpenAI"):
            client = make_chat_client("cborg", "lbl/cborg-chat", cborg_api_key="key")
        assert isinstance(client, CBorgChatClient)

    def test_cborg_openai_alias_also_returns_cborg_client(self):
        with patch("app.modules.extract_terms.openai.OpenAI"):
            client = make_chat_client("cborg-openai", "lbl/model", cborg_api_key="k")
        assert isinstance(client, CBorgChatClient)

    def test_unknown_backend_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown LLM backend"):
            make_chat_client("grpc", "model")


# ─────────────────────────────────────────────────────────────────────────────
# B. _extract_pub_metadata  — year priority branches + field extraction
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractPubMetadata:

    def _call(self, tmp_path, pages, pdf_meta=None):
        e = _bare_extractor(tmp_path)
        doc = _fake_doc(pages, metadata=pdf_meta or {})
        return e._extract_pub_metadata(doc, "/path/to/paper.pdf")

    # --- Year priority 1: explicit keyword date on first page ---
    def test_year_from_explicit_received_date(self, tmp_path):
        text = "Received 14 March 2021\nSome abstract text follows."
        meta = self._call(tmp_path, [text])
        assert meta["publication_year"] == 2021

    def test_year_from_explicit_published_year(self, tmp_path):
        text = "Published online 2019\nContent here."
        meta = self._call(tmp_path, [text])
        assert meta["publication_year"] == 2019

    def test_year_from_accepted_iso_date(self, tmp_path):
        text = "Accepted: 2022-07-15\nThis paper describes..."
        meta = self._call(tmp_path, [text])
        assert meta["publication_year"] == 2022

    # --- Year priority 2: PDF metadata creationDate ---
    def test_year_from_pdf_creation_date_metadata(self, tmp_path):
        # no keyword on first page, but PDF metadata has creationDate
        meta = self._call(
            tmp_path,
            ["No date keywords here."],
            pdf_meta={"creationDate": "D:20180601120000"},
        )
        assert meta["publication_year"] == 2018

    def test_year_ignores_future_pdf_metadata_date(self, tmp_path):
        # A creation date in the future (e.g. 2099) should be rejected
        meta = self._call(
            tmp_path,
            ["No date keywords here."],
            pdf_meta={"creationDate": "D:20990101"},
        )
        assert meta["publication_year"] != 2099

    # --- Year priority 3: month-adjacent year ---
    def test_year_from_month_adjacent_pattern(self, tmp_path):
        text = "This article was published in January 2017 in the journal."
        meta = self._call(tmp_path, [text])
        assert meta["publication_year"] == 2017

    # --- Year priority 4: most-common year fallback ---
    def test_year_from_most_common_fallback(self, tmp_path):
        text = "Copyright 2015. The work was done in 2015. Reference [1] from 2015. Also 2010."
        meta = self._call(tmp_path, [text])
        assert meta["publication_year"] == 2015

    # --- No year found ---
    def test_year_none_when_no_year_present(self, tmp_path):
        meta = self._call(tmp_path, ["No dates at all in this text."])
        assert meta["publication_year"] is None

    # --- Empty document (zero pages) ---
    def test_empty_document_returns_none_year(self, tmp_path):
        e = _bare_extractor(tmp_path)
        doc = MagicMock()
        doc.page_count = 0
        doc.metadata = {}
        result = e._extract_pub_metadata(doc, "empty.pdf")
        assert result["publication_year"] is None

    # --- Title extraction ---
    def test_title_from_pdf_metadata(self, tmp_path):
        meta = self._call(
            tmp_path,
            ["Some first-page text."],
            pdf_meta={"title": "Advances in Organic Photovoltaics"},
        )
        assert meta["paper_title"] == "Advances in Organic Photovoltaics"

    def test_title_extracted_from_first_substantive_line(self, tmp_path):
        text = "Received 2020\nAdvances in Organic Photovoltaics\nAuthor Name\nAbstract"
        meta = self._call(tmp_path, [text])
        assert meta["paper_title"] is not None
        assert "Organic Photovoltaics" in meta["paper_title"]

    # --- DOI extraction ---
    def test_doi_extracted_from_first_page_text(self, tmp_path):
        text = "Received 2020\nhttps://doi.org/10.1002/adma.202012345\nAbstract follows."
        meta = self._call(tmp_path, [text])
        assert meta["doi"] is not None
        assert "10.1002" in meta["doi"]

    def test_doi_extracted_from_pdf_metadata_subject(self, tmp_path):
        meta = self._call(
            tmp_path,
            ["plain text no doi"],
            pdf_meta={"subject": "10.1039/C9EE01249D"},
        )
        assert meta["doi"] == "10.1039/C9EE01249D"

    # --- Authors from PDF metadata ---
    def test_authors_from_pdf_metadata(self, tmp_path):
        meta = self._call(
            tmp_path,
            ["Some text."],
            pdf_meta={"author": "Smith J; Lee K; Wang L"},
        )
        assert meta["authors"] == ["Smith J", "Lee K", "Wang L"]

    # --- Journal / volume / issue / pages_range ---
    def test_journal_extracted_from_first_page(self, tmp_path):
        text = "Received 2021\nAdvanced Materials, Vol. 33, No. 12, pp. 2100456"
        meta = self._call(tmp_path, [text])
        assert meta["journal"] is not None
        assert "Advanced" in meta["journal"]

    def test_volume_issue_extracted(self, tmp_path):
        text = "Received 2021\nVolume 33, Issue 5, pp. 100-110"
        meta = self._call(tmp_path, [text])
        assert meta["volume"] == "33"
        assert meta["issue"] == "5"

    def test_pages_range_extracted(self, tmp_path):
        text = "Received 2021\npp. 100-200"
        meta = self._call(tmp_path, [text])
        assert meta["pages_range"] == "100-200"

    # --- Abstract extraction ---
    def test_abstract_extracted_from_first_page(self, tmp_path):
        text = (
            "Received 2022\n"
            "Abstract\n"
            "This study investigates the role of P3HT in organic photovoltaic devices "
            "and demonstrates improved power conversion efficiency through morphology control.\n"
            "Introduction\n"
        )
        meta = self._call(tmp_path, [text])
        assert meta["abstract_text"] is not None
        assert "P3HT" in meta["abstract_text"]

    # --- Keywords from PDF metadata ---
    def test_keywords_from_pdf_metadata(self, tmp_path):
        meta = self._call(
            tmp_path,
            ["Some text."],
            pdf_meta={"keywords": "P3HT, OPV, PCE"},
        )
        assert "P3HT" in meta["keywords"]
        assert "OPV" in meta["keywords"]


# ─────────────────────────────────────────────────────────────────────────────
# C. process_pdf
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessPdf:

    def test_happy_path_processes_pages_and_updates_metadata(self, tmp_path):
        e = _bare_extractor(tmp_path)

        # One page with enough words to pass the short-page guard
        page_text = " ".join(["word"] * 30)
        doc = _fake_doc([page_text])

        llm_resp = json.dumps({
            "terms": [{
                "term": "P3HT",
                "definition": "conjugated polymer",
                "category": "ConjugatedPolymer",
                "formula": None,
                "relations": [],
            }]
        })
        e.chat_client = _DummyClient(response=llm_resp)
        e._extract_and_attach_properties = MagicMock(return_value=False)
        e._collect_code_snippets = MagicMock(return_value=False)
        e.fuzzy_merge = MagicMock(return_value=None)
        e._save_terms_threadsafe = MagicMock()

        with patch("app.modules.extract_terms.fitz.open", return_value=doc):
            count = e.process_pdf("/fake/paper.pdf")

        assert count == 1
        assert e.metadata["processed_files"] == 1
        assert e.metadata["processed_pages_total"] == 1
        assert "p3ht" in e.terms_dict

    def test_fitz_open_failure_returns_zero(self, tmp_path):
        e = _bare_extractor(tmp_path)

        with patch("app.modules.extract_terms.fitz.open", side_effect=RuntimeError("corrupt pdf")):
            count = e.process_pdf("/fake/bad.pdf")

        assert count == 0
        assert e.metadata["processed_files"] == 0

    def test_per_page_exception_is_caught_and_logged(self, tmp_path, caplog):
        e = _bare_extractor(tmp_path)
        doc = _fake_doc([" ".join(["word"] * 30)])

        # Make process_page raise for this page
        e.process_page = MagicMock(side_effect=RuntimeError("page exploded"))
        e._extract_pub_metadata = MagicMock(return_value={
            "publication_year": None, "paper_title": None, "authors": [],
            "institutions": [], "doi": None, "journal": None, "volume": None,
            "issue": None, "pages_range": None, "abstract_text": None, "keywords": [],
        })

        with patch("app.modules.extract_terms.fitz.open", return_value=doc):
            count = e.process_pdf("/fake/paper.pdf")

        assert count == 0
        assert "Error processing page" in caplog.text

    def test_metadata_backfill_propagates_across_terms(self, tmp_path):
        """After process_pdf, all terms from that PDF share the best metadata."""
        e = _bare_extractor(tmp_path)

        # Pre-populate two terms from the same PDF
        e.terms_dict = {
            "p3ht": {
                "term": "P3HT", "source_papers": ["paper.pdf"],
                "paper_title": "OPV Study", "doi": None, "authors": [],
            },
            "pcbm": {
                "term": "PCBM", "source_papers": ["paper.pdf"],
                "paper_title": None, "doi": "10.1002/x", "authors": [],
            },
        }

        doc = _fake_doc(["short"])  # only one page, short → process_page will skip
        e._extract_pub_metadata = MagicMock(return_value={
            "publication_year": 2020, "paper_title": "OPV Study",
            "authors": ["Author A"], "institutions": [], "doi": "10.1002/x",
            "journal": None, "volume": None, "issue": None,
            "pages_range": None, "abstract_text": None, "keywords": [],
        })
        e._save_terms_threadsafe = MagicMock()

        with patch("app.modules.extract_terms.fitz.open", return_value=doc):
            e.process_pdf("/fake/paper.pdf")

        # Both terms should now have the doi and paper_title backfilled
        assert e.terms_dict["p3ht"]["doi"] == "10.1002/x"
        assert e.terms_dict["pcbm"]["paper_title"] == "OPV Study"
        assert e.terms_dict["pcbm"]["authors"] == ["Author A"]


# ─────────────────────────────────────────────────────────────────────────────
# D. process_directory
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessDirectory:

    def test_returns_error_for_missing_directory(self, tmp_path):
        e = _bare_extractor(tmp_path)
        e.data_dir = str(tmp_path / "nonexistent")
        result = e.process_directory()
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_returns_success_with_zero_files_for_empty_dir(self, tmp_path):
        e = _bare_extractor(tmp_path)
        e._save_terms_threadsafe = MagicMock()
        result = e.process_directory()
        assert result["status"] == "success"
        assert result["processed_files"] == 0

    def test_importance_scoring_high_medium_low(self, tmp_path):
        e = _bare_extractor(tmp_path)
        e._save_terms_threadsafe = MagicMock()

        # high: >1 paper
        e.terms_dict["p3ht"] = {
            "term": "P3HT", "pages": [1, 2, 3],
            "source_papers": ["a.pdf", "b.pdf"],
        }
        # medium: 3–5 occurrences, single paper
        e.terms_dict["pcbm"] = {
            "term": "PCBM", "pages": [1, 2, 3],
            "source_papers": ["a.pdf", "a.pdf", "a.pdf"],
        }
        # low: ≤2 occurrences
        e.terms_dict["ito"] = {
            "term": "ITO", "pages": [1],
            "source_papers": ["a.pdf"],
        }

        e.process_directory()  # no PDFs → no process_pdf calls, but importance runs

        assert e.terms_dict["p3ht"]["importance"] == "high"
        assert e.terms_dict["pcbm"]["importance"] == "medium"
        assert e.terms_dict["ito"]["importance"] == "low"

    def test_processes_all_pdf_files_in_directory(self, tmp_path):
        # Create dummy .pdf files (content doesn't matter — process_pdf is mocked)
        for name in ["a.pdf", "b.pdf", "notes.txt"]:
            (tmp_path / name).write_bytes(b"%PDF")

        e = _bare_extractor(tmp_path)
        e._save_terms_threadsafe = MagicMock()
        e.process_pdf = MagicMock(return_value=0)

        result = e.process_directory()

        assert result["processed_files"] == 2   # only .pdf files
        assert e.process_pdf.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# E. _extract_and_attach_properties
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractAndAttachProperties:

    def test_returns_false_when_terms_dict_empty(self, tmp_path):
        e = _bare_extractor(tmp_path)
        assert e._extract_and_attach_properties("some text") is False

    def test_returns_false_when_no_raw_props_found(self, tmp_path):
        e = _bare_extractor(tmp_path)
        e.terms_dict = {"p3ht": {"term": "P3HT"}}
        e.prop_extractor.extract.return_value = []
        assert e._extract_and_attach_properties("text") is False

    def test_attaches_new_property_to_matching_term(self, tmp_path):
        e = _bare_extractor(tmp_path)
        e.terms_dict = {"p3ht": {"term": "P3HT", "properties": []}}
        e.prop_extractor.extract.return_value = [{"material": "P3HT", "property": "bandgap"}]
        e.prop_normalizer.normalize.return_value = [{
            "material": "P3HT",
            "property": "bandgap",
            "normalized_value": "1.9",
            "normalized_unit": "eV",
            "context": "P3HT has a bandgap of 1.9 eV",
            "uncertainty_value": None,
            "unit_conversion_failed": False,
        }]

        updated = e._extract_and_attach_properties("P3HT has a bandgap of 1.9 eV")

        assert updated is True
        props = e.terms_dict["p3ht"]["properties"]
        assert len(props) == 1
        assert props[0]["property"] == "bandgap"
        assert props[0]["value"] == "1.9"
        assert props[0]["unit"] == "eV"

    def test_deduplicates_properties(self, tmp_path):
        e = _bare_extractor(tmp_path)
        existing_prop = {
            "property": "bandgap", "value": "1.9",
            "unit": "eV", "context": "P3HT has a bandgap of 1.9 eV",
        }
        e.terms_dict = {"p3ht": {"term": "P3HT", "properties": [existing_prop]}}
        e.prop_extractor.extract.return_value = [{"material": "P3HT", "property": "bandgap"}]
        e.prop_normalizer.normalize.return_value = [{
            "material": "P3HT",
            "property": "bandgap",
            "normalized_value": "1.9",
            "normalized_unit": "eV",
            "context": "P3HT has a bandgap of 1.9 eV",
            "uncertainty_value": None,
            "unit_conversion_failed": False,
        }]

        updated = e._extract_and_attach_properties("text")

        # duplicate — should NOT be added again
        assert updated is False
        assert len(e.terms_dict["p3ht"]["properties"]) == 1

    def test_skips_property_for_unknown_material(self, tmp_path):
        e = _bare_extractor(tmp_path)
        e.terms_dict = {"p3ht": {"term": "P3HT", "properties": []}}
        e.prop_extractor.extract.return_value = [{"material": "Unknown"}]
        e.prop_normalizer.normalize.return_value = [{
            "material": "Unknown",
            "property": "bandgap",
            "normalized_value": "2.0",
            "normalized_unit": "eV",
            "context": "ctx",
            "uncertainty_value": None,
            "unit_conversion_failed": False,
        }]

        updated = e._extract_and_attach_properties("text")

        assert updated is False
        assert e.terms_dict["p3ht"]["properties"] == []


# ─────────────────────────────────────────────────────────────────────────────
# F. process_page — existing-key (merge) path
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessPageMergePath:

    def _make_llm_response(self, term_name="P3HT", pub_year=2023):
        return json.dumps({
            "terms": [{
                "term": term_name,
                "definition": "conjugated polymer updated definition",
                "category": "ConjugatedPolymer",
                "formula": None,
                "publication_year": pub_year,
                "paper_title": "New OPV Paper",
                "authors": ["Jones R"],
                "doi": "10.1002/new",
                "journal": "Nature Energy",
                "relations": [
                    {"relation": "has_application", "related_term": "OPV",
                     "verified": True, "evidence": ["p1"]}
                ],
            }]
        })

    def test_merge_updates_pages_and_source_papers(self, tmp_path):
        e = _bare_extractor(tmp_path)
        # Pre-register P3HT on page 1
        e._bk_terms["P3HT"] = "p3ht"
        e.terms_dict["p3ht"] = {
            "term": "P3HT", "definition": "short def",
            "category": "ConjugatedPolymer", "pages": [1],
            "source_papers": ["paper.pdf"], "context_snippets": [],
            "relations": [], "publication_year": None,
        }

        e.chat_client = _DummyClient(response=self._make_llm_response())
        e.fuzzy_merge = MagicMock(return_value="p3ht")  # force existing-key path
        e._extract_and_attach_properties = MagicMock(return_value=False)
        e._collect_code_snippets = MagicMock(return_value=False)
        e._save_terms_threadsafe = MagicMock()

        fake_doc = MagicMock()
        fake_page = MagicMock()
        fake_page.get_text.return_value = " ".join(["word"] * 30)
        fake_doc.load_page.return_value = fake_page

        result = e.process_page(fake_doc, "paper.pdf", page_num=1)

        assert result is True
        entry = e.terms_dict["p3ht"]
        assert 2 in entry["pages"]          # page_num+1 = 2 added
        assert "paper.pdf" in entry["source_papers"]

    def test_merge_backfills_missing_pub_meta_fields(self, tmp_path):
        e = _bare_extractor(tmp_path)
        e._bk_terms["P3HT"] = "p3ht"
        e.terms_dict["p3ht"] = {
            "term": "P3HT", "definition": "def",
            "category": "ConjugatedPolymer", "pages": [1],
            "source_papers": ["paper.pdf"], "context_snippets": [],
            "relations": [], "publication_year": None,
            "paper_title": None, "doi": None, "authors": [],
        }

        e.chat_client = _DummyClient(response=self._make_llm_response())
        e.fuzzy_merge = MagicMock(return_value="p3ht")
        e._extract_and_attach_properties = MagicMock(return_value=False)
        e._collect_code_snippets = MagicMock(return_value=False)
        e._save_terms_threadsafe = MagicMock()

        fake_doc = MagicMock()
        fake_page = MagicMock()
        fake_page.get_text.return_value = " ".join(["word"] * 30)
        fake_doc.load_page.return_value = fake_page

        e.process_page(
            fake_doc, "paper.pdf", page_num=1,
            pub_meta={"paper_title": "Existing Title", "doi": None}
        )

        entry = e.terms_dict["p3ht"]
        # doi was None → should be backfilled from pub_meta LLM enrichment
        assert entry["doi"] == "10.1002/new"

    def test_merge_longer_definition_overwrites_shorter(self, tmp_path):
        e = _bare_extractor(tmp_path)
        e._bk_terms["P3HT"] = "p3ht"
        e.terms_dict["p3ht"] = {
            "term": "P3HT", "definition": "short",
            "category": "ConjugatedPolymer", "pages": [1],
            "source_papers": ["paper.pdf"], "context_snippets": [],
            "relations": [], "publication_year": None,
        }

        e.chat_client = _DummyClient(response=self._make_llm_response())
        e.fuzzy_merge = MagicMock(return_value="p3ht")
        e._extract_and_attach_properties = MagicMock(return_value=False)
        e._collect_code_snippets = MagicMock(return_value=False)
        e._save_terms_threadsafe = MagicMock()

        fake_doc = MagicMock()
        fake_page = MagicMock()
        fake_page.get_text.return_value = " ".join(["word"] * 30)
        fake_doc.load_page.return_value = fake_page

        e.process_page(fake_doc, "paper.pdf", page_num=1)

        # "conjugated polymer updated definition" > "short"
        assert e.terms_dict["p3ht"]["definition"] == "conjugated polymer updated definition"

    def test_merge_deduplicates_relations(self, tmp_path):
        e = _bare_extractor(tmp_path)
        e._bk_terms["P3HT"] = "p3ht"
        existing_rel = {"relation": "has_application", "related_term": "OPV", "verified": True}
        e.terms_dict["p3ht"] = {
            "term": "P3HT", "definition": "def",
            "category": "ConjugatedPolymer", "pages": [1],
            "source_papers": ["paper.pdf"], "context_snippets": [],
            "relations": [existing_rel], "publication_year": None,
        }

        e.chat_client = _DummyClient(response=self._make_llm_response())
        e.fuzzy_merge = MagicMock(return_value="p3ht")
        e._extract_and_attach_properties = MagicMock(return_value=False)
        e._collect_code_snippets = MagicMock(return_value=False)
        e._save_terms_threadsafe = MagicMock()

        fake_doc = MagicMock()
        fake_page = MagicMock()
        fake_page.get_text.return_value = " ".join(["word"] * 30)
        fake_doc.load_page.return_value = fake_page

        e.process_page(fake_doc, "paper.pdf", page_num=1)

        rels = e.terms_dict["p3ht"]["relations"]
        # Still exactly one has_application → OPV (no duplicate)
        matching = [r for r in rels if r["relation"] == "has_application" and r["related_term"] == "OPV"]
        assert len(matching) == 1

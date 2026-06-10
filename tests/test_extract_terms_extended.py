"""
Extended unit tests for extract_terms.py — covers gaps identified by coverage analysis.

Tests 24–32 from the coverage gap list:
  24. LLMTermExtractor.__init__ resumes from existing output file
  25. call_llm retry fires on exception, second attempt succeeds
  26. fuzzy_merge returns None when _bk_terms is empty
  27. fuzzy_merge returns existing key when LLM matches a registered term
  28. fuzzy_merge returns None when LLM response is not in _bk_terms
  29. process_page skips pages with fewer than 20 words
  30. process_page handles LLM failure gracefully — returns False
  31. process_page handles JSON parse failure — returns False
  32. process_page enriches pub_meta from first term's metadata fields
"""
from __future__ import annotations

import json
import threading
import types
from unittest.mock import MagicMock, patch

import pytest

from app.modules.extract_terms import LLMTermExtractor, retry_on_exception


# ---------------------------------------------------------------------------
# Minimal helpers — build a bare extractor without touching the filesystem
# or real LLM/schema/ChEBI dependencies.
# ---------------------------------------------------------------------------

class _DummyClient:
    """Synchronous chat client that returns a preset response."""

    def __init__(self, response: str = "{}"):
        self.response = response
        self.calls: list[str] = []

    def chat(self, prompt: str, *, temperature: float = 0.0, timeout: int = 240) -> str:
        self.calls.append(prompt)
        return self.response


class _DummySchema:
    """Minimal SchemaHelper stub."""

    def get_schema_context_for_llm(self) -> str:
        return "schema context"

    def get_code_domain_feature_context(self) -> str:
        return "- q_range: Q range"

    def validate_and_fix_term(self, term):
        return term


class _DummyFormulaChecker:
    def validate(self, formula: str) -> dict:
        return {"status": "ok", "canonical": formula}


def _make_extractor(tmp_path, client=None, response="{}"):
    """Return a fully wired LLMTermExtractor with real __init__ skipped."""
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
    e.metadata = {"version": "test"}
    e._save_lock = threading.Lock()
    import os
    os.makedirs(str(tmp_path), exist_ok=True)
    return e


def _make_fake_doc(text: str):
    """Return a fitz.Document-like object whose page yields `text`."""
    fake_page = MagicMock()
    fake_page.get_text.return_value = text
    fake_doc = MagicMock()
    fake_doc.load_page.return_value = fake_page
    return fake_doc


# ---------------------------------------------------------------------------
# 24. __init__ resumes from existing output file —
#     pre-populates terms_dict, _bk_terms, code_snippets, metadata
# ---------------------------------------------------------------------------

def test_init_resumes_from_existing_output_file(tmp_path, monkeypatch):
    # Stub out heavy dependencies that fire in real __init__
    monkeypatch.setattr(
        "app.modules.extract_terms.ChemicalFormulaValidator",
        lambda **kw: _DummyFormulaChecker(),
    )
    monkeypatch.setattr(
        "app.modules.extract_terms.SchemaHelper",
        lambda **kw: _DummySchema(),
    )
    monkeypatch.setattr(
        "app.modules.extract_terms.ChebiOboLookup",
        MagicMock(side_effect=FileNotFoundError("no chebi")),
    )
    monkeypatch.setattr(
        "app.modules.extract_terms.PhysicalPropertyExtractor",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "app.modules.extract_terms.PropertyNormalizer",
        MagicMock(return_value=MagicMock()),
    )

    output_file = tmp_path / "storage" / "terms.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    existing = {
        "metadata": {"version": "2.0", "processed_files": 5},
        "terms": [
            {"term": "P3HT", "definition": "polymer", "properties": []},
            {"term": "PCBM", "definition": "acceptor", "properties": []},
        ],
        "code_snippets": [
            {
                "function_name": "analyze",
                "code_snippet": "def analyze(x): return x",
                "source_paper": "paper.pdf",
                "page": 1,
            }
        ],
    }
    output_file.write_text(json.dumps(existing), encoding="utf-8")

    extractor = LLMTermExtractor(
        output_file=str(output_file),
        schema_path="matkg_schema.yaml",
        chat_client=_DummyClient(),
    )

    assert "p3ht" in extractor.terms_dict
    assert "pcbm" in extractor.terms_dict
    assert "P3HT" in extractor._bk_terms
    assert len(extractor.code_snippets) == 1
    assert extractor.metadata["processed_files"] == 5
    assert extractor.metadata["version"] == "2.0"


# ---------------------------------------------------------------------------
# 25. call_llm retry fires on first exception, succeeds on second attempt
# ---------------------------------------------------------------------------

def test_call_llm_retries_then_succeeds(tmp_path, monkeypatch):
    # Speed up delay
    monkeypatch.setattr(threading.Event, "wait", lambda self, t: None)

    attempts = {"n": 0}

    class FlakyClient:
        def chat(self, prompt, *, temperature=0.0, timeout=240):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient error")
            return '{"terms": []}'

    e = _make_extractor(tmp_path, client=FlakyClient())
    result = e.call_llm("some prompt")

    assert result == '{"terms": []}'
    assert attempts["n"] == 2


# ---------------------------------------------------------------------------
# 26. fuzzy_merge returns None immediately when _bk_terms is empty
# ---------------------------------------------------------------------------

def test_fuzzy_merge_returns_none_when_no_registered_terms(tmp_path):
    e = _make_extractor(tmp_path)
    assert e._bk_terms == {}
    assert e.fuzzy_merge("P3HT") is None


# ---------------------------------------------------------------------------
# 27. fuzzy_merge returns existing key when LLM response matches a registered term
# ---------------------------------------------------------------------------

def test_fuzzy_merge_returns_key_on_llm_match(tmp_path):
    # Register "GI-WAXS" → key "gi-waxs"
    e = _make_extractor(tmp_path, client=_DummyClient(response="GI-WAXS"))
    e._bk_terms["GI-WAXS"] = "gi-waxs"

    result = e.fuzzy_merge("GIWAXS")

    assert result == "gi-waxs"


# ---------------------------------------------------------------------------
# 28. fuzzy_merge returns None when LLM response is not in _bk_terms
# ---------------------------------------------------------------------------

def test_fuzzy_merge_returns_none_when_llm_response_not_registered(tmp_path):
    e = _make_extractor(tmp_path, client=_DummyClient(response="SEM"))
    e._bk_terms["TEM"] = "tem"   # LLM says "SEM" but only "TEM" is registered

    result = e.fuzzy_merge("GIWAXS")

    assert result is None


# ---------------------------------------------------------------------------
# 29. process_page skips pages with fewer than 20 words — returns False
# ---------------------------------------------------------------------------

def test_process_page_skips_short_pages(tmp_path):
    e = _make_extractor(tmp_path)
    short_text = "Only ten words total here on this page."  # < 20 words
    fake_doc = _make_fake_doc(short_text)

    result = e.process_page(fake_doc, "paper.pdf", page_num=0)

    assert result is False


# ---------------------------------------------------------------------------
# 30. process_page returns False when LLM raises an exception
# ---------------------------------------------------------------------------

def test_process_page_handles_llm_failure(tmp_path):
    class AlwaysFailsClient:
        def chat(self, prompt, *, temperature=0.0, timeout=240):
            raise RuntimeError("LLM offline")

    e = _make_extractor(tmp_path, client=AlwaysFailsClient())
    # patch retry so it doesn't re-try and swallow the error
    import app.modules.extract_terms as et
    original = et.retry_on_exception

    def no_retry(exc_types, retries=1, delay_seconds=1.0):
        def decorator(fn):
            return fn
        return decorator

    # Replace call_llm to raise directly (bypass retry decorator on the method)
    e.call_llm = MagicMock(side_effect=RuntimeError("LLM offline"))
    e._extract_and_attach_properties = MagicMock(return_value=False)
    e._collect_code_snippets = MagicMock(return_value=False)

    long_text = " ".join(["word"] * 30)
    fake_doc = _make_fake_doc(long_text)

    result = e.process_page(fake_doc, "paper.pdf", page_num=0)

    assert result is False


# ---------------------------------------------------------------------------
# 31. process_page returns False when JSON parsing fails permanently
# ---------------------------------------------------------------------------

def test_process_page_handles_json_parse_failure(tmp_path, monkeypatch):
    # LLM returns unparseable garbage
    e = _make_extractor(tmp_path, client=_DummyClient("not json at all !!!"))
    e._extract_and_attach_properties = MagicMock(return_value=False)
    e._collect_code_snippets = MagicMock(return_value=False)

    # Make extract_json_from_text always raise to simulate parse failure
    e.extract_json_from_text = MagicMock(side_effect=ValueError("bad json"))

    long_text = " ".join(["word"] * 30)
    fake_doc = _make_fake_doc(long_text)

    result = e.process_page(fake_doc, "paper.pdf", page_num=0)

    assert result is False


# ---------------------------------------------------------------------------
# 32. process_page enriches pub_meta from the first term's metadata fields
# ---------------------------------------------------------------------------

def test_process_page_enriches_pub_meta_from_term_fields(tmp_path):
    llm_response = json.dumps({
        "terms": [
            {
                "term": "P3HT",
                "definition": "conjugated polymer",
                "category": "ConjugatedPolymer",
                "formula": None,
                "publication_year": 2022,
                "paper_title": "Advances in OPV",
                "authors": ["Smith J", "Lee K"],
                "doi": "10.1002/adma.fake",
                "journal": "Advanced Materials",
                "relations": [],
            }
        ]
    })

    e = _make_extractor(tmp_path, client=_DummyClient(response=llm_response))
    # Stub downstream calls that aren't under test here
    e._extract_and_attach_properties = MagicMock(return_value=False)
    e._collect_code_snippets = MagicMock(return_value=False)
    e.fuzzy_merge = MagicMock(return_value=None)  # always new term
    e._save_terms_threadsafe = MagicMock()

    long_text = " ".join(["word"] * 30)
    fake_doc = _make_fake_doc(long_text)

    # Pass pub_meta with only publication_year missing
    result = e.process_page(
        fake_doc,
        "paper.pdf",
        page_num=0,
        pub_year=None,
        pub_meta={"journal": ""},
    )

    assert result is True
    term_entry = e.terms_dict.get("p3ht")
    assert term_entry is not None, "P3HT not registered"
    assert term_entry["paper_title"] == "Advances in OPV"
    assert term_entry["doi"] == "10.1002/adma.fake"
    assert term_entry["authors"] == ["Smith J", "Lee K"]
    assert term_entry["publication_year"] == 2022

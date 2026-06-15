"""
Unit tests for small utility functions:
  - _strip_ansi
  - _tokenize
  - _noun_phrases
  - extract_query_entities
  - snippet_text
  - format_domain_features
  - decompose
  - auto_device
  - cuda_warmup
  - load_pdf_text
  - make_chat_client
  - MissingNodeTracker
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.modules import kg_rag_api


# ─────────────────────────────────────────────────────────────────────────────
# _strip_ansi
# ─────────────────────────────────────────────────────────────────────────────


class TestStripAnsi:
    def test_removes_color_codes(self):
        assert kg_rag_api._strip_ansi("\x1b[31mRed\x1b[0m") == "Red"

    def test_removes_multiple_codes(self):
        text = "\x1b[1m\x1b[33mBold Yellow\x1b[0m normal"
        assert kg_rag_api._strip_ansi(text) == "Bold Yellow normal"

    def test_passthrough_clean_text(self):
        assert kg_rag_api._strip_ansi("no codes here") == "no codes here"

    def test_empty_string(self):
        assert kg_rag_api._strip_ansi("") == ""

    def test_removes_erase_codes(self):
        assert kg_rag_api._strip_ansi("text\x1b[2Kmore") == "textmore"


# ─────────────────────────────────────────────────────────────────────────────
# _tokenize
# ─────────────────────────────────────────────────────────────────────────────


class TestTokenize:
    def test_basic(self):
        assert kg_rag_api._tokenize("Hello World!") == ["hello", "world"]

    def test_preserves_alphanumeric(self):
        assert kg_rag_api._tokenize("P3HT-based OPV") == ["p3ht", "based", "opv"]

    def test_empty_string(self):
        assert kg_rag_api._tokenize("") == []

    def test_special_chars_only(self):
        assert kg_rag_api._tokenize("!@#$%") == []


# ─────────────────────────────────────────────────────────────────────────────
# _noun_phrases
# ─────────────────────────────────────────────────────────────────────────────


class TestNounPhrases:
    def test_empty_string(self):
        assert kg_rag_api._noun_phrases("") == []

    def test_whitespace_only(self):
        assert kg_rag_api._noun_phrases("   ") == []

    def test_fallback_when_nltk_unavailable(self, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "_NLTK_OK", False)
        result = kg_rag_api._noun_phrases("organic solar cell")
        assert result == ["organic solar cell"]


# ─────────────────────────────────────────────────────────────────────────────
# extract_query_entities
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractQueryEntities:
    def test_extracts_meaningful_tokens(self):
        result = kg_rag_api.extract_query_entities("What is P3HT?")
        assert any("p3ht" in e.lower() or "P3HT" in e for e in result)

    def test_empty_string(self):
        assert kg_rag_api.extract_query_entities("") == []

    def test_deduplicates(self):
        result = kg_rag_api.extract_query_entities("P3HT P3HT")
        lower = [e.lower() for e in result]
        assert lower.count("p3ht") == 1

    def test_filters_short_tokens(self):
        result = kg_rag_api.extract_query_entities("a is ok")
        assert all(len(e) >= 3 for e in result)

    def test_punctuation_only_returns_raw_fallback(self):
        # When NLTK is unavailable, the raw query is returned as a noun-phrase fallback
        result = kg_rag_api.extract_query_entities("??!!")
        assert result == ["??!!"]


# ─────────────────────────────────────────────────────────────────────────────
# snippet_text
# ─────────────────────────────────────────────────────────────────────────────


class TestSnippetText:
    def test_centers_on_hint(self):
        text = "aaa " * 50 + "TARGET " + "bbb " * 50
        result = kg_rag_api.snippet_text(text, 20, ["target"])
        assert "TARGET" in result

    def test_no_hints_returns_start(self):
        result = kg_rag_api.snippet_text("alpha beta gamma", 10, [])
        assert result == "alpha beta gamma"[:10]

    def test_hint_not_found_returns_start(self):
        result = kg_rag_api.snippet_text("alpha beta", 5, ["zzz"])
        assert result == "alpha"

    def test_empty_text(self):
        assert kg_rag_api.snippet_text("", 10, ["x"]) == ""

    def test_zero_length(self):
        assert kg_rag_api.snippet_text("some text", 0, ["some"]) == ""

    def test_short_text_returned_fully(self):
        assert kg_rag_api.snippet_text("short", 100, ["short"]) == "short"


# ─────────────────────────────────────────────────────────────────────────────
# format_domain_features
# ─────────────────────────────────────────────────────────────────────────────


class TestFormatDomainFeatures:
    def test_single_line(self):
        features = [{"feature_name": "q_range", "feature_value": "0.1", "feature_units": "nm"}]
        result = kg_rag_api.format_domain_features(features, multiline=False)
        assert "q_range: 0.1 nm" in result
        assert "\n" not in result

    def test_multiline(self):
        features = [
            {"feature_name": "a", "feature_value": "1"},
            {"feature_name": "b", "feature_value": "2"},
        ]
        result = kg_rag_api.format_domain_features(features, multiline=True)
        assert "- a: 1" in result
        assert "- b: 2" in result

    def test_skips_empty_name(self):
        features = [{"feature_name": "", "feature_value": "val"}]
        assert kg_rag_api.format_domain_features(features) == ""

    def test_skips_empty_value(self):
        features = [{"feature_name": "name", "feature_value": ""}]
        assert kg_rag_api.format_domain_features(features) == ""

    def test_non_dict_skipped(self):
        assert kg_rag_api.format_domain_features(["string", 42]) == ""

    def test_non_list_input(self):
        assert kg_rag_api.format_domain_features("bad") == ""

    def test_none_input(self):
        assert kg_rag_api.format_domain_features(None) == ""

    def test_source_text_in_multiline(self):
        features = [{"feature_name": "q", "feature_value": "1", "feature_source_text": "from fig"}]
        result = kg_rag_api.format_domain_features(features, multiline=True)
        assert "from fig" in result

    def test_no_units(self):
        features = [{"feature_name": "q", "feature_value": "1"}]
        result = kg_rag_api.format_domain_features(features)
        assert "q: 1" in result


# ─────────────────────────────────────────────────────────────────────────────
# decompose
# ─────────────────────────────────────────────────────────────────────────────


class TestDecompose:
    def test_splits_on_semicolons_and_question_marks(self):
        result = kg_rag_api.decompose("What is P3HT? compare OPV; list papers")
        assert len(result) == 3

    def test_single_question(self):
        assert kg_rag_api.decompose("What is P3HT") == ["What is P3HT"]

    def test_empty_string(self):
        result = kg_rag_api.decompose("")
        assert result == [""]


# ─────────────────────────────────────────────────────────────────────────────
# auto_device
# ─────────────────────────────────────────────────────────────────────────────


class TestAutoDevice:
    def test_force_cpu(self, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "FORCE_CPU", True)
        assert kg_rag_api.auto_device() == "cpu"

    def test_no_cuda(self, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "FORCE_CPU", False)
        with patch("app.modules.kg_rag_api.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = False
            assert kg_rag_api.auto_device() == "cpu"

    def test_cuda_available(self, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "FORCE_CPU", False)
        with patch("app.modules.kg_rag_api.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = True
            assert kg_rag_api.auto_device() == "cuda"


# ─────────────────────────────────────────────────────────────────────────────
# cuda_warmup
# ─────────────────────────────────────────────────────────────────────────────


class TestCudaWarmup:
    def test_noop_on_cpu(self):
        # Should not raise
        kg_rag_api.cuda_warmup("cpu")

    def test_noop_when_cuda_unavailable(self):
        with patch("app.modules.kg_rag_api.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = False
            kg_rag_api.cuda_warmup("cuda")


# ─────────────────────────────────────────────────────────────────────────────
# load_pdf_text
# ─────────────────────────────────────────────────────────────────────────────


class TestLoadPdfText:
    def test_missing_file_returns_empty(self, tmp_path):
        kg_rag_api.load_pdf_text.cache_clear()
        assert kg_rag_api.load_pdf_text(str(tmp_path / "missing.pdf")) == ""
        kg_rag_api.load_pdf_text.cache_clear()

    def test_reads_from_fitz(self, tmp_path):
        kg_rag_api.load_pdf_text.cache_clear()
        fake_page = MagicMock()
        fake_page.get_text.return_value = "page content"
        fake_doc = MagicMock()
        fake_doc.__iter__ = MagicMock(return_value=iter([fake_page]))

        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF")

        with patch("app.modules.kg_rag_api.fitz.open", return_value=fake_doc):
            result = kg_rag_api.load_pdf_text(str(pdf))
        assert "page content" in result
        kg_rag_api.load_pdf_text.cache_clear()


# ─────────────────────────────────────────────────────────────────────────────
# make_chat_client
# ─────────────────────────────────────────────────────────────────────────────


class TestMakeChatClient:
    def test_ollama_backend(self):
        client = kg_rag_api.make_chat_client(backend="ollama")
        assert isinstance(client, kg_rag_api.OllamaClient)

    def test_cborg_backend(self):
        client = kg_rag_api.make_chat_client(backend="cborg")
        assert isinstance(client, kg_rag_api.CBorgClient)

    def test_cborg_openai_alias(self):
        client = kg_rag_api.make_chat_client(backend="cborg-openai")
        assert isinstance(client, kg_rag_api.CBorgClient)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown KG-RAG LLM backend"):
            kg_rag_api.make_chat_client(backend="unknown")


# ─────────────────────────────────────────────────────────────────────────────
# MissingNodeTracker
# ─────────────────────────────────────────────────────────────────────────────


class TestMissingNodeTracker:
    def test_writes_jsonl(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tracker = kg_rag_api.MissingNodeTracker("graphs/test.json")
        node = kg_rag_api.MissingNode("q", "ent", "reason", time.time())
        tracker.log(node)

        lines = tracker.path.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["query"] == "q"
        assert record["entity"] == "ent"
        assert record["reason"] == "reason"

    def test_appends_multiple_entries(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tracker = kg_rag_api.MissingNodeTracker("graphs/test.json")
        for i in range(3):
            tracker.log(kg_rag_api.MissingNode(f"q{i}", f"e{i}", "r", time.time()))

        lines = tracker.path.read_text().splitlines()
        assert len(lines) == 3

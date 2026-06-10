import json
import threading

import pytest

from app.modules.extract_terms import (
    LLMTermExtractor,
    OllamaChatClient,
    SchemaHelper,
    make_chat_client,
    retry_on_exception,
)


class DummyClient:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def chat(self, prompt, *, temperature=0.0, timeout=240):
        self.prompts.append((prompt, temperature, timeout))
        return self.response


class DummySchema:
    def get_code_domain_feature_context(self):
        return (
            "- scattering_technique: Scattering technique (single value)\n"
            "- q_range: Q range (single value)"
        )


def make_extractor():
    extractor = LLMTermExtractor.__new__(LLMTermExtractor)
    extractor.context_length = 8
    extractor.temperature = 0.2
    return extractor


def test_make_chat_client_selects_ollama_and_rejects_unknown_backend():
    client = make_chat_client("ollama", "model-name", ollama_url="http://localhost:9999")

    assert isinstance(client, OllamaChatClient)
    assert client.model == "model-name"
    assert client.base == "http://localhost:9999"
    with pytest.raises(ValueError, match="Unknown LLM backend"):
        make_chat_client("unknown", "model-name")


def test_retry_on_exception_retries_then_succeeds(monkeypatch):
    calls = {"count": 0}
    monkeypatch.setattr(threading.Event, "wait", lambda self, seconds: None)

    @retry_on_exception((ValueError,), retries=2, delay_seconds=0.01)
    def flaky():
        calls["count"] += 1
        if calls["count"] < 2:
            raise ValueError("temporary")
        return "ok"

    assert flaky() == "ok"
    assert calls["count"] == 2


def test_retry_on_exception_raises_last_error_after_exhaustion(monkeypatch, caplog):
    monkeypatch.setattr(threading.Event, "wait", lambda self, seconds: None)

    @retry_on_exception((ValueError,), retries=1, delay_seconds=0.01)
    def always_fails():
        raise ValueError("permanent failure")

    with pytest.raises(ValueError, match="permanent failure"):
        always_fails()

    assert "Retryable error in always_fails" in caplog.text


def test_extract_json_from_text_returns_largest_terms_object():
    extractor = make_extractor()
    text = 'noise {"terms": []} more {"terms": [{"term": "P3HT"}], "extra": {"x": 1}}'

    result = extractor.extract_json_from_text(text)

    assert result == {"terms": [{"term": "P3HT"}], "extra": {"x": 1}}


def test_extract_json_from_text_returns_empty_terms_for_malformed_text():
    extractor = make_extractor()

    assert extractor.extract_json_from_text("no json {bad") == {"terms": []}
    assert extractor._extract_snippets_json_from_text('{"not_snippets": []}') == {"snippets": []}


def test_get_context_snippet_finds_sentence_and_stamps_source():
    extractor = make_extractor()
    full_text = "Intro sentence. P3HT absorbs visible light in organic solar cells. Closing."

    snippet = extractor.get_context_snippet(full_text, "P3HT", "paper.pdf", 0)

    assert snippet == {
        "text": "P3HT absorbs visible light in organic solar cells.",
        "source_paper": "paper.pdf",
        "page": 1,
    }


def test_get_context_snippet_falls_back_to_start_when_term_absent():
    extractor = make_extractor()
    full_text = "One two three four five six seven eight nine ten."

    snippet = extractor.get_context_snippet(full_text, "missing", "paper.pdf", 4)

    assert snippet == {
        "text": "One two three four five six seven eight",
        "source_paper": "paper.pdf",
        "page": 5,
    }


def test_postprocess_term_marks_missing_formula_without_validation_call():
    extractor = make_extractor()
    term = {"term": "polymer", "category": "ChemicalEntity", "formula": "not a formula"}

    result = extractor._postprocess_term(term, "context")

    assert result["formula"] is None
    assert result["formula_validation"] == {"status": "missing"}


def test_postprocess_term_records_validation_error():
    class FailingChecker:
        def validate(self, formula):
            raise RuntimeError("validator unavailable")

    extractor = make_extractor()
    extractor.formula_checker = FailingChecker()
    term = {"term": "water", "category": "ChemicalEntity", "formula": "H2O"}

    result = extractor._postprocess_term(term, "context")

    assert result["formula"] == "H2O"
    assert result["formula_validation"]["status"] == "error"
    assert "validator unavailable" in result["formula_validation"]["details"]["error"]


def test_postprocess_term_corrects_invalid_formula_with_llm_candidate():
    class Checker:
        def validate(self, formula):
            if formula == "Xx2":
                return {"status": "invalid"}
            return {"status": "valid", "formula": formula}

    extractor = make_extractor()
    extractor.formula_checker = Checker()
    extractor.call_llm = lambda prompt, timeout=240: "H2O"
    term = {"term": "water", "category": "ChemicalEntity", "formula": "Xx2"}

    result = extractor._postprocess_term(term, "water context")

    assert result["formula"] == "H2O"
    assert result["formula_validation"]["status"] == "corrected"


def test_extract_code_snippets_returns_empty_for_short_pages():
    extractor = make_extractor()

    assert extractor.extract_code_snippets("def tiny():\n    return 1\n", DummyClient("{}"), DummySchema()) == []


def test_extract_code_snippets_merges_regex_body_with_llm_domain_context():
    extractor = make_extractor()
    client = DummyClient(
        """
        {"snippets": [{
          "function_name": "analyze_scattering",
          "authors": ["Doe J"],
          "code_description": "Analyze SAXS intensity peaks.",
          "domain_features": [
            {"feature_name": "scattering_technique", "feature_value": "SAXS"},
            {"feature_name": "not_allowed", "feature_value": "drop me"}
          ]
        }]}
        """
    )
    page_text = (
        "This scientific page describes scattering analysis with enough surrounding words "
        "to pass the short-text guard before the code block appears in extracted PDF text.\n"
        "import numpy as np\n"
        "def analyze_scattering(q, intensity):\n"
        "    arr = np.asarray(intensity)\n"
        "    peaks = arr > arr.mean()\n"
        "    return peaks\n"
    )

    snippets = extractor.extract_code_snippets(
        page_text,
        client,
        DummySchema(),
        source_paper="paper.pdf",
        page=3,
    )

    assert len(snippets) == 1
    assert snippets[0]["function_name"] == "analyze_scattering"
    assert snippets[0]["code_language"] == "python"
    assert snippets[0]["authors"] == ["Doe J"]
    assert snippets[0]["code_description"] == "Analyze SAXS intensity peaks."
    assert snippets[0]["domain_features"] == [
        {
            "feature_name": "scattering_technique",
            "feature_value": "SAXS",
            "feature_units": None,
            "feature_source_text": None,
        }
    ]


def test_extract_code_snippets_uses_regex_only_when_llm_fails():
    class FailingClient:
        def chat(self, prompt, *, temperature=0.0, timeout=240):
            raise RuntimeError("offline")

    extractor = make_extractor()
    page_text = (
        "This page has enough explanatory scientific words before code appears in text output.\n"
        "def normalize_curve(values):\n"
        "    shifted = [value - min(values) for value in values]\n"
        "    scale = max(shifted) or 1\n"
        "    return [value / scale for value in shifted]\n"
    )

    snippets = extractor.extract_code_snippets(page_text, FailingClient(), DummySchema())

    assert len(snippets) == 1
    assert snippets[0]["function_name"] == "normalize_curve"
    assert snippets[0]["domain_features"] == []


def test_extract_code_snippets_applies_anonymous_llm_context():
    extractor = make_extractor()
    client = DummyClient(
        """
        {"snippets": [{
          "function_name": null,
          "authors": ["Anon"],
          "code_description": "Anonymous context applies to regex snippet.",
          "domain_features": [{"feature_name": "q_range", "feature_value": "0.02-0.5"}]
        }]}
        """
    )
    page_text = (
        "This page has enough surrounding scientific words for code extraction to run.\n"
        "def fit_curve(x, y):\n"
        "    coeff = x[0] if x else 0\n"
        "    return coeff, y\n"
    )

    snippets = extractor.extract_code_snippets(page_text, client, DummySchema())

    assert snippets[0]["authors"] == ["Anon"]
    assert snippets[0]["code_description"] == "Anonymous context applies to regex snippet."
    assert snippets[0]["domain_features"][0]["feature_name"] == "q_range"


def test_collect_code_snippets_deduplicates_and_saves_once(tmp_path):
    extractor = make_extractor()
    extractor.chat_client = DummyClient('{"snippets": []}')
    extractor.schema_helper = DummySchema()
    extractor.code_snippets = []
    extractor._snippet_seen = set()
    extractor._save_lock = threading.Lock()
    extractor.output_file = str(tmp_path / "terms.json")
    extractor.metadata = {}
    extractor.terms_dict = {}
    calls = {"saves": 0}

    snippet = {
        "function_name": "analyze",
        "code_snippet": "def analyze(x):\n    return x\n",
        "page": 1,
        "source_paper": "paper.pdf",
    }
    extractor.extract_code_snippets = lambda *args, **kwargs: [dict(snippet)]
    extractor._save_snippets_threadsafe = lambda: calls.__setitem__("saves", calls["saves"] + 1)

    assert extractor._collect_code_snippets("page text", "paper.pdf", 0, {"publication_year": 2024}) is True
    assert extractor._collect_code_snippets("page text", "paper.pdf", 0, {"publication_year": 2024}) is False
    assert calls["saves"] == 1
    assert extractor.code_snippets[0]["publication_year"] == 2024


def test_save_terms_threadsafe_writes_properties_key(tmp_path):
    extractor = make_extractor()
    extractor._save_lock = threading.Lock()
    extractor.output_file = str(tmp_path / "terms.json")
    extractor.metadata = {"version": "test"}
    extractor.terms_dict = {"p3ht": {"term": "P3HT"}}
    extractor.code_snippets = []

    extractor._save_terms_threadsafe()

    saved = json.loads((tmp_path / "terms.json").read_text(encoding="utf-8"))
    assert saved["terms"] == [{"term": "P3HT", "properties": []}]
    assert saved["metadata"] == {"version": "test"}


def test_schema_helper_validate_and_fix_term_filters_bad_relations():
    helper = SchemaHelper.__new__(SchemaHelper)
    helper.classes = {"Material": {"description": "Material", "slots": []}}
    helper.class_parents = {"Material": None}
    helper.slots = {"has_application": {"domain": None, "range": None, "multivalued": False}}
    helper._class_names_lower = ["material"]
    helper._class_map_lower = {"material": "Material"}
    helper._slot_names_lower = ["has_application"]
    helper._slot_map_lower = {"has_application": "has_application"}
    helper.fuzzy_cutoff = 70

    fixed = helper.validate_and_fix_term(
        {
            "term": "P3HT",
            "category": "material",
            "relations": [
                {"relation": "category", "related_term": "drop"},
                {"relation": "has applicaton", "related_term": "OPV"},
                {"relation": "unknown_relation", "related_term": "X"},
            ],
        }
    )

    assert fixed["category"] == "Material"
    assert fixed["relations"] == [
        {"relation": "has_application", "related_term": "OPV", "verified": True},
        {"relation": "unknown_relation", "related_term": "X", "verified": False},
    ]


def test_schema_helper_relation_validity_respects_subclasses():
    helper = SchemaHelper.__new__(SchemaHelper)
    helper.classes = {"Material": {}, "Polymer": {}, "Device": {}}
    helper.class_parents = {"Material": None, "Polymer": "Material", "Device": None}
    helper.slots = {
        "used_in": {"domain": "Material", "range": "Device", "multivalued": False}
    }

    assert helper.check_relation_validity("Polymer", "used_in", "Device") is True
    assert helper.check_relation_validity("Device", "used_in", "Polymer") is False
    assert helper.check_relation_validity("Polymer", "missing", "Device") is False

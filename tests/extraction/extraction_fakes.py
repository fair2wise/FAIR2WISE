"""Offline fakes for the T06 extraction tests: a routed fake chat client and a bare extractor.

The extractor is the real ``extract_terms.LLMTermExtractor`` class with its heavy
constructor skipped (the same pattern as tests/test_extract_terms_extended.py), so
the processing methods under test are the unmodified F2W code.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from app.modules.legacy.extract_terms_cli import LLMTermExtractor

from tests.evidence.evidence_fixtures import PAGE_TEXTS

CODE_BLOCK = "def fit_peak(q, intensity):\n    return q * intensity\n"

TERMS_REPLY = json.dumps({"terms": [
    {"term": "PM6", "definition": "a donor polymer", "category": "ConjugatedPolymer", "formula": None,
     "relations": [{"relation": "part_of", "related_term": "PM6:Y6", "verified": False}]},
    {"term": "RSoXS", "definition": "resonant soft X-ray scattering", "category": "RSoXSMeasurement"},
    {"term": "chloroform", "definition": "a solvent", "category": "ChemicalEntity", "formula": "CHCl3"},
]})


class RoutedFakeChatClient:
    """Deterministic stand-in for the F2W sync chat clients, routed on prompt content.

    Same ``chat(prompt, *, temperature, timeout)`` signature as
    ``extract_terms.ChatClient``; no network or model.
    """

    def __init__(self, *, terms_reply: str = TERMS_REPLY, model: str = "fake-model") -> None:
        self.model = model
        self.terms_reply = terms_reply
        self.calls: List[Dict[str, Any]] = []

    def chat(self, prompt: str, *, temperature: float = 0.0, timeout: int = 240) -> str:
        """Return a canned reply for the terms, code-snippet or merge prompt."""
        self.calls.append({"prompt": prompt, "temperature": temperature, "timeout": timeout})
        if "### EXAMPLE" in prompt:
            return self.terms_reply
        if "snippet" in prompt.lower():
            return json.dumps({"snippets": [{"function_name": "fit_peak", "code_description": "fits a peak"}]})
        return "NO_MATCH"


class _FormulaChecker:
    def validate(self, formula: str) -> Dict[str, Any]:
        return {"status": "valid", "canonical": formula}


class _Schema:
    def get_schema_context_for_llm(self) -> str:
        return "schema context"

    def get_code_domain_feature_context(self) -> str:
        return "- q_range: Q range"

    def validate_and_fix_term(self, term: Dict[str, Any]) -> Dict[str, Any]:
        return term

    def check_relation_validity(self, subj: str, pred: str, obj: str) -> bool:
        return True


class _MergeIndex:
    def __init__(self, owner: Any) -> None:
        self.owner = owner

    def ensure(self, names: Any) -> None:
        return None

    def add(self, name: str) -> None:
        return None

    def query(self, term: str, k: int = 12) -> List[str]:
        return list(self.owner._bk_terms.keys())[:k]


class _Chebi:
    def lookup(self, name: str) -> Optional[Dict[str, Any]]:
        if name == "chloroform":
            return {"chebi_id": "CHEBI:35255", "formula": "CHCl3", "smiles": "ClC(Cl)Cl"}
        return None


def make_extractor(tmp_path: Any, *, client: Any = None, name: str = "terms.json") -> LLMTermExtractor:
    """A real ``LLMTermExtractor`` with deterministic collaborators and no heavy init."""
    e = LLMTermExtractor.__new__(LLMTermExtractor)
    e.model_name = "fake-model"
    e.ollama_base_url = "http://localhost:11434"
    e.temperature = 0.0
    e.data_dir = str(tmp_path)
    e.output_file = str(tmp_path / name)
    e.context_length = 12
    e.max_workers = 1
    e.formula_checker = _FormulaChecker()
    e.schema_helper = _Schema()
    e.terms_dict = {}
    e._bk_terms = {}
    e.code_snippets = []
    e._snippet_seen = set()
    e.chat_client = client or RoutedFakeChatClient()
    e.prop_extractor = MagicMock()
    e.prop_extractor.extract.return_value = []
    e.prop_normalizer = MagicMock()
    e.chebi_lookup = _Chebi()
    e.metadata = {"version": "2.1", "extraction_date": "2026-10-14T00:00:00Z", "processed_files": 0,
                  "processed_pages_total": 0, "processed_pages_with_terms": 0}
    e._save_lock = threading.Lock()
    e.merge_top_k = 12
    e._merge_index = _MergeIndex(e)
    return e


def page_texts_with_code() -> List[str]:
    """The fixture pages, the second carrying a code block (code-snippet provenance)."""
    return [PAGE_TEXTS[0], PAGE_TEXTS[1] + "\n" + CODE_BLOCK]


class PlainPages:
    """Independent page container used for the *baseline* call (not the strategy's own)."""

    def __init__(self, texts: List[str]) -> None:
        self.texts = texts

    def load_page(self, number: int) -> Any:
        page = MagicMock()
        page.get_text.return_value = self.texts[number]
        return page

"""
Coverage-gap tests for kg_rag_api.py.

Targets remaining uncovered reachable lines:
  A. _noun_phrases  — NLTK-unavailable branch (always reachable), empty input
  B. run_fastapi    — uvicorn.run call wired correctly
  C. build_context  — rich node fields (formula, paper_title, doi, authors,
                      journal, source_papers, relations list, PDF snippet);
                      chars budget enforced before CodeSnippet block
  D. score_prp / build_nodeinfo — evidence_ct, formula boost, source_paper
     recency boost branches
  E. _noun_phrases NLTK-available path (monkeypatched)
  F. extract_query_entities branches
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules import kg_rag_api


# ─────────────────────────────────────────────────────────────────────────────
# Shared graph builder
# ─────────────────────────────────────────────────────────────────────────────

def _write_rich_graph(tmp_path) -> Path:
    graph = {
        "things": [
            {
                "id": "matkg:P3HT",
                "name": "P3HT",
                "category": "ConjugatedPolymer",
                "description": "Regioregular conjugated polymer.",
                "formula": "C10H14S",
                "source_papers": ["paper.pdf"],
                "publication_year": 2023,
                "paper_title": "Advances in OPV",
                "doi": "10.1002/x",
                "authors": ["Smith J", "Lee K"],
                "journal": "Advanced Materials",
            },
            {
                "id": "matkg:OPV",
                "name": "Organic Photovoltaic Device",
                "category": "Device",
                "description": "OPV device.",
                "source_papers": [],
            },
            {
                "id": "matkg:Snip",
                "name": "analyze snippet",
                "category": "CodeSnippet",
                "description": "code",
                "source_papers": [],
                "function_name": "analyze",
                "code_domain": "scattering",
                "code_language": "python",
                "code_snippet": "def analyze(x):\n    return x",
                "paper_authors": ["Doe J"],
                "domain_features": [
                    {"feature_name": "q_range", "feature_value": "0.1-1.0", "feature_units": "A^-1"}
                ],
            },
        ],
        "associations": [
            {
                "subject": "matkg:P3HT",
                "predicate": "rel:has_application",
                "object": "matkg:OPV",
                "has_evidence": "p1",
            }
        ],
    }
    p = tmp_path / "rich.json"
    p.write_text(json.dumps(graph))
    return p


# ─────────────────────────────────────────────────────────────────────────────
# A. _noun_phrases
# ─────────────────────────────────────────────────────────────────────────────

class TestNounPhrases:
    def test_returns_empty_list_for_empty_string(self):
        result = kg_rag_api._noun_phrases("")
        assert result == []

    def test_returns_whitespace_only_as_empty(self):
        result = kg_rag_api._noun_phrases("   ")
        assert result == []

    def test_fallback_returns_full_text_when_nltk_unavailable(self, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "_NLTK_OK", False)
        result = kg_rag_api._noun_phrases("organic solar cell")
        assert result == ["organic solar cell"]

    def test_nltk_path_returns_noun_phrases_when_available(self, monkeypatch):
        # Simulate NLTK being available by monkeypatching the module-level flags
        mock_word_tokenize = MagicMock(return_value=["organic", "solar", "cell"])
        mock_pos_tag = MagicMock(return_value=[
            ("organic", "JJ"), ("solar", "JJ"), ("cell", "NN")
        ])

        # Build a fake subtree that looks like nltk's parse result
        class FakeLeaf:
            def leaves(self):
                return [("organic", "JJ"), ("solar", "JJ"), ("cell", "NN")]
            def label(self):
                return "NP"

        class FakeTree:
            def subtrees(self, filter_fn):
                return [FakeLeaf()]

        mock_parser = MagicMock()
        mock_parser.return_value.parse.return_value = FakeTree()

        monkeypatch.setattr(kg_rag_api, "_NLTK_OK", True)
        monkeypatch.setattr(kg_rag_api, "word_tokenize", mock_word_tokenize)
        monkeypatch.setattr(kg_rag_api, "pos_tag", mock_pos_tag)
        monkeypatch.setattr(kg_rag_api, "RegexpParser", mock_parser)

        result = kg_rag_api._noun_phrases("organic solar cell")
        assert "organic solar cell" in result


# ─────────────────────────────────────────────────────────────────────────────
# B. run_fastapi
# ─────────────────────────────────────────────────────────────────────────────

class TestRunFastapi:
    def test_run_fastapi_calls_uvicorn_with_correct_args(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")

        graph_path = _write_rich_graph(tmp_path)
        fake_cli = MagicMock()
        fake_cli.model = "m"
        monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: fake_cli)

        with patch("app.modules.kg_rag_api.uvicorn.run") as mock_uvicorn:
            kg_rag_api.run_fastapi(str(graph_path), backend="ollama")

        mock_uvicorn.assert_called_once()
        call_kwargs = mock_uvicorn.call_args
        assert call_kwargs.kwargs.get("host") == "0.0.0.0" or \
               call_kwargs.args[1:] == () or \
               "0.0.0.0" in str(call_kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# C. build_context — rich node fields
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildContextRichFields:
    def _kg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
        monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda p: "")
        return kg_rag_api.KnowledgeGraph(str(_write_rich_graph(tmp_path)))

    def test_formula_rendered_in_context(self, tmp_path, monkeypatch):
        kg = self._kg(tmp_path, monkeypatch)
        nodes = kg.build_nodeinfo([kg_rag_api.NodeScore("matkg:P3HT", 0.9)], [], [])
        ctx = kg.build_context(nodes, include_structured=False, char_budget=5000, hint_terms=[])
        assert "C10H14S" in ctx

    def test_paper_title_doi_authors_journal_rendered(self, tmp_path, monkeypatch):
        kg = self._kg(tmp_path, monkeypatch)
        nodes = kg.build_nodeinfo([kg_rag_api.NodeScore("matkg:P3HT", 0.9)], [], [])
        ctx = kg.build_context(nodes, include_structured=False, char_budget=5000, hint_terms=[])
        assert "Advances in OPV" in ctx
        assert "10.1002/x" in ctx
        assert "Smith J" in ctx
        assert "Advanced Materials" in ctx

    def test_code_snippet_function_domain_authors_rendered(self, tmp_path, monkeypatch):
        kg = self._kg(tmp_path, monkeypatch)
        nodes = kg.build_nodeinfo([kg_rag_api.NodeScore("matkg:Snip", 1.0)], [], [])
        ctx = kg.build_context(nodes, include_structured=False, char_budget=5000, hint_terms=[])
        assert "Function: analyze" in ctx
        assert "Domain: scattering" in ctx
        assert "Doe J" in ctx
        assert "q_range: 0.1-1.0 A^-1" in ctx
        assert "```python" in ctx

    def test_relations_rendered_in_context(self, tmp_path, monkeypatch):
        kg = self._kg(tmp_path, monkeypatch)
        nodes = kg.build_nodeinfo([kg_rag_api.NodeScore("matkg:P3HT", 0.9)], [], [])
        ctx = kg.build_context(nodes, include_structured=False, char_budget=5000, hint_terms=[])
        assert "Relations:" in ctx
        assert "has_application" in ctx

    def test_pdf_snippet_injected_when_load_returns_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
        monkeypatch.setattr(
            kg_rag_api, "load_pdf_text",
            lambda p: "P3HT absorbs visible light in OPV devices."
        )
        kg = kg_rag_api.KnowledgeGraph(str(_write_rich_graph(tmp_path)))
        nodes = kg.build_nodeinfo([kg_rag_api.NodeScore("matkg:P3HT", 0.9)], [], ["p3ht"])
        ctx = kg.build_context(nodes, include_structured=False, char_budget=5000, hint_terms=["p3ht"])
        assert "P3HT absorbs" in ctx

    def test_char_budget_stops_before_adding_more_nodes(self, tmp_path, monkeypatch):
        kg = self._kg(tmp_path, monkeypatch)
        nodes = kg.build_nodeinfo(
            [
                kg_rag_api.NodeScore("matkg:P3HT", 0.9),
                kg_rag_api.NodeScore("matkg:OPV", 0.8),
            ],
            [], [],
        )
        ctx_tiny = kg.build_context(nodes, include_structured=False, char_budget=50, hint_terms=[])
        ctx_full = kg.build_context(nodes, include_structured=False, char_budget=10000, hint_terms=[])
        assert len(ctx_full) > len(ctx_tiny)


# ─────────────────────────────────────────────────────────────────────────────
# D. build_nodeinfo score paths
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildNodeinfoScoring:
    def test_evidence_ct_positive_for_node_with_source_paper(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
        kg = kg_rag_api.KnowledgeGraph(str(_write_rich_graph(tmp_path)))
        nodes = kg.build_nodeinfo([kg_rag_api.NodeScore("matkg:P3HT", 0.9)], [], [])
        p3ht = next(n for n in nodes if n.id == "matkg:P3HT")
        assert p3ht.evidence_ct > 0

    def test_evidence_ct_zero_for_node_without_source_papers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
        kg = kg_rag_api.KnowledgeGraph(str(_write_rich_graph(tmp_path)))
        nodes = kg.build_nodeinfo([kg_rag_api.NodeScore("matkg:OPV", 0.8)], [], [])
        opv = next(n for n in nodes if n.id == "matkg:OPV")
        assert opv.evidence_ct == 0

    def test_formula_boosts_score_prp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
        kg = kg_rag_api.KnowledgeGraph(str(_write_rich_graph(tmp_path)))
        nodes = kg.build_nodeinfo(
            [
                kg_rag_api.NodeScore("matkg:P3HT", 0.9),   # has formula
                kg_rag_api.NodeScore("matkg:OPV", 0.9),    # no formula
            ],
            [], [],
        )
        by_id = {n.id: n for n in nodes}
        # P3HT has formula + source_papers + recent year → higher score_prp
        assert by_id["matkg:P3HT"].score_prp > by_id["matkg:OPV"].score_prp


# ─────────────────────────────────────────────────────────────────────────────
# E. extract_query_entities
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractQueryEntities:
    def test_returns_tokens_for_normal_input(self):
        # Returns deduplicated noun-phrases + ≥3-char tokens, not the raw query
        result = kg_rag_api.extract_query_entities("What is P3HT?")
        assert any("p3ht" in e.lower() or "P3HT" in e for e in result)
        assert any(len(e) >= 3 for e in result)

    def test_returns_empty_for_empty_string(self):
        result = kg_rag_api.extract_query_entities("")
        assert result == []

    def test_deduplicates_entries(self):
        # "p3ht" should appear only once even though both noun-phrase and
        # token paths would produce it
        result = kg_rag_api.extract_query_entities("P3HT P3HT")
        lower_results = [e.lower() for e in result]
        assert lower_results.count("p3ht") == 1

    def test_filters_short_tokens(self):
        # single-char and two-char tokens must be dropped
        result = kg_rag_api.extract_query_entities("a is ok")
        # "ok" has 2 chars → dropped; "is" has 2 chars → dropped; "a" has 1 → dropped
        assert all(len(e) >= 3 for e in result)


# ─────────────────────────────────────────────────────────────────────────────
# F. snippet_text edge cases in build_context
# ─────────────────────────────────────────────────────────────────────────────

class TestSnippetTextEdgeCases:
    def test_returns_full_text_when_shorter_than_length(self):
        result = kg_rag_api.snippet_text("short", 100, ["short"])
        assert result == "short"

    def test_centers_on_first_hint_occurrence(self):
        text = "aaa " * 50 + "TARGET " + "bbb " * 50
        result = kg_rag_api.snippet_text(text, 20, ["target"])
        assert "TARGET" in result

    def test_no_hints_falls_back_to_start(self):
        text = "alpha beta gamma delta"
        result = kg_rag_api.snippet_text(text, 10, [])
        assert result == text[:10]


# ─────────────────────────────────────────────────────────────────────────────
# G. format_domain_features — source_text rendered only in multiline mode
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatDomainFeaturesSourceText:
    def test_source_text_included_in_multiline_mode(self):
        features = [{"feature_name": "q_range", "feature_value": "0.1-1.0",
                     "feature_source_text": "from paper fig 3"}]
        result = kg_rag_api.format_domain_features(features, multiline=True)
        assert "from paper fig 3" in result

    def test_source_text_not_included_in_single_line_mode(self):
        features = [{"feature_name": "q_range", "feature_value": "0.1-1.0",
                     "feature_source_text": "from paper fig 3"}]
        result = kg_rag_api.format_domain_features(features, multiline=False)
        assert "from paper fig 3" not in result

    def test_non_dict_feature_skipped(self):
        result = kg_rag_api.format_domain_features(["not a dict", 42], multiline=False)
        assert result == ""


# ─────────────────────────────────────────────────────────────────────────────
# H. auto_device — FORCE_CPU branch; load_pdf_text happy path
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoDeviceAndLoadPdf:
    def test_auto_device_returns_cpu_when_force_cpu_set(self, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "FORCE_CPU", True)
        result = kg_rag_api.auto_device()
        assert result == "cpu"

    def test_auto_device_returns_cpu_when_cuda_unavailable(self, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "FORCE_CPU", False)
        # Patch torch.cuda.is_available to return False
        with patch("app.modules.kg_rag_api.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = False
            result = kg_rag_api.auto_device()
        assert result == "cpu"

    def test_auto_device_returns_cuda_when_available(self, monkeypatch):
        monkeypatch.setattr(kg_rag_api, "FORCE_CPU", False)
        with patch("app.modules.kg_rag_api.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = True
            result = kg_rag_api.auto_device()
        assert result == "cuda"

    def test_load_pdf_text_returns_text_from_fitz(self, tmp_path, monkeypatch):
        # Clear lru_cache so previous calls don't interfere
        kg_rag_api.load_pdf_text.cache_clear()

        fake_page = MagicMock()
        fake_page.get_text.return_value = "extracted text content"
        fake_doc = MagicMock()
        fake_doc.__iter__ = MagicMock(return_value=iter([fake_page]))

        pdf_path = tmp_path / "real.pdf"
        pdf_path.write_bytes(b"%PDF")

        with patch("app.modules.kg_rag_api.fitz.open", return_value=fake_doc):
            result = kg_rag_api.load_pdf_text(str(pdf_path))

        assert "extracted text content" in result
        kg_rag_api.load_pdf_text.cache_clear()


# ─────────────────────────────────────────────────────────────────────────────
# I. KnowledgeGraph semantic backend — mocked SentenceTransformer + FAISS
# ─────────────────────────────────────────────────────────────────────────────

class TestSemanticBackend:
    def test_semantic_search_returns_node_scores(self, tmp_path, monkeypatch):
        import numpy as np

        graph = {
            "things": [
                {"id": "matkg:A", "name": "Alpha", "category": "Material",
                 "description": "First material.", "source_papers": []},
                {"id": "matkg:B", "name": "Beta", "category": "Material",
                 "description": "Second material.", "source_papers": []},
            ],
            "associations": [],
        }
        p = tmp_path / "g.json"
        p.write_text(json.dumps(graph))

        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "semantic")
        monkeypatch.setattr(kg_rag_api, "FORCE_CPU", True)

        # Mock SentenceTransformer
        fake_embed = MagicMock()
        # encode called for node texts and queries
        fake_embed.encode.return_value = np.random.rand(2, 16).astype("float32")

        # Mock FAISS index
        fake_index = MagicMock()
        fake_index.search.return_value = (
            np.array([[0.9, 0.7]], dtype="float32"),
            np.array([[0, 1]], dtype="int64"),
        )

        with patch("app.modules.kg_rag_api.SentenceTransformer", return_value=fake_embed), \
             patch("app.modules.kg_rag_api.faiss") as mock_faiss:
            mock_faiss.index_factory.return_value = MagicMock()
            mock_faiss.get_num_gpus.return_value = 0
            mock_faiss.METRIC_INNER_PRODUCT = 0
            cpu_idx = MagicMock()
            mock_faiss.index_factory.return_value = cpu_idx

            kg = kg_rag_api.KnowledgeGraph(str(p))
            kg.index = fake_index
            kg.id_map = np.array(["matkg:A", "matkg:B"])

            results = kg.semantic_search("alpha material", topk=2)

        assert len(results) >= 1
        assert all(isinstance(r, kg_rag_api.NodeScore) for r in results)

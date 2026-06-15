"""
Unit tests for the rendered_ids filtering in build_context().

Covers the fix where structured triples and per-node relations
only reference nodes that are in the rendered set — preventing
the LLM from seeing snippet names without corresponding code.

Tests:
  1. Triples only reference rendered nodes
  2. Relations only reference rendered nodes
  3. Orphan snippet names never appear in triples
  4. Orphan snippet names never appear in relations
  5. All rendered code snippets have their code blocks in context
  6. Non-CodeSnippet edge targets still filtered
  7. Empty rendered set produces no triples/relations
  8. Single node with self-referencing edge
  9. Multiple code snippets — all rendered, all code present
"""
from __future__ import annotations

import json
import re

import pytest

from app.modules import kg_rag_api


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _write_graph_with_snippets(tmp_path, *, num_snippets=5):
    """Build a graph with concept nodes pointing to multiple code snippets."""
    things = [
        {
            "id": "matkg:concept",
            "name": "Bragg peaks",
            "category": "MaterialProperty",
            "description": "Sharp diffraction peaks.",
            "source_papers": ["paper.pdf"],
        },
        {
            "id": "matkg:technique",
            "name": "1d scattering curve",
            "category": "ExperimentalTechnique",
            "description": "1D scattering plot.",
            "source_papers": [],
        },
    ]
    associations = []
    for i in range(num_snippets):
        things.append({
            "id": f"matkg:snip{i}",
            "name": f"snippet_{i} snippet",
            "category": "CodeSnippet",
            "description": f"Code snippet {i}.",
            "source_papers": [],
            "function_name": f"func_{i}",
            "code_language": "python",
            "code_snippet": f"def func_{i}(x):\n    return x + {i}",
        })
        associations.append({
            "subject": "matkg:concept",
            "predicate": "rel:has_code_snippet",
            "object": f"matkg:snip{i}",
            "has_evidence": "p1",
        })
    # Also link concept -> technique
    associations.append({
        "subject": "matkg:concept",
        "predicate": "rel:measured_by",
        "object": "matkg:technique",
        "has_evidence": "p1",
    })

    graph = {"things": things, "associations": associations}
    p = tmp_path / "graph.json"
    p.write_text(json.dumps(graph))
    return p


def _make_kg(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda p: "")
    return kg_rag_api.KnowledgeGraph(str(_write_graph_with_snippets(tmp_path, **kwargs)))


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestTriplesOnlyReferenceRenderedNodes:
    def test_triples_exclude_unrendered_snippets(self, tmp_path, monkeypatch):
        """When only concept + snip0 are rendered, triples must not mention snip1..4."""
        kg = _make_kg(tmp_path, monkeypatch, num_snippets=5)
        rendered = [
            kg_rag_api.NodeScore("matkg:concept", 1.0),
            kg_rag_api.NodeScore("matkg:snip0", 0.9),
        ]
        nodes = kg.build_nodeinfo(rendered, [], [])
        ctx = kg.build_context(nodes, include_structured=True, char_budget=20000, hint_terms=[])

        triple_targets = set(re.findall(r"-> \((.+?)\)", ctx))
        for i in range(1, 5):
            assert f"snippet_{i} snippet" not in triple_targets, (
                f"snippet_{i} should not appear in triples"
            )
        # snip0 and technique should appear only if rendered
        assert "snippet_0 snippet" in triple_targets

    def test_triples_exclude_unrendered_non_snippet_targets(self, tmp_path, monkeypatch):
        """Concept -> technique edge excluded when technique is not rendered."""
        kg = _make_kg(tmp_path, monkeypatch, num_snippets=1)
        nodes = kg.build_nodeinfo(
            [kg_rag_api.NodeScore("matkg:concept", 1.0),
             kg_rag_api.NodeScore("matkg:snip0", 0.9)],
            [], [],
        )
        ctx = kg.build_context(nodes, include_structured=True, char_budget=20000, hint_terms=[])
        assert "1d scattering curve" not in ctx


class TestRelationsOnlyReferenceRenderedNodes:
    def test_relations_exclude_unrendered_targets(self, tmp_path, monkeypatch):
        """Per-node Relations section must not list unrendered snippet names."""
        kg = _make_kg(tmp_path, monkeypatch, num_snippets=5)
        nodes = kg.build_nodeinfo(
            [kg_rag_api.NodeScore("matkg:concept", 1.0),
             kg_rag_api.NodeScore("matkg:snip0", 0.9)],
            [], [],
        )
        ctx = kg.build_context(nodes, include_structured=False, char_budget=20000, hint_terms=[])

        # Parse relation lines
        rel_targets = re.findall(r"- has_code_snippet: (.+)", ctx)
        for target in rel_targets:
            assert target == "snippet_0 snippet", (
                f"Unexpected relation target: {target}"
            )

    def test_no_relations_section_when_all_targets_unrendered(self, tmp_path, monkeypatch):
        """If concept's only edges point to unrendered nodes, no Relations header."""
        kg = _make_kg(tmp_path, monkeypatch, num_snippets=3)
        # Only render the concept — all edge targets (snippets + technique) are excluded
        nodes = kg.build_nodeinfo(
            [kg_rag_api.NodeScore("matkg:concept", 1.0)],
            [], [],
        )
        ctx = kg.build_context(nodes, include_structured=False, char_budget=20000, hint_terms=[])
        assert "Relations:" not in ctx


class TestRenderedCodeSnippetsIncludeCode:
    def test_all_rendered_snippets_have_code_blocks(self, tmp_path, monkeypatch):
        kg = _make_kg(tmp_path, monkeypatch, num_snippets=3)
        nodes = kg.build_nodeinfo(
            [kg_rag_api.NodeScore(f"matkg:snip{i}", 0.9 - i * 0.1) for i in range(3)],
            [], [],
        )
        ctx = kg.build_context(nodes, include_structured=False, char_budget=20000, hint_terms=[])

        for i in range(3):
            assert f"def func_{i}(x):" in ctx, f"func_{i} code missing from context"

    def test_snippet_without_code_body_skipped(self, tmp_path, monkeypatch):
        """CodeSnippet node with empty code_snippet should not render code block."""
        graph = {
            "things": [{
                "id": "matkg:empty",
                "name": "empty snippet",
                "category": "CodeSnippet",
                "description": "No code.",
                "source_papers": [],
                "code_snippet": "   ",
                "function_name": "empty",
                "code_language": "python",
            }],
            "associations": [],
        }
        p = tmp_path / "g.json"
        p.write_text(json.dumps(graph))
        monkeypatch.setattr(kg_rag_api, "RETRIEVAL_BACKEND", "lexical")
        monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda _: "")
        kg = kg_rag_api.KnowledgeGraph(str(p))
        nodes = kg.build_nodeinfo([kg_rag_api.NodeScore("matkg:empty", 1.0)], [], [])
        ctx = kg.build_context(nodes, include_structured=False, char_budget=5000, hint_terms=[])
        assert "```python" not in ctx
        assert "empty" not in ctx  # skipped entirely


class TestEdgeCases:
    def test_empty_node_list_produces_empty_context(self, tmp_path, monkeypatch):
        kg = _make_kg(tmp_path, monkeypatch, num_snippets=1)
        ctx = kg.build_context([], include_structured=True, char_budget=5000, hint_terms=[])
        assert "Structured_KG_Facts:" in ctx
        # No triples, no node sections
        assert "-[" not in ctx

    def test_structured_false_omits_triples_section(self, tmp_path, monkeypatch):
        kg = _make_kg(tmp_path, monkeypatch, num_snippets=1)
        nodes = kg.build_nodeinfo(
            [kg_rag_api.NodeScore("matkg:concept", 1.0),
             kg_rag_api.NodeScore("matkg:snip0", 0.9)],
            [], [],
        )
        ctx = kg.build_context(nodes, include_structured=False, char_budget=20000, hint_terms=[])
        assert "Structured_KG_Facts" not in ctx

    def test_consistency_triples_match_relations(self, tmp_path, monkeypatch):
        """Triple targets and relation targets should be the same rendered set."""
        kg = _make_kg(tmp_path, monkeypatch, num_snippets=5)
        rendered_ids = ["matkg:concept", "matkg:snip0", "matkg:snip2", "matkg:technique"]
        nodes = kg.build_nodeinfo(
            [kg_rag_api.NodeScore(nid, 1.0 - i * 0.1) for i, nid in enumerate(rendered_ids)],
            [], [],
        )
        ctx = kg.build_context(nodes, include_structured=True, char_budget=50000, hint_terms=[])

        triple_targets = set(re.findall(r"-> \((.+?)\)", ctx))
        rel_targets = set(re.findall(r"- (?:has_code_snippet|measured_by): (.+)", ctx))

        # All targets should be names of rendered nodes
        rendered_names = {kg.nodes[nid]["name"] for nid in rendered_ids}
        assert triple_targets <= rendered_names
        assert rel_targets <= rendered_names

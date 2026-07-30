"""
Unit tests for the RAG system prompt and prompt builder.

Covers:
  - RAG_SYSTEM contains all 8 guidelines
  - Guideline #8 (CodeSnippet rendering) is present and accurate
  - build_rag_prompt output structure
  - build_baseline_prompt output structure
  - Conversation multi-turn builder
"""
from __future__ import annotations

import pytest

from app.modules import kg_rag_api


# ─────────────────────────────────────────────────────────────────────────────
# RAG_SYSTEM prompt content
# ─────────────────────────────────────────────────────────────────────────────


class TestRagSystemPrompt:
    def test_contains_all_eight_guidelines(self):
        for i in range(1, 9):
            assert f"{i})" in kg_rag_api.RAG_SYSTEM, f"Guideline {i}) missing from RAG_SYSTEM"

    def test_guideline_8_mentions_code_snippet(self):
        assert "CodeSnippet" in kg_rag_api.RAG_SYSTEM

    def test_guideline_8_requires_complete_source_code(self):
        assert "ALWAYS include the complete source code" in kg_rag_api.RAG_SYSTEM

    def test_guideline_8_forbids_claiming_code_missing(self):
        assert "never claim the code is missing" in kg_rag_api.RAG_SYSTEM

    def test_guideline_8_forbids_claiming_implemented_elsewhere(self):
        assert "implemented elsewhere" in kg_rag_api.RAG_SYSTEM

    def test_guideline_8_requires_code_snippet_disclaimer(self):
        assert kg_rag_api.CODE_SNIPPET_DISCLAIMER in kg_rag_api.RAG_SYSTEM
        assert "Immediately after each reproduced code block" in kg_rag_api.RAG_SYSTEM

    def test_mentions_kg_citation_format(self):
        assert "[KG:" in kg_rag_api.RAG_SYSTEM

    def test_mentions_pdf_citation_format(self):
        assert "[PDF:" in kg_rag_api.RAG_SYSTEM

    def test_forbids_domain_knowledge(self):
        assert "Never add outside or domain knowledge" in kg_rag_api.RAG_SYSTEM
        assert "state the gap instead of answering from memory" in kg_rag_api.RAG_SYSTEM

    def test_guideline_7_forbids_invented_publication_metadata(self):
        assert "STRICTLY FORBIDDEN" in kg_rag_api.RAG_SYSTEM
        assert "Do not infer, recall, or invent" in kg_rag_api.RAG_SYSTEM
        assert "Source_Metadata" in kg_rag_api.RAG_SYSTEM

    def test_is_nonempty_string(self):
        assert isinstance(kg_rag_api.RAG_SYSTEM, str)
        assert len(kg_rag_api.RAG_SYSTEM) > 100


# ─────────────────────────────────────────────────────────────────────────────
# build_rag_prompt
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildRagPrompt:
    def test_contains_question_section(self):
        prompt = kg_rag_api.build_rag_prompt("What is P3HT?", "## P3HT\nA polymer.")
        assert "Question:\nWhat is P3HT?" in prompt

    def test_contains_retrieved_context_section(self):
        prompt = kg_rag_api.build_rag_prompt("Q?", "## Node\nContext here.")
        assert "Retrieved Context:\n## Node\nContext here." in prompt

    def test_contains_citation_instruction(self):
        prompt = kg_rag_api.build_rag_prompt("Q?", "ctx")
        assert "[KG: ...]" in prompt or "[KG:" in prompt
        assert "[PDF: ...]" in prompt or "[PDF:" in prompt

    def test_contains_strict_grounding_instruction(self):
        prompt = kg_rag_api.build_rag_prompt("Q?", "ctx")
        assert "do not add outside or domain knowledge" in prompt
        assert "state the evidence gap instead of answering from memory" in prompt

    def test_contains_publication_metadata_grounding_rule(self):
        prompt = kg_rag_api.build_rag_prompt("Q?", "ctx")
        assert "Publication metadata rule:" in prompt
        assert "do not state any author name" in prompt
        assert "verbatim in the Retrieved Context" in prompt

    def test_contains_code_snippet_disclaimer_instruction(self):
        prompt = kg_rag_api.build_rag_prompt("Q?", "ctx")
        assert kg_rag_api.CODE_SNIPPET_DISCLAIMER in prompt
        assert "append this exact disclaimer immediately after the closing fence" in prompt

    def test_strips_whitespace_from_question(self):
        prompt = kg_rag_api.build_rag_prompt("  spaced question  ", "ctx")
        assert "Question:\nspaced question" in prompt

    def test_strips_whitespace_from_context(self):
        prompt = kg_rag_api.build_rag_prompt("Q", "  spaced ctx  ")
        assert "Retrieved Context:\nspaced ctx" in prompt


# ─────────────────────────────────────────────────────────────────────────────
# build_baseline_prompt
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildBaselinePrompt:
    def test_format(self):
        assert kg_rag_api.build_baseline_prompt("Q?") == "Question: Q?\n\nAnswer:"

    def test_preserves_question_text(self):
        result = kg_rag_api.build_baseline_prompt("What is SAXS?")
        assert "What is SAXS?" in result


# ─────────────────────────────────────────────────────────────────────────────
# Conversation
# ─────────────────────────────────────────────────────────────────────────────


class TestConversation:
    def test_initial_state_has_system_message(self):
        c = kg_rag_api.Conversation("You are a helper.")
        msgs = c.build("hello")
        assert msgs[0] == {"role": "system", "content": "You are a helper."}
        assert msgs[-1] == {"role": "user", "content": "hello"}

    def test_add_appends_user_assistant_pair(self):
        c = kg_rag_api.Conversation("sys")
        c.add("u1", "a1")
        c.add("u2", "a2")
        msgs = c.build("u3")
        assert len(msgs) == 6  # system + 2*(user+assistant) + final user
        assert msgs[1] == {"role": "user", "content": "u1"}
        assert msgs[2] == {"role": "assistant", "content": "a1"}

    def test_build_with_prepend_inserts_system_before_user(self):
        c = kg_rag_api.Conversation("sys")
        msgs = c.build("question", prepend="extra instruction")
        assert msgs[-2] == {"role": "system", "content": "extra instruction"}
        assert msgs[-1] == {"role": "user", "content": "question"}

    def test_build_without_prepend(self):
        c = kg_rag_api.Conversation("sys")
        msgs = c.build("q")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_build_does_not_mutate_internal_state(self):
        c = kg_rag_api.Conversation("sys")
        msgs1 = c.build("q1")
        msgs2 = c.build("q2")
        # build should not add q1 to history
        assert len(msgs1) == len(msgs2) == 2

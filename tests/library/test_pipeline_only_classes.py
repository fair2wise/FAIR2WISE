"""CodeSnippet is filled by the pipeline: extraction and preview prompts never offer it as a category (shipped or user schema)."""
from __future__ import annotations

from pathlib import Path

from app.modules.term_extractor.schema import PIPELINE_ONLY_CLASSES, SchemaHelper
from tests.library.test_user_schema_validation import GOOD

REPO = Path(__file__).resolve().parents[2]
SHIPPED = REPO / "storage" / "schema" / "v0.3.1" / "matkg_schema.yaml"


def _entity_lines(context: str) -> list[str]:
    """The ENTITY TYPES lines of a rendered schema prompt."""
    block = context.split("ENTITY TYPES (use exactly these names):\n", 1)[1].split("\n\nVALID RELATIONSHIPS", 1)[0]
    return block.splitlines()


def test_the_shipped_schema_prompt_does_not_offer_codesnippet_or_its_relations():
    helper = SchemaHelper(str(SHIPPED))
    context = helper.get_schema_context_for_llm()
    assert "CodeSnippet" not in context and "has_code_snippet" not in context and "code_snippet" not in context
    assert any(line.startswith("- Material:") for line in _entity_lines(context))  # the rest is still offered
    # The schema itself keeps the class: code_snippets still become CodeSnippet nodes and validation still knows it.
    assert "CodeSnippet" in helper.classes and "has_code_snippet" in helper.slots
    assert PIPELINE_ONLY_CLASSES == {"CodeSnippet"}


def test_a_user_schema_prompt_does_not_offer_codesnippet(tmp_path):
    path = tmp_path / "user.yaml"
    path.write_text(GOOD, encoding="utf-8")
    helper = SchemaHelper(str(path), sanitize_descriptions=True)
    assert "CodeSnippet" in helper.classes
    context = helper.get_schema_context_for_llm()
    assert "CodeSnippet" not in context
    assert any(line.startswith("- Thing:") for line in _entity_lines(context))


def test_the_condensed_view_leaves_it_out_too(tmp_path):
    helper = SchemaHelper(str(SHIPPED))
    text, info = helper.bounded_schema_context(3_000)
    assert info is not None and "CodeSnippet" not in text and "has_code_snippet" not in text

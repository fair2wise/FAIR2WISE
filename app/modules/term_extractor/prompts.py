"""Prompt construction for the term-extractor agent.

Public API:
    build_page_prompt(schema_ctx, filename, page_num, text, *, max_text_len=8000) -> str
"""

_FEW_SHOT = r"""
### EXAMPLE
Input:
CONTENT:
"Poly(3-hexylthiophene) (P3HT) is a conjugated polymer used in organic photovoltaics."

Output:
{
  "terms": [
    {
      "term": "Poly(3-hexylthiophene) (P3HT)",
      "definition": "A conjugated polymer used in organic photovoltaics.",
      "category": "Polymer",
      "formula": "C10H14S",
      "relations": [
        {
          "relation": "has_application",
          "related_term": "organic photovoltaics",
          "verified": true
        }
      ]
    }
  ]
}
### END-EXAMPLE
"""

_TASK_TEMPLATE = """\
=== EXTRACTION TASK ===
schema_context:
{schema_ctx}

PAPER: {filename}
PAGE: {page_num}

CONTENT:
{text}

INSTRUCTIONS:
1. Extract key materials-science terms + their relations using ONLY schema slots.
2. Do NOT output relations named 'description' or 'category'.
3. For each term:
   a. Call check_existing_term first.
   b. If 'not_found', call fuzzy_merge_term to catch punctuation variants and
      acronym↔expansion pairs (e.g. "GIWAXS"/"GI-WAXS", "ARPES"/"angle-resolved
      photoemission spectroscopy"). If it returns 'match:<key>', skip registration.
   c. For chemical entities call lookup_chebi; for any formula string call
      repair_formula (validates and auto-corrects via LLM if invalid).
   d. Call register_term only if no match was found.
"""


def build_page_prompt(
    schema_ctx: str,
    filename: str,
    page_num: int,
    text: str,
    *,
    max_text_len: int = 8000,
) -> str:
    """Return the full extraction prompt for one page.

    Args:
        schema_ctx: Output of SchemaHelper.get_schema_context_for_llm().
        filename:   PDF basename.
        page_num:   Zero-based page index (rendered as 1-based in the prompt).
        text:       Raw page text extracted from the PDF.
        max_text_len: Hard cap on page text length; tail is kept (most recent content).
    """
    truncated = text[-max_text_len:] if len(text) > max_text_len else text
    return (
        _TASK_TEMPLATE.format(
            schema_ctx=schema_ctx,
            filename=filename,
            page_num=page_num + 1,
            text=truncated,
        )
        + _FEW_SHOT
    )

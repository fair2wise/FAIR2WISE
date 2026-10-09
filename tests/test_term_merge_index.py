"""Tests for ANN top-k term merge (no full-vocabulary LLM prompts)."""
from __future__ import annotations

import numpy as np

from app.modules.term_extractor.merge_index import (
    TermMergeIndex,
    build_merge_prompt,
    clean_term,
)
from app.modules.term_extractor.store import TermStore
from app.modules.term_extractor.tools import ToolState, build_tools


def _char_encoder(texts):
    """Deterministic 16-d bag-of-character vectors for tests (no MiniLM)."""
    rows = []
    for text in texts:
        vec = np.zeros(16, dtype=np.float32)
        for i, ch in enumerate(text.lower()):
            vec[i % 16] += (ord(ch) % 31) / 31.0
        rows.append(vec)
    return np.stack(rows)


def test_clean_term_collapses_whitespace():
    assert clean_term("  GI  WAXS\n") == "GI WAXS"


def test_query_returns_at_most_k_and_skips_exact_query():
    index = TermMergeIndex(top_k=3, encoder=_char_encoder)
    names = [f"term-{i:03d}" for i in range(40)] + ["GIWAXS"]
    index.ensure(names)
    hits = index.query("GI-WAXS", k=3)
    assert len(hits) <= 3
    assert "GI-WAXS" not in hits
    assert all(h in names for h in hits)


def test_add_makes_new_name_retrievable():
    index = TermMergeIndex(top_k=5, encoder=_char_encoder)
    index.ensure(["P3HT", "PCBM"])
    index.add("PTB7-Th")
    hits = index.query("PTB7 Th", k=5)
    assert "PTB7-Th" in hits


def test_merge_prompt_lists_only_supplied_candidates():
    prompt = build_merge_prompt("GIWAXS", ["GI-WAXS", "GISAXS"])
    assert "GI-WAXS" in prompt
    assert "GISAXS" in prompt
    assert "the entire list of all already" not in prompt.lower()
    assert "full vocabulary" in prompt
    assert prompt.count("\n- ") == 2


def test_fuzzy_merge_term_does_not_send_full_store(tmp_path):
    store = TermStore(str(tmp_path / "terms.json"))
    for i in range(50):
        from app.modules.term_extractor.models import TermRecord

        store.upsert(TermRecord(term=f"Material-{i:02d}", definition="x", category="Material"))

    captured = []

    def llm_invoke(prompt: str) -> str:
        captured.append(prompt)
        return "None"

    index = TermMergeIndex(top_k=12, encoder=_char_encoder)
    index.ensure(store.all_display_names())
    state = ToolState(
        store=store,
        schema=None,
        services=None,
        llm_invoke=llm_invoke,
        merge_index=index,
    )
    tools = {item.name: item for item in build_tools(state)}
    result = tools["fuzzy_merge_term"].invoke({"name": "Material-07-alt"})
    assert result == "no_match"
    assert captured
    prompt = captured[0]
    listed = [line[2:] for line in prompt.splitlines() if line.startswith("- ")]
    # Instruction examples also use "- " bullets; candidate block is the short list
    # after "retrieval candidates only". Count candidate-section bullets only.
    section = prompt.split("retrieval candidates only", 1)[1].split("You must decide", 1)[0]
    cand_lines = [line[2:] for line in section.splitlines() if line.startswith("- ")]
    assert 1 <= len(cand_lines) <= 12
    assert len(cand_lines) < 50
    assert "Material-00" not in cand_lines or len(cand_lines) <= 12

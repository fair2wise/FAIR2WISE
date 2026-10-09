"""check_existing_term must not treat an acronym as a substring of a longer term."""
from __future__ import annotations

import pytest

from app.modules.term_extractor.models import TermRecord
from app.modules.term_extractor.store import TermStore
from app.modules.term_extractor.tools import ToolState, build_tools

pytestmark = pytest.mark.eval


def _tool(tmp_path, term: str):
    store = TermStore(str(tmp_path / "terms.json"))
    store.upsert(TermRecord(term=term, definition="a material class", category="Material"))
    tools = {item.name: item for item in build_tools(ToolState(store=store, schema=None, services=None))}
    return tools["check_existing_term"]


def test_exact_semiconductor_matches(tmp_path) -> None:
    tool = _tool(tmp_path, "semiconductor")
    assert tool.invoke({"name": "semiconductor"}) == "exact_match:semiconductor"


@pytest.mark.xfail(
    strict=True,
    reason="check_existing_term uses substring containment, so SEM matches semiconductor",
)
def test_sem_does_not_match_semiconductor(tmp_path) -> None:
    tool = _tool(tmp_path, "semiconductor")
    result = tool.invoke({"name": "SEM"})
    assert result == "not_found"
    assert "semiconductor" not in result

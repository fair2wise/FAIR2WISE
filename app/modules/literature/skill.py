"""The ``research_literature`` skill over the six ``lit_*`` tools (no provider imports).

Kept apart from :mod:`.tools` so the read-path bootstrap can name the skill
without importing HTTP clients (tests/read_path import-side-effect check).
"""

from __future__ import annotations

from ..agent_runtime.skills import Skill

#: The six verified donor tool names (L-01), in preferred order.
LIT_TOOL_NAMES: tuple[str, ...] = (
    "lit_search_papers", "lit_get_papers_by_doi", "lit_find_citing_papers", "lit_find_related_papers",
    "lit_read_paper", "lit_read_paper_section",
)

LITERATURE_RESEARCH_SKILL = Skill(
    name="research_literature",
    description="Discover published papers and read open-access papers section by section.",
    when_to_use=(
        "The user needs literature beyond F2W's ingested knowledge. Search results are external candidates, not "
        "ingested evidence: report them as such. Read one section at a time and cite DOI, section and page."
    ),
    tools=LIT_TOOL_NAMES,
    required_capabilities=frozenset({"chat.ask"}),
)

__all__ = ("LITERATURE_RESEARCH_SKILL", "LIT_TOOL_NAMES")

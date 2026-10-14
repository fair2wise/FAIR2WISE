"""The ``read_paper_figures`` skill over the vision and science tools (names only; no heavy imports)."""

from __future__ import annotations

from typing import Tuple

from ..agent_runtime.skills import Skill

VISION_TOOL_NAMES: Tuple[str, ...] = (
    "vision_extract_figures", "vision_classify_figure", "vision_read_scale", "vision_read_qrange",
    "vision_read_orientation", "vision_extract_tables",
)
SCIENCE_TOOL_NAMES: Tuple[str, ...] = (
    "science_normalize_quantity", "science_q_to_spacing", "science_spacing_to_q",
)

PAPER_FIGURES_SKILL = Skill(
    name="read_paper_figures",
    description="Read figures, panels and tables of an ingested paper from its PDF text layer, with locators.",
    when_to_use=(
        "A question depends on a paper's figure, panel, scale bar, q axis, orientation or table. Start with "
        "vision_extract_figures; report only readings marked reported and name each figure/panel locator. "
        "Withheld readings and uncertainties are findings too: say they are unresolved, never fill them in. "
        "Orientation statements from the paper are observations; inference candidates are not. Use the science "
        "tools for unit conversion; never convert by hand. Figure images and crops are internal and are not shown."
    ),
    tools=(*VISION_TOOL_NAMES, *SCIENCE_TOOL_NAMES),
    required_capabilities=frozenset({"chat.ask"}),
)

__all__ = ("PAPER_FIGURES_SKILL", "SCIENCE_TOOL_NAMES", "VISION_TOOL_NAMES")

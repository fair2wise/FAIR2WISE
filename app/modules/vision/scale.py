"""Panel scale-bar readings with provenance and withholding (V-06).

A scale is *reported* only when a bar was measured in the panel's own pixels AND its
length came from the text layer or the caption (assigned to THIS panel), and nothing
upstream (panel count mismatch, rotated page, unlocated figure) is blocking. Everything
else is withheld with a named reason: an unread scale is never a missing scale, and a
guessed one is never a measurement. Pixels are analysed in-process and not returned.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/figure_scale.py (reported-scale semantics:
``record_figure_scale`` source/needs_review rules) (BSD-3-Clause); measurement kernels in ``kernels/scalebar.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .kernels.scalebar import caption_bar_lengths, pair_lengths_to_panels, read_panel_scale
from .kernels.types import PanelBox, ScaleReading, TextSpan

TRUSTED_LENGTH_SOURCES = ("text_layer", "caption")
#: A real bar spans a meaningful fraction of its panel; a white panel letter is a few pixels. A lone short
#: saturated run is refused (F2W guard over the kernel, whose dominance test only compares candidates).
MIN_BAR_FRACTION = 0.05


@dataclass(frozen=True)
class ScaleFinding:
    """One panel's scale: the kernel reading, whether it is reported, and why not when withheld."""

    panel_index: int
    reading: ScaleReading
    reported: bool
    withheld_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form; ``nm_per_px`` is only populated in ``value`` when reported."""
        reading = self.reading.to_dict()
        return {
            "panel_index": self.panel_index,
            "reported": self.reported,
            "value": {"nm_per_px": reading["nm_per_px"], "bar_length_px": reading["bar_length_px"],
                      "bar_length_nm": reading["bar_length_nm"], "length_source": reading["length_source"]}
            if self.reported else None,
            "withheld_reason": self.withheld_reason,
            "reason": reading["reason"],
            "needs_review": reading["needs_review"],
            "review_reasons": reading["review_reasons"],
        }


def caption_length_for(caption: str, label: str, n_panels: int) -> tuple[float | None, str]:
    """``(nm | None, reason)``: the bar length the caption assigns to this panel, or why it cannot."""
    pairs = pair_lengths_to_panels(caption)
    if label and label.lower() in pairs:
        return pairs[label.lower()], ""
    lengths = caption_bar_lengths(caption)
    if not lengths:
        return None, "caption_states_no_bar_length"
    if len(lengths) == 1 and n_panels == 1:
        return lengths[0][0], ""
    return None, "caption_bar_length_not_assignable_to_panel"


def read_scales(
    image: Any,
    panels: Sequence[PanelBox],
    caption: str,
    spans_by_panel: Mapping[int, Sequence[TextSpan]],
    *,
    blocked: str = "",
) -> list[ScaleFinding]:
    """Read every panel's scale. ``blocked`` (a blocking uncertainty code) withholds every value."""
    out: list[ScaleFinding] = []
    for i, panel in enumerate(panels):
        length, why = caption_length_for(caption, panel.label, len(panels))
        reading = read_panel_scale(image, panel, caption_length_nm=length,
                                   length_spans=list(spans_by_panel.get(i, ())))
        if blocked:
            out.append(ScaleFinding(i, reading, False, blocked))
        elif not reading.determined:
            out.append(ScaleFinding(i, reading, False, reading.reason or why or "undetermined"))
        elif (reading.bar_length_px or 0.0) < MIN_BAR_FRACTION * max(panel.width, 1):
            out.append(ScaleFinding(i, reading, False, "bar_too_short_for_panel"))
        elif reading.length_source not in TRUSTED_LENGTH_SOURCES:
            out.append(ScaleFinding(i, reading, False, "ocr_length_needs_review"))
        elif reading.needs_review:
            out.append(ScaleFinding(i, reading, False, "needs_review"))
        else:
            out.append(ScaleFinding(i, reading, True))
    return out


__all__ = ("ScaleFinding", "TRUSTED_LENGTH_SOURCES", "caption_length_for", "read_scales")

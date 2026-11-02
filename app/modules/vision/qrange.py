"""Per-panel q-axis ranges with reciprocal-unit handling and withholding (V-07).

Each panel reads ONLY its own text-layer spans (no cross-panel label mixing). A range is
reported (in nm^-1, as the kernel folds it) only when:

* the unit came from the panel's text layer or the figure caption (never OCR, never the
  paper prose: the donor refuses ``unit_source="sections"``);
* the axis is identified as q (an axis title in the panel, or the caption naming q): the
  donor measured intensity/temperature axes being fitted and labelled q without this check;
* the caption does not state two different inverse units while the panel states none;
* nothing upstream is blocking (panel mismatch, rotated page, unlocated figure).

Everything else is withheld with a named reason.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/figure_qrange.py (``QRANGE_UNIT_SOURCES``
admission rule, ``record_figure_qrange`` needs_review) (BSD-3-Clause); axis fit in ``kernels/qaxis.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from .kernels.qaxis import read_q_axis
from .kernels.types import PanelBox, QAxisReading, TextSpan
from .kernels.units import caption_inverse_unit, caption_inverse_units

ADMITTED_UNIT_SOURCES = ("text_layer", "caption")
_Q_TITLE = re.compile(r"(?i)(?<![A-Za-z0-9])q(?:\s?_?\s?(?:xy|x|y|z|r|\|\|))?(?![A-Za-z0-9])|scattering vector")


@dataclass(frozen=True)
class QRangeFinding:
    """One panel's q range: kernel reading, whether reported, axis identification and reason."""

    panel_index: int
    reading: QAxisReading
    reported: bool
    withheld_reason: str = ""
    axis_identified_by: str = ""

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form; the numeric range appears under ``value`` only when reported."""
        r = self.reading.to_dict()
        return {
            "panel_index": self.panel_index, "reported": self.reported,
            "value": {"q_min_nm_inv": r["q_min_nm_inv"], "q_max_nm_inv": r["q_max_nm_inv"], "unit": "nm^-1",
                      "unit_source": r["unit_source"], "axis_kind": r["axis_kind"], "n_ticks": r["n_ticks"],
                      "confidence": r["confidence"]} if self.reported else None,
            "withheld_reason": self.withheld_reason, "reason": r["reason"],
            "axis_identified_by": self.axis_identified_by,
            "needs_review": r["needs_review"], "review_reasons": r["review_reasons"],
        }


def identify_q_axis(spans: Sequence[TextSpan], caption: str) -> str:
    """``"axis_title"``, ``"caption"`` or ``""``: how (if at all) this panel's axis is shown to be q."""
    if any(s.source == "text_layer" and _Q_TITLE.search(s.text) for s in spans):
        return "axis_title"
    if _Q_TITLE.search(caption or ""):
        return "caption"
    return ""


def read_qranges(
    panels: Sequence[PanelBox],
    caption: str,
    spans_by_panel: Mapping[int, Sequence[TextSpan]],
    *,
    blocked: str = "",
) -> list[QRangeFinding]:
    """Read every panel's q range; see the module docstring for the reporting rules."""
    units = caption_inverse_units(caption)
    cap_unit = caption_inverse_unit(caption)
    out: list[QRangeFinding] = []
    for i in range(len(panels)):
        spans = list(spans_by_panel.get(i, ()))
        reading = read_q_axis(spans, caption_unit=cap_unit)
        ident = identify_q_axis(spans, caption)
        if blocked:
            reason = blocked
        elif reading.reason == "no_unit" and len(units) > 1:
            reason = "caption_states_conflicting_inverse_units"
        elif not reading.determined:
            reason = reading.reason or "undetermined"
        elif reading.unit_source not in ADMITTED_UNIT_SOURCES:
            reason = "unit_source_not_admitted"
        elif not ident:
            reason = "axis_not_identified_as_q"
        elif reading.needs_review:
            reason = "needs_review"
        else:
            reason = ""
        out.append(QRangeFinding(i, reading, not reason, reason, ident))
    return out


__all__ = ("ADMITTED_UNIT_SOURCES", "QRangeFinding", "identify_q_axis", "read_qranges")

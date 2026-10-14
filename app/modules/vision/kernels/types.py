"""What an extraction pass can conclude, including that it cannot conclude.

UNDETERMINABLE IS A RESULT, not an error and not a zero. Measured on this
corpus: 27% of figures carry no caption at all, 16% of scattering-figure pages
are flat rasters with no text layer, and only 108 of 2,931 captions mention a
scale bar. An extraction that returned "no scale" and "scale unknown" as the
same value would let a caller treat the second as the first -- which is how a
figure with an unread scale becomes a figure with no scale, and then a
dimension claim nobody can check.

Every reading here therefore carries a machine-readable REASON when it has no
value, and `needs_review` + `review_reasons` mirroring the shape
`figure_classification`'s verdicts already use, so one review surface can show
both.


Adapted from als-computing/forge_rsoxs_analysis src/forge/analysis/figure_extraction/types.py (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "PanelBox",
    "QAxisReading",
    "ScaleReading",
    "TextSpan",
]


@dataclass(frozen=True)
class TextSpan:
    """One run of text inside a figure, with where it sits.

    Attributes:
        text: The run, VERBATIM. Never normalised here -- the superscript in
            ``'(Å -1 )'`` is the Angstrom-versus-nanometre decider, and a
            pipeline that tidied it on the way in would destroy the one signal
            OCR cannot supply.
        bbox: ``(left, top, right, bottom)`` in the span's own coordinates.
            ``None`` when the parser gave no provenance -- the text is still
            useful (a unit, a caption fragment) with no position.
        page: Page the run sits on.
        source: ``"text_layer"`` (the PDF's own) or ``"ocr"``. LOAD-BEARING:
            a unit may only be believed from the text layer, so anything that
            reads a unit has to be able to tell them apart.
    """

    text: str
    bbox: tuple[float, float, float, float] | None = None
    page: int = 0
    source: str = "text_layer"

    @property
    def is_ocr(self) -> bool:
        """True when a transcription step stands between the pixels and this."""
        return self.source == "ocr"


@dataclass(frozen=True)
class PanelBox:
    """One panel of a figure, in the figure PNG's own pixel coordinates.

    Attributes:
        left / top / right / bottom: Pixel bounds, top-left origin, as the
            image itself is indexed.
        label: The panel letter, or ``""`` when the figure marks none. An
            unlabelled panel is still a panel.
        method: How it was found -- ``"text_layer"``, ``"gutter"`` or
            ``"whole_figure"`` (a single-panel figure standing as its own).
    """

    left: int
    top: int
    right: int
    bottom: int
    label: str = ""
    method: str = "gutter"

    @property
    def width(self) -> int:
        """Pixel width."""
        return self.right - self.left

    @property
    def height(self) -> int:
        """Pixel height."""
        return self.bottom - self.top

    def contains(self, x: float, y: float) -> bool:
        """Is this pixel inside the panel? Right/bottom exclusive."""
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass(frozen=True)
class ScaleReading:
    """A figure or panel's scale, or the named reason it has none.

    Attributes:
        nm_per_px: The answer, or ``None``.
        bar_length_px: Measured bar length.
        bar_length_nm: What the bar was said to be.
        length_source: Where the NANOMETRES came from -- ``"caption"``,
            ``"text_layer"`` or ``"ocr"``. The pixels are always measured; this
            names the half that was read, and it decides the provenance the
            graph stores.
        reason: ``""`` on success, else e.g. ``"no_bar"``,
            ``"bar_without_length"``, ``"multiple_bars_one_length"``.
        needs_review: A human should look, even if a value was produced.
        review_reasons: Why, in the classifier's vocabulary.
    """

    nm_per_px: float | None = None
    bar_length_px: float | None = None
    bar_length_nm: float | None = None
    length_source: str = ""
    reason: str = ""
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)

    @property
    def determined(self) -> bool:
        """True only when there is a number. An UNDETERMINABLE reading is not
        a zero and not a pass."""
        return self.nm_per_px is not None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe, for an edge property or a report row."""
        return {
            "nm_per_px": self.nm_per_px,
            "bar_length_px": self.bar_length_px,
            "bar_length_nm": self.bar_length_nm,
            "length_source": self.length_source,
            "reason": self.reason,
            "determined": self.determined,
            "needs_review": self.needs_review,
            "review_reasons": list(self.review_reasons),
        }


@dataclass(frozen=True)
class QAxisReading:
    """A scattering figure's q range, in inverse nanometres, or why not.

    Attributes:
        q_min_nm_inv / q_max_nm_inv: The range, or ``None``.
        unit_source: ``"text_layer"`` or ``"caption"`` ONLY. An OCR-sourced
            unit is refused upstream and never reaches here -- RapidOCR was
            measured to mangle the exponent on 100% of attempts, and getting it
            wrong is a factor of ten that lands on a plausible number.
        axis_kind: ``"linear"`` or ``"log"``.
        n_ticks: How many ticks the fit used.
        confidence: Fit quality, 0-1.
        reason / needs_review / review_reasons: As for :class:`ScaleReading`.
    """

    q_min_nm_inv: float | None = None
    q_max_nm_inv: float | None = None
    unit_source: str = ""
    axis_kind: str = ""
    n_ticks: int = 0
    confidence: float = 0.0
    reason: str = ""
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)

    @property
    def determined(self) -> bool:
        """True only with BOTH ends and a non-OCR unit."""
        return (
            self.q_min_nm_inv is not None
            and self.q_max_nm_inv is not None
            and self.unit_source in ("text_layer", "caption")
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe, for an edge property or a report row."""
        return {
            "q_min_nm_inv": self.q_min_nm_inv,
            "q_max_nm_inv": self.q_max_nm_inv,
            "unit_source": self.unit_source,
            "axis_kind": self.axis_kind,
            "n_ticks": self.n_ticks,
            "confidence": self.confidence,
            "reason": self.reason,
            "determined": self.determined,
            "needs_review": self.needs_review,
            "review_reasons": list(self.review_reasons),
        }

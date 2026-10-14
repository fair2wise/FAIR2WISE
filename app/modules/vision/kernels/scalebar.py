"""Pairing a bar measured in pixels with a length stated in prose.

TWO HALVES, MEASURED SEPARATELY, AND ONLY ONE OF THEM IS IN THE IMAGE. Published
scale bars are typically unlabelled white rectangles: the pixels say how long the
bar is, and only the caption or the figure's text layer says what that length
MEANS. So a reading is a pairing, and every way the pairing can be ambiguous is
a way to invent a scale.

NOT EVERY LENGTH IN A CAPTION IS A BAR. This is the defect that shapes the whole
module. Measured on a real caption:

    "The 120 nm nanocomposite thin films containing 9 vol % of 20 nm Fe3O4 NPs
     ... (a) AFM phase image. Scale bar 100 nm."

Reading every length gives ``[120, 20, 100, 0.004, 300, 300]`` -- a film
thickness, a particle size, the bar, and three numbers from elsewhere in the
prose. Taking any of the wrong ones yields a scale that is wrong by up to 6x and
looks entirely plausible. This repo already recorded the same hazard from the
other direction: of 33 captions carrying a length, the set is "dominated by film
thicknesses".

The fix is LOCALITY. A length counts only if it sits within
:data:`BAR_CONTEXT_CHARS` of the words "scale bar". Measured across four real
caption shapes, that window is the one that keeps both bars of a two-bar caption
while excluding a particle size mentioned earlier in the same sentence:

    ===============================  =====================  ==================
    caption shape                    window 30              window 40
    ===============================  =====================  ==================
    two bars, "respectively"         [2000, 200]  correct   [2000, 200]
    one bar, film thickness first    [100]        correct   [20, 100]  WRONG
    per-panel inline                 [100, 5]     correct   [100, 5]
    "bar is 500 nm for all images"   [500]        correct   [500]
    ===============================  =====================  ==================

Corpus-wide the locality rule takes captions carrying exactly one bar length
from 39 to 52, because it removes the lengths that were never bars.


Adapted from als-computing/forge_rsoxs_analysis src/forge/analysis/figure_extraction/scalebar.py (BSD-3-Clause).
"""

from __future__ import annotations

import re

from .panels import PANEL_MARKER_STYLES
from .types import PanelBox, ScaleReading, TextSpan
from .units import fold_length

__all__ = [
    "BAR_CONTEXT_CHARS",
    "BAR_DOMINANCE",
    "caption_bar_lengths",
    "pair_lengths_to_panels",
    "read_panel_scale",
]

#: How much longer the real bar must be than the next saturated candidate.
#:
#: MEASURED, and it corrects a rule that did not survive real data. The first
#: version refused any panel with more than one saturated candidate, on the
#: reasoning that picking between two would be a coin toss. Run against the
#: two-panel SEM figure this whole line of work started from, it refused BOTH
#: panels -- because a panel's own LETTER is white text on a dark field and is
#: a perfectly good saturated run:
#:
#: =========  ================  ==============  =======
#: panel      bar (px)          panel label     ratio
#: =========  ================  ==============  =======
#: (a)        26.77             11.39           2.35x
#: (b)        24.17              9.29           2.60x
#: =========  ================  ==============  =======
#:
#: A scale bar is an annotation sized to span a meaningful fraction of the
#: image; a glyph stroke is a few pixels. So the longest candidate wins when it
#: DOMINATES, and only genuinely comparable candidates -- a bar plus another
#: bar, or a bar plus a white label block -- are refused. 1.5 clears both real
#: cases with room and still refuses two bars of similar length, which is the
#: coin toss the original rule was right to avoid.
BAR_DOMINANCE: float = 1.5

#: How near the words "scale bar" a length must sit to count as that bar's.
#:
#: 30 characters, chosen against the four real caption shapes tabulated in the
#: module docstring. Widening to 40 admits a particle size named earlier in the
#: same sentence; narrowing to 20 still works on those four but leaves no
#: headroom for a longer lead-in like "Scale bars in both panels are".
BAR_CONTEXT_CHARS: int = 30

_BAR_PHRASE = re.compile(r"scale\s?bars?", re.IGNORECASE)

_LENGTH_TOKEN = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>m\s?m|nm|µm|μm|um|mm|nanometers?|nanometres?|"
    r"micrometers?|micrometres?|microns?|pm|Å|å|Å|A)\b",
    re.IGNORECASE,
)


def caption_bar_lengths(
    caption: str, *, window: int = BAR_CONTEXT_CHARS
) -> list[tuple[float, int]]:
    """Every length the caption states AS A SCALE BAR, with where it was said.

    Args:
        caption: The figure's caption.
        window: Maximum character gap between the phrase "scale bar" and the
            length, in either direction.

    Returns:
        ``(nanometres, character_offset)`` pairs in the order written. The
        offset is kept because pairing a length to a panel depends on their
        relative POSITIONS in the prose, and sorting would destroy the only
        signal the caption gives.

        An empty list means the caption states no bar length -- which is a real
        and common case (28 of 108 bar-mentioning captions name no number at
        all) and NOT a failure.
    """
    if not caption:
        return []
    phrases = [m.span() for m in _BAR_PHRASE.finditer(caption)]
    if not phrases:
        return []

    out: list[tuple[float, int]] = []
    for match in _LENGTH_TOKEN.finditer(caption):
        nm = fold_length(float(match["value"].replace(",", ".")), match["unit"])
        if nm is None:
            continue
        gap = min(
            (
                match.start() - end if match.start() >= end else start - match.end()
                for start, end in phrases
            ),
            default=None,
        )
        if gap is not None and gap <= window:
            out.append((nm, match.start()))
    return out


def pair_lengths_to_panels(caption: str) -> dict[str, float]:
    """Which panel each stated bar length belongs to, where that is decidable.

    THE 42 CAPTIONS A PER-FIGURE SCALE CANNOT HOLD. "Scale bars are 2 um and
    0.2 um in (a) and (b), respectively" states two scales; a figure node holds
    one, so storing either asserts a falsehood about the other panel.

    The rule is deliberately narrow: pair only when the number of stated bar
    lengths equals the number of distinct panel letters, and pair them IN
    ORDER. That covers "respectively" and the per-panel inline form
    ("(A) ... Scale bar, 100 nm. (B) ... Scale bar, 5 nm") and refuses
    everything else.

    Args:
        caption: The figure's caption.

    Returns:
        ``{panel_letter: nanometres}``, or an EMPTY DICT when the pairing is
        not decidable. Empty means "this caption does not say", never "these
        panels have no scale" -- a caller must not read one as the other.
    """
    lengths = caption_bar_lengths(caption)
    if not lengths:
        return {}

    marks: list[tuple[int, str]] = []
    for pattern in PANEL_MARKER_STYLES:
        for match in pattern.finditer(caption):
            marks.append((match.start(), match.group("l").lower()))
    # First mention wins: a caption re-referring to (a) later must not make it
    # a second panel and shift every pairing by one.
    ordered: list[str] = []
    for _pos, letter in sorted(marks):
        if letter not in ordered:
            ordered.append(letter)

    if len(ordered) < 2 or len(ordered) != len(lengths):
        return {}
    return {letter: nm for letter, (nm, _pos) in zip(ordered, lengths, strict=True)}


def read_panel_scale(
    image: object,
    panel: PanelBox,
    *,
    caption_length_nm: float | None = None,
    length_spans: list[TextSpan] | None = None,
    saturation: int = 250,
) -> ScaleReading:
    """Measure one panel's bar and pair it with a length.

    Args:
        image: The FIGURE as a 2-D greyscale array. The panel is cropped from
            it here rather than by the caller, so the crop and the panel box
            cannot disagree.
        panel: Where the panel sits, in figure-PNG pixels.
        caption_length_nm: The length the caption assigns to THIS panel, if the
            caller resolved one (see :func:`pair_lengths_to_panels`).
        length_spans: Text runs already known to sit inside this panel. The
            first that parses to a length is used, and it OUTRANKS the caption:
            a number printed beside the bar describes that bar, where a caption
            describes a figure and had to be apportioned.
        saturation: Grey level counted as bar.

    Returns:
        A :class:`ScaleReading`. Every refusal is NAMED -- ``"no_bar"``,
        ``"multiple_bars"``, ``"bar_without_length"``, ``"panel_too_small"`` --
        because a silent skip and a considered refusal look identical in a
        report and only one of them can be trusted.
    """
    import numpy as np

    from .barmeasure import measure_scale_bar_result

    arr = np.asarray(image)
    if arr.ndim != 2:
        raise ValueError(f"image must be 2-D greyscale; got shape {arr.shape}")

    crop = arr[panel.top:panel.bottom, panel.left:panel.right]
    if crop.size == 0 or min(crop.shape) < 4:
        return ScaleReading(reason="panel_too_small")

    found: list[tuple[float, str]] = []
    for quadrant in ("bottom-right", "bottom-left", "top-right", "top-left"):
        result = measure_scale_bar_result(crop, quadrant=quadrant, saturation=saturation)
        if result.found:
            found.append((float(result.length_px or 0.0), quadrant))

    if not found:
        return ScaleReading(reason="no_bar")

    found.sort(key=lambda pair: pair[0], reverse=True)
    if len(found) > 1 and found[0][0] < found[1][0] * BAR_DOMINANCE:
        # NOT a vote, and not a rejection of every second candidate either. A
        # panel letter is white text and is always a candidate; a bar is 2.4x
        # longer than one (measured). Two candidates of COMPARABLE length are
        # the real ambiguity -- a bar plus another bar, or a bar plus a white
        # label block -- and picking between those would be a coin toss
        # recorded as a measurement.
        return ScaleReading(
            reason="ambiguous_bars",
            needs_review=True,
            review_reasons=[f"top_two_candidates_{found[0][0]:.1f}px_and_{found[1][0]:.1f}px"],
        )

    bar_px, _quadrant = found[0]

    length_nm: float | None = None
    source = ""
    for span in length_spans or []:
        for match in _LENGTH_TOKEN.finditer(span.text):
            nm = fold_length(float(match["value"].replace(",", ".")), match["unit"])
            if nm is not None:
                length_nm, source = nm, "ocr" if span.is_ocr else "text_layer"
                break
        if length_nm is not None:
            break
    if length_nm is None and caption_length_nm is not None:
        length_nm, source = float(caption_length_nm), "caption"

    if length_nm is None:
        return ScaleReading(
            bar_length_px=bar_px,
            reason="bar_without_length",
            needs_review=True,
            review_reasons=["bar_found_but_no_length_stated"],
        )

    return ScaleReading(
        nm_per_px=length_nm / bar_px,
        bar_length_px=bar_px,
        bar_length_nm=length_nm,
        length_source=source,
    )

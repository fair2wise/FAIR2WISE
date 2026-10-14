"""Which panels a figure has, from its caption and from its pixels.

WHY A FIGURE IS THE WRONG UNIT. Measured on this corpus: at least 45% of
captioned figures are multi-panel composites, and the real object of analysis
is a PANEL -- one GISAXS pattern, one micrograph. A `paper_figure` node holds
ONE scale, so a caption reading "Scale bars are 2 um and 0.2 um in (a) and (b)"
cannot be stored against it without asserting a falsehood about one of the two
panels. 51 of the 65 scale-bar captions carrying a number are that shape.

It also decides whether a scale bar can be MEASURED at all. `measure_scale_bar`
works on a panel crop of a micrograph, where the bar is the one saturated thing
on a dark field; pointed at a whole published plate it returns the page
background, which is why it now refuses a search region that is mostly
saturated. So panel segmentation is a prerequisite for scale extraction rather
than a refinement of it.

THREE MARKER STYLES, ALL PRESENT HERE. A matcher for `(a)` alone under-reports
badly -- it missed 8 of 20 multi-panel captions in a measured sample, including
"a) Cross-sectional SEM image...; scale bar: 500 nm. b) J-V curves", which names
ONE length while describing SIX panels. Reporting that as a single-panel figure
is exactly the fabrication this package exists to avoid.


Adapted from als-computing/forge_rsoxs_analysis src/forge/analysis/figure_extraction/panels.py (BSD-3-Clause).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .geometry import FigureFrame, spans_inside
from .types import PanelBox, TextSpan

__all__ = [
    "MAX_PANELS",
    "MIN_PANEL_PX",
    "ROW_BAND_PX",
    "PANEL_MARKER_STYLES",
    "caption_names_multiple_panels",
    "consecutive_from_a",
    "ink_mask",
    "label_panels_from_spans",
    "panel_labels",
    "reconcile_panels",
    "segment_panels",
]

#: ``(left, top, right, bottom)`` in figure pixels, the internal working shape
#: before a box earns a :class:`PanelBox`.
_Box = tuple[int, int, int, int]

#: The three ways this corpus marks a panel in caption prose, each measured.
#:
#: * parenthesised -- ``"(a) SEM top-view ... (b) GISAXS pattern ..."``
#: * trailing paren -- ``"Figure 3. a) - TEM images ... b) Carbon map ..."``
#: * bare, Nature style -- ``"a Optical microscope image ... b Atomic force ..."``
#:   and ``"a , Process flow ... b , Plan view ..."``
#:
#: The bare form is deliberately the most conservative of the three: it demands
#: a following comma, or a Capital-then-lowercase word, because a lone letter is
#: otherwise indistinguishable from ordinary prose. MEASURED: loosening it to
#: "any non-space" newly calls 317 of 2,931 captions multi-panel -- 11% of the
#: corpus -- on English like "in a fully saturated water-vapor atmosphere".
#:
#: THE KNOWN COST OF THAT CHOICE: a bare marker followed by an ALL-CAPS
#: initialism is missed -- "a GIWAXS geometry for examination of ..." is a real
#: panel marker this does not find. The trade is taken deliberately in the
#: under-reporting direction. A missed panel leaves a figure looking
#: single-panel, which is recoverable; a false panel splits a figure that has
#: none, and its scale is then attributed to a region that does not exist.
#: A COLON STYLE EXISTS AND IS NOT WORTH READING. The reference plate's own
#: caption letters its panels "a: phi = 1.0; b: phi = 0.750", and a matcher for
#: it newly calls exactly 3 of 2,931 corpus captions multi-panel -- two of those
#: three being range forms ("a-c, a: W/O DIO") that need a different pattern
#: anyway. Measured and declined: a fifth pattern earning 0.1% is more surface
#: for a false panel than it is worth.
PANEL_MARKER_STYLES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\((?P<l>[a-h])\)", re.IGNORECASE),
    re.compile(r"(?:^|[\s;.])(?P<l>[a-h])\)", re.IGNORECASE),
    re.compile(r"(?:^|[\s;.])(?P<l>[a-h])\s*,\s+[A-Z]"),
    re.compile(r"(?:^|[\s;.])(?P<l>[a-h])\s+[A-Z][a-z]"),
)


def panel_labels(caption: str) -> set[str]:
    """Every distinct panel letter *caption* marks, lowercased.

    Args:
        caption: The figure's caption text.

    Returns:
        The letters found, in no particular order. An empty set means the
        caption marks no panels, which is NOT the same as the figure having
        one panel -- a caption can describe a composite without lettering it,
        and this reads prose only.
    """
    found: set[str] = set()
    for pattern in PANEL_MARKER_STYLES:
        for match in pattern.finditer(caption):
            found.add(match.group("l").lower())
    return found


def caption_names_multiple_panels(caption: str) -> bool:
    """Does this caption describe more than one panel?

    The question a per-figure value has to answer before it may be stored.
    Two or more DISTINCT letters, so a caption mentioning "(a)" repeatedly is
    still one panel.

    Args:
        caption: The figure's caption text.

    Returns:
        True when at least two distinct panel letters appear.
    """
    return len(panel_labels(caption)) > 1


# --- Image-space segmentation -------------------------------------------
#
# WHY GEOMETRY AT ALL, WHEN THE CAPTION LETTERS THE PANELS. Because the letters
# say HOW MANY and WHICH, never WHERE, and a scale bar can only be measured on a
# crop. Letters seed the count, gutters set the boundaries.
#
# Every constant below was measured on the nine-panel GISAXS plate
# ``10_1002_polb_23933-p005-05.png`` (911x460 at images_scale 2.0), the hardest
# real layout in the sample: a 3x3 grid of dense scattering panels beside a
# single tall line plot, with the grid's row gutters INVISIBLE to a global row
# profile because the line plot inks every row of the figure.

#: A pixel is ink when it differs from the page background by more than this
#: many grey levels. MEASURED: the grid's white gutters sit at 255 while panel
#: interiors sit at 0-200; antialiasing along a panel border lands within ~20.
BACKGROUND_TOLERANCE: float = 24.0

#: A row or column counts as empty below this ink fraction. MEASURED: the two
#: rows at the heart of a real 4 px gutter are 0.0000, and the rows that bound
#: it are 0.0606 -- panel-border antialiasing, not content. 0.05 separates them.
INK_THRESHOLD: float = 0.05

#: The narrowest run of empty lines that may separate two panels. MEASURED: the
#: gutters in the reference plate are 2-4 px at images_scale 2.0. Requiring 3
#: silently merged its bottom two grid rows, which is the failure this package
#: exists to avoid -- a merged panel gets ONE scale for two.
MIN_GUTTER_PX: int = 2

#: A band narrower than this fraction of the figure's short side is furniture --
#: an axis-label strip, a lone "(A)" in the margin -- not a panel. MEASURED: the
#: reference plate's "(A)" label column is 27 px of a 460 px side (0.059) and
#: its panels are 133-268 px (0.29-0.58). TUNED against 600 corpus figures whose
#: captions letter their panels: 0.06 agrees with the caption's count on 32% of
#: them, 0.15 on 36%, and above 0.15 the gain is under a point while the
#: UNDER-segmentation rate keeps climbing.
MIN_PANEL_FRACTION: float = 0.15

#: No panel is narrower than this in either direction, whatever the figure's
#: proportions. MEASURED: :data:`MIN_PANEL_FRACTION` alone is scaled by the
#: SHORT side, so a 90x657 strip admitted 5 px "panels" and a 237x910 row of
#: three plots shredded into 25 -- legend rows and the gaps between curves.
#: At images_scale 2.0 this is 24 pt of page, below which nothing carries a
#: readable axis or a measurable bar.
MIN_PANEL_PX: int = 64

#: Above this a "segmentation" is a shredding, and the figure stands whole.
MAX_PANELS: int = 32

#: How far two panels' top edges may differ and still be one ROW. MEASURED:
#: side-by-side panels come back with tops one to two pixels apart, while a row
#: is separated from the next by at least a panel's height -- which cannot be
#: below :data:`MIN_PANEL_PX`. This sits between the two with room on both
#: sides.
ROW_BAND_PX: int = 16

#: A gutter found inside one band is believed only when this fraction of the
#: figure's OTHER bands -- the band itself EXCLUDED, since it trivially agrees
#: with itself and including it makes the whole check vacuous for a two-band
#: figure -- is empty across the same lines. THE LOAD-BEARING GUARD: on the
#: reference plate the grid's true row gutters are shared by 2 and 3 of the 3
#: sibling bands, while the gaps between the line plot's stacked curves are
#: shared by NONE. Bands that do not reach the line ABSTAIN rather than
#: disagree, and a gutter no band can speak to is not believed at all.
#: Without the check that plot shreds into four strips, each then cropped,
#: classified and scaled as though it were a panel.
#:
#: 0.6 AND NOT MORE. Demanding 3 of 3 scores fractionally better against caption
#: counts (36.5% against 35.7% over 600 figures) but breaks the one layout
#: verified by eye: the reference plate's row gutters are shared by 2 of the 3
#: sibling bands, the line plot inking the third, so at 0.75 its nine panels
#: collapse to two. A tenth of a point of corpus-wide agreement is not worth a
#: rule that fails the case we can actually check.
MIN_SIBLING_AGREEMENT: float = 0.6


def ink_mask(image: NDArray[np.floating[Any]] | NDArray[np.integer[Any]]) -> NDArray[np.bool_]:
    """Which pixels of a greyscale figure are content rather than page.

    The background is the MEDIAN OF THE BORDER RING, not of the whole image:
    a dense micrograph is mostly dark, and its median is a picture value. The
    ring is the page the figure was pasted onto.

    Args:
        image: Greyscale figure, 2-D, any numeric dtype.

    Returns:
        A boolean array, True where the pixel is ink.

    Raises:
        ValueError: If *image* is not 2-D.
    """
    arr = np.asarray(image, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"expected a 2-D greyscale image, got shape {arr.shape}")
    if arr.size == 0:
        return np.zeros(arr.shape, dtype=bool)
    ring = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]])
    return np.abs(arr - float(np.median(ring))) > BACKGROUND_TOLERANCE


def _profile(ink: NDArray[np.bool_], box: _Box, axis: str) -> NDArray[np.float64]:
    """Ink fraction per column (``axis="x"``) or per row (``axis="y"``)."""
    left, top, right, bottom = box
    sub = ink[top:bottom, left:right]
    return sub.mean(axis=0 if axis == "x" else 1)


def _empty_runs(profile: NDArray[np.float64]) -> list[tuple[int, int]]:
    """Maximal runs of empty lines, as half-open ``[start, end)`` offsets."""
    empty = profile < INK_THRESHOLD
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, is_empty in enumerate(empty):
        if is_empty and start is None:
            start = index
        elif not is_empty and start is not None:
            if index - start >= MIN_GUTTER_PX:
                runs.append((start, index))
            start = None
    if start is not None and len(empty) - start >= MIN_GUTTER_PX:
        runs.append((start, len(empty)))
    return runs


def _trim(ink: NDArray[np.bool_], box: _Box) -> _Box | None:
    """*box* shrunk to its inked content, or None when it holds none.

    Margins are not gutters: an empty run touching an edge says where the
    figure starts, and treating it as a separator invents a blank panel.
    """
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return None
    cols = _profile(ink, box, "x") >= INK_THRESHOLD
    rows = _profile(ink, box, "y") >= INK_THRESHOLD
    if not cols.any() or not rows.any():
        return None
    return (
        left + int(np.argmax(cols)),
        top + int(np.argmax(rows)),
        right - int(np.argmax(cols[::-1])),
        bottom - int(np.argmax(rows[::-1])),
    )


def _bands(box: _Box, axis: str, gutters: list[tuple[int, int]], min_panel: int) -> list[_Box]:
    """*box* cut along *axis* at *gutters*, dropping slivers below *min_panel*.

    *gutters* are offsets from the box's own start, as :func:`_empty_runs`
    returns them.
    """
    left, top, right, bottom = box
    start = left if axis == "x" else top
    span = (right - left) if axis == "x" else (bottom - top)
    spans: list[tuple[int, int]] = []
    previous = 0
    for gutter_start, gutter_end in gutters:
        if gutter_start - previous >= min_panel:
            spans.append((previous, gutter_start))
        previous = gutter_end
    if span - previous >= min_panel:
        spans.append((previous, span))
    if axis == "x":
        return [(start + s, top, start + e, bottom) for s, e in spans]
    return [(left, start + s, right, start + e) for s, e in spans]


def _line_is_empty(ink: NDArray[np.bool_], box: _Box, axis: str, line: int) -> bool | None:
    """Is figure row/column *line* empty where it crosses *box*?

    ``None`` means the band cannot say: the line falls outside it entirely, as
    a row below a short panel does. That is ABSTENTION, not disagreement, and
    the difference decides real layouts -- a tall column beside a short one
    would otherwise have every one of its gutters outvoted by a neighbour that
    simply does not reach that far down the page.

    ONE LINE, NOT THE WHOLE GUTTER. Sibling bands agree on where a gutter is
    but not on how wide it looks: the reference plate's third grid column reads
    its row gutter as ``[277, 280)`` while the first two read ``[277, 279)``,
    antialiasing along their panel borders differing by a pixel. Asking whether
    the sibling is empty over the FULL interval failed on that one pixel and
    left two of the nine panels merged; asking about the gutter's midpoint is
    the same question without the brittleness.
    """
    left, top, right, bottom = box
    if axis == "y":
        if not top <= line < bottom:
            return None
        row = ink[line, left:right]
        return float(row.mean()) < INK_THRESHOLD if row.size else None
    if not left <= line < right:
        return None
    column = ink[top:bottom, line]
    return float(column.mean()) < INK_THRESHOLD if column.size else None


def segment_panels(
    image: NDArray[np.floating[Any]] | NDArray[np.integer[Any]],
) -> list[PanelBox]:
    """Split a figure into panel boxes along its whitespace gutters.

    Two alternating cuts, never more: the figure is divided along whichever
    axis carries the widest gutter, then each band is divided along the other
    -- the shape a published plate actually has. The depth is fixed rather than
    configurable because MEASURED a third level only ever split a line plot
    along the white gaps between its own stacked curves.

    THE SECOND CUT IS CROSS-CHECKED against the sibling bands
    (:data:`MIN_SIBLING_AGREEMENT`). A gutter that separates panels is empty
    right across the figure; a gap between two stacked curves inside one plot
    is empty only there. Without the check the reference plate's line plot
    shreds into four strips, each of which would then be crop, classified and
    scaled as though it were a panel.

    Args:
        image: Greyscale figure in the PNG's own pixel coordinates.

    Returns:
        At least one box, in reading order. A figure with no gutters -- a lone
        micrograph -- comes back as ONE box with ``method="whole_figure"``,
        which is the truthful answer and the one every consumer already
        handles. Labels are NOT set here; geometry says where, never which.
    """
    ink = ink_mask(image)
    height, width = ink.shape
    whole = [PanelBox(0, 0, width, height, method="whole_figure")]
    content = _trim(ink, (0, 0, width, height))
    if content is None:
        return whole
    min_panel = max(MIN_PANEL_PX, int(MIN_PANEL_FRACTION * min(height, width)))

    gutters = {axis: _empty_runs(_profile(ink, content, axis)) for axis in ("x", "y")}
    widest = {a: max((e - s for s, e in g), default=0) for a, g in gutters.items()}
    primary = "x" if widest["x"] >= widest["y"] else "y"
    secondary = "y" if primary == "x" else "x"

    bands = _bands(content, primary, gutters[primary], min_panel)
    if len(bands) < 2:
        bands = _bands(content, secondary, gutters[secondary], min_panel)
        if len(bands) < 2:
            return whole
        primary, secondary = secondary, primary

    trimmed = [b for b in (_trim(ink, band) for band in bands) if b is not None]
    if len(trimmed) < 2:
        return whole

    boxes: list[_Box] = []
    for index, band in enumerate(trimmed):
        offset = band[1] if secondary == "y" else band[0]
        siblings = [other for position, other in enumerate(trimmed) if position != index]
        agreed: list[tuple[int, int]] = []
        for start, end in _empty_runs(_profile(ink, band, secondary)):
            midpoint = offset + (start + end) // 2
            testimony = [_line_is_empty(ink, other, secondary, midpoint) for other in siblings]
            heard = [verdict for verdict in testimony if verdict is not None]
            if not heard:
                continue
            if sum(heard) / len(heard) >= MIN_SIBLING_AGREEMENT:
                agreed.append((start, end))
        # A band with no agreed gutter IS a panel, whatever its extent across
        # the cut. `_bands` drops spans below `min_panel`, and with no gutters
        # to divide there is exactly one span -- the band itself -- so a band
        # shorter than the floor in the OTHER direction was vanishing entirely
        # instead of standing. MEASURED: two 100x60 panels side by side on a
        # 660x100 strip came back as an unsplit figure. The floor governs how
        # small a piece a CUT may leave behind, not how small a figure may be.
        cells = _bands(band, secondary, agreed, min_panel) or [band]
        boxes.extend(c for c in (_trim(ink, cell) for cell in cells) if c is not None)

    if len(boxes) < 2 or len(boxes) > MAX_PANELS:
        return whole
    return [
        PanelBox(left, top, right, bottom, method="gutter")
        for left, top, right, bottom in _reading_order(boxes)
    ]


def _reading_order(boxes: list[_Box]) -> list[_Box]:
    """Boxes across each row, then down the page.

    NOT a plain sort on ``(top, left)``. Panels in one row rarely share a top
    to the pixel -- MEASURED, two side-by-side panels came back with tops one
    pixel apart -- and a raw sort then puts the right-hand one first. Nothing
    visible goes wrong, because letters are attached to boxes by containment
    rather than by position in this list; what does go wrong is the INDEX,
    which is the position in this list and which is baked into every panel's
    catalog path.

    Rows are grouped within :data:`ROW_BAND_PX`, which sits well above the
    pixel or two that separates panels in a row and well below the
    :data:`MIN_PANEL_PX` that separates one row from the next.
    """
    rows: list[list[_Box]] = []
    for box in sorted(boxes, key=lambda b: b[1]):
        if rows and box[1] - rows[-1][0][1] <= ROW_BAND_PX:
            rows[-1].append(box)
        else:
            rows.append([box])
    return [box for row in rows for box in sorted(row, key=lambda b: b[0])]


def consecutive_from_a(labels: Iterable[str]) -> set[str]:
    """The unbroken run of panel letters starting at ``a``.

    Figures letter their panels consecutively. That is a real, checkable
    property of published work, and it is the only cheap way to tell a real
    marker from a misread one when both are single letters sitting inside a
    panel.

    MEASURED on the reference plate: OCR reads its panels as
    ``{a, b, c, d, e, f, g, h, j}`` -- the ``j`` being a misread of a glyph the
    page prints as ``i``. Nothing about the ``j`` looks wrong on its own: it is
    a single letter, confidently read, inside exactly one box. What gives it
    away is the GAP. There is no ``i``, so ``j`` cannot be the tenth panel of
    a figure whose ninth is missing.

    Args:
        labels: Candidate letters, in any order and any case.

    Returns:
        The letters from ``a`` up to the first gap. Empty when there is no
        ``a`` -- a figure whose lettering does not start at the beginning is
        not one this can reason about, and guessing where it starts would admit
        every misread letter on the page.
    """
    found = {label.lower() for label in labels if len(label) == 1}
    run: set[str] = set()
    for index in range(26):
        letter = chr(ord("a") + index)
        if letter not in found:
            break
        run.add(letter)
    return run


def reconcile_panels(
    boxes: list[PanelBox],
    labels: Iterable[str],
    shape: tuple[int, int],
) -> list[PanelBox]:
    """Settle the caption's panel letters against the geometry's panel boxes.

    The two sources answer different questions and neither answers both. The
    caption says HOW MANY panels there are and what they are called; it never
    says where. The gutters say where; they cannot tell a panel from a plot
    that happens to have a white band across it.

    WHEN THEY DISAGREE, THE FIGURE STANDS WHOLE. A caption naming four panels
    against three boxes means one boundary is wrong, and there is no way to
    know which -- pairing them anyway would hand some panel its neighbour's
    scale bar, a wrong number with a real provenance chain behind it. One
    unsplit figure is recoverable; a mislabelled crop is a fabrication.

    Args:
        boxes: Geometry's answer, from :func:`segment_panels`.
        labels: Panel letters from the caption, e.g. :func:`panel_labels`.
        shape: The figure's ``(height, width)`` in pixels.

    Returns:
        Labelled boxes when the counts agree; the geometry unlabelled when the
        caption names fewer than two panels and so challenges nothing; a single
        ``method="whole_figure"`` box when they disagree.
    """
    height, width = shape
    letters = sorted({label.lower() for label in labels})
    if len(letters) < 2:
        return list(boxes)
    if len(letters) != len(boxes):
        return [PanelBox(0, 0, width, height, method="whole_figure")]
    ordered = sorted(boxes, key=lambda b: (b.top, b.left))
    return [
        PanelBox(b.left, b.top, b.right, b.bottom, label=letter, method=b.method)
        for b, letter in zip(ordered, letters, strict=True)
    ]


#: A panel marker in the figure's own text layer is a single letter and nothing
#: else. MEASURED on the reference plate: its markers arrive as bare ``'a'``,
#: ``'b'``, ``'d'`` ... spans of 8-10 points, beside axis titles like
#: ``'I(qy) [a.u.]'`` and a 900-character caption.
_MARKER_SPAN = re.compile(r"^\(?([a-z])\)?$", re.IGNORECASE)


def label_panels_from_spans(
    boxes: list[PanelBox],
    spans: list[TextSpan],
    frame: FigureFrame,
    allowed: Iterable[str] = (),
) -> list[PanelBox]:
    """Letter the panels from the figure's OWN text layer, by position.

    Better than the caption in every way that matters, when it is there: the
    letters come with coordinates, so a panel gets the letter printed INSIDE
    it rather than the one that happens to fall at its place in reading order.
    MEASURED on the reference plate, whose caption letters its panels as
    ``"a: phi = 1.0; b: phi = 0.750"`` -- a style no caption matcher here reads
    -- while the text layer puts a bare ``'a'`` at the top-right of the first
    grid cell.

    A BOX HOLDING SEVERAL MARKERS IS NOT LABELLED, and that rule is why this is
    safe. On the same plate the tall line plot is a stack of nine curves, each
    annotated with the letter of the grid panel it corresponds to: eight
    single-letter spans inside one box. Taking the first, or the topmost, would
    name that panel after a curve drawn inside it. Ambiguity here is real
    information -- it says this box is not a lettered panel -- and it is kept.

    THE SPANS CAN LIE, and *allowed* is the cheap defence. MEASURED on the
    reference plate: its third grid column reads as ``'J'`` where the page
    prints ``c``, ``f`` and ``i``. That was first written up here as a
    font-encoding fault in the PDF; it is not. Docling runs OCR by default
    (`PdfPipelineOptions.do_ocr` is True), in-figure lettering in published
    papers is usually rasterised into the image, and 34 of that figure's 35
    spans have no counterpart anywhere in the PDF's own text layer. The bad
    letters are OCR misreads. Nothing in the geometry can tell one from a real
    letter, so a caller holding an independent alphabet passes it and a marker
    outside it is ignored.

    Args:
        boxes: Panels from :func:`segment_panels`.
        spans: The figure's text layer, in page points.
        frame: How those points map onto the figure's pixels.
        allowed: The only letters that may be believed. Empty means believe
            any single letter, which is right when there is nothing to check
            against and wrong the moment there is.

    Returns:
        The boxes, in the order given, each labelled where exactly one marker
        sits inside it and no other box claims the same letter.
    """
    permitted = {letter.lower() for letter in allowed}
    claims: dict[int, str] = {}
    for index, box in enumerate(boxes):
        region = (float(box.left), float(box.top), float(box.right), float(box.bottom))
        markers = {
            match.group(1).lower()
            for span in spans_inside(spans, frame, region)
            if (match := _MARKER_SPAN.match(span.text.strip()))
            and (not permitted or match.group(1).lower() in permitted)
        }
        if len(markers) == 1:
            claims[index] = markers.pop()
    contested = {letter for letter in claims.values() if list(claims.values()).count(letter) > 1}
    return [
        PanelBox(
            box.left,
            box.top,
            box.right,
            box.bottom,
            label=("" if claims.get(index) in contested else claims.get(index, "")),
            method=box.method,
        )
        for index, box in enumerate(boxes)
    ]

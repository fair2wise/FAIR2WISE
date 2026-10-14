"""Turning a page-point bounding box into a pixel in the figure's own PNG.

TWO COORDINATE SYSTEMS, AND ONE OF THEM POINTS THE OTHER WAY. docling reports
positions in PDF page points with a ``coord_origin`` that is BOTTOMLEFT for
PDF-native content -- y increases UPWARD, so a box's ``top`` is a LARGER number
than its ``bottom``. The saved figure PNG is indexed the way every image is:
top-left origin, y increasing downward. Converting without noticing flips every
panel vertically, which does not look like an error -- it looks like a figure
whose panels are lettered from the bottom.

MEASURED, on a live parse of `10.1038/ncomms6293`:

    figure bbox   l=111.7 t=731.8 r=482.0 b=410.2   origin=BOTTOMLEFT
    figure size   370.3 x 321.6 points
    saved PNG     741 x 643 px
    ratio         x = 2.001,  y = 1.999      <- images_scale = 2.0, exactly
    spans inside the figure's own bbox: 127 of 128

That ratio is why `images_scale` is stamped beside the bbox rather than assumed:
it is the only thing that makes the two systems commensurable, and a consumer
guessing 1.0 against a 2.0 render puts every derived coordinate at half its true
position with nothing to signal it.

NOT EVERY CHILD IS INSIDE THE FIGURE. docling groups the CAPTION under the
picture too, and a caption sits below the image. Mapped across all six figures
of that paper, 127/128, 120/121, 12/13, 12/12, 16/16 and 120/121 spans land
inside the PNG -- and the single outlier on each multi-span figure is the
caption. So a consumer asking "what text is in this panel" must filter by
POSITION and not merely take the children; :func:`spans_inside` is that filter,
and a caller that skips it will attribute a caption's numbers to a panel.


Adapted from als-computing/forge_rsoxs_analysis src/forge/analysis/figure_extraction/geometry.py (BSD-3-Clause).
"""

from __future__ import annotations

from .types import TextSpan

__all__ = ["FigureFrame", "span_to_pixels", "spans_inside"]


class FigureFrame:
    """The mapping from one figure's page-point box into its PNG pixels.

    Args:
        bbox: ``(left, top, right, bottom)`` in page points, as
            ``BoundingBox.as_dict()`` records them.
        images_scale: Rendered pixels per point.
        coord_origin: ``"BOTTOMLEFT"`` (docling's PDF-native default) or
            ``"TOPLEFT"``.
        size_px: ``(width, height)`` of the saved PNG, when known. Used only to
            clip; absent means no clipping.

    Raises:
        ValueError: If the scale is not positive, or the box is degenerate in
            either axis. A zero-width figure cannot map anything and would
            divide by nothing later.
    """

    def __init__(
        self,
        bbox: tuple[float, float, float, float],
        images_scale: float,
        coord_origin: str = "BOTTOMLEFT",
        size_px: tuple[int, int] | None = None,
    ) -> None:
        left, top, right, bottom = (float(v) for v in bbox)
        if not images_scale > 0:
            raise ValueError(f"images_scale must be positive; got {images_scale!r}")
        if right == left or top == bottom:
            raise ValueError(f"degenerate figure bbox {bbox!r}")
        self.left, self.top, self.right, self.bottom = left, top, right, bottom
        self.images_scale = float(images_scale)
        self.coord_origin = coord_origin.upper()
        self.size_px = size_px

    @property
    def width_pt(self) -> float:
        """Figure width in points."""
        return abs(self.right - self.left)

    @property
    def height_pt(self) -> float:
        """Figure height in points."""
        return abs(self.top - self.bottom)

    def _y_origin_pt(self) -> float:
        """The page-point y of the figure's TOP edge, in its own convention."""
        return (
            max(self.top, self.bottom)
            if self.coord_origin == "BOTTOMLEFT"
            else min(self.top, self.bottom)
        )

    def to_pixels(self, x_pt: float, y_pt: float) -> tuple[float, float]:
        """One page-point position as a pixel in this figure's PNG.

        Args:
            x_pt: Page-point x.
            y_pt: Page-point y, in the frame's own ``coord_origin``.

        Returns:
            ``(x_px, y_px)``, top-left origin, y increasing downward. Values
            outside the image are returned as-is rather than clamped -- a span
            that lands outside is a fact worth seeing, and silently pinning it
            to an edge would put a scale-bar label on the border of a panel it
            never belonged to.
        """
        x_px = (x_pt - min(self.left, self.right)) * self.images_scale
        if self.coord_origin == "BOTTOMLEFT":
            y_px = (self._y_origin_pt() - y_pt) * self.images_scale
        else:
            y_px = (y_pt - self._y_origin_pt()) * self.images_scale
        return x_px, y_px

    def to_points(self, x_px: float, y_px: float) -> tuple[float, float]:
        """One pixel position back in page points, in this frame's convention.

        The exact inverse of :meth:`to_pixels`, and it exists because OCR reads
        the IMAGE: RapidOCR returns boxes in the figure PNG's own pixels, while
        every consumer here works in page points so that text from the PDF and
        text from the pixels can sit in one list and be compared. Converting at
        the boundary keeps one coordinate system downstream instead of two.

        Args:
            x_px: Pixel x, top-left origin.
            y_px: Pixel y, increasing downward.

        Returns:
            ``(x_pt, y_pt)`` with y in the frame's own ``coord_origin``.
        """
        x_pt = x_px / self.images_scale + min(self.left, self.right)
        if self.coord_origin == "BOTTOMLEFT":
            y_pt = self._y_origin_pt() - y_px / self.images_scale
        else:
            y_pt = self._y_origin_pt() + y_px / self.images_scale
        return x_pt, y_pt

    def contains_px(self, x_px: float, y_px: float) -> bool:
        """Is this pixel inside the saved image? False when the size is unknown."""
        if self.size_px is None:
            return False
        w, h = self.size_px
        return 0 <= x_px < w and 0 <= y_px < h


def span_to_pixels(span: TextSpan, frame: FigureFrame) -> tuple[float, float, float, float] | None:
    """A span's box in figure-PNG pixels, or ``None`` if it has no box.

    Args:
        span: The text run.
        frame: The figure it sits in.

    Returns:
        ``(left, top, right, bottom)`` in pixels, top-left origin and already
        ordered so ``top < bottom`` whichever way the source counted. ``None``
        when the span carries no bbox -- which is a real case, not an error:
        a run without provenance still carries usable text.
    """
    if span.bbox is None:
        return None
    l_pt, t_pt, r_pt, b_pt = span.bbox
    x0, y0 = frame.to_pixels(l_pt, t_pt)
    x1, y1 = frame.to_pixels(r_pt, b_pt)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def spans_inside(
    spans: list[TextSpan], frame: FigureFrame, region: tuple[float, float, float, float]
) -> list[TextSpan]:
    """The spans whose CENTRE falls inside a pixel region.

    Centre rather than any overlap: a scale-bar label sitting on a panel
    boundary belongs to the panel it is mostly in, and an overlap rule would
    hand the same label to two panels and let both claim a scale from it.

    Args:
        spans: Candidate runs.
        frame: The figure they sit in.
        region: ``(left, top, right, bottom)`` in figure-PNG pixels.

    Returns:
        The subset whose centre is inside. Spans with no bbox are EXCLUDED --
        the question is positional and they cannot answer it.
    """
    left, top, right, bottom = region
    out: list[TextSpan] = []
    for span in spans:
        box = span_to_pixels(span, frame)
        if box is None:
            continue
        cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
        if left <= cx < right and top <= cy < bottom:
            out.append(span)
    return out

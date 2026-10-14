"""PDF text layer, page geometry and figure localization (V-04), over PyMuPDF.

Source text first: spans come from the PDF's own text layer with exact positions.
All coordinates are normalised to PDF points, **top-left origin**, whatever origin
the parser recorded (Docling boxes are BOTTOMLEFT, PyMuPDF boxes TOPLEFT), and the
original origin is kept in the geometry record. The figure is mapped to render
pixels by a :class:`FigureFrame` whose scale is the parser's ``images_scale`` when
stamped, else the extractor default (recorded as ``scale_source``).

Rendering returns a numpy array for in-process analysis only; it is never returned
from a tool, stored or served (D19).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/figure_text_layer.py
(``FigureTextLayer``, ``locate_figure_on_page``, ``text_layer_for``) (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

from ..literature.parsed_document import BoundingBox, PageInfo, ParsedFigure
from .kernels.geometry import FigureFrame, spans_inside
from .kernels.types import TextSpan
from .results import Box, Uncertainty, VisionUnavailable, box4

DEFAULT_RENDER_SCALE = 2.0
#: Minimum spans inside a figure for its text layer to count as usable.
MIN_TEXT_SPANS = 3


def open_pdf(data: bytes) -> Any:
    """Open PDF bytes with PyMuPDF (lazy import; :class:`VisionUnavailable` when absent)."""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise VisionUnavailable("PyMuPDF", "install the 'figures' extra") from exc
    return fitz.open(stream=data, filetype="pdf")


@dataclass(frozen=True)
class PageGeometry:
    """One page's frame: size in points, rotation, and the fixed output origin/units."""

    page: int
    width_pt: float
    height_pt: float
    rotation: int = 0

    def as_dict(self) -> dict:
        """JSON-safe form."""
        return {"page": self.page, "width": self.width_pt, "height": self.height_pt,
                "rotation": self.rotation, "units": "pt", "origin": "top-left"}


def page_geometry(doc: Any, page_number: int, info: Optional[PageInfo] = None) -> PageGeometry:
    """Geometry of 1-based ``page_number``; PyMuPDF's own size wins over the parser's."""
    page = doc[page_number - 1]
    rect = page.rect
    return PageGeometry(page_number, float(rect.width), float(rect.height),
                        int(page.rotation or (info.rotation if info else 0)))


def to_topleft(bbox: BoundingBox, page_height: float) -> Box:
    """Convert a parser bbox (either origin, points) to ``(x0, y0, x1, y1)`` top-left points."""
    origin = (bbox.coord_origin or "BOTTOMLEFT").upper()
    if bbox.units not in ("pt", "points"):
        raise ValueError(f"unsupported bbox units {bbox.units!r}")
    if origin == "BOTTOMLEFT":
        return box4((bbox.left, page_height - bbox.top, bbox.right, page_height - bbox.bottom))
    if origin == "TOPLEFT":
        return box4((bbox.left, bbox.top, bbox.right, bbox.bottom))
    raise ValueError(f"unsupported bbox origin {bbox.coord_origin!r}")


def page_spans(doc: Any, page_number: int) -> List[TextSpan]:
    """Every text-layer span on a page, verbatim, boxed in top-left points."""
    page = doc[page_number - 1]
    spans: List[TextSpan] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text.strip():
                    spans.append(TextSpan(text, box4(tuple(span["bbox"])), page_number, "text_layer"))
    return spans


def locate_figure_on_page(doc: Any, page_number: int) -> Optional[Box]:
    """Fallback localization from embedded raster images when the parser gave no bbox.

    Returns the union of image boxes only when they form ONE cluster of area; several
    separated images are ambiguous and return ``None`` (the caller records uncertainty).
    """
    page = doc[page_number - 1]
    boxes = [box4(tuple(info["bbox"])) for info in page.get_image_info() if info.get("bbox")]
    boxes = [b for b in boxes if (b[2] - b[0]) > 20 and (b[3] - b[1]) > 20]
    if len(boxes) != 1:
        return None
    return boxes[0]


def render_gray(doc: Any, page_number: int, region: Box, scale: float) -> Any:
    """Render ``region`` (top-left points) to a 2-D uint8 numpy array at ``scale`` px/pt.

    The array is the pipeline's internal working image; it is never returned or stored.
    """
    import fitz
    import numpy as np

    page = doc[page_number - 1]
    clip = fitz.Rect(*region) & page.rect
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, colorspace=fitz.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


@dataclass(frozen=True)
class FigureTextLayer:
    """A figure's placement, frame and the text-layer spans inside it."""

    figure_id: str
    page: PageGeometry
    region: Optional[Box]            # top-left points; None when unlocated
    frame: Optional[FigureFrame]
    scale: float
    scale_source: str                # "parser" | "default"
    region_source: str               # "parser_bbox" | "page_images" | "none"
    source_origin: str               # origin the parser recorded for its bbox ("" when none)
    spans: Tuple[TextSpan, ...] = ()
    outside_spans: int = 0           # page spans NOT inside the figure (caption, body)
    size_px: Optional[Tuple[int, int]] = None
    uncertainties: Tuple[Uncertainty, ...] = field(default_factory=tuple)

    @property
    def sufficient(self) -> bool:
        """True when the text layer has enough spans inside the figure to read."""
        return len(self.spans) >= MIN_TEXT_SPANS

    def geometry(self) -> dict:
        """The explicit geometry record (page frame, source bbox origin, render transform)."""
        return {
            "page": self.page.as_dict(),
            "figure_bbox": list(self.region) if self.region else None,
            "bbox_units": "pt", "bbox_origin": "top-left",
            "source_bbox_origin": self.source_origin or None,
            "region_source": self.region_source,
            "render_frame": {
                "scale_px_per_pt": self.scale, "scale_source": self.scale_source,
                "offset_pt": [self.region[0], self.region[1]] if self.region else None,
                "size_px": list(self.size_px) if self.size_px else None,
                "pixel_origin": "top-left",
            },
        }


def text_layer_for(doc: Any, figure: ParsedFigure, info: Optional[PageInfo] = None,
                   *, default_scale: float = DEFAULT_RENDER_SCALE) -> FigureTextLayer:
    """Locate one parsed figure on its page and collect the text-layer spans inside it."""
    fid = figure.figure_id or f"fig-p{figure.page:03d}-{figure.index:02d}"
    geo = page_geometry(doc, figure.page, info)
    issues: List[Uncertainty] = []
    if geo.rotation:
        issues.append(Uncertainty("page_rotated", f"page rotation {geo.rotation} is not applied", blocking=True))
    region: Optional[Box] = None
    region_source, source_origin = "none", ""
    if figure.bbox is not None:
        try:
            region = to_topleft(figure.bbox, geo.height_pt)
            region_source, source_origin = "parser_bbox", figure.bbox.coord_origin
        except ValueError as exc:
            issues.append(Uncertainty("bbox_unusable", str(exc)))
    if region is None:
        region = locate_figure_on_page(doc, figure.page)
        if region is not None:
            region_source = "page_images"
    if region is None or region[2] - region[0] < 1 or region[3] - region[1] < 1:
        issues.append(Uncertainty("figure_region_unlocated", "figure bbox missing, unusable or ambiguous",
                                  blocking=True))
        return FigureTextLayer(fid, geo, None, None, default_scale, "default", "none", source_origin,
                               uncertainties=tuple(issues))
    scale, scale_source = default_scale, "default"
    if figure.images_scale:
        scale, scale_source = float(figure.images_scale), "parser"
    w_px, h_px = int(round((region[2] - region[0]) * scale)), int(round((region[3] - region[1]) * scale))
    frame = FigureFrame(region, scale, "TOPLEFT", size_px=(w_px, h_px))
    everything = page_spans(doc, figure.page)
    inside = spans_inside(everything, frame, (0.0, 0.0, float(w_px), float(h_px)))
    return FigureTextLayer(fid, geo, region, frame, scale, scale_source, region_source, source_origin,
                           tuple(inside), len(everything) - len(inside), (w_px, h_px), tuple(issues))


def page_text_spans_outside(layer: FigureTextLayer, doc: Any) -> Sequence[TextSpan]:
    """Page spans outside the figure frame (caption/body), for callers needing the surround."""
    if layer.frame is None or layer.size_px is None:
        return ()
    inside = set(layer.spans)
    return [s for s in page_spans(doc, layer.page.page) if s not in inside]

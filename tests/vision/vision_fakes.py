"""Synthetic PDF and ParsedDocument fixtures for the T09 vision tests (reportlab; no real papers)."""

from __future__ import annotations

from io import BytesIO
from typing import Any, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.modules.literature.parsed_document import BoundingBox, PageInfo, ParsedDocument, ParsedFigure, ParsedTable

PAGE_W, PAGE_H = 612.0, 792.0
SHA = "ab" * 32
#: Figure region on the page (top-left points) shared by the multi-panel fixture.
FIG = (40.0, 90.0, 540.0, 330.0)
PANEL_A = (50.0, 100.0, 270.0, 300.0)
PANEL_B = (300.0, 100.0, 520.0, 300.0)
CAPTION_Y = 350.0


def _text(c: Any, x: float, y: float, value: str, size: float, color: tuple[float, float, float] = (0, 0, 0)) -> None:
    """Draw ``value`` with its baseline at top-left ``(x, y)`` (PyMuPDF ``insert_text`` semantics)."""
    c.setFont("Helvetica", size)
    c.setFillColorRGB(*color)
    c.drawString(x, PAGE_H - y, value)


def _textbox(c: Any, rect: tuple[float, float, float, float], value: str, size: float) -> None:
    """Word-wrapped text in a top-left ``rect`` (matches PyMuPDF ``insert_textbox`` line placement)."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    x0, y0, x1, _ = rect
    words, lines, cur = value.split(), [], ""
    for word in words:
        trial = f"{cur} {word}" if cur else word
        if cur and stringWidth(trial, "Helvetica", size) > x1 - x0:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    lines.append(cur)
    for i, line in enumerate(lines):
        _text(c, x0, y0 + 1.075 * size + i * 1.4771 * size, line, size)


def _rect(c: Any, rect: tuple[float, float, float, float], *, fill: tuple[float, float, float] | None = None,
          stroke: tuple[float, float, float] | None = None, width: float = 1.0) -> None:
    x0, y0, x1, y1 = rect
    if fill is not None:
        c.setFillColorRGB(*fill)
    if stroke is not None:
        c.setStrokeColorRGB(*stroke)
        c.setLineWidth(width)
    c.rect(x0, PAGE_H - y1, x1 - x0, y1 - y0, stroke=int(stroke is not None), fill=int(fill is not None))


def _canvas(rotation: int = 0) -> tuple[Any, BytesIO]:
    buf = BytesIO()
    # reportlab swaps MediaBox width/height for a 90/270 page rotation, so pre-swap to keep a 612x792 MediaBox.
    size = (PAGE_H, PAGE_W) if rotation % 180 else (PAGE_W, PAGE_H)
    c = canvas.Canvas(buf, pagesize=size, invariant=1)
    c.setTitle("")
    c.setAuthor("")
    c.setSubject("")
    c.setCreator("")
    return c, buf


def _finish(c: Any, buf: BytesIO, rotation: int = 0) -> bytes:
    if rotation:
        c.setPageRotation(rotation)
    c.showPage()
    c.save()
    return buf.getvalue()


def _ticks(c: Any, panel: Tuple[float, ...], values: Sequence[str], title: str) -> None:
    x0, y0, x1, y1 = panel
    for i, v in enumerate(values):
        _text(c, x0 + 15 + i * 45, y1 - 8, v, 8)
    if title:
        _text(c, x0 + 40, y1 - 24, title, 8)


def multi_panel_pdf(
    caption: str,
    *,
    ticks_a: Sequence[str] = ("0.5", "1.0", "1.5", "2.0"),
    title_a: str = "q (nm^-1)",
    ticks_b: Sequence[str] = ("0.05", "0.10", "0.15", "0.20"),
    title_b: str = "q (A^-1)",
    bar_a: bool = True,
    letters: bool = True,
    rotation: int = 0,
    panels: Sequence[Tuple[float, ...]] = (PANEL_A, PANEL_B),
) -> bytes:
    """One page: two filled panels with tick labels, optional white scale bar in panel A, and a caption."""
    c, buf = _canvas(rotation)
    for rect in panels:
        _rect(c, rect, fill=(0.35, 0.35, 0.35))
    if letters:
        for rect, letter in zip(panels, "abcdefgh"):
            _text(c, rect[0] + 6, rect[1] + 14, letter, 10, (1, 1, 1))
    _ticks(c, panels[0], ticks_a, title_a)
    if len(panels) > 1:
        _ticks(c, panels[1], ticks_b, title_b)
    if bar_a:   # 30 pt white bar, bottom-right quadrant of panel A
        x1, y1 = panels[0][2], panels[0][3]
        _rect(c, (x1 - 45, y1 - 40, x1 - 15, y1 - 37), fill=(1, 1, 1))
    _textbox(c, (40, CAPTION_Y, 560, CAPTION_Y + 80), caption, 9)
    return _finish(c, buf, rotation)


def raster_figure_pdf(caption: str) -> bytes:
    """A flat raster figure with NO text layer inside it (two panels, bar in the first)."""
    c, buf = _canvas()
    w, h = 500, 240
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 239, 209], fill=(90, 90, 90))
    draw.rectangle([260, 10, 489, 209], fill=(110, 110, 110))
    draw.rectangle([180, 190, 229, 192], fill=(255, 255, 255))
    x0, y0, x1, y1 = FIG
    c.drawImage(ImageReader(img), x0, PAGE_H - y1, width=x1 - x0, height=y1 - y0)
    _textbox(c, (40, CAPTION_Y, 560, CAPTION_Y + 80), caption, 9)
    return _finish(c, buf)


def table_pdf(rows: List[List[str]], origin: Tuple[float, float] = (60.0, 420.0),
              cell: Tuple[float, float] = (90.0, 22.0)) -> bytes:
    """A ruled table of text cells."""
    c, buf = _canvas()
    x0, y0 = origin
    cw, ch = cell
    for r, row in enumerate(rows):
        for col, value in enumerate(row):
            rect = (x0 + col * cw, y0 + r * ch, x0 + (col + 1) * cw, y0 + (r + 1) * ch)
            _rect(c, rect, stroke=(0, 0, 0), width=0.8)
            _text(c, rect[0] + 4, rect[3] - 7, value, 9)
    _text(c, x0, y0 - 8, "Table 1. Peak positions", 9)
    return _finish(c, buf)


def bl_bbox(region: Tuple[float, float, float, float], height: float = PAGE_H) -> BoundingBox:
    """A BOTTOMLEFT (Docling-style) bbox for a top-left region in points."""
    x0, y0, x1, y1 = region
    return BoundingBox(x0, height - y0, x1, height - y1, "BOTTOMLEFT", "pt")


def tl_bbox(region: Tuple[float, float, float, float]) -> BoundingBox:
    """A TOPLEFT (PyMuPDF-style) bbox."""
    return BoundingBox(region[0], region[1], region[2], region[3], "TOPLEFT", "pt")


def parsed(
    caption: str,
    *,
    bbox: Optional[BoundingBox] = None,
    scale: Optional[float] = 2.0,
    page: int = 1,
    tables: Tuple[ParsedTable, ...] = (),
    sections: Tuple[Any, ...] = (),
    n_figures: int = 1,
) -> ParsedDocument:
    """A ParsedDocument with one figure (and optional tables/sections) on page 1."""
    figs = tuple(
        ParsedFigure(caption=caption, page=page, index=i, bbox=bbox, images_scale=scale)
        for i in range(n_figures)
    )
    return ParsedDocument(
        source_ref=f"sha256:{SHA}", markdown="", num_pages=1, parser="fake", parser_version="1",
        sections=sections, figures=figs, tables=tables, pages=(PageInfo(1, PAGE_W, PAGE_H, 0),),
    )


CAPTION = ("Figure 1. (a) 2D GIWAXS pattern of the film, q in nm^-1; scale bar 200 nm. "
           "(b) 2D GIWAXS pattern of the annealed film; scale bar 2 um.")

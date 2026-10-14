"""Synthetic PDF and ParsedDocument fixtures for the T09 vision tests (PyMuPDF only; no real papers)."""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

import fitz

from app.modules.literature.parsed_document import BoundingBox, PageInfo, ParsedDocument, ParsedFigure, ParsedTable

PAGE_W, PAGE_H = 612.0, 792.0
SHA = "ab" * 32
#: Figure region on the page (top-left points) shared by the multi-panel fixture.
FIG = (40.0, 90.0, 540.0, 330.0)
PANEL_A = (50.0, 100.0, 270.0, 300.0)
PANEL_B = (300.0, 100.0, 520.0, 300.0)
CAPTION_Y = 350.0


def _ticks(page: Any, panel: Tuple[float, ...], values: Sequence[str], title: str) -> None:
    x0, y0, x1, y1 = panel
    for i, v in enumerate(values):
        page.insert_text((x0 + 15 + i * 45, y1 - 8), v, fontsize=8)
    if title:
        page.insert_text((x0 + 40, y1 - 24), title, fontsize=8)


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
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    for rect in panels:
        page.draw_rect(fitz.Rect(*rect), color=None, fill=(0.35, 0.35, 0.35))
    if letters:
        for rect, letter in zip(panels, "abcdefgh"):
            page.insert_text((rect[0] + 6, rect[1] + 14), letter, fontsize=10, color=(1, 1, 1))
    _ticks(page, panels[0], ticks_a, title_a)
    if len(panels) > 1:
        _ticks(page, panels[1], ticks_b, title_b)
    if bar_a:   # 30 pt white bar, bottom-right quadrant of panel A
        x1, y1 = panels[0][2], panels[0][3]
        page.draw_rect(fitz.Rect(x1 - 45, y1 - 40, x1 - 15, y1 - 37), color=None, fill=(1, 1, 1))
    page.insert_textbox(fitz.Rect(40, CAPTION_Y, 560, CAPTION_Y + 80), caption, fontsize=9)
    if rotation:
        page.set_rotation(rotation)
    return doc.tobytes()


def raster_figure_pdf(caption: str) -> bytes:
    """A flat raster figure with NO text layer inside it (two panels, bar in the first)."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    w, h = 500, 240
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    pix.set_rect(fitz.IRect(10, 10, 240, 210), (90, 90, 90))
    pix.set_rect(fitz.IRect(260, 10, 490, 210), (110, 110, 110))
    pix.set_rect(fitz.IRect(180, 190, 230, 193), (255, 255, 255))
    page.insert_image(fitz.Rect(*FIG), pixmap=pix)
    page.insert_textbox(fitz.Rect(40, CAPTION_Y, 560, CAPTION_Y + 80), caption, fontsize=9)
    return doc.tobytes()


def table_pdf(rows: List[List[str]], origin: Tuple[float, float] = (60.0, 420.0),
              cell: Tuple[float, float] = (90.0, 22.0)) -> bytes:
    """A ruled table of text cells."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    x0, y0 = origin
    cw, ch = cell
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            rect = fitz.Rect(x0 + c * cw, y0 + r * ch, x0 + (c + 1) * cw, y0 + (r + 1) * ch)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 4, rect.y1 - 7), text, fontsize=9)
    page.insert_text((x0, y0 - 8), "Table 1. Peak positions", fontsize=9)
    return doc.tobytes()


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

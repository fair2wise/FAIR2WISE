"""Paper tables with cell geometry and locators, from a ParsedDocument and the PDF text layer.

The parser records each table's page, bbox, shape and cells (Docling's table-structure model gives
each cell its row/column position, text and box); this converts the cells to PDF points with a
top-left origin. A table whose bbox is missing, which has no cells, or whose shape disagrees with the
parser's is reported with an uncertainty rather than guessed. Cell text is source
text (agent-private); cells are bounded in count and length.

New for F2W (donor ``figure_extraction`` has no table-geometry equivalent); uses
:class:`ParsedTable` from the T05 parsed-document contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import logging
from typing import Any

from ..contracts.evidence import EvidenceRef, SourceLocator
from ..literature.parsed_document import ParsedDocument, ParsedTable
from .results import Box, Uncertainty, evidence, locator
from .text_layer import open_pdf, page_geometry, to_topleft

logger = logging.getLogger(__name__)

MAX_CELLS = 400
MAX_CELL_CHARS = 200


@dataclass(frozen=True)
class TableCell:
    """One cell: grid position, verbatim text and page-point box (top-left origin)."""

    row: int
    col: int
    text: str
    bbox: Box

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return {"row": self.row, "col": self.col, "text": self.text[:MAX_CELL_CHARS], "bbox": list(self.bbox)}


@dataclass(frozen=True)
class TableResult:
    """One table with locator, cells and uncertainties."""

    table_id: str
    page: int | None
    caption: str
    n_rows: int
    n_cols: int
    parsed_shape: tuple[int, int]
    locator: SourceLocator
    cells: tuple[TableCell, ...] = ()
    truncated: bool = False
    uncertainties: tuple[Uncertainty, ...] = ()
    evidence: tuple[EvidenceRef, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form (no CSV asset refs or paths)."""
        return {
            "table_id": self.table_id, "page": self.page, "caption": self.caption[:400],
            "n_rows": self.n_rows, "n_cols": self.n_cols, "parsed_shape": list(self.parsed_shape),
            "locator": self.locator.model_dump(exclude_none=True), "bbox_units": "pt", "bbox_origin": "top-left",
            "cells": [c.as_dict() for c in self.cells], "truncated": self.truncated,
            "uncertainties": [u.as_dict() for u in self.uncertainties],
        }


def table_id_of(table: ParsedTable, ordinal: int) -> str:
    """The parser's table id, else ``tbl-p{page:03d}-{ordinal:02d}``."""
    return table.table_id or f"tbl-p{(table.page or 0):03d}-{ordinal:02d}"


def _cells(table: ParsedTable, page_height: float) -> tuple[list[TableCell], int, int]:
    """The parsed cells as :class:`TableCell` (top-left points) plus the grid size they span."""
    cells: list[TableCell] = []
    n_rows = n_cols = 0
    for cell in table.cells:
        n_rows = max(n_rows, cell.row + max(cell.row_span, 1))
        n_cols = max(n_cols, cell.col + max(cell.col_span, 1))
        if cell.bbox is None:
            continue
        try:
            box = to_topleft(cell.bbox, page_height)
        except ValueError as exc:
            logger.debug("table cell box could not be converted; cell skipped: %s", type(exc).__name__)
            continue
        cells.append(TableCell(cell.row, cell.col, cell.text, box))
    return cells, n_rows, n_cols


def extract_tables(
    document: ParsedDocument,
    pdf_bytes: bytes,
    *,
    work_id: str = "",
    table_ids: Sequence[str] = (),
    page: int | None = None,
) -> list[TableResult]:
    """Cells and locators for the selected tables (all when no selector)."""
    doc = open_pdf(pdf_bytes)
    out: list[TableResult] = []
    sha = document.file_sha256
    try:
        infos = {p.number: p for p in document.pages}
        for ordinal, table in enumerate(document.tables, start=1):
            tid = table_id_of(table, ordinal)
            if (table_ids and tid not in table_ids) or (page is not None and table.page != page):
                continue
            unc: list[Uncertainty] = []
            shape = (table.n_rows, table.n_cols)
            if table.page is None or not 1 <= table.page <= len(doc):
                out.append(TableResult(tid, table.page, table.caption, 0, 0, shape,
                                       locator(table.page if table.page and table.page >= 1 else None),
                                       uncertainties=(Uncertainty("table_page_unknown", blocking=True),)))
                continue
            geo = page_geometry(doc, table.page, infos.get(table.page))
            if geo.rotation:
                unc.append(Uncertainty("page_rotated", f"rotation {geo.rotation} is not applied", blocking=True))
            region = None
            if table.bbox is not None:
                try:
                    region = to_topleft(table.bbox, geo.height_pt)
                except ValueError as exc:
                    unc.append(Uncertainty("bbox_unusable", str(exc)))
            if region is None:
                unc.append(Uncertainty("table_region_unlocated", "no usable table bbox", blocking=True))
            loc = locator(table.page, tid, None, region)
            cells: list[TableCell] = []
            n_rows = n_cols = 0
            if region is not None and not geo.rotation:
                cells, n_rows, n_cols = _cells(table, geo.height_pt)
                if not cells:
                    unc.append(Uncertainty("table_cells_unavailable",
                                           "the parser gave no cell boxes for this table", blocking=True))
                elif (n_rows, n_cols) != shape and shape != (0, 0):
                    unc.append(Uncertainty("table_shape_mismatch",
                                           f"parser {shape[0]}x{shape[1]}, cells {n_rows}x{n_cols}"))
            refs = (evidence(work_id, sha, loc, "table", tid),) if work_id else ()
            out.append(TableResult(tid, table.page, table.caption, n_rows, n_cols, shape, loc,
                                   tuple(cells[:MAX_CELLS]), len(cells) > MAX_CELLS, tuple(unc), refs))
        return out
    finally:
        doc.close()


__all__ = ("TableCell", "TableResult", "extract_tables", "table_id_of")

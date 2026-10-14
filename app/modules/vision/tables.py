"""Paper tables with cell geometry and locators, from a ParsedDocument and the PDF text layer.

The parser records each table's page, bbox and shape; this adds the cells: text and a box per
cell, in PDF points with a top-left origin, found by PyMuPDF's ruled-table detector inside the
parsed bbox. A table whose bbox is missing, whose detector finds nothing/ambiguity, or whose shape
disagrees with the parser's is reported with an uncertainty rather than guessed. Cell text is source
text (agent-private); cells are bounded in count and length.

New for F2W (donor ``figure_extraction`` has no table-geometry equivalent); uses
:class:`ParsedTable` from the T05 parsed-document contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..contracts.evidence import EvidenceRef, SourceLocator
from ..literature.parsed_document import ParsedDocument, ParsedTable
from .results import Box, Uncertainty, box4, evidence, locator
from .text_layer import open_pdf, page_geometry, to_topleft

MAX_CELLS = 400
MAX_CELL_CHARS = 200


@dataclass(frozen=True)
class TableCell:
    """One cell: grid position, verbatim text and page-point box (top-left origin)."""

    row: int
    col: int
    text: str
    bbox: Box

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe form."""
        return {"row": self.row, "col": self.col, "text": self.text[:MAX_CELL_CHARS], "bbox": list(self.bbox)}


@dataclass(frozen=True)
class TableResult:
    """One table with locator, cells and uncertainties."""

    table_id: str
    page: Optional[int]
    caption: str
    n_rows: int
    n_cols: int
    parsed_shape: Tuple[int, int]
    locator: SourceLocator
    cells: Tuple[TableCell, ...] = ()
    truncated: bool = False
    uncertainties: Tuple[Uncertainty, ...] = ()
    evidence: Tuple[EvidenceRef, ...] = field(default_factory=tuple)

    def as_dict(self) -> Dict[str, Any]:
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


def _cells(found: Any) -> Tuple[List[TableCell], int, int]:
    grid = found.extract()
    cells: List[TableCell] = []
    for r, row in enumerate(found.rows):
        for c, bb in enumerate(row.cells):
            if bb is None:
                continue
            text = grid[r][c] if r < len(grid) and c < len(grid[r]) and grid[r][c] is not None else ""
            cells.append(TableCell(r, c, str(text), box4(tuple(bb))))
    return cells, int(found.row_count), int(found.col_count)


def extract_tables(
    document: ParsedDocument,
    pdf_bytes: bytes,
    *,
    work_id: str = "",
    table_ids: Sequence[str] = (),
    page: Optional[int] = None,
) -> List[TableResult]:
    """Cells and locators for the selected tables (all when no selector)."""
    doc = open_pdf(pdf_bytes)
    out: List[TableResult] = []
    sha = document.file_sha256
    try:
        infos = {p.number: p for p in document.pages}
        for ordinal, table in enumerate(document.tables, start=1):
            tid = table_id_of(table, ordinal)
            if (table_ids and tid not in table_ids) or (page is not None and table.page != page):
                continue
            unc: List[Uncertainty] = []
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
            cells: List[TableCell] = []
            n_rows = n_cols = 0
            if region is not None and not geo.rotation:
                import fitz

                finder = doc[table.page - 1].find_tables(clip=fitz.Rect(*region))
                if len(finder.tables) != 1:
                    unc.append(Uncertainty("table_detection_ambiguous",
                                           f"{len(finder.tables)} ruled tables found inside the parsed bbox",
                                           blocking=True))
                else:
                    cells, n_rows, n_cols = _cells(finder.tables[0])
                    if (n_rows, n_cols) != shape and shape != (0, 0):
                        unc.append(Uncertainty("table_shape_mismatch",
                                               f"parser {shape[0]}x{shape[1]}, cells {n_rows}x{n_cols}"))
            refs = (evidence(work_id, sha, loc, "table", tid),) if work_id else ()
            out.append(TableResult(tid, table.page, table.caption, n_rows, n_cols, shape, loc,
                                   tuple(cells[:MAX_CELLS]), len(cells) > MAX_CELLS, tuple(unc), refs))
        return out
    finally:
        doc.close()


__all__ = ("TableCell", "TableResult", "extract_tables", "table_id_of")

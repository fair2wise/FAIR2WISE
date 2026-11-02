"""Parser-independent model of one parse of one PDF: sections, figures, tables and their geometry (L-11).

These are observations of what a parser *read*, not scientific entities: a
parsed package is not a validated publication. Coordinates are kept exactly as
the parser reports them, with their origin (``TOPLEFT`` for text spans,
``BOTTOMLEFT`` for PDF-native Docling boxes) and units (PDF points), plus each
page's size, so an overlay or crop can be reproduced without guessing.

Figure images, figure text-layer spans and table CSVs are assets in the
content-addressed store; the document refers to them by ``sha256:`` ref
(``asset_ref``, ``spans_ref``, ``data_ref``). ``path`` fields are transient,
filled only while a parse is in progress or by the legacy dict adapter.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/parsed_document.py
(``without_embedded_images``, ``BoundingBox``, ``ParsedFigure``,
``figure_geometry_props``, ``ParsedSection``, ``ParsedTable``, ``ParsedDocument``,
``as_legacy_dict``, ``document_from_dict``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any, cast

#: Version of the serialized document record.
DOCUMENT_SCHEMA_VERSION = 1
#: Bound on one section's stored text (donor value; reference lists need the headroom).
SECTION_TEXT_MAX_CHARS = 200_000


def without_embedded_images(text: str) -> str:
    """Replace base64 image data URIs in prose with ``image-artifact`` (pixels live as assets)."""
    return re.sub(
        r"data:image/[a-z0-9.+-]+(?:;[a-z0-9=.+-]+)*;base64,[a-z0-9+/=\r\n]+",
        "image-artifact",
        text,
        flags=re.IGNORECASE,
    )


@dataclass(frozen=True)
class BoundingBox:
    """Where an element sits on its page, in the parser's own coordinates (stored as given)."""

    left: float
    top: float
    right: float
    bottom: float
    coord_origin: str = "BOTTOMLEFT"
    units: str = "pt"

    def as_dict(self) -> dict[str, Any]:
        """Flat form (donor keys plus ``units``)."""
        return {
            "l": self.left,
            "t": self.top,
            "r": self.right,
            "b": self.bottom,
            "origin": self.coord_origin,
            "units": self.units,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> BoundingBox | None:
        """Inverse of :meth:`as_dict`; ``None`` passes through."""
        if not value:
            return None
        return cls(
            float(value["l"]),
            float(value["t"]),
            float(value["r"]),
            float(value["b"]),
            str(value.get("origin", "BOTTOMLEFT")),
            str(value.get("units", "pt")),
        )


@dataclass(frozen=True)
class PageInfo:
    """One page's size in points and rotation, the frame every bbox on it refers to."""

    number: int
    width: float
    height: float
    rotation: int = 0


@dataclass(frozen=True)
class ParsedFigure:
    """One usable figure: caption, page, geometry and asset refs.

    ``figure_id`` is deterministic (``fig-p{page:03d}-{index:02d}``) so a re-parse
    with the same parser version maps onto the same identifier.
    """

    caption: str
    page: int
    index: int
    bbox: BoundingBox | None = None
    images_scale: float | None = None
    asset_ref: str = ""
    spans_ref: str = ""
    caption_bbox: BoundingBox | None = None
    figure_id: str = ""
    path: Path | None = None
    spans_path: Path | None = None


@dataclass(frozen=True)
class ParsedSection:
    """One section. ``page`` is ``None`` when unknown (never a claimed 0)."""

    name: str
    text: str
    page: int | None = None
    level: int = 0
    structural_id: str = ""
    bbox: BoundingBox | None = None
    end_page: int | None = None


@dataclass(frozen=True)
class ParsedTableCell:
    """One table cell as the parser located it.

    Attributes:
        row: Zero-based first row the cell occupies.
        col: Zero-based first column the cell occupies.
        text: The cell text verbatim.
        row_span: Rows covered (at least 1).
        col_span: Columns covered (at least 1).
        bbox: The cell box in its OWN origin and units (``bbox.coord_origin``); ``None`` when the
            parser gave no geometry for the cell.
    """

    row: int
    col: int
    text: str
    row_span: int = 1
    col_span: int = 1
    bbox: BoundingBox | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return {
            "row": self.row,
            "col": self.col,
            "text": self.text,
            "row_span": self.row_span,
            "col_span": self.col_span,
            **({"bbox": self.bbox.as_dict()} if self.bbox else {}),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ParsedTableCell:
        """Inverse of :meth:`as_dict`."""
        return cls(
            int(value["row"]),
            int(value["col"]),
            str(value.get("text", "")),
            int(value.get("row_span", 1)),
            int(value.get("col_span", 1)),
            BoundingBox.from_dict(value.get("bbox")),
        )


@dataclass(frozen=True)
class ParsedTable:
    """One table with its CSV asset and geometry."""

    page: int | None
    n_rows: int
    n_cols: int
    data_ref: str = ""
    caption: str = ""
    bbox: BoundingBox | None = None
    table_id: str = ""
    data_path: Path | None = None
    #: Cells with text and boxes (empty when the parser gives no cell geometry).
    cells: tuple[ParsedTableCell, ...] = ()


def config_hash(config: Mapping[str, Any]) -> str:
    """Short stable hash of a parser configuration."""
    return hashlib.sha256(json.dumps(dict(config), sort_keys=True, default=str).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class ParsedDocument:
    """Everything one parse of one PDF produced.

    Attributes:
        source_ref: Content address of the parsed PDF bytes.
        markdown: Full text as markdown (headings as ``#`` lines).
        num_pages: Page count.
        parser / parser_version / parser_config: Which parser, which version, which settings.
        sections / figures / tables / pages: What was found, with geometry.
        timings: Parse timings (seconds).
        pdf_path: Transient local path while parsing; never serialized.
    """

    source_ref: str
    markdown: str
    num_pages: int
    parser: str
    parser_version: str = ""
    parser_config: dict[str, Any] = field(default_factory=dict)
    sections: tuple[ParsedSection, ...] = ()
    figures: tuple[ParsedFigure, ...] = ()
    tables: tuple[ParsedTable, ...] = ()
    pages: tuple[PageInfo, ...] = ()
    timings: dict[str, float] = field(default_factory=dict)
    schema_version: int = DOCUMENT_SCHEMA_VERSION
    pdf_path: Path | None = None

    @property
    def file_sha256(self) -> str:
        """Hex digest of the source (donor field name)."""
        return self.source_ref.split(":", 1)[-1]

    @property
    def parser_key(self) -> str:
        """Versioned output namespace: ``<parser>-<version>-<config hash>``."""
        return f"{self.parser}-{self.parser_version or 'unknown'}-{config_hash(self.parser_config)}"

    def section_names(self) -> tuple[str, ...]:
        """Section names in document order."""
        return tuple(s.name for s in self.sections)

    def to_record(self) -> dict[str, Any]:
        """Serialized form with asset refs only (no local paths)."""
        return {
            "schema_version": self.schema_version,
            "source_ref": self.source_ref,
            "markdown": self.markdown,
            "num_pages": self.num_pages,
            "parser": self.parser,
            "parser_version": self.parser_version,
            "parser_config": self.parser_config,
            "parser_key": self.parser_key,
            "timings": self.timings,
            "pages": [
                {"number": p.number, "width": p.width, "height": p.height, "rotation": p.rotation}
                for p in self.pages
            ],
            "sections": [
                {
                    "name": s.name,
                    "text": s.text[:SECTION_TEXT_MAX_CHARS],
                    "page": s.page,
                    "level": s.level,
                    "structural_id": s.structural_id or f"section-{i:05d}",
                    "end_page": s.end_page,
                    **({"bbox": s.bbox.as_dict()} if s.bbox else {}),
                }
                for i, s in enumerate(self.sections)
            ],
            "figures": [
                {
                    "figure_id": f.figure_id,
                    "index": f.index,
                    "caption": f.caption,
                    "page": f.page,
                    "asset_ref": f.asset_ref,
                    "spans_ref": f.spans_ref,
                    **({"bbox": f.bbox.as_dict()} if f.bbox else {}),
                    **({"caption_bbox": f.caption_bbox.as_dict()} if f.caption_bbox else {}),
                    **({"images_scale": f.images_scale} if f.images_scale else {}),
                }
                for f in self.figures
            ],
            "tables": [
                {
                    "table_id": t.table_id,
                    "page": t.page,
                    "n_rows": t.n_rows,
                    "n_cols": t.n_cols,
                    "data_ref": t.data_ref,
                    "caption": t.caption,
                    **({"bbox": t.bbox.as_dict()} if t.bbox else {}),
                    **({"cells": [c.as_dict() for c in t.cells]} if t.cells else {}),
                }
                for t in self.tables
            ],
        }

    def as_legacy_dict(self) -> dict[str, Any]:
        """The donor's legacy dict contract (``markdown``, ``figures`` ...), for unmigrated callers.

        Asset locations are the transient path when present, else the store ref.
        """
        record = self.to_record()
        figures = []
        for fig, row in zip(self.figures, record["figures"]):
            figures.append(
                {
                    **row,
                    "path": str(fig.path) if fig.path else fig.asset_ref,
                    **({"spans_path": str(fig.spans_path)} if fig.spans_path else {}),
                }
            )
        tables = []
        for table, row in zip(self.tables, record["tables"]):
            tables.append(
                {**row, "data_path": str(table.data_path) if table.data_path else (table.data_ref or None)}
            )
        return {
            "markdown": self.markdown,
            "sections": record["sections"],
            "num_pages": self.num_pages,
            "num_images": len(self.figures),
            "image_paths": [f["path"] for f in figures],
            "figures": figures,
            "tables": tables,
            "pdf_path": str(self.pdf_path) if self.pdf_path else self.source_ref,
            "parser": self.parser,
            "parser_version": self.parser_version,
            "file_sha256": self.file_sha256,
            "parser_config": self.parser_config,
            "timings": self.timings,
        }

    def without_paths(self) -> ParsedDocument:
        """Copy with every transient local path cleared."""
        return replace(
            self,
            pdf_path=None,
            figures=tuple(replace(f, path=None, spans_path=None) for f in self.figures),
            tables=tuple(replace(t, data_path=None) for t in self.tables),
        )


def figure_geometry_props(figure: Mapping[str, Any]) -> dict[str, Any]:
    """Geometry keys a figure record should carry (only those present; bbox JSON-encoded)."""
    props: dict[str, Any] = {}
    if figure.get("bbox"):
        props["bbox"] = json.dumps(figure["bbox"], sort_keys=True)
    if figure.get("images_scale"):
        props["images_scale"] = float(figure["images_scale"])
    for key in ("spans_ref", "spans_path"):
        if figure.get(key):
            props[key] = str(figure[key])
    return props


def document_from_dict(data: Mapping[str, Any]) -> ParsedDocument:
    """Round-trip :meth:`ParsedDocument.to_record`, retaining geometry.

    Raises:
        ValueError: On an unsupported schema version.
    """
    if int(data.get("schema_version", DOCUMENT_SCHEMA_VERSION)) != DOCUMENT_SCHEMA_VERSION:
        raise ValueError("unsupported parsed-document schema")
    return ParsedDocument(
        source_ref=str(data["source_ref"]),
        markdown=str(data["markdown"]),
        num_pages=int(data["num_pages"]),
        parser=str(data["parser"]),
        parser_version=str(data.get("parser_version", "")),
        parser_config=dict(data.get("parser_config") or {}),
        timings=dict(data.get("timings") or {}),
        pages=tuple(
            PageInfo(int(p["number"]), float(p["width"]), float(p["height"]), int(p.get("rotation", 0)))
            for p in data.get("pages", [])
        ),
        sections=tuple(
            ParsedSection(
                name=s["name"],
                text=s["text"],
                page=s.get("page"),
                level=int(s.get("level", 0)),
                structural_id=s.get("structural_id", ""),
                bbox=BoundingBox.from_dict(s.get("bbox")),
                end_page=s.get("end_page"),
            )
            for s in data.get("sections", [])
        ),
        figures=tuple(
            ParsedFigure(
                caption=f.get("caption", ""),
                page=int(f.get("page", 0)),
                index=int(f.get("index", i)),
                bbox=BoundingBox.from_dict(f.get("bbox")),
                images_scale=f.get("images_scale"),
                asset_ref=f.get("asset_ref", ""),
                spans_ref=f.get("spans_ref", ""),
                caption_bbox=BoundingBox.from_dict(f.get("caption_bbox")),
                figure_id=f.get("figure_id", ""),
            )
            for i, f in enumerate(data.get("figures", []))
        ),
        tables=tuple(
            ParsedTable(
                page=t.get("page"),
                n_rows=int(t.get("n_rows", 0)),
                n_cols=int(t.get("n_cols", 0)),
                data_ref=t.get("data_ref", ""),
                caption=t.get("caption", ""),
                bbox=BoundingBox.from_dict(t.get("bbox")),
                table_id=t.get("table_id", ""),
                cells=tuple(ParsedTableCell.from_dict(c) for c in t.get("cells", [])),
            )
            for t in data.get("tables", [])
        ),
    )


def extract_sections(markdown: str) -> dict[str, str]:
    """Split markdown into named sections on ``#`` headings, ignoring fenced code (donor ``_extract_sections``).

    Names are lowercased; a repeated name gets `` [2]``, `` [3]`` ...
    """
    sections: dict[str, str] = {}
    current, content, in_fence = "preamble", cast(list[str], []), False

    def flush() -> None:
        text = "\n".join(content).strip()
        if not text:
            return
        key, occurrence = current, 2
        while key in sections:
            key = f"{current} [{occurrence}]"
            occurrence += 1
        sections[key] = text

    for line in markdown.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        header = None if in_fence else re.match(r"^(#{1,6})\s+(.+)", line)
        if header:
            flush()
            current, content = header.group(2).strip().lower(), []
        else:
            content.append(line)
    flush()
    return sections


__all__ = (
    "BoundingBox",
    "DOCUMENT_SCHEMA_VERSION",
    "PageInfo",
    "ParsedDocument",
    "ParsedFigure",
    "ParsedSection",
    "ParsedTable",
    "ParsedTableCell",
    "SECTION_TEXT_MAX_CHARS",
    "config_hash",
    "document_from_dict",
    "extract_sections",
    "figure_geometry_props",
    "without_embedded_images",
)

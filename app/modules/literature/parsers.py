"""Pluggable PDF parsers behind one protocol (L-10/L-12).

:class:`PublicationParser` is the seam: a parser turns a local PDF into a
:class:`~.parsed_document.ParsedDocument` and writes any figure/table files into
a work directory. The parse service stores those files as assets and versions
the output by ``parser-version-config``.

Only :class:`DoclingParser` ships. Docling (MIT) reads the document structure: headings with level
and page, figures with bbox, caption, render scale and the text layer inside the figure, and tables
with caption, bbox, CSV and per-cell boxes. PyMuPDF (AGPL-3.0) is gone from this repository (H1 E2,
docs/migration/reviews/H0/license_dependency_audit.md); plain page text, metadata and rendering come
from :mod:`.pdf_reader` (pypdfium2). Another engine slots in as another :class:`PublicationParser`
without changing callers. Coordinates keep the engine's origin: Docling items are BOTTOMLEFT, table
cells carry their own origin, figure text spans are TOPLEFT; all in points.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/paper_reading.py (``PublicationParser``,
``_is_usable_figure``, ``_parser_version``, ``_sections_from_markdown``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
import csv
import io
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Protocol, cast, runtime_checkable

from .parsed_document import (
    BoundingBox,
    PageInfo,
    ParsedDocument,
    ParsedFigure,
    ParsedSection,
    ParsedTable,
    ParsedTableCell,
    extract_sections,
    without_embedded_images,
)
from .pdf_reader import open_pdf

logger = logging.getLogger(__name__)

PARSER_DOCLING = "docling"
#: Bumped whenever this adapter's output changes for the same input and library version.
DOCLING_ADAPTER_VERSION = 1
#: Directory holding Docling's layout and table model weights (never in the repo; images fetch them at
#: build time, see the Dockerfile ``docling-models`` stage). ``DOCLING_ARTIFACTS_PATH`` is Docling's own name.
DOCLING_ARTIFACTS_ENV = "F2W_DOCLING_ARTIFACTS_PATH"


@runtime_checkable
class PublicationParser(Protocol):
    """Turn a PDF into a :class:`ParsedDocument`.

    Raise ``ImportError`` when the parser's library is absent (the service tries
    the next one) and any other exception only when the file cannot be parsed.
    An empty section list is a legitimate answer, not a failure.
    """

    name: str

    def version(self) -> str:
        """Installed library version, or ``""``."""
        ...

    def config(self) -> dict[str, Any]:
        """Settings that change output; part of the versioned output key."""
        ...

    def parse(self, pdf_path: Path, work_dir: Path, *, source_ref: str) -> ParsedDocument:
        """Parse ``pdf_path``, writing figure/table files into ``work_dir``."""
        ...


def library_version(distribution: str) -> str:
    """Installed version of ``distribution``, or ``""`` (donor ``_parser_version``)."""
    try:
        from importlib.metadata import version

        return version(distribution)
    except Exception:  # noqa: BLE001 - an absent version must not fail a parse
        return ""


def is_usable_figure_image(png: bytes, *, min_short_side: int = 120, min_stddev: float = 1.0) -> bool:
    """Whether an image is a figure rather than page furniture (donor ``_is_usable_figure`` thresholds).

    Uses Pillow when installed for the blank check; without it only the size floor applies.
    """
    try:
        from PIL import Image, ImageStat
    except ImportError:  # pragma: no cover - Pillow ships with the API extras
        return True
    try:
        with Image.open(io.BytesIO(png)) as handle:
            if min(handle.size) < min_short_side:
                return False
            return ImageStat.Stat(handle.convert("L")).stddev[0] >= min_stddev
    except Exception:  # noqa: BLE001 - an unreadable image is not a figure
        return False


def _bbox(rect: Sequence[float]) -> BoundingBox:
    x0, y0, x1, y1 = (float(v) for v in rect)
    return BoundingBox(
        left=round(x0, 2),
        top=round(y0, 2),
        right=round(x1, 2),
        bottom=round(y1, 2),
        coord_origin="TOPLEFT",
        units="pt",
    )


def _inside(inner: Sequence[float], outer: Sequence[float], *, pad: float = 1.0) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] - pad <= cx <= outer[2] + pad and outer[1] - pad <= cy <= outer[3] + pad


def _docling_bbox(bbox: Any, *, origin: str | None = None) -> BoundingBox:
    """A Docling ``BoundingBox`` as ours, keeping its own origin (BOTTOMLEFT or TOPLEFT) and units."""
    coord = str(getattr(bbox.coord_origin, "value", bbox.coord_origin) or "BOTTOMLEFT")
    return BoundingBox(
        left=round(float(bbox.l), 2),
        top=round(float(bbox.t), 2),
        right=round(float(bbox.r), 2),
        bottom=round(float(bbox.b), 2),
        coord_origin=origin or coord,
        units="pt",
    )


def _docling_page_of(item: Any) -> tuple[int | None, Any | None]:
    """First provenance page number and box of a Docling item (``(None, None)`` without provenance)."""
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None, None
    return int(prov[0].page_no), prov[0].bbox


class DoclingParser:
    """Docling structure reader: headings with level, figures, tables with cells, page geometry.

    Docling (MIT) replaces PyMuPDF (AGPL-3.0) as the document structure engine (H1 E2, T05). Layout and
    table models run on CPU with OCR off (the PDFs carry a text layer); figure text spans and page
    rotation come from the PDF itself through :mod:`.pdf_reader`. Bounding boxes keep Docling's origin
    (BOTTOMLEFT for items, per-cell origin for table cells); ``vision.text_layer.to_topleft`` converts.

    Args:
        artifacts_path: Directory with Docling's model weights. Defaults to ``F2W_DOCLING_ARTIFACTS_PATH``,
            then Docling's own default (which downloads on first use, not allowed with ``HF_HUB_OFFLINE``).
        images_scale: Rendering scale for figure crops (pixels per point).
        min_figure_side: Short-side pixel floor for a usable figure.
        do_table_structure: Run the table-structure model (cells with boxes).
        converter: A ready ``DocumentConverter`` (tests inject one; ``None`` builds Docling's).
    """

    name = PARSER_DOCLING

    def __init__(
        self,
        *,
        artifacts_path: str | Path | None = None,
        images_scale: float = 2.0,
        min_figure_side: int = 120,
        do_table_structure: bool = True,
        converter: Any | None = None,
    ) -> None:
        self.artifacts_path = artifacts_path
        self.images_scale = images_scale
        self.min_figure_side = min_figure_side
        self.do_table_structure = do_table_structure
        self._converter_instance = converter

    def version(self) -> str:
        """Installed Docling version."""
        return library_version("docling")

    def config(self) -> dict[str, Any]:
        """Output-changing settings (part of the versioned key)."""
        return {
            "adapter_version": DOCLING_ADAPTER_VERSION,
            "images_scale": self.images_scale,
            "min_figure_side": self.min_figure_side,
            "do_ocr": False,
            "do_table_structure": self.do_table_structure,
        }

    def _converter(self) -> Any:
        if self._converter_instance is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            artifacts = self.artifacts_path or os.environ.get(DOCLING_ARTIFACTS_ENV) or None
            options = PdfPipelineOptions(
                artifacts_path=str(artifacts) if artifacts else None,
                do_ocr=False,
                do_table_structure=self.do_table_structure,
                generate_picture_images=True,
                generate_page_images=False,
                images_scale=self.images_scale,
            )
            self._converter_instance = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        return self._converter_instance

    def parse(self, pdf_path: Path, work_dir: Path, *, source_ref: str) -> ParsedDocument:
        """Parse with Docling (raises ``ImportError`` when it is not installed).

        Raises:
            ImportError: Docling is not installed.
            ValueError: The PDF could not be converted (Docling's failure status).
        """
        started = time.monotonic()
        converter = self._converter()
        result = converter.convert(str(pdf_path))
        status = getattr(getattr(result, "status", None), "value", getattr(result, "status", None))
        if status not in (None, "success", "partial_success"):
            raise ValueError(f"docling could not convert the PDF (status {status})")
        return self.document_from_docling(
            result.document,
            pdf_path=pdf_path,
            work_dir=work_dir,
            source_ref=source_ref,
            conversion_seconds=time.monotonic() - started,
        )

    def document_from_docling(
        self,
        doc: Any,
        *,
        pdf_path: Path,
        work_dir: Path,
        source_ref: str,
        conversion_seconds: float = 0.0,
    ) -> ParsedDocument:
        """Map a ``DoclingDocument`` onto a :class:`ParsedDocument` (no model inference happens here)."""
        work_dir.mkdir(parents=True, exist_ok=True)
        stem = source_ref.split(":", 1)[-1][:16]
        reader = open_pdf(pdf_path) if pdf_path and Path(pdf_path).is_file() else None
        try:
            pages = self._pages(doc, reader)
            sections, markdown_lines = self._sections(doc)
            figures = self._figures(doc, reader, pages, work_dir, stem)
            tables = self._tables(doc, work_dir, stem)
        finally:
            if reader is not None:
                reader.close()
        markdown = without_embedded_images(doc.export_to_markdown()) if hasattr(doc, "export_to_markdown") else ""
        return ParsedDocument(
            source_ref=source_ref,
            markdown=markdown,
            num_pages=len(pages),
            parser=self.name,
            parser_version=self.version(),
            parser_config=self.config(),
            sections=tuple(sections),
            figures=tuple(figures),
            tables=tuple(tables),
            pages=tuple(pages),
            timings={"parser_conversion_s": round(conversion_seconds, 3)},
            pdf_path=pdf_path,
        )

    # -- pages and text ---------------------------------------------------------------------------

    @staticmethod
    def _pages(doc: Any, reader: Any | None) -> list[PageInfo]:
        pages: list[PageInfo] = []
        for number in sorted(doc.pages):
            size = doc.pages[number].size
            rotation = 0
            if reader is not None and 0 < number <= len(reader):
                rotation = reader[number - 1].rotation
            pages.append(PageInfo(int(number), float(size.width), float(size.height), int(rotation)))
        return pages

    @staticmethod
    def _sections(doc: Any) -> tuple[list[ParsedSection], list[str]]:
        """Sections from Docling's reading order: headings open a section, body text joins it."""
        from docling_core.types.doc.labels import DocItemLabel

        skip = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}
        sections: list[ParsedSection] = []
        lines: list[str] = []
        name, level, page, bbox, parts, last_page = "preamble", 0, None, None, cast(list[str], []), None

        def flush() -> None:
            text = "\n".join(parts).strip()
            if text or name != "preamble":
                sections.append(
                    ParsedSection(
                        name=name,
                        text=text,
                        page=page,
                        level=level,
                        structural_id=f"section-{len(sections):05d}",
                        bbox=bbox,
                        end_page=last_page,
                    )
                )

        for item, _depth in doc.iterate_items():
            label = getattr(item, "label", None)
            text = str(getattr(item, "text", "") or "").strip()
            if not text or label in skip:
                continue
            item_page, item_box = _docling_page_of(item)
            if label == DocItemLabel.TITLE:  # the title is front matter, not a section
                lines.append(f"# {text}")
                continue
            if label == DocItemLabel.SECTION_HEADER:
                flush()
                name, level, page = text, int(getattr(item, "level", 1) or 1), item_page
                bbox = _docling_bbox(item_box) if item_box is not None else None
                parts, last_page = [], item_page
                lines.append(f"{'#' * min(level + 1, 6)} {text}")
                continue
            if page is None:
                page = item_page
            lines.append(text)
            parts.append(text)
            last_page = item_page or last_page
        flush()
        return sections, lines

    # -- figures and tables -----------------------------------------------------------------------

    @staticmethod
    def _caption(item: Any, doc: Any) -> tuple[str, BoundingBox | None]:
        try:
            text = str(item.caption_text(doc) or "").strip()
        except Exception:  # noqa: BLE001 - a malformed caption reference must not lose the figure
            logger.debug("caption text unreadable", exc_info=True)
            return "", None
        box = None
        for ref in getattr(item, "captions", None) or []:
            try:
                _, cap_box = _docling_page_of(ref.resolve(doc))
            except Exception:  # noqa: BLE001
                logger.debug("caption box unresolved", exc_info=True)
                continue
            if cap_box is not None:
                box = _docling_bbox(cap_box)
                break
        return text, box

    def _figures(self, doc: Any, reader: Any | None, pages: list[PageInfo], work_dir: Path, stem: str) -> list[ParsedFigure]:
        out: list[ParsedFigure] = []
        height_of = {p.number: p.height for p in pages}
        per_page: Counter[int] = Counter()
        for picture in doc.pictures:
            number, box = _docling_page_of(picture)
            if number is None or box is None:
                continue
            index = per_page[number]
            per_page[number] += 1
            try:
                image = picture.get_image(doc)
                if image is None:
                    continue
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="PNG")
                png, width_px = buffer.getvalue(), image.width
            except Exception as exc:  # noqa: BLE001 - one bad image must not lose the paper
                logger.warning("could not render picture %d on page %d: %s", index, number, type(exc).__name__)
                continue
            if not is_usable_figure_image(png, min_short_side=self.min_figure_side):
                continue
            figure_id = f"fig-p{number:03d}-{index:02d}"
            path = work_dir / f"{stem}-{figure_id}.png"
            path.write_bytes(png)
            bbox = _docling_bbox(box)
            caption, caption_bbox = self._caption(picture, doc)
            rect = self._topleft_rect(bbox, height_of.get(number))
            spans = []
            if reader is not None and rect is not None and 0 < number <= len(reader):
                spans = [
                    {"text": s.text, "page": number, "bbox": _bbox(s.bbox).as_dict()}
                    for s in reader[number - 1].get_spans()
                    if _inside(s.bbox, rect)
                ]
            spans_path = None
            if spans:  # no file when there is no text layer (absent differs from empty)
                spans_path = path.with_suffix(".spans.json")
                spans_path.write_text(json.dumps(spans), encoding="utf-8")
            out.append(
                ParsedFigure(
                    caption=caption,
                    page=number,
                    index=index,
                    bbox=bbox,
                    images_scale=round(width_px / max(bbox.right - bbox.left, 1e-6), 4),
                    caption_bbox=caption_bbox,
                    figure_id=figure_id,
                    path=path,
                    spans_path=spans_path,
                )
            )
        return out

    @staticmethod
    def _topleft_rect(bbox: BoundingBox, page_height: float | None) -> tuple[float, float, float, float] | None:
        """``bbox`` as ``(x0, y0, x1, y1)`` top-left points (``None`` when the page height is unknown)."""
        if bbox.coord_origin.upper() == "TOPLEFT":
            return (bbox.left, bbox.top, bbox.right, bbox.bottom)
        if page_height is None:
            return None
        return (bbox.left, page_height - bbox.top, bbox.right, page_height - bbox.bottom)

    def _tables(self, doc: Any, work_dir: Path, stem: str) -> list[ParsedTable]:
        out: list[ParsedTable] = []
        per_page: Counter[int] = Counter()
        for table in doc.tables:
            number, box = _docling_page_of(table)
            if number is None:
                continue
            index = per_page[number]
            per_page[number] += 1
            table_id = f"tab-p{number:03d}-{index:02d}"
            data = table.data
            rows = [[str(getattr(cell, "text", "") or "") for cell in row] for row in (data.grid or [])]
            data_path: Path | None = None
            if rows:
                try:
                    buffer = io.StringIO()
                    csv.writer(buffer).writerows(rows)
                    data_path = work_dir / f"{stem}-{table_id}.csv"
                    data_path.write_text(buffer.getvalue(), encoding="utf-8")
                except Exception as exc:  # noqa: BLE001 - a bad table must not lose the paper
                    logger.warning("could not export table %d on page %d: %s", index, number, type(exc).__name__)
            cells = tuple(
                ParsedTableCell(
                    row=int(c.start_row_offset_idx),
                    col=int(c.start_col_offset_idx),
                    text=str(c.text or ""),
                    row_span=int(getattr(c, "row_span", 1) or 1),
                    col_span=int(getattr(c, "col_span", 1) or 1),
                    bbox=_docling_bbox(c.bbox) if getattr(c, "bbox", None) is not None else None,
                )
                for c in (data.table_cells or [])
            )
            caption, _ = self._caption(table, doc)
            out.append(
                ParsedTable(
                    page=number,
                    n_rows=int(data.num_rows),
                    n_cols=int(data.num_cols),
                    caption=caption,
                    bbox=_docling_bbox(box) if box is not None else None,
                    table_id=table_id,
                    data_path=data_path,
                    cells=cells,
                )
            )
        return out


def sections_from_markdown(markdown: str) -> tuple[ParsedSection, ...]:
    """Sections recovered from markdown headings when a parser offers no structure (page unknown)."""
    return tuple(
        ParsedSection(name=name, text=text, structural_id=f"section-{i:05d}")
        for i, (name, text) in enumerate(extract_sections(markdown).items())
    )


def default_parsers() -> tuple[PublicationParser, ...]:
    """The parsers the parse service tries, in order (Docling only)."""
    return (DoclingParser(),)


__all__ = (
    "DOCLING_ADAPTER_VERSION",
    "DOCLING_ARTIFACTS_ENV",
    "DoclingParser",
    "PARSER_DOCLING",
    "PublicationParser",
    "default_parsers",
    "is_usable_figure_image",
    "library_version",
    "sections_from_markdown",
)

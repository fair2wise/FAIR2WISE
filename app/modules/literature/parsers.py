"""Pluggable PDF parsers behind one protocol (L-10/L-12).

:class:`PublicationParser` is the seam: a parser turns a local PDF into a
:class:`~.parsed_document.ParsedDocument` and writes any figure/table files into
a work directory. The parse service stores those files as assets and versions
the output by ``parser-version-config``.

Only :class:`PyMuPDFParser` ships now. It reads PyMuPDF's structured text
directly (not ``pymupdf4llm``, which is not a dependency): font-size headings
with page and bbox, figures with bbox, caption, render scale and the text layer
inside the figure, and ruled tables to CSV with bbox and caption. Coordinates are
PyMuPDF's: points, origin top-left.

DEPRECATED(pymupdf): PyMuPDF (AGPL-3.0) is to be replaced by Docling (H1 E2,
docs/migration/reviews/H0/license_dependency_audit.md). A ``DoclingParser`` slots
in as another :class:`PublicationParser` without changing callers.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/paper_reading.py (``PublicationParser``,
``PyMuPDFParser``, ``_is_usable_figure``, ``_parser_version``, ``_sections_from_markdown``)
(BSD-3-Clause); the PyMuPDF structure reader is rewritten.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from .parsed_document import (
    BoundingBox,
    PageInfo,
    ParsedDocument,
    ParsedFigure,
    ParsedSection,
    ParsedTable,
    extract_sections,
    without_embedded_images,
)

logger = logging.getLogger(__name__)

PARSER_PYMUPDF = "pymupdf"
PARSER_DOCLING = "docling"
#: Bumped whenever this adapter's output changes for the same input and library version.
PYMUPDF_ADAPTER_VERSION = 1

_FIG_CAPTION = re.compile(r"^\s*(fig\.?|figure)\s*\d+", re.IGNORECASE)
_TABLE_CAPTION = re.compile(r"^\s*table\s*\d+", re.IGNORECASE)


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

    def config(self) -> Dict[str, Any]:
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


class PyMuPDFParser:
    """PyMuPDF structure reader: headings by font size, figures, tables, page geometry.

    Args:
        heading_ratio: A line is a heading candidate when its font size is at
            least this multiple of the body size.
        min_figure_side: Short-side pixel floor for a usable figure.
        max_heading_chars: Longer lines are never headings.
    """

    name = PARSER_PYMUPDF

    def __init__(
        self, *, heading_ratio: float = 1.15, min_figure_side: int = 120, max_heading_chars: int = 160
    ) -> None:
        self.heading_ratio = heading_ratio
        self.min_figure_side = min_figure_side
        self.max_heading_chars = max_heading_chars

    def version(self) -> str:
        """Installed PyMuPDF version."""
        return library_version("PyMuPDF") or library_version("pymupdf")

    def config(self) -> Dict[str, Any]:
        """Output-changing settings (part of the versioned key)."""
        return {
            "adapter_version": PYMUPDF_ADAPTER_VERSION,
            "heading_ratio": self.heading_ratio,
            "min_figure_side": self.min_figure_side,
            "max_heading_chars": self.max_heading_chars,
        }

    def parse(self, pdf_path: Path, work_dir: Path, *, source_ref: str) -> ParsedDocument:
        """Parse with PyMuPDF (raises ``ImportError`` when it is not installed)."""
        import fitz  # DEPRECATED(pymupdf): replaced by Docling later (H1 E2).

        started = time.monotonic()
        work_dir.mkdir(parents=True, exist_ok=True)
        stem = source_ref.split(":", 1)[-1][:16]
        with fitz.open(str(pdf_path)) as doc:
            if getattr(doc, "needs_pass", False):
                raise ValueError("encrypted PDF")
            pages: List[PageInfo] = []
            lines: List[Dict[str, Any]] = []
            figures: List[ParsedFigure] = []
            tables: List[ParsedTable] = []
            for page_index in range(doc.page_count):
                page = doc.load_page(page_index)
                number = page_index + 1
                pages.append(PageInfo(number, float(page.rect.width), float(page.rect.height), int(page.rotation)))
                page_lines = self._lines(page, number)
                fig_rects = self._figures(doc, page, number, page_lines, work_dir, stem, figures)
                table_rects = self._tables(page, number, page_lines, work_dir, stem, tables)
                for line in page_lines:
                    if any(_inside(line["bbox"], r) for r in fig_rects + table_rects):
                        continue
                    lines.append(line)
        sections, markdown = self._sections(lines)
        return ParsedDocument(
            source_ref=source_ref,
            markdown=without_embedded_images(markdown),
            num_pages=len(pages),
            parser=self.name,
            parser_version=self.version(),
            parser_config=self.config(),
            sections=tuple(sections),
            figures=tuple(figures),
            tables=tuple(tables),
            pages=tuple(pages),
            timings={"parser_conversion_s": round(time.monotonic() - started, 3)},
            pdf_path=pdf_path,
        )

    # -- text -----------------------------------------------------------------------------------

    @staticmethod
    def _lines(page: Any, number: int) -> List[Dict[str, Any]]:
        out = []
        for block in page.get_text("dict", sort=True).get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if str(s.get("text", "")).strip()]
                if not spans:
                    continue
                text = " ".join(" ".join(str(s["text"]).split()) for s in spans).strip()
                size = max(float(s.get("size", 0)) for s in spans)
                bold = any(int(s.get("flags", 0)) & 16 for s in spans)
                out.append(
                    {
                        "page": number,
                        "text": text,
                        "size": round(size, 1),
                        "bold": bold,
                        "bbox": tuple(float(v) for v in line["bbox"]),
                    }
                )
        return out

    def _sections(self, lines: List[Dict[str, Any]]) -> Tuple[List[ParsedSection], str]:
        if not lines:
            return [], ""
        weights: Counter = Counter()
        for line in lines:
            weights[line["size"]] += len(line["text"])
        body = weights.most_common(1)[0][0]
        threshold = body * self.heading_ratio

        def is_heading(line: Dict[str, Any]) -> bool:
            return line["size"] >= threshold and len(line["text"]) <= self.max_heading_chars

        heading_sizes = sorted({ln["size"] for ln in lines if is_heading(ln)}, reverse=True)
        title_size = None
        if heading_sizes:
            biggest = [ln for ln in lines if ln["size"] == heading_sizes[0]]
            if len(heading_sizes) > 1 and all(ln["page"] == 1 for ln in biggest) and len(biggest) <= 2:
                title_size = heading_sizes[0]  # the title is front matter, not a section
                heading_sizes = heading_sizes[1:]
        levels = {size: i + 1 for i, size in enumerate(heading_sizes)}
        sections: List[ParsedSection] = []
        markdown: List[str] = []
        name, level, page, bbox, parts, last_page = "preamble", 0, lines[0]["page"], None, [], lines[0]["page"]

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

        for line in lines:
            if is_heading(line) and line["size"] != title_size:
                flush()
                name, level, page = line["text"], levels.get(line["size"], 1), line["page"]
                bbox, parts, last_page = _bbox(line["bbox"]), [], line["page"]
                markdown.append(f"{'#' * min(level + 1, 6)} {line['text']}")
                continue
            if line["size"] == title_size:
                markdown.append(f"# {line['text']}")
            else:
                markdown.append(line["text"])
            parts.append(line["text"])
            last_page = line["page"]
        flush()
        return sections, "\n".join(markdown)

    # -- figures and tables ---------------------------------------------------------------------

    def _figures(
        self,
        doc: Any,
        page: Any,
        number: int,
        lines: List[Dict[str, Any]],
        work_dir: Path,
        stem: str,
        out: List[ParsedFigure],
    ) -> List[Tuple[float, ...]]:
        import fitz

        rects: List[Tuple[float, ...]] = []
        for index, info in enumerate(page.get_image_info(xrefs=True)):
            xref = int(info.get("xref") or 0)
            rect = tuple(float(v) for v in info.get("bbox") or (0, 0, 0, 0))
            if not xref or rect[2] - rect[0] < 1 or rect[3] - rect[1] < 1:
                continue
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                png = pix.tobytes("png")
                width_px = pix.width
            except Exception as exc:  # noqa: BLE001 - one bad image must not lose the paper
                logger.warning("could not extract image xref %s on page %d: %s", xref, number, type(exc).__name__)
                continue
            if not is_usable_figure_image(png, min_short_side=self.min_figure_side):
                continue
            figure_id = f"fig-p{number:03d}-{index:02d}"
            path = work_dir / f"{stem}-{figure_id}.png"
            path.write_bytes(png)
            caption, caption_rect = self._caption(lines, rect, _FIG_CAPTION)
            spans = [
                {"text": ln["text"], "page": number, "bbox": _bbox(ln["bbox"]).as_dict()}
                for ln in lines
                if _inside(ln["bbox"], rect)
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
                    bbox=_bbox(rect),
                    images_scale=round(width_px / max(rect[2] - rect[0], 1e-6), 4),
                    caption_bbox=_bbox(caption_rect) if caption_rect else None,
                    figure_id=figure_id,
                    path=path,
                    spans_path=spans_path,
                )
            )
            rects.append(rect)
        return rects

    def _tables(
        self,
        page: Any,
        number: int,
        lines: List[Dict[str, Any]],
        work_dir: Path,
        stem: str,
        out: List[ParsedTable],
    ) -> List[Tuple[float, ...]]:
        rects: List[Tuple[float, ...]] = []
        try:
            finder = page.find_tables()
        except Exception as exc:  # noqa: BLE001 - table detection is best-effort
            logger.info("table detection failed on page %d: %s", number, type(exc).__name__)
            return rects
        for index, table in enumerate(getattr(finder, "tables", []) or []):
            rect = tuple(float(v) for v in table.bbox)
            table_id = f"tab-p{number:03d}-{index:02d}"
            data_path: Optional[Path] = None
            rows: List[List[Any]] = []
            try:
                rows = [[("" if cell is None else str(cell)) for cell in row] for row in table.extract()]
                buffer = io.StringIO()
                csv.writer(buffer).writerows(rows)
                data_path = work_dir / f"{stem}-{table_id}.csv"
                data_path.write_text(buffer.getvalue(), encoding="utf-8")
            except Exception as exc:  # noqa: BLE001 - a bad table must not lose the paper
                logger.warning("could not export table %d on page %d: %s", index, number, type(exc).__name__)
            caption, _ = self._caption(lines, rect, _TABLE_CAPTION)
            out.append(
                ParsedTable(
                    page=number,
                    n_rows=len(rows),
                    n_cols=max((len(r) for r in rows), default=0),
                    caption=caption,
                    bbox=_bbox(rect),
                    table_id=table_id,
                    data_path=data_path,
                )
            )
            rects.append(rect)
        return rects

    @staticmethod
    def _caption(
        lines: List[Dict[str, Any]], rect: Sequence[float], pattern: "re.Pattern[str]"
    ) -> Tuple[str, Optional[Tuple[float, ...]]]:
        """Nearest caption line above or below ``rect`` (within 72 pt) matching ``pattern``."""
        best: Optional[Dict[str, Any]] = None
        best_gap = 72.0
        for line in lines:
            if not pattern.match(line["text"]):
                continue
            box = line["bbox"]
            overlap = min(box[2], rect[2]) - max(box[0], rect[0])
            if overlap <= 0:
                continue
            gap = box[1] - rect[3] if box[1] >= rect[3] - 1 else rect[1] - box[3]
            if -1 <= gap < best_gap:
                best, best_gap = line, gap
        if best is None:
            return "", None
        return best["text"], best["bbox"]


def sections_from_markdown(markdown: str) -> Tuple[ParsedSection, ...]:
    """Sections recovered from markdown headings when a parser offers no structure (page unknown)."""
    return tuple(
        ParsedSection(name=name, text=text, structural_id=f"section-{i:05d}")
        for i, (name, text) in enumerate(extract_sections(markdown).items())
    )


def default_parsers() -> Tuple[PublicationParser, ...]:
    """The parsers available now (PyMuPDF only; Docling joins with T09/E2)."""
    return (PyMuPDFParser(),)


__all__ = (
    "PARSER_DOCLING",
    "PARSER_PYMUPDF",
    "PYMUPDF_ADAPTER_VERSION",
    "PublicationParser",
    "PyMuPDFParser",
    "default_parsers",
    "is_usable_figure_image",
    "library_version",
    "sections_from_markdown",
)

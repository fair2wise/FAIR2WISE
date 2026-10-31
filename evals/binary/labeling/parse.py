"""Parse one pilot PDF locally with the existing F2W parsers.

Structure (figures, captions, the text layer inside each figure, tables, sections, the
parser revision key) comes from :class:`app.modules.literature.parsers.DoclingParser`, which
needs Docling's model weights (``F2W_DOCLING_ARTIFACTS_PATH``). Per-page text, which locators
and quote checks need, is the whole page text layer read by ``app.modules.literature.pdf_reader``
(pypdfium2), so a quote is checked against exactly the text the PDF carries, figure and table
text included. Figure crops are rendered with the same reader.

Figure crops the parser writes go to a temporary directory that is deleted before this
function returns. The parsed text is private: it may be cached only inside the private
label directory (``store.LabelStore``), never in the repository or a report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.modules.literature.parsers import DoclingParser
from app.modules.literature.pdf_reader import open_pdf


@dataclass(frozen=True)
class ParsedPaper:
    """What the labeller needs from one PDF.

    Attributes:
        source_sha256: SHA-256 of the PDF bytes.
        parser_revision: ``<parser>-<version>-<config hash>`` (``ParsedDocument.parser_key``).
        pages: ``[{"page": n, "text": str}]`` in page order.
        figures: ``[{"figure_id", "page", "caption", "text_layer": [str], "bbox": {l,t,r,b,...} | None}]``.
        tables: ``[{"table_id", "page", "caption", "n_rows", "n_cols"}]``.
        num_pages: Page count.
    """

    source_sha256: str
    parser_revision: str
    pages: Tuple[Dict[str, Any], ...]
    figures: Tuple[Dict[str, Any], ...] = ()
    tables: Tuple[Dict[str, Any], ...] = ()
    num_pages: int = 0
    section_names: Tuple[str, ...] = field(default_factory=tuple)

    def page_text(self, page: int) -> str:
        """Text of PDF page ``page`` ("" when out of range)."""
        for row in self.pages:
            if row["page"] == page:
                return row["text"]
        return ""

    def to_cache(self) -> Dict[str, Any]:
        """JSON form for the private parse cache."""
        return {"source_sha256": self.source_sha256, "parser_revision": self.parser_revision,
                "pages": list(self.pages), "figures": list(self.figures), "tables": list(self.tables),
                "num_pages": self.num_pages, "section_names": list(self.section_names)}

    @classmethod
    def from_cache(cls, data: Dict[str, Any]) -> "ParsedPaper":
        """Inverse of :meth:`to_cache`."""
        return cls(source_sha256=data["source_sha256"], parser_revision=data["parser_revision"],
                   pages=tuple(data["pages"]), figures=tuple(data.get("figures") or ()),
                   tables=tuple(data.get("tables") or ()), num_pages=int(data.get("num_pages") or 0),
                   section_names=tuple(data.get("section_names") or ()))


def file_sha256(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_pdf(pdf_path: Path, *, parser: Any | None = None) -> ParsedPaper:
    """Parse ``pdf_path`` with Docling (structure) and the pypdfium2 reader (page text).

    Args:
        pdf_path: The PDF to parse.
        parser: A structure parser with ``parse(pdf, work_dir, source_ref=...)`` returning a
            ``ParsedDocument``; defaults to ``DoclingParser()`` (needs Docling's model weights).

    Returns:
        The parsed paper; ``parser_revision`` is the document's ``parser_key``.
    """
    pdf_path = Path(pdf_path)
    sha = file_sha256(pdf_path)
    parser = parser if parser is not None else DoclingParser()
    with tempfile.TemporaryDirectory(prefix="f2w-label-parse-") as work:
        document = parser.parse(pdf_path, Path(work), source_ref=f"sha256:{sha}")
        figures: List[Dict[str, Any]] = []
        for fig in document.figures:
            spans: List[str] = []
            if fig.spans_path is not None and Path(fig.spans_path).is_file():
                spans = [str(s.get("text", "")) for s in json.loads(Path(fig.spans_path).read_text("utf-8"))]
            figures.append({"figure_id": fig.figure_id, "page": fig.page, "caption": fig.caption,
                            "text_layer": spans, "bbox": fig.bbox.as_dict() if fig.bbox is not None else None})
    tables = [{"table_id": t.table_id, "page": t.page, "caption": t.caption, "n_rows": t.n_rows,
               "n_cols": t.n_cols} for t in document.tables]
    with open_pdf(pdf_path) as doc:
        pages = [{"page": index + 1, "text": page.get_text("text")} for index, page in enumerate(doc)]
    return ParsedPaper(source_sha256=sha, parser_revision=document.parser_key, pages=tuple(pages),
                       figures=tuple(figures), tables=tuple(tables), num_pages=document.num_pages,
                       section_names=document.section_names())


#: Render scale for figure crops sent to a vision model (D1.7): 2x the PDF's 72 dpi, PNG.
FIGURE_CROP_DPI = 144
FIGURE_CROP_MAX_SIDE = 1600


def _topleft_box(box: Dict[str, Any], page_height: float) -> Tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)`` in top-left points from a figure ``bbox`` dict (origin BOTTOMLEFT or TOPLEFT, pt)."""
    if str(box.get("units", "pt")) not in ("pt", "points"):
        raise ValueError(f"unsupported bbox units {box.get('units')!r}")
    left, top, right, bottom = (float(box[k]) for k in ("l", "t", "r", "b"))
    origin = str(box.get("origin") or "BOTTOMLEFT").upper()
    if origin == "BOTTOMLEFT":
        top, bottom = page_height - top, page_height - bottom
    elif origin != "TOPLEFT":
        raise ValueError(f"unsupported bbox origin {box.get('origin')!r}")
    return min(left, right), min(top, bottom), max(left, right), max(top, bottom)


def render_figure_crops(pdf_path: Path, figures: Sequence[Dict[str, Any]], *, dpi: int = FIGURE_CROP_DPI,
                        max_side: int = FIGURE_CROP_MAX_SIDE) -> List[Optional[bytes]]:
    """PNG bytes of each figure's area (``bbox`` on its ``page``), in ``figures`` order (D1.7).

    A figure without a bbox, or a bbox that does not render, yields ``None`` so the caller can
    say which figures the vision model saw. The whole page is never sent. The crop is scaled
    to ``dpi`` and capped at ``max_side`` pixels on the longer edge.
    """
    out: List[Optional[bytes]] = []
    with open_pdf(pdf_path) as doc:
        for fig in figures:
            box = fig.get("bbox")
            page_no = int(fig.get("page") or 0)
            if not isinstance(box, dict) or not (1 <= page_no <= len(doc)):
                out.append(None)
                continue
            try:
                page = doc[page_no - 1]
                x0, y0, x1, y1 = _topleft_box(box, page.height)
                x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(page.width, x1), min(page.height, y1)
                if x1 - x0 < 4 or y1 - y0 < 4:
                    out.append(None)
                    continue
                zoom = dpi / 72.0
                longest = max(x1 - x0, y1 - y0) * zoom
                if longest > max_side:
                    zoom *= max_side / longest
                out.append(page.render_png((x0, y0, x1, y1), zoom))
            except Exception:  # noqa: BLE001 - an unrenderable figure is reported as unseen, never guessed
                out.append(None)
    return out


__all__ = ("FIGURE_CROP_DPI", "FIGURE_CROP_MAX_SIDE", "ParsedPaper", "file_sha256", "parse_pdf", "render_figure_crops")

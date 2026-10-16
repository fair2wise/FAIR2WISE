"""Parse one pilot PDF locally with the existing F2W parser.

Structure (figures, captions, the text layer inside each figure, tables, sections, the
parser revision key) comes from :class:`app.modules.literature.parsers.PyMuPDFParser`.
Per-page text, which locators and quote checks need, is built from the same parser's line
reader (``PyMuPDFParser._lines``) so a quote is checked against exactly the text the parser
produced, figure and table text included.

Figure crops the parser writes go to a temporary directory that is deleted before this
function returns. The parsed text is private: it may be cached only inside the private
label directory (``store.LabelStore``), never in the repository or a report.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple


@dataclass(frozen=True)
class ParsedPaper:
    """What the labeller needs from one PDF.

    Attributes:
        source_sha256: SHA-256 of the PDF bytes.
        parser_revision: ``<parser>-<version>-<config hash>`` (``ParsedDocument.parser_key``).
        pages: ``[{"page": n, "text": str}]`` in page order.
        figures: ``[{"figure_id", "page", "caption", "text_layer": [str]}]``.
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


def parse_pdf(pdf_path: Path) -> ParsedPaper:
    """Parse ``pdf_path`` with ``PyMuPDFParser`` and its line reader (raises if PyMuPDF is absent)."""
    import fitz  # DEPRECATED(pymupdf): the F2W parser's library; replaced with the parser (H1 E2)

    from app.modules.literature.parsers import PyMuPDFParser

    pdf_path = Path(pdf_path)
    sha = file_sha256(pdf_path)
    parser = PyMuPDFParser()
    with tempfile.TemporaryDirectory(prefix="f2w-label-parse-") as work:
        document = parser.parse(pdf_path, Path(work), source_ref=f"sha256:{sha}")
        figures: List[Dict[str, Any]] = []
        for fig in document.figures:
            spans: List[str] = []
            if fig.spans_path is not None and Path(fig.spans_path).is_file():
                spans = [str(s.get("text", "")) for s in json.loads(Path(fig.spans_path).read_text("utf-8"))]
            figures.append({"figure_id": fig.figure_id, "page": fig.page, "caption": fig.caption,
                            "text_layer": spans})
    tables = [{"table_id": t.table_id, "page": t.page, "caption": t.caption, "n_rows": t.n_rows,
               "n_cols": t.n_cols} for t in document.tables]
    pages: List[Dict[str, Any]] = []
    with fitz.open(str(pdf_path)) as doc:
        for index in range(doc.page_count):
            lines = PyMuPDFParser._lines(doc.load_page(index), index + 1)  # noqa: SLF001 - the parser's reader
            pages.append({"page": index + 1, "text": "\n".join(line["text"] for line in lines)})
    return ParsedPaper(source_sha256=sha, parser_revision=document.parser_key, pages=tuple(pages),
                       figures=tuple(figures), tables=tuple(tables), num_pages=document.num_pages,
                       section_names=document.section_names())


__all__ = ("ParsedPaper", "file_sha256", "parse_pdf")

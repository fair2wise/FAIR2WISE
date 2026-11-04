"""Plain PDF access (page text, metadata, page frame, rendering) over pypdfium2.

Replaces the PyMuPDF (``fitz``) calls that only need to read a PDF: PyMuPDF is AGPL-3.0 and is
being removed from this repository (license audit E2, task T05). pypdfium2 (Apache-2.0 or BSD-3)
is the PDF engine Docling itself uses, so it is already part of the locked environment. Structure
extraction (headings, figures, tables) is not here; it is Docling's job in
:mod:`app.modules.literature.parsers`.

The surface deliberately mirrors the small subset of ``fitz.Document`` the code used, so call sites
change from ``fitz.open(path)`` to :func:`open_pdf` and keep reading ``len(doc)``, ``doc[i]``,
``doc.metadata`` and ``page.get_text("text")``. Importing this module has no side effects:
pypdfium2 is imported when the first document is opened.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from ..json_types import Untyped

#: ``fitz``-style metadata keys, in the order PyMuPDF reported them, mapped from PDF Info keys.
_METADATA_KEYS = {
    "title": "Title",
    "author": "Author",
    "subject": "Subject",
    "keywords": "Keywords",
    "creator": "Creator",
    "producer": "Producer",
    "creationDate": "CreationDate",
    "modDate": "ModDate",
}


@dataclass(frozen=True)
class PdfSpan:
    """One text run of a page: verbatim text, its box and font size.

    Attributes:
        text: The run exactly as the text layer holds it (never normalised: superscripts such as the
            ``-1`` in ``(Å -1 )`` are part of the run).
        bbox: ``(x0, y0, x1, y1)`` in PDF points of the displayed page, origin top-left.
        size: Font size in points (0.0 when the PDF does not say).
    """

    text: str
    bbox: tuple[float, float, float, float]
    size: float = 0.0


#: pypdfium2 marks a generated (soft) hyphen at a line end with U+FFFE; it is not part of the text.
_SOFT_HYPHEN_MARK = "\ufffe"


def _clean_text(text: str) -> str:
    r"""Normalise pypdfium2 text to PyMuPDF's conventions: ``\n`` line breaks, no U+FFFE hyphen marks."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace(_SOFT_HYPHEN_MARK, "")


class PdfUnavailable(ImportError):
    """pypdfium2 is not installed (it ships with Docling in the locked environments)."""


class PdfPage:
    """One page of a :class:`PdfDocument` (0-based ``number``)."""

    def __init__(self, document: PdfDocument, number: int, page: Any) -> None:
        """Wrap a pypdfium2 page; created by :meth:`PdfDocument.load_page`, not directly."""
        self._document = document
        self._page = page
        self.number = number

    @property
    def width(self) -> float:
        """Page width in PDF points, in the displayed (rotation applied) orientation."""
        return float(self._page.get_size()[0])

    @property
    def height(self) -> float:
        """Page height in PDF points, in the displayed (rotation applied) orientation."""
        return float(self._page.get_size()[1])

    @property
    def rotation(self) -> int:
        """Page rotation in degrees (0, 90, 180 or 270)."""
        return int(self._page.get_rotation())

    def get_text(self, kind: str = "text") -> str:
        r"""Return the page's text layer as one string with ``\n`` line breaks.

        Args:
            kind: Only ``"text"`` is supported (the one PyMuPDF mode the code used); anything else
                raises ``ValueError`` instead of silently returning something different.

        Returns:
            The text in reading order of the content stream; empty for a page without a text layer.

        Raises:
            ValueError: ``kind`` is not ``"text"``.
        """
        if kind != "text":
            raise ValueError(f"unsupported text mode {kind!r}; only 'text' is implemented")
        textpage = self._page.get_textpage()
        try:
            text = str(textpage.get_text_range())
        finally:
            textpage.close()
        text = _clean_text(text)
        # PyMuPDF ended every page with a line break; callers that concatenate pages relied on it.
        return text if not text or text.endswith("\n") else text + "\n"

    def get_spans(self) -> list[PdfSpan]:
        """Every non-blank text run on the page, in content-stream order, boxed in top-left points.

        A run is one PDF text object (what PyMuPDF called a span), so text that the producer split
        into several runs stays split. Bounds are the glyph extents, a little tighter vertically than
        PyMuPDF's font-ascent boxes.
        """
        import pypdfium2.raw as pdfium_raw

        height = self.height
        textpage = self._page.get_textpage()
        spans: list[PdfSpan] = []
        try:
            for obj in self._page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_TEXT], max_depth=3):
                obj.textpage = textpage
                text = str(obj.extract()).replace(_SOFT_HYPHEN_MARK, "")
                if not text.strip():
                    continue
                left, bottom, right, top = obj.get_bounds()
                spans.append(PdfSpan(text, (float(left), float(height - top), float(right), float(height - bottom)),
                                     float(obj.get_font_size())))
        finally:
            textpage.close()
        return spans

    def image_boxes(self) -> list[tuple[float, float, float, float]]:
        """Boxes of the raster images placed on the page, ``(x0, y0, x1, y1)`` top-left points."""
        import pypdfium2.raw as pdfium_raw

        height = self.height
        boxes = []
        for obj in self._page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE], max_depth=3):
            left, bottom, right, top = obj.get_bounds()
            boxes.append((float(left), float(height - top), float(right), float(height - bottom)))
        return boxes

    def render_gray(self, region: tuple[float, float, float, float] | None, scale: float) -> Untyped:
        """Render ``region`` (top-left points, ``(x0, y0, x1, y1)``) to a 2-D uint8 numpy array.

        Args:
            region: The area to render in the displayed page frame, clipped to the page; ``None``
                renders the whole page.
            scale: Output pixels per PDF point.

        Returns:
            A grayscale ``numpy`` array of shape ``(height_px, width_px)``; the caller owns it.
        """
        import numpy as np

        width, height = self.width, self.height
        if region is None:
            x0, y0, x1, y1 = 0.0, 0.0, width, height
        else:
            x0, y0, x1, y1 = (max(0.0, region[0]), max(0.0, region[1]),
                              min(width, region[2]), min(height, region[3]))
        # pypdfium2's crop is (left, bottom, right, top) as margins cut from each side, in points.
        crop = (x0, height - y1, width - x1, y0)
        bitmap = self._page.render(scale=scale, crop=crop, grayscale=True)
        return np.asarray(bitmap.to_numpy()).reshape(bitmap.height, bitmap.width).copy()

    def render_png(self, region: tuple[float, float, float, float] | None, scale: float) -> bytes:
        """Render ``region`` (top-left points, ``(x0, y0, x1, y1)``) to RGB PNG bytes.

        Args:
            region: The area to render in the displayed page frame, clipped to the page; ``None``
                renders the whole page.
            scale: Output pixels per PDF point.

        Returns:
            The PNG file content (RGB, no alpha).
        """
        from io import BytesIO

        width, height = self.width, self.height
        if region is None:
            x0, y0, x1, y1 = 0.0, 0.0, width, height
        else:
            x0, y0, x1, y1 = (max(0.0, region[0]), max(0.0, region[1]),
                              min(width, region[2]), min(height, region[3]))
        crop = (x0, height - y1, width - x1, y0)
        image = self._page.render(scale=scale, crop=crop).to_pil().convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def close(self) -> None:
        """Release the page handle (idempotent)."""
        if self._page is not None:
            self._page.close()
            self._page = None


class PdfDocument:
    """An open PDF: ``len()``, indexing, ``metadata`` and context-manager use like ``fitz.Document``."""

    def __init__(self, document: Any) -> None:
        """Wrap a pypdfium2 document; use :func:`open_pdf` to create one."""
        self._document = document
        self._pages: dict[int, PdfPage] = {}

    def __len__(self) -> int:
        """Number of pages."""
        return len(self._document)

    @property
    def page_count(self) -> int:
        """Number of pages (``fitz`` spelling)."""
        return len(self)

    def load_page(self, number: int) -> PdfPage:
        """Return the 0-based page ``number`` (negative numbers count from the end).

        Raises:
            IndexError: ``number`` is outside the document.
        """
        count = len(self)
        if number < 0:
            number += count
        if not 0 <= number < count:
            raise IndexError(f"page {number} out of range for a {count}-page document")
        if number not in self._pages:
            self._pages[number] = PdfPage(self, number, self._document[number])
        return self._pages[number]

    def __getitem__(self, number: int) -> PdfPage:
        """Same as :meth:`load_page`."""
        return self.load_page(number)

    def __iter__(self) -> Any:
        """Iterate the pages in order."""
        for number in range(len(self)):
            yield self.load_page(number)

    @property
    def metadata(self) -> dict[str, Any]:
        """The PDF Info dictionary with ``fitz``-style keys (``creationDate`` is the raw ``D:...``).

        Missing entries are empty strings, as PyMuPDF reported them.
        """
        raw = self._document.get_metadata_dict(skip_empty=False) or {}
        out: dict[str, Any] = {"format": f"PDF {self._document.get_version() / 10:.1f}"
                               if self._document.get_version() else "PDF"}
        for key, pdf_key in _METADATA_KEYS.items():
            out[key] = raw.get(pdf_key, "") or ""
        return out

    def close(self) -> None:
        """Close the document and its page handles (idempotent)."""
        for page in self._pages.values():
            page.close()
        self._pages.clear()
        if self._document is not None:
            self._document.close()
            self._document = None

    def __enter__(self) -> PdfDocument:
        """Context-manager entry."""
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        """Close on exit."""
        self.close()


def open_pdf(source: str | Path | bytes) -> PdfDocument:
    """Open a PDF from a path or from its bytes.

    Args:
        source: A filesystem path (``str`` or ``Path``) or the raw PDF bytes.

    Returns:
        The open :class:`PdfDocument`; close it (or use it as a context manager).

    Raises:
        PdfUnavailable: pypdfium2 is not installed.
        FileNotFoundError: ``source`` is a path that does not exist.
        RuntimeError: The file is not a readable PDF (pypdfium2's error, unchanged).
    """
    try:
        import pypdfium2
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise PdfUnavailable("pypdfium2 is not installed; it ships with the Docling environment") from exc
    if isinstance(source, (bytes, bytearray)):
        return PdfDocument(pypdfium2.PdfDocument(bytes(source)))
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return PdfDocument(pypdfium2.PdfDocument(str(path)))


__all__ = ("PdfDocument", "PdfPage", "PdfSpan", "PdfUnavailable", "open_pdf")

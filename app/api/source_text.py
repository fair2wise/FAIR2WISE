"""Per-turn retained source text for quote verification on the legacy UI API (H2).

The disclosure projector shows a quotation verbatim only when a
:class:`~app.modules.security.disclosure.QuoteVerifier` confirms it against a
source region retrieved and cited for the same turn, and it clips answers that
reproduce more than ``max_region_fraction`` (40%, owner decision O-D2) of one
region. This module supplies the two things the projector needs:

* **Capture.** ``AgentPipelineService`` records what its read paths actually
  used for a turn, as references only:

  - :func:`note_retrieval` keeps the retrieval agent's merged context string
    (``RetrievalAgent._trace_merged_context``, the text the judge saw). Its
    ``[PDF: file p.N]`` blocks are the source-RAG literature chunks
    (``hybrid_rag.context.ContextBuilder``); KG, OPS, MP and Tiled blocks are
    ignored, so answers grounded only in them are projected as before.
  - :func:`note_paper_evidence` keeps the active PDF path and the pages the
    paper-evidence answer used or cited (``PaperEvidenceAgent`` reads the same
    pages with the pypdfium2 reader's ``get_text("text")``).

  Capture is bound to the request by :func:`capture_turn` (a context variable
  set by the route); nothing is captured outside a route, and nothing comes
  from request input.
* **Read.** :class:`TurnSources` turns the capture into
  :class:`~app.modules.security.disclosure.SourceRef` regions (one per cited
  PDF page) and a ``(source_version, locator) -> text`` reader. The reader is
  only ever called by ``QuoteVerifier``, which runs it inside
  ``SourceAccessScope.read(SourceKind.PARSED_TEXT, ...)`` under an internal
  grant issued by the guard's startup-built ``SourceAccessAuthority``.

Region text. A paper-evidence region is the full extracted page. A source-RAG
region is the retrieved chunk text for that page (chunks for one page joined
by newlines), so the 40% rule is applied to the retained text the turn
actually used, which is never more than the page. When both exist for the same
citation the full page wins.

Importing this module has no side effects (the PDF reader is imported on first read).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
import contextlib
import contextvars
import hashlib
import logging
from pathlib import Path
import re
import threading
from typing import Any

from ..modules.contracts.evidence import SourceLocator
from ..modules.security.disclosure import SourceRef

logger = logging.getLogger(__name__)

#: ``[PDF: file.pdf p.3]`` exactly as ``ContextBuilder`` and ``PaperEvidenceAgent`` write it.
PDF_CITE_RE = re.compile(r"\[PDF:\s*([^\]\n]+?)\s+p\.(\d+)\]")
_TAG_LINE_RE = re.compile(r"^\[(?:KG|PDF|OPS|MP|TILED)\b[^\]\n]*\]\s*$")
_HEADING_RE = re.compile(r"^#{2,}\s")

CHUNK_VERSION = "retrieved-chunks"
PAGE_VERSION = "pdf-page-text"

_CURRENT: contextvars.ContextVar[TurnSources | None] = contextvars.ContextVar(
    "f2w_legacy_turn_sources", default=None)


def citation_for(filename: str, page: int) -> str:
    """Visible citation token for one PDF page."""
    return f"[PDF: {filename} p.{int(page)}]"


def pdf_blocks(merged_context: str) -> dict[tuple[str, int], str]:
    """Return ``{(filename, page): chunk text}`` for the ``[PDF: f p.N]`` blocks of a merged context.

    A block is a citation line followed by its body, up to the next citation line or
    ``###`` heading (``ContextBuilder._section`` layout). Bodies of the same page are joined.
    """
    out: dict[tuple[str, int], list[str]] = {}
    key: tuple[str, int] | None = None
    body: list[str] = []

    def flush() -> None:
        text = "\n".join(body).strip()
        if key is not None and text:
            out.setdefault(key, []).append(text)

    for line in str(merged_context or "").split("\n"):
        stripped = line.strip()
        if _TAG_LINE_RE.match(stripped) or _HEADING_RE.match(stripped):
            flush()
            body = []
            match = PDF_CITE_RE.fullmatch(stripped)
            key = (Path(match.group(1).strip()).name, int(match.group(2))) if match else None
            continue
        if key is not None:
            body.append(line)
    flush()
    return {k: "\n".join(v) for k, v in out.items()}


def _pdf_page_text(pdf_path: str, page: int) -> str | None:
    """Extracted text of one PDF page, as ``PaperEvidenceAgent._query`` reads it."""
    from ..modules.literature.pdf_reader import open_pdf

    path = Path(pdf_path)
    if not path.is_file():
        return None
    with open_pdf(path) as doc:
        if page < 1 or page > len(doc):
            return None
        return str(doc[page - 1].get_text("text") or "").strip() or None


class TurnSources:
    """Source regions retrieved and cited for one chat turn (references; text is read on demand).

    Args:
        page_reader: ``(pdf_path, page) -> text`` for paper-evidence pages (pypdfium2 by default).
    """

    def __init__(self, page_reader: Callable[[str, int], str | None] = _pdf_page_text) -> None:
        """Start with no captured chunks or pages; ``page_reader`` reads PDF page text on demand."""
        self._lock = threading.Lock()
        self._chunks: dict[tuple[str, int], str] = {}
        self._pages: dict[tuple[str, int], str] = {}  # (filename, page) -> pdf path
        self._page_reader = page_reader

    # ------------------------------------------------------------------ capture

    def add_merged_context(self, merged_context: Any) -> None:
        """Keep the ``[PDF: ...]`` chunk blocks of a retrieval merged context."""
        if not isinstance(merged_context, str) or "[PDF:" not in merged_context:
            return
        blocks = pdf_blocks(merged_context)
        with self._lock:
            for key, text in blocks.items():
                previous = self._chunks.get(key)
                self._chunks[key] = text if previous is None or text in previous else previous + "\n" + text

    def add_pdf_pages(self, pdf_path: Any, pages: Sequence[Any]) -> None:
        """Keep paper-evidence pages of ``pdf_path`` (a server path from workflow state)."""
        if not pdf_path:
            return
        name = Path(str(pdf_path)).name
        with self._lock:
            for value in pages:
                try:
                    page = int(value)
                except (TypeError, ValueError):
                    continue
                if page >= 1:
                    self._pages[(name, page)] = str(pdf_path)

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._chunks or self._pages)

    # ------------------------------------------------------------------ projection inputs

    @staticmethod
    def _chunk_version(text: str) -> str:
        return f"{CHUNK_VERSION}:sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"

    def refs(self) -> list[SourceRef]:
        """One :class:`SourceRef` per captured PDF page (full pages before chunks of the same page)."""
        with self._lock:
            pages = dict(self._pages)
            chunks = dict(self._chunks)
        out: list[SourceRef] = []
        for (name, page) in sorted(pages):
            out.append(SourceRef(name, PAGE_VERSION, SourceLocator(page=page), citation_for(name, page)))
        for (name, page), text in sorted(chunks.items()):
            if (name, page) not in pages:
                out.append(SourceRef(name, self._chunk_version(text), SourceLocator(page=page),
                                     citation_for(name, page)))
        return out

    def work_ids(self) -> list[str]:
        """Works the captured regions belong to (the grant's ``work_ids``)."""
        return sorted({ref.work_id for ref in self.refs()})

    def reader_for(self, refs: Sequence[SourceRef]) -> Callable[[str, SourceLocator], str | None]:
        """``(source_version, locator) -> text`` over ``refs`` only; for ``QuoteVerifier``.

        Must only be passed to a ``QuoteVerifier``, which calls it inside ``SourceAccessScope.read``.
        """
        with self._lock:
            pages = dict(self._pages)
            chunks = dict(self._chunks)
        table: dict[tuple[str, int | None], Callable[[], str | None]] = {}
        for ref in refs:
            key = (ref.work_id, ref.locator.page)
            if ref.source_version == PAGE_VERSION and key in pages:
                path, page = pages[key], int(ref.locator.page or 0)
                table[(ref.source_version, ref.locator.page)] = self._cached(lambda p=path, n=page:  # type: ignore[misc]  # mypy cannot infer a lambda with default-bound args
                                                                             self._page_reader(p, n))
            elif key in chunks:
                text = chunks[key]
                table[(ref.source_version, ref.locator.page)] = lambda t=text: t  # type: ignore[misc]  # mypy cannot infer a lambda with default-bound args

        def read(source_version: str, locator: SourceLocator) -> str | None:
            loader = table.get((source_version, locator.page))
            if loader is None:
                return None
            try:
                return loader()
            except Exception as exc:  # unreadable source: the quote stays unverified
                logger.warning("quote verification could not read a retained source region: %s",
                               type(exc).__name__)
                return None

        return read

    @staticmethod
    def _cached(loader: Callable[[], str | None]) -> Callable[[], str | None]:
        box: dict[str, str | None] = {}

        def load() -> str | None:
            if "v" not in box:
                box["v"] = loader()
            return box["v"]

        return load


# --------------------------------------------------------------------------- capture hooks


@contextlib.contextmanager
def capture_turn(sources: TurnSources | None = None) -> Iterator[TurnSources]:
    """Bind a :class:`TurnSources` to the current request (tasks created inside inherit it)."""
    sources = sources if sources is not None else TurnSources()
    token = _CURRENT.set(sources)
    try:
        yield sources
    finally:
        _CURRENT.reset(token)


def current_turn() -> TurnSources | None:
    """The turn capture of the running request, if any."""
    return _CURRENT.get()


def note_retrieval(agent: Any, verdict: Any) -> Any:
    """Record the merged context ``agent`` used for ``verdict``; returns ``verdict`` unchanged."""
    sources = _CURRENT.get()
    if sources is not None:
        try:
            sources.add_merged_context(getattr(agent, "_trace_merged_context", None))
        except Exception as exc:  # capture must never change a turn
            logger.warning("retrieval source capture failed: %s", type(exc).__name__)
    return verdict


def note_paper_evidence(pdf_path: Any, result: Any) -> Any:
    """Record the pages a paper-evidence ``result`` used or cited; returns ``result`` unchanged."""
    sources = _CURRENT.get()
    if sources is not None and isinstance(result, Mapping):
        try:
            name = Path(str(pdf_path or "")).name
            pages = list(result.get("used_pages") or [])
            pages += [int(m.group(2)) for m in PDF_CITE_RE.finditer(str(result.get("answer") or ""))
                      if Path(m.group(1).strip()).name == name]
            sources.add_pdf_pages(pdf_path, pages)
        except Exception as exc:
            logger.warning("paper evidence source capture failed: %s", type(exc).__name__)
    return result


__all__ = ("TurnSources", "capture_turn", "citation_for", "current_turn", "note_paper_evidence",
           "note_retrieval", "pdf_blocks")

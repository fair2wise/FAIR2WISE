"""Page-aware PDF chunks and heading-aware ops-doc chunks."""
from __future__ import annotations

import re
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

CHUNKER_VERSION = "1.0.0"
WINDOW_CHARS = 1200
WINDOW_OVERLAP = 200

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+\n")


def _windows(text: str, size: int = WINDOW_CHARS, overlap: int = WINDOW_OVERLAP) -> List[str]:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return []
    if len(cleaned) <= size:
        return [cleaned]
    out: List[str] = []
    start = 0
    step = max(1, size - overlap)
    while start < len(cleaned):
        out.append(cleaned[start : start + size])
        start += step
    return out


def _strip_html(raw: str) -> str:
    text = unescape(_TAG_RE.sub(" ", raw or ""))
    text = _WS_RE.sub("\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_pdf(
    path: Path,
    *,
    work_id: str,
    doc_id: str,
    corpus: str = "literature",
    extra: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Split one PDF into page-aware windows. Missing/unreadable files yield []."""
    extra = extra or {}
    try:
        import fitz
    except Exception:
        return []
    pdf = Path(path)
    if not pdf.is_file():
        return []
    try:
        doc = fitz.open(str(pdf))
    except Exception:
        return []
    chunks: List[Dict[str, Any]] = []
    try:
        for page_no, page in enumerate(doc, start=1):
            try:
                text = page.get_text("text") or ""
            except Exception:
                text = ""
            for idx, window in enumerate(_windows(text)):
                chunks.append(
                    _chunk_record(
                        corpus=corpus,
                        work_id=work_id,
                        doc_id=doc_id,
                        path=str(pdf),
                        page=page_no,
                        locator=f"p.{page_no}",
                        heading="",
                        text=window,
                        chunk_index=idx,
                        extra=extra,
                    )
                )
    finally:
        doc.close()
    return chunks


def chunk_text_document(
    path: Path,
    *,
    doc_id: str,
    corpus: str = "bl1101",
    url: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Heading-aware chunks for HTML/Markdown/plain ops docs."""
    extra = dict(extra or {})
    if url:
        extra.setdefault("url", url)
    src = Path(path)
    if not src.is_file():
        return []
    try:
        raw = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    suffix = src.suffix.lower()
    if suffix in {".html", ".htm"}:
        sections = _html_sections(raw)
    elif suffix in {".md", ".markdown"}:
        sections = _markdown_sections(raw)
    else:
        sections = [("", raw)]
    chunks: List[Dict[str, Any]] = []
    line_cursor = 1
    for heading, body in sections:
        body_text = body.strip()
        n_lines = body_text.count("\n") + (1 if body_text else 0)
        start_line = line_cursor
        end_line = line_cursor + max(0, n_lines - 1)
        locator_head = f"{src.as_posix()}:{start_line}-{end_line}"
        if heading:
            locator_head = f"{locator_head} §{heading}"
        for idx, window in enumerate(_windows(body_text or heading)):
            chunks.append(
                _chunk_record(
                    corpus=corpus,
                    work_id=None,
                    doc_id=doc_id,
                    path=str(src),
                    page=None,
                    locator=locator_head,
                    heading=heading,
                    text=window,
                    chunk_index=idx,
                    extra=extra,
                )
            )
        line_cursor = end_line + 1
    return chunks


def _chunk_record(
    *,
    corpus: str,
    work_id: Optional[str],
    doc_id: str,
    path: str,
    page: Optional[int],
    locator: str,
    heading: str,
    text: str,
    chunk_index: int,
    extra: Dict[str, Any],
) -> Dict[str, Any]:
    page_part = f"p{page}" if page is not None else "doc"
    slug_heading = re.sub(r"[^a-z0-9]+", "-", (heading or "body").lower()).strip("-") or "body"
    chunk_id = f"{corpus}:{doc_id}:{page_part}:{slug_heading}:c{chunk_index}"
    rec = {
        "chunk_id": chunk_id,
        "corpus": corpus,
        "work_id": work_id,
        "doc_id": doc_id,
        "path": path,
        "page": page,
        "locator": locator,
        "heading": heading,
        "text": text,
        "chunker_version": CHUNKER_VERSION,
        "entity_ids": list(extra.get("entity_ids") or []),
        "matched_slots": dict(extra.get("matched_slots") or {}),
        "title": extra.get("title") or "",
        "doi": extra.get("doi") or "",
        "url": extra.get("url") or "",
        "corpus_revision": extra.get("corpus_revision") or "",
        "sha256": extra.get("sha256") or "",
    }
    return rec


def _markdown_sections(raw: str) -> List[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(raw or ""))
    if not matches:
        return [("", raw or "")]
    sections: List[tuple[str, str]] = []
    if matches[0].start() > 0:
        preamble = raw[: matches[0].start()].strip()
        if preamble:
            sections.append(("", preamble))
    for i, match in enumerate(matches):
        heading = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        sections.append((heading, raw[start:end]))
    return sections or [("", raw or "")]


def _html_sections(raw: str) -> List[tuple[str, str]]:
    try:
        from lxml import html as lhtml
    except Exception:
        return [("", _strip_html(raw))]
    try:
        tree = lhtml.fromstring(raw.encode("utf-8", errors="replace"))
    except Exception:
        return [("", _strip_html(raw))]
    sections: List[tuple[str, str]] = []
    current_heading = ""
    buf: List[str] = []

    def flush() -> None:
        text = " ".join(part.strip() for part in buf if part and part.strip())
        if text or current_heading:
            sections.append((current_heading, text))
        buf.clear()

    for node in tree.iter():
        tag = str(getattr(node, "tag", "") or "").lower()
        if tag in {"script", "style", "noscript"}:
            continue
        if tag in {"h1", "h2", "h3"}:
            flush()
            current_heading = " ".join(node.text_content().split())
            continue
        if node.text:
            buf.append(node.text)
        if node.tail:
            buf.append(node.tail)
    flush()
    return sections or [("", _strip_html(raw))]


def iter_nonempty(chunks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [chunk for chunk in chunks if str(chunk.get("text") or "").strip()]

"""Which publication metadata values are real: junk titles and authors from PDF Info are never used.

Used by the extractor (``Orchestrator``) and by the library's publication records (``app/modules/library/publication_records.py``).
A file path, an ``f2w://`` URI, a file name, a tool's placeholder is never a title; an account or scanner name or a postal
address is never an author. The library record of a paper, when it has one, wins over the PDF for every field it has.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]{1,12}://", re.I)
_EXTENSION = re.compile(r"\.(?:pdf|vp|doc|docx|tex|dvi|ps|eps|indd|qxd|rtf|txt|htm|html|ppt|pptx|odt|tif|tiff|jpg|png|fm|wpd)$", re.I)
_PATHLIKE = re.compile(r"^(?:[a-z]:)?[\\/]|[\\/][^\\/\s]+[\\/]|^[\w.-]+[\\/][\w.-]+$", re.I)
_WORD_TOOL = re.compile(r"^(?:microsoft (?:word|powerpoint|excel) - |untitled|document\d*$|slide \d+$|pii:|no title)", re.I)
_BARE_ID = re.compile(r"^[a-z]{0,4}\d[\w.-]{2,16}$", re.I)   # a11v6n1, 2010-463, 0123abc: a file stem, not a title
_ADDRESS = re.compile(r"\b(?:street|avenue|boulevard|road|suite|p\.?o\.? box|zip)\b|\b[A-Z]{2}\s+\d{5}\b|\d{4,}", re.I)
_JUNK_AUTHORS = frozenset("""administrator admin user owner author unknown anonymous default guest pc laptop microsoft
xerox scanner scan canon hp epson ricoh konica minolta kyocera sharp toshiba adobe acrobat distiller pdfcreator your name
authorized user windows user office user""".split())

#: The fields of the publication metadata the extractor stores (see ``provenance._PUB_FIELDS``).
FIELDS = ("paper_title", "authors", "publication_year", "doi", "journal", "abstract_text")


def is_junk_title(text: object) -> bool:
    """Whether ``text`` cannot be a paper's title: empty, a path or URI, a file name or stem, or a tool's placeholder."""
    raw = " ".join(str(text or "").split())
    if not raw:
        return True
    return bool(_SCHEME.match(raw) or _EXTENSION.search(raw) or _PATHLIKE.search(raw) or _WORD_TOOL.match(raw)
                or (_BARE_ID.match(raw) and " " not in raw))


def is_junk_author(text: object) -> bool:
    """Whether ``text`` cannot be an author: a tool or account name, or a postal address."""
    raw = " ".join(str(text or "").split())
    if not raw:
        return True
    return raw.lower() in _JUNK_AUTHORS or bool(_ADDRESS.search(raw) and len(raw) > 20) or bool(_SCHEME.match(raw))


def sanitize_pdf_meta(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """The PDF-derived metadata without a junk title or junk authors (every other field is kept as it is)."""
    out = dict(meta or {})
    if "paper_title" in out and is_junk_title(out.get("paper_title")):
        out["paper_title"] = None
    if "authors" in out:
        out["authors"] = [a for a in out.get("authors") or [] if not is_junk_author(a)]
    return out


def merge_known_publication(pdf_meta: Mapping[str, Any] | None, known: Mapping[str, Any] | None) -> dict[str, Any]:
    """The publication metadata to store: the library record's fields, then the sanitised PDF ones for what it lacks."""
    merged = sanitize_pdf_meta(pdf_meta)
    for key, value in (known or {}).items():
        if value in (None, "", []):
            continue
        merged[key] = value
    return merged




__all__ = ("FIELDS", "is_junk_author", "is_junk_title", "merge_known_publication", "sanitize_pdf_meta")

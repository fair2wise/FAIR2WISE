"""Check every label's quote against the parsed text, and the H3 F8 hard gates as flags.

A label whose quote cannot be located is **flagged, never dropped**: the reviewer sees it
in the review sheet and decides. Checks (H3 F8: quote located; number and unit in the
quote; unit consistent with the quantity):

* ``quote_check.status``: ``exact`` or ``normalized`` on the stated page (via
  :func:`app.modules.evidence.quote_location.locate_quote`), ``dehyphenated`` when it
  matches only after joining words split across lines, ``wrong_page`` when found on other
  pages (they are listed), ``not_found``, or ``missing_quote``;
* flags ``value_not_in_quote``, ``unit_not_in_quote``, ``unit_dimension_mismatch``,
  ``quote_ambiguous`` (more than one occurrence on the page), ``page_out_of_range``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.modules.evidence.grounding import normalize
from app.modules.evidence.quote_location import count_occurrences, locate_quote
from app.modules.evidence.units import unit_dimension
from evals.binary.labeling.parse import ParsedPaper

_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_LENGTH_WORDS = ("spacing", "thickness", "distance", "diameter", "radius", "size", "period", "length", "width")
_RECIPROCAL_WORDS = ("scattering vector", "q-value", "q value", "peak position in q", "momentum transfer")


def _dehyphenate(text: str) -> str:
    return _HYPHEN_BREAK.sub(r"\1\2", text)


def _find(quote: str, text: str) -> Optional[str]:
    hit = locate_quote(quote, text)
    if hit.found:
        return hit.match_kind
    if locate_quote(_dehyphenate(quote), _dehyphenate(text)).found:
        return "dehyphenated"
    return None


def check_quote(paper: ParsedPaper, quote: Optional[str], page: Any) -> Dict[str, Any]:
    """Locate ``quote`` on ``page`` (then anywhere); return ``{"status", "found_pages", "flags"}``."""
    flags: List[str] = []
    if not isinstance(quote, str) or not quote.strip():
        return {"status": "missing_quote", "found_pages": [], "flags": flags}
    page_no = page if isinstance(page, int) and not isinstance(page, bool) else None
    if page_no is not None and not 1 <= page_no <= max(paper.num_pages, len(paper.pages)):
        flags.append("page_out_of_range")
        page_no = None
    if page_no is not None:
        kind = _find(quote, paper.page_text(page_no))
        if kind is not None:
            if count_occurrences(quote, paper.page_text(page_no)) > 1:
                flags.append("quote_ambiguous")
            return {"status": kind, "found_pages": [page_no], "flags": flags}
    found = [row["page"] for row in paper.pages if _find(quote, row["text"]) is not None]
    if found:
        return {"status": "wrong_page" if page_no is not None else "located_without_page", "found_pages": found,
                "flags": flags}
    return {"status": "not_found", "found_pages": [], "flags": flags}


def _contains(haystack: str, needle: str) -> bool:
    return normalize(needle).casefold() in normalize(haystack).casefold()


def value_flags(item: Dict[str, Any], quote: Optional[str]) -> List[str]:
    """F8 flags for a value label: number and unit inside the quote, unit fits the quantity."""
    flags: List[str] = []
    text = quote or ""
    value = item.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = repr(value)
    if isinstance(value, str) and value.strip() and not _contains(text, value.strip()):
        flags.append("value_not_in_quote")
    unit = item.get("unit")
    if isinstance(unit, str) and unit.strip() and not _contains(text, unit.strip()):
        flags.append("unit_not_in_quote")
    if isinstance(unit, str) and unit.strip():
        dim = unit_dimension(unit)
        name = str(item.get("quantity") or "").casefold()
        if dim == "reciprocal" and any(w in name for w in _LENGTH_WORDS):
            flags.append("unit_dimension_mismatch")
        elif dim == "length" and any(w in name for w in _RECIPROCAL_WORDS):
            flags.append("unit_dimension_mismatch")
    return flags


__all__ = ("check_quote", "value_flags")

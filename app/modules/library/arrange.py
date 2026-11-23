"""Where an uploaded paper goes inside a library category (A8): the folder name and the year sub-folder.

A category is a top-level folder of the library (``rsoxs``, ``tomo``); inside it papers sit in one folder per
publication year (``rsoxs/2013/x.pdf``), with ``unknown`` for papers whose year cannot be read. The year is
guessed locally, without a network: a year in the file name (publisher downloads are named
``Journal - 2016 - Author - Title.pdf``), then the printed received/published/copyright years of the first
pages, then the PDF creation date (the weakest hint). Nothing here touches the disk.
"""

from __future__ import annotations

from datetime import UTC, datetime
import re

#: Folder used when no year can be read (the harvest scripts use the same name).
UNKNOWN_YEAR = "unknown"

#: A category name: letters, digits, ``_`` and ``-``; starts with a letter or digit; at most 64 characters.
FOLDER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_YEAR = r"(?:19|20)\d{2}"
_NAME_YEAR = re.compile(rf"(?<!\d)({_YEAR})(?!\d)")
_PUBLISHED = re.compile(
    rf"(?:published|received|accepted|available\s+online|online)\b[^\n]{{0,60}}?(?<!\d)({_YEAR})(?!\d)", re.I)
_COPYRIGHT = re.compile(rf"(?:©|\(c\)|copyright)\s*(?:by\s+)?(?:[A-Za-z .,&-]{{0,30}}?\s)?({_YEAR})(?!\d)", re.I)
_CREATED = re.compile(rf"D:({_YEAR})")


def is_valid_folder_name(name: str) -> bool:
    """True when ``name`` is an acceptable category folder name."""
    return bool(FOLDER_NAME_PATTERN.match(name))


def category_problem(raw: object) -> str | None:
    """Why ``raw`` cannot be a category name (after trimming), or ``None`` when it can.

    The name becomes a top-level folder of the library, so only the :data:`FOLDER_NAME_PATTERN` characters are
    allowed: no separators, dots, spaces or leading ``-``/``_`` (hence no ``..``, absolute path or hidden folder).
    """
    name = raw.strip() if isinstance(raw, str) else ""
    if not name:
        return "enter a category name"
    if len(name) > 64:
        return "a category name is at most 64 characters"
    if not FOLDER_NAME_PATTERN.match(name):
        return "use letters, digits, '_' and '-' only, starting with a letter or digit"
    return None


def _plausible(year: int, *, now: datetime | None = None) -> bool:
    return 1900 <= year <= (now or datetime.now(UTC)).year + 1


def guess_year(file_name: str, page_text: str = "", meta_text: str = "", *, now: datetime | None = None) -> int | None:
    """Best local guess of a paper's publication year, or ``None`` when nothing plausible is found.

    Args:
        file_name: The uploaded file name.
        page_text: Text of the first pages (the probe's ``page_text``).
        meta_text: The PDF Info values (the probe's ``meta_text``), which may carry a creation date.
        now: Clock for the plausibility bound (tests).

    Returns:
        The year, preferring the file name, then printed publication or copyright years (the latest
        printed year wins, since a paper is received before it is published), then the creation date.
    """
    named = [int(y) for y in _NAME_YEAR.findall(file_name) if _plausible(int(y), now=now)]
    if named:
        return named[0]
    head = page_text[:6000]
    printed = [int(y) for y in _PUBLISHED.findall(head) + _COPYRIGHT.findall(head) if _plausible(int(y), now=now)]
    if printed:
        return max(printed)
    created = [int(y) for y in _CREATED.findall(meta_text) if _plausible(int(y), now=now)]
    return created[0] if created else None


def year_folder(year: int | None) -> str:
    """The sub-folder name of ``year`` (``unknown`` when it is ``None``)."""
    return str(year) if year is not None else UNKNOWN_YEAR


__all__ = ("FOLDER_NAME_PATTERN", "category_problem", "UNKNOWN_YEAR", "guess_year", "is_valid_folder_name", "year_folder")

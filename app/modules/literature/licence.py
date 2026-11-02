"""Licensing and open-access metadata for one paper (addendum A7 T29, B2).

Facts come from three places and are kept apart, never merged into a verdict:

* **OpenAlex**: ``open_access.is_oa`` / ``oa_status`` and the ``license`` of the primary and
  best open-access locations (the host venue's licence);
* **Crossref**: the ``license`` array of a Crossref work message (optional, injected by the caller);
* **the PDF's own notice**: a licence or copyright line found in the first pages.

Every intake record carries a :class:`LicenceInfo`; when nothing is known every field is
the explicit ``unknown`` rather than absent. :func:`licence_status` is the accessor H5
publication checks read; this module deliberately does not refuse an export itself.
No network access happens here: callers pass records they already fetched.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
import re
from typing import Any

UNKNOWN = "unknown"
#: ``licence_status`` values. ``licensed``: a registry names a licence; ``notice_only``: only the
#: PDF's own notice does; ``unknown``: nothing names one (open access without a licence is still unknown).
STATUSES: tuple[str, ...] = ("licensed", "notice_only", UNKNOWN)

_CC = re.compile(r"\bcreative\s+commons\b[^.\n]{0,80}|\bCC[\s-]BY(?:[\s-](?:NC|ND|SA))*(?:[\s-]\d\.\d)?\b|\bCC0\b",
                 re.IGNORECASE)
_RIGHTS = re.compile(r"(?:©|\(c\)|copyright)[^\n]{0,120}?all\s+rights\s+reserved|all\s+rights\s+reserved",
                     re.IGNORECASE)


def _text(value: object) -> str:
    return str(value).strip() if value not in (None, "") else ""


def pdf_notice(text: str) -> str:
    """The first licence or rights line in ``text`` (<= 200 chars), else ``""``."""
    for pattern in (_CC, _RIGHTS):
        match = pattern.search(text or "")
        if match:
            return " ".join(match.group(0).split())[:200]
    return ""


@dataclass
class LicenceInfo:
    """Licence and open-access facts for one paper; ``unknown`` is explicit, never ``None``."""

    license: str = UNKNOWN            # OpenAlex primary-location licence id (e.g. ``cc-by``)
    is_oa: bool | None = None         # ``None`` = the registry did not say
    oa_status: str = UNKNOWN          # gold | green | hybrid | bronze | diamond | closed | unknown
    host_venue: str = UNKNOWN         # name of the venue hosting the licensed location
    host_venue_license: str = UNKNOWN  # licence of the best open-access location
    crossref_licenses: list[str] = field(default_factory=list)  # Crossref licence URLs
    pdf_notice: str = UNKNOWN         # the PDF's own licence or rights line
    sources: list[str] = field(default_factory=list)            # which of openalex|crossref|pdf contributed

    @property
    def licence_status(self) -> str:
        """``licensed`` | ``notice_only`` | ``unknown`` (see :data:`STATUSES`)."""
        return licence_status(self)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form including the derived status."""
        data = asdict(self)
        data["licence_status"] = self.licence_status
        return data


def licence_status(info: LicenceInfo | Mapping[str, Any] | None) -> str:
    """The accessor for a record's licence standing; a missing record is ``unknown``."""
    if info is None:
        return UNKNOWN
    get = info.get if isinstance(info, Mapping) else lambda k, d=None: getattr(info, k, d)
    if any(_known(get(k)) for k in ("license", "host_venue_license")) or get("crossref_licenses"):
        return "licensed"
    if _known(get("pdf_notice")):
        return "notice_only"
    return UNKNOWN


def _known(value: object) -> bool:
    return bool(value) and value != UNKNOWN


def from_openalex(work: Mapping[str, Any] | None) -> LicenceInfo:
    """Licence facts from an OpenAlex work record (any subset of fields may be missing)."""
    info = LicenceInfo()
    if not work:
        return info
    primary = work.get("primary_location") or {}
    best = work.get("best_oa_location") or {}
    oa = work.get("open_access") or {}
    info.license = _text(primary.get("license")) or UNKNOWN
    if "is_oa" in oa:
        info.is_oa = bool(oa.get("is_oa"))
    info.oa_status = _text(oa.get("oa_status")) or UNKNOWN
    venue = (best.get("source") or primary.get("source") or {}).get("display_name")
    info.host_venue = _text(venue) or UNKNOWN
    info.host_venue_license = _text(best.get("license")) or UNKNOWN
    if any((info.license != UNKNOWN, info.is_oa is not None, info.oa_status != UNKNOWN,
            info.host_venue_license != UNKNOWN)):
        info.sources.append("openalex")
    return info


def crossref_licenses(message: Mapping[str, Any] | None) -> list[str]:
    """Licence URLs in a Crossref work message (``message["license"]``), de-duplicated in order."""
    urls: list[str] = []
    for entry in (message or {}).get("license") or []:
        url = _text(entry.get("URL")) if isinstance(entry, Mapping) else ""
        if url and url not in urls:
            urls.append(url)
    return urls


def collect(work: Mapping[str, Any] | None = None, *, crossref: Mapping[str, Any] | None = None,
            pdf_text: str = "") -> LicenceInfo:
    """Combine OpenAlex, Crossref and the PDF's own notice into one record."""
    info = from_openalex(work)
    urls = crossref_licenses(crossref)
    if urls:
        info.crossref_licenses = urls
        info.sources.append("crossref")
    notice = pdf_notice(pdf_text)
    if notice:
        info.pdf_notice = notice
        info.sources.append("pdf")
    return info


__all__ = ("LicenceInfo", "STATUSES", "UNKNOWN", "collect", "crossref_licenses", "from_openalex",
           "licence_status", "pdf_notice")

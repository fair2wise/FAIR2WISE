"""Shared result vocabulary for the paper-figure pipeline: frames, locators, uncertainty.

Every figure/panel result carries a contracts :class:`SourceLocator` whose bbox is in
**PDF points with a top-left origin** (the frame the PDF reader reports), plus the page size
and the transform that maps the internal render back to the page. Nothing here holds
image bytes, store paths or asset URLs (D19): rendering happens in-process and the
pixels never leave the extractor.

Rewritten for F2W (no donor equivalent); the origin/units rules follow the donor
``figure_extraction/geometry.py`` (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from ..contracts.errors import ContractError, ErrorCode
from ..contracts.evidence import EvidenceRef, MatchType, SourceLocator

ORIGIN = "top-left"
UNITS = "pt"
EXTRACTION_VERSION = "t09-figures-1"

Box = tuple[float, float, float, float]

#: Uncertainty codes that make every value of the affected figure/panel withheld.
BLOCKING = frozenset({
    "page_rotated",
    "figure_region_unlocated",
    "panel_count_mismatch",
    "panel_assignment_ambiguous",
})


class VisionUnavailable(ContractError):
    """A required capability (pypdfium2, OCR engine, ...) is missing: explicit and retryable."""

    def __init__(self, capability: str, detail: str = "") -> None:
        super().__init__(ErrorCode.UNAVAILABLE, f"{capability} is not available{': ' + detail if detail else ''}",
                         retryable=True)
        self.capability = capability


@dataclass(frozen=True)
class Uncertainty:
    """An ambiguity surfaced instead of resolved. ``blocking`` withholds the affected values."""

    code: str
    detail: str = ""
    panel: str | None = None
    blocking: bool = False

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return {"code": self.code, "detail": self.detail, "panel": self.panel, "blocking": self.blocking}


def box4(value: tuple[float, ...]) -> Box:
    """Normalise to ``(x0, y0, x1, y1)`` with ``x0 <= x1`` and ``y0 <= y1``, rounded to 0.01 pt."""
    x0, y0, x1, y1 = (float(v) for v in value)
    return (round(min(x0, x1), 2), round(min(y0, y1), 2), round(max(x0, x1), 2), round(max(y0, y1), 2))


def locator(page: int | None, figure: str | None = None, panel: str | None = None,
            bbox: Box | None = None, section: str | None = None) -> SourceLocator:
    """A :class:`SourceLocator` in the pipeline's fixed frame (units ``pt``, origin ``top-left``)."""
    return SourceLocator(
        page=page, section=section, figure=figure, panel=panel,
        bbox=box4(bbox) if bbox else None,
        bbox_units=UNITS if bbox else None, bbox_origin=ORIGIN if bbox else None,
    )


def evidence(work_id: str, source_sha256: str, loc: SourceLocator, kind: str, value: object = "") -> EvidenceRef:
    """A derived-finding evidence record (no quote: the value is derived, not quoted)."""
    key = f"{work_id}|{source_sha256}|{kind}|{loc.model_dump_json()}|{value}"
    digest = hashlib.sha256(key.encode()).hexdigest()
    return EvidenceRef(
        evidence_id=f"ev-{digest[:24]}", work_id=work_id, source_version=source_sha256,
        source_sha256=source_sha256 if len(source_sha256) == 64 else None, locator=loc,
        match_type=MatchType.NONE, parser_revision=EXTRACTION_VERSION,
    )


__all__ = (
    "BLOCKING", "Box", "EXTRACTION_VERSION", "ORIGIN", "UNITS", "Uncertainty", "VisionUnavailable",
    "box4", "evidence", "locator",
)

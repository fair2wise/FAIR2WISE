"""Evidence references: where a claim or value comes from in a source version.

Evidence links a statement to a source location. It is distinct from execution
provenance (which tool/model produced it). A quote is only labelled ``exact``
when it matches the retained source version (plan section 4.8, D19).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MatchType(str, Enum):
    """How ``quote`` relates to the source text.

    ``NORMALIZED`` means the quote equals the source after the recorded
    normalization profile (whitespace and hyphenation only). It may be shown as
    verbatim only under that profile (docs/adr/content-disclosure.md); a
    ``PARAPHRASE`` is never shown as a quotation.
    """

    EXACT = "exact"
    NORMALIZED = "normalized"
    PARAPHRASE = "paraphrase"
    NONE = "none"


class SourceLocator(BaseModel):
    """A location inside one source version.

    Attributes:
        page: 1-based page number, when the source is paginated.
        section: Section heading or identifier.
        char_span: ``(start, end)`` character offsets in the parser output.
        figure: Figure identifier, for figure evidence.
        panel: Panel label within ``figure``.
        bbox: ``(x0, y0, x1, y1)`` in ``bbox_units`` with origin ``bbox_origin``.
        bbox_units: Units for ``bbox`` (for example ``"pt"``).
        bbox_origin: Coordinate origin (for example ``"top-left"``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    page: Optional[int] = Field(default=None, ge=1)
    section: Optional[str] = None
    char_span: Optional[Tuple[int, int]] = None
    figure: Optional[str] = None
    panel: Optional[str] = None
    bbox: Optional[Tuple[float, float, float, float]] = None
    bbox_units: Optional[str] = None
    bbox_origin: Optional[str] = None

    @model_validator(mode="after")
    def _bbox_has_frame(self) -> "SourceLocator":
        """Require units and origin whenever a bounding box is given."""
        if self.bbox is not None and not (self.bbox_units and self.bbox_origin):
            raise ValueError("bbox requires bbox_units and bbox_origin")
        if self.char_span is not None and self.char_span[0] > self.char_span[1]:
            raise ValueError("char_span start must not exceed end")
        return self


class EvidenceRef(BaseModel):
    """A citation-grade pointer from a claim or value to its source.

    Attributes:
        evidence_id: Stable identifier for this evidence record.
        work_id: Canonical Work identifier.
        source_version: Identifier of the exact source version (for example a file hash).
        source_sha256: SHA-256 of the source bytes the locator refers to.
        locator: Where in the source the evidence sits.
        match_type: Relationship between ``quote`` and the source text.
        quote: Bounded excerpt or value as it appears in the source; internal
            until projected through the disclosure policy.
        parser_revision: Parser/extraction revision that produced the locator.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    work_id: str
    source_version: str
    source_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    locator: SourceLocator
    match_type: MatchType = MatchType.NONE
    quote: Optional[str] = None
    parser_revision: Optional[str] = None

    @model_validator(mode="after")
    def _quote_matches_type(self) -> "EvidenceRef":
        """An exact or normalized match must carry the quoted text."""
        if self.match_type in (MatchType.EXACT, MatchType.NORMALIZED) and not self.quote:
            raise ValueError(f"match_type {self.match_type.value} requires a quote")
        return self

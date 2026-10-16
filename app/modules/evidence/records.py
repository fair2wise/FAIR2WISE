"""Paper evidence and reported-quantity records (E-09 records half).

These are *candidate* records. They tie one reported value or assertion to an exact
location in one source version (:class:`~app.modules.contracts.evidence.EvidenceRef`)
and keep, side by side and never merged:

- the evidence-source axis (:class:`EvidenceSource`: reported / cited / derived /
  assumed), which says *how the paper relates to the value*. It is not a scientific
  entity class and never replaces one;
- the measured uncertainty the paper states (:class:`MeasuredUncertainty`), which is
  a property of the measurement;
- the extraction confidence a model or rule assigned, which is a property of the
  extraction and says nothing about the measurement;
- the raw value and unit as printed, the normalized value, and the conversion
  provenance (:class:`~app.modules.evidence.units.NormalizedQuantity`);
- explicit missing-data markers instead of invented defaults. Nothing here injects
  beamline geometry, energies or detector settings.

Simulation and fitting provenance stay in FORGE.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/paper_simulation.py
(evidence rows of ``_record_evidence`` / ``_record_reported_quantities``) (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from ..contracts.evidence import EvidenceRef
from .units import NormalizedQuantity

#: Record format version of :class:`EvidenceRecord` / :class:`QuantityRecord`.
#: 2 (T06B): quantity records carry the proposed tag, the tag reasons and any derivation.
RECORD_VERSION = 2


class EvidenceSource(str, Enum):
    """How the source relates to a value: its own result, a citation, a derivation, an assumption.

    H3 F2: every value is captured with one of these tags and none is withheld
    for its tag. Only ``reported`` is the paper's own measurement; a ``cited`` or
    ``assumed`` value is never presented as one (:attr:`presented_as`).
    """

    REPORTED = "reported"
    CITED = "cited"
    DERIVED = "derived"
    ASSUMED = "assumed"

    @property
    def is_paper_measurement(self) -> bool:
        """Whether the value may be shown as the paper's own measurement (``reported`` only)."""
        return self is EvidenceSource.REPORTED

    @property
    def presented_as(self) -> str:
        """Fixed display marker for the tag; consumers show this, not "measured", for non-reported values."""
        return _PRESENTED_AS[self]


_PRESENTED_AS = {
    EvidenceSource.REPORTED: "reported by this paper",
    EvidenceSource.CITED: "cited from other work, not this paper's measurement",
    EvidenceSource.DERIVED: "derived from values this paper reports",
    EvidenceSource.ASSUMED: "assumed, not measured",
}


@dataclass(frozen=True)
class TagDecision:
    """The evidence-source tag a value ends up with, and why (H3 F2/F8).

    Attributes:
        proposed: Tag the strategy proposed.
        tag: Tag after the validation rules. Rules only move a tag *away* from
            ``reported``; nothing is ever upgraded to ``reported``.
        reasons: Rule ids that changed or confirmed the tag.
        derivation: Provenance when the value was recognised as derived (``d = 2*pi/q``).
    """

    proposed: EvidenceSource
    tag: EvidenceSource
    reasons: Tuple[str, ...] = ()
    derivation: Optional[Dict[str, Any]] = None

    @property
    def retagged(self) -> bool:
        """Whether the rules changed the proposed tag."""
        return self.tag is not self.proposed

    def to_dict(self) -> Dict[str, Any]:
        """JSON-ready form."""
        return {"proposed": self.proposed.value, "tag": self.tag.value, "reasons": list(self.reasons),
                "derivation": dict(self.derivation) if self.derivation else None,
                "paper_measurement": self.tag.is_paper_measurement, "presented_as": self.tag.presented_as}


class UncertaintyKind(str, Enum):
    """What a stated uncertainty means."""

    STANDARD_DEVIATION = "standard_deviation"
    STANDARD_ERROR = "standard_error"
    INTERVAL = "interval"
    UNKNOWN = "unknown"


class MissingData(str, Enum):
    """Closed set of explicit absence markers on an evidence/quantity record."""

    QUOTE_ABSENT = "quote_absent"
    QUOTE_NOT_LOCATED = "quote_not_located"
    QUOTE_AMBIGUOUS = "quote_ambiguous"
    SECTION_UNKNOWN = "section_unknown"
    SECTION_LABEL_MISMATCH = "section_label_mismatch"
    PAGE_UNKNOWN = "page_unknown"
    UNIT_ABSENT = "unit_absent"
    UNIT_NOT_CONVERTIBLE = "unit_not_convertible"
    UNCERTAINTY_NOT_STATED = "uncertainty_not_stated"
    PARSER_REVISION_UNKNOWN = "parser_revision_unknown"


@dataclass(frozen=True)
class MeasuredUncertainty:
    """An uncertainty the source states for a measurement (not extraction confidence).

    Attributes:
        value: Magnitude in the reported value's units, or ``None`` when not stated.
        kind: What the magnitude means.
    """

    value: Optional[float] = None
    kind: UncertaintyKind = UncertaintyKind.UNKNOWN

    def __post_init__(self) -> None:
        if self.value is not None and self.value < 0:
            raise ValueError("a measured uncertainty must be non-negative")


@dataclass(frozen=True)
class ReportedQuantity:
    """One value a strategy says the source reports, before location and persistence.

    Attributes:
        name: Donor/strategy quantity label (not an F2W slot until H3 approves a mapping).
        raw_value: Number as printed, or ``None`` for a qualitative-only observation.
        raw_unit: Unit as printed (``""`` when absent).
        quote: Sentence the value was read from, verbatim as the strategy returned it.
        location_label: The strategy's region label ("results", "Table 1"); a pointer only.
        evidence_source: Evidence-source axis value.
        uncertainty: Stated measurement uncertainty, if any.
        extraction_confidence: Confidence of the extraction step in [0, 1], if any.
        sample_id / processing_state: Qualifiers as printed.
        qualitative_value: Non-numeric value as printed.
        caveat: Free-text caveat from the strategy.
    """

    name: str
    raw_value: Optional[float]
    raw_unit: str = ""
    quote: str = ""
    location_label: str = ""
    evidence_source: EvidenceSource = EvidenceSource.REPORTED
    uncertainty: MeasuredUncertainty = field(default_factory=MeasuredUncertainty)
    extraction_confidence: Optional[float] = None
    sample_id: str = ""
    processing_state: str = ""
    qualitative_value: str = ""
    caveat: str = ""


@dataclass(frozen=True)
class EvidenceRecord:
    """One located (or explicitly unlocated) quotation from one source version.

    Attributes:
        ref: Shared-contract evidence reference (work, version, locator, match type).
        section_id: Structural id of the containing section, when known.
        section_label: The strategy's own region label, kept beside the resolved section.
        located_by: ``label`` (named section contained it), ``any_section`` (text fallback)
            or ``none``.
        missing: Explicit absence markers.
        parser: Parser metadata (``parser``, ``parser_version``, ``parser_key``).
        strategy: Extraction strategy id and version that proposed the quote.
        caveat: Strategy caveat, bounded.
        status: Always ``candidate``: nothing here is reviewed or published.
    """

    ref: EvidenceRef
    section_id: Optional[str]
    section_label: str
    located_by: str
    missing: Tuple[MissingData, ...] = ()
    parser: Dict[str, Any] = field(default_factory=dict)
    strategy: str = ""
    caveat: str = ""
    status: str = "candidate"
    record_version: int = RECORD_VERSION

    @property
    def verified_quote_match(self) -> bool:
        """The quote was found in the stored section text (exact or normalized)."""
        return self.located_by != "none"

    def to_dict(self) -> Dict[str, Any]:
        """JSON-ready form."""
        return {
            "record_version": self.record_version, "status": self.status,
            "ref": self.ref.model_dump(mode="json"), "section_id": self.section_id,
            "section_label": self.section_label, "located_by": self.located_by,
            "verified_quote_match": self.verified_quote_match,
            "missing": [m.value for m in self.missing], "parser": dict(self.parser),
            "strategy": self.strategy, "caveat": self.caveat,
        }


@dataclass(frozen=True)
class QuantityRecord:
    """One reported value with its evidence links, kept as a candidate.

    Attributes:
        quantity_id: Deterministic id (``pub|name|value|unit``, donor-compatible).
        work_id: Canonical Work id.
        name: Strategy quantity label.
        quantity: Raw + normalized value with conversion provenance; ``None`` when
            the observation is qualitative only.
        evidence_source: Evidence-source axis value.
        uncertainty: Stated measurement uncertainty.
        extraction_confidence: Extraction confidence; never mixed with ``uncertainty``.
        evidence_ids: Evidence records supporting this value.
        missing: Explicit absence markers.
        proposed_evidence_source: Tag the strategy proposed, when validation changed it.
        tag_reasons: Validation rules behind ``evidence_source`` (T06B).
        derivation: How a ``derived`` value follows from printed values, if recognised.
    """

    quantity_id: str
    work_id: str
    name: str
    quantity: Optional[NormalizedQuantity]
    evidence_source: EvidenceSource
    uncertainty: MeasuredUncertainty
    extraction_confidence: Optional[float]
    evidence_ids: Tuple[str, ...] = ()
    sample_id: str = ""
    processing_state: str = ""
    qualitative_value: str = ""
    missing: Tuple[MissingData, ...] = ()
    proposed_evidence_source: Optional[EvidenceSource] = None
    tag_reasons: Tuple[str, ...] = ()
    derivation: Optional[Dict[str, Any]] = None
    status: str = "candidate"
    record_version: int = RECORD_VERSION

    def to_dict(self) -> Dict[str, Any]:
        """JSON-ready form."""
        return {
            "record_version": self.record_version, "status": self.status,
            "quantity_id": self.quantity_id, "work_id": self.work_id, "name": self.name,
            "quantity": self.quantity.provenance() | {"value": self.quantity.value} if self.quantity else None,
            "evidence_source": self.evidence_source.value,
            "uncertainty": {"value": self.uncertainty.value, "kind": self.uncertainty.kind.value},
            "extraction_confidence": self.extraction_confidence,
            "evidence_ids": list(self.evidence_ids), "sample_id": self.sample_id,
            "processing_state": self.processing_state, "qualitative_value": self.qualitative_value,
            "missing": [m.value for m in self.missing],
            "proposed_evidence_source": (self.proposed_evidence_source or self.evidence_source).value,
            "tag_reasons": list(self.tag_reasons),
            "derivation": dict(self.derivation) if self.derivation else None,
            "paper_measurement": self.evidence_source.is_paper_measurement,
            "presented_as": self.evidence_source.presented_as,
        }


__all__ = [
    "RECORD_VERSION", "EvidenceRecord", "EvidenceSource", "MeasuredUncertainty", "MissingData",
    "QuantityRecord", "ReportedQuantity", "TagDecision", "UncertaintyKind",
]

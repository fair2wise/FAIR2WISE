"""Paper evidence services for the FORGE-to-F2W migration (task T06).

Quote location (E-03), lexical source support (E-04), paper-reported units (E-05)
and candidate evidence/quantity persistence over parsed source versions (E-09), plus the
read-only literature-evidence bundle consumers cite (T15, :mod:`.bundle`).

Everything here produces *candidates*. Nothing writes to a scientific graph,
merges entities or publishes. Evidence-source tags (H3 F2) are decided by the
T06B validation stage (:mod:`app.modules.extraction.record_validation`).
Importing this package has no side effects.
"""

from __future__ import annotations

from .bundle import BundledEvidence, BundledQuantity, LiteratureEvidenceBundle, build_bundle, bundle_work_ids
from .grounding import SUPPORT_VERDICTS, source_support
from .persistence import (
    CandidateFileEvidenceSink, EvidenceSink, InMemoryEvidenceSink, build_evidence,
    locate_in_any_section, record_evidence, record_reported_quantities, stable_evidence_id,
)
from .quote_location import NORMALIZATION_PROFILE, QuoteLocation, locate_quote
from .records import (
    EvidenceRecord, EvidenceSource, MeasuredUncertainty, MissingData, QuantityRecord,
    ReportedQuantity, TagDecision, UncertaintyKind,
)
from .units import NormalizedQuantity, normalize_quantity

__all__ = [
    "NORMALIZATION_PROFILE", "SUPPORT_VERDICTS", "BundledEvidence", "BundledQuantity", "CandidateFileEvidenceSink",
    "EvidenceRecord", "EvidenceSink", "EvidenceSource", "InMemoryEvidenceSink", "LiteratureEvidenceBundle",
    "MeasuredUncertainty", "MissingData", "NormalizedQuantity", "QuantityRecord", "QuoteLocation",
    "ReportedQuantity", "TagDecision", "UncertaintyKind", "build_bundle", "build_evidence", "bundle_work_ids",
    "locate_in_any_section", "locate_quote", "normalize_quantity", "record_evidence",
    "record_reported_quantities", "source_support", "stable_evidence_id",
]

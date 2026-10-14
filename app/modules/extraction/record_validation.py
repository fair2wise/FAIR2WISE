"""Deliberate evidence-validation stage for extraction proposals (E-02, wired with E-03/E-04/E-05).

Two layers:

1. The donor's parser-independent records and :func:`validate_experiment_chunk`,
   kept behaviour-compatible: reject invented evidence (quote not in the source)
   and dangling sample/measurement references. ``status`` on an observation is the
   evidence-source axis (reported/cited/derived/assumed); it is not a scientific
   class. Generic string ``conditions`` are not typed RSoXS slots.
2. F2W document-aware validation over a parsed source version: every retained
   record gets an :class:`~app.modules.contracts.evidence.EvidenceRef` (work,
   source version, section/page, character span, match type, parser revision).
   Records are *withheld*, with a reason, when the quote is not located or is
   ambiguous, a value/unit is not lexically supported by its quote, a unit
   contradicts the quantity's dimension, a "reported" status sits in a sentence
   attributing the claim to other work, or an assumed instrument-geometry value is
   offered as paper evidence.

Passing validation means the text supports the record lexically. It is not
entailment and not scientific approval. Nothing here maps records onto new F2W
schema slots, merges entities or publishes; those wait for H3
(:data:`app.modules.extraction.H3_GATES`).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/scientific_records.py (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from ..contracts.evidence import EvidenceRef
from ..evidence.grounding import source_support
from ..evidence.persistence import EvidenceSink, build_evidence, record_reported_quantities, section_text_for
from ..evidence.records import EvidenceRecord, EvidenceSource, MissingData, ReportedQuantity
from ..evidence.units import dimension_mismatch
from ..literature.parsed_document import ParsedDocument
from .contract import ExtractionResult
from .strategies.paper_structure import is_attributed, sentence_around


class StrictRecord(BaseModel):
    """Base for donor records: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class EvidenceLocator(StrictRecord):
    """An exact source span supporting one scientific assertion (as proposed)."""

    section: str
    quote: str
    page: Optional[int]
    block_id: Optional[str]
    table_id: Optional[str]
    figure_panel: Optional[str]


class SampleRecord(StrictRecord):
    """One paper-local physical sample, distinct from a molecular identity."""

    sample_id: str
    label: str
    composition: List[str]
    phase: Optional[str]
    processing_state: Optional[str]
    preparation: List[str]
    evidence: EvidenceLocator


class MeasurementRecord(StrictRecord):
    """One measurement associated with explicit paper-local samples."""

    measurement_id: str
    sample_ids: List[str]
    technique: str
    instrument: Optional[str]
    conditions: Dict[str, str]
    evidence: EvidenceLocator


class ScientificObservation(StrictRecord):
    """One reported, cited, derived or assumed result as written in the paper."""

    observation_id: str
    measurement_id: Optional[str]
    sample_ids: List[str]
    quantity_label: str
    value: Optional[float]
    unit: str
    qualitative_value: Optional[str]
    uncertainty: Optional[float] = Field(ge=0)
    uncertainty_kind: Literal["standard_deviation", "standard_error", "interval", "unknown"]
    status: Literal["reported", "cited", "derived", "assumed"]
    caveat: str
    evidence: EvidenceLocator


class ExperimentChunk(StrictRecord):
    """Model output for one bounded source chunk before document reconciliation."""

    samples: List[SampleRecord]
    measurements: List[MeasurementRecord]
    observations: List[ScientificObservation]
    confidence: float = Field(ge=0, le=1)


class ValidationIssue(StrictRecord):
    """Why a proposed record cannot enter the accepted view."""

    record_type: str
    record_id: str
    reason: str
    payload: Dict[str, Any]


class ValidatedExperimentChunk(StrictRecord):
    """Accepted records plus explicit rejected/uncertain proposals."""

    samples: List[SampleRecord]
    measurements: List[MeasurementRecord]
    observations: List[ScientificObservation]
    issues: List[ValidationIssue]
    confidence: float = Field(ge=0, le=1)


def _located(quote: str, source: str) -> bool:
    return bool(quote.strip()) and " ".join(quote.split()).casefold() in " ".join(source.split()).casefold()


def validate_experiment_chunk(proposal: ExperimentChunk, source: str) -> ValidatedExperimentChunk:
    """Reject invented evidence and dangling sample/measurement relationships (donor rules)."""
    issues: List[ValidationIssue] = []
    samples: List[SampleRecord] = []
    for sample in proposal.samples:
        if _located(sample.evidence.quote, source):
            samples.append(sample)
        else:
            issues.append(ValidationIssue(record_type="sample", record_id=sample.sample_id,
                                          reason="evidence_quote_not_found", payload=sample.model_dump()))
    sample_ids = {s.sample_id for s in samples}
    measurements: List[MeasurementRecord] = []
    for measurement in proposal.measurements:
        errors = []
        if not _located(measurement.evidence.quote, source):
            errors.append("evidence_quote_not_found")
        if set(measurement.sample_ids) - sample_ids:
            errors.append("unknown_sample_reference")
        if errors:
            issues.append(ValidationIssue(record_type="measurement", record_id=measurement.measurement_id,
                                          reason="+".join(errors), payload=measurement.model_dump()))
        else:
            measurements.append(measurement)
    measurement_ids = {m.measurement_id for m in measurements}
    observations: List[ScientificObservation] = []
    for observation in proposal.observations:
        errors = []
        if not _located(observation.evidence.quote, source):
            errors.append("evidence_quote_not_found")
        if set(observation.sample_ids) - sample_ids:
            errors.append("unknown_sample_reference")
        if observation.measurement_id and observation.measurement_id not in measurement_ids:
            errors.append("unknown_measurement_reference")
        if observation.value is None and not (observation.qualitative_value or "").strip():
            errors.append("observation_has_no_value")
        if errors:
            issues.append(ValidationIssue(record_type="observation", record_id=observation.observation_id,
                                          reason="+".join(errors), payload=observation.model_dump()))
        else:
            observations.append(observation)
    return ValidatedExperimentChunk(samples=samples, measurements=measurements, observations=observations,
                                    issues=issues, confidence=proposal.confidence)


# --- F2W: document-aware validation ---------------------------------------

#: Instrument-geometry quantities that are never paper evidence when *assumed*.
INSTRUMENT_GEOMETRY_LABELS = frozenset({
    "beam_energy", "photon_energy", "sample_detector_distance", "detector_distance", "pixel_size",
    "beam_center", "beam_centre", "incidence_angle", "wavelength",
})


def _evidence_problems(evidence: Optional[EvidenceRecord]) -> List[str]:
    if evidence is None or MissingData.QUOTE_ABSENT in evidence.missing:
        return ["missing_quote"]
    if MissingData.QUOTE_NOT_LOCATED in evidence.missing:
        return ["evidence_quote_not_found"]
    if MissingData.QUOTE_AMBIGUOUS in evidence.missing:
        return ["evidence_quote_ambiguous"]
    return []


def _quantity_problems(
    document: ParsedDocument, evidence: EvidenceRecord, *, label: str, value: Optional[float], unit: str,
    status: EvidenceSource, qualitative: str,
) -> List[str]:
    problems: List[str] = []
    quote = evidence.ref.quote or ""
    if value is None and not qualitative.strip():
        problems.append("observation_has_no_value")
    if value is not None:
        verdict = source_support(section_text_for(document, evidence.section_id), quote, value, unit)
        if verdict != "source_supported":
            problems.append(verdict)
        if unit and dimension_mismatch(label, unit):
            problems.append("unit_dimension_mismatch")
    if label.strip().lower() in INSTRUMENT_GEOMETRY_LABELS and status is EvidenceSource.ASSUMED:
        problems.append("assumed_instrument_geometry_not_paper_evidence")
    if status is EvidenceSource.REPORTED and evidence.ref.locator.char_span:
        text = section_text_for(document, evidence.section_id)
        start, end = evidence.ref.locator.char_span
        if is_attributed(sentence_around(text, start, end)):
            problems.append("reported_status_in_attributed_sentence")
    return problems


@dataclass(frozen=True)
class WithheldRecord:
    """A proposal kept out of the accepted view, with every reason and its evidence attempt."""

    record_type: str
    record_id: str
    reasons: Tuple[str, ...]
    payload: Dict[str, Any]
    evidence: Optional[Dict[str, Any]] = None


@dataclass
class DocumentValidation:
    """Accepted records with their evidence refs, plus withheld proposals.

    ``mapping_applied`` is always ``False`` before H3: records are validated
    candidates, not F2W schema instances.
    """

    samples: List[SampleRecord] = field(default_factory=list)
    measurements: List[MeasurementRecord] = field(default_factory=list)
    observations: List[ScientificObservation] = field(default_factory=list)
    evidence: Dict[str, EvidenceRef] = field(default_factory=dict)
    withheld: List[WithheldRecord] = field(default_factory=list)
    confidence: Optional[float] = None
    mapping_applied: bool = False


def validate_against_document(
    proposal: ExperimentChunk,
    document: ParsedDocument,
    *,
    work_id: str,
    pub_path: str,
    strategy: str = "",
) -> DocumentValidation:
    """Validate a proposal against the stored sections of one source version.

    Evidence keys in the result are ``"<record_type>:<record_id>"``.
    """
    out = DocumentValidation(confidence=proposal.confidence)

    def locate(locator: EvidenceLocator) -> Optional[EvidenceRecord]:
        return build_evidence(document, work_id=work_id, pub_path=pub_path, quote=locator.quote,
                              location_label=locator.section, strategy=strategy)

    def withhold(kind: str, rid: str, reasons: Sequence[str], payload: Dict[str, Any],
                 evidence: Optional[EvidenceRecord]) -> None:
        out.withheld.append(WithheldRecord(kind, rid, tuple(reasons), payload,
                                           evidence.to_dict() if evidence else None))

    for sample in proposal.samples:
        evidence = locate(sample.evidence)
        problems = _evidence_problems(evidence)
        if problems:
            withhold("sample", sample.sample_id, problems, sample.model_dump(), evidence)
        else:
            out.samples.append(sample)
            out.evidence[f"sample:{sample.sample_id}"] = evidence.ref  # type: ignore[union-attr]
    sample_ids = {s.sample_id for s in out.samples}
    for measurement in proposal.measurements:
        evidence = locate(measurement.evidence)
        problems = _evidence_problems(evidence)
        if set(measurement.sample_ids) - sample_ids:
            problems.append("unknown_sample_reference")
        if problems:
            withhold("measurement", measurement.measurement_id, problems, measurement.model_dump(), evidence)
        else:
            out.measurements.append(measurement)
            out.evidence[f"measurement:{measurement.measurement_id}"] = evidence.ref  # type: ignore[union-attr]
    measurement_ids = {m.measurement_id for m in out.measurements}
    for observation in proposal.observations:
        evidence = locate(observation.evidence)
        problems = _evidence_problems(evidence)
        if set(observation.sample_ids) - sample_ids:
            problems.append("unknown_sample_reference")
        if observation.measurement_id and observation.measurement_id not in measurement_ids:
            problems.append("unknown_measurement_reference")
        if not problems and evidence is not None:
            problems += _quantity_problems(
                document, evidence, label=observation.quantity_label, value=observation.value,
                unit=observation.unit, status=EvidenceSource(observation.status),
                qualitative=observation.qualitative_value or "",
            )
        if problems:
            withhold("observation", observation.observation_id, problems, observation.model_dump(), evidence)
        else:
            out.observations.append(observation)
            out.evidence[f"observation:{observation.observation_id}"] = evidence.ref  # type: ignore[union-attr]
    return out


def validate_reported_quantities(
    quantities: Sequence[ReportedQuantity], document: ParsedDocument, *, work_id: str, pub_path: str,
    strategy: str = "",
) -> Tuple[List[Tuple[ReportedQuantity, EvidenceRecord]], List[WithheldRecord]]:
    """Split strategy observations into (accepted with evidence, withheld with reasons)."""
    accepted: List[Tuple[ReportedQuantity, EvidenceRecord]] = []
    withheld: List[WithheldRecord] = []
    for index, quantity in enumerate(quantities):
        evidence = build_evidence(document, work_id=work_id, pub_path=pub_path, quote=quantity.quote,
                                  location_label=quantity.location_label, strategy=strategy)
        problems = _evidence_problems(evidence)
        if not problems and evidence is not None:
            problems = _quantity_problems(
                document, evidence, label=quantity.name, value=quantity.raw_value, unit=quantity.raw_unit,
                status=quantity.evidence_source, qualitative=quantity.qualitative_value,
            )
        record_id = f"{index}:{quantity.name}"
        if problems:
            withheld.append(WithheldRecord("quantity", record_id, tuple(problems), _payload(quantity),
                                           evidence.to_dict() if evidence else None))
        else:
            accepted.append((quantity, evidence))  # type: ignore[arg-type]
    return accepted, withheld


def _payload(quantity: ReportedQuantity) -> Dict[str, Any]:
    return {
        "name": quantity.name, "raw_value": quantity.raw_value, "raw_unit": quantity.raw_unit,
        "quote": quantity.quote, "location_label": quantity.location_label,
        "evidence_source": quantity.evidence_source.value,
        "uncertainty": {"value": quantity.uncertainty.value, "kind": quantity.uncertainty.kind.value},
        "extraction_confidence": quantity.extraction_confidence,
    }


@dataclass
class StageReport:
    """Outcome of the validation stage for one extraction result."""

    strategy: str
    work_id: str
    accepted: int
    withheld: List[WithheldRecord]
    written: int
    mapping_applied: bool = False
    published: bool = False


def run_validation_stage(
    result: ExtractionResult, document: ParsedDocument, sink: EvidenceSink, *, pub_path: str,
    prompt_version: str = "",
) -> StageReport:
    """Validate a result's observations and write only the accepted ones as candidates.

    Withheld proposals are returned for review and never reach the sink.
    """
    accepted, withheld = validate_reported_quantities(
        result.observations, document, work_id=result.work_id, pub_path=pub_path, strategy=result.strategy,
    )
    written = record_reported_quantities(
        sink, document, [q for q, _ in accepted], work_id=result.work_id, pub_path=pub_path,
        strategy=f"{result.strategy}@{result.strategy_version}", prompt_version=prompt_version,
    )
    return StageReport(strategy=result.strategy, work_id=result.work_id, accepted=len(accepted),
                       withheld=withheld, written=written)


__all__ = [
    "INSTRUMENT_GEOMETRY_LABELS", "DocumentValidation", "EvidenceLocator", "ExperimentChunk", "MeasurementRecord",
    "SampleRecord", "ScientificObservation", "StageReport", "StrictRecord", "ValidatedExperimentChunk",
    "ValidationIssue", "WithheldRecord", "run_validation_stage", "validate_against_document",
    "validate_experiment_chunk", "validate_reported_quantities",
]

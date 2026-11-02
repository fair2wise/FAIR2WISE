"""Deliberate evidence-validation stage for extraction proposals (E-02, wired with E-03/E-04/E-05).

Two layers:

1. The donor's parser-independent records and :func:`validate_experiment_chunk`,
   kept behaviour-compatible: reject invented evidence (quote not in the source)
   and dangling sample/measurement references. ``status`` on an observation is the
   evidence-source axis (reported/cited/derived/assumed); it is not a scientific
   class. Generic string ``conditions`` are not typed RSoXS slots.
2. F2W document-aware validation over a parsed source version (H3 F2/F8, T06B):
   every retained record gets an :class:`~app.modules.contracts.evidence.EvidenceRef`
   (work, source version, section/page, character span, match type, parser
   revision) and an evidence-source tag. :data:`RULES` is the rule table. Hard
   gates (F8) *withhold* a value from every graph write: the quote is absent, not
   located or ambiguous; the number and its unit are not both in the quote; the
   unit contradicts the quantity's dimension. Tag rules never withhold (F2): a
   "reported" value in a sentence attributing it to other work becomes ``cited``,
   an assumed instrument value becomes ``assumed`` (never shown as the paper's
   measurement), and a value equal to ``2*pi/x`` for a printed ``x`` becomes
   ``derived`` with its derivation recorded. Tags only move away from
   ``reported``; nothing is upgraded to it.

Passing validation means the text supports the record lexically. It is not
entailment and not scientific approval. The default path stays on the base
schema; mapping onto schema set 0.3.0 happens only on explicit selection
(:mod:`.schema_selection`). Nothing here merges entities (``canonical_merge``,
closed until the C4 rerun) or publishes (``publish``, closed until H5).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/scientific_records.py (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..contracts.evidence import EvidenceRef
from ..evidence.grounding import printed_quantities, source_support
from ..evidence.persistence import EvidenceSink, build_evidence, record_reported_quantities, section_text_for
from ..evidence.records import EvidenceRecord, EvidenceSource, MissingData, ReportedQuantity, TagDecision
from ..evidence.units import base_quantity_name, quantity_unit_mismatch, reciprocal_derivation
from ..literature.parsed_document import ParsedDocument
from .contract import ExtractionResult
from .field_mapping import peak_slot_values
from .strategies.paper_structure import is_attributed, sentence_around


class StrictRecord(BaseModel):
    """Base for donor records: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class EvidenceLocator(StrictRecord):
    """An exact source span supporting one scientific assertion (as proposed)."""

    section: str
    quote: str
    page: int | None
    block_id: str | None
    table_id: str | None
    figure_panel: str | None


class SampleRecord(StrictRecord):
    """One paper-local physical sample, distinct from a molecular identity."""

    sample_id: str
    label: str
    composition: list[str]
    phase: str | None
    processing_state: str | None
    preparation: list[str]
    evidence: EvidenceLocator


class MeasurementRecord(StrictRecord):
    """One measurement associated with explicit paper-local samples."""

    measurement_id: str
    sample_ids: list[str]
    technique: str
    instrument: str | None
    conditions: dict[str, str]
    evidence: EvidenceLocator


class ScientificObservation(StrictRecord):
    """One reported, cited, derived or assumed result as written in the paper."""

    observation_id: str
    measurement_id: str | None
    sample_ids: list[str]
    quantity_label: str
    value: float | None
    unit: str
    qualitative_value: str | None
    uncertainty: float | None = Field(ge=0)
    uncertainty_kind: Literal["standard_deviation", "standard_error", "interval", "unknown"]
    status: Literal["reported", "cited", "derived", "assumed"]
    caveat: str
    evidence: EvidenceLocator


class ExperimentChunk(StrictRecord):
    """Model output for one bounded source chunk before document reconciliation."""

    samples: list[SampleRecord]
    measurements: list[MeasurementRecord]
    observations: list[ScientificObservation]
    confidence: float = Field(ge=0, le=1)


class ValidationIssue(StrictRecord):
    """Why a proposed record cannot enter the accepted view."""

    record_type: str
    record_id: str
    reason: str
    payload: dict[str, Any]


class ValidatedExperimentChunk(StrictRecord):
    """Accepted records plus explicit rejected/uncertain proposals."""

    samples: list[SampleRecord]
    measurements: list[MeasurementRecord]
    observations: list[ScientificObservation]
    issues: list[ValidationIssue]
    confidence: float = Field(ge=0, le=1)


def _located(quote: str, source: str) -> bool:
    return bool(quote.strip()) and " ".join(quote.split()).casefold() in " ".join(source.split()).casefold()


def validate_experiment_chunk(proposal: ExperimentChunk, source: str) -> ValidatedExperimentChunk:
    """Reject invented evidence and dangling sample/measurement relationships (donor rules)."""
    issues: list[ValidationIssue] = []
    samples: list[SampleRecord] = []
    for sample in proposal.samples:
        if _located(sample.evidence.quote, source):
            samples.append(sample)
        else:
            issues.append(ValidationIssue(record_type="sample", record_id=sample.sample_id,
                                          reason="evidence_quote_not_found", payload=sample.model_dump()))
    sample_ids = {s.sample_id for s in samples}
    measurements: list[MeasurementRecord] = []
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
    observations: list[ScientificObservation] = []
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


# --- F2W: document-aware validation (H3 F2/F8) ----------------------------

#: Version of the rule set below; recorded on every stage report.
RULES_VERSION = "h3-f2f8.1"


@dataclass(frozen=True)
class Rule:
    """One validation rule. ``gate`` rules withhold; ``tag`` rules set the evidence-source tag.

    Attributes:
        rule_id: Stable id (decision item and number).
        kind: ``gate`` or ``tag``.
        name: Short name.
        reasons: Reason strings the rule emits (withheld reasons or tag reasons).
        effect: What happens, in one sentence.
    """

    rule_id: str
    kind: str
    name: str
    reasons: tuple[str, ...]
    effect: str


#: The H3 rule table. Gates withhold a value from every graph write; tags never withhold (F2).
RULES: tuple[Rule, ...] = (
    Rule("F8.1", "gate", "quote_located", ("missing_quote", "evidence_quote_not_found", "evidence_quote_ambiguous"),
         "withhold when the quote is absent, cannot be located in the stored section text, or is ambiguous"),
    Rule("F8.2", "gate", "number_and_unit_in_quote",
         ("value_unit_not_supported", "missing_unit", "invalid_value", "quote_not_found"),
         "withhold unless the same number sits next to an equivalent unit inside the quote "
         "(a derived value instead needs its printed inputs, rule F2.3)"),
    Rule("F8.3", "gate", "unit_consistent_with_quantity", ("unit_dimension_mismatch",),
         "withhold when the unit's dimension contradicts the quantity's"),
    Rule("F2.0", "gate", "value_present", ("observation_has_no_value",),
         "withhold a record with neither a number nor a qualitative value"),
    Rule("E-02", "gate", "references_resolve", ("unknown_sample_reference", "unknown_measurement_reference"),
         "withhold a record pointing at a sample or measurement that was not accepted"),
    Rule("F8.4", "tag", "attributing_sentence_cited", ("attributed_sentence",),
         "a reported (or derived) value in a sentence attributing it to other work is tagged cited"),
    Rule("F8.5", "tag", "assumed_instrument_value", ("assumed_instrument_value",),
         "an assumed instrument value (beam energy, detector distance, pixel size, beam centre, incidence angle, "
         "wavelength) is tagged assumed and never presented as the paper's measurement"),
    Rule("F2.3", "tag", "derived_reciprocal", ("derived_reciprocal",),
         "a value not printed in the quote but equal to 2*pi/x for an x printed there (d from q, q from d) is "
         "tagged derived, with the derivation recorded"),
)

#: Instrument quantities that are tagged ``assumed`` when assumed (F8.5).
INSTRUMENT_GEOMETRY_LABELS = frozenset({
    "beam_energy", "photon_energy", "sample_detector_distance", "detector_distance", "pixel_size",
    "beam_center", "beam_centre", "incidence_angle", "wavelength",
})

#: Lexical cues that a sentence states an assumption rather than a measurement (F8.5).
_ASSUMPTION_RE = re.compile(
    r"\b(?:assum(?:e|ed|es|ing|ption)|nominal(?:ly)?|taken\s+(?:as|to\s+be)|by\s+default)\b", re.IGNORECASE
)


def _evidence_problems(evidence: EvidenceRecord | None) -> list[str]:
    if evidence is None or MissingData.QUOTE_ABSENT in evidence.missing:
        return ["missing_quote"]
    if MissingData.QUOTE_NOT_LOCATED in evidence.missing:
        return ["evidence_quote_not_found"]
    if MissingData.QUOTE_AMBIGUOUS in evidence.missing:
        return ["evidence_quote_ambiguous"]
    return []


def _derivation(quote: str, value: float, unit: str) -> dict[str, Any] | None:
    for partner_value, partner_unit in printed_quantities(quote):
        found = reciprocal_derivation(value, unit, partner_value, partner_unit)
        if found:
            return found
    return None


def assess_value(
    document: ParsedDocument, evidence: EvidenceRecord, *, label: str, value: float | None, unit: str,
    proposed: EvidenceSource, qualitative: str = "",
) -> tuple[list[str], TagDecision]:
    """Apply the F8 gates and F2/F8 tag rules to one located value.

    Returns:
        ``(problems, decision)``: any problem withholds the value; ``decision`` is the tag
        it has (kept on withheld records too, so every value is captured with a tag).
    """
    problems: list[str] = []
    reasons: list[str] = []
    tag = proposed
    derivation: dict[str, Any] | None = None
    quote = evidence.ref.quote or ""
    text = section_text_for(document, evidence.section_id)
    sentence = quote
    if evidence.ref.locator.char_span:
        start, end = evidence.ref.locator.char_span
        # end - 1: a quote ending in its own full stop must not pull in the next sentence.
        sentence = sentence_around(text, start, max(start, end - 1))
    if value is None and not qualitative.strip():
        problems.append("observation_has_no_value")
    if value is not None:
        verdict = source_support(text, quote, value, unit)
        if verdict == "value_unit_not_supported" and unit:
            derivation = _derivation(quote, value, unit)
            if derivation is not None:
                verdict = "source_supported"
                reasons.append("derived_reciprocal")
                if tag is EvidenceSource.REPORTED:
                    tag = EvidenceSource.DERIVED
        if verdict != "source_supported":
            problems.append(verdict)
        if unit and quantity_unit_mismatch(label, unit):
            problems.append("unit_dimension_mismatch")
    base = base_quantity_name(label).lower()
    if base in INSTRUMENT_GEOMETRY_LABELS and (
            proposed is EvidenceSource.ASSUMED or _ASSUMPTION_RE.search(sentence)):
        tag = EvidenceSource.ASSUMED
        reasons.append("assumed_instrument_value")
    elif tag in (EvidenceSource.REPORTED, EvidenceSource.DERIVED) and is_attributed(sentence):
        tag = EvidenceSource.CITED
        reasons.append("attributed_sentence")
    return problems, TagDecision(proposed=proposed, tag=tag, reasons=tuple(reasons), derivation=derivation)


@dataclass(frozen=True)
class WithheldRecord:
    """A proposal kept out of every graph write, with every reason, its evidence attempt and its tag."""

    record_type: str
    record_id: str
    reasons: tuple[str, ...]
    payload: dict[str, Any]
    evidence: dict[str, Any] | None = None
    tag: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form."""
        return {"record_type": self.record_type, "record_id": self.record_id, "reasons": list(self.reasons),
                "payload": self.payload, "evidence": self.evidence, "tag": self.tag}


@dataclass
class DocumentValidation:
    """Accepted records with their evidence refs and tag decisions, plus withheld proposals.

    Accepted observations carry the *decided* tag as ``status`` (the proposed one is
    in ``tags``). ``mapping_applied`` is ``True`` only after an explicit schema-set
    mapping (:mod:`.schema_selection`); the default path never sets it.
    """

    samples: list[SampleRecord] = field(default_factory=list)
    measurements: list[MeasurementRecord] = field(default_factory=list)
    observations: list[ScientificObservation] = field(default_factory=list)
    evidence: dict[str, EvidenceRef] = field(default_factory=dict)
    withheld: list[WithheldRecord] = field(default_factory=list)
    tags: dict[str, TagDecision] = field(default_factory=dict)
    confidence: float | None = None
    mapping_applied: bool = False
    rules_version: str = RULES_VERSION


def validate_against_document(
    proposal: ExperimentChunk,
    document: ParsedDocument,
    *,
    work_id: str,
    pub_path: str,
    strategy: str = "",
) -> DocumentValidation:
    """Validate a proposal against the stored sections of one source version.

    Evidence and tag keys in the result are ``"<record_type>:<record_id>"``.
    """
    out = DocumentValidation(confidence=proposal.confidence)

    def locate(locator: EvidenceLocator) -> EvidenceRecord | None:
        return build_evidence(document, work_id=work_id, pub_path=pub_path, quote=locator.quote,
                              location_label=locator.section, strategy=strategy)

    def withhold(kind: str, rid: str, reasons: Sequence[str], payload: dict[str, Any],
                 evidence: EvidenceRecord | None, tag: TagDecision | None = None) -> None:
        out.withheld.append(WithheldRecord(kind, rid, tuple(reasons), payload,
                                           evidence.to_dict() if evidence else None,
                                           tag.to_dict() if tag else None))

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
        proposed = EvidenceSource(observation.status)
        decision = TagDecision(proposed=proposed, tag=proposed)
        if not problems and evidence is not None:
            more, decision = assess_value(
                document, evidence, label=observation.quantity_label, value=observation.value,
                unit=observation.unit, proposed=proposed, qualitative=observation.qualitative_value or "",
            )
            problems += more
        key = f"observation:{observation.observation_id}"
        if problems:
            withhold("observation", observation.observation_id, problems, observation.model_dump(), evidence,
                     decision)
        else:
            out.observations.append(observation.model_copy(update={"status": decision.tag.value}))
            out.evidence[key] = evidence.ref  # type: ignore[union-attr]
            out.tags[key] = decision
    return out


#: One accepted quantity: the proposal, its located evidence and its tag decision.
AcceptedQuantity = tuple[ReportedQuantity, EvidenceRecord, TagDecision]


def validate_reported_quantities(
    quantities: Sequence[ReportedQuantity], document: ParsedDocument, *, work_id: str, pub_path: str,
    strategy: str = "",
) -> tuple[list[AcceptedQuantity], list[WithheldRecord]]:
    """Split strategy observations into (accepted with evidence and tag, withheld with reasons and tag)."""
    accepted: list[AcceptedQuantity] = []
    withheld: list[WithheldRecord] = []
    for index, quantity in enumerate(quantities):
        evidence = build_evidence(document, work_id=work_id, pub_path=pub_path, quote=quantity.quote,
                                  location_label=quantity.location_label, strategy=strategy)
        problems = _evidence_problems(evidence)
        decision = TagDecision(proposed=quantity.evidence_source, tag=quantity.evidence_source)
        if not problems and evidence is not None:
            problems, decision = assess_value(
                document, evidence, label=quantity.name, value=quantity.raw_value, unit=quantity.raw_unit,
                proposed=quantity.evidence_source, qualitative=quantity.qualitative_value,
            )
        record_id = f"{index}:{quantity.name}"
        if problems:
            withheld.append(WithheldRecord("quantity", record_id, tuple(problems), _payload(quantity),
                                           evidence.to_dict() if evidence else None, decision.to_dict()))
        else:
            accepted.append((quantity, evidence, decision))  # type: ignore[arg-type]
    return accepted, withheld


def _payload(quantity: ReportedQuantity) -> dict[str, Any]:
    return {
        "name": quantity.name, "raw_value": quantity.raw_value, "raw_unit": quantity.raw_unit,
        "quote": quantity.quote, "location_label": quantity.location_label,
        "evidence_source": quantity.evidence_source.value,
        "uncertainty": {"value": quantity.uncertainty.value, "kind": quantity.uncertainty.kind.value},
        "extraction_confidence": quantity.extraction_confidence,
        "sample_id": quantity.sample_id, "processing_state": quantity.processing_state,
        "qualitative_value": quantity.qualitative_value, "caveat": quantity.caveat,
    }


def _structure_claims(result: ExtractionResult, document: ParsedDocument, sink: EvidenceSink, *,
                      pub_path: str, strategy: str) -> list[dict[str, Any]]:
    """The morphology a structure strategy read, with its located supporting quote (donor drop 2).

    Morphology has no approved F2W slot (``Structure`` usage is pending), so this
    is a candidate claim only; ``other``/``unknown`` are not claims the paper made.
    Only a located, unambiguous quote is written to the sink.
    """
    structure = (result.side_outputs or {}).get("structure") if isinstance(result.side_outputs, Mapping) else None
    if not isinstance(structure, Mapping):
        return []
    morphology = str(structure.get("morphology") or "").strip()
    if not morphology or morphology in ("other", "unknown"):
        return []
    quote = str(structure.get("evidence") or "")
    evidence = build_evidence(document, work_id=result.work_id, pub_path=pub_path, quote=quote, strategy=strategy)
    problems = _evidence_problems(evidence)
    if not problems and evidence is not None:
        sink.put_evidence(evidence)
    return [{
        "kind": "morphology", "value": morphology, "status": "candidate", "slot": None,
        "note": "no approved F2W slot (Structure class usage pending)",
        "evidence_ids": [evidence.ref.evidence_id] if evidence is not None and not problems else [],
        "withheld_reasons": problems,
    }]


@dataclass
class StageReport:
    """Outcome of the validation stage for one extraction result.

    Attributes:
        accepted / written: Values that passed every gate / quantity records written to the sink.
        withheld: Values kept out of every graph write, each with reasons and its tag.
        tags: Count of accepted values per evidence-source tag.
        retagged: ``{record_id, proposed, tag, reasons}`` for values whose tag the rules changed.
        peak_slots: F4 candidate fill of the existing peak slots (reported peaks only).
        claims: Candidate structure claims (morphology) with located evidence.
        schema_mapping: Payload for an explicitly selected schema set, else ``None`` (default).
        gates: Snapshot of :data:`~app.modules.extraction.H3_GATES` at run time.
    """

    strategy: str
    work_id: str
    accepted: int
    withheld: list[WithheldRecord]
    written: int
    tags: dict[str, int] = field(default_factory=dict)
    retagged: list[dict[str, Any]] = field(default_factory=list)
    peak_slots: dict[str, Any] = field(default_factory=dict)
    claims: list[dict[str, Any]] = field(default_factory=list)
    schema_mapping: dict[str, Any] | None = None
    gates: dict[str, bool] = field(default_factory=dict)
    rules_version: str = RULES_VERSION
    mapping_applied: bool = False
    published: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form."""
        return {
            "strategy": self.strategy, "work_id": self.work_id, "rules_version": self.rules_version,
            "accepted": self.accepted, "written": self.written, "tags": dict(self.tags),
            "retagged": list(self.retagged), "withheld": [w.to_dict() for w in self.withheld],
            "peak_slots": self.peak_slots, "claims": self.claims, "schema_mapping": self.schema_mapping,
            "gates": dict(self.gates), "mapping_applied": self.mapping_applied, "published": self.published,
        }


def run_validation_stage(
    result: ExtractionResult, document: ParsedDocument, sink: EvidenceSink, *, pub_path: str,
    prompt_version: str = "", schema_set: str | None = None, allow_pending_review: bool = False,
    source_paper: str = "",
) -> StageReport:
    """Validate a result's observations and write the accepted ones, tagged, as candidates.

    Applies the approved H3 mappings (gate ``apply_validation_mappings``): F8 gates
    withhold, F2/F8 tags never do, F4 fills the existing peak slots from reported
    peaks. Withheld proposals are returned for review and never reach the sink.

    Args:
        result: Extraction result whose observations are validated.
        document: Parsed document the observations are checked against.
        sink: Evidence sink that receives the accepted quantities.
        pub_path: Publication path recorded on evidence and used for the default ``source_paper``.
        prompt_version: Prompt revision recorded on the written evidence.
        schema_set: ``None`` (default) keeps the base schema and maps nothing. ``"0.3.0"``
            also builds a candidate 0.3.0 payload (gate ``new_schema_slots``), which
            additionally needs ``allow_pending_review=True`` until the owner signs off
            the T22C schema diff.
        allow_pending_review: Permit a schema set that is still pending owner review.
        source_paper: File name recorded on 0.3.0 nodes (defaults to the last part of ``pub_path``).
    """
    from . import H3_GATES, require_gate

    require_gate("apply_validation_mappings")
    strategy = f"{result.strategy}@{result.strategy_version}"
    accepted, withheld = validate_reported_quantities(
        result.observations, document, work_id=result.work_id, pub_path=pub_path, strategy=result.strategy,
    )
    written = record_reported_quantities(
        sink, document, [q for q, _, _ in accepted], work_id=result.work_id, pub_path=pub_path,
        strategy=strategy, prompt_version=prompt_version, tags=[d for _, _, d in accepted],
    )
    tags: dict[str, int] = {}
    retagged: list[dict[str, Any]] = []
    for quantity, _evidence, decision in accepted:
        tags[decision.tag.value] = tags.get(decision.tag.value, 0) + 1
        if decision.retagged:
            retagged.append({"name": quantity.name, "proposed": decision.proposed.value, "tag": decision.tag.value,
                             "reasons": list(decision.reasons)})
    report = StageReport(
        strategy=result.strategy, work_id=result.work_id, accepted=len(accepted), withheld=withheld,
        written=written, tags=dict(sorted(tags.items())), retagged=retagged, peak_slots=peak_slot_values(accepted),
        claims=_structure_claims(result, document, sink, pub_path=pub_path, strategy=strategy),
        gates=dict(H3_GATES),
    )
    if schema_set is not None:
        from .schema_selection import map_to_schema_set, validation_from_quantities

        report.schema_mapping = map_to_schema_set(
            validation_from_quantities(accepted), document, work_id=result.work_id,
            source_paper=source_paper or pub_path.rsplit("/", 1)[-1], schema_set=schema_set,
            allow_pending_review=allow_pending_review,
        )
        report.mapping_applied = True
    return report


__all__ = [
    "INSTRUMENT_GEOMETRY_LABELS", "RULES", "RULES_VERSION", "AcceptedQuantity", "DocumentValidation",
    "EvidenceLocator", "ExperimentChunk", "MeasurementRecord", "Rule", "SampleRecord", "ScientificObservation",
    "StageReport", "StrictRecord", "ValidatedExperimentChunk", "ValidationIssue", "WithheldRecord", "assess_value",
    "run_validation_stage", "validate_against_document", "validate_experiment_chunk",
    "validate_reported_quantities",
]

"""Map validated T06 experiment records onto schema set 0.3.0 classes (T22C; H3 F2/F3/F5/F6/F7).

Input is a :class:`~app.modules.extraction.record_validation.DocumentValidation`: the samples, measurements
and observations the T06 validation stage *accepted*, each with a located-quote
:class:`~app.modules.contracts.evidence.EvidenceRef`. Output is a json2kg-shaped payload
(``things``/``associations``, ``rel:<slot>`` predicates) that uses only the 0.3.0 classes and slots:

=====================  =========================================================================
record                 nodes and edges
=====================  =========================================================================
``SampleRecord``       ``Sample`` (label, composition, phase, processing state, preparation);
                       ``made_of_material`` -> ``Material`` per composition entry
``MeasurementRecord``  ``MeasurementRun``; ``run_of_technique`` -> ``ExperimentalTechnique`` or
                       ``RSoXSMeasurement``; ``run_measured_sample`` -> ``Sample``;
                       ``run_on_instrument`` -> ``Instrument``; ``run_stated_condition`` ->
                       ``RunCondition`` per condition
``ScientificObservation``  ``QuantityValue`` (value/unit as printed, uncertainty and its kind,
                       ``evidence_source_tag``); ``qualifier_sample`` -> ``Sample``;
                       ``run_produced_quantity`` from its run; under a schema that has the 0.3.1
                       normalized-value slots (T22D, D1.4), ``normalized_value``/``normalized_unit``/
                       ``conversion_factor``/``conversion_profile`` from ``normalize_quantity`` when the
                       printed unit is in a closed conversion table (else absent, never guessed)
every record           ``supporting_quote`` -> ``EvidenceQuote`` -> ``quote_located_in_section``
                       -> ``PaperSection``
=====================  =========================================================================

Material, technique and instrument node ids use ``json2kg.make_id`` on the printed name, so they meet the
term graph's own nodes; samples, runs, conditions and values are paper-local (ids carry a hash of the work).
Nothing is invented: absent fields stay absent, a condition's number is read only when its printed text is
a number followed by a unit, and an instrument's category is not guessed.

This module is pure. It writes nothing, and the T06 stage does not call it: wiring it in waits for the
owner's review of the 0.3.0 schema diff (``docs/migration/reviews/H3/schema_diff_T22C.md``), so a schema set
pending review is refused unless the caller passes ``allow_pending_review=True``.
"""
from __future__ import annotations

from collections.abc import Container
import hashlib
import re
from typing import Any

from ..contracts.evidence import EvidenceRef, MatchType
from ..evidence.units import normalize_quantity
from ..json2kg import make_id
from ..literature.parsed_document import ParsedDocument, ParsedSection
from ..term_extractor.schema import GENERAL_CATEGORY_ALIASES
from .loading import load_schema_view
from .registry import RegistryError, SchemaEntry

MAPPING_VERSION = "1"
REQUIRED_CLASSES = frozenset({"Sample", "MeasurementRun", "Instrument", "RunCondition", "QuantityValue",
                              "EvidenceQuote", "PaperSection"})
#: The 0.3.1 normalized-value slots (T22D, D1.4); filled only when the selected schema declares all four.
NORMALIZED_SLOTS = ("normalized_value", "normalized_unit", "conversion_factor", "conversion_profile")
_NUMBER_UNIT = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([^\d\s].*?)?\s*$")


class MappingError(ValueError):
    """A record cannot be mapped (no located evidence, or the schema lacks the 0.3.0 classes)."""


def _key(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.lower())


def _local(kind: str, work_id: str, local_id: str) -> str:
    return make_id(f"{kind}-{hashlib.sha256(work_id.encode()).hexdigest()[:12]}-{local_id}")


def technique_class(technique: str, classes: Container[str]) -> str:
    """Map a technique spelling to a schema class (F6 table).

    RSoXS spellings (the existing alias table, exact keys only) give ``RSoXSMeasurement``, else
    ``ExperimentalTechnique``. No fuzzy match, so no technique lands on an unrelated class.
    """
    alias = GENERAL_CATEGORY_ALIASES.get(_key(technique))
    return "RSoXSMeasurement" if alias == "RSoXSMeasurement" and "RSoXSMeasurement" in classes \
        else "ExperimentalTechnique"


def normalized_slots(value: float | None, unit: str | None) -> dict[str, Any]:
    """The four 0.3.1 slot values for a printed ``value unit``, or ``{}`` when no conversion is possible.

    Uses :func:`~app.modules.evidence.units.normalize_quantity` (closed tables, profile recorded); an unknown
    unit, a missing number or a refused value gives ``{}`` so the record keeps only what was printed.
    """
    if value is None or not unit:
        return {}
    normalized = normalize_quantity(float(value), str(unit))
    if not normalized.converted:
        return {}
    return {"normalized_value": normalized.value, "normalized_unit": normalized.unit,
            "conversion_factor": normalized.factor, "conversion_profile": normalized.profile}


def _condition_number(text: str) -> tuple[float | None, str | None]:
    match = _NUMBER_UNIT.match(text or "")
    if not match or not match.group(2):
        return None, None
    return float(match.group(1)), match.group(2)


class _Builder:
    def __init__(self, source_paper: str) -> None:
        self.source_paper = source_paper
        self.things: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self._edge_keys: set[tuple[str, str, str]] = set()

    def node(self, node_id: str, category: str, name: str, **slots: Any) -> str:  # noqa: ANN401  # Any: open-ended node slots, passed through unchanged
        if node_id not in self.things:
            body = {"id": node_id, "name": name, "category": category, "type": f"matkg:{category}",
                    "description": f"{category} {name}", "source_papers": [self.source_paper]}
            body.update({k: v for k, v in slots.items() if v not in (None, "", [], {})})
            self.things[node_id] = body
        return node_id

    def edge(self, subject: str, slot: str, obj: str, evidence: str | None = None) -> None:
        key = (subject, slot, obj)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append({"subject": subject, "predicate": f"rel:{slot}", "object": obj,
                           "has_evidence": evidence})


def _section(document: ParsedDocument, ref: EvidenceRef) -> ParsedSection | None:
    for section in document.sections:
        if section.name == ref.locator.section:
            return section
    return None


def _quote(builder: _Builder, document: ParsedDocument, ref: EvidenceRef, work_id: str) -> str:
    if ref.match_type not in (MatchType.EXACT, MatchType.NORMALIZED) or not ref.quote:
        raise MappingError(f"evidence {ref.evidence_id} is not a located quote")
    span = ref.locator.char_span or (None, None)
    quote_id = builder.node(
        make_id(ref.evidence_id.replace(":", "-")), "EvidenceQuote", ref.evidence_id,
        quote_text=ref.quote, quote_match_type=ref.match_type.value, quote_char_start=span[0],
        quote_char_end=span[1], quote_page=ref.locator.page, source_version=ref.source_version,
        source_sha256=ref.source_sha256, parser_revision=ref.parser_revision)
    section = _section(document, ref)
    if section is not None:
        sid = section.structural_id or _key(section.name)
        section_id = builder.node(
            _local("section", f"{work_id}|{ref.source_version}", sid), "PaperSection", section.name,
            section_label=section.name, section_page=section.page, work_id=work_id,
            source_version=ref.source_version)
        builder.edge(quote_id, "quote_located_in_section", section_id)
    return quote_id


def map_validated_records(
    validation: Any,  # noqa: ANN401  # Any: validated document (duck-typed record_validation result)
    document: ParsedDocument,
    *,
    work_id: str,
    source_paper: str,
    entry: SchemaEntry,
    allow_pending_review: bool = False,
) -> dict[str, Any]:
    """json2kg-shaped payload for the accepted records of ``validation`` under ``entry``.

    Raises:
        RegistryError: ``entry`` is pending owner review and ``allow_pending_review`` is false.
        MappingError: ``entry`` lacks the 0.3.0 classes, or an accepted record has no located evidence.
    """
    if entry.pending_review and not allow_pending_review:
        raise RegistryError(f"{entry.path} is pending owner review; pass allow_pending_review=True")
    view = load_schema_view(entry)
    classes = set(view.all_classes())
    if not REQUIRED_CLASSES <= classes:
        raise MappingError(f"{entry.path} lacks {sorted(REQUIRED_CLASSES - classes)} (needs schema set 0.3.0)")
    fill_normalized = set(NORMALIZED_SLOTS) <= set(view.all_slots())
    evidence: dict[str, EvidenceRef] = validation.evidence
    b = _Builder(source_paper)

    def support(node_id: str, kind: str, record_id: str) -> None:
        ref = evidence.get(f"{kind}:{record_id}")
        if ref is None:
            raise MappingError(f"{kind} {record_id} has no located evidence")
        b.edge(node_id, "supporting_quote", _quote(b, document, ref, work_id), ref.quote)

    samples: dict[str, str] = {}
    for s in validation.samples:
        node = b.node(_local("sample", work_id, s.sample_id), "Sample", s.label, sample_label=s.label,
                      sample_composition=list(s.composition), sample_phase=s.phase,
                      sample_processing_state=s.processing_state, sample_preparation=list(s.preparation))
        samples[s.sample_id] = node
        support(node, "sample", s.sample_id)
        for component in s.composition:
            b.edge(node, "made_of_material", b.node(make_id(component), "Material", component))

    runs: dict[str, str] = {}
    for m in validation.measurements:
        run = b.node(_local("run", work_id, m.measurement_id), "MeasurementRun", f"{m.technique} run {m.measurement_id}")
        runs[m.measurement_id] = run
        support(run, "measurement", m.measurement_id)
        technique = b.node(make_id(m.technique), technique_class(m.technique, classes), m.technique)
        b.edge(run, "run_of_technique", technique)
        for sample_id in m.sample_ids:
            b.edge(run, "run_measured_sample", samples[sample_id])
        if m.instrument:
            b.edge(run, "run_on_instrument", b.node(make_id(m.instrument), "Instrument", m.instrument))
        for name, text in sorted(m.conditions.items()):
            number, unit = _condition_number(text)
            cond = b.node(_local("condition", work_id, f"{m.measurement_id}-{name}"), "RunCondition",
                          f"{name}: {text}", condition_name=name, condition_text=text, numeric_value=number,
                          unit=unit)
            b.edge(run, "run_stated_condition", cond)

    for o in validation.observations:
        value = b.node(
            _local("value", work_id, o.observation_id), "QuantityValue", o.quantity_label,
            quantity_label=o.quantity_label, numeric_value=o.value, unit=o.unit or None,
            qualitative_value=o.qualitative_value, uncertainty_value=o.uncertainty,
            uncertainty_kind=o.uncertainty_kind if o.uncertainty is not None else None,
            evidence_source_tag=o.status, **(normalized_slots(o.value, o.unit) if fill_normalized else {}))
        support(value, "observation", o.observation_id)
        for sample_id in o.sample_ids:
            b.edge(value, "qualifier_sample", samples[sample_id])
        if o.measurement_id:
            b.edge(runs[o.measurement_id], "run_produced_quantity", value)

    return {"things": list(b.things.values()), "associations": b.edges}


__all__ = ["MAPPING_VERSION", "MappingError", "NORMALIZED_SLOTS", "REQUIRED_CLASSES", "map_validated_records",
           "normalized_slots", "technique_class"]

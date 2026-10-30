"""Paper-only evidence persistence over a parsed source version (E-09).

The FORGE originals live in a mixed simulation module and read sections back out of
a KG store. Here the same rules run over a T05 :class:`ParsedDocument` (the exact
parser output, with stored section text truncated as the store keeps it), and write
candidate records to an :class:`EvidenceSink`. Nothing here imports the donor module,
writes to a scientific graph, or publishes; sinks only hold candidates.

Ported rules:

- the stored text is the only text offsets may index (``section_text_for``);
- an unknown page is ``None``, not ``0`` (``section_page_for``);
- the region label is the extractor's opinion and the text is evidence: a quote
  not in the labelled section is searched for in every section, exact beats
  normalized, and among equals the shortest section wins (``locate_in_any_section``);
  F2W adds an explicit ambiguity flag when equal-rank candidates tie, so the
  caller can withhold rather than pick;
- a label is matched to a section name conservatively and refuses on several
  equally good matches (``section_for_label``);
- evidence ids are deterministic over the text so one sentence is one record
  (``stable_evidence_id``, donor-compatible digest);
- a record is never invented for a quantity with neither quote nor label, and
  a miss is recorded (``quote_not_located``), not refused.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/paper_simulation.py
(``_record_reported_quantities``, ``_record_evidence``, ``_locate_in_any_section``,
``_section_text_for``, ``_section_page_for``, ``_stable_evidence_id``) (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

from ..contracts.evidence import EvidenceRef, MatchType, SourceLocator
from ..literature.parsed_document import SECTION_TEXT_MAX_CHARS, ParsedDocument, ParsedSection
from .quote_location import NORMALIZATION_PROFILE, QuoteLocation, count_occurrences, locate_quote
from .records import (
    EvidenceRecord,
    MissingData,
    QuantityRecord,
    ReportedQuantity,
    TagDecision,
)
from .units import dimension_mismatch, normalize_quantity

logger = logging.getLogger(__name__)

#: Upper bound on stored quote text, as in the donor evidence rows.
QUOTE_MAX_CHARS = 4000


def stable_evidence_id(pub_path: str, text: str) -> str:
    """Deterministic 16-hex id for one quotation from one paper (donor-compatible digest).

    MD5 is used as an identifier, not for security, so ids match FORGE-written rows.
    """
    return hashlib.md5(f"{pub_path}|{text}".encode(), usedforsecurity=False).hexdigest()[:16]


def quantity_id(pub_path: str, name: str, value: float | None, unit: str) -> str | None:
    """Deterministic quantity id (``pub|name|value|unit``), or ``None`` without name/value."""
    name = (name or "").strip()
    if not name or value is None:
        return None
    digest = hashlib.md5(f"{pub_path}|{name}|{float(value)}|{unit or ''}".encode(),
                         usedforsecurity=False).hexdigest()[:16]
    return f"quantity:{digest}"


def _section_id(section: ParsedSection, index: int) -> str:
    return section.structural_id or f"section-{index:05d}"


def _sections(document: ParsedDocument) -> list[tuple[str, ParsedSection]]:
    return [(_section_id(s, i), s) for i, s in enumerate(document.sections)]


def section_text_for(document: ParsedDocument, section_id: str | None) -> str:
    """The section text as stored (truncated at ``SECTION_TEXT_MAX_CHARS``), or ``""``."""
    if not section_id:
        return ""
    for sid, section in _sections(document):
        if sid == section_id:
            return section.text[:SECTION_TEXT_MAX_CHARS]
    return ""


def section_page_for(document: ParsedDocument, section_id: str | None) -> int | None:
    """The page the section starts on, or ``None`` when the parser could not say."""
    if not section_id:
        return None
    for sid, section in _sections(document):
        if sid == section_id:
            return section.page if isinstance(section.page, int) and section.page >= 1 else None
    return None


def section_for_label(document: ParsedDocument, label: str) -> str | None:
    """The section a region label names, or ``None`` when absent or several match equally."""
    wanted = (label or "").strip().lower()
    if not wanted:
        return None
    named = [(sid, s.name.strip().lower()) for sid, s in _sections(document)]
    exact = [sid for sid, name in named if name == wanted]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    partial = [sid for sid, name in named if name and (wanted in name or name in wanted)]
    return partial[0] if len(partial) == 1 else None


@dataclass(frozen=True)
class SectionMatch:
    """Result of locating a quote across sections.

    Attributes:
        section_id: Chosen section, or ``None``.
        location: Where it matched, or ``None``.
        ambiguous: Several sections tied at the best rank and length, or the quote
            occurs more than once in the chosen section.
    """

    section_id: str | None
    location: QuoteLocation | None
    ambiguous: bool = False


def locate_in_any_section(
    document: ParsedDocument, quote: str, *, sections: Sequence[tuple[str, ParsedSection]] | None = None
) -> SectionMatch:
    """Find the section whose stored text actually contains ``quote``.

    Exact beats normalized; among equals the shortest section wins (donor rule).
    Two equal-rank, equal-length candidates are reported as ambiguous.
    """
    if not quote:
        return SectionMatch(None, None)
    candidates: list[tuple[int, int, str, QuoteLocation]] = []
    for sid, section in sections if sections is not None else _sections(document):
        text = section.text[:SECTION_TEXT_MAX_CHARS]
        if not text:
            continue
        found = locate_quote(quote, text)
        if not found.found:
            continue
        candidates.append((0 if found.is_verbatim else 1, len(text), sid, found))
    if not candidates:
        return SectionMatch(None, None)
    candidates.sort(key=lambda c: c[:2])
    best = candidates[0]
    tied = any(c[:2] == best[:2] for c in candidates[1:])
    repeated = count_occurrences(quote, section_text_for(document, best[2])) > 1
    return SectionMatch(best[2], best[3], ambiguous=tied or repeated)


class EvidenceSink(Protocol):
    """Where candidate evidence and quantity records go. Never a scientific graph."""

    def put_evidence(self, record: EvidenceRecord) -> None:
        """Store one evidence record (idempotent on ``ref.evidence_id``)."""

    def put_quantity(self, record: QuantityRecord) -> None:
        """Store one quantity record (idempotent on ``quantity_id``)."""


class InMemoryEvidenceSink:
    """Candidate sink kept in memory (tests, previews)."""

    def __init__(self) -> None:
        self.evidence: dict[str, EvidenceRecord] = {}
        self.quantities: dict[str, QuantityRecord] = {}

    def put_evidence(self, record: EvidenceRecord) -> None:
        """Keep the first record for an id; a quotation is immutable."""
        self.evidence.setdefault(record.ref.evidence_id, record)

    def put_quantity(self, record: QuantityRecord) -> None:
        """Merge evidence ids into an existing quantity record with the same id."""
        existing = self.quantities.get(record.quantity_id)
        if existing is None:
            self.quantities[record.quantity_id] = record
            return
        ids = tuple(dict.fromkeys(existing.evidence_ids + record.evidence_ids))
        from dataclasses import replace

        self.quantities[record.quantity_id] = replace(existing, evidence_ids=ids)

    def snapshot(self) -> dict[str, Any]:
        """Sorted JSON-ready dump."""
        return {
            "status": "candidate",
            "evidence": [self.evidence[k].to_dict() for k in sorted(self.evidence)],
            "quantities": [self.quantities[k].to_dict() for k in sorted(self.quantities)],
        }


class CandidateFileEvidenceSink(InMemoryEvidenceSink):
    """In-memory sink that can be flushed atomically to one candidate JSON file.

    The file is a candidate artifact for review (H3/H5), never an import source.
    """

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = Path(path)

    def flush(self) -> Path:
        """Write the snapshot atomically and return the path."""
        from ..literature.store import atomic_write_json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, self.snapshot())
        return self.path


def _parser_meta(document: ParsedDocument) -> dict[str, Any]:
    return {"parser": document.parser, "parser_version": document.parser_version,
            "parser_key": document.parser_key}


def build_evidence(
    document: ParsedDocument,
    *,
    work_id: str,
    pub_path: str,
    quote: str,
    location_label: str = "",
    strategy: str = "",
    prompt_version: str = "",
    caveat: str = "",
    sections: Sequence[tuple[str, ParsedSection]] | None = None,
) -> EvidenceRecord | None:
    """Resolve one quote to an :class:`EvidenceRecord` over ``document``.

    Returns ``None`` when there is neither a quote nor a label (nothing to cite).
    """
    quote = (quote or "").strip()
    label = (location_label or "").strip()
    if not quote and not label:
        return None
    missing: list[MissingData] = []
    section_id = section_for_label(document, label)
    located: QuoteLocation | None = None
    located_by = "none"
    ambiguous = False
    if quote:
        if section_id:
            found = locate_quote(quote, section_text_for(document, section_id))
            if found.found:
                located, located_by = found, "label"
                ambiguous = count_occurrences(quote, section_text_for(document, section_id)) > 1
        if located is None:
            match = locate_in_any_section(document, quote, sections=sections)
            if match.location is not None:
                if section_id and match.section_id != section_id:
                    missing.append(MissingData.SECTION_LABEL_MISMATCH)
                section_id, located, located_by = match.section_id, match.location, "any_section"
                ambiguous = match.ambiguous
        if located is None:
            missing.append(MissingData.QUOTE_NOT_LOCATED)
    else:
        missing.append(MissingData.QUOTE_ABSENT)
    if ambiguous:
        missing.append(MissingData.QUOTE_AMBIGUOUS)
    if section_id is None:
        missing.append(MissingData.SECTION_UNKNOWN)
    page = section_page_for(document, section_id)
    if page is None:
        missing.append(MissingData.PAGE_UNKNOWN)
    if not document.parser_version:
        missing.append(MissingData.PARSER_REVISION_UNKNOWN)
    text = quote or label
    match_kind = located.match_kind if located else None
    evidence_id = "evidence:" + stable_evidence_id(pub_path, json.dumps(
        [text, section_id, page, match_kind, located.char_start if located else None, prompt_version]))
    section_name = None
    for sid, section in _sections(document):
        if sid == section_id:
            section_name = section.name
    match_type = located.match_type() if located else MatchType.NONE
    ref = EvidenceRef(
        evidence_id=evidence_id,
        work_id=work_id,
        source_version=document.source_ref,
        source_sha256=document.file_sha256 if len(document.file_sha256) == 64 else None,
        locator=SourceLocator(page=page, section=section_name,
                              char_span=located.span() if located else None),
        match_type=match_type,
        quote=quote[:QUOTE_MAX_CHARS] or None,
        parser_revision=document.parser_key,
        normalization_profile=NORMALIZATION_PROFILE if match_type is MatchType.NORMALIZED else None,
    )
    return EvidenceRecord(
        ref=ref, section_id=section_id, section_label=label, located_by=located_by,
        missing=tuple(dict.fromkeys(missing)), parser=_parser_meta(document),
        strategy=strategy, caveat=caveat[:1000],
    )


def record_evidence(
    sink: EvidenceSink,
    document: ParsedDocument,
    quantities: Iterable[ReportedQuantity],
    *,
    work_id: str,
    pub_path: str,
    strategy: str = "",
    prompt_version: str = "",
) -> dict[str, list[str]]:
    """Write evidence for each quantity's quote; return ``{quantity_name: [evidence_id]}``.

    Best-effort per quantity, like the donor: one failure is logged and skipped.
    """
    sections = _sections(document)
    linked: dict[str, list[str]] = {}
    for quantity in quantities:
        try:
            record = build_evidence(
                document, work_id=work_id, pub_path=pub_path, quote=quantity.quote,
                location_label=quantity.location_label, strategy=strategy,
                prompt_version=prompt_version, caveat=quantity.caveat, sections=sections,
            )
        except Exception as exc:  # noqa: BLE001 - evidence is best-effort by contract
            logger.warning("Could not build evidence for %s: %s", quantity.name, exc)
            continue
        if record is None:
            continue
        sink.put_evidence(record)
        linked.setdefault(quantity.name, []).append(record.ref.evidence_id)
    return linked


def record_reported_quantities(
    sink: EvidenceSink,
    document: ParsedDocument,
    quantities: Sequence[ReportedQuantity],
    *,
    work_id: str,
    pub_path: str,
    strategy: str = "",
    prompt_version: str = "",
    tags: Sequence[TagDecision | None] | None = None,
) -> int:
    """Record what the paper reported, as candidates, with evidence attached.

    A quantity whose unit contradicts its own dimension is dropped (donor rule).
    A qualitative-only observation is kept without a numeric quantity.

    Args:
        sink: Evidence sink that receives each evidence record and quantity record.
        document: Parsed document the quotes are located in.
        quantities: Reported quantities to record.
        work_id: Identifier of the work the evidence belongs to.
        pub_path: Publication path, part of the stable evidence and quantity ids.
        strategy: Extraction strategy name recorded on the evidence.
        prompt_version: Prompt version recorded on the evidence.
        tags: Optional validation-stage tag decision per quantity (same order). When
            given, the record's ``evidence_source`` is the decided tag and the
            proposed tag, reasons and derivation are kept beside it (T06B, H3 F2/F8).

    Returns:
        Number of quantity records written.

    Raises:
        ValueError: `tags` is given but not the same length as `quantities`.
    """
    if tags is not None and len(tags) != len(quantities):
        raise ValueError("tags must align with quantities")
    written = 0
    sections = _sections(document)
    for index, quantity in enumerate(quantities):
        decision = tags[index] if tags is not None else None
        if quantity.raw_value is not None and dimension_mismatch(quantity.name, quantity.raw_unit):
            logger.info("Dropping %s: unit %r contradicts its dimension", quantity.name, quantity.raw_unit)
            continue
        evidence = build_evidence(
            document, work_id=work_id, pub_path=pub_path, quote=quantity.quote,
            location_label=quantity.location_label, strategy=strategy,
            prompt_version=prompt_version, caveat=quantity.caveat, sections=sections,
        )
        if evidence is not None:
            sink.put_evidence(evidence)
        missing: list[MissingData] = []
        normalized = None
        if quantity.raw_value is not None:
            normalized = normalize_quantity(float(quantity.raw_value), quantity.raw_unit)
            if not quantity.raw_unit:
                missing.append(MissingData.UNIT_ABSENT)
            elif not normalized.converted:
                missing.append(MissingData.UNIT_NOT_CONVERTIBLE)
        if quantity.uncertainty.value is None:
            missing.append(MissingData.UNCERTAINTY_NOT_STATED)
        qid = quantity_id(pub_path, quantity.name, quantity.raw_value, quantity.raw_unit)
        if qid is None:
            key = quantity.qualitative_value or quantity.quote
            qid = "observation:" + stable_evidence_id(pub_path, f"{quantity.name}|{key}")
        sink.put_quantity(QuantityRecord(
            quantity_id=qid, work_id=work_id, name=quantity.name, quantity=normalized,
            evidence_source=decision.tag if decision else quantity.evidence_source,
            uncertainty=quantity.uncertainty,
            extraction_confidence=quantity.extraction_confidence,
            evidence_ids=(evidence.ref.evidence_id,) if evidence else (),
            sample_id=quantity.sample_id, processing_state=quantity.processing_state,
            qualitative_value=quantity.qualitative_value, missing=tuple(missing),
            proposed_evidence_source=decision.proposed if decision else None,
            tag_reasons=decision.reasons if decision else (),
            derivation=decision.derivation if decision else None,
        ))
        written += 1
    return written


__all__ = [
    "QUOTE_MAX_CHARS", "CandidateFileEvidenceSink", "EvidenceSink", "InMemoryEvidenceSink", "SectionMatch",
    "build_evidence", "locate_in_any_section", "quantity_id", "record_evidence",
    "record_reported_quantities", "section_for_label", "section_page_for", "section_text_for",
    "stable_evidence_id",
]

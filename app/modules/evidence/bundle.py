"""Read-only literature-evidence bundle for a consumer's recipe builder (task T15).

The plan (T15) asks for "a read-only literature-evidence bundle sufficient for FORGE's
recipe builder: paper/Work version, extracted reported quantities and contexts, units,
evidence IDs, caveats and source checksum". :class:`LiteratureEvidenceBundle` is that
DTO, built by :func:`build_bundle` from the existing T06 candidate records
(:class:`~app.modules.evidence.records.EvidenceRecord`,
:class:`~app.modules.evidence.records.QuantityRecord`) of one Work. It adds no parser,
provider, metric or model call; every field is copied from a record or derived from the
records' own absence markers.

Rules kept from the records:

* everything is a **candidate** (``status="candidate"``); nothing here is reviewed or
  published, and the bundle says so in its caveats;
* the **source checksum** is the ``source_sha256`` of the parsed source version the
  evidence refers to (``None`` when the records carry no 64-hex digest); records from
  several source versions of one Work are listed and flagged, never merged;
* **units**: the raw unit as printed and, when the T06 conversion table could convert
  it, the normalized value/unit with the conversion profile; a refused conversion keeps
  its reason;
* **quotes** are agent-private source text (D19): they are included only when the
  caller asks (``include_quotes=True``) and remain subject to the disclosure projection
  before any user-facing display. A paraphrase is never presented as a quotation.
* FORGE owns the mapping from these observations to simulation parameters; the bundle
  carries no derived prediction.

The bundle is JSON-serialisable (:meth:`LiteratureEvidenceBundle.to_json`) and validates
back (:meth:`LiteratureEvidenceBundle.from_json`). Importing this module has no side effects.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..contracts.evidence import MatchType
from .records import EvidenceRecord, QuantityRecord

#: Format version of the bundle.
BUNDLE_VERSION = 1

_CANDIDATE_CAVEAT = "candidate records: extracted, not reviewed or published (H3/H5)"
_QUOTES_PRIVATE_CAVEAT = ("quotes are agent-private source text; project through the D19 disclosure rules "
                          "before display")
_QUOTES_OMITTED_CAVEAT = "quotes omitted (include_quotes=False); evidence ids and locators identify the passages"


class BundledUncertainty(BaseModel):
    """A measurement uncertainty the source states (never extraction confidence)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float | None = None
    kind: str = "unknown"


class BundledQuantity(BaseModel):
    """One reported value with its units, evidence links and caveats.

    Attributes:
        quantity_id: Deterministic candidate id (``pub|name|value|unit``).
        name: Strategy quantity label (not an F2W schema slot).
        raw_value / raw_unit: Exactly as printed; ``raw_value`` is ``None`` for a qualitative observation.
        value / unit: Normalized value and unit when the T06 conversion converted the raw unit.
        dimension: ``length``/``reciprocal``/``energy``/``angle``/``unknown``.
        conversion_profile: Conversion table profile, or ``None`` when nothing was converted.
        conversion_refused: Why normalization was refused, if it was.
        evidence_source: Decided evidence-source tag (``reported``, ``cited``, ``derived``, ...).
        presented_as: How the tag presents the value (the record's own wording).
        paper_measurement: Whether the tag counts as the paper's own measurement.
        uncertainty: Stated measurement uncertainty.
        extraction_confidence: Extraction-step confidence; distinct from ``uncertainty``.
        evidence_ids: Evidence records supporting this value (keys of ``LiteratureEvidenceBundle.evidence``).
        caveats: Absence markers, tag reasons and a retagging note, as plain strings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    quantity_id: str
    name: str
    raw_value: float | None = None
    raw_unit: str = ""
    value: float | None = None
    unit: str | None = None
    dimension: str | None = None
    conversion_profile: str | None = None
    conversion_refused: str | None = None
    evidence_source: str
    presented_as: str
    paper_measurement: bool
    uncertainty: BundledUncertainty = Field(default_factory=BundledUncertainty)
    extraction_confidence: float | None = None
    evidence_ids: tuple[str, ...] = ()
    sample_id: str = ""
    processing_state: str = ""
    qualitative_value: str = ""
    derivation: dict[str, Any] | None = None
    caveats: tuple[str, ...] = ()
    status: str = "candidate"


class BundledEvidence(BaseModel):
    """One located (or explicitly unlocated) passage of the source version.

    Attributes:
        evidence_id: Stable evidence id (``evidence:<digest>``).
        source_version: Content address of the parsed source (``sha256:...``).
        source_sha256: Bare 64-hex digest of the source bytes, when the record carries one.
        page / section / char_span: Locator inside the source version.
        section_label: The extractor's own region label, kept beside the resolved section.
        located_by: ``label``, ``any_section`` or ``none``.
        match_type: ``exact``, ``normalized``, ``paraphrase`` or ``none``.
        verified_quote_match: The quote was found in the stored section text.
        quote: The passage, only when requested; ``None`` otherwise.
        parser_revision: Parser key that produced the locator.
        caveats: Absence markers and the strategy caveat, as plain strings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    source_version: str
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    page: int | None = None
    section: str | None = None
    char_span: tuple[int, int] | None = None
    section_label: str = ""
    located_by: str = "none"
    match_type: str = MatchType.NONE.value
    verified_quote_match: bool = False
    quote: str | None = None
    parser_revision: str | None = None
    strategy: str = ""
    caveats: tuple[str, ...] = ()
    status: str = "candidate"


class LiteratureEvidenceBundle(BaseModel):
    """What a consumer needs to cite F2W literature evidence for one Work.

    Attributes:
        bundle_version: Format version.
        work_id: Canonical Work id the records belong to.
        source_version: The one parsed source version the evidence refers to, or ``None`` when the
            records span several (then ``source_versions`` lists them and a caveat says so).
        source_sha256: Checksum of that source version's bytes, when known.
        source_versions: Every source version seen, in sorted order.
        parser: Parser metadata of the records (``parser``, ``parser_version``, ``parser_key``).
        quantities: Reported quantities, sorted by id.
        evidence: Evidence records, keyed by evidence id.
        caveats: Bundle-level caveats (candidate status, quote handling, mixed versions, dangling ids).
        status: Always ``candidate``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_version: int = BUNDLE_VERSION
    work_id: str
    source_version: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_versions: tuple[str, ...] = ()
    parser: dict[str, Any] = Field(default_factory=dict)
    quantities: tuple[BundledQuantity, ...] = ()
    evidence: dict[str, BundledEvidence] = Field(default_factory=dict)
    caveats: tuple[str, ...] = ()
    status: str = "candidate"

    @property
    def evidence_ids(self) -> list[str]:
        """Evidence ids in sorted order (stable across SDK and JSON)."""
        return sorted(self.evidence)

    @property
    def empty(self) -> bool:
        """No quantity and no evidence for the Work."""
        return not self.quantities and not self.evidence

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form."""
        return self.model_dump(mode="json")

    def to_json(self, **kw: Any) -> str:
        """Stable JSON text (sorted keys)."""
        kw.setdefault("sort_keys", True)
        return json.dumps(self.to_dict(), **kw)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LiteratureEvidenceBundle:
        """Validate a bundle from its JSON-ready form."""
        return cls.model_validate(dict(data))

    @classmethod
    def from_json(cls, text: str) -> LiteratureEvidenceBundle:
        """Validate a bundle from JSON text."""
        return cls.model_validate_json(text)


def _quantity(record: QuantityRecord) -> BundledQuantity:
    caveats: list[str] = [m.value for m in record.missing]
    caveats.extend(f"tag_reason:{r}" for r in record.tag_reasons)
    proposed = record.proposed_evidence_source
    if proposed is not None and proposed is not record.evidence_source:
        caveats.append(f"retagged:{proposed.value}->{record.evidence_source.value}")
    q = record.quantity
    return BundledQuantity(
        quantity_id=record.quantity_id, name=record.name,
        raw_value=q.raw_value if q else None, raw_unit=q.raw_unit if q else "",
        value=q.value if q else None, unit=(q.unit or None) if q else None,
        dimension=q.dimension if q else None,
        conversion_profile=q.profile if q and q.converted else None,
        conversion_refused=(q.reason or None) if q else None,
        evidence_source=record.evidence_source.value, presented_as=record.evidence_source.presented_as,
        paper_measurement=record.evidence_source.is_paper_measurement,
        uncertainty=BundledUncertainty(value=record.uncertainty.value, kind=record.uncertainty.kind.value),
        extraction_confidence=record.extraction_confidence, evidence_ids=tuple(record.evidence_ids),
        sample_id=record.sample_id, processing_state=record.processing_state,
        qualitative_value=record.qualitative_value,
        derivation=dict(record.derivation) if record.derivation else None,
        caveats=tuple(dict.fromkeys(caveats)), status=record.status,
    )


def _evidence(record: EvidenceRecord, *, include_quotes: bool) -> BundledEvidence:
    ref = record.ref
    caveats: list[str] = [m.value for m in record.missing]
    if record.caveat:
        caveats.append(record.caveat)
    if ref.match_type is MatchType.PARAPHRASE:
        caveats.append("paraphrase: not a quotation")
    return BundledEvidence(
        evidence_id=ref.evidence_id, source_version=ref.source_version, source_sha256=ref.source_sha256,
        page=ref.locator.page, section=ref.locator.section, char_span=ref.locator.char_span,
        section_label=record.section_label, located_by=record.located_by, match_type=ref.match_type.value,
        verified_quote_match=record.verified_quote_match,
        quote=ref.quote if include_quotes else None, parser_revision=ref.parser_revision,
        strategy=record.strategy, caveats=tuple(dict.fromkeys(caveats)), status=record.status,
    )


def build_bundle(
    work_id: str,
    evidence: Mapping[str, EvidenceRecord],
    quantities: Mapping[str, QuantityRecord],
    *,
    include_quotes: bool = False,
) -> LiteratureEvidenceBundle:
    """Build the bundle of ``work_id`` from candidate records (an ``InMemoryEvidenceSink``'s mappings).

    Only records whose ``work_id`` matches are included. Evidence a quantity cites but the
    mapping lacks is reported as a caveat, never invented.
    """
    quantity_records = sorted((q for q in quantities.values() if q.work_id == work_id),
                              key=lambda q: q.quantity_id)
    evidence_records = {eid: rec for eid, rec in evidence.items() if rec.ref.work_id == work_id}
    versions = sorted({rec.ref.source_version for rec in evidence_records.values()})
    caveats: list[str] = [_CANDIDATE_CAVEAT, _QUOTES_PRIVATE_CAVEAT if include_quotes else _QUOTES_OMITTED_CAVEAT]
    source_version = versions[0] if len(versions) == 1 else None
    if len(versions) > 1:
        caveats.append(f"evidence spans {len(versions)} source versions; records are listed, not merged")
    digests = sorted({rec.ref.source_sha256 for rec in evidence_records.values() if rec.ref.source_sha256})
    source_sha256 = digests[0] if len(digests) == 1 else None
    parsers = [rec.parser for rec in evidence_records.values() if rec.parser]
    parser: dict[str, Any] = dict(parsers[0]) if parsers and all(p == parsers[0] for p in parsers) else {}
    dangling = sorted({eid for q in quantity_records for eid in q.evidence_ids if eid not in evidence_records})
    if dangling:
        caveats.append(f"{len(dangling)} cited evidence id(s) not in the source: {', '.join(dangling[:5])}")
    return LiteratureEvidenceBundle(
        work_id=work_id, source_version=source_version, source_sha256=source_sha256,
        source_versions=tuple(versions), parser=parser,
        quantities=tuple(_quantity(q) for q in quantity_records),
        evidence={eid: _evidence(evidence_records[eid], include_quotes=include_quotes)
                  for eid in sorted(evidence_records)},
        caveats=tuple(caveats),
    )


def bundle_work_ids(evidence: Mapping[str, EvidenceRecord], quantities: Mapping[str, QuantityRecord]) -> list[str]:
    """Work ids present in the records, sorted."""
    ids: Iterable[str] = (*(r.ref.work_id for r in evidence.values()), *(q.work_id for q in quantities.values()))
    return sorted(set(ids))


__all__ = (
    "BUNDLE_VERSION",
    "BundledEvidence",
    "BundledQuantity",
    "BundledUncertainty",
    "LiteratureEvidenceBundle",
    "build_bundle",
    "bundle_work_ids",
)

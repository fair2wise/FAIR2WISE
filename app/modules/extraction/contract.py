"""Normalized extraction-result contract shared by every extraction strategy (T06).

A strategy reads one :class:`ExtractionSource` and returns one
:class:`ExtractionResult`. The result is a *proposal*: mentions and observations are
what the strategy says the source contains, not validated science. Validation
(:mod:`app.modules.extraction.record_validation`) and evidence location
(:mod:`app.modules.evidence`) run afterwards, and nothing here maps onto new F2W
schema slots, merges entities or publishes (see ``H3_GATES``).

Two rules keep the contract lossless:

- ``side_outputs`` carries a strategy's native output verbatim (for the default
  F2W strategy: the terms JSON entries, code snippets and metadata exactly as the
  extractor produced them), so no existing field is dropped because another
  strategy lacks it;
- ``unmapped`` carries donor fields that have no approved F2W home yet, versioned,
  instead of discarding them.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..evidence.records import ReportedQuantity
from ..literature.parsed_document import ParsedDocument

#: Version of this result contract.
EXTRACTION_CONTRACT_VERSION = "t06-draft.1"


@dataclass(frozen=True)
class ExtractionSource:
    """One source version to extract from.

    Attributes:
        work_id: Canonical Work identifier.
        document: Parsed source version (sections with pages and parser metadata).
        pdf_path: Local PDF path for strategies that read the PDF themselves.
        page_texts: Per-page text, for page-oriented strategies without a PDF.
        filename: Display filename used by the legacy extractor as ``source_paper``.
        pub_meta: Bibliographic metadata already resolved for this Work.
        title: Title given to strategies that use it as prompt context.
    """

    work_id: str
    document: Optional[ParsedDocument] = None
    pdf_path: Optional[str] = None
    page_texts: Tuple[str, ...] = ()
    filename: str = ""
    pub_meta: Mapping[str, Any] = field(default_factory=dict)
    title: str = ""

    def sections(self) -> Dict[str, str]:
        """``{section name: stored text}`` in document order (names de-duplicated)."""
        out: Dict[str, str] = {}
        for index, section in enumerate(self.document.sections if self.document else ()):
            name = section.name or f"section-{index:05d}"
            if name in out:
                name = f"{name} [{section.structural_id or index}]"
            out[name] = section.text
        return out


@dataclass(frozen=True)
class ChunkCoverage:
    """What happened to one bounded piece of source text.

    Attributes:
        chunk_id: ``<section>:<start>:<end>`` or ``page:<n>``.
        section: Section name or ``None`` for page-oriented strategies.
        start / end: Character offsets in the section text (``None`` for pages).
        ok: ``True`` model answered validly, ``False`` failed, ``None`` unknown
            (the legacy extractor logs failures without reporting them).
        extracted_by: ``llm``, ``keyword``, ``legacy_page``, ``legacy_pdf`` or ``not_applicable``.
        error / reason: Failure or disposition reason.
        settings: Non-secret inference settings used for this chunk.
    """

    chunk_id: str
    section: Optional[str]
    start: Optional[int]
    end: Optional[int]
    ok: Optional[bool]
    extracted_by: str
    error: Optional[str] = None
    reason: Optional[str] = None
    settings: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractedMention:
    """One term a strategy says the source names.

    Attributes:
        surface: The text as the source (or model) gave it.
        category: The strategy's own category label (F2W schema class for the
            default strategy, donor taxon for FORGE strategies). Not remapped here.
        canonical: Whether the strategy resolved it against an approved vocabulary.
        slug: Strategy identifier, if any.
        section / page / chunk_id: Where the strategy saw it.
        quote: Supporting text as the strategy returned it (located later).
        extracted_by: Method that produced it.
        attributes: Strategy-native extra fields (formula, ChEBI, software ...).
    """

    surface: str
    category: str
    canonical: bool = False
    slug: str = ""
    section: Optional[str] = None
    page: Optional[int] = None
    chunk_id: Optional[str] = None
    quote: str = ""
    extracted_by: str = ""
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionResult:
    """Normalized output of one strategy run over one source version."""

    strategy: str
    strategy_version: str
    work_id: str
    source_version: Optional[str]
    parser_revision: Optional[str]
    complete: bool
    mentions: Tuple[ExtractedMention, ...] = ()
    observations: Tuple[ReportedQuantity, ...] = ()
    coverage: Tuple[ChunkCoverage, ...] = ()
    side_outputs: Mapping[str, Any] = field(default_factory=dict)
    unmapped: Mapping[str, Any] = field(default_factory=dict)
    inference: Mapping[str, Any] = field(default_factory=dict)
    contract_version: str = EXTRACTION_CONTRACT_VERSION

    @property
    def incomplete_chunks(self) -> Tuple[str, ...]:
        """Chunk ids that failed and need a retry (for staging ``incomplete_chunks``)."""
        return tuple(c.chunk_id for c in self.coverage if c.ok is False)

    def summary(self) -> Dict[str, Any]:
        """Small comparison summary (counts by category, completeness)."""
        by_category: Dict[str, int] = {}
        for mention in self.mentions:
            by_category[mention.category] = by_category.get(mention.category, 0) + 1
        return {
            "strategy": self.strategy, "strategy_version": self.strategy_version,
            "complete": self.complete, "mentions": len(self.mentions),
            "observations": len(self.observations), "by_category": dict(sorted(by_category.items())),
            "incomplete_chunks": list(self.incomplete_chunks),
        }


def frozen_copy(value: Any) -> Any:
    """Deep copy so a result never aliases a strategy's mutable state."""
    return copy.deepcopy(value)


def compare_results(results: Sequence[ExtractionResult]) -> Dict[str, Any]:
    """Side-by-side comparison of strategies run on the same source.

    Reports surfaces per strategy and their overlap (case-folded). It is a review
    aid: overlap is not identity, and no merge follows from it.
    """
    surfaces = {r.strategy: sorted({m.surface.casefold() for m in r.mentions}) for r in results}
    sets = [set(v) for v in surfaces.values()]
    shared = sorted(set.intersection(*sets)) if sets else []
    return {
        "work_ids": sorted({r.work_id for r in results}),
        "source_versions": sorted({str(r.source_version) for r in results}),
        "summaries": [r.summary() for r in results],
        "surfaces": surfaces,
        "shared_surfaces": shared,
    }


__all__ = [
    "EXTRACTION_CONTRACT_VERSION", "ChunkCoverage", "ExtractedMention", "ExtractionResult", "ExtractionSource",
    "compare_results", "frozen_copy",
]

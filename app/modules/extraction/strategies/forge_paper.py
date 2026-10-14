"""Optional FORGE section-entity strategy (E-01), off by default.

Ports the donor's per-section hybrid extractor: optional keyword candidates, model
refinement of overlapping chunks (6000 chars, step 5600), one split retry on
timeout/length/JSON failures, explicit per-chunk coverage, bibliography sections
recorded as ``not_applicable`` rather than mined, and a ``ResolvedTerm`` split
between canonical terms and honest candidates.

Changes from the donor, all deliberate:

- **Inference** goes through :class:`~app.modules.extraction.structured_output.TextCompletionAdapter`
  over an existing F2W chat client. No LangChain/Ollama/vLLM client is built, no
  ``OLLAMA_MODEL`` default is read, and no ``vllm:`` routing exists.
- **Vocabulary** (C-01) is not imported. Keyword taxonomy and term resolution are
  injected; the defaults are an empty taxonomy and :class:`CandidateOnlyResolver`,
  so every term stays a candidate until an approved vocabulary is wired (T07/H3).
- **Side effects** are removed: no artifact store, uncertainty queue, vocabulary
  registry/promotion or chunk cache. Low-confidence and failed chunks are visible
  in coverage for the review stage to pick up.
- **Categories** stay donor taxa (technique/analysis/instrument/material); their
  mapping to F2W classes is pending owner approval at H3.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/pdf_extraction.py
(``Hit``, ``RefinedExtraction``, ``ResolvedTerm``, ``SectionEntities``,
``PaperExtraction``, ``extract_keywords``, ``llm_refine``, ``extract_paper``)
(BSD-3-Clause).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Pattern, Protocol, Sequence, Tuple

from ...literature.parsed_document import without_embedded_images
from ..contract import ChunkCoverage, ExtractedMention, ExtractionResult, ExtractionSource
from ..structured_output import TextCompletionAdapter, parse_json_object

logger = logging.getLogger(__name__)

STRATEGY_NAME = "forge_paper"
STRATEGY_VERSION = "entity-extraction-v5-f2w"
PROMPT_VERSION = "forge-entity-v5"

CHUNK_CHARS = 6000
CHUNK_STEP = 5600
SPLIT_MIN_CHARS = 1600
SPLIT_OVERLAP = 200
#: Below this model confidence a chunk is flagged for review (donor threshold).
REVIEW_CONFIDENCE = 0.65

TAXA = ("technique", "analysis", "instrument", "material")

_BIBLIOGRAPHY = re.compile(
    r"\s*(?:[\d.]+\s*)?(?:references|bibliography|literature cited)\s*:?(?:\s*\[\d+\])?\s*", re.IGNORECASE
)
_RETRYABLE_ERRORS = {"APITimeoutError", "ReadTimeout", "Timeout", "TimeoutError", "LengthFinishReasonError",
                     "JSONDecodeError"}
_EXPLICIT_SMILES = re.compile(r"^smiles\s*:\s*([^\s,;]+)", re.IGNORECASE)


@dataclass
class Hit:
    """One keyword match inside a section (span/snippet from the first match)."""

    taxon: str
    slug: str
    name: str
    span: Tuple[int, int]
    snippet: str


@dataclass
class RefinedExtraction:
    """Model refinement of one chunk. ``ok`` separates "said nothing" from "never reached"."""

    techniques: List[str] = field(default_factory=list)
    analyses: List[Dict[str, Any]] = field(default_factory=list)
    instruments: List[str] = field(default_factory=list)
    materials: List[str] = field(default_factory=list)
    ok: bool = False
    error: str = ""
    confidence: float = 0.5
    executed_model: Optional[str] = None


@dataclass(frozen=True)
class ResolvedTerm:
    """One extracted term after resolution; ``surface`` is kept as evidence."""

    slug: str
    name: str
    surface: str
    canonical: bool
    category: str
    software: Optional[str] = None
    extracted_by: Optional[str] = None
    chunk_id: Optional[str] = None
    identity: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class SectionEntities:
    """What one section yielded."""

    terms: List[ResolvedTerm] = field(default_factory=list)
    chunks: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def llm_ok(self) -> bool:
        """Every chunk of this section answered."""
        return bool(self.chunks) and all(c["ok"] for c in self.chunks)


@dataclass
class PaperExtraction:
    """Aggregates plus the per-section truth."""

    techniques: List[str] = field(default_factory=list)
    analyses: List[Dict[str, Any]] = field(default_factory=list)
    instruments: List[str] = field(default_factory=list)
    materials: List[str] = field(default_factory=list)
    per_section: Dict[str, List[Hit]] = field(default_factory=dict)
    llm_ok: bool = False
    by_section: Dict[str, SectionEntities] = field(default_factory=dict)


#: ``{taxon: [(slug, compiled pattern), ...]}``; injected, empty by default.
KeywordTaxonomy = Mapping[str, Sequence[Tuple[str, Pattern[str]]]]


class TermResolver(Protocol):
    """Resolve one free-text term to a canonical or candidate :class:`ResolvedTerm`."""

    def __call__(self, surface: str, category: str, *, software: Optional[str] = None) -> Optional[ResolvedTerm]:
        """Return the resolved term, or ``None`` for a surface that slugs to nothing."""


def simple_slug(text: str) -> str:
    """NFKD-fold, lowercase, non-alphanumerics to ``_`` (identifier only, not identity)."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", folded).strip("_")


class CandidateOnlyResolver:
    """Default resolver: no vocabulary, so every term is a candidate (never canonical)."""

    def __call__(self, surface: str, category: str, *, software: Optional[str] = None) -> Optional[ResolvedTerm]:
        surface = (surface or "").strip()
        if not surface:
            return None
        smiles = _EXPLICIT_SMILES.match(surface) if category == "material" else None
        if smiles:
            return ResolvedTerm(slug="smiles_" + simple_slug(smiles.group(1)), name=smiles.group(1),
                                surface=surface, canonical=False, category=category, software=software,
                                identity={"method": "explicit_smiles_unverified", "smiles": smiles.group(1)})
        slug = simple_slug(surface)
        if not slug:
            return None
        return ResolvedTerm(slug=slug, name=surface, surface=surface, canonical=False, category=category,
                            software=software, identity={"method": "not_in_vocabulary"})


def _snippet(text: str, start: int, end: int, window: int = 40) -> str:
    return text[max(0, start - window):min(len(text), end + window)]


def extract_keywords(section_text: str, taxonomy: Optional[KeywordTaxonomy] = None) -> List[Hit]:
    """One :class:`Hit` per slug per taxon (first match), from an injected taxonomy."""
    seen: set = set()
    hits: List[Hit] = []
    for taxon, patterns in (taxonomy or {}).items():
        for slug, pattern in patterns:
            if slug in seen:
                continue
            match = pattern.search(section_text)
            if match:
                seen.add(slug)
                start, end = match.span()
                hits.append(Hit(taxon=taxon, slug=slug, name=match.group(0).strip(), span=(start, end),
                                snippet=_snippet(section_text, start, end)))
    return hits


SYSTEM_PROMPT = (
    "You are a scientific text extraction assistant. "
    "Extract only terms explicitly present in the supplied section. "
    "Never infer substances or methods from citations, author initials, or journal titles. "
    "A bibliography without explicit scientific descriptions yields empty lists. "
    "Keyword candidates are untrusted hints, not evidence. "
    "Use concise source names, each once; never emit vocabulary IDs or hashes. "
    "Respond ONLY with a single JSON object — no prose, no markdown fences. "
    "Schema: "
    '{"techniques": [str], '
    '"analyses": [{"method": str, "software": str|null, "target_material": str|null}], '
    '"instruments": [str], '
    '"materials": [str], '
    '"confidence": float}'
)


def build_refine_prompt(section_text: str, candidate_hits: Sequence[Hit]) -> str:
    """System + user prompt as one text prompt for the F2W sync client."""
    candidates = "\n".join(f"- {h.taxon}: {h.name}" for h in candidate_hits)
    user = (
        f"Section text:\n{section_text}\n\n"
        f"Candidate keyword hits:\n{candidates}\n\n"
        "Extract all experimental techniques, analysis methods, instruments, "
        "and materials mentioned. Preserve full names and acronyms as written. "
        "For a SMILES string explicitly present in the source, prefix the material "
        "with SMILES:; never infer a structure from a name or formula. "
        "Return only the JSON object."
    )
    return f"{SYSTEM_PROMPT}\n\n{user}"


def _validated(data: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("techniques", "instruments", "materials"):
        values = data.get(key) or []
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(f"{key} must be a list of strings")
    analyses = data.get("analyses") or []
    if not isinstance(analyses, list) or not all(
        isinstance(v, dict) and isinstance(v.get("method"), str) for v in analyses
    ):
        raise ValueError("analyses must contain objects with method strings")
    confidence = float(data.get("confidence", 0.5))
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be within [0, 1]")
    return {**data, "confidence": confidence}


def llm_refine(
    section_text: str, candidate_hits: Sequence[Hit], adapter: TextCompletionAdapter
) -> RefinedExtraction:
    """Refine one chunk through the adapter; any failure returns ``ok=False`` with the error type.

    At most two attempts when the reply is empty (donor behaviour); the client
    itself performs no hidden retries on this path.
    """
    prompt = build_refine_prompt(section_text, candidate_hits)
    try:
        raw = ""
        for attempt in range(2):
            raw = adapter.complete(prompt).text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\n?```$", "", raw.strip())
            if raw:
                break
            logger.info("Empty extraction reply on attempt %d; asking again", attempt + 1)
        data = parse_json_object(raw)
        if data is None:
            raise json.JSONDecodeError("extraction must be a JSON object", raw, 0)
        data = _validated(data)
        return RefinedExtraction(
            techniques=list(data.get("techniques") or []), analyses=list(data.get("analyses") or []),
            instruments=list(data.get("instruments") or []), materials=list(data.get("materials") or []),
            confidence=data["confidence"], executed_model=adapter.model, ok=True,
        )
    except Exception as exc:  # noqa: BLE001 - a failed chunk is reported, never raised
        logger.warning("Entity refinement failed via %s (%s); chunk recorded as incomplete",
                       adapter.settings().get("client_class"), type(exc).__name__)
        return RefinedExtraction(error=type(exc).__name__)


def _chunks(text: str) -> List[Tuple[int, int, str]]:
    out: List[Tuple[int, int, str]] = []
    for start in range(0, len(text), CHUNK_STEP):
        chunk = text[start:start + CHUNK_CHARS]
        if chunk.strip():
            out.append((start, start + len(chunk), chunk))
        if start + CHUNK_CHARS >= len(text):
            break
    return out


def _refine_with_split(text: str, hits: Sequence[Hit], adapter: TextCompletionAdapter,
                       taxonomy: Optional[KeywordTaxonomy]) -> RefinedExtraction:
    answer = llm_refine(text, hits, adapter)
    if answer.ok or len(text) <= SPLIT_MIN_CHARS or answer.error not in _RETRYABLE_ERRORS:
        return answer
    middle = len(text) // 2
    halves = (text[:middle + SPLIT_OVERLAP], text[middle - SPLIT_OVERLAP:])
    pieces = [llm_refine(half, extract_keywords(half, taxonomy), adapter) for half in halves]
    if not all(p.ok for p in pieces):
        return answer

    def merged(name: str) -> List[Any]:
        unique: Dict[str, Any] = {}
        for piece in pieces:
            for value in getattr(piece, name):
                unique.setdefault(json.dumps(value, sort_keys=True), value)
        return list(unique.values())

    return RefinedExtraction(
        techniques=merged("techniques"), analyses=merged("analyses"), instruments=merged("instruments"),
        materials=merged("materials"), ok=True, confidence=min(p.confidence for p in pieces),
        executed_model=pieces[0].executed_model,
    )


def extract_paper(
    sections: Mapping[str, str],
    adapter: TextCompletionAdapter,
    *,
    taxonomy: Optional[KeywordTaxonomy] = None,
    resolver: Optional[TermResolver] = None,
    max_workers: int = 1,
) -> PaperExtraction:
    """Extract technique/analysis/instrument/material mentions from every section.

    Args:
        sections: ``{section name: section text}``.
        adapter: Inference adapter over an existing F2W client.
        taxonomy: Optional keyword taxonomy (empty by default).
        resolver: Term resolver (candidate-only by default).
        max_workers: Parallel chunk refinements (1 = deterministic order of calls).
    """
    resolve: TermResolver = resolver or CandidateOnlyResolver()
    sections = {name: without_embedded_images(text) for name, text in sections.items()}
    by_section: Dict[str, SectionEntities] = {}
    per_section: Dict[str, List[Hit]] = {}
    tasks: List[Tuple[str, int, int, str, List[Hit]]] = []
    for name, text in sections.items():
        by_section[name] = SectionEntities()
        if _BIBLIOGRAPHY.fullmatch(name):
            per_section[name] = []
            by_section[name].chunks.append({
                "id": f"{name}:0:{len(text)}", "start": 0, "end": len(text), "ok": True,
                "extracted_by": "not_applicable", "reason": "bibliography_not_primary_evidence",
                "requested_model": adapter.model, "executed_model": None, "error": None,
            })
            continue
        per_section[name] = extract_keywords(text, taxonomy)
        for start, end, chunk in _chunks(text):
            tasks.append((name, start, end, chunk, extract_keywords(chunk, taxonomy)))

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            refined = list(pool.map(lambda t: _refine_with_split(t[3], t[4], adapter, taxonomy), tasks))
    else:
        refined = [_refine_with_split(t[3], t[4], adapter, taxonomy) for t in tasks]

    aggregates: Dict[str, Dict[str, Any]] = {taxon: {} for taxon in TAXA}
    for (section_name, start, end, _text, hits), answer in zip(tasks, refined):
        chunk_id = f"{section_name}:{start}:{end}"
        method = "llm" if answer.ok else "keyword"
        by_section[section_name].chunks.append({
            "id": chunk_id, "start": start, "end": end, "ok": answer.ok, "extracted_by": method,
            "requested_model": adapter.model, "executed_model": answer.executed_model,
            "error": None if answer.ok else (answer.error or "model_unavailable_or_invalid_response"),
            "confidence": answer.confidence if answer.ok else None,
            "needs_review": (not answer.ok) or answer.confidence < REVIEW_CONFIDENCE,
        })
        terms: List[Optional[ResolvedTerm]] = []
        if answer.ok:
            terms += [resolve(t, "technique") for t in answer.techniques]
            terms += [resolve(str(a.get("method", "")), "analysis", software=a.get("software") or None)
                      for a in answer.analyses]
            terms += [resolve(i, "instrument") for i in answer.instruments]
            terms += [resolve(m, "material") for m in answer.materials]
        else:
            terms += [resolve(h.name, h.taxon) for h in hits]
        section_terms = [replace(t, extracted_by=method, chunk_id=chunk_id) for t in terms if t]
        by_section[section_name].terms.extend(section_terms)
        for term in section_terms:
            bucket = aggregates.setdefault(term.category, {})
            if term.slug in bucket:
                continue
            if term.category == "analysis":
                bucket[term.slug] = {"method": term.slug, **({"software": term.software} if term.software else {})}
            else:
                bucket[term.slug] = term.name if term.category == "material" else term.slug
    return PaperExtraction(
        techniques=list(aggregates["technique"].values()), analyses=list(aggregates["analysis"].values()),
        instruments=list(aggregates["instrument"].values()), materials=list(aggregates["material"].values()),
        per_section=per_section, by_section=by_section,
        llm_ok=bool(refined) and all(r.ok for r in refined),
    )


class ForgePaperStrategy:
    """E-01 as a strategy behind the normalized contract. Not registered as default.

    Args:
        adapter: Inference adapter over an existing F2W chat client.
        taxonomy / resolver: Injected vocabulary hooks (see module docstring).
        max_workers: Parallel chunk refinements.
    """

    name = STRATEGY_NAME
    version = STRATEGY_VERSION

    def __init__(self, adapter: TextCompletionAdapter, *, taxonomy: Optional[KeywordTaxonomy] = None,
                 resolver: Optional[TermResolver] = None, max_workers: int = 1) -> None:
        self.adapter = adapter
        self.taxonomy = taxonomy
        self.resolver = resolver
        self.max_workers = max_workers

    def extract(self, source: ExtractionSource) -> ExtractionResult:
        """Run :func:`extract_paper` over the parsed sections of ``source``."""
        if source.document is None:
            raise ValueError("forge_paper needs a parsed document")
        sections = source.sections()
        paper = extract_paper(sections, self.adapter, taxonomy=self.taxonomy, resolver=self.resolver,
                              max_workers=self.max_workers)
        pages = {name: s.page for name, s in zip(sections, source.document.sections)}
        mentions: List[ExtractedMention] = []
        coverage: List[ChunkCoverage] = []
        for name, entities in paper.by_section.items():
            for chunk in entities.chunks:
                coverage.append(ChunkCoverage(
                    chunk_id=chunk["id"], section=name, start=chunk["start"], end=chunk["end"], ok=chunk["ok"],
                    extracted_by=chunk["extracted_by"], error=chunk.get("error"), reason=chunk.get("reason"),
                    settings=self.adapter.settings(),
                ))
            for term in entities.terms:
                mentions.append(ExtractedMention(
                    surface=term.surface, category=term.category, canonical=term.canonical, slug=term.slug,
                    section=name, page=pages.get(name), chunk_id=term.chunk_id,
                    extracted_by=term.extracted_by or "",
                    quote=_surface_quote(term.surface, sections.get(name, "")),
                    attributes={k: v for k, v in {"software": term.software,
                                                  "identity": dict(term.identity)}.items() if v},
                ))
        needs_review = [c["id"] for e in paper.by_section.values() for c in e.chunks if c.get("needs_review")]
        return ExtractionResult(
            strategy=self.name, strategy_version=self.version, work_id=source.work_id,
            source_version=source.document.source_ref, parser_revision=source.document.parser_key,
            complete=paper.llm_ok, mentions=tuple(mentions), coverage=tuple(coverage),
            side_outputs={"aggregates": {"techniques": paper.techniques, "analyses": paper.analyses,
                                         "instruments": paper.instruments, "materials": paper.materials},
                          "needs_review_chunks": needs_review},
            unmapped={"donor_taxa": list(TAXA), "note": "taxon -> F2W class mapping pending H3"},
            inference={**self.adapter.settings(), "prompt_version": PROMPT_VERSION},
        )


def _surface_quote(surface: str, text: str) -> str:
    """The surface as it appears in the section (case-insensitive), else ``""``; never invented."""
    if not surface or not text:
        return ""
    match = re.search(r"(?<!\w)" + re.escape(surface) + r"(?!\w)", text, re.IGNORECASE)
    return match.group(0) if match else ""


__all__ = [
    "PROMPT_VERSION", "STRATEGY_NAME", "STRATEGY_VERSION", "CandidateOnlyResolver", "ForgePaperStrategy", "Hit",
    "KeywordTaxonomy", "PaperExtraction", "RefinedExtraction", "ResolvedTerm", "SectionEntities", "TermResolver",
    "build_refine_prompt", "extract_keywords", "extract_paper", "llm_refine", "simple_slug",
]

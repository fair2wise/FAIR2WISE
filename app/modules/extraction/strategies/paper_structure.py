"""Optional FORGE paper structure-reading strategy (E-08, selective), off by default.

Ports only the *paper observation* half of the donor module: the
:class:`PaperStructureExtraction` models (values and peaks exactly as printed, with
quote, location, sample/processing qualifiers, measured uncertainty and the
reported/cited/derived/assumed status), the bounded/overlapping chunk loop with
validation-error feedback retries, and the attribution detector that tells a
literature contrast from the paper's own claim.

Not ported, by design (they remain FORGE): ``SimulationCandidate``, ``gate_and_map``,
``looks_simulatable``, ``names_unsimulatable_phase`` and every
``forge.core.geometry.bl733`` default. No beam energy, detector distance, pixel size
or beam centre is ever injected; the prompt tells the model not to report them.

Inference goes through :class:`~app.modules.extraction.structured_output.TextCompletionAdapter`
over an existing F2W chat client; the donor's injected LangChain model is replaced.
A ``None`` result means "we do not know", never "there is no structure".

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/literature/extraction.py
(``ObservationContext``, ``ExtractedValue``, ``ReportedPeak``,
``PaperStructureExtraction``, ``attributed_phase_mentions``,
``extract_structure_from_text``) (BSD-3-Clause).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from ...evidence.records import EvidenceSource, MeasuredUncertainty, ReportedQuantity, UncertaintyKind
from ..contract import ChunkCoverage, ExtractionResult, ExtractionSource
from ..structured_output import TextCompletionAdapter, parse_json_object, strip_control_chars

logger = logging.getLogger(__name__)

STRATEGY_NAME = "forge_paper_structure"
STRATEGY_VERSION = "structure-v4-f2w"
#: Donor prompt v4 with the simulation framing removed.
EXTRACTION_PROMPT_VERSION = "4-f2w.1"

QuantityName = Literal[
    "lamellar_period", "cylinder_center_spacing", "d10_spacing", "domain_spacing", "cylinder_radius",
    "sphere_radius", "bcc_lattice_constant", "fcc_lattice_constant", "film_thickness", "first_peak_q",
    "grating_pitch", "line_height", "line_bottom_width", "line_top_width",
]
Morphology = Literal["lamellar", "hex_cylinder", "bcc_sphere", "fcc_sphere", "gyroid", "disordered", "other",
                     "unknown"]


class ObservationContext(BaseModel):
    """Qualifications for one occurrence, not a paper-wide sample guess."""

    sample_id: str = ""
    processing_state: str = ""
    temperature_k: Optional[float] = None
    uncertainty: Optional[float] = Field(default=None, ge=0)
    uncertainty_kind: Literal["standard_deviation", "standard_error", "interval", "unknown"] = "unknown"
    observation_status: Literal["reported", "cited", "derived", "assumed"] = "reported"


class ExtractedValue(ObservationContext):
    """One number as reported, with the sentence it came from."""

    quantity: QuantityName
    value: float
    unit: str = ""
    location: str = ""
    quote: str = ""
    caveat: str = ""


class ReportedPeak(ObservationContext):
    """One scattering peak as reported (d-spacing or q, never derived from the other)."""

    label: str = ""
    d_spacing_value: Optional[float] = None
    d_spacing_unit: str = ""
    q_value: Optional[float] = None
    q_unit: str = ""
    direction: Literal["in_plane", "out_of_plane", "isotropic", "unknown"] = "unknown"
    location: str = ""
    quote: str = ""


class PaperStructureExtraction(BaseModel):
    """What a paper reports about a structure."""

    morphology: Morphology
    extraction_complete: bool = True
    coverage: List[Dict[str, Any]] = Field(default_factory=list)
    material_system: str = ""
    orientation: Literal["perpendicular", "parallel", "mixed", "unknown"] = "unknown"
    quantities: List[ExtractedValue] = Field(default_factory=list)
    peaks: List[ReportedPeak] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: str = ""


# --- attribution: a literature contrast is not this paper's claim ---------

_ATTRIBUTION_CUES = (
    r"(?:other|some|several|previous|earlier)\s+(?:research\s+)?"
    r"(?:groups?|workers?|authors?|studies|reports?|work)",
    r"\bet\s+al\.?",
    r"(?:has|have|had|was|were|been)\s+(?:previously\s+|also\s+)?"
    r"(?:been\s+)?(?:reported|observed|described|proposed|suggested|assigned)",
    r"\b(?:in|by)\s+(?:contrast|comparison)\s+(?:to|with)",
    r"\b(?:unlike|whereas)\b",
    r"\bref(?:s|erences?)?\.?\s*\d",
    r"\bliterature\b",
)
_ATTRIBUTION_RE = re.compile("|".join(_ATTRIBUTION_CUES), re.IGNORECASE)
_ABBREVIATIONS = ("et al", "e.g", "i.e", "cf", "vs", "fig", "figs", "eq", "eqs", "ref", "refs", "no", "vol",
                  "ca", "approx", "st", "nd", "rd", "th")
_PHASE_TERMS = re.compile(
    r"\b(?:gyroid(?:al|s)?|ia\s*-?\s*3\s*-?\s*d|plumber'?s\s+nightmare|double\s+diamond|"
    r"pn\s*-?\s*3\s*-?\s*m|fddd|perforated\s+lamella(?:e|r)?)\b",
    re.IGNORECASE,
)


def _ends_abbreviation(text: str, dot: int) -> bool:
    prefix = text[max(0, dot - 12):dot].lower()
    return any(prefix.endswith(a) for a in _ABBREVIATIONS)


def sentence_around(text: str, start: int, end: int) -> str:
    """The sentence containing ``text[start:end]`` (abbreviation periods skipped)."""
    left, cursor = 0, start
    while True:
        dot = text.rfind(".", 0, cursor)
        if dot == -1:
            break
        if not _ends_abbreviation(text, dot):
            left = dot + 1
            break
        cursor = dot
    right, cursor = len(text), end
    while cursor < len(text):
        dot = min((n for n in (text.find(c, cursor) for c in ".!?") if n != -1), default=-1)
        if dot == -1:
            break
        if not _ends_abbreviation(text, dot):
            right = dot + 1
            break
        cursor = dot + 1
    return text[left:right].strip()


def is_attributed(sentence: str) -> bool:
    """Whether a sentence attributes its claim to other work (cue-based, lexical only)."""
    return bool(_ATTRIBUTION_RE.search(sentence or ""))


def attributed_phase_mentions(text: str) -> List[Tuple[str, str]]:
    """``(term, sentence)`` for named network phases inside sentences crediting other work."""
    found: List[Tuple[str, str]] = []
    for match in _PHASE_TERMS.finditer(text or ""):
        sentence = sentence_around(text, match.start(), match.end())
        if is_attributed(sentence):
            found.append((match.group(0), " ".join(sentence.split())))
    return found


_PROMPT = """You are a scattering expert (GISAXS, GIWAXS, SAXS, WAXS, RSoXS). Your job \
is to read what this paper REPORTS about sample structure: values, peaks and the \
sentences they appear in, exactly as written.

Return ONLY a JSON object, no prose and no markdown fence, with exactly these keys:

{{
  "morphology": one of "lamellar", "hex_cylinder", "bcc_sphere", "fcc_sphere",
                "gyroid", "disordered", "other", "unknown",
  "material_system": short name as written, e.g. "PS-b-PMMA" or "PM6:Y6",
  "orientation": "perpendicular" | "parallel" | "mixed" | "unknown",
  "quantities": [
    {{"quantity": one of "lamellar_period", "cylinder_center_spacing",
                   "d10_spacing", "domain_spacing", "cylinder_radius",
                   "sphere_radius", "bcc_lattice_constant",
                   "fcc_lattice_constant", "film_thickness", "first_peak_q",
                   "grating_pitch", "line_height", "line_bottom_width", "line_top_width",
      "value": number,
      "unit": exactly as the paper writes it ("nm", "angstrom", "A", "nm^-1"),
      "location": where you found it ("abstract", "results", "Table 1", "Fig 2b"),
      "quote": the sentence you read the number out of, copied EXACTLY,
      "caveat": "" or a short warning}}
  ],
  "peaks": [
    {{"label": the paper's own name for the reflection ("(100)", "010",
              "pi-pi stacking", "q1"),
      "d_spacing_value": number or null,
      "d_spacing_unit": exactly as written ("nm", "angstrom", "A") or "",
      "q_value": number or null,
      "q_unit": exactly as written ("nm^-1", "A^-1") or "",
      "direction": "in_plane" | "out_of_plane" | "isotropic" | "unknown",
      "location": where you found it,
      "quote": the sentence, copied EXACTLY}}
  ],
  "confidence": 0.0 to 1.0,
  "evidence": a short quote supporting the morphology
}}

For EVERY quantity and peak, also include these qualification fields:
sample_id (exact sample label or ""), processing_state (verbatim or ""),
temperature_k (only if reported in kelvin, else null), uncertainty (in the
reported value's units, else null), uncertainty_kind (standard_deviation,
standard_error, interval, or unknown), and observation_status (reported, cited,
derived, or assumed). Never merge observations from different samples or
conditions. Do not rename grating pitch/height/width as lamellar period or
film thickness. Preserve the paper's distinction between its own measurements
and values cited from other publications.

WHAT COUNTS AS A PEAK. Any reported reflection position: a d-spacing (lamellar
stacking ~2 nm, pi-pi ~0.36 nm, a (100) at 21 angstrom), or a q position
(q = 0.3 A^-1, 17.5 nm^-1). Report EVERY distinct one the paper states, with the
direction the paper gives (out-of-plane q_z, in-plane q_xy/q_r, or isotropic).
A paper that names no phase but reports peaks is still fully useful -- the peaks
are evidence on their own. Give each peak EITHER its d-spacing or its q, whichever
the paper prints; never derive one from the other.

RULES
- Report values EXACTLY as the paper gives them. Do not convert units.
- Do NOT report beam energy, detector distance, pixel size or beam centre even
  if present; they are not structural observations and none are assumed.
- If the paper does not report a structure, use "unknown" for morphology --
  and still report the peaks. Guessing a morphology is worse than saying
  nothing; a peak list is evidence, not a guess.
- A d-spacing and a nearest-neighbour distance are DIFFERENT numbers for a
  hexagonal lattice. Use "d10_spacing" for a reported d(10)/d-spacing and
  "cylinder_center_spacing" only for a centre-to-centre distance.
- "quote" must be COPIED from the text, not summarised or reconstructed. If you
  cannot find the sentence, leave it empty -- an invented quote is worse than
  no quote, because it looks like evidence.

PAPER TITLE: {title}

TEXT:
{text}
"""

_RETRYABLE = {"APITimeoutError", "ReadTimeout", "Timeout", "TimeoutError", "LengthFinishReasonError"}


def extract_structure_from_text(
    text: str,
    *,
    adapter: TextCompletionAdapter,
    title: str = "",
    max_chars: int = 8000,
    max_retries: int = 2,
) -> Optional[PaperStructureExtraction]:
    """Ask the model what structure the text reports; ``None`` means unknown, not absent.

    Long texts use overlapping chunks (overlap ``min(400, max_chars // 4)``) and keep
    every distinct observation; completeness requires every chunk to succeed.
    Validation errors are fed back on retry; a timeout on a long text splits it once.
    """
    if not text.strip():
        return None
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if len(text) > max_chars:
        overlap = min(400, max_chars // 4)
        outputs: List[PaperStructureExtraction] = []
        coverage: List[Dict[str, Any]] = []
        for start in range(0, len(text), max_chars - overlap):
            chunk = text[start:start + max_chars]
            answer = extract_structure_from_text(chunk, adapter=adapter, title=title, max_chars=max_chars,
                                                 max_retries=max_retries)
            coverage.append({"start": start, "end": start + len(chunk),
                             "ok": answer is not None and answer.extraction_complete})
            if answer is not None:
                outputs.append(answer)
            if start + max_chars >= len(text):
                break
        if not outputs:
            return None

        def unique(name: str) -> List[Any]:
            values: Dict[str, Any] = {}
            for answer in outputs:
                for observation in getattr(answer, name):
                    values.setdefault(observation.model_dump_json(), observation)
            return list(values.values())

        phases = {a.morphology for a in outputs if a.morphology not in ("unknown", "other")}
        phase = next(iter(phases)) if len(phases) == 1 else "unknown"
        orientations = {a.orientation for a in outputs if a.orientation != "unknown"}
        return PaperStructureExtraction(
            morphology=phase, material_system=outputs[0].material_system,
            orientation=next(iter(orientations)) if len(orientations) == 1 else "unknown",
            quantities=unique("quantities"), peaks=unique("peaks"),
            evidence=next((a.evidence for a in outputs if a.morphology == phase and a.evidence), ""),
            confidence=min(a.confidence for a in outputs), coverage=coverage,
            extraction_complete=all(c["ok"] for c in coverage),
        )

    text = strip_control_chars(text)
    prompt = _PROMPT.format(title=title or "(untitled)", text=text[:max_chars])
    feedback = ""
    for attempt in range(max_retries + 1):
        try:
            reply = adapter.complete(prompt + feedback).text
        except Exception as exc:  # noqa: BLE001 - best-effort by contract
            if len(text) > 1600 and type(exc).__name__ in _RETRYABLE:
                return extract_structure_from_text(text, adapter=adapter, title=title,
                                                   max_chars=max(800, len(text) // 2 + 200), max_retries=0)
            logger.warning("Structure extraction call failed: %s", type(exc).__name__)
            return None
        parsed = parse_json_object(reply)
        if parsed is None:
            feedback = "\n\nYour last reply was not valid JSON. Return ONLY the JSON object."
            continue
        try:
            return PaperStructureExtraction.model_validate(parsed)
        except ValidationError as exc:
            feedback = f"\n\nYour last reply failed validation:\n{exc}\nReturn corrected JSON."
            logger.debug("Structure extraction attempt %d failed validation", attempt + 1)
    logger.info("Could not extract a structure after %d attempts", max_retries + 1)
    return None


def _context(item: ObservationContext) -> Dict[str, Any]:
    return {
        "evidence_source": EvidenceSource(item.observation_status),
        "uncertainty": MeasuredUncertainty(item.uncertainty, UncertaintyKind(item.uncertainty_kind)),
        "sample_id": item.sample_id, "processing_state": item.processing_state,
    }


def to_reported_quantities(extraction: PaperStructureExtraction) -> List[ReportedQuantity]:
    """Flatten values and peaks into :class:`ReportedQuantity` proposals (units as printed).

    A peak contributes its d-spacing and/or its q exactly as reported, under
    ``peak_d_spacing``/``peak_q`` names qualified by the paper's label.
    """
    out: List[ReportedQuantity] = []
    for value in extraction.quantities:
        out.append(ReportedQuantity(
            name=value.quantity, raw_value=value.value, raw_unit=value.unit, quote=value.quote,
            location_label=value.location, extraction_confidence=extraction.confidence, caveat=value.caveat,
            **_context(value),
        ))
    for peak in extraction.peaks:
        suffix = f"[{peak.label}]" if peak.label else ""
        if peak.d_spacing_value is not None:
            out.append(ReportedQuantity(
                name=f"peak_d_spacing{suffix}", raw_value=peak.d_spacing_value, raw_unit=peak.d_spacing_unit,
                quote=peak.quote, location_label=peak.location, extraction_confidence=extraction.confidence,
                caveat=f"direction={peak.direction}", **_context(peak),
            ))
        if peak.q_value is not None:
            out.append(ReportedQuantity(
                name=f"peak_q{suffix}", raw_value=peak.q_value, raw_unit=peak.q_unit, quote=peak.quote,
                location_label=peak.location, extraction_confidence=extraction.confidence,
                caveat=f"direction={peak.direction}", **_context(peak),
            ))
    return out


class PaperStructureStrategy:
    """E-08 observation reading behind the normalized contract. Not registered as default."""

    name = STRATEGY_NAME
    version = STRATEGY_VERSION

    def __init__(self, adapter: TextCompletionAdapter, *, max_chars: int = 8000, max_retries: int = 2) -> None:
        self.adapter = adapter
        self.max_chars = max_chars
        self.max_retries = max_retries

    def extract(self, source: ExtractionSource) -> ExtractionResult:
        """Read structure observations from the parsed sections (bibliography excluded)."""
        if source.document is None:
            raise ValueError("forge_paper_structure needs a parsed document")
        sections = {name: text for name, text in source.sections().items()
                    if not re.fullmatch(r"\s*(?:[\d.]+\s*)?(?:references|bibliography|literature cited)\s*:?\s*",
                                        name, flags=re.IGNORECASE)}
        text = "\n\n".join(sections.values())
        extraction = extract_structure_from_text(text, adapter=self.adapter, title=source.title,
                                                 max_chars=self.max_chars, max_retries=self.max_retries)
        complete = bool(extraction and extraction.extraction_complete)
        coverage = [ChunkCoverage(
            chunk_id=f"structure:{c['start']}:{c['end']}", section=None, start=c["start"], end=c["end"],
            ok=c["ok"], extracted_by="llm", settings=self.adapter.settings(),
        ) for c in (extraction.coverage if extraction else [])] or [ChunkCoverage(
            chunk_id=f"structure:0:{len(text)}", section=None, start=0, end=len(text),
            ok=extraction is not None, extracted_by="llm",
            error=None if extraction is not None else "no_valid_reply", settings=self.adapter.settings(),
        )]
        return ExtractionResult(
            strategy=self.name, strategy_version=self.version, work_id=source.work_id,
            source_version=source.document.source_ref, parser_revision=source.document.parser_key,
            complete=complete, observations=tuple(to_reported_quantities(extraction)) if extraction else (),
            coverage=tuple(coverage),
            side_outputs={"structure": extraction.model_dump(mode="json") if extraction else None,
                          "attributed_phase_mentions": attributed_phase_mentions(text)},
            unmapped={"morphology": extraction.morphology if extraction else None,
                      "orientation": extraction.orientation if extraction else None,
                      "material_system": extraction.material_system if extraction else None,
                      "note": "morphology/orientation/peaks have no approved F2W slot; pending H3"},
            inference={**self.adapter.settings(), "prompt_version": EXTRACTION_PROMPT_VERSION},
        )


__all__ = [
    "EXTRACTION_PROMPT_VERSION", "STRATEGY_NAME", "STRATEGY_VERSION", "ExtractedValue", "ObservationContext",
    "PaperStructureExtraction", "PaperStructureStrategy", "ReportedPeak", "attributed_phase_mentions",
    "extract_structure_from_text", "is_attributed", "sentence_around", "to_reported_quantities",
]

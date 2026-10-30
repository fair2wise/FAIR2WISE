"""Orientation: source-supported statements recorded apart from inference candidates (V-08).

Two lists that never merge:

* ``source_statements``: orientation wording found in the paper's own text (captions and
  section prose), each with the sentence, its locator and the verbatim ``term`` stated
  ("face-on", "edge-on", "parallel", "perpendicular", "in-plane", "out-of-plane"). No term is
  translated: face-on/edge-on and parallel/perpendicular describe different things.
* ``inference_candidates``: model answers about a figure (optional, text-only, via an F2W chat
  adapter). They are ALWAYS ``reportable=False`` and never populate ``reported``.

``reported`` is set only from explicit, non-negated, non-conflicting source statements; a
negated ("no preferred orientation"), hedged or conflicting statement is surfaced as
uncertainty and withheld. A visual/model guess is never upgraded to an observation.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/figure_orientation.py
(``ORIENTATION_PROMPT``, ``parse_orientation_answer``, ``candidate_subjects`` selection by class)
(BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Sequence

from ..contracts.evidence import SourceLocator
from .classification import ChatAdapter
from .classification_units import verdict_classes

#: Figure classes worth asking a model about (donor ``_USABLE_CLASSES``).
USABLE_CLASSES = ("experimental_scattering", "simulated_scattering")

ORIENTATION_PROMPT = (
    "A 2-D grazing-incidence X-ray scattering pattern of a thin film is described below (text only).\n"
    "Where does the strongest NON-SPECULAR diffraction feature sit? Answer OUT_OF_PLANE if on the vertical "
    "(q_z) axis, IN_PLANE if on the horizontal (q_xy) axis, UNCLEAR otherwise. Do not count the Yoneda band "
    "or specular rod. Reply with exactly one word: OUT_OF_PLANE, IN_PLANE, or UNCLEAR."
)


def parse_orientation_answer(raw: str) -> str:
    """Map a model reply to ``"parallel"``, ``"perpendicular"`` or ``""`` (UNCLEAR/empty/both/unknown).

    OUT_OF_PLANE reflection means the layers lie PARALLEL to the substrate; IN_PLANE means perpendicular.
    The inversion happens here, once.
    """
    text = (raw or "").strip().upper()
    if not text or "UNCLEAR" in text:
        return ""
    has_out = "OUT_OF_PLANE" in text or "OUT-OF-PLANE" in text
    has_in = "IN_PLANE" in text or "IN-PLANE" in text
    if has_out and has_in:
        return ""
    return "parallel" if has_out else "perpendicular" if has_in else ""


_SUBJECT = re.compile(r"(?i)\b(lamell\w*|layers?|domains?|cylinders?|crystallites?|backbones?|stack\w*|"
                      r"pi-pi|π-π|chains?|polymer|molecules?|grains?)\b")
_TERM = re.compile(r"(?i)\b(face-on|edge-on|end-on|in-plane|out-of-plane|"
                   r"(?:oriented |aligned |lying |standing )?(parallel|perpendicular) to (?:the )?substrate)\b")
_NEGATION = re.compile(
    r"(?i)\b(no preferred|not preferentially|isotropic|randomly oriented|no (?:clear |obvious )?orientation|"
    r"not (?:face-on|edge-on|parallel|perpendicular)|may be|might be|could be|possibly|presumably|likely|"
    r"suggest(?:s|ed)? that)\b"
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _norm_term(raw: str) -> str:
    low = raw.lower()
    if "parallel" in low:
        return "parallel"
    if "perpendicular" in low:
        return "perpendicular"
    return low


@dataclass(frozen=True)
class OrientationStatement:
    """One orientation sentence found in the paper's text."""

    term: str
    sentence: str
    locator: SourceLocator
    origin: str                      # "caption" | "section"
    hedged: bool = False
    subject: str = ""

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form (``kind`` marks it as source-supported)."""
        return {"kind": "source_statement", "term": self.term, "subject": self.subject,
                "sentence": self.sentence[:400], "origin": self.origin, "hedged": self.hedged,
                "locator": self.locator.model_dump(exclude_none=True)}


def find_statements(text: str, locator: SourceLocator, origin: str) -> list[OrientationStatement]:
    """Orientation statements in ``text``; sentences needing a subject AND a term, hedging flagged."""
    out: list[OrientationStatement] = []
    for sentence in _SENTENCE.split(text or ""):
        subject, terms = _SUBJECT.search(sentence), {_norm_term(m.group(1)) for m in _TERM.finditer(sentence)}
        if not subject or not terms:
            continue
        hedged = bool(_NEGATION.search(sentence))
        for term in sorted(terms):
            out.append(OrientationStatement(term, sentence.strip(), locator, origin, hedged,
                                            subject.group(1).lower()))
    return out


@dataclass(frozen=True)
class OrientationFinding:
    """Orientation for one figure: separate source statements and inference candidates."""

    figure_id: str
    source_statements: Sequence[OrientationStatement] = ()
    inference_candidates: Sequence[dict[str, Any]] = ()
    reported: str | None = None
    withheld_reason: str = ""
    uncertainties: Sequence[str] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form; ``reported`` derives from source statements only."""
        return {
            "figure_id": self.figure_id, "reported": self.reported, "withheld_reason": self.withheld_reason,
            "source_statements": [s.as_dict() for s in self.source_statements],
            "inference_candidates": list(self.inference_candidates),
            "uncertainties": list(self.uncertainties),
        }


def read_orientation(
    figure_id: str,
    statements: Sequence[OrientationStatement],
    *,
    caption: str = "",
    verdict: dict[str, Any] | None = None,
    model: ChatAdapter | None = None,
    locator: SourceLocator | None = None,
) -> OrientationFinding:
    """Combine source statements with (optional) inference candidates, never upgrading the latter."""
    unc: list[str] = []
    firm = [s for s in statements if not s.hedged]
    terms = {s.term for s in firm}
    reported: str | None = None
    reason = ""
    if not statements:
        reason = "no_source_statement"
    elif not firm:
        reason, unc = "only_hedged_or_negated_statements", ["hedged_or_negated"]
    elif len(terms) > 1:
        reason, unc = "conflicting_source_statements", ["conflicting_terms:" + ",".join(sorted(terms))]
    else:
        reported = next(iter(terms))
    candidates: list[dict[str, Any]] = []
    classes = verdict_classes(verdict or {})
    if model is not None and (not classes or classes & set(USABLE_CLASSES)):
        raw = model.ask(f"{ORIENTATION_PROMPT}\nCaption: {caption[:800]}", label="vision.orientation")
        answer = parse_orientation_answer(raw)
        candidates.append({
            "kind": "inference_candidate", "orientation": answer or None, "basis": "model_text",
            "reportable": False, "needs_review": True,
            "locator": locator.model_dump(exclude_none=True) if locator else None,
        })
        if answer and reported in ("parallel", "perpendicular") and answer != reported:
            unc.append("inference_disagrees_with_source_statement")
    return OrientationFinding(figure_id, tuple(statements), tuple(candidates), reported, reason, tuple(unc))

"""Quarantine classification (C-09): set aside malformed or non-term surfaces with a reason code.

``classify_surface`` keeps the donor's ordered rule list (the order is the contract).
``check_context_row`` adds structural checks for malformed/unsupported input rows. A quarantined
term is always reported with its class; nothing is deleted or merged by quarantining.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/term_quarantine.py (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any

from .growth import definitions
from .vocabulary import CATEGORIES, Vocabulary

SOFTWARE_SEED = frozenset(
    {
        "imagej",
        "casaxps",
        "vasp",
        "gaussian",
        "igor",
        "igor pro",
        "fityk",
        "pyfai",
        "nika",
        "gixsgui",
        "origin",
        "matlab",
        "python",
        "fiji",
        "xpspeak",
        "sasview",
        "bornagain",
    }
)
PROPERTY_RE = re.compile(
    r"\b(curves?|lifetime|efficien\w*|pce|mobilit\w*|spectra|spectrum|peak|ratio|fwhm|"
    r"thickness|roughness|performance|crystallinity|morphology|band ?gap|voltage|current)\b",
    re.IGNORECASE,
)
PROCEDURE_RE = re.compile(
    r"\b(coating|casting|annealing|sonication|evaporation|deposition|treatment|synthesis|"
    r"encapsulation|doping|processing|drying|washing|cleaning)\b",
    re.IGNORECASE,
)
_ACRONYM_IN_BRACKETS = re.compile(r"\(([A-Z][A-Za-z0-9-]{1,11})\)")
_MAX_TERM_WORDS = 6
MAX_SURFACE_CHARS = 200
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _fold_all(values: Iterable[str]) -> set[str]:
    folded = {" ".join(str(v).split()).casefold() for v in values}
    folded.discard("")
    return folded


def classify_surface(
    surface: str, vocab: Vocabulary, *, software_extra: Iterable[str] = (), generic_stoplist: Iterable[str] = ()
) -> str | None:
    """The quarantine class of ``surface`` or ``None`` when it should go on to a judge. Never raises.

    Order: fragment, sentence, software, property_or_result, procedure, generic. A surface the
    vocabulary already matches is never quarantined.
    """
    text = " ".join(str(surface).split())
    if not text or vocab.match(text, None) is not None:
        return None
    if _ACRONYM_IN_BRACKETS.search(text) and not definitions(text):
        return "junk:fragment"
    if len(text.split()) > _MAX_TERM_WORDS or "," in text:
        return "junk:sentence"
    folded = text.casefold()
    if folded in SOFTWARE_SEED or folded in _fold_all(software_extra):
        return "software"
    if PROPERTY_RE.search(text):
        return "junk:property_or_result"
    if PROCEDURE_RE.search(text):
        return "procedure"
    if len(text.split()) == 1 and folded in _fold_all(generic_stoplist):
        return "junk:generic"
    return None


def check_context_row(row: object) -> dict[str, str] | None:
    """Structural quarantine for an input row: ``{"code", "detail"}`` or ``None`` when well formed."""
    if not isinstance(row, Mapping):
        return {"code": "malformed:not_a_mapping", "detail": type(row).__name__}
    surface = row.get("surface")
    if not isinstance(surface, str):
        return {"code": "malformed:surface_not_text", "detail": type(surface).__name__}
    if not surface.strip():
        return {"code": "malformed:empty_surface", "detail": ""}
    if len(surface) > MAX_SURFACE_CHARS:
        return {"code": "malformed:surface_too_long", "detail": str(len(surface))}
    if _CONTROL.search(surface):
        return {"code": "malformed:control_characters", "detail": ""}
    if not row.get("node_id"):
        return {"code": "malformed:missing_node_id", "detail": ""}
    category = row.get("category")
    if category is not None and category != "unknown" and category not in CATEGORIES:
        return {"code": "unsupported:category", "detail": str(category)}
    return None

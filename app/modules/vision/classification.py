"""Paper-figure classification from source text (V-02): caption/panel context, not experimental inference.

The taxonomy is the donor's core menu. Classification here is **text-only**: a
deterministic caption/axis-keyword pass, plus an optional model call routed through an
existing F2W client class (anything with ``chat(prompt, *, temperature, timeout, trace_label) -> str``,
for example ``term_extractor.clients.ChatClient``) via :class:`ChatAdapter`. This module adds no provider,
model name or default, and sends no image. Pixel-level (VLM) classification is deferred.

A verdict records its ``basis``; a model-only answer is an inference (``source_supported=False``) and is
flagged for review, and any disagreement between caption evidence and the model is withheld to review.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/figure_classification.py
(``caption_clauses``, ``panel_context``; taxonomy from src/tiled_agentic/figure_taxonomy.yaml core menu)
(BSD-3-Clause).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Protocol, Tuple

#: Core figure classes (donor taxonomy v1, general domain).
FIGURE_CLASSES: Tuple[str, ...] = (
    "experimental_scattering", "simulated_scattering", "simulated_optics", "1d_scattering_curve",
    "sem_image", "afm_image", "tem_image", "optical_microscopy", "spectrum", "device_curve",
    "thermal_trace", "other",
)

_PANEL_CLAUSE = re.compile(r"\(\s*([a-hA-H])\s*\)")


def caption_clauses(caption: str) -> Dict[str, str]:
    """A caption split into the clauses its ``(a)``, ``(b)`` markers introduce (lower-cased letter keys)."""
    marks = list(_PANEL_CLAUSE.finditer(caption or ""))
    out: Dict[str, str] = {}
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(caption)
        out[mark.group(1).lower()] = caption[mark.end():end].strip()
    return out


def panel_context(label: str, figure_caption: str, *, limit: int = 300) -> str:
    """What to tell a classifier about one panel: its own clause, else the whole caption labelled as such."""
    clause = caption_clauses(figure_caption).get((label or "").strip().lower())
    if clause:
        return f'This is panel ({label}) of a figure. Its own caption clause reads: "{clause[:limit]}"'
    if figure_caption.strip():
        return ("This is ONE PANEL of a multi-panel figure. The caption below is the WHOLE figure's and "
                f'describes several panels, so only part of it applies here: "{figure_caption[:limit]}"')
    return ""


# Ordered (class, pattern) pairs; first-match wins for the primary, all matches form candidates.
_RULES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = tuple(
    (cls, re.compile(pat, re.IGNORECASE)) for cls, pat in (
        ("simulated_scattering", r"simulat\w*[^.]{0,60}(?:scattering|gisaxs|giwaxs|saxs|waxs|diffraction)"),
        ("experimental_scattering", r"\b(?:2d|two-dimensional)\b[^.]{0,40}(?:gisaxs|giwaxs|saxs|waxs|rsoxs|"
                                    r"scattering|diffraction)|"
                                    r"(?:gisaxs|giwaxs|rsoxs)\b[^.]{0,30}(?:pattern|image|map)"),
        ("1d_scattering_curve", r"\b(?:i\s*\(\s*q\s*\)|azimuthal|circular(?:ly)? averag\w*|line\s?cut|"
                                r"intensity (?:vs\.?|versus) q|1d (?:profile|scattering))"),
        ("sem_image", r"\bsem\b|scanning electron"),
        ("afm_image", r"\bafm\b|atomic force"),
        ("tem_image", r"\btem\b|transmission electron|cryo-?em"),
        ("optical_microscopy", r"optical microscop|polari[sz]ed (?:optical )?microscop|\bpom\b"),
        ("spectrum", r"\b(?:nexafs|xas|xps|uv-?vis|absorption|raman|ftir|spectrum|spectra)\b"),
        ("device_curve", r"\b(?:j-?v|i-?v|current density|transfer curve|output curve|eqe)\b"),
        ("thermal_trace", r"\b(?:dsc|tga|thermogram|heat flow)\b"),
    )
)


class TextModel(Protocol):
    """The slice of an F2W chat client used here (``term_extractor.clients.ChatClient``)."""

    def chat(self, prompt: str, *, temperature: float = 0.0, timeout: int = 240,
             trace_label: Optional[str] = None) -> str:  # pragma: no cover - protocol
        """Return the model's text reply."""


class ChatAdapter:
    """Thin adapter over an existing F2W chat client; deterministic settings, no defaults added."""

    def __init__(self, client: TextModel, *, timeout: int = 120) -> None:
        self._client = client
        self._timeout = timeout

    def ask(self, prompt: str, *, label: str = "vision.classify") -> str:
        """One temperature-0 call; transport failures propagate to the caller."""
        return str(self._client.chat(prompt, temperature=0.0, timeout=self._timeout, trace_label=label))


def parse_class_answer(raw: str) -> str:
    """The single menu slug a reply names, or ``""`` when none or several are named."""
    text = (raw or "").lower()
    hits = {c for c in FIGURE_CLASSES if re.search(rf"(?<![a-z0-9_]){re.escape(c)}(?![a-z0-9_])", text)}
    if len(hits) > 1:
        hits.discard("other")
    return hits.pop() if len(hits) == 1 else ""


def _prompt(context: str) -> str:
    menu = ", ".join(FIGURE_CLASSES)
    return ("Classify a scientific paper figure from its text context only (no image is provided).\n"
            f"Classes: {menu}.\nContext: {context}\nReply with exactly one class name, or other if unsure.")


def caption_classes(text: str) -> List[str]:
    """Classes whose caption/axis keywords occur in ``text`` (source-supported evidence), in rule order."""
    return [cls for cls, pattern in _RULES if pattern.search(text or "")]


def classify_text_context(caption: str, label: str = "", *, extra_text: str = "",
                          model: Optional[ChatAdapter] = None) -> Dict[str, Any]:
    """Classify one figure or panel from its caption clause (and axis text), optionally cross-checked by a model.

    The verdict shape follows the donor's: ``class``, ``also``, ``confidence``, ``needs_review``,
    ``review_reasons``; plus ``basis`` and ``source_supported``. An empty ``class`` means unclassified.
    """
    context = panel_context(label, caption) if label else (caption or "")
    scope = caption_clauses(caption).get(label.lower()) if label else None
    evidence_text = f"{scope if scope else (caption or '')} {extra_text}".strip()
    found = caption_classes(evidence_text)
    reasons: List[str] = []
    if len(found) > 1:
        reasons.append("caption_names_several_classes")
    if label and not scope:
        reasons.append("panel_has_no_own_caption_clause")
    verdict: Dict[str, Any] = {
        "class": found[0] if found else "", "also": found[1:], "confidence": 0.0,
        "basis": "caption_keywords" if found else "none", "source_supported": bool(found), "model_used": False,
    }
    if found:
        verdict["confidence"] = 0.7 if len(found) == 1 and not (label and not scope) else 0.4
    if model is not None:
        answer = parse_class_answer(model.ask(_prompt(context[:1200] or "(no caption)")))
        verdict["model_used"] = True
        verdict["model_class"] = answer
        if not answer:
            reasons.append("model_answer_not_in_menu")
        elif not found:
            verdict.update({"class": answer, "basis": "model_text", "source_supported": False, "confidence": 0.3})
            reasons.append("model_inference_only")
        elif answer != found[0] and answer not in found:
            reasons.append("model_disagrees_with_caption")
    if not verdict["class"]:
        reasons.append("no_class_evidence")
    verdict["needs_review"] = bool(reasons)
    verdict["review_reasons"] = reasons
    return verdict

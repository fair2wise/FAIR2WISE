"""What the paper says about a figure, for a classifier that only sees pixels (L-16, T29 B5).

The image is what a model must describe; the paper context is what the figure is *for*. So
the abstract is truncated and the citing sentence is one sentence, and the rendered block says
which is which. The citing sentence is a ``text_grounded`` statement tied to its section.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/paper_context.py (``caption_figure_number``,
``citing_sentence``, ``render_paper_context``, ``figure_publications``,
``sections_by_publication``) (BSD-3-Clause). The graph reads become pure functions over rows
the caller already has; there is no store or network access.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
import re

from ..contracts.evidence import SourceLocator
from .statements import Basis, ProvenanceStatement

ABSTRACT_CHARS = 500
CITING_CHARS = 400
TOP_TECHNIQUES = 5
PRODUCER = "paper_context@v1"

#: Anchored by ``.match``: a caption that merely *mentions* another figure is not a caption for it.
_CAPTION_NUMBER = re.compile(r"\s*(?:fig(?:ure)?s?\.?)\s*(\d{1,3})", re.IGNORECASE)
#: A sentence boundary that does not cut "Fig. 3" in half.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


def caption_figure_number(caption: str) -> str:
    """The figure number a caption opens with (``"3"``), or ``""``. A string: a label, not arithmetic."""
    match = _CAPTION_NUMBER.match(caption or "")
    return match.group(1) if match else ""


def citing_sentence(number: str, sections: Sequence[str]) -> str:
    """The first sentence citing figure ``number`` (word-bounded, so Fig. 1 never matches Fig. 12)."""
    if not number:
        return ""
    pattern = re.compile(rf"\bfig(?:ure)?s?\.?\s*{re.escape(number)}\b", re.IGNORECASE)
    for text in sections:
        for sentence in _SENTENCE_SPLIT.split(text or ""):
            if pattern.search(sentence):
                return " ".join(sentence.split())[:CITING_CHARS]
    return ""


def render_paper_context(*, title: str = "", abstract: str = "", domain: str = "",
                         techniques: Sequence[str] | None = None, citing: str = "") -> str:
    """The labelled context block, or ``""`` when nothing is known (empty must be no context)."""
    lines: list[str] = []
    if title.strip():
        lines.append(f"Paper: {title.strip()}")
    if abstract.strip():
        lines.append(f"Abstract (truncated): {abstract.strip()[:ABSTRACT_CHARS]}")
    named = [t for t in (techniques or []) if t][:TOP_TECHNIQUES]
    if domain or named:
        parts = ([f"subject area {domain}"] if domain else []) + (
            ["techniques " + ", ".join(named)] if named else [])
        lines.append("Context: " + "; ".join(parts))
    if citing.strip():
        lines.append(f"The paper cites this figure saying: \"{citing.strip()}\"")
    if not lines:
        return ""
    lines.append("That is what the figure is FOR. Describe what is IN THE IMAGE; where "
                 "the image and the paper disagree, describe the image and say so.")
    return "\n".join(lines)


def figure_publications(edges: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """``{figure id: publication id}`` from ``schema:image`` edges (subject is the publication)."""
    return {str(e["objectId"]): str(e["subjectId"]) for e in edges if e.get("subjectId") and e.get("objectId")}


def sections_by_publication(pairs: Iterable[tuple[str, str]], section_text: Mapping[str, str]) -> dict[str, list[str]]:
    """``{publication id: [section text, ...]}`` in edge order; empty sections dropped."""
    out: dict[str, list[str]] = {}
    for publication_id, section_id in pairs:
        body = section_text.get(str(section_id), "")
        if body.strip():
            out.setdefault(str(publication_id), []).append(body)
    return out


def figure_context_statements(*, work_id: str, source_ref: str, captions: Mapping[str, str],
                              sections: Mapping[str, str]) -> list[ProvenanceStatement]:
    """A ``figure_cited_by`` statement per figure whose caption number is cited in a section.

    Args:
        work_id: The Work the figures belong to.
        source_ref: Content address of the parsed source.
        captions: ``{figure id: caption}``.
        sections: ``{section name: text}`` in document order.
    """
    out: list[ProvenanceStatement] = []
    for figure_id, caption in captions.items():
        number = caption_figure_number(caption)
        for name, text in sections.items():
            sentence = citing_sentence(number, [text])
            if not sentence:
                continue
            out.append(ProvenanceStatement(
                kind="figure_context", subject=work_id, predicate="figure_cited_by", object=figure_id,
                basis=Basis.TEXT_GROUNDED, producer=PRODUCER, source_ref=source_ref,
                locator=SourceLocator(section=name, figure=figure_id),
                attributes={"figure_number": number,
                            "sentence_sha256": hashlib.sha256(sentence.encode()).hexdigest(), "sentence": sentence}))
            break
    return out


__all__ = ("ABSTRACT_CHARS", "CITING_CHARS", "PRODUCER", "TOP_TECHNIQUES", "caption_figure_number",
           "citing_sentence", "figure_context_statements", "figure_publications", "render_paper_context",
           "sections_by_publication")

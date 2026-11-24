"""Matching a PDF the user supplies to a harvest candidate (design: ``design-manual-pdf-flow.md``, step 1).

Pure functions over what the library's own probe read from the PDF (``ProbeResult``: Info title, Info text, first pages'
text); no network, no model, nothing is executed. The verdict decides whether a supplied file is filed at once or the
user is asked first:

``strong``
    the candidate's DOI is in the PDF *and* it is prominent (Info text, or the first part of the first pages) or the
    titles agree; or, for a candidate without a DOI, the titles agree closely (similarity of at least 0.9).
``weak``
    only the titles are similar (0.6 to 0.9), the DOI is in the PDF but only buried in the text and the titles do not
    agree, or the PDF has no readable text (scanned, encrypted, unreadable).
``mismatch``
    the PDF names a DOI and it is not the candidate's. A preprint or repository copy of the same work legitimately
    carries a different DOI, so the verdict reports the title similarity too: the caller shows both DOIs and both titles.

A file whose DOI belongs to a *different open candidate of the same run* is reported by :func:`other_candidate` so the
caller can offer to file it there instead of calling it a mismatch.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import re
from typing import Any

#: A DOI as it appears in running text; trailing punctuation is stripped afterwards.
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"'<>\[\]{}|\\^`]+", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset({"a", "an", "and", "the", "of", "in", "on", "for", "with", "by", "to", "from", "is", "are", "at", "as"})
#: How much of the first pages counts as "prominent" for a DOI, and as the head a title is searched in.
PROMINENT_CHARS = 1500
HEAD_CHARS = 1200
STRONG_TITLE = 0.9
WEAK_TITLE = 0.6
#: A DOI that is not at the top of the text needs at least this much title agreement to count as strong.
DOI_WITH_TITLE = 0.5


@dataclass(frozen=True)
class PdfFacts:
    """What the probe read from the PDF, reduced to what matching needs."""

    dois: tuple[str, ...] = ()
    prominent_dois: tuple[str, ...] = ()
    title: str = ""
    head: str = ""
    readable: bool = False


@dataclass(frozen=True)
class Verdict:
    """The outcome of matching one PDF against one candidate."""

    kind: str                    # strong | weak | mismatch
    reason: str
    title_similarity: float = 0.0
    found_dois: tuple[str, ...] = field(default=())
    found_title: str = ""

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form: the verdict, the plain-words reason and what was found (never the PDF text)."""
        return {"kind": self.kind, "reason": self.reason, "title_similarity": round(self.title_similarity, 2),
                "found_dois": list(self.found_dois), "found_title": self.found_title}


def clean_doi(value: str) -> str:
    """A DOI in comparison form: lower case, no trailing punctuation, no resolver prefix."""
    text = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.rstrip(".,;:)]}>\"'")


def extract_dois(text: str) -> list[str]:
    """Every DOI in ``text``, cleaned, in order of first appearance, without duplicates."""
    seen: dict[str, None] = {}
    for match in _DOI.finditer(text or ""):
        doi = clean_doi(match.group(0))
        if doi and doi not in seen:
            seen[doi] = None
    return list(seen)


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP]


def title_similarity(candidate_title: str, facts: PdfFacts) -> float:
    """0..1: the best of the Jaccard similarity with the PDF's own title and how much of the candidate title is in the head."""
    wanted = _tokens(candidate_title)
    if not wanted:
        return 0.0
    best = 0.0
    own = _tokens(facts.title)
    if own:
        a, b = set(wanted), set(own)
        best = len(a & b) / len(a | b)
    head = set(_tokens(facts.head))
    if head:
        best = max(best, 0.95 * (sum(1 for t in wanted if t in head) / len(wanted)) ** 2)   # squared: partial is weak
    return best


def facts_from_probe(probe: Any) -> PdfFacts:  # noqa: ANN401 - a ProbeResult; any object with its attributes works
    """Reduce a ``ProbeResult`` to :class:`PdfFacts` (an unreadable or encrypted PDF is ``readable=False``)."""
    status = str(getattr(probe, "status", "error"))
    meta = str(getattr(probe, "meta_text", "") or "")
    page = str(getattr(probe, "page_text", "") or "")
    readable = status == "ok" and bool((page + meta).strip())
    everything = extract_dois(meta + "\n" + page)
    prominent = extract_dois(meta + "\n" + page[:PROMINENT_CHARS])
    return PdfFacts(tuple(everything), tuple(prominent), str(getattr(probe, "title", "") or ""), page[:HEAD_CHARS], readable)


def match_candidate(facts: PdfFacts, *, doi: str | None, title: str) -> Verdict:
    """The verdict for ``facts`` against a candidate with this DOI (may be empty) and title."""
    wanted = clean_doi(doi) if doi else ""
    similarity = title_similarity(title, facts)
    found = facts.dois
    shown = facts.title
    if not facts.readable:
        return Verdict("weak", "the PDF has no readable text (scanned, encrypted or unreadable), so it cannot be checked",
                       similarity, found, shown)
    if wanted and wanted in found:
        if wanted in facts.prominent_dois or similarity >= DOI_WITH_TITLE:
            return Verdict("strong", "the paper's DOI is in the PDF", similarity, found, shown)
        return Verdict("weak", "the DOI appears in the PDF but not at the top and the titles do not agree "
                       "(it may only be cited)", similarity, found, shown)
    if found and wanted:
        note = ("the titles agree closely, so this may be a preprint or repository copy with its own DOI"
                if similarity >= STRONG_TITLE else "the PDF names a different DOI")
        return Verdict("mismatch", note, similarity, found, shown)
    if similarity >= STRONG_TITLE and not wanted:
        return Verdict("strong", "the titles agree closely (the candidate has no DOI)", similarity, found, shown)
    if similarity >= WEAK_TITLE:
        return Verdict("weak", "the titles are similar but the DOI could not be confirmed", similarity, found, shown)
    return Verdict("mismatch" if found else "weak",
                   "the PDF names a different DOI" if found else "the title does not match and the PDF names no DOI",
                   similarity, found, shown)


def other_candidate(facts: PdfFacts, candidates: Iterable[Mapping[str, Any]], *, exclude: str = "") -> Mapping[str, Any] | None:
    """The one *other* candidate whose DOI the PDF carries prominently (so it probably belongs there), else ``None``.

    ``candidates`` are open candidates of the same run (dicts with ``id``, ``doi``, ``title``). Ambiguity (two
    candidates match) returns ``None``: nothing is guessed.
    """
    hits = []
    for candidate in candidates:
        if str(candidate.get("id")) == exclude or not candidate.get("doi"):
            continue
        doi = clean_doi(str(candidate["doi"]))
        if doi in facts.prominent_dois or (doi in facts.dois and title_similarity(str(candidate.get("title") or ""), facts)
                                           >= DOI_WITH_TITLE):
            hits.append(candidate)
    return hits[0] if len(hits) == 1 else None


def best_match(facts: PdfFacts, candidates: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], Verdict] | None:
    """The candidate this PDF belongs to among several (batch drop), or ``None`` when none is at least similar or two tie.

    An unreadable PDF (no text to compare) is never matched among several candidates.

    Strong beats weak; among equals the higher title similarity wins; an exact tie returns ``None`` (the caller lists the
    file as unmatched rather than guessing).
    """
    scored = []
    for candidate in candidates:
        verdict = match_candidate(facts, doi=str(candidate.get("doi") or ""), title=str(candidate.get("title") or ""))
        if verdict.kind == "strong" or (verdict.kind == "weak" and verdict.title_similarity >= WEAK_TITLE):
            scored.append((0 if verdict.kind == "strong" else 1, -verdict.title_similarity, str(candidate.get("id")), candidate,
                           verdict))
    if not scored:
        return None
    scored.sort(key=lambda row: (row[0], row[1]))
    if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
        return None
    return scored[0][3], scored[0][4]


__all__ = ("PdfFacts", "Verdict", "best_match", "clean_doi", "extract_dois", "facts_from_probe", "match_candidate",
           "other_candidate", "title_similarity")

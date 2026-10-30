"""Find where a quoted sentence sits in the stored text of one source section (E-03).

Offsets index the section text *as stored* (``ParsedDocument.to_record`` truncates
section text at ``SECTION_TEXT_MAX_CHARS``), so a span always dereferences to the
string a reader can fetch back. A miss is reported as ``not_found`` with no
offsets; a span is never invented.

``exact`` means byte-for-byte. ``normalized`` means the quote was found after the
fold recorded in :data:`NORMALIZATION_PROFILE` (NFKC, a closed set of glyph folds
and whitespace runs). It is deliberately a different profile name from the
disclosure layer's ``ws-hyphen-v1``: the disclosure projection only renders a
normalized quote as verbatim under its own profile, so this wider fold can never
be shown as a verbatim quotation by accident.

Deterministic location proves the quote exists in the source. It does not prove
that any scientific interpretation of it is entailed (plan T06).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/quote_location.py (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from ..contracts.evidence import MatchType

#: Closed set of match kinds, so a tally over them stays meaningful.
MATCH_KINDS: tuple[str, ...] = ("exact", "normalized", "not_found")

#: Profile recorded on ``EvidenceRef.normalization_profile`` for a normalized hit.
NORMALIZATION_PROFILE = "forge-nfkc-glyph-ws-v1"

#: Glyph pairs that survive a PDF parse looking identical and comparing unequal.
_GLYPH_FOLDS: tuple[tuple[str, str], ...] = (
    ("Å", "Å"),
    ("µ", "μ"),
    ("‘", "'"), ("’", "'"),
    ("“", '"'), ("”", '"'),
    ("‐", "-"), ("‑", "-"), ("‒", "-"),
    ("–", "-"), ("—", "-"), ("−", "-"),
    (" ", " "),
)

_WHITESPACE_RUN = re.compile(r"\s+")


@dataclass(frozen=True)
class QuoteLocation:
    """Where a quote sits in a section text, and how it matched.

    Attributes:
        match_kind: One of :data:`MATCH_KINDS`.
        char_start: Start offset into the stored section text, or ``None`` on a miss.
        char_end: End offset (exclusive), or ``None`` on a miss.
    """

    match_kind: str
    char_start: int | None = None
    char_end: int | None = None

    @property
    def is_verbatim(self) -> bool:
        """True only for an exact, byte-for-byte hit."""
        return self.match_kind == "exact"

    @property
    def found(self) -> bool:
        """True for an exact or normalized hit."""
        return self.match_kind != "not_found"

    def match_type(self) -> MatchType:
        """The shared-contract :class:`MatchType` for this location."""
        return {"exact": MatchType.EXACT, "normalized": MatchType.NORMALIZED}.get(self.match_kind, MatchType.NONE)

    def span(self) -> tuple[int, int] | None:
        """``(start, end)`` when located, else ``None``."""
        if self.char_start is None or self.char_end is None:
            return None
        return (self.char_start, self.char_end)


def _fold_char(ch: str) -> str:
    """One character in comparison form: NFKC, then the explicit glyph folds."""
    folded = unicodedata.normalize("NFKC", ch)
    for source, target in _GLYPH_FOLDS:
        folded = folded.replace(source, target)
    return folded


def _fold_with_map(text: str) -> tuple[str, list[int]]:
    """Fold ``text`` for comparison and keep an index back into the original string."""
    folded_chars: list[str] = []
    source_index: list[int] = []
    previous_was_space = False
    for index, ch in enumerate(text):
        if ch.isspace():
            if previous_was_space:
                continue
            previous_was_space = True
            folded_chars.append(" ")
            source_index.append(index)
            continue
        previous_was_space = False
        for folded in _fold_char(ch):
            folded_chars.append(folded)
            source_index.append(index)
    source_index.append(len(text))
    return "".join(folded_chars), source_index


def locate_quote(quote: str, section_text: str) -> QuoteLocation:
    """Locate ``quote`` in ``section_text``.

    Args:
        quote: The sentence the extractor reported, as given.
        section_text: The section text as stored (already truncated).

    Returns:
        ``exact`` with offsets, ``normalized`` with offsets mapped back into the
        original string, or ``not_found`` without offsets.
    """
    if not quote or not section_text:
        return QuoteLocation(match_kind="not_found")
    exact = section_text.find(quote)
    if exact != -1:
        return QuoteLocation("exact", exact, exact + len(quote))
    folded_text, index_map = _fold_with_map(section_text)
    folded_quote = _fold_with_map(quote)[0].strip()
    if not folded_quote:
        return QuoteLocation(match_kind="not_found")
    hit = folded_text.find(folded_quote)
    if hit == -1:
        return QuoteLocation(match_kind="not_found")
    start = index_map[hit]
    end = index_map[min(hit + len(folded_quote), len(index_map) - 1)]
    return QuoteLocation("normalized", start, end)


def count_occurrences(quote: str, section_text: str) -> int:
    """How many non-overlapping folded occurrences of ``quote`` the text holds (ambiguity check)."""
    if not quote or not section_text:
        return 0
    folded_text = _fold_with_map(section_text)[0]
    folded_quote = _fold_with_map(quote)[0].strip()
    return folded_text.count(folded_quote) if folded_quote else 0


__all__ = ["MATCH_KINDS", "NORMALIZATION_PROFILE", "QuoteLocation", "count_occurrences", "locate_quote"]

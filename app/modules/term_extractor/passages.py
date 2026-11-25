"""Locate a term in the pages of a paper and cut a supporting passage around it (design: supporting passages, step 1).

Pure and deterministic, no model calls. One tolerant locator serves extraction time (to verify or replace the model's
sentence) and the recovery job, so both behave the same:

* :func:`page_texts` is the shared page-text function: the same reader call the orchestrator uses
  (``open_pdf`` then ``load_page(i).get_text()``), so a passage located later matches what was extracted.
* :func:`squash` and :func:`locate` find a term whatever the page does to it (hyphenation, line breaks, ligatures,
  Greek letters, sub/superscripts, ``R-SoXS`` for ``RSoXS``).
* :func:`sentence_spans` and :class:`PageIndex` cut a page into sentences once and attach term-independent verdicts
  (references, header, citation, equation, caption ...), cached per page text.
* Affiliation and header blocks are recognised by SHAPE, never by a bare institution word: e-mail, URL, DOI, journal and
  licence lines, author markers (``Name1,2``), long comma lists, and a verbless fragment of institution names or a
  postal address ("Department of Chemistry, University of Bern, Freiestrasse 3, CH-3012 Bern, Switzerland."). A complete
  sentence with a verb is kept wherever it is, also when it names a national laboratory or facility ("...at beamline
  11.0.1.2 of the Advanced Light Source, Lawrence Berkeley National Laboratory"): such Methods sentences are prime evidence
  for beamlines, instruments and techniques (owner decision 2026-11-24, reversing the earlier ``National`` rule).
* :func:`candidates` returns every located mention as a :class:`Passage`, accepted or rejected with a reason;
  :func:`best_passages` returns the ranked accepted ones, best first.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
import logging
from pathlib import Path
import re
from typing import Any
import unicodedata

from ..literature.pdf_reader import PdfDocument, open_pdf
from .models import ContextSnippet

logger = logging.getLogger(__name__)

#: Passage length bounds (characters): below ``MIN_CHARS`` extend with the next sentence, above ``MAX_CHARS`` cut a
#: clause window; ``PREFERRED_MAX`` ends the preferred 120-300 band used by the ranking.
MIN_CHARS = 120
MAX_CHARS = 400
PREFERRED_MAX = 300

#: Rejection reasons (``Passage.reason``).
REASON_REFERENCES = "references"
REASON_HEADER = "header_boilerplate"
REASON_CITATION = "citation_entry"
REASON_FRAGMENT = "equation_fragment"
REASON_MID_SENTENCE = "starts_mid_sentence"
REASON_NO_END = "no_final_punctuation"
REASON_CAPTION = "figure_caption"
REASON_PAGE_NUMBER = "page_number"
REASON_SHORT = "too_short"

_GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta", "η": "eta",
    "θ": "theta", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho",
    "σ": "sigma", "ς": "sigma", "τ": "tau", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
}  # fmt: skip


# ---------------------------------------------------------------------------------------------------------------------
# Page text
# ---------------------------------------------------------------------------------------------------------------------


def page_texts_from_doc(doc: PdfDocument) -> list[str]:
    """Return the text of every page of an open document, exactly as the extractor reads it.

    Args:
        doc: An open :class:`~app.modules.literature.pdf_reader.PdfDocument`.

    Returns:
        One string per page, ``doc.load_page(i).get_text()`` with no further cleaning.
    """
    return [doc.load_page(i).get_text() for i in range(doc.page_count)]


def page_texts(source: str | Path | PdfDocument) -> list[str]:
    """Return the page texts of a PDF path or of an already-open document.

    Args:
        source: A filesystem path, or an open ``PdfDocument`` (left open).

    Returns:
        One string per page, the same text the orchestrator hands the extractor.
    """
    if isinstance(source, PdfDocument):
        return page_texts_from_doc(source)
    doc = open_pdf(source)
    try:
        return page_texts_from_doc(doc)
    finally:
        doc.close()


# ---------------------------------------------------------------------------------------------------------------------
# Squash and locate
# ---------------------------------------------------------------------------------------------------------------------


def _fold(char: str) -> str:
    """Fold one character: NFKC (ligatures, sub/superscripts, fullwidth), lower case, Greek letters to names."""
    out = unicodedata.normalize("NFKC", char).lower()
    return "".join(_GREEK.get(c, c) for c in out)


def squash(text: str) -> tuple[str, list[int]]:
    """Reduce ``text`` to letters and digits, with a map back to the original offsets.

    Greek letters become their names, NFKC folds ligatures and sub/superscripts, everything is lower-cased, and all
    other characters (whitespace, line breaks, hyphens, dashes, underscores, soft hyphens, brackets) are dropped.

    Args:
        text: Any text, typically a page.

    Returns:
        ``(key, index_map)`` where ``index_map[i]`` is the offset in ``text`` of the character that produced
        ``key[i]``.
    """
    key: list[str] = []
    index: list[int] = []
    for i, char in enumerate(text):
        if char.isascii():
            if char.isalnum():
                key.append(char.lower())
                index.append(i)
            continue
        for folded in _fold(char):
            if folded.isalnum():
                key.append(folded)
                index.append(i)
    return "".join(key), index


@dataclass(frozen=True)
class Span:
    """One located mention.

    Attributes:
        start: Offset of the mention in the page text.
        end: Offset just after the mention (after a plural tail when there is one).
        sentence_start: Offset where the sentence holding the mention starts.
        sentence_end: Offset just after that sentence.
        form: ``"long"`` (the term or its long form), ``"abbr"`` (the abbreviation of ``long form (ABBR)``) or
            ``"token"`` (a short term matched as a whole case-sensitive token).
    """

    start: int
    end: int
    sentence_start: int
    sentence_end: int
    form: str


_TERM_FORMS = re.compile(r"^(.*\S)\s*\(([A-Za-z][A-Za-z0-9\-‐-―+/]{1,14})\)\s*$")
_PLURAL_TAIL = re.compile(r"(?:es|s|'s|’s)(?![A-Za-z0-9])")


def term_forms(term: str) -> tuple[str, str | None]:
    """Split ``long form (ABBR)`` into ``(long form, ABBR)``; any other term gives ``(term, None)``."""
    stripped = term.strip()
    match = _TERM_FORMS.match(stripped)
    if match:
        return match.group(1).strip(), match.group(2)
    return stripped, None


def _whole_token(text: str, token: str, *, plural: bool = False) -> list[tuple[int, int]]:
    """Case-sensitive whole-token matches of ``token`` in ``text`` (optionally with a plural tail)."""
    tail = r"(?:es|s|'s|’s)?" if plural else ""
    pattern = r"(?<![A-Za-z0-9])" + re.escape(token) + tail + r"(?![A-Za-z0-9])"
    return [(m.start(), m.end()) for m in re.finditer(pattern, text)]


def _find_squashed(text: str, key: str, index: list[int], query: str) -> list[tuple[int, int]]:
    """Squashed substring hits of ``query`` whose original neighbours are not alphanumeric."""
    hits: list[tuple[int, int]] = []
    if len(query) < 3:
        return hits
    pos = 0
    while True:
        k = key.find(query, pos)
        if k < 0:
            break
        pos = k + 1
        start = index[k]
        end = index[k + len(query) - 1] + 1
        before = text[start - 1] if start > 0 else " "
        tail = _PLURAL_TAIL.match(text[end : end + 3])
        stop = end + tail.end() if tail else end
        after = text[stop] if stop < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            hits.append((start, stop))
    return hits


def locate(
    page_text: str,
    term: str,
    *,
    abbr_defined: bool | None = None,
    max_spans: int = 50,
) -> list[Span]:
    """Find every mention of ``term`` on one page.

    Rules: squashed substring find; the original characters around the hit must not be alphanumeric (an optional
    plural tail ``s``/``es``/``'s`` is allowed); a term of fewer than 3 alphanumerics needs a case-sensitive
    whole-token match; for ``long form (ABBR)`` the long form is matched and the abbreviation counts only where the
    paper defines it.

    Args:
        page_text: The page as the extractor saw it.
        term: The term, e.g. ``"RSoXS"`` or ``"grazing-incidence wide-angle X-ray scattering (GIWAXS)"``.
        abbr_defined: ``None`` to accept the abbreviation only when this page shows ``(ABBR)`` next to a long-form
            hit; ``True`` or ``False`` to say so from paper-level knowledge.
        max_spans: Maximum number of mentions returned (document order).

    Returns:
        Spans in document order, each with the sentence that holds it.
    """
    long_form, abbr = term_forms(term)
    index = page_index(page_text)
    query = squash(long_form)[0]
    hits: list[tuple[int, int, str]] = []
    long_hits: list[tuple[int, int]] = []
    if len(query) < 3:
        if long_form:
            hits += [(s, e, "token") for s, e in _whole_token(page_text, long_form)]
    else:
        long_hits = _find_squashed(page_text, index.key, index.index, query)
        hits += [(s, e, "long") for s, e in long_hits]
    if abbr:
        if abbr_defined is None:
            defined = bool(re.search(r"\(\s*" + re.escape(abbr) + r"\s*\)", page_text)) and bool(long_hits)
        else:
            defined = abbr_defined
        if defined:
            hits += [(s, e, "abbr") for s, e in _whole_token(page_text, abbr, plural=True)]
    spans: list[Span] = []
    last_end = -1
    for start, end, form in sorted(hits):
        if start < last_end:
            continue
        sentence = index.sentence_at(start)
        if sentence is None:
            continue
        spans.append(Span(start, end, sentence.start, sentence.end, form))
        last_end = end
        if len(spans) >= max_spans:
            break
    return spans


# ---------------------------------------------------------------------------------------------------------------------
# Sentences
# ---------------------------------------------------------------------------------------------------------------------

_BOUNDARY = re.compile(r"[.!?][\"')\]”’]*(?=\s+[\"“(\[]?[A-Z0-9])")
_ABBREVIATIONS = frozenset({
    "fig", "figs", "eq", "eqs", "ref", "refs", "vs", "e.g", "i.e", "approx", "ca", "cf", "dr", "prof",
    "sec", "tab", "resp", "al",
})  # fmt: skip
_TOKEN_BEFORE = re.compile(r"(?:^|[\s(\[])([A-Za-z.]+)$")
_EMAIL_URL = re.compile(r"@|\bE-?mail\b|https?://|doi\.org|\bdoi:|\bwww\.", re.I)
_BOILERPLATE = re.compile(
    r"(©|\bcopyright\b|all rights reserved|downloaded from|\breceived:|\baccepted:|published online|creative commons"
    r"|licen[sc]e|\bcite this\b|peer review information|reprints and permission|publisher.s note"
    r"|correspondence and requests|open access|\bpreprint\b|\bsubmitted to\b)",
    re.I,
)
_AUTHOR_MARKS = re.compile(r"[A-Za-z’']\s?\d{1,2}(?:,\d{1,2})*\*?\s?[,&]")
_JOURNAL_LINE = re.compile(
    r"\|.*\(\d{4}\)|^\W*(?:ARTICLE\s+)?(?:\d+\s+)?[A-Z][A-Z ]{6,}\s*\|"
    r"|^\W*[A-Z][\w. ]{2,30}\s(?:19|20)\d\d,\s*\d+,\s*[\w-]+\s*$"  # "Adv. Mater. 2019, 31, 1808279"
)
_RUNNING_FOOTER = re.compile(r"^\s*\S*\d+\s*\(\d+\s+of\s+\d+\)\s*$", re.I)
_PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$", re.I)


def _is_break_line(line: str) -> bool:
    """A line that is never part of a running sentence: page number, URL/DOI/e-mail, boilerplate, author marks."""
    stripped = line.strip()
    if not stripped:
        return True
    if _PAGE_NUMBER.match(stripped) or _RUNNING_FOOTER.match(stripped):
        return True
    if _EMAIL_URL.search(stripped) or _BOILERPLATE.search(stripped) or _JOURNAL_LINE.search(stripped):
        return True
    return len(_AUTHOR_MARKS.findall(stripped)) >= 2


def _ends_with_abbreviation(flat: str, run_start: int, period: int) -> bool:
    """Whether the ``.`` at ``period`` closes an abbreviation or a single initial rather than a sentence."""
    prefix = flat[max(run_start, period - 12) : period]
    if prefix.lower().endswith("et al"):
        return True
    match = _TOKEN_BEFORE.search(prefix)
    if not match:
        return False
    token = match.group(1)
    return token.lower() in _ABBREVIATIONS or bool(re.fullmatch(r"[A-Z]", token))


def _trim(flat: str, start: int, end: int) -> tuple[int, int] | None:
    """Shrink ``[start, end)`` to its non-blank content; ``None`` when nothing is left."""
    while start < end and flat[start].isspace():
        start += 1
    while end > start and flat[end - 1].isspace():
        end -= 1
    return (start, end) if end > start else None


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Cut a page into sentences, as offsets into ``text``.

    A boundary is ``[.!?]`` followed by space and a capital, bracket or digit, except after ``et al.``, ``Fig.``,
    ``Eq.``, ``e.g.``, ``i.e.`` and single initials. A line break inside a sentence is a space. Page-number,
    URL/DOI, boilerplate and author lines are cut out as segments of their own so they never glue to the body text.

    Args:
        text: Page text.

    Returns:
        ``(start, end)`` pairs in document order, never overlapping, whitespace trimmed.
    """
    flat = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    spans: list[tuple[int, int]] = []
    run_start: int | None = None
    pos = 0
    lines = text.split("\n")

    def flush(run_end: int) -> None:
        nonlocal run_start
        if run_start is None:
            return
        cut = run_start
        for match in _BOUNDARY.finditer(flat, run_start, run_end):
            if flat[match.start()] == "." and _ends_with_abbreviation(flat, run_start, match.start()):
                continue
            seg = _trim(flat, cut, match.end())
            if seg:
                spans.append(seg)
            cut = match.end()
        seg = _trim(flat, cut, run_end)
        if seg:
            spans.append(seg)
        run_start = None

    for line in lines:
        line_start, line_end = pos, pos + len(line)
        pos = line_end + 1
        if _is_break_line(line):
            flush(line_start)
            seg = _trim(flat, line_start, line_end)
            if seg:
                spans.append(seg)
        elif run_start is None:
            run_start = line_start
    flush(len(flat))
    return spans


def display_text(text: str, start: int, end: int) -> tuple[str, list[int]]:
    r"""Render ``text[start:end]`` for display and keep a map back to the page offsets.

    ``x-\ny`` is dehyphenated to ``xy``, soft hyphens are dropped, and whitespace runs become one space.

    Args:
        text: Page text.
        start: Start offset (inclusive).
        end: End offset (exclusive).

    Returns:
        ``(display, offsets)`` where ``offsets[i]`` is the page offset of ``display[i]``.
    """
    out: list[str] = []
    offsets: list[int] = []
    i = start
    while i < end:
        char = text[i]
        if char == "\xad":
            i += 1
        elif (
            char == "-" and i + 1 < end and text[i + 1] == "\n" and out and out[-1].isalnum()
            and i + 2 < end and text[i + 2].isalnum()
        ):  # fmt: skip
            i += 2
        elif char.isspace():
            if out and out[-1] != " ":
                out.append(" ")
                offsets.append(i)
            i += 1
        else:
            out.append(char)
            offsets.append(i)
            i += 1
    while out and out[-1] == " ":
        out.pop()
        offsets.pop()
    return "".join(out), offsets


# ---------------------------------------------------------------------------------------------------------------------
# Term-independent sentence verdicts
# ---------------------------------------------------------------------------------------------------------------------

#: Institution words. They count only in a fragment with no verb (see :func:`_affiliation_fragment`), never in a sentence.
_INSTITUTION = re.compile(
    r"\b(?:Universit\w*|Department|Dept\.|Institut\w*|Laborator(?:y|ies)|School of|College|Centre|Center for"
    r"|Division of|Faculty|Academy of|National|Corporation|Inc\.|Ltd\.?|Foundation|Ministry)\b"
)
#: Postal-address shapes: ``CH-3012`` or a five-digit code, a street line, or a country name that ends the fragment.
_POSTAL = re.compile(
    r"\b[A-Z]{1,2}-\d{4,5}\b|\b\d{5}(?:-\d{4})?\b"
    r"|\b\d+\s+[A-Z][a-z]+\s+(?:Street|St\.|Road|Rd\.|Avenue|Ave\.|Strasse|Stra\u00dfe|Platz|Drive)\b"
    r"|\b(?:USA|U\.S\.A\.|Switzerland|Germany|China|Japan|France|UK|United Kingdom|Canada|Australia|Korea|Netherlands"
    r"|Sweden|Italy|Spain|Denmark|Norway|Finland|Austria|Belgium|India|Singapore|Taiwan)\b[.,]?\s*$"
)
#: A finite verb or a past participle in lower case (``Advanced Light Source`` has none; ``were performed`` has two).
_VERB = re.compile(
    r"\b(?:is|are|was|were|be|been|being|has|have|had|do|does|did|can|could|will|would|may|might|must|shall|should"
    r"|show(?:s|n)?|use[sd]?|measure[sd]?|perform(?:s)?|operate[sd]?|reveal(?:s)?|demonstrate[sd]?|report(?:s)?|find|found"
    r"|observe[sd]?|indicate[sd]?|provide[sd]?|allow(?:s)?|give[sn]?|obtain(?:s)?)\b|\b[a-z]{3,}ed\b"
)
_CITATION = re.compile(
    r"\b(?:19|20)\d\d\b[^.]{0,60}?\b\d{1,4}\s*[,:]\s*\d{1,6}\s*[–\-]\s*\d{1,6}"
    r"|\bet al\.?,?\s*(?:Nat|J\.|Phys|Adv|Science|Chem|ACS|Proc|Rev|Appl|Macromol)"
    r"|\bpp\.\s*\d+\s*[–\-]\s*\d+"
)
_AUTHOR_LIST = re.compile(r"(?:[A-Z]\.\s?){1,3}[A-Z][a-zÀ-ſ]+,|[A-Z][a-zÀ-ſ]+,\s(?:[A-Z]\.\s?){1,3}")
_CAPTION_HEAD = re.compile(r"^(?:Supplementary\s+|Extended Data\s+)?(?:Fig(?:ure)?s?\.?|Table)\s*S?\d", re.I)
_PANEL_HEAD = re.compile(r"^(?:\(?[a-h]\)?\s*[,–\-]\s*)*\(?[a-h]\)?\s+[A-Z]")
_CAPTION_CONT = re.compile(
    r"^(?:The\s+)?(?:solid|dashed|dotted|dash-dotted)\b"
    r"|^(?:Error bars?|Scale bars?|Insets?|Source data|Data are|Shaded|Symbols?|Lines? (?:are|show))\b"
    r"|\(\s*[a-h](?:\s*[,–\-]\s*[a-h])?\s*\)"
)
_MATH = re.compile(r"[=−+×∑∫α-ωΔ√∞≈≤≥<>^]")
_PANEL_LABELS = re.compile(r"\ba\)\s+\S.*\bb\)\s+\S")
_CAPTION_RUN = 4


def _affiliation_fragment(text: str) -> bool:
    """A verbless run of institution names or a postal address: an affiliation block, not a sentence about a facility."""
    return not _VERB.search(text) and bool(_INSTITUTION.search(text) or _POSTAL.search(text))


def _header_like(text: str) -> bool:
    """Header, title, affiliation or boilerplate, recognised by shape.

    E-mail/URL/DOI, journal and licence lines, an affiliation fragment, long comma lists and author markers. A complete
    sentence with a verb is never rejected for the words it names.
    """
    if _EMAIL_URL.search(text) or _BOILERPLATE.search(text) or _JOURNAL_LINE.search(text):
        return True
    if _affiliation_fragment(text):
        return True
    commas = text.count(",")
    if commas >= 6 and commas / max(1, len(text.split())) > 0.15:
        return True
    return len(_AUTHOR_MARKS.findall(text)) >= 2


def _citation_like(text: str) -> bool:
    """Reference-list style entry: year + volume + page range, or author-initial lists."""
    return bool(_CITATION.search(text)) or len(_AUTHOR_LIST.findall(text)) >= 2


def _fragment_like(text: str) -> bool:
    """Equation or fragment: fewer than 5 alphabetic words, under 55% letters, or more than 6 math symbols."""
    letters = sum(c.isalpha() for c in text)
    words = re.findall(r"[A-Za-z]{3,}", text)
    return len(words) < 5 or letters / max(1, len(text)) < 0.55 or len(_MATH.findall(text)) > 6


def sentence_verdict(text: str) -> str | None:
    """Term-independent verdict on one sentence (display text); ``None`` when it may serve as a passage.

    Figure captions are decided in :class:`PageIndex` (they need the neighbouring sentences); the references heading
    is decided over the whole paper in :func:`references_start`.

    Args:
        text: The sentence as displayed (dehyphenated, single spaces).

    Returns:
        A ``REASON_*`` string, or ``None``.
    """
    if _PAGE_NUMBER.match(text):
        return REASON_PAGE_NUMBER
    if _RUNNING_FOOTER.match(text):
        return REASON_HEADER
    if _header_like(text):
        return REASON_HEADER
    if _citation_like(text):
        return REASON_CITATION
    if _fragment_like(text):
        return REASON_FRAGMENT
    if not re.match(r"[A-Z(\[0-9]", text):
        return REASON_MID_SENTENCE
    if not re.search(r"[.!?][\"')\]”’]*$", text):
        return REASON_NO_END
    return None


@dataclass(frozen=True)
class Sentence:
    """One sentence of a page with its display text and term-independent verdict.

    Attributes:
        start: Page offset of the first character.
        end: Page offset just after the last character.
        text: Display text (dehyphenated, single spaces).
        offsets: ``offsets[i]`` is the page offset of ``text[i]``.
        reason: Rejection reason, or ``None`` when the sentence passes every term-independent rule.
    """

    start: int
    end: int
    text: str
    offsets: tuple[int, ...]
    reason: str | None


@dataclass(frozen=True)
class PageIndex:
    """Everything about one page that does not depend on the term, built once and reused.

    Attributes:
        text: The page text.
        key: Squashed text (see :func:`squash`).
        index: Squashed index to page offset.
        sentences: Sentences in document order with verdicts.
    """

    text: str
    key: str
    index: list[int]
    sentences: tuple[Sentence, ...]

    def sentence_at(self, offset: int) -> Sentence | None:
        """Return the sentence containing page ``offset`` (``None`` between sentences)."""
        starts = [s.start for s in self.sentences]
        i = bisect_right(starts, offset) - 1
        if i >= 0 and offset < self.sentences[i].end:
            return self.sentences[i]
        return None


def _build_index(text: str) -> PageIndex:
    """Squash the page and cut and judge its sentences."""
    key, index = squash(text)
    sentences: list[Sentence] = []
    caption_left = 0
    for start, end in sentence_spans(text):
        shown, offsets = display_text(text, start, end)
        reason = sentence_verdict(shown)
        if _CAPTION_HEAD.match(shown) or _PANEL_HEAD.match(shown) or _PANEL_LABELS.search(shown):
            reason, caption_left = REASON_CAPTION, _CAPTION_RUN
        elif caption_left and _CAPTION_CONT.search(shown):
            reason, caption_left = REASON_CAPTION, caption_left - 1
        else:
            caption_left = 0
        sentences.append(Sentence(start, end, shown, tuple(offsets), reason))
    return PageIndex(text, key, index, tuple(sentences))


@lru_cache(maxsize=512)
def page_index(page_text: str) -> PageIndex:
    """Return the cached :class:`PageIndex` of a page text (verdicts are computed once per page text)."""
    return _build_index(page_text)


_REFERENCES_HEADING = re.compile(
    r"^\s*(?:\d+\.?\s*)?(?:references?(?:\s+list)?|bibliography|literature cited)\s*:?\s*$", re.I | re.M
)


def references_start(pages: list[str]) -> tuple[int, int]:
    """Find the ``References`` heading: everything from there on is reference-list material.

    Args:
        pages: Page texts of one paper.

    Returns:
        ``(page_index, offset)`` of the heading (0-based page), or ``(len(pages), 0)`` when there is none. The
        heading is looked for from 40% of the pages on, so a mention in the front matter is not taken for it.
    """
    for i in range(int(len(pages) * 0.4), len(pages)):
        match = _REFERENCES_HEADING.search(pages[i])
        if match:
            return i, match.start()
    return len(pages), 0


# ---------------------------------------------------------------------------------------------------------------------
# Passages
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Passage:
    """A candidate supporting passage for a term.

    Attributes:
        text: Display text of the passage.
        page: 1-based page number.
        start: Page offset where the passage starts (the sentence start, or the window start).
        end: Page offset just after it.
        form: How the term matched (``"long"``, ``"abbr"`` or ``"token"``).
        kind: Always ``"located"`` here (found by the locator, not quoted by a model).
        clause_window: ``True`` when the sentence was over 400 characters and ``text`` is a clause window.
        reason: ``None`` for an accepted passage, otherwise the rejection reason.
        mention: Page offset of the mention.
        sentence_start: Page offset where the mention's sentence starts.
    """

    text: str
    page: int
    start: int
    end: int
    form: str
    kind: str = "located"
    clause_window: bool = False
    reason: str | None = None
    mention: int = 0
    sentence_start: int = 0

    @property
    def accepted(self) -> bool:
        """Whether the passage passed every rule."""
        return self.reason is None

    @property
    def length(self) -> int:
        """Length of ``text`` in characters."""
        return len(self.text)


_CUTS = (
    (re.compile(r"[,;:](?=\s)"), "end"),
    (re.compile(r"\)"), "end"),
    (re.compile(r"\("), "start"),
    (re.compile(r"\s(?:and|which|that)\s"), "start"),
    (re.compile(r"\s[–—-]\s"), "start"),
)


def _clause_cuts(shown: str) -> list[int]:
    """Offsets in ``shown`` where a clause may begin or end."""
    cuts = {0, len(shown)}
    for pattern, where in _CUTS:
        for match in pattern.finditer(shown):
            cuts.add(match.end() if where == "end" else match.start())
    return sorted(cuts)


def clause_window(shown: str, mention_start: int, mention_end: int, limit: int = MAX_CHARS) -> tuple[int, int]:
    """Choose at most ``limit`` characters of ``shown`` around a mention, cut at clause boundaries.

    The clause holding the mention is kept whole; neighbouring clauses are added, left then right, while the total
    stays within ``limit``. When that one clause alone is longer than ``limit`` the cut falls on word boundaries
    around the mention. A cut never falls inside a word.

    Args:
        shown: Display text of the sentence.
        mention_start: Index of the mention in ``shown``.
        mention_end: Index just after the mention in ``shown``.
        limit: Maximum window length.

    Returns:
        ``(start, end)`` indices into ``shown`` (whitespace trimmed).
    """
    cuts = _clause_cuts(shown)
    li = max(0, bisect_right(cuts, mention_start) - 1)
    ri = min(len(cuts) - 1, bisect_left(cuts, mention_end))
    if cuts[ri] - cuts[li] <= limit:
        while True:
            grew = False
            if li > 0 and cuts[ri] - cuts[li - 1] <= limit:
                li -= 1
                grew = True
            if ri < len(cuts) - 1 and cuts[ri + 1] - cuts[li] <= limit:
                ri += 1
                grew = True
            if not grew:
                break
        start, end = cuts[li], cuts[ri]
    else:
        room = max(0, limit - (mention_end - mention_start))
        start = max(0, mention_start - room // 2)
        end = min(len(shown), start + limit)
        start = max(0, min(start, end - limit))
        while start > 0 and start < mention_start and shown[start - 1] != " ":
            start += 1
        while end < len(shown) and end > mention_end and shown[end] != " ":
            end -= 1
    while start < end and shown[start] == " ":
        start += 1
    while end > start and shown[end - 1] == " ":
        end -= 1
    start = _drop_leading_connector(shown, start, end, mention_start, cuts, limit)
    return start, end


_CONNECTOR = re.compile(r"(?:and|but|which|that|whereas|while)\b\s*", re.I)


def _drop_leading_connector(shown: str, start: int, end: int, mention_start: int, cuts: list[int], limit: int) -> int:
    """Move a window start off a bare connector or a lowercase opening.

    A start on ``and``/``but``/``which``/``that``/``whereas``/``while`` moves forward past the connector to the next
    word; a start on any other lowercase letter moves back to the previous clause boundary when that keeps the window
    within ``limit``. The mention is never cut off.
    """
    for _ in range(3):
        match = _CONNECTOR.match(shown, start, end)
        if not match or match.end() >= mention_start or match.end() >= end:
            break
        start = match.end()
    if start < end and shown[start].islower():
        earlier = [c for c in cuts if c < start]
        if earlier:
            back = earlier[-1]
            while back < start and shown[back] == " ":
                back += 1
            if back < start and end - back <= limit and not shown[back].islower():
                start = back
    return start


def _passage_for(
    pages: list[str], page_no: int, span: Span, refs: tuple[int, int]
) -> Passage:
    """Judge one located mention and build its :class:`Passage` (accepted, extended, windowed or rejected)."""
    index = page_index(pages[page_no])
    sentence = index.sentence_at(span.start)
    assert sentence is not None  # locate() only returns spans inside a sentence
    base = {
        "page": page_no + 1, "form": span.form, "mention": span.start, "sentence_start": sentence.start,
    }  # fmt: skip

    def rejected(reason: str) -> Passage:
        return Passage(sentence.text, start=sentence.start, end=sentence.end, reason=reason, **base)  # type: ignore[arg-type]

    if page_no > refs[0] or (page_no == refs[0] and sentence.start >= refs[1]):
        return rejected(REASON_REFERENCES)
    if sentence.reason:
        return rejected(sentence.reason)
    text, start, end = sentence.text, sentence.start, sentence.end
    if len(text) < MIN_CHARS:
        sentences = index.sentences
        pos = sentences.index(sentence)
        nxt = sentences[pos + 1] if pos + 1 < len(sentences) else None
        if (
            nxt is not None and nxt.reason is None and len(text) + 1 + len(nxt.text) <= MAX_CHARS
            and not (page_no == refs[0] and nxt.start >= refs[1])
        ):  # fmt: skip
            text, end = f"{text} {nxt.text}", nxt.end
        if len(text) < MIN_CHARS:
            return rejected(REASON_SHORT)
    windowed = False
    if len(text) > MAX_CHARS:
        local = bisect_left(sentence.offsets, span.start)
        local_end = bisect_left(sentence.offsets, span.end)
        w_start, w_end = clause_window(text, local, max(local + 1, local_end))
        start, end = sentence.offsets[w_start], sentence.offsets[w_end - 1] + 1
        text, windowed = text[w_start:w_end], True
    return Passage(text, start=start, end=end, clause_window=windowed, **base)  # type: ignore[arg-type]


def candidates(
    pages: list[str], term: str, *, abbr_defined: bool | None = None, only_pages: Sequence[int] | None = None
) -> list[Passage]:
    """Return every located mention of ``term`` in a paper, accepted and rejected, one per sentence.

    Args:
        pages: Page texts (see :func:`page_texts`).
        term: The term to find.
        abbr_defined: Passed to :func:`locate`.
        only_pages: 0-based indices of the pages to search (the references heading is still found over all pages);
            ``None`` searches every page.

    Returns:
        Passages in document order; ``reason`` is ``None`` for accepted ones and names the rule otherwise. Empty
        when the term is not found.
    """
    refs = references_start(pages)
    found: list[Passage] = []
    for page_no, text in enumerate(pages):
        if only_pages is not None and page_no not in only_pages:
            continue
        seen: set[int] = set()
        for span in locate(text, term, abbr_defined=abbr_defined):
            if span.sentence_start in seen:
                continue
            seen.add(span.sentence_start)
            found.append(_passage_for(pages, page_no, span, refs))
    return found


_DEFINITION_WORDS = re.compile(r"\b(?:is|are|defined|refers?|called|known as|used|measured)\b", re.I)


def _rank_key(passage: Passage) -> tuple[int, int, int, int]:
    """Ranking: 120-300 chars first, definition words, mention nearest the sentence start, earlier page."""
    band = 0 if MIN_CHARS <= passage.length <= PREFERRED_MAX else 1
    return band, -len(_DEFINITION_WORDS.findall(passage.text)), passage.mention - passage.sentence_start, passage.page


def best_passages(
    pages: list[str],
    term: str,
    *,
    limit: int = 3,
    abbr_defined: bool | None = None,
    only_pages: Sequence[int] | None = None,
) -> list[Passage]:
    """Return the best accepted passages for ``term``, best first.

    Args:
        pages: Page texts (see :func:`page_texts`).
        term: The term to find.
        limit: Maximum number of passages.
        abbr_defined: Passed to :func:`locate`.
        only_pages: Passed to :func:`candidates`.

    Returns:
        Accepted passages ordered by the ranking, de-duplicated by text.
    """
    accepted = [p for p in candidates(pages, term, abbr_defined=abbr_defined, only_pages=only_pages) if p.accepted]
    accepted.sort(key=_rank_key)
    out: list[Passage] = []
    seen: set[str] = set()
    for passage in accepted:
        if passage.text in seen:
            continue
        seen.add(passage.text)
        out.append(passage)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Grounding (design b): keep a model sentence only if the source has it, else locate the term
# ---------------------------------------------------------------------------------------------------------------------

#: A model sentence shorter than this many letters and digits is not specific enough to be found in a page.
MIN_SENTENCE_KEY = 10


@dataclass(frozen=True)
class Grounding:
    """What grounding a term's supporting sentence produced.

    Attributes:
        snippet: The passage to store (``quoted``/verified or ``located``), or ``None`` when nothing was found.
        rejected: ``True`` when the model gave a sentence that was not found in the page text (counted, never stored).
    """

    snippet: ContextSnippet | None
    rejected: bool


#: Classes of a rejected sentence (see :func:`classify_rejection`).
REJECT_NEAR_MATCH = "near_match"
REJECT_TABLE = "table_row"
REJECT_PARTIAL = "partial_overlap"
REJECT_ABSENT = "not_in_paper"
REJECT_CLASSES = (REJECT_NEAR_MATCH, REJECT_TABLE, REJECT_PARTIAL, REJECT_ABSENT)
_WORD3 = re.compile(r"[a-z]{3,}")


@lru_cache(maxsize=4096)
def _page_words(page_text: str) -> frozenset[str]:
    return frozenset(_WORD3.findall(page_text.lower()))


def classify_rejection(sentence: str, pages: Sequence[str]) -> tuple[str, int | None]:
    """Why a model sentence that was not found in the paper was not found: ``(class, 1-based page of best overlap)``.

    Diagnostic only (it changes nothing that is stored). The share of the sentence's words (three letters or more) found on
    its best page decides: a sentence made mostly of numbers and short cells with at least half of its words on a page is
    ``table_row`` (a row of a table written out as a sentence); at least 85% is ``near_match`` (the words are there; a
    paraphrase, an OCR or ligature difference or a reordering); at least 50% is ``partial_overlap`` (words from several lines);
    anything less is ``not_in_paper`` (the model's own words or background knowledge).
    """
    words = _WORD3.findall(sentence.lower())
    if not words:
        return REJECT_ABSENT, None
    best, best_page = 0.0, None
    for number, text in enumerate(pages, 1):
        known = _page_words(text)
        share = sum(w in known or w + "s" in known or (w.endswith("s") and w[:-1] in known) for w in words) / len(words)
        if share > best:
            best, best_page = share, number
    tokens = sentence.split()
    numeric = sum(bool(re.fullmatch(r"[\d.,;:()/±+–-]+", t)) for t in tokens)
    if best >= 0.5 and tokens and (numeric / len(tokens) >= 0.25 or sum(len(t) <= 3 for t in tokens) / len(tokens) >= 0.5):
        return REJECT_TABLE, best_page
    if best >= 0.85:
        return REJECT_NEAR_MATCH, best_page
    return (REJECT_PARTIAL if best >= 0.5 else REJECT_ABSENT), best_page


def find_sentence(pages: Sequence[str], sentence: str, *, current: int | None = None) -> int | None:
    """Return the 0-based index of the page that holds ``sentence``, or ``None``.

    The match is the squash match, which includes the exact match after whitespace normalisation (case, hyphenation,
    line breaks, dashes, ligatures, Greek letters and sub/superscripts are tolerated). The current page is tried
    first, then the others in order.

    Args:
        pages: Page texts of the paper.
        sentence: The model's sentence.
        current: 0-based index of the page the sentence came from, if known.

    Returns:
        The page index, or ``None`` (also for a sentence too short to be specific).
    """
    wanted = squash(sentence)[0]
    if len(wanted) < MIN_SENTENCE_KEY:
        return None
    order = list(range(len(pages)))
    if current is not None and 0 <= current < len(pages):
        order.remove(current)
        order.insert(0, current)
    for i in order:
        if wanted in page_index(pages[i]).key:
            return i
    return None


def ground_context(
    *,
    term: str,
    sentence: str | None,
    source_paper: str,
# ---------------------------------------------------------------------------------------------------------------------
# Aliases: the other names a paper gives a term, found by the shape of the text (no word list, no model)
# ---------------------------------------------------------------------------------------------------------------------

#: Longest alias kept and most words in one.
MAX_ALIAS_CHARS = 60
MAX_ALIAS_WORDS = 5
_ALIAS_INNER = r"([A-Za-z][^()\n]{1,78})"
_ALIAS_JUNK = re.compile(r"\d{4}|\bet al\b|\bfig|\btable\b|\beq\b|\bsee\b|\bp\.\s*\d|\bpp\b|\bvol\b|^[a-z]{1,2}$|\bhttp|@", re.I)
_ALIAS_LINK = r"(?:also|commonly|formerly|hereafter|hereinafter|sometimes)?\s*(?:known as|called|named|termed|referred to as|abbreviated(?: as| to)?|i\.e\.|or)"
_ALIAS_SPLIT = re.compile(rf"\s*(?:,|;)\s*(?:{_ALIAS_LINK}\s+)?|\s+(?:{_ALIAS_LINK})\s+", re.I)
#: Words a bracketed or preceding phrase may start with that are not part of the name (grammar, not a domain list).
_LEAD_WORDS = re.compile(r"^(?:the|a|an|this|these|those|its|their|our|or|and|of|in|as|is|are|was|were|so|i\.e\.|e\.g\.)\s+", re.I)


def _alias_pieces(candidate: str) -> list[str]:
    """The names inside a bracketed phrase: ``armoured catfish, commonly called plecos`` gives both names."""
    tail = re.split(r"[.;:!?]\s+", candidate)[-1]   # the clause after the last sentence boundary only
    out: list[str] = []
    for piece in _ALIAS_SPLIT.split(tail):
        piece = piece or ""
        while True:
            stripped = _LEAD_WORDS.sub("", piece.strip())
            if stripped == piece.strip():
                break
            piece = stripped
        if piece.strip():
            out.append(piece.strip())
    return out


def _alias_ok(candidate: str, term: str) -> str | None:
    text = " ".join(candidate.replace("\u2019", "'").split()).strip(" .,;:")
    if not (2 <= len(text) <= MAX_ALIAS_CHARS) or len(text.split()) > MAX_ALIAS_WORDS:
        return None
    if _ALIAS_JUNK.search(text) or text.lower() == term.lower():
        return None
    if sum(ch.isalpha() for ch in text) < max(2, len(text) // 2):
        return None
    return text


def aliases_in_text(term: str, text: str) -> list[str]:
    """Other names ``text`` gives ``term``, by the shape of the sentence, in order of appearance (no word list, no model).

    Shapes: ``term (X)`` and ``X (term)`` (an abbreviation, a common name, a synonym in brackets; ``X, also called Y``
    inside the brackets gives both), ``term, also known as X``, ``X, also called term``, ``term (hereafter X)``. A bracketed
    year, a citation, a figure or table reference, a URL and a bare one- or two-letter token are never aliases; a leading
    article or function word is dropped. The term itself is never returned.
    """
    base, abbr = term_forms(term)
    found: list[str] = []
    names = [n for n in (base, abbr) if n]
    lowered_names = {n.lower() for n in names}

    def keep(candidate: str, name: str) -> None:
        pieces = _alias_pieces(candidate)
        if any(re.search(r"\d{4}|\bet al\b", piece) for piece in pieces):
            return   # a bracket with a year or "et al." is a citation, not a list of names
        for piece in pieces:
            alias = _alias_ok(piece, name)
            if alias and alias.lower() not in {a.lower() for a in found} and alias.lower() not in lowered_names:
                found.append(alias)

    for name in names:
        esc = re.escape(name)
        for match in re.finditer(rf"\b{esc}\s*\(\s*(?:{_ALIAS_LINK}\s+)?{_ALIAS_INNER}\)", text, re.I):
            keep(match.group(1), name)
        for match in re.finditer(rf"{_ALIAS_INNER}\s*\(\s*(?:{_ALIAS_LINK}\s+)?{esc}\s*\)", text, re.I):
            keep(match.group(1), name)
        for match in re.finditer(rf"\b{esc}\s*,\s*{_ALIAS_LINK}\s+([^(),\n.;:]{{2,78}})", text, re.I):
            keep(match.group(1), name)
        for match in re.finditer(rf"([^(),\n.;:]{{2,78}}?)\s*,\s*{_ALIAS_LINK}\s+{esc}\b", text, re.I):
            keep(match.group(1), name)
    if abbr and abbr.lower() not in {a.lower() for a in found}:
        found.insert(0, abbr)   # the abbreviation of a ``long form (ABBR)`` term is itself an alias
    return found


def aliases_in_pages(term: str, pages: Sequence[str]) -> list[str]:
    """:func:`aliases_in_text` over every page of a paper, de-duplicated, first appearance first."""
    out: list[str] = []
    for page in pages:
        for alias in aliases_in_text(term, page):
            if alias.lower() not in {a.lower() for a in out}:
                out.append(alias)
    return out


    page: int | None,
    page_text: str | None,
    paper_pages: Sequence[str] | None,
) -> Grounding:
    """Ground the supporting sentence of one term (design b).

    The model's sentence is kept (kind ``quoted``, verified) only when the locator finds it in a page of the paper; the
    stored page is where it was found, not the claimed one. A sentence not found is rejected. With no sentence or a
    rejected one, the term's mention is located (the page it came from first, then the paper's other pages) and the
    best passage is stored as ``located``. Nothing found: no passage.

    Args:
        term: The term being registered.
        sentence: The model's ``context`` sentence, if any.
        source_paper: File name of the paper.
        page: 1-based page the term was extracted from, if known.
        page_text: Text of that page as the extractor saw it, if known.
        paper_pages: All page texts of the paper, if known.

    Returns:
        The :class:`Grounding`. Without any page text the sentence cannot be verified and is rejected.
    """
    pages: list[str]
    current: int | None
    first: int | None
    if paper_pages and (page is None or 1 <= page <= len(paper_pages)):
        pages, current, first = list(paper_pages), (page - 1 if page else None), 1
    elif page_text:
        pages, current, first = [page_text], 0, page
    else:
        return Grounding(None, rejected=bool(sentence and sentence.strip()))

    def number(index: int) -> int | None:
        return None if first is None else first + index

    clean = " ".join(sentence.split()) if sentence else ""
    rejected = False
    if clean:
        found = find_sentence(pages, clean, current=current)
        if found is not None:
            return Grounding(ContextSnippet(clean, source_paper, number(found), kind="quoted", verified=True), False)
        rejected = True
    best: list[Passage] = []
    if current is not None:
        best = best_passages(pages, term, limit=1, only_pages=[current])
    if not best:
        best = best_passages(pages, term, limit=1)
    if best:
        top = best[0]
        return Grounding(ContextSnippet(top.text, source_paper, number(top.page - 1), kind="located", verified=True),
                         rejected)
    return Grounding(None, rejected)


# ---------------------------------------------------------------------------------------------------------------------
# Relation evidence (located only): a sentence that names BOTH endpoints of a relation
# ---------------------------------------------------------------------------------------------------------------------

#: ``Passage.form`` of relation evidence: one sentence naming both endpoints, or two adjacent sentences that name one each.
FORM_BOTH = "both"
FORM_ADJACENT = "adjacent"
REASON_FAR = "endpoints_too_far"
REASON_WINDOW = "window_too_long"


@lru_cache(maxsize=65536)
def _locate_cached(page_text: str, term: str, abbr_defined: bool | None) -> tuple[Span, ...]:
    """:func:`locate` memoised per (page text, term, abbreviation flag): every relation of a term repeats these searches."""
    return tuple(locate(page_text, term, abbr_defined=abbr_defined))


@lru_cache(maxsize=16384)
def _paper_abbr_defined(pages: tuple[str, ...], term: str) -> bool | None:
    if term_forms(term)[1] is None:
        return None
    return True if any(s.form == "abbr" for page in pages for s in _locate_cached(page, term, None)) else None


def paper_abbr_defined(pages: Sequence[str], term: str) -> bool | None:
    """``True`` when the paper itself defines the abbreviation of ``long form (ABBR)`` on some page, else ``None``.

    Passed to :func:`locate` for every page, so an abbreviation defined on page 2 is also found on page 6.
    """
    return _paper_abbr_defined(tuple(pages), term)


def _hit_pairs(a_spans: list[Span], b_spans: list[Span]) -> tuple[int, int]:
    """Start and end (page offsets) of the closest pair of one endpoint-A mention and one endpoint-B mention."""
    best = min(((a, b) for a in a_spans for b in b_spans), key=lambda ab: abs(ab[0].start - ab[1].start))
    first, second = sorted(best, key=lambda s: s.start)
    return first.start, max(first.end, second.end)


def _relation_single(pages: list[str], page_no: int, pos: int, a_spans: list[Span], b_spans: list[Span],
                     refs: tuple[int, int]) -> Passage:
    """Judge one sentence that names both endpoints (extended, windowed or rejected like a term passage)."""
    index = page_index(pages[page_no])
    sentence = index.sentences[pos]
    m_start, m_end = _hit_pairs(a_spans, b_spans)
    base = {"page": page_no + 1, "form": FORM_BOTH, "mention": m_start, "sentence_start": sentence.start}

    def rejected(reason: str) -> Passage:
        return Passage(sentence.text, start=sentence.start, end=sentence.end, reason=reason, **base)  # type: ignore[arg-type]

    if page_no > refs[0] or (page_no == refs[0] and sentence.start >= refs[1]):
        return rejected(REASON_REFERENCES)
    if sentence.reason:
        return rejected(sentence.reason)
    text, start, end = sentence.text, sentence.start, sentence.end
    if len(text) < MIN_CHARS:
        nxt = index.sentences[pos + 1] if pos + 1 < len(index.sentences) else None
        if (nxt is not None and nxt.reason is None and len(text) + 1 + len(nxt.text) <= MAX_CHARS
                and not (page_no == refs[0] and nxt.start >= refs[1])):
            text, end = f"{text} {nxt.text}", nxt.end
        if len(text) < MIN_CHARS:
            return rejected(REASON_SHORT)
    windowed = False
    if len(text) > MAX_CHARS:
        lo = bisect_left(sentence.offsets, m_start)
        hi = bisect_left(sentence.offsets, m_end)
        w_start, w_end = clause_window(text, lo, max(lo + 1, hi))
        if w_start > lo or w_end < hi:
            return rejected(REASON_FAR)
        start, end = sentence.offsets[w_start], sentence.offsets[w_end - 1] + 1
        text, windowed = text[w_start:w_end], True
    return Passage(text, start=start, end=end, clause_window=windowed, **base)  # type: ignore[arg-type]


def _relation_window(pages: list[str], page_no: int, pos: int, mention: int, refs: tuple[int, int]) -> Passage:
    """Judge two adjacent sentences (``pos`` and ``pos + 1``) that name one endpoint each."""
    sentences = page_index(pages[page_no]).sentences
    first, second = sentences[pos], sentences[pos + 1]
    text = f"{first.text} {second.text}"
    base = {"page": page_no + 1, "form": FORM_ADJACENT, "mention": mention, "sentence_start": first.start}

    def rejected(reason: str) -> Passage:
        return Passage(text, start=first.start, end=second.end, reason=reason, **base)  # type: ignore[arg-type]

    for sentence in (first, second):
        if page_no > refs[0] or (page_no == refs[0] and sentence.start >= refs[1]):
            return rejected(REASON_REFERENCES)
        if sentence.reason:
            return rejected(sentence.reason)
    if len(text) > MAX_CHARS:
        return rejected(REASON_WINDOW)
    if len(text) < MIN_CHARS:
        return rejected(REASON_SHORT)
    return Passage(text, start=first.start, end=second.end, **base)  # type: ignore[arg-type]


def relation_candidates(pages: list[str], subject: str, obj: str, *, only_pages: Sequence[int] | None = None) -> list[Passage]:
    """Every place where a relation's two endpoints are named together, accepted or rejected with the reason.

    A candidate is one sentence that names both endpoints (``form="both"``) or two adjacent sentences of one page that name
    one endpoint each (``form="adjacent"``, only when neither sentence names both). Endpoints are found with
    :func:`locate` (squash rules, plural tail, short terms, ``(ABBR)`` where the paper defines it) and the sentences go
    through the same term-independent quality rules as a term passage (references, headers, citations, fragments,
    captions, length). A sentence naming only one endpoint is not a candidate: the context sentence of the term is
    therefore never evidence unless it names both.

    Args:
        pages: Page texts of the paper.
        subject: The relation's subject (the term).
        obj: The relation's object (``related_term``).
        only_pages: 0-based page indices to search (the references heading is still found over all pages).

    Returns:
        Candidates in document order.
    """
    refs = references_start(pages)
    a_abbr, b_abbr = paper_abbr_defined(pages, subject), paper_abbr_defined(pages, obj)
    found: list[Passage] = []
    for page_no, text in enumerate(pages):
        if only_pages is not None and page_no not in only_pages:
            continue
        a_all = list(_locate_cached(text, subject, a_abbr))
        b_all = list(_locate_cached(text, obj, b_abbr))
        if not a_all or not b_all:
            continue
        sentences = page_index(text).sentences
        pos_of = {s.start: i for i, s in enumerate(sentences)}
        a_at: dict[int, list[Span]] = {}
        b_at: dict[int, list[Span]] = {}
        for span in a_all:
            a_at.setdefault(pos_of[span.sentence_start], []).append(span)
        for span in b_all:
            b_at.setdefault(pos_of[span.sentence_start], []).append(span)
        both = sorted(set(a_at) & set(b_at))
        for pos in both:
            found.append(_relation_single(pages, page_no, pos, a_at[pos], b_at[pos], refs))
        for pos in sorted(set(a_at) | set(b_at)):
            nxt = pos + 1
            if pos in both or nxt in both:
                continue
            if (pos in a_at and nxt in b_at) or (pos in b_at and nxt in a_at):
                mention = min(s.start for s in (a_at.get(pos) or b_at.get(pos) or []))
                found.append(_relation_window(pages, page_no, pos, mention, refs))
    return found


def _relation_rank(passage: Passage) -> tuple[int, int, int, int, int]:
    """Both-endpoint sentences before two-sentence windows, then as for term passages."""
    band = 0 if MIN_CHARS <= passage.length <= PREFERRED_MAX else 1
    return (0 if passage.form == FORM_BOTH else 1, band, -len(_DEFINITION_WORDS.findall(passage.text)),
            passage.mention - passage.sentence_start, passage.page)


def relation_evidence(pages: list[str], subject: str, obj: str, *, only_pages: Sequence[int] | None = None) -> list[Passage]:
    """Every accepted sentence (or adjacent pair) that names both endpoints, best first, de-duplicated by text.

    No cap: storage keeps all of them; caps apply when evidence is displayed.
    """
    accepted = [p for p in relation_candidates(pages, subject, obj, only_pages=only_pages) if p.accepted]
    accepted.sort(key=_relation_rank)
    out: list[Passage] = []
    seen: set[tuple[int, str]] = set()
    for passage in accepted:
        key = (passage.page, passage.text)
        if key not in seen:
            seen.add(key)
            out.append(passage)
    return out


def evidence_entries(found: Sequence[Passage], source_paper: str) -> list[dict[str, Any]]:
    """Storage form of relation evidence: ``{text, source_paper, page, kind: "located", verified: true}``."""
    return [{"text": p.text, "source_paper": source_paper, "page": p.page, "kind": "located", "verified": True}
            for p in found]


def locate_relation_evidence(pages: list[str], subject: str, obj: str, source_paper: str) -> list[dict[str, Any]]:
    """Located evidence entries of one relation in one paper (see :func:`relation_evidence`)."""
    return evidence_entries(relation_evidence(pages, subject, obj), source_paper)


def relation_evidence_for(
    *,
    subject: str,
    obj: str,
    source_paper: str,
    page: int | None,
    page_text: str | None,
    paper_pages: Sequence[str] | None,
) -> list[dict[str, Any]]:
    """Located evidence of one relation at extraction time, from the pages the tool has.

    With the paper's pages every page is searched; with only the page being extracted that page is (its number is
    ``page``, or ``None`` when unknown). Without any page text nothing can be located.

    Returns:
        Evidence entries (see :func:`evidence_entries`), best first.
    """
    pages: list[str]
    first: int | None
    if paper_pages and (page is None or 1 <= page <= len(paper_pages)):
        pages, first = list(paper_pages), 1
    elif page_text:
        pages, first = [page_text], page
    else:
        return []
    entries = locate_relation_evidence(pages, subject, obj, source_paper)
    for entry in entries:
        entry["page"] = None if first is None else first + int(entry["page"]) - 1
    return entries

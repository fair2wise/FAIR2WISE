"""Locate a term in the pages of a paper and cut a supporting passage around it (design: supporting passages, step 1).

Pure and deterministic, no model calls. One tolerant locator serves extraction time (to verify or replace the model's
sentence) and the recovery job, so both behave the same:

* :func:`page_texts` is the shared page-text function: the same reader call the orchestrator uses
  (``open_pdf`` then ``load_page(i).get_text()``), so a passage located later matches what was extracted.
* :func:`squash` and :func:`locate` find a term whatever the page does to it (hyphenation, line breaks, ligatures,
  Greek letters, sub/superscripts, ``R-SoXS`` for ``RSoXS``).
* :func:`sentence_spans` and :class:`PageIndex` cut a page into sentences once and attach term-independent verdicts
  (references, header, citation, equation, caption ...), cached per page text.
* :func:`candidates` returns every located mention as a :class:`Passage`, accepted or rejected with a reason;
  :func:`best_passages` returns the ranked accepted ones, best first.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
import logging
from pathlib import Path
import re
import unicodedata

from ..literature.pdf_reader import PdfDocument, open_pdf

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

_INSTITUTION = re.compile(
    r"\b(?:Universit\w*|Department|Dept\.|Institut\w*|Laborator(?:y|ies)|School of|College|Centre|Center for"
    r"|Division of|Faculty|Academy of|National|Corporation|Inc\.|Ltd\.?|Foundation|Ministry)\b"
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


def _header_like(text: str) -> bool:
    """Header, title, affiliation or boilerplate: institution words, e-mail/URL/DOI, long comma lists, licence text."""
    if _EMAIL_URL.search(text) or _BOILERPLATE.search(text) or _JOURNAL_LINE.search(text):
        return True
    if _INSTITUTION.search(text):
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
    return start, end


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
    pages: list[str], term: str, *, abbr_defined: bool | None = None
) -> list[Passage]:
    """Return every located mention of ``term`` in a paper, accepted and rejected, one per sentence.

    Args:
        pages: Page texts (see :func:`page_texts`).
        term: The term to find.
        abbr_defined: Passed to :func:`locate`.

    Returns:
        Passages in document order; ``reason`` is ``None`` for accepted ones and names the rule otherwise. Empty
        when the term is not found.
    """
    refs = references_start(pages)
    found: list[Passage] = []
    for page_no, text in enumerate(pages):
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
    pages: list[str], term: str, *, limit: int = 3, abbr_defined: bool | None = None
) -> list[Passage]:
    """Return the best accepted passages for ``term``, best first.

    Args:
        pages: Page texts (see :func:`page_texts`).
        term: The term to find.
        limit: Maximum number of passages.
        abbr_defined: Passed to :func:`locate`.

    Returns:
        Accepted passages ordered by the ranking, de-duplicated by text.
    """
    accepted = [p for p in candidates(pages, term, abbr_defined=abbr_defined) if p.accepted]
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

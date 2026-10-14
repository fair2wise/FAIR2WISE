"""Deterministic merge-safety gate (C-02): may one surface form fold into another?

Embeddings and models only propose. This gate asks the narrower question "is one form
plausibly a spelling of the other?" without a model, so it cannot be argued round. It is
mandatory on every path that could merge (online resolution, group adjudication, cleanup
and migration apply). A human approval does not bypass it. Rejections are not discarded:
they are the rows a reviewer should see.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/merge_safety.py (BSD-3-Clause).
F2W additions at the end of the file: :func:`check_merge` adds the chemical-identity guard and
a same-schema-class rule. Identity checks never use class inheritance as evidence of identity
(RSoXSMeasurement is_a ExperimentalTechnique stays untouched; they are still different classes).
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache

#: Character-similarity floor for calling two forms renderings of each other.
#:
#: CHARACTERS, NOT TOKENS, and the difference is what makes this gate work. A
#: token-overlap rule cannot see a TYPO -- "Soxhlet" and "Sohxlet" share no
#: token at all -- while it happily admits
#: `AM 1.5 G solar simulator <- calibrated solar simulator`, which shares two
#: of three. Measured against the 61 live merges, character similarity gets
#: both right.
#:
#: 0.85 is where the corpus separates: every spelling, spacing and
#: pluralisation variant in it scores above (Pilatus "2M"/"2 M" 0.98,
#: Soxhlet/Sohxlet 0.94, Lightsource/Laboratory 0.88), and every
#: specific-to-generic pair scores well below (PI-MTE/CCD area detector 0.15,
#: Polymer Alignment/Rubbed Polymer Surfaces 0.51).
MIN_CHAR_SIMILARITY = 0.85

#: Longest an acronym may be. Beyond this a "letters appear in order" test
#: matches almost any pair of long strings.
MAX_ACRONYM_LEN = 10

#: Fewest identity-bearing words a form must have before it may be absorbed as
#: a SUBSET of a longer one.
#:
#: ONE WORD IS NOT A SPELLING VARIANT. "fitting" is a subset of "fitting to
#: experimental data", and merging them makes every fit in the corpus resolve
#: to one paper's phrasing. Two is where a form starts naming a thing rather
#: than a category: "Pilatus 2M" into "PILATUS 2M detector" is the same
#: detector said twice; "fitting" into a sentence is not.
MIN_SUBSET_TOKENS = 2

#: Words that carry no identity of their own. They are exactly what a generic
#: category and a specific model have in common, so a subset test that counted
#: them would let a category absorb a model number.
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "at", "in", "on", "for", "and", "with", "by",
    "detector", "system", "setup", "instrument", "apparatus", "chamber",
})


#: Qualifiers that turn one technique into another when added to its name:
#: geometry, probe, energy range, mode, regime. A form that differs from another
#: only by ADDING one of these is a variant, not a spelling.
_MODIFIERS = frozenset({
    "polarized", "polarised", "polarizing", "resonant", "soft", "hard", "grazing",
    "incidence", "incident", "ultra", "small", "wide", "angle", "scanning",
    "transmission", "reflection", "high", "low", "resolution", "situ", "operando",
    "ex", "time", "resolved", "distorted", "wave", "free", "inelastic", "elastic",
    "neutron", "electron", "x", "ray", "xray", "magnetic", "anomalous", "coherent",
    "dynamic", "near", "ambient", "pressure", "cryo", "cryogenic", "non",
})

#: Character similarity at which two DIFFERENT words are still one word misspelt
#: ("Soxhlet"/"Sohxlet" 0.86). Below it the word was swapped ("wide"/"small" 0.0,
#: "lightsource"/"laboratory" 0.38).
_SAME_WORD_SIMILARITY = 0.75


def _substituted_words(canonical: str, alias: str) -> tuple[str, str] | None:
    """A word on one side whose counterpart on the other is a DIFFERENT word.

    Returns the first ``(left_word, right_word)`` pair found, or None when every
    differing word on one side has a near-spelling on the other, or when one
    side only ADDS words (which the subset rule judges).
    """
    left_tokens, right_tokens = _tokens(canonical), _tokens(alias)
    left_only = [t for t in left_tokens if t not in set(right_tokens)]
    right_only = [t for t in right_tokens if t not in set(left_tokens)]
    # A COMPOUND WRITTEN SPLIT is one word: "FastScan" / "Fast Scan".
    for mine, theirs in ((left_only, right_only), (right_only, left_only)):
        for i in range(len(theirs) - 1):
            joined = theirs[i] + theirs[i + 1]
            if joined in mine:
                mine.remove(joined)
                del theirs[i : i + 2]
                break
    if not left_only or not right_only:
        return None
    for word in left_only:
        if not any(_same_word(word, other) for other in right_only):
            return word, right_only[0]
    return None


def _same_word(left: str, right: str) -> bool:
    """One word, spelt or inflected two ways: a typo, bend/bending, polarized/polarizing.

    A SHORT word is held to exact-or-plural. Short tokens are mostly acronyms,
    where one letter is the modifier: "tem" and "stem" score 0.86 as strings.
    """
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) <= 4 and not (len(shorter) == 4 and longer.startswith(shorter)):
        return longer in (shorter, shorter + "s")
    stem = len(os.path.commonprefix([left, right]))
    if stem >= min(6, len(shorter)):
        return True
    return SequenceMatcher(None, left, right).ratio() >= _SAME_WORD_SIMILARITY


@dataclass(frozen=True)
class MergeVerdict:
    """Whether one alias may fold into one canonical form, and why not."""

    allowed: bool
    reason: str = ""


def _fold(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace.

    THE SPACE IS LOAD-BEARING. Non-alphanumerics become a space rather than
    being deleted, because `_tokens` splits the result on whitespace: deleting
    the separator welds "poly(3-hexylthiophene)" style names into single tokens
    and changes which merges this gate approves. `vocab_cleanup.fold_key`
    deletes them instead -- correctly, for its purpose, which is collapsing
    spellings of one identifier onto one key. The two look alike and are not
    interchangeable. See `quote_location._fold_char` for the full comparison of
    the four folds in this repo and which may be unified.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", stripped)).strip()


def _tokens(text: str) -> list[str]:
    """Identity-bearing words: folded, with the category words dropped."""
    return [t for t in _fold(text).split() if t and t not in _STOPWORDS]


def _looks_like_an_acronym(text: str) -> bool:
    """Whether *text* is written the way an acronym is written.

    THE CASE IS THE EVIDENCE, and requiring it is what stops this branch
    swallowing ordinary words. Without it, "fitting" counts as an acronym of
    "fitting to experimental data" -- its letters do appear in order -- and the
    gate approves collapsing a whole activity into one paper's phrasing.
    """
    stripped = re.sub(r"[^A-Za-z]", "", text)
    if not (2 <= len(stripped) <= MAX_ACRONYM_LEN):
        return False
    uppercase = sum(1 for c in stripped if c.isupper())
    return uppercase >= max(2, len(stripped) - 1)


#: Words an expansion may carry that its acronym leaves out: the trailing class
#: noun ("Fourier transform infrared SPECTROSCOPY" -> FTIR) and chemistry's
#: functional-group words ("... butyric ACID methyl ESTER" -> PCBM).
_ACRONYM_OPTIONAL_WORDS = frozenset({
    "spectroscopy", "spectrometry", "microscopy", "measurement", "measurements",
    "analysis", "technique", "techniques", "method", "methods", "imaging",
    "experiment", "experiments", "acid", "ester", "calculation", "calculations",
    "simulation", "simulations",
})


def _acronym_words(long_form: str) -> list[str]:
    """The expansion's words, folded, with "x ray" re-joined as one word.

    `_fold` turns "X-ray" into two tokens, and no acronym in this field spends
    a letter on "ray" (XRD, GIWAXS, XPS) -- so as two words, every X-ray
    technique would fail the every-word-is-initialled rule below.
    """
    return re.sub(r"\bx ray\b", "xray", _fold(long_form)).split()


def _is_acronym_of(short: str, long_form: str) -> bool:
    """Whether *short* is an initialism of *long_form*.

    Accepts the two shapes papers use:

    * the acronym written VERBATIM, as a whole word, beside its expansion
      ("... scattering (GISAXS)");
    * an initialism in the Schwartz-Hearst sense: the acronym's characters are
      found in order, the FIRST one starts the first word, and EVERY content
      word of the expansion contributes its initial -- letters may also come
      from inside a word, which is how TGA is drawn from THERMOGRAVIMETRIC
      ANALYSIS. Only the words in `_ACRONYM_OPTIONAL_WORDS` and the stopwords
      may be skipped.

    THE OLD RULE WAS "LETTERS IN ORDER, ANYWHERE", AND IT MATCHED NEARLY
    EVERYTHING. Measured against the vocabulary's own entries, it called TEM
    an acronym of "thermogravimetric analysis" and of "scanning transmission
    electron microscopy", SAXS one of "resonant inelastic x-ray scattering",
    and CCD one of a solar-cell test station -- 211 cross-entry "renderings",
    none of them real. Every-word-initialled is what makes the match mean
    something: "transmission" has no letter in SEM, so SEM cannot be STEM.
    """
    if not _looks_like_an_acronym(short):
        return False
    letters = _fold(short).replace(" ", "")
    if not letters:
        return False
    words = _acronym_words(long_form)
    if letters in words:
        # VERBATIM -- but only beside its OWN expansion. "Carbon K-edge RSoXS"
        # contains RSoXS and names a variant of it; "... scattering (GISAXS)"
        # contains GISAXS and spells it out. The remainder decides.
        rest = [w for w in words if w != letters]
        if all(w in _ACRONYM_OPTIONAL_WORDS or w in _STOPWORDS for w in rest):
            return True  # "DFT calculations", "XRD measurements"
        return _is_acronym_of(short, " ".join(rest))
    if not words or len(words) > len(letters) + 4:
        return False
    # Numbers are optional words: "poly(3-hexylthiophene)" spends one on P3HT,
    # "[6,6]-phenyl-..." spends none on PCBM.
    required = [
        w not in _ACRONYM_OPTIONAL_WORDS and w not in _STOPWORDS and not w.isdigit()
        for w in words
    ]
    if sum(1 for w in words if w not in _STOPWORDS and not w.isdigit()) < 2:
        # ONE CONTENT WORD IS NOT AN EXPANSION. With letters allowed from inside
        # a word, any word holding the letters in order "expands" any acronym:
        # PS from "Perovskite", AU from "Alumina". A one-word name and its
        # abbreviation (PS / polystyrene) is a synonym for a human to list.
        return False

    @lru_cache(maxsize=None)
    def match(i: int, w: int, pos: int, started: bool, starts: int) -> bool:
        # i: next acronym character; w/pos: current word and offset in it;
        # started: whether word w has had its initial consumed; starts: how
        # many acronym characters were word INITIALS so far.
        if i == len(letters):
            # At least half the characters must be initials, or TEM is drawn
            # from inside THErMogravimetric (the optional "analysis" skipped).
            if 2 * starts < len(letters):
                return False
            # Every remaining required word must already be initialled.
            if w < len(words) and required[w] and not started:
                return False
            return not any(required[k] for k in range(w + 1, len(words)))
        if w >= len(words):
            return False
        word = words[w]
        # Option 1: consume letters[i] inside the current (already started) word.
        if started:
            hit = word.find(letters[i], pos)
            while hit >= 0:
                if match(i + 1, w, hit + 1, True, starts):
                    return True
                hit = word.find(letters[i], hit + 1)
        # Option 2: move on to a later word and consume its INITIAL. Words
        # skipped on the way must be optional.
        if started or not required[w]:
            nxt = w + 1 if started else w
            for k in range(nxt, len(words)):
                if words[k][0] == letters[i] and match(i + 1, k, 1, True, starts + 1):
                    return True
                if required[k]:
                    break
        elif word[0] == letters[i] and match(i + 1, w, 1, True, starts + 1):
            return True
        return False

    # The first character must start the first required word (Schwartz-Hearst).
    return match(0, 0, 0, False, 0)


def _digit_runs(text: str) -> str:
    """The digits in a form, in order: ``PC71BM`` -> ``"71"``.

    Concatenated rather than split into numbers, so a separator inside one
    number does not make two: beamline "7.3.3" and "733" are one beamline.
    """
    return "".join(re.findall(r"\d", _fold(text)))


def _two_different_acronyms(canonical: str, alias: str) -> bool:
    """Whether both forms are written as acronyms and are not the same one.

    TWO ACRONYMS ARE TWO NAMES. An acronym is already the shortest rendering of
    its term, so two different ones are not spellings of each other -- they are
    usually one technique and its MODIFIED variant, and the modifier is a
    letter: S-TEM, U-SAXS, GI-WAXS, N-EXAFS, HA-XPES, GI-XRD.

    The acronym branch below cannot be trusted with these, and that is the hole
    this closes: "TEM" appears VERBATIM inside "STEM", so `_is_acronym_of`
    approved STEM -> TEM as "an acronym beside its expansion", and character
    similarity approved USAXS -> SAXS at 0.89. Both are different measurements.

    The one difference let through is a trailing plural ``s`` ("XRDs"), which
    is grammar, not a modifier.
    """
    if not (_looks_like_an_acronym(canonical) and _looks_like_an_acronym(alias)):
        return False
    left = _fold(canonical).replace(" ", "")
    right = _fold(alias).replace(" ", "")
    if left == right:
        return False
    shorter, longer = sorted((left, right), key=len)
    return longer != shorter + "s"


def may_merge(canonical: str, alias: str) -> MergeVerdict:
    """Whether *alias* is plausibly a rendering of *canonical*.

    Args:
        canonical: The form the group would keep.
        alias: The form that would fold into it.

    Returns:
        A :class:`MergeVerdict`. `allowed` is False with a reason a reviewer
        can act on -- these rows are routed to review, not discarded.
    """
    left, right = _fold(canonical), _fold(alias)
    if not left or not right:
        return MergeVerdict(False, "one form is empty after folding")
    if left == right:
        return MergeVerdict(True, "identical after folding")

    # BEFORE the acronym branch, which would otherwise approve STEM -> TEM
    # because one string contains the other. See `_two_different_acronyms`.
    if _two_different_acronyms(canonical, alias):
        return MergeVerdict(
            False,
            "two different acronyms: an extra letter is usually a different "
            "measurement (STEM/TEM, USAXS/SAXS, GIWAXS/WAXS)",
        )

    if _is_acronym_of(alias, canonical) or _is_acronym_of(canonical, alias):
        return MergeVerdict(True, "one form is an acronym of the other")

    # A DIFFERENT NUMBER IS A DIFFERENT THING, and character similarity cannot
    # see it: one digit in six moves PC61BM -> PC71BM only to 0.83, and in a
    # longer name it would not move the score at all. In this corpus the number
    # is the identity -- the fullerene cage (C61 vs C71), the polymer (PM6 vs
    # PM7), the acceptor (Y6 vs Y7), the detector generation (Pilatus vs
    # PILATUS3). Same numbers in the same order (Pilatus "2M"/"2 M") still pass.
    # Checked AFTER the acronym branch, where an expansion legitimately carries
    # digits its acronym folds in ("[6,6]-phenyl-C61-butyric..." -> PC61BM).
    if _digit_runs(canonical) != _digit_runs(alias):
        return MergeVerdict(
            False,
            f"the forms carry different numbers ({_digit_runs(canonical)} vs "
            f"{_digit_runs(alias)}); a number is usually the identity "
            "(PC61BM/PC71BM, PM6/PM7)",
        )

    # SPACING ONLY: "Pilatus 2M" / "Pilatus 2 M", "TST-350" / "TST350".
    if left.replace(" ", "") == right.replace(" ", ""):
        return MergeVerdict(True, "identical apart from spacing")

    # A QUALIFIER ADDED is a different measurement however similar the strings:
    # "ULTRA-small-angle x-ray scattering" against "small angle x-ray
    # scattering" is 0.90, "HARD x-ray photoelectron spectroscopy" against
    # "x-ray photoelectron spectroscopy" 0.93, "SCANNING transmission electron
    # microscopy" against TEM's expansion 0.88. Checked before similarity for
    # that reason.
    left_tokens, right_tokens = set(_tokens(canonical)), set(_tokens(alias))
    shorter, longer = sorted((left_tokens, right_tokens), key=len)
    added_modifiers = sorted((longer - shorter) & _MODIFIERS)
    if shorter and shorter <= longer and added_modifiers:
        return MergeVerdict(
            False,
            f"the longer form adds a qualifier that changes the thing named: "
            f"{added_modifiers}",
        )

    similarity = SequenceMatcher(None, left, right).ratio()
    substituted = _substituted_words(canonical, alias)
    if similarity >= MIN_CHAR_SIMILARITY and not substituted:
        return MergeVerdict(True, f"character similarity {similarity:.2f}")
    if substituted and similarity >= MIN_CHAR_SIMILARITY:
        # A HIGH SCORE OVER A SWAPPED WORD. Long phrases that differ in one
        # word score high on characters: "grazing incidence WIDE angle x-ray
        # scattering" against "... SMALL angle ..." is 0.90, "scanning
        # transmission X-RAY microscopy" against "... ELECTRON ..." 0.86, "CV
        # measurement" against "J-V measurement" 0.90 -- GIWAXS/GISAXS,
        # STXM/STEM, voltammetry/photovoltaic current. The swapped word is the
        # identity; a typo changes letters inside a word, not the word.
        return MergeVerdict(
            False,
            f"character similarity {similarity:.2f}, but a word is swapped, not "
            f"misspelt: {substituted[0]!r} vs {substituted[1]!r}",
        )

    # WORD REORDERINGS AND TRAILING CATEGORY WORDS, which character similarity
    # scores badly: "Pilatus 2M" against "PILATUS 2M detector" is 0.69 while
    # being the same detector written twice. A subset of identity-bearing words
    # catches those without admitting the specific-to-generic collapses --
    # "calibrated solar simulator" is NOT a subset of "AM 1.5 G solar
    # simulator", because "calibrated" is absent from it.
    # (A subset that ADDS a qualifier -- "Born approximation" inside
    # "DISTORTED WAVE Born approximation", "optical microscopy" inside
    # "POLARIZED optical microscopy", "spin coating" inside "spin-coating-FREE
    # technologies" -- was refused above. Category words and vendor names pass.)
    if len(shorter) >= MIN_SUBSET_TOKENS and shorter <= longer:
        return MergeVerdict(
            True, f"the shorter form's {len(shorter)} words all appear in the longer"
        )
    return MergeVerdict(
        False,
        f"character similarity {similarity:.2f} < {MIN_CHAR_SIMILARITY}: these "
        "differ in content, not in spelling",
    )


def check_merge(
    canonical: str,
    alias: str,
    *,
    canonical_class: str | None = None,
    alias_class: str | None = None,
) -> MergeVerdict:
    """The full F2W identity check: class, chemical identity, then :func:`may_merge`.

    Args:
        canonical: The form the merge would keep.
        alias: The form that would fold into it.
        canonical_class / alias_class: Schema class (or category) of each side, when known.
            Different classes never merge here, even if one inherits from the other in the
            schema: inheritance relates classes, it does not make two records the same entity.

    Returns:
        A :class:`MergeVerdict`; ``allowed=False`` carries a reviewer-readable reason.
    """
    if canonical_class and alias_class and canonical_class != alias_class:
        return MergeVerdict(False, f"different schema classes ({canonical_class} vs {alias_class})")
    from .chemical_identity import chemical_verdict

    chemical = chemical_verdict(canonical, alias)
    if chemical is not None:
        return chemical
    return may_merge(canonical, alias)


#: Public names for helpers other canonicalization modules share (same behaviour).
fold = _fold
is_acronym_of = _is_acronym_of
looks_like_an_acronym = _looks_like_an_acronym

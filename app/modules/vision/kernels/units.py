"""Reading a length or an inverse length out of text a model copied from a PDF.

ONE FOLD TABLE, TWO PIPELINES. The scale-bar pipeline needs "2 um" and the
q-axis pipeline needs "nm^-1"; both meet the same hazard, and this repo has
already paid for it twice in two different modules. `_LENGTH_UNIT_SPELLINGS` in
`pdf_extraction.py` fixed it for the mesophase path, and the fix was not carried
into the peaks path, which then refused inverse angstroms -- the unit GIWAXS
papers overwhelmingly use -- for another whole corpus run. It lives here once so
a third pipeline inherits it rather than rediscovering it.

WHAT REAL CAPTIONS LOOK LIKE, measured on this corpus:

    'Å'   'Å -1'   'Å^-1'   'nm⁻¹'   'nanometers'   'µm'   'μm'   '1 m m'

`µ` (U+00B5 MICRO SIGN) and `μ` (U+03BC GREEK SMALL LETTER MU) render
identically and both occur; a table holding one silently drops half the
micrometre captions. `'m m'` is not a typo either -- the parser splits some
micro signs into a bare `m` plus the unit's own `m`, observed verbatim in
"scale bars are 1 m m".

THE UNIT MUST NEVER COME FROM OCR, and that rule is enforced by the callers of
this module rather than inside it. Measured: RapidOCR reads 6-11 px tick digits
at 0.95-1.00 confidence and mangles the superscript exponent on 100% of
attempts -- `q (nm⁻¹)` comes back as `'q(nm^{1)'`. Getting the exponent wrong is
a factor of ten in the answer and looks entirely plausible, so an inverse-length
unit is trustworthy only from the PDF's own text layer or from caption prose.


Adapted from als-computing/forge_rsoxs_analysis src/forge/analysis/figure_extraction/units.py (BSD-3-Clause).
"""

from __future__ import annotations

import re

__all__ = [
    "INVERSE_UNIT_TO_NM_INV",
    "LENGTH_UNIT_TO_NM",
    "caption_inverse_unit",
    "caption_inverse_units",
    "fold_inverse_length",
    "fold_length",
    "iter_lengths",
    "normalise_unit",
]

#: Length spellings to nanometres. Keys are already normalised by
#: :func:`normalise_unit`, so this holds one entry per unit rather than one per
#: way of writing it.
LENGTH_UNIT_TO_NM: dict[str, float] = {
    "nm": 1.0,
    "nanometer": 1.0,
    "nanometre": 1.0,
    "um": 1_000.0,
    "micrometer": 1_000.0,
    "micrometre": 1_000.0,
    "micron": 1_000.0,
    "mm": 1_000_000.0,
    "m m": 1_000_000.0,
    "a": 0.1,
    "angstrom": 0.1,
    "pm": 0.001,
}

#: Inverse-length spellings to inverse nanometres. The 10x between these two
#: is the error this module exists to prevent.
INVERSE_UNIT_TO_NM_INV: dict[str, float] = {
    "nm-1": 1.0,
    "a-1": 10.0,
    "angstrom-1": 10.0,
}

#: Both micro signs, folded to a plain ASCII `u`. They are different codepoints
#: that render identically, so no reader can tell them apart by eye and no
#: writer can be relied on to pick one.
_MICRO = {"µ": "u", "μ": "u"}

#: Superscript minus and digit one, which is how a PDF text layer spells the
#: exponent when it is NOT split into a separate run.
_SUPERSCRIPT = {"⁻": "-", "¹": "1", "−": "-"}

_ANGSTROM = {"Å": "a", "å": "a", "Å": "a"}


def normalise_unit(text: str) -> str:
    """Fold one written unit onto a table key.

    Args:
        text: The unit as it appeared, e.g. ``"Å -1"``, ``"µm"``, ``"m m"``.

    Returns:
        A lowercase key: the micro signs become ``u``, the angstrom signs
        become ``a``, superscript minus/one become ASCII, ``^`` and stray
        spaces around an exponent are dropped, and a trailing plural ``s`` is
        removed. ``"m m"`` is preserved as itself, since it is a real spelling
        rather than a spacing artefact this can safely collapse.
    """
    folded = "".join(_MICRO.get(c, _SUPERSCRIPT.get(c, _ANGSTROM.get(c, c))) for c in text)
    folded = folded.strip().lower().replace("^", "")
    # "a -1" / "nm - 1" -> "a-1" / "nm-1"; the exponent is one token however
    # the parser spaced it.
    folded = re.sub(r"\s*-\s*1\b", "-1", folded)
    folded = re.sub(r"\s+", " ", folded).strip()
    # Plural trim. Guarding this against exponent forms was considered and
    # DELETED rather than kept: no unit spelling ends in both "s" and "-1", so
    # no test could ever fail the guard, and a guard nobody can falsify is one
    # nobody can trust. Should such a spelling ever appear, this is the line.
    if folded.endswith("s"):
        folded = folded[:-1]
    return folded


def fold_length(value: float, unit: str) -> float | None:
    """A length and its written unit, in nanometres.

    Args:
        value: The magnitude, already parsed.
        unit: The unit as written, in any spelling.

    Returns:
        Nanometres, or ``None`` when the unit is not one this understands or
        the value is not positive. ``None`` is a REFUSAL and never a default: a
        caption whose unit cannot be read has no length this may report, and
        guessing nanometres would be wrong by a factor of 1,000 for a
        micrometre bar.
    """
    if not (value > 0):
        return None
    factor = LENGTH_UNIT_TO_NM.get(normalise_unit(unit))
    return None if factor is None else value * factor


def fold_inverse_length(value: float, unit: str) -> float | None:
    """An inverse length and its written unit, in inverse nanometres.

    Args:
        value: The magnitude.
        unit: The unit as written, e.g. ``"Å^-1"``, ``"nm⁻¹"``.

    Returns:
        Inverse nanometres, or ``None``. An angstrom-inverse read as a
        nanometre-inverse is a structure ten times too large, landing on a
        plausible number, which is why an unrecognised unit refuses rather than
        assuming the commoner one.
    """
    if not (value > 0):
        return None
    factor = INVERSE_UNIT_TO_NM_INV.get(normalise_unit(unit))
    return None if factor is None else value * factor


#: A magnitude followed by a unit. The alternation is longest-first so `mm`
#: cannot be taken as `m` plus stray text and `m m` gets its chance before `m`.
_LENGTH_RE = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>m\s?m|nm|µm|μm|um|mm|nanometers?|nanometres?|"
    r"micrometers?|micrometres?|microns?|pm|Å|å|Å|A)\b",
    re.IGNORECASE,
)


#: An inverse-length unit as caption prose spells it: a unit name, then an
#: exponent of MINUS ONE attached to it, optionally bracketed. The exponent
#: must sit against the unit and be exactly -1, which is what separates
#: ``"q (nm^-1)"`` from the two traps this corpus actually prints --
#: ``"q^2 (nm^-2)"`` is a q-SQUARED axis and ``"d^-1 (nm)"`` is a length, and
#: both contain the substring ``nm``.
_CAPTION_INVERSE_UNIT = re.compile(
    r"[\(\[]?\s*(nm|nanometre|nanometer|a|\u00c5|\u212b|angstrom|\u00e5ngstr\u00f6m)"
    r"\s*(?:\^|\*\*)?\s*[-\u2212\u207b]\s*(?:1|\u00b9)(?![0-9])\s*[\)\]]?",
    re.IGNORECASE,
)


def caption_inverse_unit(caption: str) -> str:
    """The inverse-length unit a caption states, folded, or ``""``.

    THE SECOND ACCEPTABLE PROVENANCE FOR A UNIT, and for most of this corpus
    the only one. In-figure axis titles are usually rasterised into the image,
    so pymupdf finds no unit inside the figure and OCR mangles the exponent
    every time -- measured, RapidOCR renders one plate's q axis as
    ``'q2 [A{]}'``. The caption is prose in the PDF's own text layer, which
    means it is readable and it is not a transcription.

    Args:
        caption: The figure's caption.

    Returns:
        A key :func:`fold_inverse_length` accepts, e.g. ``"nm-1"`` or
        ``"a-1"``. Empty when the caption states no inverse-length unit, or
        states something that only looks like one -- a caption naming BOTH
        gives nothing, because a figure whose caption says angstrom in one
        sentence and nanometre in another does not tell you which axis is
        which, and picking the first is a coin flip on a factor of ten.
    """
    found = {
        normalise_unit(match.group(0).strip("()[] "))
        for match in _CAPTION_INVERSE_UNIT.finditer(caption or "")
    }
    usable = {unit for unit in found if fold_inverse_length(1.0, unit)}
    return usable.pop() if len(usable) == 1 else ""


def caption_inverse_units(caption: str) -> frozenset[str]:
    """Every usable inverse-length unit a caption states, folded (F2W addition, not in the donor).

    :func:`caption_inverse_unit` collapses "none" and "ambiguous" to ``""``; callers that must
    tell them apart (to surface the ambiguity instead of a silent no-unit) use this set.
    """
    found = {
        normalise_unit(match.group(0).strip("()[] "))
        for match in _CAPTION_INVERSE_UNIT.finditer(caption or "")
    }
    return frozenset(unit for unit in found if fold_inverse_length(1.0, unit))


def document_inverse_unit(texts: list[str] | tuple[str, ...]) -> str:
    """The inverse-length unit a PAPER states, folded, or ``""``.

    THE THIRD ACCEPTABLE PROVENANCE, and the one that makes this readable at
    all. Measured on the 998 figures holding scattering content: a caption
    yields a unit for **40 of them (4.0%)**, and the panels underneath refuse
    with ``no_unit`` 94% of the time -- the ticks read fine, the unit is what is
    missing. The same regex over the PAPER's own section text yields exactly one
    unit for 175 of 412 publications, covering **435 figures (43.6%)**.

    IT IS THE SAME KIND OF EVIDENCE AS A CAPTION, which is why it is allowed:
    docling section text is prose out of the PDF's own text layer. It is not a
    transcription, so it does not carry the failure that bars OCR here -- a
    mangled exponent turning angstrom into nanometre, a factor of ten that lands
    on a plausible number.

    IT IS WEAKER THAN A CAPTION, which is why it is a SEPARATE source rather
    than folded in. A caption describes one figure; a paper's prose describes a
    paper, and a paper may plot two things on two axes. That risk is handled the
    only honest way available: **exactly one unit, or nothing.** A document
    naming both angstrom and nanometre gives ``""`` -- the same coin-flip
    refusal `caption_inverse_unit` already makes, for the same reason. 16 of
    412 publications land there.

    Args:
        texts: The paper's section texts, in any order.

    Returns:
        A key :func:`fold_inverse_length` accepts, or ``""``.
    """
    found: set[str] = set()
    for text in texts:
        for match in _CAPTION_INVERSE_UNIT.finditer(text or ""):
            found.add(normalise_unit(match.group(0).strip("()[] ")))
    usable = {unit for unit in found if fold_inverse_length(1.0, unit)}
    return usable.pop() if len(usable) == 1 else ""


def iter_lengths(text: str) -> list[float]:
    """Every length in *text*, in nanometres, in the order written.

    Args:
        text: Caption prose, or a text-layer run.

    Returns:
        The lengths this could read. Unreadable units are SKIPPED rather than
        raising -- a caption naming one bar and one unrelated wavelength should
        yield the bar, not an error -- so a caller that needs to know the
        caption was fully understood must compare against its own expectation.
    """
    out: list[float] = []
    for match in _LENGTH_RE.finditer(text):
        nm = fold_length(float(match["value"].replace(",", ".")), match["unit"])
        if nm is not None:
            out.append(nm)
    return out

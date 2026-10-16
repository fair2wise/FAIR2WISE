"""Conservative lexical source-support checks; not semantic verification (E-04).

:func:`source_support` passes only when the quote is present in the source and, if a
value is given, the same number sits next to an equivalent unit inside the quote. A
pass establishes *text support*: it cannot show that a model assigned the right
scientific meaning, nor that the publication's measurement is correct. Use it with
:func:`~app.modules.evidence.quote_location.locate_quote`, never instead of review.

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/literature/grounding.py
(BSD-3-Clause).
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import List, Tuple

from .units import INVERSE_UNIT_TO_NM_INV, LENGTH_UNIT_SPELLINGS, canonical_unit, unit_dimension


#: Closed set of verdicts returned by :func:`source_support`.
SUPPORT_VERDICTS = (
    "source_supported", "missing_quote", "quote_not_found", "qualifier_not_supported",
    "invalid_value", "missing_unit", "value_unit_not_supported",
)


_NUMBER = re.compile(r"(?<![\w.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_UNCERTAINTY_GAP = re.compile(r"^(?:\u00b1|\+/-|\+-)\s*(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s*")


def normalize(text: str) -> str:
    """NFKC-fold, unify minus/en-dash, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).replace("−", "-").replace("–", "-")
    return " ".join(text.split())


def source_support(
    source: str,
    quote: str,
    value: float | None = None,
    unit: str = "",
    *,
    sample_id: str = "",
    processing_state: str = "",
) -> str:
    """Require a located quote and a locally paired as-reported number/unit.

    Missing or ambiguous evidence remains a candidate. A pass establishes text
    support only: it cannot establish that an LLM assigned the correct scientific
    meaning, or that the publication's measurement itself is correct.
    """
    if not quote.strip():
        return "missing_quote"
    text = normalize(quote)
    if not source or text not in normalize(source):
        return "quote_not_found"
    for qualifier in (sample_id, processing_state):
        if qualifier and normalize(qualifier).casefold() not in text.casefold():
            return "qualifier_not_supported"
    if value is None:
        return "source_supported"
    if not math.isfinite(value):
        return "invalid_value"
    # Do not let a nearby unrelated number or a different unit qualify a claim.
    number = r"(?<![\w.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    for match in re.finditer(number, text):
        if not math.isclose(float(match[0]), value, rel_tol=1e-9, abs_tol=1e-12):
            continue
        tail = text[match.end():].lstrip(" $\\")
        # F2W: a stated uncertainty may sit between the value and its unit
        # ("105 ± 5 nm", "105 +/- 5 nm"); it does not change which unit the value has.
        tail = _UNCERTAINTY_GAP.sub("", tail, count=1)
        if not unit:
            return "missing_unit"
        # Unit spelling folds are shared with the actual numerical converters.
        for end in range(1, min(24, len(tail)) + 1):
            if end < len(tail) and (tail[end].isalnum() or tail[end] in "^-⁻¹"):
                continue
            got, wanted = canonical_unit(tail[:end]), canonical_unit(unit)
            equivalent = (
                got == wanted
                or (
                    got in LENGTH_UNIT_SPELLINGS
                    and LENGTH_UNIT_SPELLINGS.get(got) == LENGTH_UNIT_SPELLINGS.get(wanted)
                )
                or (
                    got in INVERSE_UNIT_TO_NM_INV
                    and INVERSE_UNIT_TO_NM_INV.get(got) == INVERSE_UNIT_TO_NM_INV.get(wanted)
                )
            )
            if equivalent:
                return "source_supported"
    return "value_unit_not_supported"


def printed_quantities(quote: str) -> List[Tuple[float, str]]:
    """Every ``(number, unit)`` pair printed in ``quote`` whose unit has a known dimension (F2W, T06B).

    The unit is the shortest token after the number (past a stated uncertainty)
    that :func:`~app.modules.evidence.units.unit_dimension` recognises, with the
    same token boundaries as :func:`source_support`. A number with no recognised
    unit is not listed. Used to check that a derived value's inputs are printed.
    """
    text = normalize(quote or "")
    found: List[Tuple[float, str]] = []
    for match in _NUMBER.finditer(text):
        tail = _UNCERTAINTY_GAP.sub("", text[match.end():].lstrip(" $\\"), count=1)
        for end in range(1, min(24, len(tail)) + 1):
            if end < len(tail) and (tail[end].isalnum() or tail[end] in "^-⁻¹"):
                continue
            if unit_dimension(tail[:end]) != "unknown":
                value = float(match[0])
                if math.isfinite(value):
                    found.append((value, tail[:end]))
                break
    return found

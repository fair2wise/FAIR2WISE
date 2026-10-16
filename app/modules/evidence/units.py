"""Paper-reported unit handling: refuse, never guess (E-05).

Scoped to values a paper prints and to dimensional checks. :func:`canonical_unit`
folds spelling only; closed tables decide meaning; strict converters raise and the
tolerant wrappers return ``(value, "")`` or ``(None, reason)``. No unit-parsing
library sits behind this on purpose (``pint`` reads ``"A"`` as ampere).

:func:`normalize_quantity` is the F2W addition: it keeps the raw value and unit
exactly as printed beside the normalized value and records the conversion
provenance (table, factor, profile), so a normalized number is always traceable
back to what the source said. No beamline or simulation defaults live here.

``QUANTITY_DIMENSION`` keys are the donor's closed ``QuantityName`` set. They are
not F2W schema slots; mapping them is pending owner approval at H3
(docs/migration/extraction_field_mapping.md).

The catalog target was ``app/modules/science/units.py``; T06 owns only
``app/modules/evidence``, so the module lives here until the science package exists.

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/literature/units.py
(BSD-3-Clause).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "ANGLE_TO_DEG",
    "CONVERSION_PROFILE",
    "NormalizedQuantity",
    "normalize_quantity",
    "ENERGY_TO_EV",
    "INSTRUMENT_QUANTITY_DIMENSION",
    "INVERSE_UNIT_TO_NM_INV",
    "RECIPROCAL_DERIVATION_REL_TOL",
    "base_quantity_name",
    "expected_dimension",
    "quantity_unit_mismatch",
    "reciprocal_derivation",
    "LENGTH_TO_NM",
    "LENGTH_UNIT_SPELLINGS",
    "QUANTITY_DIMENSION",
    "canonical_unit",
    "dimension_mismatch",
    "length_in_nm",
    "q_in_nm_inv",
    "q_to_d_nm",
    "to_deg",
    "to_ev",
    "to_nm",
    "unit_dimension",
]

#: Conversion factors TO nanometres. EXACT and case-sensitive on purpose: this
#: is the typed contract, and a caller holding a `ExtractedQuantity` has already
#: decided what its unit is. Text out of a PDF goes through
#: `LENGTH_UNIT_SPELLINGS` first.
LENGTH_TO_NM: dict[str, float] = {
    "nm": 1.0,
    "angstrom": 0.1,
    "A": 0.1,
    "pm": 1e-3,
    "um": 1e3,
    "micron": 1e3,
    "mm": 1e6,
}

#: Conversion factors TO electronvolts.
ENERGY_TO_EV: dict[str, float] = {
    "eV": 1.0,
    "keV": 1e3,
}

#: Conversion factors TO degrees.
ANGLE_TO_DEG: dict[str, float] = {
    "deg": 1.0,
    "rad": 180.0 / math.pi,
    "mrad": 180.0 / math.pi / 1000.0,
}

#: Spellings this corpus prints, mapped onto `LENGTH_TO_NM`'s keys.
#:
#: Keys are `canonical_unit` output (spaces gone, case folded, glyph folded to
#: `a`, micro sign to `u`), so this table only has to carry real synonyms.
#: An unlisted spelling still refuses; the point is to stop refusing units this
#: repo already knows how to convert, never to guess at one it does not.
LENGTH_UNIT_SPELLINGS: dict[str, str] = {
    "nm": "nm",
    "nanometer": "nm",
    "nanometers": "nm",
    "nanometre": "nm",
    "nanometres": "nm",
    "a": "A",
    "angstrom": "A",
    "angstroms": "A",
    "pm": "pm",
    "picometer": "pm",
    "picometre": "pm",
    "um": "um",
    "micron": "um",
    "microns": "um",
    "micrometer": "um",
    "micrometre": "um",
    "micrometers": "um",
    "micrometres": "um",
    "mm": "mm",
    "millimeter": "mm",
    "millimetre": "mm",
}

#: Reciprocal-length spellings, mapped to the factor that turns a q in that
#: unit into nm^-1. Keys are `canonical_unit` output, so 'Å⁻¹', 'Å -1', 'A^-1'
#: and 'angstrom-1' all arrive as 'a^-1' or 'a-1'. An unlisted spelling
#: REFUSES -- an Angstrom-inverse read as a nanometre-inverse is a structure
#: ten times too large simulated under the paper's name.
INVERSE_UNIT_TO_NM_INV: dict[str, float] = {
    "nm^-1": 1.0,
    "nm-1": 1.0,
    "1/nm": 1.0,
    "a^-1": 10.0,
    "a-1": 10.0,
    "1/a": 10.0,
    "angstrom^-1": 10.0,
    "angstrom-1": 10.0,
    "1/angstrom": 10.0,
}


def canonical_unit(unit: str) -> str:
    """A unit string as the PAPER prints it, reduced to one spelling.

    Papers print the ANGSTROM GLYPH -- extracted live from this corpus:
    ``'Å -1'`` (glyph, space, -1) and ``'Å'`` -- and the model copies
    units verbatim because the prompt tells it to. An ASCII-only match refused
    every one of a real paper's six extracted peaks. Spaces go, case folds,
    the glyph becomes ``a``, and the superscript minus-one becomes ``^-1``;
    what the string MEANS is still decided by the caller's closed sets, so an
    unrecognised unit still refuses rather than guesses.
    """
    folded = unit.replace(" ", "").lower()
    # ONE replace covers BOTH Angstrom codepoints, and that is a property
    # of `.lower()` rather than luck: U+00C5 (LATIN CAPITAL LETTER A WITH
    # RING ABOVE) and U+212B (ANGSTROM SIGN) both lowercase to U+00E5. An
    # explicit second replace for U+212B was added here and measured DEAD --
    # removing it changed no test -- so it is not carried.
    folded = folded.replace("å", "a")
    folded = folded.replace("⁻¹", "^-1")  # superscript minus-one
    # Micrometres are printed with the MICRO SIGN (U+00B5) or, just as often,
    # with a Greek small mu (U+03BC) -- two different codepoints that render
    # identically, so neither the writer nor the reader can tell them apart.
    folded = folded.replace("µ", "u").replace("μ", "u")
    return folded


def _convert(value: float, unit: str, table: dict[str, float], what: str, where: str) -> float:
    """One strict conversion, or a ValueError naming the accepted units."""
    if unit not in table:
        raise ValueError(
            f"ExtractedQuantity from {where!r} has unit {unit!r}, "
            f"not a recognized {what} unit {sorted(table)}; refusing to "
            f"guess a conversion to {what}."
        )
    return value * table[unit]


def to_nm(value: float, unit: str, where: str = "") -> float:
    """*value* in nanometres, or raise. Strict: the unit must be a table key."""
    return _convert(value, unit, LENGTH_TO_NM, "length", where)


def to_ev(value: float, unit: str, where: str = "") -> float:
    """*value* in electronvolts, or raise."""
    return _convert(value, unit, ENERGY_TO_EV, "energy", where)


def to_deg(value: float, unit: str, where: str = "") -> float:
    """*value* in degrees, or raise."""
    return _convert(value, unit, ANGLE_TO_DEG, "angle", where)


def length_in_nm(value: float, unit: str, where: str = "") -> tuple[float | None, str]:
    """A length in nm from a unit spelled however the paper spelled it.

    The tolerant front door: folds the spelling, then converts strictly.

    Returns:
        ``(nm, "")`` or ``(None, reason)``.
    """
    resolved = LENGTH_UNIT_SPELLINGS.get(canonical_unit(unit), unit)
    try:
        return to_nm(value, resolved, where), ""
    except ValueError as exc:
        return None, str(exc)


def q_in_nm_inv(q_value: float, q_unit: str) -> tuple[float | None, str]:
    """One scattering vector magnitude in nm^-1, or the reason it cannot be.

    The single place an inverse unit is interpreted. :func:`q_to_d_nm` is the
    d-spacing view of the same conversion and delegates here, so the two cannot
    disagree about which spellings are legal.

    Args:
        q_value: The magnitude as printed.
        q_unit: The unit as printed, in any spelling `canonical_unit` folds.

    Returns:
        ``(q_nm_inv, "")`` or ``(None, reason)``.
    """
    if q_value <= 0:
        return None, f"q is {q_value}, not positive"
    factor = INVERSE_UNIT_TO_NM_INV.get(canonical_unit(q_unit))
    if factor is None:
        return None, f"q unit {q_unit!r}; expected nm^-1 or A^-1"
    return q_value * factor, ""


def q_to_d_nm(q_value: float, q_unit: str) -> tuple[float | None, str]:
    """A scattering vector magnitude as a real-space d-spacing in nm.

    ``d = 2*pi/q``, for the reciprocal units this corpus actually prints. An
    unrecognised unit refuses with the reason rather than assuming nm^-1.
    """
    q_nm_inv, reason = q_in_nm_inv(q_value, q_unit)
    if q_nm_inv is None:
        return None, reason
    return 2.0 * math.pi / q_nm_inv, ""


# --- dimensions ------------------------------------------------------------
#
# A NAME AND A UNIT CAN CONTRADICT EACH OTHER, AND NOTHING CAUGHT IT. Measured
# on the live graph 2026-09-03: five stored quantities carry a unit that
# contradicts their own dimension -- `lamellar_period = 3.0 nm^-1` (a PERIOD in
# inverse nm), `domain_spacing = 0.0099 A^-1`, `first_peak_q = 1.8 A` (a q in a
# real-space unit), `first_peak_q = 8.3 deg` (a 2-theta, not a q). Every one is
# `epistemic_status: explicit` at confidence 0.9-0.95, i.e. stored as a fact
# read from the paper.
#
# They are almost certainly correct NUMBERS filed under the wrong name -- 3.0
# nm^-1 is a fine first-peak q, 8.3 deg is a fine 2-theta -- which is exactly
# what makes them dangerous: a downstream reader converting "a period" of 3.0
# gets a structure a thousand times too small, from a row that looks
# well-formed and carries high confidence.
#
# The strict tables above refuse at CONVERSION time, so a value nobody converts
# is never checked. This check is about the PAIRING, and it runs at write time.

#: What each extractable quantity IS, dimensionally. Keys are the
#: `extraction.QuantityName` literal; a name missing here is unchecked rather
#: than refused, so adding a quantity name does not silently start rejecting it
#: -- but `tests` pins the two sets equal so the omission is caught in review.
QUANTITY_DIMENSION: dict[str, str] = {
    "grating_pitch": "length", "line_height": "length",
    "line_bottom_width": "length", "line_top_width": "length",
    "lamellar_period": "length",
    "cylinder_center_spacing": "length",
    "d10_spacing": "length",
    "domain_spacing": "length",
    "cylinder_radius": "length",
    "sphere_radius": "length",
    "bcc_lattice_constant": "length",
    "fcc_lattice_constant": "length",
    "film_thickness": "length",
    "first_peak_q": "reciprocal",
}


def unit_dimension(unit: str) -> str:
    """What a unit string measures, or ``unknown``.

    Returns ``length``, ``reciprocal``, ``angle``, ``energy`` or ``unknown``.

    ANGLE AND ENERGY ARE HERE FOR THE GUARD, not for conversion. A stored
    `first_peak_q = 8.3 deg` is a 2-theta filed as a q -- a real row in this
    graph -- and it is only catchable if `deg` is RECOGNISED. Left out, it
    reads as an unknown unit, and unknown units pass the guard by design (see
    `dimension_mismatch`). So the set of units this function knows has to be
    wider than the set it can convert.

    Order matters: ``a-1`` is reciprocal and a sloppy length match would also
    claim it, so reciprocal is tested first.
    """
    folded = canonical_unit(unit)
    if folded in INVERSE_UNIT_TO_NM_INV:
        return "reciprocal"
    if folded in LENGTH_UNIT_SPELLINGS:
        return "length"
    # The strict tables are keyed as printed, not folded, so fold both sides.
    if folded in {canonical_unit(u) for u in ANGLE_TO_DEG}:
        return "angle"
    if folded in {canonical_unit(u) for u in ENERGY_TO_EV}:
        return "energy"
    return "unknown"


def dimension_mismatch(quantity_name: str, unit: str) -> str:
    """Why *unit* cannot belong to *quantity_name*, or ``""`` when it can.

    AN EMPTY STRING IS THE PASS, matching `unreachable_reason` and the rest of
    this repo's refusal helpers, so a caller writes ``if reason: refuse`` and
    cannot mistake a message for success.

    Unknown quantity names and unknown units both PASS. This guard exists to
    catch a contradiction it can prove, not to become a second, weaker copy of
    the unit tables' own refusal -- an unrecognised unit already fails at
    conversion with a better message, and refusing it twice in different words
    would make the same paper unparseable for two different stated reasons.

    Args:
        quantity_name: A name from `extraction.QuantityName`.
        unit: The unit as printed in the paper.

    Returns:
        ``""`` when the pairing is possible, else a sentence naming both.
    """
    expected = QUANTITY_DIMENSION.get(quantity_name)
    if expected is None:
        return ""
    actual = unit_dimension(unit)
    if actual == "unknown" or actual == expected:
        return ""
    return (
        f"{quantity_name} is a {expected} quantity but its unit {unit!r} is "
        f"{actual}. A real-space value and a reciprocal one differ by an "
        f"inversion, not a factor, so this pairing cannot be repaired by "
        f"scaling -- the value is most likely correct and the NAME wrong "
        f"(a q filed as a period, or a 2-theta filed as a q)."
    )


# --- F2W (T06B, H3 F8 gate 3): dimensions beyond the donor quantity names ----

#: Instrument settings a paper may print or a pipeline may assume (F8). Kept apart
#: from ``QUANTITY_DIMENSION``, which tests pin to the donor ``QuantityName`` set.
#: ``beam_center`` is in pixels and has no dimension here, so it is unchecked.
INSTRUMENT_QUANTITY_DIMENSION: dict[str, str] = {
    "beam_energy": "energy",
    "photon_energy": "energy",
    "wavelength": "length",
    "sample_detector_distance": "length",
    "detector_distance": "length",
    "pixel_size": "length",
    "incidence_angle": "angle",
}

#: Peak quantity names produced by the structure strategy (``peak_q[(100)]``).
_PEAK_DIMENSION = {"peak_q": "reciprocal", "peak_d_spacing": "length"}


def base_quantity_name(name: str) -> str:
    """``peak_q[(100)]`` -> ``peak_q``; other names unchanged (stripped)."""
    return (name or "").split("[", 1)[0].strip()


def expected_dimension(name: str) -> Optional[str]:
    """What a quantity name measures (donor names, peak names, instrument settings), or ``None``."""
    base = base_quantity_name(name)
    return QUANTITY_DIMENSION.get(base) or _PEAK_DIMENSION.get(base) or INSTRUMENT_QUANTITY_DIMENSION.get(base)


def quantity_unit_mismatch(name: str, unit: str) -> str:
    """:func:`dimension_mismatch` over :func:`expected_dimension` (F8 gate 3); ``""`` is the pass.

    Same rule as the donor guard: an unknown name or an unknown unit passes, a
    provable contradiction (a length printed in nm^-1, an energy in degrees) does not.
    """
    expected = expected_dimension(name)
    if expected is None or not unit:
        return ""
    actual = unit_dimension(unit)
    if actual == "unknown" or actual == expected:
        return ""
    return f"{base_quantity_name(name)} is a {expected} quantity but its unit {unit!r} is {actual}"


#: Relative tolerance for recognising ``d = 2*pi/q`` against a printed partner (rounding of both numbers).
RECIPROCAL_DERIVATION_REL_TOL = 0.01


def reciprocal_derivation(value: float, unit: str, partner_value: float, partner_unit: str) -> Optional[dict]:
    """Provenance when ``value unit`` equals ``2*pi / (partner_value partner_unit)``, else ``None``.

    Covers d-spacing from q and q from d. Both sides are converted through the
    closed tables above (``nm`` / ``nm^-1``); an unknown unit, a non-positive
    number or a same-dimension pair is ``None``, never a guess.
    """
    mine, theirs = unit_dimension(unit), unit_dimension(partner_unit)
    if {mine, theirs} != {"length", "reciprocal"}:
        return None
    if not (math.isfinite(value) and math.isfinite(partner_value)) or value <= 0 or partner_value <= 0:
        return None
    own = normalize_quantity(value, unit)
    partner = normalize_quantity(partner_value, partner_unit)
    if own.value is None or partner.value is None:
        return None
    computed = 2.0 * math.pi / partner.value
    if not math.isclose(own.value, computed, rel_tol=RECIPROCAL_DERIVATION_REL_TOL):
        return None
    formula = "d = 2*pi/q" if mine == "length" else "q = 2*pi/d"
    return {
        "formula": formula, "from_value": partner_value, "from_unit": partner_unit,
        "computed_value": computed, "computed_unit": own.unit, "stated_value": value, "stated_unit": unit,
        "rel_tol": RECIPROCAL_DERIVATION_REL_TOL, "profile": CONVERSION_PROFILE,
    }


# --- F2W: raw + normalized with conversion provenance ----------------------

#: Version of the conversion tables above; recorded on every normalization.
CONVERSION_PROFILE = "forge-units-v1"

#: Target unit per dimension.
_TARGET_UNIT = {"length": "nm", "reciprocal": "nm^-1", "energy": "eV", "angle": "deg"}


@dataclass(frozen=True)
class NormalizedQuantity:
    """A value as the source printed it, plus its normalized form and how it was derived.

    Attributes:
        raw_value: The number exactly as reported.
        raw_unit: The unit string exactly as reported.
        dimension: ``length``/``reciprocal``/``energy``/``angle``/``unknown``.
        value: Normalized value, or ``None`` when no conversion was possible.
        unit: Normalized unit, or ``""``.
        factor: Multiplicative factor applied (``value = raw_value * factor``).
        profile: Conversion-table profile used.
        reason: Why normalization was refused (empty on success).
    """

    raw_value: float
    raw_unit: str
    dimension: str
    value: Optional[float]
    unit: str
    factor: Optional[float]
    profile: str = CONVERSION_PROFILE
    reason: str = ""

    @property
    def converted(self) -> bool:
        """Whether a normalized value exists."""
        return self.value is not None

    def provenance(self) -> dict:
        """Conversion provenance as a plain dict for evidence records."""
        return {
            "raw_value": self.raw_value, "raw_unit": self.raw_unit, "dimension": self.dimension,
            "factor": self.factor, "target_unit": self.unit or None, "profile": self.profile,
            "refused_reason": self.reason or None,
        }


def normalize_quantity(raw_value: float, raw_unit: str) -> NormalizedQuantity:
    """Normalize one reported value by its unit's dimension, refusing unknown units.

    Args:
        raw_value: Number as printed.
        raw_unit: Unit as printed.

    Returns:
        A :class:`NormalizedQuantity`; ``value`` is ``None`` with a ``reason`` when the
        unit is unknown, the value is not finite, or (for reciprocal units) not positive.
    """
    dimension = unit_dimension(raw_unit) if raw_unit else "unknown"
    if not math.isfinite(raw_value):
        return NormalizedQuantity(raw_value, raw_unit, dimension, None, "", None, reason="value is not finite")
    folded = canonical_unit(raw_unit)
    factor: Optional[float] = None
    if dimension == "length":
        factor = LENGTH_TO_NM[LENGTH_UNIT_SPELLINGS[folded]]
    elif dimension == "reciprocal":
        if raw_value <= 0:
            return NormalizedQuantity(raw_value, raw_unit, dimension, None, "", None,
                                      reason=f"q is {raw_value}, not positive")
        factor = INVERSE_UNIT_TO_NM_INV[folded]
    elif dimension == "energy":
        factor = next(f for u, f in ENERGY_TO_EV.items() if canonical_unit(u) == folded)
    elif dimension == "angle":
        factor = next(f for u, f in ANGLE_TO_DEG.items() if canonical_unit(u) == folded)
    if factor is None:
        return NormalizedQuantity(raw_value, raw_unit, dimension, None, "", None,
                                  reason=f"unit {raw_unit!r} is not in a closed conversion table")
    return NormalizedQuantity(raw_value, raw_unit, dimension, raw_value * factor, _TARGET_UNIT[dimension], factor)

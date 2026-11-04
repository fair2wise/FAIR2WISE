"""Reciprocal-unit normalization and q <-> real-space spacing, with provenance.

Deterministic and table-driven: an unrecognized unit is a refusal
(:class:`ScienceError`), never a guess. The factor-of-ten between inverse
angstrom and inverse nanometre is the error this guards against, so every result
records the input value/unit, the folded unit key and the factor applied.

Reuses the paper-figure unit tables (``vision.kernels.units``), adapted from
als-computing/forge_rsoxs_analysis src/forge/analysis/figure_extraction/units.py (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TYPE_CHECKING, Any

from ..vision.kernels.units import (
    INVERSE_UNIT_TO_NM_INV,
    LENGTH_UNIT_TO_NM,
    fold_inverse_length,
    fold_length,
    normalise_unit,
)

if TYPE_CHECKING:
    from ..contracts.json_types import JsonValue

TWO_PI = 2.0 * math.pi
LENGTH = "length"
INVERSE_LENGTH = "inverse_length"


class ScienceError(ValueError):
    """A refused conversion; ``reason`` is machine-readable."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


@dataclass(frozen=True)
class NormalizedQuantity:
    """A value in canonical units (nm or nm^-1) with how it got there."""

    value: float
    unit: str
    kind: str
    input_value: float
    input_unit: str
    folded_unit: str
    factor: float

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form including provenance."""
        return {
            "value": self.value, "unit": self.unit, "kind": self.kind,
            "provenance": {
                "input_value": self.input_value, "input_unit": self.input_unit,
                "folded_unit": self.folded_unit, "factor": self.factor, "rule": "unit-table-v1",
            },
        }


def _finite_positive(value: JsonValue) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ScienceError("not_a_number", "value is not a number") from None
    if not math.isfinite(number) or number <= 0:
        raise ScienceError("not_positive", "value must be finite and positive")
    return number


def science_normalize_quantity(value: JsonValue, unit: str, kind: str | None = None) -> NormalizedQuantity:
    """Normalize a length to nm or an inverse length to nm^-1.

    Args:
        value: Positive magnitude.
        unit: As written (``"Å^-1"``, ``"nm⁻¹"``, ``"µm"``, ``"m m"``).
        kind: ``"length"`` or ``"inverse_length"`` to require one; inferred when omitted.

    Raises:
        ScienceError: ``unknown_unit``, ``kind_mismatch``, ``not_positive`` or ``not_a_number``.
    """
    number = _finite_positive(value)
    key = normalise_unit(str(unit))
    inferred = INVERSE_LENGTH if key in INVERSE_UNIT_TO_NM_INV else LENGTH if key in LENGTH_UNIT_TO_NM else ""
    if not inferred:
        raise ScienceError("unknown_unit", f"unrecognized unit {unit!r}")
    if kind is not None and kind != inferred:
        raise ScienceError("kind_mismatch", f"unit {unit!r} is {inferred}, not {kind}")
    if inferred == INVERSE_LENGTH:
        folded, out_unit, factor = fold_inverse_length(number, unit), "nm^-1", INVERSE_UNIT_TO_NM_INV[key]
    else:
        folded, out_unit, factor = fold_length(number, unit), "nm", LENGTH_UNIT_TO_NM[key]
    assert folded is not None
    return NormalizedQuantity(folded, out_unit, inferred, number, str(unit), key, factor)


def science_q_to_spacing(q: object, unit: str) -> dict[str, Any]:
    """Real-space spacing ``d = 2 pi / q`` in nm from q in nm^-1 or A^-1 (with provenance)."""
    norm = science_normalize_quantity(q, unit, INVERSE_LENGTH)
    d_nm = TWO_PI / norm.value
    return {"d_nm": d_nm, "d_angstrom": d_nm * 10.0, "q": norm.as_dict(), "formula": "d = 2*pi/q"}


def science_spacing_to_q(d: object, unit: str) -> dict[str, Any]:
    """Inverse of :func:`science_q_to_spacing`: ``q = 2 pi / d``, in nm^-1 and A^-1."""
    norm = science_normalize_quantity(d, unit, LENGTH)
    q_nm = TWO_PI / norm.value
    return {"q_nm_inv": q_nm, "q_angstrom_inv": q_nm / 10.0, "d": norm.as_dict(), "formula": "q = 2*pi/d"}

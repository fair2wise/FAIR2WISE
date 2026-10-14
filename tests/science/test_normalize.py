"""T09 science helpers: reciprocal-unit normalization and q <-> spacing (deterministic)."""

import math

import pytest

from app.modules.science import (
    ScienceError, science_normalize_quantity, science_q_to_spacing, science_spacing_to_q,
)


INVERSE = [("nm^-1", 1.0), ("nm⁻¹", 1.0), ("Å^-1", 10.0), ("Å -1", 10.0), ("A-1", 10.0)]


@pytest.mark.parametrize("unit,factor", INVERSE)
def test_inverse_units_fold_to_nm_inv(unit, factor):
    got = science_normalize_quantity(2.0, unit)
    assert got.unit == "nm^-1" and got.kind == "inverse_length"
    assert got.value == pytest.approx(2.0 * factor)
    assert got.as_dict()["provenance"]["input_unit"] == unit


@pytest.mark.parametrize("unit,factor", [("nm", 1.0), ("µm", 1000.0), ("μm", 1000.0), ("m m", 1e6), ("Å", 0.1)])
def test_length_units_fold_to_nm(unit, factor):
    assert science_normalize_quantity(3.0, unit).value == pytest.approx(3.0 * factor)


def test_unknown_unit_refuses_instead_of_guessing():
    with pytest.raises(ScienceError) as err:
        science_normalize_quantity(1.0, "furlong")
    assert err.value.reason == "unknown_unit"


def test_kind_mismatch_and_bad_values_refuse():
    with pytest.raises(ScienceError) as err:
        science_normalize_quantity(1.0, "nm", "inverse_length")
    assert err.value.reason == "kind_mismatch"
    for bad in (0, -1, float("nan"), float("inf"), "x", None):
        with pytest.raises(ScienceError):
            science_normalize_quantity(bad, "nm")


def test_q_to_spacing_angstrom_vs_nanometre_differ_tenfold():
    nm = science_q_to_spacing(1.0, "nm^-1")
    ang = science_q_to_spacing(1.0, "A^-1")
    assert nm["d_nm"] == pytest.approx(2 * math.pi)
    assert ang["d_nm"] == pytest.approx(2 * math.pi / 10)
    assert nm["d_nm"] / ang["d_nm"] == pytest.approx(10.0)
    assert ang["q"]["provenance"]["factor"] == 10.0


def test_spacing_round_trip():
    d = science_q_to_spacing(0.25, "A^-1")["d_nm"]
    assert science_spacing_to_q(d, "nm")["q_angstrom_inv"] == pytest.approx(0.25)
    with pytest.raises(ScienceError):
        science_spacing_to_q(5.0, "nm^-1")

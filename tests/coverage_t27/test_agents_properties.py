"""Behaviour of the property extractor and normalizer (moved out of the application module)."""

from app.modules.agents.properties import PhysicalPropertyExtractor, PropertyNormalizer


def test_extractor_basic_bandgap() -> None:
    """Extract a band gap value and unit when material and property are adjacent."""
    text = "The Na3Bi film exhibited a band gap of 1.9 eV at room temperature."
    extractor = PhysicalPropertyExtractor()
    entries = extractor.extract(text, ["Na3Bi"])
    assert len(entries) == 1
    e = entries[0]
    assert e["material"] == "Na3Bi"
    assert e["property"] == "band_gap"
    assert e["raw_value"] == "1.9"
    assert e["raw_unit"] == "eV"
    assert e["verified"] is True

def test_extractor_no_value_but_pattern() -> None:
    """Return an unverified entry when the pattern matches but no value is present."""
    text = "Measured_property: band gap of Na3Bi."
    extractor = PhysicalPropertyExtractor()
    entries = extractor.extract(text, ["Na3Bi"])
    assert len(entries) == 1
    e = entries[0]
    assert e["raw_value"] is None
    assert e["verified"] is False

def test_extractor_out_of_proximity() -> None:
    """Reject a property mention that is far from the material."""
    text = (
        "The PL quenching efficiency of PM6: Y6-2O blends is 30.2%, which indicates that "
        "results in a low EQE of the Y6-2O-based devices."
    )
    extractor = PhysicalPropertyExtractor()
    # We only want "efficiency" of "EQE" if truly “EQE efficiency” is mentioned near each other.
    entries = extractor.extract(text, ["EQE"])
    # Should be empty, because “EQE” and “efficiency” are far apart (not proximal/pattern-matched).
    assert len(entries) == 0

def test_extractor_density() -> None:
    """Extract a density value with a compound unit."""
    text = "The density of Na3Bi is 6.7 g/cm3 at 300 K."
    extractor = PhysicalPropertyExtractor()
    entries = extractor.extract(text, ["Na3Bi"])
    assert len(entries) == 1
    e = entries[0]
    assert e["property"] == "density"
    assert e["raw_value"] == "6.7"
    assert e["raw_unit"] == "g/cm3"
    assert e["verified"] is True

def test_extract_from_relations() -> None:
    """Pull only measured_property relations from a term record."""
    term_record = {
        "term": "Na3Bi",
        "relations": [
            {"relation": "measured_property", "related_term": "band gap", "verified": True},
            {"relation": "measured_property", "related_term": "mobility", "verified": False},
            {"relation": "energy_level", "related_term": "Fermi energy", "verified": True},
        ]
    }
    extractor = PhysicalPropertyExtractor()
    raw = extractor.extract_from_relations(term_record)
    assert len(raw) == 2
    props = {r["property"] for r in raw}
    assert "band_gap" in props
    assert "mobility" in props

def test_normalizer_ev_to_ev() -> None:
    """Leave an eV band gap unchanged and flag no conversion failure."""
    entries = [{
        "material": "Na3Bi",
        "property": "band_gap",
        "raw_value": "1.9",
        "raw_unit": "eV",
        "uncertainty": None,
        "context": "",
        "verified": True
    }]
    normalizer = PropertyNormalizer()
    ne = normalizer.normalize(entries)[0]
    assert ne["normalized_value"] == 1.9
    assert ne["normalized_unit"] == "eV"
    assert ne["unit_conversion_failed"] is False

def test_normalizer_density() -> None:
    """Convert density from g/cm3 to kg/m3, scaling the uncertainty too."""
    entries = [{
        "material": "Na3Bi",
        "property": "density",
        "raw_value": "6.7",
        "raw_unit": "g/cm3",
        "uncertainty": "0.1",
        "context": "",
        "verified": True
    }]
    normalizer = PropertyNormalizer()
    ne = normalizer.normalize(entries)[0]
    # 6.7 g/cm3 = 6700 kg/m3
    assert abs(ne["normalized_value"] - 6700.0) < 1e-6
    assert ne["normalized_unit"] == "kg/m3"
    assert abs(ne["uncertainty_value"] - 100.0) < 1e-6

def test_normalizer_efficiency_plausibility() -> None:
    """Flag out-of-range efficiency values as failed conversions."""
    # Efficiency = -20% (invalid)
    entries = [{
        "material": "EQE",
        "property": "efficiency",
        "raw_value": "-20",
        "raw_unit": "%",
        "uncertainty": None,
        "context": "",
        "verified": True
    }]
    normalizer = PropertyNormalizer()
    ne = normalizer.normalize(entries)[0]
    assert ne["unit_conversion_failed"] is True

    # Efficiency = 600% (invalid)
    entries2 = [{
        "material": "EQE",
        "property": "efficiency",
        "raw_value": "600",
        "raw_unit": "%",
        "uncertainty": None,
        "context": "",
        "verified": True
    }]
    ne2 = normalizer.normalize(entries2)[0]
    assert ne2["unit_conversion_failed"] is True

def test_normalizer_bulk_modulus() -> None:
    """Normalize GPa and MPa bulk moduli to GPa."""
    entries = [{
        "material": "Na3Bi",
        "property": "bulk_modulus",
        "raw_value": "200",
        "raw_unit": "GPa",
        "uncertainty": "5",
        "context": "",
        "verified": True
    }, {
        "material": "Na3Bi",
        "property": "bulk_modulus",
        "raw_value": "200000",
        "raw_unit": "MPa",
        "uncertainty": "5000",
        "context": "",
        "verified": True
    }]
    normalizer = PropertyNormalizer()
    ne1, ne2 = normalizer.normalize(entries)
    assert ne1["normalized_unit"] == "GPa" and abs(ne1["normalized_value"] - 200.0) < 1e-6
    assert ne2["normalized_unit"] == "GPa" and abs(ne2["normalized_value"] - 200.0) < 1e-6

def test_normalizer_failed_unit() -> None:
    """Flag unparseable values and units as failed conversions."""
    entries = [{
        "material": "Unknown",
        "property": "band_gap",
        "raw_value": "abc",
        "raw_unit": "xyz",
        "uncertainty": None,
        "context": "",
        "verified": True
    }]
    normalizer = PropertyNormalizer()
    ne = normalizer.normalize(entries)[0]
    assert ne["unit_conversion_failed"]


import pytest


def _norm(prop, value, unit, unc=None):
    entry = {"material": "M", "property": prop, "raw_value": value, "raw_unit": unit,
             "uncertainty": unc, "context": "", "verified": True}
    return PropertyNormalizer().normalize([entry])[0]


@pytest.mark.parametrize(
    ("prop", "value", "unit", "unc", "expected", "expected_unit", "expected_unc"),
    [
        ("melting_point", "25", "°C", "1", 298.15, "K", 1.0),
        ("glass_transition_temperature", "300", "K", None, 300.0, "K", None),
        ("band_gap", "1500", "meV", "10", 1.5, "eV", 0.01),
        ("conductivity", "2", "S/cm", "0.5", 200.0, "S/m", 50.0),
        ("conductivity", "2", "S/m", "0.5", 2.0, "S/m", 0.5),
        ("mobility", "10", "cm2/Vs", "1", 1e-3, "m2/(V*s)", 1e-4),
        ("mobility", "0.1", "m2/Vs", None, 0.1, "m2/(V*s)", None),
        ("efficiency", "12", "%", "1", 12.0, "%", 1.0),
        ("efficiency", "0.85", "", "0.01", 85.0, "%", 1.0),
        ("thermal_conductivity", "1.5", "W/mK", None, 1.5, "W/(m*K)", None),
        ("thermal_conductivity", "0.02", "W/cmK", "0.001", 2.0, "W/(m*K)", 0.1),
        ("dielectric_constant", "11.7", "-", "0.2", 11.7, "", 0.2),
        ("density", "6000", "kg/m3", "1", 6000.0, "kg/m3", 1.0),
        ("formation_energy", "-500", "meV/atom", "5", -0.5, "eV/atom", 0.005),
        ("formation_energy", "-0.5", "eV/atom", None, -0.5, "eV/atom", None),
        ("shear_modulus", "40000", "MPa", "1000", 40.0, "GPa", 1.0),
        ("shear_modulus", "40", "GPa", "1", 40.0, "GPa", 1.0),
        ("magnetic_moment", "2.2", "μB", "0.1", 2.2, "μ_B", 0.1),
        ("energy_above_hull", "30", "meV/atom", "3", 0.03, "eV/atom", 0.003),
        ("energy_above_hull", "0.03", "eV/atom", None, 0.03, "eV/atom", None),
        ("bulk_modulus", "5", "GPa", None, 5.0, "GPa", None),
        ("some_new_property", "3", "furlong", "0.1", 3.0, "furlong", 0.1),
    ],
)
def test_normalizer_converts_every_property_family(prop, value, unit, unc, expected, expected_unit, expected_unc):
    out = _norm(prop, value, unit, unc)
    assert out["unit_conversion_failed"] is False
    assert out["normalized_value"] == pytest.approx(expected)
    assert out["normalized_unit"] == expected_unit
    if expected_unc is None:
        assert out["uncertainty_value"] is None
    else:
        assert out["uncertainty_value"] == pytest.approx(expected_unc)


@pytest.mark.parametrize(
    ("prop", "value", "unit"),
    [
        ("melting_point", "1", "rankine"),
        ("conductivity", "1", "mho"),
        ("mobility", "1", "furlong"),
        ("thermal_conductivity", "1", "btu"),
        ("density", "1", "lb/ft3"),
        ("formation_energy", "1", "kcal"),
        ("bulk_modulus", "1", "psi"),
        ("shear_modulus", "1", "psi"),
        ("magnetic_moment", "1", "gauss"),
        ("energy_above_hull", "1", "kcal"),
        ("band_gap", "-1", "eV"),  # outside plausibility range
        ("density", "-5", "kg/m3"),
    ],
)
def test_normalizer_flags_unknown_units_and_implausible_values(prop, value, unit):
    out = _norm(prop, value, unit)
    assert out["unit_conversion_failed"] is True
    assert out["normalized_value"] is None and out["normalized_unit"] is None


def test_normalizer_skips_numeric_conversion_without_raw_value():
    out = _norm("band_gap", None, None)
    assert out["unit_conversion_failed"] is False and out["normalized_value"] is None


def test_extractor_custom_keywords_and_uncertainty_capture():
    ex = PhysicalPropertyExtractor({"hardness": "hardness"})
    entries = ex.extract("The hardness of TiN was 21 ± 2 GPa. TiN is cubic.", ["TiN", "absent"])
    assert [(e["property"], e["raw_value"], e["uncertainty"], e["raw_unit"]) for e in entries] == [
        ("hardness", "21", "2", "GPa")
    ]
    assert ex.extract("no materials here", ["TiN"]) == []


def test_extract_from_relations_falls_back_to_snake_case_property_and_defaults_unverified():
    out = PhysicalPropertyExtractor().extract_from_relations(
        {"term": " TiN ", "relations": [{"relation": "measured_property", "related_term": "Hall Coefficient"}]}
    )
    assert out == [{"material": "TiN", "property": "hall_coefficient", "raw_value": None, "raw_unit": None,
                    "uncertainty": None, "context": "measured_property:Hall Coefficient", "verified": False}]

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


# --- T27 read-through regressions: B11 (percent), B12 (density > 0), B13 (unit spellings) ---

def _extract_norm(text, material="Na3Bi", prop=None):
    raw = PhysicalPropertyExtractor().extract(text, [material])
    if prop is not None:  # "thermal conductivity" also matches the "conductivity" keyword
        raw = [r for r in raw if r["property"] == prop]
    assert len(raw) == 1, raw
    return raw[0], PropertyNormalizer().normalize(raw)[0]


@pytest.mark.parametrize(
    ("text", "value", "expected"),
    [
        ("The efficiency of Na3Bi was 85%.", "85", 85.0),
        ("The efficiency of Na3Bi was 12.5 %.", "12.5", 12.5),
        ("The efficiency of Na3Bi was 12.5 ± 0.5%.", "12.5", 12.5),
        ("The efficiency of Na3Bi reached 100% in tests.", "100", 100.0),
        ("The efficiency of Na3Bi (85%) was high.", "85", 85.0),
    ],
)
def test_b11_percent_is_extracted_and_normalized(text, value, expected):
    raw, norm = _extract_norm(text)
    assert raw["raw_value"] == value and raw["raw_unit"] == "%"
    assert norm["unit_conversion_failed"] is False
    assert norm["normalized_value"] == pytest.approx(expected)
    assert norm["normalized_unit"] == "%"


def test_b11_percent_uncertainty_end_to_end():
    raw, norm = _extract_norm("The efficiency of Na3Bi was 12.5 ± 0.5%.")
    assert raw["uncertainty"] == "0.5" and norm["uncertainty_value"] == pytest.approx(0.5)


@pytest.mark.parametrize(
    "text",
    [
        "The band gap of Na3Bi is discussed on page 85.",
        "The band gap of Na3Bi follows v8.5 of the code.",
        "The band gap of Na3Bi was reported in 2024-11.",
        "The band gap of Na3Bi is in Table 12.",
    ],
)
def test_b11_plain_numbers_are_not_values(text):
    raw = PhysicalPropertyExtractor().extract(text, ["Na3Bi"])
    assert len(raw) == 1
    assert raw[0]["raw_value"] is None and raw[0]["raw_unit"] is None and raw[0]["verified"] is False


def test_b11_digit_is_never_split_into_value_and_unit():
    raw = PhysicalPropertyExtractor().extract("The density of Na3Bi is 85 on page 5.", ["Na3Bi"])
    assert all(r["raw_unit"] != "5" for r in raw)


def test_b12_density_zero_is_implausible():
    assert _norm("density", "0.0", "g/cm3")["unit_conversion_failed"] is True
    assert _norm("density", "0", "kg/m3")["unit_conversion_failed"] is True
    out = _norm("density", "0.001", "g/cm3")
    assert out["unit_conversion_failed"] is False and out["normalized_value"] == pytest.approx(1.0)
    # end to end
    raw, norm = _extract_norm("The density of Na3Bi was 0.0 g/cm3.")
    assert raw["raw_value"] == "0.0" and norm["unit_conversion_failed"] is True


def test_b12_other_ranges_stay_inclusive():
    assert _norm("band_gap", "0", "eV")["unit_conversion_failed"] is False
    assert _norm("efficiency", "0", "%")["unit_conversion_failed"] is False
    assert _norm("efficiency", "100", "%")["unit_conversion_failed"] is False


@pytest.mark.parametrize(
    ("text", "unit", "prop", "expected", "expected_unit"),
    [
        ("The conductivity of Na3Bi was 10 S·cm⁻¹.", "S·cm⁻¹", "conductivity", 1000.0, "S/m"),
        ("The conductivity of Na3Bi was 10 S·cm-1.", "S·cm-1", "conductivity", 1000.0, "S/m"),
        ("The conductivity of Na3Bi was 10 S·m⁻¹.", "S·m⁻¹", "conductivity", 10.0, "S/m"),
        ("The mobility of Na3Bi was 5 cm²/Vs.", "cm²/Vs", "mobility", 5e-4, "m2/(V*s)"),
        ("The mobility of Na3Bi was 5 cm²/(V·s).", "cm²/(V·s)", "mobility", 5e-4, "m2/(V*s)"),
        ("The mobility of Na3Bi was 5 m²/V·s.", "m²/V·s", "mobility", 5.0, "m2/(V*s)"),
        ("The mobility of Na3Bi was 5 cm2/V*s.", "cm2/V*s", "mobility", 5e-4, "m2/(V*s)"),
        ("The thermal conductivity of Na3Bi was 2 W/(m*K).", "W/(m*K)", "thermal_conductivity", 2.0, "W/(m*K)"),
        ("The thermal conductivity of Na3Bi was 2 W/(m·K).", "W/(m·K)", "thermal_conductivity", 2.0, "W/(m*K)"),
        ("The thermal conductivity of Na3Bi was 2 W·m⁻¹·K⁻¹.", "W·m⁻¹·K⁻¹", "thermal_conductivity", 2.0, "W/(m*K)"),
        ("The thermal conductivity of Na3Bi was 2 W/(cm*K).", "W/(cm*K)", "thermal_conductivity", 200.0, "W/(m*K)"),
        ("The density of Na3Bi was 6 g·cm-3.", "g·cm-3", "density", 6000.0, "kg/m3"),
        ("The density of Na3Bi was 6 g·cm⁻³.", "g·cm⁻³", "density", 6000.0, "kg/m3"),
        ("The density of Na3Bi was 6 g/cm^3.", "g/cm^3", "density", 6000.0, "kg/m3"),
        ("The density of Na3Bi was 6000 kg·m-3.", "kg·m-3", "density", 6000.0, "kg/m3"),
        ("The magnetic moment of Na3Bi was 2 μB.", "μB", "magnetic_moment", 2.0, "μ_B"),
        ("The magnetic moment of Na3Bi was 2 µB.", "µB", "magnetic_moment", 2.0, "μ_B"),
    ],
)
def test_b13_unit_spellings_reach_normalize_from_extract(text, unit, prop, expected, expected_unit):
    raw, norm = _extract_norm(text, prop=prop)
    assert raw["raw_unit"] == unit
    assert norm["unit_conversion_failed"] is False, norm
    assert norm["normalized_value"] == pytest.approx(expected)
    assert norm["normalized_unit"] == expected_unit


def test_b13_unit_examples_from_other_properties():
    ex = PhysicalPropertyExtractor({"wavenumber": "wavenumber", "area": "area", "enthalpy": "enthalpy"})
    cases = {
        "The wavenumber of Na3Bi was 10 cm⁻¹.": "cm⁻¹",
        "The area of Na3Bi was 5 m²/g.": "m²/g",
        "The enthalpy of Na3Bi was 3 J·mol(-1).": "J·mol(-1)",
    }
    for text, unit in cases.items():
        (raw,) = ex.extract(text, ["Na3Bi"])
        assert raw["raw_unit"] == unit, text
        norm = PropertyNormalizer().normalize([raw])[0]
        assert norm["normalized_unit"] == unit  # unknown property: raw unit passes through


def test_b13_enclosing_parentheses_are_not_part_of_the_unit():
    raw, _ = _extract_norm("The band gap of Na3Bi (1.9 eV) is wide.")
    assert raw["raw_unit"] == "eV"


# --- T27 props2: efficiency fraction only without unit / explicit marker; bare words are not units ---

@pytest.mark.parametrize("unit", ["was", "and", "eV", "K", "fraction-ish"])
def test_efficiency_non_percent_unit_is_never_a_silent_x100(unit):
    out = _norm("efficiency", "0.85", unit)
    assert out["unit_conversion_failed"] is True and out["normalized_value"] is None


@pytest.mark.parametrize("unit", ["", "fraction", "Fraction"])
def test_efficiency_fraction_markers_still_scale(unit):
    out = _norm("efficiency", "0.85", unit)
    assert out["unit_conversion_failed"] is False and out["normalized_value"] == pytest.approx(85.0)


def test_efficiency_was_is_unverified_end_to_end():
    (raw,) = PhysicalPropertyExtractor().extract("The efficiency of Na3Bi 0.85 was good.", ["Na3Bi"])
    assert raw["raw_unit"] != "was" and raw["verified"] is False and raw["raw_value"] is None
    (r2,) = PhysicalPropertyExtractor().extract("The efficiency of Na3Bi was 0.85 in tests.", ["Na3Bi"])
    assert r2["verified"] is False and PropertyNormalizer().normalize([r2])[0]["normalized_value"] is None


@pytest.mark.parametrize("text", [
    "The band gap of Na3Bi is 85 and rising.",
    "The efficiency of Na3Bi is 85 and rising.",
    "The band gap of Na3Bi is 1.9 about right.",
])
def test_bare_word_after_number_is_not_a_unit(text):
    raw = PhysicalPropertyExtractor().extract(text, ["Na3Bi"])
    assert len(raw) == 1 and raw[0]["raw_unit"] is None and raw[0]["verified"] is False


def test_known_word_units_and_percent_still_extract():
    for text, unit in [("The band gap of Na3Bi is 1.9 eV.", "eV"), ("The efficiency of Na3Bi is 85%.", "%"),
                       ("The efficiency of Na3Bi was 12.5 %.", "%"), ("The melting point of Na3Bi was 300 K.", "K")]:
        r = PhysicalPropertyExtractor().extract(text, ["Na3Bi"])
        assert any(x["raw_unit"] == unit and x["verified"] for x in r), text

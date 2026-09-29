"""LinkML tests for the X-ray fundamentals overlay.

Run with::

    python -m pytest tests/test_xray_schema.py -x -q
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
XRAY_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema" / "xray_schema.yaml"
XRAY_EXTRACT_PATH = PROJECT_ROOT / "storage" / "schema" / "xray_schema_extract.yaml"
XRAY_SEED_PATH = PROJECT_ROOT / "storage" / "schema" / "xray_seed_instances.yaml"

REQUIRED_CLASSES = {
    "XRayPhenomenon",
    "XRaySource",
    "XRayBeamProperty",
    "XRayOpticalConstant",
    "AbsorptionEdge",
    "SecondaryProcess",
    "MaterialXRayInteraction",
    "XRaySafetyConcept",
    "CosmicXRayProcess",
    "ScatteringGeometry",
    "XRayTechniqueFamily",
    "InteractionCrossSection",
    "ReciprocalSpaceConcept",
    "WaveOpticsConcept",
    "AtomicTransition",
    "DetectionPrinciple",
}

REQUIRED_SLOTS = {
    "typical_energy_range_eV",
    "energy_band",
    "interaction_channel",
    "fluorescence_yield",
    "cross_section_form",
    "dose_quantity",
    "cosmic_mechanism",
    "selection_rule",
    "photon_statistic",
    "same_physics_as",
    "governs",
    "uses",
}

REQUIRED_COVERAGE = {
    "production",
    "atomic_spectroscopy",
    "channels",
    "optics",
    "q_space",
    "dose",
    "detection_statistics",
    "cosmic",
}


@pytest.fixture(scope="module")
def xray_schema() -> dict:
    return yaml.safe_load(XRAY_SCHEMA_PATH.read_text())


@pytest.fixture(scope="module")
def xray_extract_schema() -> dict:
    return yaml.safe_load(XRAY_EXTRACT_PATH.read_text())


@pytest.fixture(scope="module")
def xray_seeds() -> dict:
    return yaml.safe_load(XRAY_SEED_PATH.read_text())


def test_xray_schema_loads_with_linkml():
    from linkml_runtime.utils.schemaview import SchemaView

    view = SchemaView(str(XRAY_SCHEMA_PATH))
    names = set(view.all_classes())
    missing = REQUIRED_CLASSES - names
    assert not missing, f"Missing classes: {missing}"


def test_xray_extract_schema_loads_with_linkml():
    from linkml_runtime.utils.schemaview import SchemaView

    view = SchemaView(str(XRAY_EXTRACT_PATH))
    names = set(view.all_classes())
    missing = REQUIRED_CLASSES - names
    assert not missing, f"Extract schema missing classes: {missing}"


def test_no_prov_vocabulary_top_level(xray_schema, xray_extract_schema):
    assert "prov_vocabulary" not in xray_schema
    assert "prov_vocabulary" not in xray_extract_schema


def test_does_not_redefine_rsoxs_measurement(xray_schema):
    assert "RSoXSMeasurement" not in (xray_schema.get("classes") or {})


def test_does_not_add_ops_or_sample_classes(xray_schema):
    classes = set((xray_schema.get("classes") or {}).keys())
    banned = {
        "Motor",
        "ProcessVariable",
        "ESAF",
        "OphydDevice",
        "ConjugatedPolymer",
        "PhotovoltaicCell",
        "OFET",
    }
    assert not (classes & banned)


def test_extract_schema_imports_overlay(xray_extract_schema):
    imports = xray_extract_schema.get("imports") or []
    assert "xray_schema" in imports
    assert "prov_vocabulary" not in xray_extract_schema


def test_required_slots_present(xray_schema):
    slots = set((xray_schema.get("slots") or {}).keys())
    missing = REQUIRED_SLOTS - slots
    assert not missing, f"Missing slots: {missing}"


def test_imports_matkg_not_a_fork(xray_schema):
    assert "matkg_schema" in (xray_schema.get("imports") or [])
    assert xray_schema.get("id") == "https://w3id.org/matkg/xray"


def test_seed_instances_cover_principle_areas(xray_seeds, xray_schema):
    coverage = set(xray_seeds.get("coverage") or [])
    missing_areas = REQUIRED_COVERAGE - coverage
    assert not missing_areas, f"Seed coverage list missing: {missing_areas}"

    seen_areas: set[str] = set()
    schema_classes = set((xray_schema.get("classes") or {}).keys())
    for class_name, rows in (xray_seeds.get("instances") or {}).items():
        assert class_name in schema_classes, f"Seed class not in schema: {class_name}"
        for row in rows or []:
            seen_areas.add(str(row.get("coverage") or ""))
    missing_seeded = REQUIRED_COVERAGE - seen_areas
    assert not missing_seeded, f"No seed instance for: {missing_seeded}"


def test_schema_class_count_in_target_band(xray_schema):
    n = len(xray_schema.get("classes") or {})
    assert 16 <= n <= 20, f"Expected 16–20 xray classes, got {n}"

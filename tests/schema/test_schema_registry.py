"""T22B registry: discovery in place, keys, known-broken asset, hierarchy preserved (D20), CWD independence."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.modules.schema import loading
from app.modules.schema.loading import (
    closure_fingerprint,
    load_helper,
    load_schema_view,
    load_yaml_asset,
    probe_known_issue,
)
from app.modules.schema.registry import (
    CLASS_BASE,
    CLASS_OVERLAY,
    CLASS_OVERLAY_EXTRACT,
    RegistryError,
    SchemaRegistry,
)

REPO = Path(__file__).resolve().parents[2]
INVENTORY = json.loads((REPO / "docs/migration/schema_inventory.json").read_text(encoding="utf-8"))


def test_every_inventoried_yaml_asset_is_registered_in_place_with_unchanged_bytes(registry):
    inventoried = {a["file"]: a for a in INVENTORY["assets"] if a["file"].endswith(".yaml")}
    assert {e.path for e in registry} == set(inventoried)
    for entry in registry:
        asset = inventoried[entry.path]
        assert entry.sha256 == asset["sha256"], f"{entry.path} differs from the T22A inventory"
        assert entry.size_bytes == asset["bytes"]
        assert entry.abs_path.parent == REPO / "storage" / "schema"  # discovered, never moved


def test_classification_and_selectability(registry):
    assert registry.get("matkg_schema").classification == CLASS_BASE
    for sid in ("rsoxs_schema", "bl1101_schema", "xray_schema"):
        assert registry.get(sid).classification == CLASS_OVERLAY
    for sid in ("rsoxs_schema_extract", "xray_schema_extract"):
        assert registry.get(sid).classification == CLASS_OVERLAY_EXTRACT
    for sid in ("prov_vocabulary", "bl1101_motor_stage_map", "tiled_sim_seed", "xray_seed_instances"):
        entry = registry.get(sid)
        assert not entry.is_linkml and entry.selectable_for == ()
    assert registry.get("rsoxs_schema").declared_imports == ("matkg_schema",)
    assert registry.get("matkg_schema").declared_imports == ("linkml:types",)


def test_rsoxs_and_extract_overlay_are_distinct_registry_keys(registry):
    a, b = registry.get("rsoxs_schema"), registry.get("rsoxs_schema_extract")
    assert (a.linkml_name, a.version) == (b.linkml_name, b.version) == ("rsoxs", "rsoxs_v1")
    assert a.key != b.key and a.sha256 != b.sha256
    assert registry.id_collisions() == {"rsoxs": ["rsoxs_schema", "rsoxs_schema_extract"]}
    assert closure_fingerprint(a) != closure_fingerprint(b)
    assert registry.by_fingerprint(a.sha256) == [a]


def test_extract_overlay_is_known_broken_reported_not_repaired(registry):
    entry = registry.get("rsoxs_schema_extract")
    assert entry.known_broken and entry.selectable_for == () and entry.known_issues
    with pytest.raises(RegistryError, match="known-broken"):
        registry.select("rsoxs_schema_extract")
    with pytest.raises(RegistryError, match="known-broken"):
        load_helper(entry)
    with pytest.raises(RegistryError, match="known-broken"):
        load_schema_view(entry)
    # the defect is re-observed from the unchanged file (T22A F1)
    assert "photon_energy_eV" in probe_known_issue(entry)
    assert probe_known_issue(registry.get("rsoxs_schema")) is None


def test_non_linkml_assets_are_refused_by_the_linkml_loader(registry):
    for sid in ("prov_vocabulary", "bl1101_motor_stage_map", "tiled_sim_seed", "xray_seed_instances"):
        with pytest.raises(RegistryError, match="not a LinkML"):
            load_schema_view(registry.get(sid))
    assert load_yaml_asset(registry.get("bl1101_motor_stage_map"))["version"] == 4
    with pytest.raises(RegistryError):
        load_yaml_asset(registry.get("matkg_schema"))
    with pytest.raises(RegistryError, match="not selectable"):
        registry.select("bl1101_schema", "extraction")


def test_unregistered_paths_stay_unregistered(registry, tmp_path):
    assert registry.resolve("") is None
    assert registry.resolve("storage/schema/nope.yaml") is None
    assert registry.resolve("storage/schema/rsoxs_schema.yaml") is registry.get("rsoxs_schema")
    assert registry.resolve("rsoxs_schema.yaml") is registry.get("rsoxs_schema")
    fake = tmp_path / "storage" / "schema" / "rsoxs_schema.yaml"
    fake.parent.mkdir(parents=True)
    fake.write_text("id: x\nname: x\nclasses: {}\n", encoding="utf-8")
    assert registry.resolve(str(fake)) is None  # same file name, different bytes
    with pytest.raises(RegistryError, match="not registered"):
        registry.get("nope")


def test_rsoxs_measurement_is_still_an_experimental_technique(registry):
    """D20: no measurement/technique split; ancestors and induced slots match the T22A inventory."""
    view = load_schema_view(registry.get("rsoxs_schema"))
    assert view.class_ancestors("RSoXSMeasurement") == ["RSoXSMeasurement", "ExperimentalTechnique", "Thing"]
    assert view.get_class("RSoXSMeasurement").is_a == "ExperimentalTechnique"
    assert view.get_class("ExperimentalTechnique").is_a == "Thing"
    induced = [s.name for s in view.class_induced_slots("RSoXSMeasurement")]
    assert induced == INVENTORY["rsoxs_measurement_inheritance"]["induced_slots"]
    assert "ExperimentalTechnique" in view.class_ancestors("RSoXSMeasurement")
    base = load_schema_view(registry.get("matkg_schema"))
    assert "RSoXSMeasurement" not in base.all_classes()


def test_inheritance_is_preserved_for_every_registered_overlay(registry):
    expect = {"bl1101_schema": ("Detector", "Device"),
              "xray_schema": ("ScatteringGeometry", "ExperimentalTechnique")}
    assert load_schema_view(registry.get("bl1101_schema")).get_class("ScatteringGeometry").is_a == "Structure"
    for sid, (cls, parent) in expect.items():
        assert load_schema_view(registry.get(sid)).get_class(cls).is_a == parent


def test_resolution_and_fingerprint_do_not_depend_on_the_working_directory(registry, tmp_path, monkeypatch):
    ids = ("rsoxs_schema", "xray_schema_extract")
    here = {s: closure_fingerprint(registry.get(s)) for s in ids}
    monkeypatch.chdir(tmp_path)
    assert Path.cwd() != REPO
    other = SchemaRegistry.discover()
    assert other.schema_dir == REPO / "storage" / "schema"
    assert {s: closure_fingerprint(other.get(s)) for s in here} == here
    helper = load_helper(other.get("rsoxs_schema"))
    assert "RSoXSMeasurement" in helper.classes
    # SchemaHelper's own default path also resolves from here (T04 resource root)
    from app.modules.term_extractor.schema import SchemaHelper

    assert "Thing" in SchemaHelper("storage/schema/matkg_schema.yaml").classes


def test_discovery_does_not_write_into_the_schema_directory(tmp_path):
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in (REPO / "storage/schema").glob("*")}
    reg = SchemaRegistry.discover()
    for entry in reg:
        if entry.is_linkml and not entry.known_broken:
            closure_fingerprint(entry)
    after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in (REPO / "storage/schema").glob("*")}
    assert before == after


def test_loading_module_constructs_the_existing_helper_classes(registry):
    from app.modules.term_extractor.schema import SchemaHelper

    helper = load_helper(registry.get("matkg_schema"))
    assert type(helper) is SchemaHelper
    assert os.path.isabs(helper.schema_path)
    assert loading.NORMALIZER_TERM_EXTRACTOR == "term_extractor.schema.SchemaHelper"

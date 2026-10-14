"""T22B fingerprints: stable across runs, change when any import changes, bound into artifacts."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.modules.schema import loading
from app.modules.schema.loading import closure_files, closure_fingerprint
from app.modules.schema.registry import SchemaRegistry
from app.modules.schema.validation import (
    SchemaBindingError,
    check_binding,
    registered_fingerprints,
)

REPO = Path(__file__).resolve().parents[2]
LINKML = ("matkg_schema", "rsoxs_schema", "bl1101_schema", "xray_schema", "xray_schema_extract")


@pytest.fixture()
def copy_dir(tmp_path):
    dest = tmp_path / "copy" / "storage" / "schema"
    shutil.copytree(REPO / "storage" / "schema", dest)
    return dest


def test_fingerprint_is_stable_and_hex(registry):
    entry = registry.get("rsoxs_schema")
    first = closure_fingerprint(entry)
    again = closure_fingerprint(SchemaRegistry.discover().get("rsoxs_schema"))
    assert first == closure_fingerprint(entry) == again
    assert len(first) == 64 and int(first, 16) >= 0


def test_fingerprint_is_stable_in_a_fresh_interpreter_from_another_cwd(registry, tmp_path):
    code = (
        "from app.modules.schema.registry import SchemaRegistry\n"
        "from app.modules.schema.loading import closure_fingerprint\n"
        "r = SchemaRegistry.discover()\n"
        "print(','.join(closure_fingerprint(r.get(s)) for s in ('matkg_schema', 'rsoxs_schema')))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(REPO), "PYTHONHASHSEED": "7"},
    ).stdout.strip().splitlines()[-1]
    assert out.split(",") == [closure_fingerprint(registry.get(s)) for s in ("matkg_schema", "rsoxs_schema")]


def test_fingerprint_does_not_depend_on_where_the_schemas_live(registry, copy_dir):
    moved = SchemaRegistry.discover(copy_dir)
    for sid in LINKML:
        assert closure_fingerprint(moved.get(sid)) == closure_fingerprint(registry.get(sid))


def test_closure_covers_the_resolved_imports(registry):
    names = [n for n, _ in closure_files(registry.get("rsoxs_schema"))]
    assert names == ["matkg_schema.yaml", "rsoxs_schema.yaml"]
    assert [n for n, _ in closure_files(registry.get("matkg_schema"))] == ["matkg_schema.yaml"]
    assert [n for n, _ in closure_files(registry.get("xray_schema_extract"))] == [
        "matkg_schema.yaml", "xray_schema.yaml", "xray_schema_extract.yaml"]


@pytest.mark.parametrize("changed", ["matkg_schema", "xray_schema", "rsoxs_schema"])
def test_any_import_byte_change_changes_dependent_fingerprints(registry, copy_dir, changed):
    before = {sid: closure_fingerprint(registry.get(sid)) for sid in LINKML}
    target = copy_dir / f"{changed}.yaml"
    target.write_bytes(target.read_bytes() + b"\n# one more comment\n")
    after_registry = SchemaRegistry.discover(copy_dir)
    after = {sid: closure_fingerprint(after_registry.get(sid)) for sid in LINKML}
    closure_of = {sid: {n[:-5] for n, _ in closure_files(registry.get(sid))} for sid in LINKML}
    for sid in LINKML:
        if changed in closure_of[sid]:
            assert after[sid] != before[sid], f"{sid} must change when {changed} changes"
        else:
            assert after[sid] == before[sid], f"{sid} must not change when {changed} changes"


def test_registry_version_is_part_of_the_fingerprint(registry, monkeypatch):
    entry = registry.get("matkg_schema")
    before = closure_fingerprint(entry)
    monkeypatch.setattr(loading, "REGISTRY_VERSION", "2")
    assert closure_fingerprint(entry) != before


def test_linkml_runtime_version_is_part_of_the_fingerprint(registry, monkeypatch):
    entry = registry.get("matkg_schema")
    before = closure_fingerprint(entry)
    monkeypatch.setattr(loading, "linkml_runtime_version", lambda: "0.0.0")
    assert closure_fingerprint(entry) != before


def test_fingerprints_of_registered_assets_are_all_different(registry):
    fps = registered_fingerprints(registry)
    assert set(fps.values()) == {"matkg_schema", "rsoxs_schema", "bl1101_schema", "xray_schema",
                                 "xray_schema_extract"}  # the known-broken extract overlay is not selectable


def test_binding_verified_legacy_stale_and_unknown(registry):
    rsoxs = registry.get("rsoxs_schema")
    fp = closure_fingerprint(rsoxs)
    assert check_binding(registry, rsoxs, fp) == "verified"
    assert check_binding(registry, rsoxs, None) == "legacy_unverified"
    other = closure_fingerprint(registry.get("bl1101_schema"))
    with pytest.raises(SchemaBindingError, match="bound to registered schema 'bl1101_schema'"):
        check_binding(registry, rsoxs, other)
    with pytest.raises(SchemaBindingError, match="not registered"):
        check_binding(registry, rsoxs, "ab" * 32)

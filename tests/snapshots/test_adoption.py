"""Adoption of legacy flat KG files and release descriptors (T26A, owner decision O-R1 Q2).

Fixtures are built under ``tmp_path`` from a graph that ``create_snapshot`` froze from the core_dict fixture; the
flat copy plays the legacy file. Git is used only inside temporary repositories (descriptor tests).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

from app.modules.graph import adoption
from app.modules.graph.adoption import AdoptionRefused, adopt_legacy_directory, adopt_legacy_flat_file
from app.modules.graph.releases import sha256_bytes, verify_snapshot_dir
from app.modules.graph.snapshot import SnapshotExistsError, SnapshotRequest, create_snapshot
from app.modules.graph.snapshot_manifest import parse_manifest
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS, REPO

MANIFEST_SCHEMA = json.loads((REPO / "docs/migration/data_releases/schemas/kg_snapshot_manifest.schema.json").read_text())
NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)
GIT_ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "owner", "GIT_AUTHOR_EMAIL": "o@example.org",
           "GIT_COMMITTER_NAME": "owner", "GIT_COMMITTER_EMAIL": "o@example.org", "LC_ALL": "C"}


@pytest.fixture(scope="module")
def legacy_bytes(tmp_path_factory, registry_module) -> bytes:
    base = tmp_path_factory.mktemp("legacy-source")
    kg = base / "kg"
    kg.mkdir()
    terms = base / "core_dict.terms.json"
    shutil.copy2(INPUTS / "core_dict.terms.json", terms)
    result = create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=terms, storage_kg=kg,
                                             terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT,
                                             registry=registry_module))
    assert result.validation_status == "pass"
    return result.graph_path.read_bytes()


@pytest.fixture(scope="module")
def registry_module():
    from app.modules.schema.registry import SchemaRegistry
    return SchemaRegistry.discover()


def _flat(kg: Path, name: str, data: bytes) -> Path:
    kg.mkdir(parents=True, exist_ok=True)
    path = kg / name
    path.write_bytes(data)
    return path


def _adopt(flat: Path, registry, **kw):
    kw.setdefault("code_commit", COMMIT)
    kw.setdefault("now", NOW)
    return adopt_legacy_flat_file(flat, registry=registry, **kw)


def _graph_with(legacy_bytes: bytes, **node_fields) -> bytes:
    doc = json.loads(legacy_bytes)
    doc["things"][0].update(node_fields)
    return json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")


def test_adopts_standard_name_byte_identical_with_legacy_adoption_manifest(tmp_path, legacy_bytes, registry_module):
    kg = tmp_path / "kg"
    flat = _flat(kg, "matkg_legacyfix_v2.json", legacy_bytes)
    out = _adopt(flat, registry_module)
    assert (out.status, out.corpus, out.snapshot_id) == ("adopted", "legacyfix", "v2")
    nested = kg / "legacyfix" / "v2"
    assert sorted(p.name for p in nested.iterdir()) == ["graph.json", "manifest.json", "validation.json"]
    graph = nested / "graph.json"
    assert graph.read_bytes() == legacy_bytes == flat.read_bytes()
    assert sha256_bytes(graph.read_bytes()) == sha256_bytes(flat.read_bytes())
    assert not graph.is_symlink() and not flat.is_symlink() and graph.stat().st_ino != flat.stat().st_ino
    document = json.loads((nested / "manifest.json").read_bytes())
    jsonschema.validate(document, MANIFEST_SCHEMA)
    manifest = parse_manifest((nested / "manifest.json").read_bytes())
    assert manifest.parent.relation == "legacy_adoption" and manifest.parent.snapshot_id == "matkg_legacyfix_v2"
    assert manifest.parent.graph_sha256 == manifest.graph.sha256 == sha256_bytes(legacy_bytes)
    assert [r.model_dump(exclude_none=True) for r in manifest.extraction.revisions] == [
        {"extractor": "unknown", "version": "unknown", "unknown": True}]  # no invented model
    assert manifest.inputs == [] and manifest.corpus_revision.source_count == 0
    assert manifest.created.code_commit == COMMIT and manifest.created.at == "2026-10-30T12:00:00Z"
    assert manifest.graph_id == "legacyfix_v2" and manifest.disclosure.status == "pass"
    assert [c.path for c in manifest.compat_paths] == ["storage/kg/matkg_legacyfix_v2.json"]
    assert manifest.schema_block.name == "matkg_schema" and manifest.schema_block.import_fingerprint
    verified = verify_snapshot_dir(nested)
    assert verified.graph_sha256 == sha256_bytes(legacy_bytes)
    assert sha256_bytes((nested / "validation.json").read_bytes()) == manifest.validation.report_sha256


def test_is_deterministic_and_leaves_the_flat_file_alone(tmp_path, legacy_bytes, registry_module):
    runs = []
    for name in ("one", "two"):
        kg = tmp_path / name / "kg"
        flat = _flat(kg, "matkg_legacyfix_v1.json", legacy_bytes)
        before = flat.stat()
        _adopt(flat, registry_module)
        assert flat.read_bytes() == legacy_bytes and flat.stat().st_mtime_ns == before.st_mtime_ns
        runs.append({n: (kg / "legacyfix" / "v1" / n).read_bytes() for n in
                     ("graph.json", "manifest.json", "validation.json")})
    assert runs[0] == runs[1]


def test_non_standard_name_gets_a_slug_corpus_v1_without_a_compat_entry(tmp_path, legacy_bytes, registry_module):
    kg = tmp_path / "kg"
    flat = _flat(kg, "matkg_Qwen3_235b_100_20251004.json", legacy_bytes)
    out = _adopt(flat, registry_module)
    assert (out.corpus, out.snapshot_id) == ("qwen3_235b_100_20251004", "v1")
    manifest = parse_manifest((kg / out.corpus / "v1" / "manifest.json").read_bytes())
    assert manifest.compat_paths is None  # T26B R1 admits only matkg_<corpus>_v<N>.json flat names
    assert manifest.graph_id == "Qwen3_235b_100_20251004"  # exactly what readers derive from the flat name
    assert manifest.parent.snapshot_id == "matkg_Qwen3_235b_100_20251004"


def test_dry_run_writes_nothing(tmp_path, legacy_bytes, registry_module):
    kg = tmp_path / "kg"
    flat = _flat(kg, "matkg_legacyfix_v1.json", legacy_bytes)
    assert _adopt(flat, registry_module, dry_run=True).status == "would_adopt"
    assert sorted(p.name for p in kg.iterdir()) == ["matkg_legacyfix_v1.json"]


def test_refuses_r3_and_writes_nothing(tmp_path, legacy_bytes, registry_module):
    kg = tmp_path / "kg"
    flat = _flat(kg, "matkg_legacyfix_v1.json",
                 _graph_with(legacy_bytes, description="use api_key=abcd1234efgh5678 here"))
    with pytest.raises(AdoptionRefused) as info:
        _adopt(flat, registry_module)
    assert info.value.rule == "R3" and info.value.findings == {"R3": 1}
    assert sorted(p.name for p in kg.iterdir()) == ["matkg_legacyfix_v1.json"]


def test_refuses_r4_and_r5_and_never_allowlists(tmp_path, legacy_bytes, registry_module):
    kg = tmp_path / "kg"
    flat = _flat(kg, "matkg_legacyfix_v1.json", _graph_with(
        legacy_bytes, description="see /Users/someone/data and https://x.example/a?token=abc"))
    with pytest.raises(AdoptionRefused) as info:
        _adopt(flat, registry_module)
    assert info.value.rule == "R4,R5"
    assert not (kg / "legacyfix").exists()


def test_refuses_r7_body_field(tmp_path, legacy_bytes, registry_module):
    flat = _flat(tmp_path / "kg", "matkg_legacyfix_v1.json", _graph_with(legacy_bytes, full_text="whole page body"))
    with pytest.raises(AdoptionRefused) as info:
        _adopt(flat, registry_module)
    assert "R7" in info.value.rule


def test_refuses_an_existing_frozen_directory_and_never_modifies_it(tmp_path, legacy_bytes, registry_module):
    kg = tmp_path / "kg"
    flat = _flat(kg, "matkg_legacyfix_v1.json", legacy_bytes)
    _adopt(flat, registry_module)
    nested = kg / "legacyfix" / "v1"
    frozen = {p.name: p.read_bytes() for p in nested.iterdir()}
    with pytest.raises(SnapshotExistsError):
        _adopt(flat, registry_module, now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert {p.name: p.read_bytes() for p in nested.iterdir()} == frozen
    assert [o.rule for o in adopt_legacy_directory(kg, registry=registry_module, code_commit=COMMIT)] == [
        "target-exists"]


def test_refuses_a_symlinked_flat_file_and_a_symlinked_target(tmp_path, legacy_bytes, registry_module):
    kg = tmp_path / "kg"
    real = _flat(tmp_path / "elsewhere", "real.json", legacy_bytes)
    kg.mkdir()
    link = kg / "matkg_legacyfix_v1.json"
    link.symlink_to(real)
    with pytest.raises(AdoptionRefused) as info:
        _adopt(link, registry_module)
    assert info.value.rule == "R1"
    link.unlink()
    flat = _flat(kg, "matkg_legacyfix_v1.json", legacy_bytes)
    (kg / "legacyfix").mkdir()
    (kg / "legacyfix" / "v1").symlink_to(tmp_path)
    with pytest.raises(SnapshotExistsError):
        _adopt(flat, registry_module)


def test_listing_skips_with_exact_rules(tmp_path, legacy_bytes, registry_module):
    kg = tmp_path / "kg"
    _flat(kg, "matkg_good_v1.json", legacy_bytes)
    _flat(kg, "matkg_empty_v1.json", b'{\n  "things": [],\n  "associations": []\n}')
    _flat(kg, "matkg_nodes_edges_v3.json", json.dumps({"nodes": [{"id": "a"}], "edges": []}, indent=2).encode())
    _flat(kg, "matkg_newline_v1.json", legacy_bytes + b"\n")
    _flat(kg, "matkg_good_v1.json.bak", legacy_bytes)
    _flat(kg, "view_kg.html", b"<html></html>")
    got = {o.flat_name: (o.status, o.rule) for o in adopt_legacy_directory(
        kg, registry=registry_module, code_commit=COMMIT, now=NOW)}
    assert got == {
        "matkg_empty_v1.json": ("skipped", "placeholder"),
        "matkg_good_v1.json": ("adopted", None),
        "matkg_good_v1.json.bak": ("skipped", "R1"),
        "matkg_newline_v1.json": ("skipped", "R2-format"),
        "matkg_nodes_edges_v3.json": ("skipped", "R2-format"),
        "view_kg.html": ("skipped", "R1"),
    }
    assert (kg / "good" / "v1" / "graph.json").is_file() and not (kg / "empty").exists()


# --------------------------------------------------------------------------- descriptors


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=GIT_ENV, check=True,
                          stdin=subprocess.DEVNULL)
    return done.stdout.strip()


@pytest.fixture()
def committed(tmp_path, legacy_bytes, registry_module):
    """A temporary repo with the schema, a legacy flat file and its adopted nested directory committed."""
    repo = tmp_path / "repo"
    (repo / "storage" / "kg").mkdir(parents=True)
    shutil.copytree(REPO / "storage" / "schema", repo / "storage" / "schema")
    _git(repo, "init", "-q", "-b", "main")
    kg = repo / "storage" / "kg"
    _flat(kg, "matkg_legacyfix_v1.json", legacy_bytes)
    _flat(kg, "matkg_old_run_20250101.json", legacy_bytes)
    adopt_legacy_directory(kg, registry=registry_module, code_commit=COMMIT, now=NOW)
    _git(repo, "add", "-f", "--", "storage")
    _git(repo, "commit", "-q", "-m", "adopt")
    return repo, _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("corpus,flat_role_path", [
    ("legacyfix", None),
    ("old_run_20250101", "storage/kg/matkg_old_run_20250101.json"),
])
def test_descriptor_is_schema_valid_and_pins_committed_bytes(committed, corpus, flat_role_path):
    repo, commit = committed
    descriptor = adoption.build_adoption_descriptor(
        repo, commit, corpus, "v1", repository="github.com/example/fixture", importer_name="json_to_tiled",
        importer_version="1")
    schema = json.loads((REPO / "docs/migration/data_releases/schemas/data_release_descriptor.schema.json").read_text())
    jsonschema.validate(descriptor, schema)
    assert adoption.validate_descriptor(descriptor) == []
    assert descriptor["state"] == "candidate" and descriptor["data_commit"] == commit
    assert "staging" not in descriptor  # no binary report is invented for an adopted release
    roles = {a["role"]: a for a in descriptor["artifacts"]}
    base = f"storage/kg/{corpus}/v1"
    assert roles["graph"]["path"] == f"{base}/graph.json" and roles["manifest"]["path"] == f"{base}/manifest.json"
    for art in descriptor["artifacts"]:
        assert art["git_blob"] == _git(repo, "rev-parse", f"{commit}:{art['path']}")
    if flat_role_path:
        assert roles["legacy_flat_graph"]["path"] == flat_role_path
        assert roles["legacy_flat_graph"]["sha256"] == roles["graph"]["sha256"]
    else:
        assert roles["compat_copy"]["sha256"] == roles["graph"]["sha256"]
    assert adoption.descriptor_bytes(descriptor) == adoption.descriptor_bytes(adoption.build_adoption_descriptor(
        repo, commit, corpus, "v1", repository="github.com/example/fixture", importer_name="json_to_tiled",
        importer_version="1"))


def test_descriptor_validation_reports_schema_errors(committed):
    repo, commit = committed
    descriptor = adoption.build_adoption_descriptor(repo, commit, "legacyfix", "v1",
                                                    repository="github.com/example/fixture",
                                                    importer_name="x", importer_version="1")
    broken = dict(descriptor, state="bogus", data_commit="abc")
    errors = adoption.validate_descriptor(broken)
    assert any("state" in e for e in errors) and any("data_commit" in e for e in errors)
    with pytest.raises(ValueError):
        adoption.build_adoption_descriptor(repo, commit, "legacyfix", "v1", repository="not a repo",
                                           importer_name="x", importer_version="1")


def test_descriptor_cli_prints_to_stdout_and_refuses_paths_inside_the_repo(committed, tmp_path, capsys):
    from app.modules.commands import adopt_legacy_kg as cli
    repo, commit = committed
    base = ["descriptor", "--repo", str(repo), "--commit", commit, "--corpus", "legacyfix", "--snapshot", "v1",
            "--repository", "github.com/example/fixture"]
    assert cli.main(base) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["state"] == "candidate"
    assert cli.main(base + ["--out", str(repo / "docs" / "d.json")]) == 1
    assert "outside the repository" in capsys.readouterr().out
    target = tmp_path / "out" / "d.json"
    target.parent.mkdir()
    assert cli.main(base + ["--out", str(target)]) == 0
    assert json.loads(target.read_text()) == printed
    assert cli.main(base + ["--out", str(target)]) == 1  # never overwrites

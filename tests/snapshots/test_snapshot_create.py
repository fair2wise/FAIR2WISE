"""T23B snapshot creation: determinism vs the json2kg fixtures, layout, manifest, immutability, isolation."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

from app.modules import json2kg
from app.modules.contracts.changes import SnapshotManifest
from app.modules.graph import snapshot as snapshot_module
from app.modules.graph.releases import ReleaseVerificationError, check_paths, verify_snapshot_dir
from app.modules.graph.snapshot import (
    SnapshotBuildError,
    SnapshotError,
    SnapshotExistsError,
    SnapshotRequest,
    build_graph_isolated,
    create_snapshot,
)
from app.modules.graph.snapshot_manifest import KGManifest, parse_manifest, sources_checksum
from app.modules.graph.snapshots import load_snapshot
from app.modules.schema.loading import closure_fingerprint
from app.modules.schema.registry import RegistryError, SchemaRegistry

from tests.snapshots.snapshot_fakes import COMMIT, EXPECTED, INPUTS, REPO

SUCCESS_CASES = ("core_dict", "list_input", "empty_metadata", "custom_prefix")
MANIFEST_SCHEMA = json.loads(
    (REPO / "docs/migration/data_releases/schemas/kg_snapshot_manifest.schema.json").read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(root: Path):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


# ---------------------------------------------------------------- identity and field retention vs json2kg fixtures


@pytest.mark.parametrize("case", SUCCESS_CASES)
def test_graph_bytes_equal_the_frozen_json2kg_fixture(make, tmp_path, case):
    terms = tmp_path / f"{case}.terms.json"
    shutil.copy2(INPUTS / f"{case}.terms.json", terms)
    result = make(terms_path=terms, corpus="fixture")
    expected = (EXPECTED / f"{case}.graph.json").read_bytes()
    assert result.graph_path.read_bytes() == expected  # ids, key order, retention: all of it
    assert result.flat_path.read_bytes() == expected
    assert result.graph_sha256 == hashlib.sha256(expected).hexdigest()
    graph = json.loads(expected)
    assert (result.manifest.graph.node_count, result.manifest.graph.edge_count) == (
        len(graph["things"]), len(graph["associations"]))


def test_graph_identity_is_deterministic_across_runs_and_hash_seeds(make, tmp_path):
    first = make(corpus="alpha")
    second = make(corpus="beta")
    assert first.graph_path.read_bytes() == second.graph_path.read_bytes()
    code = (
        "import sys, pathlib\n"
        "from app.modules.graph.snapshot import SnapshotRequest, create_snapshot\n"
        "r = create_snapshot(SnapshotRequest('gamma', pathlib.Path(sys.argv[1]),\n"
        "    storage_kg=pathlib.Path(sys.argv[2]), terms_ref='x/y.json', code_commit='a'*40))\n"
        "print(r.graph_sha256)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(INPUTS / "core_dict.terms.json"), str(tmp_path / "other_kg")],
        cwd=tmp_path, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(REPO), "PYTHONHASHSEED": "4242"},
    ).stdout.strip().splitlines()[-1]
    assert out == first.graph_sha256


# ---------------------------------------------------------------- layout and manifest


def test_layout_is_exactly_the_frozen_one(make, storage_kg):
    result = make()
    assert _tree(storage_kg) == [
        "matkg_synthetic_v1.json", "synthetic", "synthetic/v1", "synthetic/v1/graph.json",
        "synthetic/v1/manifest.json", "synthetic/v1/validation.json"]
    assert not result.flat_path.is_symlink() and result.flat_path.parent == storage_kg
    assert result.flat_path.read_bytes() == result.graph_path.read_bytes()
    assert not result.graph_path.read_bytes().endswith(b"\n")  # json2kg serialization rule kept


def test_manifest_validates_against_the_normative_json_schema(make):
    result = make()
    document = json.loads(result.manifest_path.read_bytes())
    jsonschema.validate(document, MANIFEST_SCHEMA)
    assert "data_commit" not in json.dumps(document["created"]) and "data_commit" not in document
    assert parse_manifest(result.manifest_path.read_bytes()) == result.manifest
    # the committed example validates against the pydantic mirror too (both describe one schema)
    example = json.loads((REPO / "docs/migration/data_releases/examples/manifest.example.json").read_text())
    KGManifest.model_validate(example)


def test_manifest_binds_graph_schema_extraction_corpus_and_validation(make, storage_kg, registry, terms_file):
    shas = [hashlib.sha256(b"paper-a").hexdigest(), hashlib.sha256(b"paper-b").hexdigest()]
    revisions = [{"extractor": "extract_terms", "version": "2.1", "model": "m1", "run_ids": ["r1"]},
                 {"extractor": "extract_terms", "version": "2.1", "model": "m2"}]
    result = make(corpus="rsoxs", extraction_revisions=revisions, source_sha256=shas, run_id="run-7",
                  profile="ci", now=datetime(2026, 10, 14, 1, 2, 3, tzinfo=timezone.utc))
    m = result.manifest
    graph_sha = _sha(result.graph_path)
    assert (m.graph.sha256, m.graph.size_bytes) == (graph_sha, result.graph_path.stat().st_size)
    assert m.graph_id == "rsoxs_v1" and m.snapshot_id == "v1" and m.parent is None
    assert [c.model_dump() for c in m.compat_paths] == [
        {"path": "storage/kg/matkg_rsoxs_v1.json", "sha256": graph_sha}]
    entry = registry.get("rsoxs_schema")  # chosen from the flat name (multi_kg filename pattern)
    assert m.schema_block.model_dump(exclude_none=True) == {
        "name": "rsoxs_schema", "path": "storage/schema/rsoxs_schema.yaml", "sha256": entry.sha256,
        "version": "rsoxs_v1", "import_fingerprint": closure_fingerprint(entry)}
    assert [r.model_dump(exclude_none=True) for r in m.extraction.revisions] == revisions
    assert m.corpus_revision.source_count == 2 and m.corpus_revision.sources_checksum == sources_checksum(shas)
    assert m.corpus_revision.id == f"rsoxs-{sources_checksum(shas)[:12]}"
    assert m.inputs[0].sha256 == _sha(terms_file) and m.inputs[0].ref == "inputs/core_dict.terms.json"
    assert m.created.at == "2026-10-14T01:02:03Z" and m.created.code_commit == COMMIT
    assert m.created.json2kg_sha256 == _sha(Path(json2kg.__file__)) and m.created.profile == "ci"
    assert m.created.run_id == "run-7"
    validation = result.validation_path.read_bytes()
    assert m.validation.report_sha256 == hashlib.sha256(validation).hexdigest()
    assert m.validation.report_ref == "storage/kg/rsoxs/v1/validation.json"
    assert json.loads(validation)["schema"]["import_fingerprint"] == closure_fingerprint(entry)
    assert m.disclosure.status == "pass" and len(m.disclosure.report_sha256) == 64


def test_contract_snapshot_manifest_projection(make):
    result = make(extraction_revisions=[{"extractor": "x", "version": "1"}])
    contract = result.contract_manifest
    assert isinstance(contract, SnapshotManifest)
    assert SnapshotManifest.model_validate(contract.model_dump()) == contract
    assert contract.graph_path == "storage/kg/synthetic/v1/graph.json"
    assert contract.graph_sha256 == result.graph_sha256 and contract.parent_snapshot_id is None
    assert contract.schema_fingerprint == result.manifest.schema_block.import_fingerprint
    assert contract.corpus_fingerprint == result.manifest.corpus_revision.sources_checksum
    assert contract.validation_report_sha256 == result.manifest.validation.report_sha256
    assert len(contract.extraction_fingerprint) == 64


def test_extraction_fingerprint_ignores_revision_order_and_missing_revisions_are_marked_unknown(make):
    a = make(corpus="oa", extraction_revisions=[{"extractor": "e", "version": "1", "model": "m1"},
                                                {"extractor": "e", "version": "1", "model": "m2"}])
    b = make(corpus="ob", extraction_revisions=[{"extractor": "e", "version": "1", "model": "m2"},
                                                {"extractor": "e", "version": "1", "model": "m1"}])
    assert a.contract_manifest.extraction_fingerprint == b.contract_manifest.extraction_fingerprint
    none = make(corpus="oc")
    assert none.manifest.extraction.revisions[0].unknown is True
    assert any("unknown revision" in w for w in none.warnings)


def test_schema_selection_and_fingerprint_binding(make, registry, tmp_path):
    assert make(corpus="bl1101").manifest.schema_block.name == "bl1101_schema"
    assert make(corpus="other").manifest.schema_block.name == "matkg_schema"
    assert make(corpus="forced", schema_id="xray_schema").manifest.schema_block.name == "xray_schema"
    with pytest.raises(RegistryError):
        make(corpus="nope", schema_id="not_a_schema")
    with pytest.raises(SnapshotError, match="cannot be bound"):
        make(corpus="broken", schema_id="rsoxs_schema_extract")
    with pytest.raises(SnapshotError, match="cannot be bound"):
        make(corpus="vocab", schema_id="prov_vocabulary")
    # a changed schema import changes the bound fingerprint
    copy_dir = tmp_path / "schemas"
    shutil.copytree(REPO / "storage/schema", copy_dir)
    original = make(corpus="fp1", schema_id="rsoxs_schema").manifest.schema_block.import_fingerprint
    (copy_dir / "matkg_schema.yaml").write_bytes((copy_dir / "matkg_schema.yaml").read_bytes() + b"\n# x\n")
    changed = make(corpus="fp2", schema_id="rsoxs_schema", registry=SchemaRegistry.discover(copy_dir))
    assert changed.manifest.schema_block.import_fingerprint != original


def test_legacy_graph_mismatch_is_a_warning_and_nothing_is_retyped(make):
    result = make(corpus="rsoxs")
    report = json.loads(result.validation_path.read_bytes())
    assert report["status"] == "pass" and report["fingerprint_status"] == "legacy_unverified"
    assert report["categories"]["legacy_warning"]["nodes_with_category_outside_closure"] > 0
    expected = json.loads((EXPECTED / "core_dict.graph.json").read_bytes())
    assert json.loads(result.graph_path.read_bytes()) == expected  # categories exactly as json2kg wrote them


def test_snapshot_loads_through_the_t08a_reader(make):
    result = make(corpus="rsoxs")
    snap = load_snapshot(result.graph_path)
    assert snap.graph_id == "rsoxs_v1" and snap.sha256 == result.graph_sha256 and len(snap.nodes) == 14
    assert load_snapshot(result.flat_path).sha256 == snap.sha256


def test_verify_snapshot_dir_accepts_a_fresh_snapshot(make):
    result = make()
    verified = verify_snapshot_dir(result.directory)
    assert verified.graph_bytes == result.graph_path.read_bytes() and verified.compat_paths


# ---------------------------------------------------------------- immutability, versions, lineage


def test_existing_snapshot_is_never_overwritten(make, storage_kg):
    first = make(snapshot_id="v1")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in storage_kg.rglob("*") if p.is_file()}
    with pytest.raises(SnapshotExistsError, match="immutable"):
        make(snapshot_id="v1")
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in storage_kg.rglob("*") if p.is_file()}
    assert before == after and first.directory.is_dir()
    assert not [p for p in storage_kg.rglob("*") if ".staging" in p.name or p.name.endswith(".tmp")]


def test_repair_creates_a_new_version_with_a_verified_parent(make):
    v1 = make()
    v2 = make(parent_snapshot_id="v1", parent_relation="repair", parent_data_commit="b" * 40)
    assert v2.snapshot_id == "v2" and v2.manifest.graph_id == "synthetic_v2"
    parent = v2.manifest.parent
    assert (parent.snapshot_id, parent.graph_sha256, parent.relation, parent.data_commit) == (
        "v1", v1.graph_sha256, "repair", "b" * 40)
    assert v2.contract_manifest.parent_snapshot_id == "v1"
    assert verify_snapshot_dir(v1.directory).graph_sha256 == v1.graph_sha256  # parent untouched
    with pytest.raises(SnapshotError, match="parent snapshot v9"):
        make(parent_snapshot_id="v9")
    with pytest.raises(SnapshotError, match="parent_relation"):
        make(parent_snapshot_id="v1", parent_relation="mutate")


def test_a_tampered_parent_is_refused(make):
    v1 = make()
    v1.graph_path.write_bytes(v1.graph_path.read_bytes().replace(b"Material", b"Materiel", 1))
    with pytest.raises(SnapshotError, match="does not verify"):
        make(parent_snapshot_id="v1")


def test_legacy_flat_files_are_never_overwritten_or_reused(make, storage_kg):
    legacy = storage_kg / "matkg_synthetic_v1.json"
    legacy.write_bytes(b'{"things": [], "associations": []}')
    before = (legacy.read_bytes(), legacy.stat().st_mtime_ns)
    with pytest.raises(SnapshotExistsError):
        make(snapshot_id="v1")
    assert (legacy.read_bytes(), legacy.stat().st_mtime_ns) == before
    assert not (storage_kg / "synthetic" / "v1").exists()
    auto = make()  # numbering continues after the legacy flat file
    assert auto.snapshot_id == "v2" and (legacy.read_bytes(), legacy.stat().st_mtime_ns) == before


def test_source_terms_file_is_only_read(make, terms_file, tmp_path):
    schema_before = {p.name: p.read_bytes() for p in (REPO / "storage/schema").glob("*.yaml")}
    terms_file.chmod(0o444)
    before = (terms_file.read_bytes(), terms_file.stat().st_mtime_ns, terms_file.stat().st_mode)
    terms_file.parent.chmod(0o555)  # the input directory is not writable: nothing may be written there
    try:
        make()
        make()
    finally:
        terms_file.parent.chmod(0o755)
    assert (terms_file.read_bytes(), terms_file.stat().st_mtime_ns, terms_file.stat().st_mode) == before
    assert sorted(p.name for p in terms_file.parent.iterdir()) == ["core_dict.terms.json"]
    assert schema_before == {p.name: p.read_bytes() for p in (REPO / "storage/schema").glob("*.yaml")}


# ---------------------------------------------------------------- the caller-aliasing hazard


def _hazard_terms():
    return [{"term": "A", "category": "Material", "source_papers": ["p1.pdf"]},
            {"term": "A", "source_papers": ["p2.pdf"]}]


def test_build_graph_isolated_does_not_leak_the_aliasing_hazard():
    terms = _hazard_terms()
    snippets = []
    first = build_graph_isolated(terms, snippets)
    assert terms == _hazard_terms()  # caller's lists untouched (no source_papers append)
    second = build_graph_isolated(terms, snippets)
    assert json.dumps(first) == json.dumps(second)
    assert first["things"][0]["publications"] == [{"source_paper": "p1.pdf"}]
    assert first["things"][0]["source_papers"] is not terms[0]["source_papers"]
    first["things"][0]["source_papers"].append("mutated")  # mutating the output cannot reach the input either
    assert terms == _hazard_terms()


def test_the_raw_hazard_still_exists_in_json2kg_which_is_why_the_wrapper_exists():
    terms = _hazard_terms()
    json2kg.build_graph(terms)
    assert terms[0]["source_papers"] == ["p1.pdf", "p2.pdf"]  # frozen json2kg behavior (D21): not edited here
    assert json2kg.build_graph(terms)["things"][0]["publications"] == []


def test_snapshots_from_the_same_input_are_identical_even_with_duplicate_terms(make, tmp_path):
    terms = tmp_path / "dup.terms.json"
    terms.write_text(json.dumps({"terms": _hazard_terms()}), encoding="utf-8")
    a = make(corpus="dupa", terms_path=terms)
    b = make(corpus="dupb", terms_path=terms)
    assert a.graph_path.read_bytes() == b.graph_path.read_bytes()
    assert json.loads(terms.read_text()) == {"terms": _hazard_terms()}
    assert json.loads(a.graph_path.read_bytes())["things"][0]["publications"] == [{"source_paper": "p1.pdf"}]


# ---------------------------------------------------------------- failed builds leave nothing


@pytest.mark.parametrize("case", ["error_no_terms_key", "error_relation_not_object", "error_invalid_json"])
def test_failed_build_leaves_no_partial_snapshot(make, storage_kg, tmp_path, case):
    bad = tmp_path / f"{case}.json"
    shutil.copy2(INPUTS / f"{case}.terms.json", bad)
    with pytest.raises(SnapshotBuildError, match="json2kg could not convert"):
        make(terms_path=bad)
    leftovers = [p for p in storage_kg.rglob("*") if p.is_file() or p.name.startswith(".")]
    assert leftovers == []  # no graph, manifest, flat copy, staging or temp file


def test_failure_after_the_graph_is_built_also_leaves_nothing(make, storage_kg, monkeypatch):
    def boom(self):
        raise RuntimeError("disk full while writing the manifest")

    monkeypatch.setattr(KGManifest, "to_bytes", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        make()
    assert [p for p in storage_kg.rglob("*") if p.is_file() or p.name.startswith(".")] == []
    monkeypatch.undo()
    assert make().snapshot_id == "v1"  # the id was not burned


def test_failure_at_the_atomic_rename_releases_the_flat_name(make, storage_kg, monkeypatch):
    def deny(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(snapshot_module.os, "rename", deny)
    with pytest.raises(SnapshotError, match="could not freeze"):
        make()
    assert [p for p in storage_kg.rglob("*") if p.is_file() or p.name.startswith(".")] == []
    monkeypatch.undo()
    assert make().snapshot_id == "v1"


@pytest.mark.parametrize("override", [
    {"corpus": "../escape"}, {"corpus": "Upper"}, {"corpus": ""}, {"snapshot_id": "1"}, {"snapshot_id": "v0"},
    {"terms_path": Path("/nonexistent/terms.json")}, {"code_commit": "abc"},
])
def test_invalid_requests_are_refused_before_anything_is_written(make, storage_kg, override):
    with pytest.raises(ValueError):
        make(**override)
    assert [p for p in storage_kg.rglob("*") if p.is_file()] == []


def test_terms_outside_the_repo_need_an_explicit_reference(storage_kg, terms_file, registry):
    request = SnapshotRequest(corpus="syn", terms_path=terms_file, storage_kg=storage_kg, code_commit=COMMIT,
                              registry=registry)
    with pytest.raises(SnapshotError, match="terms_ref is required"):
        create_snapshot(request)
    with pytest.raises(ValueError):  # absolute local paths are rejected by the manifest pattern
        create_snapshot(SnapshotRequest(corpus="syn", terms_path=terms_file, storage_kg=storage_kg,
                                        terms_ref=str(terms_file), code_commit=COMMIT, registry=registry))
    assert [p for p in storage_kg.rglob("*") if p.is_file()] == []
    # a file inside the repository gets its repo-relative path automatically
    inside = create_snapshot(SnapshotRequest(corpus="syn", terms_path=INPUTS / "core_dict.terms.json",
                                             storage_kg=storage_kg, code_commit=COMMIT, registry=registry))
    assert inside.manifest.inputs[0].ref == "tests/contracts/json_kg/fixtures/inputs/core_dict.terms.json"


def test_code_commit_defaults_to_the_checkout_head_without_a_shell(make):
    result = make(code_commit=None)
    assert len(result.manifest.created.code_commit) in (40, 64)


# ---------------------------------------------------------------- mutable working files are not artifacts


def test_a_working_file_is_never_an_approved_artifact(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "kg.json").write_bytes((EXPECTED / "core_dict.graph.json").read_bytes())
    for target in (workdir, workdir / "kg.json", tmp_path):
        with pytest.raises(ReleaseVerificationError) as err:
            verify_snapshot_dir(target)
        assert err.value.code == "layout"
    paths = ["workdir/kg.json", "storage/kg/matkg_qwen3_235b_257papers_20260101_000000.json",
             "storage/kg/synthetic/v1/graph.json", "storage/kg/matkg_synthetic_v1.json"]
    assert [f.location for f in check_paths(paths)] == paths[:2]


def test_tampering_after_freezing_is_detected_in_the_working_tree(make):
    result = make()
    original = result.graph_path.read_bytes()
    result.graph_path.write_bytes(original.replace(b"Material", b"Materiel", 1))
    with pytest.raises(ReleaseVerificationError) as err:
        verify_snapshot_dir(result.directory)
    assert err.value.code == "tampered"
    result.graph_path.write_bytes(original)
    verify_snapshot_dir(result.directory)
    result.flat_path.write_bytes(original + b" ")
    with pytest.raises(ReleaseVerificationError, match="compat copy"):
        verify_snapshot_dir(result.directory)
    result.flat_path.unlink()
    result.flat_path.symlink_to(result.graph_path)  # a symlink is not an acceptable flat copy
    with pytest.raises(ReleaseVerificationError, match="symlink"):
        verify_snapshot_dir(result.directory)
    result.flat_path.unlink()
    result.flat_path.write_bytes(original)
    (result.directory / "extra.pdf").write_bytes(b"%PDF")
    with pytest.raises(ReleaseVerificationError, match="unexpected snapshot contents"):
        verify_snapshot_dir(result.directory)


# ---------------------------------------------------------------- isolation between parallel jobs


def _race_job(args):
    storage, terms, tag = args
    from app.modules.graph.snapshot import SnapshotExistsError, SnapshotRequest, create_snapshot

    try:
        res = create_snapshot(SnapshotRequest(
            corpus="race", terms_path=Path(terms), storage_kg=Path(storage), snapshot_id="v1",
            terms_ref=f"inputs/{tag}.json", code_commit="c" * 40))
        return ("ok", tag, res.graph_sha256)
    except SnapshotExistsError:
        return ("exists", tag, None)


def _variant_terms(tmp_path, count):
    paths = []
    for i in range(count):
        data = json.loads((INPUTS / "core_dict.terms.json").read_text(encoding="utf-8"))
        data["terms"][0]["term"] = f"Variant{i}"
        path = tmp_path / f"variant{i}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        paths.append(path)
    return paths


def test_parallel_jobs_with_automatic_ids_get_distinct_complete_snapshots(storage_kg, tmp_path, registry):
    terms = _variant_terms(tmp_path, 8)

    def job(path):
        return create_snapshot(SnapshotRequest(corpus="par", terms_path=path, storage_kg=storage_kg,
                                               terms_ref=f"inputs/{path.name}", code_commit=COMMIT,
                                               registry=registry))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(job, terms))
    assert sorted(r.snapshot_id for r in results) == sorted(f"v{i}" for i in range(1, 9))
    for result, path in zip(results, terms):
        verified = verify_snapshot_dir(result.directory)  # each snapshot is whole and self-consistent
        assert verified.manifest.inputs[0].sha256 == _sha(path)  # and built from its own job's input
    assert len(list(storage_kg.glob("matkg_par_v*.json"))) == 8
    assert not [p for p in storage_kg.rglob("*") if p.name.startswith(".")]


def test_racing_jobs_for_one_explicit_id_produce_exactly_one_consistent_winner(storage_kg, tmp_path):
    terms = _variant_terms(tmp_path, 4)
    jobs = [(str(storage_kg), str(p), p.stem) for p in terms]
    with ProcessPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(_race_job, jobs))
    winners = [o for o in outcomes if o[0] == "ok"]
    assert len(winners) == 1 and sorted(o[0] for o in outcomes) == ["exists", "exists", "exists", "ok"]
    verified = verify_snapshot_dir(storage_kg / "race" / "v1")
    assert verified.graph_sha256 == winners[0][2]
    # the manifest belongs to the winning job's input
    assert verified.manifest.inputs[0].ref == f"inputs/{winners[0][1]}.json"
    assert (storage_kg / "matkg_race_v1.json").read_bytes() == verified.graph_bytes
    assert not [p for p in storage_kg.rglob("*") if p.name.startswith(".")]


# ---------------------------------------------------------------- no Tiled, no network, no shell


def test_build_runs_with_tiled_absent_network_blocked_and_no_shell(make, monkeypatch):
    for name in ("tiled", "tiled.client"):
        monkeypatch.setitem(sys.modules, name, None)  # importing it raises ImportError

    def refuse(*_a, **_k):
        raise AssertionError("network or process access attempted during a JSON build")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(os, "system", refuse)
    monkeypatch.setattr(os, "popen", refuse)
    with pytest.raises(ImportError):
        import tiled  # noqa: F401
    assert make().snapshot_id == "v1"


def test_a_fresh_interpreter_builds_without_importing_tiled_or_the_tiled_backends(tmp_path):
    code = (
        "import sys, socket, pathlib\n"
        "def refuse(*a, **k): raise AssertionError('network')\n"
        "socket.socket.connect = refuse; socket.getaddrinfo = refuse; socket.create_connection = refuse\n"
        "from app.modules.graph.snapshot import SnapshotRequest, create_snapshot\n"
        "r = create_snapshot(SnapshotRequest('syn', pathlib.Path(sys.argv[1]),\n"
        "    storage_kg=pathlib.Path(sys.argv[2]), terms_ref='a/b.json', code_commit='a'*40))\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'tiled' or"
        " m.startswith('app.modules.graph.backends') or m == 'app.modules.tiled_graph')\n"
        "print('BAD', bad)\n"
        "print('OK', r.snapshot_id)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(INPUTS / "core_dict.terms.json"), str(tmp_path / "kg")],
        cwd=tmp_path, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(REPO)},
    ).stdout.splitlines()
    assert "OK v1" in out and "BAD []" in out


def test_snapshot_modules_contain_no_tiled_or_shell_use():
    for name in ("snapshot.py", "snapshot_manifest.py"):
        text = (REPO / "app/modules/graph" / name).read_text(encoding="utf-8")
        for needle in ("import subprocess", "from subprocess", "os.system", "shell=True", "import tiled",
                       "from tiled", "from .backends", "tiled_graph"):
            assert needle not in text, (name, needle)


# ---------------------------------------------------------------- CLI facade


def _cli(args, tmp_path):
    return subprocess.run(
        [sys.executable, str(REPO / "scripts/build_kg_snapshot.py"), *map(str, args)], cwd=tmp_path,
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})


def test_cli_creates_a_snapshot_and_reports_json(tmp_path):
    kg = tmp_path / "kg"
    proc = _cli(["--corpus", "syn", "--terms", INPUTS / "core_dict.terms.json", "--storage-kg", kg,
                 "--code-commit", COMMIT, "--extraction-revision", "extract_terms:2.1:m1"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    assert summary["snapshot_id"] == "v1" and summary["commit_ready"] is True
    assert (kg / "syn" / "v1" / "graph.json").read_bytes() == (EXPECTED / "core_dict.graph.json").read_bytes()
    verify_snapshot_dir(kg / "syn" / "v1")
    again = _cli(["--corpus", "syn", "--terms", INPUTS / "core_dict.terms.json", "--storage-kg", kg,
                  "--code-commit", COMMIT, "--snapshot-id", "v1"], tmp_path)
    assert again.returncode == 1 and "immutable" in again.stdout + again.stderr


def test_cli_failure_exits_1_and_writes_nothing(tmp_path):
    kg = tmp_path / "kg"
    proc = _cli(["--corpus", "syn", "--terms", INPUTS / "error_no_terms_key.terms.json", "--storage-kg", kg,
                 "--code-commit", COMMIT, "--terms-ref", "x/y.json"], tmp_path)
    assert proc.returncode == 1 and "ERROR" in proc.stdout + proc.stderr
    assert [p for p in kg.rglob("*") if p.is_file()] == []
    assert _cli([], tmp_path).returncode == 2


def test_manifest_copy_is_independent_of_the_result_object(make):
    result = make()
    assert copy.deepcopy(result.manifest) == result.manifest

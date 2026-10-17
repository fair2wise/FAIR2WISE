"""T26B end to end in an isolated Git fixture: prepare -> owner commits -> committed == tested -> descriptor
-> export. The descriptor validates against the normative T26A schema."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from app.modules.graph import releases
from app.modules.graph.releases import (
    ReleaseVerificationError,
    build_data_release_descriptor,
    build_release_record,
    export_snapshot_selection,
    lookup_snapshot,
    validate_commit_candidate,
    verify_snapshot_selection,
)
from tests.releases.conftest import REPOSITORY
from tests.snapshots.snapshot_fakes import REPO

DESCRIPTOR_SCHEMA = REPO / "docs/migration/data_releases/schemas/data_release_descriptor.schema.json"


def prepare(fx, **kwargs):
    report = kwargs.pop("report", None) or fx.report()
    candidate = validate_commit_candidate(fx.root, fx.rel, binary_report=report.read_bytes(),
                                          binary_report_ref="private:binary/synthetic-v1.json", **kwargs)
    return candidate, build_release_record(candidate, record_name="<release-dir>/r.json", repository=REPOSITORY)


def test_prepare_commit_verify_describe_export(fx, tmp_path):
    candidate, record = prepare(fx)
    tested = {a["path"]: a for a in record["artifacts"]}
    assert set(tested) == {f"{fx.rel}/graph.json", f"{fx.rel}/manifest.json", f"{fx.rel}/validation.json",
                           "storage/kg/matkg_synthetic_v1.json"}
    assert [a["role"] for a in record["artifacts"]][:3] == ["graph", "manifest", "validation"]
    assert record["state"] == "tested_uncommitted" and record["binary_report"]["verdict"] == "PASS"
    assert record["schema"]["binding"] == "verified" and record["private_dependencies"]["status"] == "pass"
    assert record["git"]["worktree_clean"] and record["automation"] == {"git_writes": "none", "push": "never"}
    assert "data_commit" not in json.dumps(record)  # no commit id exists yet; no circular identity
    text = releases.canonical_json_bytes(record).decode()
    assert str(fx.root) not in text and str(tmp_path) not in text and "/private/" not in text

    commit = fx.run_owner_commands(record)
    for path, item in tested.items():  # the blob Git stored is the blob the record predicted
        assert fx.git("rev-parse", f"{commit}:{path}") == item["git_blob"]

    committed = verify_snapshot_selection(fx.root, record, commit)
    assert committed.release.graph.data == fx.snapshot.graph_path.read_bytes()
    descriptor = build_data_release_descriptor(committed, importer_name="app.modules.graph.imports.tiled_json",
                                               importer_version="test")
    jsonschema.validate(descriptor, json.loads(DESCRIPTOR_SCHEMA.read_text("utf-8")))
    assert descriptor["data_commit"] == commit and descriptor["state"] == "candidate"
    assert descriptor["release_id"] == record["release_id"] == candidate.release_id
    assert descriptor["repository"] == REPOSITORY
    binary = descriptor["staging"]["binary_report"]
    assert binary["passed"] is True and binary["data_commit"] == commit
    assert binary["graph_sha256"] == tested[f"{fx.rel}/graph.json"]["sha256"]

    out = tmp_path / "mount"
    first = export_snapshot_selection(fx.root, commit, descriptor["artifacts"], out)
    assert {f["status"] for f in first} == {"written"}
    for path, item in tested.items():
        assert releases.sha256_bytes((out / path).read_bytes()) == item["sha256"]
    again = export_snapshot_selection(fx.root, commit, descriptor["artifacts"], out)
    assert {f["status"] for f in again} == {"already_present"}

    found = lookup_snapshot(fx.root, commit, "synthetic", "v1")
    assert found.graph.sha256 == binary["graph_sha256"] and found.validation is not None
    assert found.manifest_artifact.sha256 == binary["manifest_sha256"]


def test_lookup_and_export_never_select_by_branch_or_working_tree(fx, tmp_path):
    _, record = prepare(fx)
    commit = fx.run_owner_commands(record)
    for ref in ("main", "HEAD", commit[:12]):
        with pytest.raises(ReleaseVerificationError) as err:
            lookup_snapshot(fx.root, ref, "synthetic", "v1")
        assert err.value.code == "bad_commit"
    original = fx.snapshot.graph_path.read_bytes()
    fx.snapshot.graph_path.write_bytes(original + b" ")  # the working tree is never the byte source
    descriptor_items = verify_snapshot_selection(fx.root, record, commit).record["artifacts"]
    export_snapshot_selection(fx.root, commit, descriptor_items, tmp_path / "m")
    assert (tmp_path / "m" / fx.rel / "graph.json").read_bytes() == original


def test_export_refuses_to_overwrite_and_symlinked_destinations(fx, tmp_path):
    _, record = prepare(fx)
    commit = fx.run_owner_commands(record)
    out = tmp_path / "mount"
    target = out / fx.rel / "graph.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"{}")
    with pytest.raises(ReleaseVerificationError) as err:
        export_snapshot_selection(fx.root, commit, record["artifacts"], out)
    assert err.value.code == "export_conflict" and target.read_bytes() == b"{}"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    linked = tmp_path / "linked"
    (linked / "storage").mkdir(parents=True)
    (linked / "storage" / "kg").symlink_to(elsewhere)
    with pytest.raises(ReleaseVerificationError) as err:
        export_snapshot_selection(fx.root, commit, record["artifacts"], linked)
    assert err.value.code == "export_conflict" and not any(elsewhere.iterdir())


def test_rerunning_prepare_on_an_already_committed_snapshot_is_idempotent(fx):
    _, record = prepare(fx)
    commit = fx.run_owner_commands(record)
    _, again = prepare(fx)
    assert again["artifacts"] == record["artifacts"]
    assert sorted(again["git"]["already_committed"]) == sorted(a["path"] for a in record["artifacts"])
    assert again["git"]["base_commit"] == commit


def test_record_and_descriptor_hold_no_local_path_even_with_absolute_inputs(fx, tmp_path):
    root = tmp_path / "private-store"
    paper = root / "papers" / "paper_alpha.pdf"
    paper.parent.mkdir(parents=True)
    paper.write_bytes(b"%PDF-1.4 synthetic")
    inventory = {"sources": {"paper_alpha.pdf": {"ref": "private:papers/paper_alpha.pdf",
                                                 "sha256": releases.sha256_bytes(paper.read_bytes())}}}
    _, record = prepare(fx, evidence_inventory=inventory, evidence_root=root)
    text = json.dumps(record)
    assert str(root) not in text and str(Path(root).resolve()) not in text
    rows = {r["source"]: r for r in record["evidence"]["sources"]}
    assert rows["paper_alpha.pdf"]["status"] == "available"

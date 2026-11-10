"""H5 rehearsal finding 1: an export leaves the data commit on the mount; the job path records it in the receipt.

``f2w-kg-release export`` writes ``storage/kg/<corpus>/<vN>.release_commit.json`` beside the snapshot directory
(not inside it: the frozen layout admits no extra file). ``load_source`` on that directory reads it, hash-bound to
the verified bytes, so plan detail and receipt extras carry ``data_commit``. An older mount without the record
imports as before (``data_commit`` null); a record that does not match the bytes is refused, never ignored.
"""
from __future__ import annotations

import json

import pytest

from app.modules.commands import prepare_kg_release as cli
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph import releases
from app.modules.graph.imports import tiled_json as tj
from app.modules.graph.releases import (
    ReleaseVerificationError,
    build_release_record,
    export_snapshot_selection,
    read_release_commit,
    release_commit_path,
    validate_commit_candidate,
    verify_snapshot_dir,
    verify_snapshot_selection,
)
from tests.releases.conftest import REPOSITORY


def _committed(fx):
    report = fx.report()
    candidate = validate_commit_candidate(fx.root, fx.rel, binary_report=report.read_bytes(),
                                          binary_report_ref="private:binary/synthetic-v1.json")
    record = build_release_record(candidate, record_name="<release-dir>/r.json", repository=REPOSITORY)
    commit = fx.run_owner_commands(record)
    descriptor = verify_snapshot_selection(fx.root, record, commit).record["artifacts"]
    return commit, descriptor


def _fingerprints(fx):
    return tj.default_supported_schema_fingerprints(fx.registry())


def test_export_writes_a_hash_bound_commit_record_beside_the_snapshot_and_the_import_reads_it(fx, tmp_path):
    commit, artifacts = _committed(fx)
    mount = tmp_path / "mount"
    done = export_snapshot_selection(fx.root, commit, artifacts, mount)
    assert {f["path"] for f in done} == {a["path"] for a in artifacts}, "the artifact list is unchanged"
    snapshot_dir = mount / fx.rel
    record_path = release_commit_path(snapshot_dir)
    assert record_path == mount / "storage" / "kg" / "synthetic" / "v1.release_commit.json"
    record = json.loads(record_path.read_text("utf-8"))
    verified = verify_snapshot_dir(snapshot_dir)  # the sibling file does not disturb the frozen layout
    assert record["data_commit"] == commit and (record["corpus"], record["snapshot_id"]) == ("synthetic", "v1")
    assert record["graph_sha256"] == verified.graph_sha256 and record["manifest_sha256"] == verified.manifest_sha256
    assert {a["path"] for a in record["artifacts"]} == {a["path"] for a in artifacts}
    assert all(len(a["sha256"]) == 64 and len(a["git_blob"]) == 40 for a in record["artifacts"])

    assert read_release_commit(snapshot_dir, graph_sha256=verified.graph_sha256,
                               manifest_sha256=verified.manifest_sha256) == commit
    src = tj.load_source(snapshot_dir, supported_schema_fingerprints=_fingerprints(fx))
    assert src.data_commit == commit, "the directory source now carries the commit (job path)"

    again = export_snapshot_selection(fx.root, commit, artifacts, mount)
    assert {f["status"] for f in again} == {"already_present"}
    assert json.loads(record_path.read_text("utf-8")) == record, "re-export leaves the identical record"


def test_an_older_mount_without_the_record_still_imports_with_no_commit(fx, tmp_path):
    commit, artifacts = _committed(fx)
    mount = tmp_path / "mount"
    export_snapshot_selection(fx.root, commit, artifacts, mount)
    release_commit_path(mount / fx.rel).unlink()
    src = tj.load_source(mount / fx.rel, supported_schema_fingerprints=_fingerprints(fx))
    assert src.data_commit is None and src.graph_sha256 == verify_snapshot_dir(mount / fx.rel).graph_sha256


@pytest.mark.parametrize("tamper", ["other_hash", "other_snapshot", "short_commit", "not_json", "no_version", "symlink"])
def test_a_record_that_does_not_match_the_verified_bytes_is_refused_not_ignored(fx, tmp_path, tamper):
    commit, artifacts = _committed(fx)
    mount = tmp_path / "mount"
    export_snapshot_selection(fx.root, commit, artifacts, mount)
    path = release_commit_path(mount / fx.rel)
    record = json.loads(path.read_text("utf-8"))
    if tamper == "other_hash":
        record["graph_sha256"] = "0" * 64
    elif tamper == "other_snapshot":
        record["snapshot_id"] = "v2"
    elif tamper == "short_commit":
        record["data_commit"] = commit[:12]
    elif tamper == "no_version":
        record.pop("record_version")
    if tamper == "not_json":
        path.write_bytes(b"{not json")
    elif tamper == "symlink":
        elsewhere = tmp_path / "elsewhere.json"
        elsewhere.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(elsewhere)
    else:
        path.write_bytes(releases.canonical_json_bytes(record))
    verified = verify_snapshot_dir(mount / fx.rel)
    with pytest.raises(ReleaseVerificationError) as err:
        read_release_commit(mount / fx.rel, graph_sha256=verified.graph_sha256, manifest_sha256=verified.manifest_sha256)
    assert err.value.code == ("wrong_hash" if tamper == "other_hash" else "record_invalid")
    with pytest.raises(ContractError) as cerr:
        tj.load_source(mount / fx.rel, supported_schema_fingerprints=_fingerprints(fx))
    assert cerr.value.code is ErrorCode.INVALID_INPUT and "release commit record" in str(cerr.value)


def test_a_failed_export_leaves_no_record(fx, tmp_path):
    commit, artifacts = _committed(fx)
    mount = tmp_path / "mount"
    clash = mount / fx.rel / "graph.json"
    clash.parent.mkdir(parents=True)
    clash.write_bytes(b"{}")
    with pytest.raises(ReleaseVerificationError):
        export_snapshot_selection(fx.root, commit, artifacts, mount)
    assert not release_commit_path(mount / fx.rel).exists()


def test_cli_export_names_the_record(fx, tmp_path, capsys):
    commit, _artifacts = _committed(fx)
    report = fx.report()
    out = tmp_path / "release"
    assert cli.main(["prepare", "--repo", str(fx.root), "--snapshot", fx.rel, "--binary-report", str(report),
                     "--binary-report-ref", "private:binary/r.json", "--repository", REPOSITORY,
                     "--out", str(out)]) == cli.EXIT_OK
    record = next(out.glob("*.release_record.json"))
    capsys.readouterr()
    assert cli.main(["verify-committed", "--repo", str(fx.root), "--record", str(record), "--commit", commit,
                     "--out", str(out)]) == cli.EXIT_OK
    descriptor = out / json.loads(capsys.readouterr().out.split("\n\n")[0])["descriptor"]
    mount = tmp_path / "mount"
    assert cli.main(["export", "--repo", str(fx.root), "--descriptor", str(descriptor), "--dest", str(mount)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["release_commit_record"] == "storage/kg/synthetic/v1.release_commit.json"
    assert json.loads((mount / summary["release_commit_record"]).read_text("utf-8"))["data_commit"] == commit

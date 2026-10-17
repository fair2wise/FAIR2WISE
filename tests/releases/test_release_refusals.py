"""T26B refusal paths, each in an isolated temporary Git repository.

Grouped by the task's refusal list: bytes changed after testing, private path leaked, schema fingerprint missing
or mismatched, binary report for a different snapshot, dirty working tree. (Push: test_release_no_push.py.)
"""
from __future__ import annotations

import json

import pytest

from app.modules.graph import releases
from app.modules.graph.releases import (
    ReleaseRefused,
    build_data_release_descriptor,
    build_release_record,
    validate_commit_candidate,
    verify_snapshot_selection,
)
from tests.releases.conftest import REPOSITORY, finding_rules, git_input, terms_with


def prepare(fx, report=None, **kwargs):
    report = report or fx.report()
    candidate = validate_commit_candidate(fx.root, fx.rel, binary_report=report.read_bytes(),
                                          binary_report_ref="private:binary/r.json", **kwargs)
    return build_release_record(candidate, record_name="<release-dir>/r.json", repository=REPOSITORY)


def refused(fx, code, report=None, **kwargs):
    with pytest.raises(ReleaseRefused) as err:
        prepare(fx, report, **kwargs)
    assert err.value.code == code, str(err.value)
    return err.value


# ------------------------------------------------------------------ bytes changed after testing


def test_graph_edited_after_freezing_is_refused_before_commit(fx):
    fx.snapshot.graph_path.chmod(0o644)
    fx.snapshot.graph_path.write_bytes(fx.snapshot.graph_path.read_bytes().replace(b"Synth", b"Synthe", 1))
    refused(fx, "tampered")


def test_flat_copy_or_validation_edited_after_freezing_is_refused(fx):
    fx.snapshot.validation_path.chmod(0o644)
    fx.snapshot.validation_path.write_bytes(fx.snapshot.validation_path.read_bytes() + b" ")
    refused(fx, "tampered")


def test_bytes_edited_between_prepare_and_commit_fail_committed_equals_tested(fx):
    record = prepare(fx)
    graph = fx.snapshot.graph_path
    graph.chmod(0o644)
    graph.write_bytes(graph.read_bytes().replace(b"Synth", b"Synthe", 1))  # owner edits before committing
    commit = fx.run_owner_commands(record)
    with pytest.raises(ReleaseRefused) as err:
        verify_snapshot_selection(fx.root, record, commit)
    assert err.value.code == "committed_differs_from_tested"


def test_a_later_commit_that_rewrites_the_snapshot_fails_against_the_tested_record(fx):
    record = prepare(fx)
    first = fx.run_owner_commands(record)
    manifest = fx.snapshot.manifest_path
    manifest.chmod(0o644)
    manifest.write_bytes(manifest.read_bytes().replace(b'"tool"', b'"tool" ', 1))
    fx.git("commit", "-q", "-am", "manual manifest edit")
    second = fx.git("rev-parse", "HEAD")
    assert verify_snapshot_selection(fx.root, record, first).data_commit == first
    with pytest.raises(ReleaseRefused) as err:
        verify_snapshot_selection(fx.root, record, second)
    assert err.value.code == "committed_differs_from_tested"


def test_an_extra_or_missing_committed_file_in_the_snapshot_directory_is_refused(fx):
    record = prepare(fx)
    commit = fx.run_owner_commands(record)
    extra = fx.root / fx.rel / "notes.json"
    extra.write_text("{}", encoding="utf-8")
    fx.git("add", "-f", str(extra.relative_to(fx.root)))
    fx.git("commit", "-q", "-m", "extra file")
    with pytest.raises(ReleaseRefused) as err:
        verify_snapshot_selection(fx.root, record, fx.git("rev-parse", "HEAD"))
    assert err.value.code == "committed_differs_from_tested"
    fx.git("reset", "-q", "--hard", commit)
    fx.git("rm", "-q", "--cached", f"{fx.rel}/validation.json")
    fx.git("commit", "-q", "-m", "drop validation")
    with pytest.raises(ReleaseRefused) as err:
        verify_snapshot_selection(fx.root, record, fx.git("rev-parse", "HEAD"))
    assert err.value.code == "committed_differs_from_tested"


def test_a_commit_without_the_bound_schema_file_is_refused(fx):
    record = prepare(fx)
    fx.run_owner_commands(record)
    fx.git("rm", "-q", "--cached", "storage/schema/matkg_schema.yaml")
    fx.git("commit", "-q", "-m", "drop schema")
    with pytest.raises(ReleaseRefused) as err:
        verify_snapshot_selection(fx.root, record, fx.git("rev-parse", "HEAD"))
    assert err.value.code == "missing"


def test_a_tampered_record_cannot_vouch_for_other_bytes(fx):
    record = prepare(fx)
    commit = fx.run_owner_commands(record)
    forged = json.loads(json.dumps(record))
    forged["artifacts"][0]["sha256"] = "0" * 64
    with pytest.raises(ReleaseRefused) as err:
        verify_snapshot_selection(fx.root, forged, commit)
    assert err.value.code == "committed_differs_from_tested"
    for broken in ({**record, "record_kind": "other"}, {**record, "artifacts": []},
                   {**record, "repository": "/Users/someone/repo"}):
        with pytest.raises(ReleaseRefused) as err:
            verify_snapshot_selection(fx.root, broken, commit)
        assert err.value.code == "record_invalid"


# ------------------------------------------------------------------ private path leaked


def test_reference_to_gitignored_papers_is_refused(empty_fx):
    empty_fx.build(terms=terms_with(source_file_path="papers/rsoxs/secret_paper.pdf"))
    err = refused(empty_fx, "private_dependency")
    assert finding_rules(err) == ["D2"]
    assert "secret_paper" not in json.dumps([f.to_dict() for f in err.findings])  # values never echoed


def test_tracked_beamline_ops_documents_are_not_private(empty_fx):
    """O-G1: the beamline ops docs are tracked; referencing them is fine (only gitignored papers are private)."""
    doc = empty_fx.root / "papers/rsoxs/beamline_blueprint/blueprint_bl1101.html"
    doc.parent.mkdir(parents=True)
    doc.write_text("<html></html>", encoding="utf-8")
    empty_fx.git("add", str(doc.relative_to(empty_fx.root)))
    empty_fx.git("commit", "-q", "-m", "ops doc")
    empty_fx.build(terms=terms_with(source_file_path="papers/rsoxs/beamline_blueprint/blueprint_bl1101.html"))
    assert prepare(empty_fx)["private_dependencies"]["findings"] == []


def test_source_rag_index_parsed_bodies_and_crops_are_refused(empty_fx):
    empty_fx.build(terms=terms_with(source_file_path="storage/source_index/rsoxs/chunks.jsonl",
                                    repo_url="see workdir/parsed/paper_alpha/paper.json",
                                    code_snippet="data:image/png;base64,iVBORw0KGgo="))
    err = refused(empty_fx, "private_dependency")
    assert finding_rules(err) == ["D1", "D3", "D4"]


def test_full_parsed_bodies_local_paths_and_credentials_in_the_graph_are_refused(empty_fx):
    empty_fx.build(terms=terms_with(source_file_path="/Users/someone/papers/a.pdf"))
    err = refused(empty_fx, "disclosure_failed")
    assert finding_rules(err) == ["R4"]
    other = type(empty_fx)(empty_fx.root, empty_fx.tmp)
    other.build(corpus="secrets", terms=terms_with(repo_url="https://x.org/f?token=abc&api_key=abcd1234efgh5678"))
    err = refused(other, "disclosure_failed")
    assert {"R3", "R5"} <= set(finding_rules(err))


def test_gitignored_or_untracked_manifest_references_are_refused(empty_fx):
    empty_fx.build(terms_ref="storage/terminology/extracted_terms_synthetic.json")
    err = refused(empty_fx, "private_dependency")
    assert [(f.rule, f.location) for f in err.findings] == [("D2", "manifest:inputs[0].ref")]
    other = type(empty_fx)(empty_fx.root, empty_fx.tmp)
    other.build(corpus="untracked", terms_ref="inputs/terms.json")
    err = refused(other, "private_dependency", allow_untracked=True)
    assert [(f.rule, f.location) for f in err.findings] == [("D5", "manifest:inputs[0].ref")]


def test_local_path_or_secret_in_manifest_is_refused(fx):
    fx.rewrite_manifest(created__run_id="/home/someone/runs/1")
    err = refused(fx, "private_dependency")
    assert [(f.rule, f.location) for f in err.findings] == [("R4", "manifest:created.run_id")]
    fx.rewrite_manifest(created__run_id="password=hunter22")
    assert finding_rules(refused(fx, "private_dependency")) == ["R3"]


def test_extra_files_in_the_snapshot_directory_are_refused(fx):
    (fx.root / fx.rel / "crop.png").write_bytes(b"\x89PNG")
    refused(fx, "layout")


def test_reviewed_allowlist_keys_permit_a_specific_value_only(empty_fx):
    path = "storage/terminology/legacy_terms.json"
    empty_fx.build(terms=terms_with(source_file_path=path))
    err = refused(empty_fx, "private_dependency", allowlist={"0" * 64})
    assert finding_rules(err) == ["D2"]
    node_id = next(n["id"] for n in json.loads(empty_fx.snapshot.graph_path.read_text("utf-8"))["things"]
                   if n.get("source_file_path") == path)
    key = releases.allowlist_key("graph.json", node_id, "source_file_path", path)
    assert prepare(empty_fx, allowlist={key})["private_dependencies"]["status"] == "pass"


# ------------------------------------------------------------------ schema binding


def test_missing_schema_fingerprint_is_refused(fx):
    fx.rewrite_manifest(schema__import_fingerprint=None)
    refused(fx, "schema_fingerprint_missing")


def test_mismatched_schema_fingerprint_is_refused(fx):
    fx.rewrite_manifest(schema__import_fingerprint="1" * 64)
    refused(fx, "schema_fingerprint_mismatch")


def test_schema_file_changed_in_the_repository_is_refused(fx):
    schema = fx.root / "storage/schema/matkg_schema.yaml"
    schema.write_bytes(schema.read_bytes() + b"\n# edited\n")
    fx.git("commit", "-q", "-am", "edit schema after the build")
    refused(fx, "schema_mismatch")


def test_unregistered_schema_name_is_refused(fx):
    fx.rewrite_manifest(schema__name="no_such_schema")
    refused(fx, "schema_unregistered")


def test_validation_json_must_be_present_and_agree(fx):
    fx.snapshot.validation_path.chmod(0o644)
    fx.snapshot.validation_path.unlink()
    refused(fx, "validation_missing")


def test_pending_review_schema_set_binds_only_with_explicit_selection(empty_fx):
    registry = empty_fx.registry()
    pending = next(e for e in registry.set_entries("0.3.0") if e.is_linkml and not e.known_broken)
    empty_fx.build(schema_id=pending.schema_id, allow_pending_review_schema=True)
    refused(empty_fx, "schema_pending_review")
    record = prepare(empty_fx, allow_pending_review_schema=True)
    assert record["schema"]["schema_set"] == "0.3.0" and record["schema"]["review_status"] != "in_use"


# ------------------------------------------------------------------ binary report bound to another snapshot


def test_report_for_a_different_snapshot_is_refused(fx):
    other = {"kind": "kg_snapshot", "corpus": "synthetic", "snapshot_id": "v1", "graph_sha256": "a" * 64,
             "manifest_sha256": releases.sha256_bytes(fx.snapshot.manifest_path.read_bytes())}
    refused(fx, "binary_report_mismatch", fx.report(subject=other))
    good = json.loads(fx.report().read_text("utf-8"))["subject"]
    refused(fx, "binary_report_mismatch", fx.report(subject={**good, "snapshot_id": "v2"}))
    refused(fx, "binary_report_mismatch", fx.report(subject={**good, "manifest_sha256": "b" * 64}))


def test_report_without_a_subject_or_of_another_kind_is_refused(fx):
    refused(fx, "binary_report_unbound", fx.report(subject=None))
    refused(fx, "binary_report_invalid", fx.report(report_kind="something_else"))
    refused(fx, "binary_report_invalid", fx.report(status="MAYBE"))
    with pytest.raises(ReleaseRefused) as err:
        validate_commit_candidate(fx.root, fx.rel, binary_report=fx.report().read_bytes(),
                                  binary_report_ref="/Users/someone/report.json")
    assert err.value.code == "binary_report_invalid"


@pytest.mark.parametrize("status,extra,verdict", [
    ("NOT_PASS", {}, "NOT_PASS"),
    ("PROVISIONAL", {}, "PROVISIONAL"),
    ("PASS", {"results": [{"case_id": "X", "required": True, "execution_state": "completed",
                           "verdict": "PASS", "provisional": True}]}, "PROVISIONAL"),
    ("PASS", {"gate": {"status": "PASS", "provisional": True}}, "PROVISIONAL"),
    ("PASS", {"results": [{"case_id": "X", "required": True, "execution_state": "skipped",
                           "verdict": None}]}, "NOT_PASS"),
    ("PROVISIONAL", {"gate": {"status": "PROVISIONAL", "blocking_cases": [{"case_id": "X"}]}}, "NOT_PASS"),
])
def test_provisional_and_not_pass_verdicts_are_recorded_and_never_upgraded(fx, status, extra, verdict):
    record = prepare(fx, fx.report(status=status, **extra))
    assert record["binary_report"]["verdict"] == verdict and record["binary_report"]["passed"] is False
    commit = fx.run_owner_commands(record)
    descriptor = build_data_release_descriptor(verify_snapshot_selection(fx.root, record, commit),
                                               importer_name="i", importer_version="1")
    assert descriptor["staging"]["binary_report"]["passed"] is False and descriptor["state"] == "candidate"
    forged = json.loads(json.dumps(record))
    forged["binary_report"]["passed"] = True  # a hand-edited flag does not turn the verdict into PASS
    descriptor = build_data_release_descriptor(verify_snapshot_selection(fx.root, forged, commit),
                                               importer_name="i", importer_version="1")
    assert descriptor["staging"]["binary_report"]["passed"] is False


# ------------------------------------------------------------------ dirty working tree


def test_modified_tracked_file_is_a_dirty_tree(fx):
    (fx.root / "README.md").write_text("changed\n", encoding="utf-8")
    refused(fx, "dirty_worktree")


def test_unrelated_staged_change_is_a_dirty_tree(fx):
    (fx.root / "notes.txt").write_text("x\n", encoding="utf-8")
    fx.git("add", "notes.txt")
    refused(fx, "dirty_worktree", allow_untracked=True)


def test_untracked_files_need_an_explicit_allowance_and_are_counted(fx):
    (fx.root / "scratch.txt").write_text("x\n", encoding="utf-8")
    refused(fx, "dirty_worktree")
    assert prepare(fx, allow_untracked=True)["git"]["untracked_outside_candidate"] == 1


def test_staged_candidate_with_other_bytes_is_refused(fx):
    graph = f"{fx.rel}/graph.json"
    original = fx.snapshot.graph_path.read_bytes()
    fx.snapshot.graph_path.chmod(0o644)
    fx.snapshot.graph_path.write_bytes(original + b" ")
    fx.git("add", "-f", graph)
    fx.snapshot.graph_path.write_bytes(original)  # the working tree is restored, the index is not
    refused(fx, "staged_bytes_differ")


def test_committed_snapshot_with_other_bytes_is_never_overwritten(fx):
    """A candidate path that Git already holds with other bytes is a frozen-overwrite, never a new commit."""
    path = f"{fx.rel}/graph.json"
    blob = git_input(fx.root, b"{}", "hash-object", "-w", "--stdin")
    fx.git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
    fx.git("commit", "-q", "-m", "someone committed other bytes at the frozen path")
    refused(fx, "frozen_overwrite")


def test_git_attributes_that_would_change_the_stored_bytes_are_refused(fx):
    (fx.root / ".gitattributes").write_text("*.json filter=reverse\n", encoding="utf-8")
    fx.git("config", "filter.reverse.clean", "rev")
    fx.git("add", ".gitattributes")
    fx.git("commit", "-q", "-m", "attributes")
    refused(fx, "filter_would_change_bytes")


def test_a_subdirectory_or_non_repository_is_refused(fx, tmp_path):
    with pytest.raises(ReleaseRefused) as err:
        validate_commit_candidate(fx.root / "storage", "storage/kg/synthetic/v1", binary_report=b"{}",
                                  binary_report_ref="private:x")
    assert err.value.code == "not_a_work_tree"
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ReleaseRefused) as err:
        validate_commit_candidate(plain, "storage/kg/synthetic/v1", binary_report=b"{}",
                                  binary_report_ref="private:x")
    assert err.value.code == "not_a_work_tree"
    for bad in ("/storage/kg/synthetic/v1", "storage/kg/synthetic", "papers/x", "storage/kg/../kg/synthetic/v1"):
        with pytest.raises(ReleaseRefused) as err:
            validate_commit_candidate(fx.root, bad, binary_report=b"{}", binary_report_ref="private:x")
        assert err.value.code == "bad_path"

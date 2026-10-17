"""T26B evidence availability (locators resolve or are recorded unavailable; required private assets gate
promotion) and the prepare_kg_release.py CLI end to end in an isolated Git fixture."""
from __future__ import annotations

import json

import pytest

from app.modules.graph import releases
from app.modules.graph.releases import (
    ReleaseRefused,
    check_evidence_availability,
    lookup_snapshot,
    validate_commit_candidate,
    verify_release_dependencies,
)
from scripts import prepare_kg_release as cli
from tests.releases.conftest import REPOSITORY, terms_with

PAPER = b"%PDF-1.4 synthetic alpha"


def _store(tmp_path, data=PAPER):
    root = tmp_path / "private-store"
    (root / "papers").mkdir(parents=True, exist_ok=True)
    (root / "papers" / "paper_alpha.pdf").write_bytes(data)
    return root


def _inventory(**entry):
    base = {"ref": "private:papers/paper_alpha.pdf", "sha256": releases.sha256_bytes(PAPER)}
    base.update(entry)
    return {"sources": {"paper_alpha.pdf": base}}


def _rows(report):
    return {row["source"]: row for row in report.sources}


def _check(fx, **kwargs):
    graph = json.loads(fx.snapshot.graph_path.read_text("utf-8"))
    return check_evidence_availability(fx.root, graph, fx.snapshot.manifest, **kwargs)


def test_locators_resolve_against_a_mounted_private_store(fx, tmp_path):
    report = _check(fx, inventory=_inventory(), evidence_root=_store(tmp_path))
    row = _rows(report)["paper_alpha.pdf"]
    assert row["status"] == "available" and row["locators"] >= 1 and row["kind"] == "private"
    assert report.to_dict()["report_kind"] == "f2w.kg_evidence_availability"


def test_unresolvable_locators_are_recorded_as_unavailable_not_dropped(fx, tmp_path):
    no_inventory = _check(fx)
    assert no_inventory.status == "incomplete"
    assert {r["reason"] for r in no_inventory.sources} == {"not_in_inventory"}
    no_root = _rows(_check(fx, inventory=_inventory()))["paper_alpha.pdf"]
    assert (no_root["status"], no_root["reason"]) == ("unavailable", "no_evidence_root")
    empty = tmp_path / "empty-store"
    empty.mkdir()
    missing = _rows(_check(fx, inventory=_inventory(), evidence_root=empty))["paper_alpha.pdf"]
    assert (missing["status"], missing["reason"]) == ("unavailable", "missing_at_evidence_root")
    # prepare still succeeds and the record carries every unavailable locator
    candidate = validate_commit_candidate(fx.root, fx.rel, binary_report=fx.report().read_bytes(),
                                          binary_report_ref="private:b.json")
    record = releases.build_release_record(candidate, record_name="r", repository=REPOSITORY)
    assert record["evidence"]["status"] == "incomplete"
    assert record["evidence"]["counts"]["unavailable"] == len(record["evidence"]["sources"])
    assert record["evidence"]["report_sha256"] == candidate.evidence.sha256


def test_evidence_with_other_bytes_or_an_invalid_ref_is_refused(fx, tmp_path):
    root = _store(tmp_path, b"different bytes")
    assert _rows(_check(fx, inventory=_inventory(), evidence_root=root))["paper_alpha.pdf"]["status"] == "mismatch"
    with pytest.raises(ReleaseRefused) as err:
        validate_commit_candidate(fx.root, fx.rel, binary_report=fx.report().read_bytes(),
                                  binary_report_ref="private:b.json", evidence_inventory=_inventory(),
                                  evidence_root=root)
    assert err.value.code == "evidence_mismatch"
    for bad in ({"ref": "/Users/someone/papers/paper_alpha.pdf"}, {"ref": "private:../../etc/passwd"},
                {"sha256": "short"}):
        assert _rows(_check(fx, inventory=_inventory(**bad), evidence_root=root))["paper_alpha.pdf"][
            "status"] == "invalid"


def test_web_sources_are_recorded_external_and_never_fetched(empty_fx):
    empty_fx.build(terms=terms_with(source_papers=["als.lbl.gov/beamlines/11-0-1-2", "https://doi.org/10.1/x"]))
    rows = _rows(_check(empty_fx))
    assert rows["als.lbl.gov/beamlines/11-0-1-2"]["status"] == "external"
    assert rows["https://doi.org/10.1/x"]["kind"] == "web"


def test_required_private_assets_block_promotion_when_missing(fx, tmp_path):
    graph = json.loads(fx.snapshot.graph_path.read_text("utf-8"))
    inventory = _inventory(required=True)
    with pytest.raises(ReleaseRefused) as err:
        verify_release_dependencies(fx.root, graph, fx.snapshot.manifest, inventory=inventory)
    assert err.value.code == "evidence_unavailable"
    assert verify_release_dependencies(fx.root, graph, fx.snapshot.manifest, inventory=inventory,
                                       evidence_root=_store(tmp_path)).required_unavailable == []
    listed = {"sources": {}, "required_private_assets": [{"ref": "private:qa/gold.json", "sha256": "c" * 64}]}
    with pytest.raises(ReleaseRefused) as err:
        verify_release_dependencies(fx.root, graph, fx.snapshot.manifest, inventory=listed,
                                    evidence_root=_store(tmp_path))
    assert err.value.code == "evidence_unavailable"
    with pytest.raises(ReleaseRefused) as err:
        verify_release_dependencies(fx.root, graph, fx.snapshot.manifest, inventory=inventory,
                                    evidence_root=_store(tmp_path, b"tampered"))
    assert err.value.code == "evidence_mismatch"


# ------------------------------------------------------------------ CLI


def _json_head(text):
    return json.loads(text[: text.index("\n}\n") + 2] if "\n}\n" in text else text)


def test_cli_prepare_prints_owner_commands_then_verifies_and_exports(fx, tmp_path, capsys):
    out = tmp_path / "release"
    report = fx.report(status="PROVISIONAL")
    argv = ["prepare", "--repo", str(fx.root), "--snapshot", fx.rel, "--binary-report", str(report),
            "--binary-report-ref", "private:binary/r.json", "--repository", REPOSITORY, "--out", str(out)]
    assert cli.main(argv) == cli.EXIT_OK
    printed = capsys.readouterr().out
    summary = _json_head(printed)
    assert summary["status"] == "commit_ready" and summary["binary_verdict"] == "PROVISIONAL"
    assert "git push" not in printed and "never pushes" in printed
    record_path = out / summary["record"]
    record = json.loads(record_path.read_text("utf-8"))
    assert releases.sha256_bytes(record_path.read_bytes()) == summary["record_sha256"]
    assert cli.main(argv) == cli.EXIT_OK  # identical re-run: the record is not rewritten
    assert "already_present" in capsys.readouterr().out

    commit = fx.run_owner_commands(record)
    verify = ["verify-committed", "--repo", str(fx.root), "--record", str(record_path), "--commit", commit,
              "--out", str(out)]
    assert cli.main(verify) == cli.EXIT_OK
    verified = _json_head(capsys.readouterr().out)
    assert verified["status"] == "committed_equals_tested" and verified["binary_verdict"] == "PROVISIONAL"
    descriptor = json.loads((out / verified["descriptor"]).read_text("utf-8"))
    assert descriptor["staging"]["binary_report"]["passed"] is False and descriptor["data_commit"] == commit

    mount = tmp_path / "mount"
    assert cli.main(["export", "--repo", str(fx.root), "--descriptor", str(out / verified["descriptor"]),
                     "--dest", str(mount)]) == cli.EXIT_OK
    exported = _json_head(capsys.readouterr().out)
    assert {f["path"] for f in exported["files"]} == {a["path"] for a in descriptor["artifacts"]}
    assert lookup_snapshot(fx.root, commit, "synthetic", "v1").graph.data == (
        mount / fx.rel / "graph.json").read_bytes()


def test_cli_refusals_exit_2_with_the_reason_code(fx, tmp_path, capsys):
    out = tmp_path / "release"
    (fx.root / "README.md").write_text("dirty\n", encoding="utf-8")
    argv = ["prepare", "--repo", str(fx.root), "--snapshot", fx.rel, "--binary-report", str(fx.report()),
            "--binary-report-ref", "private:b.json", "--repository", REPOSITORY, "--out", str(out)]
    assert cli.main(argv) == cli.EXIT_REFUSED
    assert _json_head(capsys.readouterr().out)["code"] == "dirty_worktree"
    assert not out.exists()  # nothing written on refusal
    inside = argv[:-1] + [str(fx.root / "storage" / "kg" / "releases")]
    assert cli.main(inside) == cli.EXIT_REFUSED
    assert _json_head(capsys.readouterr().out)["code"] == "bad_path"
    fx.git("checkout", "--", "README.md")
    assert cli.main(argv) == cli.EXIT_OK
    capsys.readouterr()
    record = next(out.glob("*.release_record.json"))
    assert cli.main(["verify-committed", "--repo", str(fx.root), "--record", str(record), "--commit", "main",
                     "--out", str(out)]) == cli.EXIT_REFUSED
    assert _json_head(capsys.readouterr().out)["code"] == "bad_commit"

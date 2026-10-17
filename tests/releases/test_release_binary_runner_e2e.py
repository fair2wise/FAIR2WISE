"""Binary runner report -> KG release record, end to end (T11B x T26B).

The binary runner (fake executor, synthetic manifest, fake label set: no model, no network) produces a report
bound to a tiny synthetic snapshot through ``--subject-snapshot`` / :func:`snapshot_subject`.
``scripts/prepare_kg_release.py prepare`` then runs in an isolated temporary Git repository and the release
record must carry the runner's verdict: PASS stays PASS, a provisional result (canonical ``gate.provisional``)
gives PROVISIONAL, a failing required case gives NOT_PASS, and neither of the latter is ever upgraded. A report
bound to another snapshot is refused.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from evals.binary import reports
from evals.binary.labeling.basis import LabelSet, PaperLabels
from evals.binary.runner import Runner, SubjectError, main as runner_main, snapshot_subject
from scripts import prepare_kg_release as cli
from tests.evals_binary import binary_helpers as H
from tests.releases.conftest import REPOSITORY, Fixture, make_repo, terms_with, write_json

GATE_OPEN = {"H3": "approved"}
LABELLER = "fake-labeller"


def _labels(**status: str) -> LabelSet:
    return LabelSet({pid: PaperLabels(pid, s, labeler_models=(LABELLER,)) for pid, s in status.items()},
                    loaded=True)


def _report_file(fx: Fixture, report: Dict[str, Any], name: str) -> Path:
    """Write the runner's own JSON/JUnit/Markdown outside the repository; return the JSON path."""
    out = fx.tmp / "runner-out"
    reports.write_all(report, out, name)
    return out / f"{name}.json"


def _prepare(fx: Fixture, report: Path, capsys) -> Dict[str, Any]:
    out = fx.tmp / "release"
    argv = ["prepare", "--repo", str(fx.root), "--snapshot", fx.rel, "--binary-report", str(report),
            "--binary-report-ref", "private:binary/report.json", "--repository", REPOSITORY, "--out", str(out)]
    code = cli.main(argv)
    printed = capsys.readouterr().out
    head = json.loads(printed[: printed.index("\n}\n") + 2] if "\n}\n" in printed else printed)
    head["exit"] = code
    if code == cli.EXIT_OK:
        head["record_doc"] = json.loads((out / head["record"]).read_text("utf-8"))
    return head


def _run(fx: Fixture, cases: List[Dict[str, Any]], outcomes: Dict[str, Any], labels: LabelSet = None):
    manifest = H.make_manifest(cases, human=GATE_OPEN)
    executor = H.FakeExecutor({"primary:" + cid: o for cid, o in outcomes.items()})
    runner = Runner(manifest, executor=executor, now=H.FIXED_NOW, revision=H.NO_REVISION, labels=labels)
    report = runner.run()
    report["subject"] = snapshot_subject(fx.root / fx.rel)
    return report


def _approved(cid: str, **kw) -> Dict[str, Any]:
    return H.make_case(cid, approval="approved", **kw)


SCENARIOS = {
    # every required case completed PASS on gold (or no) labels: the only path to a PASS record
    "PASS": dict(cases=[_approved("AUTH-01")], outcomes={"AUTH-01": H.passed()}, labels=None,
                 gate="PASS", provisional=False),
    # the gate passes but a (non-required) result rests on provisional model labels: capped at PROVISIONAL
    "PROVISIONAL": dict(cases=[_approved("AUTH-01"), _approved("EXT-02", required=False)],
                        outcomes={"AUTH-01": H.passed(), "EXT-02": H.passed()},
                        labels=dict(A="provisional", B="gold", C="gold"), gate="PASS", provisional=True),
    # a required case fails: NOT_PASS
    "NOT_PASS": dict(cases=[_approved("AUTH-01"), _approved("AUTH-02")],
                     outcomes={"AUTH-01": H.passed(), "AUTH-02": H.failed()}, labels=None,
                     gate="NOT_PASS", provisional=False),
    # a required case PASSes on provisional labels: the runner's gate is NOT_PASS and stays NOT_PASS
    "NOT_PASS_PROVISIONAL": dict(cases=[_approved("EXT-02")], outcomes={"EXT-02": H.passed()},
                                 labels=dict(A="provisional", B="gold", C="gold"), gate="NOT_PASS",
                                 provisional=True),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_runner_report_verdict_reaches_the_release_record_and_is_never_upgraded(fx, capsys, name):
    sc = SCENARIOS[name]
    labels = _labels(**sc["labels"]) if sc["labels"] else None
    report = _run(fx, sc["cases"], sc["outcomes"], labels)
    assert report["gate"]["status"] == sc["gate"]
    assert report["gate"]["provisional"] is sc["provisional"]
    assert report["subject"]["kind"] == "kg_snapshot"
    assert (report["subject"]["corpus"], report["subject"]["snapshot_id"]) == ("synthetic", "v1")

    head = _prepare(fx, _report_file(fx, report, name.lower()), capsys)
    assert head["exit"] == cli.EXIT_OK, head
    expected = "NOT_PASS" if name.startswith("NOT_PASS") else name
    binary = head["record_doc"]["binary_report"]
    assert head["binary_verdict"] == binary["verdict"] == expected
    assert binary["passed"] is (expected == "PASS")
    assert binary["gate_status"] == sc["gate"]
    assert binary["subject"] == report["subject"]
    if sc["provisional"]:
        assert "provisional_results" in binary["verdict_reasons"]


def test_a_report_bound_to_another_snapshot_is_refused(fx, tmp_path, capsys):
    other = Fixture(make_repo(tmp_path, "other-repo"), tmp_path / "other")
    other.tmp.mkdir()
    other.build(terms=terms_with(definition="a different synthetic definition"))  # same synthetic/v1 id
    pass_case = [_approved("AUTH-01")]
    report = _run(other, pass_case, {"AUTH-01": H.passed()})
    assert report["gate"]["status"] == "PASS"
    head = _prepare(fx, _report_file(fx, report, "other_bytes"), capsys)
    assert head["exit"] == cli.EXIT_REFUSED and head["code"] == "binary_report_mismatch"

    report = _run(fx, pass_case, {"AUTH-01": H.passed()})
    report["subject"]["snapshot_id"] = "v2"
    head = _prepare(fx, _report_file(fx, report, "other_id"), capsys)
    assert head["exit"] == cli.EXIT_REFUSED and head["code"] == "binary_report_mismatch"


def _cases_dir(tmp: Path) -> Path:
    """A synthetic cases directory: one required case blocked on a missing capability (no oracle runs)."""
    manifest = H.make_manifest([H.make_case("AUTH-01", readiness="blocked")], human=GATE_OPEN)
    root = tmp / "cases"
    root.mkdir()
    write_json(root / "index.json", dict(manifest.header, case_files=["synthetic.json"]))
    write_json(root / "synthetic.json", {"cases": manifest.cases})
    return root


def test_runner_cli_subject_option_binds_the_report_and_is_optional(fx, tmp_path, capsys):
    cases = _cases_dir(tmp_path)
    out = tmp_path / "cli-out"
    assert runner_main(["--cases-dir", str(cases), "--out-dir", str(out), "--basename", "plain"]) == 1
    capsys.readouterr()
    plain = json.loads((out / "plain.json").read_text("utf-8"))
    assert "subject" not in plain and plain["gate"]["provisional"] is False  # no option: no subject
    head = _prepare(fx, out / "plain.json", capsys)
    assert head["exit"] == cli.EXIT_REFUSED and head["code"] == "binary_report_unbound"

    assert runner_main(["--cases-dir", str(cases), "--out-dir", str(out), "--basename", "bound",
                        "--subject-snapshot", str(fx.root / fx.rel)]) == 1
    capsys.readouterr()
    bound = json.loads((out / "bound.json").read_text("utf-8"))
    assert bound["subject"] == snapshot_subject(fx.root / fx.rel)
    assert str(fx.root) not in (out / "bound.json").read_text("utf-8")  # no local path in the report
    assert "Subject: kg_snapshot `synthetic/v1`" in (out / "bound.md").read_text("utf-8")
    head = _prepare(fx, out / "bound.json", capsys)
    assert head["exit"] == cli.EXIT_OK and head["binary_verdict"] == "NOT_PASS"


def test_runner_cli_refuses_a_subject_that_is_not_a_verified_snapshot(fx, tmp_path, capsys):
    cases = _cases_dir(tmp_path)
    with pytest.raises(SubjectError):
        snapshot_subject(fx.root / "storage" / "kg" / "synthetic")
    graph = fx.snapshot.graph_path
    graph.chmod(0o644)
    graph.write_bytes(graph.read_bytes() + b" ")
    rc = runner_main(["--cases-dir", str(cases), "--out-dir", str(tmp_path / "o"),
                      "--subject-snapshot", str(fx.root / fx.rel)])
    assert rc == 2 and "subject snapshot does not verify: tampered" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()

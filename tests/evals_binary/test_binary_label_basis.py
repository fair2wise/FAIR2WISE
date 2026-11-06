"""Runner support for pilot labels (H3 B4) and the recorded judge (O-U2): fake executors only."""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import binary_helpers as H  # noqa: E402
import label_fakes as F  # noqa: E402
from evals.binary import reports  # noqa: E402
from evals.binary.labeling import cli  # noqa: E402
from evals.binary.labeling.basis import LabelSet, PaperLabels, load_label_set  # noqa: E402
from evals.binary.labeling.roles import RoleSeparationError  # noqa: E402
from evals.binary.model import load_manifest  # noqa: E402
from evals.binary.runner import Runner, main  # noqa: E402

GATE_OPEN = {"H3": "approved"}


def labels(**status):
    return LabelSet({pid: PaperLabels(pid, s, labeler_models=(F.LABELER,) if s != "missing" else ())
                     for pid, s in status.items()}, loaded=True)


def run(case, labelset=None, outcome=None, **kw):
    manifest = H.make_manifest([case], human=GATE_OPEN)
    executor = H.FakeExecutor({"primary:" + case["case_id"]: outcome or H.passed()})
    runner = Runner(manifest, executor=executor, now=H.FIXED_NOW, revision=H.NO_REVISION, labels=labelset, **kw)
    return runner.run()


def approved(cid, **kw):
    return H.make_case(cid, approval="approved", **kw)


def test_a_pass_on_provisional_labels_is_reported_provisional_and_never_passes_the_gate():
    report = run(approved("EXT-02"), labels(A="provisional", B="gold", C="gold"))
    row = report["results"][0]
    assert row["verdict"] == "PASS" and row["reported_verdict"] == "PROVISIONAL"
    assert row["label_basis"]["status"] == "provisional"
    assert report["gate"]["status"] == "NOT_PASS"
    reasons = report["gate"]["blocking_cases"][0]["reasons"]
    assert "verdict:PROVISIONAL" in reasons and "pilot_labels:provisional" in reasons


def test_a_fail_on_provisional_labels_is_also_provisional():
    report = run(approved("EXT-02"), labels(A="gold", B="gold", C="provisional"), outcome=H.failed())
    assert report["results"][0]["reported_verdict"] == "PROVISIONAL"


def test_gold_labels_let_a_pass_count():
    report = run(approved("EXT-02"), labels(A="gold", B="gold", C="gold"))
    assert report["results"][0]["reported_verdict"] == "PASS"
    assert report["gate"]["status"] == "PASS"


def test_missing_labels_keep_a_label_case_blocked():
    case = approved("EXT-01", readiness="blocked", coverage="none", blocked_by=["B4 pilot labels"])
    report = run(case)
    row = report["results"][0]
    assert row["execution_state"] == "blocked" and row["verdict"] is None
    assert "pilot_labels_missing" in row["reason_codes"]
    assert "pilot_labels:missing" in report["gate"]["blocking_cases"][0]["reasons"]


def test_missing_labels_block_the_gate_even_when_a_synthetic_oracle_passes():
    report = run(approved("EXT-04"))
    assert report["results"][0]["reported_verdict"] == "PASS"  # what ran is the synthetic oracle
    assert "pilot_labels:missing" in report["gate"]["blocking_cases"][0]["reasons"]
    assert report["gate"]["status"] == "NOT_PASS"


def test_local_02_needs_any_one_labelled_paper():
    basis = labels(A="gold", B="missing", C="missing").case_basis("LOCAL-02")
    assert basis == {"status": "gold", "rule": "any", "papers": {"A": "gold", "B": "missing", "C": "missing"}}
    assert labels(A="gold", B="missing", C="missing").case_basis("EXT-02")["status"] == "missing"


def test_non_label_cases_have_no_label_basis():
    report = run(approved("AUTH-01"))
    assert report["results"][0]["label_basis"] is None and report["gate"]["status"] == "PASS"


def test_judge_and_model_under_test_are_recorded_in_every_report():
    report = run(approved("AUTH-01"), labels(A="provisional"), judge_model=F.JUDGE, judge_model_source="settings",
                 model_under_test=F.MUT)
    model = report["model"]
    assert (model["judge_model"], model["judge_model_source"], model["model_under_test"]) == (
        F.JUDGE, "settings", F.MUT)
    assert model["labeler_models"] == [F.LABELER]
    junit = ET.fromstring(reports.to_junit(report))
    props = {p.get("name"): p.get("value") for p in junit.find("properties")}
    assert props["judge_model"] == F.JUDGE and props["model_under_test"] == F.MUT
    assert F.JUDGE in reports.to_markdown(report)


@pytest.mark.parametrize("kw", [{"judge_model": F.LABELER}, {"model_under_test": F.LABELER},
                                {"judge_model": "same", "model_under_test": "same"}])
def test_runs_violating_role_separation_are_refused(kw):
    with pytest.raises(RoleSeparationError):
        run(approved("AUTH-01"), labels(A="provisional"), **kw)


def test_provisional_is_a_skipped_junit_case_never_a_pass():
    report = run(approved("EXT-02"), labels(A="provisional", B="gold", C="gold"))
    case = ET.fromstring(reports.to_junit(report)).find(".//testcase")
    skipped = case.find("skipped")
    assert skipped is not None and skipped.get("type") == "provisional_labels"
    assert "PROVISIONAL" in reports.to_markdown(report)


def test_report_carries_no_quotes_or_paths_from_the_label_dir(monkeypatch, tmp_path):
    F.install_synthetic(monkeypatch, tmp_path)
    monkeypatch.delenv("CBORG_API_KEY", raising=False)
    out = tmp_path / "labels"
    assert cli.main(["--model", F.LABELER, "--paper", "A", "--papers-root", str(tmp_path / "papers"), "--out",
                     str(out)], client_factory=F.fake_factory(), lister=F.lister()) == 0
    labelset = load_label_set(out)
    assert labelset.papers["A"].status == "provisional" and labelset.papers["B"].status == "missing"
    report = run(approved("EXT-02"), labelset)
    text = json.dumps(report) + reports.to_markdown(report) + reports.to_junit(report)
    assert "lamellar spacing of film X" not in text and str(out) not in text


def test_gold_from_another_pdf_is_not_gold(monkeypatch, tmp_path):
    out = tmp_path / "labels"
    (out / "A").mkdir(parents=True)
    (out / "A" / "gold.json").write_text(json.dumps({
        "status": "gold", "complete": True, "reviewer": "R", "items": [], "labeling_calls_complete": True,
        "provenance": [{"source_sha256": "0" * 64, "labeler_model": F.LABELER, "guide_version": "g"}]}))
    assert load_label_set(out).papers["A"].status == "missing"


def test_runner_cli_reads_the_judge_from_the_settings_file_and_refuses_role_clash(tmp_path, capsys):
    from app.modules.validation_settings import JudgeModelStore, judge_settings_path

    JudgeModelStore(judge_settings_path(tmp_path / "wd")).write("judge-x", updated_by="local-operator")
    rc = main(["--out-dir", str(tmp_path / "r"), "--only", "AUTH-01", "--settings-workdir", str(tmp_path / "wd"),
               "--model-under-test", "judge-x"])
    assert rc == 2 and "refused" in capsys.readouterr().err


def test_generated_label_cases_name_their_pilot_papers_and_the_b4_blocker():
    manifest = load_manifest()
    for cid in ("EXT-01", "EXT-02", "EXT-03", "EXT-04", "FIG-01", "FIG-02", "FIG-03", "QA-01", "QA-02", "QA-03",
                "QA-04", "OPS-04", "LOCAL-02"):
        case = manifest.by_id[cid]
        assert case["pilot_labels"]["papers"], cid
        # H3 recorded by the owner (2026-11-06): the rule text is approved; readiness still waits on the B4 labels.
        assert case["owner_approval"]["status"] == "approved"
        if case["readiness"] != "executable":
            assert any(b.startswith("H3 B4 pilot labels") for b in case["blocked_by"]), cid
    assert "pilot_labels" not in manifest.by_id["AUTH-01"]

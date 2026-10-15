"""Rubric adapter (judge may only propose; malformed is judge_invalid) and report formats."""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import binary_helpers as H  # noqa: E402
from evals.binary import reports  # noqa: E402
from evals.binary.assertions.base import Outcome  # noqa: E402
from evals.binary.assertions.rubric import RubricExecutor, parse_judgment  # noqa: E402
from evals.binary.runner import Runner  # noqa: E402

IDS = ["C1", "C2"]


def good(verdict="PASS", met=(True, True)):
    return json.dumps(
        {"verdict": verdict, "criteria": [{"id": i, "met": m, "evidence": "loc"} for i, m in zip(IDS, met)]}
    )


def test_parse_judgment_accepts_consistent_output_only():
    assert parse_judgment(good(), IDS) == ("PASS", [])
    assert parse_judgment(good("FAIL", (True, False)), IDS) == ("FAIL", [])
    for raw in (
        "not json",
        "[]",
        "{}",
        good("PASS", (True, False)),
        good("FAIL", (True, True)),
        json.dumps({"verdict": "UNRESOLVED", "criteria": []}),
        json.dumps({"verdict": "PASS", "criteria": [{"id": "C1", "met": "yes"}, {"id": "C2", "met": True}]}),
        json.dumps({"verdict": "PASS", "criteria": [{"id": "C1", "met": True}]}),
        json.dumps({"verdict": "MAYBE", "criteria": []}),
    ):
        verdict, problems = parse_judgment(raw, IDS)
        assert verdict is None and problems, raw


def rubrics(status):
    return {"RUB": {"rubric_id": "RUB", "approval_status": status, "criteria": [{"id": i} for i in IDS]}}


def case():
    return H.make_case("Q-01", readiness="needs_h3_rubric", rubric_ref="RUB")


def test_draft_rubric_blocks_even_with_a_judge_that_says_pass():
    out = RubricExecutor(rubrics("draft"), judge=lambda *a: good()).run(case())
    assert (out.execution_state, out.verdict) == ("blocked", None) and out.reason_codes == ["needs_h3_rubric"]


def test_approved_rubric_without_a_judge_is_blocked_not_passed():
    out = RubricExecutor(rubrics("approved")).run(case())
    assert (out.execution_state, out.verdict) == ("blocked", None) and out.reason_codes == ["no_judge_configured"]


def test_approved_rubric_valid_judgment_completes_and_malformed_is_judge_invalid():
    ok = RubricExecutor(rubrics("approved"), judge=lambda *a: good()).run(case())
    assert (ok.execution_state, ok.verdict) == ("completed", "PASS")
    bad = RubricExecutor(rubrics("approved"), judge=lambda *a: "I think it is fine").run(case())
    assert (bad.execution_state, bad.verdict) == ("judge_invalid", None)
    unresolved = RubricExecutor(
        rubrics("approved"), judge=lambda *a: json.dumps({"verdict": "UNRESOLVED", "criteria": []})
    ).run(case())
    assert unresolved.execution_state == "judge_invalid"


def test_judge_exception_is_error_not_fail():
    def boom(*a):
        raise TimeoutError("provider timeout")

    out = RubricExecutor(rubrics("approved"), judge=boom).run(case())
    assert (out.execution_state, out.verdict) == ("error", None)


def sample_report():
    cases = [
        H.make_case("A-01"),
        H.make_case("A-02"),
        H.make_case("A-03"),
        H.make_case("B-01", readiness="blocked", blocked_by=["T08B"]),
        H.make_case("B-02", readiness="blocked", blocked_by=["T10"]),
    ]
    outcomes = {
        "primary:A-01": H.passed(),
        "primary:A-02": H.failed(),
        "primary:A-03": Outcome("skipped", actual="no tiled"),
    }
    manifest = H.make_manifest(cases)
    return Runner(manifest, executor=H.FakeExecutor(outcomes), now=H.FIXED_NOW, revision=H.NO_REVISION).run()


def test_junit_maps_fail_to_failure_and_noncompleted_to_skipped_or_error():
    root = ET.fromstring(reports.to_junit(sample_report()))
    assert root.find("properties/property[@name='gate_status']").get("value") == "NOT_PASS"
    cases = {c.get("name"): c for c in root.iter("testcase")}
    assert cases["A-01"].find("failure") is None and cases["A-01"].find("skipped") is None
    assert cases["A-02"].find("failure") is not None
    assert cases["A-03"].find("skipped") is not None
    assert cases["B-01"].find("skipped") is not None
    assert int(root.get("tests")) == 5 and int(root.get("failures")) == 1


def test_junit_marks_timeout_error_and_missing_fixture_as_errors():
    cases = [H.make_case(f"E-0{i}") for i in range(3)]
    outcomes = {
        "primary:E-00": Outcome("timeout"),
        "primary:E-01": Outcome("error"),
        "primary:E-02": Outcome("missing_fixture"),
    }
    report = Runner(
        H.make_manifest(cases), executor=H.FakeExecutor(outcomes), now=H.FIXED_NOW, revision=H.NO_REVISION
    ).run()
    root = ET.fromstring(reports.to_junit(report))
    assert all(c.find("error") is not None for c in root.iter("testcase"))


def test_json_and_markdown_keep_verdict_and_state_separate_and_list_every_case():
    report = sample_report()
    data = json.loads(reports.to_json(report))
    by = {r["case_id"]: r for r in data["results"]}
    assert by["A-01"]["verdict"] == "PASS" and by["A-01"]["execution_state"] == "completed"
    assert by["A-03"]["verdict"] is None and by["A-03"]["execution_state"] == "skipped"
    text = reports.to_markdown(report)
    assert "Gate status: NOT_PASS" in text
    assert all(cid in text for cid in by)
    assert "blocked by T08B" in text


def test_write_all_creates_three_reports(tmp_path):
    paths = reports.write_all(sample_report(), tmp_path, "r")
    assert sorted(p.name for p in paths) == ["r.json", "r.md", "r.xml"]

"""Gate and execution semantics of the binary runner (fake executors only)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import binary_helpers as H  # noqa: E402
from evals.binary import reports  # noqa: E402
from evals.binary.assertions.base import Outcome  # noqa: E402
from evals.binary.runner import (  # noqa: E402
    RepeatPolicyError,
    Runner,
    aggregate_runs,
    compute_gate,
    validate_repeat_policy,
)


def run(cases, outcomes, **kw):
    manifest = H.make_manifest(cases, **{k: v for k, v in kw.items() if k in ("human", "policy")})
    executor = H.FakeExecutor(outcomes)
    runner = Runner(manifest, executor=executor, now=H.FIXED_NOW, revision=H.NO_REVISION)
    return runner.run(only=kw.get("only")), executor


def approved_case(cid, **kw):
    return H.make_case(cid, approval="approved", **kw)


def test_all_approved_full_pass_with_approved_h3_is_the_only_pass():
    cases = [approved_case("A-01"), approved_case("A-02")]
    report, _ = run(cases, {"primary:A-01": H.passed(), "primary:A-02": H.passed()}, human={"H3": "approved"})
    assert report["gate"]["status"] == "PASS"
    assert report["gate"]["blocking_cases"] == []


def test_one_fail_makes_the_gate_not_pass_despite_other_passes():
    cases = [approved_case("A-01"), approved_case("A-02"), approved_case("A-03")]
    outcomes = {"primary:A-01": H.passed(), "primary:A-02": H.failed(), "primary:A-03": H.passed()}
    report, _ = run(cases, outcomes, human={"H3": "approved"})
    assert report["gate"]["status"] == "NOT_PASS"
    assert [b["case_id"] for b in report["gate"]["blocking_cases"]] == ["A-02"]
    assert report["gate"]["blocking_cases"][0]["reasons"] == ["verdict:FAIL"]


@pytest.mark.parametrize("state", ["timeout", "skipped", "missing_fixture", "error", "judge_invalid", "blocked"])
def test_non_completed_states_never_pass_and_have_no_verdict(state):
    cases = [approved_case("A-01")]
    report, _ = run(cases, {"primary:A-01": Outcome(state, actual="x")}, human={"H3": "approved"})
    row = report["results"][0]
    assert row["execution_state"] == state and row["verdict"] is None
    assert report["gate"]["status"] == "NOT_PASS"
    assert f"execution_state:{state}" in report["gate"]["blocking_cases"][0]["reasons"]


def test_a_verdict_on_a_non_completed_outcome_is_discarded():
    cases = [approved_case("A-01")]
    report, _ = run(cases, {"primary:A-01": Outcome("skipped", "PASS")}, human={"H3": "approved"})
    row = report["results"][0]
    assert row["verdict"] is None and "verdict_discarded" in row["reason_codes"]


def test_completed_without_a_verdict_is_an_error_not_a_pass():
    report, _ = run([approved_case("A-01")], {"primary:A-01": Outcome("completed")}, human={"H3": "approved"})
    assert report["results"][0]["execution_state"] == "error"
    assert report["gate"]["status"] == "NOT_PASS"


def test_proposed_cases_cannot_pass_the_gate_even_if_every_oracle_passes():
    cases = [H.make_case("A-01")]
    report, _ = run(cases, {"primary:A-01": H.passed()}, human={"H3": "approved"})
    assert report["gate"]["status"] == "NOT_PASS"
    assert "owner_approval:proposed" in report["gate"]["blocking_cases"][0]["reasons"]


def test_partial_coverage_blocks_the_gate():
    report, _ = run(
        [approved_case("A-01", coverage="partial")], {"primary:A-01": H.passed()}, human={"H3": "approved"}
    )
    assert report["gate"]["blocking_cases"][0]["reasons"] == ["oracle_coverage:partial"]


def test_unapproved_human_gate_blocks_even_when_every_case_is_clean():
    report, _ = run([approved_case("A-01")], {"primary:A-01": H.passed()}, human={"H3": "not_approved"})
    assert report["gate"]["status"] == "NOT_PASS"
    assert report["gate"]["gate_reasons"] == ["human_gate_not_approved"]


def test_optional_cases_never_rescue_a_failed_required_case():
    cases = [approved_case("A-01"), approved_case("A-02", required=False)]
    outcomes = {"primary:A-01": H.failed(), "primary:A-02": H.passed()}
    report, _ = run(cases, outcomes, human={"H3": "approved"})
    assert report["gate"]["status"] == "NOT_PASS" and report["gate"]["required_case_count"] == 1


def test_partial_run_is_never_gate_evidence_and_missing_required_cases_are_listed():
    cases = [approved_case("A-01"), approved_case("A-02")]
    report, _ = run(cases, {"primary:A-01": H.passed()}, human={"H3": "approved"}, only=["A-01"])
    assert report["gate"]["status"] == "NOT_PASS"
    assert "partial_run_not_gate_evidence" in report["gate"]["gate_reasons"]
    assert {"case_id": "A-02", "reasons": ["no_result"]} in report["gate"]["blocking_cases"]


def test_unknown_only_id_is_rejected():
    manifest = H.make_manifest([H.make_case("A-01")])
    with pytest.raises(KeyError):
        Runner(manifest, executor=H.FakeExecutor({}), now=H.FIXED_NOW, revision=H.NO_REVISION).run(only=["Z-99"])


def test_no_case_is_dropped_blocked_and_rubric_cases_appear_with_reasons():
    cases = [
        H.make_case("A-01"),
        H.make_case("B-01", readiness="blocked", blocked_by=["T08B"]),
        H.make_case("C-01", readiness="needs_h3_rubric", rubric_ref="RUB-X"),
    ]
    manifest = H.make_manifest(cases)
    report = Runner(
        manifest,
        executor=H.FakeExecutor({"primary:A-01": H.passed()}),
        now=H.FIXED_NOW,
        revision=H.NO_REVISION,
        rubrics={"RUB-X": {"rubric_id": "RUB-X", "approval_status": "draft", "criteria": []}},
    ).run()
    assert [r["case_id"] for r in report["results"]] == ["A-01", "B-01", "C-01"]
    by = {r["case_id"]: r for r in report["results"]}
    assert by["B-01"]["execution_state"] == "blocked" and "T08B" in by["B-01"]["actual"]
    assert by["C-01"]["execution_state"] == "blocked" and by["C-01"]["reason_codes"] == ["needs_h3_rubric"]
    assert report["counts"]["total"] == 3
    assert sum(report["counts"]["by_execution_state"].values()) == 3


def test_blocked_cases_do_not_invoke_the_executor_but_supporting_selectors_do_not_count():
    blocked = H.make_case("B-01", readiness="blocked", blocked_by=["T08B"])
    blocked["oracle"]["supporting_selectors"] = ["tests/fake/s.py::test_s"]
    report, executor = run([blocked], {"support:B-01": H.passed()})
    row = report["results"][0]
    assert row["execution_state"] == "blocked" and row["verdict"] is None
    assert row["supporting_evidence"]["counts_toward_verdict"] is False
    assert executor.calls == [["support:B-01"]]


def test_output_is_deterministic_and_executor_is_called_once_without_retry():
    cases = [H.make_case("A-02"), H.make_case("A-01")]
    outcomes = {"primary:A-01": H.failed(), "primary:A-02": H.passed()}
    first, executor = run(cases, outcomes)
    second, _ = run(list(reversed(cases)), outcomes)
    assert reports.to_json(first) == reports.to_json(second)
    assert reports.to_junit(first) == reports.to_junit(second)
    assert reports.to_markdown(first) == reports.to_markdown(second)
    assert len(executor.calls) == 1  # a FAIL is not retried


def test_executor_exception_is_an_error_state_not_a_verdict():
    class Boom:
        def run_selectors(self, sets):
            raise RuntimeError("provider down: TOKEN=abc123 at /Users/x/y")

    manifest = H.make_manifest([approved_case("A-01")], human={"H3": "approved"})
    report = Runner(manifest, executor=Boom(), now=H.FIXED_NOW, revision=H.NO_REVISION).run()
    row = report["results"][0]
    assert row["execution_state"] == "error" and row["verdict"] is None
    assert "abc123" not in json.dumps(report) and "/Users/x" not in json.dumps(report)


def test_python_check_registry_and_unregistered_check():
    case = H.make_case("A-01")
    case["oracle"] = {
        "kind": "python_check",
        "check": "demo",
        "selectors": [],
        "supporting_selectors": [],
        "covers": "",
        "uncovered": "",
        "rubric_ref": None,
    }
    manifest = H.make_manifest([case])
    ok = Runner(
        manifest,
        executor=H.FakeExecutor({}),
        checks={"demo": lambda c: H.passed()},
        now=H.FIXED_NOW,
        revision=H.NO_REVISION,
    ).run()
    assert ok["results"][0]["verdict"] == "PASS"
    missing = Runner(manifest, executor=H.FakeExecutor({}), now=H.FIXED_NOW, revision=H.NO_REVISION).run()
    assert missing["results"][0]["execution_state"] == "missing_fixture"


def test_oracle_drift_on_an_approved_case_blocks_the_gate(tmp_path):
    (tmp_path / "src.txt").write_text("v1", encoding="utf-8")
    from evals.binary.model import source_files_sha256

    frozen = source_files_sha256(["src.txt"], tmp_path)
    case = approved_case("A-01", oracle_hash=frozen, selectors=["src.txt::test_x"])
    manifest = H.make_manifest([case], human={"H3": "approved"})
    executor = H.FakeExecutor({"primary:A-01": H.passed()})
    clean = Runner(manifest, executor=executor, now=H.FIXED_NOW, revision=H.NO_REVISION, root=tmp_path).run()
    assert clean["gate"]["status"] == "PASS"
    (tmp_path / "src.txt").write_text("v2", encoding="utf-8")
    drift = Runner(manifest, executor=executor, now=H.FIXED_NOW, revision=H.NO_REVISION, root=tmp_path).run()
    assert drift["gate"]["status"] == "NOT_PASS"
    assert drift["gate"]["blocking_cases"][0]["reasons"] == ["oracle_drift"]


def test_required_membership_mismatch_is_flagged():
    manifest = H.make_manifest([approved_case("A-01")], human={"H3": "approved"})
    rows = [
        {
            "case_id": "A-01",
            "required": True,
            "execution_state": "completed",
            "verdict": "PASS",
            "approval_status": "approved",
            "coverage": "full",
            "hashes": {},
        },
        {
            "case_id": "X-99",
            "required": True,
            "execution_state": "completed",
            "verdict": "PASS",
            "approval_status": "approved",
            "coverage": "full",
            "hashes": {},
        },
    ]
    gate = compute_gate(manifest, rows)
    assert gate["status"] == "NOT_PASS" and gate["unexpected_required_cases"] == ["X-99"]


def test_repeat_policy_rejects_retry_undeclared_and_unknown_aggregation():
    good = dict(H.POLICY)
    assert validate_repeat_policy(good)["runs"] == 1
    for bad in (
        {**good, "retry_on_failure": True},
        {**good, "declared_before_run": False},
        {**good, "aggregation": "best_of"},
        {**good, "runs": 0},
        {**good, "runs": True},
        {**good, "runs": 99},
    ):
        with pytest.raises(RepeatPolicyError):
            validate_repeat_policy(bad)


def test_repeat_runs_all_must_pass_and_any_failure_or_noncompleted_run_wins():
    assert aggregate_runs([H.passed(), H.passed()]).verdict == "PASS"
    assert aggregate_runs([H.passed(), H.failed()]).verdict == "FAIL"
    mixed = aggregate_runs([H.passed(), Outcome("timeout", actual="t")])
    assert mixed.execution_state == "timeout" and mixed.verdict is None
    mixed = aggregate_runs([H.failed(), Outcome("skipped")])
    assert mixed.execution_state == "skipped"


def test_repeat_policy_runs_the_declared_number_of_times_and_never_more():
    cases = [approved_case("A-01")]
    report, executor = run(cases, {"primary:A-01": H.failed()}, policy={**H.POLICY, "runs": 3})
    assert len(executor.calls) == 3
    assert report["results"][0]["repeat"]["runs"] == 3 and report["results"][0]["verdict"] == "FAIL"


def test_counts_hold_no_score():
    report, _ = run(
        [H.make_case("A-01"), H.make_case("A-02")], {"primary:A-01": H.passed(), "primary:A-02": H.failed()}
    )
    assert set(report["counts"]) == {"total", "by_execution_state", "by_verdict", "by_family", "by_readiness",
                                     "by_reported_verdict", "by_label_basis"}
    assert "score" not in json.dumps(report).lower().replace("scores", "")

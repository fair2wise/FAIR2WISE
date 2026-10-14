"""Binary case runner and gate.

Usage (from the repository root, with the app environment active)::

    python -m evals.binary.runner --out-dir docs/migration/reviews/H3/evidence

Semantics (BINARY_EVALUATION_SPEC sections 2-3, plan section 12):

* ``verdict`` is PASS or FAIL and exists only for ``execution_state == completed``;
* timeouts, skips, missing fixtures, blocked cases, errors and malformed judge output
  never become a PASS and never become a scientific FAIL;
* the gate is binary: every required case must be completed, PASS, owner-approved,
  fully covered by its oracle and un-drifted. One exception makes the gate NOT_PASS;
* no aggregate score is produced, only counts;
* the repeat policy is read from the manifest header (declared in advance) and there is
  no retry-on-failure path.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from evals.binary.assertions.base import Outcome
from evals.binary.assertions.pytest_nodes import PytestNodeExecutor
from evals.binary.assertions.rubric import RubricExecutor
from evals.binary.model import (
    CASES_DIR,
    EXECUTION_STATES,
    REPO_ROOT,
    Manifest,
    canonical_json,
    load_manifest,
    oracle_files,
    sha256_text,
    source_files_sha256,
)
from evals.binary.sanitize import sanitize

RUNNER_REVISION = "t11a-runner-1"
_STATE_PRIORITY = ("error", "timeout", "judge_invalid", "missing_fixture", "skipped", "blocked")

CheckFn = Callable[[Dict[str, Any]], Outcome]


class RepeatPolicyError(ValueError):
    """The declared repeat policy is not acceptable."""


def validate_repeat_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    """Accept only a declared, non-retrying, all-runs-must-pass policy."""
    runs = policy.get("runs")
    if not isinstance(runs, int) or isinstance(runs, bool) or not 1 <= runs <= 10:
        raise RepeatPolicyError("repeat policy runs must be an integer between 1 and 10")
    if policy.get("aggregation") != "all_runs_must_pass":
        raise RepeatPolicyError("only the all_runs_must_pass aggregation is allowed")
    if policy.get("retry_on_failure") is not False:
        raise RepeatPolicyError("retry_on_failure must be false: no retry-until-green")
    if policy.get("declared_before_run") is not True:
        raise RepeatPolicyError("the repeat policy must be declared before the run")
    return dict(policy)


def aggregate_runs(outcomes: Sequence[Outcome]) -> Outcome:
    """Combine repeated runs: any non-completed run wins, then any FAIL, else PASS."""
    if len(outcomes) == 1:
        return outcomes[0]
    for state in _STATE_PRIORITY:
        bad = [o for o in outcomes if o.execution_state == state]
        if bad:
            return Outcome(
                state,
                reason_codes=sorted({c for o in bad for c in o.reason_codes}),
                actual=f"{len(bad)} of {len(outcomes)} runs {state}: {bad[0].actual}",
                evidence_refs=sorted({r for o in outcomes for r in o.evidence_refs}),
            )
    fails = [o for o in outcomes if o.verdict == "FAIL"]
    verdict = "FAIL" if fails else "PASS"
    return Outcome(
        "completed",
        verdict,
        actual=f"{len(fails)} of {len(outcomes)} runs failed" if fails else f"all {len(outcomes)} runs passed",
        reason_codes=sorted({c for o in outcomes for c in o.reason_codes}),
        evidence_refs=sorted({r for o in outcomes for r in o.evidence_refs}),
    )


def _normalise(outcome: Outcome) -> Outcome:
    """Enforce the verdict/state invariant: a verdict only ever rides on ``completed``."""
    if outcome.execution_state not in EXECUTION_STATES:
        return Outcome(
            "error",
            reason_codes=["unknown_execution_state"],
            actual=sanitize(f"oracle returned state {outcome.execution_state!r}"),
        )
    if outcome.execution_state == "completed" and outcome.verdict not in ("PASS", "FAIL"):
        return Outcome("error", reason_codes=["completed_without_verdict"], actual="completed without PASS/FAIL")
    if outcome.execution_state != "completed" and outcome.verdict is not None:
        return Outcome(
            outcome.execution_state,
            None,
            outcome.actual,
            outcome.evidence_refs,
            list(outcome.reason_codes) + ["verdict_discarded"],
            outcome.detail,
        )
    return outcome


def source_revision(root: Path = REPO_ROOT) -> Dict[str, Any]:
    """Git commit and ``app`` tree hash of the evaluated source (None when git is unavailable)."""

    def git(*args: str) -> Optional[str]:
        try:
            done = subprocess.run(
                ["git", *args], cwd=str(root), capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    status = git("status", "--porcelain", "--untracked-files=no")
    return {
        "commit": git("rev-parse", "HEAD"),
        "app_tree": git("rev-parse", "HEAD:app"),
        "dirty": None if status is None else bool(status),
    }


class Runner:
    """Execute a manifest once under its declared repeat policy and compute the gate."""

    def __init__(
        self,
        manifest: Manifest,
        executor: Optional[PytestNodeExecutor] = None,
        rubric_executor: Optional[RubricExecutor] = None,
        checks: Optional[Dict[str, CheckFn]] = None,
        rubrics: Optional[Dict[str, Dict[str, Any]]] = None,
        now: Optional[Callable[[], str]] = None,
        revision: Optional[Callable[[], Dict[str, Any]]] = None,
        root: Path = REPO_ROOT,
    ):
        self.manifest = manifest
        self.policy = validate_repeat_policy(manifest.header["repeat_policy"])
        self.executor = executor or PytestNodeExecutor()
        self.rubric_executor = rubric_executor or RubricExecutor(rubrics or {})
        self.checks = checks or {}
        self.now = now or (lambda: _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.revision = revision or (lambda: source_revision(root))
        self.root = root

    def run(self, only: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """Run the selected cases (all when ``only`` is None); the gate always covers all required cases."""
        unknown = sorted(set(only or ()) - set(self.manifest.by_id))
        if unknown:
            raise KeyError(f"unknown case ids: {unknown}")
        selected = [c for c in self.manifest.cases if only is None or c["case_id"] in only]
        started = self.now()
        per_case: Dict[str, List[Outcome]] = {c["case_id"]: [] for c in selected}
        supporting: Dict[str, List[Outcome]] = {c["case_id"]: [] for c in selected}
        for _ in range(self.policy["runs"]):
            self._one_run(selected, per_case, supporting)
        results = [
            self._result(
                c,
                aggregate_runs(per_case[c["case_id"]]),
                supporting[c["case_id"]],
                [o.execution_state for o in per_case[c["case_id"]]],
            )
            for c in selected
        ]
        gate = compute_gate(self.manifest, results, complete=only is None)
        return {
            "report_kind": "binary_evaluation_report",
            "runner_revision": RUNNER_REVISION,
            "manifest_version": self.manifest.header["manifest_version"],
            "manifest_sha256": self.manifest.manifest_sha256,
            "required_membership_sha256": self.manifest.header["required_membership_sha256"],
            "config_sha256": sha256_text(canonical_json({"policy": self.policy, "runner": RUNNER_REVISION})),
            "source": self.revision(),
            "model": {"mode": "fake_or_none", "live_model_used": False, "judge_revision": None},
            "repeat_policy": self.policy,
            "started_at": started,
            "finished_at": self.now(),
            "selected_case_count": len(selected),
            "gate": gate,
            "counts": counts(results),
            "results": results,
        }

    def _one_run(
        self,
        selected: List[Dict[str, Any]],
        per_case: Dict[str, List[Outcome]],
        supporting: Dict[str, List[Outcome]],
    ) -> None:
        batch: Dict[str, Sequence[str]] = {}
        for case in selected:
            oracle = case["oracle"]
            if case["readiness"] == "executable" and oracle.get("kind") == "pytest_nodes":
                batch["primary:" + case["case_id"]] = oracle["selectors"]
            if case["readiness"] != "executable" and oracle.get("supporting_selectors"):
                batch["support:" + case["case_id"]] = oracle["supporting_selectors"]
        try:
            batch_out = self.executor.run_selectors(batch) if batch else {}
        except Exception as exc:  # noqa: BLE001 - infrastructure fault must not look like a verdict
            batch_out = {
                k: Outcome(
                    "error", reason_codes=["executor_exception"], actual=sanitize(f"{type(exc).__name__}: {exc}")
                )
                for k in batch
            }
        for case in selected:
            cid = case["case_id"]
            if case["readiness"] == "executable":
                if case["oracle"].get("kind") == "pytest_nodes":
                    outcome = batch_out.get("primary:" + cid) or Outcome(
                        "error", reason_codes=["no_executor_outcome"]
                    )
                else:
                    outcome = self._python_check(case)
            elif case["readiness"] == "needs_h3_rubric":
                outcome = self.rubric_executor.run(case)
                if outcome.execution_state == "blocked" and not outcome.reason_codes:
                    outcome.reason_codes = ["needs_h3_rubric"]
            else:
                outcome = Outcome(
                    "blocked",
                    reason_codes=["missing_capability"],
                    actual="blocked until: " + "; ".join(case["blocked_by"]),
                )
            per_case[cid].append(_normalise(outcome))
            if "support:" + cid in batch_out:
                supporting[cid].append(batch_out["support:" + cid])

    def _python_check(self, case: Dict[str, Any]) -> Outcome:
        fn = self.checks.get(case["oracle"].get("check") or "")
        if fn is None:
            return Outcome(
                "missing_fixture",
                reason_codes=["check_not_registered"],
                actual="python check is not registered with the runner",
            )
        try:
            return fn(case)
        except Exception as exc:  # noqa: BLE001
            return Outcome(
                "error", reason_codes=["check_exception"], actual=sanitize(f"{type(exc).__name__}: {exc}")
            )

    def _result(
        self, case: Dict[str, Any], outcome: Outcome, support: List[Outcome], run_states: List[str]
    ) -> Dict[str, Any]:
        frozen = case["hashes"].get("oracle_source_sha256")
        current = source_files_sha256(oracle_files(case), self.root) if frozen else None
        supporting_evidence = None
        if support:
            agg = aggregate_runs(support)
            supporting_evidence = {
                "counts_toward_verdict": False,
                "execution_state": agg.execution_state,
                "outcome": agg.verdict,
                "actual": agg.actual,
                "evidence_refs": agg.evidence_refs,
            }
        return {
            "case_id": case["case_id"],
            "family": case["family"],
            "suite": case["suite"],
            "required": case["required"],
            "case_revision": case["case_revision"],
            "readiness": case["readiness"],
            "coverage": case["coverage"],
            "oracle_type": case["oracle_type"],
            "approval_status": case["owner_approval"]["status"],
            "expected_rule": case["expected_rule"],
            "verdict": outcome.verdict,
            "execution_state": outcome.execution_state,
            "actual": sanitize(outcome.actual),
            "reason_codes": sorted(set(outcome.reason_codes)),
            "evidence_refs": sorted(set(outcome.evidence_refs)),
            "blocked_by": list(case["blocked_by"]),
            "supporting_evidence": supporting_evidence,
            "repeat": {"runs": len(run_states), "execution_states": run_states},
            "hashes": {
                "case_sha256": case["hashes"]["case_sha256"],
                "oracle_source_sha256": frozen,
                "oracle_source_sha256_current": current,
                "oracle_drift": bool(frozen) and frozen != current,
                "schema_sha256": case["hashes"].get("schema_sha256"),
            },
        }


def compute_gate(manifest: Manifest, results: Sequence[Dict[str, Any]], complete: bool = True) -> Dict[str, Any]:
    """Binary gate over the frozen required membership; never rescued by other cases."""
    expected = sorted(manifest.header.get("required_case_ids") or manifest.required_ids)
    by_id = {r["case_id"]: r for r in results}
    blocking: List[Dict[str, Any]] = []
    for cid in expected:
        row = by_id.get(cid)
        reasons: List[str] = []
        if row is None:
            reasons.append("no_result")
        else:
            if row["execution_state"] != "completed":
                reasons.append(f"execution_state:{row['execution_state']}")
            elif row["verdict"] != "PASS":
                reasons.append(f"verdict:{row['verdict']}")
            if row["approval_status"] != "approved":
                reasons.append(f"owner_approval:{row['approval_status']}")
            if row["coverage"] != "full":
                reasons.append(f"oracle_coverage:{row['coverage']}")
            if row["hashes"].get("oracle_drift"):
                reasons.append("oracle_drift")
            if not row["required"]:
                reasons.append("required_flag_changed")
        if reasons:
            blocking.append({"case_id": cid, "reasons": reasons})
    unexpected = sorted(cid for cid, r in by_id.items() if r["required"] and cid not in expected)
    human = dict(manifest.header.get("human_gates") or {"H3": "not_approved"})
    gate_reasons: List[str] = []
    if unexpected:
        gate_reasons.append("required_membership_mismatch")
    if not complete:
        gate_reasons.append("partial_run_not_gate_evidence")
    if any(v != "approved" for v in human.values()):
        gate_reasons.append("human_gate_not_approved")
    status = "PASS" if not blocking and not gate_reasons else "NOT_PASS"
    return {
        "status": status,
        "required_case_count": len(expected),
        "blocking_case_count": len(blocking),
        "blocking_cases": blocking,
        "unexpected_required_cases": unexpected,
        "gate_reasons": gate_reasons,
        "human_gates": human,
        "rule": (
            "PASS only if every required case is completed PASS, owner-approved and fully covered, "
            "the required membership matches, and all human gates are approved"
        ),
    }


def counts(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts only (no aggregate score): by state, verdict and family."""
    by_state = Counter(r["execution_state"] for r in results)
    by_verdict = Counter(r["verdict"] or "none" for r in results)
    by_family: Dict[str, Dict[str, int]] = {}
    for r in results:
        by_family.setdefault(r["family"], Counter())[r["execution_state"]] += 1  # type: ignore[arg-type]
    return {
        "total": len(results),
        "by_execution_state": {s: by_state.get(s, 0) for s in EXECUTION_STATES},
        "by_verdict": dict(sorted(by_verdict.items())),
        "by_family": {f: dict(sorted(c.items())) for f, c in sorted(by_family.items())},
        "by_readiness": dict(sorted(Counter(r["readiness"] for r in results).items())),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry: run the manifest and write JSON, JUnit and Markdown reports."""
    from evals.binary import reports  # local import keeps the runner importable without report code

    parser = argparse.ArgumentParser(description="Run the required binary case manifest (fake models only).")
    parser.add_argument("--cases-dir", type=Path, default=CASES_DIR)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--basename", default="binary_report")
    parser.add_argument("--only", nargs="*", default=None, help="run only these case ids (not gate evidence)")
    parser.add_argument("--timeout", type=float, default=1800.0, help="seconds per pytest batch")
    args = parser.parse_args(argv)
    manifest = load_manifest(args.cases_dir)
    problems = manifest.validate()
    if problems:
        print("manifest is invalid:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    rubrics = reports.load_rubrics(args.cases_dir)
    runner = Runner(manifest, executor=PytestNodeExecutor(timeout=args.timeout), rubrics=rubrics)
    report = runner.run(only=args.only)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    reports.write_all(report, args.out_dir, args.basename)
    print(f"gate: {report['gate']['status']}; {report['counts']['by_execution_state']}")
    return 0 if report["gate"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

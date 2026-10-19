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
  no retry-on-failure path;
* the 13 pilot-label cases (``evals.binary.labeling.pilot.CASE_PAPERS``) carry a
  ``label_basis``: ``gold``, ``provisional`` or ``missing``. A verdict resting on provisional
  (model, unreviewed) labels is reported as ``PROVISIONAL`` (``reported_verdict``), which is
  never PASS for the gate; missing labels keep the case blocked (H3 B4);
* the judge model (``--judge-model`` or the deployment setting under ``--settings-workdir``,
  owner O-U2) and the model under test are recorded in every report, and a run whose
  labeller, judge and model under test are not pairwise different is refused;
* ``gate.provisional`` is the canonical provisional marker read by KG release tooling
  (``app.modules.graph.releases.bind_binary_report``): true when any result's
  ``reported_verdict`` is PROVISIONAL. It never changes ``gate.status``, which stays binary;
* ``--subject-snapshot storage/kg/<corpus>/<id>`` binds the report to a frozen KG snapshot:
  the snapshot is hash-verified before and after the run and a ``subject`` block
  ``{kind: kg_snapshot, corpus, snapshot_id, graph_sha256, manifest_sha256}`` is written into
  the report. Without the option no subject is written.

Reports carry ids, statuses, counts and hashes only: no raw prompt, quote or source text.
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
from evals.binary.labeling.basis import GOLD, MISSING, PROVISIONAL, LabelSet, load_label_set
from evals.binary.labeling.roles import RoleSeparationError, check_roles, resolve_judge_model
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

RUNNER_REVISION = "t11b-runner-1"
SUBJECT_KIND = "kg_snapshot"
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
        labels: Optional[LabelSet] = None,
        judge_model: Optional[str] = None,
        judge_model_source: str = "none",
        model_under_test: Optional[str] = None,
    ):
        self.manifest = manifest
        self.policy = validate_repeat_policy(manifest.header["repeat_policy"])
        self.executor = executor or PytestNodeExecutor()
        self.rubric_executor = rubric_executor or RubricExecutor(rubrics or {})
        self.checks = checks or {}
        self.now = now or (lambda: _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.revision = revision or (lambda: source_revision(root))
        self.root = root
        self.labels = labels if labels is not None else LabelSet()
        self.judge_model = judge_model
        self.judge_model_source = judge_model_source if judge_model else "none"
        self.model_under_test = model_under_test
        check_roles(judge=judge_model, model_under_test=model_under_test, labelers=self.labels.labeler_models)

    def model_block(self) -> Dict[str, Any]:
        """Models of this run: judge (and where it was chosen), model under test, labellers."""
        return {
            "mode": "fake_or_none",
            "live_model_used": False,
            "judge_revision": None,
            "judge_model": self.judge_model,
            "judge_model_source": self.judge_model_source,
            "model_under_test": self.model_under_test,
            "labeler_models": self.labels.labeler_models,
            "role_separation": "checked: labeller, judge and model under test are pairwise different",
        }

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
            "model": self.model_block(),
            "labels": self.labels.summary(),
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
        basis = self.labels.case_basis(case["case_id"])
        if basis is not None and basis["status"] == MISSING and outcome.execution_state == "blocked":
            outcome.reason_codes = list(outcome.reason_codes) + ["pilot_labels_missing"]
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
            "reported_verdict": reported_verdict(outcome.verdict, basis),
            "label_basis": basis,
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


def reported_verdict(verdict: Optional[str], basis: Optional[Dict[str, Any]]) -> Optional[str]:
    """``PROVISIONAL`` for a verdict resting on provisional (unreviewed model) labels, else the verdict.

    A case with ``missing`` labels keeps the verdict of whatever ran (synthetic oracles only);
    the gate still blocks it with ``pilot_labels:missing``.
    """
    if verdict is None:
        return None
    if basis is not None and basis["status"] == PROVISIONAL:
        return "PROVISIONAL"
    return verdict


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
            shown = row.get("reported_verdict", row["verdict"])
            if row["execution_state"] != "completed":
                reasons.append(f"execution_state:{row['execution_state']}")
            elif shown != "PASS":
                reasons.append(f"verdict:{shown}")
            basis = row.get("label_basis")
            if basis is not None and basis.get("status") != GOLD:
                reasons.append(f"pilot_labels:{basis.get('status')}")
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
    provisional_ids = sorted(r["case_id"] for r in results if r.get("reported_verdict") == "PROVISIONAL")
    return {
        "status": status,
        "provisional": bool(provisional_ids),
        "provisional_case_ids": provisional_ids,
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


class SubjectError(ValueError):
    """The ``--subject-snapshot`` directory is not a verifiable frozen KG snapshot."""


def snapshot_subject(snapshot_dir: Path) -> Dict[str, Any]:
    """The report ``subject`` for a frozen ``storage/kg/<corpus>/<id>`` snapshot.

    The directory is verified with the release checker's own hash check (layout, manifest, graph and
    validation hashes), so the subject names exactly the bytes ``prepare_kg_release`` later compares.
    No local path is recorded.

    Raises:
        SubjectError: the directory is missing, tampered or not a frozen snapshot.
    """
    from app.modules.graph.releases import ReleaseVerificationError, verify_snapshot_dir

    try:
        verified = verify_snapshot_dir(Path(snapshot_dir))
    except ReleaseVerificationError as exc:
        raise SubjectError(f"subject snapshot does not verify: {exc.code}") from None
    return {
        "kind": SUBJECT_KIND,
        "corpus": verified.manifest.corpus,
        "snapshot_id": verified.manifest.snapshot_id,
        "graph_sha256": verified.graph_sha256,
        "manifest_sha256": verified.manifest_sha256,
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
        "by_reported_verdict": dict(sorted(Counter(r.get("reported_verdict") or "none" for r in results).items())),
        "by_label_basis": dict(sorted(Counter((r.get("label_basis") or {}).get("status", "not_applicable")
                                              for r in results).items())),
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
    parser.add_argument("--labels-dir", type=Path, default=None,
                        help="private pilot label directory (provisional or gold labels, H3 B4)")
    parser.add_argument("--judge-model", default=None, help="judge model id (overrides the deployment setting)")
    parser.add_argument("--settings-workdir", type=Path, default=None,
                        help="F2W workdir whose validation/judge_model.json holds the settings-panel judge (O-U2)")
    parser.add_argument("--model-under-test", default=None, help="model id of the system under test")
    parser.add_argument("--subject-snapshot", type=Path, default=None,
                        help="frozen storage/kg/<corpus>/<id> the report is bound to (writes the subject block)")
    args = parser.parse_args(argv)
    manifest = load_manifest(args.cases_dir)
    problems = manifest.validate()
    if problems:
        print("manifest is invalid:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    rubrics = reports.load_rubrics(args.cases_dir)
    judge, source = resolve_judge_model(args.judge_model, args.settings_workdir)
    try:
        runner = Runner(manifest, executor=PytestNodeExecutor(timeout=args.timeout), rubrics=rubrics,
                        labels=load_label_set(args.labels_dir), judge_model=judge, judge_model_source=source,
                        model_under_test=args.model_under_test)
    except RoleSeparationError as exc:
        print(f"refused: {sanitize(exc)}", file=sys.stderr)
        return 2
    subject = None
    if args.subject_snapshot is not None:
        try:
            subject = snapshot_subject(args.subject_snapshot)
        except SubjectError as exc:
            print(f"refused: {sanitize(exc)}", file=sys.stderr)
            return 2
    report = runner.run(only=args.only)
    if subject is not None:
        try:
            after = snapshot_subject(args.subject_snapshot)
        except SubjectError as exc:
            after = {"error": str(exc)}
        if after != subject:
            print("refused: the subject snapshot changed during the run", file=sys.stderr)
            return 2
        report["subject"] = subject
    args.out_dir.mkdir(parents=True, exist_ok=True)
    reports.write_all(report, args.out_dir, args.basename)
    print(f"gate: {report['gate']['status']}; {report['counts']['by_execution_state']}")
    return 0 if report["gate"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

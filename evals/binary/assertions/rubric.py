"""Controlled adapter for owner-approved frozen rubrics.

Rubrics here are drafts until H3. A judge (a model through the configured F2W provider,
or a fake in tests) may only answer the rubric's binary criteria. Anything else, such as
non-JSON text, a missing or non-boolean criterion, a verdict inconsistent with the
criteria, or ``UNRESOLVED``, is ``judge_invalid`` and never a verdict.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from evals.binary.assertions.base import Outcome
from evals.binary.sanitize import sanitize

# judge(rubric, case, evidence) -> raw text. Live judges are explicit and source-authorised.
Judge = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], str]


def parse_judgment(raw: Any, criterion_ids: Sequence[str]) -> Tuple[Optional[str], List[str]]:
    """Return ``(verdict, problems)``; verdict is None whenever there is any problem."""
    problems: List[str] = []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None, ["not_json"]
    if not isinstance(data, dict):
        return None, ["not_an_object"]
    verdict = data.get("verdict")
    if verdict == "UNRESOLVED":
        return None, ["unresolved"]
    if verdict not in ("PASS", "FAIL"):
        problems.append("bad_verdict")
    rows = data.get("criteria")
    if not isinstance(rows, list):
        return None, problems + ["missing_criteria"]
    met: Dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("met"), bool):
            problems.append("malformed_criterion")
            continue
        met[row["id"]] = row["met"]
    missing = [c for c in criterion_ids if c not in met]
    extra = [c for c in met if c not in criterion_ids]
    if missing:
        problems.append("missing_criteria:" + ",".join(missing))
    if extra:
        problems.append("unknown_criteria:" + ",".join(extra))
    if not problems and verdict != ("PASS" if all(met[c] for c in criterion_ids) else "FAIL"):
        problems.append("verdict_inconsistent_with_criteria")
    return (None, problems) if problems else (verdict, [])


class RubricExecutor:
    """Run a rubric case through a judge, only when the rubric has owner approval."""

    def __init__(self, rubrics: Dict[str, Dict[str, Any]], judge: Optional[Judge] = None):
        self.rubrics = rubrics
        self.judge = judge

    def run(self, case: Dict[str, Any], evidence: Optional[Dict[str, Any]] = None) -> Outcome:
        """Execute one case; unapproved rubric or no judge is ``blocked``, never a verdict."""
        rubric = self.rubrics.get(case["oracle"].get("rubric_ref") or "")
        if rubric is None:
            return Outcome("missing_fixture", reason_codes=["rubric_not_found"], actual="rubric draft not found")
        if rubric.get("approval_status") != "approved":
            return Outcome(
                "blocked",
                reason_codes=["needs_h3_rubric"],
                actual="rubric is a draft; owner approval at H3 is required",
            )
        if self.judge is None:
            return Outcome(
                "blocked",
                reason_codes=["no_judge_configured"],
                actual="no judge configured; live judging is explicit and source-authorised",
            )
        try:
            raw = self.judge(rubric, case, evidence or {})
        except Exception as exc:  # noqa: BLE001 - provider faults are infrastructure, not content verdicts
            return Outcome("error", reason_codes=["judge_error"], actual=sanitize(f"{type(exc).__name__}: {exc}"))
        verdict, problems = parse_judgment(raw, [c["id"] for c in rubric["criteria"]])
        if verdict is None:
            return Outcome(
                "judge_invalid",
                reason_codes=["judge_invalid"] + problems,
                actual="judge output was malformed or unresolved: " + ",".join(problems),
            )
        return Outcome(
            "completed",
            verdict,
            actual=f"judge verdict {verdict}",
            reason_codes=["rubric:" + rubric["rubric_id"]],
            detail={"judge": "rubric"},
        )

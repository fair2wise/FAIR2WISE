"""Synthetic manifests and fake executors for the binary-runner tests (no real oracles)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from evals.binary.assertions.base import Outcome
from evals.binary.model import Manifest, canonical_json, case_sha256, sha256_text

POLICY = {"runs": 1, "aggregation": "all_runs_must_pass", "retry_on_failure": False, "declared_before_run": True}


def make_case(
    case_id: str,
    readiness: str = "executable",
    coverage: str = "full",
    required: bool = True,
    approval: str = "proposed",
    selectors: Optional[Sequence[str]] = None,
    blocked_by: Optional[Sequence[str]] = None,
    rubric_ref: Optional[str] = None,
    oracle_hash: Optional[str] = None,
    source_refs: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """A minimal valid case record."""
    case: Dict[str, Any] = {
        "case_id": case_id,
        "family": case_id.split("-")[0],
        "suite": "synthetic",
        "title": f"title {case_id}",
        "companion_title": f"title {case_id}",
        "required": required,
        "case_revision": 1,
        "expected_rule": f"rule {case_id}",
        "expected_rule_source": "companion_title",
        "companion_expected_rule": None,
        "oracle_type": "deterministic_assertion",
        "readiness": readiness,
        "coverage": coverage,
        "oracle": {
            "kind": "pytest_nodes" if readiness == "executable" else None,
            "selectors": (
                list(selectors or [f"tests/fake/{case_id}.py::test_x"]) if readiness == "executable" else []
            ),
            "supporting_selectors": [],
            "covers": "",
            "uncovered": "",
            "rubric_ref": rubric_ref,
        },
        "blocked_by": list(blocked_by or (["T99"] if readiness == "blocked" else [])),
        "input_fixture_refs": [],
        "source_refs": list(source_refs or []),
        "schema_ref": None,
        "model_ref": "fake_or_none",
        "deployment_profiles": [],
        "forbidden_outcomes": [],
        "owner_approval": {"status": approval, "ref": None, "approved_by": None},
        "supersedes": None,
        "change_reason": None,
        "companion": {},
        "hashes": {"case_sha256": "", "oracle_source_sha256": oracle_hash, "schema_sha256": None},
    }
    case["hashes"]["case_sha256"] = case_sha256(case)
    return case


def make_manifest(
    cases: List[Dict[str, Any]], human: Optional[Dict[str, str]] = None, policy: Optional[Dict[str, Any]] = None
) -> Manifest:
    """A manifest whose header freezes the required ids of ``cases``."""
    required = sorted(c["case_id"] for c in cases if c["required"])
    header = {
        "manifest_version": "test",
        "required_case_ids": required,
        "required_membership_sha256": sha256_text(canonical_json(required)),
        "human_gates": human if human is not None else {"H3": "not_approved"},
        "repeat_policy": policy or dict(POLICY),
    }
    return Manifest(header, cases)


class FakeExecutor:
    """Returns canned outcomes per key; records every call (to prove there are no retries)."""

    def __init__(self, outcomes: Dict[str, Outcome]):
        self.outcomes = outcomes
        self.calls: List[List[str]] = []

    def run_selectors(self, selector_sets: Dict[str, Sequence[str]]) -> Dict[str, Outcome]:
        self.calls.append(sorted(selector_sets))
        return {
            k: self.outcomes.get(k, Outcome("error", reason_codes=["unscripted"], actual="no scripted outcome"))
            for k in selector_sets
        }


def passed() -> Outcome:
    """A completed PASS."""
    return Outcome("completed", "PASS", actual="ok", evidence_refs=["pytest:x"])


def failed() -> Outcome:
    """A completed FAIL."""
    return Outcome("completed", "FAIL", actual="bad")


FIXED_NOW = lambda: "2026-01-01T00:00:00Z"  # noqa: E731
NO_REVISION = lambda: {"commit": "c", "app_tree": "t", "dirty": False}  # noqa: E731

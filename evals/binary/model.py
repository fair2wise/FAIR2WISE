"""Case records, manifest loading, canonical hashing and structural validation.

A case record carries the BINARY_EVALUATION_SPEC section 1 fields plus the T11A
additions (readiness, coverage, approval, supersession). Records are plain JSON so the
owner can review diffs; this module only loads and validates them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

CASES_DIR = Path(__file__).resolve().parent / "cases"
REPO_ROOT = Path(__file__).resolve().parents[2]

MANIFEST_VERSION = "t11a-1"

READINESS = ("executable", "blocked", "needs_h3_rubric")
COVERAGE = ("full", "partial", "none")
ORACLE_TYPES = ("deterministic_assertion", "frozen_rubric", "deterministic_plus_rubric")
EXECUTION_STATES = ("completed", "timeout", "skipped", "missing_fixture", "blocked", "error", "judge_invalid")
APPROVAL_STATUSES = ("proposed", "approved", "rejected")

REQUIRED_FIELDS = (
    "case_id",
    "family",
    "suite",
    "title",
    "required",
    "case_revision",
    "expected_rule",
    "expected_rule_source",
    "oracle_type",
    "readiness",
    "coverage",
    "oracle",
    "blocked_by",
    "input_fixture_refs",
    "source_refs",
    "schema_ref",
    "model_ref",
    "hashes",
    "owner_approval",
    "supersedes",
    "change_reason",
    "forbidden_outcomes",
    "companion",
)


class ManifestError(ValueError):
    """The manifest is structurally invalid."""


def canonical_json(value: Any) -> str:
    """Return the canonical (sorted, compact, UTF-8-safe) JSON text used for hashing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    """Return the hex SHA-256 of ``text``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> Optional[str]:
    """Return the hex SHA-256 of a file, or None if it does not exist."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def case_sha256(case: Dict[str, Any]) -> str:
    """Hash a case record without its ``hashes`` block (which stores this value)."""
    body = {k: v for k, v in case.items() if k != "hashes"}
    return sha256_text(canonical_json(body))


def oracle_files(case: Dict[str, Any]) -> List[str]:
    """Repo-relative files the oracle depends on (selector files plus declared source refs)."""
    files = set(case.get("source_refs") or [])
    oracle = case.get("oracle") or {}
    for selector in oracle.get("selectors") or []:
        files.add(str(selector).split("::", 1)[0])
    if oracle.get("check_module"):
        files.add(oracle["check_module"])
    return sorted(files)


def source_files_sha256(files: Iterable[str], root: Path = REPO_ROOT) -> str:
    """Hash the named files (path and content), sorted; a missing file hashes as ``MISSING``."""
    rows = [[f, sha256_file(root / f) or "MISSING"] for f in sorted(set(files))]
    return sha256_text(canonical_json(rows))


def family_of(case_id: str) -> str:
    """Family is the alphabetic prefix of the case id (``AUTH-01`` -> ``AUTH``)."""
    return case_id.split("-", 1)[0]


def validate_case(case: Dict[str, Any]) -> List[str]:
    """Return structural problems for one case record (empty when valid)."""
    problems: List[str] = []
    cid = case.get("case_id", "<no id>")
    for field in REQUIRED_FIELDS:
        if field not in case:
            problems.append(f"{cid}: missing field {field}")
    if problems:
        return problems
    if case["family"] != family_of(cid):
        problems.append(f"{cid}: family {case['family']} does not match the id")
    if case["readiness"] not in READINESS:
        problems.append(f"{cid}: bad readiness {case['readiness']}")
    if case["coverage"] not in COVERAGE:
        problems.append(f"{cid}: bad coverage {case['coverage']}")
    if case["oracle_type"] not in ORACLE_TYPES:
        problems.append(f"{cid}: bad oracle_type {case['oracle_type']}")
    if case["owner_approval"].get("status") not in APPROVAL_STATUSES:
        problems.append(f"{cid}: bad approval status")
    if not isinstance(case["case_revision"], int) or case["case_revision"] < 1:
        problems.append(f"{cid}: case_revision must be a positive integer")
    if case["case_revision"] > 1 and not (case["supersedes"] and case["change_reason"]):
        problems.append(f"{cid}: a revised case needs supersedes and change_reason")
    if case["readiness"] == "executable":
        kind = (case["oracle"] or {}).get("kind")
        if kind not in ("pytest_nodes", "python_check"):
            problems.append(f"{cid}: executable case needs a pytest_nodes or python_check oracle")
    if case["readiness"] == "blocked" and not case["blocked_by"]:
        problems.append(f"{cid}: blocked case must name the missing capability or task")
    if case["readiness"] == "needs_h3_rubric" and not case["oracle"].get("rubric_ref"):
        problems.append(f"{cid}: needs_h3_rubric case must reference a draft rubric")
    return problems


class Manifest:
    """The loaded case manifest: header, ordered cases and the frozen required membership."""

    def __init__(self, header: Dict[str, Any], cases: Sequence[Dict[str, Any]]):
        self.header = header
        self.cases: List[Dict[str, Any]] = sorted(cases, key=lambda c: c["case_id"])
        self.by_id = {c["case_id"]: c for c in self.cases}

    @property
    def required_ids(self) -> List[str]:
        """Case ids that are required in the current records."""
        return [c["case_id"] for c in self.cases if c["required"]]

    @property
    def manifest_sha256(self) -> str:
        """Hash over every case record (hashes blocks included)."""
        return sha256_text(canonical_json(self.cases))

    def validate(self) -> List[str]:
        """Structural problems, including drift between the frozen header and the records."""
        problems: List[str] = []
        ids = [c["case_id"] for c in self.cases]
        if len(ids) != len(set(ids)):
            problems.append("duplicate case ids")
        for case in self.cases:
            problems.extend(validate_case(case))
            if case["hashes"].get("case_sha256") != case_sha256(case):
                problems.append(f"{case['case_id']}: case_sha256 does not match the record")
        frozen = self.header.get("required_case_ids")
        if frozen is not None and sorted(frozen) != sorted(self.required_ids):
            problems.append("required membership differs from the frozen header list")
        if self.header.get("required_membership_sha256") != sha256_text(canonical_json(sorted(self.required_ids))):
            problems.append("required_membership_sha256 does not match the required case ids")
        return problems


def load_manifest(cases_dir: Path = CASES_DIR) -> Manifest:
    """Load ``index.json`` and every suite file it lists."""
    header = json.loads((cases_dir / "index.json").read_text(encoding="utf-8"))
    cases: List[Dict[str, Any]] = []
    for name in header["case_files"]:
        data = json.loads((cases_dir / name).read_text(encoding="utf-8"))
        cases.extend(data["cases"])
    return Manifest(header, cases)

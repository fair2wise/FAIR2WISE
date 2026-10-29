"""Generate the case manifest from the companion inventory plus the H3 proposals.

All 108 companion cases are imported; none is dropped. A case's expectation changes
only where ``proposals.CHANGES`` cites a recorded owner decision, and each such change is
listed in ``docs/migration/reviews/H3/case_changes.md`` for owner review. The companion
file is read, never modified. Every case is written with ``owner_approval.status =
proposed``; approval is an H3 owner act.

    python -m evals.binary.import_companion            # rewrite the generated files
    python -m evals.binary.import_companion --check    # exit 1 if they are out of date
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from evals.binary import proposals
from evals.binary.labeling.pilot import CASE_PAPERS
from evals.binary.model import (
    CASES_DIR,
    MANIFEST_VERSION,
    REPO_ROOT,
    canonical_json,
    case_sha256,
    family_of,
    oracle_files,
    sha256_file,
    sha256_text,
    source_files_sha256,
)
from evals.binary.rubric_drafts import RUBRICS

COMPANION = "docs/migration/companion/binary_case_inventory.json"
SPEC = "docs/migration/companion/BINARY_EVALUATION_SPEC.md"
DECISIONS = "docs/migration/reviews/H1/owner_decisions.md"
H2_DECISION = "docs/migration/reviews/H2/decision.json"
CHANGES_DOC = "docs/migration/reviews/H3/case_changes.md"
PILOT_DOC = "docs/migration/reviews/H3/pilot_candidates.md"
#: Blocker named by every pilot-label case until reviewed (gold) labels exist (H3 B4).
B4_LABELS = ("H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold;"
             " python -m evals.binary.label)")

REPEAT_POLICY = {
    "runs": 1,
    "aggregation": "all_runs_must_pass",
    "retry_on_failure": False,
    "declared_before_run": True,
    "note": (
        "Deterministic cases run once per report. Any live repetition count is declared here by the owner at H3 "
        "before a live run; there is no retry-until-green path."
    ),
}

SCHEMA_REFS = {
    "EXT-01": "storage/schema/rsoxs_schema_extract.yaml",
    "EXT-02": "storage/schema/rsoxs_schema_extract.yaml",
    "EXT-03": "storage/schema/rsoxs_schema_extract.yaml",
    "EXT-04": "storage/schema/rsoxs_schema_extract.yaml",
    "EXT-05": "storage/schema/rsoxs_schema_extract.yaml",
    "SCH-01": "storage/schema/rsoxs_schema_extract.yaml",
    "SCH-02": "storage/schema/prov_vocabulary.yaml",
    "SCH-05": "storage/schema/rsoxs_schema.yaml",
}

FIXTURE_DIRS = {
    "tests/security": ["tests/security/security_fakes.py"],
    "tests/api_wiring": ["tests/api_wiring/_wiring_support.py"],
    "tests/disclosure": ["tests/disclosure/_disclosure_support.py"],
    "tests/extraction": ["tests/extraction/extraction_fakes.py"],
    "tests/evidence": ["tests/evidence/evidence_fixtures.py"],
    "tests/graph": ["tests/graph/graph_samples.py"],
    "tests/operations": ["tests/operations/summary_fakes.py", "tests/operations/fixtures/"],
    "tests/read_path": ["tests/read_path/read_path_fakes.py"],
    "tests/runtime": ["tests/runtime/runtime_fakes.py"],
    "tests/contracts/json_kg": ["tests/contracts/json_kg/"],
    "tests/contracts/legacy_ui": ["tests/contracts/legacy_ui/"],
    "tests/tiled": ["tests/fixtures/tiled_server.py"],
    "tests/graph_tiled": ["tests/fixtures/tiled_server.py"],
    # landed after the H3 packet (rebound 2026-10-29)
    "tests/graph_import": ["tests/graph_import/import_fakes.py", "tests/fixtures/tiled_server.py"],
    "tests/jobs": ["tests/jobs/jobs_fakes.py", "tests/jobs/publish_fakes.py"],
    "tests/snapshots": ["tests/snapshots/snapshot_fakes.py"],
    "tests/images": ["tests/images/dockerfile_model.py", "tests/images/fake_service.py"],
    "tests/gateway": ["tests/gateway/nginx_model.py"],
    "tests/security_live": [
        "tests/security_live/live_support.py",
        "tests/security_live/live_apps.py",
        "tests/fixtures/tiled_server.py",
    ],
    "tests/api_v1": ["tests/api_v1/_v1_support.py"],
    "tests/api_v1_live": ["tests/fixtures/tiled_server.py"],
    "tests/contracts/legacy_ui/closure": [
        "tests/contracts/legacy_ui/closure/closure_support.py",
        "tests/fixtures/tiled_server.py",
    ],
    "tests/schema": ["tests/schema/t22c_fixtures.py"],
}


def _fixtures(case_files: List[str], declared: List[str]) -> List[str]:
    refs = set(declared)
    for f in case_files:
        d = f.rsplit("/", 1)[0] if "/" in f else ""
        for prefix, items in FIXTURE_DIRS.items():
            if d == prefix:
                refs.update(items)
    if any(f.startswith("tests/graph/test_graph_tiled_server") for f in case_files):
        refs.add("tests/fixtures/tiled_server.py")
    return sorted(r for r in refs if (REPO_ROOT / r).exists())


def build_case(companion_case: Dict[str, Any]) -> Dict[str, Any]:
    """Build one manifest record from a companion record and its proposal."""
    cid = companion_case["case_id"]
    spec = proposals.ORACLES[cid]
    change = proposals.CHANGES.get(cid)
    rule = companion_case.get("expected_rule")
    if rule:
        source = "companion_expected_rule"
    else:
        rule = (
            f"As titled in the companion inventory: {companion_case['title']}. The companion records no separate "
            "rule text; H3 supplies the explicit rule."
        )
        source = "companion_title"
    record: Dict[str, Any] = {
        "case_id": cid,
        "family": family_of(cid),
        "suite": companion_case["suite"],
        "title": companion_case["title"],
        "companion_title": companion_case["title"],
        "required": bool(companion_case["required_proposal"]),
        "case_revision": 1,
        "expected_rule": rule,
        "expected_rule_source": source,
        "companion_expected_rule": None,
        "oracle_type": spec["oracle_type"],
        "readiness": spec["readiness"],
        "coverage": spec["coverage"],
        "oracle": {
            "kind": "pytest_nodes" if spec["readiness"] == "executable" else None,
            "selectors": spec["selectors"],
            "supporting_selectors": spec["supporting"] if spec["readiness"] != "executable" else [],
            "covers": spec["covers"],
            "uncovered": spec["uncovered"],
            "rubric_ref": spec.get("rubric_ref"),
        },
        "blocked_by": spec["blocked_by"],
        "input_fixture_refs": [],
        "source_refs": [COMPANION, SPEC],
        "schema_ref": SCHEMA_REFS.get(cid),
        "model_ref": (
            "fake_or_none" if spec["readiness"] == "executable" else "live_model_selected_explicitly_at_run_time"
        ),
        "deployment_profiles": companion_case.get("deployment_profiles", []),
        "forbidden_outcomes": [],
        "owner_approval": {"status": "proposed", "ref": None, "approved_by": None},
        "supersedes": None,
        "change_reason": None,
        "companion": copy.deepcopy(companion_case),
    }
    files = [s.split("::", 1)[0] for s in spec["selectors"] + spec["supporting"]]
    record["input_fixture_refs"] = _fixtures(files, spec["fixtures"])
    if change:
        record["case_revision"] = 2
        record["companion_expected_rule"] = companion_case.get("expected_rule")
        record["expected_rule"] = f"{rule} {change['add']}"
        record["expected_rule_source"] = "owner_decision"
        record["supersedes"] = f"{cid} revision 1 (companion v8.0 binary_case_inventory.json)"
        record["change_reason"] = (
            "Recorded owner decision(s): " + "; ".join(change["decisions"]) + f". Listed in {CHANGES_DOC}."
        )
        if change.get("title"):
            record["title"] = change["title"]
        record["source_refs"] = record["source_refs"] + [DECISIONS, H2_DECISION]
    if cid in CASE_PAPERS:
        spec = CASE_PAPERS[cid]
        record["pilot_labels"] = {
            "papers": list(spec["papers"]),
            "rule": spec["rule"],
            "requirement": (
                "H3 B4: results rest on labels of these pilot papers; model labels are provisional (reported"
                " PROVISIONAL, never PASS) until a person reviews them into gold"
            ),
        }
        if record["readiness"] != "executable":
            record["blocked_by"] = list(record["blocked_by"]) + [B4_LABELS]
        record["source_refs"] = record["source_refs"] + [PILOT_DOC]
    record["source_refs"] = sorted(set(record["source_refs"]))
    # hashes last: they cover the finished record
    record["hashes"] = {
        "case_sha256": "",
        "oracle_source_sha256": None,
        "schema_sha256": None,
        "model_sha256": None,
        "config_sha256": sha256_text(
            canonical_json(
                {"oracle": record["oracle"], "readiness": record["readiness"], "repeat_policy": REPEAT_POLICY}
            )
        ),
    }
    record["hashes"]["oracle_source_sha256"] = source_files_sha256(oracle_files(record))
    if record["schema_ref"]:
        record["hashes"]["schema_sha256"] = sha256_file(REPO_ROOT / record["schema_ref"])
    record["hashes"]["case_sha256"] = case_sha256(record)
    return record


def build_all() -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Return ``({relative file name: text}, in-memory header)`` for the whole generated manifest."""
    inventory = json.loads((REPO_ROOT / COMPANION).read_text(encoding="utf-8"))
    cases = [build_case(c) for c in inventory["cases"]]
    assert len(cases) == 108, "all companion cases must be imported"
    by_suite: Dict[str, List[Dict[str, Any]]] = {}
    for case in cases:
        by_suite.setdefault(case["suite"], []).append(case)
    files: Dict[str, str] = {}
    names = []
    for suite, rows in sorted(by_suite.items()):
        name = f"{suite}.json"
        names.append(name)
        files[name] = (
            json.dumps(
                {"suite": suite, "cases": sorted(rows, key=lambda r: r["case_id"])},
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n"
        )
    required = sorted(c["case_id"] for c in cases if c["required"])
    header = {
        "manifest_version": MANIFEST_VERSION,
        "status": "proposed_for_h3_not_approved",
        "note": (
            "All cases are proposals. Owner approval of cases, oracles and rubrics is the H3 gate; no agent"
            " approves."
        ),
        "companion_source": {
            "path": COMPANION,
            "sha256": sha256_file(REPO_ROOT / COMPANION),
            "version": inventory["version"],
            "case_count": len(inventory["cases"]),
        },
        "case_files": names,
        "rubrics_file": "rubrics.json",
        "required_case_ids": required,
        "required_membership_sha256": sha256_text(canonical_json(required)),
        "human_gates": {"H3": "not_approved"},
        "repeat_policy": REPEAT_POLICY,
    }
    files["index.json"] = json.dumps(header, indent=2, sort_keys=True) + "\n"
    files["rubrics.json"] = (
        json.dumps(
            {"status": "draft_not_approved", "rubrics": RUBRICS}, indent=2, sort_keys=True, ensure_ascii=False
        )
        + "\n"
    )
    return files, header


def changes_markdown(cases: List[Dict[str, Any]]) -> str:
    """The owner-review list of every expectation change and its cited decision."""
    changed = sorted((c for c in cases if c["case_revision"] > 1), key=lambda c: c["case_id"])
    lines = [
        "# H3 case changes for owner review",
        "",
        (
            "Generated by `python -m evals.binary.import_companion`. The companion `binary_case_inventory.json` is"
            " untouched and all"
        ),
        (
            f"{len(cases)} companion cases are present in `evals/binary/cases/`. Only the {len(changed)} cases"
            " below differ from the companion expectation, each because of a recorded owner"
        ),
        (
            "decision. Every case, changed or not, is `proposed`; nothing here is approved. Approve, reject or"
            " reword each change at H3;"
        ),
        "a change you reject is reverted by issuing a new case revision.",
        "",
        "| case | companion expectation | change | decision(s) |",
        "| --- | --- | --- | --- |",
    ]
    for c in changed:
        change = proposals.CHANGES[c["case_id"]]
        old = c["companion_expected_rule"] or f"(title only) {c['companion_title']}"
        title_note = (
            f" Title: \"{c['companion_title']}\" -> \"{c['title']}\"."
            if c["title"] != c["companion_title"]
            else ""
        )
        lines.append(
            f"| {c['case_id']} | {old.replace('|', '/')} |"
            f" {change['add'].replace('Owner decision update: ', '').replace('|', '/')}{title_note} | "
            + "; ".join(change["decisions"])
            + " |"
        )
    lines += [
        "",
        "## Decisions considered that changed no case",
        "",
        "- H1 O-A5 (JSON graph reading) is applied to AUTH-02; the per-user default graph selection follow-up and",
        "  H2 Q4 are applied to AUTH-02/AUTH-03/LLM-01; no separate case asserts them.",
        "- H2 Q8 / T02B (pending approvals survive new messages): no companion case states the old behaviour, so",
        (
            "  none changed. Existing runtime tests cover it; H3 may want a new case (a new required case, not a"
            " change)."
        ),
        "- H2 Q2 (traces record F2W_RUNTIME) and O-D2 uploaded-graph opaque ids: implementation details with",
        "  existing tests, no case expectation changed.",
        "- H1 O-P1 (checkout .env lookup, T15 bridge): packaging decisions, outside the 108 cases.",
        (
            "- H1 O-A7 touches DEP-05 only (MkDocs under the prefix); FastAPI docs pages (O-A8) touch AUTH-01 and"
            " DEP-07."
        ),
        "",
        "## Flags for the owner",
        "",
        (
            "- REL-05 reverses the companion wording: O-D1 keeps tracked KG JSON (with verbatim snippets) in the"
            " image."
        ),
        (
            "  REL-04 and REL-05 now forbid only raw PDFs, full parsed bodies, crops, the source-RAG index,"
            " credentials,"
        ),
        "  local paths and operational databases.",
        (
            "- QUOTE-01 drops count limits (O-D2) but adds the 40% single-page/section rule; confirm 40% is the"
            " intended oracle."
        ),
        (
            "- AUTH-02/AUTH-03 absorb the per-user settings decision (H2 Q4); the T25D acceptance wording is the"
            " oracle source."
        ),
        (
            "- TIL-07/DEP-02 oracle the interim 0.2.18 pin; re-issue them as a new revision when a final 0.2.19"
            " exists (O-T1)."
        ),
        "",
        "Nothing was dropped or weakened: a reviewer can diff `case_revision`, `companion_expected_rule` and",
        "`supersedes` in each record.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(root: Path = REPO_ROOT, cases_dir: Path = CASES_DIR) -> List[Path]:
    """Write the generated manifest, rubrics and the changes document."""
    files, _ = build_all()
    written = []
    cases_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (cases_dir / name).write_text(text, encoding="utf-8")
        written.append(cases_dir / name)
    all_cases = [
        c
        for name, text in files.items()
        if name not in ("index.json", "rubrics.json")
        for c in json.loads(text)["cases"]
    ]
    doc = root / CHANGES_DOC
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(changes_markdown(all_cases), encoding="utf-8")
    written.append(doc)
    return written


def check_outputs(root: Path = REPO_ROOT, cases_dir: Path = CASES_DIR) -> List[str]:
    """Names of generated files that differ from what the generator would write now."""
    files, _ = build_all()
    stale = [
        n
        for n, text in files.items()
        if not (cases_dir / n).is_file() or (cases_dir / n).read_text(encoding="utf-8") != text
    ]
    all_cases = [
        c
        for name, text in files.items()
        if name not in ("index.json", "rubrics.json")
        for c in json.loads(text)["cases"]
    ]
    doc = root / CHANGES_DOC
    if not doc.is_file() or doc.read_text(encoding="utf-8") != changes_markdown(all_cases):
        stale.append(CHANGES_DOC)
    return stale


def main(argv: List[str] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        stale = check_outputs()
        print("up to date" if not stale else "stale: " + ", ".join(stale))
        return 1 if stale else 0
    for path in write_outputs():
        print("wrote", path.relative_to(REPO_ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())

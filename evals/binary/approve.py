"""Record the OWNER's gate approval in the binary case manifest.

This is the human step the plan reserves for the owner (no agent self-approval). It does
not decide anything: it transcribes a decision the owner has already written to
``docs/migration/reviews/<gate>/decision.json`` into ``evals/binary/approvals.json`` and
regenerates the case files from it.

    python -m evals.binary.approve --gate H3 \\
        --decision docs/migration/reviews/H3/decision.json --approver "<name>"

Rules enforced here:

* the decision file must name the same gate and say ``"status": "approved"``; its
  sha256 is recorded with the approver's name and the recording date;
* a case is approved only if its ``expected_rule`` and ``case_revision`` are exactly what
  the owner reviewed in the H3 packet (the pre-H3 report at
  ``docs/migration/reviews/H3/evidence/binary_report_pre_h3.json``, the same records
  ``case_changes.md`` lists); a case whose rule text changed since stays ``proposed`` and
  is printed for a new review;
* rubrics are untouched (they stay draft until the H3 B4 labels exist);
* ``--dry-run`` prints the plan and writes nothing.

The generator (``import_companion``) copies the record; nothing else in the repository
writes an approval. Run from the repository root, then commit the result as the owner.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from evals.binary import import_companion
from evals.binary.model import CASES_DIR, REPO_ROOT, load_manifest, sha256_file

GATES = ("H3",)
DEFAULT_PACKETS = {"H3": "docs/migration/reviews/H3/evidence/binary_report_pre_h3.json"}


class ApprovalError(ValueError):
    """The approval cannot be recorded as requested."""


def verify_decision(path: Path, gate: str) -> Dict[str, Any]:
    """Load the owner's decision file and check it approves ``gate``."""
    if not path.is_file():
        raise ApprovalError(f"decision file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApprovalError(f"decision file is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ApprovalError("decision file must hold a JSON object")
    if data.get("gate") != gate:
        raise ApprovalError(f"decision file is for gate {data.get('gate')!r}, not {gate}")
    if data.get("status") != "approved":
        raise ApprovalError(f"decision file status is {data.get('status')!r}, not 'approved'; nothing recorded")
    return data


def packet_rules(packet: Path) -> Dict[str, Tuple[str, int]]:
    """``{case_id: (expected_rule, case_revision)}`` as the owner reviewed them in the gate packet."""
    if not packet.is_file():
        raise ApprovalError(f"gate packet report not found: {packet}")
    report = json.loads(packet.read_text(encoding="utf-8"))
    rows = report.get("results") or []
    if not rows:
        raise ApprovalError("gate packet report holds no case results")
    return {r["case_id"]: (r["expected_rule"], int(r["case_revision"])) for r in rows}


def plan_cases(
    cases: Sequence[Dict[str, Any]], reviewed: Dict[str, Tuple[str, int]]
) -> Tuple[List[str], Dict[str, str]]:
    """Split the cases into (approvable ids, {id: why it stays proposed})."""
    approvable: List[str] = []
    kept: Dict[str, str] = {}
    for case in cases:
        cid = case["case_id"]
        if cid not in reviewed:
            kept[cid] = "not in the reviewed packet"
            continue
        rule, revision = reviewed[cid]
        if case["expected_rule"] != rule:
            kept[cid] = "expected_rule text changed since the reviewed packet"
        elif case["case_revision"] != revision:
            kept[cid] = f"case_revision {case['case_revision']} differs from the reviewed {revision}"
        else:
            approvable.append(cid)
    return approvable, kept


def build_record(
    gate: str,
    decision_path: Path,
    decision_sha256: str,
    approver: str,
    approvable: Sequence[str],
    recorded_on: str,
    root: Path,
) -> Dict[str, Any]:
    """The ``approvals.json`` content."""
    ref = decision_path.resolve().relative_to(root.resolve()).as_posix()
    return {
        "record": "Owner gate approval transcribed by python -m evals.binary.approve (human act, not an agent's)",
        "human_gates": {
            gate: {
                "status": "approved",
                "decision_ref": ref,
                "decision_sha256": decision_sha256,
                "approved_by": approver,
                "recorded_on": recorded_on,
            }
        },
        "cases": {cid: {"status": "approved", "ref": ref, "approved_by": approver} for cid in sorted(approvable)},
    }


def record(
    gate: str,
    decision: Path,
    approver: str,
    root: Path = REPO_ROOT,
    cases_dir: Path = CASES_DIR,
    packet: Optional[Path] = None,
    recorded_on: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Verify, plan and (unless ``dry_run``) write the approval record and regenerate the manifest."""
    if gate not in GATES:
        raise ApprovalError(f"unknown gate {gate!r}; recordable gates: {', '.join(GATES)}")
    approver = (approver or "").strip()
    if not approver:
        raise ApprovalError("--approver must name the person recording the approval")
    decision = decision if decision.is_absolute() else root / decision
    try:
        decision.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ApprovalError("the decision file must live inside the repository") from exc
    verify_decision(decision, gate)
    digest = sha256_file(decision)
    packet_path = packet or (root / DEFAULT_PACKETS[gate])
    reviewed = packet_rules(packet_path)
    manifest = load_manifest(cases_dir)
    approvable, kept = plan_cases(manifest.cases, reviewed)
    if not approvable:
        raise ApprovalError("no case matches the reviewed packet; nothing to approve")
    when = recorded_on or _dt.date.today().isoformat()
    data = build_record(gate, decision, digest, approver, approvable, when, root)
    summary = {
        "gate": gate,
        "decision_ref": data["human_gates"][gate]["decision_ref"],
        "decision_sha256": digest,
        "approved_by": approver,
        "recorded_on": when,
        "approved_cases": sorted(approvable),
        "kept_proposed": kept,
        "written": [],
        "dry_run": dry_run,
    }
    if dry_run:
        return summary
    out = root / import_companion.APPROVALS
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    written = [out] + import_companion.write_outputs(root=root, cases_dir=cases_dir)
    stale = import_companion.check_outputs(root=root, cases_dir=cases_dir)
    if stale:
        raise ApprovalError(f"generated files are inconsistent after recording: {stale}")
    summary["written"] = [str(p) for p in written]
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point (owner-run)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gate", required=True, choices=GATES)
    parser.add_argument("--decision", required=True, type=Path, help="the owner's decision.json for the gate")
    parser.add_argument("--approver", required=True, help="name of the person recording the approval")
    parser.add_argument("--packet", type=Path, default=None,
                        help="the gate packet report whose rule texts were reviewed (default: the pre-H3 report)")
    parser.add_argument("--recorded-on", default=None, help="ISO date (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    args = parser.parse_args(argv)
    try:
        summary = record(args.gate, args.decision, args.approver, packet=args.packet,
                         recorded_on=args.recorded_on, dry_run=args.dry_run)
    except ApprovalError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    verb = "would approve" if summary["dry_run"] else "approved"
    print(f"{summary['gate']}: {verb} {len(summary['approved_cases'])} case(s) on the authority of"
          f" {summary['decision_ref']} (sha256 {summary['decision_sha256'][:16]}...) recorded by"
          f" {summary['approved_by']} on {summary['recorded_on']}")
    for cid, why in sorted(summary["kept_proposed"].items()):
        print(f"  kept proposed: {cid}: {why}")
    for path in summary["written"]:
        print("wrote", path)
    if not summary["dry_run"]:
        print("next: review `git diff evals/binary/cases evals/binary/approvals.json` and commit it as the owner")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The owner-run approval recorder (``python -m evals.binary.approve``) on a temporary copy.

Nothing here touches the committed manifest: every test copies the case files, the H3
decision and the packet report into a temporary root and records there.
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evals.binary import approve, import_companion  # noqa: E402
from evals.binary.model import CASES_DIR, REPO_ROOT, load_manifest  # noqa: E402
from evals.binary.runner import Runner  # noqa: E402
import binary_helpers as H  # noqa: E402

DECISION = "docs/migration/reviews/H3/decision.json"
PACKET = approve.DEFAULT_PACKETS["H3"]


@pytest.fixture
def temp_root(tmp_path):
    """A repository-shaped temp root: committed case files, decision.json and the packet report."""
    root = tmp_path / "repo"
    cases = root / "evals" / "binary" / "cases"
    shutil.copytree(CASES_DIR, cases)
    for rel in (DECISION, PACKET):
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, dst)
    return root, cases


def _record(root, cases, **kw):
    args = dict(gate="H3", decision=Path(DECISION), approver="Project Owner", root=root, cases_dir=cases,
                recorded_on="2026-10-30")
    args.update(kw)
    return approve.record(**args)


def test_committed_tree_carries_the_owner_h3_approval_record():
    """The owner ran ``python -m evals.binary.approve --gate H3`` on 2026-11-06; the record is committed."""
    path = REPO_ROOT / import_companion.APPROVALS
    assert path.exists()
    record = import_companion.load_approvals()
    assert record is not None
    gate = record["human_gates"]["H3"]
    assert gate["status"] == "approved" and gate["approved_by"] and gate["decision_ref"] == DECISION
    assert gate["decision_sha256"] == hashlib.sha256((REPO_ROOT / DECISION).read_bytes()).hexdigest()
    assert load_manifest().header["human_gates"] == {"H3": "approved"}


def test_recording_approves_every_case_whose_rule_matches_the_packet_and_regenerates(temp_root):
    root, cases = temp_root
    summary = _record(root, cases)
    assert summary["kept_proposed"] == {} and len(summary["approved_cases"]) == 108
    assert not summary["dry_run"] and summary["written"]
    digest = hashlib.sha256((root / DECISION).read_bytes()).hexdigest()
    assert summary["decision_sha256"] == digest and summary["decision_ref"] == DECISION

    record = json.loads((root / import_companion.APPROVALS).read_text(encoding="utf-8"))
    assert record["human_gates"]["H3"] == {
        "status": "approved",
        "decision_ref": DECISION,
        "decision_sha256": digest,
        "approved_by": "Project Owner",
        "recorded_on": "2026-10-30",
    }
    assert set(record["cases"]) == set(load_manifest().by_id)

    manifest = load_manifest(cases)
    assert manifest.validate() == []
    assert manifest.header["human_gates"] == {"H3": "approved"}
    assert manifest.header["human_gate_records"]["H3"]["decision_sha256"] == digest
    assert manifest.header["status"] == "h3_recorded_by_owner"
    for case in manifest.cases:
        assert case["owner_approval"] == {"status": "approved", "ref": DECISION, "approved_by": "Project Owner"}
    # the generator reproduces exactly what was written, so --check passes on the recorded tree
    assert import_companion.check_outputs(root=root, cases_dir=cases) == []
    # rubrics are untouched
    rubrics = json.loads((cases / "rubrics.json").read_text(encoding="utf-8"))
    assert rubrics["status"] == "draft_not_approved"


def test_the_gate_reads_the_recorded_approval(temp_root):
    root, cases = temp_root
    _record(root, cases)
    manifest = load_manifest(cases)
    executor = H.FakeExecutor({})
    report = Runner(manifest, executor=executor, now=H.FIXED_NOW, revision=H.NO_REVISION).run(only=["AUTH-01"])
    assert report["gate"]["human_gates"] == {"H3": "approved"}
    assert "human_gate_not_approved" not in report["gate"]["gate_reasons"]
    assert report["results"][0]["approval_status"] == "approved"


def test_a_case_whose_rule_changed_since_the_packet_stays_proposed(temp_root, tmp_path):
    root, cases = temp_root
    packet = json.loads((root / PACKET).read_text(encoding="utf-8"))
    for row in packet["results"]:
        if row["case_id"] == "AUTH-01":
            row["expected_rule"] += " (reworded after the review)"
        if row["case_id"] == "DEP-02":
            row["case_revision"] = 1
    altered = tmp_path / "altered_packet.json"
    altered.write_text(json.dumps(packet), encoding="utf-8")
    summary = _record(root, cases, packet=altered)
    assert set(summary["kept_proposed"]) == {"AUTH-01", "DEP-02"}
    assert "expected_rule" in summary["kept_proposed"]["AUTH-01"]
    assert "case_revision" in summary["kept_proposed"]["DEP-02"]
    manifest = load_manifest(cases)
    assert manifest.by_id["AUTH-01"]["owner_approval"] == {"status": "proposed", "ref": None, "approved_by": None}
    assert manifest.by_id["DEP-02"]["owner_approval"]["status"] == "proposed"
    assert manifest.by_id["AUTH-02"]["owner_approval"]["status"] == "approved"
    assert manifest.header["human_gates"] == {"H3": "approved"}
    assert import_companion.check_outputs(root=root, cases_dir=cases) == []


def test_dry_run_writes_nothing(temp_root):
    root, cases = temp_root
    before = {p: p.read_bytes() for p in cases.iterdir()}
    summary = _record(root, cases, dry_run=True)
    assert summary["dry_run"] and summary["written"] == [] and len(summary["approved_cases"]) == 108
    assert not (root / import_companion.APPROVALS).exists()
    assert {p: p.read_bytes() for p in cases.iterdir()} == before
    # The copied cases carry whatever the committed tree recorded; a dry run leaves that header as it was.
    assert load_manifest(cases).header["human_gates"] == load_manifest(CASES_DIR).header["human_gates"]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.update(status="pending"), "not 'approved'"),
        (lambda d: d.update(gate="H4"), "not H3"),
        (lambda d: d.update(status="rejected"), "not 'approved'"),
    ],
)
def test_a_decision_that_does_not_approve_the_gate_is_refused_without_writes(temp_root, mutate, message):
    root, cases = temp_root
    path = root / DECISION
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    before = {p: p.read_bytes() for p in cases.iterdir()}
    with pytest.raises(approve.ApprovalError, match=message):
        _record(root, cases)
    assert not (root / import_companion.APPROVALS).exists()
    assert {p: p.read_bytes() for p in cases.iterdir()} == before


def test_missing_decision_empty_approver_unknown_gate_and_outside_file_are_refused(temp_root, tmp_path):
    root, cases = temp_root
    with pytest.raises(approve.ApprovalError, match="not found"):
        _record(root, cases, decision=Path("docs/migration/reviews/H3/nope.json"))
    with pytest.raises(approve.ApprovalError, match="approver"):
        _record(root, cases, approver="   ")
    with pytest.raises(approve.ApprovalError, match="unknown gate"):
        _record(root, cases, gate="H4")
    outside = tmp_path / "elsewhere.json"
    shutil.copy(REPO_ROOT / DECISION, outside)
    with pytest.raises(approve.ApprovalError, match="inside the repository"):
        _record(root, cases, decision=outside)
    assert not (root / import_companion.APPROVALS).exists()


def test_the_generator_refuses_a_malformed_approval_record(temp_root):
    root, _ = temp_root
    out = root / import_companion.APPROVALS
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('{"something": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="approval record"):
        import_companion.load_approvals(root)


def test_cli_dry_run_on_the_committed_tree_plans_without_writing(capsys):
    """The command the owner runs, with --dry-run, against the real tree: plan only."""
    approvals_before = (REPO_ROOT / import_companion.APPROVALS).read_bytes()
    rc = approve.main(["--gate", "H3", "--decision", DECISION, "--approver", "Project Owner", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0 and "would approve 108 case(s)" in out and "wrote" not in out
    assert (REPO_ROOT / import_companion.APPROVALS).read_bytes() == approvals_before
    assert load_manifest().header["human_gates"] == {"H3": "approved"}


def test_cli_refusal_exits_2(tmp_path, capsys):
    bad = tmp_path / "decision.json"
    bad.write_text(json.dumps({"gate": "H3", "status": "pending"}), encoding="utf-8")
    rc = approve.main(["--gate", "H3", "--decision", str(bad), "--approver", "x", "--dry-run"])
    assert rc == 2 and "refused" in capsys.readouterr().err

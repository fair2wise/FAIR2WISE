"""T10: controlled publication job against a fake PublishStep (T08B fulfils the port)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.modules.contracts.context import ActorKind
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState, StageOutcome
from app.modules.jobs.publish_ports import PackageProblem
from app.modules.jobs.snapshot_source import DirectorySnapshotSource, staged_packages_check
from app.modules.jobs.telemetry import job_progress
from tests.jobs.jobs_fakes import local_ctx
from tests.jobs.publish_fakes import NOW, TARGET, World, approval_for


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path)
    world.snapshots.add()
    return world


def to_review(w, **kw):
    handle = w.submit(**kw)
    assert w.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    return handle.job_id


def publish_fully(w, ref="rsoxs/v1"):
    job_id = to_review(w, ref=ref)
    w.approve(job_id)
    assert w.work() == (job_id, JobState.SUCCEEDED)
    return job_id


# --- happy path and the stage order ------------------------------------------------------------------------------


def test_plan_then_wait_for_review_without_touching_the_target(w):
    job_id = to_review(w)
    assert w.tiled.writes == [] and w.pub.pointer(TARGET) is None
    plan = w.pub.current_plan(w.ctx, job_id)
    assert plan.creates == 3 and plan.links == 2 and plan.target == TARGET
    stages = {s.stage: s.status for s in w.jobs_store.stages(job_id)}
    assert stages["plan"] is StageOutcome.COMPLETED and stages["review"] is StageOutcome.PENDING
    assert "import" not in stages and w.step.apply_calls == 0


def test_full_publication_verifies_receipts_and_activates(w):
    job_id = publish_fully(w)
    assert len(w.tiled.entities) == 3 and len(w.tiled.links) == 2
    plan = w.pub.current_plan(w.ctx, job_id)
    receipt = w.pubstore.receipt_for(plan.plan_sha256, TARGET)
    assert receipt.verified and receipt.snapshot_sha256 == plan.input_sha256
    assert w.pubstore.receipt_for(plan.plan_sha256, "http://other/api#x") is None  # valid only for its target
    pointer = w.pub.pointer(TARGET)
    assert pointer.snapshot_ref == "rsoxs/v1" and pointer.receipt_id == receipt.receipt_id and pointer.version == 1
    stages = [s.stage for s in w.jobs_store.stages(job_id)]
    assert stages == ["plan", "review", "revalidate", "import", "verify", "receipt", "activate"]
    kinds = [(e.kind, e.phase) for _s, e in w.log.events()]
    assert ("import_step", "intent") in kinds and ("activation", "outcome") in kinds and w.log.verify_chain()[0]
    assert w.pubstore.approval_status("appr-1")[0] == "consumed"


def test_telemetry_reports_review_and_import_progress_separately(w):
    job_id = to_review(w)
    report = job_progress(w.jobs_store, w.service.kind("publish").pipeline, job_id)
    assert report["state"] == "awaiting_review"
    assert report["phases"]["review"]["counts"] == {"pending": 1}
    assert report["phases"]["validation"]["counts"] == {"completed": 1, "pending": 1}
    w.approve(job_id)
    w.work()
    done = job_progress(w.jobs_store, w.service.kind("publish").pipeline, job_id)
    assert done["phases"]["import"]["counts"] == {"completed": 4}
    assert done["phases"]["import"]["progress"]["import.created"] == 5


def test_import_requires_the_publish_capability_to_submit(w):
    with pytest.raises(ContractError) as err:
        w.pub.submit(local_ctx("chat.ask"), "rsoxs/v1", TARGET)
    assert err.value.code is ErrorCode.DENIED


# --- approvals ---


def test_models_workers_and_unprivileged_callers_cannot_approve(w):
    job_id = to_review(w)
    plan = w.pub.current_plan(w.ctx, job_id)
    for ctx in (
        local_ctx("curation.approve", actor_kind=ActorKind.WORKER),
        local_ctx("curation.approve", actor_kind=ActorKind.FORGE_DELEGATE),
        local_ctx("graph.publish"),
    ):
        with pytest.raises(ContractError) as err:
            w.pub.submit_approval(ctx, job_id, approval_for(plan))
        assert err.value.code is ErrorCode.DENIED
    assert w.state(job_id) is JobState.AWAITING_REVIEW and w.work() is None


def test_approval_for_a_different_plan_is_stale(w):
    job_id = to_review(w)
    plan = w.pub.current_plan(w.ctx, job_id)
    forged = approval_for(plan).model_copy(update={"plan_sha256": "f" * 64})
    with pytest.raises(ContractError) as err:
        w.pub.submit_approval(w.ctx, job_id, forged)
    assert err.value.code is ErrorCode.STALE_APPROVAL


@pytest.mark.parametrize(
    "attr,value,label", [("schema", "schema-2", "schema_changed"), ("vocab", "vocab-2", "vocabulary_changed")]
)
def test_approval_arriving_after_schema_or_vocabulary_change_is_stale(w, attr, value, label):
    job_id = to_review(w)
    setattr(w.probe, attr, value)
    with pytest.raises(ContractError) as err:
        w.pub.submit_approval(w.ctx, job_id, approval_for(w.pub.current_plan(w.ctx, job_id)))
    assert err.value.code is ErrorCode.STALE_APPROVAL and label in str(err.value)
    assert w.tiled.writes == []
    # B27 (owner decision 2026-10-30): the stale approval re-queues the job; one run detects the staleness,
    # re-plans under the new revisions in the same run and waits for the approval of the NEW plan.
    assert w.state(job_id) is JobState.QUEUED
    assert w.work() == (job_id, JobState.AWAITING_REVIEW)
    record = w.pubstore.current_plan(job_id)
    assert (record.schema_fingerprint if attr == "schema" else record.vocabulary_revision) == value
    assert w.work() is None  # nothing more to do until a human approves the new plan
    w.approve(job_id, approval_id="appr-2")
    assert w.work() == (job_id, JobState.SUCCEEDED)


def test_candidate_changed_after_approval_invalidates_it_before_import(w):
    job_id = to_review(w)
    w.approve(job_id)
    w.probe.vocab = "vocab-2"  # the vocabulary moved between approval and import
    assert w.work() == (job_id, JobState.AWAITING_REVIEW)
    assert w.tiled.writes == [] and w.step.apply_calls == 0
    assert w.pubstore.approval_status("appr-1")[0] == "invalidated"
    assert "vocabulary_changed" in w.pubstore.approval_status("appr-1")[1]
    # B27: the same run re-planned under the new vocabulary; the new plan now awaits its own approval
    assert w.pubstore.current_plan(job_id).vocabulary_revision == "vocab-2"
    assert w.work() is None
    w.approve(job_id, approval_id="appr-2")
    assert w.work() == (job_id, JobState.SUCCEEDED)


def test_target_changed_by_someone_else_makes_the_plan_stale(w):
    job_id = to_review(w)
    w.approve(job_id)
    w.tiled.entities.add("foreign:node")  # another writer changed the target since planning
    w.tiled.writes.append("foreign:node")
    assert w.work() == (job_id, JobState.AWAITING_REVIEW)
    assert w.step.apply_calls == 0
    assert "target_revision_changed" in w.pubstore.approval_status("appr-1")[1]


def test_expired_approval_is_refused_at_import_time(w):
    job_id = to_review(w)
    plan = w.pub.current_plan(w.ctx, job_id)
    w.pub.submit_approval(w.ctx, job_id, approval_for(plan, expires_at=NOW + timedelta(seconds=30)))
    w.clock.advance(31)
    assert w.work() == (job_id, JobState.AWAITING_REVIEW)
    assert w.step.apply_calls == 0


def test_scope_change_or_revocation_invalidates_dependent_approvals(w):
    job_id = to_review(w)
    w.approve(job_id)
    w.service.notify_scope_change({"subject": "local-operator", "reason": "credential revoked"})
    assert w.pubstore.approval_status("appr-1") == ("invalidated", "credential revoked")
    assert w.work() == (job_id, JobState.AWAITING_REVIEW)
    assert w.step.apply_calls == 0 and w.tiled.writes == []


def test_worker_losing_authority_stops_and_invalidates_approvals(w):
    job_id = to_review(w)
    w.approve(job_id)

    def revoked(job, caps):
        raise ContractError(ErrorCode.DENIED, "worker stopped: credential revoked")

    w.service.register("publish", w.pub.pipeline, capability="graph.publish", authority=revoked)
    assert w.work() == (job_id, JobState.FAILED)
    assert w.jobs_store.stage(job_id, "import").error_code is ErrorCode.DENIED
    assert w.pubstore.approval_status("appr-1")[0] == "invalidated"
    assert w.tiled.writes == [] and w.step.apply_calls == 0


# --- quarantine and the frozen snapshot --------------------------------------------------------------------------


def test_incomplete_packages_are_quarantined_with_a_reason_and_block_publication(tmp_path):
    def packages(snapshot):
        return [
            PackageProblem("pkg-aaaa1111", "incomplete stage(s): extraction; 2 extraction chunk(s) failed"),
            PackageProblem("pkg-bbbb2222", "package is missing from staging"),
        ]

    w = World(tmp_path, packages=packages)
    w.snapshots.add()
    handle = w.submit()
    assert w.work() == (handle.job_id, JobState.FAILED)
    rows = w.pubstore.quarantined(handle.job_id)
    assert [r["subject"] for r in rows] == ["pkg-aaaa1111", "pkg-bbbb2222"]
    assert "extraction chunk" in rows[0]["reason"]
    assert w.pub.pointer(TARGET) is None and w.tiled.writes == [] and w.step.apply_calls == 0
    assert w.jobs_store.stage(handle.job_id, "plan").status is StageOutcome.QUARANTINED


def test_staged_package_check_reads_real_paper_json_headers(tmp_path):
    root = tmp_path / "staged"
    for key, body in {
        "good": {"complete": True, "stages": {"intake": "complete"}},
        "half": {
            "complete": False,
            "stages": {"intake": "complete", "extraction": "incomplete"},
            "incomplete_chunks": [1, 2],
        },
    }.items():
        (root / key).mkdir(parents=True)
        (root / key / "paper.json").write_text(json.dumps(body))
    (root / "corrupt").mkdir()
    (root / "corrupt" / "paper.json").write_text("{nope")
    snap, _ = __import__("tests.jobs.publish_fakes", fromlist=["make_snapshot"]).make_snapshot(
        package_keys=("good", "half", "gone", "corrupt")
    )
    problems = {p.key: p.reason for p in staged_packages_check(root)(snap)}
    assert set(problems) == {"half", "gone", "corrupt"}
    assert "extraction" in problems["half"] and "2 extraction chunk" in problems["half"]
    assert "missing" in problems["gone"] and "corrupt" in problems["corrupt"]


def test_snapshot_changed_after_planning_is_quarantined_not_imported(w):
    job_id = to_review(w)
    w.approve(job_id)
    w.snapshots.tamper("rsoxs/v1")
    assert w.work() == (job_id, JobState.FAILED)
    assert w.step.apply_calls == 0 and w.tiled.writes == []
    assert w.pubstore.quarantined(job_id)[0]["reason"].startswith("frozen snapshot bytes changed")


def test_failure_of_the_step_never_invalidates_the_snapshot(w):
    snap_before = w.snapshots.load("rsoxs/v1")
    job_id = to_review(w)
    w.approve(job_id)
    w.step.fail_apply = RuntimeError("tiled returned 503")
    assert w.work() == (job_id, JobState.FAILED)
    assert w.snapshots.load("rsoxs/v1") == snap_before and w.pub.pointer(TARGET) is None
    # a later retry with a healthy step succeeds from the same, unchanged snapshot
    w.step.fail_apply = None
    w.service.resume(w.ctx, job_id)
    assert w.work() == (job_id, JobState.SUCCEEDED)
    assert len(w.tiled.entities) == 3


def test_readback_verification_failure_quarantines_and_issues_no_receipt_or_pointer(w):
    job_id = to_review(w)
    w.approve(job_id)
    w.step.corrupt_readback = True
    assert w.work() == (job_id, JobState.FAILED)
    plan = w.pub.current_plan(w.ctx, job_id)
    assert w.pubstore.receipt_for(plan.plan_sha256, TARGET) is None and w.pub.pointer(TARGET) is None
    assert "read-back verification failed" in w.pubstore.quarantined(job_id)[0]["reason"]
    assert w.jobs_store.stage(job_id, "verify").status is StageOutcome.QUARANTINED


# --- publisher lease, audit, index and pointer ---


def test_competing_publisher_for_the_same_target_is_refused(w):
    job_id = to_review(w)
    w.approve(job_id)
    held = w.jobs_store.acquire_lease("publish:" + TARGET, "another-publisher")
    assert w.work() == (job_id, JobState.FAILED)  # single-writer lease: the second publisher does not write
    assert w.tiled.writes == [] and w.step.apply_calls == 0
    row = w.jobs_store.stage(job_id, "import")
    assert row.error_code is ErrorCode.CONFLICT
    w.jobs_store.release_lease(held)
    w.service.resume(w.ctx, job_id)
    assert w.work() == (job_id, JobState.SUCCEEDED)


def test_audit_unavailable_blocks_the_import_and_nothing_is_written(w):
    job_id = to_review(w)
    w.approve(job_id)

    def broken(*a, **k):
        raise OSError("disk full")

    w.log.probe, w.log.append = broken, broken
    assert w.work() == (job_id, JobState.FAILED)
    assert w.step.apply_calls == 0 and w.tiled.writes == []
    del w.log.probe, w.log.append  # storage returns
    w.service.resume(w.ctx, job_id)
    assert w.work() == (job_id, JobState.SUCCEEDED) and w.tiled.writes


def test_index_failure_leaves_previous_pair_active(w):
    publish_fully(w)
    old = w.pub.pointer(TARGET)
    w.snapshots.add(ref="rsoxs/v2", nodes=("x", "y"), links=(("x", "y"),))
    job2 = to_review(w, ref="rsoxs/v2")
    w.approve(job2, approval_id="appr-2")
    w.index.fail_verify = True
    assert w.work() == (job2, JobState.FAILED)
    assert w.pub.pointer(TARGET) == old  # the old graph/index pair stays active
    assert w.pubstore.receipt_for(w.pub.current_plan(w.ctx, job2).plan_sha256, TARGET).verified  # receipt kept
    w.index.fail_verify = False
    w.service.resume(w.ctx, job2)
    assert w.work() == (job2, JobState.SUCCEEDED)
    assert w.pub.pointer(TARGET).snapshot_ref == "rsoxs/v2" and w.pub.pointer(TARGET).version == 2


def test_rollback_restores_previous_pointer_and_keeps_candidate_history(w):
    publish_fully(w)
    first = w.pub.pointer(TARGET)
    w.snapshots.add(ref="rsoxs/v2", nodes=("x",), links=())
    job2 = to_review(w, ref="rsoxs/v2")
    w.approve(job2, approval_id="appr-2")
    w.work()
    assert w.pub.pointer(TARGET).snapshot_ref == "rsoxs/v2"
    restored = w.pub.rollback(w.ctx, TARGET, reason="bad readback")
    assert (
        restored.snapshot_ref == "rsoxs/v1" and restored.receipt_id == first.receipt_id and restored.version == 3
    )
    hist = [(h["version"], h["action"]) for h in w.pubstore.pointer_history(TARGET)]
    assert hist == [(1, "activate"), (2, "activate"), (3, "rollback")]
    assert w.pubstore.receipt_for(w.pub.current_plan(w.ctx, job2).plan_sha256, TARGET)  # candidate retained
    assert ("pointer_rollback", "outcome") in [(e.kind, e.phase) for _s, e in w.log.events()]
    with pytest.raises(ContractError):
        w.pub.rollback(local_ctx("graph.publish", actor_kind=ActorKind.WORKER), TARGET, reason="x")


def test_pointer_only_moves_to_a_verified_receipt_for_its_own_target(w):
    job_id = publish_fully(w)
    plan = w.pub.current_plan(w.ctx, job_id)
    receipt = w.pubstore.receipt_for(plan.plan_sha256, TARGET)
    with pytest.raises(ContractError):
        w.pubstore.activate(
            "http://other/api#x",
            snapshot_ref="r",
            snapshot_sha256=receipt.snapshot_sha256,
            receipt=receipt,
            index_ref="i",
            expected_version=0,
            actor="a",
        )
    unverified = receipt.model_copy(update={"verified": False})
    with pytest.raises(ContractError):
        w.pubstore.activate(
            TARGET,
            snapshot_ref="r",
            snapshot_sha256=receipt.snapshot_sha256,
            receipt=unverified,
            index_ref="i",
            expected_version=1,
            actor="a",
        )
    with pytest.raises(ContractError) as err:  # compare-and-swap: a stale reader cannot overwrite
        w.pubstore.activate(
            TARGET,
            snapshot_ref="r2",
            snapshot_sha256=receipt.snapshot_sha256,
            receipt=receipt,
            index_ref="i2",
            expected_version=0,
            actor="a",
        )
    assert err.value.code is ErrorCode.CONFLICT


def test_directory_snapshot_source_verifies_real_t23b_snapshots(tmp_path):
    from app.modules.graph.snapshot import SnapshotRequest, create_snapshot
    from tests.snapshots.snapshot_fakes import COMMIT, INPUTS

    storage = tmp_path / "storage" / "kg"
    storage.mkdir(parents=True)
    result = create_snapshot(
        SnapshotRequest(
            corpus="synthetic",
            terms_path=INPUTS / "core_dict.terms.json",
            storage_kg=storage,
            terms_ref="tests/contracts/json_kg/fixtures/inputs/core_dict.terms.json",
            code_commit=COMMIT,
        )
    )
    source = DirectorySnapshotSource(storage)
    ref = f"synthetic/{result.snapshot_id}"
    snap = source.load(ref)
    assert snap.corpus == "synthetic" and len(snap.sha256) == 64 and snap.manifest.graph_sha256 == snap.sha256
    graph = storage / "synthetic" / result.snapshot_id / "graph.json"
    graph.chmod(0o644)
    graph.write_bytes(graph.read_bytes() + b" ")
    with pytest.raises(ContractError):
        source.load(ref)  # edited after freezing: refused
    for bad in ("nocorpus", "a/../b", "a/v1/extra"):
        with pytest.raises(ContractError):
            source.load(bad)

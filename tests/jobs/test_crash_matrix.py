"""T10 plan section 11.3: the mandatory failure-injection matrix, one test per boundary.

Each test crashes or degrades the real component at that boundary (a ``Crash`` is a
``BaseException``, so nothing handles it, exactly like a dead process) and checks the recovery
rule the plan states. Publication cases use the fake PublishStep (T08B fulfils the real one).
"""

from __future__ import annotations

import pytest

from app.modules.contracts.context import ActorKind
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState, StageOutcome
from app.modules.contracts.ports import GraphCapabilities
from app.modules.curation.budgets import BudgetLedger
from app.modules.curation.cost_ledger import CallOutcome, settle_outcome
from app.modules.curation.repair import IncompleteRead, plan_reextraction
from app.modules.jobs import JobService, JobStore, Pipeline, StageSpec
from app.modules.jobs.sqlite import OperationalDB
from app.modules.jobs.stages import acquire_stage
from tests.jobs.jobs_fakes import Clock, Crash, inputs, local_ctx
from tests.jobs.publish_fakes import TARGET, World, approval_for
from tests.jobs.test_pipeline import FakeAcquirer, chunk_pipeline
from tests.literature.lit_fakes import local_ctx as lit_ctx, minimal_pdf_bytes


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path)
    world.snapshots.add()
    return world


def approved_job(w):
    handle = w.submit()
    assert w.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    w.approve(handle.job_id)
    return handle.job_id


# 1 ---------------------------------------------------------------------------------------------------------------
def test_case_01_crash_after_pdf_download_before_manifest_update(tmp_path):
    from app.modules.literature.store import FileDocumentStore

    docs = FileDocumentStore(tmp_path / "docs")
    acquirer = FakeAcquirer(docs, minimal_pdf_bytes("paper"))
    acquirer.crash_after_download = True
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    service = JobService(store)
    stage = acquire_stage(acquirer, docs, doi_of=lambda sc: "10.1/x", ctx_for=lambda sc: lit_ctx())
    service.register("ingest", Pipeline([StageSpec("acquire", stage, phase="parsing")]))
    handle = service.submit(local_ctx(), "ingest", inputs())
    with pytest.raises(Crash):
        service.work_one("w1")
    clock.advance(61)
    acquirer.crash_after_download = False
    assert service.work_one("w2") == (handle.job_id, JobState.SUCCEEDED)
    assert acquirer.downloads == 1  # recovered the verified asset, no duplicate acquisition


# 2 ---------------------------------------------------------------------------------------------------------------
def test_case_02_crash_during_extraction_retains_chunks_and_incomplete_coverage_blocks_publication(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    first, second = [], []
    svc = JobService(store)
    svc.register("ingest", Pipeline(chunk_pipeline(first, crash_on="c2")))
    handle = svc.submit(local_ctx(), "ingest", inputs())
    with pytest.raises(Crash):
        svc.work_one("w1")
    clock.advance(61)
    svc2 = JobService(store)
    svc2.register("ingest", Pipeline(chunk_pipeline(second, fail_ids=("c3",))))
    assert svc2.work_one("w2") == (handle.job_id, JobState.FAILED)
    assert first == ["c0", "c1"] and second == ["c2"]  # compatible chunks retained, only the rest re-run
    assert store.stage(handle.job_id, "extract").status is StageOutcome.FAILED
    assert store.get(handle.job_id).state is not JobState.SUCCEEDED  # incomplete coverage never reads as success


# 3 ---------------------------------------------------------------------------------------------------------------
def test_case_03_lease_expiry_while_worker_alive_stale_owner_cannot_apply_later_mutations(w):
    job_id = approved_job(w)
    holder = {}

    def stall_after_entities():
        w.clock.advance(120)  # worker is alive but its lease (60s) has expired
        holder["rescuer"] = w.jobs_store.claim_next("rescuer")

    w.step.after_entities = stall_after_entities
    assert w.work("stale-worker") == (job_id, None)  # stale owner is fenced out; it applied nothing further
    assert len(w.tiled.entities) == 3 and w.tiled.links == set()  # links were NOT written by the stale owner
    w.step.after_entities = None
    lease, record = holder["rescuer"]
    assert w.service.kind("publish").runner.run(lease, record) is JobState.SUCCEEDED
    assert len(w.tiled.entities) == 3 and len(w.tiled.links) == 2  # new owner completed the plan, no duplicates


# 4 ---------------------------------------------------------------------------------------------------------------
def test_case_04_crash_after_graph_entities_before_links_and_receipt(w):
    job_id = approved_job(w)
    w.step.crash_after_entities = True
    with pytest.raises(Crash):
        w.work("w1")
    assert len(w.tiled.entities) == 3 and w.tiled.links == set()
    assert w.pubstore.receipt_for(w.pub.current_plan(w.ctx, job_id).plan_sha256, TARGET) is None
    w.step.crash_after_entities = False
    w.clock.advance(61)
    assert w.work("w2") == (job_id, JobState.SUCCEEDED)
    assert len(w.tiled.entities) == 3 and len(w.tiled.links) == 2
    assert w.tiled.writes.count(next(iter(w.tiled.entities))) == 1  # every scientific fact written exactly once
    assert w.pubstore.receipt_for(w.pub.current_plan(w.ctx, job_id).plan_sha256, TARGET).verified


# 5 ---------------------------------------------------------------------------------------------------------------
def test_case_05_crash_after_publication_before_active_index_switch(w):
    first = approved_job(w)
    assert w.work() == (first, JobState.SUCCEEDED)
    old = w.pub.pointer(TARGET)
    w.snapshots.add(ref="rsoxs/v2", nodes=("x", "y"), links=(("x", "y"),))
    handle = w.submit(ref="rsoxs/v2")
    assert w.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    w.approve(handle.job_id, approval_id="appr-2")
    w.index.crash_on_build = True
    with pytest.raises(Crash):
        w.work("w1")
    assert w.pub.pointer(TARGET) == old  # the old graph/index pair remains usable
    assert w.pubstore.receipt_for(w.pub.current_plan(w.ctx, handle.job_id).plan_sha256, TARGET).verified
    w.index.crash_on_build = False
    w.clock.advance(61)
    assert w.work("w2") == (handle.job_id, JobState.SUCCEEDED)
    new = w.pub.pointer(TARGET)
    assert new.snapshot_ref == "rsoxs/v2" and new.version == old.version + 1


# 6 ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("change", ["vocab", "schema", "candidate"])
def test_case_06_approval_after_candidate_schema_or_vocabulary_changed_is_stale(w, change):
    handle = w.submit()
    assert w.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    plan = w.pub.current_plan(w.ctx, handle.job_id)
    if change == "vocab":
        w.probe.vocab = "vocab-2"
    elif change == "schema":
        w.probe.schema = "schema-2"
    else:
        w.tiled.entities.add("foreign")
        w.tiled.writes.append("foreign")
    with pytest.raises(ContractError) as err:
        w.pub.submit_approval(w.ctx, handle.job_id, approval_for(plan))
    assert err.value.code is ErrorCode.STALE_APPROVAL
    assert w.step.apply_calls == 0 and w.pub.pointer(TARGET) is None


# 7 ---------------------------------------------------------------------------------------------------------------
def test_case_07_provider_bills_failed_response_or_omits_cost_is_reconciled_never_free(tmp_path):
    ledger = BudgetLedger(OperationalDB(tmp_path / "b.sqlite3"), clock=Clock())
    billed_failure = ledger.reserve(corpus="c", case_id="1", model="m", amount_usd=1.0, cap_usd=10.0)
    omitted = ledger.reserve(corpus="c", case_id="2", model="m", amount_usd=1.0, cap_usd=10.0)
    assert settle_outcome(ledger, billed_failure, CallOutcome.from_response(500, "0.30")) == "settled"
    assert settle_outcome(ledger, omitted, CallOutcome.from_response(200, None)) == "settled_unknown"
    summary = ledger.summary(cap_usd=10.0)
    assert summary["known_usd"] == 0.30 and summary["unknown_calls"] == 1 and summary["charged_usd"] == 1.30


# 8 ---------------------------------------------------------------------------------------------------------------
def test_case_08_read_returns_only_a_page_so_repair_planning_aborts():
    caps = GraphCapabilities(backend="memory", complete_listing=True)
    with pytest.raises(IncompleteRead):
        plan_reextraction(
            corpus="rsoxs",
            cases=[],
            graph_read=([{"id": "first-page-only"}], False),
            capabilities=caps,
            extractor_version="e1",
            limit=10,
        )


# 9 ---------------------------------------------------------------------------------------------------------------
def test_case_09_audit_event_cannot_be_written_so_critical_mutation_is_blocked(w):
    job_id = approved_job(w)

    def broken(*args, **kwargs):
        raise OSError("disk full")

    w.log.probe, w.log.append = broken, broken
    assert w.work() == (job_id, JobState.FAILED)
    assert w.step.apply_calls == 0 and w.tiled.writes == []
    assert w.audit.status().healthy is False and "not writable" in w.audit.status().reason
    del w.log.probe, w.log.append  # storage returns: the same job can now proceed
    w.service.resume(w.ctx, job_id)
    assert w.work() == (job_id, JobState.SUCCEEDED) and w.audit.status().healthy


# 10 --------------------------------------------------------------------------------------------------------------
def test_case_10_scope_change_or_revocation_invalidates_dependent_approvals(w):
    job_id = approved_job(w)
    w.service.notify_scope_change(
        {"subject": "local-operator", "corpus_scope": ["rsoxs"], "reason": "scope reduced"}
    )
    assert w.pubstore.approval_status("appr-1")[0] == "invalidated"
    assert w.work() == (job_id, JobState.AWAITING_REVIEW)  # no import, no fallback to an unrestricted credential
    assert w.step.apply_calls == 0 and w.tiled.writes == []
    # and a model/worker/tool still has no way to mint the replacement approval
    plan = w.pub.current_plan(w.ctx, job_id)
    with pytest.raises(ContractError) as err:
        w.pub.submit_approval(
            local_ctx("curation.approve", actor_kind=ActorKind.WORKER), job_id, approval_for(plan)
        )
    assert err.value.code is ErrorCode.DENIED

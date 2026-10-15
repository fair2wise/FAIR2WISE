"""T10: leases, fencing, state machine, events (plan 11.2, 11.3 lease expiry)."""

from __future__ import annotations

import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState, StageOutcome
from app.modules.jobs.keys import OperationInputs, SecretInKey, stage_key
from app.modules.jobs.store import InvalidTransition, JobStore, LeaseLost
from tests.jobs.jobs_fakes import Clock, inputs


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = JobStore(tmp_path / "jobs.sqlite3", lease_seconds=60, clock=clock)
    yield s
    s.close()


def test_submit_is_idempotent_and_emits_event(store):
    a = store.submit("ingest", inputs(), created_by="u1")
    b = store.submit("ingest", inputs(), created_by="u1")
    assert a.job_id == b.job_id and a.state is JobState.QUEUED
    assert [e.stage for e in store.events(a.job_id)] == ["submit"]
    assert store.submit("ingest", inputs(source_sha256="b" * 64), created_by="u1").job_id != a.job_id


def test_keys_refuse_secrets_and_ignore_irrelevant_inputs():
    with pytest.raises(SecretInKey):
        inputs(params={"api_key": "x"})
    with pytest.raises(SecretInKey):
        inputs(model="Bearer abcdefghijkl")
    base = inputs()
    k1 = stage_key(base, "parse", ("source_sha256", "parser_version"), {})
    changed_vocab = inputs(vocabulary_revision="v2")
    assert stage_key(changed_vocab, "parse", ("source_sha256", "parser_version"), {}) == k1
    assert stage_key(inputs(parser_version="p2"), "parse", ("source_sha256", "parser_version"), {}) != k1
    assert OperationInputs.from_dict(base.as_dict()) == base


def test_stale_owner_cannot_mutate_after_expiry_and_takeover(store, clock):
    job = store.submit("ingest", inputs(), created_by="u1")
    lease1, _ = store.claim_next("w1", ttl=30)
    clock.advance(31)  # lease expires while worker 1 is still alive
    with pytest.raises(LeaseLost):
        store.begin_stage(lease1, job.job_id, "parse", 0, "k")
    with pytest.raises(LeaseLost):
        store.renew_lease(lease1)  # a stale owner cannot revive its lease either
    lease2, rec = store.claim_next("w2")
    assert rec.job_id == job.job_id and lease2.fence == lease1.fence + 1
    store.begin_stage(lease2, job.job_id, "parse", 0, "k")
    with pytest.raises(LeaseLost):  # worker 1 wakes up and tries again: fenced out
        store.complete_stage(lease1, job.job_id, "parse", "k")
    with pytest.raises(LeaseLost):
        store.save_unit(lease1, job.job_id, "parse", "u", "uk", {"x": 1})
    assert store.stage(job.job_id, "parse").status is StageOutcome.RUNNING


def test_single_writer_lease(store):
    store.submit("ingest", inputs(), created_by="u1")
    assert store.claim_next("w1") is not None
    assert store.claim_next("w2") is None  # running with a live lease


def test_state_machine_rejects_illegal_moves(store):
    job = store.submit("ingest", inputs(), created_by="u1")
    lease, _ = store.claim_next("w1")
    store.set_state(lease, job.job_id, JobState.SUCCEEDED)
    with pytest.raises(InvalidTransition):
        store.set_state(lease, job.job_id, JobState.RUNNING)
    store.release_lease(lease)
    with pytest.raises(InvalidTransition):
        store.transition_without_lease(job.job_id, JobState.QUEUED, reason="x", actor="u")


def test_resume_while_owner_alive_is_refused_and_revision_cas(store, clock):
    job = store.submit("ingest", inputs(), created_by="u1")
    lease, _ = store.claim_next("w1")
    store.set_state(lease, job.job_id, JobState.PAUSED_BUDGET, reason="cap")
    with pytest.raises(ContractError) as err:
        store.transition_without_lease(job.job_id, JobState.QUEUED, reason="r", actor="u")
    assert err.value.code is ErrorCode.CONFLICT
    store.release_lease(lease)
    rev = store.get(job.job_id).revision
    with pytest.raises(ContractError):
        store.transition_without_lease(
            job.job_id, JobState.QUEUED, reason="r", actor="u", expected_revision=rev - 1
        )
    assert (
        store.transition_without_lease(
            job.job_id, JobState.QUEUED, reason="r", actor="u", expected_revision=rev
        ).state
        is JobState.QUEUED
    )


def test_events_are_sequenced_versioned_and_durable(tmp_path, clock):
    path = tmp_path / "j.sqlite3"
    s = JobStore(path, clock=clock)
    job = s.submit("ingest", inputs(), created_by="u1")
    lease, _ = s.claim_next("w1")
    s.begin_stage(lease, job.job_id, "parse", 0, "k")
    s.update_progress(lease, job.job_id, "parse", {"pages_done": 3})
    s.close()
    again = JobStore(path, clock=clock)
    events = again.events(job.job_id)
    assert [e.sequence for e in events] == list(range(len(events)))
    assert all(e.version == 1 for e in events)
    assert events[-1].progress == {"pages_done": 3}
    assert again.events(job.job_id, after=1)[0].sequence == 2

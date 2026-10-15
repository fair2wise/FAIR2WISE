"""T10 K-13/K-14: durable audit, degraded status, blocked critical mutation, lagging projection."""

from __future__ import annotations

import pytest

from app.modules.provenance import (
    AuditLog,
    AuditRecorder,
    AuditUnavailable,
    GraphWriteEvent,
    QueuedRecorder,
    redact,
)
from app.modules.provenance.canonicalization import (
    ResolutionPlan,
    activity_node_id,
    link_resolved_terms,
    plan_node_id,
    write_activity,
    write_plan,
)
from tests.jobs.jobs_fakes import Clock


class BrokenLog(AuditLog):
    """A log whose storage can be switched off (disk full, permission loss)."""

    broken = False

    def append(self, event):
        if self.broken:
            raise OSError("disk full")
        return super().append(event)

    def probe(self):
        if self.broken:
            raise OSError("disk full")
        return super().probe()


@pytest.fixture
def log(tmp_path):
    return BrokenLog(tmp_path / "audit.sqlite3", clock=Clock())


def test_redaction_removes_secrets_and_bounds_payload():
    out = redact({"api_key": "x", "note": "Bearer abcdef123456", "big": "z" * 5000, "items": list(range(100))})
    assert out["api_key"] == "[redacted]" and "[redacted]" in out["note"]
    assert len(out["big"]) < 600 and len(out["items"]) == 41
    event = GraphWriteEvent.build(kind="graph_write", operation_id="op", actor="u", payload={"token": "t", "n": 1})
    assert event.payload["token"] == "[redacted]" and len(event.payload_sha256) == 64


def test_guard_writes_intent_before_mutation_and_outcome_after(log):
    rec = AuditRecorder(log)
    order = []

    def mutation():
        order.append([e.phase for _, e in log.events()])
        return "done"

    assert rec.guard(kind="graph_write", operation_id="op1", actor="u", target="t", mutation=mutation) == "done"
    assert order == [["intent"]]  # intent was durable before the mutation started
    assert [e.phase for _, e in log.events()] == ["intent", "outcome"]
    assert log.verify_chain() == (True, 2)


def test_failed_mutation_records_failed_outcome(log):
    rec = AuditRecorder(log)
    with pytest.raises(ValueError):
        rec.guard(
            kind="graph_write",
            operation_id="op1",
            actor="u",
            target="t",
            mutation=lambda: (_ for _ in ()).throw(ValueError("boom")),
        )
    assert [(e.phase, e.status) for _, e in log.events()] == [("intent", "started"), ("outcome", "failed")]


def test_audit_unwritable_blocks_critical_mutation_and_reports_degraded(log):
    rec = AuditRecorder(log)
    ran = []
    log.broken = True
    with pytest.raises(AuditUnavailable):
        rec.guard(kind="graph_write", operation_id="op", actor="u", target="t", mutation=lambda: ran.append(1))
    assert ran == []  # the mutation never started
    status = rec.status()
    assert not status.healthy and "not writable" in status.reason
    log.broken = False
    rec.guard(kind="graph_write", operation_id="op", actor="u", target="t", mutation=lambda: ran.append(1))
    assert ran == [1] and rec.status().healthy  # recovered once storage returned


def test_outcome_write_failure_keeps_event_and_blocks_next_mutation_until_flushed(log):
    rec = AuditRecorder(log)
    ran = []

    def mutation():
        ran.append("first")
        log.broken = True  # storage dies after the mutation started
        return 1

    assert rec.guard(kind="graph_write", operation_id="a", actor="u", target="t", mutation=mutation) == 1
    assert rec.status().spilled == 1 and not rec.status().healthy
    with pytest.raises(AuditUnavailable):
        rec.guard(kind="graph_write", operation_id="b", actor="u", target="t", mutation=lambda: ran.append("b"))
    assert ran == ["first"]
    log.broken = False
    rec.guard(kind="graph_write", operation_id="b", actor="u", target="t", mutation=lambda: ran.append("b"))
    ops = [(e.operation_id, e.phase) for _, e in log.events()]
    assert ("a", "outcome") in ops  # the spilled outcome was persisted, not lost
    assert rec.status().healthy and log.verify_chain()[0]


def test_noncritical_events_are_spilled_never_silently_dropped(log):
    rec = AuditRecorder(log, spill_limit=2)
    log.broken = True
    for i in range(4):
        rec.record(GraphWriteEvent.build(kind="note", operation_id=f"n{i}", actor="u"))
    status = rec.status()
    assert status.spilled == 2 and status.dropped == 2 and not status.healthy
    with pytest.raises(AuditUnavailable):
        rec.ensure_healthy()


def test_hash_chain_detects_tampering(log):
    rec = AuditRecorder(log)
    for i in range(3):
        rec.record(GraphWriteEvent.build(kind="note", operation_id=f"n{i}", actor="u"))
    with log.db.tx() as con:
        con.execute("UPDATE audit_events SET event=replace(event, 'n1', 'xx') WHERE seq=2")
    assert log.verify_chain() == (False, 1)


class FlakySink:
    def __init__(self):
        self.up, self.got = False, []

    def project(self, seq, event):
        if not self.up:
            raise ConnectionError("graph down")
        self.got.append(seq)


def test_projection_lags_without_losing_audit(log):
    rec = AuditRecorder(log)
    sink = FlakySink()
    queue = QueuedRecorder(log, sink)
    for i in range(3):
        rec.record(GraphWriteEvent.build(kind="graph_write", operation_id=f"o{i}", actor="u", critical=True))
    assert queue.flush(timeout=0.01) is False  # sink down: reported, not hidden
    stats = queue.stats()
    assert stats["pending"] == 3 and stats["failures"] >= 1
    assert len(log.events()) == 3  # audit is intact
    sink.up = True
    assert queue.flush(timeout=1.0) is True and sink.got == [1, 2, 3]  # ordered replay


def test_queue_worker_thread_projects_and_closes_cleanly(log):
    rec = AuditRecorder(log)
    sink = FlakySink()
    sink.up = True
    queue = QueuedRecorder(log, sink, interval=0.01).start()
    rec.record(GraphWriteEvent.build(kind="graph_write", operation_id="o", actor="u"))
    queue.notify()
    assert queue.close(timeout=2.0) is True and sink.got == [1]


def test_resolution_plan_is_content_addressed_and_unknown_cost_stays_unknown(log):
    rec = AuditRecorder(log)
    plan = ResolutionPlan(
        prompt_sha256="a" * 64,
        schema_sha256="b" * 64,
        model_ref="m",
        f2w_commit="f" * 40,
        donor_commit="d" * 40,
        vocabulary_revision="v1",
    )
    assert plan.plan_id == ResolutionPlan(**plan.model_dump()).plan_id
    node = write_plan(rec, plan, actor="u")
    assert node == plan_node_id(plan.plan_id)
    act = write_activity(
        rec,
        round_key="c:term:1",
        plan=plan,
        started=1.0,
        finished=2.0,
        counts={"resolved": 3},
        cost_usd=None,
        input_tokens=None,
        output_tokens=None,
        actor="u",
    )
    assert act == activity_node_id("c:term:1")
    assert link_resolved_terms(rec, act, {"t1": "canon:a", "t2": "canon:b"}, actor="u") == 2
    payloads = [e.payload for _, e in log.events(kind="provenance")]
    activity = [p for p in payloads if p.get("type") == "activity"][0]
    assert activity["cost_usd"] is None and activity["cost_known"] is False
    assert plan.f2w_commit in str(payloads[0]) and plan.donor_commit in str(payloads[0])

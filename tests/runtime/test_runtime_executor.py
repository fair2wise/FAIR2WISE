"""T02: authorizing executor and write/approval policies (no model, no network)."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from langgraph.errors import GraphInterrupt

from app.modules.agent_runtime.executor import RuntimeLimits, ToolExecutor, TurnLedger
from app.modules.agent_runtime.policies import (
    AuditKind,
    FailingEventSink,
    InMemoryEventSink,
    JsonlEventSink,
)
from app.modules.agent_runtime.registry import ToolRegistry
from app.modules.contracts import ContractError, CostClass, Effect, ErrorCode, ToolResult, ToolStatus
from app.modules.contracts.testing import InMemoryDocumentStore
from tests.runtime.runtime_fakes import (
    NOW,
    CountingAdmission,
    FakeClock,
    Recorder,
    als_ctx,
    local_approval,
    local_ctx,
    make_plan,
    read_spec,
    write_spec,
)


def make(tools, *, sink=None, limits=None, store=None, clock=None, admission=None):
    reg = ToolRegistry()
    for spec, fn in tools:
        reg.register(spec, fn)
    reg.freeze()
    sleeps = []
    ex = ToolExecutor(reg, event_sink=sink if sink is not None else InMemoryEventSink(), limits=limits,
                      artifact_store=store, clock=clock or FakeClock(), sleep=sleeps.append, admission=admission)
    ex.sleeps = sleeps
    return ex


# -- preflight ----------------------------------------------------------------------


def test_unknown_tool_and_invalid_input():
    rec = Recorder()
    ex = make([(read_spec(), rec.ok)])
    assert ex.execute(local_ctx(), "lit_missing", {}).error_code is ErrorCode.NOT_FOUND
    bad = ex.execute(local_ctx(), "lit_echo", {"q": 3})
    assert bad.status is ToolStatus.INVALID and bad.error_code is ErrorCode.INVALID_INPUT
    assert ex.execute(local_ctx(), "lit_echo", {"q": "x", "extra": 1}).status is ToolStatus.INVALID
    assert ex.execute(local_ctx(), "lit_echo", "not-an-object").status is ToolStatus.INVALID
    assert rec.calls == []
    ok = ex.execute(local_ctx(), "lit_echo", {"q": "x"})
    assert ok.status is ToolStatus.OK and ok.data == {"echo": {"q": "x"}}


def test_chat_only_context_denied_write_before_effect():
    rec, sink = Recorder(), InMemoryEventSink()
    ex = make([(write_spec(), rec.ok)], sink=sink)
    result = ex.execute(als_ctx(admin=False), "kg_apply_plan", {"plan_id": "p1"},
                        plan=make_plan(), approval=None)
    assert result.status is ToolStatus.DENIED and result.error_code is ErrorCode.DENIED
    assert rec.calls == []
    assert sink.kinds() == [AuditKind.TOOL_DENIED]
    event = sink.events[0]
    assert event.args_sha256 and event.subject == "alice" and event.tool == "kg_apply_plan"
    assert "args" not in event.model_dump()  # arguments are hashed, never recorded


def test_write_without_approval_or_plan_denied():
    rec, sink = Recorder(), InMemoryEventSink()
    ex = make([(write_spec(), rec.ok)], sink=sink)
    no_plan = ex.execute(local_ctx(), "kg_apply_plan", {"plan_id": "p1"}, approval=local_approval())
    no_approval = ex.execute(local_ctx(), "kg_apply_plan", {"plan_id": "p1"}, plan=make_plan())
    assert no_plan.error_code is ErrorCode.DENIED and no_approval.error_code is ErrorCode.DENIED
    assert rec.calls == [] and AuditKind.WRITE_ATTEMPTED not in sink.kinds()


@pytest.mark.parametrize("approval_kw, plan_kw, args", [
    ({"plan_sha256": "c" * 64}, {}, {"plan_id": "p1"}),          # different hash
    ({"plan_id": "p2"}, {}, {"plan_id": "p1"}),                  # different plan
    ({"target": "tiled-other"}, {}, {"plan_id": "p1"}),          # different target
    ({"expires_at": NOW - timedelta(seconds=1)}, {}, {"plan_id": "p1"}),  # expired
    ({}, {"corpus_scope": ("rsoxs", "saxs")}, {"plan_id": "p1"}),  # plan widened beyond approval scope
    ({}, {}, {"plan_id": "p9"}),                                  # tool args name another plan
])
def test_mismatched_approval_is_stale(approval_kw, plan_kw, args):
    rec, sink = Recorder(), InMemoryEventSink()
    ex = make([(write_spec(), rec.ok)], sink=sink)
    result = ex.execute(local_ctx(), "kg_apply_plan", args, plan=make_plan(**plan_kw),
                        approval=local_approval(**approval_kw))
    assert result.status is ToolStatus.ERROR and result.error_code is ErrorCode.STALE_APPROVAL
    assert rec.calls == [] and sink.kinds() == [AuditKind.TOOL_DENIED]


def test_approval_from_other_profile_denied():
    rec = Recorder()
    ex = make([(write_spec(), rec.ok)])
    ctx = als_ctx("bob", admin=True)
    result = ex.execute(ctx, "kg_apply_plan", {"plan_id": "p1"}, plan=make_plan(), approval=local_approval())
    assert result.error_code is ErrorCode.DENIED and rec.calls == []


def test_matching_approval_writes_once_and_is_audited():
    rec, sink = Recorder(), InMemoryEventSink()
    ex = make([(write_spec(), rec.ok)], sink=sink)
    result = ex.execute(local_ctx(), "kg_apply_plan", {"plan_id": "p1"}, plan=make_plan(),
                        approval=local_approval())
    assert result.status is ToolStatus.OK and len(rec.calls) == 1
    assert rec.calls[0].approval.approval_id == "a1" and rec.calls[0].plan.plan_id == "p1"
    assert sink.kinds() == [AuditKind.WRITE_ATTEMPTED, AuditKind.WRITE_SUCCEEDED]
    assert {e.approval_id for e in sink.events} == {"a1"} and {e.approved_by for e in sink.events} == {
        "local-operator"}


def test_write_refused_without_durable_audit():
    rec = Recorder()
    for sink in (None, FailingEventSink()):
        reg = ToolRegistry()
        reg.register(write_spec(), rec.ok)
        ex = ToolExecutor(reg.freeze(), event_sink=sink, clock=FakeClock())
        result = ex.execute(local_ctx(), "kg_apply_plan", {"plan_id": "p1"}, plan=make_plan(),
                            approval=local_approval())
        assert result.status is ToolStatus.UNAVAILABLE
    assert rec.calls == []


def test_staged_write_needs_no_approval_but_is_audited():
    rec, sink = Recorder(), InMemoryEventSink()
    spec = read_spec("extraction_stage_candidate", domain="extraction", effects=frozenset({Effect.WRITE_STAGED}))
    ex = make([(spec, rec.ok)], sink=sink)
    assert ex.execute(local_ctx(), spec.name, {"q": "x"}).status is ToolStatus.OK
    assert sink.kinds() == [AuditKind.WRITE_ATTEMPTED, AuditKind.WRITE_SUCCEEDED]


def test_jsonl_sink_appends(tmp_path):
    path = tmp_path / "audit.jsonl"
    rec = Recorder()
    ex = make([(write_spec(), rec.ok)], sink=JsonlEventSink(path))
    ex.execute(local_ctx(), "kg_apply_plan", {"plan_id": "p1"}, plan=make_plan(), approval=local_approval())
    ex.execute(local_ctx(), "kg_apply_plan", {"plan_id": "p1"}, plan=make_plan())
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["kind"] for line in lines] == ["write_attempted", "write_succeeded", "tool_denied"]
    assert all("args" not in line for line in lines)


# -- budgets, deadline, repeats ---------------------------------------------------------


def test_repeat_call_refused_from_ledger():
    rec = Recorder()
    ex = make([(read_spec(), rec.ok)])
    ledger = TurnLedger()
    first = ex.execute_call(local_ctx(), "lit_echo", {"q": "x"}, ledger=ledger)
    again = ex.execute_call(local_ctx(), "lit_echo", {"q": "x"}, ledger=ledger)
    assert first.charged and again.repeated and not again.charged
    assert len(rec.calls) == 1 and any("repeated_call" in w for w in again.result.warnings)
    assert again.result.data == first.result.data
    other = ex.execute_call(local_ctx(), "lit_echo", {"q": "y"}, ledger=ledger)
    assert other.charged and len(rec.calls) == 2


def test_call_and_cost_budgets():
    rec = Recorder()
    ex = make([(read_spec(), rec.ok), (read_spec("lit_costly", cost_class=CostClass.EXPENSIVE), rec.ok)],
              limits=RuntimeLimits(max_tool_calls=2, max_cost_units=20))
    ledger = TurnLedger()
    assert ex.execute(local_ctx(), "lit_costly", {"q": "a"}, ledger=ledger).status is ToolStatus.OK
    over_cost = ex.execute(local_ctx(), "lit_costly", {"q": "b"}, ledger=ledger)
    assert over_cost.error_code is ErrorCode.BUDGET_EXHAUSTED
    assert ex.execute(local_ctx(), "lit_echo", {"q": "c"}, ledger=ledger).status is ToolStatus.OK
    over_calls = ex.execute(local_ctx(), "lit_echo", {"q": "d"}, ledger=ledger)
    assert over_calls.error_code is ErrorCode.BUDGET_EXHAUSTED
    assert len(rec.calls) == 2 and ledger.calls_used == 2 and ledger.cost_used == 20


def test_deadline_passed():
    rec = Recorder()
    ex = make([(read_spec(), rec.ok)])
    result = ex.execute(local_ctx(deadline=NOW - timedelta(seconds=1)), "lit_echo", {"q": "x"})
    assert result.error_code is ErrorCode.BUDGET_EXHAUSTED and rec.calls == []


def test_async_deadline_during_call():
    clock = FakeClock()

    async def slow(inv):
        clock.now = NOW + timedelta(seconds=5)
        await asyncio.sleep(1)
        return {"late": True}

    ex = make([(read_spec(), slow)], clock=clock)
    ctx = local_ctx(deadline=NOW + timedelta(seconds=0.05))
    result = asyncio.run(ex.aexecute(ctx, "lit_echo", {"q": "x"}))
    assert result.error_code is ErrorCode.BUDGET_EXHAUSTED


# -- retries and error typing --------------------------------------------------------------


def test_transient_failure_retried_then_succeeds():
    attempts = []

    def flaky(inv):
        attempts.append(inv.attempt)
        if inv.attempt < 3:
            raise ConnectionError("provider reset")
        return {"ok": True}

    ex = make([(read_spec(), flaky)], limits=RuntimeLimits(max_retries=2, retry_backoff_s=0.1))
    outcome = ex.execute_call(local_ctx(), "lit_echo", {"q": "x"})
    assert outcome.result.status is ToolStatus.OK and attempts == [1, 2, 3] and outcome.attempts == 3
    assert ex.sleeps == [0.1, 0.2]


def test_unavailable_provider_is_never_empty_or_ok():
    def down(inv):
        raise ContractError(ErrorCode.UNAVAILABLE, "CBORG unavailable")

    ex = make([(read_spec(), down)], limits=RuntimeLimits(max_retries=1, retry_backoff_s=0))
    result = ex.execute(local_ctx(), "lit_echo", {"q": "x"})
    assert result.status is ToolStatus.UNAVAILABLE and result.error_code is ErrorCode.UNAVAILABLE
    assert result.retryable and result.data is None


@pytest.mark.parametrize("exc, status, code", [
    (ContractError(ErrorCode.INVALID_INPUT, "bad doi"), ToolStatus.INVALID, ErrorCode.INVALID_INPUT),
    (ValueError("deterministic bug"), ToolStatus.ERROR, ErrorCode.INTERNAL),
    (ContractError(ErrorCode.NOT_FOUND, "no paper"), ToolStatus.ERROR, ErrorCode.NOT_FOUND),
])
def test_deterministic_failures_not_retried(exc, status, code):
    attempts = []

    def broken(inv):
        attempts.append(inv.attempt)
        raise exc

    ex = make([(read_spec(), broken)])
    result = ex.execute(local_ctx(), "lit_echo", {"q": "x"})
    assert (result.status, result.error_code) == (status, code) and attempts == [1]


def test_non_idempotent_tools_not_retried():
    attempts = []

    def flaky_write(inv):
        attempts.append(inv.attempt)
        raise ConnectionError("reset after send")

    ex = make([(write_spec(), flaky_write)])
    result = ex.execute(local_ctx(), "kg_apply_plan", {"plan_id": "p1"}, plan=make_plan(),
                        approval=local_approval())
    assert result.status is ToolStatus.UNAVAILABLE and attempts == [1]


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_process_control_exceptions_propagate(exc_type):
    def stop(inv):
        raise exc_type()

    with pytest.raises(exc_type):
        make([(read_spec(), stop)]).execute(local_ctx(), "lit_echo", {"q": "x"})


def test_langgraph_interrupt_and_cancel_code_propagate():
    def pause(inv):
        raise GraphInterrupt(())

    def cancel(inv):
        raise ContractError(ErrorCode.CANCELLED, "operator cancelled")

    ex = make([(read_spec(), pause), (read_spec("lit_cancel"), cancel)])
    with pytest.raises(GraphInterrupt):
        ex.execute(local_ctx(), "lit_echo", {"q": "x"})
    with pytest.raises(ContractError):
        ex.execute(local_ctx(), "lit_cancel", {"q": "x"})


def test_async_cancellation_propagates():
    done = []

    async def long_tool(inv):
        await asyncio.sleep(10)
        done.append(True)
        return {"never": True}

    ex = make([(read_spec(), long_tool)])

    async def main():
        task = asyncio.create_task(ex.aexecute(local_ctx(), "lit_echo", {"q": "x"}))
        await asyncio.sleep(0.01)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main())
    assert done == []


# -- output handling ------------------------------------------------------------------


def test_large_output_bounded_to_artifact_ref():
    store = InMemoryDocumentStore()

    def big(inv):
        return {"text": "x" * 5000}

    ex = make([(read_spec(), big)], store=store, limits=RuntimeLimits(max_output_chars=1000))
    result = ex.execute(local_ctx(), "lit_echo", {"q": "x"})
    assert result.status is ToolStatus.PARTIAL and result.data["truncated"] is True
    assert len(json.dumps(result.data)) < 1000 and len(result.artifact_refs) == 1
    stored = json.loads(store.get(local_ctx(), result.artifact_refs[0]))
    assert stored == {"text": "x" * 5000}


def test_output_schema_violation_is_internal_error_and_empty_mapping_is_empty():
    strict = read_spec(output_schema={"type": "object", "required": ["hits"]})
    ex = make([(strict, lambda inv: {"wrong": 1}), (read_spec("lit_empty"), lambda inv: {})])
    assert ex.execute(local_ctx(), "lit_echo", {"q": "x"}).error_code is ErrorCode.INTERNAL
    assert ex.execute(local_ctx(), "lit_empty", {"q": "x"}).status is ToolStatus.EMPTY


def test_tools_receive_admission_and_context():
    admission = CountingAdmission()
    seen = []

    def uses_model(inv):
        with inv.admission.slot("cborg"):
            seen.append((inv.ctx.request_id, inv.spec.name))
        return ToolResult(tool="whatever", status=ToolStatus.OK, data={"a": 1})

    ex = make([(read_spec(), uses_model)], admission=admission)
    result = ex.execute(local_ctx(), "lit_echo", {"q": "x"})
    assert result.tool == "lit_echo" and admission.sync == {"cborg": 1} and seen == [("req-local", "lit_echo")]

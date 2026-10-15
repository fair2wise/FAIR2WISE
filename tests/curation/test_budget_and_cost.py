"""T10 U-06: reservation, settlement of failed/partial work, unknown cost is never free (plan 11.3)."""

from __future__ import annotations

import pytest

from app.modules.curation.budgets import BudgetLedger
from app.modules.curation.cost_ledger import CallOutcome, parse_cost_header, settle_outcome
from app.modules.jobs.sqlite import OperationalDB
from tests.jobs.jobs_fakes import Clock


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def ledger(tmp_path, clock):
    return BudgetLedger(OperationalDB(tmp_path / "b.sqlite3"), clock=clock)


def reserve(ledger, amount=1.0, cap=3.0, **kw):
    return ledger.reserve(corpus="rsoxs", case_id="c", model="m", amount_usd=amount, cap_usd=cap, **kw)


def test_cap_is_enforced_by_reservations_before_any_call(ledger):
    assert [reserve(ledger) is not None for _ in range(4)] == [True, True, True, False]
    assert ledger.summary(cap_usd=3.0)["reserved_usd"] == 3.0


def test_scope_cap_applies_in_addition_to_global_cap(ledger):
    assert reserve(ledger, 1.0, cap=10, scope="a", scope_cap_usd=1.0)
    assert reserve(ledger, 1.0, cap=10, scope="a", scope_cap_usd=1.0) is None
    assert reserve(ledger, 1.0, cap=10, scope="b", scope_cap_usd=1.0)


def test_no_configured_cap_still_records_everything(ledger):
    for _ in range(5):
        assert reserve(ledger, 2.0, cap=None)
    assert ledger.summary()["charged_usd"] == 10.0 and ledger.summary()["cap_exceeded"] is False


def test_parse_cost_header_missing_or_garbage_is_none_never_zero():
    assert parse_cost_header(None) is None and parse_cost_header("") is None
    assert parse_cost_header("nan") is None and parse_cost_header("inf") is None
    assert parse_cost_header("-1") is None and parse_cost_header("abc") is None
    assert parse_cost_header("0.0123") == 0.0123 and parse_cost_header("0") == 0.0


def test_provider_bills_a_failed_response_cost_is_recorded(ledger):
    r = reserve(ledger, 1.0)
    state = settle_outcome(ledger, r, CallOutcome.from_response(500, "0.37"))
    assert state == "settled"
    assert ledger.summary()["known_usd"] == 0.37 and ledger.summary()["unknown_calls"] == 0


def test_omitted_cost_header_is_unknown_and_stays_charged(ledger):
    r = reserve(ledger, 1.0)
    assert settle_outcome(ledger, r, CallOutcome.from_response(200, None)) == "settled_unknown"
    s = ledger.summary()
    assert s["unknown_calls"] == 1 and s["unknown_charge_usd"] == 1.0 and s["charged_usd"] == 1.0
    assert s["known_usd"] == 0.0  # known and unknown are never merged
    # the unknown charge still counts against the cap
    assert reserve(ledger, 2.5, cap=3.0) is None


def test_server_error_without_cost_is_unknown_not_free(ledger):
    r = reserve(ledger)
    assert settle_outcome(ledger, r, CallOutcome.from_response(502, None)) == "settled_unknown"


def test_rate_limit_rejection_and_never_sent_are_released(ledger):
    r1, r2 = reserve(ledger), reserve(ledger)
    assert settle_outcome(ledger, r1, CallOutcome.from_response(429, None)) == "cancelled"
    assert settle_outcome(ledger, r2, CallOutcome.from_response(None, None, sent=False)) == "cancelled"
    assert ledger.summary()["charged_usd"] == 0.0


def test_429_that_reports_a_cost_is_still_charged(ledger):
    r = reserve(ledger)
    assert settle_outcome(ledger, r, CallOutcome.from_response(429, "0.01")) == "settled"


def test_settlement_happens_once(ledger):
    r = reserve(ledger)
    ledger.settle_known(r, 0.2)
    ledger.settle_known(r, 99.0)  # ignored: not 'reserved' any more
    ledger.cancel(r)
    assert ledger.summary()["known_usd"] == 0.2


def test_settle_known_rejects_non_numbers(ledger):
    r = reserve(ledger)
    for bad in (None, float("nan"), True, "1"):
        with pytest.raises(TypeError):
            ledger.settle_known(r, bad)


def test_overrun_is_visible_and_blocks_further_reservations(ledger):
    r = reserve(ledger, 1.0, cap=2.0)
    ledger.settle_known(r, 2.5)  # the provider really charged more than the estimate
    s = ledger.summary(cap_usd=2.0)
    assert s["overrun_usd"] == 1.5 and s["cap_exceeded"] is True
    assert reserve(ledger, 0.1, cap=2.0) is None


def test_dead_worker_reservations_are_reaped_into_unknown_not_freed(ledger, clock):
    r = reserve(ledger, 1.0)
    clock.advance(1000)
    assert ledger.reap_stale(600) == 1
    assert ledger.state_of(r) == "settled_unknown"
    assert ledger.summary()["charged_usd"] == 1.0

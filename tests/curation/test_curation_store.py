"""T10 U-01: case identity, tenant scoping, lease tokens, revision-bound resets."""

from __future__ import annotations

import pytest

from app.modules.curation import CaseState, CurationStore, LeaseNotOwned, ResolutionAttempt
from tests.curation.cur_fakes import case
from tests.jobs.jobs_fakes import Clock


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = CurationStore(tmp_path / "cur.sqlite3", clock=clock)
    yield s
    s.close()


def attempt(case_id, **kw):
    base = dict(
        case_id=case_id,
        provider="local",
        requested_model="m",
        executed_model="m",
        request_fingerprint="f",
        outcome="proposed",
        response={"x": 1},
        validation={"accepted": True, "errors": []},
        cost_usd=None,
    )
    base.update(kw)
    return ResolutionAttempt(**base)


def test_enqueue_is_idempotent_and_reruns_do_not_multiply(store):
    c = case()
    assert store.enqueue(c) == (c.id, True)
    assert store.enqueue(c) == (c.id, False)
    assert len(store.list_cases(["rsoxs"])) == 1


def test_changed_revision_resets_case_and_invalidates_decision(store):
    c = case()
    store.enqueue(c)
    (row,) = store.claim("w", ["rsoxs"])
    store.record_attempt(attempt(c.id), row["lease_token"], next_state=CaseState.NEEDS_REVIEW)
    changed = case(vocabulary_revision="v2")
    assert changed.id == c.id and changed.revision != c.revision
    assert store.enqueue(changed) == (c.id, True)
    got = store.get_case(c.id, ["rsoxs"])
    assert got["state"] == "pending" and got["proposal_attempt_id"] is None
    assert [e["event_type"] for e in store.events(c.id)][-1] == "revision_changed"


def test_tenant_scoping_on_every_read_and_claim(store):
    a, b = case(corpus="rsoxs"), case(corpus="other", publication_id="10.1/y")
    store.enqueue(a)
    store.enqueue(b)
    assert [r["id"] for r in store.claim("w", ["rsoxs"], limit=5)] == [a.id]
    assert store.claim("w", [], limit=5) == []
    assert store.list_cases(["other"])[0]["id"] == b.id
    with pytest.raises(Exception) as err:
        store.get_case(b.id, ["rsoxs"])
    assert "no such case" in str(err.value)


def test_stale_lease_token_cannot_record_or_release(store, clock):
    c = case()
    store.enqueue(c)
    (first,) = store.claim("w1", ["rsoxs"], lease_seconds=30)
    clock.advance(31)
    (second,) = store.claim("w2", ["rsoxs"])
    assert second["lease_token"] != first["lease_token"]
    with pytest.raises(LeaseNotOwned):
        store.record_attempt(attempt(c.id), first["lease_token"])
    with pytest.raises(LeaseNotOwned):
        store.release(c.id, first["lease_token"])
    store.record_attempt(attempt(c.id, cost_usd=None), second["lease_token"])
    rows = store.attempts(c.id)
    assert len(rows) == 1 and rows[0]["cost_usd"] is None and rows[0]["cost_known"] == 0  # unknown, not zero


def test_proposal_never_resolves_a_case(store):
    c = case()
    store.enqueue(c)
    (row,) = store.claim("w", ["rsoxs"])
    store.record_attempt(attempt(c.id, outcome="proposed"), row["lease_token"])
    got = store.get_case(c.id, ["rsoxs"])
    assert got["state"] == "needs_review" and got["proposal_attempt_id"] and got["resolution"] is None


def test_claim_orders_by_priority_and_respects_retry_after(store, clock):
    lo, hi = case(source_locator="a", priority=10), case(source_locator="b", priority=90)
    store.enqueue(lo)
    store.enqueue(hi)
    (first,) = store.claim("w", ["rsoxs"])
    assert first["id"] == hi.id
    store.release(hi.id, first["lease_token"], state=CaseState.RETRY_WAIT, retry_after=clock() + 100)
    (second,) = store.claim("w", ["rsoxs"])
    assert second["id"] == lo.id
    assert store.claim("w", ["rsoxs"]) == []
    clock.advance(101)
    assert store.claim("w", ["rsoxs"])[0]["id"] == hi.id


def test_artifact_registration_idempotent(store):
    kw = dict(corpus="rsoxs", publication_id="p", document_hash="d", kind="chunk", locator="l", content_hash="c")
    assert store.register_artifact(**kw) == store.register_artifact(**kw)

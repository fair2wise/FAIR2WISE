"""C3 (T14 Q8): the job list is scoped in SQL by the store, with the service's visibility rule.

``JobService.list_visible`` / ``JobStore.list_jobs_scoped`` must return exactly what the
previous ``F2WOperations.list_jobs`` returned (every job filtered through
``JobService._visible``, most recently updated first, then cut to ``limit``), for every
caller, state, kind and limit.
"""

from __future__ import annotations

import itertools

import pytest

from app.api.operations import F2WOperations
from app.modules.contracts.errors import ContractError
from app.modules.contracts.jobs import JobState
from app.modules.jobs.service import JobService
from app.modules.jobs.store import JobStore
from app.modules.security.authorization import DeploymentSettings
from tests.jobs.jobs_fakes import Clock, inputs, local_ctx
from tests.runtime.runtime_fakes import als_ctx

CREATORS = ("alice", "bob", "local-operator")
SCOPES = ((), ("rsoxs",), ("tomo",), ("rsoxs", "tomo"), ("o'brien; DROP TABLE jobs",))
KINDS = ("ingest", "publish")


def _als(subject, scope, *caps):
    return als_ctx(subject, capabilities=frozenset(caps), corpus_scope=frozenset(scope))


CONTEXTS = {
    "local-operator": local_ctx(),
    "local-no-admin": local_ctx("ingest.extract"),
    "als-admin": _als("admin", (), "jobs.admin"),
    "alice-rsoxs": _als("alice", ("rsoxs",)),
    "alice-both": _als("alice", ("rsoxs", "tomo")),
    "alice-none": _als("alice", ()),
    "alice-quote": _als("alice", ("o'brien; DROP TABLE jobs",)),
    "bob-tomo": _als("bob", ("tomo", "other")),
    "carol": _als("carol", ("rsoxs", "tomo")),
}


@pytest.fixture
def service(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "jobs.sqlite3", lease_seconds=60, clock=clock)
    n = 0
    for creator, scope, kind in itertools.product(CREATORS, SCOPES, KINDS):
        n += 1
        if n % 4:  # some equal updated_at values, so the job_id tie-break is exercised too
            clock.advance(1)
        job = store.submit(kind, inputs(corpus_scope=scope, source_sha256=f"{n:064x}"), created_by=creator)
        if n % 3 == 0:
            clock.advance(1)
            store.transition_without_lease(job.job_id, JobState.CANCELLED, reason="test", actor="t")
    yield JobService(store)
    store.close()


def _reference(service, ctx, *, state=None, kind=None, limit=100):
    """The pre-C3 algorithm of F2WOperations.list_jobs, verbatim."""
    def visible(job):
        try:
            service._visible(ctx, job)  # noqa: SLF001
        except ContractError:
            return False
        return True
    rows = service.store.list_jobs(state=state, kind=kind, limit=1000)
    return [job.job_id for job in rows if visible(job)][:limit]


@pytest.mark.parametrize("who", sorted(CONTEXTS))
def test_scoped_query_matches_the_service_visibility_rule(service, who):
    ctx = CONTEXTS[who]
    for state, kind, limit in itertools.product((None, JobState.QUEUED, JobState.CANCELLED), (None, *KINDS),
                                                (1, 3, 7, 100, 1000)):
        got = [j.job_id for j in service.list_visible(ctx, state=state, kind=kind, limit=limit)]
        assert got == _reference(service, ctx, state=state, kind=kind, limit=limit), (who, state, kind, limit)


@pytest.mark.parametrize("who", sorted(CONTEXTS))
def test_every_listed_job_passes_get_and_every_hidden_one_is_not_found(service, who):
    ctx = CONTEXTS[who]
    listed = {j.job_id for j in service.list_visible(ctx, limit=1000)}
    for job in service.store.list_jobs(limit=1000):
        try:
            service.get(ctx, job.job_id)
            seen = True
        except ContractError:
            seen = False
        assert seen == (job.job_id in listed), (who, job.created_by, job.inputs.corpus_scope)


def test_expected_visibility_spot_checks(service):
    def scopes(who):
        return sorted({(j.created_by, j.inputs.corpus_scope) for j in service.list_visible(CONTEXTS[who],
                                                                                            limit=1000)})
    total = len(CREATORS) * len(SCOPES)
    assert len(scopes("als-admin")) == len(scopes("local-operator")) == total
    assert scopes("alice-rsoxs") == [("alice", ()), ("alice", ("rsoxs",))]
    assert scopes("alice-none") == [("alice", ())]
    assert scopes("alice-quote") == [("alice", ()), ("alice", ("o'brien; DROP TABLE jobs",))]
    assert scopes("bob-tomo") == [("bob", ()), ("bob", ("tomo",))]
    assert scopes("carol") == []
    # local_trusted without jobs.admin: own jobs, any scope.
    assert len(scopes("local-no-admin")) == len(SCOPES)
    assert len(service.store.list_jobs(limit=1000)) == total * len(KINDS)  # the quote did no harm


def test_operations_list_jobs_uses_the_scoped_query(service):
    settings = DeploymentSettings(profile="local", writer_identity="f2w-writer", tiled_target="http://127.0.0.1:1")
    ops = F2WOperations(settings, service)
    for who, ctx in CONTEXTS.items():
        for state, limit in ((None, 100), ("cancelled", 2), ("queued", 1000)):
            got = [h.job_id for h in ops.list_jobs(ctx, state=state, limit=limit)]
            assert got == _reference(service, ctx, state=JobState(state) if state else None, limit=limit), who
    calls = []
    original = service.store.list_jobs_scoped
    service.store.list_jobs_scoped = lambda **kw: calls.append(kw) or original(**kw)
    ops.list_jobs(CONTEXTS["alice-rsoxs"], limit=5)
    assert calls == [{"created_by": "alice", "within_scope": frozenset({"rsoxs"}), "state": None, "kind": None,
                      "limit": 5}]


def test_a_visible_job_behind_a_thousand_hidden_ones_is_still_listed(tmp_path):
    """The one intended difference: the old code cut to the 1000 newest jobs *before* filtering, so a
    caller's job behind 1000 newer jobs of others was silently missing. The SQL filter runs first."""
    clock = Clock()
    store = JobStore(tmp_path / "many.sqlite3", clock=clock)
    service = JobService(store)
    mine = store.submit("ingest", inputs(), created_by="alice").job_id
    for n in range(1000):
        clock.advance(1)
        store.submit("ingest", inputs(source_sha256=f"{n + 1:064x}"), created_by="bob")
    alice = _als("alice", ("rsoxs",))
    assert [j.job_id for j in service.list_visible(alice)] == [mine]
    assert _reference(service, alice) == []  # what the pre-C3 code returned
    store.close()

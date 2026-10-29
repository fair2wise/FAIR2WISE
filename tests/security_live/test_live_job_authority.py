"""AUD-01 against a disposable Tiled: the default ``JobAuthority`` stops a job mid-run (T28A).

The initiating admin signs in through the real ``TiledSessionStore``; the worker looks the
credential up by subject (``credential_for_subject``) and re-verifies it with the real
``TiledWhoamiAdapter`` before every guarded stage. Between the two stages the admin role is
removed (or the session is signed out); the second stage's effect never happens.
"""
from __future__ import annotations

import httpx
import pytest

from live_support import drop_role, loopback_only, utcnow

from app.api.sessions import als_identity_wiring
from app.modules.contracts.errors import ErrorCode
from app.modules.contracts.jobs import JobState, StageOutcome
from app.modules.jobs import JobService, JobStore, Pipeline, StageResult, StageSpec
from app.modules.security.authorization import DeploymentSettings, resolve_context
from tests.jobs.jobs_fakes import Clock, inputs
from tiled_server import tiled_python

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")

CAP = "graph.publish"


def _world(monkeypatch, tmp_path, inst):
    loopback_only(monkeypatch)
    settings = DeploymentSettings.for_disposable_test_target("als-dev", tiled_target=inst.url,
                                                             writer_identity=inst.writer.uuid,
                                                             corpus_scope=frozenset({"rsoxs"}))
    http = httpx.Client(follow_redirects=False)
    port, store = als_identity_wiring(settings, http, cookie_path="/")
    token = store.sign_in(inst.provider, "admin", inst.passwords["admin"])
    ctx = resolve_context(settings, store.get(token), request_id="r-admin", identity_port=port, clock=utcnow,
                          require_fresh=True)
    store.bind_subject(token, ctx.principal.subject)  # what app.api.auth does on every resolved request
    service = JobService(JobStore(tmp_path / "jobs.sqlite3", lease_seconds=60, clock=Clock()), settings=settings,
                         identity_port=port, credential_lookup=store.credential_for_subject, clock=utcnow)
    return settings, port, store, token, ctx, service


def _pipeline(effects, between):
    def one(sc):
        effects.append("one")
        between()
        return StageResult()

    def two(sc):
        effects.append("two")
        return StageResult()

    return Pipeline([
        StageSpec("one", one, phase="parsing", required_capability=CAP),
        StageSpec("two", two, phase="import", depends_on=("one",), required_capability=CAP),
    ])


@pytest.mark.parametrize("change", ["downgrade", "sign_out"])
def test_real_downgrade_or_sign_out_between_stages_stops_the_job(monkeypatch, tmp_path, live_fresh, change):
    settings, port, store, token, ctx, service = _world(monkeypatch, tmp_path, live_fresh)
    effects = []

    def between():
        if change == "downgrade":
            assert drop_role(live_fresh, "admin") == 1
        else:
            assert store.sign_out(token)

    service.register("publish", _pipeline(effects, between), capability=CAP)
    h = service.submit(ctx, "publish", inputs())
    assert service.work_one() == (h.job_id, JobState.FAILED)
    assert effects == ["one"]
    two = service.store.stage(h.job_id, "two")
    assert two.error_code is ErrorCode.DENIED and two.status is not StageOutcome.COMPLETED
    assert service.store.stage(h.job_id, "one").status is StageOutcome.COMPLETED


def test_real_admin_keeps_authority_across_stages(monkeypatch, tmp_path, live_fresh):
    settings, port, store, token, ctx, service = _world(monkeypatch, tmp_path, live_fresh)
    effects = []
    service.register("publish", _pipeline(effects, lambda: None), capability=CAP)
    h = service.submit(ctx, "publish", inputs())
    assert service.work_one() == (h.job_id, JobState.SUCCEEDED)
    assert effects == ["one", "two"]

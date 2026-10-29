"""AUD-01 (T28A): job stages re-authorize the initiating subject by default; nothing is a silent no-op.

* ``JobService.register`` wires the service's ``JobAuthority`` into every kind; a kind whose pipeline
  declares a ``required_capability`` is refused at registration when no authority exists (fail closed).
* ``StageContext.require`` without an authority denies the stage instead of returning ``None``.
* On ALS a role downgrade or a sign-out between two stages stops the job before the second stage's
  effect (fake identity port and fake session lookup; the live-Tiled variant is in
  ``tests/security_live/test_live_job_authority.py``).
* ``:local`` keeps working with the local-operator authority and no identity call.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "tests" / "security") not in sys.path:
    sys.path.insert(0, str(REPO / "tests" / "security"))

from security_fakes import (  # noqa: E402
    FakeClock,
    FakeTiledIdentityPort,
    ForbiddenIdentityPort,
    FakeUser,
    STAGING,
    admin_user,
    local_settings,
    staging_settings,
)

from app.modules.contracts.context import ActorKind  # noqa: E402
from app.modules.contracts.errors import ContractError, ErrorCode  # noqa: E402
from app.modules.contracts.jobs import JobState, StageOutcome  # noqa: E402
from app.modules.jobs import JobService, JobStore, Pipeline, StageResult, StageSpec  # noqa: E402
from app.modules.security.authorization import (  # noqa: E402
    JobAuthority,
    StartupConfigError,
    local_operator_context,
    resolve_context,
)
from app.modules.security.tiled_identity import TiledCredential  # noqa: E402
from tests.jobs.jobs_fakes import Clock, inputs, local_ctx  # noqa: E402

CAP = "graph.publish"  # admin-only on ALS


def _store(tmp_path):
    return JobStore(tmp_path / "jobs.sqlite3", lease_seconds=60, clock=Clock())


def _two_stage(effects, between=None):
    """Two guarded stages; ``between`` runs at the end of the first (after its effect)."""

    def one(sc):
        effects.append("one")
        if between is not None:
            between()
        return StageResult()

    def two(sc):
        effects.append("two")
        return StageResult()

    return Pipeline([
        StageSpec("one", one, phase="parsing", required_capability=CAP),
        StageSpec("two", two, phase="import", depends_on=("one",), required_capability=CAP),
    ])


# ------------------------------------------------------------------ registration fails closed


def test_registering_a_guarded_kind_without_any_authority_is_refused(tmp_path):
    service = JobService(_store(tmp_path))  # no deployment settings: no default authority
    with pytest.raises(StartupConfigError, match="required_capability.*no worker authority"):
        service.register("publish", _two_stage([]), capability=CAP)
    assert "publish" not in service._kinds  # noqa: SLF001


def test_registering_with_an_explicit_authority_still_works_without_settings(tmp_path):
    service = JobService(_store(tmp_path))
    seen = []

    def authority(job, caps):
        seen.append(tuple(caps))
        return local_ctx()

    service.register("publish", _two_stage([]), capability=CAP, authority=authority)
    h = service.submit(local_ctx(), "publish", inputs())
    assert service.work_one() == (h.job_id, JobState.SUCCEEDED)
    assert seen == [(CAP,), (CAP,)]


def test_require_without_authority_denies_the_stage_instead_of_returning_none(tmp_path):
    service = JobService(_store(tmp_path))
    effects = []

    def handler(sc):
        sc.require("ingest.acquire")  # legacy handlers call require themselves (stages.acquire_stage)
        effects.append("effect")
        return StageResult()

    service.register("ingest", Pipeline([StageSpec("acquire", handler, phase="parsing")]))
    h = service.submit(local_ctx(), "ingest", inputs())
    assert service.work_one() == (h.job_id, JobState.FAILED)
    assert effects == []
    assert service.store.stage(h.job_id, "acquire").error_code is ErrorCode.DENIED


def test_als_job_authority_needs_identity_port_and_credential_lookup():
    with pytest.raises(StartupConfigError, match="identity port"):
        JobAuthority(staging_settings(), credential_lookup=lambda s: None)
    with pytest.raises(StartupConfigError, match="credential lookup"):
        JobAuthority(staging_settings(), identity_port=FakeTiledIdentityPort())
    with pytest.raises(StartupConfigError):
        JobAuthority(staging_settings())
    JobAuthority(local_settings())  # :local needs neither


# ------------------------------------------------------------------ :local keeps working


def test_local_default_authority_runs_guarded_stages_without_identity_calls(tmp_path):
    service = JobService(_store(tmp_path), settings=local_settings(), identity_port=ForbiddenIdentityPort(),
                         clock=FakeClock())
    effects = []
    service.register("publish", _two_stage(effects), capability=CAP)
    ctx = local_operator_context(local_settings(), request_id="r1")
    h = service.submit(ctx, "publish", inputs())
    assert service.work_one() == (h.job_id, JobState.SUCCEEDED)
    assert effects == ["one", "two"]


# ------------------------------------------------------------------ ALS: downgrade and sign-out mid-job


class _AlsWorld:
    """An ALS deployment with a fake identity port and a fake server-side session index by subject."""

    def __init__(self, tmp_path):
        self.settings = staging_settings(corpus_scope=frozenset({"rsoxs"}))
        self.clock = FakeClock()
        self.port = FakeTiledIdentityPort(tiled_target=STAGING)
        self.admin = admin_user()
        self.port.tokens["token-admin"] = self.admin
        self.credentials = {self.admin.subject: TiledCredential("token-admin", tiled_target=STAGING)}
        self.service = JobService(_store(tmp_path), settings=self.settings, identity_port=self.port,
                                  credential_lookup=lambda subject: self.credentials.get(subject),
                                  clock=self.clock)
        self.changes = []
        self.service.on_scope_change(self.changes.append)
        self.effects = []

    def ctx(self):
        return resolve_context(self.settings, self.credentials[self.admin.subject], request_id="r-admin",
                               identity_port=self.port, clock=self.clock, require_fresh=True)

    def downgrade(self):
        self.port.tokens["token-admin"] = FakeUser(self.admin.subject, roles=("user",))

    def sign_out(self):
        self.credentials.pop(self.admin.subject)

    def run(self, between):
        self.service.register("publish", _two_stage(self.effects, between), capability=CAP)
        h = self.service.submit(self.ctx(), "publish", inputs())
        return h.job_id, self.service.work_one()


@pytest.mark.parametrize("change", ["downgrade", "sign_out"])
def test_als_downgrade_or_sign_out_between_stages_stops_the_job_before_its_next_effect(tmp_path, change):
    w = _AlsWorld(tmp_path)
    job_id, outcome = w.run(getattr(w, change))
    assert outcome == (job_id, JobState.FAILED)
    assert w.effects == ["one"]  # the second stage never ran
    assert w.service.store.stage(job_id, "one").status is StageOutcome.COMPLETED
    two = w.service.store.stage(job_id, "two")
    assert two is not None and two.error_code is ErrorCode.DENIED and two.status is not StageOutcome.COMPLETED
    assert w.changes and w.changes[0]["job_id"] == job_id and w.changes[0]["subject"] == w.admin.subject
    # Every guarded boundary re-verified the credential: at least one whoami per stage.
    assert len(w.port.calls) >= 2


def test_als_authority_re_verifies_at_every_stage_and_keeps_the_worker_actor_kind(tmp_path):
    w = _AlsWorld(tmp_path)
    seen = []

    def one(sc):
        seen.append(sc.require(CAP))
        return StageResult()

    w.service.register("publish", Pipeline([StageSpec("one", one, phase="parsing", required_capability=CAP)]),
                       capability=CAP)
    h = w.service.submit(w.ctx(), "publish", inputs())
    calls = len(w.port.calls)
    assert w.service.work_one() == (h.job_id, JobState.SUCCEEDED)
    assert len(w.port.calls) >= calls + 2  # required_capability check plus the handler's own require
    (ctx,) = seen
    assert ctx.actor_kind is ActorKind.WORKER and ctx.run_id == h.job_id
    assert ctx.principal.subject == w.admin.subject and ctx.has(CAP)


def test_als_job_submitted_by_a_subject_without_a_session_never_starts_an_effect(tmp_path):
    w = _AlsWorld(tmp_path)
    ctx = w.ctx()
    w.sign_out()  # signed out between submit and the worker claiming the job
    w.service.register("publish", _two_stage(w.effects), capability=CAP)
    h = w.service.submit(ctx, "publish", inputs())
    assert w.service.work_one() == (h.job_id, JobState.FAILED)
    assert w.effects == []
    with pytest.raises(ContractError) as err:
        w.service.authority.for_job(w.service.store.get(h.job_id))(w.service.store.get(h.job_id), (CAP,))
    assert err.value.code is ErrorCode.DENIED and "signed_out" in str(err.value)


def test_a_stopped_run_stays_stopped_even_if_the_role_comes_back(tmp_path):
    w = _AlsWorld(tmp_path)
    job = type("J", (), {"job_id": "j1", "created_by": w.admin.subject})()
    run = w.service.authority.for_job(job)
    assert run(job, (CAP,)).has(CAP)
    w.downgrade()
    with pytest.raises(ContractError):
        run(job, (CAP,))
    w.port.tokens["token-admin"] = w.admin  # role restored: this run never regains authority (AUTH-07)
    with pytest.raises(ContractError) as err:
        run(job, (CAP,))
    assert err.value.code is ErrorCode.DENIED and "worker stopped" in str(err.value)
    assert w.service.authority.for_job(job)(job, (CAP,)).has(CAP)  # a new run re-verifies afresh

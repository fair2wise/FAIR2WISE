"""T25B: worker revalidation and approval binding (access_contract.md section 11)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.modules.contracts.changes import ApprovalRecord
from app.modules.contracts.context import LOCAL_OPERATOR, ActorKind, DeploymentProfile
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.security.authorization import (
    LOCAL_DEFAULT_TILED_TARGET,
    Unauthenticated,
    WorkerAuthority,
    create_approval,
    resolve_context,
    verify_approval,
    worker_context,
)
from app.modules.security.tiled_identity import TiledCredential
from security_fakes import (
    PROD,
    STAGING,
    WRITER,
    FakeClock,
    FakeTiledIdentityPort,
    FakeUser,
    ForbiddenIdentityPort,
    admin_user,
    local_settings,
    make_plan,
    plain_user,
    prod_settings,
    staging_settings,
)

LOCAL_TARGET = LOCAL_DEFAULT_TILED_TARGET + "/f2w"
STAGING_TARGET = STAGING + "/f2w"
PROD_TARGET = PROD + "/f2w"


def _local_ctx(**kw):
    return resolve_context(local_settings(), None, request_id="r", clock=FakeClock(), **kw)


def _als_ctx(port, token, settings=None, clock=None, **kw):
    return resolve_context(settings or staging_settings(), token, request_id="r", identity_port=port,
                           clock=clock or FakeClock(), **kw)


# --- workers (LOCAL-04, AUTH-07) ---------------------------------------------------

def test_worker_context_records_actor_and_run():
    ctx = worker_context(_local_ctx(), run_id="run-1")
    assert ctx.actor_kind is ActorKind.WORKER and ctx.run_id == "run-1" and ctx.principal.subject == LOCAL_OPERATOR


def test_local_worker_needs_no_refresh_and_no_identity_call():
    """LOCAL-04 / LOCAL-05."""
    port = ForbiddenIdentityPort()
    authority = WorkerAuthority(_local_ctx(), local_settings(), identity_port=port, clock=FakeClock())
    for _ in range(3):
        assert authority.check("graph.publish", "ingest.extract").actor_kind is ActorKind.WORKER
    assert port.calls == 0


@pytest.mark.parametrize("change", ["downgrade", "expire", "revoke", "sign_out", "swap_subject"])
def test_als_worker_stops_on_downgrade_expiry_revocation(change):
    """AUTH-07: revalidated at every boundary; once stopped, no new effects."""
    clock = FakeClock()
    port = FakeTiledIdentityPort(tokens={"a": admin_user(), "u": plain_user()})
    store = {"cred": TiledCredential("a", tiled_target=STAGING)}
    ctx = worker_context(_als_ctx(port, "a", clock=clock), run_id="run-1")
    authority = WorkerAuthority(ctx, staging_settings(), identity_port=port, credential_provider=lambda: store["cred"],
                                clock=clock)
    effects = []
    authority.check("graph.publish")
    effects.append("write-1")
    calls_before = len(port.calls)
    if change == "downgrade":
        port.tokens["a"] = FakeUser("uuid-admin", roles=("user",), identity_ids=("alice",))
    elif change == "expire":
        store["cred"] = TiledCredential("a", tiled_target=STAGING, expires_at=clock.now + timedelta(seconds=1))
    elif change == "revoke":
        port.rejected["a"] = "revoked"
    elif change == "sign_out":
        store["cred"] = None
    else:
        store["cred"] = TiledCredential("u", tiled_target=STAGING)
    with pytest.raises(ContractError) as info:
        authority.check("graph.publish")
        effects.append("write-2")
    assert info.value.code is ErrorCode.DENIED and effects == ["write-1"]
    assert authority.stopped_reason
    port.tokens["a"], store["cred"] = admin_user(), TiledCredential("a", tiled_target=STAGING)
    port.rejected.clear()
    with pytest.raises(ContractError):
        authority.check("graph.publish")  # stays stopped
    if change == "downgrade":
        assert len(port.calls) > calls_before


def test_als_worker_outage_is_retryable_not_trusted():
    port = FakeTiledIdentityPort(tokens={"a": admin_user()})
    authority = WorkerAuthority(_als_ctx(port, "a"), staging_settings(), identity_port=port,
                                credential_provider=lambda: TiledCredential("a"), clock=FakeClock())
    port.outage = True
    with pytest.raises(ContractError) as info:
        authority.check("graph.publish")
    assert info.value.code is ErrorCode.UNAVAILABLE and authority.stopped_reason is None


def test_worker_context_cannot_move_between_deployments():
    """LOCAL-10 / AUTH-08: a recorded local or staging grant does not run on prod."""
    port = FakeTiledIdentityPort(tokens={"a": admin_user()})
    for ctx in (_local_ctx(), _als_ctx(port, "a")):
        with pytest.raises(ContractError):
            WorkerAuthority(ctx, prod_settings(), identity_port=port, clock=FakeClock())


# --- approvals (LOCAL-03, LOCAL-10, AUTH-05, AUTH-08) -------------------------------

def test_local_approval_is_local_operator_with_writer_and_no_identity():
    """LOCAL-03."""
    clock = FakeClock()
    plan = make_plan(LOCAL_TARGET)
    rec = create_approval(_local_ctx(), plan, local_settings(), approval_id="ap-1", clock=clock,
                          identity_port=ForbiddenIdentityPort())
    assert rec.requested_by == rec.approved_by == LOCAL_OPERATOR and rec.executed_as == WRITER
    assert rec.approver_issuer is None and rec.role_checked_at is None and rec.approver_is_admin is None
    assert rec.role_check_evidence is None and rec.requester_issuer is None
    assert verify_approval(rec, plan, local_settings(), at=clock.now) is rec


def test_local_approval_never_authorizes_an_als_target():
    """LOCAL-10."""
    clock = FakeClock()
    with pytest.raises(ContractError):
        create_approval(_local_ctx(), make_plan(PROD_TARGET), local_settings(), approval_id="ap", clock=clock)
    rec = create_approval(_local_ctx(), make_plan(LOCAL_TARGET), local_settings(), approval_id="ap", clock=clock)
    for settings, target in ((prod_settings(), PROD_TARGET), (staging_settings(), STAGING_TARGET)):
        forged = rec.model_copy(update={"target": target})
        with pytest.raises(ContractError) as info:
            verify_approval(forged, make_plan(target), settings, at=clock.now)
        assert info.value.code is ErrorCode.DENIED


def test_als_admin_approval_has_fresh_role_check_and_separate_writer():
    """AUTH-05."""
    clock = FakeClock()
    port = FakeTiledIdentityPort(tokens={"a": admin_user(), "u": plain_user()})
    requester = _als_ctx(port, "u", clock=clock).principal
    approver_ctx = _als_ctx(port, "a", clock=clock)
    clock.advance(30)
    calls = len(port.calls)
    plan = make_plan(STAGING_TARGET)
    rec = create_approval(approver_ctx, plan, staging_settings(), approval_id="ap-2", clock=clock,
                          identity_port=port, approver_credential=TiledCredential("a"), requester=requester,
                          operation_id="op-7")
    assert len(port.calls) == calls + 1 and rec.role_checked_at == clock.now
    assert rec.requested_by == "uuid-user" and rec.approved_by == "uuid-admin" and rec.executed_as == WRITER
    assert rec.approver_issuer == rec.requester_issuer == STAGING and rec.approver_is_admin is True
    assert rec.role_check_evidence.method == "tiled_whoami" and rec.role_check_evidence.roles == ("admin",)
    assert ApprovalRecord.model_validate(rec.model_dump()) == rec
    assert verify_approval(rec, plan, staging_settings(), at=clock.now) is rec


def test_als_approval_refused_for_non_admin_downgrade_worker_or_wrong_target():
    """AUTH-04 / AUTH-07 / AUTH-08."""
    clock = FakeClock()
    port = FakeTiledIdentityPort(tokens={"a": admin_user(), "u": plain_user()})
    settings, plan = staging_settings(), make_plan(STAGING_TARGET)
    kw = dict(approval_id="ap", clock=clock, identity_port=port)
    with pytest.raises(ContractError):
        create_approval(_als_ctx(port, "u"), plan, settings, approver_credential=TiledCredential("u"), **kw)
    admin_ctx = _als_ctx(port, "a")
    with pytest.raises(ContractError):  # wrong target
        create_approval(admin_ctx, make_plan(PROD_TARGET), settings, approver_credential=TiledCredential("a"), **kw)
    with pytest.raises(ContractError):  # a worker never approves
        create_approval(worker_context(admin_ctx, run_id="r"), plan, settings,
                        approver_credential=TiledCredential("a"), **kw)
    port.tokens["a"] = FakeUser("uuid-admin", roles=("user",), identity_ids=("alice",))
    with pytest.raises(ContractError):  # downgraded since the cached context
        create_approval(admin_ctx, plan, settings, approver_credential=TiledCredential("a"), **kw)
    port.rejected["a"] = "revoked"
    with pytest.raises(Unauthenticated):
        create_approval(admin_ctx, plan, settings, approver_credential=TiledCredential("a"), **kw)


def test_staging_approval_does_not_authorize_prod():
    """AUTH-08."""
    clock = FakeClock()
    port = FakeTiledIdentityPort(tokens={"a": admin_user()})
    rec = create_approval(_als_ctx(port, "a"), make_plan(STAGING_TARGET), staging_settings(), approval_id="ap",
                          clock=clock, identity_port=port, approver_credential=TiledCredential("a"))
    forged = rec.model_copy(update={"profile": DeploymentProfile.ALS_PROD, "target": PROD_TARGET})
    with pytest.raises(ContractError) as info:
        verify_approval(forged, make_plan(PROD_TARGET), prod_settings(), at=clock.now)
    assert info.value.code is ErrorCode.DENIED
    with pytest.raises(ContractError) as info:
        verify_approval(rec, make_plan(STAGING_TARGET, plan_id="other"), staging_settings(), at=clock.now)
    assert info.value.code is ErrorCode.STALE_APPROVAL

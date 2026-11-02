"""T25C item 1: the real whoami adapter and resolver against disposable Tiled 0.2.18 servers.

Binary cases: AUTH-02/AUTH-04 (reader is chat-only, admin gets every capability), AUTH-06
(service principals and the writer never establish a user), AUTH-07 (expiry, revocation,
role downgrade, outage), AUTH-08 (a second server's credential is refused), LOCAL-07
(an outage never enables local trust).
"""
from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import requests

from live_support import (
    CountingPort, access_token, bearer, drop_role, mint_expired_token, principal_uuid, revoke_user_api_key,
    user_api_key, utcnow,
)

from app.modules.contracts.context import (
    ADMIN_CAPABILITIES,
    CAPABILITIES,
    CHAT_CAPABILITIES,
    LOCAL_ONLY_CAPABILITIES,
    ActorKind,
)
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.security.authorization import (
    DeploymentSettings,
    RoleCheckCache,
    Unauthenticated,
    WorkerAuthority,
    create_approval,
    resolve_context,
    verify_identity,
)
from app.modules.security.tiled_identity import (
    IdentityRejected,
    IdentityUnavailable,
    TiledCredential,
    TiledWhoamiAdapter,
)
from security_fakes import make_plan
from tiled_server import tiled_python

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")


@pytest.fixture
def http():
    with httpx.Client(follow_redirects=False) as client:
        yield client


def _settings(inst, writer="f2w-writer", profile="als-dev"):
    writer = getattr(getattr(inst, "writer", None), "uuid", writer)
    return DeploymentSettings.for_disposable_test_target(profile, tiled_target=inst.url, writer_identity=writer)


def _ctx(settings, port, credential, **kw):
    return resolve_context(settings, credential, request_id="r-live", identity_port=port, clock=utcnow, **kw)


def _cred(inst, token, **kw):
    return TiledCredential(token, tiled_target=inst.url, **kw)


# ------------------------------------------------------------------ roles


def test_admin_gets_every_capability_and_reader_is_chat_only(live_auth, http):
    settings = _settings(live_auth)
    port = TiledWhoamiAdapter(live_auth.url, http)
    admin = _ctx(settings, port, _cred(live_auth, access_token(live_auth, "admin")))
    reader = _ctx(settings, port, _cred(live_auth, access_token(live_auth, "reader")))
    assert admin.capabilities == CAPABILITIES - LOCAL_ONLY_CAPABILITIES and admin.principal.is_admin is True
    assert reader.capabilities == CHAT_CAPABILITIES and reader.principal.is_admin is False
    assert not reader.capabilities & ADMIN_CAPABILITIES
    # The subject is the stable Tiled principal uuid; the issuer is the server that answered.
    assert admin.principal.subject == principal_uuid(live_auth, "admin")
    assert admin.principal.issuer == settings.tiled_target == live_auth.url
    assert admin.principal.identity_provider == live_auth.provider
    assert set(admin.principal.roles) == {"user", "admin"} and reader.principal.roles == ("user",)


def test_identity_carries_the_server_version_as_evidence(live_auth, http):
    identity = TiledWhoamiAdapter(live_auth.url, http).whoami(
        _cred(live_auth, access_token(live_auth, "admin")), now=utcnow())
    assert identity.tiled_library_version == "0.2.18"
    assert identity.identity_ids == ("admin",) and identity.principal_type == "user"


def test_subject_is_stable_across_sign_ins(live_auth, http):
    port = TiledWhoamiAdapter(live_auth.url, http)
    first = port.whoami(_cred(live_auth, access_token(live_auth, "reader")), now=utcnow())
    second = port.whoami(_cred(live_auth, access_token(live_auth, "reader")), now=utcnow())
    assert first.subject == second.subject  # D-T3 for the password provider


def test_reader_api_key_resolves_to_the_same_user(live_auth, http):
    """D-T8: per-user API keys exist on 0.2.18 and resolve to the user principal."""
    secret, token = user_api_key(live_auth, "reader")
    port = TiledWhoamiAdapter(live_auth.url, http)
    via_key = port.whoami(_cred(live_auth, secret, scheme="Apikey"), now=utcnow())
    via_token = port.whoami(_cred(live_auth, token), now=utcnow())
    assert via_key.subject == via_token.subject and not via_key.is_admin
    revoke_user_api_key(live_auth, secret, token)
    with pytest.raises(IdentityRejected) as exc:
        port.whoami(_cred(live_auth, secret, scheme="Apikey"), now=utcnow())
    assert exc.value.reason == "invalid"  # 0.2.18: "Invalid API key" for revoked and unknown alike


# ------------------------------------------------------------------ rejected credentials


@pytest.mark.parametrize("token", ["garbage", "a.b.c", ""], ids=["garbage", "malformed-jwt", "empty"])
def test_invalid_credentials_are_unauthenticated(live_auth, http, token):
    with pytest.raises(Unauthenticated):
        _ctx(_settings(live_auth), TiledWhoamiAdapter(live_auth.url, http), _cred(live_auth, token))


def test_no_credential_is_null_at_tiled_and_unauthenticated_here(live_auth, http):
    assert requests.get(f"{live_auth.url}/api/v1/auth/whoami", timeout=10).text == "null"
    with pytest.raises(Unauthenticated) as exc:
        _ctx(_settings(live_auth), TiledWhoamiAdapter(live_auth.url, http), None)
    assert exc.value.reason == "missing"


def test_expired_access_token_is_refused_by_the_server(live_auth, http):
    expired = mint_expired_token(live_auth, access_token(live_auth, "admin"))
    resp = requests.get(f"{live_auth.url}/api/v1/auth/whoami", headers=bearer(expired), timeout=10)
    assert resp.status_code == 401 and "expired" in resp.json()["detail"]
    with pytest.raises(Unauthenticated) as exc:
        _ctx(_settings(live_auth), TiledWhoamiAdapter(live_auth.url, http), _cred(live_auth, expired))
    assert exc.value.reason == "expired"


def test_known_expiry_is_refused_without_asking_the_server(live_auth, http):
    port = CountingPort(TiledWhoamiAdapter(live_auth.url, http))
    cred = _cred(live_auth, access_token(live_auth, "admin"), expires_at=utcnow() + timedelta(seconds=5))
    with pytest.raises(Unauthenticated) as exc:
        _ctx(_settings(live_auth), port, cred)  # within the clock-skew tolerance: treated as expired
    assert exc.value.reason == "expired" and port.calls == []


def test_revoked_session_cannot_be_refreshed_and_its_token_lives_until_expiry(live_auth, http):
    """D-T6 gap on 0.2.18: revoking a session blocks refresh, not the outstanding access token."""
    tokens = live_auth.login("reader")
    assert requests.post(f"{live_auth.url}/api/v1/auth/session/revoke",
                         json={"refresh_token": tokens["refresh_token"]}, timeout=10).status_code == 204
    refreshed = requests.post(f"{live_auth.url}/api/v1/auth/session/refresh",
                              json={"refresh_token": tokens["refresh_token"]}, timeout=10)
    assert refreshed.status_code == 401
    # Observed: the access token still passes whoami (JWT decoded without a session lookup).
    identity = TiledWhoamiAdapter(live_auth.url, http).whoami(_cred(live_auth, tokens["access_token"]),
                                                              now=utcnow())
    assert identity.roles == ("user",)


# ------------------------------------------------------------------ issuer binding (AUTH-08)


def test_credential_from_a_second_server_is_refused(live_auth, live_other, http):
    other_token = access_token(live_other, "admin")
    settings = _settings(live_auth)
    port = CountingPort(TiledWhoamiAdapter(live_auth.url, http))
    # Bound to the other server: refused before any call.
    with pytest.raises(Unauthenticated) as exc:
        _ctx(settings, port, TiledCredential(other_token, tiled_target=live_other.url))
    assert exc.value.reason == "wrong_issuer" and port.calls == []
    # Unbound: the profile server itself rejects the other server's signature.
    with pytest.raises(Unauthenticated) as exc:
        _ctx(settings, port, TiledCredential(other_token))
    assert exc.value.reason == "invalid" and len(port.calls) == 1
    # A port bound to the other server is never consulted for this deployment.
    with pytest.raises(ContractError) as err:
        verify_identity(settings, TiledCredential(other_token),
                        identity_port=TiledWhoamiAdapter(live_other.url, http), clock=utcnow)
    assert err.value.code is ErrorCode.UNAVAILABLE


def test_an_approval_verified_by_one_server_does_not_authorize_another(live_auth, live_other, http):
    staging = _settings(live_auth)
    prod = DeploymentSettings.for_disposable_test_target("als-prod", tiled_target=live_other.url,
                                                         writer_identity=staging.writer_identity)
    port = TiledWhoamiAdapter(live_auth.url, http)
    cred = _cred(live_auth, access_token(live_auth, "admin"))
    ctx = _ctx(staging, port, cred)
    plan = make_plan(live_auth.url)
    approval = create_approval(ctx, plan, staging, approval_id="a-live", clock=utcnow, identity_port=port,
                               approver_credential=cred)
    assert approval.approver_issuer == live_auth.url and approval.role_check_evidence.tiled_library_version
    from app.modules.security.authorization import verify_approval

    assert verify_approval(approval, plan, staging, at=utcnow()) is approval
    with pytest.raises(ContractError) as err:
        verify_approval(approval, make_plan(live_other.url), prod, at=utcnow())
    assert err.value.code is ErrorCode.DENIED


# ------------------------------------------------------------------ service principals (AUTH-06)


def test_service_principal_and_writer_never_establish_a_user(live_auth, http):
    from live_support import create_service_account

    settings = _settings(live_auth)  # writer_identity = the writer service account's uuid
    port = TiledWhoamiAdapter(live_auth.url, http)
    writer = _cred(live_auth, live_auth.writer.key, scheme="Apikey")
    assert port.whoami(writer, now=utcnow()).principal_type == "service"
    with pytest.raises(Unauthenticated) as exc:
        _ctx(settings, port, writer)
    assert exc.value.reason == "writer_credential"
    other_service = create_service_account(live_auth)
    with pytest.raises(Unauthenticated) as exc:
        _ctx(settings, port, _cred(live_auth, other_service.key, scheme="Apikey"))
    assert exc.value.reason == "service_principal"


# ------------------------------------------------------------------ downgrade and outage (AUTH-07, LOCAL-07)


def test_role_downgrade_denies_the_next_protected_boundary(live_fresh, http):
    """0.2.18 has no role-change API: the admin role row is removed in the authn database."""
    settings = _settings(live_fresh)
    port = CountingPort(TiledWhoamiAdapter(live_fresh.url, http))
    cache = RoleCheckCache(settings.role_check_max_age)
    cred = _cred(live_fresh, access_token(live_fresh, "admin"))
    ctx = _ctx(settings, port, cred, role_cache=cache)
    assert ctx.has("graph.edit")
    worker = WorkerAuthority(ctx, settings, identity_port=port, credential_provider=lambda: cred, clock=utcnow)
    assert worker.check("graph.publish").has("graph.publish")

    assert drop_role(live_fresh, "admin") == 1
    calls = len(port.calls)
    # Within the cache age a chat-level entry may still use the cached check ...
    assert _ctx(settings, port, cred, role_cache=cache).has("graph.edit") and len(port.calls) == calls
    # ... but every protected boundary re-runs whoami and sees the downgrade.
    fresh = _ctx(settings, port, cred, role_cache=cache, require_fresh=True)
    assert fresh.capabilities == CHAT_CAPABILITIES
    with pytest.raises(ContractError) as err:
        worker.check("graph.publish")
    assert err.value.code is ErrorCode.DENIED and worker.stopped_reason
    with pytest.raises(ContractError) as err:
        create_approval(ctx, make_plan(live_fresh.url), settings, approval_id="a-down", clock=utcnow,
                        identity_port=port, approver_credential=cred)
    assert err.value.code is ErrorCode.DENIED


def test_identity_server_stopped_makes_privileged_work_unavailable(live_fresh, http):
    settings = _settings(live_fresh)
    port = TiledWhoamiAdapter(live_fresh.url, http, timeout=2.0)
    cred = _cred(live_fresh, access_token(live_fresh, "admin"))
    ctx = _ctx(settings, port, cred)
    live_fresh.stop()
    with pytest.raises(IdentityUnavailable):
        port.whoami(cred, now=utcnow())
    for kw in ({}, {"require_fresh": True}, {"actor_kind": ActorKind.WORKER}):
        with pytest.raises(ContractError) as err:
            _ctx(settings, port, cred, **kw)
        assert err.value.code is ErrorCode.UNAVAILABLE  # never local trust, another server or the writer
    worker = WorkerAuthority(ctx, settings, identity_port=port, credential_provider=lambda: cred, clock=utcnow)
    with pytest.raises(ContractError) as err:
        worker.check("graph.publish")
    assert err.value.code is ErrorCode.UNAVAILABLE and worker.stopped_reason is None  # retryable, not stopped

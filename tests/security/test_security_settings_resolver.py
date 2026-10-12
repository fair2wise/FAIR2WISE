"""T25B: startup settings and context resolution (access_contract.md sections 2-6, 12)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from app.modules.contracts.context import (
    ADMIN_CAPABILITIES,
    CAPABILITIES,
    CHAT_CAPABILITIES,
    LOCAL_OPERATOR,
    ActorKind,
    AuthorizationMode,
    DeploymentProfile,
)
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.security.authorization import (
    LOOPBACK_HOST,
    NETWORK_EXPOSURE_WARNING,
    DeploymentSettings,
    RoleCheckCache,
    StartupConfigError,
    Unauthenticated,
    resolve_context,
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
    plain_user,
    prod_settings,
    staging_settings,
)

REPO = Path(__file__).resolve().parents[2]


# --- startup (S1-S3, LOCAL-07, LOCAL-08) ------------------------------------------

@pytest.mark.parametrize("profile", ["als-dev", "als-prod"])
def test_als_with_local_trusted_refuses_startup(profile):
    """LOCAL-07 / S1: ALS + local_trusted from any source is a startup error."""
    with pytest.raises(StartupConfigError, match="local_trusted not allowed"):
        DeploymentSettings(profile=profile, mode="local_trusted", writer_identity=WRITER)
    with pytest.raises(StartupConfigError, match="local_trusted not allowed"):
        DeploymentSettings.from_mapping({"F2W_DEPLOYMENT_PROFILE": profile, "F2W_AUTHORIZATION_MODE":
                                         "local_trusted", "F2W_TILED_WRITER_IDENTITY": WRITER})


@pytest.mark.parametrize("profile", ["", "test", "LOCAL", "als", "als-staging", None])
def test_unknown_or_empty_profile_refuses_startup(profile):
    with pytest.raises(StartupConfigError):
        DeploymentSettings.from_mapping({"F2W_DEPLOYMENT_PROFILE": profile or "",
                                         "F2W_TILED_WRITER_IDENTITY": WRITER})
    with pytest.raises(StartupConfigError):
        DeploymentSettings(profile=profile, writer_identity=WRITER)


def test_local_with_tiled_authenticated_refuses_startup():
    """S2."""
    with pytest.raises(StartupConfigError):
        DeploymentSettings(profile="local", mode="tiled_authenticated", writer_identity=WRITER)


def test_als_target_is_fixed_per_profile():
    """S3 / AUTH-08: ALS targets cannot be pointed at another server."""
    assert staging_settings().tiled_target == STAGING
    assert prod_settings(tiled_target="https://TILED.als.lbl.gov/").tiled_target == PROD
    with pytest.raises(StartupConfigError):
        staging_settings(tiled_target=PROD)
    with pytest.raises(StartupConfigError):
        prod_settings(tiled_target="http://127.0.0.1:8765")
    with pytest.raises(StartupConfigError):
        local_settings(tiled_target="https://tiled.als.lbl.gov")


def test_writer_identity_required_and_not_local_operator():
    with pytest.raises(StartupConfigError):
        DeploymentSettings(profile="local", writer_identity="")
    with pytest.raises(StartupConfigError):
        DeploymentSettings(profile="local", writer_identity=LOCAL_OPERATOR)


def test_role_check_max_age_is_bounded():
    assert staging_settings(role_check_max_age=timedelta(seconds=30)).role_check_max_age.seconds == 30
    for bad in (timedelta(0), timedelta(hours=1)):
        with pytest.raises(StartupConfigError):
            staging_settings(role_check_max_age=bad)


def test_local_bind_defaults_to_loopback_and_network_warns():
    """LOCAL-08."""
    s = DeploymentSettings.from_mapping({"F2W_DEPLOYMENT_PROFILE": "local", "F2W_TILED_WRITER_IDENTITY": WRITER})
    assert s.local_bind == "loopback" and s.bind_host == LOOPBACK_HOST and s.exposure_warning is None
    wide = local_settings(local_bind="network")
    assert wide.bind_host == "0.0.0.0" and wide.exposure_warning == NETWORK_EXPOSURE_WARNING
    with pytest.raises(StartupConfigError):
        staging_settings(local_bind="network")
    with pytest.raises(StartupConfigError):
        local_settings(local_bind="everywhere")


def test_settings_are_immutable():
    s = local_settings()
    with pytest.raises(Exception):
        s.profile = DeploymentProfile.ALS_PROD


# --- :local (LOCAL-01, LOCAL-02, LOCAL-05) -------------------------------------------

@pytest.mark.parametrize("credential", [
    None, "", "garbage", TiledCredential("expired", tiled_target=PROD, expires_at=FakeClock().now - timedelta(1)),
    TiledCredential("non-admin-token"),
])
def test_local_is_local_operator_with_all_capabilities_and_no_identity_call(credential):
    port = ForbiddenIdentityPort()
    ctx = resolve_context(local_settings(), credential, request_id="r1", identity_port=port, clock=FakeClock())
    assert ctx.principal.subject == LOCAL_OPERATOR and ctx.principal.is_local_operator()
    assert ctx.mode is AuthorizationMode.LOCAL_TRUSTED and ctx.capabilities == CAPABILITIES
    assert port.calls == 0


# --- ALS resolution (AUTH-01, AUTH-02, AUTH-06, AUTH-08) ------------------------------

def _als(port, credential, clock=None, **kw):
    return resolve_context(staging_settings(), credential, request_id="r1", identity_port=port,
                           clock=clock or FakeClock(), **kw)


def test_als_admin_gets_all_capabilities_user_gets_chat_only():
    port = FakeTiledIdentityPort(tokens={"a": admin_user(), "u": plain_user()})
    admin, user = _als(port, "a"), _als(port, "u")
    assert admin.capabilities == CAPABILITIES and admin.principal.is_admin is True
    assert admin.principal.issuer == STAGING and admin.principal.roles == ("admin",)
    assert user.capabilities == CHAT_CAPABILITIES and not (user.capabilities & ADMIN_CAPABILITIES)
    assert user.principal.is_admin is False and user.tiled_target == STAGING


@pytest.mark.parametrize("credential,reason", [
    (None, "missing"), ("", "missing"), ("unknown", "invalid"), ("revoked", "revoked"),
    ("expired-at-tiled", "expired"),
    (TiledCredential("u", expires_at=FakeClock().now + timedelta(seconds=5)), "expired"),
    (TiledCredential("u", tiled_target=PROD), "wrong_issuer"),
])
def test_als_unauthenticated_has_no_context(credential, reason):
    """AUTH-01, AUTH-07, AUTH-08: missing/expired/revoked/wrong-issuer give no context."""
    port = FakeTiledIdentityPort(tokens={"u": plain_user()},
                                 rejected={"revoked": "revoked", "expired-at-tiled": "expired"})
    with pytest.raises(Unauthenticated) as info:
        _als(port, credential)
    assert info.value.reason == reason


def test_als_writer_or_service_credential_never_establishes_a_user():
    """AUTH-06: the writer's identity is never used for the caller's role."""
    port = FakeTiledIdentityPort(tokens={
        "w": FakeUser("uuid-writer", roles=("admin",), identity_ids=(WRITER,)),
        "s": FakeUser("uuid-svc", roles=("admin",), principal_type="service"),
    })
    for token, reason in (("w", "writer_credential"), ("s", "service_principal")):
        with pytest.raises(Unauthenticated) as info:
            _als(port, token)
        assert info.value.reason == reason


def test_identity_port_for_another_server_is_refused():
    """AUTH-08: no implicit alternate-server validation."""
    port = FakeTiledIdentityPort(tiled_target=PROD, tokens={"a": admin_user()})
    with pytest.raises(ContractError) as info:
        _als(port, "a")
    assert info.value.code is ErrorCode.UNAVAILABLE


def test_identity_outage_never_enables_local_trust():
    """LOCAL-07: outage -> UNAVAILABLE; never local-operator, writer or another server."""
    port = FakeTiledIdentityPort(tokens={"a": admin_user()}, outage=True)
    with pytest.raises(ContractError) as info:
        _als(port, "a", require_fresh=True)
    assert info.value.code is ErrorCode.UNAVAILABLE and info.value.retryable
    with pytest.raises(ContractError):
        resolve_context(staging_settings(), "a", request_id="r", identity_port=None, clock=FakeClock())


def test_role_cache_is_bounded_and_bypassed_at_protected_boundaries():
    clock = FakeClock()
    port = FakeTiledIdentityPort(tokens={"a": admin_user()})
    cache = RoleCheckCache(timedelta(seconds=60))
    _als(port, "a", clock, role_cache=cache)
    _als(port, "a", clock, role_cache=cache)
    assert len(port.calls) == 1
    _als(port, "a", clock, role_cache=cache, require_fresh=True)
    assert len(port.calls) == 2
    # Downgrade at Tiled: a fresh boundary sees it immediately ...
    port.tokens["a"] = FakeUser("uuid-admin", roles=("user",), identity_ids=("alice",))
    assert not _als(port, "a", clock, role_cache=cache, require_fresh=True).principal.is_admin
    # ... and cached chat reads never exceed max age.
    port.tokens["a"] = admin_user()
    _als(port, "a", clock, role_cache=cache, require_fresh=True)
    port.tokens["a"] = FakeUser("uuid-admin", roles=("user",), identity_ids=("alice",))
    assert _als(port, "a", clock, role_cache=cache).principal.is_admin  # within 60 s
    clock.advance(61)
    assert not _als(port, "a", clock, role_cache=cache).principal.is_admin


def test_outage_with_fresh_cache_keeps_chat_but_refuses_privileged():
    """Section 12: chat continues within the role-cache age; privileged actions 503."""
    clock = FakeClock()
    port = FakeTiledIdentityPort(tokens={"u": plain_user()})
    cache = RoleCheckCache(timedelta(seconds=60))
    _als(port, "u", clock, role_cache=cache)
    port.outage = True
    assert _als(port, "u", clock, role_cache=cache).has("chat.ask")
    with pytest.raises(ContractError):
        _als(port, "u", clock, role_cache=cache, require_fresh=True)
    clock.advance(61)
    with pytest.raises(ContractError):
        _als(port, "u", clock, role_cache=cache)


def test_cache_never_stores_raw_tokens():
    cache = RoleCheckCache(timedelta(seconds=60))
    port = FakeTiledIdentityPort(tokens={"secret-token-value": plain_user()})
    _als(port, "secret-token-value", role_cache=cache)
    assert "secret-token-value" not in repr(cache._entries)
    assert "secret-token-value" not in repr(TiledCredential("secret-token-value"))


def test_resolver_has_no_trust_claim_inputs():
    """AUTH-06 / LOCAL-07: the resolver takes no request field that could claim trust."""
    import inspect
    params = set(inspect.signature(resolve_context).parameters)
    assert not params & {"headers", "body", "mode", "is_admin", "host", "hostname", "profile", "roles"}


def test_forge_delegate_keeps_caller_capability():
    """AUTH-10: a non-admin delegated call inside an admin-capable host stays chat-only."""
    port = FakeTiledIdentityPort(tokens={"u": plain_user()})
    ctx = _als(port, "u", actor_kind=ActorKind.FORGE_DELEGATE)
    assert ctx.actor_kind is ActorKind.FORGE_DELEGATE and not ctx.has("ingest.acquire")


# --- import hygiene --------------------------------------------------------------------

def test_import_has_no_side_effects():
    """Importing the security modules loads no heavy deps or other app code and changes no env."""
    code = (
        "import json, os, sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "env = dict(os.environ); before = set(sys.modules)\n"
        "import app.modules.security, app.modules.security.authorization, app.modules.security.tiled_identity\n"
        "new = sorted(set(sys.modules) - before)\n"
        "print(json.dumps({'new': new, 'env_changed': dict(os.environ) != env}))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True, cwd="/")
    result = json.loads(out.stdout.strip().splitlines()[-1])
    app_mods = [m for m in result["new"] if m.startswith("app")]
    allowed = ("app.modules.contracts", "app.modules.security")
    assert all(m in {"app", "app.modules"} or m.startswith(allowed) for m in app_mods), app_mods
    heavy = {"torch", "faiss", "sentence_transformers", "fitz", "openai", "langgraph", "tiled", "dotenv",
             "academy", "httpx", "fastapi", "starlette", "requests"}
    assert not heavy & {m.split(".")[0] for m in result["new"]}
    assert result["env_changed"] is False

"""Fakes for the T25B security tests. Nothing here contacts a Tiled server."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.modules.contracts.changes import ChangePlan
from app.modules.security.authorization import ALS_PROFILE_TARGETS, DeploymentSettings
from app.modules.contracts.context import DeploymentProfile
from app.modules.security.tiled_identity import (
    IdentityRejected,
    IdentityUnavailable,
    TiledCredential,
    VerifiedIdentity,
)

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
STAGING = ALS_PROFILE_TARGETS[DeploymentProfile.ALS_DEV]
PROD = ALS_PROFILE_TARGETS[DeploymentProfile.ALS_PROD]
WRITER = "f2w-writer"
SHA = "a" * 64


class FakeClock:
    """Manually advanced clock."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class FakeUser:
    """A principal the fake Tiled knows."""

    subject: str
    roles: Tuple[str, ...] = ("user",)
    principal_type: str = "user"
    identity_ids: Tuple[str, ...] = ()
    provider: str = "toy"


@dataclass
class FakeTiledIdentityPort:
    """Programmable identity port for one Tiled target.

    ``tokens`` maps access tokens to users; ``rejected`` maps tokens to rejection
    reasons; ``outage`` makes every call raise ``IdentityUnavailable``.
    """

    tiled_target: str = STAGING
    tokens: Dict[str, FakeUser] = field(default_factory=dict)
    rejected: Dict[str, str] = field(default_factory=dict)
    outage: bool = False
    calls: List[str] = field(default_factory=list)

    def whoami(self, credential: TiledCredential, *, now: datetime) -> VerifiedIdentity:
        self.calls.append(credential.fingerprint())
        if self.outage:
            raise IdentityUnavailable("fake outage")
        if credential.access_token in self.rejected:
            raise IdentityRejected(self.rejected[credential.access_token])
        user = self.tokens.get(credential.access_token)
        if user is None:
            raise IdentityRejected("invalid")
        return VerifiedIdentity(
            subject=user.subject, issuer=self.tiled_target, principal_type=user.principal_type,
            roles=user.roles, verified_at=now, identity_provider=user.provider,
            identity_ids=user.identity_ids or (user.subject,), credential_expires_at=credential.expires_at,
        )


class ForbiddenIdentityPort:
    """Identity port that fails the test if it is ever called (LOCAL-05)."""

    tiled_target = "http://127.0.0.1:8765"

    def __init__(self) -> None:
        self.calls = 0

    def whoami(self, credential: TiledCredential, *, now: datetime) -> VerifiedIdentity:
        self.calls += 1
        raise AssertionError("local resolution must never call the user-identity port")


class InMemoryCredentialStore:
    """Server-side session store: opaque cookie value -> credential."""

    def __init__(self) -> None:
        self.sessions: Dict[str, TiledCredential] = {}

    def get(self, session_token: str) -> Optional[TiledCredential]:
        return self.sessions.get(session_token)


def local_settings(**kw) -> DeploymentSettings:
    return DeploymentSettings(profile="local", writer_identity=WRITER, **kw)


def staging_settings(**kw) -> DeploymentSettings:
    return DeploymentSettings(profile="als-dev", writer_identity=WRITER, **kw)


def prod_settings(**kw) -> DeploymentSettings:
    return DeploymentSettings(profile="als-prod", writer_identity=WRITER, **kw)


def admin_user() -> FakeUser:
    return FakeUser("uuid-admin", roles=("admin",), identity_ids=("alice",))


def plain_user() -> FakeUser:
    return FakeUser("uuid-user", roles=("user",), identity_ids=("bob",))


def make_plan(target: str, plan_id: str = "plan-1") -> ChangePlan:
    return ChangePlan(
        plan_id=plan_id, snapshot_id="snap-1", input_sha256=SHA, target=target, corpus_scope=("rsoxs",),
        base_revision="r0", creates=1, links=0, supersessions=0, validation_report_sha256=SHA,
        plan_sha256=SHA,
    )

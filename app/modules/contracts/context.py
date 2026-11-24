"""Request context: who is acting, under which deployment profile, with what scope.

The context is built server-side from startup configuration and, on ALS profiles,
from a verified Tiled identity. It is never accepted as authority from a request
body, header or model argument (plan section 4.13, D30/D34).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

LOCAL_OPERATOR = "local-operator"

#: Capabilities an ordinary signed-in ALS user holds (chat-only, D30).
CHAT_CAPABILITIES = frozenset({
    "health.read", "auth.session", "chat.ask", "chat.read_support", "session.own", "trace.read_own",
    "settings.personal", "telemetry.write", "annotation.write", "chat.decline_pending",
})

#: Privileged capabilities: local-operator (D34) or verified ALS Tiled admin (D30).
ADMIN_CAPABILITIES = frozenset({
    "settings.global", "session.any", "trace.read_any", "ingest.acquire", "ingest.extract",
    "figures.process", "ops.run", "graph.edit", "graph.upload", "curation.approve", "json.build",
    "graph.publish", "jobs.admin",
})

#: ``library.author_schema`` (schema generation): create, validate, generate and preview user LinkML schemas.
#: Library capabilities (A8/T34): held by the local operator only. They are in the vocabulary but never
#: granted to an ALS principal, admin or not (``capabilities_for``; ``RequestContext`` refuses them in
#: ``tiled_authenticated`` mode). ALS-admin support for the library is T44, deferred.
LOCAL_ONLY_CAPABILITIES = frozenset({"library.read", "library.upload", "library.index", "library.extract",
                                    "library.harvest", "library.author_schema"})

#: Frozen capability vocabulary (docs/migration/authentication/access_contract.md section 6).
#: Reading private sources (``source.read``) is deliberately *not* a capability: it can
#: never be placed on a request. It needs a sealed per-request grant from trusted
#: service code (``app/modules/security/content_access.py``).
CAPABILITIES = CHAT_CAPABILITIES | ADMIN_CAPABILITIES | LOCAL_ONLY_CAPABILITIES


class DeploymentProfile(StrEnum):
    """Image/deployment profile selected at startup (D23-D25)."""

    LOCAL = "local"
    ALS_DEV = "als-dev"
    ALS_PROD = "als-prod"


class AuthorizationMode(StrEnum):
    """How caller authority is established.

    Attributes:
        LOCAL_TRUSTED: ``:local`` only. Every caller is the local operator with
            full application capabilities and no sign-in (D34).
        TILED_AUTHENTICATED: ALS profiles. The caller is a verified Tiled
            principal; privileged work requires current admin status (D30).
    """

    LOCAL_TRUSTED = "local_trusted"
    TILED_AUTHENTICATED = "tiled_authenticated"


class ActorKind(StrEnum):
    """What is driving the request."""

    INTERACTIVE = "interactive"
    WORKER = "worker"
    FORGE_DELEGATE = "forge_delegate"
    CLI = "cli"


class Principal(BaseModel):
    """The acting identity recorded in audit and approvals.

    Attributes:
        subject: ``"local-operator"`` in local mode, else the verified Tiled subject.
        issuer: Tiled server that verified the subject; ``None`` in local mode.
        is_admin: Verified admin status at ``verified_at``, derived server-side from
            ``roles``; ``None`` in local mode, where no role check is performed.
        verified_at: When identity/role was checked; ``None`` in local mode.
        identity_provider: Tiled authentication provider of the sign-in (ALS only).
        roles: Role names reported by Tiled at ``verified_at`` (ALS only).
        credential_expires_at: Expiry of the user's Tiled credential (ALS only).
            The credential itself is never stored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    issuer: str | None = None
    is_admin: bool | None = None
    verified_at: datetime | None = None
    identity_provider: str | None = None
    roles: tuple[str, ...] | None = None
    credential_expires_at: datetime | None = None

    def is_local_operator(self) -> bool:
        """Return whether this is the local-operator with no Tiled identity fields."""
        return self.subject == LOCAL_OPERATOR and not any(
            (self.issuer, self.verified_at, self.identity_provider, self.roles, self.credential_expires_at)
        ) and self.is_admin is None


class RequestContext(BaseModel):
    """Server-constructed context passed to every service, tool and job.

    Attributes:
        profile: Deployment profile from startup configuration.
        mode: Authorization mode implied by ``profile``.
        principal: Acting identity.
        capabilities: Capability names granted for this request.
        corpus_scope: Corpora this request may read or affect.
        request_id: Correlation ID for this request.
        run_id: Agent/job run this request belongs to, if any.
        deadline: Absolute time after which work must stop.
        budget_ref: Reference to the configured cost/rate budget, if any.
        session_id: Chat session bound server-side to this principal; never a credential.
        tiled_target: Normalized base URL of the profile's Tiled instance; binds
            identity and grants to one environment.
        actor_kind: Whether an interactive caller, worker, FORGE delegate or CLI drives it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: DeploymentProfile
    mode: AuthorizationMode
    principal: Principal
    capabilities: frozenset[str] = Field(default_factory=frozenset)
    corpus_scope: frozenset[str] = Field(default_factory=frozenset)
    request_id: str
    run_id: str | None = None
    deadline: datetime | None = None
    budget_ref: str | None = None
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    tiled_target: str | None = None
    actor_kind: ActorKind = ActorKind.INTERACTIVE

    @model_validator(mode="after")
    def _profile_matches_mode(self) -> RequestContext:
        """Validate the context's mode, profile and capabilities together.

        Rejects trusted-local mode outside ``:local``, unverified ALS principals and
        capability names outside the frozen vocabulary.
        """
        unknown = self.capabilities - CAPABILITIES
        if unknown:
            raise ValueError(f"unknown capabilities: {sorted(unknown)}")
        if self.mode is AuthorizationMode.LOCAL_TRUSTED:
            if self.profile is not DeploymentProfile.LOCAL:
                raise ValueError("local_trusted mode is only valid for the local profile")
            if self.principal.subject != LOCAL_OPERATOR:
                raise ValueError("local_trusted mode must act as the local-operator")
            if not self.principal.is_local_operator():
                raise ValueError("local-operator must not carry a synthetic Tiled identity or role check")
        else:
            if self.principal.subject == LOCAL_OPERATOR:
                raise ValueError("ALS requests cannot act as local-operator")
            if self.capabilities & LOCAL_ONLY_CAPABILITIES:
                raise ValueError("library capabilities are local-operator only")
            if self.profile is DeploymentProfile.LOCAL:
                raise ValueError("the local profile uses local_trusted mode")
            if not (self.principal.issuer and self.principal.verified_at and self.principal.is_admin is not None):
                raise ValueError("ALS principals must carry issuer, verified_at and a role check result")
        return self

    def has(self, capability: str) -> bool:
        """Return whether ``capability`` is granted to this request."""
        return capability in self.capabilities

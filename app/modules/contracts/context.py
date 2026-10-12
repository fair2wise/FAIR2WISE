"""Request context: who is acting, under which deployment profile, with what scope.

The context is built server-side from startup configuration and, on ALS profiles,
from a verified Tiled identity. It is never accepted as authority from a request
body, header or model argument (plan section 4.13, D30/D34).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import FrozenSet, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

LOCAL_OPERATOR = "local-operator"


class DeploymentProfile(str, Enum):
    """Image/deployment profile selected at startup (D23-D25)."""

    LOCAL = "local"
    ALS_DEV = "als-dev"
    ALS_PROD = "als-prod"


class AuthorizationMode(str, Enum):
    """How caller authority is established.

    Attributes:
        LOCAL_TRUSTED: ``:local`` only. Every caller is the local operator with
            full application capabilities and no sign-in (D34).
        TILED_AUTHENTICATED: ALS profiles. The caller is a verified Tiled
            principal; privileged work requires current admin status (D30).
    """

    LOCAL_TRUSTED = "local_trusted"
    TILED_AUTHENTICATED = "tiled_authenticated"


class Principal(BaseModel):
    """The acting identity recorded in audit and approvals.

    Attributes:
        subject: ``"local-operator"`` in local mode, else the verified Tiled subject.
        issuer: Tiled server that verified the subject; ``None`` in local mode.
        is_admin: Verified admin status at ``verified_at``; ``None`` in local mode,
            where no role check is performed.
        verified_at: When identity/role was checked; ``None`` in local mode.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    issuer: Optional[str] = None
    is_admin: Optional[bool] = None
    verified_at: Optional[datetime] = None


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
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: DeploymentProfile
    mode: AuthorizationMode
    principal: Principal
    capabilities: FrozenSet[str] = Field(default_factory=frozenset)
    corpus_scope: FrozenSet[str] = Field(default_factory=frozenset)
    request_id: str
    run_id: Optional[str] = None
    deadline: Optional[datetime] = None
    budget_ref: Optional[str] = None

    @model_validator(mode="after")
    def _profile_matches_mode(self) -> "RequestContext":
        """Reject trusted-local mode outside ``:local`` and unverified ALS principals."""
        if self.mode is AuthorizationMode.LOCAL_TRUSTED:
            if self.profile is not DeploymentProfile.LOCAL:
                raise ValueError("local_trusted mode is only valid for the local profile")
            if self.principal.subject != LOCAL_OPERATOR:
                raise ValueError("local_trusted mode must act as the local-operator")
            if self.principal.issuer or self.principal.verified_at or self.principal.is_admin is not None:
                raise ValueError("local-operator must not carry a synthetic Tiled identity or role check")
        else:
            if self.profile is DeploymentProfile.LOCAL:
                raise ValueError("the local profile uses local_trusted mode")
            if not (self.principal.issuer and self.principal.verified_at and self.principal.is_admin is not None):
                raise ValueError("ALS principals must carry issuer, verified_at and a role check result")
        return self

    def has(self, capability: str) -> bool:
        """Return whether ``capability`` is granted to this request."""
        return capability in self.capabilities

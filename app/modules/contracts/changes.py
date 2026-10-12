"""Change plans, approvals, snapshot manifests and import receipts.

The publication path is: frozen versioned JSON snapshot -> ``ChangePlan`` ->
``ApprovalRecord`` -> import -> verified ``ImportReceipt`` (D21, D31). An approval
binds to one plan hash and target; it never authorizes a different plan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .context import LOCAL_OPERATOR, AuthorizationMode, DeploymentProfile

_SHA256 = r"^[0-9a-f]{64}$"


class SnapshotManifest(BaseModel):
    """Sidecar manifest for one frozen versioned JSON KG snapshot.

    The graph payload itself keeps its existing json2kg shape; fingerprints live here.

    Attributes:
        snapshot_id: Identifier of this snapshot.
        corpus: Corpus the snapshot covers.
        graph_path: Repository-relative path of the graph JSON.
        graph_sha256: SHA-256 of the graph JSON bytes.
        parent_snapshot_id: Snapshot this one revises, if any.
        schema_fingerprint: Fingerprint of the schema set used.
        extraction_fingerprint: Fingerprint of extraction code/prompt/model settings.
        corpus_fingerprint: Fingerprint of the source set.
        validation_report_sha256: Hash of the validation report for these bytes.
        created_at: Creation time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    corpus: str
    graph_path: str
    graph_sha256: str = Field(pattern=_SHA256)
    parent_snapshot_id: Optional[str] = None
    schema_fingerprint: str
    extraction_fingerprint: str
    corpus_fingerprint: str
    validation_report_sha256: Optional[str] = Field(default=None, pattern=_SHA256)
    created_at: datetime


class ChangePlan(BaseModel):
    """Immutable, scoped plan to project one snapshot onto one graph target.

    Attributes:
        plan_id: Identifier of this plan.
        snapshot_id: Source snapshot.
        input_sha256: SHA-256 of the snapshot bytes the plan was computed from.
        target: Target graph instance identifier (for example a Tiled URL + namespace).
        corpus_scope: Corpora the plan may affect.
        base_revision: Target revision the plan was computed against.
        creates: Count of entities/links to create.
        links: Count of links to add.
        supersessions: Count of records superseded.
        affected_ids: Stable IDs the plan touches.
        validation_report_sha256: Validation report hash for ``input_sha256``.
        plan_sha256: Hash of the canonical plan body; approvals bind to it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    snapshot_id: str
    input_sha256: str = Field(pattern=_SHA256)
    target: str
    corpus_scope: Tuple[str, ...]
    base_revision: str
    creates: int = Field(ge=0)
    links: int = Field(ge=0)
    supersessions: int = Field(ge=0)
    affected_ids: Tuple[str, ...] = ()
    validation_report_sha256: str = Field(pattern=_SHA256)
    plan_sha256: str = Field(pattern=_SHA256)


class RoleCheckEvidence(BaseModel):
    """How an ALS approver's admin status was established.

    Attributes:
        method: Always ``"tiled_whoami"``: a live check against the profile's Tiled
            with the approver's own credential, never the writer's.
        tiled_target: Tiled base URL the check ran against.
        roles: Role names Tiled reported.
        identity_provider: Tiled authentication provider, if reported.
        tiled_library_version: Tiled server version, if reported.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: Literal["tiled_whoami"] = "tiled_whoami"
    tiled_target: str
    roles: Tuple[str, ...]
    identity_provider: Optional[str] = None
    tiled_library_version: Optional[str] = None


class ApprovalRecord(BaseModel):
    """An explicit approval of one plan, made by a human through a trusted interface.

    Never created by a model or tool call.

    Attributes:
        approval_id: Identifier of this approval.
        profile: Deployment profile where the approval was made.
        mode: Authorization mode in effect.
        plan_id: Approved plan.
        plan_sha256: Hash the approval binds to.
        target: Target the approval applies to.
        scope: Corpora the approval covers.
        requested_by: Who requested the change.
        approved_by: Who approved it.
        executed_as: Technical writer identity that will perform the import (D27).
        approver_issuer: Verified issuer of ``approved_by`` (ALS only).
        role_checked_at: When ``approved_by``'s admin status was verified (ALS only).
        approver_is_admin: Result of that role check; must be ``True`` on ALS (ALS only).
        role_check_evidence: How the role check was made (ALS only).
        requester_issuer: Verified issuer of ``requested_by`` (ALS only).
        operation_id: Job/operation that requested the plan.
        limits: Numeric limits on the approved operation (for example max creates).
        expires_at: Time after which the approval is void.
        approved_at: Approval time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str
    profile: DeploymentProfile
    mode: AuthorizationMode
    plan_id: str
    plan_sha256: str = Field(pattern=_SHA256)
    target: str
    scope: Tuple[str, ...]
    requested_by: str
    approved_by: str
    executed_as: str
    approver_issuer: Optional[str] = None
    role_checked_at: Optional[datetime] = None
    approver_is_admin: Optional[bool] = None
    role_check_evidence: Optional[RoleCheckEvidence] = None
    requester_issuer: Optional[str] = None
    operation_id: Optional[str] = None
    limits: Dict[str, int] = Field(default_factory=dict)
    expires_at: Optional[datetime] = None
    approved_at: datetime

    @model_validator(mode="after")
    def _profile_rules(self) -> "ApprovalRecord":
        """Local approvals are local-operator with no synthetic identity; ALS ones are verified."""
        if self.mode is AuthorizationMode.LOCAL_TRUSTED:
            if self.profile is not DeploymentProfile.LOCAL:
                raise ValueError("local_trusted approvals are only valid for the local profile")
            if self.approved_by != LOCAL_OPERATOR or self.requested_by != LOCAL_OPERATOR:
                raise ValueError("local approvals record requested_by and approved_by as local-operator")
            als_fields = (
                self.approver_issuer, self.role_checked_at, self.role_check_evidence, self.requester_issuer
            )
            if any(als_fields) or self.approver_is_admin is not None:
                raise ValueError("local approvals must not carry a synthetic Tiled identity or role check")
        else:
            if self.profile is DeploymentProfile.LOCAL:
                raise ValueError("the local profile uses local_trusted approvals")
            if not (self.approver_issuer and self.role_checked_at and self.requester_issuer):
                raise ValueError(
                    "ALS approvals require verified approver and requester issuers and a role check time"
                )
            if self.approver_is_admin is not True:
                raise ValueError("ALS approvals require a role check showing the approver is an admin")
            if LOCAL_OPERATOR in (self.approved_by, self.requested_by):
                raise ValueError("ALS approvals cannot be requested or made by local-operator")
        if self.executed_as == self.approved_by:
            raise ValueError("executed_as (technical writer) must be recorded separately from approved_by")
        return self

    def authorizes(self, plan: ChangePlan, at: datetime) -> bool:
        """Return whether this approval covers ``plan`` at time ``at``."""
        if self.expires_at is not None and at >= self.expires_at:
            return False
        return (
            plan.plan_id == self.plan_id
            and plan.plan_sha256 == self.plan_sha256
            and plan.target == self.target
            and set(plan.corpus_scope) <= set(self.scope)
        )


class ImportReceipt(BaseModel):
    """Target-specific record that an approved plan was applied and verified.

    Receipts never modify the source snapshot. A receipt is valid only for the
    target and revision it names.

    Attributes:
        receipt_id: Identifier of this receipt.
        plan_id: Applied plan.
        plan_sha256: Plan hash that was applied.
        approval_id: Approval that authorized the import.
        snapshot_sha256: Snapshot bytes that were imported.
        target: Target instance.
        executed_as: Writer identity used.
        adapter_version: Import adapter build/version.
        id_mapping_sha256: Hash of the snapshot-ID to target-ID mapping artifact.
        outcomes: Counts per outcome (for example ``{"created": 10, "reconciled": 2}``).
        verified: Whether post-import readback verification passed.
        completed_at: Completion time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str
    plan_id: str
    plan_sha256: str = Field(pattern=_SHA256)
    approval_id: str
    snapshot_sha256: str = Field(pattern=_SHA256)
    target: str
    executed_as: str
    adapter_version: str
    id_mapping_sha256: str = Field(pattern=_SHA256)
    outcomes: Dict[str, int] = Field(default_factory=dict)
    verified: bool
    completed_at: datetime

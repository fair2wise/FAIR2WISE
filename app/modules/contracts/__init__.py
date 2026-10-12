"""Shared, versioned contracts for the FORGE-to-F2W literature migration (task T01).

These records are the boundary types that tools, jobs, the public client, FastAPI
and FORGE consumers exchange. They carry no behavior beyond validation, and
importing this package has no side effects: it reads no environment variables or
files, opens no network or Tiled connection, and imports no other ``app.modules``
code.

Status: draft for gate H1. See the ADRs in ``docs/adr/`` and
``docs/migration/public_api_spec.md``.
"""

from __future__ import annotations

from .changes import ApprovalRecord, ChangePlan, ImportReceipt, RoleCheckEvidence, SnapshotManifest
from .context import (
    ADMIN_CAPABILITIES,
    CAPABILITIES,
    CHAT_CAPABILITIES,
    ActorKind,
    AuthorizationMode,
    DeploymentProfile,
    Principal,
    RequestContext,
)
from .errors import CapabilityError, ContractError, ErrorCode
from .evidence import EvidenceRef, MatchType, SourceLocator
from .jobs import JobEvent, JobHandle, JobState, StageOutcome
from .ports import DocumentStore, GraphCapabilities, GraphPort
from .tools import CostClass, Effect, ToolResult, ToolSpec, ToolStatus, status_for_code

CONTRACT_VERSION = "1.0.0-draft.3"

__all__ = [
    "ADMIN_CAPABILITIES",
    "CAPABILITIES",
    "CHAT_CAPABILITIES",
    "CONTRACT_VERSION",
    "ActorKind",
    "ApprovalRecord",
    "AuthorizationMode",
    "CapabilityError",
    "ChangePlan",
    "ContractError",
    "CostClass",
    "DeploymentProfile",
    "DocumentStore",
    "Effect",
    "ErrorCode",
    "EvidenceRef",
    "GraphCapabilities",
    "GraphPort",
    "ImportReceipt",
    "JobEvent",
    "JobHandle",
    "JobState",
    "MatchType",
    "Principal",
    "RequestContext",
    "RoleCheckEvidence",
    "SnapshotManifest",
    "SourceLocator",
    "StageOutcome",
    "ToolResult",
    "ToolSpec",
    "ToolStatus",
    "status_for_code",
]

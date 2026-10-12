"""Tool specifications and results.

A tool declares an *effect set* rather than one side-effect label: one call can
perform network I/O, write staged artifacts and spend model budget (plan section
4.4). Exposure is controlled by capabilities, not role names.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import ErrorCode
from .evidence import EvidenceRef


class Effect(str, Enum):
    """Effects a tool may have. A tool with no effects is a pure read."""

    READ_CORPUS = "read_corpus"
    READ_PRIVATE_SOURCE = "read_private_source"
    NETWORK = "network"
    WRITE_STAGED = "write_staged"
    WRITE_GRAPH = "write_graph"
    MODEL_INFERENCE = "model_inference"
    PAID_INFERENCE = "paid_inference"
    DESTRUCTIVE = "destructive"


class CostClass(str, Enum):
    """Rough cost of one call, for admission and budgeting."""

    FREE = "free"
    CHEAP = "cheap"
    MODERATE = "moderate"
    EXPENSIVE = "expensive"


class ToolSpec(BaseModel):
    """Registry entry for one agent-callable tool.

    Attributes:
        name: Stable canonical name (for example ``"lit_search_papers"``).
        version: Semantic version of this tool's contract.
        domain: Owning domain (for example ``"literature"``, ``"graph"``).
        description: What the tool does, for the model.
        when_to_use: Conditions under which the tool is appropriate.
        when_not_to_use: Conditions under which it must not be chosen.
        input_schema: JSON Schema of the arguments.
        output_schema: JSON Schema of ``ToolResult.data``.
        effects: Effect set; empty means pure read.
        cost_class: Rough per-call cost.
        required_capabilities: Capabilities the request context must hold.
        evidence_contract: What evidence references a successful result carries.
        idempotent: Whether repeating the call with the same input is safe.
        aliases: Legacy names accepted at compatibility boundaries only.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    domain: str
    description: str
    when_to_use: str
    when_not_to_use: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    effects: FrozenSet[Effect] = Field(default_factory=frozenset)
    cost_class: CostClass = CostClass.FREE
    required_capabilities: FrozenSet[str] = Field(default_factory=frozenset)
    evidence_contract: str = "none"
    idempotent: bool = True
    aliases: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _write_tools_declare_capability(self) -> "ToolSpec":
        """Tools with write, destructive or paid effects must require a capability."""
        guarded = {Effect.WRITE_GRAPH, Effect.DESTRUCTIVE, Effect.PAID_INFERENCE}
        if self.effects & guarded and not self.required_capabilities:
            raise ValueError("tools with graph-write, destructive or paid effects must require a capability")
        if Effect.DESTRUCTIVE in self.effects and self.idempotent:
            raise ValueError("destructive tools must not be declared idempotent")
        return self


class ToolStatus(str, Enum):
    """Outcome of a tool call. ``EMPTY`` is a successful call with no results;
    it is never used for an unavailable dependency."""

    OK = "ok"
    EMPTY = "empty"
    PARTIAL = "partial"
    AWAITING_REVIEW = "awaiting_review"
    DENIED = "denied"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


_STATUS_ERROR = {
    ToolStatus.DENIED: ErrorCode.DENIED,
    ToolStatus.INVALID: ErrorCode.INVALID_INPUT,
    ToolStatus.UNAVAILABLE: ErrorCode.UNAVAILABLE,
}


class ToolResult(BaseModel):
    """Typed result of one tool call.

    Attributes:
        tool: Canonical tool name.
        status: Outcome; distinguishes empty, unavailable, denied, invalid,
            partial and awaiting-review.
        data: Typed payload matching the tool's ``output_schema``.
        artifact_refs: References to durable artifacts instead of large payloads.
        evidence: Evidence references supporting ``data``.
        warnings: Non-fatal issues the caller should see.
        error_code: Required for denied/invalid/unavailable/error statuses.
        retryable: Whether the same call may succeed later.
        provenance_id: Execution-provenance record for this call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str
    status: ToolStatus
    data: Optional[Dict[str, Any]] = None
    artifact_refs: Tuple[str, ...] = ()
    evidence: Tuple[EvidenceRef, ...] = ()
    warnings: Tuple[str, ...] = ()
    error_code: Optional[ErrorCode] = None
    retryable: bool = False
    provenance_id: Optional[str] = None

    @model_validator(mode="after")
    def _status_consistency(self) -> "ToolResult":
        """Failure statuses need an error code; success statuses must not carry one."""
        failing = {ToolStatus.DENIED, ToolStatus.INVALID, ToolStatus.UNAVAILABLE, ToolStatus.ERROR}
        if self.status in failing and self.error_code is None:
            raise ValueError(f"status {self.status.value} requires error_code")
        if self.status not in failing and self.error_code is not None:
            raise ValueError(f"status {self.status.value} must not carry error_code")
        expected = _STATUS_ERROR.get(self.status)
        if expected is not None and self.error_code is not expected:
            raise ValueError(f"status {self.status.value} requires error_code {expected.value}")
        if self.status is ToolStatus.EMPTY and self.data:
            raise ValueError("empty results carry no data")
        return self

    @property
    def evidence_ids(self) -> List[str]:
        """Return the evidence IDs in order, for parity comparisons."""
        return [e.evidence_id for e in self.evidence]

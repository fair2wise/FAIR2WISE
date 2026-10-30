"""Graph state and run context for the literature supervisor (task T02, R-04).

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/graph.py ``AgentState`` (BSD-3-Clause).
Copyright (c) 2026, The Regents of the University of California, through Lawrence
Berkeley National Laboratory. See the FORGE LICENSE for the full notice.

The checkpointed state holds conversation messages plus approval bookkeeping. It
never holds authority: the :class:`RunContext` (server-built ``RequestContext``)
is passed per invocation as LangGraph runtime context (``graph.invoke(...,
context=...)``) and is not written to checkpoints, so a resume is authorized by
the context of the resuming call, not by stored state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Mapping, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, NotRequired, TypedDict

from ..contracts import ContractError, ErrorCode, RequestContext


def merge_open_approvals(
    left: Optional[Mapping[str, Any]], right: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Reducer for ``open_approvals``: merge by tool-call id; a ``None`` value closes that entry (T02B)."""
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


class LiteratureState(TypedDict):
    """State threaded through the supervisor graph.

    Attributes:
        messages: Conversation with ``add_messages`` semantics (append, or replace by id).
        owner: Opaque owner key of the thread (profile/issuer/subject/session), set on
            first use; a different owner is refused.
        pending_approvals: Unused since T02B (kept so T02 checkpoints still load).
        open_approvals: Approval requests still awaiting a decision, keyed by tool-call id
            (T02B). Each pins the tool, arguments and exact plan, and carries an expiry. They
            survive new turns on the thread until approved, declined or expired.
        approvals: Decisions taken on resume, keyed by tool-call id (an ``ApprovalRecord``
            JSON dict, or ``None`` for a decline), consumed by the apply step.
    """

    messages: Annotated[List[BaseMessage], add_messages]
    owner: NotRequired[str]
    pending_approvals: NotRequired[List[Dict[str, Any]]]
    open_approvals: NotRequired[Annotated[Dict[str, Dict[str, Any]], merge_open_approvals]]
    approvals: NotRequired[Dict[str, Optional[Dict[str, Any]]]]


@dataclass(frozen=True)
class RunContext:
    """Per-invocation runtime context.

    Attributes:
        request: The server-built request context.
        scope: Optional per-turn capability narrowing (never widening).
        tools: Optional per-turn tool narrowing (never widening): only these registered tool
            names are offered to the model and listed in the skills section. ``None`` offers
            every tool the context may use. The legacy UI QA turn pins ``answer_from_knowledge``.
    """

    request: RequestContext
    scope: Optional[FrozenSet[str]] = None
    tools: Optional[FrozenSet[str]] = None


def as_run_context(value: Any) -> RunContext:
    """Accept a :class:`RunContext` or a bare ``RequestContext``.

    Raises:
        ContractError: ``DENIED`` when no server-built context was supplied.
    """
    if isinstance(value, RunContext):
        return value
    if isinstance(value, RequestContext):
        return RunContext(request=value)
    raise ContractError(ErrorCode.DENIED, "a server-built RequestContext must be passed as context=")

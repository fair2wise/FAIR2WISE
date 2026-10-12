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
from typing import Any, Dict, FrozenSet, List, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, NotRequired, TypedDict

from app.modules.contracts import ContractError, ErrorCode, RequestContext


class LiteratureState(TypedDict):
    """State threaded through the supervisor graph.

    Attributes:
        messages: Conversation with ``add_messages`` semantics (append, or replace by id).
        owner: Opaque owner key of the thread (profile/issuer/subject/session), set on
            first use; a different owner is refused.
        pending_approvals: Approval requests raised by the current step (plans pinned).
        approvals: Approval records supplied on resume, keyed by tool-call id.
    """

    messages: Annotated[List[BaseMessage], add_messages]
    owner: NotRequired[str]
    pending_approvals: NotRequired[List[Dict[str, Any]]]
    approvals: NotRequired[Dict[str, Dict[str, Any]]]


@dataclass(frozen=True)
class RunContext:
    """Per-invocation runtime context.

    Attributes:
        request: The server-built request context.
        scope: Optional per-turn capability narrowing (never widening).
    """

    request: RequestContext
    scope: Optional[FrozenSet[str]] = None


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

"""Shared fakes for the T02 runtime tests. No network, no provider, no env reads."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.modules.contracts import (
    ApprovalRecord,
    AuthorizationMode,
    ChangePlan,
    CostClass,
    DeploymentProfile,
    Effect,
    Principal,
    RequestContext,
    ToolResult,
    ToolSpec,
    ToolStatus,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
H = "a" * 64
PLAN_SHA = "b" * 64
OBJ = {"type": "object"}


def local_ctx(**kw: Any) -> RequestContext:
    base: Dict[str, Any] = dict(profile=DeploymentProfile.LOCAL, mode=AuthorizationMode.LOCAL_TRUSTED,
                                principal=Principal(subject="local-operator"), request_id="req-local",
                                capabilities=frozenset({"chat.ask", "graph.edit", "graph.publish"}))
    base.update(kw)
    return RequestContext(**base)


def als_ctx(subject: str = "alice", *, admin: bool = False, **kw: Any) -> RequestContext:
    caps = {"chat.ask", "chat.read_support"} | ({"graph.edit", "graph.publish"} if admin else set())
    base: Dict[str, Any] = dict(
        profile=DeploymentProfile.ALS_DEV, mode=AuthorizationMode.TILED_AUTHENTICATED,
        principal=Principal(subject=subject, issuer="https://tiled-staging.als.lbl.gov/", is_admin=admin,
                            verified_at=NOW),
        request_id=f"req-{subject}", capabilities=frozenset(caps))
    base.update(kw)
    return RequestContext(**base)


def make_plan(**kw: Any) -> ChangePlan:
    base: Dict[str, Any] = dict(plan_id="p1", snapshot_id="s1", input_sha256=H, target="tiled-local",
                                corpus_scope=("rsoxs",), base_revision="rev0", creates=2, links=1, supersessions=0,
                                affected_ids=("e1", "e2"), validation_report_sha256=H, plan_sha256=PLAN_SHA)
    base.update(kw)
    return ChangePlan(**base)


def local_approval(**kw: Any) -> ApprovalRecord:
    base: Dict[str, Any] = dict(approval_id="a1", profile=DeploymentProfile.LOCAL,
                                mode=AuthorizationMode.LOCAL_TRUSTED, plan_id="p1", plan_sha256=PLAN_SHA,
                                target="tiled-local", scope=("rsoxs",), requested_by="local-operator",
                                approved_by="local-operator", executed_as="f2w-writer", approved_at=NOW,
                                expires_at=NOW + timedelta(days=1))
    base.update(kw)
    return ApprovalRecord(**base)


def read_spec(name: str = "lit_echo", **kw: Any) -> ToolSpec:
    base: Dict[str, Any] = dict(
        name=name, version="1.0.0", domain="literature", description="echo", when_to_use="tests",
        when_not_to_use="never", input_schema={"type": "object", "properties": {"q": {"type": "string"}},
                                               "required": ["q"], "additionalProperties": False},
        output_schema=OBJ, effects=frozenset({Effect.READ_CORPUS}), required_capabilities=frozenset({"chat.ask"}))
    base.update(kw)
    return ToolSpec(**base)


def write_spec(name: str = "kg_apply_plan", **kw: Any) -> ToolSpec:
    base: Dict[str, Any] = dict(
        name=name, version="1.0.0", domain="graph", description="apply an approved plan", when_to_use="tests",
        when_not_to_use="never",
        input_schema={"type": "object", "properties": {"plan_id": {"type": "string"}}, "required": ["plan_id"],
                      "additionalProperties": False},
        output_schema=OBJ, effects=frozenset({Effect.WRITE_GRAPH}), cost_class=CostClass.MODERATE,
        required_capabilities=frozenset({"graph.publish"}), idempotent=False)
    base.update(kw)
    return ToolSpec(**base)


class Recorder:
    """Records tool invocations so tests can prove an effect did or did not happen."""

    def __init__(self) -> None:
        self.calls: List[Any] = []

    def ok(self, inv: Any) -> ToolResult:
        self.calls.append(inv)
        return ToolResult(tool=inv.spec.name, status=ToolStatus.OK, data={"echo": dict(inv.args)})


class FakeClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class CountingAdmission:
    """Fake admission: counts sync/async slot entries per provider."""

    def __init__(self) -> None:
        self.sync: Dict[str, int] = {}
        self.aio: Dict[str, int] = {}
        self.held = 0

    @contextmanager
    def slot(self, provider: str):
        self.sync[provider] = self.sync.get(provider, 0) + 1
        self.held += 1
        try:
            yield
        finally:
            self.held -= 1

    @asynccontextmanager
    async def aslot(self, provider: str):
        self.aio[provider] = self.aio.get(provider, 0) + 1
        self.held += 1
        try:
            yield
        finally:
            self.held -= 1


Step = Callable[[Sequence[BaseMessage]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    """Deterministic tool-calling chat model. ``script`` items are AIMessages or callables."""

    script: List[Any] = Field(default_factory=list)
    calls: List[List[BaseMessage]] = Field(default_factory=list)
    bound: List[List[str]] = Field(default_factory=list)
    default: str = "final answer"

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools: Sequence[Any], **kw: Any) -> "ScriptedChatModel":
        self.bound.append([t["function"]["name"] for t in tools])
        return self

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None,
                  **kw: Any) -> ChatResult:
        self.calls.append(list(messages))
        item = self.script.pop(0) if self.script else AIMessage(content=self.default)
        message = item(messages) if callable(item) else item
        return ChatResult(generations=[ChatGeneration(message=message)])


def tool_call(name: str, args: Dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])

"""Literature-scoped LangGraph runtime (task T02).

Importing this package has no side effects: it reads no environment or files,
constructs no model or provider, registers no tools and imports neither LangGraph
nor any provider SDK. Public names are resolved lazily on first attribute access;
everything is built explicitly by the application (see
``app.modules.tools.bootstrap.build_registry`` and :func:`build_literature_supervisor`).
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "Admission": ("admission", "Admission"),
    "NoOpAdmission": ("admission", "NoOpAdmission"),
    "RegisteredTool": ("registry", "RegisteredTool"),
    "ToolRegistry": ("registry", "ToolRegistry"),
    "ToolRegistryError": ("registry", "ToolRegistryError"),
    "Skill": ("skills", "Skill"),
    "SkillRegistry": ("skills", "SkillRegistry"),
    "SkillRegistryError": ("skills", "SkillRegistryError"),
    "RuntimeLimits": ("executor", "RuntimeLimits"),
    "ToolExecutor": ("executor", "ToolExecutor"),
    "ToolInvocation": ("executor", "ToolInvocation"),
    "ToolOutcome": ("executor", "ToolOutcome"),
    "TurnLedger": ("executor", "TurnLedger"),
    "AuditEvent": ("policies", "AuditEvent"),
    "AuditKind": ("policies", "AuditKind"),
    "ApprovalDecision": ("policies", "ApprovalDecision"),
    "PlanApprovalCheck": ("policies", "PlanApprovalCheck"),
    "InMemoryEventSink": ("policies", "InMemoryEventSink"),
    "JsonlEventSink": ("policies", "JsonlEventSink"),
    "CapabilityGroup": ("profiles", "CapabilityGroup"),
    "CheckpointerConfig": ("checkpointer", "CheckpointerConfig"),
    "ManagedCheckpointer": ("checkpointer", "ManagedCheckpointer"),
    "scoped_thread_id": ("checkpointer", "scoped_thread_id"),
    "thread_config": ("checkpointer", "thread_config"),
    "LiteratureState": ("state", "LiteratureState"),
    "RunContext": ("state", "RunContext"),
    "PLACEHOLDER_SYSTEM_PROMPT": ("supervisor", "PLACEHOLDER_SYSTEM_PROMPT"),
    "build_literature_supervisor": ("supervisor", "build_literature_supervisor"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:  # noqa: ANN401  # Any: PEP 562 lazy-export hook; the attribute type depends on the name
    try:
        module, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(f"{__name__}.{module}"), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

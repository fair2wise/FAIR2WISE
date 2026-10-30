"""Bridge from the existing F2W read/QA path to the literature runtime (task T03).

This package wraps the working F2W retrieval/QA path
(``f2w_agent.retrieval_agent.RetrievalAgent.query``: KG and multi-KG retrieval,
optional source-document RAG, the evidence gate, the ``JUDGE_SYSTEM`` judge,
contextual leeway and the existing citation tokens) as one read-only Tool and
one task Skill, without changing its behavior.

Importing this package has no side effects: it reads no environment or files,
constructs no model, agent or registry, and imports neither LangGraph, the
retrieval agent nor any provider SDK. Public names resolve lazily; everything
is built explicitly by :class:`app.modules.application.F2WApplication` or by
:func:`selection.attach_read_path` at service startup.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "RUNTIME_ENV": ("mode", "RUNTIME_ENV"),
    "RuntimeConfigError": ("mode", "RuntimeConfigError"),
    "RuntimeMode": ("mode", "RuntimeMode"),
    "runtime_mode": ("mode", "runtime_mode"),
    "QA_ROUTE_ENV": ("qa_route", "QA_ROUTE_ENV"),
    "QaRoute": ("qa_route", "QaRoute"),
    "qa_route": ("qa_route", "qa_route"),
    "LegacyRetrievalService": ("retrieval", "LegacyRetrievalService"),
    "RetrievalSettings": ("retrieval", "RetrievalSettings"),
    "build_retrieval_agent": ("retrieval", "build_retrieval_agent"),
    "ANSWER_FROM_KNOWLEDGE": ("qa_tool", "ANSWER_FROM_KNOWLEDGE"),
    "LegacyResultLedger": ("qa_tool", "LegacyResultLedger"),
    "ReadOutcome": ("qa_tool", "ReadOutcome"),
    "make_answer_tool": ("qa_tool", "make_answer_tool"),
    "verdict_to_result": ("qa_tool", "verdict_to_result"),
    "ANSWER_SKILL": ("bootstrap", "ANSWER_SKILL"),
    "QA_DELEGATION_PROMPT": ("bootstrap", "QA_DELEGATION_PROMPT"),
    "build_read_registry": ("bootstrap", "build_read_registry"),
    "build_read_skills": ("bootstrap", "build_read_skills"),
    "LegacyReadPath": ("legacy_adapter", "LegacyReadPath"),
    "build_legacy_read_path": ("legacy_adapter", "build_legacy_read_path"),
    "attach_read_path": ("selection", "attach_read_path"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(f"{__name__}.{module}"), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

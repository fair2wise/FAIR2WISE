"""Deprecated home of the workflow orchestrator; the logic lives in ``agent_runtime.routing`` (T17B P2).

The deterministic routing, validation and the LangGraph decision graph moved to
:mod:`app.modules.agent_runtime.routing`; the classifier call goes through
:class:`app.modules.agent_runtime.llm_gate.AdmittedChat` (admission, curation budget, deadline). This module
keeps the legacy import path and the legacy wiring (ops-layout predicate, turn trace, chat-client factory,
provider admission, deadline helper) so ``api.py`` and the retained contract tests are unchanged. It no longer
subclasses the Academy ``Agent``: ``decide`` is a plain coroutine (nothing launches these through Academy).
New code imports ``agent_runtime.routing``; this file is removed with the other H8 shims.
"""
from __future__ import annotations

from collections.abc import Iterable
import os
from typing import Any

from ..agent_runtime.llm_gate import BudgetPort
from ..agent_runtime.routing import (
    ACTION_AGENTS,
    ACTIONS,
    DEFAULT_MAX_STEPS,
    FRESH_TURN_CLASSES,
    RoutingHooks,
    WorkflowRouter,
    _decision,
    _direct_download_request,
    _extracted_terms_followup,
    _paper_reference_followup,
    _parse_json_object,
    classify_turn,
    fresh_turn_decision,
    validate_decision,
)
from .admitted_chat import legacy_admitted_chat
from .retrieval_agent import _is_ops_layout_question
from .trace import bind_context, note_error, note_precheck

__all__ = (
    "ACTIONS", "ACTION_AGENTS", "DEFAULT_MAX_STEPS", "FRESH_TURN_CLASSES", "WorkflowOrchestratorAgent",
    "_decision", "_direct_download_request", "_extracted_terms_followup", "_paper_reference_followup",
    "_parse_json_object",
)

HOOKS = RoutingHooks(
    is_ops_layout=_is_ops_layout_question,
    note_precheck=note_precheck,
    note_error=note_error,
    bind_context=bind_context,
)


class WorkflowOrchestratorAgent:
    """Select one safe pipeline action from structured workflow state (legacy facade over ``WorkflowRouter``)."""

    def __init__(
        self,
        *,
        backend: str | None = None,
        model: str | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        budget: BudgetPort | None = None,
    ) -> None:
        """Store chat backend/model, the step budget and the optional curation budget port."""
        self._backend = backend or os.environ.get("KG_RAG_BACKEND", "cborg")
        self._model = model
        self.budget = budget
        self.max_steps = max(1, int(max_steps or DEFAULT_MAX_STEPS))
        self._router = WorkflowRouter(
            classifier=lambda turn, state: self._llm_classify(turn, state),  # looked up per call (tests replace it)
            hooks=HOOKS,
            max_steps=self.max_steps,
        )

    def _safe_fallback(self, user_turn: str, state: dict[str, Any], route_hint: str | None) -> dict[str, Any]:
        """Choose the next action from approvals, phase, and cheap turn heuristics."""
        return self._router.fallback(user_turn, state, route_hint)

    def _llm_classify(self, user_turn: str, state: dict[str, Any]) -> dict[str, Any]:
        """Ask the chat model (admitted, budgeted, deadline-bounded) to classify a fresh turn."""
        model = self._model or os.environ.get("KG_RAG_CBORG_MODEL", "lbl/cborg-chat")
        return classify_turn(legacy_admitted_chat(self._backend, model, self.budget), user_turn, state)

    @staticmethod
    def _fresh_turn_decision(classification: dict[str, Any]) -> dict[str, Any] | None:
        """Map a fresh-turn classification to retrieve_kg or direct_response."""
        return fresh_turn_decision(classification)

    def validate(
        self,
        proposed: dict[str, Any],
        state: dict[str, Any],
        *,
        available_agents: Iterable[str] | None = None,
    ) -> dict[str, Any] | None:
        """Accept a proposed action only if it matches phase, approvals, and agents."""
        return validate_decision(proposed, state, available_agents=available_agents)

    async def decide(
        self,
        user_turn: str,
        state: dict[str, Any],
        route_hint: str | None = None,
        available_agents: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a validated strict-JSON-shaped next action."""
        return await self._router.decide(user_turn, state, route_hint, available_agents)

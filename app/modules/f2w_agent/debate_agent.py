"""Deprecated home of the evidence-debate agent; the logic lives in ``curation.evidence`` (T17B P2).

Scoring, prompt, guard rails, the heuristic fallback and the LangGraph decision graph moved to
:mod:`app.modules.curation.evidence`; the model call goes through
:class:`app.modules.agent_runtime.llm_gate.AdmittedChat` (admission, curation budget, deadline). This module
keeps the legacy import path and the legacy wiring (turn trace, chat-client factory) so ``api.py``,
``coordinator.py`` and the retained tests are unchanged. It no longer subclasses the Academy ``Agent``:
``decide`` is a plain coroutine. New code imports ``curation.evidence``; this file is removed with the other H8
shims.
"""
from __future__ import annotations

import os
from typing import Any

from ..agent_runtime.llm_gate import BudgetPort
from ..curation.evidence import (
    ACTIONS,
    MAX_PROMPT_CANDIDATES,
    MIN_FALLBACK_SCORE,
    DebateHooks,
    EvidenceDebater,
    _candidate_titles,
    _normalize_objections,
    _parse_json_object,
    _score,
    _selected_indices,
    _summary_from_decision,
    ask_debate,
    fallback_decide,
    resolve_decision,
)
from ..model_policy import helper_model_calls_enabled
from .admitted_chat import legacy_admitted_chat
from .trace import bind_context, note_error

__all__ = (
    "ACTIONS", "MAX_PROMPT_CANDIDATES", "MIN_FALLBACK_SCORE", "EvidenceDebateAgent", "_candidate_titles",
    "_normalize_objections", "_parse_json_object", "_score", "_selected_indices", "_summary_from_decision",
)

HOOKS = DebateHooks(note_error=note_error, bind_context=bind_context)


class EvidenceDebateAgent:
    """Gates answer/search/download/extraction actions (legacy facade over ``EvidenceDebater``)."""

    def __init__(
        self,
        *,
        backend: str | None = None,
        model: str | None = None,
        budget: BudgetPort | None = None,
    ) -> None:
        """Store chat backend/model used for LLM debate decisions and the optional curation budget port."""
        self._backend = backend or os.environ.get("KG_RAG_BACKEND", "cborg")
        self._model = model
        self.budget = budget
        self._debater = EvidenceDebater(
            asker=lambda q, probe, cands, rnd: self._ask(q, probe, cands, rnd),  # looked up per call
            hooks=HOOKS,
            model_calls_enabled=helper_model_calls_enabled,  # opt-in (F2W_HELPER_AGENT_LLM), default off
        )

    def _fallback_decide(
        self,
        question: str,
        retrieval_probe: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Heuristic next action when the LLM is unavailable or returns invalid JSON."""
        return fallback_decide(retrieval_probe, candidates)

    def _ask(
        self,
        question: str,
        retrieval_probe: dict[str, Any],
        candidates: list[dict[str, Any]],
        round_no: int,
    ) -> dict[str, Any]:
        """One admitted, budgeted model call; returns the parsed reply."""
        model = self._model or os.environ.get("KG_RAG_CBORG_MODEL", "lbl/cborg-chat")
        chat = legacy_admitted_chat(self._backend, model, self.budget)
        return ask_debate(chat, question, retrieval_probe, candidates, round_no)

    def _llm_decide(
        self,
        question: str,
        retrieval_probe: dict[str, Any],
        candidates: list[dict[str, Any]],
        round_no: int,
    ) -> dict[str, Any]:
        """Ask the chat model for a JSON action, falling back to heuristics."""
        return resolve_decision(self._ask(question, retrieval_probe, candidates, round_no), retrieval_probe, candidates)

    async def decide(
        self,
        question: str,
        retrieval_probe: dict[str, Any],
        candidates: list[dict[str, Any]] | None = None,
        round_no: int = 1,
    ) -> dict[str, Any]:
        """Return summarized debate decision; raw transcript is not exposed."""
        return await self._debater.decide(question, retrieval_probe, candidates, round_no)

"""Legacy wiring of :class:`AdmittedChat` for the orchestrator and debate facades (T17B P2).

Binds the pieces ``agent_runtime`` may not import: the F2W chat-client factory (looked up per call, so tests and
the legacy harness can replace ``term_extractor.clients.make_chat_client``), the shared provider admission and
the bounded ``cborg_http.call_with_deadline``.
"""
from __future__ import annotations

import os
from typing import Any

from ..agent_runtime.llm_gate import AGENT_ADMISSION_KEY, AdmittedChat, BudgetPort


def legacy_admitted_chat(backend: str, model: str, budget: BudgetPort | None) -> AdmittedChat:
    """An :class:`AdmittedChat` over ``term_extractor.clients.make_chat_client`` (looked up per call)."""
    from ..cborg_http import call_with_deadline
    from ..llm_admission import ProviderAdmission
    from ..model_policy import require_free_cborg_model
    from ..term_extractor import clients

    def factory() -> Any:  # noqa: ANN401 - the chat client
        return clients.make_chat_client(
            backend=backend,
            model=model,
            cborg_base=os.environ.get("CBORG_BASE_URL"),
            cborg_api_key=os.environ.get("CBORG_API_KEY"),
        )

    return AdmittedChat(
        client_factory=factory,
        model=model,
        admission=ProviderAdmission(),
        run_with_deadline=call_with_deadline,
        budget=budget,
        local=(backend or "").lower() == "ollama",
        admission_key=AGENT_ADMISSION_KEY,
        # Free-model policy (owner decision 2026-11-09): a paid id is refused before budget, slot or client.
        model_check=lambda m: require_free_cborg_model(m, context="chat helper agent"),
    )

__all__ = ("legacy_admitted_chat",)

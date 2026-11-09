"""Run a routing case through an orchestrator agent with a scripted fake chat client (no network).

Used by the recorder that produced ``golden/routing_transcripts.json`` (against the legacy module) and by
the parity tests (against the ported modules). The fake replaces ``term_extractor.clients.make_chat_client``
and the trace collector's ``_call`` so every model call and precheck note is captured.
"""

from __future__ import annotations

import asyncio
import copy
import os
from typing import Any
from unittest import mock

from app.modules.f2w_agent import trace
from app.modules.term_extractor import clients

from .routing_cases import CASES


class _Notes:
    def __init__(self, sink: list[list[Any]]) -> None:
        self._sink = sink

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - records any collector method call
        def record(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
            self._sink.append([name, *[str(a) if isinstance(a, BaseException) else a for a in args]])

        return record


class FakeChat:
    def __init__(self, script: Any, calls: list[dict[str, Any]]) -> None:  # noqa: ANN401
        self.script, self.calls = script, calls

    def chat(self, prompt: str, **kw: Any) -> str:  # noqa: ANN401
        self.calls.append({"prompt": prompt, "kwargs": dict(kw)})
        if self.script is None:
            raise AssertionError("the model must not be called for this case")
        if isinstance(self.script, dict):
            raise RuntimeError(self.script["raise"])
        return str(self.script)


def run_case(case_id: str, make_agent: Any) -> dict[str, Any]:  # noqa: ANN401 - factory returning an agent with ``decide``
    """Return ``{"decision", "calls", "factory_calls", "notes"}`` for one case."""
    turn, state, hint, available, script = CASES[case_id]
    state = copy.deepcopy(state)
    calls: list[dict[str, Any]] = []
    factory: list[dict[str, Any]] = []
    notes: list[list[Any]] = []

    def make_client(**kw: Any) -> FakeChat:  # noqa: ANN401
        factory.append(kw)
        return FakeChat(script, calls)

    env = {k: v for k, v in os.environ.items() if k not in {"KG_RAG_BACKEND", "KG_RAG_CBORG_MODEL", "CBORG_BASE_URL",
                                                              "CBORG_API_KEY"}}
    with mock.patch.dict(os.environ, env, clear=True), \
            mock.patch.object(clients, "make_chat_client", make_client), \
            mock.patch.object(trace, "_call", lambda fn: fn(_Notes(notes))):
        agent = make_agent()
        decision = asyncio.run(agent.decide(turn, state, hint, available))
    return {"decision": decision, "calls": calls, "factory_calls": factory, "notes": notes}

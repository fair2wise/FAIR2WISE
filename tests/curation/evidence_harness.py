"""Run an evidence-debate case through a debate agent with a scripted fake chat client (no network)."""

from __future__ import annotations

import asyncio
import copy
import os
from typing import Any
from unittest import mock

from app.modules.f2w_agent import trace
from app.modules.term_extractor import clients
from tests.runtime.routing_harness import FakeChat, _Notes

from .evidence_cases import CASES


def run_case(case_id: str, make_agent: Any) -> dict[str, Any]:  # noqa: ANN401 - factory returning an agent with ``decide``
    question, probe, candidates, round_no, script = CASES[case_id]
    probe, candidates = copy.deepcopy(probe), copy.deepcopy(candidates)
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
        decision = asyncio.run(agent.decide(question, probe, candidates, round_no))
    return {"decision": decision, "calls": calls, "factory_calls": factory, "notes": notes}

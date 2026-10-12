"""Shared fakes for the T03 read-path tests: a fixture KG, fake inference, no network.

The real ``RetrievalAgent`` runs over a small JSON KG with lexical retrieval. The
async judge/leeway client is ``contracts.testing.FakeAsyncChatClient``, installed
where ``RetrievalAgent`` looks it up (``retrieval_agent.krag.make_chat_client``),
as the existing retrieval tests do. Materials Project is stubbed and sockets are
blocked, so nothing leaves the process.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

os.environ.setdefault("KG_RAG_RETRIEVAL_BACKEND", "lexical")

from app.modules.contracts.testing import FakeAsyncChatClient  # noqa: E402

GRAPH_ID = "fixture_v1"
GRAPH_NAME = f"matkg_{GRAPH_ID}.json"
OPS_GRAPH_ID = "fixture_ops_v1"

FIXTURE_KG: Dict[str, Any] = {
    "things": [
        {"id": "matkg:RSoXS", "name": "Resonant soft X-ray scattering", "category": "Technique",
         "description": "RSoXS probes chemical contrast and molecular orientation in polymer films.",
         "source_papers": ["paper_a.pdf"]},
        {"id": "matkg:PEDOT", "name": "PEDOT:PSS", "category": "Material",
         "description": "Conducting polymer blend whose morphology is studied with RSoXS.",
         "source_papers": ["paper_b.pdf"]},
        {"id": "matkg:Edge", "name": "Carbon K-edge", "category": "Measurement",
         "description": "Absorption edge used for resonant contrast in organic films."},
        {"id": "matkg:Widget", "name": "Gamma widget", "category": "Thing"},
    ],
    "associations": [
        {"subject": "matkg:RSoXS", "predicate": "rel:studies", "object": "matkg:PEDOT"},
        {"subject": "matkg:RSoXS", "predicate": "rel:uses", "object": "matkg:Edge"},
    ],
}

SECOND_KG: Dict[str, Any] = {
    "things": [
        {"id": "matkg:RSoXS", "name": "Resonant soft X-ray scattering (beamline)", "category": "Technique",
         "description": "Same label in a second graph: identity stays graph-scoped.",
         "source_papers": ["ops_manual.pdf"]},
    ],
    "associations": [],
}

ANSWERED_Q = "What is resonant soft X-ray scattering of PEDOT:PSS?"
INSUFFICIENT_Q = "What is the exact value of the photon energy in eV used for PEDOT:PSS RSoXS?"
NO_EVIDENCE_Q = "zzqx unrelated quokka sandwich"

ANSWER_TEXT = (f"RSoXS probes chemical contrast [KG: {GRAPH_ID}: Resonant soft X-ray scattering] "
               f"in PEDOT:PSS films [KG: {GRAPH_ID}: PEDOT:PSS].")


def judge_reply(question: str) -> str:
    """Deterministic judge output keyed by the question (the policy itself stays F2W's)."""
    if question == INSUFFICIENT_Q:
        return json.dumps({"sufficient": False, "answer": None, "missing_topics": ["photon energy value"]})
    return json.dumps({"sufficient": True, "answer": ANSWER_TEXT, "missing_topics": []})


class QuestionKeyedClient(FakeAsyncChatClient):
    """FakeAsyncChatClient whose reply depends on the question in the user message."""

    def __init__(self, *, fail: Optional[BaseException] = None) -> None:
        super().__init__(model="fake-judge")
        self.fail = fail

    async def chat(self, messages: Sequence[Dict[str, str]]) -> str:
        await super().chat(messages)
        if self.fail is not None:
            raise self.fail
        user = messages[-1]["content"]
        for question in (INSUFFICIENT_Q, ANSWERED_Q):
            if question in user:
                return judge_reply(question)
        return judge_reply("")


class NetworkBlocked(RuntimeError):
    pass


def _blocked(*args: Any, **kwargs: Any) -> None:
    raise NetworkBlocked(f"network blocked in read-path tests: {args!r}")


def write_graph(directory: Path, data: Dict[str, Any] = FIXTURE_KG, name: str = GRAPH_NAME) -> Path:
    """Write a JSON KG fixture and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def install_fake_inference(monkeypatch: Any, client: Optional[FakeAsyncChatClient] = None) -> FakeAsyncChatClient:
    """Route RetrievalAgent's judge/leeway client to ``client``; stub MP; block sockets."""
    from app.modules.f2w_agent import retrieval_agent

    client = client or QuestionKeyedClient()
    for name in ("CBORG_API_KEY", "OPENAI_API_KEY", "MP_API_KEY", "TILED_URI", "TILED_API_KEY", "F2W_RUNTIME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KG_RAG_RETRIEVAL_BACKEND", "lexical")
    monkeypatch.setenv("F2W_SOURCE_RAG", "0")
    monkeypatch.setenv("F2W_LIVE_TILED", "0")
    monkeypatch.setattr(retrieval_agent.krag, "make_chat_client", lambda *a, **k: client)
    no_mp = {"triggered": False, "queries": [], "records": [], "context": "", "skipped": None}
    monkeypatch.setattr(retrieval_agent, "lookup_materials_project", lambda q: dict(no_mp))
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    return client


def local_settings(**kw: Any) -> Any:
    """Validated ``:local`` DeploymentSettings."""
    from app.modules.security.authorization import DeploymentSettings

    base: Dict[str, Any] = dict(profile="local", writer_identity="f2w-writer")
    base.update(kw)
    return DeploymentSettings(**base)


def retrieval_service(graph_files: Sequence[Path]) -> Any:
    """A LegacyRetrievalService over a real RetrievalAgent for ``graph_files``."""
    from app.modules.runtime_bridge.retrieval import LegacyRetrievalService, RetrievalSettings

    return LegacyRetrievalService(settings=RetrievalSettings(
        graph_files=tuple(str(p) for p in graph_files), graph_source="json", backend="cborg", model="fake-judge"))


class SpyAgent:
    """Wraps a retrieval agent and records each query call (and the wrapper identity)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.questions: List[str] = []

    def set_live_tiled(self, enabled: bool, *, uri: Optional[str] = None) -> None:
        self.inner.set_live_tiled(enabled, uri=uri)

    async def query(self, question: str, history: Any = None, source_rag: Any = None) -> Any:
        self.questions.append(question)
        return await self.inner.query(question, history=history, source_rag=source_rag)


def ask_tool_then_relay(question: str, call_id: str = "c1") -> List[Any]:
    """Supervisor script: call answer_from_knowledge with ``question``, then relay its answer."""
    from langchain_core.messages import AIMessage

    from tests.runtime.runtime_fakes import tool_call

    def relay(messages: Sequence[Any]) -> AIMessage:
        body = json.loads(messages[-1].content)
        data = body.get("data") or {}
        return AIMessage(content=data.get("answer") or f"[{body['status']}] no grounded answer")

    return [tool_call("answer_from_knowledge", {"question": question}, call_id), relay]

"""Supporting passages survive the REAL tool-calling extraction path (pilot 2026-11-24).

The real ``Orchestrator`` with the real LangGraph agent/tool loop (``build_graph``, ``ToolNode``, ``build_tools``) and a
scripted chat model: the page is processed on a worker thread as ``process_pdf`` does, the tools run on the tool node's
own pool threads, and every term the model registered with a context must have its passage in ``terms.json``. With the
thread-local page context this test fails (page ``None``: no passage, no page).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage
import pytest

from app.modules.term_extractor import orchestrator as orch_mod
from app.modules.term_extractor.orchestrator import Orchestrator
from app.modules.term_extractor.services import Services
from tests.runtime.runtime_fakes import ScriptedChatModel

PAGE = ("Cloud tomography reconstructs the three-dimensional structure of clouds from many camera views. "
        "Ray tracing follows each photon through the voxel grid of the scene to render a synthetic image. "
        "A voxel grid divides the reconstruction volume into small cubes that hold the retrieved extinction. "
        "The method was evaluated on the field campaign data described below and on simulated scenes.")


def call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    """One tool call of an ``AIMessage`` (the dict form LangGraph's ToolNode executes)."""
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class ToolAwareScript(ScriptedChatModel):
    """The scripted model of the runtime tests, binding LangChain ``BaseTool`` objects (the orchestrator passes those)."""

    def bind_tools(self, tools: Any, **kw: Any) -> "ToolAwareScript":  # noqa: ANN401 - BaseTool objects or dicts
        self.bound.append([getattr(t, "name", None) or t["function"]["name"] for t in tools])
        return self


class FakeMergeIndex:
    def __init__(self, model_name: str, top_k: int) -> None:
        self.model_name, self.top_k = model_name, top_k

    def ensure(self, names: Any) -> None:
        return None

    def query(self, term: str) -> list[str]:
        return []

    def add(self, name: str) -> None:
        return None


@pytest.fixture
def real_orchestrator(monkeypatch, tmp_path):
    script = [
        AIMessage(content="", tool_calls=[
            call("register_term", {"term": "cloud tomography", "definition": "3D reconstruction of clouds.",
                                        "category": "Method",
                                        "context": "Cloud tomography reconstructs the three-dimensional structure of clouds "
                                                   "from many camera views."}, "c1"),
            call("register_term", {"term": "ray tracing", "definition": "Following photons through a scene.",
                                        "category": "Method",
                                        "relations": [{"relation": "uses", "related_term": "voxel grid"}],
                                        "context": "Ray tracing follows each photon through the voxel grid of the scene to "
                                                   "render a synthetic image."}, "c2"),
            call("register_term", {"term": "field campaign", "definition": "Observations in the field.",
                                        "category": "Thing"}, "c3"),          # no context, and the page names no sentence for it
            call("register_term", {"term": "voxel grid", "definition": "Grid of small cubes.",
                                        "category": "Thing"}, "c4"),          # no context: located in the page (two sentences)
        ]),
        AIMessage(content="done"),
    ]
    model = ToolAwareScript(script=script)
    monkeypatch.setattr(orch_mod, "TermMergeIndex", FakeMergeIndex)
    monkeypatch.setattr(orch_mod, "build_services", lambda chebi_obo_path: Services(formula_checker=None, chebi_lookup=None))  # type: ignore[arg-type]
    monkeypatch.setattr(orch_mod, "make_chat_client", lambda **kw: SimpleNamespace(kw=kw))
    monkeypatch.setattr(Orchestrator, "_build_llm", lambda self: model)
    orch = Orchestrator("scripted", str(tmp_path / "terms.json"), backend="ollama", chebi_obo_path=None,
                        schema_path="storage/schema/matkg_schema.yaml")
    return orch, model, tmp_path / "terms.json"


def test_every_term_registered_with_a_context_has_its_passage_in_terms_json(real_orchestrator):
    orch, model, terms_path = real_orchestrator
    tool_threads: set[int] = set()
    state = orch._tool_state
    original = state.page_context

    def spying_page_context():
        tool_threads.add(threading.get_ident())
        return original()

    state.page_context = spying_page_context          # the tools call state.page_context() at call time
    with ThreadPoolExecutor(max_workers=1) as pool:   # the page thread, as process_pdf runs pages
        page_thread, added = pool.submit(lambda: (threading.get_ident(), orch.process_page(PAGE, "clouds.pdf", 0))).result()
    assert added is True and len(model.calls) == 2 and model.bound and "register_term" in model.bound[0]
    assert tool_threads and page_thread not in tool_threads      # the tools really ran on other threads
    orch.store.save()
    terms = {t["term"]: t for t in json.loads(terms_path.read_text(encoding="utf-8"))["terms"]}
    assert set(terms) == {"cloud tomography", "ray tracing", "field campaign", "voxel grid"}
    for name in ("cloud tomography", "ray tracing"):
        record = terms[name]
        assert record["pages"] == [1] and record["source_papers"] == ["clouds.pdf"]
        assert len(record["context_snippets"]) == 1
        snippet = record["context_snippets"][0]
        assert snippet["source_paper"] == "clouds.pdf" and snippet["page"] == 1 and snippet["text"].startswith(name.split()[0].capitalize())
        assert snippet["verified"] is True and "kind" not in snippet       # quoted is the default kind: not written
    assert terms["field campaign"]["context_snippets"] == [] and terms["field campaign"]["pages"] == [1]
    (located,) = terms["voxel grid"]["context_snippets"]
    assert located["kind"] == "located" and located["verified"] is True and located["page"] == 1
    assert located["text"].startswith("A voxel grid divides") and located["text"].endswith("below and on simulated scenes.")
    # Coverage counters (design d): this run, per paper, and persisted in the terms file's metadata.
    expected = {"terms": 4, "with_passage": 3, "quoted": 2, "located": 1, "rejected": 0, "uncovered": 1, "relations": 1,
                "relations_with_evidence": 1, "evidence_sentences": 1, "relations_without": 0}
    assert orch.passage_summary() == {"total": expected, "papers": {"clouds.pdf": expected}}
    assert json.loads(terms_path.read_text(encoding="utf-8"))["metadata"]["passages"] == {"clouds.pdf": expected}
    assert terms["ray tracing"]["relations"][0]["related_term"] == "voxel grid"
    (evidence,) = terms["ray tracing"]["relations"][0]["evidence"]       # located: a sentence naming both ends (no model)
    assert evidence["kind"] == "located" and evidence["verified"] is True and evidence["page"] == 1
    assert evidence["text"].startswith("Ray tracing follows each photon through the voxel grid")

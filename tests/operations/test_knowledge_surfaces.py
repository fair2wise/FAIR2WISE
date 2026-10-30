"""T19E: the five T19C operations as agent Tools (registry, skills, supervisor parity, selection, D19).

Parity (as the T03 fixtures): the same question gives the same bounded tool result through
the direct executor call, the shared ``KnowledgeOperations.result`` (what ``/v1``, the SDK and
the CLI return) and the LangGraph supervisor route with a scripted planner. No model is
called: the planner is ``ScriptedChatModel`` and the operations compute with code.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from langchain_core.messages import AIMessage

from app.modules.agent_runtime.executor import RuntimeLimits, ToolExecutor
from app.modules.agent_runtime.policies import InMemoryEventSink
from app.modules.application import F2WApplication
from app.modules.contracts import ContractError, ErrorCode, ToolStatus
from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.operations.corpus import TOOL_AUDIT, CorpusAuditTool, mapping_corpus_resolver
from app.modules.operations.graph import TOOL_COMPARE, TOOL_RECENT, TOOL_STATS, TOOL_SUBGRAPH, GraphOperationsTool
from app.modules.operations.knowledge import DynamicJsonGraphs, KnowledgeOperations, directory_corpus_resolver
from app.modules.runtime_bridge.bootstrap import build_read_registry, build_read_skills
from app.modules.runtime_bridge.qa_tool import LegacyResultLedger
from app.modules.runtime_bridge.retrieval import LegacyRetrievalService
from tests.read_path.read_path_fakes import local_settings
from tests.read_path.test_read_tool import SUCCESS, StubAgent
from tests.runtime.runtime_fakes import ScriptedChatModel, local_ctx, tool_call

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "graph"))

from graph_samples import things_payload, write  # noqa: E402

CANARY = "SYNTHETIC-DESCRIPTION-CANARY-5521"
SNIPPET = "SYNTHETIC-SNIPPET-CANARY-1187"
OPERATIONS = (TOOL_STATS, TOOL_COMPARE, TOOL_SUBGRAPH, TOOL_RECENT, TOOL_AUDIT)
QUESTIONS = {  # question -> the tool call a planner makes for it
    "how many distinct papers are in this graph?": (TOOL_STATS, {"graph_id": "syn_v2"}),
    "what changed between v1 and v2?": (TOOL_COMPARE, {"graph_ids": ["syn_v1", "syn_v2"]}),
    "what does the graph hold about paper_a.pdf?": (TOOL_SUBGRAPH, {"graph_id": "syn_v2", "paper": "paper_a.pdf"}),
    "how many works does the corpus hold?": (TOOL_AUDIT, {"corpus": "syn"}),
}


def _payload() -> Dict[str, Any]:
    data = things_payload()
    data["things"][0]["description"] = CANARY
    data["things"].append({"id": "matkg:Film", "name": "Film", "category": "Material",
                           "source_papers": ["/abs/dir/paper_a.pdf", "10.1021_x.1.pdf"],
                           "context_snippets": [{"text": SNIPPET}]})
    data["associations"].append({"subject": "matkg:Film", "predicate": "rel:PART_OF", "object": "matkg:P3HT"})
    return data


def _terms() -> Dict[str, Any]:
    def term(name: str, papers: List[str]) -> Dict[str, Any]:
        return {"term": name, "category": "Material", "definition": f"{CANARY} {name}", "pages": [1],
                "source_papers": papers,
                "context_snippets": [{"text": SNIPPET, "source_paper": f"/abs/dir/{p}", "page": 1}
                                     for p in papers],
                "relations": []}

    return {"terms": [term("P3HT", ["paper_a.pdf"]), term("PCBM", ["paper_a.pdf", "paper_b.pdf"])],
            "metadata": {"paper_a.pdf": {"title": "Paper A", "year": 2021}}}


@pytest.fixture
def files(tmp_path):
    return {"syn_v2": write(tmp_path / "syn_v2.json", _payload()),
            "syn_v1": write(tmp_path / "syn_v1.json", things_payload()),
            "terms": write(tmp_path / "corpus" / "syn.json", _terms())}


def _knowledge(files, **kw) -> KnowledgeOperations:
    graph = GraphOperationsTool(JsonGraphBackend({"syn_v2": files["syn_v2"], "syn_v1": files["syn_v1"]}), **kw)
    return KnowledgeOperations(graph, CorpusAuditTool(mapping_corpus_resolver({"syn": str(files["terms"])})))


def _service():
    return LegacyRetrievalService(StubAgent(SUCCESS))


def _app(knowledge, script=()) -> F2WApplication:
    model = ScriptedChatModel(script=list(script)) if script else None
    return F2WApplication(settings=local_settings(), retrieval=_service(), chat_model=model, knowledge=knowledge)


def _relay(messages) -> AIMessage:
    body = json.loads(messages[-1].content)
    data = body.get("data") or {}
    return AIMessage(content=json.dumps({"status": body["status"], "metrics": data.get("metrics")}))


def _comparable(result: Dict[str, Any]) -> Dict[str, Any]:
    """A tool result minus what differs per call (provenance id and wall-clock stamps)."""
    out = dict(result)
    out.pop("provenance_id", None)
    data = dict(out.get("data") or {})
    for key in ("started_at", "finished_at"):
        data.pop(key, None)
    out["data"] = data
    return out


# -- registration ------------------------------------------------------------------------------------

def test_registry_and_skills_opt_in_the_five_operations(files):
    knowledge = _knowledge(files)
    plain = build_read_registry(_service(), LegacyResultLedger(), name="plain")
    assert not any(name in plain for name in OPERATIONS)  # absent unless the caller passes knowledge
    registry = build_read_registry(_service(), LegacyResultLedger(), name="ops", knowledge=knowledge)
    assert all(name in registry for name in OPERATIONS)
    skills = build_read_skills(registry)
    names = {s.name for s in skills.list_skills()}
    assert {"answer_literature_question", "analyze_knowledge_graph", "audit_literature_corpus"} <= names
    assert {t for s in skills.list_skills() for t in s.tools} >= set(OPERATIONS)  # full coverage
    visible = {t.spec.name for t in registry.visible_to(local_ctx(capabilities=frozenset({"chat.ask"})))}
    assert set(OPERATIONS) <= visible
    assert registry.visible_to(local_ctx(capabilities=frozenset())) == []  # chat.ask is required


def test_application_exposes_the_operations_to_its_executor(files):
    app = _app(_knowledge(files))
    result = asyncio.run(app.executor.aexecute(app.local_context(), TOOL_STATS, {"graph_id": "syn_v2"}))
    assert result.status is ToolStatus.OK and result.data["metrics"]["distinct_works"] == 3
    assert app.knowledge is not None and len(app.event_sink.events) == 0  # reads write no audit events


# -- parity: direct call == shared service == supervisor route -----------------------------------------

def test_supervisor_route_matches_direct_call_on_every_question(files):
    knowledge = _knowledge(files)
    script = [step for name, args in QUESTIONS.values() for step in (tool_call(name, args, f"c-{name}"), _relay)]
    app = _app(knowledge, script)
    ctx = app.local_context(session_id="parity")

    async def run():
        rows = []
        for i, (question, (name, args)) in enumerate(QUESTIONS.items()):
            direct = await app.executor.aexecute(ctx, name, args)
            service = knowledge.result(ctx, name, args)
            delegated = await app.delegate(ctx, question, thread_id=f"ops-{i}")
            rows.append((question, name, direct, service, delegated))
        return rows

    for question, name, direct, service, delegated in asyncio.run(run()):
        assert direct.status is ToolStatus.OK, question
        assert len(delegated.tool_results) == 1 and delegated.tool_results[0].tool == name
        via_supervisor = delegated.tool_results[0]
        assert _comparable(via_supervisor.model_dump(mode="json")) == _comparable(direct.model_dump(mode="json"))
        assert _comparable(service.model_dump(mode="json")) == _comparable(direct.model_dump(mode="json"))
        assert direct.data["coverage"]["completeness"] == "complete"
        relayed = json.loads(delegated.final_text)
        assert relayed["metrics"] == direct.data["metrics"]  # the model relays the computed totals
    assert app.chat_model.script == []  # every scripted step was consumed: one plan + one relay per question


def test_recent_entities_on_a_json_snapshot_is_unsupported_on_every_surface(files):
    knowledge = _knowledge(files)
    app = _app(knowledge, [tool_call(TOOL_RECENT, {"graph_id": "syn_v2"}, "r1"), AIMessage(content="n/a")])
    ctx = app.local_context()
    direct = asyncio.run(app.executor.aexecute(ctx, TOOL_RECENT, {"graph_id": "syn_v2"}))
    assert direct.status is ToolStatus.ERROR and direct.error_code is ErrorCode.UNSUPPORTED_CAPABILITY
    with pytest.raises(ContractError) as err:
        knowledge.result(ctx, TOOL_RECENT, {"graph_id": "syn_v2"})
    assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    delegated = asyncio.run(app.delegate(ctx, "what was added recently?", thread_id="r"))
    assert delegated.tool_results[0].error_code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_partial_scan_stays_partial_through_direct_service_and_supervisor(files):
    capped = _knowledge(files, max_records=4)
    app = _app(capped, [tool_call(TOOL_STATS, {"graph_id": "syn_v2"}, "p1"), _relay])
    ctx = app.local_context()
    direct = asyncio.run(app.executor.aexecute(ctx, TOOL_STATS, {"graph_id": "syn_v2"}))
    service = capped.result(ctx, TOOL_STATS, {"graph_id": "syn_v2"})
    delegated = asyncio.run(app.delegate(ctx, "how many entities?", thread_id="p"))
    for result in (direct, service, delegated.tool_results[0]):
        assert result.status is ToolStatus.PARTIAL
        assert result.data["coverage"]["completeness"] == "partial"
        assert result.data["coverage"]["records_scanned"] == 4
        assert result.warnings[0].startswith("partial:")
    full = _knowledge(files).result(ctx, TOOL_STATS, {"graph_id": "syn_v2"})
    assert full.status is ToolStatus.OK and full.data["metrics"]["entities"] > direct.data["metrics"]["entities"]


def test_no_source_material_leaves_any_agent_surface(files, tmp_path):
    knowledge = _knowledge(files)
    script = [step for name, args in QUESTIONS.values() for step in (tool_call(name, args, f"d-{name}"), _relay)]
    app = _app(knowledge, script)
    ctx = app.local_context()
    bodies = []
    for i, (question, (name, args)) in enumerate(QUESTIONS.items()):
        bodies.append(json.dumps(asyncio.run(app.executor.aexecute(ctx, name, args)).model_dump(mode="json")))
        bodies.append(json.dumps(knowledge.result(ctx, name, args).model_dump(mode="json")))
        delegated = asyncio.run(app.delegate(ctx, question, thread_id=f"d-{i}"))
        bodies.append(json.dumps([r.model_dump(mode="json") for r in delegated.tool_results]))
        bodies.append(delegated.final_text)
    for body in bodies:
        assert CANARY not in body and SNIPPET not in body and "/abs/" not in body and str(tmp_path) not in body


# -- capability, validation and scope --------------------------------------------------------------------

def test_service_requires_chat_ask_validates_arguments_and_rejects_unknown_operations(files):
    knowledge = _knowledge(files)
    with pytest.raises(ContractError) as err:
        knowledge.result(local_ctx(capabilities=frozenset({"graph.edit"})), TOOL_STATS, {"graph_id": "syn_v2"})
    assert err.value.code is ErrorCode.DENIED
    for operation, args in ((TOOL_STATS, {}), (TOOL_STATS, {"graph_id": "syn_v2", "extra": 1}),
                            (TOOL_COMPARE, {"graph_ids": ["syn_v2"]}), ("run_python", {"code": "1"})):
        with pytest.raises(ContractError) as err:
            knowledge.result(local_ctx(), operation, args)
        assert err.value.code is ErrorCode.INVALID_INPUT, (operation, args)
    with pytest.raises(ContractError) as err:
        knowledge.result(local_ctx(), TOOL_STATS, {"graph_id": "nope"})
    assert err.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(ContractError) as err:  # T19C scope rule, unchanged
        knowledge.result(local_ctx(corpus_scope=frozenset({"other"})), TOOL_STATS, {"graph_id": "syn_v2"})
    assert err.value.code is ErrorCode.DENIED


def test_dynamic_graphs_follow_the_callers_selection(files):
    selection: List[str] = [str(files["syn_v2"])]
    knowledge = KnowledgeOperations.from_graph_files(lambda: list(selection))
    assert knowledge.graph.port.graph_ids() == ["syn_v2"]
    with pytest.raises(ContractError) as err:
        knowledge.compute(local_ctx(), TOOL_STATS, {"graph_id": "syn_v1"})
    assert err.value.code is ErrorCode.NOT_FOUND  # outside the current selection
    selection.append(str(files["syn_v1"]))  # the user selects a second graph (T25D)
    assert knowledge.graph.port.graph_ids() == ["syn_v2", "syn_v1"]
    assert knowledge.compute(local_ctx(), TOOL_COMPARE, {"graph_ids": ["syn_v1", "syn_v2"]}).metrics
    selection.clear()
    assert knowledge.graph.port.graph_ids() == []
    port = DynamicJsonGraphs(lambda: [files["syn_v2"]])
    first = port.snapshot(local_ctx(), "syn_v2")
    assert port.snapshot(local_ctx(), "syn_v2") is first  # stable selection reuses the loaded snapshot
    port.reload()
    assert port.snapshot(local_ctx(), "syn_v2") is not first


def test_legacy_adapter_builds_operations_over_the_active_runtime_graphs(files, tmp_path, monkeypatch):
    from app.modules.runtime_bridge.legacy_adapter import active_json_graph_files, build_knowledge_operations

    monkeypatch.chdir(tmp_path)
    runtime = SimpleNamespace(graph_source="json",
                              json_graph_paths=[str(files["syn_v2"]), "storage/kg/missing.json"])
    pipeline = SimpleNamespace(runtime=runtime, _runtime_json_paths=lambda: list(runtime.json_graph_paths))
    assert active_json_graph_files(pipeline) == [str(files["syn_v2"])]  # missing files are skipped
    knowledge = build_knowledge_operations(pipeline)
    assert knowledge.graph.port.graph_ids() == ["syn_v2"]
    runtime.json_graph_paths = [str(files["syn_v1"]), str(files["syn_v2"])]  # per-user selection changes
    assert knowledge.graph.port.graph_ids() == ["syn_v1", "syn_v2"]
    runtime.graph_source = "splash"
    assert active_json_graph_files(pipeline) == [] and knowledge.graph.port.graph_ids() == []
    assert knowledge.corpus is not None  # storage/terminology under the project root (cwd)


def test_langgraph_mode_legacy_service_registers_the_operations_for_its_selection(tmp_path, monkeypatch):
    from tests.read_path.read_path_fakes import (
        GRAPH_ID, OPS_GRAPH_ID, SECOND_KG, install_fake_inference, write_graph)
    from tests.read_path.test_read_path_parity import _pipeline

    monkeypatch.chdir(tmp_path)
    install_fake_inference(monkeypatch)
    kgs = [write_graph(tmp_path / "kg"), write_graph(tmp_path / "kg", SECOND_KG, name="matkg_fixture_ops_v1.json")]
    monkeypatch.setenv("F2W_RUNTIME", "langgraph")
    monkeypatch.setenv("F2W_QA_ROUTE", "direct")
    service = _pipeline(tmp_path, kgs, "ops")
    app = service._read_path.application
    assert all(name in app.registry for name in OPERATIONS)
    assert sorted(app.knowledge.graph.port.graph_ids()) == sorted([GRAPH_ID, OPS_GRAPH_ID])
    ctx = app.local_context()
    result = asyncio.run(app.executor.aexecute(ctx, TOOL_STATS, {"graph_id": GRAPH_ID}))
    assert result.status is ToolStatus.OK and result.data["metrics"]["entities"] == 4
    monkeypatch.delenv("F2W_RUNTIME")
    monkeypatch.delenv("F2W_QA_ROUTE")
    legacy = _pipeline(tmp_path, kgs)  # legacy runtime: no read path, no registry, unchanged
    assert legacy._read_path is None


def test_directory_corpus_resolver_names_files_inside_the_directory_only(files, tmp_path):
    resolve = directory_corpus_resolver(tmp_path / "corpus")
    assert resolve(local_ctx(), "syn").corpus_id == "syn"
    assert resolve(local_ctx(), "missing") is None
    assert resolve(local_ctx(), "../syn_v2") is None and resolve(local_ctx(), ".hidden") is None
    with pytest.raises(ContractError):
        resolve(local_ctx(corpus_scope=frozenset({"other"})), "syn")
    result = KnowledgeOperations(GraphOperationsTool(JsonGraphBackend({})), CorpusAuditTool(resolve)).result(
        local_ctx(), TOOL_AUDIT, {"corpus": "syn"})
    assert result.status is ToolStatus.OK and result.data["metrics"]["summary"]["distinct_works"] == 2


def test_executor_over_the_bundle_runs_without_any_model(files):
    registry = build_read_registry(_service(), LegacyResultLedger(), name="x", knowledge=_knowledge(files))
    executor = ToolExecutor(registry, event_sink=InMemoryEventSink(), limits=RuntimeLimits(max_retries=0))
    result = executor.execute(local_ctx(), TOOL_AUDIT, {"corpus": "syn"})
    assert result.status is ToolStatus.OK and result.data["model_calls"] == 0

"""T04: app.public (f2w.public) facades and the one-name guard, in checkout mode."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import public
from app.modules.contracts import ToolStatus
from app.modules.operations.papers import PaperScope
from tests.operations.summary_fakes import CASES, write_case
from tests.read_path.read_path_fakes import ANSWER_TEXT, ANSWERED_Q, ask_tool_then_relay, local_settings
from tests.read_path.test_read_tool import SUCCESS, StubAgent
from tests.runtime.runtime_fakes import ScriptedChatModel, als_ctx

REPO = Path(__file__).resolve().parents[2]


def _retrieval(agent=None):
    from app.modules.runtime_bridge.retrieval import LegacyRetrievalService

    return LegacyRetrievalService(agent or StubAgent(SUCCESS))


def _client(**kw):
    return public.F2WClient.from_settings(local_settings(), retrieval=_retrieval(kw.pop("agent", None)), **kw)


def test_answer_from_knowledge_is_the_application_call():
    with _client() as client:
        ctx = client.context_for(session_id="s1")
        assert ctx.principal.subject == "local-operator"
        out = asyncio.run(client.answer_from_knowledge(ctx, "q?"))
        assert out.result.status is ToolStatus.OK and out.to_legacy() == SUCCESS
        assert client.answer_from_knowledge_sync(ctx, "q?").to_legacy() == SUCCESS
        assert client.application.retrieval.calls == 2


def test_two_clients_share_no_state():
    a, b = _client(), _client()
    assert a.application is not b.application
    assert a.application.registry is not b.application.registry
    assert a.application.executor is not b.application.executor


def test_summary_runs_the_t19b_operation_through_an_authorizing_executor(tmp_path):
    entry, terms = CASES["capped"]
    paths = write_case(tmp_path, entry, terms)
    scope = PaperScope(paths["terms_path"], paths["manifest_path"], paths["filename"])
    client = _client()
    ctx = client.context_for()
    result = asyncio.run(client.summarize_paper_terms(ctx, scope, max_terms=5))
    assert result.status is ToolStatus.OK and result.data["term_count"] == 23
    assert result.data["displayed_term_count"] == 5 and result.data["completeness"] == "partial"
    refused = asyncio.run(client.summarize_paper_terms(ctx, scope, max_terms=99))
    assert refused.status is ToolStatus.INVALID
    denied = asyncio.run(client.summarize_paper_terms(als_ctx("bob", capabilities=frozenset()), scope))
    assert denied.status is ToolStatus.DENIED
    with pytest.raises(TypeError):
        asyncio.run(client.summarize_paper_terms(ctx, paths["terms_path"]))


def test_delegate_through_create_literature_agent():
    model = ScriptedChatModel(script=ask_tool_then_relay(ANSWERED_Q))
    with public.create_literature_agent(settings=local_settings(),
                                        retrieval=_retrieval(StubAgent(dict(SUCCESS, answer=ANSWER_TEXT))),
                                        chat_model=model) as agent:
        ctx = agent.client.context_for(session_id="s")
        out = asyncio.run(agent.delegate(ctx, ANSWERED_Q, thread_id="t1"))
        assert out.answered and len(out.reads) == 1


def test_agent_factory_argument_rules():
    with pytest.raises(ValueError):
        public.create_literature_agent(client=_client())  # no chat model, no supervisor
    with pytest.raises(ValueError):
        public.create_literature_agent(settings=local_settings())
    with pytest.raises(NotImplementedError, match="T14/T15"):
        public.create_literature_agent(client=_client(), async_model=object())
    agent = public.create_literature_agent(client=_client(chat_model=ScriptedChatModel()))
    assert isinstance(agent, public.LiteratureAgent)


@pytest.mark.parametrize("method,args,backing", [
    ("query_knowledge", ("q",), "kg_find_entities"), ("trace_provenance", ("r",), "kg_get_lineage"),
])
def test_unimplemented_methods_name_their_missing_backing(method, args, backing):
    """T15: the two pending methods name the unported K-12 tools, not a merged task."""
    client = _client()
    with pytest.raises(NotImplementedError, match=backing) as err:
        getattr(client, method)(client.context_for(), *args)
    assert "T05" not in str(err.value) and "T08" not in str(err.value).replace("T08 did not port", "")


@pytest.mark.parametrize("method,args", [
    ("search_literature", ("q",)), ("resolve_work", ("10.1/x",)), ("read_paper", ("10.1/x",)),
    ("read_section", ("10.1/x", "results")), ("get_evidence", (["e"],)), ("evidence_bundle", ("w",)),
])
def test_literature_and_evidence_methods_without_wiring_are_unsupported(method, args):
    """T15: no LiteratureToolset / evidence source given -> UNSUPPORTED_CAPABILITY, never a silent empty."""
    from app.modules.contracts.errors import ContractError, ErrorCode

    client = _client()
    with pytest.raises(ContractError) as err:
        getattr(client, method)(client.context_for(), *args)
    assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


@pytest.mark.parametrize("method,args", [
    ("submit_ingest", ({},)), ("get_job", ("j",)), ("cancel_job", ("j",)), ("plan_publication", ("s", "t")),
    ("publish_plan", (object(), object())),
    ("graph_statistics", ("g",)), ("compare_graphs", (["a", "b"],)), ("paper_subgraph", ("g", "paper.pdf")),
    ("recent_entities", ("g",)), ("audit_corpus", ("c",)), ("run_knowledge_operation", ("kg_compute_statistics", {})),
    ("submit_knowledge_operation", ("kg_compute_statistics", {})), ("knowledge_job_result", ("j",)),
    ("cost_control", ()), ("bulk_review", ([],)), ("vocabulary_quality", ()), ("figure_scales", ()),
    ("figure_scale", ("r",)), ("folder_roots", ()), ("list_folder", ("papers",)), ("folder_index", ("papers",)),
])
def test_job_methods_without_operations_are_unsupported(method, args):
    """C2: these forward to F2WOperations (tests/api_v1/test_v1_sdk_client.py); none given here."""
    from app.modules.contracts.errors import ContractError, ErrorCode

    client = _client()
    with pytest.raises(ContractError) as err:
        getattr(client, method)(client.context_for(), *args)
    assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


def _run(code: str) -> dict:
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd="/",
                         env={"PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_public_import_is_light():
    result = _run(
        "import json, os, sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "env = dict(os.environ); before = set(sys.modules)\n"
        "import app.public\n"
        "new = {m.split('.')[0] for m in set(sys.modules) - before}\n"
        "print(json.dumps({'new': sorted(new), 'env_changed': dict(os.environ) != env}))\n"
    )
    heavy = {"torch", "faiss", "sentence_transformers", "fitz", "openai", "langgraph", "langchain_core",
             "tiled", "dotenv", "academy", "fastapi", "requests", "numpy"}
    assert not heavy & set(result["new"]), result["new"]
    assert result["env_changed"] is False


def test_guard_rejects_loading_f2w_under_a_second_name():
    """Simulate an installed ``f2w`` (same files under another name) and a later checkout ``import app``."""
    result = _run(
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('f2w', {str(REPO / 'app' / '__init__.py')!r},\n"
        f"    submodule_search_locations=[{str(REPO / 'app')!r}])\n"
        "mod = importlib.util.module_from_spec(spec); sys.modules['f2w'] = mod; spec.loader.exec_module(mod)\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "try:\n"
        "    import app\n"
        "    err = None\n"
        "except ImportError as exc:\n"
        "    err = str(exc)\n"
        "print(json.dumps({'err': err, 'app_loaded': 'app' in sys.modules}))\n"
    )
    assert result["err"] and "already imported as 'f2w'" in result["err"]
    assert result["app_loaded"] is False


def test_guard_ignores_a_foreign_app_package(tmp_path):
    forge = tmp_path / "forge" / "app"
    forge.mkdir(parents=True)
    (forge / "__init__.py").write_text("FORGE_APP = True\n")
    result = _run(
        "import importlib.util, json, sys\n"
        f"sys.path.insert(0, {str(forge.parent)!r})\n"
        "import app\n"
        f"spec = importlib.util.spec_from_file_location('f2w', {str(REPO / 'app' / '__init__.py')!r},\n"
        f"    submodule_search_locations=[{str(REPO / 'app')!r}])\n"
        "mod = importlib.util.module_from_spec(spec); sys.modules['f2w'] = mod; spec.loader.exec_module(mod)\n"
        "print(json.dumps({'forge': getattr(sys.modules['app'], 'FORGE_APP', False)}))\n"
    )
    assert result == {"forge": True}

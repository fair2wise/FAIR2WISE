"""T19D: Knowledge Operations never generate, compile or run code, and never run model-written code.

KNOWLEDGE_OPERATIONS.md section 8: "Known reviewed operations are the initial execution
model. Generating and running arbitrary new Python to answer an unseen question is a
separate future design". Three independent proofs:

1. **Static (AST).** The operations package, the wrapped legacy summary function and the
   graph read path they call contain no ``eval``/``exec``/builtin ``compile``/``__import__``,
   no process, ``runpy``, ``pickle``/``marshal`` or ``ctypes`` use, no ``getattr`` on
   builtins, and ``re.compile`` only on literal patterns. The single dynamic import is the
   package's lazy ``__getattr__`` over its constant export table.
2. **Runtime.** With those primitives replaced by tripwires, every operation runs over
   the large populations (and with code-shaped inputs) without touching one.
3. **Contract.** Every operation spec is read-only (``read_corpus`` only: no model,
   network or write effect), its inputs are identifiers, enums or bounded text that no
   code path evaluates, no tool named like a code runner exists, and job dispatch refuses
   an operation that is not in the reviewed table.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import json
import os
import re
import runpy
import subprocess
import textwrap
from pathlib import Path

import pytest

from app.modules.agent_runtime.executor import RuntimeLimits, ToolExecutor
from app.modules.agent_runtime.policies import InMemoryEventSink
from app.modules.contracts import ContractError, Effect, ErrorCode, ToolStatus
from app.modules.f2w_agent import paper_evidence_agent as pea
from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.operations import corpus as corpus_mod
from app.modules.operations import graph as graph_mod
from app.modules.operations import papers as papers_mod
from app.modules.operations.corpus import CURATE_AUDIT_CORPUS, CorpusAuditTool, mapping_corpus_resolver
from app.modules.operations.graph import GRAPH_SPECS, TOOL_STATS, TOOL_SUBGRAPH, GraphOperationsTool
from app.modules.operations.papers import (
    LIT_SUMMARIZE_EXTRACTED_TERMS,
    TOOL_NAME as TOOL_SUMMARY,
    PaperScope,
    PaperSummaryTool,
    pinned_paper_scope,
)
from app.modules.operations.reports import ScanControl, operation_dispatch
from app.modules.tools.bootstrap import build_registry
from tests.operations import scale_population as sp
from tests.runtime.runtime_fakes import local_ctx

REPO = Path(__file__).resolve().parents[2]
OPS_DIR = REPO / "app" / "modules" / "operations"
#: The graph read path the operations call (scan, snapshot, Tiled paging, identity mapping, scope).
READ_PATH = [REPO / "app" / "modules" / "graph" / p for p in
             ("backends/json.py", "backends/tiled.py", "backends/tiled_client.py", "identity_adapter.py",
              "ports.py", "snapshot.py")]
#: The legacy function lit_summarize_extracted_terms wraps, and its helpers.
WRAPPED = (pea.summarize_extracted_terms, pea._manifest_entry, pea._tokens)

FORBIDDEN_NAMES = {"eval", "exec", "compile", "__import__", "breakpoint", "execfile"}
FORBIDDEN_MODULES = {"subprocess", "runpy", "code", "codeop", "pickle", "marshal", "ctypes", "multiprocessing",
                     "shelve", "dill", "cloudpickle", "pty", "importlib"}
FORBIDDEN_ATTRS = {"system", "popen", "Popen", "spawnl", "spawnv", "execl", "execv", "execve", "execvp",
                   "run_path", "run_module", "exec_module", "import_module", "loads", "load", "FunctionType",
                   "CodeType", "interact"}


def _violations(tree: ast.AST, where: str):
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in FORBIDDEN_NAMES:
                found.append(f"{where}:{node.lineno} {fn.id}()")
            if isinstance(fn, ast.Name) and fn.id == "getattr" and node.args and \
                    isinstance(node.args[0], ast.Name) and node.args[0].id in ("builtins", "__builtins__"):
                found.append(f"{where}:{node.lineno} getattr(builtins, ...)")
            if isinstance(fn, ast.Attribute):
                base = ast.unparse(fn.value)
                if fn.attr == "compile" and base == "re":
                    if not (node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                        found.append(f"{where}:{node.lineno} re.compile on a non-literal pattern")
                elif fn.attr in FORBIDDEN_NAMES or (fn.attr in FORBIDDEN_ATTRS and
                                                     base.split(".")[0] in FORBIDDEN_MODULES | {"os", "types"}):
                    found.append(f"{where}:{node.lineno} {base}.{fn.attr}()")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for mod in mods:
                if mod.split(".")[0] in FORBIDDEN_MODULES:
                    found.append(f"{where}:{node.lineno} import {mod}")
        elif isinstance(node, ast.Name) and node.id in ("__builtins__",):
            found.append(f"{where}:{node.lineno} __builtins__")
    return found


def _lazy_export_table_only(tree: ast.Module):
    """``operations/__init__``: the one ``importlib.import_module`` is ``__getattr__`` over ``_EXPORTS``."""
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "import_module"]
    assert len(calls) == 1
    getattr_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "__getattr__")
    assert calls[0] in list(ast.walk(getattr_fn))
    assert "_EXPORTS[name]" in ast.unparse(getattr_fn)
    exports = next(n for n in tree.body if isinstance(n, ast.AnnAssign) and ast.unparse(n.target) == "_EXPORTS")
    assert isinstance(exports.value, ast.Dict) and all(isinstance(v, ast.Tuple) and
                                                       all(isinstance(e, ast.Constant) for e in v.elts)
                                                       for v in exports.value.values)


# -- 1. static -------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", sorted(OPS_DIR.glob("*.py")) + READ_PATH, ids=lambda p: p.name)
def test_no_code_execution_primitive_in_the_operations_or_their_read_path(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = _violations(tree, path.name)
    if path == OPS_DIR / "__init__.py":
        _lazy_export_table_only(tree)
        found = [f for f in found if "import_module" not in f and "import importlib" not in f]
    assert found == []


@pytest.mark.parametrize("fn", WRAPPED, ids=lambda f: f.__name__)
def test_the_wrapped_legacy_summary_runs_no_generated_code(fn):
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    assert _violations(tree, fn.__name__) == []


def test_the_static_check_catches_what_it_claims():
    bad = ast.parse("import subprocess\neval(x)\nexec(y)\ncompile(s, 'f', 'exec')\nre.compile(p)\n"
                    "os.system(c)\nimportlib.import_module(m)\ngetattr(builtins, 'eval')\n__import__(n)\n")
    found = _violations(bad, "probe")
    assert len(found) == 9, found
    assert _violations(ast.parse("re.compile(r'^x$')\njson.loads(s)\n"), "ok") == []


# -- 2. runtime ------------------------------------------------------------------------------------

@pytest.fixture
def tripwires(monkeypatch):
    calls = []

    def trip(name):
        def fire(*a, **k):
            calls.append(name)
            raise AssertionError(f"{name} called during a Knowledge Operation")
        return fire

    for name in ("eval", "exec", "compile"):
        monkeypatch.setattr(builtins, name, trip(name))
    for mod, attr in ((subprocess, "Popen"), (subprocess, "run"), (os, "system"), (os, "popen"), (os, "execv"),
                      (os, "execve"), (runpy, "run_path"), (runpy, "run_module")):
        monkeypatch.setattr(mod, attr, trip(f"{mod.__name__}.{attr}"))
    return calls


def _executor(*registrations):
    registry = build_registry(None, include=(), extra=list(registrations), name="t19d-nocode", freeze=True)
    return ToolExecutor(registry, event_sink=InMemoryEventSink(), limits=RuntimeLimits(max_retries=0))


@pytest.fixture(scope="module")
def tools(big_graph, big_corpus, big_session):
    graph = GraphOperationsTool(JsonGraphBackend({"syn_big": big_graph.path}))
    corpus = CorpusAuditTool(mapping_corpus_resolver({"syn_big": str(big_corpus.path)}))
    executor = _executor(*graph.registrations(), corpus.registration(), PaperSummaryTool().registration())
    scope = PaperScope(str(big_session.path), str(big_session.extra[0]), sp.ACTIVE)
    work = max(big_graph.truth.work_members, key=lambda w: len(big_graph.truth.work_members[w]))
    calls = [(TOOL_STATS, {"graph_id": "syn_big"}),
             (TOOL_SUBGRAPH, {"graph_id": "syn_big", "paper": big_graph.truth.work_keys[work][0]}),
             ("curate_audit_corpus", {"corpus": "syn_big"}), (TOOL_SUMMARY, {})]
    with pinned_paper_scope(scope):  # warm-up: first imports may legitimately run module-level code
        for name, args in calls:
            assert executor.execute(local_ctx(), name, args).status is ToolStatus.OK
    return executor, scope, calls


def test_operations_over_the_large_populations_touch_no_code_primitive(tools, tripwires):
    executor, scope, calls = tools
    with pinned_paper_scope(scope):
        results = [executor.execute(local_ctx(), name, args) for name, args in calls]
    assert [r.status for r in results] == [ToolStatus.OK] * 4 and tripwires == []
    assert all(r.data.get("model_calls", 0) == 0 for r in results)


CODE_INPUTS = [
    "__import__('os').system('touch {flag}')",
    "eval(compile('open(\"{flag}\", \"w\")', 'x', 'exec'))",
    "{{{{ ''.__class__.__mro__[1].__subclasses__() }}}}; touch {flag}",
    "$(touch {flag}) `touch {flag}`",
]


@pytest.mark.parametrize("template", CODE_INPUTS)
def test_code_shaped_inputs_are_data_never_code(tools, tripwires, tmp_path, template, big_session):
    executor, scope, _calls = tools
    flag = tmp_path / "executed.flag"
    text = template.format(flag=flag)
    sub = executor.execute(local_ctx(), TOOL_SUBGRAPH, {"graph_id": "syn_big", "paper": text[:300]})
    assert sub.status is ToolStatus.ERROR and sub.error_code is ErrorCode.NOT_FOUND
    bad_id = executor.execute(local_ctx(), TOOL_STATS, {"graph_id": text})
    assert bad_id.status is ToolStatus.INVALID  # the graph id pattern rejects it before any code runs
    with pinned_paper_scope(scope):
        summary = executor.execute(local_ctx(), TOOL_SUMMARY, {"query": text, "missing_topics": [text]})
    assert summary.status is ToolStatus.OK and summary.data["term_count"] == big_session.truth.term_count
    assert not flag.exists() and tripwires == []


# -- 3. contract -----------------------------------------------------------------------------------

ALL_SPECS = (*GRAPH_SPECS, CURATE_AUDIT_CORPUS, LIT_SUMMARIZE_EXTRACTED_TERMS)
_CODE_FIELD = re.compile(r"(?i)^(code|script|python|program|expression|expr|command|cmd|source|function|lambda|"
                         r"snippet|sql|query_code)$")


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.name)
def test_specs_are_read_only_and_take_no_code(spec):
    assert spec.effects == frozenset({Effect.READ_CORPUS})
    assert not spec.effects & {Effect.MODEL_INFERENCE, Effect.PAID_INFERENCE, Effect.NETWORK, Effect.WRITE_GRAPH,
                               Effect.WRITE_STAGED, Effect.DESTRUCTIVE}
    assert spec.input_schema.get("additionalProperties") is False
    for prop, schema in spec.input_schema.get("properties", {}).items():
        assert not _CODE_FIELD.match(prop), prop
        if schema.get("type") == "string":
            assert "pattern" in schema or "enum" in schema or schema.get("maxLength", 10**9) <= 16000, prop


def test_no_code_runner_tool_exists_in_the_application():
    runner = re.compile(r"""["'](?:run|exec|execute|eval)_(?:python|script|code|sql)["']|["']operations_execute["']""")
    hits = []
    for path in (REPO / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        hits += [f"{path.relative_to(REPO)}:{m.group(0)}" for m in runner.finditer(text)]
    assert hits == []


def test_job_dispatch_runs_only_reviewed_operations(big_graph):
    graph = GraphOperationsTool(JsonGraphBackend({"syn_big": big_graph.path}))
    run = operation_dispatch(graph)
    for name in ("run_python", "exec", "eval", "kg_compute_statistics; import os"):
        with pytest.raises(ContractError) as err:
            run(local_ctx(), name, {"graph_id": "syn_big"}, ScanControl(1))
        assert err.value.code is ErrorCode.INVALID_INPUT
    assert set(graph.operation_names()) == {s.name for s in GRAPH_SPECS}


def test_reports_never_carry_code_for_a_model_to_run(tools):
    executor, scope, calls = tools
    with pinned_paper_scope(scope):
        bodies = [json.dumps(executor.execute(local_ctx(), n, a).data) for n, a in calls]
    for body in bodies:
        assert not re.search(r"\b(?:def |lambda |import |exec\(|eval\()", body)
    assert graph_mod.__doc__ and corpus_mod.__doc__ and papers_mod.__doc__  # reviewed, documented modules

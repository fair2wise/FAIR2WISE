"""Lazy package exports, the LangGraph agent loop and the launcher helpers that do not need a live exchange."""

from __future__ import annotations

import asyncio
import builtins
import importlib
from pathlib import Path
import pickle
import runpy
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("academy")


# ------------------------------------------------------------------ lazy exports
@pytest.mark.parametrize("package", ["app.modules.agent_runtime", "app.modules.runtime_bridge"])
def test_every_lazy_export_resolves_to_a_real_attribute(package):
    module = importlib.import_module(package)
    for name in module.__all__:
        value = getattr(module, name)
        assert value is not None and name in dir(module)
        assert getattr(module, name) is value  # cached on first access
    with pytest.raises(AttributeError, match="no attribute"):
        getattr(module, "definitely_not_exported")


def test_f2w_agent_lazy_classes_resolve_and_unknown_names_raise():
    pkg = importlib.import_module("app.modules.f2w_agent")
    for name in ("RetrievalAgent", "DownloadAgent", "ExtractorAgent", "EvidenceDebateAgent",
                 "WorkflowOrchestratorAgent", "PaperEvidenceAgent"):
        assert getattr(pkg, name).__name__ == name
    with pytest.raises(AttributeError):
        pkg.NotAnAgent


def test_term_extractor_package_degrades_to_none_when_optional_imports_fail(monkeypatch):
    names = ["app.modules.term_extractor"] + [
        f"app.modules.term_extractor.{sub}" for sub in ("academy_agent", "clients", "orchestrator")]
    saved = {name: sys.modules[name] for name in names if name in sys.modules}
    real_import = builtins.__import__

    def failing(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 1 and name in ("academy_agent", "clients", "orchestrator"):
            raise ImportError(f"simulated missing dependency for {name}")
        return real_import(name, globals, locals, fromlist, level)

    for name in names:
        sys.modules.pop(name, None)
    builtins.__import__ = failing
    try:
        degraded = importlib.import_module("app.modules.term_extractor")
    finally:
        builtins.__import__ = real_import
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)  # other tests keep the module objects they imported
        if "app.modules.term_extractor" in saved:  # the import system also rebinds the attribute on the parent
            setattr(sys.modules["app.modules"], "term_extractor", saved["app.modules.term_extractor"])
    assert degraded.TermExtractorAgent is None and degraded.make_chat_client is None and degraded.Orchestrator is None
    assert degraded.SchemaHelper is not None


# ------------------------------------------------------------------ LangGraph loop
def test_build_graph_alternates_agent_and_tools_until_no_tool_calls():
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.tools import tool

    from app.modules.term_extractor.agent import build_graph

    @tool
    def double(x: int) -> int:
        """Double a number."""
        return 2 * x

    class ScriptedLLM:
        def __init__(self):
            self.seen = []

        def invoke(self, messages):
            self.seen.append(len(messages))
            if len(self.seen) == 1:
                return AIMessage(content="", tool_calls=[{"name": "double", "args": {"x": 21}, "id": "call-1"}])
            return AIMessage(content="the answer is 42")

    llm = ScriptedLLM()
    result = build_graph(llm=llm, tools=[double]).invoke({"messages": [HumanMessage(content="double 21")]})
    messages = result["messages"]
    assert llm.seen == [1, 3]  # question; then question + tool request + tool result
    assert messages[2].content == "42" and messages[-1].content == "the answer is 42"
    plain = ScriptedLLM()
    plain.seen = [0]  # skip the scripted tool request
    out = build_graph(llm=plain).invoke({"messages": [HumanMessage(content="hi")]})
    assert out["messages"][-1].content == "the answer is 42" and len(out["messages"]) == 2


# ------------------------------------------------------------------ launchers
def test_academy_auth_creates_a_globus_exchange_user_client(monkeypatch):
    from app.modules.launchers import academy_auth

    seen = {}

    class Factory:
        def __init__(self, url, auth_method):
            seen.update(url=url, auth_method=auth_method)

        async def create_user_client(self):
            seen["client_created"] = True

    monkeypatch.setattr(academy_auth, "HttpExchangeFactory", Factory)
    academy_auth.main()
    assert seen == {"url": "https://exchange.academy-agents.org", "auth_method": "globus", "client_created": True}


def test_f2w_agent_launcher_loads_dotenv_explicitly_then_exits_with_cli_status(monkeypatch):
    from app.modules import dotenv_entry
    from app.modules.f2w_agent import cli

    loaded = []
    monkeypatch.setattr(dotenv_entry, "load_entrypoint_dotenv", lambda anchor: loaded.append(Path(anchor).name))
    monkeypatch.setattr(cli, "main", lambda: 3)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("app.modules.launchers.f2w_agent", run_name="__main__")
    assert exc.value.code == 3 and loaded == ["f2w_agent.py"]
    imported = importlib.import_module("app.modules.launchers.f2w_agent")  # importing never loads a .env
    assert loaded == ["f2w_agent.py"] and imported.main is not None


class TokenData:
    def __init__(self, expires_in, token_type="Bearer", token="tok"):
        import time

        self.expires_at_seconds = time.time() + expires_in
        self.token_type, self.access_token = token_type, token


def _patch_storage(monkeypatch, module, token_data):
    seen = {}

    def get_storage(filepath):
        seen["path"] = filepath
        return SimpleNamespace(get_token_data=lambda scope: token_data)

    monkeypatch.setattr(module, "get_token_storage", get_storage)
    return seen


def test_user_agent_launcher_reads_cached_token_and_rejects_missing_or_expiring_ones(monkeypatch, tmp_path):
    from app.modules.launchers import user_agent as ua

    seen = _patch_storage(monkeypatch, ua, TokenData(3600, token_type=None, token="abc"))
    assert ua.get_cached_auth_headers(tmp_path / "db") == {"Authorization": "Bearer abc"} and seen["path"] == tmp_path / "db"
    _patch_storage(monkeypatch, ua, TokenData(3600, token_type="MAC", token="x"))
    assert ua.get_cached_auth_headers()["Authorization"] == "MAC x"
    _patch_storage(monkeypatch, ua, None)
    with pytest.raises(RuntimeError, match="No Academy Exchange token"):
        ua.get_cached_auth_headers(tmp_path / "db")
    _patch_storage(monkeypatch, ua, TokenData(ua.TOKEN_EXPIRY_SKEW_S - 5))
    with pytest.raises(RuntimeError, match="expired or about to expire"):
        ua.get_cached_auth_headers(tmp_path / "db")


def test_user_agent_launch_writes_handle_file_and_waits(monkeypatch, tmp_path):
    from app.modules.launchers import user_agent as ua

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ua, "init_logging", lambda: None)
    monkeypatch.setattr(ua, "get_cached_auth_headers", lambda db: {"Authorization": "Bearer t"})
    events = []
    agent_id = SimpleNamespace(uid="uid-1")

    class Manager:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def launch(self, cls, kwargs):
            events.append(("launch", cls.__name__, kwargs))
            return SimpleNamespace(agent_id=agent_id)

        async def wait(self, handles, raise_error):
            events.append(("wait", raise_error))

        @classmethod
        async def from_exchange_factory(cls, factory, executors):
            events.append(("factory", factory.kwargs))
            return cls()

    class Factory:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(ua, "Manager", Manager)
    monkeypatch.setattr(ua, "HttpExchangeFactory", Factory)
    infos = []
    monkeypatch.setattr(ua.output, "info", lambda msg, *a, **k: infos.append(msg))
    asyncio.run(ua.launch(8123, tmp_path / "db"))
    assert events[0][1]["additional_headers"] == {"Authorization": "Bearer t"} and events[0][1]["auth_method"] is None
    assert events[1] == ("launch", "UserAgent", {"port": 8123}) and events[2] == ("wait", True)
    assert pickle.loads((tmp_path / "user_agent_handle.pkl").read_bytes()) == agent_id
    assert infos == ["User Agent Handle >>>> uid-1"]


def test_user_agent_main_parses_port_and_token_db(monkeypatch):
    from app.modules.launchers import user_agent as ua

    calls = []

    async def fake_launch(port, token_db):
        calls.append((port, token_db))

    monkeypatch.setattr(ua, "launch", fake_launch)
    monkeypatch.setattr(sys, "argv", ["prog", "-p", "9001", "--token-db", "~/tokens.db"])
    ua.main()
    assert calls == [(9001, Path("~/tokens.db").expanduser())]
    monkeypatch.setattr(sys, "argv", ["prog"])
    ua.main()
    assert calls[1] == (8000, ua.DEFAULT_TOKEN_DB.expanduser())


@pytest.fixture
def ae(monkeypatch):
    """The extractor launcher with a stand-in for the Globus Compute SDK (not installed in every environment)."""
    class Executor:
        def __init__(self, endpoint_id):
            self.endpoint_id = endpoint_id

    monkeypatch.setitem(sys.modules, "globus_compute_sdk", SimpleNamespace(Executor=Executor))
    monkeypatch.delitem(sys.modules, "app.modules.launchers.academy_extractor", raising=False)
    module = importlib.import_module("app.modules.launchers.academy_extractor")
    yield module
    sys.modules.pop("app.modules.launchers.academy_extractor", None)


def test_extractor_launcher_requires_the_handle_file_and_parses_arguments(monkeypatch, tmp_path, ae):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="user_agent_handle.pkl not found"):
        ae._load_user_agent_handle()
    (tmp_path / "user_agent_handle.pkl").write_bytes(pickle.dumps("agent-id"))
    monkeypatch.setattr(ae, "Handle", lambda agent_id: ("handle", agent_id))
    assert ae._load_user_agent_handle() == ("handle", "agent-id")

    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(ae, "run", fake_run)
    monkeypatch.setattr(ae, "_configure_logging", lambda: captured.setdefault("logging", True))
    monkeypatch.setattr(sys, "argv", ["prog", "--data-dir", "/d", "--output", "/o.json", "--backend", "cborg",
                                      "--max-workers", "5", "--log-file", "/l.log"])
    ae.main()
    assert captured == {"logging": True, "data_dir": "/d", "output_file": "/o.json", "model": "qwen3.5:9b",
                        "backend": "cborg", "schema_path": "storage/schema/matkg_schema.yaml", "max_workers": 5,
                        "log_file": "/l.log"}
    monkeypatch.setattr(sys, "argv", ["prog", "--data-dir", "/d"])
    with pytest.raises(SystemExit):
        ae.main()  # --output is required


def test_extractor_run_launches_agent_on_the_endpoint_and_shuts_down(monkeypatch, tmp_path, ae):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "user_agent_handle.pkl").write_bytes(pickle.dumps("agent-id"))
    monkeypatch.setattr(ae, "Handle", lambda agent_id: ("handle", agent_id))
    monkeypatch.setenv("GLOBUS_COMPUTE_ENDPOINT_ID", "endpoint-1")
    monkeypatch.setenv("CBORG_API_KEY", "key")
    monkeypatch.delenv("CBORG_BASE_URL", raising=False)
    events = []

    class Remote:
        async def process_directory(self, data_dir):
            events.append(("process", data_dir))
            return {"status": "success"}

        async def get_term_count(self):
            return 7

    class Manager:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def launch(self, agent):
            events.append(("launch", agent.kwargs))
            return Remote()

        async def shutdown(self, handle, blocking):
            events.append(("shutdown", blocking))

        @classmethod
        async def from_exchange_factory(cls, factory, executors):
            events.append(("factory", factory.args, executors.endpoint_id))
            return cls()

    class Factory:
        def __init__(self, *args, **kwargs):
            self.args = (args, kwargs)

    class Agent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(ae, "Manager", Manager)
    monkeypatch.setattr(ae, "HttpExchangeFactory", Factory)
    monkeypatch.setattr(ae, "TermExtractorAgent", Agent)
    infos = []
    monkeypatch.setattr(ae.output, "info", lambda msg, *a, **k: infos.append(msg))
    asyncio.run(ae.run("/data", "/out.json", "m", "schema.yaml", "cborg", 3, None))
    assert events[0] == ("factory", (("https://exchange.academy-agents.org",), {"auth_method": "globus"}), "endpoint-1")
    launched = events[1][1]
    assert launched["user_agent_handle"] == ("handle", "agent-id") and launched["cborg_api_key"] == "key"
    assert launched["cborg_base"] == "https://api.cborg.lbl.gov" and launched["max_workers"] == 3
    assert events[2:] == [("process", "/data"), ("shutdown", True)] and infos == ["\nResult: {'status': 'success'}"]
    monkeypatch.delenv("GLOBUS_COMPUTE_ENDPOINT_ID")
    with pytest.raises(KeyError):
        asyncio.run(ae.run("/data", "/out.json", "m", "s", "ollama", 1, None))


def test_extractor_logging_goes_to_a_file_not_the_console(monkeypatch, tmp_path, ae):
    import logging

    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        for h in before:
            root.removeHandler(h)
        ae._configure_logging()
        assert [type(h).__name__ for h in root.handlers] == ["FileHandler"]
    finally:
        for h in list(root.handlers):
            h.close()
            root.removeHandler(h)
        for h in before:
            root.addHandler(h)

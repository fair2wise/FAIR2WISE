"""Entry-point behaviour for the Tiled launcher and the owned worker process (fakes only)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

from app.modules.workers import __main__ as worker_main

REPO = Path(__file__).resolve().parents[2]
ENTRY = REPO / "app/modules/deployment/_tiled_entry.py"


@pytest.fixture
def entry():
    spec = importlib.util.spec_from_file_location("_tiled_entry_under_test", ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_fake_tiled(monkeypatch, *, version="0.2.18", parse_result=None, valid=True, record=None):
    record = record if record is not None else {}
    tiled = types.ModuleType("tiled")
    tiled.__version__ = version

    class Config:
        @staticmethod
        def model_validate(conf):
            if not valid:
                raise ValueError("bad conf SECRET-VALUE")
            record["validated"] = conf
            return types.SimpleNamespace(uvicorn=conf.get("uvicorn", {}))

    cfg = types.ModuleType("tiled.config")
    cfg.Config = Config
    utils = types.ModuleType("tiled.utils")
    utils.parse = lambda path: record.setdefault("parsed_path", path) and parse_result
    serve = types.ModuleType("tiled.commandline._serve")
    serve._setup_log_config = lambda *a: {"log": a}
    app_mod = types.ModuleType("tiled.server.app")
    app_mod.build_app_from_config = lambda config: ("app", config)
    uvicorn = types.ModuleType("uvicorn")
    uvicorn.run = lambda app, **kw: record.update(run=(app, kw))
    for name, mod in {
        "tiled": tiled,
        "tiled.config": cfg,
        "tiled.utils": utils,
        "tiled.commandline": types.ModuleType("tiled.commandline"),
        "tiled.commandline._serve": serve,
        "tiled.server": types.ModuleType("tiled.server"),
        "tiled.server.app": app_mod,
        "uvicorn": uvicorn,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return record


def test_entry_rejects_version_mismatch(entry, monkeypatch, capsys):
    _install_fake_tiled(monkeypatch, version="0.2.19")
    rc = entry.main(["--expected-version", "0.2.18", "--instance", "n1", "--config", "x.yml"])
    assert rc == entry.EXIT_UNSUPPORTED_VERSION
    assert "installed=0.2.19 expected=0.2.18" in capsys.readouterr().err


def test_entry_requires_config_unless_compiling(entry, monkeypatch):
    _install_fake_tiled(monkeypatch)
    with pytest.raises(SystemExit):
        entry.main(["--expected-version", "0.2.18", "--instance", "n1"])


def test_entry_injects_secrets_from_files_and_never_prints_them(entry, monkeypatch, tmp_path, capsys):
    key = tmp_path / "key"
    key.write_text("API-KEY-VALUE\n")
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({"secret_keys": ["k1"], "provider": "toy", "users_to_passwords": {"u": "pw"}})
    )
    conf = {
        "uvicorn": {"host": "0.0.0.0", "port": "9001"},
        "authentication": {"providers": [{"provider": "toy"}, {"provider": "other"}]},
    }
    record = _install_fake_tiled(monkeypatch, parse_result=conf)
    rc = entry.main(
        [
            "--expected-version", "0.2.18", "--instance", "nonce", "--config", str(tmp_path / "c.yml"),
            "--api-key-file", str(key), "--authn-secrets-file", str(secrets),
        ]
    )
    assert rc == 0
    auth = record["validated"]["authentication"]
    assert auth["single_user_api_key"] == "API-KEY-VALUE"
    assert auth["secret_keys"] == ["k1"]
    assert auth["providers"][0]["args"]["users_to_passwords"] == {"u": "pw"}
    assert "args" not in auth["providers"][1]
    app, kwargs = record["run"]
    assert kwargs["host"] == "0.0.0.0" and kwargs["port"] == 9001
    err = capsys.readouterr().err
    assert "F2W_TILED_LAUNCH version=0.2.18 host=0.0.0.0 port=9001 instance=nonce" in err
    assert "API-KEY-VALUE" not in err and "pw" not in err.replace("F2W_TILED_LAUNCH", "")


def test_entry_defaults_host_port_and_reports_invalid_config_by_type_only(entry, monkeypatch, tmp_path, capsys):
    record = _install_fake_tiled(monkeypatch, parse_result={})
    assert entry.main(["--expected-version", "0.2.18", "--instance", "n", "--config", "c"]) == 0
    assert record["run"][1]["host"] == "127.0.0.1" and record["run"][1]["port"] == 8765
    _install_fake_tiled(monkeypatch, parse_result={}, valid=False)
    rc = entry.main(["--expected-version", "0.2.18", "--instance", "n", "--config", "c"])
    err = capsys.readouterr().err
    assert rc == entry.EXIT_CONFIG
    assert "F2W_TILED_ERROR config_invalid ValueError" in err and "SECRET-VALUE" not in err


def test_entry_compile_tags_branch_recreates_db(entry, monkeypatch, tmp_path):
    _install_fake_tiled(monkeypatch)
    calls = []

    class Compiler:
        def __init__(self, scopes, tags, db, groups):
            calls.append((scopes, tags, db, db["uri"]))
            self.connection = types.SimpleNamespace(close=lambda: calls.append("closed"))
            with pytest.raises(KeyError):
                groups("g")

        def load_tag_config(self):
            calls.append("load")

        def compile(self):
            calls.append("compile")

    pkg = types.ModuleType("tiled.access_control")
    tags = types.ModuleType("tiled.access_control.access_tags")
    tags.AccessTagsCompiler = Compiler
    scopes = types.ModuleType("tiled.access_control.scopes")
    scopes.ALL_SCOPES = {"read"}
    for n, m in {"tiled.access_control": pkg, "tiled.access_control.access_tags": tags,
                 "tiled.access_control.scopes": scopes}.items():
        monkeypatch.setitem(sys.modules, n, m)
    src = tmp_path / "tags.json"
    src.write_text('{"tag": 1}')
    db = tmp_path / "tags.sqlite"
    db.write_text("stale")
    rc = entry.main(["--expected-version", "0.2.18", "--instance", "n", "--compile-tags", str(src),
                     "--tags-db", str(db)])
    assert rc == 0
    assert not db.exists()
    assert calls[0] == ({"read"}, {"tag": 1}, {"uri": str(db)}, str(db))
    assert calls[1:] == ["load", "compile", "closed"]


def test_load_factory_validates_spec_and_imports():
    for bad in ("os:system", "app.modules.x", "app.modules.workers.factories:bad name", "app:thing"):
        with pytest.raises(SystemExit):
            worker_main.load_factory(bad)
    fn = worker_main.load_factory("app.modules.workers.factories:idle_service")
    assert callable(fn)


class _FakeExecutor:
    instances: list = []

    def __init__(self, service, max_concurrency):
        self.service, self.max_concurrency, self.events = service, max_concurrency, []
        self.clean = True
        _FakeExecutor.instances.append(self)

    def start(self):
        self.events.append("start")
        return self

    def run_until_idle(self):
        self.events.append("idle")

    def shutdown(self):
        self.events.append("shutdown")
        return self.clean


def test_worker_once_runs_until_idle_and_exit_code_reflects_shutdown(monkeypatch):
    _FakeExecutor.instances = []
    seen = {}
    monkeypatch.setattr(worker_main, "WorkerExecutor", _FakeExecutor)
    monkeypatch.setattr(worker_main, "load_factory", lambda spec: (lambda db: seen.update(db=db) or "svc"))
    assert worker_main.main(["--db", "/x.db", "--factory", "app.modules.a:b", "--once", "--concurrency", "3"]) == 0
    ex = _FakeExecutor.instances[0]
    assert (seen["db"], ex.service, ex.max_concurrency) == ("/x.db", "svc", 3)
    assert ex.events == ["start", "idle", "shutdown"]
    monkeypatch.setattr(_FakeExecutor, "shutdown", lambda self: False)
    assert worker_main.main(["--db", "/x.db", "--factory", "app.modules.a:b", "--once"]) == 1


def test_worker_signal_handler_stops_wait_loop(monkeypatch):
    import signal

    _FakeExecutor.instances = []
    handlers = {}
    monkeypatch.setattr(worker_main, "WorkerExecutor", _FakeExecutor)
    monkeypatch.setattr(worker_main, "load_factory", lambda spec: (lambda db: "svc"))
    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.__setitem__(sig, h))

    class AutoStop:
        def __init__(self):
            self._set = False

        def set(self):
            self._set = True

        def wait(self):
            assert set(handlers) == {signal.SIGTERM, signal.SIGINT}
            handlers[signal.SIGTERM](signal.SIGTERM, None)  # simulate delivery during wait

    monkeypatch.setattr(worker_main.threading, "Event", AutoStop)
    assert worker_main.main(["--db", "d", "--factory", "app.modules.a:b"]) == 0
    assert _FakeExecutor.instances[0].events == ["start", "shutdown"]

"""``f2w-tiled-server`` (app.modules.commands.tiled_server): flags, settings, each command and its error reporting.

The lifecycle (spawn, stop, upgrade) and the capability checks are faked, so no server starts and nothing connects;
the tests assert which settings reach them and what the operator sees.
"""
from __future__ import annotations

import json
from pathlib import Path
import types
from typing import Any

import pytest

from app.modules.commands import tiled_server as cli
from app.modules.deployment import tiled_capabilities as caps
from app.modules.deployment import tiled_lifecycle as lifecycle
from app.modules.deployment.tiled_config import TiledDeploymentError


class Report:
    def __init__(self, tag: str = "ok") -> None:
        self.tag = tag

    def to_dict(self) -> dict[str, Any]:
        return {"library_version": "0.2.18", "tag": self.tag}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("F2W_DEPLOYMENT_PROFILE", *cli.FLAG_TO_ENV.values()):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def managed_env(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    return {"F2W_DEPLOYMENT_PROFILE": "test", "F2W_TILED_DATA_DIR": str(data), "F2W_TILED_PYTHON": "/opt/tiled/bin/python",
            "F2W_TILED_PORT": "0", "UNRELATED": "dropped"}


def test_help_lists_every_command_and_flag(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.build_parser().parse_args(["--help"])
    assert stop.value.code == 0
    text = capsys.readouterr().out
    for word in ("start", "status", "stop", "check", "upgrade-catalog", "--timeout", "--writer-secret-file", "F2W_TILED_PORT"):
        assert word in text


def test_unknown_command_and_missing_command_exit_2(capsys):
    for argv in (["restart"], []):
        with pytest.raises(SystemExit) as stop:
            cli.build_parser().parse_args(argv)
        assert stop.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_flags_override_the_environment_and_unrelated_variables_are_not_forwarded():
    args = cli.build_parser().parse_args(["status", "--profile", "local", "--port", "7001", "--ca-bundle", "/ca.pem"])
    env = cli._settings_env(args, {"F2W_DEPLOYMENT_PROFILE": "test", "F2W_TILED_DATA_DIR": "/d", "F2W_TILED_PORT": "1",
                                   "HOME": "/h", "CBORG_API_KEY": "sk-x"})
    assert env == {"F2W_DEPLOYMENT_PROFILE": "local", "F2W_TILED_DATA_DIR": "/d", "F2W_TILED_PORT": "7001",
                   "F2W_TILED_CA_BUNDLE": "/ca.pem"}


def test_status_passes_the_managed_settings_to_the_lifecycle(monkeypatch, managed_env):
    seen = []
    monkeypatch.setattr(lifecycle, "managed_status", lambda cfg: seen.append(cfg) or {"state": "absent"})
    assert cli.run(["status"], environ=managed_env) == {"state": "absent"}
    cfg = seen[0]
    assert str(cfg.data_dir) == managed_env["F2W_TILED_DATA_DIR"] and str(cfg.tiled_python) == "/opt/tiled/bin/python"
    assert cfg.port == 0 and cfg.profile == "test"


def test_start_returns_url_pid_log_and_capabilities_and_passes_the_timeout(monkeypatch, managed_env, tmp_path):
    calls = []
    server = types.SimpleNamespace(url="http://127.0.0.1:5555", pid=42, log_path=tmp_path / "s.log",
                                   capabilities=Report())
    monkeypatch.setattr(lifecycle, "start_managed", lambda cfg, ready_timeout: calls.append((cfg, ready_timeout)) or server)
    out = cli.run(["start", "--timeout", "7.5"], environ=managed_env)
    assert out == {"state": "running", "url": "http://127.0.0.1:5555", "pid": 42, "log": str(tmp_path / "s.log"),
                   "capabilities": {"library_version": "0.2.18", "tag": "ok"}}
    assert calls[0][1] == 7.5
    server.capabilities = None
    assert cli.run(["start"], environ=managed_env)["capabilities"] is None
    assert calls[1][1] == 60.0


def test_stop_without_a_server_reports_the_state_and_with_one_stops_it(monkeypatch, managed_env):
    monkeypatch.setattr(lifecycle, "find_managed", lambda cfg: None)
    monkeypatch.setattr(lifecycle, "managed_status", lambda cfg: {"state": "stopped"})
    assert cli.run(["stop"], environ=managed_env) == {"state": "stopped", "stopped": False}
    found = object()
    monkeypatch.setattr(lifecycle, "find_managed", lambda cfg: found)
    monkeypatch.setattr(lifecycle, "stop_managed", lambda s: {"stopped": True, "who": s is found})
    assert cli.run(["stop"], environ=managed_env) == {"stopped": True, "who": True}


def test_upgrade_catalog_calls_the_explicit_upgrade(monkeypatch, managed_env):
    monkeypatch.setattr(lifecycle, "upgrade_catalog", lambda cfg: {"upgraded": True, "port": cfg.port})
    assert cli.run(["upgrade-catalog"], environ=managed_env) == {"upgraded": True, "port": 0}


def test_check_managed_requires_a_running_server(monkeypatch, managed_env):
    monkeypatch.setattr(lifecycle, "managed_status", lambda cfg: {"state": "stopped", "log_tail": "boom"})
    with pytest.raises(TiledDeploymentError) as err:
        cli.run(["check"], environ=managed_env)
    assert err.value.code == "target_unavailable" and "stopped" in str(err.value)


def test_check_managed_reads_the_writer_credential_in_process_not_from_argv(monkeypatch, managed_env):
    monkeypatch.setattr(lifecycle, "managed_status", lambda cfg: {"state": "running", "url": "http://127.0.0.1:1"})
    monkeypatch.setattr(lifecycle, "writer_credential", lambda cfg: "writer-key-value")
    got = {}
    monkeypatch.setattr(caps, "check_capabilities", lambda url, credential, expected_version: got.update(
        url=url, credential=credential, version=expected_version) or Report("managed"))
    out = cli.run(["check"], environ=managed_env)
    assert out == {"mode": "managed", "capabilities": {"library_version": "0.2.18", "tag": "managed"}}
    assert got["url"] == "http://127.0.0.1:1" and got["credential"] == "writer-key-value" and got["version"]


def test_check_external_connects_without_touching_the_lifecycle(monkeypatch):
    monkeypatch.setattr(lifecycle, "managed_status", lambda cfg: pytest.fail("external mode never manages"))
    seen = []
    monkeypatch.setattr(caps, "connect_external", lambda cfg: seen.append(cfg) or Report("ext"))
    out = cli.run(["check"], environ={"F2W_DEPLOYMENT_PROFILE": "als-dev"})
    assert out["mode"] == "external" and out["capabilities"]["tag"] == "ext"
    assert seen[0].profile == "als-dev"


@pytest.mark.parametrize("command", ["start", "status", "stop", "upgrade-catalog"])
def test_lifecycle_commands_are_refused_on_an_external_profile(command):
    with pytest.raises(TiledDeploymentError) as err:
        cli.run([command], environ={"F2W_DEPLOYMENT_PROFILE": "als-prod"})
    assert err.value.code == "not_managed" and "external" in str(err.value)


def test_check_without_any_target_is_not_managed(monkeypatch):
    settings = types.SimpleNamespace(external=None, managed=None, profile="x")
    monkeypatch.setattr(cli, "load_tiled_settings", lambda env: settings)
    with pytest.raises(TiledDeploymentError) as err:
        cli.run(["check"], environ={})
    assert err.value.code == "not_managed"


def test_a_secret_value_in_the_environment_is_refused_and_not_echoed(managed_env):
    with pytest.raises(TiledDeploymentError) as err:
        cli.run(["status"], environ={**managed_env, "F2W_TILED_API_KEY": "s3cret-value"})
    assert "F2W_TILED_WRITER_SECRET_FILE" in str(err.value) and "s3cret-value" not in str(err.value)


def test_main_prints_json_on_stdout_and_exits_0(monkeypatch, managed_env, capsys):
    for name, value in managed_env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(lifecycle, "managed_status", lambda cfg: {"state": "absent", "when": Path("/x")})
    assert cli.main(["status"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out) == {"state": "absent", "when": "/x"} and out.err == ""


def test_main_reports_errors_as_json_on_stderr_with_status_2(monkeypatch, capsys):
    monkeypatch.setenv("F2W_DEPLOYMENT_PROFILE", "als-prod")
    assert cli.main(["start"]) == 2
    err = capsys.readouterr().err
    body = json.loads(err.splitlines()[0])
    assert body["error"] == "not_managed" and body["detail"] == {} and len(err.splitlines()) == 1


def test_main_prints_the_log_tail_separately_from_the_json_detail(monkeypatch, managed_env, capsys):
    for name, value in managed_env.items():
        monkeypatch.setenv(name, value)

    def boom(cfg, ready_timeout):
        raise TiledDeploymentError("server exited", code="startup_failed",
                                   detail={"log_tail": "Traceback: port in use", "exit": 1})

    monkeypatch.setattr(lifecycle, "start_managed", boom)
    assert cli.main(["start"]) == 2
    lines = capsys.readouterr().err.splitlines()
    assert json.loads(lines[0]) == {"error": "startup_failed", "message": "server exited", "detail": {"exit": 1}}
    assert lines[1] == "Traceback: port in use"

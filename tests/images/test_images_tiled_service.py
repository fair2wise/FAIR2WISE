"""The ``:local`` managed-Tiled service under the supervisor (T24B).

The live tests start a real Tiled 0.2.18 through ``docker/entrypoint/tiled_service.py`` on an
ephemeral 127.0.0.1 port with a temporary data dir. They skip unless ``F2W_TILED_PYTHON``
names the isolated Tiled interpreter (``tests/fixtures/tiled_server.py``). The permanent
failure path needs no Tiled.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parents[2]
SUPERVISOR = REPO / "docker/entrypoint/f2w_supervisor.py"
TILED_SERVICE = REPO / "docker/entrypoint/tiled_service.py"
FAKE = Path(__file__).with_name("fake_service.py")
TILED_PYTHON = os.environ.get("F2W_TILED_PYTHON", "")
live = pytest.mark.skipif(not TILED_PYTHON, reason="set F2W_TILED_PYTHON to the isolated Tiled 0.2.18 interpreter")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_profile(tmp: Path, *, data_dir: Path, python: str, port: int) -> Path:
    local = json.loads((REPO / "docker/profiles/local.json").read_text(encoding="utf-8"))
    tiled = next(s for s in local["services"] if s["name"] == "tiled")
    tiled = dict(tiled, health=dict(tiled["health"], timeout=120, interval=0.5),
                 restart={"max": 1, "backoff": 0.2})
    locked = dict(local["locked_env"], F2W_TILED_DATA_DIR=str(data_dir), F2W_TILED_PYTHON=python,
                  F2W_TILED_PORT=str(port), PYTHONPATH=str(REPO), F2W_RESOURCE_ROOT=str(REPO))
    data = dict(local, cwd=str(REPO), locked_env=locked, preflight=[],
                services=[dict(tiled, argv=["@python", str(TILED_SERVICE)]),
                          {"name": "api", "argv": ["@python", str(FAKE), "--name", "api", "--log", str(tmp / "ev.log")],
                           "health": {"alive_for": 0.2}, "stop_timeout": 3}])
    path = tmp / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def start(tmp: Path, profile: Path) -> subprocess.Popen:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp), "TMPDIR": str(tmp),
           "F2W_SUPERVISOR_STATUS": str(tmp / "status.json")}
    return subprocess.Popen([sys.executable, str(SUPERVISOR), "--profile-file", str(profile)], env=env,
                            stdout=subprocess.DEVNULL, stderr=open(tmp / "sup.err", "w"))


def status(tmp: Path) -> dict:
    try:
        return json.loads((tmp / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def wait_for(predicate, timeout: float, proc: subprocess.Popen, tmp: Path) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    raise AssertionError((tmp / "sup.err").read_text(encoding="utf-8")[-4000:])


def test_wrong_tiled_environment_is_permanent_and_not_restarted(tmp_path):
    data = tmp_path / "tiled"
    data.mkdir()
    # The app interpreter has no tiled: a permanent environment error (exit 78), no restart.
    profile = write_profile(tmp_path, data_dir=data, python=sys.executable, port=free_port())
    proc = start(tmp_path, profile)
    try:
        wait_for(lambda: status(tmp_path).get("services", {}).get("tiled", {}).get("state") == "failed", 60,
                 proc, tmp_path)
        tiled = status(tmp_path)["services"]["tiled"]
        assert tiled["last_exit"] == 78 and tiled["restarts"] == 0
        assert proc.poll() is None  # the rest of the application keeps running
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert "refused code=" in (tmp_path / "sup.err").read_text(encoding="utf-8")
    assert not (data / "catalog.db").exists()


@live
def test_managed_tiled_starts_supervised_and_stops_on_sigterm_keeping_data(tmp_path):
    data = tmp_path / "tiled"
    data.mkdir()
    port = free_port()
    profile = write_profile(tmp_path, data_dir=data, python=TILED_PYTHON, port=port)
    proc = start(tmp_path, profile)
    try:
        wait_for(lambda: status(tmp_path).get("phase") == "ready", 150, proc, tmp_path)
        assert status(tmp_path)["services"]["tiled"]["state"] == "running"
        health = requests.get(f"http://127.0.0.1:{port}/healthz", timeout=5)
        assert health.status_code == 200 and health.json()["status"] == "ready"
        key = (data / "secrets" / "writer.key").read_text(encoding="utf-8")
        manifest = json.loads((data / "run" / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["state"] == "running" and manifest["port"] == port
        assert manifest["tiled_version"] == "0.2.18" and manifest["owner"] == "f2w-local"
        assert key not in (tmp_path / "sup.err").read_text(encoding="utf-8")

        # The Tiled process dies: the wrapper exits 1 and the supervisor restarts it once.
        os.killpg(manifest["pid"], signal.SIGKILL)
        wait_for(lambda: status(tmp_path).get("services", {}).get("tiled", {}).get("restarts") == 1
                 and status(tmp_path)["services"]["tiled"]["state"] == "running", 150, proc, tmp_path)
        assert (data / "secrets" / "writer.key").read_text(encoding="utf-8") == key  # survives restart

        proc.send_signal(signal.SIGTERM)
        assert proc.wait(60) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)
        manifest = json.loads((data / "run" / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("state") != "stopped" and manifest.get("pid"):
            try:
                os.killpg(manifest["pid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    manifest = json.loads((data / "run" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "stopped" and manifest["stop_signal"] == "SIGTERM"
    assert (data / "catalog.db").exists()  # data retained on the mount
    with pytest.raises(requests.ConnectionError):
        requests.get(f"http://127.0.0.1:{port}/healthz", timeout=2)

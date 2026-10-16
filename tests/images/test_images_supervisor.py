"""The image supervisor with fake child processes (T24B): order, health, signals, failures.

Each test writes a profile with fake services (``fake_service.py``), runs
``docker/entrypoint/f2w_supervisor.py`` as a real process and inspects the event log the
children write. Nothing here touches Docker, Tiled or the network beyond 127.0.0.1.
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
from typing import Any, Callable, Dict, List, Optional

import pytest

REPO = Path(__file__).resolve().parents[2]
SUPERVISOR = REPO / "docker" / "entrypoint" / "f2w_supervisor.py"
FAKE = Path(__file__).with_name("fake_service.py")

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie is not alive
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
        return bool(out.strip()) and not out.strip().startswith("Z")
    except OSError:
        return True


class Harness:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.log = tmp / "events.log"
        self.log.touch()
        self.status = tmp / "status.json"
        self.proc: Optional[subprocess.Popen] = None
        self.stderr_path = tmp / "supervisor.err"

    def service(self, name: str, *, port: Optional[int] = None, alive_for: float = 0.0, timeout: float = 10.0,
                extra: Optional[List[str]] = None, **fields: Any) -> Dict[str, Any]:
        argv = ["@python", str(FAKE), "--name", name, "--log", str(self.log)]
        if port:
            argv += ["--port", str(port)]
        argv += extra or []
        health = {"http": f"http://127.0.0.1:{port}/health"} if port else {"alive_for": alive_for or 0.3}
        health.update(timeout=timeout, interval=0.1)
        return {"name": name, "argv": argv, "health": health, "stop_timeout": 3, **fields}

    def profile(self, services: List[Dict[str, Any]], **fields: Any) -> Path:
        data = {"schema": "f2w.image.profile/v1", "profile": "local", "tiled_mode": "managed",
                "locked_env": {"F2W_DEPLOYMENT_PROFILE": "local"}, "services": services, **fields}
        path = self.tmp / "profile.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def start(self, profile: Path, env: Optional[Dict[str, str]] = None, args: Optional[List[str]] = None):
        base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp),
                "F2W_SUPERVISOR_STATUS": str(self.status)}
        base.update(env or {})
        self.err = open(self.stderr_path, "w", encoding="utf-8")
        argv = [sys.executable, str(SUPERVISOR), "--profile-file", str(profile), *(args or [])]
        self.proc = subprocess.Popen(argv,
                                     env=base, stdout=subprocess.PIPE, stderr=self.err, text=True)
        return self.proc

    def run(self, profile: Path, env: Optional[Dict[str, str]] = None, timeout: float = 30,
            args: Optional[List[str]] = None) -> int:
        self.start(profile, env, args)
        return self.wait(timeout)

    def wait(self, timeout: float = 30) -> int:
        assert self.proc is not None
        try:
            return self.proc.wait(timeout=timeout)
        finally:
            self.err.close()

    def events(self) -> List[List[str]]:
        return [line.split() for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def kinds(self) -> List[str]:
        return [f"{e[0]} {e[1]}" for e in self.events()]

    def stderr(self) -> str:
        return self.stderr_path.read_text(encoding="utf-8")

    def until(self, predicate: Callable[[], bool], timeout: float = 15.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            if self.proc is not None and self.proc.poll() is not None:
                break
            time.sleep(0.05)
        raise AssertionError(f"condition not met; events={self.kinds()} stderr={self.stderr()[-2000:]}")

    def ready(self) -> bool:
        return "f2w-supervisor: ready" in self.stderr()

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(5)
        for e in self.events():
            try:
                os.killpg(int(e[2]), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, ValueError):
                pass


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.kill()


def test_starts_in_order_after_health_and_stops_in_reverse_on_sigterm(h):
    ports = [free_port(), free_port()]
    profile = h.profile([
        h.service("tiled", port=ports[0], required=False, extra=["--ready-delay", "0.6"]),
        h.service("api", port=ports[1], extra=["--ready-delay", "0.3"]),
        h.service("worker", alive_for=0.4),
        h.service("gateway", alive_for=0.2, stop_signal="SIGQUIT"),
    ])
    h.start(profile)
    h.until(h.ready)
    assert h.kinds() == ["start tiled", "start api", "start worker", "start gateway"]
    status = json.loads(h.status.read_text(encoding="utf-8"))
    assert status["phase"] == "ready"
    assert {k: v["state"] for k, v in status["services"].items()} == dict.fromkeys(
        ["tiled", "api", "worker", "gateway"], "running")
    h.proc.send_signal(signal.SIGTERM)
    assert h.wait() == 0
    stops = [k for k in h.kinds() if k.startswith("signal")]
    assert stops == [f"signal{int(signal.SIGQUIT)} gateway", f"signal{int(signal.SIGTERM)} worker",
                     f"signal{int(signal.SIGTERM)} api", f"signal{int(signal.SIGTERM)} tiled"]
    assert all(not pid_alive(int(e[2])) for e in h.events())
    assert json.loads(h.status.read_text(encoding="utf-8"))["phase"] == "stopped"


def test_next_service_waits_for_the_previous_health_check(h):
    port = free_port()
    profile = h.profile([h.service("api", port=port, extra=["--ready-delay", "1.0"]),
                         h.service("worker", alive_for=0.2)])
    h.start(profile)
    h.until(lambda: "start api" in h.kinds())
    time.sleep(0.6)
    assert "start worker" not in h.kinds()  # api not healthy yet
    h.until(h.ready)
    assert h.kinds() == ["start api", "start worker"]
    h.proc.send_signal(signal.SIGINT)
    assert h.wait() == 0


def test_required_child_death_stops_everything_and_exits_nonzero(h):
    profile = h.profile([h.service("api", alive_for=0.2),
                         h.service("worker", alive_for=0.2, extra=["--die-after", "1.0", "--exit-code", "9"]),
                         h.service("gateway", alive_for=0.2)])
    assert h.run(profile) == 70
    kinds = h.kinds()
    assert "die worker" in kinds
    assert f"signal{int(signal.SIGTERM)} gateway" in kinds and f"signal{int(signal.SIGTERM)} api" in kinds
    assert 'required_child_exited service=worker exit=9' in h.stderr()
    assert all(not pid_alive(int(e[2])) for e in h.events())
    status = json.loads(h.status.read_text(encoding="utf-8"))
    assert status["services"]["worker"]["last_exit"] == 9


def test_required_health_timeout_is_a_startup_failure(h):
    port = free_port()
    profile = h.profile([h.service("api", port=port, timeout=1.0, extra=["--ready-delay", "30"]),
                         h.service("worker", alive_for=0.2)])
    assert h.run(profile) == 71
    assert "start worker" not in h.kinds()
    assert "health_timeout service=api" in h.stderr()


def test_optional_child_is_restarted_with_backoff_then_marked_failed(h):
    profile = h.profile([
        h.service("tiled", alive_for=0.2, required=False, restart={"max": 2, "backoff": 0.1},
                  extra=["--die-after", "0.5", "--exit-code", "1"]),
        h.service("api", alive_for=0.2),
    ])
    h.start(profile)
    h.until(lambda: "service_failed service=tiled" in h.stderr(), timeout=20)
    assert h.kinds().count("start tiled") == 3  # first start + 2 restarts
    assert h.proc.poll() is None  # the application keeps running without Tiled
    status = json.loads(h.status.read_text(encoding="utf-8"))
    assert status["services"]["tiled"]["state"] == "failed"
    assert status["services"]["api"]["state"] == "running"
    h.proc.send_signal(signal.SIGTERM)
    assert h.wait() == 0


def test_optional_child_permanent_exit_code_is_not_restarted(h):
    profile = h.profile([
        h.service("tiled", alive_for=0.2, required=False, restart={"max": 3, "backoff": 0.1},
                  no_restart_exit_codes=[78], extra=["--die-after", "0.4", "--exit-code", "78"]),
        h.service("api", alive_for=0.2),
    ])
    h.start(profile)
    h.until(lambda: "service_failed service=tiled exit=78" in h.stderr())
    time.sleep(0.5)
    assert h.kinds().count("start tiled") == 1
    h.proc.send_signal(signal.SIGTERM)
    assert h.wait() == 0


def test_child_ignoring_sigterm_is_killed_and_the_stop_is_unclean(h):
    profile = h.profile([h.service("api", alive_for=0.2, stop_timeout=0.5, extra=["--on-term", "ignore"])])
    h.start(profile)
    h.until(h.ready)
    pid = int(h.events()[0][2])
    h.proc.send_signal(signal.SIGTERM)
    assert h.wait() == 1
    assert "stop_timeout_kill service=api" in h.stderr()
    assert not pid_alive(pid)


def test_worker_unclean_exit_on_stop_is_reported(h):
    profile = h.profile([h.service("worker", alive_for=0.2, extra=["--on-term", "code:1"])])
    h.start(profile)
    h.until(h.ready)
    h.proc.send_signal(signal.SIGTERM)
    assert h.wait() == 1


def test_process_group_is_reaped_with_its_leader(h):
    pidfile = h.tmp / "grandchild.pid"
    profile = h.profile([h.service("api", alive_for=0.2, extra=["--grandchild", str(pidfile)])])
    h.start(profile)
    h.until(lambda: h.ready() and pidfile.exists() and pidfile.read_text().strip() != "")
    grandchild = int(pidfile.read_text())
    assert pid_alive(grandchild)
    h.proc.send_signal(signal.SIGTERM)
    h.wait()
    end = time.monotonic() + 5
    while pid_alive(grandchild) and time.monotonic() < end:
        time.sleep(0.05)
    assert not pid_alive(grandchild)


def test_preflight_failure_starts_nothing(h):
    profile = h.profile([h.service("api", alive_for=0.2)],
                        preflight=[["@python", "-c", "import sys; sys.exit(5)"]])
    assert h.run(profile) == 78
    assert h.kinds() == []
    assert json.loads(h.status.read_text(encoding="utf-8"))["phase"] == "refused"


def test_preflight_runs_with_the_resolved_environment(h):
    out = h.tmp / "preflight-env.json"
    profile = h.profile([h.service("api", alive_for=0.2)],
                        preflight=[["@python", "-c",
                                    f"import json, os; json.dump(dict(os.environ), open({str(out)!r}, 'w'))"]],
                        env={"F2W_WORKDIR": "/state/session"})
    h.start(profile)
    h.until(h.ready)
    env = json.loads(out.read_text(encoding="utf-8"))
    assert env["F2W_DEPLOYMENT_PROFILE"] == "local" and env["F2W_DOTENV"] == "off"
    assert env["F2W_WORKDIR"] == "/state/session"
    assert env["F2W_PROFILE_FILE"] == str(profile)
    h.proc.send_signal(signal.SIGTERM)
    assert h.wait() == 0


# --------------------------------------------------------------------------- profile/env


def _env_profile(h, dump: Path, **fields):
    return h.profile([h.service("api", alive_for=0.2, extra=["--dump-env", str(dump)])], **fields)


def test_children_get_the_fixed_profile_and_no_legacy_tiled_settings(h):
    dump = h.tmp / "env.json"
    profile = _env_profile(h, dump, locked_env={"F2W_DEPLOYMENT_PROFILE": "local", "F2W_TILED_MODE": "managed"},
                           stripped_env=["TILED_URI", "TILED_API_KEY", "F2W_LIVE_TILED"],
                           env={"F2W_WORKER_CONCURRENCY": "2"})
    h.start(profile, env={"TILED_URI": "https://tiled.als.lbl.gov", "TILED_API_KEY": "not-a-real-key",
                          "F2W_LIVE_TILED": "1", "F2W_WORKER_CONCURRENCY": "4"})
    h.until(lambda: h.ready() and dump.exists())
    env = json.loads(dump.read_text(encoding="utf-8"))
    assert "TILED_URI" not in env and "TILED_API_KEY" not in env and "F2W_LIVE_TILED" not in env
    assert env["F2W_DEPLOYMENT_PROFILE"] == "local" and env["F2W_TILED_MODE"] == "managed"
    assert env["F2W_WORKER_CONCURRENCY"] == "4"  # defaults never override the operator
    assert env["F2W_DOTENV"] == "off"
    assert "not-a-real-key" not in h.stderr()
    h.proc.send_signal(signal.SIGTERM)
    assert h.wait() == 0


@pytest.mark.parametrize("env, reason", [
    ({"F2W_DEPLOYMENT_PROFILE": "als-prod"}, "F2W_DEPLOYMENT_PROFILE='als-prod' is not allowed"),
    ({"F2W_TILED_MODE": "external"}, "F2W_TILED_MODE='external' is not allowed"),
    ({"F2W_TILED_URL": "https://tiled.als.lbl.gov"}, "refuses these settings: F2W_TILED_URL"),
])
def test_profile_cannot_be_switched_by_environment(h, env, reason):
    profile = h.profile([h.service("api", alive_for=0.2)],
                        locked_env={"F2W_DEPLOYMENT_PROFILE": "local", "F2W_TILED_MODE": "managed"},
                        forbidden_env=["F2W_TILED_URL"])
    assert h.run(profile, env=env) == 78
    assert h.kinds() == []
    assert reason in h.stderr()


def test_equal_locked_values_are_accepted(h):
    profile = h.profile([h.service("api", alive_for=0.2)],
                        locked_env={"F2W_DEPLOYMENT_PROFILE": "local", "F2W_TILED_URL": "https://x.example"})
    h.start(profile, env={"F2W_DEPLOYMENT_PROFILE": "local", "F2W_TILED_URL": "https://x.example/"})
    h.until(h.ready)
    h.proc.send_signal(signal.SIGTERM)
    assert h.wait() == 0


def test_explicit_dotenv_is_loaded_once_and_cannot_switch_the_profile(h):
    dump = h.tmp / "env.json"
    dotenv = h.tmp / "runtime.env"
    dotenv.write_text("CBORG_API_KEY=from-file\nF2W_MODEL=from-file\n", encoding="utf-8")
    profile = _env_profile(h, dump)
    h.start(profile, env={"F2W_DOTENV": str(dotenv), "F2W_MODEL": "from-env"})
    h.until(lambda: h.ready() and dump.exists())
    env = json.loads(dump.read_text(encoding="utf-8"))
    assert env["CBORG_API_KEY"] == "from-file"
    assert env["F2W_MODEL"] == "from-env"  # never overrides the process environment
    assert env["F2W_DOTENV"] == "off"  # children never load a dotenv themselves
    assert "from-file" not in h.stderr()
    h.proc.send_signal(signal.SIGTERM)
    assert h.wait() == 0

    bad = h.tmp / "switch.env"
    bad.write_text("F2W_DEPLOYMENT_PROFILE=als-dev\n", encoding="utf-8")
    (h.tmp / "second").mkdir()
    h2 = Harness(h.tmp / "second")
    try:
        assert h2.run(h2.profile([h2.service("api", alive_for=0.2)]), env={"F2W_DOTENV": str(bad)}) == 78
        assert h2.kinds() == []
    finally:
        h2.kill()


def test_no_dotenv_is_read_implicitly(h):
    dump = h.tmp / "env.json"
    workdir = h.tmp / "cwd"
    workdir.mkdir()
    (workdir / ".env").write_text("F2W_IMPLICIT=1\n", encoding="utf-8")
    (h.tmp / ".env").write_text("F2W_IMPLICIT=1\n", encoding="utf-8")
    profile = _env_profile(h, dump, cwd=str(workdir))
    h.start(profile)
    h.until(lambda: h.ready() and dump.exists())
    assert "F2W_IMPLICIT" not in json.loads(dump.read_text(encoding="utf-8"))
    h.proc.send_signal(signal.SIGTERM)
    assert h.wait() == 0


def test_missing_explicit_dotenv_is_refused(h):
    profile = h.profile([h.service("api", alive_for=0.2)])
    assert h.run(profile, env={"F2W_DOTENV": str(h.tmp / "absent.env")}) == 78
    assert "is not a file" in h.stderr()


def test_invalid_profiles_are_refused(h):
    good = h.service("api", alive_for=0.2)
    cases = [
        {"schema": "other"},
        {"locked_env": {}},
        {"services": [good, good]},
        {"services": [dict(good, restart={"max": 2})]},  # required services are not restarted
        {"services": [dict(good, health={})]},
        {"env": {"F2W_TILED_URL": "x"}, "forbidden_env": ["F2W_TILED_URL"]},
    ]
    for fields in cases:
        data = {"schema": "f2w.image.profile/v1", "profile": "local",
                "locked_env": {"F2W_DEPLOYMENT_PROFILE": "local"}, "services": [good], **fields}
        path = h.tmp / "bad.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        done = subprocess.run([sys.executable, str(SUPERVISOR), "--profile-file", str(path), "--check"],
                              capture_output=True, text=True, env={"PATH": os.environ.get("PATH", "")})
        assert done.returncode == 78, fields

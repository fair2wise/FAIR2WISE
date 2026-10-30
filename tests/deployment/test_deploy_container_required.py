"""Container-level checks for T24E that need a built image and a running Docker daemon.

Every test here is skipped unless ``F2W_CONTAINER_TESTS=1`` is set, because building and
running the images locally is pending owner decision D3.11. The bodies are the exact
procedure ``tests/deployment/README.md`` lists; they have not been executed yet and should
be treated as the first thing to run (and fix) once D3.11 is approved. Nothing here touches
an ALS host: ALS images are only started to check startup refusals and the gateway, and
checks that need the central Tiled (reader/admin sessions) are owner-run and documented
only.

Required environment when enabled::

    F2W_CONTAINER_TESTS=1
    F2W_IMAGE_LOCAL=ghcr.io/fair2wise/fair2wise:local       # or a local build tag
    F2W_IMAGE_ALS_DEV=ghcr.io/fair2wise/fair2wise:als-dev
    F2W_CONTAINER_PORT=18080                                 # free loopback port to publish on
    F2W_CONTAINER_SHARED_DIR=$HOME/.cache/f2w-container-tests  # optional: a directory the Docker VM can bind-mount
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Iterator, List

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("F2W_CONTAINER_TESTS"),
    reason="container-level T24E checks run only with F2W_CONTAINER_TESTS=1 (owner decision D3.11)",
)

REPO = Path(__file__).resolve().parents[2]
IMAGE_LOCAL = os.environ.get("F2W_IMAGE_LOCAL", "ghcr.io/fair2wise/fair2wise:local")
IMAGE_ALS_DEV = os.environ.get("F2W_IMAGE_ALS_DEV", "ghcr.io/fair2wise/fair2wise:als-dev")
PORT = int(os.environ.get("F2W_CONTAINER_PORT", "18080"))
FORBIDDEN_PORTS = {8011, 8765, 8002, 8004, 5057, 8080}
BASE = f"http://127.0.0.1:{PORT}/fair2wise"


def docker(*args: str, check: bool = True, timeout: float = 300) -> subprocess.CompletedProcess:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not on PATH")
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=timeout)


def get(url: str, timeout: float = 5.0) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def wait_http(url: str, deadline: float = 240.0) -> None:
    end = time.monotonic() + deadline
    last = None
    while time.monotonic() < end:
        try:
            status, _h, _b = get(url, timeout=2.0)
            if status == 200:
                return
            last = status
        except Exception as exc:  # noqa: BLE001 - connection refused while starting
            last = exc
        time.sleep(1.0)
    raise AssertionError(f"{url} not healthy after {deadline}s: {last!r}")


@pytest.fixture(scope="module")
def volumes() -> Iterator[dict]:
    assert PORT not in FORBIDDEN_PORTS, "pick a port that is not one of the developer's own servers"
    names = {"state": f"f2w-t24e-state-{os.getpid()}", "tiled": f"f2w-t24e-tiled-{os.getpid()}"}
    for name in names.values():
        docker("volume", "create", name)
    try:
        yield names
    finally:
        for name in names.values():
            docker("volume", "rm", "-f", name, check=False)


@pytest.fixture
def local_container(volumes) -> Iterator[str]:
    name = f"f2w-t24e-local-{os.getpid()}"
    docker("run", "-d", "--name", name, "-p", f"127.0.0.1:{PORT}:8080", "--stop-timeout", "120",
           "-v", f"{volumes['state']}:/var/lib/fair2wise/state", "-v", f"{volumes['tiled']}:/var/lib/fair2wise/tiled",
           IMAGE_LOCAL)
    try:
        wait_http(f"{BASE}/healthz")
        yield name
    finally:
        docker("rm", "-f", name, check=False)


# ---- DEP-07 / DEP-22: UI through the gateway ------------------------------------------------
def test_ui_and_assets_are_served_under_the_prefix_through_the_real_gateway(local_container) -> None:
    status, headers, body = get(f"{BASE}/")
    assert status == 200 and headers.get("Content-Type", "").startswith("text/html")
    assert b"/fair2wise/assets/" in body, "the bundle was built for /fair2wise"
    status, _h, _b = get(f"{BASE}/api/health")
    assert status == 200
    status, _h, _b = get(f"{BASE}/assets/does-not-exist.js")
    assert status == 404, "a missing asset is never index.html"
    status, headers, _b = get(f"http://127.0.0.1:{PORT}/fair2wise")
    assert status in (200, 301) and (status == 200 or headers.get("Location") == "/fair2wise/")


# ---- DEP-08: SSE through the gateway (nginx) --------------------------------------------------
def test_sse_streams_through_nginx_with_one_terminal_event(local_container) -> None:
    """A chat stream through the real nginx: first event arrives before the stream ends, exactly
    one terminal event. Uses the API's own stream endpoint with a prompt that needs no model
    (the T18C closure harness documents which one); adjust the request body if the route changed."""
    req = urllib.request.Request(f"{BASE}/api/chat/stream", data=json.dumps({"message": "ping"}).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    events: List[str] = []
    with urllib.request.urlopen(req, timeout=120) as resp:
        assert resp.headers.get("Content-Type", "").startswith("text/event-stream")
        assert resp.headers.get("X-Accel-Buffering", "no") == "no"
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
    assert events, "no SSE event arrived"
    terminal = [e for e in events if e in ("complete", "error", "end")]
    assert len(terminal) == 1, events


# ---- DEP-10 / DEP-18 / DEP-20: restart and named-volume persistence ---------------------------
def test_restart_keeps_state_and_tiled_volumes_and_the_supervisor_stops_in_order(local_container, volumes) -> None:
    status = json.loads(docker("exec", local_container, "cat", "/run/fair2wise/supervisor.json").stdout)
    assert status["phase"] in ("ready", "degraded")
    required = {n for n, s in status["services"].items() if s["required"]}
    assert {"api", "worker", "gateway"} <= required and all(status["services"][n]["state"] == "running" for n in required)
    marker = docker("exec", local_container, "sh", "-c",
                    "ls -la /var/lib/fair2wise/tiled/secrets/writer.key && stat -c %Y /var/lib/fair2wise/tiled/secrets/writer.key").stdout
    stop = docker("stop", "-t", "120", local_container, timeout=200)
    assert stop.returncode == 0
    logs = docker("logs", local_container).stderr + docker("logs", local_container).stdout
    assert "f2w-supervisor" in logs and "phase=stopped" in logs or "stopped" in logs
    inspect = json.loads(docker("inspect", local_container).stdout)[0]
    assert inspect["State"]["ExitCode"] == 0, "clean reverse-order stop"
    docker("start", local_container)
    wait_http(f"{BASE}/healthz")
    marker_after = docker("exec", local_container, "sh", "-c",
                          "ls -la /var/lib/fair2wise/tiled/secrets/writer.key && stat -c %Y /var/lib/fair2wise/tiled/secrets/writer.key").stdout
    assert marker_after == marker, "the generated writer key persisted across restart (named volume)"


# ---- DEP-17: backup/restore of the named volume with the host tool --------------------------------
def test_state_volume_backup_and_restore_round_trip_with_the_host_tool(local_container, volumes, tmp_path) -> None:
    tool = REPO / "deploy" / "scripts" / "f2w_state_archive.py"
    docker("stop", "-t", "120", local_container, timeout=200)
    # Read the volume through a disposable helper container into a host directory, then archive it.
    host_copy = tmp_path / "state"
    host_copy.mkdir()
    docker("run", "--rm", "-v", f"{volumes['state']}:/src:ro", "-v", f"{host_copy}:/dst", "alpine:3.20",
           "sh", "-c", "cp -a /src/. /dst/")
    archive = tmp_path / "state.tar.gz"
    proc = subprocess.run(["python3", "-I", str(tool), "backup", "--state-root", str(host_copy), "--archive",
                           str(archive), "--profile", "local"], capture_output=True, text=True, check=True)
    assert json.loads(proc.stdout)["result"] == "ok"
    restored = tmp_path / "restored"
    subprocess.run(["python3", "-I", str(tool), "restore", "--archive", str(archive), "--destination", str(restored),
                    "--expected-profile", "local"], check=True)
    new_volume = f"{volumes['state']}-restored"
    docker("volume", "create", new_volume)
    try:
        docker("run", "--rm", "-v", f"{restored}:/src:ro", "-v", f"{new_volume}:/dst", "alpine:3.20",
               "sh", "-c", "cp -a /src/. /dst/ && chown -R 10001:10001 /dst")
        name = f"{local_container}-restored"
        docker("run", "-d", "--name", name, "-p", f"127.0.0.1:{PORT + 1}:8080", "--stop-timeout", "120",
               "-v", f"{new_volume}:/var/lib/fair2wise/state", "-v", f"{volumes['tiled']}:/var/lib/fair2wise/tiled",
               IMAGE_LOCAL)
        try:
            wait_http(f"http://127.0.0.1:{PORT + 1}/fair2wise/healthz")
            status, _h, _b = get(f"http://127.0.0.1:{PORT + 1}/fair2wise/api/health")
            assert status == 200, "the container runs on the restored state"
        finally:
            docker("rm", "-f", name, check=False)
    finally:
        docker("volume", "rm", "-f", new_volume, check=False)


# ---- DEP-16: ALS image startup contract (no host, no central Tiled contacted) ----------------------
def test_als_image_refuses_to_start_without_state_mount_and_cors_origins() -> None:
    proc = docker("run", "--rm", IMAGE_ALS_DEV, check=False, timeout=300)
    assert proc.returncode == 78, proc.stderr[-2000:]
    assert "F2W_CORS_ORIGINS" in proc.stderr and "/var/lib/fair2wise/state" in proc.stderr


def test_als_image_refuses_a_group_readable_writer_secret(tmp_path, volumes) -> None:
    # A bind mount only works from a directory the Docker VM can see. Colima shares $HOME and /tmp/colima,
    # not the macOS temp dir, so F2W_CONTAINER_SHARED_DIR (a directory under $HOME) overrides tmp_path.
    shared = os.environ.get("F2W_CONTAINER_SHARED_DIR")
    base = Path(shared) / f"secret-{os.getpid()}" if shared else tmp_path
    base.mkdir(parents=True, exist_ok=True)
    secret = base / "writer.secret"
    secret.write_text("not-a-real-secret\n", encoding="utf-8")
    secret.chmod(0o640)
    proc = docker("run", "--rm", "-v", f"{volumes['state']}:/var/lib/fair2wise/state",
                  "-v", f"{secret}:/run/secrets/tiled_writer:ro", "-e", "F2W_TILED_WRITER_SECRET_FILE=/run/secrets/tiled_writer",
                  "-e", "F2W_CORS_ORIGINS=https://ui.example.invalid", IMAGE_ALS_DEV, check=False, timeout=300)
    assert proc.returncode == 78 and "group- or world-accessible" in proc.stderr


def test_als_image_rejects_local_trusted_from_the_environment() -> None:
    proc = docker("run", "--rm", "-e", "F2W_AUTHORIZATION_MODE=local_trusted", IMAGE_ALS_DEV, check=False, timeout=300)
    assert proc.returncode == 78

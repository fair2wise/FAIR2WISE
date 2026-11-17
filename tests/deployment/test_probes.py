"""``app.api.probes``: liveness, readiness and their checks, in isolation (no app, no network)."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.api import probes


def _inner() -> Starlette:
    return Starlette(routes=[Route("/other", lambda r: PlainTextResponse("inner"))])


def test_other_paths_and_methods_pass_through(tmp_path):
    client = TestClient(probes.ProbeMiddleware(_inner(), workdir=tmp_path, environ={}))
    assert client.get("/other").text == "inner"
    assert client.post("/livez").status_code in (404, 405)  # not a probe: the app decides


def test_livez_is_cheap_and_ignores_every_dependency():
    def boom(*_):
        raise AssertionError("liveness must not run readiness checks")

    response = TestClient(probes.ProbeMiddleware(_inner(), check=boom)).get("/livez")
    assert response.status_code == 200 and response.json() == {"status": "live"}


def test_root_path_prefix_is_accepted(tmp_path):
    middleware = probes.ProbeMiddleware(_inner(), workdir=tmp_path, environ={"F2W_READYZ_CHECKS": "state"})
    client = TestClient(middleware, root_path="/fair2wise/api")
    assert client.get("/fair2wise/api/livez").status_code == 200


def test_state_check_needs_a_writable_directory(tmp_path):
    assert probes.check_state(tmp_path) is True
    assert probes.check_state(tmp_path / "missing") is False
    if os.geteuid() != 0:
        ro = tmp_path / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        try:
            assert probes.check_state(ro) is False
        finally:
            ro.chmod(0o700)


def test_worker_check_reads_the_supervisor_status_file(tmp_path):
    status = tmp_path / "s.json"
    env = {"F2W_SUPERVISOR_STATUS": str(status)}
    assert probes.check_worker(env) is None  # no supervisor: skipped
    status.write_text("not json", encoding="utf-8")
    assert probes.check_worker(env) is False
    status.write_text(json.dumps({"services": {"worker": {"state": "running", "pid": os.getpid()}}}), encoding="utf-8")
    assert probes.check_worker(env) is True
    status.write_text(json.dumps({"services": {"worker": {"state": "running", "pid": 2 ** 22 + 12345}}}),
                      encoding="utf-8")
    assert probes.check_worker(env) is False  # stale pid
    status.write_text(json.dumps({"services": {}}), encoding="utf-8")
    assert probes.check_worker(env) is False
    status.write_text(json.dumps({"services": {"worker": {"state": "backoff", "pid": os.getpid()}}}),
                      encoding="utf-8")
    assert probes.check_worker(env) is False


class _Healthz(BaseHTTPRequestHandler):
    code = 200

    def do_GET(self):  # noqa: N802
        self.send_response(self.code)
        self.end_headers()
        self.wfile.write(b'{"status":"ready"}')

    def log_message(self, *args):
        pass


def _serve(code):
    server = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (_Healthz,), {"code": code}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_tiled_check_uses_loopback_without_a_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")  # an egress proxy must never see the probe
    monkeypatch.setenv("NO_PROXY", "")
    assert probes.check_tiled({"F2W_TILED_MODE": "external"}) is None
    server = _serve(200)
    try:
        assert probes.check_tiled({"F2W_TILED_MODE": "managed", "F2W_TILED_PORT": str(server.server_address[1])})
    finally:
        server.shutdown()
        server.server_close()
    broken = _serve(503)
    try:
        port = str(broken.server_address[1])
        assert probes.check_tiled({"F2W_TILED_MODE": "managed", "F2W_TILED_PORT": port}) is False
    finally:
        broken.shutdown()
        broken.server_close()
    assert probes.check_tiled({"F2W_TILED_MODE": "managed", "F2W_TILED_PORT": "1"}) is False
    assert probes.check_tiled({"F2W_TILED_MODE": "managed", "F2W_TILED_PORT": "x"}) is False


def test_readiness_defaults_and_selection(tmp_path):
    assert probes.selected_checks({}) == ("state", "worker", "tiled")
    assert probes.selected_checks({"F2W_READYZ_CHECKS": " State , worker "}) == ("state", "worker")
    out = probes.readiness({"F2W_SUPERVISOR_STATUS": str(tmp_path / "none")}, tmp_path)
    assert out == {"state": "ok", "worker": "skipped", "tiled": "skipped"}
    assert Path(tmp_path).exists()

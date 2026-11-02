"""T20B: capability checks and lifecycle guards against fakes (no Tiled install needed).

A stdlib HTTP server on an ephemeral loopback port imitates a Tiled service, so failure
classification (wrong version, missing graph, missing operation, denied writer, outage)
is exercised in the default suite.
"""
import json
import os
import socket
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.modules.deployment import tiled_capabilities as caps
from app.modules.deployment import tiled_config as tc
from app.modules.deployment import tiled_lifecycle as lc

REPO = Path(__file__).resolve().parents[2]

MUTATIONS = ["createEntity", "createLink", "updateEntity", "deleteEntity", "deleteLink", "updateLink"]
QUERIES = ["entity", "entities", "link", "links", "namespaces"]


class FakeTiled:
    """Configurable imitation of the 0.2.18 endpoints used by the checks."""

    def __init__(self):
        self.version = "0.2.18"
        self.graph = True
        self.mutations = list(MUTATIONS)
        self.providers = []
        self.valid_key = "goodkey"
        self.status_override = None
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorized(self):
                return self.headers.get("Authorization") == f"Apikey {fake.valid_key}"

            def do_GET(self):
                fake.requests.append(("GET", self.path, self.headers.get("Authorization")))
                if fake.status_override:
                    return self._send(fake.status_override, {"detail": "boom"})
                if self.path == "/healthz":
                    return self._send(200, {"status": "ready"})
                if self.path == "/api/v1/":
                    return self._send(200, {"library_version": fake.version, "api_version": 0,
                                            "authentication": {"required": True, "links": None,
                                                               "providers": fake.providers}})
                if self.path == "/api/v1/metadata/":
                    return self._send(200 if self._authorized() else 401, {})
                return self._send(404, {"detail": "Not Found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.requests.append(("POST", self.path, self.headers.get("Authorization"), body.get("query")))
                if fake.status_override:
                    return self._send(fake.status_override, {"detail": "boom"})
                if self.path != "/api/graphql" or not fake.graph:
                    return self._send(404, {"detail": "Not Found"})
                query = body.get("query") or ""
                if "__type(" in query:
                    name = body["variables"]["n"]
                    fields = {"Mutation": fake.mutations, "Query": QUERIES,
                              "UpdateEntityInput": ["name", "uri", "entityType", "nodeId", "accessBlob"],
                              "UpdateLinkInput": ["predicate", "accessBlob"]}[name]
                    key = "inputFields" if name.endswith("Input") else "fields"
                    return self._send(200, {"data": {"__type": {key: [{"name": f} for f in fields]}}})
                if "__typename" in query:
                    return self._send(200, {"data": {"__typename": "Query"}})
                if "entities" in query:
                    return self._send(200, {"data": {"entities": []}})
                return self._send(200, {"errors": [{"message": "unexpected"}]})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake():
    server = FakeTiled()
    yield server
    server.close()


def test_capabilities_pass_and_are_read_only(fake):
    report = caps.check_capabilities(fake.url, credential="goodkey")
    assert report.library_version == "0.2.18"
    assert report.writer_principal == "single-user-api-key"
    assert report.entity_properties_updatable is False and report.link_properties_updatable is False
    assert report.read_probe == "empty"
    assert "update-requires-accessBlob" in report.quirks
    assert "goodkey" not in json.dumps(report.to_dict())
    queries = [r[3] for r in fake.requests if r[0] == "POST"]
    assert not any("mutation" in (q or "").lower() for q in queries)
    # C1-C3 are unsigned; only the writer probe and read probe carry the credential.
    assert [r[1] for r in fake.requests if r[2]] == ["/api/v1/metadata/", "/api/graphql"]


def test_unsupported_version_is_a_clear_error(fake):
    fake.version = "0.2.19"
    with pytest.raises(caps.TiledUnsupportedVersion) as err:
        caps.check_capabilities(fake.url)
    assert err.value.code == "unsupported_version"
    assert "0.2.19" in str(err.value) and "0.2.18" in str(err.value)


def test_missing_graph_and_missing_operation(fake):
    fake.graph = False
    with pytest.raises(caps.TiledGraphUnavailable) as err:
        caps.check_capabilities(fake.url)
    assert err.value.code == "graph_unavailable"
    fake.graph = True
    fake.mutations = ["createEntity", "createLink"]
    with pytest.raises(caps.TiledCapabilityMissing) as err:
        caps.check_capabilities(fake.url)
    assert err.value.code == "capability_missing:updateEntity"
    assert set(err.value.detail["missing"]) == {"updateEntity", "deleteEntity", "deleteLink"}


def test_denied_writer_and_unverifiable_role(fake):
    with pytest.raises(caps.TiledWriterUnauthorized):
        caps.check_capabilities(fake.url, credential="wrong")
    with pytest.raises(caps.TiledWriterUnauthorized, match="single-user"):
        caps.check_capabilities(fake.url, credential="goodkey", require_writer_role="admin")


def test_outage_and_5xx_are_target_unavailable(fake):
    fake.status_override = 503
    with pytest.raises(caps.TiledTargetUnavailable):
        caps.check_capabilities(fake.url)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed = sock.getsockname()[1]
    with pytest.raises(caps.TiledTargetUnavailable) as err:
        caps.check_capabilities(f"http://127.0.0.1:{closed}", timeout=2)
    assert err.value.code == "target_unavailable"
    assert caps.check_health(f"http://127.0.0.1:{closed}") is False


def test_connect_external_reads_secret_file_and_never_manages(fake, tmp_path, monkeypatch):
    secret = tmp_path / "writer"
    tc.write_private_file(secret, "goodkey\n")
    cfg = tc.ExternalTiledConfig(profile="test", url=fake.url, writer_secret_file=secret)

    def forbidden(*a, **k):
        raise AssertionError("external mode must not call the lifecycle")

    for name in ("start_managed", "stop_managed", "destroy_owned", "upgrade_catalog"):
        monkeypatch.setattr(lc, name, forbidden)
    report = caps.connect_external(cfg)
    assert report.writer_checked and report.url == fake.url


def test_external_mode_does_not_import_lifecycle():
    code = ("import sys; import app.modules.deployment.tiled_capabilities as c; "
            "import app.modules.deployment.tiled_config as t; "
            "print('app.modules.deployment.tiled_lifecycle' in sys.modules, 'tiled' in sys.modules)")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/")}
    out = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["False", "False"]


def _fake_python(tmp_path, prefix, version, rc=0):
    script = tmp_path / "fakepython"
    script.write_text(f"#!/bin/sh\necho '{prefix}'\necho '{version}'\necho 3.12.0\nexit {rc}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_probe_environment_rejects_wrong_version_and_app_env(tmp_path):
    with pytest.raises(lc.TiledEnvironmentError) as err:
        lc.probe_environment(_fake_python(tmp_path, "/opt/tiled/venv", "0.2.19"))
    assert err.value.code == "unsupported_version"
    with pytest.raises(lc.TiledEnvironmentError, match="own environment"):
        lc.probe_environment(_fake_python(tmp_path, sys.prefix, "0.2.18"))
    with pytest.raises(lc.TiledEnvironmentError, match="does not exist"):
        lc.probe_environment(tmp_path / "missing-python")
    # The app interpreter itself has no tiled (pydantic-settings conflict keeps it out).
    with pytest.raises(lc.TiledEnvironmentError):
        lc.probe_environment(Path(sys.executable))


def test_start_requires_existing_data_dir(tmp_path):
    cfg = tc.ManagedTiledConfig(profile="local", data_dir=tmp_path / "absent", tiled_python=Path(sys.executable))
    with pytest.raises(tc.TiledConfigError, match="does not exist"):
        lc.start_managed(cfg)


def test_stop_never_signals_an_unowned_pid(tmp_path):
    victim = subprocess.Popen(["sleep", "30"])
    try:
        cfg = tc.ManagedTiledConfig(profile="test", data_dir=tmp_path, tiled_python=Path("/x"), port=0,
                                    owner=tc.OWNER_TEST)
        impostor = lc.ManagedServer(config=cfg, port=1, pid=victim.pid, nonce="not-this-one",
                                    log_path=tmp_path / "log")
        with pytest.raises(lc.TiledNotOwned):
            lc.stop_managed(impostor)
        assert victim.poll() is None
    finally:
        victim.kill()
        victim.wait()


def _owned_stopped_server(tmp_path, base, owner=tc.OWNER_TEST, profile="test"):
    root = base / "inst"
    root.mkdir(parents=True)
    cfg = tc.ManagedTiledConfig(profile=profile, data_dir=root, tiled_python=Path("/x"), port=0, owner=owner)
    proc = subprocess.Popen(["true"])
    proc.wait()
    server = lc.ManagedServer(config=cfg, port=1, pid=proc.pid, nonce="n1", log_path=root / "log", process=proc)
    lc._write_json(cfg.manifest_path, {"schema": lc.MANIFEST_SCHEMA, "nonce": "n1", "owner": owner,
                                       "state": "running", "pid": proc.pid})
    return server


def test_destroy_owned_only_inside_base_with_matching_nonce(tmp_path):
    base = tmp_path / "base"
    server = _owned_stopped_server(tmp_path, base)
    outside = tmp_path / "outside"
    outside.mkdir()
    (server.config.data_dir / "escape").symlink_to(outside)
    with pytest.raises(lc.TiledNotOwned, match="symlinks"):
        lc.destroy_owned(server, base=base)
    (server.config.data_dir / "escape").unlink()
    with pytest.raises(lc.TiledNotOwned, match="inside the fixture base"):
        lc.destroy_owned(server, base=tmp_path / "elsewhere")
    server.nonce = "other"
    with pytest.raises(lc.TiledNotOwned, match="manifest"):
        lc.destroy_owned(server, base=base)
    server.nonce = "n1"
    receipt = lc.destroy_owned(server, base=base)
    assert receipt["removed"] and not server.config.data_dir.exists() and outside.exists()


def test_destroy_refuses_local_instances(tmp_path):
    server = _owned_stopped_server(tmp_path, tmp_path / "b", owner=tc.OWNER_LOCAL, profile="local")
    with pytest.raises(lc.TiledNotOwned, match="not a disposable"):
        lc.destroy_owned(server, base=tmp_path / "b")
    assert server.config.data_dir.exists()


def test_status_reports_stale_manifest(tmp_path):
    server = _owned_stopped_server(tmp_path, tmp_path / "b")
    assert lc.managed_status(server.config)["state"] == "stale"
    assert lc.find_managed(server.config) is None
    empty = tc.ManagedTiledConfig(profile="test", data_dir=tmp_path, tiled_python=Path("/x"), owner=tc.OWNER_TEST)
    assert lc.managed_status(empty)["state"] == "absent"


def test_entry_script_is_stdlib_only_at_import():
    source = (REPO / "app/modules/deployment/_tiled_entry.py").read_text()
    top_imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert top_imports == ["import argparse", "from collections.abc import Sequence", "import json",
                           "from pathlib import Path", "import sys", "from typing import NoReturn"]

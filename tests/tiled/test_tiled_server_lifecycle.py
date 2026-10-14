"""T20B: managed lifecycle against real disposable Tiled 0.2.18 servers.

Skips with a reason unless ``F2W_TILED_PYTHON`` names an isolated tiled[server]==0.2.18
interpreter (tests/fixtures/tiled_server.py).
"""
import os
import signal
import stat

import pytest
import requests
from tiled_server import start_tiled_instance, tiled_python

from app.modules.deployment import tiled_capabilities as caps
from app.modules.deployment import tiled_config as tc
from app.modules.deployment import tiled_lifecycle as lc

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")

COUNT = "{ entities(limit: 1000) { id uri } }"


def _uris(instance):
    return {row["uri"] for row in instance.graphql(COUNT)["entities"]}


def test_launch_health_and_capabilities(tiled_instance):
    inst = tiled_instance
    assert requests.get(f"{inst.url}/healthz", timeout=5).json() == {"status": "ready"}
    report = inst.server.capabilities
    assert report.library_version == "0.2.18" and report.writer_principal == "single-user-api-key"
    assert lc.managed_status(inst.config)["state"] == "running"
    # Runs in its own environment, own process group, with nothing ambient.
    assert os.getpgid(inst.server.pid) == inst.server.pid
    assert lc._cmdline(inst.server.pid).startswith(f"{inst.config.tiled_python} -I ")
    manifest = lc._read_manifest(inst.config.manifest_path)
    assert manifest["owner"] == "f2w-test" and manifest["state"] == "running"
    assert manifest["pid"] == inst.server.pid and manifest["port"] == inst.server.port
    # No secret in the generated config, the command line or the log.
    assert inst.writer_key not in inst.config.server_config_path.read_text()
    assert inst.writer_key not in lc._cmdline(inst.server.pid)
    assert inst.writer_key not in inst.server.log_path.read_text()
    assert stat.S_IMODE(inst.config.effective_writer_secret_file.stat().st_mode) == 0o600
    # Anonymous callers cannot write or use REST. (Anonymous graph *listing* is NOT blocked
    # in 0.2.18; see test_tiled_capabilities_0218.py and the evidence file.)
    assert requests.get(f"{inst.url}/api/v1/metadata/", timeout=5).status_code == 401
    body = inst.graphql_raw('mutation { createEntity(input: {entityType: "T", name: "x"}) { id } }',
                            credential=None)
    assert body["errors"][0]["message"] == "Not permitted"
    assert requests.post(f"{inst.url}/api/graphql", json={"query": COUNT},
                         headers={"Authorization": "Apikey wrongkey"}, timeout=5).status_code == 401


def test_seeded_catalog_is_deterministic(tiled_instance):
    receipt = tiled_instance.seed_receipt
    assert receipt["seed"] == 20260916 and receipt["entities"] == 11 and receipt["links"] == 6
    assert (tiled_instance.root / "seed-receipt.json").exists()
    assert "forge:sentinel/esaf/1" in _uris(tiled_instance)


def test_two_instances_are_isolated(tiled_base):
    a = start_tiled_instance(tiled_base, seed=False)
    try:
        b = start_tiled_instance(tiled_base, seed=False)
        try:
            assert a.server.port != b.server.port and a.root != b.root
            assert a.writer_key != b.writer_key
            a.graphql('mutation { createEntity(input: {entityType: "T", name: "only-a", uri: "u:a"}) { id } }')
            assert _uris(a) == {"u:a"} and _uris(b) == set()
            # A's key is not valid on B.
            assert requests.post(f"{b.url}/api/graphql", json={"query": COUNT}, headers=a.headers(),
                                 timeout=5).status_code == 401
            # A fixed port that is already taken is reported, and the owner is left alone.
            cfg = tc.ManagedTiledConfig(profile="test", data_dir=tiled_base / "unused", port=b.server.port,
                                        tiled_python=a.config.tiled_python, owner=tc.OWNER_TEST)
            cfg.data_dir.mkdir()
            with pytest.raises(lc.TiledPortInUse):
                lc.start_managed(cfg)
            assert lc.managed_status(b.config)["state"] == "running"
        finally:
            b.destroy()
        assert not b.root.exists() and a.root.exists()
        assert lc.managed_status(a.config)["state"] == "running"
    finally:
        a.destroy()


def test_stop_retains_data_and_restart_reads_it_back(tiled_instance):
    inst = tiled_instance
    before = _uris(inst)
    old_pid = inst.server.pid
    receipt = inst.stop()
    assert receipt["stopped"] and receipt["data_retained"] and receipt["signal"] == "SIGTERM"
    assert lc.managed_status(inst.config)["state"] == "stopped"
    assert inst.config.catalog_path.exists()
    assert not caps.check_health(inst.url)
    inst.restart()
    assert inst.server.pid != old_pid
    assert _uris(inst) == before
    manifest = lc._read_manifest(inst.config.manifest_path)
    assert manifest["catalog_initialized_now"] is False


def test_second_start_on_same_root_is_refused(tiled_empty_instance):
    with pytest.raises(lc.TiledAlreadyRunning):
        lc.start_managed(tiled_empty_instance.config)


def test_recovery_after_killed_server(tiled_instance):
    inst = tiled_instance
    before = _uris(inst)
    os.killpg(inst.server.pgid, signal.SIGKILL)
    inst.server.process.wait(timeout=10)
    assert lc.managed_status(inst.config)["state"] == "stale"
    assert lc.find_managed(inst.config) is None
    inst.restart()
    assert _uris(inst) == before
    assert list(inst.config.run_dir.glob("stale-*.json"))


def test_external_connect_to_fixture_without_lifecycle(tiled_instance, tmp_path, monkeypatch):
    secret = tmp_path / "writer.key"
    tc.write_private_file(secret, tiled_instance.writer_key)
    settings = tc.load_tiled_settings({"F2W_DEPLOYMENT_PROFILE": "test", "F2W_TILED_MODE": "external",
                                       "F2W_TILED_URL": tiled_instance.url + "/",
                                       "F2W_TILED_WRITER_SECRET_FILE": str(secret)})

    def forbidden(*a, **k):
        raise AssertionError("external mode must not manage a server")

    for name in ("start_managed", "stop_managed", "destroy_owned", "upgrade_catalog", "wait_until_ready"):
        monkeypatch.setattr(lc, name, forbidden)
    report = caps.connect_external(settings.external)
    assert report.writer_checked and report.read_probe == "ok"
    assert report.capability_hash == tiled_instance.server.capabilities.capability_hash
    # Denied writer: no fallback, no anonymous retry.
    tc.write_private_file(secret, "wrongkey")
    with pytest.raises(caps.TiledWriterUnauthorized):
        caps.connect_external(settings.external)


def test_external_outage_never_starts_a_server(tiled_base, monkeypatch):
    inst = start_tiled_instance(tiled_base, seed=False)
    url = inst.url
    try:
        inst.stop()
        ext = tc.ExternalTiledConfig(profile="test", url=url, timeout=2)
        monkeypatch.setattr(lc, "start_managed", lambda *a, **k: pytest.fail("outage must not start Tiled"))
        with pytest.raises(caps.TiledTargetUnavailable):
            caps.connect_external(ext)
        assert not caps.check_health(url)
        assert lc.managed_status(inst.config)["state"] == "stopped"
    finally:
        monkeypatch.undo()
        inst.destroy()


def test_running_server_with_unexpected_version_is_rejected(tiled_session_instance):
    with pytest.raises(caps.TiledUnsupportedVersion) as err:
        caps.check_capabilities(tiled_session_instance.url, expected_version="0.2.19")
    assert err.value.detail == {"url": tiled_session_instance.url, "found": "0.2.18", "expected": "0.2.19"}

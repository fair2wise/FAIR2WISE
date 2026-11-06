"""UI graph sources on ``:local``: the wiring of the own-Tiled reader and one end-to-end read (2026-11-06).

* the scanner exists only where the H5 publication gate would allow the image's own Tiled (``:local`` + managed
  loopback target), never on an ALS profile, never for another port, and it exposes no write method;
* end to end (real disposable Tiled on 127.0.0.1): a frozen KG snapshot is published through the real
  ``:local`` pipeline, then the real app lists it as ``tiled:<corpus>`` and serves it in the JSON wire shape;
  the seeded FORGE entities of the shared server never appear.
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (REPO / "tests" / "security", REPO / "tests" / "fixtures", REPO / "tests" / "contracts" / "legacy_ui" / "closure", HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import _legacy_ui_harness as H  # noqa: E402
from _wiring_support import make_cfg  # noqa: E402
from closure_support import loopback_guard  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from security_fakes import STAGING, staging_settings  # noqa: E402
from tiled_server import tiled_base, tiled_python, tiled_seeded_pool  # noqa: E402,F401

from app.api import local_publication as lp  # noqa: E402
from app.api.main import create_api_app  # noqa: E402
from app.modules.contracts.jobs import JobState  # noqa: E402
from app.modules.deployment.tiled_config import ManagedTiledConfig  # noqa: E402
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot  # noqa: E402
from app.modules.schema.registry import SchemaRegistry  # noqa: E402
from app.modules.security.authorization import DeploymentSettings  # noqa: E402
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS  # noqa: E402

LOCAL = "http://127.0.0.1:8765"
WRITER = "f2w-writer"


def managed(tmp_path, port=8765, profile="local"):
    extra = {"owner": "f2w-test"} if profile == "test" else {}
    return ManagedTiledConfig(profile=profile, data_dir=tmp_path, tiled_python=Path("/nonexistent/python"),
                              port=port, **extra)


def local(target=LOCAL):
    return DeploymentSettings(profile="local", writer_identity=WRITER, tiled_target=target)


def env_for(tmp_path, port=8765):
    return {"F2W_DEPLOYMENT_PROFILE": "local", "F2W_TILED_DATA_DIR": str(tmp_path),
            "F2W_TILED_PYTHON": "/nonexistent/python", "F2W_TILED_PORT": str(port)}


def test_the_scanner_exists_only_for_the_images_own_managed_tiled(tmp_path):
    scanner = lp.local_graph_scanner(local(), managed(tmp_path))
    assert scanner is not None
    assert sorted(n for n in dir(scanner) if not n.startswith("_")) == ["scan_entities", "scan_links"]
    assert "Apikey" not in repr(scanner) and "credential" not in repr(scanner)
    assert lp.local_graph_scanner(local(), managed(tmp_path, port=9999)) is None        # not the deployment's target
    assert lp.local_graph_scanner(local(), None) is None
    assert lp.local_graph_scanner(local(), managed(tmp_path, profile="test")) is None   # the test profile is not :local
    for profile in ("als-dev", "als-prod"):
        als = DeploymentSettings(profile=profile, writer_identity=WRITER)
        assert lp.local_graph_scanner(als, managed(tmp_path)) is None


def test_create_api_app_attaches_the_scanner_on_local_only_when_trusted_wiring_asks(tmp_path, monkeypatch):
    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    (tmp_path / "tiled").mkdir()
    startup = {**env_for(tmp_path / "tiled"), "F2W_TILED_WRITER_IDENTITY": WRITER}
    off = create_api_app(make_cfg(ctx), deployment=local(), startup_config=startup, operations_root=tmp_path / "o1")
    assert off.state.f2w_graph_sources.tiled_offered is False
    on = create_api_app(make_cfg(ctx), deployment=local(), startup_config=startup, operations_root=tmp_path / "o2",
                        local_publication=True)
    assert on.state.f2w_graph_sources.tiled_offered is True


def test_create_api_app_on_als_never_attaches_a_scanner(tmp_path, monkeypatch):
    from security_fakes import FakeTiledIdentityPort, InMemoryCredentialStore

    from app.api.operations import build_operations

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    (tmp_path / "tiled").mkdir()
    settings = staging_settings(tiled_target=STAGING)
    port = FakeTiledIdentityPort(tiled_target=STAGING)
    prebuilt = build_operations(settings, tmp_path / "o", identity_port=port, credential_lookup=lambda _s: None)
    app = create_api_app(make_cfg(ctx), deployment=settings, identity_port=port,
                         credential_store=InMemoryCredentialStore(), operations=prebuilt,
                         startup_config=env_for(tmp_path / "tiled"), local_publication=True)
    assert app.state.f2w_graph_sources.tiled_offered is False


def test_end_to_end_published_release_is_listed_and_served_from_the_real_tiled(tmp_path, monkeypatch, request):
    if tiled_python() is None:
        pytest.skip("F2W_TILED_PYTHON is not set")
    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    loopback_guard(monkeypatch)                           # 127.0.0.1 only
    inst = request.getfixturevalue("tiled_seeded_pool").acquire()
    port = int(inst.url.rsplit(":", 1)[1])
    settings = DeploymentSettings(profile="local", writer_identity=WRITER, tiled_target=inst.url)
    startup = {"F2W_DEPLOYMENT_PROFILE": "local", "F2W_TILED_DATA_DIR": str(inst.root),
               "F2W_TILED_PYTHON": str(tiled_python()), "F2W_TILED_PORT": str(port)}
    app = create_api_app(make_cfg(ctx), deployment=settings, startup_config=startup,
                         operations_root=tmp_path / "ops", local_publication=True)
    ops = app.state.f2w_operations
    storage_kg = Path(app.state.f2w_agent_service.cfg.workdir) / "storage_kg"
    create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json",
                                    storage_kg=storage_kg, terms_ref="inputs/core_dict.terms.json",
                                    code_commit=COMMIT, registry=SchemaRegistry.discover()))
    octx = ops.cli_context()
    handle = ops.submit_publication(octx, "synthetic/v1", inst.url)
    assert ops.jobs.work_one("w1") == (handle.job_id, JobState.AWAITING_REVIEW)
    ops.approve_publication(octx, handle.job_id, ops.current_plan(octx, handle.job_id).plan_sha256)
    assert ops.jobs.work_one("w1") == (handle.job_id, JobState.SUCCEEDED)

    expected = json.loads((storage_kg / "synthetic" / "v1" / "graph.json").read_text(encoding="utf-8"))
    ids = {t["id"] for t in expected["things"]}
    client = TestClient(app)
    listing = client.get("/v1/graph/sources").json()
    assert listing["tiled"]["status"] == "ok"
    kinds = {s["id"]: s for s in listing["sources"]}
    assert "json:synthetic/v1" in kinds and kinds["json:synthetic/v1"]["kind"] == "json"
    tiled_id = next(i for i, s in kinds.items() if s["kind"] == "tiled" and s["label"].startswith("synthetic"))
    row = kinds[tiled_id]
    assert row["read_only"] is True and row["node_count"] == len(ids) and row["revision"]

    body = client.get("/graph", params={"source": tiled_id}).json()
    assert {n["id"] for n in body["nodes"]} == ids                   # no FORGE seed entity, no marker
    json_body = client.get("/graph", params={"source": "json:synthetic/v1"}).json()
    assert {n["id"] for n in json_body["nodes"]} == ids
    assert {(e["source"], e["predicate"], e["target"]) for e in body["edges"]} == \
        {(e["source"], e["predicate"], e["target"]) for e in json_body["edges"]}
    one = body["nodes"][0]
    assert client.get(f"/graph/node/{one['id']}", params={"source": tiled_id}).json()["label"] == one["label"]
    found = client.post("/graph/nodes/search", params={"source": tiled_id},
                        json={"query": one["label"], "limit": 3}).json()
    assert found["results"] and found["results"][0]["node"]["id"] in ids
    assert client.patch(f"/graph/node/{one['id']}", params={"source": tiled_id}, json={"label": "x"}).status_code == 400

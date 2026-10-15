"""T08B import tests. Server-backed cases skip unless ``F2W_TILED_PYTHON`` is set."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from import_fakes import CountingClient, FakeClock, MemoryTiled  # noqa: E402
from tiled_server import start_tiled_instance, tiled_base, tiled_python  # noqa: E402,F401

from app.modules.graph.imports import tiled_json as tj  # noqa: E402
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot  # noqa: E402
from app.modules.schema.registry import SchemaRegistry  # noqa: E402
from app.modules.security.authorization import (  # noqa: E402
    DeploymentSettings,
    create_approval,
    local_operator_context,
)
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS  # noqa: E402


@pytest.fixture(scope="session")
def registry():
    return SchemaRegistry.discover()


@pytest.fixture(scope="session")
def fingerprints(registry):
    return tj.default_supported_schema_fingerprints(registry)


@pytest.fixture(scope="session")
def base_snapshot(tmp_path_factory, registry):
    root = tmp_path_factory.mktemp("kg") / "storage" / "kg"
    root.mkdir(parents=True)
    create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json",
                                    storage_kg=root, terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT,
                                    registry=registry))
    return root / "synthetic" / "v1"


@pytest.fixture()
def snap(base_snapshot, tmp_path):
    """The real T23B snapshot, copied to a private tree (graph bytes unchanged)."""
    import shutil
    shutil.copytree(base_snapshot.parents[1], tmp_path / "storage" / "kg")
    return tmp_path / "storage" / "kg" / "synthetic" / "v1"


@pytest.fixture()
def clock():
    return FakeClock()


class Env:
    """Everything an import test needs against one backend."""

    def __init__(self, client, url, tmp_path, clock, fingerprints, instance=None):
        self.client, self.url, self.clock = client, url, clock
        self.fingerprints, self.instance = fingerprints, instance
        self.settings = DeploymentSettings(profile="local", writer_identity="f2w-writer", tiled_target=url)
        self.ctx = local_operator_context(self.settings, request_id="t08b")
        self.state_dir = tmp_path / "state"

    def plan(self, source, **kw):
        return tj.plan_import(source, client=self.client, settings=self.settings,
                              supported_schema_fingerprints=self.fingerprints, **kw)

    def approve(self, plan, **kw):
        kw.setdefault("approval_id", "appr-" + plan.change.plan_sha256[:8])
        return create_approval(self.ctx, plan.change, self.settings, clock=self.clock, **kw)

    def apply(self, plan, approval, source, **kw):
        return tj.apply_import(self.ctx, plan, approval, source=source, client=self.client, settings=self.settings,
                               supported_schema_fingerprints=self.fingerprints, state_dir=self.state_dir,
                               clock=self.clock, **kw)

    def scan(self):
        return self.client.scan_entities(), self.client.scan_links()

    def writes(self):
        return list(self.client.writes)

    def forge_add(self):
        if self.instance is None:
            self.client.add_forge_sentinel()


@pytest.fixture(params=["memory", "server"])
def env(request, tmp_path, clock, fingerprints, tiled_base):  # noqa: F811
    if request.param == "memory":
        client = MemoryTiled()
        client.add_forge_sentinel()
        yield Env(client, client.base_url, tmp_path, clock, fingerprints)
        return
    if tiled_python() is None:
        pytest.skip("F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")
    inst = start_tiled_instance(tiled_base)  # seeded: includes the FORGE sentinel entities
    try:
        client = CountingClient.from_managed(inst.server)
        yield Env(client, inst.url, tmp_path, clock, fingerprints, instance=inst)
    finally:
        inst.destroy()

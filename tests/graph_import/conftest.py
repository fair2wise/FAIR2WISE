"""T08B import tests. Server-backed cases skip unless ``F2W_TILED_PYTHON`` is set."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from import_fakes import CountingClient, FakeClock, MemoryTiled  # noqa: E402
from tiled_server import start_tiled_instance, tiled_base, tiled_python  # noqa: E402,F401

from app.modules.graph.imports import corpus_scope as cs  # noqa: E402
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

    # -- T08C corpus-scoped replacement -------------------------------------------------------
    def replan(self, source, **kw):
        kw.setdefault("state_dir", self.state_dir)
        return cs.plan_replacement(source, client=self.client, settings=self.settings,
                                   supported_schema_fingerprints=self.fingerprints, **kw)

    def rapply(self, plan, approval, source, **kw):
        return cs.apply_replacement(self.ctx, plan, approval, source=source, client=self.client,
                                    settings=self.settings, supported_schema_fingerprints=self.fingerprints,
                                    state_dir=self.state_dir, clock=self.clock, **kw)

    def replace(self, source, **kw):
        """Plan, approve and apply a replacement in one go (returns plan, outcome)."""
        plan = self.replan(source, **kw)
        out = self.rapply(plan, None if plan.is_noop else self.approve(plan), source)
        return plan, out

    def abandon(self, plan):
        return cs.abandon_plan(self.ctx, plan, client=self.client, settings=self.settings,
                               state_dir=self.state_dir, clock=self.clock)

    def scope_root(self, scope="synthetic"):
        return cs.scope_dir(self.state_dir, self.url, scope)

    def scan(self):
        return self.client.scan_entities(), self.client.scan_links()

    def graphql(self, query, variables=None):
        """Raw GraphQL against the server instance (``None`` on the memory backend)."""
        return self.instance.graphql(query, variables) if self.instance is not None else None

    def foreign_entity_id(self):
        """A FORGE-owned entity UUID on this backend."""
        if self.instance is not None:
            return self.instance.seed_receipt["sentinel_id"]
        return next(e["id"] for e in self.client.entities.values() if e["uri"] == "forge:esaf/1")

    def add_foreign_link(self, subject_id, object_id, predicate="forge:cites"):
        """Create a link F2W does not own (as FORGE would)."""
        if self.instance is not None:
            return self.instance.graphql(
                "mutation($i: CreateLinkInput!) { createLink(input: $i) { id } }",
                {"i": {"subjectId": subject_id, "predicate": predicate, "objectId": object_id,
                       "properties": {"owner": "forge"}}})["createLink"]["id"]
        import uuid
        lid = str(uuid.uuid4())
        self.client.links[lid] = {"id": lid, "subjectId": subject_id, "objectId": object_id, "predicate": predicate,
                                  "properties": {"owner": "forge"}, "accessBlob": None}
        return lid

    def rename_foreign_entity(self, entity_id, name):
        """Change a FORGE record under F2W's feet (Q14)."""
        if self.instance is not None:
            self.instance.graphql("mutation($id: ID!, $i: UpdateEntityInput!) { updateEntity(id: $id, input: $i) "
                                  "{ id } }", {"id": entity_id, "i": {"name": name, "accessBlob": {}}})
        else:
            self.client.entities[entity_id]["name"] = name

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

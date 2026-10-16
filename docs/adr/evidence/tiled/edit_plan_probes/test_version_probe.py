"""Probe: what the T08B importer does with version N -> N+1 today (evidence for the editing plan; not committed)."""
import copy
import json
import sys

import pytest

from conftest import WT

for extra in ("tests/graph_import", "tests/graph"):
    sys.path.insert(0, str(WT / extra))

from tiled_server import start_tiled_instance, tiled_python  # noqa: E402

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="needs F2W_TILED_PYTHON")

OBS = {}


@pytest.fixture
def env(tiled_base, tmp_path):
    from import_fakes import FakeClock
    from app.modules.graph.backends.tiled_mutations import TiledMutationClient
    from app.modules.graph.imports import tiled_json as tj
    from app.modules.schema.registry import SchemaRegistry
    from app.modules.security.authorization import DeploymentSettings, create_approval, local_operator_context

    inst = start_tiled_instance(tiled_base)
    client = TiledMutationClient.from_managed(inst.server)
    fp = tj.default_supported_schema_fingerprints(SchemaRegistry.discover())
    settings = DeploymentSettings(profile="local", writer_identity="probe-writer", tiled_target=inst.url)
    ctx = local_operator_context(settings, request_id="probe")
    clock = FakeClock()

    def run(source, **kw):
        plan = tj.plan_import(source, client=client, settings=settings, supported_schema_fingerprints=fp, **kw)
        approval = None if plan.is_noop else create_approval(ctx, plan.change, settings, clock=clock,
                                                             approval_id="p-" + plan.change.plan_sha256[:8])
        return plan, tj.apply_import(ctx, plan, approval, source=source, client=client, settings=settings,
                                     supported_schema_fingerprints=fp, state_dir=tmp_path / "state", clock=clock)
    try:
        yield inst, client, run, tj
    finally:
        inst.destroy()


def _owned_graph_ids(client):
    out = {}
    for e in client.scan_entities():
        p = e.get("properties") or {}
        if p.get("f2w_owner") == "f2w-literature" and "/node/" in (e.get("uri") or ""):
            out[p["f2w_graph_id"]] = out.get(p["f2w_graph_id"], 0) + 1
    return out


def test_two_real_snapshots_get_two_graph_ids(env, tmp_path):
    from app.modules.graph.snapshot import SnapshotRequest, create_snapshot
    from app.modules.schema.registry import SchemaRegistry
    from tests.snapshots.snapshot_fakes import COMMIT, INPUTS
    inst, client, run, tj = env
    storage = tmp_path / "storage" / "kg"
    storage.mkdir(parents=True)
    reg = SchemaRegistry.discover()
    for _ in range(2):
        create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json",
                                        storage_kg=storage, terms_ref="inputs/core_dict.terms.json",
                                        code_commit=COMMIT, registry=reg))
    v1, v2 = storage / "synthetic" / "v1", storage / "synthetic" / "v2"
    OBS["manifest_graph_ids"] = [json.loads((d / "manifest.json").read_text())["graph_id"] for d in (v1, v2)]
    run(v1)
    p2, out2 = run(v2)
    OBS["v2_plan_retires_v1"] = sum(1 for o in p2.ops if o.kind == "retire_node")
    OBS["owned_graph_ids_after_v1_then_v2"] = _owned_graph_ids(client)


def test_foreign_link_on_superseded_node_stays_on_retired_entity(env, tmp_path):
    from app.modules.graph.snapshot import SnapshotRequest, create_snapshot
    from app.modules.schema.registry import SchemaRegistry
    from import_fakes import snapshot_with_graph
    from tests.snapshots.snapshot_fakes import COMMIT, INPUTS
    inst, client, run, tj = env
    storage = tmp_path / "storage" / "kg"
    storage.mkdir(parents=True)
    create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json",
                                    storage_kg=storage, terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT,
                                    registry=SchemaRegistry.discover()))
    v1 = storage / "synthetic" / "v1"
    run(v1)
    g = json.loads((v1 / "graph.json").read_text())
    node = g["things"][0]
    gid = tj.load_source(v1, supported_schema_fingerprints=tj.default_supported_schema_fingerprints()).graph_id
    rows = [e for e in client.scan_entities() if (e.get("properties") or {}).get("f2w_id") == node["id"]
            and (e.get("properties") or {}).get("f2w_graph_id") == gid]
    target_uuid = rows[0]["id"]
    sentinel = inst.seed_receipt["sentinel_id"]
    foreign = inst.graphql("mutation($i: CreateLinkInput!) { createLink(input: $i) { id } }",
                           {"i": {"subjectId": sentinel, "predicate": "forge:cites", "objectId": target_uuid,
                                  "properties": {"owner": "forge"}}})["createLink"]["id"]
    g2 = copy.deepcopy(g)
    g2["things"][0]["description"] = "changed in v2"
    v2 = snapshot_with_graph(v1, tmp_path / "v2root", "synthetic", "v2", g2)  # same graph_id (copied manifest)
    _, out = run(v2)
    link = inst.graphql("query($i: ID!) { link(id: $i) { objectId object { uri } } }", {"i": foreign})["link"]
    OBS["import_status_with_foreign_link"] = out.status
    OBS["foreign_link_object_after_supersede"] = link
    OBS["foreign_link_still_on_old_uuid"] = link["objectId"] == target_uuid


def test_zz_report():
    print("\nOBSERVATIONS\n" + json.dumps(OBS, indent=1, sort_keys=True, default=str))

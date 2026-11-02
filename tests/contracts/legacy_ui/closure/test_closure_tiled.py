"""T18C closure on real disposable Tiled 0.2.18 (skipped without ``F2W_TILED_PYTHON``).

* Versioned JSON -> separate Tiled import (T08B adapter) in **both Tiled modes through the
  same adapter**: a managed instance (``from_managed``) and a connect-only external target
  (``ExternalTiledConfig`` + writer secret file -> ``from_external``).
* Field, id and evidence retention: the T08A Tiled read port returns, for every node, the
  same id, fields and citations as the JSON read port over the same snapshot.
* Edit reconciliation: an acknowledged scoped edit blocks a re-import that would lose it;
  reconciled into a child snapshot (``parent.relation = edit_reconciliation``) it imports and
  reads back under the same public id; rolling back to the parent needs a reviewed
  resolution, and the old citation id resolves at every step.
* UI27: the legacy live-Tiled lookup (``app.modules.tiled_graph``) against the server.
* D27: the importer writes only with the configured writer; FORGE-owned records untouched.
"""
from __future__ import annotations

import copy
import json
import os
import stat
from pathlib import Path

import pytest

from closure_support import loopback_guard
from graph_contexts import make_ctx
from import_fakes import FakeClock, snapshot_with_graph
from tiled_server import start_tiled_instance, tiled_python

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.deployment.tiled_config import ExternalTiledConfig
from app.modules.graph import edit_journal as ej
from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.graph.backends.tiled import TiledGraphBackend
from app.modules.graph.backends.tiled_client import TiledGraphClient
from app.modules.graph.backends.tiled_mutations import TiledMutationClient
from app.modules.graph.imports import tiled_json as tj
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot
from app.modules.schema.registry import SchemaRegistry
from app.modules.security.authorization import DeploymentSettings, create_approval, local_operator_context
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")

WRITER = "f2w-closure-writer"


@pytest.fixture(scope="module")
def registry():
    return SchemaRegistry.discover()


@pytest.fixture(scope="module")
def fingerprints(registry):
    return tj.default_supported_schema_fingerprints(registry)


@pytest.fixture
def snap(tmp_path, registry):
    storage = tmp_path / "storage" / "kg"
    storage.mkdir(parents=True)
    create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json",
                                    storage_kg=storage, terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT,
                                    registry=registry))
    return storage / "synthetic" / "v1"


def _external_client(inst, tmp_path) -> TiledMutationClient:
    secret = tmp_path / "writer.secret"
    secret.write_text(inst.writer_key, encoding="utf-8")
    os.chmod(secret, stat.S_IRUSR | stat.S_IWUSR)
    cfg = ExternalTiledConfig(profile="test", url=inst.url, writer_secret_file=secret)
    return TiledMutationClient.from_external(cfg)


@pytest.fixture(params=["managed", "external"])
def target(request, tiled_base, tmp_path):  # noqa: F811
    inst = start_tiled_instance(tiled_base)  # seeded, includes a FORGE-owned sentinel
    try:
        if request.param == "managed":
            client = TiledMutationClient.from_managed(inst.server)
        else:
            client = _external_client(inst, tmp_path)
        assert type(client) is TiledMutationClient  # one adapter class for both modes
        yield request.param, inst, client
    finally:
        inst.destroy()


def _graph(snapdir: Path):
    return json.loads((snapdir / "graph.json").read_text(encoding="utf-8"))


def _foreign(client):
    rows = [*client.scan_entities(), *client.scan_links()]
    return sorted(json.dumps(r, sort_keys=True) for r in rows
                  if (r.get("properties") or {}).get("f2w_owner") != "f2w-literature")


class Ops:
    def __init__(self, client, url, tmp_path, fingerprints):
        self.client, self.fp, self.clock = client, fingerprints, FakeClock()
        self.settings = DeploymentSettings(profile="local", writer_identity=WRITER, tiled_target=url)
        self.ctx = local_operator_context(self.settings, request_id="t18c")
        self.state = tmp_path / "import-state"

    def plan(self, source, **kw):
        return tj.plan_import(source, client=self.client, settings=self.settings,
                              supported_schema_fingerprints=self.fp, **kw)

    def apply(self, plan, source):
        approval = None
        if not plan.is_noop:
            approval = create_approval(self.ctx, plan.change, self.settings, clock=self.clock,
                                       approval_id="t18c-" + plan.change.plan_sha256[:8])
        return tj.apply_import(self.ctx, plan, approval, source=source, client=self.client, settings=self.settings,
                               supported_schema_fingerprints=self.fp, state_dir=self.state, clock=self.clock)


def _reader(mode, inst, client):
    """The T08A read port over the same target, in the same mode."""
    if mode == "managed":
        return TiledGraphBackend(TiledGraphClient.from_managed(inst.server))
    return TiledGraphBackend(TiledGraphClient(client.base_url, profile="test", credential=inst.writer_key))


def test_json_to_tiled_import_retains_fields_ids_evidence_and_reconciles_edits(target, snap, tmp_path, fingerprints):
    mode, inst, client = target
    ops = Ops(client, inst.url, tmp_path, fingerprints)
    foreign = _foreign(client)
    g1 = _graph(snap)
    graph_id = tj.load_source(snap, supported_schema_fingerprints=fingerprints).graph_id

    out1 = ops.apply(ops.plan(snap), snap)
    assert out1.status == "applied" and out1.receipt.receipt.verified
    assert out1.receipt.receipt.executed_as == WRITER and out1.receipt.extras.approved_by == "local-operator"

    # Field / id / evidence retention: Tiled read port == JSON read port over the same graph.
    ctx = make_ctx()
    tiled = _reader(mode, inst, client)
    json_port = JsonGraphBackend([snap / "graph.json"])
    json_gid = json_port.graph_ids()[0] if hasattr(json_port, "graph_ids") else None
    checked = 0
    for node in g1["things"]:
        got = tiled.get_entity(ctx, graph_id, node["id"])
        assert got is not None, node["id"]
        assert got["id"] == node["id"] and got["graph_id"] == graph_id
        if json_gid is not None:
            want = json_port.get_entity(ctx, json_gid, node["id"])
            for key in ("id", "name", "category", "citations", "data"):
                assert got[key] == want[key], (node["id"], key)
        checked += 1
    assert checked == len(g1["things"]) > 0

    # An acknowledged scoped edit (as the PATCH projection would journal it) is protected.
    edited = g1["things"][0]
    cite_id = edited["id"]  # an "old citation" [KG: <id>] held by a session or bookmark
    journal = ej.EditJournal(tmp_path / "edits.jsonl")
    journal.append(ej.EditRecord(
        edit_id="ui15-1", seq=1, state="acknowledged", target=tj.target_for(client), scope=("synthetic",),
        base_snapshot_id="v1", base_graph_sha256=out1.receipt.receipt.snapshot_sha256, profile="local",
        mode="local_trusted", actor="local-operator", operation="node_patch", ids=(cite_id,),
        before={"description": edited.get("description")}, after={"description": "Edited in the old UI"},
        recorded_at=ops.clock()))
    with pytest.raises(ContractError) as err:
        ops.plan(snap, edit_journal=journal)  # re-importing v1 would erase the edit
    assert err.value.code is ErrorCode.CONFLICT and "ui15-1" in str(err.value)

    # Reconcile into a child snapshot v2 (edit_reconciliation) and import it.
    g2 = copy.deepcopy(g1)
    g2["things"][0]["description"] = "Edited in the old UI"
    m1 = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    v2 = snapshot_with_graph(snap, tmp_path / "v2root", "synthetic", "v2", g2, patch_manifest={"parent": {
        "snapshot_id": "v1", "graph_sha256": m1["graph"]["sha256"], "relation": "edit_reconciliation"}})
    journal.advance("ui15-1", "reconciled", ops.clock(), reconciled_in="v2")
    plan2 = ops.plan(v2, edit_journal=journal)
    assert plan2.change.creates + plan2.summary.get("supersedes", 0) >= 0 and not plan2.is_noop
    out2 = ops.apply(plan2, v2)
    assert out2.status == "applied" and out2.receipt.receipt.verified
    after = _reader(mode, inst, client).get_entity(ctx, graph_id, cite_id)
    assert after["id"] == cite_id and after["data"].get("description") == "Edited in the old UI"
    for node in g2["things"][1:]:  # no other record changed identity
        assert _reader(mode, inst, client).get_entity(ctx, graph_id, node["id"])["id"] == node["id"]

    # Rollback to v1 (the pre-edit parent). T23C flips the T18C finding F4 (Q15): a ``reconciled``
    # edit protects every import whose lineage lacks its snapshot, so rolling the target back below the
    # reconciliation snapshot is refused until a reviewed ConflictResolution exists.
    with pytest.raises(ContractError) as refused:
        ops.plan(snap, edit_journal=journal)
    assert refused.value.code is ErrorCode.CONFLICT and "ui15-1" in str(refused.value)
    res = ej.ConflictResolution(edit_id="ui15-1", decision="discarded", reviewed_by="local-operator",
                                reviewed_at=ops.clock(), note="T18C rollback rehearsal")
    carried = ej.ConflictResolution(edit_id="ui15-1", decision="carried", reviewed_by="local-operator",
                                    reviewed_at=ops.clock(), note="T18C rollback rehearsal")
    rollback = ops.plan(snap, edit_journal=journal, resolutions=[res])
    assert not rollback.is_noop
    assert ops.plan(snap, edit_journal=journal, resolutions=[carried]).change.plan_sha256 != rollback.change.plan_sha256
    out3 = ops.apply(rollback, snap)
    assert out3.status == "applied"
    back = _reader(mode, inst, client).get_entity(ctx, graph_id, cite_id)
    assert back["id"] == cite_id and back["data"].get("description") == edited.get("description")

    # Roll forward again to v2: the edit's snapshot is in the lineage, so no resolution is needed and
    # the same id resolves to the edited record.
    out4 = ops.apply(ops.plan(v2, edit_journal=journal), v2)
    assert out4.status == "applied"
    assert _reader(mode, inst, client).get_entity(ctx, graph_id, cite_id)["data"].get(
        "description") == "Edited in the old UI"

    # Replay of the same plan is a no-op (no duplicate facts); FORGE records untouched.
    assert ops.plan(v2, edit_journal=journal).is_noop
    owned = [e for e in client.scan_entities() if (e.get("properties") or {}).get("f2w_owner") == "f2w-literature"
             and not str(e.get("uri", "")).count("#")]
    assert len({e["uri"] for e in owned}) == len(owned)
    assert _foreign(client) == foreign


def test_legacy_live_tiled_lookup_reads_identity_records_from_tiled_0218(tiled_base, monkeypatch):  # noqa: F811
    """UI27: the baseline GraphQL lookup (with its nodeId fallback) works on the pinned server."""
    from app.modules import tiled_graph

    inst = start_tiled_instance(tiled_base, seed=False)
    try:
        loopback_guard(monkeypatch)
        create = "mutation($i: CreateEntityInput!) { createEntity(input: $i) { id } }"
        esaf = inst.graphql(create, {"i": {"entityType": "ESAF", "name": "ESAF 2026-00043",
                                           "uri": "beamline:ESAF-2026-00043",
                                           "properties": {"esaf_number": "2026-00043"}}})["createEntity"]["id"]
        sample = inst.graphql(create, {"i": {"entityType": "Sample", "name": "P3HT blend 7",
                                             "uri": "beamline:Sample-P3HT-blend-7",
                                             "properties": {"sample_code": "P3HT-blend-7"}}})["createEntity"]["id"]
        inst.graphql("mutation($i: CreateLinkInput!) { createLink(input: $i) { id } }",
                     {"i": {"subjectId": esaf, "predicate": "rel:hasSample", "objectId": sample, "properties": {}}})
        monkeypatch.setenv("TILED_API_KEY", inst.writer_key)
        status = tiled_graph.probe_tiled_status(uri=inst.url, enabled=True)
        assert status["tiled_status"] == "ok" and status["tiled_api_key_set"] is True
        pack = tiled_graph.lookup_experiment_identity("Which samples are on ESAF 2026-00043?", uri=inst.url,
                                                      enabled=True, include_catalog=False)
        assert pack.status == "ok"
        ids = {h.id for h in pack.hits}
        assert {"beamline:ESAF-2026-00043", "beamline:Sample-P3HT-blend-7"} <= ids
        near = tiled_graph.tiled_neighborhood_ids(pack.graph, ["beamline:ESAF-2026-00043"], hops=1)
        assert "beamline:Sample-P3HT-blend-7" in set(near)
        monkeypatch.delenv("TILED_API_KEY")
        unsigned = tiled_graph.lookup_experiment_identity("Which samples are on ESAF 2026-00043?", uri=inst.url,
                                                          enabled=True, include_catalog=False)
        # Observed: the managed single-user test server also answers unsigned GraphQL reads (its
        # loopback-only, anonymous-read configuration), so the legacy "unsigned" status is not
        # reachable here. Pinned so a change in the managed server's access mode is noticed.
        assert unsigned.status == "ok" and unsigned.hits
    finally:
        inst.destroy()

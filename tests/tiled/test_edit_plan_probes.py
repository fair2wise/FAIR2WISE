"""Tiled 0.2.18 edit-plan probes (T20C): the facts ``tiled_graph_editing_plan.md`` relies on.

Run 2026-10-16 against disposable Tiled 0.2.18 servers as evidence
(``docs/adr/evidence/tiled/edit_plan_probes/README.md``); moved here by T28C so they run in the
CI Tiled job. Each probe records what happened in ``OBS`` (printed by ``test_zz_report``)
instead of asserting guesses; the probe bodies are unchanged. They skip without
``F2W_TILED_PYTHON`` like every server-backed test in this directory.

Two groups:

* edit-plan probes (``tiled_session_instance`` / ``tiled_acl_instance``): schema surface,
  delete cascade into foreign links, retire-by-uri, link endpoint immutability, accessBlob
  retagging, TagBasedAccessPolicy owner rights;
* version probes (a fresh instance per test): what the T08B importer does with version
  N -> N+1 and with a foreign link on a superseded node.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
for extra in ("tests/graph_import", "tests/graph"):
    if str(REPO / extra) not in sys.path:
        sys.path.insert(0, str(REPO / extra))

from tiled_server import start_tiled_instance, tiled_python  # noqa: E402

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="needs F2W_TILED_PYTHON")

OBS = {}
CE = "mutation($i: CreateEntityInput!) { createEntity(input: $i) { id } }"
CL = "mutation($i: CreateLinkInput!) { createLink(input: $i) { id } }"
UE = "mutation($id: ID!, $i: UpdateEntityInput!) { updateEntity(id: $id, input: $i) { id uri name } }"
DE = "mutation($id: ID!) { deleteEntity(id: $id) }"
DL = "mutation($id: ID!) { deleteLink(id: $id) }"
LINK = "query($id: ID!) { link(id: $id) { id subjectId objectId predicate accessBlob } }"


# ----------------------------------------------------------------- edit-plan probes ----


def _e(inst, name, uri, props=None, **kw):
    return inst.graphql(CE, {"i": {"entityType": "T", "name": name, "uri": uri, "properties": props or {}}},
                        **kw)["createEntity"]["id"]


def _l(inst, s, o, pred="p", props=None, blob=None, **kw):
    body = {"subjectId": s, "predicate": pred, "objectId": o, "properties": props or {}}
    if blob is not None:
        body["accessBlob"] = blob
    return inst.graphql(CL, {"i": body}, **kw)["createLink"]["id"]


def test_schema_surface(tiled_session_instance):
    inst = tiled_session_instance
    q = inst.graphql('{ __type(name: "Query") { fields { name args { name } } } }')["__type"]["fields"]
    m = inst.graphql('{ __type(name: "Mutation") { fields { name args { name } } } }')["__type"]["fields"]
    OBS["query_args"] = {f["name"]: [a["name"] for a in f["args"]] for f in q}
    OBS["mutations"] = sorted(f["name"] for f in m)
    for t in ("UpdateEntityInput", "UpdateLinkInput", "CreateEntityInput", "CreateLinkInput"):
        fields = inst.graphql('query($n: String!) { __type(name: $n) { inputFields { name } } }', {"n": t})
        OBS[t] = sorted(f["name"] for f in fields["__type"]["inputFields"])


def test_delete_entity_cascades_into_foreign_links(tiled_session_instance):
    """F2W deletes its own entity; a FORGE link that points at it disappears too."""
    inst = tiled_session_instance
    mine = _e(inst, "f2w node", "f2w:kg/demo_v1/node/a", {"f2w_owner": "f2w-literature", "f2w_graph_id": "demo_v1",
                                                          "f2w_id": "a"})
    forge = _e(inst, "forge sample", "forge:sample/1", {"owner": "forge"})
    foreign_in = _l(inst, forge, mine, "forge:mentions", {"owner": "forge"})
    foreign_out = _l(inst, mine, forge, "forge:about", {"owner": "forge"})
    assert inst.graphql(DE, {"id": mine}) == {"deleteEntity": True}
    OBS["cascade_foreign_incoming_link_after_delete"] = inst.graphql(LINK, {"id": foreign_in})["link"]
    OBS["cascade_foreign_outgoing_link_after_delete"] = inst.graphql(LINK, {"id": foreign_out})["link"]
    OBS["forge_entity_survives"] = inst.graphql("query($i: ID!) { entity(id: $i) { id } }", {"i": forge})["entity"]


def test_retire_by_uri_keeps_links_attached(tiled_session_instance):
    """Moving an entity to a 'retired' uri (what T08B does) keeps every link, including foreign ones."""
    inst = tiled_session_instance
    mine = _e(inst, "f2w node", "f2w:kg/demo_v1/node/b")
    forge = _e(inst, "forge run", "forge:run/1")
    foreign_in = _l(inst, forge, mine, "forge:mentions")
    inst.graphql(UE, {"id": mine, "i": {"uri": "f2w:kg/demo_v1/retired/b/tag", "accessBlob": {}}})
    OBS["foreign_link_after_retire_by_uri"] = inst.graphql(LINK, {"id": foreign_in})["link"]
    incoming = inst.graphql("query($i: ID!) { entity(id: $i) { uri incomingLinks { id } } }", {"i": mine})["entity"]
    OBS["retired_entity_incoming"] = incoming


def test_link_endpoint_cannot_move_and_find_incoming(tiled_session_instance):
    inst = tiled_session_instance
    a, b, c = (_e(inst, n, f"x:{n}") for n in ("ea", "eb", "ec"))
    link = _l(inst, a, b)
    body = inst.graphql_raw("mutation($id: ID!) { updateLink(id: $id, input: {objectId: \"%s\", accessBlob: {}}) "
                            "{ id } }" % c, {"id": link})
    OBS["update_link_object_id"] = (body.get("errors") or [{}])[0].get("message")
    OBS["links_by_object_filter"] = inst.graphql("query($o: ID!) { links(objectId: $o) { id } }", {"o": b})["links"]
    # Entities cannot be filtered by uri or property: only entityType/limit/offset.
    body = inst.graphql_raw('{ entities(uri: "x:ea") { id } }')
    OBS["entities_uri_filter"] = (body.get("errors") or [{}])[0].get("message")


def test_access_blob_retag_and_readback(tiled_session_instance):
    inst = tiled_session_instance
    a = _e(inst, "tagme", "x:tagme")
    b = _e(inst, "tagme2", "x:tagme2")
    link = _l(inst, a, b, blob={"tags": ["f2w_lit_demo"]})
    inst.graphql(UE, {"id": a, "i": {"accessBlob": {"tags": ["f2w_lit_demo", "f2w_v1"]}}})
    upd = inst.graphql("mutation($id: ID!, $i: UpdateLinkInput!) { updateLink(id: $id, input: $i) { accessBlob } }",
                       {"id": link, "i": {"accessBlob": {"tags": ["f2w_lit_demo", "f2w_v2"]}}})
    OBS["link_blob_after_retag"] = upd["updateLink"]["accessBlob"]
    OBS["link_blob_reread_after_retag"] = inst.graphql(LINK, {"id": link})["link"]["accessBlob"]
    upd2 = inst.graphql("mutation($id: ID!, $i: UpdateLinkInput!) { updateLink(id: $id, input: $i) { predicate accessBlob } }",
                        {"id": link, "i": {"predicate": "p2", "accessBlob": {"tags": ["zzz"]}}})
    OBS["link_after_predicate_and_blob_update"] = upd2["updateLink"]
    OBS["link_reread_after_predicate_and_blob_update"] = inst.graphql(LINK, {"id": link})["link"]
    body = inst.graphql_raw("query($i: ID!) { entity(id: $i) { accessBlob } }", {"i": a})
    OBS["entity_blob_readable"] = (body.get("errors") or [{}])[0].get("message")


def test_acl_cascade_and_owner_rights(tiled_acl_instance):
    """Under TagBasedAccessPolicy: what can the tag owner do, and does deleteEntity cascade into links
    the owner cannot see or write (an admin-private link)?"""
    inst = tiled_acl_instance
    tok = {u: inst.login(u)["access_token"] for u in ("admin", "reader", "owner")}

    def as_(user):
        return {"credential": tok[user], "scheme": "Bearer"}

    def raw(user, q, v=None):
        return inst.graphql_raw(q, v, credential=tok[user], scheme="Bearer")

    tagged = {"tags": ["f2w_literature"]}
    mine = inst.graphql("mutation($i: CreateEntityInput!) { createEntity(input: $i) { id } }",
                        {"i": {"entityType": "T", "name": "owned", "uri": "f2w:kg/acl/node/a", "accessBlob": tagged}},
                        **as_("owner"))["createEntity"]["id"]
    other = inst.graphql("mutation($i: CreateEntityInput!) { createEntity(input: $i) { id } }",
                         {"i": {"entityType": "T", "name": "admin-thing", "uri": "forge:x"}},
                         **as_("admin"))["createEntity"]["id"]
    admin_link = inst.graphql(CL, {"i": {"subjectId": other, "predicate": "forge:mentions", "objectId": mine}},
                              **as_("admin"))["createLink"]["id"]
    OBS["acl_owner_sees_admin_link"] = raw("owner", LINK, {"id": admin_link}).get("data")
    r = raw("owner", UE, {"id": mine, "i": {"uri": "f2w:kg/acl/retired/a/t", "accessBlob": tagged}})
    OBS["acl_owner_update_uri_own_tagged"] = r.get("errors") or r.get("data")
    r = raw("reader", UE, {"id": mine, "i": {"name": "x", "accessBlob": tagged}})
    OBS["acl_reader_update"] = r.get("errors") or r.get("data")
    r = raw("owner", DE, {"id": mine})
    OBS["acl_owner_delete_own_tagged"] = r.get("errors") or r.get("data")
    OBS["acl_admin_link_after_owner_delete"] = raw("admin", LINK, {"id": admin_link}).get("data")
    # Can the owner delete an admin-created link that points at a tagged entity?
    mine2 = inst.graphql("mutation($i: CreateEntityInput!) { createEntity(input: $i) { id } }",
                         {"i": {"entityType": "T", "name": "owned2", "uri": "f2w:kg/acl/node/b", "accessBlob": tagged}},
                         **as_("owner"))["createEntity"]["id"]
    admin_link2 = inst.graphql(CL, {"i": {"subjectId": other, "predicate": "forge:mentions", "objectId": mine2}},
                               **as_("admin"))["createLink"]["id"]
    r = raw("owner", DL, {"id": admin_link2})
    OBS["acl_owner_delete_admin_link"] = r.get("errors") or r.get("data")
    r = raw("admin", DE, {"id": other})
    OBS["acl_admin_delete_any"] = r.get("errors") or r.get("data")


# ------------------------------------------------------------------- version probes ----
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

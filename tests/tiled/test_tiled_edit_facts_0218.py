"""T20C: the edit-relevant Tiled 0.2.18 facts P1-P9 of ``tiled_graph_editing_plan.md`` section 4.1, pinned.

Unlike ``test_edit_plan_probes.py`` (observation-only), every test here asserts the fact the editing plan
and the T08C/T08D importers rely on. On a server where a fact changes, exactly that test fails:

======  ===============================================================================================
P1      ``deleteEntity`` deletes foreign links attached to the deleted entity, in both directions
P2      under ``TagBasedAccessPolicy`` the tag owner's ``deleteEntity`` also deletes an admin link the
        owner cannot read (and cannot ``deleteLink`` directly)
P3      changing an entity's URI (how F2W retires) keeps every link, including foreign ones
P4      link endpoints cannot change (``UpdateLinkInput`` has no ``objectId``/``subjectId``)
P5      query surface: ``entities(entityType, limit, offset)`` only; ``links(subjectId, predicate,
        objectId, ...)``; no batch, transaction or conditional mutation
P6      an access-blob change is silently ignored without an access policy
P7      an entity's access blob cannot be read
P8      two real snapshots of one corpus get two graph ids; the per-version importer leaves v1 live
P9      revising a node in place leaves a foreign link on the retired revision and still verifies
ACL     tag owner may change the URI of its own tagged entity; a reader may not; admin deletes anything
======  ===============================================================================================

The capability flags that encode P1 and P6 (``delete_cascades_foreign_links``, ``blob_update_effective``)
are asserted against the server's own :class:`CapabilityReport`. Skips without ``F2W_TILED_PYTHON``.
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

from tiled_server import TEST_ACCESS_TAGS, start_tiled_instance, tiled_python  # noqa: E402

from app.modules.deployment import tiled_capabilities as caps  # noqa: E402

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="needs F2W_TILED_PYTHON")

CE = "mutation($i: CreateEntityInput!) { createEntity(input: $i) { id } }"
CL = "mutation($i: CreateLinkInput!) { createLink(input: $i) { id } }"
UE = "mutation($id: ID!, $i: UpdateEntityInput!) { updateEntity(id: $id, input: $i) { id uri name entityType } }"
UL = "mutation($id: ID!, $i: UpdateLinkInput!) { updateLink(id: $id, input: $i) { id predicate accessBlob } }"
DE = "mutation($id: ID!) { deleteEntity(id: $id) }"
DL = "mutation($id: ID!) { deleteLink(id: $id) }"
LINK = "query($id: ID!) { link(id: $id) { id subjectId objectId predicate accessBlob } }"
ENTITY = "query($i: ID!) { entity(id: $i) { id uri entityType incomingLinks { id } outgoingLinks { id } } }"


# The probes mutate their servers, so they get module-scoped instances of their own (the session-wide
# fixtures of tests/tiled/conftest.py stay clean for the read-only capability tests).
@pytest.fixture(scope="module")
def inst(tiled_base):
    server = start_tiled_instance(tiled_base)
    try:
        yield server
    finally:
        server.destroy()


@pytest.fixture(scope="module")
def acl(tiled_base):
    server = start_tiled_instance(tiled_base, auth=True, seed=False, access_tags=TEST_ACCESS_TAGS)
    try:
        yield server
    finally:
        server.destroy()


def _e(inst, name, uri, props=None, entity_type="T", **kw):
    return inst.graphql(CE, {"i": {"entityType": entity_type, "name": name, "uri": uri,
                                   "properties": props or {}}}, **kw)["createEntity"]["id"]


def _l(inst, s, o, pred="p", props=None, blob=None, **kw):
    body = {"subjectId": s, "predicate": pred, "objectId": o, "properties": props or {}}
    if blob is not None:
        body["accessBlob"] = blob
    return inst.graphql(CL, {"i": body}, **kw)["createLink"]["id"]


def _error(body):
    assert body.get("errors"), body
    return body["errors"][0]["message"]


# ------------------------------------------------------------------------------------------- P1 ----
def test_p1_delete_entity_cascades_into_foreign_links_both_directions(inst):
    mine = _e(inst, "f2w node", "f2w:kg/demo_v1/node/a",
              {"f2w_owner": "f2w-literature", "f2w_graph_id": "demo_v1", "f2w_id": "a"})
    forge = _e(inst, "forge sample", "forge:sample/1", {"owner": "forge"})
    foreign_in = _l(inst, forge, mine, "forge:mentions", {"owner": "forge"})
    foreign_out = _l(inst, mine, forge, "forge:about", {"owner": "forge"})
    assert inst.graphql(LINK, {"id": foreign_in})["link"]["id"] == foreign_in
    assert inst.graphql(DE, {"id": mine}) == {"deleteEntity": True}
    assert inst.graphql(LINK, {"id": foreign_in})["link"] is None, "incoming foreign link must cascade"
    assert inst.graphql(LINK, {"id": foreign_out})["link"] is None, "outgoing foreign link must cascade"
    assert inst.graphql(ENTITY, {"i": forge})["entity"]["id"] == forge, "the foreign entity itself survives"
    # The flag T08D's purge consults encodes exactly this fact.
    assert inst.server.capabilities.delete_cascades_foreign_links is True
    assert "deleteEntity-cascades-foreign-links" in caps.KNOWN_QUIRKS["0.2.18"]


# ------------------------------------------------------------------------------------------- P2 ----
def _tokens(acl):
    return {u: acl.login(u)["access_token"] for u in ("admin", "reader", "owner")}


def test_p2_tag_owner_delete_cascades_into_an_admin_link_the_owner_cannot_read(acl):
    tok = _tokens(acl)

    def as_(user):
        return {"credential": tok[user], "scheme": "Bearer"}

    tagged = {"tags": ["f2w_literature"]}
    mine = acl.graphql(CE, {"i": {"entityType": "T", "name": "owned", "uri": "f2w:kg/acl/node/p2",
                                  "accessBlob": tagged}}, **as_("owner"))["createEntity"]["id"]
    other = acl.graphql(CE, {"i": {"entityType": "T", "name": "admin-thing", "uri": "forge:p2"}},
                        **as_("admin"))["createEntity"]["id"]
    admin_link = acl.graphql(CL, {"i": {"subjectId": other, "predicate": "forge:mentions", "objectId": mine}},
                             **as_("admin"))["createLink"]["id"]
    # The owner cannot read the admin's private link, and cannot delete it directly ...
    assert acl.graphql(LINK, {"id": admin_link}, **as_("owner"))["link"] is None
    assert _error(acl.graphql_raw(DL, {"id": admin_link}, **as_("owner"))) == "Not permitted"
    assert acl.graphql(LINK, {"id": admin_link}, **as_("admin"))["link"]["id"] == admin_link
    # ... but deleting its own tagged entity deletes that link anyway (database cascade, not the resolver).
    assert acl.graphql(DE, {"id": mine}, **as_("owner")) == {"deleteEntity": True}
    assert acl.graphql(LINK, {"id": admin_link}, **as_("admin"))["link"] is None


# ------------------------------------------------------------------------------------------- P3 ----
def test_p3_retire_by_uri_and_type_keeps_every_link_including_foreign(inst):
    mine = _e(inst, "f2w node", "f2w:kg/demo_v1/node/b", entity_type="Material")
    forge = _e(inst, "forge run", "forge:run/1")
    foreign_in = _l(inst, forge, mine, "forge:mentions")
    foreign_out = _l(inst, mine, forge, "forge:about")
    inst.graphql(UE, {"id": mine, "i": {"uri": "f2w:kg/demo_v1/retired/b/tag", "entityType": "f2w:Retired",
                                        "accessBlob": {}}})
    link = inst.graphql(LINK, {"id": foreign_in})["link"]
    assert link == {"id": foreign_in, "subjectId": forge, "objectId": mine, "predicate": "forge:mentions",
                    "accessBlob": None}
    assert inst.graphql(LINK, {"id": foreign_out})["link"]["objectId"] == forge
    row = inst.graphql(ENTITY, {"i": mine})["entity"]
    assert row["uri"] == "f2w:kg/demo_v1/retired/b/tag" and row["entityType"] == "f2w:Retired"
    assert [lk["id"] for lk in row["incomingLinks"]] == [foreign_in]
    assert [lk["id"] for lk in row["outgoingLinks"]] == [foreign_out]
    # A type-filtered listing no longer returns the retired record under its real type (T08C relies on this).
    listed = inst.graphql('{ entities(entityType: "Material", limit: 1000) { id } }')["entities"]
    assert mine not in {r["id"] for r in listed}
    listed = inst.graphql('{ entities(entityType: "f2w:Retired", limit: 1000) { id } }')["entities"]
    assert mine in {r["id"] for r in listed}


# ------------------------------------------------------------------------------------------- P4 ----
def test_p4_link_endpoints_cannot_change(inst):
    a, b, c = (_e(inst, n, f"x:{n}") for n in ("ea", "eb", "ec"))
    link = _l(inst, a, b)
    for field in ("objectId", "subjectId"):
        document = ("mutation($id: ID!) { updateLink(id: $id, input: {" + field + ': "' + c
                    + '", accessBlob: {}}) { id } }')
        body = inst.graphql_raw(document, {"id": link})
        assert f"unknown field '{field}'" in _error(body)
    fields = inst.graphql('query { __type(name: "UpdateLinkInput") { inputFields { name } } }')
    assert sorted(f["name"] for f in fields["__type"]["inputFields"]) == ["accessBlob", "predicate"]
    assert inst.graphql(LINK, {"id": link})["link"]["objectId"] == b
    # Incoming links are found through the links(objectId:) filter.
    assert inst.graphql("query($o: ID!) { links(objectId: $o) { id } }", {"o": b})["links"] == [{"id": link}]


# ------------------------------------------------------------------------------------------- P5 ----
def test_p5_query_surface_has_no_uri_filter_batch_transaction_or_conditional_write(inst):
    q = inst.graphql('{ __type(name: "Query") { fields { name args { name } } } }')["__type"]["fields"]
    args = {f["name"]: sorted(a["name"] for a in f["args"]) for f in q}
    assert args["entities"] == ["entityType", "limit", "offset"]
    assert args["links"] == ["limit", "objectId", "offset", "predicate", "subjectId"]
    assert args["entity"] == ["id"] and args["link"] == ["id"]
    m = inst.graphql('{ __type(name: "Mutation") { fields { name } } }')["__type"]["fields"]
    assert sorted(f["name"] for f in m) == ["createEntity", "createLink", "deleteEntity", "deleteLink",
                                            "deleteNamespace", "updateEntity", "updateLink", "upsertNamespace"]
    assert "Unknown argument 'uri'" in _error(inst.graphql_raw('{ entities(uri: "x:ea") { id } }'))
    assert "Unknown argument 'properties'" in _error(inst.graphql_raw('{ entities(properties: {}) { id } }'))


# ------------------------------------------------------------------------------------------- P6 ----
def test_p6_access_blob_change_is_silently_ignored_without_a_policy(inst):
    a = _e(inst, "tagme", "x:tagme")
    b = _e(inst, "tagme2", "x:tagme2")
    link = _l(inst, a, b, blob={"tags": ["f2w_lit_demo"]})
    upd = inst.graphql(UL, {"id": link, "i": {"accessBlob": {"tags": ["f2w_lit_demo", "f2w_v2"]}}})["updateLink"]
    assert upd["accessBlob"] == {"tags": ["f2w_lit_demo"]}, "the response shows the old blob"
    assert inst.graphql(LINK, {"id": link})["link"]["accessBlob"] == {"tags": ["f2w_lit_demo"]}
    # A predicate change in the same call applies while the blob change does not.
    upd2 = inst.graphql(UL, {"id": link, "i": {"predicate": "p2", "accessBlob": {"tags": ["zzz"]}}})["updateLink"]
    assert upd2["predicate"] == "p2" and upd2["accessBlob"] == {"tags": ["f2w_lit_demo"]}
    reread = inst.graphql(LINK, {"id": link})["link"]
    assert reread["predicate"] == "p2" and reread["accessBlob"] == {"tags": ["f2w_lit_demo"]}
    assert inst.server.capabilities.blob_update_effective is False
    assert "accessBlob-update-ignored-without-policy" in caps.KNOWN_QUIRKS["0.2.18"]


# ------------------------------------------------------------------------------------------- P7 ----
def test_p7_entity_access_blob_is_not_readable(inst):
    a = _e(inst, "blobless", "x:blobless")
    body = inst.graphql_raw("query($i: ID!) { entity(id: $i) { accessBlob } }", {"i": a})
    assert "Cannot query field 'accessBlob' on type 'Entity'" in _error(body)
    fields = inst.graphql('query { __type(name: "Entity") { fields { name } } }')["__type"]["fields"]
    assert "accessBlob" not in {f["name"] for f in fields}
    link_fields = inst.graphql('query { __type(name: "Link") { fields { name } } }')["__type"]["fields"]
    assert "accessBlob" in {f["name"] for f in link_fields}


# ------------------------------------------------------------------------------------------- ACL ---
def test_acl_owner_may_retire_own_tagged_entity_reader_may_not_admin_deletes_anything(acl):
    tok = _tokens(acl)

    def raw(user, q, v=None):
        return acl.graphql_raw(q, v, credential=tok[user], scheme="Bearer")

    tagged = {"tags": ["f2w_literature"]}
    mine = acl.graphql(CE, {"i": {"entityType": "T", "name": "owned", "uri": "f2w:kg/acl/node/x",
                                  "accessBlob": tagged}}, credential=tok["owner"], scheme="Bearer")
    mine = mine["createEntity"]["id"]
    r = raw("owner", UE, {"id": mine, "i": {"uri": "f2w:kg/acl/retired/x/t", "accessBlob": tagged}})
    assert r.get("data", {}).get("updateEntity", {}).get("uri") == "f2w:kg/acl/retired/x/t", r
    assert _error(raw("reader", UE, {"id": mine, "i": {"name": "x", "accessBlob": tagged}})) == "Not permitted"
    other = acl.graphql(CE, {"i": {"entityType": "T", "name": "admin-thing", "uri": "forge:acl"}},
                        credential=tok["admin"], scheme="Bearer")["createEntity"]["id"]
    assert raw("admin", DE, {"id": mine})["data"] == {"deleteEntity": True}
    assert raw("admin", DE, {"id": other})["data"] == {"deleteEntity": True}


# ------------------------------------------------------------------------------------- P8 / P9 -----
@pytest.fixture
def importer_env(tiled_base, tmp_path):
    """A fresh server plus the T08B per-version importer (``tiled_json``), as the editing plan measured it."""
    from import_fakes import FakeClock
    from app.modules.graph.backends.tiled_mutations import TiledMutationClient
    from app.modules.graph.imports import tiled_json as tj
    from app.modules.schema.registry import SchemaRegistry
    from app.modules.security.authorization import DeploymentSettings, create_approval, local_operator_context

    server = start_tiled_instance(tiled_base)
    client = TiledMutationClient.from_managed(server.server)
    fp = tj.default_supported_schema_fingerprints(SchemaRegistry.discover())
    settings = DeploymentSettings(profile="local", writer_identity="probe-writer", tiled_target=server.url)
    ctx = local_operator_context(settings, request_id="t20c")
    clock = FakeClock()

    def run(source):
        plan = tj.plan_import(source, client=client, settings=settings, supported_schema_fingerprints=fp)
        approval = None if plan.is_noop else create_approval(ctx, plan.change, settings, clock=clock,
                                                             approval_id="p-" + plan.change.plan_sha256[:8])
        return plan, tj.apply_import(ctx, plan, approval, source=source, client=client, settings=settings,
                                     supported_schema_fingerprints=fp, state_dir=tmp_path / "state", clock=clock)
    try:
        yield server, client, run, tj
    finally:
        server.destroy()


def _two_snapshots(tmp_path):
    from app.modules.graph.snapshot import SnapshotRequest, create_snapshot
    from app.modules.schema.registry import SchemaRegistry
    from tests.snapshots.snapshot_fakes import COMMIT, INPUTS
    storage = tmp_path / "storage" / "kg"
    storage.mkdir(parents=True)
    reg = SchemaRegistry.discover()
    for _ in range(2):
        create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json",
                                        storage_kg=storage, terms_ref="inputs/core_dict.terms.json",
                                        code_commit=COMMIT, registry=reg))
    return storage / "synthetic" / "v1", storage / "synthetic" / "v2"


def _owned_live_by_graph(client):
    out = {}
    for e in client.scan_entities():
        p = e.get("properties") or {}
        if p.get("f2w_owner") == "f2w-literature" and "/node/" in (e.get("uri") or ""):
            out[p["f2w_graph_id"]] = out.get(p["f2w_graph_id"], 0) + 1
    return out


def test_p8_two_real_snapshots_get_two_graph_ids_and_the_per_version_importer_keeps_v1_live(importer_env, tmp_path):
    _server, client, run, _tj = importer_env
    v1, v2 = _two_snapshots(tmp_path)
    ids = [json.loads((d / "manifest.json").read_text())["graph_id"] for d in (v1, v2)]
    assert ids == ["synthetic_v1", "synthetic_v2"]
    run(v1)
    plan2, out2 = run(v2)
    assert out2.status == "applied"
    assert sum(1 for o in plan2.ops if o.kind == "retire_node") == 0, "v2 retires nothing of v1"
    live = _owned_live_by_graph(client)
    assert live["synthetic_v1"] == live["synthetic_v2"] == 14


def test_p9_in_place_revision_leaves_a_foreign_link_on_the_retired_revision_and_still_verifies(importer_env,
                                                                                              tmp_path):
    from import_fakes import snapshot_with_graph
    server, client, run, tj = importer_env
    v1, _v2 = _two_snapshots(tmp_path)
    run(v1)
    g = json.loads((v1 / "graph.json").read_text())
    node = g["things"][0]
    gid = tj.load_source(v1, supported_schema_fingerprints=tj.default_supported_schema_fingerprints()).graph_id
    rows = [e for e in client.scan_entities() if (e.get("properties") or {}).get("f2w_id") == node["id"]
            and (e.get("properties") or {}).get("f2w_graph_id") == gid]
    assert len(rows) == 1
    target_uuid = rows[0]["id"]
    sentinel = server.seed_receipt["sentinel_id"]
    foreign = server.graphql(CL, {"i": {"subjectId": sentinel, "predicate": "forge:cites", "objectId": target_uuid,
                                        "properties": {"owner": "forge"}}})["createLink"]["id"]
    g2 = copy.deepcopy(g)
    g2["things"][0]["description"] = "changed in v2"
    v2 = snapshot_with_graph(v1, tmp_path / "v2root", "synthetic", "v2", g2)  # same graph_id (copied manifest)
    _plan, out = run(v2)
    assert out.status == "applied" and out.receipt.receipt.verified
    link = server.graphql("query($i: ID!) { link(id: $i) { objectId object { uri } } }", {"i": foreign})["link"]
    assert link["objectId"] == target_uuid, "the foreign link stays on the old UUID"
    assert link["object"]["uri"].startswith(f"f2w:kg/{gid}/retired/"), "which is now the retired revision"

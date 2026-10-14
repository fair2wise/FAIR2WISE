"""T20B: the observed tiled 0.2.18 graph/auth contract (re-check of the rc2-based T20A ADR).

Each test pins one observation recorded in ``docs/adr/evidence/tiled/capabilities_0.2.18.md``.
If the pin changes, failures here show exactly which assumption moved.
Skips with a reason unless ``F2W_TILED_PYTHON`` is set.
"""
import pytest
import requests
from tiled_server import GraphQLFailed, tiled_python

from app.modules.deployment import tiled_capabilities as caps

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")

CREATE_ENTITY = "mutation($i: CreateEntityInput!) { createEntity(input: $i) { id nodeId properties createdAt } }"
CREATE_LINK = "mutation($i: CreateLinkInput!) { createLink(input: $i) { id predicate properties accessBlob } }"


def _entity(inst, name, **extra):
    return inst.graphql(CREATE_ENTITY, {"i": {"entityType": "T", "name": name, **extra}})["createEntity"]


def _link(inst, s, o, predicate="p", **extra):
    return inst.graphql(CREATE_LINK, {"i": {"subjectId": s, "predicate": predicate, "objectId": o,
                                            **extra}})["createLink"]


def _error(body):
    assert body.get("errors"), body
    return body["errors"][0]["message"]


def test_entity_and_link_create_read_and_neighborhood(tiled_session_instance):
    inst = tiled_session_instance
    a = _entity(inst, "a", uri="u:a", properties={"x": 1, "nested": {"y": [1, 2]}})
    b = _entity(inst, "b")
    assert a["nodeId"] is None and a["properties"] == {"x": 1, "nested": {"y": [1, 2]}}
    link = _link(inst, a["id"], b["id"], properties={"evidence": "e1"})
    assert link["properties"] == {"evidence": "e1"} and link["accessBlob"] is None
    hood = inst.graphql("query($i: ID!) { entity(id: $i) { name outgoingLinks { predicate object { name } } "
                        "incomingLinks { id } } }", {"i": a["id"]})["entity"]
    assert hood == {"name": "a", "outgoingLinks": [{"predicate": "p", "object": {"name": "b"}}],
                    "incomingLinks": []}
    found = inst.graphql("query($s: ID!) { links(subjectId: $s) { id subject { name } object { name } } }",
                         {"s": a["id"]})["links"]
    assert found == [{"id": link["id"], "subject": {"name": "a"}, "object": {"name": "b"}}]
    # There is no dedicated neighborhood query; traversal is nested and depth-limited to 10.
    deep = "{ entities(limit: 1) { " + "outgoingLinks { object { " * 5 + "name" + " } }" * 5 + " } }"
    assert "maximum operation depth of 10" in _error(inst.graphql_raw(deep))


def test_properties_are_create_only(tiled_session_instance):
    inst = tiled_session_instance
    a = _entity(inst, "props", properties={"v": 1})
    b = _entity(inst, "props-b")
    link = _link(inst, a["id"], b["id"])
    msg = _error(inst.graphql_raw('mutation($i: ID!) { updateEntity(id: $i, input: {properties: {v: 2}}) { id } }',
                                  {"i": a["id"]}))
    assert "unknown field 'properties'" in msg
    msg = _error(inst.graphql_raw('mutation($i: ID!) { updateLink(id: $i, input: {properties: {v: 2}}) { id } }',
                                  {"i": link["id"]}))
    assert "unknown field 'properties'" in msg
    report = inst.server.capabilities
    assert report.update_entity_fields == ("accessBlob", "entityType", "name", "nodeId", "uri")
    assert report.update_link_fields == ("accessBlob", "predicate")
    assert not report.entity_properties_updatable and not report.link_properties_updatable


def test_updates_fail_without_access_blob_quirk(tiled_session_instance):
    inst = tiled_session_instance
    a = _entity(inst, "upd")
    b = _entity(inst, "upd-b")
    link = _link(inst, a["id"], b["id"])
    upd_entity = "mutation($i: ID!, $in: UpdateEntityInput!) { updateEntity(id: $i, input: $in) { name uri } }"
    upd_link = "mutation($i: ID!, $in: UpdateLinkInput!) { updateLink(id: $i, input: $in) { predicate } }"
    assert "UnsetType" in _error(inst.graphql_raw(upd_entity, {"i": a["id"], "in": {"name": "renamed"}}))
    assert "UnsetType" in _error(inst.graphql_raw(upd_link, {"i": link["id"], "in": {"predicate": "q"}}))
    ok = inst.graphql(upd_entity, {"i": a["id"], "in": {"name": "renamed", "uri": "u:r", "accessBlob": {}}})
    assert ok["updateEntity"] == {"name": "renamed", "uri": "u:r"}
    assert inst.graphql(upd_link, {"i": link["id"], "in": {"predicate": "q", "accessBlob": {}}})["updateLink"] == {
        "predicate": "q"}
    assert "update-requires-accessBlob" in caps.KNOWN_QUIRKS["0.2.18"]


def test_access_blobs_not_access_tags_and_node_id_not_is_node_bound(tiled_session_instance):
    inst = tiled_session_instance
    assert "Did you mean 'accessBlob'" in _error(inst.graphql_raw(
        'mutation { createEntity(input: {entityType: "T", name: "x", accessTags: ["a"]}) { id } }'))
    assert "Cannot query field 'isNodeBound'" in _error(inst.graphql_raw("{ entities(limit: 1) { isNodeBound } }"))
    assert "Unknown argument 'nodePathParts'" in _error(
        inst.graphql_raw('{ entities(nodePathParts: ["a"]) { id } }'))
    assert "Cannot query field 'accessBlob' on type 'Entity'" in _error(
        inst.graphql_raw("{ entities(limit: 1) { accessBlob } }"))
    # Without an access policy an access blob is stored as given; on links it reads back.
    a = _entity(inst, "blob", accessBlob={"tags": ["x"]})
    link = _link(inst, a["id"], a["id"], accessBlob={"tags": ["x"]})
    assert link["accessBlob"] == {"tags": ["x"]}
    assert inst.graphql('{ catalogNodeId(path: ["missing"]) }') == {"catalogNodeId": None}


def test_no_upsert_uri_not_unique_and_delete_cascades(tiled_session_instance):
    inst = tiled_session_instance
    first = _entity(inst, "dup1", uri="u:dup")
    second = _entity(inst, "dup2", uri="u:dup")
    assert first["id"] != second["id"]
    link = _link(inst, first["id"], second["id"])
    assert inst.graphql("mutation($i: ID!) { deleteEntity(id: $i) }", {"i": first["id"]}) == {"deleteEntity": True}
    assert inst.graphql("query($i: ID!) { link(id: $i) { id } }", {"i": link["id"]}) == {"link": None}


def test_namespaces_compact_predicates(tiled_session_instance):
    inst = tiled_session_instance
    inst.graphql('mutation { upsertNamespace(prefix: "t20b", uri: "http://example.org/t20b/") { prefix } }')
    a = _entity(inst, "ns-a")
    full = _link(inst, a["id"], a["id"], predicate="http://example.org/t20b/rel")
    curie = _link(inst, a["id"], a["id"], predicate="t20b:rel2")
    assert (full["predicate"], curie["predicate"]) == ("t20b:rel", "t20b:rel2")
    rows = inst.graphql('{ links(predicate: "http://example.org/t20b/rel2") { predicate } }')["links"]
    assert rows == [{"predicate": "t20b:rel2"}]


def test_single_user_mode_has_no_whoami_and_anonymous_listing_leaks(tiled_session_instance):
    inst = tiled_session_instance
    about = requests.get(f"{inst.url}/api/v1/", timeout=5).json()["authentication"]
    assert about == {"required": True, "providers": [], "links": None}
    assert requests.get(f"{inst.url}/api/v1/auth/whoami", headers=inst.headers(), timeout=5).status_code == 404
    sentinel = inst.seed_receipt["sentinel_id"]
    # allow_anonymous_access: false blocks REST and single-entity reads, but 0.2.18's
    # entities/links listing applies only access-policy filters, never the caller's
    # read:metadata scope. With no access policy, anonymous listing returns everything.
    assert inst.graphql("query($i: ID!) { entity(id: $i) { id } }", {"i": sentinel}, credential=None) == {
        "entity": None}
    anon = inst.graphql("{ entities(limit: 1000) { id } }", credential=None)["entities"]
    assert sentinel in {row["id"] for row in anon}
    assert inst.graphql("{ links(limit: 1) { id } }", credential=None)["links"]
    assert inst.graphql("{ namespaces { prefix } }", credential=None) == {"namespaces": []}


def _bearer(inst, user):
    return {"Authorization": f"Bearer {inst.login(user)['access_token']}"}


def test_auth_variant_admin_and_reader_principals(tiled_auth_instance):
    inst = tiled_auth_instance
    about = requests.get(f"{inst.url}/api/v1/", timeout=5).json()["authentication"]
    assert [p["provider"] for p in about["providers"]] == ["f2wtest"]
    assert set(about["links"]) == {"whoami", "apikey", "refresh_session", "revoke_session", "logout"}
    admin = requests.get(f"{inst.url}/api/v1/auth/whoami", headers=_bearer(inst, "admin"), timeout=5).json()
    reader = requests.get(f"{inst.url}/api/v1/auth/whoami", headers=_bearer(inst, "reader"), timeout=5).json()
    for principal in (admin, reader):
        assert {"uuid", "type", "identities", "roles", "api_keys", "sessions", "latest_activity"} <= set(principal)
        assert principal["type"] == "user"
    assert admin["identities"][0]["id"] == "admin" and admin["identities"][0]["provider"] == "f2wtest"
    assert [r["name"] for r in admin["roles"]] == ["user", "admin"]
    assert [r["name"] for r in reader["roles"]] == ["user"]
    assert requests.get(f"{inst.url}/api/v1/auth/whoami", timeout=5).text == "null"
    assert requests.get(f"{inst.url}/api/v1/auth/whoami", headers={"Authorization": "Bearer garbage"},
                        timeout=5).status_code == 401
    # Principals listing is admin-only.
    assert requests.get(f"{inst.url}/api/v1/auth/principal", headers=_bearer(inst, "reader"),
                        timeout=5).status_code == 401


def test_auth_variant_reader_can_write_without_access_policy(tiled_auth_instance):
    """The default 'user' role carries write:metadata; only an access policy restricts it."""
    inst = tiled_auth_instance
    token = inst.login("reader")["access_token"]
    made = inst.graphql('mutation { createEntity(input: {entityType: "T", name: "by-reader"}) { id } }',
                        credential=token, scheme="Bearer")
    assert made["createEntity"]["id"]


def test_auth_variant_service_writer_key_and_revocation(tiled_auth_instance):
    inst = tiled_auth_instance
    admin = _bearer(inst, "admin")
    svc = requests.post(f"{inst.url}/api/v1/auth/principal", params={"role": "admin"}, headers=admin,
                        timeout=5).json()
    assert svc["type"] == "service" and [r["name"] for r in svc["roles"]] == ["admin"]
    issued = requests.post(f"{inst.url}/api/v1/auth/principal/{svc['uuid']}/apikey",
                           json={"expires_in": 600, "scopes": ["inherit"]}, headers=admin, timeout=5).json()
    key = issued["secret"]
    report = caps.check_capabilities(inst.url, credential=key, require_writer_role="admin")
    assert report.writer_principal == svc["uuid"] and "admin" in report.writer_roles
    assert key not in str(report.to_dict())
    inst.graphql('mutation { createEntity(input: {entityType: "T", name: "by-service"}) { id } }', credential=key)
    gone = requests.delete(f"{inst.url}/api/v1/auth/principal/{svc['uuid']}/apikey",
                           params={"first_eight": issued["first_eight"]}, headers=admin, timeout=5)
    assert gone.status_code == 204
    with pytest.raises(caps.TiledWriterUnauthorized):
        caps.check_capabilities(inst.url, credential=key)
    # A non-admin writer fails the role requirement instead of silently writing.
    reader_token = inst.login("reader")["access_token"]
    with pytest.raises(caps.TiledWriterUnauthorized, match="lacks the 'admin' role"):
        caps.check_capabilities(inst.url, credential=reader_token, scheme="Bearer", require_writer_role="admin")
    with pytest.raises(GraphQLFailed):
        inst.graphql("{ entities { isNodeBound } }", credential=reader_token, scheme="Bearer")


def test_acl_variant_tag_policy_scopes_graph_reads_and_writes(tiled_acl_instance):
    """TagBasedAccessPolicy (0.2.18 'access blobs' carrying tags) is what closes the anonymous listing."""
    inst = tiled_acl_instance
    tokens = {user: inst.login(user)["access_token"] for user in ("admin", "reader", "owner")}

    def call(user, query, variables=None):
        if user is None:
            return inst.graphql_raw(query, variables, credential=None)
        return inst.graphql_raw(query, variables, credential=tokens[user], scheme="Bearer")

    create = ('mutation($n: String!, $b: JSON) { createEntity(input: {entityType: "T", name: $n, '
              'accessBlob: $b}) { id } }')
    tagged = {"tags": ["f2w_literature"]}
    owner_tagged = call("owner", create, {"n": "owner-tagged", "b": tagged})["data"]["createEntity"]["id"]
    call("admin", create, {"n": "admin-private"})
    call("reader", create, {"n": "reader-private"})
    assert "not an owner of tag='f2w_literature'" in _error(call("reader", create, {"n": "x", "b": tagged}))
    assert "only Tiled admins can apply the 'public' tag" in _error(
        call("owner", create, {"n": "x", "b": {"tags": ["public"]}}))

    def names(user):
        return {row["name"] for row in call(user, "{ entities(limit: 1000) { name } }")["data"]["entities"]}

    assert names(None) == set()
    assert names("reader") == {"owner-tagged", "reader-private"}
    assert names("owner") == {"owner-tagged"}
    assert {"owner-tagged", "admin-private", "reader-private"} <= names("admin")
    denied = call("reader", "mutation($i: ID!) { deleteEntity(id: $i) }", {"i": owner_tagged})
    assert _error(denied) == "Not permitted"

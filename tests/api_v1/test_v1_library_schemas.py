"""Schema authoring routes (V89-V95): ``:local`` only, capability ``library.author_schema``, size caps, typed errors."""
from __future__ import annotations

import json

import pytest

from app.api.auth import undeclared_routes
from app.api.policies import API_ROUTE_POLICIES
from app.modules.contracts.context import CAPABILITIES, LOCAL_ONLY_CAPABILITIES
from app.modules.library import user_schema_validation as v
from app.modules.library.user_schemas import UserSchemaStore
from app.modules.security.authorization import capabilities_for
from tests.library.test_user_schema_validation import GOOD

BASE = "/v1/library/schemas"


def test_the_capability_is_local_only_and_the_rows_declare_it(local, als):
    assert "library.author_schema" in CAPABILITIES and "library.author_schema" in LOCAL_ONLY_CAPABILITIES
    rows = {p.route_id: p for p in API_ROUTE_POLICIES if p.route_id in {f"V{n}" for n in range(89, 96)}}
    assert set(rows) == {f"V{n}" for n in range(89, 96)} and all(not p.on_als for p in rows.values())
    assert {r.capabilities == frozenset({"library.author_schema"}) for k, r in rows.items() if k in ("V89", "V90", "V91", "V95")} == {True}
    assert undeclared_routes(local.app) == []
    assert local.client().get("/v1/library/schemas/user").status_code == 200   # the local operator holds the library caps


def test_als_answers_404_for_every_route_and_no_als_principal_holds_the_capability(als):
    for who in ("admin", "user"):
        c = als.client(who)
        assert c.post(f"{BASE}/validate", json={"text": GOOD}).status_code == 404
        assert c.post(f"{BASE}/insert-core", json={"text": GOOD}).status_code == 404
        assert c.post(BASE, json={"name": "catfish", "text": GOOD}).status_code == 404
        assert c.get(f"{BASE}/user").status_code == 404
        assert c.get(f"{BASE}/user/catfish@v1").status_code == 404
        assert c.get(f"{BASE}/user/catfish/diff?from=1&to=2").status_code == 404
        assert c.delete(f"{BASE}/user/catfish@v1").status_code == 404
    from tests.library.test_library_jobs import _identity

    assert "library.author_schema" not in capabilities_for(_identity(admin=True))


def test_validate_returns_the_report_and_stores_nothing(local, tmp_path):
    c = local.client()
    ok = c.post(f"{BASE}/validate", json={"text": GOOD})
    assert ok.status_code == 200 and ok.json()["ok"] is True and ok.json()["stats"]["classes"] == 4
    assert "loads in SchemaView and SchemaHelper" in ok.json()["checked"][-1] and "Not full LinkML" in ok.json()["note"]
    assert len(ok.json()["checked"]) == 5 and ok.json()["fixable"] is False
    bad = c.post(f"{BASE}/validate", json={"text": GOOD.replace("    domain: Species\n", "    domain: Nope\n")}).json()
    assert bad["ok"] is False and bad["problems"][0]["line"] > 1 and bad["problems"][0]["code"] == "unknown_reference"
    assert not (local.app.state.f2w_kg_library.user_schemas.root).exists()


def test_validate_text_is_not_clipped_by_the_response_projector(local):
    long = GOOD + "# " + "y" * 5000 + "\n"
    saved = local.client().post(BASE, json={"name": "catfish", "text": long})
    assert saved.status_code == 200
    got = local.client().get(f"{BASE}/user/catfish@v1").json()
    assert got["text"] == long and got["id"] == "user/catfish@v1"


def test_insert_core_adds_the_missing_parts(local):
    out = local.client().post(f"{BASE}/insert-core", json={"text": "id: https://example.org/x\nname: x\nversion: 1.0\nclasses:\n  A:\n    description: a\n"})
    assert out.status_code == 200 and set(out.json()) == {"text"}
    assert v.validate_schema_text(out.json()["text"]).ok is True
    assert "Thing:" in out.json()["text"] and "related_to:" in out.json()["text"]


def test_save_versions_list_read_diff_and_delete(local):
    c = local.client()
    one = c.post(BASE, json={"name": "catfish", "text": GOOD})
    assert one.status_code == 200 and one.json()["id"] == "user/catfish@v1" and "text" not in one.json()
    two = c.post(BASE, json={"name": "catfish", "text": GOOD.replace("A species of catfish.", "An armoured catfish.")})
    assert two.json()["id"] == "user/catfish@v2"
    listing = c.get(f"{BASE}/user").json()["schemas"]
    assert listing[0]["name"] == "catfish" and listing[0]["versions"] == [1, 2] and listing[0]["latest"]["id"] == "user/catfish@v2"
    assert "text" not in listing[0]["latest"]
    diff = c.get(f"{BASE}/user/catfish/diff", params={"from": 1, "to": 2}).json()["diff"]
    assert "-    description: A species of catfish." in diff and "+    description: An armoured catfish." in diff
    assert c.get(f"{BASE}/user/catfish/diff", params={"from": 1, "to": 9}).status_code == 404
    assert c.get(f"{BASE}/user/catfish@v9").status_code == 404
    # the schema appears in the extraction choices, origin user, with no host path
    choices = c.get(BASE).json()["schemas"]
    mine = [s for s in choices if s.get("origin") == "user"]
    assert [s["name"] for s in mine] == ["user/catfish@v2", "user/catfish@v1"] and "path" not in mine[0]
    assert c.delete(f"{BASE}/user/catfish@v1").json() == {"deleted": "user/catfish@v1"}
    assert c.get(f"{BASE}/user/catfish@v1").status_code == 404


def test_delete_is_refused_while_a_kg_uses_the_version(local):
    c = local.client()
    c.post(BASE, json={"name": "catfish", "text": GOOD})
    service = local.app.state.f2w_kg_library
    from app.modules.library.schemas import manifest_block

    service.store.create("fishkg", schema=manifest_block(service.resolve("user/catfish@v1")))
    out = c.delete(f"{BASE}/user/catfish@v1")
    assert out.status_code == 409 and out.json()["reason"] == "schema_in_use"
    assert c.get(f"{BASE}/user/catfish@v1").status_code == 200


def test_invalid_save_is_422_with_the_report_and_lines(local):
    c = local.client()
    bad = GOOD.replace("name: catfish", "name: eels")   # does not match the saved name
    out = c.post(BASE, json={"name": "catfish", "text": bad})
    assert out.status_code == 422
    body = out.json()
    assert body["code"] == "invalid_input" and body["reason"] == "schema_invalid" and body["report"]["ok"] is False
    assert body["report"]["problems"][0]["code"] == "name_mismatch" and body["report"]["problems"][0]["line"] == 2
    broken = c.post(BASE, json={"name": "catfish", "text": "id: [x\n"})
    assert broken.status_code == 422 and broken.json()["report"]["problems"][0]["code"] == "yaml_syntax"
    assert c.get(f"{BASE}/user").json() == {"schemas": []}


@pytest.mark.parametrize("name", ["../x", "A", "a b", "", "x" * 70, "a/b"])
def test_bad_names_are_422_and_nothing_is_written(local, name):
    out = local.client().post(BASE, json={"name": name, "text": GOOD})
    assert out.status_code == 422
    assert not local.app.state.f2w_kg_library.user_schemas.root.exists()


def test_bodies_over_the_cap_are_refused_before_parsing(local):
    c = local.client()
    huge = json.dumps({"text": "x" * (300 * 1024)})
    for path in ("/validate", "/insert-core", ""):
        out = c.post(BASE + path, content=huge, headers={"content-type": "application/json"})
        assert out.status_code == 422 and out.json()["reason"] == "body_too_large", path
    # a body with no Content-Length (streamed) is capped too
    def chunks():
        for _ in range(60):
            yield b"x" * 8192
    out = c.post(f"{BASE}/validate", content=chunks(), headers={"content-type": "application/json"})
    assert out.status_code == 422 and out.json()["reason"] == "body_too_large"
    assert c.post(f"{BASE}/validate", content=b"[1]", headers={"content-type": "application/json"}).status_code == 422
    assert c.post(f"{BASE}/validate", content=b"{nope", headers={"content-type": "application/json"}).status_code == 422
    assert c.post(f"{BASE}/validate", json={"text": 5}).status_code == 422
    assert c.post(f"{BASE}/validate", json={}).status_code == 422


def test_the_store_lives_under_the_workdir_schemas_directory(local):
    store = local.app.state.f2w_kg_library.user_schemas
    assert isinstance(store, UserSchemaStore) and store.root.name == "schemas" and store.root.parent.name != "kgs"

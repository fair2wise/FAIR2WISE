"""Splash GraphQL helpers against an in-memory fake of the splash-links API (no sockets)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.f2w_agent import kg_update


class FakeSplash:
    """Tiny GraphQL double: entities and links in dicts, dispatched on the operation text."""

    def __init__(self, entities=(), links=()):
        self.entities = {e["id"]: dict(e) for e in entities}
        self.links = {l["id"]: dict(l) for l in links}
        self.requests = []
        self.errors = None

    def urlopen(self, req, timeout):
        body = json.loads(req.data.decode())
        self.requests.append((req.full_url, req.get_method(), timeout, body))
        q, v = body["query"], body["variables"]
        if self.errors:
            data = {"errors": self.errors}
        elif "deleteEntity" in q:
            self.entities.pop(v["id"], None)
            data = {"data": {"deleteEntity": True}}
        elif "ListEntities" in q or "query Entities" in q:
            rows = list(self.entities.values())[v["offset"]: v["offset"] + v["limit"]]
            data = {"data": {"entities": rows}}
        elif "query Entity(" in q:
            data = {"data": {"entity": self.entities.get(v["id"])}}
        elif "updateEntity" in q:
            ent = self.entities[v["id"]]
            ent.update({k: val for k, val in v["input"].items()})
            data = {"data": {"updateEntity": ent}}
        elif "createEntity" in q:
            ent = {"id": f"e{len(self.entities)+1}", **v["input"]}
            self.entities[ent["id"]] = ent
            data = {"data": {"createEntity": ent if v["input"]["name"] != "null" else None}}
        elif "createLink" in q:
            link = {"id": f"l{len(self.links)+1}", "subjectId": v["input"]["subjectId"],
                    "predicate": v["input"]["predicate"], "objectId": v["input"]["objectId"]}
            self.links[link["id"]] = link
            data = {"data": {"createLink": link if v["input"]["predicate"] != "null" else None}}
        elif "deleteLink" in q:
            return self._resp({"data": {"deleteLink": self.links.pop(v["id"], None) is not None}})
        elif "query Links($subjectId" in q:
            rows = [l for l in self.links.values()
                    if (v["subjectId"] in (None, l["subjectId"])) and (v["predicate"] in (None, l["predicate"]))
                    and (v["objectId"] in (None, l["objectId"]))]
            data = {"data": {"links": rows[: v["limit"]]}}
        elif "query Links(" in q:
            rows = list(self.links.values())[v["offset"]: v["offset"] + v["limit"]]
            data = {"data": {"links": rows}}
        else:  # pragma: no cover - guards the fake
            raise AssertionError(q)
        return self._resp(data)

    @staticmethod
    def _resp(payload):
        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(payload).encode()

        return R()


@pytest.fixture
def splash(monkeypatch):
    fake = FakeSplash(
        entities=[
            {"id": "u1", "entityType": "Material", "name": "Gold", "uri": "matkg:gold", "properties": {"x": 1}},
            {"id": "u2", "entityType": None, "name": None, "uri": None, "properties": {"matkg_id": "matkg:two"}},
            {"id": "u3", "entityType": "Technique", "name": "RSoXS", "uri": None, "properties": None},
        ],
        links=[
            {"id": "l1", "subjectId": "u1", "predicate": "rel:measured_by", "objectId": "u3",
             "properties": {"has_evidence": "e"}},
            {"id": "l2", "subjectId": "u1", "predicate": "rel:x", "objectId": "ghost", "properties": None},
        ],
    )
    monkeypatch.setattr(kg_update.request, "urlopen", fake.urlopen)
    return fake


def test_base_url_conversion():
    assert kg_update._splash_base_url("splash://host:8081") == "http://host:8081"
    assert kg_update._splash_base_url("splash:localhost:8081") == "http://localhost:8081"
    assert kg_update._splash_base_url("https://h/x/") == "https://h/x"
    with pytest.raises(ValueError, match="unsupported splash URI"):
        kg_update._splash_base_url("ftp://h")


@pytest.mark.parametrize(
    ("configured", "expected"),
    [(":memory:", None), ("sqlite:///:memory:", None), ("sqlite:///data/l.sqlite", "REPO/data/l.sqlite"),
     ("/abs/l.sqlite", "/abs/l.sqlite"), ("rel.sqlite", "REPO/rel.sqlite")],
)
def test_splash_db_path_resolution(monkeypatch, tmp_path, configured, expected):
    monkeypatch.setattr(kg_update, "config_value", lambda key, fallback=None: configured)
    got = kg_update._splash_db_path(tmp_path)
    assert (str(got) if got else None) == (expected.replace("REPO", str(tmp_path)) if expected else None)


def test_graphql_posts_json_and_raises_on_errors(splash):
    assert kg_update._splash_graphql("splash://h:1", "query Entity($id: ID!) {}", {"id": "nope"}, timeout=7) == {
        "entity": None}
    url, method, timeout, body = splash.requests[0]
    assert url == "http://h:1/splash_links/graphql" and method == "POST" and timeout == 7
    splash.errors = [{"message": "denied"}]
    with pytest.raises(RuntimeError, match="denied"):
        kg_update._splash_graphql("splash://h:1", "query x")


def test_wipe_via_graphql_pages_until_empty(splash):
    assert kg_update._wipe_splash_via_graphql("splash://h:1", page_size=2) == 3
    assert splash.entities == {}
    assert kg_update._wipe_splash_via_graphql("splash://h:1") == 0


def test_find_entity_by_id_uri_property_and_pagination(splash):
    assert kg_update.splash_find_entity_by_matkg_id("u1", splash_uri="splash://h:1")["name"] == "Gold"
    assert kg_update.splash_find_entity_by_matkg_id("matkg:gold", splash_uri="splash://h:1", page_size=1)["id"] == "u1"
    assert kg_update.splash_find_entity_by_matkg_id("matkg:two", splash_uri="splash://h:1", page_size=1)["id"] == "u2"
    assert kg_update.splash_find_entity_by_matkg_id("missing", splash_uri="splash://h:1", page_size=2) is None
    assert kg_update.splash_find_entity_by_matkg_id("") is None


def test_update_entity_patches_only_given_fields_and_empty_update_is_a_fetch(splash):
    out = kg_update.splash_update_entity("u1", name="Au", properties={"y": 2}, splash_uri="splash://h:1")
    assert out["name"] == "Au" and out["properties"] == {"y": 2} and out["entityType"] == "Material"
    kg_update.splash_update_entity("u1", entity_type="Metal", splash_uri="splash://h:1")
    assert splash.entities["u1"]["entityType"] == "Metal"
    n = len(splash.requests)
    assert kg_update.splash_update_entity("u3", splash_uri="splash://h:1")["id"] == "u3"
    assert "updateEntity" not in splash.requests[n][3]["query"]


def test_create_entity_and_link_raise_on_null_mutation_results(splash):
    ent = kg_update.splash_create_entity(entity_type="Material", name="Si", uri="matkg:si", splash_uri="splash://h:1")
    assert ent["name"] == "Si" and ent["id"] in splash.entities
    with pytest.raises(RuntimeError, match="createEntity returned null"):
        kg_update.splash_create_entity(entity_type="T", name="null", splash_uri="splash://h:1")
    link = kg_update.splash_create_link(subject_id="u1", predicate="rel:p", object_id="u3", splash_uri="splash://h:1")
    assert link["predicate"] == "rel:p"
    with pytest.raises(RuntimeError, match="createLink returned null"):
        kg_update.splash_create_link(subject_id="u1", predicate="null", object_id="u3", splash_uri="splash://h:1")


def test_find_and_delete_links(splash):
    s = "splash://h:1"
    assert [l["id"] for l in kg_update.splash_find_links(subject_id="u1", splash_uri=s)] == ["l1", "l2"]
    assert [l["id"] for l in kg_update.splash_find_links(predicate="rel:x", splash_uri=s)] == ["l2"]
    assert kg_update.splash_find_links(object_id="u3", splash_uri=s, limit=1)[0]["id"] == "l1"
    assert kg_update.splash_delete_link("l1", splash_uri=s) is True
    assert kg_update.splash_delete_link("l1", splash_uri=s) is False


def test_uri_default_precedence(monkeypatch):
    monkeypatch.delenv("KG_RAG_SPLASH_URI", raising=False)
    monkeypatch.delenv("SPLASH_LINKS_URI", raising=False)
    assert kg_update.splash_uri_default() == "splash://localhost:8081"
    monkeypatch.setenv("SPLASH_LINKS_URI", "splash://b:2")
    assert kg_update.splash_uri_default() == "splash://b:2"
    monkeypatch.setenv("KG_RAG_SPLASH_URI", "splash://a:1")
    assert kg_update.splash_uri_default() == "splash://a:1"


def test_node_mapping_prefers_uri_then_matkg_id_then_uuid_and_defaults():
    assert kg_update._splash_node_id({"id": "u", "uri": "x", "properties": {"matkg_id": "m"}}) == "x"
    assert kg_update._splash_node_id({"id": "u", "properties": {"matkg_id": "m"}}) == "m"
    assert kg_update._splash_node_id({"id": "u"}) == "u"
    node = kg_update._splash_entity_to_node({"id": "u", "entityType": None, "name": None, "properties": None})
    assert node == {"id": "u", "name": "u", "category": "Thing", "description": ""}
    node = kg_update._splash_entity_to_node({"id": "u", "name": "N", "entityType": "T",
                                             "properties": {"description": "d", "category": "ignored"}})
    assert node["category"] == "T" and node["description"] == "d"


def test_load_graph_pages_entities_and_links_and_drops_dangling_links(splash):
    graph = kg_update.load_splash_graph(splash_uri="splash://h:1", page_size=2)
    ids = {n["id"] for n in graph["things"]}
    assert ids == {"matkg:gold", "matkg:two", "u3"}
    assert graph["associations"] == [
        {"subject": "matkg:gold", "predicate": "rel:measured_by", "object": "u3", "has_evidence": "e"}]  # l2 dangles


def test_export_graph_writes_json_file(splash, tmp_path):
    dest = tmp_path / "out" / "kg.json"
    data = kg_update.export_splash_graph_to_json(dest, splash_uri="splash://h:1")
    assert json.loads(dest.read_text()) == data and len(data["things"]) == 3
    assert splash.requests[0][2] == 5  # short timeout for UI reads


def test_rebuild_kg_converts_terms_and_reports_counts(tmp_path, monkeypatch):
    seen = {}
    import app.modules.json2kg as json2kg

    def fake_convert(src, out):
        seen["args"] = (src, out)
        return {"things": [1, 2], "associations": [1]}

    monkeypatch.setattr(json2kg, "convert_terms_to_graph", fake_convert)
    res = kg_update.rebuild_kg(str(tmp_path / "t.json"), str(tmp_path / "deep/dir/kg.json"))
    assert res == {"status": "success", "kg_json": str(tmp_path / "deep/dir/kg.json"), "nodes": 2, "edges": 1}
    assert (tmp_path / "deep/dir").is_dir() and seen["args"][0] == tmp_path / "t.json"


# -------------------------------------------------------------- reimport remaining branches
def _repo(tmp_path):
    repo = tmp_path / "splash_links"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "import_kg.py").write_text("")
    return repo


@pytest.fixture
def reimport_env(monkeypatch, tmp_path):
    monkeypatch.setattr(kg_update.shutil, "which", lambda n: "/usr/bin/pixi")
    monkeypatch.setattr(kg_update, "config_value", lambda key, fallback=None: "links.sqlite")
    kg = tmp_path / "kg.json"
    kg.write_text("{}")
    return _repo(tmp_path), kg


def test_reimport_missing_repo_and_missing_local_db_guard(reimport_env, tmp_path):
    repo, kg = reimport_env
    assert "repo not found" in kg_update.splash_reimport(str(kg), splash_repo=str(tmp_path / "none"))["message"]
    res = kg_update.splash_reimport(str(kg), splash_repo=str(repo), splash_uri="splash://localhost:1",
                                    wipe=True, allow_wipe=True)
    assert res["status"] == "error" and "Restart splash-links" in res["message"]


def test_reimport_api_wipe_failure_is_reported(reimport_env, monkeypatch):
    repo, kg = reimport_env
    (repo / "links.sqlite").write_text("db")

    def boom(uri, **kw):
        raise ConnectionError("refused")

    monkeypatch.setattr(kg_update, "_wipe_splash_via_graphql", boom)
    res = kg_update.splash_reimport(str(kg), splash_repo=str(repo), wipe=True, allow_wipe=True)
    assert res["status"] == "error" and "refused" in res["message"] and res["splash_uri"]


@pytest.mark.parametrize(
    ("proc", "status", "needle"),
    [
        (SimpleNamespace(returncode=0, stdout=" imported \n", stderr=""), "success", "imported"),
        (SimpleNamespace(returncode=2, stdout="", stderr="trace" * 1000), "error", "trace"),
        (SimpleNamespace(returncode=2, stdout="", stderr=""), "error", "import_kg.py failed"),
    ],
)
def test_reimport_subprocess_outcomes(reimport_env, monkeypatch, proc, status, needle):
    repo, kg = reimport_env
    calls = []
    monkeypatch.setattr(kg_update.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)) or proc)
    res = kg_update.splash_reimport(str(kg), splash_repo=str(repo), wipe=False, splash_uri="splash://h:1")
    assert res["status"] == status and needle in (res.get("stdout") or res.get("message"))
    assert calls[0][0][:4] == ["pixi", "run", "python", "scripts/import_kg.py"] and calls[0][1]["timeout"] == 900
    if status == "error":
        assert len(res["message"]) <= 2000


def test_reimport_with_wipe_reports_deleted_count_and_subprocess_exception(reimport_env, monkeypatch):
    repo, kg = reimport_env
    (repo / "links.sqlite").write_text("db")
    monkeypatch.setattr(kg_update, "_wipe_splash_via_graphql", lambda uri: 7)
    monkeypatch.setattr(kg_update.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="ok", stderr=""))
    res = kg_update.splash_reimport(str(kg), splash_repo=str(repo), wipe=True, allow_wipe=True)
    assert res["status"] == "success" and res["deleted_entities"] == 7

    def timeout(cmd, **kw):
        raise TimeoutError("900s")

    monkeypatch.setattr(kg_update.subprocess, "run", timeout)
    res = kg_update.splash_reimport(str(kg), splash_repo=str(repo), wipe=True, allow_wipe=True)
    assert res["status"] == "error" and "splash import failed: 900s" in res["message"]

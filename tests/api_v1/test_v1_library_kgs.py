"""T38: ``/v1/library/{kgs,schemas,models,estimate}`` on ``:local``; absent (404) on ALS. Fakes only."""
from __future__ import annotations

import json

import pytest

from app.api.auth import undeclared_routes
from app.modules.library.estimate import PaperFacts
from app.modules.model_catalog import CachedModelCatalog, ModelListing
from app.modules.graph.snapshot import build_graph_isolated

KG = build_graph_isolated([{"term": "P3HT", "definition": "d", "category": "Polymer", "source_papers": ["a.pdf"],
                            "pages": [1]}], [])


class FakePapers:
    def __init__(self):
        self.facts = {"p1": PaperFacts("p1", "a.pdf", "h1", 4), "p2": PaperFacts("p2", "b.pdf", "h2", 6)}

    def paper_facts(self, paper_id):
        return self.facts.get(paper_id)


@pytest.fixture
def kgs(local, tmp_path):
    svc = local.app.state.f2w_kg_library
    release = tmp_path / "release"
    release.mkdir()
    (release / "matkg_old.json").write_text(json.dumps(KG))
    svc._release = release
    svc.papers = FakePapers()
    svc.catalog = CachedModelCatalog(lambda: ModelListing("cborg", "live", ("m1", "m2")))
    return svc, release


def _schema(c):
    return c.get("/v1/library/schemas").json()["schemas"][0]["name"]


def test_policies_are_declared_and_local_only(local, als):
    assert undeclared_routes(local.app) == []
    paths = {(r.methods and sorted(r.methods)[0], r.path) for r in local.app.routes}
    assert ("GET", "/v1/library/kgs") in paths and ("POST", "/v1/library/kgs/{name}/export") in paths
    c = als.client("admin")
    for method, url in (("get", "/v1/library/kgs"), ("get", "/v1/library/schemas"), ("get", "/v1/library/models"),
                        ("post", "/v1/library/estimate"), ("post", "/v1/library/kgs/continue"),
                        ("post", "/v1/library/kgs/x/export")):
        assert getattr(c, method)(url).status_code == 404, url


def test_models_schemas_and_unavailable_backend(local, kgs):
    svc, _ = kgs
    c = local.client()
    body = c.get("/v1/library/models").json()
    policy = body.pop("model_policy")  # additive (free-model policy, 2026-11-09)
    assert policy["default_model"] == "lbl/cborg-chat" and policy["as_of"]
    assert body == {"available": True, "models": ["m1", "m2"], "source": "live", "error": None, "error_code": None,
                    "backend": "cborg"}
    svc.catalog = CachedModelCatalog(lambda: ModelListing("cborg", "unavailable", error="no key"))
    out = c.get("/v1/library/models").json()
    assert out["available"] is False and out["models"] == [] and out["error"] == "no key"
    schemas = c.get("/v1/library/schemas").json()["schemas"]
    assert schemas and all({"name", "label", "description"} <= set(s) for s in schemas)
    assert not any(k in c.get("/v1/library/schemas").text for k in ("/Users/", "/private/"))


def test_estimate_and_confirmation_token(local, kgs):
    svc, _ = kgs
    c = local.client()
    schema = _schema(c)
    body = {"paper_ids": ["p1", "p2"], "model": "m1", "schema": schema}
    first = c.post("/v1/library/estimate", json=body)
    assert first.status_code == 200, first.text
    out = first.json()
    assert out["pages"] == 10 and out["estimated_cost_usd"] is None and out["estimated_tokens"] == 16000
    assert c.post("/v1/library/estimate", json=body).json() == out
    svc.check_confirmation(out["confirmation"], ["p1", "p2"], "m1", schema)
    assert c.post("/v1/library/estimate", json={**body, "paper_ids": ["p1", "nope"]}).status_code == 422 \
        or c.post("/v1/library/estimate", json={**body, "paper_ids": ["p1", "nope"]}).status_code == 400
    assert c.post("/v1/library/estimate", json={**body, "schema": "bogus"}).status_code in (400, 422)
    assert c.post("/v1/library/estimate", json={**body, "paper_ids": []}).status_code in (400, 422)
    svc.papers = None
    assert c.post("/v1/library/estimate", json=body).status_code == 503


def test_continue_export_and_listing(local, kgs):
    svc, release = kgs
    c = local.client()
    schema = _schema(c)
    assert c.get("/v1/library/kgs").json() == {"bundles": [], "sources": ["matkg_old.json"]}
    made = c.post("/v1/library/kgs/continue", json={"name": "child", "source": "matkg_old.json", "schema": schema,
                                                    "topic": "t"})
    assert made.status_code == 200, made.text
    assert made.json()["reconstructed"] is True and made.json()["schema"] == schema
    again = c.post("/v1/library/kgs/continue", json={"name": "child", "source": "matkg_old.json", "schema": schema})
    assert again.status_code == 409
    assert c.post("/v1/library/kgs/continue", json={"name": "bad name", "source": "matkg_old.json",
                                                    "schema": schema}).status_code in (400, 422)
    assert c.post("/v1/library/kgs/continue", json={"name": "x", "source": "../etc.json", "schema": schema}
                  ).status_code in (400, 422)
    assert c.post("/v1/library/kgs/continue", json={"name": "x", "source": "matkg_old.json"}).status_code in (400, 422)
    assert c.post("/v1/library/kgs/continue", json={"name": "x", "source": "matkg_old.json", "parent": "child",
                                                    "schema": schema}).status_code in (400, 422)
    other = [s["name"] for s in c.get("/v1/library/schemas").json()["schemas"] if s["name"] != schema][0]
    locked = c.post("/v1/library/kgs/continue", json={"name": "kid", "parent": "child", "schema": other})
    assert locked.status_code in (400, 422) and "locked" in locked.text
    kid = c.post("/v1/library/kgs/continue", json={"name": "kid", "parent": "child"})
    assert kid.status_code == 200 and kid.json()["parent"] == "child" and kid.json()["schema"] == schema
    names = [b["name"] for b in c.get("/v1/library/kgs").json()["bundles"]]
    assert names == ["child", "kid"]
    assert c.post("/v1/library/kgs/child/export", json={}).status_code in (400, 422)
    assert not (release / "matkg_child.json").exists()
    done = c.post("/v1/library/kgs/child/export", json={"confirm": True})
    assert done.status_code == 200 and done.json() == {"name": "child", "file_name": "matkg_child.json"}
    assert json.loads((release / "matkg_child.json").read_text())["metadata"]["schema"] == schema
    assert c.post("/v1/library/kgs/ghost/export", json={"confirm": True}).status_code == 404
    assert c.post("/v1/library/kgs/..%2Fx/export", json={"confirm": True}).status_code in (400, 404, 422)
    # a name equal to an existing release file (matkg_old.json) is refused
    assert c.post("/v1/library/kgs/continue", json={"name": "old", "source": "matkg_old.json", "schema": schema}
                  ).status_code == 409
    assert str(release) not in c.get("/v1/library/kgs").text


def test_bundles_do_not_disturb_settings_and_contract(local, kgs, monkeypatch):
    svc, _ = kgs
    monkeypatch.setenv("F2W_WORKDIR", str(svc.store.root.parent))
    c = local.client()
    before = c.get("/settings").json()
    schema = _schema(c)
    assert c.post("/v1/library/kgs/continue", json={"name": "child", "source": "matkg_old.json", "schema": schema}
                  ).status_code == 200
    after = c.get("/settings")
    assert after.status_code == 200
    assert "kgs/child/matkg_child.json" in json.dumps(after.json())
    assert [g for g in before["available_json_graphs"]] == [g for g in after.json()["available_json_graphs"]
                                                           if not g.startswith("kgs/")] if "available_json_graphs" in before else True

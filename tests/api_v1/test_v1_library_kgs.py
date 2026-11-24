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


def test_models_schemas_and_unavailable_backend(local, kgs, monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    svc, _ = kgs
    c = local.client()
    body = c.get("/v1/library/models").json()
    policy = body.pop("model_policy")  # additive (free-model policy, 2026-11-09)
    assert body.pop("ollama")["state"] == "unset"  # no OLLAMA_URL in the test environment
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
    on_disk = (release / "matkg_child.json")
    assert done.status_code == 200 and done.json() == {"name": "child", "file_name": "matkg_child.json",
                                                      "size_bytes": on_disk.stat().st_size}
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
    # one name for the KG everywhere: the source listing, and so the viewer and the node card header, say "child",
    # never the virtual file name matkg_child.json (no such file exists in the release folder)
    listed = c.get("/v1/graph/sources").json()["sources"]
    bundle = next(src for src in listed if src["graph_id"] == "child")
    assert bundle["label"] == "child" and "matkg_child" not in json.dumps(bundle)
    assert all(g["label"] != "matkg_child.json" for g in after.json().get("available_graphs", []))
    # a KG whose nodes carry no passages shows cleanly: the full record loads, with no snippet fields and no error
    monkeypatch.setenv("F2W_DISCLOSE_SNIPPETS", "1")
    node = c.get("/graph/node/matkg:P3HT", params={"source": bundle["id"]})
    assert node.status_code == 200, node.text
    papers = node.json()["publications"]
    assert papers and all("snippets" not in p and "snippet_count" not in p for p in papers)
    assert [g for g in before["available_json_graphs"]] == [g for g in after.json()["available_json_graphs"]
                                                           if not g.startswith("kgs/")] if "available_json_graphs" in before else True


def test_models_403_ip_not_authorized_reports_the_reason_and_still_lists_ollama(local, kgs):
    """The Extract tab's "model list unavailable" is CBORG's 403 on GET /models; Ollama is listed regardless."""
    import httpx
    import openai

    from app.modules.cborg_errors import IP_NOT_AUTHORIZED_MESSAGE
    from app.modules.model_catalog import list_cborg_models_live
    from app.modules.ollama_catalog import OllamaListing

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(403, json={"error": {"type": "ip_not_authorized", "message": "IP 203.0.113.9 not allowed"}})

    client = openai.OpenAI(api_key="k", base_url="https://cborg.invalid",
                           http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    svc, _ = kgs
    svc.catalog = CachedModelCatalog(lambda: list_cborg_models_live(client=client))
    svc._ollama = lambda: OllamaListing("ok", ("llama3:8b",))
    out = local.client().get("/v1/library/models").json()
    assert calls == ["GET /models"]  # the app-side fetch is GET {base}/models and it is the one refused
    assert out["available"] is False and out["models"] == [] and out["source"] == "unavailable"
    assert out["error"] == IP_NOT_AUTHORIZED_MESSAGE and out["error_code"] == "cborg_ip_not_authorized"
    assert "203.0.113.9" not in json.dumps(out)
    assert out["ollama"] == {"state": "ok", "models": ["llama3:8b"], "error": None}


def test_models_reports_every_ollama_state_without_a_url(local, kgs):
    from app.modules.ollama_catalog import OllamaListing

    svc, _ = kgs
    c = local.client()
    for listing in (OllamaListing("unset", error="OLLAMA_URL is not set on the server"),
                    OllamaListing("unreachable", error="Ollama did not answer"), OllamaListing("empty")):
        svc._ollama = lambda listing=listing: listing
        out = c.get("/v1/library/models").json()
        assert out["ollama"]["state"] == listing.state and out["ollama"]["models"] == []
        assert out["available"] is True  # CBORG is independent of Ollama


def test_an_exported_kg_can_be_downloaded_and_nothing_else(local, kgs):
    """The Download action streams the exported file; nothing is created and no other file is reachable."""
    svc, release = kgs
    c = local.client()
    schema = _schema(c)
    assert c.post("/v1/library/kgs/continue", json={"name": "child", "source": "matkg_old.json", "schema": schema}
                  ).status_code == 200
    before = sorted(p.name for p in release.iterdir())
    early = c.get("/v1/library/kgs/child/download")
    assert early.status_code == 404 and "not been exported" in early.text      # nothing to download yet, nothing created
    assert sorted(p.name for p in release.iterdir()) == before
    assert c.post("/v1/library/kgs/child/export", json={"confirm": True}).status_code == 200
    got = c.get("/v1/library/kgs/child/download")
    assert got.status_code == 200 and got.content == (release / "matkg_child.json").read_bytes()
    assert got.headers["content-type"].startswith("application/json")
    assert 'filename="matkg_child.json"' in got.headers["content-disposition"] and got.headers["cache-control"] == "no-store"
    assert c.get("/v1/library/kgs/ghost/download").status_code == 404
    assert c.get("/v1/library/kgs/..%2Fold/download").status_code in (400, 404, 422)
    # a file in the release folder that is not an export of an existing bundle is not reachable by name
    assert c.get("/v1/library/kgs/old/download").status_code == 404
    (release / "matkg_child.json").unlink()
    (release / "matkg_child.json").symlink_to(release / "matkg_old.json")
    assert c.get("/v1/library/kgs/child/download").status_code == 404           # a link is never served

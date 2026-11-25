"""``POST /v1/library/kgs/{name}/relations`` (V100): start ``library_relations`` on ``:local`` only; typed refusals; no model."""
from __future__ import annotations

import json
from pathlib import Path

from _v1_support import build_als, build_local

from app.api.policies import API_ROUTE_POLICIES
from app.modules.contracts.jobs import JobState
from app.modules.library import bundles as B
from app.modules.library.probe import ProbeResult
from app.modules.library.service import LibraryService
from tests.library.test_relation_repairs import SCHEMA as SCHEMA_TEXT
from tests.library.test_relations_job import SHIPPED, plecos_terms
from tests.security.security_fakes import local_settings

URL = "/v1/library/kgs/{}/relations"


def build(monkeypatch, tmp_path: Path):
    lib = tmp_path / "papers"
    lib.mkdir()
    service = LibraryService(lib, tmp_path / "state" / "library" / "catalog.sqlite", settings=local_settings(),
                             probe=lambda p: ProbeResult(status="ok", pages=1, page_text="x"))
    app = build_local(monkeypatch, tmp_path, library=service)
    user_schemas = app.app.state.f2w_kg_library.user_schemas
    saved = user_schemas.save("plecos", SCHEMA_TEXT)
    store: B.BundleStore = app.app.state.f2w_library_relations.store
    store.create("kg1", schema={"id": saved.id, "version": str(saved.version), "sha256": saved.sha256}, terms=plecos_terms(),
                 papers=[B.PaperRef("a.pdf", "x", 1)])
    store.create("shipped", schema=SHIPPED, terms=plecos_terms())
    return app, store


def test_the_policy_is_local_only_with_the_extract_capability():
    row = next(p for p in API_ROUTE_POLICIES if p.route_id == "V100")
    assert (row.method, row.path, row.capabilities, row.on_als) == (
        "POST", "/v1/library/kgs/{name}/relations", frozenset({"library.extract"}), False)


def test_als_answers_404(monkeypatch, tmp_path):
    als = build_als(monkeypatch, tmp_path)
    assert als.client("admin").post(URL.format("kg1"), json={}).status_code == 404


def test_start_answers_the_job_handle_and_the_job_repairs_the_bundle(monkeypatch, tmp_path):
    app, store = build(monkeypatch, tmp_path)
    c = app.client()
    resp = c.post(URL.format("kg1"), json={})
    assert resp.status_code == 200, resp.text
    handle = resp.json()
    assert set(handle) == {"job_id", "kind", "state", "submitted_at", "settings_revision"}
    assert handle["kind"] == "library_relations" and handle["state"] == "queued"
    job_id, state = app.work()
    assert (job_id, state) == (handle["job_id"], JobState.SUCCEEDED)
    terms = {t["term"]: t for t in json.loads(store.get("kg1").file(B.TERMS_FILE).read_text())["terms"]}
    assert terms["Ancistrus"]["relations"][0]["related_term"] == "530 mm"
    summary = c.get(f"/v1/jobs/{handle['job_id']}/progress").json()["summary"]
    assert summary["params"] == {"bundle": "kg1"}
    assert summary["counters"]["repair_swapped"] == 1 and summary["counters"]["repair_attributes"] == 2
    assert (store.get("kg1").directory / "previous" / B.TERMS_FILE).is_file()


def test_typed_refusals(monkeypatch, tmp_path):
    app, store = build(monkeypatch, tmp_path)
    c = app.client()
    assert c.post(URL.format("nope"), json={}).status_code == 404
    shipped = c.post(URL.format("shipped"), json={})
    assert shipped.status_code == 422 and shipped.json()["reason"] == "not_user_schema"
    first = c.post(URL.format("kg1"), json={})
    assert first.status_code == 200
    again = c.post(URL.format("kg1"), json={})
    assert again.status_code == 409 and again.json()["reason"] == "relations_in_progress"
    assert c.post(URL.format("kg1"), json={"nonce": "x" * 65}).status_code == 422
